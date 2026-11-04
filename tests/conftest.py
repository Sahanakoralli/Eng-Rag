import hashlib
import re

import pytest


class FakeEmbedder:
    """Deterministic stand-in for the real models (no downloads needed)."""
    dense_name, sparse_name, dim = "fake-dense", "fake-sparse", 8

    def dense(self, texts):
        return [[b / 255 for b in hashlib.md5(t.encode()).digest()[:8]] for t in texts]

    def sparse(self, texts):
        out = []
        for t in texts:
            toks = sorted({int(hashlib.md5(w.encode()).hexdigest()[:6], 16) for w in re.findall(r"\w+", t.lower())})
            out.append((toks, [1.0] * len(toks)))
        return out


def _dq(self, text):
    return self.dense([text])[0]


def _sq(self, text):
    return self.sparse([text])[0]


FakeEmbedder.dense_query = _dq
FakeEmbedder.sparse_query = _sq


class FakeReranker:
    """Scores a chunk by how many query words it contains."""
    def scores(self, query, texts):
        words = set(re.findall(r"\w+", query.lower()))
        return [float(len(words & set(re.findall(r"\w+", t.lower())))) for t in texts]


@pytest.fixture
def fake_reranker():
    return FakeReranker()


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()
