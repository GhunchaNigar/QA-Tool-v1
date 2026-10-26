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
    'SOCIAL_DOMAINS',
    '_hostname_matches_social_domain',
    'BLOCK_SIGNALS',
    'clean',
    'clean_multiline',
    'is_meaningful',
    'empty_business',
    '_looks_blocked',
    'fetch_via_scraperapi',
    'fetch_via_scraperapi_ladder',
    'FetchError',
    'summarize_attempts',
    'SCRAPERAPI_TIERS',
    'CLOUDFLARE_ERROR_SIGNALS',
    '_looks_like_cloudflare_error',
    '_is_maps_link',
    '_split_blinx_address',
    '_split_street_and_city',
    '_split_city_state_zip_address',
    '_split_address_allow_no_comma',
    '_find_cf_email',
    '_decode_cf_email',
    '_value_by_label',
    '_RATE_LIMIT_BACKOFFS',
    'fetch_via_requests',
    '_BUSINESS_TO_CONFIG_FIELD',
    '_FIELD_EMPTY_DEFAULTS',
    '_empty_value_for',
    'filter_business_fields',
    'fetch_via_curl_cffi',
]

import json
import os
import re
import sys
import time
import html
import html as html_lib  # alias: every parse_*(url, html) shadows the `html` module name
import random
import subprocess
import threading
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

