#!/usr/bin/env python3
"""
scraperapi_cost_check.py -- fetch ONE url through ScraperAPI and report what it cost.

Usage
-----
  python scraperapi_cost_check.py API_KEY URL
  python scraperapi_cost_check.py                      # prompts for key + url
  SCRAPERAPI_KEY=xxx python scraperapi_cost_check.py URL

Default request is the cheapest possible: only api_key + url (ScraperAPI applies
its bot-protection bypass automatically). Nothing else is sent unless you ask.

Useful flags
  --ladder          try flat -> premium -> ultra_premium, stop at the first real page
  --premium / --ultra-premium / --render    add ONE of these parameters yourself
  --max-cost N      hard cap per request (API answers 403 if the cost would exceed it)
  --estimate-only   ask /account/urlcost for the price, do NOT scrape (no page fetched)
  --plan hobby|startup|business|scaling|professional    also print dollars
  --expect TEXT     text that must be in the HTML for the fetch to count as a success
                    (auto 'ng-state' for crunchbase.com URLs)
  --save FILE       write the returned HTML to FILE

What is reported
  * sa-credit-cost   -- credits ScraperAPI says this request cost (response header)
  * account counter  -- requestCount before/after from /account (best effort; it can lag)
  * block detection  -- a verification page returned as HTTP 200 is STILL BILLED,
                        so the script checks the body and tells you if it was a block page.
"""
import argparse
import getpass
import json
import os
import re
import sys
import time

import requests

BASE = os.environ.get("SCRAPERAPI_BASE", "https://api.scraperapi.com")
TIMEOUT = 75  # ScraperAPI retries for up to 70 s; stay above that

# $/month and credits per plan, from the pricing page (Professional: base credits only).
PLANS = {
    "hobby": (49, 100_000),
    "startup": (149, 1_000_000),
    "business": (299, 3_000_000),
    "scaling": (475, 5_000_000),
    "professional": (975, 10_500_000),
}

BLOCK_SIGNALS = (
    "just a moment", "verify you are human", "attention required",
    "enable javascript and cookies to continue", "access denied",
    "checking your browser", "cf-chl", "px-captcha", "datadome",
)
LATE_MARKERS = ("challenges.cloudflare.com", "_cf_chl_opt", "cf-turnstile")

LADDER = [
    ("flat", {}),
    ("premium", {"premium": "true"}),
    ("ultra_premium", {"ultra_premium": "true"}),
]


def looks_blocked(html):
    low = html.lower()
    if any(s in low[:4000] for s in BLOCK_SIGNALS):
        return True
    if len(low) < 60000:
        return any(m in low for m in LATE_MARKERS)
    return False


def get_usage(key):
    """Account counters (best effort). Returns dict or None."""
    try:
        r = requests.get(f"{BASE}/account", params={"api_key": key}, timeout=30)
        if r.status_code == 200:
            return r.json()
    except (requests.RequestException, ValueError):
        pass
    return None


def estimate(key, url, extra):
    """Ask ScraperAPI what this request WOULD cost (no scrape)."""
    try:
        r = requests.get(f"{BASE}/account/urlcost",
                         params={"api_key": key, "url": url, **extra}, timeout=30)
        return f"HTTP {r.status_code}: {r.text.strip()[:300]}"
    except requests.RequestException as e:
        return f"failed: {e}"


def fmt_money(credits, plan):
    if plan and credits is not None:
        price, total = PLANS[plan]
        return f"  ~${credits * price / total:.4f} on the {plan} plan"
    return ""


