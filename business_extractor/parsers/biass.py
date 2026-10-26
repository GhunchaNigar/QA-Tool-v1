"""
Site parser: biass.us
(business websites built on Biass, e.g. https://lindsaycrowell.biass.us/)

Each business gets its own <slug>.biass.us site. The site sits behind a
verification step (Cloudflare) that headless fetches may not pass, so this
domain is in HEADED_DOMAINS (playwright_worker.py) and dispatched as
"playwright".

The page is server-rendered. Where each field lives:

  Name         the hero <h1> inside .main_info      (fallback: li.company-name, og:title)
  Owner Name   NOT published on these pages -- left blank on purpose. The
               sub-domain slug (e.g. "lindsaycrowell") is not used as a guess.
  Street       "Address" row of table.quick-contact (fallback: footer address, 1st line)
  City         "City" row, text before the comma   ("Austin, Texas" -> "Austin")
  State        "City" row, text after the comma    ("Austin, Texas" -> "Texas")
  Zipcode      only printed in the footer address  ("Austin, 78757, Texas")
  Country      "Country" row
  Phone        "Telephone" row                      (fallback: span.phoneNumber)
  Website URL  "Website" row                        (fallback: sidebar .widget-user-footer .website)
  Description  first <p> under the "About" headline (the <meta> description is
               truncated with "...", so it is only a fallback)
  Social Media Links
               footer ".follow_us" icons. Inactive icons (class "inactive-link",
               no <a>) mean the business has not set that profile. The share
               buttons at the top only share the page, so they are ignored.
  Category     NOT published on these pages (the hero only says "Service Provider",
               which is the account type, not a category) -- left blank.
  Logo         .top-sticky-nav logo <img>           (fallback: sidebar logo, og:image)
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


_OWN_HOSTS = ("biass.us", "biass.ca", "biass.uk", "biass.co")
_ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")


def _is_own(href):
    host = (urlparse(href).netloc or "").lower().split(":")[0]
    return any(host == h or host.endswith("." + h) for h in _OWN_HOSTS)


def _contact_table(soup):
    """{lower-case label: value <td>} for the 'Contacts' table."""
    rows = {}
    table = soup.select_one("table.quick-contact")
    if not table:
        return rows
    for tr in table.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 2:
            continue
        label = clean(tds[0].get_text(" ")).lower()
        if label and label not in rows:
            rows[label] = tds[1]
    return rows


def _cell_text(cell):
    return clean(cell.get_text(" ")) if cell else ""


def _footer_address_lines(soup):
    """Lines of the footer address block (the <li> with the house icon)."""
    icon = soup.select_one(".pre-footer .contacts i.icon_house")
    if not icon:
        return []
    li = icon.find_parent("li")
    col2 = li.select_one(".col2") if li else None
    if not col2:
        return []
    return [clean(x) for x in col2.get_text("\n").split("\n") if clean(x)]


def parse_biass(url, html):

    business = empty_business()

    if not html or _looks_blocked(html):
        return business

    soup = BeautifulSoup(html, "lxml")

    hero_h1 = soup.select_one(".main_info h1")
    table = _contact_table(soup)
    if not hero_h1 and not table:
        return business  # verification / error page: no listing here

    # ---- Name ----
    name = clean(hero_h1.get_text(" ")) if hero_h1 else ""
    if not name:
        name = _cell_text(table.get("company name"))
    if not name:
        el = soup.select_one("li.company-name")
        if el:
            name = clean(el.get_text(" ").split("|")[0])
    if not name:
        og = soup.find("meta", property="og:title")
        name = clean((og.get("content") or "").split(" - ")[0]) if og else ""
    business["Business Name"] = name

    # ---- Address ----
    footer = _footer_address_lines(soup)

    street = _cell_text(table.get("address"))
    if not street and footer:
        street = footer[0]
    business["Street"] = street

    city_cell = _cell_text(table.get("city"))
    city, state = city_cell, ""
    if "," in city_cell:
        city, _, state = city_cell.rpartition(",")
        city, state = clean(city), clean(state)

    zipcode = ""
    if len(footer) > 1:
        # "Austin, 78757, Texas" -> city / ZIP / state, in any order
        parts = [clean(p) for p in footer[-1].split(",") if clean(p)]
        for p in parts:
            if _ZIP_RE.fullmatch(p):
                zipcode = p
        rest = [p for p in parts if p != zipcode]
        if not city and rest:
            city = rest[0]
        if not state and len(rest) > 1:
            state = rest[-1]
    if not zipcode:
        m = _ZIP_RE.search(street + " " + city_cell)
        zipcode = m.group(0) if m else ""

    business["City"] = city
    business["State"] = state
    business["Zipcode"] = zipcode
    business["Country"] = _cell_text(table.get("country"))

    # ---- Phone ----
    phone = _cell_text(table.get("telephone"))
    if not phone:
        el = soup.select_one("span.phoneNumber")
        phone = clean(el.get_text(" ")) if el else ""
    if not phone:
        tel = soup.select_one("a[href^='tel:']")
        phone = clean(tel.get_text(" ")) if tel else ""
    business["Phone"] = phone

    # ---- Website URL ----
    website = ""
    cell = table.get("website")
    if cell:
        a = cell.find("a", href=True)
        website = (a["href"] if a else _cell_text(cell)).strip()
    if not website:
        a = soup.select_one(".widget-user-footer .website a[href^='http']")
        website = a["href"].strip() if a else ""
    if website and _is_own(website):
        website = ""
    business["Website URL"] = website

    # ---- Description ----
    desc = ""
    for h in soup.select("h1.headline"):
        if clean(h.get_text(" ")).lower() == "about":
            body = h.find_parent(class_="card-body")
            p = body.find("p") if body else None
            if p:
                desc = clean(p.get_text(" "))
            break
    if not desc:
        meta = (soup.find("meta", attrs={"name": "description"})
                or soup.find("meta", property="og:description"))
        desc = clean(meta.get("content", "")) if meta else ""
    business["Description"] = desc

    # ---- Social Media Links (footer "Follow Us" only) ----
    for a in soup.select(".follow_us li:not(.inactive-link) a[href]"):
        href = a["href"].strip()
        if not href.lower().startswith("http") or _is_own(href):
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href
                break

    # ---- Logo ----
    logo = ""
    for sel in (".top-sticky-nav li.logo img", ".widget-user-image img"):
        img = soup.select_one(sel)
        if img and img.get("src"):
            logo = urljoin(url, img["src"].strip())
            break
    if not logo:
        og = soup.find("meta", property="og:image")
        logo = urljoin(url, og["content"].strip()) if og and og.get("content") else ""
    business["Logo"] = logo

    return business