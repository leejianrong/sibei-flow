-- Hero pipeline seed — the HEALTHY / PRE-rename upstream state.
--
-- Run by `make hero` (against the running `warehouse` service) *before* the
-- first `analytics_daily` DAG run, so that run goes GREEN: raw_customers still
-- has `customer_id`, which orders.sql references.
--
-- This is deliberately NOT `db/warehouse/init.sql`. init.sql seeds the
-- POST-rename state (`cust_id`) that the worker tests (test_agent_loop /
-- test_sandbox) and the fixture-payload demo depend on, and it only runs once on
-- a fresh volume. The hero flow instead drives the state live:
--   make hero        -> this script (pre-rename, healthy)   -> DAG green
--   make hero-break  -> hero_break.sql (rename)              -> DAG fails -> heal
-- The end state after the break (cust_id present, customer_id gone) matches the
-- shape init.sql produces (same columns), which is exactly the drift the worker
-- expects — init.sql itself is untouched by this file.
--
-- KAN-1016: real seed data. 152 rows drawn from the real 1996 orders in the
-- classic Northwind sample dataset (customers + orders + order-details),
-- reshaped to fit this table's existing 3-column drift story:
--   customer_id  <- a stable integer assigned to each real Northwind customer
--                   code (alphabetical rank; e.g. ALFKI -> 1), so the SAME
--                   customer_id repeats across that customer's real orders.
--   order_ts     <- the real order date, with a deterministic business-hours
--                   time-of-day derived from the source orderID (Northwind's
--                   orders carry a date only).
--   amount       <- the real order total: sum(unitPrice * quantity *
--                   (1 - discount)) over that order's line items
--                   (order-details.csv), rounded to cents.
-- `company_name` / `contact_name` / `country` are the real Northwind customer
-- attributes for that row's customer_id — carried along for realism (a human
-- poking at the warehouse sees an actual company, not a bare id) but NOT
-- referenced by orders.sql, so the rename story below is unaffected by them.
--
-- Source: the classic Northwind sample database, whose canonical/licensed home
-- is Microsoft's own https://github.com/microsoft/sql-server-samples
-- (samples/databases/northwind-pubs; MIT — see that repo's license.txt). Row
-- values here were pulled from the plain-CSV re-encoding of that same dataset
-- at https://github.com/neo4j-contrib/northwind-neo4j (customers.csv /
-- orders.csv / order-details.csv) for convenience, then reshaped by
-- scripts/gen_hero_seed.py — see that script to regenerate or extend (not run
-- automatically; the output below is committed so `make hero` stays fully
-- offline with no network fetch at seed time).
--
-- 67 distinct real customers ordered in 1996; 152 real orders total. Real,
-- freely-licensed, no auth/API key, small enough to commit (~10KB of INSERTs).
--
-- Idempotent: safe to re-run. Resets raw_customers to the healthy state.

CREATE SCHEMA IF NOT EXISTS raw;

-- Recreate the upstream table in the PRE-rename (healthy) shape. No PRIMARY KEY
-- on customer_id: this is a raw landing table (one row per order event), not a
-- deduped customer dimension, so the same real customer legitimately repeats
-- across their orders.
DROP TABLE IF EXISTS raw.raw_customers CASCADE;
CREATE TABLE raw.raw_customers (
    customer_id  integer NOT NULL,     -- healthy: not yet drifted to cust_id
    company_name text,                 -- real Northwind customer (unused by orders.sql)
    contact_name text,
    country      text,
    order_ts     timestamptz,
    amount       numeric(12, 2)
);

INSERT INTO raw.raw_customers
    (customer_id, company_name, contact_name, country, order_ts, amount)
