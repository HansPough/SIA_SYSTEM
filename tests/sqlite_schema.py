"""Small SQLite copy of the tables the Inventory screens use (for tests only)."""
SCHEMA = """
CREATE TABLE roles (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT);
CREATE TABLE locations (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT, loc_type TEXT, address TEXT, is_active INTEGER DEFAULT 1);
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, full_name TEXT, email TEXT, password_hash TEXT,
  signature_data TEXT, role_id INTEGER, location_id INTEGER, is_active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, item_code TEXT UNIQUE, item_name TEXT UNIQUE, brand TEXT, size TEXT,
  ply_rating TEXT, pattern TEXT, model TEXT, category TEXT DEFAULT 'tire', unit TEXT DEFAULT 'pc', is_active INTEGER DEFAULT 1);
CREATE TABLE item_location_settings (item_id INTEGER, location_id INTEGER, rop INTEGER DEFAULT 0, max_level INTEGER DEFAULT 0,
  lead_time_days INTEGER DEFAULT 0, rack TEXT, updated_by INTEGER, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (item_id, location_id), CHECK (max_level >= rop));
CREATE TABLE stock_balances (item_id INTEGER, location_id INTEGER, on_hand INTEGER NOT NULL DEFAULT 0 CHECK (on_hand >= 0),
  reserved INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0), updated_at TEXT, PRIMARY KEY (item_id, location_id), CHECK (reserved <= on_hand));
CREATE TABLE stock_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER, location_id INTEGER, movement_type TEXT,
  qty_change INTEGER CHECK (qty_change <> 0), balance_after INTEGER CHECK (balance_after >= 0), ref_type TEXT, ref_id INTEGER,
  ref_no TEXT, remarks TEXT, created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE stock_issues (id INTEGER PRIMARY KEY AUTOINCREMENT, issue_no TEXT UNIQUE, location_id INTEGER, customer_name TEXT,
  issue_date TEXT, document_ref TEXT, reason TEXT CHECK (reason IN ('sale','other')), remarks TEXT, recorded_by INTEGER,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  status TEXT NOT NULL DEFAULT 'posted' CHECK (status IN ('posted','void_requested','voided')),
  void_reason TEXT, void_requested_by INTEGER, void_requested_at TEXT, voided_by INTEGER, voided_at TEXT,
  CHECK (status = 'posted' OR (void_reason IS NOT NULL AND void_requested_by IS NOT NULL)),
  CHECK (status <> 'voided' OR voided_by IS NOT NULL),
  CHECK (voided_by IS NULL OR voided_by <> void_requested_by));
CREATE TABLE stock_issue_items (id INTEGER PRIMARY KEY AUTOINCREMENT, issue_id INTEGER, item_id INTEGER, qty INTEGER CHECK (qty > 0));
CREATE TABLE reservations (id INTEGER PRIMARY KEY AUTOINCREMENT, rf_item_id INTEGER, item_id INTEGER, location_id INTEGER,
  qty INTEGER CHECK (qty > 0), status TEXT, reserved_by INTEGER, reserved_at TEXT, released_at TEXT);
CREATE TABLE stock_adjustments (id INTEGER PRIMARY KEY AUTOINCREMENT, adj_no TEXT UNIQUE, location_id INTEGER, reason TEXT,
  status TEXT DEFAULT 'pending', auto_approved INTEGER DEFAULT 0, source_ar_id INTEGER, remarks TEXT,
  requested_by INTEGER NOT NULL, reviewed_by INTEGER,
  review_remarks TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, reviewed_at TEXT,
  CHECK (status = 'pending' OR auto_approved = 1 OR reviewed_by IS NOT NULL),
  CHECK (reviewed_by IS NULL OR auto_approved = 1 OR reviewed_by <> requested_by));
CREATE TABLE stock_adjustment_items (id INTEGER PRIMARY KEY AUTOINCREMENT, adjustment_id INTEGER, item_id INTEGER,
  qty_system INTEGER, qty_actual INTEGER, item_remarks TEXT, UNIQUE (adjustment_id, item_id));
CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, notif_type TEXT CHECK (notif_type IN ('low_stock','restock')),
  location_id INTEGER, status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','dismissed')), dismissed_reason TEXT,
  snoozed_until TEXT, snoozed_by INTEGER, closed_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, closed_at TEXT, CHECK (status <> 'dismissed' OR dismissed_reason IS NOT NULL));
CREATE TABLE notification_items (id INTEGER PRIMARY KEY AUTOINCREMENT, notification_id INTEGER, item_id INTEGER, current_qty INTEGER,
  rop INTEGER, suggested_qty INTEGER CHECK (suggested_qty > 0), reason TEXT NOT NULL DEFAULT 'below_rop' CHECK (reason IN ('shortage','below_rop')),
  UNIQUE (notification_id, item_id));
CREATE TABLE requisitions (id INTEGER PRIMARY KEY AUTOINCREMENT, rf_no TEXT UNIQUE, shop_id INTEGER, warehouse_id INTEGER, notification_id INTEGER,
  status TEXT DEFAULT 'draft' CHECK (status IN ('draft','open','partially_delivered','closed','cancelled')), requested_by INTEGER, remarks TEXT,
  cancel_reason TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, submitted_at TEXT, closed_at TEXT, CHECK (shop_id <> warehouse_id));
CREATE TABLE requisition_items (id INTEGER PRIMARY KEY AUTOINCREMENT, rf_id INTEGER, item_id INTEGER, qty_requested INTEGER CHECK (qty_requested > 0),
  qty_reserved INTEGER NOT NULL DEFAULT 0 CHECK (qty_reserved >= 0), qty_delivered INTEGER NOT NULL DEFAULT 0 CHECK (qty_delivered >= 0),
  UNIQUE (rf_id, item_id), CHECK (qty_delivered <= qty_requested));
CREATE TABLE transfer_slips (id INTEGER PRIMARY KEY AUTOINCREMENT, ts_no TEXT UNIQUE, rf_id INTEGER, from_location_id INTEGER,
  to_location_id INTEGER, driver_id INTEGER, vehicle_id INTEGER, status TEXT DEFAULT 'draft', prepared_by INTEGER,
  loaded_by INTEGER, released_by INTEGER, released_at TEXT, delivered_at TEXT, remarks TEXT, driver_paper_reason TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE transfer_slip_items (id INTEGER PRIMARY KEY AUTOINCREMENT, ts_id INTEGER, rf_item_id INTEGER, item_id INTEGER,
  qty_released INTEGER CHECK (qty_released > 0), loaded INTEGER NOT NULL DEFAULT 0, UNIQUE (ts_id, rf_item_id));
CREATE TABLE drivers (id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT, contact_no TEXT, license_no TEXT, is_active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE vehicles (id INTEGER PRIMARY KEY AUTOINCREMENT, plate_no TEXT UNIQUE, vehicle_type TEXT, is_active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE acknowledgment_receipts (id INTEGER PRIMARY KEY AUTOINCREMENT, ar_no TEXT UNIQUE, ts_id INTEGER UNIQUE, received_by INTEGER,
  received_at TEXT DEFAULT CURRENT_TIMESTAMP, has_variance INTEGER NOT NULL DEFAULT 0, remarks TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE acknowledgment_receipt_items (id INTEGER PRIMARY KEY AUTOINCREMENT, ar_id INTEGER, ts_item_id INTEGER, item_id INTEGER,
  qty_expected INTEGER, qty_received INTEGER, qty_variance INTEGER GENERATED ALWAYS AS (qty_received - qty_expected) STORED,
  variance_reason TEXT, variance_resolved_at TEXT, UNIQUE (ar_id, ts_item_id));
CREATE TABLE goods_receipts (id INTEGER PRIMARY KEY AUTOINCREMENT, rm_no TEXT UNIQUE, po_id INTEGER, supplier_dr_no TEXT,
  supplier_invoice_no TEXT, delivered_by TEXT,
  received_date TEXT, received_by INTEGER, remarks TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE goods_receipt_items (id INTEGER PRIMARY KEY AUTOINCREMENT, rm_id INTEGER, po_item_id INTEGER, item_id INTEGER,
  qty_delivered INTEGER, qty_accepted INTEGER, qty_rejected INTEGER NOT NULL DEFAULT 0, reject_reason TEXT);
CREATE TABLE discrepancy_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, report_no TEXT UNIQUE, rm_id INTEGER, po_id INTEGER,
  status TEXT NOT NULL DEFAULT 'open', follow_up_notes TEXT, created_by INTEGER,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, resolved_at TEXT);
CREATE TABLE discrepancy_items (id INTEGER PRIMARY KEY AUTOINCREMENT, report_id INTEGER, po_item_id INTEGER, item_id INTEGER,
  qty_short INTEGER NOT NULL DEFAULT 0, qty_rejected INTEGER NOT NULL DEFAULT 0, qty_replaced INTEGER NOT NULL DEFAULT 0,
  reason TEXT);
CREATE TABLE suppliers (id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_name TEXT UNIQUE, contact_person TEXT, email TEXT,
  phone TEXT, address TEXT, is_regular INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE supplier_items (id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_id INTEGER, item_id INTEGER, agreed_price REAL,
  valid_from TEXT, valid_to TEXT, UNIQUE (supplier_id, item_id, valid_from));
CREATE TABLE replenishments (id INTEGER PRIMARY KEY AUTOINCREMENT, ref_no TEXT UNIQUE, warehouse_id INTEGER, notification_id INTEGER,
  status TEXT DEFAULT 'draft', created_by INTEGER, remarks TEXT, cancel_reason TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, submitted_at TEXT, closed_at TEXT);
CREATE TABLE replenishment_items (id INTEGER PRIMARY KEY AUTOINCREMENT, ref_id INTEGER, item_id INTEGER, qty_requested INTEGER,
  qty_received INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT 'below_rop', rf_id INTEGER,
  qty_dropped INTEGER NOT NULL DEFAULT 0, dropped_reason TEXT, UNIQUE (ref_id, item_id));
CREATE TABLE rfqs (id INTEGER PRIMARY KEY AUTOINCREMENT, rfq_no TEXT UNIQUE, ref_id INTEGER, reply_deadline TEXT, terms TEXT,
  status TEXT DEFAULT 'draft', created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, sent_at TEXT);
CREATE TABLE rfq_suppliers (rfq_id INTEGER, supplier_id INTEGER, sent_at TEXT, replied_at TEXT, PRIMARY KEY (rfq_id, supplier_id));
CREATE TABLE quotations (id INTEGER PRIMARY KEY AUTOINCREMENT, rfq_id INTEGER, supplier_id INTEGER, quotation_ref TEXT,
  quotation_date TEXT, valid_until TEXT, total_amount REAL, notes TEXT, recorded_by INTEGER,
  recorded_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE (rfq_id, supplier_id));
CREATE TABLE quotation_items (id INTEGER PRIMARY KEY AUTOINCREMENT, quotation_id INTEGER, replenishment_item_id INTEGER,
  item_id INTEGER, qty_offered INTEGER, unit_price REAL, lead_time_days INTEGER,
  UNIQUE (quotation_id, replenishment_item_id));
CREATE TABLE purchase_approvals (id INTEGER PRIMARY KEY AUTOINCREMENT, ref_id INTEGER, supplier_id INTEGER, basis TEXT,
  quotation_id INTEGER, decision TEXT DEFAULT 'pending', submitted_by INTEGER, submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
  decided_by INTEGER, decided_at TEXT, remarks TEXT, batch_id INTEGER);
CREATE TABLE purchase_approval_items (id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id INTEGER,
  replenishment_item_id INTEGER, item_id INTEGER, qty INTEGER, unit_price NUMERIC, UNIQUE (approval_id, replenishment_item_id));
CREATE TABLE purchase_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, po_no TEXT UNIQUE, ref_id INTEGER, supplier_id INTEGER,
  approval_id INTEGER UNIQUE, po_date TEXT, expected_date TEXT, terms TEXT, status TEXT DEFAULT 'draft',
  total_amount REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, sent_at TEXT);
CREATE TABLE purchase_order_items (id INTEGER PRIMARY KEY AUTOINCREMENT, po_id INTEGER, replenishment_item_id INTEGER,
  item_id INTEGER, qty_ordered INTEGER, unit_price REAL, qty_received INTEGER NOT NULL DEFAULT 0,
  UNIQUE (po_id, replenishment_item_id));
CREATE TABLE document_sequences (doc_type TEXT, doc_year INTEGER, last_no INTEGER DEFAULT 0, PRIMARY KEY (doc_type, doc_year));
CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT, table_name TEXT, record_id INTEGER,
  old_values TEXT, new_values TEXT, ip_address TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE document_signatures (id INTEGER PRIMARY KEY AUTOINCREMENT, doc_type TEXT, doc_id INTEGER, slot TEXT,
  signer_user_id INTEGER, signer_name TEXT, signature_data TEXT, remarks TEXT, signed_at TEXT DEFAULT CURRENT_TIMESTAMP,
  signer_role TEXT, doc_hash TEXT, ip_address TEXT, captured_by_user_id INTEGER,
  UNIQUE (doc_type, doc_id, slot));
CREATE TABLE supplier_returns (id INTEGER PRIMARY KEY AUTOINCREMENT, return_no TEXT UNIQUE, po_id INTEGER, supplier_id INTEGER,
  rm_id INTEGER, return_date TEXT, reason TEXT, credit_amount REAL DEFAULT 0, status TEXT DEFAULT 'draft',
  created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, confirmed_by INTEGER, confirmed_at TEXT);
CREATE TABLE supplier_return_items (id INTEGER PRIMARY KEY AUTOINCREMENT, return_id INTEGER, po_item_id INTEGER, item_id INTEGER,
  qty_returned INTEGER, unit_price REAL DEFAULT 0, UNIQUE (return_id, po_item_id));
CREATE TABLE schema_migrations (filename TEXT PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""


class SqliteBackend:
    def __init__(self):
        import sqlite3
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def execute(self, sql, **p):
        self.db.execute(sql, p)

    def one(self, sql, **p):
        r = self.db.execute(sql, p).fetchone()
        return dict(r) if r else None

    def all(self, sql, **p):
        return [dict(r) for r in self.db.execute(sql, p).fetchall()]

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()


_invoice_counter = [0]


def next_invoice():
    """A fresh e-invoice number: every sale now needs its own."""
    _invoice_counter[0] += 1
    return "EI-%06d" % _invoice_counter[0]
