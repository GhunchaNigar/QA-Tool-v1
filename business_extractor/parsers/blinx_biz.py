"""
Site parser: blinx.biz
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def _split_blinx_address(address):
    street, city, state, zipcode = "", "", "", ""

    parts = [p.strip() for p in address.split(",") if p.strip()]

    if len(parts) >= 3:
        street = ", ".join(parts[:-2])
        city = parts[-2]
        state_zip = parts[-1]
    elif len(parts) == 2:
        street = parts[0]
        state_zip = parts[1]
    elif len(parts) == 1:
        state_zip = parts[0]
    else:
        state_zip = ""

    match = re.match(r"^(.*?)\s+([\w-]*\d[\w-]*)$", state_zip.strip())
    if match:
        state = match.group(1).strip()
        zipcode = match.group(2).strip()
    else:
        state = state_zip.strip()

    return street, city, state, zipcode


# Matches "City State Zip" with zero commas at all (e.g. "Plano TX 75023")
# -- a street-less address shape seen on several sources. _split_blinx_address
# only splits on commas, so with none present it dumps the whole "City
# State" run into "state" and never populates "city". This has broken
# multiple site parsers (metriteweb, letsknowit, locuul) independently, so
# it's factored out here as a shared fallback any caller of
# _split_blinx_address can opt into.
_CITY_STATE_ZIP_NO_COMMA_RE = re.compile(
    r"^(?P<city>.+?)\s+(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _split_address_allow_no_comma(address):
    """Like _split_blinx_address, but first checks for the no-street,
    no-comma "City State Zip" shape before falling back to the
    comma-based splitter, which mishandles that shape (see
    _CITY_STATE_ZIP_NO_COMMA_RE above)."""
    if "," not in address:
        match = _CITY_STATE_ZIP_NO_COMMA_RE.match(address)
        if match:
            return "", match.group("city").strip(), match.group("state").strip(), match.group("zip")
    return _split_blinx_address(address)


def _split_city_state_zip_address(address):
    """Split addresses with NO street segment, in either of two shapes:

      (a) Comma-free "City State Zip" (e.g. "Plano TX 75023") -- used by
          askmap.net, blogs.globalbusinessdirectory.us, place123.net,
          milestones.business, earthmom.org, gravitysplash.com,
          webforcompany.com, and local-biz.directory.

      (b) Two comma-separated spans, "City State, Zip" (e.g.
          preferredprofessionals.com renders <span>Plano TX</span>,
          <span>75023</span> -> "Plano TX, 75023").

    _split_blinx_address() assumes commas separate street/city/state-zip.
    Shape (a) has no commas at all, so it lands in _split_blinx_address()
    as one trailing "State Zip" token and mis-splits into
    state="Plano TX", zipcode="75023", city="" (never populated). Shape
    (b) fares no better: _split_blinx_address() takes street="Plano TX",
    state_zip="75023" -- and since "75023" has no internal whitespace to
    split on, that regex fails too, leaving state="75023" and city blank.
    Detect both shapes directly here instead of falling through.
    """
    address = address.strip()

    # Shape (a): comma-free "City State Zip".
    if "," not in address:
        match = _CITY_STATE_ZIP_NO_COMMA_RE.match(address)
        if match:
            return "", match.group("city").strip(), match.group("state").strip(), match.group("zip")
        return _split_blinx_address(address)

    # Shape (b): two comma-separated parts, "City State, Zip".
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if len(parts) == 2 and re.match(r"^\d{5}(?:-\d{4})?$", parts[1]):
        match = re.match(r"^(?P<city>[A-Za-z][A-Za-z .'-]*?)\s+(?P<state>[A-Z]{2})$", parts[0])
        if match:
            return "", clean(match.group("city")), match.group("state"), parts[1]

    return _split_blinx_address(address)


def _split_listings_gbd_address(address):
    """Split the My Listing theme's rendered address block.

    Unlike blinx.biz, this theme's map-block-address text has no street
    segment -- it's just "City, State Zip[, Country]" (e.g.
    "Plano, Texas 75023, United States"). Reusing _split_blinx_address()
    here mis-shifts every field by one, because that function assumes a
    leading street part whenever there are >=2 comma-separated pieces.

    Strategy: drop a trailing country segment (it has no digits, whereas
    the "State Zip" segment does), then treat whatever's left as
    City, State Zip -- or Street, City, State Zip if there happen to be
    three or more parts remaining.
    """
    street, city, state, zipcode = "", "", "", ""

    parts = [p.strip() for p in address.split(",") if p.strip()]

    # Trailing country segment has no digits (e.g. "United States"); the
    # "State Zip" segment right before it does (e.g. "Texas 75023").
    if len(parts) >= 2 and not re.search(r"\d", parts[-1]):
        parts = parts[:-1]

    if len(parts) >= 3:
        street = ", ".join(parts[:-2])
        city = parts[-2]
        state_zip = parts[-1]
    elif len(parts) == 2:
        city = parts[0]
        state_zip = parts[1]
    elif len(parts) == 1:
        state_zip = parts[0]

    match = re.match(r"^(.*?)\s+([\w-]*\d[\w-]*)$", state_zip.strip())
    if match:
        state = match.group(1).strip()
        zipcode = match.group(2).strip()
    else:
        state = state_zip.strip()

    return street, city, state, zipcode


_BLINX_RENDERED_ADDRESS_RE = re.compile(
    r"^(?P<street>.+?),\s*(?P<city>[^,]+?),\s*(?P<state>[A-Za-z]{2,})\s*,?\s*(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _extract_blinx_address_from_dom(soup):

    for raw_line in soup.get_text(separator="\n").split("\n"):
        line = clean(raw_line)
        if not line or "," not in line:
            continue
        match = _BLINX_RENDERED_ADDRESS_RE.match(line)
        if match:
            return (
                match.group("street").strip(),
                match.group("city").strip(),
                match.group("state").strip(),
                match.group("zip").strip(),
            )

    return None


def _find_brownbook_record(obj, _depth=0):
    if _depth > 12:
        return None

    if isinstance(obj, dict):
        if "brownbook_id" in obj:
            return obj
        for value in obj.values():
            found = _find_brownbook_record(value, _depth + 1)
            if found:
                return found

    elif isinstance(obj, list):
        for item in obj:
            found = _find_brownbook_record(item, _depth + 1)
            if found:
                return found

    return None


def _blinx_links_to_business(business, links):
    if not isinstance(links, list):
        return

    for entry in links:
        if isinstance(entry, str):
            href = entry
        elif isinstance(entry, dict):
            href = entry.get("url") or entry.get("href") or entry.get("link") or ""
        else:
            continue

        if not href:
            continue

        is_social = any(domain in href.lower() for domain in SOCIAL_DOMAINS)

        if not is_social and not business["Website URL"]:
            business["Website URL"] = href


def parse_blinx(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Primary source: Next.js __NEXT_DATA__ hydration payload ----
    record = None
    next_data_script = soup.find("script", id="__NEXT_DATA__")

    if next_data_script and next_data_script.string:
        try:
            next_data = json.loads(next_data_script.string)
        except Exception:
            next_data = None

        if next_data:
            record = _find_brownbook_record(next_data)

    if record:
        business["Business Name"] = record.get("name") or record.get("title") or ""

        business["Country"] = record.get("country", "")
        business["Phone"] = record.get("phone", "")
        business["Business Email"] = record.get("email", "")

        logo = record.get("logo") or record.get("image")
        if logo:
            business["Logo"] = urljoin(url, logo)

        _blinx_links_to_business(business, record.get("links"))

        # The API's "address" field can be a full "Street, City, State Zip"
        # string, or -- as seen here -- just "City State Zip" with no
        # street and no commas at all. Use the no-comma-aware splitter so
        # both shapes are handled; the DOM extraction below overrides this
        # when it finds a more complete rendered address anyway.
        address = record.get("address", "")
        if address:
            street, city, state, zipcode = _split_address_allow_no_comma(address)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    # ---- Address: prefer the rendered DOM ----
    dom_address = _extract_blinx_address_from_dom(soup)
    if dom_address:
        street, city, state, zipcode = dom_address
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Business Name fallback (og:title / <title>) ----
    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"])
        elif soup.title:
            business["Business Name"] = clean(soup.title.get_text()).split("|")[0].strip()

    # ---- Logo fallback (og:image) ----
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Phone fallback (tel: link on the page) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Email fallback (mailto: link on the page) ----
    if not business["Business Email"]:
        email = soup.select_one('a[href^="mailto:"]')
        if email:
            business["Business Email"] = email["href"].replace("mailto:", "").strip()

    # ---- Website / social fallback (visible anchors) ----
    # fetched via plain requests).
    for a in soup.find_all("a", href=True):
        href = a["href"]

        if not href.startswith("http"):
            continue
        if "blinx.biz" in href.lower():
            continue
        if "google.com/maps" in href.lower() or _is_maps_link(href):
            continue

        is_social = any(domain in href.lower() for domain in SOCIAL_DOMAINS)

        if not is_social and not business["Website URL"]:
            business["Website URL"] = href

    return business


