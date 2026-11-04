import json

import pytest

from engrag.config import settings
from engrag.evaluation.health import health_report, iter_payloads
from engrag.evaluation.metrics import evaluate, score_question
from engrag.ingestion import index as idx
from engrag.ingestion.pipeline import ingest
from engrag.retrieval.search import Hit, Retriever, build_filter

DOCS = {
    "pumps/xp200.md": ("XP-200", "3.1", "# XP-200 Pump\n\n## Torque\n" + "Flange bolt torque is 25 Nm on the XP-200 pump casing. " * 12),
    "pumps/xp300.md": ("XP-300", "1.0", "# XP-300 Pump\n\n## Torque\n" + "Casing bolt torque is 45 Nm on the XP-300 housing. " * 12),
    "safety/site.md": ("none", "2.0", "# Site Safety\n\n## PPE\n" + "Workers must wear helmets and high visibility vests at all times. " * 12),
}


@pytest.fixture
def corpus(tmp_path, fake_embedder):
    object.__setattr__(settings, "processed_dir", str(tmp_path / "processed"))
    object.__setattr__(settings, "manifest_path", str(tmp_path / "manifest.json"))
    raw = tmp_path / "raw"
    for rel, (product, version, body) in DOCS.items():
        f = raw / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
        f.with_name(f.name + ".meta.json").write_text(json.dumps({"product": product, "version": version}))
    client = idx.get_client(":memory:")
    ingest(raw, embedder=fake_embedder, client=client, log=lambda *_: None)
    return client


def test_sparse_mode_finds_exact_keywords(corpus, fake_embedder):
    r = Retriever(corpus, fake_embedder)
    hits = r.search("helmets vests", k=3, mode="sparse", rerank=False)
    assert hits[0].doc_id == "safety/site.md"


def test_hybrid_returns_ranked_hits_with_citation_fields(corpus, fake_embedder):
    hits = Retriever(corpus, fake_embedder).search("bolt torque XP-300", k=5, mode="hybrid", rerank=False)
    assert hits and all(isinstance(h, Hit) for h in hits)
    assert hits[0].section_path and hits[0].text


def test_rerank_reorders_by_cross_encoder_score(corpus, fake_embedder, fake_reranker):
    r = Retriever(corpus, fake_embedder, fake_reranker)
    hits = r.search("Casing bolt torque 45 Nm XP-300 housing", k=3, mode="hybrid", rerank=True)
    assert hits[0].doc_id == "pumps/xp300.md"
    assert hits[0].score >= hits[-1].score


def test_dense_mode_runs(corpus, fake_embedder):
    assert Retriever(corpus, fake_embedder).search("anything", k=2, mode="dense", rerank=False)


def test_metadata_filters(corpus, fake_embedder):
    r = Retriever(corpus, fake_embedder)
    only = r.search("torque bolt", k=10, rerank=False, filters={"product": "XP-200"})
    assert only and {h.doc_id for h in only} == {"pumps/xp200.md"}
    several = r.search("torque bolt", k=10, rerank=False, filters={"product": ["XP-200", "XP-300"]})
    assert {h.doc_id for h in several} == {"pumps/xp200.md", "pumps/xp300.md"}
    assert build_filter(None) is None


def test_unknown_mode_rejected(corpus, fake_embedder):
    with pytest.raises(ValueError):
        Retriever(corpus, fake_embedder).search("x", mode="magic", rerank=False)


def test_citation_formats_pages():
    assert Hit(1, "t", "a.pdf", page_start=4, page_end=4).citation == "a.pdf, p.4"
    assert Hit(1, "t", "a.pdf", page_start=4, page_end=6).citation == "a.pdf, p.4-6"
    assert Hit(1, "t", "a.md").citation == "a.md"


# ---------- metrics ----------
def hit(doc):
    return Hit(1.0, "t", doc)


def test_score_question_values():
    hits = [hit("x"), hit("y"), hit("g1"), hit("g1"), hit("z")]
    s = score_question(hits, ["g1", "g2"], ks=(1, 3, 5))
    assert (s["hit@1"], s["hit@3"], s["hit@5"]) == (0.0, 1.0, 1.0)
    assert s["recall@3"] == 0.5 and s["mrr"] == pytest.approx(1 / 3)


def test_evaluate_aggregates_groups_and_failures():
    rows = [
        {"id": "a", "question": "qa", "gold_docs": ["d1"], "answer_type": "text_only"},
        {"id": "b", "question": "qb", "gold_docs": ["d2"], "answer_type": "table_required"},
    ]
    fake = {"qa": [hit("d1")], "qb": [hit("zzz")]}
    res = evaluate(lambda q: fake[q], rows, ks=(1, 5))
    assert res["overall"]["hit@1"] == 0.5 and res["overall"]["mrr"] == 0.5
    assert res["by_group"]["answer_type=text_only"]["hit@1"] == 1.0
    assert res["by_group"]["answer_type=table_required"]["hit@1"] == 0.0
    assert [f["id"] for f in res["failures"]] == ["b"]


# ---------- health ----------
def test_health_report_on_real_index_has_no_false_alarms(corpus):
    r = health_report(list(iter_payloads(corpus, settings.collection)))
    assert r["n_docs"] == 3 and r["n_chunks"] >= 3 and not [w for w in r["warnings"] if "tiny" in w]


def test_health_report_flags_problems():
    bad = [{"doc_id": f"d{i // 2}.pdf", "doc_type": "pdf", "tokens": 5, "section_path": ""} for i in range(20)]
    r = health_report(bad, manifest_docs={"d0.pdf": {}, "gone.pdf": {}})
    text = " ".join(r["warnings"])
    assert "tiny" in text and "page number" in text and "gone.pdf" in text and "2 or fewer" in text
    assert health_report([])["warnings"]
