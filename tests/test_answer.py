import json

import pytest

from engrag.config import settings
from engrag.evaluation.answers import evaluate_answers, parse_verdict
from engrag.generation import answer as ans
from engrag.generation.answer import REFUSAL, Answer, build_prompt, parse_citations
from engrag.ingestion import index as idx
from engrag.ingestion.pipeline import ingest
from engrag.retrieval.search import Hit, Retriever

BODY = {"a.md": "# Pump\n\n## Torque\n" + "Flange bolt torque is 25 Nm on the XP-200 casing. " * 12,
        "b.md": "# Safety\n\n## PPE\n" + "Workers must wear helmets and vests. " * 12}


@pytest.fixture
def retriever(tmp_path, fake_embedder):
    object.__setattr__(settings, "processed_dir", str(tmp_path / "p"))
    object.__setattr__(settings, "manifest_path", str(tmp_path / "m.json"))
    raw = tmp_path / "raw"; raw.mkdir()
    for n, b in BODY.items():
        (raw / n).write_text(b)
    client = idx.get_client(":memory:")
    ingest(raw, embedder=fake_embedder, client=client, log=lambda *_: None)
    return Retriever(client, fake_embedder)


def test_prompt_numbers_sources_and_includes_question():
    hits = [Hit(1, "bolt text", "a.pdf", "Pump > Torque", 4, 4), Hit(1, "ppe text", "b.pdf")]
    p = build_prompt("What torque?", hits)
    assert "[1] (a.pdf, p.4; section: Pump > Torque)" in p and "[2] (b.pdf" in p and p.endswith("then the answer):")


def test_parse_citations_ignores_invalid_and_duplicates():
    assert parse_citations("A [1]. B [3][1]. C [9]. D [0].", 3) == [1, 3]


def test_answer_with_citation(retriever, monkeypatch):
    monkeypatch.setattr(ans.llm, "complete", lambda prompt, **kw: "The flange bolt torque is 25 Nm [1].")
    a = ans.answer("flange bolt torque", retriever, k=3)
    assert not a.refused and a.cited == [1] and a.cited_docs == [a.hits[0].doc_id]
    assert a.text.endswith("[1].")


def test_system_prompt_is_passed_and_sources_are_in_prompt(retriever, monkeypatch):
    seen = {}
    def fake(prompt, **kw):
        seen.update(prompt=prompt, **kw)
        return "ok [1]"
    monkeypatch.setattr(ans.llm, "complete", fake)
    ans.answer("flange bolt torque", retriever, k=2)
    assert "Flange bolt torque" in seen["prompt"] and "STATUS: NOT_FOUND" in seen["system"] and seen["temperature"] == 0.0


def test_refusal_is_detected_and_has_no_citations(retriever, monkeypatch):
    monkeypatch.setattr(ans.llm, "complete", lambda p, **kw: REFUSAL + " The sources cover pumps [1].")
    a = ans.answer("flange bolt torque", retriever, k=2)
    assert a.refused and a.cited == []


