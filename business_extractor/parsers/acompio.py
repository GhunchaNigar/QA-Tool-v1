"""
Site parser: acompio.us  (Acompio Network, United States)

Path in project:  business_extractor/parsers/acompio.py

Data sources, most reliable first:
  1. <script type="application/ld+json"> LocalBusiness  (name, phone, email,
     keywords, description, full address)
  2. the visible page: <h1>, #phone, #place, "Visit Website" button,
     "Categories" / "Keywords" tag lists, "About ..." paragraph
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py
from ..common import _is_maps_link


def _listing_ld(soup):
    """The LocalBusiness JSON-LD object (the page also carries FAQPage and a
    site-wide Organization/WebSite graph that must be ignored)."""
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw, strict=False)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop(0)
            if not isinstance(obj, dict):
                continue
            t = obj.get("@type")
            types = [str(x).lower() for x in (t if isinstance(t, list) else [t])]
            if any(x == "localbusiness" or x.endswith("business") for x in types):
                return obj
            if isinstance(obj.get("@graph"), list):
                stack.extend(obj["@graph"])
    return {}


def _s(value):
    return clean(str(value)) if isinstance(value, (str, int, float)) and str(value).strip() else ""


def parse_acompio(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()
    ld = _listing_ld(soup)
    main = soup.select_one("main") or soup

    # ---- JSON-LD ----
    business["Business Name"] = _s(ld.get("name"))
    business["Phone"] = _s(ld.get("telephone"))
    business["Business Email"] = _s(ld.get("email"))
    business["Keywords"] = _s(ld.get("keywords"))
    business["Description"] = clean_multiline(_s(ld.get("description")))

    addr = ld.get("address") if isinstance(ld.get("address"), dict) else {}
    business["Street"] = _s(addr.get("streetAddress"))
    if _s(addr.get("extendedAddress")):
        business["Street"] = clean(f"{business['Street']}, {_s(addr.get('extendedAddress'))}")
    business["City"] = _s(addr.get("addressLocality"))
    business["State"] = _s(addr.get("addressRegion"))
    business["Zipcode"] = _s(addr.get("postalCode"))
    business["Country"] = _s(addr.get("addressCountry"))

    # ---- Visible-page fallbacks ----
    if not business["Business Name"]:
        h1 = main.select_one("h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    if not business["Phone"]:
        p = main.select_one("#phone p.fs-md")
        if p and clean(p.get_text()).lower() != "n/a":
            business["Phone"] = clean(p.get_text())
    if not business["Phone"]:
        tel = main.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    if not business["Business Email"]:
        mail = main.select_one('a[href^="mailto:"]')
        if mail:
            business["Business Email"] = mail["href"].replace("mailto:", "").split("?")[0].strip()

    place = main.select_one("#place")
    if place:
        if not business["Street"]:
            street = place.select_one(".location-list p")
            if street:
                business["Street"] = clean(street.get_text())
        spans = [clean(s.get_text()) for s in place.select(".dot-labels span")]
        if len(spans) >= 3:
            business["Zipcode"] = business["Zipcode"] or spans[0]
            business["City"] = business["City"] or spans[1]
            business["State"] = business["State"] or spans[2]
    if not business["Country"]:
        m = re.search(r'let country = "([^"]+)"', html)
        if m:
            business["Country"] = clean(m.group(1))

    # ---- Website URL: the "Visit Website" button ----
    for a in main.select('#buttons a[href^="http"], a.button[href^="http"]'):
        if "visit website" in clean(a.get_text()).lower() or "website" in clean(a.get("alt", "")).lower():
            business["Website URL"] = a["href"].strip()
            break
    if not business["Website URL"]:
        for a in main.select("a.button[href^='http']"):
            host = urlparse(a["href"]).netloc.lower()
            if "acompio" not in host and "visit" in clean(a.get_text()).lower():
                business["Website URL"] = a["href"].strip()
                break

    # ---- Description fallback ----
    if not business["Description"]:
        p = main.select_one(".company-description p")
        if p:
            business["Description"] = clean_multiline(p.get_text("\n"))

    # ---- Category: tags under the "Categories" heading ----
    cats = []
    for a in main.select('a[href*="/Category/"]'):
        name = clean(a.get_text())
        if name and name not in cats:
            cats.append(name)
    if cats:
        business["Category"] = ", ".join(cats)

    # ---- Keywords fallback: tags under the "Keywords" heading ----
    if not business["Keywords"]:
        kws = []
        for a in main.select('a[href*="search.php?name="]'):
            name = clean(a.get_text())
            if name and name not in kws:
                kws.append(name)
        if kws:
            business["Keywords"] = ", ".join(kws)

    # ---- Social links + GBP link (JSON-LD sameAs, then links inside the listing) ----
    same_as = ld.get("sameAs") or []
    hrefs = [same_as] if isinstance(same_as, str) else [h for h in same_as if isinstance(h, str)]
    hrefs += [a["href"] for a in main.find_all("a", href=True)]
    for href in hrefs:
        if not href.lower().startswith("http"):
            continue
        host = urlparse(href).netloc.lower()
        if "acompio" in host:
            continue
        if not business["GBP Link"] and _is_maps_link(href):
            business["GBP Link"] = href
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"].setdefault(network, href)

    # ---- Logo ----
    logo = _s(ld.get("logo")) or _s(ld.get("image"))
    if not logo:
        img = main.select_one("#logo img[src]")
        if img:
            logo = img["src"]
    if not logo:
        og = soup.find("meta", property="og:image")
        logo = og["content"] if og and og.get("content") else ""
    if logo:
        business["Logo"] = urljoin(url, logo)

    return business