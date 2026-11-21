"""Regression coverage for price scope and repeat checks."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scrapper.__main__ import compare, run
from scrapper.providers import ONCALL, PINEYWOODS, extract


ONCALL_HTML = b"""
<h3>MRI</h3><h5>CASH PAY PRICE</h5><h6>Prompt Pay</h6>
<p>$300 without contrast</p><p>$400 with contrast</p>
<h5>AVERAGE COST AT MAJOR HEALTHCARE SYSTEMS</h5><p>$900 without contrast</p>
<h3>CT Scan</h3><h5>CASH PAY PRICE</h5><h6>Prompt Pay</h6>
<p>$200 without contrast</p><p>$250 with contrast</p>
<h4>Address</h4><h5>1609 First Ave.<br>Rock Falls, IL 61071</h5>
<h4>Appointments</h4><h5>(815) 555-0100</h5>
"""


def pineywoods_section(modality, rows):
    entries = "".join(
        f'<div class="pwi-price-row"><span class="pwi-price-name">{label}</span>'
        f'<span class="pwi-price-amt">${amount}</span></div>'
        for label, amount in rows
    )
    return f'<div class="et_pb_row"><h2>{modality}</h2>{entries}</div>'


class ScrapperTests(TestCase):
    def test_compares_same_offer_and_ignores_hospital_average(self):
        center, original = extract(ONCALL, ONCALL_HTML, "first")
        _, revised = extract(ONCALL, ONCALL_HTML.replace(b"$300", b"$325"), "second")
        self.assertEqual(center["phones"], ["(815) 555-0100"])
        self.assertEqual([o["amount_cents"] for o in original], [30000, 40000, 20000, 25000])
        changes = compare(revised, original)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["changed_fields"], ["amount_cents"])
        self.assertEqual(changes[0]["observed"]["amount_cents"], 32500)

    def test_paired_prices_keep_contrast_and_body_separate(self):
        common = [("Without contrast", 100), ("With contrast", 200), ("With &amp; without contrast", 300)]
        page = (
            "<meta charset='utf-8'><p>Rates apply when paid in full at time of service and not billed to insurance.</p>"
            + pineywoods_section("MRI", common)
            + pineywoods_section("CT", common + [
                ("Abdomen &amp; Pelvis — without contrast", 410),
                ("Abdomen &amp; Pelvis — with contrast", 510),
                ("Angiogram — each additional body part", 90),
            ])
            + pineywoods_section("MRA", common)
            + "<h4>3318 N. University Drive Nacogdoches, Texas 75965</h4>"
        )
        _, offers = extract(PINEYWOODS, page.encode("utf-8"), "first")
        self.assertEqual(len(offers), 8)
        self.assertEqual(
            [(o["body"], o["contrast"], o["amount_cents"]) for o in offers[-3:]],
            [("general", "both", 30000), ("abdomen and pelvis", "without", 41000),
             ("abdomen and pelvis", "with", 51000)],
        )

    def test_missing_rows_and_unknown_url_only_report_source_issues(self):
        _, baseline = extract(ONCALL, ONCALL_HTML, "first")
        fetched = []

        def fetch(url):
            fetched.append(url)
            return ONCALL_HTML.replace(b"<p>$200 without contrast</p>", b"")

        with TemporaryDirectory() as directory:
            path = Path(directory) / "sources.csv"
            path.write_text(f"pricing_url\n{ONCALL}\n{ONCALL}\nhttp://example.com/\n")
            report = run(path, baseline, fetch)
        self.assertEqual(fetched, [ONCALL])
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["offers"], [])
        self.assertEqual([i["status"] for i in report["issues"]], ["source_issue", "source_issue"])

    def test_unseen_table_and_inline_prices(self):
        from scrapper.dynamic import review_page
        page = b"""<table><tr><th>Exam</th><th>Insurance</th><th>Cash price</th></tr>
        <tr><td>MRI knee</td><td>$900</td><td>$375</td></tr>
        <tr><td>CT neck</td><td>$600</td><td>From $200</td></tr></table>
        <p>CT spine $350 plus $40 reading fee</p>"""
        rows = review_page(page, "https://example.org/prices", "now")
        self.assertEqual([r["amount_cents"] for r in rows], [37500, None, None])
        self.assertTrue(all(r["status"] == "needs_review" for r in rows))
