"""
Parser for provenemployer.com employer profile pages.

Not to be confused with parsers.provenexpert (provenexpert.com) -- this is
a sister site under the same company (Expert Systems) with its own
template/markup for *employer* profiles rather than expert/freelancer
profiles, so it needs its own parser despite the similar domain name.

The page's own JSON-LD (`application/ld+json`) only carries a bare
Organization name + sameAs (website) -- not a full LocalBusiness/PostalAddress
block -- so this parser relies on the HTML markup as the primary source
and only falls back to the JSON-LD for Name/Website URL if those specific
HTML selectors ever come back empty.
"""

import re

from ..common import (
    BeautifulSoup,
    clean,
    empty_business,
    urljoin,
    SOCIAL_DOMAINS,
    _hostname_matches_social_domain,
    _extract_pe_cover,
    _extract_pe_hours,
    _extract_pe_gallery,
    pe_hours_need_render,
)

# Sharing-widget URL fragments that must NOT be treated as the business's
# own social media profiles. The profile page reuses the exact same
# facebook.com / twitter.com / linkedin.com / etc. hostnames for its
# "Share this profile" buttons, so a plain domain match against
# SOCIAL_DOMAINS would otherwise misattribute ProvenEmployer's own share
# links to the listed business.
_SHARE_WIDGET_URL_MARKERS = (
    "/sharer/sharer.php",
    "intent/tweet",
    "shareArticle",
    "spi/shares/new",
    "api.whatsapp.com/send",
    "mailto:?subject=Check out this",
)


def _is_share_widget_link(href):
    return any(marker in href for marker in _SHARE_WIDGET_URL_MARKERS)


_ZIP_TOKEN_RE = re.compile(r"^(?=.*\d)[A-Z0-9]{3,8}(?:-[A-Z0-9]{3,4})?$", re.I)


def _split_locality(text):
    """Split the locality line(s) into (city, state, zipcode).

    Handles both layouts seen on ProvenEmployer:
        US:  "Dover, Delaware (DE) 19901"     -> Dover / DE / 19901
        AU:  "3207 Port Melbourne ,VIC"        -> Port Melbourne / VIC / 3207
    The zipcode can lead or trail; the state is either a "(ABBR)" token or
    whatever follows the last comma.
    """
    text = clean(text)
    if not text:
        return "", "", ""

    tokens = text.split()
    zipcode = ""
    # Postcode first (AU/EU style) ...
    if tokens and _ZIP_TOKEN_RE.match(tokens[0]):
        zipcode = tokens.pop(0)
    # ... or last (US style).
    elif tokens and _ZIP_TOKEN_RE.match(tokens[-1]):
        zipcode = tokens.pop()
    rest = " ".join(tokens)

    state = ""
    paren = re.search(r"\(([^)]+)\)", rest)
    if paren:
        state = paren.group(1).strip()
        before = rest[:paren.start()].strip().rstrip(",").strip()
        # "Dover, Delaware" -> city is the part before the comma
        city = before.split(",")[0].strip() if "," in before else before
    elif "," in rest:
        city, _, state = rest.rpartition(",")
        city, state = city.strip().rstrip(",").strip(), state.strip()
    else:
        city = rest.strip()

    return clean(city), clean(state), zipcode


def _parse_address_block(address_tag):
    """Split the <address> block into street/city/state/zipcode/country.

    Template shape:
        <address>
            {street}<br>
            <div> {zip} {city} ,{state} ... <br></div>   <- locality
            {country}<br>
        </address>

    The inner <div> separates street (text before it) from country (text
    after it), so that structure is used when present. A flat text-node
    heuristic is kept as the fallback for pages without the <div>.
    """
    locality_div = address_tag.find("div")
    if locality_div:
        street_parts, country_parts = [], []
        target = street_parts
        for node in address_tag.children:
            if node is locality_div:
                target = country_parts
                continue
            text = node.get_text(" ") if hasattr(node, "get_text") else str(node)
            text = clean(text)
            if text:
                target.append(text)

        city, state, zipcode = _split_locality(" ".join(locality_div.stripped_strings))
        return (
            clean(" ".join(street_parts)),
            city,
            state,
            zipcode,
            clean(" ".join(country_parts)),
        )

    # ---- Fallback: flat list of text nodes ----
    parts = [clean(p) for p in address_tag.stripped_strings if clean(p)]
    if not parts:
        return "", "", "", "", ""
    country = parts.pop() if len(parts) > 1 else ""
    street = parts.pop(0) if len(parts) > 1 else ""
    city, state, zipcode = _split_locality(" ".join(parts))
    return street, city, state, zipcode, country


