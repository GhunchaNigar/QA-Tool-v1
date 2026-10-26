"""
Site parser: nearfinderus.com

Cloudflare blocks plain requests, curl_cffi and headless Chromium on this
site, so dispatch.py fetches it with the "headed" method (a visible Chrome
window, see business_extractor/headed_fetch.py).

Where each field lives on a listing page:
  Name         JSON-LD LocalBusiness "name" (fallback: <h1> / og:title)
  Street       <address> block, first line (fallback: JSON-LD streetAddress)
  City, State  <address> block link text "Miramar Beach - FL"
  Zipcode      <address> block zip link (fallback: JSON-LD postalCode)
  Country      JSON-LD addressCountry
  Phone        tel: link in the Phone number card
  Website URL  "Website" button; the real URL is the ?url= value of the
               /en/empresa/redirect link
  Social links any other redirect button in the same "Social / Internet" list
  Description  text="" attribute of <nf-show-more-text> (fallback: meta description)
  Hours        table.table-horario-funcionamento
  Category     links under the "Categories" heading (fallback: breadcrumb)
  Logo         og:image (fallback: JSON-LD image), only if it is a real
               uploaded logo and not a placeholder
Business Email  mailto: link / data-cfemail / JSON "email" when the page publishes
               one. The "Message - Email the business" row is a contact form on
               many listings, in which case there is no address to extract.
Owner Name and GBP Link are not published on these pages
(the Maps button is just an address search, not a Business Profile).
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py
from urllib.parse import unquote


_DAY_RE = re.compile(
    r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)$", re.I
)
_TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*(?:to|-|–)\s*\d{1,2}:\d{2}", re.I)

_SITE_HOSTS = ("nearfinderus.com",)
_JUNK_HOSTS = (
    "google.", "gstatic.", "googletagmanager.", "googlesyndication.", "doubleclick.",
    "cloudflare.", "apple.com", "play.google", "schema.org", "w3.org",
)
_URL_PARAMS = ("url", "u", "link", "target", "website", "to", "redirect", "dest")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_BAD_EMAIL_DOMAINS = ("nearfinderus.com", "example.com", "sentry.io", "schema.org")


def _is_external(target):
    host = (urlparse(target).hostname or "").lower()
    if not host or not target.lower().startswith("http"):
        return False
    if any(host == h or host.endswith("." + h) for h in _SITE_HOSTS):
        return False
    return not any(j in host for j in _JUNK_HOSTS)


def _unwrap(href):
    """Return the real target of a redirect-style link, else the href itself."""
    href = html_lib.unescape((href or "").strip())
    q = parse_qs(urlparse(href).query)
    for key in _URL_PARAMS:
        val = (q.get(key) or [""])[0]
        val = unquote(val).strip()
        if val.lower().startswith("http"):
            return val
    return href


def _good_email(value):
    value = clean(unquote(value or "")).strip().lower()
    value = value.split("?")[0]
    if not _EMAIL_RE.fullmatch(value):
        return ""
    if any(value.endswith("@" + d) or value.endswith("." + d) for d in _BAD_EMAIL_DOMAINS):
        return ""
    return value


def _label_of(tag):
    """Visible text + aria-label/title of the element and its parent, lower-cased."""
    parts = [tag.get_text(" "), tag.get("aria-label", ""), tag.get("title", "")]
    if tag.parent is not None and tag.parent.name not in ("body", "html"):
        parts.append(tag.parent.get_text(" ")[:120])
    return clean(" ".join(parts)).lower()


def _digits(s):
    return re.sub(r"\D", "", s or "")


def _json_values(html_text, keys):
    """Values of "key":"value" pairs found anywhere in the markup (JSON blobs in
    attributes / inline scripts of the Vue components)."""
    out = []
    raw = html_lib.unescape(html_text).replace("\\/", "/")
    for key in keys:
        for m in re.finditer(r'"%s"\s*:\s*"([^"]+)"' % re.escape(key), raw, re.I):
            out.append(m.group(1))
    return out


def _json_ld_local_business(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "LocalBusiness":
                return item
    return {}


def _meta(soup, **attrs):
    tag = soup.find("meta", attrs=attrs)
    return clean(tag.get("content", "")) if tag else ""


def _format_hours(soup):
    table = soup.select_one("table.table-horario-funcionamento, .table-horario-funcionamento table")
    if not table:
        return ""

    days = []  # [[day, [time, time, ...]], ...]
    for tr in table.find_all("tr"):
        cells = [clean(td.get_text(" ")) for td in tr.find_all("td")]
        cells = [c for c in cells if c and c != "\xa0"]
        if not cells:
            continue
        if _DAY_RE.match(cells[0]):
            times = [c for c in cells[1:] if _TIME_RE.search(c)]
            days.append([cells[0].capitalize(), times])
        elif days:
            # Extra time range for the previous day (split shifts use rowspan).
            days[-1][1].extend(c for c in cells if _TIME_RE.search(c))

    parts = []
    for day, times in days:
        if not times:
            parts.append(f"{day}: Closed")
            continue
        shown = []
        for t in times:
            # The site stores "open all day" as 00:00 to 00:00.
            if re.fullmatch(r"00:00\s*(?:to|-|–)\s*00:00", t):
                shown.append("Open 24 hours")
            else:
                shown.append(t)
        parts.append(f"{day}: {', '.join(shown)}")
    return "; ".join(parts)


def parse_nearfinderus(url, html):

    business = empty_business()

    if not html or _looks_blocked(html):
        return business

    soup = BeautifulSoup(html, "lxml")
    ld = _json_ld_local_business(soup)
    ld_addr = ld.get("address") or {}

    # ---- Business Name ----
    name = clean(ld.get("name", ""))
    if not name:
        h1 = soup.find("h1")
        if h1:
            # "ABBOTT MARTIN GROUP - MIRAMAR BEACH, FL" -> drop the city suffix
            name = clean(h1.get_text(" ")).split(" - ")[0]
    if not name:
        name = _meta(soup, property="og:title")
    business["Business Name"] = name

    # ---- Street / City / State / Zipcode ----
    addr = soup.find("address")
    street = city = state = zipcode = ""
    if addr:
        span = addr.find("span", class_="float-right") or addr
        first_line = next(
            (clean(str(s)) for s in span.children if isinstance(s, NavigableString) and clean(str(s)) not in ("", "-")),
            "",
        )
        street = first_line
        links = span.find_all("a")
        if links:
            # "Miramar Beach - FL"
            m = re.match(r"^(.*?)\s+-\s+([A-Za-z]{2})$", clean(links[0].get_text(" ")))
            if m:
                city, state = clean(m.group(1)), m.group(2).upper()
        for a in links:
            if re.fullmatch(r"\d{5}(?:-\d{4})?", clean(a.get_text())):
                zipcode = clean(a.get_text())
    if not state:
        # New layout: "9657 US-98 W, Miramar Beach - FL - 32550" (address row / map caption)
        m = re.search(r"\s-\s([A-Z]{2})\s-\s\d{5}\b", soup.get_text(" "))
        if m:
            state = m.group(1)
    if not state:
        # ...or the state code in the page's own links: /state_fl+city_miramar-beach.html
        m = re.search(r"/state_([a-z]{2})\+city_", html)
        if m:
            state = m.group(1).upper()
    street = street or clean(ld_addr.get("streetAddress", ""))
    zipcode = zipcode or clean(ld_addr.get("postalCode", ""))
    if not city:
        # JSON-LD locality looks like "Miramar Beach-Florida"
        city = clean(ld_addr.get("addressLocality", "")).rsplit("-", 1)[0].strip()
    business["Street"] = street
    business["City"] = city
    business["State"] = state
    business["Zipcode"] = zipcode
    business["Country"] = clean(ld_addr.get("addressCountry", "")) or "US"

    # ---- Phone ----
    # The "Call" button is a tel: link whose *text* is just "Call", so prefer the
    # number in the href. The sidebar card shows a masked "(850) 460-••••".
    phone = ""
    tel_links = soup.find_all("a", href=re.compile(r"^tel:", re.I))
    # The "phone number" dialog link shows the full formatted number, e.g. "(850) 460-2900"
    tel_links.sort(key=lambda a: 0 if len(_digits(clean(a.get_text(" ")))) >= 7 else 1)
    for a in tel_links:
        num = unquote(a["href"][4:]).strip()
        shown = clean(a.get_text(" "))
        if len(_digits(shown)) >= 7 and "\u2022" not in shown:
            phone = shown
            break
        if len(_digits(num)) >= 7:
            phone = num
            break
    if not phone:
        phone = clean(ld.get("telephone", ""))
    if not phone:
        for v in _json_values(html, ("telephone", "phone", "phoneNumber")):
            if len(_digits(v)) >= 7 and "\u2022" not in v:
                phone = v
                break
    business["Phone"] = phone

    # ---- Website URL / Social Media Links / Business Email ----
    # The page layout changed: the website and the e-mail are now sidebar rows
    # ("Business website", "Message - Email the business") instead of the old
    # "Social / Internet" list, so scan every link/attribute on the page.
    website = ""
    email = ""

    def _add_target(target, label=""):
        nonlocal website
        if not target.lower().startswith("http") or not _is_external(target):
            return
        network = next(
            (n for d, n in SOCIAL_DOMAINS.items() if _hostname_matches_social_domain(target, d)),
            None,
        )
        if network:
            if "share" not in target.lower() and "sharer" not in target.lower():
                business["Social Media Links"].setdefault(network, target)
        elif not website and ("website" in label or "internet" in label or not label):
            website = target

    weak_sites = []  # external links not labelled "website" (last resort)
    for a in soup.find_all("a", href=True):
        href = html_lib.unescape(a["href"].strip())
        low = href.lower()
        if low.startswith("mailto:"):
            email = email or _good_email(href[7:])
            continue
        if low.startswith(("tel:", "javascript:", "#", "sms:")):
            continue
        target = _unwrap(href)
        label = _label_of(a)
        if target != href or "redirect" in low:
            _add_target(target, "website" if "website" in label else label or "website")
        elif _is_external(target):
            if "website" in label:
                _add_target(target, "website")
            elif not any(_hostname_matches_social_domain(target, d) for d in SOCIAL_DOMAINS):
                weak_sites.append(target)

    # data-* / component props carrying the value (Vue custom elements, buttons)
    for tag in soup.find_all(True):
        for attr, val in tag.attrs.items():
            if not isinstance(val, str) or not val:
                continue
            a_low = attr.lower()
            if not email and ("email" in a_low or "mail" in a_low) and a_low != "data-cfemail":
                email = _good_email(val)
            elif not website and any(k in a_low for k in ("website", "site", "url", "href", "link")):
                if a_low in ("href", "src", "action", "content", "data-src", "srcset"):
                    continue
                _add_target(_unwrap(val), "website")

    # JSON blobs ("website":"...", "email":"...") inside attributes / scripts
    for v in _json_values(html, ("website", "websiteUrl", "website_url", "site", "web")):
        if not website:
            _add_target(_unwrap(v), "website")
    if not email:
        for v in _json_values(html, ("email", "emailAddress", "email_address", "mail")):
            email = _good_email(v)
            if email:
                break

    # JSON-LD
    if not website:
        _add_target(_unwrap(clean(ld.get("url", ""))), "website")
    if not email:
        email = _good_email(ld.get("email", ""))
    for sa in (ld.get("sameAs") or []) if isinstance(ld.get("sameAs"), list) else [ld.get("sameAs") or ""]:
        if isinstance(sa, str):
            _add_target(sa, "website")

    # Cloudflare-obfuscated address ("[email protected]")
    if not email:
        email = _good_email(_find_cf_email(soup))

    # Old layout fallback: the "Social / Internet" list.
    if not website and not email:
        heading = soup.find(
            lambda t: t.name in ("h2", "h3") and clean(t.get_text()).lower() == "social / internet"
        )
        scope = heading.find_next("ul") if heading else None
        for a in (scope.find_all("a", href=True) if scope else []):
            _add_target(_unwrap(a["href"]), "website")

    if not website and len(weak_sites) == 1:
        website = weak_sites[0]

    business["Website URL"] = website
    business["Business Email"] = email

    # ---- Description ----
    desc_tag = soup.find("nf-show-more-text")
    description = clean(desc_tag.get("text", "")) if desc_tag else ""
    if not description:
        meta_desc = _meta(soup, name="description")
        # Meta description is prefixed with "<address> - Business specializing in X. "
        description = re.sub(r"^.*?Business specializing in [^.]*\.\s*", "", meta_desc)
    business["Description"] = description

    # ---- Hours ----
    business["Hours"] = _format_hours(soup)

    # ---- Category ----
    cat_heading = soup.find(
        lambda t: t.name in ("h2", "h3") and clean(t.get_text()).lower() == "categories"
    )
    categories = []
    if cat_heading:
        ul = cat_heading.find_next("ul")
        if ul:
            categories = [clean(a.get_text(" ")) for a in ul.find_all("a") if clean(a.get_text(" "))]
    if not categories:
        crumb = soup.find("a", href=re.compile(r"/business-directory/category_"))
        if crumb:
            categories = [clean(crumb.get_text(" "))]
    business["Category"] = ", ".join(dict.fromkeys(categories))

    # ---- Logo ----
    ld_image = ld.get("image", "")
    if isinstance(ld_image, list):
        ld_image = ld_image[0] if ld_image else ""
    header_img = soup.select_one("img.emp-cab__logo")
    for cand in (
        _meta(soup, property="og:image"),
        clean(ld_image).replace("\\/", "/"),
        header_img.get("src", "") if header_img else "",
    ):
        if cand and "/images/logos/" in cand:
            business["Logo"] = urljoin(url, cand)
            break

    return business