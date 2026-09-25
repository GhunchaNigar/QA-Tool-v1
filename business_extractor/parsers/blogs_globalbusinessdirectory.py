"""
Site parser: blogs.globalbusinessdirectory.us
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



_BLOGS_GBD_LABELS = [
    "Owner Name", "Address", "Phone", "Website", "Business Email",
    "About Us", "Related Searches",
]


def parse_blogs_globalbusinessdirectory(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one("h1.post-title")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"])
    if not business["Business Name"]:
        title_tag = soup.find("title")
        if title_tag:
            business["Business Name"] = clean(
                re.sub(r"\s*[&#8211;\-]+.*$", "", title_tag.get_text())
            )

    # ---- Label/value block ----
    # The theme renders each field as "Label<br/>Value<br/>Label<br/>
    # Value..." all inside ONE <p>, with no separator other than <br/>
    # between them. p.get_text() (no separator) ignores <br/> and
    # concatenates every text node directly, collapsing the whole
    # field list into a single unbroken blob, e.g.
    # "Owner NameNeera TruongAddressPlano TX 75023Phone...". Since the
    # section regex below only matches a label at the very start of a
    # line, that blob let just the *first* label ("Owner Name") match
    # at position 0, and it then swallowed everything else in the <p>
    # as its own value -- every other field came back empty.
    # get_text(separator="\n") puts a newline between each text node
    # instead, restoring one label/value per line as clean_multiline
    # expects.
    description = ""
    content_block = soup.select_one("div.post-content.theme-blog-details")
    if content_block:
        lines = []
        for p in content_block.find_all("p", recursive=False):
            text = clean_multiline(p.get_text(separator="\n"))
            if text:
                lines.append(text)
        description = "\n".join(lines)

    if not description:
        og_desc = soup.find("meta", property="og:description")
        description = og_desc["content"] if og_desc and og_desc.get("content") else ""

    sections = _band_description_sections(description, labels=_BLOGS_GBD_LABELS)

    # ---- Owner Name ----
    if sections.get("Owner Name"):
        business["Owner Name"] = sections["Owner Name"]

    # ---- Address -> Street / City / State / Zipcode ----
    address = sections.get("Address", "")
    if address:
        street, city, state, zipcode = _split_city_state_zip_address(address)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone ----
    if sections.get("Phone"):
        business["Phone"] = sections["Phone"]

    # ---- Website URL ----
    if sections.get("Website"):
        business["Website URL"] = sections["Website"]
    if not business["Website URL"] and content_block:
        # Fallback for when the URL is rendered as a clickable <a> link
        # instead of plain text: grab the first outbound link in the
        # field block (skip the site's own domain, mailto/tel links).
        for a in content_block.select("a[href]"):
            href = a["href"].strip()
            if href.lower().startswith(("http://", "https://")) and "globalbusinessdirectory.us" not in href.lower():
                business["Website URL"] = href
                break

    # ---- Business Email ----
    if sections.get("Business Email"):
        business["Business Email"] = sections["Business Email"]
    if not business["Business Email"] and content_block:
        mailto = content_block.select_one("a[href^='mailto:']")
        if mailto:
            business["Business Email"] = clean(mailto["href"][len("mailto:"):].split("?")[0])

    # ---- Description ("About Us" section) ----
    if sections.get("About Us"):
        business["Description"] = sections["About Us"]

    # ---- Keywords ("Related Searches" section) ----
    if sections.get("Related Searches"):
        business["Keywords"] = sections["Related Searches"]

    # ---- Category ----
    category_link = soup.select_one(".post-category-box a")
    if category_link:
        cat_text = clean(category_link.get_text())
        if is_meaningful(cat_text):
            business["Category"] = cat_text

    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])
    if not business["Logo"]:
        img = soup.select_one("div.post-block-media-wrap img")
        if img and img.get("src"):
            business["Logo"] = urljoin(url, img["src"])

    return business


