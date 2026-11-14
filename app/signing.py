"""E-signatures: who signed, when, and exactly what they signed.

Rules (all enforced here, in one place):

  * The signer's NAME and ROLE always come from the logged-in account.
    Nothing is typed, so nobody can sign as somebody else.
  * Only managers (and the owner) sign. Staff are shown as "prepared by"
    text on the document and never need a signature.
  * A signed line is FINAL. It cannot be replaced or removed.
  * Each signature stores a SHA-256 fingerprint of the document content at
    that moment. If the document is changed afterwards, the document page and
    the PDF show a "changed after signing" warning.
  * Signing normally happens INSIDE the action ("Sign & submit", "Approve &
    sign", "Sign & release", ...), in the same database transaction. If the
    signature fails, the action is not saved either.

Slot modes
  action  signed by the workflow button itself
  manual  the manager presses "Sign" on the document page (for documents that
          staff may have recorded, e.g. a receiving memo)
  captured the driver signs on the warehouse staff's screen after loading. The signature
          belongs to the driver (name from the assigned driver, never typed); the warehouse
          account that held the device is recorded as "captured by"
  wet     signed by hand on the printed copy
"""
import hashlib
import json

from . import inventory_service as inv
from .inventory_service import InventoryError
from .roles import ROLE_LABELS

# doc_type -> list of slots. Order = order on the printed document.
SLOTS = {
    "RF": [
        {"key": "requested_by", "label": "Requested by", "roles": ("shop_manager",), "mode": "action"},
        {"key": "noted_by", "label": "Noted by (Warehouse)", "roles": ("warehouse_manager",), "mode": "action"},
    ],
    "REF": [
        {"key": "prepared_by", "label": "Prepared by", "roles": ("warehouse_manager",), "mode": "action"},
        {"key": "approved_by", "label": "Approved by (Owner)", "roles": ("owner",), "mode": "action"},
    ],
    "PO": [
        {"key": "approved_by", "label": "Approved by (Owner)", "roles": ("owner",), "mode": "action"},
        {"key": "sent_by", "label": "Sent to supplier by", "roles": ("warehouse_manager",), "mode": "action"},
    ],
    "TS": [
        {"key": "released_by", "label": "Released by (Warehouse)", "roles": ("warehouse_manager",), "mode": "action"},
        {"key": "driver", "label": "Driver", "roles": ("warehouse_staff", "warehouse_manager"), "mode": "captured"},
        {"key": "received_by", "label": "Received by (Shop)", "roles": ("shop_manager",), "mode": "action"},
    ],
    "AR": [
        {"key": "received_by", "label": "Received by (Shop)", "roles": ("shop_manager",), "mode": "action"},
    ],
    "RM": [
        {"key": "checked_by", "label": "Checked by (Warehouse)", "roles": ("warehouse_manager",), "mode": "manual"},
    ],
    "DR": [
        {"key": "prepared_by", "label": "Prepared by (Warehouse)", "roles": ("warehouse_manager",), "mode": "manual"},
        {"key": "noted_by", "label": "Noted by (Owner)", "roles": ("owner",), "mode": "manual"},
    ],
}

DOC_TYPES = tuple(SLOTS)


def slot_def(doc_type, slot):
    for s in SLOTS.get((doc_type or "").upper(), []):
        if s["key"] == slot:
            return s
    return None


# ---------------------------------------------------------------------
# Fingerprint of what was signed
# ---------------------------------------------------------------------

