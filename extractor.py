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
from bs4 import BeautifulSoup, NavigableString
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


# ==========================================================
# Fetchers
# ==========================================================

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
    last_line = [line for line in stdout.splitlines() if line.strip()][-1]
    data = json.loads(last_line)

    if not data.get("success"):
        raise RuntimeError(f"Playwright fetch failed: {data.get('debug')}")

    return data["html"]


# ==========================================================
# Site parser: bpublic.com
# ==========================================================

def _bpublic_field(soup, field_name):
    """Text of the value span/cell inside a div.table-display-<field_name>
    row, or "" if that row isn't present on this listing."""
    el = soup.select_one(f".table-display-{field_name} .col-sm-8 span") \
        or soup.select_one(f".table-display-{field_name} .col-sm-8")
    return clean(el.get_text()) if el else ""


def _bpublic_clean_value(text):
    text = clean(text)
    if not text or text.upper() == "N/A":
        return ""
    return text


def _parse_bpublic_about_block(business, about_el, url):
    """Some bPUBLIC listings (e.g. "Focal") don't fill in the structured
    address/phone/website rows at all -- instead everything is packed as
    "Label:" / value paragraph pairs inside the free-text "About" box.
    Pull Address/Phone/Website out of that pattern and treat any
    remaining paragraphs as the Description."""
    paragraphs = about_el.find_all("p")
    desc_parts = []
    i = 0
    while i < len(paragraphs):
        label = clean(paragraphs[i].get_text())

        if label == "Address:" and i + 1 < len(paragraphs):
            address_text = _bpublic_clean_value(paragraphs[i + 1].get_text())
            if address_text and not business["Street"]:
                zip_match = re.search(r"(\d{5}(?:-\d{4})?)\s*$", address_text)
                if zip_match and not business["Zipcode"]:
                    business["Zipcode"] = zip_match.group(1)
                street = address_text
                if business["City"]:
                    idx = address_text.lower().find(business["City"].lower())
                    if idx > 0:
                        street = address_text[:idx].rstrip(", ").strip()
                business["Street"] = street
            i += 2
            continue

        if label == "Phone:" and i + 1 < len(paragraphs):
            phone_text = _bpublic_clean_value(paragraphs[i + 1].get_text())
            if phone_text and not business["Phone"]:
                business["Phone"] = phone_text
            i += 2
            continue

        if label == "Website:" and i + 1 < len(paragraphs):
            link = paragraphs[i + 1].find("a", href=True)
            website_text = link["href"].strip() if link else _bpublic_clean_value(paragraphs[i + 1].get_text())
            if website_text and not business["Website URL"]:
                business["Website URL"] = urljoin(url, website_text)
            i += 2
            continue

        if label == "About Us:":
            i += 1
            continue

        text = clean(paragraphs[i].get_text())
        if is_meaningful(text) and text not in ("Address:", "Phone:", "Website:", "About Us:"):
            desc_parts.append(text)
        i += 1

    if desc_parts and not business["Description"]:
        business["Description"] = "\n\n".join(desc_parts)


