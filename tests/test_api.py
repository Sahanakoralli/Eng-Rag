import json
import time

import pytest
from fastapi.testclient import TestClient

from engrag.api import create_app
from engrag.generation import answer as ans
from engrag.ingestion import index as idx
from engrag.ingestion.pipeline import ingest

BOLTS = b"# Valve Guide\n\n## Torque\n" + b"Tighten the flange bolts to 25 Nm in a star pattern. " * 15
HELMET = b"# Site Safety\n\n## PPE\n" + b"Workers must wear a helmet and vest near cranes. " * 15


@pytest.fixture
def llm(monkeypatch):
    calls = []
    def complete(prompt, **kw):
        calls.append(prompt)
        if "STANDALONE QUESTION:" in prompt:
            return "What torque do the flange bolts need?"
        return "Tighten to 25 Nm [1]."
    monkeypatch.setattr(ans.llm, "complete", complete)
    complete.calls = calls
    return complete


def make(tmp_path, fake_embedder, **kw):
    kw.setdefault("sync_ingest", True)
    return create_app(client=idx.get_client(":memory:"), embedder=fake_embedder, uploads_dir=tmp_path / "uploads",
                      chats_dir=tmp_path / "chats", log_path=tmp_path / "qa.jsonl", **kw)


@pytest.fixture
def api(tmp_path, fake_embedder, llm):
    with TestClient(make(tmp_path, fake_embedder)) as c:
        c.tmp = tmp_path
        yield c


def upload(c, name, data, headers=None):
    return c.post("/files", files={"file": (name, data)}, headers=headers or {})


# ---------------------------------------------------------------- basics
def test_home_page_and_health(api):
    assert "Chat with your documents" in api.get("/").text
    h = api.get("/health").json()
    assert h["status"] == "ok" and h["auth_required"] is False and h["uploaded_files"] == 0


# ---------------------------------------------------------------- upload
def test_upload_indexes_the_file(api):
    r = upload(api, "valve.md", BOLTS)
    assert r.status_code == 202
    m = r.json()
    assert m["status"] == "ready" and m["chunks"] >= 1 and m["filename"] == "valve.md"
    assert api.get(f"/files/{m['file_id']}").json()["status"] == "ready"
    assert [f["file_id"] for f in api.get("/files").json()] == [m["file_id"]]
    assert api.get("/health").json()["uploaded_chunks"] >= 1


def test_filename_cannot_escape_the_upload_folder(api):
    m = upload(api, "../../evil.md", BOLTS).json()
    assert m["filename"] == "evil.md" and m["doc_id"] == f"{m['file_id']}/evil.md"
    assert not (api.tmp.parent / "evil.md").exists() and (api.tmp / "uploads" / m["file_id"] / "evil.md").exists()


@pytest.mark.parametrize("name,data,code", [("tool.exe", b"MZ", 415), ("empty.md", b"", 400), ("fake.pdf", b"not a pdf at all", 400)])
def test_bad_uploads_are_rejected_and_leave_nothing_behind(api, name, data, code):
    assert upload(api, name, data).status_code == code
    assert api.get("/files").json() == [] and not list((api.tmp / "uploads").glob("*"))


def test_too_large_upload_is_rejected(tmp_path, fake_embedder, llm):
    with TestClient(make(tmp_path, fake_embedder, max_upload_mb=0.0001)) as c:  # ~100 bytes
        assert upload(c, "big.md", BOLTS).status_code == 413
        assert c.get("/files").json() == []


