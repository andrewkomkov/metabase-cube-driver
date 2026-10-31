CREATE TABLE repro.orders (id int PRIMARY KEY, status text, amount int, created_at timestamp);
INSERT INTO repro.orders VALUES
(1, 'a', 10, '2025-01-01 20:00:00'),
(2, 'a', 100, '2025-01-02 02:00:00');
