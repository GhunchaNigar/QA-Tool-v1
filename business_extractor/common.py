"""
Shared imports, constants, and helper functions used across every
site parser in business_extractor.parsers.

This module used to be the top of the monolithic extractor.py.
Every parser file does `from ..common import *` to get all of this
(regular expressions/BeautifulSoup imports, clean(), empty_business(),
the bot-wall/cloudflare detectors, the fetchers, and the
fields_config-driven filter_business_fields()).
"""

__all__ = [
    'json',
    're',
    'sys',
    'time',
    'html',
    'html_lib',
    'random',
    'subprocess',
    'requests',
    'urllib3',
    'BeautifulSoup',
    'NavigableString',
    'Comment',
    'urljoin',
    'urlparse',
    'parse_qs',
    'fields_config',
    'HEADERS',
    'IGNORE_CERT_ERRORS_DOMAINS',
    '_FINDUSHERE_EXCLUDED_LINK_DOMAINS',
    '_domain_needs_cert_bypass',
    'SLOW_FETCH_TIMEOUTS_MS',
    '_timeout_ms_for_domain',
    'SOCIAL_DOMAINS',
    '_hostname_matches_social_domain',
    'BLOCK_SIGNALS',
    'clean',
    'clean_multiline',
    'is_meaningful',
    'empty_business',
    '_looks_blocked',
    'CLOUDFLARE_ERROR_SIGNALS',
    '_looks_like_cloudflare_error',
    '_is_maps_link',
    '_split_blinx_address',
    '_split_city_state_zip_address',
    '_split_address_allow_no_comma',
    '_find_cf_email',
    '_decode_cf_email',
    '_value_by_label',
    '_RATE_LIMIT_BACKOFFS',
    'fetch_via_requests',
    'fetch_via_playwright',
    '_BUSINESS_TO_CONFIG_FIELD',
    '_FIELD_EMPTY_DEFAULTS',
    '_empty_value_for',
    'filter_business_fields',
    '_band_description_sections',
    '_split_listings_gbd_address',
]

import json
import re
import sys
import time
import html
import html as html_lib  # alias: every parse_*(url, html) shadows the `html` module name
import random
import subprocess
import requests
import urllib3
from bs4 import BeautifulSoup, NavigableString, Comment
from urllib.parse import urljoin, urlparse, parse_qs

import fields_config


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0 Safari/537.36"
    )
}

IGNORE_CERT_ERRORS_DOMAINS = {
    "bestdealfinder.com",
}

_FINDUSHERE_EXCLUDED_LINK_DOMAINS = (
    "find-us-here.com", "facebook.com", "twitter.com", "x.com",
    "whatsapp.com", "wa.me", "telegram.me", "t.me", "google.com",
    "ezoic.net",
)

# Domains known to be slow to fully load in Playwright (heavy JS,
# slow origin servers, etc.) and that routinely blow past the default
# 45s render budget -- e.g. supplyautonomy.com's business profile
# pages were timing out at the default and surfacing a raw
# "Command [...] timed out after 75.0 seconds" subprocess error
# instead of a clean fetch. Give these domains a longer budget instead
# of raising the default for every site.
SLOW_FETCH_TIMEOUTS_MS = {
    "supplyautonomy.com": 90000,
    "zumvu.com": 90000,
    "cake.me": 90000,
    "gravitysplash.com": 90000,
}


def _split_address_allow_no_comma(address):
    """Like _split_blinx_address, but first checks for the no-street,
    no-comma "City State Zip" shape before falling back to the
    comma-based splitter, which mishandles that shape (see
    _CITY_STATE_ZIP_NO_COMMA_RE above)."""
    if "," not in address:
        match = _CITY_STATE_ZIP_NO_COMMA_RE.match(address)
        if match:
            return "", match.group("city").strip(), match.group("state").strip(), match.group("zip")
    return _split_blinx_address(address)


