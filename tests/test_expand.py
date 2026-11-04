import json

import pytest

from engrag.config import settings
from engrag.generation import answer as ans
from engrag.generation.answer import build_prompt
from engrag.ingestion import index as idx
from engrag.ingestion.pipeline import ingest
from engrag.retrieval.expand import expand_references, find_table_chunks, mentioned_tables
from engrag.retrieval.search import Hit, Retriever

PROSE = "# Slope Study\n\n## Slope methods\n" + (
    "Slope is estimated with a three by three kernel on the grid; a review of the weighting concepts is given in Table 1. " * 6)
TABLE = "## Appendix\n\nTable 1. Review of kernel weighting concepts\n\n| Method | Author |\n|---|---|\n| Max. Slope | Shanholtz |\n| Neighborhood | Sharpnack |\n"


@pytest.fixture
def corpus(tmp_path, fake_embedder):
    object.__setattr__(settings, "processed_dir", str(tmp_path / "p"))
    object.__setattr__(settings, "manifest_path", str(tmp_path / "m.json"))
    raw = tmp_path / "raw"; raw.mkdir()
    (raw / "slope.md").write_text(PROSE + "\n\n" + TABLE)
    client = idx.get_client(":memory:")
    ingest(raw, embedder=fake_embedder, client=client, log=lambda *_: None)
    return Retriever(client, fake_embedder)


def test_mentioned_tables_finds_labels():
    assert mentioned_tables("see Table 1 and table 2.1, also Table A-3; not Tablet 9") == ["1", "2.1", "A-3"]


def _prose_hit(retriever):
    pts, _ = retriever.client.scroll(retriever.collection, limit=50, with_payload=True)
    p = [x.payload for x in pts if "given in Table 1" in x.payload["text"]][0]
    return Hit(1.0, p["text"], p["doc_id"], p["section_path"], payload=p)


def test_linked_table_is_fetched_when_only_the_prose_was_retrieved(corpus):
    extra = expand_references(corpus, [_prose_hit(corpus)])
    assert len(extra) == 1
    assert "Max. Slope" in extra[0].text and extra[0].payload["linked_from"] == "Table 1"


def test_no_duplicate_when_table_already_retrieved(corpus):
    table_pts, _ = corpus.client.scroll(corpus.collection, limit=50, with_payload=True)
    tp = [x.payload for x in table_pts if "Max. Slope" in x.payload["text"]][0]
    table_hit = Hit(0.5, tp["text"], tp["doc_id"], payload=tp)
    assert expand_references(corpus, [_prose_hit(corpus), table_hit]) == []


def test_reference_to_missing_table_is_harmless(corpus):
    h = _prose_hit(corpus)
    h.text = "as shown in Table 9"
    assert expand_references(corpus, [h]) == []


def test_caption_and_table_in_neighbouring_chunks():
    chunks = [
        {"chunk_index": 0, "chunk_kind": "text", "text": "intro"},
        {"chunk_index": 1, "chunk_kind": "text", "text": "Table 2. Loads on beams"},
        {"chunk_index": 2, "chunk_kind": "table", "text": "| Beam | kN |\n|---|---|\n| B1 | 12 |"},
        {"chunk_index": 3, "chunk_kind": "text", "text": "later text"},
    ]
    assert [c["chunk_index"] for c in find_table_chunks(chunks, "2")] == [2]
    assert find_table_chunks(chunks, "7") == []


def test_max_extra_caps_added_chunks(corpus):
    h = _prose_hit(corpus)
    h.text = "Table 1 Table 1.2 Table 3"
    assert len(expand_references(corpus, [h], max_extra=1)) <= 1


def test_prompt_explains_linked_sources():
    linked = Hit(0.0, "| a | b |", "x.pdf", payload={"linked_from": "Table 1"})
    p = build_prompt("q", [Hit(1, "see Table 1", "x.pdf"), linked])
    assert "linked because a source above mentions Table 1" in p


def test_answer_gets_the_linked_table_and_can_be_switched_off(corpus, monkeypatch):
    seen = []
    monkeypatch.setattr(ans.llm, "complete", lambda prompt, **kw: seen.append(prompt) or "Max. Slope [2].")
    a = ans.answer("three by three kernel weighting concepts review", corpus, k=1, mode="sparse")
    assert any(h.payload.get("linked_from") == "Table 1" for h in a.hits) and "Shanholtz" in seen[0]
    ans.answer("three by three kernel weighting concepts review", corpus, k=1, mode="sparse", expand=False)
    assert "Shanholtz" not in seen[1]
