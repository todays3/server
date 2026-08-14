"""Catalog API smoke."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_sources_catalog_endpoint():
    res = client.get("/api/v1/sources/catalog")
    assert res.status_code == 200
    body = res.json()
    assert "groups" in body
    assert "mega_map" in body
    assert any(g["id"] == "stock-kr" for g in body["groups"])
    assert "IT" in body["mega_map"]
