"""
Site parser: place123.net

Listing pages render the contact block as label/value pairs separated by
<br/> tags, e.g.

    Owner Name:<br/>Theresa Francis<br/>Phone Number:<br/>0422 799 410<br/>
    Website URL:<br/><a ...>https://...</a><br/>Business Email:<br/>...

Label wording varies between listings ("Phone", "Phone Number", "Telephone",
"Website", "Website URL", ...), so labels are matched through an alias table
rather than exact strings. Phone also has tel:-link and regex fallbacks.
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py
from urllib.parse import urlparse


# Every accepted label spelling (lower-case, no trailing colon) -> field name.
_PLACE123_LABELS = {
    # Owner
    "owner name": "Owner Name",
    "owner": "Owner Name",
    "contact name": "Owner Name",
    "contact person": "Owner Name",
    # Phone
    "phone": "Phone",
    "phone number": "Phone",
    "phone no": "Phone",
    "phone no.": "Phone",
    "telephone": "Phone",
    "telephone number": "Phone",
    "tel": "Phone",
    "tel.": "Phone",
    "mobile": "Phone",
    "mobile number": "Phone",
    "contact number": "Phone",
    "business phone": "Phone",
    "business phone number": "Phone",
    # Website
    "website": "Website URL",
    "website url": "Website URL",
    "web site": "Website URL",
    "url": "Website URL",
    "homepage": "Website URL",
    "url/website": "Website URL",
    "website/url": "Website URL",
    # Email
    "business email": "Business Email",
    "email": "Business Email",
    "e-mail": "Business Email",
    "email address": "Business Email",
    # Description
    "about us": "Description",
    "about": "Description",
    "description": "Description",
    "description long": "Description",
    "long description": "Description",
    "description short": "Description",
    "short description": "Description",
    # Keywords
    "related searches": "Keywords",
    "keywords": "Keywords",
    "keywords or tags": "Keywords",
    "tags": "Keywords",
    # Hours
    "hours": "Hours",
    "business hours": "Hours",
    "opening hours": "Hours",
    "operating hours": "Hours",
    "hours of operation": "Hours",
}

_PLACE123_TERMINATORS = {
    "what do you think about us?",
    "what do you think about us",
    "your nickname",
    "comments",
    "start a discussion",
    "places nearby",
    "edit business",
    "your business in this directory?",
    "your business in this directory",
    "add your business",
    "position on map",
    "gps coordinates",
    "find nearby",
    "street view",
    "write a review",
}

# Loose phone pattern: optional +, then 7+ digits allowing spaces, dots,
# dashes and parentheses between them. Digit count is validated separately.
_PHONE_RE = re.compile(r"(\+?\(?\d[\d\s().\-]{5,}\d)")


def _norm_label(text):
    """'Phone Number:' -> 'phone number', 'Business Phone *' -> 'business phone'"""
    return clean(text).strip().rstrip(":*").strip().rstrip(":*").strip().lower()


def _is_label(text):
    return _norm_label(text) in _PLACE123_LABELS


def _is_stop(text):
    norm = _norm_label(text)
    return norm in _PLACE123_LABELS or norm in _PLACE123_TERMINATORS


def _clean_phone(value):
    """Pull a plausible phone number out of a string, or return ''."""
    if not value:
        return ""
    for match in _PHONE_RE.finditer(value):
        candidate = clean(match.group(1)).strip(" .-")
        digits = re.sub(r"\D", "", candidate)
        if 7 <= len(digits) <= 15:
            return candidate
    return ""


def _find_content_block(soup):
    """The main listing column; falls back to the whole document."""
    return (
        soup.select_one("#content .grid_8")
        or soup.select_one("#content")
        or soup.body
        or soup
    )


def _split_label_inline(line):
    """
    Handles 'Phone Number: 0422 799 410' on a single line.
    Returns (label, value) or (None, None).
    """
    if ":" not in line:
        return None, None
    label, _, rest = line.partition(":")
    if _norm_label(label) in _PLACE123_LABELS and rest.strip():
        return label, rest.strip()
    return None, None


def _is_place123_placeholder(img_url):
    """True for place123's built-in images (e.g. /images/poi.png)."""
    parsed = urlparse(img_url)
    return "place123.net" in parsed.netloc.lower() and parsed.path.lower().startswith("/images/")


_ZIP_TOKEN_RE = re.compile(r"^(?=.*\d)[A-Z0-9]{3,8}(?:-[A-Z0-9]{3,4})?$", re.I)


def _split_locality(text):
    """'20715 Bowie, MD' / 'Bowie, MD 20715' / 'Port Melbourne, VIC 3207'
    -> (city, state, zipcode). Zip may lead or trail."""
    tokens = clean(text).split()
    zipcode = ""
    if tokens and _ZIP_TOKEN_RE.match(tokens[0]):
        zipcode = tokens.pop(0)
    elif tokens and _ZIP_TOKEN_RE.match(tokens[-1]):
        zipcode = tokens.pop()
    rest = " ".join(tokens).strip(" ,")
    if "," in rest:
        city, _, state = rest.rpartition(",")
        return clean(city).strip(" ,"), clean(state), zipcode
    return clean(rest), "", zipcode


