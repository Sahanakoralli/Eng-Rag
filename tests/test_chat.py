import json

import pytest

from engrag.generation import answer as ans
from engrag.generation.answer import Answer, format_history, rewrite_question
from engrag.generation.chat import Session, Turn, chat_turn, list_sessions, safe_name, turn_from_answer
from engrag.retrieval.search import Hit


class StubRetriever:
    def __init__(self, hits):
        self.hits, self.queries = hits, []

    def search(self, query, **kw):
        self.queries.append(query)
        return self.hits


def turn(q="What techniques are used?", a="Max. Slope [1] and Neighborhood [2].", docs=("x.pdf",)):
    return Turn(question=q, answer=a, cited_docs=list(docs))


def fake_llm(rewrite="What did Wood (1996) propose in the slope techniques?", answer_text="Wood proposed Constraint-Quadratic [1]."):
    calls = []
    def complete(prompt, **kw):
        calls.append(prompt)
        return rewrite if "STANDALONE QUESTION:" in prompt else answer_text
    complete.calls = calls
    return complete


# ---------- storage ----------
def test_session_roundtrip_window_and_clear(tmp_path):
    s = Session("demo", tmp_path)
    for i in range(6):
        s.add(turn(q=f"q{i}"))
    again = Session("demo", tmp_path)
    assert [t.question for t in again.turns] == [f"q{i}" for i in range(6)]
    assert [t.question for t in again.recent(2)] == ["q4", "q5"] and again.recent(0) == []
    assert list_sessions(tmp_path) == {"demo": 6}
    again.clear()
    assert not (tmp_path / "demo.json").exists() and list_sessions(tmp_path) == {}


def test_safe_name_blocks_path_tricks(tmp_path):
    assert safe_name("../../etc/passwd") == ".._.._etc_passwd" and safe_name("   ") == "default"
    s = Session("../evil", tmp_path)
    s.add(turn())
    assert s.path.parent == tmp_path  # cannot escape the chats folder


# ---------- rewriting ----------
def test_no_history_means_no_extra_llm_call(monkeypatch):
    monkeypatch.setattr(ans.llm, "complete", lambda *a, **k: pytest.fail("must not call the LLM"))
    assert rewrite_question("What is the torque?", []) == "What is the torque?"


def test_rewrite_uses_history_and_cleans_output(monkeypatch):
    f = fake_llm(rewrite='Standalone question: "Who proposed Constraint-Quadratic?"')
    monkeypatch.setattr(ans.llm, "complete", f)
    out = rewrite_question("Who proposed the fourth one?", [turn()])
    assert out == "Who proposed Constraint-Quadratic?"
    assert "Max. Slope" in f.calls[0] and "FOLLOW-UP QUESTION: Who proposed the fourth one?" in f.calls[0]


@pytest.mark.parametrize("bad", ["", "ok", "x" * 700])
def test_rewrite_falls_back_to_original_when_output_is_unusable(monkeypatch, bad):
    monkeypatch.setattr(ans.llm, "complete", lambda *a, **k: bad)
    assert rewrite_question("and the second one?", [turn()]) == "and the second one?"


def test_history_text_removes_citation_numbers_and_truncates():
    h = format_history([turn(a="Max. Slope [1] and more [2]. " + "z" * 900)])
    assert "[1]" not in h and "[2]" not in h and "(sources: x.pdf)" in h and h.count("z") < 750


# ---------- answering with history ----------
HITS = [Hit(1.0, "Wood (1996) proposed the constraint-quadratic surface.", "x.pdf", "Slope", 3, 3)]


def test_follow_up_searches_with_the_standalone_question_and_prompt_has_context(monkeypatch):
    f = fake_llm()
    monkeypatch.setattr(ans.llm, "complete", f)
    r = StubRetriever(HITS)
    a = ans.answer("What about the fourth one?", r, history=[turn()])
    assert r.queries == ["What did Wood (1996) propose in the slope techniques?"]
    assert a.standalone == r.queries[0] and a.question == "What about the fourth one?"
    prompt = f.calls[-1]
    assert "CONVERSATION" in prompt and "What techniques are used?" in prompt
    assert "QUESTION: What about the fourth one?" in prompt
    assert "Max. Slope [1]" not in prompt  # old citation numbers must not leak into the new answer's context
    assert "not by earlier answers" in ans.SYSTEM


def test_first_question_is_one_llm_call_and_has_no_conversation_section(monkeypatch):
    f = fake_llm()
    monkeypatch.setattr(ans.llm, "complete", f)
    ans.answer("What is Wood's method?", StubRetriever(HITS))
    assert len(f.calls) == 1 and "CONVERSATION" not in f.calls[0]


# ---------- chat_turn ----------
def test_chat_turn_remembers_answer_sources_and_citations(tmp_path, monkeypatch):
    monkeypatch.setattr(ans.llm, "complete", fake_llm())
    s = Session("t", tmp_path)
    a = chat_turn("What did Wood propose?", StubRetriever(HITS), s)
    assert a.cited == [1]
    saved = json.loads((tmp_path / "t.json").read_text())["turns"][0]
    assert saved["question"] == "What did Wood propose?" and saved["cited_docs"] == ["x.pdf"]
    assert saved["sources"][0] == {"n": 1, "citation": "x.pdf, p.3", "section": "Slope", "cited": True, "linked": ""}
    # second turn is a follow-up: the rewrite step runs and history is used
    chat_turn("and in what year?", StubRetriever(HITS), s)
    assert len(Session("t", tmp_path).turns) == 2


def test_history_turns_limits_how_much_context_is_sent(tmp_path, monkeypatch):
    f = fake_llm()
    monkeypatch.setattr(ans.llm, "complete", f)
    s = Session("t", tmp_path)
    for i in range(5):
        s.add(turn(q=f"OLDQ{i}"))
    chat_turn("next?", StubRetriever(HITS), s, history_turns=2)
    assert "OLDQ4" in f.calls[-1] and "OLDQ3" in f.calls[-1] and "OLDQ2" not in f.calls[-1]


def test_status_is_saved_with_the_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(ans.llm, "complete", lambda prompt, **kw: "STATUS: PARTIAL\n\nOnly half [1].")
    s = Session("t", tmp_path)
    chat_turn("What did Wood propose?", StubRetriever(HITS), s)
    assert json.loads((tmp_path / "t.json").read_text())["turns"][0]["status"] == "partial"
    assert Session("t", tmp_path).turns[0].status == "partial"
