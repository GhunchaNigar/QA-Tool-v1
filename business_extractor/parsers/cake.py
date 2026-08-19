from ..common import *


# Order doesn't matter for correctness (positions get sorted), but this
# mirrors the order fields appear on the page. Each label's value is
# "whatever text comes before the next label".
#
# NOTE: labels are stored WITHOUT a trailing colon. Cake renders these
# labels two different ways depending on which profile template is
# active:
#   - "guest view" template:  "Owner Name:", "Address:", "Phone:", ...
#   - fuller "header" template: "Owner Name", "Address", "Phone", ...
#     (no colon at all -- except "Business Email", which never had one)
# _find_label() below tries the colon form first, then the bare form,
# so both templates work off the same table.
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


def _find_label(text, label):
    """Find `label` in `text`, trying the colon form first ("Owner
    Name:") and falling back to the bare form ("Owner Name") -- Cake's
    two profile templates render labels differently (see _LABELS).
    Returns the index immediately AFTER the matched label (and colon,
    if present), i.e. where the label's value starts, or None if
    neither form is found.
    """
    for candidate in (label + ":", label):
        idx = text.find(candidate)
        if idx != -1:
            return idx + len(candidate)
    return None


def _extract_labeled_segments(container):
    """Split the container's text by the known labels, returning
    {field_key: raw text between this label and the next}."""
    text = container.get_text("\n")

    positions = []
    for label, key in _LABELS:
        value_start = _find_label(text, label)
        if value_start is not None:
            positions.append((value_start, key))
    positions.sort()

    segments = {}
    for i, (start, key) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        segments[key] = text[start:end]

    return segments


def parse_cake(url, html):
    soup = BeautifulSoup(html, "html.parser")
    business = empty_business()

    # ---- Business Name ----
    # The profile heading is the most reliable source; og:title
    # ("<Name> | Cake") is the fallback. Cake's two templates name
    # their heading <h2> differently -- "UserProfileGuestView...name"
    # on the guest view, "UserProfileHeader...name" on the fuller
    # view -- so match any h2 whose class belongs to a "UserProfile*"
    # component and ends in "name", rather than one exact class. This
    # matters because falling through to og:title on the fuller
    # template pulls in the headline too (e.g. "<Name> - personal
    # injury lawyer" instead of just "<Name>").
    name = ""
    heading = soup.find(
        "h2",
        class_=lambda c: c and "UserProfile" in c and c.rsplit("__", 1)[-1] == "name",
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
