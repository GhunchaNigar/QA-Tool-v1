"""
Site parser: trustburn.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_trustburn(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    name_el = soup.select_one(".tb-company-header__name")
    if name_el:
        name_text = clean(name_el.get_text())
        if is_meaningful(name_text):
            business["Business Name"] = name_text

    # ---- About card: Description / Website / Phone / Address ----
    # Field labels live in <dt> with their values in the sibling <dd>
    # (Website/Phone are links, Address is plain "Street, City, State Zip"
    # text) -- reuse _split_blinx_address for the combined address line.
    about_card = soup.select_one(".company-about")
    if about_card:
        lead = about_card.select_one(".company-about__lead")
        if lead:
            desc_text = clean(lead.get_text())
            if is_meaningful(desc_text):
                business["Description"] = desc_text

        for row in about_card.select(".company-about__row"):
            dt = row.find("dt")
            dd = row.find("dd")
            if not dt or not dd:
                continue
            label = clean(dt.get_text()).lower()

            if "website" in label:
                link = dd.find("a", href=True)
                if link:
                    business["Website URL"] = urljoin(url, link["href"].strip())
                else:
                    text = clean(dd.get_text())
                    if is_meaningful(text):
                        business["Website URL"] = text
            elif "phone" in label:
                link = dd.find("a", href=True)
                phone_text = clean(link.get_text()) if link else clean(dd.get_text())
                if is_meaningful(phone_text):
                    business["Phone"] = phone_text
            elif "address" in label:
                addr_text = clean(dd.get_text())
                if is_meaningful(addr_text):
                    street, city, state, zipcode = _split_blinx_address(addr_text)
                    business["Street"] = street
                    business["City"] = city
                    business["State"] = state
                    business["Zipcode"] = zipcode

    # ---- Logo ----
    logo_img = soup.select_one(".tb-company-header__photo img[src]")
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


