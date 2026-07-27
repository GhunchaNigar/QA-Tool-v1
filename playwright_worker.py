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
    return any(s in combined for s in BLOCK_SIGNALS)


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
            try:
                response = await page.goto(url, timeout=timeout, wait_until="networkidle")
            except Exception:
                response = await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
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
_RATE_LIMIT_BACKOFFS = [5.0, 12.0]
_RATE_LIMIT_MAX_WAIT = 20.0  # cap even if Retry-After asks for longer


async def scrape(url, timeout, ignore_https_errors=False):
    from playwright.async_api import async_playwright
    result = {"success": False, "html": "", "text": "", "title": "", "debug": ""}

    own_domain = urlparse(url).netloc.lower()
    if own_domain.startswith("www."):
        own_domain = own_domain[4:]

    try:
        async with async_playwright() as p:
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
                # net::ERR_CERT_AUTHORITY_INVALID before any navigation
                # can happen at all -- goto() fails immediately, on both
                # the fast and patient attempts, with no page content to
                # even evaluate "thin" against. This is passed in per-call
                # (see __main__ below) rather than defaulted True, so it
                # only relaxes verification for the specific domains the
                # caller (extractor.py's IGNORE_CERT_ERRORS_DOMAINS) has
                # already confirmed have this problem, not for every site.
                ignore_https_errors=ignore_https_errors,
            )
            await context.add_init_script(_STEALTH_INIT_SCRIPT)

            debug_notes = []
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
                        f"{label}: too thin ({len(text.strip())} chars) | preview: {_preview(text)!r}"
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
        print(json.dumps(result, ensure_ascii=False))
    except UnicodeEncodeError:
        # Last-resort fallback if reconfigure() itself wasn't available
        # (e.g. very old Python) -- escape non-ASCII rather than lose
        # the result entirely.
        print(json.dumps(result, ensure_ascii=True))
