"""
Site parser: tradeford.com  (us.tradeford.com, www.tradeford.com, ...)

Path in project:  business_extractor/parsers/tradeford.py

A TradeFord company page keeps the real listing data in one text block under
"Company Introduction", written as label / value lines separated by <br>:

    URL/Website *  -> https://...        Business Address * -> street
    Keywords       -> ...                                      City, ST
    Description    -> ...                                      zip
    Owner Name     -> ...                Business Email *   -> (Cloudflare-obfuscated)
    Business Phone *-> ...

Country and Industry (category) come from the header box; the name from
JSON-LD / the company heading; the logo from og:image.
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py
from ..common import _decode_cf_email, _find_cf_email


_LABELS = {
    "url/website": "website",
    "website": "website",
    "keywords": "keywords",
    "description": "description",
    "owner name": "owner",
    "business address": "address",
    "business email": "email",
    "business phone": "phone",
}

_ZIP_RE = re.compile(r"^(\d{5}(?:-\d{4})?|[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d|[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2})$")


def _label_key(line):
    text = clean(line).rstrip("*").strip().rstrip(":").strip().lower()
    return _LABELS.get(text)


def _intro_lines(soup):
    """Return the 'Company Introduction' block as a list of text lines, with
    Cloudflare-obfuscated e-mail addresses decoded."""
    heading = None
    for h in soup.find_all(["h4", "h3", "h2"]):
        if "company introduction" in clean(h.get_text()).lower():
            heading = h
            break
    if not heading or not heading.parent:
        return []

    block = BeautifulSoup(str(heading.parent), "lxml")
    for h in block.find_all(["h4", "h3", "h2"]):
        if "company introduction" in clean(h.get_text()).lower():
            h.decompose()
    for a in block.select("[data-cfemail]"):
        a.replace_with(_decode_cf_email(a.get("data-cfemail", "")) or "")
    for a in block.select('a[href*="/cdn-cgi/l/email-protection#"]'):
        a.replace_with(_decode_cf_email(a["href"].split("#", 1)[-1]) or "")
    for br in block.find_all("br"):
        br.replace_with("\n")
    # the generic ad <ins> slots carry no listing data
    for tag in block.find_all(["script", "ins", "style"]):
        tag.decompose()

    text = block.get_text()
    return [clean(line) for line in text.split("\n") if clean(line)]


def _group_by_label(lines):
    fields, current = {}, None
    for line in lines:
        key = _label_key(line)
        if key and key not in fields:
            current = key
            fields[current] = []
        elif current:
            fields[current].append(line)
    return fields


def _split_address(lines):
    """['25 Everett St', 'Woburn, MA', '01801'] -> street, city, state, zip"""
    street = city = state = zipcode = ""
    lines = [l for l in lines if l]
    if not lines:
        return street, city, state, zipcode

    # trailing zip / postal code on its own line, or at the end of the last line
    if _ZIP_RE.match(lines[-1]) and len(lines) > 1:
        zipcode = lines.pop()
    else:
        m = re.search(r"[\s,]+(\d{5}(?:-\d{4})?)$", lines[-1])
        if m:
            zipcode = m.group(1)
            lines[-1] = lines[-1][: m.start()]

    # "City, ST" line
    if len(lines) >= 2:
        city_line = lines.pop()
        parts = [p.strip() for p in city_line.split(",") if p.strip()]
        if len(parts) >= 2:
            city, state = ", ".join(parts[:-1]), parts[-1]
        else:
            city = city_line
    street = ", ".join(lines)
    return clean(street), clean(city), clean(state), clean(zipcode)


def parse_tradeford(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Label/value block ----
    fields = _group_by_label(_intro_lines(soup))

    def val(key):
        return clean(" ".join(fields.get(key, [])))

    business["Website URL"] = val("website")
    business["Keywords"] = val("keywords")
    business["Owner Name"] = val("owner")
    business["Phone"] = val("phone")
    business["Business Email"] = val("email")
    if business["Business Email"] and "@" not in business["Business Email"]:
        business["Business Email"] = ""
    business["Description"] = clean_multiline("\n".join(fields.get("description", [])))

    (business["Street"], business["City"],
     business["State"], business["Zipcode"]) = _split_address(list(fields.get("address", [])))

    # ---- Email fallback (anywhere on the page) ----
    if not business["Business Email"]:
        mail = soup.select_one('a[href^="mailto:"]')
        if mail:
            business["Business Email"] = mail["href"].replace("mailto:", "").split("?")[0].strip()
        else:
            business["Business Email"] = _find_cf_email(soup)

    # ---- Business Name: JSON-LD Organization -> hidden input -> <h1> ----
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = (script.string or script.get_text() or "").strip()
        try:
            data = json.loads(raw, strict=False)
        except Exception:
            continue
        for obj in (data if isinstance(data, list) else [data]):
            if isinstance(obj, dict) and obj.get("@type") == "Organization" and obj.get("name"):
                business["Business Name"] = clean(str(obj["name"]))
                break
        if business["Business Name"]:
            break
    if not business["Business Name"]:
        company_input = soup.select_one("input#company[value]")
        if company_input:
            business["Business Name"] = clean(company_input["value"])
    if not business["Business Name"]:
        h1 = soup.select_one("h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Header box rows: Country / Industry ----
    for row in soup.select(".cgrid"):
        label_el = row.select_one(".cgrid_sub1")
        value_el = row.select_one(".cgrid_sub2")
        if not label_el or not value_el:
            continue
        label = clean(label_el.get_text()).lower()
        value = clean(value_el.get_text())
        if label == "country" and value and not business["Country"]:
            business["Country"] = value
        elif label == "industry" and value and not business["Category"]:
            business["Category"] = value

    # ---- Category fallback: breadcrumb (position 2 = industry) ----
    if not business["Category"]:
        for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
            try:
                data = json.loads((script.string or "").strip(), strict=False)
            except Exception:
                continue
            for obj in (data if isinstance(data, list) else [data]):
                if isinstance(obj, dict) and obj.get("@type") == "BreadcrumbList":
                    items = obj.get("itemListElement") or []
                    if len(items) >= 3:
                        name = (items[1].get("item") or {}).get("name", "")
                        business["Category"] = clean(name)

    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])
    if not business["Logo"]:
        a = soup.select_one("a.gal_lightbox[href]")
        img = soup.select_one("img.thumb_lrg[src]")
        if a:
            business["Logo"] = urljoin(url, a["href"])
        elif img:
            business["Logo"] = urljoin(url, img["src"])

    return business