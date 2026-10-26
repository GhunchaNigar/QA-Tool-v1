"""
playwright_worker.py
Standalone script called by scraper.py via subprocess to avoid
Windows asyncio/Streamlit event loop conflicts.

Usage: python playwright_worker.py <url> <timeout_ms> [<ignore_https_errors>]
Output: JSON to stdout  {"success": bool, "html": "...", "text": "...", "title": "...", "debug": "..."}
"""

import sys
import json
import asyncio
import re
from urllib.parse import urlparse

def set_windows_event_loop():
    """
    Ensure ProactorEventLoop on Windows.

    Playwright's async API launches the browser as a subprocess, and only
    ProactorEventLoop supports subprocess pipes on Windows — SelectorEventLoop
    raises a bare NotImplementedError (empty message) the moment Playwright
    tries to spawn chromium. ProactorEventLoop has been the Windows default
    since Python 3.8, so this just makes that explicit/guaranteed rather
    than relying on whatever the ambient policy happens to be.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

BLOCK_SIGNALS = [
    "captcha", "are you human", "cf-browser-verification",
    "ddos-guard", "checking your browser", "verify you are human",
    "enable cookies to continue", "please enable cookies",
    "security check", "access to this page has been denied",
    # Cloudflare interstitial / Turnstile. Without these the worker used to
    # report the "Just a moment..." page as a successful fetch.
    "just a moment", "challenges.cloudflare.com", "_cf_chl_opt",
    "cf-turnstile", "verifying you are human", "performing security",
]

# ── Rate-limit signals ───────────────────────────────────────────────
# Distinct from BLOCK_SIGNALS above: a CAPTCHA/bot-wall is a dead end
# that waiting out won't fix, but a 429 is explicitly telling us to
# slow down and come back later -- retrying immediately just draws
# another 429. Confirmed on closelocation.com: both the fast and
# patient attempts returned the exact same 81-char body ("Too Many
# Requests The user has sent too many requests in a given amount of
# time.") and got misdiagnosed as generic "too thin" content, which
# reads like a broken/empty listing rather than what it actually is --
# the site rate-limiting this IP. Checking the navigation response's
# HTTP status (most reliable) plus this text as a fallback (in case a
# proxy/CDN returns 200 with a rate-limit message in the body) lets
# scrape() apply an actual backoff instead of repeating the same
# doomed request twice in a row.
RATE_LIMIT_SIGNALS = [
    "too many requests",
    "rate limit exceeded",
    "429 too many requests",
]

# ── Stealth init script ──────────────────────────────────────────────
# Injected into every new page BEFORE any site JS runs (via
# add_init_script), so it patches the fingerprints most bot-detection
# scripts check for a plain headless Chromium launch:
#   - navigator.webdriver is normally `true` under automation; real
#     Chrome never sets it, so we redefine the getter to return
#     undefined.
#   - navigator.plugins/mimeTypes are empty arrays under headless
#     Chrome; real browsers always report a handful of built-in PDF
#     plugins, so we fake a non-empty list.
#   - navigator.languages is sometimes empty under headless launch.
#   - window.chrome is missing entirely under headless Chromium; some
#     detection scripts specifically check for its absence.
#   - the Permissions API's `notifications` query behaves differently
#     under automation and is a known fingerprinting signal.
# This is a best-effort patch, not a guarantee of evading detection —
# but it removes the handful of cheapest, most commonly checked
# signals, which is often enough to stop a site from silently serving
# a stripped-down/blocked page instead of the real content.
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = window.chrome || { runtime: {} };
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );
}
"""

# ── Data-arrival selectors ───────────────────────────────────────────
# Several sites (blinx.biz confirmed via DevTools Network tab, likely
# others) render an empty/skeleton shell in the initial HTML and only
# populate the real business content (phone, email, external website
# link) via a client-side XHR call that fires AFTER the page's load/
# networkidle/domcontentloaded events already resolved. Blindly sleeping
# a fixed number of ms after those events is a guess: too short and we
# snapshot the shell (this is what produced the "too thin (181 chars)"
# result on blinx.biz/focal even though the data reliably exists and
# loads a few seconds later); too long and every other page pays the
# extra wait for nothing.
#
# Instead, explicitly wait for ONE of these selectors to appear before
# extracting -- they're the actual data we care about, so their
# presence is a direct signal the XHR has resolved rather than an
# indirect proxy like "did innerText length stop changing for a bit".
# This is best-effort: a listing with no phone/email/website at all
# would never match and we'd fall through to the timeout, which is
# fine since the existing text-stabilization polling below still runs
# as a secondary check either way.
_DATA_READY_SELECTOR = (
    'a[href^="tel:"], '
    'a[href^="mailto:"], '
    'a[href^="http"]:not([href*="blinx.biz"]):not([href*="brownbook.net"])'
)