# Comma-free "City ST Zip" (e.g. "Plano TX 75023").
_CITY_STATE_ZIP_NO_COMMA_RE = re.compile(
    r"^(?P<city>.+?)\s+(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


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
    """Split addresses with NO street segment, in either of two shapes:

      (a) Comma-free "City State Zip" (e.g. "Plano TX 75023") -- used by
          askmap.net, blogs.globalbusinessdirectory.us, place123.net,
          milestones.business, earthmom.org, gravitysplash.com,
          webforcompany.com, and local-biz.directory.

      (b) Two comma-separated spans, "City State, Zip" (e.g.
          preferredprofessionals.com renders <span>Plano TX</span>,
          <span>75023</span> -> "Plano TX, 75023").

    _split_blinx_address() assumes commas separate street/city/state-zip.
    Shape (a) has no commas at all, so it lands in _split_blinx_address()
    as one trailing "State Zip" token and mis-splits into
    state="Plano TX", zipcode="75023", city="" (never populated). Shape
    (b) fares no better: _split_blinx_address() takes street="Plano TX",
    state_zip="75023" -- and since "75023" has no internal whitespace to
    split on, that regex fails too, leaving state="75023" and city blank.
    Detect both shapes directly here instead of falling through.
    """
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
    "verifying you are human", "just a moment", "security verification",
    "review the security of your connection", "challenges.cloudflare.com",
    "_cf_chl_opt", "cf-turnstile", "performing security",
    "attention required! | cloudflare", "sorry, you have been blocked",
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


def _visible_text_len(html_text):
    """Length of the human-visible text (scripts/styles/tags removed)."""
    t = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", html_text)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return len(re.sub(r"\s+", " ", t).strip())


def _looks_blocked(html_text):
    # Challenge / block pages are small (tens of KB at most). A page this big
    # is a real page that merely mentions "captcha" etc. (e.g. Yelp, ~1.6 MB).
    if len(html_text) > 300000:
        return False
    combined = html_text[:4000].lower()
    if any(s in combined for s in BLOCK_SIGNALS):
        return True
    # Cloudflare challenge pages can push their markers past 4000 chars
    # (inline CSS/JS). A challenge page has no real content, so only flag
    # a late marker when the page is small AND has almost no visible text
    # (challenge pages: a few hundred chars; a real listing: 1000+).
    # (A real listing page often just loads a Turnstile script for its
    # contact form, e.g. zipcode2business.com.)
    if len(html_text) < 60000:
        tail = html_text.lower()
        if any(m in tail for m in ("challenges.cloudflare.com", "_cf_chl_opt", "cf-turnstile")):
            return _visible_text_len(html_text) < 600
    return False

CLOUDFLARE_ERROR_SIGNALS = [
    "error 521", "error 522", "error 523", "error 524", "error 525", "error 526",
    "web server is down", "connection timed out", "origin is unreachable",
    "cloudflare ray id",
]


def _looks_like_cloudflare_error(html_text):
    combined = html_text[:4000].lower()
    return any(s in combined for s in CLOUDFLARE_ERROR_SIGNALS)

_STREET_TYPES = (
    "street|st|road|rd|avenue|ave|av|drive|dr|crescent|cres|court|ct|lane|ln|"
    "place|pl|parade|pde|highway|hwy|boulevard|blvd|terrace|tce|way|close|cl|"
    "circuit|cct|grove|gr|square|sq|walk|esplanade|esp|loop|trail|row|path|"
    "mews|rise|ridge|view|green|gardens|gdns|broadway"
)


def _split_street_and_city(street):
    """"12 hilltop crescent Burwood East" (no comma between street and
    suburb) -> ("12 hilltop crescent", "Burwood East"). Splits after the
    LAST street-type word that still has words after it; returns
    (street, "") unchanged when no such word is found."""
    words = street.split()
    pattern = re.compile(r"^(?:%s)\.?$" % _STREET_TYPES, re.I)
    for i in range(len(words) - 2, 0, -1):
        if pattern.match(words[i]):
            return " ".join(words[: i + 1]), " ".join(words[i + 1 :])
    return street, ""


_SHORT_MAPS_LINK_RE = re.compile(
    r"(?:^|//)(?:www\.)?(?:g\.page|maps\.app\.goo\.gl|share\.google|business\.google\.com)(?:/|$)"
    r"|goo\.gl/maps"
)


def _is_maps_link(href):
    """Google Maps / Business Profile link. Covers the long form
    (google.com/maps/..., maps.google.com/...) and Google's short forms
    (g.page/..., maps.app.goo.gl/..., goo.gl/maps/..., share.google/...),
    which don't contain both "google" and "map"."""
    href = href.lower()
    if "google" in href and "map" in href:
        return True
    return bool(_SHORT_MAPS_LINK_RE.search(href))




# ---- Fetching ----


# Fallback backoff (seconds) when a 429 response doesn't include a
# Retry-After header. Confirmed on closelocation.com: a burst of scrape
# requests started getting "429 Too Many Requests" back, and because
# fetch_via_requests previously raised immediately on any non-2xx
# status, extract_business() treated that as "blocked" and fell straight
# through to the next fetch path with no delay at all -- so that
# fetch landed on the exact same live rate limit window and
# got the identical 429 back. Retrying in-place here, with a real wait,
# gives the limiter a chance to actually clear before either fetch path
# tries again.
_RATE_LIMIT_BACKOFFS = [5, 12, 20]


# Statuses worth retrying in place with a backoff: 429 = rate limited,
# 502/503/504 = origin temporarily down/overloaded (often recovers within
# seconds).
_SERVER_ERROR_STATUSES = (502, 503, 504)
_RETRYABLE_STATUSES = (429,) + _SERVER_ERROR_STATUSES


def fetch_via_requests(url, timeout=30):
    verify = not _domain_needs_cert_bypass(url)
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    delays = [0] + _RATE_LIMIT_BACKOFFS
    response = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        response = requests.get(url, headers=HEADERS, timeout=timeout, verify=verify)
        if response.status_code not in _RETRYABLE_STATUSES:
            break

    if response.status_code == 429:
        # Retries exhausted -- surface a clear, specific error instead
        # of the generic HTTPError raise_for_status() would give, so
        # callers/logs can tell "rate limited" apart from a real 4xx/5xx.
        raise requests.exceptions.RequestException(
            f"Rate limited (429) fetching {url} after {len(delays)} attempts"
        )

    if response.status_code in _SERVER_ERROR_STATUSES:
        # Origin is down / overloaded (e.g. Couponler's "Application Error"
        # 503 page). Retried above with backoff; if it is still failing the
        # problem is on the site's side, not in the parser or fetcher.
        raise requests.exceptions.RequestException(
            f"Server error ({response.status_code}) fetching {url} after "
            f"{len(delays)} attempts -- the site appears to be down or "
            f"overloaded. Try again later."
        )

    response.raise_for_status()
    return response.text
def fetch_via_curl_cffi(url, timeout=30, challenge_ok=True):
    """Plain HTTP fetch that imitates Chrome's TLS/HTTP2 fingerprint. Gets past
    Cloudflare checks that reject `requests` (e.g. medium.com) without the cost
    of launching a browser. Needs `pip install curl_cffi`; raises ImportError
    if it is missing so the caller can fall back to plain requests.

    challenge_ok=True hands a 403 body back to the caller (legacy behaviour);
    False raises on any status >= 400 so the caller can fall back to ScraperAPI."""
    from curl_cffi import requests as curl_requests

    verify = not _domain_needs_cert_bypass(url)
    response = None
    for delay in [0] + _RATE_LIMIT_BACKOFFS:
        if delay:
            time.sleep(delay)
        response = curl_requests.get(
            url, headers=HEADERS, impersonate="chrome", timeout=timeout, verify=verify
        )
        if response.status_code not in _RETRYABLE_STATUSES:
            break
    if response.status_code == 403 and challenge_ok:
        # Cloudflare challenge: hand the body back so _looks_blocked() sees it
        # and the dispatcher can escalate to a real browser.
        return response.text
    if response.status_code >= 400:
        raise requests.exceptions.RequestException(
            f"HTTP {response.status_code} fetching {url}"
        )
    return response.text

_SCRAPERAPI_COUNTRY_SUBDOMAINS = {"au", "uk", "ca", "nz", "us", "ie", "za", "in", "sg"}


def _scraperapi_country_code(url):
    """Guess a geotarget from a country subdomain, e.g. au.enrollbusiness.com
    -> "au". Returns None when the host has no country prefix."""
    host = urlparse(url).netloc.lower().split(":")[0]
    first = host.split(".")[0]
    if first in _SCRAPERAPI_COUNTRY_SUBDOMAINS and host.count(".") >= 2:
        return "gb" if first == "uk" else first
    return None


# ---- ScraperAPI ladder ----
#
# Tried in this order, for every site, only after the free direct fetch has
# failed. Each step is tried only if the previous one failed (HTTP 5xx, a
# timeout, an empty body, or a captcha / verification page).
SCRAPERAPI_TIERS = (
    ("ScraperAPI simple", {}),
    ("ScraperAPI premium", {"premium": "true"}),
    ("ScraperAPI ultra-premium", {"ultra_premium": "true"}),
)


def summarize_attempts(attempts):
    """{"method": <step that produced the page, "" if none>,
        "credits": <total ScraperAPI credits spent over ALL attempts>,
        "attempts": [...]}"""
    used = next((a for a in reversed(attempts) if a.get("ok")), None)
    return {
        "method": used["step"] if used else "",
        "credits": sum(int(a.get("credits") or 0) for a in attempts),
        "attempts": list(attempts),
    }


class FetchError(RuntimeError):
    """A fetch failed. `.attempts` lists every step tried; `.fetch_info`
    summarises them, including credits already spent (a captcha page that
    ScraperAPI returned with HTTP 200 is billed even though it is useless)."""

    def __init__(self, message, attempts=None):
        super().__init__(message)
        self.attempts = list(attempts or [])

    @property
    def fetch_info(self):
        return summarize_attempts(self.attempts)


# Extra steps used only when an earlier step returned a captcha / "checking
# your browser" page. Those are JavaScript tests: only a rendered page (a real
# browser inside ScraperAPI) passes them, a different IP does not.
SCRAPERAPI_RENDER_FALLBACK_TIERS = (
    ("ScraperAPI simple", {}),
    ("ScraperAPI premium", {"premium": "true"}),
)

_BLOCK_NOTE = "captcha / verification page"


# ---- Remembered "needs rendering" sites ----
# When a site's JavaScript check was passed only by a rendered fetch
# ("ScraperAPI simple + render"), the host is saved here so the next URL on
# that host starts at that step and skips the steps known to fail. Delete the
# file (or one line in it) to forget. If the remembered step ever fails, the
# host is forgotten automatically and the normal ladder runs.
_RENDER_MEMORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".scraperapi_render_sites.json",
)
_render_memory_lock = threading.Lock()


