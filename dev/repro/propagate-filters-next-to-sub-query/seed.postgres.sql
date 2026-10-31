CREATE TABLE repro.users (id int);
CREATE TABLE repro.orders (id int, user_id int, status text);
INSERT INTO repro.users VALUES (1),(2);
INSERT INTO repro.orders VALUES (1,1,'early'),(2,1,'late'),(3,2,'late');
