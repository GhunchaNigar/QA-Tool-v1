import argparse
import json
import re
import sys
from typing import Optional

import requests
from bs4 import BeautifulSoup

FIELDS = [
    "Business Name", "Street", "City", "State", "Zipcode", "Country",
    "Phone", "Website URL", "Keywords", "Description", "Hours",
    "Business Email", "Category", "Logo",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _find_localbusiness_jsonld(soup: BeautifulSoup) -> Optional[dict]:
    """Return the first JSON-LD block whose @type is LocalBusiness (or a
    LocalBusiness subtype)."""
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and "LocalBusiness" in str(item.get("@type", "")):
                return item
            # some themes nest it inside an @graph array
            if isinstance(item, dict) and "@graph" in item:
                for sub in item["@graph"]:
                    if isinstance(sub, dict) and "LocalBusiness" in str(sub.get("@type", "")):
                        return sub
    return None


def _split_street_address(street_address: str):
    """
    GeoDirectory jams "street, city, state zip" into postalAddress.streetAddress,
    e.g. "2244 Faraday Ave #206 Carlsbad, CA 92008".
    Split it heuristically: last token(s) = "STATE ZIP", the piece before the
    last comma = city, everything before that = street.
    """
    street_address = _clean(street_address)
    city = state = zipcode = ""
    street = street_address

    # "... City, ST 12345" or "... City, ST 12345-6789"
    m = re.search(
        r"^(?P<street>.*?)\s*,\s*(?P<city>[^,]+?)\s*,\s*"
        r"(?P<state>[A-Za-z]{2,})\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$",
        street_address,
    )
    if m:
        street = m.group("street").strip()
        city = m.group("city").strip()
        state = m.group("state").strip()
        zipcode = m.group("zip").strip()
    else:
        # fallback: "Street City, ST ZIP" (one comma only)
        m2 = re.search(
            r"^(?P<rest>.*?)\s*,\s*(?P<state>[A-Za-z]{2,})\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$",
            street_address,
        )
        if m2:
            state = m2.group("state").strip()
            zipcode = m2.group("zip").strip()
            rest = m2.group("rest").strip()
            # split rest into street / city on the last space-separated
            # capitalized word run — best effort only.
            street = rest
            city = ""
    return street, city, state, zipcode


def _strip_duplicated_city(street: str, city: str) -> str:
    """
    GeoDirectory sometimes bakes the city onto the end of
    postalAddress.streetAddress (e.g. "2244 Faraday Ave #206 Carlsbad")
    even when addressLocality/addressRegion/postalCode are ALSO already
    populated separately and correctly. In that case the old logic never
    ran _split_street_address at all (it's gated on city/state/zip being
    *missing*), so the duplicated city passed straight through into
    Street untouched. This runs unconditionally whenever we already know
    the city, regardless of whether state/zip are present, and trims a
    trailing occurrence of it (plus any trailing comma/whitespace) off
    of street.
    """
    if not street or not city:
        return street
    pattern = re.compile(re.escape(city) + r"\s*,?\s*$", re.IGNORECASE)
    trimmed = pattern.sub("", street).strip().rstrip(",").strip()
    return trimmed or street


def _decode_obfuscated_email(soup: BeautifulSoup) -> str:
    """
    GeoDirectory hides emails behind an onclick handler like:
        onclick="javascript:window.open('mailto:'+(['user','domain.com']).join('@'),'_blank')"
    Reconstruct "user@domain.com" from that.
    """
    email_block = soup.select_one(".geodir-field-email a")
    if email_block and email_block.has_attr("onclick"):
        parts = re.findall(r"\[\s*'([^']+)'\s*,\s*'([^']+)'\s*\]", email_block["onclick"])
        if parts:
            return f"{parts[0][0]}@{parts[0][1]}"
    if email_block and email_block.has_attr("href") and email_block["href"].startswith("mailto:"):
        return email_block["href"].replace("mailto:", "").strip()
    # last resort: visible (possibly split by HTML comments) text
    if email_block:
        text = _clean(email_block.get_text(" "))
        text = text.replace(" @ ", "@").replace(" ", "")
        if "@" in text:
            return text
    return ""


def _extract_category(soup: BeautifulSoup) -> str:
    cat = soup.select_one(".geodir-field-post_category a")
    if cat:
        return _clean(cat.get_text())
    cat = soup.select_one(".geodir-category a")
    if cat:
        return _clean(cat.get_text())
    return ""


def _extract_keywords(soup: BeautifulSoup) -> str:
    tags = soup.select(".geodir-field-post_tags a") or soup.select(".geodir-tags a")
    if tags:
        return ", ".join(_clean(t.get_text()) for t in tags)
    meta = soup.find("meta", attrs={"name": "keywords"})
    if meta and meta.get("content"):
        return _clean(meta["content"])
    return ""


def _extract_hours(soup: BeautifulSoup) -> str:
    """
    GeoDirectory's business-hours widget can render under a few different
    class names depending on theme version. Try the common ones; if none
    are present the listing simply didn't publish hours.
    """
    selectors = [
        ".geodir-field-business_hours",
        ".geodir-bh-wrap",
        ".gd-w-business-hours",
        ".geodir_post_meta.geodir-field-business_hours",
    ]
    for sel in selectors:
        node = soup.select_one(sel)
        if node:
            return _clean(node.get_text(" "))
    return ""


def _extract_description(soup: BeautifulSoup, jsonld_desc: str) -> str:
    if jsonld_desc:
        return _clean(jsonld_desc)
    node = soup.select_one(".geodir-field-post_content p") or \
        soup.select_one(".geodir_post_meta.geodir-field-post_content")
    if node:
        return _clean(node.get_text(" "))
    return ""


def _extract_logo(soup: BeautifulSoup, jsonld: dict) -> str:
    img = (jsonld or {}).get("image")
    if isinstance(img, dict) and img.get("url"):
        return img["url"]
    if isinstance(img, str):
        return img
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return og["content"]
    logo_img = soup.select_one("#site-logo img")
    if logo_img and logo_img.get("data-lazy-src"):
        return logo_img["data-lazy-src"]
    return ""


def _extract_business_title(soup: BeautifulSoup) -> str:
    """
    Find the listing's own title, trying progressively more generic
    selectors and only accepting a match if it actually has non-empty
    text.

    Two things were added to the original selector list:

    1. `[itemprop=name]` and GeoDirectory-specific classes
       (`.geodir-entry-title`, `.gd-title`, `.listing-title`,
       `.geodir_post_meta.geodir-field-post_title`). The original list
       only covered `.single-post-title` / `.geodir-title`, which are
       from a *different* GeoDirectory theme skin than bulkadspost.com
       actually uses -- so on this site every selector in the old list
       missed, silently falling through to the (also failing) bare "h1"
       catch-all.
    2. A bare "h1" selector alone (still kept as the final fallback)
       grabs the FIRST <h1> anywhere on the page in document order --
       on most WP themes that's the site logo/branding in the header,
       not the listing title, and it's frequently empty or icon-only.
       Trying specific, listing-scoped selectors first avoids that.
    """
    for sel in (
        "[itemprop=name]",
        ".geodir-entry-title",
        ".gd-title",
        ".listing-title",
        ".single-post-title",
        ".geodir-title",
        ".geodir_post_meta.geodir-field-post_title",
        ".entry-title",
        "article h1",
        "main h1",
        "h1",
    ):
        node = soup.select_one(sel)
        if node:
            text = _clean(node.get_text())
            if text:
                return text
    return ""


def _extract_og_title(soup: BeautifulSoup) -> str:
    """
    og:title is set by nearly every SEO plugin (Yoast, RankMath, AIOSEO)
    independently of whichever theme markup renders the visible h1, so
    it's a reliable fallback when the DOM selectors above miss a theme
    that doesn't use the classes we know about.
    """
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return _clean(og["content"])
    return ""


def _extract_title_from_html_title_tag(soup: BeautifulSoup, site_name: str = "") -> str:
    """
    Last-resort fallback for the business name. The <title> tag on these
    listing-directory pages conventionally reads
    "<Business Name> <separator> <Site Name>"; split on the LAST such
    separator and keep everything before it, since the business name is
    always the leading part in that convention.

    The separator character class was expanded from just "-" and "|" to
    also include the typographic en dash "–", em dash "—", and a bullet
    "•" -- WordPress SEO plugins (Yoast/RankMath) commonly use one of
    these instead of a plain hyphen, and the previous version silently
    failed to split on those, returning the *whole* "Name – Site Name"
    string (or nothing at all, if the whole-string fallback then failed
    an emptiness check elsewhere) instead of just the business name.

    If a site_name (e.g. from meta og:site_name) is supplied and it
    appears verbatim in the title, it's stripped directly as a second,
    independent safety net regardless of which separator was used.
    """
    title_tag = soup.find("title")
    if not title_tag:
        return ""
    text = _clean(title_tag.get_text())
    if not text:
        return ""

    if site_name:
        text = re.sub(
            r"\s*[-|–—•:]\s*" + re.escape(site_name) + r"\s*$",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()

    parts = re.split(r"\s+[-|–—•]\s+(?!.*\s+[-|–—•]\s+)", text)
    return _clean(parts[0]) if parts else text


# --------------------------------------------------------------------------
# main parse function
# --------------------------------------------------------------------------
#
# NOTE: dispatch.py calls every "requests"-method parser as parser(url, html),
# and looks it up as parsers.bulkadspost.parse_bulkadspost. Both the name and
# the argument order below match that convention.

def parse_bulkadspost(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    jsonld = _find_localbusiness_jsonld(soup) or {}

    # ---- Name -----------------------------------------------------------
    # Layered fallback, each one independent of the others so a gap in
    # any single source doesn't leave Name blank:
    #   1. JSON-LD "name", then "legalName", then "alternateName" --
    #      some GeoDirectory schema output only populates legalName for
    #      LocalBusiness listings, not name.
    #   2. DOM selectors scoped to the listing title (see
    #      _extract_business_title docstring for what changed here).
    #   3. og:title meta tag -- set independently by SEO plugins.
    #   4. <title> tag, stripping the site name / separator.
    site_name = ""
    og_site_name = soup.find("meta", property="og:site_name")
    if og_site_name and og_site_name.get("content"):
        site_name = _clean(og_site_name["content"])

    name = (
        _clean(jsonld.get("name"))
        or _clean(jsonld.get("legalName"))
        or _clean(jsonld.get("alternateName"))
        or _extract_business_title(soup)
        or _extract_og_title(soup)
        or _extract_title_from_html_title_tag(soup, site_name=site_name)
    )

    # ---- Address ------------------------------------------------------
    addr = jsonld.get("address") or {}
    street = _clean(addr.get("streetAddress", ""))
    city = _clean(addr.get("addressLocality", ""))
    state = _clean(addr.get("addressRegion", ""))
    zipcode = _clean(addr.get("postalCode", ""))
    country = _clean(addr.get("addressCountry", ""))

    if street and city:
        # Runs unconditionally (not just when city/state/zip are
        # missing) -- see _strip_duplicated_city docstring.
        street = _strip_duplicated_city(street, city)

    if street and (not zipcode or not city or not state):
        s2, c2, st2, z2 = _split_street_address(street)
        street = s2 or street
        city = city or c2
        state = state or st2
        zipcode = zipcode or z2

    if not street and not city:
        addr_node = soup.select_one(".geodir-field-address [itemprop=streetAddress]") \
            or soup.select_one(".geodir-field-address")
        if addr_node:
            full = _clean(addr_node.get_text(" "))
            s2, c2, st2, z2 = _split_street_address(full)
            street, city, state, zipcode = s2, c2, st2, z2

    if not country:
        country = "United States"  # site-wide default for this listing set

    # ---- Phone --------------------------------------------------------
    phone = _clean(jsonld.get("telephone", ""))
    if not phone:
        tel = soup.select_one(".geodir-field-phone a[href^='tel:']")
        if tel:
            phone = _clean(tel.get_text())

    # ---- Website --------------------------------------------------------
    website = ""
    same_as = jsonld.get("sameAs")
    if isinstance(same_as, list) and same_as:
        website = same_as[0]
    elif isinstance(same_as, str):
        website = same_as
    if not website:
        w = soup.select_one(".geodir-field-website a")
        if w and w.get("href"):
            website = w["href"]

    # ---- Business Email -------------------------------------------------
    email = _decode_obfuscated_email(soup)

    # ---- Category / Keywords / Hours / Description / Logo --------------
    category = _extract_category(soup)
    keywords = _extract_keywords(soup)
    hours = _extract_hours(soup)
    description = _extract_description(soup, jsonld.get("description", ""))
    logo = _extract_logo(soup, jsonld)

    return {
        "Business Name": name,
        "Street": street,
        "City": city,
        "State": state,
        "Zipcode": zipcode,
        "Country": country,
        "Phone": phone,
        "Website URL": website,
        "Keywords": keywords,
        "Description": description,
        "Hours": hours,
        "Business Email": email,
        "Category": category,
        "Logo": logo,
        "Source URL": url,
    }


def fetch_and_parse(url: str) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return parse_bulkadspost(url, resp.text)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Parse bulkadspost.com listing pages")
    ap.add_argument("urls", nargs="+", help="Listing URL(s) to parse")
    ap.add_argument("--json", action="store_true", help="Print raw JSON instead of a table")
    args = ap.parse_args()

    results = []
    for url in args.urls:
        try:
            results.append(fetch_and_parse(url))
        except Exception as exc:  # noqa: BLE001
            print(f"[!] Failed to parse {url}: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for r in results:
            print(f"\n=== {r.get('Business Name') or r.get('Source URL')} ===")
            for field in FIELDS:
                print(f"{field:15}: {r.get(field, '')}")


if __name__ == "__main__":
    main()
