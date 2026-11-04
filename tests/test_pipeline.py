import json

import pytest

from engrag.config import settings
from engrag.ingestion import index as idx
from engrag.ingestion.pipeline import ingest

BODY = "# Valve Guide\n\n## Torque\n" + ("Tighten the flange bolts to 25 Nm in a star pattern. " * 15)


@pytest.fixture
def env(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    object.__setattr__(settings, "processed_dir", str(tmp_path / "processed"))
    object.__setattr__(settings, "manifest_path", str(tmp_path / "manifest.json"))
    return raw, idx.get_client(":memory:")


def run(env, emb, **kw):
    raw, client = env
    return ingest(raw, embedder=emb, client=client, log=lambda *_: None, **kw)


def test_full_lifecycle(env, fake_embedder):
    raw, client = env
    (raw / "valve.md").write_text(BODY)
    (raw / "valve.md.meta.json").write_text(json.dumps({"product": "V200", "version": "2.1"}))
    (raw / "notes.txt").write_text("Plain notes. " * 100)

    s = run(env, fake_embedder)
    assert sorted(s["indexed"]) == ["notes.txt", "valve.md"] and not s["failed"]
    assert s["total_points"] == s["chunks"] > 0

    # sidecar metadata lands in payload
    pts, _ = client.scroll(settings.collection, limit=100, with_payload=True)
    valve = [p.payload for p in pts if p.payload["doc_id"] == "valve.md"]
    assert valve and all(p["product"] == "V200" and p["version"] == "2.1" for p in valve)
    assert valve[0]["section_path"] and valve[0]["doc_type"] == "md"

    # unchanged -> skipped
    s = run(env, fake_embedder)
    assert sorted(s["skipped"]) == ["notes.txt", "valve.md"] and not s["indexed"]

    # edited -> re-indexed, no duplicates
    before = idx.count(client, settings.collection)
    (raw / "valve.md").write_text(BODY + "\n\n## Extra\n" + "More details about seals. " * 40)
    s = run(env, fake_embedder)
    assert s["indexed"] == ["valve.md"]
    assert idx.count(client, settings.collection) > before

    # deleted -> removed from index
    (raw / "notes.txt").unlink()
    s = run(env, fake_embedder)
    assert s["removed"] == ["notes.txt"]
    pts, _ = client.scroll(settings.collection, limit=100, with_payload=True)
    assert {p.payload["doc_id"] for p in pts} == {"valve.md"}


def test_bad_file_does_not_stop_run(env, fake_embedder):
    raw, _ = env
    (raw / "empty.md").write_text("   ")
    (raw / "good.md").write_text(BODY)
    s = run(env, fake_embedder)
    assert s["indexed"] == ["good.md"]
    assert s["failed"] and s["failed"][0][0] == "empty.md"


def test_config_change_triggers_reindex(env, fake_embedder):
    raw, _ = env
    (raw / "a.md").write_text(BODY)
    run(env, fake_embedder)
    fake_embedder.dense_name = "other-model"
    s = run(env, fake_embedder)
    assert s["indexed"] == ["a.md"]


def test_macos_junk_files_are_ignored(env, fake_embedder):
    raw, _ = env
    (raw / "good.md").write_text(BODY)
    (raw / "._good.md").write_bytes(b"\x00\x05\x16\x07 apple double")
    (raw / ".DS_Store").write_bytes(b"junk")
    (raw / "__MACOSX").mkdir()
    (raw / "__MACOSX" / "good.md").write_text("junk")
    s = run(env, fake_embedder)
    assert s["indexed"] == ["good.md"] and not s["failed"]


def test_pages_and_figure_captions_reach_the_index(env, fake_embedder, monkeypatch):
    from engrag.ingestion import pipeline
    from engrag.ingestion.parse import PAGE_BREAK, Parsed

    raw, client = env
    (raw / "plan.pdf").write_bytes(b"%PDF fake")
    md = ("# Plan\n\n## Safety\n" + "Wear a hard hat. " * 30 + f"\n\n{PAGE_BREAK}\n\n## Loads\n"
          "[Image 1: Beam layout, span 6 m] " + "Beam load 12 kN. " * 30)
    monkeypatch.setattr(pipeline, "parse_file", lambda *a, **k: Parsed(md, pages=True, n_images=1, n_captioned=1))
    s = run(env, fake_embedder, images=True, caption_fn=lambda b: "x")
    assert s["images"] == 1 and s["captioned"] == 1 and not s["failed"]
    pts, _ = client.scroll(settings.collection, limit=50, with_payload=True)
    loads = [p.payload for p in pts if "Beam layout" in p.payload["text"]]
    assert loads and loads[0]["page_start"] == 2
    safety = [p.payload for p in pts if p.payload["section_path"].endswith("Safety")]
    assert safety[0]["page_start"] == 1


def test_toggling_images_triggers_reindex(env, fake_embedder):
    raw, _ = env
    (raw / "a.md").write_text(BODY)
    run(env, fake_embedder)
    s = run(env, fake_embedder, images=True, caption_fn=lambda b: "x")
    assert s["indexed"] == ["a.md"]


def test_progress_is_saved_after_each_document_so_a_stopped_run_resumes(env, fake_embedder):
    raw, client = env
    for n in "abc":
        (raw / f"{n}.md").write_text(BODY + f"\n\n## Doc {n}\n" + "unique words " * 40)

    class Bomb:  # simulates Ctrl+C while processing the 3rd document
        dense_name, sparse_name, dim = "fake-dense", "fake-sparse", 8
        calls = 0
        def dense(self, t):
            Bomb.calls += 1
            if Bomb.calls == 3:
                raise KeyboardInterrupt
            return fake_embedder.dense(t)
        def sparse(self, t):
            return fake_embedder.sparse(t)

    with pytest.raises(KeyboardInterrupt):
        run(env, Bomb())
    saved = json.loads(open(settings.manifest_path).read())
    assert set(saved) == {"_config", "a.md", "b.md"}  # first two were checkpointed

    s = run(env, fake_embedder)
    assert sorted(s["skipped"]) == ["a.md", "b.md"] and s["indexed"] == ["c.md"]


def test_limit_processes_n_files_and_does_not_delete_the_rest(env, fake_embedder):
    raw, client = env
    for n in "abcd":
        (raw / f"{n}.md").write_text(BODY + n * 50)
    s = run(env, fake_embedder, limit=2)
    assert len(s["indexed"]) == 2 and len(s["deferred"]) == 2 and not s["removed"]
    s = run(env, fake_embedder, limit=2)
    assert len(s["indexed"]) == 2 and len(s["skipped"]) == 2
    s = run(env, fake_embedder)
    assert len(s["skipped"]) == 4 and not s["indexed"]


def test_ocr_flag_is_passed_to_parser_and_changes_config(env, fake_embedder, monkeypatch):
    from engrag.ingestion import pipeline
    from engrag.ingestion.parse import Parsed
    seen = []
    def fake_parse(path, **kw):
        seen.append(kw["ocr"])
        return Parsed(BODY)
    monkeypatch.setattr(pipeline, "parse_file", fake_parse)
    raw, _ = env
    (raw / "x.pdf").write_bytes(b"%PDF")
    run(env, fake_embedder, ocr=False)
    assert seen == [False]
    run(env, fake_embedder, ocr=True)  # toggling OCR re-indexes
    assert seen == [False, True]


def test_settings_change_interrupted_run_still_resumes_correctly(env, fake_embedder):
    raw, _ = env
    for n in "ab":
        (raw / f"{n}.md").write_text(BODY + n * 50)
    run(env, fake_embedder)
    fake_embedder.dense_name = "new-model"
    s = run(env, fake_embedder, limit=1)  # re-index only 1 of 2 under the new settings
    assert len(s["indexed"]) == 1
    s = run(env, fake_embedder)           # the other one must NOT be skipped as 'up to date'
    assert len(s["indexed"]) == 1 and len(s["skipped"]) == 1


# ---------- titles and re-chunking from saved Markdown ----------
def test_title_comes_from_the_first_heading_of_any_level_not_the_file_number(env, fake_embedder, monkeypatch):
    from engrag.ingestion import pipeline
    from engrag.ingestion.parse import Parsed
    raw, client = env
    (raw / "1132750.pdf").write_bytes(b"%PDF")
    md = "## Great British Plans\n\n## Natural balance\n" + "Text about planning. " * 30
    monkeypatch.setattr(pipeline, "parse_file", lambda *a, **k: Parsed(md, pages=True))
    run(env, fake_embedder)
    pts, _ = client.scroll(settings.collection, limit=20, with_payload=True)
    assert pts[0].payload["title"] == "Great British Plans"
    assert pts[0].payload["text"].startswith("Great British Plans > Natural balance") and "1132750" not in pts[0].payload["text"]


def test_changing_chunk_size_rebuilds_from_saved_markdown_without_reparsing(env, fake_embedder, monkeypatch):
    from engrag.ingestion import pipeline
    from engrag.ingestion.parse import Parsed
    raw, client = env
    (raw / "a.pdf").write_bytes(b"%PDF")
    md = "# A\n\n## S\n" + "Sentence about bolts. " * 200
    parses = []
    monkeypatch.setattr(pipeline, "parse_file", lambda *a, **k: parses.append(1) or Parsed(md, pages=True))
    run(env, fake_embedder)
    before = pipeline.idx.count(client, settings.collection)
    old = settings.chunk_size
    object.__setattr__(settings, "chunk_size", 120)
    try:
        s = run(env, fake_embedder)
    finally:
        object.__setattr__(settings, "chunk_size", old)
    assert parses == [1] and s["reused"] == 1 and s["indexed"] == ["a.pdf"]        # parsed once, re-chunked from the saved file
    assert pipeline.idx.count(client, settings.collection) > before


def test_a_new_chunker_version_triggers_rechunking_from_saved_markdown(env, fake_embedder, monkeypatch):
    from engrag.ingestion import pipeline
    from engrag.ingestion.parse import Parsed
    raw, _ = env
    (raw / "a.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(pipeline, "parse_file", lambda *a, **k: Parsed("# A\n\n## S\n" + "Words here. " * 80, pages=True))
    run(env, fake_embedder)
    manifest = json.loads(open(settings.manifest_path).read())
    del manifest["_config"]["chunker_version"]            # what an older project's progress file looks like
    open(settings.manifest_path, "w").write(json.dumps(manifest))
    monkeypatch.setattr(pipeline, "parse_file", lambda *a, **k: pytest.fail("must not re-parse"))
    s = run(env, fake_embedder)
    assert s["reused"] == 1 and s["indexed"] == ["a.pdf"] and not s["failed"]


def test_a_changed_file_is_reparsed_even_when_rechunking(env, fake_embedder, monkeypatch):
    from engrag.ingestion import pipeline
    from engrag.ingestion.parse import Parsed
    raw, _ = env
    (raw / "a.pdf").write_bytes(b"%PDF one")
    parses = []
    monkeypatch.setattr(pipeline, "parse_file", lambda *a, **k: parses.append(1) or Parsed("# A\n\n## S\n" + "Words here. " * 80, pages=True))
    run(env, fake_embedder)
    (raw / "a.pdf").write_bytes(b"%PDF two (edited)")
    s = run(env, fake_embedder, force=True, reuse_parsed=True)
    assert len(parses) == 2 and s["reused"] == 0


def test_missing_saved_markdown_falls_back_to_parsing(env, fake_embedder, monkeypatch):
    from engrag.ingestion import pipeline
    from engrag.ingestion.parse import Parsed
    raw, _ = env
    (raw / "a.pdf").write_bytes(b"%PDF")
    parses = []
    monkeypatch.setattr(pipeline, "parse_file", lambda *a, **k: parses.append(1) or Parsed("# A\n\n## S\n" + "Words here. " * 80, pages=True))
    run(env, fake_embedder)
    for f in __import__("pathlib").Path(settings.processed_dir).glob("*.md"):
        f.unlink()
    s = run(env, fake_embedder, force=True, reuse_parsed=True)
    assert len(parses) == 2 and s["reused"] == 0 and not s["failed"]


def test_entities_are_cleaned_before_chunking(env, fake_embedder, monkeypatch):
    from engrag.ingestion import pipeline
    from engrag.ingestion.parse import Parsed
    raw, client = env
    (raw / "a.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(pipeline, "parse_file", lambda *a, **k: Parsed("# ARM Programming &amp; Architecture\n\n## Intro\n" + "Fill in \\_\\_\\_ here. " * 30, pages=True))
    run(env, fake_embedder)
    text = pipeline.idx.__dict__ and client.scroll(settings.collection, limit=5, with_payload=True)[0][0].payload["text"]
    assert "&amp;" not in text and "Programming & Architecture" in text and "\\_" not in text
