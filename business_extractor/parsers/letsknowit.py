"""
Site parser: letsknowit.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def _letsknowit_label_el(h3):
    """Find the label element of an <h3> detail row. Current markup uses
    <span class="lkit-biz-fact__label">Label</span>; older markup used
    <label><small>Label:</small></label>. Support both."""
    return h3.select_one(".lkit-biz-fact__label") or h3.find("small")


def _letsknowit_value_el(h3):
    """Find the value element of an <h3> detail row. Current markup uses
    <span class="lkit-biz-fact__value">value</span> (there's also a
    sibling icon-only <span class="lkit-biz-fact__icon">, so this must be
    targeted specifically rather than just grabbing the first <span>).
    Older markup had a single plain <span>value</span>, so fall back to
    that when the newer class isn't present."""
    return h3.select_one(".lkit-biz-fact__value") or h3.find("span")


def _letsknowit_detail_value(details, label_keyword):
    """The '.companyDetails' block is a flat list of <h3> rows (Location,
    Headquarter, Phone, Company Size, Website, ...), each with a label and
    a value. Finds the row whose label text matches label_keyword and
    returns its value text."""
    if not details:
        return None
    for h3 in details.find_all("h3"):
        label_el = _letsknowit_label_el(h3)
        if label_el and label_keyword.lower() in clean(label_el.get_text()).lower():
            value_el = _letsknowit_value_el(h3)
            if value_el:
                return clean(value_el.get_text())
    return None


def _letsknowit_address_row(details):
    """Legacy markup fallback: older pages had an *unlabeled* <h3> (icon
    only, no caption) whose <span> held the full 'Street, City, State Zip,
    Country' address. Current markup labels this row "Location" instead --
    see parse_letsknowit(), which tries _letsknowit_detail_value(details,
    "location") first and only falls back to this function for the old,
    unlabeled shape."""
    if not details:
        return None
    for h3 in details.find_all("h3"):
        if _letsknowit_label_el(h3):
            continue  # a labeled row (Headquarter, Phone, Company Size, Website)
        if not h3.select_one("i.fa-map-marker"):
            continue
        value_el = _letsknowit_value_el(h3)
        if value_el:
            text = clean(value_el.get_text())
            if text:
                return text
    return None


def _letsknowit_website_url(details):
    """The 'Website' row's value is an <a href="..."> whose visible text
    is a truncated display domain (e.g. "psychtools.com"), not the full
    URL, so pull the href attribute directly rather than reusing
    _letsknowit_detail_value's text extraction."""
    if not details:
        return None
    for h3 in details.find_all("h3"):
        label_el = _letsknowit_label_el(h3)
        if label_el and "website" in clean(label_el.get_text()).lower():
            value_el = _letsknowit_value_el(h3)
            if value_el:
                link = value_el.find("a", href=True)
                if link:
                    return link["href"].strip()
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
    # Current markup labels this row "Location"; older pages left it
    # unlabeled (icon only) -- try the labeled lookup first, then fall
    # back to the legacy unlabeled-row scan.
    map_row_text = (
        _letsknowit_detail_value(details, "location")
        or _letsknowit_address_row(details)
    )
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

    # ---- Website URL ("Website" row) ----
    # Older markup marked the value span with a "tl_exp" class; current
    # markup uses the generic .lkit-biz-fact__value structure instead --
    # try the legacy selector first, then the current one.
    if details:
        website_span = details.select_one("h3 span.tl_exp")
        link = website_span.find("a", href=True) if website_span else None
        if link:
            business["Website URL"] = link["href"].strip()
        else:
            url_val = _letsknowit_website_url(details)
            if url_val:
                business["Website URL"] = url_val

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
