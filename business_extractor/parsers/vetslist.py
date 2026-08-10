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
    # This site has no itemprop="streetAddress" at all -- addressLocality
    # holds a combined "City ST" string (e.g. "Plano TX") and postalCode
    # holds the zip separately, with the country as plain text right after
    # a <br/> in the same block (e.g. "...75023<br/>United States of America").
    addr_li = soup.select_one('[itemprop="address"][itemtype*="PostalAddress"]')
    if addr_li:
        locality_span = addr_li.select_one('span[itemprop="addressLocality"]')
        if locality_span:
            locality_text = clean(locality_span.get_text())
            match = re.match(r"^(?P<city>[A-Za-z][A-Za-z .'-]*?)\s+(?P<state>[A-Z]{2})$", locality_text)
            if match:
                business["City"] = match.group("city")
                business["State"] = match.group("state")
            elif is_meaningful(locality_text):
                business["City"] = locality_text

        postal_span = addr_li.select_one('span[itemprop="postalCode"]')
        if postal_span:
            postal_text = clean(postal_span.get_text())
            if is_meaningful(postal_text):
                business["Zipcode"] = postal_text

        br = addr_li.find("br")
        if br and br.next_sibling:
            country_text = clean(str(br.next_sibling))
            if is_meaningful(country_text):
                business["Country"] = country_text

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


