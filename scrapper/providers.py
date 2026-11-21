"""Evidence-bound extraction for the two pilot provider pages."""

import re
from decimal import Decimal

from lxml import html


ONCALL = "https://oncallimaging.net/pricing/"
PINEYWOODS = "https://pineywoodsimaging.com/pricing/"

BRANCHES = {
    ONCALL: ("oncall-rock-falls-il", "OnCall Imaging", "1609 First Ave., Rock Falls, IL 61071"),
    PINEYWOODS: (
        "pineywoods-nacogdoches-tx", "Pineywoods Imaging Center",
        "3318 N. University Drive, Nacogdoches, TX 75965",
    ),
}

CONTRAST = {
    "without contrast": "without",
    "with contrast": "with",
    "with & without contrast": "both",
    "with and without contrast": "both",
    "without and with contrast": "both",
}
PRICE = r"\$(?:[1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.\d{2})?"
PHONE = re.compile(r"(?:\(\d{3}\)\s*|\d{3}[.\- ])\d{3}[.\- ]\d{4}")


def clean(value):
    return " ".join(value.split())


def node_text(node):
    return clean(node.text_content())


def class_match(name):
    return f"contains(concat(' ', normalize-space(@class), ' '), ' {name} ')"


def oncall_rows(doc):
    rows = []
    modality, cash, terms = None, False, ""
    for node in doc.xpath("//h2|//h3|//h5|//h6|//p"):
        value = node_text(node)
        if node.tag in ("h2", "h3"):
            modality = {"MRI": "MRI", "CT Scan": "CT"}.get(value)
            cash, terms = False, ""
        elif node.tag == "h5":
            cash, terms = value == "CASH PAY PRICE", ""
        elif node.tag == "h6" and cash:
            terms = value if value.startswith("Prompt Pay") else ""
        elif modality and cash and terms and node.tag == "p":
            match = re.fullmatch(f"({PRICE})\\s+(.+)", value)
            if match:
                rows.append((modality, "general", match[2], match[1], terms))
    return rows


def pineywoods_rows(doc):
    terms = next(
        (node_text(node) for node in doc.xpath("//p")
         if "paid in full at time of service" in node_text(node).casefold()
         and "not billed to insurance" in node_text(node).casefold()),
        "",
    )
    if not terms:
        raise ValueError("Cash payment terms were not found")
    rows = []
    for section in doc.xpath('//h2[normalize-space(.)="MRI" or normalize-space(.)="CT"]'):
        modality = node_text(section)
        containers = section.xpath(f"ancestor::*[{class_match('et_pb_row')}][1]")
        for container in containers:
            for row in container.xpath(f".//*[{class_match('pwi-price-row')}]"):
                label, = row.xpath(f".//*[{class_match('pwi-price-name')}]")
                amount, = row.xpath(f".//*[{class_match('pwi-price-amt')}]")
                scope, separator, contrast = node_text(label).partition(" — ")
                body = "general"
                if separator:
                    if scope.casefold() != "abdomen & pelvis":
                        continue
                    body = "abdomen and pelvis"
                rows.append((modality, body, contrast if separator else scope, node_text(amount), terms))
    return rows


def extract(url, content, checked_at):
    location_id, name, address = BRANCHES[url]
    doc = html.document_fromstring(content)
    for node in doc.xpath("//script|//style|//template"):
        node.drop_tree()
    page_text = clean(" ".join(doc.xpath("//body//text()")))
    address_marker = "Rock Falls, IL 61071" if url == ONCALL else "Nacogdoches, Texas 75965"
    if address_marker not in page_text or address.split(",")[0] not in page_text:
        raise ValueError("Expected branch address was not found")

    if url == ONCALL:
        phone_section = doc.xpath('//h4[normalize-space(.)="Appointments"]/following::h5[1]')
        phone_text = node_text(phone_section[0]) if phone_section else ""
        email = None
        rows = oncall_rows(doc)
    else:
        phone_text = " ".join(doc.xpath('//a[starts-with(@href,"tel:")]/text()'))
        mail = re.search(r"\binfo@pineywoodsimaging\.com\b", page_text, re.IGNORECASE)
        email = mail.group() if mail else None
        rows = pineywoods_rows(doc)

    center = {
        "location_id": location_id, "name": name, "address": address,
        "phones": list(dict.fromkeys(PHONE.findall(phone_text))),
        "email": email, "source_url": url,
    }
    offers = []
    keys = set()
    for modality, body, wording, amount, terms in rows:
        contrast = CONTRAST.get(wording.casefold())
        if contrast is None or not re.fullmatch(PRICE, amount):
            continue
        key = (modality, body, contrast)
        if key in keys:
            raise ValueError("More than one price row matched the same exam and contrast")
        keys.add(key)
        offers.append({
            "location_id": location_id, "mod": modality, "body": body, "contrast": contrast,
            "payment_option": "direct_prompt_pay", "price_type": "fixed",
            "amount_cents": int(Decimal(amount[1:].replace(",", "")) * 100),
            "scope": f"{modality}, {body}, {wording}", "payment_terms": terms,
            "source_url": url, "evidence": f"{modality}, {body}, {wording} — {amount}",
            "checked_at": checked_at,
        })
    required = {
        (modality, "general", contrast) for modality in ("MRI", "CT")
        for contrast in (("without", "with") if url == ONCALL else ("without", "with", "both"))
    }
    if url == PINEYWOODS:
        required.update(("CT", "abdomen and pelvis", contrast) for contrast in ("without", "with"))
    if not required <= keys:
        raise ValueError("Expected MRI/CT price rows are missing; review the page before using prices")
    return center, offers
