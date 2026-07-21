"""
extractor.py
Unified business-listing extractor.

Supports (auto-detected by domain):
  - nearfinderus.com      -- static HTML, fetched with requests
  - smallbusinessusa.com  -- sits behind a JS "Checking your Browser" wall,
                             fetched via playwright_worker.py (subprocess)
  - zeemaps.com           -- map widget; data pulled directly from its
                             internal JSON API (no browser needed at all)
  - callupcontact.com     -- static HTML, fetched with requests
  - zumvu.com             -- sits behind a JS "browser check" wall,
                             fetched via playwright_worker.py (subprocess)
  - blinx.biz             -- Next.js page whose business record is loaded
                             client-side via an XHR call AFTER the initial
                             HTML loads (confirmed via DevTools Network
                             tab -- it is NOT present in the raw HTML or in
                             __NEXT_DATA__), so this is fetched via
                             playwright_worker.py (subprocess) to let the
                             page hydrate and render the real values before
                             we read them; embedded __NEXT_DATA__ is still
                             checked first as a cheap win when present.
  - place123.net          -- static HTML, fetched with requests (old-style
                             server-rendered directory template; see
                             parse_place123 for details)
  - freelistingusa.com    -- static HTML, fetched with requests (plain
                             server-rendered listing template; see
                             parse_freelistingusa for details)
  - askmap.net            -- static HTML, fetched with requests (plain
                             server-rendered directory template with
                             labeled sections -- "Address details",
                             "Phone & WWW", etc; see parse_askmap for
                             details)
  - zipleaf.us            -- static HTML, fetched with requests (JSON-LD
                             LocalBusiness block for name/address/phone/
                             logo/description; Website URL comes from the
                             site-link anchor's visible text, not its
                             internal "/GoToWebsite/" redirect href; see
                             parse_zipleaf for details)
  - cataloxy.us            -- static HTML, fetched with requests (region
                             subdomains like de-newark.cataloxy.us all
                             match on "cataloxy.us"; real <meta
                             name="keywords"> and a genuine Category in
                             the breadcrumb; address read from
                             schema.org PostalAddress microdata; Website
                             URL comes from the site-link anchor's
                             title="..." attribute, not its javascript:
                             href; see parse_cataloxy for details)
  - fyple.com              -- static HTML, fetched with requests
                             (schema.org/LocalBusiness + PostalAddress
                             microdata for name/address; Phone number,
                             Categories, Company description, OPEN
                             HOURS, and Photos read from their own
                             label/section markup, since they aren't
                             part of the microdata block; no Website
                             URL, Business Email, Keywords, or Social
                             Media Links field exists anywhere in this
                             template -- see parse_fyple for details)
  - merchantcircle.com     -- static HTML, fetched with requests
                             (business:contact_data:* OG meta tags for
                             Street/City/Zipcode/Country/Phone/Website;
                             State has no meta equivalent and is read
                             from a schema.org addressRegion span in
                             the body instead; this page variant's own
                             <title>/meta-description are a "Map and
                             Directions to X" flavor of the listing, so
                             Name/Description are read from og:title/
                             og:description instead; no genuine
                             Business Email, Social Media Links, or GBP
                             Link exists anywhere in this template --
                             see parse_merchantcircle for details)
  - globalbusinessdirectory.us -- static HTML, fetched with requests
                             (WordPress + WP Job Manager "findus"
                             theme; address is a single unsplit
                             "Street, City, ST Zip" string, reused via
                             the same splitter as blinx.biz's rendered
                             address; Country isn't printed as text
                             anywhere but is encoded in the <article>
                             tag's own "job_listing_region-<slug>" CSS
                             class; Business Email is a genuine
                             business-owned address in the "Contact
                             Business" sidebar widget, not the theme/
                             site owner's own footer contact email; no
                             Hours widget was present on the tested
                             listing -- see parse_globalbusinessdirectory
                             for details)
  - chamberofcommerce.com  -- static HTML, fetched with requests (name/
                             address/description/logo come from an
                             embedded schema.org LocalBusiness JSON-LD
                             block, but that block has no "telephone"
                             field -- Phone is read from the "Key
                             Contacts" sidebar's fa-phone line instead,
                             deliberately skipping a second number
                             tagged with an fa-fax icon; Business Email
                             is rendered through Cloudflare's "email
                             protection" obfuscation and is decoded
                             rather than read directly; Category comes
                             from the breadcrumb crumb just before the
                             business-name crumb; GBP Link is left
                             blank since the only Maps references are a
                             directions search-query link and an Embed
                             API iframe URL carrying the page's own API
                             key -- see parse_chamberofcommerce for
                             details)
  - trueen.com             -- static HTML, fetched with requests
                             (verified against the real page source:
                             Street/City/State/Zipcode, Phone, Website
                             URL, and the "Who is X?" Description are
                             read from the page's own @type: FAQPage
                             JSON-LD block first -- cleanest source,
                             no markup to strip -- with the @type:
                             LocalBusiness JSON-LD block and then
                             verified CSS selectors [h1.header-titlex,
                             span.single-page-category a, the
                             fa-map-marker/fa-passport icon lines,
                             p.single-page-phone, and the "View
                             website" button (a.view-button with
                             target="_blank" + rel="nofollow", which
                             distinguishes it from the "Write a
                             Review" button sharing the same class)]
                             as fallbacks; no genuine Hours, Business
                             Email, Keywords, Social Media Links, GBP
                             Link, Logo, or Photos were found on the
                             (unclaimed) listing this was built
                             against -- see parse_trueen for details)
  - citysquares.com        -- static HTML, fetched with requests (no
                             JSON-LD on this template; every field
                             comes from a verified CSS selector --
                             h1.listing, div.logo img, div.phone.element,
                             #full-address [a "Street, City, State,
                             Zipcode" string with FOUR comma-separated
                             segments -- Zip is its own segment here,
                             unlike blinx.biz's shape, so it needs its
                             own splitter, _split_citysquares_address],
                             div.website.element's rel="nofollow" link,
                             div.socials.section, div.hours.section,
                             div.about.section, the breadcrumb's last
                             "/cat/" link for Category, and
                             div.images.section for Photos; Business
                             Email is Cloudflare-obfuscated, same shape
                             as chamberofcommerce.com, decoded via the
                             shared _find_cf_email() helper; no
                             Country or Keywords field exists on this
                             template, and GBP Link is left blank for
                             the same reason as chamberofcommerce.com/
                             trueen.com -- the only Google Maps
                             reference is a Maps Embed API iframe URL
                             carrying the page's own API key, not a
                             real shareable GBP link -- see
                             parse_citysquares for details)
  - b2bco.com              -- static HTML, fetched with requests
                             (SmartPortal-based B2B marketplace/directory
                             template; Name comes from the profile-header
                             <h1> rather than <title>/og:title, which
                             carry a " - Marketplace and Business
                             Network - B2BCO" suffix; Street/City/State/
                             Country are read from the labeled "General
                             Information" section rather than a combined
                             address string; Website URL is the visible
                             anchor text, since the href is an internal
                             "/l/?channel=..." click-tracking redirect;
                             Description/Keywords come from the
                             "Business Summary"/"Business Keywords"
                             labeled blocks, falling back to the meta
                             tags; Category is the first "Categories"
                             breadcrumb link; no genuine Hours or
                             Business Email were found on the tested
                             (unclaimed/"not complete") listing this was
                             built against -- see parse_b2bco for
                             details)
  - find-us-here.com       -- fetched via Playwright (JS-rendered;
                             confirmed via DevTools that the Business
                             Email <a href="mailto:..."> is written into
                             the DOM by an inline <script> and is absent
                             from the raw server HTML a plain requests
                             fetch would see). Most of this parser was
                             built from a text/markdown extraction of
                             the live page rather than raw HTML source,
                             so it locates fields by their on-page label
                             -- "Address", "Phone", "Web", "Category:"
                             -- instead of fixed CSS classes; Name from
                             <h1>; Street/City/State/Zipcode split from
                             the multi-line block between the "Address"
                             and "Phone" labels; Country from the last
                             token of the "<City> <State abbr>
                             <Country>" subheading; Website URL is the
                             first external, non-directory/non-social
                             link after the "Web" label; Business Email
                             is the confirmed real mailto: link scoped
                             to its <span itemprop="email"> wrapper
                             (Cloudflare-style obfuscation kept only as
                             a fallback, not the primary path);
                             Category/Description come from a
                             "Category: X" line and the description
                             block immediately after it; if any field
                             doesn't match once run against a live
                             fetch, see parse_findushere to tighten it
                             with a verified selector)
  - a-zbusinessfinder.com  -- fetched via Playwright, same reasoning as
                             find-us-here.com (this is the same
                             directory-network template family, so its
                             Business Email is assumed to be JS-injected
                             too, but that specific assumption is NOT
                             yet confirmed via DevTools for this domain
                             -- if it comes back blank on a live run,
                             inspect the "Email" row and report back).
                             Built from a text/markdown extraction, same
                             as find-us-here.com, with confirmed
                             structural differences from it: the
                             Address/Phone/Email/Website block is a
                             bullet list where "Physical Address" shares
                             its line with the first address line
                             (rather than being its own heading); there
                             is no "Category: X" line anywhere on the
                             page (Category instead comes from the last
                             crumb of the "»"-separated breadcrumb
                             trail) and no <meta property="og:image">
                             tag at all (confirmed absent -- Logo comes
                             from the listing's own photo <img>
                             instead); Description comes from the
                             "Business/Community Description" section
                             (not "About <Business>" like find-us-
                             here.com) -- see parse_azbusinessfinder for
                             details)

  - cybo.com               -- static HTML, fetched with requests. Built
                             from a text/markdown extraction of the live
                             page (not raw view-source), same caveat as
                             find-us-here.com/a-zbusinessfinder.com --
                             re-check against a live fetch before relying
                             on it. What IS confirmed from the real href
                             values seen in that extraction: Website URL
                             and every Social Media Links entry are
                             wrapped in the same "/r/biz/web?..." click-
                             tracking redirect, distinguished from each
                             other only by a "social_tag=" query param
                             (absent on the Website link, present -- fb/
                             tw/yt/linkedin/instagram/Tiktok -- on social
                             icons); the real destination is recoverable
                             from the anchor's visible text for Website
                             and for TikTok specifically, but NOT for the
                             other social icons, which render as bare
                             icon-name text with no visible URL anywhere
                             in the page -- those are left pointing at
                             the tracking redirect itself, not a real
                             profile URL. Phone is also not a real tel:
                             link but a "/phone/how-to-call/..." redirect
                             whose visible text is the real number.
                             Street/City/State/Zipcode/Country come from
                             a labeled "Address" block ("City: X",
                             "State: X", "Postal Code: X", "Country: X");
                             Category prefers the "Categories: X" label
                             in the About section over the shorter
                             category tag/pill under the header; GBP Link
                             is left blank since the only Google-Maps
                             reference is a plain Maps *search* query
                             built from the street address, not a real
                             Business Profile link; see parse_cybo for
                             details)
  - band.us                -- static HTML, fetched with requests (Naver
                             BAND group "intro" pages; despite the page
                             being a client-hydrated app shell, the
                             entire business record -- Owner Name,
                             Address, Phone, Business Email, About us,
                             Related Searches -- is pre-baked into a
                             single newline-separated meta
                             name="description" string, duplicated
                             verbatim in og:description/twitter:
                             description, so no Playwright render is
                             needed; Business Name comes from og:title
                             with the fixed " | BAND" suffix stripped;
                             no genuine Country, Website URL, Hours,
                             Social Media Links, GBP Link, Category, or
                             Photos exist anywhere on this template --
                             see parse_band for details)
  - americansearch.info    -- static HTML, fetched with requests
                             (Brilliant-Directories-family template, no
                             LocalBusiness JSON-LD block; Name from the
                             profile h1 (og:title is site-branded "X on
                             AMERICAN SEARCH"); Street/City/State/Zip
                             from schema.org streetAddress microdata;
                             Country and Category both read from the
                             breadcrumb (Home > Country > Category >
                             business name); Phone from schema.org
                             telephone microdata; Website URL from the
                             itemprop="url" weblink anchor; Description
                             from the "About my Business" free-text
                             block; Logo from the profile photo; no
                             genuine Hours, Social Media Links, or GBP
                             Link exist on the tested listing -- see
                             parse_americansearch for details)
  - linkcentre.com         -- static HTML, fetched with requests. Built
                             from the site's own real HTML source (not a
                             text/markdown reconstruction, unlike cybo.com
                             above) -- verified selectors throughout.
                             Street/City/Zipcode/Country/Phone come from
                             the business:contact_data:* OG meta tags,
                             same reliable shape as merchantcircle.com;
                             State has no meta equivalent, so it's read
                             from the @graph-wrapped JSON-LD
                             LocalBusiness block's address.addressRegion
                             instead, which also backstops every other
                             field and supplies Website URL (sameAs[0]),
                             Logo, Description, and Category (knowsAbout,
                             confirmed identical to the "Listed In" pill
                             text used as its own fallback); Name prefers
                             the profile <h1> over og:title, which on
                             this template carries a trailing " |
                             Restoration Services Reviews & Info |
                             LinkCentre" suffix. Social Media Links and
                             Business Email are intentionally left blank
                             -- confirmed on the tested (unclaimed/free)
                             listing that every social/email link on the
                             page belongs to LinkCentre itself (its own
                             Facebook/X/LinkedIn share-intent buttons and
                             an Organization-level support@linkcentre.com
                             in the JSON-LD), not the business -- see
                             parse_linkcentre for details)

Any other domain falls back to a best-effort generic parser fetched with
requests; if that response looks like a bot-check page, it automatically
retries via Playwright too.

Usage:
    python extractor.py "<url1>" "<url2>" ...
    (with no arguments, runs the two URLs in __main__ below)

Requires playwright_worker.py in the same directory for any site that
needs the Playwright fetch path.
"""

import json
import re
import sys
import html
import random
import subprocess
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs

import fields_config


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0 Safari/537.36"
    )
}

SOCIAL_DOMAINS = {
    "facebook": "Facebook",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "twitter": "Twitter",
    "x.com": "Twitter",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "pinterest": "Pinterest",
    # WhatsApp "click to chat" links (api.whatsapp.com/send?phone=...
    # and wa.me/...) are a messaging CTA, not the business's website --
    # several directory templates (nearfinderus.com confirmed) render
    # this under a "Website" button/icon when the listing has a WhatsApp
    # contact configured but no real external site on file. Without
    # excluding it here, the generic "first external, non-social link"
    # Website URL scan picks it up and mistakes it for the real site.
    "wa.me": "WhatsApp",
    "whatsapp.com": "WhatsApp",
}


def _hostname_matches_social_domain(href, domain):
    """Hostname-boundary check for SOCIAL_DOMAINS keys that a plain
    `domain in href.lower()` substring test can false-match against --
    e.g. "https://www.cataloxy-mx.com/" contains the raw substring
    "x.com" without being an x.com/Twitter link at all. Confirms the
    match falls on an actual hostname label boundary instead of
    anywhere in the URL string.

    SOCIAL_DOMAINS mixes two key shapes:
      - full-hostname fragments, e.g. "x.com", "wa.me", "whatsapp.com"
        -> must equal the netloc or be a subdomain of it
      - bare brand names, e.g. "facebook", "twitter", "youtube"
        -> must appear as one of the netloc's dot-separated labels
        (so "myfacebooktools.com" doesn't false-match "facebook")
    """
    try:
        netloc = urlparse(href).netloc.lower().split(":")[0]
    except Exception:
        return False
    if not netloc:
        return False
    domain = domain.lower()
    if "." in domain:
        return netloc == domain or netloc.endswith("." + domain)
    return domain in netloc.split(".")

# Same signals playwright_worker.py checks for -- used here to decide
# whether a plain requests.get() response is actually a bot-check page,
# so unmapped domains can auto-escalate to Playwright.
BLOCK_SIGNALS = [
    "captcha", "are you human", "cf-browser-verification",
    "ddos-guard", "checking your browser", "verify you are human",
    "enable cookies to continue", "please enable cookies",
    "security check", "access to this page has been denied",
    "verify you're human",
]


# ==========================================================
# Small helpers
# ==========================================================

