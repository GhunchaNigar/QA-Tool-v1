"""
Site parser: enrollbusiness.com (country sub-domains such as au., us., uk.)

Server-rendered profile page. Everything is read from the "Home" tab markup:

  - Name       <h1> in the profile header
  - Address    <p class="address"> with itemprop spans (street-address,
               locality, region, postal-code, country-name)
  - Phone      first a.num under .phNumbers
  - Hours      .timeCntr (a "Not Available." placeholder means none)
  - Website    first link in .websites .links; other links there are socials
  - Category   .categories .name entries
  - Owner      .owner (placeholder "Not Available." means none)
  - Description <p class="about">
  - Logo       background-image of the profile image (.BThumbCntr .Img)

The page has no keyword list, so Keywords comes from <meta name="keywords">
when the site provides one and is otherwise blank.
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py

_SOCIAL_DOMAINS = {
    "facebook.com": "Facebook",
    "twitter.com": "Twitter",
    "x.com": "Twitter",
    "instagram.com": "Instagram",
    "linkedin.com": "LinkedIn",
    "youtube.com": "YouTube",
    "pinterest.com": "Pinterest",
    "tiktok.com": "TikTok",
}

_BG_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.I)


def _eb_available_text(node, head_class):
    """Text of a labelled row, or "" when the row only says Not Available."""
    if node is None or node.select_one(".NotAvail"):
        return ""
    clone = BeautifulSoup(str(node), "lxml")
    for head in clone.select("." + head_class):
        head.decompose()
    return clean(clone.get_text(" "))


def _eb_social_label(href):
    host = urlparse(href).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    for domain, label in _SOCIAL_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return label
    return ""


def parse_enrollbusiness(url, html):
    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Name ----
    h1 = soup.select_one(".BThumbCntr .Head h1") or soup.find("h1")
    business["Business Name"] = clean(h1.get_text()) if h1 else ""

    # ---- Owner ----
    business["Owner Name"] = _eb_available_text(soup.select_one(".InfoSec .owner"), "head")

    # ---- Address ----
    addr = soup.select_one("p.address")

    def _part(prop):
        node = addr.find(attrs={"itemprop": prop}) if addr else None
        return clean(node.get_text()) if node else ""

    business["Street"] = _part("street-address")
    business["City"] = _part("locality")
    business["State"] = _part("region")
    business["Zipcode"] = _part("postal-code")
    business["Country"] = _part("country-name")
    if not business["Country"]:
        code = soup.select_one("#jsCountryCode")
        if code and code.get("value"):
            try:
                import pycountry
                match = pycountry.countries.get(alpha_2=code["value"].upper())
                business["Country"] = match.name if match else code["value"]
            except Exception:
                business["Country"] = code["value"]

    # ---- Phone ----
    phone = soup.select_one(".phNumbers a.num")
    if phone:
        business["Phone"] = clean(phone.get_text())

    # ---- Website URL + Social Media Links ----
    for a in soup.select(".websites .links a[href]"):
        href = a["href"].strip()
        if not href.lower().startswith("http"):
            continue
        label = _eb_social_label(href)
        if label:
            business["Social Media Links"].setdefault(label, href)
        elif not business["Website URL"]:
            business["Website URL"] = href

    # ---- Category ----
    names = []
    for node in soup.select(".industryGroups .categories .name"):
        text = clean(node.get_text())
        if text and text.lower() not in (n.lower() for n in names):
            names.append(text)
    business["Category"] = ", ".join(names)

    # ---- Description ----
    about = soup.select_one("p.about")
    if about:
        business["Description"] = clean_multiline(
            BeautifulSoup(str(about).replace("<br/>", "\n").replace("<br>", "\n"), "lxml").get_text()
        )

    # ---- Hours ----
    business["Hours"] = _eb_available_text(soup.select_one(".timeCntr .time"), "head")

    # ---- Keywords ----
    meta = soup.find("meta", attrs={"name": "keywords"})
    if meta and meta.get("content"):
        business["Keywords"] = clean(meta["content"])

    # ---- Logo ----
    img = soup.select_one(".BThumbCntr .Img")
    if img and img.get("style"):
        m = _BG_URL_RE.search(img["style"])
        if m:
            business["Logo"] = urljoin(url, m.group(1))

    return business