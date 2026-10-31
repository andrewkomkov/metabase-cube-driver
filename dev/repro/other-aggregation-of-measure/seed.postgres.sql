CREATE TABLE repro.orders (id int PRIMARY KEY, status text, amount int, created_at timestamp);
INSERT INTO repro.orders VALUES
(1, 'a', 100, '2025-05-05 00:00:00'),
(2, 'a', 200, '2025-05-15 00:00:00'),
(3, 'b', 400, '2025-06-01 00:00:00');
