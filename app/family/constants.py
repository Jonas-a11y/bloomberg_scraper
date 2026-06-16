"""Wikidata property maps and entity-kind classification."""

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
USER_AGENT = "bloomberg-scraper/1.0 (https://github.com/jonasgiessler/bloomberg_scraper)"

RELATION_PROPS = {
    "P22": "father",
    "P25": "mother",
    "P26": "spouse",
    "P40": "child",
    "P3373": "sibling",
    "P1038": "relative",
}

# person -> entity (forward)
# Note: P166 (award) and P39 (position_held) are not listed here — both attach
# meaningful structure to a generic class node ("honorary doctorate", "board
# member"), so we instead pull them statement-level via fetch_award_relations
# and fetch_position_relations and re-target at the conferring/affiliated org.
# P551 (residence) is also omitted: bridging billionaires through "lives in
# San Francisco" was noise, not signal.
ENTITY_PROPS_FORWARD = {
    "P108": "employer",
    "P69": "educated_at",
    "P463": "member_of",
    "P3320": "board_member",
    "P800": "notable_work",
    "P102": "political_party",
    "P1416": "affiliated_with",
    "P1830": "owner_of",
    "P1344": "participant_in",
}

# entity -> person (inverse). The wikidata edge points entity → person, so
# when persisting we flip it so the person is always the subject.
ENTITY_PROPS_INVERSE = {
    "P112": "founded",
    "P488": "chair",
    "P127": "owner_of",  # entity owned_by person => person owner_of entity
}

# When walking the graph in the opposite direction of the canonical edge,
# the role needs a different verb so the path reads naturally.
# e.g. person —[educated_at]→ school —[alum_of]→ person.
REVERSE_ROLE = {
    # family
    "father": "child", "mother": "child", "child": "parent",
    "spouse": "spouse", "sibling": "sibling", "relative": "relative",
    # person↔entity
    "educated_at": "alum_of", "employer": "employs",
    "member_of": "includes", "board_member": "board_of",
    "notable_work": "created_by",
    "founded": "founded_by", "chair": "chaired_by",
    "position_held": "held_by",
    "political_party": "has_member",
    "owner_of": "owned_by", "participant_in": "had_participant",
    # entity↔entity
    "subsidiary_of": "has_subsidiary", "has_subsidiary": "subsidiary_of",
    "owned_by": "owns", "owns": "owned_by",
    "part_of": "has_part", "has_part": "part_of",
    "affiliated_with": "affiliated_with",
}

# entity ↔ entity (forward; the subject is the "from" side in our edges)
ENTITY_TO_ENTITY_PROPS = {
    "P749": "subsidiary_of",   # entity -> parent
    "P355": "has_subsidiary",  # entity -> child (we'll flip on store)
    "P127": "owned_by",        # entity -> owner entity
    "P1830": "owns",           # entity -> owned entity (rare for orgs)
    "P463": "member_of",       # entity -> larger org
    "P361": "part_of",         # entity -> larger entity
    "P1416": "affiliated_with",
}

# Wikidata entity types we recognize for visual grouping.
ENTITY_KIND_BY_INSTANCE = {
    "Q4830453": "company", "Q783794": "company", "Q43229": "organization",
    "Q891723": "company", "Q6881511": "company", "Q1058914": "company",
    "Q22687": "company",
    "Q3918": "school", "Q875538": "school", "Q38723": "school",
    "Q1188663": "school", "Q9826": "school", "Q189004": "school",
    "Q157031": "organization", "Q163740": "organization",
    "Q4358176": "board",
    "Q571": "work", "Q11424": "work",
    "Q4164871": "position", "Q294414": "position", "Q702492": "position",
    "Q618779": "award", "Q4220917": "award", "Q193622": "award",
    "Q7278": "party", "Q24649": "party",
    "Q515": "place", "Q1549591": "place", "Q5119": "place", "Q3957": "place",
    "Q6256": "place", "Q484170": "place",
    "Q1656682": "event", "Q18608583": "event", "Q175331": "event",
}

BUSINESS_HINTS = (
    "businessman", "businesswoman", "businessperson",
    "billionaire", "investor",
    "entrepreneur", "ceo", "founder", "co-founder", "banker",
    "magnate", "heiress", "heir ", "tycoon", "executive",
    "chairman", "chairwoman", "chief executive", "chief financial",
    "industrialist", "financier", "philanthropist",
    # Wikidata sometimes describes founders by their company, e.g.
    # "American business" / "owner of …".
    "business", "owner of", "co-owner",
    # Sector-specific framings that show up in real Wikidata descriptions.
    "real estate developer", "media proprietor", "newspaper publisher",
    "venture capital", "hedge fund",
    # Bloomberg lists family-controlled fortunes as e.g. "Walton family";
    # Wikidata describes those as "American family" / "wealthy family".
    "family", "dynasty",
    # Wider noun phrases observed in real Wikidata descriptions of
    # billionaires we were missing:
    "oligarch",                 # Roman Abramovich, Mikhail Fridman
    "fashion designer",          # Ralph Lauren
    "real estate", "property developer",
    "fund manager", "asset manager", "money manager",
    "trader",                    # Stan Druckenmiller, Steve Cohen
    "art dealer", "art collector",
    "shipping", "shipowner",
    "manufacturer",              # car/textile manufacturers without "industrialist"
    # NOTE: do NOT add "landowner", "duke", "earl", "baron", "prince" —
    # Wikidata's search frequently returns 19th-century aristocrats
    # ahead of the current titled holder. The current Hugh Grosvenor
    # (7th Duke, b. 1991) IS already described as "British aristocrat
    # and businessman" — "businessman" catches him without dragging in
    # the 1825-1899 1st Duke whose description reads "English landowner".
)


