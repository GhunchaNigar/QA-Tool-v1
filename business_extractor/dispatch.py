"""
Domain -> parser dispatch table and the extract_business() entry point.

This is the only place that needs to change when a new site parser is
added: import the new parsers.<module> and add one line to SITE_PARSERS.
"""

import sys
from urllib.parse import urlparse

import requests

from . import parsers
from .common import fetch_via_requests, fetch_via_playwright, filter_business_fields
from .common import fetch_via_scraperapi
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
    "cataloxy.us": ("requests", parsers.cataloxy.parse_cataloxy),
    # Same site/markup on its .net domain (e.g. nsw-state.cataloxy.net).
    "cataloxy.net": ("requests", parsers.cataloxy.parse_cataloxy),
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
    "nextbizmaker.com": ("requests", parsers.nextbizmaker.parse_nextbizmaker),
    "nextbizthing.com": ("requests", parsers.nextbizthing.parse_nextbizthing),
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
    # Yelp blocks plain requests, so go straight to Playwright.
    "yelp.com": ("playwright", parsers.yelp.parse_yelp),
}


# Sites that 403 cloud/datacenter IPs regardless of browser. If Playwright
# fails on these, the fetch is retried through ScraperAPI (needs the
# SCRAPERAPI_KEY env var / Streamlit secret).
PROXY_FALLBACK_DOMAINS = {"techdirectory.io"}


# Sites that are simply slow to load. Per-site timeouts (seconds for
# requests, milliseconds for Playwright) so these don't time out while
# every other site keeps the quick defaults.
SLOW_SITES = {
    "letsknowit.com": {"requests_timeout": 90, "playwright_ms": 120000},
    # Yelp loads slowly and its bot checks add delay; the default 45s
    # budget (75s outer limit) timed out before the worker could report.
    "yelp.com": {"requests_timeout": 90, "playwright_ms": 120000},
    # Couponler is server-rendered but loads many ad/analytics scripts, so
    # the default 30s requests / 45s Playwright budgets can time out.
    "couponler.com": {"requests_timeout": 90, "playwright_ms": 120000},
}


def extract_business(url, worker_path="playwright_worker.py"):

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
    pw_kwargs = {"timeout_ms": slow["playwright_ms"]} if "playwright_ms" in slow else {}

    if method == "api":
        # Parser drives its own requests calls; no HTML fetch needed.
        business = parser(url)
        if isinstance(business, list):
            return [filter_business_fields(record, url) for record in business]
        return filter_business_fields(business, url)

    if method == "requests":
        try:
            html = fetch_via_requests(url, timeout=req_timeout)
            blocked = _looks_blocked(html)
        except requests.exceptions.RequestException:
            html = None
            blocked = True

        if blocked:
            # Unmapped/blocked site -- retry via Playwright automatically
            html = fetch_via_playwright(url, worker_path=worker_path, **pw_kwargs)
        else:
            # Parsers can opt in to a rendered re-fetch when the plain HTML
            # is missing JS-loaded content (e.g. ProvenExpert opening
            # hours) by exposing a needs_render(html) -> bool attribute.
            # Only those pages pay the Playwright cost.
            needs_render = getattr(parser, "needs_render", None)
            if needs_render is not None:
                try:
                    wants_render = needs_render(html)
                except Exception:
                    wants_render = False
                if wants_render:
                    try:
                        rendered = fetch_via_playwright(url, worker_path=worker_path, **pw_kwargs)
                        if rendered and not _looks_blocked(rendered):
                            html = rendered
                            if needs_render(rendered):
                                print(f"[render] {url}: page rendered but JS "
                                      f"content still empty", file=sys.stderr)
                        else:
                            print(f"[render] {url}: rendered page empty/blocked, "
                                  f"using plain HTML", file=sys.stderr)
                    except Exception as e:
                        # Keep the plain HTML so the other fields still parse,
                        # but say why the rendered fetch didn't happen.
                        print(f"[render] {url}: Playwright re-fetch failed: {e}",
                              file=sys.stderr)
    else:
        try:
            html = fetch_via_playwright(url, worker_path=worker_path, **pw_kwargs)
        except RuntimeError as pw_err:
            # Some sites refuse datacenter IPs outright (403 for every
            # browser/header combination), so retry through ScraperAPI.
            if matched in PROXY_FALLBACK_DOMAINS:
                try:
                    html = fetch_via_scraperapi(url)
                except Exception as proxy_err:
                    raise RuntimeError(
                        f"{pw_err} | ScraperAPI fallback failed: {proxy_err}"
                    ) from proxy_err
            else:
                raise

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

    if isinstance(business, list):
        return [filter_business_fields(record, url) for record in business]

    return filter_business_fields(business, url)