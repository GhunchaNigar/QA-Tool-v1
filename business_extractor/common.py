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
    if any(s in combined for s in BLOCK_SIGNALS):
        return True
    # Cloudflare challenge pages can push their markers past 4000 chars
    # (inline CSS/JS). A challenge page has no real content, so only flag
    # a late marker when the page is small.
    if len(html_text) < 60000:
        tail = html_text.lower()
        return any(m in tail for m in ("challenges.cloudflare.com", "_cf_chl_opt", "cf-turnstile"))
    return False

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
    # Split on "\n" only -- str.splitlines() also breaks on U+2028/U+2029/
    # U+0085, which can appear inside the scraped HTML and would cut the
    # JSON result line into fragments. Walk back from the end so any stray
    # non-JSON output printed after the result can't hide it either.
    data = None
    for line in reversed([l for l in stdout.split("\n") if l.strip()]):
        try:
            data = json.loads(line)
            break
        except ValueError:
            continue
    if data is None:
        raise RuntimeError(
            "playwright_worker.py output was not valid JSON. "
            f"Last 300 chars: {stdout[-300:]!r} | stderr: {proc.stderr[-500:]!r}"
        )

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
