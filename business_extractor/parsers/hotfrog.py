from ..common import *


_HF_HOST = "hotfrog."


def parse_hotfrog(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "html.parser")

    ld = _hf_local_business_ld(soup)
    address = ld.get("address") or {}
    if not isinstance(address, dict):
        address = {}

    business["Business Name"] = clean(ld.get("name", "")) or _hf_name_fallback(soup)

    business["Street"] = clean(address.get("streetAddress", ""))
    business["City"] = clean(address.get("addressLocality", ""))
    business["State"] = clean(address.get("addressRegion", ""))
    business["Zipcode"] = clean(address.get("postalCode", ""))

    # The description carries the phone on some listings, so split it first.
    desc_phone, description = _hf_description(soup, ld)

    business["Phone"] = _hf_phone(soup, ld) or desc_phone
    business["Website URL"] = _hf_website(soup)
    business["Description"] = description
    business["Keywords"] = _hf_keywords(soup, ld)
    business["Category"] = _hf_category(soup, ld)
    business["Hours"] = _hf_hours(soup, ld)
    business["Logo"] = _hf_logo(soup, ld)
    business["Photos"] = _hf_photos(soup, ld, business["Logo"])
    business["Social Media Links"] = _hf_social_links(soup, ld)

    return filter_business_fields(business, url)


# ---------------------------------------------------------------- JSON-LD