def run_attempt(key, url, label, extra, max_cost, expect, plan):
    params = {"api_key": key, "url": url, **extra}
    if max_cost:
        params["max_cost"] = max_cost
    shown = {k: v for k, v in params.items() if k not in ("api_key", "url")}
    print(f"\n--- attempt: {label}  params={shown or 'none (flat)'}")

    before = get_usage(key)
    t0 = time.time()
    try:
        r = requests.get(f"{BASE}/", params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        print(f"    request error: {e}")
        return {"ok": False, "credits": None, "html": ""}
    elapsed = time.time() - t0
    after = get_usage(key)

    raw_cost = r.headers.get("sa-credit-cost")
    try:
        credits = float(raw_cost) if raw_cost is not None else None
    except ValueError:
        credits = None

    html = r.text if r.status_code == 200 else ""
    blocked = bool(html) and looks_blocked(html)
    missing = bool(expect) and bool(html) and not re.search(re.escape(expect), html)
    ok = r.status_code == 200 and not blocked and not missing

    print(f"    HTTP status      : {r.status_code}   ({elapsed:.1f}s, {len(r.content):,} bytes)")
    if credits is not None:
        credits_txt = f"{credits:g}"
        print(f"    credits charged  : {credits_txt}  (sa-credit-cost header){fmt_money(credits, plan)}")
    else:
        print("    credits charged  : header sa-credit-cost not present "
              "(normal for failed/403 responses, which are not billed)")
    if before and after and isinstance(before.get("requestCount"), (int, float)) \
            and isinstance(after.get("requestCount"), (int, float)):
        print(f"    account counter  : requestCount {before['requestCount']} -> "
              f"{after['requestCount']}  (delta {after['requestCount'] - before['requestCount']};"
              " may lag a few seconds)")
    if r.status_code == 403:
        print("    note: 403 = max_cost exceeded or the request was refused; not billed.")
    elif r.status_code not in (200, 404):
        print(f"    body: {r.text[:200].strip()!r}")
    if blocked:
        print("    RESULT: block/verification page returned. It was HTTP 200, so it IS billed "
              "but contains no real data.")
    elif missing:
        print(f"    RESULT: page returned but '{expect}' not found in it (wrong or partial page).")
    elif ok:
        print("    RESULT: real page returned.")
    return {"ok": ok, "credits": credits, "html": html}


def main():
    ap = argparse.ArgumentParser(description="ScraperAPI cost / credits checker (single URL)")
    ap.add_argument("api_key", nargs="?")
    ap.add_argument("url", nargs="?")
    ap.add_argument("--ladder", action="store_true")
    ap.add_argument("--premium", action="store_true")
    ap.add_argument("--ultra-premium", action="store_true")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--max-cost", type=int)
    ap.add_argument("--estimate-only", action="store_true")
    ap.add_argument("--plan", choices=sorted(PLANS))
    ap.add_argument("--expect")
    ap.add_argument("--save")
    a = ap.parse_args()

    # Allow `script.py URL` when the key comes from the environment.
    key, url = a.api_key, a.url
    if key and key.lower().startswith("http") and not url:
        key, url = None, key
    key = key or os.environ.get("SCRAPERAPI_KEY") or getpass.getpass("ScraperAPI key: ").strip()
    url = url or input("URL: ").strip()
    if not key or not url.lower().startswith("http"):
        sys.exit("Need an API key and a URL starting with http(s).")

    expect = a.expect or ("ng-state" if "crunchbase.com" in url.lower() else None)

    if a.ladder:
        steps = LADDER
    else:
        extra, label = {}, "flat"
        if a.ultra_premium:
            extra["ultra_premium"] = "true"
        if a.premium:
            extra["premium"] = "true"
        if a.render:
            extra["render"] = "true"
        if extra:
            label = "+".join(extra)
        steps = [(label, extra)]

    print(f"URL: {url}")
    if a.estimate_only:
        for label, extra in steps:
            print(f"estimate [{label}]: {estimate(key, url, extra)}")
        return

    start = get_usage(key)
    total, last_html, success = 0.0, "", False
    for label, extra in steps:
        print(f"estimate [{label}]: {estimate(key, url, extra)}")
        res = run_attempt(key, url, label, extra, a.max_cost, expect, a.plan)
        total += res["credits"] or 0
        last_html = res["html"] or last_html
        if res["ok"]:
            success = True
            break

    print("\n================ SUMMARY ================")
    print(f"outcome            : {'SUCCESS - real page' if success else 'NOT retrieved'}")
    print(f"credits charged    : {total:g}{fmt_money(total, a.plan)}")
    end = get_usage(key)
    if start and end:
        for k in ("requestCount", "requestLimit"):
            if k in end:
                print(f"account {k:<14}: {start.get(k)} -> {end.get(k)}")
        if isinstance(end.get("requestLimit"), (int, float)) and isinstance(end.get("requestCount"), (int, float)):
            print(f"credits remaining  : {end['requestLimit'] - end['requestCount']:,.0f}")
    if a.save and last_html:
        with open(a.save, "w", encoding="utf-8") as f:
            f.write(last_html)
        print(f"html saved to      : {a.save}")


if __name__ == "__main__":
    main()