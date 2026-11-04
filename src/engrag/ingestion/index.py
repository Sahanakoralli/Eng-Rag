"""Step 3: store chunks in Qdrant with a dense vector, a BM25 sparse vector, and metadata."""
import uuid

from qdrant_client import QdrantClient, models

DENSE = "dense"
SPARSE = "bm25"
_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def get_client(path: str) -> QdrantClient:
    return QdrantClient(path=path) if path != ":memory:" else QdrantClient(":memory:")


def ensure_collection(client: QdrantClient, name: str, dim: int) -> None:
    if client.collection_exists(name):
        return
    client.create_collection(
        collection_name=name,
        vectors_config={DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE)},
        sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )


def delete_doc(client: QdrantClient, name: str, doc_id: str) -> None:
    if not client.collection_exists(name):
        return
    client.delete(
        collection_name=name,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
            )
        ),
    )


def upsert_chunks(client, name, doc_id, payloads, dense_vecs, sparse_vecs, batch=64) -> int:
    points = []
    for i, (pl, dv, (idx, val)) in enumerate(zip(payloads, dense_vecs, sparse_vecs)):
        points.append(
            models.PointStruct(
                id=str(uuid.uuid5(_NS, f"{doc_id}::{i}")),
                vector={DENSE: dv, SPARSE: models.SparseVector(indices=idx, values=val)},
                payload=pl,
            )
        )
    for s in range(0, len(points), batch):
        client.upsert(collection_name=name, points=points[s : s + batch])
    return len(points)


def count(client: QdrantClient, name: str) -> int:
    return client.count(collection_name=name, exact=True).count if client.collection_exists(name) else 0
