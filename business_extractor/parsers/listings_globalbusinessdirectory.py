"""
Site parser: listings.globalbusinessdirectory.us
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


def _listings_gbd_jsonld(soup):
    """Return the first LocalBusiness JSON-LD object on the page, if any."""
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string, strict=False)
        except Exception:
            continue
        objects = data if isinstance(data, list) else [data]
        for obj in objects:
            if isinstance(obj, dict) and obj.get("@type") == "LocalBusiness":
                return obj
    return None


def _looks_like_person_name(text):
    """
    Heuristic check for whether a short string is actually a person's name,
    as opposed to a niche/service descriptor (e.g. "personal injury lawyer",
    "divorce attorney", "family dentist").

    This theme's tagline field is used inconsistently by listing owners:
    sometimes it holds a real name, but far more often it holds either a
    keyword-stuffed SEO phrase (caught separately via the comma/word-count
    check) or a short, all-lowercase service description like the ones
    above. Those short descriptors slip past the keyword-stuffing check
    because they're short and comma-free, so a second, independent check
    is needed here.

    Real names in these listings are consistently capitalized ("Neera
    Truong", "John A. Smith"), while niche descriptors are consistently
    lowercase. So require every word to be capitalized before accepting
    the text as a name; also constrain to a plausible name-length word
    count and reject anything with digits.
    """
    if not text:
        return False

    words = text.split()

    # Real names are short: "First Last", "First Middle Last", possibly
    # with a suffix/initial. Reject anything outside a plausible range.
    if not (2 <= len(words) <= 5):
        return False

    for word in words:
        core = word.strip(".,")
        if not core:
            continue
        # Any digit rules out a name ("Plano38", "Suite 200", etc.)
        if any(ch.isdigit() for ch in core):
            return False
        # Every word must start with an uppercase letter -- this is the
        # key signal that rules out lowercase niche descriptors like
        # "personal injury lawyer".
        if not core[0].isupper():
            return False

    return True


def parse_listings_globalbusinessdirectory(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    jsonld = _listings_gbd_jsonld(soup)

    # ---- Business Name ----
    name_tag = soup.select_one("h1.case27-primary-text")
    if name_tag:
        business["Business Name"] = clean(name_tag.get_text())
    if not business["Business Name"] and jsonld and jsonld.get("name"):
        business["Business Name"] = clean(jsonld["name"])

    # ---- Owner Name (rendered as the listing's "tagline") ----
    # Caution: this theme's "tagline" field is frequently filled with
    # either SEO keyword stuffing (e.g. "plano real estate agent, plano
    # realtor, homes for sale plano, ...") or a short, all-lowercase
    # niche/service descriptor (e.g. "personal injury lawyer") rather
    # than an actual person's name.
    #
    # Two independent checks guard against this:
    #   1. Keyword-stuffing check: reject if it has a comma or runs
    #      long (>6 words) -- catches the SEO-phrase case.
    #   2. Name-shape check (_looks_like_person_name): reject unless
    #      every word is capitalized and the word count is in a
    #      plausible name range -- catches the short lowercase
    #      descriptor case, which the check above alone does not.
    owner_tag = soup.select_one("h2.profile-tagline")
    if owner_tag:
        owner_text = clean(owner_tag.get_text())
        looks_like_keyword_list = "," in owner_text or len(owner_text.split()) > 6
        if (
            is_meaningful(owner_text)
            and not looks_like_keyword_list
            and _looks_like_person_name(owner_text)
        ):
            business["Owner Name"] = owner_text

    # ---- Owner Name fallback: "Author" block ----
    # When the tagline isn't usable as a name (the common case) fall back
    # to the listing's Author block (.block-type-author .host-name). Note
    # this is the directory account that submitted the listing, not a
    # verified owner-name field -- it will often be a handle like
    # "RealEstate38" rather than a person's actual name, so treat it as a
    # best-effort fallback only.
    if not business["Owner Name"]:
        author_tag = soup.select_one(".block-type-author .host-name")
        if author_tag:
            author_text = clean(author_tag.get_text())
            if is_meaningful(author_text):
                business["Owner Name"] = author_text

    # ---- Address (Street / City / State / Zipcode) ----
    addr_tag = soup.select_one(".map-block-address p")
    addr_text = clean(addr_tag.get_text()) if addr_tag else ""
    if not addr_text and jsonld:
        addr_obj = jsonld.get("address")
        if isinstance(addr_obj, dict) and addr_obj.get("address"):
            addr_text = clean(addr_obj["address"])
        elif isinstance(addr_obj, str):
            addr_text = clean(addr_obj)
    if addr_text:
        street, city, state, zipcode = _split_listings_gbd_address(addr_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Country (rendered as a "Region" block) ----
    region_tag = soup.select_one(".block-type-terms .pf-body li a span")
    if region_tag:
        region_text = clean(region_tag.get_text())
        if is_meaningful(region_text):
            business["Country"] = region_text

    # ---- Contact Information block: Email / Phone / Website ----
    for li in soup.select(".block-type-details .pf-body li"):
        icon = li.find("i")
        value_tag = li.select_one("span.wp-editor-content")
        if not icon or not value_tag:
            continue
        icon_classes = icon.get("class", [])
        value_text = clean(value_tag.get_text())
        if not value_text:
            continue
        if "email" in icon_classes:
            business["Business Email"] = value_text
        elif "phone" in icon_classes:
            business["Phone"] = value_text
        elif "web" in icon_classes:
            business["Website URL"] = value_text

    if not business["Business Email"] and jsonld and jsonld.get("email"):
        business["Business Email"] = clean(jsonld["email"])
    if not business["Phone"] and jsonld and jsonld.get("telephone"):
        business["Phone"] = clean(jsonld["telephone"])

    # Fallback: the "Website" quick-action button near the top of the page
    # (icon class "fa-link", href to the business's own external site).
    if not business["Website URL"]:
        for a in soup.select(".lmb-calltoaction a[href], .quick-listing-actions a[href]"):
            href = a.get("href", "")
            if href.startswith("http") and a.find("i", class_="fa-link"):
                business["Website URL"] = href
                break

    # ---- Description ----
    desc_tag = soup.select_one(".block-type-text .pf-body p")
    if desc_tag:
        desc_text = clean(desc_tag.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text
    if not business["Description"] and jsonld and jsonld.get("description"):
        stripped = re.sub(r"<[^>]+>", " ", jsonld["description"])
        stripped = clean(stripped)
        if is_meaningful(stripped):
            business["Description"] = stripped

    # ---- Category ----
    cat_names = [clean(s.get_text()) for s in soup.select(".block-type-categories .category-name")]
    cat_names = [c for c in cat_names if c]
    if cat_names:
        business["Category"] = ", ".join(cat_names)

    # ---- Hours (theme renders this as its own block, when present) ----
    # Confirmed on neera-truong-real-estate: the actual markup class is
    # "block-type-work_hours" (not "block-type-hours" or
    # "block-type-business_hours" as previously assumed), so the old
    # selector never matched and Hours came back blank even when the
    # page had a populated weekly schedule.
    hours_tag = soup.select_one(
        ".block-type-work_hours .pf-body, "
        ".block-type-hours .pf-body, "
        ".block-type-business_hours .pf-body"
    )
    if hours_tag:
        hours_text = clean_multiline(hours_tag.get_text(separator="\n"))
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Social Media Links ----
    content_scope = soup.select_one("#c27-single-listing") or soup
    own_domain = urlparse(url).netloc.lower().replace("www.", "")
    for a in content_scope.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if own_domain in href.lower():
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href

    return business
