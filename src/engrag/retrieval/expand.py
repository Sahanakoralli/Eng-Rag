"""Follow references: if a retrieved chunk says "see Table 1", also fetch Table 1 from the same document.

Tables hold names and numbers, so they match questions poorly and often rank below the prose that mentions them.
"""
import re

from qdrant_client import models

from engrag.retrieval.search import Hit

MENTION = re.compile(r"\btable\s+([A-Z]?[.\-]?\d+(?:[.\-]\d+)*)\b", re.IGNORECASE)
TABLE_KINDS = ("table", "mixed")


def mentioned_tables(text: str) -> list[str]:
    out = []
    for m in MENTION.finditer(text):
        label = m.group(1).upper()
        if label not in out:
            out.append(label)
    return out


def _caption(label: str):
    return re.compile(rf"(?im)^\s*[*_#>|\s]*table\s+{re.escape(label)}\b")


def find_table_chunks(chunks: list[dict], label: str) -> list[dict]:
    """chunks: one document's chunk payloads sorted by chunk_index. Returns the chunk(s) holding that table."""
    cap, out, seen = _caption(label), [], set()
    for i, c in enumerate(chunks):
        if not cap.search(c["text"]):
            continue
        if c.get("chunk_kind") in TABLE_KINDS and "|" in c["text"]:
            cands = [c]  # caption and table together
        else:  # caption ended up in a neighbouring chunk: the table is directly after (or before) it
            cands = [chunks[j] for j in (i + 1, i - 1)
                     if 0 <= j < len(chunks) and chunks[j].get("chunk_kind") in TABLE_KINDS][:1]
        for t in cands:
            if t["chunk_index"] not in seen:
                seen.add(t["chunk_index"])
                out.append(t)
    return out


def doc_chunks(retriever, doc_id: str) -> list[dict]:
    cache = retriever.__dict__.setdefault("_doc_cache", {})
    if doc_id not in cache:
        if len(cache) >= 64:
            cache.pop(next(iter(cache)))
        flt = models.Filter(must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))])
        pts, offset = [], None
        while True:
            batch, offset = retriever.client.scroll(retriever.collection, scroll_filter=flt, limit=256,
                                                    offset=offset, with_payload=True, with_vectors=False)
            pts += [p.payload for p in batch]
            if offset is None:
                break
        cache[doc_id] = sorted(pts, key=lambda p: p.get("chunk_index", 0))
    return cache[doc_id]


def expand_references(retriever, hits: list[Hit], max_extra: int = 3, scan_top: int = 6) -> list[Hit]:
    """Extra Hits for tables mentioned in the top hits but not already among them."""
    have = {(h.doc_id, h.payload.get("chunk_index")) for h in hits}
    extra: list[Hit] = []
    for h in hits[:scan_top]:
        for label in mentioned_tables(h.text):
            if len(extra) >= max_extra:
                return extra
            cap = _caption(label)
            if any(x.doc_id == h.doc_id and x.payload.get("chunk_kind") in TABLE_KINDS and cap.search(x.text) for x in hits + extra):
                continue  # that table is already in the results
            for t in find_table_chunks(doc_chunks(retriever, h.doc_id), label):
                key = (h.doc_id, t["chunk_index"])
                if key in have or len(extra) >= max_extra:
                    continue
                have.add(key)
                extra.append(Hit(score=0.0, text=t["text"], doc_id=h.doc_id, section_path=t.get("section_path", ""),
                                 page_start=t.get("page_start"), page_end=t.get("page_end"),
                                 payload={**t, "linked_from": f"Table {label}"}))
    return extra
