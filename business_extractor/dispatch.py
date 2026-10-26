"""
Domain -> parser dispatch table and the extract_business() entry point.

This is the only place that needs to change when a new site parser is
added: import the new parsers.<module> and add one line to SITE_PARSERS.
"""

import os
import sys
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from . import parsers
from .parsers import tradeford as _tradeford  # registered here, no parsers/__init__.py edit needed
from .parsers import acompio as _acompio  # same: no parsers/__init__.py edit needed
from .common import fetch_via_requests, fetch_via_curl_cffi, filter_business_fields
from .common import fetch_via_scraperapi_ladder, FetchError, summarize_attempts
from .common import _looks_blocked, _looks_like_cloudflare_error

# NOTE (carried over from the old single-file extractor.py): parse_generic
# is referenced below for unmatched domains but is not defined anywhere in
# this codebase. That was already the case before this split -- it will
# raise a NameError if extract_business() is ever called on a URL that
# doesn't match any entry in SITE_PARSERS. Add a real parse_generic (e.g.
# in parsers/generic.py) or remove that fallback branch.


SITE_PARSERS = {
    "letsknowit.com": ("requests", parsers.letsknowit.parse_letsknowit),
    "metriteweb.com": ("requests", parsers.metriteweb.parse_metriteweb),
    "qdexx.com": ("requests", parsers.qdexx.parse_qdexx),
    "dbesearch.com": ("requests", parsers.dbesearch.parse_dbesearch),
    "locuul.com": ("requests", parsers.locuul.parse_locuul),
    "bpublic.com": ("requests", parsers.bpublic.parse_bpublic),
    "smallbusinessusa.com": ("playwright", parsers.smallbusinessusa.parse_smallbusinessusa),
    "zeemaps.com": ("api", parsers.zeemaps.parse_zeemaps),
    "callupcontact.com": ("requests", parsers.callupcontact.parse_callupcontact),
    "zumvu.com": ("playwright", parsers.zumvu.parse_zumvu),
    "blinx.biz": ("playwright", parsers.blinx_biz.parse_blinx),
    "place123.net": ("requests", parsers.place123.parse_place123),
    "freelistingusa.com": ("requests", parsers.freelistingusa.parse_freelistingusa),
    "askmap.net": ("requests", parsers.askmap.parse_askmap),
    "earthmom.org": ("requests", parsers.earthmom.parse_earthmom),
    "gravitysplash.com": ("requests", parsers.gravitysplash.parse_gravitysplash),
    "webforcompany.com": ("requests", parsers.webforcompany.parse_webforcompany),
    "provenexpert.com": ("requests", parsers.provenexpert.parse_provenexpert),
    "zipleaf.us": ("requests", parsers.zipleaf.parse_zipleaf),
    "cataloxy.us": ("curl_cffi", parsers.cataloxy.parse_cataloxy),
    # Same site/markup on its .net domain (e.g. nsw-state.cataloxy.net).
    "cataloxy.net": ("curl_cffi", parsers.cataloxy.parse_cataloxy),
    "fyple.com": ("requests", parsers.fyple.parse_fyple),
    "merchantcircle.com": ("requests", parsers.merchantcircle.parse_merchantcircle),
    "globalbusinessdirectory.us": ("requests", parsers.globalbusinessdirectory.parse_globalbusinessdirectory),
    "listings.globalbusinessdirectory.us": ("requests", parsers.listings_globalbusinessdirectory.parse_listings_globalbusinessdirectory),
    "usa.globalbusinessdirectory.us": ("requests", parsers.listings_globalbusinessdirectory.parse_listings_globalbusinessdirectory),
    "cities.globalbusinessdirectory.us": ("requests", parsers.listings_globalbusinessdirectory.parse_listings_globalbusinessdirectory),
    "local.globalbusinessdirectory.us": ("requests", parsers.listings_globalbusinessdirectory.parse_listings_globalbusinessdirectory),
    "blogs.globalbusinessdirectory.us": ("requests", parsers.blogs_globalbusinessdirectory.parse_blogs_globalbusinessdirectory),
    "chamberofcommerce.com": ("requests", parsers.chamberofcommerce.parse_chamberofcommerce),
    "trueen.com": ("requests", parsers.trueen.parse_trueen),
    "citysquares.com": ("requests", parsers.citysquares.parse_citysquares),
    "b2bco.com": ("requests", parsers.b2bco.parse_b2bco),
    "find-us-here.com": ("playwright", parsers.find_us_here.parse_findushere),
    "a-zbusinessfinder.com": ("playwright", parsers.a_zbusinessfinder.parse_azbusinessfinder),
    "cybo.com": ("requests", parsers.cybo.parse_cybo),
    "linkcentre.com": ("requests", parsers.linkcentre.parse_linkcentre),
    "band.us": ("requests", parsers.band.parse_band),
    "americansearch.info": ("requests", parsers.americansearch.parse_americansearch),
    "n49.com": ("requests", parsers.n49.parse_n49),
    # TradeFord company pages (us./www./... .tradeford.com): server-rendered, plain requests is enough.
    "tradeford.com": ("requests", _tradeford.parse_tradeford),
    # Acompio Network (acompio.us): server-rendered, listing data is in JSON-LD.
    "acompio.us": ("requests", _acompio.parse_acompio),
    "bizhwy.com": ("requests", parsers.bizhwy.parse_bizhwy),
    "yplocal.com": ("requests", parsers.yplocal.parse_yplocal),
    "golocalezservices.com": ("requests", parsers.golocalezservices.parse_golocalezservices),
    "findabusinesspro.com": ("requests", parsers.findabusinesspro.parse_findabusinesspro),
    "globeconnected.com": ("requests", parsers.globeconnected.parse_globeconnected),
    "whatsyourhours.com": ("requests", parsers.whatsyourhours.parse_whatsyourhours),
    "milestones.business": ("requests", parsers.milestones.parse_milestones),
    "iformative.com": ("requests", parsers.iformative.parse_iformative),
    "thebusinessminded.com": ("requests", parsers.thebusinessminded.parse_thebusinessminded),
    "cleansway.com": ("requests", parsers.cleansway.parse_cleansway),
    "preferredprofessionals.com": ("requests", parsers.preferredprofessionals.parse_preferredprofessionals),
    "bestdealfinder.com": ("requests", parsers.bestdealfinder.parse_bestdealfinder),
    "911getit.com": ("requests", parsers.x911getit.parse_911getit),
    "touchafro.com": ("requests", parsers.touchafro.parse_touchafro),
    "supplyautonomy.com": ("requests", parsers.supplyautonomy.parse_supplyautonomy),
    # Only reachable from some countries (turn a system-wide VPN on) and it
    # 403s headless browsers, so it opens a visible Chrome window
    # (HEADED_DOMAINS in playwright_worker.py).
    # Cloudflare challenge blocks requests, curl_cffi and headless Chrome; only a
    # visible Chrome window passes (HEADED_DOMAINS in playwright_worker.py).
    "manta.com": ("playwright", parsers.manta.parse_manta),
    # Cloudflare "verify you are human" blocks requests/headless; a visible Chrome passes.
    "crunchbase.com": ("playwright", parsers.crunchbase.parse_crunchbase),
    # Hubbiz listings live on <slug>.hub.biz; needs the visible-browser verification.
    "hub.biz": ("playwright", parsers.hub_biz.parse_hubbiz),
    "surfyourtown.com": ("playwright", parsers.surfyourtown.parse_surfyourtown),
    "mybusinessplaces.com": ("requests", parsers.mybusinessplaces.parse_mybusinessplaces),
    "local-biz.directory": ("requests", parsers.local_biz.parse_localbizdirectory),
    "vetslist.com": ("requests", parsers.vetslist.parse_vetslist),
    "vymaps.com": ("requests", parsers.vymaps.parse_vymaps),
    "wireanium.com": ("requests", parsers.wireanium.parse_wireanium),
    "closelocation.com": ("requests", parsers.closelocation.parse_closelocation),
    "trustburn.com": ("requests", parsers.trustburn.parse_trustburn),
    "searchmypro.com": ("requests", parsers.searchmypro.parse_searchmypro),
    "yourbizlistings.com": ("requests", parsers.yourbizlistings.parse_yourbizlistings),
    "bulkpostads.com": ("requests", parsers.bulkpostads.parse_bulkpostads),
    "meetyourmarkets.com": ("requests", parsers.meetyourmarkets.parse_meetyourmarkets),
    # bulkadspost.com is the sister listings site to bulkpostads.com --
    # same GeoDirectory theme/markup (verified against a live listing
    # page), so it reuses parsers.bulkpostads.parse_bulkpostads rather than duplicating it.
    "bulkadspost.com": ("requests", parsers.bulkpostads.parse_bulkpostads),
    "bizcoupon.directory": ("requests", parsers.bizcoupon.parse_bizcoupon),
    "countrypwr.com": ("requests", parsers.countrypwr.parse_countrypwr),
    "bizmakersamerica.org": ("requests", parsers.meetyourmarkets.parse_bizmakersamerica),
    "homify.com": ("requests", parsers.homify.parse_homify),
    "cake.me": ("requests", parsers.cake.parse_cake),
    "americasmallbiz.com": ("requests", parsers.americasmallbiz.parse_americasmallbiz),
    "bizforgeusa.com": ("requests", parsers.bizforgeusa.parse_bizforgeusa),
    "bigbizstuff.com": ("requests", parsers.bigbizstuff.parse_bigbizstuff),
    "biz411.org": ("requests", parsers.biz411.parse_biz411),
    "bizbangboom.com": ("requests", parsers.bizbangboom.parse_bizbangboom),
    "bizbuildboom.com": ("requests", parsers.bizbuildboom.parse_bizbuildboom),
    "bizlinkbuilder.com": ("requests", parsers.bizlinkbuilder.parse_bizlinkbuilder),
    "blogbangboom.com": ("requests", parsers.blogbangboom.parse_blogbangboom),
    "homepros411.com": ("requests", parsers.homepros411.parse_homepros411),
    "selfemployedai.com": ("requests", parsers.selfemployedai.parse_selfemployedai),
    "smallbizamerica.org": ("requests", parsers.smallbizamerica.parse_smallbizamerica),
    "smallbizblog.net": ("requests", parsers.smallbizblog.parse_smallbizblog),
    "perrysplacepromotions.org": ("requests", parsers.perrysplacepromotions.parse_perrysplacepromotions),
    "provenemployer.com": ("requests", parsers.provenemployer.parse_provenemployer),
    "biztobiz.org": ("requests", parsers.biztobiz.parse_biztobiz),
    "bizmaker.org": ("requests", parsers.bizmaker.parse_bizmaker),
    # "requests" first; extract_business() automatically retries through
    # Playwright if Hotfrog serves a bot-check page or rejects the request.
    "hotfrog.com": ("requests", parsers.hotfrog.parse_hotfrog),
    "thelocal.directory": ("requests", parsers.thelocal_directory.parse_thelocal_directory),
    # Cloudflare blocks plain requests here ("Attention Required" 403), so use
    # the real-Chrome Playwright path (REAL_CHROME_DOMAINS in playwright_worker.py).
    "nextbizmaker.com": ("playwright", parsers.nextbizmaker.parse_nextbizmaker),
    "nextbizthing.com": ("requests", parsers.nextbizthing.parse_nextbizthing),
    # EnrollBusiness (au./us./uk. ... sub-domains) is server-rendered, so plain
    # requests is enough; blocked pages retry through Playwright automatically.
    "enrollbusiness.com": ("requests", parsers.enrollbusiness.parse_enrollbusiness),
    "directoryup.com": ("requests", parsers.directoryup.parse_directoryup),
    "addonbiz.com": ("requests", parsers.addonbiz.parse_addonbiz),
    "zipcode2business.com": ("requests", parsers.zipcode2business.parse_zipcode2business),
    "golocalads.com": ("requests", parsers.golocalads.parse_golocalads),
    "couponler.com": ("requests", parsers.couponler.parse_couponler),
    # techdirectory.io answers plain requests (and the default headless
    # Chromium) with "403 Forbidden", so go straight to Playwright, which
    # uses the installed Chrome for this domain (REAL_CHROME_DOMAINS in
    # playwright_worker.py).
    "techdirectory.io": ("playwright", parsers.techdirectory.parse_techdirectory),
    # 757pages.com is a Brilliant Directories site (same layout family as
    # techdirectory.io); "requests" first, automatic Playwright retry if blocked.
    "757pages.com": ("requests", parsers.pages757.parse_pages757),
    # Brownbook is Next.js; the full record is embedded in the HTML
    # (JSON-LD + flight data), so plain requests is enough.
    "brownbook.net": ("requests", parsers.brownbook.parse_brownbook),
    # Yelp blocks plain requests, so go straight to Playwright.
    "yelp.com": ("playwright", parsers.yelp.parse_yelp),
    # Medium business-listing posts. Cloudflare 403s plain requests, but a
    # Chrome-fingerprint fetch (curl_cffi) passes; blocked pages escalate to
    # Playwright automatically.
    "medium.com": ("curl_cffi", parsers.medium.parse_medium),
    # Quora profile bios are inlined in the page as GraphQL JSON; same
    # Chrome-fingerprint fetch with automatic Playwright fallback.
    "quora.com": ("curl_cffi", parsers.quora.parse_quora),
    # Ecity (eDirectory platform): address/name/description are in JSON-LD,
    # server-rendered, so plain requests is enough (auto Playwright retry if blocked).
    "ecitybiz.com": ("requests", parsers.ecitybiz.parse_ecitybiz),
    # BatchGeo map pages used as listings. Cloudflare 403s plain requests; the
    # Chrome-fingerprint fetch passes (verified with try_methods.py), with
    # automatic Playwright fallback if blocked.
    "batchgeo.com": ("curl_cffi", parsers.batchgeo.parse_batchgeo),
    # NearFinderUS: Cloudflare blocks plain requests/curl_cffi, so go through
    # Playwright (falls back to ScraperAPI if SCRAPERAPI_KEY is set).
    "nearfinderus.com": ("playwright", parsers.nearfinderus.parse_nearfinderus),
    # Foursquare (also app.foursquare.com): visible Chrome window, see
    # HEADED_DOMAINS in playwright_worker.py.
    "foursquare.com": ("playwright", parsers.foursquare.parse_foursquare),
    # Biass business sites (<slug>.biass.us). Sits behind a
    # verification step, so it opens a visible Chrome window
    # (HEADED_DOMAINS in playwright_worker.py).
    "biass.us": ("playwright", parsers.biass.parse_biass),
    # directory9.net (eDirectory platform). Sits behind a verification step, so it
    # opens a visible Chrome window (HEADED_DOMAINS in playwright_worker.py).
    "directory9.net": ("playwright", parsers.directory9.parse_directory9),
}


