"""
extractor.py
Unified business-listing extractor.

Supports (auto-detected by domain):
  - nearfinderus.com      -- static HTML, fetched with requests
  - smallbusinessusa.com  -- sits behind a JS "Checking your Browser" wall,
                             fetched via playwright_worker.py (subprocess)
  - zeemaps.com           -- map widget; data pulled directly from its
                             internal JSON API (no browser needed at all)
  - callupcontact.com     -- static HTML, fetched with requests
  - zumvu.com             -- sits behind a JS "browser check" wall,
                             fetched via playwright_worker.py (subprocess)
  - blinx.biz             -- Next.js page whose business record is loaded
                             client-side via an XHR call AFTER the initial
                             HTML loads (confirmed via DevTools Network
                             tab -- it is NOT present in the raw HTML or in
                             __NEXT_DATA__), so this is fetched via
                             playwright_worker.py (subprocess) to let the
                             page hydrate and render the real values before
                             we read them; embedded __NEXT_DATA__ is still
                             checked first as a cheap win when present.
  - place123.net          -- static HTML, fetched with requests (old-style
                             server-rendered directory template; see
                             parse_place123 for details)
  - freelistingusa.com    -- static HTML, fetched with requests (plain
                             server-rendered listing template; see
                             parse_freelistingusa for details)
  

Any other domain falls back to a best-effort generic parser fetched with
requests; if that response looks like a bot-check page, it automatically
retries via Playwright too.

Usage:
    python extractor.py "<url1>" "<url2>" ...
    (with no arguments, runs the two URLs in __main__ below)

Requires playwright_worker.py in the same directory for any site that
needs the Playwright fetch path.
"""

import json
import re
import sys
import html
import random
import subprocess
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs

import fields_config


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0 Safari/537.36"
    )
}

SOCIAL_DOMAINS = {
    "facebook": "Facebook",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "twitter": "Twitter",
    "x.com": "Twitter",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "pinterest": "Pinterest",
    # WhatsApp "click to chat" links (api.whatsapp.com/send?phone=...
    # and wa.me/...) are a messaging CTA, not the business's website --
    # several directory templates (nearfinderus.com confirmed) render
    # this under a "Website" button/icon when the listing has a WhatsApp
    # contact configured but no real external site on file. Without
    # excluding it here, the generic "first external, non-social link"
    # Website URL scan picks it up and mistakes it for the real site.
    "wa.me": "WhatsApp",
    "whatsapp.com": "WhatsApp",
}

# Same signals playwright_worker.py checks for -- used here to decide
# whether a plain requests.get() response is actually a bot-check page,
# so unmapped domains can auto-escalate to Playwright.
BLOCK_SIGNALS = [
    "captcha", "are you human", "cf-browser-verification",
    "ddos-guard", "checking your browser", "verify you are human",
    "enable cookies to continue", "please enable cookies",
    "security check", "access to this page has been denied",
    "verify you're human",
]


# ==========================================================
# Small helpers
# ==========================================================

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
    """True if text has real content once commas/whitespace are stripped.
    Guards against junk like a keywords tag whose content is just ", "."""
    return bool(re.sub(r"[,\s]", "", text or ""))


