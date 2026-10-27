"""
Site parser: bigbizmaker.com

BigBizMaker is a Brilliant Directories site (same platform / markup family as
bizmaker.org, biztobiz.org and nextbizmaker.com). Listing pages are
server-rendered, so a plain requests fetch is enough.

Where each field comes from (visible DOM first, JSON-LD second):

    Name         .header-member-name h1          -> LocalBusiness.name
    Address      .overview-tab-the-member-address -> LocalBusiness.address
    Phone        span.phone_number (hidden until "See Phone Number" is clicked,
                 but present in the HTML)         -> LocalBusiness.telephone
    Website      a.weblink                        -> LocalBusiness.sameAs
    Description  .froala-data.field-about_me      -> LocalBusiness.description
    Category     .profile-header-top-category     -> breadcrumb -> ProfilePage.about
    Logo         .profile-image img               -> LocalBusiness.image.url
    Hours        .table-display-hours (only when the listing has it)
    Social       profile-tab links to known social networks (only when present)

Things worth knowing:

  * The address block is one <div> with <br>-separated lines:
        <span>street</span><br>
        <span>city</span>, <span>state</span>, <span>zip</span><br>
        United States
    so it is parsed line by line. Do NOT feed just the first <span> to
    _split_blinx_address() -- that is the street only, and it would end up
    in State.
  * The JSON-LD telephone is malformed ("850460-2900"), so the visible number
    is preferred; the JSON-LD value is only a normalised fallback.
  * The "Share This Page" buttons are <button>s, not links, and sit outside the
    profile tabs, so they are never mistaken for the business's own social
    profiles.
"""

from ..common import *  # noqa: F401,F403

_OWN_HOST = "bigbizmaker.com"

_PLACEHOLDERS = {"", "n/a", "na", "none", "null", "-"}
_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
_STATE_ZIP_RE = re.compile(r"^(?P<state>.*?)\s+(?P<zip>\d{5}(?:-\d{4})?)$")
_PLACEHOLDER_IMAGE_HINTS = ("holder", "placeholder", "no-image", "noimage")
_COUNTRY_NAMES = {"US": "United States", "USA": "United States"}


def _val(value):
    """Clean a scalar; the site's "N/A"-style placeholders count as empty."""
    if not isinstance(value, str):
        return ""
    text = clean(value)
    return "" if text.lower() in _PLACEHOLDERS else text


def _ld_entries(soup):
    """Every JSON-LD entry on the page, with @graph blocks flattened."""
    entries = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw, strict=False)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            entries.extend(data["@graph"])
        elif isinstance(data, list):
            entries.extend(data)
        else:
            entries.append(data)
    return [e for e in entries if isinstance(e, dict)]


def _ld_find(entries, ld_type):
    for entry in entries:
        entry_type = entry.get("@type")
        if entry_type == ld_type or (isinstance(entry_type, list) and ld_type in entry_type):
            return entry
    return {}


def _format_us_phone(raw):
    """"850460-2900" / "8504602900" / "+1 850 460 2900" -> "(850) 460-2900".
    Anything that is not a 10-digit US number is returned cleaned, unchanged."""
    raw = clean(raw)
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return raw


def _address_lines(col):
    """Split the address <div> into its <br>-separated text lines."""
    lines, current = [], []
    for node in col.children:
        if getattr(node, "name", None) == "br":
            lines.append(clean("".join(current)))
            current = []
        elif isinstance(node, Comment):
            continue
        elif hasattr(node, "get_text"):
            current.append(node.get_text(""))
        else:
            current.append(str(node))
    lines.append(clean("".join(current)))
    return [line for line in lines if is_meaningful(line)]


def _split_locality(line):
    """"Miramar Beach, Florida, 32550" -> ("Miramar Beach", "Florida", "32550").
    Also copes with "Miramar Beach, FL 32550"."""
    parts = [p.strip() for p in line.split(",") if p.strip()]
    city = state = zipcode = ""
    state_from_zip_part = False

    if parts and _ZIP_RE.match(parts[-1]):
        zipcode = parts.pop()
    elif parts:
        m = _STATE_ZIP_RE.match(parts[-1])
        if m:
            zipcode = m.group("zip")
            parts[-1] = m.group("state")
            state_from_zip_part = True

    if len(parts) >= 2:
        state = parts[-1]
        city = ", ".join(parts[:-1])
    elif len(parts) == 1:
        if state_from_zip_part:
            state = parts[0]
        else:
            city = parts[0]
    return city, state, zipcode


def _parse_dom_address(soup):
    """-> dict with any of street / city / state / zipcode / country found in
    the visible address block (empty dict when the block is missing)."""
    col = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if not col:
        return {}

    lines = _address_lines(col)
    if not lines:
        return {}

    result = {}

    # Last line is the country when it is plain text ("United States").
    if len(lines) >= 2 and "," not in lines[-1] and not re.search(r"\d", lines[-1]):
        result["country"] = lines.pop()

    if len(lines) >= 2:
        result["street"] = ", ".join(lines[:-1])
        locality = lines[-1]
    elif re.match(r"^\d", lines[0]) and "," not in lines[0]:
        # A lone line that starts with a number is a street, not "City, ST".
        result["street"] = lines[0]
        locality = ""
    else:
        locality = lines[0]

    if locality:
        city, state, zipcode = _split_locality(locality)
        result.update({"city": city, "state": state, "zipcode": zipcode})
    return result


