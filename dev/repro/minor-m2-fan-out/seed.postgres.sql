CREATE TABLE repro.orders (id int, quantity int);
CREATE TABLE repro.payments (id int, order_id int);
INSERT INTO repro.orders VALUES (1,5),(2,7);
INSERT INTO repro.payments VALUES (1,1),(2,1),(3,2),(4,2);
