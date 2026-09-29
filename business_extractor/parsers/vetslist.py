"""
Site parser: vetslist.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_vetslist(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".member_profile h1.bold") or soup.select_one("h1.bold")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Phone ----
    phone_span = soup.select_one('span[itemprop="telephone"]')
    if phone_span:
        phone_text = clean(phone_span.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text

    # ---- Address ----
    # VetsList listings vary in markup: some have no itemprop="streetAddress"
    # at all -- addressLocality holds a combined "City ST" string (e.g.
    # "Plano TX") and postalCode holds the zip separately, with the country
    # as plain text right after a <br/> in the same block (e.g.
    # "...75023<br/>United States of America"). Others (e.g. WrightWay
    # Emergency Services) instead put the *entire* address -- street, city,
    # state, and zip -- into a single itemprop="streetAddress" span (e.g.
    # "300 Triple Diamond Blvd ,Nokomis, FL 34275") with nothing else in the
    # block, so neither the addressLocality nor postalCode selector matched
    # and every address field was silently left blank. Handle both shapes.
    addr_li = soup.select_one('[itemprop="address"][itemtype*="PostalAddress"]') \
        or soup.select_one('[itemprop="address"]')
    if addr_li:
        def _span(prop):
            el = addr_li.select_one('[itemprop="%s"]' % prop)
            txt = clean(el.get_text()) if el else ""
            return txt if is_meaningful(txt) else ""

        street_text = _span("streetAddress")
        locality_text = _span("addressLocality")
        region_text = _span("addressRegion")
        postal_text = _span("postalCode")

        if street_text and not (locality_text or region_text or postal_text):
            # Whole address crammed into one streetAddress span.
            street, city, state, zipcode = _split_address_allow_no_comma(street_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode
        else:
            # Street is read independently of the locality/postal spans.
            if street_text:
                business["Street"] = street_text
            if locality_text:
                m = re.match(
                    r"^(?P<city>[A-Za-z][A-Za-z .'-]*?)[,\s]+(?P<state>[A-Z]{2})$",
                    locality_text,
                )
                if m:
                    business["City"] = m.group("city").strip()
                    business["State"] = m.group("state")
                else:
                    business["City"] = locality_text
            if region_text and not business["State"]:
                business["State"] = region_text
            if postal_text:
                business["Zipcode"] = postal_text

        # Country is plain text (not a tag) after the last <br>.
        for br in reversed(addr_li.find_all("br")):
            sib = br.next_sibling
            if isinstance(sib, NavigableString):
                country_text = clean(str(sib))
                if is_meaningful(country_text):
                    business["Country"] = country_text
                    break

    # ---- JSON-LD fallback for anything the itemprop markup didn't give us ----
    if not (business["Street"] and business["State"]):
        for tag in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(tag.string or "")
            except (ValueError, TypeError):
                continue
            nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
            for node in nodes if isinstance(nodes, list) else []:
                addr = node.get("address") if isinstance(node, dict) else None
                if isinstance(addr, dict):
                    for key, field in (
                        ("streetAddress", "Street"),
                        ("addressLocality", "City"),
                        ("addressRegion", "State"),
                        ("postalCode", "Zipcode"),
                    ):
                        val = clean(str(addr.get(key, "")))
                        if is_meaningful(val) and not business[field]:
                            business[field] = val

    # ---- Category (breadcrumb crumb right before the current-page
    # business name; "Home"/root crumbs are excluded) ----
    crumbs = [
        clean(li.get_text())
        for li in soup.select("ol.breadcrumb li[itemprop='itemListElement']")
    ]
    crumbs = [c for c in crumbs if c]
    if len(crumbs) >= 2:
        business["Category"] = crumbs[-1]

    # ---- Website URL & Description ----
    about = soup.select_one(".textarea.textarea-about_me")
    if about:
        paragraphs = [clean(p.get_text()) for p in about.find_all("p")]
        desc_parts = []
        i = 0
        while i < len(paragraphs):
            label = paragraphs[i].rstrip(":").strip().lower()
            if label in ("url", "website") and i + 1 < len(paragraphs):
                url_text = paragraphs[i + 1]
                if is_meaningful(url_text):
                    business["Website URL"] = url_text
                i += 2
                continue
            if label == "about us" and i + 1 < len(paragraphs):
                # Collect every remaining paragraph as the description --
                # some listings wrap it across more than one <p>.
                for p_text in paragraphs[i + 1:]:
                    if is_meaningful(p_text):
                        desc_parts.append(p_text)
                break
            i += 1
        if desc_parts:
            business["Description"] = "\n".join(desc_parts)

    # ---- Logo (dedicated itemprop, falling back to og:image) ----
    logo_img = soup.select_one('img[itemprop="logo"]')
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])

    if not business["Logo"]:
        og_image = soup.select_one('meta[property="og:image"]')
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- GBP Link ("Get Directions" Google Maps anchor) ----
    directions = soup.select_one("a.get-directions-link[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    return business
