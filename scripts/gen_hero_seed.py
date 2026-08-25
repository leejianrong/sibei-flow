#!/usr/bin/env python3
"""Regenerate the real-data INSERT block in db/warehouse/hero_seed.sql (KAN-1016).

This is a one-off/manual tool, not part of `make hero` or any CI step — the
seed data it produces is committed directly into hero_seed.sql, so `make hero`
never needs network access. Run this only if you want to regenerate or extend
the dataset (e.g. pull a different year, or more rows).

Source data: the classic Northwind sample database. Its canonical, MIT-licensed
home is Microsoft's own https://github.com/microsoft/sql-server-samples
(samples/databases/northwind-pubs/license.txt). For convenience this script
reads the same dataset from its plain-CSV re-encoding at
https://github.com/neo4j-contrib/northwind-neo4j — fetch customers.csv,
orders.csv and order-details.csv from that repo's `data/` directory into the
current directory before running this script (not committed here; only the
generated SQL is).

Usage:
    curl -sLO https://raw.githubusercontent.com/neo4j-contrib/northwind-neo4j/master/data/customers.csv
    curl -sLO https://raw.githubusercontent.com/neo4j-contrib/northwind-neo4j/master/data/orders.csv
    curl -sLO https://raw.githubusercontent.com/neo4j-contrib/northwind-neo4j/master/data/order-details.csv
    python3 scripts/gen_hero_seed.py > /tmp/values.sql
    # then splice /tmp/values.sql into db/warehouse/hero_seed.sql's
    # INSERT ... VALUES block.

Reshaping: raw_customers keeps its existing 3-column drift story (customer_id /
order_ts / amount) plus real customer attributes for flavor (company_name /
contact_name / country, unused by orders.sql):
  customer_id  <- a stable integer per real Northwind customer code
                  (alphabetical rank over customers.csv, e.g. ALFKI -> 1); the
                  SAME id repeats across that customer's real orders.
  order_ts     <- the real order date (orders.csv) plus a deterministic
                  business-hours time-of-day derived from the orderID
                  (Northwind's own dates carry no time component).
  amount       <- the real order total: sum(unitPrice * quantity *
                  (1 - discount)) over that order's line items
                  (order-details.csv), rounded to cents.

customers.csv has a couple of rows with an unquoted comma inside the address
field (e.g. HANAR's "Rua do Paço, 67"), which breaks a naive CSV split; see
`load_customers` below.
"""

from __future__ import annotations

import csv
import datetime
import sys

CUST_COLS = [
    "customerID",
    "companyName",
    "contactName",
    "contactTitle",
    "address",
    "city",
    "region",
    "postalCode",
    "country",
    "phone",
    "fax",
]

# Sanity check: a country column should look like a country, not a
# mis-parsed postal code (the tell for the unquoted-comma bug above).
KNOWN_COUNTRIES = {
    "Germany",
    "Mexico",
    "UK",
    "Sweden",
    "France",
    "Spain",
    "Canada",
    "Argentina",
    "Switzerland",
    "Brazil",
    "Austria",
    "Italy",
    "Portugal",
    "USA",
    "Venezuela",
    "Ireland",
    "Belgium",
    "Norway",
    "Denmark",
    "Finland",
    "Poland",
}


def load_customers(path: str) -> dict[str, dict[str, str]]:
    """Robust parser for customers.csv's few unquoted-comma rows: any row with
    more than 11 comma-split fields gets its extras folded back into the
    `address` field (index 4)."""
    out: dict[str, dict[str, str]] = {}
    with open(path) as f:
        lines = f.read().splitlines()
    header = lines[0].split(",")
    assert header == CUST_COLS, header
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split(",")
        n = len(fields)
        if n > 11:
            extra = n - 11
            merged_address = ",".join(fields[4 : 4 + extra + 1])
            fields = fields[:4] + [merged_address] + fields[4 + extra + 1 :]
        assert len(fields) == 11, (line, fields)
        row = dict(zip(CUST_COLS, fields))
        assert row["country"] in KNOWN_COUNTRIES, row
        out[row["customerID"]] = row
    return out


def esc(s: str) -> str:
    return (s or "").replace("'", "''")


def main(year: str = "1996") -> None:
    customers = load_customers("customers.csv")

    with open("orders.csv") as f:
        orders = [r for r in csv.DictReader(f) if r["orderDate"].startswith(year)]

    with open("order-details.csv") as f:
        details = list(csv.DictReader(f))

    totals: dict[str, float] = {}
    for d in details:
        oid = d["orderID"]
        amt = float(d["unitPrice"]) * float(d["quantity"]) * (1 - float(d["discount"]))
        totals[oid] = totals.get(oid, 0.0) + amt

    # Stable integer id per real Northwind customer code (alphabetical rank).
    codes = sorted(customers.keys())
    code_to_id = {c: i + 1 for i, c in enumerate(codes)}

    orders.sort(key=lambda r: (r["orderDate"], r["orderID"]))

    rows = []
    for r in orders:
        cust = customers.get(r["customerID"])
        if cust is None:
            continue
        total = round(totals.get(r["orderID"], 0.0), 2)
        if total <= 0:
            continue
        d = datetime.date.fromisoformat(r["orderDate"][:10])
        seed = int(r["orderID"])
        hh = 8 + (seed % 9)  # deterministic business hours 08:00-16:00
        mm = (seed * 7) % 60
        ts = datetime.datetime.combine(d, datetime.time(hh, mm)).isoformat() + "+00"
        rows.append(
            (
                code_to_id[r["customerID"]],
                esc(cust["companyName"]),
                esc(cust["contactName"]),
                esc(cust["country"]),
                ts,
                total,
            )
        )

    print(
        f"-- {len(rows)} real Northwind orders ({year}), "
        f"{len(set(row[0] for row in rows))} distinct customers",
        file=sys.stderr,
    )
    lines = [
        f"    ({cid}, '{company}', '{contact}', '{country}', '{ts}', {amt:.2f})"
        for cid, company, contact, country, ts, amt in rows
    ]
    print(",\n".join(lines) + ";")


if __name__ == "__main__":
    main(*sys.argv[1:])
