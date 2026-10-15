"""
Site parser: couponler.com  (public coupon pages, /<business-slug>/<coupon-slug>)

The page is a single "coupon card" (inside #non-printable; a print-only copy
of the same data sits in #printable and is ignored because select_one() hits
the first match):

  .offer h1                    -> Business Name
  .offer h2                    -> Description (the coupon text)
  .couponLogo img              -> Logo (a pre-signed S3 URL -- it carries an
                                  X-Amz-Expires=300 query string, so it is
                                  only valid for ~5 minutes after the page
                                  was served; it is returned unchanged
                                  because stripping the query breaks it)
  .couponAddress               -> "<street><br>City, ST ZIP<br>", then the
                                  .couponPhone div and the website <a>

State is only available in the visible "City, ST ZIP" line. The page <meta>
tags (business:contact_data:*) are used as fallbacks for street / city / zip.
"""

from ..common import *  # noqa: F401,F403

_CITY_STATE_ZIP_RE = re.compile(
    r"^(?P<city>.+?)[,\s]+(?P<state>[A-Za-z]{2})\.?\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _cpl_meta(soup, prop):
    tag = soup.find("meta", attrs={"property": prop})
    return clean(tag.get("content", "")) if tag else ""


def _cpl_address_lines(address_box):
    """Plain-text lines that sit directly inside .couponAddress (the street
    and the 'City, ST ZIP' line). The <h3> name, .couponPhone div and website
    link are tags, not direct strings, so they are skipped automatically."""
    lines = []
    for node in address_box.children:
        if isinstance(node, NavigableString) and not isinstance(node, Comment):
            text = clean(str(node))
            if text:
                lines.append(text)
    return lines


def parse_couponler(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "html.parser")

    offer = soup.select_one(".couponDash .offer") or soup

    # ---- Business Name ----
    h1 = offer.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"]:
        business["Business Name"] = _cpl_meta(soup, "og:title")

    # ---- Address ----
    address_box = offer.select_one(".couponBusinessBox .couponAddress")
    street = city = state = zipcode = ""
    if address_box:
        lines = _cpl_address_lines(address_box)
        # The last line that looks like "City, ST 12345" is the locality line;
        # everything before it is the street.
        for i in range(len(lines) - 1, -1, -1):
            m = _CITY_STATE_ZIP_RE.match(lines[i])
            if m:
                city, state, zipcode = m.group("city"), m.group("state").upper(), m.group("zip")
                street = ", ".join(lines[:i])
                break
        else:
            street = ", ".join(lines)

    street = street or _cpl_meta(soup, "business:contact_data:street_address")
    city = city or _cpl_meta(soup, "business:contact_data:locality")
    zipcode = zipcode or _cpl_meta(soup, "business:contact_data:postal_code")
    business["Street"], business["City"] = street, city
    business["State"], business["Zipcode"] = state, zipcode

    # ---- Phone ----
    phone = (address_box or offer).select_one(".couponPhone")
    if phone:
        business["Phone"] = clean(phone.get_text())

    # ---- Website URL ----
    link = None
    for a in (address_box or offer).find_all("a", href=True):
        if a["href"].strip().lower().startswith(("http://", "https://")):
            link = a
            break
    if not link:
        link = offer.select_one(".couponLogo a[href]")
    if link and link["href"].strip().lower().startswith(("http://", "https://")):
        business["Website URL"] = link["href"].strip()

    # ---- Description ----
    h2 = offer.find("h2")
    if h2:
        business["Description"] = clean_multiline(h2.decode_contents())
        # decode_contents() keeps the <p> tags; strip any remaining markup.
        business["Description"] = clean(BeautifulSoup(business["Description"], "html.parser").get_text(" "))
    if not business["Description"]:
        business["Description"] = _cpl_meta(soup, "og:description")

    # ---- Logo ----
    img = offer.select_one(".couponLogo img[src]")
    if img:
        business["Logo"] = urljoin(url, img["src"].strip().replace("&amp;", "&"))

    return filter_business_fields(business, url)