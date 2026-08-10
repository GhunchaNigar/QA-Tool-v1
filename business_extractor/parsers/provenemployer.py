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


def _parse_address_block(address_tag):
    """Split the <address> block's stripped text nodes into
    street/city/state/zipcode/country.

    Observed shape (see haqq-legal-ai2 profile): a flat run of text nodes
    with no per-field markup --
        ["8 The Green", "Dover,", "Delaware (DE)", "19901", "United States of America"]
    -- so this peels fields off the END of the list, since country and
    zipcode are the most reliably identifiable tokens (last item is always
    the country; the item before it is a zipcode if it contains a digit).
    State is unwrapped from its trailing "(ABBR)" when present, otherwise
    kept as-is. Whatever remains at the front is the street.
    """
    parts = list(address_tag.stripped_strings)
    if not parts:
        return "", "", "", "", ""

    country = parts[-1]
    parts = parts[:-1]

    zipcode = ""
    if parts and len(parts[-1]) <= 12 and re.search(r"\d", parts[-1]):
        zipcode = parts[-1]
        parts = parts[:-1]

    state = ""
    if parts:
        state_match = re.search(r"\(([^)]+)\)", parts[-1])
        state = state_match.group(1) if state_match else parts[-1]
        parts = parts[:-1]

    city = ""
    if parts:
        city = parts[-1].rstrip(",").strip()
        parts = parts[:-1]

    street = clean(" ".join(parts))
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

    # Photo gallery -- empty on listings (like this one) that haven't
    # uploaded any; #slideshowContainer holds <img> tags when populated.
    photos = []
    slideshow = soup.select_one("#slideshowContainer")
    if slideshow:
        for img in slideshow.select("img[src]"):
            photos.append(urljoin(url, img["src"]))
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