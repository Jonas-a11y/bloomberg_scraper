"""Network refresh orchestration: resolve → fetch relations → bridge → label."""
import logging
from datetime import datetime

from app.database import get_db, get_network_db

from .bridges import (
    filter_bridges,
    write_edges,
    write_entities_and_links,
    write_entity_edges,
    write_second_tier,
)
from .holdings import refresh_holdings_bridges
from .resolver import resolve_persons
from .wikidata import (
    fetch_entity_edges,
    fetch_entity_metadata,
    fetch_entity_relations,
    fetch_award_relations,
    fetch_neighbor_edges,
    fetch_person_metadata,
    fetch_position_relations,
    fetch_relations,
    fetch_series_parents,
    fetch_wikipedia_thumbnail,
)

logger = logging.getLogger(__name__)


_state = {
    "running": False,
    "stage": None,
    "done": 0, "total": 0,
    "errors": 0,
    "qids_resolved": 0,
    "edges_added": 0,
    "entities_added": 0,
    "entity_links_added": 0,
    "entity_edges_added": 0,
    "second_tier_added": 0,
    "second_tier_edges_added": 0,
    "holdings_bridges_added": 0,
    "holdings_links_added": 0,
    "images_added": 0,
    "started_at": None,
    "finished_at": None,
    "message": None,
}


def get_state():
    return dict(_state)


def is_running():
    return _state["running"]


