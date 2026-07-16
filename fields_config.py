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
    
    "askmap.net": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Keywords", "Description", "Hours",
        "Category", "Logo",
    ],
    "earthmom.org": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description",
        "Hours", "Social Media Link", "GBP Link",
        "Category", "Logo", 
    ],
    "gravitysplash.com": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Description",
        "Social Media Links",
        "Category",
    ],
    "webforcompany.com": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Keywords", "Description",
        "Hours", "Social Media Links", "GBP Link",
        "Business Email", "Logo",
    ],
    "provenexpert.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
    "Phone", "Website URL", "Keywords", "Description",
    "Hours", "Social Media Links", "GBP Link", "Business Email",
    "Category", "Logo", "Photos",
    ],
    "zipleaf.us": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Keywords", "Description",
        "Hours", "Social Media Links", "GBP Link", "Business Email",
        "Category", "Logo",
    ],
    "cataloxy.us": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Keywords", "Description",
        "Social Media Links", "Business Email",
        "Category", "Logo",
    ],
    
    "fyple.com": [
        "Name",	"Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL",	"Keywords",	"Description",	"Hours",
        "Social Media Links",	"GBP Link",	"Business Email",	"Category",	"Logo",	"Photos",
    ],
    
    "merchantcircle.com": [
        "Name",	"Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL",	"Description",	"Hours",
        "Social Media Links",	"GBP Link",	"Category",	"Logo",
    ],
    
    "globalbusinessdirectory.us": [
        "Name",	"Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL",	"Keywords",	"Description",
        "Social Media Links",	"Category",	"Logo",
    ],
    "chamberofcommerce.com": [
        # No GBP Link: the only Google Maps references on this template
        # are a bare directions search-query link and a Maps Embed API
        # iframe URL carrying the page's own API key -- neither is a
        # real, shareable Google Business Profile link (see
        # parse_chamberofcommerce in extractor.py for details).
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Keywords", "Description", "Hours",
        "Social Media Links", "Business Email", "Category", "Logo", "Photos",
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
