(ns metabase.driver.cube
  "Metabase driver for Cube Core. Queries go through the Cube SQL API, which speaks the Postgres protocol. The data
  model comes from the Cube REST API, because pg_catalog on the SQL API has no titles, descriptions or member kinds."
  (:require
   [clojure.string :as str]
   [metabase.driver :as driver]
   [metabase.driver-api.core :as driver-api]
   [metabase.driver.cube.meta :as cube.meta]
   [metabase.driver.postgres]
   [metabase.driver.sql-jdbc.connection :as sql-jdbc.conn]
   [metabase.driver.sql.query-processor :as sql.qp]
   [metabase.util.honey-sql-2 :as h2x]
   [metabase.util.log :as log]
   [metabase.warehouse-schema.humanization :as humanization]
   [toucan2.core :as t2]))

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
                 :index/fetch
                 :index/standalone-create
                 :metadata/key-constraints
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
              (map #(dissoc % ::cube.meta/title)))
        (sort-by :name (cube.meta/public-cubes (cube.meta/fetch-meta (:details database))))))

(defn- apply-model-metadata!
  "Sync has no input for a field title, so this step writes titles to the app DB after sync. A display name that an
  admin changed is not the humanized name, and stays. Measures and segments get no field values, because the SQL API
  has no list of values for them."
  [database]
  (let [cubes      (cube.meta/public-cubes (cube.meta/fetch-meta (:details database)))
        name->cube (into {} (map (juxt :name identity)) cubes)
        humanized? (fn [{entity-name :name, display-name :display_name}]
                     (= display-name (humanization/name->human-readable-name entity-name)))]
    (doseq [{table-id :id, table-name :name, :as table} (t2/select :model/Table :db_id (:id database), :active true)
            :let  [cube (name->cube table-name)]
            :when cube
            :let  [name->member (into {} (map (juxt :name identity)) (cube.meta/cube->fields cube))]]
      (when (and (humanized? table) (seq (:title cube)) (not= (:title cube) (:display_name table)))
        (t2/update! :model/Table table-id {:display_name (:title cube)}))
      (doseq [{field-id :id, field-name :name, :as field} (t2/select :model/Field :table_id table-id, :active true)
              :let  [title         (::cube.meta/title (name->member field-name))
                     database-type (str (:database_type field))
                     no-values?    (or (str/starts-with? database-type "cube.measure/")
                                       (= database-type "cube.segment"))
                     changes       (cond-> {}
                                     (and title (humanized? field) (not= title (:display_name field)))
                                     (assoc :display_name title)

                                     (and no-values? (not= :none (:has_field_values field)))
                                     (assoc :has_field_values :none))]
              :when (seq changes)]
        (t2/update! :model/Field field-id changes)))))

(defmethod driver/sync-in-context :cube
  [_driver database f]
  (let [result (f)]
    (try
      (apply-model-metadata! database)
      (catch Throwable e
        (log/warnf e "Could not apply Cube titles to database %s" (:id database))))
    result))

;;; --------------------------------------------------- compiler --------------------------------------------------

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