def _split_city_state_zip_address(address):
    address = address.strip()

    # Shape (a): comma-free "City State Zip".
    if "," not in address:
        match = _CITY_STATE_ZIP_NO_COMMA_RE.match(address)
        if match:
            return "", match.group("city").strip(), match.group("state").strip(), match.group("zip")
        return _split_blinx_address(address)

    # Shape (b): two comma-separated parts, "City State, Zip".
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if len(parts) == 2 and re.match(r"^\d{5}(?:-\d{4})?$", parts[1]):
        match = re.match(r"^(?P<city>[A-Za-z][A-Za-z .'-]*?)\s+(?P<state>[A-Z]{2})$", parts[0])
        if match:
            return "", clean(match.group("city")), match.group("state"), parts[1]

    return _split_blinx_address(address)


def _split_listings_gbd_address(address):
    street, city, state, zipcode = "", "", "", ""

    parts = [p.strip() for p in address.split(",") if p.strip()]

    if len(parts) >= 2 and not re.search(r"\d", parts[-1]):
        parts = parts[:-1]

    if len(parts) >= 3:
        street = ", ".join(parts[:-2])
        city = parts[-2]
        state_zip = parts[-1]
    elif len(parts) == 2:
        city = parts[0]
        state_zip = parts[1]
    elif len(parts) == 1:
        state_zip = parts[0]

    # Match "<state> <zip>" with optional trailing junk (e.g. a country
    # name glued on with no comma, as in "CA 94501 United States").
    # State prefix is optional too, in case state_zip is zip-only.
    match = re.match(
        r"^(?:(?P<state>.*?)\s+)?(?P<zip>\d{5}(?:-\d{4})?)(?:\s+.*)?$",
        state_zip.strip(),
    )
    if match:
        state = (match.group("state") or "").strip()
        zipcode = match.group("zip")
    else:
        state = state_zip.strip()

    return street, city, state, zipcode

def _decode_cf_email(hex_string):
    """Decode Cloudflare's [email protected] obfuscation.

    Cloudflare replaces `user@example.com` in the page with a hex string
    (the `data-cfemail` attribute, or the URL fragment on the
    /cdn-cgi/l/email-protection link). The first byte is a single-byte
    XOR key; every following byte is the corresponding email character
    XORed with that key.
    """
    try:
        data = bytes.fromhex(hex_string)
    except ValueError:
        return ""

    if len(data) < 2:
        return ""

    key = data[0]
    decoded = bytes(b ^ key for b in data[1:])

    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError:
        return ""

def _band_description_sections(description, labels=None):
    if not description:
        return {}

    labels = labels or _BAND_DESCRIPTION_LABELS
    canonical_by_lower = {label.lower(): label for label in labels}
    label_pattern = "|".join(re.escape(l) for l in labels)
    matches = list(re.finditer(rf"(?:^|\n)({label_pattern}):?\n?", description, flags=re.I))

    sections = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(description)
        canonical_label = canonical_by_lower[m.group(1).lower()]
        sections[canonical_label] = clean(description[start:end])
    return sections
    
def _find_cf_email(soup):
    # Form 1: <a href="/cdn-cgi/l/email-protection#HEX">
    link = soup.select_one('a[href*="/cdn-cgi/l/email-protection#"]')
    if link:
        hex_part = link["href"].split("#", 1)[-1]
        decoded = _decode_cf_email(hex_part)
        if decoded:
            return decoded

    # Form 2: <span class="__cf_email__" data-cfemail="HEX">
    span = soup.select_one("[data-cfemail]")
    if span:
        decoded = _decode_cf_email(span["data-cfemail"])
        if decoded:
            return decoded

    return ""

def _split_blinx_address(address):
    street, city, state, zipcode = "", "", "", ""

    parts = [p.strip() for p in address.split(",") if p.strip()]

    if len(parts) >= 3:
        street = ", ".join(parts[:-2])
        city = parts[-2]
        state_zip = parts[-1]
    elif len(parts) == 2:
        street = parts[0]
        state_zip = parts[1]
    elif len(parts) == 1:
        state_zip = parts[0]
    else:
        state_zip = ""

    match = re.match(r"^(.*?)\s+([\w-]*\d[\w-]*)$", state_zip.strip())
    if match:
        state = match.group(1).strip()
        zipcode = match.group(2).strip()
    else:
        state = state_zip.strip()

    return street, city, state, zipcode


