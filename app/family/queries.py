"""Read-only graph queries used by the API layer."""
import random
import statistics
from collections import deque

from app import database
from app.database import get_db, get_network_db

from .constants import REVERSE_ROLE


# Six-degrees stats are stable until the graph mutates. Key by (counts of the
# three edge tables) — every refresh path rewrites those tables, so this
# invalidates automatically. Scoped to process lifetime; first call after
# restart pays the ~0.5s cost.
_SIX_DEGREES_CACHE = {}


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
        persons_meta = {}
        if person_ids:
            placeholders = ",".join("?" * len(person_ids))
            wealth_rows = main.execute(f"""
                SELECT p.person_id, p.common_name, s.net_worth_usd, s.rank
                FROM persons p
                LEFT JOIN snapshots s ON p.person_id = s.person_id
                  AND s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
                WHERE p.person_id IN ({placeholders})
            """, person_ids).fetchall()
            for r in wealth_rows:
                persons_meta[r["person_id"]] = dict(r)
        people = []
        for r in link_rows:
            meta = persons_meta.get(r["person_id"], {})
            people.append({
                "person_id": r["person_id"],
                "name": meta.get("common_name", "?"),
                "role": r["role"],
                "net_worth_usd": meta.get("net_worth_usd"),
                "rank": meta.get("rank"),
            })
        people.sort(key=lambda p: p["net_worth_usd"] or 0, reverse=True)
        aggregate_wealth = sum(p["net_worth_usd"] or 0 for p in people)

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
        related_person_counts = {}
        if related_ids:
            placeholders = ",".join("?" * len(related_ids))
            for r in net.execute(
                f"SELECT entity_id, name, kind FROM entities WHERE entity_id IN ({placeholders})",
                list(related_ids),
            ).fetchall():
                related_meta[r["entity_id"]] = (r["name"], r["kind"])
            for r in net.execute(
                f"SELECT entity_id, COUNT(*) AS c FROM entity_links "
                f"WHERE entity_id IN ({placeholders}) GROUP BY entity_id",
                list(related_ids),
            ).fetchall():
                related_person_counts[r["entity_id"]] = r["c"]
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
                "person_count": related_person_counts.get(other_id, 0),
            })
        related.sort(key=lambda r: r["person_count"], reverse=True)

        return {**dict(ent), "people": people, "related": related,
                "aggregate_wealth_usd": aggregate_wealth,
                "person_count": len([p for p in people if p["person_id"]])}
    finally:
        net.close()
        main.close()


