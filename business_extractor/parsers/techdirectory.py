"""
Site parser: techdirectory.io  (Brilliant Directories profile pages,
/<category-slug>/<business-slug>)

Same directory platform as nextbizthing.com / directoryup.com, so
parse_nextbizthing() does the heavy lifting (name, "street, city, ST zip"
location, website link, category, profile image, country from JSON-LD).
Differences handled here:

  - The phone number is NOT in a phone field (JSON-LD "telephone" is "N/A").
    It is the first line of the About text, which reads
    "Phone:(850) 460-2900About US:<description>". The phone is pulled out
    of that prefix and the "Phone:" / "About US:" labels are stripped, so
    Description is just the about text.
  - The same text is also on the page as .textarea-about_me paragraphs
    ("Phone:", "(850) ...", "About US:", "..."); used as a fallback when
    the JSON-LD description is missing.
  - There is no opening-hours section on this template, so Hours stays
    empty unless a .table-display-hours block ever appears.
"""

from ..common import *  # noqa: F401,F403
from .nextbizmaker import _nbm_ld_entries, _nbm_find, _nbm_value
from .nextbizthing import parse_nextbizthing

# "Phone:(850) 460-2900About US:Some text". The phone class only allows
# phone characters, and the "About US:" label is required after it, so the
# phone can never swallow the description.
_PHONE_ABOUT_RE = re.compile(
    r"^\s*Phone\s*:\s*(?P<phone>[\d\s().+\-]*?)\s*About\s*Us\s*:\s*(?P<about>.*)$",
    re.I | re.S,
)
_ABOUT_ONLY_RE = re.compile(r"^\s*About\s*Us\s*:\s*(?P<about>.*)$", re.I | re.S)


def _td_split_description(raw):
    """-> (phone, about) from the raw "Phone:...About US:..." text."""
    raw = clean(raw)
    m = _PHONE_ABOUT_RE.match(raw)
    if m:
        return clean(m.group("phone")), clean(m.group("about"))
    m = _ABOUT_ONLY_RE.match(raw)
    if m:
        return "", clean(m.group("about"))
    return "", raw


def _td_raw_description(soup):
    ld = _nbm_find(_nbm_ld_entries(soup), "LocalBusiness")
    raw = _nbm_value(ld.get("description"))
    if raw:
        return raw
    about = soup.select_one(".textarea-about_me")
    return clean(about.get_text(" ")) if about else ""


def parse_techdirectory(url, html_text):
    business = parse_nextbizthing(url, html_text)
    if not any(v for v in business.values()):
        return business  # blocked / empty page

    soup = BeautifulSoup(html_text, "html.parser")
    phone, about = _td_split_description(_td_raw_description(soup))

    if not business["Phone"] and sum(ch.isdigit() for ch in phone) >= 7:
        business["Phone"] = phone
    business["Description"] = about

    return filter_business_fields(business, url)