def _value_by_label(soup, label):
    """Find the value associated with a text label (e.g. a "Business
    Description :" heading followed by its content elsewhere in the DOM).

    NOTE: this is a best-effort generic implementation written without a
    real sample of the target page's markup -- it was referenced by
    freelistingusa.py but had no definition anywhere in common.py. Verify
    against live HTML (or an existing definition elsewhere in the real
    codebase, if this file was only a partial copy) before relying on it.

    Strategy, in order:
      1. A heading/label/strong/dt tag whose own text matches `label`
         (optionally trailing ":") -- take the next sibling's text.
      2. Same tag match, but the value lives in the *parent's* text once
         the label text is stripped out (label and value share a container).
    """
    label_norm = clean(label).lower().rstrip(":").strip()
    if not label_norm:
        return ""

    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "label", "dt", "span"]):
        tag_text = clean(tag.get_text()).lower().rstrip(":").strip()
        if tag_text != label_norm:
            continue

        sib = tag.find_next_sibling()
        while sib is not None and isinstance(sib, NavigableString):
            sib = sib.find_next_sibling()
        if sib is not None:
            value = clean_multiline(str(sib)) if sib.find("br") else clean(sib.get_text())
            if is_meaningful(value):
                return value

        parent = tag.find_parent()
        if parent is not None:
            parent_text = clean(parent.get_text())
            remainder = clean(parent_text[len(tag.get_text()):]) if parent_text.lower().startswith(tag.get_text().strip().lower()) else ""
            if is_meaningful(remainder):
                return remainder

    return ""


