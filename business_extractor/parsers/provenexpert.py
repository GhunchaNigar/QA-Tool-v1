"""
Site parser: provenexpert.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py
from ..common import _extract_pe_cover, _extract_pe_gallery, _extract_pe_hours, pe_hours_need_render


def parse_provenexpert(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- JSON-LD (Name, Logo, Street/City/Zipcode/Country, Phone) ----
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]
        for obj in objects:
            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
                continue

            if obj.get("name"):
                business["Business Name"] = obj["name"]

            image = obj.get("image")
            if isinstance(image, dict) and image.get("url"):
                business["Logo"] = image["url"]
            elif isinstance(image, str) and image:
                business["Logo"] = image

            addr = obj.get("address", {})
            if addr.get("streetAddress"):
                business["Street"] = addr["streetAddress"]
            if addr.get("addressLocality"):
                business["City"] = addr["addressLocality"]
            if addr.get("postalCode"):
                business["Zipcode"] = addr["postalCode"]
            if addr.get("addressCountry"):
                business["Country"] = addr["addressCountry"]

            if obj.get("telephone"):
                business["Phone"] = obj["telephone"]

    # ---- Business Name fallback (visible <h1>) ----
    if not business["Business Name"]:
        h1 = soup.select_one("h1.profileName")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Category (tagline directly under the business name) ----
    job = soup.select_one("h2.profileJob")
    if job:
        business["Category"] = clean(job.get_text())

    # ---- Keywords  ----
    tags = [clean(t.get_text()) for t in soup.select("#offerTagsPublic .peTagPill")]
    tags = [t for t in tags if t]
    if tags:
        business["Keywords"] = ", ".join(tags)

    # Fallback: many profiles list their services only in the tagline under
    # the name (h2.profileJob), e.g. "HVAC contractor, HVAC repair, AC
    # installation." or "luxury alterations | tailor | alterations".
    if not business["Keywords"] and job:
        parts = re.split(r"\s*[,|;]\s*", clean(job.get_text()))
        keywords = []
        for part in parts:
            part = part.strip(" .")
            if part and part.lower() not in {k.lower() for k in keywords}:
                keywords.append(part)
        if keywords:
            business["Keywords"] = ", ".join(keywords)

    # ---- Description (About text, incl. the CSS-hidden continuation) ----
    welcome = soup.select_one("#welcomeTextPublic")
    if welcome:
        for junk in welcome.select(".textEtc, .collapseAboutme, .foldAboutme, #offerTags"):
            junk.decompose()
        text = clean(welcome.get_text(separator=" "))
        if is_meaningful(text):
            business["Description"] = text

    # ---- Contact box: State (JSON-LD doesn't have it), Phone, Email ----
    contact = soup.select_one("#personalPublic")
    if contact:
        address_tag = contact.select_one("address")
        if address_tag:
            lines = [clean(l) for l in address_tag.get_text(separator="\n").split("\n")]
            lines = [l for l in lines if l]
            if len(lines) >= 3 and not business["State"]:
                business["State"] = re.sub(r"\s*\([A-Za-z]{2,3}\)\s*$", "", lines[2]).strip()
            if len(lines) >= 4 and not business["Zipcode"]:
                business["Zipcode"] = lines[3]
            if len(lines) >= 5 and not business["Country"]:
                business["Country"] = lines[4]

        # ---- Owner Name ("Contact person" label, with the name itself
        #      sitting as a bare text node right after a <br> rather than
        #      inside its own tag) ----
        for strong in contact.find_all("strong"):
            if clean(strong.get_text()).lower() != "contact person":
                continue

            owner_name = ""
            node = strong.next_sibling
            while node is not None:
                if isinstance(node, NavigableString):
                    text = clean(str(node))
                    if text:
                        owner_name = text
                        break
                elif getattr(node, "name", None) != "br":
                    break
                node = node.next_sibling

            if owner_name:
                business["Owner Name"] = owner_name
            break

        tel = contact.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

        # mailto hrefs here carry a "?Subject=..." query string -- strip it.
        email = contact.select_one('a[href^="mailto:"]')
        if email:
            business["Business Email"] = email["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Website URL ("Websites" box) ----
    # The box can also hold a "GMB Listing" (Google Maps) link and, in a
    # second #profilesPublic block, social profiles -- and either can come
    # first. Take the first link that is neither of those.
    for a in soup.select("#profilesPublic a[href^='http']"):
        href = a["href"].strip()
        low = href.lower()
        if _is_maps_link(href):
            continue
        if any(domain in low for domain in SOCIAL_DOMAINS):
            continue
        business["Website URL"] = href
        break

    # ---- Social Media Links / GBP Link (anchors across the profile links box) ----
    for a in soup.select("#profilesPublic a[href^='http'], #personalPublic a[href^='http']"):
        href = a["href"]
        if _is_maps_link(href):
            if not business["GBP Link"]:
                business["GBP Link"] = href
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    # ---- Hours (JS-rendered into #profilesOpening; see needs_render) ----
    hours = _extract_pe_hours(soup)
    if not hours:
        hours_tag = soup.select_one('[itemprop="openingHours"]') or soup.select_one(".openingHours")
        if hours_tag:
            hours = clean(hours_tag.get_text(separator=" "))
    if is_meaningful(hours):
        business["Hours"] = hours

    # ---- Photos (cover image + profile gallery) ----
    photos = []

    def _add_photo(src):
        if not src or src.startswith("data:"):
            return
        src = urljoin(url, src.strip().strip("'\""))
        if src not in photos:
            photos.append(src)

    # Cover image (CSS background in #customHeaderStyle) -- shared
    # helper with provenemployer.com, see common._extract_pe_cover.
    _add_photo(_extract_pe_cover(soup, url))

    # Gallery (background-image thumbnails and/or <img> tags).
    for src in _extract_pe_gallery(soup, url):
        _add_photo(src)

    if photos:
        business["Photos"] = photos

    return business


# Hours are filled in by JavaScript after page load, so ask dispatch to
# re-fetch with Playwright when the static HTML has an empty hours box.
parse_provenexpert.needs_render = pe_hours_need_render
