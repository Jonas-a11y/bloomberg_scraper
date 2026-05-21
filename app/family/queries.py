"""Read-only graph queries used by the API layer."""
from collections import deque

from app import database
from app.database import get_db, get_network_db

from .constants import REVERSE_ROLE


def get_graph():
    conn = get_db()
    conn.execute("ATTACH DATABASE ? AS net", (str(database.NETWORK_DB_PATH),))
    person_nodes = [
        dict(row) for row in conn.execute("""
            SELECT p.person_id AS id, p.common_name AS name,
                   p.citizenship, p.industry, ni.wikidata_qid,
                   s.net_worth_usd, s.rank
            FROM persons p
            LEFT JOIN net.persons_index ni ON ni.person_id = p.person_id
            LEFT JOIN snapshots s ON s.person_id = p.person_id
                AND s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
        """).fetchall()
    ]
    family_edges = [
        dict(row) for row in conn.execute("""
            SELECT person_id AS source, related_id AS target, kind
            FROM net.family_edges
        """).fetchall()
    ]
    entities = [
        dict(row) for row in conn.execute("""
            SELECT entity_id AS id, qid, name, kind, description,
                   inception_year, country, industry, website,
                   employee_count, revenue_usd, wikipedia_url
            FROM net.entities
        """).fetchall()
    ]
    entity_links = [
        dict(row) for row in conn.execute("""
            SELECT person_id, entity_id, role FROM net.entity_links
        """).fetchall()
    ]
    entity_edges = [
        dict(row) for row in conn.execute("""
            SELECT entity_a_id AS source, entity_b_id AS target, kind
            FROM net.entity_edges
        """).fetchall()
    ]
    conn.execute("DETACH DATABASE net")
    conn.close()
    return {
        "nodes": person_nodes,
        "edges": family_edges,
        "entities": entities,
        "entity_links": entity_links,
        "entity_edges": entity_edges,
    }


def get_entity_detail(entity_id):
    """Return full metadata for an entity plus its connected persons and entities."""
    net = get_network_db()
    main = get_db()
    try:
        ent = net.execute(
            "SELECT entity_id AS id, qid, name, kind, description, "
            "inception_year, country, industry, website, employee_count, "
            "revenue_usd, wikipedia_url FROM entities WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        if not ent:
            return None

        link_rows = net.execute(
            "SELECT person_id, role FROM entity_links WHERE entity_id = ?",
            (entity_id,),
        ).fetchall()
        person_ids = [r["person_id"] for r in link_rows]
        persons_by_id = {}
        if person_ids:
            placeholders = ",".join("?" * len(person_ids))
            for r in main.execute(
                f"SELECT person_id, common_name FROM persons WHERE person_id IN ({placeholders})",
                person_ids,
            ).fetchall():
                persons_by_id[r["person_id"]] = r["common_name"]
        people = [
            {"person_id": r["person_id"], "name": persons_by_id.get(r["person_id"], "?"),
             "role": r["role"]}
            for r in link_rows
        ]

        edge_rows = net.execute(
            "SELECT entity_a_id, entity_b_id, kind FROM entity_edges "
            "WHERE entity_a_id = ? OR entity_b_id = ?",
            (entity_id, entity_id),
        ).fetchall()
        related_ids = {
            row["entity_b_id"] if row["entity_a_id"] == entity_id else row["entity_a_id"]
            for row in edge_rows
        }
        related_meta = {}
        if related_ids:
            placeholders = ",".join("?" * len(related_ids))
            for r in net.execute(
                f"SELECT entity_id, name, kind FROM entities WHERE entity_id IN ({placeholders})",
                list(related_ids),
            ).fetchall():
                related_meta[r["entity_id"]] = (r["name"], r["kind"])
        related = []
        for row in edge_rows:
            if row["entity_a_id"] == entity_id:
                other_id, kind = row["entity_b_id"], row["kind"]
            else:
                other_id, kind = row["entity_a_id"], REVERSE_ROLE.get(row["kind"], row["kind"])
            name, ekind = related_meta.get(other_id, ("?", "other"))
            related.append({
                "entity_id": other_id, "name": name, "entity_kind": ekind,
                "kind": kind,
            })

        return {**dict(ent), "people": people, "related": related}
    finally:
        net.close()
        main.close()


def find_path(src_id, dst_id):
    """BFS shortest path between two persons through family + entity bridges.
    Returns list of dicts: [{kind: 'person'|'entity', id, name, role?}, ...]
    where each element after the first is reached via the role on its predecessor.
    """
    if src_id == dst_id:
        return []
    main = get_db()
    persons = {
        row["person_id"]: row["common_name"]
        for row in main.execute(
            "SELECT person_id, common_name FROM persons"
        ).fetchall()
    }
    main.close()

    net = get_network_db()
    entities = {
        row["entity_id"]: (row["name"], row["kind"])
        for row in net.execute(
            "SELECT entity_id, name, kind FROM entities"
        ).fetchall()
    }
    family_pairs = net.execute(
        "SELECT person_id, related_id, kind FROM family_edges"
    ).fetchall()
    entity_rows = net.execute(
        "SELECT person_id, entity_id, role FROM entity_links"
    ).fetchall()
    entity_pairs = net.execute(
        "SELECT entity_a_id, entity_b_id, kind FROM entity_edges"
    ).fetchall()
    net.close()

    # adjacency: node = ('person', id) | ('entity', id) -> list of (neighbor, role, direction)
    # direction = 'forward' means the wikidata edge points node->neighbor,
    # 'reverse' means neighbor->node.
    adj = {}
    for row in family_pairs:
        a = ("person", row["person_id"])
        b = ("person", row["related_id"])
        adj.setdefault(a, []).append((b, row["kind"], "forward"))
        adj.setdefault(b, []).append((a, row["kind"], "reverse"))
    for row in entity_rows:
        p = ("person", row["person_id"])
        e = ("entity", row["entity_id"])
        adj.setdefault(p, []).append((e, row["role"], "forward"))
        adj.setdefault(e, []).append((p, row["role"], "reverse"))
    for row in entity_pairs:
        a = ("entity", row["entity_a_id"])
        b = ("entity", row["entity_b_id"])
        adj.setdefault(a, []).append((b, row["kind"], "forward"))
        adj.setdefault(b, []).append((a, row["kind"], "reverse"))

    start = ("person", src_id)
    target = ("person", dst_id)
    if start not in adj:
        return None

    parents = {start: (None, None, None)}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == target:
            break
        for nbr, role, direction in adj.get(node, []):
            if nbr not in parents:
                parents[nbr] = (node, role, direction)
                queue.append(nbr)
    if target not in parents:
        return None

    chain = []
    node = target
    while node is not None:
        prev, role, direction = parents[node]
        display_role = REVERSE_ROLE.get(role, role) if direction == "reverse" else role
        kind, ident = node
        if kind == "person":
            chain.append({
                "kind": "person", "id": ident,
                "name": persons.get(ident, "?"), "role": display_role,
            })
        else:
            ent_name, ent_kind = entities.get(ident, ("?", "other"))
            chain.append({
                "kind": "entity", "id": ident, "name": ent_name,
                "entity_kind": ent_kind, "role": display_role,
            })
        node = prev
    chain.reverse()
    if chain:
        chain[0]["role"] = None  # first node has no incoming role
    return chain