def get_person_profile(person_id):
    """Combined profile payload: bloomberg metadata + last snapshot (latest if
    still active, last-seen if dropped), full wealth history, family edges with
    related person names, and entity links with entity metadata.

    Works for both current and dropped-out persons. Returns None if unknown."""
    main = get_db()
    person = main.execute("""
        SELECT p.person_id, p.common_name, p.full_name, p.first_name, p.last_name,
               p.middle_name, p.citizenship, p.age, p.birth_year, p.gender,
               p.gender_confidence, p.industry, p.biography, p.overview,
               p.net_worth_summary, p.schools_json, p.facts_json,
               p.milestones_json, p.slug
        FROM persons p
        WHERE p.person_id = ?
    """, (person_id,)).fetchone()
    if not person:
        main.close()
        return None
    person = dict(person)

    snapshot = main.execute("""
        SELECT s.scraped_at, s.rank, s.net_worth_usd, s.last_change_usd,
               s.last_change_pct, s.ytd_change_usd, s.ytd_change_pct,
               s.public_assets_total, s.private_assets_total, s.cash_assets_total,
               s.public_assets_json, s.private_assets_json,
               s.cash_asset_value, s.liabilities_value, s.liabilities_note
        FROM snapshots s
        WHERE s.person_id = ?
        ORDER BY s.scraped_at DESC LIMIT 1
    """, (person_id,)).fetchone()
    snapshot = dict(snapshot) if snapshot else {}

    latest_at = main.execute(
        "SELECT MAX(scraped_at) FROM snapshots"
    ).fetchone()[0]
    is_active = bool(snapshot) and snapshot.get("scraped_at") == latest_at

    history = [dict(r) for r in main.execute(
        "SELECT date, net_worth_usd FROM wealth_history "
        "WHERE person_id = ? ORDER BY date",
        (person_id,),
    ).fetchall()]
    if not history:
        history = [
            {"date": r["scraped_at"], "net_worth_usd": r["net_worth_usd"]}
            for r in main.execute(
                "SELECT scraped_at, net_worth_usd FROM snapshots "
                "WHERE person_id = ? AND net_worth_usd IS NOT NULL "
                "ORDER BY scraped_at",
                (person_id,),
            ).fetchall()
        ]

    main.close()

    net = get_network_db()
    try:
        family_rows = net.execute("""
            SELECT person_id, related_id, kind FROM family_edges
            WHERE person_id = ? OR related_id = ?
        """, (person_id, person_id)).fetchall()

        related_pids = set()
        for r in family_rows:
            related_pids.add(r["related_id"] if r["person_id"] == person_id else r["person_id"])

        entity_link_rows = net.execute(
            "SELECT entity_id, role FROM entity_links WHERE person_id = ?",
            (person_id,),
        ).fetchall()
        entity_ids = [r["entity_id"] for r in entity_link_rows]
        entities_meta = {}
        if entity_ids:
            placeholders = ",".join("?" * len(entity_ids))
            for r in net.execute(
                f"SELECT entity_id, name, kind, description, country, industry, "
                f"website, wikipedia_url FROM entities WHERE entity_id IN ({placeholders})",
                entity_ids,
            ).fetchall():
                entities_meta[r["entity_id"]] = dict(r)

        qid_row = net.execute(
            "SELECT wikidata_qid, image_url, image_filename, signature_filename, "
            "wikidata_metadata FROM persons_index WHERE person_id = ?",
            (person_id,),
        ).fetchone()
        wikidata_qid = qid_row["wikidata_qid"] if qid_row else None
        image_url = qid_row["image_url"] if qid_row else None
        # Back-compat: older rows only stored the bare filename.
        if not image_url and qid_row and qid_row["image_filename"]:
            from urllib.parse import quote
            image_url = (
                "https://commons.wikimedia.org/wiki/Special:FilePath/"
                + quote(qid_row["image_filename"])
                + "?width=320"
            )
        signature_filename = qid_row["signature_filename"] if qid_row else None
        import json as _json
        try:
            wikidata_meta = _json.loads(qid_row["wikidata_metadata"]) if qid_row and qid_row["wikidata_metadata"] else {}
        except (ValueError, TypeError):
            wikidata_meta = {}
    finally:
        net.close()

    family = []
    if related_pids:
        main = get_db()
        placeholders = ",".join("?" * len(related_pids))
        rel_meta = {
            r["person_id"]: dict(r)
            for r in main.execute(f"""
                SELECT p.person_id, p.common_name, s.rank, s.net_worth_usd
                FROM persons p
                LEFT JOIN snapshots s ON s.person_id = p.person_id
                  AND s.scraped_at = (SELECT MAX(scraped_at) FROM snapshots
                                       WHERE person_id = p.person_id)
                WHERE p.person_id IN ({placeholders})
            """, list(related_pids)).fetchall()
        }
        main.close()
        seen = set()
        for r in family_rows:
            if r["person_id"] == person_id:
                other_id, kind = r["related_id"], r["kind"]
            else:
                other_id, kind = r["person_id"], REVERSE_ROLE.get(r["kind"], r["kind"])
            key = (other_id, kind)
            if key in seen:
                continue
            seen.add(key)
            meta = rel_meta.get(other_id, {})
            family.append({
                "person_id": other_id,
                "name": meta.get("common_name", "?"),
                "kind": kind,
                "rank": meta.get("rank"),
                "net_worth_usd": meta.get("net_worth_usd"),
            })
        family.sort(key=lambda f: (f["net_worth_usd"] or 0), reverse=True)

    entity_links = []
    for r in entity_link_rows:
        meta = entities_meta.get(r["entity_id"], {})
        entity_links.append({
            "entity_id": r["entity_id"],
            "name": meta.get("name", "?"),
            "entity_kind": meta.get("kind"),
            "role": r["role"],
            "description": meta.get("description"),
            "country": meta.get("country"),
            "industry": meta.get("industry"),
            "website": meta.get("website"),
            "wikipedia_url": meta.get("wikipedia_url"),
        })

    main = get_db()
    # Pull the full set; the UI groups by year and decides how much to show.
    # Ordered by date so the year groupings come back in chronological order.
    news = [dict(r) for r in main.execute(
        """
        SELECT article_date, date_precision, title, url, source, importance
        FROM news_articles
        WHERE person_id = ?
        ORDER BY article_date DESC, importance DESC
        LIMIT 500
        """,
        (person_id,),
    ).fetchall()]
    # When was the news last fetched? Drives the "Updated 2h ago" label on
    # the news card so visitors can see freshness.
    news_fetched_row = main.execute(
        "SELECT fetched_at, backfilled FROM news_fetched WHERE person_id = ?",
        (person_id,),
    ).fetchone()
    news_fetched_at = news_fetched_row["fetched_at"] if news_fetched_row else None
    news_backfilled = bool(news_fetched_row["backfilled"]) if news_fetched_row else False
    main.close()

    return {
        **person,
        **snapshot,
        "is_active": is_active,
        "last_seen_at": snapshot.get("scraped_at"),
        "wikidata_qid": wikidata_qid,
        "image_url": image_url,
        "signature_filename": signature_filename,
        "wikidata_metadata": wikidata_meta,
        "history": history,
        "family": family,
        "entity_links": entity_links,
        "news": news,
        "news_fetched_at": news_fetched_at,
        "news_backfilled": news_backfilled,
    }


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


