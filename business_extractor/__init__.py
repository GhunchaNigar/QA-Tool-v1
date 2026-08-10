"""
business_extractor -- multi-domain business directory scraper.

Public API (unchanged from the old single-file extractor.py):

    from business_extractor import extract_business, SITE_PARSERS

Layout:
    common.py        shared imports/constants/helpers/fetchers used by every parser
    parsers/          one module per site (parsers/<site>.py), each with a parse_xxx(url, html)
    dispatch.py       SITE_PARSERS dispatch table + extract_business()
"""

from .dispatch import extract_business, SITE_PARSERS

__all__ = ["extract_business", "SITE_PARSERS"]