def _is_blocked(html, text):
    combined = (html[:3000] + text[:1000]).lower()
    if any(s in combined for s in BLOCK_SIGNALS):
        return True
    # Challenge pages push their markers past 3000 chars (inline CSS/JS) but
    # have no real content, so only flag a late marker on small pages.
    if len(html) < 60000:
        tail = html.lower()
        return any(m in tail for m in ("challenges.cloudflare.com", "_cf_chl_opt", "cf-turnstile"))
    return False


async def _challenge_result(page, response):
    """Challenge never cleared: return the page as-is right away (skipping the
    slow scroll/expand steps) so the caller reports it as blocked."""
    try:
        html = await page.content()
        title = await page.title()
    except Exception:
        html, title = "", ""
    return html, "", title, None, (response.status if response else None), None


async def _wait_out_challenge(page, max_s=12):
    """Cloudflare's "Just a moment..." interstitial often resolves itself
    after a few seconds in a real browser and then redirects to the page.
    Poll the title (survives the navigation, unlike wait_for_function)
    instead of snapshotting the challenge page. If it never clears, the
    caller's _is_blocked check reports it as blocked."""
    for _ in range(max_s):
        try:
            title = (await page.title()).lower()
        except Exception:
            title = "just a moment"  # mid-navigation; keep waiting
        if "just a moment" not in title:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)
            return True
        await page.wait_for_timeout(1000)
    return False


def _is_rate_limited(html, text, status=None):
    """True if the response is an HTTP 429, or the body reads like one
    even under a 200 (some CDNs/WAFs return rate-limit pages without
    setting the status code). Status check first since it's the most
    reliable signal when available."""
    if status == 429:
        return True
    combined = (html[:2000] + text[:500]).lower()
    return any(s in combined for s in RATE_LIMIT_SIGNALS)


# A page's <title>/og:title being literally its own bare domain (e.g.
# "www.manta.com" instead of the actual listing's name) is a strong,
# generic signal that a bot-mitigation stub/shell rendered instead of
# real content. Confirmed on manta.com: the stub still has a full nav
# bar and footer (hundreds of chars of boilerplate text), so a plain
# text-length "thin" check alone doesn't catch it -- this title check
# does, directly, regardless of how much surrounding chrome padded the
# character count. Deliberately conservative (exact match, not a
# substring test) so it can't misfire on a real listing whose name
# happens to mention the site's name.
_BARE_DOMAIN_TITLE_RE = re.compile(r"^(?:www\.)?[\w-]+\.[a-z]{2,}$", re.I)


def _is_bare_domain_title(title, own_domain=None):
    t = (title or "").strip()
    if not t:
        return False
    if not _BARE_DOMAIN_TITLE_RE.match(t):
        return False
    if own_domain:
        bare = t[4:] if t.lower().startswith("www.") else t
        return bare.lower() == own_domain.lower()
    return True

# Same signal as _DATA_READY_SELECTOR below (tel:/mailto:/external
# link), but as a plain string check against already-fetched HTML
# rather than a live page.wait_for_selector call. Used by _is_thin as
# an override: a page that already contains real contact data is not
# "thin" no matter how short its total text is.
#
# Confirmed on blinx.biz/focal: total rendered text is only 181 chars,
# but that 181 chars *is* the complete, correct business record --
# name, address, phone, website, email. It was being discarded purely
# because it's under the flat 200-char floor, even though both the
# fast pass and the patient retry pass independently rendered the same
# complete result (i.e. the page was genuinely done, not mid-hydration).
#
# IMPORTANT: this must exclude common site-chrome/social-platform
# domains, not just the two names below. Confirmed on manta.com: its
# bot-mitigation stub page (served to Playwright too -- real listing
# content missing, but the surrounding site template intact) still
# contains the page's own persistent footer links to twitter.com/Manta,
# facebook.com/mantacom, and linkedin.com/company/manta. Those are
# boilerplate present on EVERY page of the site, blocked or not, so
# without excluding them here they satisfied this "real data" check
# and made a blocked stub page with zero actual business data look
# like a complete, legitimate extraction (success:true, but every
# field downstream ends up blank except a garbage Name scraped from
# the stub's own bare-domain og:title).
_CHROME_DOMAINS = (
    r"blinx\.biz|brownbook\.net"
    r"|facebook\.com|twitter\.com|x\.com|instagram\.com"
    r"|linkedin\.com|youtube\.com|tiktok\.com|pinterest\.com"
    r"|google\.com|googletagmanager\.com|googleapis\.com|gstatic\.com"
    r"|doubleclick\.net|wa\.me|whatsapp\.com"
)

