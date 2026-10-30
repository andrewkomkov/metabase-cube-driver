// Demo config for per-user data in Cube. All values are throwaway values for fake data.
//
// The SQL API user name is the identity. Metabase signs in with the name of the person who runs the query.
// One password is shared. Only Metabase and Cube know it. See docs/security.md.
const PEOPLE = {
  // The account that Metabase uses for sync. It sees all rows.
  metabase: { all: true, groups: ['analyst'] },
  'admin@example.com': { all: true, groups: ['analyst'] },
  'alice@example.com': { city: 'Austin', groups: ['analyst'] },
  'bob@example.com': { city: 'Boston', groups: [] },
  // A value of a Metabase user attribute works in the same way.
  'tenant-seattle': { city: 'Seattle', groups: [] },
};

module.exports = {
  checkSqlAuth: async (_req, username) => {
    const person = PEOPLE[username];
    if (!person) {
      throw new Error(`Unknown SQL user: ${username}`);
    }
    return {
      password: process.env.CUBEJS_SQL_PASSWORD,
      securityContext: { user: username, ...person },
    };
  },

  // A separate compiled model per kind of person, so that COMPILE_CONTEXT takes effect.
  contextToAppId: ({ securityContext }) => `app_${securityContext?.all ? 'all' : 'limited'}`,

  contextToGroups: ({ securityContext }) => securityContext?.groups ?? [],

  // The row-level rule. A query with no known person fails.
  queryRewrite: (query, { securityContext }) => {
    if (securityContext?.all) {
      return query;
    }
    const city = securityContext?.city;
    if (!city) {
      throw new Error('No row-level rule for this user');
    }
    query.filters = query.filters ?? [];
    query.filters.push({ member: 'users.city', operator: 'equals', values: [city] });
    return query;
  },
};
