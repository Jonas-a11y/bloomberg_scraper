import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.database import init_db, insert_scrape_data


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.database
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app.database, "DB_PATH", db_path)
    init_db(str(db_path))
    from app.main import app
    return TestClient(app)


@pytest.fixture
def seeded_client(client, tmp_path):
    db_path = str(tmp_path / "test.db")
    rows = [
        {
            "scraped_at": "2026-05-12T08:00:00",
            "updated_at": "2026-05-12T08:00:00",
            "person_id": i,
            "rank": i,
            "common_name": f"Person {i}",
            "full_name": f"Person {i} Full",
            "first_name": "FIRST",
            "last_name": f"LAST{i}",
            "middle_name": None,
            "citizenship": "United States" if i % 2 == 0 else "France",
            "age": 40 + i,
            "birth_year": 1986 - i,
            "gender": "male" if i % 3 != 0 else "female",
            "gender_confidence": 0.9,
            "industry": "Technology" if i <= 3 else "Finance",
            "net_worth_usd": (10 - i) * 100000000000,
            "last_change_usd": 1000000000,
            "last_change_pct": 1.0,
            "ytd_change_usd": 5000000000,
            "ytd_change_pct": 5.0,
            "public_assets_total": 50000000000,
            "private_assets_total": 30000000000,
            "cash_assets_total": 0,
            "public_assets_json": None,
            "private_assets_json": None,
            "cash_asset_value": None,
            "liabilities_value": None,
            "liabilities_note": None,
            "schools_json": None,
            "facts_json": None,
            "milestones_json": None,
            "biography": "A test person.",
            "overview": "Overview.",
            "net_worth_summary": "Summary.",
            "slug": f"person-{i}",
            "confidence": 3,
        }
        for i in range(1, 6)
    ]
    insert_scrape_data(db_path, rows)
    return client


def test_dashboard_stats(seeded_client):
    resp = seeded_client.get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 5
    assert data["snapshots"] == 1
    assert data["total_wealth"] > 0


def test_billionaires_list(seeded_client):
    resp = seeded_client.get("/api/billionaires")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["data"]) == 5
    assert data["data"][0]["rank"] == 1


def test_billionaires_filter_country(seeded_client):
    resp = seeded_client.get("/api/billionaires?country=France")
    assert resp.status_code == 200
    data = resp.json()
    assert all(b["citizenship"] == "France" for b in data["data"])


def test_search(seeded_client):
    resp = seeded_client.get("/api/search?q=Person 3")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert "Person 3" in data[0]["common_name"]


def test_analytics_by_industry(seeded_client):
    resp = seeded_client.get("/api/analytics/by-industry")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0
    assert "industry" in data[0]
    assert "total_wealth" in data[0]


def test_analytics_by_country(seeded_client):
    resp = seeded_client.get("/api/analytics/by-country")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0


def test_analytics_demographics(seeded_client):
    resp = seeded_client.get("/api/analytics/demographics")
    assert resp.status_code == 200
    data = resp.json()
    assert "gender" in data
    assert "age_distribution" in data


def test_snapshots_list(seeded_client):
    resp = seeded_client.get("/api/snapshots")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1


def test_snapshots_compare(seeded_client):
    resp = seeded_client.get("/api/snapshots/compare?from_date=2026-05-12&to_date=2026-05-12")
    assert resp.status_code == 200
