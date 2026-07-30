"""
fields_config.py
Field lists for each supported business directory source.
No layout assumptions — Gemini finds fields anywhere on the page.
"""

ALL_FIELDS = [
    "Name", "Owner Name", "Street", "City", "State", "Zipcode", "Country",
    "Phone", "Website URL", "Keywords", "Description",
    "Hours", "Social Media Links", "GBP Link", "Business Email",
    "Category", "Logo", "Photos",
]

SOURCE_FIELDS = {
    "bpublic.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Hours", "Category", "Logo",
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
        "Name", "Owner Name", "Street", "City", "State", "Zipcode", "Country",
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
        "Hours", "Social Media Links", "GBP Link",
        "Category", "Logo", 
    ],
    "gravitysplash.com": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Description",
        "Social Media Links",
        "Category",
    ],
    "webforcompany.com": [
        "Name", "Owner Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Keywords", "Description",
        "Hours", "Social Media Links", "GBP Link",
        "Business Email", "Logo",
    ],
    "provenexpert.com": [
        "Name", "Owner Name", "Street", "City", "State", "Zipcode", "Country",
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
        "Name", "Owner Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Keywords", "Description", "Hours",
        "Social Media Links", "Business Email", "Category", "Logo", "Photos",
    ],
    "trueen.com": [
        "Name", "Owner Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Hours",
        "Social Media Links", "Category",
    ],
    "citysquares.com": [
        "Name", "Street", "City", "State", "Zipcode",
    ],
    "b2bco.com": [
        "Name", "Street", "City", "State", "Country",
        "Phone", "Website URL", "Keywords", "Description",
        "Hours", "Business Email", "Category", "Logo",
    ],
    "find-us-here.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Business Email",
        "Category", "Logo",
    ],
    "a-zbusinessfinder.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Business Email",
        "Category", "Logo",
    ],
    "cybo.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Hours",
        "Social Media Links", "GBP Link", "Category", "Logo",
    ],
    "linkcentre.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Social Media Links",
        "Business Email", "Category", "Logo",
    ],
    "band.us": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Business Email", "Description", "Keywords",
    ],
    "americansearch.info": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Category", "Logo",
    ],
    "listings.globalbusinessdirectory.us": [
        "Name", "Owner Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Hours",
        "Social Media Links", "Business Email", "Category",
    ],
    "usa.globalbusinessdirectory.us": [
        "Name", "Owner Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Hours",
        "Social Media Links", "Business Email", "Category",
    ],
    "cities.globalbusinessdirectory.us": [
        "Name", "Owner Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Hours",
        "Social Media Links", "Business Email", "Category",
    ],
    "local.globalbusinessdirectory.us": [
        "Name", "Owner Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description",
        "Social Media Links", "Business Email", "Category",
    ],
    "blogs.globalbusinessdirectory.us": [
        "Name", "Owner Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Keywords", "Description",
        "Business Email", "Logo",
    ],
    "n49.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Hours",
        "Social Media Links", "Business Email", "Category",
        "Logo", "Photos",
    ],
    "bizhwy.com": [
        "Name", "Street", "City", "State", "Zipcode", "Phone", "Category",
    ],
    "yplocal.com": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Keywords", "Description", "Hours",
        "Social Media Links", "GBP Link", "Category", "Logo",
    ],
    "golocalezservices.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Category", "Logo",
    ],
    "findabusinesspro.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Category", "Logo",
    ],
    "globeconnected.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Website URL", "Description", "Category", "Logo",
    ],
    "whatsyourhours.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Hours",
        "Social Media Links", "GBP Link", "Business Email", "Category", "Logo",
    ],
    "milestones.business": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Category", "Logo",
    ],
    "iformative.com": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Category",
    ],
    "thebusinessminded.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Category", "Logo",
    ],
    "cleansway.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Category", "Logo",
    ],
    "preferredprofessionals.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Category", "Logo",
    ],
    "bestdealfinder.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Business Email",
        "GBP Link", "Category", "Logo",
    ],
    
    "911getit.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Category",
        "Logo", "Social Media Links",
    ],
    "touchafro.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Social Media Links",
        "Category", "Logo",
    ],
    "supplyautonomy.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Social Media Links", "Logo",
    ],
    "mybusinessplaces.com": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Description", "Hours", "Category",
    ],
    "local-biz.directory": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Keywords", "Description",
        "Category", "Logo",
    ],
    "vetslist.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description",
        "GBP Link", "Category", "Logo",
    ],
    "vymaps.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Keywords", "Description",
        "Business Email", "Category", "GBP Link", "Photos",
    ],
    "wireanium.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Hours",
        "Social Media Links", "Business Email", "GBP Link",
        "Category", "Logo",
    ],
    "locuul.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Hours",
        "Social Media Links", "Business Email", "GBP Link",
        "Category", "Logo",
    ],
    "dbesearch.com": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Category", "Logo",
    ],
    "qdexx.com": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Description", "Hours", "Category",
    ],
    "letsknowit.com": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Description", "Business Email",
        "Logo", "Photos",
    ],
    "metriteweb.com": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Description", "Category", "Logo",
    ],
    "closelocation.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Category", "Logo", "Photos",
    ],
    "trustburn.com": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Description", "Logo",
    ],
    "searchmypro.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Description", "Hours",
        "Social Media Links", "Category", "Logo",
    ],
    "yourbizlistings.com": [
        "Name", "Street", "City", "State", "Zipcode",
        "Phone", "Website URL", "Description", "Hours",
        "Business Email", "Category", "Logo",
    ],
    "bulkpostads.com": [
        "Name", "Street", "City", "State", "Zipcode", "Country",
        "Phone", "Website URL", "Keywords", "Description", "Hours",
        "Business Email", "Category", "Logo", "Photos",
    ],
}

VISUAL_FIELDS = {"Logo", "Photos"}

NA_OVERRIDES = {}

# No site-specific layout hints — Gemini searches the whole page for each field.
SOURCE_PROMPT_HINTS = {}


def detect_source(url: str) -> str:
    """Auto-detect directory source from URL. Returns SOURCE_FIELDS key or None.

    Some source keys are substrings of others (e.g. "globalbusinessdirectory.us"
    is a substring of "listings.globalbusinessdirectory.us"). To avoid a
    shorter/less-specific key stealing the match, collect every key that
    matches and return the longest (most specific) one.
    """
    url_lower = url.lower()
    matches = [
        source_key for source_key in SOURCE_FIELDS
        if source_key.replace("www.", "") in url_lower
    ]
    if not matches:
        return None
    return max(matches, key=len)
