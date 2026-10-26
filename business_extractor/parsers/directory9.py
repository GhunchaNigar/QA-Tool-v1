"""
Site parser: directory9.net/listing/<slug>.html  (eDirectory platform)

The site sits behind a verification step, so directory9.net is in
HEADED_DOMAINS (playwright_worker.py) and dispatched as "playwright".

Data sources on the page, in the order they are tried:

  Name         JSON-LD LocalBusiness name -> the page <h1> -> og:title without the
               " | directory9" suffix
  Street / City / State / Zipcode
               JSON-LD address -> <meta business:contact_data:street_address,
               locality, region, postal_code>. On directory9 the street_address
               meta holds the WHOLE address ("9657 US-98 W, Miramar Beach, FL 32550"),
               so it is split into its four parts when locality/region are absent.
  Country      JSON-LD / meta country_name; otherwise "US" when the address is
               US-shaped (2-letter state + 5-digit ZIP), like the other US parsers.
               The footer "New Zealand" belongs to directory9 itself and is ignored.
  Phone        <meta business:contact_data:phone_number> -> JSON-LD telephone -> the
               tel: link outside the footer (the footer phone is directory9's own).
  Website URL  <meta business:contact_data:website> -> the "Visit Website" link.
               directory9's own links are ignored.
  Description  JSON-LD description -> #long-description .description (the listing text)
               -> p.summary-description -> the text under the "About" heading
               (stops at the next heading, e.g. "Location").
  Category     the "in <Category>" link(s): a[title^='Explore Category'] or
               .details-categories a
  Logo         twitter:image / og:image (the listing's own picture). directory9's
               site logo (content_files/img_logo.png) is never reported.
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


_OWN_HOSTS = ("directory9.net",)
_ADDR_RE = re.compile(
    r"^(?P<street>.+?),\s*(?P<city>[^,]+?),\s*(?P<state>[A-Za-z]{2,}\.?)\s+"
    r"(?P<zip>\d{5}(?:-\d{4})?)(?:,\s*(?P<country>.+))?$"
)
_US_STATE = re.compile(r"^[A-Z]{2}$")
_US_ZIP = re.compile(r"^\d{5}(?:-\d{4})?$")
_HEADING = ("h1", "h2", "h3", "h4", "h5", "h6")


def _is_own(href):
    host = (urlparse(href).netloc or "").lower().split(":")[0]
    return any(host == h or host.endswith("." + h) for h in _OWN_HOSTS)


def _ld_business(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "{}")
        except (ValueError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and (
                "LocalBusiness" in str(item.get("@type", ""))
                or "Organization" in str(item.get("@type", ""))
            ):
                return item
    return {}


def _meta(soup, key):
    """Content of <meta property|name=key>."""
    tag = (soup.find("meta", attrs={"property": key})
           or soup.find("meta", attrs={"name": key}))
    return clean(tag.get("content", "")) if tag else ""


def _in_footer(el):
    return el.find_parent("footer") is not None


def _about_text(soup):
    """Text under the 'About' heading, up to the next heading."""
    for h in soup.find_all(_HEADING):
        if clean(h.get_text(" ")).lower() != "about":
            continue
        node = h
        for _ in range(3):  # the text may sit beside the heading or one wrapper up
            parts = []
            for sib in node.find_next_siblings():
                if sib.name in _HEADING or sib.find(_HEADING):
                    break
                text = clean(sib.get_text(" "))
                if text:
                    parts.append(text)
            if parts:
                return " ".join(parts)
            node = node.parent
            if node is None or node.name in ("body", "html"):
                break
    return ""


def parse_directory9(url, html):

    business = empty_business()

    if not html:
        return business

    soup = BeautifulSoup(html, "lxml")

    ld = _ld_business(soup)
    addr = ld.get("address") or {}
    if not isinstance(addr, dict):
        addr = {}

    h1 = soup.find("h1")
    has_listing = bool(
        ld
        or _meta(soup, "business:contact_data:street_address")
        or _meta(soup, "business:contact_data:phone_number")
        or soup.select_one("a[title^='Explore Category']")
    )
    # Every page carries a sign-in modal with a captcha image, so the word
    # "captcha" alone must not count as a bot-wall: only treat the page as
    # blocked when it ALSO has no listing data.
    if not has_listing and (_looks_blocked(html) or not h1):
        return business  # verification / error page: no listing here

    # ---- Name ----
    name = clean(ld.get("name", "")) or (clean(h1.get_text(" ")) if h1 else "")
    if not name:
        name = re.sub(r"\s*\|\s*directory9\s*$", "", _meta(soup, "og:title"), flags=re.I)
    business["Business Name"] = name

    # ---- Address ----
    street = clean(addr.get("streetAddress", "")) or _meta(soup, "business:contact_data:street_address")
    city = clean(addr.get("addressLocality", "")) or _meta(soup, "business:contact_data:locality")
    state = clean(addr.get("addressRegion", "")) or _meta(soup, "business:contact_data:region")
    zipcode = clean(addr.get("postalCode", "")) or _meta(soup, "business:contact_data:postal_code")
    country = clean(addr.get("addressCountry", "")) or _meta(soup, "business:contact_data:country_name")

    if street and not (city and state and zipcode):
        m = _ADDR_RE.match(street)
        if m:
            street = clean(m.group("street"))
            city = city or clean(m.group("city"))
            state = state or clean(m.group("state"))
            zipcode = zipcode or m.group("zip")
            country = country or clean(m.group("country") or "")
        else:
            s, c, st, z = _split_blinx_address(street)
            if c or st or z:
                street, city, state, zipcode = s or "", city or c, state or st, zipcode or z
    if not country and _US_STATE.match(state) and _US_ZIP.match(zipcode):
        country = "US"

    business["Street"] = street
    business["City"] = city
    business["State"] = state
    business["Zipcode"] = zipcode
    business["Country"] = country

    # ---- Phone ----
    phone = _meta(soup, "business:contact_data:phone_number") or clean(ld.get("telephone", ""))
    if not phone:
        for a in soup.select("a[href^='tel:']"):
            if not _in_footer(a):
                phone = clean(a.get_text(" ")) or a["href"][4:].strip()
                break
    business["Phone"] = phone

    # ---- Website URL ----
    website = _meta(soup, "business:contact_data:website")
    if not website:
        for a in soup.find_all("a", href=True):
            if re.match(r"^\s*visit website\s*$", clean(a.get_text(" ")), re.I) \
                    and a["href"].lower().startswith("http") and not _is_own(a["href"]):
                website = a["href"].strip()
                break
    if website and _is_own(website):
        website = ""
    business["Website URL"] = website

    # ---- Description ----
    desc = clean(ld.get("description", ""))
    if not desc:
        block = soup.select_one("#long-description .description, p.summary-description")
        desc = clean(block.get_text(" ")) if block else ""
    if not desc:
        desc = _about_text(soup)
    business["Description"] = desc

    # ---- Category ----
    cats = []
    for a in soup.select("a[title^='Explore Category'], .details-categories a"):
        text = clean(a.get_text(" "))
        if text and text not in cats:
            cats.append(text)
    business["Category"] = ", ".join(cats)

    # ---- Logo ----
    logo = ""
    for key in ("twitter:image", "og:image"):
        candidate = _meta(soup, key)
        if candidate and "content_files/img_logo" not in candidate:
            logo = urljoin(url, candidate)
            break
    business["Logo"] = logo

    return business