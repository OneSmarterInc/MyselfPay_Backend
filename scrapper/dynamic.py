"""Collect source-backed candidates from previously unseen public pricing pages."""
import re
from urllib.parse import unquote, urljoin, urlsplit

from lxml import html

from .body_parts import CONTRAST as CONTRAST_PHRASE, split_exclusions
from .providers import PRICE, node_text
from .menus import provider_comparison, read_branch_grids, read_card_grids, read_selectors, read_single_service
from .pdf_prices import read_schedule
from .prices import EXAM, NON_OFFER, PAIR, parse_value


CASH = re.compile(
    r"(?:self[- ]pay|cash(?:[- ]pay)?|prompt[- ]pay)(?: (?:price|rate|amount))?(?: \(USD\))?",
    re.I,
)
PAY_CONTEXT = re.compile(r"\b(?:cash(?:[- ]pay)?|self[- ]pay|prompt[- ]pay)\b", re.I)
OFFER_SENTENCE = re.compile(
    rf"We offer (?P<label>[\w -]{{2,100}}) for (?P<value>{PRICE})"
    r"(?: \(this price may change[^$()]*\))?\.?(?: We require an order[^$.]*\.)?", re.I)
HEADING = re.compile(r"h[1-6]$")


DESCRIPTORS = {"exam", "exam name", "procedure", "service", "modality", "scan", "body part",
               "body region", "body area", "region", "body site", "anatomy", "contrast", "protocol", "variant", "description", "notes",
               "restrictions", "exclusions"}
PROTOCOLS = {
    "without contrast": "without contrast", "with contrast": "with contrast",
    "with and without contrast": "with and without contrast",
    "without and with contrast": "with and without contrast",
}


def protocol_header(value):
    return PROTOCOLS.get(" ".join(value.lower().replace("&", "and").split()))


def exam_table_label(headers, values):
    """Combine named columns so a modality-only cell never swallows anatomy or protocol."""
    pieces = []
    for header, value in zip(headers, values):
        name = header.casefold()
        if not value:
            continue
        if name not in DESCRIPTORS:
            # Preserve unfamiliar text qualifiers beside an exam. Dropping a
            # package or anatomy column could turn its rate into a general rate.
            if not name or CASH.fullmatch(header) or protocol_header(header) or "$" in value:
                continue
            if re.search(r"[a-z]", value, re.I):
                pieces.append(f"({header}: {value})")
            continue
        if name == "contrast" and value.casefold() in {"with", "without", "both"}:
            value = "with and without contrast" if value.casefold() == "both" else value + " contrast"
        if name == "exclusions":
            value = "(excludes " + value + ")"
        pieces.append(value)
    return " ".join(pieces)


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


