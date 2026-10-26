"""
Site parser: manta.com

Manta sits behind a Cloudflare challenge that blocks requests, curl_cffi and
headless Chrome ("Just a moment..."). A visible Chrome window passes, so
manta.com is in HEADED_DOMAINS (playwright_worker.py) and is dispatched as
"playwright". Once the real page is in hand, everything lives in the
LocalBusiness JSON-LD block, plus a few spots in the visible markup.
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


_SOCIAL_DOMAINS = {
    "Facebook": "facebook.com",
    "Twitter": "twitter.com",
    "X": "x.com",
    "Instagram": "instagram.com",
    "LinkedIn": "linkedin.com",
    "YouTube": "youtube.com",
    "Pinterest": "pinterest.com",
    "TikTok": "tiktok.com",
    "Yelp": "yelp.com",
}

# Manta's own site-wide footer accounts -- present on every page, not the business's.
_MANTA_OWN_SOCIAL = re.compile(
    r"(twitter\.com/Manta|facebook\.com/mantacom|linkedin\.com/company/manta)(?:[/?#]|$)",
    re.I,
)

_GBP_RE = re.compile(
    r"^https?://(?:(?:www\.)?google\.[a-z.]+/maps/place/|maps\.app\.goo\.gl/|g\.page/|"
    r"(?:www\.)?google\.[a-z.]+/maps/cid/|goo\.gl/maps/)",
    re.I,
)


def _local_business(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except Exception:
            continue
        items = data.get("@graph", [data]) if isinstance(data, dict) else data
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type")
            if t == "LocalBusiness" or (isinstance(t, list) and "LocalBusiness" in t):
                return item
    return {}


def _abs(url, u):
    u = (u or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    return urljoin(url, u)


def _hours_from_ld(ld):
    spec = ld.get("openingHoursSpecification")
    if isinstance(spec, dict):
        spec = [spec]
    lines = []
    for s in spec or []:
        if not isinstance(s, dict):
            continue
        days = s.get("dayOfWeek")
        days = [days] if isinstance(days, str) else (days or [])
        days = [str(d).rsplit("/", 1)[-1] for d in days]
        opens, closes = s.get("opens", ""), s.get("closes", "")
        if days and opens and closes:
            lines.append(f"{', '.join(days)}: {opens} - {closes}")
    if lines:
        return "\n".join(lines)
    oh = ld.get("openingHours")
    if isinstance(oh, str):
        return clean(oh)
    if isinstance(oh, list):
        return "\n".join(clean(x) for x in oh if isinstance(x, str))
    return ""


def parse_manta(url, html):

    business = empty_business()

    if not html or _looks_blocked(html):
        return business

    soup = BeautifulSoup(html, "lxml")
    ld = _local_business(soup)
    if not ld:
        return business  # challenge/stub page: no listing data to trust

    addr = ld.get("address") or {}

    # ---- Business Name ----
    name = clean(ld.get("name", ""))
    if not name:
        h1 = soup.find("h1")
        name = clean(h1.get_text(" ")) if h1 else ""
    business["Business Name"] = name

    # ---- Address ----
    business["Street"] = clean(addr.get("streetAddress", ""))
    business["City"] = clean(addr.get("addressLocality", ""))
    business["State"] = clean(addr.get("addressRegion", ""))
    business["Zipcode"] = clean(addr.get("postalCode", ""))
    business["Country"] = clean(addr.get("addressCountry", ""))

    # ---- Phone ----
    phone = clean(ld.get("telephone", ""))
    if not phone:
        tel = soup.select_one("a[href^='tel:']")
        if tel:
            phone = clean(tel.get_text(" ")) or tel["href"][4:]
    if not phone:
        m = re.search(r"Call\s*(\(\d{3}\)\s*\d{3}-\d{4})", soup.get_text(" "))
        phone = m.group(1) if m else ""
    business["Phone"] = phone

    # ---- Website URL ----
    same_as = ld.get("sameAs")
    same_as = [same_as] if isinstance(same_as, str) else (same_as or [])
    website = ""
    for u in same_as:
        if isinstance(u, str) and u.startswith("http") and "manta.com" not in u:
            if not any(d in u.lower() for d in _SOCIAL_DOMAINS.values()):
                website = u
                break
    business["Website URL"] = website

    # ---- Keywords (products & services offered) ----
    offers = ld.get("makesOffer") or []
    kws = []
    for o in offers if isinstance(offers, list) else [offers]:
        item = (o or {}).get("itemOffered") or {}
        n = clean(item.get("name", "")) if isinstance(item, dict) else ""
        if n and n.lower() not in (k.lower() for k in kws):
            kws.append(n)
    business["Keywords"] = ", ".join(kws)

    # ---- Description ----
    business["Description"] = clean(ld.get("description", ""))

    # ---- Hours ----
    business["Hours"] = _hours_from_ld(ld)

    # ---- Social media / GBP / email: only links that belong to the business ----
    social = {}
    gbp = ""
    emails = []
    candidates = list(same_as)
    candidates += [a["href"] for a in soup.find_all("a", href=True)]
    for href in candidates:
        if not isinstance(href, str):
            continue
        href = href.strip()
        if href.lower().startswith("mailto:"):
            e = href[7:].split("?")[0].strip()
            if e and e not in emails:
                emails.append(e)
            continue
        if not href.startswith("http"):
            continue
        if _MANTA_OWN_SOCIAL.search(href):
            continue
        if not gbp and _GBP_RE.match(href):
            gbp = href
            continue
        for label, dom in _SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, dom) and label not in social:
                social[label] = href
                break
    business["Social Media Links"] = social
    business["GBP Link"] = gbp
    e = clean(ld.get("email", ""))
    if e and e not in emails:
        emails.insert(0, e)
    business["Business Email"] = emails[0] if emails else ""

    # ---- Category ----
    cat = ""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "BreadcrumbList":
            items = data.get("itemListElement") or []
            if items:
                last = items[-1].get("item") or {}
                cat = clean(last.get("name", ""))
    if not cat:
        for row in soup.find_all(string=re.compile(r"^\s*Type\s*$")):
            nxt = row.find_next(string=lambda s: s and s.strip())
            if nxt:
                cat = clean(nxt)
                break
    business["Category"] = cat

    # ---- Logo ----
    logo = ld.get("logo") or ld.get("image") or ""
    if isinstance(logo, dict):
        logo = logo.get("url", "")
    if not logo:
        img = soup.find("img", alt=re.compile(r"logo", re.I))
        if img and "manta_logo" not in (img.get("src") or ""):
            logo = img.get("src") or ""
    business["Logo"] = _abs(url, logo)

    # ---- Photos (gallery images other than the logo / Manta chrome) ----
    photos = []
    for img in soup.find_all("img"):
        src = _abs(url, img.get("src") or img.get("data-src") or "")
        if not src or "images.manta-r3.com" not in src:
            continue
        if src == business["Logo"] or src in photos:
            continue
        if re.search(r"logo", img.get("alt") or "", re.I):
            continue
        photos.append(src)
    business["Photos"] = photos

    return business