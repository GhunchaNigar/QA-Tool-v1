"""
Site parser: blinx.biz
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py
from urllib.parse import unquote



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


_BLINX_CONTACT_ADDRESS_RE = re.compile(
    r"^(?P<street_city>.+?),\s*(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)

# Street-type suffix tokens that signal we're still inside the street
# portion, not the city, when walking backward through the tokens of
# "<street> <city>" looking for the street/city boundary (see
# _split_blinx_contact_address below).
_STREET_SUFFIX_TOKENS = {
    "st", "st.", "street", "ave", "ave.", "avenue", "blvd", "blvd.",
    "boulevard", "rd", "rd.", "road", "dr", "dr.", "drive", "ln", "ln.",
    "lane", "ct", "ct.", "court", "pl", "pl.", "place", "way", "hwy",
    "highway", "pkwy", "parkway", "cir", "circle", "ter", "terrace",
    "trl", "trail", "sq", "square", "loop", "suite", "ste", "ste.",
}


def _split_blinx_contact_address(address):
    """
    Splits the "street_address" field of a blinx.biz contact_details link
    (a richer address than the top-level record's "address" field, but
    rendered with NO comma between street and city -- only before
    "state zip", e.g. "2244 Faraday Ave #206 Carlsbad, CA 92008").

    _split_blinx_address() assumes a comma separates street from city,
    so on this shape it lumps "<street> <city>" into a single trailing
    "state_zip" token and fails outright, leaving city/state/zip blank
    (or, worse, dumping the whole string into "state" -- see
    _split_blinx_address's docstring-less regex, which requires a
    word-only token at the very end and chokes on "#206").

    Strategy: split off the trailing ", <state> <zip>" first (unambiguous
    since state+zip is separated from street+city by the only comma).
    Then, since street and city are separated by nothing but a space,
    walk backward through the remaining tokens collecting the city --
    stopping as soon as a token contains a digit/"#" or is a common
    street-type suffix (Ave, Blvd, Ste, ...), which signals we've walked
    back into the street portion.

    Returns (street, city, state, zipcode), or ("", "", "", "") if the
    address doesn't end in the expected ", ST ZIP" shape.
    """
    if not address:
        return "", "", "", ""

    match = _BLINX_CONTACT_ADDRESS_RE.match(address.strip())
    if not match:
        return "", "", "", ""

    state = match.group("state")
    zipcode = match.group("zip")
    street_city = match.group("street_city").strip()

    tokens = street_city.split()
    city_tokens = []
    for token in reversed(tokens):
        bare = token.strip(".,#").lower()
        if not bare or any(ch.isdigit() for ch in token) or "#" in token or bare in _STREET_SUFFIX_TOKENS:
            break
        city_tokens.insert(0, token)

    if city_tokens:
        city = " ".join(city_tokens)
        street = " ".join(tokens[: len(tokens) - len(city_tokens)]).strip()
    else:
        city = ""
        street = street_city

    return street, city, state, zipcode


def _find_blinx_contact_details(links):
    """
    Finds the "contact_details" link entry in the __NEXT_DATA__ payload's
    "links" list and returns its "linkable" dict (which carries a fuller
    street_address/email/website/phone than the top-level record), or
    None if there isn't one.
    """
    if not isinstance(links, list):
        return None
    for entry in links:
        if isinstance(entry, dict) and entry.get("linkable_type") == "contact_details":
            linkable = entry.get("linkable")
            if isinstance(linkable, dict):
                return linkable
    return None


_BLINX_RENDERED_ADDRESS_RE = re.compile(
    r"^(?P<street>.+?),\s*(?P<city>[^,]+?),\s*(?P<state>[A-Za-z]{2,})\s*,?\s*(?P<zip>\d{5}(?:-\d{4})?)$"
)

# Some blinx.biz listings render only "Street, Zip" on the visible address
# line -- no city or state segment at all (e.g. "1883 N Silverspring Dr,
# 54913"). _BLINX_RENDERED_ADDRESS_RE requires a city+state between street
# and zip, so it never matches this shorter shape and the address falls
# through to the JSON record's (incomplete) "address" field instead. Try
# this narrower pattern too.
_BLINX_STREET_ZIP_RE = re.compile(
    r"^(?P<street>.+?),\s*(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _extract_blinx_address_from_dom(soup):

    street_zip_fallback = None

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
        if not street_zip_fallback:
            sz_match = _BLINX_STREET_ZIP_RE.match(line)
            if sz_match:
                street_zip_fallback = (
                    sz_match.group("street").strip(),
                    "",
                    "",
                    sz_match.group("zip").strip(),
                )

    return street_zip_fallback


def _extract_blinx_city_state_from_maps_link(soup):
    """
    When the visible address line omits city/state (see
    _BLINX_STREET_ZIP_RE above), blinx.biz still embeds them in the
    Google Maps iframe/link's "q=" query param, e.g.:
      .../maps/embed/v1/search?...&q=1883 N Silverspring Dr, Appleton ,WI, US
    Parse that param as "Street, City, State[, Country]" (or "City, State
    [, Country]" without a street) to recover City/State/Country.
    """
    for tag in soup.find_all(["a", "iframe"], href=True) + soup.find_all("iframe", src=True):
        href = tag.get("href") or tag.get("src") or ""
        if "maps/embed" not in href and "google.com/maps" not in href.lower():
            continue

        q = None
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        if qs.get("q"):
            q = qs["q"][0]
        if not q:
            continue

        parts = [p.strip() for p in unquote(q).split(",") if p.strip()]
        if len(parts) >= 4:
            return parts[1], parts[2], parts[3]  # city, state, country
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]  # city, state, country
        if len(parts) == 2:
            return parts[0], parts[1], ""         # city, state

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
        # NOTE: intentionally NOT set from record.get("email") here -- see
        # the contact_details block below, which supplies the real
        # practice email; record["email"] is often a synthetic/placeholder
        # address (e.g. "thelawoffic@emailsl.com") rather than what's
        # actually shown on the page.

        logo = record.get("logo") or record.get("image")
        if logo:
            business["Logo"] = urljoin(url, logo)

        _blinx_links_to_business(business, record.get("links"))

        # The API's top-level "address" field can be a full
        # "Street, City, State Zip" string, or -- as seen here -- just the
        # street with no city/state/zip and no commas at all. Use the
        # no-comma-aware splitter so both shapes are handled; the
        # contact_details and DOM extraction below override this with a
        # more complete address when they find one.
        address = record.get("address", "")
        if address:
            street, city, state, zipcode = _split_address_allow_no_comma(address)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

        # ---- contact_details link: fuller address/email/website/phone ----
        # The top-level "address" field is often street-only. The
        # "contact_details" link entry (rendered as the page's "Contact
        # details" card) carries a fuller street_address string like
        # "2244 Faraday Ave #206 Carlsbad, CA 92008" -- no comma between
        # street and city, so it needs its own splitter (see
        # _split_blinx_contact_address). It also carries the real practice
        # email/website/phone, which can differ from the top-level record.
        contact = _find_blinx_contact_details(record.get("links"))
        if contact:
            contact_address = contact.get("street_address", "")
            if contact_address:
                c_street, c_city, c_state, c_zip = _split_blinx_contact_address(contact_address)
                if c_city or c_state or c_zip:
                    business["Street"] = c_street
                    business["City"] = c_city
                    business["State"] = c_state
                    business["Zipcode"] = c_zip
            if contact.get("email"):
                business["Business Email"] = contact["email"]
            if contact.get("website") and not business["Website URL"]:
                business["Website URL"] = contact["website"]
            if not business["Phone"] and (contact.get("phone_work") or contact.get("phone_mobile")):
                business["Phone"] = contact.get("phone_work") or contact.get("phone_mobile")

        # Fall back to the top-level record's email only if contact_details
        # didn't supply one.
        if not business["Business Email"]:
            business["Business Email"] = record.get("email", "")

    # ---- Address: prefer the rendered DOM ----
    dom_address = _extract_blinx_address_from_dom(soup)
    if dom_address:
        street, city, state, zipcode = dom_address
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

        # The visible address line sometimes has no city/state at all
        # (just "Street, Zip") -- recover those from the Maps embed link.
        if not city and not state:
            maps_location = _extract_blinx_city_state_from_maps_link(soup)
            if maps_location:
                map_city, map_state, map_country = maps_location
                business["City"] = map_city
                business["State"] = map_state
                if map_country and not business["Country"]:
                    business["Country"] = map_country

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
