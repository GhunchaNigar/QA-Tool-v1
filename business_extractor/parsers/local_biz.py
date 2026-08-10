"""
Site parser: local-biz.directory
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def parse_localbizdirectory(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    name_el = soup.select_one("h1.title")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Address / Category / Keywords (table.ar_desc labeled rows) ----
    for row in soup.select("table.ar_desc tr"):
        label_el = row.select_one("td.label")
        value_el = row.select_one("td:not(.label)")
        if not label_el or not value_el:
            continue
        label = clean(label_el.get_text()).rstrip(":").strip()

        if label == "Address":
            addr_text = clean(value_el.get_text())
            if "," not in addr_text:
                # Some listings (e.g. "Plano TX 75023") render a bare
                # comma-free "City State Zip" string with no street segment
                # at all. The comma-based logic below expects >=2 comma
                # parts and would otherwise dump the whole string into
                # Street, leaving City/State/Zipcode blank.
                street, city, state, zipcode = _split_city_state_zip_address(addr_text)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode
                continue
            parts = [clean(p) for p in addr_text.split(",") if clean(p)]
            if parts and parts[-1].upper() in ("USA", "US", "UNITED STATES"):
                business["Country"] = "United States"
                parts = parts[:-1]
            # A standalone suite/unit/floor segment (e.g. "131 Continental Dr,
            # Suite 305, Newark, DE 19713") belongs with the street line, not
            # the city -- merge it back in before splitting street/city/state.
            if len(parts) >= 2 and re.match(
                r"^(suite|ste|unit|apt|apartment|#|bldg|building|floor|fl)\b",
                parts[1], re.I
            ):
                parts = [f"{parts[0]}, {parts[1]}"] + parts[2:]
            if len(parts) >= 3:
                business["Street"] = parts[0]
                state_zip_match = re.match(r"^([A-Za-z]{2,})\s+(\d{5}(?:-\d{4})?)$", parts[-1])
                if state_zip_match:
                    business["State"] = state_zip_match.group(1)
                    business["Zipcode"] = state_zip_match.group(2)
                    business["City"] = ", ".join(parts[1:-1])
                else:
                    business["City"] = ", ".join(parts[1:-1])
                    business["State"] = parts[-1]
            elif len(parts) == 2:
                business["Street"] = parts[0]
                business["City"] = parts[1]
            elif parts:
                business["Street"] = ", ".join(parts)

        elif label == "Category":
            cat_link = value_el.select_one("a")
            cat_text = clean(cat_link.get_text()) if cat_link else clean(value_el.get_text())
            if is_meaningful(cat_text):
                business["Category"] = cat_text

        elif label == "Tag":
            tag_links = value_el.select("a")
            if tag_links:
                keywords = ", ".join(clean(a.get_text()) for a in tag_links if clean(a.get_text()))
            else:
                keywords = clean(value_el.get_text())
            if is_meaningful(keywords):
                business["Keywords"] = keywords

    # ---- Phone -----
    tab_content = soup.select_one("#popular .tab_content")
    if tab_content:
        paragraphs = tab_content.find_all("p", recursive=False)
        desc_parts = []
        label_map = {
            "phone": "Phone",
            "website": "Website URL",
            "owner name": "Owner Name",
            "business email": "Business Email",
            "email": "Business Email",
            "about us": "Description",
        }
        i = 0
        while i < len(paragraphs):
            label_key = clean(paragraphs[i].get_text()).rstrip(":").strip().lower()
            field = label_map.get(label_key)

            if field and i + 1 < len(paragraphs):
                if field == "Description":
                    # Collect every paragraph after "About Us:" as the
                    # description (some listings wrap it across more than
                    # one <p>).
                    for p in paragraphs[i + 1:]:
                        p_text = clean(p.get_text())
                        if is_meaningful(p_text):
                            desc_parts.append(p_text)
                    break
                elif field == "Website URL":
                    link = paragraphs[i + 1].select_one("a")
                    if link and link.get("href"):
                        business["Website URL"] = link["href"]
                    elif is_meaningful(clean(paragraphs[i + 1].get_text())):
                        business["Website URL"] = clean(paragraphs[i + 1].get_text())
                elif field == "Phone":
                    phone_text = clean(paragraphs[i + 1].get_text())
                    if is_meaningful(phone_text):
                        business["Phone"] = phone_text
                elif field == "Owner Name":
                    owner_text = clean(paragraphs[i + 1].get_text())
                    if is_meaningful(owner_text):
                        business["Owner Name"] = owner_text
                elif field == "Business Email":
                    email_link = paragraphs[i + 1].select_one("a[href^=mailto]")
                    if email_link:
                        business["Business Email"] = clean(email_link.get_text())
                    else:
                        email_text = clean(paragraphs[i + 1].get_text())
                        if is_meaningful(email_text):
                            business["Business Email"] = email_text
                i += 2
                continue

            # Not a recognized label -- e.g. the unlabeled description
            # paragraph that some listings put first. Treat it as
            # description text rather than skipping it.
            p_text = clean(paragraphs[i].get_text())
            if is_meaningful(p_text):
                desc_parts.append(p_text)
            i += 1
        if desc_parts:
            business["Description"] = "\n".join(desc_parts)

    # ---- Logo (JSON-LD WebPage image, falling back to the slider image) ----
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        graph = data.get("@graph", []) if isinstance(data, dict) else []
        for node in graph:
            image = node.get("image") if isinstance(node, dict) else None
            if isinstance(image, dict) and image.get("url"):
                business["Logo"] = urljoin(url, image["url"])
                break
        if business["Logo"]:
            break
    if not business["Logo"]:
        slider_img = soup.select_one(".article_slider .flexslider img")
        if slider_img and slider_img.get("src"):
            business["Logo"] = urljoin(url, slider_img["src"])

    return business


