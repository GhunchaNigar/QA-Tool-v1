"""
Site parser: surfyourtown.com

The site is only reachable from some countries (turn a system-wide VPN on).
The listing's data is in a LocalBusiness JSON-LD block plus the profile
header/contact table, so a plain fetch is enough once the page is reachable.

Where each field lives on a listing page:
  Name       LocalBusiness JSON-LD "name" (fallback: <h1>)
  Street     JSON-LD address.streetAddress
  City       JSON-LD address.addressLocality
  State      JSON-LD address.addressRegion (e.g. "FL")
  Zipcode    JSON-LD address.postalCode
  Country    JSON-LD address.addressCountry (e.g. "US")
  Phone      tel: link in the profile header ("Show Phone Number" button);
             the JSON-LD phone is malformed ("850460-2900"), so it's a fallback only
  Website    .weblink link in the "Contact Information" table (fallback:
             JSON-LD sameAs entry that isn't surfyourtown.com)
  Hours      the "Hours" row of the contact table when a listing has one
             (this listing has none, so it stays blank)
  Category   .profile-header-top-category
  Logo       profile photo (JSON-LD image / .profile-image img), made absolute
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


def _local_business(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except Exception:
            continue
        items = data.get("@graph", [data]) if isinstance(data, dict) else data
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "LocalBusiness":
                return item
    return {}


def _table_value(soup, label):
    """Value cell of the contact-table row whose label cell reads `label`."""
    want = label.lower()
    for group in soup.select(".table-view-group"):
        cells = group.find_all("div", recursive=False)
        if len(cells) >= 2 and clean(cells[0].get_text(" ")).lower() == want:
            return cells[1]
    return None


def parse_surfyourtown(url, html):

    business = empty_business()

    if not html or _looks_blocked(html):
        return business

    soup = BeautifulSoup(html, "lxml")
    ld = _local_business(soup)
    addr = ld.get("address") or {}

    # ---- Business Name ----
    name = clean(ld.get("name", ""))
    if not name:
        h1 = soup.find("h1")
        name = clean(h1.get_text(" ")) if h1 else ""
    business["Business Name"] = name

    # ---- Street / City / State / Zipcode / Country ----
    business["Street"] = clean(addr.get("streetAddress", ""))
    business["City"] = clean(addr.get("addressLocality", ""))
    business["State"] = clean(addr.get("addressRegion", ""))
    business["Zipcode"] = clean(addr.get("postalCode", ""))
    business["Country"] = clean(addr.get("addressCountry", ""))

    if not business["Street"]:
        # Fallback: the "Location" row -> "<street><br><city>, <state>, <zip><br>Country"
        loc = _table_value(soup, "Location")
        if loc:
            spans = [clean(s.get_text(" ")) for s in loc.find_all("span")]
            if len(spans) >= 4:
                business["Street"], business["City"], business["State"], business["Zipcode"] = spans[:4]

    # ---- Phone ----
    tel = soup.select_one(".profile-header-phone-number a[href^='tel:'], a[href^='tel:']")
    if tel:
        business["Phone"] = clean(tel.get_text(" ")).replace("Call:", "").strip() or tel["href"][4:]
    else:
        business["Phone"] = clean(ld.get("telephone", ""))

    # ---- Website URL ----
    link = soup.select_one(".table-display-website a.weblink, a.weblink")
    website = (link.get("href") or "").strip() if link else ""
    if not website:
        for u in ld.get("sameAs") or []:
            if isinstance(u, str) and u.startswith("http") and "surfyourtown.com" not in u:
                website = u
                break
    business["Website URL"] = website

    # ---- Hours ----
    for label in ("Hours", "Business Hours", "Opening Hours", "Hours of Operation"):
        cell = _table_value(soup, label)
        if cell:
            business["Hours"] = clean_multiline(str(cell)) if cell.find("br") else clean(cell.get_text(" "))
            break

    # ---- Category ----
    cat = soup.select_one(".profile-header-top-category")
    business["Category"] = clean(cat.get_text(" ")) if cat else ""

    # ---- Logo ----
    img = soup.select_one(".profile-image img")
    logo = (img.get("src") if img else "") or ""
    if not logo:
        image = ld.get("image")
        logo = image.get("url", "") if isinstance(image, dict) else (image or "")
    if logo:
        business["Logo"] = urljoin(url, logo)

    return business