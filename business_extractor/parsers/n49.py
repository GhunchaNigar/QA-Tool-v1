"""
Site parser: n49.com

Path in project:  business_extractor/parsers/n49.py

n49 redesigned its listing pages. The old `var n49Business = {...}` object is
gone; the data now lives in (in order of preference):
  1. `var N49Cfg = {... "ctx": {"biz": {...}}}`   (full record, incl. social/hours)
  2. <script type="application/ld+json"> LocalBusiness
  3. the visible HTML (h2 name, address link, tel: link, mailto:, ...)
The legacy `var n49Business` format is still handled first, in case some
pages are served with the old template.
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


def _extract_balanced_json_object(text, start_marker):
    """Find `start_marker` in `text`, then return the JSON substring of the
    first balanced {...} object that follows it (handles nested {} / [])."""
    marker_pos = text.find(start_marker)
    if marker_pos == -1:
        return None

    brace_start = text.find("{", marker_pos)
    if brace_start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(brace_start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start:i + 1]
    return None


_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_US_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT "
    "NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)
_CA_PROVINCES = set("AB BC MB NB NL NS NT NU ON PE QC SK YT".split())


def _format_hours(ops_hours, hours_text=""):
    """Accepts the legacy {day: [times]} dict, a {Day: "9-5"} dict, a list of
    strings / {day, hours} dicts, or a plain string. Returns "" when unknown."""
    if hours_text == "doNotDisplay" or not ops_hours:
        return ""
    if isinstance(ops_hours, str):
        return clean(ops_hours)
    parts = []
    if isinstance(ops_hours, dict):
        lower = {str(k).lower(): v for k, v in ops_hours.items()}
        for day in _DAYS:
            times = lower.get(day)
            if not times:
                continue
            if isinstance(times, (list, tuple)):
                times = ", ".join(str(t) for t in times)
            parts.append(f"{day.capitalize()}: {times}")
    elif isinstance(ops_hours, list):
        for item in ops_hours:
            if isinstance(item, str):
                parts.append(clean(item))
            elif isinstance(item, dict):
                day = item.get("day") or item.get("name") or ""
                val = item.get("hours") or item.get("times") or item.get("value") or ""
                if isinstance(val, (list, tuple)):
                    val = ", ".join(str(v) for v in val)
                if day or val:
                    parts.append(clean(f"{day}: {val}"))
    return "; ".join(p for p in parts if p)


def _country_from_region(region, is_canadian=False):
    region = (region or "").strip().upper()
    if is_canadian or region in _CA_PROVINCES:
        return "CA"
    if region in _US_STATES:
        return "US"
    return ""


def _street_only(address, city="", region=""):
    """'6800 Burnet Rd Ste 8,, Austin, TX' -> '6800 Burnet Rd Ste 8'"""
    address = clean(address)
    if not address:
        return ""
    if city:
        pattern = r",[\s,]*" + re.escape(city) + r"(?:[\s,]+" + re.escape(region or "") + r")?[\s,]*(?:\d{5}(?:-\d{4})?|[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d)?\s*$"
        address = re.sub(pattern, "", address, flags=re.I)
    return clean(address.strip(" ,"))


def _first_str(*values):
    for v in values:
        if isinstance(v, str) and v.strip():
            return clean(v)
    return ""


def _fill_from_biz(business, biz, url):
    """Fill `business` from the N49Cfg ctx.biz dict."""
    business["Business Name"] = _first_str(biz.get("name"), business["Business Name"])

    city = _first_str(biz.get("city"))
    region = _first_str(biz.get("province"))
    business["City"] = city or business["City"]
    business["State"] = region or business["State"]
    business["Zipcode"] = _first_str(biz.get("postal")) or business["Zipcode"]
    if biz.get("showAddress", True):
        business["Street"] = _street_only(biz.get("address", ""), city, region) or business["Street"]
    business["Country"] = (
        _first_str(biz.get("countryCode"), biz.get("country"))
        or _country_from_region(region, bool(biz.get("isCanadian")))
        or business["Country"]
    )

    phones = biz.get("phones") or []
    phone = _first_str(*(phones if isinstance(phones, list) else [phones]), biz.get("tollfree"))
    business["Phone"] = phone or business["Phone"]

    business["Website URL"] = _first_str(biz.get("website")) or business["Website URL"]
    business["Business Email"] = _first_str(biz.get("email")) or business["Business Email"]
    business["Description"] = _first_str(biz.get("about"), biz.get("description")) or business["Description"]

    hours = _format_hours(biz.get("hours"))
    if hours:
        business["Hours"] = hours

    for field_name, network in (
        ("facebook", "Facebook"), ("twitter", "Twitter"), ("instagram", "Instagram"),
        ("pinterest", "Pinterest"), ("linkedin", "LinkedIn"), ("youtube", "YouTube"),
    ):
        value = _first_str(biz.get(field_name))
        if value:
            business["Social Media Links"].setdefault(network, value)

    cats = biz.get("categories") or []
    names = []
    for c in cats:
        if isinstance(c, str):
            names.append(c)
        elif isinstance(c, dict) and c.get("name"):
            names.append(c["name"])
    if names:
        business["Category"] = ", ".join(clean(n) for n in names if n)

    logo = _first_str(biz.get("logo"))
    if logo:
        business["Logo"] = urljoin(url, logo)

    photos = []
    for img in biz.get("gallery") or []:
        if isinstance(img, str):
            photos.append(urljoin(url, img))
        elif isinstance(img, dict):
            u = img.get("url") or img.get("src") or img.get("image")
            if u:
                photos.append(urljoin(url, u))
    if photos:
        business["Photos"] = photos


def _parse_legacy(business, data, url):
    """Old `var n49Business` object (kept for pages still on the old template)."""
    business["Business Name"] = clean(data.get("bName", ""))
    business["Street"] = clean((data.get("bAddr1") or "").rstrip(","))
    business["City"] = clean(data.get("bcity", ""))
    business["State"] = clean(data.get("bProvState", ""))
    business["Zipcode"] = clean(data.get("bPostalZip", ""))
    business["Country"] = clean(data.get("countryCode", ""))
    if data.get("bPhone1"):
        business["Phone"] = clean(data["bPhone1"])
    if data.get("bWebsite"):
        business["Website URL"] = clean(data["bWebsite"])
    if data.get("bEmail"):
        business["Business Email"] = clean(data["bEmail"])
    if data.get("bDesc"):
        business["Description"] = clean(data["bDesc"])
    business["Hours"] = _format_hours(data.get("bOpsHours"), data.get("hoursText", ""))
    for field_name, network in (
        ("facebookPageUrl", "Facebook"), ("facebook", "Facebook"),
        ("twitterHandle", "Twitter"), ("twitter", "Twitter"),
        ("instagram", "Instagram"), ("youtube", "YouTube"),
        ("pinterest", "Pinterest"), ("linkedin", "LinkedIn"),
    ):
        value = data.get(field_name)
        if value and network not in business["Social Media Links"]:
            business["Social Media Links"][network] = value
    categories = data.get("categories") or [
        c.get("name") for c in (data.get("categoryObjects") or []) if c.get("name")
    ]
    if categories:
        business["Category"] = ", ".join(categories)
    if data.get("logoImagePath"):
        business["Logo"] = urljoin(url, data["logoImagePath"])
    photos = [
        img["url"] for img in (data.get("galleryImages") or [])
        if isinstance(img, dict) and img.get("url")
    ]
    if photos:
        business["Photos"] = photos


def _fill_from_json_ld(business, soup, url):
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw, strict=False)
        except Exception:
            continue
        for obj in (data if isinstance(data, list) else [data]):
            if not isinstance(obj, dict):
                continue
            t = obj.get("@type")
            types = [str(x).lower() for x in (t if isinstance(t, list) else [t])]
            if not any(x in ("localbusiness", "organization") or x.endswith("business") for x in types):
                continue
            if not business["Business Name"]:
                business["Business Name"] = _first_str(obj.get("name"))
            if not business["Phone"]:
                business["Phone"] = _first_str(obj.get("telephone"))
            if not business["Description"]:
                business["Description"] = _first_str(obj.get("description"))
            if not business["Logo"] and isinstance(obj.get("image"), str):
                business["Logo"] = urljoin(url, obj["image"])
            addr = obj.get("address")
            if isinstance(addr, dict):
                city = _first_str(addr.get("addressLocality"))
                region = _first_str(addr.get("addressRegion"))
                if not business["City"]:
                    business["City"] = city
                if not business["State"]:
                    business["State"] = region
                if not business["Zipcode"]:
                    business["Zipcode"] = _first_str(addr.get("postalCode"))
                if not business["Street"]:
                    business["Street"] = _street_only(addr.get("streetAddress", ""), city, region)
                if not business["Country"]:
                    business["Country"] = _first_str(addr.get("addressCountry")) or _country_from_region(region)
            return


def _fill_from_html(business, soup, url):
    """Last resort: scrape what is visible on the page."""
    box = soup.select_one(".info-box.normal") or soup

    if not business["Business Name"]:
        h = box.select_one("h2") or soup.select_one("h1")
        if h:
            business["Business Name"] = clean(h.get_text())
    if not business["Business Name"] and soup.title and soup.title.string:
        t = clean(soup.title.string)
        if "n49.com" in t.lower():  # only trust the real listing title template
            business["Business Name"] = clean(t.split("|")[0])

    addr_a = box.select_one("a.n49-addr-link")
    if addr_a and not (business["Street"] and business["City"]):
        parts = [p.strip() for p in addr_a.get_text().split(",") if p.strip()]
        # "6800 Burnet Rd Ste 8,, Austin, TX, 78757"
        if len(parts) >= 4 and re.match(r"^[A-Za-z]\d[A-Za-z]|\d{5}", parts[-1]):
            business["Zipcode"] = business["Zipcode"] or parts[-1]
            business["State"] = business["State"] or parts[-2]
            business["City"] = business["City"] or parts[-3]
            business["Street"] = business["Street"] or ", ".join(parts[:-3])
        elif len(parts) >= 3:
            business["State"] = business["State"] or parts[-1]
            business["City"] = business["City"] or parts[-2]
            business["Street"] = business["Street"] or ", ".join(parts[:-2])

    if not business["Phone"]:
        tel = box.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = clean(tel.get_text()) or tel["href"].replace("tel:", "").strip()

    if not business["Website URL"]:
        for a in box.select('a[href^="http"]'):
            if clean(a.get_text()).lower() == "website":
                business["Website URL"] = a["href"].strip()
                break

    if not business["Business Email"]:
        mail = soup.select_one('a[href^="mailto:"]')
        if mail:
            business["Business Email"] = mail["href"].replace("mailto:", "").split("?")[0].strip()

    if not business["Description"]:
        about = box.select_one(".n49-about-full") or box.select_one(".n49-about")
        if about:
            business["Description"] = clean(about.get_text().replace("Read more", ""))

    if not business["Logo"]:
        img = box.select_one(".img-wrap img[src]")
        if img:
            business["Logo"] = urljoin(url, img["src"])

    if not business["Country"]:
        business["Country"] = _country_from_region(business["State"])


def parse_n49(url, html):

    business = empty_business()

    if _looks_blocked(html):
        return business

    # ---- 1. Legacy template (var n49Business) ----
    raw_json = _extract_balanced_json_object(html, "var n49Business")
    if raw_json:
        try:
            _parse_legacy(business, json.loads(raw_json), url)
            return business
        except Exception:
            business = empty_business()

    # ---- 2. Current template: var N49Cfg -> ctx.biz ----
    raw_cfg = _extract_balanced_json_object(html, "var N49Cfg")
    if raw_cfg:
        try:
            cfg = json.loads(raw_cfg)
            biz = ((cfg.get("ctx") or {}).get("biz")) or {}
            if isinstance(biz, dict) and biz:
                _fill_from_biz(business, biz, url)
        except Exception:
            pass

    # ---- 3/4. Fill any gaps from JSON-LD, then the visible HTML ----
    soup = BeautifulSoup(html, "lxml")
    _fill_from_json_ld(business, soup, url)
    _fill_from_html(business, soup, url)

    return business