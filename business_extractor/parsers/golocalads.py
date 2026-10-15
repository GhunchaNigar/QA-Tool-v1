"""
Site parser: golocalads.com  (ClassiPress ad listings, /ads/<slug>/)

Details live in a <ul> of <li id="cp_..."> rows ("<span>Label:</span> value"):
  cp_email_id, cp_contact_no, cp_website, cp_street, cp_city, cp_zipcode,
  cp_country, and the state row (whose id is a numeric custom-field id, so
  it is matched by its "State:" label).

  - The email is Cloudflare-obfuscated ("[email protected]"); it is decoded
    with the shared _find_cf_email() helper.
  - Description is the <p> after <h3 class="description-area">. (The page
    <meta> descriptions are truncated, so they are only a fallback.)
  - Category is the ad-category link in the breadcrumb.
  - Logo is the ad's main picture (#main-pic); the site's own header logo
    is ignored.
"""

from ..common import *  # noqa: F401,F403


def _gla_rows(soup):
    """{lowercased label without colon: value text} from the details list."""
    rows = {}
    for li in soup.select("div.bigright li"):
        label_el = li.find("span")
        if not label_el:
            continue
        label = clean(label_el.get_text()).rstrip(":").lower()
        value = clean(li.get_text(" ").replace(label_el.get_text(), "", 1))
        rows.setdefault(label, (li, value))
    return rows


def parse_golocalads(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "html.parser")
    rows = _gla_rows(soup)

    def val(label):
        return rows.get(label, (None, ""))[1]

    # ---- Name ----
    h1 = soup.select_one("h1.single-listing") or soup.find("h1")
    business["Business Name"] = clean(h1.get_text()) if h1 else ""

    # ---- Address ----
    business["Street"] = val("street")
    business["City"] = val("city")
    business["State"] = val("state")
    business["Zipcode"] = val("zip/postal code")
    business["Country"] = val("country")

    # ---- Phone ----
    business["Phone"] = val("contact no")

    # ---- Website URL ----
    site_li = rows.get("website", (None, ""))[0]
    link = site_li.find("a", href=True) if site_li else None
    if link and link["href"].strip().lower().startswith(("http://", "https://")):
        business["Website URL"] = link["href"].strip()
    else:
        text = val("website")
        if text.lower().startswith(("http://", "https://")):
            business["Website URL"] = text

    # ---- Business Email (Cloudflare-protected) ----
    email_li = rows.get("email id", (None, ""))[0]
    business["Business Email"] = (_find_cf_email(email_li) if email_li else "") or _find_cf_email(soup)
    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto:
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Description ----
    desc_head = soup.select_one("h3.description-area")
    desc = ""
    if desc_head:
        parts = []
        for sib in desc_head.find_next_siblings():
            if sib.name == "p":
                parts.append(clean_multiline(sib.decode_contents()))
            elif sib.name in ("h3", "div") and parts:
                break
        desc = "\n".join(p for p in parts if is_meaningful(p))
    if not desc:
        meta = soup.find("meta", attrs={"property": "og:description"})
        desc = clean(meta.get("content", "")) if meta else ""
    business["Description"] = desc

    # ---- Category (breadcrumb ad-category link) ----
    cat = soup.select_one('.breadcrumbs a[href*="/ad-category/"], .breadcrumb-trail a[href*="/ad-category/"]')
    if cat:
        business["Category"] = clean(cat.get_text())

    # ---- Logo (the ad's main picture) ----
    pic = soup.select_one("#main-pic img[src]")
    if pic:
        business["Logo"] = urljoin(url, pic["src"].strip())
    else:
        og = soup.find("meta", attrs={"property": "og:image"})
        if og and og.get("content"):
            business["Logo"] = urljoin(url, og["content"].strip())

    return filter_business_fields(business, url)