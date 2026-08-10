"""
Site parser: trueen.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



_TRUEEN_OWN_EMAIL_DOMAINS = ("trueen.com",)
_TRUEEN_OWN_SOCIAL_HANDLES = (
    "facebook.com/trueencom",
    "twitter.com/trueen_com",
    "linkedin.com/company/trueen-com",
)


def _split_trueen_address(text):
    result = {"Street": "", "City": "", "State": "", "Zipcode": ""}

    text = clean(text)
    if not text:
        return result

    zip_match = re.search(r"(\d{5}(?:-\d{4})?)\s*$", text)
    if zip_match:
        result["Zipcode"] = zip_match.group(1)
        text = text[:zip_match.start()].strip().rstrip(",").strip()

    parts = [p.strip() for p in text.split(",") if p.strip()]

    if parts:
        result["State"] = parts.pop()
    if parts:
        result["City"] = parts.pop()
    if parts:
        result["Street"] = ", ".join(parts)

    # This template usually gives "Street, City, State" (comma-separated),
    # but some listings render the address with NO commas at all -- just
    # "City ST" left after the zip is stripped off (e.g. "Plano TX").
    # The comma split above then produces a single part, which the logic
    # dumps whole into State ("Plano TX") and leaves City blank. Detect
    # that one-part, comma-free case and split it into city/state instead.
    if result["City"] == "" and result["Street"] == "" and result["State"]:
        match = re.match(r"^(?P<city>.+?)\s+(?P<state>[A-Za-z]{2})$", result["State"])
        if match:
            result["City"] = clean(match.group("city"))
            result["State"] = match.group("state")

    return result


def _trueen_faq_answers(soup):
    answers = {}
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("@type") != "FAQPage":
            continue
        for item in data.get("mainEntity", []):
            if not isinstance(item, dict):
                continue
            question = clean(item.get("name", ""))
            accepted = item.get("acceptedAnswer", {})
            text = accepted.get("text", "") if isinstance(accepted, dict) else ""
            if question and text:
                answers[question.lower()] = text
    return answers


def _trueen_local_business_jsonld(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "LocalBusiness":
            return data
    return {}


def parse_trueen(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    faq = _trueen_faq_answers(soup)
    local_business = _trueen_local_business_jsonld(soup)

    # ---- Business Name ----
    h1 = soup.select_one("h1.header-titlex") or soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"] and local_business.get("name"):
        business["Business Name"] = clean(local_business["name"])
    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            # Drop the " - <tagline>, <location> - TRUEen" suffix this
            # template appends to every og:title.
            business["Business Name"] = clean(og_title["content"].split(" - ")[0])

    # ---- Category ----
    cat_link = soup.select_one("span.single-page-category a") or \
        soup.select_one('a[href*="/business-listing/category/"]')
    if cat_link:
        business["Category"] = clean(cat_link.get_text())

    # ---- Country ----
    country_icon = soup.select_one("i.fa-passport")
    country_link = (
        country_icon.find_parent("p").select_one("a") if country_icon and country_icon.find_parent("p") else None
    ) or soup.select_one('a[href*="/business-listing/country/"]')
    if country_link:
        business["Country"] = clean(country_link.get_text())
    elif local_business.get("address", {}).get("addressCountry"):
        business["Country"] = clean(local_business["address"]["addressCountry"])

    # ---- Street / City / State / Zipcode ----
    address_text = None
    for question, text in faq.items():
        if "headquarters located" in question:
            address_text = text
            break

    if not address_text:
        addr_locality = local_business.get("address", {}).get("addressLocality")
        if addr_locality:
            address_text = addr_locality

    if not address_text:
        marker_icon = soup.select_one("i.fa-map-marker")
        if marker_icon and marker_icon.find_parent("p"):
            address_text = marker_icon.find_parent("p").get_text()

    if address_text:
        parts = _split_trueen_address(address_text)
        business["Street"] = parts["Street"]
        business["City"] = parts["City"]
        business["State"] = parts["State"]
        business["Zipcode"] = parts["Zipcode"]

    # ---- Phone ----
    for question, text in faq.items():
        if "contact phone number" in question and re.search(r"\d{5,}", text):
            business["Phone"] = clean(text)
            break

    if not business["Phone"] and local_business.get("telephone"):
        business["Phone"] = clean(local_business["telephone"])

    if not business["Phone"]:
        phone_p = soup.select_one("p.single-page-phone")
        if phone_p:
            business["Phone"] = clean(phone_p.get_text())

    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel and tel.get("href"):
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Website URL ----
    website_link = soup.select_one('a.view-button[target="_blank"][rel="nofollow"]')
    if website_link and website_link.get("href"):
        href = website_link["href"].strip()
        if href and not href.lower().startswith("javascript:"):
            business["Website URL"] = href

    if not business["Website URL"]:
        for question, text in faq.items():
            if "official website" in question and text.strip().lower().startswith(("http://", "https://")):
                business["Website URL"] = clean(text)
                break

    # ---- Description ----
    for question, text in faq.items():
        if question.startswith("who is") and "owner" not in question and "ceo" not in question:
            business["Description"] = clean_multiline(text)
            break

    if not business["Description"]:
        bio = soup.select_one("div.company-bio")
        if bio:
            paragraphs = [clean(p.get_text()) for p in bio.find_all("p")]
            paragraphs = [p for p in paragraphs if p]
            if paragraphs:
                business["Description"] = "\n".join(paragraphs)
            else:
                business["Description"] = clean(bio.get_text())

    if not business["Description"] and local_business.get("description"):
        business["Description"] = clean(local_business["description"])

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and is_meaningful(meta_desc.get("content", "")):
            business["Description"] = clean(meta_desc["content"])

    # ---- Owner Name (FAQPage JSON-LD only: "Who is the Owner/CEO/
    #      Representative of <business>?" -- the HTML-rendered version of
    #      this question is just a lead-gen form with no real name, so
    #      only the JSON-LD answer ever carries an actual person's name) ----
    for question, text in faq.items():
        if "owner" in question and ("ceo" in question or "representative" in question):
            owner_name = clean(text)
            # Some listings' JSON-LD just echoes the business name back as
            # the "answer" (no real owner on file) instead of omitting it
            # or saying so explicitly -- e.g. answer text ==
            # "Neera Truong Real Estate", the business's own name. That's
            # not an owner, so treat it the same as the other placeholder
            # case ("company information...") and leave Owner Name blank.
            is_placeholder = (
                not is_meaningful(owner_name)
                or "company information" in owner_name.lower()
                or owner_name.lower() == business["Business Name"].lower()
            )
            if not is_placeholder:
                business["Owner Name"] = owner_name
            break

    # ---- Hours ----
    for question, text in faq.items():
        if "business hours" in question or "opening hours" in question or "operating hours" in question:
            business["Hours"] = clean(text)
            break

    # ---- Social Media Links -----
    # Restrict the scan to the business content area and skip the site
    # footer entirely -- the footer carries TRUEen's OWN contact links
    # (e.g. its own WhatsApp number, its own Facebook/Twitter/LinkedIn),
    # which have nothing to do with the listed business. Previously only
    # the three own-brand profile URLs were excluded, so the footer's
    # WhatsApp link was getting misattributed to every single listing.
    footer = soup.select_one("div.footer")
    for a in soup.find_all("a", href=True):
        if footer and footer in a.parents:
            continue
        href = a["href"].strip()
        if not href or href == "#" or href.lower().startswith("javascript:"):
            continue
        low = href.lower()
        if any(handle in low for handle in _TRUEEN_OWN_SOCIAL_HANDLES):
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href

    return business


