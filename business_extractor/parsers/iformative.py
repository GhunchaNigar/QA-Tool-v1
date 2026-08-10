"""
Site parser: iformative.com
"""

from ..common import *  # noqa: F401,F403 -- see business_extractor/common.py



def _split_iformative_address(address):
    """Split an iFormative-style address string. Handles two distinct
    shapes depending on whether Zip is its own trailing comma segment:

      (a) "...[, Street], City State, Zip" -- Zip is its own segment,
          and City+State are merged into the segment right before it
          with no comma between them (e.g. "Plano TX, 75023", or
          "123 Main St, Plano TX, 75023" when a Street is present).
      (b) "...[, Street], City, State, Zip" -- City and State are each
          their own comma segment too, so the segment before Zip is a
          bare State with no space in it.
      (c) "Street, City, State Zip" -- State+Zip share one trailing
          token with a space, no comma between them (fallback below).
    """
    street, city, state, zipcode = "", "", "", ""

    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return street, city, state, zipcode

    if len(parts) >= 2 and re.fullmatch(r"\d{5}(?:-\d{4})?", parts[-1]):
        zipcode = parts[-1]
        rest = parts[:-1]
        last = rest[-1]
        merged = re.match(r"^(.*?)\s+([A-Za-z]{2,})$", last)
        if merged:
            # Shape (a): last segment before Zip is "City State" merged.
            city = merged.group(1).strip()
            state = merged.group(2).strip()
            street = ", ".join(rest[:-1])
        else:
            # Shape (b): last segment before Zip is a bare State; the
            # segment before that (if any) is a bare City.
            state = last
            if len(rest) >= 2:
                city = rest[-2]
                street = ", ".join(rest[:-2])
        return street, city, state, zipcode

    # Fallback shape: "Street, City, State Zip" (state+zip sharing one
    # trailing token, no comma between them, and no bare trailing Zip
    # segment for the branch above to key off of).
    if len(parts) >= 3:
        street = ", ".join(parts[:-2])
        city = parts[-2]
        state_zip = parts[-1]
    elif len(parts) == 2:
        street = parts[0]
        state_zip = parts[1]
    else:
        state_zip = parts[0]

    match = re.match(r"^(.*?)\s+([\w-]*\d[\w-]*)$", state_zip.strip())
    if match:
        state = match.group(1).strip()
        zipcode = match.group(2).strip()
    else:
        state = state_zip.strip()

    return street, city, state, zipcode


def parse_iformative(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".product-view h1") or soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    info_td = soup.select_one("td.info")
    if info_td:
        # ---- Website URL  ----
        site_link = info_td.select_one("a[href^='http']")
        if site_link and site_link.get("href"):
            business["Website URL"] = site_link["href"]

        # ---- Normalize----
        info_copy = BeautifulSoup(str(info_td), "lxml")
        for br in info_copy.find_all("br"):
            br.replace_with("\n")
        lines = [clean(line) for line in info_copy.get_text().split("\n")]
        lines = [line for line in lines if line]

        # ---- Category ("Category: <value>" on its own line) ----
        for line in lines:
            match = re.match(r"^Category:\s*(.+)$", line, flags=re.I)
            if match:
                cat_text = clean(match.group(1))
                if is_meaningful(cat_text):
                    business["Category"] = cat_text
                break

        # ---- Address (the line right after the "Contact Information"
        # label) ----
        for i, line in enumerate(lines):
            if line.lower() == "contact information" and i + 1 < len(lines):
                addr_text = lines[i + 1]
                if is_meaningful(addr_text):
                    street, city, state, zipcode = _split_iformative_address(addr_text)
                    business["Street"] = street
                    business["City"] = city
                    business["State"] = state
                    business["Zipcode"] = zipcode
                break

        # ---- Phone ("Phone number: <value>" on its own line) ----
        for line in lines:
            match = re.match(r"^Phone number:\s*(.+)$", line, flags=re.I)
            if match:
                phone_text = clean(match.group(1))
                if is_meaningful(phone_text):
                    business["Phone"] = phone_text
                break

    return business


