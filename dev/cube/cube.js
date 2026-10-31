// CUBE_CLICKHOUSE_FIXES=off gives stock Cube. See cube-clickhouse/README.md.
module.exports = process.env.CUBE_CLICKHOUSE_FIXES === 'off' ? {} : require('./cube-clickhouse');
