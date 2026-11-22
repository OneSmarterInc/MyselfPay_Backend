"""Read provider comparison columns, service selectors and visually tabular price lists."""
import re

from .prices import EXAM, parse_value
from .providers import PRICE

CASH_HEADING = re.compile(r"(?:self[- ]pay|cash(?:[- ]pay)?)(?:\s+(?:prices?|pricing|rates?))?", re.I)
QUALIFIED = re.compile(rf"({PRICE})\s*[-–—]\s*(?:all\s+)?(non[- ]contrast|without contrast|with contrast)\s+scans?", re.I)


def text(node):
    return " ".join(" ".join(node.itertext()).split())


def payment_section(node):
    for section in node.iterancestors():
        headings = [text(h) for h in section.xpath("./h1|./h2|./h3|./h4")]
        if any(re.search(r"\binsured\b|\binsurance\b", h, re.I) for h in headings):
            return "insured"
        if any(CASH_HEADING.fullmatch(h) for h in headings):
            return next(h for h in headings if CASH_HEADING.fullmatch(h))
    return ""


def read_selectors(doc, add):
    panels = {n.get("id"): n for n in doc.xpath("//*[@id]")}
    handled = set()
    for control in doc.xpath("//option|//*[@role='tab' or @aria-controls or @data-toggle='tab' or @data-bs-toggle='tab']"):
        label = text(control)
        if not EXAM.search(label):
            continue
        ref = (control.get("aria-controls") or control.get("data-bs-target") or control.get("data-target")
               or control.get("href") or control.get("value") or "").removeprefix("#")
        panel = panels.get(ref)
        if control.tag == "option" and panel is None:
            context = payment_section(control)
            inline = re.fullmatch(rf"([^$]+?)\s*(?:[:|]|[-–—])\s*({PRICE})", label)
            if context and context != "insured" and inline:
                add(inline[1].strip(), label, parse_value(inline[2]), context)
        if panel is None or ref in handled:
            continue
        context = payment_section(panel)
        if context == "insured":
            handled.add(ref)
            continue
        if not context:
            continue
        headings = [text(h) for h in panel.xpath(".//h2|.//h3|.//h4|.//strong")]
        if label.casefold() not in [h.casefold() for h in headings]:
            continue
        rates = [h for h in headings if re.fullmatch(PRICE, h)]
        # Use the displayed total; separate notes may mention contrast or other fees.
        markers = {h.casefold() for h in headings}
        if len(rates) != 1 or not markers.intersection({"you pay", "estimated total cost", "self-pay price", "cash price"}):
            continue
        amount, high, kind = parse_value(rates[0])
        if "estimated total cost" in markers:
            kind = "estimate"
        evidence = text(panel)
        add(label, evidence, (amount, high, kind), context + " — " + evidence)
        handled.add(ref)
    return handled


def provider_comparison(table, title, add):
    rows = [[text(c) for c in r.xpath("./td|./th")] for r in table.xpath("./tr|./thead/tr|./tbody/tr")]
    if not rows or len(rows[0]) < 3 or table.xpath(".//*[@rowspan or @colspan]"):
        return False
    matching = [i for i, h in enumerate(rows[0]) if h and h.casefold() == title.strip().casefold()]
    if len(matching) != 1:
        return False
    i = matching[0]
    eligible = [r for r in rows[1:] if len(r) == len(rows[0])]
    eligibility = next((r for r in eligible if r[0].lower() == "insurance required" and r[i].lower() in {"never", "no"}), None)
    if not eligibility:
        return False
    found = False
    for row in eligible:
        match = re.fullmatch(r"price for (?:one|an?) (MRI|CT)(?: scan)?", row[0], re.I)
        rate = QUALIFIED.fullmatch(row[i])
        if match and rate:
            label = match[1].upper() + " " + rate[2].lower()
            add(label, " | ".join((title, row[0], row[i])), parse_value(rate[1]), eligibility[0] + ": " + eligibility[i])
            found = True
    return found


def read_card_grids(doc, contexts, add):
    for start in doc.xpath("//*[normalize-space(text())='Starting At']"):
        header = start.getparent()
        if header is None or [text(n).lower() for n in header.xpath("./*")] != ["service", "starting at", "notes"]:
            continue
        if not contexts.get(header):
            continue
        for row in header.itersiblings():
            cells = row.xpath("./*")
            if len(cells) != 3:
                continue
            label, amount, notes = map(text, cells)
            if not EXAM.search(label) or not re.fullmatch(PRICE, amount):
                continue
            if re.search(r"\bnon[- ]contrast\b", notes, re.I):
                label += " without contrast"
            add(label, " | ".join((label, "Starting at " + amount, notes)),
                parse_value("From " + amount), contexts[header] + " — " + notes)


