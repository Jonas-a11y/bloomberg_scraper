"""Network refresh orchestration: resolve → fetch relations → bridge → label."""
import logging
from datetime import datetime

from app.database import get_network_db

from .bridges import filter_bridges, write_edges, write_entities_and_links
from .resolver import resolve_persons
from .wikidata import fetch_entity_metadata, fetch_entity_relations, fetch_relations

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

        _state["stage"] = "entities"
        _state["message"] = "Fetching shared employers / schools / boards…"
        triples = fetch_entity_relations(qids, state=_state)
        bridge_triples, bridge_qids = filter_bridges(triples, min_links=2)

        _state["stage"] = "labels"
        _state["message"] = f"Labeling {len(bridge_qids)} bridge entities…"
        metadata = fetch_entity_metadata(bridge_qids, state=_state)

        ents, links = write_entities_and_links(bridge_triples, metadata)
        _state["entities_added"] = ents
        _state["entity_links_added"] = links
        _state["message"] = (
            f"Done — {_state['edges_added']} family edges, "
            f"{ents} bridging entities, {links} entity links "
            f"across {len(qids)} resolved persons"
        )
    except Exception as e:
        logger.exception("Network refresh failed")
        _state["message"] = f"Failed: {e}"
    finally:
        _state["running"] = False
        _state["stage"] = None
        _state["finished_at"] = datetime.now().isoformat()
