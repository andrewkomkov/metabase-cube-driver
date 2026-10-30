-- Deterministic demo data. Every value derives from generate_series, so the
-- conformance suite can compute the truth directly from these tables.
CREATE TABLE users (
  id int PRIMARY KEY, name text NOT NULL, email text NOT NULL, city text NOT NULL,
  state text NOT NULL, latitude double precision NOT NULL, longitude double precision NOT NULL,
  is_vip boolean NOT NULL, created_at timestamp NOT NULL);
CREATE TABLE products (
  id int PRIMARY KEY, name text NOT NULL, category text NOT NULL, price numeric(10,2) NOT NULL);
CREATE TABLE orders (
  id int PRIMARY KEY, user_id int NOT NULL REFERENCES users(id), product_id int NOT NULL REFERENCES products(id),
  status text NOT NULL, quantity int NOT NULL, amount numeric(12,2) NOT NULL, created_at timestamp NOT NULL);

INSERT INTO users
SELECT i, 'User ' || i, 'user' || i || '@example.com',
       (ARRAY['Austin','Chicago','Seattle','Boston'])[1 + i % 4],
       (ARRAY['TX','IL','WA','MA'])[1 + i % 4],
       (ARRAY[30.27,41.88,47.61,42.36])[1 + i % 4], (ARRAY[-97.74,-87.63,-122.33,-71.06])[1 + i % 4],
       i % 5 = 0, timestamp '2024-01-01' + (i || ' days')::interval
FROM generate_series(1, 200) i;

INSERT INTO products
SELECT i, 'Product ' || i, (ARRAY['Gadget','Widget','Gizmo'])[1 + i % 3], (10 + i)::numeric
FROM generate_series(1, 30) i;

INSERT INTO orders
SELECT i, 1 + i % 200, 1 + i % 30,
       (ARRAY['completed','completed','shipped','processing','cancelled'])[1 + i % 5],
       1 + i % 4,
       ((1 + i % 4) * (11 + i % 30))::numeric,
       timestamp '2025-01-01' + ((i % 365) || ' days')::interval + ((i % 24) || ' hours')::interval
FROM generate_series(1, 5000) i;
