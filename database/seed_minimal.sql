-- Minimal sample data for testing the screens (not the full 416-item list).
-- Load it into an EMPTY database after database/schema.sql:
--
--   createdb inventory_sample
--   psql -U postgres -d inventory_sample -v ON_ERROR_STOP=1 -f database/schema.sql
--   psql -U postgres -d inventory_sample -v ON_ERROR_STOP=1 -f database/seed_minimal.sql
--
-- Passwords for all demo users: changeme
-- This file is safe to run again on the same sample database (it upserts).

BEGIN;

-- Locations -----------------------------------------------------------------
INSERT INTO locations (code, name, loc_type, address, is_active)
VALUES ('WH', 'Main Warehouse', 'warehouse', '3T Compound, Batangas', TRUE),
       ('SHOP', 'Main Shop', 'shop', '3T Store, Batangas', TRUE)
ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, loc_type = EXCLUDED.loc_type,
    address = EXCLUDED.address, is_active = TRUE;

-- Users (same demo password hash as database/seed_users.sql: changeme) -------
INSERT INTO users (username, full_name, email, password_hash, role_id, location_id, is_active)
VALUES
 ('owner', 'Demo Owner', 'owner@3ttier.example',
  'scrypt:32768:8:1$hp9pOIsWBBL3VasA$38b61ef1145982d71386b5113b556c879d688231fb62f04067e2be1cc63dcef1946fdd40c2f82a584d79c343d538f1d31a51c008e64fda457be6731ddc1c9f0e',
  (SELECT id FROM roles WHERE code='owner'), NULL, TRUE),
 ('whmgr', 'Demo Warehouse Manager', 'whmgr@3ttier.example',
  'scrypt:32768:8:1$hp9pOIsWBBL3VasA$38b61ef1145982d71386b5113b556c879d688231fb62f04067e2be1cc63dcef1946fdd40c2f82a584d79c343d538f1d31a51c008e64fda457be6731ddc1c9f0e',
  (SELECT id FROM roles WHERE code='warehouse_manager'), (SELECT id FROM locations WHERE code='WH'), TRUE),
 ('whstaff', 'Demo Warehouse Staff', 'whstaff@3ttier.example',
  'scrypt:32768:8:1$hp9pOIsWBBL3VasA$38b61ef1145982d71386b5113b556c879d688231fb62f04067e2be1cc63dcef1946fdd40c2f82a584d79c343d538f1d31a51c008e64fda457be6731ddc1c9f0e',
  (SELECT id FROM roles WHERE code='warehouse_staff'), (SELECT id FROM locations WHERE code='WH'), TRUE),
 ('shopmgr', 'Demo Shop Manager', 'shopmgr@3ttier.example',
  'scrypt:32768:8:1$hp9pOIsWBBL3VasA$38b61ef1145982d71386b5113b556c879d688231fb62f04067e2be1cc63dcef1946fdd40c2f82a584d79c343d538f1d31a51c008e64fda457be6731ddc1c9f0e',
  (SELECT id FROM roles WHERE code='shop_manager'), (SELECT id FROM locations WHERE code='SHOP'), TRUE),
 ('shopstaff', 'Demo Shop Staff', 'shopstaff@3ttier.example',
  'scrypt:32768:8:1$hp9pOIsWBBL3VasA$38b61ef1145982d71386b5113b556c879d688231fb62f04067e2be1cc63dcef1946fdd40c2f82a584d79c343d538f1d31a51c008e64fda457be6731ddc1c9f0e',
  (SELECT id FROM roles WHERE code='shop_staff'), (SELECT id FROM locations WHERE code='SHOP'), TRUE)
ON CONFLICT (username) DO NOTHING;

