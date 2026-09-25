def parse_letsknowit(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Name ----
    name_el = soup.select_one(".userProfileName h1")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    details = soup.select_one(".companyDetails.profilegeneraldetail")

    # ---- Street / City / State / Zipcode ----
    map_row_text = _letsknowit_address_row(details)
    headquarter_text = _letsknowit_detail_value(details, "headquarter")
    address_text = max(
        (map_row_text, headquarter_text),
        key=_letsknowit_address_quality,
    )

    if _letsknowit_address_quality(address_text) >= 0:
        street, city, state, zipcode = _letsknowit_split_address(address_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone ("Phone:" row) ----
    phone = _letsknowit_detail_value(details, "phone")
    if phone:
        business["Phone"] = phone

    # ---- Phone (sidebar "Contact Details" widget fallback) ----
    if not business["Phone"]:
        tel = soup.select_one('.widget.personal-info a[href^="tel:"] span')
        if tel:
            business["Phone"] = clean(tel.get_text())

    # ---- Website URL ("Website:" row, marked with the tl_exp class) ----
    if details:
        website_span = details.select_one("h3 span.tl_exp")
        if website_span:
            link = website_span.find("a", href=True)
            if link:
                business["Website URL"] = link["href"].strip()

    # ---- Business Email (sidebar "Contact Details" widget -- the mailto
    #      href itself is blanked out client-side, so read the visible
    #      span text instead) ----
    email_anchor = soup.select_one('.widget.personal-info a[href^="mailto:"]')
    if email_anchor:
        span = email_anchor.find("span")
        email_text = clean(span.get_text()) if span else ""
        if not email_text:
            email_text = email_anchor["href"].replace("mailto:", "").strip()
        if "@" in email_text:
            business["Business Email"] = email_text

    # ---- Description ("About <Name>" block; site emits invalid nested
    #      <p><p>...</p></p> markup, so pull text from the container
    #      rather than a single <p> match) ----
    about = soup.select_one("#aboutcontent")
    if about:
        text = clean(about.get_text(separator=" "))
        if is_meaningful(text):
            business["Description"] = text

    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])
    if not business["Logo"]:
        logo_img = soup.select_one(".profile-pic img[src]")
        if logo_img:
            business["Logo"] = urljoin(url, logo_img["src"])

    # ---- Photos (gallery section shows an "empty_message" placeholder
    #      instead of images when nothing has been uploaded) ----
    gallery = soup.select_one("#companyGalleryContent")
    if gallery and not gallery.select_one(".empty_message"):
        photos = []
        for img in gallery.select("img[src]"):
            src = urljoin(url, img["src"])
            if src not in photos:
                photos.append(src)
        if photos:
            business["Photos"] = photos

    return business





