"""
Site parser: n49.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def _extract_balanced_json_object(text, start_marker):
    """Find `start_marker` in `text`, then return the JSON substring of
    the first balanced {...} object that follows it. Needed because a
    naive regex can't safely capture objects containing nested {}/[]
    (as n49Business does: aggregateRating, _geoloc, serviceBoundaries...).
    """
    marker_pos = text.find(start_marker)
    if marker_pos == -1:
        return None

    brace_start = text.find("{", marker_pos)
    if brace_start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(brace_start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start:i + 1]
    return None


_N49_OPS_HOURS_DAY_ORDER = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
]


def _format_n49_hours(ops_hours, hours_text):
    if hours_text == "doNotDisplay" or not ops_hours:
        return ""
    parts = []
    for day in _N49_OPS_HOURS_DAY_ORDER:
        times = ops_hours.get(day)
        if times:
            parts.append(f"{day.capitalize()}: {', '.join(times)}")
    return "; ".join(parts)


def parse_n49(url, html):

    business = empty_business()

    if _looks_blocked(html):
        return business

    raw_json = _extract_balanced_json_object(html, "var n49Business")
    if not raw_json:
        return business

    try:
        data = json.loads(raw_json)
    except Exception:
        return business

    # ---- Business Name ----
    business["Business Name"] = clean(data.get("bName", ""))

    # ---- Street/City/State/Zipcode ----
    # bAddr1 comes with a trailing comma baked in (e.g. "6800 Burnet Rd
    # Ste 8,") since n49 stores city/state/zip separately already.
    business["Street"] = clean((data.get("bAddr1") or "").rstrip(","))
    business["City"] = clean(data.get("bcity", ""))
    business["State"] = clean(data.get("bProvState", ""))
    business["Zipcode"] = clean(data.get("bPostalZip", ""))
    business["Country"] = clean(data.get("countryCode", ""))

    # ---- Phone ----
    if data.get("bPhone1"):
        business["Phone"] = clean(data["bPhone1"])

    # ---- Website URL ----
    if data.get("bWebsite"):
        business["Website URL"] = clean(data["bWebsite"])

    # ---- Business Email ----
    if data.get("bEmail"):
        business["Business Email"] = clean(data["bEmail"])

    # ---- Description ----
    if data.get("bDesc"):
        business["Description"] = clean(data["bDesc"])

    # ---- Hours ----
    business["Hours"] = _format_n49_hours(
        data.get("bOpsHours"), data.get("hoursText", "")
    )

    # ---- Social Media Links ----
    social_field_to_network = {
        "facebookPageUrl": "Facebook",
        "facebook": "Facebook",
        "twitterHandle": "Twitter",
        "twitter": "Twitter",
        "instagram": "Instagram",
        "youtube": "YouTube",
        "pinterest": "Pinterest",
        "linkedin": "LinkedIn",
    }
    for field_name, network in social_field_to_network.items():
        value = data.get(field_name)
        if value and network not in business["Social Media Links"]:
            business["Social Media Links"][network] = value

    # ---- Category ----
    categories = data.get("categories") or [c.get("name") for c in (data.get("categoryObjects") or []) if c.get("name")]
    if categories:
        business["Category"] = ", ".join(categories)

    # ---- Logo ----
    if data.get("logoImagePath"):
        business["Logo"] = urljoin(url, data["logoImagePath"])

    # ---- Photos ----
    photos = [
        img["url"] for img in (data.get("galleryImages") or [])
        if isinstance(img, dict) and img.get("url")
    ]
    if photos:
        business["Photos"] = photos

    return business