_DATA_PRESENT_RE = re.compile(
    r'href=["\']tel:|href=["\']mailto:'
    r'|href=["\']https?://(?!(?:www\.)?(?:' + _CHROME_DOMAINS + r'))',
    re.I,
)


def _has_real_data(html, own_domain=None):
    """own_domain (the site currently being scraped, e.g. "manta.com")
    is excluded too, on top of the fixed chrome-domain list, so a
    directory site's self-referential nav/footer links (e.g. Manta
    linking to its own other listing pages) can't count as evidence of
    THIS listing's real data either."""
    text = html or ""
    if own_domain:
        text = re.sub(
            r'href=["\']https?://(?:www\.)?' + re.escape(own_domain),
            "",
            text,
            flags=re.I,
        )
    return bool(_DATA_PRESENT_RE.search(text))


def _is_thin(text, html="", min_chars=200, own_domain=None):
    if _has_real_data(html, own_domain=own_domain):
        return False
    return len(text.strip()) < min_chars


async def _wait_for_data(page, timeout_ms):
    """Waits for a selector that signals real business data has
    rendered (see _DATA_READY_SELECTOR above), rather than a blind
    sleep. Never raises -- a timeout here just means the page may
    genuinely have no phone/email/external link, or is taking longer
    than expected; either way extraction proceeds with whatever's
    there, same as before this was added."""
    try:
        await page.wait_for_selector(_DATA_READY_SELECTOR, timeout=timeout_ms)
        return True
    except Exception:
        return False


async def _extract_and_expand(page):
    """Scrolls the page, force-expands hidden/collapsed content, clicks
    any 'See More'-style buttons, then returns (html, text, title).
    Split out from scrape() so it can be reused across retry attempts
    without duplicating this logic."""

    # ── ProvenExpert / ProvenEmployer opening hours ─────────────────
    # #profilesOpening is sent empty and filled by the site's own JS
    # (Profile.setProfileOpeningHours) after load. Wait until it has
    # text, then click "Display all times" so every day is in the DOM.
    # No-op on every other site (the element doesn't exist there).
    try:
        if await page.query_selector("#profilesOpening"):
            try:
                await page.wait_for_function(
                    "() => { const e = document.querySelector('#profilesOpening');"
                    " return e && e.innerText.trim().length > 0; }",
                    timeout=15000,
                )
            except Exception:
                pass
            await page.evaluate("""() => {
                const a = document.querySelector('#showAllOpeningTimes a');
                if (a) { try { a.click(); } catch (e) {} }
            }""")
            await page.wait_for_timeout(800)
    except Exception:
        pass

    # ── Scroll entire page to trigger lazy-loaded images and content ──
    await page.wait_for_timeout(2000)
    await page.evaluate("""async () => {
        await new Promise(resolve => {
            let total = document.body.scrollHeight;
            let current = 0;
            let step = 400;
            const timer = setInterval(() => {
                window.scrollBy(0, step);
                current += step;
                if (current >= total) {
                    clearInterval(timer);
                    window.scrollTo(0, 0);
                    resolve();
                }
            }, 120);
        });
    }""")
    await page.wait_for_timeout(2000)

    # ── Expand all collapsed/hidden text sections ──────────────────
    # This handles "See More", "Show more", max-height collapsing, etc.
    await page.evaluate("""() => {
        // Force-show all hidden elements that contain text
        document.querySelectorAll('*').forEach(el => {
            const style = window.getComputedStyle(el);
            const isHidden = (
                style.display === 'none' ||
                style.visibility === 'hidden' ||
                style.opacity === '0' ||
                (style.maxHeight && style.maxHeight !== 'none' && parseInt(style.maxHeight) < 50 && el.innerText && el.innerText.trim().length > 20)
            );
            if (isHidden && el.innerText && el.innerText.trim().length > 10) {
                el.style.display = 'block';
                el.style.visibility = 'visible';
                el.style.opacity = '1';
                el.style.maxHeight = 'none';
                el.style.overflow = 'visible';
            }
        });
        // Also click any "See More" / "Show more" buttons
        document.querySelectorAll('a, button, span').forEach(el => {
            const txt = (el.innerText || '').toLowerCase().trim();
            if (txt === 'see more' || txt === 'show more' || txt === 'read more' || txt === 'ver más') {
                try { el.click(); } catch(e) {}
            }
        });
    }""")
    await page.wait_for_timeout(1500)

    # ── Poll for body text to stabilize instead of a single fixed ──
    # sleep. Some sites (earthmom.org included) render their real
    # content client-side a beat after networkidle/domcontentloaded
    # already resolved, so a fixed wait can grab the page mid-render.
    # Checking innerText length across a few short intervals and only
    # stopping once it holds steady (or we hit a small cap) catches
    # that without slowing down pages that were already done.
    previous_len = -1
    for _ in range(6):
        current_text = await page.evaluate(
            "() => document.body ? document.body.innerText.trim().length : 0"
        )
        if current_text == previous_len and current_text > 0:
            break
        previous_len = current_text
        await page.wait_for_timeout(800)

    html = await page.content()
    title = await page.title()

    # ── Extract text WITHOUT removing hidden elements ───────────────
    # We already expanded them above; removing display:none now would
    # strip content that was just made visible by our JS above.
    text = await page.evaluate("""() => {
        const els = document.querySelectorAll(
            'script,style,noscript,iframe,svg'
        );
        els.forEach(el => el.remove());
        return document.body ? document.body.innerText : '';
    }""")

    return html, text, title


