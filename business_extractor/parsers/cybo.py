"""
Site parser: cybo.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



CYBO_SOCIAL_TAG_MAP = {
    "fb": "Facebook",
    "tw": "Twitter",
    "yt": "YouTube",
    "linkedin": "LinkedIn",
    "instagram": "Instagram",
    "tiktok": "TikTok",
}

CYBO_NETWORK_DOMAIN_ROOT = {
    "TikTok": "tiktok.com",
}

_JSONLD_DAYS = {
    "mo": "Monday", "tu": "Tuesday", "we": "Wednesday", "th": "Thursday",
    "fr": "Friday", "sa": "Saturday", "su": "Sunday",
}


def _to_12h(hhmm):
    """'19:30' -> '7:30 PM' (leaves anything that is not HH:MM untouched)."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", hhmm.strip())
    if not m:
        return hhmm.strip()
    h, mi = int(m.group(1)), m.group(2)
    suffix = "AM" if h < 12 or h == 24 else "PM"
    return f"{(h % 12) or 12}:{mi} {suffix}"


def _cybo_hours(soup):
    """Opening hours of the current Cybo layout.

    1. Rendered rows:  <div class="...hoursRow"><span class="...hoursDay">Every day</span>
                       <span><span>7:00 AM - 7:30 PM</span></span></div>
       (present in the HTML even while the "Hours" card is collapsed; class names
       carry a build hash, so they are matched by their "hoursRow"/"hoursDay" part)
    2. JSON-LD "openingHours": ["Mo 7:00-19:30", ...]
    """
    parts = []
    for row in soup.find_all(class_=re.compile(r"hoursRow")):
        day_tag = row.find(class_=re.compile(r"hoursDay"))
        day = clean(day_tag.get_text(" ")) if day_tag else ""
        times = []
        for sp in (day_tag.find_next_siblings() if day_tag else row.find_all("span")):
            leaves = [x for x in sp.find_all("span") if not x.find("span")] or [sp]
            times += [clean(x.get_text(" ")) for x in leaves if clean(x.get_text(" "))]
        if day and times:
            parts.append(f"{day}: {', '.join(times)}")
    if parts:
        return "; ".join(parts)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except Exception:
            continue
        for item in (data if isinstance(data, list) else [data]):
            oh = item.get("openingHours") if isinstance(item, dict) else None
            if isinstance(oh, str):
                oh = [oh]
            if not oh:
                continue
            by_day = {}
            for entry in oh:
                m = re.match(r"^\s*([A-Za-z,\-]+)\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$", entry)
                if not m:
                    continue
                span = f"{_to_12h(m.group(2))} \u2013 {_to_12h(m.group(3))}"
                for d in re.split(r",", m.group(1)):
                    d = d.strip().lower()[:2]
                    if d in _JSONLD_DAYS:
                        by_day.setdefault(_JSONLD_DAYS[d], []).append(span)
            if by_day:
                if len(by_day) == 7 and len({tuple(v) for v in by_day.values()}) == 1:
                    return f"Every day: {', '.join(next(iter(by_day.values())))}"
                return "; ".join(f"{d}: {', '.join(v)}" for d, v in by_day.items())
    return ""


