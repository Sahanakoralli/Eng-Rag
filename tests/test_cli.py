"""Runs the real commands end to end (with stand-in models) to make sure the wiring works."""
import json

import pytest
from typer.testing import CliRunner

from engrag import cli
from engrag.config import settings
from engrag.ingestion import index as idx
from engrag.ingestion.pipeline import ingest

runner = CliRunner()
BODY = "# Valve Guide\n\n## Torque\n" + "Tighten the flange bolts to 25 Nm in a star pattern. " * 15


@pytest.fixture
def project(tmp_path, monkeypatch, fake_embedder, fake_reranker):
    for k, v in {"qdrant_path": str(tmp_path / "qdrant"), "processed_dir": str(tmp_path / "processed"),
                 "manifest_path": str(tmp_path / "manifest.json"), "raw_dir": str(tmp_path / "raw"),
                 "chats_dir": str(tmp_path / "chats")}.items():
        object.__setattr__(settings, k, v)
    raw = tmp_path / "raw" / "construction"
    raw.mkdir(parents=True)
    (raw / "valve.md").write_text(BODY)
    (raw / "other.md").write_text("# Other\n\n## Safety\n" + "Wear a helmet near cranes. " * 30)
    client = idx.get_client(settings.qdrant_path)
    ingest(tmp_path / "raw", embedder=fake_embedder, client=client, log=lambda *_: None)
    client.close()
    monkeypatch.setattr("engrag.ingestion.embed.Embedder", lambda *a, **k: fake_embedder)
    monkeypatch.setattr("engrag.retrieval.search.Reranker", lambda *a, **k: fake_reranker)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_inspect_command(project):
    r = runner.invoke(cli.app, ["inspect", "--samples", "1"])
    assert r.exit_code == 0, r.output
    assert "Index health" in r.output and "n_docs" in r.output


def test_search_command(project):
    r = runner.invoke(cli.app, ["search", "flange bolts torque", "--k", "2", "--no-rerank"])
    assert r.exit_code == 0, r.output
    assert "construction/valve.md" in r.output


def test_search_with_filter_and_rerank(project):
    r = runner.invoke(cli.app, ["search", "helmet cranes", "--k", "1", "--rerank", "--filter", "doc_type=md"])
    assert r.exit_code == 0, r.output
    assert "construction/other.md" in r.output


def test_evaluate_compare_command(project):
    (project / "eval").mkdir()
    rows = [{"id": "q1", "question": "flange bolts torque star pattern", "gold_docs": ["construction/valve.md"], "answer_type": "text_only"},
            {"id": "q2", "question": "helmet near cranes", "gold_docs": ["construction/other.md"], "answer_type": "text_only"}]
    (project / "eval" / "construction_eval.jsonl").write_text("\n".join(json.dumps(x) for x in rows))
    r = runner.invoke(cli.app, ["evaluate", "--compare"])
    assert r.exit_code == 0, r.output
    for name in ("dense", "bm25", "hybrid", "hybrid+rerank", "hit@5", "Breakdown for the best method"):
        assert name in r.output
    assert list((project / "eval" / "results").glob("*_failures.json"))


def test_evaluate_without_question_file_explains(project):
    r = runner.invoke(cli.app, ["evaluate"])
    assert r.exit_code == 1 and "get-data --eval-only" in r.output


