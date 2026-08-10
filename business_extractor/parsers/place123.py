"""
Site parser: place123.net
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



_PLACE123_LABELS = {
    "owner name": "Owner Name",
    "phone": "Phone",
    "website": "Website URL",
    "url": "Website URL",
    "business email": "Business Email",
    "about us": "Description",
    "related searches": "Keywords",
    "hours": "Hours",
}

_PLACE123_TERMINATORS = {
    "what do you think about us?",
    "your nickname",
    "comments",
    "start a discussion",
    "places nearby",
    "edit business",
    "your business in this directory?",
    "add your business",
    "position on map",
    "gps coordinates",
    "find nearby",
    "street view",
    "write a review",
}


def parse_place123(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (og:title matches the visible heading) ----
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        business["Business Name"] = clean(og_title["content"])

    if not business["Business Name"]:
        h_tag = soup.find(re.compile(r"^h[1-6]$"))
        if h_tag:
            business["Business Name"] = clean(h_tag.get_text())

    # ---- Logo ----
    logo_img = soup.find("img", alt=re.compile("location logo", re.I))
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Whole-page text as lines  ----
    lines = [
        clean(line)
        for line in soup.get_text(separator="\n").split("\n")
    ]
    lines = [l for l in lines if l]
    label_keys = set(_PLACE123_LABELS.keys())

    # ---- Category / Address / Country (positional: the 3 lines right
    #      after the business-name heading) ----
    name_idx = None
    if business["Business Name"]:
        target = business["Business Name"].lower()
        for idx, line in enumerate(lines):
            if line.lower() == target:
                name_idx = idx
                break

    if name_idx is not None:
        if name_idx + 1 < len(lines) and lines[name_idx + 1].rstrip(":").lower() not in label_keys:
            business["Category"] = lines[name_idx + 1]

        if name_idx + 2 < len(lines):
            address_line = lines[name_idx + 2]
            # Address line is often comma-free (e.g. "Plano TX 75023"), so
            # try the comma-free "City ST Zipcode" splitter first and fall
            # back to the comma-aware splitter for lines that do have commas.
            street, city, state, zipcode = _split_city_state_zip_address(address_line)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

        if name_idx + 3 < len(lines) and lines[name_idx + 3].rstrip(":").lower() not in label_keys:
            business["Country"] = lines[name_idx + 3]

    # ---- Owner Name / Phone / Website / URL / Business Email / About Us /
    i = 0
    n = len(lines)
    while i < n:
        norm = lines[i].rstrip(":").strip().lower()

        if norm in label_keys:
            field = _PLACE123_LABELS[norm]

            j = i + 1
            value_lines = []
            while j < n:
                next_norm = lines[j].rstrip(":").strip().lower()
                if next_norm in label_keys or next_norm in _PLACE123_TERMINATORS:
                    break
                value_lines.append(lines[j])
                j += 1

            value = clean(" ".join(value_lines))
            if field and value:
                business[field] = value

            i = j
        else:
            i += 1

    # ---- Website URL fallback (visible external anchor) ----
    if not business["Website URL"] or not business["Website URL"].startswith("http"):
        business["Website URL"] = ""
        for a in soup.find_all("a", href=True):
            href = a["href"]

            if not href.startswith("http"):
                continue
            if "place123.net" in href.lower():
                continue
            if "graph.facebook.com" in href.lower():
                continue
            if "google.com" in href.lower() or "googleapis.com" in href.lower():
                continue
            if any(domain in href.lower() for domain in SOCIAL_DOMAINS):
                continue

            business["Website URL"] = href
            break

    # ---- Description fallback (meta description -- truncated SEO
    #      snippet of the same "About Us" copy, so About Us wins if present) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    return business