async def _attempt(context, url, timeout, patient):
    """Runs a single navigation + extraction attempt on a fresh page.
    `patient` widens the wait strategy for the retry pass -- the first
    attempt tries to be quick (networkidle, falling back to
    domcontentloaded), the retry pass gives the page more room to
    settle (domcontentloaded first, then an explicit data-ready wait)
    since a page that was too slow/thin on attempt 1 may just need
    more time rather than a different approach entirely.

    Returns (html, text, title, error, status, retry_after) -- status
    is the navigation response's HTTP status code (None if navigation
    itself raised), and retry_after is the parsed Retry-After response
    header in seconds when the server sent one (None otherwise), so a
    429 can be backed off for exactly as long as the server asked
    instead of a guessed delay."""

    page = await context.new_page()
    try:
        response = None
        if not patient:
            # Deliberately short budget for the networkidle attempt --
            # this branch exists to be the FAST path, but reusing the
            # full per-attempt `timeout` here meant a page whose network
            # never goes idle (ads/analytics/websockets that keep
            # polling -- confirmed on cybo.com and supplyautonomy.com)
            # would burn the entire `timeout` on this call, THEN burn a
            # second full `timeout` on the domcontentloaded fallback
            # below: up to 2x`timeout` just for attempt 1's navigation,
            # before _wait_for_data/_extract_and_expand even run. That
            # blew straight through common.py's subprocess.run() outer
            # timeout ((timeout_ms/1000)+30s) with the worker killed
            # mid-flight and no JSON ever printed -- exactly the bare
            # "Command [...] timed out after 75.0 seconds" failures with
            # no attempt1/attempt2 debug notes at all. Capping this at a
            # small fixed budget means a hung networkidle wait fails
            # fast into the fallback instead of doubling the wait.
            fast_networkidle_timeout = min(timeout, 12000)
            try:
                response = await page.goto(url, timeout=fast_networkidle_timeout, wait_until="networkidle")
            except Exception:
                response = await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            if not await _wait_out_challenge(page):
                return await _challenge_result(page, response)
            # Give client-side-hydrated content (e.g. blinx.biz's
            # business record, loaded via a post-load XHR) a chance to
            # land before the fast pass extracts. This is a real signal
            # (selector presence), not a blind sleep -- see
            # _DATA_READY_SELECTOR above for why that matters.
            await _wait_for_data(page, min(timeout, 8000))
        else:
            # Retry pass: domcontentloaded first (less likely to itself
            # time out on pages with persistent background requests
            # like analytics/ads that never let networkidle fire), then
            # wait explicitly for the data-bearing selector with a much
            # longer budget before falling back to extraction regardless.
            response = await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            if not await _wait_out_challenge(page):
                return await _challenge_result(page, response)
            data_arrived = await _wait_for_data(page, min(timeout, 20000))
            if not data_arrived:
                # Selector never showed up within budget -- give the
                # page one more flat settle window as a last resort
                # rather than extracting immediately on timeout.
                await page.wait_for_timeout(3000)

        html, text, title = await _extract_and_expand(page)

        status = response.status if response else None
        retry_after = None
        if response is not None:
            try:
                header_val = response.headers.get("retry-after")
                if header_val is not None:
                    retry_after = float(header_val)
            except (TypeError, ValueError):
                retry_after = None

        return html, text, title, None, status, retry_after
    except Exception as e:
        return "", "", "", f"goto/extract failed: {e}", None, None
    finally:
        await page.close()


def _preview(text, n=300):
    """Short, single-line preview of captured text for debug output,
    so a 'too thin' result is actually diagnosable (shell-before-
    hydration vs. an unrecognized bot-block page vs. a genuinely
    sparse listing) without having to rerun with extra instrumentation."""
    flat = re.sub(r"\s+", " ", (text or "")).strip()
    if len(flat) > n:
        flat = flat[:n] + "…"
    return flat


