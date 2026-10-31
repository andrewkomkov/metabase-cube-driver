const { ClickHouseQuery } = require('@cubejs-backend/schema-compiler/dist/src/adapter/ClickHouseQuery');

class FixedClickHouseQuery extends ClickHouseQuery {
  // Keeps milliseconds in a time parameter. The strict bound of the SQL API is one millisecond later.
  dateTimeCast(value, timezone) {
    if (timezone) {
      return super.dateTimeCast(value, timezone);
    }
    return `parseDateTime64BestEffort(${value}, 3, 'UTC')`;
  }

  // Keeps milliseconds in a selected time.
  convertTz(field) {
    return `toTimeZone(toDateTime64(${field}, 3), '${this.timezone}')`;
  }

  // An original_sql table keeps the column names of the cube sql, so a member alias does not exist there.
  preAggregationLoadSql(cube, preAggregation, tableName) {
    if (preAggregation.type === 'originalSql') {
      const [sql, params] = this.preAggregationSql(cube, preAggregation);
      return [`CREATE TABLE ${tableName} ENGINE = MergeTree() ORDER BY tuple() AS ${sql}`, params];
    }
    return super.preAggregationLoadSql(cube, preAggregation, tableName);
  }

  indexSql(cube, preAggregation, index, indexName, tableName) {
    if (preAggregation.type === 'originalSql') {
      return ['SELECT 1', []];
    }
    return super.indexSql(cube, preAggregation, index, indexName, tableName);
  }

  sqlTemplates() {
    const templates = super.sqlTemplates();
    templates.expressions.interval_single_date_part = 'INTERVAL {{ num }} {{ date_part }}';
    templates.expressions.extract = 'EXTRACT({{ date_part }} FROM {{ expr }})';
    templates.functions.PERCENTILECONT = 'quantile({{ args[0] }})';
    templates.expressions.within_group = "{{ fun_sql }}({{ within_group_concat | replace(' ASC NULLS LAST', '') }})";
    return templates;
  }
}

module.exports = {
  dialectFactory: ({ dbType }) => (dbType === 'clickhouse' ? FixedClickHouseQuery : undefined),
};