def content_hash(doc_type, doc_no, party_from, party_to, lines, total=None):
    """SHA-256 over the parts of a document that must not change after signing.

    Status, notes and delivery progress are left out on purpose: they change in
    normal use and would make every signature look tampered with.
    """
    payload = {
        "type": (doc_type or "").upper(),
        "no": str(doc_no),
        "from": [str(x) for x in party_from],
        "to": [str(x) for x in party_to],
        "lines": [[str(l.get("code")), str(l.get("qty")),
                   str(l.get("price")) if l.get("price") is not None else ""] for l in lines],
        "total": str(total) if total is not None else "",
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


# ---------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------

def signatures_for(doc_type, doc_id):
    rows = inv.backend().all(
        "SELECT s.*, cu.full_name AS captured_by_name FROM document_signatures s "
        "LEFT JOIN users cu ON cu.id = s.captured_by_user_id WHERE s.doc_type = :t AND s.doc_id = :i",
        t=(doc_type or "").upper(), i=doc_id)
    return {r["slot"]: r for r in rows}


def role_title(role_code):
    if role_code == "driver":
        return "Driver"
    return ROLE_LABELS.get(role_code, role_code or "")


def user_has_signature(user_id):
    row = inv.backend().one("SELECT signature_data FROM users WHERE id = :i", i=user_id)
    return bool(row and row["signature_data"])


# ---------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------

def _client_ip():
    try:
        from flask import request
        return (request.remote_addr or "")[:45] or None
    except RuntimeError:          # no request (scripts, tests without a context)
        return None


def sign_slot(user, doc_type, doc_id, slot, doc_hash, *, skip_if_signed=False):
    """Record one signature for the logged-in user. Returns the slot label,
    or None when skip_if_signed is set and the line was already signed.

    Does not commit: the caller commits together with the action itself.
    """
    doc_type = (doc_type or "").upper()
    spec = slot_def(doc_type, slot)
    if spec is None:
        raise InventoryError("Unknown signature line.")
    if spec["mode"] == "wet":
        raise InventoryError("This line is signed by hand on the printed copy.")
    if spec["mode"] == "captured":
        raise InventoryError("The driver signs on the warehouse screen: use 'Driver signs' on the transfer slip.")
    if user.get("role") not in spec["roles"]:
        raise InventoryError("Your role cannot sign the '%s' line." % spec["label"])
    if not user.get("id"):
        raise InventoryError("Log in with your own account to sign.")

    existing = signatures_for(doc_type, doc_id).get(slot)
    if existing:
        if skip_if_signed:
            return None
        raise InventoryError("'%s' is already signed by %s. A signed line cannot be changed."
                             % (spec["label"], existing["signer_name"]))

    account = inv.backend().one(
        "SELECT u.id, u.full_name, u.signature_data, u.is_active, r.code AS role "
        "FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = :i", i=user["id"])
    if account is None or not account["is_active"]:
        raise InventoryError("Your account is not active, so it cannot sign.")
    if account["role"] != user["role"]:
        raise InventoryError("Your role changed. Log out and log in again before signing.")

    inv.backend().execute(
        "INSERT INTO document_signatures (doc_type, doc_id, slot, signer_user_id, signer_name, "
        "signer_role, signature_data, doc_hash, ip_address) "
        "VALUES (:t, :d, :s, :u, :n, :r, :sig, :h, :ip)",
        t=doc_type, d=doc_id, s=slot, u=account["id"], n=account["full_name"], r=account["role"],
        sig=account["signature_data"], h=doc_hash, ip=_client_ip())
    inv.write_audit(account["id"], "sign", "document_signatures", doc_id,
                    new={"doc_type": doc_type, "slot": slot, "signer": account["full_name"],
                         "role": account["role"], "drawn": bool(account["signature_data"]),
                         "doc_hash": doc_hash[:16]}, ip=_client_ip())
    return spec["label"]


# ---------------------------------------------------------------------
# The saved signature image (drawn on the pad, or uploaded)
# ---------------------------------------------------------------------

SIGNATURE_MAX_UPLOAD = 2 * 1024 * 1024      # a phone photo of a signature is well under this
SIGNATURE_MAX_PIXELS = 4000                 # refuse absurd dimensions before decoding
SIGNATURE_OUT_W, SIGNATURE_OUT_H = 600, 200
DATA_PREFIX = "data:image/png;base64,"


def normalize_signature(raw, *, uploaded=False):
    """Check an image really is a PNG/JPEG signature and return a clean data URL.

    * must decode as an image (a fake 'data:image/png' string is rejected)
    * must contain ink (a blank pad is rejected)
    * an uploaded photo/scan on white paper gets a transparent background
    * cropped to the ink and scaled down to at most 600 x 200 so documents stay small
    Raises ValueError with a message that is safe to show.
    """
    import base64
    import io
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(raw))
        if img.format not in ("PNG", "JPEG"):
            raise ValueError("Use a PNG or JPG image.")
        if max(img.size) > SIGNATURE_MAX_PIXELS:
            raise ValueError("The image is too large. Use a smaller picture of your signature.")
        img.load()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ValueError("The signature could not be read. Clear it and draw again.")

    img = ImageOps.exif_transpose(img).convert("RGBA")
    alpha = img.getchannel("A")
    has_transparency = alpha.getextrema()[0] < 250
    if uploaded or not has_transparency:
        # ink on white paper: darkness becomes opacity, white becomes transparent
        gray = img.convert("L")
        cutoff = 235
        mask = gray.point(lambda v: 0 if v >= cutoff else min(255, int((cutoff - v) * 255 / cutoff * 1.6)))
        ink = Image.new("RGBA", img.size, (11, 31, 51, 255))
        ink.putalpha(mask)
        img = ink
    box = img.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
    if box is None:
        raise ValueError("The signature is empty. Draw it again, or upload a clearer picture.")
    pad = 6
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(img.width, box[2] + pad), min(img.height, box[3] + pad))
    img = img.crop(box)
    img.thumbnail((SIGNATURE_OUT_W, SIGNATURE_OUT_H), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return DATA_PREFIX + base64.b64encode(out.getvalue()).decode("ascii")


def decode_data_url(data):
    """The PNG bytes inside a 'data:image/png;base64,...' string sent by the pad."""
    import base64
    if not data or not data.startswith(DATA_PREFIX):
        raise ValueError("Sign in the box before saving.")
    if len(data) > 800_000:
        raise ValueError("The signature is too large. Clear it and sign again with a thinner line.")
    try:
        return base64.b64decode(data[len(DATA_PREFIX):], validate=True)
    except Exception:
        raise ValueError("The signature could not be read. Clear it and sign again.")


def capture_driver(user, ts_id, driver_name, signature_png, doc_hash):
    """Store the driver's signature, captured on a warehouse user's screen. No commit.

    The signature belongs to the driver (signer_user_id stays empty because drivers have no
    account); captured_by_user_id is the warehouse account that held the device.
    """
    spec = slot_def("TS", "driver")
    if user.get("role") not in spec["roles"] or not user.get("id"):
        raise InventoryError("Only warehouse staff or the warehouse manager can capture the driver's signature.")
    if not driver_name:
        raise InventoryError("Assign a driver to this transfer slip first.")
    if signatures_for("TS", ts_id).get("driver"):
        raise InventoryError("The driver has already signed. A signed line cannot be changed.")
    inv.backend().execute(
        "INSERT INTO document_signatures (doc_type, doc_id, slot, signer_user_id, signer_name, signer_role, "
        "signature_data, doc_hash, ip_address, captured_by_user_id) "
        "VALUES ('TS', :d, 'driver', NULL, :n, 'driver', :sig, :h, :ip, :u)",
        d=ts_id, n=driver_name, sig=signature_png, h=doc_hash, ip=_client_ip(), u=user["id"])
    inv.write_audit(user["id"], "sign", "document_signatures", ts_id,
                    new={"doc_type": "TS", "slot": "driver", "signer": driver_name, "role": "driver",
                         "captured_by": user["id"], "doc_hash": doc_hash[:16]}, ip=_client_ip())