def _description(soup, ld_business):
    box = soup.select_one(".froala-data.field-about_me")
    if box:
        blocks = box.find_all(["p", "li", "h2", "h3", "h4", "h5", "h6"])
        chunks = []
        for block in blocks or [box]:
            text = block.get_text("\n") if block.find("br") else block.get_text(" ")
            chunks.extend(clean(line) for line in text.split("\n"))
        value = "\n".join(line for line in chunks if is_meaningful(line))
        if value:
            return value
    return _val(ld_business.get("description"))


def _category(soup, ld_page):
    tag = soup.select_one(".profile-header-top-category")
    if tag:
        value = clean(tag.get_text(" "))
        if value:
            return value

    # Breadcrumb: Home > Country > State > City > <Category> > <Business>
    current = soup.select_one("ol.breadcrumb li.breadcrumb-current")
    if current:
        previous = current.find_previous_sibling("li")
        if previous:
            value = clean(previous.get_text(" "))
            if value:
                return value

    about = ld_page.get("about")
    if isinstance(about, list) and about:
        return _val(about[0])
    return _val(about)


def _hours(scope):
    if not scope:
        return ""

    box = scope.select_one(".table-display-hours, .table-display-business-hours, .business-hours")
    if not box:
        # Generic "Hours" row in the Overview table: label in .col-sm-4, value in .col-sm-8.
        for group in scope.select(".table-view-group"):
            label = group.select_one(".col-sm-4")
            if label and re.search(r"\bhours\b", label.get_text(" "), re.I):
                box = group.select_one(".col-sm-8")
                break
    if not box:
        return ""

    rows = [clean(r.get_text(" ")) for r in box.select("li, tr, .row")]
    rows = [r for r in rows if is_meaningful(r)]
    if rows:
        return "; ".join(rows)

    text = clean(box.get_text(" "))
    text = re.sub(r"^(?:business\s+)?hours\s*:?\s*", "", text, flags=re.I)
    return text if is_meaningful(text) else ""


def _social_links(scope, website):
    socials = {}
    if not scope:
        return socials
    for a in scope.find_all("a", href=True):
        href = a["href"].strip()
        if not href.lower().startswith(("http://", "https://")):
            continue
        if _OWN_HOST in urlparse(href).netloc.lower() or href == website:
            continue
        for domain, label in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                socials.setdefault(label, href)
                break
    return socials


def _logo(soup, url, ld_business):
    tag = soup.select_one(".profile-image img")
    candidates = [tag.get("src") if tag else ""]

    ld_image = ld_business.get("image")
    candidates.append(ld_image.get("url") if isinstance(ld_image, dict) else ld_image)

    og = soup.find("meta", attrs={"property": "og:image"})
    candidates.append(og.get("content") if og else "")

    for src in candidates:
        src = _val(src)
        if not src or any(hint in src.lower() for hint in _PLACEHOLDER_IMAGE_HINTS):
            continue
        return urljoin(url, src)
    return ""


def parse_bigbizmaker(url, html_text):
    business = empty_business()
    if not html_text:
        return business

    soup = BeautifulSoup(html_text, "html.parser")

    entries = _ld_entries(soup)
    ld_business = _ld_find(entries, "LocalBusiness")
    ld_page = _ld_find(entries, "ProfilePage")
    ld_address = ld_business.get("address") if isinstance(ld_business.get("address"), dict) else {}

    # ---- Business Name ----
    name_tag = soup.select_one(".header-member-name h1")
    name = clean(name_tag.get_text()) if name_tag else ""
    if not name:
        name = _val(ld_business.get("name"))
    if not name:
        og = soup.find("meta", attrs={"property": "og:title"})
        name = re.sub(r"\s+on\s+BigBizMaker\s*$", "", clean(og.get("content", "")) if og else "", flags=re.I)
    business["Business Name"] = name

    # ---- Street / City / State / Zipcode / Country ----
    address = _parse_dom_address(soup)
    business["Street"] = address.get("street") or _val(ld_address.get("streetAddress"))
    business["City"] = address.get("city") or _val(ld_address.get("addressLocality"))
    business["State"] = address.get("state") or _val(ld_address.get("addressRegion"))
    business["Zipcode"] = address.get("zipcode") or _val(ld_address.get("postalCode"))
    country = address.get("country") or _val(ld_address.get("addressCountry"))
    business["Country"] = _COUNTRY_NAMES.get(country.upper(), country)

    # ---- Phone ----
    phone_tag = soup.select_one("span.phone_number") or soup.select_one(".phone_number_header")
    phone = clean(phone_tag.get_text()) if phone_tag else ""
    if not re.search(r"\d", phone):
        phone = _format_us_phone(_val(ld_business.get("telephone")))
    business["Phone"] = phone

    # ---- Website URL ----
    website_tag = soup.select_one("a.weblink")
    website = clean(website_tag.get("href", "")) if website_tag else ""
    if not website:
        same_as = ld_business.get("sameAs")
        for candidate in (same_as if isinstance(same_as, list) else [same_as]):
            candidate = _val(candidate)
            if candidate.lower().startswith(("http://", "https://")) and _OWN_HOST not in candidate.lower():
                website = candidate
                break
    business["Website URL"] = website

    # ---- Description ----
    business["Description"] = _description(soup, ld_business)

    # ---- Category ----
    business["Category"] = _category(soup, ld_page)

    # ---- Logo ----
    business["Logo"] = _logo(soup, url, ld_business)

    # ---- Hours / Social Media Links ----
    # Not present on every listing; both stay at the empty_business()
    # defaults ("" and {}) when the page has no source for them.
    scope = soup.select_one(".member-profile-tabs") or soup.select_one("#main-content")
    business["Hours"] = _hours(scope)
    business["Social Media Links"] = _social_links(scope, website)

    return business