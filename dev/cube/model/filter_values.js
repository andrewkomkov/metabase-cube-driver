// Cubes whose SQL passes a filter of Cube to a parameter of a view, as a value. The views are in dev/clickhouse/seed-extra.sql.
// With no filter that Cube can read there, Cube writes `1 = 1` in the place of the value.
cube(`orders_in_window`, {
  title: `Orders in a window`,
  description: `A view with parameters reads the date range as a value.`,
  sql: `SELECT * FROM shop.orders_window(
    date_from = ${FILTER_PARAMS.orders_in_window.createdAt.filter((from, to) => from)},
    date_to = ${FILTER_PARAMS.orders_in_window.createdAt.filter((from, to) => to)})`,
  dimensions: {
    id: { sql: `id`, type: `number`, primaryKey: true, public: true },
    status: { sql: `status`, type: `string`, description: `The status of the order.` },
    createdAt: { sql: `created_at`, type: `time`, description: `The time of the order.` },
  },
  measures: {
    revenue: { sql: `amount`, type: `sum`, description: `Revenue.` },
  },
});

cube(`orders_of_status`, {
  title: `Orders of a status`,
  description: `A view with a parameter reads the filter on a text as a value.`,
  sql: `SELECT * FROM shop.orders_status(wanted = ${FILTER_PARAMS.orders_of_status.status.filter((value) => value)})`,
  dimensions: {
    id: { sql: `id`, type: `number`, primaryKey: true, public: true },
    status: { sql: `status`, type: `string`, description: `The status of the order.` },
  },
  measures: {
    revenue: { sql: `amount`, type: `sum`, description: `Revenue.` },
  },
});
