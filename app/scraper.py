import json
import re
from datetime import datetime

from curl_cffi import requests

URL = "https://www.bloomberg.com/billionaires/"

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

MALE_PATTERNS = [re.compile(p) for p in [
    r"\bhe\b", r"\bhis\b", r"\bhim\b", r"\bhimself\b",
    r"\bson\b", r"\bfather\b", r"\bbusinessman\b", r"\bchairman\b",
]]
FEMALE_PATTERNS = [re.compile(p) for p in [
    r"\bshe\b", r"\bher\b", r"\bherself\b",
    r"\bdaughter\b", r"\bmother\b", r"\bbusinesswoman\b",
    r"\bchairwoman\b", r"\bgranddaughter\b",
]]


def extract_birth_year(person):
    milestones = person.get("milestones", [])
    if milestones:
        first = milestones[0]
        event = first.get("event", "").lower()
        if "born" in event or "birth" in event:
            return first.get("year")
    age = person.get("age")
    if age:
        return datetime.now().year - age
    return None


def infer_gender(person):
    text = " ".join([
        person.get("biography", ""),
        person.get("overview", ""),
        person.get("netWorthSummary", ""),
    ]).lower()
    male_count = sum(len(p.findall(text)) for p in MALE_PATTERNS)
    female_count = sum(len(p.findall(text)) for p in FEMALE_PATTERNS)
    total = male_count + female_count
    if total == 0:
        return "unknown", 0.0
    if male_count > female_count:
        return "male", round(male_count / total, 2)
    elif female_count > male_count:
        return "female", round(female_count / total, 2)
    return "unknown", 0.0


def flatten_person(person):
    row = {}
    row["person_id"] = person.get("personId")
    row["rank"] = person.get("rank")
    row["full_name"] = person.get("fullName")
    row["common_name"] = person.get("commonName")
    row["first_name"] = person.get("firstName")
    row["last_name"] = person.get("lastName")
    row["middle_name"] = person.get("middleName")
    row["citizenship"] = person.get("citizenship")
    row["age"] = person.get("age")
    row["birth_year"] = extract_birth_year(person)
    gender, gender_confidence = infer_gender(person)
    row["gender"] = gender
    row["gender_confidence"] = gender_confidence
    row["industry"] = person.get("industry")
    row["sector"] = person.get("sector")
    row["net_worth_usd"] = person.get("worth")
    row["last_change_usd"] = person.get("lastChange")
    row["last_change_pct"] = person.get("lastPercentChange")
    row["ytd_change_usd"] = person.get("ytdChange")
    row["ytd_change_pct"] = person.get("ytdPercentChange")
    row["public_assets_total"] = person.get("publicAssetsTotal")
    row["private_assets_total"] = person.get("privateAssetsTotal")
    row["cash_assets_total"] = person.get("cashAssetsTotal")
    public_assets = person.get("publicAssets", [])
    row["public_assets_json"] = json.dumps(
        [{"ticker": a.get("ticker"), "value": a.get("value")} for a in public_assets]
    ) if public_assets else None
    private_assets = person.get("privateAssets", [])
    row["private_assets_json"] = json.dumps(
        [{"name": a.get("name"), "value": a.get("value")} for a in private_assets]
    ) if private_assets else None
    cash_assets = person.get("cashAssets", [])
    row["cash_asset_value"] = cash_assets[0].get("value") if cash_assets else None
    liabilities = person.get("miscLiabilities", [])
    if liabilities:
        row["liabilities_value"] = liabilities[0].get("value")
        row["liabilities_note"] = liabilities[0].get("note", "").strip()
    else:
        row["liabilities_value"] = None
        row["liabilities_note"] = None
    schools = person.get("schools", [])
    row["schools_json"] = json.dumps(
        [s.get("school") for s in schools]
    ) if schools else None
    intelligence = person.get("intelligence", [])
    row["facts_json"] = json.dumps(intelligence) if intelligence else None
    milestones = person.get("milestones", [])
    row["milestones_json"] = json.dumps(milestones) if milestones else None
    row["biography"] = person.get("biography")
    row["overview"] = person.get("overview")
    row["net_worth_summary"] = person.get("netWorthSummary")
    row["slug"] = person.get("slug")
    row["confidence"] = person.get("confidence")
    return row


def scrape_billionaires():
    """Fetch and parse Bloomberg billionaires data. Returns list of dicts."""
    response = requests.get(URL, impersonate="chrome", headers=HEADERS)
    if response.status_code != 200:
        raise RuntimeError(f"Request failed: {response.status_code}")
    if "robot" in response.text[:1000].lower():
        raise RuntimeError("Blocked by bot detection")
    match = re.search(
        r"window\.top500\s*=\s*(\[.*?\])\s*;?\s*(?:</script>|window\.)",
        response.text,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError("Could not find window.top500 data in page")
    data = json.loads(match.group(1))
    if len(data) < 400:
        raise RuntimeError(f"Partial data: only {len(data)} records")
    scraped_at = datetime.now().isoformat()
    rows = []
    for person in data:
        row = flatten_person(person)
        row["scraped_at"] = scraped_at
        row["updated_at"] = scraped_at
        rows.append(row)
    return rows
