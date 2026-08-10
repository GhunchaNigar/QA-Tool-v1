"""
Site parser: find-us-here.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



_FINDUSHERE_EXCLUDED_LINK_DOMAINS = (
    "find-us-here.com", "facebook.com", "twitter.com", "x.com",
    "whatsapp.com", "wa.me", "telegram.me", "t.me", "google.com",
    "ezoic.net",
)


def parse_findushere(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    page_text = soup.get_text("\n")

    # ---- Address (Street / City / State / Zipcode) ----
    addr_match = re.search(r"\bAddress\b\s*\n(.*?)\n\s*Phone\b", page_text, re.S)
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

    # ---- Country ----
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
    web_label = soup.find(
        lambda tag: tag.name in ("h3", "h4", "h5", "strong", "b", "p", "div", "span")
        and clean(tag.get_text()) == "Web"
    )
    if web_label:
        for link in web_label.find_all_next("a", href=True):
            href = link["href"]
            if not href.startswith("http"):
                continue
            if _hostname_matches_social_domain(href, "google.com") and "maps" in href.lower():
                continue
            if any(_hostname_matches_social_domain(href, d) for d in _FINDUSHERE_EXCLUDED_LINK_DOMAINS):
                continue
            business["Website URL"] = href
            break
        if not business["Website URL"]:
            web_match = re.search(r"\bWeb\b\s*\n\s*(\S+)", page_text)
            if web_match:
                business["Website URL"] = web_match.group(1).strip("<>")

    # ---- Category + Description  ----
    category_node = None
    for node in soup.find_all(string=re.compile(r"Category:\s*\S")):
        if node.find_parent(["script", "style"]):
            continue
        candidate = clean(re.sub(r"^.*Category:\s*", "", str(node), flags=re.S))
        if not candidate or len(candidate) > 80 or re.search(r"[{}();=]", candidate):
            continue
        category_node = node
        business["Category"] = candidate
        break

    if category_node:
        category_block = category_node.find_parent(["tr", "li", "div", "p"])
        if category_block:
            desc_block = category_block.find_next_sibling(["tr", "li", "div", "p"])
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

    # ---- Logo (og:image, preferred over any inline listing photo since
    #      it's the one consistently populated across listings) ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])

    return business