def _parse_place123_address(content, business):
    """The <address> block is rendered as up to three lines:

        {street[, city, state zip]}<br>
        {locality, e.g. "20715 Bowie, MD" -- often empty}<br>
        {country}

    Listings fill it inconsistently (everything on line 1, or the city on
    line 2, or a service-area word like "Serving" as the street), so the
    lines are classified instead of read by position.
    """
    address_tag = content.find("address")
    if not address_tag:
        return
    parts = [clean(t) for t in address_tag.get_text("\n").split("\n")]
    parts = [p for p in parts if p]
    if not parts:
        return

    # Country: a last line with no digits and no comma ("Australia").
    if len(parts) >= 2 and not re.search(r"[\d,]", parts[-1]):
        business["Country"] = parts.pop()

    if len(parts) == 1:
        # Everything on one line: "350 Ingles Street Unit E2, Port Melbourne, VIC 3207"
        street, city, state, zipcode = _split_city_state_zip_address(parts[0])
    else:
        street = parts[0]
        city, state, zipcode = _split_locality(" ".join(parts[1:]))

    business["Street"] = street
    business["City"] = city
    business["State"] = state
    business["Zipcode"] = zipcode


def parse_place123(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (og:title matches the visible heading) ----
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        business["Business Name"] = clean(og_title["content"])

    if not business["Business Name"]:
        h_tag = soup.find("h4", class_="locaheader") or soup.find(re.compile(r"^h[1-6]$"))
        if h_tag:
            business["Business Name"] = clean(h_tag.get_text())

    # ---- Logo ----
    # Listings without an uploaded logo show place123's own generic map-pin
    # icon (/images/poi.png) in the same spot -- that is not the business's
    # logo, so any image served from place123's /images/ folder is ignored.
    logo_img = soup.find("img", alt=re.compile("location logo", re.I))
    if logo_img and logo_img.get("src"):
        logo_url = urljoin(url, logo_img["src"])
        if not _is_place123_placeholder(logo_url):
            business["Logo"] = logo_url

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            og_img_url = og_image["content"]
            # place123 emits a broken "graph.facebook.com//picture" when
            # there is no Facebook id -- ignore that placeholder.
            if "graph.facebook.com//" not in og_img_url:
                business["Logo"] = urljoin(url, og_img_url)

    content = _find_content_block(soup)

    # ---- Whole-block text as lines ----
    lines = [clean(line) for line in content.get_text(separator="\n").split("\n")]
    lines = [l for l in lines if l]

    # ---- Category (line right after the business-name heading) ----
    name_idx = None
    if business["Business Name"]:
        target = business["Business Name"].lower()
        for idx, line in enumerate(lines):
            if line.lower() == target:
                name_idx = idx
                break

    if name_idx is not None:
        if name_idx + 1 < len(lines) and not _is_stop(lines[name_idx + 1]):
            business["Category"] = lines[name_idx + 1]

    # ---- Street / City / State / Zipcode / Country (<address> block) ----
    _parse_place123_address(content, business)

    # ---- Label / value pairs (Owner Name, Phone, Website, Email, About
    #      Us, Related Searches, Hours) ----
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        # Case 1: "Phone Number: 0422 799 410" on one line
        inline_label, inline_value = _split_label_inline(line)
        if inline_label:
            field = _PLACE123_LABELS[_norm_label(inline_label)]
            if not business.get(field):
                business[field] = inline_value
            i += 1
            continue

        # Case 2: "Phone Number:" then value on the following line(s)
        if _is_label(line):
            field = _PLACE123_LABELS[_norm_label(line)]

            j = i + 1
            value_lines = []
            while j < n and not _is_stop(lines[j]):
                value_lines.append(lines[j])
                j += 1

            value = clean(" ".join(value_lines))
            if value and not business.get(field):
                business[field] = value

            i = j
        else:
            i += 1

    # ---- Phone: normalise, then fall back to tel: links / raw text ----
    business["Phone"] = _clean_phone(business.get("Phone", ""))

    if not business["Phone"]:
        tel_link = content.find("a", href=re.compile(r"^tel:", re.I))
        if tel_link:
            business["Phone"] = _clean_phone(
                tel_link.get_text() or tel_link["href"][4:]
            )

    if not business["Phone"]:
        # Look for a number next to any phone-ish word in the listing block.
        text = content.get_text(" ")
        m = re.search(
            r"(?:phone(?:\s*number)?|tel(?:ephone)?|mobile|contact\s*number)\s*:?\s*(\+?[\d\s().\-]{7,})",
            text,
            re.I,
        )
        if m:
            business["Phone"] = _clean_phone(m.group(1))

    # ---- Business Email: strip stray text, fall back to mailto: ----
    if business["Business Email"]:
        m = re.search(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", business["Business Email"])
        business["Business Email"] = m.group(0) if m else ""

    if not business["Business Email"]:
        mailto = content.find("a", href=re.compile(r"^mailto:", re.I))
        if mailto:
            business["Business Email"] = clean(mailto["href"][7:].split("?")[0])

    # ---- Website URL fallback (visible external anchor) ----
    if not business["Website URL"] or not business["Website URL"].startswith("http"):
        business["Website URL"] = ""
        for a in content.find_all("a", href=True):
            href = a["href"]
            low = href.lower()

            if not low.startswith("http"):
                continue
            if "place123.net" in low:
                continue
            if "facebook.com" in low or "disqus.com" in low:
                continue
            if "google.com" in low or "googleapis.com" in low:
                continue
            if any(domain in low for domain in SOCIAL_DOMAINS):
                continue

            business["Website URL"] = href
            break

    # ---- Description fallback (meta description -- truncated SEO
    #      snippet of the same "About Us" copy, so About Us wins) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    return business