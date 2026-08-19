"""
Site parser: touchafro.com
"""
from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py

# When the "address" field on this template is comma-separated (the usual
# case, e.g. "2244 Faraday Ave #206, CA 92008"), the greedy
# "^(.*\S)\s+(\d{5}...)$" match applied to the last comma-split segment
# correctly captures just the State portion, since the Street was already
# split off by the comma.
#
# Some listings omit that comma entirely -- the address field is just
# "Street State Zip" with no separator at all (e.g.
# "2244 Faraday Ave #206 CA 92008"). City lives in its own field on this
# template, so it's never part of this string either way. In the
# no-comma case, addr_parts has only one element, so the same greedy
# regex is applied to the *whole* address string and swallows the street
# into "State" (it just captures everything before the trailing zip
# digits), leaving Street blank and State wrong. This regex anchors on a
# real 2-letter state abbreviation immediately before the zip instead, so
# Street and State get split correctly in the no-comma case.
_STREET_STATE_ZIP_NO_COMMA_RE = re.compile(
    r"^(?P<street>.+?)\s+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)$"
)


def parse_touchafro(url, html):
    soup = BeautifulSoup(html, "lxml")
    business = empty_business()
    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business
    # ---- Business Name ----
    name_el = soup.select_one(".reportHeading h3")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())
    # ---- Labeled customer_info rows, keyed by their own label text
    # (row div classes repeat across unrelated rows, so they aren't a
    # reliable way to tell rows apart) ----
    info = {}
    for row in soup.select(".customer_info > div"):
        label_el = row.find(class_="headings_extra")
        if not label_el:
            continue
        label = clean(label_el.get_text()).rstrip(":").strip().lower()
        parts = []
        for sib in label_el.next_siblings:
            if isinstance(sib, NavigableString):
                parts.append(str(sib))
            else:
                parts.append(sib.get_text())
        info[label] = clean(" ".join(parts))
    # ---- Address ----
    address = info.get("address", "")
    if address:
        addr_parts = [clean(p) for p in address.split(",")]
        if len(addr_parts) > 1:
            # Comma present -- Street was already split off from
            # "State Zip" by the comma, so the greedy trailing-zip match
            # on the last segment safely captures just the State.
            state_zip_match = re.match(r"^(.*\S)\s+(\d{5}(?:-\d{4})?)$", addr_parts[-1])
            if state_zip_match:
                business["State"] = state_zip_match.group(1)
                business["Zipcode"] = state_zip_match.group(2)
                business["Street"] = ", ".join(addr_parts[:-1])
            else:
                business["Street"] = address
        else:
            # No comma anywhere -- the address is a single
            # "Street State Zip" chunk. Anchor on a real state
            # abbreviation so Street doesn't get swallowed into State
            # (see _STREET_STATE_ZIP_NO_COMMA_RE docstring above).
            no_comma_match = _STREET_STATE_ZIP_NO_COMMA_RE.match(addr_parts[0])
            if no_comma_match:
                business["Street"] = no_comma_match.group("street")
                business["State"] = no_comma_match.group("state")
                business["Zipcode"] = no_comma_match.group("zip")
            else:
                business["Street"] = address
    if info.get("city"):
        business["City"] = info["city"]
    if info.get("country"):
        business["Country"] = info["country"]
    if info.get("phone"):
        business["Phone"] = info["phone"]
    if info.get("website"):
        business["Website URL"] = info["website"]
    if info.get("email"):
        business["Business Email"] = info["email"]
    # ---- Description  ----
    desc_el = soup.select_one(".description")
    if desc_el:
        desc_paragraphs = [
            clean(p.get_text()) for p in desc_el.find_all("p") if clean(p.get_text())
        ]
        if desc_paragraphs:
            business["Description"] = "\n".join(desc_paragraphs)
    # ---- Category ----
    category_el = soup.select_one(".category_meta a")
    if category_el:
        cat_text = clean(category_el.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text
    # ---- Logo (first gallery-slider image) ----
    logo_el = soup.select_one(".left_thumb.gall-img img[src]") \
        or soup.select_one(".fagsfacf-gallery-slide-inner img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])
    # ---- Social Media Links (the business's own "You can also find us
    # on" list -- NOT the footer's or share-widget's TouchAfro-owned
    # links) ----
    social_list = soup.select_one(".follow_social .social_link_btns")
    if social_list:
        for a in social_list.find_all("a", href=True):
            href = a["href"]
            for domain, network in SOCIAL_DOMAINS.items():
                if _hostname_matches_social_domain(href, domain):
                    business["Social Media Links"][network] = href
    return business