-- 16 items (10 tires, 3 tubes, 3 flaps) --------------------------------------
INSERT INTO items (item_code, item_name, brand, model, size, ply_rating, pattern, category, unit, is_active)
VALUES
 ('TIR-0001', 'ANSU 9.5R17.5 18PR BY616',          'ANSU',  'BY616', '9.5R17.5',  '18PR', 'Rib',        'tire', 'pc', TRUE),
 ('TIR-0002', 'ANSU 9.5R/7.5 18PR BY996 LUG',      'ANSU',  'BY996', '9.5R17.5',  '18PR', 'Lug',        'tire', 'pc', TRUE),
 ('TIR-0003', 'ANSU 12R22.5 18PR AL505 MIX',       'ANSU',  'AL505', '12R22.5',   '18PR', 'Mixed',      'tire', 'pc', TRUE),
 ('TIR-0004', 'ANSU 215/75R17.5 18PR BY616 RIB',   'ANSU',  'BY616', '215/75R17.5','18PR', 'Rib',       'tire', 'pc', TRUE),
 ('TIR-0005', 'ANSU 215/75R17.5 18PR BY996 LUG',   'ANSU',  'BY996', '215/75R17.5','18PR', 'Lug',       'tire', 'pc', TRUE),
 ('TIR-0006', 'ANSU 235/75R17.5 18PR BY616',       'ANSU',  'BY616', '235/75R17.5','18PR', 'All-Season','tire', 'pc', TRUE),
 ('TIR-0007', 'ANSU 235/75R17.5 18PR BY996 LUG',   'ANSU',  'BY996', '235/75R17.5','18PR', 'Lug',       'tire', 'pc', TRUE),
 ('TIR-0008', 'ANSU 265/70R19.5 18PR BY616',       'ANSU',  'BY616', '265/70R19.5','18PR', 'Mixed',     'tire', 'pc', TRUE),
 ('TIR-0009', 'ANSU 275/70R22.5 18PR BY518 RIB',   'ANSU',  'BY518', '275/70R22.5','18PR', 'Rib',       'tire', 'pc', TRUE),
 ('TIR-0010', 'ANSU 315/80R22.5 20PR BYD68 AT',    'ANSU',  'BYD68', '315/80R22.5','20PR', 'All-Terrain','tire','pc', TRUE),
 ('TUB-0001', 'BAYI TUBES 10.00R20',               'BAYI',  NULL,    '10.00R20',  NULL,   'N/A',       'tube', 'pc', TRUE),
 ('TUB-0002', 'CEAT TUBES 7.00-15',                'CEAT',  NULL,    '7.00-15',   NULL,   'N/A',       'tube', 'pc', TRUE),
 ('TUB-0003', 'CEAT TUBES 12.4-24',                'CEAT',  NULL,    '12.4-24',   NULL,   'N/A',       'tube', 'pc', TRUE),
 ('FLP-0001', 'AAA FLAPS 700-12',                  'AAA',   NULL,    '700-12',    NULL,   'N/A',       'flap', 'pc', TRUE),
 ('FLP-0002', 'ARMOUR FLAPS 17.5-25',              'ARMOUR',NULL,    '17.5-25',   NULL,   'N/A',       'flap', 'pc', TRUE),
 ('FLP-0003', 'ARMOUR FLAPS 23.5-25',              'ARMOUR',NULL,    '23.5-25',   NULL,   'N/A',       'flap', 'pc', TRUE)
ON CONFLICT (item_code) DO UPDATE SET item_name = EXCLUDED.item_name, brand = EXCLUDED.brand,
    model = EXCLUDED.model, size = EXCLUDED.size, ply_rating = EXCLUDED.ply_rating,
    pattern = EXCLUDED.pattern, category = EXCLUDED.category, unit = EXCLUDED.unit, is_active = TRUE;

-- Warehouse stock: 40 for every item ----------------------------------------
INSERT INTO stock_balances (item_id, location_id, on_hand, reserved)
SELECT i.id, l.id, 40, 0
FROM items i CROSS JOIN locations l
WHERE l.code = 'WH' AND i.item_code IN (
 'TIR-0001','TIR-0002','TIR-0003','TIR-0004','TIR-0005','TIR-0006','TIR-0007',
 'TIR-0008','TIR-0009','TIR-0010','TUB-0001','TUB-0002','TUB-0003',
 'FLP-0001','FLP-0002','FLP-0003')
ON CONFLICT (item_id, location_id) DO UPDATE SET on_hand = EXCLUDED.on_hand, reserved = 0;

-- Shop stock: a smaller portion ---------------------------------------------
INSERT INTO stock_balances (item_id, location_id, on_hand, reserved)
SELECT i.id, l.id, 8, 0
FROM items i CROSS JOIN locations l
WHERE l.code = 'SHOP' AND i.item_code IN (
 'TIR-0001','TIR-0003','TIR-0005','TIR-0007','TUB-0001','FLP-0001')
