"""
Site parser: storeboard.com  (profile pages: www.storeboard.com/<slug>)

Path in project:  business_extractor/parsers/storeboard.py

Data sources, most reliable first:
  1. <script type="application/ld+json"> Organization subtype (Electrician,
     LocalBusiness, ...) whose @id is the profile URL: name, telephone,
     address, sameAs (website).  NOTE: its "description" is truncated at ~400
     chars, so the full text comes from the visible About card (#pv2About).
  2. Visible "Contact" side card (.pv2s-card): address, phones, hours,
     website, obfuscated email.
  3. "Find us online" card (.pv2s-soc): social links.
  4. <meta name="keywords">, avatar (.pv2-av img), breadcrumb / headline.

Not published on Storeboard profiles: Owner Name, State (non-US pages only
carry the country), GBP Link -- these stay empty unless the page links a
Google Maps *place* URL in the Find-us-online card.
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py
from ..common import _is_maps_link

# ISO-3166 alpha-2 -> name for the countries Storeboard pages commonly use.
# Anything else falls back to the code itself (or pycountry if installed).
_COUNTRY_FALLBACK = {
    "US": "United States", "AU": "Australia", "GB": "United Kingdom",
    "CA": "Canada", "NZ": "New Zealand", "IE": "Ireland", "IN": "India",
    "ZA": "South Africa", "SG": "Singapore", "PK": "Pakistan",
}

# "..., NSW 2042" | "..., 2042" | "..., FL 33008-1234" | "..., SW1A 1AA"
_TRAILING_POSTCODE_RE = re.compile(
    r"[,\s]+(?:(?P<state>[A-Z]{2,3})\s+)?(?P<zip>(?=[A-Z0-9 -]*\d)[A-Z0-9]{2,4}(?:[ -][A-Z0-9]{2,4})?)\s*$"
)


def _country_name(code):
    code = (code or "").strip()
    if not code:
        return ""
    if len(code) != 2:
        return code
    try:
        import pycountry  # already in requirements.txt
        c = pycountry.countries.get(alpha_2=code.upper())
        if c:
            return getattr(c, "common_name", None) or c.name
    except Exception:
        pass
    return _COUNTRY_FALLBACK.get(code.upper(), code)


def _profile_ld(soup):
    """The business JSON-LD node (skips the site-wide Organization/Breadcrumb)."""
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw, strict=False)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop(0)
            if not isinstance(node, dict):
                continue
            if "@graph" in node and isinstance(node["@graph"], list):
                stack.extend(node["@graph"])
            t = node.get("@type")
            t = " ".join(t) if isinstance(t, list) else (t or "")
            if t in ("Organization", "BreadcrumbList", "WebSite", "ListItem"):
                continue
            if node.get("address") or node.get("telephone"):
                return node
    return {}


def _split_street_zip(street_line):
    """'192 Edgeware Rd, 2042' -> ('192 Edgeware Rd', '', '2042')."""
    street_line = clean(street_line)
    m = _TRAILING_POSTCODE_RE.search(street_line)
    if not m:
        return street_line, "", ""
    street = street_line[:m.start()].strip(" ,")
    # Guard: if nothing is left, the whole line was the postcode-like token.
    if not street:
        return street_line, "", ""
    return street, (m.group("state") or ""), m.group("zip")


def _contact_card(soup):
    for card in soup.select(".pv2s-card"):
        h = card.select_one(".pv2-h2")
        if h and clean(h.get_text()).lower() == "contact":
            return card
    return None


def _row(card, key):
    """The .pv2s-row whose small grey label (.pv2s-k) equals `key`."""
    if not card:
        return None
    for row in card.select(".pv2s-row"):
        k = row.select_one(".pv2s-k")
        if k and clean(k.get_text()).lower() == key.lower():
            return row
    return None


def _decode_reversed_email(soup):
    """Storeboard hides the email as a reversed string in data-em."""
    a = soup.select_one("a[data-em]")
    if a:
        rev = (a.get("data-em") or "")[::-1].strip()
        if "@" in rev:
            return rev
    link = soup.select_one('a[href^="mailto:"]')
    if link:
        return link["href"].split(":", 1)[1].split("?")[0].strip()
    return _find_cf_email(soup)  # noqa: F405 -- from common


def _hours(card):
    row = _row(card, "Hours")
    grid = row.select_one(".pv2s-hours") if row else None
    if not grid:
        return ""
    cells = [clean(s.get_text()) for s in grid.find_all("span", recursive=False)]
    lines = []
    for i in range(0, len(cells) - 1, 2):
        day, val = cells[i], cells[i + 1]
        if day and val:
            lines.append(f"{day}: {val}")
    return "\n".join(lines)


def _phone(card, ld):
    row = _row(card, "Phone")
    if row:
        a = row.select_one('a[href^="tel:"]')
        if a and is_meaningful(a.get_text()):
            return clean(a.get_text())
    return clean(ld.get("telephone") or "")


def _website(soup, card, ld):
    row = _row(card, "Website")
    if row:
        a = row.select_one("a[href]")
        if a and a["href"].lower().startswith("http"):
            return a["href"].strip()
    same = ld.get("sameAs")
    for s in ([same] if isinstance(same, str) else (same or [])):
        if isinstance(s, str) and s.lower().startswith("http") \
                and not any(_hostname_matches_social_domain(s, d) for d in SOCIAL_DOMAINS):
            return s.strip()
    return ""


def _socials_and_gbp(soup):
    socials, gbp = {}, ""
    for a in soup.select(".pv2s-soc[href]"):
        href = a["href"].strip()
        if not href.lower().startswith("http"):
            continue
        if not gbp and _is_maps_link(href):
            gbp = href
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                socials.setdefault(network, href)
                break
    return socials, gbp


def _category(soup):
    # Breadcrumb: Home > City > <Category> > Business
    crumbs = soup.select(".pv2-bc a")
    if len(crumbs) >= 3:
        return clean(crumbs[2].get_text())
    head = soup.select_one(".pv2-head")  # "Electricians in Newtown, Australia"
    if head:
        return clean(re.split(r"\s+in\s+", head.get_text(), maxsplit=1)[0])
    return ""


def parse_storeboard(url, html):
    soup = BeautifulSoup(html, "html.parser")
    business = empty_business()
    ld = _profile_ld(soup)
    card = _contact_card(soup)

    # ---- Name ----
    h1 = soup.select_one("h1.pv2-name")
    if h1:
        # the h1 also holds the "Verified" badge span -> take only own text
        own = "".join(t for t in h1.find_all(string=True, recursive=False))
        business["Business Name"] = clean(own)
    if not is_meaningful(business["Business Name"]):
        business["Business Name"] = clean(ld.get("name") or "")

    # ---- Address (JSON-LD first, visible card as fallback) ----
    addr = ld.get("address") if isinstance(ld.get("address"), dict) else {}
    street_line = addr.get("streetAddress") or ""
    city = addr.get("addressLocality") or ""
    state = addr.get("addressRegion") or ""
    zipcode = addr.get("postalCode") or ""
    country = addr.get("addressCountry") or ""

    if not street_line:
        row = _row(card, "Address")
        if row:
            lines = [clean(x) for x in row.get_text("\n").split("\n")]
            lines = [x for x in lines if x and x.lower() != "get directions"
                     and x.lower() != "address"]
            if lines:
                street_line = lines[0]
            if len(lines) > 1 and not city:
                city = lines[1].split(",")[0].strip()
                if not country and "," in lines[1]:
                    country = lines[1].rsplit(",", 1)[1].strip()

    street, st, zp = _split_street_zip(street_line)
    business["Street"] = street
    business["City"] = clean(city)
    business["State"] = clean(state) or st
    business["Zipcode"] = clean(zipcode) or zp
    business["Country"] = _country_name(country)

    # ---- Phone / Website / Email ----
    business["Phone"] = _phone(card, ld)
    business["Website URL"] = _website(soup, card, ld)
    business["Business Email"] = _decode_reversed_email(soup)

    # ---- Keywords (meta) ----
    kw = soup.find("meta", attrs={"name": "keywords"})
    if kw and kw.get("content"):
        business["Keywords"] = clean(kw["content"])

    # ---- Description: full About text, JSON-LD is truncated ----
    about = soup.select_one("#pv2About")
    if about:
        business["Description"] = clean_multiline(about.get_text("\n")) \
            if "\n" in about.get_text() else clean(about.get_text())
    if not is_meaningful(business["Description"]):
        business["Description"] = clean(ld.get("description") or "")

    # ---- Hours ----
    business["Hours"] = _hours(card)

    # ---- Social + GBP ----
    socials, gbp = _socials_and_gbp(soup)
    business["Social Media Links"] = socials
    business["GBP Link"] = gbp

    # ---- Category / Logo ----
    business["Category"] = _category(soup)
    av = soup.select_one(".pv2-av img")
    if av and av.get("src"):
        business["Logo"] = urljoin(url, av["src"].strip())
    elif ld.get("image"):
        img = ld["image"][0] if isinstance(ld["image"], list) else ld["image"]
        business["Logo"] = urljoin(url, str(img))

    return business