(defmethod sql.qp/->honeysql [:cube :field]
  [driver [_ {:keys [temporal-unit]} _ :as clause]]
  (let [parent   (get-method sql.qp/->honeysql [:postgres :field])
        compiled (parent driver clause)
        field    (stored-field clause)]
    ;; The compiler uses the database type to choose casts. It must not see the cube.* marker.
    (if (and field (contains? #{nil :default} temporal-unit))
      (h2x/with-database-type-info compiled (physical-type field))
      compiled)))

;; ClickHouse rejects the filter that Cube makes from `= TRUE` when the dimension SQL is an expression:
;; "Cannot convert string 'true' to type UInt8". Cube accepts 1 and 0 for boolean members on all columns.
(defmethod sql.qp/->honeysql [:cube :value]
  [driver [_ {:keys [database-type]} raw-value :as clause]]
  (if (and (boolean? raw-value) (str/starts-with? (str database-type) "cube."))
    [:inline (if raw-value 1 0)]
    ((get-method sql.qp/->honeysql [:postgres :value]) driver clause)))

(def ^:private extraction-units
  "Temporal units that are not a Cube granularity. The SQL API runs them with SQL push-down."
  #{:second-of-minute :minute-of-hour :hour-of-day :day-of-week :day-of-month :day-of-year
    :week-of-year :week-of-year-iso :month-of-year :quarter-of-year :year-of-era})

(defn- extraction-breakout?
  []
  (some (fn [clause]
          (and (vector? clause)
               (= :field (first clause))
               (contains? extraction-units (:temporal-unit (second clause)))))
        (:breakout sql.qp/*inner-query*)))

(defn- push-down-measure
  "SQL push-down rejects MEASURE(). It accepts the SQL aggregation of the measure type, for these types only."
  [driver arg]
  (let [compiled (sql.qp/->honeysql driver arg)]
    (case (:database-type (stored-field arg))
      "cube.measure/sum"   [:sum compiled]
      "cube.measure/min"   [:min compiled]
      "cube.measure/max"   [:max compiled]
      "cube.measure/count" :%count.*
      [:measure compiled])))

(doseq [agg-type [:sum :avg :min :max :count :distinct]]
  (let [parent (get-method sql.qp/->honeysql [:postgres agg-type])]
    (defmethod sql.qp/->honeysql [:cube agg-type]
      [driver [_ _opts arg :as clause]]
      ;; A measure has its aggregation in the data model. Cube rejects a SQL aggregation of another type with
      ;; "Measure aggregation type doesn't match", and MEASURE() has no such check.
      (cond
        (not= :measure (member-kind arg)) (parent driver clause)
        (extraction-breakout?)            (push-down-measure driver arg)
        :else                             [:measure (sql.qp/->honeysql driver arg)]))))

(defn- raw-select-item
  "A measure has no value for one row, and a segment column on the SQL API is always NULL. Cube fails an ungrouped
  query that selects a measure with an aggregate in its SQL or a multi-stage measure, and one such measure would break
  the whole table view. So raw rows show NULL for these columns."
  [driver clause]
  (let [[_compiled alias-form :as selected] (sql.qp/as driver clause)
        null-column (h2x/cast (physical-type (stored-field clause)) nil)]
    (cond
      (not (#{:measure :segment} (member-kind clause))) selected
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

;; Cube turns DATE_TRUNC on a time dimension into a granularity of the CubeScan. A cast around the column or an
;; interval on it stops that rewrite.
(defn- date-trunc
  [unit expr]
  (h2x/with-database-type-info [:date_trunc (h2x/literal unit) expr] "timestamp"))

(doseq [unit [:minute :hour :day :week :month :quarter :year]]
  (defmethod sql.qp/date [:cube unit] [_driver _unit expr] (date-trunc unit expr)))

;; SQL push-down accepts EXTRACT, but not the integer cast that the Postgres driver adds.
(doseq [[unit part] {:second-of-minute :second
                     :minute-of-hour   :minute
                     :hour-of-day      :hour
                     :day-of-month     :day
                     :day-of-year      :doy
                     :week-of-year-iso :week
                     :month-of-year    :month
                     :quarter-of-year  :quarter
                     :year-of-era      :year}]
  (defmethod sql.qp/date [:cube unit] [_driver _unit expr] [::h2x/extract part expr]))

;; Metabase counts days from 1. EXTRACT(DOW) counts from Sunday as 0.
(defmethod sql.qp/date [:cube :day-of-week]
  [_driver _unit expr]
  (h2x/+ [::h2x/extract :dow expr] [:inline 1]))

;; These helpers in the util namespace have one branch for each driver and no branch for :cube.
(defmethod sql.qp/current-datetime-honeysql-form :cube
  [_driver]
  (h2x/current-datetime-honeysql-form :postgres))

(defmethod sql.qp/add-interval-honeysql-form :cube
  [_driver hsql-form amount unit]
  (h2x/add-interval-honeysql-form :postgres hsql-form amount unit))
