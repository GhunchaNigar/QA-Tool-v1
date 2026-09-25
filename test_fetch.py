from business_extractor.common import fetch_via_requests

url = "https://www.bizmaker.org/business-services/wrightway-emergency-services"

try:
    html = fetch_via_requests(url)
    print("HTML LENGTH:", len(html))
    print("---- FIRST 2000 CHARS ----")
    print(html[:2000])
    print("---- CONTAINS KEY MARKERS? ----")
    print("header-member-name:", "header-member-name" in html)
    print("phone_number:", "phone_number" in html)
    print("profile-image:", "profile-image" in html)
except Exception as e:
    print("FETCH FAILED WITH ERROR:")
    print(repr(e))