# Sites that are simply slow to load. Per-site timeouts in seconds (for the
# simple fetch and for the ScraperAPI fallback) so these don't time out while
# every other site keeps the quick defaults.
SLOW_SITES = {
    "letsknowit.com": {"requests_timeout": 90, "scraperapi_timeout": 120},
    # Yelp loads slowly and its bot checks add delay.
    "yelp.com": {"requests_timeout": 90, "scraperapi_timeout": 120},
    # Couponler is server-rendered but loads many ad/analytics scripts, so
    # the default 30s requests / 45s Playwright budgets can time out.
    "couponler.com": {"requests_timeout": 90, "scraperapi_timeout": 120},
    "nearfinderus.com": {"requests_timeout": 90, "scraperapi_timeout": 120},
    "brownbook.net": {"requests_timeout": 90, "scraperapi_timeout": 120},
    "enrollbusiness.com": {"requests_timeout": 90, "scraperapi_timeout": 120},
    "foursquare.com": {"requests_timeout": 90, "scraperapi_timeout": 120},
    "surfyourtown.com": {"requests_timeout": 90, "scraperapi_timeout": 120},
    "biass.us": {"requests_timeout": 90, "scraperapi_timeout": 120},
    "directory9.net": {"requests_timeout": 90, "scraperapi_timeout": 120},
    # Extremely slow origin: ScraperAPI gives up after ~1 min (HTTP 500 on every
    # tier), so the direct fetch needs a long read timeout.
    "fyple.com": {"requests_timeout": 240, "scraperapi_timeout": 120},
}