# ── Rate-limit backoff schedule ──────────────────────────────────────
# Used only as a fallback when the server didn't send a Retry-After
# header. Two short waits rather than one -- a burst-limited endpoint
# (like closelocation.com, seen returning 429 on both the fast AND
# patient attempts back-to-back with zero delay between them) often
# clears within single-digit seconds, but padding a second, longer
# wait in after that covers a slower-draining limiter without making
# every normal, non-rate-limited page pay for it.
# Domains that 403 the bundled headless Chromium but serve a real Chrome.
# Keep in sync with the "playwright" entries in business_extractor/dispatch.py
# that mention REAL_CHROME_DOMAINS. (Yelp is handled by its own check below.)
REAL_CHROME_DOMAINS = (
    "techdirectory.io",
    "brownbook.net",
)

_RATE_LIMIT_BACKOFFS = [5.0, 12.0]
_RATE_LIMIT_MAX_WAIT = 20.0  # cap even if Retry-After asks for longer


# Domains where Cloudflare blocks every headless browser (verified: requests,
# curl_cffi and headless Chrome all get "Just a moment..."), but a normal,
# visible Chrome window passes. For these the worker opens a real window; if a
# "verify you are human" box appears, click it. The browser uses its own
# profile folder (.headed_profile, next to this file) so Cloudflare's "passed"
# cookie is kept and later runs usually need no click.
HEADED_DOMAINS = (
    "nearfinderus.com",
    "brownbook.net",
    "enrollbusiness.com",   # also covers au./us./uk. ... sub-domains
    "foursquare.com",       # also covers app.foursquare.com
    "surfyourtown.com",     # 403s headless browsers; needs a system-wide VPN too
    "manta.com",            # Cloudflare "Just a moment..." blocks all headless/requests
    "crunchbase.com",       # Cloudflare "verify you are human"; visible Chrome passes
    "hub.biz",              # also covers <slug>.hub.biz listing pages
    "biass.us",             # also covers <slug>.biass.us business sites; verification step
    "directory9.net",       # verification step; visible Chrome passes
)


async def _open_profile_context(p, profile, headless, errors):
    """Open the persistent .headed_profile context (visible or headless).
    Tries installed Chrome, then Edge, then bundled Chromium. Returns the
    context, or None if every browser failed to start."""
    launch_args = ["--disable-blink-features=AutomationControlled"]
    if not headless:
        launch_args.append("--start-maximized")
    if sys.platform.startswith("linux"):
        launch_args += ["--no-sandbox", "--disable-dev-shm-usage"]
    for channel in ("chrome", "msedge", None):
        try:
            kwargs = dict(
                user_data_dir=profile,
                headless=headless,
                args=launch_args,
                ignore_default_args=["--enable-automation"],
            )
            if headless:
                kwargs["viewport"] = {"width": 1280, "height": 900}
            else:
                kwargs["no_viewport"] = True
            if channel:
                kwargs["channel"] = channel
            return await p.chromium.launch_persistent_context(**kwargs)
        except Exception as e:
            errors.append(f"{'headless ' if headless else ''}{channel or 'chromium'}: "
                          f"{str(e).splitlines()[0][:150]}")
    return None


async def _try_headless_with_profile(p, profile, url, errors):
    """Quiet first attempt: same saved profile (so any Cloudflare 'passed'
    cookie is reused) but NO window. Returns (html, text, title) if the real
    page came back, or None if the site blocked/challenged us -- in which
    case the caller falls back to a visible window."""
    ctx = await _open_profile_context(p, profile, True, errors)
    if ctx is None:
        return None
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        status = None
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            status = resp.status if resp else None
        except Exception as e:
            errors.append(f"headless goto: {str(e).splitlines()[0][:120]}")
            return None
        if status is not None and status >= 400:
            errors.append(f"headless: HTTP {status}")
            return None

        # Give a JS challenge a few seconds to clear on its own.
        for _ in range(5):
            try:
                html = await page.content()
                text = await page.inner_text("body")
            except Exception:
                html = text = ""
            if html and not _is_blocked(html, text) and len(text.strip()) >= 200:
                await page.wait_for_timeout(1500)  # let late scripts finish
                try:
                    html = await page.content()
                    text = await page.inner_text("body")
                    title = await page.title()
                except Exception:
                    title = ""
                return html, text, title
            await asyncio.sleep(2)
        errors.append("headless: blocked/challenge page")
        return None
    finally:
        await ctx.close()