def parse_bpublic(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    company_el = soup.select_one(".table-display-company .textbox-company")
    if company_el:
        business["Business Name"] = clean(company_el.get_text())
    if not business["Business Name"]:
        h1 = soup.select_one(".header-member-name h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Category (badge under the name, e.g. "Professional Services") ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        business["Category"] = clean(category_el.get_text())

    # ---- Structured address rows (when a listing fills them in) ----
    business["Street"] = _bpublic_field(soup, "address1") or _bpublic_field(soup, "street")
    business["City"] = _bpublic_field(soup, "city")
    business["State"] = _bpublic_field(soup, "state_ln") or _bpublic_field(soup, "state")
    business["Zipcode"] = _bpublic_field(soup, "zip_code") or _bpublic_field(soup, "zipcode")
    business["Country"] = _bpublic_field(soup, "country_ln") or _bpublic_field(soup, "country")

    # ---- Country / State / City / Category fallback: breadcrumb trail
    #      (Home > Country > State > City > Category) ----
    crumbs = [clean(s.get_text()) for s in soup.select(".breadcrumb span[itemprop='name']")]
    if crumbs and crumbs[0].lower() == "home":
        crumbs = crumbs[1:]
    if len(crumbs) >= 1 and not business["Country"]:
        business["Country"] = crumbs[0]
    if len(crumbs) >= 2 and not business["State"]:
        business["State"] = crumbs[1]
    if len(crumbs) >= 3 and not business["City"]:
        business["City"] = crumbs[2]
    if len(crumbs) >= 4 and not business["Category"]:
        business["Category"] = crumbs[3]

    # ---- Phone (structured row, tel: link, or reveal-on-click header) ----
    phone_el = soup.select_one(".table-display-phone_number .phone") \
        or soup.select_one(".table-display-phone .phone")
    if phone_el:
        business["Phone"] = clean(phone_el.get_text())
    if not business["Phone"]:
        phone_header = soup.select_one(".phone_number_header")
        if phone_header:
            business["Phone"] = clean(phone_header.get_text())
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = clean(tel["href"].replace("tel:", ""))

    # ---- Website URL (structured row) ----
    website_el = soup.select_one(".table-display-website a[href]") \
        or soup.select_one(".table-display-website .weblink[href]")
    if website_el:
        business["Website URL"] = website_el["href"]

    # ---- Hours (structured row, when a listing has one) ----
    hours_el = soup.select_one(".table-display-hours")
    if hours_el:
        business["Hours"] = clean(hours_el.get_text())

    # ---- Description + Address/Phone/Website fallback: the "About" box.
    #      On many listings this is just free-text description, but on
    #      some (e.g. "Focal") it also carries Address/Phone/Website as
    #      label/value paragraph pairs when the structured rows above
    #      were left empty. ----
    about_el = soup.select_one(".table-display-about_me .froala-data") \
        or soup.select_one(".field-about_me")
    if about_el:
        paragraphs = [clean(p.get_text()) for p in about_el.find_all("p")]
        paragraphs = [p for p in paragraphs if p]
        has_labels = any(p in ("Address:", "Phone:", "Website:", "About Us:") for p in paragraphs)

        if has_labels:
            _parse_bpublic_about_block(business, about_el, url)
        elif paragraphs:
            business["Description"] = "\n".join(paragraphs)

    if not business["Description"]:
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            business["Description"] = clean(meta["content"])

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img")
    if logo_el and logo_el.get("src"):
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

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
    region_is_state = _looks_like_us_state(region_val)
    locality_is_state = _looks_like_us_state(locality_val)

    if region_is_state and not locality_is_state:
        # Normal, non-swapped order: locality is city, region is state.
        return locality_val, region_val

    if locality_is_state and not region_is_state:
        # Swapped on this listing: region is city, locality is state.
        return region_val, locality_val

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

            if obj.get("telephone") and not business["Phone"]:
                business["Phone"] = obj["telephone"]

            addr = obj.get("address", {})

            if not business["Street"]:
                business["Street"] = addr.get("streetAddress", "")
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

    # ---- Phone fallback (tel: link) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

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

        # ---- Address fallback: some ZeeMaps groups never populate ----
        if business["Street"] and not business["City"] and not business["State"]:
            street, city, state, zipcode = _split_blinx_address(business["Street"])
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            if not business["Zipcode"]:
                business["Zipcode"] = zipcode

        # ---- Custom fields, resolved generically by name ----
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

        if not business["Description"]:
            business["Description"] = map_about

        # ---- Photo (embedded as an <img> tag inside the "i" field) ----
        img_html = detail.get("i", "")
        if img_html:
            img_match = re.search(r"src=['\"]([^'\"]+)['\"]", img_html)
            if img_match:
                business["Logo"] = img_match.group(1)

        results.append(business)

    if not results:
        return empty_business()
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

def _value_after_heading(soup, label, separator=" "):
    return _value_by_label(soup, label, separator=separator)


def parse_callupcontact(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name (page <h1>) ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

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

    # ---- Hours ----
    hours = _value_after_heading(soup, "Hours") or _value_after_heading(soup, "Business Hours")
    if hours:
        business["Hours"] = hours

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
            elif "fa-globe" in icon_classes:
                business["Website URL"] = a["href"]

    # ---- Hours (same icon-list block as phone/website, keyed off a clock icon) ----
    if contact_ul:
        for li in contact_ul.find_all("li"):
            icon = li.find("i")
            if not icon:
                continue
            icon_classes = icon.get("class", [])
            if "fa-clock" in icon_classes or "fa-clock-o" in icon_classes:
                text = clean(li.get_text())
                if text:
                    business["Hours"] = text

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

    # ---- Category (breadcrumb fallback) ----
    if not business["Category"]:
        crumbs = [clean(a.get_text()) for a in soup.select("ul.breadcrumb a, .breadcrumb a")]
        crumbs = [c for c in crumbs if c and c.lower() != "home"]
        if crumbs:
            business["Category"] = crumbs[-1]

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

    match = re.match(r"^(.*?)\s+([\w-]*\d[\w-]*)$", state_zip.strip())
    if match:
        state = match.group(1).strip()
        zipcode = match.group(2).strip()
    else:
        state = state_zip.strip()

    return street, city, state, zipcode


_BLINX_RENDERED_ADDRESS_RE = re.compile(
    r"^(?P<street>.+?),\s*(?P<city>[^,]+?),\s*(?P<state>[A-Za-z]{2,})\s*,?\s*(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _extract_blinx_address_from_dom(soup):

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

        is_social = any(domain in href.lower() for domain in SOCIAL_DOMAINS)

        if not is_social and not business["Website URL"]:
            business["Website URL"] = href


def parse_blinx(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Primary source: Next.js __NEXT_DATA__ hydration payload ----
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

        logo = record.get("logo") or record.get("image")
        if logo:
            business["Logo"] = urljoin(url, logo)

        _blinx_links_to_business(business, record.get("links"))

        # The API's "address" field is only ever the bare street (e.g.
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
    # fetched via plain requests).
    for a in soup.find_all("a", href=True):
        href = a["href"]

        if not href.startswith("http"):
            continue
        if "blinx.biz" in href.lower():
            continue
        if "google.com/maps" in href.lower() or _is_maps_link(href):
            continue

        is_social = any(domain in href.lower() for domain in SOCIAL_DOMAINS)

        if not is_social and not business["Website URL"]:
            business["Website URL"] = href

    return business


# ==========================================================
# Site parser: place123.net
# ==========================================================

_PLACE123_LABELS = {
    "owner name": "Owner Name",
    "phone": "Phone",
    "website": "Website URL",
    "url": "Website URL",
    "business email": "Business Email",
    "about us": "Description",
    "related searches": "Keywords",
    "hours": "Hours",
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

    # ---- Whole-page text as lines  ----
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
        business["Logo"] = photo_link["href"]
    else:
        photo_img = soup.select_one('img[src*="freelistingusa.s3"]')
        if photo_img and photo_img.get("src"):
            business["Logo"] = urljoin(url, photo_img["src"])

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Social Media (dedicated #listing-follow block --
    follow_block = soup.select_one("#listing-follow")
    if follow_block:
        for a in follow_block.find_all("a", href=True):
            href = a["href"]
            for domain, network in SOCIAL_DOMAINS.items():
                if domain in href.lower():
                    business["Social Media Links"][network] = href

    return business



# ==========================================================
# Site parser: askmap.net
# ==========================================================

def _askmap_section_container(soup, header_text):
    for h3 in soup.find_all("h3"):
        if clean(h3.get_text()).lower() == header_text.strip().lower():
            return h3.parent
    return None


def parse_askmap(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (visible <h1>, falls back to og:title) ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"]).split("|")[0].strip()

    # ---- Category ("<b>Category</b>: <span>value</span>" --
    for b_tag in soup.find_all("b"):
        if clean(b_tag.get_text()).lower() == "category":
            value_tag = b_tag.find_next_sibling()
            if value_tag:
                business["Category"] = clean(value_tag.get_text())
            break

    # ---- Address details ----
    address_container = _askmap_section_container(soup, "Address details")
    if address_container:
        address_tag = address_container.find("address")
        if address_tag:
            address_text = clean(address_tag.get_text(separator=" "))
            if address_text:
                street, city, state, zipcode = _split_blinx_address(address_text)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode

    # ---- Phone & WWW ----
    contact_container = _askmap_section_container(soup, "Phone & WWW")
    if contact_container:
        tel = contact_container.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()
        else:
            phone_match = re.search(
                r"[\d][\d\-.\s()]{6,}\d", clean(contact_container.get_text())
            )
            if phone_match:
                business["Phone"] = clean(phone_match.group())

        for a in contact_container.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                continue
            if "askmap.net" in href.lower():
                continue
            if any(domain in href.lower() for domain in SOCIAL_DOMAINS):
                continue
            business["Website URL"] = href
            break

    # ---- Business hours (own <div>; blank for many listings -- 
    hours_container = _askmap_section_container(soup, "Business hours")
    if hours_container:
        hours_copy = BeautifulSoup(str(hours_container), "lxml")
        heading = hours_copy.find("h3")
        if heading:
            heading.decompose()
        pieces = [clean(s) for s in hours_copy.find_all(string=True)]
        pieces = [p for p in pieces if p]
        hours_text = "; ".join(pieces)
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Description ----
    info_container = _askmap_section_container(soup, "Info")
    if info_container:
        info_copy = BeautifulSoup(str(info_container), "lxml")
        heading = info_copy.find("h3")
        if heading:
            heading.decompose()
        desc_text = clean(info_copy.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords (meta keywords tag) ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            business["Keywords"] = clean(kw_raw)

    # ---- Logo (og:image -- matches the listing logo shown top-left) ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])

    return business


# ==========================================================
# Site parser: earthmom.org
# ==========================================================

_EARTHMOM_LABEL_MAP = {
    "phone": "Phone",
    "website": "Website URL",
}

_EARTHMOM_ABOUT_HEADINGS = {"about us", "about", "about company", "about the company"}


def _parse_earthmom_about_block(container):
    result = {}
    description_lines = []

    paragraphs = [clean(p.get_text(separator=" ")) for p in container.find_all("p")]
    n = len(paragraphs)

    i = 0
    while i < n:
        text = paragraphs[i]
        if not text:
            i += 1
            continue

        label_key = text.rstrip(":").strip().lower()

        if label_key in _EARTHMOM_LABEL_MAP and i + 1 < n and paragraphs[i + 1]:
            result[_EARTHMOM_LABEL_MAP[label_key]] = paragraphs[i + 1]
            i += 2
            continue

        if label_key in _EARTHMOM_ABOUT_HEADINGS:
            i += 1
            continue

        description_lines.append(text)
        i += 1

    if description_lines:
        result["Description"] = "\n".join(description_lines)

    return result


def parse_earthmom(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (visible <h1>, falls back to og:title split on
    #      " on " since the template renders it as "<Name> on Earth Mom") ----
    h1 = soup.select_one(".header-member-name h1") or soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"]).split(" on ")[0].strip()

    # ---- Category (short line directly under the name) ----
    category_tag = soup.select_one(".profile-header-top-category")
    if category_tag:
        business["Category"] = clean(category_tag.get_text())

    # ---- Address ----
    address_tag = soup.select_one('[itemprop="streetAddress"]')
    if address_tag:
        address_text = clean(address_tag.get_text(separator=" "))
        if address_text:
            street, city, state, zipcode = _split_blinx_address(address_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    # ---- Phone / Website / Business Email / Description ----
    about_container = soup.select_one(".overview-tab-about-me .textarea-about_me")
    if about_container:
        about_fields = _parse_earthmom_about_block(about_container)
        for field, value in about_fields.items():
            if is_meaningful(value):
                business[field] = value

    # ---- Description fallback (meta description, SEO-truncated) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Phone fallback (tel: link, if the free-form block didn't
    #      have one) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Country (same itemprop convention as the street address) ----
    country_tag = soup.select_one('[itemprop="addressCountry"]')
    if country_tag:
        business["Country"] = clean(country_tag.get_text())

    # ---- Hours ----
    hours_tag = soup.select_one('[itemprop="openingHours"]') or soup.select_one(".business-hours")
    if hours_tag:
        hours_text = clean(hours_tag.get_text(separator=" "))
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Social Media / GBP Link (external anchors, scanned like the
    #      other site parsers) ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "earthmom.org" in href.lower():
            continue
        if _is_maps_link(href):
            if not business["GBP Link"]:
                business["GBP Link"] = href
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])
    else:
        profile_img = soup.select_one(".profile-image img")
        if profile_img and profile_img.get("src"):
            business["Logo"] = urljoin(url, profile_img["src"])

    return business


# ==========================================================
# Site parser: gravitysplash.com
# ==========================================================
def _gravitysplash_sidebar_value(soup, li_class):

    li = soup.select_one(f"li.{li_class}")
    if not li:
        return None
    spans = li.find_all("span")
    if not spans:
        return None
    return clean(spans[-1].get_text())


def parse_gravitysplash(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".post-meta-left-box h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Category ----
    breadcrumb_links = soup.select(".breadcrumbs li a")
    if len(breadcrumb_links) >= 2:
        business["Category"] = clean(breadcrumb_links[1].get_text())

    # ---- Description (full write-up) ----
    desc_container = soup.select_one(".post-detail-content")
    if desc_container:
        desc_text = clean(desc_container.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Address ----
    address_text = _gravitysplash_sidebar_value(soup, "lp-details-address")
    if address_text:
        street, city, state, zipcode = _split_blinx_address(address_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone  ----
    phone_link = soup.select_one("li.lp-listing-phone a[href^='tel:']")
    if phone_link:
        business["Phone"] = phone_link["href"].replace("tel:", "").strip()
    else:
        phone_text = _gravitysplash_sidebar_value(soup, "lp-listing-phone")
        if phone_text:
            business["Phone"] = phone_text

    # ---- Website URL ----
    website_link = soup.select_one("li.lp-user-web a[href]")
    if website_link:
        business["Website URL"] = website_link["href"]

    # ---- Social Media Links ----
    contact_list = None
    for li_class in ("lp-user-web", "lp-listing-phone", "lp-details-address"):
        anchor_li = soup.select_one(f"li.{li_class}")
        if anchor_li:
            contact_list = anchor_li.find_parent("ul")
            if contact_list:
                break

    if contact_list:
        social_list = contact_list.find_next_sibling("ul")
        if social_list:
            for a in social_list.find_all("a", href=True):
                href = a["href"]
                for domain, network in SOCIAL_DOMAINS.items():
                    if domain in href.lower():
                        business["Social Media Links"][network] = href

    # ---- Fallbacks from the embedded LocalBusiness JSON-LD, only for
    #      whichever fields the sidebar didn't already fill in ----
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("@type") != "LocalBusiness":
            continue
        if not business["Business Name"] and data.get("name"):
            business["Business Name"] = data["name"]
        if not business["Phone"] and data.get("telephone"):
            business["Phone"] = data["telephone"]
        break

    return business


# ==========================================================
# Site parser: webforcompany.com
# ==========================================================
_WEBFORCOMPANY_LABELS = {
    "business name": "Business Name",
    "owner name": "Owner Name",
    "phone": "Phone",
    "website": "Website URL",
    "business email": None,  # real value comes from _find_cf_email, not this text
    "about us": "Description",
    "related searches": "Keywords",
    "hours": "Hours",
    "business hours": "Hours",
}


def parse_webforcompany(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Logo (real per-business header image, when uploaded) ----
    logo_img = soup.select_one(".navbar-brand img")
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])

    # ---- Locate the label/value block (homepage shape, then about.php shape) ----
    scope = soup.select_one(".about p")
    if not scope:
        scope = soup.select_one(".aboutus .col-md-12")
    if not scope:
        return business

    # ---- Website URL (real href, not the label's visible text) ----
    for a in scope.find_all("a", href=True):
        href = a["href"]
        if "cdn-cgi/l/email-protection" in href.lower():
            continue
        if href.startswith("http"):
            business["Website URL"] = href
            break

    # ---- Business Email (Cloudflare-obfuscated placeholder text) ----
    email = _find_cf_email(scope)
    if email:
        business["Business Email"] = email

    # ---- Flat label-then-value scan for everything else ----
    lines = [clean(line) for line in scope.get_text(separator="\n").split("\n")]
    lines = [l for l in lines if l]
    label_keys = set(_WEBFORCOMPANY_LABELS.keys())

    i, n = 0, len(lines)
    while i < n:
        norm = lines[i].rstrip(":").strip().lower()

        if norm == "address":
            if i + 1 < n:
                street, city, state, zipcode = _split_blinx_address(lines[i + 1])
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode
            i += 2
            continue

        if norm in label_keys:
            field = _WEBFORCOMPANY_LABELS[norm]

            j = i + 1
            value_lines = []
            while j < n:
                next_norm = lines[j].rstrip(":").strip().lower()
                if next_norm in label_keys or next_norm == "address":
                    break
                value_lines.append(lines[j])
                j += 1

            value = clean(" ".join(value_lines))
            if field and value:
                business[field] = value

            i = j
        else:
            i += 1

    # ---- Social Media Links / GBP Link (page-wide anchor scan) ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "webforcompany.com" in href.lower():
            continue
        if _is_maps_link(href):
            if not business["GBP Link"]:
                business["GBP Link"] = href
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# Site parser: provenexpert.com
# ==========================================================
def parse_provenexpert(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- JSON-LD (Name, Logo, Street/City/Zipcode/Country, Phone) ----
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

            if obj.get("name"):
                business["Business Name"] = obj["name"]

            image = obj.get("image")
            if isinstance(image, dict) and image.get("url"):
                business["Logo"] = image["url"]
            elif isinstance(image, str) and image:
                business["Logo"] = image

            addr = obj.get("address", {})
            if addr.get("streetAddress"):
                business["Street"] = addr["streetAddress"]
            if addr.get("addressLocality"):
                business["City"] = addr["addressLocality"]
            if addr.get("postalCode"):
                business["Zipcode"] = addr["postalCode"]
            if addr.get("addressCountry"):
                business["Country"] = addr["addressCountry"]

            if obj.get("telephone"):
                business["Phone"] = obj["telephone"]

    # ---- Business Name fallback (visible <h1>) ----
    if not business["Business Name"]:
        h1 = soup.select_one("h1.profileName")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Category (tagline directly under the business name) ----
    job = soup.select_one("h2.profileJob")
    if job:
        business["Category"] = clean(job.get_text())

    # ---- Keywords  ----
    tags = [clean(t.get_text()) for t in soup.select("#offerTagsPublic .peTagPill")]
    tags = [t for t in tags if t]
    if tags:
        business["Keywords"] = ", ".join(tags)

    # ---- Description (About text, incl. the CSS-hidden continuation) ----
    welcome = soup.select_one("#welcomeTextPublic")
    if welcome:
        for junk in welcome.select(".textEtc, .collapseAboutme, #offerTags"):
            junk.decompose()
        text = clean(welcome.get_text(separator=" "))
        if is_meaningful(text):
            business["Description"] = text

    # ---- Contact box: State (JSON-LD doesn't have it), Phone, Email ----
    contact = soup.select_one("#personalPublic")
    if contact:
        address_tag = contact.select_one("address")
        if address_tag:
            lines = [clean(l) for l in address_tag.get_text(separator="\n").split("\n")]
            lines = [l for l in lines if l]
            if len(lines) >= 3 and not business["State"]:
                business["State"] = re.sub(r"\s*\([A-Za-z]{2,3}\)\s*$", "", lines[2]).strip()
            if len(lines) >= 4 and not business["Zipcode"]:
                business["Zipcode"] = lines[3]
            if len(lines) >= 5 and not business["Country"]:
                business["Country"] = lines[4]

        # ---- Owner Name ("Contact person" label, with the name itself
        #      sitting as a bare text node right after a <br> rather than
        #      inside its own tag) ----
        for strong in contact.find_all("strong"):
            if clean(strong.get_text()).lower() != "contact person":
                continue

            owner_name = ""
            node = strong.next_sibling
            while node is not None:
                if isinstance(node, NavigableString):
                    text = clean(str(node))
                    if text:
                        owner_name = text
                        break
                elif getattr(node, "name", None) != "br":
                    break
                node = node.next_sibling

            if owner_name:
                business["Owner Name"] = owner_name
            break

        tel = contact.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

        # mailto hrefs here carry a "?Subject=..." query string -- strip it.
        email = contact.select_one('a[href^="mailto:"]')
        if email:
            business["Business Email"] = email["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Website URL ("Websites" box) ----
    website_link = soup.select_one("#profilesPublic a[href^='http']")
    if website_link:
        business["Website URL"] = website_link["href"]

    # ---- Social Media Links / GBP Link (anchors across the profile links box) ----
    for a in soup.select("#profilesPublic a[href^='http'], #personalPublic a[href^='http']"):
        href = a["href"]
        if _is_maps_link(href):
            if not business["GBP Link"]:
                business["GBP Link"] = href
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    # ---- Hours ----
    hours_tag = soup.select_one('[itemprop="openingHours"]') or soup.select_one(".openingHours")
    if hours_tag:
        hours_text = clean(hours_tag.get_text(separator=" "))
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Photos (profile gallery, if present) ----
    gallery_imgs = soup.select(".peGallery img, .profileGallery img")
    photos = []
    for img in gallery_imgs:
        src = img.get("src")
        if src:
            src = urljoin(url, src)
            if src not in photos:
                photos.append(src)
    if photos:
        business["Photos"] = photos

    return business


# ==========================================================
# Site parser: zipleaf.us
# ==========================================================

ZIPLEAF_SHARE_LINK_SIGNALS = [
    "sharer.php", "intent/tweet", "share-offsite", "pin/create/button",
]


def parse_zipleaf(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- JSON-LD (primary source: name, address, phone, logo, description) ----
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

            if obj.get("description"):
                business["Description"] = clean(obj["description"])

            if obj.get("image") and not business["Logo"]:
                business["Logo"] = urljoin(url, obj["image"])

            if obj.get("telephone") and not business["Phone"]:
                business["Phone"] = obj["telephone"]

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

    # ---- Business Name fallback (visible listing title) ----
    if not business["Business Name"]:
        title = soup.select_one("h3.card-title span")
        if title:
            business["Business Name"] = clean(title.get_text())

    main_card = soup.select_one("div.listing-contact-info") or soup

    # ---- Website URL (visible text of the site link, not its redirect href) ----
    website_link = main_card.select_one('a[href^="/GoToWebsite/"], a[href*="/GoToWebsite/"]')
    if website_link:
        site_text = clean(website_link.get_text())
        if site_text:
            business["Website URL"] = site_text
        elif website_link.get("href"):
            business["Website URL"] = urljoin(url, website_link["href"])

    # ---- Phone fallback (tel: link) ----
    if not business["Phone"]:
        tel = main_card.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Business Email (mailto: link, if present) ----
    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Description fallback (meta description) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            business["Keywords"] = clean(kw_raw)

    if not business["Keywords"]:
        product_tags = [clean(a.get_text()) for a in soup.select("a.product-link")]
        product_tags = [t for t in product_tags if t]
        if product_tags:
            business["Keywords"] = ", ".join(product_tags)

    # ---- Logo fallback (listing photo / og:image) ----
    if not business["Logo"]:
        logo_img = soup.select_one("#business-logo img[src]")
        if logo_img:
            business["Logo"] = urljoin(url, logo_img["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Category (breadcrumb, minus Home / location / listing-name crumbs) ----
    crumbs = [clean(li.get_text()) for li in soup.select("ol.breadcrumb li.breadcrumb-item")]
    skip = {"home", (business["Business Name"] or "").lower()}
    category_crumbs = [c for c in crumbs if c and c.lower() not in skip]
    if category_crumbs:
        business["Category"] = ", ".join(category_crumbs)

    # ---- GBP Link (a Google Maps / Business Profile link, if present) ----
    gbp_link = soup.select_one('a[href*="google.com/maps"], a[href*="g.page"], a[href*="goo.gl/maps"]')
    if gbp_link and gbp_link.get("href"):
        business["GBP Link"] = gbp_link["href"]

    # ---- Hours ----
    hours_tag = soup.select_one('[itemprop="openingHours"]') or soup.select_one(".listing-hours, .business-hours")
    if hours_tag:
        hours_text = clean(hours_tag.get_text(separator=" "))
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Social Media Links ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(sig in href.lower() for sig in ZIPLEAF_SHARE_LINK_SIGNALS):
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business

# ==========================================================
# Site parser: cataloxy.us
# ==========================================================
def parse_cataloxy(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- JSON-LD----
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

            if obj.get("telephone") and not business["Phone"]:
                business["Phone"] = obj["telephone"]

            addr = obj.get("address", {})
            if not business["Street"]:
                business["Street"] = addr.get("streetAddress", "")
            if not business["City"]:
                business["City"] = addr.get("addressLocality", "")
            if not business["State"]:
                business["State"] = addr.get("addressRegion", "")
            if not business["Country"]:
                business["Country"] = addr.get("addressCountry", "")

    # ---- Business Name fallback (visible <h1 class="firms">) ----
    if not business["Business Name"]:
        h1 = soup.select_one("h1.firms")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Address microdata (primary source -- has the zip code) ----
    addr_block = soup.select_one('span[itemprop="address"]')
    if addr_block:
        street = addr_block.select_one('[itemprop="streetAddress"]')
        if street:
            business["Street"] = clean(street.get_text())
        zipcode = addr_block.select_one('[itemprop="postalCode"]')
        if zipcode:
            business["Zipcode"] = clean(zipcode.get_text())
        city = addr_block.select_one('[itemprop="addressLocality"]')
        if city:
            business["City"] = clean(city.get_text())
        state = addr_block.select_one('[itemprop="addressRegion"]')
        if state:
            business["State"] = clean(state.get_text())
        country = addr_block.select_one('[itemprop="addressCountry"]')
        if country:
            business["Country"] = country.get("content") or clean(country.get_text())

    # ---- Phone fallback (tel: link) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Website URL  ----
    site_link = soup.select_one("a.firmDomain")
    if site_link:
        if site_link.get("title"):
            business["Website URL"] = site_link["title"]
        else:
            business["Website URL"] = clean(site_link.get_text())

    # ---- Business Email ----
    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Description (itemprop="description" paragraph) ----
    desc_el = soup.select_one('[itemprop="description"]')
    if desc_el:
        desc = clean_multiline(desc_el.decode_contents())
        if is_meaningful(desc):
            business["Description"] = desc
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            business["Keywords"] = clean(kw_raw)
    if not business["Keywords"]:
        kw_links = [clean(a.get_text()) for a in soup.select('a[href*="/firms/kw/"]')]
        kw_links = [k for k in kw_links if k]
        if kw_links:
            business["Keywords"] = ", ".join(kw_links)

    # ---- Category ----
    crumb_names = [
        clean(span.get_text())
        for span in soup.select('#top_navigator span[itemprop="name"]')
    ]
    if crumb_names:
        business["Category"] = crumb_names[-1]

    # ---- Logo ----
    logo_el = soup.select_one('span[itemprop="logo"]')
    if logo_el and is_meaningful(logo_el.get_text()):
        business["Logo"] = urljoin(url, clean(logo_el.get_text()))
    if not business["Logo"]:
        logo_img = soup.select_one(".firm-top-panel__logo img[src]")
        if logo_img:
            business["Logo"] = urljoin(url, logo_img["src"])
    if not business["Logo"]:
        logo_img = soup.select_one("img.logo[src]")
        if logo_img:
            business["Logo"] = urljoin(url, logo_img["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "js-native-share" in (a.get("class") or []):
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# fyple.com
# ==========================================================

def _fyple_label_value(soup, label_text):
    """fyple's Contact rows are a flat two-column layout:
        <div class="row">
            <div class="col-xs-12 col-sm-5"><strong>LABEL:</strong></div>
            <div class="col-xs-12 col-sm-7">VALUE</div>
        </div>
    The value div is a sibling of the LABEL div (which itself wraps
    the <strong>), not of the <strong> tag directly -- so this steps
    up to the label's parent before looking for the next sibling.
    """
    for strong in soup.find_all("strong"):
        if clean(strong.get_text()).rstrip(":").lower() == label_text.lower():
            label_cell = strong.parent
            value_cell = label_cell.find_next_sibling("div") if label_cell else None
            if value_cell:
                return clean(value_cell.get_text(separator=" "))
    return ""


def _fyple_section_heading(soup, heading_text):
    """Returns the <h3 class="comp_section_title"> tag whose text
    matches heading_text exactly (case-insensitive), or None."""
    for h3 in soup.find_all("h3"):
        if clean(h3.get_text()).lower() == heading_text.lower():
            return h3
    return None


def parse_fyple(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name  ----
    name_tag = soup.select_one('[itemtype*="LocalBusiness"] h1[itemprop="name"]')
    if name_tag:
        business["Business Name"] = clean(name_tag.get_text())

    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"]).split(" in ")[0].strip()

    # ---- Address  ----
    addr = soup.select_one('[itemprop="address"][itemtype*="PostalAddress"]')
    if addr:
        street = addr.find("span", itemprop="streetAddress")
        city = addr.find("span", itemprop="addressLocality")
        zipcode = addr.find("span", itemprop="postalCode")
        state = addr.find("span", itemprop="addressRegion")
        country = addr.find("span", itemprop="addressCountry")

        if street:
            business["Street"] = clean(street.get_text())
        if city:
            business["City"] = clean(city.get_text())
        if zipcode:
            business["Zipcode"] = clean(zipcode.get_text())
        if state:
            business["State"] = clean(state.get_text())
        if country:
            business["Country"] = clean(country.get_text())

    # ---- Phone number ("Phone number:" label/value row) ----
    phone = _fyple_label_value(soup, "Phone number")
    if phone:
        business["Phone"] = phone

    # ---- Hours ----
    hours_container = soup.find("div", id="OpenHoursCollapse")
    if hours_container:
        cells = [clean(c.get_text()) for c in hours_container.find_all("div", recursive=False)]
        cells = [c for c in cells if c]
        pairs = [f"{cells[i]}: {cells[i + 1]}" for i in range(0, len(cells) - 1, 2)]
        hours_text = "; ".join(pairs)
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category ----
    cat_heading = _fyple_section_heading(soup, "Categories")
    if cat_heading:
        cat_container = cat_heading.find_next("div", class_="comp_wrap")
        if cat_container:
            cat_links = [clean(a.get_text()) for a in cat_container.find_all("a")]
            cat_links = [c for c in cat_links if c]
            if cat_links:
                business["Category"] = " > ".join(cat_links)

    # ---- Description  ----
    desc_heading = _fyple_section_heading(soup, "Company description")
    if desc_heading and desc_heading.parent:
        desc_copy = BeautifulSoup(str(desc_heading.parent), "lxml")
        heading_copy = desc_copy.find("h3")
        if heading_copy:
            heading_copy.decompose()
        desc_text = clean(desc_copy.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Photos + Logo ----
    photos = []
    logo_found = ""
    for a in soup.select('a[data-lightbox="images"][href]'):
        href = urljoin(url, a["href"])
        if not logo_found and re.search(r"logo", href, re.I):
            logo_found = href
        else:
            photos.append(href)

    if logo_found:
        business["Logo"] = logo_found
    business["Photos"] = photos

    # ---- Website URL / Business Email (same label/value row shape as Phone) ----
    website = _fyple_label_value(soup, "Website")
    if website:
        business["Website URL"] = website

    email = _fyple_label_value(soup, "Email address") or _fyple_label_value(soup, "Email")
    if email:
        business["Business Email"] = email

    # ---- Keywords (Tags section, same shape as Categories) ----
    kw_heading = _fyple_section_heading(soup, "Tags") or _fyple_section_heading(soup, "Keywords")
    if kw_heading:
        kw_container = kw_heading.find_next("div", class_="comp_wrap")
        if kw_container:
            kw_links = [clean(a.get_text()) for a in kw_container.find_all("a")]
            kw_links = [k for k in kw_links if k]
            if kw_links:
                business["Keywords"] = ", ".join(kw_links)

    # ---- Social Media Links / GBP Link ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "fyple.com" in href.lower():
            continue
        if _is_maps_link(href):
            if not business["GBP Link"]:
                business["GBP Link"] = href
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# merchantcircle.com
# ==========================================================


def parse_merchantcircle(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name  ----
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        business["Business Name"] = clean(og_title["content"])

    if not business["Business Name"]:
        h1 = soup.select_one("h1.business-info-title")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Address  ----
    meta_street = soup.find("meta", property="business:contact_data:street_address")
    if meta_street and meta_street.get("content"):
        business["Street"] = clean(meta_street["content"])

    meta_city = soup.find("meta", property="business:contact_data:locality")
    if meta_city and meta_city.get("content"):
        business["City"] = clean(meta_city["content"])
    if not business["City"]:
        city_tag = soup.select_one('span[itemprop="addressLocality"]')
        if city_tag:
            business["City"] = clean(city_tag.get_text()).rstrip(",")

    state_tag = soup.select_one('span[itemprop="addressRegion"]')
    if state_tag:
        business["State"] = clean(state_tag.get_text())

    meta_zip = soup.find("meta", property="business:contact_data:postal_code")
    if meta_zip and meta_zip.get("content"):
        business["Zipcode"] = clean(meta_zip["content"])
    if not business["Zipcode"]:
        zip_tag = soup.select_one('span[itemprop="postalCode"]')
        if zip_tag:
            business["Zipcode"] = clean(zip_tag.get_text())

    meta_country = soup.find("meta", property="business:contact_data:country_name")
    if meta_country and meta_country.get("content"):
        business["Country"] = clean(meta_country["content"])

    # ---- Phone ----
    meta_phone = soup.find("meta", property="business:contact_data:phone_number")
    if meta_phone and meta_phone.get("content"):
        business["Phone"] = clean(meta_phone["content"])
    if not business["Phone"]:
        phone_tag = soup.select_one('span[itemprop="telephone"]')
        if phone_tag:
            business["Phone"] = clean(phone_tag.get_text())

    # ---- Website URL ----
    meta_website = soup.find("meta", property="business:contact_data:website")
    if meta_website and meta_website.get("content"):
        business["Website URL"] = clean(meta_website["content"])
    if not business["Website URL"]:
        site_link = soup.select_one(".bi-list-item a.bi-list-item-text[href]")
        if site_link:
            business["Website URL"] = site_link["href"]

    # ---- Description  ----
    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        desc = clean(og_desc["content"])
        if is_meaningful(desc):
            business["Description"] = desc

    if not business["Description"]:
        desc_tag = soup.select_one("#business-description")
        if desc_tag:
            desc_copy = BeautifulSoup(str(desc_tag), "lxml")
            dots = desc_copy.find("span", class_="dots")
            if dots:
                dots.decompose()
            button = desc_copy.find("button")
            if button:
                button.decompose()
            desc_text = clean(desc_copy.get_text(separator=" "))
            if is_meaningful(desc_text):
                business["Description"] = desc_text

    # ---- Hours  ----
    hours_container = soup.select_one(".listing-location-hours ul")
    if hours_container:
        pairs = []
        for li in hours_container.find_all("li"):
            spans = li.find_all("span")
            if len(spans) < 2:
                continue
            day = clean(spans[0].get_text())
            value = clean(spans[-1].get_text())
            if day:
                pairs.append(f"{day}: {value}")
        hours_text = "; ".join(pairs)
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category  ----
    type_container = soup.select_one(".business-info-type")
    if type_container:
        full_text = type_container.get_text(separator=" ")
        if "\u2022" in full_text:
            full_text = full_text.split("\u2022", 1)[1]
        cats = [clean(c) for c in full_text.split(",")]
        cats = [c for c in cats if c]
        if cats:
            business["Category"] = ", ".join(cats)

    # ---- Logo  ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])
    if not business["Logo"]:
        avatar = soup.select_one(".business-info-avatar img[src]")
        if avatar:
            business["Logo"] = urljoin(url, avatar["src"])

    # ---- Social Media Links / GBP Link ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "merchantcircle.com" in href.lower():
            continue
        if _is_maps_link(href):
            if not business["GBP Link"]:
                business["GBP Link"] = href
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# globalbusinessdirectory.us
# ==========================================================
_GBD_REGION_CLASS_RE = re.compile(r"job_listing_region-([\w-]+)")


def parse_globalbusinessdirectory(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    name_tag = soup.select_one('h1.entry-title[itemprop="name"]')
    if name_tag:
        business["Business Name"] = clean(name_tag.get_text())

    if not business["Business Name"]:
        meta_title = soup.find("meta", itemprop="title")
        if meta_title and meta_title.get("content"):
            business["Business Name"] = clean(meta_title["content"])

    # ---- Address ----
    addr_tag = soup.select_one("a.google_map_link")
    if addr_tag:
        addr_text = clean(addr_tag.get_text())
        if addr_text:
            street, city, state, zipcode = _split_blinx_address(addr_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    # ---- Country  ----
    article = soup.select_one("article.job_listing")
    if article:
        region_match = _GBD_REGION_CLASS_RE.search(" ".join(article.get("class", [])))
        if region_match:
            business["Country"] = region_match.group(1).replace("-", " ").title()

    # ---- Phone  ----
    phone_tag = soup.select_one('[itemprop="telephone"]')
    if phone_tag:
        business["Phone"] = clean(phone_tag.get_text())

    # ---- Website URL ----
    site_link = soup.select_one("a.listing--website[href]")
    if site_link:
        business["Website URL"] = site_link["href"]

    # ---- Keywords  ----
    tagline = soup.select_one(".listing-tagline")
    if tagline:
        kw_text = clean(tagline.get_text())
        if is_meaningful(kw_text):
            business["Keywords"] = kw_text

    # ---- Description ----
    desc_tag = soup.select_one("#listing-description .box-inner p")
    if desc_tag:
        desc_text = clean(desc_tag.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Category  ----
    cat_links = [clean(a.get_text()) for a in soup.select(".listing-category a")]
    cat_links = [c for c in cat_links if c]
    if cat_links:
        business["Category"] = ", ".join(cat_links)

    # ---- Logo  ----
    logo_tag = soup.select_one(".listing-logo img")
    if logo_tag:
        logo_src = logo_tag.get("data-src") or logo_tag.get("src")
        if logo_src and not logo_src.startswith("data:"):
            business["Logo"] = urljoin(url, logo_src)

    # ---- Social Media Links ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "globalbusinessdirectory.us" in href.lower():
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# Site parser: listings.globalbusinessdirectory.us
# ==========================================================

def _listings_gbd_jsonld(soup):
    """Return the first LocalBusiness JSON-LD object on the page, if any."""
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue
        objects = data if isinstance(data, list) else [data]
        for obj in objects:
            if isinstance(obj, dict) and obj.get("@type") == "LocalBusiness":
                return obj
    return None


def parse_listings_globalbusinessdirectory(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    jsonld = _listings_gbd_jsonld(soup)

    # ---- Business Name ----
    name_tag = soup.select_one("h1.case27-primary-text")
    if name_tag:
        business["Business Name"] = clean(name_tag.get_text())
    if not business["Business Name"] and jsonld and jsonld.get("name"):
        business["Business Name"] = clean(jsonld["name"])

    # ---- Owner Name (rendered as the listing's "tagline") ----
    owner_tag = soup.select_one("h2.profile-tagline")
    if owner_tag:
        owner_text = clean(owner_tag.get_text())
        if is_meaningful(owner_text):
            business["Owner Name"] = owner_text

    # ---- Address (Street / City / State / Zipcode) ----
    addr_tag = soup.select_one(".map-block-address p")
    addr_text = clean(addr_tag.get_text()) if addr_tag else ""
    if not addr_text and jsonld:
        addr_obj = jsonld.get("address")
        if isinstance(addr_obj, dict) and addr_obj.get("address"):
            addr_text = clean(addr_obj["address"])
        elif isinstance(addr_obj, str):
            addr_text = clean(addr_obj)
    if addr_text:
        street, city, state, zipcode = _split_blinx_address(addr_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Country (rendered as a "Region" block) ----
    region_tag = soup.select_one(".block-type-terms .pf-body li a span")
    if region_tag:
        region_text = clean(region_tag.get_text())
        if is_meaningful(region_text):
            business["Country"] = region_text

    # ---- Contact Information block: Email / Phone / Website ----
    for li in soup.select(".block-type-details .pf-body li"):
        icon = li.find("i")
        value_tag = li.select_one("span.wp-editor-content")
        if not icon or not value_tag:
            continue
        icon_classes = icon.get("class", [])
        value_text = clean(value_tag.get_text())
        if not value_text:
            continue
        if "email" in icon_classes:
            business["Business Email"] = value_text
        elif "phone" in icon_classes:
            business["Phone"] = value_text
        elif "web" in icon_classes:
            business["Website URL"] = value_text

    if not business["Business Email"] and jsonld and jsonld.get("email"):
        business["Business Email"] = clean(jsonld["email"])
    if not business["Phone"] and jsonld and jsonld.get("telephone"):
        business["Phone"] = clean(jsonld["telephone"])

    # Fallback: the "Website" quick-action button near the top of the page
    # (icon class "fa-link", href to the business's own external site).
    if not business["Website URL"]:
        for a in soup.select(".lmb-calltoaction a[href], .quick-listing-actions a[href]"):
            href = a.get("href", "")
            if href.startswith("http") and a.find("i", class_="fa-link"):
                business["Website URL"] = href
                break

    # ---- Description ----
    desc_tag = soup.select_one(".block-type-text .pf-body p")
    if desc_tag:
        desc_text = clean(desc_tag.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text
    if not business["Description"] and jsonld and jsonld.get("description"):
        stripped = re.sub(r"<[^>]+>", " ", jsonld["description"])
        stripped = clean(stripped)
        if is_meaningful(stripped):
            business["Description"] = stripped

    # ---- Category ----
    cat_names = [clean(s.get_text()) for s in soup.select(".block-type-categories .category-name")]
    cat_names = [c for c in cat_names if c]
    if cat_names:
        business["Category"] = ", ".join(cat_names)

    # ---- Hours (theme renders this as its own block, when present) ----
    hours_tag = soup.select_one(".block-type-hours .pf-body, .block-type-business_hours .pf-body")
    if hours_tag:
        hours_text = clean_multiline(hours_tag.get_text(separator="\n"))
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Social Media Links ----
    content_scope = soup.select_one("#c27-single-listing") or soup
    own_domain = urlparse(url).netloc.lower().replace("www.", "")
    for a in content_scope.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if own_domain in href.lower():
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# chamberofcommerce.com
# ==========================================================
def parse_chamberofcommerce(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- JSON-LD (primary source: name, address, description, logo) ----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:

            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
                continue

            if obj.get("name"):
                business["Business Name"] = clean(obj["name"])

            if obj.get("description"):
                desc_text = clean(
                    BeautifulSoup(obj["description"], "lxml").get_text(separator=" ")
                )
                if is_meaningful(desc_text):
                    business["Description"] = desc_text

            if obj.get("image"):
                business["Logo"] = urljoin(url, obj["image"])

            addr = obj.get("address", {})
            if isinstance(addr, dict):
                business["Street"] = clean(addr.get("streetAddress", ""))
                business["City"] = clean(addr.get("addressLocality", ""))
                business["State"] = clean(addr.get("addressRegion", ""))
                business["Zipcode"] = clean(addr.get("postalCode", ""))
                business["Country"] = clean(addr.get("addressCountry", ""))

    # ---- Business Name fallback (visible H1) ----
    if not business["Business Name"]:
        h1 = soup.select_one("h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Address fallback----
    if not business["Street"]:
        addr1 = soup.select_one('span[selector-type="Address1"]')
        if addr1:
            street = clean(addr1.get_text())
            addr2 = soup.select_one('span[selector-type="Address2"]')
            if addr2:
                addr2_text = clean(addr2.get_text())
                if addr2_text:
                    street = f"{street}, {addr2_text}"
            business["Street"] = street

    if not business["City"]:
        city_tag = soup.select_one('span[selector-type="City"]')
        if city_tag:
            business["City"] = clean(city_tag.get_text()).rstrip(",")

    if not business["State"]:
        state_tag = soup.select_one('span[selector-type="State"]')
        if state_tag:
            business["State"] = clean(state_tag.get_text())

    if not business["Zipcode"]:
        zip_tag = soup.select_one('span[selector-type="Zip"]')
        if zip_tag:
            business["Zipcode"] = clean(zip_tag.get_text())

    if not business["Country"] and business["Street"]:
        business["Country"] = "US"

    # ---- Description  ----
    if not business["Description"]:
        about_card = None
        for heading in soup.select(".card-body h3.card-title"):
            if "about" in clean(heading.get_text()).lower():
                about_card = heading.find_parent("div", class_="card-body")
                break
        if about_card:
            card_copy = BeautifulSoup(str(about_card), "lxml")
            heading_copy = card_copy.find("h3")
            if heading_copy:
                heading_copy.decompose()
            desc_text = clean(card_copy.get_text(separator=" "))
            if is_meaningful(desc_text):
                business["Description"] = desc_text

    # ---- Description final fallback ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            desc_text = clean(meta_desc["content"])
            if is_meaningful(desc_text):
                business["Description"] = desc_text

    # ---- Phone ----
    phone_icon = soup.select_one("i.fa-phone")
    if phone_icon and phone_icon.parent:
        phone_text = clean(phone_icon.parent.get_text())
        if phone_text:
            business["Phone"] = phone_text

    # ---- Website URL ----
    site_span = soup.select_one('span[selector-type="Website"] a[href]')
    if site_span:
        business["Website URL"] = site_span["href"]

    # ---- Keywords ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_text = clean(meta_kw.get("content", ""))
        if is_meaningful(kw_text):
            business["Keywords"] = kw_text

    # ---- Hours----
    hours_container = soup.select_one(".HoursofOperation .row.mb-0.text-dark")
    if hours_container:
        cells = hours_container.find_all("div", recursive=False)
        pairs = []
        for i in range(0, len(cells) - 1, 2):
            day = clean(cells[i].get_text()).rstrip(":")
            value = clean(cells[i + 1].get_text())
            if day:
                pairs.append(f"{day}: {value}")
        hours_text = "; ".join(pairs)
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category  ----
    crumbs = [clean(li.get_text()) for li in soup.select(".breadcrumb li.breadcrumb-item")]
    crumbs = [c for c in crumbs if c]
    if len(crumbs) >= 2:
        business["Category"] = crumbs[-2]

    # ---- Owner Name ("Key Contacts" card: name sits in an <h5>, e.g.
    #      "Svetlana Reeves", above a job-title <h6> and phone/email) ----
    contact_card = None
    for heading in soup.select(".card-body h3.card-title"):
        if "key contact" in clean(heading.get_text()).lower():
            contact_card = heading.find_parent("div", class_="card-body")
            break
    if contact_card:
        name_tag = contact_card.select_one("h5")
        if name_tag:
            owner_name = clean(name_tag.get_text())
            if is_meaningful(owner_name):
                business["Owner Name"] = owner_name

    # ---- Owner Name fallback (FAQPage JSON-LD -- the "Is there a key
    #      contact at ...?" answer reads "You can contact NAME at PHONE.") ----
    if not business["Owner Name"]:
        for script in soup.find_all("script", type="application/ld+json"):
            if not script.string:
                continue
            try:
                data = json.loads(script.string, strict=False)
            except Exception:
                continue
            if not isinstance(data, dict) or data.get("@type") != "FAQPage":
                continue
            for item in data.get("mainEntity", []):
                if not isinstance(item, dict):
                    continue
                if "key contact" not in clean(item.get("name", "")).lower():
                    continue
                answer = item.get("acceptedAnswer", {})
                text = answer.get("text", "") if isinstance(answer, dict) else ""
                match = re.search(r"contact\s+(.+?)\s+at\b", text, re.I)
                if match:
                    business["Owner Name"] = clean(match.group(1))
            break

    # ---- Logo fallback (profile image, if JSON-LD had none) ----
    if not business["Logo"]:
        logo_img = soup.select_one("img.ProfileImage")
        if logo_img and logo_img.get("src"):
            business["Logo"] = urljoin(url, logo_img["src"])

    # ---- Photos ----
    photos = []
    for a in soup.select("#profile_images a.lightbox_trigger[href]"):
        photos.append(urljoin(url, a["href"]))
    business["Photos"] = photos

    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email

    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto and mailto.get("href"):
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Social Media Links ----
    for network in ("Facebook", "Twitter"):
        link = soup.select_one(f'span[selector-type="{network}"] a[href]')
        if link and link.get("href"):
            business["Social Media Links"][network] = link["href"]

    return business


# ==========================================================
# Site parser: trueen.com
# ==========================================================

_TRUEEN_OWN_EMAIL_DOMAINS = ("trueen.com",)
_TRUEEN_OWN_SOCIAL_HANDLES = (
    "facebook.com/trueencom",
    "twitter.com/trueen_com",
    "linkedin.com/company/trueen-com",
)


def _split_trueen_address(text):
    result = {"Street": "", "City": "", "State": "", "Zipcode": ""}

    text = clean(text)
    if not text:
        return result

    zip_match = re.search(r"(\d{5}(?:-\d{4})?)\s*$", text)
    if zip_match:
        result["Zipcode"] = zip_match.group(1)
        text = text[:zip_match.start()].strip().rstrip(",").strip()

    parts = [p.strip() for p in text.split(",") if p.strip()]

    if parts:
        result["State"] = parts.pop()
    if parts:
        result["City"] = parts.pop()
    if parts:
        result["Street"] = ", ".join(parts)

    return result


def _trueen_faq_answers(soup):
    answers = {}
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("@type") != "FAQPage":
            continue
        for item in data.get("mainEntity", []):
            if not isinstance(item, dict):
                continue
            question = clean(item.get("name", ""))
            accepted = item.get("acceptedAnswer", {})
            text = accepted.get("text", "") if isinstance(accepted, dict) else ""
            if question and text:
                answers[question.lower()] = text
    return answers


def _trueen_local_business_jsonld(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "LocalBusiness":
            return data
    return {}


def parse_trueen(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    faq = _trueen_faq_answers(soup)
    local_business = _trueen_local_business_jsonld(soup)

    # ---- Business Name ----
    h1 = soup.select_one("h1.header-titlex") or soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"] and local_business.get("name"):
        business["Business Name"] = clean(local_business["name"])
    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            # Drop the " - <tagline>, <location> - TRUEen" suffix this
            # template appends to every og:title.
            business["Business Name"] = clean(og_title["content"].split(" - ")[0])

    # ---- Category ----
    cat_link = soup.select_one("span.single-page-category a") or \
        soup.select_one('a[href*="/business-listing/category/"]')
    if cat_link:
        business["Category"] = clean(cat_link.get_text())

    # ---- Country ----
    country_icon = soup.select_one("i.fa-passport")
    country_link = (
        country_icon.find_parent("p").select_one("a") if country_icon and country_icon.find_parent("p") else None
    ) or soup.select_one('a[href*="/business-listing/country/"]')
    if country_link:
        business["Country"] = clean(country_link.get_text())
    elif local_business.get("address", {}).get("addressCountry"):
        business["Country"] = clean(local_business["address"]["addressCountry"])

    # ---- Street / City / State / Zipcode ----
    address_text = None
    for question, text in faq.items():
        if "headquarters located" in question:
            address_text = text
            break

    if not address_text:
        addr_locality = local_business.get("address", {}).get("addressLocality")
        if addr_locality:
            address_text = addr_locality

    if not address_text:
        marker_icon = soup.select_one("i.fa-map-marker")
        if marker_icon and marker_icon.find_parent("p"):
            address_text = marker_icon.find_parent("p").get_text()

    if address_text:
        parts = _split_trueen_address(address_text)
        business["Street"] = parts["Street"]
        business["City"] = parts["City"]
        business["State"] = parts["State"]
        business["Zipcode"] = parts["Zipcode"]

    # ---- Phone ----
    for question, text in faq.items():
        if "contact phone number" in question and re.search(r"\d{5,}", text):
            business["Phone"] = clean(text)
            break

    if not business["Phone"] and local_business.get("telephone"):
        business["Phone"] = clean(local_business["telephone"])

    if not business["Phone"]:
        phone_p = soup.select_one("p.single-page-phone")
        if phone_p:
            business["Phone"] = clean(phone_p.get_text())

    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel and tel.get("href"):
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Website URL ----
    website_link = soup.select_one('a.view-button[target="_blank"][rel="nofollow"]')
    if website_link and website_link.get("href"):
        href = website_link["href"].strip()
        if href and not href.lower().startswith("javascript:"):
            business["Website URL"] = href

    if not business["Website URL"]:
        for question, text in faq.items():
            if "official website" in question and text.strip().lower().startswith(("http://", "https://")):
                business["Website URL"] = clean(text)
                break

    # ---- Description ----
    for question, text in faq.items():
        if question.startswith("who is") and "owner" not in question and "ceo" not in question:
            business["Description"] = clean_multiline(text)
            break

    if not business["Description"]:
        bio = soup.select_one("div.company-bio")
        if bio:
            paragraphs = [clean(p.get_text()) for p in bio.find_all("p")]
            paragraphs = [p for p in paragraphs if p]
            if paragraphs:
                business["Description"] = "\n".join(paragraphs)
            else:
                business["Description"] = clean(bio.get_text())

    if not business["Description"] and local_business.get("description"):
        business["Description"] = clean(local_business["description"])

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and is_meaningful(meta_desc.get("content", "")):
            business["Description"] = clean(meta_desc["content"])

    # ---- Owner Name (FAQPage JSON-LD only: "Who is the Owner/CEO/
    #      Representative of <business>?" -- the HTML-rendered version of
    #      this question is just a lead-gen form with no real name, so
    #      only the JSON-LD answer ever carries an actual person's name) ----
    for question, text in faq.items():
        if "owner" in question and ("ceo" in question or "representative" in question):
            owner_name = clean(text)
            if is_meaningful(owner_name) and "company information" not in owner_name.lower():
                business["Owner Name"] = owner_name
            break

    # ---- Hours ----
    for question, text in faq.items():
        if "business hours" in question or "opening hours" in question or "operating hours" in question:
            business["Hours"] = clean(text)
            break

    # ---- Social Media Links -----
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href == "#" or href.lower().startswith("javascript:"):
            continue
        low = href.lower()
        if any(handle in low for handle in _TRUEEN_OWN_SOCIAL_HANDLES):
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# Site parser: citysquares.com
# ==========================================================

def _split_citysquares_address(text):
    result = {"Street": "", "City": "", "State": "", "Zipcode": ""}

    parts = [p.strip() for p in clean(text).split(",") if p.strip()]
    if not parts:
        return result

    if re.match(r"^\d{5}(?:-\d{4})?$", parts[-1]):
        result["Zipcode"] = parts.pop()

    if parts:
        result["State"] = parts.pop()
    if parts:
        result["City"] = parts.pop()
    if parts:
        result["Street"] = ", ".join(parts)

    return result


def parse_citysquares(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name ----
    h1 = soup.select_one("h1.listing")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Street / City / State / Zipcode ----
    address_span = soup.select_one("#full-address")
    if address_span:
        parts = _split_citysquares_address(address_span.get_text())
        business["Street"] = parts["Street"]
        business["City"] = parts["City"]
        business["State"] = parts["State"]
        business["Zipcode"] = parts["Zipcode"]


    return business


# ==========================================================
# Site parser: b2bco.com
# ==========================================================

def parse_b2bco(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name ----
    name_el = soup.select_one("div.business.s-title h1")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Address (labeled "General Information" section) ----
    for addr_div in soup.select("div.Businessaddress"):
        text = clean(addr_div.get_text())
        if re.match(r"^Address:", text, flags=re.I):
            business["Street"] = re.sub(r"^Address:\s*", "", text, flags=re.I)
            break

    country_el = soup.select_one("div.Businesscountry a")
    if country_el:
        business["Country"] = clean(country_el.get_text())

    state_el = soup.select_one("div.countrypart a")
    if state_el:
        business["State"] = clean(state_el.get_text())

    city_el = soup.select_one("div.businesscity a")
    if city_el:
        business["City"] = clean(city_el.get_text())

    # ---- Phone (tel: link) ----
    phone_el = soup.select_one("div.Businessphone a[href^='tel:']")
    if phone_el:
        business["Phone"] = clean(phone_el.get_text())

    # ---- Website URL  ----
    website_el = soup.select_one("div.Businessweb a")
    if website_el:
        site_text = clean(website_el.get_text())
        if site_text:
            business["Website URL"] = site_text

    # ---- Description  ----
    desc_label = soup.find(string=re.compile(r"Business Summary", re.I))
    if desc_label:
        desc_block = desc_label.find_parent("div")
        if desc_block:
            summary_div = desc_block.find_next_sibling("div", class_="comtext")
            if summary_div:
                desc_text = clean(summary_div.get_text())
                if is_meaningful(desc_text):
                    business["Description"] = desc_text
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords ----
    kw_label = soup.find(string=re.compile(r"Business Keywords", re.I))
    if kw_label:
        kw_block = kw_label.find_parent("div")
        if kw_block:
            kw_div = kw_block.find_next_sibling("div", class_="comtext")
            if kw_div:
                kw_text = clean(kw_div.get_text())
                if is_meaningful(kw_text):
                    business["Keywords"] = kw_text
    if not business["Keywords"]:
        meta_kw = soup.find("meta", attrs={"name": "keywords"})
        if meta_kw:
            kw_raw = clean(meta_kw.get("content", ""))
            if is_meaningful(kw_raw):
                business["Keywords"] = kw_raw

    # ---- Category  ----
    category_el = soup.select_one("ul.b-activities li a")
    if category_el:
        business["Category"] = clean(category_el.get_text())

    # ---- Logo (profile header logo image) ----
    logo_el = soup.select_one("div.business.s-title div.logo img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Business Email (mailto: link, if present) ----
    email_el = soup.select_one('a[href^="mailto:"]')
    if email_el:
        business["Business Email"] = email_el["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Hours ----
    hours_label = soup.find(string=re.compile(r"Business Hours", re.I))
    if hours_label:
        hours_block = hours_label.find_parent("div")
        if hours_block:
            hours_div = hours_block.find_next_sibling("div", class_="comtext")
            if hours_div:
                hours_text = clean(hours_div.get_text())
                if is_meaningful(hours_text):
                    business["Hours"] = hours_text

    return business


# ==========================================================
# Site parser: find-us-here.com
# ==========================================================

_FINDUSHERE_EXCLUDED_LINK_DOMAINS = (
    "find-us-here.com", "facebook.com", "twitter.com", "x.com",
    "whatsapp.com", "wa.me", "telegram.me", "t.me", "google.com",
    "ezoic.net",
)


def parse_findushere(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    page_text = soup.get_text("\n")

    # ---- Address (Street / City / State / Zipcode) ----
    addr_match = re.search(r"\bAddress\b\s*\n(.*?)\n\s*Phone\b", page_text, re.S)
    if addr_match:
        addr_lines = [clean(line) for line in addr_match.group(1).split("\n")]
        addr_lines = [line for line in addr_lines if line]
        if addr_lines and re.fullmatch(r"\d{5}(-\d{4})?", addr_lines[-1]):
            business["Zipcode"] = addr_lines.pop()
        if addr_lines:
            business["State"] = addr_lines.pop()
        if addr_lines:
            business["City"] = addr_lines.pop()
        if addr_lines:
            business["Street"] = " ".join(addr_lines)

    # ---- Country ----
    h2 = soup.find("h2")
    if h2:
        tokens = clean(h2.get_text()).split()
        if tokens:
            business["Country"] = tokens[-1]

    # ---- Phone (tel: link) ----
    tel = soup.select_one('a[href^="tel:"]')
    if tel:
        phone_text = clean(tel.get_text())
        business["Phone"] = phone_text or tel["href"].replace("tel:", "").strip()

    # ---- Business Email ----
    email_scope = soup.select_one('[itemprop="email"]') or soup
    mailto = email_scope.select_one('a[href^="mailto:"]') or soup.select_one('a[href^="mailto:"]')
    if mailto:
        business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()
    if not business["Business Email"]:
        business["Business Email"] = _find_cf_email(soup)

    # ---- Website URL  ----
    web_label = soup.find(
        lambda tag: tag.name in ("h3", "h4", "h5", "strong", "b", "p", "div", "span")
        and clean(tag.get_text()) == "Web"
    )
    if web_label:
        for link in web_label.find_all_next("a", href=True):
            href = link["href"]
            if not href.startswith("http"):
                continue
            if _hostname_matches_social_domain(href, "google.com") and "maps" in href.lower():
                continue
            if any(_hostname_matches_social_domain(href, d) for d in _FINDUSHERE_EXCLUDED_LINK_DOMAINS):
                continue
            business["Website URL"] = href
            break
        if not business["Website URL"]:
            web_match = re.search(r"\bWeb\b\s*\n\s*(\S+)", page_text)
            if web_match:
                business["Website URL"] = web_match.group(1).strip("<>")

    # ---- Category + Description  ----
    category_node = None
    for node in soup.find_all(string=re.compile(r"Category:\s*\S")):
        if node.find_parent(["script", "style"]):
            continue
        candidate = clean(re.sub(r"^.*Category:\s*", "", str(node), flags=re.S))
        if not candidate or len(candidate) > 80 or re.search(r"[{}();=]", candidate):
            continue
        category_node = node
        business["Category"] = candidate
        break

    if category_node:
        category_block = category_node.find_parent(["tr", "li", "div", "p"])
        if category_block:
            desc_block = category_block.find_next_sibling(["tr", "li", "div", "p"])
            if desc_block:
                desc_text = clean(desc_block.get_text())
                if is_meaningful(desc_text):
                    business["Description"] = desc_text

    if not business["Description"]:
        meta_desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Logo (og:image, preferred over any inline listing photo since
    #      it's the one consistently populated across listings) ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])

    return business


# ==========================================================
# Site parser: a-zbusinessfinder.com
# ==========================================================

def parse_azbusinessfinder(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    page_text = soup.get_text("\n")

    # ---- Address ----
    addr_match = re.search(r"Physical Address\s*(.*?)\n-?\s*Phone\b", page_text, re.S)
    if addr_match:
        addr_lines = [clean(line) for line in addr_match.group(1).split("\n")]
        addr_lines = [line for line in addr_lines if line]
        if addr_lines and re.fullmatch(r"\d{5}(-\d{4})?", addr_lines[-1]):
            business["Zipcode"] = addr_lines.pop()
        if addr_lines:
            business["State"] = addr_lines.pop()
        if addr_lines:
            business["City"] = addr_lines.pop()
        if addr_lines:
            business["Street"] = " ".join(addr_lines)

    # ---- Country  ----
    h2 = soup.find("h2")
    if h2:
        tokens = clean(h2.get_text()).split()
        if tokens:
            business["Country"] = tokens[-1]

    # ---- Phone (tel: link) ----
    tel = soup.select_one('a[href^="tel:"]')
    if tel:
        phone_text = clean(tel.get_text())
        business["Phone"] = phone_text or tel["href"].replace("tel:", "").strip()

    # ---- Business Email ----
    email_scope = soup.select_one('[itemprop="email"]') or soup
    mailto = email_scope.select_one('a[href^="mailto:"]') or soup.select_one('a[href^="mailto:"]')
    if mailto:
        business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()
    if not business["Business Email"]:
        business["Business Email"] = _find_cf_email(soup)

    # ---- Website URL  ----
    website_label = soup.find(
        lambda tag: tag.name in ("h3", "h4", "h5", "strong", "b", "p", "div", "span", "li", "td", "th")
        and clean(tag.get_text()) == "Website"
    )
    if website_label:
        for link in website_label.find_all_next("a", href=True):
            href = link["href"]
            if not href.startswith("http"):
                continue
            if "maps" in href.lower() and _hostname_matches_social_domain(href, "google.com"):
                continue
            if any(_hostname_matches_social_domain(href, d) for d in _FINDUSHERE_EXCLUDED_LINK_DOMAINS):
                continue
            business["Website URL"] = href
            break
    if not business["Website URL"]:
        url_link = soup.select_one('a[itemprop="url"][href^="http"]')
        if url_link:
            business["Website URL"] = url_link["href"]
    if not business["Website URL"]:
        web_match = re.search(r"\bWebsite\b\s*\n\s*(\S+)", page_text)
        if web_match:
            business["Website URL"] = web_match.group(1).strip("<>")

    # ---- Category  ----
    breadcrumb = soup.find(lambda tag: tag.name in ("nav", "div", "ul", "ol", "p", "table", "tr", "td") and "»" in tag.get_text())
    if breadcrumb:
        crumb_links = breadcrumb.find_all("a")
        if crumb_links:
            business["Category"] = clean(crumb_links[-1].get_text())

    # ---- Description ----
    desc_header = soup.find(string=re.compile(r"Business/Community Description", re.I))
    if desc_header and not desc_header.find_parent(["script", "style"]):
        header_block = desc_header.find_parent(["tr", "th", "td", "div", "p"])
        if header_block and header_block.name in ("td", "th"):
            header_block = header_block.find_parent("tr") or header_block
        if header_block:
            desc_block = header_block.find_next_sibling(["tr", "div", "p"])
            if desc_block:
                desc_text = clean(desc_block.get_text())
                if is_meaningful(desc_text):
                    business["Description"] = desc_text

    if not business["Description"]:
        meta_desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Logo ----
    logo_img = soup.select_one('img[src*="business_images/main"]') or soup.select_one('img[src*="business_images"]')
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


# ==========================================================
# Site parser: cybo.com
# ==========================================================

CYBO_SOCIAL_TAG_MAP = {
    "fb": "Facebook",
    "tw": "Twitter",
    "yt": "YouTube",
    "linkedin": "LinkedIn",
    "instagram": "Instagram",
    "tiktok": "TikTok",
}

CYBO_NETWORK_DOMAIN_ROOT = {
    "TikTok": "tiktok.com",
}


def parse_cybo(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()
    page_text = soup.get_text("\n")

    # ---- Business Name ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Street  ----
    maps_link = soup.select_one('a[href^="https://www.google.com/maps/search/"]')
    if maps_link:
        business["Street"] = clean(maps_link.get_text())
        business["GBP Link"] = maps_link["href"]

    # ---- City / State / Zipcode / Country (labeled "Address" block) ----
    city_match = re.search(r"\bCity:\s*([^\n]+)", page_text)
    if city_match:
        business["City"] = clean(city_match.group(1))
    state_match = re.search(r"\bState:\s*([^\n]+)", page_text)
    if state_match:
        business["State"] = clean(state_match.group(1))
    zip_match = re.search(r"\bPostal Code:\s*([^\n]+)", page_text)
    if zip_match:
        business["Zipcode"] = clean(zip_match.group(1))
    country_match = re.search(r"\bCountry:\s*([^\n]+)", page_text)
    if country_match:
        business["Country"] = clean(country_match.group(1))

    # ---- Zipcode fallback ----
    if business["Street"] and business["City"]:
        tail_pattern = r",?\s*" + re.escape(business["City"])
        if business["State"]:
            tail_pattern += r",?\s*" + re.escape(business["State"])
        tail_pattern += r"\s*(\d{5}(?:-\d{4})?)?\s*$"
        tail_match = re.search(tail_pattern, business["Street"], re.I)
        if tail_match:
            if not business["Zipcode"] and tail_match.group(1):
                business["Zipcode"] = tail_match.group(1)
            business["Street"] = clean(business["Street"][:tail_match.start()].rstrip(","))

    # ---- Phone  ----
    phone_link = soup.select_one('a[href*="/phone/how-to-call/"]')
    if phone_link:
        business["Phone"] = clean(phone_link.get_text())

    # ---- Website URL  ----
    for a in soup.select('a[href*="/r/biz/web"]'):
        href = a.get("href", "")
        tag_match = re.search(r"[?&]social_tag=([^&]+)", href)
        if not tag_match:
            if not business["Website URL"]:
                site_text = clean(a.get_text())
                business["Website URL"] = site_text if site_text else href
            continue
        network = CYBO_SOCIAL_TAG_MAP.get(tag_match.group(1).lower(), tag_match.group(1).title())
        link_text = clean(a.get_text())
        value = href
        domain_root = CYBO_NETWORK_DOMAIN_ROOT.get(network)
        if domain_root:
            idx = link_text.lower().find(domain_root)
            if idx != -1:
                value = link_text[idx:]
        business["Social Media Links"][network] = value

    # ---- Description ("About" section) ----
    about_label = soup.find(string=re.compile(r"^\s*About\s*$"))
    if about_label:
        block = about_label.find_parent(["h1", "h2", "h3", "h4", "div", "span"]) or about_label
        next_block = block.find_next(["p", "div"])
        if next_block:
            desc_text = clean(next_block.get_text())
            if is_meaningful(desc_text):
                business["Description"] = desc_text
    if not business["Description"]:
        about_match = re.search(
            r"\nAbout\n+(.+?)\n\n(?:💳|👥|\*\*Categories|Categories:|##|$)",
            page_text, re.S,
        )
        if about_match:
            desc_text = clean(about_match.group(1))
            if is_meaningful(desc_text):
                business["Description"] = desc_text
    if not business["Description"]:
        meta_desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Hours ----
    hours_match = re.search(r"\bHours\s*\n(.*?)\nPhone\b", page_text, re.S)
    if hours_match:
        hour_lines = [clean(line) for line in hours_match.group(1).split("\n")]
        hour_lines = [line for line in hour_lines if line and line != "\u25be"]
        detail_lines = [line for line in hour_lines if "day" in line.lower() or ":" in line]
        chosen = detail_lines[-1] if detail_lines else (hour_lines[-1] if hour_lines else "")
        chosen = re.sub(r"(?<=[a-z])(?=\d)", " ", chosen)
        if is_meaningful(chosen):
            business["Hours"] = chosen

    # ---- Category ----
    cat_match = re.search(r"\*?\*?Categories:\*?\*?\s*([^\n.]+)", page_text)
    if cat_match:
        business["Category"] = clean(cat_match.group(1))
    if not business["Category"]:
        # Fallback: the category pill/tag link under the header, which
        # (unlike the location breadcrumb links above it) points at a
        # two-segment /US/<city-state-slug>/<category-slug> path.
        cat_link = soup.find("a", href=re.compile(r"^/US/[a-z0-9-]+/[a-z0-9-]+/?$"))
        if cat_link:
            business["Category"] = clean(cat_link.get_text())


    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])

    return business


# ==========================================================
# Site parser: linkcentre.com
# ==========================================================

def parse_linkcentre(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Address ----
    meta_map = {
        "business:contact_data:street_address": "Street",
        "business:contact_data:locality": "City",
        "business:contact_data:postal_code": "Zipcode",
        "business:contact_data:country_name": "Country",
        "business:contact_data:phone_number": "Phone",
    }
    for prop, field in meta_map.items():
        tag = soup.find("meta", property=prop)
        if tag and tag.get("content"):
            business[field] = clean(tag["content"])

    # ---- Business Name ----
    h1 = soup.select_one("h1.v2-hero-name")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- JSON-LD ----
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue

        graph = data.get("@graph") if isinstance(data, dict) else None
        objects = graph if graph else (data if isinstance(data, list) else [data])

        for obj in objects:
            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
                continue

            if not business["Business Name"]:
                business["Business Name"] = obj.get("name", "")

            addr = obj.get("address", {}) or {}
            if not business["Street"]:
                business["Street"] = addr.get("streetAddress", "")
            if not business["City"]:
                business["City"] = addr.get("addressLocality", "")
            if not business["State"]:
                business["State"] = addr.get("addressRegion", "")
            if not business["Zipcode"]:
                business["Zipcode"] = addr.get("postalCode", "")

            if not business["Phone"]:
                business["Phone"] = obj.get("telephone", "")

            same_as = obj.get("sameAs") or []
            for link in same_as:
                matched_social = False
                for domain, network in SOCIAL_DOMAINS.items():
                    if domain in link.lower():
                        business["Social Media Links"][network] = link
                        matched_social = True
                        break
                if not matched_social and not business["Website URL"]:
                    business["Website URL"] = link

            if obj.get("description"):
                business["Description"] = clean(obj["description"])

            logo_obj = obj.get("logo") or obj.get("image")
            if isinstance(logo_obj, dict) and logo_obj.get("url"):
                business["Logo"] = urljoin(url, logo_obj["url"])
            elif isinstance(logo_obj, str):
                business["Logo"] = urljoin(url, logo_obj)

            knows_about = obj.get("knowsAbout") or []
            if knows_about:
                business["Category"] = ", ".join(knows_about)

    # ---- Website URL fallback  ----
    if not business["Website URL"]:
        listing_url = soup.select_one("a.v2-listing-url[href]")
        if listing_url:
            business["Website URL"] = listing_url["href"]

    # ---- Description fallback (meta description) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Category fallback  ----
    if not business["Category"]:
        cat_links = [clean(a.get_text()) for a in soup.select("div.v2-cat-pills a.v2-cat-pill")]
        cat_links = [c for c in cat_links if c]
        if cat_links:
            business["Category"] = ", ".join(cat_links)

    # ---- Logo fallback (og:image) ----
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Business Email ----
    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").split("?")[0].strip()
    if not business["Business Email"]:
        business["Business Email"] = _find_cf_email(soup)

    return business


# ==========================================================
# Site parser: band.us (BAND / Naver Band group intro pages)
# ==========================================================

_BAND_DESCRIPTION_LABELS = [
    "Owner Name", "Address", "Phone", "Business Email", "About us", "Related Searches",
]


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


def parse_band(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name --
    og_title = soup.find("meta", property="og:title")
    title_text = og_title["content"] if og_title and og_title.get("content") else None
    if not title_text:
        title_tag = soup.find("title")
        title_text = title_tag.get_text() if title_tag else ""
    business["Business Name"] = clean(re.sub(r"\s*\|\s*BAND\s*$", "", title_text or "", flags=re.I))

    desc_tag = soup.find("meta", attrs={"name": "description"})
    description = desc_tag["content"] if desc_tag and desc_tag.get("content") else None
    if not description:
        og_desc = soup.find("meta", property="og:description")
        description = og_desc["content"] if og_desc and og_desc.get("content") else ""

    sections = _band_description_sections(description)

    # ---- Address -- 
    address = sections.get("Address", "")
    if address:
        street, city, state, zipcode = _split_blinx_address(address)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone ----
    if sections.get("Phone"):
        business["Phone"] = sections["Phone"]

    # ---- Business Email ----
    if sections.get("Business Email"):
        business["Business Email"] = sections["Business Email"]

    # ---- Description ("About us:" section) ----
    if sections.get("About us"):
        business["Description"] = sections["About us"]

    # ---- Keywords  ----
    if sections.get("Related Searches"):
        business["Keywords"] = sections["Related Searches"]

    return business


# ==========================================================
# Site parser: americansearch.info
# ==========================================================

def parse_americansearch(url, html):
    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name -- 
    h1 = soup.select_one("div.header-member-name h1.bold")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(re.sub(r"\s+on\s+AMERICAN SEARCH\s*$", "", og_title["content"], flags=re.I))

    # ---- Address -- 
    addr_tag = soup.select_one('[itemprop="streetAddress"]')
    if addr_tag:
        street, city, state, zipcode = _split_blinx_address(clean(addr_tag.get_text()))
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Country  ----
    crumbs = [clean(s.get_text()) for s in soup.select('ol.breadcrumb span[itemprop="name"]')]
    if len(crumbs) >= 3:
        business["Country"] = crumbs[1]

    # ---- Category  ----
    if len(crumbs) >= 3:
        business["Category"] = crumbs[2]
    if not business["Category"]:
        cat_tag = soup.select_one("span.profile-header-top-category")
        if cat_tag:
            business["Category"] = clean(cat_tag.get_text())

    # ---- Phone ----
    phone_tag = soup.select_one('[itemprop="telephone"]')
    if phone_tag:
        business["Phone"] = clean(phone_tag.get_text())

    # ---- Website URL ----
    site_link = soup.select_one('a.weblink[itemprop="url"]')
    if site_link and site_link.get("href"):
        business["Website URL"] = clean(site_link["href"])

    # ---- Description ("About my Business" free-text block) ----
    about_tag = soup.select_one("span.textarea.textarea-about_me")
    if about_tag:
        business["Description"] = clean(about_tag.get_text())

    # ---- Logo----
    logo_tag = soup.select_one("div.profile-image img.img-rounded")
    if logo_tag and logo_tag.get("src"):
        business["Logo"] = urljoin(url, logo_tag["src"])

    return business


# ==========================================================
# Site parser: blogs.globalbusinessdirectory.us
# ==========================================================

_BLOGS_GBD_LABELS = [
    "Owner Name", "Address", "Phone", "Website", "Business Email",
    "About Us", "Related Searches",
]


def parse_blogs_globalbusinessdirectory(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one("h1.post-title")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"])
    if not business["Business Name"]:
        title_tag = soup.find("title")
        if title_tag:
            business["Business Name"] = clean(
                re.sub(r"\s*[&#8211;\-]+.*$", "", title_tag.get_text())
            )

    # ---- Label/value  ----
    description = ""
    content_block = soup.select_one("div.post-content.theme-blog-details")
    if content_block:
        lines = []
        for p in content_block.find_all("p", recursive=False):
            text = clean(p.get_text())
            if text:
                lines.append(text)
        description = "\n".join(lines)

    if not description:
        og_desc = soup.find("meta", property="og:description")
        description = og_desc["content"] if og_desc and og_desc.get("content") else ""

    sections = _band_description_sections(description, labels=_BLOGS_GBD_LABELS)

    # ---- Owner Name ----
    if sections.get("Owner Name"):
        business["Owner Name"] = sections["Owner Name"]

    # ---- Address -> Street / City / State / Zipcode ----
    address = sections.get("Address", "")
    if address:
        street, city, state, zipcode = _split_blinx_address(address)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone ----
    if sections.get("Phone"):
        business["Phone"] = sections["Phone"]

    # ---- Website URL ----
    if sections.get("Website"):
        business["Website URL"] = sections["Website"]
    if not business["Website URL"]:
        # Fallback: the rendered body wraps the URL in an <a> tag right
        # after a "Website" label paragraph.
        for strong in soup.select("div.post-content.theme-blog-details strong"):
            if clean(strong.get_text()).lower() == "website":
                value_p = strong.find_parent("p").find_next_sibling("p")
                link = value_p.find("a") if value_p else None
                if link and link.get("href"):
                    business["Website URL"] = clean(link["href"])
                break

    # ---- Business Email ----
    if sections.get("Business Email"):
        business["Business Email"] = sections["Business Email"]

    # ---- Description ("About Us" section) ----
    if sections.get("About Us"):
        business["Description"] = sections["About Us"]

    # ---- Keywords ("Related Searches" section) ----
    if sections.get("Related Searches"):
        business["Keywords"] = sections["Related Searches"]

    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])
    if not business["Logo"]:
        img = soup.select_one("div.post-block-media-wrap img")
        if img and img.get("src"):
            business["Logo"] = urljoin(url, img["src"])

    return business


# ==========================================================
# Site parser: n49.com
# ==========================================================

def _extract_balanced_json_object(text, start_marker):
    """Find `start_marker` in `text`, then return the JSON substring of
    the first balanced {...} object that follows it. Needed because a
    naive regex can't safely capture objects containing nested {}/[]
    (as n49Business does: aggregateRating, _geoloc, serviceBoundaries...).
    """
    marker_pos = text.find(start_marker)
    if marker_pos == -1:
        return None

    brace_start = text.find("{", marker_pos)
    if brace_start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(brace_start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start:i + 1]
    return None


_N49_OPS_HOURS_DAY_ORDER = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
]


def _format_n49_hours(ops_hours, hours_text):
    if hours_text == "doNotDisplay" or not ops_hours:
        return ""
    parts = []
    for day in _N49_OPS_HOURS_DAY_ORDER:
        times = ops_hours.get(day)
        if times:
            parts.append(f"{day.capitalize()}: {', '.join(times)}")
    return "; ".join(parts)


def parse_n49(url, html):

    business = empty_business()

    if _looks_blocked(html):
        return business

    raw_json = _extract_balanced_json_object(html, "var n49Business")
    if not raw_json:
        return business

    try:
        data = json.loads(raw_json)
    except Exception:
        return business

    # ---- Business Name ----
    business["Business Name"] = clean(data.get("bName", ""))

    # ---- Street/City/State/Zipcode ----
    # bAddr1 comes with a trailing comma baked in (e.g. "6800 Burnet Rd
    # Ste 8,") since n49 stores city/state/zip separately already.
    business["Street"] = clean((data.get("bAddr1") or "").rstrip(","))
    business["City"] = clean(data.get("bcity", ""))
    business["State"] = clean(data.get("bProvState", ""))
    business["Zipcode"] = clean(data.get("bPostalZip", ""))
    business["Country"] = clean(data.get("countryCode", ""))

    # ---- Phone ----
    if data.get("bPhone1"):
        business["Phone"] = clean(data["bPhone1"])

    # ---- Website URL ----
    if data.get("bWebsite"):
        business["Website URL"] = clean(data["bWebsite"])

    # ---- Business Email ----
    if data.get("bEmail"):
        business["Business Email"] = clean(data["bEmail"])

    # ---- Description ----
    if data.get("bDesc"):
        business["Description"] = clean(data["bDesc"])

    # ---- Hours ----
    business["Hours"] = _format_n49_hours(
        data.get("bOpsHours"), data.get("hoursText", "")
    )

    # ---- Social Media Links ----
    social_field_to_network = {
        "facebookPageUrl": "Facebook",
        "facebook": "Facebook",
        "twitterHandle": "Twitter",
        "twitter": "Twitter",
        "instagram": "Instagram",
        "youtube": "YouTube",
        "pinterest": "Pinterest",
        "linkedin": "LinkedIn",
    }
    for field_name, network in social_field_to_network.items():
        value = data.get(field_name)
        if value and network not in business["Social Media Links"]:
            business["Social Media Links"][network] = value

    # ---- Category ----
    categories = data.get("categories") or [c.get("name") for c in (data.get("categoryObjects") or []) if c.get("name")]
    if categories:
        business["Category"] = ", ".join(categories)

    # ---- Logo ----
    if data.get("logoImagePath"):
        business["Logo"] = urljoin(url, data["logoImagePath"])

    # ---- Photos ----
    photos = [
        img["url"] for img in (data.get("galleryImages") or [])
        if isinstance(img, dict) and img.get("url")
    ]
    if photos:
        business["Photos"] = photos

    return business


# ==========================================================
# Site parser: bizhwy.com
# ==========================================================

_BIZHWY_CITY_STATE_ZIP_RE = re.compile(
    r"^(?P<city>.+?),\s*(?P<state>.+?)\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


def parse_bizhwy(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    if _looks_blocked(html):
        return business

    # ---- Locate the business info block (has the border-#cccccc style
    # and a <strong> name tag) ----
    info_div = None
    for div in soup.find_all("div", style=True):
        if "1px solid #cccccc" in div["style"] and div.find("strong"):
            info_div = div
            break

    if not info_div:
        return business

    # ---- Business Name ----
    strong = info_div.find("strong")
    if strong:
        business["Business Name"] = clean(strong.get_text())

    # ---- Remaining lines: Street / "City, State Zip" / Phone / Category / SubCat ----
    lines = [clean(line) for line in info_div.get_text("\n").split("\n")]
    lines = [line for line in lines if line]
    if lines and business["Business Name"] and lines[0] == business["Business Name"]:
        lines = lines[1:]

    categories = []
    for line in lines:
        lower = line.lower()
        if lower.startswith("phone:"):
            business["Phone"] = clean(line.split(":", 1)[1])
        elif lower.startswith("category:"):
            categories.append(clean(line.split(":", 1)[1]))
        elif lower.startswith("subcat:"):
            categories.append(clean(line.split(":", 1)[1]))
        else:
            match = _BIZHWY_CITY_STATE_ZIP_RE.match(line)
            if match:
                business["City"] = match.group("city")
                business["State"] = match.group("state")
                business["Zipcode"] = match.group("zip")
            elif not business["Street"]:
                business["Street"] = line

    if categories:
        business["Category"] = ", ".join(categories)

    return business


# ==========================================================
# Site parser: yplocal.com
# ==========================================================

_YPLOCAL_ADDRESS_RE = re.compile(
    r"^(?P<street>.+),\s*(?P<city>[^,]+),\s*"
    r"(?P<state>[A-Za-z][A-Za-z .]*?)\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _yplocal_jsonld_local_business(soup):
    """Return the LocalBusiness object from the page's JSON-LD (handles
    both a plain object/list and an @graph-wrapped block)."""
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue

        graph = data.get("@graph") if isinstance(data, dict) else None
        objects = graph if isinstance(graph, list) else (
            data if isinstance(data, list) else [data]
        )

        for obj in objects:
            if isinstance(obj, dict) and obj.get("@type") == "LocalBusiness":
                return obj

    return None


def parse_yplocal(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    ld_business = _yplocal_jsonld_local_business(soup) or {}

    # ---- Business Name ----
    if ld_business.get("name"):
        business["Business Name"] = clean(ld_business["name"])

    if not business["Business Name"]:
        h1 = soup.select_one("h1.bold.inline-block")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        company = soup.select_one(".table-display-company .textbox-company")
        if company:
            business["Business Name"] = clean(company.get_text())

    # ---- Phone ----
    tel = soup.select_one('a[href^="tel:"]')
    if tel and tel.get("href"):
        business["Phone"] = tel["href"].replace("tel:", "").strip()

    if not business["Phone"] and ld_business.get("telephone"):
        business["Phone"] = clean(ld_business["telephone"])

    # ---- Website URL ----
    weblink = soup.select_one("a.weblink[href]")
    if weblink:
        business["Website URL"] = weblink["href"]

    # ---- Description (multi-paragraph About section) ----
    about = soup.select_one(".froala-data.field-about_me")
    if about:
        desc_text = clean_multiline(about.get_text(separator="\n"))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"] and ld_business.get("description"):
        desc_text = clean(ld_business["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Keywords (published under the "SERVICES" row) ----
    for row in soup.select(".table-view-group"):
        label = row.select_one(".col-sm-4")
        value = row.select_one(".col-sm-8")
        if label and value and clean(label.get_text()).lower() == "services":
            kw_text = clean(value.get_text())
            if is_meaningful(kw_text):
                business["Keywords"] = kw_text
            break

    # ---- Address (single unstructured string -> Street/City/State/Zip) ----
    addr_span = soup.select_one(".overview-tab-the-member-address .col-sm-8 span")
    addr_text = clean(addr_span.get_text()) if addr_span else ""

    match = _YPLOCAL_ADDRESS_RE.match(addr_text) if addr_text else None
    if match:
        business["Street"] = clean(match.group("street"))
        business["City"] = clean(match.group("city"))
        business["State"] = clean(match.group("state"))
        business["Zipcode"] = match.group("zip")
    elif addr_text:
        # Fall back to storing the raw string as Street rather than
        # dropping the address entirely if it doesn't match the
        # expected "Street, City, State Zip" shape.
        business["Street"] = addr_text

    # ---- Country  ----
    addr_obj = ld_business.get("address")
    if isinstance(addr_obj, dict):
        country = clean(addr_obj.get("addressCountry", ""))
        if country and country.upper() != "N/A":
            business["Country"] = country

    # ---- Category ----
    category_span = soup.select_one(".profile-header-top-category")
    if category_span:
        cat_text = clean(category_span.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    if not business["Category"]:
        crumbs = [clean(li.get_text()) for li in soup.select("ol.breadcrumb li")]
        crumbs = [c for c in crumbs if c and c.lower() != "home"]
        if len(crumbs) >= 2:
            business["Category"] = crumbs[-2]

    # ---- Logo ----
    logo_img = soup.select_one(".profile-image img[src]")
    if logo_img:
        business["Logo"] = urljoin(url, logo_img["src"])

    if not business["Logo"] and ld_business.get("image"):
        image = ld_business["image"]
        image_url = image.get("url") if isinstance(image, dict) else image
        if image_url:
            business["Logo"] = urljoin(url, image_url)

    # ---- Business Email (opportunistic; not every listing publishes one) ----
    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email

    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto and mailto.get("href"):
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Social Media Links / Website fallback (JSON-LD sameAs) ----
    same_as = ld_business.get("sameAs")
    same_as = same_as if isinstance(same_as, list) else ([same_as] if same_as else [])
    for href in same_as:
        if not isinstance(href, str) or not href.startswith("http"):
            continue
        if "yplocal.com" in href.lower():
            continue
        matched_social = False
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href
                matched_social = True
                break
        if not matched_social and not business["Website URL"]:
            business["Website URL"] = href

    # ---- GBP Link  ----
    directions = soup.select_one("a.member-directions[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    if not business["GBP Link"]:
        location = ld_business.get("location")
        if isinstance(location, dict) and location.get("hasMap"):
            business["GBP Link"] = location["hasMap"]

    return business


# ==========================================================
# Site parser: golocalezservices.com
# ==========================================================

_GOLOCALEZ_ADDRESS_RE = re.compile(
    r"^(?P<street>.+),\s*(?P<city>[^,]+),\s*"
    r"(?P<state>[A-Za-z][A-Za-z .]*?)\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _golocalez_jsonld_local_business(soup):
    """Return the LocalBusiness object from the page's JSON-LD (handles
    both a plain object/list and an @graph-wrapped block)."""
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue

        graph = data.get("@graph") if isinstance(data, dict) else None
        objects = graph if isinstance(graph, list) else (
            data if isinstance(data, list) else [data]
        )

        for obj in objects:
            if isinstance(obj, dict) and obj.get("@type") == "LocalBusiness":
                return obj

    return None


def parse_golocalezservices(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    ld_business = _golocalez_jsonld_local_business(soup) or {}
    page_domain = urlparse(url).netloc.lower().replace("www.", "")

    # ---- Business Name ----
    if ld_business.get("name"):
        business["Business Name"] = clean(ld_business["name"])

    if not business["Business Name"]:
        h1 = soup.select_one("h1.bold.inline-block")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        company = soup.select_one(".table-display-company .textbox-company")
        if company:
            business["Business Name"] = clean(company.get_text())

    # ---- Phone (hidden span, revealed by JS -- no tel: anchor here) ----
    phone_span = soup.select_one(".phone_number")
    if phone_span:
        phone_text = clean(phone_span.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text

    if not business["Phone"] and ld_business.get("telephone"):
        business["Phone"] = clean(ld_business["telephone"])

    # ---- About block (source of both Website URL and Description) ----
    about = soup.select_one(".textarea.textarea-about_me")

    # ---- Website URL ----
    if about:
        for anchor in about.select("a[href]"):
            href = anchor["href"].strip()
            if not href.lower().startswith(("http://", "https://")):
                continue
            if _hostname_matches_social_domain(href, page_domain):
                continue
            business["Website URL"] = href
            break

    # ---- Description  ----
    if about:
        desc_text = clean_multiline(about.get_text(separator="\n"))
        lines = [
            line for line in desc_text.split("\n")
            if line.strip().lower() not in ("website:", "about us:")
            and line.strip() != business["Website URL"]
        ]
        desc_text = "\n".join(lines).strip()
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"] and ld_business.get("description"):
        desc_text = clean(ld_business["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Address (single unstructured string -> Street/City/State/Zip) ----
    addr_span = soup.select_one(".overview-tab-the-member-address .col-sm-8 span")
    addr_text = clean(addr_span.get_text()) if addr_span else ""

    match = _GOLOCALEZ_ADDRESS_RE.match(addr_text) if addr_text else None
    if match:
        business["Street"] = clean(match.group("street"))
        business["City"] = clean(match.group("city"))
        business["State"] = clean(match.group("state"))
        business["Zipcode"] = match.group("zip")
    elif addr_text:
        business["Street"] = addr_text

    # ---- Country  ----
    addr_obj = ld_business.get("address")
    if isinstance(addr_obj, dict):
        country = clean(addr_obj.get("addressCountry", ""))
        if country and country.upper() != "N/A":
            business["Country"] = country

    # ---- Category ----
    category_span = soup.select_one(".profile-header-top-category")
    if category_span:
        cat_text = clean(category_span.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    if not business["Category"]:
        crumbs = [clean(li.get_text()) for li in soup.select("ol.breadcrumb li")]
        crumbs = [c for c in crumbs if c and c.lower() != "home"]
        if len(crumbs) >= 2:
            business["Category"] = crumbs[-2]

    # ---- Logo ----
    logo_img = soup.select_one(".profile-image img[src]")
    if logo_img:
        business["Logo"] = urljoin(url, logo_img["src"])

    if not business["Logo"] and ld_business.get("image"):
        image = ld_business["image"]
        image_url = image.get("url") if isinstance(image, dict) else image
        if image_url:
            business["Logo"] = urljoin(url, image_url)

    # ---- Business Email (opportunistic; not every listing publishes one) ----
    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email

    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto and mailto.get("href"):
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- GBP Link  ----
    directions = soup.select_one("a.get-directions-link[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    if not business["GBP Link"]:
        location = ld_business.get("location")
        if isinstance(location, dict) and location.get("hasMap"):
            business["GBP Link"] = location["hasMap"]

    return business


# ==========================================================
# Site parser: findabusinesspro.com
# ==========================================================

def _findabusinesspro_jsonld_local_business(soup):
    """Return the LocalBusiness object from the page's JSON-LD (handles
    both a plain object/list and an @graph-wrapped block)."""
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue

        graph = data.get("@graph") if isinstance(data, dict) else None
        objects = graph if isinstance(graph, list) else (
            data if isinstance(data, list) else [data]
        )

        for obj in objects:
            if isinstance(obj, dict) and obj.get("@type") == "LocalBusiness":
                return obj

    return None


def parse_findabusinesspro(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    ld_business = _findabusinesspro_jsonld_local_business(soup) or {}
    page_domain = urlparse(url).netloc.lower().replace("www.", "")

    # ---- Business Name ----
    if ld_business.get("name"):
        business["Business Name"] = clean(ld_business["name"])

    if not business["Business Name"]:
        h1 = soup.select_one("h1.bold.inline-block")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        company = soup.select_one(".table-display-company .textbox-company")
        if company:
            business["Business Name"] = clean(company.get_text())

    # ---- About block (source of Website URL, Phone, and Description) ----
    about = soup.select_one(".textarea.textarea-about_me")
    about_paragraphs = [clean(p.get_text()) for p in about.find_all("p")] if about else []

    # ---- Website URL: first external, non-directory http(s) link inside
    # the About block ----
    if about:
        for anchor in about.select("a[href]"):
            href = anchor["href"].strip()
            if not href.lower().startswith(("http://", "https://")):
                continue
            if _hostname_matches_social_domain(href, page_domain):
                continue
            business["Website URL"] = href
            break

    # ---- Phone ----
    for i, para_text in enumerate(about_paragraphs):
        if para_text.strip().lower() == "phone:" and i + 1 < len(about_paragraphs):
            candidate = about_paragraphs[i + 1]
            if is_meaningful(candidate):
                business["Phone"] = candidate
            break

    # ---- Description (About block, with the "Phone:"/"Website:" label
    # lines and their values stripped back out since those are captured
    # separately) ----
    if about:
        desc_text = clean_multiline(about.get_text(separator="\n"))
        lines = [
            line for line in desc_text.split("\n")
            if line.strip().lower() not in ("phone:", "website:")
            and line.strip() != business["Phone"]
            and line.strip() != business["Website URL"]
        ]
        desc_text = "\n".join(lines).strip()
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"] and ld_business.get("description"):
        desc_text = clean(ld_business["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Address (split across individual <span> elements: street, city,
    # state, zip, with a trailing plain-text country after the final <br>) ----
    addr_container = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_container:
        addr_spans = addr_container.find_all("span", recursive=False)
        if len(addr_spans) >= 4:
            business["Street"] = clean(addr_spans[0].get_text())
            business["City"] = clean(addr_spans[1].get_text())
            business["State"] = clean(addr_spans[2].get_text())
            business["Zipcode"] = clean(addr_spans[3].get_text())
        elif not business["Street"]:
            # Fall back to storing the raw container text as Street rather
            # than dropping the address entirely if the expected span
            # layout isn't present.
            addr_text = clean(addr_container.get_text())
            if is_meaningful(addr_text):
                business["Street"] = addr_text

        # Country: trailing plain-text node directly under the container
        # (after the final <br>), not inside any of the address spans.
        trailing_text_nodes = [
            clean(node) for node in addr_container.contents
            if isinstance(node, NavigableString) and clean(node) and clean(node) != ","
        ]
        if trailing_text_nodes:
            country_text = trailing_text_nodes[-1]
            if country_text:
                business["Country"] = country_text

    # ---- Country fallback (JSON-LD; this template's page text sometimes
    # spells the country out in full ("United States") where JSON-LD gives
    # the ISO short form -- only used when the page itself had nothing) ----
    if not business["Country"]:
        addr_obj = ld_business.get("address")
        if isinstance(addr_obj, dict):
            country = clean(addr_obj.get("addressCountry", ""))
            if country and country.upper() != "N/A":
                business["Country"] = country

    # ---- Category ----
    category_span = soup.select_one(".profile-header-top-category")
    if category_span:
        cat_text = clean(category_span.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    if not business["Category"]:
        crumbs = [clean(li.get_text()) for li in soup.select("ol.breadcrumb li")]
        crumbs = [c for c in crumbs if c and c.lower() != "home"]
        if len(crumbs) >= 2:
            business["Category"] = crumbs[-2]

    # ---- Logo ----
    logo_img = soup.select_one(".profile-image img[src]")
    if logo_img:
        business["Logo"] = urljoin(url, logo_img["src"])

    if not business["Logo"] and ld_business.get("image"):
        image = ld_business["image"]
        image_url = image.get("url") if isinstance(image, dict) else image
        if image_url:
            business["Logo"] = urljoin(url, image_url)

    # ---- Business Email (opportunistic; not every listing publishes one) ----
    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email

    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto and mailto.get("href"):
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- GBP Link (scoped to the "Get Directions" anchor and JSON-LD
    # location.hasMap, NOT a page-wide scan -- the footer on this template
    # carries the directory's own unrelated social/map links) ----
    directions = soup.select_one("a.get-directions-link[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    if not business["GBP Link"]:
        location = ld_business.get("location")
        if isinstance(location, dict) and location.get("hasMap"):
            business["GBP Link"] = location["hasMap"]

    return business


# ==========================================================
# Site parser: globeconnected.com
# ==========================================================

def _globeconnected_jsonld(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "LocalBusiness":
            return data
    return {}


def parse_globeconnected(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    jsonld = _globeconnected_jsonld(soup)

    # ---- Business Name ----
    h1 = soup.select_one(".result-content h1") or soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"] and jsonld.get("name"):
        business["Business Name"] = clean(jsonld["name"])

    # ---- Address  ----
    addr_tag = soup.select_one("p.address")
    addr_text = clean(addr_tag.get_text()) if addr_tag else ""
    if not addr_text:
        addr_obj = jsonld.get("address")
        if isinstance(addr_obj, dict) and addr_obj.get("streetAddress"):
            addr_text = clean(addr_obj["streetAddress"])

    if addr_text:
        street, city, state, zipcode = _split_blinx_address(addr_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Country (JSON-LD only; not rendered anywhere on the page) ----
    addr_obj = jsonld.get("address")
    if isinstance(addr_obj, dict) and addr_obj.get("addressCountry"):
        business["Country"] = clean(addr_obj["addressCountry"])

    # ---- Phone ----
    tel = soup.select_one("p.phone a[href^='tel:']")
    if tel and tel.get("href"):
        business["Phone"] = tel["href"].replace("tel:", "").strip()
    if not business["Phone"] and jsonld.get("telephone"):
        business["Phone"] = clean(jsonld["telephone"])

    # ---- Website URL (the business's own external site, not this
    #      directory listing) ----
    site_link = soup.select_one("a.web[href]")
    if site_link and site_link.get("href"):
        business["Website URL"] = site_link["href"]
    if not business["Website URL"] and jsonld.get("url"):
        business["Website URL"] = jsonld["url"]

    # ---- Business Email (Cloudflare-obfuscated on the page; plain in
    #      JSON-LD as a fallback) ----
    email = _find_cf_email(soup)
    if email:
        business["Business Email"] = email
    if not business["Business Email"] and jsonld.get("email"):
        business["Business Email"] = clean(jsonld["email"])

    # ---- Description ("About" section, heading stripped) ----
    desc_tag = soup.select_one("section.description")
    if desc_tag:
        desc_copy = BeautifulSoup(str(desc_tag), "lxml")
        heading = desc_copy.find("h5")
        if heading:
            heading.decompose()
        desc_text = clean(desc_copy.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"] and jsonld.get("description"):
        desc_text = clean(jsonld["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Category (p.cats link list) ----
    cat_links = [clean(a.get_text()) for a in soup.select("p.cats a")]
    cat_links = [c for c in cat_links if c]
    if cat_links:
        business["Category"] = ", ".join(cat_links)

    # ---- Logo (JSON-LD "image" is the business's own logo; og:image on
    #      this template is the directory site's own logo, so it's only
    #      used as a last-resort fallback) ----
    if jsonld.get("image"):
        business["Logo"] = urljoin(url, jsonld["image"])

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


# ==========================================================
# Site parser: whatsyourhours.com
# ==========================================================

def _whatsyourhours_field(soup, field_name):
    """Text of the value span inside a div.table-display-<field_name> row,
    or "" if that row isn't present on this listing."""
    el = soup.select_one(f".table-display-{field_name} .col-sm-8 span")
    return clean(el.get_text()) if el else ""


def parse_whatsyourhours(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"])

    # ---- Owner Name (First Name + Last Name rows combined) ----
    first_name = _whatsyourhours_field(soup, "first_name")
    last_name = _whatsyourhours_field(soup, "last_name")
    owner_name = " ".join(part for part in [first_name, last_name] if part)
    if owner_name:
        business["Owner Name"] = owner_name

    # ---- Address ----
    business["Street"] = _whatsyourhours_field(soup, "address1")
    business["City"] = _whatsyourhours_field(soup, "city")
    business["State"] = _whatsyourhours_field(soup, "state_ln")
    business["Zipcode"] = _whatsyourhours_field(soup, "zip_code")
    business["Country"] = _whatsyourhours_field(soup, "country_ln")

    # ---- Phone (visible phone-number row, falling back to the
    #      header's click-to-reveal phone span) ----
    phone_el = soup.select_one(".table-display-phone_number .phone")
    if phone_el:
        business["Phone"] = clean(phone_el.get_text())
    if not business["Phone"]:
        phone_header = soup.select_one(".phone_number_header")
        if phone_header:
            business["Phone"] = clean(phone_header.get_text())

    # ---- Business Email ----
    email_el = soup.select_one(".table-display-email .email")
    if email_el:
        business["Business Email"] = clean(email_el.get_text())

    # ---- Website URL ----
    website_el = soup.select_one(".table-display-website a[href]")
    if website_el:
        business["Website URL"] = website_el["href"]

    # ---- Description ("Write About You And Your Company" textarea,
    #      one paragraph per line) ----
    about_el = soup.select_one(".table-display-about_me .textarea")
    if about_el:
        paragraphs = [clean(p.get_text()) for p in about_el.find_all("p")]
        paragraphs = [p for p in paragraphs if p]
        if paragraphs:
            business["Description"] = "\n".join(paragraphs)

    # ---- Hours ----
    hours_el = soup.select_one(".table-display-hours")
    if hours_el:
        business["Hours"] = clean(hours_el.get_text())

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        business["Category"] = clean(category_el.get_text())

    # ---- Social Media Links ----
    social_links = {}
    for a in soup.select(".table-display-social_media_links a[href]"):
        href = a.get("href", "")
        for domain, name in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                social_links[name] = href
    if social_links:
        business["Social Media Links"] = social_links

   

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img")
    if logo_el and logo_el.get("src"):
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


# ==========================================================
# Site parser: thebusinessminded.com
# ==========================================================

def parse_thebusinessminded(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"]:
        company_el = soup.select_one(".table-display-company .textbox-company")
        if company_el:
            business["Business Name"] = clean(company_el.get_text())

    # ---- Address ----
    addr_div = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_div:
        direct_spans = addr_div.find_all("span", recursive=False)

        if len(direct_spans) >= 4:
            business["Street"] = clean(direct_spans[0].get_text())
            business["City"] = clean(direct_spans[1].get_text())
            business["State"] = clean(direct_spans[2].get_text())
            business["Zipcode"] = clean(direct_spans[3].get_text())

            addr_copy = BeautifulSoup(str(addr_div), "lxml")
            for br in addr_copy.find_all("br"):
                br.replace_with("\n")
            lines = [clean(line) for line in addr_copy.get_text().split("\n")]
            lines = [line for line in lines if line]
            if lines and not re.search(r"\d", lines[-1]):
                business["Country"] = lines[-1]
        else:
            raw_address = clean(addr_div.get_text())
            if raw_address:
                street, city, state, zipcode = _split_blinx_address(raw_address)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode

    # ---- Website URL ----
    website_el = soup.select_one(".table-display-website a[href]")
    if website_el and website_el.get("href"):
        business["Website URL"] = website_el["href"]

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        business["Category"] = clean(category_el.get_text())

    # ---- Phone + Description ----
    about_el = soup.select_one(".field-about_me")
    if about_el:
        paragraphs = [clean(p.get_text()) for p in about_el.find_all("p")]
        paragraphs = [p for p in paragraphs if p]

        desc_paragraphs = []
        i = 0
        while i < len(paragraphs):
            line = paragraphs[i]
            if re.match(r"^phone:?$", line, flags=re.I) and i + 1 < len(paragraphs):
                business["Phone"] = paragraphs[i + 1]
                i += 2
                continue
            if re.match(r"^about us:?$", line, flags=re.I):
                i += 1
                continue
            desc_paragraphs.append(line)
            i += 1

        if desc_paragraphs:
            business["Description"] = "\n".join(desc_paragraphs)

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img")
    if logo_el and logo_el.get("src"):
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

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
# Site parser: milestones.business
# ==========================================================

def parse_milestones(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    content = soup.select_one(".acadp-listing .col-md-8") or soup

    # ---- Business Name ----
    h1 = content.select_one("h1.acadp-no-margin") or soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Description  ----
    for p in content.find_all("p", recursive=False):
        if p.select_one("img"):
            continue
        text = clean(p.get_text())
        if is_meaningful(text):
            business["Description"] = text
            break

    # ---- Category  ----
    cat_link = content.select_one(".acadp-post-title a[href*='/listing-category/']")
    if cat_link:
        cat_text = clean(cat_link.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Address  ----
    addr_span = soup.select_one("span.acadp-street-address")
    if addr_span:
        addr_text = clean(addr_span.get_text())
        if addr_text:
            street, city, state, zipcode = _split_blinx_address(addr_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    # ---- Country ----
    country_span = soup.select_one("span.acadp-country-name")
    if country_span:
        country_text = clean(country_span.get_text())
        if is_meaningful(country_text):
            business["Country"] = country_text

    # ---- Phone  ----
    phone_span = soup.select_one("span.acadp-phone")
    if phone_span:
        phone_copy = BeautifulSoup(str(phone_span), "lxml")
        icon = phone_copy.find(class_=lambda c: c and "glyphicon" in c)
        if icon:
            icon.decompose()
        phone_text = clean(phone_copy.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text

    # ---- Website URL ----
    site_link = soup.select_one("span.acadp-website a[href]")
    if site_link and site_link.get("href"):
        business["Website URL"] = site_link["href"]

    # ---- Logo  ----
    logo_img = content.select_one("p > img[src]")
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])

    if not business["Logo"]:
        meta_img = soup.select_one("[itemprop='image'] meta[itemprop='url']")
        if meta_img and meta_img.get("content"):
            business["Logo"] = urljoin(url, meta_img["content"])

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


# ==========================================================
# Site parser: iformative.com
# ==========================================================

def _split_iformative_address(address):
    """Split an iFormative-style "Street[, Suite], City, State, Zip"
    address string. Unlike _split_blinx_address, City/State/Zip are each
    their own comma segment here rather than "State Zip" sharing one
    trailing token, so a bare-ZIP last segment is checked for first."""
    street, city, state, zipcode = "", "", "", ""

    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return street, city, state, zipcode

    if len(parts) >= 4 and re.fullmatch(r"\d{5}(?:-\d{4})?", parts[-1]):
        zipcode = parts[-1]
        state = parts[-2]
        city = parts[-3]
        street = ", ".join(parts[:-3])
        return street, city, state, zipcode

    # Fallback shape: "Street, City, State Zip" (state+zip sharing one
    # trailing token), in case some listings punctuate differently.
    if len(parts) >= 3:
        street = ", ".join(parts[:-2])
        city = parts[-2]
        state_zip = parts[-1]
    elif len(parts) == 2:
        street = parts[0]
        state_zip = parts[1]
    else:
        state_zip = parts[0]

    match = re.match(r"^(.*?)\s+([\w-]*\d[\w-]*)$", state_zip.strip())
    if match:
        state = match.group(1).strip()
        zipcode = match.group(2).strip()
    else:
        state = state_zip.strip()

    return street, city, state, zipcode


def parse_iformative(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".product-view h1") or soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    info_td = soup.select_one("td.info")
    if info_td:
        # ---- Website URL  ----
        site_link = info_td.select_one("a[href^='http']")
        if site_link and site_link.get("href"):
            business["Website URL"] = site_link["href"]

        # ---- Normalize----
        info_copy = BeautifulSoup(str(info_td), "lxml")
        for br in info_copy.find_all("br"):
            br.replace_with("\n")
        lines = [clean(line) for line in info_copy.get_text().split("\n")]
        lines = [line for line in lines if line]

        # ---- Category ("Category: <value>" on its own line) ----
        for line in lines:
            match = re.match(r"^Category:\s*(.+)$", line, flags=re.I)
            if match:
                cat_text = clean(match.group(1))
                if is_meaningful(cat_text):
                    business["Category"] = cat_text
                break

        # ---- Address (the line right after the "Contact Information"
        # label) ----
        for i, line in enumerate(lines):
            if line.lower() == "contact information" and i + 1 < len(lines):
                addr_text = lines[i + 1]
                if is_meaningful(addr_text):
                    street, city, state, zipcode = _split_iformative_address(addr_text)
                    business["Street"] = street
                    business["City"] = city
                    business["State"] = state
                    business["Zipcode"] = zipcode
                break

        # ---- Phone ("Phone number: <value>" on its own line) ----
        for line in lines:
            match = re.match(r"^Phone number:\s*(.+)$", line, flags=re.I)
            if match:
                phone_text = clean(match.group(1))
                if is_meaningful(phone_text):
                    business["Phone"] = phone_text
                break

    return business


# ==========================================================
# Site parser: cleansway.com
# ==========================================================

def parse_cleansway(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"]:
        company_el = soup.select_one(".table-display-company .textbox-company")
        if company_el:
            business["Business Name"] = clean(company_el.get_text())

    # ---- Address  ----
    addr_div = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_div:
        raw_address = clean(addr_div.get_text())
        if raw_address:
            street, city, state, zipcode = _split_blinx_address(raw_address)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    # ---- Country  ----
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        graph = data.get("@graph", [data]) if isinstance(data, dict) else data
        if not isinstance(graph, list):
            continue
        for node in graph:
            if not isinstance(node, dict) or node.get("@type") != "LocalBusiness":
                continue
            country = node.get("address", {}).get("addressCountry", "")
            if country and country.upper() != "N/A":
                business["Country"] = country
            break
        if business["Country"]:
            break

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        business["Category"] = clean(category_el.get_text())

    # ---- Phone + Website URL + Description ----
    about_el = soup.select_one(".field-about_me")
    if about_el:
        para_tags = [p for p in about_el.find_all("p") if clean(p.get_text())]

        desc_paragraphs = []
        i = 0
        while i < len(para_tags):
            line = clean(para_tags[i].get_text())
            if re.match(r"^phone:?$", line, flags=re.I) and i + 1 < len(para_tags):
                business["Phone"] = clean(para_tags[i + 1].get_text())
                i += 2
                continue
            if re.match(r"^website:?$", line, flags=re.I) and i + 1 < len(para_tags):
                link = para_tags[i + 1].find("a", href=True)
                business["Website URL"] = link["href"] if link else clean(para_tags[i + 1].get_text())
                i += 2
                continue
            if re.match(r"^about us:?$", line, flags=re.I):
                i += 1
                continue
            desc_paragraphs.append(line)
            i += 1

        if desc_paragraphs:
            business["Description"] = "\n".join(desc_paragraphs)

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img")
    if logo_el and logo_el.get("src"):
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


# ==========================================================
# Site parser: preferredprofessionals.com
# ==========================================================

def parse_preferredprofessionals(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"]:
        company_el = soup.select_one(".table-display-company .textbox-company")
        if company_el:
            business["Business Name"] = clean(company_el.get_text())

    # ---- Address (one combined "Street, City, State Zip" string in a
    # single <span>, same as cleansway.com) ----
    addr_div = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_div:
        raw_address = clean(addr_div.get_text())
        if raw_address:
            street, city, state, zipcode = _split_blinx_address(raw_address)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    # ---- Country (not on the visible page -- only in the LocalBusiness
    # JSON-LD block's address.addressCountry) ----
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        graph = data.get("@graph", [data]) if isinstance(data, dict) else data
        if not isinstance(graph, list):
            continue
        for node in graph:
            if not isinstance(node, dict) or node.get("@type") != "LocalBusiness":
                continue
            country = node.get("address", {}).get("addressCountry", "")
            if country and country.upper() != "N/A":
                business["Country"] = country
            break
        if business["Country"]:
            break

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        business["Category"] = clean(category_el.get_text())

    # ---- Phone + Website URL + Description (label/value paragraph pairs
    # inside "span.textarea.textarea-about_me" -- this skin's equivalent
    # of cleansway's "div.froala-data.field-about_me") ----
    about_el = soup.select_one("span.textarea-about_me")
    if about_el:
        para_tags = [p for p in about_el.find_all("p") if clean(p.get_text())]

        desc_paragraphs = []
        i = 0
        while i < len(para_tags):
            line = clean(para_tags[i].get_text())
            if re.match(r"^phone:?$", line, flags=re.I) and i + 1 < len(para_tags):
                business["Phone"] = clean(para_tags[i + 1].get_text())
                i += 2
                continue
            if re.match(r"^website:?$", line, flags=re.I) and i + 1 < len(para_tags):
                link = para_tags[i + 1].find("a", href=True)
                business["Website URL"] = link["href"] if link else clean(para_tags[i + 1].get_text())
                i += 2
                continue
            if re.match(r"^about us:?$", line, flags=re.I):
                i += 1
                continue
            desc_paragraphs.append(line)
            i += 1

        if desc_paragraphs:
            business["Description"] = "\n".join(desc_paragraphs)

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img")
    if logo_el and logo_el.get("src"):
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


# ==========================================================
# Site parser: bestdealfinder.com
# ==========================================================

def parse_bestdealfinder(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"]:
        company_el = soup.select_one(".table-display-company .textbox-company")
        if company_el:
            business["Business Name"] = clean(company_el.get_text())

    # ---- Address (split across individual <span> elements: street, city,
    # state, zip, with a trailing plain-text country after the final <br>,
    # same layout as findabusinesspro.com) ----
    addr_container = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_container:
        addr_spans = addr_container.find_all("span", recursive=False)
        if len(addr_spans) >= 4:
            business["Street"] = clean(addr_spans[0].get_text())
            business["City"] = clean(addr_spans[1].get_text())
            business["State"] = clean(addr_spans[2].get_text())
            business["Zipcode"] = clean(addr_spans[3].get_text())
        elif not business["Street"]:
            addr_text = clean(addr_container.get_text())
            if is_meaningful(addr_text):
                business["Street"] = addr_text

        trailing_text_nodes = [
            clean(node) for node in addr_container.contents
            if isinstance(node, NavigableString) and clean(node) and clean(node) != ","
        ]
        if trailing_text_nodes:
            country_text = trailing_text_nodes[-1]
            if country_text:
                business["Country"] = country_text

    # ---- Country fallback (LocalBusiness JSON-LD) ----
    if not business["Country"]:
        for script in soup.find_all("script", type="application/ld+json"):
            if not script.string:
                continue
            try:
                data = json.loads(script.string, strict=False)
            except Exception:
                continue
            graph = data.get("@graph", [data]) if isinstance(data, dict) else data
            if not isinstance(graph, list):
                continue
            for node in graph:
                if not isinstance(node, dict) or node.get("@type") != "LocalBusiness":
                    continue
                country = clean(node.get("address", {}).get("addressCountry", ""))
                if country and country.upper() != "N/A":
                    business["Country"] = country
                break
            if business["Country"]:
                break

    # ---- Phone (dedicated labeled row, not embedded in the About block) ----
    phone_el = soup.select_one(".table-display-phone .col-sm-8 span") \
        or soup.select_one(".table-display-phone span")
    if phone_el:
        phone_text = clean(phone_el.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text

    # ---- Website URL (dedicated labeled row, not embedded in the About
    # block) ----
    website_el = soup.select_one(".table-display-website .weblink[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()

    # ---- Description (the About block on this skin holds only plain
    # paragraph text -- no "Phone:"/"Website:" label pairs to strip out,
    # since those fields have their own dedicated rows above) ----
    about_el = soup.select_one(".froala-data.field-about_me")
    if about_el:
        desc_paragraphs = [
            clean(p.get_text()) for p in about_el.find_all("p") if clean(p.get_text())
        ]
        if desc_paragraphs:
            business["Description"] = "\n".join(desc_paragraphs)

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        cat_text = clean(category_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Business Email (opportunistic; not every listing publishes one) ----
    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email
    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto and mailto.get("href"):
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- GBP Link (scoped to the "Get Directions" anchor, not a page-wide
    # scan -- the footer on this template carries the directory's own
    # unrelated social/contact links) ----
    directions = soup.select_one("a.get-directions-link[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    return business


# ==========================================================
# Site parser: 911getit.com
# ==========================================================

def parse_911getit(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Address (single ".profile-header-location" span holding
    # "Street<br>City, State, Zip<br>Country" -- not discrete spans) ----
    addr_el = soup.select_one(".profile-header-location")
    if addr_el:
        lines = [
            line for line in addr_el.get_text(separator="|", strip=True).split("|")
            if line
        ]
        if lines:
            business["Street"] = lines[0]
        if len(lines) >= 2:
            parts = [clean(p) for p in lines[1].split(",")]
            if len(parts) >= 1 and parts[0]:
                business["City"] = parts[0]
            if len(parts) >= 2 and parts[1]:
                business["State"] = parts[1]
            if len(parts) >= 3 and parts[2]:
                business["Zipcode"] = parts[2]
        if len(lines) >= 3 and lines[2]:
            business["Country"] = lines[2]

    # ---- Country fallback (LocalBusiness JSON-LD) ----
    if not business["Country"]:
        for script in soup.find_all("script", type="application/ld+json"):
            if not script.string:
                continue
            try:
                data = json.loads(script.string, strict=False)
            except Exception:
                continue
            graph = data.get("@graph", [data]) if isinstance(data, dict) else data
            if not isinstance(graph, list):
                continue
            for node in graph:
                if not isinstance(node, dict) or node.get("@type") != "LocalBusiness":
                    continue
                country = clean(node.get("address", {}).get("addressCountry", ""))
                if country and country.upper() != "N/A":
                    business["Country"] = country
                break
            if business["Country"]:
                break

    # ---- Phone (click-to-call button, not a dedicated labeled row) ----
    phone_el = soup.select_one(".search_show_phone_txt a[href^='tel:']")
    if phone_el:
        phone_text = clean(phone_el.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text

    # ---- Website URL (icon button, not a dedicated labeled row) ----
    website_el = soup.select_one(".member-search-website a[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()

    # ---- Description ----
    about_el = soup.select_one(".froala-data.field-about_me")
    if about_el:
        desc_paragraphs = [
            clean(p.get_text()) for p in about_el.find_all("p") if clean(p.get_text())
        ]
        if desc_paragraphs:
            business["Description"] = "\n".join(desc_paragraphs)

    # ---- Category (no dedicated field on the page -- read from the
    # breadcrumb item just before the business name) ----
    breadcrumb_items = soup.select("ol.breadcrumb li[itemprop='itemListElement'] span[itemprop='name']")
    if breadcrumb_items:
        cat_text = clean(breadcrumb_items[-1].get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Social Media Links (real anchors, scoped to the profile column
    # so the directory's own sitewide header/footer chrome -- e.g. its
    # Facebook-login button -- doesn't get picked up as the business's) ----
    profile_col = soup.select_one(".col-md-9") or soup
    for a in profile_col.find_all("a", href=True):
        href = a["href"]
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# Site parser: touchafro.com
# ==========================================================

def parse_touchafro(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    name_el = soup.select_one(".reportHeading h3")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Labeled customer_info rows, keyed by their own label text
    # (row div classes repeat across unrelated rows, so they aren't a
    # reliable way to tell rows apart) ----
    info = {}
    for row in soup.select(".customer_info > div"):
        label_el = row.find(class_="headings_extra")
        if not label_el:
            continue
        label = clean(label_el.get_text()).rstrip(":").strip().lower()
        parts = []
        for sib in label_el.next_siblings:
            if isinstance(sib, NavigableString):
                parts.append(str(sib))
            else:
                parts.append(sib.get_text())
        info[label] = clean(" ".join(parts))

    # ---- Address ----
    address = info.get("address", "")
    if address:
        addr_parts = [clean(p) for p in address.split(",")]
        state_zip_match = re.match(r"^(.*\S)\s+(\d{5}(?:-\d{4})?)$", addr_parts[-1]) if addr_parts else None
        if state_zip_match and len(addr_parts) >= 2:
            business["State"] = state_zip_match.group(1)
            business["Zipcode"] = state_zip_match.group(2)
            business["Street"] = ", ".join(addr_parts[:-1])
        else:
            business["Street"] = address

    if info.get("city"):
        business["City"] = info["city"]
    if info.get("country"):
        business["Country"] = info["country"]
    if info.get("phone"):
        business["Phone"] = info["phone"]
    if info.get("website"):
        business["Website URL"] = info["website"]

    # ---- Description  ----
    desc_el = soup.select_one(".description")
    if desc_el:
        desc_paragraphs = [
            clean(p.get_text()) for p in desc_el.find_all("p") if clean(p.get_text())
        ]
        if desc_paragraphs:
            business["Description"] = "\n".join(desc_paragraphs)

    # ---- Category ----
    category_el = soup.select_one(".category_meta a")
    if category_el:
        cat_text = clean(category_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Logo (first gallery-slider image) ----
    logo_el = soup.select_one(".left_thumb.gall-img img[src]") \
        or soup.select_one(".fagsfacf-gallery-slide-inner img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Social Media Links (the business's own "You can also find us
    # on" list -- NOT the footer's or share-widget's TouchAfro-owned
    # links) ----
    social_list = soup.select_one(".follow_social .social_link_btns")
    if social_list:
        for a in social_list.find_all("a", href=True):
            href = a["href"]
            for domain, network in SOCIAL_DOMAINS.items():
                if _hostname_matches_social_domain(href, domain):
                    business["Social Media Links"][network] = href

    return business


# ==========================================================
# Site parser: supplyautonomy.com
# ==========================================================

def parse_supplyautonomy(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    name_el = soup.select_one("[itemprop='name']")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Address (schema.org PostalAddress microdata block) ----
    addr_el = soup.select_one("[itemprop='address']")
    if addr_el:
        street_el = addr_el.select_one("[itemprop='streetAddress']")
        city_el = addr_el.select_one("[itemprop='addressLocality']")
        state_el = addr_el.select_one("[itemprop='addressRegion']")
        zip_el = addr_el.select_one("[itemprop='postalCode']")
        country_el = addr_el.select_one("[itemprop='addressCountry']")
        if street_el:
            business["Street"] = clean(street_el.get_text())
        if city_el:
            business["City"] = clean(city_el.get_text())
        if state_el:
            business["State"] = clean(state_el.get_text())
        if zip_el:
            business["Zipcode"] = clean(zip_el.get_text())
        if country_el:
            business["Country"] = clean(country_el.get_text())

    # ---- Phone ----
    phone_el = soup.select_one("[itemprop='telephone']")
    if phone_el:
        phone_text = clean(phone_el.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text

    # ---- Website URL ----
    website_el = soup.select_one("a[itemprop='url']")
    if website_el and website_el.get("href"):
        business["Website URL"] = website_el["href"]

    # ---- Description ----
    desc_el = soup.select_one("#companyDescription")
    if desc_el:
        desc_text = clean(desc_el.get_text())
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Logo (background-image URL embedded in a style attribute,
    # not an <img src="">) ----
    logo_el = soup.select_one("[itemprop='logo']")
    if logo_el and logo_el.get("style"):
        match = re.search(r"url\(([^)]+)\)", logo_el["style"])
        if match:
            business["Logo"] = urljoin(url, match.group(1).strip("'\""))

    # ---- Social Media Links (only icons that lack the "inactive" class,
    # since unset ones are dummy links to the bare platform homepage) ----
    for a in soup.select(".socialMediaLinks a[href]"):
        classes = a.get("class") or []
        if "inactive" in classes:
            continue
        href = a["href"]
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# Site parser: mybusinessplaces.com
# ==========================================================

def parse_mybusinessplaces(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    name_el = soup.select_one("h1")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    addr_el = soup.select_one("li.lp-details-address")
    if addr_el:
        addr_text = clean(addr_el.get_text())
        parts = [clean(p) for p in addr_text.split(",") if clean(p)]
        if parts and parts[-1].upper() in ("USA", "US", "UNITED STATES"):
            business["Country"] = "United States"
            parts = parts[:-1]
        if len(parts) >= 3:
            business["Street"] = parts[0]
            state_zip_match = re.match(r"^([A-Za-z]{2,})\s+(\d{5}(?:-\d{4})?)$", parts[-1])
            if state_zip_match:
                business["State"] = state_zip_match.group(1)
                business["Zipcode"] = state_zip_match.group(2)
                business["City"] = ", ".join(parts[1:-1])
            else:
                # Last segment isn't "State Zip" -- fall back to treating it
                # as State (no zip found) and everything else as City.
                business["State"] = parts[-1]
                business["City"] = ", ".join(parts[1:-1])
        elif len(parts) == 2:
            business["Street"] = parts[0]
            business["City"] = parts[1]
        elif parts:
            business["Street"] = ", ".join(parts)

    # ---- Phone ----
    phone_el = soup.select_one("li.lp-listing-phone a")
    if phone_el:
        phone_text = clean(phone_el.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text

    # ---- Website URL ----
    website_el = soup.select_one("li.lp-user-web a")
    if website_el and website_el.get("href"):
        business["Website URL"] = website_el["href"]

    # ---- Description ----
    desc_el = soup.select_one(".post-detail-content")
    if desc_el:
        desc_text = clean(desc_el.get_text())
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Category (breadcrumb link between "Home" and the business name)
    for a in soup.select("ul.breadcrumbs li a"):
        text = clean(a.get_text())
        if text and text.lower() != "home":
            business["Category"] = text
            break

    # ---- Hours (opportunistic -- no dedicated widget on this sample
    # listing, but scrape it if a future listing has a table-view-group
    # style hours block) ----
    hours_el = soup.select_one(".lp-listing-hours, .business-hours, .lp-hours-table")
    if hours_el:
        hours_text = clean_multiline(hours_el.get_text())
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    return business


# ==========================================================
# Dispatcher
# ==========================================================

# ==========================================================
# Site parser: local-biz.directory
# ==========================================================

def parse_localbizdirectory(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    name_el = soup.select_one("h1.title")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Address / Category / Keywords (table.ar_desc labeled rows) ----
    for row in soup.select("table.ar_desc tr"):
        label_el = row.select_one("td.label")
        value_el = row.select_one("td:not(.label)")
        if not label_el or not value_el:
            continue
        label = clean(label_el.get_text()).rstrip(":").strip()

        if label == "Address":
            addr_text = clean(value_el.get_text())
            parts = [clean(p) for p in addr_text.split(",") if clean(p)]
            if parts and parts[-1].upper() in ("USA", "US", "UNITED STATES"):
                business["Country"] = "United States"
                parts = parts[:-1]
            # A standalone suite/unit/floor segment (e.g. "131 Continental Dr,
            # Suite 305, Newark, DE 19713") belongs with the street line, not
            # the city -- merge it back in before splitting street/city/state.
            if len(parts) >= 2 and re.match(
                r"^(suite|ste|unit|apt|apartment|#|bldg|building|floor|fl)\b",
                parts[1], re.I
            ):
                parts = [f"{parts[0]}, {parts[1]}"] + parts[2:]
            if len(parts) >= 3:
                business["Street"] = parts[0]
                state_zip_match = re.match(r"^([A-Za-z]{2,})\s+(\d{5}(?:-\d{4})?)$", parts[-1])
                if state_zip_match:
                    business["State"] = state_zip_match.group(1)
                    business["Zipcode"] = state_zip_match.group(2)
                    business["City"] = ", ".join(parts[1:-1])
                else:
                    business["City"] = ", ".join(parts[1:-1])
                    business["State"] = parts[-1]
            elif len(parts) == 2:
                business["Street"] = parts[0]
                business["City"] = parts[1]
            elif parts:
                business["Street"] = ", ".join(parts)

        elif label == "Category":
            cat_link = value_el.select_one("a")
            cat_text = clean(cat_link.get_text()) if cat_link else clean(value_el.get_text())
            if is_meaningful(cat_text):
                business["Category"] = cat_text

        elif label == "Tag":
            tag_links = value_el.select("a")
            if tag_links:
                keywords = ", ".join(clean(a.get_text()) for a in tag_links if clean(a.get_text()))
            else:
                keywords = clean(value_el.get_text())
            if is_meaningful(keywords):
                business["Keywords"] = keywords

    # ---- Phone -----
    tab_content = soup.select_one("#popular .tab_content")
    if tab_content:
        paragraphs = tab_content.find_all("p", recursive=False)
        desc_parts = []
        label_map = {
            "phone": "Phone",
            "website": "Website URL",
            "owner name": "Owner Name",
            "business email": "Business Email",
            "email": "Business Email",
            "about us": "Description",
        }
        i = 0
        while i < len(paragraphs):
            label_key = clean(paragraphs[i].get_text()).rstrip(":").strip().lower()
            field = label_map.get(label_key)

            if field and i + 1 < len(paragraphs):
                if field == "Description":
                    # Collect every paragraph after "About Us:" as the
                    # description (some listings wrap it across more than
                    # one <p>).
                    for p in paragraphs[i + 1:]:
                        p_text = clean(p.get_text())
                        if is_meaningful(p_text):
                            desc_parts.append(p_text)
                    break
                elif field == "Website URL":
                    link = paragraphs[i + 1].select_one("a")
                    if link and link.get("href"):
                        business["Website URL"] = link["href"]
                    elif is_meaningful(clean(paragraphs[i + 1].get_text())):
                        business["Website URL"] = clean(paragraphs[i + 1].get_text())
                elif field == "Phone":
                    phone_text = clean(paragraphs[i + 1].get_text())
                    if is_meaningful(phone_text):
                        business["Phone"] = phone_text
                elif field == "Owner Name":
                    owner_text = clean(paragraphs[i + 1].get_text())
                    if is_meaningful(owner_text):
                        business["Owner Name"] = owner_text
                elif field == "Business Email":
                    email_link = paragraphs[i + 1].select_one("a[href^=mailto]")
                    if email_link:
                        business["Business Email"] = clean(email_link.get_text())
                    else:
                        email_text = clean(paragraphs[i + 1].get_text())
                        if is_meaningful(email_text):
                            business["Business Email"] = email_text
                i += 2
                continue

            # Not a recognized label -- e.g. the unlabeled description
            # paragraph that some listings put first. Treat it as
            # description text rather than skipping it.
            p_text = clean(paragraphs[i].get_text())
            if is_meaningful(p_text):
                desc_parts.append(p_text)
            i += 1
        if desc_parts:
            business["Description"] = "\n".join(desc_parts)

    # ---- Logo (JSON-LD WebPage image, falling back to the slider image) ----
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        graph = data.get("@graph", []) if isinstance(data, dict) else []
        for node in graph:
            image = node.get("image") if isinstance(node, dict) else None
            if isinstance(image, dict) and image.get("url"):
                business["Logo"] = urljoin(url, image["url"])
                break
        if business["Logo"]:
            break
    if not business["Logo"]:
        slider_img = soup.select_one(".article_slider .flexslider img")
        if slider_img and slider_img.get("src"):
            business["Logo"] = urljoin(url, slider_img["src"])

    return business


# ==========================================================
# Site parser: vetslist.com
# ==========================================================

def parse_vetslist(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".member_profile h1.bold") or soup.select_one("h1.bold")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Phone ----
    phone_span = soup.select_one('span[itemprop="telephone"]')
    if phone_span:
        phone_text = clean(phone_span.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text

    # ---- Address  ----
    addr_span = soup.select_one('span[itemprop="streetAddress"]')
    if addr_span:
        addr_text = clean(addr_span.get_text())
        if is_meaningful(addr_text):
            street, city, state, zipcode = _split_blinx_address(addr_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    # ---- Country ----
    intro_p = soup.select_one("p.line-height-xl.nomargin")
    if intro_p:
        intro_lines = clean_multiline(intro_p.get_text(separator="\n")).split("\n")
        if len(intro_lines) >= 2:
            country_text = intro_lines[-1]
            if is_meaningful(country_text):
                business["Country"] = country_text

    # ---- Category (breadcrumb crumb right before the current-page
    # business name; "Home"/root crumbs are excluded) ----
    crumbs = [
        clean(li.get_text())
        for li in soup.select("ol.breadcrumb li[itemprop='itemListElement']")
    ]
    crumbs = [c for c in crumbs if c]
    if len(crumbs) >= 2:
        business["Category"] = crumbs[-1]

    # ---- Website URL & Description ----
    about = soup.select_one(".textarea.textarea-about_me")
    if about:
        paragraphs = [clean(p.get_text()) for p in about.find_all("p")]
        desc_parts = []
        i = 0
        while i < len(paragraphs):
            label = paragraphs[i].rstrip(":").strip().lower()
            if label in ("url", "website") and i + 1 < len(paragraphs):
                url_text = paragraphs[i + 1]
                if is_meaningful(url_text):
                    business["Website URL"] = url_text
                i += 2
                continue
            if label == "about us" and i + 1 < len(paragraphs):
                # Collect every remaining paragraph as the description --
                # some listings wrap it across more than one <p>.
                for p_text in paragraphs[i + 1:]:
                    if is_meaningful(p_text):
                        desc_parts.append(p_text)
                break
            i += 1
        if desc_parts:
            business["Description"] = "\n".join(desc_parts)

    # ---- Logo (dedicated itemprop, falling back to og:image) ----
    logo_img = soup.select_one('img[itemprop="logo"]')
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])

    if not business["Logo"]:
        og_image = soup.select_one('meta[property="og:image"]')
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- GBP Link ("Get Directions" Google Maps anchor) ----
    directions = soup.select_one("a.get-directions-link[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    return business


# ==========================================================
# Site parser: vymaps.com
# ==========================================================

def _vymaps_jsonld(soup):
    """Return the first LocalBusiness JSON-LD object on the page, if any."""
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if isinstance(obj, dict) and obj.get("@type") == "LocalBusiness":
                return obj
    return {}


def parse_vymaps(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    jsonld = _vymaps_jsonld(soup)

    # ---- Business Name ----
    h1 = soup.select_one(".profile-cover-content h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"] and jsonld.get("name"):
        business["Business Name"] = clean(jsonld["name"])

    # ---- Address  ----
    addr_link = soup.select_one("a.listing-address[href]")
    if addr_link:
        addr_text = clean(addr_link.get_text())
        if is_meaningful(addr_text):
            street, city, state, zipcode = _split_blinx_address(addr_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode
        if _is_maps_link(addr_link["href"]):
            business["GBP Link"] = addr_link["href"]

    # ---- Country (JSON-LD only; never rendered as visible page text) ----
    addr_obj = jsonld.get("address")
    if isinstance(addr_obj, dict) and addr_obj.get("addressCountry"):
        business["Country"] = clean(addr_obj["addressCountry"])

    # ---- Phone ----
    tel = soup.select_one('a[href^="tel:"]')
    if tel and tel.get("href"):
        business["Phone"] = tel["href"].replace("tel:", "").strip()
    if not business["Phone"] and jsonld.get("telephone"):
        business["Phone"] = clean(jsonld["telephone"])

    # ---- Website URL ----
    site_link = soup.select_one('a[aria-label="Website"][href]')
    if site_link and site_link.get("href"):
        business["Website URL"] = site_link["href"]
    if not business["Website URL"] and jsonld.get("url"):
        business["Website URL"] = jsonld["url"]

    # ---- Business Email (Cloudflare-obfuscated) ----
    email = _find_cf_email(soup)
    if email:
        business["Business Email"] = email

    # ---- Description & Keywords ----
    about = soup.select_one("div.listing-title-bar")
    if about:
        paragraphs = about.find_all("p", recursive=False)
        for i, p in enumerate(paragraphs):
            text = clean(p.get_text())
            if not is_meaningful(text):
                continue
            tags_match = re.match(r"^Tags\s*:\s*(.*)$", text, flags=re.I)
            if tags_match:
                tags_text = tags_match.group(1).strip()
                if is_meaningful(tags_text):
                    business["Keywords"] = ", ".join(
                        tag.lstrip("#").strip()
                        for tag in tags_text.split()
                        if tag.lstrip("#").strip()
                    )
                continue
            if i == 0:
                continue
            if not business["Description"]:
                business["Description"] = text

    if not business["Description"] and jsonld.get("description"):
        desc_text = clean(jsonld["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Category (single hero badge, not a breadcrumb trail) ----
    cat_tag = soup.select_one("span.category-tag")
    if cat_tag:
        cat_text = clean(cat_tag.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Photos  ----
    photos = []
    for img in soup.select("ul.gallery-list img[src]"):
        if not img.get("src"):
            continue
        src = urljoin(url, img["src"])
        if src not in photos:
            photos.append(src)
    if photos:
        business["Photos"] = photos

    return business


# ==========================================================
# Site parser: wireanium.com
# ==========================================================

def parse_wireanium(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Address (split across individual <span> elements: street, city,
    # state, zip, with a trailing plain-text country after the final
    # <br>) ----
    addr_container = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_container:
        addr_spans = addr_container.find_all("span", recursive=False)
        if len(addr_spans) >= 4:
            business["Street"] = clean(addr_spans[0].get_text())
            business["City"] = clean(addr_spans[1].get_text())
            business["State"] = clean(addr_spans[2].get_text())
            business["Zipcode"] = clean(addr_spans[3].get_text())
        elif not business["Street"]:
            addr_text = clean(addr_container.get_text())
            if is_meaningful(addr_text):
                business["Street"] = addr_text

        trailing_text_nodes = [
            clean(node) for node in addr_container.contents
            if isinstance(node, NavigableString) and clean(node) and clean(node) != ","
        ]
        if trailing_text_nodes:
            country_text = trailing_text_nodes[-1]
            if country_text:
                business["Country"] = country_text

    # ---- Country/Phone fallback (LocalBusiness node inside the page's
    # JSON-LD "@graph" array) ----
    jsonld_local_business = {}
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue
        graph = data.get("@graph", [data]) if isinstance(data, dict) else data
        if not isinstance(graph, list):
            continue
        for node in graph:
            if isinstance(node, dict) and node.get("@type") == "LocalBusiness":
                jsonld_local_business = node
                break
        if jsonld_local_business:
            break

    if not business["Country"]:
        country = clean(jsonld_local_business.get("address", {}).get("addressCountry", ""))
        if country and country.upper() != "N/A":
            business["Country"] = country

    # ---- Phone  ----
    phone_link = soup.select_one(".table-display-phone a[href^='tel:']")
    if phone_link and phone_link.get("href"):
        business["Phone"] = phone_link["href"].replace("tel:", "").strip()
    if not business["Phone"] and jsonld_local_business.get("telephone"):
        business["Phone"] = clean(jsonld_local_business["telephone"])

    # ---- Website URL (dedicated labeled row) ----
    website_el = soup.select_one(".table-display-website .weblink[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()

    # ---- Description (the About block on this skin holds only plain
    # paragraph text -- Phone/Website have their own dedicated rows
    # above) ----
    about_el = soup.select_one(".froala-data.field-about_me")
    if about_el:
        desc_paragraphs = [
            clean(p.get_text()) for p in about_el.find_all("p") if clean(p.get_text())
        ]
        if desc_paragraphs:
            business["Description"] = "\n".join(desc_paragraphs)

    # ---- Hours (opportunistic; not every listing on this source
    # publishes one) ----
    hours_el = soup.select_one(".table-display-hours")
    if hours_el:
        hours_text = clean(hours_el.get_text())
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        cat_text = clean(category_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Social Media Links (opportunistic; not every listing on this
    # source publishes any) ----
    social_links = {}
    for a in soup.select(".table-display-social_media_links a[href]"):
        href = a.get("href", "")
        for domain, name in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                social_links[name] = href
    if social_links:
        business["Social Media Links"] = social_links

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Business Email (opportunistic; not every listing publishes one) ----
    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email
    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto and mailto.get("href"):
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- GBP Link (scoped to the "Get Directions" anchor) ----
    directions = soup.select_one("a.get-directions-link[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    return business


# ==========================================================
# Site parser: locuul.com
# ==========================================================

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Matches "Street[, Suite/Unit ...], City, State Zip" -- the unstructured
# single-string address shape used on listings like haqq-legal-ai and
# focal. Street is greedy so it absorbs any internal commas (e.g. a
# "Suite 305" segment); only the LAST two comma-separated segments are
# required to be City and "State Zip".
_LOCUUL_ADDRESS_RE = re.compile(
    r"^(?P<street>.+),\s*(?P<city>[^,]+),\s*"
    r"(?P<state>[A-Za-z][A-Za-z .]*?)\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


def parse_locuul(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one("h1.bold.inline-block")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        company = soup.select_one(".table-display-company .textbox-company")
        if company:
            business["Business Name"] = clean(company.get_text())

    # ---- Address  ----
    addr_container = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_container:
        addr_spans = addr_container.find_all("span", recursive=False)
        if len(addr_spans) >= 4:
            business["Street"] = clean(addr_spans[0].get_text())
            business["City"] = clean(addr_spans[1].get_text())
            business["State"] = clean(addr_spans[2].get_text())
            business["Zipcode"] = clean(addr_spans[3].get_text())
        elif not business["Street"]:
            addr_text = clean(addr_container.get_text())
            if is_meaningful(addr_text):
                match = _LOCUUL_ADDRESS_RE.match(addr_text)
                if match:
                    business["Street"] = clean(match.group("street"))
                    business["City"] = clean(match.group("city"))
                    business["State"] = clean(match.group("state"))
                    business["Zipcode"] = match.group("zip")
                else:
                    business["Street"] = addr_text

        trailing_text_nodes = [
            clean(node) for node in addr_container.contents
            if isinstance(node, NavigableString) and clean(node) and clean(node) != ","
        ]
        if trailing_text_nodes:
            country_text = trailing_text_nodes[-1]
            if country_text:
                business["Country"] = country_text

    # ---- Country/Phone fallback (LocalBusiness node inside the page's
    # JSON-LD "@graph" array) ----
    jsonld_local_business = {}
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue
        graph = data.get("@graph", [data]) if isinstance(data, dict) else data
        if not isinstance(graph, list):
            continue
        for node in graph:
            if isinstance(node, dict) and node.get("@type") == "LocalBusiness":
                jsonld_local_business = node
                break
        if jsonld_local_business:
            break

    if not business["Country"]:
        country = clean(jsonld_local_business.get("address", {}).get("addressCountry", ""))
        if country and country.upper() != "N/A":
            business["Country"] = country

    # ---- Phone  ----
    phone_el = soup.select_one(".table-display-phone .col-sm-8")
    if phone_el:
        phone_text = clean(phone_el.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text
    if not business["Phone"]:
        phone_link = soup.select_one(".table-display-phone a[href^='tel:']")
        if phone_link and phone_link.get("href"):
            business["Phone"] = phone_link["href"].replace("tel:", "").strip()
    if not business["Phone"] and jsonld_local_business.get("telephone"):
        business["Phone"] = clean(jsonld_local_business["telephone"])

    # ---- Website URL (dedicated labeled row) ----
    website_el = soup.select_one(".table-display-website .weblink[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()

    # ---- Description  ----
    about_el = soup.select_one(".froala-data.field-about_me")
    about_text = ""
    if about_el:
        about_text = clean_multiline(about_el.get_text(separator="\n"))
        if is_meaningful(about_text):
            business["Description"] = about_text

    if not business["Description"] and jsonld_local_business.get("description"):
        desc_text = clean(jsonld_local_business["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Hours  ----
    for row in soup.select(".table-view-group"):
        label = row.select_one(".col-sm-4")
        value = row.select_one(".col-sm-8")
        if label and value and clean(label.get_text()).lower() == "hours of operation":
            hours_text = clean(value.get_text())
            if is_meaningful(hours_text):
                business["Hours"] = hours_text
            break

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        cat_text = clean(category_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    if not business["Category"]:
        crumbs = [clean(li.get_text()) for li in soup.select("ol.breadcrumb li")]
        crumbs = [c for c in crumbs if c and c.lower() != "home"]
        if len(crumbs) >= 2:
            business["Category"] = crumbs[-1]

    # ---- Social Media Links (opportunistic; not every listing on this
    # source publishes any) ----
    social_links = {}
    for a in soup.select(".table-display-social-links a[href]"):
        href = a.get("href", "")
        for domain, name in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                social_links[name] = href
    if social_links:
        business["Social Media Links"] = social_links

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Business Email ----
    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email
    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto and mailto.get("href"):
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()
    if not business["Business Email"] and about_text:
        email_match = _EMAIL_RE.search(about_text)
        if email_match:
            business["Business Email"] = email_match.group(0)

    # ---- GBP Link (scoped to the "Get Directions" anchor) ----
    directions = soup.select_one("a.get-directions-link[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    return business


# ==========================================================
# Site parser: dbesearch.com
# ==========================================================

_DBESEARCH_CITY_STATE_ZIP_RE = re.compile(
    r"^(?P<city>.+?),\s*(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _split_dbesearch_address(address_text):
    """Splits the clean_multiline()'d contents of .business_address into
    Street / City / State / Zipcode. Expected shape (line 1 = street,
    line 2 = "City, ST 12345"), e.g.:
        300 Triple Diamond Blvd
        Nokomis, FL 34275
    """
    street, city, state, zipcode = "", "", "", ""

    lines = [clean(line) for line in address_text.split("\n") if clean(line)]
    if not lines:
        return street, city, state, zipcode

    street = lines[0]

    if len(lines) >= 2:
        match = _DBESEARCH_CITY_STATE_ZIP_RE.match(lines[1])
        if match:
            city = match.group("city").strip()
            state = match.group("state").strip()
            zipcode = match.group("zip").strip()
        else:
            city = lines[1]

    return street, city, state, zipcode


def parse_dbesearch(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    jumbotron = soup.select_one(".jumbotron")

    # ---- Name ----
    name_el = jumbotron.select_one("h1") if jumbotron else soup.select_one("h1")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Category (the <p><b>Category: </b>Other</p> block) ----
    if jumbotron:
        for p in jumbotron.find_all("p"):
            b = p.find("b")
            if b and "category" in clean(b.get_text()).lower():
                full_text = clean(p.get_text())
                label = clean(b.get_text())
                business["Category"] = full_text[len(label):].strip(" :")
                break

    # ---- Logo ----
    logo_el = soup.select_one('img[name^="logo_"]')
    if logo_el and logo_el.get("src"):
        business["Logo"] = urljoin(url, logo_el["src"])

    # ---- Website URL ----
    website_el = soup.select_one("a.business-web-link[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()

    # ---- Street / City / State / Zipcode ----
    address_el = soup.select_one(".business_address")
    if address_el:
        # <br> splits the address into two separate text nodes (street,
        # then "City, ST Zip"); separator="\n" joins them one per line.
        address_text = address_el.get_text(separator="\n")
        street, city, state, zipcode = _split_dbesearch_address(address_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone ----
    phone_el = soup.select_one('.business_contact_phone a[href^="tel:"]')
    if phone_el:
        business["Phone"] = clean(phone_el.get_text()) or phone_el["href"].replace("tel:", "").strip()

    return business


# ==========================================================
# Site parser: qdexx.com
# ==========================================================

# Some qdexx listings have no dedicated phone field/element on the page at
# all -- the business owner instead crammed it into the free-text "About"
# description as a literal "Phone:\n<number>" line. This is the only place
# a phone number appears on such listings, so it's extracted from there
# as a labeled fallback (not a blind scan of arbitrary page text).
_QDEXX_PHONE_LABEL_RE = re.compile(r"Phone:\s*([\d][\d\-.\s()]{6,}\d)", re.I)


def _qdexx_load_json_ld(soup):
    """Returns (main_business_dict, breadcrumb_dict) from the page's
    JSON-LD <script> tags."""
    main_ld, breadcrumb_ld = None, None
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            # strict=False: some qdexx listings embed literal, unescaped
            # newlines inside JSON string values (e.g. a multi-line
            # description), which strict JSON rejects as a control
            # character but the site's own templating clearly intends
            # as part of the string.
            data = json.loads(script.string, strict=False)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("@type") == "BreadcrumbList":
            breadcrumb_ld = data
        elif "address" in data or "name" in data:
            main_ld = data
    return main_ld, breadcrumb_ld


def parse_qdexx(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    main_ld, breadcrumb_ld = _qdexx_load_json_ld(soup)

    # ---- Name / Description / Address / Website (JSON-LD, primary) ----
    if main_ld:
        business["Business Name"] = html_lib.unescape(clean(main_ld.get("name", "")))
        business["Description"] = html_lib.unescape(clean(main_ld.get("description", "")))
        website = main_ld.get("url", "")
        if website:
            business["Website URL"] = website.strip()

        address = main_ld.get("address") or {}
        business["Street"] = clean(address.get("streetAddress", ""))
        business["City"] = clean(address.get("addressLocality", ""))
        business["State"] = clean(address.get("addressRegion", ""))
        business["Zipcode"] = clean(str(address.get("postalCode", "")))

    # ---- Name (DOM fallback) ----
    if not business["Business Name"]:
        h1 = soup.select_one(".tileOverlay h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Description (DOM fallback -- "About" tile) ----
    if not business["Description"]:
        about_p = soup.select_one("p.pre")
        if about_p:
            business["Description"] = clean(about_p.get_text())

    # ---- Category (breadcrumb JSON-LD: second-to-last item, since the
    #      last item is the business listing itself) ----
    if breadcrumb_ld:
        items = breadcrumb_ld.get("itemListElement") or []
        if len(items) >= 2:
            cat_item = items[-2].get("item") or {}
            cat_name = clean(cat_item.get("name", ""))
            if cat_name:
                business["Category"] = cat_name

    # ---- Category (DOM fallback -- tagline tile, e.g. "Lawyer in Dover DE") ----
    if not business["Category"]:
        tagline_h2 = soup.select_one("li.tagline h2")
        if tagline_h2:
            text = clean(tagline_h2.get_text())
            match = re.match(r"^(.*?)\s+in\s+.+$", text, re.I)
            if match:
                business["Category"] = match.group(1).strip()

    # ---- Website URL (DOM fallback -- "Online" tile) ----
    if not business["Website URL"]:
        for li in soup.select("li.tile.bp"):
            h3 = li.find("h3")
            if h3 and clean(h3.get_text()).lower() == "online":
                link = li.select_one("a[href]")
                if link:
                    business["Website URL"] = link["href"].strip()
                break

    # ---- Hours ("Hours of Operation" tile) ----
    for li in soup.select("li.tile.bp"):
        h3 = li.find("h3")
        if h3 and clean(h3.get_text()).lower() == "hours of operation":
            p = li.find("p")
            if p:
                lines = [clean(l) for l in p.get_text(separator="\n").split("\n") if clean(l)]
                if lines:
                    business["Hours"] = "; ".join(lines)
            break

    # ---- Phone (labeled fallback out of the About description -- this
    #      site provides no dedicated phone field/element for this listing) ----
    phone_source = business["Description"]
    if not phone_source:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            phone_source = meta_desc.get("content", "")
    if phone_source:
        phone_match = _QDEXX_PHONE_LABEL_RE.search(phone_source)
        if phone_match:
            business["Phone"] = clean(phone_match.group(1))

    return business


# ==========================================================
# Site parser: letsknowit.com
# ==========================================================

def _letsknowit_detail_value(details, label_keyword):
    """The '.companyDetails' block is a flat list of <h3><label>icon</label>
    <label><small>Label:</small></label><span>value</span></h3> rows (e.g.
    Headquarter, Phone, Company Size). Finds the row whose <small> label
    text matches label_keyword and returns its <span> value."""
    if not details:
        return None
    for h3 in details.find_all("h3"):
        label_el = h3.find("small")
        if label_el and label_keyword.lower() in clean(label_el.get_text()).lower():
            span = h3.find("span")
            if span:
                return clean(span.get_text())
    return None


def _letsknowit_address_row(details):
    """The '.companyDetails' block's *first* <h3> is unlabeled -- its
    <label> holds only the map-marker icon (no <small> caption) -- and its
    <span> holds the full 'Street, City, State Zip, Country' address. This
    is the real address; the separate 'Headquarter:' row is almost always
    just an unset 'N/A' placeholder and should only be used as a fallback."""
    if not details:
        return None
    for h3 in details.find_all("h3"):
        if h3.find("small"):
            continue  # a labeled row (Headquarter, Phone, Company Size, Website)
        if not h3.select_one("i.fa-map-marker"):
            continue
        span = h3.find("span")
        if span:
            text = clean(span.get_text())
            if text:
                return text
    return None


_LETSKNOWIT_COUNTRY_SUFFIX_RE = re.compile(
    r",\s*(united states(?: of america)?|usa|us)\s*$", re.I
)


def _letsknowit_split_address(address):
    """_split_blinx_address expects 'Street, City, State Zip' (3 parts),
    but letsknowit renders 'Street, City, State Zip, Country' (4 parts),
    which shifts city/state/zip off by one. Strip the trailing country
    first so the shared splitter parses it correctly."""
    address = _LETSKNOWIT_COUNTRY_SUFFIX_RE.sub("", address).strip()
    return _split_blinx_address(address)


def _letsknowit_address_quality(text):
    """Score how "address-like" a candidate string is. Listings are
    inconsistent about which of the two rows (map-marker vs 'Headquarter:')
    holds the real address and which holds a sparse/placeholder value --
    sometimes it's 'Street, City, State Zip' in one and 'City, Country' (or
    'N/A') in the other, sometimes it's reversed. Rather than trusting one
    row by position, score both and keep the more complete one: more
    comma-separated segments is better, and a segment that ends in digits
    (a zip code) is a strong signal of a real, complete address. Returns
    -1 for missing/placeholder text so it always loses to a real address."""
    if not text or text.strip().upper() == "N/A":
        return -1
    address = _LETSKNOWIT_COUNTRY_SUFFIX_RE.sub("", text).strip()
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return -1
    score = len(parts)
    if re.search(r"\d", parts[-1]):
        score += 1
    return score


def parse_letsknowit(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Name ----
    name_el = soup.select_one(".userProfileName h1")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    details = soup.select_one(".companyDetails.profilegeneraldetail")

    # ---- Street / City / State / Zipcode ----
    map_row_text = _letsknowit_address_row(details)
    headquarter_text = _letsknowit_detail_value(details, "headquarter")
    address_text = max(
        (map_row_text, headquarter_text),
        key=_letsknowit_address_quality,
    )

    if _letsknowit_address_quality(address_text) >= 0:
        street, city, state, zipcode = _letsknowit_split_address(address_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone ("Phone:" row) ----
    phone = _letsknowit_detail_value(details, "phone")
    if phone:
        business["Phone"] = phone

    # ---- Phone (sidebar "Contact Details" widget fallback) ----
    if not business["Phone"]:
        tel = soup.select_one('.widget.personal-info a[href^="tel:"] span')
        if tel:
            business["Phone"] = clean(tel.get_text())

    # ---- Website URL ("Website:" row, marked with the tl_exp class) ----
    if details:
        website_span = details.select_one("h3 span.tl_exp")
        if website_span:
            link = website_span.find("a", href=True)
            if link:
                business["Website URL"] = link["href"].strip()

    # ---- Business Email (sidebar "Contact Details" widget -- the mailto
    #      href itself is blanked out client-side, so read the visible
    #      span text instead) ----
    email_anchor = soup.select_one('.widget.personal-info a[href^="mailto:"]')
    if email_anchor:
        span = email_anchor.find("span")
        email_text = clean(span.get_text()) if span else ""
        if not email_text:
            email_text = email_anchor["href"].replace("mailto:", "").strip()
        if "@" in email_text:
            business["Business Email"] = email_text

    # ---- Description ("About <Name>" block; site emits invalid nested
    #      <p><p>...</p></p> markup, so pull text from the container
    #      rather than a single <p> match) ----
    about = soup.select_one("#aboutcontent")
    if about:
        text = clean(about.get_text(separator=" "))
        if is_meaningful(text):
            business["Description"] = text

    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])
    if not business["Logo"]:
        logo_img = soup.select_one(".profile-pic img[src]")
        if logo_img:
            business["Logo"] = urljoin(url, logo_img["src"])

    # ---- Photos (gallery section shows an "empty_message" placeholder
    #      instead of images when nothing has been uploaded) ----
    gallery = soup.select_one("#companyGalleryContent")
    if gallery and not gallery.select_one(".empty_message"):
        photos = []
        for img in gallery.select("img[src]"):
            src = urljoin(url, img["src"])
            if src not in photos:
                photos.append(src)
        if photos:
            business["Photos"] = photos

    return business


# ==========================================================
# Site parser: metriteweb.com
# ==========================================================

def parse_metriteweb(url, html):
    """metriteweb.com runs the WordPress "Classified Listing" (rtcl)
    plugin's default listing template. Every field lives under
    predictable rtcl-* / listingDetails-* classes."""

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Name ----
    name_el = soup.select_one(".listingDetails-header__heading")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Category ----
    cat_el = soup.select_one("a.listingDetails-header__tag")
    if cat_el:
        cat_text = clean(cat_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Description ----
    desc_el = soup.select_one(".listingDetails-block__des__text")
    if desc_el:
        text = clean(desc_el.get_text(separator=" "))
        if is_meaningful(text):
            business["Description"] = text

    # ---- Street / City / State / Zipcode (the address is the first,
    #      link-less <li> in the "Posted By" info-list -- the other two
    #      <li>s are the phone and website links) ----
    addr_li = soup.select_one(".rtcl-listing-user-info .info-list li")
    if addr_li and not addr_li.find("a"):
        addr_text = clean(addr_li.get_text())
        if addr_text:
            street, city, state, zipcode = _split_blinx_address(addr_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    # ---- Phone ----
    phone_link = soup.select_one("a.rtcl-phone-link")
    if phone_link:
        business["Phone"] = clean(phone_link.get_text())

    # ---- Website URL ----
    site_link = soup.select_one("a.rtcl-website-link")
    if site_link and site_link.get("href"):
        business["Website URL"] = urljoin(url, site_link["href"].strip())

    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])

    return business


# ==========================================================
# Site parser: closelocation.com
# ==========================================================

def parse_closelocation(url, html):
    """closelocation.com business profile pages. Core fields sit in two
    static blocks -- the ".address_box" (address/phone/email/country,
    identified by their fa-* icons rather than position, since a missing
    field just drops its <p>) and the ".card" containing "About Us"
    (owner name / website / description, labelled by <strong> tags).
    The page also emits invalid nested <p><p>...</p></p> markup here,
    but lxml auto-closes the outer tag so every field ends up as a
    sibling <p> we can walk in document order."""

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    if _looks_blocked(html):
        return business

    # ---- Name ----
    name_el = soup.select_one(".title_box h1")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Category (banner shows "<Category> |  ID: ..."  --
    #      category is whatever precedes the "|" separator) ----
    cat_el = soup.select_one(".title_box .text-sm.text-uppercase")
    if cat_el:
        cat_text = clean(clean(cat_el.get_text()).split("|")[0])
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    address_box = soup.select_one(".address_box")

    # ---- Street / City / State / Zipcode ----
    if address_box:
        map_icon = address_box.select_one(".fa-map")
        addr_p = map_icon.find_parent("p") if map_icon else None
        if addr_p:
            addr_text = clean(addr_p.get_text())
            if is_meaningful(addr_text):
                street, city, state, zipcode = _split_blinx_address(addr_text)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode

    # ---- Phone ----
    if address_box:
        phone_icon = address_box.select_one(".fa-phone")
        phone_p = phone_icon.find_parent("p") if phone_icon else None
        if phone_p:
            phone_text = clean(phone_p.get_text())
            if is_meaningful(phone_text):
                business["Phone"] = phone_text

    # ---- Business Email ----
    if address_box:
        email_icon = address_box.select_one(".fa-envelope")
        email_p = email_icon.find_parent("p") if email_icon else None
        if email_p:
            email_text = clean(email_p.get_text())
            if "@" in email_text:
                business["Business Email"] = email_text

    # ---- Country (line reads "United States,   ,   |   " -- only the
    #      first comma-separated segment is populated) ----
    if address_box:
        country_icon = address_box.select_one(".fa-building-o")
        if country_icon:
            country_text = clean(country_icon.get_text())
            country = clean(country_text.split(",")[0])
            if is_meaningful(country):
                business["Country"] = country

    # ---- About Us card: Owner Name / Website / Description ----
    # This card's field labels are inconsistent across listings --
    # confirmed the "Website:" label is sometimes "URL:" instead
    # (wrightway-emergency-services), and some listings skip the
    # "About Us:" label entirely, starting straight in with the
    # description paragraph before any label at all (haqq-legal-ai).
    # Matching labels by substring (rather than an exact string) and
    # defaulting the very first, still-unlabeled paragraph(s) to the
    # description section handles both without misreading the other
    # recognized labels.
    about_card = None
    for div in soup.select(".col-md-9.card"):
        h4 = div.find("h4")
        if h4 and "about us" in clean(h4.get_text()).lower():
            about_card = div
            break

    if about_card:
        section = "description"  # default: unlabeled leading text is description
        desc_parts = []
        for p in about_card.find_all("p"):
            strong = p.find("strong")
            if strong:
                label = clean(strong.get_text()).rstrip(":").lower()
                if "owner" in label:
                    section = "owner"
                elif "website" in label or "url" in label:
                    section = "website"
                elif "about" in label:
                    section = "description"
                else:
                    # Covers "Related Searches:" and any other label
                    # we don't specifically capture -- stop collecting
                    # into Description rather than risk pulling in
                    # unrelated trailing content (e.g. keyword lists).
                    section = None
                continue

            if section == "owner":
                text = clean(p.get_text())
                if is_meaningful(text):
                    business["Owner Name"] = text
                section = None
            elif section == "website":
                link = p.find("a", href=True)
                if link:
                    business["Website URL"] = urljoin(url, link["href"].strip())
                else:
                    text = clean(p.get_text())
                    if is_meaningful(text):
                        business["Website URL"] = text
                section = None
            elif section == "description":
                text = clean(p.get_text())
                if is_meaningful(text):
                    desc_parts.append(text)

        if desc_parts:
            business["Description"] = "\n\n".join(desc_parts)

    # ---- Logo ----
    logo_img = soup.select_one(".logo_main_box img[src]")
    if logo_img:
        business["Logo"] = urljoin(url, logo_img["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Photos (banner's CSS background-image slider) ----
    for slider in soup.select(".slider_box"):
        style = slider.get("style", "")
        if "background-image" not in style:
            continue
        match = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
        if match and match.group(1):
            business["Photos"] = [urljoin(url, match.group(1))]
        break

    return business


SITE_PARSERS = {
    "letsknowit.com": ("requests", parse_letsknowit),
    "metriteweb.com": ("requests", parse_metriteweb),
    "qdexx.com": ("requests", parse_qdexx),
    "dbesearch.com": ("requests", parse_dbesearch),
    "locuul.com": ("requests", parse_locuul),
    "bpublic.com": ("requests", parse_bpublic),
    "smallbusinessusa.com": ("playwright", parse_smallbusinessusa),
    "zeemaps.com": ("api", parse_zeemaps),
    "callupcontact.com": ("requests", parse_callupcontact),
    "zumvu.com": ("playwright", parse_zumvu),
    "blinx.biz": ("playwright", parse_blinx),
    "place123.net": ("requests", parse_place123),
    "freelistingusa.com": ("requests", parse_freelistingusa),
    "askmap.net": ("requests", parse_askmap),
    "earthmom.org": ("requests", parse_earthmom),
    "gravitysplash.com": ("requests", parse_gravitysplash),
    "webforcompany.com": ("requests", parse_webforcompany),
    "provenexpert.com": ("requests", parse_provenexpert),
    "zipleaf.us": ("requests", parse_zipleaf),
    "cataloxy.us": ("requests", parse_cataloxy),
    "fyple.com": ("requests", parse_fyple),
    "merchantcircle.com": ("requests", parse_merchantcircle),
    "globalbusinessdirectory.us": ("requests", parse_globalbusinessdirectory),
    "listings.globalbusinessdirectory.us": ("requests", parse_listings_globalbusinessdirectory),
    "usa.globalbusinessdirectory.us": ("requests", parse_listings_globalbusinessdirectory),
    "cities.globalbusinessdirectory.us": ("requests", parse_listings_globalbusinessdirectory),
    "local.globalbusinessdirectory.us": ("requests", parse_listings_globalbusinessdirectory),
    "blogs.globalbusinessdirectory.us": ("requests", parse_blogs_globalbusinessdirectory),
    "chamberofcommerce.com": ("requests", parse_chamberofcommerce),
    "trueen.com": ("requests", parse_trueen),
    "citysquares.com": ("requests", parse_citysquares),
    "b2bco.com": ("requests", parse_b2bco),
    "find-us-here.com": ("playwright", parse_findushere),
    "a-zbusinessfinder.com": ("playwright", parse_azbusinessfinder),
    "cybo.com": ("requests", parse_cybo),
    "linkcentre.com": ("requests", parse_linkcentre),
    "band.us": ("requests", parse_band),
    "americansearch.info": ("requests", parse_americansearch),
    "n49.com": ("requests", parse_n49),
    "bizhwy.com": ("requests", parse_bizhwy),
    "yplocal.com": ("requests", parse_yplocal),
    "golocalezservices.com": ("requests", parse_golocalezservices),
    "findabusinesspro.com": ("requests", parse_findabusinesspro),
    "globeconnected.com": ("requests", parse_globeconnected),
    "whatsyourhours.com": ("requests", parse_whatsyourhours),
    "milestones.business": ("requests", parse_milestones),
    "iformative.com": ("requests", parse_iformative),
    "thebusinessminded.com": ("requests", parse_thebusinessminded),
    "cleansway.com": ("requests", parse_cleansway),
    "preferredprofessionals.com": ("requests", parse_preferredprofessionals),
    "bestdealfinder.com": ("requests", parse_bestdealfinder),
    "911getit.com": ("requests", parse_911getit),
    "touchafro.com": ("requests", parse_touchafro),
    "supplyautonomy.com": ("requests", parse_supplyautonomy),
    "mybusinessplaces.com": ("requests", parse_mybusinessplaces),
    "local-biz.directory": ("requests", parse_localbizdirectory),
    "vetslist.com": ("requests", parse_vetslist),
    "vymaps.com": ("requests", parse_vymaps),
    "wireanium.com": ("requests", parse_wireanium),
    "closelocation.com": ("requests", parse_closelocation),
}


def extract_business(url, worker_path="playwright_worker.py"):

    domain = urlparse(url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]

    candidates = [k for k in SITE_PARSERS if k in domain]
    matched = max(candidates, key=len) if candidates else None

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
            html = None
            blocked = True

        if blocked:
            # Unmapped/blocked site -- retry via Playwright automatically
            html = fetch_via_playwright(url, worker_path=worker_path)
    else:
        html = fetch_via_playwright(url, worker_path=worker_path)

    if _looks_like_cloudflare_error(html):
        raise RuntimeError(
            f"Fetch for {url} returned a Cloudflare error page "
            f"(origin server appears to be down or unreachable), "
            f"not the real page content."
        )

    business = parser(url, html)

    if isinstance(business, list):
        return [filter_business_fields(record, url) for record in business]

    return filter_business_fields(business, url)
