"""The redesigned interface: sidebar attention badges, the bell, snoozing alerts, documents first on
every form page, real buttons, and tables that live in their own frame."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_delivery_screens import Base, WH, SHOP, A, B


def page(r):
    return r.get_data(as_text=True)


class SidebarTests(Base):
    def badge_for(self, html, label):
        """The number in the badge next to a sidebar item, or None."""
        m = re.search(r'<span>%s</span><span class="nav-badge">(\d+)</span>' % re.escape(label), html)
        return int(m.group(1)) if m else None

    def test_documents_to_sign_shows_a_badge_and_the_bell_counts_it(self):
        self.receive_po()                                # staff recorded a memo: the manager must sign it
        self.login("whmgr")
        html = page(self.c.get("/"))
        self.assertEqual(self.badge_for(html, "Documents to sign"), 1)
        self.assertRegex(html, r'<span class="bell-count">\d+</span>')
        self.assertIn("Needs your attention", html)
        self.assertRegex(html, r'attn-row" href="/documents/to-sign"><span>Documents to sign</span><span class="attn-n">1</span>')

    def test_group_badge_adds_up_its_items_and_clears_when_the_work_is_done(self):
        self.receive_po()
        self.login("whmgr")
        html = page(self.c.get("/"))
        self.assertRegex(html, r'(?s)data-group="To do"[^>]*>\s*<summary>.*?<span class="nav-badge" title="\d+ waiting">\d+</span>')
        self.c.post("/documents/RM/1/sign", data={"slot": "checked_by"})
        html = page(self.c.get("/"))
        self.assertIsNone(self.badge_for(html, "Documents to sign"))

    def test_all_clear_when_nothing_waits(self):
        self.login("shopstaff")
        html = page(self.c.get("/"))
        self.assertIn("All clear", html)
        self.assertNotRegex(html, r'class="bell-count"')

    def test_groups_are_dropdowns_and_only_the_current_one_is_open(self):
        self.login("owner")
        html = page(self.c.get("/admin/users"))
        self.assertRegex(html, r'<details class="nav-group has-active" data-group="Administration" open>')
        self.assertRegex(html, r'<details class="nav-group " data-group="Purchasing" >')
        self.assertRegex(html, r'<details class="nav-group " data-group="Inventory" >')

    def test_user_menu_has_real_buttons(self):
        self.login("owner")
        html = page(self.c.get("/"))
        self.assertRegex(html, r'<a class="btn" href="/signature">')
        self.assertRegex(html, r'<a class="btn danger" href="/logout">')
        self.assertRegex(html, r'<div class="who-card"><strong>owner</strong><span>Owner</span></div>')

    def test_badges_never_break_a_page(self):
        """If counting fails, the page still loads (just without badges)."""
        from app import nav_ops
        original = nav_ops.counts
        nav_ops.counts = lambda user: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.login("whmgr")
            r = self.c.get("/")
            self.assertEqual(r.status_code, 200)
            self.assertNotRegex(page(r), r'class="nav-badge"')
        finally:
            nav_ops.counts = original


class SnoozeTests(Base):
    """Snooze: silent for a few days, the alert stays open and counts again by itself."""

    def make_alert(self, notif_type, location):
        self.be.execute("INSERT INTO notifications (notif_type, location_id, status) VALUES (:t, :l, 'active')",
                        t=notif_type, l=location)
        self.be.commit()
        return self.be.one("SELECT MAX(id) AS i FROM notifications")["i"]

    def status(self, alert_id):
        return self.be.one("SELECT status, snoozed_until FROM notifications WHERE id = :i", i=alert_id)

    # (module, notif type, location, role, list endpoint, label in the sidebar)
    CASES = (("requisition", "low_stock", SHOP, "shopmgr", "Low-stock alerts"),
             ("purchasing", "restock", WH, "whmgr", "Restock alerts"))

    def test_snooze_keeps_the_alert_open_but_silent_and_it_wakes_by_itself(self):
        import datetime as dt
        for mod, ntype, loc, user, label in self.CASES:
            aid = self.make_alert(ntype, loc)
            self.login(user)
            html = page(self.c.get("/"))
            self.assertRegex(html, r'<span>%s</span><span class="nav-badge">1</span>' % label, mod)
            r = self.c.post("/%s/alerts/%d/snooze" % (mod, aid), data={"days": "3"}, follow_redirects=True)
            self.assertIn("is silent until", page(r), mod)
            row = self.status(aid)
            self.assertEqual(row["status"], "active", mod)                   # NOT closed, NOT dismissed
            self.assertEqual(str(row["snoozed_until"])[:10], (dt.date.today() + dt.timedelta(days=3)).isoformat(), mod)
            html = page(self.c.get("/"))
            self.assertNotRegex(html, r'<span>%s</span><span class="nav-badge">' % label, mod)   # silent
            # it is still listed, under Snoozed, with its date
            lst = page(self.c.get("/%s/alerts?status=snoozed" % mod))
            self.assertIn("Snoozed until", lst, mod)
            self.assertNotIn("Snoozed until", page(self.c.get("/%s/alerts?status=active" % mod)), mod)
            # the snooze date passes: it counts again, nobody has to do anything
            self.be.execute("UPDATE notifications SET snoozed_until = :d WHERE id = :i",
                            d=(dt.date.today() - dt.timedelta(days=1)).isoformat(), i=aid)
            self.be.commit()
            html = page(self.c.get("/"))
            self.assertRegex(html, r'<span>%s</span><span class="nav-badge">1</span>' % label, mod)
            self.be.execute("DELETE FROM notifications"); self.be.commit()

    def test_wake_now_makes_it_count_again(self):
        for mod, ntype, loc, user, label in self.CASES:
            aid = self.make_alert(ntype, loc)
            self.login(user)
            self.c.post("/%s/alerts/%d/snooze" % (mod, aid), data={"days": "7"})
            r = self.c.post("/%s/alerts/%d/wake" % (mod, aid), data={}, follow_redirects=True)
            self.assertIn("is active again", page(r), mod)
            self.assertIsNone(self.status(aid)["snoozed_until"], mod)
            self.assertRegex(page(self.c.get("/")), r'<span>%s</span><span class="nav-badge">1</span>' % label, mod)
            self.be.execute("DELETE FROM notifications"); self.be.commit()

    def test_snooze_rules(self):
        for mod, ntype, loc, user, label in self.CASES:
            aid = self.make_alert(ntype, loc)
            self.login(user)
            for bad in ("0", "2", "-1", "abc", "", "9999"):               # only the offered choices
                r = self.c.post("/%s/alerts/%d/snooze" % (mod, aid), data={"days": bad}, follow_redirects=True)
                self.assertIn("Choose how long", page(r), (mod, bad))
            self.assertIsNone(self.status(aid)["snoozed_until"], mod)
            # a closed alert cannot be snoozed
            self.be.execute("UPDATE notifications SET status = 'completed' WHERE id = :i", i=aid); self.be.commit()
            r = self.c.post("/%s/alerts/%d/snooze" % (mod, aid), data={"days": "3"}, follow_redirects=True)
            self.assertIn("already completed", page(r), mod)
            # waking something that is not snoozed
            self.be.execute("UPDATE notifications SET status = 'active' WHERE id = :i", i=aid); self.be.commit()
            r = self.c.post("/%s/alerts/%d/wake" % (mod, aid), data={}, follow_redirects=True)
            self.assertIn("not snoozed", page(r), mod)
            self.be.execute("DELETE FROM notifications"); self.be.commit()

    def test_only_the_alerts_own_manager_can_snooze_it(self):
        shop_alert = self.make_alert("low_stock", SHOP)
        wh_alert = self.make_alert("restock", WH)
        # wrong role
        for who in ("owner", "whmgr", "whstaff", "shopstaff"):
            self.login(who)
            self.assertEqual(self.c.post("/requisition/alerts/%d/snooze" % shop_alert, data={"days": "3"}).status_code, 403, who)
        for who in ("owner", "shopmgr", "whstaff", "shopstaff"):
            self.login(who)
            self.assertEqual(self.c.post("/purchasing/alerts/%d/snooze" % wh_alert, data={"days": "3"}).status_code, 403, who)
        # right role, someone else's alert
        self.login("shopmgr")
        r = self.c.post("/requisition/alerts/%d/snooze" % wh_alert, data={"days": "3"}, follow_redirects=True)
        self.assertIn("not found", page(r))
        self.assertIsNone(self.status(wh_alert)["snoozed_until"])

    def test_dismiss_is_no_longer_offered_but_snooze_is(self):
        aid = self.make_alert("low_stock", SHOP)
        self.login("shopmgr")
        html = page(self.c.get("/requisition/alerts/%d" % aid))
        self.assertIn("Snooze", html)
        self.assertNotIn("Dismiss alert", html)
        self.assertRegex(html, r'<select name="days"')

    def test_snoozed_alert_page_shows_wake_now(self):
        aid = self.make_alert("restock", WH)
        self.login("whmgr")
        self.c.post("/purchasing/alerts/%d/snooze" % aid, data={"days": "14"})
        html = page(self.c.get("/purchasing/alerts/%d" % aid))
        self.assertIn("Wake now", html)
        self.assertIn("It stays open", html)
        self.assertIn("Create REF", html)                                 # still actionable while snoozed

    def test_dashboard_counts_ignore_snoozed_alerts(self):
        aid = self.make_alert("low_stock", SHOP)
        self.login("shopmgr")
        self.assertRegex(page(self.c.get("/")), r"1 active alert|Active alerts?:?\s*1")
        self.c.post("/requisition/alerts/%d/snooze" % aid, data={"days": "3"})
        from app import dashboard_ops
        with self.app.app_context():
            s = dashboard_ops.summary({"id": self.uid["shopmgr"], "role": "shop_manager", "location_id": SHOP})
        self.assertEqual(s["active_alerts"], 0)


class MenuStructureTests(Base):
    """Related screens share one menu item, and setup lists live under Master data."""

    def group_items(self, html, group):
        """The labels listed inside one sidebar group."""
        m = re.search(r'(?s)data-group="%s".*?</details>' % re.escape(group), html)
        self.assertIsNotNone(m, group)
        return re.findall(r'<a href="[^"]*" class="nav-link[^"]*"[^>]*><span>([^<]+)</span>', m.group(0))

    def test_discrepancies_is_one_menu_item_for_owner_and_warehouse_manager(self):
        for who in ("owner", "whmgr"):
            self.login(who)
            html = page(self.c.get("/"))
            self.assertIn(">Discrepancies<", html, who)
            self.assertNotIn(">Delivery variances<", html, who)
            self.assertNotIn(">Discrepancy reports<", html, who)
            self.assertIn("Discrepancies", self.group_items(html, "Delivery"), who)

    def test_shop_manager_keeps_the_variances_item(self):
        self.login("shopmgr")
        html = page(self.c.get("/"))
        self.assertIn("Delivery variances", self.group_items(html, "Delivery"))
        self.assertNotIn("Discrepancies", html.split('data-group="Delivery"')[1].split("</details>")[0])

    def test_both_tabs_keep_discrepancies_highlighted_and_show_the_tabs(self):
        for who in ("owner", "whmgr"):
            self.login(who)
            for path, active_tab in (("/delivery/discrepancies", "From suppliers"), ("/delivery/variances", "To shops")):
                html = page(self.c.get(path))
                self.assertRegex(html, r'class="nav-link active" aria-current="page"><span>Discrepancies</span>', (who, path))
                self.assertRegex(html, r'<a class="tab active" role="tab" href="[^"]*">\s*%s' % active_tab, (who, path))
                self.assertIn("From suppliers", html); self.assertIn("To shops", html)

    def test_shop_manager_has_no_tabs_because_they_only_see_their_own_variances(self):
        self.login("shopmgr")
        html = page(self.c.get("/delivery/variances"))
        self.assertNotIn('role="tablist"', html)
        self.assertRegex(html, r'class="nav-link active" aria-current="page"><span>Delivery variances</span>')
        self.assertIn("Delivery Variances", html)

    def test_open_variances_count_on_the_menu_the_tab_and_the_shop_managers_own_item(self):
        self.chain()
        self.receive(qty_a=3, reason="damaged in transit")           # the shop rejects part of the delivery
        self.login("whmgr")
        html = page(self.c.get("/"))
        self.assertRegex(html, r'<span>Discrepancies</span><span class="nav-badge">1</span>')
        self.assertRegex(page(self.c.get("/delivery/discrepancies")), r'To shops<span class="tab-count">1</span>')
        self.login("owner")
        self.assertRegex(page(self.c.get("/")), r'<span>Discrepancies</span><span class="nav-badge">1</span>')
        self.login("shopmgr")
        self.assertRegex(page(self.c.get("/")), r'<span>Delivery variances</span><span class="nav-badge">1</span>')

    def test_tab_counts_separate_supplier_and_shop_items(self):
        self.receive_po(b=(4, 3, 1, "damaged casing"))                # a supplier discrepancy report
        self.login("whmgr")
        html = page(self.c.get("/delivery/variances"))
        self.assertRegex(html, r'From suppliers<span class="tab-count">1</span>')
        self.assertNotRegex(html, r'To shops<span class="tab-count">')

    def test_drivers_vehicles_and_suppliers_are_master_data_for_the_warehouse_manager(self):
        self.login("whmgr")
        html = page(self.c.get("/"))
        master = self.group_items(html, "Master data")
        for label in ("Items", "Suppliers", "Drivers", "Vehicles"):
            self.assertIn(label, master)
        delivery = self.group_items(html, "Delivery")
        self.assertNotIn("Drivers", delivery); self.assertNotIn("Vehicles", delivery)
        self.assertNotIn("Suppliers", self.group_items(html, "Purchasing"))
        self.assertEqual(self.group_items(html, "Purchasing"),
                         ["Restock alerts", "Replenishments (REF)", "Purchase orders (PO)"])

    def test_suppliers_sit_under_administration_for_the_owner(self):
        self.login("owner")
        html = page(self.c.get("/"))
        self.assertIn("Suppliers", self.group_items(html, "Administration"))
        self.assertNotIn("Suppliers", self.group_items(html, "Purchasing"))
        self.assertEqual(self.group_items(html, "Purchasing"), ["Replenishments (REF)", "Purchase orders (PO)"])

    def test_every_menu_item_still_opens_for_every_role(self):
        from app.roles import MENU
        from flask import url_for
        users = {"owner": "owner", "shop_manager": "shopmgr", "shop_staff": "shopstaff",
                 "warehouse_manager": "whmgr", "warehouse_staff": "whstaff"}
        for role, who in users.items():
            self.login(who)
            with self.app.test_request_context():
                paths = [url_for(ep) for label, ep in MENU[role] if label != "--"]
            for path in paths:
                self.assertEqual(self.c.get(path).status_code, 200, (role, path))


class DocumentFirstTests(Base):
    """Opening a form shows its document straight away; there is no separate 'View document' step."""

    def assert_document_first(self, html, doc_no_prefix):
        self.assertIn('class="card doc"', html)
        self.assertIn(doc_no_prefix, html)
        self.assertIn("Signatures", html)
        self.assertNotIn(">View document<", html)
        self.assertIn("Progress and details" if "Progress" in html else "Form details", html)

    def test_po_and_ref_pages_open_on_the_document(self):
        self.chain()
        self.login("owner")
        po = page(self.c.get("/purchasing/po/1"))
        self.assertIn('class="card doc"', po); self.assertIn("Purchase Order", po)
        self.assertNotIn(">View document<", po)
        self.assertIn("Progress and details", po)
        ref = page(self.c.get("/purchasing/ref/1"))
        self.assertIn('class="card doc"', ref); self.assertIn("Replenishment Form", ref)
        self.assertNotIn(">View document<", ref)
        self.assertIn("Form details", ref)
        self.assertIn("Download PDF", ref)

    def test_receiving_memo_and_ar_open_on_the_document(self):
        self.chain()
        self.receive()
        self.receive_po()
        self.login("whmgr")
        rm = page(self.c.get("/delivery/receiving/rm/1"))
        self.assertIn('class="card doc"', rm); self.assertIn("Receiving Memo", rm)
        self.assertNotIn(">View document<", rm)
        ts = page(self.c.get("/delivery/transfers/1"))
        self.assertIn("Transfer Slip", ts)
        self.assertIn("Acknowledgment receipt", ts)                         # the AR shows under the TS
        self.assertIn("Acknowledgment Receipt", ts)

    def test_manager_signs_a_staff_memo_right_on_its_page(self):
        self.receive_po()
        self.login("whmgr")
        html = page(self.c.get("/delivery/receiving/rm/1"))
        self.assertIn('name="slot" value="checked_by"', html)               # the Sign button is on the document itself
        self.login("whstaff")
        self.assertNotIn('name="slot" value="checked_by"', page(self.c.get("/delivery/receiving/rm/1")))

    def test_discrepancy_page_opens_on_the_document_and_keeps_its_form(self):
        self.receive_po(b=(4, 3, 1, "damaged casing"))
        self.login("whmgr")
        html = page(self.c.get("/delivery/discrepancies/1"))
        self.assertIn('class="card doc"', html); self.assertIn("Discrepancy Report", html)
        self.assertNotIn(">View document<", html)
        self.assertIn("Save follow-up", html)

    def test_the_standalone_document_page_still_works_for_printing(self):
        self.chain()
        self.login("owner")
        self.assertEqual(self.c.get("/documents/PO/1").status_code, 200)
        self.assertEqual(self.c.get("/documents/PO/1.pdf").status_code, 200)


class DocumentButtonStandardTests(Base):
    """One standard for every document: Download PDF and Print inside the document card, Back last."""

    def pages(self):
        """(role, path, number of documents shown on the page)."""
        self.chain()
        self.receive()
        self.receive_po(b=(4, 3, 1, "damaged casing"))
        return [("owner", "/purchasing/po/1", 1), ("owner", "/purchasing/ref/1", 1),
                ("whmgr", "/delivery/transfers/1", 2),            # the TS and its AR
                ("whmgr", "/delivery/receiving/rm/1", 1), ("whmgr", "/delivery/discrepancies/1", 1),
                ("whmgr", "/documents/PO/1", 1), ("owner", "/documents/TS/1", 1)]

    def test_every_document_card_has_the_same_two_tools(self):
        for who, path, n_docs in self.pages():
            self.login(who)
            html = page(self.c.get(path))
            self.assertEqual(html.count('class="card doc"'), n_docs, path)
            self.assertEqual(html.count("Download PDF"), n_docs, path)
            self.assertEqual(html.count("data-print-doc"), n_docs, path)
            # and the tools sit inside the card, before the document header
            for m in re.finditer(r'<div class="card doc">\s*<div class="doc-tools no-print">', html):
                pass
            self.assertEqual(len(re.findall(r'<div class="card doc">\s*<div class="doc-tools no-print">', html)), n_docs, path)

    def test_no_page_still_has_its_own_differently_named_pdf_or_print_button(self):
        for who, path, _n in self.pages():
            self.login(who)
            html = page(self.c.get(path))
            for old in (">PDF<", ">TS PDF<", ">AR PDF<", 'onclick="window.print()"'):
                self.assertNotIn(old, html, (path, old))

    def test_the_pdf_button_downloads_the_file(self):
        self.chain()
        self.login("owner")
        html = page(self.c.get("/purchasing/po/1"))
        self.assertRegex(html, r'<a class="btn small" href="/documents/PO/1\.pdf" download>Download PDF</a>')
        r = self.c.get("/documents/PO/1.pdf")
        self.assertEqual(r.mimetype, "application/pdf")
        self.assertIn("PO-", r.headers["Content-Disposition"])

    def test_back_is_always_the_last_button_of_the_bottom_bar(self):
        for who, path, _n in self.pages():
            self.login(who)
            html = page(self.c.get(path))
            bars = re.findall(r'<div class="actions[^"]*">(.*?)\n</div>', html, re.S)
            self.assertTrue(bars, path)
            last_bar = bars[-1]
            buttons = re.findall(r'<(?:a|button)\b[^>]*class="btn[^"]*"[^>]*>(.*?)</(?:a|button)>', last_bar, re.S)
            self.assertEqual(re.sub(r"<[^>]+>", "", buttons[-1]).strip(), "Back", (path, buttons))

    def test_the_standalone_document_page_uses_the_same_standard(self):
        self.chain()
        self.login("owner")
        html = page(self.c.get("/documents/PO/1"))
        self.assertEqual(html.count("Download PDF"), 1)
        self.assertNotIn("doc-toolbar", html)
        self.assertEqual(html.count(">Back<"), 1)

    def test_sidebar_groups_are_remembered_and_not_an_accordion(self):
        self.login("whmgr")
        html = page(self.c.get("/"))
        self.assertIn('localStorage.getItem("nav.open")', html)          # restored before the first paint
        js = self.c.get("/static/js/app.js").get_data(as_text=True)
        self.assertIn('localStorage.setItem("nav.open"', js)
        self.assertNotIn("other.open = false", js)                       # opening one group no longer closes the others


class ButtonsAndTablesTests(Base):
    def test_table_actions_are_buttons_not_plain_links(self):
        self.login("owner")
        html = page(self.c.get("/admin/items"))
        self.assertRegex(html, r'<a class="btn small" href="/admin/items/\d+/edit">Edit</a>')
        self.assertNotRegex(html, r'<a href="/admin/items/\d+/edit">Edit</a>')
        self.login("whmgr")
        html = page(self.c.get("/delivery/transfers/new"))
        self.assertNotRegex(html, r'<td[^>]*><a href="[^"]*">(Open|Edit|Receive)</a></td>')

    def test_every_data_table_is_in_its_own_scrolling_frame(self):
        """A wide table scrolls inside its frame; the page itself never scrolls sideways."""
        pages = {"owner": ["/admin/items", "/admin/users", "/purchasing/ref", "/purchasing/po", "/inventory/stock",
                           "/delivery/transfers"],
                 "whmgr": ["/purchasing/suppliers", "/inventory/rfqueue", "/delivery/receiving"]}
        for user, paths in pages.items():
            self.login(user)
            for path in paths:
                r = self.c.get(path)
                if r.status_code != 200:
                    continue
                html = page(r)
                for m in re.finditer(r'<table class="data', html):
                    before = html[max(0, m.start() - 40):m.start()]
                    self.assertIn('class="table-wrap"', before, (user, path))

    def test_user_facing_texts_are_not_regressed(self):
        self.login("owner")
        html = page(self.c.get("/"))
        self.assertIn("3T TIRE SUPPLY", html)
        self.assertNotIn("TIER", html)


class StatusColourTests(Base):
    """One status tone everywhere: ok / warn / danger / info / neutral."""

    def test_the_tone_map_covers_every_status_in_the_database(self):
        from app.statuses import tone
        for value, expected in (
                ("active", "ok"), ("approved", "ok"), ("completed", "ok"), ("received", "ok"),
                ("pending", "warn"), ("partially_received", "warn"), ("in_transit", "warn"),
                ("void_requested", "warn"), ("cancelled", "danger"), ("rejected", "danger"),
                ("draft", "neutral"), ("inactive", "neutral"), ("sent", "info"), ("Partially received", "warn"),
                ("Delivered with variance", "warn"), ("something unknown", "neutral")):
            self.assertEqual(tone(value), expected, value)

    def test_list_pages_render_coloured_status_badges(self):
        self.login("shopmgr")
        html = page(self.c.get("/inventory/stock"))
        self.assertRegex(html, r'<span class="badge s-(ok|warn|danger)">')

    def test_document_status_uses_the_same_badge(self):
        self.chain()
        self.login("owner")
        html = page(self.c.get("/documents/PO/1"))
        self.assertRegex(html, r'<div class="doc-status">\s*<span class="badge s-(ok|warn|danger|info|neutral)">')

    def test_an_active_alert_is_amber_even_though_items_are_green_when_active(self):
        self.be.execute("INSERT INTO notifications (notif_type, location_id, status) "
                        "VALUES ('low_stock', :l, 'active')", l=SHOP)
        self.be.commit()
        self.login("shopmgr")
        html = page(self.c.get("/requisition/alerts"))
        self.assertIn('class="badge s-warn">Active', html)
        self.login("owner")
        self.assertIn('class="badge s-ok">Active', page(self.c.get("/admin/items")))


class NewWorkflowTests(Base):
    """Small flow fixes: real document numbers, stock shortcuts, AR preview."""

    def test_new_documents_show_the_next_number_instead_of_auto(self):
        self.login("shopmgr")
        rf = page(self.c.get("/requisition/rf/new"))
        self.assertIn("RF-2026-0001", rf)
        self.assertNotIn('value="auto"', rf)
        self.login("whmgr")
        ref = page(self.c.get("/purchasing/ref/new"))
        self.assertIn("REF-2026-0001", ref)
        self.assertNotIn('value="auto"', ref)

    def test_stock_screen_offers_count_and_adjustment(self):
        self.login("shopmgr")
        html = page(self.c.get("/inventory/stock"))
        self.assertIn('href="/inventory/count"', html)
        self.assertIn('href="/inventory/adjustments/new?from=stock"', html)   # Back returns to Stock

    def test_stock_count_and_adjustment_add_rows_by_search(self):
        self.login("shopmgr")
        for path, kind in (("/inventory/count", "count"), ("/inventory/adjustments/new", "adjust")):
            html = page(self.c.get(path))
            self.assertIn('data-add-item="%s"' % kind, html, path)
            self.assertIn('%s-row-template' % kind, html, path)
            self.assertIn('data-allow-empty', html, path)
            self.assertIn('id="%s-search"' % kind, html, path)

    def test_progress_and_details_include_the_lifecycle(self):
        self.chain()
        self.login("whmgr")
        html = page(self.c.get("/delivery/transfers/1"))
        self.assertIn("Progress and details", html)
        self.assertIn("Lifecycle", html)
        self.assertIn("Created", html)
        self.assertIn("Signed (Warehouse Manager)", html)

    def test_delivered_transfer_offers_the_acknowledgment_receipt_preview(self):
        self.chain()
        self.receive()
        self.login("shopmgr")
        html = page(self.c.get("/delivery/deliveries"))
        self.assertIn("/documents/AR/1", html)


class LiveUpdateTests(Base):
    """The /api/live payload behind the bell and the automatic refresh."""

    def live(self):
        return self.c.get("/api/live")

    def test_live_state_requires_a_login(self):
        self.assertEqual(self.live().status_code, 401)

    def test_live_state_has_counts_the_bell_and_sidebar_need(self):
        self.receive_po()                       # a memo the manager must sign
        self.login("whmgr")
        data = self.live().get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["rev"])
        self.assertGreaterEqual(data["total"], 1)
        self.assertIn("Documents to sign", [row["label"] for row in data["attention"]])
        groups = {g["label"]: g for g in data["groups"]}
        self.assertIn("To do", groups)
        self.assertTrue(any(i["url"] == "/documents/to-sign" for i in groups["To do"]["items"]))

    def test_revision_changes_when_another_user_saves_work(self):
        self.login("whmgr")
        before = self.live().get_json()["rev"]
        self.receive_po()
        self.login("whmgr")
        after = self.live().get_json()["rev"]
        self.assertNotEqual(before, after)

    def test_pages_that_can_refresh_themselves_and_pages_that_cannot(self):
        self.login("whmgr")
        self.assertIn('data-live="1"', page(self.c.get("/delivery/transfers")))
        self.assertIn("/static/js/live.js", page(self.c.get("/")))
        self.login("shopmgr")
        self.assertIn('data-live="0"', page(self.c.get("/inventory/adjustments/new")))
        self.assertIn('data-live="0"', page(self.c.get("/inventory/issues/new")))


if __name__ == "__main__":
    unittest.main()