def _site_key(url):
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _load_render_sites():
    try:
        with open(_RENDER_MEMORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_render_sites(data):
    try:
        tmp = _RENDER_MEMORY_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, _RENDER_MEMORY_PATH)
    except OSError as e:
        print(f"[fetch] could not save render memory: {e}", file=sys.stderr)


def _site_needs_render(site):
    with _render_memory_lock:
        return site in _load_render_sites()


def _remember_render_site(site):
    with _render_memory_lock:
        data = _load_render_sites()
        if site not in data:
            data[site] = time.strftime("%Y-%m-%d")
            _save_render_sites(data)


def _forget_render_site(site):
    with _render_memory_lock:
        data = _load_render_sites()
        if data.pop(site, None) is not None:
            _save_render_sites(data)


def _looks_like_js_check(html_text):
    """A tiny "checking your browser / JavaScript is necessary" waiting page.
    A better IP (premium / ultra-premium) cannot pass it; only a rendered
    page can, so the ladder jumps straight to the render steps."""
    low = html_text.lower()
    return (
        len(html_text) < 5000
        and "javascript" in low
        and any(m in low for m in (
            "checking your browser", "checking browser", "just a moment",
            "enable javascript",
        ))
    )


def fetch_via_scraperapi_ladder(url, timeout=90, render=False, validate=None):
    """Fetch `url` through ScraperAPI (paid, needs SCRAPERAPI_KEY), escalating
    simple -> premium -> ultra-premium. Returns (html, attempts); raises
    FetchError (with .attempts) when every step failed.

    If any of those steps got a captcha / verification page, two more steps
    are tried with JavaScript rendering: simple + render, premium + render.
    When the page is a tiny "checking your browser" JavaScript check, the
    ladder jumps to those render steps at once (no point paying for premium
    and ultra-premium, which cannot run JavaScript).

    `validate(html) -> True` marks a page as unusable (e.g. _looks_blocked);
    such a page counts as a failed step and the next step is tried.
    A 4xx answer (bad key, quota, rate limit, target 404) stops the ladder,
    because a higher tier would not change it.
    `render=True` adds render=true to every step (JavaScript rendering).
    Each attempt: {"step", "ok", "status", "credits", "seconds", "note"};
    credits come from ScraperAPI's sa-credit-cost response header.
    """
    key = os.environ.get("SCRAPERAPI_KEY")
    if not key:
        raise FetchError("SCRAPERAPI_KEY is not set", [])

    country = _scraperapi_country_code(url)
    attempts = []
    stopped = [False]  # set when a 4xx says a higher tier would not help
    js_check = [False]  # a JavaScript browser check: skip to the render steps

    def run(tiers, remembered=False):
        for label, extra, use_render in tiers:
            extra = dict(extra)
            if use_render:
                extra["render"] = "true"
            if country and "premium" in extra:
                extra["country_code"] = country
            attempt = {
                "step": label + (" + render" if use_render else ""),
                "ok": False, "status": None, "credits": 0, "seconds": 0.0, "note": "",
            }
            if remembered:
                attempt["remembered"] = True
            attempts.append(attempt)
            started = time.time()
            try:
                response = requests.get(
                    "https://api.scraperapi.com/",
                    params={"api_key": key, "url": url, **extra},
                    timeout=timeout,
                )
            except requests.exceptions.RequestException as e:
                attempt["seconds"] = round(time.time() - started, 1)
                attempt["note"] = type(e).__name__
                continue
            attempt["seconds"] = round(time.time() - started, 1)
            attempt["status"] = response.status_code
            try:
                attempt["credits"] = int(response.headers.get("sa-credit-cost") or 0)
            except ValueError:
                attempt["credits"] = 0

            if response.status_code >= 400:
                body = (response.text or "").strip().replace("\n", " ")[:200]
                attempt["note"] = f"HTTP {response.status_code} {body}".strip()
                if response.status_code < 500:
                    stopped[0] = True  # key / quota / rate limit / target 404
                    return None
                continue

            html_text = response.text
            if not html_text or not html_text.strip():
                attempt["note"] = "empty response"
                continue
            if validate is not None and validate(html_text):
                attempt["note"] = _BLOCK_NOTE
                if not use_render and _looks_like_js_check(html_text):
                    attempt["note"] = _BLOCK_NOTE + " (JavaScript check, trying render)"
                    js_check[0] = True
                    return None  # premium / ultra-premium cannot pass this
                continue
            attempt["ok"] = True
            return html_text
        return None

    def fail():
        raise FetchError(
            "ScraperAPI failed ("
            + "; ".join(f"{a['step']}: {a['note'] or 'failed'}" for a in attempts)
            + ")",
            attempts,
        )

    site = _site_key(url)

    # A host already known to need rendering: go straight to simple + render.
    if not render and _site_needs_render(site):
        html_text = run([("ScraperAPI simple", {}, True)], remembered=True)
        if html_text is not None:
            return html_text, attempts
        _forget_render_site(site)  # no longer works: run the normal ladder
        if stopped[0]:
            fail()

    html_text = run([(l, e, render) for l, e in SCRAPERAPI_TIERS])
    if (
        html_text is None
        and not stopped[0]
        and not render
        and any(
            a["note"].startswith(_BLOCK_NOTE) or a.get("status") == 500
            for a in attempts
        )
        # HTTP 500 from every tier = ScraperAPI could not get through (typically a
        # Cloudflare Turnstile / JS challenge, e.g. cybo.com). Only a rendered
        # fetch can pass that, so try the render steps before giving up.
    ):
        html_text = run([(l, e, True) for l, e in SCRAPERAPI_RENDER_FALLBACK_TIERS])
        if html_text is not None and attempts[-1]["step"] == "ScraperAPI simple + render":
            _remember_render_site(site)
    if html_text is not None:
        return html_text, attempts
    fail()


