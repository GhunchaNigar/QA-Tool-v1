"""
Site parser: citysquares.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



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