ON CONFLICT (item_id, location_id) DO UPDATE SET on_hand = EXCLUDED.on_hand, reserved = 0;

-- Opening ledger rows (only once) -------------------------------------------
INSERT INTO stock_ledger (item_id, location_id, movement_type, qty_change, balance_after, remarks)
SELECT i.id, l.id, 'opening', b.on_hand, b.on_hand, 'Minimal sample opening'
FROM stock_balances b
JOIN items i ON i.id = b.item_id
JOIN locations l ON l.id = b.location_id
WHERE l.code IN ('WH', 'SHOP')
  AND NOT EXISTS (SELECT 1 FROM stock_ledger sl WHERE sl.item_id = b.item_id
                  AND sl.location_id = b.location_id AND sl.remarks = 'Minimal sample opening');

-- ROP / max / lead time at the warehouse ------------------------------------
INSERT INTO item_location_settings (item_id, location_id, rop, max_level, lead_time_days, rack)
SELECT i.id, l.id, 10, 40, 7, 'Rack A'
FROM items i CROSS JOIN locations l
WHERE l.code = 'WH'
ON CONFLICT (item_id, location_id) DO UPDATE SET rop = EXCLUDED.rop, max_level = EXCLUDED.max_level,
    lead_time_days = EXCLUDED.lead_time_days, rack = EXCLUDED.rack;

-- Suppliers -----------------------------------------------------------------
INSERT INTO suppliers (supplier_name, contact_person, email, phone, address, is_regular, is_active)
VALUES
 ('Northern Tire Trading', 'Ana Cruz', 'ana@northerntire.example', '0917 555 0101',
  'Batangas City', TRUE, TRUE),
 ('Metro Rubber Supply', 'Ben Santos', 'ben@metrorubber.example', '0918 555 0102',
  'Quezon City', TRUE, TRUE),
 ('Highland Wheels Corp', 'Carla Reyes', 'carla@highlandwheels.example', '0919 555 0103',
  'Baguio City', FALSE, TRUE)
ON CONFLICT (supplier_name) DO UPDATE SET contact_person = EXCLUDED.contact_person,
    email = EXCLUDED.email, phone = EXCLUDED.phone, address = EXCLUDED.address,
    is_regular = EXCLUDED.is_regular, is_active = TRUE;

-- Agreed prices for the two regular suppliers (16 items each) --------------
INSERT INTO supplier_items (supplier_id, item_id, agreed_price, valid_from, valid_to)
SELECT s.id, i.id, 1200 + (i.id * 173) % 5500, DATE '2026-01-01', NULL
FROM suppliers s JOIN items i ON TRUE
WHERE s.supplier_name = 'Northern Tire Trading'
  AND i.item_code IN ('TIR-0001','TIR-0002','TIR-0003','TIR-0004','TIR-0005','TIR-0006',
                      'TIR-0007','TIR-0008','TIR-0009','TIR-0010','TUB-0001','TUB-0002',
                      'TUB-0003','FLP-0001','FLP-0002','FLP-0003')
ON CONFLICT (supplier_id, item_id, valid_from) DO UPDATE SET agreed_price = EXCLUDED.agreed_price,
    valid_to = NULL;

INSERT INTO supplier_items (supplier_id, item_id, agreed_price, valid_from, valid_to)
SELECT s.id, i.id, (1200 + (i.id * 173) % 5500) * 1.07, DATE '2026-01-01', NULL
FROM suppliers s JOIN items i ON TRUE
WHERE s.supplier_name = 'Metro Rubber Supply'
  AND i.item_code IN ('TIR-0001','TIR-0002','TIR-0003','TIR-0004','TIR-0005','TIR-0006',
                      'TIR-0007','TIR-0008','TIR-0009','TIR-0010','TUB-0001','TUB-0002',
                      'TUB-0003','FLP-0001','FLP-0002','FLP-0003')
ON CONFLICT (supplier_id, item_id, valid_from) DO UPDATE SET agreed_price = EXCLUDED.agreed_price,
    valid_to = NULL;

COMMIT;
