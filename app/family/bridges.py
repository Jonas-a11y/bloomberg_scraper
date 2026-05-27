"""Bridge filtering and edge persistence.

A "bridge" is a Wikidata entity (school, company, board, etc.) that connects
multiple billionaires we track. Singletons add no signal; mega-bridges like
"United States" or "Republican Party" produce dense fan-outs that swamp the
graph view.
"""
from app.database import get_network_db


def filter_bridges(triples, min_links=2, max_links=75):
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
    """Replace prior wikidata-sourced entity rows + entity_links with the new set.

    `metadata` is {qid: dict} where dict has at least name + kind, optionally
    description / inception_year / country / industry / website /
    employee_count / revenue_usd / wikipedia_url.
    """
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
        "(SELECT entity_id FROM entity_links "
        " UNION SELECT entity_a_id FROM entity_edges "
        " UNION SELECT entity_b_id FROM entity_edges)"
    )

    cols = ("qid", "name", "kind", "description", "inception_year", "country",
            "industry", "website", "employee_count", "revenue_usd",
            "wikipedia_url")
    placeholders = ", ".join("?" * len(cols))
    update_assignments = ", ".join(f"{c} = excluded.{c}" for c in cols[1:])
    insert_sql = (
        f"INSERT INTO entities ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(qid) DO UPDATE SET {update_assignments}"
    )

    for entity_qid, meta in metadata.items():
        conn.execute(insert_sql, (
            entity_qid,
            meta.get("name"),
            meta.get("kind"),
            meta.get("description"),
            meta.get("inception_year"),
            meta.get("country"),
            meta.get("industry"),
            meta.get("website"),
            meta.get("employee_count"),
            meta.get("revenue_usd"),
            meta.get("wikipedia_url"),
        ))

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


def write_entity_edges(edges):
    """Replace prior wikidata-sourced entity_edges with `edges`.

    `edges` is a list of (subject_qid, kind, object_qid). Edges where either
    endpoint isn't in the entities table are skipped silently."""
    conn = get_network_db()
    qid_to_eid = {
        row["qid"]: row["entity_id"]
        for row in conn.execute("SELECT entity_id, qid FROM entities").fetchall()
    }
    conn.execute("DELETE FROM entity_edges WHERE source = 'wikidata'")
    written = 0
    for s_qid, kind, o_qid in edges:
        a = qid_to_eid.get(s_qid)
        b = qid_to_eid.get(o_qid)
        if a and b and a != b:
            cur = conn.execute(
                "INSERT OR IGNORE INTO entity_edges "
                "(entity_a_id, entity_b_id, kind, source) VALUES (?, ?, ?, 'wikidata')",
                (a, b, kind),
            )
            written += cur.rowcount
    conn.commit()
    conn.close()
    return written


def write_second_tier(metadata, edges):
    """Insert second-tier entities (with metadata) and their entity_edges.

    Second-tier entities are not connected to any person directly — they exist
    purely to bridge two or more first-tier bridges (e.g. a holding company
    that owns subsidiaries already in the graph). They show up in the graph
    via entity_edges only; pruning queries elsewhere preserve them by
    checking entity_edges membership.

    Returns (entities_inserted, edges_inserted)."""
    if not metadata and not edges:
        return 0, 0
    conn = get_network_db()

    cols = ("qid", "name", "kind", "description", "inception_year", "country",
            "industry", "website", "employee_count", "revenue_usd",
            "wikipedia_url")
    placeholders = ", ".join("?" * len(cols))
    update_assignments = ", ".join(f"{c} = excluded.{c}" for c in cols[1:])
    insert_sql = (
        f"INSERT INTO entities ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(qid) DO UPDATE SET {update_assignments}"
    )
    for entity_qid, meta in metadata.items():
        conn.execute(insert_sql, (
            entity_qid,
            meta.get("name"),
            meta.get("kind"),
            meta.get("description"),
            meta.get("inception_year"),
            meta.get("country"),
            meta.get("industry"),
            meta.get("website"),
            meta.get("employee_count"),
            meta.get("revenue_usd"),
            meta.get("wikipedia_url"),
        ))

    qid_to_eid = {
        row["qid"]: row["entity_id"]
        for row in conn.execute("SELECT entity_id, qid FROM entities").fetchall()
    }
    edges_inserted = 0
    for s_qid, kind, o_qid in edges:
        a = qid_to_eid.get(s_qid)
        b = qid_to_eid.get(o_qid)
        if a and b and a != b:
            cur = conn.execute(
                "INSERT OR IGNORE INTO entity_edges "
                "(entity_a_id, entity_b_id, kind, source) VALUES (?, ?, ?, 'wikidata')",
                (a, b, kind),
            )
            edges_inserted += cur.rowcount
    conn.commit()
    conn.close()
    return len(metadata), edges_inserted


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
