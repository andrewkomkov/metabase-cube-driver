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
   [metabase.driver.cube.semantic]
   [metabase.driver.postgres]
   [metabase.driver.sql-jdbc.connection :as sql-jdbc.conn]
   [metabase.driver.sql-jdbc.execute :as sql-jdbc.execute]
   [metabase.driver.sql.query-processor :as sql.qp]
   [metabase.query-processor.middleware.cache :as qp.middleware.cache]
   [metabase.util.honey-sql-2 :as h2x]
   [toucan2.core :as t2])
  (:import
   (java.sql ResultSet ResultSetMetaData Types)
   (java.time DayOfWeek Instant LocalDate LocalDateTime OffsetDateTime ZoneId ZonedDateTime)
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
                 ;; The Cube templates for ClickHouse have no PERCENTILE_CONT, so Cube rejects these aggregations.
                 :percentile-aggregations
                 :index/fetch
                 :index/standalone-create
                 :metadata/table-existence-check
                 :native-pivot-tables
                 :nested-field-columns
                 :persist-models
                 :rename
                 :split-part
                 :transforms/index-ddl
                 :transforms/python
                 :transforms/table
                 :uploads
                 :uuid-type]]
  (defmethod driver/database-supports? [:cube feature] [_driver _feature _db] false))

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
;; to another person. A driver has no hook for this, so this wraps the middleware. Metabase rebuilds the query processor
;; when this var changes. If a new Metabase has no such var, the driver does not load, which is the safe result.
(defn- per-person-query?
  []
  (let [database (driver-api/database (driver-api/metadata-provider))]
    (and (= :cube (:engine database))
         (some? (cube.identity/attribute (:details database))))))

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

