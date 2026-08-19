"""
Site parser: globalbusinessdirectory.us
"""
from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py
_GBD_REGION_CLASS_RE = re.compile(r"job_listing_region-([\w-]+)")
def parse_globalbusinessdirectory(url, html):
    soup = BeautifulSoup(html, "lxml")
    business = empty_business()
    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business
    # ---- Business Name ----
    name_tag = soup.select_one('h1.entry-title[itemprop="name"]')
    if name_tag:
        business["Business Name"] = clean(name_tag.get_text())
    if not business["Business Name"]:
        meta_title = soup.find("meta", itemprop="title")
        if meta_title and meta_title.get("content"):
            business["Business Name"] = clean(meta_title["content"])
    # ---- Address ----
    addr_tag = soup.select_one("a.google_map_link")
    if addr_tag:
        addr_text = clean(addr_tag.get_text())
        if addr_text:
            street, city, state, zipcode = _split_blinx_address(addr_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode
    # ---- Country  ----
    article = soup.select_one("article.job_listing")
    if article:
        region_match = _GBD_REGION_CLASS_RE.search(" ".join(article.get("class", [])))
        if region_match:
            business["Country"] = region_match.group(1).replace("-", " ").title()
    # ---- Phone  ----
    phone_tag = soup.select_one('[itemprop="telephone"]')
    if phone_tag:
        business["Phone"] = clean(phone_tag.get_text())
    # ---- Website URL ----
    site_link = soup.select_one("a.listing--website[href]")
    if site_link:
        business["Website URL"] = site_link["href"]
    # ---- Keywords  ----
    tagline = soup.select_one(".listing-tagline")
    if tagline:
        kw_text = clean(tagline.get_text())
        if is_meaningful(kw_text):
            business["Keywords"] = kw_text
    # ---- Description ----
    desc_tag = soup.select_one("#listing-description .box-inner p")
    if desc_tag:
        desc_text = clean(desc_tag.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text
    # ---- Category  ----
    cat_links = [clean(a.get_text()) for a in soup.select(".listing-category a")]
    cat_links = [c for c in cat_links if c]
    if cat_links:
        business["Category"] = ", ".join(cat_links)
    # ---- Logo  ----
    logo_tag = soup.select_one(".listing-logo img")
    if logo_tag:
        logo_src = logo_tag.get("data-src") or logo_tag.get("src")
        if logo_src and not logo_src.startswith("data:"):
            business["Logo"] = urljoin(url, logo_src)
    # ---- Social Media Links ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "globalbusinessdirectory.us" in href.lower():
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href
    return business
