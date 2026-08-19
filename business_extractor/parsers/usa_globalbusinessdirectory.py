
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
    """
    Split a combined "<street> <city>" string (no comma between them) into
    (street, city).

    This theme stores the full address as ONE free-text string
    ("2244 Faraday Ave #206 Carlsbad, CA 92008") with no structured
    street/city/state/zip fields behind it, so there's no reliable way to
    split street from city other than heuristics. Strategy, in order:

      1. If a unit/suite token (#206, Suite 4, Apt B, ...) appears, treat
         everything up to and including the unit's value as the street and
         everything after it as the city. This is the common case for
         suite-numbered offices like this listing.
      2. Otherwise, if a street-type suffix word (Ave, St, Blvd, ...)
         appears, treat everything up to and including that suffix as the
         street and everything after it as the city.
      3. Otherwise, fall back to treating the last word as the city and the
         rest as the street (weak fallback for addresses this heuristic
         doesn't recognize — flag such cases for manual review).
    """
    tokens = pre_comma_text.split()
    if not tokens:
        return "", ""

    # Case 1: unit/suite token, e.g. "... Ave #206 Carlsbad" or
    # "... Ave Suite 206 Carlsbad"
    for i, tok in enumerate(tokens):
        if _UNIT_TOKEN_RE.match(tok):
            end = i + 1
            # "Suite 206" / "Unit B" / "Apt 4" — swallow the value token too
            if (
                end < len(tokens)
                and not _UNIT_TOKEN_RE.match(tok)  # tok itself isn't "#206"-style
                and re.match(r"^#?\w+$", tokens[end])
                and tok.lower() in ("suite", "ste.", "ste", "unit", "apt", "apt.", "no", "no.")
            ):
                end += 1
            street = " ".join(tokens[:end])
            city = " ".join(tokens[end:])
            if city:
                return street, city

    # Case 2: street-suffix word (walk backwards so we catch the *last*
    # suffix occurrence, closest to the city name)
    for i in range(len(tokens) - 1, -1, -1):
        if _STREET_SUFFIX_RE.match(tokens[i].strip(".")):
            street = " ".join(tokens[: i + 1])
            city = " ".join(tokens[i + 1 :])
            if city:
                return street, city

    # Case 3: weak fallback
    if len(tokens) > 1:
        return " ".join(tokens[:-1]), tokens[-1]
    return pre_comma_text, ""


def _split_full_address(address_text):
    """
    Split "<street> <city>, <ST> <zipcode>" into
    (street, city, state, zipcode).

    Returns ("", "", "", "") if the trailing ", ST ZIP" pattern isn't found
    at all (rather than guessing).
    """
    if not address_text:
        return "", "", "", ""

    address_text = " ".join(address_text.split())  # collapse whitespace

    m = re.match(
        r"^(?P<pre>.+),\s*(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$",
        address_text,
    )
    if not m:
        return "", "", "", ""

    street, city = _split_combined_street_city(m.group("pre").strip())
    return street, city, m.group("state").upper(), m.group("zip")


def _clean_text(el):
    return el.get_text(strip=True) if el else None


# --- main parser ------------------------------------------------------------

def parse_usaglobalbusinessdirectory(html, url=None):
    """
    Parse a usa.globalbusinessdirectory.us business listing page.

    Returns a dict with: Name, Owner Name, Street, City, State, Zipcode,
    Country, Phone, Website URL, Description, Hours, Social Media Links,
    Business Email, Category.
    """
    soup = BeautifulSoup(html, "html.parser")

    data = {
        "Name": None,
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

    # --- JSON-LD LocalBusiness block (preferred source where it applies) ---
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
        data["Name"] = ld.get("name")
        data["Phone"] = ld.get("telephone")
        data["Business Email"] = ld.get("email")
        if ld.get("description"):
            data["Description"] = BeautifulSoup(
                ld["description"], "html.parser"
            ).get_text(strip=True)
        # ld["address"]["address"] is a combined free-text string (non-
        # standard schema.org for this theme) — parsed below alongside the
        # HTML fallback so we only write the splitting logic once.
        ld_address_text = None
        if isinstance(ld.get("address"), dict):
            ld_address_text = ld["address"].get("address")
    else:
        ld_address_text = None

    # --- Name (HTML fallback / cross-check) ---
    if not data["Name"]:
        data["Name"] = _clean_text(soup.select_one("h1.case27-primary-text"))

    # --- Description (HTML fallback) ---
    if not data["Description"]:
        desc_el = soup.select_one(
            ".block-type-text.block-field-job_description .pf-body"
        )
        data["Description"] = _clean_text(desc_el)

    # --- Contact Information block: email / phone / website ---
    # Icons (mi email / mi phone / mi web) identify each line; text lives in
    # the following <span>.
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

    # Website URL fallback: the "Website" CTA button near the top of the
    # page, if the contact-info block didn't have one.
    if not data["Website URL"]:
        cta = soup.select_one('.lmb-calltoaction a[href]:not([href^="tel:"])')
        if cta:
            data["Website URL"] = cta["href"]

    # --- Category ---
    data["Category"] = _clean_text(
        soup.select_one(".block-type-categories .category-name")
    )

    # --- Region / Country ---
    # The theme labels this block "Region" but it holds the country
    # (e.g. "United States"), linked via /region/<slug>/.
    country_el = soup.select_one(".block-type-terms .details-list li a span")
    data["Country"] = _clean_text(country_el)

    # --- Owner Name (best-effort; see module docstring caveat) ---
    data["Owner Name"] = _clean_text(
        soup.select_one(".block-type-author .host-name")
    )

    # --- Address: prefer the combined string surfaced in the map block's
    # data-options JSON (identical to what's in the visible address <p>,
    # but avoids relying on exact whitespace in the rendered HTML). Fall
    # back to the visible <p> text if the map JSON isn't present or
    # unparsable.
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

    # --- Hours (only present on listings that set them; selector matches
    # the same block-type used on the sibling listings.* site) ---
    hours_el = soup.select_one(".block-type-work_hours .pf-body")
    if hours_el:
        data["Hours"] = _clean_text(hours_el)

    # --- Social Media Links ---
    # IMPORTANT: exclude #social-share-modal — that's the generic
    # site-wide "share this listing" widget (Facebook/X/WhatsApp/LinkedIn/
    # Mail), not the business's own social accounts. Only look inside the
    # business's own content blocks (main profile columns) for genuine
    # outbound social links, and only for real social platforms — the
    # contact-info "Website" link is excluded since it's already captured
    # as Website URL.
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


