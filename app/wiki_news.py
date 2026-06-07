"""Wikipedia references → news articles.

Every billionaire's Wikipedia article cites the news stories that reported
the events in their bio — IPOs, acquisitions, deaths, lawsuits. These
citations have dates and URLs. Pulling them gives us a curated, dated news
timeline without rate-limit issues.

We fetch the article wikitext via the Wikipedia Action API and extract
`{{cite news|...}}` / `{{cite web|...}}` templates, then convert them into
the same shape as `news_articles` rows.
"""
import html
import logging
import re
import time
from datetime import datetime
from urllib.parse import unquote, urlparse

from curl_cffi import requests

from app.news import score_importance

logger = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "BloombergScraper/1.0 (educational; github.com/jonas-giessler/bloomberg_scraper)"

CITE_TEMPLATES = ("cite news", "cite web", "cite magazine", "cite press release")

# Wikipedia 429s when we hit them too fast. Their published guidance is "no
# fixed limit but be reasonable" — empirically, ~1 req/sec works, bursts of
# 200 in a few seconds get blocked. Backfill caller spaces requests; this
# retry handles transient blocks.
WIKI_RETRY_DELAYS_SEC = [10, 30, 60]


def _title_from_url(wikipedia_url):
    if not wikipedia_url:
        return None
    parsed = urlparse(wikipedia_url)
    if not parsed.path or "/wiki/" not in parsed.path:
        return None
    return unquote(parsed.path.split("/wiki/", 1)[1])


def _parse_cite_params(body):
    """Crude pipe-split parser for `key=value|key=value` template bodies.

    Real wikitext can have nested templates inside parameter values; for
    citations these are rare and we just treat the inner brace pairs as
    opaque text. Good enough — we only need url, title, date, publisher,
    work, and we lose little by skipping malformed citations."""
    out = {}
    # Strip nested {{...}} templates so they don't confuse the split
    body = re.sub(r"\{\{[^{}]*\}\}", "", body)
    for part in body.split("|"):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        out[k.strip().lower()] = v.strip()
    return out


