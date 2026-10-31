CREATE TABLE repro.orders (id int PRIMARY KEY, status text, amount int, created_at timestamp);
INSERT INTO repro.orders VALUES
(1, 'a', 1, '2025-04-15 00:00:00'),
(2, 'a', 10, '2025-05-01 00:00:00'),
(3, 'a', 100, '2025-05-20 00:00:00'),
(4, 'a', 1000, '2025-06-10 00:00:00');
