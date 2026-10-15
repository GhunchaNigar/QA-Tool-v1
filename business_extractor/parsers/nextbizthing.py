"""
Site parser: nextbizthing.com

Same directory platform as nextbizmaker.com (JSON-LD LocalBusiness + the
.member_profile overview table), so the shared helpers are reused from
parsers/nextbizmaker.py. Differences handled here:

  - The "Location" row is just "City, ST ZIP" (no street). Listings with a
    full "street, city, ST ZIP" location are split into all four parts; a
    city-only location leaves Street blank instead of putting the city in it.
  - JSON-LD address fields hold "N/A" placeholders, which are ignored.
  - There is no .header-member-name <h1>; the name comes from JSON-LD /
    the "Company Name" row.
  - Category is the first line of the profile header block (the second line
    is the country); falls back to the breadcrumb.
  - The logo is the profile image inside .member_profile.
"""

from ..common import *  # noqa: F401,F403
from .nextbizmaker import (
    _nbm_value, _nbm_ld_entries, _nbm_find, _nbm_hours, _nbm_links,
)

_CITY_STATE_ZIP_RE = re.compile(r"^(?P<head>.*?)[,\s]*\b(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$")


def _nbt_split_location(text):
    """"Phoenix, AZ 85001" -> ("", "Phoenix", "AZ", "85001").
    "123 Main St, Phoenix, AZ 85001" -> ("123 Main St", "Phoenix", "AZ", "85001")."""
    text = clean(text)
    if not text:
        return "", "", "", ""
    m = _CITY_STATE_ZIP_RE.match(text)
    if not m:
        return text, "", "", ""
    parts = [p.strip() for p in m.group("head").split(",") if p.strip()]
    city = parts[-1] if parts else ""
    street = ", ".join(parts[:-1])
    return street, city, m.group("state"), m.group("zip")


def parse_nextbizthing(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "html.parser")

    if _looks_blocked(html_text):
        return business

    entries = _nbm_ld_entries(soup)
    ld_business = _nbm_find(entries, "LocalBusiness")
    ld_address = ld_business.get("address") if isinstance(ld_business.get("address"), dict) else {}

    scope = soup.select_one(".member_profile") or soup.select_one("#main-content") or soup

    # ---- Name ----
    name_el = soup.select_one(".header-member-name h1") or soup.select_one(".textbox-company")
    business["Business Name"] = _nbm_value(ld_business.get("name")) or (
        clean(name_el.get_text()) if name_el else ""
    )
    if not business["Business Name"]:
        h1 = scope.find(["h1", "h2"])
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Street / City / State / Zipcode ----
    loc_el = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    location = clean(loc_el.get_text(" ")) if loc_el else ""
    if not is_meaningful(location):
        location = _nbm_value(ld_address.get("streetAddress"))
    street, city, state, zipcode = _nbt_split_location(location)
    business["Street"] = street
    business["City"] = city or _nbm_value(ld_address.get("addressLocality"))
    business["State"] = state or _nbm_value(ld_address.get("addressRegion"))
    business["Zipcode"] = zipcode or _nbm_value(ld_address.get("postalCode"))

    # ---- Country ----
    business["Country"] = _nbm_value(ld_address.get("addressCountry"))

    # ---- Phone (hidden "See Phone Number" span holds the real number) ----
    phone_el = soup.select_one("span.phone_number") or soup.select_one(".phone_number_header")
    if phone_el and is_meaningful(phone_el.get_text()):
        business["Phone"] = clean(phone_el.get_text())
    else:
        business["Phone"] = _nbm_value(ld_business.get("telephone"))

    # ---- Website URL ----
    link = soup.select_one("a.weblink[href]")
    if link and link["href"].strip().lower().startswith(("http://", "https://")):
        business["Website URL"] = link["href"].strip()

    # ---- Description ----
    business["Description"] = _nbm_value(ld_business.get("description"))

    # ---- Category: first line of the header block ("Category<br>Country") ----
    category = ""
    for p in scope.select("p.line-height-xl"):
        lines = [clean(x) for x in p.get_text("\n").split("\n")]
        lines = [x for x in lines if x]
        if lines:
            category = lines[0]
            break
    if not category:
        crumbs = [clean(a.get_text()) for a in soup.select(".breadcrumb a")]
        crumbs = [c for c in crumbs if c and c.lower() != "home"]
        category = crumbs[-1] if crumbs else ""
    business["Category"] = category

    # ---- Logo (profile image) ----
    logo = scope.select_one("img.img-rounded[src]") or scope.select_one(".profile-image img[src]")
    if logo:
        business["Logo"] = urljoin(url, logo["src"].strip())

    # ---- Hours / Social Media Links / GBP Link (best-effort) ----
    business["Hours"] = _nbm_hours(scope)
    business["Social Media Links"], business["GBP Link"] = _nbm_links(scope, business["Website URL"])

    return filter_business_fields(business, url)