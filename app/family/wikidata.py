"""Wikidata HTTP and SPARQL clients + batched fetches.

State updates (error counter) live on the refresh state object passed in.
"""
import logging

from curl_cffi import requests

from .constants import (
    ENTITY_KIND_BY_INSTANCE,
    ENTITY_PROPS_FORWARD,
    ENTITY_PROPS_INVERSE,
    RELATION_PROPS,
    USER_AGENT,
    WIKIDATA_API,
    WIKIDATA_SPARQL,
)

logger = logging.getLogger(__name__)


def _wikidata_get(params, timeout=15):
    return requests.get(
        WIKIDATA_API,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        impersonate="chrome",
        timeout=timeout,
    )


def search_candidates(name, limit=5):
    try:
        r = _wikidata_get({
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "type": "item",
            "limit": limit,
            "format": "json",
        })
        if r.status_code != 200:
            return []
        return r.json().get("search", [])
    except Exception as e:
        logger.debug(f"wbsearchentities {name!r}: {e}")
        return []


def sparql(query, state=None, timeout=45):
    """Run a SPARQL query, return list of bindings or [] on failure.
    `state` (optional): refresh state dict whose "errors" counter is bumped on failure."""
    try:
        r = requests.get(
            WIKIDATA_SPARQL,
            params={"query": query, "format": "json"},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/sparql-results+json",
            },
            impersonate="chrome",
            timeout=timeout,
        )
        if r.status_code != 200:
            logger.warning(f"SPARQL: status {r.status_code}")
            if state is not None:
                state["errors"] += 1
            return []
        return r.json().get("results", {}).get("bindings", [])
    except Exception as e:
        logger.warning(f"SPARQL failed: {e}")
        if state is not None:
            state["errors"] += 1
        return []


def fetch_relations(qids, state=None, chunk=80, timeout=45):
    """Single batch SPARQL — return list of (subject_qid, kind, object_qid)."""
    edges = []
    qid_list = list(qids)
    props_clause = " ".join(f"wdt:{p}" for p in RELATION_PROPS)
    for i in range(0, len(qid_list), chunk):
        batch = qid_list[i:i + chunk]
        values = " ".join(f"wd:{q}" for q in batch)
        query = f"""
        SELECT ?s ?p ?o WHERE {{
            VALUES ?s {{ {values} }}
            VALUES ?p {{ {props_clause} }}
            ?s ?p ?o .
        }}
        """
        for binding in sparql(query, state=state, timeout=timeout):
            s = binding["s"]["value"].rsplit("/", 1)[-1]
            p = binding["p"]["value"].rsplit("/", 1)[-1]
            o = binding["o"]["value"].rsplit("/", 1)[-1]
            kind = RELATION_PROPS.get(p)
            if kind:
                edges.append((s, kind, o))
    return edges


def fetch_entity_relations(qids, state=None, chunk=80, timeout=60):
    """Pull person->entity links (forward + inverse). Returns list of
    (person_qid, role, entity_qid) regardless of which way the wikidata
    edge points."""
    triples = []
    qid_list = list(qids)
    fwd_clause = " ".join(f"wdt:{p}" for p in ENTITY_PROPS_FORWARD)
    inv_clause = " ".join(f"wdt:{p}" for p in ENTITY_PROPS_INVERSE)

    for i in range(0, len(qid_list), chunk):
        batch = qid_list[i:i + chunk]
        values = " ".join(f"wd:{q}" for q in batch)

        forward = f"""
        SELECT ?s ?p ?o WHERE {{
            VALUES ?s {{ {values} }}
            VALUES ?p {{ {fwd_clause} }}
            ?s ?p ?o .
        }}
        """
        for b in sparql(forward, state=state, timeout=timeout):
            s = b["s"]["value"].rsplit("/", 1)[-1]
            p = b["p"]["value"].rsplit("/", 1)[-1]
            o = b["o"]["value"].rsplit("/", 1)[-1]
            role = ENTITY_PROPS_FORWARD.get(p)
            if role and o.startswith("Q"):
                triples.append((s, role, o))

        inverse = f"""
        SELECT ?s ?p ?o WHERE {{
            VALUES ?o {{ {values} }}
            VALUES ?p {{ {inv_clause} }}
            ?s ?p ?o .
        }}
        """
        for b in sparql(inverse, state=state, timeout=timeout):
            s = b["s"]["value"].rsplit("/", 1)[-1]
            p = b["p"]["value"].rsplit("/", 1)[-1]
            o = b["o"]["value"].rsplit("/", 1)[-1]
            role = ENTITY_PROPS_INVERSE.get(p)
            if role and s.startswith("Q"):
                # Person is on the object side; entity is the subject.
                triples.append((o, role, s))
    return triples


def fetch_entity_metadata(entity_qids, state=None, chunk=120, timeout=60):
    """Return {qid: (name, kind)} where kind is bucketed via ENTITY_KIND_BY_INSTANCE."""
    out = {}
    qid_list = list(entity_qids)
    for i in range(0, len(qid_list), chunk):
        batch = qid_list[i:i + chunk]
        values = " ".join(f"wd:{q}" for q in batch)
        query = f"""
        SELECT ?e ?eLabel ?type WHERE {{
            VALUES ?e {{ {values} }}
            OPTIONAL {{ ?e wdt:P31 ?type . }}
            SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
        }}
        """
        types_by_qid = {}
        names = {}
        for b in sparql(query, state=state, timeout=timeout):
            qid = b["e"]["value"].rsplit("/", 1)[-1]
            label = b.get("eLabel", {}).get("value")
            if label:
                names[qid] = label
            t = b.get("type", {}).get("value", "").rsplit("/", 1)[-1]
            if t:
                types_by_qid.setdefault(qid, []).append(t)
        for qid in batch:
            kinds = [
                ENTITY_KIND_BY_INSTANCE[t]
                for t in types_by_qid.get(qid, [])
                if t in ENTITY_KIND_BY_INSTANCE
            ]
            kind = kinds[0] if kinds else "other"
            out[qid] = (names.get(qid, qid), kind)
    return out
