(ns metabase.driver.cube
  "Metabase driver for Cube Core. Queries go through the Cube SQL API, which speaks the Postgres protocol. The data
  model comes from the Cube REST API, because pg_catalog on the SQL API has no titles, descriptions or member kinds."
  (:require
   [clojure.string :as str]
   [clojure.walk :as walk]
   [metabase.driver :as driver]
   [metabase.driver-api.core :as driver-api]
   [metabase.driver.cube.identity :as cube.identity]
   [metabase.driver.cube.meta :as cube.meta]
   [metabase.driver.cube.native :as cube.native]
   [metabase.driver.cube.probe :as cube.probe]
   [metabase.driver.cube.semantic :as cube.semantic]
   [metabase.driver.postgres]
   [metabase.driver.sql-jdbc.connection :as sql-jdbc.conn]
   [metabase.driver.sql-jdbc.execute :as sql-jdbc.execute]
   [metabase.driver.sql.query-processor :as sql.qp]
   [metabase.query-processor.middleware.cache :as qp.middleware.cache]
   [metabase.query-processor.middleware.limit :as qp.limit]
   [metabase.query-processor.middleware.row-restricted-metadata :as qp.row-restricted-metadata]
   [metabase.util.honey-sql-2 :as h2x]
   [metabase.util.log :as log]
   [toucan2.core :as t2])
  (:import
   (java.sql ResultSet ResultSetMetaData Types)
   (java.time DayOfWeek Instant LocalDate LocalDateTime OffsetDateTime ZoneId ZoneOffset ZonedDateTime)
   (java.time.format DateTimeFormatter)
   (java.time.temporal ChronoUnit TemporalAdjusters)))

(set! *warn-on-reflection* true)

(driver/register! :cube, :parent :postgres)

(defmethod driver/display-name :cube [_] "Cube")

;; A Cube deployment is read-only and has no DDL. Joins, binning and other query features are not listed here.
;; Their real status is in conformance/features.yaml.
(doseq [feature [:atomic-renames
                 :connection-impersonation
                 :convert-timezone
                 :database-routing
                 :datetime-diff
                 :describe-default-expr
                 :describe-indexes
                 :describe-is-generated
                 :describe-is-nullable
                 ;; Cube joins through the data model, and its joins are LEFT from the cube that the model starts at.
                 :full-join
                 :right-join
                 :index/fetch
                 :index/standalone-create
                 :metadata/table-existence-check
                 :native-pivot-tables
                 :nested-field-columns
                 :persist-models
                 :rename
                 :transforms/index-ddl
                 :transforms/python
                 :transforms/table
                 :uploads
                 :uuid-type]]
  (defmethod driver/database-supports? [:cube feature] [_driver _feature _db] false))

;; Stock Cube has no PERCENTILE_CONT for ClickHouse, and Cube with the ClickHouse dialect or on another data source has it. With no database to ask, false.
(defmethod driver/database-supports? [:cube :percentile-aggregations]
  [_driver _feature database]
  (boolean (some-> database :details not-empty cube.probe/percentiles?)))

;; Stock Cube has no template for SPLIT_PART on ClickHouse, and the ClickHouse dialect adds one. With no database to ask, false.
(defmethod driver/database-supports? [:cube :split-part]
  [_driver _feature database]
  (boolean (some-> database :details not-empty cube.probe/split-part?)))

;; ClickHouse reads a pattern with RE2, which has no lookahead and no lookbehind.
(defmethod driver/database-supports? [:cube :regex/lookaheads-and-lookbehinds] [_driver _feature _db] false)

;;; ------------------------------------------------- connection -------------------------------------------------

