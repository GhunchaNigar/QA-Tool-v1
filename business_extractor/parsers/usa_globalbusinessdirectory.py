import json
import re
from bs4 import BeautifulSoup


# --- shared-style helpers -------------------------------------------------

_STREET_SUFFIX_RE = re.compile(
    r"^(st|street|ave|avenue|blvd|boulevard|dr|drive|rd|road|ln|lane|way|"
    r"ct|court|pl|place|pkwy|parkway|hwy|highway|cir|circle|ter|terrace|"
    r"sq|square|trl|trail|loop|xing|crossing)\.?$",
    re.IGNORECASE,
)

_UNIT_TOKEN_RE = re.compile(
    r"^(#\S*|suite|ste\.?|unit|apt\.?|no\.?)$", re.IGNORECASE
)


def _split_combined_street_city(pre_comma_text):
    tokens = pre_comma_text.split()
    if not tokens:
        return "", ""

    for i, tok in enumerate(tokens):
        if _UNIT_TOKEN_RE.match(tok):
            end = i + 1
            if (
                end < len(tokens)
                and not _UNIT_TOKEN_RE.match(tok)
                and re.match(r"^#?\w+$", tokens[end])
                and tok.lower() in ("suite", "ste.", "ste", "unit", "apt", "apt.", "no", "no.")
            ):
                end += 1
            street = " ".join(tokens[:end])
            city = " ".join(tokens[end:])
            if city:
                return street, city

    for i in range(len(tokens) - 1, -1, -1):
        if _STREET_SUFFIX_RE.match(tokens[i].strip(".")):
            street = " ".join(tokens[: i + 1])
            city = " ".join(tokens[i + 1 :])
            if city:
                return street, city

    if len(tokens) > 1:
        return " ".join(tokens[:-1]), tokens[-1]
    return pre_comma_text, ""


def _split_full_address(address_text):
    if not address_text:
        return "", "", "", ""

    address_text = " ".join(address_text.split())

    m = re.match(
        r"^(?P<pre>.+),\s*(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$",
        address_text,
    )
    if not m:
        return "", "", "", ""

    street, city = _split_combined_street_city(m.group("pre").strip())
    return street, city, m.group("state").upper(), m.group("zip")


def _clean_text(el):
    # separator=" " -- several blocks on this theme (notably the
    # description) are more than one child tag (e.g. multiple <p>s);
    # without a separator, get_text(strip=True) runs their text together
    # with no space between them.
    return el.get_text(" ", strip=True) if el else None


def parse_usaglobalbusinessdirectory(url, html):
    # NOTE: (url, html) -- matching the calling convention every other
    # parse_<site>(url, html) function in this codebase uses. This was
    # previously declared as (html, url=None), i.e. swapped. The harness
    # calls every site parser positionally as parse_<site>(url, html),
    # so that swap meant `html` was silently bound to the page URL
    # string and `url` to the real HTML. BeautifulSoup(html, ...) then
    # parsed a bare URL as markup -- which has no tags at all -- so every
    # selector below returned None/empty, matching the "everything is
    # blank" symptom exactly (same root cause as the cities.* parser).
    soup = BeautifulSoup(html, "html.parser")

    data = {
        # NOTE: key must be "Business Name", not "Name" -- every other
        # site parser in this codebase (see empty_business() in
        # common.py) returns the business name under "Business Name".
        # A mismatched key here means the harness's downstream
        # merge/normalization step would silently drop this field even
        # once the argument-order bug above is fixed.
        "Business Name": None,
        "Owner Name": None,
        "Street": None,
        "City": None,
        "State": None,
        "Zipcode": None,
        "Country": None,
        "Phone": None,
        "Website URL": None,
        "Description": None,
        "Hours": None,
        "Social Media Links": [],
        "Business Email": None,
        "Category": None,
    }

    ld = None
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            candidate = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        if isinstance(candidate, dict) and candidate.get("@type") == "LocalBusiness":
            ld = candidate
            break

    if ld:
        data["Business Name"] = ld.get("name")
        data["Phone"] = ld.get("telephone")
        data["Business Email"] = ld.get("email")
        if ld.get("description"):
            # separator=" " -- the description is often multiple <p> tags
            # (e.g. "...San Diego residents</p><p>recover compensation...");
            # get_text(strip=True) alone has no separator between them and
            # runs the paragraphs together with no space.
            data["Description"] = BeautifulSoup(
                ld["description"], "html.parser"
            ).get_text(" ", strip=True)
        ld_address_text = None
        if isinstance(ld.get("address"), dict):
            ld_address_text = ld["address"].get("address")
    else:
        ld_address_text = None

    if not data["Business Name"]:
        data["Business Name"] = _clean_text(soup.select_one("h1.case27-primary-text"))

    if not data["Description"]:
        desc_el = soup.select_one(
            ".block-type-text.block-field-job_description .pf-body"
        )
        data["Description"] = _clean_text(desc_el)

    for li in soup.select(".block-type-details .details-block-content li"):
        icon = li.find("i")
        span = li.find("span")
        if not icon or not span:
            continue
        icon_classes = icon.get("class", [])
        value = span.get_text(strip=True)
        if "email" in icon_classes:
            data["Business Email"] = data["Business Email"] or value
        elif "phone" in icon_classes:
            data["Phone"] = data["Phone"] or value
        elif "web" in icon_classes:
            data["Website URL"] = value

    if not data["Website URL"]:
        cta = soup.select_one('.lmb-calltoaction a[href]:not([href^="tel:"])')
        if cta:
            data["Website URL"] = cta["href"]

    data["Category"] = _clean_text(
        soup.select_one(".block-type-categories .category-name")
    )

    country_el = soup.select_one(".block-type-terms .details-list li a span")
    data["Country"] = _clean_text(country_el)

    data["Owner Name"] = _clean_text(
        soup.select_one(".block-type-author .host-name")
    )

    address_text = ld_address_text
    if not address_text:
        map_el = soup.select_one(".c27-map[data-options]")
        if map_el:
            try:
                map_opts = json.loads(map_el["data-options"])
                locations = map_opts.get("locations") or []
                if locations:
                    address_text = locations[0].get("address")
            except (ValueError, KeyError, TypeError):
                pass
    if not address_text:
        address_text = _clean_text(soup.select_one(".map-block-address p"))

    street, city, state, zipcode = _split_full_address(address_text or "")
    data["Street"] = street or None
    data["City"] = city or None
    data["State"] = state or None
    data["Zipcode"] = zipcode or None

    hours_el = soup.select_one(".block-type-work_hours .pf-body")
    if hours_el:
        data["Hours"] = _clean_text(hours_el)

    social_domains = (
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "linkedin.com",
        "youtube.com",
        "tiktok.com",
        "pinterest.com",
    )
    social_links = []
    main_columns = soup.select_one(".tab-template-two-columns")
    if main_columns:
        for a in main_columns.select("a[href]"):
            href = a["href"]
            if any(domain in href for domain in social_domains):
                if href not in social_links:
                    social_links.append(href)
    data["Social Media Links"] = social_links

    return data
