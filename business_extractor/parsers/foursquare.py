"""
Site parser: foursquare.com (also app.foursquare.com)

Cloudflare/bot checks block headless browsers here, so the worker opens a
visible Chrome window for this domain (HEADED_DOMAINS in playwright_worker.py).

Where each field lives on a venue page (/v/<slug>/<id>):
  Name      <h1 itemprop="name"> (fallback: og:title)
  Street    itemprop="streetAddress"
  City      itemprop="addressLocality"
  State     itemprop="addressRegion"
  Zipcode   itemprop="postalCode"
  Country   last line of the address block, e.g. "United States"
  Phone     itemprop="telephone"
  Website   the venue's own link in the details sidebar (itemprop="url" /
            the "urlBlock" row); Foursquare-internal links are ignored
  Category  .categories text (fallback: og:description "<Category> in <City>, <ST>")
  Photos    venue photos in the banner (.photosSection), enlarged to 600x600
  Logo      Foursquare has no business logo, so this stays blank (the only
            images are visitor photos and a generic category icon)
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py


def _meta(soup, **attrs):
    tag = soup.find("meta", attrs=attrs)
    return clean(tag.get("content", "")) if tag else ""


def _itemprop(soup, prop):
    tag = soup.find(attrs={"itemprop": prop})
    return clean(tag.get_text(" ")) if tag else ""


def _is_foursquare_link(href):
    host = urlparse(href).netloc.lower()
    return (not host) or host.endswith("foursquare.com") or host.endswith("4sqi.net") \
        or host.endswith("swarmapp.com")


def parse_foursquare(url, html):

    business = empty_business()

    if not html or _looks_blocked(html):
        return business

    soup = BeautifulSoup(html, "lxml")

    # ---- Business Name ----
    name = _itemprop(soup, "name")
    if not name:
        h1 = soup.find("h1")
        name = clean(h1.get_text(" ")) if h1 else ""
    business["Business Name"] = name or _meta(soup, property="og:title")

    # ---- Street / City / State / Zipcode / Country ----
    business["Street"] = _itemprop(soup, "streetAddress")
    business["City"] = _itemprop(soup, "addressLocality")
    business["State"] = _itemprop(soup, "addressRegion")
    business["Zipcode"] = _itemprop(soup, "postalCode")

    adr = soup.find(attrs={"itemprop": "address"})
    if adr:
        # The country is the bare text after the last <br>, outside any span.
        tail = [clean(str(n)) for n in adr.children if isinstance(n, NavigableString)]
        tail = [t for t in tail if t and t != ","]
        if tail:
            business["Country"] = tail[-1]

    # Fallbacks from the <meta> tags when the markup block is missing:
    #   keywords:       "Name, 9657 US-98 W, Miramar Beach, FL, ..., Category"
    #   og:description: "Real Estate Service in Miramar Beach, FL"
    og_desc = _meta(soup, property="og:description")
    m = re.match(r"^(.*?)\s+in\s+(.+?),\s*([A-Z]{2})$", og_desc)
    if m:
        if not business["City"]:
            business["City"] = clean(m.group(2))
        if not business["State"]:
            business["State"] = m.group(3)

    # ---- Phone ----
    business["Phone"] = _itemprop(soup, "telephone")

    # ---- Website URL ----
    website = ""
    for tag in soup.select('[itemprop="url"], .urlBlock a, a.url'):
        href = tag.get("href") or tag.get("content") or clean(tag.get_text(" "))
        if href and href.lower().startswith("http") and not _is_foursquare_link(href):
            website = href.strip()
            break
    business["Website URL"] = website

    # ---- Category ----
    cats = [clean(c.get_text(" ")) for c in soup.select(".categories .unlinkedCategory, .categories a")]
    cats = [c for c in cats if c]
    if not cats and m:
        cats = [clean(m.group(1))]
    business["Category"] = ", ".join(dict.fromkeys(cats))

    # ---- Photos ----
    photos = []
    for img in soup.select(".photosSection li.photo img"):
        src = img.get("src") or ""
        if "/img/general/" not in src:
            continue
        # .../img/general/200x200/<file> -> larger copy of the same photo
        src = re.sub(r"/img/general/\d+x\d+/", "/img/general/600x600/", src)
        if src not in photos:
            photos.append(urljoin(url, src))
    if not photos:
        og_img = _meta(soup, property="og:image")
        if og_img and "/img/general/" in og_img:
            photos.append(og_img)
    business["Photos"] = photos

    return business