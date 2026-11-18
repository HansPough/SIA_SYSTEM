# Changes: signing, PDF, branding, navigation

Run once on an existing database (after the earlier migrations), in this order:
`psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/migrations/010_signature_integrity.sql`
`psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/migrations/011_driver_signature.sql`
`psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/migrations/012_split_ordering.sql`
then `pip install -r requirements.txt` (adds reportlab and Pillow) and `python scripts/check_setup.py`.

## Signing
- Signing now happens in the action button (Sign & send RF, Reserve, Sign & submit REF, Approve, Sign & mark sent,
  Sign & release, Sign & confirm receipt). Before, those buttons signed nothing and the "signature" boxes on the TS and
  AR screens were blank decoration.
- Name and role come from the account. The typed-name path is gone.
- A signed line cannot be replaced (app + database trigger). Owner can no longer sign a shop manager's line.
- Staff never sign (RM "Received by" is text). RM/DR are signed by the manager from "Documents to sign".
- SHA-256 fingerprint per signature; "changed after signing" warning on page and PDF.
- Signature image is validated (real PNG/JPG, has ink), cleaned, cropped; upload option added.

## If you already ran an earlier 011
Run `011_driver_signature.sql` again (it is safe to repeat). The column is now `captured_by_user_id`. An earlier version
used `captured_by`, which clashed with a text column of the same name that already existed in one database. The migration now
stops with a clear message if any of its columns already exist with a different type, and `scripts/check_setup.py` checks
column types too. Then run `python scripts/check_setup.py` and restart.

## Driver signature
- The driver signs on the warehouse screen after loading ("Driver signs"). Name comes from the assigned driver.
- "Sign & release" is blocked until the driver has signed, or the manager gives a written reason.

## Alerts
- Saving a ROP now raises or clears the alert immediately (it used to wait for the next stock movement).

## PDF
- Real server-side PDF: /documents/<TYPE>/<id>.pdf (logo, items, prepared-by, stamped signatures, driver hand-sign box).

## Branding and flow
- Company name fixed to "3T Tire Supply" (was "Tier") everywhere; one config value `COMPANY_NAME`.
- Black/white theme from the logo (CSS variables at the top of style.css).
- Menu: "To do" group, "Documents to sign" page and dashboard tile, banner asking managers to save a signature.
- DEV_LOGIN now defaults to OFF when the setting is missing.

## Process streamlining (no new migration)
- **PO:** the owner approves, only the warehouse manager sends ("Sign & mark as sent"). The owner no longer sees the send
  button and the server refuses it. A new PO is shown as "Ready to send" instead of "Draft".
- **Shop receiving:** the screen starts with "Check the TS" (released by, driver signed, vehicle). Every line must be ticked
  "Counted" before the AR can be signed; blank quantities no longer mean "everything accepted" on their own.
- **Warehouse:** "Prepare & assign TS" is one step (driver and vehicle chosen on the prepare list). After the loading check the
  staff member goes straight to the driver's signature.
- Not changed: reserve -> prepare TS are still separate steps.

## Ordering one REF from several suppliers (migration 012)
- **Choose suppliers** screen (was "compare"): one row per item still to order, with every supplier's offer (quotation or regular
  supplier's agreed price). The cheapest supplier that can cover the whole quantity is pre-selected. Per item choose a supplier,
  **Keep open** (order later) or **Drop** (a reason is required). A supplier that can only part-supply leaves the balance open.
- One **approval per supplier**, submitted together (same `batch_id`) and decided by the owner on **one screen**; the owner can
  approve one supplier and reject another (rejecting needs remarks). One click signs the REF once and each new PO.
- Each approval has its own lines (`purchase_approval_items`). Each approved supplier gets its own PO.
- The REF is **approved** only when no quantity is still open; it **closes** only when everything wanted has been received and
  nothing is open. Until then it shows "Partly ordered" and the warehouse manager can order the rest from another supplier,
  or drop the remaining items from the REF page.
- Cancelling or rejecting a PO frees its items again. A REF with active POs cannot be cancelled.
- `schema.sql` now includes migrations 010-012, so a new install needs only `schema.sql` (existing databases run the migrations).
- Old pending approvals are given their lines automatically when migration 012 runs.

## When a supplier does not have an item (no migration)
- **More RFQ rounds.** A REF used to allow exactly one RFQ. Now, while some items are still without a supplier, the warehouse
  manager can press **Ask more suppliers (new RFQ)** on the REF. The new RFQ lists only the items still open and can go to
  suppliers that were already asked or to new ones. Quotations for it are recorded as usual and show up on **Choose suppliers**.
- **Giving up is not dismissing.** It needs a real reason (at least 5 characters, not "drop"). If the item is still at or below
  its reorder point, a fresh restock alert is raised, and a shop RF still waiting for it is named in a message. The button is
  now "Give up" with a confirmation, and "Keep open / ask more suppliers" is the normal choice.
- **Suppliers and agreed prices** can be added and edited by the owner as well as the warehouse manager.
- `check_setup.py` now reports a missing `purchase_approval_items` table. The REF page shows a PO's total from its own lines.

## Supplier returns removed; giving up moved out of "Choose suppliers"
- **Supplier returns** (screens, menu, PO button, code) are removed because the discrepancy report covers goods that are short
  or rejected at receiving. The tables stay in the database and no data is deleted. (A supplier return also used to deduct
  stock that had already been accepted; if you ever need that, use a stock adjustment with a reason.)
- **Choose suppliers** now offers only a supplier or "Not now: keep it open". A supplier having "no stock" is a reason to ask
  more suppliers, not to give up. Giving up is done from the REF page, needs a real reason, and no longer happens at the same
  moment as choosing a supplier.
