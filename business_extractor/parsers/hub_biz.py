"""
Site parser: hub.biz  (Hubbiz business pages, e.g. https://flowchefs.hub.biz/)

Listings are served from <slug>.hub.biz. The site sits behind a verification
step that headless fetches don't pass, so hub.biz is in HEADED_DOMAINS
(playwright_worker.py) and dispatched as "playwright".

The page is server-rendered schema.org microdata, so everything is plain HTML:

  Name         h1.company[itemprop=name]            (fallback: og:title before the 1st comma)
  Street       p.street-address[itemprop=streetAddress]
  City         [itemprop=addressLocality]
  State        [itemprop=addressRegion]
  Zipcode      [itemprop=postalCode]
  Country      not printed on the page; "US" is filled in when the address is
               US-shaped (2-letter state + 5-digit ZIP), like the other US-only parsers
  Phone        [itemprop=telephone]
  Website URL  a.site-business (the "Website:" list); Hubbiz's own links are ignored
  Description  p.about-us[itemprop=description]     (the <meta> description is truncated with "...")
  Hours        [itemprop=openingHours] or a "Hours:" label block -- only present on
               some listings; blank otherwise
  Business Email  mailto: link / [itemprop=email] -- most listings only have a
               "Send" form button, so this is usually blank
  Category     the "Category:" row (fallback: the h2.cat-in line / last breadcrumb)
  Logo         .company-logo img (hubbiz S3 "business/logo/<id>/..." only, so a
               site placeholder is never reported as the company's logo)
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


_OWN_HOSTS = ("hub.biz", "hubbiz.com", "hubbiz.net")
_STATE_ZIP_US = re.compile(r"^[A-Z]{2}$"), re.compile(r"^\d{5}(?:-\d{4})?$")


def _itemprop(soup, prop, scope=None):
    el = (scope or soup).find(attrs={"itemprop": prop})
    if not el:
        return ""
    if el.name == "meta":
        return clean(el.get("content", ""))
    return clean(el.get_text(" "))


def _is_own(href):
    host = (urlparse(href).netloc or "").lower()
    return any(host == h or host.endswith("." + h) for h in _OWN_HOSTS)


def _label_value(soup, label_re):
    """Text of a 'Label: value' paragraph row (<p><span class="c">Label:</span> value</p>)."""
    for span in soup.select("p span.c"):
        if re.match(label_re, clean(span.get_text(" ")), re.I):
            full = clean(span.parent.get_text(" "))
            return clean(re.sub(label_re, "", full, count=1, flags=re.I))
    return ""


def _hours(soup):
    items = []
    for el in soup.find_all(attrs={"itemprop": "openingHours"}):
        t = clean(el.get("content") or el.get_text(" "))
        if t and t not in items:
            items.append(t)
    if items:
        return "\n".join(items)
    pat = re.compile(r"^(?:business |opening )?hours\b\s*:?", re.I)
    for tag in soup.select(".sub_detail, h2.sub_detail"):
        if pat.match(clean(tag.get_text(" "))):
            nxt = tag.find_next_sibling(["ul", "p", "div", "table"])
            if nxt:
                lines = [clean(li.get_text(" ")) for li in nxt.find_all("li")]
                lines = [l for l in lines if l] or [clean(nxt.get_text(" "))]
                return "\n".join(l for l in lines if l)
    return _label_value(soup, r"^(?:business |opening )?hours\s*:?\s*")


def parse_hubbiz(url, html):

    business = empty_business()

    if not html or _looks_blocked(html):
        return business

    soup = BeautifulSoup(html, "lxml")
    h1 = soup.select_one("h1.company") or soup.find("h1", attrs={"itemprop": "name"})
    addr = soup.select_one("[itemprop=address]") or soup
    street_el = addr.select_one("[itemprop=streetAddress]")
    if not h1 and not street_el:
        return business  # verification / error page: no listing here

    # ---- Name ----
    name = clean(h1.get_text(" ")) if h1 else ""
    if not name:
        og = soup.find("meta", property="og:title")
        name = clean((og.get("content") or "").split(",")[0]) if og else ""
    business["Business Name"] = name

    # ---- Address ----
    business["Street"] = clean(street_el.get_text(" ")) if street_el else ""
    business["City"] = _itemprop(soup, "addressLocality", addr)
    business["State"] = _itemprop(soup, "addressRegion", addr)
    business["Zipcode"] = _itemprop(soup, "postalCode", addr)
    country = _itemprop(soup, "addressCountry", addr)
    if not country and _STATE_ZIP_US[0].match(business["State"]) \
            and _STATE_ZIP_US[1].match(business["Zipcode"]):
        country = "US"
    business["Country"] = country

    # ---- Phone ----
    business["Phone"] = _itemprop(soup, "telephone")
    if not business["Phone"]:
        tel = soup.select_one("a[href^='tel:']")
        business["Phone"] = clean(tel.get_text(" ")) if tel else ""

    # ---- Website URL ----
    link = soup.select_one("a.site-business")
    website = (link.get("href") or "").strip() if link else ""
    if not website:
        for a in soup.select("ul.biz_detail_option a[href^='http']"):
            if not _is_own(a["href"]):
                website = a["href"].strip()
                break
    business["Website URL"] = website

    # ---- Description ----
    desc_el = soup.select_one("p.about-us") or soup.find("p", attrs={"itemprop": "description"})
    desc = clean(desc_el.get_text(" ")) if desc_el else ""
    if not desc:
        og = soup.find("meta", property="og:description")
        desc = clean(og.get("content", "")) if og else ""
    business["Description"] = desc

    # ---- Hours ----
    business["Hours"] = _hours(soup)

    # ---- Business Email ----
    email = ""
    for a in soup.select("a[href^='mailto:']"):
        email = a["href"][7:].split("?")[0].strip()
        if email:
            break
    business["Business Email"] = email or _itemprop(soup, "email")

    # ---- Category ----
    cat = _label_value(soup, r"^category\s*:?\s*")
    if not cat:
        h2 = soup.select_one("h2.cat-in")
        if h2:
            cat = re.sub(r"\s+in\s+[^,]+,\s*[A-Z]{2}\s*\d{0,5}\s*$", "",
                         clean(h2.get_text(" ")))
    if not cat:
        crumbs = soup.select(".breadcrumb_items [itemprop=name]")
        cat = clean(crumbs[-1].get_text(" ")) if crumbs else ""
    business["Category"] = cat

    # ---- Logo ----
    logo = ""
    img = soup.select_one(".company-logo img")
    cands = [img.get("src") if img else ""]
    og = soup.find("meta", property="og:image")
    cands.append(og.get("content") if og else "")
    for src in cands:
        if src and "/business/logo/" in src:
            logo = urljoin(url, src.strip())
            break
    business["Logo"] = logo

    return business