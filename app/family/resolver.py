"""QID resolution: maps each known billionaire to their Wikidata QID."""
import re
import time

from app.database import get_db, get_network_db

from .constants import BUSINESS_HINTS
from .wikidata import search_candidates


# Common English nicknames → likely Wikidata label form. Bloomberg
# almost always uses the diminutive (Stan, Phil, Ed, Bob…) while
# Wikidata sometimes only carries the full given name. When the
# diminutive search returns nothing, retry with the expansion.
NICKNAME_EXPANSIONS = {
    "Stan": "Stanley",
    "Phil": "Philip",
    "Ed": "Edward",
    "Bob": "Robert",
    "Bill": "William",
    "Jim": "James",
    "Tom": "Thomas",
    "Dave": "David",
    "Dick": "Richard",
    "Rick": "Richard",
    "Steve": "Stephen",
    "Mike": "Michael",
    "Jack": "John",
    "Joe": "Joseph",
    "Ted": "Theodore",
    "Tony": "Anthony",
    "Sam": "Samuel",
    "Andy": "Andrew",
    "Chris": "Christopher",
    "Dan": "Daniel",
    "Greg": "Gregory",
    "Ken": "Kenneth",
    "Larry": "Lawrence",
    "Nick": "Nicholas",
    "Pat": "Patrick",
    "Pete": "Peter",
    "Rob": "Robert",
    "Ron": "Ronald",
    "Russ": "Russell",
    "Tim": "Timothy",
    "Will": "William",
    "Eddie": "Edward",
    "Lenny": "Leonard",
    "Mick": "Michael",
}

# Romanization fallbacks: Bloomberg writes "Wuerth" / "Mueller" / "Goetz",
# Wikidata uses the umlaut form. ß → ss is asymmetric: many German names
# use both (Strauß / Strauss). We try both directions.
DIACRITIC_PAIRS = [
    ("ue", "ü"), ("oe", "ö"), ("ae", "ä"),
    ("Ue", "Ü"), ("Oe", "Ö"), ("Ae", "Ä"),
    ("ss", "ß"),
]


def looks_like_billionaire(description):
    if not description:
        return False
    d = description.lower()
    return any(h in d for h in BUSINESS_HINTS)


def looks_like_disqualifying(description):
    """Hard-skip candidates whose description proves they're NOT a person
    we want — buildings, streets, awards, dictionaries, family-name
    pages, etc. Without this filter the wider hint list below would
    occasionally pick a noun-phrase instead of a person."""
    if not description:
        return False
    d = description.lower()
    BAD = (
        "family name", "given name", "surname",
        "street", "road", "avenue", "boulevard",
        "mansion", "house in", "building in",
        "dictionary", "wikimedia",
        "ditch", "canal",
        "scientific article", "academic paper",
    )
    return any(b in d for b in BAD)


def _query_variants(name):
    """Generate progressive query variants, in order of decreasing
    Bloomberg-style faithfulness:

      1. as-is
      2. strip ' & family' / ' and family' / parentheticals
      3. romanization variants (ue↔ü, ß↔ss …)
      4. nickname → full name (Stan → Stanley)

    Tried in order; the first that returns ≥1 candidate wins. Yields
    UNIQUE strings only, so the search HTTP layer never repeats."""
    seen = set()

    def emit(s):
        s = (s or "").strip()
        if s and s not in seen:
            seen.add(s)
            yield s

    if not name:
        return
    yield from emit(name)

    # Strip family / parenthetical / "Sr."-style suffixes.
    cleaned = re.sub(r"\s*&\s*family\s*$", "", name, flags=re.I)
    cleaned = re.sub(r"\s+and\s+family\s*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*\([^)]*\)\s*", " ", cleaned).strip()
    yield from emit(cleaned)

    # Strip a single-letter middle initial — Bloomberg writes "Galen G
    # Weston" / "Stan F Druckenmiller", Wikidata search returns 0 hits
    # on those but resolves the unprefixed form fine. Pattern: a name
    # token that's exactly one letter (with or without a trailing dot)
    # in the middle of the string. We don't strip leading/trailing
    # single tokens (could be the actual first or last name).
    no_middle = re.sub(r"(?<=\S)\s+[A-Z]\.?\s+(?=\S)", " ", cleaned)
    if no_middle != cleaned:
        yield from emit(no_middle)
        cleaned = no_middle  # downstream variants apply to the cleaner form

    # Romanization swaps. Apply pair-by-pair on the cleaned form.
    base = cleaned
    for asciic, uni in DIACRITIC_PAIRS:
        if asciic in base:
            yield from emit(base.replace(asciic, uni))
        if uni in base:
            yield from emit(base.replace(uni, asciic))

    # Nickname expansion on the first whitespace token.
    parts = cleaned.split()
    if parts and parts[0] in NICKNAME_EXPANSIONS:
        expanded = NICKNAME_EXPANSIONS[parts[0]] + " " + " ".join(parts[1:])
        yield from emit(expanded)


