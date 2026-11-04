"""Checks that the environment is ready. Run: python scripts/verify_setup.py [--models]"""
import importlib
import os
import sys

from dotenv import load_dotenv

load_dotenv()
RESULTS = []


def check(name, fn):
    try:
        detail = fn() or ""
        RESULTS.append((True, name, detail))
    except Exception as e:  # noqa: BLE001
        RESULTS.append((False, name, f"{type(e).__name__}: {e}"))


def python_version():
    assert sys.version_info >= (3, 10), "need Python 3.10+"
    return sys.version.split()[0]


def import_pkg(mod, required=True):
    def _f():
        m = importlib.import_module(mod)
        return getattr(m, "__version__", "")
    return _f


def qdrant_local():
    from qdrant_client import QdrantClient, models

    c = QdrantClient(":memory:")
    c.create_collection("t", vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE))
    c.upsert("t", [models.PointStruct(id=1, vector=[0.1, 0.2, 0.3, 0.4], payload={"a": 1})])
    hits = c.query_points("t", query=[0.1, 0.2, 0.3, 0.4], limit=1).points
    assert hits and hits[0].id == 1
    return "in-memory insert + search works"


def splitter():
    from langchain_text_splitters import MarkdownHeaderTextSplitter

    s = MarkdownHeaderTextSplitter([("#", "h1"), ("##", "h2")])
    docs = s.split_text("# A\ntext\n## B\nmore")
    assert len(docs) == 2
    return "markdown header splitting works"


def api_key():
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    var = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "nvidia": "NVIDIA_API_KEY"}.get(provider)
    assert var, f"LLM_PROVIDER must be 'openai', 'anthropic', 'gemini' or 'nvidia', got '{provider}'"
    k = os.getenv(var, "")
    assert k and k != "your_key_here", f"{var} not set in .env"
    return f"{var} found (provider={provider})"


def models():
    from fastembed import SparseTextEmbedding, TextEmbedding
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    emb = TextEmbedding(os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    v = next(iter(emb.embed(["hello"])))
    sp = next(iter(SparseTextEmbedding(os.getenv("SPARSE_MODEL", "Qdrant/bm25")).embed(["M8 bolt 25 Nm"])))
    assert len(sp.indices) > 0
    rr = TextCrossEncoder(os.getenv("RERANKER_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2"))
    s = list(rr.rerank("torque spec", ["bolt torque is 25 Nm", "unrelated text"]))
    assert len(s) == 2
    return f"dense dim={len(v)}, BM25 OK, reranker OK (models downloaded)"


check("Python >= 3.10", python_version)
for label, mod in [("qdrant-client", "qdrant_client"), ("fastembed", "fastembed"),
                   ("langchain-text-splitters", "langchain_text_splitters"),
                   ("openai", "openai"), ("anthropic", "anthropic"), ("google-genai", "google.genai"), ("typer", "typer"), ("pytest", "pytest")]:
    check(f"import {label}", import_pkg(mod))
check("Qdrant local DB", qdrant_local)
check("Markdown splitter", splitter)
check("Docling parser (optional)", import_pkg("docling"))
check("LLM API key", api_key)
if "--models" in sys.argv:
    check("Embedding + reranker models", models)

print()
failed_required = False
for ok, name, detail in RESULTS:
    optional = "optional" in name
    mark = "PASS" if ok else ("SKIP" if optional else "FAIL")
    if not ok and not optional and "API key" not in name:
        failed_required = True
    print(f"[{mark}] {name:<32} {detail}")
print()
print("Ready." if not failed_required else "Some required checks failed. See above.")
sys.exit(1 if failed_required else 0)
