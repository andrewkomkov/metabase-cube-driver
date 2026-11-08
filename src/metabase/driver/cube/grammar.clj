(ns metabase.driver.cube.grammar
  "The grammar of native SQL that the driver sends to Cube. The SQL API of Cube reads much of SQL with another meaning and gives a number with no error, so this is an allow-list: `check` walks the tree of
  `metabase.driver.cube.sqltext`, and each node must match a construct of `allow-list`, on the types and names that the data model gives. Each other node stops the query with a message that names it,
  so the default of each branch here is a refusal. `dev/fuzz/native_grammar.py` proves each construct with a truth from ClickHouse, on each path of Cube. This namespace reads no Metabase code, so
  `dev/fuzz/admit.clj` can run it with no Metabase."
  (:require
   [clojure.string :as str]
   [metabase.driver.cube.sqltext :as sqltext])
  (:import
   (java.math BigDecimal)))

(set! *warn-on-reflection* true)

(def allow-list
  "Each construct that can reach Cube. `docs/query-compilation.md` has this table with its proof."
  {:query/select            "SELECT ... FROM one table, a view or a subquery"
   :query/no-from           "SELECT of literals, with no FROM"
   :query/limit             "LIMIT or FETCH FIRST with a whole number"
   :query/offset            "OFFSET with a whole number"
   :query/order             "ORDER BY a position, an alias, a column or an expression, ASC or DESC, NULLS LAST"
   :query/distinct          "SELECT DISTINCT"
   :query/with              "WITH of queries that are not recursive"
   :query/union             "UNION and UNION ALL at the top of a query"
   :from/subquery           "FROM a subquery, with a filter, a star or an aggregation over it"
   :from/join-model         "JOIN or LEFT JOIN of two cubes on the join of the data model, as __cubeJoinField or as the keys of that join"
   :from/cross-model        "CROSS JOIN or a comma of two cubes, with a.__cubeJoinField = b.__cubeJoinField in WHERE, or with MEASURE()"
   :select/column           "A dimension, by its name, with or without its table"
   :select/star             "* or t.*: the public dimensions, written out by the driver"
   :select/alias            "An alias of a column, also with the name of a column"
   :select/segment          "A segment in WHERE"
   :value/literal           "A number, a text, NULL, TRUE or FALSE"
   :value/time-literal      "A time as a text, as TIMESTAMP 'x', as DATE 'x' or as a cast of a text"
   :value/arithmetic        "+ - * % and unary minus on numbers"
   :value/division          "/ on numbers: a real division, and NULL for a divisor of 0"
   :value/concat            "|| on text"
   :value/case              "CASE WHEN ... THEN ... ELSE ... END, and CASE x WHEN"
   :value/cast-number       "CAST(x AS numeric, decimal, double precision, real or float), and x::numeric"
   :value/cast-text         "CAST(x AS text or varchar) of a text"
   :value/time-interval     "A time + or - INTERVAL 'n unit', with the dialect and with no report time zone"
   :value/cast-time         "CAST(x AS timestamp) of a time column, which is that column"
   :value/cast-date         "CAST(x AS date), x::date or DATE(x) of a time: DATE_TRUNC('day', x)"
   :fn/date-trunc           "DATE_TRUNC with a unit in a literal, on a time"
   :fn/number               "ABS, ROUND, FLOOR, CEIL, CEILING"
   :fn/null                 "COALESCE, NULLIF"
   :fn/text                 "LOWER, UPPER, SUBSTRING, SUBSTR, LEFT, RIGHT, CONCAT, LENGTH, STRPOS"
   :fn/datediff             "DATEDIFF with a unit in a literal, on two times"
   :agg/measure             "MEASURE(x) of a measure"
   :agg/count               "COUNT(*), COUNT(x), COUNT(DISTINCT x)"
   :agg/number              "SUM, AVG, STDDEV, STDDEV_SAMP and STDDEV_POP, also on an expression"
   :agg/min-max             "MIN, MAX on each type"
   :agg/distinct            "MIN and MAX with DISTINCT"
   :agg/of-measure          "The aggregation of a measure column that holds that measure, such as SUM(revenue) for a sum"
   :agg/filter              "FILTER (WHERE p) on an aggregation of a column"
   :agg/window              "ROW_NUMBER, RANK, DENSE_RANK, LAG, LEAD and an aggregation OVER a window of the groups, or of the rows of a subquery with GROUP BY, with no frame"
   :clause/where            "WHERE"
   :clause/group            "GROUP BY a position, an alias, a column or an expression"
   :clause/having           "HAVING on an aggregation, a group or an alias"
   :pred/compare-number     "= <> != < <= > >= on numbers, with exact values"
   :pred/compare-text       "= <> != < <= > >= on text"
   :pred/compare-time       "= <> != < <= > >= on a time"
   :pred/compare-boolean    "= <> on a boolean, with TRUE, FALSE, 1 or 0"
   :pred/compare-null       "= and <> with NULL, which keep no row"
   :pred/constant           "A comparison of two literals, as the 1 = 1 of a field filter with no value"
   :pred/in-list            "IN and NOT IN with a list of literals, also NULL"
   :pred/in-subquery        "IN with a subquery of one column, and NOT IN with one that has column IS NOT NULL in its WHERE"
   :pred/between            "BETWEEN and NOT BETWEEN on numbers and on times"
   :pred/like               "LIKE, ILIKE, NOT LIKE and NOT ILIKE, with a literal pattern"
   :pred/is-null            "IS NULL and IS NOT NULL"
   :pred/is-boolean         "IS [NOT] TRUE and IS [NOT] FALSE on a boolean column"
   :pred/is-distinct        "IS [NOT] DISTINCT FROM a literal"
   :pred/boolean-column     "A boolean column as a condition"
   :pred/logic              "AND, OR, NOT and parentheses. NOT, <>, NOT IN, NOT LIKE, NOT BETWEEN, IS TRUE and IS DISTINCT FROM only at the top of WHERE, HAVING or FILTER"
   :pred/join-field         "a.__cubeJoinField = b.__cubeJoinField"
   :param/value             "A template variable or a field filter, as its value"})

(def ^:private ^:dynamic *catalog* nil)
(def ^:private ^:dynamic *opts* nil)
(def ^:private ^:dynamic *used* nil)
(def ^:private ^:dynamic *edits* nil)
(def ^:private ^:dynamic *selects* nil)

(defn refuse!
  "Stops the query. `construct` names the first form that is not on the list. `hint` gives a form that is."
  ([construct] (refuse! construct nil))
  ([construct hint]
   (throw (ex-info (str "Native SQL on Cube: " construct " is not on the list of SQL that the driver can prove correct, so this query did not run."
                        (when hint (str " " hint))
                        " See \"Native SQL\" in docs/query-compilation.md.")
                   {:type :invalid-query, :cube/refused construct}))))

(defn- use!
  [construct]
  (assert (contains? allow-list construct) (str construct))
  (swap! *used* conj construct))

(defn- edit!
  "A change of the text at the place [s e). `check` applies each one and reads the new text."
  [s e text]
  (swap! *edits* conj {:s s, :e e, :text text}))

(defn- source-text
  [node]
  (subs (:sql *opts*) (:s node) (:e node)))

(defn- quoted
  [column-name]
  (str "\"" (str/replace column-name "\"" "\"\"") "\""))

(defn- unwrapped
  [node]
  (if (= :paren (:k node)) (recur (:x node)) node))

(def ^:private ^:dynamic *one-source?* false)