# Sites that are ALWAYS fetched with one specific ScraperAPI step, skipping the
# free fetch and the simple / premium / ultra-premium ladder entirely. Use this
# for a site where every cheaper step is known to fail (each failed step still
# costs credits). One call, no escalation, no render retry afterwards.
#   domain -> (label shown in debug info, extra ScraperAPI params)
FORCED_SCRAPERAPI = {
    "zumvu.com": ("ScraperAPI premium + render", {"premium": "true", "render": "true"}),
}


def _fetch_forced_scraperapi(url, label, extra, timeout):
    """Exactly ONE ScraperAPI request with the given params. Returns
    (html, attempts); raises FetchError (with attempts) on failure."""
    import time as _time

    key = os.environ.get("SCRAPERAPI_KEY")
    if not key:
        raise FetchError(f"SCRAPERAPI_KEY is not set, but this site is configured "
                         f"to always use {label}. This listing was NOT checked.", [])
    attempt = {"step": label, "ok": False, "status": None,
               "credits": 0, "seconds": 0.0, "note": ""}
    started = _time.time()
    try:
        response = requests.get(
            "https://api.scraperapi.com/",
            params={"api_key": key, "url": url, **extra},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        attempt["seconds"] = round(_time.time() - started, 1)
        attempt["note"] = type(e).__name__
        raise FetchError(f"{label} failed for {url}: {type(e).__name__}. "
                         f"This listing was NOT checked.", [attempt]) from e

    attempt["seconds"] = round(_time.time() - started, 1)
    attempt["status"] = response.status_code
    try:
        attempt["credits"] = int(response.headers.get("sa-credit-cost") or 0)
    except ValueError:
        attempt["credits"] = 0

    body = response.text or ""
    if response.status_code >= 400:
        attempt["note"] = f"HTTP {response.status_code} {body.strip()[:200]}".strip()
        raise FetchError(f"{label} failed for {url}: {attempt['note']}. "
                         f"This listing was NOT checked.", [attempt])
    if not body.strip():
        attempt["note"] = "empty response"
        raise FetchError(f"{label} returned an empty page for {url}. "
                         f"This listing was NOT checked.", [attempt])
    if _looks_blocked(body):
        attempt["note"] = "captcha / verification page"
        raise FetchError(f"{label} returned a captcha / verification page for {url}. "
                         f"This listing was NOT checked.", [attempt])
    attempt["ok"] = True
    return body, [attempt]


def _fetch_simple(url, method, timeout):
    """Cheap first attempt: plain `requests` (or curl_cffi's Chrome-fingerprint
    fetch for sites that reject `requests`). Returns (html, "") on success, or
    (None, reason) when the fetch failed or the site served a captcha /
    verification / bot-check page -- the caller then falls back to ScraperAPI.
    """
    try:
        if method == "requests":
            html = fetch_via_requests(url, timeout=timeout)
        else:
            try:
                # challenge_ok=False: a 403 means "blocked", so raise and let
                # the caller escalate instead of parsing the challenge page.
                html = fetch_via_curl_cffi(url, timeout=timeout, challenge_ok=False)
            except ImportError:
                html = fetch_via_requests(url, timeout=timeout)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"
    if not html or not html.strip():
        return None, "empty response"
    if _looks_blocked(html):
        return None, "captcha / verification page"
    return html, ""


def _fetch_simple_then_scraperapi(url, method, timeout, api_timeout):
    """Fetch the page, trying cheapest first, for every site:
         1. simple fetch (requests, or curl_cffi's Chrome fingerprint)   0 credits
         2. ScraperAPI simple
         3. ScraperAPI premium
         4. ScraperAPI ultra-premium
    Each step runs only if the previous one failed or returned a captcha /
    verification page. No local browser is ever launched.

    Returns (html, attempts). On failure raises FetchError carrying every
    attempt (and the credits already spent)."""
    simple_label = "Simple fetch (requests)" if method == "requests" else "Simple fetch (curl_cffi)"
    html, reason = _fetch_simple(url, method, timeout)
    if html is not None:
        return html, [{"step": simple_label, "ok": True, "status": 200,
                       "credits": 0, "seconds": 0.0, "note": ""}]
    attempts = [{"step": simple_label, "ok": False, "status": None,
                 "credits": 0, "seconds": 0.0, "note": reason}]
    if not os.environ.get("SCRAPERAPI_KEY"):
        raise FetchError(
            f"Simple fetch failed for {url} ({reason}) and SCRAPERAPI_KEY is "
            f"not set, so there is no fallback. This listing was NOT checked.",
            attempts,
        )
    print(f"[fetch] {url}: simple fetch failed ({reason}); trying ScraperAPI",
          file=sys.stderr)
    try:
        html, api_attempts = fetch_via_scraperapi_ladder(
            url, timeout=api_timeout, validate=_looks_blocked
        )
    except FetchError as api_err:
        attempts += api_err.attempts
        raise FetchError(
            f"Could not fetch {url}: simple fetch failed ({reason}); "
            f"{str(api_err)[:1200]}. This listing was NOT checked.",
            attempts,
        ) from api_err
    return html, attempts + api_attempts


def _strip_challenge_params(url):
    """Drop Cloudflare challenge tokens (e.g. ?__cf_chl_rt_tk=...) that get copied
    along when a URL is pasted from a browser that just passed a challenge."""
    p = urlparse(url)
    if "__cf_chl" not in p.query:
        return url
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not k.startswith("__cf_chl")]
    return urlunparse(p._replace(query=urlencode(q)))


