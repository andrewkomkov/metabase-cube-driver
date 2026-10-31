-- Data for the checks that came after the first seed. Each statement is safe to run again, so this runs on an old volume too.
CREATE DATABASE IF NOT EXISTS shop_second;
CREATE TABLE IF NOT EXISTS shop_second.tickets (id UInt32, priority String, minutes UInt32) ENGINE = MergeTree ORDER BY id;
INSERT INTO shop_second.tickets SELECT number + 1, ['low', 'medium', 'high'][number % 3 + 1], (number * 7) % 50 + 1 FROM numbers(300)
  WHERE (SELECT count() FROM shop_second.tickets) = 0;
CREATE TABLE IF NOT EXISTS shop.refresh_marks (id UInt32) ENGINE = MergeTree ORDER BY id;
