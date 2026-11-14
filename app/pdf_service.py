"""Server-side PDF for every printable document (RF, REF, PO, TS, AR, RM, DR).

Built with reportlab from the same data the printable page uses (document_ops),
so the screen, the printout and the PDF can never disagree. The PDF does not
depend on the browser's Print dialog.

Signature block rules
  * signed line      drawn signature (if the account has one), the signer's name
                     and role from the account, date and time, and a short
                     reference code (first 12 characters of the document fingerprint)
  * not signed yet   empty line with "Not signed yet"
  * hand signature   empty line (the driver has no login)
  * content changed  a visible warning if the document was altered after signing
"""
import base64
import binascii
import io
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

INK = colors.HexColor("#111111")
MUTED = colors.HexColor("#666666")
LINE = colors.HexColor("#cfcfcf")
SOFT = colors.HexColor("#f2f2f2")
WARN = colors.HexColor("#9a1b1b")

LOGO_PATH = os.path.join(os.path.dirname(__file__), "static", "img", "logo.png")
PAGE_W, PAGE_H = A4
MARGIN = 16 * mm
CONTENT_W = PAGE_W - 2 * MARGIN


def _t(value):
    """Plain text -> safe for reportlab's built-in fonts (WinAnsi) and Paragraph markup."""
    if value is None:
        return ""
    text = str(value).encode("cp1252", "replace").decode("cp1252")
    return escape(text)


def _styles():
    base = getSampleStyleSheet()["Normal"]
    base.fontName = "Helvetica"
    base.fontSize = 9
    base.leading = 12
    base.textColor = INK
    s = {"base": base}
    s["small"] = ParagraphStyle("small", parent=base, fontSize=7.5, leading=9.5, textColor=MUTED)
    s["label"] = ParagraphStyle("label", parent=base, fontSize=7, leading=9, textColor=MUTED,
                                fontName="Helvetica-Bold")
    s["h2"] = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=10, leading=13,
                             spaceBefore=10, spaceAfter=4)
    s["title"] = ParagraphStyle("title", parent=base, fontName="Helvetica-Bold", fontSize=17, leading=20)
    s["right"] = ParagraphStyle("right", parent=base, alignment=TA_RIGHT)
    s["right_b"] = ParagraphStyle("right_b", parent=base, alignment=TA_RIGHT, fontName="Helvetica-Bold")
    s["cell"] = ParagraphStyle("cell", parent=base, fontSize=8.5, leading=10.5)
    s["name"] = ParagraphStyle("name", parent=base, fontName="Helvetica-Bold", fontSize=10, leading=12)
    s["warn"] = ParagraphStyle("warn", parent=base, fontSize=7.5, leading=9.5, textColor=WARN,
                               fontName="Helvetica-Bold")
    return s


def _signature_image(data_url, max_w, max_h):
    """A saved drawn signature (data URL) as a reportlab Image, or None if it cannot be used.

    The image is fully decoded and re-encoded here. A damaged or half-saved picture (the old
    signature pad only checked the text prefix) must never break the whole PDF: the signer's
    name, role and time are still printed, just without the drawing.
    """
    if not data_url or "," not in data_url:
        return None
    try:
        from PIL import Image as PILImage
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=False)
        pil = PILImage.open(io.BytesIO(raw))
        pil.load()                                   # force a full decode now, not later in the build
        pil = pil.convert("RGBA")
        w, h = pil.size
        if not w or not h:
            return None
        clean = io.BytesIO()
        pil.save(clean, format="PNG")
        clean.seek(0)
        scale = min(max_w / w, max_h / h)
        return Image(clean, width=w * scale, height=h * scale)
    except Exception:                                # any bad image: skip the drawing, keep the PDF
        return None


def _header(doc, company, st):
    logo = Image(LOGO_PATH, width=22 * mm, height=22 * mm) if os.path.exists(LOGO_PATH) else ""
    left = [Paragraph("<b>%s</b>" % _t(company.upper()), ParagraphStyle(
                "brand", parent=st["base"], fontSize=13, leading=16)),
            Paragraph("Tire Supply &amp; Inventory", st["small"])]
    right = [Paragraph(_t(doc["title"]), st["right_b"]),
             Paragraph(_t(doc["doc_no"]), ParagraphStyle("no", parent=st["right_b"], fontSize=12, leading=15)),
             Paragraph("Date: %s" % _t(doc["date"]), st["right"]),
             Paragraph("Status: %s" % _t(doc["status"]), st["right"])]
    t = Table([[logo, left, right]], colWidths=[26 * mm, CONTENT_W - 26 * mm - 62 * mm, 62 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LINEBELOW", (0, 0), (-1, 0), 1.6, INK),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                           ("LEFTPADDING", (0, 0), (0, 0), 0)]))
    return t


def _parties_and_meta(doc, st):
    (fl, fv), (tl, tv) = doc["party_from"], doc["party_to"]

    def box(label, value):
        return [Paragraph(_t(label).upper(), st["label"]), Paragraph("<b>%s</b>" % _t(value), st["base"])]
    parties = Table([[box(fl, fv), box(tl, tv)]], colWidths=[CONTENT_W / 2, CONTENT_W / 2])
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 8),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (0, 0), 0)]))
    out = [parties]
    meta = doc.get("meta") or []
    if meta:
        cells = [[Paragraph(_t(k).upper(), st["label"]), Paragraph(_t(v), st["cell"])] for k, v in meta]
        rows, per_row = [], 2
        for i in range(0, len(cells), per_row):
            chunk = cells[i:i + per_row]
            while len(chunk) < per_row:
                chunk.append(["", ""])
            rows.append([chunk[0][0], chunk[0][1], chunk[1][0], chunk[1][1]])
        t = Table(rows, colWidths=[26 * mm, CONTENT_W / 2 - 26 * mm, 26 * mm, CONTENT_W / 2 - 26 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (0, -1), 0),
                               ("BACKGROUND", (0, 0), (-1, -1), SOFT)]))
        out += [Spacer(1, 4), t]
    return out


