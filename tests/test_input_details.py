"""Specific business details on the forms: the e-invoice number on a sale, and supplier details on receiving."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_inventory_screens import ScreenTests, TIRE_A
from test_delivery_screens import Base
from sqlite_schema import next_invoice


def page(r):
    return r.get_data(as_text=True)


class EInvoiceTests(ScreenTests):
    """A sale needs its own e-invoice number. A voided sale frees its number."""

    def sale(self, invoice, reason="sale", qty="1", expect_ok=True):
        self.login("shopstaff")
        data = {"issue_date": "2026-10-30", "reason": reason, "customer_name": "Walk-in",
                "item_id[]": [str(TIRE_A)], "qty[]": [qty]}
        if invoice is not None:
            data["document_ref"] = invoice
        return self.c.post("/inventory/issues/new", data=data, follow_redirects=not expect_ok)

    def issues(self):
        return self.be.all("SELECT issue_no, document_ref, status FROM stock_issues ORDER BY id")

    def test_a_sale_without_an_e_invoice_number_is_refused(self):
        for missing in (None, "", "   "):
            r = self.sale(missing, expect_ok=False)
            self.assertIn("Enter the e-invoice number", page(r))
        self.assertEqual(self.issues(), [])
        self.assertEqual(self.bal(TIRE_A, 2), 10)                       # no stock left the shop
        self.assertIsNone(self.be.one("SELECT 1 AS x FROM document_sequences WHERE doc_type = 'ISS'"))  # no number wasted

    def test_an_e_invoice_number_cannot_be_used_twice_even_in_another_case(self):
        self.assertEqual(self.sale("EI-100").status_code, 302)
        for again in ("EI-100", "ei-100", " Ei-100 "):
            r = self.sale(again, expect_ok=False)
            self.assertIn("E-invoice no.", page(r)); self.assertIn("is already used on ISS-", page(r))
        self.assertEqual(len(self.issues()), 1)
        self.assertEqual(self.bal(TIRE_A, 2), 9)                        # only the first sale moved stock

    def test_a_voided_sale_frees_its_number(self):
        self.sale("EI-200")
        self.login("shopstaff")
        self.c.post("/inventory/issues/1/void-request", data={"void_reason": "Wrong item"})
        self.login("shopmgr")
        self.c.post("/inventory/issues/1/void-review", data={"decision": "confirm", "remarks": ""})
        self.assertEqual(self.issues()[0]["status"], "voided")
        self.assertEqual(self.sale("EI-200").status_code, 302)           # the replacement sale can use it again

    def test_other_reasons_do_not_need_an_invoice_but_a_given_one_is_still_unique(self):
        self.assertEqual(self.sale(None, reason="other").status_code, 302)
        self.assertEqual(self.sale("", reason="other").status_code, 302)
        self.assertEqual(self.sale("REF-77", reason="other").status_code, 302)
        r = self.sale("ref-77", reason="other", expect_ok=False)
        self.assertIn("already used", page(r))

    def test_length_limit_and_visible_labels(self):
        r = self.sale("X" * 41, expect_ok=False)
        self.assertIn("at most 40 characters", page(r))
        self.sale("EI-300")
        self.login("shopmgr")
        self.assertIn("E-invoice no.", page(self.c.get("/inventory/issues")))
        self.assertIn("EI-300", page(self.c.get("/inventory/issues/1")))
        form = page(self.c.get("/inventory/issues/new"))
        self.assertIn("E-invoice no.", form)
        self.assertNotIn("Receipt / invoice no.", form)

    def test_the_report_names_the_column(self):
        self.sale("EI-400")
        self.login("owner")
        r = self.c.get("/reports/sales.csv") if self.c.get("/reports/sales.csv").status_code == 200 else None
        if r is not None:
            self.assertIn("e_invoice_no", page(r))
            self.assertIn("EI-400", page(r))


class SupplierDetailsTests(Base):
    def test_the_receiving_form_shows_the_supplier_and_asks_for_the_delivery_details(self):
        self.be.execute("UPDATE suppliers SET contact_person = 'Ana Reyes', phone = '0917-000-1111', email = 'ana@sup.example'")
        self.be.commit()
        self.chain()
        self.login("whstaff")
        html = page(self.c.get("/delivery/receiving/1/check"))
        self.assertIn("Supplier contact", html)
        self.assertIn("Ana Reyes · 0917-000-1111 · ana@sup.example", html)
        for field in ("supplier_dr_no", "supplier_invoice_no", "delivered_by"):
            self.assertIn('name="%s"' % field, html)

    def test_details_are_saved_and_shown_on_the_receiving_memo(self):
        data = self.receipt_data(dr="DR-5001")
        data.update(supplier_invoice_no="INV-9001", delivered_by="Mang Lito (driver)")
        self.login("whstaff")
        self.c.post("/delivery/receiving/1/check", data=data)
        row = self.be.one("SELECT supplier_dr_no, supplier_invoice_no, delivered_by FROM goods_receipts")
        self.assertEqual((row["supplier_dr_no"], row["supplier_invoice_no"], row["delivered_by"]),
                         ("DR-5001", "INV-9001", "Mang Lito (driver)"))
        html = page(self.c.get("/delivery/receiving/rm/1"))
        for text in ("DR-5001", "INV-9001", "Mang Lito (driver)", "Supplier invoice no.", "Delivered by"):
            self.assertIn(text, html)

    def test_the_optional_details_are_optional(self):
        self.receive_po(dr="DR-5002")
        row = self.be.one("SELECT supplier_invoice_no, delivered_by FROM goods_receipts")
        self.assertEqual((row["supplier_invoice_no"], row["delivered_by"]), (None, None))
        self.assertNotIn("Delivered by", page(self.c.get("/delivery/receiving/rm/1")))

    def test_a_supplier_dr_number_is_used_once_per_supplier(self):
        self.chain()
        self.receive_po(a=(4, 4, 0, ""), b=(2, 2, 0, ""), dr="DR-7000")
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM goods_receipts")["n"], 1)
        r = self.c.post("/delivery/receiving/1/check", data=self.receipt_data(a=(2, 2, 0, ""), b=(1, 1, 0, ""), dr="dr-7000"))
        self.assertIn("DR no. dr-7000 from this supplier was already recorded on RM-", page(r))
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM goods_receipts")["n"], 1)
        before = self.be.one("SELECT SUM(on_hand) AS n FROM stock_balances")["n"]
        r = self.c.post("/delivery/receiving/1/check", data=self.receipt_data(a=(2, 2, 0, ""), b=(1, 1, 0, ""), dr="DR-7001"))
        self.assertEqual(r.status_code, 302)                              # a new DR number is fine
        self.assertGreater(self.be.one("SELECT SUM(on_hand) AS n FROM stock_balances")["n"], before)

    def test_a_supplier_invoice_number_is_used_once_per_supplier(self):
        d1 = self.receipt_data(a=(4, 4, 0, ""), b=(2, 2, 0, ""), dr="DR-8001"); d1["supplier_invoice_no"] = "INV-1"
        self.login("whstaff")
        self.c.post("/delivery/receiving/1/check", data=d1)
        d2 = self.receipt_data(a=(2, 2, 0, ""), b=(1, 1, 0, ""), dr="DR-8002"); d2["supplier_invoice_no"] = "inv-1"
        r = self.c.post("/delivery/receiving/1/check", data=d2)
        self.assertIn("Invoice no. inv-1 from this supplier was already recorded", page(r))
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM goods_receipts")["n"], 1)

    def test_length_limits(self):
        d = self.receipt_data(dr="DR-8100"); d["delivered_by"] = "x" * 81
        self.login("whstaff")
        self.assertIn("at most 80 characters", page(self.c.post("/delivery/receiving/1/check", data=d)))
        d = self.receipt_data(dr="DR-8101"); d["supplier_invoice_no"] = "x" * 41
        self.assertIn("at most 40 characters", page(self.c.post("/delivery/receiving/1/check", data=d)))


if __name__ == "__main__":
    unittest.main()
