from ..common import *


_NBM_PLACEHOLDERS = {"", "n/a", "na", "none", "null", "-"}


def parse_nextbizmaker(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "html.parser")

    if _looks_blocked(html_text):
        return business

    entries = _nbm_ld_entries(soup)
    ld_business = _nbm_find(entries, "LocalBusiness")
    ld_page = _nbm_find(entries, "ProfilePage")
    ld_address = ld_business.get("address") if isinstance(ld_business.get("address"), dict) else {}

    # ---- Name ----
    name_el = soup.select_one(".header-member-name h1") or soup.select_one(".textbox-company")
    business["Business Name"] = (
        clean(name_el.get_text()) if name_el else _nbm_value(ld_business.get("name"))
    )

    # ---- Street / City / State / Zipcode ----
    address_el = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    address_text = clean(address_el.get_text(" ")) if address_el else ""
    if not is_meaningful(address_text):
        address_text = _nbm_value(ld_address.get("streetAddress"))
    street, city, state, zipcode = _nbm_split_address(address_text)
    business["Street"] = street
    business["City"] = city or _nbm_value(ld_address.get("addressLocality"))
    business["State"] = state or _nbm_value(ld_address.get("addressRegion"))
    business["Zipcode"] = zipcode or _nbm_value(ld_address.get("postalCode"))

    # ---- Country ----
    business["Country"] = _nbm_value(ld_address.get("addressCountry"))

    # ---- Phone ----
    phone_el = soup.select_one("span.phone_number") or soup.select_one(".phone_number_header")
    business["Phone"] = (
        clean(phone_el.get_text()) if phone_el and is_meaningful(phone_el.get_text())
        else _nbm_value(ld_business.get("telephone"))
    )

    # ---- Website URL ----
    link = soup.select_one("a.weblink[href]")
    if link and link["href"].strip().lower().startswith(("http://", "https://")):
        business["Website URL"] = link["href"].strip()

    # ---- Description (JSON-LD only on this template) ----
    business["Description"] = _nbm_value(
        ld_business.get("description") or ld_page.get("description")
    )

    # ---- Category ----
    cat_el = soup.select_one(".profile-header-top-category")
    if cat_el:
        business["Category"] = clean(cat_el.get_text())
    else:
        business["Category"] = _nbm_value(ld_page.get("keywords"))

    # ---- Logo ----
    logo = soup.select_one(".profile-image img[src]")
    if logo:
        business["Logo"] = urljoin(url, logo["src"].strip())

    # ---- Hours / Social Media Links / GBP Link (best-effort) ----
    scope = soup.select_one(".member_profile") or soup.select_one("#main-content")
    business["Hours"] = _nbm_hours(scope)
    business["Social Media Links"], business["GBP Link"] = _nbm_links(scope, business["Website URL"])

    return filter_business_fields(business, url)


# ---------------------------------------------------------------- helpers

def _nbm_value(value):
    """Clean a scalar; the site's "N/A" placeholders count as empty."""
    if not isinstance(value, str):
        return ""
    text = clean(value)
    return "" if text.lower() in _NBM_PLACEHOLDERS else text


def _nbm_ld_entries(soup):
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        if "LocalBusiness" not in raw:
            continue
        try:
            data = json.loads(raw, strict=False)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            return data["@graph"]
        return data if isinstance(data, list) else [data]
    return []


def _nbm_find(entries, ld_type):
    for entry in entries:
        if isinstance(entry, dict) and entry.get("@type") == ld_type:
            return entry
    return {}


def _nbm_split_address(address):
    """"street, city, state zip" -> parts. Anything with fewer than three
    comma segments is kept whole as the street rather than guessing, since
    _split_blinx_address() would otherwise treat a bare street as a state."""
    address = clean(address)
    if not address:
        return "", "", "", ""
    if len([p for p in address.split(",") if p.strip()]) < 3:
        return address, "", "", ""
    return _split_blinx_address(address)


def _nbm_hours(scope):
    if not scope:
        return ""
    box = scope.select_one(".table-display-hours, .table-display-business-hours, .business-hours")
    if box:
        rows = [clean(r.get_text(" ")) for r in box.select("li, tr, .row")]
        rows = [r for r in rows if is_meaningful(r)]
        if rows:
            return "; ".join(rows)
        text = clean(box.get_text(" "))
        text = re.sub(r"^(?:business\s+)?hours\s*:?\s*", "", text, flags=re.I)
        return text if is_meaningful(text) else ""
    return ""


def _nbm_links(scope, website):
    socials, gbp = {}, ""
    if not scope:
        return socials, gbp
    for a in scope.find_all("a", href=True):
        href = a["href"].strip()
        if not href.lower().startswith(("http://", "https://")):
            continue
        host = urlparse(href).netloc.lower()
        if "nextbizmaker.com" in host or href == website:
            continue
        # Maps links with 0,0 coordinates are the site's placeholder.
        if _is_maps_link(href) and "q=0.00000000,0.00000000" not in href:
            gbp = gbp or href
            continue
        for domain, label in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                socials.setdefault(label, href)
                break
    return socials, gbp