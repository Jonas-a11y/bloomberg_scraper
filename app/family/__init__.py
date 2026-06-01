"""Wikidata-based network graph resolver.

For each person in our DB without a wikidata_qid, query wbsearchentities to
find a candidate QID, then run batched SPARQL queries for:

- Family relations (P22/P25/P26/P40/P3373/P1038) — direct person-to-person edges.
- Bridging entities — companies, schools, boards (P108/P69/P463/P3320/P800
  forward; P112/P488 inverse). We keep only entities that link >=2 of our
  billionaires, so every entity in the graph is by definition a connector.

Edges are written only when both endpoints map to a person we know about
(family) or to a bridging entity (entity_links), so false-positive QIDs from
the disambiguation step naturally drop out.
"""
from .bridges import (
    filter_bridges, write_edges, write_entities_and_links, write_entity_edges,
    write_second_tier,
)
from .constants import (
    BUSINESS_HINTS,
    ENTITY_KIND_BY_INSTANCE,
    ENTITY_PROPS_FORWARD,
    ENTITY_PROPS_INVERSE,
    ENTITY_TO_ENTITY_PROPS,
    RELATION_PROPS,
    REVERSE_ROLE,
)
from .holdings import refresh_holdings_bridges
from .queries import compare_persons, find_path, get_entity_detail, get_graph, get_metrics, get_person_profile
from .refresh import get_state, is_running, run_refresh
from .resolver import looks_like_billionaire, resolve_persons, resolve_qid, sync_persons_index
from .wikidata import (
    fetch_entity_edges,
    fetch_entity_metadata,
    fetch_entity_relations,
    fetch_award_relations,
    fetch_neighbor_edges,
    fetch_position_relations,
    fetch_relations,
    fetch_series_parents,
    search_candidates,
)

__all__ = [
    # constants
    "RELATION_PROPS", "ENTITY_PROPS_FORWARD", "ENTITY_PROPS_INVERSE",
    "ENTITY_TO_ENTITY_PROPS",
    "REVERSE_ROLE", "ENTITY_KIND_BY_INSTANCE", "BUSINESS_HINTS",
    # wikidata
    "search_candidates", "fetch_relations", "fetch_entity_relations",
    "fetch_entity_metadata", "fetch_entity_edges", "fetch_neighbor_edges",
    "fetch_position_relations",
    "fetch_award_relations", "fetch_series_parents",
    # resolver
    "looks_like_billionaire", "resolve_qid", "resolve_persons",
    "sync_persons_index",
    # bridges
    "filter_bridges", "write_edges", "write_entities_and_links",
    "write_entity_edges", "write_second_tier",
    # holdings
    "refresh_holdings_bridges",
    # refresh
    "get_state", "is_running", "run_refresh",
    # queries
    "get_graph", "find_path", "get_entity_detail", "get_metrics",
    "get_person_profile",
    "compare_persons",
]
