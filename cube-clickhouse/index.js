// A small ClickHouse dialect for stock Cube. It holds only the fixes that wait for a change in Cube. When Cube ships a fix,
// remove its override here. docs/cube-audit.md has the verdict and the repro of each defect.
const { ClickHouseQuery } = require('@cubejs-backend/schema-compiler/dist/src/adapter/ClickHouseQuery');
const { CubeStoreQuery } = require('@cubejs-backend/schema-compiler/dist/src/adapter/CubeStoreQuery');
const { PreAggregationLoader } = require('@cubejs-backend/query-orchestrator/dist/src/orchestrator/PreAggregationLoader');
const { QueryCache } = require('@cubejs-backend/query-orchestrator/dist/src/orchestrator/QueryCache');
const { CubeStoreDriver } = require('@cubejs-backend/cubestore-driver');
const { CubeSymbols } = require('@cubejs-backend/schema-compiler/dist/src/compiler/CubeSymbols');
const { CubeToMetaTransformer } = require('@cubejs-backend/schema-compiler/dist/src/compiler/CubeToMetaTransformer');
const { Transform, pipeline } = require('stream');

// Geo dimension of a view: https://github.com/cube-js/cube/pull/12537
// A view copies the type geo of a dimension and not its latitude and longitude,
// so each query on it fails with `Cannot read properties of undefined (reading 'sql')`. With another type, the planner reads the SQL of the view member,
// which is a reference to the dimension of the cube, and that one has both. /v1/meta keeps the type geo.
const geoOfViews = new WeakMap();
const generateIncludeMembers = CubeSymbols.prototype.generateIncludeMembers;
CubeSymbols.prototype.generateIncludeMembers = function generateIncludeMembersWithGeo(members, type, targetCube, ...rest) {
  const included = generateIncludeMembers.call(this, members, type, targetCube, ...rest);
  for (const [name, definition] of type === 'dimensions' ? included : []) {
    if (definition.type === 'geo') {
      definition.type = 'string';
      geoOfViews.set(this, (geoOfViews.get(this) || new Set()).add(`${targetCube.name}.${name}`));
    }
  }
  return included;
};
const transform = CubeToMetaTransformer.prototype.transform;
CubeToMetaTransformer.prototype.transform = function transformWithGeo(...args) {
  const result = transform.apply(this, args);
  const geo = geoOfViews.get(this.cubeEvaluator);
  for (const dimension of geo ? result.config.dimensions : []) {
    if (geo.has(dimension.name)) {
      dimension.type = 'geo';
    }
  }
  return result;
};

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

// Source rows of a rollup_lambda: no pull request yet, see cube-js/cube#12285. csvQuery gives a callback to stream.pipeline and awaits its result, which is the last stream
// and not a promise. So it closes the client while the rows still come, and the query fails with `Stream query failed: aborted`, or not, with the timing.
// This reads all rows first and gives them to the branch of csvQuery for a driver with no stream. Cube limits these rows with maxSourceRowLimit.
const csvQuery = QueryCache.prototype.csvQuery;
QueryCache.prototype.csvQuery = function csvQueryAfterTheRows(client, q) {
  if (!client.stream) {
    return csvQuery.call(this, client, q);
  }
  const downloadQueryResults = async (...args) => {
    const tableData = await client.stream(...args);
    try {
      const rows = [];
      for await (const row of tableData.rowStream) {
        rows.push(row);
      }
      return { rows };
    } finally {
      await tableData.release?.();
    }
  };
  return csvQuery.call(this, Object.create(client, { stream: { value: undefined }, downloadQueryResults: { value: downloadQueryResults } }), q);
};

const SERIES_ZONE = 'zone-of-the-query';

