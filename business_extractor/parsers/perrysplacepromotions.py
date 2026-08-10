
from ..common import (
    json,
    BeautifulSoup,
    clean,
    clean_multiline,
    empty_business,
    urljoin,
    SOCIAL_DOMAINS,
    _hostname_matches_social_domain,
    _is_maps_link,
    _split_blinx_address,
)


def _find_local_business_jsonld(soup):
    """Return the first LocalBusiness dict found in any application/ld+json
    block on the page (handles both bare-object and @graph-wrapped forms)."""
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue

        candidates = []
        if isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                candidates.extend(graph)
            else:
                candidates.append(data)
        elif isinstance(data, list):
            candidates.extend(data)

        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "LocalBusiness":
                return item
    return None


def parse_perrysplacepromotions(url, html):
    soup = BeautifulSoup(html, "html.parser")
    business = empty_business()

    # ---- Structured data (most reliable) --------------------------------
    local_business = _find_local_business_jsonld(soup)

    if local_business:
        business["Business Name"] = clean(local_business.get("name", ""))
        business["Description"] = clean(local_business.get("description", ""))
        # NOTE: local_business["telephone"] is used only as a last-resort
        # fallback below -- on this template it's sometimes missing the
        # separator between area code and number (e.g. "941379-8669"
        # instead of "(941) 379-8669"), while the on-page phone widget is
        # reliably formatted, so that's tried first.

        # sameAs on this template is just the business's real external
        # website (the directory's own listing URL is exposed separately
        # via "url", not folded into sameAs) -- but guard against that
        # ever changing by skipping any self-referential link.
        for link in local_business.get("sameAs", []) or []:
            if isinstance(link, str) and "perrysplacepromotions.org" not in link.lower():
                business["Website URL"] = link.strip()
                break

        address = local_business.get("address", {}) or {}
        street_addr = clean(address.get("streetAddress", ""))
        if street_addr:
            street, city, state, zipcode = _split_blinx_address(street_addr)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

        country = clean(address.get("addressCountry", ""))
        if country and country != "N/A":
            business["Country"] = country

        image = local_business.get("image")
        logo_url = ""
        if isinstance(image, dict):
            logo_url = image.get("url", "")
        elif isinstance(image, str):
            logo_url = image
        if logo_url:
            business["Logo"] = urljoin(url, logo_url)

    # ---- HTML fallbacks for anything JSON-LD didn't have -----------------
    if not business["Business Name"]:
        name_tag = soup.select_one(".header-member-name h1")
        if name_tag:
            business["Business Name"] = clean(name_tag.get_text())

    phone_tag = soup.select_one(".author-phone") or soup.select_one(
        ".table-display-phone .col-sm-8"
    )
    if phone_tag:
        business["Phone"] = clean(phone_tag.get_text())
    elif not business["Phone"] and local_business:
        business["Phone"] = clean(local_business.get("telephone", ""))

    if not business["Website URL"]:
        website_tag = soup.select_one("a.weblink")
        if website_tag and website_tag.get("href"):
            business["Website URL"] = website_tag["href"].strip()

    if not business["Description"]:
        desc_tag = soup.select_one(".textarea-about_me")
        if desc_tag:
            business["Description"] = (
                clean_multiline(str(desc_tag)) if desc_tag.find("br") else clean(desc_tag.get_text())
            )

    if not (business["Street"] or business["City"] or business["State"] or business["Zipcode"]):
        addr_tag = soup.select_one(".overview-tab-the-member-address .col-sm-8 span") or (
            soup.select_one(".overview-tab-the-member-address .col-sm-8")
        )
        if addr_tag:
            street, city, state, zipcode = _split_blinx_address(clean(addr_tag.get_text()))
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    if not business["Logo"]:
        logo_tag = soup.select_one(".profile-image img")
        if logo_tag and logo_tag.get("src"):
            business["Logo"] = urljoin(url, logo_tag["src"])
        else:
            og_image = soup.find("meta", property="og:image")
            if og_image and og_image.get("content"):
                business["Logo"] = urljoin(url, og_image["content"])

    # ---- Fields never present in the JSON-LD block ------------------------
    category_tag = soup.select_one(".profile-header-top-category")
    if category_tag:
        business["Category"] = clean(category_tag.get_text())

    hours_tag = soup.select_one(".table-display-hours .col-sm-8")
    if hours_tag:
        business["Hours"] = (
            clean_multiline(str(hours_tag)) if hours_tag.find("br") else clean(hours_tag.get_text())
        )

    # Social Media Links / GBP Link: scope the search to the business's own
    # profile tab (#div1) so we never pick up the directory site's own
    # sitewide footer social icons or the Facebook/LinkedIn/X *share*
    # buttons (those use onclick handlers, not hrefs, so they wouldn't
    # match anyway, but scoping keeps this correct if that ever changes).
    profile_content = soup.select_one("#div1") or soup

    social_links = {}
    gbp_link = ""
    for link in profile_content.select("a[href]"):
        href = link["href"].strip()
        if not href or href.startswith("#"):
            continue
        if not gbp_link and _is_maps_link(href):
            gbp_link = href
            continue
        for domain_key, label in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain_key):
                social_links[label] = href
                break

    if social_links:
        business["Social Media Links"] = social_links
    if gbp_link:
        business["GBP Link"] = gbp_link

    return business