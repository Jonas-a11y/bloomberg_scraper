"""Birth-country → residence-country flows from Wikidata metadata.

Each ``flows`` entry: ``{birth, residence, count, total_wealth_usd,
is_self_flow, sample_people: [{person_id, name, wealth}]}``. The UI
suppresses self-flows on the Sankey but keeps them aggregated in
the country totals (``nodes``).
"""
from __future__ import annotations

import json as _json
from collections import defaultdict

from app.database import get_db, get_network_db


# The Wikidata fields look like "City, Region, Country" or just "City".
# We try the trailing comma-chunk first, fall back to any chunk that
# matches a known country name, and finally lean on the person's
# citizenship if nothing parses. Without this heuristic, US entries
# like "Pretoria" (no comma) would get classified as "Pretoria".
_KNOWN_COUNTRIES = {
    "United States", "United Kingdom", "Canada", "Australia",
    "China", "India", "Russia", "Russian Federation", "Germany",
    "France", "Italy", "Spain", "Japan", "South Korea", "Brazil",
    "Mexico", "Saudi Arabia", "United Arab Emirates", "Israel",
    "South Africa", "Switzerland", "Sweden", "Norway", "Denmark",
    "Netherlands", "Belgium", "Austria", "Finland", "Ireland",
    "Singapore", "Hong Kong", "Taiwan", "Thailand", "Indonesia",
    "Malaysia", "Philippines", "Vietnam", "Turkey", "Egypt",
    "Nigeria", "Kenya", "Ukraine", "Poland", "Czech Republic",
    "Greece", "Portugal", "Argentina", "Chile", "Colombia",
    "Peru", "Venezuela", "New Zealand", "Lebanon", "Cyprus",
    "Monaco", "Liechtenstein", "Luxembourg",
}

# Different name forms for the same country collapse to a canonical
# label so the flow chart doesn't have separate "Russia" /
# "Russian Federation" nodes etc.
_NORMALIZE = {
    "Russian Federation": "Russia",
    "Republic of China": "China",
    "PRC": "China",
    "U.S.": "United States",
    "USA": "United States",
    "UK": "United Kingdom",
    "U.K.": "United Kingdom",
}


def _country(s, fallback=None):
    """Resolve a Wikidata place string to a canonical country, or None."""
    if not s:
        return _NORMALIZE.get(fallback, fallback) if fallback in _KNOWN_COUNTRIES else None
    tail = s.split(",")[-1].strip()
    if tail in _KNOWN_COUNTRIES:
        return _NORMALIZE.get(tail, tail)
    for chunk in s.split(","):
        c = chunk.strip()
        if c in _KNOWN_COUNTRIES:
            return _NORMALIZE.get(c, c)
    if fallback and fallback in _KNOWN_COUNTRIES:
        return _NORMALIZE.get(fallback, fallback)
    return None


def geo_migration():
    main = get_db()
    persons = {
        r["person_id"]: dict(r) for r in main.execute(
            """
            SELECT p.person_id, p.common_name, p.citizenship,
                   s.net_worth_usd
            FROM persons p
            LEFT JOIN snapshots s ON s.person_id = p.person_id
              AND s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
            """,
        ).fetchall()
    }
    main.close()

    net = get_network_db()
    rows = net.execute(
        "SELECT person_id, wikidata_metadata FROM persons_index "
        "WHERE wikidata_metadata IS NOT NULL"
    ).fetchall()
    net.close()

    flows = defaultdict(lambda: {"count": 0, "total_wealth_usd": 0,
                                  "people": []})
    nodes = {}
    for r in rows:
        try:
            blob = _json.loads(r["wikidata_metadata"])
        except (ValueError, TypeError):
            continue
        person = persons.get(r["person_id"], {})
        citizenship = person.get("citizenship")
        birth_c = _country(blob.get("birth_place"), citizenship)
        res_c = _country(blob.get("residence"), citizenship)
        if not birth_c and not res_c:
            continue
        wealth = person.get("net_worth_usd") or 0
        name = person.get("common_name", "?")
        if birth_c and res_c:
            key = (birth_c, res_c)
            flows[key]["count"] += 1
            flows[key]["total_wealth_usd"] += wealth
            flows[key]["people"].append({
                "person_id": r["person_id"],
                "name": name,
                "wealth": wealth,
            })
        for c in (birth_c, res_c):
            if c:
                nodes.setdefault(c, {"persons": 0, "wealth": 0})
                nodes[c]["persons"] += 1
                nodes[c]["wealth"] += wealth

    flow_list = []
    for (b, r), v in flows.items():
        flow_list.append({
            "birth": b, "residence": r,
            "count": v["count"],
            "total_wealth_usd": v["total_wealth_usd"],
            "is_self_flow": b == r,
            # Bumped 5 → 25 so the click-through drill-down can show
            # the whole list. Frontend slices by display preference.
            "sample_people": sorted(
                v["people"], key=lambda p: -p["wealth"]
            )[:25],
        })
    flow_list.sort(key=lambda f: -f["total_wealth_usd"])

    return {
        "flows": flow_list,
        "nodes": [
            {"country": c, "persons": v["persons"], "wealth": v["wealth"]}
            for c, v in sorted(nodes.items(), key=lambda kv: -kv[1]["wealth"])
        ],
    }
