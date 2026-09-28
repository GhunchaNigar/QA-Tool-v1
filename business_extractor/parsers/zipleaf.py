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
    # Rendered server-side as:
    #   <div class="zl-hours">
    #     <div class="zl-hours-row [is-today]">
    #       <div class="zl-hours-day">Saturday <span class="zl-hours-today">Today</span></div>
    #       <div class="zl-hours-when">
    #         <span class="zl-hours-time"><time>11:30</time><span class="zl-hours-sep">–</span><time>18:30</time></span>
    #         <span class="zl-hours-note"></span>
    #       </div>
    #     </div> ...
    hours_rows = []
    for row in soup.select(".zl-hours .zl-hours-row"):
        day_el = row.select_one(".zl-hours-day")
        if not day_el:
            continue
        day_el = BeautifulSoup(str(day_el), "lxml")
        for badge in day_el.select(".zl-hours-today"):
            badge.decompose()  # drop the "Today" marker
        day = clean(day_el.get_text(" "))

        when = row.select_one(".zl-hours-when")
        value = ""
        if when:
            times = [clean(t.get_text()) for t in when.select("time") if clean(t.get_text())]
            if len(times) >= 2:
                # pair up open/close times (handles split shifts too)
                value = ", ".join(
                    f"{times[i]} - {times[i + 1]}" for i in range(0, len(times) - 1, 2)
                )
            else:
                value = clean(when.get_text(" "))  # e.g. "Closed" / "Open 24 hours"
            note_el = when.select_one(".zl-hours-note")
            note = clean(note_el.get_text()) if note_el else ""
            if note and note not in value:
                value = f"{value} ({note})" if value else note
        if day:
            hours_rows.append(f"{day}: {value}" if value else day)
    if hours_rows:
        business["Hours"] = " | ".join(hours_rows)

    if not business["Hours"]:
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