from urllib.parse import urlparse
import requests
from . import parsers
from .common import fetch_via_requests, fetch_via_playwright, filter_business_fields
from .common import _looks_blocked, _looks_like_cloudflare_error

SITE_PARSERS = {
    "letsknowit.com": ("requests", parsers.letsknowit.parse_letsknowit),
    "metriteweb.com": ("requests", parsers.metriteweb.parse_metriteweb),
    "qdexx.com": ("requests", parsers.qdexx.parse_qdexx),
    "dbesearch.com": ("requests", parsers.dbesearch.parse_dbesearch),
    "locuul.com": ("requests", parsers.locuul.parse_locuul),
    "smallbusinessusa.com": ("playwright", parsers.smallbusinessusa.parse_smallbusinessusa),
    "zumvu.com": ("playwright", parsers.zumvu.parse_zumvu),
    "blinx.biz": ("playwright", parsers.blinx_biz.parse_blinx),
    "place123.net": ("requests", parsers.place123.parse_place123),
    "freelistingusa.com": ("requests", parsers.freelistingusa.parse_freelistingusa),
    "askmap.net": ("requests", parsers.askmap.parse_askmap),
    "gravitysplash.com": ("requests", parsers.gravitysplash.parse_gravitysplash),
    "provenexpert.com": ("requests", parsers.provenexpert.parse_provenexpert),
    "zipleaf.us": ("requests", parsers.zipleaf.parse_zipleaf),
    "fyple.com": ("requests", parsers.fyple.parse_fyple),
    "merchantcircle.com": ("requests", parsers.merchantcircle.parse_merchantcircle),
    "globalbusinessdirectory.us": ("requests", parsers.globalbusinessdirectory.parse_globalbusinessdirectory),
    "listings.globalbusinessdirectory.us": ("requests", parsers.listings_globalbusinessdirectory.parse_listings_globalbusinessdirectory),
    "usa.globalbusinessdirectory.us": ("requests", parsers.usa_globalbusinessdirectory.parse_usaglobalbusinessdirectory),
    "cities.globalbusinessdirectory.us": ("requests", parsers.cities_globalbusinessdirectory.parse_citiesglobalbusinessdirectory),
    "local.globalbusinessdirectory.us": ("requests", parsers.local_globalbusinessdirectory.parse_localglobalbusinessdirectory),
    "blogs.globalbusinessdirectory.us": ("requests", parsers.blogs_globalbusinessdirectory.parse_blogs_globalbusinessdirectory),
    "chamberofcommerce.com": ("requests", parsers.chamberofcommerce.parse_chamberofcommerce),
    "trueen.com": ("requests", parsers.trueen.parse_trueen),
    "citysquares.com": ("requests", parsers.citysquares.parse_citysquares),
    "b2bco.com": ("requests", parsers.b2bco.parse_b2bco),
    "find-us-here.com": ("playwright", parsers.find_us_here.parse_findushere),
    "a-zbusinessfinder.com": ("playwright", parsers.a_zbusinessfinder.parse_azbusinessfinder),
    "linkcentre.com": ("requests", parsers.linkcentre.parse_linkcentre),
    "band.us": ("requests", parsers.band.parse_band),
    "americansearch.info": ("requests", parsers.americansearch.parse_americansearch),
    "n49.com": ("requests", parsers.n49.parse_n49),
    "bizhwy.com": ("requests", parsers.bizhwy.parse_bizhwy),
    "yplocal.com": ("requests", parsers.yplocal.parse_yplocal),
    "golocalezservices.com": ("requests", parsers.golocalezservices.parse_golocalezservices),
    "globeconnected.com": ("requests", parsers.globeconnected.parse_globeconnected),
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
    "vetslist.com": ("requests", parsers.vetslist.parse_vetslist),
    "vymaps.com": ("requests", parsers.vymaps.parse_vymaps),
    "wireanium.com": ("requests", parsers.wireanium.parse_wireanium),
    "closelocation.com": ("requests", parsers.closelocation.parse_closelocation),
    "trustburn.com": ("requests", parsers.trustburn.parse_trustburn),
    "searchmypro.com": ("requests", parsers.searchmypro.parse_searchmypro),
    "yourbizlistings.com": ("requests", parsers.yourbizlistings.parse_yourbizlistings),
    "bulkpostads.com": ("requests", parsers.bulkpostads.parse_bulkpostads),
    "meetyourmarkets.com": ("requests", parsers.meetyourmarkets.parse_meetyourmarkets),
    "bulkadspost.com": ("requests", parsers.bulkadspost.parse_bulkadspost),
    "bizcoupon.directory": ("requests", parsers.bizcoupon.parse_bizcoupon),
    "countrypwr.com": ("requests", parsers.countrypwr.parse_countrypwr),
    "homify.com": ("requests", parsers.homify.parse_homify),
    "cake.me": ("requests", parsers.cake.parse_cake),
    "perrysplacepromotions.org": ("requests", parsers.perrysplacepromotions.parse_perrysplacepromotions),
    "provenemployer.com": ("requests", parsers.provenemployer.parse_provenemployer),
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
            html = None
            blocked = True

        # ---- DEBUG: confirm what the "requests" fetch actually got back.
        # Check this line in your Streamlit Cloud logs (Manage app -> Logs)
        # after re-running a URL that's coming back empty. If html_len is
        # small or the snippet doesn't look like the real page (JS shell,
        # "just a moment", redirect, etc.) even though blocked=False, the
        # site is quietly serving different content to the cloud IP than
        # it serves to BLOCK_SIGNALS-style bot walls -- switch this
        # domain's method to "playwright" in SITE_PARSERS above.
        #
        # flush=True: Streamlit Cloud's stdout is not attached to a real
        # terminal, so Python block-buffers print() output by default.
        # Without flush=True these lines can sit in an unflushed buffer
        # indefinitely (never appearing in Manage app -> Logs) even
        # though this code path definitely ran.
        print(
            f"[DEBUG extract_business] url={url} matched={matched} "
            f"blocked={blocked} html_len={len(html) if html else 0} "
            f"snippet={(html or '')[:300]!r}",
            flush=True,
        )

        if blocked:
            # Unmapped/blocked site -- retry via Playwright automatically
            html = fetch_via_playwright(url, worker_path=worker_path)
    else:
        html = fetch_via_playwright(url, worker_path=worker_path)

        # ---- DEBUG: the "requests" branch above has always had this
        # visibility; the "playwright" branch never did. Without it we
        # can't tell whether Playwright got the real page, an empty JS
        # shell, or a bot-check/interstitial page for domains routed
        # straight to playwright (like bizmaker.org).
        print(
            f"[DEBUG extract_business:playwright] url={url} matched={matched} "
            f"html_len={len(html) if html else 0} "
            f"snippet={(html or '')[:500]!r}",
            flush=True,
        )

    if _looks_like_cloudflare_error(html):
        raise RuntimeError(
            f"Fetch for {url} returned a Cloudflare error page "
            f"(origin server appears to be down or unreachable), "
            f"not the real page content."
        )

    business = parser(url, html)

    # ---- DEBUG: confirm whether the parser found anything at all once it
    # got HTML. If html_len above looked healthy (a real page) but every
    # value here is still empty, the bug is in parsers/<site>.py's
    # selectors, not in fetching -- share that parser file next.
    #
    # flush=True: same buffering reason as the print above -- this line
    # also runs for "playwright"-routed domains (like bizmaker.org),
    # which never hit the print above, so it's the only debug signal
    # for those. It needs to actually reach the log viewer.
    non_empty = sum(1 for v in business.values() if v not in ("", {}, []))
    print(
        f"[DEBUG extract_business] url={url} parser={parser.__name__} "
        f"non_empty_fields={non_empty}/{len(business)}",
        flush=True,
    )

    if isinstance(business, list):
        return [filter_business_fields(record, url) for record in business]

    return filter_business_fields(business, url)