def _top_schools(main, persons, top_n=10, min_alumni=2):
    """Top schools by tracked-billionaire alumni count."""
    rows = main.execute("""
        SELECT e.entity_id AS id, e.name,
               COUNT(DISTINCT l.person_id) AS alumni_count,
               GROUP_CONCAT(l.person_id) AS person_ids_csv
        FROM net.entities e
        JOIN net.entity_links l ON l.entity_id = e.entity_id
        WHERE e.kind = 'school'
        GROUP BY e.entity_id
        HAVING alumni_count >= ?
        ORDER BY alumni_count DESC, e.name
        LIMIT ?
    """, (min_alumni, top_n)).fetchall()
    out = []
    for r in rows:
        pids = [int(p) for p in (r["person_ids_csv"] or "").split(",") if p]
        out.append({
            "id": r["id"], "name": r["name"],
            "alumni_count": r["alumni_count"],
            "alumni": sorted(
                [{"person_id": pid, "name": persons.get(pid, "?")} for pid in set(pids)],
                key=lambda p: p["name"],
            ),
        })
    return out


def _components(family_neighbors):
    """All connected components (lists of person_ids) reachable via family edges."""
    visited = set()
    components = []
    for seed in family_neighbors:
        if seed in visited:
            continue
        component = []
        queue = deque([seed])
        visited.add(seed)
        while queue:
            node = queue.popleft()
            component.append(node)
            for nbr in family_neighbors.get(node, ()):
                if nbr not in visited:
                    visited.add(nbr)
                    queue.append(nbr)
        components.append(component)
    return components


def _dynasties(components, persons, wealth, min_size=3, top_n=5):
    """Roll up family components into dynasty totals.

    Persons missing from `wealth` (e.g. fell off the index) contribute 0 but
    are still counted in `member_count` so the UI can show '$X across N of M'."""
    rollups = []
    for component in components:
        if len(component) < min_size:
            continue
        member_rows = sorted(
            [
                {"person_id": pid, "name": persons.get(pid, "?"),
                 "net_worth_usd": wealth.get(pid)}
                for pid in component
            ],
            key=lambda r: -(r["net_worth_usd"] or 0),
        )
        total = sum((r["net_worth_usd"] or 0) for r in member_rows)
        tracked = sum(1 for r in member_rows if r["net_worth_usd"])
        # Surname heuristic: wealthiest tracked member's last whitespace-split
        # token. Misfires for in-laws / matrilineal clusters — acceptable v1.
        head = next((r for r in member_rows if r["net_worth_usd"]), member_rows[0])
        surname = (head["name"] or "").split()[-1] if head["name"] else "Family"
        rollups.append({
            "label": f"{surname} family",
            "member_count": len(component),
            "tracked_count": tracked,
            "total_worth_usd": total,
            "members": member_rows,
        })
    rollups.sort(key=lambda r: -r["total_worth_usd"])
    return rollups[:top_n]


def _build_connectivity_adj(family_pairs, entity_link_rows, entity_pairs):
    """Undirected adjacency keyed by ('person'|'entity', id). Used for
    six-degrees BFS where we don't care about role/direction."""
    adj = {}
    for row in family_pairs:
        a = ("person", row["person_id"])
        b = ("person", row["related_id"])
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    for row in entity_link_rows:
        p = ("person", row["person_id"])
        e = ("entity", row["entity_id"])
        adj.setdefault(p, []).append(e)
        adj.setdefault(e, []).append(p)
    for row in entity_pairs:
        a = ("entity", row["entity_a_id"])
        b = ("entity", row["entity_b_id"])
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    return adj


def _bfs_distance(adj, start, target):
    """Hop count from start to target, or None if unreachable."""
    if start == target:
        return 0
    if start not in adj:
        return None
    seen = {start}
    queue = deque([(start, 0)])
    while queue:
        node, dist = queue.popleft()
        for nbr in adj.get(node, ()):
            if nbr == target:
                return dist + 1
            if nbr not in seen:
                seen.add(nbr)
                queue.append((nbr, dist + 1))
    return None


