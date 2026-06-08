"""Forbes 1997-2023 historical importer (Kaggle dataset).

Pulls the `guillemservera/forbes-billionaires-1997-2023` dataset from
Kaggle via the official CLI, parses its CSV(s), and writes to the
historical_rankings table. This replaces the Wikipedia scraper as the
primary historical source — the Kaggle dataset is dense (every year has
~1500-2500 rows vs. Wikipedia's sparse top-7s), pre-cleaned, and not
subject to live-page vandalism.

Auth: the kaggle CLI reads ~/.kaggle/kaggle.json with API credentials.
Get the token from https://www.kaggle.com/settings → "Create New Token"
and chmod 600 the resulting file.

The Wikipedia scraper (app.forbes_history) is kept as a fallback for
years past the Kaggle dataset's 2023 freeze.
"""
import csv
import logging
import os
import re
import subprocess
import zipfile
from pathlib import Path

from app.database import get_db

logger = logging.getLogger(__name__)

KAGGLE_DATASET = "guillemservera/forbes-billionaires-1997-2023"
DEFAULT_DOWNLOAD_DIR = Path("data/forbes_kaggle")

# Source label written to historical_rankings.source — keeps Kaggle rows
# distinguishable from Wikipedia-scraped rows so we can re-import either
# without overlap.
SOURCE_LABEL = "forbes_kaggle"


