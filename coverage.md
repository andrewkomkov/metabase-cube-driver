# Coverage

CI generates this page from `conformance/run.py`. Do not edit it.

**coverage: 314/405 pass** (314 pass, 0 partial, 23 blocked, 6 unsupported, 62 fail of 405)

Metabase `v0.64.3`, Cube `v1.8.15`, ClickHouse `26.8.36-alpine`, commit `a13fc93507f4`, [run](https://github.com/andrewkomkov/metabase-cube-driver/actions/runs/41071291793), 2026-10-30.

| Feature | Status | Reason |
| --- | --- | --- |
| `measure.format.abbr` | fail | column has no format settings |
| `measure.format.accounting` | fail | column settings {}: want decimals 2 |
| `measure.format.currency` | pass |  |
| `measure.format.currency_code` | fail | column settings {}, semantic type/Currency: want number_style currency |
| `measure.format.d3` | fail | column has no format settings |
| `measure.format.number_n` | fail | column settings {}: want decimals 0 |
| `measure.format.percent` | pass |  |
| `measure.format.percent_n` | fail | column settings {}, semantic None: want number_style percent |
| `measure.multi_stage.case_switch` | pass |  |
| `measure.multi_stage.filter_exclude` | pass |  |
| `measure.multi_stage.grain_exclude` | pass |  |
| `measure.multi_stage.grain_include` | pass |  |
| `measure.multi_stage.grain_keep_only` | pass |  |
| `measure.multi_stage.group_by_legacy` | pass |  |
| `measure.multi_stage.share_of_total` | pass |  |
| `measure.multi_stage.time_shift_named_calendar` | pass |  |
| `measure.multi_stage.time_shift_next` | pass |  |
| `measure.multi_stage.time_shift_prior` | pass |  |
| `measure.opt.cross_cube_reference` | pass |  |
| `measure.opt.description` | pass |  |
| `measure.opt.drill_members` | fail | metadata of orders has no text 'drill' |
| `measure.opt.filters` | pass |  |
| `measure.opt.filters_on_count` | pass |  |
| `measure.opt.mask` | pass |  |
| `measure.opt.meta` | fail | metadata of orders has no text 'Sum of order amount.' |
| `measure.opt.multi_stage` | pass |  |
| `measure.opt.public_false` | pass |  |
| `measure.opt.rolling_type_deprecated` | unsupported | year_to_date, quarter_to_date and month_to_date are deprecated and Tesseract does not read them. The demo model uses to_date. |
| `measure.opt.time_shift` | pass |  |
| `measure.opt.title` | pass |  |
| `measure.rolling.leading_offset` | pass |  |
| `measure.rolling.to_date_month` | pass |  |
| `measure.rolling.to_date_year` | pass |  |
| `measure.rolling.trailing` | pass |  |
| `measure.rolling.unbounded` | fail | POST /api/dataset: HTTP 400: {"database_id":2,"parameterized":false,"started_at":"2026-10-30T14:27:43.397221Z","via":[{"status":"failed","class":"class clojure.lang.ExceptionInfo","error":"Error executing query: ERROR: Database Execution Error: Query failed: Error: Cannot determine join keys in JOIN ON expression lessOrEquals(_ |
| `measure.type.avg` | pass |  |
| `measure.type.boolean` | pass |  |
| `measure.type.count` | pass |  |
| `measure.type.count_distinct` | pass |  |
| `measure.type.count_distinct_approx` | pass |  |
| `measure.type.max` | pass |  |
| `measure.type.min` | pass |  |
| `measure.type.number` | pass |  |
| `measure.type.number_agg` | pass |  |
| `measure.type.rank` | pass |  |
| `measure.type.string` | pass |  |
| `measure.type.sum` | pass |  |
| `measure.type.time` | fail | rows: got [None], truth ['2025-01-01T01:00:00'] |
| `dimension.format.abbr` | fail | orders.amount_abbr has no format settings |
| `dimension.format.currency` | pass |  |
| `dimension.format.currency_code` | fail | orders.amount_eur settings {}, want currency EUR |
| `dimension.format.id` | fail | orders.order_ref has no format settings |
| `dimension.format.image_url` | pass |  |
| `dimension.format.link` | pass |  |
| `dimension.format.link_label` | fail | metadata of orders has no text 'Open invoice' |
| `dimension.format.percent` | fail | orders.amount_pct has no format settings |
| `dimension.format.time` | fail | metadata of orders has no text '%Y-%m-%d %H:%M:%S' |
| `dimension.opt.case_label_sql` | pass |  |
| `dimension.opt.case_sql` | pass |  |
| `dimension.opt.description` | pass |  |
| `dimension.opt.links` | fail | metadata of orders has no text 'Search status' |
| `dimension.opt.mask_sql` | pass |  |
| `dimension.opt.mask_static` | pass |  |
| `dimension.opt.meta` | fail | metadata of orders has no text 'Use for order lifecycle questions.' |
| `dimension.opt.order` | blocked | A Metabase field has no default sort order. The driver cannot apply this option without a Metabase change, which is out of scope. |
| `dimension.opt.primary_key` | pass |  |
| `dimension.opt.propagate_filters` | pass |  |
| `dimension.opt.public_false` | pass |  |
| `dimension.opt.sub_query` | pass |  |
| `dimension.opt.synthetic_hidden` | fail | orders.status_linked___link_search_url is visible |
| `dimension.opt.time_shift_calendar` | pass |  |
| `dimension.opt.title` | pass |  |
| `dimension.type.boolean` | pass |  |
| `dimension.type.geo` | fail | users has no fields with Latitude and Longitude semantic types |
| `dimension.type.number` | pass |  |
| `dimension.type.string` | pass |  |
| `dimension.type.switch` | pass |  |
| `dimension.type.time` | pass |  |
| `granularity.custom.interval_hours` | fail | rows: got ['2025-01-01T00:00:00', 13.0], truth ['2025-01-01T00:00:00', 7.0] |
| `granularity.custom.negative_offset` | pass |  |
| `granularity.custom.offset` | fail | rows: got ['2025-02-01T00:00:00', 825.0], truth ['2024-11-01T00:00:00', 433.0] |
| `granularity.custom.origin` | pass |  |
| `granularity.custom.title` | pass |  |
| `granularity.proxy_default` | pass |  |
| `segment.combined` | pass |  |
| `segment.description` | pass |  |
| `segment.filter` | pass |  |
| `segment.second` | pass |  |
| `segment.title` | pass |  |
| `segment.with_breakout` | pass |  |
| `join.alias` | pass |  |
| `join.fan_trap_avg` | pass |  |
| `join.many_to_one` | pass |  |
| `join.one_to_many_child_measure` | pass |  |
| `join.one_to_many_no_fanout` | pass |  |
| `join.one_to_one` | pass |  |
| `join.sql_cube_join_field` | pass |  |
| `join.transitive` | pass |  |
| `cube.calendar` | pass |  |
| `cube.calendar_granularity_sql` | blocked | Needs a calendar table with SQL granularities in the demo model. Not modelled yet. |
| `cube.data_source` | blocked | The demo stack has one data source. A second one needs a compose change and a Cube config, which do not exist yet. |
| `cube.description` | pass |  |
| `cube.extends` | pass |  |
| `cube.meta` | fail | metadata of users has no text 'One row per customer account.' |
| `cube.public_false` | pass |  |
| `cube.refresh_key` | pass |  |
| `cube.sql` | pass |  |
| `cube.sql_alias` | pass |  |
| `cube.sql_table` | pass |  |
| `cube.title` | pass |  |
| `context.compile_context` | blocked | Needs a security context. See security.compile_context. |
| `context.filter_group` | blocked | FILTER_GROUP is not in the demo model yet. |
| `context.filter_params` | pass |  |
| `context.sql_utils_convert_tz` | pass |  |
| `security.access_policy_groups` | blocked | Group policies need groups in the security context, so they need a cube.js. Policies for all groups are checked in view.access_*. |
| `security.compile_context` | blocked | Needs a cube.js with contextToAppId and a mount in dev/docker-compose.yml. The stack mounts only the model. |
| `security.security_context_row_filter` | blocked | Needs a cube.js with checkSqlAuth and a mount in dev/docker-compose.yml. The driver must also pass a Metabase user attribute to Cube. |
| `security.sql_user_impersonation` | blocked | Needs CUBEJS_SQL_SUPER_USER and a virtual __user filter. The compose file sets no super user. |
| `view.access_member_level` | pass |  |
| `view.access_row_level` | pass |  |
| `view.basic` | pass |  |
| `view.default_filters` | pass |  |
| `view.derived_measure` | pass |  |
| `view.description` | pass |  |
| `view.excludes` | pass |  |
| `view.extends` | pass |  |
| `view.folders` | fail | metadata of sales has no text 'Money' |
| `view.folders_nested` | fail | metadata of paid_sales has no text 'Ratios' |
| `view.group` | fail | metadata of paid_sales has no text 'Revenue views' |
| `view.includes` | pass |  |
| `view.member_alias` | pass |  |
| `view.member_description` | pass |  |
| `view.member_format` | pass |  |
| `view.member_meta` | fail | metadata of paid_sales has no text 'curated' |
| `view.member_title` | pass |  |
| `view.meta` | fail | metadata of sales has no text 'Preferred entry point for agents.' |
| `view.meta_auto_run` | unsupported | meta.auto_run controls Cube Cloud workbooks. Metabase has no such behaviour. |
| `view.meta_default_ui_filters` | unsupported | meta.default_ui_filters controls Cube Cloud workbooks. Metabase has no such behaviour. |
| `view.prefix` | pass |  |
| `view.public_false` | pass |  |
| `view.title` | pass |  |
| `hierarchy.levels` | fail | metadata of orders has no text 'Fulfilment' |
| `hierarchy.public_false` | pass |  |
| `preagg.indexes` | pass |  |
| `preagg.original_sql` | fail | POST /api/dataset: HTTP 400: {"database_id":2,"parameterized":false,"started_at":"2026-10-30T14:28:13.018829923Z","via":[{"status":"failed","class":"class clojure.lang.ExceptionInfo","error":"Error executing query: ERROR: Database Execution Error: Command failed: Error: Missing columns: 'lambda_orders__status' while processing: |
| `preagg.partitioned` | pass |  |
| `preagg.rollup` | pass |  |
| `preagg.rollup_join` | blocked | rollup_join needs two data sources. The demo stack has one. |
| `preagg.rollup_lambda` | blocked | rollup_lambda needs streaming or partitioned source data. Not modelled yet. |
| `sqlapi.aggregate_function` | pass |  |
| `sqlapi.date_trunc` | pass |  |
| `sqlapi.having` | pass |  |
| `sqlapi.measure_function` | pass |  |
| `sqlapi.order_limit` | pass |  |
| `sqlapi.post_processing` | pass |  |
| `sqlapi.pushdown_window` | pass |  |
| `sqlapi.segment_in_where` | pass |  |
| `sqlapi.ungrouped` | pass |  |
| `sqlapi.union` | pass |  |
| `sqlapi.where_between` | pass |  |
| `sqlapi.where_in` | pass |  |
| `sqlapi.where_is_null` | pass |  |
| `sqlapi.where_like` | pass |  |
| `restapi.compare_date_range` | blocked | compareDateRange exists only in the REST API. No Metabase query shape maps to it. |
| `restapi.total_query` | blocked | A total query exists only in the REST API. No Metabase query shape maps to it. |
| `clickhouse.bool` | pass |  |
| `clickhouse.datetime_utc` | pass |  |
| `clickhouse.decimal` | pass |  |
| `clickhouse.function_in_measure` | pass |  |
| `clickhouse.large_result` | pass |  |
| `clickhouse.low_cardinality` | pass |  |
| `clickhouse.nullable_expression` | pass |  |
| `meta.base_types` | pass |  |
| `meta.fingerprint_distinct_count` | fail | rows: got [None], truth [4.0] |
| `meta.fk_implicit_target` | fail | no foreign key from orders to users |
| `meta.measure_is_marked` | pass |  |
| `meta.no_internal_columns` | pass |  |
| `meta.resync_keeps_metadata` | pass |  |
| `meta.semantic_types` | fail | users.name semantic_type None, want type/Name |
| `meta.tables_match_public_members` | pass |  |
| `mb.agg.avg_field` | pass |  |
| `mb.agg.count_rows` | pass |  |
| `mb.agg.count_where` | pass |  |
| `mb.agg.cum_count` | pass |  |
| `mb.agg.cum_sum` | pass |  |
| `mb.agg.custom_expression` | pass |  |
| `mb.agg.distinct` | pass |  |
| `mb.agg.max_field` | pass |  |
| `mb.agg.median` | fail | POST /api/dataset: HTTP 400: {"database_id":2,"parameterized":false,"started_at":"2026-10-30T14:28:29.339931312Z","via":[{"status":"failed","class":"class clojure.lang.ExceptionInfo","error":"Error executing query: ERROR: Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional in |
| `mb.agg.min_field` | pass |  |
| `mb.agg.offset_window` | fail | rows: got ['2025-01-01T00:00:00', 0.0], truth ['2025-01-01T00:00:00', None] |
| `mb.agg.percentile` | fail | POST /api/dataset: HTTP 400: {"database_id":2,"parameterized":false,"started_at":"2026-10-30T14:28:30.287777764Z","via":[{"status":"failed","class":"class clojure.lang.ExceptionInfo","error":"Error executing query: ERROR: Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional in |
| `mb.agg.share` | pass |  |
| `mb.agg.stddev` | pass |  |
| `mb.agg.sum_field` | pass |  |
| `mb.agg.sum_where` | pass |  |
| `mb.agg.two_measures` | pass |  |
| `mb.agg.variance` | pass |  |
| `mb.bucket.day` | pass |  |
| `mb.bucket.day-of-month` | pass |  |
| `mb.bucket.day-of-week` | pass |  |
| `mb.bucket.day-of-year` | pass |  |
| `mb.bucket.hour` | pass |  |
| `mb.bucket.hour-of-day` | pass |  |
| `mb.bucket.minute` | pass |  |
| `mb.bucket.minute-of-hour` | pass |  |
| `mb.bucket.month` | pass |  |
| `mb.bucket.month-of-year` | pass |  |
| `mb.bucket.quarter` | pass |  |
| `mb.bucket.quarter-of-year` | pass |  |
| `mb.bucket.week` | fail | rows: got ['2024-12-30T00:00:00', 69.0], truth ['2024-12-29T00:00:00', 55.0] |
| `mb.bucket.week-of-year` | fail | rows: got [1.0, 98.0], truth [1.0, 55.0] |
| `mb.bucket.year` | pass |  |
| `mb.breakout.bin_count` | fail | POST /api/dataset: HTTP 400: {"database_id":2,"parameterized":false,"started_at":"2026-10-30T14:28:36.846025756Z","via":[{"status":"failed","class":"class clojure.lang.ExceptionInfo","error":"Error executing query: ERROR: Rewrite Error: Schema contains duplicate unqualified field name 'Float64(20)'. Please check logs for additi |
| `mb.breakout.bin_width` | fail | POST /api/dataset: HTTP 400: {"database_id":2,"parameterized":false,"started_at":"2026-10-30T14:28:37.085265019Z","via":[{"status":"failed","class":"class clojure.lang.ExceptionInfo","error":"Error executing query: ERROR: Rewrite Error: Schema contains duplicate unqualified field name 'Float64(100)'. Please check logs for addit |
| `mb.breakout.boolean` | pass |  |
| `mb.breakout.limit` | pass |  |
| `mb.breakout.sorted_by_measure` | pass |  |
| `mb.breakout.two` | pass |  |
| `mb.filter.and` | pass |  |
| `mb.filter.between_number` | pass |  |
| `mb.filter.boolean_false` | pass |  |
| `mb.filter.boolean_true` | pass |  |
| `mb.filter.contains` | pass |  |
| `mb.filter.contains_ci` | pass |  |
| `mb.filter.date_after` | pass |  |
| `mb.filter.date_before` | pass |  |
| `mb.filter.date_between` | pass |  |
| `mb.filter.date_month` | pass |  |
| `mb.filter.date_on` | fail | rows: got [None], truth [14.0] |
| `mb.filter.ends_with` | pass |  |
| `mb.filter.eq_multi` | pass |  |
| `mb.filter.eq_number` | pass |  |
| `mb.filter.eq_string` | pass |  |
| `mb.filter.exclude_day_of_week` | fail | POST /api/dataset: HTTP 400: {"database_id":2,"parameterized":false,"started_at":"2026-10-30T14:28:43.677069308Z","via":[{"status":"failed","class":"class clojure.lang.ExceptionInfo","error":"Error executing query: ERROR: Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional in |
| `mb.filter.exclude_month_of_year` | fail | POST /api/dataset: HTTP 400: {"database_id":2,"parameterized":false,"started_at":"2026-10-30T14:28:43.917837713Z","via":[{"status":"failed","class":"class clojure.lang.ExceptionInfo","error":"Error executing query: ERROR: Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional in |
| `mb.filter.gt` | pass |  |
| `mb.filter.gte` | pass |  |
| `mb.filter.is_empty` | pass |  |
| `mb.filter.is_null` | pass |  |
| `mb.filter.lt` | pass |  |
| `mb.filter.lte` | pass |  |
| `mb.filter.neq_number` | pass |  |
| `mb.filter.neq_string` | pass |  |
| `mb.filter.not` | pass |  |
| `mb.filter.not_contains` | pass |  |
| `mb.filter.not_empty` | pass |  |
| `mb.filter.not_null` | pass |  |
| `mb.filter.or` | pass |  |
| `mb.filter.relative_current` | fail | rows: got [None], truth [0.0] |
| `mb.filter.relative_past` | pass |  |
| `mb.filter.starts_with` | pass |  |
| `mb.filter.starts_with_ci` | pass |  |
| `mb.raw.fields_sorted` | pass |  |
| `mb.raw.sort_desc` | pass |  |
| `mb.expr.abs` | pass |  |
| `mb.expr.add` | pass |  |
| `mb.expr.case` | pass |  |
| `mb.expr.ceil` | pass |  |
| `mb.expr.coalesce` | pass |  |
| `mb.expr.concat` | pass |  |
| `mb.expr.datetime_add` | pass |  |
| `mb.expr.datetime_diff` | fail | POST /api/dataset: HTTP 400: {"database_id":2,"parameterized":false,"started_at":"2026-10-30T14:28:52.783479305Z","via":[{"status":"failed","class":"class clojure.lang.ExceptionInfo","error":"Error executing query: ERROR: Internal Error: Can't generate SQL for interval: Internal Error: Error getting expressions/interval_single_ |
| `mb.expr.div` | pass |  |
| `mb.expr.floor` | pass |  |
| `mb.expr.get_month` | pass |  |
| `mb.expr.get_year` | pass |  |
| `mb.expr.length` | pass |  |
| `mb.expr.log` | fail | POST /api/dataset: HTTP 400: {"database_id":2,"parameterized":false,"started_at":"2026-10-30T14:28:54.6091286Z","via":[{"status":"failed","class":"class clojure.lang.ExceptionInfo","error":"Error executing query: ERROR: Planning Error: Initial planning error: Error during planning: Coercion from [Int64, Float64] to the signatur |
| `mb.expr.lower` | pass |  |
| `mb.expr.mul` | pass |  |
| `mb.expr.power` | pass |  |
| `mb.expr.replace` | pass |  |
| `mb.expr.round` | pass |  |
| `mb.expr.sqrt` | pass |  |
| `mb.expr.substring` | pass |  |
| `mb.expr.upper` | pass |  |
| `mb.join.explicit_inner` | fail | POST /api/dataset: HTTP 400: {"database_id":2,"parameterized":false,"started_at":"2026-10-30T14:28:57.539328095Z","via":[{"status":"failed","class":"class clojure.lang.ExceptionInfo","error":"Error executing query: ERROR: Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional in |
| `mb.join.explicit_left` | fail | POST /api/dataset: HTTP 400: {"database_id":2,"parameterized":false,"started_at":"2026-10-30T14:28:57.803543542Z","via":[{"status":"failed","class":"class clojure.lang.ExceptionInfo","error":"Error executing query: ERROR: Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional in |
| `mb.join.implicit` | fail | no foreign key from orders to users |
| `mb.join.implicit_filter` | fail | no foreign key from orders to users |
| `mb.join.measure_across_cubes` | fail | POST /api/dataset: HTTP 400: {"database_id":2,"parameterized":false,"started_at":"2026-10-30T14:28:58.045077672Z","via":[{"status":"failed","class":"class clojure.lang.ExceptionInfo","error":"Error executing query: ERROR: Rewrite Error: Can't detect Cube query and it may be not supported yet. Please check logs for additional in |
| `mb.nested.filter_on_aggregation` | pass |  |
| `mb.nested.question_as_source` | pass |  |
| `mb.card.metric` | pass |  |
| `mb.card.metric_with_breakout` | fail | rows: got ['cancelled', None], truth ['cancelled', 69970.0] |
| `mb.card.model` | pass |  |
| `mb.card.saved_question` | pass |  |
| `mb.entity.measure_custom` | pass |  |
| `mb.entity.segment_custom` | pass |  |
| `mb.export.csv` | pass |  |
| `mb.export.json` | pass |  |
| `mb.native.field_filter` | pass |  |
| `mb.native.field_filter_date` | pass |  |
| `mb.native.optional_clause` | pass |  |
| `mb.native.param_date` | pass |  |
| `mb.native.param_number` | pass |  |
| `mb.native.param_text` | pass |  |
| `mb.native.plain` | pass |  |
| `mb.native.saved_card_param` | pass |  |
| `mb.dashboard.filter_date` | pass |  |
| `mb.dashboard.filter_number` | pass |  |
| `mb.dashboard.filter_search` | pass |  |
| `mb.dashboard.filter_string` | pass |  |
| `mb.dashboard.filter_values` | pass |  |
| `mb.dashboard.linked_filter` | pass |  |
| `mb.dashboard.native_field_filter` | pass |  |
| `mb.field_values.boolean` | pass |  |
| `mb.field_values.list` | pass |  |
| `mb.field_values.search` | pass |  |
| `mb.search.by_description` | pass |  |
| `mb.search.measure` | fail | search has no measure Revenue. Found: [] |
| `mb.search.segment` | fail | search has no segment Paid. Found: [] |
| `mb.search.table` | pass |  |
| `mb.xray.candidates` | pass |  |
| `mb.xray.table` | pass |  |
| `mb.xray.view` | pass |  |
| `mb.report_timezone` | fail | rows: got 1 rows, truth has 2. got=[['2025-01-01T00:00:00', 5000.0]] truth=[['2025-01-01T00:00:00', 4996.0], ['2026-01-01T00:00:00', 4.0]] |
| `mb.other.actions` | unsupported | Metabase actions write data. The Cube SQL API is read only. |
| `mb.other.drill_through_ui` | blocked | Drill-through runs in the browser. The suite has no browser check yet. |
| `mb.other.model_persistence` | unsupported | Model persistence needs CREATE TABLE. The Cube SQL API is read only. |
| `mb.other.uploads` | unsupported | CSV uploads write tables. The Cube SQL API is read only. |
| `mcp.agent_journey_no_cube_knowledge` | fail | no table from the search lists a revenue measure |
| `mcp.alert_write` | blocked | Needs an email or Slack channel. The demo stack has neither. |
| `mcp.bookmark_content` | blocked | A bookmark holds no Cube value that a check can compare with the truth. |
| `mcp.browse.descriptions_in_get_fields` | fail | get_fields does not show 'Revenue questions start here.' |
| `mcp.browse.get_fields_dimensions` | pass |  |
| `mcp.browse.get_fields_measures` | pass |  |
| `mcp.browse.get_fields_segments` | fail | get_fields lists no segment Paid. It lists: ['conformanceshipped'] |
| `mcp.browse.list_databases` | pass |  |
| `mcp.browse.list_tables` | pass |  |
| `mcp.collection_write` | blocked | A collection holds no Cube value that a check can compare with the truth. |
| `mcp.connect_oauth` | pass |  |
| `mcp.dashboard_write` | pass |  |
| `mcp.dashboard_write.create_and_filter` | pass |  |
| `mcp.document_write` | blocked | A document holds no Cube value that a check can compare with the truth. |
| `mcp.duplicate_content` | pass |  |
| `mcp.execute_query` | pass |  |
| `mcp.execute_query.breakout` | pass |  |
| `mcp.execute_query.date_bucket` | pass |  |
| `mcp.execute_query.filter` | pass |  |
| `mcp.execute_query.implicit_join` | fail | no foreign key from orders to users |
| `mcp.execute_query.measure` | pass |  |
| `mcp.execute_query.paging` | fail | no next_cursor: {'query_handle': '2d849f84-9329-450e-81d5-ab61a42e1e9c', 'returned': 3, 'truncated': True, 'cols': [{'name': 'id', 'base_type': 'type/Float', 'display_name': 'Order ID', 'effective_type': 'type/Float' |
| `mcp.execute_query.segment` | pass |  |
| `mcp.execute_query.view` | pass |  |
| `mcp.execute_sql` | pass |  |
| `mcp.get_content` | pass |  |
| `mcp.get_content.question` | pass |  |
| `mcp.get_parameter_values` | pass |  |
| `mcp.get_parameter_values.dashboard` | pass |  |
| `mcp.get_parameter_values.question` | pass |  |
| `mcp.get_parameter_values.search` | pass |  |
| `mcp.glossary` | blocked | The glossary holds no Cube value that a check can compare with the truth. |
| `mcp.learn` | blocked | Skill text holds no Cube value that a check can compare with the truth. |
| `mcp.measure_write` | pass |  |
| `mcp.metric_write` | pass |  |
| `mcp.question_write` | pass |  |
| `mcp.question_write.native` | pass |  |
| `mcp.question_write.query` | pass |  |
| `mcp.question_write.query_handle` | pass |  |
| `mcp.render_drill_through` | blocked | The tools/list reply for a session without MCP Apps support does not include this tool. |
| `mcp.run_saved_question` | pass |  |
| `mcp.search` | pass |  |
| `mcp.search.measure` | fail | search has no measure Revenue: [] |
| `mcp.search.segment` | fail | search has no segment Paid: [] |
| `mcp.search.table_by_description` | pass |  |
| `mcp.search.view_description` | pass |  |
| `mcp.segment_write` | pass |  |
| `mcp.subscription_write` | blocked | Needs an email or Slack channel. The demo stack has neither. |
| `mcp.visualize_query` | blocked | The tools/list reply for a session without MCP Apps support does not include this tool. |
| `inherit.cube.members` | pass |  |
| `inherit.cube.override_measure` | pass |  |
| `inherit.cube.override_dimension` | pass |  |
| `inherit.cube.add_measure` | pass |  |
| `inherit.cube.add_dimension` | pass |  |
| `inherit.multi_level.rows` | pass |  |
| `inherit.multi_level.add_measure` | pass |  |
| `inherit.multi_level.override` | pass |  |
| `inherit.abstract_base` | pass |  |
| `inherit.join` | pass |  |
| `inherit.segment` | pass |  |
| `inherit.pre_aggregation` | pass |  |
| `inherit.view.members` | pass |  |
| `inherit.view.adds` | pass |  |
| `inherit.view.multi_level` | pass |  |
| `view.includes_star` | pass |  |
| `view.excludes_star` | pass |  |
| `view.public_true` | pass |  |
| `view.join_path_transitive` | pass |  |
| `view.several_paths` | pass |  |
| `view.same_name_two_cubes` | pass |  |
| `view.on_extended_cube` | pass |  |
| `view.prefix_sync` | pass |  |
| `view.member_alias_sync` | pass |  |
| `cube.sql_sync` | pass |  |
| `cube.sql_alias_sync` | pass |  |
| `cube.data_source_default` | pass |  |
| `cube.polymorphic_first` | pass |  |
| `cube.polymorphic_second` | pass |  |
| `cube.polymorphic_base` | pass |  |
| `cube.generated_jinja` | pass |  |
| `cube.generated_jinja_second` | pass |  |
| `cube.generated_async` | pass |  |
| `cube.generated_async_second` | pass |  |
