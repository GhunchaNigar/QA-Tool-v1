"""
Site parser: chamberofcommerce.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


def parse_chamberofcommerce(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- JSON-LD (primary source: name, address, description, logo) ----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:

            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
                continue

            if obj.get("name"):
                business["Business Name"] = clean(obj["name"])

            if obj.get("description"):
                desc_text = clean(
                    BeautifulSoup(obj["description"], "lxml").get_text(separator=" ")
                )
                if is_meaningful(desc_text):
                    business["Description"] = desc_text

            if obj.get("image"):
                business["Logo"] = urljoin(url, obj["image"])

            addr = obj.get("address", {})
            if isinstance(addr, dict):
                business["Street"] = clean(addr.get("streetAddress", ""))
                business["City"] = clean(addr.get("addressLocality", ""))
                business["State"] = clean(addr.get("addressRegion", ""))
                business["Zipcode"] = clean(addr.get("postalCode", ""))
                business["Country"] = clean(addr.get("addressCountry", ""))

    # ---- Business Name fallback (visible H1) ----
    if not business["Business Name"]:
        h1 = soup.select_one("h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Address fallback----
    if not business["Street"]:
        addr1 = soup.select_one('span[selector-type="Address1"]')
        if addr1:
            street = clean(addr1.get_text())
            addr2 = soup.select_one('span[selector-type="Address2"]')
            if addr2:
                addr2_text = clean(addr2.get_text())
                if addr2_text:
                    street = f"{street}, {addr2_text}"
            business["Street"] = street

    if not business["City"]:
        city_tag = soup.select_one('span[selector-type="City"]')
        if city_tag:
            business["City"] = clean(city_tag.get_text()).rstrip(",")

    if not business["State"]:
        state_tag = soup.select_one('span[selector-type="State"]')
        if state_tag:
            business["State"] = clean(state_tag.get_text())

    if not business["Zipcode"]:
        zip_tag = soup.select_one('span[selector-type="Zip"]')
        if zip_tag:
            business["Zipcode"] = clean(zip_tag.get_text())

    if not business["Country"] and business["Street"]:
        business["Country"] = "US"

    # ---- Description  ----
    if not business["Description"]:
        about_card = None
        for heading in soup.select(".card-body h3.card-title"):
            if "about" in clean(heading.get_text()).lower():
                about_card = heading.find_parent("div", class_="card-body")
                break
        if about_card:
            card_copy = BeautifulSoup(str(about_card), "lxml")
            heading_copy = card_copy.find("h3")
            if heading_copy:
                heading_copy.decompose()
            desc_text = clean(card_copy.get_text(separator=" "))
            if is_meaningful(desc_text):
                business["Description"] = desc_text

    # ---- Description final fallback ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            desc_text = clean(meta_desc["content"])
            if is_meaningful(desc_text):
                business["Description"] = desc_text

    # ---- Phone ----
    phone_icon = soup.select_one("i.fa-phone")
    if phone_icon and phone_icon.parent:
        phone_text = clean(phone_icon.parent.get_text())
        if phone_text:
            business["Phone"] = phone_text

    # ---- Website URL ----
    site_span = soup.select_one('span[selector-type="Website"] a[href]')
    if site_span:
        business["Website URL"] = site_span["href"]

    # ---- Keywords ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_text = clean(meta_kw.get("content", ""))
        if is_meaningful(kw_text):
            business["Keywords"] = kw_text

    # ---- Hours----
    hours_container = soup.select_one(".HoursofOperation .row.mb-0.text-dark")
    if hours_container:
        cells = hours_container.find_all("div", recursive=False)
        pairs = []
        for i in range(0, len(cells) - 1, 2):
            day = clean(cells[i].get_text()).rstrip(":")
            value = clean(cells[i + 1].get_text())
            if day:
                pairs.append(f"{day}: {value}")
        hours_text = "; ".join(pairs)
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category  ----
    crumbs = [clean(li.get_text()) for li in soup.select(".breadcrumb li.breadcrumb-item")]
    crumbs = [c for c in crumbs if c]
    if len(crumbs) >= 2:
        business["Category"] = crumbs[-2]

    # ---- Owner Name ("Key Contacts" card: name sits in an <h5>, e.g.
    #      "Svetlana Reeves", above a job-title <h6> and phone/email) ----
    contact_card = None
    for heading in soup.select(".card-body h3.card-title"):
        if "key contact" in clean(heading.get_text()).lower():
            contact_card = heading.find_parent("div", class_="card-body")
            break
    if contact_card:
        name_tag = contact_card.select_one("h5")
        if name_tag:
            owner_name = clean(name_tag.get_text())
            if is_meaningful(owner_name):
                business["Owner Name"] = owner_name

    # ---- Owner Name fallback (FAQPage JSON-LD -- the "Is there a key
    #      contact at ...?" answer reads "You can contact NAME at PHONE.") ----
    if not business["Owner Name"]:
        for script in soup.find_all("script", type="application/ld+json"):
            if not script.string:
                continue
            try:
                data = json.loads(script.string, strict=False)
            except Exception:
                continue
            if not isinstance(data, dict) or data.get("@type") != "FAQPage":
                continue
            for item in data.get("mainEntity", []):
                if not isinstance(item, dict):
                    continue
                if "key contact" not in clean(item.get("name", "")).lower():
                    continue
                answer = item.get("acceptedAnswer", {})
                text = answer.get("text", "") if isinstance(answer, dict) else ""
                match = re.search(r"contact\s+(.+?)\s+at\b", text, re.I)
                if match:
                    business["Owner Name"] = clean(match.group(1))
            break

    # ---- Logo fallback (profile image, if JSON-LD had none) ----
    if not business["Logo"]:
        logo_img = soup.select_one("img.ProfileImage")
        if logo_img and logo_img.get("src"):
            business["Logo"] = urljoin(url, logo_img["src"])

    # ---- Photos ----
    photos = []
    for a in soup.select("#profile_images a.lightbox_trigger[href]"):
        photos.append(urljoin(url, a["href"]))
    business["Photos"] = photos

    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email

    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto and mailto.get("href"):
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Social Media Links ----
    for network in ("Facebook", "Twitter"):
        link = soup.select_one(f'span[selector-type="{network}"] a[href]')
        if link and link.get("href"):
            business["Social Media Links"][network] = link["href"]

    return business


