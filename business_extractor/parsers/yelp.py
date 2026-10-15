"""
Site parser: yelp.com (business pages, https://www.yelp.com/biz/<slug>)

Yelp blocks plain `requests` fetches, so this site is routed through
Playwright in dispatch.py.

Where each field comes from (verified against a saved Cascade Tree Works page):

  Name      <meta property="og:title"> ("<Name> - <City>, <ST>"), else <h1>
  Address   <address> block (street / "Ste 9" / "City, ST ZIP"), else the
            JSON-LD ImageObject.contentLocation.address (PostalAddress,
            also gives the country code)
  Phone     the "Phone number" row in the sidebar, else the first
            (xxx) xxx-xxxx in the page <meta> description block
  Website   Yelp wraps it in /biz_redir?url=<encoded>; the real URL is
            decoded from that query string
  Hours     the weekly hours <table> -> "Mon: 9:00 AM - 5:00 PM; ..."
            (the live "Closed now" / "Open now" label is ignored)
  Category  last item of the JSON-LD BreadcrumbList (Yelp's category
            link texts as a fallback)
  Photos    JSON-LD ItemList of ImageObjects (full-size l.jpg URLs)
  Logo      Yelp has no separate logo, so this is left blank
"""

from urllib.parse import unquote

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


_PHONE_RE = re.compile(r"\(\d{3}\)\s*\d{3}-\d{4}")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_yelp(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "lxml")
    ld_blocks = _yelp_ld_blocks(soup)

    # ---- Name ----
    business["Business Name"] = _yelp_name(soup)

    # ---- Address ----
    street, city, state, zipcode, country = _yelp_address(soup, ld_blocks)
    business["Street"] = street
    business["City"] = city
    business["State"] = state
    business["Zipcode"] = zipcode
    business["Country"] = country

    # ---- Phone ----
    business["Phone"] = _yelp_phone(soup)

    # ---- Website URL ----
    business["Website URL"] = _yelp_website(soup)

    # ---- Hours ----
    business["Hours"] = _yelp_hours(soup)

    # ---- Category ----
    business["Category"] = _yelp_category(soup, ld_blocks)

    # ---- Photos ----
    business["Photos"] = _yelp_photos(ld_blocks, soup)

    return filter_business_fields(business, url)


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def _yelp_ld_blocks(soup):
    blocks = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (ValueError, TypeError):
            continue
        if isinstance(data, list):
            blocks.extend(d for d in data if isinstance(d, dict))
        elif isinstance(data, dict):
            blocks.append(data)
    return blocks


def _yelp_name(soup):
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        title = clean(og["content"])
        # "Cascade Tree Works - Vancouver, WA" -> "Cascade Tree Works"
        name = re.sub(r"\s+-\s+[^-]+,\s*[A-Z]{2}\s*$", "", title)
        if name:
            return name
    h1 = soup.find("h1")
    return clean(h1.get_text()) if h1 else ""


def _yelp_address(soup, ld_blocks):
    street = city = state = zipcode = country = ""

    # Country (and a fallback for everything else) from the JSON-LD
    # PostalAddress that Yelp attaches to each ImageObject.
    postal = {}
    for block in ld_blocks:
        for item in block.get("itemListElement", []) or []:
            if not isinstance(item, dict):
                continue
            addr = (item.get("contentLocation") or {}).get("address")
            if isinstance(addr, dict) and addr.get("streetAddress"):
                postal = addr
                break
        if postal:
            break

    addr_el = soup.find("address")
    if addr_el:
        paragraphs = [clean(p.get_text(" ")) for p in addr_el.find_all("p")]
        paragraphs = [p for p in paragraphs if p]
        if paragraphs:
            # Last <p> is "City, ST 12345"; the rest is the street (+ suite).
            m2 = re.match(r"^(.*),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", paragraphs[-1])
            if m2:
                city, state, zipcode = m2.group(1), m2.group(2), m2.group(3)
                street = ", ".join(paragraphs[:-1])
            elif len(paragraphs) == 1:
                street = paragraphs[0]

    if not (street and city):
        street = street or clean(postal.get("streetAddress", ""))
        city = city or clean(postal.get("addressLocality", ""))
        state = state or clean(postal.get("addressRegion", ""))
        zipcode = zipcode or clean(postal.get("postalCode", ""))

    country = clean(postal.get("addressCountry", ""))
    return street, city, state, zipcode, country


def _yelp_phone(soup):
    # Sidebar row: <p>Phone number</p> ... <p>(360) 718-7108</p>
    label = soup.find(string=re.compile(r"^\s*Phone number\s*$", re.I))
    if label:
        row = label.find_parent("div")
        for _ in range(4):
            if row is None:
                break
            m = _PHONE_RE.search(row.get_text(" "))
            if m:
                return m.group(0)
            row = row.find_parent("div")
    # Fallback: tel: link, then the <meta> description (name, address, phone, hours)
    tel = soup.select_one('a[href^="tel:"]')
    if tel:
        return clean(tel.get_text()) or tel["href"].replace("tel:", "").strip()
    for meta in soup.find_all("meta"):
        m = _PHONE_RE.search(meta.get("content", "") or "")
        if m:
            return m.group(0)
    return ""


def _yelp_website(soup):
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/biz_redir" in href and "website_link_type=website" in href:
            qs = parse_qs(urlparse(html_lib.unescape(href)).query)
            target = (qs.get("url") or [""])[0]
            if target:
                return unquote(target)
    return ""


def _yelp_hours(soup):
    for table in soup.find_all("table"):
        pairs = []
        for tr in table.find_all("tr"):
            cells = [clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
            if not cells or cells[0] not in _DAYS:
                continue
            # Drop the live "Closed now"/"Open now" badge Yelp appends to today's row.
            value = " ".join(
                re.sub(r"\b(Open|Closed) now\b", "", c, flags=re.I).strip()
                for c in cells[1:]
            ).strip()
            if value:
                pairs.append(f"{cells[0]}: {value}")
        if len(pairs) >= 3:
            return "; ".join(pairs)
    return ""


def _yelp_category(soup, ld_blocks):
    for block in ld_blocks:
        if block.get("@type") == "BreadcrumbList":
            items = block.get("itemListElement", [])
            names = [clean(i.get("name", "")) for i in items if isinstance(i, dict)]
            names = [n for n in names if n]
            # [0] is the top-level group ("Home Services"), the last is the category.
            if len(names) >= 2:
                return names[-1]
            if names:
                return names[0]
    cats = []
    for a in soup.select('a[href*="/search?cflt="]'):
        text = clean(a.get_text())
        if text and text not in cats:
            cats.append(text)
    return ", ".join(cats)


def _yelp_photos(ld_blocks, soup):
    photos = []
    for block in ld_blocks:
        if block.get("@type") != "ItemList":
            continue
        for item in block.get("itemListElement", []) or []:
            if isinstance(item, dict) and item.get("@type") == "ImageObject":
                src = item.get("contentUrl") or item.get("url")
                if src and src not in photos:
                    photos.append(src)
    if not photos:
        og = soup.find("meta", attrs={"property": "og:image"})
        if og and og.get("content"):
            photos.append(og["content"])
    return photos