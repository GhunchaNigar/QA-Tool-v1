"""
Site parser: zipleaf.us
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



ZIPLEAF_SHARE_LINK_SIGNALS = [
    "sharer.php", "intent/tweet", "share-offsite", "pin/create/button",
]


def parse_zipleaf(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- JSON-LD (primary source: name, address, phone, logo, description) ----
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

            business["Business Name"] = obj.get("name", business["Business Name"])

            if obj.get("description"):
                business["Description"] = clean(obj["description"])

            if obj.get("image") and not business["Logo"]:
                business["Logo"] = urljoin(url, obj["image"])

            if obj.get("telephone") and not business["Phone"]:
                business["Phone"] = obj["telephone"]

            addr = obj.get("address", {})
            if not business["Street"]:
                business["Street"] = addr.get("streetAddress", "")
            if not business["City"]:
                business["City"] = addr.get("addressLocality", "")
            if not business["State"]:
                business["State"] = addr.get("addressRegion", "")
            if not business["Zipcode"]:
                business["Zipcode"] = addr.get("postalCode", "")
            if not business["Country"]:
                business["Country"] = addr.get("addressCountry", "")

    # ---- Business Name fallback (visible listing title) ----
    if not business["Business Name"]:
        title = soup.select_one("h3.card-title span")
        if title:
            business["Business Name"] = clean(title.get_text())

    main_card = soup.select_one("div.listing-contact-info") or soup

    # ---- Website URL (visible text of the site link, not its redirect href) ----
    website_link = main_card.select_one('a[href^="/GoToWebsite/"], a[href*="/GoToWebsite/"]')
    if website_link:
        site_text = clean(website_link.get_text())
        if site_text:
            business["Website URL"] = site_text
        elif website_link.get("href"):
            business["Website URL"] = urljoin(url, website_link["href"])

    # ---- Phone fallback (tel: link) ----
    if not business["Phone"]:
        tel = main_card.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Business Email (mailto: link, if present) ----
    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Description fallback (meta description) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            business["Keywords"] = clean(kw_raw)

    if not business["Keywords"]:
        product_tags = [clean(a.get_text()) for a in soup.select("a.product-link")]
        product_tags = [t for t in product_tags if t]
        if product_tags:
            business["Keywords"] = ", ".join(product_tags)

    # ---- Logo fallback (listing photo / og:image) ----
    if not business["Logo"]:
        logo_img = soup.select_one("#business-logo img[src]")
        if logo_img:
            business["Logo"] = urljoin(url, logo_img["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Category (breadcrumb, minus Home / location / listing-name crumbs) ----
    crumbs = [clean(li.get_text()) for li in soup.select("ol.breadcrumb li.breadcrumb-item")]
    skip = {"home", (business["Business Name"] or "").lower()}
    category_crumbs = [c for c in crumbs if c and c.lower() not in skip]
    if category_crumbs:
        business["Category"] = ", ".join(category_crumbs)

    # ---- GBP Link (a Google Maps / Business Profile link, if present) ----
    gbp_link = soup.select_one('a[href*="google.com/maps"], a[href*="g.page"], a[href*="goo.gl/maps"]')
    if gbp_link and gbp_link.get("href"):
        business["GBP Link"] = gbp_link["href"]

    # ---- Hours ----
    hours_tag = soup.select_one('[itemprop="openingHours"]') or soup.select_one(".listing-hours, .business-hours")
    if hours_tag:
        hours_text = clean(hours_tag.get_text(separator=" "))
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Social Media Links ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(sig in href.lower() for sig in ZIPLEAF_SHARE_LINK_SIGNALS):
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business

