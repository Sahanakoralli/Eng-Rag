"""Score retrieval against questions whose correct source documents are known."""
import json
from collections import defaultdict
from pathlib import Path


def load_eval(path) -> list[dict]:
    rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in rows if r.get("question") and r.get("gold_docs")]


def _unique_docs(hits, k):
    seen, out = set(), []
    for h in hits[:k]:
        if h.doc_id not in seen:
            seen.add(h.doc_id)
            out.append(h.doc_id)
    return out


def score_question(hits, gold: list[str], ks=(1, 3, 5, 10)) -> dict:
    gold_set = set(gold)
    out = {}
    for k in ks:
        docs = set(_unique_docs(hits, k))
        out[f"hit@{k}"] = float(bool(gold_set & docs))          # found at least one correct document
        out[f"recall@{k}"] = len(gold_set & docs) / len(gold_set)  # fraction of correct documents found
    rank = next((i + 1 for i, h in enumerate(hits) if h.doc_id in gold_set), None)
    out["mrr"] = 1 / rank if rank else 0.0                       # 1/position of first correct chunk
    return out


def evaluate(search_fn, rows: list[dict], ks=(1, 3, 5, 10), progress=None) -> dict:
    """search_fn(question) -> ranked list of Hit (best first). Returns overall + per-type metrics + failures."""
    per_q, groups, failures = [], defaultdict(list), []
    depth = max(ks)
    for i, row in enumerate(rows):
        hits = search_fn(row["question"])
        s = score_question(hits, row["gold_docs"], ks)
        per_q.append(s)
        for key in ("answer_type", "question_type"):
            if row.get(key):
                groups[f"{key}={row[key]}"].append(s)
        if s[f"hit@{depth}"] == 0:
            failures.append({"id": row.get("id"), "question": row["question"], "gold_docs": row["gold_docs"],
                             "retrieved": [{"doc": h.doc_id, "section": h.section_path} for h in hits[:5]]})
        if progress:
            progress(i + 1, len(rows))

    def avg(items):
        return {m: round(sum(x[m] for x in items) / len(items), 4) for m in items[0]} if items else {}

    return {"n": len(rows), "overall": avg(per_q), "by_group": {g: {"n": len(v), **avg(v)} for g, v in groups.items()},
            "failures": failures}
