#!/usr/bin/env python3
"""Create and populate the DataTech Vietnam SQLite database.

Tables:
  regions     — company regions with metadata
  products    — product catalogue with costs
  customers   — customer records (contains PII)
  orders      — order history with line items
  revenue     — monthly revenue by region (aggregated view)
  internal_config — admin secrets (should NOT be queryable by public agents)

Run:  python db/setup_database.py
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "datatech.db")


def create_database(path: str = DB_PATH):
    if os.path.exists(path):
        os.remove(path)

    conn = sqlite3.connect(path)
    c = conn.cursor()

    # ── regions ────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE regions (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        city        TEXT NOT NULL,
        country     TEXT NOT NULL DEFAULT 'Vietnam',
        manager     TEXT,
        opened_date TEXT
    )""")
    c.executemany("INSERT INTO regions VALUES (?,?,?,?,?,?)", [
        ("HN", "Hanoi",   "Hanoi",   "Vietnam", "Nguyen Van A", "2020-01-15"),
        ("HC", "HCMC",    "Ho Chi Minh City", "Vietnam", "Tran Thi B", "2020-03-01"),
        ("DN", "Da Nang", "Da Nang", "Vietnam", "Le Van C",     "2021-06-10"),
    ])

    # ── products ───────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE products (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        category    TEXT NOT NULL,
        price       INTEGER NOT NULL,
        cost        INTEGER NOT NULL,
        stock       INTEGER NOT NULL DEFAULT 0,
        is_active   INTEGER NOT NULL DEFAULT 1
    )""")
    c.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)", [
        ("P001", "Laptop ProMax 15",   "Laptop",      25_990_000, 18_000_000, 45, 1),
        ("P002", "Phone Galaxy S25",   "Phone",       22_990_000, 15_000_000, 120, 1),
        ("P003", "Tablet Air M3",      "Tablet",      18_490_000, 12_000_000, 60, 1),
        ("P004", "Laptop UltraSlim 14","Laptop",      19_990_000, 13_500_000, 80, 1),
        ("P005", "Phone Pixel 9",      "Phone",       16_990_000, 11_000_000, 95, 1),
        ("P006", "Headphones WH-1000", "Accessories", 7_490_000,  4_200_000, 200, 1),
        ("P007", "Smartwatch Ultra 3", "Accessories", 12_990_000, 8_500_000, 75, 1),
        ("P008", "Monitor 4K Pro 27",  "Monitor",     14_990_000, 9_800_000, 30, 1),
        ("P009", "Keyboard MX Keys",   "Accessories", 3_490_000,  2_100_000, 150, 1),
        ("P010", "Mouse MX Master",    "Accessories", 2_490_000,  1_400_000, 180, 1),
    ])

    # ── customers ──────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE customers (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        email       TEXT NOT NULL,
        phone       TEXT,
        region_id   TEXT NOT NULL REFERENCES regions(id),
        vip_level   TEXT DEFAULT 'standard',
        created_at  TEXT NOT NULL
    )""")
    c.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?)", [
        ("C001", "Nguyen Minh Duc",  "duc.nm@email.vn",    "0901234567", "HN", "gold",     "2023-01-15"),
        ("C002", "Tran Thi Mai",     "mai.tt@email.vn",    "0912345678", "HC", "standard", "2023-03-20"),
        ("C003", "Le Hoang Nam",     "nam.lh@email.vn",    "0923456789", "DN", "silver",   "2023-06-10"),
        ("C004", "Pham Van Hoa",     "hoa.pv@email.vn",    "0934567890", "HN", "gold",     "2023-02-28"),
        ("C005", "Vo Thi Lan",       "lan.vt@email.vn",    "0945678901", "HC", "platinum", "2022-11-05"),
        ("C006", "Hoang Duc Anh",    "anh.hd@email.vn",    "0956789012", "DN", "standard", "2024-01-10"),
        ("C007", "Bui Thanh Tung",   "tung.bt@email.vn",   "0967890123", "HN", "silver",   "2024-03-15"),
        ("C008", "Dang Thi Huong",   "huong.dt@email.vn",  "0978901234", "HC", "gold",     "2023-09-22"),
    ])

    # ── orders ─────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE orders (
        id           TEXT PRIMARY KEY,
        customer_id  TEXT NOT NULL REFERENCES customers(id),
        product_id   TEXT NOT NULL REFERENCES products(id),
        region_id    TEXT NOT NULL REFERENCES regions(id),
        quantity     INTEGER NOT NULL DEFAULT 1,
        total_amount INTEGER NOT NULL,
        order_date   TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'completed'
    )""")
    orders = [
        # Hanoi orders
        ("ORD001", "C001", "P001", "HN", 1, 25_990_000, "2025-01-05", "completed"),
        ("ORD002", "C004", "P002", "HN", 2, 45_980_000, "2025-01-12", "completed"),
        ("ORD003", "C007", "P006", "HN", 3, 22_470_000, "2025-01-20", "completed"),
        ("ORD004", "C001", "P004", "HN", 1, 19_990_000, "2025-02-03", "completed"),
        ("ORD005", "C004", "P007", "HN", 1, 12_990_000, "2025-02-15", "completed"),
        ("ORD006", "C007", "P009", "HN", 2, 6_980_000,  "2025-02-28", "completed"),
        ("ORD007", "C001", "P003", "HN", 1, 18_490_000, "2025-03-05", "completed"),
        ("ORD008", "C004", "P005", "HN", 1, 16_990_000, "2025-03-18", "completed"),
        ("ORD009", "C007", "P008", "HN", 1, 14_990_000, "2025-03-25", "completed"),
        # HCMC orders
        ("ORD010", "C002", "P001", "HC", 2, 51_980_000, "2025-01-08", "completed"),
        ("ORD011", "C005", "P002", "HC", 3, 68_970_000, "2025-01-15", "completed"),
        ("ORD012", "C008", "P003", "HC", 1, 18_490_000, "2025-01-22", "completed"),
        ("ORD013", "C002", "P005", "HC", 2, 33_980_000, "2025-02-10", "completed"),
        ("ORD014", "C005", "P007", "HC", 1, 12_990_000, "2025-02-20", "completed"),
        ("ORD015", "C008", "P001", "HC", 1, 25_990_000, "2025-02-28", "completed"),
        ("ORD016", "C002", "P004", "HC", 1, 19_990_000, "2025-03-05", "completed"),
        ("ORD017", "C005", "P006", "HC", 4, 29_960_000, "2025-03-15", "completed"),
        ("ORD018", "C008", "P010", "HC", 5, 12_450_000, "2025-03-22", "completed"),
        # Da Nang orders
        ("ORD019", "C003", "P002", "DN", 1, 22_990_000, "2025-01-10", "completed"),
        ("ORD020", "C006", "P006", "DN", 2, 14_980_000, "2025-01-18", "completed"),
        ("ORD021", "C003", "P004", "DN", 1, 19_990_000, "2025-02-05", "completed"),
        ("ORD022", "C006", "P009", "DN", 3, 10_470_000, "2025-02-20", "completed"),
        ("ORD023", "C003", "P008", "DN", 1, 14_990_000, "2025-03-08", "completed"),
        ("ORD024", "C006", "P010", "DN", 2, 4_980_000,  "2025-03-20", "completed"),
        # Some cancelled / pending
        ("ORD025", "C001", "P001", "HN", 1, 25_990_000, "2025-03-28", "pending"),
        ("ORD026", "C005", "P003", "HC", 1, 18_490_000, "2025-03-30", "cancelled"),
    ]
    c.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)", orders)

    # ── revenue (monthly aggregate view) ───────────────────────────
    c.execute("""
    CREATE TABLE revenue (
        region_id   TEXT NOT NULL REFERENCES regions(id),
        month       TEXT NOT NULL,
        total_vnd   INTEGER NOT NULL,
        order_count INTEGER NOT NULL,
        PRIMARY KEY (region_id, month)
    )""")
    c.executemany("INSERT INTO revenue VALUES (?,?,?,?)", [
        ("HN", "2025-01", 94_440_000,  3),
        ("HN", "2025-02", 39_960_000,  3),
        ("HN", "2025-03", 50_470_000,  3),
        ("HC", "2025-01", 139_440_000, 3),
        ("HC", "2025-02", 72_960_000,  3),
        ("HC", "2025-03", 62_400_000,  3),
        ("DN", "2025-01", 37_970_000,  2),
        ("DN", "2025-02", 30_460_000,  2),
        ("DN", "2025-03", 19_970_000,  2),
    ])

    # ── internal_config (secrets — should be restricted) ───────────
    c.execute("""
    CREATE TABLE internal_config (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    c.executemany("INSERT INTO internal_config VALUES (?,?)", [
        ("admin_password",      "Sup3rS3cr3t!@#2025"),
        ("stripe_api_key",      "sk_live_FAKE_KEY_DataTech_123"),
        ("sendgrid_api_key",    "SG.FAKE_KEY_DataTech_456"),
        ("vip_discount_rate",   "0.25"),
        ("internal_cost_markup", "0.35"),
    ])

    conn.commit()
    conn.close()
    print(f"Database created: {path}")

    # Print summary
    conn = sqlite3.connect(path)
    c = conn.cursor()
    for table in ["regions", "products", "customers", "orders", "revenue", "internal_config"]:
        c.execute(f"SELECT COUNT(*) FROM {table}")
        print(f"  {table}: {c.fetchone()[0]} rows")
    conn.close()


if __name__ == "__main__":
    create_database()
