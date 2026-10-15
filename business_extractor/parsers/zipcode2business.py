"""
Site parser: zipcode2business.com  (https://www.zipcode2business.com/business/<id>)

The listing is a plain two-column "Business Details" table (label | value),
so every field is looked up by its row label. Placeholder cells are "-".

  <h1> is "<Name>'s Details"; the clean name is the card title (<h3>).
  Business Category              -> Category
  Business Short Description     -> Description (kept verbatim)
  Business Address / Zip Code / City / State / Telephone No.
  Company Website                -> Website URL (the link's href)
  Facebook / Twitter / LinkedIn  -> Social Media Links (only real http links)

The site has no photos or logo, so Photos stays empty.
"""

from ..common import *  # noqa: F401,F403

_PLACEHOLDERS = {"", "-", "n/a", "na", "none", "null"}


def _zb_value(cell):
    text = clean(cell.get_text(" ")) if cell else ""
    return "" if text.lower() in _PLACEHOLDERS else text


def _zb_rows(soup):
    """{label: <td> value cell} for the first (Business Details) table."""
    rows = {}
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) >= 2:
                label = clean(cells[0].get_text(" ")).lower()
                rows.setdefault(label, cells[1])
        if rows:
            break
    return rows


def parse_zipcode2business(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "html.parser")
    rows = _zb_rows(soup)

    # ---- Name ----
    title = soup.select_one(".card-title")
    if title and is_meaningful(title.get_text()):
        business["Business Name"] = clean(title.get_text())
    else:
        h1 = soup.find("h1")
        if h1:
            business["Business Name"] = re.sub(r"[’']s Details\s*$", "", clean(h1.get_text()))

    # ---- Address ----
    business["Street"] = _zb_value(rows.get("business address"))
    business["Zipcode"] = _zb_value(rows.get("zip code"))
    business["City"] = _zb_value(rows.get("city"))
    business["State"] = _zb_value(rows.get("state"))

    # ---- Phone ----
    business["Phone"] = _zb_value(rows.get("telephone no."))

    # ---- Website URL ----
    cell = rows.get("company website")
    link = cell.find("a", href=True) if cell else None
    if link and link["href"].strip().lower().startswith(("http://", "https://")):
        business["Website URL"] = link["href"].strip()
    else:
        site_text = _zb_value(cell)
        if site_text.lower().startswith(("http://", "https://")):
            business["Website URL"] = site_text

    # ---- Description / Category ----
    business["Description"] = _zb_value(rows.get("business short description"))
    business["Category"] = _zb_value(rows.get("business category"))

    # ---- Social Media Links ----
    for label in ("facebook", "twitter", "linkedin", "instagram", "youtube"):
        cell = rows.get(label)
        a = cell.find("a", href=True) if cell else None
        href = a["href"].strip() if a else ""
        if href.lower().startswith(("http://", "https://")):
            business["Social Media Links"][SOCIAL_DOMAINS.get(label, label.title())] = href

    return filter_business_fields(business, url)