"""
Site parser: zumvu.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_zumvu(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- JSON-LD (ProfilePage -> mainEntity) ----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except Exception:
            continue

        entity = data.get("mainEntity") if isinstance(data, dict) else None
        if not isinstance(entity, dict):
            continue
        # Template mislabels businesses as "Person" -- accept either.
        if entity.get("@type") not in ("Person", "Organization", "LocalBusiness"):
            continue

        business["Business Name"] = entity.get("name", business["Business Name"])

        if entity.get("about") and is_meaningful(entity["about"]):
            business["Category"] = clean(entity["about"])

        if entity.get("image"):
            business["Logo"] = urljoin(url, entity["image"])

        if entity.get("description") and is_meaningful(entity["description"]):
            business["Description"] = clean(entity["description"])

        addr = entity.get("address", {})
        if isinstance(addr, dict):
            street = addr.get("streetAddress", "")
            city = addr.get("addressLocality", "")
            state = addr.get("addressRegion", "")
            zipcode = addr.get("postalCode", "")
            country = addr.get("addressCountry", "")

            # Zumvu's JSON-LD template sometimes dumps the *entire*
            # address ("131 Continental Dr, Suite 305, Newark,
            # Delaware 19713") into streetAddress alone, leaving
            # addressLocality/addressRegion/postalCode blank. Detect
            # that and split streetAddress ourselves instead of
            # letting the whole blob land in Street.
            if street and not (city and state and zipcode):
                split_street, split_city, split_state, split_zip = _split_blinx_address(street)
                street = split_street
                city = city or split_city
                state = state or split_state
                zipcode = zipcode or split_zip

            business["Street"] = street or business["Street"]
            business["City"] = city or business["City"]
            business["State"] = state or business["State"]
            business["Zipcode"] = zipcode or business["Zipcode"]
            business["Country"] = country or business["Country"]

        knows_about = entity.get("knowsAbout")
        if knows_about and isinstance(knows_about, list):
            terms = [clean(t) for t in knows_about if clean(t)]
            if terms:
                business["Keywords"] = ", ".join(terms)

        if entity.get("sameAs"):
            links = entity["sameAs"]
            if isinstance(links, list):
                for link in links:
                    for domain, name in SOCIAL_DOMAINS.items():
                        if domain in link.lower():
                            business["Social Media Links"][name] = link

    # ---- Business Name fallback (visible <h1>) ----
    if not business["Business Name"]:
        h1 = soup.select_one(".prottlebx h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Contact block: phone / email / website by icon class ----
    contact_ul = soup.select_one(".contactbox.extncontctbx ul.abtcontact-page")
    if contact_ul:
        for li in contact_ul.find_all("li"):
            icon = li.find("i")
            a = li.find("a", href=True)
            if not icon or not a:
                continue
            icon_classes = icon.get("class", [])

            if "fa-phone" in icon_classes:
                business["Phone"] = a["href"].replace("tel:", "").strip()
            elif "fa-globe" in icon_classes:
                business["Website URL"] = a["href"]

    # ---- Hours (same icon-list block as phone/website, keyed off a clock icon) ----
    if contact_ul:
        for li in contact_ul.find_all("li"):
            icon = li.find("i")
            if not icon:
                continue
            icon_classes = icon.get("class", [])
            if "fa-clock" in icon_classes or "fa-clock-o" in icon_classes:
                text = clean(li.get_text())
                if text:
                    business["Hours"] = text

    # ---- Description (About section -- richer than meta description) ----
    about = soup.select_one(".resabout .addinfo")
    if about:
        text = clean_multiline(about.get_text(separator="\n"))
        if is_meaningful(text):
            business["Description"] = text

    # ---- Meta description fallback (usually empty on this template) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords fallback: meta tag, then the visible tag pills ----
    if not business["Keywords"]:
        meta_kw = soup.find("meta", attrs={"name": "keywords"})
        if meta_kw and is_meaningful(meta_kw.get("content", "")):
            business["Keywords"] = clean(meta_kw["content"])

    if not business["Keywords"]:
        tags = [clean(t.get_text()) for t in soup.select(".taginfoabout .right-tags")]
        tags = [t for t in tags if t]
        if tags:
            business["Keywords"] = ", ".join(tags)

    # ---- Address fallback (visible Location block, if JSON-LD missing) ----
    if not any([business["Street"], business["City"], business["State"]]):
        loc_li = soup.select_one(".locflexfirstcol ul.abtcontact-page li")
        if loc_li:
            addr_text = clean_multiline(loc_li.get_text(separator="\n"))
            lines = [l for l in addr_text.split("\n") if l]
            if lines:
                business["Street"] = lines[0]
            if len(lines) > 1:
                # e.g. "Dover, Delaware 19901, UNITED STATES"
                business["City"] = lines[1]

    # ---- Country fallback (visible map-marker line under the business
    #      name, e.g. "USA" -- JSON-LD address.addressCountry is often
    #      simply absent from this template's mainEntity block) ----
    if not business["Country"]:
        for li in soup.select("ul.profileaddrss li"):
            icon = li.find("i")
            if icon and "fa-map-marker" in icon.get("class", []):
                country_text = clean(li.get_text())
                if is_meaningful(country_text):
                    business["Country"] = country_text
                break

    # ---- Logo fallback (og:image) ----
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Category (folder-icon line under the business name) ----
    if not business["Category"]:
        for li in soup.select("ul.profileaddrss li"):
            icon = li.find("i")
            if icon and "fa-folder-o" in icon.get("class", []):
                cat_text = clean(li.get_text())
                if is_meaningful(cat_text):
                    business["Category"] = cat_text
                break

    # ---- Category (breadcrumb fallback) ----
    if not business["Category"]:
        crumbs = [clean(a.get_text()) for a in soup.select("ul.breadcrumb a, .breadcrumb a")]
        crumbs = [c for c in crumbs if c and c.lower() != "home"]
        if crumbs:
            business["Category"] = crumbs[-1]

    # ---- Social Media (real anchors, in case JSON-LD sameAs was empty)
    #      Scoped to the business's own content column (#reslt /
    #      .proleftcol). Scanning the whole page picks up Zumvu's own
    #      corporate social accounts from the site header's
    #      ".social-home" block (facebook.com/zumvu, twitter.com/zumvu,
    #      pinterest.com/zumvu) and misattributes them to every listing
    #      that has no real sameAs links of its own -- those accounts
    #      don't contain "zumvu.com" in the href, so the existing
    #      "zumvu.com not in href" filter doesn't catch them. ----
    social_scope = soup.select_one("#reslt") or soup.select_one(".proleftcol") or soup
    for a in social_scope.find_all("a", href=True):
        href = a["href"]
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower() and "zumvu.com" not in href.lower():
                business["Social Media Links"][network] = href

    return business
