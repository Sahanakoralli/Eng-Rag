"""Retrieval: dense + BM25 hybrid search (RRF fusion), metadata filters, optional cross-encoder rerank."""
from dataclasses import dataclass, field

from qdrant_client import models

from engrag.config import settings
from engrag.ingestion.index import DENSE, SPARSE

MODES = ("hybrid", "dense", "sparse")


@dataclass
class Hit:
    score: float
    text: str
    doc_id: str
    section_path: str = ""
    page_start: int | None = None
    page_end: int | None = None
    payload: dict = field(default_factory=dict)

    @property
    def citation(self) -> str:
        pages = ""
        if self.page_start:
            pages = f", p.{self.page_start}" + (f"-{self.page_end}" if self.page_end and self.page_end != self.page_start else "")
        return f"{self.doc_id}{pages}"


class Reranker:
    """Cross-encoder: reads (query, chunk) together, so it judges relevance far better than vectors alone."""

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or settings.reranker_model
        self._m = None

    def scores(self, query: str, texts: list[str]) -> list[float]:
        if self._m is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._m = TextCrossEncoder(self.model_name)
        return [float(s) for s in self._m.rerank(query, texts)]


def build_filter(filters: dict | None):
    """{'product': 'XP-200', 'doc_type': ['pdf','docx']} -> Qdrant filter (all conditions must match)."""
    if not filters:
        return None
    must = []
    for key, val in filters.items():
        if isinstance(val, (list, tuple, set)):
            must.append(models.FieldCondition(key=key, match=models.MatchAny(any=list(val))))
        else:
            must.append(models.FieldCondition(key=key, match=models.MatchValue(value=val)))
    return models.Filter(must=must)


class Retriever:
    def __init__(self, client, embedder, reranker=None, collection: str | None = None):
        self.client = client
        self.embedder = embedder
        self.reranker = reranker
        self.collection = collection or settings.collection

    def _candidates(self, query, n, mode, flt):
        c, name = self.client, self.collection
        if mode == "dense":
            return c.query_points(name, query=self.embedder.dense_query(query), using=DENSE,
                                  limit=n, query_filter=flt, with_payload=True).points
        idx, val = self.embedder.sparse_query(query)
        sparse = models.SparseVector(indices=idx, values=val)
        if mode == "sparse":
            return c.query_points(name, query=sparse, using=SPARSE, limit=n,
                                  query_filter=flt, with_payload=True).points
        if mode != "hybrid":
            raise ValueError(f"mode must be one of {MODES}")
        return c.query_points(
            name,
            prefetch=[
                models.Prefetch(query=self.embedder.dense_query(query), using=DENSE, limit=n, filter=flt),
                models.Prefetch(query=sparse, using=SPARSE, limit=n, filter=flt),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=n, with_payload=True,
        ).points

    def search(self, query: str, k: int = 8, candidates: int = 50, mode: str = "hybrid",
               rerank: bool = True, filters: dict | None = None) -> list[Hit]:
        points = self._candidates(query, max(candidates, k), mode, build_filter(filters))
        hits = [Hit(score=float(p.score), text=p.payload.get("text", ""), doc_id=p.payload.get("doc_id", ""),
                    section_path=p.payload.get("section_path", ""), page_start=p.payload.get("page_start"),
                    page_end=p.payload.get("page_end"), payload=p.payload) for p in points]
        if rerank and hits:
            if self.reranker is None:
                self.reranker = Reranker()
            scores = self.reranker.scores(query, [h.text for h in hits])
            for h, s in zip(hits, scores):
                h.score = s
            hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]