def fetch_via_scraperapi(url, timeout=90):
    """Backward-compatible wrapper (used by debug_hours.py): rendered page
    through the ladder, HTML only."""
    html_text, _ = fetch_via_scraperapi_ladder(
        url, timeout=timeout, render=True, validate=_looks_blocked
    )
    return html_text


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

def _extract_pe_cover(soup, url):
    """Cover (header) image on ProvenExpert / ProvenEmployer profiles.

    Rendered as a CSS background on .profileHeader, declared inside
    <div id="customHeaderStyle"><style>...</style></div> with a desktop
    (header_full_*) and mobile (header_mobile_*) variant. The block only
    exists when the business uploaded its own cover. Falls back to the
    matching <link rel="preload" as="image"> tags. Returns the desktop
    URL, or "".
    """
    urls = []
    header_style = soup.select_one("#customHeaderStyle")
    if header_style:
        urls = re.findall(
            r"background-image\s*:\s*url\(([^)]+)\)",
            header_style.get_text(" "),
            re.I,
        )
    if not urls:
        for link in soup.find_all("link", href=True):
            rel = link.get("rel") or []
            if "preload" in rel and link.get("as") == "image" and "/header_" in link["href"]:
                urls.append(link["href"])
    urls = [u.strip().strip("'\"") for u in urls if u.strip()]
    if not urls:
        return ""
    desktop = [u for u in urls if "header_mobile" not in u]
    return urljoin(url, (desktop or urls)[0])