async def _scrape_headed(p, url, timeout_ms, result):
    import os
    profile = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".headed_profile")
    os.makedirs(profile, exist_ok=True)

    errors = []

    # 1) Try silently first. A visible window is only opened when the site
    #    actually blocks the headless attempt (verification / captcha / 403).
    quiet = await _try_headless_with_profile(p, profile, url, errors)
    if quiet is not None:
        html, text, title = quiet
        result.update({"success": True, "html": html, "text": text, "title": title,
                       "debug": f"Playwright OK (headless, saved profile) | text={len(text):,} chars"})
        return result

    # 2) Verification needed -> open the visible browser so it can be cleared.
    ctx = await _open_profile_context(p, profile, False, errors)
    if ctx is None:
        result["debug"] = ("Playwright: could not open a visible browser (close any Chrome "
                           "using the .headed_profile folder, or run `playwright install "
                           "chromium`) | " + " | ".join(errors))
        return result

    html = text = title = ""
    passed = False
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.bring_to_front()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            errors.append(f"goto: {str(e).splitlines()[0][:120]}")

        # Wait for the real page; this is the window in which you click the
        # "verify you are human" box if Cloudflare shows one.
        deadline = asyncio.get_event_loop().time() + max(timeout_ms / 1000, 60)
        while asyncio.get_event_loop().time() < deadline:
            try:
                html = await page.content()
                text = await page.inner_text("body")
            except Exception:
                html = text = ""
            if html and not _is_blocked(html, text):
                passed = True
                break
            await asyncio.sleep(2)
        if passed:
            await page.wait_for_timeout(1500)  # let late scripts finish
            try:
                html = await page.content()
                text = await page.inner_text("body")
                title = await page.title()
            except Exception:
                pass
    finally:
        await ctx.close()

    if not passed:
        result["debug"] = ("Playwright: blocked/CAPTCHA - the verify box was not cleared in "
                           "time | " + " | ".join(errors))
        return result
    result.update({"success": True, "html": html, "text": text, "title": title,
                   "debug": f"Playwright OK (visible browser) | text={len(text):,} chars"})
    return result


