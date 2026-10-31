CREATE TABLE repro.events (id Int32, big_int UInt64, big_dec Decimal(38,8)) ENGINE=MergeTree ORDER BY id;
INSERT INTO repro.events VALUES (1, 9007199254740995, 3000000000000.00000003);