- **Restore:** a given-up item can be brought back (REF page, "Restore") until the REF is closed. After that, create a new REF.
## Interface redesign (migration 013 for existing databases)
Run `psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/migrations/013_alert_snooze.sql`
(new installs get it from `schema.sql`), then `python scripts/check_setup.py`.

- **Layout.** Content is centered (max 1180 px) next to a sticky sidebar. The top bar has a bell and a user menu
  (My signature, Log out, and the role preview in development).
- **Real buttons.** Everything that does something is a button (Open, Edit, Stock, Receive, Back, Log out, My signature...).
  Plain links are only used inside sentences and for document numbers.
- **Navigation.** Sidebar groups (To do, Purchasing, Requisition, Inventory, Delivery, Administration) are dropdowns: only the group
  you are in is open. Each item and group shows a red number when something waits for you. The bell lists the same numbers.
  The numbers come from the dashboard counts, so they always agree; snoozed alerts are not counted.
- **Tables** sit in their own frame. A wide table scrolls inside its frame and the page never scrolls sideways
  (checked at phone, tablet and laptop widths). Codes and plies no longer break across lines.
- **Documents open first.** REF, PO, receiving memo, discrepancy report and AR pages open on their document (like RF and TS);
  the operational details are below. There is no separate "View document" step. The PDF button stays. The Sign button
  for manager-signed lines (receiving memo, discrepancy report) is on the document itself.
- **Snooze instead of dismiss.** For low-stock alerts (RF) and restock alerts (REF): choose 1 day, 3 days, 1 week, 2 weeks
  or 1 month. The alert stays OPEN but is silent: no badge, no bell, not counted on the dashboard, shown under the
  "Snoozed" filter with its date, and it counts again by itself when the date passes. "Wake now" brings it back at once.
  No reason is needed. (Dismiss is no longer offered; old dismissed alerts remain visible under "Dismissed".)
- **Housekeeping.** The leftover supplier-return templates were deleted. Action bars (Sign, Release, PDF) stay in view on
  document pages; buttons inside data-entry forms stay in the normal flow so they never cover an input.
## Menu clean-up (no migration)
- **Discrepancies** is one menu item for the owner and the warehouse manager, with two tabs: **From suppliers** (discrepancy
  reports: goods that arrived short or rejected at receiving) and **To shops** (items a shop rejected or found short when it
  signed the AR). Each tab shows its own open count, the menu badge adds both, and the item stays highlighted on both tabs.
  They were never duplicates: one is supplier -> warehouse, the other warehouse -> shop. They were only named confusingly
  and listed side by side. The shop manager keeps **Delivery variances** (only their own shop's items).
- **Master data** (warehouse manager) now holds Items, Suppliers, Drivers and Vehicles: setup lists that rarely change.
  They no longer sit among the daily Delivery and Purchasing steps. For the owner, Suppliers moved from Purchasing to
  Administration. Purchasing is now only the flow: Restock alerts -> REF -> PO.
- The dashboard tile "Discrepancy reports" became "Discrepancies" and shows both counts.
## One standard for document buttons, sidebar that remembers, and a test seed (no migration)
- **Document buttons.** Every document card, on every page, has the same two tools in the same corner: **Download PDF** and
  **Print**. They replaced the mixed "PDF", "TS PDF", "AR PDF" and "Print" buttons that sat in different places. The bottom bar
  holds the workflow buttons (Sign, Release, Receive...) with **Back** always last. "Print" prints only its own document, so a TS
  page that also shows its AR prints just the one you pressed.
- **Sidebar.** Groups open and close independently and are remembered in the browser, so a group you open stays open on every page.
  The group of the page you are on is always open. (It used to behave like an accordion and closed the others.)
- **Scenario seed.** `python scripts/seed_scenarios.py --reset --yes` builds 40 scenarios on a demo database by driving the real
  screens, and writes a guide (`docs/TEST_SCENARIOS.md`). See that file for the safety rules.
## Documents, RF queue, input details and shorter wording (migration 014 for existing databases)
Run `psql -U postgres -d inventory_db -v ON_ERROR_STOP=1 -f database/migrations/014_invoice_and_supplier_details.sql`
(new installs get it from `schema.sql`), then `python scripts/check_setup.py`.

- **One header per document.** The document card is the only header, and it matches the PDF: logo and company name on the left,
  title, number, date and status on the right. The separate print header, the second page title and the "Tire Supply &amp; Inventory"
  tagline are gone (screen, print and PDF). Printing a TS page that also shows its AR puts each on its own page.
- **Signatures sit side by side** (up to three per row) on screen, in print and in the PDF.
- **Checkboxes** in tables are one centered column with no text beside the box (loading check, shop receiving).
- **RF queue.** Each RF shows one stage: Ready to reserve, Waiting for stock, Ready for transfer, Transfer in progress, In transit.
  The button is always **Review RF** (highlighted only when something can be reserved), or **Create transfer** / **Open transfer**.
  When no stock is available there is no Reserve button on the RF page, only "Waiting for stock" and a link to the restock alert.
  Pressing an old Reserve button with no stock no longer says "Reserved 0 unit(s)". The shortage goes to the restock alert
  when the RF is sent, so nobody has to press a button to flag it.
- **Stock out:** the e-invoice number is required for a sale and is used once (case-insensitive). A voided sale frees its number.
- **Receiving from a supplier:** supplier contact is shown; the supplier DR number and invoice number are each used once per
  supplier; new optional fields **Supplier invoice no.** and **Delivered by** appear on the receiving memo.
- **Wording.** 37 long instructions were cut to one short line each (about 8,900 to 3,500 characters of instruction text).