def resolve_qid(name):
    """Resolve a person name to a Wikidata QID.

    Strategy (in order):
      1. For each query variant (see `_query_variants`), pull candidates.
      2. Within a candidate set, the first whose description matches a
         business hint wins. Hard-disqualifiers (street, family-name
         page, mansion) are skipped regardless.
      3. Single-candidate fallback. If only ONE candidate came back
         AND its description doesn't disqualify, accept it — there's
         no homonym risk.

    We deliberately do NOT auto-accept the top-1 just because its
    description starts with a demonym. That misfires badly on common
    names — "David Thomson" returns the British film critic before
    the Canadian media billionaire, "Hugh Grosvenor" returns the 1st
    Duke (1825–1899) before the current 7th Duke (the actual entry
    on the Bloomberg list). Better to leave a name unresolved than
    pollute the family graph with the wrong QID.
    """
    for variant in _query_variants(name):
        candidates = search_candidates(variant)
        if not candidates:
            continue
        # Filter out the obvious non-person candidates first.
        viable = [c for c in candidates if not looks_like_disqualifying(c.get("description"))]
        if not viable:
            continue

        # Step 1: business-hint match. Walks all viable candidates,
        # not just the top one — Wikidata sometimes ranks a homonym
        # historian above the billionaire we want.
        for c in viable:
            if looks_like_billionaire(c.get("description")):
                return c["id"]

        # Step 2: single-candidate accept if description exists.
        if len(viable) == 1 and viable[0].get("description"):
            return viable[0]["id"]

    return None


def sync_persons_index():
    """Mirror bloomberg.db's persons (id, common_name) into the network DB,
    preserving any existing wikidata_qid. Makes network.db self-contained
    for downloads."""
    main = get_db()
    persons = main.execute(
        "SELECT person_id, common_name FROM persons"
    ).fetchall()
    main.close()

    net = get_network_db()
    for row in persons:
        net.execute(
            """
            INSERT INTO persons_index (person_id, common_name, wikidata_qid)
            VALUES (?, ?, NULL)
            ON CONFLICT(person_id) DO UPDATE SET common_name = excluded.common_name
            """,
            (row["person_id"], row["common_name"]),
        )
    net.commit()
    net.close()


def resolve_persons(state, delay=0.2):
    """Resolve QID for every person currently missing one in network.db.
    Updates state["total"], state["done"], state["qids_resolved"]."""
    sync_persons_index()

    net = get_network_db()
    rows = net.execute(
        "SELECT person_id, common_name FROM persons_index WHERE wikidata_qid IS NULL"
    ).fetchall()
    net.close()

    main = get_db()
    full_names = {
        row["person_id"]: row["full_name"]
        for row in main.execute("SELECT person_id, full_name FROM persons").fetchall()
    }
    main.close()

    state["total"] = len(rows)
    state["done"] = 0

    for row in rows:
        # Try common_name first (Wikidata's labels are usually the public
        # name, "Henry Kravis", not Bloomberg's full legal "Henry Roberts
        # Kravis"). Fall back to full_name only if the common search fails.
        common = row["common_name"]
        full = full_names.get(row["person_id"])
        qid = None
        for candidate in (common, full):
            if not candidate or candidate == qid:
                continue
            qid = resolve_qid(candidate)
            if qid:
                break
        if qid:
            net = get_network_db()
            net.execute(
                "UPDATE persons_index SET wikidata_qid = ? WHERE person_id = ?",
                (qid, row["person_id"]),
            )
            net.commit()
            net.close()
            state["qids_resolved"] += 1
        state["done"] += 1
        time.sleep(delay)
