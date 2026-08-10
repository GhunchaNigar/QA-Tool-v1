"""
Site parser: linkcentre.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_linkcentre(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Address ----
    meta_map = {
        "business:contact_data:street_address": "Street",
        "business:contact_data:locality": "City",
        "business:contact_data:postal_code": "Zipcode",
        "business:contact_data:country_name": "Country",
        "business:contact_data:phone_number": "Phone",
    }
    for prop, field in meta_map.items():
        tag = soup.find("meta", property=prop)
        if tag and tag.get("content"):
            business[field] = clean(tag["content"])

    # ---- Business Name ----
    h1 = soup.select_one("h1.v2-hero-name")
    if h1:
        business["Business Name"] = clean(h1.get_text())

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
            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
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

    # ---- Website URL fallback  ----
    if not business["Website URL"]:
        listing_url = soup.select_one("a.v2-listing-url[href]")
        if listing_url:
            business["Website URL"] = listing_url["href"]

    # ---- Description fallback (meta description) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
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
    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").split("?")[0].strip()
    if not business["Business Email"]:
        business["Business Email"] = _find_cf_email(soup)

    return business


