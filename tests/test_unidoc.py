import json

from engrag.evaluation.unidoc import gold_ids, load_qa, select_and_export


def q(doc, answer_type="text_only", used=True):
    return {
        "rewritten_question_obscured": f"question about {doc}?",
        "complete_answer": f"answer {doc}",
        "question_type": "factual_retrieval",
        "answer_type": answer_type,
        "chunk_used": {
            "chunk_0": {"used": used, "metadata": {"source": f"/x/database/{doc}_id_3_pg4_pg5.txt"}},
            "img_0": {"used": False, "metadata": f"/x/figures/9999999/figure-1.jpg"},
        },
    }


def make_pdfs(tmp_path, n):
    d = tmp_path / "pdfs"
    d.mkdir()
    out = []
    for i in range(n):
        p = d / f"{1000000 + i}.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        out.append(p)
    return out


def test_gold_ids_only_from_used_evidence():
    assert gold_ids(q("1000001")) == {"1000001"}
    assert gold_ids(q("1000001", used=False)) == set()


def test_filters_and_exports(tmp_path):
    pdfs = make_pdfs(tmp_path, 10)
    qa = [q("1000001"), q("1000002", "image_only"), q("1000003", "table_required"),
          q("5555555"),  # evidence PDF is not in the download
          q("1000004", used=False)]  # no usable evidence id
    r = select_and_export(pdfs, qa, "construction", tmp_path / "raw", tmp_path / "eval.jsonl")
    assert r["questions_kept"] == 2
    assert r["skipped"] == {"image_or_multimodal": 1, "no_evidence_id": 1, "pdf_not_found": 1}
    rows = [json.loads(l) for l in (tmp_path / "eval.jsonl").read_text().splitlines()]
    assert rows[0]["gold_docs"] == ["construction/1000001.pdf"]
    assert (tmp_path / "raw/construction/1000001.pdf").exists()
    assert r["pdfs_copied"] == 10


def test_subset_keeps_evidence_docs_and_adds_distractors(tmp_path):
    pdfs = make_pdfs(tmp_path, 50)
    qa = [q(f"{1000000 + i}") for i in range(0, 50, 2)]  # 25 answerable questions
    r = select_and_export(pdfs, qa, "energy", tmp_path / "raw", tmp_path / "e.jsonl", max_docs=20)
    assert r["pdfs_copied"] == 20
    copied = {p.stem for p in (tmp_path / "raw/energy").glob("*.pdf")}
    rows = [json.loads(l) for l in (tmp_path / "e.jsonl").read_text().splitlines()]
    assert rows and len(rows) <= 25
    for row in rows:  # every kept question's evidence is actually in the corpus
        assert all(g.split("/")[1][:-4] in copied for g in row["gold_docs"])
    assert len(copied) > len({g for r_ in rows for g in r_["gold_docs"]})  # distractors present


def test_load_qa_accepts_list_dict_and_jsonl(tmp_path):
    items = [q("1000001")]
    (tmp_path / "a.json").write_text(json.dumps(items))
    (tmp_path / "b.json").write_text(json.dumps({"data": items}))
    (tmp_path / "c.json").write_text("\n".join(json.dumps(i) for i in items))
    assert all(len(load_qa(tmp_path / f)) == 1 for f in ["a.json", "b.json", "c.json"])


# ---- download logic with the network mocked ----
import sys
import tarfile
import types

import pytest

from engrag.evaluation import unidoc


@pytest.fixture
def fake_hub(tmp_path, monkeypatch):
    """Fake huggingface_hub: repo with an archive of 3 PDFs (one macOS junk file) and optional QA file."""
    src = tmp_path / "src"; src.mkdir()
    for n in ("1000001", "1000002", "1000003"):
        (src / f"{n}.pdf").write_bytes(b"%PDF")
    (src / "._1000001.pdf").write_bytes(b"junk")
    archive = tmp_path / "hub" / "construction_pdfs.tar.gz"; archive.parent.mkdir()
    with tarfile.open(archive, "w:gz") as t:
        t.add(src, arcname="construction")
    qa_json = tmp_path / "hub" / "construction.json"
    qa_json.write_text(json.dumps([q("1000001"), q("1000002")]))
    state = {"files": ["README.md", "construction_pdfs.tar.gz", "QA/filtered/construction.json"]}

    state["pr_json"] = False  # is construction.json available on the PR branch?
    state["main_json_text"] = qa_json

    def hf_hub_download(repo, filename, repo_type=None, local_dir=None, revision=None):
        if filename == "construction_pdfs.tar.gz":
            return str(archive)
        if filename.endswith("construction.json"):
            if revision is None and filename in state["files"]:
                return str(state["main_json_text"])
            if revision == "refs/pr/5" and state["pr_json"]:
                return str(qa_json)
        raise FileNotFoundError(filename)

    hub = types.ModuleType("huggingface_hub")
    hub.hf_hub_download = hf_hub_download
    hub.list_repo_files = lambda repo, repo_type=None: state["files"]
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    return state


