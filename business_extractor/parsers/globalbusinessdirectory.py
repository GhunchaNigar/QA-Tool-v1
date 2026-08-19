"""
Site parser: globalbusinessdirectory.us (cities.globalbusinessdirectory.us)

NOTE: this rewrite targets the "My Listing" / Elementor "case27" WordPress
theme actually served on this subdomain (h1.case27-primary-text,
.block-type-* content blocks, .map-block-address, etc). The previous
version of this file was written against a different WP Job Manager
theme variant (h1.entry-title[itemprop="name"], a.google_map_link,
article.job_listing.job_listing_region-*, .listing-category,
.listing-logo, ...) whose selectors don't exist anywhere on this markup,
so almost every field either silently came back empty or was picked up
by an unrelated fallback elsewhere in the pipeline -- which is how the
page's tagline ("personal injury lawyer") ended up misassigned to
Owner Name, and why Street/City never got split (no element matched
a.google_map_link, so the raw address text was never routed through
_split_blinx_address at all).
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


def parse_globalbusinessdirectory(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- JSON-LD (LocalBusiness) -- used only as a light-touch fallback
    #      below. Its "url" field is this directory's own listing page,
    #      NOT the business's external site, so it's deliberately never
    #      used for Website URL. ----
    jsonld = {}
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "LocalBusiness":
            jsonld = data
            break

    # ---- Business Name ----
    name_tag = soup.select_one("h1.case27-primary-text")
    if name_tag:
        business["Business Name"] = clean(name_tag.get_text())
    if not business["Business Name"] and jsonld.get("name"):
        business["Business Name"] = clean(jsonld["name"])

    # ---- Owner Name (the listing's submitting/author account, shown in
    #      the "Author" content block -- the page has no other concept
    #      of an owner's name) ----
    host_name = soup.select_one(".block-type-author .host-name")
    if host_name:
        text = clean(host_name.get_text())
        if is_meaningful(text):
            business["Owner Name"] = text

    # ---- Address (the "Location" block's plain address line, e.g.
    #      "2244 Faraday Ave #206 Carlsbad, CA 92008") ----
    addr_tag = soup.select_one(".map-block-address p")
    addr_text = clean(addr_tag.get_text()) if addr_tag else ""
    if not addr_text:
        addr_obj = jsonld.get("address")
        if isinstance(addr_obj, dict) and addr_obj.get("address"):
            addr_text = clean(addr_obj["address"])

    if addr_text:
        street, city, state, zipcode = _split_blinx_address(addr_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Country (the "Region" content block links to
    #      /region/<country-slug>/ with the display name as its text) ----
    region_link = soup.select_one('a[href*="/region/"] span')
    if region_link:
        country_text = clean(region_link.get_text())
        if is_meaningful(country_text):
            business["Country"] = country_text

    # ---- Phone / Business Email / Website URL (all three live together
    #      in the "Contact Information" block, one <li> per icon type) ----
    for li in soup.select(".block-type-details .pf-body li"):
        icon = li.find("i")
        span = li.find("span")
        if not icon or not span:
            continue
        icon_classes = icon.get("class", [])
        value = clean(span.get_text())
        if not value:
            continue

        if "mi-phone" in icon_classes or "phone" in icon_classes:
            business["Phone"] = value
        elif "mi-email" in icon_classes or "email" in icon_classes:
            business["Business Email"] = value
        elif "mi-web" in icon_classes or "web" in icon_classes:
            business["Website URL"] = value

    if not business["Phone"] and jsonld.get("telephone"):
        business["Phone"] = clean(jsonld["telephone"])
    if not business["Business Email"] and jsonld.get("email"):
        business["Business Email"] = clean(jsonld["email"])

    # ---- Keywords (the short tagline under the business name, e.g.
    #      "personal injury lawyer" -- NOT an owner's name) ----
    tagline = soup.select_one(".listing-tagline-field")
    if tagline:
        kw_text = clean(tagline.get_text())
        if is_meaningful(kw_text):
            business["Keywords"] = kw_text

    # ---- Description (the "Description" content block; it's usually
    #      split across multiple <p> tags that all need to be joined --
    #      grabbing only the first one truncates the listing) ----
    desc_paragraphs = soup.select(".block-type-text .pf-body p")
    if desc_paragraphs:
        desc_text = clean(" ".join(p.get_text(separator=" ") for p in desc_paragraphs))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Category (the "Categories" content block) ----
    cat_links = [clean(c.get_text()) for c in soup.select(".block-type-categories .category-name")]
    cat_links = [c for c in cat_links if c]
    if cat_links:
        business["Category"] = ", ".join(cat_links)

    # ---- Logo ----
    # Deliberately no extraction here. The listing container itself is
    # flagged "listing-no-logo" (<div class="single-job-listing
    # listing-no-logo" id="c27-single-listing">), and this template
    # doesn't emit an og:image meta tag either, so there's no reliable
    # per-business photo to fall back to on this theme.

    # ---- Social Media Links ----
    # Scoped to the single-listing container (#c27-single-listing) only.
    # Scanning the whole page would also pick up two false sources:
    #   1. The footer's own social links, which belong to the directory
    #      site's builder/operator ("Created by Digital Mix"), not the
    #      business.
    #   2. The generic "#social-share-modal" block (outside the listing
    #      container, rendered once per page) with hardcoded share-this-
    #      listing links to facebook.com/share.php, x.com/share,
    #      linkedin.com/shareArticle, etc. -- utility share actions, not
    #      the business's own profiles.
    content_area = soup.select_one("#c27-single-listing") or soup
    for a in content_area.find_all("a", href=True):
        href = a["href"]
        if "globalbusinessdirectory.us" in href.lower():
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business