def test_no_hits_refuses_without_calling_the_llm(retriever, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("LLM must not be called without evidence")
    monkeypatch.setattr(ans.llm, "complete", boom)
    a = ans.answer("anything", retriever, filters={"product": "does-not-exist"})
    assert a.refused and a.hits == []


# ---------- grading ----------
def test_parse_verdict_handles_fences_and_garbage():
    assert parse_verdict('```json\n{"verdict": "Correct", "reason": "ok"}\n```')["verdict"] == "correct"
    assert parse_verdict("no json here")["verdict"] == "unparseable"
    assert parse_verdict('{"verdict": "maybe"}')["verdict"] == "unparseable"


def test_evaluate_answers_summary():
    rows = [
        {"id": "1", "question": "q1", "reference_answer": "25 Nm", "gold_docs": ["a.md"], "answer_type": "text_only"},
        {"id": "2", "question": "q2", "reference_answer": "helmets", "gold_docs": ["b.md"], "answer_type": "table_required"},
        {"id": "3", "question": "q3", "reference_answer": "x", "gold_docs": ["c.md"], "answer_type": "text_only"},
    ]
    def ask(q):
        if q == "q1":
            return Answer(q, "25 Nm [1]", [Hit(1, "t", "a.md")], [1])
        if q == "q2":
            return Answer(q, "vests [1]", [Hit(1, "t", "WRONG.md")], [1])
        return Answer(q, REFUSAL, [Hit(1, "t", "c.md")], [], refused=True)
    verdicts = {"q1": "correct", "q2": "incorrect"}
    res = evaluate_answers(rows, ask, judge_fn=lambda q, ref, cand: {"verdict": verdicts[q], "reason": "r"})
    o = res["overall"]
    assert o["n"] == 3 and o["correct"] == pytest.approx(1 / 3, abs=1e-3) and o["incorrect"] == pytest.approx(1 / 3, abs=1e-3) and o["refused"] == pytest.approx(1 / 3, abs=1e-3)
    assert o["answers_citing_gold_doc"] == 0.5 and o["retrieved_gold_doc"] == pytest.approx(2 / 3, abs=1e-3)
    assert res["by_group"]["answer_type=text_only"]["n"] == 2
    refused = [r for r in res["results"] if r["id"] == "3"][0]
    assert refused["verdict"] == "refused"  # never sent to the judge


def test_progress_callbacks_report_each_question_with_timing():
    rows = [{"id": "1", "question": "q1", "reference_answer": "a", "gold_docs": ["a.md"]},
            {"id": "2", "question": "q2", "reference_answer": "b", "gold_docs": ["b.md"]}]
    starts, dones = [], []
    evaluate_answers(rows, lambda q: Answer(q, REFUSAL, [], [], refused=True),
                     on_start=lambda i, n, q: starts.append((i, n, q)),
                     progress=lambda i, n, r, sec: dones.append((i, n, r["verdict"], sec >= 0)))
    assert starts == [(1, 2, "q1"), (2, 2, "q2")]
    assert dones == [(1, 2, "refused", True), (2, 2, "refused", True)]


# ---------- answer vs refuse: the prompt must allow partial answers ----------
def test_prompt_defines_three_statuses_and_forbids_refusing_when_something_answers():
    s = ans.SYSTEM
    for status in ("STATUS: ANSWERED", "STATUS: PARTIAL", "STATUS: NOT_FOUND"):
        assert status in s
    assert "even briefly" in s and "never use NOT_FOUND" in s and "no formal definition" in s
    assert "Never fill gaps with outside knowledge" in s


def test_partial_answer_is_not_treated_as_a_refusal(retriever, monkeypatch):
    monkeypatch.setattr(ans.llm, "complete", lambda p, **kw:
                        "The documents give no formal definition, but they describe it as protecting habitats [1].")
    a = ans.answer("flange bolt torque", retriever, k=2)
    assert not a.refused and a.cited == [1]


def test_a_real_refusal_is_still_detected(retriever, monkeypatch):
    monkeypatch.setattr(ans.llm, "complete", lambda p, **kw: REFUSAL + " The sources cover pumps.")
    assert ans.answer("flange bolt torque", retriever, k=2).refused


# ---------- the status line (replaces matching an exact refusal sentence) ----------
from engrag.generation.answer import parse_status  # noqa: E402

MICRO = ("STATUS: ANSWERED\n\nA microcontroller puts the CPU, RAM, ROM and I/O on a single IC chip; "
         "SOC and MCU are other names for it [1].")


@pytest.mark.parametrize("text,status,body_starts", [
    ("STATUS: ANSWERED\n\nTorque is 25 Nm [1].", "answered", "Torque is 25 Nm"),
    ("**STATUS: PARTIAL**\nOnly the pattern is given [1].", "partial", "Only the pattern"),
    ("status: not_found - The sources cover pumps.", "not_found", "The sources cover pumps."),
    ("STATUS: NOT FOUND\n\nNothing about this.", "not_found", "Nothing about this."),
    ("Torque is 25 Nm [1].", "", "Torque is 25 Nm"),               # no status line: left untouched
])
def test_parse_status_variants(text, status, body_starts):
    s, body = parse_status(text)
    assert s == status and body.startswith(body_starts)


def test_answered_status_keeps_citations_and_strips_the_status_line(retriever, monkeypatch):
    monkeypatch.setattr(ans.llm, "complete", lambda p, **kw: MICRO)
    a = ans.answer("what is a microcontroller", retriever, k=2)
    assert a.status == "answered" and not a.refused and a.cited == [1]
    assert a.text.startswith("A microcontroller puts") and "STATUS" not in a.text


def test_partial_status_is_an_answer_with_citations(retriever, monkeypatch):
    monkeypatch.setattr(ans.llm, "complete", lambda p, **kw: "STATUS: PARTIAL\n\nOnly torque is given [1]; the pattern is missing.")
    a = ans.answer("flange bolt torque and pattern", retriever, k=2)
    assert a.status == "partial" and not a.refused and a.cited == [1]


def test_not_found_status_becomes_a_clear_refusal_without_citations(retriever, monkeypatch):
    monkeypatch.setattr(ans.llm, "complete", lambda p, **kw: "STATUS: NOT_FOUND\n\nThe sources cover pumps [1].")
    a = ans.answer("something unrelated", retriever, k=2)
    assert a.refused and a.status == "not_found" and a.cited == []
    assert a.text.startswith(REFUSAL) and "The sources cover pumps" in a.text


def test_status_wins_over_a_stray_refusal_sentence(retriever, monkeypatch):
    """The failure you saw: the model answered, but the old code judged it by its opening sentence."""
    monkeypatch.setattr(ans.llm, "complete", lambda p, **kw: "STATUS: ANSWERED\n\n" + REFUSAL + " ... yet here: it is a chip [1].")
    a = ans.answer("what is a microcontroller", retriever, k=2)
    assert not a.refused and a.cited == [1]


def test_old_style_refusal_without_status_line_is_still_recognised(retriever, monkeypatch):
    monkeypatch.setattr(ans.llm, "complete", lambda p, **kw: REFUSAL + " The sources cover pumps.")
    a = ans.answer("something unrelated", retriever, k=2)
    assert a.refused and a.status == "not_found"


def test_no_hits_status_is_not_found(retriever, monkeypatch):
    a = ans.answer("anything", retriever, filters={"product": "nope"})
    assert a.refused and a.status == "not_found"


# ---------- the echoed-prompt bug (real output: the status description was copied into the answer) ----------
ECHOED = ("STATUS: ANSWERED  - the sources answer the question, directly or by describing how the documents treat the topic. "
          'For "what is X" questions with no formal definition, explain what the documents say about X and note that '
          "no formal definition is given.\n\nANSWER: The sources list the ARM families as ARM7, ARM9, ARM10, and ARM11. [2][3]")


def test_echoed_status_description_is_removed_and_the_answer_label_too():
    status, body = parse_status(ECHOED)
    assert status == "answered"
    assert body == "The sources list the ARM families as ARM7, ARM9, ARM10, and ARM11. [2][3]"


def test_echo_removed_end_to_end_and_citations_kept(retriever, monkeypatch):
    monkeypatch.setattr(ans.llm, "complete", lambda p, **kw: ECHOED.replace("[2][3]", "[1][2]"))
    a = ans.answer("flange bolt torque helmets vests", retriever, k=3)
    assert len(a.hits) >= 2 and a.cited == [1, 2] and not a.refused and a.status == "answered"
    assert a.text.startswith("The sources list the ARM families")
    assert "directly or by describing" not in a.text and "ANSWER:" not in a.text and "STATUS" not in a.text


def test_text_on_the_same_line_as_the_label_is_kept_when_it_is_not_an_echo():
    assert parse_status("STATUS: ANSWERED The torque is 25 Nm [1].") == ("answered", "The torque is 25 Nm [1].")
    assert parse_status("STATUS: PARTIAL - only the pattern is given [1].")[1] == "only the pattern is given [1]."


def test_answer_label_is_stripped_even_without_a_status_line():
    assert parse_status("ANSWER: Tighten to 25 Nm [1].") == ("", "Tighten to 25 Nm [1].")
    assert parse_status("**Answer:** Tighten to 25 Nm [1].")[1] == "Tighten to 25 Nm [1]."


def test_prompt_shows_labels_on_their_own_lines_and_asks_for_complete_lists():
    s = ans.SYSTEM
    assert "\nSTATUS: ANSWERED\nSTATUS: PARTIAL\nSTATUS: NOT_FOUND\n" in s       # labels alone: nothing to copy after them
    assert "combine the items from every relevant source" in s
