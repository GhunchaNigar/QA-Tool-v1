"""
Site parser: letsknowit.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def _letsknowit_detail_value(details, label_keyword):
    """The '.companyDetails' block is a flat list of <h3><label>icon</label>
    <label><small>Label:</small></label><span>value</span></h3> rows (e.g.
    Headquarter, Phone, Company Size). Finds the row whose <small> label
    text matches label_keyword and returns its <span> value."""
    if not details:
        return None
    for h3 in details.find_all("h3"):
        label_el = h3.find("small")
        if label_el and label_keyword.lower() in clean(label_el.get_text()).lower():
            span = h3.find("span")
            if span:
                return clean(span.get_text())
    return None


def _letsknowit_address_row(details):
    """The '.companyDetails' block's *first* <h3> is unlabeled -- its
    <label> holds only the map-marker icon (no <small> caption) -- and its
    <span> holds the full 'Street, City, State Zip, Country' address. This
    is the real address; the separate 'Headquarter:' row is almost always
    just an unset 'N/A' placeholder and should only be used as a fallback."""
    if not details:
        return None
    for h3 in details.find_all("h3"):
        if h3.find("small"):
            continue  # a labeled row (Headquarter, Phone, Company Size, Website)
        if not h3.select_one("i.fa-map-marker"):
            continue
        span = h3.find("span")
        if span:
            text = clean(span.get_text())
            if text:
                return text
    return None


_LETSKNOWIT_COUNTRY_SUFFIX_RE = re.compile(
    r",\s*(united states(?: of america)?|usa|us)\s*$", re.I
)

# Matches "City State Zip" with no internal comma at all (e.g. "Plano TX
# 75023") -- the shape left behind once _LETSKNOWIT_COUNTRY_SUFFIX_RE strips
# the trailing ", United States" from a street-less listing's address. This
# has zero commas, so _split_blinx_address (comma-based only) dumps the
# whole "City State" run into "state" and never populates "city".
_LETSKNOWIT_CITY_STATE_ZIP_RE = re.compile(
    r"^(?P<city>.+?)\s+(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _letsknowit_split_address(address):
    """_split_blinx_address expects 'Street, City, State Zip' (3 parts),
    but letsknowit renders 'Street, City, State Zip, Country' (4 parts),
    which shifts city/state/zip off by one. Strip the trailing country
    first so the shared splitter parses it correctly.

    Some listings have no street segment and no internal comma either --
    just "City State Zip" once the country suffix is gone. Handle that
    shape explicitly before falling back to the comma-based splitter."""
    address = _LETSKNOWIT_COUNTRY_SUFFIX_RE.sub("", address).strip()
    if "," not in address:
        match = _LETSKNOWIT_CITY_STATE_ZIP_RE.match(address)
        if match:
            return "", match.group("city").strip(), match.group("state").strip(), match.group("zip")
    return _split_blinx_address(address)


def _letsknowit_address_quality(text):
    """Score how "address-like" a candidate string is. Listings are
    inconsistent about which of the two rows (map-marker vs 'Headquarter:')
    holds the real address and which holds a sparse/placeholder value --
    sometimes it's 'Street, City, State Zip' in one and 'City, Country' (or
    'N/A') in the other, sometimes it's reversed. Rather than trusting one
    row by position, score both and keep the more complete one: more
    comma-separated segments is better, and a segment that ends in digits
    (a zip code) is a strong signal of a real, complete address. Returns
    -1 for missing/placeholder text so it always loses to a real address."""
    if not text or text.strip().upper() == "N/A":
        return -1
    address = _LETSKNOWIT_COUNTRY_SUFFIX_RE.sub("", text).strip()
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return -1
    score = len(parts)
    if re.search(r"\d", parts[-1]):
        score += 1
    return score


def parse_letsknowit(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Name ----
    name_el = soup.select_one(".userProfileName h1")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    details = soup.select_one(".companyDetails.profilegeneraldetail")

    # ---- Street / City / State / Zipcode ----
    map_row_text = _letsknowit_address_row(details)
    headquarter_text = _letsknowit_detail_value(details, "headquarter")
    address_text = max(
        (map_row_text, headquarter_text),
        key=_letsknowit_address_quality,
    )

    if _letsknowit_address_quality(address_text) >= 0:
        street, city, state, zipcode = _letsknowit_split_address(address_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone ("Phone:" row) ----
    phone = _letsknowit_detail_value(details, "phone")
    if phone:
        business["Phone"] = phone

    # ---- Phone (sidebar "Contact Details" widget fallback) ----
    if not business["Phone"]:
        tel = soup.select_one('.widget.personal-info a[href^="tel:"] span')
        if tel:
            business["Phone"] = clean(tel.get_text())

    # ---- Website URL ("Website:" row, marked with the tl_exp class) ----
    if details:
        website_span = details.select_one("h3 span.tl_exp")
        if website_span:
            link = website_span.find("a", href=True)
            if link:
                business["Website URL"] = link["href"].strip()

    # ---- Business Email (sidebar "Contact Details" widget -- the mailto
    #      href itself is blanked out client-side, so read the visible
    #      span text instead) ----
    email_anchor = soup.select_one('.widget.personal-info a[href^="mailto:"]')
    if email_anchor:
        span = email_anchor.find("span")
        email_text = clean(span.get_text()) if span else ""
        if not email_text:
            email_text = email_anchor["href"].replace("mailto:", "").strip()
        if "@" in email_text:
            business["Business Email"] = email_text

    # ---- Description ("About <Name>" block; site emits invalid nested
    #      <p><p>...</p></p> markup, so pull text from the container
    #      rather than a single <p> match) ----
    about = soup.select_one("#aboutcontent")
    if about:
        text = clean(about.get_text(separator=" "))
        if is_meaningful(text):
            business["Description"] = text

    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])
    if not business["Logo"]:
        logo_img = soup.select_one(".profile-pic img[src]")
        if logo_img:
            business["Logo"] = urljoin(url, logo_img["src"])

    # ---- Photos (gallery section shows an "empty_message" placeholder
    #      instead of images when nothing has been uploaded) ----
    gallery = soup.select_one("#companyGalleryContent")
    if gallery and not gallery.select_one(".empty_message"):
        photos = []
        for img in gallery.select("img[src]"):
            src = urljoin(url, img["src"])
            if src not in photos:
                photos.append(src)
        if photos:
            business["Photos"] = photos

    return business