def _six_degrees(adj, person_node_ids, sample_size=300, seed=42):
    """Sample random pairs, BFS each, return aggregate hop stats."""
    if len(person_node_ids) < 2:
        return None
    rng = random.Random(seed)
    pool = list(person_node_ids)
    distances = []
    pairs_seen = 0
    target_pairs = min(sample_size, len(pool) * (len(pool) - 1) // 2)
    seen_pairs = set()
    while pairs_seen < target_pairs:
        a, b = rng.sample(pool, 2)
        key = (a, b) if a < b else (b, a)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        pairs_seen += 1
        d = _bfs_distance(adj, ("person", a), ("person", b))
        if d is not None:
            distances.append(d)
    if not distances:
        return {
            "sampled_pairs": pairs_seen, "connected_pairs": 0,
            "avg_hops": None, "median_hops": None, "max_hops": None,
        }
    return {
        "sampled_pairs": pairs_seen,
        "connected_pairs": len(distances),
        "avg_hops": round(statistics.fmean(distances), 2),
        "median_hops": int(statistics.median(distances)),
        "max_hops": max(distances),
    }


def get_metrics(top_n=5):
    """Snapshot of who/what is most connected in the current graph.

    Returns:
      - top_people: highest total degree (distinct family neighbors + entity_links).
      - top_entities: highest bridge degree (distinct persons linked).
      - largest_family: size + member names of the biggest connected component
        when traversing family edges only (entity bridges excluded — clusters
        through "Harvard" or "Goldman Sachs" aren't actual families).
      - top_schools: top schools by tracked-billionaire alumni count.
      - dynasties: top family components ranked by aggregate net worth.
      - six_degrees: avg/median/max hop count across the combined graph.
    """
    main = get_db()
    main.execute("ATTACH DATABASE ? AS net", (str(database.NETWORK_DB_PATH),))

    family_pairs = main.execute(
        "SELECT person_id, related_id FROM net.family_edges"
    ).fetchall()
    entity_link_rows = main.execute(
        "SELECT person_id, entity_id FROM net.entity_links"
    ).fetchall()
    entity_pairs = main.execute(
        "SELECT entity_a_id, entity_b_id FROM net.entity_edges"
    ).fetchall()
    persons = {
        row["person_id"]: row["common_name"]
        for row in main.execute(
            "SELECT person_id, common_name FROM persons"
        ).fetchall()
    }
    wealth = {
        row["person_id"]: row["net_worth_usd"]
        for row in main.execute("""
            SELECT person_id, net_worth_usd FROM snapshots
            WHERE scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
        """).fetchall()
    }

    family_neighbors = {}
    for row in family_pairs:
        family_neighbors.setdefault(row["person_id"], set()).add(row["related_id"])
        family_neighbors.setdefault(row["related_id"], set()).add(row["person_id"])
    entity_count = {}
    for row in entity_link_rows:
        entity_count[row["person_id"]] = entity_count.get(row["person_id"], 0) + 1

    person_ids = set(family_neighbors) | set(entity_count)
    scored = []
    for pid in person_ids:
        fam = len(family_neighbors.get(pid, ()))
        ent = entity_count.get(pid, 0)
        scored.append({
            "person_id": pid,
            "name": persons.get(pid, "?"),
            "family_degree": fam,
            "entity_degree": ent,
            "total_degree": fam + ent,
        })
    scored.sort(key=lambda r: (-r["total_degree"], r["name"]))
    top_people = scored[:top_n]

    top_entities = [
        dict(row) for row in main.execute("""
            SELECT e.entity_id AS id, e.name, e.kind,
                   COUNT(DISTINCT l.person_id) AS person_count
            FROM net.entities e
            JOIN net.entity_links l ON l.entity_id = e.entity_id
            GROUP BY e.entity_id
            ORDER BY person_count DESC, e.name
            LIMIT ?
        """, (top_n,)).fetchall()
    ]

    components = _components(family_neighbors)
    largest = max(components, key=len) if components else []
    largest_family = {
        "size": len(largest),
        "members": sorted(
            [{"person_id": pid, "name": persons.get(pid, "?")} for pid in largest],
            key=lambda r: r["name"],
        ),
    }
    dynasties = _dynasties(components, persons, wealth)
    top_schools = _top_schools(main, persons)

    cache_key = (len(family_pairs), len(entity_link_rows), len(entity_pairs))
    if cache_key in _SIX_DEGREES_CACHE:
        six = _SIX_DEGREES_CACHE[cache_key]
    else:
        adj = _build_connectivity_adj(family_pairs, entity_link_rows, entity_pairs)
        person_pool = {pid for kind, pid in adj if kind == "person"}
        six = _six_degrees(adj, person_pool)
        _SIX_DEGREES_CACHE.clear()
        _SIX_DEGREES_CACHE[cache_key] = six

    main.execute("DETACH DATABASE net")
    main.close()
    return {
        "top_people": top_people,
        "top_entities": top_entities,
        "largest_family": largest_family,
        "top_schools": top_schools,
        "dynasties": dynasties,
        "six_degrees": six,
    }


def compare_persons(a_id, b_id):
    """All direct connections between two people: family ties, shared entities,
    mutual family neighbors, plus the shortest path through the full graph."""
    if a_id == b_id:
        return None
    main = get_db()
    a_row = main.execute(
        "SELECT person_id, common_name FROM persons WHERE person_id = ?", (a_id,)
    ).fetchone()
    b_row = main.execute(
        "SELECT person_id, common_name FROM persons WHERE person_id = ?", (b_id,)
    ).fetchone()
    if not a_row or not b_row:
        main.close()
        return None
    persons = {
        row["person_id"]: row["common_name"]
        for row in main.execute("SELECT person_id, common_name FROM persons").fetchall()
    }
    main.close()

    net = get_network_db()
    family_rows = net.execute(
        "SELECT person_id, related_id, kind FROM family_edges "
        "WHERE (person_id = ? AND related_id = ?) OR (person_id = ? AND related_id = ?)",
        (a_id, b_id, b_id, a_id),
    ).fetchall()
    direct_family = []
    for r in family_rows:
        if r["person_id"] == a_id:
            direct_family.append({"kind": r["kind"], "direction": "a_to_b"})
        else:
            direct_family.append({
                "kind": REVERSE_ROLE.get(r["kind"], r["kind"]),
                "direction": "a_to_b",
            })

    a_links = net.execute(
        "SELECT entity_id, role FROM entity_links WHERE person_id = ?", (a_id,)
    ).fetchall()
    b_links = net.execute(
        "SELECT entity_id, role FROM entity_links WHERE person_id = ?", (b_id,)
    ).fetchall()
    a_by_eid = {}
    for r in a_links:
        a_by_eid.setdefault(r["entity_id"], []).append(r["role"])
    b_by_eid = {}
    for r in b_links:
        b_by_eid.setdefault(r["entity_id"], []).append(r["role"])
    shared_eids = set(a_by_eid) & set(b_by_eid)
    shared_entities = []
    if shared_eids:
        placeholders = ",".join("?" * len(shared_eids))
        ent_rows = net.execute(
            f"SELECT entity_id, name, kind FROM entities WHERE entity_id IN ({placeholders})",
            list(shared_eids),
        ).fetchall()
        for er in ent_rows:
            shared_entities.append({
                "entity_id": er["entity_id"],
                "name": er["name"],
                "entity_kind": er["kind"],
                "roles_a": a_by_eid[er["entity_id"]],
                "roles_b": b_by_eid[er["entity_id"]],
            })
        shared_entities.sort(key=lambda r: r["name"] or "")

    fam_a = net.execute(
        "SELECT person_id, related_id FROM family_edges "
        "WHERE person_id = ? OR related_id = ?", (a_id, a_id),
    ).fetchall()
    fam_b = net.execute(
        "SELECT person_id, related_id FROM family_edges "
        "WHERE person_id = ? OR related_id = ?", (b_id, b_id),
    ).fetchall()
    net.close()
    a_neighbors = {
        (r["related_id"] if r["person_id"] == a_id else r["person_id"])
        for r in fam_a
    } - {a_id, b_id}
    b_neighbors = {
        (r["related_id"] if r["person_id"] == b_id else r["person_id"])
        for r in fam_b
    } - {a_id, b_id}
    mutual_people = sorted(
        [{"person_id": pid, "name": persons.get(pid, "?")} for pid in a_neighbors & b_neighbors],
        key=lambda r: r["name"],
    )

    chain = find_path(a_id, b_id)

    return {
        "a": {"person_id": a_row["person_id"], "name": a_row["common_name"]},
        "b": {"person_id": b_row["person_id"], "name": b_row["common_name"]},
        "direct_family": direct_family,
        "shared_entities": shared_entities,
        "mutual_people": mutual_people,
        "path": chain,
        "path_length": (max(len(chain) - 1, 0) if chain else None),
    }
