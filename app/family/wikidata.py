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


def search_candidates(name, limit=10):
    """Wikidata wbsearchentities search.

    `limit=10` is intentional (default 5 misses billionaires whose
    historical homonyms outrank them — Hugh Grosvenor 7th Duke sits
    at position #6 behind five older dukes / disambig pages). The
    resolver walks all candidates looking for a business-hint match,
    so a wider net = better recall."""
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


def fetch_person_metadata(qids, state=None, chunk=80, timeout=60):
    """Pull a richer per-person bundle from Wikidata for profile display.

    Returns {qid: dict} with keys (any may be missing):
      - image_filename, signature_filename
      - description, wikipedia_url
      - gender ('male'|'female'|'other'), gender_qid
      - birth_date, death_date (ISO date strings)
      - birth_place, death_place (label strings)
      - residence (label)
      - children_count (int)
      - occupations (list of labels)
      - languages (list of labels)

    Uses GROUP_CONCAT for multi-valued props so we get one row per QID."""
    out = {}
    qid_list = list(qids)
    for i in range(0, len(qid_list), chunk):
        batch = qid_list[i:i + chunk]
        values = " ".join(f"wd:{q}" for q in batch)
        # schema:description filtered to English avoids 200+ language rows.
        # Wikipedia URL via schema:about + sitelink to the enwiki Wikipedia.
        query = f"""
        SELECT ?s ?image ?signature ?gender ?dob ?dod
               ?pobLabel ?podLabel ?residenceLabel ?children ?article
               (GROUP_CONCAT(DISTINCT ?occupationLabel; separator="|") AS ?occupations)
               (GROUP_CONCAT(DISTINCT ?languageLabel; separator="|") AS ?languages)
               (SAMPLE(?desc) AS ?description)
        WHERE {{
            VALUES ?s {{ {values} }}
            OPTIONAL {{ ?s wdt:P18 ?image . }}
            OPTIONAL {{ ?s wdt:P109 ?signature . }}
            OPTIONAL {{ ?s wdt:P21 ?gender . }}
            OPTIONAL {{ ?s wdt:P569 ?dob . }}
            OPTIONAL {{ ?s wdt:P570 ?dod . }}
            OPTIONAL {{ ?s wdt:P19 ?pob . ?pob rdfs:label ?pobLabel . FILTER(LANG(?pobLabel) = "en") }}
            OPTIONAL {{ ?s wdt:P20 ?pod . ?pod rdfs:label ?podLabel . FILTER(LANG(?podLabel) = "en") }}
            OPTIONAL {{ ?s wdt:P551 ?residence . ?residence rdfs:label ?residenceLabel . FILTER(LANG(?residenceLabel) = "en") }}
            OPTIONAL {{ ?s wdt:P1971 ?children . }}
            OPTIONAL {{ ?s wdt:P106 ?occupation . ?occupation rdfs:label ?occupationLabel . FILTER(LANG(?occupationLabel) = "en") }}
            OPTIONAL {{ ?s wdt:P1412 ?language . ?language rdfs:label ?languageLabel . FILTER(LANG(?languageLabel) = "en") }}
            OPTIONAL {{
                ?article schema:about ?s ;
                         schema:isPartOf <https://en.wikipedia.org/> .
            }}
            OPTIONAL {{ ?s schema:description ?desc . FILTER(LANG(?desc) = "en") }}
        }}
        GROUP BY ?s ?image ?signature ?gender ?dob ?dod
                 ?pobLabel ?podLabel ?residenceLabel ?children ?article
        """
        for b in sparql(query, state=state, timeout=timeout):
            qid = b["s"]["value"].rsplit("/", 1)[-1]
            row = out.setdefault(qid, {})

            from urllib.parse import unquote

            def _filename(key):
                v = b.get(key, {}).get("value")
                return unquote(v.rsplit("/", 1)[-1]) if v else None

            def _label(key):
                v = b.get(key, {}).get("value")
                return v if v else None

            def _date(key):
                v = b.get(key, {}).get("value")
                # Wikidata dates are ISO with leading "+"; trim time + sign.
                if not v:
                    return None
                v = v.lstrip("+")
                return v.split("T")[0] if "T" in v else v

            row.setdefault("image_filename", _filename("image"))
            row.setdefault("signature_filename", _filename("signature"))
            row.setdefault("birth_date", _date("dob"))
            row.setdefault("death_date", _date("dod"))
            row.setdefault("birth_place", _label("pobLabel"))
            row.setdefault("death_place", _label("podLabel"))
            row.setdefault("residence", _label("residenceLabel"))
            row.setdefault("description", _label("description"))

            article = b.get("article", {}).get("value")
            if article:
                row.setdefault("wikipedia_url", article)

            children = b.get("children", {}).get("value")
            if children is not None:
                try:
                    row["children_count"] = int(children)
                except (TypeError, ValueError):
                    pass

            gender_qid = (b.get("gender", {}).get("value") or "").rsplit("/", 1)[-1]
            if gender_qid:
                row.setdefault("gender_qid", gender_qid)
                # Q6581097 = male, Q6581072 = female; everything else → other
                row.setdefault("gender", {
                    "Q6581097": "male", "Q6581072": "female",
                }.get(gender_qid, "other"))

            occs = (b.get("occupations", {}).get("value") or "").split("|")
            occs = [o for o in occs if o and not o.startswith("Q")]
            if occs:
                row["occupations"] = sorted(set(occs))

            langs = (b.get("languages", {}).get("value") or "").split("|")
            langs = [l for l in langs if l and not l.startswith("Q")]
            if langs:
                row["languages"] = sorted(set(langs))
    return out