def _hf_local_business_ld(soup):
    """Pick the LocalBusiness block out of the page's JSON-LD scripts
    (it also ships WebSite and Organization blocks for Hotfrog itself,
    sometimes inside an @graph)."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw, strict=False)
        except (ValueError, TypeError):
            continue
        nodes = []
        if isinstance(data, dict):
            nodes = data.get("@graph") if isinstance(data.get("@graph"), list) else [data]
        elif isinstance(data, list):
            nodes = data
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type")
            types = node_type if isinstance(node_type, list) else [node_type]
            if "LocalBusiness" in types:
                return node
    return {}


def _hf_as_list(value):
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


# ------------------------------------------------------------------ fields

def _hf_name_fallback(soup):
    el = soup.select_one(".entity h3.entity-title a") or soup.select_one("h3.entity-title a")
    return clean(el.get_text()) if el else ""


def _hf_is_hotfrog(href):
    try:
        host = urlparse(href).netloc.lower()
    except Exception:
        return False
    return _HF_HOST in host


def _hf_website(soup):
    """The business's own site. Never the Hotfrog listing URL (which is
    what JSON-LD `url` holds)."""
    for a in soup.select('a[data-entity-metric="website"]'):
        href = (a.get("href") or "").strip()
        if href.lower().startswith(("http://", "https://")) and not _hf_is_hotfrog(href):
            return href
    return ""


def _hf_phone(soup, ld):
    for a in soup.select('a[href^="tel:"], a[data-entity-metric="phone"]'):
        href = (a.get("href") or "").strip()
        if href.lower().startswith("tel:"):
            value = clean(href[4:])
        else:
            value = clean(a.get_text())
        if is_meaningful(value):
            return value
    return clean(ld.get("telephone", ""))


_HF_PHONE_PREFIX_RE = re.compile(
    r"^\s*Phone\s*:\s*(?P<phone>\+?[\d(][\d\s().\-]{5,}\d)\s*(?:\n|$)", re.I
)
_HF_ABOUT_PREFIX_RE = re.compile(r"^\s*About\s+Us\s*:\s*", re.I)


def _hf_description(soup, ld):
    """Returns (phone_from_description, description_text).

    Hotfrog's profile text is "Phone:\\n<number>\\n\\nAbout Us:\\n<text>"
    on listings whose owner put the phone into the profile body.
    """
    raw = ""
    desc_el = soup.select_one(".entity-desc")
    if desc_el:
        blocks = [p.get_text("\n") for p in desc_el.find_all("p")]
        raw = "\n\n".join(b for b in blocks if b and b.strip())
    if not raw:
        raw = ld.get("description", "") or ""
    raw = html_lib.unescape(raw).replace("\r\n", "\n").strip()
    if not raw:
        return "", ""

    phone = ""
    match = _HF_PHONE_PREFIX_RE.match(raw)
    if match:
        phone = clean(match.group("phone"))
        raw = raw[match.end():]
    raw = _HF_ABOUT_PREFIX_RE.sub("", raw.lstrip(), count=1)

    # Keep paragraph breaks, collapse the stray single newlines inside a paragraph.
    paragraphs = [clean(block) for block in re.split(r"\n\s*\n", raw)]
    paragraphs = [p for p in paragraphs if is_meaningful(p)]
    return phone, "\n\n".join(paragraphs)


def _hf_keywords(soup, ld):
    tags = [clean(s.get_text()) for s in soup.select(".entity-tags .csv-list span")]
    tags = [t for t in tags if is_meaningful(t)]
    if not tags:
        tags = [clean(k) for k in _hf_as_list(ld.get("keywords")) if isinstance(k, str)]
        tags = [t for t in tags if is_meaningful(t)]
    return ", ".join(dict.fromkeys(tags))


def _hf_category(soup, ld):
    cats = [clean(s.get_text()) for s in soup.select(".entity-categories [data-category]")]
    cats = [c for c in cats if is_meaningful(c)]
    if not cats:
        cats = [clean(c) for c in _hf_as_list(ld.get("knowsAbout")) if isinstance(c, str)]
        cats = [c for c in cats if is_meaningful(c)]
    return ", ".join(dict.fromkeys(cats))


_HF_DAY_SHORT = {
    "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu",
    "friday": "Fri", "saturday": "Sat", "sunday": "Sun",
}


def _hf_hours(soup, ld):
    """Best-effort: JSON-LD first, then a rendered hours block. Returns
    "" when the listing publishes no hours (as the sample page did)."""
    entries = []

    specs = ld.get("openingHoursSpecification")
    for spec in _hf_as_list(specs):
        if not isinstance(spec, dict):
            continue
        opens, closes = clean(spec.get("opens", "")), clean(spec.get("closes", ""))
        for day in _hf_as_list(spec.get("dayOfWeek")):
            day_name = str(day).rsplit("/", 1)[-1].strip()
            label = _HF_DAY_SHORT.get(day_name.lower(), day_name)
            if opens and closes:
                entries.append(f"{label}: {opens}-{closes}")
            elif label:
                entries.append(f"{label}: Closed")
    if entries:
        return "; ".join(entries)

    simple = [clean(h) for h in _hf_as_list(ld.get("openingHours")) if isinstance(h, str)]
    simple = [h for h in simple if is_meaningful(h)]
    if simple:
        return "; ".join(simple)

    box = soup.select_one(".entity-hours, .entity-opening-hours, section[title*='Hours' i]")
    if box:
        rows = [clean(r.get_text(" ")) for r in box.select("li, tr")]
        rows = [r for r in rows if is_meaningful(r)]
        if rows:
            return "; ".join(rows)
    return ""


def _hf_logo(soup, ld):
    for value in _hf_as_list(ld.get("logo")):
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict) and value.get("url"):
            return str(value["url"]).strip()
    img = soup.select_one("#bdp-image-title .entity-image img, .entity-image img")
    if img and (img.get("src") or "").strip():
        return img["src"].strip()
    return ""


def _hf_size_less(url):
    """The same asset can show up as .../large.png and .../medium.png;
    compare on the directory so a logo isn't counted as a photo."""
    return url.rsplit("/", 1)[0] if url else url


def _hf_photos(soup, ld, logo):
    photos = []
    for a in soup.select("#photo-gallery a.entity-photo"):
        href = (a.get("href") or "").strip()
        if href:
            photos.append(href)

    if not photos:
        for value in _hf_as_list(ld.get("image")):
            if isinstance(value, dict):
                value = value.get("url", "")
            if isinstance(value, str) and value.strip():
                photos.append(value.strip())

    logo_key = _hf_size_less(logo)
    out, seen = [], set()
    for p in photos:
        key = _hf_size_less(p)
        if key == logo_key or key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _hf_social_links(soup, ld):
    links = {}

    for same_as in _hf_as_list(ld.get("sameAs")):
        if isinstance(same_as, str):
            _hf_add_social(links, same_as)

    # Listing content only -- the footer/header carry Hotfrog's own icons.
    scope = soup.select_one("main#main") or soup.select_one("#main")
    if scope:
        for a in scope.find_all("a", href=True):
            _hf_add_social(links, a["href"].strip())
    return links


def _hf_add_social(links, href):
    if _hf_is_hotfrog(href):
        return
    for domain, label in SOCIAL_DOMAINS.items():
        if _hostname_matches_social_domain(href, domain):
            links.setdefault(label, href)
            return