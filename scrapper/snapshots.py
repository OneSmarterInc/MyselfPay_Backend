"""Match locally saved provider HTML to exactly one uploaded source URL."""
import re
from urllib.parse import urldefrag, urlsplit
from lxml import html


def match_snapshots(files, urls):
    matched = {}
    for raw in files:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Saved provider pages must contain HTML")
        doc = html.document_fromstring(raw)
        saved = re.search(r"<!--\s*saved from url=\(\d+\)(https://[^\s<>]+)\s*-->", raw[:2048], re.I)
        refs = [saved[1]] if saved else doc.xpath(
            "//link[contains(concat(' ',normalize-space(@rel),' '),' canonical ')]/@href|//meta[@property='og:url']/@content")
        exact = {urldefrag(ref)[0] for ref in refs if urlsplit(ref).scheme == "https"} & set(urls)
        if len(exact) != 1:
            raise ValueError("Each saved HTML page needs an original or canonical URL matching one pricing_url exactly")
        url = exact.pop()
        if url in matched:
            raise ValueError("Upload one saved HTML copy per pricing_url")
        matched[url] = raw
    return matched
