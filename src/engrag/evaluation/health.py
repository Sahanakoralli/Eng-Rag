"""Sanity checks on what is actually inside the index."""
import statistics
from collections import Counter


def iter_payloads(client, collection: str, batch: int = 256):
    offset = None
    while True:
        points, offset = client.scroll(collection, limit=batch, offset=offset, with_payload=True, with_vectors=False)
        for p in points:
            yield p.payload
        if offset is None:
            break


def _pct(sorted_vals, q):
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def health_report(payloads: list[dict], manifest_docs: dict | None = None) -> dict:
    n = len(payloads)
    if n == 0:
        return {"n_chunks": 0, "n_docs": 0, "warnings": ["The index is empty. Run ingest first."]}
    toks = sorted(p.get("tokens", 0) for p in payloads)
    per_doc = Counter(p["doc_id"] for p in payloads)
    pdf_chunks = [p for p in payloads if p.get("doc_type") == "pdf"]
    with_pages = sum(1 for p in pdf_chunks if p.get("page_start"))
    tiny = sum(1 for t in toks if t < 25)
    big = sum(1 for t in toks if t > 900)
    no_section = sum(1 for p in payloads if not p.get("section_path"))
    few = sorted(d for d, c in per_doc.items() if c <= 2)
    r = {
        "n_chunks": n, "n_docs": len(per_doc),
        "tokens": {"min": toks[0], "p5": _pct(toks, .05), "median": int(statistics.median(toks)),
                   "p95": _pct(toks, .95), "max": toks[-1]},
        "kinds": dict(Counter(p.get("chunk_kind", "?") for p in payloads)),
        "chunks_per_doc": {"min": min(per_doc.values()), "median": int(statistics.median(per_doc.values())),
                           "max": max(per_doc.values())},
        "pdf_chunks_with_page_numbers_pct": round(100 * with_pages / len(pdf_chunks), 1) if pdf_chunks else None,
        "tiny_chunks_pct": round(100 * tiny / n, 1), "oversized_chunks_pct": round(100 * big / n, 1),
        "no_section_path_pct": round(100 * no_section / n, 1), "docs_with_2_or_fewer_chunks": few,
    }
    w = []
    if r["tiny_chunks_pct"] > 10:
        w.append(f"{r['tiny_chunks_pct']}% of chunks are tiny (<25 tokens): headings may not be detected well; read some data/processed/*.md files.")
    if r["oversized_chunks_pct"] > 5:
        w.append(f"{r['oversized_chunks_pct']}% of chunks are very large (>900 tokens): a table or paragraph may not be splitting.")
    if r["pdf_chunks_with_page_numbers_pct"] is not None and r["pdf_chunks_with_page_numbers_pct"] < 90:
        w.append("Many PDF chunks have no page number, so citations will be incomplete.")
    if r["no_section_path_pct"] > 30:
        w.append(f"{r['no_section_path_pct']}% of chunks have no section heading: the parser found few headings in these documents.")
    if few:
        w.append(f"{len(few)} document(s) have 2 or fewer chunks (empty or badly parsed?): {', '.join(few[:5])}{' ...' if len(few) > 5 else ''}")
    if manifest_docs is not None:
        missing = sorted(set(manifest_docs) - set(per_doc))
        extra = sorted(set(per_doc) - set(manifest_docs))
        if missing:
            w.append(f"{len(missing)} document(s) are in the progress file but not in the index: {', '.join(missing[:5])}")
        if extra:
            w.append(f"{len(extra)} document(s) are in the index but not in the progress file: {', '.join(extra[:5])}")
    r["warnings"] = w
    return r
