"""
Site parser: freelistingusa.com
"""

from ..common import *  



def parse_freelistingusa(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (og:title, minus the site-name suffix) ----
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        business["Business Name"] = clean(og_title["content"]).split("|")[0].strip()

    if not business["Business Name"]:
        h_tag = soup.find(re.compile(r"^h[1-6]$"))
        if h_tag:
            business["Business Name"] = clean(h_tag.get_text())

    # ---- Contact block, scoped via the tel: link ----
    tel = soup.select_one('a[href^="tel:"]')
    scope = soup

    if tel:
        business["Phone"] = tel["href"].replace("tel:", "").strip()
        # Walk up to the nearest list/container so Address/Website/Email
        # below are read from this same block, not the whole page.
        contact_container = tel.find_parent(["ul", "ol", "div"])
        if contact_container:
            scope = contact_container

    # Address (Google Maps link's visible text holds the full address)
    maps_link = scope.select_one('a[href*="maps.google.com"]')
    if maps_link:
        address_text = clean(maps_link.get_text())
        normalized = re.sub(r"\s*-\s*(\d)", r" \1", address_text)
        street, city, state, zipcode = _split_blinx_address(normalized)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # Email (Cloudflare-obfuscated, scoped to the contact block so the
    # footer's separate "Contact Us" email is never picked up instead)
    email = _find_cf_email(scope)
    if email:
        business["Business Email"] = email

    # Website (whichever external link is left once maps/tel/email are excluded)
    for a in scope.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "freelistingusa.com" in href.lower():
            continue
        if "maps.google.com" in href.lower() or "google.com/maps" in href.lower():
            continue
        if "cdn-cgi/l/email-protection" in href.lower():
            continue
        business["Website URL"] = href
        break

    # ---- Category ("Listed In :" link -- same URL as the breadcrumb) ----
    category_links = soup.select('a[href*="/listings/category/"]')
    categories = []
    for a in category_links:
        text = clean(a.get_text())
        if text and text not in categories:
            categories.append(text)
    if categories:
        business["Category"] = ", ".join(categories)

    # ---- Description ("Business Description" heading) ----
    description = _value_by_label(soup, "Business Description")
    if is_meaningful(description):
        business["Description"] = description

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords ("Services" + "Tags :" tag links, both /listings/tag/) ----
    tag_links = soup.select('a[href*="/listings/tag/"]')
    tags = []
    for a in tag_links:
        text = clean(a.get_text())
        if text and text not in tags:
            tags.append(text)
    if tags:
        business["Keywords"] = ", ".join(tags)

    # ---- Business Hours (dedicated hours-grid block, one <p> per day) ----
    hours_grid = soup.select_one("div.business-hours-listing div.hours-grid")
    if hours_grid:
        day_entries = [clean(p.get_text()) for p in hours_grid.find_all("p")]
        day_entries = [d for d in day_entries if d]
        if day_entries:
            business["Hours"] = "; ".join(day_entries)

    # ---- Logo / Photos (S3-hosted listing photo, full-size via its
    #      wrapping anchor rather than the smaller "_thumb" <img> src) ----
    photo_link = soup.select_one('a[href*="freelistingusa.s3"]')
    if photo_link and photo_link.get("href"):
        business["Logo"] = photo_link["href"]
    else:
        photo_img = soup.select_one('img[src*="freelistingusa.s3"]')
        if photo_img and photo_img.get("src"):
            business["Logo"] = urljoin(url, photo_img["src"])

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Social Media (dedicated #listing-follow block --
    follow_block = soup.select_one("#listing-follow")
    if follow_block:
        for a in follow_block.find_all("a", href=True):
            href = a["href"]
            for domain, network in SOCIAL_DOMAINS.items():
                if domain in href.lower():
                    business["Social Media Links"][network] = href

    return business