def read_branch_grids(doc, add):
    # These CSS grids contain a separate rich-text cell per facility. Only use
    # rows belonging to the grid with a matching facility / exam / facility header.
    for grid in doc.xpath("//*[@data-testid='mesh-container-content']"):
        headers = [text(n) for n in grid.xpath("./*[@data-testid='richTextElement']")][:3]
        if len(headers) != 3 or headers[1].lower() != "exam":
            continue
        names = [re.fullmatch(r"(.+?)\s+self[- ]pay price", headers[i], re.I) for i in (0, 2)]
        if not all(names):
            continue
        # Wix sometimes flattens multiple visual rows into the outer grid. Its
        # inner rows still keep each amount adjacent to the named exam.
        def emit(cells):
            if len(cells) < 2 or not EXAM.search(cells[1]):
                return
            for col, name in zip((0, 2), names):
                if col >= len(cells):
                    continue
                parsed = parse_value(cells[col])
                if parsed:
                    add(cells[1], " | ".join((headers[col], cells[1], cells[col])), parsed,
                        headers[col], provider=name[1])

        for row in grid.xpath(".//*[@data-testid='mesh-container-content']"):
            cells = [text(n) for n in row.xpath("./*[@data-testid='richTextElement']")]
            if len(cells) in (2, 3):
                emit(cells)
        # A price, exam and explicit facility-only marker can also be direct
        # siblings in the parent. Never infer a missing opposite-column price.
        direct = [text(n) for n in grid.xpath("./*[@data-testid='richTextElement']")]
        for i in range(3, len(direct) - 2):
            if names[0][1].casefold() + " only" in direct[i + 2].casefold():
                emit(direct[i:i+3])


def read_single_service(doc, add):
    headings = [text(n) for n in doc.xpath("//h1")]
    if len(headings) == 1 and re.fullmatch(r"Magnetic Resonance Imaging\s*\(MRI\)|MRI|CT", headings[0], re.I):
        match = re.search(rf"\bself[- ]pay rates? starting at ({PRICE})(?=\.(?:\s|$)|\s|$)", text(doc), re.I)
        if match:
            label = EXAM.search(headings[0])[1].upper()
            add(label, headings[0] + " | " + match[0], parse_value("From " + match[1]), match[0])
    for h in headings:
        match = re.fullmatch(rf"({PRICE})\s+(MRI|CT)\s+[A-Z ,.-]+", h)
        if match:
            terms = re.search(re.escape(match[1]) + r" price displayed is our Prompt Payment Price for self-pay patients who pay at the time of service", text(doc), re.I)
            if terms:
                add(match[2], h + " | " + terms[0], parse_value(match[1]), terms[0])

    for node in doc.xpath("//span[not(*)]"):
        match = re.fullmatch(rf"({PRICE})\s+(MRI(?:/MRA)?|CT(?:/CTA)?)", text(node), re.I)
        if not match:
            continue
        card = node.getparent()
        container = card.getparent() if card is not None else None
        if container is None:
            continue
        policy = re.search(r"Self-pay price applies when insurance is not billed", text(container), re.I)
        if not policy:
            continue
        add(match[2], text(card), parse_value(match[1]), policy[0] + " | " + text(card))
        special = re.search(rf"Coronary Calcium Score\s+({PRICE})", text(card), re.I)
        if special and match[2].startswith("CT"):
            add("CT coronary calcium score", text(card), parse_value(special[1]), policy[0])

    if any(re.search(r"coronary calcium scoring tests?", h, re.I) for h in [*headings, *(text(n) for n in doc.xpath("//h2"))]):
        content = text(doc)
        modality = re.search(r"test itself is a (?:simple )?CT scan of the heart and chest", content, re.I)
        term = re.search(rf"insurance companies do not cover the cost of this test.{{1,6}}patients will be expected to pay a ({PRICE}) testing fee", content, re.I)
        if modality and term:
            add("CT coronary calcium scoring", modality[0] + " | " + term[0],
                parse_value(term[1]), term[0])

    policy = re.search(r"Most commercial insurers including Medicare do not cover these (?:types of )?scans", text(doc), re.I)
    if policy:
        for p in doc.xpath("//p|//div[not(*)]"):
            sentence = text(p)
            current = re.match(rf"Our (full body CT scan) is regularly {PRICE}, with current pricing at ({PRICE})\.", sentence, re.I)
            if current:
                add(current[1], current[0], parse_value(current[2]), policy[0] + " | current advertised price")
            calcium = re.match(rf"Our coronary calcium scan starts at ({PRICE})[^.]*\. It includes the CT scan to measure calcium buildup", sentence, re.I)
            if calcium:
                add("CT coronary calcium scan", calcium[0], parse_value("From " + calcium[1]), policy[0])

    for p in doc.xpath("//p|//div[not(*)]"):
        sentence = text(p)
        rate = re.search(rf"It is available for ({PRICE}) at our [^.$]{{3,150}} locations with a doctor", sentence, re.I)
        if rate and re.search(r"cardiac calcium CT scan", sentence, re.I):
            add("CT cardiac calcium scoring", sentence, parse_value(rate[1]))