async def scrape(url, timeout, ignore_https_errors=False):
    from playwright.async_api import async_playwright
    result = {"success": False, "html": "", "text": "", "title": "", "debug": ""}

    own_domain = urlparse(url).netloc.lower()
    if own_domain.startswith("www."):
        own_domain = own_domain[4:]

    try:
        async with async_playwright() as p:
            if any(own_domain == d or own_domain.endswith("." + d) for d in HEADED_DOMAINS):
                return await _scrape_headed(p, url, timeout, result)
            is_yelp = own_domain == "yelp.com" or own_domain.endswith(".yelp.com")
            needs_real_chrome = is_yelp or any(
                own_domain == d or own_domain.endswith("." + d)
                for d in REAL_CHROME_DOMAINS
            )

            if needs_real_chrome:
                # These sites' bot protection rejects the default setup
                # (Playwright's bundled headless-shell Chromium) with a bare
                # "403 Forbidden", even though the same URL opens fine in a
                # normal browser. Launch a real, full Chrome-family browser
                # headless instead (no window opens) and present its real
                # version in the user agent. Order of preference:
                #   1. installed Google Chrome / Microsoft Edge (Windows/Mac)
                #   2. a system Chromium (Linux / Streamlit Cloud installs
                #      one via packages.txt)
                #   3. Playwright's "chromium" channel = the new headless
                #      mode, which shares the real browser's code path and
                #      fingerprint
                #   4. the bundled headless shell (last resort)
                launch_args = ["--disable-blink-features=AutomationControlled"]
                if sys.platform.startswith("linux"):
                    launch_args += ["--no-sandbox", "--disable-dev-shm-usage"]
                browser = None
                launch_errors = []

                for channel in ("chrome", "msedge", "chromium"):
                    try:
                        browser = await p.chromium.launch(
                            channel=channel, headless=True, args=launch_args,
                        )
                        break
                    except Exception as e:
                        launch_errors.append(f"{channel}: {str(e).splitlines()[0][:80]}")
                        browser = None

                if browser is None:
                    import shutil
                    for exe in (
                        shutil.which("chromium"),
                        shutil.which("chromium-browser"),
                        "/usr/bin/chromium",
                        "/usr/bin/chromium-browser",
                    ):
                        if not exe:
                            continue
                        try:
                            browser = await p.chromium.launch(
                                executable_path=exe, headless=True, args=launch_args,
                            )
                            break
                        except Exception as e:
                            launch_errors.append(f"{exe}: {str(e).splitlines()[0][:80]}")
                            browser = None

                if browser is None:
                    browser = await p.chromium.launch(headless=True, args=launch_args)

                # Match the user agent's OS to the machine we're really on,
                # so it agrees with navigator.platform and the TLS/HTTP
                # fingerprint instead of claiming Windows from a Linux box.
                if sys.platform == "win32":
                    os_token = "Windows NT 10.0; Win64; x64"
                elif sys.platform == "darwin":
                    os_token = "Macintosh; Intel Mac OS X 10_15_7"
                else:
                    os_token = "X11; Linux x86_64"
                context = await browser.new_context(
                    user_agent=(
                        f"Mozilla/5.0 ({os_token}) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        f"Chrome/{browser.version} Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 900},
                    locale="en-US",
                    extra_http_headers={
                        "Accept-Language": "en-US,en;q=0.9",
                        "Upgrade-Insecure-Requests": "1",
                    },
                    ignore_https_errors=ignore_https_errors,
                )
                # Short warm-up on the site's home page so it sets its
                # normal cookies and the real request carries a Referer,
                # like a visitor who clicked through.
                try:
                    warm = await context.new_page()
                    await warm.goto(f"https://{urlparse(url).netloc}/", timeout=10000,
                                    wait_until="domcontentloaded")
                    await warm.wait_for_timeout(1500)
                    await warm.close()
                except Exception:
                    pass
            else:
                browser = await p.chromium.launch(
                    headless=True,
                    # Removes the most commonly checked automation flag from
                    # Chromium's own DevTools protocol surface; combined with
                    # the stealth init script below, this covers both the JS-
                    # visible and protocol-visible automation signals.
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 900},
                    locale="en-US",
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                    # Some sources (confirmed: bestdealfinder.com) serve a
                    # broken/self-signed/mismatched TLS certificate on HTTPS,
                    # which Chromium rejects outright as
                    # net::ERR_CERT_AUTHORITY_INVALID. Passed in per-call so
                    # it only relaxes verification for known-bad domains.
                    ignore_https_errors=ignore_https_errors,
                )
            await context.add_init_script(_STEALTH_INIT_SCRIPT)

            debug_notes = []
            if needs_real_chrome:
                debug_notes.append(
                    f"browser={browser.version} (real-chrome path)"
                    + (f" | launch fallbacks: {'; '.join(launch_errors)}" if launch_errors else "")
                )
            html, text, title = "", "", ""

            # ── Attempt loop ────────────────────────────────────────
            # Up to: 1 fast attempt + 1 patient attempt, same as
            # before -- PLUS up to len(_RATE_LIMIT_BACKOFFS) extra
            # patient retries, but ONLY when the previous attempt was
            # specifically rate-limited (429), each preceded by an
            # actual sleep. A CAPTCHA/bot-wall or a genuinely thin page
            # still exits after the normal 2 attempts exactly as
            # before -- only the 429 case gets the extra, delayed
            # retries, since that's the one case where waiting is
            # actually expected to help.
            attempt_num = 0
            rate_limit_retries = 0
            ok = False

            while True:
                attempt_num += 1
                patient = attempt_num > 1
                html, text, title, err, status, retry_after = await _attempt(
                    context, url, timeout, patient=patient
                )

                rate_limited = (not err) and _is_rate_limited(html, text, status)
                blocked = (not err) and not rate_limited and _is_blocked(html, text)
                bare_title = (not err) and not rate_limited and _is_bare_domain_title(title, own_domain)
                thin = (
                    not err and not rate_limited and not blocked and not bare_title
                    and _is_thin(text, html, own_domain=own_domain)
                )
                ok = not err and not rate_limited and not blocked and not bare_title and not thin

                label = f"attempt{attempt_num}"
                if err:
                    debug_notes.append(f"{label}: {err}")
                elif rate_limited:
                    debug_notes.append(
                        f"{label}: rate limited (HTTP {status if status else '??'}) "
                        f"| preview: {_preview(text)!r}"
                    )
                elif blocked:
                    debug_notes.append(f"{label}: blocked/CAPTCHA")
                elif bare_title:
                    debug_notes.append(f"{label}: bare-domain title stub ({title!r})")
                elif thin:
                    debug_notes.append(
                        f"{label}: too thin ({len(text.strip())} chars) | HTTP {status} | "
                        f"title={title!r} | html={len(html):,} chars | preview: {_preview(text)!r}"
                    )
                else:
                    debug_notes.append(f"{label} OK | text={len(text):,} chars")

                if ok:
                    break

                if rate_limited and rate_limit_retries < len(_RATE_LIMIT_BACKOFFS):
                    wait_s = retry_after if retry_after else _RATE_LIMIT_BACKOFFS[rate_limit_retries]
                    wait_s = min(wait_s, _RATE_LIMIT_MAX_WAIT)
                    debug_notes.append(f"waiting {wait_s:.0f}s before retry (rate limited)")
                    await asyncio.sleep(wait_s)
                    rate_limit_retries += 1
                    continue

                # A Cloudflare challenge that didn't clear won't clear on an
                # identical retry; stop now so the worker reports in time.
                if blocked and any(m in html.lower() for m in
                                   ("challenges.cloudflare.com", "_cf_chl_opt", "just a moment")):
                    break

                # Non-rate-limit failure: retry once (the original
                # fast -> patient fallback), then give up.
                if attempt_num < 2:
                    continue

                break

            await browser.close()

            if not ok:
                final_status = None
                # Re-derive the terminal failure reason from the last
                # debug note for the top-level message.
                last_note = debug_notes[-1] if debug_notes else ""
                if "rate limited" in last_note:
                    result["debug"] = (
                        "Playwright: rate limited (429), retries exhausted | "
                        + " | ".join(debug_notes)
                    )
                elif "blocked/CAPTCHA" in last_note:
                    result["debug"] = "Playwright: blocked/CAPTCHA | " + " | ".join(debug_notes)
                elif "bare-domain title stub" in last_note:
                    result["debug"] = (
                        f"Playwright: bare-domain title stub ({title!r}) | "
                        + " | ".join(debug_notes)
                    )
                elif "too thin" in last_note:
                    result["debug"] = (
                        f"Playwright: too thin ({len(text.strip())} chars) | "
                        + " | ".join(debug_notes)
                    )
                else:
                    result["debug"] = "Playwright: failed | " + " | ".join(debug_notes)
                return result

            result.update({
                "success": True, "html": html, "text": text,
                "title": title,
                "debug": f"Playwright OK | text={len(text):,} chars | " + " | ".join(debug_notes),
            })
    except Exception as e:
        result["debug"] = f"Playwright exception: {e}"
    return result


