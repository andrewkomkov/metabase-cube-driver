CREATE TABLE repro.orders (id int, user_id int, status text, amount int, created_at timestamp);
CREATE TABLE repro.users (id int, vip int);
INSERT INTO repro.orders VALUES (1,1,'a',100,'2025-01-01 00:00:00'),(2,2,'b',500,'2025-01-02 00:00:00'),(3,2,'b',100,'2025-01-03 00:00:00');
INSERT INTO repro.users VALUES (1,1),(2,0);
