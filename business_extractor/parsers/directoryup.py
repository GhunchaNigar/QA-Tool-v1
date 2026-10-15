"""
Site parser: *.directoryup.com (e.g. discover247.directoryup.com)

Same directory platform as nextbizthing.com, so parse_nextbizthing() does
the heavy lifting (name, "street, city, ST zip" location, hidden phone span,
category, profile image). Differences handled here:

  - There is no website link on the page. The URL only appears in the
    JSON-LD "sameAs" and at the start of the description
    ("Website:https://... About US:...").
  - That description prefix ("Website:<url>" and the "About US:" label) is
    stripped so Description is just the about text.
"""

from ..common import *  # noqa: F401,F403
from .nextbizmaker import _nbm_ld_entries, _nbm_find, _nbm_value
from .nextbizthing import parse_nextbizthing

_WEBSITE_PREFIX_RE = re.compile(r"^\s*Website\s*:\s*(https?://\S+?)\s*(?=About\b|$)", re.I)
_ABOUT_LABEL_RE = re.compile(r"^\s*About\s*(?:Us|US|us)?\s*:\s*", re.I)


def parse_directoryup(url, html_text):
    business = parse_nextbizthing(url, html_text)
    if not any(business.values()):
        return business

    soup = BeautifulSoup(html_text, "html.parser")
    ld = _nbm_find(_nbm_ld_entries(soup), "LocalBusiness")
    raw_desc = _nbm_value(ld.get("description")) or business["Description"]

    website_in_desc = ""
    m = _WEBSITE_PREFIX_RE.match(raw_desc)
    if m:
        website_in_desc = m.group(1)
        raw_desc = raw_desc[m.end():]
    raw_desc = _ABOUT_LABEL_RE.sub("", raw_desc).strip()
    business["Description"] = raw_desc

    if not business["Website URL"]:
        same_as = ld.get("sameAs")
        if isinstance(same_as, str):
            same_as = [same_as]
        candidates = [u for u in (same_as or []) if isinstance(u, str) and u.startswith(("http://", "https://"))]
        business["Website URL"] = (candidates[0] if candidates else website_in_desc).strip()

    return filter_business_fields(business, url)