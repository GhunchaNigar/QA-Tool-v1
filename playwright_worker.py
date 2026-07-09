"""
playwright_worker.py
Standalone script called by scraper.py via subprocess to avoid
Windows asyncio/Streamlit event loop conflicts.

Usage: python playwright_worker.py <url> <timeout_ms>
Output: JSON to stdout  {"success": bool, "html": "...", "text": "...", "title": "...", "debug": "..."}
"""

import sys
import json
import asyncio
import re

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

def _is_thin(text, min_chars=200):
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
    more time rather than a different approach entirely."""

    page = await context.new_page()
    try:
        if not patient:
            try:
                await page.goto(url, timeout=timeout, wait_until="networkidle")
            except Exception:
                await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
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
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            data_arrived = await _wait_for_data(page, min(timeout, 20000))
            if not data_arrived:
                # Selector never showed up within budget -- give the
                # page one more flat settle window as a last resort
                # rather than extracting immediately on timeout.
                await page.wait_for_timeout(3000)

        html, text, title = await _extract_and_expand(page)
        return html, text, title, None
    except Exception as e:
        return "", "", "", f"goto/extract failed: {e}"
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


async def scrape(url, timeout):
    from playwright.async_api import async_playwright
    result = {"success": False, "html": "", "text": "", "title": "", "debug": ""}

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
            )
            await context.add_init_script(_STEALTH_INIT_SCRIPT)

            debug_notes = []
            html, text, title = "", "", ""

            # ---- Attempt 1 (fast path) ----
            html, text, title, err = await _attempt(context, url, timeout, patient=False)
            if err:
                debug_notes.append(f"attempt1: {err}")
            elif _is_blocked(html, text):
                debug_notes.append("attempt1: blocked/CAPTCHA")
            elif _is_thin(text):
                debug_notes.append(
                    f"attempt1: too thin ({len(text.strip())} chars) | preview: {_preview(text)!r}"
                )
            else:
                debug_notes.append(f"attempt1 OK | text={len(text):,} chars")

            attempt1_ok = (
                not err and not _is_blocked(html, text) and not _is_thin(text)
            )

            # ---- Attempt 2 (patient retry) ----
            # Only retried when attempt 1 didn't cleanly succeed -- this
            # is what fixes the "works sometimes, fails other times"
            # pattern: a single slow/early snapshot no longer means the
            # whole extraction fails outright.
            if not attempt1_ok:
                html2, text2, title2, err2 = await _attempt(
                    context, url, timeout, patient=True
                )
                if err2:
                    debug_notes.append(f"attempt2: {err2}")
                elif _is_blocked(html2, text2):
                    debug_notes.append("attempt2: blocked/CAPTCHA")
                elif _is_thin(text2):
                    debug_notes.append(
                        f"attempt2: too thin ({len(text2.strip())} chars) | preview: {_preview(text2)!r}"
                    )
                else:
                    debug_notes.append(f"attempt2 OK | text={len(text2):,} chars")

                # Prefer attempt 2's result if it's usable, even if
                # attempt 1 produced *some* non-empty output -- a thin
                # or blocked attempt 1 result is not a valid fallback.
                if not err2 and not _is_blocked(html2, text2) and not _is_thin(text2):
                    html, text, title = html2, text2, title2

            await browser.close()

            final_blocked = _is_blocked(html, text)
            final_thin = _is_thin(text)

            if final_blocked:
                result["debug"] = "Playwright: blocked/CAPTCHA | " + " | ".join(debug_notes)
                return result
            if final_thin:
                result["debug"] = (
                    f"Playwright: too thin ({len(text.strip())} chars) | "
                    + " | ".join(debug_notes)
                )
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
    loop    = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(scrape(url, timeout))
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
