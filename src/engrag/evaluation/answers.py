"""Score generated answers against reference answers with an LLM judge, and check the citations."""
import json
import re
import time
from collections import defaultdict

from engrag.generation import llm

JUDGE_SYSTEM = "You are a strict grader of answers to technical questions. Reply with JSON only."
JUDGE_PROMPT = """QUESTION:
{question}

REFERENCE ANSWER (ground truth):
{reference}

CANDIDATE ANSWER:
{candidate}

Grade the candidate against the reference. Judge facts, numbers and units, not wording.
- "correct": all key facts in the reference are present and nothing contradicts it.
- "partial": some key facts are right but important ones are missing or wrong.
- "incorrect": the key facts are wrong or missing, or it contradicts the reference.
Reply with JSON only: {{"verdict": "correct" | "partial" | "incorrect", "reason": "<one sentence>"}}"""


def parse_verdict(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        d = json.loads(m.group(0)) if m else {}
        v = str(d.get("verdict", "")).lower().strip()
        if v in ("correct", "partial", "incorrect"):
            return {"verdict": v, "reason": str(d.get("reason", ""))}
    except json.JSONDecodeError:
        pass
    return {"verdict": "unparseable", "reason": text[:150]}


def judge(question: str, reference: str, candidate: str) -> dict:
    out = llm.complete(JUDGE_PROMPT.format(question=question, reference=reference, candidate=candidate),
                       system=JUDGE_SYSTEM, max_tokens=200, temperature=0.0)
    return parse_verdict(out)


def evaluate_answers(rows: list[dict], ask_fn, judge_fn=judge, progress=None, on_start=None) -> dict:
    """ask_fn(question) -> Answer. Rows need question, reference_answer, gold_docs.
    on_start(i, n, question) is called before each question; progress(i, n, result, seconds) after it."""
    results = []
    for i, row in enumerate(rows):
        t0 = time.time()
        if on_start:
            on_start(i + 1, len(rows), row["question"])
        a = ask_fn(row["question"])
        if a.refused:
            verdict = {"verdict": "refused", "reason": "the system said it could not find the answer"}
        else:
            verdict = judge_fn(row["question"], row.get("reference_answer", ""), a.text)
        gold = set(row["gold_docs"])
        results.append({
            "id": row.get("id"), "question": row["question"], "reference": row.get("reference_answer", ""),
            "answer": a.text, "status": a.status, **verdict, "answer_type": row.get("answer_type"),
            "cited_docs": a.cited_docs, "gold_docs": row["gold_docs"],
            "cited_a_gold_doc": bool(gold & set(a.cited_docs)),
            "retrieved_a_gold_doc": any(h.doc_id in gold for h in a.hits),
        })
        if progress:
            progress(i + 1, len(rows), results[-1], time.time() - t0)

    def summary(items):
        n = len(items)
        if not n:
            return {}
        c = lambda v: sum(1 for r in items if r["verdict"] == v) / n  # noqa: E731
        answered = [r for r in items if r["verdict"] != "refused"]
        return {"n": n, "correct": round(c("correct"), 3), "partial": round(c("partial"), 3),
                "incorrect": round(c("incorrect"), 3), "refused": round(c("refused"), 3),
                "unparseable": round(c("unparseable"), 3),
                "retrieved_gold_doc": round(sum(r["retrieved_a_gold_doc"] for r in items) / n, 3),
                "answers_citing_gold_doc": round(sum(r["cited_a_gold_doc"] for r in answered) / len(answered), 3) if answered else None}

    groups = defaultdict(list)
    for r in results:
        if r.get("answer_type"):
            groups[f"answer_type={r['answer_type']}"].append(r)
    return {"overall": summary(results), "by_group": {g: summary(v) for g, v in groups.items()}, "results": results}
