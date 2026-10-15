"""
Site parser: 757pages.com  (Brilliant Directories profile pages,
/<category-slug>/<business-slug>)

Same directory platform as techdirectory.io / nextbizthing.com, so
parse_nextbizthing() does the heavy lifting (name, "street, city, ST zip"
location, website link, description and country from JSON-LD). This layout
differs in a few places, handled here:

  - Phone: JSON-LD "telephone" is unformatted/garbled ("850460-2900"), so the
    visible .table-display-phone value ("(850) 460-2900") is used first.
  - Category: no breadcrumb / header block. Taken from the JSON-LD
    ProfilePage ("about" / "keywords"), then from the "All <category>" link,
    then from the URL slug ("real-estate-services-1" -> "Real Estate Services").
  - Logo: the JSON-LD LocalBusiness image (the uploaded profile photo). The
    site's "profile-placeholder" image means no logo was uploaded -> blank.
  - Hours: .table-display-hours (or any "Hours" row in the contact table);
    blank when the listing has none, as with Abbott Martin Group.
"""

from ..common import *  # noqa: F401,F403
from .nextbizmaker import _nbm_ld_entries, _nbm_find, _nbm_value, _nbm_hours
from .nextbizthing import parse_nextbizthing

_PLACEHOLDER_IMG_RE = re.compile(r"placeholder|no[-_]?image|default", re.I)


def _p757_category(soup, entries, url):
    profile = _nbm_find(entries, "ProfilePage")
    for key in ("about", "keywords"):
        val = profile.get(key)
        if isinstance(val, list):
            val = next((v for v in val if isinstance(v, str) and v.strip()), "")
        if isinstance(val, str) and is_meaningful(val):
            return clean(val.split(",")[0])

    link = soup.select_one(".related-searches-ul a[title], [class*=related-searches] a[title]")
    if link and is_meaningful(link.get("title", "")):
        return clean(link["title"])

    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) >= 2:
        slug = re.sub(r"-\d+$", "", parts[0])
        return clean(slug.replace("-", " ").title())
    return ""


def _p757_hours(soup):
    hours = _nbm_hours(soup)
    if hours:
        return hours
    for group in soup.select(".table-view-group"):
        label = group.select_one(".col-sm-4")
        value = group.select_one(".col-sm-8")
        if label and value and re.search(r"hours", label.get_text(), re.I):
            text = clean(value.get_text(" "))
            if is_meaningful(text):
                return text
    return ""


def parse_pages757(url, html_text):
    business = parse_nextbizthing(url, html_text)
    if not any(v for v in business.values()):
        return business  # blocked / empty page

    soup = BeautifulSoup(html_text, "html.parser")
    entries = _nbm_ld_entries(soup)
    ld_business = _nbm_find(entries, "LocalBusiness")

    # ---- Phone (visible, formatted value first) ----
    phone_el = soup.select_one(".table-display-phone .col-sm-8")
    if phone_el and sum(ch.isdigit() for ch in phone_el.get_text()) >= 7:
        business["Phone"] = clean(phone_el.get_text(" "))

    # ---- Category ----
    business["Category"] = _p757_category(soup, entries, url)

    # ---- Logo ----
    image = ld_business.get("image")
    logo = image.get("url") if isinstance(image, dict) else image
    logo = _nbm_value(logo) if isinstance(logo, str) else ""
    if logo and not _PLACEHOLDER_IMG_RE.search(logo):
        business["Logo"] = urljoin(url, logo)
    else:
        business["Logo"] = ""

    # ---- Hours ----
    business["Hours"] = _p757_hours(soup)

    return filter_business_fields(business, url)