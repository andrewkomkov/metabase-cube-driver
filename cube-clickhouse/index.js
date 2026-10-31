// A small ClickHouse dialect for stock Cube. It holds only the fixes that wait for a change in Cube. When Cube ships a fix,
// remove its override here. docs/cube-audit.md has the verdict and the repro of each defect.
const { ClickHouseQuery } = require('@cubejs-backend/schema-compiler/dist/src/adapter/ClickHouseQuery');
const { CubeStoreQuery } = require('@cubejs-backend/schema-compiler/dist/src/adapter/CubeStoreQuery');
const { PreAggregationLoader } = require('@cubejs-backend/query-orchestrator/dist/src/orchestrator/PreAggregationLoader');
const { QueryCache } = require('@cubejs-backend/query-orchestrator/dist/src/orchestrator/QueryCache');

// Rollup over original_sql: no pull request yet, see docs/cube-audit.md#original-sql-rollup.
// The load of a rollup into Cube Store streams its SQL with the name of the original_sql table, and that name has no version.
// Only the load into the source database replaces the name, so a rollup with use_original_sql_pre_aggregations fails.
const refresh = PreAggregationLoader.prototype.refresh;
PreAggregationLoader.prototype.refresh = function refreshWithVersionedNames(...args) {
  const tables = this.preAggregationsTablesToTempTables;
  if (this.preAggregation.external && tables && tables.length) {
    const [sql, params] = this.preAggregation.sql;
    this.preAggregation = { ...this.preAggregation, sql: [QueryCache.replacePreAggregationTableNamesInSql(sql, tables), params] };
  }
  return refresh.apply(this, args);
};

const SERIES_ZONE = 'zone-of-the-query';

class ClickHouseFixes extends ClickHouseQuery {
  // B1: https://github.com/cube-js/cube/pull/12532
  // parseDateTimeBestEffort drops the milliseconds of a bound, and the SQL API starts a strict bound 1 ms later, so the row at
  // the bound stays in the result.
  dateTimeCast(value, timezone) {
    if (timezone) {
      return super.dateTimeCast(value, timezone);
    }
    // M3: no pull request yet, see docs/review/0.1.0-final.md. ClickHouse compares a Date with a time in the zone of that time, so a bound with the zone
    // of the query keeps a Date on its day. A DateTime keeps its instant.
    // Rolling window in a zone: the planner writes the range of a rolling window as a local time of the query, with no offset, and each other range with Z. A parser with the zone reads both.
    return `parseDateTime64BestEffort(${value}, 3, '${this.timezone || 'UTC'}')`;
  }

  // datetime64: https://github.com/cube-js/cube/pull/12533
  // Precision 0 drops the milliseconds of a selected time and of a time in SQL push-down.
  // M3: no pull request yet, see docs/review/0.1.0-final.md. toDateTime64 with the zone keeps the instant of a DateTime, and it reads a Date as
  // midnight in that zone. toTimeZone(toDateTime64(x, 3), zone) reads a Date as midnight UTC, so it moves to the day before, west of UTC.
  convertTz(field) {
    return `toDateTime64(${field}, 3, '${this.timezone}')`;
  }

  // N2: no pull request yet, branch fix/clickhouse-legacy-time-series, see docs/cube-audit.md#n2.
  // Only the legacy planner reads this. Two arrayJoin calls in one SELECT give a cross product of the bounds.
  seriesSql(timeDimension) {
    const series = timeDimension.timeSeries();
    const quoted = (values) => `['${values.join("','")}']`;
    const from = quoted(series.map(([start]) => start));
    const to = quoted(series.map(([, end]) => end));
    return `SELECT ${this.seriesBound('dates.1')} as date_from, ${this.seriesBound('dates.2')} as date_to FROM (SELECT arrayJoin(arrayZip(${from}, ${to})) as dates)`;
  }

  // Rolling window in a zone: no pull request yet, see docs/cube-audit.md#rolling-window-zone.
  // Cube writes the bounds of a bucket as local times of the query, and stock Cube reads them in the zone of the server. So in New York the first day of
  // a month starts in the month before, and a window to date adds that month. East of UTC, a window also takes the first hours of the next bucket.
  seriesBound(text) {
    return `parseDateTime64BestEffort(${text}, 3, '${this.timezone || 'UTC'}')`;
  }

