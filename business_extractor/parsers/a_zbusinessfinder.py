"""
Site parser: a-zbusinessfinder.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_azbusinessfinder(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    page_text = soup.get_text("\n")

    # ---- Address ----
    addr_match = re.search(r"Physical Address\s*(.*?)\n-?\s*Phone\b", page_text, re.S)
    if addr_match:
        addr_lines = [clean(line) for line in addr_match.group(1).split("\n")]
        addr_lines = [line for line in addr_lines if line]
        if addr_lines and re.fullmatch(r"\d{5}(-\d{4})?", addr_lines[-1]):
            business["Zipcode"] = addr_lines.pop()
        if addr_lines:
            business["State"] = addr_lines.pop()
        if addr_lines:
            business["City"] = addr_lines.pop()
        if addr_lines:
            business["Street"] = " ".join(addr_lines)

    # ---- Country  ----
    h2 = soup.find("h2")
    if h2:
        tokens = clean(h2.get_text()).split()
        if tokens:
            business["Country"] = tokens[-1]

    # ---- Phone (tel: link) ----
    tel = soup.select_one('a[href^="tel:"]')
    if tel:
        phone_text = clean(tel.get_text())
        business["Phone"] = phone_text or tel["href"].replace("tel:", "").strip()

    # ---- Business Email ----
    email_scope = soup.select_one('[itemprop="email"]') or soup
    mailto = email_scope.select_one('a[href^="mailto:"]') or soup.select_one('a[href^="mailto:"]')
    if mailto:
        business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()
    if not business["Business Email"]:
        business["Business Email"] = _find_cf_email(soup)

    # ---- Website URL  ----
    website_label = soup.find(
        lambda tag: tag.name in ("h3", "h4", "h5", "strong", "b", "p", "div", "span", "li", "td", "th")
        and clean(tag.get_text()) == "Website"
    )
    if website_label:
        for link in website_label.find_all_next("a", href=True):
            href = link["href"]
            if not href.startswith("http"):
                continue
            if "maps" in href.lower() and _hostname_matches_social_domain(href, "google.com"):
                continue
            if any(_hostname_matches_social_domain(href, d) for d in _FINDUSHERE_EXCLUDED_LINK_DOMAINS):
                continue
            business["Website URL"] = href
            break
    if not business["Website URL"]:
        url_link = soup.select_one('a[itemprop="url"][href^="http"]')
        if url_link:
            business["Website URL"] = url_link["href"]
    if not business["Website URL"]:
        web_match = re.search(r"\bWebsite\b\s*\n\s*(\S+)", page_text)
        if web_match:
            business["Website URL"] = web_match.group(1).strip("<>")

    # ---- Category (breadcrumb) ----
    # This template's breadcrumb anchors each carry their own
    # class="breadcrumb" (USA -> California -> Alameda -> the actual
    # category, e.g. "Psychologist"), so select those directly rather
    # than searching for a generic container tag containing "»".
    #
    # That generic search (`tag.name in (...) and "»" in tag.get_text()`)
    # used to match the FIRST tag satisfying it in document order --
    # but "»" being anywhere in a tag's full text means every ancestor
    # wrapping the real breadcrumb div also "contains" it, and the
    # outermost page wrapper (e.g. .off-canvas-wrap) comes first.
    # find_all("a") on that wrapper then pulls in every anchor on the
    # whole page (nav links, star-rating icons, etc.), and the actual
    # category name only survived if the LAST such anchor in document
    # order happened to be the real breadcrumb link -- on this listing
    # it was an icon-only star-rating <a> with no text, so Category
    # came back "" instead of "Psychologist".
    crumb_links = [a for a in soup.select("a.breadcrumb") if is_meaningful(clean(a.get_text()))]
    if crumb_links:
        business["Category"] = clean(crumb_links[-1].get_text())

    if not business["Category"]:
        # Fallback for pages that don't use class="breadcrumb" on the
        # links themselves: same idea, but requires "»" to be a DIRECT
        # text child of the tag (not just present somewhere in its
        # descendants), so a page-wide wrapper div can no longer match
        # ahead of the actual breadcrumb container.
        def _has_direct_raquo(tag):
            return any(
                isinstance(child, NavigableString) and "\u00bb" in child
                for child in tag.children
            )

        breadcrumb = soup.find(
            lambda tag: tag.name in ("nav", "div", "ul", "ol", "p", "table", "tr", "td")
            and _has_direct_raquo(tag)
        )
        if breadcrumb:
            fallback_links = [a for a in breadcrumb.find_all("a") if is_meaningful(clean(a.get_text()))]
            if fallback_links:
                business["Category"] = clean(fallback_links[-1].get_text())

    # ---- Description ----
    desc_header = soup.find(string=re.compile(r"Business/Community Description", re.I))
    if desc_header and not desc_header.find_parent(["script", "style"]):
        header_block = desc_header.find_parent(["tr", "th", "td", "div", "p"])
        if header_block and header_block.name in ("td", "th"):
            header_block = header_block.find_parent("tr") or header_block
        if header_block:
            desc_block = header_block.find_next_sibling(["tr", "div", "p"])
            if desc_block:
                desc_text = clean(desc_block.get_text())
                if is_meaningful(desc_text):
                    business["Description"] = desc_text

    if not business["Description"]:
        meta_desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Logo ----
    logo_img = soup.select_one('img[src*="business_images/main"]') or soup.select_one('img[src*="business_images"]')
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business