def extract_business(url):

    url = _strip_challenge_params(url)

    domain = urlparse(url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]

    candidates = [k for k in SITE_PARSERS if k in domain]
    matched = max(candidates, key=len) if candidates else None

    if matched:
        method, parser = SITE_PARSERS[matched]
    else:
        method, parser = "requests", parse_generic

    slow = SLOW_SITES.get(matched, {})
    req_timeout = slow.get("requests_timeout", 30)
    api_timeout = slow.get("scraperapi_timeout", 90)

    if method == "api":
        # Parser drives its own requests calls; no HTML fetch needed.
        business = parser(url)
        info = {"method": "Direct API (no page fetch)", "credits": 0, "attempts": []}
        if isinstance(business, list):
            return [_with_fetch(filter_business_fields(record, url), info) for record in business]
        return _with_fetch(filter_business_fields(business, url), info)

    # Every site follows the same path, cheapest first, moving on only when
    # the previous step failed or returned a captcha / verification page:
    #   1. simple fetch (requests, or curl_cffi for sites that reject requests)
    #   2. ScraperAPI simple -> 3. ScraperAPI premium -> 4. ScraperAPI ultra-premium
    # Entries tagged "playwright" in SITE_PARSERS are treated as "curl_cffi"
    # here; no local browser is launched.
    forced = FORCED_SCRAPERAPI.get(matched)
    if forced:
        label, extra = forced
        html, attempts = _fetch_forced_scraperapi(
            url, label, extra, max(api_timeout, 120)
        )
    else:
        simple_method = "requests" if method == "requests" else "curl_cffi"
        html, attempts = _fetch_simple_then_scraperapi(url, simple_method, req_timeout, api_timeout)
    try:
        business = _parse_fetched(url, html, parser, api_timeout, attempts,
                                  already_rendered=bool(forced))
    except Exception as e:
        # Keep the fetch record (credits already spent) on the error.
        e.fetch_info = summarize_attempts(attempts)
        raise
    info = summarize_attempts(attempts)
    if isinstance(business, list):
        return [_with_fetch(record, info) for record in business]
    return _with_fetch(business, info)


