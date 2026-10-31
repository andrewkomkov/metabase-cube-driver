CREATE TABLE repro.orders (id int, user_id int, status text, amount int);
CREATE TABLE repro.users (id int, state text);
INSERT INTO repro.orders VALUES (1,1,'a',10),(2,2,'a',100);
INSERT INTO repro.users VALUES (1,'TX'),(2,'WA');
