from ..common import *


# Labels in the order they appear on the page. The colon is OPTIONAL:
# the guest template prints "Address:" while the fuller template prints a
# bare "Address" on its own line. Each label's value is "whatever text
# comes before the next label".
_LABELS = [
    ("Owner Name", "owner_name"),
    ("Address", "address"),
    ("Phone", "phone"),
    ("Website", "website"),
    ("Business Email", "email_text"),
    ("About Us", "about"),
    ("Related Searches", "keywords"),
]


def _find_info_container(soup):
    """The profile info block is a div whose class contains both a
    "UserProfile*" component name and "description" -- e.g.
    "UserProfileGuestView-module-scss-module__9PoEVa__description" on
    the logged-out ("guest") view, or
    "UserProfileHeader-module-scss-module__snOe4G__description" on the
    fuller view (used e.g. when the profile is public). Matching on
    "UserProfile" + "description" substrings, rather than one exact
    component name, covers both templates and survives Cake's
    per-build CSS module hash (the "__9PoEVa__"/"__snOe4G__" part)
    changing over time.

    IMPORTANT: if this match fails and the code falls through to the
    whole-page-text fallback below, the LAST label found (usually
    "Related Searches:") will have its value run all the way to the
    end of the page -- vacuuming up nav/sidebar/footer/JSON text into
    Keywords. Getting this match right matters a lot more than it
    looks like it would.
    """
    container = soup.find(
        "div",
        class_=lambda c: c and "UserProfile" in c and "description" in c,
    )
    if container is not None:
        return container

    # Fallback: whole page text, scripts/styles stripped so embedded
    # JSON (e.g. __NEXT_DATA__, which repeats this same description)
    # doesn't get parsed as if it were the real profile text.
    stripped = BeautifulSoup(str(soup), "html.parser")
    for tag in stripped(["script", "style"]):
        tag.decompose()
    return stripped


def _extract_labeled_segments(container):
    """Split the container's text by the known labels, returning
    {field_key: raw text between this label and the next}.

    A label only counts at the START of a line (so the word "Address" or
    "Phone" inside the description is ignored), with an optional trailing
    colon. Labels are searched in page order, each one after the previous."""
    text = container.get_text("\n")

    positions = []
    cursor = 0
    for label, key in _LABELS:
        m = re.compile(r"(?im)^[ \t]*" + re.escape(label) + r"[ \t]*:?").search(text, cursor)
        if m:
            positions.append((m.start(), m.end(), key))
            cursor = m.end()

    segments = {}
    for i, (start, label_end, key) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        segments[key] = text[label_end:end]

    return segments


_STREET_TYPES = (
    "street|st|road|rd|avenue|ave|av|drive|dr|crescent|cres|court|ct|lane|ln|"
    "place|pl|parade|pde|highway|hwy|boulevard|blvd|terrace|tce|way|close|cl|"
    "circuit|cct|grove|gr|square|sq|walk|esplanade|esp|loop|trail|row|path|"
    "mews|rise|ridge|view|green|gardens|gdns|broadway"
)


def _split_street_and_city(street):
    """Cake prints "12 hilltop crescent Burwood East" with no comma between
    street and suburb, so _split_blinx_address leaves it all in Street.
    Split after the LAST street-type word that still has words after it."""
    words = street.split()
    pattern = re.compile(r"^(?:%s)\.?$" % _STREET_TYPES, re.I)
    for i in range(len(words) - 2, 0, -1):
        if pattern.match(words[i]):
            return " ".join(words[: i + 1]), " ".join(words[i + 1 :])
    return street, ""


def parse_cake(url, html):
    soup = BeautifulSoup(html, "html.parser")
    business = empty_business()

    # ---- Business Name ----
    # The profile heading is the most reliable source; og:title
    # ("<Name> | Cake") is the fallback.
    name = ""
    heading = soup.find(
        "h2",
        class_=lambda c: c and "UserProfileGuestView" in c and "name" in c,
    )
    if heading:
        name = clean(heading.get_text())
    if not is_meaningful(name):
        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title and og_title.get("content"):
            name = re.sub(r"\s*\|\s*Cake\s*$", "", clean(og_title["content"]), flags=re.I).strip()
    business["Business Name"] = name

    # ---- Everything else lives in one unlabeled text block ----
    container = _find_info_container(soup)
    segments = _extract_labeled_segments(container)

    business["Owner Name"] = clean(segments.get("owner_name", ""))

    address = clean(segments.get("address", ""))
    if is_meaningful(address):
        street, city, state, zipcode = _split_blinx_address(address)
        if street and not city:
            street, city = _split_street_and_city(street)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    business["Phone"] = clean(segments.get("phone", ""))

    website = clean(segments.get("website", ""))
    if not is_meaningful(website):
        # The fuller (UserProfileHeader) template has no inline
        # "Website:" label -- the URL instead lives in its own widget,
        # an <a> whose class ends in "...websiteLink".
        link = soup.find("a", class_=lambda c: c and "websiteLink" in c)
        if link and link.get("href"):
            website = link["href"].strip()
    business["Website URL"] = website

    business["Description"] = clean(segments.get("about", ""))
    business["Keywords"] = clean(segments.get("keywords", ""))

    # ---- Business Email ----
    # Rendered via Cloudflare's [email protected] obfuscation right
    # after the "Business Email" label -- decode that first. The
    # plain-text segment after the label is just the CF placeholder
    # text, not a real address, so it's only a last-resort fallback.
    email = _find_cf_email(soup)
    if not is_meaningful(email):
        fallback = clean(segments.get("email_text", ""))
        if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", fallback):
            email = fallback
    business["Business Email"] = email

    # ---- Logo ----
    logo = ""
    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image and og_image.get("content"):
        logo = og_image["content"].strip()
    if not is_meaningful(logo):
        avatar = soup.select_one('img[alt*="Avatar" i]')
        if avatar and avatar.get("src"):
            logo = avatar["src"].strip()
    business["Logo"] = logo

    return filter_business_fields(business, url)