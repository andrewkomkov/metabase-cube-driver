CREATE TABLE repro.orders (id Int32, quantity Int32) ENGINE=MergeTree ORDER BY id;
CREATE TABLE repro.payments (id Int32, order_id Int32) ENGINE=MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES (1,5),(2,7);
INSERT INTO repro.payments VALUES (1,1),(2,1),(3,2),(4,2);
