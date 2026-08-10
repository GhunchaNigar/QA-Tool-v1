"""
Site parser: 911getit.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_911getit(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".header-member-name h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Address (single ".profile-header-location" span holding
    # "Street<br>City, State, Zip<br>Country" -- not discrete spans) ----
    addr_el = soup.select_one(".profile-header-location")
    if addr_el:
        lines = [
            line for line in addr_el.get_text(separator="|", strip=True).split("|")
            if line
        ]

        def _looks_like_city_state_zip(line):
            parts = [clean(p) for p in line.split(",")]
            return len(parts) >= 2 and bool(re.search(r"\d{5}(?:-\d{4})?$", parts[-1]))

        if lines and _looks_like_city_state_zip(lines[0]):
            # Some listings (e.g. this one) render NO street line at all --
            # the first line is already "City, State, Zip" followed only by
            # Country: "Plano, Texas, 75023<br>United States" (2 lines, not
            # 3). Treating lines[0] as Street here (as the else-branch below
            # assumes) leaves City/State/Zip blank and misreads the trailing
            # Country line as City.
            parts = [clean(p) for p in lines[0].split(",")]
            if len(parts) >= 1 and parts[0]:
                business["City"] = parts[0]
            if len(parts) >= 2 and parts[1]:
                business["State"] = parts[1]
            if len(parts) >= 3 and parts[2]:
                business["Zipcode"] = parts[2]
            if len(lines) >= 2 and lines[1]:
                business["Country"] = lines[1]
        else:
            if lines:
                business["Street"] = lines[0]
            if len(lines) >= 2:
                parts = [clean(p) for p in lines[1].split(",")]
                if len(parts) >= 1 and parts[0]:
                    business["City"] = parts[0]
                if len(parts) >= 2 and parts[1]:
                    business["State"] = parts[1]
                if len(parts) >= 3 and parts[2]:
                    business["Zipcode"] = parts[2]
            if len(lines) >= 3 and lines[2]:
                business["Country"] = lines[2]

    # ---- Country fallback (LocalBusiness JSON-LD) ----
    if not business["Country"]:
        for script in soup.find_all("script", type="application/ld+json"):
            if not script.string:
                continue
            try:
                data = json.loads(script.string, strict=False)
            except Exception:
                continue
            graph = data.get("@graph", [data]) if isinstance(data, dict) else data
            if not isinstance(graph, list):
                continue
            for node in graph:
                if not isinstance(node, dict) or node.get("@type") != "LocalBusiness":
                    continue
                country = clean(node.get("address", {}).get("addressCountry", ""))
                if country and country.upper() != "N/A":
                    business["Country"] = country
                break
            if business["Country"]:
                break

    # ---- Phone (click-to-call button, not a dedicated labeled row) ----
    phone_el = soup.select_one(".search_show_phone_txt a[href^='tel:']")
    if phone_el:
        phone_text = clean(phone_el.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text

    # ---- Website URL (icon button, not a dedicated labeled row) ----
    website_el = soup.select_one(".member-search-website a[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()

    # ---- Description ----
    about_el = soup.select_one(".froala-data.field-about_me")
    if about_el:
        desc_paragraphs = [
            clean(p.get_text()) for p in about_el.find_all("p") if clean(p.get_text())
        ]
        if desc_paragraphs:
            business["Description"] = "\n".join(desc_paragraphs)

    # ---- Category (no dedicated field on the page -- read from the
    # breadcrumb item just before the business name) ----
    breadcrumb_items = soup.select("ol.breadcrumb li[itemprop='itemListElement'] span[itemprop='name']")
    if breadcrumb_items:
        cat_text = clean(breadcrumb_items[-1].get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Social Media Links (real anchors, scoped to the profile column
    # so the directory's own sitewide header/footer chrome -- e.g. its
    # Facebook-login button -- doesn't get picked up as the business's) ----
    profile_col = soup.select_one(".col-md-9") or soup
    for a in profile_col.find_all("a", href=True):
        href = a["href"]
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href

    return business