def download_dataset(target_dir=DEFAULT_DOWNLOAD_DIR, force=False):
    """Download + unzip the Kaggle dataset. Returns the directory path.

    No-op if the directory already has CSV files unless force=True.
    Tries the Python `kaggle` API first (in-process, works without
    the `kaggle` shell binary on $PATH — common on Docker / systemd
    deployments). Falls back to the CLI for legacy installs.
    Raises RuntimeError with a clear, actionable message on any
    failure path."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if not force:
        existing = list(target_dir.glob("*.csv"))
        if existing:
            logger.info(f"Found {len(existing)} CSV(s) in {target_dir}, skipping download")
            return target_dir

    logger.info(f"Downloading {KAGGLE_DATASET} to {target_dir}…")

    auth_hint = (
        "Kaggle requires API credentials for this dataset.\n"
        "  1) Sign in at https://www.kaggle.com\n"
        "  2) Settings → Account → Create New Token (downloads kaggle.json)\n"
        "  3) mkdir -p ~/.kaggle && mv ~/Downloads/kaggle.json ~/.kaggle/\n"
        "  4) chmod 600 ~/.kaggle/kaggle.json\n"
        "  Or set KAGGLE_USERNAME and KAGGLE_KEY as env vars."
    )

    # Path 1: Python API. Avoids the subprocess entirely — same
    # interpreter, same dependency tree. Works on a deployment that
    # has the `kaggle` pip package but no shell wrapper on $PATH.
    py_err = None
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
        api = KaggleApi()
        api.authenticate()
        api.dataset_download_files(
            KAGGLE_DATASET, path=str(target_dir), unzip=True, quiet=True,
        )
    except ImportError as e:
        py_err = f"kaggle Python package not importable: {e}"
    except Exception as e:
        msg = str(e).lower()
        if ("credentials" in msg or "401" in msg or "403" in msg
                or "kaggle.json" in msg or "could not find" in msg):
            raise RuntimeError(auth_hint) from e
        py_err = f"kaggle Python API failed: {e}"

    # Path 2: shell CLI (legacy / diagnostic). Only attempted if the
    # Python path didn't already produce CSVs.
    csvs = list(target_dir.glob("*.csv"))
    if not csvs:
        try:
            result = subprocess.run(
                ["kaggle", "datasets", "download", "-d", KAGGLE_DATASET,
                 "-p", str(target_dir), "--unzip"],
                capture_output=True, text=True, check=False,
            )
        except FileNotFoundError as e:
            # Bare `kaggle` binary missing on $PATH — happens on most
            # locked-down server-side deployments. Surface BOTH errors
            # so the user knows the Python path is also broken.
            raise RuntimeError(
                f"Could not download from Kaggle.\n"
                f"  Python API: {py_err or 'not attempted'}\n"
                f"  Shell CLI:  {e}\n"
                f"\nFix: install the kaggle Python package "
                f"(`pip install kaggle`) AND ensure credentials "
                f"are present.\n\n{auth_hint}"
            ) from e
        if result.returncode != 0:
            stderr = result.stderr.strip().lower()
            if ("could not find kaggle.json" in stderr or "credentials" in stderr
                    or "401" in stderr or "403" in stderr):
                raise RuntimeError(auth_hint)
            raise RuntimeError(
                f"kaggle download failed (exit {result.returncode}):\n"
                f"  stderr: {result.stderr.strip()}\n"
                f"  stdout: {result.stdout.strip()}"
            )

    # If the --unzip flag didn't apply (older CLIs), do it manually
    for zf in target_dir.glob("*.zip"):
        with zipfile.ZipFile(zf) as z:
            z.extractall(target_dir)
        zf.unlink()

    csvs = list(target_dir.glob("*.csv"))
    if not csvs:
        raise RuntimeError(f"No CSV files in {target_dir} after download")
    logger.info(f"Got {len(csvs)} CSV(s): {[c.name for c in csvs]}")
    return target_dir


def _parse_net_worth(s):
    """The dataset stores net worth as either a bare float in billions
    ("53.5") or a string ("$53.5 B"). Returns int USD, or None on garbage.

    Numeric 0 is treated as missing — a billionaire with zero net worth
    is almost certainly a CSV typo or a placeholder."""
    if s is None or s == "" or s == 0:
        return None
    if isinstance(s, (int, float)):
        if s <= 0:
            return None
        return int(round(float(s) * 1_000_000_000))
    cleaned = str(s).replace("$", "").replace(",", "").replace("B", "").replace("billion", "").strip()
    try:
        billions = float(cleaned)
    except ValueError:
        return None
    if billions <= 0 or billions > 1000:
        return None
    return int(round(billions * 1_000_000_000))


def _parse_int(s):
    if s is None or s == "":
        return None
    try:
        return int(float(str(s).strip()))
    except (ValueError, TypeError):
        return None


def _row_from_csv(row, year_override=None):
    """Best-effort schema-flexible row parser. The Kaggle dataset's columns
    vary slightly (the publisher has revised the CSV format over versions);
    we look for sensible header variants instead of hardcoding positions."""
    def _get(*names):
        for n in names:
            if n in row and row[n] not in (None, ""):
                return row[n]
        return None

    year = _parse_int(_get("year", "Year"))
    if year_override is not None:
        year = year_override
    rank = _parse_int(_get("rank", "Rank", "position"))
    name = _get("full_name", "name", "Name", "personName", "fullName")
    last_name = _get("last_name", "lastName")
    first_name = _get("first_name", "firstName")
    if not name and (first_name or last_name):
        name = f"{first_name or ''} {last_name or ''}".strip()
    worth = _parse_net_worth(_get("net_worth", "netWorth", "finalWorth",
                                   "worth", "Net Worth", "Net Worth (B)"))
    country = _get("country_of_citizenship", "country", "Country",
                   "countryOfCitizenship", "citizenship")
    age = _parse_int(_get("age", "Age"))
    industry = _get("business_industries", "business_category",
                    "industry", "Industry", "category", "source", "Source")
    # Birth date is gold for matching across name spellings — keep it.
    birth_date = _get("birth_date", "birthDate", "dob")
    birth_year = None
    if birth_date and len(str(birth_date)) >= 4:
        try:
            birth_year = int(str(birth_date)[:4])
        except ValueError:
            pass
    return {
        "year": year, "rank": rank, "name": name,
        "last_name": last_name, "first_name": first_name,
        "net_worth_usd": worth, "citizenship": country,
        "age": age, "birth_year": birth_year, "industry": industry,
    }


def parse_csv(csv_path):
    """Stream-read one CSV file, yield validated row dicts."""
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row = _row_from_csv(raw)
            if not row["year"] or not row["rank"] or not row["name"] or not row["net_worth_usd"]:
                continue
            # Strip trailing notes from name and remember as `notes`
            name = row["name"].strip()
            notes = ""
            for marker in ("& family", "& Family", "& family,", "& Estate"):
                if name.endswith(marker):
                    name = name[: -len(marker)].strip().rstrip(",")
                    notes = "family"
                    break
            row["name"] = name
            row["notes"] = notes or None
            yield row


def _normalize_name(name):
    """Lowercase, strip punctuation/diacritics-ish, collapse whitespace.
    Used for matching only; the original casing is preserved in the row.
    """
    if not name:
        return ""
    s = name.lower().strip()
    # Drop common suffixes/prefixes that vary between sources
    for suffix in (" jr.", " jr", " sr.", " sr", " iii", " ii", " iv",
                   " & family", " family", " & estate"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    s = s.replace(".", "").replace(",", "")
    s = re.sub(r"\s+", " ", s)
    return s


def link_to_persons(rows):
    """Multi-strategy linker. Tries each strategy in order; first hit wins.

    1. **Exact normalized name** — "Bill Gates" matches "bill gates"
    2. **Last-name + birth-year** — "Gates" + 1955 matches across spellings
    3. **Last-name + citizenship** — "Kravis" + "United States" disambiguates
    4. **Substring containment** with length floor — "Bill Gates" contained
       in / containing a known name ≥ 6 chars

    Returns rows with `person_id` filled in where confident."""
    conn = get_db()
    persons = conn.execute(
        "SELECT person_id, common_name, full_name, last_name, citizenship, "
        "       birth_year FROM persons"
    ).fetchall()
    conn.close()

    # Build the lookup indexes once.
    by_norm_name = {}        # normalized full/common name → person_id
    by_last_year = {}        # (lower last_name, birth_year) → person_id
    by_last_country = {}     # (lower last_name, citizenship) → [person_ids]
    by_norm_set = []         # all (normalized_name, person_id) for substring fallback
    for p in persons:
        for n in (p["common_name"], p["full_name"]):
            if n:
                norm = _normalize_name(n)
                if norm and norm not in by_norm_name:
                    by_norm_name[norm] = p["person_id"]
                if norm:
                    by_norm_set.append((norm, p["person_id"]))
        last = (p["last_name"] or "").lower().strip()
        if last and p["birth_year"]:
            by_last_year[(last, p["birth_year"])] = p["person_id"]
        if last and p["citizenship"]:
            by_last_country.setdefault((last, p["citizenship"]), []).append(p["person_id"])

    # Track per-strategy hit counts so we can report what's working.
    stats = {"exact": 0, "last_year": 0, "last_country": 0, "substring": 0}

    for r in rows:
        target = _normalize_name(r["name"])
        if not target:
            continue

        # Strategy 1: exact normalized name
        pid = by_norm_name.get(target)
        if pid:
            r["person_id"] = pid
            stats["exact"] += 1
            continue

        # Strategy 2: last name + birth year
        last = (r.get("last_name") or "").lower().strip()
        if not last and " " in target:
            last = target.rsplit(" ", 1)[-1]
        if last and r.get("birth_year"):
            pid = by_last_year.get((last, r["birth_year"]))
            if pid:
                r["person_id"] = pid
                stats["last_year"] += 1
                continue

        # Strategy 3: last name + citizenship — only commit if exactly one
        # candidate matches (multi-candidate is too risky to auto-link)
        if last and r.get("citizenship"):
            candidates = by_last_country.get((last, r["citizenship"]), [])
            if len(candidates) == 1:
                r["person_id"] = candidates[0]
                stats["last_country"] += 1
                continue

        # Strategy 4: substring containment — both sides must be ≥ 6 chars
        # to avoid "Steve" matching too freely
        if len(target) >= 6:
            best_pid = None
            ambiguous = False
            for norm, pid in by_norm_set:
                if len(norm) < 6:
                    continue
                if norm in target or target in norm:
                    if best_pid and best_pid != pid:
                        ambiguous = True
                        break
                    best_pid = pid
            if best_pid and not ambiguous:
                r["person_id"] = best_pid
                stats["substring"] += 1

    logger.info(
        f"Linker: exact={stats['exact']} last+yr={stats['last_year']} "
        f"last+ctry={stats['last_country']} substring={stats['substring']}"
    )
    return rows


def import_csvs(directory=DEFAULT_DOWNLOAD_DIR, source=SOURCE_LABEL):
    """Walk every CSV in the directory, parse, link, and bulk-INSERT to
    historical_rankings. Returns a per-year report.

    The Kaggle dataset ships an all-in-one `all_billionaires_*.csv` plus
    per-year shards with the same content. We prefer the all-in-one when
    present to avoid double-counting; if it's missing we fall back to the
    per-year files."""
    directory = Path(directory)
    all_in_one = sorted(directory.glob("all_billionaires*.csv"))
    if all_in_one:
        csvs = [all_in_one[-1]]  # newest if more than one
        logger.info(f"Using all-in-one CSV: {csvs[0].name}")
    else:
        csvs = sorted(directory.glob("billionaires_*.csv"))
        logger.info(f"Using {len(csvs)} per-year CSV(s)")
    if not csvs:
        raise RuntimeError(f"No Forbes CSV files in {directory} — call download_dataset first")

    all_rows = []
    for csv_path in csvs:
        logger.info(f"Parsing {csv_path.name}…")
        all_rows.extend(parse_csv(csv_path))
    logger.info(f"Parsed {len(all_rows)} total rows across {len(csvs)} CSV(s)")

    # The Kaggle dataset records monthly snapshots in some years. Multiple
    # rows can share (year, rank) — keep the highest net worth as the
    # canonical entry. (Order is roughly chronological in the source CSV;
    # later rows tend to be later months, with refined estimates.)
    by_yr_rank = {}
    for r in all_rows:
        key = (r["year"], r["rank"])
        prev = by_yr_rank.get(key)
        if prev is None or (r["net_worth_usd"] or 0) > (prev["net_worth_usd"] or 0):
            by_yr_rank[key] = r
    deduped = list(by_yr_rank.values())
    if len(deduped) < len(all_rows):
        logger.info(
            f"Deduped {len(all_rows)} → {len(deduped)} rows (kept max net worth per year+rank)"
        )

    deduped = link_to_persons(deduped)
    linked = sum(1 for r in deduped if r.get("person_id"))

    conn = get_db()
    conn.executemany(
        """
        INSERT OR REPLACE INTO historical_rankings
            (source, year, rank, person_id, name, net_worth_usd,
             citizenship, age, industry, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (source, r["year"], r["rank"], r.get("person_id"), r["name"],
             r["net_worth_usd"], r.get("citizenship"), r.get("age"),
             r.get("industry"), r.get("notes"))
            for r in deduped
        ],
    )
    conn.commit()

    by_year = {}
    for r in conn.execute(
        "SELECT year, COUNT(*) AS n, SUM(person_id IS NOT NULL) AS linked "
        "FROM historical_rankings WHERE source = ? GROUP BY year ORDER BY year",
        (source,),
    ).fetchall():
        by_year[r["year"]] = {"rows": r["n"], "linked": r["linked"]}
    conn.close()

    return {
        "total_imported": len(deduped),
        "total_linked": linked,
        "by_year": by_year,
    }


def run(force_download=False):
    """One-shot: download (if not cached) + import. The function the
    /api/scraper/forbes-kaggle endpoint calls in a background thread."""
    download_dataset(force=force_download)
    return import_csvs()
