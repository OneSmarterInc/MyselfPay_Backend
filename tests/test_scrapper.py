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
                ("Angiogram — first body part", 225),
                ("Angiogram — each additional body part", 90),
                ("Calcium scoring", 125),
            ])
            + pineywoods_section("MRA", common)
            + "<h4>3318 N. University Drive Nacogdoches, Texas 75965</h4>"
        )
        _, offers = extract(PINEYWOODS, page.encode("utf-8"), "first")
        self.assertEqual(len(offers), 11)
        self.assertEqual([(r["price_type"], r["amount_cents"]) for r in offers[-3:]],
                         [("fixed", 22500), ("add_on", 9000), ("fixed", 12500)])
        self.assertEqual(
            [(o["body"], o["contrast"], o["amount_cents"]) for o in offers[5:8]],
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
        self.assertEqual([r["amount_cents"] for r in rows], [37500, 20000, None])
        self.assertTrue(all(r["status"] == "needs_review" for r in rows))

    def test_cash_deposit_is_not_a_scan_price(self):
        from scrapper.dynamic import review_page
        for header in ("Cash deposit", "Self-pay discount", "Cash price excluding reading"):
            page = f"<table><tr><th>Exam</th><th>{header}</th></tr><tr><td>MRI knee</td><td>$75</td></tr></table>"
            rows = review_page(page, "https://example.org/menu", "now")
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]["amount_cents"])
            self.assertIn(header, rows[0]["evidence"])

    def test_homepage_to_provider_pricing_menu(self):
        import io
        from unittest.mock import patch
        from scrapper.__main__ import run_csv
        home = "https://example.org/"
        menu = home + "self-pay"
        pages = {home: b'<a href="/self-pay">Cash prices</a><a href="/self-pay#ct">CT pricing</a>',
                 menu: b'<table><tr><td>MRI w/ &amp; w/o Contrast</td><td>$575</td></tr></table>'}
        with patch("scrapper.__main__.fetch_public", side_effect=lambda url, limit: (pages[url], url)) as fetch:
            report = run_csv(io.StringIO("pricing_url\n" + home), [])
        candidate, = report["review_candidates"]
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual((candidate["amount_cents"], candidate["contrast"]), (57500, "both"))
        self.assertEqual((candidate["source_url"], candidate["starting_url"]), (menu, home))

    def test_numeric_output_from_different_html_layouts(self):
        import csv
        import io
        from unittest.mock import patch
        from scrapper.__main__ import run_csv
        from scrapper.prices import write_csv

        source = "https://example.org/pricing"
        page = """<title>Imaging Center</title><h1>Cash Pay Imaging Prices</h1>
        <ul><li>MRI: Starting at $440</li><li>CT Scan: Starting at $285</li></ul>
        <dl><dt>CT pelvis with contrast</dt><dd>$335.75 to $480.00</dd></dl>
        <p>Call today for MRI. Save $200 with this promotion!</p>
        <table><tr><th>Exam</th><th>Insurance</th><th>Cash price</th></tr>
        <tr><td>Breast MRI</td><td>$990</td><td>$520</td></tr></table>
        <h2>Insurance charges</h2><p>CT neck: $999</p>
        <h1>Other services</h1><p>CT spine: $475</p>"""
        with patch("scrapper.__main__.fetch_public", return_value=(page, source)):
            report = run_csv(io.StringIO("pricing_url\n" + source), [])
        rows = report["pricing_rows"]
        self.assertCountEqual([(r["exam"], r["price_usd"], r["max_price_usd"], r["price_type"])
                               for r in rows], [
            ("MRI", 440, None, "starting"), ("CT Scan", 285, None, "starting"),
            ("CT pelvis with contrast", 335.75, 480, "range"), ("Breast MRI", 520, None, "listed")])
        self.assertTrue(all(r["location_id"] is None and r["status"] == "needs_review" for r in rows))
        buf = io.StringIO()
        write_csv(buf, rows)
        self.assertEqual([float(r["price_usd"]) for r in csv.DictReader(io.StringIO(buf.getvalue()))],
                         [r["price_usd"] for r in rows])

    def test_currency_header_and_ambiguous_prices(self):
        from scrapper.dynamic import review_page
        from scrapper.prices import price_rows
        page = """<h1>Self-pay</h1><table><tr><th>Exam</th><th>Cash price (USD)</th></tr>
        <tr><td>CT ankle</td><td>395.50</td></tr></table>
        <table><tr><td>Service</td><td>Cash deposit</td></tr><tr><td>CT neck</td><td>$50</td></tr></table>
        <li>MRI knee: $300 plus $40 reading fee</li><p>CT brain: $700 to $400</p>"""
        report = dict(centers=[], offers=[], review_candidates=review_page(page, "https://example.org/", "now"))
        row, = price_rows(report)
        self.assertEqual((row["exam"], row["price_usd"]), ("CT ankle", 395.50))

    def test_projection_preserves_body_scope_and_price_variants(self):
        import csv
        import io
        from unittest.mock import patch
        from scrapper.__main__ import run_csv
        from scrapper.body_parts import OVERVIEW_FIELDS, overview_records
        from scrapper.prices import write_csv

        page = """<title>One operator</title><h1>Self-pay prices</h1>
        <p>MRI without contrast (excludes breast and prostate): $400</p>
        <p>MRI left knee without contrast: From $350</p>
        <p>MRI right knee without contrast: $310 to $410</p>
        <p>Whole body screening MRI: $950</p><p>CT calcium scoring: $125</p>"""
        urls = ["https://example.org/east", "https://example.org/west"]
        with patch("scrapper.__main__.fetch_public", side_effect=lambda url, _: (page, url)):
            report = run_csv(io.StringIO("pricing_url\n" + "\n".join(urls)), [])
        view, prices = report["body_part_overview"], report["pricing_rows"]
        self.assertEqual(len(view["rows"]), 6)
        self.assertEqual(len(prices), 10)
        for group in view["rows"]:
            for indexes in group["cells"].values():
                self.assertTrue(all(prices[i]["source_url"] == group["source_url"] for i in indexes))
        cells = view["rows"][0]["cells"]
        self.assertEqual(cells, {"General": [0], "Knee": [1, 2]})
        self.assertEqual(prices[0]["exclusions"], "breast and prostate")
        self.assertEqual(prices[2]["max_price_usd"], 410)
        self.assertEqual(view["rows"][1]["cells"], {"Whole body": [3]})
        self.assertEqual(view["rows"][1]["contrast"], "unknown")
        self.assertEqual(view["rows"][2]["cells"], {"Other exams": [4]})
        output = io.StringIO()
        write_csv(output, overview_records(view, prices), OVERVIEW_FIELDS)
        first = next(csv.DictReader(io.StringIO(output.getvalue())))
        self.assertEqual((first["General"], first["Knee"], first["Breast"]), ("400.0", "", ""))
        self.assertIn("starting, USD 350.00", first["cell_details"])
        self.assertIn("range, USD 310.00–410.00", first["cell_details"])

    def test_table_prices_follow_explicit_body_and_contrast(self):
        from scrapper.dynamic import review_page
        from scrapper.prices import price_rows

        page = """<h1>Self-pay prices</h1><h2>MRI</h2><table>
        <tr><th>Body part</th><th>Without contrast</th><th>With contrast</th></tr>
        <tr><td>Brain</td><td>$275</td><td>$375</td></tr></table>
        <table><tr><th>Modality</th><th>Body part</th><th>Contrast</th><th>Insured</th><th>Cash price</th></tr>
        <tr><td>CT</td><td>Abdomen and pelvis</td><td>both</td><td>$1900</td><td>$600</td></tr></table>"""
        candidates = review_page(page, "https://example.org/", "now")
        rows = list(price_rows(dict(centers=[], offers=[], review_candidates=candidates)))
        self.assertCountEqual([(r["body_part"], r["modality"], r["contrast"], r["price_usd"]) for r in rows], [
            ("Brain", "MRI", "without", 275), ("Brain", "MRI", "with", 375),
            ("Abdomen & pelvis", "CT", "both", 600)])

    def test_ambiguous_or_excluded_regions_do_not_fill_body_cells(self):
        from scrapper.body_parts import exam_scope

        for label in ("MRI knee and ankle", "MRI non-breast", "CT calcium scoring", "MRI ankle",
                      "CT angiogram — each additional body part", "MRI biopsy"):
            self.assertEqual(exam_scope(label)["body_part_column"], "Other exams", label)
        self.assertEqual(exam_scope("MRI (excludes breast) left knee without contrast")["body_part"], "Knee")
        self.assertEqual(exam_scope("MRI w/ & w/o Contrast (excludes Prostate MRI)")["body_part_column"], "General")

    def test_new_knee_rate_on_a_pilot_page(self):
        from scrapper.prices import price_rows
        page = ONCALL_HTML.replace(b"<p>$300 without contrast</p>",
                                   b"<p>$300 without contrast</p><p>$375 Knee without contrast</p>")
        center, offers = extract(ONCALL, page, "now")
        rows = list(price_rows(dict(centers=[center], offers=offers, review_candidates=[])))
        knee, = [r for r in rows if r["body_part"] == "Knee"]
        self.assertEqual((knee["contrast"], knee["price_usd"]), ("without", 375))