;; The MCP tool `execute_query` reads each bound of `between` as a whole day, also a bound with a time. The driver sees only the
;; whole days, so it cannot stop this, and the number is wrong for each driver. So this keeps the time. A Metabase version with
;; no such var keeps its own behaviour, see mcp.query.between_keeps_time.
(defn- keep-time-of-bound
  [wrap-iso-date]
  (fn [value]
    (let [wrapped (wrap-iso-date value)]
      (if (and (vector? wrapped)
               (string? value)
               (re-find #"\d[T ]\d{2}:\d{2}" value))
        (assoc wrapped 3 "default")
        wrapped))))

(defonce between-wrapped
  (when-let [wrap-iso-date (try
                             (requiring-resolve 'metabase.agent-lib.representations.repair/wrap-iso-date)
                             (catch Exception _ nil))]
    (alter-var-root wrap-iso-date keep-time-of-bound)
    true))

(defmethod sql-jdbc.execute/set-timezone-sql :cube [_] "SET TIME ZONE %s")

(defmethod driver/can-connect? :cube
  [driver details]
  (let [parent (get-method driver/can-connect? :postgres)]
    (and (parent driver details)
         (some? (cube.meta/fetch-meta details)))))

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

(declare ^:dynamic *bucketed-field*)

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

(declare relative-time current-meta table-name-of-field)

(def ^:private ^:dynamic *keep-date-ranges*
  "True if a measure of this query needs a date range: a rolling window or a time shift reads no other filter as a range."
  false)

(def ^:private ^:dynamic *inside-not*
  "True inside a NOT. Cube does not merge it into a date range, and the form with IS NULL would keep the rows with no value."
  false)

(defmethod sql.qp/->honeysql [:cube :not]
  [driver clause]
  (binding [*inside-not* true]
    ((get-method sql.qp/->honeysql [:postgres :not]) driver clause)))

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

(defmethod sql.qp/->honeysql [:cube :>]
  [driver [_ opts field value :as clause]]
  (let [t (plain-bound-time field value)]
    (check-no-fraction! field t)
    (cond
      (or (nil? t) *inside-not* (exact-bounds? field)) (postgres-clause driver clause)
      ;; A rolling window or a time shift reads whole buckets, so the rows inside one second do not change it.
      *keep-date-ranges* (postgres-clause driver [:>= opts field (at-time (.plusSeconds (whole-second-down t) 1))])
      :else (not-merged driver field (postgres-clause driver clause)))))

(defmethod sql.qp/->honeysql [:cube :>=]
  [driver [_ _opts field value :as clause]]
  (check-no-fraction! field (plain-bound-time field value))
  (postgres-clause driver clause))

(defmethod sql.qp/->honeysql [:cube :<]
  [driver [_ _opts field value :as clause]]
  (let [t (plain-bound-time field value)]
    (check-no-fraction! field t)
    (if (or (nil? t) *inside-not* *keep-date-ranges* (exact-bounds? field) (rollup-on-column? field))
      (postgres-clause driver clause)
      (not-merged driver field (postgres-clause driver clause)))))

(defmethod sql.qp/->honeysql [:cube :<=]
  [driver [_ _opts field value :as clause]]
  (check-no-fraction! field (plain-bound-time field value))
  (postgres-clause driver clause))

(defmethod sql.qp/->honeysql [:cube :between]
  [driver [_ _opts field low high :as clause]]
  (check-no-fraction! field (plain-bound-time field low) (plain-bound-time field high))
  (postgres-clause driver clause))

(doseq [op [:= :!=]]
  (defmethod sql.qp/->honeysql [:cube op]
    [driver [_ _opts field value :as clause]]
    (check-no-fraction! field (plain-bound-time field value))
    (postgres-clause driver clause)))

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

(declare start-of-week-index)

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
    (some #(= :type/PK (:semantic-type (column-field (pop earlier) %))) (:fields (peek earlier)))))

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

(defn- check-aggregations!
  [stages]
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

(defn- check-stages!
  [stages]
  (doseq [i (range (count stages))
          :let [stage (nth stages i)]
          :when (not= :mbql.stage/native (:lib/type stage))]
    (let [upto (subvec stages 0 (inc i))]
      (check-no-group-by-member! (pop upto) stage)
      (check-no-filter-on-measure! (pop upto) stage)
      (check-row-values-give-measure! (pop upto) stage)
      (check-equal-sql-breakout! (pop upto) stage)
      (check-aggregations! upto)
      (check-time-shift! upto)
      (check-custom-granularities! upto))))

(defn- needs-date-ranges?
  "True if a measure of a stage has a rolling window or can have a time shift."
  [stages]
  (let [cube-meta (current-meta)]
    (some (fn [i]
            (let [earlier (subvec stages 0 i)]
              (some (fn [field]
                      (or (rolling-field? field)
                          (when cube-meta
                            (cube.probe/time-shift-candidate? cube-meta (full-member-name field)))))
                    (when-not (= :mbql.stage/native (:lib/type (nth stages i)))
                      (aggregated-measure-fields earlier (nth stages i))))))
          (range (count stages)))))

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
                         (and (string? native) (aggregated-native? native)) (assoc stage :native (limit-native native))
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
        null-column (h2x/cast (physical-type field) nil)]
    (when (and *raw-measures* alias-form (= :measure (member-kind clause))
               (contains? *aggregated-measures* (:name field)))
      (swap! *raw-measures* assoc alias-form compiled)
      (when (contains? *row-measures* (:name field))
        (swap! *row-measure-aliases* conj alias-form)))
    (cond
      (not (or (#{:measure :segment} (member-kind clause))
               (= "cube.dimension/geo-in-view" (:database-type field))
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
          selected (keep #(:name (stored-field %)) fields)]
      (into #{}
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

(defn- datediff
  [unit x y]
  [:datediff (h2x/literal (name unit)) x y])

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

;; A relative date in weeks has no column, so it cannot use a custom granularity, and a week number is not a time. The time
;; of its first day is a plain literal on each first day of the week.
(defmethod sql.qp/->honeysql [:cube :relative-datetime]
  [driver [_ _opts amount unit :as clause]]
  (if (and (= :week unit) (not= 1 (start-of-week-index)))
    (time-literal (relative-time amount unit))
    ((get-method sql.qp/->honeysql [:postgres :relative-datetime]) driver clause)))

(defmethod sql.qp/date [:cube :week]
  [_driver _unit expr]
  (let [start (start-of-week-index)]
    (if (= 1 start)
      (date-trunc :week expr)
      (if-let [granularity (some-> *bucketed-field* (week-granularity start))]
        (date-trunc granularity expr)
        (week-number start expr)))))

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
                         (group-raw-stage restored raw-measures null-columns)
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
  "The answers from Cube that the scan of native SQL needs. See cube.native/check-native!."
  [details cube-meta]
  {:keeps-fractions? (cube.probe/keeps-fractions? details cube-meta nil)
   :time-shift?      (partial cube.probe/time-shift? details cube-meta)
   :wrong-bin-reason (partial cube.probe/wrong-bin-reason details cube-meta)})

(defn- check-native-stages!
  [stages]
  (when-let [cube-meta (current-meta)]
    (let [details (:details (driver-api/database (driver-api/metadata-provider)))]
      (doseq [{:keys [native params]} stages
              :when (string? native)]
        (cube.native/check-native! cube-meta native (or params []) (native-questions details cube-meta))))))

(defn- query-with-stages
  "`query` with the checks done and the limits added, the measures that a later stage aggregates, the columns that Cube reads as
  MEASURE(), and the measures that a later stage filters or groups by."
  [driver {:keys [stages] :as query}]
  (if (:lib/type query)
    (let [preprocessed (vec (sql.qp/preprocess driver query))]
      (check-stages! preprocessed)
      (check-native-stages! preprocessed)
      [(assoc query :stages (limit-subqueries (vec stages)))
       (aggregated-measures preprocessed)
       (named-measures preprocessed)
       (row-measure-names preprocessed)
       (boolean (needs-date-ranges? preprocessed))])
    [query {} {} #{} false]))

(defmethod driver/mbql->native :cube
  [driver query]
  (binding [*week-numbers* (atom [])
            *raw-measures* (atom {})
            *null-columns* (atom #{})
            *row-measure-aliases* (atom #{})]
    (let [[query aggregated named row-measures date-ranges?] (query-with-stages driver query)
          honeysql-form (binding [*aggregated-measures* (into (set (keys aggregated)) row-measures)
                                  *named-measures*      named
                                  *row-measures*        row-measures
                                  *keep-date-ranges*    date-ranges?]
                          (sql.qp/mbql->honeysql driver query))
          honeysql-form (restore-nested-measures honeysql-form @*raw-measures* @*row-measure-aliases* @*null-columns*)
          honeysql-form (if (and (map? honeysql-form) (seq @*week-numbers*))
                          (nest-all-week-numbers driver honeysql-form @*week-numbers*)
                          honeysql-form)
          [sql & args]  (sql.qp/format-honeysql driver honeysql-form)]
      {:query sql, :params args})))

;;; Parts of a date. Cube runs EXTRACT over a CubeScan as post-processing, which reads at most CUBEJS_DB_QUERY_LIMIT rows
;;; of a smaller grain, and which fails in a filter or with a measure that is not additive. DATEDIFF goes to the
;;; database as SQL push-down, with no such limits.

(defn- since
  "The units of `unit` from the start of the `period` to `expr`."
  [unit period expr]
  (datediff unit (date-trunc period expr) expr))

(defmethod sql.qp/date [:cube :second-of-minute] [_ _ expr] (since :second :minute expr))
(defmethod sql.qp/date [:cube :minute-of-hour]   [_ _ expr] (since :minute :hour expr))
(defmethod sql.qp/date [:cube :hour-of-day]      [_ _ expr] (since :hour :day expr))
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

(defn- week-of-year-from
  "Week 1 starts on 1 January and week 2 on the first day `start` after it."
  [start expr]
  (let [days-before (since :day :year expr)
        first-day   [:% [:+ [:% [:- (weekday-from start expr) days-before] [:inline 7]] [:inline 7]] [:inline 7]]]
    [:+ [:floor [:/ [:+ days-before first-day] [:inline 7.0]]] [:inline 1]]))

(defmethod sql.qp/date [:cube :week-of-year]
  [_driver _unit expr]
  (week-of-year-from (start-of-week-index) expr))

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
   ["Invalid identifier"
    (str "This table has no column with that name. Call browse_data with action get_fields to read the columns of the "
         "table. A column of another cube is not on this table: use a view that has it, or write "
         "FROM a CROSS JOIN b WHERE a.__cubeJoinField = b.__cubeJoinField and read a.column and b.column.")]
   ["Can't detect Cube query"
    (str "Cube cannot plan this query. EXTRACT on a time dimension next to a measure, a function that the SQL API does "
         "not know, and a JOIN with an ON condition are the common cause. Use a time bucket, or add a dimension for it "
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
   ["Stream query failed"
    (str "Cube cannot read a rollup_lambda on ClickHouse with a range that reads source rows. Add a date "
         "filter inside the range of the rollup, or read this measure from a plain rollup.")]
   ["Date range is required for time series"
    (str "Cube needs a date range for this query. A rolling window measure by a time bucket needs a date filter on the "
         "same time column. Add a date filter.")]])

(defn- exception-text
  "The messages of an exception and its causes."
  [^Throwable e]
  (str/join " " (keep (fn [^Throwable t] (.getMessage t))
                      (take-while some? (iterate (fn [^Throwable t] (.getCause t)) e)))))

(defn- clear-error
  [message]
  (some (fn [[needle text]]
          (when (str/includes? message needle)
            (str text " Cube said: " message)))
        cube-errors))

(defn- native-only?
  [{:keys [stages type]}]
  (if (seq stages)
    (every? #(= :mbql.stage/native (:lib/type %)) stages)
    (= :native type)))

(defn- sync-query?
  "True if this query only reads samples of a table for sync, which show no number to a person."
  [query]
  (contains? #{:table-rows-sample :fingerprint} (-> query :info :context)))

(defn- check-sql!
  "Stop a shape of SQL text that gives a wrong number. Sync reads samples of a table and does not need this."
  [{{sql :query, params :params} :native, :as query}]
  (when-not (sync-query? query)
    (when-let [cube-meta (current-meta)]
      (if (native-only? query)
        (cube.native/check-native! cube-meta sql (or params [])
                                   (native-questions (:details (driver-api/database (driver-api/metadata-provider))) cube-meta))
        (cube.native/check-paths! cube-meta sql)))))

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

(defmethod driver/execute-reducible-query :cube
  [driver query context respond]
  (cube.identity/assert-person! (:details (driver-api/database (driver-api/metadata-provider))))
  (check-sql! query)
  (try
    (binding [*stop-big-numbers* (not (sync-query? query))]
      (sql-jdbc.execute/execute-reducible-query driver query context respond))
    (catch Exception e
      (if-let [text (clear-error (exception-text e))]
        (throw (ex-info text {:type :invalid-query}))
        (throw e)))))