// Aggregate of no rows: no pull request yet. SQL gives NULL for sum, min and max of no rows, and Metabase on Postgres shows it. ClickHouse gives 0 and
// 1970-01-01, also for a measure with its own SQL, so only this setting of the query reaches each form. A readonly user cannot send a setting.
const { ClickHouseDriver } = require('@cubejs-backend/clickhouse-driver');
for (const method of ['queryResponse', 'stream']) {
  const original = ClickHouseDriver.prototype[method];
  ClickHouseDriver.prototype[method] = function withNullOnNoRows(...args) {
    if (!this.readOnlyMode) {
      this.config.clickhouseSettings.aggregate_functions_null_for_empty = 1;
    }
    return original.apply(this, args);
  };
}

// Empty text in a rollup: no pull request yet. Cube loads a rollup as CSV, and Cube Store reads an empty value as NULL, also a quoted one. So the group of empty text joins the group of NULL,
// and a filter on empty text finds no rows. Cube Store keeps empty text that comes with INSERT, so the rows with empty text go to the same table with INSERT after the load.
const importStream = CubeStoreDriver.prototype.importStream;
CubeStoreDriver.prototype.importStream = async function importStreamWithEmptyText(columns, tableData, table, indexes, aggregations, tracing) {
  const rows = [];
  const withoutEmptyText = new Transform({
    objectMode: true,
    transform(row, _encoding, callback) {
      if (columns.some((column) => row[column.name] === '')) {
        rows.push(row);
        callback();
      } else {
        callback(null, row);
      }
    },
  });
  // The caller reads the errors of this stream, so a failed source also fails the load.
  pipeline(tableData.rowStream, withoutEmptyText, () => {});
  await importStream.call(this, columns, { ...tableData, rowStream: withoutEmptyText }, table, indexes, aggregations, tracing);
  if (rows.length) {
    // importRows creates the table first. Here the table exists. It drops the table if an INSERT fails, so no rollup stays with a part of its rows.
    const toExistingTable = Object.create(this, { createTableWithOptions: { value: async () => undefined } });
    await toExistingTable.importRows(table, columns, indexes, aggregations, { rows }, tracing);
  }
};

// Time measure in a zone: no pull request yet. Cube makes each time dimension local to the zone of the query, and it leaves a measure of type time as the database prints it: `max(ts)` has the
// clock of the zone of the column, so UTC for a usual column. ClickHouse knows the type of the column here and Cube and the driver do not, so this reads the value as the zone of the query.
// A Date stays at midnight of its day, and a measure that reads convertTz itself keeps its value. The driver finds this in the SQL of /v1/sql, see probe/time-measure-in-zone?.
const measuresInZone = new WeakSet();
function readTimeMeasuresInZone(evaluator) {
  if (measuresInZone.has(evaluator)) {
    return;
  }
  measuresInZone.add(evaluator);
  for (const cube of Object.values(evaluator.evaluatedCubes)) {
    for (const measure of cube.isView ? [] : Object.values(cube.measures || {})) {
      if (measure.type === 'time' && typeof measure.sql === 'function') {
        // Cube reads the names of the arguments from the text of the function, so the new function needs them in its text.
        const names = evaluator.funcArguments(measure.sql);
        const params = names.includes('SQL_UTILS') ? names : [...names, 'SQL_UTILS'];
        // eslint-disable-next-line no-new-func
        measure.sql = new Function('original', `return (${params.join(', ')}) => SQL_UTILS.convertTz(original(${names.join(', ')}));`)(measure.sql);
      }
    }
  }
}

class ClickHouseFixes extends ClickHouseQuery {
  constructor(compilers, options) {
    readTimeMeasuresInZone(compilers.cubeEvaluator);
    super(compilers, options);
  }

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
    // percentile: no pull request yet, see docs/cube-findings.md#percentile. Stock Cube removes this function for ClickHouse, so a median and a
    // percentile fail with `Can't detect Cube query`. quantileExactInclusive is the linear form of PERCENTILE_CONT, and quantile is an estimate.
    templates.functions.PERCENTILECONT = 'quantileExactInclusive({{ args[0] }})';
    templates.expressions.within_group = "{{ fun_sql }}(toFloat64({{ within_group_concat | replace(' ASC NULLS LAST', '') }}))";
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