def clean(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def clean_multiline(text):
    """Like clean(), but converts <br> tags to real newlines and
    preserves paragraph breaks instead of collapsing everything
    to a single line."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def is_meaningful(text):
    """True if text has real content once commas/whitespace are stripped.
    Guards against junk like a keywords tag whose content is just ", "."""
    return bool(re.sub(r"[,\s]", "", text or ""))


def empty_business():
    return {
        "Business Name": "",
        "Street": "",
        "City": "",
        "State": "",
        "Zipcode": "",
        "Country": "",
        "Phone": "",
        "Website URL": "",
        "Keywords": "",
        "Description": "",
        "Hours": "",
        "Social Media Links": {},
        "GBP Link": "",
        "Business Email": "",
        "Category": "",
        "Logo": "",
        "Photos": []
    }


def _looks_blocked(html_text):
    combined = html_text[:4000].lower()
    return any(s in combined for s in BLOCK_SIGNALS)


# Signals that the fetched HTML is Cloudflare's own error page (origin
# server down / unreachable / timed out) rather than real page content.
# Distinct from BLOCK_SIGNALS above: a 5xx error page can render with a
# normal HTML layout and still count as a "successful" fetch from some
# fetchers (e.g. Playwright's page.goto() considers navigation
# successful even though what rendered is Cloudflare's error page for
# an unreachable origin, not the actual site), so _looks_blocked's
# bot-check phrases don't catch it. Without this separate check, the
# parser runs on the error page as if it were real content -- every
# business field comes back empty, and the generic anchor scan can pick
# up an incidental link from the error page itself (e.g. Cloudflare's
# troubleshooting docs) and mistakenly set it as the Website URL.
CLOUDFLARE_ERROR_SIGNALS = [
    "error 521", "error 522", "error 523", "error 524", "error 525", "error 526",
    "web server is down", "connection timed out", "origin is unreachable",
    "cloudflare ray id",
]


def _looks_like_cloudflare_error(html_text):
    combined = html_text[:4000].lower()
    return any(s in combined for s in CLOUDFLARE_ERROR_SIGNALS)


# Domains/patterns that indicate a Google Maps / directions link rather
# than the business's own external website. Several site templates put
# a "Directions" or map-pin link among the page's external anchors, and
# a naive "first external, non-social link wins" scan will grab that
# instead of the real website unless it's explicitly excluded (this bit
# Nearfinder: its Website URL scan picked up a maps.google.com.br link
# instead of the business's actual site).
def _is_maps_link(href):
    href = href.lower()
    return "google" in href and "map" in href


# ==========================================================
# Fetchers
# ==========================================================

def fetch_via_requests(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def fetch_via_playwright(url, worker_path="playwright_worker.py", timeout_ms=45000):
    """Runs playwright_worker.py as a subprocess and returns rendered HTML.
    Raises RuntimeError if the fetch failed or was blocked."""

    proc = subprocess.run(
        [sys.executable, worker_path, url, str(timeout_ms)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=(timeout_ms / 1000) + 30,
    )

    stdout = proc.stdout.strip()

    if not stdout:
        raise RuntimeError(
            f"playwright_worker.py produced no output. stderr: {proc.stderr}"
        )

    # The worker prints exactly one JSON line to stdout; take the last
    # non-empty line in case anything else leaked onto stdout.
    last_line = [line for line in stdout.splitlines() if line.strip()][-1]
    data = json.loads(last_line)

    if not data.get("success"):
        raise RuntimeError(f"Playwright fetch failed: {data.get('debug')}")

    return data["html"]


# ==========================================================
# Site parser: nearfinderus.com
# ==========================================================

def _extract_nearfinder_redirect_url(href):
    """This site wraps the real external website behind a same-domain
    click-tracking redirect link (e.g.
    "/en/empresa/redirect?url=<url-encoded target>&id=...&cache=..."),
    rather than linking to the business's site directly. That href is
    relative, so the generic "must start with http" anchor scan below
    skips it entirely and falls through to the next absolute external
    link it finds instead -- which is frequently one of NearFinder's
    own links (blog, corporate site) in the page footer, not the
    business's actual website. Detects this redirect shape and returns
    the decoded target URL, or None if `href` doesn't match it.

    NOTE: this same /empresa/redirect wrapper is used for MULTIPLE kinds
    of outbound CTAs on this template -- not just the "Website" button.
    Confirmed on the FOCAL listing: the WhatsApp "click to chat" button
    (top of page, before the Website button in document order) is ALSO
    wrapped in /empresa/redirect?url=https://api.whatsapp.com/send?..., 
    and it renders earlier in the HTML than the real Website button
    further down in the "Social / Internet" section. Because of that,
    callers MUST NOT treat "found a redirect link" as "found the
    website" -- the unwrapped target still needs to be checked against
    SOCIAL_DOMAINS / maps patterns just like a plain absolute href
    would be (see parse_nearfinderus's Website URL loop)."""
    if "/empresa/redirect" not in href.lower():
        return None
    target = parse_qs(urlparse(href).query).get("url")
    return target[0] if target else None


def parse_nearfinderus(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- JSON-LD ----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            obj = json.loads(script.string)
        except Exception:
            continue

        objects = obj if isinstance(obj, list) else [obj]

        for data in objects:

            if not isinstance(data, dict):
                continue

            if data.get("@type") not in (
                "LocalBusiness", "Organization", "Corporation",
                "Store", "ProfessionalService",
            ):
                continue

            business["Business Name"] = data.get("name", business["Business Name"])

            img = data.get("image")
            if img:
                business["Logo"] = urljoin(url, img)

            addr = data.get("address", {})
            business["Street"] = addr.get("streetAddress", business["Street"])

            locality = addr.get("addressLocality", "")
            if "-" in locality:
                city, state = locality.split("-", 1)
                business["City"] = city.strip()
                business["State"] = state.strip()
            else:
                business["City"] = locality

            business["Zipcode"] = addr.get("postalCode", business["Zipcode"])
            business["Country"] = addr.get("addressCountry", business["Country"])

            if data.get("telephone"):
                business["Phone"] = data["telephone"]

            # NOTE: JSON-LD "url" on this site points back at the
            # directory listing itself, not the real business site --
            # deliberately not used to set Website URL.

            if data.get("description"):
                business["Description"] = clean(data["description"])

            if data.get("email"):
                business["Business Email"] = data["email"]

            if data.get("openingHours"):
                business["Hours"] = data["openingHours"]

            if data.get("openingHoursSpecification"):
                business["Hours"] = data["openingHoursSpecification"]

            if data.get("sameAs"):
                links = data["sameAs"]
                if isinstance(links, list):
                    for link in links:
                        for domain, name in SOCIAL_DOMAINS.items():
                            if domain in link.lower():
                                business["Social Media Links"][name] = link

    # ---- Meta description ----
    meta = soup.find("meta", attrs={"name": "description"})

    if meta:
        description = clean(meta.get("content", ""))

        if description and not business["Description"]:
            business["Description"] = description

        match = re.search(r"Company specialized in (.+?)\.", description, re.I)
        if match:
            business["Category"] = match.group(1).strip()

    # ---- Full description override (nf-show-more-text widget) ----
    # The <meta name="description"> here is deliberately truncated for
    # SEO snippets; the full write-up lives in this widget's "text" attr.
    show_more = soup.select_one("nf-show-more-text")

    if show_more:
        full_text = clean_multiline(show_more.get("text", ""))
        if full_text:
            business["Description"] = full_text

    # ---- Meta keywords ----
    keywords = soup.find("meta", attrs={"name": "keywords"})
    if keywords:
        business["Keywords"] = keywords.get("content", "")

    # ---- OpenGraph ----
    for meta in soup.find_all("meta"):
        prop = meta.get("property", "").lower()

        if prop == "og:image" and not business["Logo"]:
            business["Logo"] = urljoin(url, meta.get("content", ""))
        elif prop == "og:title" and not business["Business Name"]:
            business["Business Name"] = meta.get("content", "")
        elif prop == "og:description" and not business["Description"]:
            business["Description"] = meta.get("content", "")
        # og:url is the directory listing's own URL here, not the
        # business's real external site -- intentionally not used.

    # ---- Phone ----
    tel = soup.select_one('a[href^="tel:"]')
    if tel:
        business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Email ----
    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").strip()

    # ---- Website URL (external site only, checked before Social) ----
    # The real website link on this template is wrapped in a same-domain
    # click-tracking redirect (see _extract_nearfinder_redirect_url), but
    # that SAME redirect wrapper is also used for other outbound CTAs on
    # the page -- confirmed: the WhatsApp "click to chat" button uses the
    # identical /empresa/redirect?url=... shape, targeting
    # api.whatsapp.com, and it sits earlier in the HTML (top CTA row)
    # than the real "Website" button (Social / Internet section further
    # down the page). Treating "found any redirect link" as "found the
    # website" therefore grabs the WhatsApp target first and never
    # reaches the real site.
    #
    # Fix: after unwrapping a redirect target, run it through the SAME
    # exclusion checks (social/WhatsApp domains, Google Maps/directions
    # links) used below for plain absolute external anchors, and keep
    # scanning rather than stopping if it's excluded. Only accept -- and
    # then break on -- a redirect target (or plain external link) that
    # survives those checks.
    for a in soup.find_all("a", href=True):
        href = a["href"]

        redirect_target = _extract_nearfinder_redirect_url(href)
        if redirect_target:
            if any(domain in redirect_target.lower() for domain in SOCIAL_DOMAINS):
                continue
            if _is_maps_link(redirect_target):
                continue
            business["Website URL"] = redirect_target
            break

        if not href.startswith("http"):
            continue
        if "nearfinderus.com" in href.lower() or "nearfinder.com" in href.lower():
            continue
        if any(domain in href.lower() for domain in SOCIAL_DOMAINS):
            continue
        if _is_maps_link(href):
            continue

        if not business["Website URL"]:
            business["Website URL"] = href
            break

    # ---- Social Media ----
    # NOTE: this deliberately re-scans ALL anchors (including redirect-
    # wrapped ones) rather than only plain absolute hrefs, so that a
    # WhatsApp CTA wrapped in /empresa/redirect?url=... still gets
    # recorded under Social Media Links even though it was correctly
    # excluded from Website URL above.
    for a in soup.find_all("a", href=True):
        href = a["href"]

        redirect_target = _extract_nearfinder_redirect_url(href)
        link_target = redirect_target if redirect_target else href

        for domain, network in SOCIAL_DOMAINS.items():
            if domain in link_target.lower():
                business["Social Media Links"][network] = link_target

    # ---- Hours (HTML fallback -- table, not JSON-LD) ----
    if not business["Hours"]:
        hours_table = soup.select_one("div.table-horario-funcionamento table")

        if hours_table:
            hours_list = []
            for tr in hours_table.select("tbody tr"):
                cells = tr.find_all("td")
                if len(cells) >= 2:
                    day = clean(cells[0].get_text())
                    time_range = clean(cells[1].get_text())
                    if day and time_range:
                        hours_list.append(f"{day}: {time_range}")

            if hours_list:
                business["Hours"] = "; ".join(hours_list)

    # ---- Category (link-based fallback) ----
    if not business["Category"]:
        category_links = soup.select('a[href*="/business-directory/category_"]')
        categories = []

        for a in category_links:
            text = clean(a.get_text())
            if text and text not in categories:
                categories.append(text)

        if categories:
            business["Category"] = ", ".join(categories)

    # ---- Images ----
    images = []
    for img in soup.find_all("img"):
        src = img.get("src")
        if src:
            src = urljoin(url, src)
            if src not in images:
                images.append(src)

    business["Photos"] = images

    return business


# ==========================================================
# Site parser: smallbusinessusa.com
# ==========================================================

_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
    "puerto rico",
}

_US_STATE_ABBR = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi",
    "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi",
    "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc",
    "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut",
    "vt", "va", "wa", "wv", "wi", "wy", "dc", "pr",
}


def _looks_like_us_state(value):
    """True if `value` reads as a US state/territory name or its
    2-letter postal abbreviation (case-insensitive)."""
    text = clean(value).strip().lower()
    if not text:
        return False
    if text in _US_STATE_NAMES:
        return True
    if len(text) == 2 and text in _US_STATE_ABBR:
        return True
    return False


def _resolve_city_state(locality_val, region_val):
    """Given a (locality, region) pair whose City/State meaning may or
    may not be swapped on this site's template (see note above), figures
    out which one is actually the state by checking whether its value
    reads as a US state name/abbreviation, and returns (city, state)
    with the roles corrected accordingly.

    Falls back to the standard convention (locality=city, region=state)
    when neither or both values look like a state, since that's the
    far more common template behavior generally."""

    region_is_state = _looks_like_us_state(region_val)
    locality_is_state = _looks_like_us_state(locality_val)

    if region_is_state and not locality_is_state:
        # Normal, non-swapped order: locality is city, region is state.
        return locality_val, region_val

    if locality_is_state and not region_is_state:
        # Swapped on this listing: region is city, locality is state.
        return region_val, locality_val

    # Ambiguous (neither or both look like a state) -- default to the
    # standard convention rather than guessing.
    return locality_val, region_val


def parse_smallbusinessusa(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- business:contact_data Open Graph extension (primary source) ----
    contact_meta = {}
    for meta in soup.find_all("meta", property=True):
        prop = meta["property"]
        if prop.startswith("business:contact_data:"):
            key = prop.split(":")[-1]
            contact_meta[key] = clean(meta.get("content", ""))

    business["Street"] = contact_meta.get("street_address", "")
    # City/State roles are resolved dynamically per-listing -- see the
    # module note above for why a hardcoded swap doesn't work here.
    business["City"], business["State"] = _resolve_city_state(
        contact_meta.get("locality", ""), contact_meta.get("region", "")
    )
    business["Zipcode"] = contact_meta.get("postal_code", "")
    business["Country"] = contact_meta.get("country_name", "")
    business["Phone"] = contact_meta.get("phone_number", "")
    business["Website URL"] = contact_meta.get("website", "")

    # ---- JSON-LD (name/logo, backs up address/phone if missing) ----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:

            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
                continue

            business["Business Name"] = obj.get("name", business["Business Name"])

            if obj.get("image") and not business["Logo"]:
                business["Logo"] = urljoin(url, obj["image"])

            if obj.get("telephone") and not business["Phone"]:
                business["Phone"] = obj["telephone"]

            addr = obj.get("address", {})

            if not business["Street"]:
                business["Street"] = addr.get("streetAddress", "")
            # City/State roles may or may not be swapped here too, same
            # as the contact_data meta tags above -- resolve dynamically
            # rather than assuming either fixed order (see module note).
            if not business["City"] and not business["State"]:
                business["City"], business["State"] = _resolve_city_state(
                    addr.get("addressLocality", ""), addr.get("addressRegion", "")
                )
            if not business["Zipcode"]:
                business["Zipcode"] = addr.get("postalCode", "")
            if not business["Country"]:
                business["Country"] = addr.get("addressCountry", "")

    # ---- Business Name fallback (visible <h1>) ----
    if not business["Business Name"]:
        h1 = soup.select_one("article.detail h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Meta description ----
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc:
        desc = clean(meta_desc.get("content", ""))
        if is_meaningful(desc):
            business["Description"] = desc

    # ---- Meta keywords ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            business["Keywords"] = clean(kw_raw)

    # ---- Phone fallback (tel: link) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Email (mailto: link, if any) ----
    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").strip()

    # ---- Website URL fallback ("Visit Website" button) ----
    if not business["Website URL"]:
        website_link = soup.select_one("#visit-website")
        if website_link and website_link.get("href"):
            business["Website URL"] = website_link["href"]

    # ---- Category (breadcrumb inside the listing article) ----
    category_links = soup.select("article.detail ul.breadcrumb a")
    categories = []
    for a in category_links:
        text = clean(a.get_text())
        if text and text not in categories:
            categories.append(text)
    if categories:
        business["Category"] = ", ".join(categories)

    # ---- Logo fallback (og:image) ----
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Photos (full-size gallery images, fallback to thumbnails) ----
    photos = []
    for a in soup.select("div.image-gallery a.image-link[href]"):
        photo_url = urljoin(url, a["href"])
        if photo_url not in photos:
            photos.append(photo_url)
    if not photos:
        for img in soup.select("div.image-gallery img[src]"):
            photo_url = urljoin(url, img["src"])
            if photo_url not in photos:
                photos.append(photo_url)
    business["Photos"] = photos

    # ---- Social Media Links (real anchors only) ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# Site parser: zeemaps.com
# ==========================================================

ZEEMAPS_BASE = "https://www.zeemaps.com"


def _zeemaps_group_id(url):
    qs = parse_qs(urlparse(url).query)
    group = qs.get("group") or qs.get("g")
    if not group:
        raise ValueError(f"No ?group= or ?g= parameter found in ZeeMaps URL: {url}")
    return group[0]


def _zeemaps_get(path, **params):
    response = requests.get(f"{ZEEMAPS_BASE}{path}", params=params, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.json()


def parse_zeemaps(url, html=None):
    """html is accepted (and ignored) so this fits the same
    parser(url, html) signature as the other site parsers, even
    though ZeeMaps data comes from API calls, not page HTML."""

    group = _zeemaps_group_id(url)

    # ---- Data version hash (required by /emarkers) ----
    version = _zeemaps_get("/regions/version", g=group).get("v", "")

    # ---- Marker list ----
    markers = _zeemaps_get("/emarkers", g=group, k="REGULAR", e="false", v=version)

    # ---- Custom field id -> name mapping (generic, not hardcoded) ----
    attrs_raw = _zeemaps_get("/data/attributes", group=group)
    field_names = {fid: meta.get("n", "").strip().lower() for fid, meta in attrs_raw.items()}

    # ---- Map-level description fallback ----
    mapprops = _zeemaps_get("/data/mapprops", group=group, readonly="true")
    map_about = clean_multiline(mapprops.get("mp", {}).get("about", ""))

    results = []

    for m in markers:
        marker_id = m.get("id")
        business = empty_business()

        # Base fields from the marker list
        business["Business Name"] = m.get("nm", "")
        business["Street"] = m.get("s", "")
        business["City"] = m.get("city", "")
        business["State"] = m.get("state", "")
        business["Zipcode"] = m.get("zip", "")
        business["Country"] = m.get("cty", "")

        # ---- Per-marker popup detail (has the real field values) ----
        try:
            detail = _zeemaps_get(
                "/etext",
                g=group,
                j=1,
                sh="",
                _dc=random.random(),
                eids=f"[{marker_id}]",
            )
            if isinstance(detail, list):
                detail = detail[0] if detail else {}
        except Exception:
            detail = {}

        if detail.get("title"):
            business["Business Name"] = detail["title"]

        addr = detail.get("ad", {})
        if addr.get("street"):
            business["Street"] = addr["street"]
        if addr.get("city"):
            business["City"] = addr["city"]
        if addr.get("state"):
            business["State"] = addr["state"]
        if addr.get("postcode"):
            business["Zipcode"] = addr["postcode"]
        if addr.get("country"):
            business["Country"] = addr["country"]

        # ---- Address fallback: some ZeeMaps groups never populate ----
        # separate city/state/zip fields at all -- the whole address
        # (e.g. "131 Continental Dr, Suite 305, Newark, Delaware 19713")
        # sits in the street field instead, from both the marker list
        # AND the /etext detail call. When that happens, split it the
        # same way blinx.biz/place123.net addresses are split, rather
        # than leaving City/State blank. Only fires when City AND State
        # are both still empty, so maps that DO give clean separate
        # fields are never touched.
        if business["Street"] and not business["City"] and not business["State"]:
            street, city, state, zipcode = _split_blinx_address(business["Street"])
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            if not business["Zipcode"]:
                business["Zipcode"] = zipcode

        # ---- Custom fields, resolved generically by name ----
        unmapped_fields = {}
        for fid, value in detail.get("fields", {}).items():
            if not value:
                continue
            name = field_names.get(fid, "")
            if name == "phone":
                business["Phone"] = value
            elif name == "website":
                business["Website URL"] = value
            elif name == "email":
                business["Business Email"] = value
            elif name == "description":
                business["Description"] = clean_multiline(value)
            else:
                # Preserve any custom field this map defines that we
                # don't have a dedicated slot for, instead of dropping it.
                label = attrs_raw.get(fid, {}).get("n", fid)
                unmapped_fields[label] = value

        if unmapped_fields:
            business["Zeemaps Extra Fields"] = unmapped_fields

        if not business["Description"]:
            business["Description"] = map_about

        # ---- Photo (embedded as an <img> tag inside the "i" field) ----
        img_html = detail.get("i", "")
        if img_html:
            img_match = re.search(r"src=['\"]([^'\"]+)['\"]", img_html)
            if img_match:
                photo_url = img_match.group(1)
                business["Logo"] = photo_url
                business["Photos"] = [photo_url]

        results.append(business)

    if not results:
        return empty_business()

    # Most ZeeMaps listing pages carry exactly one marker (the business
    # this map was published for). Return that single dict directly so
    # callers get the same shape as the other parsers; only fall back
    # to a list if the map genuinely has multiple markers.
    return results[0] if len(results) == 1 else results


# ==========================================================
# Site parser: callupcontact.com
# ==========================================================


HEADING_TAGS = re.compile(r"^h[1-6]$")

_CALLUPCONTACT_KEYWORD_BOILERPLATE = {
    "businessprofile",
    "ratings",
    "business profiles",
    "products and services",
    "directions",
    "maps",
    "business listing",
    "telephone",
    "fax",
    "postal address",
    "postal code",
}


def _decode_cf_email(hex_string):
    """Decodes Cloudflare's email-obfuscation hex string back to a
    plain email address. The XOR-decoded bytes come out as literal
    numeric HTML entities (e.g. "&#105;&#110;..."), so an extra
    html.unescape() pass is needed to get the real address."""
    try:
        key = int(hex_string[:2], 16)
        decoded = "".join(
            chr(int(hex_string[i:i + 2], 16) ^ key)
            for i in range(2, len(hex_string), 2)
        )
        return html.unescape(decoded)
    except Exception:
        return ""


def _find_cf_email(soup):
    # Form 1: <a href="/cdn-cgi/l/email-protection#HEX">
    link = soup.select_one('a[href*="/cdn-cgi/l/email-protection#"]')
    if link:
        hex_part = link["href"].split("#", 1)[-1]
        decoded = _decode_cf_email(hex_part)
        if decoded:
            return decoded

    # Form 2: <span class="__cf_email__" data-cfemail="HEX">
    span = soup.select_one("[data-cfemail]")
    if span:
        decoded = _decode_cf_email(span["data-cfemail"])
        if decoded:
            return decoded

    return ""


def _is_leaf(tag):
    """True if tag has no nested element children (only text/whitespace).
    Used to avoid matching wrapper divs whose get_text() happens to
    include both the label and the value concatenated together."""
    return tag.find(True) is None


def _find_label_value_element(soup, label):
    """Finds any leaf tag whose text exactly matches `label`, then
    returns the neighboring element that holds its value (not the
    text -- the element itself, so callers can inspect its structure,
    e.g. pull out just the <a> tags for a multi-value field)."""

    for tag in soup.find_all(True):
        if not _is_leaf(tag):
            continue
        if clean(tag.get_text()).lower() != label.lower():
            continue

        sib = tag.find_next_sibling()
        while sib is not None and not clean(sib.get_text()):
            sib = sib.find_next_sibling()
        if sib:
            return sib

        if tag.parent:
            parent_sib = tag.parent.find_next_sibling()
            while parent_sib is not None and not clean(parent_sib.get_text()):
                parent_sib = parent_sib.find_next_sibling()
            if parent_sib:
                return parent_sib

    return None


def _value_by_label(soup, label, separator=" "):
    elem = _find_label_value_element(soup, label)
    return clean(elem.get_text(separator=separator)) if elem else ""


# Kept as a thin wrapper so any external references to the old name
# keep working; heading-only matching is no longer accurate for this
# site's actual markup.
def _value_after_heading(soup, label, separator=" "):
    return _value_by_label(soup, label, separator=separator)


def parse_callupcontact(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name (page <h1>) ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Category (multiple tag links under one heading) ----
    category_text = _value_after_heading(soup, "Category", separator=", ")
    if category_text:
        business["Category"] = category_text

    # ---- About Us (description) ----
    description = _value_after_heading(soup, "About Us")
    if description:
        business["Description"] = description

    # ---- Call & Message ----
    phone = _value_after_heading(soup, "Telephone")
    if phone:
        business["Phone"] = phone

    website = _value_after_heading(soup, "Website")
    if website:
        business["Website URL"] = website

    # ---- Email (Cloudflare-obfuscated, not a plain mailto:) ----
    email = _find_cf_email(soup)
    if email:
        business["Business Email"] = email

    # ---- Address ----
    street = _value_after_heading(soup, "Street Address")
    if street:
        business["Street"] = street

    city = _value_after_heading(soup, "City")
    if city:
        business["City"] = city

    state = _value_after_heading(soup, "State / Province")
    if state:
        business["State"] = state

    zipcode = _value_after_heading(soup, "Zip / Postal Code")
    if zipcode:
        business["Zipcode"] = zipcode

    country = _value_after_heading(soup, "Country")
    if country:
        business["Country"] = country

    # ---- Meta description fallback (page-level, matches About Us usually) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Meta keywords (strip this template's fixed boilerplate tail --
    #      see _CALLUPCONTACT_KEYWORD_BOILERPLATE above) ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            tokens = [clean(t) for t in kw_raw.split(",")]
            tokens = [
                t for t in tokens
                if t and t.lower() not in _CALLUPCONTACT_KEYWORD_BOILERPLATE
            ]
            if tokens:
                business["Keywords"] = ", ".join(tokens)

    return business


# ==========================================================
# Site parser: zumvu.com
# ==========================================================

def parse_zumvu(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- JSON-LD (ProfilePage -> mainEntity) ----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except Exception:
            continue

        entity = data.get("mainEntity") if isinstance(data, dict) else None
        if not isinstance(entity, dict):
            continue
        # Template mislabels businesses as "Person" -- accept either.
        if entity.get("@type") not in ("Person", "Organization", "LocalBusiness"):
            continue

        business["Business Name"] = entity.get("name", business["Business Name"])

        if entity.get("image"):
            business["Logo"] = urljoin(url, entity["image"])

        if entity.get("description") and is_meaningful(entity["description"]):
            business["Description"] = clean(entity["description"])

        addr = entity.get("address", {})
        if isinstance(addr, dict):
            business["Street"] = addr.get("streetAddress", business["Street"])
            business["City"] = addr.get("addressLocality", business["City"])
            business["State"] = addr.get("addressRegion", business["State"])
            business["Zipcode"] = addr.get("postalCode", business["Zipcode"])
            business["Country"] = addr.get("addressCountry", business["Country"])

        knows_about = entity.get("knowsAbout")
        if knows_about and isinstance(knows_about, list):
            terms = [clean(t) for t in knows_about if clean(t)]
            if terms:
                business["Keywords"] = ", ".join(terms)

        if entity.get("sameAs"):
            links = entity["sameAs"]
            if isinstance(links, list):
                for link in links:
                    for domain, name in SOCIAL_DOMAINS.items():
                        if domain in link.lower():
                            business["Social Media Links"][name] = link

    # ---- Business Name fallback (visible <h1>) ----
    if not business["Business Name"]:
        h1 = soup.select_one(".prottlebx h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Contact block: phone / email / website by icon class ----
    contact_ul = soup.select_one(".contactbox.extncontctbx ul.abtcontact-page")
    if contact_ul:
        for li in contact_ul.find_all("li"):
            icon = li.find("i")
            a = li.find("a", href=True)
            if not icon or not a:
                continue
            icon_classes = icon.get("class", [])

            if "fa-phone" in icon_classes:
                business["Phone"] = a["href"].replace("tel:", "").strip()
            elif "fa-envelope" in icon_classes:
                business["Business Email"] = a["href"].replace("mailto:", "").strip()
            elif "fa-globe" in icon_classes:
                business["Website URL"] = a["href"]

    # ---- Description (About section -- richer than meta description) ----
    about = soup.select_one(".resabout .addinfo")
    if about:
        text = clean_multiline(about.get_text(separator="\n"))
        if is_meaningful(text):
            business["Description"] = text

    # ---- Meta description fallback (usually empty on this template) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords fallback: meta tag, then the visible tag pills ----
    if not business["Keywords"]:
        meta_kw = soup.find("meta", attrs={"name": "keywords"})
        if meta_kw and is_meaningful(meta_kw.get("content", "")):
            business["Keywords"] = clean(meta_kw["content"])

    if not business["Keywords"]:
        tags = [clean(t.get_text()) for t in soup.select(".taginfoabout .right-tags")]
        tags = [t for t in tags if t]
        if tags:
            business["Keywords"] = ", ".join(tags)

    # ---- Address fallback (visible Location block, if JSON-LD missing) ----
    if not any([business["Street"], business["City"], business["State"]]):
        loc_li = soup.select_one(".locflexfirstcol ul.abtcontact-page li")
        if loc_li:
            addr_text = clean_multiline(loc_li.get_text(separator="\n"))
            lines = [l for l in addr_text.split("\n") if l]
            if lines:
                business["Street"] = lines[0]
            if len(lines) > 1:
                # e.g. "Dover, Delaware 19901, UNITED STATES"
                business["City"] = lines[1]

    # ---- Logo fallback (og:image) ----
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Social Media (real anchors, in case JSON-LD sameAs was empty) ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower() and "zumvu.com" not in href.lower():
                business["Social Media Links"][network] = href

    return business



# ==========================================================
# Site parser: blinx.biz
# ==========================================================

def _split_blinx_address(address):
    street, city, state, zipcode = "", "", "", ""

    parts = [p.strip() for p in address.split(",") if p.strip()]

    if len(parts) >= 3:
        street = ", ".join(parts[:-2])
        city = parts[-2]
        state_zip = parts[-1]
    elif len(parts) == 2:
        street = parts[0]
        state_zip = parts[1]
    elif len(parts) == 1:
        state_zip = parts[0]
    else:
        state_zip = ""

    # "Delaware 19901" -> state="Delaware", zip="19901". Zip can be a
    # plain US 5-digit/ZIP+4 code; if the trailing token isn't
    # digit-like, leave everything in state rather than guessing.
    match = re.match(r"^(.*?)\s+([\w-]*\d[\w-]*)$", state_zip.strip())
    if match:
        state = match.group(1).strip()
        zipcode = match.group(2).strip()
    else:
        state = state_zip.strip()

    return street, city, state, zipcode


# This is the shape Blinx renders the full address in once the page is
# rendered: "<street>, <city>, <ST> ,<zip>" (note the loose comma/space
# right before the zip -- that's how their own template formats it, not
# a parsing artifact on our end). Captured as its own regex because the
# API's raw "address" field (see _find_brownbook_record / below) is only
# ever the bare street with no commas at all, so it can't be split the
# same way place123/freelistingusa addresses are split.
_BLINX_RENDERED_ADDRESS_RE = re.compile(
    r"^(?P<street>.+?),\s*(?P<city>[^,]+?),\s*(?P<state>[A-Za-z]{2,})\s*,?\s*(?P<zip>\d{5}(?:-\d{4})?)$"
)


def _extract_blinx_address_from_dom(soup):
    """Blinx's business-detail API only returns the raw street address
    in its "address" field (e.g. "8910 University Center Ln" -- no
    city/state/zip attached, confirmed via the actual API response).
    City/State/Zip only ever appear together in the rendered page text,
    in a line shaped like "8910 University Center Ln, San Diego, CA
    ,92122". Scan the rendered text for that shape instead of trying to
    reconstruct it from the API payload alone.

    Returns (street, city, state, zip) or None if no matching line is
    found (e.g. because the HTML was fetched via plain requests before
    the page hydrated and rendered the address)."""

    for raw_line in soup.get_text(separator="\n").split("\n"):
        line = clean(raw_line)
        if not line or "," not in line:
            continue
        match = _BLINX_RENDERED_ADDRESS_RE.match(line)
        if match:
            return (
                match.group("street").strip(),
                match.group("city").strip(),
                match.group("state").strip(),
                match.group("zip").strip(),
            )

    return None


def _find_brownbook_record(obj, _depth=0):
    """Recursively searches a decoded __NEXT_DATA__ JSON tree for the
    first dict that has a "brownbook_id" key, which identifies the
    actual listing record regardless of how deeply Next.js nests it
    inside pageProps.

    NOTE: on the current version of blinx.biz this record is loaded by
    a client-side XHR call made *after* the initial page load (verified
    via the browser Network tab), not embedded in __NEXT_DATA__ at all.
    This function is kept because some listings/older pages may still
    embed it server-side, but callers must not assume it will find
    anything -- see parse_blinx, which treats the rendered DOM as the
    primary source and this as a bonus when present."""

    if _depth > 12:
        return None

    if isinstance(obj, dict):
        if "brownbook_id" in obj:
            return obj
        for value in obj.values():
            found = _find_brownbook_record(value, _depth + 1)
            if found:
                return found

    elif isinstance(obj, list):
        for item in obj:
            found = _find_brownbook_record(item, _depth + 1)
            if found:
                return found

    return None


def _blinx_links_to_business(business, links):
    """The record's "links" field can plausibly be a list of plain
    URL strings or a list of {"type"/"name": ..., "url"/"href": ...}
    dicts, depending on link type (website vs. social). Handle both
    shapes rather than assuming one."""

    if not isinstance(links, list):
        return

    for entry in links:
        if isinstance(entry, str):
            href = entry
        elif isinstance(entry, dict):
            href = entry.get("url") or entry.get("href") or entry.get("link") or ""
        else:
            continue

        if not href:
            continue

        matched_social = False
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href
                matched_social = True
                break

        if not matched_social and not business["Website URL"]:
            business["Website URL"] = href


def parse_blinx(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Primary source: Next.js __NEXT_DATA__ hydration payload ----
    # Only present when a listing happens to be server-rendered with the
    # record already embedded; on the common case (record loaded via a
    # post-load XHR call) this will simply come back None and every
    # field below falls through to the rendered-DOM / meta-tag sources.
    record = None
    next_data_script = soup.find("script", id="__NEXT_DATA__")

    if next_data_script and next_data_script.string:
        try:
            next_data = json.loads(next_data_script.string)
        except Exception:
            next_data = None

        if next_data:
            record = _find_brownbook_record(next_data)

    if record:
        business["Business Name"] = record.get("name") or record.get("title") or ""

        business["Country"] = record.get("country", "")
        business["Phone"] = record.get("phone", "")
        business["Business Email"] = record.get("email", "")

        description = record.get("description", "")
        if is_meaningful(description):
            business["Description"] = clean_multiline(description)

        logo = record.get("logo") or record.get("image")
        if logo:
            business["Logo"] = urljoin(url, logo)

        _blinx_links_to_business(business, record.get("links"))

        # The API's "address" field is only ever the bare street (e.g.
        # "8910 University Center Ln") with no city/state/zip attached.
        # Only run it through the comma-splitter if it actually looks
        # like a full "street, city, state zip" string; otherwise it's
        # just the street, and running it through the splitter would
        # dump the whole thing into State (which is what the previous
        # version of this parser did).
        address = record.get("address", "")
        if address:
            if "," in address:
                street, city, state, zipcode = _split_blinx_address(address)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode
            else:
                business["Street"] = clean(address)

    # ---- Address: prefer the rendered DOM ----
    # This is the authoritative source for the full address (street +
    # city + state + zip together) since the API/record only ever gives
    # the bare street. Overrides whatever the record block above set.
    dom_address = _extract_blinx_address_from_dom(soup)
    if dom_address:
        street, city, state, zipcode = dom_address
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Business Name fallback (og:title / <title>) ----
    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"])
        elif soup.title:
            business["Business Name"] = clean(soup.title.get_text()).split("|")[0].strip()

    # ---- Description fallback (meta description) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Logo fallback (og:image) ----
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Phone fallback (tel: link on the page) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Email fallback (mailto: link on the page) ----
    if not business["Business Email"]:
        email = soup.select_one('a[href^="mailto:"]')
        if email:
            business["Business Email"] = email["href"].replace("mailto:", "").strip()

    # ---- Website / social fallback (visible anchors) ----
    # This is also the primary source in practice: since the record is
    # loaded client-side, the real website link is almost always only
    # available once rendered, as a plain anchor here, rather than via
    # `record["links"]` above (which is frequently unavailable when
    # fetched via plain requests).
    for a in soup.find_all("a", href=True):
        href = a["href"]

        if not href.startswith("http"):
            continue
        if "blinx.biz" in href.lower():
            continue
        if "google.com/maps" in href.lower() or _is_maps_link(href):
            continue

        matched_social = False
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"].setdefault(network, href)
                matched_social = True
                break

        if not matched_social and not business["Website URL"]:
            business["Website URL"] = href

    return business


# ==========================================================
# Site parser: place123.net
# ==========================================================

_PLACE123_LABELS = {
    "owner name": None,
    "phone": "Phone",
    "website": "Website URL",
    "url": "Website URL",
    "business email": "Business Email",
    "about us": "Description",
    "related searches": "Keywords",
}

_PLACE123_TERMINATORS = {
    "what do you think about us?",
    "your nickname",
    "comments",
    "start a discussion",
    "places nearby",
    "edit business",
    "your business in this directory?",
    "add your business",
    "position on map",
    "gps coordinates",
    "find nearby",
    "street view",
    "write a review",
}


def parse_place123(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (og:title matches the visible heading) ----
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        business["Business Name"] = clean(og_title["content"])

    if not business["Business Name"]:
        h_tag = soup.find(re.compile(r"^h[1-6]$"))
        if h_tag:
            business["Business Name"] = clean(h_tag.get_text())

    # ---- Logo ----
    logo_img = soup.find("img", alt=re.compile("location logo", re.I))
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Whole-page text as lines (this template has no wrapping tags
    #      around Category/Address/Country or the Owner Name..Related
    #      Searches block -- everything is <br>-separated plain text) ----
    lines = [
        clean(line)
        for line in soup.get_text(separator="\n").split("\n")
    ]
    lines = [l for l in lines if l]
    label_keys = set(_PLACE123_LABELS.keys())

    # ---- Category / Address / Country (positional: the 3 lines right
    #      after the business-name heading) ----
    name_idx = None
    if business["Business Name"]:
        target = business["Business Name"].lower()
        for idx, line in enumerate(lines):
            if line.lower() == target:
                name_idx = idx
                break

    if name_idx is not None:
        if name_idx + 1 < len(lines) and lines[name_idx + 1].rstrip(":").lower() not in label_keys:
            business["Category"] = lines[name_idx + 1]

        if name_idx + 2 < len(lines):
            address_line = lines[name_idx + 2]
            if "," in address_line:
                street, city, state, zipcode = _split_blinx_address(address_line)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode

        if name_idx + 3 < len(lines) and lines[name_idx + 3].rstrip(":").lower() not in label_keys:
            business["Country"] = lines[name_idx + 3]

    # ---- Owner Name / Phone / Website / URL / Business Email / About Us /
    #      Related Searches (flat label-then-value scan, stopping at
    #      either the next known label or a page-chrome terminator).
    #      NOTE: this site labels the website link "URL:" rather than
    #      "Website:" -- both are mapped to Website URL in
    #      _PLACE123_LABELS above. Without "url" recognized as a label,
    #      the value-collection loop for the preceding field (Business
    #      Email) wouldn't stop at it, and the email value would swallow
    #      the "URL:" line and the URL itself. ----
    i = 0
    n = len(lines)
    while i < n:
        norm = lines[i].rstrip(":").strip().lower()

        if norm in label_keys:
            field = _PLACE123_LABELS[norm]

            j = i + 1
            value_lines = []
            while j < n:
                next_norm = lines[j].rstrip(":").strip().lower()
                if next_norm in label_keys or next_norm in _PLACE123_TERMINATORS:
                    break
                value_lines.append(lines[j])
                j += 1

            value = clean(" ".join(value_lines))
            if field and value:
                business[field] = value

            i = j
        else:
            i += 1

    # ---- Website URL fallback (visible external anchor) ----
    if not business["Website URL"] or not business["Website URL"].startswith("http"):
        business["Website URL"] = ""
        for a in soup.find_all("a", href=True):
            href = a["href"]

            if not href.startswith("http"):
                continue
            if "place123.net" in href.lower():
                continue
            if "graph.facebook.com" in href.lower():
                continue
            if "google.com" in href.lower() or "googleapis.com" in href.lower():
                continue
            if any(domain in href.lower() for domain in SOCIAL_DOMAINS):
                continue

            business["Website URL"] = href
            break

    # ---- Description fallback (meta description -- truncated SEO
    #      snippet of the same "About Us" copy, so About Us wins if present) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    return business


# ==========================================================
# Site parser: freelistingusa.com
# ==========================================================

def parse_freelistingusa(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (og:title, minus the site-name suffix) ----
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        business["Business Name"] = clean(og_title["content"]).split("|")[0].strip()

    if not business["Business Name"]:
        h_tag = soup.find(re.compile(r"^h[1-6]$"))
        if h_tag:
            business["Business Name"] = clean(h_tag.get_text())

    # ---- Contact block, scoped via the tel: link ----
    tel = soup.select_one('a[href^="tel:"]')
    scope = soup

    if tel:
        business["Phone"] = tel["href"].replace("tel:", "").strip()
        # Walk up to the nearest list/container so Address/Website/Email
        # below are read from this same block, not the whole page.
        contact_container = tel.find_parent(["ul", "ol", "div"])
        if contact_container:
            scope = contact_container

    # Address (Google Maps link's visible text holds the full address)
    maps_link = scope.select_one('a[href*="maps.google.com"]')
    if maps_link:
        address_text = clean(maps_link.get_text())
        normalized = re.sub(r"\s*-\s*(\d)", r" \1", address_text)
        street, city, state, zipcode = _split_blinx_address(normalized)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # Email (Cloudflare-obfuscated, scoped to the contact block so the
    # footer's separate "Contact Us" email is never picked up instead)
    email = _find_cf_email(scope)
    if email:
        business["Business Email"] = email

    # Website (whichever external link is left once maps/tel/email are excluded)
    for a in scope.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "freelistingusa.com" in href.lower():
            continue
        if "maps.google.com" in href.lower() or "google.com/maps" in href.lower():
            continue
        if "cdn-cgi/l/email-protection" in href.lower():
            continue
        business["Website URL"] = href
        break

    # ---- Category ("Listed In :" link -- same URL as the breadcrumb) ----
    category_links = soup.select('a[href*="/listings/category/"]')
    categories = []
    for a in category_links:
        text = clean(a.get_text())
        if text and text not in categories:
            categories.append(text)
    if categories:
        business["Category"] = ", ".join(categories)

    # ---- Description ("Business Description" heading) ----
    description = _value_by_label(soup, "Business Description")
    if is_meaningful(description):
        business["Description"] = description

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords ("Services" + "Tags :" tag links, both /listings/tag/) ----
    tag_links = soup.select('a[href*="/listings/tag/"]')
    tags = []
    for a in tag_links:
        text = clean(a.get_text())
        if text and text not in tags:
            tags.append(text)
    if tags:
        business["Keywords"] = ", ".join(tags)

    # ---- Business Hours (dedicated hours-grid block, one <p> per day) ----
    hours_grid = soup.select_one("div.business-hours-listing div.hours-grid")
    if hours_grid:
        day_entries = [clean(p.get_text()) for p in hours_grid.find_all("p")]
        day_entries = [d for d in day_entries if d]
        if day_entries:
            business["Hours"] = "; ".join(day_entries)

    # ---- Logo / Photos (S3-hosted listing photo, full-size via its
    #      wrapping anchor rather than the smaller "_thumb" <img> src) ----
    photo_link = soup.select_one('a[href*="freelistingusa.s3"]')
    if photo_link and photo_link.get("href"):
        full_photo = photo_link["href"]
        business["Logo"] = full_photo
        business["Photos"] = [full_photo]
    else:
        photo_img = soup.select_one('img[src*="freelistingusa.s3"]')
        if photo_img and photo_img.get("src"):
            business["Logo"] = urljoin(url, photo_img["src"])
            business["Photos"] = [business["Logo"]]

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Social Media (dedicated #listing-follow block -- this sits
    #      as a SIBLING of the tel:-scoped contact list, not inside it,
    #      so it must be located independently of `scope` above. Using
    #      this fixed id also keeps FreeListingUSA's own Facebook link
    #      in the page footer from being picked up instead.) ----
    follow_block = soup.select_one("#listing-follow")
    if follow_block:
        for a in follow_block.find_all("a", href=True):
            href = a["href"]
            for domain, network in SOCIAL_DOMAINS.items():
                if domain in href.lower():
                    business["Social Media Links"][network] = href

    return business



# ==========================================================
# Site parser: askmap.net
# ==========================================================
#
# Confirmed via DevTools (Network tab, "Doc" filter, Response tab) and
# the real page source that the listing page comes back fully
# server-rendered on the very first request -- meta keywords/
# description/og:* tags are already present in the raw response body,
# and _looks_blocked()'s bot-check phrases don't match anything in it.
# So, like place123/freelistingusa/callupcontact/nearfinderus, this is
# fetched with plain requests, no Playwright needed.
#
# Template layout (confirmed against the real page source):
#   <h1>Business Name</h1>
#   "in <a>state (often blank)</a>, <a>country</a><br/>"   <- breadcrumb,
#       flat inline text/links in the same container as the <h1> and
#       share buttons, NOT its own wrapped block -- terminated by the
#       first <br/> that follows it.
#   "<b>Category</b>: <span>value</span>"                  <- also flat
#       inline text, label and value share one line.
#   "Address details" / "Coordinates" / "Phone & WWW" / "Business hours"
#   / "Info" / "Discussions" are each their OWN <h3>-headed <div> --
#   i.e. the <h3>'s PARENT div holds exactly that section's content
#   (confirmed: every one of these headings sits directly inside a
#   dedicated <div style="padding:10px;...">, sibling to no other
#   section's content). So section content is read from
#   `heading.parent`, not from the heading's next sibling -- Phone & WWW
#   in particular has its phone number and website link as flat
#   siblings of the <h3> (an <img>, then bare text, then <br/>, then
#   another <img>, then the <a>) with no single wrapping tag around
#   them, so grabbing just "the next sibling" (as an earlier version of
#   this parser did) missed the phone number and website entirely.
#   "Random Images" (page footer) -> a SITE-WIDE random-stock-photo
#       widget, NOT this listing's own photos (thumbnails are unrelated
#       stock images, e.g. a Singapore restaurant, an Italian bar) --
#       deliberately never read into Photos.

def _askmap_section_container(soup, header_text):
    """Returns the parent element of the <h3>header_text</h3> heading.
    Each labeled section on this template (Address details, Phone & WWW,
    Business hours, Info, ...) is wrapped in its own dedicated <div>, so
    the heading's parent holds exactly that section's content (the
    heading text itself included) and nothing from neighboring
    sections."""

    for h3 in soup.find_all("h3"):
        if clean(h3.get_text()).lower() == header_text.strip().lower():
            return h3.parent
    return None


def parse_askmap(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (visible <h1>, falls back to og:title) ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"]).split("|")[0].strip()

    # ---- Country (breadcrumb right after the name: "in <state>, <country>";
    #      state is frequently blank -- e.g. "in , United States" -- so the
    #      LAST link before the line's closing <br/> is always the country,
    #      never the state. Scanned from h1 forward, rather than from the
    #      top of the whole document, and stepping only through TAG
    #      siblings (find_next_sibling() with no filter never returns a
    #      bare NavigableString -- confirmed -- so this can't wander past
    #      the breadcrumb's own text into some unrelated later element) ----
    breadcrumb = h1.find_next(string=re.compile(r"^\s*in\b", re.I)) if h1 else None
    if breadcrumb:
        links = []
        sib = breadcrumb.find_next_sibling()
        while sib is not None and sib.name != "br":
            if sib.name == "a":
                links.append(sib)
            sib = sib.find_next_sibling()
        if links:
            # The state+country often live in a SINGLE anchor's text (e.g.
            # "         , United States" when no state is on file), so
            # split on the last comma rather than assuming one link = one
            # value.
            last_link_text = clean(links[-1].get_text())
            business["Country"] = last_link_text.split(",")[-1].strip()

    # ---- Category ("<b>Category</b>: <span>value</span>" -- label and
    #      value are flat inline siblings, not a wrapped block, so the
    #      value is simply the label's next TAG sibling (find_next_sibling
    #      skips the bare ": " text node in between automatically)) ----
    for b_tag in soup.find_all("b"):
        if clean(b_tag.get_text()).lower() == "category":
            value_tag = b_tag.find_next_sibling()
            if value_tag:
                business["Category"] = clean(value_tag.get_text())
            break

    # ---- Address details (own <div>; the street/city/state/zip live in
    #      a single <address> tag, plus a "Print route »" helper link
    #      that must be dropped before parsing so it doesn't get glued
    #      onto the zip code) ----
    address_container = _askmap_section_container(soup, "Address details")
    if address_container:
        address_tag = address_container.find("address")
        if address_tag:
            address_text = clean(address_tag.get_text(separator=" "))
            if address_text:
                street, city, state, zipcode = _split_blinx_address(address_text)
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode

    # ---- Phone & WWW (own <div>; phone number is bare text between two
    #      icon <img> tags, website is the first non-askmap, non-social
    #      external link -- both read via the section's full text/links,
    #      since neither is wrapped in its own container tag) ----
    contact_container = _askmap_section_container(soup, "Phone & WWW")
    if contact_container:
        tel = contact_container.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()
        else:
            phone_match = re.search(
                r"[\d][\d\-.\s()]{6,}\d", clean(contact_container.get_text())
            )
            if phone_match:
                business["Phone"] = clean(phone_match.group())

        for a in contact_container.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                continue
            if "askmap.net" in href.lower():
                continue
            if any(domain in href.lower() for domain in SOCIAL_DOMAINS):
                continue
            business["Website URL"] = href
            break

    # ---- Business hours (own <div>; blank for many listings -- read the
    #      full section text minus the heading itself) ----
    hours_container = _askmap_section_container(soup, "Business hours")
    if hours_container:
        hours_copy = BeautifulSoup(str(hours_container), "lxml")
        heading = hours_copy.find("h3")
        if heading:
            heading.decompose()
        # Join only the non-blank text pieces -- get_text(separator="; ")
        # would otherwise insert a stray "; " between two whitespace-only
        # text nodes (e.g. the blank line before/after a removed heading)
        # and clean() would collapse that into a lone ";" even though the
        # section has no real content.
        pieces = [clean(s) for s in hours_copy.find_all(string=True)]
        pieces = [p for p in pieces if p]
        hours_text = "; ".join(pieces)
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Description ("Info" section holds the full, untruncated copy;
    #      meta description is the same text but SEO-truncated, so it's
    #      only used as a fallback) ----
    info_container = _askmap_section_container(soup, "Info")
    if info_container:
        info_copy = BeautifulSoup(str(info_container), "lxml")
        heading = info_copy.find("h3")
        if heading:
            heading.decompose()
        desc_text = clean(info_copy.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords (meta keywords tag) ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            business["Keywords"] = clean(kw_raw)

    # ---- Logo (og:image -- matches the listing logo shown top-left) ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])

    # ---- Business Email (mailto:, if the listing has one on file) ----
    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").strip()

    return business


# ==========================================================
# Site parser: earthmom.org
# ==========================================================
#
# Confirmed fields from the rendered listing template (sampled on the
# FOCAL listing): visible <h1> name, "Professional Services"-style
# category line right under the name, a "Contact Information" table
# whose "Location" row holds the full one-line address in a
# schema.org PostalAddress span, meta og:image (logo), and a free-form
# "Write About You And Your Company" rich-text block the business
# owner fills in themselves.
#
# That last block is NOT structured data -- on the sampled listing the
# owner typed plain-text labels ("Phone:", "Website:", "About Us:")
# each followed by its own paragraph, but nothing on the template
# guarantees any listing does this consistently (some owners may only
# write free-form copy with no labels at all). _parse_earthmom_about_block
# below detects the label/value shape when present and folds every
# other paragraph into Description, so a listing that skips the labels
# still gets its write-up captured rather than losing it entirely.
#
# No Hours, GBP Link, or Social Media Links were present on the sample
# listing -- the page's own "Share This Page" buttons are a generic
# Facebook/LinkedIn/X share widget (pointed at earthmom.org's own URL),
# not the business's social profiles, so they're deliberately never
# read into Social Media Links. Keywords meta was polluted with
# site-taxonomy terms (e.g. "Earth Mom Partner", repeated category
# name) rather than business-specific tags, so it's left untracked
# here too -- see fields_config.SOURCE_FIELDS.

_EARTHMOM_LABEL_MAP = {
    "phone": "Phone",
    "website": "Website URL",
    "email": "Business Email",
}

_EARTHMOM_ABOUT_HEADINGS = {"about us", "about", "about company", "about the company"}


def _parse_earthmom_about_block(container):
    """Walks the <p> tags inside the free-form "about me" rich-text
    block. Whenever a paragraph is exactly a known label ("Phone:",
    "Website:", "Email:") the following paragraph is captured as that
    field's value and both are consumed; an "About Us:"-style heading
    paragraph is dropped on its own (it's just a section label, not a
    value pair); every other non-blank paragraph is appended to
    Description in document order. Returns a dict with whichever of
    Phone / Website URL / Business Email / Description were found."""

    result = {}
    description_lines = []

    paragraphs = [clean(p.get_text(separator=" ")) for p in container.find_all("p")]
    n = len(paragraphs)

    i = 0
    while i < n:
        text = paragraphs[i]
        if not text:
            i += 1
            continue

        label_key = text.rstrip(":").strip().lower()

        if label_key in _EARTHMOM_LABEL_MAP and i + 1 < n and paragraphs[i + 1]:
            result[_EARTHMOM_LABEL_MAP[label_key]] = paragraphs[i + 1]
            i += 2
            continue

        if label_key in _EARTHMOM_ABOUT_HEADINGS:
            i += 1
            continue

        description_lines.append(text)
        i += 1

    if description_lines:
        result["Description"] = "\n".join(description_lines)

    return result


def parse_earthmom(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (visible <h1>, falls back to og:title split on
    #      " on " since the template renders it as "<Name> on Earth Mom") ----
    h1 = soup.select_one(".header-member-name h1") or soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"]).split(" on ")[0].strip()

    # ---- Category (short line directly under the name) ----
    category_tag = soup.select_one(".profile-header-top-category")
    if category_tag:
        business["Category"] = clean(category_tag.get_text())

    # ---- Address ("Location" row of the Contact Information table --
    #      full one-line address lives in a single schema.org
    #      streetAddress span and needs splitting) ----
    address_tag = soup.select_one('[itemprop="streetAddress"]')
    if address_tag:
        address_text = clean(address_tag.get_text(separator=" "))
        if address_text:
            street, city, state, zipcode = _split_blinx_address(address_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    # ---- Phone / Website / Business Email / Description (free-form
    #      "Write About You And Your Company" block) ----
    about_container = soup.select_one(".overview-tab-about-me .textarea-about_me")
    if about_container:
        about_fields = _parse_earthmom_about_block(about_container)
        for field, value in about_fields.items():
            if is_meaningful(value):
                business[field] = value

    # ---- Description fallback (meta description, SEO-truncated) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Phone fallback (tel: link, if the free-form block didn't
    #      have one) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Business Email fallback (mailto:, if the free-form block
    #      didn't have one) ----
    if not business["Business Email"]:
        email = soup.select_one('a[href^="mailto:"]')
        if email:
            business["Business Email"] = email["href"].replace("mailto:", "").strip()

    # ---- Logo (og:image -- matches the social-share preview image;
    #      falls back to the profile photo shown top-left of the
    #      listing if og:image is missing) ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])
    else:
        profile_img = soup.select_one(".profile-image img")
        if profile_img and profile_img.get("src"):
            business["Logo"] = urljoin(url, profile_img["src"])

    return business


# ==========================================================
# Site parser: gravitysplash.com
# ==========================================================
#
# Confirmed fields from the rendered listing template (sampled on the
# WRIGHTWAY EMERGENCY SERVICES listing): a ListingPro WordPress theme.
# <h1> name, a breadcrumb whose middle link is the listing's category,
# a one-line "tagline" paragraph directly under the name (comma-
# separated service terms -- read as Keywords), the full write-up in
# a dedicated "post-detail-content" block, and a sidebar info list
# (.lp-details-address / .lp-listing-phone / .lp-user-web) holding the
# address/phone/website as the last <span> inside each <li> (the first
# span in each is just the icon).
#
# The page also embeds a LocalBusiness JSON-LD block, but on the
# sample its address fields were mostly blank/wrong (addressLocality
# empty, addressRegion "ON" -- clearly a template default, not the
# real Florida listing), so the sidebar list is used as the primary
# source and JSON-LD is only a fallback for name/phone.
#
# No Hours (openingHoursSpecification was an empty array on the
# sample), Logo, GBP Link, or Business Email were reliably present --
# og:image is GravitySplash's own site logo, not a business photo, and
# the header/footer share & social icons point at GravitySplash's own
# accounts/share intents, not the business's.
#
# Social Media Links IS real and extractable on this template, just not
# populated on the FOCAL (WrightWay) sample: confirmed on another live
# listing (wes-electrical) that when the business has added social
# profiles, they render as a second <ul> immediately following the
# address/phone/website sidebar list, as plain <a href> icons (e.g.
# Facebook, Instagram). That block is distinct from both the page-top
# "Share" buttons (which link to share-intent URLs like
# facebook.com/sharer/sharer.php -- not the business's own profile) and
# the site-wide footer social icons (GravitySplash's own accounts) --
# see parse_gravitysplash below for how it's scoped to avoid both.

def _gravitysplash_sidebar_value(soup, li_class):
    """The sidebar info list wraps each value in its own <li>, with the
    icon in the first <span> and the actual text in the last <span> --
    reads that last span rather than the full <li> text so the (empty)
    icon <img>'s alt text can never leak into the value."""

    li = soup.select_one(f"li.{li_class}")
    if not li:
        return None
    spans = li.find_all("span")
    if not spans:
        return None
    return clean(spans[-1].get_text())


def parse_gravitysplash(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    h1 = soup.select_one(".post-meta-left-box h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Category (middle breadcrumb link -- first is always "Home",
    #      last is the current listing name with no link at all) ----
    breadcrumb_links = soup.select(".breadcrumbs li a")
    if len(breadcrumb_links) >= 2:
        business["Category"] = clean(breadcrumb_links[1].get_text())

    # ---- Keywords (tagline paragraph directly under the name --
    #      comma-separated service terms) ----
    tagline = soup.select_one(".post-meta-left-box p")
    if tagline:
        tagline_text = clean(tagline.get_text())
        if is_meaningful(tagline_text):
            business["Keywords"] = tagline_text

    # ---- Description (full write-up) ----
    desc_container = soup.select_one(".post-detail-content")
    if desc_container:
        desc_text = clean(desc_container.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Address ----
    address_text = _gravitysplash_sidebar_value(soup, "lp-details-address")
    if address_text:
        street, city, state, zipcode = _split_blinx_address(address_text)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone (tel: href is the authoritative value; sidebar span
    #      text is used only as a fallback) ----
    phone_link = soup.select_one("li.lp-listing-phone a[href^='tel:']")
    if phone_link:
        business["Phone"] = phone_link["href"].replace("tel:", "").strip()
    else:
        phone_text = _gravitysplash_sidebar_value(soup, "lp-listing-phone")
        if phone_text:
            business["Phone"] = phone_text

    # ---- Website URL (href attribute, not the span text -- the two
    #      are usually identical on this template, but the href is the
    #      normalized/canonical form) ----
    website_link = soup.select_one("li.lp-user-web a[href]")
    if website_link:
        business["Website URL"] = website_link["href"]

    # ---- Social Media Links (business's own icons, when the listing
    #      owner has added them -- rendered as a second <ul> immediately
    #      following the address/phone/website sidebar list. Scoped this
    #      way, rather than a page-wide anchor scan, so it can't pick up
    #      the page-top "Share" buttons (share-intent URLs, not the
    #      business's profile) or GravitySplash's own footer social
    #      icons -- neither of which live in this sidebar list.) ----
    contact_list = None
    for li_class in ("lp-user-web", "lp-listing-phone", "lp-details-address"):
        anchor_li = soup.select_one(f"li.{li_class}")
        if anchor_li:
            contact_list = anchor_li.find_parent("ul")
            if contact_list:
                break

    if contact_list:
        social_list = contact_list.find_next_sibling("ul")
        if social_list:
            for a in social_list.find_all("a", href=True):
                href = a["href"]
                for domain, network in SOCIAL_DOMAINS.items():
                    if domain in href.lower():
                        business["Social Media Links"][network] = href

    # ---- Fallbacks from the embedded LocalBusiness JSON-LD, only for
    #      whichever fields the sidebar didn't already fill in ----
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("@type") != "LocalBusiness":
            continue
        if not business["Business Name"] and data.get("name"):
            business["Business Name"] = data["name"]
        if not business["Phone"] and data.get("telephone"):
            business["Phone"] = data["telephone"]
        break

    return business


# ==========================================================
# Site parser: webforcompany.com
# ==========================================================
#
# This site serves each business on a per-business subdomain-style path
# (e.g. /haqq-legal-ai/, /focal/), and the SAME business's content is
# duplicated across at least two page templates with different markup:
#
#   1) The homepage (e.g. /haqq-legal-ai/, url path "/index.php"
#      implied) -- confirmed: a ".about" section holding ONE <p> whose
#      entire content is a flat, <br>-separated "Label:<br/>Value<br/>"
#      block (same shape as place123.net's Owner Name/Address/Phone/...
#      block).
#
#   2) The "About Us" subpage (".../about.php", confirmed on
#      /focal/about.php) -- a ".aboutus" section holding a SEPARATE
#      <p> for each label and each value (Word/Calibri-styled spans,
#      no <br> at all). The label/value pairs still appear in the same
#      order as template 1, so once both are reduced to a flat list of
#      text lines, the same label-scan logic below works for either.
#
# Both templates are handled by trying ".about p" first, then falling
# back to ".aboutus .col-md-12" (the div that holds exactly this
# page's label/value <p> tags and nothing else -- unlike the homepage
# template, there's no trailing "Read More" link or heading text mixed
# into this scope to worry about).
#
# Quirks common to both templates:
#   - "Business Email" sometimes has a trailing colon (about.php) and
#     sometimes doesn't (homepage) -- label matching strips ":" before
#     comparing either way.
#   - The visible Business Email text is always a Cloudflare-obfuscated
#     placeholder ("[email protected]"), not the real address, so the
#     real value has to come from _find_cf_email's decoded
#     data-cfemail attribute instead of the label's text.
#
# Logo: confirmed via a side-by-side comparison of two businesses that
# the header's ".navbar-brand img" (when present) is this business's
# OWN uploaded logo (e.g. Focal's banner image) -- but when a business
# hasn't uploaded one, the homepage falls back to rendering a shared
# ".about_img" placeholder instead (confirmed: two unrelated listings,
# haqq-legal-ai and a dental practice, both rendered the IDENTICAL
# ".about_img" image). So Logo is read from ".navbar-brand img" only;
# ".about_img" is deliberately never used, since it isn't this
# business's own logo.

_WEBFORCOMPANY_LABELS = {
    "business name": "Business Name",
    "owner name": None,
    "phone": "Phone",
    "website": "Website URL",
    "business email": None,  # real value comes from _find_cf_email, not this text
    "about us": "Description",
    "related searches": "Keywords",
}


def parse_webforcompany(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Logo (real per-business header image, when uploaded) ----
    logo_img = soup.select_one(".navbar-brand img")
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])

    # ---- Locate the label/value block (homepage shape, then about.php shape) ----
    scope = soup.select_one(".about p")
    if not scope:
        scope = soup.select_one(".aboutus .col-md-12")
    if not scope:
        return business

    # ---- Website URL (real href, not the label's visible text) ----
    for a in scope.find_all("a", href=True):
        href = a["href"]
        if "cdn-cgi/l/email-protection" in href.lower():
            continue
        if href.startswith("http"):
            business["Website URL"] = href
            break

    # ---- Business Email (Cloudflare-obfuscated placeholder text) ----
    email = _find_cf_email(scope)
    if email:
        business["Business Email"] = email

    # ---- Flat label-then-value scan for everything else ----
    lines = [clean(line) for line in scope.get_text(separator="\n").split("\n")]
    lines = [l for l in lines if l]
    label_keys = set(_WEBFORCOMPANY_LABELS.keys())

    i, n = 0, len(lines)
    while i < n:
        norm = lines[i].rstrip(":").strip().lower()

        if norm == "address":
            if i + 1 < n:
                street, city, state, zipcode = _split_blinx_address(lines[i + 1])
                business["Street"] = street
                business["City"] = city
                business["State"] = state
                business["Zipcode"] = zipcode
            i += 2
            continue

        if norm in label_keys:
            field = _WEBFORCOMPANY_LABELS[norm]

            j = i + 1
            value_lines = []
            while j < n:
                next_norm = lines[j].rstrip(":").strip().lower()
                if next_norm in label_keys or next_norm == "address":
                    break
                value_lines.append(lines[j])
                j += 1

            value = clean(" ".join(value_lines))
            if field and value:
                business[field] = value

            i = j
        else:
            i += 1

    return business


# ==========================================================
# Site parser: provenexpert.com
# ==========================================================
#
# A review-platform profile page (not a directory listing like the
# other sites above), but it happens to expose most of the fields we
# need cleanly:
#
#   - A schema.org LocalBusiness JSON-LD block gives Business Name,
#     Logo (its "image.url"), Street, City, Zipcode, Country, and
#     Phone directly -- no HTML scraping needed for those.
#   - "#personalPublic" (the "Contact information" box) has the same
#     address again as visible text inside an <address> tag, PLUS the
#     one field JSON-LD is missing: State (rendered inline as
#     "Delaware (DE)" -- the "(DE)" abbreviation is stripped here).
#     This box also has the real tel:/mailto: links -- used instead of
#     re-deriving Phone from JSON-LD, and as the only source for
#     Business Email (JSON-LD has no email field at all here).
#   - "#welcomeTextPublic" holds the About/description text. Part of
#     it (".textRest") is CSS-hidden behind a "View full description"
#     toggle, but it's still present in the raw server-rendered HTML,
#     so no click-through/JS execution is needed to read it. The "..."
#     ellipsis (".textEtc") and the "View full description" link text
#     itself (".collapseAboutme") are stripped out before reading, so
#     neither pollutes the Description value.
#   - "#offerTagsPublic .peTagPill" ("What's on offer" tag pills, also
#     CSS-hidden by default but present in the HTML) is used for
#     Keywords, the same way Related Searches/tag-pill blocks are used
#     on the other directory sites above.
#   - "h2.profileJob" (the one-line tagline directly under the
#     business name, e.g. "ChatGPT Ads") is used for Category -- it's
#     the closest thing this template has to a category/industry tag.
#   - "#profilesPublic" ("Websites" box) gives Website URL.
#
# NOT populated: the "Directions" link under the address is a Google
# Maps *search* URL (maps.google.com/maps?q=...), not an actual Google
# Business Profile listing link, so it is deliberately NOT used for
# GBP Link (left blank, same as every other parser above). Hours are
# loaded asynchronously via a separate JS call
# (Profile.setProfileOpeningHours(...)) after page load and are not
# present anywhere in the static HTML, so Hours is left blank too. No
# social-network links (Facebook/Instagram/etc for the business itself,
# as opposed to ProvenExpert's own share buttons) were observed on the
# sample profile.

def parse_provenexpert(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- JSON-LD (Name, Logo, Street/City/Zipcode/Country, Phone) ----
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]
        for obj in objects:
            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
                continue

            if obj.get("name"):
                business["Business Name"] = obj["name"]

            image = obj.get("image")
            if isinstance(image, dict) and image.get("url"):
                business["Logo"] = image["url"]
            elif isinstance(image, str) and image:
                business["Logo"] = image

            addr = obj.get("address", {})
            if addr.get("streetAddress"):
                business["Street"] = addr["streetAddress"]
            if addr.get("addressLocality"):
                business["City"] = addr["addressLocality"]
            if addr.get("postalCode"):
                business["Zipcode"] = addr["postalCode"]
            if addr.get("addressCountry"):
                business["Country"] = addr["addressCountry"]

            if obj.get("telephone"):
                business["Phone"] = obj["telephone"]

    # ---- Business Name fallback (visible <h1>) ----
    if not business["Business Name"]:
        h1 = soup.select_one("h1.profileName")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Category (tagline directly under the business name) ----
    job = soup.select_one("h2.profileJob")
    if job:
        business["Category"] = clean(job.get_text())

    # ---- Keywords ("What's on offer" tag pills -- read BEFORE the
    #      Description step below decomposes this same block) ----
    tags = [clean(t.get_text()) for t in soup.select("#offerTagsPublic .peTagPill")]
    tags = [t for t in tags if t]
    if tags:
        business["Keywords"] = ", ".join(tags)

    # ---- Description (About text, incl. the CSS-hidden continuation) ----
    welcome = soup.select_one("#welcomeTextPublic")
    if welcome:
        for junk in welcome.select(".textEtc, .collapseAboutme, #offerTags"):
            junk.decompose()
        text = clean(welcome.get_text(separator=" "))
        if is_meaningful(text):
            business["Description"] = text

    # ---- Contact box: State (JSON-LD doesn't have it), Phone, Email ----
    contact = soup.select_one("#personalPublic")
    if contact:
        address_tag = contact.select_one("address")
        if address_tag:
            lines = [clean(l) for l in address_tag.get_text(separator="\n").split("\n")]
            lines = [l for l in lines if l]
            # Confirmed shape: ["<street>", "<city>,", "<state> (<abbr>)",
            # "<zip>", "<country>"] -- read State positionally rather
            # than trying to label-match it, since it has no label of
            # its own in this markup.
            if len(lines) >= 3 and not business["State"]:
                business["State"] = re.sub(r"\s*\([A-Za-z]{2,3}\)\s*$", "", lines[2]).strip()
            if len(lines) >= 4 and not business["Zipcode"]:
                business["Zipcode"] = lines[3]
            if len(lines) >= 5 and not business["Country"]:
                business["Country"] = lines[4]

        tel = contact.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

        # mailto hrefs here carry a "?Subject=..." query string -- strip it.
        email = contact.select_one('a[href^="mailto:"]')
        if email:
            business["Business Email"] = email["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Website URL ("Websites" box) ----
    website_link = soup.select_one("#profilesPublic a[href^='http']")
    if website_link:
        business["Website URL"] = website_link["href"]

    return business


# ==========================================================
# Site parser: zipleaf.us
# ==========================================================
# Static HTML, fetched with requests. Address/name/phone/logo/description
# come from the JSON-LD LocalBusiness block. The "Website URL" is NOT the
# href of the site-link anchor -- that href points at an internal
# "/GoToWebsite/<slug>" redirect route, not the real external site -- the
# actual URL is only present as the anchor's visible text, so that's what
# we read instead. The sidebar's "Share This Listing" icons (Facebook/
# Twitter/LinkedIn/Pinterest) are share-this-page widgets, not the
# business's own social profiles, so that block is explicitly excluded
# from the Social Media Links scan.

ZIPLEAF_SHARE_LINK_SIGNALS = [
    "sharer.php", "intent/tweet", "share-offsite", "pin/create/button",
]


def parse_zipleaf(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- JSON-LD (primary source: name, address, phone, logo, description) ----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:

            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
                continue

            business["Business Name"] = obj.get("name", business["Business Name"])

            if obj.get("description"):
                business["Description"] = clean(obj["description"])

            if obj.get("image") and not business["Logo"]:
                business["Logo"] = urljoin(url, obj["image"])

            if obj.get("telephone") and not business["Phone"]:
                business["Phone"] = obj["telephone"]

            addr = obj.get("address", {})
            if not business["Street"]:
                business["Street"] = addr.get("streetAddress", "")
            if not business["City"]:
                business["City"] = addr.get("addressLocality", "")
            if not business["State"]:
                business["State"] = addr.get("addressRegion", "")
            if not business["Zipcode"]:
                business["Zipcode"] = addr.get("postalCode", "")
            if not business["Country"]:
                business["Country"] = addr.get("addressCountry", "")

    # ---- Business Name fallback (visible listing title) ----
    if not business["Business Name"]:
        title = soup.select_one("h3.card-title span")
        if title:
            business["Business Name"] = clean(title.get_text())

    main_card = soup.select_one("div.listing-contact-info") or soup

    # ---- Website URL (visible text of the site link, not its redirect href) ----
    website_link = main_card.select_one('a[href^="/GoToWebsite/"], a[href*="/GoToWebsite/"]')
    if website_link:
        site_text = clean(website_link.get_text())
        if site_text:
            business["Website URL"] = site_text
        elif website_link.get("href"):
            business["Website URL"] = urljoin(url, website_link["href"])

    # ---- Phone fallback (tel: link) ----
    if not business["Phone"]:
        tel = main_card.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Business Email (mailto: link, if present) ----
    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Description fallback (meta description) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords ----
    # zipleaf.us listings don't carry a <meta name="keywords"> tag in
    # practice -- the closest equivalent is the "Products/Services" tag
    # list (a.product-link), which functions like a keyword cloud for the
    # listing (e.g. "ChatGPT Ads", "ChatGPT Ads Agency"). Meta keywords
    # checked first in case some listing template variant does carry it;
    # product/service tags used as the real-world fallback.
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            business["Keywords"] = clean(kw_raw)

    if not business["Keywords"]:
        product_tags = [clean(a.get_text()) for a in soup.select("a.product-link")]
        product_tags = [t for t in product_tags if t]
        if product_tags:
            business["Keywords"] = ", ".join(product_tags)

    # ---- Logo fallback (listing photo / og:image) ----
    if not business["Logo"]:
        logo_img = soup.select_one("#business-logo img[src]")
        if logo_img:
            business["Logo"] = urljoin(url, logo_img["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Category (breadcrumb, minus Home / location / listing-name crumbs) ----
    # On zipleaf.us the breadcrumb trail is location-only (Home > State >
    # City > Listing Name), so nothing meaningful survives this filter on
    # the sample seen so far -- left in place in case a category-bearing
    # crumb appears on other listings.
    crumbs = [clean(li.get_text()) for li in soup.select("ol.breadcrumb li.breadcrumb-item")]
    skip = {"home", (business["Business Name"] or "").lower()}
    category_crumbs = [c for c in crumbs if c and c.lower() not in skip]
    business["Category"] = ""

    # ---- GBP Link (a Google Maps / Business Profile link, if present) ----
    gbp_link = soup.select_one('a[href*="google.com/maps"], a[href*="g.page"], a[href*="goo.gl/maps"]')
    if gbp_link and gbp_link.get("href"):
        business["GBP Link"] = gbp_link["href"]

    # ---- Hours (not present in the standard zipleaf.us template) ----
    # No hours block observed on this template; left blank if absent.

    # ---- Social Media Links (business's own profiles only --
    # excludes the "Share This Listing" widget, which links to
    # facebook.com/sharer.php etc. for sharing the *listing page*,
    # not the business's own social accounts) ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(sig in href.lower() for sig in ZIPLEAF_SHARE_LINK_SIGNALS):
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business






# ==========================================================
# Site parser: cataloxy.us
# ==========================================================
# Static HTML, fetched with requests. This directory publishes real
# per-listing metadata that most of the other sources here don't:
#   - an actual <meta name="keywords"> tag
#   - a genuine business Category in the breadcrumb (the crumb right
#     before the business name, e.g. "Chatgpt ads agency" -- unlike
#     zipleaf.us where the breadcrumb is location-only)
#   - address as schema.org PostalAddress microdata (itemprop spans),
#     which is used as the primary source since it carries the zip
#     code that this listing's JSON-LD block omits
# Regional subdomains vary (de-newark.cataloxy.us, md-elkton.cataloxy.us,
# etc.) but all contain "cataloxy.us", which is what SITE_PARSERS matches
# on. The "Website" link's href is a javascript: no-op (onclick="go2me
# (this)" handles the actual redirect client-side) -- the real external
# URL is only present in that anchor's title="..." attribute.
# The only "share" affordance here is a single native Web Share API
# button (span.share-native / a.js-native-share) for sharing the listing
# page itself, not a business social-profile block, so Social Media
# Links stays empty unless a listing actually links out to a real
# facebook.com/instagram.com/etc. profile page.

def parse_cataloxy(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- JSON-LD (name/phone/address fallback -- see microdata below
    # for the primary address source, which includes the zip code that
    # this site's JSON-LD block leaves out) ----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            data = json.loads(script.string)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:

            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
                continue

            business["Business Name"] = obj.get("name", business["Business Name"])

            if obj.get("telephone") and not business["Phone"]:
                business["Phone"] = obj["telephone"]

            addr = obj.get("address", {})
            if not business["Street"]:
                business["Street"] = addr.get("streetAddress", "")
            if not business["City"]:
                business["City"] = addr.get("addressLocality", "")
            if not business["State"]:
                business["State"] = addr.get("addressRegion", "")
            if not business["Country"]:
                business["Country"] = addr.get("addressCountry", "")

    # ---- Business Name fallback (visible <h1 class="firms">) ----
    if not business["Business Name"]:
        h1 = soup.select_one("h1.firms")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Address microdata (primary source -- has the zip code) ----
    addr_block = soup.select_one('span[itemprop="address"]')
    if addr_block:
        street = addr_block.select_one('[itemprop="streetAddress"]')
        if street:
            business["Street"] = clean(street.get_text())
        zipcode = addr_block.select_one('[itemprop="postalCode"]')
        if zipcode:
            business["Zipcode"] = clean(zipcode.get_text())
        city = addr_block.select_one('[itemprop="addressLocality"]')
        if city:
            business["City"] = clean(city.get_text())
        state = addr_block.select_one('[itemprop="addressRegion"]')
        if state:
            business["State"] = clean(state.get_text())
        country = addr_block.select_one('[itemprop="addressCountry"]')
        if country:
            business["Country"] = country.get("content") or clean(country.get_text())

    # ---- Phone fallback (tel: link) ----
    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Website URL (real URL lives in the link's title=, not its
    # javascript: href) ----
    site_link = soup.select_one("a.firmDomain")
    if site_link:
        if site_link.get("title"):
            business["Website URL"] = site_link["title"]
        else:
            business["Website URL"] = clean(site_link.get_text())

    # ---- Business Email (mailto: link, if present -- "Write to the
    # company" here is a JS contact-form modal, not a real email) ----
    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Description (itemprop="description" paragraph) ----
    desc_el = soup.select_one('[itemprop="description"]')
    if desc_el:
        desc = clean_multiline(desc_el.decode_contents())
        if is_meaningful(desc):
            business["Description"] = desc
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords (real <meta name="keywords">, with the on-page
    # "Keywords:" tag list as a fallback for listings missing the meta) ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            business["Keywords"] = clean(kw_raw)
    if not business["Keywords"]:
        kw_links = [clean(a.get_text()) for a in soup.select('a[href*="/firms/kw/"]')]
        kw_links = [k for k in kw_links if k]
        if kw_links:
            business["Keywords"] = ", ".join(kw_links)

    # ---- Category (breadcrumb crumb immediately before the business
    # name -- this site's breadcrumb includes a real category tag,
    # unlike location-only breadcrumbs on some other directories) ----
    crumb_names = [
        clean(span.get_text())
        for span in soup.select('#top_navigator span[itemprop="name"]')
    ]
    if crumb_names:
        business["Category"] = crumb_names[-1]

    # ---- Logo ----
    logo_el = soup.select_one('span[itemprop="logo"]')
    if logo_el and is_meaningful(logo_el.get_text()):
        business["Logo"] = urljoin(url, clean(logo_el.get_text()))
    if not business["Logo"]:
        logo_img = soup.select_one(".firm-top-panel__logo img[src]")
        if logo_img:
            business["Logo"] = urljoin(url, logo_img["src"])
    if not business["Logo"]:
        # Broader fallback in case the logo image isn't nested under
        # .firm-top-panel__logo on some listing template variant.
        logo_img = soup.select_one("img.logo[src]")
        if logo_img:
            business["Logo"] = urljoin(url, logo_img["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- GBP Link (an actual Google Maps/Business Profile link, if
    # present -- this template's own map widget is Leaflet/OSM-based,
    # not Google, so this will usually stay empty) ----
    gbp_link = soup.select_one('a[href*="google.com/maps"], a[href*="g.page"], a[href*="goo.gl/maps"]')
    if gbp_link and gbp_link.get("href"):
        business["GBP Link"] = gbp_link["href"]

    # ---- Hours (not present on this listing's template; left blank
    # if absent -- worktime widgets are driven by a JS data-state blob
    # elsewhere on the site, not consistently present per listing) ----

    # ---- Social Media Links (business's own profiles only -- the
    # page's only share affordance is a single native Web Share API
    # button for sharing the *listing page*, not a social-profile
    # block, so nothing is scanned in from it). Hostname-boundary
    # matching (not raw substring) is used here specifically because
    # this site's own cross-region links (e.g. https://www.cataloxy-mx.com/,
    # its Mexico edition) contain the literal substring "x.com" and
    # would otherwise be misclassified as a Twitter/X link. ----
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "js-native-share" in (a.get("class") or []):
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# fyple.com
# ==========================================================
#
# Static, server-rendered HTML (no bot-wall observed) built on
# schema.org/LocalBusiness microdata for the core identity/address
# fields, plus a handful of plain label/value rows (Phone number,
# Categories, Company description, OPEN HOURS, Photos) that aren't
# part of the microdata block and have to be read from their own
# markup instead. Confirmed fields on a live listing
# (tahir-health-service-lcc-u8kn9pc): Name, full address, Phone,
# Hours, Category, Description, Photos. No Website URL, Business
# Email, Keywords, or Social Media Links field exists anywhere in
# this template (the only "social" markup is generic page-share
# buttons wired to "#", not the business's own profiles), and the
# "Location on map" iframe is a bare lat/lng embed API URL, not a
# proper Google Business Profile link -- so none of those four are
# in SOURCE_FIELDS for this source and this parser doesn't attempt
# to populate them.

def _fyple_label_value(soup, label_text):
    """fyple's Contact rows are a flat two-column layout:
        <div class="row">
            <div class="col-xs-12 col-sm-5"><strong>LABEL:</strong></div>
            <div class="col-xs-12 col-sm-7">VALUE</div>
        </div>
    The value div is a sibling of the LABEL div (which itself wraps
    the <strong>), not of the <strong> tag directly -- so this steps
    up to the label's parent before looking for the next sibling.
    """
    for strong in soup.find_all("strong"):
        if clean(strong.get_text()).rstrip(":").lower() == label_text.lower():
            label_cell = strong.parent
            value_cell = label_cell.find_next_sibling("div") if label_cell else None
            if value_cell:
                return clean(value_cell.get_text(separator=" "))
    return ""


def _fyple_section_heading(soup, heading_text):
    """Returns the <h3 class="comp_section_title"> tag whose text
    matches heading_text exactly (case-insensitive), or None."""
    for h3 in soup.find_all("h3"):
        if clean(h3.get_text()).lower() == heading_text.lower():
            return h3
    return None


def parse_fyple(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (schema.org itemprop, falls back to og:title
    #      minus the "in <City>, <State>" suffix the <title>/og:title
    #      tags both append) ----
    name_tag = soup.select_one('[itemtype*="LocalBusiness"] h1[itemprop="name"]')
    if name_tag:
        business["Business Name"] = clean(name_tag.get_text())

    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(og_title["content"]).split(" in ")[0].strip()

    # ---- Address (schema.org/PostalAddress microdata block -- each
    #      component is its own itemprop span, so read them directly
    #      rather than trying to split a flattened text string) ----
    addr = soup.select_one('[itemprop="address"][itemtype*="PostalAddress"]')
    if addr:
        street = addr.find("span", itemprop="streetAddress")
        city = addr.find("span", itemprop="addressLocality")
        zipcode = addr.find("span", itemprop="postalCode")
        state = addr.find("span", itemprop="addressRegion")
        country = addr.find("span", itemprop="addressCountry")

        if street:
            business["Street"] = clean(street.get_text())
        if city:
            business["City"] = clean(city.get_text())
        if zipcode:
            business["Zipcode"] = clean(zipcode.get_text())
        if state:
            business["State"] = clean(state.get_text())
        if country:
            business["Country"] = clean(country.get_text())

    # ---- Phone number ("Phone number:" label/value row) ----
    phone = _fyple_label_value(soup, "Phone number")
    if phone:
        business["Phone"] = phone

    # ---- Hours (#OpenHoursCollapse holds day/value pairs as flat
    #      sibling <div> pairs -- col-xs-4 day name, col-xs-8 value
    #      (either "H:MM - H:MM" text or a "Closed" label span) ----
    hours_container = soup.find("div", id="OpenHoursCollapse")
    if hours_container:
        cells = [clean(c.get_text()) for c in hours_container.find_all("div", recursive=False)]
        cells = [c for c in cells if c]
        pairs = [f"{cells[i]}: {cells[i + 1]}" for i in range(0, len(cells) - 1, 2)]
        hours_text = "; ".join(pairs)
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category ("Categories" section -- breadcrumb-style anchors,
    #      parent category first, subcategory second) ----
    cat_heading = _fyple_section_heading(soup, "Categories")
    if cat_heading:
        cat_container = cat_heading.find_next("div", class_="comp_wrap")
        if cat_container:
            cat_links = [clean(a.get_text()) for a in cat_container.find_all("a")]
            cat_links = [c for c in cat_links if c]
            if cat_links:
                business["Category"] = " > ".join(cat_links)

    # ---- Description ("Company description" section -- plain text
    #      sitting directly in the heading's parent <div>, not its own
    #      wrapped element, so the heading itself has to be stripped
    #      out first rather than just reading a sibling's text) ----
    desc_heading = _fyple_section_heading(soup, "Company description")
    if desc_heading and desc_heading.parent:
        desc_copy = BeautifulSoup(str(desc_heading.parent), "lxml")
        heading_copy = desc_copy.find("h3")
        if heading_copy:
            heading_copy.decompose()
        desc_text = clean(desc_copy.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Photos + Logo (data-lightbox="images" anchors hold the
    #      full-size "-big.jpg" URL; the thumbnail <img> inside is the
    #      smaller "-med.jpg" version, so the anchor href is preferred.
    #      fyple doesn't expose a separate Logo field/image anywhere in
    #      this template, but one of the uploaded photos is frequently
    #      the actual logo file (confirmed: ".../logo.jpg....-big.jpg"
    #      on this listing) -- filenames containing "logo" are pulled
    #      out into the Logo field instead of left mixed into Photos.) ----
    photos = []
    logo_found = ""
    for a in soup.select('a[data-lightbox="images"][href]'):
        href = urljoin(url, a["href"])
        if not logo_found and re.search(r"logo", href, re.I):
            logo_found = href
        else:
            photos.append(href)

    if logo_found:
        business["Logo"] = logo_found
    business["Photos"] = photos

    # ---- Website URL, Business Email, Keywords, Social Media Links,
    #      GBP Link: intentionally left blank -- see module docstring
    #      above. This template has no field for any of them. ----

    return business


# ==========================================================
# merchantcircle.com
# ==========================================================
#
# Static, server-rendered HTML (no bot-wall observed). The reliable
# fields live in two places: `business:contact_data:*` OG meta tags
# (Street/City/Zipcode/Country/Phone/Website) and schema.org itemprop
# spans in the body (name/address/phone -- addressRegion has no meta
# equivalent at all, so State always comes from the body). Confirmed
# on a live listing (church-street-family-cosmetic-dentistry-mount-
# laurel-nj): this specific page variant's own <title>/meta-description
# are a "Map and Directions to X in City, State Zip" flavor of the
# listing, NOT the plain business page, even though its og:url matches
# the canonical listing URL -- so Name/Description are read from the
# og: tags rather than <title>/meta-description, which are correct for
# the directions variant but wrong (or truncated) for the business
# record itself.

def parse_merchantcircle(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name (og:title -- NOT the raw <title> tag, which on
    #      this page variant is "Map and Directions to X in City, State
    #      Zip", not the bare business name) ----
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        business["Business Name"] = clean(og_title["content"])

    if not business["Business Name"]:
        h1 = soup.select_one("h1.business-info-title")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Address -- Street/Zipcode/Country come from the
    #      business:contact_data:* OG meta tags (most reliable/cleanest
    #      source); State has no meta equivalent at all, so it's read
    #      from the schema.org addressRegion span in the body instead;
    #      City prefers the meta tag too, since the body's
    #      addressLocality span's text literally has a trailing comma
    #      baked in (e.g. "Mount Laurel,") ----
    meta_street = soup.find("meta", property="business:contact_data:street_address")
    if meta_street and meta_street.get("content"):
        business["Street"] = clean(meta_street["content"])

    meta_city = soup.find("meta", property="business:contact_data:locality")
    if meta_city and meta_city.get("content"):
        business["City"] = clean(meta_city["content"])
    if not business["City"]:
        city_tag = soup.select_one('span[itemprop="addressLocality"]')
        if city_tag:
            business["City"] = clean(city_tag.get_text()).rstrip(",")

    state_tag = soup.select_one('span[itemprop="addressRegion"]')
    if state_tag:
        business["State"] = clean(state_tag.get_text())

    meta_zip = soup.find("meta", property="business:contact_data:postal_code")
    if meta_zip and meta_zip.get("content"):
        business["Zipcode"] = clean(meta_zip["content"])
    if not business["Zipcode"]:
        zip_tag = soup.select_one('span[itemprop="postalCode"]')
        if zip_tag:
            business["Zipcode"] = clean(zip_tag.get_text())

    meta_country = soup.find("meta", property="business:contact_data:country_name")
    if meta_country and meta_country.get("content"):
        business["Country"] = clean(meta_country["content"])

    # ---- Phone ----
    meta_phone = soup.find("meta", property="business:contact_data:phone_number")
    if meta_phone and meta_phone.get("content"):
        business["Phone"] = clean(meta_phone["content"])
    if not business["Phone"]:
        phone_tag = soup.select_one('span[itemprop="telephone"]')
        if phone_tag:
            business["Phone"] = clean(phone_tag.get_text())

    # ---- Website URL ----
    meta_website = soup.find("meta", property="business:contact_data:website")
    if meta_website and meta_website.get("content"):
        business["Website URL"] = clean(meta_website["content"])
    if not business["Website URL"]:
        site_link = soup.select_one(".bi-list-item a.bi-list-item-text[href]")
        if site_link:
            business["Website URL"] = site_link["href"]

    # ---- Keywords (this template's <meta name="keywords"> is built for
    #      the "Map and Directions" SEO variant of the page and always
    #      opens with the fixed, non-business tokens "map, location,
    #      directions," ahead of the real name/city/state/zip/category
    #      terms -- stripped off so Keywords only holds the
    #      business-relevant remainder) ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        tokens = [clean(t) for t in meta_kw.get("content", "").split(",")]
        tokens = [t for t in tokens if t]
        boilerplate = {"map", "location", "directions"}
        while tokens and tokens[0].lower() in boilerplate:
            tokens.pop(0)
        kw_text = ", ".join(tokens)
        if is_meaningful(kw_text):
            business["Keywords"] = kw_text

    # ---- Description (og:description carries the full, untruncated
    #      copy; the on-page paragraph is only the truncated "read
    #      more" version, with a "..." <span class="dots"> spliced into
    #      the middle of a word -- og:description is preferred, the
    #      paragraph (dots/button stripped) is only a fallback) ----
    og_desc = soup.find("meta", property="og:description")
    if og_desc and og_desc.get("content"):
        desc = clean(og_desc["content"])
        if is_meaningful(desc):
            business["Description"] = desc

    if not business["Description"]:
        desc_tag = soup.select_one("#business-description")
        if desc_tag:
            desc_copy = BeautifulSoup(str(desc_tag), "lxml")
            dots = desc_copy.find("span", class_="dots")
            if dots:
                dots.decompose()
            button = desc_copy.find("button")
            if button:
                button.decompose()
            desc_text = clean(desc_copy.get_text(separator=" "))
            if is_meaningful(desc_text):
                business["Description"] = desc_text

    # ---- Hours (each <li> renders the day name TWICE back-to-back for
    #      responsive layout -- .hour-day-mob and .hour-day-tab hold
    #      identical text -- so only the first span is read for the
    #      day; the value is always the <li>'s LAST span regardless of
    #      which responsive variant is present) ----
    hours_container = soup.select_one(".listing-location-hours ul")
    if hours_container:
        pairs = []
        for li in hours_container.find_all("li"):
            spans = li.find_all("span")
            if len(spans) < 2:
                continue
            day = clean(spans[0].get_text())
            value = clean(spans[-1].get_text())
            if day:
                pairs.append(f"{day}: {value}")
        hours_text = "; ".join(pairs)
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category (the "Claimed • Dental, Dental Specialties,
    #      Dentistry" line mixes a claimed-badge span, a bullet
    #      character, and comma separators rendered as bare <b>,</b>
    #      tags -- not a clean list container -- so this reads the
    #      block's full text and slices off everything up to and
    #      including the bullet character first) ----
    type_container = soup.select_one(".business-info-type")
    if type_container:
        full_text = type_container.get_text(separator=" ")
        if "\u2022" in full_text:
            full_text = full_text.split("\u2022", 1)[1]
        cats = [clean(c) for c in full_text.split(",")]
        cats = [c for c in cats if c]
        if cats:
            business["Category"] = ", ".join(cats)

    # ---- Logo (avatar image at the top of the listing card -- same
    #      URL as og:image) ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])
    if not business["Logo"]:
        avatar = soup.select_one(".business-info-avatar img[src]")
        if avatar:
            business["Logo"] = urljoin(url, avatar["src"])

    # ---- Photos (gallery slider items -- data-src holds the full-size
    #      image; the nested <img src> is a smaller duplicate of the
    #      same photo, so data-src is preferred) ----
    photos = []
    for item in soup.select(".listing-photos-slider-item[data-src]"):
        src = item.get("data-src")
        if src:
            photos.append(urljoin(url, src))
    business["Photos"] = photos

    # ---- Business Email / Social Media Links / GBP Link: intentionally
    #      left blank --
    #      - Business Email: no business-owned email appears anywhere in
    #        this template; the only mailto: on the page
    #        (support@merchantcircle.com) is MerchantCircle's own
    #        support address, not the listed business's.
    #      - Social Media Links: the only social links present are
    #        MerchantCircle's own footer icons (facebook.com/
    #        MerchantCirclecom, twitter.com/MerchantCircle,
    #        linkedin.com/company/merchantcircle), identical on every
    #        page regardless of listing -- not the business's own
    #        profiles, so not scanned in.
    #      - GBP Link: the "Location & hours" map is a bare Google Maps
    #        embed API URL built from the address/lat-long (see the
    #        inline `map_url` in the page's own JS), not a real Google
    #        Business Profile link. ----

    return business


# ==========================================================
# globalbusinessdirectory.us
# ==========================================================
#
# Static, server-rendered HTML (WordPress + WP Job Manager "findus"
# theme; no bot-wall observed). Confirmed on a live listing (church-
# street-family-and-cosmetic-dentistry): Name/Description/Category/
# Keywords/Phone/Website/Logo/Photos are all readable straight from
# their own elements; the address is a single unsplit "Street, City,
# ST Zip" string (reused via _split_blinx_address, same shape as
# blinx.biz's rendered address); Country isn't printed anywhere as
# text but IS encoded in the <article> tag's own
# "job_listing_region-<slug>" CSS class (WP Job Manager's region
# taxonomy term). Business Email is a genuine business-owned address
# in the sidebar "Contact Business" widget -- NOT the theme/site
# owner's own contact email that also appears in the footer.
# No Hours widget was rendered anywhere on this listing (the theme's
# JS strings reference one, e.g. "closed_text":"Closed", so the
# feature exists, but nothing renders when a listing has no hours on
# file) -- Hours is therefore left out of SOURCE_FIELDS for this
# source until a listing with actual hours confirms the markup.

_GBD_REGION_CLASS_RE = re.compile(r"job_listing_region-([\w-]+)")


def parse_globalbusinessdirectory(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name ----
    name_tag = soup.select_one('h1.entry-title[itemprop="name"]')
    if name_tag:
        business["Business Name"] = clean(name_tag.get_text())

    if not business["Business Name"]:
        meta_title = soup.find("meta", itemprop="title")
        if meta_title and meta_title.get("content"):
            business["Business Name"] = clean(meta_title["content"])

    # ---- Address (rendered as a single unsplit "Street, City, ST Zip"
    #      string on the map link -- same shape blinx.biz's page
    #      renders its address in, so the existing splitter is reused
    #      rather than writing a second copy of the same regex) ----
    addr_tag = soup.select_one("a.google_map_link")
    if addr_tag:
        addr_text = clean(addr_tag.get_text())
        if addr_text:
            street, city, state, zipcode = _split_blinx_address(addr_text)
            business["Street"] = street
            business["City"] = city
            business["State"] = state
            business["Zipcode"] = zipcode

    # ---- Country (not printed as text anywhere on the page -- WP Job
    #      Manager instead encodes the region taxonomy term directly in
    #      the <article> tag's own CSS class, e.g.
    #      "job_listing_region-united-states") ----
    article = soup.select_one("article.job_listing")
    if article:
        region_match = _GBD_REGION_CLASS_RE.search(" ".join(article.get("class", [])))
        if region_match:
            business["Country"] = region_match.group(1).replace("-", " ").title()

    # ---- Phone (itemprop="telephone" appears on the sidebar copy; the
    #      entry-header copy above it has no itemprop, so the selector
    #      targets itemprop specifically rather than the first ".phone"
    #      match, which could otherwise land on the header's) ----
    phone_tag = soup.select_one('[itemprop="telephone"]')
    if phone_tag:
        business["Phone"] = clean(phone_tag.get_text())

    # ---- Website URL ----
    site_link = soup.select_one("a.listing--website[href]")
    if site_link:
        business["Website URL"] = site_link["href"]

    # ---- Keywords (the tagline directly under the title is a genuine
    #      comma-separated business keyword string, not page-boilerplate
    #      -- confirmed: "dentists, cosmetic dentistry, dental implants,
    #      dental crowns, teeth whitening") ----
    tagline = soup.select_one(".listing-tagline")
    if tagline:
        kw_text = clean(tagline.get_text())
        if is_meaningful(kw_text):
            business["Keywords"] = kw_text

    # ---- Description ----
    desc_tag = soup.select_one("#listing-description .box-inner p")
    if desc_tag:
        desc_text = clean(desc_tag.get_text(separator=" "))
        if is_meaningful(desc_text):
            business["Description"] = desc_text

    # ---- Category (breadcrumb-style category link(s) directly under
    #      the title/phone/address metas) ----
    cat_links = [clean(a.get_text()) for a in soup.select(".listing-category a")]
    cat_links = [c for c in cat_links if c]
    if cat_links:
        business["Category"] = ", ".join(cat_links)

    # ---- Logo (the listing's own uploaded logo image; markup is
    #      lazy-loaded so the real URL lives in data-src, not src --
    #      src is only ever a tiny inline placeholder SVG) ----
    logo_tag = soup.select_one(".listing-logo img")
    if logo_tag:
        logo_src = logo_tag.get("data-src") or logo_tag.get("src")
        if logo_src and not logo_src.startswith("data:"):
            business["Logo"] = urljoin(url, logo_src)

    # ---- Photos (main gallery slider only -- a second slider right
    #      below it duplicates the same photo(s) as small nav
    #      thumbnails, so only ".slick-carousel-gallery-main" is read
    #      to avoid pulling in duplicates) ----
    photos = []
    for a in soup.select(".slick-carousel-gallery-main .photo-gallery-item[href]"):
        photos.append(urljoin(url, a["href"]))
    business["Photos"] = photos

    # ---- Business Email (the sidebar "Contact Business" widget's own
    #      author-info block holds the business's real email -- NOT the
    #      theme/site owner's own contact email that also appears
    #      unrelated in the page footer) ----
    email_tag = soup.select_one(".author-info a[href^='mailto:']")
    if email_tag:
        business["Business Email"] = email_tag["href"].replace("mailto:", "").strip()

    # ---- Social Media Links / GBP Link: intentionally left blank --
    #      - Social Media Links: the only social icons present are a
    #        generic page-share popup (Facebook/Twitter/LinkedIn/
    #        Google+/Pinterest links built from the listing's own share
    #        URL via onclick JS, not the business's profiles) and a
    #        footer set of icons that are all unconfigured placeholder
    #        hrefs ("#") -- neither represents the business's own
    #        social presence.
    #      - GBP Link: the "Get Directions"/map links are all bare
    #        maps.google.com/maps?q=<address> search-query URLs, not a
    #        real Google Business Profile link. ----

    return business


# ==========================================================
# chamberofcommerce.com
# ==========================================================
#
# Static, server-rendered HTML (requests-fetched; no bot-wall observed
# on the tested listing). Name/address/description/logo are all
# available from an embedded schema.org LocalBusiness JSON-LD block,
# but that block has NO "telephone" field at all on this template --
# the real business phone only exists as plain text in the "Key
# Contacts" sidebar card, next to a literal fa-phone icon (a second
# number appears in the "Contact Info" card next to an fa-fax icon --
# that one is deliberately excluded here since it's explicitly the fax
# line, not the phone). Business Email is rendered through
# Cloudflare's "email protection" obfuscation (a data-cfemail hex blob
# on a <span class="__cf_email__">, not a real mailto: link) and has
# to be decoded rather than read directly -- reuses the shared
# _find_cf_email()/_decode_cf_email() helpers already used by other
# parsers in this file (NOT a locally-defined duplicate: an earlier
# version of this parser accidentally redefined _decode_cf_email under
# the same name, which silently shadowed the original for every other
# parser in the module too, since Python resolves module-level
# function names at call time, not definition time -- fixed by
# removing the duplicate and calling the shared helper directly). Also
# falls back to a plain mailto: link, since Cloudflare's own
# email-decode.min.js script rewrites the obfuscated markup into a
# real mailto: link client-side -- a fetch path that runs JavaScript
# (or a response variant that isn't obfuscated at all) would only ever
# have the plain, already-decoded form to read. Category comes from the
# breadcrumb trail (the crumb immediately before the business-name
# crumb). GBP Link is intentionally left blank: the only Google Maps
# references on this template are a bare "q=<address>" directions
# search link and a Maps *Embed API* iframe URL that carries the
# page's own API key -- neither is a real, shareable Google Business
# Profile link.

def parse_chamberofcommerce(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- JSON-LD (primary source: name, address, description, logo) ----
    for script in soup.find_all("script", type="application/ld+json"):

        if not script.string:
            continue

        try:
            # strict=False: this template's own "description" field embeds
            # literal, unescaped line breaks between paragraphs (not "\n"
            # escape sequences -- actual raw newline bytes), which fails
            # strict JSON parsing outright and would otherwise silently
            # drop the entire LocalBusiness object -- wiping out every
            # field sourced from it (Street/City/State/Zipcode/Country/
            # Description/Logo) even though the object is fine other than
            # that one string's formatting.
            data = json.loads(script.string, strict=False)
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]

        for obj in objects:

            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
                continue

            if obj.get("name"):
                business["Business Name"] = clean(obj["name"])

            if obj.get("description"):
                # The JSON-LD description carries raw embedded HTML
                # (<p>/<span> tags) as plain string content, not just
                # text -- stripped via BeautifulSoup rather than
                # regexed out, so entities/whitespace collapse cleanly.
                desc_text = clean(
                    BeautifulSoup(obj["description"], "lxml").get_text(separator=" ")
                )
                if is_meaningful(desc_text):
                    business["Description"] = desc_text

            if obj.get("image"):
                business["Logo"] = urljoin(url, obj["image"])

            addr = obj.get("address", {})
            if isinstance(addr, dict):
                business["Street"] = clean(addr.get("streetAddress", ""))
                business["City"] = clean(addr.get("addressLocality", ""))
                business["State"] = clean(addr.get("addressRegion", ""))
                business["Zipcode"] = clean(addr.get("postalCode", ""))
                business["Country"] = clean(addr.get("addressCountry", ""))

    # ---- Business Name fallback (visible H1) ----
    if not business["Business Name"]:
        h1 = soup.select_one("h1")
        if h1:
            business["Business Name"] = clean(h1.get_text())

    # ---- Address fallback (visible summary-block spans, tagged with
    #      the same selector-type="..." attribute convention used for
    #      Website/Facebook/Twitter below -- independent of JSON-LD, so
    #      this still fills Street/City/State/Zipcode even on the
    #      occasions the JSON-LD block fails to parse. City/State/Zip
    #      render with baked-in punctuation/whitespace ("Spokane
    #      Valley,", " Washington", " 99216"), stripped here.
    #      Country has no equivalent visible span anywhere on this
    #      template -- when this fallback path is the one supplying the
    #      address (i.e. JSON-LD didn't), Country defaults to "US"
    #      since chamberofcommerce.com is a US-only business directory
    #      and every listing's breadcrumb is a US state) ----
    if not business["Street"]:
        addr1 = soup.select_one('span[selector-type="Address1"]')
        if addr1:
            street = clean(addr1.get_text())
            addr2 = soup.select_one('span[selector-type="Address2"]')
            if addr2:
                addr2_text = clean(addr2.get_text())
                if addr2_text:
                    street = f"{street}, {addr2_text}"
            business["Street"] = street

    if not business["City"]:
        city_tag = soup.select_one('span[selector-type="City"]')
        if city_tag:
            business["City"] = clean(city_tag.get_text()).rstrip(",")

    if not business["State"]:
        state_tag = soup.select_one('span[selector-type="State"]')
        if state_tag:
            business["State"] = clean(state_tag.get_text())

    if not business["Zipcode"]:
        zip_tag = soup.select_one('span[selector-type="Zip"]')
        if zip_tag:
            business["Zipcode"] = clean(zip_tag.get_text())

    if not business["Country"] and business["Street"]:
        business["Country"] = "US"

    # ---- Description fallback (on-page About card). This template's
    #      own markup is <p id="BusinessAbout"><p>...</p><p>...</p></p>
    #      -- a <p> nested directly inside another <p>, which is invalid
    #      HTML that lxml (like a browser) auto-closes the OUTER <p> for
    #      as soon as the inner one starts. The id="BusinessAbout" tag
    #      ends up empty, and the real paragraphs become its following
    #      siblings instead of its children -- so selecting by id and
    #      reading its own get_text() returns nothing. Instead, this
    #      locates the "About" card by its heading and reads the whole
    #      card body's text with just the heading removed, which is
    #      immune to how the paragraphs inside end up nested/sibling'd. ----
    if not business["Description"]:
        about_card = None
        for heading in soup.select(".card-body h3.card-title"):
            if "about" in clean(heading.get_text()).lower():
                about_card = heading.find_parent("div", class_="card-body")
                break
        if about_card:
            card_copy = BeautifulSoup(str(about_card), "lxml")
            heading_copy = card_copy.find("h3")
            if heading_copy:
                heading_copy.decompose()
            desc_text = clean(card_copy.get_text(separator=" "))
            if is_meaningful(desc_text):
                business["Description"] = desc_text

    # ---- Description final fallback (meta description tag -- shorter
    #      and more generic/boilerplate than the About card, so only
    #      used if nothing else produced anything at all) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            desc_text = clean(meta_desc["content"])
            if is_meaningful(desc_text):
                business["Description"] = desc_text

    # ---- Phone (Key Contacts card's fa-phone line -- the Contact Info
    #      card's own number sits next to an fa-fax icon and is
    #      excluded on purpose, since it's the fax line, not Phone) ----
    phone_icon = soup.select_one("i.fa-phone")
    if phone_icon and phone_icon.parent:
        phone_text = clean(phone_icon.parent.get_text())
        if phone_text:
            business["Phone"] = phone_text

    # ---- Website URL ----
    site_span = soup.select_one('span[selector-type="Website"] a[href]')
    if site_span:
        business["Website URL"] = site_span["href"]

    # ---- Keywords ----
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_text = clean(meta_kw.get("content", ""))
        if is_meaningful(kw_text):
            business["Keywords"] = kw_text

    # ---- Hours (two duplicate blocks exist for mobile/desktop layout
    #      toggling -- .HoursofOperation -- day/value pairs are plain
    #      sibling col-5/col-7 divs rather than a <table>, so they're
    #      read in lockstep pairs off the first block found) ----
    hours_container = soup.select_one(".HoursofOperation .row.mb-0.text-dark")
    if hours_container:
        cells = hours_container.find_all("div", recursive=False)
        pairs = []
        for i in range(0, len(cells) - 1, 2):
            day = clean(cells[i].get_text()).rstrip(":")
            value = clean(cells[i + 1].get_text())
            if day:
                pairs.append(f"{day}: {value}")
        hours_text = "; ".join(pairs)
        if is_meaningful(hours_text):
            business["Hours"] = hours_text

    # ---- Category (breadcrumb crumb immediately before the active
    #      business-name crumb -- this template renders the breadcrumb
    #      as a <ul class="breadcrumb">, NOT an <ol>, so the selector
    #      is tag-agnostic rather than assuming <ol> like some other
    #      sources in this file do) ----
    crumbs = [clean(li.get_text()) for li in soup.select(".breadcrumb li.breadcrumb-item")]
    crumbs = [c for c in crumbs if c]
    if len(crumbs) >= 2:
        business["Category"] = crumbs[-2]

    # ---- Logo fallback (profile image, if JSON-LD had none) ----
    if not business["Logo"]:
        logo_img = soup.select_one("img.ProfileImage")
        if logo_img and logo_img.get("src"):
            business["Logo"] = urljoin(url, logo_img["src"])

    # ---- Photos (lightbox-linked full-size images in the Images
    #      carousel; the nested <img src> is a small 125x125 thumbnail
    #      of the same photo, so the anchor's href is preferred) ----
    photos = []
    for a in soup.select("#profile_images a.lightbox_trigger[href]"):
        photos.append(urljoin(url, a["href"]))
    business["Photos"] = photos

    # ---- Business Email. Primary path is the shared _find_cf_email()
    #      helper (already used by other parsers in this file), which
    #      handles Cloudflare's "email protection" obfuscation in both
    #      the shapes it can appear in -- the <a href="/cdn-cgi/l/
    #      email-protection#HEX"> wrapper and the inner <span
    #      data-cfemail="HEX">. Falls back to a plain mailto: link,
    #      since Cloudflare's own email-decode.min.js script rewrites
    #      that obfuscated markup into a real mailto: link client-side
    #      -- a fetch path that executes JavaScript (or any response
    #      variant that isn't obfuscated to begin with) would only ever
    #      have the plain, already-decoded form to read here. ----
    cf_email = _find_cf_email(soup)
    if cf_email:
        business["Business Email"] = cf_email

    if not business["Business Email"]:
        mailto = soup.select_one('a[href^="mailto:"]')
        if mailto and mailto.get("href"):
            business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Social Media Links ----
    for network in ("Facebook", "Twitter"):
        link = soup.select_one(f'span[selector-type="{network}"] a[href]')
        if link and link.get("href"):
            business["Social Media Links"][network] = link["href"]

    # ---- GBP Link: intentionally left blank -- the only Google Maps
    #      references on this template are a bare "Directions" link
    #      built from a "q=<address>" search query and a Maps Embed API
    #      iframe URL carrying the page's own API key; neither is a
    #      real, shareable Google Business Profile link. ----

    return business


# ==========================================================
# Site parser: trueen.com
# ==========================================================
#
# Built and verified against the listing's actual HTML (not just a
# rendered/markdown view). This template carries two JSON-LD blocks
# that make extraction unusually clean for a directory site:
#
#   1. @type: FAQPage -- its answer text is the cleanest source for
#      Street/City/State/Zipcode, Phone, Website URL, and the "Who is
#      X?" narrative used as Description: no icon markup or nested-tag
#      cleanup needed, just the raw answer string. Checked first for
#      each of those fields.
#   2. @type: LocalBusiness -- name/telephone/address/description, used
#      as a secondary cross-check/fallback if the FAQPage block above
#      is ever missing. NOTE: on the tested listing this block's
#      address.addressLocality holds the *entire* "Street, City, State
#      Zip" string (not just the locality) -- addressLocality is still
#      run through the same _split_trueen_address() splitter here
#      rather than treated as a real single-field locality.
#
# CSS fallbacks (verified selectors, not guesses) back up both JSON-LD
# blocks in case a listing variant omits one:
#   - Business Name:  h1.header-titlex
#   - Category:       span.single-page-category a
#   - Country:         the <a> inside the <p> with the fa-passport icon
#   - Street/City/etc: the <p> with the fa-map-marker icon
#   - Phone:           p.single-page-phone
#   - Website URL:     the "View website" button -- a.view-button with
#                       target="_blank" + rel="nofollow", which is what
#                       distinguishes it from the "Write a Review"
#                       button (also class="view-button", but href="#"
#                       and no target/rel)
#   - Description:     div.company-bio (note: this template nests <p>
#                       tags without closing them -- BeautifulSoup/lxml
#                       auto-repairs that on parse, so paragraph text is
#                       still read out correctly via find_all("p"))
#
# The listing tested (Focal Newark, unclaimed/free-tier) has no Hours,
# Business Email, Keywords, Social Media Links, GBP Link, Logo, or
# Photos anywhere on the page:
#   - The "Share This Listing" icons are share-intent buttons
#     (href="#", the target URL is built in an onclick handler that
#     opens facebook.com/sharer.php/twitter.com/intent/etc for THIS
#     listing's own URL) -- not the business's own social profiles.
#   - The only mailto: link tied to the listing is the "Share via
#     Email" CTA (mailto:?subject=...&body=...), with no address
#     before the "?" -- not a real business email.
#   - TRUEen's own footer (support/info@trueen.com, its Facebook/
#     Twitter/LinkedIn handles, WhatsApp number) must NOT be mistaken
#     for the business's own contact info -- explicitly excluded below.
#   - og:image is TRUEen's own generic share-card image
#     (images/logo/og-image-trueen.png), not the business's logo.
# A claimed/premium listing may expose more of these (the page itself
# says "Do you want to add product, services, areas and many more
# features? Just upgrade your business now."), so best-effort
# extraction is still attempted for Email/Logo/Social; fields_config.py's
# trueen.com entry can be widened later once that's confirmed without
# touching this function.

_TRUEEN_OWN_EMAIL_DOMAINS = ("trueen.com",)
_TRUEEN_OWN_SOCIAL_HANDLES = (
    "facebook.com/trueencom",
    "twitter.com/trueen_com",
    "linkedin.com/company/trueen-com",
)


def _split_trueen_address(text):
    """Splits a single "Street[, Suite], City, State Zip" string (this
    template renders the full address as one run of text, comma-
    separated, with no per-field markup -- confirmed both in the
    visible <p class="fa-map-marker"> line and in the JSON-LD's
    address.addressLocality) into parts. Pulls the trailing ZIP off
    the end first since it's the only fixed-format anchor; State is
    then whatever comma-segment is left immediately before it; City is
    the segment before that; everything earlier (which may itself
    contain commas, e.g. a ", Suite 305" clause) is rejoined as Street.

    "131 Continental Dr, Suite 305, Newark, Delaware 19713" ->
    Street="131 Continental Dr, Suite 305", City="Newark",
    State="Delaware", Zipcode="19713"
    """
    result = {"Street": "", "City": "", "State": "", "Zipcode": ""}

    text = clean(text)
    if not text:
        return result

    zip_match = re.search(r"(\d{5}(?:-\d{4})?)\s*$", text)
    if zip_match:
        result["Zipcode"] = zip_match.group(1)
        text = text[:zip_match.start()].strip().rstrip(",").strip()

    parts = [p.strip() for p in text.split(",") if p.strip()]

    if parts:
        result["State"] = parts.pop()
    if parts:
        result["City"] = parts.pop()
    if parts:
        result["Street"] = ", ".join(parts)

    return result


def _trueen_faq_answers(soup):
    """Reads the @type: FAQPage JSON-LD block into a
    {lowercased question: raw answer text} dict. Returns {} if the
    block isn't present (older/variant listings)."""
    answers = {}
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("@type") != "FAQPage":
            continue
        for item in data.get("mainEntity", []):
            if not isinstance(item, dict):
                continue
            question = clean(item.get("name", ""))
            accepted = item.get("acceptedAnswer", {})
            text = accepted.get("text", "") if isinstance(accepted, dict) else ""
            if question and text:
                answers[question.lower()] = text
    return answers


def _trueen_local_business_jsonld(soup):
    """Reads the @type: LocalBusiness JSON-LD block. Returns {} if not
    present."""
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("@type") == "LocalBusiness":
            return data
    return {}


def parse_trueen(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    faq = _trueen_faq_answers(soup)
    local_business = _trueen_local_business_jsonld(soup)

    # ---- Business Name ----
    h1 = soup.select_one("h1.header-titlex") or soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"] and local_business.get("name"):
        business["Business Name"] = clean(local_business["name"])
    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            # Drop the " - <tagline>, <location> - TRUEen" suffix this
            # template appends to every og:title.
            business["Business Name"] = clean(og_title["content"].split(" - ")[0])

    # ---- Category ----
    # Rendered once near the top ("Unclaimed ! . <category>") and again
    # at the bottom ("More Recent Businesses" link) with the identical
    # href -- select_one takes the first (top) match either way.
    cat_link = soup.select_one("span.single-page-category a") or \
        soup.select_one('a[href*="/business-listing/category/"]')
    if cat_link:
        business["Category"] = clean(cat_link.get_text())

    # ---- Country ----
    country_icon = soup.select_one("i.fa-passport")
    country_link = (
        country_icon.find_parent("p").select_one("a") if country_icon and country_icon.find_parent("p") else None
    ) or soup.select_one('a[href*="/business-listing/country/"]')
    if country_link:
        business["Country"] = clean(country_link.get_text())
    elif local_business.get("address", {}).get("addressCountry"):
        business["Country"] = clean(local_business["address"]["addressCountry"])

    # ---- Street / City / State / Zipcode ----
    # Priority: FAQPage's "headquarters located" answer (cleanest, no
    # markup to strip) -> LocalBusiness JSON-LD's addressLocality
    # (confirmed on this template to hold the full address string, not
    # just a locality) -> the visible fa-map-marker line as a last
    # resort. All three feed the same splitter since they're all the
    # same "Street, City, State Zip" single-string shape.
    address_text = None
    for question, text in faq.items():
        if "headquarters located" in question:
            address_text = text
            break

    if not address_text:
        addr_locality = local_business.get("address", {}).get("addressLocality")
        if addr_locality:
            address_text = addr_locality

    if not address_text:
        marker_icon = soup.select_one("i.fa-map-marker")
        if marker_icon and marker_icon.find_parent("p"):
            address_text = marker_icon.find_parent("p").get_text()

    if address_text:
        parts = _split_trueen_address(address_text)
        business["Street"] = parts["Street"]
        business["City"] = parts["City"]
        business["State"] = parts["State"]
        business["Zipcode"] = parts["Zipcode"]

    # ---- Phone ----
    for question, text in faq.items():
        if "contact phone number" in question and re.search(r"\d{5,}", text):
            business["Phone"] = clean(text)
            break

    if not business["Phone"] and local_business.get("telephone"):
        business["Phone"] = clean(local_business["telephone"])

    if not business["Phone"]:
        phone_p = soup.select_one("p.single-page-phone")
        if phone_p:
            business["Phone"] = clean(phone_p.get_text())

    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel and tel.get("href"):
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    # ---- Website URL ----
    # The "View website" button is the reliable CSS target: it's the
    # only a.view-button on the page with target="_blank" AND
    # rel="nofollow" -- the "Write a Review" button shares the same
    # view-button class but has href="#" and neither attribute.
    website_link = soup.select_one('a.view-button[target="_blank"][rel="nofollow"]')
    if website_link and website_link.get("href"):
        href = website_link["href"].strip()
        if href and not href.lower().startswith("javascript:"):
            business["Website URL"] = href

    if not business["Website URL"]:
        for question, text in faq.items():
            if "official website" in question and text.strip().lower().startswith(("http://", "https://")):
                business["Website URL"] = clean(text)
                break

    # ---- Description ("Who is X?" narrative -- the only genuine
    #      free-text bio on the page) ----
    for question, text in faq.items():
        # Exclude the "Who is the Owner/CEO/Representative of X?"
        # question -- same "Who is" prefix, different answer entirely
        # (and not a field this schema tracks).
        if question.startswith("who is") and "owner" not in question and "ceo" not in question:
            business["Description"] = clean_multiline(text)
            break

    if not business["Description"]:
        bio = soup.select_one("div.company-bio")
        if bio:
            paragraphs = [clean(p.get_text()) for p in bio.find_all("p")]
            paragraphs = [p for p in paragraphs if p]
            if paragraphs:
                business["Description"] = "\n".join(paragraphs)
            else:
                business["Description"] = clean(bio.get_text())

    if not business["Description"] and local_business.get("description"):
        business["Description"] = clean(local_business["description"])

    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and is_meaningful(meta_desc.get("content", "")):
            business["Description"] = clean(meta_desc["content"])

    # ---- Business Email (best-effort; none present on the tested
    #      listing) ----
    # The only mailto: tied to the listing itself is the "Share via
    # Email" CTA (mailto:?subject=...&body=...) with no address before
    # the "?", and TRUEen's own support/info@trueen.com footer emails
    # aren't the business's -- both are excluded here.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.lower().startswith("mailto:"):
            continue
        addr = href[len("mailto:"):].split("?")[0].strip()
        if not addr:
            continue
        if any(addr.lower().endswith("@" + d) for d in _TRUEEN_OWN_EMAIL_DOMAINS):
            continue
        business["Business Email"] = addr
        break

    # ---- Logo (best-effort; none present on the tested listing) ----
    # og:image on this template is TRUEen's own generic share-card
    # image (images/logo/og-image-trueen.png), not the business's own
    # logo, so it's deliberately NOT used as a fallback here the way
    # other parsers in this file use og:image.
    for img in soup.find_all("img", src=True):
        src = img["src"]
        low = src.lower()
        if "trueen-logo" in low or "og-image-trueen" in low or "loader.gif" in low:
            continue
        alt = clean(img.get("alt", "")).lower()
        if business["Business Name"] and business["Business Name"].lower() in alt:
            business["Logo"] = urljoin(url, src)
            break

    # ---- Social Media Links (best-effort; none present on the tested
    #      listing) ----
    # The "Share This Listing" icons are share-intent buttons
    # (href="#", real target built in an onclick handler pointing at
    # facebook.com/sharer.php etc for THIS LISTING's own URL, not a
    # link to the business's own social profile), and TRUEen's own
    # footer social links (Facebook/Twitter/LinkedIn "TrueEnCom"-style
    # handles) belong to TRUEen itself, not the business -- both are
    # excluded here.
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href == "#" or href.lower().startswith("javascript:"):
            continue
        low = href.lower()
        if any(handle in low for handle in _TRUEEN_OWN_SOCIAL_HANDLES):
            continue
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# Site parser: citysquares.com
# ==========================================================
#
# Static HTML, fetched with requests. Verified against the real page
# source (WrightWay Emergency Services / Nokomis, FL listing) -- no
# JSON-LD on this template, so every field below comes from a
# confirmed CSS selector rather than a structured-data block:
#   - Business Name:  h1.listing
#   - Logo:            div.logo img (the business's own uploaded
#                       photo, hosted on the site's S3 bucket -- not
#                       to be confused with the CitySquares site logo
#                       in the top nav, which lives outside this div)
#   - Phone:           div.phone.element
#   - Street/City/
#     State/Zipcode:   span#full-address, a single "Street, City,
#                       State, Zipcode" string with FOUR comma-
#                       separated segments (Zip is its OWN segment
#                       here, not merged into the State segment the
#                       way _split_blinx_address expects) -- needs its
#                       own splitter, _split_citysquares_address.
#   - Business Email:  Cloudflare's "email protection" obfuscation
#                       (identical markup shape to chamberofcommerce.com
#                       -- decoded via the shared _find_cf_email()
#                       helper already used there).
#   - Website URL:     div.website.element a[rel="nofollow"]
#   - Social Media
#     Links:           div.socials.section -- Instagram/Twitter/
#                       Facebook/LinkedIn/YouTube anchors, matched
#                       generically against SOCIAL_DOMAINS the same
#                       way parse_trueen and others do.
#   - Hours:           div.hours.section (label "Business hours" is a
#                       heading, not the value -- the value is the
#                       <p> beneath it).
#   - Description:     div.about.section's "About us" paragraph.
#   - Category:        the breadcrumb's LAST link whose href starts
#                       with "/cat/" (the breadcrumb also includes a
#                       "/p/<state>/<city>" location crumb earlier,
#                       which is not a category and is skipped).
#   - Photos:          all <img> inside div.images.section (the
#                       gallery slideshow) -- kept distinct from Logo.
#
# No Country anywhere on the page (state names like "Florida" imply
# the US but that's not the same as a labeled Country field, so it's
# deliberately left blank rather than assumed), no Keywords field
# exists on this template, and GBP Link is left blank for the same
# reason as chamberofcommerce.com and trueen.com: the only Google Maps
# reference is a Maps Embed API iframe URL carrying the page's own API
# key, not a real shareable Google Business Profile link.

def _split_citysquares_address(text):
    """Splits a single "Street, City, State, Zipcode" string -- FOUR
    comma-separated segments on this template, with Zip as its own
    trailing segment (unlike blinx.biz's "Street, City, ST ,Zip" shape,
    where state+zip share a segment -- _split_blinx_address's "last
    segment is state+zip together" assumption doesn't hold here, hence
    a dedicated splitter).

    "300 Triple Diamond Boulevard, Nokomis, Florida, 34275" ->
    Street="300 Triple Diamond Boulevard", City="Nokomis",
    State="Florida", Zipcode="34275"
    """
    result = {"Street": "", "City": "", "State": "", "Zipcode": ""}

    parts = [p.strip() for p in clean(text).split(",") if p.strip()]
    if not parts:
        return result

    if re.match(r"^\d{5}(?:-\d{4})?$", parts[-1]):
        result["Zipcode"] = parts.pop()

    if parts:
        result["State"] = parts.pop()
    if parts:
        result["City"] = parts.pop()
    if parts:
        result["Street"] = ", ".join(parts)

    return result


def parse_citysquares(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name ----
    h1 = soup.select_one("h1.listing")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Street / City / State / Zipcode ----
    address_span = soup.select_one("#full-address")
    if address_span:
        parts = _split_citysquares_address(address_span.get_text())
        business["Street"] = parts["Street"]
        business["City"] = parts["City"]
        business["State"] = parts["State"]
        business["Zipcode"] = parts["Zipcode"]


    return business



    """Best-effort fallback for domains without a dedicated parser above.
    Covers the common patterns seen across both sites so far: JSON-LD
    LocalBusiness, business:contact_data:* meta, plain meta tags,
    tel:/mailto: links. Category/Hours are left blank since those are
    too structurally different site-to-site to guess reliably."""

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    for meta in soup.find_all("meta", property=True):
        prop = meta["property"]
        if prop.startswith("business:contact_data:"):
            key = prop.split(":")[-1]
            val = clean(meta.get("content", ""))
            if key == "street_address":
                business["Street"] = val
            elif key == "locality":
                business["City"] = val
            elif key == "region":
                business["State"] = val
            elif key == "postal_code":
                business["Zipcode"] = val
            elif key == "country_name":
                business["Country"] = val
            elif key == "phone_number":
                business["Phone"] = val
            elif key == "website":
                business["Website URL"] = val

    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        objects = data if isinstance(data, list) else [data]
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            if obj.get("@type") in ("LocalBusiness", "Organization"):
                business["Business Name"] = obj.get("name", business["Business Name"])
                if obj.get("telephone") and not business["Phone"]:
                    business["Phone"] = obj["telephone"]
                if obj.get("image") and not business["Logo"]:
                    business["Logo"] = urljoin(url, obj["image"])
                addr = obj.get("address", {})
                if not business["Street"]:
                    business["Street"] = addr.get("streetAddress", "")
                if not business["City"]:
                    business["City"] = addr.get("addressLocality", "")
                if not business["State"]:
                    business["State"] = addr.get("addressRegion", "")
                if not business["Zipcode"]:
                    business["Zipcode"] = addr.get("postalCode", "")
                if not business["Country"]:
                    business["Country"] = addr.get("addressCountry", "")

    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = og_title["content"]

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc:
        desc = clean(meta_desc.get("content", ""))
        if is_meaningful(desc):
            business["Description"] = desc

    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw:
        kw_raw = meta_kw.get("content", "")
        if is_meaningful(kw_raw):
            business["Keywords"] = clean(kw_raw)

    if not business["Phone"]:
        tel = soup.select_one('a[href^="tel:"]')
        if tel:
            business["Phone"] = tel["href"].replace("tel:", "").strip()

    email = soup.select_one('a[href^="mailto:"]')
    if email:
        business["Business Email"] = email["href"].replace("mailto:", "").strip()

    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    for a in soup.find_all("a", href=True):
        href = a["href"]
        for domain, network in SOCIAL_DOMAINS.items():
            if domain in href.lower():
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# Site parser: b2bco.com
# ==========================================================
# Static HTML, fetched with requests (SmartPortal-based B2B marketplace/
# directory template). Name comes from the profile-header <h1>, not
# <title>/og:title, since those are suffixed with " - Marketplace and
# Business Network - B2BCO"; Street/City/State/Country are read from the
# labeled "General Information" section's own spans/links, not a single
# combined address string; Website URL is the visible text of the
# website anchor (e.g. "www.haqq.ai"), not its href, which is an
# internal "/l/?channel=<slug>" outbound-click-tracking redirect;
# Description and Keywords come from the "Business Summary" and
# "Business Keywords" labeled blocks under the Description section (the
# real <meta name="keywords"> tag is used as a fallback since some
# listings may omit the in-page block); Category is the first crumb
# under "Categories" (skips the duplicate "<Category> in <Country>"
# link that follows it); Logo is the profile-header logo image; Hours
# and Business Email were not present anywhere on the tested (unclaimed/
# "not complete") listing this was built against, so they're left
# blank when absent -- see parse_b2bco for details.

def parse_b2bco(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name (profile header h1, not <title>/og:title, which
    # carry a " - Marketplace and Business Network - B2BCO" suffix) ----
    name_el = soup.select_one("div.business.s-title h1")
    if name_el:
        business["Business Name"] = clean(name_el.get_text())

    # ---- Address (labeled "General Information" section) ----
    for addr_div in soup.select("div.Businessaddress"):
        text = clean(addr_div.get_text())
        if re.match(r"^Address:", text, flags=re.I):
            business["Street"] = re.sub(r"^Address:\s*", "", text, flags=re.I)
            break

    country_el = soup.select_one("div.Businesscountry a")
    if country_el:
        business["Country"] = clean(country_el.get_text())

    state_el = soup.select_one("div.countrypart a")
    if state_el:
        business["State"] = clean(state_el.get_text())

    city_el = soup.select_one("div.businesscity a")
    if city_el:
        business["City"] = clean(city_el.get_text())

    # ---- Phone (tel: link) ----
    phone_el = soup.select_one("div.Businessphone a[href^='tel:']")
    if phone_el:
        business["Phone"] = clean(phone_el.get_text())

    # ---- Website URL (visible anchor text, not its /l/?channel=...
    # click-tracking redirect href) ----
    website_el = soup.select_one("div.Businessweb a")
    if website_el:
        site_text = clean(website_el.get_text())
        if site_text:
            business["Website URL"] = site_text

    # ---- Description ("Business Summary" labeled block) ----
    desc_label = soup.find(string=re.compile(r"Business Summary", re.I))
    if desc_label:
        desc_block = desc_label.find_parent("div")
        if desc_block:
            summary_div = desc_block.find_next_sibling("div", class_="comtext")
            if summary_div:
                desc_text = clean(summary_div.get_text())
                if is_meaningful(desc_text):
                    business["Description"] = desc_text
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Keywords ("Business Keywords" labeled block, falling back to
    # <meta name="keywords">) ----
    kw_label = soup.find(string=re.compile(r"Business Keywords", re.I))
    if kw_label:
        kw_block = kw_label.find_parent("div")
        if kw_block:
            kw_div = kw_block.find_next_sibling("div", class_="comtext")
            if kw_div:
                kw_text = clean(kw_div.get_text())
                if is_meaningful(kw_text):
                    business["Keywords"] = kw_text
    if not business["Keywords"]:
        meta_kw = soup.find("meta", attrs={"name": "keywords"})
        if meta_kw:
            kw_raw = clean(meta_kw.get("content", ""))
            if is_meaningful(kw_raw):
                business["Keywords"] = kw_raw

    # ---- Category (first crumb under "Categories"; skips the duplicate
    # "<Category> in <Country>" link that follows it in the same <li>) ----
    category_el = soup.select_one("ul.b-activities li a")
    if category_el:
        business["Category"] = clean(category_el.get_text())

    # ---- Logo (profile header logo image) ----
    logo_el = soup.select_one("div.business.s-title div.logo img[src]")
    if logo_el:
        business["Logo"] = urljoin(url, logo_el["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Business Email (mailto: link, if present) ----
    email_el = soup.select_one('a[href^="mailto:"]')
    if email_el:
        business["Business Email"] = email_el["href"].replace("mailto:", "").split("?")[0].strip()

    # ---- Hours (not present anywhere on the tested "not complete"
    # listing -- left blank if absent) ----

    # ---- GBP Link (a genuine Google Maps / Business Profile link, if
    # present -- excludes the page's own generic map/barcode widgets,
    # which aren't links to an external Maps listing) ----
    gbp_link = soup.select_one('a[href*="google.com/maps"], a[href*="g.page"], a[href*="goo.gl/maps"]')
    if gbp_link and gbp_link.get("href"):
        business["GBP Link"] = gbp_link["href"]

    # ---- Social Media Links (business's own profiles only -- excludes
    # B2BCO's own footer/header social icons, which link to B2BCO's own
    # Facebook/Twitter/LinkedIn/Instagram/Pinterest accounts, not the
    # listed business's) ----
    business_section = soup.select_one("div.core-rail") or soup
    for a in business_section.find_all("a", href=True):
        href = a["href"]
        for domain, network in SOCIAL_DOMAINS.items():
            if _hostname_matches_social_domain(href, domain):
                business["Social Media Links"][network] = href

    return business


# ==========================================================
# Site parser: find-us-here.com
# ==========================================================
# Static HTML, fetched with requests. NOTE: this parser was built from
# a text/markdown extraction of the live listing page (raw HTML source
# wasn't available while writing it), so it deliberately avoids relying
# on exact CSS class names -- every field is located by walking to the
# next element/line after its own on-page label ("Address", "Phone",
# "Email", "Web", "Category:") rather than a fixed selector, so it stays
# robust to markup details that couldn't be directly inspected. Name
# comes from the page's own <h1>, not <title>/og:title, which are
# suffixed with ", <City>, <State>, <Country>"; Street/City/State/
# Zipcode are read by splitting the multi-line block between the
# "Address" and "Phone" labels (last line -> Zipcode if it's 5 digits,
# then State, then City, remainder -> Street); Country is the last
# whitespace-separated token of the "<City>  <State abbr>  <Country>"
# subheading; Phone/Business Email come from tel:/mailto: links; Website
# URL is the first external link found after the "Web" label (falling
# back to the label's own next line of text if no anchor is present, in
# case the template renders it as plain auto-linked text rather than a
# real <a>); Category is read from a "Category: <value>" line, and
# Description from the next sibling block after that line (this
# template appears to pair a "Category: X" row with a longer business
# description row immediately after it in the "About <Business>"
# section); Logo prefers <meta property="og:image"> over any inline
# listing photo. If any of these turn out to not match the real markup
# once run against a live fetch, flag the specific field and it can be
# tightened with a verified CSS selector instead.

_FINDUSHERE_EXCLUDED_LINK_DOMAINS = (
    "find-us-here.com", "facebook.com", "twitter.com", "x.com",
    "whatsapp.com", "wa.me", "telegram.me", "t.me", "google.com",
    "ezoic.net",
)


def parse_findushere(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name (page h1, not <title>/og:title, which carry a
    # ", <City>, <State>, <Country>" suffix) ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    page_text = soup.get_text("\n")

    # ---- Address (Street / City / State / Zipcode) ----
    # The block between the "Address" and "Phone" labels renders as one
    # line per address component (street, city, state, zip); read from
    # the end since Zipcode/State/City are the most reliably identified
    # lines, with whatever remains at the top treated as Street.
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

    # ---- Country (last token of the "<City>  <State abbr>  <Country>"
    # subheading right under the business name, e.g. "Nokomis FL USA") ----
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

    # ---- Business Email. Confirmed via DevTools inspection of the live
    # DOM: rendered as a real <a href="mailto:..."> inside a
    # <span itemprop="email"> wrapper -- but that <a> is written into
    # the DOM by an inline <script>, not present in the raw server HTML,
    # which is why this domain is fetched via Playwright (see
    # SITE_PARSERS) rather than plain requests. Scoped to itemprop=email
    # first to avoid accidentally matching an unrelated mailto: link
    # elsewhere on the page; a bare mailto: scan and the
    # Cloudflare-obfuscation decoder are kept as fallbacks in case some
    # listings render this differently. ----
    email_scope = soup.select_one('[itemprop="email"]') or soup
    mailto = email_scope.select_one('a[href^="mailto:"]') or soup.select_one('a[href^="mailto:"]')
    if mailto:
        business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()
    if not business["Business Email"]:
        business["Business Email"] = _find_cf_email(soup)

    # ---- Website URL (first external, non-directory/non-social link
    # after the "Web" label) ----
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
            # Some listings render the URL as plain auto-linked text
            # rather than a real <a> -- fall back to the next non-empty
            # line of text after the label.
            web_match = re.search(r"\bWeb\b\s*\n\s*(\S+)", page_text)
            if web_match:
                business["Website URL"] = web_match.group(1).strip("<>")

    # ---- Category + Description ("Category: X" line, paired with a
    # longer description block immediately after it under "About
    # <Business>"). Playwright renders the full page, including
    # third-party ad/consent scripts (Ezoic, etc.) whose inline JS can
    # itself contain the literal substring "Category:" -- so
    # script/style text nodes are skipped, and any match is sanity-
    # checked to rule out grabbing minified JS instead of the real
    # label. ----
    category_node = None
    for node in soup.find_all(string=re.compile(r"Category:\s*\S")):
        if node.find_parent(["script", "style"]):
            continue
        candidate = clean(re.sub(r"^.*Category:\s*", "", str(node), flags=re.S))
        # A real category value is a short, plain label -- minified JS
        # caught by the same regex reads nothing like one (long, and/or
        # full of code punctuation), so reject those instead of using
        # them.
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
    return business


# ==========================================================
# Site parser: a-zbusinessfinder.com
# ==========================================================
# Fetched via Playwright from the start (not plain requests) -- this
# site is the same underlying directory-network template family as
# find-us-here.com (identical layout, wording, and even hosts the same
# WrightWay Emergency Services listing data), and find-us-here.com's
# Business Email turned out to be written into the DOM by an inline
# <script> rather than present in the raw server HTML. Confirmed
# structural differences from find-us-here.com that this parser was
# built to handle instead of reusing parse_findushere as-is:
#   - the Address/Phone/Email/Website block renders as a bullet list
#     ("- Physical Address <multi-line address>", "- Phone ...",
#     "- Email", "- Website"), with the "Physical Address" label
#     sharing its very first line with the street address (not on its
#     own line like find-us-here.com's "Address" heading)
#   - there is NO "Category: X" line anywhere on the page and NO
#     <meta property="og:image"> tag at all (confirmed absent from the
#     fetched <head>) -- Category is instead read from the last crumb
#     of the breadcrumb trail (the "»"-separated USA / Florida /
#     Nokomis / Water Damage Restoration path), and Logo comes from the
#     listing's own photo <img> instead of a meta tag
# Business Email is still unconfirmed against the real DOM/network for
# this domain specifically (this parser was built from a text/markdown
# extraction, the same way find-us-here.com's first draft was, and that
# one turned out to need a DevTools check) -- if it comes back blank on
# a live run, inspect the "Email" row the same way and report back what
# the real element looks like.

def parse_azbusinessfinder(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Business Name (page h1, not <title>, which carries a
    # " -  <City>, <State>, <Country> - <Category>" suffix) ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    page_text = soup.get_text("\n")

    # ---- Address (Street / City / State / Zipcode). "Physical Address"
    # shares its line with the first address line, e.g. "Physical
    # Address 300 Triple Diamond Blvd", so it's stripped as a prefix
    # rather than searched for as its own line. ----
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

    # ---- Country (last token of the "<City>  <State>  <Country>"
    # subheading right under the business name) ----
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

    # ---- Business Email. Same platform family as find-us-here.com,
    # where this turned out to be a real mailto: link scoped to a
    # <span itemprop="email"> wrapper but only present in the DOM after
    # an inline <script> runs -- tried here on the same assumption
    # (hence fetching via Playwright), with a page-wide mailto: scan and
    # the Cloudflare-obfuscation decoder kept as fallbacks in case this
    # site's real markup differs. ----
    email_scope = soup.select_one('[itemprop="email"]') or soup
    mailto = email_scope.select_one('a[href^="mailto:"]') or soup.select_one('a[href^="mailto:"]')
    if mailto:
        business["Business Email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()
    if not business["Business Email"]:
        business["Business Email"] = _find_cf_email(soup)

    # ---- Website URL (first external, non-directory/non-social link
    # after the "Website" label) ----
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
        # Falls back to a plain itemprop="url" anchor (this template marks
        # the site link with schema.org itemprop="url"), then to a
        # page-text regex, regardless of whether the "Website" label tag
        # itself was found above -- the tag-based search above can miss
        # the label entirely on table-based layouts, and previously that
        # meant this fallback never ran at all.
        url_link = soup.select_one('a[itemprop="url"][href^="http"]')
        if url_link:
            business["Website URL"] = url_link["href"]
    if not business["Website URL"]:
        web_match = re.search(r"\bWebsite\b\s*\n\s*(\S+)", page_text)
        if web_match:
            business["Website URL"] = web_match.group(1).strip("<>")

    # ---- Category (last link of the "»"-separated breadcrumb trail --
    # no "Category:" line exists anywhere on this template) ----
    breadcrumb = soup.find(lambda tag: tag.name in ("nav", "div", "ul", "ol", "p", "table", "tr", "td") and "»" in tag.get_text())
    if breadcrumb:
        crumb_links = breadcrumb.find_all("a")
        if crumb_links:
            business["Category"] = clean(crumb_links[-1].get_text())

    # ---- Description ("Business/Community Description" section). If
    # the label sits inside a <td>/<th>, that's the nearest match for
    # find_parent() -- but find_next_sibling() from a <td> only reaches
    # other cells in the *same row*, not the next <tr>, so a matched
    # td/th is walked up to its row first. ----
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

    # ---- Logo (listing's own photo -- confirmed no og:image meta tag
    # exists on this template) ----
    logo_img = soup.select_one('img[src*="business_images/main"]') or soup.select_one('img[src*="business_images"]')
    if logo_img and logo_img.get("src"):
        business["Logo"] = urljoin(url, logo_img["src"])
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    return business


# ==========================================================
# Site parser: cybo.com
# ==========================================================

# social_tag query-param values -> Social Media Links network name, as
# seen on the tested listing's redirect hrefs (?...&social_tag=fb, &tw,
# &yt, &linkedin, &instagram, &Tiktok). Unrecognized tags fall back to
# their raw (title-cased) value rather than being dropped, in case a
# different listing carries a network not seen here.
CYBO_SOCIAL_TAG_MAP = {
    "fb": "Facebook",
    "tw": "Twitter",
    "yt": "YouTube",
    "linkedin": "LinkedIn",
    "instagram": "Instagram",
    "tiktok": "TikTok",
}

# Where a social icon's visible text DOES include the real destination
# (confirmed for TikTok: "Tiktoktiktok.com/@wrightwayes" -- the icon's
# own CSS class name "Tiktok" runs directly into the domain "tiktok.com"
# with no separator), the domain root is searched for by name and
# sliced from there, rather than matched with a generic domain regex --
# a generic "[a-z0-9-]+\.[a-z]{2,}" pattern greedily swallows the
# "Tiktok" prefix too (matching "Tiktoktiktok.com" instead of
# "tiktok.com") since there's no character boundary between the two.
# Networks not listed here (Facebook/Twitter/YouTube/LinkedIn/Instagram
# on the tested listing) render as bare icon-name text with no visible
# URL at all, so they fall back to the tracking-redirect href instead.
CYBO_NETWORK_DOMAIN_ROOT = {
    "TikTok": "tiktok.com",
}


def parse_cybo(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()
    page_text = soup.get_text("\n")

    # ---- Business Name ----
    h1 = soup.find("h1")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- Street (Google Maps search link built from the street
    # address -- its visible text is the clean street line; also used
    # as the source for GBP-Link-is-blank below, since this is a plain
    # search query, not a real Business Profile link) ----
    maps_link = soup.select_one('a[href^="https://www.google.com/maps/search/"]')
    if maps_link:
        business["Street"] = clean(maps_link.get_text())

    # ---- City / State / Zipcode / Country (labeled "Address" block) ----
    city_match = re.search(r"\bCity:\s*([^\n]+)", page_text)
    if city_match:
        business["City"] = clean(city_match.group(1))
    state_match = re.search(r"\bState:\s*([^\n]+)", page_text)
    if state_match:
        business["State"] = clean(state_match.group(1))
    zip_match = re.search(r"\bPostal Code:\s*([^\n]+)", page_text)
    if zip_match:
        business["Zipcode"] = clean(zip_match.group(1))
    country_match = re.search(r"\bCountry:\s*([^\n]+)", page_text)
    if country_match:
        business["Country"] = clean(country_match.group(1))

    # ---- Zipcode fallback + Street cleanup for listings with no
    # standalone "Postal Code:" label (confirmed: present on the
    # WrightWay listing, absent on the Focal listing). When that label
    # is missing, the Maps-link text used for Street above turns out to
    # be the FULL "Street, City, State Zip" address rather than just the
    # street line (again confirmed by comparing the two listings) -- so
    # the trailing ", <City>, <State> <Zip>?" is stripped off using the
    # City/State already read above, both cleaning up Street and, if
    # Zipcode is still blank, recovering it from that trailing Zip.
    if business["Street"] and business["City"]:
        tail_pattern = r",?\s*" + re.escape(business["City"])
        if business["State"]:
            tail_pattern += r",?\s*" + re.escape(business["State"])
        tail_pattern += r"\s*(\d{5}(?:-\d{4})?)?\s*$"
        tail_match = re.search(tail_pattern, business["Street"], re.I)
        if tail_match:
            if not business["Zipcode"] and tail_match.group(1):
                business["Zipcode"] = tail_match.group(1)
            business["Street"] = clean(business["Street"][:tail_match.start()].rstrip(","))

    # ---- Phone (NOT a tel: link on this template -- it's wrapped in a
    # "/phone/how-to-call/..." redirect whose visible text is the real,
    # human-formatted number) ----
    phone_link = soup.select_one('a[href*="/phone/how-to-call/"]')
    if phone_link:
        business["Phone"] = clean(phone_link.get_text())

    # ---- Website URL / Social Media Links (both wrapped in the same
    # "/r/biz/web?..." click-tracking redirect; distinguished by the
    # presence/absence of a "social_tag=" query param -- see module
    # docstring for what is/isn't recoverable for each) ----
    for a in soup.select('a[href*="/r/biz/web"]'):
        href = a.get("href", "")
        tag_match = re.search(r"[?&]social_tag=([^&]+)", href)
        if not tag_match:
            if not business["Website URL"]:
                site_text = clean(a.get_text())
                business["Website URL"] = site_text if site_text else href
            continue
        network = CYBO_SOCIAL_TAG_MAP.get(tag_match.group(1).lower(), tag_match.group(1).title())
        link_text = clean(a.get_text())
        value = href
        domain_root = CYBO_NETWORK_DOMAIN_ROOT.get(network)
        if domain_root:
            idx = link_text.lower().find(domain_root)
            if idx != -1:
                value = link_text[idx:]
        business["Social Media Links"][network] = value

    # ---- Description ("About" section) ----
    about_label = soup.find(string=re.compile(r"^\s*About\s*$"))
    if about_label:
        block = about_label.find_parent(["h1", "h2", "h3", "h4", "div", "span"]) or about_label
        next_block = block.find_next(["p", "div"])
        if next_block:
            desc_text = clean(next_block.get_text())
            if is_meaningful(desc_text):
                business["Description"] = desc_text
    if not business["Description"]:
        # Fallback: the paragraph of running text right after a
        # standalone "About" line, before the next labeled section.
        about_match = re.search(
            r"\nAbout\n+(.+?)\n\n(?:💳|👥|\*\*Categories|Categories:|##|$)",
            page_text, re.S,
        )
        if about_match:
            desc_text = clean(about_match.group(1))
            if is_meaningful(desc_text):
                business["Description"] = desc_text
    if not business["Description"]:
        meta_desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Hours (labeled block between "Hours" and "Phone"; the
    # expanded "Every day..." row is preferred over the collapsed
    # single-line summary above it, and a space is restored where the
    # page's own layout runs a label straight into a time, e.g.
    # "Every day12:00 AM") ----
    hours_match = re.search(r"\bHours\s*\n(.*?)\nPhone\b", page_text, re.S)
    if hours_match:
        hour_lines = [clean(line) for line in hours_match.group(1).split("\n")]
        hour_lines = [line for line in hour_lines if line and line != "\u25be"]
        detail_lines = [line for line in hour_lines if "day" in line.lower() or ":" in line]
        chosen = detail_lines[-1] if detail_lines else (hour_lines[-1] if hour_lines else "")
        chosen = re.sub(r"(?<=[a-z])(?=\d)", " ", chosen)
        if is_meaningful(chosen):
            business["Hours"] = chosen

    # ---- Category (prefers the explicit "Categories: X" label in the
    # About section over the shorter category tag/pill under the
    # header, since the labeled one carries the fuller name) ----
    cat_match = re.search(r"\*?\*?Categories:\*?\*?\s*([^\n.]+)", page_text)
    if cat_match:
        business["Category"] = clean(cat_match.group(1))
    if not business["Category"]:
        # Fallback: the category pill/tag link under the header, which
        # (unlike the location breadcrumb links above it) points at a
        # two-segment /US/<city-state-slug>/<category-slug> path.
        cat_link = soup.find("a", href=re.compile(r"^/US/[a-z0-9-]+/[a-z0-9-]+/?$"))
        if cat_link:
            business["Category"] = clean(cat_link.get_text())

    # ---- GBP Link intentionally left blank -- the only Google-Maps
    # reference on this template is the plain search-query link used
    # for Street above, not a real, shareable Business Profile link. ----

    # ---- Logo ----
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        business["Logo"] = urljoin(url, og_image["content"])

    return business


# ==========================================================
# Site parser: linkcentre.com
# ==========================================================

def parse_linkcentre(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Address / Phone (business:contact_data:* OG meta tags --
    # same reliable shape as merchantcircle.com; State has no meta
    # equivalent here either, so it's read from the JSON-LD address
    # block below instead) ----
    meta_map = {
        "business:contact_data:street_address": "Street",
        "business:contact_data:locality": "City",
        "business:contact_data:postal_code": "Zipcode",
        "business:contact_data:country_name": "Country",
        "business:contact_data:phone_number": "Phone",
    }
    for prop, field in meta_map.items():
        tag = soup.find("meta", property=prop)
        if tag and tag.get("content"):
            business[field] = clean(tag["content"])

    # ---- Business Name (og:title carries a " | Restoration Services
    # Reviews & Info | LinkCentre" suffix -- prefer the profile <h1>) ----
    h1 = soup.select_one("h1.v2-hero-name")
    if h1:
        business["Business Name"] = clean(h1.get_text())

    # ---- JSON-LD LocalBusiness block (@graph-wrapped) -- fills in
    # State (no meta equivalent), Website URL, Logo, Description, and
    # backstops Name/Street/City/Zipcode/Phone if the meta tags above
    # were ever missing on some other listing ----
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue

        graph = data.get("@graph") if isinstance(data, dict) else None
        objects = graph if graph else (data if isinstance(data, list) else [data])

        for obj in objects:
            if not isinstance(obj, dict) or obj.get("@type") != "LocalBusiness":
                continue

            if not business["Business Name"]:
                business["Business Name"] = obj.get("name", "")

            addr = obj.get("address", {}) or {}
            if not business["Street"]:
                business["Street"] = addr.get("streetAddress", "")
            if not business["City"]:
                business["City"] = addr.get("addressLocality", "")
            if not business["State"]:
                business["State"] = addr.get("addressRegion", "")
            if not business["Zipcode"]:
                business["Zipcode"] = addr.get("postalCode", "")

            if not business["Phone"]:
                business["Phone"] = obj.get("telephone", "")

            # sameAs holds the business's own external site (confirmed
            # ["https://wrightway.com"] on the tested listing) -- takes
            # the first entry, same idea as zipleaf's site link.
            same_as = obj.get("sameAs") or []
            if same_as:
                business["Website URL"] = same_as[0]

            if obj.get("description"):
                business["Description"] = clean(obj["description"])

            logo_obj = obj.get("logo") or obj.get("image")
            if isinstance(logo_obj, dict) and logo_obj.get("url"):
                business["Logo"] = urljoin(url, logo_obj["url"])
            elif isinstance(logo_obj, str):
                business["Logo"] = urljoin(url, logo_obj)

            # knowsAbout doubles as the category list on this template
            # (confirmed identical to the "Listed In" pill text below).
            knows_about = obj.get("knowsAbout") or []
            if knows_about:
                business["Category"] = ", ".join(knows_about)

    # ---- Website URL fallback (the real, non-tracking anchor in the
    # "Websites & Listings" card, in case sameAs is ever absent) ----
    if not business["Website URL"]:
        listing_url = soup.select_one("a.v2-listing-url[href]")
        if listing_url:
            business["Website URL"] = listing_url["href"]

    # ---- Description fallback (meta description) ----
    if not business["Description"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            desc = clean(meta_desc.get("content", ""))
            if is_meaningful(desc):
                business["Description"] = desc

    # ---- Category fallback ("Listed In" pills; joined if more than
    # one, matching the knowsAbout join above) ----
    if not business["Category"]:
        cat_links = [clean(a.get_text()) for a in soup.select("div.v2-cat-pills a.v2-cat-pill")]
        cat_links = [c for c in cat_links if c]
        if cat_links:
            business["Category"] = ", ".join(cat_links)

    # ---- Logo fallback (og:image) ----
    if not business["Logo"]:
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            business["Logo"] = urljoin(url, og_image["content"])

    # ---- Social Media Links / Business Email intentionally left
    # blank -- confirmed on the tested (unclaimed/free-tier) listing
    # that every social icon and email link on the page belongs to
    # LinkCentre itself: the "Share" strip posts to LinkCentre's own
    # Facebook/X/LinkedIn share-intent URLs and a Cloudflare-obfuscated
    # "share via email" link (both pointing at the profile URL, not a
    # business account), and the only other email present is
    # support@linkcentre.com inside the page's Organization JSON-LD
    # block. No business-owned social profile or email exists anywhere
    # on this template. ----

    return business


# ==========================================================
# Site parser: band.us (BAND / Naver Band group intro pages)
# ==========================================================

_BAND_DESCRIPTION_LABELS = [
    "Owner Name", "Address", "Phone", "Business Email", "About us", "Related Searches",
]


def _band_description_sections(description):
    """BAND packs the entire business record into a single, newline-
    separated meta name="description" string (duplicated verbatim in
    og:description and twitter:description) using fixed labels --
    "Owner Name:", "Address:", "Phone:", "Business Email:", "About us:",
    "Related Searches:" -- rather than exposing these as their own meta
    tags or DOM elements anywhere else on the page (confirmed against
    the raw page source: none of these values appear a second time
    outside this one blob). Splits that blob into a {label: value}
    dict so each field can be read independently instead of re-
    regexing the whole blob per field. "Owner Name" is included in the
    label list even though the caller discards it -- it's not part of
    the common business schema (fields_config.ALL_FIELDS has no such
    field) -- so this list stays a faithful map of what the template
    actually contains, not just what we keep, and a label boundary
    doesn't get missed because it was left out here.

    Label casing is NOT fixed across listings -- confirmed by comparing
    two real listings: one used "About us:" / "Related Searches:", a
    different one used "About Us:" (capital U) instead. A case-
    SENSITIVE match on "About us" silently fails to find that boundary
    on the second listing, and the regex just keeps scanning for the
    next label it DOES recognize -- which, since "About Us"/"Related
    Searches" both went unmatched there, meant the "Business Email:"
    section swallowed everything after it (About-us blurb and
    Related-Searches keywords included) all the way to the end of the
    string, while Description/Keywords silently came back empty. Matches
    case-insensitively and normalizes the captured label back to its
    canonical form from _BAND_DESCRIPTION_LABELS so section lookups
    elsewhere (sections.get("About us"), etc.) keep working regardless
    of which casing this particular listing happened to use."""
    if not description:
        return {}

    canonical_by_lower = {label.lower(): label for label in _BAND_DESCRIPTION_LABELS}
    label_pattern = "|".join(re.escape(l) for l in _BAND_DESCRIPTION_LABELS)
    matches = list(re.finditer(rf"(?:^|\n)({label_pattern}):\n?", description, flags=re.I))

    sections = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(description)
        canonical_label = canonical_by_lower[m.group(1).lower()]
        sections[canonical_label] = clean(description[start:end])
    return sections


def parse_band(url, html):

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name -- the raw <title>/og:title is "X | BAND", so
    # the fixed " | BAND" suffix is stripped off rather than kept as
    # part of the name ----
    og_title = soup.find("meta", property="og:title")
    title_text = og_title["content"] if og_title and og_title.get("content") else None
    if not title_text:
        title_tag = soup.find("title")
        title_text = title_tag.get_text() if title_tag else ""
    business["Business Name"] = clean(re.sub(r"\s*\|\s*BAND\s*$", "", title_text or "", flags=re.I))

    # ---- Everything else lives inside the description blob (see
    # _band_description_sections) -- og:description/twitter:description
    # repeat the same text verbatim, so meta name="description" is read
    # first as the primary source with og:description as a fallback if
    # it's ever missing ----
    desc_tag = soup.find("meta", attrs={"name": "description"})
    description = desc_tag["content"] if desc_tag and desc_tag.get("content") else None
    if not description:
        og_desc = soup.find("meta", property="og:description")
        description = og_desc["content"] if og_desc and og_desc.get("content") else ""

    sections = _band_description_sections(description)

    # ---- Address -- reuses blinx's comma-split + trailing "STATE ZIP"
    # regex helper, since BAND renders addresses in the same loose
    # "Street, City ,ST Zip" shape (stray space before the second comma
    # -- confirmed on the tested listing: "300 Triple Diamond Blvd,
    # Nokomis ,FL 34275") that _split_blinx_address already handles
    # correctly ----
    address = sections.get("Address", "")
    if address:
        street, city, state, zipcode = _split_blinx_address(address)
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Phone ----
    if sections.get("Phone"):
        business["Phone"] = sections["Phone"]

    # ---- Business Email ----
    if sections.get("Business Email"):
        business["Business Email"] = sections["Business Email"]

    # ---- Description ("About us:" section) ----
    if sections.get("About us"):
        business["Description"] = sections["About us"]

    # ---- Keywords ("Related Searches:" is a comma-separated list of
    # search terms -- the closest thing this template has to a
    # Keywords field) ----
    if sections.get("Related Searches"):
        business["Keywords"] = sections["Related Searches"]

    # ---- No genuine Country, Website URL, Hours, Social Media Links,
    # GBP Link, Category, or Photos exist anywhere on this template.
    # og:image is present but is the group's own cover photo, not a
    # business logo, so it's deliberately left out of "Logo" too
    # rather than guessing. ----

    return business


# ==========================================================
# Site parser: americansearch.info
# ==========================================================

def parse_americansearch(url, html):
    """Brilliant-Directories-style template (same underlying platform
    family as chamberofcommerce.com/trueen.com, confirmed by the
    formValidation/Froala/select2 boilerplate shared across all three).
    Unlike those, this template has no LocalBusiness JSON-LD block at
    all -- every field is read from plain schema.org microdata
    attributes and verified CSS selectors in the profile body."""

    soup = BeautifulSoup(html, "lxml")
    business = empty_business()

    # ---- Bot-wall guard ----
    if _looks_blocked(html):
        return business

    # ---- Business Name -- prefer the profile <h1> over og:title, since
    # og:title on this template is "X on AMERICAN SEARCH" (site-branded),
    # not the bare business name ----
    h1 = soup.select_one("div.header-member-name h1.bold")
    if h1:
        business["Business Name"] = clean(h1.get_text())
    if not business["Business Name"]:
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            business["Business Name"] = clean(re.sub(r"\s+on\s+AMERICAN SEARCH\s*$", "", og_title["content"], flags=re.I))

    # ---- Address -- schema.org streetAddress microdata renders as a
    # single "Street, City, ST Zip" string; reuses blinx's comma-split +
    # trailing "STATE ZIP" regex helper, which handles this shape too ----
    addr_tag = soup.select_one('[itemprop="streetAddress"]')
    if addr_tag:
        street, city, state, zipcode = _split_blinx_address(clean(addr_tag.get_text()))
        business["Street"] = street
        business["City"] = city
        business["State"] = state
        business["Zipcode"] = zipcode

    # ---- Country -- not present as its own labeled field anywhere on
    # the page; this template's breadcrumb is always Home > {Country} >
    # {Category} > {business name}, so the second crumb's itemprop="name"
    # span is read as Country rather than leaving it blank ----
    crumbs = [clean(s.get_text()) for s in soup.select('ol.breadcrumb span[itemprop="name"]')]
    if len(crumbs) >= 3:
        business["Country"] = crumbs[1]

    # ---- Category (breadcrumb's third crumb, same list as above --
    # kept as a separate read so a missing/short breadcrumb doesn't
    # silently borrow the wrong crumb for either field) ----
    if len(crumbs) >= 3:
        business["Category"] = crumbs[2]
    if not business["Category"]:
        cat_tag = soup.select_one("span.profile-header-top-category")
        if cat_tag:
            business["Category"] = clean(cat_tag.get_text())

    # ---- Phone (schema.org telephone microdata -- the header's
    # "See Phone Number" reveal button/span is NOT itemprop-tagged and
    # duplicates the same number, so this is the only occurrence that
    # needs reading) ----
    phone_tag = soup.select_one('[itemprop="telephone"]')
    if phone_tag:
        business["Phone"] = clean(phone_tag.get_text())

    # ---- Website URL ----
    site_link = soup.select_one('a.weblink[itemprop="url"]')
    if site_link and site_link.get("href"):
        business["Website URL"] = clean(site_link["href"])

    # ---- Description ("About my Business" free-text block) ----
    about_tag = soup.select_one("span.textarea.textarea-about_me")
    if about_tag:
        business["Description"] = clean(about_tag.get_text())

    # ---- Logo (profile photo shown next to the business name; resolved
    # to an absolute URL since the src is site-relative) ----
    logo_tag = soup.select_one("div.profile-image img.img-rounded")
    if logo_tag and logo_tag.get("src"):
        business["Logo"] = urljoin(url, logo_tag["src"])

    # ---- No genuine Hours section exists on the tested (unclaimed)
    # listing's Overview tab. No genuine Social Media Links or GBP Link
    # exist either -- the page's only Facebook/LinkedIn/X icons are the
    # "Share This Page" buttons, which post the AMERICAN SEARCH listing
    # URL to those networks' share-intent endpoints and belong to the
    # directory, not the business; the only Maps references are a
    # directions search-query link and an Embed API iframe URL carrying
    # the page's own API key, same pattern as chamberofcommerce.com. ----

    return business


# ==========================================================
# Field filtering (fields_config.py-driven)
# ==========================================================

_BUSINESS_TO_CONFIG_FIELD = {"Business Name": "Name"}

_FIELD_EMPTY_DEFAULTS = {
    "Social Media Links": {},
    "Photos": [],
}


def _empty_value_for(field_name):
    default = _FIELD_EMPTY_DEFAULTS.get(field_name, "")
    # Return a fresh copy so callers never share a mutable default.
    return default.copy() if isinstance(default, (dict, list)) else default


def filter_business_fields(business, url):
    """Keeps only the fields listed in fields_config.SOURCE_FIELDS for
    this URL's domain, resetting every other key in `business` to its
    empty value (and dropping any key that isn't part of the common
    schema at all, e.g. one-off extras like "Zeemaps Extra Fields").

    If the domain isn't found in fields_config.SOURCE_FIELDS, `business`
    is returned unchanged.
    """

    source_key = fields_config.detect_source(url)
    if not source_key:
        return business

    allowed = set(fields_config.SOURCE_FIELDS.get(source_key, []))
    if not allowed:
        return business

    filtered = {}
    for field_name, value in business.items():
        config_name = _BUSINESS_TO_CONFIG_FIELD.get(field_name, field_name)

        if config_name in allowed:
            filtered[field_name] = value
        else:
            filtered[field_name] = _empty_value_for(field_name)

    return filtered


# ==========================================================
# Dispatcher
# ==========================================================

# domain -> (fetch method, parser function)
# fetch method "api" means the parser makes its own requests calls and
# doesn't need pre-fetched HTML at all.
SITE_PARSERS = {
    "nearfinderus.com": ("requests", parse_nearfinderus),
    "smallbusinessusa.com": ("playwright", parse_smallbusinessusa),
    "zeemaps.com": ("api", parse_zeemaps),
    "callupcontact.com": ("requests", parse_callupcontact),
    "zumvu.com": ("playwright", parse_zumvu),
    # blinx.biz's business record loads via a client-side XHR call made
    # AFTER the initial page load (confirmed via the browser Network
    # tab -- it's not in the raw HTML or __NEXT_DATA__ that a plain
    # requests.get() would see), so this needs Playwright to let the
    # page hydrate and populate the DOM before we read it.
    "blinx.biz": ("playwright", parse_blinx),
    "place123.net": ("requests", parse_place123),
    "freelistingusa.com": ("requests", parse_freelistingusa),
    "askmap.net": ("requests", parse_askmap),
    "earthmom.org": ("requests", parse_earthmom),
    "gravitysplash.com": ("requests", parse_gravitysplash),
    "webforcompany.com": ("requests", parse_webforcompany),
    "provenexpert.com": ("requests", parse_provenexpert),
    "zipleaf.us": ("requests", parse_zipleaf),
    "cataloxy.us": ("requests", parse_cataloxy),
    "fyple.com": ("requests", parse_fyple),
    "merchantcircle.com": ("requests", parse_merchantcircle),
    "globalbusinessdirectory.us": ("requests", parse_globalbusinessdirectory),
    "chamberofcommerce.com": ("requests", parse_chamberofcommerce),
    "trueen.com": ("requests", parse_trueen),
    "citysquares.com": ("requests", parse_citysquares),
    "b2bco.com": ("requests", parse_b2bco),
    # The Business Email link is inserted into the DOM by an inline
    # <script> rather than being present in the raw server-rendered
    # HTML (confirmed via DevTools: a plain requests.get() never sees
    # the resulting <a href="mailto:..."> at all), so this needs
    # Playwright to let that script run before we read the page.
    "find-us-here.com": ("playwright", parse_findushere),
    # Same platform family as find-us-here.com; fetched via Playwright
    # up front rather than discovering the same JS-injected-email issue
    # a second time -- see parse_azbusinessfinder for details.
    "a-zbusinessfinder.com": ("playwright", parse_azbusinessfinder),
    "cybo.com": ("requests", parse_cybo),
    "linkcentre.com": ("requests", parse_linkcentre),
    # All the fields BAND exposes for a group's business record (name,
    # address, phone, email, about-us blurb, related-search keywords)
    # are already baked into the server-rendered <meta name="description">
    # (and its og:/twitter: duplicates) in the raw HTML, so no
    # JS-rendering wait is needed even though the rest of the page is a
    # client-hydrated app shell -- see parse_band for details.
    "band.us": ("requests", parse_band),
    "americansearch.info": ("requests", parse_americansearch),
}


def extract_business(url, worker_path="playwright_worker.py"):

    domain = urlparse(url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]

    matched = next((k for k in SITE_PARSERS if k in domain), None)

    if matched:
        method, parser = SITE_PARSERS[matched]
    else:
        method, parser = "requests", parse_generic

    if method == "api":
        # Parser drives its own requests calls; no HTML fetch needed.
        business = parser(url)
        if isinstance(business, list):
            return [filter_business_fields(record, url) for record in business]
        return filter_business_fields(business, url)

    if method == "requests":
        try:
            html = fetch_via_requests(url)
            blocked = _looks_blocked(html)
        except requests.exceptions.RequestException:
            # Outright HTTP failure (401/403/etc, not just a 200 with
            # bot-check text) -- also worth a Playwright retry rather
            # than failing the whole extraction.
            html = None
            blocked = True

        if blocked:
            # Unmapped/blocked site -- retry via Playwright automatically
            html = fetch_via_playwright(url, worker_path=worker_path)
    else:
        html = fetch_via_playwright(url, worker_path=worker_path)

    # A Cloudflare error page can come back as a "successful" fetch
    # (see _looks_like_cloudflare_error above) -- catch it here, before
    # handing it to the parser, rather than silently returning an
    # empty/garbage record.
    if _looks_like_cloudflare_error(html):
        raise RuntimeError(
            f"Fetch for {url} returned a Cloudflare error page "
            f"(origin server appears to be down or unreachable), "
            f"not the real page content."
        )

    business = parser(url, html)

    # zeemaps' "api" branch above already returns early, but its parser
    # can still return a list from other call paths (kept defensive here).
    if isinstance(business, list):
        return [filter_business_fields(record, url) for record in business]

    return filter_business_fields(business, url)


if __name__ == "__main__":

    urls = sys.argv[1:] or [
        "https://us.enrollbusiness.com/BusinessProfile/7823462/HAQQ-Legal-AI-Dover-DE-19901/Home",
        "https://nearfinderus.com/en/business/fl/nokomis/category_water-damage-restoration/wrightway-emergency-services_21911037+0.html",
        "https://smallbusinessusa.com/listing/wrightway-emergency-services-6a1ac1527f9a5.html",
        "https://www.zeemaps.com/map/ombxa?group=7085104",
        "https://www.callupcontact.com/b/businessprofile/WrightWay_Emergency_Services/10109082",
        "https://www.zumvu.com/haqqlegalai/",
        "https://www.blinx.biz/haqq-legal-ai",
        "http://www.place123.net/place/haqq-legal-ai---united-states",
        "https://www.freelistingusa.com/listings/haqq-legal-ai",
        "https://askmap.net/location/7831489/united-states/haqq-legal-ai",
        "https://www.zipleaf.us/Companies/Focal",
        "https://www.chamberofcommerce.com/business-directory/washington/spokane-valley/home-health-care-service/2023027461-loving-neighbor-home-care-llc",
        "https://trueen.com/business/listing/focal-newark/752375",
        "https://www.cybo.com/US-biz/wrightway-emergency-services_30",
        "https://www.linkcentre.com/profile/joshuareyno45/",

    ]

    for url in urls:
        print("=" * 100)
        print(f"URL: {url}")
        print("=" * 100)

        try:
            data = extract_business(url)
        except Exception as e:
            print(f"ERROR extracting {url}: {e}")
            print("-" * 80)
            continue

        # zeemaps parser can return a list if a map has multiple markers
        records = data if isinstance(data, list) else [data]

        for record in records:
            for key, value in record.items():
                print(f"{key}:")
                print(value)
                print("-" * 80)
