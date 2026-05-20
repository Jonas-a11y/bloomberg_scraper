"""Bridge filtering and edge persistence.

A "bridge" is a Wikidata entity (school, company, board, etc.) that connects
multiple billionaires we track. Singletons add no signal; mega-bridges like
"United States" or "Republican Party" produce dense fan-outs that swamp the
graph view.
"""
from app.database import get_network_db


def filter_bridges(triples, min_links=2, max_links=50):
    """Keep entities that connect between min_links and max_links distinct
    persons. Returns (kept_triples, kept_qids)."""
    persons_per_entity = {}
    for person_qid, _role, entity_qid in triples:
        persons_per_entity.setdefault(entity_qid, set()).add(person_qid)
    keep = {
        e for e, ps in persons_per_entity.items()
        if min_links <= len(ps) <= max_links
    }
    return [t for t in triples if t[2] in keep], keep


def write_entities_and_links(triples, metadata):
    """Replace prior wikidata-sourced entity rows + entity_links with the new set."""
    conn = get_network_db()
    qid_to_pid = {
        row["wikidata_qid"]: row["person_id"]
        for row in conn.execute(
            "SELECT person_id, wikidata_qid FROM persons_index "
            "WHERE wikidata_qid IS NOT NULL"
        ).fetchall()
    }

    conn.execute("DELETE FROM entity_links WHERE source = 'wikidata'")
    conn.execute(
        "DELETE FROM entities WHERE entity_id NOT IN "
        "(SELECT entity_id FROM entity_links)"
    )

    for entity_qid, (name, kind) in metadata.items():
        conn.execute(
            "INSERT OR IGNORE INTO entities (qid, name, kind) VALUES (?, ?, ?)",
            (entity_qid, name, kind),
        )

    qid_to_eid = {
        row["qid"]: row["entity_id"]
        for row in conn.execute("SELECT entity_id, qid FROM entities").fetchall()
    }

    entities_added = len(metadata)
    links_added = 0
    for person_qid, role, entity_qid in triples:
        pid = qid_to_pid.get(person_qid)
        eid = qid_to_eid.get(entity_qid)
        if pid and eid:
            cur = conn.execute(
                "INSERT OR IGNORE INTO entity_links "
                "(person_id, entity_id, role, source) VALUES (?, ?, ?, 'wikidata')",
                (pid, eid, role),
            )
            links_added += cur.rowcount
    conn.commit()
    conn.close()
    return entities_added, links_added


def write_edges(edges):
    conn = get_network_db()
    qid_to_pid = {
        row["wikidata_qid"]: row["person_id"]
        for row in conn.execute(
            "SELECT person_id, wikidata_qid FROM persons_index "
            "WHERE wikidata_qid IS NOT NULL"
        ).fetchall()
    }
    conn.execute("DELETE FROM family_edges WHERE source = 'wikidata'")
    written = 0
    for s_qid, kind, o_qid in edges:
        s_pid = qid_to_pid.get(s_qid)
        o_pid = qid_to_pid.get(o_qid)
        if s_pid and o_pid and s_pid != o_pid:
            cur = conn.execute(
                "INSERT OR IGNORE INTO family_edges "
                "(person_id, related_id, kind, source) VALUES (?, ?, ?, 'wikidata')",
                (s_pid, o_pid, kind),
            )
            written += cur.rowcount
    conn.commit()
    conn.close()
    return written
