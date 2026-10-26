"""Usage: python try_methods.py <url> [method ...]

Tries each fetch method on one URL, one at a time, and reports what came back.
Without method names it runs them all in order and waits for Enter between
them. ScraperAPI is deliberately not included.

Methods:
  requests         plain requests with the tool's browser headers
  curl_cffi        Chrome network-fingerprint imitation (skipped if not installed:
                   pip install curl_cffi)
  headless         Playwright + installed Chrome, headless (the tool's default)
  headed           visible Chrome window + saved profile; you are present and
                   click any challenge checkbox yourself
  cdp              attach to your own Chrome (see setup below)
  file:<path>      read a page you saved by hand (manual fallback, see below)

cdp setup (Chrome 136+ ignores --remote-debugging-port on your everyday
profile, so it must start with its own profile folder):

  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" ^
      --remote-debugging-port=9222 --user-data-dir="C:\\chrome-qa"

In that window install the VPN extension, open the listing yourself, pass any
check, then run:  python try_methods.py <url> cdp
If cdp is still blocked, stop there rather than adding more workarounds.

Manual fallback for blocked sites: open the listing in your browser, press
Ctrl+S, choose "Webpage, HTML only", then run:
  python try_methods.py <url> file:C:\\path\\to\\listing.html

Result meanings:
  WORKED       page loaded and contains business data; safe to check
  NOT CHECKED  page was blocked, empty or had no data; nobody saw the listing,
               so do NOT report it as an issue with the client's listing

VPN / WARP: turn it on for the whole computer, then re-run this script
(for example `python try_methods.py <url> requests headless`).
"""
import os
import re
import sys
import time
from pathlib import Path

import requests

from business_extractor.common import HEADERS, _looks_blocked

# The tool no longer launches a local browser, so common.py has no
# fetch_via_playwright any more. Keep this script usable for the
# requests / curl_cffi / file: methods; headless and cdp are skipped.
try:
    from business_extractor.common import fetch_via_playwright
except ImportError:
    fetch_via_playwright = None

ALL = ["requests", "curl_cffi", "headless", "headed", "cdp"]

# Separate profile folder so it never clashes with your everyday Chrome,
# which is the most common reason a visible window refuses to open.
HEADED_PROFILE = Path(__file__).resolve().parent / ".headed_profile"
HEADED_WAIT_SECONDS = 180


def report(label, html, status=None):
    """Print what came back and return "WORKED" or "NOT CHECKED"."""
    html = html or ""
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    has_data = "__next_f" in html or "application/ld+json" in html
    blocked = bool(html) and _looks_blocked(html)
    http_error = isinstance(status, int) and status >= 400

    if html and has_data and not blocked and not http_error:
        verdict, reason = "WORKED", ""
    else:
        verdict = "NOT CHECKED"
        if not html:
            reason = "empty response"
        elif blocked or http_error:
            reason = "blocked by the site"
        else:
            reason = "page loaded but contains no business data"

    print(f"  status={status} len={len(html)} "
          f"title={title.group(1).strip()[:70] if title else None!r}")
    print(f"  looks_blocked={blocked} has_business_data={has_data}  ->  {verdict}")
    if verdict == "NOT CHECKED":
        print(f"  reason: {reason}. This listing was NOT verified; do not report "
              "it as an issue with the client's listing.")
    return verdict


def fetch_headed(url):
    """Open a visible browser, let the user solve any challenge, return HTML."""
    from playwright.sync_api import sync_playwright

    HEADED_PROFILE.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        ctx = None
        errors = []
        # Try installed Chrome first, then Edge, then Playwright's own Chromium.
        for channel in ("chrome", "msedge", None):
            try:
                kwargs = dict(
                    user_data_dir=str(HEADED_PROFILE),
                    headless=False,
                    args=["--start-maximized"],
                    no_viewport=True,
                )
                if channel:
                    kwargs["channel"] = channel
                ctx = p.chromium.launch_persistent_context(**kwargs)
                print(f"  opened browser: {channel or 'playwright chromium'}")
                break
            except Exception as e:
                errors.append(f"{channel or 'chromium'}: {str(e).splitlines()[0][:200]}")
        if ctx is None:
            print("  could not open any visible browser:")
            for line in errors:
                print("   -", line)
            print("  fixes: close every Chrome window using that profile, or run "
                  "`playwright install chromium`; if you are in WSL/SSH/a server "
                  "there is no display, so use the cdp or file method instead.")
            return ""

        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.bring_to_front()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"  goto warning: {str(e).splitlines()[0][:150]}")

            print("  if a 'verify you are human' box appears, click it now "
                  f"(waiting up to {HEADED_WAIT_SECONDS}s)...")
            deadline = time.time() + HEADED_WAIT_SECONDS
            html = ""
            while time.time() < deadline:
                try:
                    html = page.content()
                except Exception:
                    html = ""
                if html and not _looks_blocked(html):
                    break
                time.sleep(2)
            time.sleep(1)  # let late scripts finish
            try:
                html = page.content()
            except Exception:
                pass
            return html
        finally:
            ctx.close()


def read_saved_file(path):
    """Manual fallback: read a page saved with Ctrl+S ("Webpage, HTML only")."""
    p = Path(path.strip().strip('"'))
    if not p.is_file():
        print(f"  file not found: {p}")
        return ""
    data = p.read_bytes()
    for enc in ("utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def run(method, url):
    if method == "requests":
        r = requests.get(url, headers=HEADERS, timeout=30)
        return report(method, r.text, r.status_code)
    elif method == "curl_cffi":
        try:
            from curl_cffi import requests as cr
        except ImportError:
            print("  skipped: curl_cffi not installed")
            return "SKIPPED"
        r = cr.get(url, headers=HEADERS, impersonate="chrome", timeout=30)
        return report(method, r.text, r.status_code)
    elif method == "headed":
        return report(method, fetch_headed(url))
    elif method.startswith("file:"):
        return report(method, read_saved_file(method[len("file:"):]), "saved file")
    elif method in ("headless", "cdp"):
        if fetch_via_playwright is None:
            print("  skipped: this tool no longer launches a local browser "
                  "(use requests, curl_cffi or file:<path>)")
            return "SKIPPED"
        if method == "headless":
            os.environ.pop("QA_BROWSER_MODE", None)
        else:
            os.environ["QA_BROWSER_MODE"] = method
            print("  needs Chrome started with --remote-debugging-port=9222 "
                  "AND its own --user-data-dir (Chrome 136+), with the listing "
                  "already open and any check passed. See the top of this file.")
        try:
            verdict = report(method, fetch_via_playwright(url))
            if method == "cdp" and verdict == "NOT CHECKED":
                print("  cdp still not working: stop here. Use the file: "
                      "fallback or ask the directory to allowlist you.")
            return verdict
        finally:
            os.environ.pop("QA_BROWSER_MODE", None)
    else:
        print(f"  unknown method {method!r}; choose from {ALL} or file:<path>")
        return "SKIPPED"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    url = sys.argv[1]
    methods = sys.argv[2:] or ALL
    results = {}
    for i, m in enumerate(methods):
        print(f"\n=== {m} ===")
        try:
            results[m] = run(m, url)
        except Exception as e:
            print(f"  error: {str(e)[:400]}")
            results[m] = "NOT CHECKED"
            print("  This listing was NOT verified.")
        if len(methods) > 1 and i < len(methods) - 1:
            input("Press Enter for the next method...")

    print("\n=== summary ===")
    for m, v in results.items():
        print(f"  {m:<14} {v}")
    if "WORKED" not in results.values():
        print("  No method got the page: mark this listing NOT CHECKED in the QA report.")