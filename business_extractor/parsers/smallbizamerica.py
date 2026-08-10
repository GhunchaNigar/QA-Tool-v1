
from ..common import *


def parse_smallbizamerica(url, html):
    business = empty_business()
    soup = BeautifulSoup(html, "html.parser")

    # ---- Name ----
    name_tag = soup.select_one(".header-member-name h1")
    if name_tag:
        business["Business Name"] = clean(name_tag.get_text())

    # ---- Category ----
    category_tag = soup.select_one(".profile-header-top-category")
    if category_tag:
        business["Category"] = clean(category_tag.get_text())

    # ---- Phone ----
    phone_tag = soup.select_one(".table-display-phone .col-sm-8")
    if phone_tag:
        business["Phone"] = clean(phone_tag.get_text())

    # ---- Website URL ----
    website_tag = soup.select_one(".table-display-website a")
    if website_tag:
        href = website_tag.get("href", "")
        business["Website URL"] = clean(href) if href else clean(website_tag.get_text())

    # ---- Address (Street / City / State / Zipcode) ----
    address_tag = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if address_tag:
        address_text = clean(address_tag.get_text())
        street, city, state, zipcode = _split_blinx_address(address_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Country ----
    # Not present in the visible markup -- pulled from the embedded
    # LocalBusiness JSON-LD, the only place it appears on this template.
    country = ""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        graph = data.get("@graph", [data]) if isinstance(data, dict) else data
        if not isinstance(graph, list):
            continue
        for node in graph:
            if not isinstance(node, dict):
                continue
            addr = node.get("address")
            if isinstance(addr, dict) and addr.get("addressCountry"):
                country = clean(addr["addressCountry"])
                break
        if country:
            break
    business["Country"] = country

    # ---- Description ----
    desc_tag = soup.select_one(".field-about_me")
    if desc_tag:
        paragraphs = [clean(p.get_text()) for p in desc_tag.find_all("p")]
        paragraphs = [p for p in paragraphs if is_meaningful(p)]
        business["Description"] = "\n\n".join(paragraphs) if paragraphs else clean(desc_tag.get_text())

    # ---- Hours ----
    # No hours block exists on this template; left as empty_business() default.

    # ---- Social Media Links ----
    # On the sampled page this block is CSS-hidden and not rendered at all,
    # but the selector is kept (matching selfemployedai.com's parser) in
    # case other listings on this domain do render real social links.
    social_links = {}
    for a in soup.select(".list-social-links a"):
        href = a.get("href", "")
        if not href or href.startswith("/") or href.startswith("mailto:"):
            continue
        for domain, label in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                social_links[label] = href
                break
    business["Social Media Links"] = social_links

    # ---- Logo ----
    logo_tag = soup.select_one(".profile-image img")
    if logo_tag and logo_tag.get("src"):
        business["Logo"] = urljoin(url, logo_tag["src"])

    return business