def test_get_data_eval_only_uses_existing_pdfs(project, monkeypatch):
    from engrag.evaluation import unidoc
    (project / "raw" / "construction" / "1000001.pdf").write_bytes(b"%PDF")
    qa = [{"rewritten_question_obscured": "q?", "complete_answer": "a", "answer_type": "text_only", "question_type": "factual",
           "chunk_used": {"chunk_0": {"used": True, "metadata": {"source": "/db/1000001_id_1_pg1.txt"}}}},
          {"rewritten_question_obscured": "q2?", "answer_type": "text_only",
           "chunk_used": {"chunk_0": {"used": True, "metadata": {"source": "/db/9999999_id_1_pg1.txt"}}}}]
    monkeypatch.setattr(unidoc, "load_questions", lambda domain, work, log=print: qa)
    r = runner.invoke(cli.app, ["get-data", "--eval-only"])
    assert r.exit_code == 0, r.output
    rows = [json.loads(l) for l in (project / "eval" / "construction_eval.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["gold_docs"] == ["construction/1000001.pdf"]


def test_get_data_eval_only_works_from_the_index_when_pdfs_are_gone(project, monkeypatch):
    from engrag.evaluation import unidoc
    manifest = {"_config": {}, "construction/1000001.pdf": {"hash": "x", "chunks": 3}, "construction/valve.md": {}}
    (project / "manifest.json").write_text(json.dumps(manifest))
    qa = [{"rewritten_question_obscured": "q?", "answer_type": "table_required",
           "chunk_used": {"tab_0": {"used": True, "metadata": "/figs/1000001/table-1-1.jpg"}}}]
    monkeypatch.setattr(unidoc, "load_questions", lambda domain, work, log=print: qa)
    r = runner.invoke(cli.app, ["get-data", "--eval-only"])
    assert r.exit_code == 0, r.output
    rows = [json.loads(l) for l in (project / "eval" / "construction_eval.jsonl").read_text().splitlines()]
    assert rows[0]["gold_docs"] == ["construction/1000001.pdf"]
    assert not (project / "raw" / "construction" / "1000001.pdf").exists()  # nothing was copied


def test_rerank_is_off_by_default(project, monkeypatch):
    called = []
    monkeypatch.setattr("engrag.retrieval.search.Reranker", lambda *a, **k: called.append(1) or None)
    r = runner.invoke(cli.app, ["search", "flange bolts torque", "--k", "1"])
    assert r.exit_code == 0, r.output
    # Retriever never calls the reranker when rerank is off
    assert "construction/valve.md" in r.output


def test_ask_command_prints_answer_and_marks_cited_sources(project, monkeypatch):
    from engrag.generation import answer as ans
    monkeypatch.setattr(ans.llm, "complete", lambda p, **kw: "Tighten to 25 Nm in a star pattern [1].")
    r = runner.invoke(cli.app, ["ask", "flange bolts torque", "--k", "2"])
    assert r.exit_code == 0, r.output
    assert "25 Nm" in r.output and "Sources" in r.output and "*[1] construction/" in r.output


def test_evaluate_answers_command(project, monkeypatch):
    from engrag.generation import llm
    def fake_complete(prompt, **kw):
        return '{"verdict": "correct", "reason": "matches"}' if "REFERENCE ANSWER" in prompt else "25 Nm [1]."
    monkeypatch.setattr(llm, "complete", fake_complete)
    (project / "eval").mkdir()
    rows = [{"id": "q1", "question": "flange bolts torque", "reference_answer": "25 Nm",
             "gold_docs": ["construction/valve.md"], "answer_type": "text_only"}]
    (project / "eval" / "construction_eval.jsonl").write_text(json.dumps(rows[0]))
    r = runner.invoke(cli.app, ["evaluate-answers"])
    assert r.exit_code == 0, r.output
    assert "Answer quality" in r.output and "ALL" in r.output
    out = list((project / "eval" / "results").glob("*_answers.jsonl"))
    assert out and json.loads(out[0].read_text())["verdict"] == "correct"


@pytest.fixture
def provider():
    old = settings.llm_provider
    yield lambda name: object.__setattr__(settings, "llm_provider", name)
    object.__setattr__(settings, "llm_provider", old)


def test_list_models_command(project, monkeypatch, provider):
    from engrag.generation import llm
    provider("gemini")
    monkeypatch.setattr(llm, "list_gemini_models", lambda: ["gemini-3.6-flash", "gemini-3.8-flash"])
    r = runner.invoke(cli.app, ["list-models"])
    assert r.exit_code == 0, r.output
    assert "gemini-3.8-flash" in r.output and "LLM_MODEL" in r.output


def test_list_models_reports_bad_key_clearly(project, monkeypatch, provider):
    from engrag.generation import llm
    provider("gemini")
    def boom():
        raise RuntimeError("GEMINI_API_KEY is not set")
    monkeypatch.setattr(llm, "list_gemini_models", boom)
    r = runner.invoke(cli.app, ["list-models"])
    assert r.exit_code == 1 and "GEMINI_API_KEY" in r.output


def test_find_text_shows_matches_and_explains_when_missing(project):
    r = runner.invoke(cli.app, ["find-text", "flange bolts"])
    assert r.exit_code == 0, r.output
    assert "construction/valve.md" in r.output and "1 matching chunk" in r.output
    r = runner.invoke(cli.app, ["find-text", "flange bolts", "--doc", "other"])
    assert "0 matching" in r.output and "Not in the index" in r.output


def test_find_text_empty_pattern_lists_chunks_of_a_kind(project):
    r = runner.invoke(cli.app, ["find-text", "", "--doc", "valve", "--kind", "text", "--max", "1"])
    assert r.exit_code == 0, r.output
    assert "kind=text" in r.output and "construction/valve.md" in r.output
    r = runner.invoke(cli.app, ["find-text", "", "--kind", "table"])
    assert "0 matching" in r.output


def _llm(monkeypatch, log):
    from engrag.generation import answer as ans
    def complete(prompt, **kw):
        log.append(prompt)
        if "STANDALONE QUESTION:" in prompt:
            return "What torque do the flange bolts need?"
        return "Tighten the flange bolts to 25 Nm [1]."
    monkeypatch.setattr(ans.llm, "complete", complete)


def test_ask_with_session_supports_follow_ups_and_saves_history(project, monkeypatch):
    log = []
    _llm(monkeypatch, log)
    r1 = runner.invoke(cli.app, ["ask", "What torque do flange bolts need?", "--session", "demo", "--k", "2"])
    assert r1.exit_code == 0, r1.output
    assert "searched for" not in r1.output          # first question: nothing to rewrite
    r2 = runner.invoke(cli.app, ["ask", "and in what pattern?", "--session", "demo", "--k", "2"])
    assert r2.exit_code == 0, r2.output
    assert "searched for: What torque do the flange bolts need?" in r2.output
    saved = json.loads((project / "chats" / "demo.json").read_text())["turns"]
    assert [t["question"] for t in saved] == ["What torque do flange bolts need?", "and in what pattern?"]
    assert "Tighten the flange bolts to 25 Nm" in log[-1]  # earlier answer was given as context (without [1])


def test_ask_without_session_stays_stateless(project, monkeypatch):
    _llm(monkeypatch, [])
    r = runner.invoke(cli.app, ["ask", "flange bolts torque", "--k", "2"])
    assert r.exit_code == 0 and not (project / "chats").exists()


def test_chat_repl_follow_up_history_sources_and_new(project, monkeypatch):
    log = []
    _llm(monkeypatch, log)
    r = runner.invoke(cli.app, ["chat", "--session", "t", "--k", "2"],
                      input="flange bolts torque\nand the pattern?\n/history\n/sources\n/new\n/quit\n")
    assert r.exit_code == 0, r.output
    assert "searched for" in r.output and "1. Q: flange bolts torque" in r.output and "2. Q: and the pattern?" in r.output
    assert "construction/valve.md" in r.output and "Started a new conversation" in r.output
    assert not (project / "chats" / "t.json").exists()  # /new cleared it


def test_chat_resumes_an_existing_session(project, monkeypatch):
    _llm(monkeypatch, [])
    runner.invoke(cli.app, ["ask", "flange bolts torque", "--session", "r", "--k", "2"])
    r = runner.invoke(cli.app, ["chat", "--session", "r"], input="/quit\n")
    assert "1 earlier turns" in r.output


def test_chat_history_command_lists_shows_and_clears(project, monkeypatch):
    _llm(monkeypatch, [])
    assert "No saved conversations" in runner.invoke(cli.app, ["chat-history"]).output
    runner.invoke(cli.app, ["ask", "flange bolts torque", "--session", "h", "--k", "2"])
    assert "h  (1 turns)" in runner.invoke(cli.app, ["chat-history"]).output
    shown = runner.invoke(cli.app, ["chat-history", "h"]).output
    assert "Q: flange bolts torque" in shown and "25 Nm" in shown
    assert "Deleted" in runner.invoke(cli.app, ["chat-history", "h", "--clear"]).output
    assert "No saved conversations" in runner.invoke(cli.app, ["chat-history"]).output


def test_list_models_for_nvidia_filters_to_nemotron_by_default(project, monkeypatch, provider):
    from engrag.generation import llm
    provider("nvidia")
    seen = []
    monkeypatch.setattr(llm, "list_nvidia_models", lambda contains=None: seen.append(contains) or ["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"])
    r = runner.invoke(cli.app, ["list-models"])
    assert r.exit_code == 0 and "nemotron-3-nano-omni" in r.output and seen == ["nemotron"]
    runner.invoke(cli.app, ["list-models", "--contains", "llama"])
    assert seen[-1] == "llama"


def test_list_models_explains_unsupported_provider(project, provider):
    provider("openai")
    r = runner.invoke(cli.app, ["list-models"])
    assert r.exit_code == 1 and "gemini and nvidia" in r.output


def test_ask_debug_prints_the_full_text_the_model_saw(project, monkeypatch):
    _llm(monkeypatch, [])
    r = runner.invoke(cli.app, ["ask", "flange bolts torque", "--k", "2", "--debug"])
    assert r.exit_code == 0, r.output
    assert "What the model was shown" in r.output and "Model output" in r.output
    assert "Tighten the flange bolts to 25 Nm in a star pattern." in r.output       # the source text itself
    plain = runner.invoke(cli.app, ["ask", "flange bolts torque", "--k", "2"])
    assert "What the model was shown" not in plain.output


def test_search_show_option_controls_how_much_text_is_printed(project):
    short = runner.invoke(cli.app, ["search", "flange bolts torque", "--k", "1", "--no-rerank", "--show", "60"])
    assert short.exit_code == 0, short.output
    assert "more characters: use --show" in short.output
    full = runner.invoke(cli.app, ["search", "flange bolts torque", "--k", "1", "--no-rerank", "--show", "5000"])
    assert "more characters" not in full.output and "star pattern" in full.output


def _eval_file(project):
    (project / "eval").mkdir(exist_ok=True)
    rows = [{"id": f"q{i}", "question": f"Question number {i}?", "reference_answer": f"Answer {i}",
             "gold_docs": [f"construction/d{i}.pdf"], "answer_type": "text_only" if i % 2 else "table_required"} for i in range(6)]
    (project / "eval" / "construction_eval.jsonl").write_text("\n".join(json.dumps(r) for r in rows))


def test_sample_questions_prints_question_expected_answer_and_document(project):
    _eval_file(project)
    r = runner.invoke(cli.app, ["sample-questions", "--n", "2", "--seed", "1"])
    assert r.exit_code == 0, r.output
    assert r.output.count("expected:") == 2 and r.output.count("found in: construction/d") == 2
    again = runner.invoke(cli.app, ["sample-questions", "--n", "2", "--seed", "1"])
    assert again.output == r.output                      # a fixed seed repeats


def test_sample_questions_filters_by_answer_type_and_handles_missing_file(project):
    r = runner.invoke(cli.app, ["sample-questions"])
    assert r.exit_code == 1 and "get-data --eval-only" in r.output
    _eval_file(project)
    r = runner.invoke(cli.app, ["sample-questions", "--n", "10", "--answer-type", "table_required"])
    assert r.exit_code == 0 and r.output.count("expected:") == 3 and "(text_only)" not in r.output
    assert runner.invoke(cli.app, ["sample-questions", "--answer-type", "nope"]).exit_code == 1