def parse_cybo(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()
    page_text = soup.get_text("\n")

    # ---- Business Name ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Street  ----
    maps_link = soup.select_one('a[href^="https://www.google.com/maps/search/"]')
    if maps_link:
        business["Street"] = clean(maps_link.get_text())
        business["GBP Link"] = maps_link["href"]

    # ---- City / State / Zipcode / Country (labeled "Address" block) ----
    city_match = re.search(r"\bCity:\s*([^\n]+)", page_text)
    if city_match:
        business["City"] = clean(city_match.group(1))
    state_match = re.search(r"\bState:\s*([^\n]+)", page_text)
    if state_match:
        business["State"] = clean(state_match.group(1))
    zip_match = re.search(r"\bPostal Code:\s*([^\n]+)", page_text)
    if zip_match:
        business["Zipcode"] = clean(zip_match.group(1))
    country_match = re.search(r"\bCountry:\s*([^\n]+)", page_text)
    if country_match:
        business["Country"] = clean(country_match.group(1))

    # ---- Zipcode fallback ----
    if business["Street"] and business["City"]:
        tail_pattern = r",?\s*" + re.escape(business["City"])
        if business["State"]:
            tail_pattern += r",?\s*" + re.escape(business["State"])
        tail_pattern += r"\s*(\d{5}(?:-\d{4})?)?\s*$"
        tail_match = re.search(tail_pattern, business["Street"], re.I)
        if tail_match:
            if not business["Zipcode"] and tail_match.group(1):
                business["Zipcode"] = tail_match.group(1)
            business["Street"] = clean(business["Street"][:tail_match.start()].rstrip(","))

    # ---- Phone  ----
    phone_link = soup.select_one('a[href*="/phone/how-to-call/"]')
    if phone_link:
        business["Phone"] = clean(phone_link.get_text())

    # ---- Website URL  ----
    for a in soup.select('a[href*="/r/biz/web"]'):
        href = a.get("href", "")
        tag_match = re.search(r"[?&]social_tag=([^&]+)", href)
        if not tag_match:
            if not business["Website URL"]:
                site_text = clean(a.get_text())
                business["Website URL"] = site_text if site_text else href
            continue
        network = CYBO_SOCIAL_TAG_MAP.get(tag_match.group(1).lower(), tag_match.group(1).title())
        link_text = clean(a.get_text())
        value = href
        domain_root = CYBO_NETWORK_DOMAIN_ROOT.get(network)
        if domain_root:
            idx = link_text.lower().find(domain_root)
            if idx != -1:
                value = link_text[idx:]
        business["Social Media Links"][network] = value

    # ---- Description ("About" section) ----
    about_label = soup.find(string=re.compile(r"^\s*About\s*$"))
    if about_label:
        block = about_label.find_parent(["h1", "h2", "h3", "h4", "div", "span"]) or about_label
        next_block = block.find_next(["p", "div"])
        if next_block:
            desc_text = clean(next_block.get_text())
            if is_meaningful(desc_text):
                business["Description"] = desc_text
    if not business["Description"]:
        about_match = re.search(
            r"\nAbout\n+(.+?)\n\n(?:💳|👥|\*\*Categories|Categories:|##|$)",
            page_text, re.S,
        )
        if about_match:
            desc_text = clean(about_match.group(1))
            if is_meaningful(desc_text):
                business["Description"] = desc_text
    if not business["Description"]:
        meta_desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Hours ----
    business["Hours"] = _cybo_hours(soup)
    hours_match = None if business["Hours"] else re.search(r"\bHours\s*\n(.*?)\nPhone\b", page_text, re.S)
    if hours_match:
        hour_lines = [clean(line) for line in hours_match.group(1).split("\n")]
        hour_lines = [line for line in hour_lines if line and line != "\u25be"]
        detail_lines = [line for line in hour_lines if "day" in line.lower() or ":" in line]
        chosen = detail_lines[-1] if detail_lines else (hour_lines[-1] if hour_lines else "")
        chosen = re.sub(r"(?<=[a-z])(?=\d)", " ", chosen)
        if is_meaningful(chosen):
            business["Hours"] = chosen

    # ---- Category ----
    cat_match = re.search(r"\*?\*?Categories:\*?\*?\s*([^\n.]+)", page_text)
    if cat_match:
        business["Category"] = clean(cat_match.group(1))
    if not business["Category"]:
        # Fallback: the category pill/tag link under the header, which
        # (unlike the location breadcrumb links above it) points at a
        # two-segment /US/<city-state-slug>/<category-slug> path.
        cat_link = soup.find("a", href=re.compile(r"^/US/[a-z0-9-]+/[a-z0-9-]+/?$"))
        if cat_link:
            business["Category"] = clean(cat_link.get_text())


    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])

    return business