def _domain_needs_cert_bypass(url):
    domain = urlparse(url).netloc.lower().split(":")[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return any(domain == d or domain.endswith("." + d) for d in IGNORE_CERT_ERRORS_DOMAINS)


def _timeout_ms_for_domain(url, requested_timeout_ms):
    """Look up a per-domain minimum render timeout for slow sites
    (see SLOW_FETCH_TIMEOUTS_MS) and return whichever is larger: the
    caller's requested timeout, or the domain's known-slow override.
    Callers that explicitly ask for a longer timeout than the override
    are never shortened."""
    domain = urlparse(url).netloc.lower().split(":")[0]
    if domain.startswith("www."):
        domain = domain[4:]

    override_ms = None
    for slow_domain, ms in SLOW_FETCH_TIMEOUTS_MS.items():
        if domain == slow_domain or domain.endswith("." + slow_domain):
            override_ms = ms
            break

    if override_ms is None:
        return requested_timeout_ms
    return max(requested_timeout_ms, override_ms)


SOCIAL_DOMAINS = {
    "facebook": "Facebook",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "twitter": "Twitter",
    "x.com": "Twitter",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "pinterest": "Pinterest",
    "wa.me": "WhatsApp",
    "whatsapp.com": "WhatsApp",
}


def _hostname_matches_social_domain(href, domain):
    try:
        netloc = urlparse(href).netloc.lower().split(":")[0]
    except Exception:
        return False
    if not netloc:
        return False
    domain = domain.lower()
    if "." in domain:
        return netloc == domain or netloc.endswith("." + domain)
    return domain in netloc.split(".")

BLOCK_SIGNALS = [
    "captcha", "are you human", "cf-browser-verification",
    "ddos-guard", "checking your browser", "verify you are human",
    "enable cookies to continue", "please enable cookies",
    "security check", "access to this page has been denied",
    "verify you're human",
]




# ---- General helpers ----


def clean(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def clean_multiline(text):
    """Like clean(), but converts <br> tags to real newlines and
    preserves paragraph breaks instead of collapsing everything
    to a single line."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def is_meaningful(text):
    return bool(re.sub(r"[,\s]", "", text or ""))


def empty_business():
    return {
        "Business Name": "",
        "Owner Name": "",
        "Street": "",
        "City": "",
        "State": "",
        "Zipcode": "",
        "Country": "",
        "Phone": "",
        "Website URL": "",
        "Keywords": "",
        "Description": "",
        "Hours": "",
        "Social Media Links": {},
        "GBP Link": "",
        "Business Email": "",
        "Category": "",
        "Logo": "",
        "Photos": []
    }


def _looks_blocked(html_text):
    combined = html_text[:4000].lower()
    return any(s in combined for s in BLOCK_SIGNALS)

CLOUDFLARE_ERROR_SIGNALS = [
    "error 521", "error 522", "error 523", "error 524", "error 525", "error 526",
    "web server is down", "connection timed out", "origin is unreachable",
    "cloudflare ray id",
]


def _looks_like_cloudflare_error(html_text):
    combined = html_text[:4000].lower()
    return any(s in combined for s in CLOUDFLARE_ERROR_SIGNALS)

def _is_maps_link(href):
    href = href.lower()
    return "google" in href and "map" in href




# ---- Fetching ----


# Fallback backoff (seconds) when a 429 response doesn't include a
# Retry-After header. Confirmed on closelocation.com: a burst of scrape
# requests started getting "429 Too Many Requests" back, and because
# fetch_via_requests previously raised immediately on any non-2xx
# status, extract_business() treated that as "blocked" and fell straight
# through to fetch_via_playwright() with no delay at all -- so the
# Playwright fetch landed on the exact same live rate limit window and
# got the identical 429 back. Retrying in-place here, with a real wait,
# gives the limiter a chance to actually clear before either fetch path
# tries again.
_RATE_LIMIT_BACKOFFS = [5, 12, 20]


def fetch_via_requests(url):
    verify = not _domain_needs_cert_bypass(url)
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    delays = [0] + _RATE_LIMIT_BACKOFFS
    response = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        response = requests.get(url, headers=HEADERS, timeout=30, verify=verify)
        if response.status_code != 429:
            break

    if response.status_code == 429:
        # Retries exhausted -- surface a clear, specific error instead
        # of the generic HTTPError raise_for_status() would give, so
        # callers/logs can tell "rate limited" apart from a real 4xx/5xx.
        raise requests.exceptions.RequestException(
            f"Rate limited (429) fetching {url} after {len(delays)} attempts"
        )

    response.raise_for_status()
    return response.text


def fetch_via_playwright(url, worker_path="playwright_worker.py", timeout_ms=45000):
    # Slow sites (see SLOW_FETCH_TIMEOUTS_MS) get a longer render
    # budget than the default -- e.g. supplyautonomy.com's profile
    # pages were blowing past the default 45s and hitting the
    # subprocess-level timeout below before Playwright even had a
    # chance to time out cleanly on its own.
    timeout_ms = _timeout_ms_for_domain(url, timeout_ms)

    ignore_https_errors = _domain_needs_cert_bypass(url)
    proc = subprocess.run(
        [sys.executable, worker_path, url, str(timeout_ms), str(int(ignore_https_errors))],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=(timeout_ms / 1000) + 30,
    )

    stdout = proc.stdout.strip()

    if not stdout:
        raise RuntimeError(
            f"playwright_worker.py produced no output. stderr: {proc.stderr}"
        )
    last_line = [line for line in stdout.splitlines() if line.strip()][-1]
    data = json.loads(last_line)

    if not data.get("success"):
        raise RuntimeError(f"Playwright fetch failed: {data.get('debug')}")

    return data["html"]




# ---- Field filtering (fields_config.py-driven) ----


_BUSINESS_TO_CONFIG_FIELD = {"Business Name": "Name"}

_FIELD_EMPTY_DEFAULTS = {
    "Social Media Links": {},
    "Photos": [],
}


def _empty_value_for(field_name):
    default = _FIELD_EMPTY_DEFAULTS.get(field_name, "")
    # Return a fresh copy so callers never share a mutable default.
    return default.copy() if isinstance(default, (dict, list)) else default


def filter_business_fields(business, url):

    source_key = fields_config.detect_source(url)
    if not source_key:
        return business

    allowed = set(fields_config.SOURCE_FIELDS.get(source_key, []))
    if not allowed:
        return business

    filtered = {}
    for field_name, value in business.items():
        config_name = _BUSINESS_TO_CONFIG_FIELD.get(field_name, field_name)

        if config_name in allowed:
            filtered[field_name] = value
        else:
            filtered[field_name] = _empty_value_for(field_name)

    return filtered
