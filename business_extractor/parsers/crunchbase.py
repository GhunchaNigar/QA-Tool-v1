"""
Site parser: crunchbase.com  (https://www.crunchbase.com/organization/<slug>)

Crunchbase sits behind a Cloudflare challenge ("verify you are human"), so
crunchbase.com is in HEADED_DOMAINS (playwright_worker.py) and dispatched as
"playwright". The page is an Angular SSR app; the same data appears in three
places, tried in this order:

  1. <script id="ng-state"> JSON -> HttpState -> ".../entities/organizations/<slug>"
     (most complete: contact_fields, company_about_fields2, overview_description,
     social_fields, properties.identifier ...)
  2. the WebPage -> mainEntity (Corporation) JSON-LD block
  3. the rendered markup (.entity-name, tile-field rows, .social-link-icons)

Where each field comes from:
  Name         properties.identifier.value
  Street/City/State/Zipcode
               the "Address: ..." line at the top of overview_description
               (e.g. "5100 rue Bernard-Lefebvre, Laval, QC H7C 0B7"); only
               present when the company wrote one. Otherwise City/State come
               from the JSON-LD address and Street/Zipcode stay blank.
  Country      JSON-LD addressCountry / the "country" location identifier
  Phone        cards.contact_fields.phone_number
  Website URL  cards.company_about_fields2.website.value
  Keywords     short_description when it is a comma-separated keyword list
               (otherwise the category names, if the profile has any)
  Description  overview_description minus the "Address:" block and the
               "About us" heading (fallback: short_description)
  Social       cards.social_fields (linkedin/facebook/twitter/...); Crunchbase's
               own footer accounts are ignored
  Category     properties.category_groups, else the category chips; blank when
               the profile lists none
  Logo         JSON-LD logo / image_id URL on images.crunchbase.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


_SOCIAL_LABELS = {
    "linkedin": "LinkedIn", "facebook": "Facebook", "twitter": "Twitter",
    "instagram": "Instagram", "youtube": "YouTube", "x": "X",
}
_SOCIAL_DOMAINS = {
    "LinkedIn": "linkedin.com", "Facebook": "facebook.com", "Twitter": "twitter.com",
    "X": "x.com", "Instagram": "instagram.com", "YouTube": "youtube.com",
}
_COUNTRIES = {"canada", "usa", "us", "united states", "united states of america"}
_ZIP_RE = re.compile(r"^(?:\d{5}(?:-\d{4})?|[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d)$")


def _ng_state(soup):
    tag = soup.find("script", id="ng-state")
    if not tag:
        return {}
    try:
        return json.loads(tag.string or tag.get_text() or "")
    except Exception:
        return {}


def _org_data(state):
    """The organization entity payload inside HttpState (skips the meters call etc.)."""
    http = state.get("HttpState") if isinstance(state, dict) else None
    for key, val in (http or {}).items():
        if "/entities/organizations/" not in key or not isinstance(val, dict):
            continue
        data = val.get("data")
        if isinstance(data, dict) and (data.get("cards") or data.get("properties")):
            return data
    return {}


def _org_ld(soup):
    """mainEntity of the WebPage JSON-LD (the company), not the site-wide Crunchbase org."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except Exception:
            continue
        if not (isinstance(data, dict) and data.get("@type") == "WebPage"):
            continue
        ent = data.get("mainEntity")
        if isinstance(ent, dict) and "/organization/" in (ent.get("url") or ""):
            return ent
    return {}


def _tile_value(soup, label):
    """Value of a 'Details' tile-field row whose label reads `label`."""
    want = label.lower()
    for tf in soup.find_all("tile-field"):
        lab = tf.find("label-with-info")
        if lab and clean(lab.get_text(" ")).lower().startswith(want):
            ff = tf.find("field-formatter")
            if ff:
                return clean(ff.get_text(" "))
    return ""


def _split_description(desc):
    """-> (address_line, about_text) from Crunchbase's 'Address: ...\\n\\nAbout us\\n\\n...' blob."""
    lines = [clean(l) for l in (desc or "").replace("\r", "").split("\n") if clean(l)]
    address = ""
    if lines and lines[0].lower().startswith("address"):
        first = re.sub(r"^address\s*:?\s*", "", lines[0], flags=re.I)
        if first:
            address, lines = first, lines[1:]
        elif len(lines) > 1:
            address, lines = lines[1], lines[2:]
        else:
            lines = []
    if lines and lines[0].lower() in ("about us", "about"):
        lines = lines[1:]
    return address, " ".join(lines)


def _parse_address(line):
    """'5100 rue Bernard-Lefebvre, Laval, QC H7C 0B7' -> street, city, state, zip (or None)."""
    parts = [p.strip() for p in (line or "").split(",") if p.strip()]
    if parts and parts[-1].lower() in _COUNTRIES:
        parts = parts[:-1]
    if len(parts) < 3:
        return None
    m = re.match(r"^([A-Za-z]{2})\s+(\S.*)$", parts[-1])
    if not m or not _ZIP_RE.match(m.group(2).strip()):
        return None
    return {"Street": ", ".join(parts[:-2]), "City": parts[-2],
            "State": m.group(1).upper(), "Zipcode": m.group(2).strip().upper()}


