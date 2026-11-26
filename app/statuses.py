"""One place that decides the colour of every status word in the app.

The same word must look the same on a list, a document and a dashboard, so the
templates never pick a colour themselves: they call ``status_tone()`` and the
badge gets one of five tones.

    ok       done, safe, nothing to do
    warn     waiting for a person, or work in progress
    danger   stopped, rejected, or a problem
    info     just recorded, no action needed
    neutral  a draft or something that neither needs nor blocks anything

Colour is never the only signal: the status word itself is always shown.
"""

# Lower-case words as they come out of the database (spaces / dashes normalised).
STATUS_TONES = {
    # ---- finished, good -------------------------------------------------
    "active": "ok",
    "approved": "ok",
    "completed": "ok",
    "closed": "ok",
    "confirmed": "ok",
    "delivered": "ok",
    "in_stock": "ok",
    "ok": "ok",
    "paid": "ok",
    "posted": "ok",
    "received": "ok",
    "released": "ok",
    "resolved": "ok",
    "signed": "ok",
    "available": "ok",

    # ---- waiting for someone / in progress ------------------------------
    "assigned": "warn",
    "below_rop": "warn",
    "follow_up": "warn",
    "for_approval": "warn",
    "in_transit": "warn",
    "loaded": "warn",
    "low": "warn",
    "open": "warn",
    "partially_delivered": "warn",
    "partially_received": "warn",
    "partly_ordered": "warn",
    "pending": "warn",
    "receiving": "warn",
    "requested": "warn",
    "review": "warn",
    "shortage": "warn",
    "snoozed": "warn",
    "void_requested": "warn",
    "waiting": "warn",
    "delivered_with_variance": "warn",

    # ---- stopped / problem ----------------------------------------------
    "blocked": "danger",
    "cancelled": "danger",
    "canceled": "danger",
    "discrepancy": "danger",
    "failed": "danger",
    "out": "danger",
    "out_of_stock": "danger",
    "overdue": "danger",
    "rejected": "danger",
    "voided": "danger",
    "inactive": "neutral",

    # ---- recorded / still moving ----------------------------------------
    "draft": "neutral",
    "new": "info",
    "rfq_sent": "info",
    "sent": "info",
    "ready_to_send": "info",
    "submitted": "info",
    "pending_owner": "warn",
    "info": "info",
    "created": "info",
    "updated": "info",
}

TONE_CLASSES = {
    "ok": "s-ok",
    "warn": "s-warn",
    "danger": "s-danger",
    "info": "s-info",
    "neutral": "s-neutral",
}

# Statuses whose normal capitalisation looks wrong (abbreviations, small words).
STATUS_LABELS = {
    "rfq_sent": "RFQ sent",
    "pending_owner": "Waiting for owner",
    "submitted": "Ready for RFQ",
    "ready_to_send": "Ready to send",
    "ok": "OK",
    "in_transit": "In transit",
    "out_of_stock": "Out of stock",
    "void_requested": "Void requested",
    "delivered_with_variance": "Delivered with variance",
    "partially_received": "Partially received",
    "partially_delivered": "Partially delivered",
    "partly_ordered": "Partly ordered",
    "below_rop": "Below ROP",
}


def normalise(value):
    """'Partially received' / 'partially-received' -> 'partially_received'."""
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def tone(value):
    """The tone name for a status word (neutral when unknown)."""
    return STATUS_TONES.get(normalise(value), "neutral")


def tone_class(value):
    """The CSS class suffix for a status word, e.g. 's-warn'."""
    return TONE_CLASSES[tone(value)]


def label(value):
    """The readable text of a status: 'rfq_sent' -> 'RFQ sent'."""
    key = normalise(value)
    if key in STATUS_LABELS:
        return STATUS_LABELS[key]
    word = str(value or "").strip().replace("_", " ").replace("-", " ")
    return word[:1].upper() + word[1:] if word else ""
