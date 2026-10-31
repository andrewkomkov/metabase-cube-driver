// A small ClickHouse dialect for stock Cube. It holds only the fixes that wait for a change in Cube. When Cube ships a fix,
// remove its override here. docs/cube-audit.md has the verdict and the repro of each defect.
const { ClickHouseQuery } = require('@cubejs-backend/schema-compiler/dist/src/adapter/ClickHouseQuery');

class ClickHouseFixes extends ClickHouseQuery {
  // B1: https://github.com/cube-js/cube/pull/12532
  // parseDateTimeBestEffort drops the milliseconds of a bound, and the SQL API starts a strict bound 1 ms later, so the row at
  // the bound stays in the result.
  dateTimeCast(value, timezone) {
    if (timezone) {
      return super.dateTimeCast(value, timezone);
    }
    return `parseDateTime64BestEffort(${value}, 3, 'UTC')`;
  }

  // datetime64: https://github.com/cube-js/cube/pull/12533
  // Precision 0 drops the milliseconds of a selected time and of a time in SQL push-down.
  convertTz(field) {
    return `toTimeZone(toDateTime64(${field}, 3), '${this.timezone}')`;
  }

  // N2: no pull request yet, branch fix/clickhouse-legacy-time-series, see docs/cube-audit.md#n2.
  // Only the legacy planner reads this. Two arrayJoin calls in one SELECT give a cross product of the bounds.
  seriesSql(timeDimension) {
    const series = timeDimension.timeSeries();
    const quoted = (values) => `['${values.join("','")}']`;
    const from = quoted(series.map(([start]) => start));
    const to = quoted(series.map(([, end]) => end));
    return `SELECT parseDateTimeBestEffort(dates.1) as date_from, parseDateTimeBestEffort(dates.2) as date_to FROM (SELECT arrayJoin(arrayZip(${from}, ${to})) as dates)`;
  }

  // m5: no pull request yet, branch fix/clickhouse-original-sql-no-index, see docs/cube-audit.md#m5.
  // An original_sql table has the columns of the cube SQL, and an index names members, so a sort key or an index on it fails.
  preAggregationLoadSql(cube, preAggregation, tableName) {
    if (preAggregation.type === 'originalSql') {
      const [sql, params] = this.preAggregationSql(cube, preAggregation);
      return [`CREATE TABLE ${tableName} ENGINE = MergeTree() ORDER BY tuple() ${this.asSyntaxTable} ${sql}`, params];
    }
    return super.preAggregationLoadSql(cube, preAggregation, tableName);
  }

  // m5: see preAggregationLoadSql. Cube runs this statement after it loads the table.
  indexSql(cube, preAggregation, index, indexName, tableName) {
    if (preAggregation.type === 'originalSql') {
      return ['SELECT 1', []];
    }
    return super.indexSql(cube, preAggregation, index, indexName, tableName);
  }

  sqlTemplates() {
    const templates = super.sqlTemplates();
    // interval: https://github.com/cube-js/cube/pull/12534
    templates.expressions.interval_single_date_part = 'INTERVAL {{ num }} {{ date_part }}';
    // B1: https://github.com/cube-js/cube/pull/12532 does not change this template, which SQL push-down writes.
    // Remove it when push-down keeps milliseconds.
    templates.expressions.timestamp_literal = "parseDateTime64BestEffort('{{ value }}', 3, 'UTC')";
    return templates;
  }
}

// Cube refuses an unknown key in its options, so this exports only the option.
module.exports = {
  dialectFactory: ({ dbType }) => (dbType === 'clickhouse' ? ClickHouseFixes : undefined),
};
