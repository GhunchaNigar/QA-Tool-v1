"""
Site parser: medium.com (business listings posted as Medium stories)

These posts are a flat sequence of paragraphs where a bold label paragraph
is followed by its value paragraph:

    <p><strong>Owner Name:</strong></p>   <p>Talin Mouradian</p>
    <p><strong>Address:</strong></p>      <p>11611 Dona Alicia PL, Studio City, California 91604</p>
    ...

Labels seen so far: Owner Name, Address, Phone, Website, Business Email,
About US, Related Searches. The story title is the business name. The only
image is the story's og/ld+json image (probably a banner), so it is returned
in Photos, not Logo. Hours, social links and GBP link are not on these posts.

Medium sits behind Cloudflare: plain `requests` gets a 403 "Just a moment"
page. dispatch.py therefore fetches this site with curl_cffi (Chrome TLS
fingerprint) and falls back to Playwright if that is blocked.
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py

_LABEL_KEYS = {
    "owner name": "owner",
    "owner": "owner",
    "address": "address",
    "phone": "phone",
    "website": "website",
    "business email": "email",
    "email": "email",
    "about us": "about",
    "about": "about",
    "related searches": "keywords",
}

_ADDRESS_RE = re.compile(
    r"^(?P<street>.*?),\s*(?P<city>[^,]+),\s*(?P<state>[A-Za-z][A-Za-z .]+?)\s+"
    r"(?P<zip>\d{5}(?:-\d{4})?)$"
)

# Only used to decide whether a 5-digit ZIP means the page is a US address.
_US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
    "district of columbia",
}


def _labelled_values(soup):
    """{canonical key: value text} from 'bold label paragraph -> value paragraph'."""
    paras = soup.select("p.pw-post-body-paragraph") or soup.select("article p")
    values = {}
    for i, p in enumerate(paras):
        strong = p.find("strong")
        if not strong or i + 1 >= len(paras):
            continue
        label = clean(strong.get_text()).rstrip(":").strip().lower()
        key = _LABEL_KEYS.get(label)
        if not key or key in values:
            continue
        # The label paragraph must contain only the label, otherwise it is a
        # normal sentence with a bold word in it.
        if clean(p.get_text()).rstrip(":").strip().lower() != label:
            continue
        nxt = paras[i + 1]
        link = nxt.find("a", href=True)
        text = clean(nxt.get_text(" "))
        if key == "email" and link and link["href"].lower().startswith("mailto:"):
            text = link["href"][7:].split("?")[0]
        elif key == "website" and link:
            text = link["href"]
        values[key] = text
    return values


def _split_address(address):
    street = city = state = zipcode = ""
    m = _ADDRESS_RE.match(address or "")
    if m:
        street, city, state, zipcode = (clean(m.group(k)) for k in ("street", "city", "state", "zip"))
    elif address:
        street = address
    return street, city, state, zipcode


def _story_images(soup):
    """Story image URLs (ld+json first, then the og <source>), de-duplicated."""
    urls = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "{}")
        except (ValueError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            images = item.get("image") if isinstance(item, dict) else None
            if isinstance(images, str):
                images = [images]
            urls.extend(u for u in (images or []) if isinstance(u, str))
    og = soup.select_one("source[data-testid='og']")
    if og and og.get("srcset"):
        first = og["srcset"].split(",")[0].strip().split(" ")[0]
        if first.startswith("http"):
            urls.append(first)
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:1]  # the og source is just a resized copy of the ld+json image


def parse_medium(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "html.parser")

    title = soup.select_one("h1[data-testid='storyTitle']") or soup.find("h1")
    business["Business Name"] = clean(title.get_text()) if title else ""

    values = _labelled_values(soup)
    business["Owner Name"] = values.get("owner", "")

    street, city, state, zipcode = _split_address(values.get("address", ""))
    business["Street"], business["City"], business["State"], business["Zipcode"] = street, city, state, zipcode
    # The post never states a country; infer it only for a US state + ZIP.
    if zipcode and state.lower() in _US_STATES:
        business["Country"] = "United States"

    business["Phone"] = values.get("phone", "").replace("\u2013", "-").replace("\u2014", "-")
    business["Website URL"] = values.get("website", "")
    business["Business Email"] = values.get("email", "")
    business["Description"] = values.get("about", "")
    business["Keywords"] = values.get("keywords", "")
    business["Photos"] = _story_images(soup)

    return filter_business_fields(business, url)