  // The planner reads the templates once, for all queries, and the data of this template has no zone. So the template holds a mark, and this writes the zone of the query.
  buildSqlAndParamsRust(exportAnnotatedSql) {
    const [sql, ...rest] = super.buildSqlAndParamsRust(exportAnnotatedSql);
    return [sql.split(SERIES_ZONE).join(this.timezone || 'UTC'), ...rest];
  }

  // Time shift and rolling window in a zone: no pull request yet, see docs/cube-audit.md#rolling-window-zone. ClickHouse adds a day or a month in the zone of the value. A column in UTC gets
  // 24 hours next to a clock change of the zone of the query, so a row moves to another day of that zone.
  addInterval(date, interval) {
    return super.addInterval(this.inQueryZone(date), interval);
  }

  subtractInterval(date, interval) {
    return super.subtractInterval(this.inQueryZone(date), interval);
  }

  inQueryZone(date) {
    return this.timezone && this.timezone !== 'UTC' ? `toDateTime64(${date}, 3, '${this.timezone}')` : date;
  }

  // Range of a partitioned rollup: no pull request yet, see docs/cube-audit.md#rollup-range-zone.
  // Cube reads the first and the last time of the data as UTC. convertTz gives them in the zone of the query, so west of UTC a rollup
  // leaves out the last hours of the data, and east of UTC the first hours.
  aggSelectForDimension(cube, dimension, aggFunction) {
    const sql = super.aggSelectForDimension(cube, dimension, aggFunction);
    if (!this.timezone || this.timezone === 'UTC') {
      return sql;
    }
    return sql.replace(/^select (\w+)\((.*?)\) from /s, "select toTimeZone($1($2), 'UTC') from ");
  }

  // m5: no pull request yet, branch fix/clickhouse-original-sql-no-index, see docs/cube-audit.md#m5.
  // An original_sql table has the columns of the cube SQL, and an index names members, so a sort key or an index on it fails.
  // A rollup with no index: no pull request yet. Stock Cube stops each query on it, but the docs give this form. See docs/cube-audit.md#rollup-no-index.
  preAggregationLoadSql(cube, preAggregation, tableName) {
    if (preAggregation.type === 'originalSql' || !preAggregation.indexes) {
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
    // Rolling window in a zone: see seriesBound and buildSqlAndParamsRust.
    templates.statements.time_series_select = 'SELECT dates.f date_from, dates.t date_to \n' +
      'FROM (\n' +
      '{% for time_item in seria  %}' +
      `    select parseDateTime64BestEffort('{{ time_item[0] }}', 3, '${SERIES_ZONE}') f, parseDateTime64BestEffort('{{ time_item[1] }}', 3, '${SERIES_ZONE}') t \n` +
      '{% if not loop.last %} UNION ALL\n{% endif %}' +
      '{% endfor %}' +
      ') AS dates';
    // M3: see dateTimeCast. SQL push-down writes these templates.
    if (this.timezone && this.timezone !== 'UTC') {
      const inZone = (text) => `toTimeZone(${text}, '${this.timezone}')`;
      templates.expressions.timestamp_literal = inZone(templates.expressions.timestamp_literal);
      templates.tesseract.time_param_cast = inZone(templates.tesseract.time_param_cast);
    }
    return templates;
  }
}

// Rolling window from a rollup in a zone: no pull request yet, see docs/cube-audit.md#rolling-window-zone.
// Cube builds a rollup in the zone of the query, so a bucket in it is a local time already. The planner converts the bucket again in the filter of a window, with one offset for the
// whole range, so a window loses its first bucket west of UTC, and it does not hold the offset next to a clock change.
class CubeStoreFixes extends CubeStoreQuery {
  convertTz(field) {
    return field;
  }
}

// Cube refuses an unknown key in its options, so this exports only the options.
module.exports = {
  dialectFactory: ({ dbType }) => (dbType === 'clickhouse' ? ClickHouseFixes : undefined),
  externalDialectFactory: () => CubeStoreFixes,
};
