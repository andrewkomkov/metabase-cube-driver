// Demo config for per-user data in Cube. All values are throwaway values for fake data.
//
// The SQL API user name is the identity. Metabase signs in with the name of the person who runs the query.
// One password is shared. Only Metabase and Cube know it. See docs/security.md.
// CUBE_CLICKHOUSE_FIXES=off gives stock Cube. See cube-clickhouse/README.md.
const clickHouseFixes = process.env.CUBE_CLICKHOUSE_FIXES === 'off' ? {} : require('./cube-clickhouse');

const PEOPLE = {
  // The account that Metabase uses for sync. It sees all rows.
  metabase: { all: true, groups: ['analyst'] },
  'admin@example.com': { all: true, groups: ['analyst'] },
  'alice@example.com': { city: 'Austin', groups: ['analyst'] },
  'bob@example.com': { city: 'Boston', groups: [] },
  // A value of a Metabase user attribute works in the same way.
  'tenant-seattle': { city: 'Seattle', groups: [] },
};

// The security context is { user } on the SQL API, from checkSqlAuth, and on the REST API, from the JWT of the driver.
const personOf = (securityContext) => PEOPLE[securityContext?.user];

module.exports = {
  ...clickHouseFixes,

  checkSqlAuth: async (_req, username) => {
    if (!PEOPLE[username]) {
      throw new Error(`Unknown SQL user: ${username}`);
    }
    return {
      password: process.env.CUBEJS_SQL_PASSWORD,
      securityContext: { user: username },
    };
  },

  // A separate compiled model per kind of person, so that COMPILE_CONTEXT takes effect.
  contextToAppId: ({ securityContext }) => `app_${personOf(securityContext)?.all ? 'all' : 'limited'}`,

  contextToGroups: ({ securityContext }) => personOf(securityContext)?.groups ?? [],

  // The row-level rule. A query with no known person fails.
  queryRewrite: (query, { securityContext }) => {
    const person = personOf(securityContext);
    if (person?.all) {
      return query;
    }
    const city = person?.city;
    if (!city) {
      throw new Error('No row-level rule for this user');
    }
    query.filters = query.filters ?? [];
    query.filters.push({ member: 'users.city', operator: 'equals', values: [city] });
    return query;
  },
};
