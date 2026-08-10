"""
Site parser: qdexx.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



# Some qdexx listings have no dedicated phone field/element on the page at
# all -- the business owner instead crammed it into the free-text "About"
# description as a literal "Phone:\n<number>" line. This is the only place
# a phone number appears on such listings, so it's extracted from there
# as a labeled fallback (not a blind scan of arbitrary page text).
_QDEXX_PHONE_LABEL_RE = re.compile(r"Phone:\s*([\d][\d\-.\s()]{6,}\d)", re.I)


def _qdexx_load_json_ld(soup):
    """Returns (main_business_dict, breadcrumb_dict) from the page's
    JSON-LD <script> tags."""
    main_ld, breadcrumb_ld = None, None
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            # strict=False: some qdexx listings embed literal, unescaped
            # newlines inside JSON string values (e.g. a multi-line
            # description), which strict JSON rejects as a control
            # character but the site's own templating clearly intends
            # as part of the string.
            data = json.loads(script.string, strict=False)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("@type") == "BreadcrumbList":
            breadcrumb_ld = data
        elif "address" in data or "name" in data:
            main_ld = data
    return main_ld, breadcrumb_ld


def parse_qdexx(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    main_ld, breadcrumb_ld = _qdexx_load_json_ld(soup)

    # ---- Name / Description / Address / Website (JSON-LD, primary) ----
    if main_ld:
        business["Business Name"] = html_lib.unescape(clean(main_ld.get("name", "")))
        business["Description"] = html_lib.unescape(clean(main_ld.get("description", "")))
        website = main_ld.get("url", "")
        if website:
            business["Website URL"] = website.strip()

        address = main_ld.get("address") or {}
        business["Street"] = clean(address.get("streetAddress", ""))
        business["City"] = clean(address.get("addressLocality", ""))
        business["State"] = clean(address.get("addressRegion", ""))
        business["Zipcode"] = clean(str(address.get("postalCode", "")))

        if main_ld.get("telephone"):
            business["Phone"] = clean(main_ld["telephone"])
        if main_ld.get("email"):
            business["Business Email"] = clean(main_ld["email"])

    # ---- Name (DOM fallback) ----
    if not business["Business Name"]:
        h1 = soup.select_one(".tileOverlay h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Description (DOM fallback -- "About" tile) ----
    if not business["Description"]:
        about_p = soup.select_one("p.pre")
        if about_p:
            business["Description"] = clean(about_p.get_text())

    # ---- Category (breadcrumb JSON-LD: second-to-last item, since the
    #      last item is the business listing itself) ----
    if breadcrumb_ld:
        items = breadcrumb_ld.get("itemListElement") or []
        if len(items) >= 2:
            cat_item = items[-2].get("item") or {}
            cat_name = clean(cat_item.get("name", ""))
            if cat_name:
                business["Category"] = cat_name

    # ---- Category (DOM fallback -- tagline tile, e.g. "Lawyer in Dover DE") ----
    if not business["Category"]:
        tagline_h2 = soup.select_one("li.tagline h2")
        if tagline_h2:
            text = clean(tagline_h2.get_text())
            match = re.match(r"^(.*?)\s+in\s+.+$", text, re.I)
            if match:
                business["Category"] = match.group(1).strip()

    # ---- Website URL (DOM fallback -- "Online" tile) ----
    if not business["Website URL"]:
        for li in soup.select("li.tile.bp"):
            h3 = li.find("h3")
            if h3 and clean(h3.get_text()).lower() == "online":
                link = li.select_one("a[href]")
                if link:
                    business["Website URL"] = link["href"].strip()
                break

    # ---- Hours ("Hours of Operation" tile) ----
    for li in soup.select("li.tile.bp"):
        h3 = li.find("h3")
        if h3 and clean(h3.get_text()).lower() == "hours of operation":
            p = li.find("p")
            if p:
                lines = [clean(l) for l in p.get_text(separator="\n").split("\n") if clean(l)]
                if lines:
                    business["Hours"] = "; ".join(lines)
            break

    # ---- Phone / Email (DOM fallback -- "Contact" tile) ----
    if not business["Phone"] or not business["Business Email"]:
        for li in soup.select("li.tile.bp"):
            h3 = li.find("h3")
            if h3 and clean(h3.get_text()).lower() == "contact":
                if not business["Phone"]:
                    tel_link = li.select_one('a[href^="tel:"]')
                    if tel_link:
                        phone_text = clean(tel_link.get_text())
                        # the visible text is "tel (214) 566-1908" -- strip the
                        # leading "tel" label, falling back to the href itself
                        phone_text = re.sub(r"^tel\s*", "", phone_text, flags=re.I).strip()
                        business["Phone"] = phone_text or tel_link["href"].replace("tel:", "").strip()
                if not business["Business Email"]:
                    mail_link = li.select_one('a[href^="mailto:"]')
                    if mail_link:
                        email_text = clean(mail_link.get_text()) or mail_link["href"].replace("mailto:", "").strip()
                        if is_meaningful(email_text):
                            business["Business Email"] = email_text
                break

    # ---- Phone (labeled fallback out of the About description -- this
    #      site provides no dedicated phone field/element for this listing) ----
    phone_source = business["Description"]
    if not business["Phone"]:
        if not phone_source:
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc:
                phone_source = meta_desc.get("content", "")
        if phone_source:
            phone_match = _QDEXX_PHONE_LABEL_RE.search(phone_source)
            if phone_match:
                business["Phone"] = clean(phone_match.group(1))

    return business


