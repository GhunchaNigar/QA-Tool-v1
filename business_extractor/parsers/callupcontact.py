"""
Site parser: callupcontact.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py




HEADING_TAGS = re.compile(r"^h[1-6]$")

_CALLUPCONTACT_KEYWORD_BOILERPLATE = {
    "businessprofile",
    "ratings",
    "business profiles",
    "products and services",
    "directions",
    "maps",
    "business listing",
    "telephone",
    "fax",
    "postal address",
    "postal code",
}


def _decode_cf_email(hex_string):
    """Decodes Cloudflare's email-obfuscation hex string back to a
    plain email address. The XOR-decoded bytes come out as literal
    numeric HTML entities (e.g. "&#105;&#110;..."), so an extra
    html.unescape() pass is needed to get the real address."""
    try:
        key = int(hex_string[:2], 16)
        decoded = "".join(
            chr(int(hex_string[i:i + 2], 16) ^ key)
            for i in range(2, len(hex_string), 2)
        )
        return html.unescape(decoded)
    except Exception:
        return ""


def _find_cf_email(soup):
    # Form 1: <a href="/cdn-cgi/l/email-protection#HEX">
    link = soup.select_one('a[href*="/cdn-cgi/l/email-protection#"]')
    if link:
        hex_part = link["href"].split("#", 1)[-1]
        decoded = _decode_cf_email(hex_part)
        if decoded:
            return decoded

    # Form 2: <span class="__cf_email__" data-cfemail="HEX">
    span = soup.select_one("[data-cfemail]")
    if span:
        decoded = _decode_cf_email(span["data-cfemail"])
        if decoded:
            return decoded

    return ""


def _is_leaf(tag):
    """True if tag has no nested element children (only text/whitespace).
    Used to avoid matching wrapper divs whose get_text() happens to
    include both the label and the value concatenated together."""
    return tag.find(True) is None


def _find_label_value_element(soup, label):
    """Finds any leaf tag whose text exactly matches `label`, then
    returns the neighboring element that holds its value (not the
    text -- the element itself, so callers can inspect its structure,
    e.g. pull out just the <a> tags for a multi-value field)."""

    for tag in soup.find_all(True):
        if not _is_leaf(tag):
            continue
        if clean(tag.get_text()).lower() != label.lower():
            continue

        sib = tag.find_next_sibling()
        while sib is not None and not clean(sib.get_text()):
            sib = sib.find_next_sibling()
        if sib:
            return sib

        if tag.parent:
            parent_sib = tag.parent.find_next_sibling()
            while parent_sib is not None and not clean(parent_sib.get_text()):
                parent_sib = parent_sib.find_next_sibling()
            if parent_sib:
                return parent_sib

    return None


def _value_by_label(soup, label, separator=" "):
    elem = _find_label_value_element(soup, label)
    return clean(elem.get_text(separator=separator)) if elem else ""

def _value_after_heading(soup, label, separator=" "):
    return _value_by_label(soup, label, separator=separator)


def parse_callupcontact(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name (page <h1>) ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- About Us (description) ----
    description = _value_after_heading(soup, "About Us")
    if description:
        business["Description"] = description

    # ---- Call & Message ----
    phone = _value_after_heading(soup, "Telephone")
    if phone:
        business["Phone"] = phone

    website = _value_after_heading(soup, "Website")
    if website:
        business["Website URL"] = website

    # ---- Email (Cloudflare-obfuscated, not a plain mailto:) ----
    email = _find_cf_email(soup)
    if email:
        business["Business Email"] = email

    # ---- Address ----
    street = _value_after_heading(soup, "Street Address")
    if street:
        business["Street"] = street

    city = _value_after_heading(soup, "City")
    if city:
        business["City"] = city

    state = _value_after_heading(soup, "State / Province")
    if state:
        business["State"] = state

    zipcode = _value_after_heading(soup, "Zip / Postal Code")
    if zipcode:
        business["Zipcode"] = zipcode

    country = _value_after_heading(soup, "Country")
    if country:
        business["Country"] = country

    # ---- Hours ----
    hours = _value_after_heading(soup, "Hours") or _value_after_heading(soup, "Business Hours")
    if hours:
        business["Hours"] = hours

    # ---- Meta description fallback (page-level, matches About Us usually) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Meta keywords (strip this template's fixed boilerplate tail --
    #      see _CALLUPCONTACT_KEYWORD_BOILERPLATE above) ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            tokens = [clean(t) for t in kw_raw.split(",")]
            tokens = [
                t for t in tokens
                if t and t.lower() not in _CALLUPCONTACT_KEYWORD_BOILERPLATE
            ]
            if tokens:
                business["Keywords"] = ", ".join(tokens)

    return business