def test_download_uses_json_when_present_and_skips_mac_junk(fake_hub, tmp_path):
    pdfs, qa = unidoc.download_domain("construction", tmp_path / "w", log=lambda *_: None)
    assert len(qa) == 2 and sorted(p.name for p in pdfs) == ["1000001.pdf", "1000002.pdf", "1000003.pdf"]


def test_download_falls_back_to_datasets_library_when_no_qa_file(fake_hub, tmp_path, monkeypatch):
    fake_hub["files"] = ["README.md", "construction_pdfs.tar.gz", "data/train-00000-of-00001.parquet"]
    ds = types.ModuleType("datasets")
    ds.load_dataset = lambda repo, split=None: [q("1000001"), {**q("1000003"), "chunk_used": json.dumps(q("1000003")["chunk_used"])}]
    monkeypatch.setitem(sys.modules, "datasets", ds)
    pdfs, qa = unidoc.download_domain("construction", tmp_path / "w", log=lambda *_: None)
    assert len(qa) == 2 and gold_ids(qa[1]) == {"1000003"}  # JSON-string chunk_used was normalised


def test_empty_repo_listing_still_works_via_datasets(fake_hub, tmp_path, monkeypatch):
    fake_hub["files"] = []  # what your Colab run showed
    ds = types.ModuleType("datasets")
    ds.load_dataset = lambda repo, split=None: [q("1000002")]
    monkeypatch.setitem(sys.modules, "datasets", ds)
    pdfs, qa = unidoc.download_domain("construction", tmp_path / "w", log=lambda *_: None)
    assert len(qa) == 1 and len(pdfs) == 3


def test_clear_error_when_questions_cannot_be_loaded(fake_hub, tmp_path, monkeypatch):
    fake_hub["files"] = ["README.md"]
    monkeypatch.setitem(sys.modules, "datasets", None)  # not installed
    with pytest.raises(RuntimeError, match="pip install datasets"):
        unidoc.download_domain("construction", tmp_path / "w", log=lambda *_: None)


def test_find_qa_file_prefers_json_in_qa_folder():
    files = ["x/construction.parquet", "QA/filtered/construction.json", "construction_pdfs.tar.gz", "QA/filtered/energy.json"]
    assert unidoc._find_qa_file(files, "construction") == "QA/filtered/construction.json"
    assert unidoc._find_qa_file(["a.parquet"], "construction") is None


def test_parquet_roundtrip(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    pd.DataFrame([q("1000001")]).to_parquet(tmp_path / "c.parquet")
    qa = unidoc.load_qa(tmp_path / "c.parquet")
    assert gold_ids(qa[0]) == {"1000001"}


def test_prefers_branch_json_with_evidence_over_evidence_free_parquet_data(fake_hub, tmp_path, monkeypatch):
    """The real situation: main has parquet tables (no chunk_used); the PR branch has the full JSON."""
    fake_hub["files"] = ["construction_pdfs.tar.gz", "data/construction-00000-of-00001.parquet"]
    fake_hub["pr_json"] = True
    ds = types.ModuleType("datasets")
    ds.load_dataset = lambda repo, split=None: [{"question": "q", "answer": "a", "answer_type": "text_only"}] * 5
    monkeypatch.setitem(sys.modules, "datasets", ds)
    pdfs, qa = unidoc.download_domain("construction", tmp_path / "w", log=lambda *_: None)
    assert unidoc._has_evidence(qa) and gold_ids(qa[0]) == {"1000001"}


def test_warns_when_only_evidence_free_questions_exist(fake_hub, tmp_path, monkeypatch):
    fake_hub["files"] = ["construction_pdfs.tar.gz"]
    ds = types.ModuleType("datasets")
    ds.load_dataset = lambda repo, split=None: [{"question": "q", "answer": "a", "answer_type": "text_only"}]
    monkeypatch.setitem(sys.modules, "datasets", ds)
    msgs = []
    pdfs, qa = unidoc.download_domain("construction", tmp_path / "w", log=msgs.append)
    assert len(qa) == 1 and any("no evidence links" in m for m in msgs)
