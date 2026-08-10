"""
Site parser: bizhwy.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



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


