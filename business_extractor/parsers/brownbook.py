"""
Site parser: brownbook.net

Brownbook is a Next.js (App Router) site. The visible HTML is only partly
server-rendered (hours/reviews load client-side), but the full business
record is embedded in the page in two places, which are far more reliable
than scraping the DOM:

  1. <script type="application/ld+json">  (LocalBusiness: name, address,
     phone, email, description, logo)
  2. The Next.js flight stream, i.e. the self.__next_f.push([1, "..."])
     calls, which contain a `"business":{...}` object with the complete
     record (tags, social links, hours, website, category, images...).

Address note: on listings seen so far `city`, `state` and `zip_code` are
null and the whole address is one string ("9657 US-98 W, Miramar Beach,
FL 32550"), so we split it ourselves when the structured fields are empty.
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\]\)')

_US_ADDRESS_RE = re.compile(
    r"^(?P<street>.*?),\s*(?P<city>[^,]+),\s*(?P<state>[A-Za-z]{2})\s+"
    r"(?P<zip>\d{5}(?:-\d{4})?)$"
)

_SOCIAL_FIELDS = {
    "facebook": "Facebook",
    "twitter": "Twitter",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "tiktok": "TikTok",
}

_DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _flight_stream(html_text):
    """Rebuild the Next.js flight stream from the self.__next_f.push calls."""
    parts = []
    for raw in _PUSH_RE.findall(html_text):
        try:
            parts.append(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            continue
    return "".join(parts)


def _extract_business_object(html_text):
    """Return the `business` dict embedded in the flight stream, or {}."""
    stream = _flight_stream(html_text)
    if not stream:
        return {}
    decoder = json.JSONDecoder()
    # `"business":{` is the real object; `"business":"$21:props..."` entries
    # are back-references and are skipped by requiring an opening brace.
    for match in re.finditer(r'"business":\s*(?=\{)', stream):
        try:
            obj, _ = decoder.raw_decode(stream, match.end())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("name") and "id" in obj:
            return obj
    return {}


def _extract_json_ld(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, list):
            data = next((d for d in data if isinstance(d, dict)), {})
        if isinstance(data, dict) and "LocalBusiness" in str(data.get("@type", "")):
            return data
    return {}


def _text(value):
    return clean(value) if isinstance(value, str) else ""


def _split_address(biz, ld):
    """Return (street, city, state, zipcode)."""
    city = _text(biz.get("city"))
    state = _text(biz.get("state"))
    zipcode = _text(biz.get("zip_code"))
    street = _text(biz.get("address_line_1"))

    if city and state:
        if not street:
            street = _text(biz.get("address"))
        return street, city, state, zipcode

    full = _text(biz.get("address")) or _text((ld.get("address") or {}).get("streetAddress"))
    if not full:
        return street, city, state, zipcode

    match = _US_ADDRESS_RE.match(full)
    if match:
        return (
            clean(match.group("street")),
            clean(match.group("city")),
            match.group("state").upper(),
            match.group("zip"),
        )
    # Not the standard US shape: fall back to the generic comma splitter.
    return _split_blinx_address(full)


def _format_hours(hours):
    """business_hours is {} when unset; the populated shape isn't known, so
    handle dict-of-days, list-of-rows and plain strings defensively."""
    if not hours:
        return ""
    if isinstance(hours, str):
        return clean(hours)

    def _row_text(day, val):
        if isinstance(val, dict):
            if val.get("closed") or val.get("is_closed"):
                val = "Closed"
            else:
                start = val.get("open") or val.get("from") or val.get("start") or ""
                end = val.get("close") or val.get("to") or val.get("end") or ""
                val = f"{start} - {end}".strip(" -")
        elif isinstance(val, (list, tuple)):
            val = ", ".join(_text(v) if isinstance(v, str) else json.dumps(v) for v in val)
        val = clean(str(val)) if val not in (None, "") else ""
        return f"{clean(str(day)).title()}: {val}" if val else ""

    rows = []
    if isinstance(hours, dict):
        keys = sorted(
            hours,
            key=lambda k: _DAY_ORDER.index(str(k).lower()) if str(k).lower() in _DAY_ORDER else 99,
        )
        rows = [_row_text(k, hours[k]) for k in keys]
    elif isinstance(hours, list):
        for item in hours:
            if isinstance(item, dict):
                day = item.get("day") or item.get("name") or ""
                rows.append(_row_text(day, item))
            elif isinstance(item, str):
                rows.append(clean(item))
    return "; ".join(r for r in rows if r)


def _image_urls(items):
    urls = []
    for item in items or []:
        if isinstance(item, str):
            url = item
        elif isinstance(item, dict):
            url = item.get("url") or item.get("src") or item.get("path") or item.get("image") or ""
        else:
            url = ""
        if url and url not in urls:
            urls.append(url)
    return urls


def parse_brownbook(url, html):
    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    biz = _extract_business_object(html)
    ld = _extract_json_ld(soup)

    # ---- Business Name ----
    name = _text(biz.get("name")) or _text(ld.get("name"))
    if not name:
        heading = soup.find("span", class_=lambda c: c and "text-4xl" in c and "font-bold" in c)
        if heading:
            name = clean(heading.get_text())
    business["Business Name"] = name

    # ---- Owner ----
    business["Owner Name"] = _text(biz.get("owner_name")) or _text((ld.get("founder") or {}).get("name"))

    # ---- Address ----
    street, city, state, zipcode = _split_address(biz, ld)
    business["Street"] = street
    business["City"] = city
    business["State"] = state
    business["Zipcode"] = zipcode
    business["Country"] = _text(biz.get("country")) or _text(biz.get("country_code")) \
        or _text((ld.get("address") or {}).get("addressCountry"))

    # ---- Phone ----
    phone = _text(biz.get("phone")) or _text(ld.get("telephone"))
    if not phone:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            phone = clean(tel.get_text())
    business["Phone"] = phone

    # ---- Website URL ----
    website = _text(biz.get("website"))
    if not website:
        for a in soup.select('a[target="_blank"][href^="http"]'):
            if "brownbook" not in a["href"] and clean(a.get_text()).startswith("http"):
                website = a["href"]
                break
    business["Website URL"] = website

    # ---- Keywords (tags) ----
    keywords = []
    for tag in biz.get("tags") or []:
        tag_name = _text(tag.get("name")) if isinstance(tag, dict) else _text(tag)
        if tag_name and tag_name.lower() not in (k.lower() for k in keywords):
            keywords.append(tag_name)
    business["Keywords"] = ", ".join(keywords)

    # ---- Description ----
    description = _text(biz.get("description")) or _text(ld.get("description"))
    if not description:
        overview = soup.select_one("div.prose")
        if overview:
            description = clean(overview.get_text(" "))
    business["Description"] = description

    # ---- Hours ----
    business["Hours"] = _format_hours(biz.get("business_hours"))

    # ---- Social Media Links ----
    for key, label in _SOCIAL_FIELDS.items():
        value = _text(biz.get(key))
        if value:
            business["Social Media Links"][label] = value

    # ---- Business Email ----
    email = _text(biz.get("email")) or _text(ld.get("email"))
    if not email:
        mail = soup.select_one('a[href^="mailto:"]')
        if mail:
            email = mail["href"].replace("mailto:", "").strip()
    business["Business Email"] = email

    # ---- Category ----
    # Only the site's own `category`. `naics_title` is deliberately NOT used:
    # it is an automatic classification that is frequently wrong (e.g. a
    # real-estate agent tagged "Business Service Centers").
    category = biz.get("category")
    if isinstance(category, dict):
        category = category.get("name")
    elif isinstance(category, list):
        category = ", ".join(_text(c.get("name") if isinstance(c, dict) else c) for c in category)
    business["Category"] = _text(category)

    # ---- Logo ----
    logo = _text(biz.get("logo")) or _text(ld.get("logo"))
    if not logo:
        img = soup.select_one("#business-logo img")
        if img and img.get("src"):
            logo = urljoin(url, img["src"])
    if logo and "logo-placeholder" in logo:
        logo = ""
    business["Logo"] = logo

    # ---- Photos ----
    business["Photos"] = _image_urls(biz.get("images"))

    return business