def run_refresh():
    if _state["running"]:
        return
    _state.update({
        "running": True,
        "stage": "resolve",
        "done": 0, "total": 0, "errors": 0,
        "qids_resolved": 0, "edges_added": 0,
        "entities_added": 0, "entity_links_added": 0,
        "entity_edges_added": 0,
        "second_tier_added": 0, "second_tier_edges_added": 0,
        "holdings_bridges_added": 0, "holdings_links_added": 0,
        "images_added": 0,
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "message": "Resolving Wikidata QIDs…",
    })
    try:
        resolve_persons(_state)

        net = get_network_db()
        qids = [
            row[0] for row in net.execute(
                "SELECT wikidata_qid FROM persons_index WHERE wikidata_qid IS NOT NULL"
            ).fetchall()
        ]
        net.close()

        _state["stage"] = "relations"
        _state["message"] = "Fetching family relations…"
        edges = fetch_relations(qids, state=_state)
        _state["edges_added"] = write_edges(edges)

        _state["stage"] = "person_metadata"
        _state["message"] = "Fetching person metadata + photos…"
        meta = fetch_person_metadata(qids, state=_state)

        # Wikipedia thumbnail fallback for persons without P18 but with an article.
        for qid, row in meta.items():
            if not row.get("image_filename") and row.get("wikipedia_url"):
                thumb = fetch_wikipedia_thumbnail(row["wikipedia_url"])
                if thumb:
                    row["image_url"] = thumb

        if meta:
            net = get_network_db()
            updates = []
            for qid, row in meta.items():
                # Persisted columns: image_url (commons URL or wp thumbnail),
                # signature_filename, wikidata_metadata (json blob of the rest).
                image_url = row.get("image_url")
                if not image_url and row.get("image_filename"):
                    from urllib.parse import quote
                    image_url = (
                        "https://commons.wikimedia.org/wiki/Special:FilePath/"
                        + quote(row["image_filename"])
                        + "?width=320"
                    )
                blob = {k: v for k, v in row.items() if k not in (
                    "image_filename", "image_url", "signature_filename"
                )}
                import json as _json
                updates.append((
                    image_url,
                    row.get("signature_filename"),
                    _json.dumps(blob, ensure_ascii=False) if blob else None,
                    qid,
                ))
            net.executemany(
                "UPDATE persons_index SET image_url = ?, signature_filename = ?, "
                "wikidata_metadata = ? WHERE wikidata_qid = ?",
                updates,
            )
            net.commit()

            # Overwrite the heuristic-detected gender on persons with Wikidata's
            # authoritative P21 value. Joins via persons_index.wikidata_qid →
            # person_id since persons.gender lives in the main DB.
            qid_to_gender = {qid: r["gender"] for qid, r in meta.items() if r.get("gender")}
            if qid_to_gender:
                qid_to_pid = dict(net.execute(
                    "SELECT wikidata_qid, person_id FROM persons_index WHERE wikidata_qid IS NOT NULL"
                ).fetchall())
                main = get_db()
                main.executemany(
                    "UPDATE persons SET gender = ?, gender_confidence = 1.0 WHERE person_id = ?",
                    [(g, qid_to_pid[qid]) for qid, g in qid_to_gender.items() if qid in qid_to_pid],
                )
                main.commit()
                main.close()
            net.close()
        _state["images_added"] = sum(1 for r in meta.values() if r.get("image_filename") or r.get("image_url"))

        _state["stage"] = "entities"
        _state["message"] = "Fetching shared employers / schools / boards…"
        triples = fetch_entity_relations(qids, state=_state)
        position_triples = fetch_position_relations(qids, state=_state)
        award_triples = fetch_award_relations(qids, state=_state)
        triples.extend(position_triples)
        triples.extend(award_triples)

        # Collapse year-by-year instances into their parent series so e.g. nine
        # separate "WEF Annual Meeting 20XX" nodes become one "WEF Annual
        # Meeting" bridge. Driven entirely by Wikidata's P179 — works for any
        # serialized event/edition, not just WEF.
        candidate_qids = {t[2] for t in triples}
        series_map = fetch_series_parents(candidate_qids, state=_state)
        if series_map:
            triples = [(s, r, series_map.get(o, o)) for s, r, o in triples]

        bridge_triples, bridge_qids = filter_bridges(triples, min_links=2)

        _state["stage"] = "labels"
        _state["message"] = f"Labeling {len(bridge_qids)} bridge entities…"
        metadata = fetch_entity_metadata(bridge_qids, state=_state)

        ents, links = write_entities_and_links(bridge_triples, metadata)
        _state["entities_added"] = ents
        _state["entity_links_added"] = links

        _state["stage"] = "entity_edges"
        _state["message"] = f"Fetching relations between {len(bridge_qids)} entities…"
        ent_edges = fetch_entity_edges(bridge_qids, state=_state)
        _state["entity_edges_added"] = write_entity_edges(ent_edges)

        # Second-tier bridges: entities one hop away that connect 2+ first-tier
        # bridges. They aren't held/employed-at directly, but linking them in
        # surfaces structures like "Berkshire owns BNSF + GEICO + Apple" as a
        # single visible spine. Cap at 100 to keep the graph readable.
        _state["stage"] = "second_tier"
        _state["message"] = "Discovering second-tier bridges…"
        neighbor_edges = fetch_neighbor_edges(bridge_qids, state=_state)
        first_tier = set(bridge_qids)
        touches = {}
        for s_qid, _kind, o_qid in neighbor_edges:
            if s_qid in first_tier and o_qid not in first_tier:
                touches.setdefault(o_qid, set()).add(s_qid)
            elif o_qid in first_tier and s_qid not in first_tier:
                touches.setdefault(s_qid, set()).add(o_qid)
        ranked = sorted(
            ((qid, ts) for qid, ts in touches.items() if len(ts) >= 2),
            key=lambda kv: -len(kv[1]),
        )[:100]
        second_tier_qids = {qid for qid, _ in ranked}
        if second_tier_qids:
            _state["message"] = f"Labeling {len(second_tier_qids)} second-tier entities…"
            second_metadata = fetch_entity_metadata(second_tier_qids, state=_state)
            kept_pool = first_tier | second_tier_qids
            second_edges = [
                (s, k, o) for s, k, o in neighbor_edges
                if s in kept_pool and o in kept_pool
            ]
            ents2, edges2 = write_second_tier(second_metadata, second_edges)
            _state["second_tier_added"] = ents2
            _state["second_tier_edges_added"] = edges2

        _state["stage"] = "holdings"
        _state["message"] = "Linking shared public holdings…"
        refresh_holdings_bridges(state=_state)

        _state["message"] = (
            f"Done — {_state['edges_added']} family edges, "
            f"{ents} bridging entities, {links} entity links, "
            f"{_state['entity_edges_added']} entity↔entity edges, "
            f"{_state['second_tier_added']} second-tier bridges "
            f"({_state['second_tier_edges_added']} edges), "
            f"{_state['holdings_bridges_added']} ticker bridges "
            f"({_state['holdings_links_added']} holdings links) "
            f"across {len(qids)} resolved persons"
        )
    except Exception as e:
        logger.exception("Network refresh failed")
        _state["message"] = f"Failed: {e}"
    finally:
        _state["running"] = False
        _state["stage"] = None
        _state["finished_at"] = datetime.now().isoformat()
