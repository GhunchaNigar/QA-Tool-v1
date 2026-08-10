"""
Site parser: closelocation.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_closelocation(url, html):
    """closelocation.com business profile pages. Core fields sit in two
    static blocks -- the ".address_box" (address/phone/email/country,
    identified by their fa-* icons rather than position, since a missing
    field just drops its <p>) and the ".card" containing "About Us"
    (owner name / website / description, labelled by <strong> tags).
    The page also emits invalid nested <p><p>...</p></p> markup here,
    but lxml auto-closes the outer tag so every field ends up as a
    sibling <p> we can walk in document order."""

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    if _looks_blocked(html):
        return business

    # ---- Name ----
    name_el = soup.select_one(".title_box h1")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Category (banner shows "<Category> |  ID: ..."  --
    #      category is whatever precedes the "|" separator) ----
    cat_el = soup.select_one(".title_box .text-sm.text-uppercase")
    if cat_el:
        cat_text = clean(clean(cat_el.get_text()).split("|")[0])
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    address_box = soup.select_one(".address_box")

    # ---- Street / City / State / Zipcode ----
    if address_box:
        map_icon = address_box.select_one(".fa-map")
        addr_p = map_icon.find_parent("p") if map_icon else None
        if addr_p:
            addr_text = clean(addr_p.get_text())
            if is_meaningful(addr_text):
                street, city, state, zipcode = _split_address_allow_no_comma(addr_text)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode

    # ---- Phone ----
    if address_box:
        phone_icon = address_box.select_one(".fa-phone")
        phone_p = phone_icon.find_parent("p") if phone_icon else None
        if phone_p:
            phone_text = clean(phone_p.get_text())
            if is_meaningful(phone_text):
                business["Phone"] = phone_text

    # ---- Business Email ----
    if address_box:
        email_icon = address_box.select_one(".fa-envelope")
        email_p = email_icon.find_parent("p") if email_icon else None
        if email_p:
            email_text = clean(email_p.get_text())
            if "@" in email_text:
                business["Business Email"] = email_text

    # ---- Country (line reads "United States,   ,   |   " -- only the
    #      first comma-separated segment is populated) ----
    if address_box:
        country_icon = address_box.select_one(".fa-building-o")
        if country_icon:
            country_text = clean(country_icon.get_text())
            country = clean(country_text.split(",")[0])
            if is_meaningful(country):
                business["Country"] = country

    # ---- About Us card: Owner Name / Website / Description ----
    # This card's field labels are inconsistent across listings --
    # confirmed the "Website:" label is sometimes "URL:" instead
    # (wrightway-emergency-services), and some listings skip the
    # "About Us:" label entirely, starting straight in with the
    # description paragraph before any label at all (haqq-legal-ai).
    # Matching labels by substring (rather than an exact string) and
    # defaulting the very first, still-unlabeled paragraph(s) to the
    # description section handles both without misreading the other
    # recognized labels.
    about_card = None
    for div in soup.select(".col-md-9.card"):
        h4 = div.find("h4")
        if h4 and "about us" in clean(h4.get_text()).lower():
            about_card = div
            break

    if about_card:
        section = "description"  # default: unlabeled leading text is description
        desc_parts = []
        for p in about_card.find_all("p"):
            strong = p.find("strong")
            if strong:
                label = clean(strong.get_text()).rstrip(":").lower()
                if "owner" in label:
                    section = "owner"
                elif "website" in label or "url" in label:
                    section = "website"
                elif "about" in label:
                    section = "description"
                else:
                    # Covers "Related Searches:" and any other label
                    # we don't specifically capture -- stop collecting
                    # into Description rather than risk pulling in
                    # unrelated trailing content (e.g. keyword lists).
                    section = None
                continue

            if section == "owner":
                text = clean(p.get_text())
                if is_meaningful(text):
                    business["Owner Name"] = text
                section = None
            elif section == "website":
                link = p.find("a", href=True)
                if link:
                    business["Website URL"] = urljoin(url, link["href"].strip())
                else:
                    text = clean(p.get_text())
                    if is_meaningful(text):
                        business["Website URL"] = text
                section = None
            elif section == "description":
                text = clean(p.get_text())
                if is_meaningful(text):
                    desc_parts.append(text)

        if desc_parts:
            business["Description"] = "\n\n".join(desc_parts)

    # ---- Logo ----
    logo_img = soup.select_one(".logo_main_box img[src]")
    if logo_img:
        business["Logo"] = urljoin(url, logo_img["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Photos (banner's CSS background-image slider) ----
    # When no photo has been uploaded for a listing, this template still
    # emits a background-image url() -- but pointing at the bare uploads
    # *directory* with no filename (e.g. ".../uploads/business/'), which
    # renders as the flat gray placeholder box seen on this listing.
    # Require an actual filename after the last "/" so that placeholder
    # isn't mistaken for a real photo.
    for slider in soup.select(".slider_box"):
        style = slider.get("style", "")
        if "background-image" not in style:
            continue
        match = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
        if match and match.group(1):
            photo_path = match.group(1).strip()
            if photo_path and not photo_path.endswith("/"):
                business["Photos"] = [urljoin(url, photo_path)]
        break

    return business


