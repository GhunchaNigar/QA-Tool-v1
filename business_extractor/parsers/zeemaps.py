"""
Site parser: zeemaps.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



ZEEMAPS_BASE = "https://www.zeemaps.com"


def _zeemaps_group_id(url):
    qs = parse_qs(urlparse(url).query)
    group = qs.get("group") or qs.get("g")
    if not group:
        raise ValueError(f"No ?group= or ?g= parameter found in ZeeMaps URL: {url}")
    return group[0]


def _zeemaps_get(path, **params):
    response = requests.get(f"{ZEEMAPS_BASE}{path}", params=params, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.json()


def parse_zeemaps(url, html=None):
    group = _zeemaps_group_id(url)

    # ---- Data version hash (required by /emarkers) ----
    version = _zeemaps_get("/regions/version", g=group).get("v", "")

    # ---- Marker list ----
    markers = _zeemaps_get("/emarkers", g=group, k="REGULAR", e="false", v=version)

    # ---- Custom field id -> name mapping (generic, not hardcoded) ----
    attrs_raw = _zeemaps_get("/data/attributes", group=group)
    field_names = {fid: meta.get("n", "").strip().lower() for fid, meta in attrs_raw.items()}

    # ---- Map-level description fallback ----
    mapprops = _zeemaps_get("/data/mapprops", group=group, readonly="true")
    map_about = clean_multiline(mapprops.get("mp", {}).get("about", ""))

    results = []

    for m in markers:
        marker_id = m.get("id")
        business = empty_business()

        # Base fields from the marker list
        business["Business Name"] = m.get("nm", "")
        business["Street"] = m.get("s", "")
        business["City"] = m.get("city", "")
        business["State"] = m.get("state", "")
        business["Zipcode"] = m.get("zip", "")

        # ---- Per-marker popup detail (has the real field values) ----
        try:
            detail = _zeemaps_get(
                "/etext",
                g=group,
                j=1,
                sh="",
                _dc=random.random(),
                eids=f"[{marker_id}]",
            )
            if isinstance(detail, list):
                detail = detail[0] if detail else {}
        except Exception:
            detail = {}

        if detail.get("title"):
            business["Business Name"] = detail["title"]

        addr = detail.get("ad", {})
        if addr.get("street"):
            business["Street"] = addr["street"]
        if addr.get("city"):
            business["City"] = addr["city"]
        if addr.get("state"):
            business["State"] = addr["state"]
        if addr.get("postcode"):
            business["Zipcode"] = addr["postcode"]

        # ---- Address fallback: some ZeeMaps groups never populate ----
        if business["Street"] and not business["City"] and not business["State"]:
            street, city, state, zipcode = _split_blinx_address(business["Street"])
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            if not business["Zipcode"]:
                business["Zipcode"] = zipcode

        # ---- Custom fields, resolved generically by name ----
        for fid, value in detail.get("fields", {}).items():
            if not value:
                continue
            name = field_names.get(fid, "")
            if name == "phone":
                business["Phone"] = value
            elif name == "website":
                business["Website URL"] = value
            elif name == "email":
                business["Business Email"] = value
            elif name == "description":
                business["Description"] = clean_multiline(value)

        if not business["Description"]:
            business["Description"] = map_about

        # ---- Photo (embedded as an <img> tag inside the "i" field) ----
        img_html = detail.get("i", "")
        if img_html:
            img_match = re.search(r"src=['\"]([^'\"]+)['\"]", img_html)
            if img_match:
                business["Logo"] = img_match.group(1)

        results.append(business)

    if not results:
        return empty_business()
    return results[0] if len(results) == 1 else results


