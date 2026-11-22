"""Printable documents and e-signatures (uses the delivery test fixtures)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_delivery_screens import Base

import base64
import io

from PIL import Image, ImageDraw

from test_delivery_screens import make_png

PNG = make_png()


class DocumentTests(Base):
    def slot(self, doc_type, slot):
        return {r["slot"]: r for r in self.sigs(doc_type)}[slot]

    def sigs(self, doc_type=None):
        sql = "SELECT * FROM document_signatures"
        if doc_type:
            return self.be.all(sql + " WHERE doc_type = :t ORDER BY id", t=doc_type)
        return self.be.all(sql + " ORDER BY id")

    # ---- signing happens inside the action, with the account's name ----
    def test_release_signs_the_ts_with_name_role_and_saved_signature(self):
        self.login("whmgr")
        self.assertEqual(self.c.post("/signature", data={"action": "save", "signature_data": PNG}).status_code, 302)
        saved = self.be.one("SELECT signature_data FROM users WHERE id = 2")["signature_data"]
        self.assertTrue(saved.startswith("data:image/png;base64,"))
        self.chain()
        row = self.slot("TS", "released_by")
        self.assertEqual((row["slot"], row["signer_name"], row["signer_role"]),
                         ("released_by", "whmgr", "warehouse_manager"))
        self.assertEqual(row["signature_data"], saved)
        self.assertEqual(len(row["doc_hash"]), 64)
        page = self.c.get("/documents/TS/1").get_data(as_text=True)
        self.assertIn("data:image/png;base64", page)
        self.assertNotIn("Changed after signing", page)

    def test_signing_without_a_drawn_signature_still_stamps_the_account_name(self):
        self.chain()
        row = self.slot("TS", "released_by")
        self.assertEqual(row["signer_name"], "whmgr")
        self.assertIsNone(row["signature_data"])

    def test_nobody_can_type_a_name_or_replace_a_signed_line(self):
        self.chain()
        before = self.slot("TS", "released_by")
        n_before = len(self.sigs("TS"))
        self.login("whmgr")
        r = self.c.post("/documents/TS/1/sign", data={"slot": "released_by", "signer_name": "Somebody Else"},
                        follow_redirects=True)
        self.assertIn("not here", r.get_data(as_text=True))       # action line, not a manual line
        self.assertEqual(len(self.sigs("TS")), n_before)
        self.assertEqual(self.slot("TS", "released_by")["signer_name"], before["signer_name"])

    def test_signing_twice_is_refused(self):
        from app import signing
        from app import inventory_service as inv
        self.chain()
        with self.assertRaises(inv.InventoryError):
            signing.sign_slot({"id": self.uid["whmgr"], "role": "warehouse_manager"}, "TS", 1,
                              "released_by", "x" * 64)

    def test_wrong_roles_cannot_sign(self):
        from app import signing
        from app import inventory_service as inv
        self.chain()
        self.receive()
        for user, role, dt, slot in (("shopmgr", "shop_manager", "PO", "approved_by"),
                                     ("owner", "owner", "TS", "released_by"),
                                     ("whstaff", "warehouse_staff", "RM", "checked_by"),
                                     ("shopstaff", "shop_staff", "AR", "received_by")):
            with self.assertRaises(inv.InventoryError, msg=(user, dt, slot)):
                signing.sign_slot({"id": self.uid[user], "role": role}, dt, 1, slot, "x" * 64)
        self.assertEqual([r["doc_type"] for r in self.sigs()].count("PO"), 0)

    def test_transfer_slip_shows_the_requested_quantity_against_the_shipped(self):
        self.chain()
        page = self.c.get("/documents/TS/1").get_data(as_text=True)
        self.assertIn("requested 5", page)          # RF line, while the Qty column shows what this slip carries
        self.assertIn("requested 3", page)
        self.assertNotIn("Changed after signing", page)      # line notes are outside the signature hash

    def test_receipt_shows_requested_against_received_and_the_shortfall(self):
        self.chain()
        self.receive(qty_b=2, reason="one short")
        page = self.c.get("/documents/AR/1").get_data(as_text=True)
        self.assertIn("requested 3", page)
        self.assertIn("1 rejected/short", page)

    def test_receiving_signs_the_ar_and_the_ts_received_line(self):
        self.chain()
        self.receive()
        ar = self.sigs("AR")
        self.assertEqual((ar[0]["slot"], ar[0]["signer_name"], ar[0]["signer_role"]),
                         ("received_by", "shopmgr", "shop_manager"))
        ts = {r["slot"]: r for r in self.sigs("TS")}
        self.assertEqual(ts["received_by"]["signer_name"], "shopmgr")
        self.assertIn("released_by", ts)

    # ---- staff never sign; the manager signs ----
    def test_staff_record_the_memo_and_the_manager_signs_it(self):
        self.receive_po()
        self.assertEqual(self.sigs("RM"), [])                      # staff did not sign
        self.login("whstaff")
        page = self.c.get("/documents/RM/1").get_data(as_text=True)
        self.assertIn("whstaff", page)                              # shown as 'Received by' text
        self.assertNotIn('name="slot"', page)                       # no sign button for staff
        r = self.c.post("/documents/RM/1/sign", data={"slot": "checked_by"}, follow_redirects=True)
        self.assertEqual(self.sigs("RM"), [])
        self.login("whmgr")
        todo = self.c.get("/documents/to-sign").get_data(as_text=True)
        self.assertIn("RM-", todo)
        self.c.post("/documents/RM/1/sign", data={"slot": "checked_by", "signer_name": "Hacker"})
        row = self.sigs("RM")[0]
        self.assertEqual((row["signer_name"], row["signer_role"]), ("whmgr", "warehouse_manager"))
        self.assertNotIn("RM-", self.c.get("/documents/to-sign").get_data(as_text=True))

    def test_manager_recording_the_memo_signs_it_in_the_same_step(self):
        self.login("whmgr")
        self.c.post("/delivery/receiving/1/check", data=self.receipt_data())
        row = self.sigs("RM")
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]["signer_name"], "whmgr")

    def test_dr_is_prepared_by_manager_then_noted_by_owner(self):
        self.receive_po(b=(4, 3, 1, "damaged casing"))
        self.login("owner")
        self.assertNotIn("DR-", self.c.get("/documents/to-sign").get_data(as_text=True))   # not before the manager
        self.login("whmgr")
        self.c.post("/documents/DR/1/sign", data={"slot": "prepared_by"})
        self.login("owner")
        self.assertIn("2026", self.c.get("/documents/to-sign").get_data(as_text=True))
        self.c.post("/documents/DR/1/sign", data={"slot": "noted_by"})
        self.assertEqual([r["slot"] for r in self.sigs("DR")], ["prepared_by", "noted_by"])


    # ---- the driver signs on the warehouse screen ----
    def test_driver_signs_on_the_staff_screen_with_the_assigned_name(self):
        self.prepare(); self.assign(); self.load()
        r = self.driver_signs("whstaff")
        self.assertEqual(r.status_code, 302)
        row = self.slot("TS", "driver")
        self.assertEqual((row["signer_name"], row["signer_role"]), ("Dan Driver", "driver"))
        self.assertIsNone(row["signer_user_id"])                         # drivers have no account
        self.assertEqual(row["captured_by_user_id"], self.uid["whstaff"])        # who held the device
        self.assertEqual(len(row["doc_hash"]), 64)
        self.assertTrue(row["signature_data"].startswith("data:image/png"))
        page = self.c.get("/documents/TS/1").get_data(as_text=True)
        self.assertIn("Dan Driver", page)
        self.assertIn("Captured by whstaff", page)

    def test_driver_name_cannot_be_typed(self):
        self.prepare(); self.assign(); self.load()
        self.login("whstaff")
        self.c.post("/delivery/transfers/1/driver-sign",
                    data={"signature_data": make_png(), "signer_name": "Somebody Else"})
        self.assertEqual(self.slot("TS", "driver")["signer_name"], "Dan Driver")

    def test_driver_signature_only_after_loading_and_only_once(self):
        self.prepare(); self.assign()                                    # assigned, not loaded yet
        self.login("whstaff")
        r = self.c.post("/delivery/transfers/1/driver-sign", data={"signature_data": make_png()},
                        follow_redirects=True)
        self.assertIn("after the loading check", r.get_data(as_text=True))
        self.assertEqual([x for x in self.sigs("TS") if x["slot"] == "driver"], [])
        self.load()
        self.driver_signs()
        r = self.c.post("/delivery/transfers/1/driver-sign", data={"signature_data": make_png()},
                        follow_redirects=True)
        self.assertEqual(len([x for x in self.sigs("TS") if x["slot"] == "driver"]), 1)

    def test_blank_or_fake_driver_signature_is_refused(self):
        self.prepare(); self.assign(); self.load()
        self.login("whstaff")
        fake = "data:image/png;base64," + base64.b64encode(b"not an image").decode()
        for data in ("", make_png(ink=False), fake):
            self.c.post("/delivery/transfers/1/driver-sign", data={"signature_data": data})
        self.assertEqual([x for x in self.sigs("TS") if x["slot"] == "driver"], [])

    def test_shop_and_owner_cannot_capture_the_driver_signature(self):
        self.prepare(); self.assign(); self.load()
        for who in ("shopmgr", "shopstaff", "owner"):
            self.login(who)
            r = self.c.post("/delivery/transfers/1/driver-sign", data={"signature_data": make_png()})
            self.assertIn(r.status_code, (302, 403, 404), who)
        self.assertEqual([x for x in self.sigs("TS") if x["slot"] == "driver"], [])

    def test_release_is_blocked_until_the_driver_signs(self):
        self.prepare(); self.assign(); self.load()
        self.login("whmgr")
        r = self.c.post("/delivery/transfers/1/release", data={}, follow_redirects=True)
        self.assertIn("The driver has not signed yet", r.get_data(as_text=True))
        self.assertEqual(self.be.one("SELECT status FROM transfer_slips")["status"], "loaded")
        self.assertEqual([x for x in self.sigs("TS")], [])               # nothing signed either

    def test_manager_can_release_with_a_written_reason_and_it_is_kept(self):
        self.prepare(); self.assign(); self.load()
        self.login("whmgr")
        self.c.post("/delivery/transfers/1/release", data={"paper_reason": "Driver left, signed paper copy"})
        row = self.be.one("SELECT status, driver_paper_reason FROM transfer_slips")
        self.assertEqual((row["status"], row["driver_paper_reason"]), ("in_transit", "Driver left, signed paper copy"))
        page = self.c.get("/documents/TS/1").get_data(as_text=True)
        self.assertIn("Driver left, signed paper copy", page)
        self.assertEqual(self.slot("TS", "released_by")["signer_name"], "whmgr")

    def test_a_driver_signature_does_not_carry_over_to_a_different_driver(self):
        from app import document_ops as docs
        self.prepare(); self.assign(); self.load(); self.driver_signs()
        self.be.execute("INSERT INTO drivers (full_name, contact_no, license_no, is_active) VALUES ('New Guy', '1', 'L9', 1)")
        self.be.execute("UPDATE transfer_slips SET driver_id = 3")
        self.be.commit()
        self.login("whmgr")
        r = self.c.post("/delivery/transfers/1/release", data={}, follow_redirects=True)
        self.assertIn("The driver has not signed yet", r.get_data(as_text=True))

    def test_driver_signature_stays_valid_after_release_and_is_on_the_pdf(self):
        self.chain()
        self.login("whmgr")
        page = self.c.get("/documents/TS/1").get_data(as_text=True)
        self.assertNotIn("Changed after signing", page)
        pdf = self.c.get("/documents/TS/1.pdf").get_data()
        self.assertGreaterEqual(pdf.count(b"/Subtype /Image"), 2)        # logo + at least the driver's signature

    def test_printable_screen_header_keeps_parties_and_details_in_two_aligned_columns(self):
        """From/To and the detail pairs share the same two columns, whatever the document."""
        self.chain()
        self.receive()
        self.login("owner")
        for path in ("/documents/TS/1", "/documents/AR/1"):
            html = self.c.get(path).get_data(as_text=True)
            self.assertEqual(html.count('<div class="doc-parties">'), 1)
            self.assertEqual(html.count('<dl class="info doc-meta">'), 1)
        css = self.c.get("/static/css/style.css").get_data(as_text=True)
        two_cols = r"grid-template-columns:\s*minmax\(0, 1fr\) minmax\(0, 1fr\)"
        self.assertRegex(css, r"\.doc-parties\s*\{[^}]*" + two_cols)
        self.assertRegex(css, r"dl\.info\.doc-meta\s*\{[^}]*" + two_cols)      # never the auto-fill 3-4 column grid
        self.assertNotRegex(css, r"\.doc-parties\s*\{[^}]*grid-template-columns:\s*1fr;")


    # ---- the shop manager must check the TS first ----
    def test_ar_cannot_be_signed_until_every_line_is_counted(self):
        self.chain()
        data = self.receive_data()
        n = len(data["counted[]"])
        for counted in ([], data["counted[]"][:-1]):                 # nothing ticked, or one line missed
            data["counted[]"] = counted
            self.login("shopmgr")
            r = self.c.post("/delivery/deliveries/%d/receive" % self.ts_id(), data=data, follow_redirects=True)
            self.assertIn("Check the delivery against the TS first", r.get_data(as_text=True))
        self.assertEqual(self.be.one("SELECT status FROM transfer_slips")["status"], "in_transit")
        self.assertEqual([x for x in self.sigs("AR")], [])           # nothing signed, nothing received
        self.assertEqual(self.be.one("SELECT COUNT(*) AS n FROM acknowledgment_receipts")["n"], 0)
        self.assertGreater(n, 1)

    def test_blank_quantities_still_need_the_counted_ticks(self):
        self.chain()
        data = self.receive_data()
        data["qty_accepted[]"] = [""] * len(data["qty_accepted[]"])
        data["qty_rejected[]"] = [""] * len(data["qty_rejected[]"])
        data["counted[]"] = []
        self.login("shopmgr")
        self.c.post("/delivery/deliveries/%d/receive" % self.ts_id(), data=data)
        self.assertEqual(self.be.one("SELECT status FROM transfer_slips")["status"], "in_transit")

    def test_receive_page_shows_the_ts_check_panel(self):
        self.chain()
        self.login("shopmgr")
        page = self.c.get("/delivery/deliveries/%d/receive" % self.ts_id()).get_data(as_text=True)
        self.assertIn("Check the TS", page)
        self.assertIn("Released by", page)
        self.assertIn("whmgr", page)                                  # who released it
        self.assertIn("Dan Driver", page)
        self.assertIn('name="counted[]"', page)

    # ---- PO: owner approves, warehouse manager sends ----
    def test_only_the_warehouse_manager_can_send_a_po(self):
        from app import purchasing_ops as pur
        from app import inventory_service as inv
        self.chain()
        self.be.execute("UPDATE purchase_orders SET status = 'draft'")
        self.be.commit()
        for who, role in (("owner", "owner"), ("shopmgr", "shop_manager"), ("whstaff", "warehouse_staff")):
            self.login(who)
            r = self.c.post("/purchasing/po/1", data={"action": "mark_sent"}, follow_redirects=True)
            self.assertEqual(self.be.one("SELECT status FROM purchase_orders")["status"], "draft", who)
        self.login("owner")
        page = self.c.get("/purchasing/po/1").get_data(as_text=True)
        self.assertNotIn("Sign &amp; mark as sent", page)
        self.assertIn("Ready to send", page)
        self.assertIn("The warehouse manager sends it", page)
        self.login("whmgr")
        self.assertIn("Sign &amp; mark as sent", self.c.get("/purchasing/po/1").get_data(as_text=True))
        self.c.post("/purchasing/po/1", data={"action": "mark_sent"})
        self.assertEqual(self.be.one("SELECT status FROM purchase_orders")["status"], "sent")

    # ---- warehouse: prepare and assign in one step; load check leads to the driver ----
    def test_prepare_and_assign_in_one_step(self):
        self.login("whmgr")
        self.c.post("/delivery/transfers/new", data={"rf_id": "1", "driver_id": "1", "vehicle_id": "1"})
        ts = self.be.one("SELECT status, driver_id, vehicle_id FROM transfer_slips")
        self.assertEqual((ts["status"], ts["driver_id"], ts["vehicle_id"]), ("assigned", 1, 1))
        page = self.c.get("/delivery/transfers/new").get_data(as_text=True)
        self.assertNotIn('name="rf_id" value="1"', page)  # an active TS cannot be created twice

    def test_prepare_without_a_driver_still_makes_a_draft(self):
        self.login("whmgr")
        self.c.post("/delivery/transfers/new", data={"rf_id": "1"})
        self.assertEqual(self.be.one("SELECT status FROM transfer_slips")["status"], "draft")

    def test_loading_check_goes_straight_to_the_driver_signature(self):
        self.prepare(); self.assign()
        self.login("whstaff")
        items = [str(i["id"]) for i in self.be.all("SELECT id FROM transfer_slip_items")]
        r = self.c.post("/delivery/transfers/%d/load" % self.ts_id(), data={"loaded[]": items})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/driver-sign"))

    # ---- tamper evidence ----
    def test_changing_a_signed_document_is_flagged(self):
        self.chain()
        self.login("whmgr")
        self.assertNotIn("Changed after signing", self.c.get("/documents/TS/1").get_data(as_text=True))
        self.be.execute("UPDATE transfer_slip_items SET qty_released = qty_released - 1 WHERE id = "
                        "(SELECT MIN(id) FROM transfer_slip_items)")
        self.be.commit()
        self.assertIn("Changed after signing", self.c.get("/documents/TS/1").get_data(as_text=True))
        pdf = self.c.get("/documents/TS/1.pdf")
        self.assertEqual(pdf.status_code, 200)

    # ---- PDF ----
    def test_pdf_download_has_logo_and_signature_image(self):
        self.login("whmgr")
        self.c.post("/signature", data={"action": "save", "signature_data": PNG})
        self.chain()
        r = self.c.get("/documents/TS/1.pdf")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "application/pdf")
        body = r.get_data()
        self.assertTrue(body.startswith(b"%PDF"))
        self.assertGreaterEqual(body.count(b"/Subtype /Image"), 2)      # logo + the manager's signature
        self.assertIn("attachment" if False else "inline", r.headers["Content-Disposition"])

    def test_every_document_type_makes_a_pdf(self):
        self.chain()
        self.receive()
        self.receive_po(b=(4, 3, 1, "damaged casing"))
        self.login("owner")
        for path in ("PO/1", "RF/1", "TS/1", "AR/1", "RM/1", "DR/1", "REF/1"):
            r = self.c.get("/documents/%s.pdf" % path)
            self.assertEqual(r.status_code, 200, path)
            self.assertTrue(r.get_data().startswith(b"%PDF"), path)

    def test_a_damaged_stored_signature_never_breaks_the_pdf(self):
        """Old pad code only checked the text prefix, so half-saved images can exist in the database."""
        good = base64.b64decode(make_png().split(",", 1)[1])
        for raw in (good[: len(good) // 2], good[:40], b"not an image", b""):
            self.setUp()
            self.be.execute("UPDATE users SET signature_data = :d WHERE id = 2",
                            d="data:image/png;base64," + base64.b64encode(raw).decode())
            self.be.commit()
            self.chain()
            self.login("whmgr")
            r = self.c.get("/documents/TS/1.pdf")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.get_data().startswith(b"%PDF"))
            self.assertEqual(self.c.get("/documents/TS/1").status_code, 200)

    def test_pdf_respects_scope_and_unknown_types(self):
        self.chain()
        self.login("shopstaff")
        self.assertEqual(self.c.get("/documents/NOPE/1.pdf").status_code, 404)
        self.assertEqual(self.c.get("/documents/TS/999.pdf").status_code, 404)

    # ---- the signature pad ----
    def test_pad_rejects_fake_blank_and_oversize_images(self):
        self.login("whmgr")
        fake = "data:image/png;base64," + base64.b64encode(b"this is not an image").decode()
        for data in (fake, make_png(ink=False), "data:image/png;base64,@@@", "", "javascript:alert(1)"):
            self.c.post("/signature", data={"action": "save", "signature_data": data})
            self.assertIsNone(self.be.one("SELECT signature_data FROM users WHERE id = 2")["signature_data"])

    def test_pad_normalises_and_upload_removes_white_background(self):
        self.login("whmgr")
        white = Image.new("RGB", (900, 500), "white")
        ImageDraw.Draw(white).line([(50, 400), (300, 100), (600, 380), (850, 60)], fill=(20, 20, 20), width=14)
        buf = io.BytesIO()
        white.save(buf, "JPEG")
        buf.seek(0)
        r = self.c.post("/signature", data={"action": "save", "signature_file": (buf, "sig.jpg")},
                        content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        saved = self.be.one("SELECT signature_data FROM users WHERE id = 2")["signature_data"]
        img = Image.open(io.BytesIO(base64.b64decode(saved.split(",", 1)[1])))
        self.assertLessEqual(img.size[0], 600)
        self.assertLessEqual(img.size[1], 200)
        self.assertLess(img.getchannel("A").getextrema()[0], 10)       # transparent where the paper was

    def test_staff_have_no_signature_page(self):
        self.login("whstaff")
        self.assertEqual(self.c.get("/signature").status_code, 403)
        self.login("shopstaff")
        self.assertNotIn("My signature", self.c.get("/").get_data(as_text=True))

    def test_company_name_is_spelled_like_the_logo(self):
        self.login("owner")
        page = self.c.get("/").get_data(as_text=True)
        self.assertIn("3T TIRE SUPPLY", page)
        self.assertNotIn("TIER", page)


if __name__ == "__main__":
    unittest.main()