# ---- ProvenExpert / ProvenEmployer opening hours ----------------------
# Hours live in <div id="profilesOpening">, which the server sends EMPTY;
# the site's own JS (Profile.setProfileOpeningHours('<id>')) fills it in
# after load. A plain requests fetch therefore never sees them -- the page
# has to be rendered (see pe_hours_need_render + dispatch.py).

_PE_DAY_RE = re.compile(
    r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b\.?:?\s*(.*)$",
    re.I,
)
_PE_HOURS_NOISE = {"display all times", "show less", "today", "opening hours"}


def pe_hours_need_render(html):
    """True when the page has the hours widget but it hasn't been filled
    in yet (i.e. we're looking at the raw server HTML)."""
    soup = BeautifulSoup(html, "lxml")
    box = soup.select_one("#profilesOpening")
    return box is not None and not clean(box.get_text(" "))


_PE_TIME_RE = re.compile(r"\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*[-\u2013]\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?|closed|open 24", re.I)


def _pe_hours_from_container(box):
    parts = [clean(t) for t in box.stripped_strings]
    parts = [
        p for p in parts
        if p and p.lower().rstrip(":") not in _PE_HOURS_NOISE
        and not p.lower().startswith("appointments by")
    ]
    if not parts:
        return ""

    rows, current = [], None
    for part in parts:
        m = _PE_DAY_RE.match(part)
        if m:
            if current:
                rows.append(current)
            current = [m.group(1).capitalize(), [m.group(2)] if m.group(2) else []]
        elif current:
            current[1].append(part)
    if current:
        rows.append(current)

    out, seen_days = [], set()
    for day, values in rows:
        key = day.lower()[:3]
        if key in seen_days:  # widget may repeat today's row at the top
            continue
        seen_days.add(key)
        value = ", ".join(clean(v) for v in values if clean(v))
        out.append(f"{day}: {value}" if value else day)

    hours = " | ".join(out)
    # Only accept it if at least one real time/"Closed" value is present,
    # so a heading or stray link text can't pass as hours.
    return hours if _PE_TIME_RE.search(hours) else ""


