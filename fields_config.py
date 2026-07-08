"""
fields_config.py
Field lists for each supported business directory source.
No layout assumptions — Gemini finds fields anywhere on the page.
"""

ALL_FIELDS = [
    "Name", "Street", "City", "State", "Zipcode", "Country",
    "Phone", "Website URL", "Keywords", "Description",
    "Hours", "Social Media Links", "GBP Link", "Business Email",
    "Category", "Logo", "Photos",
]

SOURCE_FIELDS = {
    "nearfinderus.com":[
        "Name",	"Street",	"City",	"State",	"Zipcode",	"Country",	"Phone",	"Website URL",
        "Description",	"Hours",	"Social Media Links",	"GBP Link",	"Business Email",	"Category",	"Logo"
    ],
    "smallbusinessusa.com":[
        "Name",	"Street",	"City",	"State", "Zipcode", "Country",
        "Phone", "Website URL", "Category", 
    ],
    "zeemaps.com": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Description", "Business Email",
        "Logo",
    ],
    "callupcontact.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Keywords", "Description",
        "Hours", "Business Email",
    ],
    "zumvu.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Keywords", "Description",
        "Hours", "Social Media Links",
        "Category", "Logo",
    ],
    "blinx.biz": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Business Email",
        "Logo",
    ],
    "place123.net": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Keywords", "Description",
        "Hours", "Business Email",
        "Category", "Logo",
    ],
    "freelistingusa.com": [
        "Name",	"Street",	"City",	"State",	"Zipcode",
       "Phone",	"Website URL",	"Keywords",	"Description",	"Hours",	"Social Media Links",
        "Business Email",	"Category",	"Logo"
    ],
    
    
    
}

VISUAL_FIELDS = {"Logo", "Photos"}

NA_OVERRIDES = {}

# No site-specific layout hints — Gemini searches the whole page for each field.
SOURCE_PROMPT_HINTS = {}


def detect_source(url: str) -> str:
    """Auto-detect directory source from URL. Returns SOURCE_FIELDS key or None."""
    url_lower = url.lower()
    for source_key in SOURCE_FIELDS:
        if source_key.replace("www.", "") in url_lower:
            return source_key
    return None