import os
import tempfile

from app.database import get_db, init_db, insert_scrape_data, get_latest_snapshot


def test_init_db_creates_tables():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        init_db(db_path)
        db = get_db(db_path)
        cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        db.close()
        assert "persons" in tables
        assert "snapshots" in tables
        assert "scrape_runs" in tables
        assert "schedule_config" in tables


def test_insert_and_query():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "test.db")
        init_db(db_path)
        rows = [
            {
                "scraped_at": "2026-05-12T08:00:00",
                "updated_at": "2026-05-12T08:00:00",
                "person_id": 1,
                "rank": 1,
                "common_name": "Test Person",
                "full_name": "Test A Person",
                "first_name": "TEST",
                "last_name": "PERSON",
                "middle_name": "A",
                "citizenship": "United States",
                "age": 50,
                "birth_year": 1976,
                "gender": "male",
                "gender_confidence": 0.95,
                "industry": "Technology",
                "sector": "Technology",
                "net_worth_usd": 100000000000,
                "last_change_usd": 1000000000,
                "last_change_pct": 1.0,
                "ytd_change_usd": 5000000000,
                "ytd_change_pct": 5.0,
                "public_assets_total": 80000000000,
                "private_assets_total": 20000000000,
                "cash_assets_total": 0,
                "public_assets_json": '[{"ticker":"TEST","value":80000000000}]',
                "private_assets_json": None,
                "cash_asset_value": None,
                "liabilities_value": None,
                "liabilities_note": None,
                "schools_json": None,
                "facts_json": None,
                "milestones_json": None,
                "biography": "A test person.",
                "overview": "Test overview.",
                "net_worth_summary": "Test summary.",
                "slug": "test-person",
                "confidence": 3,
            }
        ]
        insert_scrape_data(db_path, rows)
        result = get_latest_snapshot(db_path)
        assert len(result) == 1
        assert result[0]["common_name"] == "Test Person"
        assert result[0]["net_worth_usd"] == 100000000000
