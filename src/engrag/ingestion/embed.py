"""Local embedders (free, CPU). Dense = meaning, sparse BM25 = exact keywords/part numbers."""
from engrag.config import settings


class Embedder:
    def __init__(self, dense_model: str | None = None, sparse_model: str | None = None):
        self.dense_name = dense_model or settings.embedding_model
        self.sparse_name = sparse_model or settings.sparse_model
        self._dense = None
        self._sparse = None

    # models load lazily so importing this module is instant
    def _load(self):
        if self._dense is None:
            from fastembed import SparseTextEmbedding, TextEmbedding

            self._dense = TextEmbedding(self.dense_name)
            self._sparse = SparseTextEmbedding(self.sparse_name)

    @property
    def dim(self) -> int:
        from fastembed import TextEmbedding

        return TextEmbedding.get_embedding_size(self.dense_name)

    def dense(self, texts: list[str]) -> list[list[float]]:
        self._load()
        return [v.tolist() for v in self._dense.embed(texts)]

    def sparse(self, texts: list[str]) -> list[tuple[list[int], list[float]]]:
        self._load()
        return [(e.indices.tolist(), e.values.tolist()) for e in self._sparse.embed(texts)]

    # query-side variants (used by retrieval)
    def dense_query(self, text: str) -> list[float]:
        self._load()
        return next(iter(self._dense.query_embed(text))).tolist()

    def sparse_query(self, text: str) -> tuple[list[int], list[float]]:
        self._load()
        e = next(iter(self._sparse.query_embed(text)))
        return e.indices.tolist(), e.values.tolist()
