CREATE TABLE repro.orders (id Int32, status String, amount Int32, created_at DateTime('UTC'), shipped_at DateTime('UTC')) ENGINE=MergeTree ORDER BY id;
INSERT INTO repro.orders VALUES (1,'a',1,'2025-01-31 00:00:00','2025-02-01 00:00:00'),(2,'a',1,'2025-01-01 00:00:00','2025-01-31 00:00:00');
