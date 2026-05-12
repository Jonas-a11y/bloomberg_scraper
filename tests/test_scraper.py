from app.scraper import scrape_billionaires, extract_birth_year, infer_gender, flatten_person


def test_extract_birth_year_from_milestones():
    person = {"milestones": [{"year": 1971, "event": "Born in Pretoria."}], "age": 54}
    assert extract_birth_year(person) == 1971


def test_extract_birth_year_from_age():
    person = {"milestones": [{"year": 2000, "event": "Founded company."}], "age": 50}
    from datetime import datetime
    assert extract_birth_year(person) == datetime.now().year - 50


def test_infer_gender_male():
    person = {"biography": "He founded the company. His vision was clear.", "overview": "", "netWorthSummary": ""}
    gender, conf = infer_gender(person)
    assert gender == "male"
    assert conf > 0.8


def test_infer_gender_female():
    person = {"biography": "She is the daughter of the founder. Her leadership transformed the company.", "overview": "", "netWorthSummary": ""}
    gender, conf = infer_gender(person)
    assert gender == "female"
    assert conf > 0.6


def test_flatten_person():
    person = {
        "personId": 1, "rank": 1, "fullName": "Test Person", "commonName": "Test",
        "firstName": "TEST", "lastName": "PERSON", "middleName": None,
        "citizenship": "US", "age": 50, "industry": "Tech", "sector": "Tech",
        "worth": 100, "lastChange": 10, "lastPercentChange": 1.0,
        "ytdChange": 50, "ytdPercentChange": 5.0,
        "fWorth": "$100", "fLastChange": "+$10", "fYtdChange": "+$50",
        "fLastPercentChange": "+1%", "fYtdPercentChange": "+5%",
        "publicAssetsTotal": 80, "privateAssetsTotal": 20, "cashAssetsTotal": 0,
        "publicAssets": [], "privateAssets": [], "cashAssets": [], "miscLiabilities": [],
        "schools": [], "intelligence": [], "milestones": [],
        "biography": "He is a businessman.", "overview": "", "netWorthSummary": "",
        "slug": "test", "confidence": 3,
    }
    row = flatten_person(person)
    assert row["person_id"] == 1
    assert row["rank"] == 1
    assert row["net_worth_usd"] == 100
    assert row["gender"] == "male"
