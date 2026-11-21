"""Collect source-backed candidates from previously unseen public pricing pages."""
import http.client
import ipaddress
import re
import socket
import ssl
from decimal import Decimal
from urllib.parse import unquote, urljoin, urlsplit

from lxml import html

from .providers import PRICE, node_text


def fetch_public(url, limit):
    for _ in range(5):
        parts = urlsplit(url)
        host = parts.hostname
        if parts.scheme != "https" or not host or parts.username is not None or parts.port not in (443, None):
            raise ValueError("Use a public HTTPS pricing page")
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        for family, _, _, _, address in addresses:
            ip = ipaddress.ip_address(address[0])
            if not ip.is_global or ip.is_multicast or (ip.version == 6 and ip.ipv4_mapped):
                raise ValueError("Source must have a public address")
        conn = http.client.HTTPSConnection(host, timeout=12)
        try:
            # Pin the validated address while retaining hostname verification and SNI.
            raw = socket.create_connection((addresses[0][4][0], 443), timeout=12)
            try:
                conn.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
            except BaseException:
                raw.close()
                raise
            target = parts.path or "/"
            if parts.query:
                target += "?" + parts.query
            conn.request("GET", target, headers={"User-Agent": "MySelfPayPriceReview/0.2"})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308) and response.getheader("Location"):
                url = urljoin(url, response.getheader("Location"))
                continue
            if response.status != 200:
                raise ValueError(f"Source returned HTTP {response.status}")
            if response.headers.get_content_type() != "text/html":
                raise ValueError("This source needs document review")
            content = response.read(limit + 1)
            if len(content) > limit:
                raise ValueError("Source exceeds size limit")
            return content, url
        finally:
            conn.close()
    raise ValueError("Too many redirects")


CASH = re.compile(
    r"(?:self[- ]pay|cash(?:[- ]pay)?|prompt[- ]pay)(?: (?:price|rate|amount))?(?: \(USD\))?",
    re.I,
)
EXAM = re.compile(r"\b(?:MRI|CT)\b", re.I)


def review_page(content, url, checked):
    doc = html.document_fromstring(content)
    for node in doc.xpath("//script|//style|//template|//noscript|//*[@hidden]"):
        node.drop_tree()
    contacts = {}
    for field, scheme in (("phones", "tel:"), ("emails", "mailto:")):
        contacts[field] = list(dict.fromkeys(
            unquote(link[len(scheme):].split("?")[0])
            for link in doc.xpath("//a/@href") if link.lower().startswith(scheme)
        ))
    contacts["addresses"] = [node_text(n) for n in doc.xpath("//address") if node_text(n)]
    title = doc.xpath("string(//meta[@property='og:site_name']/@content)") or doc.xpath("string(//title)")
    candidates = []
    seen = set()

    def add(label, evidence, amount=None):
        if evidence in seen:
            return
        seen.add(evidence)
        contrast_words = re.search(r"\b(with(?:out)?\s+(?:and|&)\s+with(?:out)?|without|with)\s+contrast\b", label, re.I)
        contrast = "unknown"
        if contrast_words:
            wording = contrast_words[1].lower()
            contrast = wording if wording in ("with", "without") else "both"
        candidates.append({"provider_page_title": title, "page_contacts": contacts,
                           "contrast": contrast, "exam": label, "evidence": evidence, "source_url": url,
                           "checked_at": checked, "status": "needs_review",
                           "amount_cents": int(Decimal(amount[1:].replace(",", "")) * 100) if amount else None})

    for table in doc.xpath("//table"):
        headers = []
        for row in table.xpath(".//tr"):
            if row.xpath("ancestor::table[1]")[0] != table:
                continue
            cells = row.xpath("./th|./td")
            values = [node_text(cell) for cell in cells]
            if cells and all(cell.tag == "th" for cell in cells):
                headers = values if all(cell.get("colspan","1")==cell.get("rowspan","1")=="1" for cell in cells) else []
                continue
            evidence = " | ".join(values)
            if not EXAM.search(evidence) or "$" not in evidence:
                continue
            cash = [i for i, heading in enumerate(headers) if CASH.fullmatch(heading)]
            amount = None
            if len(values) == len(headers) and len(cash) == 1 and not table.xpath(".//*[@rowspan or @colspan]"):
                value = values[cash[0]]
                if re.fullmatch(PRICE, value):
                    amount = value
            add(next(v for v in values if EXAM.search(v)), " | ".join(headers) + " / " + evidence, amount)

    for node in doc.xpath("//p|//li[not(.//li)]|//div[not(div or p or table or ul or ol)]"):
        if node.xpath("ancestor::table"):
            continue
        value = node_text(node)
        if len(value) <= 600 and EXAM.search(value) and "$" in value:
            add(value, value)
    for block in doc.xpath("//section|//article|//div"):
        children = block.xpath("./h2|./h3|./h4")
        if len(children) != 1 or not EXAM.search(node_text(children[0])):
            continue
        value = " ".join(" ".join(block.itertext()).split())
        if len(value) <= 500 and "$" in value:
            add(node_text(children[0]), value)
    return candidates