def _normalize_date(raw):
    """Turn varied citation date strings into (YYYY-MM-DD, precision).

    precision is 'day' / 'month' / 'year'. Wikipedia cites use formats like
    "12 March 2018", "March 12, 2018", "2018-03-12", "March 2018", "2018".
    Day-precision is what the chart wants; coarser dates fall back to a
    placeholder day (YYYY-MM-15 or YYYY-06-15) and the chart filters them
    out so the marker doesn't sit on a wealth value that was never real for
    that date.

    Returns (None, None) when nothing parses."""
    if not raw:
        return None, None
    raw = raw.strip()
    # Try ISO first
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "day"
    # Try "12 March 2018" or "March 12, 2018"
    for fmt in ("%d %B %Y", "%B %d, %Y", "%B %d %Y", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d"), "day"
        except ValueError:
            continue
    # "March 2018" → mid-month default
    for fmt in ("%B %Y", "%b %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-15"), "month"
        except ValueError:
            continue
    # Bare year
    m = re.match(r"^(\d{4})$", raw)
    if m:
        return f"{m.group(1)}-06-15", "year"
    return None, None


def _domain_of(url):
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
        return host.removeprefix("www.")
    except Exception:
        return None


def fetch_wikipedia_news(wikipedia_url, limit=100, timeout=20):
    """Pull citation-derived news articles from a Wikipedia page.

    Returns the same shape as `app.news.fetch_news_for_person`:
    list of {article_date, title, url, source, importance}.
    """
    title = _title_from_url(wikipedia_url)
    if not title:
        return []

    # Retry on 429 / "too many requests" with growing backoff. Wikipedia's
    # rate limiter clears in ~30-60s for unauthenticated traffic.
    for attempt, delay in enumerate([0] + WIKI_RETRY_DELAYS_SEC):
        if delay:
            logger.info(f"Wikipedia throttled, sleeping {delay}s for {title}")
            time.sleep(delay)
        try:
            r = requests.get(
                WIKI_API,
                params={
                    "action": "parse",
                    "page": title,
                    "format": "json",
                    "prop": "wikitext",
                    "redirects": 1,
                    "formatversion": 2,
                },
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=timeout,
                impersonate="chrome",
            )
        except Exception as e:
            logger.warning(f"Wikipedia fetch failed for {title}: {e}")
            return []
        # The 429 sometimes comes back as 200 with a plaintext "too many" body
        # — sniff for that too.
        body_preview = r.text[:200].lower() if hasattr(r, "text") else ""
        throttled = (
            r.status_code == 429
            or "too many requests" in body_preview
            or "<html" in body_preview  # CDN error page
        )
        if not throttled:
            break
    else:
        logger.warning(f"Wikipedia 429 for {title}")
        return []

    if r.status_code != 200:
        logger.warning(f"Wikipedia {r.status_code} for {title}")
        return []
    try:
        data = r.json()
    except Exception:
        return []
    if "error" in data:
        return []

    wikitext = data.get("parse", {}).get("wikitext", "")
    if not wikitext:
        return []

    # Extract cite templates. Citations don't typically nest other templates
    # at the top level, so a non-greedy match between {{ and }} is fine.
    out = []
    seen_urls = {}
    pattern = re.compile(
        r"\{\{(?:" + "|".join(CITE_TEMPLATES) + r")\b([^}]*?)\}\}",
        re.IGNORECASE | re.DOTALL,
    )
    # First pass: count how many times each URL is cited in the article.
    # Reuse via a named <ref name="..."/> tag is a strong signal of importance
    # — the editor came back to the same source for multiple claims.
    cite_counts = {}
    for m in pattern.finditer(wikitext):
        params = _parse_cite_params(m.group(1))
        url = params.get("url")
        if url:
            cite_counts[url] = cite_counts.get(url, 0) + 1
    # Also count <ref name="x"/> reuses — these are bare references back to
    # an earlier full citation, which the parser would otherwise miss.
    ref_name_pattern = re.compile(r"<ref\s+name\s*=\s*[\"']?([^\"'/>]+)[\"']?\s*/>", re.IGNORECASE)
    name_reuse_counts = {}
    for m in ref_name_pattern.finditer(wikitext):
        name = m.group(1).strip()
        name_reuse_counts[name] = name_reuse_counts.get(name, 0) + 1
    # Map ref names to URLs by scanning <ref name="X">{{cite ...|url=...}}</ref>
    ref_with_name_pattern = re.compile(
        r"<ref\s+name\s*=\s*[\"']?([^\"'>]+)[\"']?\s*>\s*\{\{(?:" + "|".join(CITE_TEMPLATES) + r")\b([^}]*?)\}\}",
        re.IGNORECASE | re.DOTALL,
    )
    for m in ref_with_name_pattern.finditer(wikitext):
        name = m.group(1).strip()
        params = _parse_cite_params(m.group(2))
        url = params.get("url")
        if url and name in name_reuse_counts:
            cite_counts[url] = cite_counts.get(url, 0) + name_reuse_counts[name]

    for m in pattern.finditer(wikitext):
        body = m.group(1)
        params = _parse_cite_params(body)
        url = params.get("url")
        title_str = params.get("title")
        if not url or not title_str or url in seen_urls:
            continue
        # Strip wiki link syntax that sometimes leaks into title
        title_str = re.sub(r"\[\[([^|\]]+\|)?([^\]]+)\]\]", r"\2", title_str).strip()
        # Decode HTML entities (&nbsp;, &amp;, etc.) so the UI shows real text
        title_str = html.unescape(title_str)
        # Collapse runs of whitespace from the entity unescape
        title_str = re.sub(r"\s+", " ", title_str)
        date, precision = _normalize_date(
            params.get("date") or params.get("publishdate") or params.get("year")
        )
        if not date:
            continue
        publisher = params.get("publisher") or params.get("work") or params.get("website")
        source = _domain_of(url) or (publisher.strip() if publisher else None)
        seen_urls[url] = True
        # Score: baseline (Wikipedia inclusion) + keyword score + citation
        # density (capped at +6 so reused-but-routine sources don't dwarf
        # singular but high-keyword articles).
        density_bonus = min(6, max(0, cite_counts.get(url, 1) - 1) * 2)
        out.append({
            "article_date": date,
            "date_precision": precision,
            "title": title_str,
            "url": url,
            "source": source,
            "importance": 4 + score_importance(title_str, url) + density_bonus,
        })
        if len(out) >= limit:
            break
    return out
