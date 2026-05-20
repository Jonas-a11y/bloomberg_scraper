"""QID resolution: maps each known billionaire to their Wikidata QID."""
import time

from app.database import get_db, get_network_db

from .constants import BUSINESS_HINTS
from .wikidata import search_candidates


def looks_like_billionaire(description):
    if not description:
        return False
    d = description.lower()
    return any(h in d for h in BUSINESS_HINTS)


def resolve_qid(name):
    candidates = search_candidates(name)
    for c in candidates:
        if looks_like_billionaire(c.get("description")):
            return c["id"]
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
        name = full_names.get(row["person_id"]) or row["common_name"]
        if name:
            qid = resolve_qid(name)
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
