"""Wikidata HTTP and SPARQL clients + batched fetches.

State updates (error counter) live on the refresh state object passed in.
"""
import logging

from curl_cffi import requests

from .constants import (
    ENTITY_KIND_BY_INSTANCE,
    ENTITY_PROPS_FORWARD,
    ENTITY_PROPS_INVERSE,
    ENTITY_TO_ENTITY_PROPS,
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


def fetch_position_relations(qids, state=None, chunk=80, timeout=60):
    """Pull P39 (position_held) statements with a qualifier that names the
    actual company/org so each row links a person to a real entity via a
    position-typed role. Without a qualifier the position alone (e.g. "board
    of directors member") has no target — those statements are dropped.

    Qualifiers consulted, in order of frequency on this dataset:
      - P2389: organization directed from this office (executive/board)
      - P642:  "of" (more general but rarely populated for this use)

    Returns list of (person_qid, position_label, company_qid)."""
    triples = []
    qid_list = list(qids)
    for i in range(0, len(qid_list), chunk):
        batch = qid_list[i:i + chunk]
        values = " ".join(f"wd:{q}" for q in batch)
        query = f"""
        SELECT ?s ?position ?positionLabel ?of WHERE {{
            VALUES ?s {{ {values} }}
            ?s p:P39 ?stmt .
            ?stmt ps:P39 ?position .
            ?stmt (pq:P2389|pq:P642) ?of .
            SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
        }}
        """
        for b in sparql(query, state=state, timeout=timeout):
            person_qid = b["s"]["value"].rsplit("/", 1)[-1]
            of = b.get("of", {}).get("value")
            if not of:
                continue
            company_qid = of.rsplit("/", 1)[-1]
            role = b.get("positionLabel", {}).get("value") or "position_held"
            if company_qid.startswith("Q") and person_qid != company_qid:
                triples.append((person_qid, role, company_qid))
    return triples


def fetch_entity_metadata(entity_qids, state=None, chunk=60, timeout=60):
    """Return {qid: dict} where dict has name, kind, description, inception_year,
    country, industry, website, employee_count, revenue_usd, wikipedia_url.

    `kind` is bucketed via ENTITY_KIND_BY_INSTANCE; missing fields are None."""
    out = {}
    qid_list = list(entity_qids)
    for i in range(0, len(qid_list), chunk):
        batch = qid_list[i:i + chunk]
        values = " ".join(f"wd:{q}" for q in batch)
        # One row per (entity, P31 type) — other fields are constant per entity
        # but get duplicated across rows; we deduplicate below.
        query = f"""
        SELECT ?e ?eLabel ?type ?description ?inception
               ?countryLabel ?industryLabel ?website ?employees ?revenue ?wikipedia
        WHERE {{
            VALUES ?e {{ {values} }}
            OPTIONAL {{ ?e wdt:P31 ?type . }}
            OPTIONAL {{ ?e schema:description ?description . FILTER(LANG(?description) = "en") }}
            OPTIONAL {{ ?e wdt:P571 ?inception . }}
            OPTIONAL {{ ?e wdt:P17 ?country . }}
            OPTIONAL {{ ?e wdt:P452 ?industry . }}
            OPTIONAL {{ ?e wdt:P856 ?website . }}
            OPTIONAL {{ ?e wdt:P1128 ?employees . }}
            OPTIONAL {{ ?e wdt:P2139 ?revenue . }}
            OPTIONAL {{
                ?wikipedia schema:about ?e ;
                           schema:isPartOf <https://en.wikipedia.org/> .
            }}
            SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
        }}
        """
        types_by_qid = {}
        fields = {}  # qid -> dict of single-valued fields (first non-null wins)
        for b in sparql(query, state=state, timeout=timeout):
            qid = b["e"]["value"].rsplit("/", 1)[-1]
            f = fields.setdefault(qid, {})
            label = b.get("eLabel", {}).get("value")
            if label and not f.get("name"):
                f["name"] = label
            t = b.get("type", {}).get("value", "").rsplit("/", 1)[-1]
            if t:
                types_by_qid.setdefault(qid, []).append(t)
            for src, dst in (
                ("description", "description"),
                ("countryLabel", "country"),
                ("industryLabel", "industry"),
                ("website", "website"),
                ("wikipedia", "wikipedia_url"),
            ):
                v = b.get(src, {}).get("value")
                if v and not f.get(dst):
                    f[dst] = v
            inc = b.get("inception", {}).get("value")
            if inc and not f.get("inception_year"):
                # ISO datetime "1998-09-04T00:00:00Z" — pull year prefix.
                year_str = inc.lstrip("-")[:4]
                if year_str.isdigit():
                    f["inception_year"] = int(year_str)
            for src, dst in (("employees", "employee_count"), ("revenue", "revenue_usd")):
                v = b.get(src, {}).get("value")
                if v and not f.get(dst):
                    try:
                        f[dst] = int(float(v))
                    except (TypeError, ValueError):
                        pass

        for qid in batch:
            f = fields.get(qid, {})
            kinds = [
                ENTITY_KIND_BY_INSTANCE[t]
                for t in types_by_qid.get(qid, [])
                if t in ENTITY_KIND_BY_INSTANCE
            ]
            out[qid] = {
                "name": f.get("name", qid),
                "kind": kinds[0] if kinds else "other",
                "description": f.get("description"),
                "inception_year": f.get("inception_year"),
                "country": f.get("country"),
                "industry": f.get("industry"),
                "website": f.get("website"),
                "employee_count": f.get("employee_count"),
                "revenue_usd": f.get("revenue_usd"),
                "wikipedia_url": f.get("wikipedia_url"),
            }
    return out


def fetch_entity_edges(entity_qids, state=None, chunk=80, timeout=60):
    """Return list of (subject_qid, kind, object_qid) for edges where BOTH
    endpoints are in `entity_qids`. Uses ENTITY_TO_ENTITY_PROPS."""
    kept = set(entity_qids)
    if not kept:
        return []
    edges = []
    qid_list = list(kept)
    props_clause = " ".join(f"wdt:{p}" for p in ENTITY_TO_ENTITY_PROPS)
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
        for b in sparql(query, state=state, timeout=timeout):
            s = b["s"]["value"].rsplit("/", 1)[-1]
            p = b["p"]["value"].rsplit("/", 1)[-1]
            o = b["o"]["value"].rsplit("/", 1)[-1]
            kind = ENTITY_TO_ENTITY_PROPS.get(p)
            if kind and o in kept and s != o:
                edges.append((s, kind, o))
    return edges
