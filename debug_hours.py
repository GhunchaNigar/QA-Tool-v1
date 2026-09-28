"""
Diagnose ProvenExpert / ProvenEmployer opening-hours extraction.

Run from the project folder:
    python debug_hours.py https://www.provenexpert.com/en-us/tayriyan/
"""
import sys

from bs4 import BeautifulSoup

from business_extractor.common import (
    fetch_via_requests, fetch_via_playwright, pe_hours_need_render, _extract_pe_hours,
)

url = sys.argv[1] if len(sys.argv) > 1 else "https://www.provenexpert.com/en-us/tayriyan/"
print("URL:", url)

html = fetch_via_requests(url)
print("\n[1] plain fetch: hours box empty -> needs browser?", pe_hours_need_render(html))

try:
    rendered = fetch_via_playwright(url)
except Exception as e:
    print("\n[2] Playwright FAILED:", e)
    sys.exit(1)

soup = BeautifulSoup(rendered, "lxml")
box = soup.select_one("#profilesOpening")
print("\n[2] Playwright OK, rendered HTML length:", len(rendered))
print("    #profilesOpening found:", box is not None)
print("    #profilesOpening HTML:\n", (box.decode()[:2000] if box else "-"))

print("\n[3] parsed Hours:", repr(_extract_pe_hours(soup)))

with open("debug_rendered.html", "w", encoding="utf-8") as f:
    f.write(rendered)
print("\nFull rendered page saved to debug_rendered.html")