"""
Site parser: vetslist.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


def _strip_leaked_address(text, business):
    """Some listings glue the street address and the country into a single
    text node with no <br/> or tag boundary for BeautifulSoup to split on
    -- the whole thing is one literal string in the source HTML (e.g.
    "1883 N Silverspring Dr, Appleton, WI 54913 United States of
    America"). If the zip code we already parsed for this business shows
    up inside the candidate country text, assume the address leaked in
    and keep only whatever comes after the LAST occurrence of the zip
    code, stripping any leftover punctuation/whitespace.
    """
    zipcode = business.get("Zipcode")
    if is_meaningful(zipcode) and zipcode in text:
        text = text.rsplit(zipcode, 1)[-1]
    return clean(text.strip(" ,-"))


def _country_from_line(header_line, business):
    """Extract a country string from a "<category><br/><country>" style
    header line -- or from a "Get Directions" style line where the
    address and country are glued together with no separator at all.
    Returns "" if nothing usable is found.
    """
    # Preferred path: a real <br/> splits the line into distinct pieces.
    # get_text(separator="\n") is robust to the country being a bare text
    # node OR wrapped in its own tag after the <br/>.
    if header_line.find("br"):
        lines = [clean(t) for t in header_line.get_text(separator="\n").split("\n")]
        lines = [t for t in lines if is_meaningful(t)]
        if len(lines) >= 2:
            candidate = _strip_leaked_address(lines[-1], business)
            if is_meaningful(candidate):
                return candidate

    # Fallback: no <br/> (or the <br/>-based split didn't isolate
    # anything usable) -- the address and country may be one literal
    # text node glued together by a plain space in the source HTML.
    # Anchor on the zip code we already parsed to pull the country back
    # out; if there's no zip code to anchor on, or the zip code isn't in
    # this line at all, there's nothing safe to extract here.
    full_text = clean(header_line.get_text(separator=" "))
    zipcode = business.get("Zipcode")
    if is_meaningful(zipcode) and zipcode in full_text:
        candidate = _strip_leaked_address(full_text, business)
        if is_meaningful(candidate):
            return candidate

    return ""


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
    # VetsList listings vary in markup:
    #   1) Some have no itemprop="streetAddress" at all -- addressLocality
    #      holds just "City ST" (e.g. "Plano TX") and postalCode holds the
    #      zip separately, with the country as plain text right after a
    #      <br/> in the same block (e.g. "...75023<br/>United States of
    #      America").
    #   2) Others (e.g. WrightWay Emergency Services) instead put the
    #      *entire* address -- street, city, state, and zip -- into a
    #      single itemprop="streetAddress" span (e.g. "300 Triple Diamond
    #      Blvd ,Nokomis, FL 34275") with nothing else in the block. This
    #      shape has NO country text after its <br/> -- it lives in a
    #      separate header line instead (see the Country fallback below).
    #   3) Others still (e.g. Valley Exteriors) put the *entire* address
    #      into the itemprop="addressLocality" span itself (e.g.
    #      "1883 N Silverspring Dr, Appleton, WI 54913"), with no separate
    #      streetAddress or postalCode span at all. The old "City ST"
    #      regex didn't match this longer string, so it silently fell back
    #      to dumping the whole string into City and left Street/State/Zip
    #      blank. Detect and split this full-address shape explicitly.
    addr_li = soup.select_one('[itemprop="address"][itemtype*="PostalAddress"]')
    if addr_li:
        locality_span = addr_li.select_one('span[itemprop="addressLocality"]')
        if locality_span:
            locality_text = clean(locality_span.get_text())

            # Shape 3: "Street, City, ST Zip" all inside addressLocality.
            full_match = re.match(
                r"^(?P<street>.+?),\s*(?P<city>[A-Za-z][A-Za-z .'-]*?),\s*"
                r"(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$",
                locality_text,
            )
            # Shape 1: just "City ST".
            simple_match = re.match(
                r"^(?P<city>[A-Za-z][A-Za-z .'-]*?)\s+(?P<state>[A-Z]{2})$",
                locality_text,
            )

            if full_match:
                street = clean(full_match.group("street"))
                city = clean(full_match.group("city"))
                state = clean(full_match.group("state"))
                zipcode = clean(full_match.group("zip"))
                if is_meaningful(street):
                    business["Street"] = street
                if is_meaningful(city):
                    business["City"] = city
                if is_meaningful(state):
                    business["State"] = state
                if is_meaningful(zipcode):
                    business["Zipcode"] = zipcode
            elif simple_match:
                business["City"] = simple_match.group("city")
                business["State"] = simple_match.group("state")
            elif is_meaningful(locality_text):
                business["City"] = locality_text

        postal_span = addr_li.select_one('span[itemprop="postalCode"]')
        if postal_span:
            postal_text = clean(postal_span.get_text())
            if is_meaningful(postal_text) and not business["Zipcode"]:
                business["Zipcode"] = postal_text

        if not locality_span and not postal_span:
            street_span = addr_li.select_one('span[itemprop="streetAddress"]')
            if street_span:
                addr_text = clean(street_span.get_text())
                if is_meaningful(addr_text):
                    street, city, state, zipcode = _split_blinx_address(addr_text)
                    if is_meaningful(street):
                        business["Street"] = street
                    if is_meaningful(city):
                        business["City"] = city
                    if is_meaningful(state):
                        business["State"] = state
                    if is_meaningful(zipcode):
                        business["Zipcode"] = zipcode

        br = addr_li.find("br")
        if br and br.next_sibling:
            country_text = clean(str(br.next_sibling))
            country_text = _strip_leaked_address(country_text, business)
            if is_meaningful(country_text):
                business["Country"] = country_text

    # ---- Country (fallback) ----
    # Shape 2 listings (see the Address comment above) have no country text
    # inside the itemprop="address" block at all -- the <br/> there is
    # followed immediately by the closing </li>, so the lookup above finds
    # nothing. On those listings the country instead lives in the profile
    # header, in a "<category><br/><country>" line right under the business
    # name/h1, e.g.:
    #   <p class="line-height-xl nomargin">
    #       Legal, Community & Education<br />United States of America
    #   </p>
    # Only used as a fallback so it never overrides a country already found
    # in the address block itself.
    #
    # CAUTION #1: "line-height-xl" is not unique to that header line -- the
    # sidebar "Get Directions" widget also stamps it on its own address
    # paragraph, e.g. (Valley Exteriors):
    #   <p class="btn-sm bg-secondary text-center nomargin line-height-xl
    #             bold no-radius-bottom">
    #       <i class="fa fa-map-marker fa-fw text-danger"></i>
    #       1883 N Silverspring Dr, Appleton, WI 54913 United States of America
    #   </p>
    # On some listings that widget DOES carry the country appended after
    # the address -- but on the SAME line, with no <br/> or tag boundary
    # separating them (it's one literal text node in the source HTML), so
    # line-splitting alone can't isolate the country from the address.
    #
    # CAUTION #2: because of that, naive get_text() splitting either grabs
    # nothing (no <br/> to split on) or grabs the whole glued string
    # ("1883 N Silverspring Dr, Appleton, WI 54913 United States of
    # America") as a single "country". Since we've already parsed this
    # business's zip code by this point, use it as an anchor: whatever
    # text follows the zip code -- whether split by a <br/>, a tag
    # boundary, or nothing at all -- is the real country.
    if not is_meaningful(business["Country"]):
        for header_line in soup.select("p.line-height-xl"):
            country_text = _country_from_line(header_line, business)
            if is_meaningful(country_text):
                business["Country"] = country_text
                break

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
