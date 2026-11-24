# Login, users, locations, security and printable documents

## Login

- `/login` checks `users.username` + `users.password_hash` with Werkzeug
  `check_password_hash`. The seed users all use `changeme`.
- New passwords are hashed with `generate_password_hash` (scrypt by default) and
  are never stored or logged in plain text.
- `DEV_LOGIN=1` keeps the `/dev/role/<role>` and `/dev/user/<id>` shortcuts for
  development. Set `DEV_LOGIN=0` in `.env` on a real deployment; those routes
  then return 404.
- Logout clears the session and writes an audit row.

## Users and locations (owner)

| Screen | Route |
|---|---|
| Users list / search | `/admin/users` |
| New user | `/admin/users/new` |
| Edit user (role, location, password, active) | `/admin/users/<id>/edit` |
| Locations list | `/admin/locations` |
| New / edit location | `/admin/locations/new`, `/admin/locations/<id>/edit` |

Rules enforced in `admin_ops.py`:

- Username is unique, 3–30 characters (`letters, numbers, . _ -`), password at
  least 8 characters on create/change.
- An owner has no location; shop roles need a shop; warehouse roles need a
  warehouse.
- The owner cannot remove their own owner role or deactivate themselves.
- A location cannot be deactivated while active users or stock still use it, or while it
  still has open paperwork: an open RF, an active or `in_transit` TS, an open PO, a pending
  stock adjustment or a void request waiting. The error names the documents; past five it
  lists the first five and says "…and N more".
- An item cannot be deactivated while it has stock, or while it is on an open RF/REF/PO,
  an active TS, a held reservation or a pending stock adjustment. The error names them,
  capped the same way.
- The last active shop manager of a shop cannot be deactivated, moved to another location,
  or given another role while a delivery is `in_transit` to that shop or an AR there is
  still unsigned.
- A location type cannot change once the location is used on a requisition.
- Every create/update writes an `audit_log` row (the password is not included).

## Security

- Session cookie: `HttpOnly`, `SameSite=Lax`, 12-hour lifetime. Set
  `SESSION_COOKIE_SECURE=1` when served over HTTPS.
- Every POST checks the browser `Origin`/`Referer` host against the request host
  and returns 400 on a cross-site post. This is the CSRF layer; no extra
  dependency is required.
- `run.py` starts with `debug=False`. Use `FLASK_DEBUG=1` only locally.
- `.env.example` documents `SECRET_KEY`, `DATABASE_URL`, `DEV_LOGIN` and
  `SESSION_COOKIE_SECURE`.

## Printable documents and e-signatures

| Document | Route |
|---|---|
| RF, REF, PO, TS, AR, RM, DR | `/documents/<TYPE>/<id>` |

Every detail screen now has a **Print / Sign** button. The page:

- has a clean A4 print layout with the 3T Tier Supply logo, document number,
  parties, items, totals, notes and signature boxes;
- uses the signer's **saved drawn signature** when one is on file, otherwise
  lets the role type a full name;
- stores `doc_type`, `doc_id`, `slot`, `signer_user_id`, `signer_name`,
  `signature_data`, `remarks` and `signed_at` in `document_signatures`;
- prints the drawn signature (or the typed name) and timestamp under the line.

## Ordering from several suppliers

A REF can be ordered from more than one supplier. On **Choose suppliers**, each open item goes to exactly one
supplier (a quotation or a regular supplier's agreed price) or is **kept open**; a supplier who quoted an item is
listed once, with that quotation. The screen preselects the cheapest offer per item, prefills each row's
**Qty to order** and shows its **subtotal**, plus a **split preview** with every supplier's items and estimated PO
total before you submit. One approval is created **per supplier**;
the owner sees every supplier of that submission on one approval screen and decides each one, and each approved
supplier gets its own PO, which the warehouse manager sends. Choosing the same supplier again in a later RFQ round
merges into that supplier's single approval and PO. The REF is "approved" only when nothing is still open and
closes when all that was wanted has been received.

If a supplier does not have an item: **Keep open**, then **Ask more suppliers (new RFQ)** on the REF (a new round
covering only the open items, with any suppliers), or order it at a regular supplier's agreed price — agreed prices
work even when no RFQ was ever sent. Suppliers and their agreed prices are managed by the owner and the warehouse
manager (**Suppliers**). **Drop** is only on the REF page and only for an item you will not order at all: it
needs a real reason, and the item is flagged again in the restock alerts if it is still below its ROP.

## How signing works (migration `010_signature_integrity.sql`)

The button that sends or approves a form also signs it, in the same save. If signing fails the action is not saved.
The signer's **name and role always come from the logged-in account**: nothing is typed. A **signed line can never be
replaced** (enforced in the app and by a database trigger). Each signature stores a SHA-256 fingerprint of the
document content; if the document is changed afterwards, the page and PDF show a "changed after signing" warning.
**Staff never sign**: they appear as "Prepared by / Received by" text and a manager signs.

| Document | Line | Who | Signed by |
|---|---|---|---|
| RF | Requested by | shop manager | **Sign & send RF** |
| RF | Noted by | warehouse manager | **Reserve stock** in the RF queue (once) |
| REF | Prepared by | warehouse manager | **Sign & submit REF** |
| REF, PO | Approved by | owner | **Approve** in Approvals (signs both) |
| PO | Sent by | warehouse manager (the owner approves, so does not send) | **Sign & mark as sent** |
| TS | Released by | warehouse manager | **Sign & release** |
| TS | Driver | driver (no login) | **Driver signs** on the warehouse screen after loading (see below) |
| TS, AR | Received by | shop manager | **Sign & confirm receipt** |
| RM | Checked by | warehouse manager | signed on saving if the manager recorded it; otherwise **Documents to sign** |
| DR | Prepared by, Noted by | warehouse manager, then owner | **Documents to sign** |

### Driver signature (migration `011_driver_signature.sql`)

After the loading check the warehouse staff opens the TS and presses **Driver signs**, then hands the device to the driver.
The signature is saved under the **driver's name from the assigned driver** (never typed); the staff member who held the
device is recorded as "captured by". The manager cannot **Sign & release** until it exists. If it truly cannot be done on
screen, the manager writes a reason (kept on the TS and in the audit log) and releases anyway. A signature only counts for the
driver assigned now, so changing the driver requires a new signature. Drivers' signatures are not saved for reuse.

PDFs: `/documents/<TYPE>/<id>.pdf` (reportlab, built from the same data as the page; no browser Print needed).

## My signature

Owner, shop manager and warehouse manager open **My signature** to draw it once (mouse, pen, finger) or upload a photo
of a signature on white paper. The image is checked and cleaned on the server (must be a real PNG/JPG with ink, white
background removed, cropped, max 600x200). A manager with no drawn signature still signs; the PDF then shows the account
name, role and time. The image is copied into the signature record when signing, so changing it later never alters
documents already signed.

## Branding

The client logo is `app/static/img/logo.svg` and is used in the top bar, the
login page and the print header. The palette is defined at the top of
`app/static/css/style.css`. The logo is a single black mark on white, so the UI is ink black and white; change `--brand` and
`--brand-2` to re-colour the app. (The company name is `COMPANY_NAME` in config, default "3T Tire Supply".)
Print styles live at the bottom of the same file.

## Tests

`python -m unittest discover tests -v` includes `test_admin_screens.py` (login,
users, locations, CSRF) and `test_document_screens.py` (print views and
signatures).
