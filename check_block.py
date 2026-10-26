"""Usage: python check_block.py out.html

Tells you WHY a saved page was flagged as a block/verification page:
which marker matched, where, and whether the real listing content is there.
Run it from the project root (it imports the tool's own detector).
"""
import re
import sys

from business_extractor.common import BLOCK_SIGNALS, _looks_blocked
import scraperapi_cost_check as cc

path = sys.argv[1] if len(sys.argv) > 1 else "out.html"
html = open(path, encoding="utf-8", errors="replace").read()
low = html.lower()

title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
print(f"file            : {path}  ({len(html):,} chars)")
title_text = re.sub(r"\s+", " ", title.group(1)).strip() if title else "(none)"
print(f"<title>         : {title_text}")
print(f"tool says       : {'BLOCKED' if _looks_blocked(html) else 'ok'}   "
      f"cost-check script says: {'BLOCKED' if cc.looks_blocked(html) else 'ok'}")


def show(label, needle):
    i = low.find(needle)
    if i >= 0:
        ctx = re.sub(r"\s+", " ", html[max(0, i - 80): i + 120])
        print(f"  MATCH [{label}] {needle!r} at char {i:,}:\n      ...{ctx}...")


print("\nMarkers found:")
for s in BLOCK_SIGNALS:
    if s in low[:4000]:
        show("tool, first 4000 chars", s)
for m in ("challenges.cloudflare.com", "_cf_chl_opt", "cf-turnstile"):
    if m in low:
        show("late marker", m)
for s in cc.BLOCK_SIGNALS:
    if s in low[:4000]:
        show("cost-check script", s)

print("\nReal-content check:")
for needle in ("business details", "<table", "business address", "telephone"):
    print(f"  {needle!r:22}: {'present' if needle in low else 'absent'}")