def _with_fetch(record, info):
    """Attach how the page was fetched (approach, credits, attempts)."""
    if isinstance(record, dict):
        record["_fetch"] = info
    return record


_LISTING_KEYS = ("Business Name", "Street", "Phone", "Website URL", "Description")


def _has_listing_data(business):
    return isinstance(business, dict) and any(business.get(k) for k in _LISTING_KEYS)


def _describe_page(html):
    """Short fingerprint of what was actually fetched, for error messages."""
    import re as _re
    m = _re.search(r"<title[^>]*>(.*?)</title>", html or "", _re.I | _re.S)
    title = _re.sub(r"\s+", " ", m.group(1)).strip()[:120] if m else "(no <title>)"
    text = _re.sub(r"<(script|style)\b.*?</\1>", " ", html or "", flags=_re.I | _re.S)
    text = _re.sub(r"<[^>]+>", " ", text)
    text = _re.sub(r"\s+", " ", text).strip()[:200]
    return f"{len(html or '')} bytes, title: {title!r}, text starts: {text!r}"


def _parse_fetched(url, html, parser, api_timeout, attempts, already_rendered=False):
    """Everything after the fetch: optional rendered re-fetch, sanity
    checks, parsing, field filtering. Extra ScraperAPI attempts (render
    re-fetch) are appended to `attempts` so their credits are counted."""

    # Parsers can opt in to a rendered re-fetch when the plain HTML is missing
    # JS-loaded content (e.g. ProvenExpert opening hours) by exposing a
    # needs_render(html) -> bool attribute. The rendered copy comes from
    # ScraperAPI (render=true), and only those pages pay that cost.
    needs_render = getattr(parser, "needs_render", None)
    if needs_render is not None:
        try:
            wants_render = needs_render(html)
        except Exception:
            wants_render = False
        if wants_render and os.environ.get("SCRAPERAPI_KEY"):
            try:
                rendered, render_attempts = fetch_via_scraperapi_ladder(
                    url, timeout=api_timeout, render=True, validate=_looks_blocked
                )
                attempts.extend(render_attempts)
                html = rendered
                already_rendered = True
                if needs_render(rendered):
                    print(f"[render] {url}: page rendered but JS content "
                          f"still empty", file=sys.stderr)
            except Exception as e:
                attempts.extend(getattr(e, "attempts", []))
                # Keep the plain HTML so the other fields still parse, but
                # say why the rendered fetch didn't happen.
                print(f"[render] {url}: ScraperAPI render failed: {e}",
                      file=sys.stderr)
        elif wants_render:
            print(f"[render] {url}: needs a rendered page but SCRAPERAPI_KEY "
                  f"is not set; using plain HTML", file=sys.stderr)

    if _looks_like_cloudflare_error(html):
        raise RuntimeError(
            f"Fetch for {url} returned a Cloudflare error page "
            f"(origin server appears to be down or unreachable), "
            f"not the real page content."
        )

    business = parser(url, html)

    # Several parsers return an empty record when the page looks like a
    # bot-wall/captcha. Surface that as an error rather than a green tick
    # with every field blank, which looks like "the listing has no data".
    if (
        isinstance(business, dict)
        and _looks_blocked(html)
        and not any(v for v in business.values())
    ):
        raise RuntimeError(
            f"{url} returned a bot-check / captcha page instead of the "
            f"listing, so nothing could be extracted. Try again later or "
            f"from another network."
        )

    # A record with no name, address, phone, website or description means the
    # page loaded but no listing data was found on it. The cheap fetch often
    # returns a stripped / placeholder page (e.g. cataloxy served through
    # ScraperAPI "simple"), so before giving up, re-fetch ONCE through
    # ScraperAPI with JavaScript rendering and parse that copy instead.
    if isinstance(business, dict) and not _has_listing_data(business):
        first_look = _describe_page(html)
        if not already_rendered and os.environ.get("SCRAPERAPI_KEY"):
            print(f"[fetch] {url}: no listing data in fetched page ({first_look}); "
                  f"retrying with ScraperAPI render", file=sys.stderr)
            try:
                rendered, render_attempts = fetch_via_scraperapi_ladder(
                    url, timeout=api_timeout, render=True, validate=_looks_blocked
                )
                attempts.extend(render_attempts)
                rendered_business = parser(url, rendered)
                if _has_listing_data(rendered_business):
                    business, html = rendered_business, rendered
                else:
                    first_look += f" | rendered copy: {_describe_page(rendered)}"
            except Exception as e:
                attempts.extend(getattr(e, "attempts", []))
                first_look += f" | render retry failed: {str(e)[:200]}"

        if not _has_listing_data(business):
            raise RuntimeError(
                f"Fetched {url} but found no listing data on the page (no name, "
                f"address, phone, website or description). The listing may not "
                f"exist or was removed, the site layout may have changed, or the "
                f"content is loaded by JavaScript. This listing was NOT checked. "
                f"Page received: {first_look}"
            )

    if isinstance(business, list):
        return [filter_business_fields(record, url) for record in business]

    return filter_business_fields(business, url)