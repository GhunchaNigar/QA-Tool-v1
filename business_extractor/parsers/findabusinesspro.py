"""
Site parser: findabusinesspro.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def _findabusinesspro_jsonld_local_business(soup):
    """Return the LocalBusiness object from the page's JSON-LD (handles
    both a plain object/list and an @graph-wrapped block)."""
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


def parse_findabusinesspro(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    ld_business = _findabusinesspro_jsonld_local_business(soup) or {}
    page_domain = urlparse(url).netloc.lower().replace("www.", "")

    # ---- Business Name ----
    if ld_business.get("name"):
        business["Business Name"] = clean(ld_business["name"])

    if not business["Business Name"]:
        h1 = soup.select_one("h1.bold.inline-block")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        company = soup.select_one(".table-display-company .textbox-company")
        if company:
            business["Business Name"] = clean(company.get_text())

    # ---- About block (source of Website URL, Phone, and Description) ----
    about = soup.select_one(".textarea.textarea-about_me")
    about_paragraphs = [clean(p.get_text()) for p in about.find_all("p")] if about else []

    # ---- Website URL: first external, non-directory http(s) link inside
    # the About block ----
    if about:
        for anchor in about.select("a[href]"):
            href = anchor["href"].strip()
            if not href.lower().startswith(("http://", "https://")):
                continue
            if _hostname_matches_social_domain(href, page_domain):
                continue
            business["Website URL"] = href
            break

    # ---- Phone ----
    for i, para_text in enumerate(about_paragraphs):
        # Label paragraph is sometimes "Phone" and sometimes "Phone:" --
        # this template isn't consistent about the trailing colon, so
        # strip it before comparing instead of requiring it.
        if para_text.strip().lower().rstrip(":") == "phone" and i + 1 < len(about_paragraphs):
            candidate = about_paragraphs[i + 1]
            if is_meaningful(candidate):
                business["Phone"] = candidate
            break

    # ---- Description (About block, with the "Phone:"/"Website:" label
    # lines and their values stripped back out since those are captured
    # separately) ----
    if about:
        desc_text = clean_multiline(about.get_text(separator="\n"))
        lines = [
            line for line in desc_text.split("\n")
            # Label lines appear with or without a trailing colon
            # ("Phone" / "Phone:") depending on the listing, so strip it
            # before comparing. "About us" is a label too, not content.
            if line.strip().lower().rstrip(":") not in ("phone", "website", "about us")
            and line.strip() != business["Phone"]
            and line.strip() != business["Website URL"]
        ]
        desc_text = "\n".join(lines).strip()
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"] and ld_business.get("description"):
        desc_text = clean(ld_business["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Address (split across individual <span> elements: street, city,
    # state, zip, with a trailing plain-text country after the final <br>) ----
    addr_container = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_container:
        addr_spans = addr_container.find_all("span", recursive=False)
        span_texts = [clean(s.get_text()) for s in addr_spans]
        span_texts = [t for t in span_texts if t]
        if len(span_texts) >= 4:
            business["Street"] = span_texts[0]
            business["City"] = span_texts[1]
            business["State"] = span_texts[2]
            business["Zipcode"] = span_texts[3]
        elif len(span_texts) == 3:
            # Template omits the Street span entirely when a member hasn't
            # entered one (rather than rendering it empty), so 3 spans here
            # means City, State, Zip with no street -- not the first 3 of
            # a 4-part street/city/state/zip layout.
            business["City"] = span_texts[0]
            business["State"] = span_texts[1]
            business["Zipcode"] = span_texts[2]
        elif len(span_texts) == 2:
            business["City"] = span_texts[0]
            business["State"] = span_texts[1]
        elif len(span_texts) == 1:
            business["City"] = span_texts[0]
        elif not business["Street"]:
            # No spans at all -- fall back to storing the raw container
            # text as Street rather than dropping the address entirely.
            addr_text = clean(addr_container.get_text())
            if is_meaningful(addr_text):
                business["Street"] = addr_text

        # Country: trailing plain-text node directly under the container
        # (after the final <br>), not inside any of the address spans.
        trailing_text_nodes = [
            clean(node) for node in addr_container.contents
            if isinstance(node, NavigableString) and clean(node) and clean(node) != ","
        ]
        if trailing_text_nodes:
            country_text = trailing_text_nodes[-1]
            if country_text:
                business["Country"] = country_text

    # ---- Country fallback (JSON-LD; this template's page text sometimes
    # spells the country out in full ("United States") where JSON-LD gives
    # the ISO short form -- only used when the page itself had nothing) ----
    if not business["Country"]:
        addr_obj = ld_business.get("address")
        if isinstance(addr_obj, dict):
            country = clean(addr_obj.get("addressCountry", ""))
            if country and country.upper() != "N/A":
                business["Country"] = country

    # ---- Category ----
    category_span = soup.select_one(".profile-header-top-category")
    if category_span:
        cat_text = clean(category_span.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    if not business["Category"]:
        crumbs = [clean(li.get_text()) for li in soup.select("ol.breadcrumb li")]
        crumbs = [c for c in crumbs if c and c.lower() != "home"]
        if len(crumbs) >= 2:
            business["Category"] = crumbs[-2]

    # ---- Logo ----
    logo_img = soup.select_one(".profile-image img[src]")
    if logo_img:
        business["Logo"] = urljoin(url, logo_img["src"])

    if not business["Logo"] and ld_business.get("image"):
        image = ld_business["image"]
        image_url = image.get("url") if isinstance(image, dict) else image
        if image_url:
            business["Logo"] = urljoin(url, image_url)

    # ---- Business Email (opportunistic; not every listing publishes one) ----
    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email

    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto and mailto.get("href"):
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- GBP Link (scoped to the "Get Directions" anchor and JSON-LD
    # location.hasMap, NOT a page-wide scan -- the footer on this template
    # carries the directory's own unrelated social/map links) ----
    directions = soup.select_one("a.get-directions-link[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    if not business["GBP Link"]:
        location = ld_business.get("location")
        if isinstance(location, dict) and location.get("hasMap"):
            business["GBP Link"] = location["hasMap"]

    return business