def _pe_is_hidden(el):
    return "hidden" in (el.get("class") or [])


def _pe_hours_from_rows(box):
    """Structured read of the rendered widget (confirmed markup):

        <div id="openingDayRow1">
          <span id="weekday1openingDay">Monday</span>
          <span id="weekday1openingClosed" class="... hidden">CLOSED</span>
          <span id="weekday1openingTime">11:30 AM - 6:30 PM</span>
        </div>

    Every row carries BOTH a "CLOSED" span and a time span; the site adds
    class="hidden" to whichever one doesn't apply. Only the visible one is
    the real value. (Rows themselves may be hidden until "Display all
    times" is clicked -- they are still real days, so they're kept.)
    """
    out = []
    for row in box.select("[id^='openingDayRow']"):
        day_el = row.select_one("[id$='openingDay']")
        if not day_el:
            continue
        day = clean(day_el.get_text())
        closed_el = row.select_one("[id$='openingClosed']")
        time_els = row.select("[id$='openingTime']")

        visible_times = [
            clean(t.get_text(" ")) for t in time_els
            if not _pe_is_hidden(t) and clean(t.get_text(" "))
        ]
        if visible_times:
            value = ", ".join(visible_times)
        elif closed_el is not None and not _pe_is_hidden(closed_el):
            value = "Closed"
        else:
            value = ""
        out.append(f"{day}: {value}" if value else day)
    return " | ".join(out)


def _extract_pe_hours(soup):
    """Opening hours from a RENDERED ProvenExpert/ProvenEmployer page.

    1. Structured read of the #profilesOpening day rows (exact markup).
    2. Generic weekday/time text scan of the box, with hidden value spans
       removed, in case the widget markup changes.
    3. Same scan over the whole "Opening hours" section.
    """
    box = soup.select_one("#profilesOpening")
    if box:
        hours = _pe_hours_from_rows(box)
        if hours and _PE_TIME_RE.search(hours):
            return hours

        box_copy = BeautifulSoup(str(box), "lxml")
        for el in box_copy.find_all(True):
            # Drop hidden value spans, but never whole day rows.
            if _pe_is_hidden(el) and not (el.get("id") or "").startswith("openingDayRow"):
                el.decompose()
        hours = _pe_hours_from_container(box_copy)
        if hours:
            return hours

    for heading in soup.find_all(["h3", "h4", "strong"]):
        if clean(heading.get_text()).lower() == "opening hours":
            section = heading.find_parent(class_="mb20") or heading.find_parent("div")
            if section:
                hours = _pe_hours_from_container(section)
                if hours:
                    return hours
    return ""


def _extract_pe_gallery(soup, url):
    """Gallery images on ProvenExpert / ProvenEmployer. Thumbnails are
    rendered as <div class="pictureThumb" style="background-image:url(...)">
    rather than <img>, so both forms are read."""
    photos = []
    container = soup.select_one("#slideshowContainer")
    if not container:
        return photos
    for el in container.select("[style*='background-image']"):
        for u in re.findall(r"background-image\s*:\s*url\(([^)]+)\)", el.get("style", ""), re.I):
            u = u.strip().strip("'\"")
            if u and not u.startswith("data:"):
                u = urljoin(url, u)
                if u not in photos:
                    photos.append(u)
    for img in container.select("img"):
        u = img.get("data-src") or img.get("src")
        if u and not u.startswith("data:"):
            u = urljoin(url, u)
            if u not in photos:
                photos.append(u)
    return photos