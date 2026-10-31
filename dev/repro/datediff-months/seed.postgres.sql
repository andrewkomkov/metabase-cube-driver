CREATE TABLE repro.orders (id int, status text, amount int, created_at timestamp, shipped_at timestamp);
INSERT INTO repro.orders VALUES (1,'a',1,'2025-01-31 00:00:00','2025-02-01 00:00:00'),(2,'a',1,'2025-01-01 00:00:00','2025-01-31 00:00:00');
