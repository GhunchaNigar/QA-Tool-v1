"""
Site parser: touchafro.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_touchafro(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    name_el = soup.select_one(".reportHeading h3")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Labeled customer_info rows, keyed by their own label text
    # (row div classes repeat across unrelated rows, so they aren't a
    # reliable way to tell rows apart) ----
    info = {}
    for row in soup.select(".customer_info > div"):
        label_el = row.find(class_="headings_extra")
        if not label_el:
            continue
        label = clean(label_el.get_text()).rstrip(":").strip().lower()
        parts = []
        for sib in label_el.next_siblings:
            if isinstance(sib, NavigableString):
                parts.append(str(sib))
            else:
                parts.append(sib.get_text())
        info[label] = clean(" ".join(parts))

    # ---- Address ----
    address = info.get("address", "")
    if address:
        addr_parts = [clean(p) for p in address.split(",")]
        state_zip_match = re.match(r"^(.*\S)\s+(\d{5}(?:-\d{4})?)$", addr_parts[-1]) if addr_parts else None
        if state_zip_match:
            # Works whether or not a Street segment precedes State+Zip --
            # when addr_parts has only this one element (no comma at all,
            # e.g. "TX 75023" with no street on file), addr_parts[:-1] is
            # simply empty and Street correctly comes out blank.
            business["State"] = state_zip_match.group(1)
            business["Zipcode"] = state_zip_match.group(2)
            business["Street"] = ", ".join(addr_parts[:-1])
        else:
            business["Street"] = address

    if info.get("city"):
        business["City"] = info["city"]
    if info.get("country"):
        business["Country"] = info["country"]
    if info.get("phone"):
        business["Phone"] = info["phone"]
    if info.get("website"):
        business["Website URL"] = info["website"]
    if info.get("email"):
        business["Business Email"] = info["email"]

    # ---- Description  ----
    desc_el = soup.select_one(".description")
    if desc_el:
        desc_paragraphs = [
            clean(p.get_text()) for p in desc_el.find_all("p") if clean(p.get_text())
        ]
        if desc_paragraphs:
            business["Description"] = "\n".join(desc_paragraphs)

    # ---- Category ----
    category_el = soup.select_one(".category_meta a")
    if category_el:
        cat_text = clean(category_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Logo (first gallery-slider image) ----
    logo_el = soup.select_one(".left_thumb.gall-img img[src]") \
        or soup.select_one(".fagsfacf-gallery-slide-inner img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Social Media Links (the business's own "You can also find us
    # on" list -- NOT the footer's or share-widget's TouchAfro-owned
    # links) ----
    social_list = soup.select_one(".follow_social .social_link_btns")
    if social_list:
        for a in social_list.find_all("a", href=True):
            href = a["href"]
            for domain, network in SOCIAL_DOMAINS.items():
                if _hostname_matches_social_domain(href, domain):
                    business["Social Media Links"][network] = href

    return business


