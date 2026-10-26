"""
Site parser: quora.com/profile/<name>

Quora profiles are rendered client-side, but the server inlines the GraphQL
result for the page as strings pushed into
window.ansFrontendGlobals.data.inlineQueryResults.results[<hash>].push("<json>").
The profile record there has:

  names[0].givenName / familyName      -> business name
  profileImageUrl                      -> Logo
  descriptionQtextDocument.legacyJson  -> the bio, as a list of paragraphs

Business listings are written into the bio as "Label:" paragraphs followed by
their value paragraph(s) (Owner Name, Address, Phone, Website, Business Email,
About US, Related Searches, optionally Hours). Links keep their real URL in the
span modifiers (the visible text of the Website link is a title, not the URL).

Quora is fetched with curl_cffi (Chrome fingerprint); blocked pages escalate
to Playwright automatically (see dispatch.py).
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py
from .medium import _split_address, _US_STATES

_PUSH_RE = re.compile(r'\.push\(\s*"((?:[^"\\]|\\.)*)"\s*\)')

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
    "keywords": "keywords",
    "hours": "hours",
    "business hours": "hours",
}


def _inline_results(html_text):
    """Yield each inlined GraphQL result (parsed JSON) from the page."""
    for raw in _PUSH_RE.findall(html_text):
        if "descriptionQtextDocument" not in raw and "profileImageUrl" not in raw:
            continue
        try:
            yield json.loads(json.loads('"' + raw + '"'))
        except (ValueError, TypeError):
            continue


def _find_user(node):
    """First dict that looks like the profile user record."""
    if isinstance(node, dict):
        if "descriptionQtextDocument" in node or "profileImageUrl" in node:
            return node
        for v in node.values():
            found = _find_user(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_user(v)
            if found:
                return found
    return None


def _bio_sections(user):
    """[(text, [link urls])] -- one entry per bio paragraph."""
    doc = (user.get("descriptionQtextDocument") or {}).get("legacyJson")
    if not doc:
        return []
    try:
        data = json.loads(doc)
    except (ValueError, TypeError):
        return []
    sections = []
    for sec in data.get("sections", []):
        text, links = "", []
        for span in sec.get("spans", []):
            text += span.get("text", "")
            link = ((span.get("modifiers") or {}).get("link") or {}).get("url")
            if link:
                links.append(link)
        text = clean(text)
        if text:
            sections.append((text, links))
    return sections


def _group_by_label(sections):
    """{key: [(text, links), ...]} -- every paragraph after a label, up to the next label."""
    grouped, current = {}, None
    for text, links in sections:
        key = _LABEL_KEYS.get(text.rstrip(":").strip().lower())
        if key and text.endswith(":"):
            current = key
            grouped.setdefault(current, [])
        elif current:
            grouped[current].append((text, links))
    return grouped


def _meta(soup, prop):
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    return clean(tag.get("content", "")) if tag else ""


def parse_quora(url, html_text):
    business = empty_business()
    soup = BeautifulSoup(html_text, "html.parser")

    user = None
    for result in _inline_results(html_text):
        user = _find_user(result)
        if user and user.get("descriptionQtextDocument"):
            break

    user = user or {}
    names = user.get("names") or []
    name = ""
    if names:
        name = clean(f"{names[0].get('givenName', '')} {names[0].get('familyName', '')}")
    business["Business Name"] = name or _meta(soup, "og:title")
    business["Logo"] = user.get("profileImageUrl") or _meta(soup, "og:image")

    grouped = _group_by_label(_bio_sections(user))

    def first(key):
        items = grouped.get(key) or []
        return items[0] if items else ("", [])

    business["Owner Name"] = first("owner")[0]

    street, city, state, zipcode = _split_address(first("address")[0])
    business["Street"], business["City"], business["State"], business["Zipcode"] = street, city, state, zipcode
    # The bio never states a country; infer it only for a US state + ZIP.
    if zipcode and state.lower() in _US_STATES:
        business["Country"] = "United States"

    business["Phone"] = first("phone")[0].replace("\u2013", "-").replace("\u2014", "-")

    web_text, web_links = first("website")
    business["Website URL"] = web_links[0] if web_links else (web_text if web_text.startswith("http") else "")

    mail_text, mail_links = first("email")
    mailto = next((l for l in mail_links if l.lower().startswith("mailto:")), "")
    business["Business Email"] = mailto[7:].split("?")[0] if mailto else mail_text

    business["Description"] = " ".join(t for t, _ in grouped.get("about", []))
    business["Keywords"] = first("keywords")[0]
    business["Hours"] = "\n".join(t for t, _ in grouped.get("hours", []))

    # Social links: any bio link that points at a known social network.
    social = {}
    for _, links in (item for items in grouped.values() for item in items):
        for link in links:
            host = urlparse(link).netloc.lower()
            for domain, label in SOCIAL_DOMAINS.items():
                if _hostname_matches_social_domain(host, domain):
                    social.setdefault(label, link)
    business["Social Media Links"] = social

    return filter_business_fields(business, url)