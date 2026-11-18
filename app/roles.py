from functools import wraps
from flask import abort, redirect, request, session, url_for

ROLE_LABELS = {'owner': 'Owner',
 'shop_manager': 'Shop Manager',
 'shop_staff': 'Shop Staff',
 'warehouse_manager': 'Warehouse Manager',
 'warehouse_staff': 'Warehouse Staff'}

# Sidebar menu per role. ("--", "Heading") is a group heading, otherwise (label, endpoint).
MENU = {'owner': [('Dashboard', 'dashboard.index'),
           ('--', 'To do'),
           ('Approvals', 'purchasing.approval_list'),
           ('Documents to sign', 'documents.to_sign'),
           ('--', 'Purchasing'),
           ('Replenishments (REF)', 'purchasing.ref_list'),
           ('Purchase orders (PO)', 'purchasing.po_list'),
           ('--', 'Requisition'),
           ('Requisitions (RF)', 'requisition.rf_list'),
           ('--', 'Inventory'),
           ('Stock', 'inventory.stock_list'),
           ('Stock count', 'inventory.count_sheet'),
           ('ROP settings', 'inventory.rop_list'),
           ('Adjustments', 'inventory.adjustment_list'),
           ('Sales', 'inventory.issue_list'),
           ('--', 'Delivery'),
           ('Transfers (TS)', 'delivery.transfer_list'),
           ('Discrepancies', 'delivery.discrepancy_list'),
           ('--', 'Administration'),
           ('Users', 'admin.user_list'),
           ('Locations', 'admin.location_list'),
           ('Items', 'admin.item_list'),
           ('Brands & patterns', 'admin.classification'),
           ('Suppliers', 'purchasing.supplier_list'),
           ('Reports', 'dashboard.reports')],
 'shop_manager': [('Dashboard', 'dashboard.index'),
                  ('--', 'Requisition'),
                  ('Low-stock alerts', 'requisition.alert_list'),
                  ('Requisitions (RF)', 'requisition.rf_list'),
                  ('--', 'Delivery'),
                  ('Incoming deliveries', 'delivery.delivery_list'),
                  ('Delivery variances', 'delivery.variance_list'),
                  ('--', 'Inventory'),
                  ('Shop stock', 'inventory.stock_list'),
                  ('Stock count', 'inventory.count_sheet'),
                  ('Sales', 'inventory.issue_list'),
                  ('Adjustments', 'inventory.adjustment_list')],
 'shop_staff': [('Dashboard', 'dashboard.index'),
                ('--', 'Inventory'),
                ('Sales', 'inventory.issue_list'),
                ('Shop stock', 'inventory.stock_list'),
                ('Stock count', 'inventory.count_sheet'),
                ('Adjustments', 'inventory.adjustment_list')],
 'warehouse_manager': [('Dashboard', 'dashboard.index'),
                       ('--', 'To do'),
                       ('RF queue', 'inventory.rfqueue_list'),
                       ('Documents to sign', 'documents.to_sign'),
                       ('--', 'Requisition'),
                       ('Requisitions (RF)', 'requisition.rf_list'),
                       ('--', 'Inventory'),
                       ('Stock', 'inventory.stock_list'),
                       ('Stock count', 'inventory.count_sheet'),
                       ('Adjustments', 'inventory.adjustment_list'),
                       ('--', 'Purchasing'),
                       ('Restock alerts', 'purchasing.alert_list'),
                       ('Replenishments (REF)', 'purchasing.ref_list'),
                       ('Purchase orders (PO)', 'purchasing.po_list'),
                       ('--', 'Delivery'),
                       ('Transfers (TS)', 'delivery.transfer_list'),
                       ('Supplier receiving', 'delivery.receiving_list'),
                       ('Discrepancies', 'delivery.discrepancy_list'),
                       ('--', 'Master data'),
                       ('Items', 'admin.item_list'),
                       ('Suppliers', 'purchasing.supplier_list'),
                       ('Drivers', 'delivery.driver_list'),
                       ('Vehicles', 'delivery.vehicle_list')],
 'warehouse_staff': [('Dashboard', 'dashboard.index'),
                     ('--', 'Purchasing'),
                     ('Purchase orders (PO)', 'purchasing.po_list'),
                     ('--', 'Delivery'),
                     ('Supplier receiving', 'delivery.receiving_list'),
                     ('Transfers (TS)', 'delivery.transfer_list'),
                     ('--', 'Inventory'),
                     ('Stock', 'inventory.stock_list'),
                     ('Stock count', 'inventory.count_sheet'),
                     ('Adjustments', 'inventory.adjustment_list')]}

# Screens that belong under another menu item: the sidebar keeps that item highlighted on them.
# "Discrepancies" has two tabs: goods from suppliers (the item itself) and items the shops rejected.
ALSO_ACTIVE = {'delivery.discrepancy_list': ('delivery.variance_list',)}


def roles_required(*allowed):
    """Only the listed roles can open the page."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            role = session.get("role")
            if role is None:
                return redirect(url_for("auth.login", next=request.path))
            if role not in allowed:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator
