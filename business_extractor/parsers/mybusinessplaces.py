"""
Site parser: mybusinessplaces.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



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


