def test_package_imports():
    import engrag  # noqa: F401


def test_qdrant_roundtrip():
    from qdrant_client import QdrantClient, models

    c = QdrantClient(":memory:")
    c.create_collection("t", vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
    c.upsert("t", [models.PointStruct(id=1, vector=[1.0, 0.0], payload={"doc": "a"})])
    assert c.query_points("t", query=[1.0, 0.0], limit=1).points[0].payload["doc"] == "a"