(defn- shape
  "The form of a node with no place in the text, so that two spellings of one expression are equal. With one source, a column with its table and one with none are equal."
  [node]
  (cond
    (and (map? node) (= :name (:k node))) {:k :name, :parts (if *one-source?* [(peek (:parts node))] (:parts node))}
    (map? node)    (let [node (if (= :paren (:k node)) (shape (:x node)) node)]
                     (into {} (for [[k v] node :when (not (contains? #{:s :e :args-end :filter-at :filter-end} k))] [k (shape v)])))
    (vector? node) (mapv shape node)
    :else          node))

;;; Values.

(def ^:private number-types #{"numeric" "decimal" "double precision" "float" "float4" "float8" "real" "double"})
(def ^:private text-types #{"text" "varchar" "character varying" "string"})
(def ^:private time-types #{"timestamp" "timestamptz" "date"})
(def ^:private units #{"second" "minute" "hour" "day" "week" "month" "quarter" "year"})
(def ^:private count-kinds #{"count"})
(def ^:private number-aggregations #{"sum" "avg" "stddev" "stddev_pop" "stddev_samp"})
(def ^:private window-only #{"row_number" "rank" "dense_rank" "lag" "lead"})

(declare value predicate check-query!)

(defn- refuse-private!
  [member table]
  (throw (ex-info (format (str "%s is a private member of %s in the Cube data model, so it has no values here. Use another column, or make it public in the "
                               "model. A Cube in dev mode reads private members, so do not run dev mode in production.")
                          member table)
                  {:type :invalid-query, :cube/refused "a private member"})))

(defn- check-private-name!
  [{:keys [sources]} column]
  (doseq [{:keys [privates table]} sources
          :when (contains? privates column)]
    (refuse-private! column table)))

(defn- parts-of-source?
  "True if `qualifier`, the parts of a name before the column, names this source."
  [{source-alias :alias, :keys [table]} qualifier]
  (let [last-part (peek qualifier)]
    (and (or (= last-part source-alias)
             (and (nil? source-alias) (= last-part table))
             (= last-part table))
         (or (= 1 (count qualifier))
             (and (= 2 (count qualifier)) (= "public" (first qualifier)) (= last-part table))))))

(def ^:private next-step-for-a-column
  "The next step after an unknown column, for a person and for an agent of the Metabase MCP server."
  (str " Call browse_data with action get_fields to read the columns of the table. A column of another cube is not on this table: use a view that has it, or write"
       " FROM a CROSS JOIN b WHERE a.__cubeJoinField = b.__cubeJoinField and read a.column and b.column."))

(defn- resolve-column
  "The column of this name, with its source. A name with no table reads each source. A private member refuses first, so an alias cannot hide it."
  [{:keys [sources aliases clause] :as env} parts]
  (let [column    (peek parts)
        qualifier (pop parts)]
    (when (contains? #{"__user" "__cubejoinfield"} column)
      (refuse! (str "the column " column) (if (= "__cubejoinfield" column)
                                            "It reads only as a.__cubeJoinField = b.__cubeJoinField."
                                            "A query cannot read as another person: the connection sets the person.")))
    (if (seq qualifier)
      (let [source (first (filter #(parts-of-source? % qualifier) sources))]
        (when-not source
          (refuse! (str "the table or alias " (str/join "." qualifier)) "It is not in the FROM of this SELECT, and a subquery that reads its outer query is not on the list."))
        (when (contains? (:privates source) column)
          (refuse-private! column (:table source)))
        (or (when-let [col (get (:columns source) column)]
              (assoc col :source source :column column))
            (refuse! (format "the column %s of %s" column (str/join "." qualifier)) (str "The data model has no such public column." next-step-for-a-column))))
      (let [alias-of (get aliases column)
            found    (keep (fn [source]
                             (when-let [col (get (:columns source) column)]
                               (assoc col :source source :column column)))
                           sources)]
        (cond
          ;; ORDER BY and HAVING read an alias first. A GROUP BY and a WHERE read a column first, as SQL does.
          (and alias-of (contains? #{:order :having :group} clause) (or (empty? found) (not= :group clause)))
          (assoc alias-of :alias? true)

          :else
          (do
            (check-private-name! env column)
            (cond
              (next found)  (refuse! (format "the column %s with no table, which more than one source has" column) (format "Write t.%s." column))
              (seq found)   (first found)
              :else         (refuse! (format "the column %s" column)
                                     (str "The sources of this SELECT have no such public column. A name of a Cube member is its short name, as status for orders.status."
                                          next-step-for-a-column)))))))))

(defn- type-of-name
  [{:keys [clause] :as env} node]
  (let [{:keys [member kind aggregate?] :as col} (resolve-column env (:parts node))]
    (when (and (= member :geo) (not= clause :measure))
      (refuse! (str "the geo dimension " (:column col)) "Add dimensions for its latitude and longitude to the data model."))
    (case member
      :measure (when-not (or (:in-measure? env)
                             ;; The form of the docs of Cube: raw rows with a measure column in a subquery, and MEASURE() of it outside.
                             (and (:source-query? env) (= :select clause) (not (:grouped? env))))
                 (refuse! (format "the measure column %s outside MEASURE()" (:column col))
                          (format "Write MEASURE(%s), with GROUP BY for each other column." (:column col))))
      :segment (when-not (contains? #{:where} clause)
                 (refuse! (format "the segment %s outside WHERE" (:column col)) (format "Use it in WHERE: WHERE %s." (:column col))))
      :dimension nil
      :output (when (and (:measure-column col) (not (:in-measure? env)))
                (refuse! (format "the measure column %s of a subquery outside MEASURE()" (:column col))
                         "Cube reads a filter on it as a filter on the aggregate and not on rows. Aggregate it with GROUP BY in the subquery, and filter on its result."))
      (refuse! (str "the column " (:column col))))
    (when (and aggregate? (= :where clause) (not (:over-subquery? env)))
      (refuse! (str "an aggregation in WHERE, through the alias " (:column col)) "Use HAVING."))
    (when (and (:mark-date-only? col) (:zone? *opts*))
      (refuse! (format "the Date column %s under a report time zone on stock Cube" (:column col))
               "Remove the report time zone, or install the ClickHouse dialect, see cube-clickhouse/README.md."))
    (when (and (= :time kind) (= :dimension member)) nil)
    (swap! (:refs env) conj {:clause clause, :column (:column col), :member member, :source (:source col), :col col})
    {:type (or kind :any), :agg? (boolean (or aggregate? (:alias? col))), :col col, :column-node (when-not (:alias? col) node), :member member}))

(defn- time-text
  [node]
  (let [node (unwrapped node)]
    (case (:k node)
      :typed (:v node)
      :cast  (time-text (:x node))
      :str   (:v node)
      nil)))

(defn- constant-number
  "The value of a number with no column, as a BigDecimal, or nil."
  ^BigDecimal [node]
  (let [node (unwrapped node)]
    (case (:k node)
      :num  (sqltext/plain-number (:v node))
      :neg  (when-let [value (constant-number (:x node))]
              (if (= "-" (:op node)) (.negate value) value))
      :cast (when (contains? number-types (:type node)) (constant-number (:x node)))
      nil)))

(defn- constant?
  "True if this expression has no column and no function: a form with one value that the driver writes out."
  [node]
  (let [node (unwrapped node)]
    (case (:k node)
      (:num :str :null :bool :typed) true
      :neg (constant? (:x node))
      :cast (constant? (:x node))
      (:bin :div) (and (constant? (:l node)) (constant? (:r node)))
      false)))

(defn- interval-node?
  "An interval: INTERVAL '1 day', or the cast of a text that a real parser prints for it."
  [node]
  (let [node (unwrapped node)]
    (or (= :interval (:k node))
        (and (= :cast (:k node)) (= "interval" (:type node)) (= :str (:k (unwrapped (:x node))))))))

(defn- both-numbers!
  [node a b]
  (when-not (and (contains? #{:number :null :any} (:type a)) (contains? #{:number :null :any} (:type b)))
    (refuse! (format "%s on a %s and a %s" (source-text node) (name (:type a)) (name (:type b)))
             "Arithmetic reads numbers, and Cube cannot give an interval as a result. Use DATEDIFF for two times.")))

(defn- aggregate-of-measure!
  "An aggregation of a measure column holds that measure only for the clause of its type."
  [fn-name {:keys [col]}]
  (let [wanted (get {"sum" "sum", "count" "sum", "countDistinct" "sum", "countDistinctApprox" "sum", "runningTotal" "sum", "avg" "avg", "min" "min", "max" "max"} (:agg-type col))]
    (when-not (= wanted fn-name)
      (refuse! (format "%s of the measure %s" (str/upper-case fn-name) (:column col))
               (format "Write MEASURE(%s)." (:column col))))))

(defn- aggregate-call
  [{fn-name :name, :keys [args distinct? over?] :as node} env]
  (when (and (:in-aggregate? env) (not (:window-arg? env)))
    (refuse! (str "an aggregation inside an aggregation: " (source-text node))))
  (when-not (or (:aggs? env) over?)
    (refuse! (str "an aggregation in this clause: " (source-text node)) "Use HAVING for a condition on an aggregation."))
  (let [inner (assoc env :in-aggregate? true)
        arg   (first args)]
    (cond
      (= "count" fn-name)
      (do
        (use! :agg/count)
        (when-not (= 1 (count args)) (refuse! (source-text node)))
        (when-not (= :star (:k arg))
          (let [argument (value arg (assoc inner :in-measure? (= :name (:k (unwrapped arg)))))]
            (when (= :measure (:member argument))
              (aggregate-of-measure! fn-name argument))))
        (when (and (= :star (:k arg)) (:parts arg))
          (refuse! (source-text node) "Write COUNT(*)."))
        {:type :number, :agg? true})

      (contains? number-aggregations fn-name)
      (do
        (when-not (= 1 (count args))
          (refuse! (source-text node)))
        (when distinct?
          (refuse! (format "%s with DISTINCT" (str/upper-case fn-name))
                   "Cube drops this DISTINCT, so 4.49 where the truth is 4.5. Read the distinct values in a subquery and add them up there: SELECT SUM(x) FROM (SELECT x FROM t GROUP BY 1) s."))
        (use! :agg/number)
        (let [argument (value arg (assoc inner :in-measure? (= :name (:k (unwrapped arg)))))]
          (when (= :measure (:member argument))
            (use! :agg/of-measure)
            (aggregate-of-measure! fn-name argument))
          (when-not (contains? #{:number :any} (:type argument))
            (refuse! (format "%s of a %s" (str/upper-case fn-name) (name (:type argument)))))
          {:type :number, :agg? true}))

      :else
      (do
        (when-not (= 1 (count args))
          (refuse! (source-text node)))
        (when distinct? (use! :agg/distinct))
        (use! :agg/min-max)
        (let [argument (value arg (assoc inner :in-measure? (= :name (:k (unwrapped arg)))))]
          (when (= :measure (:member argument))
            (use! :agg/of-measure)
            (aggregate-of-measure! fn-name argument))
          {:type (:type argument), :agg? true})))))

(defn- whole-number-literal!
  [node what]
  (let [number (constant-number node)]
    (when-not (and number (<= (.scale (.stripTrailingZeros number)) 0) (<= 0 (.signum number)))
      (refuse! (str what " that is not a whole number in the text")))))

(defn- time-unit-literal!
  "The unit of DATE_TRUNC or DATEDIFF: a literal, of the units or of the custom granularities of the data model."
  [node column]
  (let [node (unwrapped node)]
    (when-not (= :str (:k node))
      (refuse! "a unit that is not a literal" "Write DATE_TRUNC('month', x)."))
    (let [unit (str/lower-case (:v node))]
      (when-not (or (contains? units unit) (contains? (:granularities column) unit))
        (refuse! (format "the unit '%s'" (:v node)) "Use second, minute, hour, day, week, month, quarter, year or a custom granularity of the column."))
      unit)))

(defn- function-call
  [{fn-name :name, :keys [args] :as node} env]
  (let [typed    (fn [] (mapv #(value % env) args))
        arity!   (fn [low high]
                   (when-not (<= low (count args) high)
                     (refuse! (format "%s with %d arguments" (str/upper-case fn-name) (count args)))))
        all-of!  (fn [kind values]
                   (doseq [v values]
                     (when-not (contains? #{kind :null :any} (:type v))
                       (refuse! (format "%s on a %s" (str/upper-case fn-name) (name (:type v)))))))
        agg?     (fn [values] (boolean (some :agg? values)))]
    (case fn-name
      "measure"
      (do
        (use! :agg/measure)
        (arity! 1 1)
        (when-not (or (:aggs? env) (:over? node))
          (refuse! (str "MEASURE() in this clause: " (source-text node)) "Use HAVING for a condition on a measure."))
        (let [arg (unwrapped (first args))]
          (when-not (= :name (:k arg))
            (refuse! (str "MEASURE() of an expression: " (source-text node)) "Add a measure to the data model."))
          (let [{:keys [member col]} (value arg (assoc env :in-measure? true))]
            (when-not (or (= :measure member) (:measure-column col))
              (refuse! (format "MEASURE() of %s, which is not a measure" (source-text arg)) "Use an aggregation."))
            (swap! (:measures env) conj {:column (:column col), :source (:source col), :col col})
            {:type (or (:kind col) :number), :agg? true, :measure col})))

      ;; DATE(x) is CAST(x AS date) in Postgres. The day of a time, in the report time zone, as for the cast.
      "date"
      (do
        (arity! 1 1)
        (let [inner (unwrapped (first args))]
          (when-not (= :name (:k inner))
            (refuse! (str "DATE of an expression: " (source-text node)) "Use DATE(x) of a time column, or DATE 'yyyy-mm-dd' for a literal."))
          (let [argument (value inner env)]
            (when-not (= :time (:type argument))
              (refuse! (format "DATE of a %s" (name (:type argument)))))
            (use! :value/cast-date)
            (edit! (:s node) (:e node) (format "DATE_TRUNC('day', %s)" (source-text inner)))
            {:type :time})))

      "date_trunc"
      (do
        (use! :fn/date-trunc)
        (arity! 2 2)
        (let [{:keys [col type agg?]} (value (second args) env)
              unit                    (time-unit-literal! (first args) col)]
          (when-not (contains? #{:time :any} type)
            (refuse! (format "DATE_TRUNC on a %s" (name type))))
          (when (and col (not= :output (:member col)))
            (swap! (:truncs env) conj {:column (:column col), :source (:source col), :unit unit}))
          {:type :time, :agg? agg?, :trunc {:column (:column col), :unit unit}}))

      "datediff"
      (do
        (use! :fn/datediff)
        (arity! 3 3)
        (when (:zone? *opts*)
          (refuse! "DATEDIFF under a report time zone" "Remove the report time zone."))
        (time-unit-literal! (first args) nil)
        (let [values (mapv #(value % env) (rest args))]
          (all-of! :time values)
          {:type :number, :agg? (agg? values)}))

      ("abs" "floor" "ceil" "ceiling")
      (do
        (use! :fn/number)
        (arity! 1 1)
        (let [values (typed)]
          (all-of! :number values)
          (when (constant? (first args)) (refuse! (str (source-text node) " on a literal") "Write the number."))
          {:type :number, :agg? (agg? values)}))

      "round"
      (do
        (use! :fn/number)
        (arity! 1 2)
        (when (second args) (whole-number-literal! (second args) "the digits of ROUND"))
        (let [values (mapv #(value % env) (take 1 args))]
          (all-of! :number values)
          (when (constant? (first args)) (refuse! (str (source-text node) " on a literal") "Write the number."))
          {:type :number, :agg? (agg? values)}))

      "coalesce"
      (do
        (use! :fn/null)
        (arity! 1 16)
        (let [values (typed)
              kinds  (disj (set (map :type values)) :null :any)]
          (when (next kinds)
            (refuse! (format "COALESCE of a %s" (str/join " and a " (map name (sort kinds))))))
          {:type (or (first kinds) :any), :agg? (agg? values)}))

      "nullif"
      (do
        (use! :fn/null)
        (arity! 2 2)
        (let [values (typed)
              kinds  (disj (set (map :type values)) :null :any)]
          (when (next kinds)
            (refuse! (format "NULLIF of a %s" (str/join " and a " (map name (sort kinds))))))
          {:type (or (first kinds) :any), :agg? (agg? values)}))

      ("lower" "upper")
      (do
        (use! :fn/text)
        (arity! 1 1)
        (let [values (typed)]
          (all-of! :text values)
          {:type :text, :agg? (agg? values)}))

      ("length" "char_length")
      (do
        (use! :fn/text)
        (arity! 1 1)
        (let [values (typed)]
          (all-of! :text values)
          {:type :number, :agg? (agg? values)}))

      ("left" "right")
      (do
        (use! :fn/text)
        (arity! 2 2)
        (whole-number-literal! (second args) (str "the length of " (str/upper-case fn-name)))
        (let [values [(value (first args) env)]]
          (all-of! :text values)
          {:type :text, :agg? (agg? values)}))

      ("substr" "substring")
      (do
        (use! :fn/text)
        (arity! 2 3)
        (doseq [arg (rest args)] (whole-number-literal! arg "the place in SUBSTRING"))
        (let [values [(value (first args) env)]]
          (all-of! :text values)
          {:type :text, :agg? (agg? values)}))

      "strpos"
      (do
        (use! :fn/text)
        (arity! 2 2)
        (when-not (= :str (:k (unwrapped (second args))))
          (refuse! "STRPOS with a second argument that is not a literal"))
        (let [values [(value (first args) env)]]
          (all-of! :text values)
          {:type :number, :agg? (agg? values)}))

      "concat"
      (do
        (use! :fn/text)
        (arity! 1 16)
        (let [values (typed)]
          (all-of! :text values)
          {:type :text, :agg? (agg? values)}))

      (refuse! (format "the function %s" (str/upper-case fn-name))
               (case fn-name
                 "extract"   "Use DATE_TRUNC."
                 "date_part" "Use DATE_TRUNC."
                 "to_char"   "Use DATE_TRUNC."
                 "Add a dimension or a measure with this SQL to the data model.")))))

(defn- window-call
  [{fn-name :name, :keys [args over] :as node} env]
  (use! :agg/window)
  (when (or (:frame? over) (:named? over))
    (refuse! (str "a window with a frame or a name: " (source-text node)) "Write OVER (PARTITION BY a ORDER BY b)."))
  (when (not= :select (:clause env))
    (refuse! (str "a window function outside SELECT: " (source-text node)) "Read it in a subquery."))
  (when-not (or (:grouped? env) (and (seq (:sources env)) (every? :grouped-rows? (:sources env))))
    (refuse! (str "a window function over raw rows: " (source-text node))
             "Group by a column first and use the window over the groups."))
  (doseq [part (concat (:partition over) (map :x (:order over)))]
    (value part (assoc env :window-arg? true, :in-aggregate? false)))
  (doseq [{:keys [nulls]} (:order over)]
    (when (= :first nulls)
      (refuse! "NULLS FIRST" "Cube drops it. Leave it out.")))
  (cond
    (contains? window-only fn-name)
    (do
      (doseq [[i arg] (map-indexed vector args)]
        (if (zero? i)
          (value arg (assoc env :window-arg? true))
          (whole-number-literal! arg "an offset of a window function")))
      {:type :number, :agg? true, :window? true})

    (or (contains? number-aggregations fn-name) (contains? #{"count" "min" "max"} fn-name))
    (let [arg (first args)]
      (if (= :star (:k arg))
        {:type :number, :agg? true, :window? true}
        (assoc (value arg (assoc env :window-arg? true, :aggs? true)) :agg? true, :window? true)))

    :else (refuse! (str "the window function " (str/upper-case fn-name)))))

(defn- call
  [{fn-name :name, :keys [special-args? all? trim-word over?] :as node} env]
  (when (or special-args? all? trim-word)
    (refuse! (str "this form of " (str/upper-case fn-name) ": " (source-text node))
             (when (= "extract" fn-name) "Use DATE_TRUNC, or add a dimension with this SQL to the data model.")))
  (when (and (:distinct? node) (not (contains? #{"count" "avg" "min" "max" "sum" "stddev" "stddev_pop" "stddev_samp"} fn-name)))
    (refuse! (str "DISTINCT in " (str/upper-case fn-name))))
  (when (:filter node)
    (use! :agg/filter)
    (when-not (or (contains? count-kinds fn-name) (contains? number-aggregations fn-name) (contains? #{"min" "max"} fn-name))
      (refuse! (format "%s with FILTER" (str/upper-case fn-name)) "Use WHERE, or CASE inside the aggregation."))
    (predicate (:filter node) (assoc env :clause :case, :aggs? false, :in-aggregate? true, :top? true)))
  (cond
    over?
    (window-call node env)

    (or (contains? count-kinds fn-name) (contains? number-aggregations fn-name) (contains? #{"min" "max"} fn-name))
    (aggregate-call node env)

    :else
    (function-call node env)))

(defn- check-time-literal!
  "A time literal must be one that the driver can read. A fraction of a second has no meaning on a stock Cube, which drops it in a filter."
  [node column-name]
  (let [text   (time-text node)
        parsed (when text (sqltext/parse-time text))]
    (when-not parsed
      (refuse! (format "the time %s" (source-text node)) "Write 'yyyy-mm-dd' or 'yyyy-mm-dd hh:mm:ss'."))
    (when (and (sqltext/time-fraction? parsed) (not (:keeps-fractions? *opts*)))
      (refuse! (format "the time %s with a fraction of a second on %s on stock Cube" (source-text node) column-name)
               "Use whole seconds, or install the ClickHouse dialect, see cube-clickhouse/README.md."))
    (use! :value/time-literal)
    (when-not (contains? #{:typed :str} (:k (unwrapped node)))
      (use! :value/time-literal))
    parsed))

(defn value
  "The type of this expression: {:type, :agg? true if it has an aggregation, ...}. Each other form refuses."
  [node env]
  (case (:k node)
    :num  (do (use! :value/literal) {:type :number, :constant? true})
    :str  (do (use! :value/literal) {:type :text, :constant? true})
    :null (do (use! :value/literal) {:type :null, :constant? true})
    :bool (do (use! :value/literal) {:type :bool, :constant? true})
    :typed (if (contains? time-types (:type node))
             (do (when-not (sqltext/parse-time (:v node))
                   (refuse! (format "the time %s" (source-text node)) "Write 'yyyy-mm-dd' or 'yyyy-mm-dd hh:mm:ss'."))
                 (use! :value/time-literal)
                 {:type :time, :constant? true})
             (refuse! (str "the type " (:type node))))
    :param (refuse! "a parameter that has no value" "Give the parameter a value, or put it in an optional clause.")
    :paren (value (:x node) env)
    :name  (type-of-name env node)
    :star  (refuse! (str (source-text node) " in an expression") "Name the columns.")
    :neg   (do (use! :value/arithmetic)
               (let [inner (value (:x node) env)]
                 (both-numbers! node inner {:type :number})
                 (dissoc inner :column-node :col :member)))
    :bin   (let [{:keys [op l r]} node]
             (if (and (contains? #{"+" "-"} op) (interval-node? r))
               (let [a (value l env)]
                 (when-not (:interval? *opts*)
                   (refuse! (str "the interval in " (source-text node) " on stock Cube") "The ClickHouse dialect of stock Cube has no template for an interval. Install the ClickHouse dialect, see cube-clickhouse/README.md."))
                 (when (:zone? *opts*)
                   (refuse! (str "the interval in " (source-text node) " under a report time zone") "Remove the report time zone."))
                 (when-not (= :time (:type a))
                   (refuse! (str "an interval on a " (name (:type a)))))
                 (when (:constant? a)
                   (refuse! (str "the interval in " (source-text node) " on a literal") "Write the time as one literal."))
                 (use! :value/time-interval)
                 (when (= :cast (:k (unwrapped r)))
                   (edit! (:s r) (:e r) (format "INTERVAL '%s'" (str/replace (:v (unwrapped (:x (unwrapped r)))) "'" "''"))))
                 {:type :time, :agg? (:agg? a)})
               (if (= "||" op)
                 (let [a (value l env), b (value r env)]
                   (use! :value/concat)
                   (when-not (and (contains? #{:text :null :any} (:type a)) (contains? #{:text :null :any} (:type b)))
                     (refuse! (str "|| on a " (name (:type a)) " and a " (name (:type b)))))
                   {:type :text, :agg? (or (:agg? a) (:agg? b)), :constant? (and (:constant? a) (:constant? b))})
                 (do
                   (use! :value/arithmetic)
                   (let [a (value l env), b (value r env)]
                     (both-numbers! node a b)
                     {:type :number, :agg? (or (:agg? a) (:agg? b)), :constant? (and (:constant? a) (:constant? b))})))))
    :div   (let [a (value (:l node) env), b (value (:r node) env)]
             (use! :value/division)
             (both-numbers! node a b)
             {:type :number, :agg? (or (:agg? a) (:agg? b)), :constant? (and (:constant? a) (:constant? b))})
    :cast  (let [{:keys [x type]} node
                 inner (unwrapped x)]
             (cond
               (and (contains? time-types type) (= :str (:k inner)))
               (do
                 (when-not (sqltext/parse-time (:v inner))
                   (refuse! (format "the time %s" (source-text node)) "Write 'yyyy-mm-dd' or 'yyyy-mm-dd hh:mm:ss'."))
                 (use! :value/time-literal)
                 {:type :time, :constant? true})

               (and (= :null (:k inner)) (or (contains? number-types type) (contains? text-types type) (contains? time-types type) (= "boolean" type) (= "bigint" type)))
               ;; Next to an expression, Cube has a graph that grows with each cast of NULL, and 20 of them restart it. NULL has the same value.
               (do (use! :value/literal)
                   (edit! (:s node) (:e node) "NULL")
                   {:type :null, :constant? true})

               (and (contains? #{"timestamp" "timestamptz"} type) (= :name (:k inner)))
               (let [argument (value x env)]
                 (when-not (= :time (:type argument))
                   (refuse! (format "a cast of a %s to %s" (name (:type argument)) type)))
                 (use! :value/cast-time)
                 (edit! (:s node) (:e node) (source-text inner))
                 argument)

               ;; The day of a time, in the report time zone. DATE_TRUNC has that value on each path.
               (and (= "date" type) (= :name (:k inner)))
               (let [argument (value x env)]
                 (when-not (= :time (:type argument))
                   (refuse! (format "a cast of a %s to date" (name (:type argument)))))
                 (use! :value/cast-date)
                 (edit! (:s node) (:e node) (format "DATE_TRUNC('day', %s)" (source-text inner)))
                 {:type :time})

               (contains? number-types type)
               (let [argument (value x env)]
                 (use! :value/cast-number)
                 (when-not (contains? #{:number :any :null} (:type argument))
                   (when-not (and (= :text (:type argument)) (:constant? argument) (constant-number (assoc inner :k :num)))
                     (refuse! (format "a cast of a %s to %s" (name (:type argument)) type))))
                 (assoc argument :type :number :constant? (:constant? argument)))

               (contains? text-types type)
               (let [argument (value x env)]
                 (use! :value/cast-text)
                 (when-not (contains? #{:text :any :null} (:type argument))
                   (refuse! (format "a cast of a %s to %s" (name (:type argument)) type) "Add a dimension with this SQL to the data model."))
                 argument)

               :else (refuse! (format "a cast to %s: %s" type (source-text node)) "Use numeric, decimal, double precision, text, or a literal as a time.")))
    :call  (call node env)
    :case  (let [{:keys [operand whens else]} node
                 operand-type (when operand (value operand env))
                 results      (mapv (fn [{:keys [c r]}]
                                      (if operand
                                        (let [v (value c env)]
                                          (when-not (or (= (:type v) (:type operand-type)) (contains? #{:null :any} (:type v)) (= :any (:type operand-type)))
                                            (refuse! (format "CASE on a %s with WHEN of a %s" (name (:type operand-type)) (name (:type v))))))
                                        (predicate c (assoc env :clause :case, :top? false)))
                                      (value r env))
                                    whens)
                 results      (cond-> results else (conj (value else env)))
                 kinds        (disj (set (map :type results)) :null :any)]
             (use! :value/case)
             (when (next kinds)
               (refuse! (format "CASE with results of a %s" (str/join " and a " (map name (sort kinds))))
                        "Each result must have one type."))
             (when (and operand (contains? #{:time :bool} (:type operand-type)))
               (refuse! (str "CASE x WHEN on a " (name (:type operand-type))) "Write CASE WHEN x = v."))
             {:type (or (first kinds) :any), :agg? (boolean (or (some :agg? results) (:agg? operand-type)))})
    (:cmp :in :between :like :is :is-distinct :not :and :or)
    (do (predicate node (assoc env :clause (if (contains? #{:where :having} (:clause env)) (:clause env) :case), :top? false))
        {:type :bool})
    :interval (refuse! (str "the interval " (source-text node)) "Write the bound of the time as a literal, or use DATEDIFF.")
    :sub (refuse! "a subquery as a value" "Read it in FROM, or use IN with a subquery.")
    :exists (refuse! "EXISTS" "Use IN with a subquery, or a join of the data model.")
    :tuple (refuse! "a list of values in parentheses")
    :at-zone (refuse! "AT TIME ZONE" "The report time zone of Metabase sets the zone.")
    :similar (refuse! "SIMILAR TO" "Use LIKE.")
    (refuse! (str "the expression " (source-text node)))))

;;; Conditions.

(defn- comparable?
  [a b]
  (let [ta (:type a), tb (:type b)]
    (or (= ta tb)
        (contains? #{:any} ta)
        (contains? #{:any} tb))))

(defn- text-number?
  "True if this literal is a number in a text, which compares as a number with a number column."
  [node]
  (let [node (unwrapped node)
        node (if (= :cast (:k node)) (unwrapped (:x node)) node)]
    (and (= :str (:k node)) (some? (sqltext/plain-number (:v node))))))

(def ^:private most-exact-digits
  "A number with a fraction compares as a double. A double holds 15 digits exactly."
  15)

(defn- check-exact-number!
  [node]
  (let [n (unwrapped node)
        number (or (constant-number n) (when (text-number? n) (sqltext/plain-number (time-text (if (= :cast (:k n)) (:x n) n)))))]
    (when (and number
               (pos? (.scale (.stripTrailingZeros number)))
               (> (.precision (.stripTrailingZeros number)) most-exact-digits))
      (refuse! (format "the number %s, with more than %d digits and a fraction" (source-text node) most-exact-digits)
               "Compare it as a text in a dimension with SQL toString(column)."))))

(defn- compare-types!
  "A comparison of `l` and `r`: the kinds of the two sides, and a literal that means a time when the other side is a time."
  [node l r a b]
  (let [ta (:type a), tb (:type b)]
    (cond
      ;; No row, as SQL has it. It is on the list so that a person sees the empty result that SQL gives.
      (or (= :null ta) (= :null tb))
      (use! :pred/compare-null)

      (and (= :time ta) (or (= :text tb) (= :time tb)) (:constant? b))
      (do (check-time-literal! r (source-text l))
          (use! :pred/compare-time))

      (and (= :time tb) (or (= :text ta) (= :time ta)) (:constant? a))
      (do (check-time-literal! l (source-text r))
          (use! :pred/compare-time))

      (and (= :time ta) (= :time tb))
      (use! :pred/compare-time)

      (and (= :number ta) (= :number tb))
      (do (check-exact-number! l) (check-exact-number! r)
          (use! :pred/compare-number))

      (and (= :number ta) (= :text tb) (:constant? b) (text-number? r))
      (do (check-exact-number! r) (use! :pred/compare-number))

      (and (= :text ta) (= :number tb) (:constant? a) (text-number? l))
      (do (check-exact-number! l) (use! :pred/compare-number))

      (and (= :text ta) (= :text tb))
      (use! :pred/compare-text)

      (and (= :bool ta) (= :bool tb))
      (use! :pred/compare-boolean)

      (and (= :bool ta) (:constant? b) (= :num (:k (unwrapped r))) (contains? #{"0" "1"} (:v (unwrapped r))))
      (use! :pred/compare-boolean)

      (and (= :bool tb) (:constant? a) (= :num (:k (unwrapped l))) (contains? #{"0" "1"} (:v (unwrapped l))))
      (use! :pred/compare-boolean)

      (comparable? a b)
      nil

      :else
      (refuse! (format "a comparison of a %s and a %s: %s" (name ta) (name tb) (source-text node)) "Both sides need one type."))))

(defn- time-in-zone!
  "With a report time zone, Cube reads a time literal in that zone only in a date range of WHERE. See `metabase.driver.cube.correct`. In other places the driver cannot show which time the literal means."
  [{:keys [clause over-subquery?]} node a b]
  (when (and (:zone? *opts*)
             (or (= :time (:type a)) (= :time (:type b)))
             (or (not= :where clause) over-subquery?))
    (refuse! (format "a time in %s under a report time zone: %s"
                     (case clause
                       :having "HAVING"
                       :case   "CASE or FILTER"
                       :where  "a filter over a subquery"
                       "this clause")
                     (source-text node))
             "Write it in the WHERE of a query on the table, or remove the report time zone.")))

(defn- constant-text?
  "True if this is a text that the driver can fold: a string, or strings joined with ||."
  [node]
  (let [node (unwrapped node)]
    (case (:k node)
      :str true
      :bin (and (= "||" (:op node)) (constant-text? (:l node)) (constant-text? (:r node)))
      false)))

(defn- check-join-field!
  "a.__cubeJoinField = b.__cubeJoinField."
  [node {:keys [sources] :as env} l r]
  (let [source-of (fn [{:keys [parts]}]
                    (first (filter #(parts-of-source? % (pop parts)) sources)))
        a (source-of l)
        b (source-of r)]
    (when-not (and a b (:table a) (:table b) (not= a b) (contains? #{:from-on :where} (:clause env)))
      (refuse! (str "this use of __cubeJoinField: " (source-text node)) "Write a.__cubeJoinField = b.__cubeJoinField for two tables of FROM."))
    (use! :pred/join-field)))

(defn- join-field?
  [node]
  (let [node (unwrapped node)]
    (and (= :name (:k node)) (= "__cubejoinfield" (peek (:parts node))))))

(defn- null-logic
  "The name of the form of `node` if its value with NULL needs a correction for Cube, or nil. See the note on NULL in metabase.driver.cube.correct."
  [node]
  (case (:k node)
    :not         "NOT"
    :cmp         (when (contains? #{"<>" "!="} (:op node)) (:op node))
    :in          (when (:not? node) "NOT IN")
    :like        (when (:not? node) (str "NOT " (str/upper-case (:op node))))
    :between     (when (:not? node) "NOT BETWEEN")
    :is          (when (contains? #{"true" "false"} (:what node)) (str "IS " (when (:not? node) "NOT ") (str/upper-case (:what node))))
    :is-distinct (str "IS " (when-not (:not? node) "NOT ") "DISTINCT FROM")
    nil))

(defn- check-top-null-logic!
  "Cube reads NOT, <>, NOT IN, NOT LIKE, NOT BETWEEN, IS TRUE and IS DISTINCT FROM with another meaning for NULL, and the driver corrects them only where it proved the form: at
  the top of WHERE and HAVING and in FILTER, joined with AND, OR and NOT. In CASE, as a value, in a function or in GROUP BY or ORDER BY the value with NULL can show, so these
  forms refuse there."
  [node env]
  (when-not (:top? env)
    (when-let [form (null-logic node)]
      (refuse! (format "%s inside CASE, as a value, in a function, or in GROUP BY or ORDER BY: %s" form (source-text node))
               (str "The driver corrects the meaning of NULL for this form only at the top of WHERE, HAVING or FILTER. Write it there, such as "
                    "COUNT(*) FILTER (WHERE x NOT LIKE 'a%'), or use =, LIKE, IN, BETWEEN or IS NULL here.")))))

(defn- conjuncts
  [node]
  (let [node (unwrapped node)]
    (if (= :and (:k node)) (concat (conjuncts (:l node)) (conjuncts (:r node))) [node])))

(defn- non-null-output?
  "True if the one column of the subquery `q` cannot be NULL, because its WHERE has `column IS NOT NULL` at the top."
  [{:keys [withs terms ops]}]
  (let [term (first terms)
        x    (some-> term :items first :x unwrapped)]
    (boolean
     (and (empty? withs) (empty? ops) (= 1 (count terms)) (= :select (:k term)) (= 1 (count (:items term)))
          (= :name (:k x))
          (some (fn [c]
                  (let [y (some-> c :x unwrapped)]
                    (and (= :is (:k c)) (= "null" (:what c)) (:not? c) (= :name (:k y))
                         (= (str/lower-case (peek (:parts y))) (str/lower-case (peek (:parts x)))))))
                (some-> (:where term) conjuncts))))))

(defn- check-not-in-subquery!
  "SQL gives no row for x NOT IN (subquery) when the subquery gives NULL. Cube and ClickHouse read the NULL as a value and give rows, so this form refuses unless the column of
  the subquery cannot be NULL."
  [node env]
  (when (and (:q node) (not= (boolean (:not? node)) (boolean (:negated? env))) (not (non-null-output? (:q node))))
    (refuse! (str "NOT IN with a subquery that can give NULL: " (source-text node))
             (str "SQL gives no row when the subquery gives NULL, and Cube gives rows. Add WHERE column IS NOT NULL to the subquery, such as "
                  "x NOT IN (SELECT y FROM t WHERE y IS NOT NULL), or use IN."))))

(defn predicate
  "A condition of WHERE, HAVING, CASE or FILTER. Each other form refuses."
  [node env]
  (check-top-null-logic! node env)
  (case (:k node)
    :paren (predicate (:x node) env)
    :and   (do (use! :pred/logic) (predicate (:l node) env) (predicate (:r node) env))
    :or    (do (use! :pred/logic) (predicate (:l node) env) (predicate (:r node) env))
    :not   (do (use! :pred/logic) (predicate (:x node) (update env :negated? not)))
    :cmp   (let [{:keys [l r op quantified]} node]
             (when quantified
               (refuse! (format "%s %s" op (str/upper-case quantified)) "Use IN or a join of the data model."))
             (when-not (contains? #{"=" "<>" "!=" "<" "<=" ">" ">="} op)
               (refuse! (str "the operator " op) "Use LIKE."))
             (if (and (join-field? l) (join-field? r) (= "=" op))
               (check-join-field! node env (unwrapped l) (unwrapped r))
               (let [a (value l env), b (value r env)]
                 (when (and (:constant? a) (:constant? b))
                   (use! :pred/constant)
                   (when-not (and (constant-number l) (constant-number r))
                     (refuse! (str "a comparison of two literals: " (source-text node)) "Leave it out.")))
                 (when (and (:zone? *opts*) (or (:trunc a) (:trunc b)))
                   (refuse! (format "DATE_TRUNC in a filter under a report time zone: %s" (source-text node)) "Write x >= a AND x < b."))
                 (when-not (and (:constant? a) (:constant? b))
                   (when (or (and (:constant? a) (not (constant? l))) (and (:constant? b) (not (constant? r))))
                     (refuse! (str "this literal: " (source-text (if (:constant? a) l r)))))
                   (compare-types! node l r a b)
                   (time-in-zone! env node a b)))))
    :in    (let [{:keys [x items q]} node
                 a (value x env)]
             (when (:constant? a)
               (refuse! (str "IN on a literal: " (source-text node)) "Compare a column."))
             (when (and (:zone? *opts*) (= :time (:type a)))
               (refuse! (format "IN on a time under a report time zone: %s" (source-text node)) "Write x >= a AND x < b, or remove the report time zone."))
             (if q
               (let [_       (check-not-in-subquery! node env)
                     outputs (check-query! q (assoc env :in-subquery? true, :negated? false))]
                 (use! :pred/in-subquery)
                 (when-not (= 1 (count outputs))
                   (refuse! "IN with a subquery of more than one column")))
               (do
                 (use! :pred/in-list)
                 (doseq [item items]
                   (let [b (value item (assoc env :in-aggregate? (:in-aggregate? env)))]
                     (when-not (:constant? b)
                       (refuse! (str "IN with a value that is not a literal: " (source-text item)) "Use a literal, or OR with a comparison."))
                     (when-not (= :null (:type b))
                       (compare-types! node x item a b))))
                 (when (> (count items) (long (:longest-list *opts* 2000)))
                   (refuse! (format "a list of %d values" (count items))
                            "Cube works up to 2000, and it can restart on a longer one. Use a shorter list, a range, or add a segment to the data model.")))))
    :between (let [{:keys [x lo hi symmetric?]} node
                   a (value x env), b (value lo env), c (value hi env)]
               (when symmetric? (refuse! "BETWEEN SYMMETRIC" "Write the lower bound first."))
               (use! :pred/between)
               (compare-types! node x lo a b)
               (compare-types! node x hi a c)
               (time-in-zone! env node a b))
    :like  (let [{:keys [x p esc op]} node
                 a (value x env)]
             (when esc (refuse! (str (str/upper-case op) " with ESCAPE") "A backslash escapes in a pattern."))
             (when-not (constant-text? p)
               (refuse! (format "%s with a pattern that is not a literal: %s" (str/upper-case op) (source-text p))
                        "Write the pattern as a text, or a parameter with its wildcards. '%' || {{x}} || '%' also works."))
             (when-not (contains? #{:text :any} (:type a))
               (refuse! (format "%s on a %s" (str/upper-case op) (name (:type a)))))
             (value p env)
             (use! :pred/like))
    :is    (let [{:keys [x what]} node
                 a (value x env)]
             (case what
               "null" (use! :pred/is-null)
               ("true" "false")
               (do
                 (when-not (and (= :bool (:type a)) (= :name (:k (unwrapped x))))
                   (refuse! (str "IS TRUE or IS FALSE on an expression: " (source-text node)) "Write the condition with no IS TRUE, or use it on a boolean column."))
                 (use! :pred/is-boolean))
               (refuse! (str "IS " (str/upper-case what)))))
    :is-distinct (let [{:keys [x r]} node
                       a (value x env), b (value r env)]
                   (when-not (and (:constant? b) (not= :null (:type b)))
                     (refuse! (str "IS DISTINCT FROM a value that is not a literal: " (source-text node)) "Write it with a literal, or with IS NULL and =."))
                   (compare-types! node x r a b)
                   (time-in-zone! env node a b)
                   (use! :pred/is-distinct))
    :name  (let [{:keys [type member col]} (value node env)]
             (cond
               (= :segment member) (use! :select/segment)
               (= :bool type)      (use! :pred/boolean-column)
               :else (refuse! (format "the %s column %s as a condition" (name type) (:column col)) "Compare it with a value.")))
    :call  (refuse! (str "a function as a condition: " (source-text node)) "Compare it with a value.")
    :bool  (refuse! (str "the condition " (source-text node)) "Leave it out.")
    :exists (refuse! "EXISTS" "Use IN with a subquery, or a join of the data model.")
    :similar (refuse! "SIMILAR TO" "Use LIKE.")
    (refuse! (str "the condition " (source-text node)))))

;;; Sources.

(defn- table-source
  [{:keys [parts table column-list? lateral?], source-alias :alias} ctes]
  (when (or column-list? lateral?)
    (refuse! "a column list or LATERAL in FROM"))
  (when (> (count parts) 2)
    (refuse! (str "the table " (str/join "." parts)) "Write its name."))
  (when (and (= 2 (count parts)) (not= "public" (first parts)))
    (refuse! (str "the table " (str/join "." parts)) "Only tables of the data model are on the list."))
  (if-let [outputs (and (= 1 (count parts)) (get ctes table))]
    {:alias source-alias, :table table, :derived? true, :over-subquery? true, :columns outputs, :privates #{}, :grouped-rows? (boolean (::grouped-rows? (meta outputs)))}
    (let [entry (get *catalog* table)]
      (when-not entry
        (refuse! (str "the table " table) "The data model has no such table, and system tables are not on the list."))
      (when (:private? entry)
        (throw (ex-info (format "%s is private in the Cube data model, so it has no values here. A Cube in dev mode reads private cubes, so do not run dev mode in production." table)
                        {:type :invalid-query, :cube/refused "a private cube"})))
      {:alias source-alias, :table table, :columns (:columns entry), :privates (:privates entry), :entry entry})))

(defn- output-columns
  "The columns of a query as a map by name, and in their order under ::order."
  [outputs]
  (with-meta (into {} (map (juxt :name identity)) outputs) {::order (mapv :name outputs)}))

(defn- subquery-source
  [{:keys [q column-list? lateral?] source-alias :alias} env]
  (when (or column-list? lateral?)
    (refuse! "a column list or LATERAL on a subquery"))
  (use! :from/subquery)
  (let [outputs (check-query! q (assoc env :source-query? true))]
    ;; A subquery with GROUP BY or an aggregation gives one row for each group, so a window over its rows is a window over groups.
    {:alias source-alias, :derived? true, :over-subquery? true, :columns (output-columns outputs), :privates #{}, :grouped-rows? (boolean (:grouped? (peek @*selects*)))}))

(defn- join-of-keys
  "The two sides of ON if it compares the keys of a join of the data model, or nil."
  [on sources]
  (let [on (unwrapped on)]
    (when (and (= :cmp (:k on)) (= "=" (:op on)))
      (let [side (fn [node]
                   (let [node (unwrapped node)]
                     (when (and (= :name (:k node)) (next (:parts node)))
                       (when-let [source (first (filter #(parts-of-source? % (pop (:parts node))) sources))]
                         [source (peek (:parts node))]))))
            a    (side (:l on))
            b    (side (:r on))]
        (when (and a b)
          (let [pair #{[(:table (first a)) (second a)] [(:table (first b)) (second b)]}]
            (when (contains? (:join-pairs *opts*) pair)
              [(first a) (first b)])))))))

(defn- name-of-source
  [{source-alias :alias, :keys [table]}]
  (quoted (or source-alias table)))

(defn- check-join!
  "A JOIN or a LEFT JOIN on the join of the data model. Each other form refuses. Cube joins with a left join, so an inner join also needs the key of its right side: `:inner-keys` has them."
  [{:keys [type on using natural?] :as node} left-sources right-source env]
  (when (or using natural?)
    (refuse! "JOIN with USING or NATURAL" "Write ON a.__cubeJoinField = b.__cubeJoinField."))
  (when (or (:derived? right-source) (some :derived? left-sources))
    (refuse! "a JOIN of a subquery" "Read each source in its own query. A subquery has no join of the data model."))
  (when (or (get-in right-source [:entry :view?]) (some #(get-in % [:entry :view?]) left-sources))
    (refuse! "a JOIN of a view" "A view has its joins. Select its columns."))
  (case type
    (:inner :left)
    (let [sources (conj (vec left-sources) right-source)
          on-node (some-> on unwrapped)]
      (when-not on-node
        (refuse! "JOIN with no condition" "Write ON a.__cubeJoinField = b.__cubeJoinField."))
      (cond
        (and (= :cmp (:k on-node)) (join-field? (:l on-node)) (join-field? (:r on-node)) (= "=" (:op on-node)))
        (check-join-field! on-node (assoc env :sources sources, :clause :from-on) (unwrapped (:l on-node)) (unwrapped (:r on-node)))

        (join-of-keys on sources)
        (let [[a b] (join-of-keys on sources)]
          (edit! (:s on) (:e on) (format "%s.__cubeJoinField = %s.__cubeJoinField" (name-of-source a) (name-of-source b))))

        :else
        (refuse! (str "this JOIN condition: " (source-text on)) "Cube reads only the joins of the data model. Write ON a.__cubeJoinField = b.__cubeJoinField, or the two keys of that join."))
      (use! :from/join-model)
      (when (= :inner type)
        (let [right  (:table right-source)
              lefts  (into #{} (map :table) left-sources)
              column (first (for [pair         (:join-pairs *opts*)
                                  [cube member] pair
                                  :when (and (= cube right)
                                             (some (fn [[other _]] (contains? lefts other)) (disj pair [cube member])))]
                              member))]
          (when-not (and column (get (:columns right-source) column))
            (refuse! (format "an inner JOIN to %s, whose key is not a public column" right)
                     "Cube joins with a left join. Write LEFT JOIN with a filter on a public column of that table."))
          (swap! (:inner-keys env) conj (format "%s.%s IS NOT NULL" (name-of-source right-source) (quoted column))))))

    :cross
    (use! :from/cross-model)

    (refuse! (str (str/upper-case (name type)) " JOIN") "Cube joins with a left join of the data model."))
  node)

(defn- from-item
  "A list of sources for this item of FROM."
  [item env left]
  (case (:k item)
    :from-table [(table-source item (:ctes env))]
    :from-sub   [(subquery-source item env)]
    :join       (let [l (from-item (:l item) env left)
                      r (from-item (:r item) env left)]
                  (when (next r) (refuse! "parentheses in a JOIN"))
                  (check-join! item l (first r) env)
                  (conj (vec l) (first r)))
    :from-paren (refuse! "parentheses in FROM")
    :from-fn    (refuse! (str "the function " (some-> item :call :name str/upper-case) " in FROM"))
    (refuse! (str "this form of FROM: " (source-text item)))))

(defn- sources-of
  [from env]
  (let [lists (mapv #(from-item % env nil) from)]
    (when (next lists) (use! :from/cross-model))
    (vec (apply concat lists))))

;;; A select.

(defn- star-columns
  "The written columns of a star: the public dimensions of a table, in the order of the data model, or the columns of a subquery."
  [source qualified?]
  (let [prefix (when qualified? (str (name-of-source source) "."))]
    (if (:derived? source)
      (let [names (or (::order (meta (:columns source))) (keys (:columns source)))]
        (for [column-name names]
          {:text (str prefix (quoted column-name)), :name column-name, :col (get (:columns source) column-name)}))
      (for [{column-name :name, :as col} (get-in source [:entry :dimension-order])
            :when (not (or (:switch? col) (= :geo (:member col)) (:same-source-later? col)))]
        {:text (str prefix (quoted column-name)), :name column-name, :col col}))))

(defn- output-of
  "The column that an item of a select gives."
  [{:keys [x] alias-name :alias} {:keys [type agg? col measure member]}]
  (let [node (unwrapped x)
        own  (cond
               alias-name         alias-name
               (= :name (:k node)) (peek (:parts node))
               (= :call (:k node)) (:name node)
               :else               "column")]
    {:name       own
     :member     :output
     :kind       (if (contains? #{:number :text :time :bool} type) type :any)
     :aggregate? (boolean agg?)
     :measure    measure
     :measure-column (when (or (= :measure member) (:measure-column col)) (or (:measure-column col) col))
     :inner      col}))

(defn- group-keys
  "The form of each expression of GROUP BY. A name reads a column first and then an alias, as SQL does."
  [items group-nodes sources]
  (mapv (fn [node]
          (let [plain (unwrapped node)]
            (cond
              (and (= :num (:k plain)) (sqltext/plain-number (:v plain)))
              (let [position (long (.longValue ^BigDecimal (sqltext/plain-number (:v plain))))]
                (when-not (<= 1 position (count items))
                  (refuse! (format "GROUP BY %d, with %d columns in SELECT" position (count items))))
                (shape (:x (nth items (dec position)))))

              (and (= :name (:k plain)) (= 1 (count (:parts plain)))
                   (not (some #(contains? (:columns %) (first (:parts plain))) sources))
                   (some #(= (:alias %) (first (:parts plain))) items))
              (shape (:x (first (filter #(= (:alias %) (first (:parts plain))) items))))

              :else (shape node))))
        group-nodes))

(defn- comparison-of-name?
  "True if this tree reads the name `column` with no table, outside an aggregation. Inside COUNT, SUM, AVG, MIN, MAX or STDDEV the name is the column of the source on each path, as SQL has it:
  `MIN(x) AS x ... HAVING MAX(x) > 5` keeps the groups of the truth."
  [node column]
  (let [aggregation? (fn [n] (and (= :call (:k n)) (not (:over? n))
                                  (or (contains? count-kinds (:name n)) (contains? number-aggregations (:name n)) (contains? #{"min" "max"} (:name n)))))]
    (some #(and (= :name (:k %)) (= [column] (:parts %)))
          (tree-seq #(and (sqltext/node? %) (not (aggregation? %))) sqltext/kids node))))

(defn- check-alias-collision!
  "An alias with the name of a column of its source that holds an aggregation: Cube cannot plan or reads another meaning when the query names it in HAVING. WHERE and GROUP BY read the column, and ORDER BY the alias, as SQL does."
  [{:keys [items having]} sources]
  (doseq [{alias-name :alias, :keys [x]} items
          :when (and alias-name
                     (not= :name (:k (unwrapped x)))
                     (some #(contains? (:columns %) alias-name) sources)
                     having
                     (comparison-of-name? having alias-name))]
    (refuse! (format "HAVING on the alias %s, which is also a column" alias-name) "Write the aggregation in HAVING, or use another alias.")))

;;; `a CROSS JOIN b WHERE a.__cubeJoinField = b.__cubeJoinField`, and the same with a comma. This is how the docs of Cube write a join of the data model, so it is a left join, and a row of `a` with no row of `b` counts. Next to an expression in WHERE, Cube
;;; reads this text as no rows: 0 where the truth is 2408. With ON it is right, so this form is a LEFT JOIN, and its condition in WHERE is 1 = 1. MEASURE() with no condition is the same join.

(defn- join-field-condition
  "The condition of the top of `where` that joins the source `right` to another, or nil."
  [where right sources]
  (when where
    (let [conditions (fn conditions [node]
                       (case (:k node)
                         :and   (concat (conditions (:l node)) (conditions (:r node)))
                         :paren (conditions (:x node))
                         [node]))
          source-of  (fn [part] (first (filter #(parts-of-source? % (pop (:parts (unwrapped part)))) sources)))]
      (first (filter (fn [{:keys [k l r op]}]
                       (and (= :cmp k) (= "=" op) (join-field? l) (join-field? r)
                            (some #(= right (source-of %)) [l r])))
                     (conditions where))))))

(defn- rewrite-cross!
  "`l-end` and `r-start` are the place of the words between two sources, and `r-end` the end of the right one."
  [l-end r-start r-end right where sources aggregates?]
  (if-let [condition (join-field-condition where right sources)]
    (do
      (edit! l-end r-start " LEFT JOIN ")
      (edit! r-end r-end (str " ON " (source-text condition)))
      (edit! (:s condition) (:e condition) "1 = 1"))
    (if aggregates?
      (let [other (first (remove #(= right %) sources))]
        (edit! l-end r-start " LEFT JOIN ")
        (edit! r-end r-end (format " ON %s.__cubeJoinField = %s.__cubeJoinField" (name-of-source other) (name-of-source right))))
      (refuse! "a cross product: a comma in FROM or a CROSS JOIN with no join condition"
               "Cube ignores a cross product, and it reads the sources through the joins of the data model. Write a.__cubeJoinField = b.__cubeJoinField in WHERE for a join of the data model, or read each source in its own query."))))

(defn- rewrite-cross-joins!
  [from where sources items]
  (let [measure? (some (fn [{:keys [x]}] (some #(and (= :call (:k %)) (= "measure" (:name %))) (sqltext/nodes x))) items)
        named    (fn [item] (first (filter #(= (or (:alias item) (:table item)) (or (:alias %) (:table %))) sources)))
        visit    (fn visit [item]
                   (when (= :join (:k item))
                     (visit (:l item))
                     (visit (:r item))
                     (when (= :cross (:type item))
                       (when-not (= :from-table (:k (:r item)))
                         (refuse! "a CROSS JOIN of a subquery" "Read each source in its own query."))
                       (rewrite-cross! (:e (:l item)) (:s (:r item)) (:e item) (named (:r item)) where sources measure?))))]
    (run! visit from)
    (when (next from)
      (when (or (> (count from) 2) (some #(not= :from-table (:k %)) from))
        (refuse! "a cross product of more than two tables or of a subquery: a comma in FROM" "Use JOIN of the data model, or read each source in its own query."))
      (let [[a b] from]
        (rewrite-cross! (:e a) (:s b) (:e b) (named b) where sources measure?)))))

(defn- check-select!
  [{:keys [items from where group having distinct-on? group-all? distinct?] :as select} env]
  (when (= :group-set (:k (first (filter #(= :group-set (:k %)) group))))
    (let [form (:form (first (filter #(= :group-set (:k %)) group)))]
      (refuse! (str "GROUP BY " form) "Cube drops the rows of the totals. Write the groups and each total as separate SELECT statements with UNION ALL.")))
  (when distinct-on? (refuse! "DISTINCT ON" "Use GROUP BY."))
  (when group-all? (refuse! "GROUP BY ALL" "Name the groups."))
  (when distinct? (use! :query/distinct))
  (when (empty? items)
    (refuse! "a SELECT with no columns" "Name the columns."))
  (let [refs       (atom [])
        measures   (atom [])
        truncs     (atom [])
        inner-keys (atom [])
        base       (assoc env :refs refs, :measures measures, :truncs truncs, :inner-keys inner-keys)
        sources    (if from (sources-of from base) [])
        _          (rewrite-cross-joins! from where sources items)
        over?      (boolean (some :over-subquery? sources))
        env        (assoc base :sources sources, :over-subquery? over?, :aliases {})
        aggregates (fn aggregates [node]
                     (cond
                       ;; SUM(x) OVER () is a window, and it does not group the rows. SUM(COUNT(*)) OVER () has an aggregation in it.
                       (= :call (:k node)) (or (and (not (:over? node))
                                                    (contains? #{"count" "sum" "avg" "min" "max" "measure" "stddev" "stddev_pop" "stddev_samp" "variance" "var_pop" "var_samp"} (:name node)))
                                               (some aggregates (:args node)))
                       (contains? #{:sub :query :exists} (:k node)) false
                       :else (some aggregates (sqltext/kids node))))
        grouped?   (boolean (or (seq group) (some #(aggregates (:x %)) items)))
        env        (assoc env :grouped? grouped?)
        _          (when-not from
                     (use! :query/no-from)
                     (doseq [{:keys [x]} items]
                       (when-not (constant? x)
                         (refuse! (str "this SELECT with no FROM: " (source-text x)) "Read a table of the data model."))))
        select-env (assoc env :clause :select, :aggs? true)
        typed      (mapv (fn [{:keys [x alias] :as item}]
                           (use! (if (= :name (:k (unwrapped x))) :select/column :value/literal))
                           (when alias (use! :select/alias))
                           (when (and alias (some #(contains? (:privates %) alias) sources))
                             (refuse! (format "the alias %s, which is a private member" alias)
                                      "Cube reads a filter on a column of a subquery with this name as a filter on the member. Use another name."))
                           (if (= :star (:k x))
                             (let [targets (if-let [parts (:parts x)]
                                             (filter #(parts-of-source? % (vec parts)) sources)
                                             sources)]
                               (when (empty? targets)
                                 (refuse! (str "the star " (source-text x)) "Its table is not in FROM."))
                               (when grouped?
                                 (refuse! "a star next to an aggregation or GROUP BY" "Name the columns."))
                               (use! :select/star)
                               (let [columns (mapcat #(star-columns % (next sources)) targets)]
                                 (when (empty? columns)
                                   (refuse! "a star on a table with no public columns" "Name the columns."))
                                 (edit! (:s x) (:e x) (str/join ", " (map :text columns)))
                                 {:star columns, :item item}))
                             {:typed (value x select-env), :item item}))
                         items)
        outputs    (vec (mapcat (fn [{:keys [star typed item]}]
                                  (if star
                                    (map (fn [{column-name :name, :keys [col]}]
                                           {:name column-name, :member :output, :kind (:kind col), :aggregate? (:aggregate? col), :inner col})
                                         star)
                                    [(output-of item typed)]))
                                typed))
        aliases    (into {} (keep (fn [{:keys [item typed star]}]
                                    (when (and (not star) (:alias item))
                                      [(:alias item) {:member :output, :kind (let [t (:type typed)] (if (contains? #{:number :text :time :bool} t) t :any)),
                                                      :column (:alias item), :aggregate? (:agg? typed), :trunc (:trunc typed)}])))
                         typed)
        env        (assoc env :aliases aliases)]
    ;; Next to an expression, Cube reads a column with no alias twice: `Schema contains duplicate qualified field name`. `id AS id` is that column.
    (when (and (not grouped?) from (some #(not (or (:star %) (= :name (:k (unwrapped (get-in % [:item :x])))))) typed))
      (doseq [{:keys [star item]} typed
              :let  [plain (unwrapped (:x item))]
              :when (and (not star) (= :name (:k plain)) (not (:alias item)))]
        (edit! (:e item) (:e item) (str " AS " (quoted (peek (:parts plain)))))))
    (when where
      (use! :clause/where)
      (predicate where (assoc env :clause :where, :aggs? false, :top? true)))
    (when (seq group)
      (use! :clause/group)
      (doseq [node group
              :let [plain (unwrapped node)]
              :when (not (and (= :num (:k plain)) (sqltext/plain-number (:v plain))))]
        (value node (assoc env :clause :group, :aggs? false))))
    (when having
      (use! :clause/having)
      (when-not grouped?
        (refuse! "HAVING with no aggregation and no GROUP BY" "Use WHERE."))
      (predicate having (assoc env :clause :having, :aggs? true, :top? true)))
    (check-alias-collision! select sources)
    (when grouped?
      (binding [*one-source?* (not (next sources))]
        (let [keys-of-group (set (group-keys items group sources))]
          (doseq [{:keys [typed item star]} typed
                  :when (and (not star) (not (:agg? typed)) (not (:constant? typed))
                             (not (contains? keys-of-group (shape (:x item)))))]
            (refuse! (format "the column %s, which is in SELECT and not in GROUP BY" (source-text (:x item)))
                     "Add it to GROUP BY, or use it in an aggregation.")))))
    (swap! *selects* conj {:select select, :sources sources, :outputs outputs, :refs @refs, :measures @measures, :truncs @truncs, :inner-keys @inner-keys, :grouped? grouped?, :typed typed, :over-subquery? over?})
    (when-let [missing (seq (remove #(and where (str/includes? (source-text where) %)) @inner-keys))]
      (let [text (str/join " AND " missing)]
        (if where
          (do (edit! (:s where) (:s where) (str text " AND ("))
              (edit! (:e where) (:e where) ")"))
          (let [last-from (peek (vec from))]
            (edit! (:e last-from) (:e last-from) (str " WHERE " text))))))
    outputs))

(defn- check-order!
  [{:keys [order]} outputs env]
  (when (seq order)
    (use! :query/order)
    (let [{:keys [sources]} env]
      (doseq [{:keys [x nulls]} order]
        (when (= :first nulls)
          (refuse! "NULLS FIRST" "Cube drops it. Leave it out, or filter with IS NULL in a separate query."))
        (let [plain (unwrapped x)]
          (cond
            (and (= :num (:k plain)) (sqltext/plain-number (:v plain)))
            (let [position (.longValue ^BigDecimal (sqltext/plain-number (:v plain)))]
              (when-not (<= 1 position (count outputs))
                (refuse! (format "ORDER BY %d, with %d columns in SELECT" position (count outputs)))))

            (and (= :name (:k plain)) (= 1 (count (:parts plain))) (some #(= (:name %) (first (:parts plain))) outputs))
            nil

            sources
            (value x (assoc env :clause :order, :aggs? true, :aliases (into {} (map (juxt :name #(assoc % :column (:name %)))) outputs)))

            :else
            (refuse! (str "ORDER BY " (source-text x) " on a UNION") "Use a position or an alias of the first SELECT.")))))))

(defn- check-limit!
  [{:keys [limit offset fetch?]}]
  ;; FETCH FIRST n ROWS ONLY is LIMIT n, and a real parser prints it so.
  (when fetch?
    (when limit (refuse! "FETCH next to LIMIT" "Use LIMIT."))
    (use! :query/limit)
    (when-let [amount (:amount fetch?)]
      (whole-number-literal! amount "a FETCH"))
    (edit! (:s fetch?) (:e fetch?) (format "LIMIT %s" (if-let [amount (:amount fetch?)] (source-text amount) "1"))))
  (when limit
    (use! :query/limit)
    (whole-number-literal! limit "a LIMIT"))
  (when offset
    (use! :query/offset)
    (whole-number-literal! offset "an OFFSET")))

(defn check-query!
  "The columns that this query gives. Each form that is not on the list refuses."
  [{:keys [withs terms ops] :as query} env]
  (let [ctes (reduce (fn [ctes {cte-name :name, :keys [q recursive? column-list? modifier?]}]
                       (when (or recursive? column-list? modifier?)
                         (refuse! "WITH RECURSIVE, a column list or MATERIALIZED in WITH" "Write WITH name AS (SELECT ...)."))
                       (use! :query/with)
                       (let [outputs (check-query! q (assoc env :ctes ctes, :source-query? true))]
                         (assoc ctes cte-name (vary-meta (output-columns outputs) assoc ::grouped-rows? (and (empty? (:ops q)) (:grouped? (peek @*selects*)))))))
                     (:ctes env)
                     withs)
        env  (assoc env :ctes ctes)]
    (when (seq ops)
      (when (or (:in-subquery? env) (:nested? env))
        (refuse! "a UNION in a subquery" "Write it at the top of the query."))
      (doseq [{:keys [op]} ops]
        (when-not (= "union" op)
          (refuse! (str/upper-case op) "Use UNION ALL, or a filter.")))
      (use! :query/union))
    (let [outputs (reduce (fn [first-outputs term]
                            (when-not (= :select (:k term))
                              (refuse! (if (= :values (:k term)) "VALUES" "a query in parentheses") "Write SELECT ... FROM a table of the data model."))
                            (let [these (check-select! term (assoc env :nested? true))]
                              (when (and first-outputs (not= (count first-outputs) (count these)))
                                (refuse! "a UNION of SELECT statements with other numbers of columns"))
                              (or first-outputs these)))
                          nil
                          terms)]
      (use! :query/select)
      (check-order! query outputs (assoc env
                                         :sources (when-not (seq ops)
                                                    (:sources (peek @*selects*)))
                                         :refs (atom []) :measures (atom []) :truncs (atom []) :inner-keys (atom [])
                                         :grouped? (:grouped? (peek @*selects*))))
      (check-limit! query)
      outputs)))

;;; The whole text.

(defn- apply-edits
  "The text with each edit. A change inside the place of another one stops: only one of them could hold."
  [^String sql edits]
  (let [sorted (sort-by (juxt :s :e) (distinct edits))]
    (loop [at 0, [edit & more] sorted, out (StringBuilder.)]
      (if-not edit
        (do (.append out (subs sql at)) (.toString out))
        (let [{:keys [s e text]} edit]
          (.append out (subs sql at s))
          (.append out ^String text)
          (recur (long e) more out))))))

(defn check
  "Reads `sql`. Returns {:sql the text with its stars and its join keys written out, :tree, :selects, :used the set of constructs}, or it stops with a message. `catalog` is a map from a lower case table to
  {:columns {name col}, :privates #{names}, :private? boolean, :view? boolean, :dimension-order [col]}. `opts` has :zone?, :keeps-fractions?, :interval?, :join-pairs and :longest-list."
  [^String sql catalog opts]
  (loop [text (sqltext/without-unicode-escapes sql), used #{}, tries 0]
    (let [tree (sqltext/parse text)]
      (when-let [reason (:error tree)]
        (refuse! (str "this text: " reason) "Write the query in plain SQL."))
      (let [{:keys [edits] :as result} (binding [*catalog* catalog
                                                 *opts*    (assoc opts :sql text)
                                                 *used*    (atom used)
                                                 *edits*   (atom [])
                                                 *selects* (atom [])]
                                         (check-query! tree {:ctes {}})
                                         {:edits @*edits*, :selects @*selects*, :used @*used*})]
        (if (and (seq edits) (< tries 3))
          (recur (apply-edits text edits) (:used result) (inc tries))
          {:sql text, :tree tree, :selects (:selects result), :used (:used result)})))))