VALUES
    (85, 'Vins et alcools Chevalier', 'Paul Henriot', 'France', '1996-07-04T14:36:00+00', 440.00),
    (79, 'Toms Spezialitäten', 'Karin Josephs', 'Germany', '1996-07-05T15:43:00+00', 1863.40),
    (34, 'Hanari Carnes', 'Mario Pontes', 'Brazil', '1996-07-08T16:50:00+00', 1552.60),
    (84, 'Victuailles en stock', 'Mary Saveley', 'France', '1996-07-08T08:57:00+00', 654.06),
    (76, 'Suprêmes délices', 'Pascale Cartrain', 'Belgium', '1996-07-09T09:04:00+00', 3597.90),
    (34, 'Hanari Carnes', 'Mario Pontes', 'Brazil', '1996-07-10T10:11:00+00', 1444.80),
    (14, 'Chop-suey Chinese', 'Yang Wang', 'Switzerland', '1996-07-11T11:18:00+00', 556.62),
    (68, 'Richter Supermarkt', 'Michael Holz', 'Switzerland', '1996-07-12T12:25:00+00', 2490.50),
    (88, 'Wellington Importadora', 'Paula Parente', 'Brazil', '1996-07-15T13:32:00+00', 517.80),
    (35, 'HILARION-Abastos', 'Carlos Hernández', 'Venezuela', '1996-07-16T14:39:00+00', 1119.90),
    (20, 'Ernst Handel', 'Roland Mendel', 'Austria', '1996-07-17T15:46:00+00', 1614.88),
    (13, 'Centro comercial Moctezuma', 'Francisco Chang', 'Mexico', '1996-07-18T16:53:00+00', 100.80),
    (56, 'Ottilies Käseladen', 'Henriette Pfalzheim', 'Germany', '1996-07-19T08:00:00+00', 1504.65),
    (61, 'Que Delícia', 'Bernardo Batista', 'Brazil', '1996-07-19T09:07:00+00', 448.00),
    (65, 'Rattlesnake Canyon Grocery', 'Paula Wilson', 'USA', '1996-07-22T10:14:00+00', 584.00),
    (20, 'Ernst Handel', 'Roland Mendel', 'Austria', '1996-07-23T11:21:00+00', 1873.80),
    (24, 'Folk och fä HB', 'Maria Larsson', 'Sweden', '1996-07-24T12:28:00+00', 695.62),
    (7, 'Blondesddsl père et fils', 'Frédérique Citeaux', 'France', '1996-07-25T13:35:00+00', 1176.00),
    (87, 'Wartian Herkku', 'Pirkko Koskitalo', 'Finland', '1996-07-26T14:42:00+00', 346.56),
    (25, 'Frankenversand', 'Peter Franken', 'Germany', '1996-07-29T15:49:00+00', 3536.60),
    (33, 'GROSELLA-Restaurante', 'Manuel Pereira', 'Venezuela', '1996-07-30T16:56:00+00', 1101.20),
    (89, 'White Clover Markets', 'Karl Jablonski', 'USA', '1996-07-31T08:03:00+00', 642.20),
    (87, 'Wartian Herkku', 'Pirkko Koskitalo', 'Finland', '1996-08-01T09:10:00+00', 1376.00),
    (75, 'Split Rail Beer & Ale', 'Art Braunschweiger', 'USA', '1996-08-01T10:17:00+00', 48.00),
    (65, 'Rattlesnake Canyon Grocery', 'Paula Wilson', 'USA', '1996-08-02T11:24:00+00', 1456.00),
    (63, 'QUICK-Stop', 'Horst Kloss', 'Germany', '1996-08-05T12:31:00+00', 2037.28),
    (85, 'Vins et alcools Chevalier', 'Paul Henriot', 'France', '1996-08-06T13:38:00+00', 538.60),
    (49, 'Magazzini Alimentari Riuniti', 'Giovanni Rovelli', 'Italy', '1996-08-07T14:45:00+00', 291.84),
    (80, 'Tortuga Restaurante', 'Miguel Angel Paolino', 'Mexico', '1996-08-08T15:52:00+00', 420.00),
    (52, 'Morgenstern Gesundkost', 'Alexander Feuer', 'Germany', '1996-08-09T16:59:00+00', 1200.80),
    (5, 'Berglunds snabbköp', 'Christina Berglund', 'Sweden', '1996-08-12T08:06:00+00', 1488.80),
    (44, 'Lehmanns Marktstand', 'Renate Messner', 'Germany', '1996-08-13T09:13:00+00', 351.00),
    (5, 'Berglunds snabbköp', 'Christina Berglund', 'Sweden', '1996-08-14T10:20:00+00', 613.20),
    (69, 'Romero y tomillo', 'Alejandra Camino', 'Spain', '1996-08-14T11:27:00+00', 86.50),
    (69, 'Romero y tomillo', 'Alejandra Camino', 'Spain', '1996-08-15T12:34:00+00', 155.40),
    (46, 'LILA-Supermercado', 'Carlos González', 'Venezuela', '1996-08-16T13:41:00+00', 1414.80),
    (44, 'Lehmanns Marktstand', 'Renate Messner', 'Germany', '1996-08-19T14:48:00+00', 1170.38),
    (63, 'QUICK-Stop', 'Horst Kloss', 'Germany', '1996-08-20T15:55:00+00', 1743.36),
    (63, 'QUICK-Stop', 'Horst Kloss', 'Germany', '1996-08-21T16:02:00+00', 3016.00),
    (67, 'Ricardo Adocicados', 'Janete Limeira', 'Brazil', '1996-08-22T08:09:00+00', 819.00),
    (66, 'Reggiani Caseifici', 'Maurizio Moroni', 'Italy', '1996-08-23T09:16:00+00', 80.10),
    (11, 'B''s Beverages', 'Victoria Ashworth', 'UK', '1996-08-26T10:23:00+00', 479.40),
    (15, 'Comércio Mineiro', 'Pedro Afonso', 'Brazil', '1996-08-27T11:30:00+00', 2169.00),
    (61, 'Que Delícia', 'Bernardo Batista', 'Brazil', '1996-08-27T12:37:00+00', 497.52),
    (81, 'Tradição Hipermercados', 'Anabela Domingues', 'Brazil', '1996-08-28T13:44:00+00', 1296.00),
    (80, 'Tortuga Restaurante', 'Miguel Angel Paolino', 'Mexico', '1996-08-29T14:51:00+00', 848.70),
    (65, 'Rattlesnake Canyon Grocery', 'Paula Wilson', 'USA', '1996-08-30T15:58:00+00', 1887.60),
    (85, 'Vins et alcools Chevalier', 'Paul Henriot', 'France', '1996-09-02T16:05:00+00', 121.60),
    (46, 'LILA-Supermercado', 'Carlos González', 'Venezuela', '1996-09-03T08:12:00+00', 1050.60),
    (7, 'Blondesddsl père et fils', 'Frédérique Citeaux', 'France', '1996-09-04T09:19:00+00', 1420.00),
    (37, 'Hungry Owl All-Night Grocers', 'Patricia McKenna', 'Ireland', '1996-09-05T10:26:00+00', 2645.00),
    (67, 'Ricardo Adocicados', 'Janete Limeira', 'Brazil', '1996-09-06T11:33:00+00', 349.50),
    (49, 'Magazzini Alimentari Riuniti', 'Giovanni Rovelli', 'Italy', '1996-09-09T12:40:00+00', 608.00),
    (86, 'Die Wandernde Kuh', 'Rita Müller', 'Germany', '1996-09-09T13:47:00+00', 755.00),
    (76, 'Suprêmes délices', 'Pascale Cartrain', 'Belgium', '1996-09-10T14:54:00+00', 2708.80),
    (30, 'Godos Cocina Típica', 'José Pedro Freyre', 'Spain', '1996-09-11T15:01:00+00', 1117.80),
    (80, 'Tortuga Restaurante', 'Miguel Angel Paolino', 'Mexico', '1996-09-12T16:08:00+00', 954.40),
    (55, 'Old World Delicatessen', 'Rene Phillips', 'USA', '1996-09-13T08:15:00+00', 3741.30),
    (69, 'Romero y tomillo', 'Alejandra Camino', 'Spain', '1996-09-16T09:22:00+00', 498.50),
    (48, 'Lonesome Pine Restaurant', 'Fran Wilson', 'USA', '1996-09-17T10:29:00+00', 424.00),
    (2, 'Ana Trujillo Emparedados y helados', 'Ana Trujillo', 'Mexico', '1996-09-18T11:36:00+00', 88.80),
    (37, 'Hungry Owl All-Night Grocers', 'Patricia McKenna', 'Ireland', '1996-09-19T12:43:00+00', 1762.00),
    (77, 'The Big Cheese', 'Liz Nixon', 'USA', '1996-09-20T13:50:00+00', 336.00),
    (18, 'Du monde entier', 'Janine Labrune', 'France', '1996-09-20T14:57:00+00', 268.80),
    (86, 'Die Wandernde Kuh', 'Rita Müller', 'Germany', '1996-09-23T15:04:00+00', 1614.80),
    (63, 'QUICK-Stop', 'Horst Kloss', 'Germany', '1996-09-24T16:11:00+00', 182.40),
    (65, 'Rattlesnake Canyon Grocery', 'Paula Wilson', 'USA', '1996-09-25T08:18:00+00', 2094.30),
    (38, 'Island Trading', 'Helen Bennett', 'UK', '1996-09-26T09:25:00+00', 516.80),
    (65, 'Rattlesnake Canyon Grocery', 'Paula Wilson', 'USA', '1996-09-27T10:32:00+00', 2835.00),
    (48, 'Lonesome Pine Restaurant', 'Fran Wilson', 'USA', '1996-09-30T11:39:00+00', 288.00),
    (38, 'Island Trading', 'Helen Bennett', 'UK', '1996-10-01T12:46:00+00', 240.40),
    (80, 'Tortuga Restaurante', 'Miguel Angel Paolino', 'Mexico', '1996-10-02T13:53:00+00', 1191.20),
    (87, 'Wartian Herkku', 'Pirkko Koskitalo', 'Finland', '1996-10-03T14:00:00+00', 516.00),
    (38, 'Island Trading', 'Helen Bennett', 'UK', '1996-10-03T15:07:00+00', 144.00),
    (58, 'Pericles Comidas clásicas', 'Guillermo Fernández', 'Mexico', '1996-10-04T16:14:00+00', 112.00),
    (39, 'Königlich Essen', 'Philip Cramer', 'Germany', '1996-10-07T08:21:00+00', 164.40),
    (71, 'Save-a-lot Markets', 'Jose Pavarotti', 'USA', '1996-10-08T09:28:00+00', 5275.72),
    (39, 'Königlich Essen', 'Philip Cramer', 'Germany', '1996-10-09T10:35:00+00', 1497.00),
    (8, 'Bólido Comidas preparadas', 'Martín Sommer', 'Spain', '1996-10-10T11:42:00+00', 982.00),
    (24, 'Folk och fä HB', 'Maria Larsson', 'Sweden', '1996-10-11T12:49:00+00', 1810.00),
    (28, 'Furia Bacalhau e Frutos do Mar', 'Lino Rodriguez', 'Portugal', '1996-10-14T13:56:00+00', 1168.00),
    (75, 'Split Rail Beer & Ale', 'Art Braunschweiger', 'USA', '1996-10-15T14:03:00+00', 4578.43),
    (46, 'LILA-Supermercado', 'Carlos González', 'Venezuela', '1996-10-16T15:10:00+00', 1649.00),
    (9, 'Bon app''', 'Laurence Lebihan', 'France', '1996-10-16T16:17:00+00', 88.50),
    (51, 'Mère Paillarde', 'Jean Fresnière', 'Canada', '1996-10-17T08:24:00+00', 1786.88),
    (87, 'Wartian Herkku', 'Pirkko Koskitalo', 'Finland', '1996-10-18T09:31:00+00', 877.20),
    (84, 'Victuailles en stock', 'Mary Saveley', 'France', '1996-10-21T10:38:00+00', 144.80),
    (37, 'Hungry Owl All-Night Grocers', 'Patricia McKenna', 'Ireland', '1996-10-22T11:45:00+00', 2036.16),
    (60, 'Princesa Isabel Vinhos', 'Isabel de Castro', 'Portugal', '1996-10-23T12:52:00+00', 285.12),
    (25, 'Frankenversand', 'Peter Franken', 'Germany', '1996-10-24T13:59:00+00', 2467.00),
    (55, 'Old World Delicatessen', 'Rene Phillips', 'USA', '1996-10-25T14:06:00+00', 934.50),
    (51, 'Mère Paillarde', 'Jean Fresnière', 'Canada', '1996-10-28T15:13:00+00', 3354.00),
    (9, 'Bon app''', 'Laurence Lebihan', 'France', '1996-10-29T16:20:00+00', 2436.18),
    (73, 'Simons bistro', 'Jytte Petersen', 'Denmark', '1996-10-29T08:27:00+00', 352.60),
    (25, 'Frankenversand', 'Peter Franken', 'Germany', '1996-10-30T09:34:00+00', 1840.64),
    (44, 'Lehmanns Marktstand', 'Renate Messner', 'Germany', '1996-10-31T10:41:00+00', 1584.00),
    (89, 'White Clover Markets', 'Karl Jablonski', 'USA', '1996-11-01T11:48:00+00', 2296.00),
    (63, 'QUICK-Stop', 'Horst Kloss', 'Germany', '1996-11-04T12:55:00+00', 2924.80),
    (65, 'Rattlesnake Canyon Grocery', 'Paula Wilson', 'USA', '1996-11-05T13:02:00+00', 1618.88),
    (21, 'Familia Arquibaldo', 'Aria Cruz', 'Brazil', '1996-11-06T14:09:00+00', 814.42),
    (86, 'Die Wandernde Kuh', 'Rita Müller', 'Germany', '1996-11-07T15:16:00+00', 363.60),
    (75, 'Split Rail Beer & Ale', 'Art Braunschweiger', 'USA', '1996-11-08T16:23:00+00', 141.60),
    (41, 'La maison d''Asie', 'Annette Roulet', 'France', '1996-11-11T08:30:00+00', 642.06),
    (20, 'Ernst Handel', 'Roland Mendel', 'Austria', '1996-11-11T09:37:00+00', 5398.73),
    (28, 'Furia Bacalhau e Frutos do Mar', 'Lino Rodriguez', 'Portugal', '1996-11-12T10:44:00+00', 136.30),
    (59, 'Piccolo und mehr', 'Georg Pipps', 'Austria', '1996-11-13T11:51:00+00', 8593.28),
    (58, 'Pericles Comidas clásicas', 'Guillermo Fernández', 'Mexico', '1996-11-14T12:58:00+00', 568.80),
    (4, 'Around the Horn', 'Thomas Hardy', 'UK', '1996-11-15T13:05:00+00', 480.00),
    (86, 'Die Wandernde Kuh', 'Rita Müller', 'Germany', '1996-11-18T14:12:00+00', 1106.40),
    (46, 'LILA-Supermercado', 'Carlos González', 'Venezuela', '1996-11-19T15:19:00+00', 1167.68),
    (41, 'La maison d''Asie', 'Annette Roulet', 'France', '1996-11-20T16:26:00+00', 429.40),
    (72, 'Seven Seas Imports', 'Hari Kumar', 'UK', '1996-11-21T08:33:00+00', 3471.68),
    (7, 'Blondesddsl père et fils', 'Frédérique Citeaux', 'France', '1996-11-22T09:40:00+00', 7390.20),
    (63, 'QUICK-Stop', 'Horst Kloss', 'Germany', '1996-11-22T10:47:00+00', 2046.24),
    (9, 'Bon app''', 'Laurence Lebihan', 'France', '1996-11-25T11:54:00+00', 1549.60),
    (17, 'Drachenblut Delikatessen', 'Sven Ottlieb', 'Germany', '1996-11-26T12:01:00+00', 447.20),
    (19, 'Eastern Connection', 'Ann Devon', 'UK', '1996-11-26T13:08:00+00', 950.00),
    (3, 'Antonio Moreno Taquería', 'Antonio Moreno', 'Mexico', '1996-11-27T14:15:00+00', 403.20),
    (29, 'Galería del gastrónomo', 'Eduardo Saavedra', 'Spain', '1996-11-28T15:22:00+00', 136.00),
    (83, 'Vaffeljernet', 'Palle Ibsen', 'Denmark', '1996-11-28T16:29:00+00', 834.20),
    (20, 'Ernst Handel', 'Roland Mendel', 'Austria', '1996-11-29T08:36:00+00', 1689.78),
    (75, 'Split Rail Beer & Ale', 'Art Braunschweiger', 'USA', '1996-12-02T09:43:00+00', 2390.40),
    (14, 'Chop-suey Chinese', 'Yang Wang', 'Switzerland', '1996-12-03T10:50:00+00', 1117.60),
    (41, 'La maison d''Asie', 'Annette Roulet', 'France', '1996-12-03T11:57:00+00', 72.96),
    (62, 'Queen Cozinha', 'Lúcia Carvalho', 'Brazil', '1996-12-04T12:04:00+00', 9210.90),
    (37, 'Hungry Owl All-Night Grocers', 'Patricia McKenna', 'Ireland', '1996-12-05T13:11:00+00', 1366.40),
    (91, 'Wolski  Zajazd', 'Zbyszek Piestrzeniewicz', 'Poland', '1996-12-05T14:18:00+00', 459.00),
    (36, 'Hungry Coyote Import Store', 'Yoshi Latimer', 'USA', '1996-12-06T15:25:00+00', 338.00),
    (51, 'Mère Paillarde', 'Jean Fresnière', 'Canada', '1996-12-09T16:32:00+00', 399.00),
    (72, 'Seven Seas Imports', 'Hari Kumar', 'UK', '1996-12-09T08:39:00+00', 863.60),
    (24, 'Folk och fä HB', 'Maria Larsson', 'Sweden', '1996-12-10T09:46:00+00', 103.20),
    (61, 'Que Delícia', 'Bernardo Batista', 'Brazil', '1996-12-11T10:53:00+00', 863.28),
    (37, 'Hungry Owl All-Night Grocers', 'Patricia McKenna', 'Ireland', '1996-12-12T11:00:00+00', 1313.82),
    (46, 'LILA-Supermercado', 'Carlos González', 'Venezuela', '1996-12-12T12:07:00+00', 112.00),
    (20, 'Ernst Handel', 'Roland Mendel', 'Austria', '1996-12-13T13:14:00+00', 2900.00),
    (4, 'Around the Horn', 'Thomas Hardy', 'UK', '1996-12-16T14:21:00+00', 899.00),
    (5, 'Berglunds snabbköp', 'Christina Berglund', 'Sweden', '1996-12-16T15:28:00+00', 2222.40),
    (75, 'Split Rail Beer & Ale', 'Art Braunschweiger', 'USA', '1996-12-17T16:35:00+00', 691.20),
    (21, 'Familia Arquibaldo', 'Aria Cruz', 'Brazil', '1996-12-18T08:42:00+00', 166.00),
    (70, 'Santé Gourmet', 'Jonas Bergulfsen', 'Norway', '1996-12-18T09:49:00+00', 1058.40),
    (72, 'Seven Seas Imports', 'Hari Kumar', 'UK', '1996-12-19T10:56:00+00', 1228.80),
    (10, 'Bottom-Dollar Markets', 'Elizabeth Lincoln', 'Canada', '1996-12-20T11:03:00+00', 1832.80),
    (20, 'Ernst Handel', 'Roland Mendel', 'Austria', '1996-12-23T12:10:00+00', 2090.88),
    (17, 'Drachenblut Delikatessen', 'Sven Ottlieb', 'Germany', '1996-12-23T13:17:00+00', 86.40),
    (59, 'Piccolo und mehr', 'Georg Pipps', 'Austria', '1996-12-24T14:24:00+00', 1440.00),
    (71, 'Save-a-lot Markets', 'Jose Pavarotti', 'USA', '1996-12-25T15:31:00+00', 2556.95),
    (36, 'Hungry Coyote Import Store', 'Yoshi Latimer', 'USA', '1996-12-25T16:38:00+00', 442.00),
    (35, 'HILARION-Abastos', 'Carlos Hernández', 'Venezuela', '1996-12-26T08:45:00+00', 2122.92),
    (25, 'Frankenversand', 'Peter Franken', 'Germany', '1996-12-27T09:52:00+00', 1903.80),
    (60, 'Princesa Isabel Vinhos', 'Isabel de Castro', 'Portugal', '1996-12-27T10:59:00+00', 716.72),
    (71, 'Save-a-lot Markets', 'Jose Pavarotti', 'USA', '1996-12-30T11:06:00+00', 2505.60),
    (83, 'Vaffeljernet', 'Palle Ibsen', 'Denmark', '1996-12-31T12:13:00+00', 1765.60);

