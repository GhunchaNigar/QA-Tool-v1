"""
Site parser: sites.bubblelife.com (BubbleLife Connect / Advice Local business sites)

URL shape: https://sites.bubblelife.com/community/<slug>

Server-rendered ASP.NET page. The listing data sits in a "Contact Info"
widget marked up with schema.org microdata:

    <span itemprop="streetAddress">320 W Main St Suite #200</span>
    <span itemprop="addressLocality">Murfreesboro</span>, <span itemprop="addressRegion">TN</span>
    <span itemprop="postalCode">37130</span>
    <span itemprop="telephone">(615) 900-6873</span>
    <a itemprop="url" href="https://totaltrans4mation.com">

Not on the page, so left blank: Hours, Logo and Category. og:image is the
generic BubbleLife logo (never the business's), and the page has no
category field (the <meta name=DESCRIPTION> service list is truncated at
~160 chars, so it is not used). Country is inferred as United States only
for a US state abbreviation + 5-digit ZIP.
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py

_US_STATE_ABBR = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}


def _itemprop(scope, name):
    tag = scope.find(attrs={"itemprop": name})
    return clean(tag.get_text(" ")) if tag else ""


def parse_bubblelife(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "lxml")

    # The listing lives in the Contact Info widget; fall back to the page.
    scope = soup.select_one(".widgetaddress") or soup

    # ---- Business Name ----
    name = _itemprop(scope, "name")
    if not name:
        link = soup.select_one(".dxmDomainTopText a")
        name = clean(link.get_text()) if link else ""
    if not name:
        og = soup.find("meta", property="og:title")
        name = clean(og.get("content", "")) if og else ""
    business["Business Name"] = name

    # ---- Address ----
    addr = scope.find(attrs={"itemprop": "address"}) or scope
    business["Street"] = _itemprop(addr, "streetAddress")
    business["City"] = _itemprop(addr, "addressLocality")
    business["State"] = _itemprop(addr, "addressRegion")
    business["Zipcode"] = _itemprop(addr, "postalCode")
    if re.fullmatch(r"\d{5}(-\d{4})?", business["Zipcode"]) and \
            business["State"].upper() in _US_STATE_ABBR:
        business["Country"] = "United States"

    # ---- Phone ----
    business["Phone"] = _itemprop(scope, "telephone")

    # ---- Website URL ----
    site = scope.find("a", attrs={"itemprop": "url"})
    if site:
        business["Website URL"] = site.get("href") or clean(site.get_text())

    # ---- Description ----
    desc = scope.find(attrs={"property": "description"})
    text = clean(desc.get_text(" ")) if desc else ""
    if not text:
        og = soup.find("meta", property="og:description")
        text = clean(og.get("content", "")) if og else ""
        if text.endswith("..."):  # og:description is truncated; not trustworthy
            text = ""
    business["Description"] = text

    # ---- Business Email (footer mailto, when present) ----
    mail = soup.select_one(".PageFooterEmail a[href^='mailto:']")
    if mail:
        business["Business Email"] = mail["href"][7:].split("?")[0]

    return filter_business_fields(business, url)