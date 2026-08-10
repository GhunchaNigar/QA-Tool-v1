"""
Site parser: bulkpostads.com (also reused, unchanged, for bulkadspost.com --
same GeoDirectory theme/markup)
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def _bulkpostads_jsonld_local_business(soup):
    """Return the LocalBusiness object from the page's JSON-LD. On this
    template it's its own top-level script (not wrapped in @graph), but
    handle both shapes since other listings could vary."""
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue

        graph = data.get("@graph") if isinstance(data, dict) else None
        objects = graph if isinstance(graph, list) else (
            data if isinstance(data, list) else [data]
        )

        for obj in objects:
            if isinstance(obj, dict) and obj.get("@type") == "LocalBusiness":
                return obj

    return None


def _bulkpostads_deobfuscated_email(a_tag):
    """The email link's visible text is split across text nodes with
    empty HTML comments injected between them as an anti-scraper
    obfuscation (e.g. "info<!---->@<!---->wrightway.com"). Comments
    carry no text of their own, but the surrounding whitespace does --
    so strip each text node individually and join with no separator,
    rather than calling get_text(), which would leave stray spaces
    around the "@"."""
    if not a_tag:
        return ""
    parts = []
    for node in a_tag.find_all(string=True):
        if isinstance(node, Comment):
            continue
        text = node.strip()
        if text:
            parts.append(text)
    return "".join(parts)


def parse_bulkpostads(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    ld_business = _bulkpostads_jsonld_local_business(soup) or {}

    # ---- Business Name ----
    if ld_business.get("name"):
        business["Business Name"] = clean(ld_business["name"])

    if not business["Business Name"]:
        h1 = soup.select_one("h1.page-header-title")
        if h1:
            # Title is "<Name> in <City>, <State>, <Country>" -- keep
            # only the part before " in ".
            title_text = clean(h1.get_text())
            business["Business Name"] = title_text.split(" in ")[0].strip()

    # ---- Street / City / State / Zipcode / Country (JSON-LD address) ----
    addr_obj = ld_business.get("address")
    if isinstance(addr_obj, dict):
        street_raw = clean(addr_obj.get("streetAddress", ""))
        # This template's streetAddress sometimes has ",City State"
        # appended onto the actual street (a bug in their own JSON-LD) --
        # keep only the part before the first comma.
        business["Street"] = street_raw.split(",")[0].strip()
        business["City"] = clean(addr_obj.get("addressLocality", ""))
        business["State"] = clean(addr_obj.get("addressRegion", ""))
        business["Zipcode"] = clean(addr_obj.get("postalCode", ""))
        business["Country"] = clean(addr_obj.get("addressCountry", ""))

    # ---- Address fallback (sidebar widget) ----
    if not business["Street"]:
        street_el = soup.select_one(".geodir-field-address [itemprop='streetAddress']")
        if street_el:
            business["Street"] = clean(street_el.get_text()).split(",")[0].strip()

    if not business["Zipcode"]:
        zip_el = soup.select_one(".geodir-field-address [itemprop='postalCode']")
        if zip_el:
            business["Zipcode"] = clean(zip_el.get_text())

    # ---- Zipcode fallback: regex out of the raw address blob ----
    # This template doesn't always give postalCode its own field/element --
    # sometimes it's just tacked onto the end of the streetAddress string
    # (e.g. "131 Continental Dr, Suite 305, Newark, Delaware 19713"), so
    # postalCode in the JSON-LD is blank AND there's no itemprop='postalCode'
    # element to select above. Pull it out of whichever raw address string
    # we have with a regex as a last resort.
    if not business["Zipcode"]:
        addr_blob = ""
        if isinstance(addr_obj, dict):
            addr_blob = addr_obj.get("streetAddress", "")
        if not addr_blob:
            street_el = soup.select_one(".geodir-field-address [itemprop='streetAddress']")
            if street_el:
                addr_blob = street_el.get_text()
        zip_match = re.search(r"\b(\d{5})(?:-\d{4})?\b", addr_blob)
        if zip_match:
            business["Zipcode"] = zip_match.group(0)

    # ---- Phone ----
    tel = soup.select_one(".geodir-field-phone a[href^='tel:']")
    if tel:
        phone_text = clean(tel.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text

    if not business["Phone"] and ld_business.get("telephone"):
        business["Phone"] = clean(ld_business["telephone"])

    # ---- Hours (only present on listings that filled it in; not every
    # listing on this template publishes one, so no fallback needed) ----
    hours_el = soup.select_one(".geodir_post_meta.geodir-field-business_hours")
    if hours_el:
        icon_el = hours_el.select_one(".geodir_post_meta_icon")
        if icon_el:
            icon_el.extract()
        hours_text = clean_multiline(hours_el.get_text(separator="\n"))
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Website URL ----
    website_link = soup.select_one(".geodir-field-website a[href]")
    if website_link:
        business["Website URL"] = website_link["href"].strip()

    # ---- Business Email (de-obfuscated mailto link) ----
    email_link = soup.select_one(".geodir-field-email a")
    email_text = _bulkpostads_deobfuscated_email(email_link)
    if is_meaningful(email_text):
        business["Business Email"] = email_text

    # ---- Description ----
    if ld_business.get("description"):
        desc_text = clean(ld_business["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"]:
        content_p = soup.select_one(".geodir-field-post_content p")
        if content_p:
            desc_text = clean(content_p.get_text())
            if is_meaningful(desc_text):
                business["Description"] = desc_text

    # ---- Category ----
    category_link = soup.select_one(".geodir_post_meta.geodir-field-post_category a")
    if category_link:
        cat_text = clean(category_link.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Keywords (Place Tags) ----
    tag_links = soup.select(".geodir_post_meta.geodir-field-post_tags a")
    if tag_links:
        tags = [clean(a.get_text()) for a in tag_links]
        tags = [t for t in tags if is_meaningful(t)]
        if tags:
            business["Keywords"] = ", ".join(tags)

    # ---- Logo (featured image) ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])

    if not business["Logo"] and ld_business.get("image"):
        image = ld_business["image"]
        image_url = image.get("url") if isinstance(image, dict) else image
        if image_url:
            business["Logo"] = urljoin(url, image_url)

    # ---- Photos (gallery tab) ----
    photos = []
    for a in soup.select(".geodir-images-gallery a.aui-lightbox-image[href]"):
        photo_url = urljoin(url, a["href"].strip())
        if photo_url not in photos:
            photos.append(photo_url)
    if photos:
        business["Photos"] = photos

    return business


