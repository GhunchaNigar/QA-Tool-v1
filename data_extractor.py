"""
data_extractor.py
Replaces the old ScraperAPI + Gemini pipeline (scraper.py + ai_extractor.py)
with the deterministic, no-API-key parsers in extractor.py.

extractor.py's `extract_business(url)` returns a dict shaped like
empty_business() in that file, e.g.:

    {
        "Business Name": "...", "Street": "...", ..., "Country": "...",
        "Phone": "...", "Website URL": "...", "Keywords": "...",
        "Description": "...", "Hours": "...",
        "Social Media Links": {"Facebook": "https://...", ...},
        "GBP Link": "...", "Business Email": "...", "Category": "...",
        "Logo": "https://.../logo.png",   # URL string, or "" if none
        "Photos": ["https://...", ...],   # list of URLs, or [] if none
    }

comparator.py / excel_writer.py instead expect ALL_FIELDS-shaped rows
(fields_config.ALL_FIELDS uses "Name" not "Business Name"), with:
  - "Social Media Links" as a plain string (comparator._match_social just
    regex-searches for network names in it)
  - "Logo" / "Photos" as the literal string "PRESENT" when something was
    found (comparator.compare_row: `if extracted_val == "PRESENT"`),
    since these are presence-only checks, not value comparisons.

This module bridges that gap and exposes an `extract_batch(urls, ...)`
function with the same call shape the old `ai_extractor.extract_batch`
had (list in, list out, optional progress_callback(done, total)), so it
can be dropped into pages/analysis.py with minimal changes.
"""

import concurrent.futures
from extractor import extract_business
from fields_config import SOURCE_FIELDS, detect_source


# ── Field-shape adapter ───────────────────────────────────────────────────────

def _normalize_extracted(raw: dict, url: str = "") -> dict:
    """Map extractor.py's field names/shapes onto the ALL_FIELDS contract
    that comparator.py / excel_writer.py already know how to consume.

    Only fields listed in fields_config.SOURCE_FIELDS for this URL's
    detected source are included in the returned dict at all -- fields
    that aren't tracked for this source are left out entirely (not just
    set to ""), so they don't show up in the debug view or anywhere else
    downstream. If the source isn't recognised, every field is kept.
    """

    out = {}
    out["Name"] = raw.get("Business Name", "") or ""

    for field in (
        "Street", "City", "State", "Zipcode", "Country", "Phone",
        "Website URL", "Keywords", "Description", "Hours",
        "GBP Link", "Business Email", "Category",
    ):
        out[field] = raw.get(field, "") or ""

    # Social Media Links: dict {"Facebook": "url", ...} -> flat string.
    # comparator._match_social() only greps for network names, so any
    # string containing them works.
    social = raw.get("Social Media Links", {}) or {}
    if isinstance(social, dict):
        out["Social Media Links"] = ", ".join(
            f"{network}: {link}" for network, link in social.items()
        )
    else:
        out["Social Media Links"] = str(social or "")

    # Visual fields: comparator.py only checks for the literal "PRESENT"
    # marker, it never compares the actual URL/list contents.
    out["Logo"] = "PRESENT" if raw.get("Logo") else ""
    out["Photos"] = "PRESENT" if raw.get("Photos") else ""

    # ── Drop fields not tracked for this source ──────────────────────────
    source_key = detect_source(url) if url else None
    if source_key:
        allowed = set(SOURCE_FIELDS.get(source_key, []))
        out = {field: value for field, value in out.items() if field in allowed}

    return out


def extract_one(url: str) -> dict:
    """
    Extract + normalize a single URL. Never raises — on any failure it
    returns a row with "_scrape_error" set, matching the contract the old
    ai_extractor.extract_batch used, so comparator.compare_row marks the
    whole row as "SCRAPE ERROR" instead of crashing the batch.
    """
    try:
        raw = extract_business(url)

        # zeemaps map pages can contain multiple markers and parse_zeemaps
        # returns a list in that case — take the first match. (If your
        # zeemaps URLs always include a `group=` filter down to one
        # business, this is effectively always a single-item list.)
        if isinstance(raw, list):
            raw = raw[0] if raw else {}

        result = _normalize_extracted(raw, url)

    except Exception as e:
        result = {"_scrape_error": str(e), "_scrape_debug": repr(e)}

    result["_url"] = url
    return result


def extract_batch(urls: list, batch_size: int = 4, progress_callback=None) -> list:
    """
    Extract every URL, in parallel (bounded by batch_size — extractor.py's
    Playwright fallback launches a real browser subprocess per URL, so keep
    this modest). Returns results in the SAME ORDER as `urls`, with a
    progress_callback(done_count, total_count) fired as each completes —
    same shape the old ai_extractor.extract_batch used.
    """
    results = [None] * len(urls)
    done = 0

    def _worker(index_url):
        index, url = index_url
        return index, extract_one(url)

    with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
        futures = {
            executor.submit(_worker, (i, url)): i
            for i, url in enumerate(urls)
        }
        for future in concurrent.futures.as_completed(futures):
            index, row = future.result()
            results[index] = row
            done += 1
            if progress_callback:
                progress_callback(done, len(urls))

    return results
