"""
Site parser: cataloxy.us / cataloxy.net (same markup)

Path in project:  business_extractor/parsers/cataloxy.py

Every field now has several fallbacks (JSON-LD -> microdata -> visible HTML ->
<meta>/<title> -> URL slug), so the record is only empty when the page truly
has no listing on it.
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


_LISTING_LD_TYPES = {
    "localbusiness", "organization", "corporation", "store", "professionalservice",
    "homeandconstructionbusiness", "medicalbusiness", "foodestablishment",
    "automotivebusiness", "financialservice", "legalservice", "realestateagent",
}

# Breadcrumb entries that are navigation, not a category
_NOT_CATEGORY = re.compile(r"^(cataloxy\b|companies\b|directory\b|home$)", re.I)


def _ld_objects(soup):
    """Yield every dict found in every JSON-LD block (handles lists / @graph)."""
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw, strict=False)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop(0)
            if not isinstance(obj, dict):
                continue
            yield obj
            graph = obj.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)


def _is_listing_type(obj):
    t = obj.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(str(x).lower() in _LISTING_LD_TYPES for x in types if x)


def _normalize_site(value):
    value = clean(value)
    if not value:
        return ""
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value.lstrip("/")
    return value


def parse_cataloxy(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- 1. JSON-LD (LocalBusiness / Organization) ----
    ld_description = ""
    for obj in _ld_objects(soup):
        if not _is_listing_type(obj):
            continue

        if obj.get("name") and not business["Business Name"]:
            business["Business Name"] = clean(str(obj["name"]))

        if obj.get("telephone") and not business["Phone"]:
            business["Phone"] = clean(str(obj["telephone"]))

        addr = obj.get("address") or {}
        if isinstance(addr, str):
            addr = {"streetAddress": addr}
        if isinstance(addr, dict):
            for field, key in (
                ("Street", "streetAddress"),
                ("City", "addressLocality"),
                ("State", "addressRegion"),
                ("Zipcode", "postalCode"),
                ("Country", "addressCountry"),
            ):
                val = addr.get(key, "")
                if isinstance(val, dict):
                    val = val.get("name", "")
                if val and not business[field]:
                    business[field] = clean(str(val))

        if obj.get("description") and not ld_description:
            ld_description = clean_multiline(str(obj["description"]))

    # ---- 2. Business Name fallbacks ----
    if not business["Business Name"]:
        h1 = soup.select_one("h1.firms") or soup.select_one("h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"]:
        meta_name = soup.select_one('#company-info-5520488 meta[itemprop="name"], [itemscope] > meta[itemprop="name"], .hidden > meta[itemprop="name"]')
        if meta_name and meta_name.get("content"):
            business["Business Name"] = clean(meta_name["content"])
    if not business["Business Name"]:
        title_src = ""
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title_src = og_title["content"]
        elif soup.title and soup.title.string:
            title_src = soup.title.string
        # "Focal, Newark — phone, address, website | Cataloxy.us"
        title_src = clean(title_src)
        # Only trust the <title> when it is the listing template
        # "<Name>, <City> — phone, address, website | Cataloxy"; a generic
        # or error page title must not become a fake business name.
        if not re.search(r"\s+[—–]\s+phone", title_src, re.I):
            title_src = ""
        name = re.split(r"\s+[—–]\s+|\s+\|\s+", title_src)[0]
        if business["City"] and name.endswith(", " + business["City"]):
            name = name[: -len(business["City"]) - 2]
        elif re.search(r"\s+[—–]\s+phone", title_src, re.I) and "," in name:
            name = name.rsplit(",", 1)[0]  # drop the trailing ", <City>"
        business["Business Name"] = clean(name)

    # ---- 3. Address microdata (visible block -- has the zip code) ----
    addr_block = soup.select_one('#firm_contacts span[itemprop="address"]') \
        or soup.select_one('span[itemprop="address"]')
    if addr_block:
        for field, prop in (
            ("Street", "streetAddress"),
            ("Zipcode", "postalCode"),
            ("City", "addressLocality"),
            ("State", "addressRegion"),
        ):
            el = addr_block.select_one(f'[itemprop="{prop}"]')
            if el:
                val = clean(el.get_text())
                if val:
                    business[field] = val
        country = addr_block.select_one('[itemprop="addressCountry"]')
        if country:
            business["Country"] = country.get("content") or clean(country.get_text()) or business["Country"]

    # ---- 4. Phone fallbacks ----
    if not business["Phone"]:
        tel = (
            soup.select_one('#firm_contacts a[href^="tel:"]')
            or soup.select_one('a.telnum[href^="tel:"]')
            or soup.select_one('[itemprop="telephone"][href^="tel:"]')
            or soup.select_one('a[href^="tel:"]')
        )
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()
    if not business["Phone"]:
        tel_el = soup.select_one('#firm_contacts [itemprop="telephone"], #phonelisttab a.telnum')
        if tel_el:
            business["Phone"] = clean(tel_el.get_text())

    # ---- 5. Website URL ----
    site_link = (
        soup.select_one("#site_link_copy a.firmDomain")
        or soup.select_one("#firm_contacts a.firmDomain")
        or soup.select_one("a.firmDomain")
    )
    if site_link:
        site = site_link.get("title") or site_link.get_text()
        business["Website URL"] = _normalize_site(site)
    if not business["Website URL"] and business["Business Name"]:
        # Cataloxy listing slugs are the company domain: /firms/www.tryfocal.com.htm
        m = re.search(r"/firms/([a-z0-9][a-z0-9.\-]*\.[a-z]{2,})\.htm", urlparse(url).path, re.I)
        if m and not m.group(1).lower().startswith(("kw", "rubrics")):
            business["Website URL"] = "https://" + m.group(1)

    # ---- 6. Business Email ----
    email = soup.select_one('#firm_contacts a[href^="mailto:"]') or soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- 7. Description ----
    desc_el = soup.select_one('#full-descr [itemprop="description"]') \
        or soup.select_one('p[itemprop="description"]') \
        or soup.select_one('[itemprop="description"]')
    if desc_el:
        desc = clean_multiline(desc_el.decode_contents())
        # decode_contents() leaves tags such as <br/> handled, strip any others
        desc = clean_multiline(BeautifulSoup(desc, "lxml").get_text("\n"))
        if is_meaningful(desc):
            business["Description"] = desc
    if not business["Description"] and is_meaningful(ld_description):
        business["Description"] = ld_description
    if not business["Description"]:
        for sel in ({"name": "description"}, {"property": "og:description"}):
            meta_desc = soup.find("meta", attrs=sel)
            if meta_desc:
                desc = clean(meta_desc.get("content", ""))
                # Cataloxy's generic template text is not a real description
                if is_meaningful(desc) and "Company information and services" not in desc:
                    business["Description"] = desc
                    break

    # ---- 8. Keywords ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            business["Keywords"] = clean(kw_raw)
    if not business["Keywords"]:
        scope = soup.select_one("main") or soup
        kw_links = [clean(a.get_text()) for a in scope.select('a[href*="/firms/kw/"]')]
        kw_links = [k for k in kw_links if k]
        if kw_links:
            business["Keywords"] = ", ".join(dict.fromkeys(kw_links))

    # ---- 9. Category (last breadcrumb that is a real category) ----
    crumb_names = [
        clean(span.get_text())
        for span in soup.select('#top_navigator span[itemprop="name"]')
    ]
    crumb_names = [c for c in crumb_names if c and not _NOT_CATEGORY.match(c)]
    if crumb_names:
        business["Category"] = crumb_names[-1]

    # ---- 10. Logo ----
    logo_el = soup.select_one('span[itemprop="logo"]')
    if logo_el and is_meaningful(logo_el.get_text()):
        business["Logo"] = urljoin(url, clean(logo_el.get_text()))
    if not business["Logo"]:
        for img in (
            soup.select_one(".firm-top-panel__logo img[src]"),
            soup.select_one("#company-info-5520488 img.logo[src]"),
            soup.select_one("img.logo[src]"),
        ):
            if img:
                business["Logo"] = urljoin(url, img["src"])
                break
    if not business["Logo"]:
        for obj in _ld_objects(soup):
            if _is_listing_type(obj) and isinstance(obj.get("image"), str):
                business["Logo"] = urljoin(url, obj["image"])
                break
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- 11. Social links (only inside the company block, not the site footer) ----
    social_scope = soup.select_one("main.firm-page-main") or soup.select_one("#firm_contacts") or soup
    for a in social_scope.find_all("a", href=True):
        href = a["href"]
        if "js-native-share" in (a.get("class") or []):
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"].setdefault(network, href)

    # ---- 12. Zipcode fallback ----
    # Some listings (seen on cataloxy.net) have no postalCode in the address
    # markup, but the description ends with the full address, e.g.
    # "..., 12 Rickard Road, Carramar, NSW 2163." Only accept a code that
    # directly follows "<City>, <STATE ABBR>" so a stray number can't match.
    if not business["Zipcode"] and business["City"]:
        text_for_zip = " ".join(
            [business["Description"]]
            + [clean(m.get("content", "")) for m in soup.find_all("meta", attrs={"name": "description"})]
        )
        zip_match = re.search(
            re.escape(business["City"]) + r",\s*[A-Z]{2,3}\s+(\d{4,5}(?:-\d{4})?)\b",
            text_for_zip,
        )
        if zip_match:
            business["Zipcode"] = zip_match.group(1)

    return business