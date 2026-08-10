"""
Site parser: band.us (BAND / Naver Band group intro pages)
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



_BAND_DESCRIPTION_LABELS = [
    "Owner Name", "Address", "Phone", "Business Email", "About us", "Related Searches",
]


def _band_description_sections(description, labels=None):
    if not description:
        return {}

    labels = labels or _BAND_DESCRIPTION_LABELS
    canonical_by_lower = {label.lower(): label for label in labels}
    label_pattern = "|".join(re.escape(l) for l in labels)
    matches = list(re.finditer(rf"(?:^|\n)({label_pattern}):?\n?", description, flags=re.I))

    sections = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(description)
        canonical_label = canonical_by_lower[m.group(1).lower()]
        sections[canonical_label] = clean(description[start:end])
    return sections


def parse_band(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name --
    og_title = soup.find("meta", property="og:title")
    title_text = og_title["content"] if og_title and og_title.get("content") else None
    if not title_text:
        title_tag = soup.find("title")
        title_text = title_tag.get_text() if title_tag else ""
    business["Business Name"] = clean(re.sub(r"\s*\|\s*BAND\s*$", "", title_text or "", flags=re.I))

    desc_tag = soup.find("meta", attrs={"name": "description"})
    description = desc_tag["content"] if desc_tag and desc_tag.get("content") else None
    if not description:
        og_desc = soup.find("meta", property="og:description")
        description = og_desc["content"] if og_desc and og_desc.get("content") else ""

    sections = _band_description_sections(description)

    # ---- Address -- 
    address = sections.get("Address", "")
    if address:
        street, city, state, zipcode = _split_blinx_address(address)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone ----
    if sections.get("Phone"):
        business["Phone"] = sections["Phone"]

    # ---- Business Email ----
    if sections.get("Business Email"):
        business["Business Email"] = sections["Business Email"]

    # ---- Description ("About us:" section) ----
    if sections.get("About us"):
        business["Description"] = sections["About us"]

    # ---- Keywords  ----
    if sections.get("Related Searches"):
        business["Keywords"] = sections["Related Searches"]

    return business


