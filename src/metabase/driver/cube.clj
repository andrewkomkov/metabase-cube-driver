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
   [metabase.driver.cube.semantic]
   [metabase.driver.postgres]
   [metabase.driver.sql-jdbc.connection :as sql-jdbc.conn]
   [metabase.driver.sql-jdbc.execute :as sql-jdbc.execute]
   [metabase.driver.sql.query-processor :as sql.qp]
   [metabase.query-processor.middleware.cache :as qp.middleware.cache]
   [metabase.util.honey-sql-2 :as h2x]
   [toucan2.core :as t2])
  (:import
   (java.time DayOfWeek LocalDate LocalDateTime OffsetDateTime ZoneOffset ZonedDateTime)
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
                 :set-timezone
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
        details (connection-details db-or-id-or-spec)]
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

(defmethod sql.qp/->honeysql [:cube :value]
  [driver [_ {:keys [database-type]} raw-value :as clause]]
  (if (and (boolean? raw-value)
           (not *inside-case*)
           (str/starts-with? (str database-type) "cube.")
           ;; Cube turns `segment = TRUE` into a segment of the CubeScan, which a rollup can serve. `= 1` goes to push-down.
           (not= database-type "cube.segment"))
    [:inline (if raw-value 1 0)]
    ((get-method sql.qp/->honeysql [:postgres :value]) driver clause)))

