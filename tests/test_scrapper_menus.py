import io
import os
import socket
import unittest
from unittest.mock import patch

from scrapper.__main__ import run_csv
from scrapper.browser_prices import needs_browser
from scrapper.dynamic import review_page
from scrapper.prices import price_rows
from scrapper.public_fetch import public_ips

URL = "https://provider.example/pricing"
CHECKED = "2026-11-23T00:00:00+00:00"


def numeric(page):
    report = {"offers": [], "centers": [], "review_candidates": review_page(page, URL, CHECKED)}
    return list(price_rows(report))


class MenuTests(unittest.TestCase):
    def test_provider_column_in_any_position(self):
        page = """<meta property="og:site_name" content="Center A"><table>
        <tr><th></th><th>Center A</th><th>Other centers</th></tr>
        <tr><td>Price for one MRI</td><td>$417 — all non-contrast scans</td><td>$600–$1,050</td></tr>
        <tr><td>Insurance required</td><td>No</td><td>Usually</td></tr></table>"""
        row, = numeric(page)
        self.assertEqual((row["price_usd"], row["contrast"], row["body_part_column"]), (417, "without", "General"))
        self.assertEqual(numeric(page.replace('content="Center A"', 'content="Center B"')), [])

    def test_self_pay_dropdown_pairs_all_six_totals_with_their_own_panel(self):
        exams = [f"{mod} {contrast} contrast" for mod in ("MRI", "CT")
                 for contrast in ("without", "with", "with &amp; without")]
        def section(title, offset):
            controls = [f'<a role="tab" href="#p{offset+i}">{name}</a>' for i, name in enumerate(exams)]
            panels = [f'<div role="tabpanel" hidden id="p{offset+i}"><h2>{name}</h2>'
                      f'<h2>${offset+i}</h2><h4>You Pay</h4><p>Contrast fee $50</p></div>'
                      for i, name in enumerate(exams)]
            return f"<section><h1>{title}</h1>" + "".join(controls + panels[::-1]) + "</section>"
        page = section("Insured", 2000) + section("Self-Pay", 500) + "<script></script>"
        rows = numeric(page)
        self.assertEqual([r["price_usd"] for r in rows], list(range(500, 506)))
        self.assertEqual([r["contrast"] for r in rows], ["without", "with", "both"] * 2)
        self.assertFalse(needs_browser(page, review_page(page, URL, CHECKED), URL))

    def test_cash_table_excludes_ct_ng_lab_test(self):
        page = """<h1>Cash pricing</h1><table><tr><th>Procedure</th><th>Price</th></tr>
        <tr><td>CT w/o Contrast</td><td>225</td></tr><tr><td>CT/NG</td><td>100</td></tr></table>"""
        row, = numeric(page)
        self.assertEqual((row["price_usd"], row["contrast"]), (225, "without"))

    def test_service_urls_tried_even_after_one_result(self):
        opts = "".join(f'<option value="/price/{i}">CT test {i}</option>' for i in range(5))
        home = "<h1>Cash pricing</h1><p>MRI: $500</p><select>" + opts + "</select>"
        def fetch(u, _):
            if u == URL:
                return home, u
            if u.endswith("/3"):
                raise OSError("source not reachable")
            return f"<h1>Cash pricing</h1><p>CT test {u[-1]}: $350</p>", u
        with patch("scrapper.__main__.fetch_public", side_effect=fetch):
            report = run_csv(io.StringIO("pricing_url\n" + URL), [])
        source, = report["source_results"]
        self.assertEqual((source["numeric_count"], len(source["attempts"])), (5, 6))
        self.assertTrue(any("not reachable" in item["detail"] for item in source["attempts"]))

    def test_uploaded_snapshot_requires_matching_origin_and_retains_provenance(self):
        page = f'<link rel="canonical" href="{URL}"><h1>Self-pay</h1><p>MRI knee: $360</p>'
        with patch("scrapper.__main__.fetch_public") as fetch:
            report = run_csv(io.StringIO("pricing_url\n" + URL), [], snapshots=[page])
            with self.assertRaisesRegex(ValueError, "matching"):
                run_csv(io.StringIO("pricing_url\n" + URL + "/other"), [], snapshots=[page])
            fetch.assert_not_called()
        row, = report["pricing_rows"]
        self.assertEqual((row["price_usd"], row["evidence_origin"]), (360, "uploaded_html"))

    def test_dns_retry_rejects_nonpublic_response(self):
        with patch("scrapper.public_fetch.socket.getaddrinfo", side_effect=socket.gaierror()), \
             patch("scrapper.public_fetch.urlopen") as lookup:
            lookup.return_value.__enter__.return_value.read.return_value = b'{"Answer":[{"type":1,"data":"127.0.0.1"}]}'
            with self.assertRaisesRegex(ValueError, "public"):
                public_ips("provider.example.com")

    @unittest.skipUnless(os.getenv("MYSELFPAY_TEST_BROWSER"), "Set MYSELFPAY_TEST_BROWSER with Chromium installed")
    def test_javascript_updates_result_for_selected_exam(self):
        page = b"""<section><h1>Self-Pay</h1><select onchange="show(this)"><option>Choose</option>
        <option>MRI knee without contrast</option><option>CT chest with contrast</option></select>
        <div><h3 id="exam"></h3><div><strong id="rate"></strong><h4>You Pay</h4></div></div></section>
        <script>function show(s){setTimeout(()=>{document.querySelector('#exam').textContent=s.value;
        document.querySelector('#rate').textContent=s.selectedIndex===1?'$421':'$316';},80)}</script>"""
        rows = review_page(page, URL, CHECKED, interactive=True)
        self.assertEqual([r["amount_cents"] for r in rows], [42100, 31600])

    def test_current_price_separate_from_former_rate_and_financing(self):
        page = """<p>Most commercial insurers including Medicare do not cover these scans.</p>
        <p>Our full body CT scan is regularly $4,000, with current pricing at $2,490.</p>
        <p>Monthly payment: $75</p>"""
        row, = numeric(page)
        self.assertEqual((row["price_usd"], row["body_part_column"]), (2490, "Whole body"))

    def test_pdf_keeps_self_pay_column_and_page_evidence(self):
        from unittest.mock import MagicMock
        pdf = MagicMock()
        pdf.is_encrypted = False
        page = MagicMock()
        pdf.pages = [page]
        page.extract_text.return_value = ("Procedure            SELF-PAY\nMRI Knee without contrast       $478\n"
                                          "Insurance Rates\nMRI Knee without contrast      $900\n")
        with patch("pypdf.PdfReader", return_value=pdf):
            row, = numeric(b"%PDF-1.7")
        self.assertEqual((row["price_usd"], row["body_part_column"], row["contrast"]), (478, "Knee", "without"))
        self.assertIn("PDF page 1", row["evidence"])

    def test_image_price_keeps_original_evidence_and_modality(self):
        with patch("scrapper.image_prices.schedule_rows", return_value=[(4, "MR Joint Arthrogram", "$987")]):
            candidates = review_page(b'<img src="/cash-pricing.png">', URL, CHECKED,
                                     image_fetch=lambda url, *a, **k: (b"image", url, "image/png"))
        row, = price_rows({"offers": [], "centers": [], "review_candidates": candidates})
        self.assertEqual((row["modality"], row["price_usd"], row["evidence_origin"]), ("MRI", 987, "provider_image"))
        self.assertIn("MR Joint Arthrogram", row["evidence"])
        self.assertTrue(row["evidence_url"].endswith("cash-pricing.png"))