# ---------------------------------------------------------------- asking
def test_ask_answers_from_the_uploaded_file_and_stores_everything(api, llm):
    fid = upload(api, "valve.md", BOLTS).json()["file_id"]
    r = api.post("/ask", json={"question": "What torque do flange bolts need?", "file_ids": [fid]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["answer"] == "Tighten to 25 Nm [1]." and not j["refused"] and j["turn"] == 1
    assert j["sources"][0]["cited"] is True and j["sources"][0]["filename"] == "valve.md" and j["sources"][0]["text"] is None
    # stored per session ...
    saved = api.get(f"/sessions/{j['session_id']}").json()["turns"]
    assert saved[0]["question"] == "What torque do flange bolts need?" and saved[0]["answer"] == j["answer"]
    # ... and in the global log
    log = api.get("/log").json()
    assert log[-1]["question"] == "What torque do flange bolts need?" and log[-1]["session_id"] == j["session_id"]
    assert (api.tmp / "qa.jsonl").read_text().count("\n") == 1


def test_follow_up_in_same_session_uses_history(api, llm):
    fid = upload(api, "valve.md", BOLTS).json()["file_id"]
    first = api.post("/ask", json={"question": "What torque do flange bolts need?", "file_ids": [fid]}).json()
    second = api.post("/ask", json={"question": "and in what pattern?", "session_id": first["session_id"], "file_ids": [fid]}).json()
    assert second["session_id"] == first["session_id"] and second["turn"] == 2
    assert second["standalone_question"] == "What torque do the flange bolts need?"
    assert "CONVERSATION" in llm.calls[-1] and "What torque do flange bolts need?" in llm.calls[-1]
    assert len(api.get(f"/sessions/{first['session_id']}").json()["turns"]) == 2


def test_questions_are_limited_to_the_selected_file(api):
    a = upload(api, "valve.md", BOLTS).json()["file_id"]
    b = upload(api, "safety.md", HELMET).json()["file_id"]
    only_a = api.post("/ask", json={"question": "helmet vest cranes", "file_ids": [a], "include_text": True}).json()
    assert {s["doc_id"].split("/")[0] for s in only_a["sources"]} == {a}
    only_b = api.post("/search", json={"query": "flange bolts torque", "file_ids": [b], "k": 5}).json()
    assert only_b and {s["doc_id"].split("/")[0] for s in only_b} == {b} and "text" in only_b[0]
    both = api.post("/search", json={"query": "helmet", "k": 10}).json()
    assert {s["doc_id"].split("/")[0] for s in both} == {a, b}


def test_same_filename_twice_stays_independent_and_delete_is_clean(api):
    a = upload(api, "manual.md", BOLTS).json()["file_id"]
    b = upload(api, "manual.md", HELMET).json()["file_id"]
    assert api.delete(f"/files/{a}").status_code == 200
    assert api.get(f"/files/{a}").status_code == 404 and api.delete(f"/files/{a}").status_code == 404
    left = api.post("/search", json={"query": "helmet vest", "k": 10}).json()
    assert left and {s["doc_id"].split("/")[0] for s in left} == {b}
    assert not (api.tmp / "uploads" / a).exists()


def test_asking_before_a_file_is_ready_or_known_gives_clear_errors(api, llm):
    assert api.post("/ask", json={"question": "hi there"}).status_code == 409           # nothing uploaded
    assert api.post("/ask", json={"question": "hi there", "file_ids": ["nope"]}).status_code == 404
    fid = upload(api, "valve.md", BOLTS).json()["file_id"]
    meta_file = api.tmp / "uploads" / fid / "meta.json"
    m = json.loads(meta_file.read_text()); m["status"] = "processing"; meta_file.write_text(json.dumps(m))
    r = api.post("/ask", json={"question": "hi there", "file_ids": [fid]})
    assert r.status_code == 409 and "not ready" in r.json()["detail"]
    assert api.delete(f"/files/{fid}").status_code == 409                                # cannot delete mid-processing
    assert llm.calls == []                                                                # no LLM money spent on errors


@pytest.mark.parametrize("body", [{"question": ""}, {"question": "   "}, {"question": "ok?", "k": 0}, {"question": "ok?", "mode": "magic"}])
def test_invalid_requests_are_rejected(api, body):
    assert api.post("/ask", json=body).status_code == 422


def test_llm_failure_becomes_502_and_is_not_stored(api, monkeypatch):
    fid = upload(api, "valve.md", BOLTS).json()["file_id"]
    def boom(*a, **k):
        raise RuntimeError("quota exceeded")
    monkeypatch.setattr(ans.llm, "complete", boom)
    r = api.post("/ask", json={"question": "torque?", "session_id": "s1", "file_ids": [fid]})
    assert r.status_code == 502 and "quota exceeded" in r.json()["detail"]
    assert api.get("/sessions/s1").status_code == 404 and api.get("/log").json() == []


# ---------------------------------------------------------------- sessions
def test_session_endpoints(api):
    fid = upload(api, "valve.md", BOLTS).json()["file_id"]
    sid = api.post("/ask", json={"question": "flange bolts torque", "file_ids": [fid]}).json()["session_id"]
    assert api.get("/sessions").json() == [{"session_id": sid, "turns": 1}]
    assert api.delete(f"/sessions/{sid}").status_code == 200
    assert api.get(f"/sessions/{sid}").status_code == 404 and api.get("/sessions").json() == []
    assert len(api.get("/log").json()) == 1  # the log is a permanent record, even after a session is deleted


def test_session_id_is_sanitised(api):
    fid = upload(api, "valve.md", BOLTS).json()["file_id"]
    j = api.post("/ask", json={"question": "flange bolts torque", "session_id": "../../x", "file_ids": [fid]}).json()
    assert "/" not in j["session_id"] and (api.tmp / "chats" / f"{j['session_id']}.json").exists()


# ---------------------------------------------------------------- security
def test_api_key_protects_everything_except_page_and_health(tmp_path, fake_embedder, llm):
    with TestClient(make(tmp_path, fake_embedder, api_key="s3cret")) as c:
        assert c.get("/health").json()["auth_required"] is True and c.get("/").status_code == 200
        assert upload(c, "valve.md", BOLTS).status_code == 401
        assert upload(c, "valve.md", BOLTS, {"X-API-Key": "wrong"}).status_code == 401
        ok = upload(c, "valve.md", BOLTS, {"X-API-Key": "s3cret"})
        assert ok.status_code == 202
        for path in ("/files", "/sessions", "/log"):
            assert c.get(path).status_code == 401 and c.get(path, headers={"X-API-Key": "s3cret"}).status_code == 200
        assert c.post("/ask", json={"question": "torque?"}).status_code == 401


# ---------------------------------------------------------------- library corpus
def test_library_corpus_is_separate_from_uploads(tmp_path, fake_embedder, llm):
    client = idx.get_client(":memory:")
    lib = tmp_path / "lib"; lib.mkdir(); (lib / "lib.md").write_bytes(HELMET)
    ingest(lib, embedder=fake_embedder, client=client, collection="library", manifest_path=tmp_path / "lm.json",
           processed_dir=tmp_path / "lp", log=lambda *_: None)
    app = create_app(client=client, embedder=fake_embedder, library_collection="library", uploads_dir=tmp_path / "uploads",
                     chats_dir=tmp_path / "chats", log_path=tmp_path / "qa.jsonl", sync_ingest=True)
    with TestClient(app) as c:
        assert c.get("/health").json()["library_available"] is True
        r = c.post("/ask", json={"question": "helmet near cranes", "corpus": "library"})
        assert r.status_code == 200 and r.json()["sources"][0]["doc_id"] == "lib.md"
        assert c.post("/ask", json={"question": "helmet near cranes"}).status_code == 409  # uploads are still empty


def test_missing_library_gives_404(api):
    assert api.post("/ask", json={"question": "anything?", "corpus": "library"}).status_code == 404


# ---------------------------------------------------------------- background worker (the real mode)
def wait_ready(c, fid, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        m = c.get(f"/files/{fid}").json()
        if m["status"] in ("ready", "failed"):
            return m
        time.sleep(0.1)
    raise AssertionError("file was never processed")


def test_background_processing_and_failure_reporting(tmp_path, fake_embedder, llm):
    with TestClient(make(tmp_path, fake_embedder, sync_ingest=False)) as c:
        queued = upload(c, "valve.md", BOLTS).json()
        assert queued["status"] in ("queued", "processing", "ready")
        assert wait_ready(c, queued["file_id"])["status"] == "ready"
        assert c.post("/ask", json={"question": "flange bolts torque", "file_ids": [queued["file_id"]]}).status_code == 200
        bad = upload(c, "blank.md", b"   \n  ").json()          # parser finds no text
        m = wait_ready(c, bad["file_id"])
        assert m["status"] == "failed" and "no text" in m["error"]
        assert wait_ready(c, queued["file_id"])["status"] == "ready"  # one bad file does not harm others


def test_interrupted_files_are_processed_again_after_restart(tmp_path, fake_embedder, llm):
    folder = tmp_path / "uploads" / "abc123"
    folder.mkdir(parents=True)
    (folder / "valve.md").write_bytes(BOLTS)
    (folder / "valve.md.meta.json").write_text(json.dumps({"file_id": "abc123", "filename": "valve.md"}))
    (folder / "meta.json").write_text(json.dumps({"file_id": "abc123", "filename": "valve.md", "doc_id": "abc123/valve.md",
                                                  "size_bytes": 1, "status": "processing", "chunks": 0, "error": "", "uploaded_at": 1}))
    with TestClient(make(tmp_path, fake_embedder, sync_ingest=False)) as c:
        assert wait_ready(c, "abc123")["status"] == "ready"


def test_api_returns_the_status(tmp_path, fake_embedder, monkeypatch):
    monkeypatch.setattr(ans.llm, "complete", lambda prompt, **kw:
                        "What torque do the flange bolts need?" if "STANDALONE QUESTION:" in prompt else "STATUS: ANSWERED\n\nTighten to 25 Nm [1].")
    with TestClient(make(tmp_path, fake_embedder)) as c:
        fid = upload(c, "valve.md", BOLTS).json()["file_id"]
        j = c.post("/ask", json={"question": "flange bolts torque", "file_ids": [fid]}).json()
        assert j["status"] == "answered" and j["answer"] == "Tighten to 25 Nm [1]." and j["refused"] is False


def test_history_lists_chats_newest_first(api, llm):
    upload(api, "valve.md", BOLTS)
    assert api.get("/history").json() == []
    a = api.post("/ask", json={"question": "What torque for the flange bolts?", "session_id": "one"}).json()
    time.sleep(0.01)
    api.post("/ask", json={"question": "Second chat question", "session_id": "two"})
    h = api.get("/history").json()
    assert [c["session_id"] for c in h] == ["two", "one"]
    assert h[1]["title"] == "What torque for the flange bolts?" and h[1]["turns"] == 1 and a["answer"].startswith(h[1]["preview"][:10])