def fetch_wikipedia_thumbnail(article_url, timeout=10):
    """Fetch the lead image from Wikipedia's REST page summary endpoint.
    `article_url` is a full enwiki URL. Returns the thumbnail URL string or
    None. Used as a fallback when Wikidata P18 is missing.

    Filters out non-portrait artifacts: SVGs (almost always a logo or coat
    of arms) and URLs that mention "logo" / "coat" / "arms" — those are
    accurate page images but not what we want next to a person's name."""
    try:
        title = article_url.rsplit("/", 1)[-1]
        if not title:
            return None
        r = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            impersonate="chrome",
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        body = r.json()
        for k in ("originalimage", "thumbnail"):
            img = body.get(k) or {}
            src = img.get("source")
            if not src:
                continue
            low = src.lower()
            if low.endswith(".svg") or low.endswith(".svg.png"):
                continue
            if any(bad in low for bad in ("logo", "coat_of_arms", "coat-of-arms", "/arms_")):
                continue
            return src
    except Exception as e:
        logger.debug(f"wikipedia thumbnail {article_url}: {e}")
    return None


def fetch_series_parents(entity_qids, state=None, chunk=120, timeout=45):
    """Return {qid: series_qid} for entities whose Wikidata page declares
    P179 ("part of the series"). Used to collapse year-by-year instances
    (e.g. award editions, conference years) into one canonical bridge so
    the graph isn't fragmented across editions.

    P179 is the only signal: it's explicit and unambiguous. P31 ("instance
    of") was tried as a fallback but couldn't reliably distinguish
    "year edition of an event" from "instance of a generic class" without
    per-parent subclass probing — Stanford collapsed to "private research
    university" and Wharton/HBS to "business school". Some series in
    Wikidata don't set P179 (e.g. the WEF Annual Meeting yearlies as of
    this writing), so those will remain fragmented in the graph.

    Entities without a P179 statement are absent from the result."""
    out = {}
    qid_list = list(entity_qids)
    for i in range(0, len(qid_list), chunk):
        batch = qid_list[i:i + chunk]
        values = " ".join(f"wd:{q}" for q in batch)
        query = f"""
        SELECT ?s ?series WHERE {{
            VALUES ?s {{ {values} }}
            ?s wdt:P179 ?series .
        }}
        """
        for b in sparql(query, state=state, timeout=timeout):
            s = b["s"]["value"].rsplit("/", 1)[-1]
            series = b["series"]["value"].rsplit("/", 1)[-1]
            if series.startswith("Q") and series != s and s not in out:
                out[s] = series
    return out


def fetch_award_relations(qids, state=None, chunk=80, timeout=60):
    """Pull P166 (award received) statements with the P1027 "conferred by"
    qualifier so each row links a person to the conferring institution via
    an award-typed role. Awards without a conferring qualifier (most generic
    "honorary doctorate", "Knight Bachelor" etc.) are dropped — bridging
    billionaires through an award class is not signal.

    Returns list of (person_qid, award_label, conferred_by_qid)."""
    triples = []
    qid_list = list(qids)
    for i in range(0, len(qid_list), chunk):
        batch = qid_list[i:i + chunk]
        values = " ".join(f"wd:{q}" for q in batch)
        query = f"""
        SELECT ?s ?award ?awardLabel ?by WHERE {{
            VALUES ?s {{ {values} }}
            ?s p:P166 ?stmt .
            ?stmt ps:P166 ?award .
            ?stmt pq:P1027 ?by .
            SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
        }}
        """
        for b in sparql(query, state=state, timeout=timeout):
            person_qid = b["s"]["value"].rsplit("/", 1)[-1]
            by = b.get("by", {}).get("value")
            if not by:
                continue
            by_qid = by.rsplit("/", 1)[-1]
            role = b.get("awardLabel", {}).get("value") or "award"
            if by_qid.startswith("Q") and person_qid != by_qid:
                triples.append((person_qid, role, by_qid))
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


def fetch_neighbor_edges(entity_qids, state=None, chunk=80, timeout=60):
    """Return list of (subject_qid, kind, object_qid) for edges where AT LEAST
    ONE endpoint is in `entity_qids`. Used to discover second-tier bridges:
    entities that aren't person-bridges themselves but link two or more
    first-tier bridges (e.g. a holding company with multiple subsidiaries
    in our graph).

    Runs both forward (VALUES ?s) and inverse (VALUES ?o) so an edge gets
    captured even if only the *other* endpoint is a first-tier bridge."""
    seeds = set(entity_qids)
    if not seeds:
        return []
    edges = []
    qid_list = list(seeds)
    props_clause = " ".join(f"wdt:{p}" for p in ENTITY_TO_ENTITY_PROPS)

    def _collect(query):
        for b in sparql(query, state=state, timeout=timeout):
            s = b["s"]["value"].rsplit("/", 1)[-1]
            p = b["p"]["value"].rsplit("/", 1)[-1]
            o = b["o"]["value"].rsplit("/", 1)[-1]
            kind = ENTITY_TO_ENTITY_PROPS.get(p)
            if kind and s.startswith("Q") and o.startswith("Q") and s != o:
                edges.append((s, kind, o))

    for i in range(0, len(qid_list), chunk):
        batch = qid_list[i:i + chunk]
        values = " ".join(f"wd:{q}" for q in batch)
        _collect(f"""
        SELECT ?s ?p ?o WHERE {{
            VALUES ?s {{ {values} }}
            VALUES ?p {{ {props_clause} }}
            ?s ?p ?o .
        }}
        """)
        _collect(f"""
        SELECT ?s ?p ?o WHERE {{
            VALUES ?o {{ {values} }}
            VALUES ?p {{ {props_clause} }}
            ?s ?p ?o .
        }}
        """)
    return edges