-- The pipeline's OWN application role. Distinct from sibei-flow's read-only
-- (sbflow_ro) and dev/sample (sbflow_dev) roles: Airflow's dbt runs use this to
-- build staging + mart views. sibei-flow never holds this credential — it only
-- reads (sbflow_ro) or builds into a sample schema (sbflow_dev). No prod-write
-- credential lives anywhere in sibei-flow itself.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_app') THEN
        CREATE ROLE analytics_app LOGIN PASSWORD 'analytics_app';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE warehouse TO analytics_app;
GRANT USAGE, CREATE ON SCHEMA raw TO analytics_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA raw TO analytics_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO analytics_app;
-- A schema for the DAG's built models (staging + marts materialize here).
CREATE SCHEMA IF NOT EXISTS analytics AUTHORIZATION analytics_app;

-- Re-grant sibei-flow's read-only + sample roles on the freshly recreated table
-- (DROP TABLE dropped the old grants). These roles are created by init.sql; if
-- the volume was seeded elsewhere they may be absent, so guard the grants.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sbflow_ro') THEN
        GRANT USAGE ON SCHEMA raw TO sbflow_ro;
        GRANT SELECT ON raw.raw_customers TO sbflow_ro;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sbflow_dev') THEN
        GRANT USAGE ON SCHEMA raw TO sbflow_dev;
        GRANT SELECT ON raw.raw_customers TO sbflow_dev;
    END IF;
END
$$;
