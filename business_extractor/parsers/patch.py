"""
Site parser: patch.com  (Patch business listings, e.g.
https://patch.com/florida/fort-myers/business/listing/473896/<slug>)

Path in project:  business_extractor/parsers/patch.py

Patch is a server-rendered Next.js site, so plain requests is enough.
Data sources, most reliable first:
  1. <script id="__NEXT_DATA__">  props.pageProps.mainContent.item
       business.{name,address,phone,url}, description (FULL text -- the
       visible card is clamped to 7 lines), category slug, categories,
       hoursOfOperation, imageThumbnail / images / headerImage
  2. <script type="application/ld+json"> LocalBusiness
       name, telephone, url, address, keywords, image
  3. the visible page: <h1>, tel: link, Address / Website rows,
       "Categories" chips, "About Us" paragraph

Notes
  * headerImage is the banner, NOT the logo. Logo = imageThumbnail / images[0].
  * hoursOfOperation is null on many listings -> Hours stays empty.
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py
from ..common import _is_maps_link


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _s(value):
    """Scalar -> cleaned string, anything else -> ''."""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value).strip()
        return clean(text) if text else ""
    return ""


def _next_item(soup):
    """props.pageProps.mainContent.item from __NEXT_DATA__ ({} if absent)."""
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        return {}
    raw = (script.string or script.get_text() or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw, strict=False)
    except Exception:
        return {}
    try:
        item = data["props"]["pageProps"]["mainContent"]["item"]
    except (KeyError, TypeError):
        return {}
    return item if isinstance(item, dict) else {}


def _listing_ld(soup):
    """The LocalBusiness JSON-LD object ({} if absent)."""
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw, strict=False)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop(0)
            if not isinstance(obj, dict):
                continue
            t = obj.get("@type")
            types = [str(x).lower() for x in (t if isinstance(t, list) else [t])]
            if any(x == "localbusiness" or x.endswith("business") for x in types):
                return obj
            if isinstance(obj.get("@graph"), list):
                stack.extend(obj["@graph"])
    return {}


def _format_phone(raw):
    """'2393993000' / '+1 239-399-3000' -> '(239) 399-3000' (what Patch shows).
    Anything that is not a 10/11-digit US number is returned unchanged."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return clean(raw or "")


def _slug_to_title(slug):
    """'home-repair' -> 'Home Repair'."""
    slug = (slug or "").strip().strip("/")
    if not slug:
        return ""
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", slug) if w)


