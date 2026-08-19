"""
Site parser: vymaps.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


def _vymaps_jsonld(soup):
    """Return the first LocalBusiness JSON-LD object on the page, if any.

    strict=False is required here: this template sometimes emits a raw,
    unescaped newline inside a JSON string value (e.g. streetAddress
    spanning "street\\ncity, state\\nzip" as literal line breaks rather
    than "\\n" escapes). Strict-mode json.loads rejects control
    characters inside strings and raises, which silently drops the
    entire JSON-LD block -- including fields like addressCountry that
    aren't rendered as visible page text anywhere else.
    """
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if isinstance(obj, dict) and obj.get("@type") == "LocalBusiness":
                return obj
    return {}


def _vymaps_split_camel_tag(tag):
    """Insert spaces at CamelCase word boundaries.

    This template sometimes glues an entire tag list into a single
    hashtag with no delimiters at all, e.g.
    "#PersonalInjuryLawyerCarAccidentLawyerSlipAndFallLawyerDogBiteLawyer".
    There's no reliable way to recover which words belonged to which
    original tag from that alone, but splitting on capital letters at
    least turns it into a readable, space-separated string instead of
    one unbroken run of words.
    """
    words = re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", tag)
    return " ".join(words) if words else tag


def parse_vymaps(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    jsonld = _vymaps_jsonld(soup)

    # ---- Business Name ----
    h1 = soup.select_one(".profile-cover-content h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"] and jsonld.get("name"):
        business["Business Name"] = clean(jsonld["name"])

    # ---- Address ----
    addr_link = soup.select_one("a.listing-address[href]")
    if addr_link:
        # This template renders the address as three literal lines --
        # street / "city, state" / zip -- separated by real newlines.
        # Grabbing the raw text (before clean() collapses whitespace)
        # lets us split on that structure directly instead of guessing
        # from a generic comma-split, which has no way to tell the city
        # apart from the rest of the street once the newlines are gone.
        raw_addr = addr_link.get_text()
        lines = [clean(line) for line in raw_addr.split("\n")]
        lines = [l for l in lines if l]

        if len(lines) >= 3:
            business["Street"] = lines[0]
            business["Zipcode"] = lines[-1]
            city_state = lines[1]
            if "," in city_state:
                city_part, state_part = city_state.split(",", 1)
                business["City"] = clean(city_part)
                business["State"] = clean(state_part)
            else:
                business["City"] = city_state
        else:
            addr_text = clean(raw_addr)
            if is_meaningful(addr_text):
                street, city, state, zipcode = _split_blinx_address(addr_text)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode

        if _is_maps_link(addr_link["href"]):
            business["GBP Link"] = addr_link["href"]

    # ---- Country ----
    # Prefer the rendered "Places list in <country>" link (globe icon,
    # e.g. "United States") over JSON-LD's raw two-letter addressCountry
    # code -- it's what's actually shown on the page, and it doesn't
    # depend on the JSON-LD block having parsed successfully.
    country_icon = soup.select_one(".profile-cover-content .cover-buttons i.fa-globe")
    if country_icon and country_icon.parent:
        country_text = clean(country_icon.parent.get_text())
        if is_meaningful(country_text):
            business["Country"] = country_text

    if not business["Country"]:
        addr_obj = jsonld.get("address")
        if isinstance(addr_obj, dict) and addr_obj.get("addressCountry"):
            business["Country"] = clean(addr_obj["addressCountry"])

    # ---- Phone ----
    tel = soup.select_one('a[href^="tel:"]')
    if tel and tel.get("href"):
        business["Phone"] = tel["href"].replace("tel:", "").strip()
    if not business["Phone"] and jsonld.get("telephone"):
        business["Phone"] = clean(jsonld["telephone"])

    # ---- Website URL ----
    site_link = soup.select_one('a[aria-label="Website"][href]')
    if site_link and site_link.get("href"):
        business["Website URL"] = site_link["href"]
    if not business["Website URL"] and jsonld.get("url"):
        business["Website URL"] = jsonld["url"]

    # ---- Business Email (Cloudflare-obfuscated) ----
    email = _find_cf_email(soup)
    if email:
        business["Business Email"] = email

    # ---- Description & Keywords ----
    about = soup.select_one("div.listing-title-bar")
    if about:
        paragraphs = about.find_all("p", recursive=False)
        for i, p in enumerate(paragraphs):
            text = clean(p.get_text())
            if not is_meaningful(text):
                continue
            tags_match = re.match(r"^Tags\s*:\s*(.*)$", text, flags=re.I)
            if tags_match:
                tags_text = tags_match.group(1).strip()
                if is_meaningful(tags_text):
                    raw_tags = [
                        tag.lstrip("#").strip()
                        for tag in tags_text.split()
                        if tag.lstrip("#").strip()
                    ]
                    business["Keywords"] = ", ".join(
                        _vymaps_split_camel_tag(tag) for tag in raw_tags
                    )
                continue
            if i == 0:
                continue
            if not business["Description"]:
                business["Description"] = text

    if not business["Description"] and jsonld.get("description"):
        desc_text = clean(jsonld["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Category (single hero badge, not a breadcrumb trail) ----
    cat_tag = soup.select_one("span.category-tag")
    if cat_tag:
        cat_text = clean(cat_tag.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Photos ----
    photos = []
    for img in soup.select("ul.gallery-list img[src]"):
        if not img.get("src"):
            continue
        src = urljoin(url, img["src"])
        if src not in photos:
            photos.append(src)
    if photos:
        business["Photos"] = photos

    return business