def review_page(content, url, checked, *, interactive=False, notes=None, image_fetch=None):
    pdf = isinstance(content, bytes) and content.lstrip().startswith(b"%PDF-")
    doc = html.document_fromstring(b"<html></html>" if pdf else content)
    for node in doc.xpath("//script|//style|//template|//noscript"):
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

    def add(label, evidence, parsed=None, context="", provider=None):
        if re.search(r"\bCT\s*/\s*NG\b|\bchlamydia\b", label, re.I):
            return
        identity = (provider, label, evidence, context)
        if identity in seen:
            return
        seen.add(identity)
        positive, _ = split_exclusions(label)
        if NON_OFFER.search(label) or len({m.upper() for m in EXAM.findall(positive)}) != 1 or "$" in label:
            parsed = None
        low, high, kind = parsed or (None, None, "unconfirmed")
        if parsed and re.search(r"\b(?:add[- ]on|each additional|additional fee)\b", label, re.I):
            kind = "add_on" if high is None and kind == "listed" else "unconfirmed"
            if kind == "unconfirmed":
                low = high = None
        positive = re.sub(r"\bw\s+(?=(?:and|contrast)\b)", "with ", positive, flags=re.I)
        normalized = re.sub(r"\bw/\s*(?:&|and)\s*w/o\b", "with and without", positive, flags=re.I)
        normalized = re.sub(r"\bw/o\b", "without", normalized, flags=re.I)
        normalized = re.sub(r"\bw/(?=\s)", "with", normalized, flags=re.I)
        contrast_words = re.findall(r"\b(with(?:out)?\s+(?:and|&)\s+with(?:out)?|without|with)\s+contrast\b", normalized, re.I)
        contrast = "unknown"
        if re.search(r"\bor\b", positive, re.I) and re.search("contrast", positive, re.I):
            contrast = "unknown"
        elif re.search(r"\bwith(?:out)?\s+(?:or|/)\s*with(?:out)?\s+contrast", normalized, re.I):
            contrast = "unknown"
        elif len(contrast_words) == 1:
            wording = contrast_words[0].lower()
            contrast = wording if wording in ("with", "without") else "both"
        elif not contrast_words and re.search(r"\bnon[- ]?contrast\b", normalized, re.I):
            contrast = "without"
        candidates.append({"provider_page_title": provider or title, "page_contacts": contacts,
                           "contrast": contrast, "exam": label, "evidence": evidence, "source_url": url,
                           "checked_at": checked, "status": "needs_review",
                           "amount_cents": low, "max_amount_cents": high, "price_type": kind,
                           "payment_context": context})

    if pdf:
        read_schedule(content, add)
        return candidates

    # Inactive options are often already delivered as separate HTML panels.
    # Read only explicitly linked self-pay panels before removing hidden/insured content.
    handled = read_selectors(doc, add)
    for node in doc.xpath("//*[@id or @hidden or @aria-hidden='true']"):
        if (node.get("id") in handled or "hidden" in node.attrib or node.get("aria-hidden") == "true") and node.getparent() is not None:
            node.drop_tree()
    read_card_grids(doc, contexts, add)
    read_branch_grids(doc, add)
    read_single_service(doc, add)

    for table in doc.xpath("//table"):
        if provider_comparison(table, title, add):
            continue
        headings = table.xpath("./caption") or table.xpath("preceding-sibling::*[1][self::h2 or self::h3 or self::h4]")
        section = node_text(headings[0]) if headings else ""
        table_modality = section if re.fullmatch(r"(MRI|CT)(?: scans?| exams?)?", section, re.I) else ""
        headers = []
        simple = not table.xpath(".//*[@rowspan and @rowspan!='1' or @colspan and @colspan!='1'] | .//table")
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
            # A US provider's explicitly cash-priced table may omit currency signs.
            bare_price = (simple and bool(contexts.get(row)) and len(headers) == len(values)
                          and len(headers) == 2 and headers[1].lower() in ("price", "cash price", "self-pay price")
                          and re.fullmatch(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{2})?", values[1]))
            if "$" not in evidence and not explicit_usd and not bare_price:
                continue
            label = exam_table_label(headers, values) if len(headers) == len(values) else ""
            if not label:
                label = next((v for v in values if EXAM.search(v)), "")
            if label and table_modality and not EXAM.search(label):
                label = table_modality + " " + label
                evidence = section + " / " + evidence
            if not EXAM.search(label):
                continue
            context = contexts.get(row, "")
            parsed = None
            protocol_columns = [(i, protocol_header(h)) for i, h in enumerate(headers) if protocol_header(h)]
            if simple and context and len(headers) == len(values) and protocol_columns and not CONTRAST_PHRASE.search(label) and all(
                    h.casefold() in DESCRIPTORS or protocol_header(h) for h in headers):
                for i, protocol in protocol_columns:
                    add(label + " " + protocol, " | ".join(headers) + " / " + evidence + " / " + headers[i],
                        parse_value(values[i]), context)
                continue
            if simple and len(values) == len(headers) and len(cash) == 1:
                context = headers[cash[0]]
                value = values[cash[0]]
                if explicit_usd and re.fullmatch(r"\d+(?:\.\d{2})?", value):
                    value = "$" + value
                parsed = parse_value(value)
            elif (simple and context and len(headers) == len(values)
                  and sum(h.casefold() in {"price", "amount", "rate"} for h in headers) == 1
                  and all(h.casefold() in DESCRIPTORS | {"price", "amount", "rate"} for h in headers)):
                index = next(i for i, h in enumerate(headers) if h.casefold() in {"price", "amount", "rate"})
                parsed = parse_value(values[index])
            elif simple and len(values) == 2 and label == values[0] and (
                not any(headers) or (len(headers) == 2 and
                                     headers[1].lower() in ("price", "amount", "rate", "price (usd)"))
            ):
                value = values[1]
                main, marker, note = value.partition("*")
                if marker and re.fullmatch(r"\s*Please note that cost may vary dependent upon [\w -]+\.?", note, re.I):
                    value = main
                parsed = parse_value(value)
            if bare_price:
                parsed = parse_value("$" + values[1])
                context = contexts[row]
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
    if image_fetch is not None:
        from .image_prices import image_schedule, schedule_rows
        from http.client import HTTPException
        image_url = image_schedule(content, url)
        if image_url:
            try:
                blob, final, _ = image_fetch(image_url, 5_000_000, origin=url, formats={"image/png", "image/jpeg"})
                read = schedule_rows(blob)
                for number, label, rate in read:
                    exam = re.sub(r"^MR (?=(?:joint )?arthrogram)", "MRI ", label, flags=re.I)
                    add(exam, f"Image row {number} | {label} | {rate} | {final}", parse_value(rate), "Provider cash pricing schedule")
                    candidates[-1].update(evidence_origin="provider_image", evidence_url=final)
                if notes is not None:
                    notes.append(f"Read linked image: {len(read)} matching OCR rows")
            except (OSError, HTTPException, ValueError) as exc:
                if notes is not None:
                    notes.append(str(exc))
    if interactive:
        from .browser_prices import explore, needs_browser
        if needs_browser(content, candidates, url):
            known = {r["exam"].casefold() for r in candidates if r["amount_cents"] is not None and r["payment_context"]}
            extra, detail = explore(content, url, known)
            for label, evidence, amount, context in extra:
                add(label, evidence, amount, context)
            if notes is not None:
                notes.append(detail)
    return candidates


def price_links(content, source, *, options_only=False):
    """Prioritize explicit self-pay links, then general pricing links on this host."""
    if isinstance(content, bytes) and content.lstrip().startswith(b"%PDF-"):
        return []
    doc = html.document_fromstring(content)
    ranked = {}
    for link in doc.xpath("//a[@href]|//select/option[@value]"):
        target = link.get("href") or link.get("value") or ""
        if options_only and link.tag != "option":
            continue
        if link.tag == "option" and not (target.startswith(("/", "https://", "?")) and EXAM.search(node_text(link))):
            continue
        url = urljoin(source, target).split("#")[0]
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != urlsplit(source).netloc or url == source:
            continue
        words = (node_text(link) + " " + parts.path).lower()
        if link.tag == "option":
            rank = -1
        elif re.search(r"self[- ]pay|cash[- ](?:price|pricing|rate)", words):
            rank = 0
        elif re.search(r"\bpric(?:es|ing|e list)\b", words):
            rank = 1
        elif re.fullmatch(r"(?:cardiac|coronary) calcium (?:scoring|scan)", node_text(link), re.I):
            rank = 2
        else:
            continue
        ranked[url] = min(rank, ranked.get(url, rank))
    return [u for u, rank in ranked.items() if rank == -1][:24] + [
        u for u in sorted(ranked, key=ranked.get) if ranked[u] >= 0][:3]


def page_hint(content, source):
    if isinstance(content, bytes) and content.lstrip().startswith(b"%PDF-"):
        return "PDF opened; no supported self-pay schedule identified"
    doc = html.document_fromstring(content)
    for node in doc.xpath("//script|//style"):
        node.drop_tree()
    for image in doc.xpath("//img[@src]"):
        attr = image.get("src", "") + " " + image.get("alt", "")
        if re.search(r"(?:cash|self[- ]pay)[- ]pric", attr, re.I):
            target = urljoin(source, image.get("src"))
            if urlsplit(target).scheme == "https":
                return f"Price schedule published as an image; inspect the original: {target}"
    body = " ".join(" ".join(doc.itertext()).split())
    if re.search(r"personalized quote|confirm the price for your specific scan", body, re.I):
        return "Provider requests a personalized quote; no numeric self-pay rate identified"
    if "Service Locator" in body and "Request Appointment" in body:
        return "Service locator lists appointments and facilities; no numeric price identified"
    if "self-pay service" in body.lower() and "$" not in body:
        return "Self-pay eligibility listed; no amount displayed"
    return "Page opened; no explicit self-pay exam price identified"
