"""
Site parser: linkcentre.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_linkcentre(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Address / contact meta tags ----
    # NOTE: this template does NOT always emit separate locality/region/
    # postal_code meta tags -- on plenty of listings (confirmed on
    # markvigildombeckphd) only street_address, country_name, and
    # phone_number are present, with the ENTIRE address ("1402 Park St,
    # Ste G, Alameda, CA 94501") dumped into street_address alone. City/
    # State/Zipcode are handled separately below instead of through this
    # map, so a combined blob doesn't silently leave them blank.
    meta_map = {
        "business:contact_data:street_address": "Street",
        "business:contact_data:locality": "City",
        "business:contact_data:region": "State",
        "business:contact_data:postal_code": "Zipcode",
        "business:contact_data:country_name": "Country",
        "business:contact_data:phone_number": "Phone",
        "business:contact_data:website": "Website URL",
    }
    for prop, field in meta_map.items():
        tag = soup.find("meta", property=prop)
        if tag and tag.get("content"):
            business[field] = clean(tag["content"])

    # If City/State/Zipcode never came from their own meta tags, the
    # street_address meta tag likely holds the whole combined address
    # (see note above) -- split it the same way _split_blinx_address
    # handles other sites that dump one comma-joined blob into a single
    # field.
    if business["Street"] and not (business["City"] and business["State"] and business["Zipcode"]):
        split_street, split_city, split_state, split_zip = _split_blinx_address(business["Street"])
        business["Street"] = split_street or business["Street"]
        business["City"] = business["City"] or split_city
        business["State"] = business["State"] or split_state
        business["Zipcode"] = business["Zipcode"] or split_zip

    # ---- Business Name ----
    h1 = soup.select_one("h1.v2-hero-name")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Owner / contact person name (Contact card, user-icon row) ----
    for item in soup.select(".v2-contact-grid .v2-contact-item"):
        icon = item.select_one(".v2-contact-icon i")
        value = item.select_one(".v2-contact-value")
        if icon and value and "fa-user" in icon.get("class", []):
            owner = clean(value.get_text())
            if is_meaningful(owner):
                business["Owner Name"] = owner
            break

    # ---- JSON-LD ----
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue

        graph = data.get("@graph") if isinstance(data, dict) else None
        objects = graph if graph else (data if isinstance(data, list) else [data])

        for obj in objects:
            if not isinstance(obj, dict) or obj.get("@type") not in ("LocalBusiness", "Organization", "Person"):
                continue

            # This template's @graph reuses "Organization" for BOTH the
            # site-wide LinkCentre entity and the per-listing business
            # entity. They're distinguished by the business entity being
            # "isPartOf" the site's WebSite node -- the site-wide entity
            # isn't part of anything. Without this check, the site-wide
            # LinkCentre org (wrong name, wrong sameAs links, wrong
            # everything for this listing) would either overwrite or race
            # against the real business entity depending on @graph order.
            if "isPartOf" not in obj:
                continue

            if not business["Business Name"]:
                business["Business Name"] = obj.get("name", "")

            addr = obj.get("address", {}) or {}
            if not business["Street"]:
                business["Street"] = addr.get("streetAddress", "")
            if not business["City"]:
                business["City"] = addr.get("addressLocality", "")
            if not business["State"]:
                business["State"] = addr.get("addressRegion", "")
            if not business["Zipcode"]:
                business["Zipcode"] = addr.get("postalCode", "")

            if not business["Phone"]:
                business["Phone"] = obj.get("telephone", "")

            same_as = obj.get("sameAs") or []
            for link in same_as:
                matched_social = False
                for domain, network in SOCIAL_DOMAINS.items():
                    if domain in link.lower():
                        business["Social Media Links"][network] = link
                        matched_social = True
                        break
                if not matched_social and not business["Website URL"]:
                    business["Website URL"] = link

            if obj.get("description"):
                business["Description"] = clean(obj["description"])

            logo_obj = obj.get("logo") or obj.get("image")
            if isinstance(logo_obj, dict) and logo_obj.get("url"):
                business["Logo"] = urljoin(url, logo_obj["url"])
            elif isinstance(logo_obj, str):
                business["Logo"] = urljoin(url, logo_obj)

            knows_about = obj.get("knowsAbout") or []
            if knows_about:
                business["Category"] = ", ".join(knows_about)

    # ---- Website URL fallback (listing card, when present) ----
    if not business["Website URL"]:
        listing_url = soup.select_one("a.v2-listing-url[href]")
        if listing_url:
            business["Website URL"] = listing_url["href"]

    # ---- Website URL fallback (visible Contact card link) ----
    if not business["Website URL"]:
        for item in soup.select(".v2-contact-grid .v2-contact-item"):
            icon = item.select_one(".v2-contact-icon i")
            link = item.select_one(".v2-contact-value a[href]")
            if icon and link and "fa-globe" in icon.get("class", []):
                business["Website URL"] = link["href"]
                break

    # ---- Description fallback (meta description) ----
    # LinkCentre auto-generates the meta description tag for every
    # listing that has no real bio/about text filled in, using a fixed
    # template: "{name}. [phone icon] {phone}. Get directions, read
    # reviews & contact details." -- confirmed verbatim on
    # markvigildombeckphd ("Mark Vigil Dombeck, PhD. (510) 900-5123.
    # Get directions, read reviews & contact details."). That's just
    # restating fields already extracted separately (Name, Phone), not
    # a real description, so using it as Description reports boilerplate
    # as if the business had written something -- worse than leaving
    # the field empty. Detect that fixed tail phrase and skip it; any
    # OTHER meta description (a listing that actually has one) still
    # comes through normally.
    _LC_GENERIC_META_DESC_RE = re.compile(
        r"get directions,\s*read reviews\s*&\s*contact details\.?\s*$", re.I
    )
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc) and not _LC_GENERIC_META_DESC_RE.search(desc):
                business["Description"] = desc

    # ---- Category fallback  ----
    if not business["Category"]:
        cat_links = [clean(a.get_text()) for a in soup.select("div.v2-cat-pills a.v2-cat-pill")]
        cat_links = [c for c in cat_links if c]
        if cat_links:
            business["Category"] = ", ".join(cat_links)

    # ---- Logo fallback (og:image) ----
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Business Email ----
    # IMPORTANT: scoped to the Contact card only. The page-wide "Share
    # via Email" button (.v2-share-email) is ALSO a Cloudflare-obfuscated
    # /cdn-cgi/l/email-protection# link, but it's really an empty
    # mailto:?subject=...&body=... share link with no real address in it
    # -- confirmed on markvigildombeckphd, where a page-wide mailto/
    # cf-email search decoded that share button's hex and returned its
    # subject/body query string as the "Business Email". Restricting the
    # search to .v2-contact-grid excludes the share strip entirely, so a
    # listing with no real email correctly comes back empty instead of
    # returning that garbage string.
    contact_grid = soup.select_one(".v2-contact-grid")
    if contact_grid:
        email = contact_grid.select_one('a[href^="mailto:"]')
        if email:
            business["Business Email"] = email["href"].replace("mailto:", "").split("?")[0].strip()
        if not business["Business Email"]:
            business["Business Email"] = _find_cf_email(contact_grid)

    return business