def parse_metriteweb(url, html):
    """metriteweb.com runs the WordPress "Classified Listing" (rtcl)
    plugin's default listing template. Every field lives under
    predictable rtcl-* / listingDetails-* classes."""

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Name ----
    name_el = soup.select_one(".listingDetails-header__heading")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Category ----
    cat_el = soup.select_one("a.listingDetails-header__tag")
    if cat_el:
        cat_text = clean(cat_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Description ----
    desc_el = soup.select_one(".listingDetails-block__des__text")
    if desc_el:
        text = clean(desc_el.get_text(separator=" "))
        if is_meaningful(text):
            business["Description"] = text

    # ---- Street / City / State / Zipcode (the address is the first,
    #      link-less <li> in the "Posted By" info-list -- the other two
    #      <li>s are the phone and website links) ----
    addr_li = soup.select_one(".rtcl-listing-user-info .info-list li")
    if addr_li and not addr_li.find("a"):
        addr_text = clean(addr_li.get_text())
        if addr_text:
            street, city, state, zipcode = _split_address_allow_no_comma(addr_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    # ---- Phone ----
    phone_link = soup.select_one("a.rtcl-phone-link")
    if phone_link:
        business["Phone"] = clean(phone_link.get_text())

    # ---- Website URL ----
    site_link = soup.select_one("a.rtcl-website-link")
    if site_link and site_link.get("href"):
        business["Website URL"] = urljoin(url, site_link["href"].strip())

    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])

    return business





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



    def parse_dbesearch(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    jumbotron = soup.select_one(".jumbotron")

    # ---- Name ----
    name_el = jumbotron.select_one("h1") if jumbotron else soup.select_one("h1")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Category (the <p><b>Category: </b>Other</p> block) ----
    if jumbotron:
        for p in jumbotron.find_all("p"):
            b = p.find("b")
            if b and "category" in clean(b.get_text()).lower():
                full_text = clean(p.get_text())
                label = clean(b.get_text())
                business["Category"] = full_text[len(label):].strip(" :")
                break

    # ---- Logo ----
    logo_el = soup.select_one('img[name^="logo_"]')
    if logo_el and logo_el.get("src"):
        business["Logo"] = urljoin(url, logo_el["src"])

    # ---- Website URL ----
    website_el = soup.select_one("a.business-web-link[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()

    # ---- Street / City / State / Zipcode ----
    address_el = soup.select_one(".business_address")
    if address_el:
        # <br> splits the address into two separate text nodes (street,
        # then "City, ST Zip"); separator="\n" joins them one per line.
        address_text = address_el.get_text(separator="\n")
        street, city, state, zipcode = _split_dbesearch_address(address_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone ----
    phone_el = soup.select_one('.business_contact_phone a[href^="tel:"]')
    if phone_el:
        business["Phone"] = clean(phone_el.get_text()) or phone_el["href"].replace("tel:", "").strip()

    return business




    def parse_locuul(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one("h1.bold.inline-block")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        company = soup.select_one(".table-display-company .textbox-company")
        if company:
            business["Business Name"] = clean(company.get_text())

    # ---- Address  ----
    addr_container = soup.select_one(".overview-tab-the-member-address .col-sm-8")
    if addr_container:
        addr_spans = addr_container.find_all("span", recursive=False)
        if len(addr_spans) >= 4:
            business["Street"] = clean(addr_spans[0].get_text())
            business["City"] = clean(addr_spans[1].get_text())
            business["State"] = clean(addr_spans[2].get_text())
            business["Zipcode"] = clean(addr_spans[3].get_text())
        elif not business["Street"]:
            addr_text = clean(addr_container.get_text())
            if is_meaningful(addr_text):
                match = _LOCUUL_ADDRESS_RE.match(addr_text)
                if match:
                    business["Street"] = clean(match.group("street"))
                    business["City"] = clean(match.group("city"))
                    business["State"] = clean(match.group("state"))
                    business["Zipcode"] = match.group("zip")
                else:
                    city_state_zip_match = _LOCUUL_CITY_STATE_ZIP_RE.match(addr_text)
                    if city_state_zip_match:
                        business["City"] = clean(city_state_zip_match.group("city"))
                        business["State"] = clean(city_state_zip_match.group("state"))
                        business["Zipcode"] = city_state_zip_match.group("zip")
                    else:
                        business["Street"] = addr_text

        trailing_text_nodes = [
            clean(node) for node in addr_container.contents
            if isinstance(node, NavigableString) and clean(node) and clean(node) != ","
        ]
        if trailing_text_nodes:
            country_text = trailing_text_nodes[-1]
            if country_text:
                business["Country"] = country_text

    # ---- Country/Phone fallback (LocalBusiness node inside the page's
    # JSON-LD "@graph" array) ----
    jsonld_local_business = {}
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
            if isinstance(node, dict) and node.get("@type") == "LocalBusiness":
                jsonld_local_business = node
                break
        if jsonld_local_business:
            break

    if not business["Country"]:
        country = clean(jsonld_local_business.get("address", {}).get("addressCountry", ""))
        if country and country.upper() != "N/A":
            business["Country"] = country

    # ---- Phone  ----
    phone_el = soup.select_one(".table-display-phone .col-sm-8")
    if phone_el:
        phone_text = clean(phone_el.get_text())
        if is_meaningful(phone_text):
            business["Phone"] = phone_text
    if not business["Phone"]:
        phone_link = soup.select_one(".table-display-phone a[href^='tel:']")
        if phone_link and phone_link.get("href"):
            business["Phone"] = phone_link["href"].replace("tel:", "").strip()
    if not business["Phone"] and jsonld_local_business.get("telephone"):
        business["Phone"] = clean(jsonld_local_business["telephone"])

    # ---- Website URL (dedicated labeled row) ----
    website_el = soup.select_one(".table-display-website .weblink[href]")
    if website_el:
        business["Website URL"] = website_el["href"].strip()

    # ---- Description  ----
    about_el = soup.select_one(".froala-data.field-about_me")
    about_text = ""
    if about_el:
        about_text = clean_multiline(about_el.get_text(separator="\n"))
        if is_meaningful(about_text):
            business["Description"] = about_text

    if not business["Description"] and jsonld_local_business.get("description"):
        desc_text = clean(jsonld_local_business["description"])
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Hours  ----
    for row in soup.select(".table-view-group"):
        label = row.select_one(".col-sm-4")
        value = row.select_one(".col-sm-8")
        if label and value and clean(label.get_text()).lower() == "hours of operation":
            hours_text = clean(value.get_text())
            if is_meaningful(hours_text):
                business["Hours"] = hours_text
            break

    # ---- Category ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        cat_text = clean(category_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    if not business["Category"]:
        crumbs = [clean(li.get_text()) for li in soup.select("ol.breadcrumb li")]
        crumbs = [c for c in crumbs if c and c.lower() != "home"]
        if len(crumbs) >= 2:
            business["Category"] = crumbs[-1]

    # ---- Social Media Links (opportunistic; not every listing on this
    # source publishes any) ----
    social_links = {}
    for a in soup.select(".table-display-social-links a[href]"):
        href = a.get("href", "")
        for domain, name in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                social_links[name] = href
    if social_links:
        business["Social Media Links"] = social_links

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Business Email ----
    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email
    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto and mailto.get("href"):
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()
    if not business["Business Email"] and about_text:
        email_match = _EMAIL_RE.search(about_text)
        if email_match:
            business["Business Email"] = email_match.group(0)

    # ---- GBP Link (scoped to the "Get Directions" anchor) ----
    directions = soup.select_one("a.get-directions-link[href]")
    if directions and _is_maps_link(directions["href"]):
        business["GBP Link"] = directions["href"]

    return business




    def parse_bpublic(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    company_el = soup.select_one(".table-display-company .textbox-company")
    if company_el:
        business["Business Name"] = clean(company_el.get_text())
    if not business["Business Name"]:
        h1 = soup.select_one(".header-member-name h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Category (badge under the name, e.g. "Professional Services") ----
    category_el = soup.select_one(".profile-header-top-category")
    if category_el:
        business["Category"] = clean(category_el.get_text())

    # ---- Structured address rows (when a listing fills them in) ----
    business["Street"] = _bpublic_field(soup, "address1") or _bpublic_field(soup, "street")
    business["City"] = _bpublic_field(soup, "city")
    business["State"] = _bpublic_field(soup, "state_ln") or _bpublic_field(soup, "state")
    business["Zipcode"] = _bpublic_field(soup, "zip_code") or _bpublic_field(soup, "zipcode")
    business["Country"] = _bpublic_field(soup, "country_ln") or _bpublic_field(soup, "country")

    # ---- Country / State / City / Category fallback: breadcrumb trail
    #      (Home > Country > State > City > Category) ----
    crumbs = [clean(s.get_text()) for s in soup.select(".breadcrumb span[itemprop='name']")]
    if crumbs and crumbs[0].lower() == "home":
        crumbs = crumbs[1:]
    if len(crumbs) >= 1 and not business["Country"]:
        business["Country"] = crumbs[0]
    if len(crumbs) >= 2 and not business["State"]:
        business["State"] = crumbs[1]
    if len(crumbs) >= 3 and not business["City"]:
        business["City"] = crumbs[2]
    if len(crumbs) >= 4 and not business["Category"]:
        business["Category"] = crumbs[3]

    # ---- Phone (structured row, tel: link, or reveal-on-click header) ----
    phone_el = soup.select_one(".table-display-phone_number .phone") \
        or soup.select_one(".table-display-phone .phone")
    if phone_el:
        business["Phone"] = clean(phone_el.get_text())
    if not business["Phone"]:
        phone_header = soup.select_one(".phone_number_header")
        if phone_header:
            business["Phone"] = clean(phone_header.get_text())
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = clean(tel["href"].replace("tel:", ""))

    # ---- Website URL (structured row) ----
    website_el = soup.select_one(".table-display-website a[href]") \
        or soup.select_one(".table-display-website .weblink[href]")
    if website_el:
        business["Website URL"] = website_el["href"]

    # ---- Hours (structured row, when a listing has one) ----
    hours_el = soup.select_one(".table-display-hours")
    if hours_el:
        business["Hours"] = clean(hours_el.get_text())

    # ---- Description + Address/Phone/Website fallback: the "About" box.
    #      On many listings this is just free-text description, but on
    #      some (e.g. "Focal") it also carries Address/Phone/Website as
    #      label/value paragraph pairs when the structured rows above
    #      were left empty. ----
    about_el = soup.select_one(".table-display-about_me .froala-data") \
        or soup.select_one(".field-about_me")
    if about_el:
        paragraphs = [clean(p.get_text()) for p in about_el.find_all("p")]
        paragraphs = [p for p in paragraphs if p]
        has_labels = any(_bpublic_normalize_label(p) in _BPUBLIC_ABOUT_LABELS for p in paragraphs)

        if has_labels:
            _parse_bpublic_about_block(business, about_el, url)
        elif paragraphs:
            business["Description"] = "\n".join(paragraphs)

    if not business["Description"]:
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            business["Description"] = clean(meta["content"])

    # ---- Logo ----
    logo_el = soup.select_one(".profile-image img")
    if logo_el and logo_el.get("src"):
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business



    def parse_smallbusinessusa(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- business:contact_data Open Graph extension (primary source) ----
    contact_meta = {}
    for meta in soup.find_all("meta", property=True):
        prop = meta["property"]
        if prop.startswith("business:contact_data:"):
            key = prop.split(":")[-1]
            contact_meta[key] = clean(meta.get("content", ""))

    business["Street"] = contact_meta.get("street_address", "")
    business["City"], business["State"] = _resolve_city_state(
        contact_meta.get("locality", ""), contact_meta.get("region", "")
    )
    business["Zipcode"] = contact_meta.get("postal_code", "")
    business["Country"] = contact_meta.get("country_name", "")
    business["Phone"] = contact_meta.get("phone_number", "")
    business["Website URL"] = contact_meta.get("website", "")

    # ---- JSON-LD (name/logo, backs up address/phone if missing) ----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:

            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
                continue

            business["Business Name"] = obj.get("name", business["Business Name"])

            if obj.get("telephone") and not business["Phone"]:
                business["Phone"] = obj["telephone"]

            addr = obj.get("address", {})

            if not business["Street"]:
                business["Street"] = addr.get("streetAddress", "")
            if not business["City"] and not business["State"]:
                business["City"], business["State"] = _resolve_city_state(
                    addr.get("addressLocality", ""), addr.get("addressRegion", "")
                )
            if not business["Zipcode"]:
                business["Zipcode"] = addr.get("postalCode", "")
            if not business["Country"]:
                business["Country"] = addr.get("addressCountry", "")

    # ---- Business Name fallback (visible <h1>) ----
    if not business["Business Name"]:
        h1 = soup.select_one("article.detail h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Phone fallback (tel: link) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Website URL fallback ("Visit Website" button) ----
    if not business["Website URL"]:
        website_link = soup.select_one("#visit-website")
        if website_link and website_link.get("href"):
            business["Website URL"] = website_link["href"]

    # ---- Category (breadcrumb inside the listing article) ----
    category_links = soup.select("article.detail ul.breadcrumb a")
    categories = []
    for a in category_links:
        text = clean(a.get_text())
        if text and text not in categories:
            categories.append(text)
    if categories:
        business["Category"] = ", ".join(categories)

    return business






def parse_zeemaps(url, html=None):
    group = _zeemaps_group_id(url)

    # ---- Data version hash (required by /emarkers) ----
    version = _zeemaps_get("/regions/version", g=group).get("v", "")

    # ---- Marker list ----
    markers = _zeemaps_get("/emarkers", g=group, k="REGULAR", e="false", v=version)

    # ---- Custom field id -> name mapping (generic, not hardcoded) ----
    attrs_raw = _zeemaps_get("/data/attributes", group=group)
    field_names = {fid: meta.get("n", "").strip().lower() for fid, meta in attrs_raw.items()}

    # ---- Map-level description fallback ----
    mapprops = _zeemaps_get("/data/mapprops", group=group, readonly="true")
    map_about = clean_multiline(mapprops.get("mp", {}).get("about", ""))

    results = []

    for m in markers:
        marker_id = m.get("id")
        business = empty_business()

        # Base fields from the marker list
        business["Business Name"] = m.get("nm", "")
        business["Street"] = m.get("s", "")
        business["City"] = m.get("city", "")
        business["State"] = m.get("state", "")
        business["Zipcode"] = m.get("zip", "")

        # ---- Per-marker popup detail (has the real field values) ----
        try:
            detail = _zeemaps_get(
                "/etext",
                g=group,
                j=1,
                sh="",
                _dc=random.random(),
                eids=f"[{marker_id}]",
            )
            if isinstance(detail, list):
                detail = detail[0] if detail else {}
        except Exception:
            detail = {}

        if detail.get("title"):
            business["Business Name"] = detail["title"]

        addr = detail.get("ad", {})
        if addr.get("street"):
            business["Street"] = addr["street"]
        if addr.get("city"):
            business["City"] = addr["city"]
        if addr.get("state"):
            business["State"] = addr["state"]
        if addr.get("postcode"):
            business["Zipcode"] = addr["postcode"]

        # ---- Address fallback: some ZeeMaps groups never populate ----
        if business["Street"] and not business["City"] and not business["State"]:
            street, city, state, zipcode = _split_blinx_address(business["Street"])
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            if not business["Zipcode"]:
                business["Zipcode"] = zipcode

        # ---- Custom fields, resolved generically by name ----
        for fid, value in detail.get("fields", {}).items():
            if not value:
                continue
            name = field_names.get(fid, "")
            if name == "phone":
                business["Phone"] = value
            elif name == "website":
                business["Website URL"] = value
            elif name == "email":
                business["Business Email"] = value
            elif name == "description":
                business["Description"] = clean_multiline(value)

        if not business["Description"]:
            business["Description"] = map_about

        # ---- Photo (embedded as an <img> tag inside the "i" field) ----
        img_html = detail.get("i", "")
        if img_html:
            img_match = re.search(r"src=['\"]([^'\"]+)['\"]", img_html)
            if img_match:
                business["Logo"] = img_match.group(1)

        results.append(business)

    if not results:
        return empty_business()
    return results[0] if len(results) == 1 else results



    def parse_callupcontact(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name (page <h1>) ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- About Us (description) ----
    description = _value_after_heading(soup, "About Us")
    if description:
        business["Description"] = description

    # ---- Call & Message ----
    phone = _value_after_heading(soup, "Telephone")
    if phone:
        business["Phone"] = phone

    website = _value_after_heading(soup, "Website")
    if website:
        business["Website URL"] = website

    # ---- Email (Cloudflare-obfuscated, not a plain mailto:) ----
    email = _find_cf_email(soup)
    if email:
        business["Business Email"] = email

    # ---- Address ----
    street = _value_after_heading(soup, "Street Address")
    if street:
        business["Street"] = street

    city = _value_after_heading(soup, "City")
    if city:
        business["City"] = city

    state = _value_after_heading(soup, "State / Province")
    if state:
        business["State"] = state

    zipcode = _value_after_heading(soup, "Zip / Postal Code")
    if zipcode:
        business["Zipcode"] = zipcode

    country = _value_after_heading(soup, "Country")
    if country:
        business["Country"] = country

    # ---- Hours ----
    hours = _value_after_heading(soup, "Hours") or _value_after_heading(soup, "Business Hours")
    if hours:
        business["Hours"] = hours

    # ---- Meta description fallback (page-level, matches About Us usually) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Meta keywords (strip this template's fixed boilerplate tail --
    #      see _CALLUPCONTACT_KEYWORD_BOILERPLATE above) ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            tokens = [clean(t) for t in kw_raw.split(",")]
            tokens = [
                t for t in tokens
                if t and t.lower() not in _CALLUPCONTACT_KEYWORD_BOILERPLATE
            ]
            if tokens:
                business["Keywords"] = ", ".join(tokens)

    return business


def parse_earthmom(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (visible <h1>, falls back to og:title split on
    #      " on " since the template renders it as "<Name> on Earth Mom") ----
    h1 = soup.select_one(".header-member-name h1") or soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"]).split(" on ")[0].strip()

    # ---- Category (short line directly under the name) ----
    category_tag = soup.select_one(".profile-header-top-category")
    if category_tag:
        business["Category"] = clean(category_tag.get_text())

    # ---- Address ----
    # This template usually gives a plain streetAddress itemprop, but on
    # profile pages like this one there's no street at all -- instead
    # addressLocality holds a merged "City ST" string (e.g. "Plano TX")
    # and postalCode is a separate sibling span. Fall back to combining
    # those into a single "City ST Zip" string the comma-less splitter
    # can parse correctly, the same fix applied for milestones.business.
    address_tag = soup.select_one('[itemprop="streetAddress"]')
    if address_tag:
        address_text = clean(address_tag.get_text(separator=" "))
        if address_text:
            street, city, state, zipcode = _split_blinx_address(address_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode
    else:
        locality_tag = soup.select_one('[itemprop="addressLocality"]')
        if locality_tag:
            locality_text = clean(locality_tag.get_text())
            zip_tag = soup.select_one('[itemprop="postalCode"]')
            zip_text = clean(zip_tag.get_text()) if zip_tag else ""
            combined = f"{locality_text} {zip_text}".strip()
            if combined:
                street, city, state, zipcode = _split_city_state_zip_address(combined)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode or zip_text

    # ---- Phone / Website / Business Email / Description ----
    about_container = soup.select_one(".overview-tab-about-me .textarea-about_me")
    if about_container:
        about_fields = _parse_earthmom_about_block(about_container)
        for field, value in about_fields.items():
            if is_meaningful(value):
                business[field] = value

    # ---- Description fallback (meta description, SEO-truncated) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Phone fallback (tel: link, if the free-form block didn't
    #      have one) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Country (same itemprop convention as the street address) ----
    country_tag = soup.select_one('[itemprop="addressCountry"]')
    if country_tag:
        business["Country"] = clean(country_tag.get_text())

    # ---- Country fallback: bare text node after the <br> ----
    # On pages like this one there's no addressCountry itemprop at all --
    # the country is just plain text sitting directly in the address
    # container after a <br>, following the addressLocality/postalCode
    # spans (e.g. "...75023<br />United States of America"). Pull the
    # last meaningful direct text node out of that container instead.
    if not business["Country"]:
        address_container = soup.select_one('[itemprop="address"]')
        if address_container:
            text_nodes = [
                clean(str(node)) for node in address_container.contents
                if isinstance(node, NavigableString)
            ]
            text_nodes = [t for t in text_nodes if is_meaningful(t)]
            if text_nodes:
                business["Country"] = text_nodes[-1]

    # ---- Hours ----
    hours_tag = soup.select_one('[itemprop="openingHours"]') or soup.select_one(".business-hours")
    if hours_tag:
        hours_text = clean(hours_tag.get_text(separator=" "))
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Social Media / GBP Link (external anchors, scanned like the
    #      other site parsers) ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "earthmom.org" in href.lower():
            continue
        if _is_maps_link(href):
            if not business["GBP Link"]:
                business["GBP Link"] = href
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])
    else:
        profile_img = soup.select_one(".profile-image img")
        if profile_img and profile_img.get("src"):
            business["Logo"] = urljoin(url, profile_img["src"])

    return business

    def parse_gravitysplash(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".post-meta-left-box h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Category ----
    breadcrumb_links = soup.select(".breadcrumbs li a")
    if len(breadcrumb_links) >= 2:
        business["Category"] = clean(breadcrumb_links[1].get_text())

    # ---- Description (full write-up) ----
    desc_container = soup.select_one(".post-detail-content")
    if desc_container:
        desc_text = clean(desc_container.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Address ----
    address_text = _gravitysplash_sidebar_value(soup, "lp-details-address")
    if address_text:
        # gravitysplash renders a plain comma-free "City ST Zipcode" string
        # (e.g. "Plano TX 75023"), not "street, city, state zip". Feeding
        # that into _split_blinx_address() mis-splits it into
        # state="Plano TX", zipcode="75023", city="" -- use the helper
        # built for exactly this shape instead.
        street, city, state, zipcode = _split_city_state_zip_address(address_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone  ----
    phone_link = soup.select_one("li.lp-listing-phone a[href^='tel:']")
    if phone_link:
        business["Phone"] = phone_link["href"].replace("tel:", "").strip()
    else:
        phone_text = _gravitysplash_sidebar_value(soup, "lp-listing-phone")
        if phone_text:
            business["Phone"] = phone_text

    # ---- Website URL ----
    website_link = soup.select_one("li.lp-user-web a[href]")
    if website_link:
        business["Website URL"] = website_link["href"]

    # ---- Social Media Links ----
    contact_list = None
    for li_class in ("lp-user-web", "lp-listing-phone", "lp-details-address"):
        anchor_li = soup.select_one(f"li.{li_class}")
        if anchor_li:
            contact_list = anchor_li.find_parent("ul")
            if contact_list:
                break

    if contact_list:
        social_list = contact_list.find_next_sibling("ul")
        if social_list:
            for a in social_list.find_all("a", href=True):
                href = a["href"]
                for domain, network in SOCIAL_DOMAINS.items():
                    if domain in href.lower():
                        business["Social Media Links"][network] = href

    # ---- Fallbacks from the embedded LocalBusiness JSON-LD, only for
    #      whichever fields the sidebar didn't already fill in ----
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("@type") != "LocalBusiness":
            continue
        if not business["Business Name"] and data.get("name"):
            business["Business Name"] = data["name"]
        if not business["Phone"] and data.get("telephone"):
            business["Phone"] = data["telephone"]
        break

    return business

























"letsknowit.com": ("requests", parse_letsknowit),
    "metriteweb.com": ("requests", parse_metriteweb),
    "qdexx.com": ("requests", parse_qdexx),
    "dbesearch.com": ("requests", parse_dbesearch),
    "locuul.com": ("requests", parse_locuul),
    "bpublic.com": ("requests", parse_bpublic),
    "smallbusinessusa.com": ("playwright", parse_smallbusinessusa),
    "zeemaps.com": ("api", parse_zeemaps),
    "callupcontact.com": ("requests", parse_callupcontact),