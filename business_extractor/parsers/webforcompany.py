"""
Site parser: webforcompany.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


_WEBFORCOMPANY_LABELS = {
    "business name": "Business Name",
    "owner name": "Owner Name",
    "phone": "Phone",
    "website": "Website URL",
    "business email": None,  # real value comes from _find_cf_email, not this text
    "about us": "Description",
    "related searches": "Keywords",
    "hours": "Hours",
    "business hours": "Hours",
}


def parse_webforcompany(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Logo (real per-business header image, when uploaded) ----
    logo_img = soup.select_one(".navbar-brand img")
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])

    # ---- Locate the label/value block (homepage shape, then about.php shape) ----
    scope = soup.select_one(".about p")
    if not scope:
        scope = soup.select_one(".aboutus .col-md-12")
    if not scope:
        return business

    # ---- Website URL (real href, not the label's visible text) ----
    for a in scope.find_all("a", href=True):
        href = a["href"]
        if "cdn-cgi/l/email-protection" in href.lower():
            continue
        if href.startswith("http"):
            business["Website URL"] = href
            break

    # ---- Business Email (Cloudflare-obfuscated placeholder text) ----
    email = _find_cf_email(scope)
    if email:
        business["Business Email"] = email

    # ---- Flat label-then-value scan for everything else ----
    lines = [clean(line) for line in scope.get_text(separator="\n").split("\n")]
    lines = [l for l in lines if l]
    label_keys = set(_WEBFORCOMPANY_LABELS.keys())

    i, n = 0, len(lines)
    while i < n:
        norm = lines[i].rstrip(":").strip().lower()

        if norm == "address":
            if i + 1 < n:
                # Rendered as a plain comma-free "City ST Zipcode" line
                # (e.g. "Plano TX 75023"), same shape gravitysplash.com
                # uses -- _split_blinx_address() mis-splits that into
                # state="Plano TX", city="", so use the dedicated helper.
                street, city, state, zipcode = _split_city_state_zip_address(lines[i + 1])
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode
            i += 2
            continue

        if norm in label_keys:
            field = _WEBFORCOMPANY_LABELS[norm]

            j = i + 1
            value_lines = []
            while j < n:
                next_norm = lines[j].rstrip(":").strip().lower()
                if next_norm in label_keys or next_norm == "address":
                    break
                value_lines.append(lines[j])
                j += 1

            value = clean(" ".join(value_lines))
            if field and value:
                business[field] = value

            i = j
        else:
            i += 1

    # ---- Social Media Links / GBP Link (page-wide anchor scan) ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "webforcompany.com" in href.lower():
            continue
        if _is_maps_link(href):
            if not business["GBP Link"]:
                business["GBP Link"] = href
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business