def _first_image(value):
    """JSON-LD / JSON image field: str | list[str] | list[{url}] | {url}."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _s(value.get("url") or value.get("contentUrl"))
    if isinstance(value, list):
        for v in value:
            found = _first_image(v)
            if found:
                return found
    return ""


def _format_hours(hours):
    """hoursOfOperation is null on most listings. If Patch ever supplies it,
    flatten the common shapes to 'Mon: 9am-5pm; Tue: ...' and never raise."""
    if not hours:
        return ""
    if isinstance(hours, str):
        return clean(hours)
    parts = []
    if isinstance(hours, dict):
        for day, val in hours.items():
            if isinstance(val, (list, tuple)):
                val = ", ".join(_s(v) for v in val if _s(v))
            elif isinstance(val, dict):
                o = _s(val.get("open") or val.get("opens"))
                c = _s(val.get("close") or val.get("closes"))
                val = f"{o}-{c}" if o or c else ""
            else:
                val = _s(val)
            if val:
                parts.append(f"{_s(day)}: {val}")
    elif isinstance(hours, (list, tuple)):
        for entry in hours:
            if isinstance(entry, dict):
                day = _s(entry.get("day") or entry.get("dayOfWeek"))
                o = _s(entry.get("open") or entry.get("opens"))
                c = _s(entry.get("close") or entry.get("closes"))
                if day and (o or c):
                    parts.append(f"{day}: {o}-{c}")
                elif day and entry.get("closed"):
                    parts.append(f"{day}: Closed")
            else:
                text = _s(entry)
                if text:
                    parts.append(text)
    return "; ".join(parts)


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------
def parse_patch(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    item = _next_item(soup)
    biz = item.get("business") if isinstance(item.get("business"), dict) else {}
    baddr = biz.get("address") if isinstance(biz.get("address"), dict) else {}
    ld = _listing_ld(soup)
    laddr = ld.get("address") if isinstance(ld.get("address"), dict) else {}
    main = soup.select_one("main") or soup

    # ---- Name ----
    business["Business Name"] = (
        _s(biz.get("name")) or _s(ld.get("name")) or _s(item.get("title"))
    )
    if not business["Business Name"]:
        h1 = main.select_one("h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Address ----
    business["Street"] = _s(baddr.get("streetAddress")) or _s(laddr.get("streetAddress"))
    business["City"] = _s(baddr.get("city")) or _s(laddr.get("addressLocality"))
    business["State"] = _s(baddr.get("region")) or _s(laddr.get("addressRegion"))
    business["Zipcode"] = _s(baddr.get("postalCode")) or _s(laddr.get("postalCode"))
    # JSON-LD says "US", __NEXT_DATA__ says "USA"; the JSON-LD form is the
    # standard ISO code, so prefer it.
    business["Country"] = _s(laddr.get("addressCountry")) or _s(baddr.get("country"))

    if not business["Street"]:
        for row in main.select("div[role='button']"):
            label = row.find("span")
            if label and clean(label.get_text()).lower() == "address":
                spans = row.find_all("span")
                if len(spans) >= 2:
                    business["Street"] = clean(spans[1].get_text())
                break

    # ---- Phone ----
    phone_raw = _s(biz.get("phone")) or _s(ld.get("telephone"))
    if not phone_raw:
        tel = main.select_one('a[href^="tel:"]')
        if tel:
            phone_raw = tel["href"].replace("tel:", "").strip()
    business["Phone"] = _format_phone(phone_raw)

    # ---- Website URL ----
    website = _s(biz.get("url")) or _s(ld.get("url"))
    # ld["url"] is sometimes the Patch page itself on other listings; ignore that.
    if website and "patch.com" in urlparse(website).netloc.lower():
        website = ""
    if not website:
        for a in main.select('a[target="_blank"][href^="http"]'):
            href = a["href"].strip()
            if "patch.com" not in urlparse(href).netloc.lower():
                website = href
                break
    business["Website URL"] = website

    # ---- Keywords (JSON-LD: list or comma string) ----
    kw = ld.get("keywords")
    if isinstance(kw, list):
        kw = ", ".join(_s(k) for k in kw if _s(k))
    business["Keywords"] = _s(kw)

    # ---- Description: __NEXT_DATA__ holds the full text; the visible <p>
    #      is clamped to 7 lines, and JSON-LD / og:description is truncated
    #      with "...". Use them in that order. ----
    desc = _s(item.get("description"))
    if not desc:
        p = main.select_one("p.MuiTypography-body1 span")
        if p:
            desc = clean_multiline(p.get_text("\n"))
    if not desc:
        desc = _s(ld.get("description")) or _s(item.get("summary"))
    business["Description"] = clean_multiline(desc) if desc else ""

    # ---- Hours ----
    business["Hours"] = _format_hours(item.get("hoursOfOperation"))

    # ---- Category: slug in __NEXT_DATA__, chips on the page ----
    cats = []
    for c in (item.get("categories") or []):
        name = _s(c.get("name") if isinstance(c, dict) else c)
        if name and name not in cats:
            cats.append(name)
    if not cats:
        for chip in main.select(".MuiChip-label"):
            name = clean(chip.get_text())
            if name and name not in cats:
                cats.append(name)
    if not cats:
        title = _slug_to_title(_s(item.get("category")))
        if title:
            cats.append(title)
    business["Category"] = ", ".join(cats)

    # ---- Social + GBP: only if the business gave links on the listing ----
    for a in main.find_all("a", href=True):
        href = a["href"].strip()
        if not href.lower().startswith("http"):
            continue
        if "patch.com" in urlparse(href).netloc.lower():
            continue
        if not business["GBP Link"] and _is_maps_link(href):
            business["GBP Link"] = href
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"].setdefault(network, href)

    # ---- Logo (NOT headerImage -- that's the banner) ----
    logo = (
        _s(item.get("imageThumbnail"))
        or _first_image(item.get("images"))
        or _first_image(ld.get("image"))
    )
    if not logo:
        og = soup.find("meta", property="og:image")
        logo = og["content"].strip() if og and og.get("content") else ""
    if logo:
        business["Logo"] = urljoin(url, logo)

    return business