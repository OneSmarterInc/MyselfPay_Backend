"""Collect source-backed candidates from previously unseen public pricing pages."""
import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import unquote, urljoin, urlsplit

from lxml import html

from .providers import PRICE, node_text
from .prices import EXAM, NON_OFFER, PAIR, parse_value


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
PAY_CONTEXT = re.compile(r"\b(?:cash(?:[- ]pay)?|self[- ]pay|prompt[- ]pay)\b", re.I)
OFFER_SENTENCE = re.compile(
    rf"We offer (?P<label>[\w -]{{2,100}}) for (?P<value>{PRICE})"
    r"(?: \(this price may change[^$()]*\))?\.?(?: We require an order[^$.]*\.)?", re.I)
HEADING = re.compile(r"h[1-6]$")


def payment_contexts(doc):
    """Apply a section heading until a sibling or higher-level heading replaces it."""
    headings = {}
    contexts = {}
    for node in doc.iter():
        if not isinstance(node.tag, str):
            continue
        if HEADING.fullmatch(node.tag):
            level = int(node.tag[1])
            headings = {n: title for n, title in headings.items() if n < level}
            headings[level] = node_text(node)
        if any(NON_OFFER.search(title) for title in headings.values()):
            continue
        for title in reversed(headings.values()):
            if PAY_CONTEXT.search(title):
                contexts[node] = title
                break
    return contexts


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
    title = doc.xpath("string(//meta[@property='og:site_name']/@content)") or doc.xpath("string(//title)") or urlsplit(url).hostname
    candidates = []
    seen = set()

    contexts = payment_contexts(doc)

    def add(label, evidence, parsed=None, context=""):
        if (evidence, context) in seen:
            return
        seen.add((evidence, context))
        if NON_OFFER.search(label):
            parsed = None
        low, high, kind = parsed or (None, None, "unconfirmed")
        if parsed and re.search(r"\b(?:add[- ]on|each additional|additional fee)\b", label, re.I):
            kind = "add_on" if high is None and kind == "listed" else "unconfirmed"
            if kind == "unconfirmed":
                low = high = None
        normalized = re.sub(r"\bw/\s*&\s*w/o\b", "with and without", label, flags=re.I)
        normalized = re.sub(r"\bw/o\b", "without", normalized, flags=re.I)
        normalized = re.sub(r"\bw/(?=\s)", "with", normalized, flags=re.I)
        contrast_words = re.search(r"\b(with(?:out)?\s+(?:and|&)\s+with(?:out)?|without|with)\s+contrast\b", normalized, re.I)
        contrast = "unknown"
        if re.search(r"\bwith(?:out)?\s+(?:or|/)\s*with(?:out)?\s+contrast", normalized, re.I):
            contrast = "unknown"
        elif contrast_words:
            wording = contrast_words[1].lower()
            contrast = wording if wording in ("with", "without") else "both"
        elif re.search(r"\bnon[- ]?contrast\b", normalized, re.I):
            contrast = "without"
        candidates.append({"provider_page_title": title, "page_contacts": contacts,
                           "contrast": contrast, "exam": label, "evidence": evidence, "source_url": url,
                           "checked_at": checked, "status": "needs_review",
                           "amount_cents": low, "max_amount_cents": high, "price_type": kind,
                           "payment_context": context})

    for table in doc.xpath("//table"):
        headers = []
        simple = not table.xpath(".//*[@rowspan or @colspan] | .//table")
        for row in table.xpath(".//tr"):
            if row.xpath("ancestor::table[1]")[0] != table:
                continue
            cells = row.xpath("./th|./td")
            values = [node_text(cell) for cell in cells]
            if cells and (all(cell.tag == "th" for cell in cells) or (len(values) >= 2 and
                    not any("$" in v for v in values) and any(re.search(r"insurance|cash|self.pay|price|charge|deposit|rate", v, re.I) for v in values))):
                headers = values if all(cell.get("colspan","1")==cell.get("rowspan","1")=="1" for cell in cells) else []
                continue
            evidence = " | ".join(values)
            cash = [i for i, heading in enumerate(headers) if CASH.fullmatch(heading)]
            explicit_usd = len(cash) == 1 and "USD" in headers[cash[0]].upper()
            if not EXAM.search(evidence) or ("$" not in evidence and not explicit_usd):
                continue
            label = next(v for v in values if EXAM.search(v))
            context = contexts.get(row, "")
            parsed = None
            if simple and len(values) == len(headers) and len(cash) == 1:
                context = headers[cash[0]]
                value = values[cash[0]]
                if explicit_usd and re.fullmatch(r"\d+(?:\.\d{2})?", value):
                    value = "$" + value
                parsed = parse_value(value)
            elif simple and len(values) == 2 and label == values[0] and (
                not any(headers) or (len(headers) == 2 and
                                     headers[1].lower() in ("price", "amount", "rate", "price (usd)"))
            ):
                value = values[1]
                main, marker, note = value.partition("*")
                if marker and re.fullmatch(r"\s*Please note that cost may vary dependent upon [\w -]+\.?", note, re.I):
                    value = main
                parsed = parse_value(value)
            if "$" in label:
                parsed = None
            add(label, " | ".join(headers) + " / " + evidence, parsed, context)

    for node in doc.xpath("//p|//li[not(.//li)]|//div[not(div or p or table or ul or ol)]"):
        if node.xpath("ancestor::table"):
            continue
        value = node_text(node)
        if len(value) <= 600 and EXAM.search(value) and "$" in value:
            match = PAIR.fullmatch(value) or OFFER_SENTENCE.fullmatch(value)
            if match and EXAM.search(match["label"]) and not NON_OFFER.search(match["label"]):
                label = match["label"].strip()
                context = label if PAY_CONTEXT.search(label) else contexts.get(node, "")
                add(label, value, parse_value(match["value"]), context)
            else:
                add(value, value)
    for term in doc.xpath("//dt"):
        label = node_text(term)
        description = term.xpath("following-sibling::*[1][self::dd]")
        if EXAM.search(label) and description:
            value = node_text(description[0])
            if "$" in value:
                add(label, label + " | " + value, parse_value(value), contexts.get(term, ""))

    for block in doc.xpath("//section|//article|//div"):
        children = block.xpath("./h2|./h3|./h4")
        if len(children) != 1 or not EXAM.search(node_text(children[0])):
            continue
        value = " ".join(" ".join(block.itertext()).split())
        if len(value) <= 500 and "$" in value:
            label = node_text(children[0])
            tail = value.removeprefix(label).strip() if value.startswith(label) else ""
            add(label, value, parse_value(tail), contexts.get(children[0], ""))
    return candidates


def price_links(content, source):
    """Prioritize explicit self-pay links, then general pricing links on this host."""
    doc = html.document_fromstring(content)
    ranked = {}
    for link in doc.xpath("//a[@href]"):
        url = urljoin(source, link.get("href")).split("#")[0]
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != urlsplit(source).netloc or url == source:
            continue
        words = (node_text(link) + " " + parts.path).lower()
        if re.search(r"self[- ]pay|cash[- ](?:price|pricing|rate)", words):
            rank = 0
        elif re.search(r"\bpric(?:es|ing|e list)\b", words):
            rank = 1
        else:
            continue
        ranked[url] = min(rank, ranked.get(url, rank))
    return sorted(ranked, key=ranked.get)[:3]