(defn- zero-when-empty
  "Cube reads a rollup with SUM, so a count over no rows is NULL. SQL has 0 there. With a breakout there are no rows."
  [compiled]
  (if (seq (:breakout sql.qp/*inner-query*))
    compiled
    [:coalesce compiled [:inline 0]]))

(defn- count-measure?
  [arg]
  (contains? #{"cube.measure/count" "cube.measure/countDistinct" "cube.measure/countDistinctApprox"}
             (str (:database-type (stored-field arg)))))

;;; Rolling windows. Cube computes a rolling window for each whole bucket of a time series. With no bucket it returns the
;;; total of all rows, and a date filter that ends inside a bucket does not limit the window. Both give a number that
;;; looks right, so the driver stops these queries.

(declare start-of-week-index)

(def ^:private bucket-rank
  "The units that make a time series, from small to large."
  {:minute 0, :hour 1, :day 2, :week 3, :month 4, :quarter 5, :year 6})

(defn- rolling-measure?
  [arg]
  (str/starts-with? (str (:database-type (stored-field arg))) "cube.measure/cumulative-"))

(defn- time-bucket
  "The breakout that makes the time series of this stage, or nil."
  []
  (some (fn [clause]
          (when (and (vector? clause)
                     (= :field (first clause))
                     (= :dimension (member-kind clause))
                     (bucket-rank (:temporal-unit (second clause))))
            clause))
        (:breakout sql.qp/*inner-query*)))

(defn- utc-time
  ^LocalDateTime [t]
  (condp instance? t
    LocalDateTime  t
    LocalDate      (.atStartOfDay ^LocalDate t)
    OffsetDateTime (.toLocalDateTime (.withOffsetSameInstant ^OffsetDateTime t ZoneOffset/UTC))
    ZonedDateTime  (.toLocalDateTime (.withZoneSameInstant ^ZonedDateTime t ZoneOffset/UTC))
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
      (when-let [t (utc-time a)]
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

(defn- mentions-field?
  [clause field-id]
  (some (fn [x] (and (vector? x) (= :field (first x)) (= field-id (nth x 2 nil))))
        (tree-seq coll? seq clause)))

(defn- filter-on-boundaries?
  "True if each date bound on the column of the bucket is a boundary of the bucket. Only an AND of plain comparisons can
  show this, so a filter with OR, NOT or a part of a date on that column is false."
  [bucket field-id clause]
  (let [[op _ & args] clause]
    (cond
      (= :and op)                       (every? #(filter-on-boundaries? bucket field-id %) args)
      (not (mentions-field? clause field-id)) true
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
  [arg]
  (let [measure-name (:name (stored-field arg))
        bucket       (time-bucket)]
    (when-not bucket
      (throw (ex-info (format (str "%s is a rolling window measure. With no time bucket, Cube returns the total of all "
                                   "rows, not the window. Group by a time bucket, for example by month, and add a date "
                                   "filter.")
                              measure-name)
                      {:type :invalid-query})))
    (let [unit     (:temporal-unit (second bucket))
          field-id (nth bucket 2)
          filters  (concat (:filters sql.qp/*inner-query*)
                           (some-> (:filter sql.qp/*inner-query*) list))]
      (when-not (every? #(filter-on-boundaries? unit field-id %) filters)
        (throw (ex-info (format (str "%s is a rolling window measure, and Cube computes it for each whole %s. This date "
                                     "filter starts or ends inside a %s, and Cube does not apply that part, so the number "
                                     "would count rows outside the filter. Use a filter on whole %ss, or a smaller time "
                                     "bucket.")
                                measure-name (name unit) (name unit) (name unit))
                        {:type :invalid-query}))))))

(defn- own-aggregation?
  "True if `agg-type` is the clause that holds this measure. See cube.meta/aggregation-of-measure."
  [agg-type arg]
  (let [member-type (-> (str (:database-type (stored-field arg)))
                        (str/replace-first "cube.measure/" "")
                        (str/replace-first "cumulative-" ""))]
    (= agg-type (cube.meta/aggregation-of-measure member-type :max))))

(doseq [agg-type [:sum :avg :min :max :count :distinct]]
  (let [parent (get-method sql.qp/->honeysql [:postgres agg-type])]
    (defmethod sql.qp/->honeysql [:cube agg-type]
      [driver [_ _opts arg :as clause]]
      ;; A measure has its aggregation in the data model, and its own clause is MEASURE(). Another aggregation stays a SQL
      ;; aggregation: Cube computes it from the rows, or fails with "Measure aggregation type doesn't match". As MEASURE()
      ;; it would return the measure with no error, under the name of another aggregation.
      (cond
        (and (= agg-type :count) (nil? arg)) (zero-when-empty (parent driver clause))
        (not= :measure (member-kind arg))    (parent driver clause)
        (not (own-aggregation? agg-type arg)) (parent driver clause)
        :else
        (do
          (when (rolling-measure? arg)
            (check-rolling-measure! arg))
          (if (count-measure? arg)
            (zero-when-empty [:measure (sql.qp/->honeysql driver arg)])
            [:measure (sql.qp/->honeysql driver arg)]))))))

(def ^:private ^:dynamic *raw-measures*
  "An atom from column alias to the compiled measure, for measures that a stage of this query selects as raw rows."
  nil)

(defn- raw-select-item
  "A measure has no value for one row, and a segment column on the SQL API is always NULL. Cube fails an ungrouped
  query that selects a measure with an aggregate in its SQL or a multi-stage measure, and one such measure would break
  the whole table view. So raw rows show NULL for these columns, and for a geo dimension of a view, which Cube cannot read."
  [driver clause]
  (let [[compiled alias-form :as selected] (sql.qp/as driver clause)
        null-column (h2x/cast (physical-type (stored-field clause)) nil)]
    (when (and *raw-measures* alias-form (= :measure (member-kind clause)))
      (swap! *raw-measures* assoc alias-form compiled))
    (cond
      (not (or (#{:measure :segment} (member-kind clause))
               (= "cube.dimension/geo-in-view" (:database-type (stored-field clause))))) selected
      alias-form                                        [null-column alias-form]
      :else                                             [null-column])))

(defmethod sql.qp/apply-top-level-clause [:cube :fields]
  [driver _ honeysql-form {fields :fields}]
  (update honeysql-form :select (fnil into []) (map (partial raw-select-item driver)) fields))

(defmethod sql.qp/apply-top-level-clause [:cube :breakout]
  [driver clause honeysql-form {breakout-fields :breakout :as query}]
  (doseq [field-clause breakout-fields
          :let  [kind (member-kind field-clause)]
          :when (#{:measure :segment} kind)]
    ;; Cube accepts these, ignores the grouping and returns one total row, so stop here and tell the user.
    (throw (ex-info (format "Cube cannot group by the %s %s. Use it as a %s."
                            (name kind)
                            (:name (stored-field field-clause))
                            (if (= kind :segment) "filter" "metric"))
                    {:type :invalid-query})))
  ((get-method sql.qp/apply-top-level-clause [:postgres :breakout]) driver clause honeysql-form query))

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
  [driver honeysql-form week-numbers]
  (let [{:keys [select order-by limit offset]} honeysql-form
        numbers     (set week-numbers)
        expr->alias (into {} (map (juxt first second)) select)
        start       (start-of-week-index)]
    (when (not-any? numbers (map first select))
      (throw (ex-info (format (str "Cube cannot use a week that starts on %s here, because it cannot add an interval to "
                                   "a time on ClickHouse. Set the first day of the week to Monday in Metabase, or add "
                                   "a custom granularity with `interval: 1 week` and the offset of that day to the "
                                   "time dimension.")
                              (name (driver-api/start-of-week)))
                      {:type :invalid-query})))
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

(defn- restore-nested-measures
  "A measure is an empty column in raw rows, see raw-select-item. An outer stage aggregates the column of a subquery, and
  Cube reads that as the measure, so a subquery must select the measure itself."
  [honeysql-form raw-measures]
  (if (and (map? honeysql-form) (seq raw-measures))
    (update honeysql-form :from
            (fn [from]
              (walk/postwalk
               (fn [form]
                 (if (and (map? form) (:select form))
                   (update form :select
                           (fn [items]
                             (mapv (fn [item]
                                     (if-let [compiled (when (vector? item) (get raw-measures (second item)))]
                                       [compiled (second item)]
                                       item))
                                   items)))
                   form))
               from)))
    honeysql-form))

(defmethod driver/mbql->native :cube
  [driver query]
  (binding [*week-numbers* (atom [])
            *raw-measures* (atom {})]
    (let [honeysql-form (-> (sql.qp/mbql->honeysql driver query)
                            (restore-nested-measures @*raw-measures*))
          honeysql-form (if (and (map? honeysql-form) (seq @*week-numbers*))
                          (nest-week-numbers driver honeysql-form @*week-numbers*)
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

(defmethod driver/execute-reducible-query :cube
  [driver query context respond]
  (cube.identity/assert-person! (:details (driver-api/database (driver-api/metadata-provider))))
  (try
    (sql-jdbc.execute/execute-reducible-query driver query context respond)
    (catch Exception e
      (if-let [text (clear-error (exception-text e))]
        (throw (ex-info text {:type :invalid-query}))
        (throw e)))))
