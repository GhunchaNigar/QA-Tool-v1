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

# Signals that the rendered page is Cloudflare's own error page (origin
# server down / unreachable / timed out) rather than the site's real
# content. This is a different failure shape from BLOCK_SIGNALS above:
# a bot-check page gets caught by _is_blocked, but a Cloudflare 5xx
# error page is neither a bot-check page nor "thin" -- it renders
# enough of its own boilerplate text to clear _is_thin's char floor --
# so without checking for it explicitly, page.goto() "succeeds" (the
# navigation completed, it's just Cloudflare's error page that loaded)
# and the worker reports success:true with Cloudflare's error page as
# the scraped content. The caller then has no way to tell this apart
# from a real, empty listing.
CLOUDFLARE_ERROR_SIGNALS = [
    "error 521", "error 522", "error 523", "error 524", "error 525", "error 526",
    "web server is down", "connection timed out", "origin is unreachable",
    "cloudflare ray id",
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

def _is_blocked(html, text):
    combined = (html[:3000] + text[:1000]).lower()
    return any(s in combined for s in BLOCK_SIGNALS)

def _is_cloudflare_error(html, text):
    combined = (html[:3000] + text[:1000]).lower()
    return any(s in combined for s in CLOUDFLARE_ERROR_SIGNALS)

def _is_thin(text, min_chars=200):
    return len(text.strip()) < min_chars


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
    for _ in range(4):
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
    settle (domcontentloaded first, then an explicit extra pause)
    since a page that was too slow/thin on attempt 1 may just need
    more time rather than a different approach entirely."""

    page = await context.new_page()
    try:
        if not patient:
            try:
                await page.goto(url, timeout=timeout, wait_until="networkidle")
            except Exception:
                await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        else:
            # Retry pass: domcontentloaded first (less likely to itself
            # time out on pages with persistent background requests
            # like analytics/ads that never let networkidle fire), then
            # give the page extra settle time before extracting.
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

        html, text, title = await _extract_and_expand(page)
        return html, text, title, None
    except Exception as e:
        return "", "", "", f"goto/extract failed: {e}"
    finally:
        await page.close()


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
            elif _is_cloudflare_error(html, text):
                debug_notes.append("attempt1: Cloudflare error page (origin down/unreachable)")
            elif _is_thin(text):
                debug_notes.append(f"attempt1: too thin ({len(text.strip())} chars)")
            else:
                debug_notes.append(f"attempt1 OK | text={len(text):,} chars")

            attempt1_ok = (
                not err
                and not _is_blocked(html, text)
                and not _is_cloudflare_error(html, text)
                and not _is_thin(text)
            )

            # ---- Attempt 2 (patient retry) ----
            # Only retried when attempt 1 didn't cleanly succeed -- this
            # is what fixes the "works sometimes, fails other times"
            # pattern: a single slow/early snapshot no longer means the
            # whole extraction fails outright. Note: if the origin is
            # actually down (Cloudflare error), this retry will very
            # likely hit the same error page again -- that's expected
            # and reported below, rather than treated as a real result.
            if not attempt1_ok:
                html2, text2, title2, err2 = await _attempt(
                    context, url, timeout, patient=True
                )
                if err2:
                    debug_notes.append(f"attempt2: {err2}")
                elif _is_blocked(html2, text2):
                    debug_notes.append("attempt2: blocked/CAPTCHA")
                elif _is_cloudflare_error(html2, text2):
                    debug_notes.append("attempt2: Cloudflare error page (origin down/unreachable)")
                elif _is_thin(text2):
                    debug_notes.append(f"attempt2: too thin ({len(text2.strip())} chars)")
                else:
                    debug_notes.append(f"attempt2 OK | text={len(text2):,} chars")

                # Prefer attempt 2's result if it's usable, even if
                # attempt 1 produced *some* non-empty output -- a thin,
                # blocked, or Cloudflare-error attempt 1 result is not
                # a valid fallback.
                if (
                    not err2
                    and not _is_blocked(html2, text2)
                    and not _is_cloudflare_error(html2, text2)
                    and not _is_thin(text2)
                ):
                    html, text, title = html2, text2, title2

            await browser.close()

            final_blocked = _is_blocked(html, text)
            final_cf_error = _is_cloudflare_error(html, text)
            final_thin = _is_thin(text)

            if final_blocked:
                result["debug"] = "Playwright: blocked/CAPTCHA | " + " | ".join(debug_notes)
                return result
            if final_cf_error:
                result["debug"] = (
                    "Playwright: Cloudflare error page (origin server down/unreachable) | "
                    + " | ".join(debug_notes)
                )
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
