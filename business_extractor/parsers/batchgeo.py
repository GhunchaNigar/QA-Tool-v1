"""
Site parser: batchgeo.com/map/<id>  (also es./de./fr. ... language sub-domains)

BatchGeo "maps" are used here as listing pages: the map description holds the
business record as "Label:" lines followed by their value line(s):

    Owner Name: / Address: / Phone: / Website: / Business Email: /
    About US: / Related Searches: / (optionally) Hours:

The same text is present in several places; they are tried in order of
reliability:
  1. <script type="application/ld+json"> @type Map -> "description"
  2. the rendered <div id="description">
  3. <meta name="description">
The map title (= business name) comes from the ld+json name, #map-title or <title>.

BatchGeo sits behind Cloudflare: plain `requests` gets a 403 "Just a moment"
page. dispatch.py fetches it with curl_cffi (Chrome fingerprint) and falls back
to Playwright if that is blocked.
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py
from .medium import _split_address

_LABEL_KEYS = {
    "owner name": "owner",
    "owner": "owner",
    "address": "address",
    "phone": "phone",
    "website": "website",
    "business email": "email",
    "email": "email",
    "about us": "about",
    "about": "about",
    "related searches": "keywords",
    "keywords": "keywords",
    "hours": "hours",
    "business hours": "hours",
}

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _description_text(soup):
    """The raw labelled description text, newline separated."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "{}")
        except (ValueError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "Map" and item.get("description"):
                return item["description"]
    div = soup.select_one("#description")
    if div and clean(div.get_text()):
        return div.get_text("\n")
    meta = soup.find("meta", attrs={"name": "description"})
    return meta.get("content", "") if meta else ""


def _group_by_label(text):
    grouped, current = {}, None
    for raw in text.replace("\r", "").split("\n"):
        line = clean(raw)
        if not line:
            continue
        key = _LABEL_KEYS.get(line.rstrip(":").strip().lower()) if line.endswith(":") else None
        if key:
            current = key
            grouped.setdefault(current, [])
        elif current:
            grouped[current].append(line)
    return grouped


def parse_batchgeo(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "html.parser")

    title = soup.select_one("#map-title")
    name = clean(title.get_text()) if title else ""
    if not name:
        t = soup.find("title")
        name = re.sub(r"^\s*BatchGeo\s*-\s*", "", clean(t.get_text()) if t else "")
    business["Business Name"] = name

    grouped = _group_by_label(_description_text(soup))

    def first(key):
        items = grouped.get(key) or []
        return items[0] if items else ""

    business["Owner Name"] = first("owner")

    street, city, state, zipcode = _split_address(first("address"))
    business["Street"], business["City"], business["State"], business["Zipcode"] = street, city, state, zipcode

    business["Phone"] = first("phone").replace("\u2013", "-").replace("\u2014", "-")

    m = _URL_RE.search(" ".join(grouped.get("website", [])))
    business["Website URL"] = m.group(0).rstrip(".,;") if m else ""

    m = _EMAIL_RE.search(" ".join(grouped.get("email", [])))
    business["Business Email"] = m.group(0) if m else ""

    business["Description"] = " ".join(grouped.get("about", []))
    business["Keywords"] = first("keywords")
    business["Hours"] = "\n".join(grouped.get("hours", []))

    # Social / Google Business links: any URL in the record that points there.
    social, gbp = {}, ""
    for link in _URL_RE.findall(" ".join(v for vals in grouped.values() for v in vals)):
        link = link.rstrip(".,;")
        host = urlparse(link).netloc.lower()
        if _is_maps_link(link):
            gbp = gbp or link
            continue
        for domain, label in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(host, domain):
                social.setdefault(label, link)
    business["Social Media Links"] = social
    business["GBP Link"] = gbp

    return filter_business_fields(business, url)