def empty_business():
    return {
        "Business Name": "",
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


# Signals that the fetched HTML is Cloudflare's own error page (origin
# server down / unreachable / timed out) rather than real page content.
# Distinct from BLOCK_SIGNALS above: a 5xx error page can render with a
# normal HTML layout and still count as a "successful" fetch from some
# fetchers (e.g. Playwright's page.goto() considers navigation
# successful even though what rendered is Cloudflare's error page for
# an unreachable origin, not the actual site), so _looks_blocked's
# bot-check phrases don't catch it. Without this separate check, the
# parser runs on the error page as if it were real content -- every
# business field comes back empty, and the generic anchor scan can pick
# up an incidental link from the error page itself (e.g. Cloudflare's
# troubleshooting docs) and mistakenly set it as the Website URL.
CLOUDFLARE_ERROR_SIGNALS = [
    "error 521", "error 522", "error 523", "error 524", "error 525", "error 526",
    "web server is down", "connection timed out", "origin is unreachable",
    "cloudflare ray id",
]


def _looks_like_cloudflare_error(html_text):
    combined = html_text[:4000].lower()
    return any(s in combined for s in CLOUDFLARE_ERROR_SIGNALS)


# Domains/patterns that indicate a Google Maps / directions link rather
# than the business's own external website. Several site templates put
# a "Directions" or map-pin link among the page's external anchors, and
# a naive "first external, non-social link wins" scan will grab that
# instead of the real website unless it's explicitly excluded (this bit
# Nearfinder: its Website URL scan picked up a maps.google.com.br link
# instead of the business's actual site).
def _is_maps_link(href):
    href = href.lower()
    return "google" in href and "map" in href


# ==========================================================
# Fetchers
# ==========================================================

def fetch_via_requests(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def fetch_via_playwright(url, worker_path="playwright_worker.py", timeout_ms=45000):
    """Runs playwright_worker.py as a subprocess and returns rendered HTML.
    Raises RuntimeError if the fetch failed or was blocked."""

    proc = subprocess.run(
        [sys.executable, worker_path, url, str(timeout_ms)],
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

    # The worker prints exactly one JSON line to stdout; take the last
    # non-empty line in case anything else leaked onto stdout.
    last_line = [line for line in stdout.splitlines() if line.strip()][-1]
    data = json.loads(last_line)

    if not data.get("success"):
        raise RuntimeError(f"Playwright fetch failed: {data.get('debug')}")

    return data["html"]


# ==========================================================
# Site parser: nearfinderus.com
# ==========================================================

def _extract_nearfinder_redirect_url(href):
    """This site wraps the real external website behind a same-domain
    click-tracking redirect link (e.g.
    "/en/empresa/redirect?url=<url-encoded target>&id=...&cache=..."),
    rather than linking to the business's site directly. That href is
    relative, so the generic "must start with http" anchor scan below
    skips it entirely and falls through to the next absolute external
    link it finds instead -- which is frequently one of NearFinder's
    own links (blog, corporate site) in the page footer, not the
    business's actual website. Detects this redirect shape and returns
    the decoded target URL, or None if `href` doesn't match it.

    NOTE: this same /empresa/redirect wrapper is used for MULTIPLE kinds
    of outbound CTAs on this template -- not just the "Website" button.
    Confirmed on the FOCAL listing: the WhatsApp "click to chat" button
    (top of page, before the Website button in document order) is ALSO
    wrapped in /empresa/redirect?url=https://api.whatsapp.com/send?..., 
    and it renders earlier in the HTML than the real Website button
    further down in the "Social / Internet" section. Because of that,
    callers MUST NOT treat "found a redirect link" as "found the
    website" -- the unwrapped target still needs to be checked against
    SOCIAL_DOMAINS / maps patterns just like a plain absolute href
    would be (see parse_nearfinderus's Website URL loop)."""
    if "/empresa/redirect" not in href.lower():
        return None
    target = parse_qs(urlparse(href).query).get("url")
    return target[0] if target else None


def parse_nearfinderus(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- JSON-LD ----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            obj = json.loads(script.string)
        except Exception:
            continue

        objects = obj if isinstance(obj, list) else [obj]

        for data in objects:

            if not isinstance(data, dict):
                continue

            if data.get("@type") not in (
                "LocalBusiness", "Organization", "Corporation",
                "Store", "ProfessionalService",
            ):
                continue

            business["Business Name"] = data.get("name", business["Business Name"])

            img = data.get("image")
            if img:
                business["Logo"] = urljoin(url, img)

            addr = data.get("address", {})
            business["Street"] = addr.get("streetAddress", business["Street"])

            locality = addr.get("addressLocality", "")
            if "-" in locality:
                city, state = locality.split("-", 1)
                business["City"] = city.strip()
                business["State"] = state.strip()
            else:
                business["City"] = locality

            business["Zipcode"] = addr.get("postalCode", business["Zipcode"])
            business["Country"] = addr.get("addressCountry", business["Country"])

            if data.get("telephone"):
                business["Phone"] = data["telephone"]

            # NOTE: JSON-LD "url" on this site points back at the
            # directory listing itself, not the real business site --
            # deliberately not used to set Website URL.

            if data.get("description"):
                business["Description"] = clean(data["description"])

            if data.get("email"):
                business["Business Email"] = data["email"]

            if data.get("openingHours"):
                business["Hours"] = data["openingHours"]

            if data.get("openingHoursSpecification"):
                business["Hours"] = data["openingHoursSpecification"]

            if data.get("sameAs"):
                links = data["sameAs"]
                if isinstance(links, list):
                    for link in links:
                        for domain, name in SOCIAL_DOMAINS.items():
                            if domain in link.lower():
                                business["Social Media Links"][name] = link

    # ---- Meta description ----
    meta = soup.find("meta", attrs={"name": "description"})

    if meta:
        description = clean(meta.get("content", ""))

        if description and not business["Description"]:
            business["Description"] = description

        match = re.search(r"Company specialized in (.+?)\.", description, re.I)
        if match:
            business["Category"] = match.group(1).strip()

    # ---- Full description override (nf-show-more-text widget) ----
    # The <meta name="description"> here is deliberately truncated for
    # SEO snippets; the full write-up lives in this widget's "text" attr.
    show_more = soup.select_one("nf-show-more-text")

    if show_more:
        full_text = clean_multiline(show_more.get("text", ""))
        if full_text:
            business["Description"] = full_text

    # ---- Meta keywords ----
    keywords = soup.find("meta", attrs={"name": "keywords"})
    if keywords:
        business["Keywords"] = keywords.get("content", "")

    # ---- OpenGraph ----
    for meta in soup.find_all("meta"):
        prop = meta.get("property", "").lower()

        if prop == "og:image" and not business["Logo"]:
            business["Logo"] = urljoin(url, meta.get("content", ""))
        elif prop == "og:title" and not business["Business Name"]:
            business["Business Name"] = meta.get("content", "")
        elif prop == "og:description" and not business["Description"]:
            business["Description"] = meta.get("content", "")
        # og:url is the directory listing's own URL here, not the
        # business's real external site -- intentionally not used.

    # ---- Phone ----
    tel = soup.select_one('a[href^="tel:"]')
    if tel:
        business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Email ----
    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").strip()

    # ---- Website URL (external site only, checked before Social) ----
    # The real website link on this template is wrapped in a same-domain
    # click-tracking redirect (see _extract_nearfinder_redirect_url), but
    # that SAME redirect wrapper is also used for other outbound CTAs on
    # the page -- confirmed: the WhatsApp "click to chat" button uses the
    # identical /empresa/redirect?url=... shape, targeting
    # api.whatsapp.com, and it sits earlier in the HTML (top CTA row)
    # than the real "Website" button (Social / Internet section further
    # down the page). Treating "found any redirect link" as "found the
    # website" therefore grabs the WhatsApp target first and never
    # reaches the real site.
    #
    # Fix: after unwrapping a redirect target, run it through the SAME
    # exclusion checks (social/WhatsApp domains, Google Maps/directions
    # links) used below for plain absolute external anchors, and keep
    # scanning rather than stopping if it's excluded. Only accept -- and
    # then break on -- a redirect target (or plain external link) that
    # survives those checks.
    for a in soup.find_all("a", href=True):
        href = a["href"]

        redirect_target = _extract_nearfinder_redirect_url(href)
        if redirect_target:
            if any(domain in redirect_target.lower() for domain in SOCIAL_DOMAINS):
                continue
            if _is_maps_link(redirect_target):
                continue
            business["Website URL"] = redirect_target
            break

        if not href.startswith("http"):
            continue
        if "nearfinderus.com" in href.lower() or "nearfinder.com" in href.lower():
            continue
        if any(domain in href.lower() for domain in SOCIAL_DOMAINS):
            continue
        if _is_maps_link(href):
            continue

        if not business["Website URL"]:
            business["Website URL"] = href
            break

    # ---- Social Media ----
    # NOTE: this deliberately re-scans ALL anchors (including redirect-
    # wrapped ones) rather than only plain absolute hrefs, so that a
    # WhatsApp CTA wrapped in /empresa/redirect?url=... still gets
    # recorded under Social Media Links even though it was correctly
    # excluded from Website URL above.
    for a in soup.find_all("a", href=True):
        href = a["href"]

        redirect_target = _extract_nearfinder_redirect_url(href)
        link_target = redirect_target if redirect_target else href

        for domain, network in SOCIAL_DOMAINS.items():
            if domain in link_target.lower():
                business["Social Media Links"][network] = link_target

    # ---- Hours (HTML fallback -- table, not JSON-LD) ----
    if not business["Hours"]:
        hours_table = soup.select_one("div.table-horario-funcionamento table")

        if hours_table:
            hours_list = []
            for tr in hours_table.select("tbody tr"):
                cells = tr.find_all("td")
                if len(cells) >= 2:
                    day = clean(cells[0].get_text())
                    time_range = clean(cells[1].get_text())
                    if day and time_range:
                        hours_list.append(f"{day}: {time_range}")

            if hours_list:
                business["Hours"] = "; ".join(hours_list)

    # ---- Category (link-based fallback) ----
    if not business["Category"]:
        category_links = soup.select('a[href*="/business-directory/category_"]')
        categories = []

        for a in category_links:
            text = clean(a.get_text())
            if text and text not in categories:
                categories.append(text)

        if categories:
            business["Category"] = ", ".join(categories)

    # ---- Images ----
    images = []
    for img in soup.find_all("img"):
        src = img.get("src")
        if src:
            src = urljoin(url, src)
            if src not in images:
                images.append(src)

    business["Photos"] = images

    return business


# ==========================================================
# Site parser: smallbusinessusa.com
# ==========================================================

_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
    "puerto rico",
}

_US_STATE_ABBR = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi",
    "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi",
    "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc",
    "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut",
    "vt", "va", "wa", "wv", "wi", "wy", "dc", "pr",
}


def _looks_like_us_state(value):
    """True if `value` reads as a US state/territory name or its
    2-letter postal abbreviation (case-insensitive)."""
    text = clean(value).strip().lower()
    if not text:
        return False
    if text in _US_STATE_NAMES:
        return True
    if len(text) == 2 and text in _US_STATE_ABBR:
        return True
    return False


def _resolve_city_state(locality_val, region_val):
    """Given a (locality, region) pair whose City/State meaning may or
    may not be swapped on this site's template (see note above), figures
    out which one is actually the state by checking whether its value
    reads as a US state name/abbreviation, and returns (city, state)
    with the roles corrected accordingly.

    Falls back to the standard convention (locality=city, region=state)
    when neither or both values look like a state, since that's the
    far more common template behavior generally."""

    region_is_state = _looks_like_us_state(region_val)
    locality_is_state = _looks_like_us_state(locality_val)

    if region_is_state and not locality_is_state:
        # Normal, non-swapped order: locality is city, region is state.
        return locality_val, region_val

    if locality_is_state and not region_is_state:
        # Swapped on this listing: region is city, locality is state.
        return region_val, locality_val

    # Ambiguous (neither or both look like a state) -- default to the
    # standard convention rather than guessing.
    return locality_val, region_val


def parse_smallbusinessusa(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- business:contact_data Open Graph extension (primary source) ----
    contact_meta = {}
    for meta in soup.find_all("meta", property=True):
        prop = meta["property"]
        if prop.startswith("business:contact_data:"):
            key = prop.split(":")[-1]
            contact_meta[key] = clean(meta.get("content", ""))

    business["Street"] = contact_meta.get("street_address", "")
    # City/State roles are resolved dynamically per-listing -- see the
    # module note above for why a hardcoded swap doesn't work here.
    business["City"], business["State"] = _resolve_city_state(
        contact_meta.get("locality", ""), contact_meta.get("region", "")
    )
    business["Zipcode"] = contact_meta.get("postal_code", "")
    business["Country"] = contact_meta.get("country_name", "")
    business["Phone"] = contact_meta.get("phone_number", "")
    business["Website URL"] = contact_meta.get("website", "")

    # ---- JSON-LD (name/logo, backs up address/phone if missing) ----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:

            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
                continue

            business["Business Name"] = obj.get("name", business["Business Name"])

            if obj.get("image") and not business["Logo"]:
                business["Logo"] = urljoin(url, obj["image"])

            if obj.get("telephone") and not business["Phone"]:
                business["Phone"] = obj["telephone"]

            addr = obj.get("address", {})

            if not business["Street"]:
                business["Street"] = addr.get("streetAddress", "")
            # City/State roles may or may not be swapped here too, same
            # as the contact_data meta tags above -- resolve dynamically
            # rather than assuming either fixed order (see module note).
            if not business["City"] and not business["State"]:
                business["City"], business["State"] = _resolve_city_state(
                    addr.get("addressLocality", ""), addr.get("addressRegion", "")
                )
            if not business["Zipcode"]:
                business["Zipcode"] = addr.get("postalCode", "")
            if not business["Country"]:
                business["Country"] = addr.get("addressCountry", "")

    # ---- Business Name fallback (visible <h1>) ----
    if not business["Business Name"]:
        h1 = soup.select_one("article.detail h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Meta description ----
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc:
        desc = clean(meta_desc.get("content", ""))
        if is_meaningful(desc):
            business["Description"] = desc

    # ---- Meta keywords ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            business["Keywords"] = clean(kw_raw)

    # ---- Phone fallback (tel: link) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Email (mailto: link, if any) ----
    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").strip()

    # ---- Website URL fallback ("Visit Website" button) ----
    if not business["Website URL"]:
        website_link = soup.select_one("#visit-website")
        if website_link and website_link.get("href"):
            business["Website URL"] = website_link["href"]

    # ---- Category (breadcrumb inside the listing article) ----
    category_links = soup.select("article.detail ul.breadcrumb a")
    categories = []
    for a in category_links:
        text = clean(a.get_text())
        if text and text not in categories:
            categories.append(text)
    if categories:
        business["Category"] = ", ".join(categories)

    # ---- Logo fallback (og:image) ----
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Photos (full-size gallery images, fallback to thumbnails) ----
    photos = []
    for a in soup.select("div.image-gallery a.image-link[href]"):
        photo_url = urljoin(url, a["href"])
        if photo_url not in photos:
            photos.append(photo_url)
    if not photos:
        for img in soup.select("div.image-gallery img[src]"):
            photo_url = urljoin(url, img["src"])
            if photo_url not in photos:
                photos.append(photo_url)
    business["Photos"] = photos

    # ---- Social Media Links (real anchors only) ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# Site parser: zeemaps.com
# ==========================================================

ZEEMAPS_BASE = "https://www.zeemaps.com"


def _zeemaps_group_id(url):
    qs = parse_qs(urlparse(url).query)
    group = qs.get("group") or qs.get("g")
    if not group:
        raise ValueError(f"No ?group= or ?g= parameter found in ZeeMaps URL: {url}")
    return group[0]


def _zeemaps_get(path, **params):
    response = requests.get(f"{ZEEMAPS_BASE}{path}", params=params, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.json()


def parse_zeemaps(url, html=None):
    """html is accepted (and ignored) so this fits the same
    parser(url, html) signature as the other site parsers, even
    though ZeeMaps data comes from API calls, not page HTML."""

    group = _zeemaps_group_id(url)

    # ---- Data version hash (required by /emarkers) ----
    version = _zeemaps_get("/regions/version", g=group).get("v", "")

    # ---- Marker list ----
    markers = _zeemaps_get("/emarkers", g=group, k="REGULAR", e="false", v=version)

    # ---- Custom field id -> name mapping (generic, not hardcoded) ----
    attrs_raw = _zeemaps_get("/data/attributes", group=group)
    field_names = {fid: meta.get("n", "").strip().lower() for fid, meta in attrs_raw.items()}

    # ---- Map-level description fallback ----
    mapprops = _zeemaps_get("/data/mapprops", group=group, readonly="true")
    map_about = clean_multiline(mapprops.get("mp", {}).get("about", ""))

    results = []

    for m in markers:
        marker_id = m.get("id")
        business = empty_business()

        # Base fields from the marker list
        business["Business Name"] = m.get("nm", "")
        business["Street"] = m.get("s", "")
        business["City"] = m.get("city", "")
        business["State"] = m.get("state", "")
        business["Zipcode"] = m.get("zip", "")
        business["Country"] = m.get("cty", "")

        # ---- Per-marker popup detail (has the real field values) ----
        try:
            detail = _zeemaps_get(
                "/etext",
                g=group,
                j=1,
                sh="",
                _dc=random.random(),
                eids=f"[{marker_id}]",
            )
            if isinstance(detail, list):
                detail = detail[0] if detail else {}
        except Exception:
            detail = {}

        if detail.get("title"):
            business["Business Name"] = detail["title"]

        addr = detail.get("ad", {})
        if addr.get("street"):
            business["Street"] = addr["street"]
        if addr.get("city"):
            business["City"] = addr["city"]
        if addr.get("state"):
            business["State"] = addr["state"]
        if addr.get("postcode"):
            business["Zipcode"] = addr["postcode"]
        if addr.get("country"):
            business["Country"] = addr["country"]

        # ---- Address fallback: some ZeeMaps groups never populate ----
        # separate city/state/zip fields at all -- the whole address
        # (e.g. "131 Continental Dr, Suite 305, Newark, Delaware 19713")
        # sits in the street field instead, from both the marker list
        # AND the /etext detail call. When that happens, split it the
        # same way blinx.biz/place123.net addresses are split, rather
        # than leaving City/State blank. Only fires when City AND State
        # are both still empty, so maps that DO give clean separate
        # fields are never touched.
        if business["Street"] and not business["City"] and not business["State"]:
            street, city, state, zipcode = _split_blinx_address(business["Street"])
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            if not business["Zipcode"]:
                business["Zipcode"] = zipcode

        # ---- Custom fields, resolved generically by name ----
        unmapped_fields = {}
        for fid, value in detail.get("fields", {}).items():
            if not value:
                continue
            name = field_names.get(fid, "")
            if name == "phone":
                business["Phone"] = value
            elif name == "website":
                business["Website URL"] = value
            elif name == "email":
                business["Business Email"] = value
            elif name == "description":
                business["Description"] = clean_multiline(value)
            else:
                # Preserve any custom field this map defines that we
                # don't have a dedicated slot for, instead of dropping it.
                label = attrs_raw.get(fid, {}).get("n", fid)
                unmapped_fields[label] = value

        if unmapped_fields:
            business["Zeemaps Extra Fields"] = unmapped_fields

        if not business["Description"]:
            business["Description"] = map_about

        # ---- Photo (embedded as an <img> tag inside the "i" field) ----
        img_html = detail.get("i", "")
        if img_html:
            img_match = re.search(r"src=['\"]([^'\"]+)['\"]", img_html)
            if img_match:
                photo_url = img_match.group(1)
                business["Logo"] = photo_url
                business["Photos"] = [photo_url]

        results.append(business)

    if not results:
        return empty_business()

    # Most ZeeMaps listing pages carry exactly one marker (the business
    # this map was published for). Return that single dict directly so
    # callers get the same shape as the other parsers; only fall back
    # to a list if the map genuinely has multiple markers.
    return results[0] if len(results) == 1 else results


# ==========================================================
# Site parser: callupcontact.com
# ==========================================================


HEADING_TAGS = re.compile(r"^h[1-6]$")

_CALLUPCONTACT_KEYWORD_BOILERPLATE = {
    "businessprofile",
    "ratings",
    "business profiles",
    "products and services",
    "directions",
    "maps",
    "business listing",
    "telephone",
    "fax",
    "postal address",
    "postal code",
}


def _decode_cf_email(hex_string):
    """Decodes Cloudflare's email-obfuscation hex string back to a
    plain email address. The XOR-decoded bytes come out as literal
    numeric HTML entities (e.g. "&#105;&#110;..."), so an extra
    html.unescape() pass is needed to get the real address."""
    try:
        key = int(hex_string[:2], 16)
        decoded = "".join(
            chr(int(hex_string[i:i + 2], 16) ^ key)
            for i in range(2, len(hex_string), 2)
        )
        return html.unescape(decoded)
    except Exception:
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


def _is_leaf(tag):
    """True if tag has no nested element children (only text/whitespace).
    Used to avoid matching wrapper divs whose get_text() happens to
    include both the label and the value concatenated together."""
    return tag.find(True) is None


def _find_label_value_element(soup, label):
    """Finds any leaf tag whose text exactly matches `label`, then
    returns the neighboring element that holds its value (not the
    text -- the element itself, so callers can inspect its structure,
    e.g. pull out just the <a> tags for a multi-value field)."""

    for tag in soup.find_all(True):
        if not _is_leaf(tag):
            continue
        if clean(tag.get_text()).lower() != label.lower():
            continue

        sib = tag.find_next_sibling()
        while sib is not None and not clean(sib.get_text()):
            sib = sib.find_next_sibling()
        if sib:
            return sib

        if tag.parent:
            parent_sib = tag.parent.find_next_sibling()
            while parent_sib is not None and not clean(parent_sib.get_text()):
                parent_sib = parent_sib.find_next_sibling()
            if parent_sib:
                return parent_sib

    return None


def _value_by_label(soup, label, separator=" "):
    elem = _find_label_value_element(soup, label)
    return clean(elem.get_text(separator=separator)) if elem else ""


# Kept as a thin wrapper so any external references to the old name
# keep working; heading-only matching is no longer accurate for this
# site's actual markup.
def _value_after_heading(soup, label, separator=" "):
    return _value_by_label(soup, label, separator=separator)


def parse_callupcontact(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name (page <h1>) ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Category (multiple tag links under one heading) ----
    category_text = _value_after_heading(soup, "Category", separator=", ")
    if category_text:
        business["Category"] = category_text

    # ---- About Us (description) ----
    description = _value_after_heading(soup, "About Us")
    if description:
        business["Description"] = description

    # ---- Call & Message ----
    phone = _value_after_heading(soup, "Telephone")
    if phone:
        business["Phone"] = phone

    website = _value_after_heading(soup, "Website")
    if website:
        business["Website URL"] = website

    # ---- Email (Cloudflare-obfuscated, not a plain mailto:) ----
    email = _find_cf_email(soup)
    if email:
        business["Business Email"] = email

    # ---- Address ----
    street = _value_after_heading(soup, "Street Address")
    if street:
        business["Street"] = street

    city = _value_after_heading(soup, "City")
    if city:
        business["City"] = city

    state = _value_after_heading(soup, "State / Province")
    if state:
        business["State"] = state

    zipcode = _value_after_heading(soup, "Zip / Postal Code")
    if zipcode:
        business["Zipcode"] = zipcode

    country = _value_after_heading(soup, "Country")
    if country:
        business["Country"] = country

    # ---- Meta description fallback (page-level, matches About Us usually) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Meta keywords (strip this template's fixed boilerplate tail --
    #      see _CALLUPCONTACT_KEYWORD_BOILERPLATE above) ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            tokens = [clean(t) for t in kw_raw.split(",")]
            tokens = [
                t for t in tokens
                if t and t.lower() not in _CALLUPCONTACT_KEYWORD_BOILERPLATE
            ]
            if tokens:
                business["Keywords"] = ", ".join(tokens)

    return business


# ==========================================================
# Site parser: zumvu.com
# ==========================================================

def parse_zumvu(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- JSON-LD (ProfilePage -> mainEntity) ----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except Exception:
            continue

        entity = data.get("mainEntity") if isinstance(data, dict) else None
        if not isinstance(entity, dict):
            continue
        # Template mislabels businesses as "Person" -- accept either.
        if entity.get("@type") not in ("Person", "Organization", "LocalBusiness"):
            continue

        business["Business Name"] = entity.get("name", business["Business Name"])

        if entity.get("image"):
            business["Logo"] = urljoin(url, entity["image"])

        if entity.get("description") and is_meaningful(entity["description"]):
            business["Description"] = clean(entity["description"])

        addr = entity.get("address", {})
        if isinstance(addr, dict):
            business["Street"] = addr.get("streetAddress", business["Street"])
            business["City"] = addr.get("addressLocality", business["City"])
            business["State"] = addr.get("addressRegion", business["State"])
            business["Zipcode"] = addr.get("postalCode", business["Zipcode"])
            business["Country"] = addr.get("addressCountry", business["Country"])

        knows_about = entity.get("knowsAbout")
        if knows_about and isinstance(knows_about, list):
            terms = [clean(t) for t in knows_about if clean(t)]
            if terms:
                business["Keywords"] = ", ".join(terms)

        if entity.get("sameAs"):
            links = entity["sameAs"]
            if isinstance(links, list):
                for link in links:
                    for domain, name in SOCIAL_DOMAINS.items():
                        if domain in link.lower():
                            business["Social Media Links"][name] = link

    # ---- Business Name fallback (visible <h1>) ----
    if not business["Business Name"]:
        h1 = soup.select_one(".prottlebx h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Contact block: phone / email / website by icon class ----
    contact_ul = soup.select_one(".contactbox.extncontctbx ul.abtcontact-page")
    if contact_ul:
        for li in contact_ul.find_all("li"):
            icon = li.find("i")
            a = li.find("a", href=True)
            if not icon or not a:
                continue
            icon_classes = icon.get("class", [])

            if "fa-phone" in icon_classes:
                business["Phone"] = a["href"].replace("tel:", "").strip()
            elif "fa-envelope" in icon_classes:
                business["Business Email"] = a["href"].replace("mailto:", "").strip()
            elif "fa-globe" in icon_classes:
                business["Website URL"] = a["href"]

    # ---- Description (About section -- richer than meta description) ----
    about = soup.select_one(".resabout .addinfo")
    if about:
        text = clean_multiline(about.get_text(separator="\n"))
        if is_meaningful(text):
            business["Description"] = text

    # ---- Meta description fallback (usually empty on this template) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords fallback: meta tag, then the visible tag pills ----
    if not business["Keywords"]:
        meta_kw = soup.find("meta", attrs={"name": "keywords"})
        if meta_kw and is_meaningful(meta_kw.get("content", "")):
            business["Keywords"] = clean(meta_kw["content"])

    if not business["Keywords"]:
        tags = [clean(t.get_text()) for t in soup.select(".taginfoabout .right-tags")]
        tags = [t for t in tags if t]
        if tags:
            business["Keywords"] = ", ".join(tags)

    # ---- Address fallback (visible Location block, if JSON-LD missing) ----
    if not any([business["Street"], business["City"], business["State"]]):
        loc_li = soup.select_one(".locflexfirstcol ul.abtcontact-page li")
        if loc_li:
            addr_text = clean_multiline(loc_li.get_text(separator="\n"))
            lines = [l for l in addr_text.split("\n") if l]
            if lines:
                business["Street"] = lines[0]
            if len(lines) > 1:
                # e.g. "Dover, Delaware 19901, UNITED STATES"
                business["City"] = lines[1]

    # ---- Logo fallback (og:image) ----
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Social Media (real anchors, in case JSON-LD sameAs was empty) ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower() and "zumvu.com" not in href.lower():
                business["Social Media Links"][network] = href

    return business



# ==========================================================
# Site parser: blinx.biz
# ==========================================================

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

    # "Delaware 19901" -> state="Delaware", zip="19901". Zip can be a
    # plain US 5-digit/ZIP+4 code; if the trailing token isn't
    # digit-like, leave everything in state rather than guessing.
    match = re.match(r"^(.*?)\s+([\w-]*\d[\w-]*)$", state_zip.strip())
    if match:
        state = match.group(1).strip()
        zipcode = match.group(2).strip()
    else:
        state = state_zip.strip()

    return street, city, state, zipcode


# This is the shape Blinx renders the full address in once the page is
# rendered: "<street>, <city>, <ST> ,<zip>" (note the loose comma/space
# right before the zip -- that's how their own template formats it, not
# a parsing artifact on our end). Captured as its own regex because the
# API's raw "address" field (see _find_brownbook_record / below) is only
# ever the bare street with no commas at all, so it can't be split the
# same way place123/freelistingusa addresses are split.
_BLINX_RENDERED_ADDRESS_RE = re.compile(
    r"^(?P<street>.+?),\s*(?P<city>[^,]+?),\s*(?P<state>[A-Za-z]{2,})\s*,?\s*(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _extract_blinx_address_from_dom(soup):
    """Blinx's business-detail API only returns the raw street address
    in its "address" field (e.g. "8910 University Center Ln" -- no
    city/state/zip attached, confirmed via the actual API response).
    City/State/Zip only ever appear together in the rendered page text,
    in a line shaped like "8910 University Center Ln, San Diego, CA
    ,92122". Scan the rendered text for that shape instead of trying to
    reconstruct it from the API payload alone.

    Returns (street, city, state, zip) or None if no matching line is
    found (e.g. because the HTML was fetched via plain requests before
    the page hydrated and rendered the address)."""

    for raw_line in soup.get_text(separator="\n").split("\n"):
        line = clean(raw_line)
        if not line or "," not in line:
            continue
        match = _BLINX_RENDERED_ADDRESS_RE.match(line)
        if match:
            return (
                match.group("street").strip(),
                match.group("city").strip(),
                match.group("state").strip(),
                match.group("zip").strip(),
            )

    return None


def _find_brownbook_record(obj, _depth=0):
    """Recursively searches a decoded __NEXT_DATA__ JSON tree for the
    first dict that has a "brownbook_id" key, which identifies the
    actual listing record regardless of how deeply Next.js nests it
    inside pageProps.

    NOTE: on the current version of blinx.biz this record is loaded by
    a client-side XHR call made *after* the initial page load (verified
    via the browser Network tab), not embedded in __NEXT_DATA__ at all.
    This function is kept because some listings/older pages may still
    embed it server-side, but callers must not assume it will find
    anything -- see parse_blinx, which treats the rendered DOM as the
    primary source and this as a bonus when present."""

    if _depth > 12:
        return None

    if isinstance(obj, dict):
        if "brownbook_id" in obj:
            return obj
        for value in obj.values():
            found = _find_brownbook_record(value, _depth + 1)
            if found:
                return found

    elif isinstance(obj, list):
        for item in obj:
            found = _find_brownbook_record(item, _depth + 1)
            if found:
                return found

    return None


def _blinx_links_to_business(business, links):
    """The record's "links" field can plausibly be a list of plain
    URL strings or a list of {"type"/"name": ..., "url"/"href": ...}
    dicts, depending on link type (website vs. social). Handle both
    shapes rather than assuming one."""

    if not isinstance(links, list):
        return

    for entry in links:
        if isinstance(entry, str):
            href = entry
        elif isinstance(entry, dict):
            href = entry.get("url") or entry.get("href") or entry.get("link") or ""
        else:
            continue

        if not href:
            continue

        matched_social = False
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href
                matched_social = True
                break

        if not matched_social and not business["Website URL"]:
            business["Website URL"] = href


def parse_blinx(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Primary source: Next.js __NEXT_DATA__ hydration payload ----
    # Only present when a listing happens to be server-rendered with the
    # record already embedded; on the common case (record loaded via a
    # post-load XHR call) this will simply come back None and every
    # field below falls through to the rendered-DOM / meta-tag sources.
    record = None
    next_data_script = soup.find("script", id="__NEXT_DATA__")

    if next_data_script and next_data_script.string:
        try:
            next_data = json.loads(next_data_script.string)
        except Exception:
            next_data = None

        if next_data:
            record = _find_brownbook_record(next_data)

    if record:
        business["Business Name"] = record.get("name") or record.get("title") or ""

        business["Country"] = record.get("country", "")
        business["Phone"] = record.get("phone", "")
        business["Business Email"] = record.get("email", "")

        description = record.get("description", "")
        if is_meaningful(description):
            business["Description"] = clean_multiline(description)

        logo = record.get("logo") or record.get("image")
        if logo:
            business["Logo"] = urljoin(url, logo)

        _blinx_links_to_business(business, record.get("links"))

        # The API's "address" field is only ever the bare street (e.g.
        # "8910 University Center Ln") with no city/state/zip attached.
        # Only run it through the comma-splitter if it actually looks
        # like a full "street, city, state zip" string; otherwise it's
        # just the street, and running it through the splitter would
        # dump the whole thing into State (which is what the previous
        # version of this parser did).
        address = record.get("address", "")
        if address:
            if "," in address:
                street, city, state, zipcode = _split_blinx_address(address)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode
            else:
                business["Street"] = clean(address)

    # ---- Address: prefer the rendered DOM ----
    # This is the authoritative source for the full address (street +
    # city + state + zip together) since the API/record only ever gives
    # the bare street. Overrides whatever the record block above set.
    dom_address = _extract_blinx_address_from_dom(soup)
    if dom_address:
        street, city, state, zipcode = dom_address
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Business Name fallback (og:title / <title>) ----
    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"])
        elif soup.title:
            business["Business Name"] = clean(soup.title.get_text()).split("|")[0].strip()

    # ---- Description fallback (meta description) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Logo fallback (og:image) ----
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Phone fallback (tel: link on the page) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Email fallback (mailto: link on the page) ----
    if not business["Business Email"]:
        email = soup.select_one('a[href^="mailto:"]')
        if email:
            business["Business Email"] = email["href"].replace("mailto:", "").strip()

    # ---- Website / social fallback (visible anchors) ----
    # This is also the primary source in practice: since the record is
    # loaded client-side, the real website link is almost always only
    # available once rendered, as a plain anchor here, rather than via
    # `record["links"]` above (which is frequently unavailable when
    # fetched via plain requests).
    for a in soup.find_all("a", href=True):
        href = a["href"]

        if not href.startswith("http"):
            continue
        if "blinx.biz" in href.lower():
            continue
        if "google.com/maps" in href.lower() or _is_maps_link(href):
            continue

        matched_social = False
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"].setdefault(network, href)
                matched_social = True
                break

        if not matched_social and not business["Website URL"]:
            business["Website URL"] = href

    return business


# ==========================================================
# Site parser: place123.net
# ==========================================================

_PLACE123_LABELS = {
    "owner name": None,
    "phone": "Phone",
    "website": "Website URL",
    "url": "Website URL",
    "business email": "Business Email",
    "about us": "Description",
    "related searches": "Keywords",
}

_PLACE123_TERMINATORS = {
    "what do you think about us?",
    "your nickname",
    "comments",
    "start a discussion",
    "places nearby",
    "edit business",
    "your business in this directory?",
    "add your business",
    "position on map",
    "gps coordinates",
    "find nearby",
    "street view",
    "write a review",
}


def parse_place123(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (og:title matches the visible heading) ----
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        business["Business Name"] = clean(og_title["content"])

    if not business["Business Name"]:
        h_tag = soup.find(re.compile(r"^h[1-6]$"))
        if h_tag:
            business["Business Name"] = clean(h_tag.get_text())

    # ---- Logo ----
    logo_img = soup.find("img", alt=re.compile("location logo", re.I))
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Whole-page text as lines (this template has no wrapping tags
    #      around Category/Address/Country or the Owner Name..Related
    #      Searches block -- everything is <br>-separated plain text) ----
    lines = [
        clean(line)
        for line in soup.get_text(separator="\n").split("\n")
    ]
    lines = [l for l in lines if l]
    label_keys = set(_PLACE123_LABELS.keys())

    # ---- Category / Address / Country (positional: the 3 lines right
    #      after the business-name heading) ----
    name_idx = None
    if business["Business Name"]:
        target = business["Business Name"].lower()
        for idx, line in enumerate(lines):
            if line.lower() == target:
                name_idx = idx
                break

    if name_idx is not None:
        if name_idx + 1 < len(lines) and lines[name_idx + 1].rstrip(":").lower() not in label_keys:
            business["Category"] = lines[name_idx + 1]

        if name_idx + 2 < len(lines):
            address_line = lines[name_idx + 2]
            if "," in address_line:
                street, city, state, zipcode = _split_blinx_address(address_line)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode

        if name_idx + 3 < len(lines) and lines[name_idx + 3].rstrip(":").lower() not in label_keys:
            business["Country"] = lines[name_idx + 3]

    # ---- Owner Name / Phone / Website / URL / Business Email / About Us /
    #      Related Searches (flat label-then-value scan, stopping at
    #      either the next known label or a page-chrome terminator).
    #      NOTE: this site labels the website link "URL:" rather than
    #      "Website:" -- both are mapped to Website URL in
    #      _PLACE123_LABELS above. Without "url" recognized as a label,
    #      the value-collection loop for the preceding field (Business
    #      Email) wouldn't stop at it, and the email value would swallow
    #      the "URL:" line and the URL itself. ----
    i = 0
    n = len(lines)
    while i < n:
        norm = lines[i].rstrip(":").strip().lower()

        if norm in label_keys:
            field = _PLACE123_LABELS[norm]

            j = i + 1
            value_lines = []
            while j < n:
                next_norm = lines[j].rstrip(":").strip().lower()
                if next_norm in label_keys or next_norm in _PLACE123_TERMINATORS:
                    break
                value_lines.append(lines[j])
                j += 1

            value = clean(" ".join(value_lines))
            if field and value:
                business[field] = value

            i = j
        else:
            i += 1

    # ---- Website URL fallback (visible external anchor) ----
    if not business["Website URL"] or not business["Website URL"].startswith("http"):
        business["Website URL"] = ""
        for a in soup.find_all("a", href=True):
            href = a["href"]

            if not href.startswith("http"):
                continue
            if "place123.net" in href.lower():
                continue
            if "graph.facebook.com" in href.lower():
                continue
            if "google.com" in href.lower() or "googleapis.com" in href.lower():
                continue
            if any(domain in href.lower() for domain in SOCIAL_DOMAINS):
                continue

            business["Website URL"] = href
            break

    # ---- Description fallback (meta description -- truncated SEO
    #      snippet of the same "About Us" copy, so About Us wins if present) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    return business


# ==========================================================
# Site parser: freelistingusa.com
# ==========================================================

def parse_freelistingusa(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (og:title, minus the site-name suffix) ----
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        business["Business Name"] = clean(og_title["content"]).split("|")[0].strip()

    if not business["Business Name"]:
        h_tag = soup.find(re.compile(r"^h[1-6]$"))
        if h_tag:
            business["Business Name"] = clean(h_tag.get_text())

    # ---- Contact block, scoped via the tel: link ----
    tel = soup.select_one('a[href^="tel:"]')
    scope = soup

    if tel:
        business["Phone"] = tel["href"].replace("tel:", "").strip()
        # Walk up to the nearest list/container so Address/Website/Email
        # below are read from this same block, not the whole page.
        contact_container = tel.find_parent(["ul", "ol", "div"])
        if contact_container:
            scope = contact_container

    # Address (Google Maps link's visible text holds the full address)
    maps_link = scope.select_one('a[href*="maps.google.com"]')
    if maps_link:
        address_text = clean(maps_link.get_text())
        normalized = re.sub(r"\s*-\s*(\d)", r" \1", address_text)
        street, city, state, zipcode = _split_blinx_address(normalized)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # Email (Cloudflare-obfuscated, scoped to the contact block so the
    # footer's separate "Contact Us" email is never picked up instead)
    email = _find_cf_email(scope)
    if email:
        business["Business Email"] = email

    # Website (whichever external link is left once maps/tel/email are excluded)
    for a in scope.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "freelistingusa.com" in href.lower():
            continue
        if "maps.google.com" in href.lower() or "google.com/maps" in href.lower():
            continue
        if "cdn-cgi/l/email-protection" in href.lower():
            continue
        business["Website URL"] = href
        break

    # ---- Category ("Listed In :" link -- same URL as the breadcrumb) ----
    category_links = soup.select('a[href*="/listings/category/"]')
    categories = []
    for a in category_links:
        text = clean(a.get_text())
        if text and text not in categories:
            categories.append(text)
    if categories:
        business["Category"] = ", ".join(categories)

    # ---- Description ("Business Description" heading) ----
    description = _value_by_label(soup, "Business Description")
    if is_meaningful(description):
        business["Description"] = description

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords ("Services" + "Tags :" tag links, both /listings/tag/) ----
    tag_links = soup.select('a[href*="/listings/tag/"]')
    tags = []
    for a in tag_links:
        text = clean(a.get_text())
        if text and text not in tags:
            tags.append(text)
    if tags:
        business["Keywords"] = ", ".join(tags)

    # ---- Business Hours (dedicated hours-grid block, one <p> per day) ----
    hours_grid = soup.select_one("div.business-hours-listing div.hours-grid")
    if hours_grid:
        day_entries = [clean(p.get_text()) for p in hours_grid.find_all("p")]
        day_entries = [d for d in day_entries if d]
        if day_entries:
            business["Hours"] = "; ".join(day_entries)

    # ---- Logo / Photos (S3-hosted listing photo, full-size via its
    #      wrapping anchor rather than the smaller "_thumb" <img> src) ----
    photo_link = soup.select_one('a[href*="freelistingusa.s3"]')
    if photo_link and photo_link.get("href"):
        full_photo = photo_link["href"]
        business["Logo"] = full_photo
        business["Photos"] = [full_photo]
    else:
        photo_img = soup.select_one('img[src*="freelistingusa.s3"]')
        if photo_img and photo_img.get("src"):
            business["Logo"] = urljoin(url, photo_img["src"])
            business["Photos"] = [business["Logo"]]

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Social Media (dedicated #listing-follow block -- this sits
    #      as a SIBLING of the tel:-scoped contact list, not inside it,
    #      so it must be located independently of `scope` above. Using
    #      this fixed id also keeps FreeListingUSA's own Facebook link
    #      in the page footer from being picked up instead.) ----
    follow_block = soup.select_one("#listing-follow")
    if follow_block:
        for a in follow_block.find_all("a", href=True):
            href = a["href"]
            for domain, network in SOCIAL_DOMAINS.items():
                if domain in href.lower():
                    business["Social Media Links"][network] = href

    return business



# ==========================================================
# Generic best-effort parser (unmapped domains)
# ==========================================================

def parse_generic(url, html):
    """Best-effort fallback for domains without a dedicated parser above.
    Covers the common patterns seen across both sites so far: JSON-LD
    LocalBusiness, business:contact_data:* meta, plain meta tags,
    tel:/mailto: links. Category/Hours are left blank since those are
    too structurally different site-to-site to guess reliably."""

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    for meta in soup.find_all("meta", property=True):
        prop = meta["property"]
        if prop.startswith("business:contact_data:"):
            key = prop.split(":")[-1]
            val = clean(meta.get("content", ""))
            if key == "street_address":
                business["Street"] = val
            elif key == "locality":
                business["City"] = val
            elif key == "region":
                business["State"] = val
            elif key == "postal_code":
                business["Zipcode"] = val
            elif key == "country_name":
                business["Country"] = val
            elif key == "phone_number":
                business["Phone"] = val
            elif key == "website":
                business["Website URL"] = val

    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        objects = data if isinstance(data, list) else [data]
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            if obj.get("@type") in ("LocalBusiness", "Organization"):
                business["Business Name"] = obj.get("name", business["Business Name"])
                if obj.get("telephone") and not business["Phone"]:
                    business["Phone"] = obj["telephone"]
                if obj.get("image") and not business["Logo"]:
                    business["Logo"] = urljoin(url, obj["image"])
                addr = obj.get("address", {})
                if not business["Street"]:
                    business["Street"] = addr.get("streetAddress", "")
                if not business["City"]:
                    business["City"] = addr.get("addressLocality", "")
                if not business["State"]:
                    business["State"] = addr.get("addressRegion", "")
                if not business["Zipcode"]:
                    business["Zipcode"] = addr.get("postalCode", "")
                if not business["Country"]:
                    business["Country"] = addr.get("addressCountry", "")

    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = og_title["content"]

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc:
        desc = clean(meta_desc.get("content", ""))
        if is_meaningful(desc):
            business["Description"] = desc

    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            business["Keywords"] = clean(kw_raw)

    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").strip()

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    for a in soup.find_all("a", href=True):
        href = a["href"]
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# Field filtering (fields_config.py-driven)
# ==========================================================

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
    """Keeps only the fields listed in fields_config.SOURCE_FIELDS for
    this URL's domain, resetting every other key in `business` to its
    empty value (and dropping any key that isn't part of the common
    schema at all, e.g. one-off extras like "Zeemaps Extra Fields").

    If the domain isn't found in fields_config.SOURCE_FIELDS, `business`
    is returned unchanged.
    """

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


# ==========================================================
# Dispatcher
# ==========================================================

# domain -> (fetch method, parser function)
# fetch method "api" means the parser makes its own requests calls and
# doesn't need pre-fetched HTML at all.
SITE_PARSERS = {
    "nearfinderus.com": ("requests", parse_nearfinderus),
    "smallbusinessusa.com": ("playwright", parse_smallbusinessusa),
    "zeemaps.com": ("api", parse_zeemaps),
    "callupcontact.com": ("requests", parse_callupcontact),
    "zumvu.com": ("playwright", parse_zumvu),
    # blinx.biz's business record loads via a client-side XHR call made
    # AFTER the initial page load (confirmed via the browser Network
    # tab -- it's not in the raw HTML or __NEXT_DATA__ that a plain
    # requests.get() would see), so this needs Playwright to let the
    # page hydrate and populate the DOM before we read it.
    "blinx.biz": ("playwright", parse_blinx),
    "place123.net": ("requests", parse_place123),
    "freelistingusa.com": ("requests", parse_freelistingusa),
}


def extract_business(url, worker_path="playwright_worker.py"):

    domain = urlparse(url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]

    matched = next((k for k in SITE_PARSERS if k in domain), None)

    if matched:
        method, parser = SITE_PARSERS[matched]
    else:
        method, parser = "requests", parse_generic

    if method == "api":
        # Parser drives its own requests calls; no HTML fetch needed.
        business = parser(url)
        if isinstance(business, list):
            return [filter_business_fields(record, url) for record in business]
        return filter_business_fields(business, url)

    if method == "requests":
        try:
            html = fetch_via_requests(url)
            blocked = _looks_blocked(html)
        except requests.exceptions.RequestException:
            # Outright HTTP failure (401/403/etc, not just a 200 with
            # bot-check text) -- also worth a Playwright retry rather
            # than failing the whole extraction.
            html = None
            blocked = True

        if blocked:
            # Unmapped/blocked site -- retry via Playwright automatically
            html = fetch_via_playwright(url, worker_path=worker_path)
    else:
        html = fetch_via_playwright(url, worker_path=worker_path)

    # A Cloudflare error page can come back as a "successful" fetch
    # (see _looks_like_cloudflare_error above) -- catch it here, before
    # handing it to the parser, rather than silently returning an
    # empty/garbage record.
    if _looks_like_cloudflare_error(html):
        raise RuntimeError(
            f"Fetch for {url} returned a Cloudflare error page "
            f"(origin server appears to be down or unreachable), "
            f"not the real page content."
        )

    business = parser(url, html)

    # zeemaps' "api" branch above already returns early, but its parser
    # can still return a list from other call paths (kept defensive here).
    if isinstance(business, list):
        return [filter_business_fields(record, url) for record in business]

    return filter_business_fields(business, url)


if __name__ == "__main__":

    urls = sys.argv[1:] or [
        "https://us.enrollbusiness.com/BusinessProfile/7823462/HAQQ-Legal-AI-Dover-DE-19901/Home",
        "https://nearfinderus.com/en/business/fl/nokomis/category_water-damage-restoration/wrightway-emergency-services_21911037+0.html",
        "https://smallbusinessusa.com/listing/wrightway-emergency-services-6a1ac1527f9a5.html",
        "https://www.zeemaps.com/map/ombxa?group=7085104",
        "https://www.callupcontact.com/b/businessprofile/WrightWay_Emergency_Services/10109082",
        "https://www.zumvu.com/haqqlegalai/",
        "https://www.blinx.biz/haqq-legal-ai",
        "http://www.place123.net/place/haqq-legal-ai---united-states",
        "https://www.freelistingusa.com/listings/haqq-legal-ai",
        "https://www.earthmom.org/legal/haqq-legal-ai",
    ]

    for url in urls:
        print("=" * 100)
        print(f"URL: {url}")
        print("=" * 100)

        try:
            data = extract_business(url)
        except Exception as e:
            print(f"ERROR extracting {url}: {e}")
            print("-" * 80)
            continue

        # zeemaps parser can return a list if a map has multiple markers
        records = data if isinstance(data, list) else [data]

        for record in records:
            for key, value in record.items():
                print(f"{key}:")
                print(value)
                print("-" * 80)
