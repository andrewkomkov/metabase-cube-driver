// A child cube in JavaScript that defines nothing again. Member names differ from column names, so only the SQL of the parent
// tells which column a join reads. Cube leaves that SQL out of /v1/meta on the child.
cube(`js_base_line`, {
  public: false,
  title: `JS base line`,
  sql: `SELECT * FROM shop.orders`,
  joins: {
    users: { sql: `${CUBE}.user_id = ${users.id}`, relationship: `belongsTo` },
  },
  dimensions: {
    id: { sql: `id`, type: `number`, primaryKey: true, public: true },
    buyerKey: { sql: `user_id`, type: `number`, description: `The key of the buyer.` },
    orderStatus: { sql: `status`, type: `string`, description: `The status of the order.` },
    sizeBand: {
      type: `string`,
      description: `Large or small.`,
      case: { when: [{ sql: `${CUBE}.quantity >= 3`, label: `large` }], else: { label: `small` } },
    },
    placedAt: { sql: `created_at`, type: `time`, description: `The time of the order.` },
  },
  measures: {
    count: { type: `count`, description: `Orders.` },
    revenue: { sql: `amount`, type: `sum`, description: `Revenue.` },
    revenueTrailing: { sql: `amount`, type: `sum`, rollingWindow: { trailing: `30 day` }, description: `Revenue of 30 days.` },
    revenuePriorYear: {
      multiStage: true,
      sql: `${revenue}`,
      type: `number`,
      timeShift: [{ timeDimension: placedAt, interval: `1 year`, type: `prior` }],
      description: `Revenue one year before.`,
    },
  },
});

cube(`js_child_line`, {
  extends: js_base_line,
  public: true,
  title: `JS child line`,
  description: `Each member comes from the parent.`,
});
