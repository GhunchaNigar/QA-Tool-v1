
import html as _html

from ..common import *  # noqa: F401,F403
from .nextbizthing import _nbt_split_location

_ADDR_ABOUT_RE = re.compile(
    r"^\s*Address\s*:\s*(?P<addr>.*?)\s*(?:About\s*Us\s*:\s*(?P<about>.*))?$",
    re.I | re.S,
)


def _ld_place(soup):
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "", strict=False)
        except (ValueError, TypeError):
            continue
        items = data.get("@graph") if isinstance(data, dict) and isinstance(data.get("@graph"), list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") in ("Place", "LocalBusiness", "Organization"):
                return item
    return {}


def _text(value):
    return clean(_html.unescape(value or "").replace("\xa0", " "))


def parse_addonbiz(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "html.parser")
    ld = _ld_place(soup)

    # ---- Name ----
    h1 = soup.select_one("h1.job_listing-title") or soup.find("h1")
    business["Business Name"] = clean(h1.get_text()) if h1 else _text(ld.get("name"))

    # ---- Address + Description ----
    # Description text may be HTML-escaped ("&nbsp;") inside the JSON string.
    raw_desc = _html.unescape(ld.get("description") or "").replace("\xa0", " ")
    address_text, about_text = "", ""
    m = _ADDR_ABOUT_RE.match(raw_desc)
    if m and re.match(r"^\s*Address\s*:", raw_desc, re.I):
        address_text = clean(m.group("addr"))
        about_text = clean(m.group("about") or "")
    else:
        about_text = clean(raw_desc)

    location_el = soup.select_one(".job_listing-location")
    location_text = clean(location_el.get_text(" ")) if location_el else ""
    # On this theme the visible location is just the country for expired
    # listings, but a full address on active ones.
    if location_text and re.search(r"\d", location_text):
        address_text = address_text or location_text
        location_text = ""

    street, city, state, zipcode = _nbt_split_location(address_text)
    business["Street"] = street
    business["City"] = city
    business["State"] = state
    business["Zipcode"] = zipcode
    business["Country"] = location_text if location_text and "," not in location_text else ""

    business["Description"] = about_text

    # ---- Phone (active listings only) ----
    tel = soup.select_one('a[href^="tel:"]')
    phone_el = soup.select_one(".job_listing-phone")
    if tel:
        business["Phone"] = clean(tel.get_text()) or tel["href"].replace("tel:", "").strip()
    elif phone_el and is_meaningful(phone_el.get_text()):
        business["Phone"] = clean(phone_el.get_text())
    elif ld.get("telephone"):
        business["Phone"] = _text(ld.get("telephone"))

    # ---- Website URL ----
    website = ""
    site_el = soup.select_one("a.listing-contact.website[href], .job_listing-website a[href], a.website[href]")
    if site_el:
        website = site_el["href"].strip()
    if not website:
        mep = ld.get("mainEntityOfPage")
        cand = mep.get("@id") if isinstance(mep, dict) else mep
        if isinstance(cand, str) and cand.startswith(("http://", "https://")) \
                and "addonbiz.com" not in urlparse(cand).netloc.lower():
            website = cand.strip()
    business["Website URL"] = website

    # ---- Hours (active listings only) ----
    hours_box = soup.select_one(".job_listing-business-hours, .business-hours, .listing-business-hours")
    if hours_box:
        rows = [clean(r.get_text(" ")) for r in hours_box.select("li, tr")]
        rows = [r for r in rows if is_meaningful(r)]
        business["Hours"] = "; ".join(rows) if rows else clean(hours_box.get_text(" "))

    # ---- Category ----
    cats = [clean(a.get_text()) for a in soup.select(".content-single-job_listing-title-category a[rel='tag']")]
    cats = [c for c in cats if c]
    business["Category"] = ", ".join(dict.fromkeys(cats))

    # ---- Logo ----
    logo = soup.select_one("img.listing-entry-company-image__img[src]")
    logo_url = logo["src"].strip() if logo else (ld.get("logo") if isinstance(ld.get("logo"), str) else "")
    business["Logo"] = urljoin(url, logo_url) if logo_url else ""

    return filter_business_fields(business, url)