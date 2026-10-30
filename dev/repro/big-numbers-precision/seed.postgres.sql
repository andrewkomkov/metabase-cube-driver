CREATE TABLE repro.events (id int, big_int bigint, big_dec numeric(38,8));
INSERT INTO repro.events VALUES (1, 9007199254740995, 3000000000000.00000003);
