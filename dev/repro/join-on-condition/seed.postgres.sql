CREATE TABLE repro.orders (id int, user_id int, other_id int);
CREATE TABLE repro.users (id int, vip int);
INSERT INTO repro.orders VALUES (1,1,2),(2,2,1);
INSERT INTO repro.users VALUES (1,1),(2,0),(3,1);