def _make_stdout_utf8_safe():
    """
    Windows consoles default stdout to a legacy codepage (e.g. cp1252),
    not UTF-8. Scraped page text can contain arbitrary Unicode --
    emoji, CJK, symbols -- and json.dumps(..., ensure_ascii=False)
    writes those characters through to stdout as-is. On a cp1252
    console that raises UnicodeEncodeError and kills the whole worker
    AFTER the scrape already succeeded, which is exactly what happened
    here (crashed on the final print, not during scraping).

    Reconfiguring stdout to UTF-8 fixes this for the normal case.
    `errors="replace"` is a second safety net: if some future
    character still can't be represented for any reason, it's swapped
    for a placeholder instead of crashing the process and losing the
    entire successfully-scraped result.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # reconfigure() needs Python 3.7+; if it's ever unavailable for
        # some reason, fall back to ensure_ascii=True at the print site
        # below rather than leaving stdout on its legacy encoding.
        pass


if __name__ == "__main__":
    set_windows_event_loop()
    _make_stdout_utf8_safe()
    url     = sys.argv[1] if len(sys.argv) > 1 else ""
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 45000
    # Optional 3rd CLI arg from extractor.py's fetch_via_playwright(): "1"
    # to relax TLS certificate validation for a known-bad-cert domain
    # (see IGNORE_CERT_ERRORS_DOMAINS there), "0"/absent otherwise. Kept
    # opt-in rather than a bare bool(int(...)) crash risk if the caller
    # is ever an older extractor.py build that doesn't pass this arg yet.
    ignore_https_errors = False
    if len(sys.argv) > 3:
        try:
            ignore_https_errors = bool(int(sys.argv[3]))
        except ValueError:
            ignore_https_errors = False
    loop    = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(scrape(url, timeout, ignore_https_errors))
    except Exception as e:
        result = {"success": False, "html": "", "text": "", "title": "",
                  "debug": f"worker top-level error: {e}"}
    finally:
        loop.close()
    # Write JSON to stdout — scraper.py reads this. ensure_ascii=False
    # is kept (so non-ASCII text stays human-readable in the JSON
    # rather than turning into \uXXXX escapes); stdout is now UTF-8
    # with a replace-on-error fallback, so this print can no longer
    # crash the way it did before.
    try:
        # ensure_ascii=True escapes every non-ASCII character (including
        # U+2028/U+2029/U+0085, which str.splitlines() treats as line
        # breaks), so the result is guaranteed to be ONE line of JSON.
        # The reader decodes the escapes back to the original text.
        print(json.dumps(result, ensure_ascii=True))
    except UnicodeEncodeError:
        # Last-resort fallback if reconfigure() itself wasn't available
        # (e.g. very old Python) -- escape non-ASCII rather than lose
        # the result entirely.
        print(json.dumps(result, ensure_ascii=True))