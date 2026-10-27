"""
Site parser: clickasnap.com (business listings posted as user profile bios)

The listing is the profile's bio, a flat "Label:" / value text block:

    Owner Name:
    Henry Purchase
    Address:
    131 Continental Dr, Suite 305, Newark, Delaware 19713
    Phone: ... Business Email: ... Website: ... About Us: ... Related Searches: ...

Where the bio can be read, best first:
  1. <meta name="description"> / og:description -- plain text, and the only
     place the e-mail is NOT obfuscated (the visible bio replaces it with
     Cloudflare's "[email protected]" placeholder).
  2. The "bio" string in the Next.js flight data (self.__next_f.push).
  3. The visible bio paragraph, decoding Cloudflare's data-cfemail e-mail.

Business name = profile display name (<h1>); logo = profile avatar
(og:image, the full-size S3 file; the page only shows an 80x80 thumbnail).
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py
from .medium import _split_address, _US_STATES

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


def _decode_cfemail(hex_str):
    """Decode Cloudflare's data-cfemail value (first byte is the XOR key)."""
    try:
        key = int(hex_str[:2], 16)
        return "".join(
            chr(int(hex_str[i:i + 2], 16) ^ key) for i in range(2, len(hex_str), 2)
        )
    except (ValueError, TypeError):
        return ""


def _bio_text(soup, html_text):
    """Return the raw bio text (newline separated), or ''."""
    for attrs in ({"name": "description"}, {"property": "og:description"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content") and "\n" in tag["content"]:
            return tag["content"]

    # Flight data: ..."bio":"Owner Name:\nHenry ..."  (a JSON string literal,
    # itself escaped once more inside the self.__next_f.push string).
    m = re.search(r'\\"bio\\":\\"((?:[^"\\]|\\\\.)*?)\\",\\"', html_text)
    if m:
        try:
            return json.loads('"' + m.group(1).replace('\\\\', '\\') + '"')
        except ValueError:
            pass

    p = soup.select_one('section[aria-label="User Biography"] p')
    if p:
        for a in p.select("a.__cf_email__[data-cfemail]"):
            a.replace_with(_decode_cfemail(a["data-cfemail"]))
        return p.get_text("\n")
    return ""


def _labelled_values(bio):
    """{canonical key: value} from 'Label:' lines followed by value lines."""
    values, key = {}, None
    for raw in bio.splitlines():
        line = raw.strip()
        if not line:
            continue
        label = line[:-1].strip().lower() if line.endswith(":") else None
        if label in _LABEL_KEYS:
            key = _LABEL_KEYS[label]
            values.setdefault(key, [])
            continue
        if key is not None:
            values[key].append(line)
    return {k: " ".join(v).strip() for k, v in values.items() if v}


def parse_clickasnap(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "lxml")

    # ---- Business Name ----
    h1 = soup.select_one("article header h1") or soup.find("h1")
    name = clean(h1.get_text()) if h1 else ""
    if not name:
        title = soup.find("title")
        if title:
            name = clean(re.sub(r"\s*\(@[^)]*\)\s*\|.*$", "", title.get_text()))
    business["Business Name"] = name

    # ---- Bio fields ----
    values = _labelled_values(_bio_text(soup, html_text))
    business["Owner Name"] = values.get("owner", "")

    street, city, state, zipcode = _split_address(values.get("address", ""))
    business["Street"], business["City"] = street, city
    business["State"], business["Zipcode"] = state, zipcode
    if zipcode and state.lower() in _US_STATES:
        business["Country"] = "United States"

    business["Phone"] = values.get("phone", "")
    business["Website URL"] = values.get("website", "")
    business["Business Email"] = values.get("email", "")
    business["Description"] = values.get("about", "")
    business["Keywords"] = values.get("keywords", "")

    # Website: prefer the real href when the bio paragraph linkified it.
    if not business["Website URL"]:
        link = soup.select_one('section[aria-label="User Biography"] a[href^="http"]')
        if link:
            business["Website URL"] = link["href"]

    # ---- Logo (profile avatar) ----
    og = soup.find("meta", property="og:image")
    logo = og.get("content", "") if og else ""
    if not logo:
        img = soup.select_one('img[alt="User Avatar"]')
        logo = img.get("src", "") if img else ""
    if logo:
        business["Logo"] = urljoin(url, logo)

    return filter_business_fields(business, url)