def _items_table(doc, st):
    prices = doc["show_prices"]
    head = ["Code", "Item", "Qty"] + (["Unit price", "Amount"] if prices else []) + ["Note"]
    rows = [[Paragraph("<b>%s</b>" % h, st["cell"]) for h in head]]
    for it in doc["lines"]:
        row = [Paragraph(_t(it.get("code")), st["cell"]), Paragraph(_t(it.get("name")), st["cell"]),
               Paragraph(_t(it.get("qty")), st["right"])]
        if prices:
            row += [Paragraph(_t("{:,.2f}".format(float(it.get("price") or 0))), st["right"]),
                    Paragraph(_t("{:,.2f}".format(float(it.get("amount") or 0))), st["right"])]
        row.append(Paragraph(_t(it.get("note") or ""), st["cell"]))
        rows.append(row)
    if not doc["lines"]:
        rows.append([Paragraph("No items.", st["small"])] + [""] * (len(head) - 1))
    if prices and doc.get("total") is not None:
        pad = [""] * 2
        rows.append(pad + [Paragraph("<b>Total</b>", st["right"]), "",
                           Paragraph("<b>%s</b>" % _t("{:,.2f}".format(float(doc["total"] or 0))), st["right"]), ""])

    if prices:
        widths = [24 * mm, CONTENT_W - 24 * mm - 14 * mm - 24 * mm - 26 * mm - 30 * mm, 14 * mm, 24 * mm, 26 * mm, 30 * mm]
    else:
        widths = [26 * mm, CONTENT_W - 26 * mm - 18 * mm - 50 * mm, 18 * mm, 50 * mm]
    t = Table(rows, colWidths=widths, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), SOFT), ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
             ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    if prices and doc.get("total") is not None:
        style.append(("LINEABOVE", (0, -1), (-1, -1), 1.0, INK))
    t.setStyle(TableStyle(style))
    return t


def _signature_block(doc, st):
    """Prepared-by text, then one box per signature line."""
    flow = []
    prepared = doc.get("prepared") or []
    if prepared:
        txt = "   |   ".join("%s: <b>%s</b>" % (_t(a), _t(b)) for a, b in prepared)
        flow.append(Paragraph(txt, st["small"]))
        flow.append(Spacer(1, 6))

    boxes = []
    sig_w = (CONTENT_W - 12 * mm) / 2
    for s in doc["slots"]:
        cell = []
        sig = s["signed"]
        if sig:
            img = _signature_image(sig.get("signature_data"), 52 * mm, 16 * mm)
            cell.append(img if img else Spacer(1, 14 * mm))
            cell.append(Paragraph(_t(sig["signer_name"]), st["name"]))
            cell.append(Paragraph(_t(s["role_title"]), st["small"]))
            cell.append(Paragraph("Signed %s" % _t(str(sig["signed_at"])[:16]), st["small"]))
            if sig.get("captured_by_name"):
                cell.append(Paragraph("Captured by %s" % _t(sig["captured_by_name"]), st["small"]))
            if sig.get("doc_hash"):
                cell.append(Paragraph("Ref %s" % _t(sig["doc_hash"][:12].upper()), st["small"]))
            if s["changed"]:
                cell.append(Paragraph("WARNING: the document was changed after this signature.", st["warn"]))
        else:
            cell.append(Spacer(1, 16 * mm))
            cell.append(Paragraph("Signature over printed name" if s["mode"] in ("wet", "captured") else "Not signed yet",
                                  st["small"]))
        cell.insert(0, Paragraph(_t(s["label"]).upper(), st["label"]))
        boxes.append(cell)

    if not boxes:
        return flow
    rows = []
    for i in range(0, len(boxes), 2):
        pair = boxes[i:i + 2]
        while len(pair) < 2:
            pair.append("")
        rows.append(pair)
    t = Table(rows, colWidths=[sig_w + 6 * mm, sig_w + 6 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEABOVE", (0, 0), (-1, -1), 0.9, INK),
                           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    flow.append(t)
    return flow


def build_pdf(doc, company="3T Tire Supply"):
    """Return the PDF bytes for one document (the dict from document_ops.load)."""
    st = _styles()
    buf = io.BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=14 * mm,
                            bottomMargin=16 * mm, title="%s %s" % (doc["title"], doc["doc_no"]),
                            author=company, subject=doc["title"])
    story = [_header(doc, company, st), Spacer(1, 2)]
    story += _parties_and_meta(doc, st)
    story += [Paragraph("Items", st["h2"]), _items_table(doc, st)]
    if doc.get("notes"):
        story += [Paragraph("Notes", st["h2"])] + [Paragraph(_t(n), st["base"]) for n in doc["notes"]]
    story += [Spacer(1, 8), KeepTogether([Paragraph("Signatures", st["h2"])] + _signature_block(doc, st))]

    changed = any(s["changed"] for s in doc["slots"])
    ref = doc["doc_hash"][:12].upper()

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, 9 * mm, "%s  |  Signed electronically in the %s system  |  Content ref %s"
                          % (doc["doc_no"], company, ref))
        canvas.drawRightString(PAGE_W - MARGIN, 9 * mm, "Page %d" % canvas.getPageNumber())
        if changed:
            canvas.setFillColor(WARN)
            canvas.setFont("Helvetica-Bold", 8)
            canvas.drawString(MARGIN, 13 * mm, "WARNING: this document was changed after it was signed.")
        canvas.restoreState()

    pdf.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
