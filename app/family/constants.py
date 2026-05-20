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
ENTITY_PROPS_FORWARD = {
    "P108": "employer",
    "P69": "educated_at",
    "P463": "member_of",
    "P3320": "board_member",
    "P800": "notable_work",
    "P39": "position_held",
    "P166": "award",
    "P102": "political_party",
    "P551": "residence",
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
    "position_held": "held_by", "award": "awarded_to",
    "political_party": "has_member", "residence": "resident",
    "owner_of": "owned_by", "participant_in": "had_participant",
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
    "businessman", "businesswoman", "billionaire", "investor",
    "entrepreneur", "ceo", "founder", "co-founder", "banker",
    "magnate", "heiress", "heir ", "tycoon", "executive",
    "chairman", "chairwoman", "chief executive",
    "industrialist", "financier", "philanthropist",
)