def _looks_like_keywords(text):
    t = (text or "").strip()
    return t.count(",") >= 2 and not re.search(r"[.!?]\s*$", t)


def parse_crunchbase(url, html):

    business = empty_business()

    if not html or _looks_blocked(html):
        return business

    soup = BeautifulSoup(html, "lxml")
    data = _org_data(_ng_state(soup))
    cards = data.get("cards") or {}
    props = data.get("properties") or {}
    ld = _org_ld(soup)

    if not data and not ld:
        return business  # challenge/stub page: nothing about the company to trust

    # ---- Name ----
    name = clean((props.get("identifier") or {}).get("value", "")) or clean(props.get("title", ""))
    if not name:
        name = clean(ld.get("name", ""))
    if not name:
        el = soup.select_one(".entity-name") or soup.find("h1")
        name = clean(el.get_text(" ")) if el else ""
    business["Business Name"] = name

    # ---- Description / address ----
    desc_raw = (cards.get("overview_description") or {}).get("description", "")
    if not desc_raw:
        span = soup.select_one("tile-description .description")
        desc_raw = "\n".join(p.get_text(" ") for p in soup.select("tile-description p")) if span else ""
    address_line, about = _split_description(desc_raw)

    short = clean((cards.get("about_short_description") or {}).get("short_description", "")
                  or props.get("short_description", "") or ld.get("description", ""))
    business["Description"] = about or short

    # ---- Address ----
    addr = _parse_address(address_line)
    ld_addr = ld.get("address") or {}
    if addr:
        business.update(addr)
    else:
        business["City"] = clean(ld_addr.get("addressLocality", ""))
        business["State"] = clean(ld_addr.get("addressRegion", ""))

    country = clean(ld_addr.get("addressCountry", ""))
    if not country:
        for loc in (cards.get("company_about_fields2") or {}).get("location_identifiers") or []:
            if isinstance(loc, dict) and loc.get("location_type") == "country":
                country = clean(loc.get("value", ""))
                break
    if not country and addr and re.match(r"^[A-Z]\d[A-Z]", addr["Zipcode"]):
        country = "Canada"
    business["Country"] = country

    # ---- Phone ----
    phone = clean((cards.get("contact_fields") or {}).get("phone_number", ""))
    business["Phone"] = phone or _tile_value(soup, "Phone")

    # ---- Website URL ----
    website = clean(((cards.get("company_about_fields2") or {}).get("website") or {}).get("value", ""))
    if not website:
        a = soup.select_one("field-formatter.hide-external-link-icon a[href^='http']")
        website = (a.get("href") or "").strip() if a else ""
    if not website:
        for u in ld.get("sameAs") or []:
            if isinstance(u, str) and u.startswith("http") and "crunchbase.com" not in u \
                    and not any(d in u.lower() for d in _SOCIAL_DOMAINS.values()):
                website = u
                break
    business["Website URL"] = website

    # ---- Category ----
    cats = []
    for g in props.get("category_groups") or []:
        n = clean(g.get("value", "")) if isinstance(g, dict) else clean(str(g))
        if n and n not in cats:
            cats.append(n)
    if not cats:
        for chip in soup.select(".categories chip, chips-container chip"):
            n = clean(chip.get_text(" "))
            if n and n not in cats:
                cats.append(n)
    business["Category"] = ", ".join(cats)

    # ---- Keywords ----
    business["Keywords"] = short if _looks_like_keywords(short) else ", ".join(cats)

    # ---- Social media (the company's own, never Crunchbase's footer links) ----
    social = {}
    for key, val in (cards.get("social_fields") or {}).items():
        link = clean(val.get("value", "") if isinstance(val, dict) else str(val or ""))
        if link.startswith("http"):
            social[_SOCIAL_LABELS.get(key.lower(), key.capitalize())] = link
    if not social:
        cands = [a["href"] for a in soup.select(".social-link-icons a[href]")]
        cands += [u for u in (ld.get("sameAs") or []) if isinstance(u, str)]
        for href in cands:
            if "crunchbase" in href.lower():
                continue
            for label, dom in _SOCIAL_DOMAINS.items():
                if _hostname_matches_social_domain(href, dom) and label not in social:
                    social[label] = href
                    break
    business["Social Media Links"] = social

    # ---- Logo ----
    logo = ld.get("logo") or ld.get("image") or ""
    if isinstance(logo, dict):
        logo = logo.get("url", "")
    if not logo:
        image_id = (props.get("identifier") or {}).get("image_id", "")
        if image_id:
            logo = ("https://images.crunchbase.com/image/upload/"
                    f"c_pad,f_auto,q_auto:eco,dpr_1/q_30/{image_id}?ik-sanitizeSvg=true")
    if not logo:
        og = soup.find("meta", property="og:image")
        logo = (og.get("content") or "") if og else ""
    business["Logo"] = logo

    return business