(defmethod sql-jdbc.conn/connection-details->spec :cube
  [driver details]
  (let [parent (get-method sql-jdbc.conn/connection-details->spec :postgres)]
    (parent driver (-> (apply dissoc details (filter #(str/starts-with? (name %) "cube-") (keys details)))
                       (update :port #(or % 15432))))))

;; With `cube-user-attribute`, each query has its own connection and the person is the user name. See
;; metabase.driver.cube.identity. A raw JDBC spec does not go through the pool of the database.
(defn- connection-details
  [db-or-id-or-spec]
  (if (integer? db-or-id-or-spec)
    (driver-api/with-metadata-provider db-or-id-or-spec
      (:details (driver-api/database (driver-api/metadata-provider))))
    (:details db-or-id-or-spec)))

(defmethod sql-jdbc.execute/do-with-connection-with-options :cube
  [driver db-or-id-or-spec options f]
  (let [parent  (get-method sql-jdbc.execute/do-with-connection-with-options :postgres)
        details (connection-details db-or-id-or-spec)
        ;; A pool can give this connection to a query with no report time zone, so each query sets one.
        options (update options :session-timezone #(or % "UTC"))]
    (if-let [sql-user (some-> details cube.identity/sql-user)]
      (parent driver (sql-jdbc.conn/connection-details->spec driver (assoc details :user sql-user)) options f)
      (parent driver db-or-id-or-spec options f))))

;; The Metabase result cache has one entry per query, with no person in the key. A live test showed the rows of one person
;; to another person. A driver has no hook for this: the cache middleware runs before the driver, and the key comes from the
;; query. So this wraps the middleware. It skips the cache only for a Cube database with per-person data, and each other
;; database goes through the middleware as before, see isolation.other_driver_cache. Metabase rebuilds the query processor
;; when this var changes. If a new Metabase has no such var, the driver does not load, which is the safe result.
(defn- per-person-database?
  [database]
  (and (= :cube (:engine database))
       (some? (cube.identity/attribute (:details database)))))

(defn- per-person-query?
  []
  (per-person-database? (driver-api/database (driver-api/metadata-provider))))

(defn- skip-cache-for-per-person-data
  [middleware]
  (fn [qp]
    (let [with-cache (middleware qp)]
      (fn [query rff]
        (if (and (:cache-strategy query) (per-person-query?))
          (qp query rff)
          (with-cache query rff))))))

(defonce cache-wrapped
  (do (alter-var-root #'qp.middleware.cache/maybe-return-cached-results skip-cache-for-per-person-data)
      true))

;; Metabase stores a fingerprint of the result of a saved question or a model: min, max, quartiles, average and distinct
;; counts. Each reader of the card gets it with no run, through GET /api/card/:id. On per-person data, it holds values from
;; the rows of the person who ran the card last, so a live test showed the rows of one person to another. Metabase removes
;; these fingerprints when a run is row-restricted: a sandbox, impersonation or database routing. A driver has no hook for
;; this, so this wrap makes a query on a Cube database with per-person data row-restricted. Metabase then stores no
;; metadata from such a run, and it returns none for a save. Each other database gets the original answer, see
;; isolation.other_db_card_fingerprint. cube.semantic/clear-result-fingerprints-of-person-data! removes the fingerprints
;; that a card kept from before.
(defn- per-person-rows?
  "True for a query on a Cube database with per-person data. With no database to read, true: a run then keeps no
  fingerprint, which loses nothing but statistics."
  [query]
  (try
    (per-person-database? (driver-api/database (or (:lib/metadata query) (driver-api/metadata-provider))))
    (catch Exception _ true)))

(defn- row-restricted-on-per-person-data
  [row-restricted?]
  (fn [query]
    (or (row-restricted? query)
        (per-person-rows? query))))

(defonce fingerprints-wrapped
  (do (alter-var-root #'qp.row-restricted-metadata/row-restricted? row-restricted-on-per-person-data)
      true))

;; On Metabase 0.64 the MCP tool `execute_query` reads each bound of `between` as a whole day, also a bound with a time. The
;; private function `wrap-iso-date` does this in the repair step, before the query processor. The query reaches the driver with
;; whole days, so the driver has no other place for this. This wrap acts only when the metadata provider of the query is on a
;; Cube database: it writes a bound with a time as a literal with no bucket, and `wrap-iso-date` then leaves it as it is. Each
;; other database gets the same arguments as before. A Metabase with no `wrap-iso-date` keeps its own behaviour, and this wrap
;; does not change its vars. See mcp.query.between_keeps_time and isolation.other_driver_mcp_between.
(defn- cube-metadata-provider?
  [metadata-provider]
  (try
    (= :cube (:engine (driver-api/database metadata-provider)))
    (catch Exception _ false)))

(defn- bound-keeping-time
  [bound]
  (if (and (string? bound)
           (re-matches #"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+\-]\d{2}:?\d{2})?" (str/trim bound)))
    ["absolute-datetime" {} (str/trim bound) "default"]
    bound))

(defn- keep-time-of-between-bounds
  [parsed-query]
  (walk/postwalk
   (fn [form]
     (if (and (vector? form) (= "between" (first form)) (<= 4 (count form) 5))
       (-> form
           (update (- (count form) 2) bound-keeping-time)
           (update (- (count form) 1) bound-keeping-time))
       form))
   parsed-query))

(defn- keep-time-on-cube
  [repair]
  (fn [metadata-provider parsed-query & more]
    (apply repair
           metadata-provider
           (cond-> parsed-query
             (cube-metadata-provider? metadata-provider) keep-time-of-between-bounds)
           more)))

(defonce between-wrapped
  (when (try
          (requiring-resolve 'metabase.agent-lib.representations.repair/wrap-iso-date)
          (catch Exception _ nil))
    (alter-var-root (requiring-resolve 'metabase.agent-lib.representations.repair/repair) keep-time-on-cube)
    true))

;; The option `order` of a Cube dimension is its default sort order, and a Metabase field has none. Metabase sorts by each breakout in ascending order before the driver reads
;; the query, and the driver cannot tell that order from the choice of a person. So this wrap acts first, and only on a Cube database. On a stage with a breakout of a
;; dimension with `order: desc`, it writes the order of each breakout. Metabase then adds none, and an order that a person selected stays. See dimension.opt.order.
(defn- descending-dimension?
  [metadata-provider cube-meta clause]
  (when-let [field (when (and (vector? clause) (= :field (first clause)) (integer? (nth clause 2 nil)))
                     (driver-api/field metadata-provider (nth clause 2)))]
    (let [table-name (:name (driver-api/table metadata-provider (:table-id field)))
          member     (str table-name "." (:name field))]
      (boolean (some #(and (= member (:name %)) (= "desc" (:order %)))
                     (mapcat :dimensions (filter #(= table-name (:name %)) (:cubes cube-meta))))))))

(defn- window-aggregation?
  [stage]
  (boolean (some #(and (vector? %) (#{:cum-sum :cum-count :offset} (first %)))
                 (tree-seq coll? seq (mapcat stage [:aggregation :expressions])))))

(defn- with-order-of-dimensions
  [query]
  (let [metadata-provider (:lib/metadata query)
        cube-meta         (cube.meta/cached-meta (:details (driver-api/database metadata-provider)))
        descending?       (partial descending-dimension? metadata-provider cube-meta)
        order-by          (requiring-resolve 'metabase.lib.core/order-by)
        fresh-uuids       (requiring-resolve 'metabase.lib.core/fresh-uuids)]
    (reduce (fn [query stage-number]
              (let [{:keys [breakout] :as stage} (nth (:stages query) stage-number)]
                (if (and (some descending? breakout) (not (window-aggregation? stage)))
                  (reduce #(order-by %1 stage-number (fresh-uuids %2) (if (descending? %2) :desc :asc)) query breakout)
                  query)))
            query
            (range (count (:stages query))))))

(defn- order-of-dimensions-on-cube
  [add-implicit-clauses]
  (fn [query]
    (add-implicit-clauses
     (try
       (cond-> query
         (cube-metadata-provider? (:lib/metadata query)) with-order-of-dimensions)
       (catch Exception e
         (log/warn e "Cube: the default order of a dimension was not applied")
         query)))))

(defonce order-wrapped
  (do (alter-var-root (requiring-resolve 'metabase.query-processor.middleware.add-implicit-clauses/add-implicit-clauses) order-of-dimensions-on-cube)
      true))

;; The MCP tool `execute_query` writes a total order into the query before the query processor, for its pages: each column that has no order gets an ascending one.
;; So the default order of a dimension must be in the query before that step. This wrap acts only on a Cube database, and a Metabase with no such step keeps its vars.
(defn- total-order-after-order-of-dimensions
  [total-order]
  (fn [serialized-query]
    (total-order
     (or (try
           (when (and (map? serialized-query) (pos-int? (:database serialized-query)) (seq (:stages serialized-query)))
             (let [metadata-provider ((requiring-resolve 'metabase.lib-be.core/application-database-metadata-provider) (:database serialized-query))]
               (when (cube-metadata-provider? metadata-provider)
                 (let [query   ((requiring-resolve 'metabase.lib.core/query) metadata-provider serialized-query)
                       ordered (with-order-of-dimensions query)]
                   (when-not (= query ordered)
                     ((requiring-resolve 'metabase.mcp.v2.query/serialize-preserving-parameters) serialized-query ordered))))))
           (catch Exception e
             (log/warn e "Cube: the default order of a dimension was not applied to a query of the MCP server")
             nil))
         serialized-query))))

(defonce mcp-order-wrapped
  (when-let [total-order (try
                           (requiring-resolve 'metabase.mcp.v2.query/with-total-order)
                           (catch Exception _ nil))]
    (alter-var-root total-order total-order-after-order-of-dimensions)
    true))

;; A connection that gets the attribute of a person has lists of values, field fingerprints and card fingerprints for all
;; rows. See cube.semantic/clear-values-of-person-data!, cube.semantic/clear-field-fingerprints-of-person-data! and
;; cube.semantic/clear-result-fingerprints-of-person-data!.
(defmethod driver/notify-database-updated :cube
  [driver database]
  ((get-method driver/notify-database-updated :postgres) driver database)
  (try
    (cube.semantic/clear-values-of-person-data! database)
    (cube.semantic/clear-field-fingerprints-of-person-data! database)
    (cube.semantic/clear-result-fingerprints-of-person-data! database)
    (catch Exception e
      (log/warnf e "Could not clear the lists of values and the fingerprints of database %s" (:id database)))))

(defmethod sql-jdbc.execute/set-timezone-sql :cube [_] "SET TIME ZONE %s")

(defmethod driver/can-connect? :cube
  [driver details]
  (let [parent (get-method driver/can-connect? :postgres)]
    (and (parent driver details)
         (some? (cube.meta/fetch-meta details)))))

;; Metabase 0.65 compiles the stages of a query with the parent driver :postgres as WITH. The driver reads a subquery in :from, and
;; Cube plans MEASURE() of a column of a subquery, so a stage stays a subquery. Metabase 0.64 has no such method.
(when-let [use-ctes-for-stages? (resolve 'metabase.driver.sql.query-processor/use-ctes-for-stages?)]
  (.addMethod ^clojure.lang.MultiFn @use-ctes-for-stages? :cube (constantly false)))

;; Cube always starts weeks on Monday.
(defmethod driver/db-start-of-week :cube [_] :monday)

;; The Postgres method reads enum types from pg_catalog. Cube has none.
(defmethod driver/dynamic-database-types-lookup :cube [_driver _database _database-types] nil)

;;; ---------------------------------------------------- sync ----------------------------------------------------

(defmethod driver/describe-database* :cube
  [_driver database]
  {:tables (into #{}
                 (map cube.meta/cube->table)
                 (cube.meta/public-cubes (cube.meta/fetch-meta (:details database))))})

(defn- allowed?
  [allowed value]
  (or (empty? allowed) (contains? (set allowed) value)))

(defmethod driver/describe-fields :cube
  [_driver database & {:keys [schema-names table-names]}]
  (into []
        (comp (filter (fn [_] (allowed? schema-names cube.meta/schema-name)))
              (filter #(allowed? table-names (:name %)))
              (mapcat cube.meta/cube->fields)
              (map cube.meta/sync-field))
        (sort-by :name (cube.meta/public-cubes (cube.meta/fetch-meta (:details database))))))

;; Metabase keeps one fingerprint of a field for all people. On per-person data it would show the rows of one person, or of
;; all rows, to each person. With no database to read, false. See security.field_fingerprints.
(defmethod driver/database-supports? [:cube :fingerprint]
  [_driver _feature database]
  (and (some? database) (nil? (cube.identity/attribute (:details database)))))

;; Fingerprinting wraps each text column in SUBSTRING. On a wide cube or view, this makes the SQL API fail with
;; "Can't find rewrite due to AST node limit reached".
;;
;; With per-user data, Metabase keeps one fingerprint for all people, so sync reads no rows.
(defmethod driver/table-rows-sample :cube
  [driver table fields rff opts]
  (if (cube.identity/attribute (t2/select-one-fn :details :model/Database :id (:db_id table)))
    (let [rf (rff {:cols (mapv #(select-keys % [:name :base_type]) fields)})]
      (rf (rf)))
    ((get-method driver/table-rows-sample :postgres) driver table fields rff (dissoc opts :truncation-size))))

(defmethod driver/describe-fks :cube
  [_driver database & {:keys [schema-names table-names]}]
  (into []
        (comp (filter (fn [_] (allowed? schema-names cube.meta/schema-name)))
              (filter (fn [{[fk-table] :fk}] (allowed? table-names fk-table)))
              (map (fn [{[fk-table fk-column] :fk, [pk-table pk-column] :pk}]
                     {:fk-table-schema cube.meta/schema-name
                      :fk-table-name   fk-table
                      :fk-column-name  fk-column
                      :pk-table-schema cube.meta/schema-name
                      :pk-table-name   pk-table
                      :pk-column-name  pk-column})))
        (cube.meta/foreign-keys (cube.meta/fetch-meta (:details database)))))

;; Tables, fields, measures and segments get the rest of the data model after sync. See metabase.driver.cube.semantic.

;;; --------------------------------------------------- compiler --------------------------------------------------

(declare ^:dynamic *bucketed-field* current-meta)

(defn- stored-field
  [clause]
  (when (and (vector? clause) (= :field (first clause)) (integer? (nth clause 2 nil)))
    (driver-api/field (driver-api/metadata-provider) (nth clause 2))))

(defn- member-kind
  "The member kind of a field clause: :measure, :segment, :dimension, or nil if it has no stored Cube field."
  [clause]
  (let [database-type (str (:database-type (stored-field clause)))]
    (cond
      (str/starts-with? database-type "cube.measure/")   :measure
      (= database-type "cube.segment")                   :segment
      (str/starts-with? database-type "cube.dimension/") :dimension)))

(defn- physical-type
  "The SQL type of a field on the SQL API."
  [{base-type :base-type}]
  (condp #(isa? %2 %1) base-type
    :type/BigInteger "bigint"
    :type/Number     "numeric"
    :type/Boolean    "boolean"
    :type/DateTime   "timestamp"
    "text"))

(declare apply-binning)

;; Cube cannot plan split_part on the SQL API. It sends substr and strpos to the database.
(defn- geo-part-sql
  "The number in one part of the `latitude,longitude` text of a geo dimension."
  [part geo-text]
  (let [comma [:strpos geo-text [:inline ","]]]
    (h2x/cast "double precision"
              (if (= part "latitude")
                [:substr geo-text [:inline 1] [:- comma [:inline 1]]]
                [:substr geo-text [:+ comma [:inline 1]]]))))

(defn- geo-column
  "`compiled` is the identifier of a latitude or longitude field. It has no column on the SQL API, so this reads its geo
  dimension. A later stage or an alias reads the column that the first stage selected, with the name of the field."
  [{:keys [database-type], field-name :name} opts compiled]
  (let [part         (cube.meta/geo-part database-type)
        source-table (driver-api/qp.add.source-table opts)
        identifier   (h2x/unwrap-typed-honeysql-form compiled)]
    (if (and part
             (h2x/identifier? identifier)
             (not (contains? #{driver-api/qp.add.source driver-api/qp.add.none} source-table)))
      (geo-part-sql part (update identifier 2 #(conj (vec (butlast %)) (cube.meta/geo-dimension-name field-name))))
      compiled)))

(defmethod sql.qp/->honeysql [:cube :field]
  [driver [tag {:keys [temporal-unit binning] :as opts} id :as clause]]
  (let [parent   (get-method sql.qp/->honeysql [:postgres :field])
        field    (stored-field clause)
        compiled (binding [*bucketed-field* field]
                   (parent driver (if binning [tag (dissoc opts :binning) id] clause)))
        compiled (geo-column field opts compiled)
        compiled (if binning (apply-binning binning compiled) compiled)]
    ;; The compiler uses the database type to choose casts. It must not see the cube.* marker.
    (if (and field (not binning) (contains? #{nil :default} temporal-unit))
      (h2x/with-database-type-info compiled (physical-type field))
      compiled)))

;; ClickHouse rejects the filter that Cube makes from `= TRUE` when the dimension SQL is an expression:
;; "Cannot convert string 'true' to type UInt8". Cube accepts 1 and 0 for boolean members on all columns.
(def ^:private ^:dynamic *inside-case*
  "True inside a CASE, where Cube plans the comparison itself and needs a real boolean."
  false)

(defmethod sql.qp/->honeysql [:cube :case]
  [driver clause]
  (binding [*inside-case* true]
    ((get-method sql.qp/->honeysql [:postgres :case]) driver clause)))

;; Metabase writes `= a b c` as OR of each value. A list of 100 values stops the planner of Cube with "AST node limit reached", and IN goes up to about 1000. A time or a true or false
;; value has its own form of `=`.
(def ^:private shortest-in-list 8)

(defn- equal-list
  "The field and the values if each clause of an OR is `field = value` on one number or text field, and the list is long, or nil."
  [clauses]
  (when (>= (count clauses) shortest-in-list)
    (let [plain? (fn [clause]
                   (and (vector? clause)
                        (= := (first clause))
                        (= 4 (count clause))
                        (vector? (nth clause 3))
                        (= :value (first (nth clause 3)))
                        (let [v   (nth (nth clause 3) 2)
                              lhs (nth clause 2)]
                          (and (vector? lhs) (= :field (first lhs)) (integer? (nth lhs 2 nil))
                               (let [{:keys [base-type]} (stored-field lhs)]
                                 (or (and (number? v) (isa? base-type :type/Number))
                                     (and (string? v) (isa? base-type :type/Text))))))))
          field-of (fn [clause] (let [[_ opts id] (nth clause 2)] [id (:join-alias opts) (:temporal-unit opts)]))]
      (when (and (every? plain? clauses) (apply = (map field-of clauses)))
        [(nth (first clauses) 2) (map #(nth % 3) clauses)]))))

(declare fraction-value? exact-column plain-bound-time at-time report-zone? check-no-fraction!)

(defn- same-field?
  [clauses field-of]
  (let [field (fn [clause] (let [[_ opts id] (field-of clause)] [id (:join-alias opts) (:temporal-unit opts)]))]
    (apply = (map field clauses))))

(defn- time-list
  "The field and the time values if each clause is `field = time` on one time field, and the list is long, or nil. A report time zone reads a literal in other ways, see Date bounds in the docs."
  [clauses op]
  (when (and (>= (count clauses) shortest-in-list) (not (report-zone?)))
    (let [plain? (fn [clause]
                   (and (vector? clause) (= op (first clause)) (= 4 (count clause))
                        (some? (plain-bound-time (nth clause 2) (nth clause 3)))))]
      (when (and (every? plain? clauses) (same-field? clauses #(nth % 2)))
        [(nth (first clauses) 2) (map #(plain-bound-time (nth % 2) (nth % 3)) clauses)]))))

(defn- whole-day?
  "True if these two times are a whole day."
  [^LocalDateTime low ^LocalDateTime high]
  (and low high
       (= low (.truncatedTo low ChronoUnit/DAYS))
       (= high (.plusDays low 1))))

(defn- day-of-range
  "The field and the first time of the clause `x >= day AND x < next day`, or nil."
  [clause]
  (when (and (vector? clause) (= :and (first clause)) (= 4 (count clause)))
    (let [[_ _ [low-op _ low-field low] [high-op _ high-field high]] clause]
      (when (and (= :>= low-op) (= :< high-op) (= (nth low-field 2 nil) (nth high-field 2 nil)))
        (let [low-time  (plain-bound-time low-field low)
              high-time (plain-bound-time high-field high)]
          (when (whole-day? low-time high-time)
            [low-field low-time]))))))

(defn- day-list
  "The field and the days if each clause reads one whole day of one time field, and the list is long, or nil."
  [clauses]
  (when (and (>= (count clauses) shortest-in-list) (not (report-zone?)))
    (let [days (map day-of-range clauses)]
      (when (and (every? some? days) (same-field? days first))
        [(ffirst days) (map second days)]))))

(defn- time-literal-form
  [^LocalDateTime value]
  (sql.qp/->honeysql :cube (at-time value)))

(defmethod sql.qp/->honeysql [:cube :or]
  [driver [_ _opts & clauses :as clause]]
  (if-let [[field values] (equal-list clauses)]
    [:in
     (if (some fraction-value? values)
       (exact-column driver field)
       (sql.qp/->honeysql driver field))
     (mapv #(sql.qp/->honeysql driver %) values)]
    (if-let [[field times] (time-list clauses :=)]
      (do (run! #(check-no-fraction! field %) times)
          [:in (sql.qp/->honeysql driver field) (mapv time-literal-form times)])
      (if-let [[field days] (day-list clauses)]
        [:in [:date_trunc (h2x/literal "day") (sql.qp/->honeysql driver field)] (mapv time-literal-form days)]
        ((get-method sql.qp/->honeysql [:postgres :or]) driver clause)))))

(defn- not-equal-list
  "[field values with-null?] if each clause is `field != value` on one field, or NOT of `field = value`, and the list is long, or nil. A list of `AND` stops the planner of Cube at about 100 values."
  [clauses]
  (when (>= (count clauses) shortest-in-list)
    (let [inner (fn [clause]
                  (when (vector? clause)
                    (case (first clause)
                      :!=  (when (= 4 (count clause)) [:= (second clause) (nth clause 2) (nth clause 3)])
                      :not (let [x (nth clause 2 nil)]
                             (when (and (vector? x) (= := (first x)) (= 4 (count x))) x))
                      nil)))
          equals (map inner clauses)
          with-null? (every? #(= :!= (first %)) clauses)]
      (when (every? some? equals)
        (when-let [[field values] (equal-list equals)]
          [field values with-null?])))))

(defn- not-day-of-range
  "The field and the first time of the clause `x < day OR x >= next day`, or nil."
  [clause]
  (when (and (vector? clause) (= :or (first clause)) (= 4 (count clause)))
    (let [[_ _ [low-op _ low-field low] [high-op _ high-field high]] clause]
      (when (and (= :< low-op) (= :>= high-op) (= (nth low-field 2 nil) (nth high-field 2 nil)))
        (let [low-time  (plain-bound-time low-field low)
              high-time (plain-bound-time high-field high)]
          (when (whole-day? low-time high-time)
            [low-field low-time]))))))

(defn- not-day-list
  [clauses]
  (when (and (>= (count clauses) shortest-in-list) (not (report-zone?)))
    (let [days (map not-day-of-range clauses)]
      (when (and (every? some? days) (same-field? days first))
        [(ffirst days) (map second days)]))))

(defmethod sql.qp/->honeysql [:cube :and]
  [driver [_ _opts & clauses :as clause]]
  (if-let [[field values with-null?] (not-equal-list clauses)]
    (let [column (if (some fraction-value? values)
                   (exact-column driver field)
                   (sql.qp/->honeysql driver field))
          plain  (sql.qp/->honeysql driver field)
          out    [:not-in column (mapv #(sql.qp/->honeysql driver %) values)]]
      (if with-null?
        [:or out [:= plain nil]]
        [:and out [:not= plain nil]]))
    (if-let [[field times] (time-list clauses :!=)]
      (let [plain (sql.qp/->honeysql driver field)]
        (run! #(check-no-fraction! field %) times)
        [:or [:not-in plain (mapv time-literal-form times)] [:= plain nil]])
      (if-let [[field days] (not-day-list clauses)]
        ;; Cube reads DATE_TRUNC('day', x) NOT IN (a, b) as notInDateRange and joins the ranges with OR, so two days apart exclude no row. A cast keeps the list. `docs/cube-findings.md` has this.
        (let [plain (sql.qp/->honeysql driver field)]
          [:and
           [:not-in (h2x/cast "date" plain) (mapv (fn [^LocalDateTime day] [:raw (format "DATE '%s'" (.toLocalDate day))]) days)]
           [:not= plain nil]])
        ((get-method sql.qp/->honeysql [:postgres :and]) driver clause)))))

;; Cube computes SUBSTRING and SUBSTR above a subquery, and a sort of that column stops with "unsupported type Utf8View". A cast to text gives a type that it can sort.
(defmethod sql.qp/->honeysql [:cube :substring]
  [driver [_ _opts arg start length]]
  (let [text  (sql.qp/->honeysql driver arg)
        start (sql.qp/->honeysql driver start)]
    (h2x/cast "text"
              (if (some? length)
                [:substr text start (sql.qp/->honeysql driver length)]
                [:substr text start]))))

;; With a query parameter for a string, Cube names each column of its plan after the placeholder. Two of them in one
;; expression make it fail with "duplicate unqualified field name 'Utf8("replaced_placeholder")'".
(defmethod sql.qp/->honeysql [:cube String]
  [_driver value]
  [:inline value])

;; With a query parameter for a time, a CubeScan under post-processing fails with "Can't parse date: 'replaced_placeholder'".
(def ^:private ^DateTimeFormatter whole-second-format (DateTimeFormatter/ofPattern "yyyy-MM-dd HH:mm:ss"))
(def ^:private ^DateTimeFormatter fraction-format (DateTimeFormatter/ofPattern "yyyy-MM-dd HH:mm:ss.SSS"))

(declare session-time)

(defn- time-literal
  [^LocalDateTime t]
  (let [formatter (if (zero? (.getNano t)) whole-second-format fraction-format)]
    (h2x/with-database-type-info [:raw (format "TIMESTAMP '%s'" (.format formatter t))] "timestamp")))

(doseq [klass [LocalDateTime LocalDate OffsetDateTime ZonedDateTime Instant]]
  (defmethod sql.qp/->honeysql [:cube klass]
    [_driver value]
    (time-literal (session-time value))))

(defmethod sql.qp/->honeysql [:cube :value]
  [driver [_ {:keys [database-type]} raw-value :as clause]]
  (if (and (boolean? raw-value)
           (not *inside-case*)
           (str/starts-with? (str database-type) "cube.")
           ;; Cube turns `segment = TRUE` into a segment of the CubeScan, which a rollup can serve. `= 1` goes to push-down.
           (not= database-type "cube.segment"))
    [:inline (if raw-value 1 0)]
    ((get-method sql.qp/->honeysql [:postgres :value]) driver clause)))

;;; Date bounds. The SQL API makes a date range from two bounds, and it starts it 1 ms after the bound of `>`. The ClickHouse
;;; dialect of stock Cube writes the range with parseDateTimeBestEffort, which drops the fraction, so the range includes the
;;; bound. A range that ends with `<` ends at the last whole second, so a DateTime64 loses the rows of that second. Cube does not
;;; show the type of a column. A filter that Cube does not merge into a range reads the bound as it is, for each type:
;;; `(x > t OR x IS NULL) AND x IS NOT NULL` has the same rows as `x > t`. A dialect that keeps the fraction needs none of this,
;;; so the driver asks Cube. See cube.probe/keeps-fractions?.

(declare bucket-rank bucket-start relative-time table-name-of-field utc-time)

(def ^:private ^:dynamic *keep-date-ranges*
  "Not nil if a measure of this query needs a date range, because it reads no other filter as a range: :rolling for a rolling
  window, which reads whole buckets, and :shift for a time shift."
  nil)

(def ^:private ^:dynamic *inside-not*
  "True inside a NOT. Cube does not merge it into a date range, and the form with IS NULL would keep the rows with no value."
  false)

(defn- plain-bound-time
  "The time of the value in a comparison of a column with no time bucket, or nil."
  ^LocalDateTime [field value]
  (when (and (vector? field)
             (= :field (first field))
             (contains? #{nil :default} (:temporal-unit (second field))))
    (let [[tag _ a unit] (when (vector? value) value)]
      (case tag
        :absolute-datetime (when (contains? #{nil :default} unit) (session-time a))
        :relative-datetime (when (number? a) (relative-time a unit))
        nil))))

(defn- at-time
  [^LocalDateTime t]
  [:absolute-datetime {} t :default])

(defn- whole-second-down
  ^LocalDateTime [^LocalDateTime t]
  (.truncatedTo t ChronoUnit/SECONDS))

(defn- fraction?
  [^LocalDateTime t]
  (and t (not (zero? (.getNano t)))))

(defn- postgres-clause
  [driver clause]
  ((get-method sql.qp/->honeysql [:postgres (first clause)]) driver clause))

(defn- exact-bounds?
  "True if Cube keeps a bound of this column as it is, also the fraction."
  [field]
  (if-let [cube-meta (current-meta)]
    (cube.probe/keeps-fractions? (:details (driver-api/database (driver-api/metadata-provider)))
                                 cube-meta
                                 (table-name-of-field field))
    false))

(defn- check-no-fraction!
  [field & times]
  (when (and (some fraction? times) (not (exact-bounds? field)))
    (throw (ex-info (str "Cube drops the fraction of a second in a filter on a time, so the number would be wrong. Use whole "
                         "seconds, or install the ClickHouse dialect, see cube-clickhouse/README.md.")
                    {:type :invalid-query}))))

(defn- not-merged
  "The rows of `compiled`, a bound on `field`, in a form that Cube does not merge into a date range."
  [driver field compiled]
  (let [column (sql.qp/->honeysql driver field)]
    [:and [:or compiled [:is column nil]] [:is-not column nil]]))

(defn- rollup-on-column?
  "True if a rollup can read this column. A rollup needs a date range."
  [field]
  (when-let [cube-meta (current-meta)]
    (cube.probe/rollup-on-time? cube-meta (table-name-of-field field) (:name (stored-field field)))))

(defn- value-read-members-of
  "See `cube.probe/value-read-members`."
  [table-name]
  (when-let [cube-meta (when table-name (current-meta))]
    (cube.probe/value-read-members (:details (driver-api/database (driver-api/metadata-provider))) cube-meta table-name)))

(defn- date-range-column?
  "True if this column needs a date range: a rollup can read it, or the SQL of the cube reads its range as a value."
  [field]
  (or (rollup-on-column? field)
      (some-> (value-read-members-of (table-name-of-field field))
              (contains? (str/lower-case (str (:name (stored-field field))))))))

;;; A report time zone. Cube reads the two bounds of a date range in the zone of the session. Each other bound, also one bound
;;; next to another filter or inside OR or NOT, it reads as UTC. So the meaning of a literal depends on the other filters of the query.
;;; The driver writes each bound in the form that Cube does not merge, with the literal in UTC. A time shift needs a date range, so
;;; with a time shift the bounds stay in the zone, and check-time-shift-bounds! stops one bound alone.

(defn- zone-bound-time
  "The time in the zone of the session of a value in a comparison with a time, also a date with a unit, as a filter on a whole day
  or month has it. Nil for a value that is not a time."
  ^LocalDateTime [value]
  (let [[tag _ a unit] (when (vector? value) value)]
    (case tag
      :absolute-datetime (when-let [t (session-time a)]
                           (if (bucket-rank unit) (bucket-start unit t) t))
      :relative-datetime (cond
                           (number? a)    (relative-time a unit)
                           (= :current a) (relative-time 0 unit))
      nil)))

(def ^:private ^:dynamic *over-aggregated-stage*
  "True while the filters of a stage compile, if the stage reads a stage with an aggregation."
  false)

(declare subquery-limit)

(defn- reads-aggregated-stage?
  "True if the compiled `form` reads a stage with an aggregation, also through stages with no aggregation."
  [form]
  (let [source (ffirst (:from form))]
    (and (map? source)
         (boolean (or (:group-by source)
                      (= subquery-limit (:limit source))
                      (reads-aggregated-stage? source))))))

(def ^:private ^:dynamic *rollup-ranges*
  "The clauses of a filter that stay a date range in the zone of the session, so that a rollup can read them."
  #{})

(defn- filter-parts
  "The clauses of a filter that an AND joins. The key :filters of a stage holds a list of clauses, and the key :filter holds one clause."
  [clause]
  (cond
    (not (keyword? (first clause))) (mapcat filter-parts clause)
    (= :and (first clause))         (mapcat filter-parts (drop 2 clause))
    :else                           [clause]))

(defn- plain-column-id
  "The id of the column of a comparison with no time bucket, or nil."
  [[_ _ field]]
  (when (and (vector? field)
             (= :field (first field))
             (contains? #{nil :default} (:temporal-unit (second field))))
    (nth field 2)))

(defn- range-of-column
  "The clauses of the only two bounds, or the only `between`, on one column that needs a date range. Cube reads these as a date range in the
  zone of the session. A third bound or another filter on the column makes the literals UTC, so this gives nil."
  [parts id]
  (let [on-column  (filter #(some #{id} (tree-seq vector? seq %)) parts)
        lower      (filter #(= :>= (first %)) on-column)
        upper      (filter #(#{:< :<=} (first %)) on-column)
        between    (filter #(= :between (first %)) on-column)
        times      (fn [clause] (every? zone-bound-time (drop 3 clause)))]
    (when (and (every? times on-column)
               (every? #(= id (plain-column-id %)) on-column)
               (date-range-column? (nth (first on-column) 2))
               (or (= 1 (count on-column) (count between))
                   (and (= 2 (count on-column)) (= 1 (count lower) (count upper)))))
      on-column)))

(def ^:private goes-to-the-database
  #{:not :or :contains :starts-with :ends-with :does-not-contain :temporal-extract :sum-where :count-where :share :distinct-where :case :binning
    :+ :- :* :/ :minute-of-hour :hour-of-day :day-of-week :day-of-month :day-of-year :week-of-year :month-of-year :quarter-of-year})

(defn- reads-the-database?
  "True if Cube sends this query to the database with its literals, so no date range reads the zone: a text filter, a part of a date, a bin, an
  expression, or a number with a fraction."
  [{:keys [aggregation breakout] :as query} clause]
  (some #(or (goes-to-the-database %) (and (number? %) (not (integer? %))))
        (tree-seq coll? seq [aggregation breakout clause (:expressions query)])))

(defn- rollup-ranges
  "See `*rollup-ranges*`. A stage over an aggregation filters the rows of a result, and those have no date range."
  [clause over-aggregated-stage? query]
  (if (and (seq clause) (report-zone?) (not over-aggregated-stage?) (not (reads-the-database? query clause)))
    (let [parts (filter-parts clause)]
      (into #{} (mapcat #(range-of-column parts %)) (distinct (keep plain-column-id parts))))
    #{}))

(doseq [clause-key [:filter :filters]]
  (defmethod sql.qp/apply-top-level-clause [:cube clause-key]
    [driver top-level-clause honeysql-form query]
    (let [over-aggregated (reads-aggregated-stage? honeysql-form)]
      (binding [*over-aggregated-stage* over-aggregated
                *rollup-ranges*         (rollup-ranges (get query clause-key) over-aggregated query)]
        ((get-method sql.qp/apply-top-level-clause [:postgres top-level-clause]) driver top-level-clause honeysql-form query)))))

;; A total has one row, so its order has no effect. The MCP tools sort by the aggregation. With COALESCE around the measure, that order sends
;; the query to the database, where stock Cube compares a time in whole seconds.
(defmethod sql.qp/apply-top-level-clause [:cube :order-by]
  [driver top-level-clause honeysql-form query]
  (if (and (seq (:aggregation query)) (empty? (:breakout query)))
    honeysql-form
    ((get-method sql.qp/apply-top-level-clause [:postgres top-level-clause]) driver top-level-clause honeysql-form query)))

(defn- post-processed-column?
  "True if this filter reads a column of an earlier stage with an aggregation. Cube keeps the limit on that stage, so this filter reads the
  result of the stage, where a time is in the zone of the session. A column of raw rows goes to the scan of Cube."
  []
  *over-aggregated-stage*)

(defn- zone-bound
  "A comparison of `field` with a time, in the form for a report time zone, or nil if this comparison is not one."
  [driver [op opts field value :as clause]]
  (if (and (report-zone?)
           *inside-case*
           (vector? field)
           (= :field (first field))
           (bucket-rank (:temporal-unit (second field)))
           (zone-bound-time value))
    ;; A CASE goes to the database, which reads the literal as UTC and makes the bucket of the column in the zone. So the literal is the start of the
    ;; bucket, in UTC.
    (postgres-clause driver [op opts field (at-time (utc-time (zone-bound-time value)))])
    (when (and (report-zone?)
               (nil? *keep-date-ranges*)
               (vector? field)
               (= :field (first field))
               (contains? #{nil :default} (:temporal-unit (second field)))
               (not (post-processed-column?)))
      (when-let [t (zone-bound-time value)]
        (if (contains? *rollup-ranges* clause)
          ;; The time of the session, also for a relative bound: Cube reads the two bounds of a date range in that zone.
          (postgres-clause driver [op opts field (at-time t)])
          (let [compiled (postgres-clause driver [op opts field (at-time (utc-time t))])]
            ;; `x != t` keeps NULL, and Cube merges no range from it.
            (if (or *inside-not* *inside-case* (= :!= op))
              compiled
              (not-merged driver field compiled))))))))

(defn- check-no-strict-bound-with-shift!
  "A time shift needs a date range, and a date range on stock Cube has no form for `x > t` that is right for a DateTime64."
  [field t]
  (when (and t (= :shift *keep-date-ranges*) (not (exact-bounds? field)))
    (throw (ex-info (str "This measure has a time shift, and it needs a date range. Stock Cube cannot write x > t in a date range so that "
                         "it is right for a DateTime64, so the number could be wrong. Use >= with the next second, or install the "
                         "ClickHouse dialect, see cube-clickhouse/README.md.")
                    {:type :invalid-query}))))

(defn- fraction-value?
  "True if this clause is a number with a part after the point that is not zero."
  [[tag _ value :as clause]]
  (and (vector? clause) (= :value tag) (number? value) (not (integer? value)) (not= (double value) (Math/rint (double value)))))

(defn- exact-column
  "A column in an expression makes Cube write the number that it compares with as it is, and it compares two doubles."
  [driver field]
  [:* (sql.qp/->honeysql driver field) (h2x/cast "double precision" (h2x/literal "1.0"))])

(defn- fraction-bound
  "Cube sends a number to ClickHouse as text, and ClickHouse cuts text to the scale of a Decimal column. So `x < 12.50001` reads as `x < 12.5`, and `x IN (44.001)` as `x IN (44)`."
  [driver [op _opts field & values]]
  (when (and (vector? field)
             (= :field (first field))
             (some fraction-value? values))
    (let [column  (exact-column driver field)
          numbers (map (partial sql.qp/->honeysql driver) values)]
      (case op
        :between [:and [:>= column (first numbers)] [:<= column (second numbers)]]
        :!=      [:or [:not= column (first numbers)] [:= column nil]]
        (into [op column] numbers)))))

(defn- exact-bound
  [driver clause]
  (or (fraction-bound driver clause)
      (zone-bound driver clause)))

(defmethod sql.qp/->honeysql [:cube :>]
  [driver [_ opts field value :as clause]]
  (let [t (plain-bound-time field value)]
    (check-no-fraction! field t)
    (or
     (exact-bound driver clause)
     (cond
       (or (nil? t) *inside-not*) (postgres-clause driver clause)
       ;; A range that starts 1 ms after the end of a bucket would read that bucket, also with a dialect that keeps the fraction.
       (= :rolling *keep-date-ranges*) (postgres-clause driver [:>= opts field (at-time (.plusSeconds (whole-second-down t) 1))])
       (exact-bounds? field) (postgres-clause driver clause)
       :else (do (check-no-strict-bound-with-shift! field t)
                 (not-merged driver field (postgres-clause driver clause)))))))

(defmethod sql.qp/->honeysql [:cube :>=]
  [driver [_ _opts field value :as clause]]
  (check-no-fraction! field (plain-bound-time field value))
  (or (exact-bound driver clause)
      (postgres-clause driver clause)))

(defmethod sql.qp/->honeysql [:cube :<]
  [driver [_ _opts field value :as clause]]
  (let [t (plain-bound-time field value)]
    (check-no-fraction! field t)
    (or
     (exact-bound driver clause)
     (if (or (nil? t) *inside-not* *keep-date-ranges* (exact-bounds? field) (date-range-column? field))
       (postgres-clause driver clause)
       (not-merged driver field (postgres-clause driver clause))))))

(defmethod sql.qp/->honeysql [:cube :<=]
  [driver [_ _opts field value :as clause]]
  (check-no-fraction! field (plain-bound-time field value))
  (or (exact-bound driver clause)
      (postgres-clause driver clause)))

(defmethod sql.qp/->honeysql [:cube :between]
  [driver [_ opts field low high :as clause]]
  (check-no-fraction! field (plain-bound-time field low) (plain-bound-time field high))
  (cond
    (some #(fraction-bound driver [:>= opts field %]) [low high])
    (fraction-bound driver clause)

    (contains? *rollup-ranges* clause)
    (postgres-clause driver [:between opts field (at-time (zone-bound-time low)) (at-time (zone-bound-time high))])

    (and (zone-bound-time low)
         (zone-bound-time high)
         (zone-bound driver [:>= opts field low]))
    [:and
     (sql.qp/->honeysql driver [:>= opts field low])
     (sql.qp/->honeysql driver [:<= opts field high])]

    :else
    (postgres-clause driver clause)))

;; With a breakout on the same column, Cube reads `x = t` as the bucket that starts at `t`, and it returns the whole bucket. Two
;; bounds give the rows at `t`.
(defmethod sql.qp/->honeysql [:cube :=]
  [driver [_ opts field value :as clause]]
  (let [t (plain-bound-time field value)]
    (check-no-fraction! field t)
    (if (and t (not *inside-case*))
      [:and
       (sql.qp/->honeysql driver [:>= opts field value])
       (sql.qp/->honeysql driver [:<= opts field value])]
      (or (exact-bound driver clause)
          (postgres-clause driver clause)))))

(defmethod sql.qp/->honeysql [:cube :!=]
  [driver [_ _opts field value :as clause]]
  (check-no-fraction! field (plain-bound-time field value))
  (or (exact-bound driver clause)
      (postgres-clause driver clause)))

;; SQL push-down compares whole seconds in NOT on stock Cube, and Cube reads the literal of a NOT as UTC. So NOT of a bound on a time
;; is the opposite bound. Rows with no value stay out on both forms. See the docs in Known wrong numbers.
(def ^:private opposite-bound
  {:> :<=, :>= :<, :< :>=, :<= :>, := :!=, :!= :=})

(defn- without-not
  "The clause that has the rows of NOT `clause`, with no NOT around a bound on a time, or nil if the clause has none."
  [clause]
  (let [[op opts & args] (when (vector? clause) clause)]
    (cond
      (= :not op)  (first args)
      (= :and op)  (into [:or opts] (map #(or (without-not %) [:not {} %])) args)
      (= :or op)   (into [:and opts] (map #(or (without-not %) [:not {} %])) args)
      (and (= :between op)
           (plain-bound-time (first args) (second args))) [:or opts
                                                           [:< opts (first args) (second args)]
                                                           [:> opts (first args) (nth args 2)]]
      (and (opposite-bound op)
           (plain-bound-time (first args) (second args))) [(opposite-bound op) opts (first args) (second args)])))

(defn- has-time-bound?
  [clause]
  (some #(and (vector? %)
              (or (opposite-bound (first %)) (= :between (first %)))
              (plain-bound-time (nth % 2 nil) (nth % 3 nil)))
        (tree-seq vector? seq clause)))

;; Cube reads NOT (x = v) as its operator notEquals, and that keeps NULL. In SQL, and in Metabase on Postgres, NULL stays out.
(defn- not-equal-with-no-null
  [driver inner]
  (when (= := (first inner))
    (let [[tag column value] (binding [*inside-not* true] (sql.qp/->honeysql driver inner))]
      (when (= := tag)
        [:and [:not= column value] [:not= column nil]]))))

(defmethod sql.qp/->honeysql [:cube :not]
  [driver [_ _opts inner :as clause]]
  (if-let [opposite (when (has-time-bound? inner) (without-not inner))]
    (sql.qp/->honeysql driver opposite)
    (or (not-equal-with-no-null driver inner)
        (binding [*inside-not* true]
          ((get-method sql.qp/->honeysql [:postgres :not]) driver clause)))))

;;; Text filters. Cube reads `x LIKE '%a%'` as its operator `contains`, and also `STRPOS(x, 'a') > 0`, `LEFT` and `RIGHT`. On ClickHouse
;;; that operator ignores case, and in Metabase these filters are case sensitive by default. These forms are not an operator for Cube, so
;;; they go to the database. `chr(31)` marks the end of the text for `ends-with`.

(defn- case-sensitive?
  [opts]
  (:case-sensitive opts true))

(doseq [[tag position-sql]
        {:contains    (fn [column text] [:>= [:strpos column text] [:inline 1]])
         :starts-with (fn [column text] [:= [:strpos column text] [:inline 1]])
         :ends-with   (fn [column text]
                        (let [end [:chr [:inline 31]]]
                          [:>= [:strpos [:|| column end] [:|| text end]] [:inline 1]]))}]
  (defmethod sql.qp/->honeysql [:cube tag]
    [driver [_ opts field text :as clause]]
    (if (case-sensitive? opts)
      (position-sql (sql.qp/->honeysql driver field) (sql.qp/->honeysql driver text))
      (postgres-clause driver clause))))

(defn- zero-when-empty
  "Cube reads a rollup with SUM, so a count over no rows is NULL. SQL has 0 there. With a breakout there are no rows."
  [compiled]
  (if (seq (:breakout sql.qp/*inner-query*))
    compiled
    [:coalesce compiled [:inline 0]]))

(defn- count-field?
  [field]
  (contains? #{"cube.measure/count" "cube.measure/countDistinct" "cube.measure/countDistinctApprox"}
             (str (:database-type field))))

;;; Rolling windows. Cube computes a rolling window for each whole bucket of a time series. With no bucket it returns the
;;; total of all rows, and a date filter that ends inside a bucket does not limit the window. Both give a number that
;;; looks right, so the driver stops these queries.

(declare full-member-name start-of-week-index)

(def ^:private bucket-rank
  "The units that make a time series, from small to large."
  {:minute 0, :hour 1, :day 2, :week 3, :month 4, :quarter 5, :year 6})

(defn- session-zone
  "The time zone of the SQL session. Cube reads a time literal and makes each bucket in this zone."
  ^ZoneId []
  (ZoneId/of (or (driver-api/report-timezone-id-if-supported :cube (driver-api/database (driver-api/metadata-provider)))
                 "UTC")))

(defn- session-time
  "`t` as a local time in the zone of the session."
  ^LocalDateTime [t]
  (condp instance? t
    LocalDateTime  t
    LocalDate      (.atStartOfDay ^LocalDate t)
    OffsetDateTime (LocalDateTime/ofInstant (.toInstant ^OffsetDateTime t) (session-zone))
    ZonedDateTime  (LocalDateTime/ofInstant (.toInstant ^ZonedDateTime t) (session-zone))
    Instant        (LocalDateTime/ofInstant ^Instant t (session-zone))
    nil))

(defn- report-zone?
  "True if the session has a time zone with another time than UTC."
  []
  (let [rules (.getRules (session-zone))]
    (not (and (.isFixedOffset rules) (zero? (.getTotalSeconds (.getOffset rules Instant/EPOCH)))))))

(defn- utc-time
  "The local time `t` of the zone of the session, as a local time in UTC."
  ^LocalDateTime [^LocalDateTime t]
  (LocalDateTime/ofInstant (.toInstant (.atZone t (session-zone))) ZoneOffset/UTC))

(defn- bucket-start
  ^LocalDateTime [unit ^LocalDateTime t]
  (let [day (.truncatedTo t ChronoUnit/DAYS)]
    (case unit
      :minute  (.truncatedTo t ChronoUnit/MINUTES)
      :hour    (.truncatedTo t ChronoUnit/HOURS)
      :day     day
      ;; DayOfWeek counts from Monday as 1, and the index counts from Sunday as 0.
      :week    (.with day (TemporalAdjusters/previousOrSame (DayOfWeek/of (int (inc (mod (dec (start-of-week-index)) 7))))))
      :month   (.withDayOfMonth day 1)
      :quarter (.withDayOfMonth (.withMonth day (inc (* 3 (quot (dec (.getMonthValue day)) 3)))) 1)
      :year    (.withDayOfYear day 1))))

(defn- one-later
  "The end of the period that starts at `t`. Cube times have whole seconds, so with no unit this is one second."
  ^LocalDateTime [unit ^LocalDateTime t]
  (case unit
    :minute  (.plusMinutes t 1)
    :hour    (.plusHours t 1)
    :day     (.plusDays t 1)
    :week    (.plusWeeks t 1)
    :month   (.plusMonths t 1)
    :quarter (.plusMonths t 3)
    :year    (.plusYears t 1)
    (.plusSeconds (.truncatedTo t ChronoUnit/SECONDS) 1)))

(defn- on-boundary?
  "True if the filter value `value` is a boundary of `bucket`. `inclusive?` means that the value itself is inside the
  range, so the boundary is one `field-unit` later."
  [bucket field-unit inclusive? value]
  (let [[tag _ a unit] (when (vector? value) value)]
    (case tag
      :absolute-datetime
      (when-let [t (session-time a)]
        (when (or (nil? field-unit) (= :default field-unit) (bucket-rank field-unit))
          (let [boundary (if inclusive? (one-later field-unit t) t)]
            (= boundary (bucket-start bucket boundary)))))

      ;; A relative date starts at a boundary of its own unit. A month does not start at a boundary of a week.
      :relative-datetime
      (and (bucket-rank unit)
           (>= (long (bucket-rank unit)) (long (bucket-rank bucket)))
           (or (= unit bucket) (not= :week bucket))
           (not= :week unit))

      false)))

;;; Checks of the query as a whole. A column of a model or of an earlier stage has no stored field in the outer stage: its
;;; clause has a name. So these read the stages, and they find the member behind a name in the earlier stages.

(defn- selected-as?
  "True if the clause `selected` of a stage has the output name `column-name`."
  [column-name [_ opts id]]
  (let [output (or (get opts driver-api/qp.add.desired-alias)
                   (when (string? id) id))]
    (= column-name output)))

(defn- column-field
  "The stored field behind `clause`, a :field clause of the stage after `earlier`, or nil. An aggregation of an earlier
  stage has no stored field, so a column with its name gives nil."
  [earlier clause]
  (when (and (vector? clause) (= :field (first clause)))
    (let [id (nth clause 2 nil)]
      (cond
        (integer? id) (stored-field clause)
        (and (string? id) (seq earlier))
        (let [source (peek earlier)]
          (when-let [selected (first (filter (partial selected-as? id)
                                             (concat (:fields source) (:breakout source))))]
            (column-field (pop earlier) selected)))))))

(defn- field-kind
  [field]
  (let [database-type (str (:database-type field))]
    (cond
      (str/starts-with? database-type "cube.measure/")   :measure
      (= database-type "cube.segment")                   :segment
      (str/starts-with? database-type "cube.dimension/") :dimension)))

(defn- field-clauses
  [form]
  (filter #(and (vector? %) (= :field (first %))) (tree-seq coll? seq form)))

(defn- rolling-field?
  [field]
  (str/starts-with? (str (:database-type field)) "cube.measure/cumulative-"))

(defn- selects-primary-key?
  "True if the stage before this one selects the primary key of a cube, so each row of that stage is one row of the cube."
  [earlier]
  (when (seq earlier)
    (some #(let [field (column-field (pop earlier) %)]
             (and (= :type/PK (:semantic-type field))
                  (not= cube.meta/private-key-type (:database-type field))))
          (:fields (peek earlier)))))

(defn- row-measure?
  "True if an earlier stage can give this measure for each row: a stage with raw rows and the primary key. The driver then
  groups that stage by its dimensions. A rolling window measure has no value for one row."
  [earlier field]
  (and (selects-primary-key? earlier) (not (rolling-field? field))))

(def ^:private aggregation-tags #{:sum :avg :min :max :count :distinct})

(defn- own-aggregation-of-column?
  [form]
  (and (vector? form)
       (aggregation-tags (first form))
       (let [arg (nth form 2 nil)]
         (and (vector? arg) (= :field (first arg))))))

(defn- columns-inside-aggregation
  "The columns of an aggregation that are not its direct argument, such as `x` in `sum(x + 1)` or in `count-where(x > 1)`."
  [form]
  (cond
    (own-aggregation-of-column? form)              nil
    (and (vector? form) (= :field (first form)))   [form]
    (coll? form)                                   (mapcat columns-inside-aggregation form)))

(defn- row-value-clauses
  "The columns of a stage where a measure needs the value of each row."
  [{:keys [filters expressions aggregation]}]
  (concat (field-clauses filters) (field-clauses expressions) (columns-inside-aggregation aggregation)))

(defn- check-no-group-by-member!
  "Cube accepts these, ignores the grouping and returns one total row, so stop here and tell the user."
  [earlier {:keys [breakout]}]
  (doseq [clause breakout
          :let   [field (column-field earlier clause)
                  kind  (field-kind field)]
          :when  (and (#{:measure :segment} kind)
                      (not (and (= :measure kind) (row-measure? earlier field))))]
    (throw (ex-info (format "Cube cannot group by the %s %s. Use it as a %s."
                            (name kind)
                            (:name field)
                            (if (= kind :segment) "filter" "metric"))
                    {:type :invalid-query}))))

(defn- check-no-filter-on-measure!
  "On a table, Cube reads a filter on a measure column as a filter on the aggregate of the groups, so it does not filter rows.
  On a model it is a filter on rows, if each row has its value. See row-measure?."
  [earlier stage]
  (doseq [clause (row-value-clauses stage)
          :let   [field (column-field earlier clause)]
          :when  (and (= :measure (field-kind field))
                      (not (row-measure? earlier field)))]
    (throw (ex-info (format (str "Cube cannot filter on the measure column %s here, because it reads this as a filter on the "
                                 "aggregate and not on rows, so the number would be wrong. Filter on a dimension, select the "
                                 "primary key in the model, or make a question that aggregates %s and filter on its result.")
                            (:name field) (:name field))
                    {:type :invalid-query}))))

(defn- own-aggregation?
  "True if `agg-type` is the clause that holds this measure. See cube.meta/aggregation-of-measure."
  [agg-type field]
  (let [member-type (-> (str (:database-type field))
                        (str/replace-first "cube.measure/" "")
                        (str/replace-first "cumulative-" ""))]
    (= agg-type (cube.meta/aggregation-of-measure member-type :max))))

(defn- time-bucket
  "The field and the unit of the breakout that makes the time series of this stage, or nil."
  [earlier {:keys [breakout]}]
  (some (fn [clause]
          (let [field (column-field earlier clause)
                unit  (:temporal-unit (second clause))]
            (when (and (= :dimension (field-kind field)) (bucket-rank unit))
              {:field-id (:id field), :unit unit})))
        breakout))

(defn- filter-on-boundaries?
  "True if each date bound on the column of the bucket is a boundary of the bucket. Only an AND of plain comparisons can
  show this, so a filter with OR, NOT or a part of a date on that column is false."
  [bucket same-column? clause]
  (let [[op _ & args] clause]
    (cond
      (= :and op)                        (every? #(filter-on-boundaries? bucket same-column? %) args)
      (not-any? same-column? (field-clauses clause)) true
      :else
      (let [[field & values] args
            field-unit       (when (vector? field) (:temporal-unit (second field)))
            on-boundary      (partial on-boundary? bucket field-unit)]
        (case op
          (:>= :<)   (on-boundary false (first values))
          (:> :<=)   (on-boundary true (first values))
          :between   (and (on-boundary false (first values)) (on-boundary true (second values)))
          :=         (every? #(and (on-boundary false %) (on-boundary true %)) values)
          false)))))

(defn- rolling-time-member
  "The full name of the time dimension of the bucket of this stage."
  [stage earlier]
  (some (fn [clause]
          (let [field (column-field earlier clause)]
            (when (and (= :dimension (field-kind field)) (bucket-rank (:temporal-unit (second clause))))
              (str (:name (driver-api/table (driver-api/metadata-provider) (:table-id field))) "." (:name field)))))
        (:breakout stage)))

(defn- check-rolling-measure!
  "`stages` are the stages up to and including the one with the measure. A filter of an earlier stage goes to the same scan
  of Cube, so it counts too."
  [stages field]
  (let [measure-name          (:name field)
        {:keys [unit field-id]} (time-bucket (pop stages) (peek stages))]
    (when-not unit
      (throw (ex-info (format (str "%s is a rolling window measure. With no time bucket, Cube returns the total of all "
                                   "rows, not the window. Group by a time bucket, for example by month, and add a date "
                                   "filter.")
                              measure-name)
                      {:type :invalid-query})))
    (when (report-zone?)
      (when-let [reason (cube.probe/rolling-zone-reason (:details (driver-api/database (driver-api/metadata-provider)))
                                                        (full-member-name field)
                                                        (rolling-time-member (peek stages) (pop stages))
                                                        (str (session-zone)))]
        (throw (ex-info reason {:type :invalid-query}))))
    (when-let [reason (cube.probe/rolling-window-reason (:details (driver-api/database (driver-api/metadata-provider)))
                                                        (full-member-name field)
                                                        (rolling-time-member (peek stages) (pop stages))
                                                        unit)]
      (throw (ex-info reason {:type :invalid-query})))
    (doseq [i     (range (count stages))
            :let  [earlier (subvec stages 0 i)
                   same?   #(= field-id (:id (column-field earlier %)))]
            clause (:filters (nth stages i))]
      (when-not (filter-on-boundaries? unit same? clause)
        (throw (ex-info (format (str "%s is a rolling window measure, and Cube computes it for each whole %s. This date "
                                     "filter starts or ends inside a %s, and Cube does not apply that part, so the number "
                                     "would count rows outside the filter. Use a filter on whole %ss, or a smaller time "
                                     "bucket.")
                                measure-name (name unit) (name unit) (name unit))
                        {:type :invalid-query}))))))

(defn- aggregated-fields
  "[agg-type field] for each aggregation in this stage of a column with a stored field."
  [earlier {:keys [aggregation]}]
  (for [form  (tree-seq coll? seq aggregation)
        :when (and (vector? form) (aggregation-tags (first form)))
        :let  [field (column-field earlier (nth form 2 nil))]
        :when field]
    [(first form) field]))

(defn- rows-give-measure?
  "True if the own aggregation of the values of the rows is the measure. A distinct count or a measure with its own SQL has
  no such rule."
  [field]
  (contains? #{"cube.measure/sum" "cube.measure/count" "cube.measure/avg" "cube.measure/min" "cube.measure/max"}
             (str (:database-type field))))

(defn- reads-row-values?
  "True if this stage aggregates the values of rows: it filters or groups by a measure, or the earlier stage has an expression,
  and Cube cannot plan MEASURE() of a subquery with an expression."
  [earlier stage]
  (or (some #(= :measure (field-kind (column-field earlier %))) (concat (row-value-clauses stage) (:breakout stage)))
      (some :expressions earlier)))

(declare time-field?)

(defn- zone-repeats-the-clock?
  "True if this zone has an hour twice, now or since 1970. A zone that has only moved its clock forward, as Asia/Kathmandu did in 1986, has no such hour."
  [^ZoneId zone]
  (let [rules (.getRules zone)]
    (boolean (or (seq (.getTransitionRules rules))
                 (some (fn [^java.time.zone.ZoneOffsetTransition transition]
                         (and (.isOverlap transition)
                              (>= (.getYear (.getDateTimeBefore transition)) 1970)))
                       (.getTransitions rules))))))

(defn- check-no-time-measure-in-zone!
  "Cube makes each time dimension local to the report time zone, and it leaves the value of a measure with `type: time` in UTC, unless the SQL of the
  measure reads SQL_UTILS.convertTz. A distinct count of a time counts the clock, so two rows in the repeated hour of a clock change count once."
  [stages]
  (when (report-zone?)
    (doseq [[agg-type field] (aggregated-fields (pop stages) (peek stages))]
      (when (and (= :measure (field-kind field))
                 (isa? (:base-type field) :type/Temporal)
                 (not (some-> (current-meta) (cube.meta/measure-in-query-zone? (full-member-name field))))
                 (not (cube.probe/time-measure-in-zone? (:details (driver-api/database (driver-api/metadata-provider)))
                                                        (full-member-name field)
                                                        (str (session-zone)))))
        (throw (ex-info (format (str "%s is a measure of type time, and stock Cube gives it in the time zone of the column under a report time zone, so the "
                                     "time would be wrong. Install cube-clickhouse, which gives each such measure in the report time zone, read the time "
                                     "with SQL_UTILS.convertTz in the SQL of the measure, or remove the report time zone.")
                                (:name field))
                        {:type :invalid-query})))
      (when (and (= :distinct agg-type)
                 (time-field? field)
                 (zone-repeats-the-clock? (session-zone)))
        (throw (ex-info (format (str "Cube counts the clock of %s in the report time zone, so two rows in the repeated hour of a clock change "
                                     "count once. Remove the report time zone, or count a dimension with another type.")
                                (:name field))
                        {:type :invalid-query}))))))

(defn- check-no-time-filter-over-aggregation-in-zone!
  "A filter on a time of an aggregated stage goes to the database or to a rollup. The database reads the literal as UTC and a rollup reads it in the
  report time zone, so `x >= t` picks another bucket on one of them. The driver cannot see which one Cube picks."
  [earlier {:keys [filters]}]
  (when (and (report-zone?) (seq (:aggregation (peek earlier))))
    (doseq [[_ opts :as clause] (field-clauses filters)
            :when (and (isa? (:base-type opts) :type/Temporal)
                       (not (contains? #{:minute-of-hour :hour-of-day :day-of-week :day-of-month :day-of-year :week-of-year :month-of-year :quarter-of-year}
                                       (:temporal-unit opts))))]
      (throw (ex-info (format (str "This filter reads the time column %s of a question with an aggregation. With a report time zone that is not "
                                   "UTC, Cube can read the filter value in UTC or in the zone, so it can pick another bucket. Put the filter "
                                   "inside the question, or remove the report time zone.")
                              (nth clause 2))
                      {:type :invalid-query})))))

(defn- check-no-count-of-null-key!
  "Cube writes a measure of type count as `count(pk)`, so a row with a NULL key does not count. It keeps a left join from counting a row with no match, so the driver
  cannot write COUNT(*) in its place."
  [stages]
  (when-let [cube-meta (current-meta)]
    (doseq [[_ field] (aggregated-fields (pop stages) (peek stages))
            :let  [[cube-name plain] (when (and (= :measure (field-kind field))
                                                (= "cube.measure/count" (str (:database-type field))))
                                       (cube.meta/key-count-measure cube-meta (full-member-name field)))]
            :when (and plain
                       (cube.probe/null-key-rows? (:details (driver-api/database (driver-api/metadata-provider))) cube-name plain))]
      (throw (ex-info (format (str "%s counts the primary key of the cube, and some rows have a NULL key, so it would miss these rows. Use the count of rows of Metabase, "
                                   "or give the cube a key with no NULL.")
                              (:name field))
                      {:type :invalid-query})))))

(defn- check-aggregations!
  [stages]
  (check-no-time-measure-in-zone! stages)
  (doseq [[agg-type field] (aggregated-fields (pop stages) (peek stages))
          :when (rolling-field? field)]
    (when-not (own-aggregation? agg-type field)
      (throw (ex-info (format (str "%s is a rolling window measure, and Cube cannot compute %s of it. Use the measure itself "
                                   "by a time bucket.")
                              (:name field) (name agg-type))
                      {:type :invalid-query})))
    (check-rolling-measure! stages field)))

(defn- check-row-values-give-measure!
  "When a stage aggregates the values of rows, the result is right only for a measure with rows-give-measure?."
  [earlier stage]
  (when (reads-row-values? earlier stage)
    (doseq [[agg-type field] (aggregated-fields earlier stage)
            :when (and (= :measure (field-kind field))
                       (own-aggregation? agg-type field)
                       (not (rows-give-measure? field)))]
      (throw (ex-info (format (str "Cube cannot aggregate %s next to an expression, or a filter or a group on a measure "
                                   "column, because the values of the rows of this measure do not add up to it. Filter on a "
                                   "dimension, or make a question for it.")
                              (:name field))
                      {:type :invalid-query})))))

(defn- table-name-of-field
  "The name of the table of a stored field, or nil."
  [clause]
  (when-let [table-id (:table-id (stored-field clause))]
    (:name (driver-api/table (driver-api/metadata-provider) table-id))))

(defn- time-field?
  [field]
  (= "cube.dimension/time" (str (:database-type field))))

(defn- full-member-name
  [field]
  (str (:name (driver-api/table (driver-api/metadata-provider) (:table-id field))) "." (:name field)))

(defn- aggregated-measure-fields
  [earlier stage]
  (for [[_ field] (aggregated-fields earlier stage)
        :when (= :measure (field-kind field))]
    field))

(defn- check-time-shift-zone!
  "See `cube.probe/time-shift-zone-reason`."
  [stages]
  (when (report-zone?)
    (when-let [cube-meta (current-meta)]
      (let [details (:details (driver-api/database (driver-api/metadata-provider)))
            stage   (peek stages)
            earlier (pop stages)]
        (doseq [field (aggregated-measure-fields earlier stage)
                :let  [table-name (:name (driver-api/table (driver-api/metadata-provider) (:table-id field)))]
                :when (cube.probe/time-shift? details cube-meta table-name (full-member-name field))]
          (when-let [reason (cube.probe/time-shift-zone-reason details (full-member-name field) (rolling-time-member stage earlier) (str (session-zone)))]
            (throw (ex-info reason {:type :invalid-query}))))))))

(defn- check-time-shift!
  "With no time bucket and no date filter, Cube returns the total of all rows for a measure with a time shift."
  [stages]
  (let [stage   (peek stages)
        earlier (pop stages)
        time?   (fn [before clause] (time-field? (column-field before clause)))
        bucket? (some (partial time? earlier) (:breakout stage))
        filter? (some (fn [i]
                        (let [before (subvec stages 0 i)]
                          (some (partial time? before) (field-clauses (:filters (nth stages i))))))
                      (range (count stages)))]
    (when-not (or bucket? filter?)
      (when-let [cube-meta (current-meta)]
        (let [details (:details (driver-api/database (driver-api/metadata-provider)))]
          (doseq [field (aggregated-measure-fields earlier stage)
                  :let  [table-name (:name (driver-api/table (driver-api/metadata-provider) (:table-id field)))]
                  :when (cube.probe/time-shift? details cube-meta table-name (full-member-name field))]
            (throw (ex-info (format (str "%s has a time shift. With no time bucket and no date filter, Cube returns the total of "
                                         "all rows, and a shift of that is not a number that you can read. Group by a time "
                                         "bucket, or add a date filter.")
                                    (:name field))
                            {:type :invalid-query}))))))))

(defn- bound-sides
  "The sides of the date range that the bounds in `filters` make on the time columns: :lower and :upper, for an AND of plain bounds."
  [earlier filters]
  (let [time? #(time-field? (column-field earlier %))]
    (into #{}
          (mapcat (fn [[op _ field]]
                    (when (time? field)
                      (case op
                        (:> :>=) [:lower]
                        (:< :<=) [:upper]
                        (:between :=) [:lower :upper]
                        nil))))
          (mapcat (fn [clause] (if (= :and (first clause)) (drop 2 clause) [clause])) filters))))

(defn- check-time-shift-bounds!
  "A measure with a time shift reads a date range. One bound alone is not a range, so Cube returns months outside the filter."
  [stages]
  (let [stage   (peek stages)
        earlier (pop stages)
        sides   (bound-sides earlier (:filters stage))
        {:keys [unit field-id]} (time-bucket earlier stage)
        same?   #(= field-id (:id (column-field earlier %)))
        inside? (and (report-zone?)
                     unit
                     (or (reads-the-database? stage (:filters stage))
                         (not-every? #(filter-on-boundaries? unit same? %) (:filters stage))))]
    (when (or (= 1 (count sides)) inside?)
      (when-let [cube-meta (current-meta)]
        (let [details (:details (driver-api/database (driver-api/metadata-provider)))]
          (doseq [field (aggregated-measure-fields earlier stage)
                  :let  [table-name (:name (driver-api/table (driver-api/metadata-provider) (:table-id field)))]
                  :when (cube.probe/time-shift? details cube-meta table-name (full-member-name field))]
            (throw (ex-info (format (if (= 1 (count sides))
                                      (str "%s has a time shift, and Cube reads it only with a date range. This filter has one bound, so "
                                           "Cube would return periods outside the filter. Give the filter a lower and an upper bound.")
                                      (str "%s has a time shift. Under a report time zone, Cube moves the rows of other periods when the date "
                                           "range starts or ends inside a bucket, or when a text filter sends the query to the database. "
                                           "Use whole buckets and plain filters, or remove the report time zone."))
                                    (:name field))
                            {:type :invalid-query}))))))))

(defn- check-date-only-columns!
  "With a report time zone, stock Cube reads a Date as a time at midnight UTC, so it moves to another day. Cube does not show the type of
  a column, so this acts on a dimension that the model marks with `meta: { date_only: true }`."
  [earlier {:keys [breakout filters fields aggregation expressions]}]
  (when (report-zone?)
    (when-let [cube-meta (current-meta)]
      (doseq [clause (field-clauses [breakout filters fields aggregation expressions])
              :let   [field (column-field earlier clause)]
              :when  (and field
                          (cube.probe/date-only? cube-meta (full-member-name field))
                          ;; Stock Cube v1.8.19 keeps the fraction of a bound too, so this asks how Cube reads a Date.
                          (not (cube.probe/dates-stay-in-zone? (:details (driver-api/database (driver-api/metadata-provider))) cube-meta)))]
        (throw (ex-info (format (str "%s is a Date column, and stock Cube reads it as a time at midnight UTC. With a report time zone "
                                     "that is not UTC, each row can move to another day, so the number would be wrong. Remove the report "
                                     "time zone, or install the ClickHouse dialect, see cube-clickhouse/README.md.")
                                (:name field))
                        {:type :invalid-query}))))))

(defn- check-no-end-of-second-next-to-text!
  "A case-sensitive text filter sends the query to the database, and there stock Cube compares a time in whole seconds. Then `x > t` loses the
  rows of the second after t, and `x <= t` gets them. Cube does not show the type of a column, so this acts on each time column."
  [earlier {:keys [filters]}]
  (let [nodes     (filter vector? (mapcat #(tree-seq vector? seq %) filters))
        text?     (fn [[op opts]]
                    (and (#{:contains :starts-with :ends-with} op)
                         (:case-sensitive (when (map? opts) opts) true)))
        end-bound (fn [[op opts field]]
                    (and (#{:> :<= := :between} op)
                         (vector? field)
                         (= :field (first field))
                         (contains? #{nil :default} (:temporal-unit (when (map? opts) (second field))))
                         (time-field? (column-field earlier field))
                         (not (exact-bounds? field))))]
    (when (and (some text? nodes) (some end-bound nodes))
      (throw (ex-info (str "This filter has a text filter next to a bound on a time. Stock Cube then compares a time in whole seconds, so on "
                           "a DateTime64 the number could be wrong. Use >= and < for the time, or install the ClickHouse dialect, see "
                           "cube-clickhouse/README.md.")
                      {:type :invalid-query})))))

(defn- check-custom-granularities!
  "A breakout on a dimension that reads a custom granularity, with a measure that a rollup serves."
  [stages]
  (let [stage    (peek stages)
        earlier  (pop stages)
        measures (map full-member-name (aggregated-measure-fields earlier stage))]
    (when (seq measures)
      (when-let [cube-meta (current-meta)]
        (let [details (:details (driver-api/database (driver-api/metadata-provider)))]
          (doseq [clause (:breakout stage)
                  :let   [field (column-field earlier clause)]
                  :when  (time-field? field)
                  :let   [[cube-name member granularity] (cube.probe/granularity-of-member
                                                          cube-meta (full-member-name field))]
                  :when  granularity]
            (when-let [reason (cube.probe/wrong-bin-reason details cube-meta cube-name member granularity measures)]
              (throw (ex-info reason {:type :invalid-query})))))))))

(defn- check-equal-sql-breakout!
  "Two `sub_query` dimensions with equal SQL in a query with a filter read one subquery."
  [earlier {:keys [breakout filters]}]
  (when (seq filters)
    (when-let [cube-meta (current-meta)]
      (doseq [[table-id fields] (group-by :table-id (filter :table-id (map (partial column-field earlier) breakout)))
              :let [table-name (:name (driver-api/table (driver-api/metadata-provider) table-id))
                    names      (map :name fields)]
              group (cube.meta/same-sql-groups-of-cube cube-meta table-name)
              :when (next (filter group names))]
        (throw (ex-info (format (str "The columns %s have equal SQL. With a filter in the query, Cube reads the first subquery "
                                     "for both columns, so one number would be wrong. Use one of these columns in a query, or "
                                     "give one of them other SQL in the data model, such as {orders.count} + 0.")
                                (str/join " and " (sort (filter group names))))
                        {:type :invalid-query}))))))

(defn- foreign-key-to-table?
  "True if a field of the table `source-table` is a foreign key to the table `table-id`."
  [source-table table-id]
  (let [provider (driver-api/metadata-provider)]
    (some (fn [{:keys [fk-target-field-id]}]
            (and fk-target-field-id
                 (= table-id (:table-id (driver-api/field provider fk-target-field-id)))))
          (driver-api/fields provider source-table))))

(def ^:private row-aggregations
  "The aggregations that give another number if a row of a joined cube counts once and not for each row of the query. `min`, `max` and `distinct` do not depend on this. A count of rows has no column,
  so it does not stop here. Cube gives `median` and `percentile` no form, so they have their own message."
  #{:sum :avg :stddev :var :sum-where :count-where :share :count :cum-sum :cum-count :median :percentile})

(defn- expression-of-stage
  "The clause of the custom column `expression-name` of this stage, or nil."
  [{:keys [expressions]} expression-name]
  (if (map? expressions)
    (get expressions expression-name)
    (first (filter #(= expression-name (:lib/expression-name (second %))) expressions))))

(defn- lower-text
  [text]
  (some-> text str/lower-case))

(defn- columns-of-name
  "The selected columns of this stage of raw rows that a reference by the name `column-name` can mean. A query can name a column by its output, such as `products__via__product_id__price`, or by the
  name of its field, as `price`, and Metabase core finds it by both. Two columns with one name both count, because a wrong guess here gives a wrong number."
  [column-name {:keys [fields]}]
  (let [wanted (lower-text column-name)
        by-output (filter (fn [item]
                            (or (selected-as? column-name item)
                                (and (= :expression (first item)) (= column-name (nth item 2 nil)))))
                          fields)]
    (if (seq by-output)
      by-output
      (filter (fn [item]
                (let [field (when (and (vector? item) (= :field (first item)) (integer? (nth item 2 nil)))
                              (stored-field item))]
                  (= wanted (lower-text (:name field)))))
              fields))))

(defn- source-fields
  "The stored fields that `clause` reads, in the stage after `earlier`. This reads through a custom column, and through a column of an earlier stage of raw rows. A column of a stage
  with an aggregation or a breakout is a value of a group, so a later stage gives the number of SQL."
  [earlier stage clause]
  (mapcat (fn [form]
            (cond
              (and (vector? form) (= :expression (first form)))
              (when-let [definition (expression-of-stage stage (nth form 2 nil))]
                (source-fields earlier stage definition))

              (and (vector? form) (= :field (first form)))
              (let [id     (nth form 2 nil)
                    source (peek earlier)]
                (cond
                  (integer? id) (keep identity [(stored-field form)])

                  (and (string? id) source (empty? (:aggregation source)) (empty? (:breakout source)))
                  (mapcat #(source-fields (pop earlier) source %) (columns-of-name id source))))))
          (tree-seq #(and (coll? %) (not (and (vector? %) (= :field (first %))))) seq clause)))

(defn- joined-through-model?
  "True if a row of the table `root-table` reads one row of the table of this field, so a query has each row of that table for many rows of the query."
  [cube-meta root-table {:keys [table-id], field-name :name}]
  (let [provider (driver-api/metadata-provider)]
    (if (= root-table table-id)
      (and cube-meta (cube.meta/joined-member-of-view? cube-meta (:name (driver-api/table provider table-id)) field-name))
      (or (foreign-key-to-table? root-table table-id)
          (and cube-meta
               (cube.meta/shared-by-many-rows? cube-meta
                                               (:name (driver-api/table provider root-table))
                                               (:name (driver-api/table provider table-id))))))))

(defn- table-name-of-id
  [table-id]
  (:name (driver-api/table (driver-api/metadata-provider) table-id)))

(defn- tables-of-query
  "The names of the tables that the stages of a query read: the first table, each join, and the table of each field."
  [stages]
  (into #{}
        (keep (fn [form]
                (cond
                  (and (map? form) (integer? (:source-table form)))
                  (table-name-of-id (:source-table form))

                  (and (vector? form) (= :field (first form)) (integer? (nth form 2 nil)))
                  (some-> (stored-field form) :table-id table-name-of-id))))
        (tree-seq coll? seq stages)))

(defn- fan-out-of-field
  "The cube of the query that gives each row of the cube of `field` many rows, or nil. See `cube.meta/fans-out?`."
  [cube-meta stages root {:keys [table-id], field-name :name}]
  (when (and cube-meta (integer? root))
    (let [root-name (table-name-of-id root)
          start     (cube.meta/root-cube cube-meta root-name)
          own       (cube.meta/member-cube cube-meta (table-name-of-id table-id) field-name)]
      (first (filter #(cube.meta/fans-out? cube-meta start own %)
                     (mapcat (fn [table]
                               (if (cube.meta/view? cube-meta table)
                                 nil
                                 [table]))
                             (tables-of-query stages)))))))

(defn- check-no-view-fan-out!
  "A count of rows or an aggregation of a column on a view with a one to many join. Cube joins only the cubes of the columns of a query, so the view has no one set of rows."
  [cube-meta root stage earlier]
  (when (and cube-meta (integer? root))
    (when-let [[one many] (cube.meta/view-fan-out cube-meta (table-name-of-id root))]
      (doseq [form  (tree-seq coll? seq (:aggregation stage))
              :when (and (vector? form) (row-aggregations (first form)))
              :let  [fields (source-fields earlier stage (vec (drop 2 form)))]
              :when (or (and (empty? (drop 2 form)) (= :count (first form)))
                        (some #(= :dimension (field-kind %)) fields))]
        (throw (ex-info (format (str "The view %s reads %s through a one to many join from %s, and Cube joins only the cubes of the columns of a query. So a count of rows or an aggregation of a column of "
                                     "this view does not read the rows of the join, and the number would be wrong. Use a measure of the view, or start from the cube with the join.")
                                (table-name-of-id root) many one)
                        {:type :invalid-query}))))))

(defn- check-no-total-through-join!
  "Cube protects a dimension of the one side of a join from fan-out, so it reads each row of that cube once. SUM(products.price) by a column of orders gives the total of the products in place of the
  sum over the orders, and COUNT(products.price) gives the number of products. A later stage over raw rows gives the same number, because Cube reads it as one query. The same holds for a dimension of the first
  cube next to a one to many join: SUM(users.id) over users and orders reads each user once. The second form of two stages counts the rows of the join in each group."
  [stages]
  (let [stage   (peek stages)
        earlier (pop stages)
        root    (:source-table (first stages))
        cube-meta (current-meta)]
    (check-no-view-fan-out! cube-meta root stage earlier)
    (when (integer? root)
      (doseq [form  (tree-seq coll? seq (:aggregation stage))
              :when (and (vector? form) (row-aggregations (first form)))
              field (source-fields earlier stage (vec (drop 2 form)))
              :when (= :dimension (field-kind field))
              :let  [fan-out (fan-out-of-field cube-meta stages root field)]
              :when (or fan-out (joined-through-model? cube-meta root field))]
        (throw (ex-info (format (str "Cube cannot add up the dimension %s over the rows of this query. It reads each row of the "
                                     "%s once, so the number would be wrong. Add a measure to the data model, or start "
                                     "from the cube of this dimension.")
                                (:name field)
                                (if fan-out (format "cube of this dimension, also next to the one to many join to %s," fan-out) "joined cube"))
                        (cond-> {:type :invalid-query, ::try-two-stages true}
                          fan-out (assoc ::fan-out true))))))))

(defn- date-range-clauses?
  "True if these clauses on one time column are a date range for Cube: one `between`, or one `>=` next to one `<` or `<=`, with time literals."
  [clauses]
  (let [ops (map first clauses)]
    (and (every? #(contains? #{nil :default} (:temporal-unit (second (nth % 2)))) clauses)
         (every? (fn [clause] (every? zone-bound-time (drop 3 clause))) clauses)
         (or (= [:between] ops)
             (= #{:>= :<} (set ops))
             (= #{:>= :<=} (set ops)))
         (= (count ops) (count (set ops))))))

(defn- check-value-read-members!
  "The SQL of a cube can read a filter of Cube as a value. Cube gives this value only for a date range, or for one equal value. With another
  filter or none, the database reads `1 = 1` there, so the result is empty or wrong, or the database fails with an unclear message. A filter of a
  later stage over raw rows also reaches Cube."
  [stages]
  (let [provider (driver-api/metadata-provider)
        table-id (:source-table (first stages))
        members  (when (pos-int? table-id)
                   (value-read-members-of (:name (driver-api/table provider table-id))))]
    (doseq [[member kind] members]
      (let [stages-to-cube (let [[raw more] (split-with #(empty? (:aggregation %)) stages)]
                             (concat raw (take 1 more)))
            on-member      (for [[i stage] (map-indexed vector stages-to-cube)
                                 clause    (filter-parts (:filters stage))
                                 :let      [earlier (subvec (vec stages) 0 i)]
                                 :when     (some (fn [f] (= member (some-> (:name (column-field earlier f)) str/lower-case)))
                                                 (field-clauses [clause]))]
                             {:clause clause, :stage stage})
            clauses        (map :clause on-member)]
        (when-not (if (= :time kind)
                    (and (date-range-clauses? clauses)
                         (not (and (report-zone?) (some #(reads-the-database? (:stage %) (:filters (:stage %))) on-member))))
                    (and (= 1 (count clauses))
                         (= := (ffirst clauses))
                         (= 4 (count (first clauses)))))
          (throw (ex-info (format (if (= :time kind)
                                    (str "The SQL of this cube reads the date range of %s as a value, through FILTER_PARAMS. Cube gives that value "
                                         "only for a lower and an upper bound, >= with < or <=, or between, with no OR and no NOT. With another "
                                         "filter or none, the database reads 1 = 1 there, so the number would be wrong. Give %s a lower and an "
                                         "upper bound. With a report time zone, also remove a text filter, a part of a date or an expression "
                                         "next to it, because then Cube does not read a date range.")
                                    (str "The SQL of this cube reads the filter on %s as a value, through FILTER_PARAMS. Cube gives that value only "
                                         "for one equal value. With another filter or none, the database reads 1 = 1 there, so the number would "
                                         "be wrong. Filter %s on one value."))
                                  member member)
                          {:type :invalid-query})))))))

(defn- private-of-name
  "A map with the name of a member that `column-name` names on `table-id`, if Cube marks that member private, or nil. Metabase also takes a reference by name, which has no stored
  field. A private member that no foreign key targets has no field at all, so this also reads the model."
  [table-id column-name]
  (when (and (pos-int? table-id) (string? column-name))
    (let [provider (driver-api/metadata-provider)
          lower    (str/lower-case column-name)
          table    (:name (driver-api/table provider table-id))]
      (or (first (filter #(and (= cube.meta/private-key-type (:database-type %))
                               (= lower (str/lower-case (:name %))))
                         (driver-api/fields provider table-id)))
          (when-let [cube-meta (current-meta)]
            (when (contains? (get (cube.meta/private-members cube-meta) (str/lower-case table)) lower)
              {:name column-name}))))))

(defn- refuse-private-member!
  [{field-name :name, :keys [database-type]}]
  (throw (ex-info (if (= cube.meta/private-key-type database-type)
                    (format "%s is a private primary key of the cube, so it has no values here. Use another column, or make it public in the model." field-name)
                    (format "%s is a private member of the cube, so it has no values here. Use another column, or make it public in the model." field-name))
                  {:type :invalid-query})))

(defn- check-no-private-key!
  "A private primary key is a field only as the target of a foreign key. Cube does not read it, or it reads it only in dev mode, so a stage must not read its values. A raw row
  has NULL for a field by id, see raw-select-item. A reference by name has no such step, so `:fields` counts for it. cube.native/check-no-private-member! also reads the SQL
  of each query, for the forms that this misses."
  [{:keys [source-table] :as stage}]
  (doseq [form  (tree-seq coll? seq (select-keys stage [:breakout :filters :aggregation :order-by :expressions :fields]))
          :when (and (vector? form) (= :field (first form)))
          :let  [field   (stored-field form)
                 by-name (when-not (:join-alias (second form)) (private-of-name source-table (nth form 2 nil)))]
          :when (or by-name
                    (and (= cube.meta/private-key-type (:database-type field))
                         (not (some #(identical? form %) (:fields stage)))))]
    (refuse-private-member! (or by-name field))))

(defn- check-no-private-key-of-earlier-stage!
  "A later stage reads a column of an earlier stage by its name. Raw rows of a private key are NULL, but Cube reads a filter of a later stage as a filter on the member, so
  `x > 100000` over a stage with `NULL AS x` counts the rows of the real value. So a later stage can show that column in its rows, and it cannot read it. A model and a saved question
  are stages too."
  [stages]
  (doseq [i     (range 1 (count stages))
          :let  [earlier (subvec stages 0 i)
                 stage   (nth stages i)]
          form  (field-clauses (select-keys stage [:breakout :filters :aggregation :order-by :expressions]))
          :let  [field (column-field earlier form)
                 field (if (= cube.meta/private-key-type (:database-type field))
                         field
                         (private-of-name (:source-table (first stages)) (nth form 2 nil)))]
          :when field]
    (refuse-private-member! field)))

(defn- check-stages!
  [stages]
  (check-value-read-members! stages)
  (check-no-private-key-of-earlier-stage! stages)
  (doseq [i (range (count stages))
          :let [stage (nth stages i)]
          :when (not= :mbql.stage/native (:lib/type stage))]
    (let [upto (subvec stages 0 (inc i))]
      (check-no-private-key! stage)
      (check-no-total-through-join! upto)
      (check-no-time-filter-over-aggregation-in-zone! (pop upto) stage)
      (check-no-group-by-member! (pop upto) stage)
      (check-no-filter-on-measure! (pop upto) stage)
      (check-row-values-give-measure! (pop upto) stage)
      (check-equal-sql-breakout! (pop upto) stage)
      (check-aggregations! upto)
      (check-no-count-of-null-key! upto)
      (check-time-shift! upto)
      (check-time-shift-zone! upto)
      (check-time-shift-bounds! upto)
      (check-date-only-columns! (pop upto) stage)
      (check-no-end-of-second-next-to-text! (pop upto) stage)
      (check-custom-granularities! upto))))

(defn- needs-date-ranges
  "See `*keep-date-ranges*`."
  [stages]
  (let [cube-meta (current-meta)
        fields    (mapcat (fn [i]
                            (when-not (= :mbql.stage/native (:lib/type (nth stages i)))
                              (aggregated-measure-fields (subvec stages 0 i) (nth stages i))))
                          (range (count stages)))]
    (cond
      (some rolling-field? fields) :rolling
      (and cube-meta
           (some #(cube.probe/time-shift? (:details (driver-api/database (driver-api/metadata-provider)))
                                          cube-meta
                                          (:name (driver-api/table (driver-api/metadata-provider) (:table-id %)))
                                          (full-member-name %))
                 fields))
      :shift)))

(defn- aggregated-measures
  "A map from the name of each measure that a later stage aggregates to its field. Only these need the measure itself in a
  subquery."
  [stages]
  (into {}
        (mapcat (fn [i]
                  (keep (fn [[_ field]]
                          (when (= :measure (field-kind field)) [(:name field) field]))
                        (aggregated-fields (subvec stages 0 i) (nth stages i)))))
        (range 1 (count stages))))

(defn- named-measures
  "A map from the name of a column to its measure, for the stages where Cube reads MEASURE() of that column of a subquery."
  [stages]
  (into {}
        (mapcat (fn [i]
                  (let [earlier (subvec stages 0 i)
                        stage   (nth stages i)]
                    (when-not (reads-row-values? earlier stage)
                      (keep (fn [[_ field]]
                              (when (= :measure (field-kind field)) [(:name field) field]))
                            (aggregated-fields earlier stage))))))
        (range 1 (count stages))))

(defn- row-measure-names
  "The measures that a later stage filters or groups by. A subquery with these must give the value of each row."
  [stages]
  (into #{}
        (mapcat (fn [i]
                  (let [earlier (subvec stages 0 i)
                        stage   (nth stages i)]
                    (keep (fn [clause]
                            (let [field (column-field earlier clause)]
                              (when (= :measure (field-kind field)) (:name field))))
                          (concat (row-value-clauses stage) (:breakout stage))))))
        (range 1 (count stages))))

;;; Cube pushes a filter of an outer stage below the aggregation of a subquery, also into a multi-stage measure, so the rows
;;; of the subquery change. A limit on the subquery stops this. This limit is above any result, and CUBEJS_DB_QUERY_LIMIT
;;; still applies to the rows of a subquery.
(def ^:private subquery-limit 2147483647)

(defn- aggregated-native?
  [text]
  (boolean (re-find #"(?i)\bgroup\s+by\b|\b(measure|count|sum|avg|min|max)\s*\(" text)))

(defn- limit-native
  [text]
  (format "SELECT * FROM (\n%s\n) AS \"native_source\" LIMIT %d" (str/replace text #"[\s;]+$" "") subquery-limit))

(defn- limit-subqueries
  "Add the limit to each stage with an aggregation that has a next stage."
  [stages]
  (into []
        (map-indexed (fn [i {:keys [aggregation breakout limit native] :as stage}]
                       (cond
                         (= (inc i) (count stages))                  stage
                         (and (string? native) (or (aggregated-native? native)
                                                   (when-let [cube-meta (current-meta)]
                                                     (cube.native/joined-rows? cube-meta native))))
                         (assoc stage :native (limit-native native))
                         (string? native)                            stage
                         (and (or (seq aggregation) (seq breakout)) (nil? limit)) (assoc stage :limit subquery-limit)
                         :else                                       stage)))
        stages))

(def ^:private ^:dynamic *named-measures*
  "A map from the name of a column of an earlier stage to its measure, for the columns that this query aggregates."
  {})

(def ^:private ^:dynamic *row-measures*
  "The names of the measures that a later stage filters or groups by."
  #{})

(defn- measure-of-clause
  "The measure field of an aggregation argument: a stored field, or a column by the name of an earlier stage."
  [arg]
  (or (when (= :measure (member-kind arg)) (stored-field arg))
      (when (and (vector? arg) (string? (nth arg 2 nil)))
        (get *named-measures* (nth arg 2)))))

(doseq [agg-type [:sum :avg :min :max :count :distinct]]
  (let [parent (get-method sql.qp/->honeysql [:postgres agg-type])]
    (defmethod sql.qp/->honeysql [:cube agg-type]
      [driver [_ _opts arg :as clause]]
      ;; A measure has its aggregation in the data model, and its own clause is MEASURE(). Another aggregation stays a SQL
      ;; aggregation: Cube computes it from the rows, or fails with "Measure aggregation type doesn't match". As MEASURE()
      ;; it would return the measure with no error, under the name of another aggregation.
      ;; A column of a model by name is the same: Cube reads MEASURE() of the column of a subquery as the measure, and a SQL
      ;; aggregation of it adds up the value of each row, which is wrong for a distinct count or a rank.
      (let [field (measure-of-clause arg)]
        (cond
          (and (= agg-type :count) (nil? arg)) (zero-when-empty (parent driver clause))
          (nil? field)                         (parent driver clause)
          (not (own-aggregation? agg-type field)) (parent driver clause)
          ;; A time series keeps each bucket, so a window of no rows is a row with NULL, and SQL has 0 there.
          (= "cube.measure/cumulative-count" (str (:database-type field))) [:coalesce [:measure (sql.qp/->honeysql driver arg)] [:inline 0]]
          (count-field? field)                 (zero-when-empty [:measure (sql.qp/->honeysql driver arg)])
          :else                                [:measure (sql.qp/->honeysql driver arg)])))))

(def ^:private ^:dynamic *raw-measures*
  "An atom from column alias to the compiled measure, for measures that a stage of this query selects as raw rows and a
  later stage aggregates."
  nil)

(def ^:private ^:dynamic *aggregated-measures*
  "The names of the measures that a later stage of this query aggregates, filters or groups by."
  #{})

(def ^:private ^:dynamic *row-measure-aliases*
  "An atom with the aliases of the measures in `*row-measures*` that a stage of raw rows selects."
  nil)

(def ^:private ^:dynamic *null-columns*
  "An atom with the aliases of the columns that a stage of raw rows selects as NULL."
  nil)

(defn- raw-select-item
  "A measure has no value for one row, and a segment column on the SQL API is always NULL. Cube fails an ungrouped
  query that selects a measure with an aggregate in its SQL or a multi-stage measure, and one such measure would break
  the whole table view. So raw rows show NULL for these columns, and for a geo dimension of a view, which Cube cannot read.
  The same rule applies to raw rows of a model. A column that repeats a member through another join path of a view is also
  NULL, because Cube gives it the value of the first path."
  [driver same-source clause]
  (let [[compiled alias-form :as selected] (sql.qp/as driver clause)
        field       (stored-field clause)
        ;; With no cast. Next to an expression, each cast of NULL grows the rewrite graph of Cube: 10 stop on its limit, and 20 restart it. `compile.raw.custom_column_next_to_each_column` holds this for each table.
        null-column nil]
    (when (and *raw-measures* alias-form (= :measure (member-kind clause))
               (contains? *aggregated-measures* (:name field)))
      (swap! *raw-measures* assoc alias-form compiled)
      (when (contains? *row-measures* (:name field))
        (swap! *row-measure-aliases* conj alias-form)))
    (cond
      (not (or (#{:measure :segment} (member-kind clause))
               (= "cube.dimension/geo-in-view" (:database-type field))
               (= cube.meta/private-key-type (:database-type field))
               (contains? same-source (:name field)))) selected
      alias-form                                        (do (some-> *null-columns* (swap! conj alias-form))
                                                            [null-column alias-form])
      :else                                             [null-column])))

(defn- later-same-source-fields
  "The names of the fields in `fields` after the first one of each group of aliases of one member. With `filtered?`, also after
  the first one of each group with equal SQL: Cube reads one `sub_query` for them when the query has a filter."
  [fields filtered?]
  (when-let [table-id (some #(:table-id (stored-field %)) fields)]
    (let [provider (driver-api/metadata-provider)
          groups   (try
                     (let [cube-meta  (cube.meta/cached-meta (:details (driver-api/database provider)))
                           table-name (:name (driver-api/table provider table-id))]
                       (concat (cube.meta/same-source-groups-of-cube cube-meta table-name)
                               (when filtered? (cube.meta/same-sql-groups-of-cube cube-meta table-name))))
                     (catch Exception _ nil))
          selected (keep #(:name (stored-field %)) fields)
          switches (when (next selected)
                     (try
                       (cube.meta/switch-dimension-names (cube.meta/cached-meta (:details (driver-api/database provider)))
                                                         (:name (driver-api/table provider table-id)))
                       (catch Exception _ nil)))]
      ;; Cube gives each row once for each value of a switch dimension, so raw rows show NULL for it, and each row stays one row.
      (into (set (filter #(contains? switches (str/lower-case %)) selected))
            (mapcat (fn [group] (rest (filter group selected))))
            groups))))

(defmethod sql.qp/apply-top-level-clause [:cube :fields]
  [driver _ honeysql-form {fields :fields, filters :filters}]
  (let [same-source (later-same-source-fields fields (seq filters))]
    (update honeysql-form :select (fnil into []) (map (partial raw-select-item driver (set same-source))) fields)))

;; The SQL API plans a join only from __cubeJoinField on both sides. It takes the join condition from the data model.
(defn- join-field
  "`\"public\".\"orders\".\"user_id\"` -> `\"public\".\"orders\".\"__cubeJoinField\"`, or nil if `clause` is not a field."
  [driver clause]
  (let [compiled (h2x/unwrap-typed-honeysql-form (sql.qp/->honeysql driver clause))]
    (when (h2x/identifier? compiled)
      (update compiled 2 #(conj (vec (butlast %)) "__cubeJoinField")))))

(defn- plain-table-id
  "The table id of a join stage that only selects columns of one table. Metabase writes such a join as a subquery, and
  a subquery has no __cubeJoinField."
  [{:keys [stages]}]
  (let [[{:keys [source-table] :as stage} & more] stages]
    (when (and source-table
               (empty? more)
               (not-any? stage [:aggregation :breakout :filters :expressions :joins :order-by :limit]))
      source-table)))

(defn- field-member
  "[cube member] of a stored field."
  [provider clause]
  (when-let [field (stored-field clause)]
    [(:name (driver-api/table provider (:table-id field))) (:name field)]))

(defn- check-model-join
  "Cube ignores the ON columns and takes the join from the data model. A condition on other columns would give the
  numbers of the model join, so stop here."
  [lhs rhs]
  (let [provider (driver-api/metadata-provider)
        left     (field-member provider lhs)
        right    (field-member provider rhs)
        pairs    (try
                   (cube.meta/join-pairs (cube.meta/cached-meta (:details (driver-api/database provider))))
                   (catch Exception _ nil))]
    (when (and pairs left right (not (contains? pairs #{left right})))
      (throw (ex-info (format (str "Cube joins only through the data model. It has no join on %s.%s = %s.%s, so Cube "
                                   "would ignore this condition. Join on the columns of a join in the model.")
                              (first left) (second left) (first right) (second right))
                      {:type :invalid-query})))))

(defmethod sql.qp/join->honeysql :cube
  [driver {:keys [conditions] :as join}]
  (let [[operator _ lhs rhs] (first conditions)
        table-id             (plain-table-id join)
        left                 (when (= := operator) (join-field driver lhs))
        right                (when (= := operator) (join-field driver rhs))]
    (if (and table-id left right (= 1 (count conditions)))
      (do
        (check-model-join lhs rhs)
        [[(sql.qp/->honeysql driver (driver-api/table (driver-api/metadata-provider) table-id))
          [(sql.qp/->honeysql driver (h2x/identifier :table-alias ((some-fn driver-api/qp.add.alias :alias) join)))]]
         [:= left right]])
      (throw (ex-info (str "Cube can join only a whole table with one equality of two columns, because it takes the join "
                           "from the data model. Filter or aggregate after the join, or add a view to the model.")
                      {:type :invalid-query})))))

;; Cube joins are LEFT. An inner join is a left join that drops the rows with no match.
(defmethod sql.qp/apply-top-level-clause [:cube :joins]
  [driver clause honeysql-form {:keys [joins] :as query}]
  (let [form (reduce (fn [form {:keys [strategy conditions]}]
                       (if (= :inner-join strategy)
                         (let [[_ _ lhs rhs] (first conditions)
                               joined?     (fn [[_ opts]] (string? (driver-api/qp.add.source-table opts)))
                               column      (sql.qp/->honeysql driver (if (joined? rhs) rhs lhs))]
                           (update form :where (fn [where]
                                                 (let [not-null [:is-not column nil]]
                                                   (if where [:and where not-null] not-null)))))
                         form))
                     honeysql-form
                     joins)]
    ((get-method sql.qp/apply-top-level-clause [:postgres :joins]) driver clause form query)))

;; Cube turns DATE_TRUNC on a time dimension into a granularity of the CubeScan. A cast around the column or an
;; interval on it stops that rewrite.
(defn- date-trunc
  [unit expr]
  (h2x/with-database-type-info [:date_trunc (h2x/literal (name unit)) expr] "timestamp"))

(doseq [unit [:minute :hour :day :month :quarter :year]]
  (defmethod sql.qp/date [:cube unit] [_driver _unit expr] (date-trunc unit expr)))

;;; Weeks. Cube weeks start on Monday, and Metabase has a setting for the first day. The Postgres driver shifts the
;;; column by an interval, but Cube cannot generate SQL for an interval on ClickHouse (metabase/metabase#42255).
;;; So Monday is a plain DATE_TRUNC, another day uses a custom granularity of the data model if one fits, and otherwise
;;; Cube groups by a week number that the outer query turns back into a date. Only the first two can read a rollup.

(def ^:private week-days [:sunday :monday :tuesday :wednesday :thursday :friday :saturday])

(defn- start-of-week-index
  "The first day of the week in Metabase, where Sunday is 0."
  ^long []
  (let [index (.indexOf ^java.util.List week-days (driver-api/start-of-week))]
    (if (neg? index) 0 (long index))))

(defn- week-granularity
  "The name of a custom granularity of `field` with weeks that start on day `start`, or nil."
  [field ^long start]
  (when-let [table-id (:table-id field)]
    (let [provider (driver-api/metadata-provider)
          details  (:details (driver-api/database provider))
          table    (driver-api/table provider table-id)
          ;; Cube weeks start on Monday, so Sunday is an offset of -1 day.
          days     (long (mod (- start 1) 7))
          wanted   (into #{(format "%d day" days) (format "%d days" days)
                           (format "-%d day" (- 7 days)) (format "-%d days" (- 7 days))})]
      (some (fn [{:keys [interval offset origin], granularity-name :name}]
              (when (and (= interval "1 week") (nil? origin) (contains? wanted offset))
                granularity-name))
            (try
              (cube.meta/time-granularities (cube.meta/cached-meta details) (:name table) (:name field))
              (catch Exception _ nil))))))

(def ^:private ^:dynamic *bucketed-field*
  "The stored field that the compiler is bucketing now."
  nil)

(def ^:private ^:dynamic *week-numbers*
  "An atom with the week numbers that this query selects. The outer query turns them into dates."
  nil)

(defn- first-week-day-number
  "Days from 1970-01-01 to the first day `start` after it. ClickHouse has no DateTime before 1970, and it makes an
  earlier value 1970-01-01, so the first week must be after that date."
  ^long [^long start]
  (+ 3 start))

(defn- time-argument
  "A text of a time goes to ClickHouse as it is, and dateDiff stops on a text, so the driver casts it."
  [x]
  (if (and (vector? x) (= :inline (first x)) (string? (second x)))
    (h2x/cast "timestamp" x)
    x))

(defn- datediff
  [unit x y]
  [:datediff (h2x/literal (name unit)) (time-argument x) (time-argument y)])

(defn- week-number
  [start expr]
  (let [origin   (.plusDays (java.time.LocalDate/of 1970 1 1) (first-week-day-number start))
        compiled [:floor [:/
                          (datediff :day [:raw (format "TIMESTAMP '%s 00:00:00'" origin)] expr)
                          [:inline 7.0]]]]
    (when *week-numbers*
      (swap! *week-numbers* conj compiled))
    compiled))

(defn- week-date
  "The first day of the week number in the column `identifier`, in the outer query."
  [start identifier]
  [:to_timestamp_millis
   (h2x/cast "bigint"
             [:+ [:* identifier [:inline (* 7 86400000)]]
              [:inline (* 86400000 (first-week-day-number start))]])])

(defn- plus-units
  ^LocalDateTime [^LocalDateTime t unit ^long amount]
  (case unit
    :minute  (.plusMinutes t amount)
    :hour    (.plusHours t amount)
    :day     (.plusDays t amount)
    :week    (.plusWeeks t amount)
    :month   (.plusMonths t amount)
    :quarter (.plusMonths t (* 3 amount))
    :year    (.plusYears t amount)
    (.plusSeconds t amount)))

(defn- relative-time
  "The time of a relative date in the zone of the session, at the start of its unit, as the Postgres form does in SQL."
  ^LocalDateTime [amount unit]
  (let [now (.truncatedTo (LocalDateTime/now (session-zone)) ChronoUnit/SECONDS)]
    (plus-units (if (bucket-rank unit) (bucket-start unit now) now) unit amount)))

;; ClickHouse clips a time before 1900, with no error. So a relative date before that year compares with another date.
(defn- check-relative-in-range!
  [[_ _ amount unit]]
  (when (and (number? amount) (< (.getYear (relative-time amount unit)) 1900))
    (throw (ex-info "This relative date is before the year 1900, and ClickHouse has no such date. Use a shorter relative date, or a fixed date."
                    {:type :invalid-query}))))

;; Cube has no template for NOW() on ClickHouse, so it computes the function after the query, and that fails on a cube with a rolling
;; window measure. The time of the query is a literal here, as for a relative date.
(defmethod sql.qp/->honeysql [:cube :now]
  [_driver _clause]
  (time-literal (.truncatedTo (LocalDateTime/now (session-zone)) ChronoUnit/SECONDS)))

(defmethod sql.qp/->honeysql [:cube :today]
  [_driver _clause]
  (h2x/cast "date" (time-literal (.truncatedTo (LocalDateTime/now (session-zone)) ChronoUnit/DAYS))))

;; A relative date in weeks has no column, so it cannot use a custom granularity, and a week number is not a time. The time
;; of its first day is a plain literal on each first day of the week.
(defmethod sql.qp/->honeysql [:cube :relative-datetime]
  [driver [_ _opts amount unit :as clause]]
  (check-relative-in-range! clause)
  (if (and (= :week unit) (not= 1 (start-of-week-index)))
    (time-literal (relative-time amount unit))
    ((get-method sql.qp/->honeysql [:postgres :relative-datetime]) driver clause)))

(defn- filter-goes-to-the-database?
  "True if a filter of this stage is SQL for the database and not a filter of Cube: a case-sensitive text filter, a NOT, or each filter
  under a report time zone, except in a query that keeps its date ranges. Cube cannot use a custom granularity next to such a filter."
  []
  (let [stage   sql.qp/*inner-query*
        clauses (concat (when-let [one (:filter stage)] [one]) (:filters stage))]
    (and (seq clauses)
         (or (and (report-zone?) (nil? *keep-date-ranges*))
             (some #(and (vector? %) (#{:contains :starts-with :ends-with :not} (first %)))
                   (mapcat #(tree-seq vector? seq %) clauses))))))

(defmethod sql.qp/date [:cube :week]
  [_driver _unit expr]
  (let [start (start-of-week-index)]
    (if (= 1 start)
      (date-trunc :week expr)
      (if-let [granularity (when-not (filter-goes-to-the-database?)
                             (some-> *bucketed-field* (week-granularity start)))]
        (date-trunc granularity expr)
        (do
          ;; A week number has no time series, so Cube computes a rolling window for one bucket or none.
          (when (= :rolling *keep-date-ranges*)
            (throw (ex-info (str "This query has a rolling window measure, and Cube has weeks from Monday only. A week that starts on another day has no time series here, so "
                                 "the number would be wrong. Add a custom granularity with `interval: 1 week` and the offset of that day to the time dimension, remove a text "
                                 "filter that Cube cannot read, or set the first day of the week to Monday.")
                            {:type :invalid-query})))
          (week-number start expr))))))

(defn- nest-week-numbers
  "Move a query that groups by week number into a subquery, and turn the numbers into dates in the outer query."
  [driver honeysql-form numbers]
  (let [{:keys [select order-by limit offset]} honeysql-form
        expr->alias (into {} (map (juxt first second)) select)
        start       (start-of-week-index)]
    (cond-> {:select (mapv (fn [[expr alias-form]]
                             (if (contains? numbers expr)
                               [(week-date start alias-form) alias-form]
                               [alias-form alias-form]))
                           select)
             :from   [[(dissoc honeysql-form :order-by :limit :offset)
                       [(sql.qp/->honeysql driver (h2x/identifier :table-alias "source"))]]]}
      order-by (assoc :order-by (mapv (fn [[expr direction]] [(get expr->alias expr expr) direction]) order-by))
      limit    (assoc :limit limit)
      offset   (assoc :offset offset))))

(defn- selects-week-number?
  [numbers {:keys [select]}]
  (some numbers (map first select)))

(defn- nest-week-numbers-in-stages
  "`seen` is an atom that turns true if a stage selects a week number. A subquery of a model or a question can select one."
  [driver form numbers seen]
  (if-not (map? form)
    form
    (let [form (cond-> form
                 (:from form) (update :from (fn [from]
                                              (mapv (fn [item]
                                                      (if (and (vector? item) (map? (first item)))
                                                        (update item 0 #(nest-week-numbers-in-stages driver % numbers seen))
                                                        item))
                                                    from))))]
      (if (selects-week-number? numbers form)
        (do (reset! seen true)
            (nest-week-numbers driver form numbers))
        form))))

(defn- nest-all-week-numbers
  [driver honeysql-form week-numbers]
  (let [numbers (set week-numbers)
        seen    (atom false)
        form    (nest-week-numbers-in-stages driver honeysql-form numbers seen)]
    (when-not @seen
      (throw (ex-info (format (str "Cube cannot use a week that starts on %s here, because it cannot add an interval to "
                                   "a time on ClickHouse. Set the first day of the week to Monday in Metabase, or add "
                                   "a custom granularity with `interval: 1 week` and the offset of that day to the "
                                   "time dimension, and group by that week.")
                              (name (driver-api/start-of-week)))
                      {:type :invalid-query})))
    form))

(defn- from-a-table?
  "True if this stage reads a table. A later stage reads the column of a subquery, with its own name."
  [{:keys [from]}]
  (not (map? (ffirst from))))

(defn- group-raw-stage
  "Give each row of this stage the value of the measure. Cube fails an outer filter on a measure of an ungrouped subquery, or
  ignores it. With MEASURE() and GROUP BY of the dimensions, and the primary key among them, each group is one row."
  [{:keys [select] :as form} raw-measures null-columns]
  (let [measure? (fn [[_ alias-form]] (contains? raw-measures alias-form))
        null?    (fn [[_ alias-form]] (contains? null-columns alias-form))]
    (assoc form
           :select (mapv (fn [[compiled alias-form :as item]]
                           (if (measure? item) [[:measure compiled] alias-form] item))
                         select)
           :group-by (into [] (comp (remove measure?) (remove null?) (map first)) select))))

(defn- identifier-names
  "The last part of each identifier in a part of a HoneySQL form."
  [form]
  (into #{}
        (keep (fn [x]
                (when (h2x/identifier? x)
                  (last (nth x 2)))))
        (tree-seq coll? seq form)))

(defn- stage-reads-a-table?
  [form]
  (and (map? form) (:select form) (from-a-table? form)))

(defn- without-table-stages
  "`form` without the stages that read a table, so what is left reads their columns."
  [form]
  (walk/prewalk (fn [x] (if (stage-reads-a-table? x) nil x)) form))

(defn- drop-unused-null-columns
  "Cube cannot plan MEASURE() of a subquery with a filter when the subquery has a NULL column: it fails, or it gives no row. No
  later stage reads such a column, unless it shows raw rows, so the driver leaves it out."
  [{:keys [select] :as form} null-columns used]
  (assoc form :select (filterv (fn [[_ alias-form :as item]]
                                 (or (not (contains? null-columns alias-form))
                                     (contains? used (first (identifier-names item)))
                                     (some used (identifier-names alias-form))))
                               select)))

(def ^:private ^:dynamic *grouped-stages*
  "The stages that give the value of a measure for each row, as [raw grouped] forms with no measure."
  nil)

(defn- restore-nested-measures
  "A measure is an empty column in raw rows, see raw-select-item. An outer stage aggregates the column of a subquery, and
  Cube reads that as the measure, so a subquery must select the measure itself. With `row-measures`, an outer stage filters or
  groups by the column, so the subquery must give the value of each row."
  [honeysql-form raw-measures row-measure-aliases null-columns]
  (if (and (map? honeysql-form) (seq raw-measures))
    (let [used (identifier-names (without-table-stages honeysql-form))]
      (update honeysql-form :from
              (fn [from]
                (walk/postwalk
                 (fn [form]
                   (if (stage-reads-a-table? form)
                     (let [restored (-> form
                                        (drop-unused-null-columns null-columns used)
                                        (update :select
                                                (fn [items]
                                                  (mapv (fn [item]
                                                          (if-let [compiled (when (vector? item) (get raw-measures (second item)))]
                                                            [compiled (second item)]
                                                            item))
                                                        items))))]
                       (if (some (comp row-measure-aliases second) (:select restored))
                         (let [grouped (group-raw-stage restored raw-measures null-columns)]
                           (when *grouped-stages*
                             (swap! *grouped-stages* conj grouped))
                           grouped)
                         restored))
                     form))
                 from))))
    honeysql-form))

(defn- current-meta
  "The Cube data model, or nil. A check that needs it does not stop a query when the REST API is down."
  []
  (try
    (cube.meta/cached-meta (:details (driver-api/database (driver-api/metadata-provider))))
    (catch Exception _ nil)))

(defn- native-questions
  "The answers from Cube that native SQL needs. See cube.native/compile-native."
  [details cube-meta]
  {:keeps-fractions? (cube.probe/keeps-fractions? details cube-meta nil)
   :dates-stay-in-zone? (cube.probe/dates-stay-in-zone? details cube-meta)
   :zone             (when (report-zone?) (java.time.ZoneId/of (str (session-zone))))
   :value-read       (partial cube.probe/value-read-members details cube-meta)
   :rolling-reason   (partial cube.probe/rolling-window-reason details)
   :rolling-zone-reason (when (report-zone?)
                          (fn [measure time-dimension _unit]
                            (cube.probe/rolling-zone-reason details measure time-dimension (str (session-zone)))))
   :time-shift?      (partial cube.probe/time-shift? details cube-meta)
   :wrong-bin-reason (partial cube.probe/wrong-bin-reason details cube-meta)})

(defn- current-meta-or-refuse
  "The data model. Native SQL does not run with no data model, because the driver then cannot read its columns."
  []
  (or (current-meta)
      (throw (ex-info "The driver cannot read the data model of Cube from its REST API, so it cannot check this native SQL. Check the Cube REST URL of this database."
                      {:type :invalid-query}))))

(defn- exact-native-stage
  "A saved native question as a stage of a query. See cube.native/compile-native."
  [{:keys [native params] :as stage}]
  (if (string? native)
    (let [cube-meta (current-meta-or-refuse)
          details   (:details (driver-api/database (driver-api/metadata-provider)))]
      (-> stage
          (assoc :native (cube.native/compile-native cube-meta native (or params []) (native-questions details cube-meta)))
          (dissoc :params)))
    stage))

(defn- query-with-stages
  "`query` with the checks done and the limits added, the measures that a later stage aggregates, the columns that Cube reads as
  MEASURE(), and the measures that a later stage filters or groups by."
  [driver {:keys [stages] :as query}]
  (if (:lib/type query)
    (let [preprocessed (vec (sql.qp/preprocess driver query))]
      (check-stages! preprocessed)
      [(assoc query :stages (limit-subqueries (mapv exact-native-stage stages)))
       (aggregated-measures preprocessed)
       (named-measures preprocessed)
       (row-measure-names preprocessed)
       (needs-date-ranges preprocessed)])
    [query {} {} #{} nil]))

;;; A row of a model. A later stage that filters or groups by a measure column reads the value of each row, and the driver gets it with MEASURE() and GROUP BY of the
;;; dimensions, see group-raw-stage. If the key has NULL or repeats, and no other dimension tells two rows apart, a group is more than one row: COUNT(*) on the model then
;;; misses rows, and the value is the sum of the group. SQL and Metabase on Postgres read each row. Cube has no other way to read the value of a row in a subquery.

(defn- check-grouped-stages!
  "Stop a query if a stage of [[group-raw-stage]] has fewer groups than rows. This asks Cube only for this shape, and each answer is at most one minute old."
  [driver stages]
  (let [details (:details (driver-api/database (driver-api/metadata-provider)))
        text    (fn [form]
                  (let [[sql & args] (sql.qp/format-honeysql driver form)]
                    (cube.native/inline-parameters sql args)))]
    (doseq [{:keys [group-by] :as stage} stages]
      (let [plain      (dissoc stage :limit :order-by :group-by)
            rows-sql   (text (assoc plain :select [[[:count :*] :rows]]))
            groups     (text (assoc plain
                                    :select (vec (map-indexed (fn [i column] [column (keyword (str "c" i))]) group-by))
                                    :group-by group-by))
            groups-sql (when groups (format "SELECT COUNT(*) FROM (%s) AS \"groups\"" groups))]
        (when (and rows-sql groups-sql (not (cube.probe/same-row-count? details rows-sql groups-sql)))
          (throw (ex-info (str "This column of a measure has no value for each row here, because the primary key of the cube has NULL or repeats, and the other columns of the "
                               "model do not tell the rows apart. The driver reads such a value with GROUP BY, so rows would join. Add a column that tells the rows apart to the "
                               "model, or filter or group by a dimension.")
                          {:type :invalid-query})))))))

;;; Rollups that read a part of the rows. See the part on them in metabase.driver.cube.probe.

(defn- table-of-stage
  "The name of the table that this stage of honeysql reads, or nil."
  [{[[table]] :from}]
  (when (h2x/identifier? table)
    (last (nth table 2))))

(defn- past-rollup-filter
  "A filter that holds for each row, on a column of `table` that the rollup does not hold, or nil. Cube cannot read it from the rollup, so it reads the source."
  [cube-meta wrong table]
  (when-let [column (cube.probe/column-past-rollup cube-meta table wrong)]
    (let [identifier (h2x/identifier :field table column)]
      [:or [:is identifier nil] [:is-not identifier nil]])))

(defn- read-source-in-stages
  "`form` with the filter of [[past-rollup-filter]] on each stage that reads a table. Throws if a table with this rollup has no such column."
  [cube-meta wrong partial-tables form]
  (walk/postwalk
   (fn [stage]
     (if-let [table (when (map? stage) (some-> (table-of-stage stage) not-empty))]
       (if (partial-tables (str/lower-case table))
         (if-let [every-row (past-rollup-filter cube-meta wrong table)]
           (update stage :where #(if % [:and % every-row] every-row))
           (throw (ex-info (format "%s Cube would give a part of the rows with no error, and each column of this table is in the rollup, so the driver cannot read the source. Add a date filter in the range of the rollup, or change the rollup."
                                   (:reason wrong))
                           {:type :invalid-query, ::try-two-stages true})))
         stage)
       stage))
   form))

(defn- format-past-wrong-rollup
  "The SQL and the parameters of `form`. If Cube would read it from a rollup that does not hold each row of the query, this makes Cube read the source, which has them."
  [driver form]
  (let [[sql & args] (sql.qp/format-honeysql driver form)
        cube-meta    (current-meta)
        details      (:details (driver-api/database (driver-api/metadata-provider)))
        zone         (str (session-zone))
        wrong        (when-let [text (when cube-meta (cube.native/inline-parameters sql args))]
                       (cube.probe/wrong-rollup details cube-meta text zone))]
    (if-not wrong
      [sql args]
      (let [partial-tables       (cube.meta/tables-with-partial-rollups cube-meta)
            [new-sql & new-args] (sql.qp/format-honeysql driver (read-source-in-stages cube-meta wrong partial-tables form))]
        (when-let [text (cube.native/inline-parameters new-sql new-args)]
          (when-let [still (cube.probe/wrong-rollup details cube-meta text zone)]
            (throw (ex-info (format "%s Cube would give a part of the rows with no error, and it does not read the source for this query. Add a date filter in the range of the rollup, or change the rollup."
                                    (:reason still))
                            {:type :invalid-query, ::try-two-stages true}))))
        [new-sql new-args]))))

;;; Two stages. Cube plans a query in two ways: it reads members with a CubeScan, or it writes SQL of its own, push-down. A view with default_filters has no push-down for a query that
;;; reads a member inside a function. Cube adds up each row of a joined cube once in a SQL aggregation, and it cannot plan an expression that reads one column twice next to COUNT(*).
;;; A rollup does not hold a raw time, so this form also reads the source where a filter cannot make Cube do that.
;;; In each case, an inner stage that reads the plain members with GROUP BY works, and an outer stage computes the rest from it. Cube writes both stages as one SQL query of the
;;; database, so CUBEJS_DB_QUERY_LIMIT does not cut the inner one, and it applies the default_filters. An aggregation of the outer stage then needs the rows of each group:
;;;
;;; - The inner stage has the measure that counts the rows of the first cube of the table, and a conditional aggregation adds up that count.
;;; - A measure comes from its values on smaller groups, with [[cube.meta/roll-up-of-measure]]. A distinct count has no such form, so it keeps its refusal.
;;; - Smaller groups add up only if each row of the cube of the measure is in one group, so the extra columns come from that cube or from a many to one join of it.

(def ^:private row-count-column "mb_row_count")

(defn- no-split!
  []
  (throw (ex-info "no split" {::no-split true})))

(defn- new-uuid [] (str (random-uuid)))

(defn- integer-field?
  [form]
  (and (vector? form) (= :field (first form)) (integer? (nth form 2 nil))))

(defn- inner-column-name
  [[_ {:keys [join-alias]} :as clause]]
  (let [column (:name (stored-field clause))]
    (if join-alias (str join-alias "__" column) column)))

(defn- outer-ref
  "The column of the inner stage for a field of the first stage. It keeps the bucket or the bin, which the outer stage makes."
  [[_ opts :as clause]]
  [:field
   (-> (select-keys opts [:temporal-unit :binning :effective-type])
       (assoc :lib/uuid (new-uuid), :base-type (or (:base-type opts) (:base-type (stored-field clause)))))
   (inner-column-name clause)])

(defn- outer-form
  "`form` with each field as a column of the inner stage. A reference by name is a form that the driver cannot follow here."
  [form]
  (cond
    (integer-field? form)                              (outer-ref form)
    (and (vector? form) (= :field (first form)))       (no-split!)
    (and (vector? form) (= :measure (first form)))     (no-split!)
    (vector? form)                                     (mapv outer-form form)
    :else                                              form))

(def ^:private ^:dynamic *fan-out*
  "True if the groups of the inner stage must count the rows of a one to many join: COUNT(*) is a real count of the rows of the join, and a measure of a cube is not."
  false)

(defn- count-column
  [ctx]
  (reset! (:needs-count ctx) true)
  [:field {:lib/uuid (new-uuid), :base-type :type/BigInteger} row-count-column])

(defn- named
  "`opts` with a name for a clause that has another tag than the clause of the first stage, because a later stage reads a column by its name. Metabase names such a column after
  its tag, and it adds a number to the second one, so the driver gives that name only to the one clause with this tag."
  [opts tag {:keys [later-stages? tags]}]
  (cond
    (:name opts)                                    opts
    (and later-stages? (not= 1 (get tags tag)))     (no-split!)
    (and later-stages? (not (#{:count :avg} tag)))  (no-split!)
    :else                                           (assoc opts :name (name tag))))

(defn- outer-measure
  "The aggregation of a measure from its values on smaller groups."
  [{:keys [measures table-name cube-meta] :as ctx} tag opts arg]
  (let [field (stored-field arg)
        roll  (cube.meta/roll-up-of-measure cube-meta table-name (:name field))]
    ;; Next to a one to many join, the value of a measure in two groups can read one row, so it does not add up.
    (when (or *fan-out* (not (and roll (own-aggregation? tag field))))
      (no-split!))
    (swap! measures assoc (:name field) arg)
    (let [column [:field {:lib/uuid (new-uuid), :base-type (:base-type field)} (:name field)]]
      (if (cube.meta/count-measure? cube-meta table-name (:name field))
        [:coalesce (named opts tag ctx) [roll {:lib/uuid (new-uuid)} column] 0]
        [roll opts column]))))

(defn- outer-aggregation
  [ctx clause]
  (let [[tag opts & [arg second-arg :as args]] (when (vector? clause) clause)
        u      (fn [] {:lib/uuid (new-uuid)})
        times  (fn [x] [:* (u) (outer-form x) (count-column ctx)])
        rows   (fn [] (count-column ctx))
        ;; Metabase core writes :not-null as this form before a driver gets a query.
        not-null (fn [x] [:!= (u) (outer-form x) [:value (assoc (u) :base-type :type/*) nil]])]
    (cond
      (not (vector? clause))                                   clause
      (= :value tag)                                           clause
      (#{:+ :- :* :/} tag)                                     (into [tag opts] (map #(outer-aggregation ctx %)) args)
      (and (#{:sum :avg :min :max :count :distinct} tag) (= :measure (member-kind arg))) (outer-measure ctx tag opts arg)
      (= :count tag)                                           [:coalesce (named opts tag ctx)
                                                                (if arg
                                                                  [:sum-where (u) (rows) (not-null arg)]
                                                                  [:sum (u) (rows)])
                                                                0]
      (#{:min :max :distinct} tag)                             [tag opts (outer-form arg)]
      (= :sum tag)                                             [:sum opts (times arg)]
      (= :cum-sum tag)                                         [:cum-sum opts (times arg)]
      (= :cum-count tag)                                       [:cum-sum opts (if arg
                                                                                [:case (u) [[(not-null arg) (rows)]] 0]
                                                                                (rows))]
      (= :avg tag)                                             [:/ (named opts tag ctx)
                                                                [:sum (u) (times arg)]
                                                                [:sum-where (u) (rows) (not-null arg)]]
      (= :count-where tag)                                     [:sum-where (named opts tag ctx) (rows) (outer-form arg)]
      (= :sum-where tag)                                       [:sum-where opts (times arg) (outer-form second-arg)]
      (= :share tag)                                           [:/ (named opts tag ctx)
                                                                [:sum-where (u) (rows) (outer-form arg)]
                                                                [:sum (u) (rows)]]
      :else                                                    (no-split!))))

(defn- has-segment?
  [clause]
  (some #(= :segment (member-kind %)) (field-clauses [clause])))

(defn- has-time?
  [clause]
  (some #(time-field? (stored-field %)) (filter integer-field? (field-clauses [clause]))))

(defn- filter-stays-inside?
  "`strategy` says which filters of the query the inner stage reads. A segment has no column in a later stage."
  [strategy clause]
  (or (has-segment? clause)
      (case strategy
        :all  true
        :time (has-time? clause)
        :none false)))

(defn- check-folded-columns!
  "See the note on two stages: the groups of the inner stage add up only if each row of the cube of a measure is in one group. A column that the outer stage reads as it is
  folds nothing."
  [{:keys [cube-meta table-name] :as _ctx} dimension-clauses breakout measure-names count?]
  (let [plain     (into #{}
                        (comp (filter integer-field?)
                              (remove #(or (not (contains? #{nil :default} (:temporal-unit (second %))))
                                           (:binning (second %))))
                              (map (juxt #(nth % 2) (comp :join-alias second))))
                        breakout)
        provider  (driver-api/metadata-provider)
        switches  (cube.meta/switch-dimension-names cube-meta table-name)
        cube-of-field (fn [clause]
                        (let [field (stored-field clause)
                              table (:name (driver-api/table provider (:table-id field)))]
                          (cube.meta/cube-of-member cube-meta table (:name field) :dimensions)))
        folded    (remove #(contains? plain ((juxt (fn [c] (nth c 2)) (comp :join-alias second)) %)) dimension-clauses)
        measure-cubes (cond-> (map #(cube.meta/cube-of-member cube-meta table-name % :measures) measure-names)
                        count? (conj (second (cube.meta/row-count-measure cube-meta table-name))))]
    ;; `users.lifetime_paid_orders` by status gave 1301 where a SQL join gives 25130: Cube changes a `sub_query` with the other columns of the query.
    (doseq [clause dimension-clauses
            :let [field (stored-field clause)]]
      (when (cube.meta/dimension-reads-a-member? cube-meta (:name (driver-api/table provider (:table-id field))) (:name field))
        (no-split!)))
    (doseq [clause folded
            :let [dimension (cube-of-field clause)
                  column    (str/lower-case (:name (stored-field clause)))]]
      (when (contains? switches column)
        (no-split!))
      (doseq [measure-cube (when-not *fan-out* measure-cubes)]
        (when-not (and measure-cube dimension
                       (or (= measure-cube dimension)
                           (cube.meta/many-to-one-path? cube-meta measure-cube dimension)))
          (no-split!))))))

(def ^:private ^:dynamic *read-source*
  "True if the two stages must make Cube read the source of a rollup. A rollup does not hold a raw time, so the inner stage groups by the times that its filters read."
  false)

(defn- split-first-stage
  "`stages` with the first stage as two stages, or nil. See the note on two stages."
  [cube-meta stages strategy]
  (try
    (let [{:keys [source-table aggregation breakout filters expressions order-by fields joins] :as stage} (first stages)
          provider   (driver-api/metadata-provider)
          table-name (when (pos-int? source-table) (:name (driver-api/table provider source-table)))]
      (when (and table-name cube-meta (seq aggregation) (empty? fields) (= :mbql.stage/mbql (:lib/type stage)))
        (let [ctx           {:cube-meta     cube-meta
                             :table-name    table-name
                             :measures      (atom {})
                             :needs-count   (atom false)
                             :later-stages? (some? (next stages))
                             :tags          (frequencies (map first aggregation))}
              aggregation'  (mapv #(outer-aggregation ctx %) aggregation)
              inside        (filterv #(filter-stays-inside? strategy %) filters)
              outside       (filterv #(not (filter-stays-inside? strategy %)) filters)
              dimensions    (->> (field-clauses (concat breakout outside expressions order-by aggregation
                                                        (when *read-source* (filter has-time? inside))))
                                 (filter integer-field?)
                                 (remove #(= :measure (member-kind %)))
                                 (remove has-segment?)
                                 (reduce (fn [found clause]
                                           (let [k [(nth clause 2) (:join-alias (second clause))]]
                                             (if (contains? found k) found (assoc found k clause))))
                                         {})
                                 vals)
              _             (check-folded-columns! ctx dimensions breakout (keys @(:measures ctx)) @(:needs-count ctx))
              [count-name]  (when (and @(:needs-count ctx) (not *fan-out*)) (cube.meta/row-count-measure cube-meta table-name))
              _             (when (and @(:needs-count ctx) (not *fan-out*) (not count-name)) (no-split!))
              count-field   (when count-name
                              (first (filter #(= count-name (:name %)) (driver-api/fields provider source-table))))
              _             (when (and @(:needs-count ctx) (not *fan-out*) (not count-field)) (no-split!))
              details       (:details (driver-api/database provider))
              _             (when (and count-name
                                       (when-let [[cube plain] (cube.meta/key-count-measure cube-meta (str table-name "." count-name))]
                                         (cube.probe/null-key-rows? details cube plain)))
                              (no-split!))
              inner         (cond-> {:lib/type    :mbql.stage/mbql
                                     :source-table source-table
                                     :aggregation (cond-> (mapv (fn [[column-name arg]]
                                                                  (let [field (stored-field arg)]
                                                                    [(cube.meta/aggregation-of-measure (str/replace-first (str (:database-type field)) "cube.measure/" "") :max)
                                                                     {:lib/uuid (new-uuid), :name column-name}
                                                                     [:field {:lib/uuid (new-uuid), :base-type (:base-type field)} (:id field)]]))
                                                                @(:measures ctx))
                                                    count-field (conj [:sum {:lib/uuid (new-uuid), :name row-count-column}
                                                                       [:field {:lib/uuid (new-uuid), :base-type (:base-type count-field)} (:id count-field)]])
                                                    (and *fan-out* @(:needs-count ctx)) (conj [:count {:lib/uuid (new-uuid), :name row-count-column}]))}
                              (seq dimensions) (assoc :breakout (mapv (fn [[_ {:keys [join-alias base-type]} id :as clause]]
                                                                        [:field (cond-> {:lib/uuid (new-uuid), :base-type (or base-type (:base-type (stored-field clause)))}
                                                                                  join-alias (assoc :join-alias join-alias))
                                                                         id])
                                                                      dimensions))
                              (seq joins)  (assoc :joins joins)
                              (seq inside) (assoc :filters inside))
              outer         (cond-> (-> (dissoc stage :source-table :joins :filters :expressions :order-by :breakout)
                                        (assoc :aggregation aggregation'))
                              (seq breakout)    (assoc :breakout (outer-form breakout))
                              (seq outside)     (assoc :filters (outer-form outside))
                              (seq expressions) (assoc :expressions (outer-form expressions))
                              (seq order-by)    (assoc :order-by (outer-form order-by)))]
          (when (or (seq dimensions) (seq (:aggregation inner)))
            (into [(cond-> inner (empty? (:aggregation inner)) (dissoc :aggregation)) outer] (rest stages))))))
    (catch clojure.lang.ExceptionInfo e
      (if (::no-split (ex-data e))
        nil
        (throw e)))))

(declare compile-stages)

(defn- compile-two-stages
  "The first SQL of the two stage forms of `query` that Cube can plan, or nil."
  [driver {:keys [stages] :as query} cube-meta]
  (let [details (:details (driver-api/database (driver-api/metadata-provider)))
        zone    (str (session-zone))]
    (some (fn [strategy]
            (when-let [split (split-first-stage cube-meta (vec stages) strategy)]
              (try
                (let [{sql :query, params :params, :as compiled} (compile-stages driver (assoc query :stages split))
                      text (cube.native/inline-parameters sql params)]
                  (when (and text (not (cube.probe/planning-error details text zone)))
                    compiled))
                (catch Exception e
                  (log/debugf e "Cube cannot compile the two stage form of a query")
                  nil))))
          ;; A filter of an outer stage on a time reads its literal in another zone than the column under a report time zone. See check-no-time-filter-over-aggregation-in-zone!.
          (if (report-zone?) [:all :time] [:all :time :none]))))

(defn- merged-raw-stage
  "`stages` with a first stage of raw rows and the stage after it as one stage, or nil. Cube reads the two as one query, so an aggregation of a joined column in the second stage has the
  same problem as in one stage, and one stage has the form of [[split-first-stage]]. Nil if the first stage does more than select columns and filter, or if a column has no field."
  [[{:keys [fields] :as first-stage} {:keys [aggregation] :as second-stage} & more]]
  (when (and (= :mbql.stage/mbql (:lib/type first-stage))
             (= :mbql.stage/mbql (:lib/type second-stage))
             (seq aggregation)
             (seq fields)
             (every? integer-field? fields)
             (not-any? first-stage [:aggregation :breakout :expressions :order-by :limit]))
    (let [column-names (fn [[_ opts :as clause]]
                         (keep identity [(get opts driver-api/qp.add.desired-alias) (inner-column-name clause) (:lib/desired-column-alias opts) (:name opts) (:name (stored-field clause))]))
          ;; Metabase core finds a column by its first match, so the first one of a name stays.
          by-name      (reduce (fn [found [column-name clause]]
                                 (if (contains? found column-name) found (assoc found column-name clause)))
                               {}
                               (for [clause fields, column-name (column-names clause)] [column-name clause]))
          missing?     (atom false)
          merged       (walk/postwalk
                        (fn [form]
                          (if (and (vector? form) (= :field (first form)) (string? (nth form 2 nil)))
                            (if-let [[_ selected-opts id] (get by-name (nth form 2))]
                              [:field (merge (dissoc selected-opts :lib/uuid) (select-keys (second form) [:lib/uuid :temporal-unit :binning])) id]
                              (do (reset! missing? true) form))
                            form))
                        (dissoc second-stage :lib/type))]
      (when-not @missing?
        (into [(cond-> (assoc merged
                              :lib/type :mbql.stage/mbql
                              :source-table (:source-table first-stage))
                 (seq (:joins first-stage))    (assoc :joins (:joins first-stage))
                 (or (seq (:filters first-stage))
                     (seq (:filters merged)))  (assoc :filters (into (vec (:filters first-stage)) (:filters merged))))]
              more)))))

(defn- may-fail-to-plan?
  "True if Cube may fail to plan this first stage, so the driver asks it. See the note on two stages."
  [cube-meta {:keys [source-table aggregation expressions breakout filters]}]
  (and cube-meta
       (pos-int? source-table)
       (seq aggregation)
       (or (seq expressions)
           (some (fn [[_ {:keys [temporal-unit]}]]
                   (and temporal-unit (not= :default temporal-unit) (not (bucket-rank temporal-unit))))
                 (filter integer-field? breakout))
           (and (cube.meta/view? cube-meta (:name (driver-api/table (driver-api/metadata-provider) source-table)))
                (or (seq filters)
                    (some (fn [[_ _ arg]] (not= :measure (member-kind arg))) aggregation))))))

(defmethod driver/mbql->native :cube
  [driver query]
  (if-not (and (:lib/type query) (seq (:stages query)))
    (compile-stages driver query)
    (let [cube-meta (current-meta)
          stage     (first (:stages query))
          details   (:details (driver-api/database (driver-api/metadata-provider)))]
      (try
        (let [{sql :query, params :params, :as compiled} (compile-stages driver query)
              text (when (may-fail-to-plan? cube-meta stage) (cube.native/inline-parameters sql params))]
          (or (when (and text (cube.probe/planning-error details text (str (session-zone))))
                (compile-two-stages driver query cube-meta))
              compiled))
        (catch clojure.lang.ExceptionInfo e
          (or (when (::try-two-stages (ex-data e))
                (binding [*read-source* true
                          *fan-out*     (boolean (::fan-out (ex-data e)))]
                  (or (compile-two-stages driver query cube-meta)
                      (when-let [merged (merged-raw-stage (:stages query))]
                        (compile-two-stages driver (assoc query :stages merged) cube-meta)))))
              (throw e)))))))

(defn- time-measure-columns
  "The names of the columns of `form` with MEASURE() of a time measure of the first stage. Cube gives such a value as text, with no type of a time. See read-column-thunk for text."
  [form stages]
  (let [table-id (:source-table (first stages))
        times    (when (pos-int? table-id)
                   (into #{}
                         (comp (filter #(and (= :measure (field-kind %)) (isa? (:base-type %) :type/Temporal)))
                               (map :name))
                         (driver-api/fields (driver-api/metadata-provider) table-id)))]
    (when (seq times)
      (into #{}
            (keep (fn [item]
                    (when (and (vector? item) (= 2 (count item)))
                      (let [[compiled alias-form] item
                            compiled (h2x/unwrap-typed-honeysql-form compiled)]
                        (when (and (vector? compiled) (= :measure (first compiled))
                                   (some times (identifier-names compiled)))
                          (first (identifier-names alias-form)))))))
            (:select form)))))

(defn- compile-stages
  [driver query]
  (binding [*week-numbers* (atom [])
            *raw-measures* (atom {})
            *null-columns* (atom #{})
            *row-measure-aliases* (atom #{})]
    (let [[query aggregated named row-measures date-ranges] (query-with-stages driver query)
          honeysql-form (binding [*aggregated-measures* (into (set (keys aggregated)) row-measures)
                                  *named-measures*      named
                                  *row-measures*        row-measures
                                  *keep-date-ranges*    date-ranges]
                          (sql.qp/mbql->honeysql driver query))
          grouped-stages (atom [])
          honeysql-form (binding [*grouped-stages* grouped-stages]
                          (restore-nested-measures honeysql-form @*raw-measures* @*row-measure-aliases* @*null-columns*))
          _             (check-grouped-stages! driver @grouped-stages)
          honeysql-form (if (and (map? honeysql-form) (seq @*week-numbers*))
                          (nest-all-week-numbers driver honeysql-form @*week-numbers*)
                          honeysql-form)
          [sql args]    (format-past-wrong-rollup driver honeysql-form)
          times         (when (map? honeysql-form) (time-measure-columns honeysql-form (:stages query)))]
      (cond-> {:query sql, :params args}
        (seq times) (assoc :cube/time-measure-columns times)))))

;;; Parts of a date. Cube runs EXTRACT over a CubeScan as post-processing, which reads at most CUBEJS_DB_QUERY_LIMIT rows
;;; of a smaller grain, and which fails in a filter or with a measure that is not additive. DATEDIFF goes to the
;;; database as SQL push-down, with no such limits.

(defn- since
  "The units of `unit` from the start of the `period` to `expr`."
  [unit period expr]
  (datediff unit (date-trunc period expr) expr))

(defmethod sql.qp/date [:cube :second-of-minute] [_ _ expr] (since :second :minute expr))
(defmethod sql.qp/date [:cube :minute-of-hour]   [_ _ expr] (since :minute :hour expr))
;; DATEDIFF counts boundaries of hours in absolute time. In a zone with an offset of part of an hour, each row is one hour too high, and on the day
;; of a clock change the rows move. The text of the time has the hour on the clock, and it goes to the database with no bucket.
;; Stock Cube has no template for SPLIT_PART and REGEXP_SUBSTR on ClickHouse, and the ClickHouse dialect adds them.
(defn- need-text-function
  [template? function]
  (when-not (some-> (driver-api/database (driver-api/metadata-provider)) :details not-empty template?)
    (throw (ex-info (format (str "Stock Cube cannot compute %s on ClickHouse. Add a dimension with that SQL to the data model, or install "
                                 "the ClickHouse dialect, see cube-clickhouse/README.md.")
                            function)
                    {:type :invalid-query}))))

;; The Postgres form is a CASE around the call. The position is a number of 1 or more in MBQL, so the call alone is enough.
(defmethod sql.qp/->honeysql [:cube :split-part]
  [driver [_ _opts text divider position]]
  (need-text-function cube.probe/split-part? "splitPart")
  [:split_part (sql.qp/->honeysql driver text) (sql.qp/->honeysql driver divider) (sql.qp/->honeysql driver position)])

;; `substring(x FROM pattern)` of Postgres reads as a position in Cube, so this is REGEXP_SUBSTR.
(defmethod sql.qp/->honeysql [:cube :regex-match-first]
  [driver [_ _opts arg pattern]]
  (need-text-function cube.probe/regex? "a regular expression, such as regexExtract, host, domain, subdomain and path,")
  [:regexp_substr (sql.qp/->honeysql driver arg) (sql.qp/->honeysql driver pattern)])

(defmethod sql.qp/date [:cube :hour-of-day]
  [_ _ expr]
  (if (report-zone?)
    ;; ClickHouse stops on a cast of NULL to text.
    [:case [:not= expr nil] (h2x/cast "double precision" [:substr (h2x/cast "text" expr) [:inline 12] [:inline 2]])]
    (since :hour :day expr)))
(defmethod sql.qp/date [:cube :day-of-month]     [_ _ expr] [:+ (since :day :month expr) [:inline 1]])
(defmethod sql.qp/date [:cube :day-of-year]      [_ _ expr] [:+ (since :day :year expr) [:inline 1]])
(defmethod sql.qp/date [:cube :month-of-year]    [_ _ expr] [:+ (since :month :year expr) [:inline 1]])
(defmethod sql.qp/date [:cube :quarter-of-year]  [_ _ expr] [:+ (since :quarter :year expr) [:inline 1]])

(defmethod sql.qp/date [:cube :year-of-era]
  [_ _ expr]
  [:+ (datediff :year [:raw "TIMESTAMP '1970-01-01 00:00:00'"] expr) [:inline 1970]])

(defn- weekday-from
  "The day of the week, from 0 for the day `start`. DATE_TRUNC('week') gives Monday, which has index 1."
  [start expr]
  [:% [:+ (since :day :week expr) [:inline (mod (- 1 start) 7)]] [:inline 7]])

;; Metabase counts days from 1, from the first day of the week in the setting.
(defmethod sql.qp/date [:cube :day-of-week]
  [_driver _unit expr]
  [:+ (weekday-from (start-of-week-index) expr) [:inline 1]])

;; The mode `iso` of get-day-of-week counts from Monday for each setting. The method of the parent binds the first day and calls :day-of-week,
;; and that binding does not reach `start-of-week-index`.
(defmethod sql.qp/date [:cube :day-of-week-iso]
  [_driver _unit expr]
  [:+ (weekday-from 1 expr) [:inline 1]])

(defn- week-of-year-from
  "Week 1 starts on 1 January and week 2 on the first day `start` after it."
  [start expr]
  (let [days-before (since :day :year expr)
        first-day   [:% [:+ [:% [:- (weekday-from start expr) days-before] [:inline 7]] [:inline 7]] [:inline 7]]]
    [:+ [:floor [:/ [:+ days-before first-day] [:inline 7.0]]] [:inline 1]]))

;; Metabase core defines this unit as ceil(day-of-year(week(x)) / 7), so it is the same number on each database. A week can start in the year
;; before, and Cube cannot add an interval to a time, so this reads the day of the year where the week starts. 0 or less is a day of the year
;; before. That year has 365 or 366 days, so the week is 53 for the last day of that year and for
;; the day before it in a long year, and 52 for each earlier day.
(defn- long-year-before
  [expr]
  (let [year-before [:+ (datediff :year [:raw "TIMESTAMP '1970-01-01 00:00:00'"] expr) [:inline 1969]]
        divides?    (fn [n] [:= [:% year-before [:inline n]] [:inline 0]])]
    [:and (divides? 4) [:or [:not (divides? 100)] (divides? 400)]]))

(defn- week-of-year-of-week-start
  "`day` is the day of the year where the week starts, from 1."
  [start expr]
  (let [day [:+ [:- (since :day :year expr) (weekday-from start expr)] [:inline 1]]]
    [:case
     [:>= day [:inline 1]]                                       [:floor [:/ [:+ day [:inline 6]] [:inline 7.0]]]
     [:or [:= day [:inline 0]]
      [:and [:= day [:inline -1]] (long-year-before expr)]]      [:inline 53.0]
     :else                                                       [:inline 52.0]]))

(defmethod sql.qp/date [:cube :week-of-year]
  [_driver _unit expr]
  ;; The ELSE of this CASE would give a row with no time a week.
  [:case [:not= expr nil] (week-of-year-of-week-start (start-of-week-index) expr)])

(defmethod sql.qp/date [:cube :week-of-year-instance]
  [_driver _unit expr]
  (week-of-year-from (start-of-week-index) expr))

(defmethod sql.qp/date [:cube :week-of-year-us]
  [_driver _unit expr]
  (week-of-year-from 0 expr))

;; The ISO week of the first and last days of a year needs the length of the year next to it, so this one part stays as
;; EXTRACT. The SQL API runs it as post-processing over rows of one day.
(defmethod sql.qp/date [:cube :week-of-year-iso] [_ _ expr] [::h2x/extract :week expr])

;;; Numbers and times that the Postgres driver writes in a form that means something else on Cube.

;; Postgres LOG(x) has base 10. Cube sends LOG(x) to ClickHouse, where it is the natural logarithm, and LOG(10, x) does
;; not plan.
(defmethod sql.qp/->honeysql [:cube :log]
  [driver [_ _opts arg]]
  [:/ [:ln (sql.qp/->honeysql driver arg)] [:ln [:inline 10]]])

;; ClickHouse LAG gives the default of the type on the first row of a column with no Nullable, so an earlier month that
;; does not exist would show 0. SQL has NULL there.
(defmethod sql.qp/->honeysql [:cube :offset]
  [driver [_ _opts _expr n :as clause]]
  (let [compiled ((get-method sql.qp/->honeysql [:postgres :offset]) driver clause)
        [op [_ window]] (h2x/unwrap-typed-honeysql-form compiled)]
    (if (and (= :over op) (neg? n))
      [:case [:> [:over [[:row_number] window]] [:inline (- n)]] compiled]
      compiled)))

;; TRIM makes Cube fail with "Date range is required for time series" on a plain select. The two sided form does not.
(defmethod sql.qp/->honeysql [:cube :trim]
  [driver [_ _opts arg]]
  [:ltrim [:rtrim (sql.qp/->honeysql driver arg)]])

;; DATEDIFF counts the boundaries between two times. That is the Metabase result for days. For smaller units, Metabase
;; counts whole units, so the driver divides the seconds. The Cube templates have no way to count whole months.
(defn- whole-units
  [x y seconds]
  [:trunc [:/ (datediff :second x y) [:inline (double seconds)]]])

(defmethod sql.qp/datetime-diff [:cube :second] [_driver _unit x y] (datediff :second x y))
(defmethod sql.qp/datetime-diff [:cube :minute] [_driver _unit x y] (whole-units x y 60))
(defmethod sql.qp/datetime-diff [:cube :hour]   [_driver _unit x y] (whole-units x y 3600))
(defmethod sql.qp/datetime-diff [:cube :day]    [_driver _unit x y] (datediff :day x y))
(defmethod sql.qp/datetime-diff [:cube :week]   [_driver _unit x y] [:trunc [:/ (datediff :day x y) [:inline 7.0]]])

(doseq [unit [:month :quarter :year]]
  (defmethod sql.qp/datetime-diff [:cube unit]
    [_driver _unit _x _y]
    (throw (ex-info (format "Cube cannot count whole %ss between two times. Use days, or a dimension in the data model."
                            (name unit))
                    {:type :invalid-query}))))

;; Two equal literals in one expression make the SQL API fail with "Schema contains duplicate unqualified field name".
;; A cast of a text has no such name, and Cube sends it to the database as it is.
(defn- number-literal
  [number]
  (h2x/cast "double precision" (h2x/literal (str (double number)))))

;; A cast of a measure goes to ClickHouse, and it fails on NULL with `Cannot convert NULL value to non-Nullable type`, so a measure of a
;; group with no value stops the query. SQL gives NULL. A product with a double keeps NULL. A plain 1.0 is a decimal in Cube, and it cuts
;; the digits of a Decimal.
(defmethod sql.qp/->float :cube
  [_driver value]
  [:* value (number-literal 1.0)])

(defn- apply-binning
  [{:keys [bin-width min-value]} expr]
  (let [shifted (if (zero? min-value) expr [:- expr (number-literal min-value)])
        binned  [:* [:floor [:/ shifted [:inline (double bin-width)]]] (number-literal bin-width)]]
    (if (zero? min-value) binned [:+ binned [:inline (double min-value)]])))

;; These helpers in the util namespace have one branch for each driver and no branch for :cube.
(defmethod sql.qp/current-datetime-honeysql-form :cube
  [_driver]
  (h2x/current-datetime-honeysql-form :postgres))

(defmethod sql.qp/add-interval-honeysql-form :cube
  [_driver hsql-form amount unit]
  (h2x/add-interval-honeysql-form :postgres hsql-form amount unit))

;;; --------------------------------------------------- errors ----------------------------------------------------

(def ^:private cube-errors
  "Texts of Cube errors, and what they mean for a person who built a question."
  [["PERCENTILE_CONT"
    (str "Cube cannot compute a median or a percentile on this data source. Add a measure with the SQL of the database "
         "to the data model.")]
   ["percentile_cont"
    (str "Cube cannot compute a median or a percentile on this data source. Add a measure with the SQL of the database "
         "to the data model.")]
   ["Can't find join path"
    (str "Cube has no join path between these tables in the direction of this query. A join starts at the cube that holds the join in the data model. Start from that cube, "
         "or add the join to the data model.")]
   ["Invalid identifier"
    (str "This table has no column with that name. Call browse_data with action get_fields to read the columns of the "
         "table. A column of another cube is not on this table: use a view that has it, or write "
         "FROM a CROSS JOIN b WHERE a.__cubeJoinField = b.__cubeJoinField and read a.column and b.column.")]
   ["Can't detect Cube query"
    (str "Cube cannot plan this query. EXTRACT on a time dimension next to a measure, a function that the SQL API does "
         "not know, and a JOIN with an ON condition are the common cause. On a view with default_filters, each such query also needs measures that add up over groups, "
         "such as sum, count, min or max, and not a distinct count or an average. Use a time bucket, or add a dimension for it "
         "to the data model. For a join, write FROM a CROSS JOIN b WHERE a.__cubeJoinField = b.__cubeJoinField.")]
   ["You cannot change security context"
    "This connection gives data per person, so a query cannot read as another person."]
   ["is not allowed to switch to"
    "This connection gives data per person, so a query cannot read as another person."]
   ["password authentication failed"
    (str "Cube refused the sign-in. Check the password of the connection. With per-user data, also check that "
         "checkSqlAuth in cube.js knows this person.")]
   ["Can't generate SQL for interval"
    (str "Cube cannot add an interval to a time in an aggregated query on this data source. Remove the date arithmetic, "
         "or add a dimension with that SQL to the data model.")]
   ["MessageTooLargeError"
    "Cube Store cannot send this many rows in one message. Add a filter or an aggregation, or add LIMIT to the query."]
   ["Queue storage error"
    (str "The queue of Cube failed on this query. A result with about a million rows or more is the common cause on Cube Store. Add a filter, an "
         "aggregation or a LIMIT.")]
   ["externalDriverFactory is not provided"
    (str "Cube reads this query from a rollup, and this Cube has no Cube Store to hold rollups. Run Cube with Cube Store, or in dev mode, "
         "or remove the rollup from the data model.")]
   ["clientFactory is not a function"
    (str "Cube reads this query from a rollup, and this Cube has no Cube Store to hold rollups. Run Cube with Cube Store, or in dev "
         "mode, or remove the rollup from the data model.")]
   ;; Cube says "Function with name `numberAgg` does not exist".
   ["numberAgg"
    (str "The legacy planner of Cube has no form for this measure. Run Cube with CUBEJS_TESSERACT_SQL_PLANNER=true, or change the measure in "
         "the data model.")]
   ;; The legacy planner on a measure of a cube that it cannot join, such as nk_count of the demo.
   ["Cannot read properties of null (reading 'joins')"
    (str "The legacy planner of Cube cannot plan this measure. Run Cube with CUBEJS_TESSERACT_SQL_PLANNER=true, the default since Cube v1.7.0, or change the "
         "measure in the data model.")]
   ["Time series queries without dateRange aren't supported"
    (str "The legacy planner of Cube needs a date range for a rolling window or a time bucket of this measure. Add a filter on the time column, "
         "such as WHERE created_at >= '2025-01-01' AND created_at < '2026-01-01', or run Cube with CUBEJS_TESSERACT_SQL_PLANNER=true.")]
   ["required to be pushed down"
    (str "Cube cannot run this query, because it needs MEASURE() and post-processing at the same time. Use a plain "
         "aggregation in the first stage, or add a measure to the data model.")]
   ["Measure aggregation type doesn't match"
    "This measure has its aggregation in the data model. Use the measure, not another aggregation of its column."]
   ["Can't use OR operator with segments"
    (str "Cube cannot join a segment to another filter with OR. Use one segment, or add a segment with both conditions to "
         "the data model.")]
   ["No field named"
    (str "This column is not on the table of the query. For a segment or a member of another cube, use a view that has it, "
         "or add it to the data model of this table.")]
   ["Cannot determine join keys in JOIN ON expression"
    (str "Cube cannot compute a rolling window with `trailing: unbounded` for more than one bucket on ClickHouse. Use one "
         "bucket, or a window with a size in the data model.")]
   ["approximate distinct count is not supported"
    (str "A rollup holds this count_distinct_approx measure, and Cube cannot store it for ClickHouse. Remove the measure from the "
         "rollup in the data model. The rollup also stops each run of the refresh worker of Cube.")]
   ["doesn't support pre-aggregations without indexes"
    (str "Stock Cube cannot build this rollup on ClickHouse, because it has no index. Add an index to the rollup in the data "
         "model, or install the ClickHouse dialect, see cube-clickhouse/README.md.")]
   ["Unknown table expression identifier"
    (str "Stock Cube cannot build a rollup with use_original_sql_pre_aggregations on ClickHouse. Remove the option in the data "
         "model, or install the ClickHouse dialect, see cube-clickhouse/README.md.")]
   ["Stream query failed"
    (str "Cube cannot read a rollup_lambda on ClickHouse with a range that reads source rows. Add a date "
         "filter inside the range of the rollup, or read this measure from a plain rollup.")]
   ["doesn't look like datepart name in dateTrunc"
    (str "Cube cannot use a custom granularity next to this filter, such as NOT of a group. Change the filter, or set the first day of "
         "the week to Monday in Metabase.")]
   ["was used with the aggregate function"
    (str "Cube cannot aggregate a dimension of a view next to a measure, or a dimension that comes from another cube. Add a measure to "
         "the data model, or start from the cube of this dimension.")]
   ["lead to row multiplication"
    (str "Cube cannot add up an expression on columns of two joined cubes, because a join multiplies rows. Add a measure to the data "
         "model, or start from one cube.")]
   ["out-of-range datetime"
    (str "Cube cannot read a date this far from today. Use a shorter relative date, or a fixed date.")]
   ["Cannot evaluate binary expression"
    (str "Cube cannot compare this time with a time of a later stage, because one has a time zone and the other has none. Put the filter "
         "inside the first stage, or use a time bucket.")]
   ["accumulator not implemented"
    (str "Cube cannot compute min or max of a true or false column in a later stage. Use the measure in the first stage, or filter on it.")]
   ["is not under aggregate function"
    (str "Cube wrote SQL that ClickHouse cannot run for this cube, with a filter inside the first stage and an aggregation in the next. "
         "Put the filter in the last stage, or use one stage.")]
   ["duplicate unqualified field name"
    (str "Cube cannot plan this query, because two parts of it read as one name, such as two bins or two equal numbers. Use a measure in place of an "
         "aggregation, or add a dimension with this SQL to the data model.")]
   ["of argument of function"
    (str "ClickHouse cannot run this function on the type of this column, such as a filter that ignores case on an Enum. Add a dimension "
         "with toString(column) to the data model, or use a filter that keeps the case.")]
   ["not found in provided schemas"
    (str "Cube cannot plan this expression in an aggregation. Add a measure to the data model, or use a plain filter.")]
   ["AST node limit"
    (str "This query is too long for the planner of Cube, which stops at about 10000 nodes. A list in IN or in a filter on one column works up to 2000 values, and conditions joined with OR or AND "
         "stop at about 50. Use a shorter list, a range, or add a segment to the data model.")]
   ["Cannot convert NULL value to non-Nullable type"
    (str "Cube writes a cast of a column with no Nullable on ClickHouse, so it stops on a row with NULL. Remove the cast, or write COALESCE(x, 0) inside it.")]
   ["duplicate qualified field name"
    (str "Cube cannot plan a custom column that reads one column twice next to this aggregation. Use a measure of the data model that adds up, such as sum or count, or add a "
         "dimension for the custom column to the data model.")]
   ["Invalid function"
    (str "The SQL API of Cube does not know a function of this query. Add a dimension with that SQL to the data model.")]
   ["unsupported type Utf8View"
    (str "Cube cannot compute this text function. Add a dimension with that SQL to the data model.")]
   ["user-defined coercion failed"
    (str "Cube cannot compute this text function. Add a dimension with that SQL to the data model.")]
   ["cannot compile re2"
    (str "ClickHouse cannot run a regular expression with a lookahead or a lookbehind, and the functions host, domain, subdomain and "
         "path use one. Add a dimension with that SQL to the data model.")]
   ["Date range is required for time series"
    (str "Cube needs a date range for this query. A rolling window measure by a time bucket needs a date filter on the "
         "same time column. Add a date filter.")]
   ;; Metabase keeps one set of tables for all people. Cube gives this when the security context of the person hides the
   ;; table, such as the access_policy of a view, or when the table left the data model after the last sync.
   ["Table or CTE with name"
    (str "Cube has no such table for this person. The data model hides it from this person, for example with the access_policy "
         "of a view, or the table left the data model after the last sync of Metabase. Ask the owner of the data model for access, "
         "or sync the database.")]
   ;; The last entries: each other error of the planner of Cube.
   ["Planning Error"
    "Cube cannot plan this query. A column or a function of it is not on this table for the SQL API. Use another column, or add a dimension with that SQL to the data model."]
   ["Rewrite Error"
    "Cube cannot plan this query. Use a simpler form, or add a measure or a dimension with that SQL to the data model."]])

(defn- exception-text
  "The messages of an exception and its causes."
  [^Throwable e]
  (str/join " " (keep (fn [^Throwable t] (.getMessage t))
                      (take-while some? (iterate (fn [^Throwable t] (.getCause t)) e)))))

(defn- construct-error
  "A message that names the construct of native SQL behind a general error of Cube, or nil. A general message about a rolling window or a function sends a person to the wrong place."
  [message sql]
  (when (string? sql)
    (let [planning? (some #(str/includes? message %) ["Can't detect Cube query" "Planning Error" "Rewrite Error" "Scalar subquery"])]
      (cond
        (and (str/includes? message "Date range is required for time series")
             (re-find #"(?i)\bover\s*\(|\bdistinct\s+on\b" sql))
        (str "Cube cannot compute a window function or DISTINCT ON over the rows of a cube with a rolling window measure, because it reads each measure of that cube here, and a rolling window needs a date "
             "range. Group by a column first and use the window over the groups, or read a view with no rolling window measure.")

        (and planning? (re-find #"(?i)\bexists\s*\(" sql))
        "Cube cannot plan EXISTS here. It has no form for a subquery that reads other rows for each row. Use IN with a subquery, or a join of the data model."

        (and planning? (re-find #"(?is)^\s*select\b[^;]*?\(\s*select\b" sql)
             (not (re-find #"(?is)^\s*select\b.*?\bfrom\s*\(\s*select\b" sql)))
        "Cube cannot plan a subquery in the select next to an aggregation. Read each number in its own query, or read the subquery in FROM."

        (and (str/includes? message "Unable to map value") (str/includes? message "Interval"))
        "Cube cannot give an interval as a result, such as the difference of two times. Use DATEDIFF, which gives a number."

        (str/includes? message "Divide by zero")
        "A divisor in this query is 0, and Cube stops on it above the database. Write NULLIF(x, 0) as the divisor."))))

(defn- clear-error
  ([message] (clear-error message nil))
  ([message sql]
   (if-let [text (construct-error message sql)]
     (str text " Cube said: " message)
     (some (fn [[needle text]]
             (when (str/includes? message needle)
               (str text " Cube said: " message)))
           cube-errors))))

(defn- native-only?
  [{:keys [stages type]}]
  (if (seq stages)
    (every? #(= :mbql.stage/native (:lib/type %)) stages)
    (= :native type)))

(defn- sync-query?
  "True if this query only reads samples of a table for sync, which show no number to a person."
  [query]
  (contains? #{:table-rows-sample :fingerprint} (-> query :info :context)))

(defn- check-view-on-legacy-planner!
  "Stops a query on a view when Cube plans with its legacy planner, which drops the default_filters of a view. The driver cannot see which views have them. See
  cube.probe/legacy-planner?."
  [query]
  (when-not (sync-query? query)
    (when-let [cube-meta (current-meta)]
      (let [tables (set (map (comp str/lower-case second)
                             (re-seq #"(?i)\b(?:from|join)\s+(?:\"?\w+\"?\s*\.\s*)?\"?(\w+)\"?" (str (get-in query [:native :query])))))
            views  (filter #(and (= "view" (:type %)) (tables (str/lower-case (:name %)))) (:cubes cube-meta))]
        (when (and (seq views)
                   (cube.probe/legacy-planner? (:details (driver-api/database (driver-api/metadata-provider))) cube-meta))
          (throw (ex-info (format (str "This Cube plans with its legacy planner (CUBEJS_TESSERACT_SQL_PLANNER=false), which drops the default_filters of a view, so a number of the "
                                       "view %s could count rows outside its filter with no error. Run Cube with the Tesseract planner, the default since Cube v1.7.0, or query the cube "
                                       "of the view.")
                                  (:name (first views)))
                          {:type :invalid-query})))))))

(defn- check-native-rollup!
  "Native SQL has no stage to add a filter to, so the driver stops it if Cube would read it from a rollup that does not hold each row of the query."
  [details cube-meta sql]
  (when-let [{:keys [reason]} (cube.probe/wrong-rollup details cube-meta sql (str (session-zone)))]
    (throw (ex-info (format "%s Cube would give a part of the rows with no error. Add a date filter in the range of the rollup that fits its buckets, or a filter on a column that the rollup does not hold, so that Cube reads the source."
                            reason)
                    {:type :invalid-query}))))

(defn- check-native-count-of-null-key!
  "See [[check-no-count-of-null-key!]]. MEASURE() names a measure of type count of a table in this text."
  [details cube-meta sql]
  (let [words    (set (map str/lower-case (re-seq #"\w+" sql)))
        measures (map (comp str/lower-case second)
                      (re-seq #"(?i)\bmeasure\s*\(\s*(?:\"?\w+\"?\s*\.\s*)?\"?(\w+)\"?\s*\)" sql))]
    (doseq [cube    (:cubes cube-meta)
            :when   (words (str/lower-case (:name cube)))
            measure measures
            :let    [[cube-name plain] (cube.meta/key-count-measure cube-meta (str (:name cube) "." measure))]
            :when   (and plain (cube.probe/null-key-rows? details cube-name plain))]
      (throw (ex-info (format (str "%s counts the primary key of the cube, and some rows have a NULL key, so it would miss these rows. Use COUNT(*), or give the cube a key "
                                   "with no NULL.")
                              measure)
                      {:type :invalid-query})))))

(defn- check-sql!
  "Stops a query that the query builder wrote in a shape that gives a wrong number. Sync reads samples of a table and does not need this."
  [{{sql :query} :native, :as query}]
  (when-not (or (sync-query? query) (native-only? query))
    (when-let [cube-meta (current-meta)]
      (cube.native/check-no-private-member! cube-meta sql)
      (cube.native/check-paths! cube-meta sql))))

(defn- native-row-limit
  "The number of rows that Metabase keeps of this native query, or nil if it keeps each row."
  [query]
  (try
    (qp.limit/determine-query-max-rows query)
    (catch Exception e
      (log/debugf e "Could not read the row limit of a native query")
      nil)))

(defn- with-native-grammar
  "A native query in the form that goes to Cube, or a refusal. See cube.native/compile-native."
  [{{sql :query, params :params} :native, :as query}]
  (if (and (string? sql) (not (sync-query? query)) (native-only? query))
    (let [cube-meta (current-meta-or-refuse)
          details   (:details (driver-api/database (driver-api/metadata-provider)))
          compiled  (cube.native/compile-native cube-meta sql (or params []) (native-questions details cube-meta))]
      (check-native-rollup! details cube-meta compiled)
      (check-native-count-of-null-key! details cube-meta compiled)
      (-> query
          (assoc-in [:native :query] (cube.native/with-row-limit compiled (native-row-limit query)))
          (assoc-in [:native :params] nil)))
    query))

(def ^:private download-rows
  "Fewer rows than this fit in one message of Cube Store. Metabase asks for 2000 rows on screen and for about a million in a download."
  100000)

(defn- check-result-size!
  "Stops a query with a result that is too large for Cube Store, which would hang. See cube.probe/too-large-result."
  [query]
  (let [n     (native-row-limit query)
        own   (some-> (get-in query [:native :query]) cube.native/top-limit)
        limit (if (and n own) (min n own) (or n own))]
    (when (and (not (sync-query? query)) (or (nil? limit) (> limit download-rows)))
      (when-let [cube-meta (current-meta)]
        (let [details (:details (driver-api/database (driver-api/metadata-provider)))
              sql     (cube.native/inline-parameters (get-in query [:native :query]) (get-in query [:native :params]))]
          (when-not (cube.probe/dev-mode? details cube-meta)
            (when-let [{:keys [rows bytes rows-that-fit unknown]} (cube.probe/too-large-result details cube-meta sql)]
              (throw (ex-info (if unknown
                                (str "This Cube runs with dev mode off, so it keeps each result in Cube Store, as one message of at most 64 MiB. The driver counts the rows of a large "
                                     "result first, and Cube gave no answer to that count (" unknown "), so the query did not run: a result that is too large hangs with no end. "
                                     "Add a filter, an aggregation or a LIMIT of at most 100000 rows, or try again when Cube is less busy.")
                                (format (str "This Cube runs with dev mode off, so it keeps each result in Cube Store, as one message of at most 64 MiB. This result has %d rows, about %d MB, "
                                             "so Cube would retry it with no end and the query would hang. Add a filter or an aggregation, or LIMIT the rows to about %d.")
                                        rows (quot bytes (* 1024 1024)) rows-that-fit))
                              {:type :invalid-query})))))))))

;;; Numbers above 2^53. The Cube SQL API has the type Double for a number, and the ClickHouse driver of Cube reads JSON numbers, so
;;; these lost digits before they reach this driver. The driver cannot get them back, so it stops the query.

(def ^:private ^:dynamic *stop-big-numbers*
  "True for a query with a result for a person. Sync reads samples, and a big number must not stop it."
  false)

(def ^:private max-exact-number 9007199254740992.0)

(doseq [jdbc-type [Types/DOUBLE Types/FLOAT Types/REAL Types/BIGINT Types/NUMERIC Types/DECIMAL]]
  (defmethod sql-jdbc.execute/read-column-thunk [:cube jdbc-type]
    [driver ^ResultSet rs ^ResultSetMetaData rsmeta ^Integer i]
    (let [thunk ((get-method sql-jdbc.execute/read-column-thunk [:postgres jdbc-type]) driver rs rsmeta i)]
      (fn []
        (let [value (thunk)]
          (when (and *stop-big-numbers*
                     (number? value)
                     (>= (Math/abs (double value)) max-exact-number))
            (throw (ex-info (format (str "Cube read %s in the column %s as a Double, so a number above 2^53 has lost digits. Add a "
                                         "dimension with SQL toString(column) to the data model to read the full digits as text, "
                                         "or filter out these rows.")
                                    value (.getColumnLabel rsmeta i))
                            {:type :invalid-query})))
          value)))))

;;; The value of a time measure. The SQL API gives a measure with `type: time` as text, and a time dimension as a timestamp. So Metabase gives a dimension an offset and a measure none,
;;; and a client that parses the measure can read another instant.

(def ^:private ^:dynamic *time-measure-columns*
  "The names of the columns of this result that hold the text of a time."
  nil)

(doseq [jdbc-type [Types/VARCHAR Types/LONGVARCHAR]]
  (defmethod sql-jdbc.execute/read-column-thunk [:cube jdbc-type]
    [driver ^ResultSet rs ^ResultSetMetaData rsmeta ^Integer i]
    (let [thunk ((get-method sql-jdbc.execute/read-column-thunk [:postgres jdbc-type]) driver rs rsmeta i)]
      (if (contains? *time-measure-columns* (.getColumnLabel rsmeta i))
        (fn []
          (let [value (thunk)]
            (try
              (if (string? value) (LocalDateTime/parse value) value)
              (catch java.time.format.DateTimeParseException _ value))))
        thunk))))

(defmethod driver/execute-reducible-query :cube
  [driver query context respond]
  (cube.identity/assert-person! (:details (driver-api/database (driver-api/metadata-provider))))
  (check-sql! query)
  (check-view-on-legacy-planner! query)
  (try
    (binding [*stop-big-numbers* (not (sync-query? query))
              *time-measure-columns* (set (get-in query [:native :cube/time-measure-columns]))]
      (let [query (with-native-grammar query)]
        (check-result-size! query)
        (sql-jdbc.execute/execute-reducible-query driver query context respond)))
    (catch Exception e
      (if-let [text (clear-error (exception-text e) (when (native-only? query) (get-in query [:native :query])))]
        (throw (ex-info text {:type :invalid-query}))
        (throw e)))))