def _extract_description(container):
    """Build the full "about" text.

    The template splits the description into a visible lead-in, a "..."
    marker (span.textEtc), and the rest of the text hidden behind a
    "View full description" toggle (span.textRest, display:none). The
    hidden span still holds real content -- it's just CSS-collapsed, not
    actually absent -- so it belongs in the description; only the "..."
    marker itself and the two toggle links (collapseAboutme / foldAboutme)
    should be dropped.
    """
    paragraph = container.select_one(".welcomeTextParagraph")
    if not paragraph:
        return ""

    paragraph = BeautifulSoup(str(paragraph), "html.parser")
    for toggle in paragraph.select(".collapseAboutme, .foldAboutme"):
        toggle.decompose()
    for ellipsis in paragraph.select(".textEtc"):
        ellipsis.decompose()

    return clean(paragraph.get_text())


def parse_provenemployer(url, html):
    soup = BeautifulSoup(html, "html.parser")
    business = empty_business()

    name_tag = soup.select_one("h1.profileName")
    if name_tag:
        business["Business Name"] = clean(name_tag.get_text())

    category_tag = soup.select_one("h2.profileJob")
    if category_tag:
        business["Category"] = clean(category_tag.get_text())

    about_container = soup.select_one("#aboutMeDataContainer") or soup
    business["Description"] = _extract_description(about_container)

    # Keywords: the "What's on offer" tag pills (#offerTagsPublic .peTagPill).
    # Like span.textRest in the description, this block is CSS-collapsed
    # (style="display:none" on the parent #offerTags) rather than actually
    # absent -- it only renders once "View full description" is expanded --
    # so it must be read directly rather than skipped as hidden content.
    # Not every listing has offer tags (e.g. haqq-legal-ai2 has none), in
    # which case this correctly comes back empty.
    keyword_tags = [
        clean(tag.get_text())
        for tag in about_container.select("#offerTagsPublic .peTagPill")
        if clean(tag.get_text())
    ]
    if keyword_tags:
        business["Keywords"] = ", ".join(keyword_tags)

    contact_block = soup.select_one("#personalPublic")
    if contact_block:
        address_tag = contact_block.find("address")
        if address_tag:
            street, city, state, zipcode, country = _parse_address_block(address_tag)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode
            business["Country"] = country

        phone_tag = contact_block.select_one('a[href^="tel:"]')
        if phone_tag:
            business["Phone"] = clean(phone_tag.get_text()) or phone_tag["href"][len("tel:"):]

        email_tag = contact_block.select_one('a[href^="mailto:"]')
        if email_tag:
            email = email_tag["href"][len("mailto:"):].split("?", 1)[0]
            business["Business Email"] = clean(email)

    website_tag = soup.select_one("#profilesPublic a[href]")
    if website_tag:
        business["Website URL"] = website_tag["href"].strip()

    logo_tag = soup.find("meta", property="og:image")
    if logo_tag and logo_tag.get("content"):
        business["Logo"] = urljoin(url, logo_tag["content"])
    else:
        avatar_tag = soup.select_one(".avatarContainer img")
        if avatar_tag and avatar_tag.get("src"):
            business["Logo"] = urljoin(url, avatar_tag["src"])

    hours = _extract_pe_hours(soup)
    if hours:
        business["Hours"] = hours

    # Photos: cover image first, then any gallery/slideshow images.
    photos = []
    cover = _extract_pe_cover(soup, url)
    if cover:
        photos.append(cover)
    for src in _extract_pe_gallery(soup, url):
        if src not in photos:
            photos.append(src)
    business["Photos"] = photos

    # Social Media Links: scoped to the business's own "about" content so
    # the sitewide share-profile widgets (same domains, different intent)
    # never get mistaken for the business's real social accounts. This
    # listing has none, but the logic generalizes to listings that do.
    social_links = {}
    for link in about_container.select("a[href]"):
        href = link["href"].strip()
        if not href or href.startswith("#") or _is_share_widget_link(href):
            continue
        for domain_key, label in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain_key):
                social_links[label] = href
                break
    if social_links:
        business["Social Media Links"] = social_links

    return business


# Same JS-loaded hours widget as provenexpert.com.
parse_provenemployer.needs_render = pe_hours_need_render