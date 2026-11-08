(ns metabase.driver.cube.native
  "Native SQL: what `metabase.driver.cube.grammar` admits, what the data model adds to that, and `metabase.driver.cube.correct` for the form that Cube reads as SQL does. A native query passes through
  [[compile-native]] once, for a question, a saved question as a stage, a template variable, a field filter and a snippet. docs/query-compilation.md has the list. A text of the query builder has a known form, so it
  has only the two checks at the end of this file."
  (:require
   [clojure.string :as str]
   [metabase.driver.cube.correct :as correct]
   [metabase.driver.cube.grammar :as grammar]
   [metabase.driver.cube.meta :as cube.meta]
   [metabase.driver.cube.probe :as cube.probe]
   [metabase.driver.cube.sqltext :as sqltext :refer [tokens]])
  (:import
   (java.time DayOfWeek LocalDate LocalDateTime OffsetDateTime ZoneOffset ZonedDateTime)
   (java.time.format DateTimeFormatter)
   (java.time.temporal ChronoUnit TemporalAdjusters)))

(set! *warn-on-reflection* true)

(defn- refuse!
  [text]
  (throw (ex-info text {:type :invalid-query})))

(defn- word?
  [tok text]
  (and (= :word (:kind tok)) (= text (:text tok))))

(defn- name-token?
  [tok]
  (contains? #{:word :ident} (:kind tok)))

(defn- short-name
  [member-name]
  (str/lower-case (last (str/split (str member-name) #"\."))))

;;; ------------------------------------------------- the data model ------------------------------------------------

(defn- column-kind
  [member-type]
  (case member-type
    "number"  :number
    "string"  :text
    "time"    :time
    "boolean" :bool
    :any))

(defn- catalog
  "What the grammar knows about the tables: `metabase.driver.cube.grammar/check`. With `date-only?`, the driver knows a Date column that a stock Cube reads as a time at midnight UTC."
  [cube-meta date-only?]
  (let [private (cube.meta/private-members cube-meta)
        hidden  (cube.meta/private-cubes cube-meta)]
    (into {}
          (map (fn [{cube-name :name, :keys [dimensions measures segments]}]
                 (let [table      (str/lower-case cube-name)
                       privates   (get private table #{})
                       switches   (cube.meta/switch-dimension-names cube-meta cube-name)
                       later      (into #{} (mapcat (comp rest sort)) (cube.meta/same-source-groups dimensions))
                       dimension  (fn [{member :name, member-type :type, :keys [granularities] :as d}]
                                    (let [own (short-name member)]
                                      {:name               own
                                       :full-name          member
                                       :member             (if (contains? #{"geo" "geo-in-view"} member-type) :geo :dimension)
                                       :kind               (column-kind member-type)
                                       :granularities      (into #{} (map (comp str/lower-case :name)) granularities)
                                       :switch?            (contains? switches own)
                                       :same-source-later? (contains? later own)
                                       :mark-date-only?    (and date-only? (cube.probe/date-only? cube-meta member))
                                       :primary-key?       (boolean (:primaryKey d))}))
                       measure    (fn [{member :name, member-type :type, :keys [aggType cumulative]}]
                                    {:name      (short-name member)
                                     :full-name member
                                     :member    :measure
                                     :kind      (column-kind member-type)
                                     :agg-type  aggType
                                     :rolling?  (boolean cumulative)})
                       segment    (fn [{member :name}]
                                    {:name (short-name member), :full-name member, :member :segment, :kind :bool})
                       public?    (fn [{own :name}] (not (contains? privates own)))
                       shown      (filter public? (map dimension dimensions))
                       columns    (concat shown (filter public? (map measure measures)) (filter public? (map segment segments)))]
                   [table {:cube-name       cube-name
                           :private?        (contains? hidden table)
                           :view?           (cube.meta/view? cube-meta cube-name)
                           :privates        privates
                           :columns         (into {} (map (juxt :name identity)) columns)
                           :dimension-order (vec shown)}])))
          (:cubes cube-meta))))

(defn- join-pairs
  "Each pair of the keys of a join of the data model, with lower case names."
  [cube-meta]
  (into #{}
        (map (fn [pair] (into #{} (map (fn [[cube member]] [(str/lower-case cube) (str/lower-case member)])) pair)))
        (cube.meta/join-pairs cube-meta)))

;;; --------------------------------------------- what a select reads -------------------------------------------------

(defn- table-of
  [{:keys [source]}]
  (:table source))

(defn- names-in
  "The short names of the columns of each ref of this clause, or each clause."
  [refs & [clause]]
  (into #{} (comp (filter #(or (nil? clause) (= clause (:clause %)))) (map :column)) refs))

(defn- top-conditions
  [node]
  (case (:k node)
    :and   (concat (top-conditions (:l node)) (top-conditions (:r node)))
    :paren (top-conditions (:x node))
    [node]))

(defn- unwrapped
  [node]
  (if (= :paren (:k node)) (recur (:x node)) node))

(defn- column-of-node
  [node]
  (let [node (unwrapped node)]
    (when (= :name (:k node))
      (peek (:parts node)))))

(defn- literal-time-of
  [node]
  (let [node (unwrapped node)]
    (case (:k node)
      (:str :typed) (sqltext/parse-time (:v node))
      :cast         (literal-time-of (:x node))
      nil)))

(defn- utc-time
  "The clock of a literal."
  ^LocalDateTime [{^LocalDateTime value :time, ^ZoneOffset offset :offset}]
  (if offset
    (.toLocalDateTime (.withZoneSameInstant (.atZone value offset) ZoneOffset/UTC))
    value))

(def ^:private flipped
  {"<" ">", "<=" ">=", ">" "<", ">=" "<=", "=" "="})

(defn- bounds-on
  "Each `column op literal` at the top of this WHERE, as {:op, :time}. Each other condition that names the column gives {:other true}."
  [where column]
  (when where
    (keep (fn [condition]
            (let [named? (some #(and (= :name (:k %)) (= column (peek (:parts %)))) (sqltext/nodes condition))]
              (when named?
                (or (case (:k condition)
                      :cmp (let [{:keys [l r op]} condition]
                             (cond
                               (and (= column (column-of-node l)) (literal-time-of r)) {:op op, :time (utc-time (literal-time-of r))}
                               (and (= column (column-of-node r)) (literal-time-of l) (flipped op)) {:op (flipped op), :time (utc-time (literal-time-of l))}))
                      :between (when (and (not (:not? condition)) (= column (column-of-node (:x condition)))
                                          (literal-time-of (:lo condition)) (literal-time-of (:hi condition)))
                                 {:op :between, :low (utc-time (literal-time-of (:lo condition))), :high (utc-time (literal-time-of (:hi condition)))})
                      nil)
                    {:other true}))))
          (top-conditions where))))

(defn- has-or-not?
  [where]
  (some #(contains? #{:or :not} (:k %)) (sqltext/nodes where)))

;;; ------------------------------------------------ one join path -----------------------------------------------

(defn- check-one-path-of-select!
  "Cube joins a cube once in a query. A view with two join paths to one cube gives the same value for both columns."
  [{:keys [select refs]}]
  (doseq [[table of-table] (group-by table-of (filter :source refs))
          :let  [entry   (get-in (first of-table) [:source :entry])
                 cube-dimensions (:same-source entry)
                 used    (names-in of-table)]
          group cube-dimensions
          :let  [both (sort (filter used group))]
          :when (next both)]
    (refuse! (format (str "The columns %s of %s come from one cube through different join paths. Cube joins that cube "
                          "once in a query, so both columns would show the same value and the number would be wrong. "
                          "Use one of these columns in a query, or use a join alias in the data model.")
                     (str/join " and " both) table)))
  (when (:where select)
    (doseq [[table of-table] (group-by table-of (filter :source refs))
            :let  [entry (get-in (first of-table) [:source :entry])
                   used  (names-in of-table)]
            group (:same-sql entry)
            :let  [both (sort (filter used group))]
            :when (next both)]
      (refuse! (format (str "The columns %s of %s have equal SQL. With a filter in the query, Cube reads the first subquery for "
                            "both columns, so one number would be wrong. Use one of these columns in a query, or give one "
                            "of them other SQL in the data model, such as {orders.count} + 0.")
                       (str/join " and " both) table)))))

;;; ---------------------------------------------- rolling window measures -----------------------------------------

(defn- bucket-start
  ^LocalDateTime [unit ^LocalDateTime t]
  (let [day (.truncatedTo t ChronoUnit/DAYS)]
    (case unit
      "second"  (.truncatedTo t ChronoUnit/SECONDS)
      "minute"  (.truncatedTo t ChronoUnit/MINUTES)
      "hour"    (.truncatedTo t ChronoUnit/HOURS)
      "day"     day
      ;; DATE_TRUNC('week') on Cube starts on Monday.
      "week"    (.with day (TemporalAdjusters/previousOrSame DayOfWeek/MONDAY))
      "month"   (.withDayOfMonth day 1)
      "quarter" (.withDayOfMonth (.withMonth day (inc (* 3 (quot (dec (.getMonthValue day)) 3)))) 1)
      "year"    (.withDayOfYear day 1)
      nil)))

(defn- on-boundary?
  [unit ^LocalDateTime t]
  (and t (some-> (bucket-start unit t) (.equals t))))

(defn- bound-on-boundary?
  "The range of a bucket ends at the last second before the next bucket, so an inclusive bound is one second earlier."
  [unit op ^LocalDateTime t]
  (and t
       (case op
         (:>= :<) (on-boundary? unit t)
         (:> :<=) (on-boundary? unit (.plusSeconds t 1))
         false)))

(defn- time-column-of-truncs
  "The first DATE_TRUNC on a time column of this table in a select."
  [truncs table]
  (first (filter #(= table (get-in % [:source :table])) truncs)))

(defn- check-rolling!
  [{:keys [select measures truncs]} {:keys [rolling-reason rolling-zone-reason]}]
  (when-let [{:keys [column], :as used} (first (filter #(get-in % [:col :rolling?]) measures))]
    (let [table (:table (:source used))
          where (:where select)
          {time-column :column, unit :unit} (time-column-of-truncs truncs table)]
      (when-not time-column
        (refuse! (format (str "%s is a rolling window measure. With no time bucket, Cube returns the total of all rows, not "
                              "the window. Group by DATE_TRUNC on a time column and add a date filter on whole buckets.")
                         column)))
      (when (and where (has-or-not? where))
        (refuse! (format (str "%s is a rolling window measure, and Cube computes it for each whole bucket. The driver cannot "
                              "show that a filter with OR or NOT keeps whole buckets, so write the filter with >= and <.")
                         column)))
      (when (some :other (bounds-on where time-column))
        (refuse! (format (str "%s is a rolling window measure, and Cube computes it for each whole bucket. The driver cannot read "
                              "how the query uses %s, so it cannot show that the filter keeps whole buckets. Write the filter "
                              "with >= and <.")
                         column time-column)))
      (when-not (bucket-start unit (LocalDateTime/of 2025 1 1 0 0))
        (refuse! (format (str "%s is a rolling window measure, and the driver cannot read the bucket '%s' of native SQL. "
                              "Use a bucket of day, week, month, quarter or year, or the query builder.")
                         column unit)))
      (let [cube-name (get-in used [:source :entry :cube-name])]
        (doseq [question [rolling-zone-reason rolling-reason]
                :when    question]
          (when-let [reason (question (str cube-name "." column) (str cube-name "." time-column) (keyword unit))]
            (refuse! reason))))
      (doseq [{:keys [op time low high]} (bounds-on where time-column)]
        (let [ok? (case op
                    :between (and (bound-on-boundary? unit :>= low) (bound-on-boundary? unit :<= high))
                    "="      (and (bound-on-boundary? unit :>= time) (bound-on-boundary? unit :<= time))
                    (bound-on-boundary? unit (keyword op) time))]
          (when-not ok?
            (refuse! (format (str "%s is a rolling window measure, and Cube computes it for each whole %s. This date filter "
                                  "starts or ends inside a %s, and Cube does not apply that part, so the number would count "
                                  "rows outside the filter. Use a filter on whole %ss.")
                             column unit unit unit))))))))

;;; ------------------------------------------------- raw rows --------------------------------------------------

(defn- check-raw-select!
  "A raw select gives each row once for each value of a `switch` dimension."
  [{:keys [refs grouped?]}]
  (when-not grouped?
    (doseq [{:keys [col column source]} refs
            :when (:switch? col)]
      (refuse! (format (str "%s of %s is a switch dimension. A select of raw rows gives each row once for each of its values, so the rows "
                            "would be wrong. Group by it with a measure, or leave out this column.")
                       column (:table source))))))

;;; -------------------------------------------- what the data model reads --------------------------------------------

(defn- check-value-read!
  "The SQL of a cube can read a filter of Cube as a value. See `cube.probe/value-read-members`."
  [{:keys [select sources]} value-read]
  (let [where (:where select)]
    (doseq [{:keys [entry]} sources
            :let  [cube-name (:cube-name entry)]
            :when cube-name
            [member kind] (value-read cube-name)]
      (let [found (bounds-on where member)
            ops   (set (map :op found))]
        (when (or (and where (has-or-not? where))
                  (if (= :time kind)
                    ;; `>` has its own form, which is not a date range, so Cube would give no value.
                    (or (contains? ops ">")
                        (not (or (:between ops)
                                 (and (contains? ops ">=") (some ops ["<" "<="])))))
                    (not= #{"="} (set (keep (fn [condition]
                                              (when (and (= :cmp (:k condition)) (= member (column-of-node (:l condition))))
                                                (:op condition)))
                                            (when where (top-conditions where)))))))
          (refuse! (format (if (= :time kind)
                             (str "The SQL of %s reads the date range of %s as a value, through FILTER_PARAMS. Cube gives that value only for a "
                                  "lower and an upper bound, with no OR and no NOT. With another filter or none, the database reads 1 = 1 "
                                  "there, so the number would be wrong. Write %s >= a AND %s < b.")
                             (str "The SQL of %s reads the filter on %s as a value, through FILTER_PARAMS. Cube gives that value only for one "
                                  "equal value. With another filter or none, the database reads 1 = 1 there, so the number would be wrong. "
                                  "Write %s = a, with no OR and no NOT."))
                           cube-name member member member)))))))

(defn- check-time-shift!
  [{:keys [select measures truncs]} time-shift?]
  (doseq [{:keys [column source]} measures
          :let  [cube-name (get-in source [:entry :cube-name])
                 times     (into #{} (comp (filter #(= :time (:kind %))) (map :name)) (get-in source [:entry :dimension-order]))
                 where     (:where select)]
          :when (and cube-name
                     (empty? truncs)
                     (not (some (fn [time-column] (seq (bounds-on where time-column))) times))
                     (time-shift? cube-name (str cube-name "." column)))]
    (refuse! (format (str "%s has a time shift. With no time bucket and no date filter, Cube returns the total of all rows, and "
                          "a shift of that is not a number that you can read. Group by DATE_TRUNC on a time column, or add a "
                          "date filter.")
                     column))))

(defn- check-granularity!
  [{:keys [measures truncs]} wrong-bin-reason]
  (doseq [{:keys [column unit source]} truncs
          :let  [cube-name (get-in source [:entry :cube-name])
                 own       (for [{measure :column, measure-source :source} measures
                                 :when (= (:table measure-source) (:table source))]
                             (str cube-name "." measure))]
          :when cube-name]
    (when-let [reason (wrong-bin-reason cube-name column unit own)]
      (refuse! reason))))

(defn- with-index
  "The columns of each table, as `check-fan-out!` reads them."
  [tables]
  (into {}
        (map (fn [[table {:keys [columns cube-name]}]]
               [table {:cube-name  cube-name
                       :dimensions (into #{} (comp (filter #(= :dimension (:member %))) (map :name)) (vals columns))}]))
        tables))

;;; ---------------------------------------- an aggregation of a joined dimension ----------------------------------

(def ^:private row-aggregations
  "The aggregations that give another number if a row counts once and not for each row of the query."
  #{"sum" "avg" "count" "stddev" "stddev_pop" "stddev_samp" "variance" "var_pop" "var_samp"})

(defn- check-fan-out!
  "Cube reads each row of a cube once. Next to a one to many join, SUM(users.id) over users and orders gives the sum of the users, where SQL counts each user once for each order. And a view with such a
  join has no one set of rows."
  [cube-meta index tree]
  (when-not (:error tree)
    (doseq [select (filter #(= :select (:k %)) (sqltext/nodes tree))
            :let   [sources (filter :table (mapcat (fn visit [item]
                                                     (case (:k item)
                                                       :join (concat (visit (:l item)) (visit (:r item)))
                                                       :from-paren (mapcat visit (:items item))
                                                       [item]))
                                                   (:from select)))
                    by-name (into {} (mapcat (fn [{:keys [table alias]}]
                                               (cond-> [[table table]] alias (conj [alias table]))))
                                  sources)
                    member-table (fn [[qualifier column]]
                                   (if qualifier
                                     (get by-name qualifier)
                                     (first (filter #(contains? (:dimensions (get index %)) column) (map :table sources)))))
                    cube-name-of (fn [table] (:cube-name (get index table)))]
            call   (filter #(and (= :call (:k %))
                                 (contains? row-aggregations (:name %))
                                 (not (:distinct? %)))
                           (tree-seq #(not (contains? #{:sub :query :exists} (:k %))) sqltext/kids
                                     {:k :items, :s 0, :e 0, :items (:items select)}))]
      (let [args    (:args call)
            columns (keep (fn [node]
                            (when (= :name (:k node))
                              [(when (next (:parts node)) (peek (pop (:parts node)))) (peek (:parts node))]))
                          (mapcat sqltext/nodes args))]
        (doseq [{:keys [table]} sources
                :let  [view (cube-name-of table)]
                :when view
                :let  [[one many] (cube.meta/view-fan-out cube-meta view)]
                :when one]
          (when (or (and (= "count" (:name call)) (= :star (:k (first args))))
                    (some #(contains? (:dimensions (get index table)) (second %)) columns))
            (refuse! (format (str "The view %s reads %s through a one to many join from %s, and Cube joins only the cubes of the columns of a query. So %s does not read the rows of the join, and the "
                                  "number would be wrong. Use MEASURE() of a measure of the view, or start from the cube with the join.")
                             view many one (str/upper-case (:name call))))))
          ;; A row of a joined cube that many rows read: see compile.join.total_of_dimension_refused.
        (doseq [column columns
                :let  [table (member-table column)
                       start (:table (first sources))
                       start-cube (cube-name-of start)]
                :when (and table start-cube
                           (contains? (:dimensions (get index table)) (second column)))]
          (when (and (not= table start)
                     (not (cube.meta/fans-out? cube-meta start-cube start-cube (cube-name-of table))))
            (refuse! (format (str "Cube cannot add up the dimension %s of %s over the rows of %s. It reads each row of the joined cube once, so "
                                  "the number would be wrong. Add a measure to the data model, or start from %s.")
                             (second column) table start table)))
          (when (and (= table start) (cube.meta/joined-member-of-view? cube-meta start-cube (second column)))
            (let [[row-count] (cube.meta/row-count-measure cube-meta start-cube)]
              (refuse! (str (format (str "Cube cannot add up the column %s of the view %s over the rows of the view, because this column comes from a joined cube, and Cube reads each of "
                                         "its rows once. So the number would be wrong. ")
                                    (second column) table)
                            (if row-count
                              (format (str "Weight each value with the count of rows, which gives the exact number: SELECT SUM(%s * n), SUM(n) FROM (SELECT %s, MEASURE(%s) AS n FROM %s "
                                           "GROUP BY 1) t. A WHERE goes inside the subquery. Or add a measure to the data model.")
                                      (second column) (second column) row-count table)
                              "Add a measure to the data model, or start from the cube of this column."))))))
        (doseq [column columns
                :let  [table (member-table column)
                       own   (cube-name-of table)]
                :when (and own (contains? (:dimensions (get index table)) (second column)))
                {other :table} sources
                :let  [other-cube (cube-name-of other)]
                :when (and other-cube
                           (not (cube.meta/view? cube-meta own))
                           (not (cube.meta/view? cube-meta other-cube))
                           (cube.meta/fans-out? cube-meta (cube-name-of (:table (first sources))) own other-cube))]
          (refuse! (format (str "Cube cannot add up the dimension %s of %s next to the one to many join to %s. It reads each row of %s once, and SQL over the join reads it once for each row of %s, so "
                                "the number would be wrong. Group by a key of %s in a subquery with COUNT(*), or add a measure to the data model.")
                           (second column) table other table other table)))))))

;;; ------------------------------------------------ a parameter ---------------------------------------------------

(def ^:private ^DateTimeFormatter date-format (DateTimeFormatter/ofPattern "yyyy-MM-dd HH:mm:ss.SSSxxx"))

(defn- literal-of-parameter
  "The text of a parameter of a native query. Cube reads a parameter of a time as `replaced_placeholder` above a CubeScan, and a literal on each path. The text quotes each quote, and a text with a backslash before a quote
  stops, because another dialect reads that quote as part of the string."
  [value]
  (cond
    (nil? value)     "NULL"
    (true? value)    "TRUE"
    (false? value)   "FALSE"
    (number? value)  (str (if (ratio? value) (bigdec value) value))
    (string? value)  (do
                       (when (str/includes? value "\\'")
                         (grammar/refuse! "a text with a backslash before a quote in a parameter"))
                       (str "'" (str/replace value "'" "''") "'"))
    (instance? LocalDate value)      (format "TIMESTAMP '%s 00:00:00'" value)
    (instance? LocalDateTime value)  (format "TIMESTAMP '%s'" (str/replace (str value) "T" " "))
    (instance? OffsetDateTime value) (format "'%s'" (.format date-format ^OffsetDateTime value))
    (instance? ZonedDateTime value)  (format "'%s'" (.format date-format (.toOffsetDateTime ^ZonedDateTime value)))
    :else (grammar/refuse! (str "a parameter of the type " (.getName (class value))) "Use a text, a number or a date.")))

(defn inline-parameters
  "`sql` with the literal of each `?`. The lexer finds them, so a `?` in a string or a comment stays."
  [^String sql params]
  (if (empty? params)
    sql
    (let [places (filter #(= :param (:kind %)) (tokens sql))
          out    (StringBuilder.)]
      (when-not (= (count places) (count params))
        (grammar/refuse! (format "a text with %d places for %d parameters" (count places) (count params))))
      (let [end (reduce (fn [at [{:keys [start end]} value]]
                          (.append out (subs sql at start))
                          (.append out ^String (literal-of-parameter value))
                          (long end))
                        0
                        (map vector places params))]
        (.append out (subs sql end))
        (.toString out)))))

;;; -------------------------------------------------- the whole text -------------------------------------------------

(def ^:private most-conditions
  "The most conditions joined with AND or OR that the driver sends. Each one adds to the rewrite graph of Cube: 50 stop on the limit of the planner, and 2000 restart Cube with no message to the client."
  500)

(defn- check-conditions!
  [^String sql]
  (when (> (count sql) 4000)
    (let [conditions (count (filter #(or (word? % "or") (word? % "and")) (tokens sql)))]
      (when (> conditions most-conditions)
        (refuse! (format (str "This query has %d conditions joined with AND or OR, and that is too long for the planner of Cube. It stops on about 50, and a longer text can restart it. Write one column and many values as IN, "
                              "use a range, or add a segment to the data model.")
                         conditions))))))

(defn- with-groups
  "The groups of columns of each table, from the data model."
  [catalog-map cube-meta]
  (into {}
        (map (fn [{cube-name :name, :keys [dimensions]}]
               (let [table (str/lower-case cube-name)]
                 [table (when-let [entry (get catalog-map table)]
                          (assoc entry
                                 :same-source (cube.meta/same-source-groups dimensions)
                                 :same-sql (cube.meta/same-sql-groups dimensions)))])))
        (:cubes cube-meta)))

(defn- range-columns
  "The time columns where a bound stays a date range of Cube, because a rollup, a rolling window or a time shift needs it."
  [cube-meta selects]
  (into #{}
        (mapcat (fn [{:keys [sources]}]
                  (for [{:keys [entry]} sources
                        :let  [cube (first (filter #(= (:cube-name entry) (:name %)) (:cubes cube-meta)))]
                        :when (and cube (or (seq (:preAggregations cube))
                                            (cube.meta/view? cube-meta (:name cube))
                                            (some :cumulative (:measures cube))))
                        col   (:dimension-order entry)
                        :when (= :time (:kind col))]
                    (:name col))))
        selects))

(defn- correction-context
  "What the corrections need to know about the tables that this text reads."
  [cube-meta selects {:keys [zone keeps-fractions? value-read]}]
  (let [entries (into [] (comp (mapcat :sources) (keep :entry)) selects)
        columns (mapcat (comp vals :columns) entries)
        names   (fn [pred] (into #{} (comp (filter pred) (map :name)) columns))
        view-names (into #{} (comp (filter :view?) (map (comp str/lower-case :cube-name))) entries)
        measures (names #(= :measure (:member %)))
        dimensions (names #(= :dimension (:member %)))]
    {:numbers          (names #(and (= :dimension (:member %)) (= :number (:kind %))))
     :booleans         (names #(and (= :dimension (:member %)) (= :bool (:kind %))))
     :times            (names #(and (= :dimension (:member %)) (= :time (:kind %))))
     :measures         (reduce disj measures dimensions)
     :count-measures   (names #(contains? #{"count" "countDistinct" "countDistinctApprox"} (:agg-type %)))
     :views            view-names
     :zone             zone
     :keeps-fractions? keeps-fractions?
     ;; A time that the SQL of a cube reads through FILTER_PARAMS needs a date range too: Cube gives the value only from one.
     :range-columns    (into (range-columns cube-meta selects)
                             (when value-read
                               (for [{:keys [entry]} (mapcat :sources selects)
                                     :let  [cube-name (:cube-name entry)]
                                     :when cube-name
                                     [member kind] (value-read cube-name)
                                     :when (= :time kind)]
                                 member)))}))

(defn compile-native
  "The SQL that goes to Cube for this native text, or a refusal. `questions` has the answers that need Cube: :keeps-fractions? a boolean, :zone the report time zone if it is not UTC, :time-shift? a function of the cube and the full
  name of a measure, :wrong-bin-reason a function of the cube, the time member, the granularity and the full names of the measures, :rolling-reason a function of the full names of a measure and a time member and the unit,
  :rolling-zone-reason, and :value-read a function of a cube that gives `cube.probe/value-read-members`."
  [cube-meta ^String sql params {:keys [keeps-fractions? zone time-shift? value-read wrong-bin-reason] :as questions}]
  (let [text  (inline-parameters sql params)
        _     (check-conditions! text)
        tables (with-groups (catalog cube-meta (not keeps-fractions?)) cube-meta)
        {checked :sql, :keys [selects tree]} (grammar/check text tables {:zone?            (some? zone)
                                                                         :keeps-fractions? keeps-fractions?
                                                                         :join-pairs       (join-pairs cube-meta)})]
    (check-fan-out! cube-meta (with-index tables) tree)
    (doseq [select selects]
      (check-one-path-of-select! select)
      (check-rolling! select questions)
      (check-raw-select! select)
      (when value-read (check-value-read! select value-read))
      (when time-shift? (check-time-shift! select time-shift?))
      (when wrong-bin-reason (check-granularity! select wrong-bin-reason)))
    (correct/correct checked (correction-context cube-meta selects {:zone zone, :keeps-fractions? keeps-fractions?, :value-read value-read}))))

(defn with-row-limit
  "`sql` with at most `n` rows at the top. Metabase reads at most `n` rows of a native query and drops the rest, so the result that a person sees is the same: the
  driver adds LIMIT `n` to a query with no LIMIT or FETCH, also after an OFFSET, and it writes `n` in place of a larger LIMIT or FETCH. With ORDER BY these are the first
  `n` rows in that order. Cube Store sends all rows of a query in one message, and with about a million rows the message is too large: Cube then retries with no end,
  and the query hangs. See native.row_limit."
  [^String sql n]
  (let [tree (sqltext/parse sql)]
    (if (or (nil? n) (:error tree) (not= :query (:k tree)))
      sql
      (let [own (or (:limit tree) (some-> (:fetch? tree) :amount))]
        (cond
          (and own (= :num (:k own)) (re-matches #"\d+" (:v own)) (> (bigint (:v own)) n))
          (str (subs sql 0 (:s own)) (long n) (subs sql (:e own)))

          (or own (:fetch? tree))
          sql

          :else
          (str (str/replace sql #"[\s;]+$" "") "\nLIMIT " (long n)))))))

(defn top-limit
  "The number of the LIMIT or FETCH at the top of `sql`, or nil."
  [^String sql]
  (let [tree (sqltext/parse sql)
        own  (when (and (not (:error tree)) (= :query (:k tree))) (or (:limit tree) (some-> (:fetch? tree) :amount)))]
    (when (and own (= :num (:k own)) (re-matches #"\d+" (:v own)))
      (long (bigint (:v own))))))

(defn admit-native
  "The result of [[compile-native]] with no question to Cube, for `dev/fuzz/admit.clj`: {:sql, :used} or a refusal."
  [cube-meta ^String sql params opts]
  (let [text   (inline-parameters sql params)
        tables (with-groups (catalog cube-meta (not (:keeps-fractions? opts))) cube-meta)
        {checked :sql, :keys [used]} (grammar/check text tables (assoc opts :join-pairs (join-pairs cube-meta)))]
    {:sql checked, :used used}))

(defn joined-rows?
  "True if this native question reads raw rows through a join or a view, so a later stage that aggregates it needs a limit. See `correct/joined-rows?`."
  [cube-meta ^String sql]
  (let [tree (sqltext/parse sql)]
    (and (not (:error tree))
         (correct/joined-rows? tree (into #{}
                                          (comp (filter :type) (filter #(= "view" (:type %))) (map (comp str/lower-case :name)))
                                          (:cubes cube-meta))))))

;;; ------------------------------------------- SQL that the driver wrote -------------------------------------------

(defn- used-names
  "The names in the text that are not an alias, so not the word after AS."
  [toks]
  (into #{}
        (keep-indexed (fn [i tok]
                        (when (and (name-token? tok) (not (word? (get toks (dec i)) "as")))
                          (:text tok))))
        toks))

(defn- read-tables
  [toks]
  (keep (fn [i]
          (let [tok (get toks i)]
            (when (or (word? tok "from") (word? tok "join"))
              (let [names (take-while #(or (= "." (:text %)) (name-token? %)) (subvec toks (inc i) (min (count toks) (+ i 6))))]
                (:text (last (filter name-token? (take-while #(not (and (= :word (:kind %)) (contains? #{"where" "group" "order" "limit" "join" "on"} (:text %)))) names))))))))
        (range (count toks))))

(defn- check-one-path!
  "See [[check-one-path-of-select!]]. The query builder writes a NULL for a later column of such a group, so this holds for a name in its text."
  [cube-meta toks]
  (let [used  (used-names toks)
        index (into {} (map (fn [{cube-name :name, :keys [dimensions]}] [(str/lower-case cube-name) {:same-source (cube.meta/same-source-groups dimensions)
                                                                                                     :same-sql (cube.meta/same-sql-groups dimensions)}]))
                    (:cubes cube-meta))]
    (doseq [table (read-tables toks)
            group (:same-source (get index table))
            :let  [both (sort (filter used group))]
            :when (next both)]
      (refuse! (format (str "The columns %s of %s come from one cube through different join paths. Cube joins that cube "
                            "once in a query, so both columns would show the same value and the number would be wrong. "
                            "Use one of these columns in a query, or use a join alias in the data model.")
                       (str/join " and " both) table)))
    (when (some #(word? % "where") toks)
      (doseq [table (read-tables toks)
              group (:same-sql (get index table))
              :let  [both (sort (filter used group))]
              :when (next both)]
        (refuse! (format (str "The columns %s of %s have equal SQL. With a filter in the query, Cube reads the first subquery for "
                              "both columns, so one number would be wrong. Use one of these columns in a query, or give one "
                              "of them other SQL in the data model, such as {orders.count} + 0.")
                         (str/join " and " both) table))))))

(def ^:private not-an-alias
  #{"where" "group" "order" "having" "limit" "on" "join" "cross" "left" "right" "inner" "full" "outer" "union" "using" "offset" "select" "from" "as"})

(defn- part-of-name?
  [tok]
  (contains? #{:word :ident} (:kind tok)))

(defn- table-at
  "The last part of the name of a table at index `i`, and the index after it. `a.b.c` gives `c`."
  [toks i]
  (when (part-of-name? (get toks i))
    (loop [i (long i)]
      (if (and (= "." (:text (get toks (inc i)))) (part-of-name? (get toks (+ i 2))))
        (recur (+ i 2))
        [(:text (get toks i)) (inc i)]))))

(defn- table-names
  "A map from each table or alias after FROM or JOIN, and after a comma of a FROM list, to the lower case name of the table."
  [toks]
  (loop [i 0, out {}, in-from? false]
    (if (>= i (count toks))
      out
      (let [{:keys [text kind]} (get toks i)
            starts?             (or (and (= :word kind) (#{"from" "join"} text))
                                    (and in-from? (= "," text)))]
        (if-let [[table after] (when starts? (table-at toks (inc i)))]
          (let [as?          (and (= :word (:kind (get toks after))) (= "as" (:text (get toks after))))
                alias-at     (if as? (inc after) after)
                alias-tok    (get toks alias-at)
                alias-name   (when (and (part-of-name? alias-tok)
                                        (or as?
                                            (= :ident (:kind alias-tok))
                                            (not (not-an-alias (:text alias-tok)))))
                               (:text alias-tok))]
            (recur (long (if alias-name (inc alias-at) after))
                   (cond-> (assoc out table table) alias-name (assoc alias-name table))
                   true))
          (recur (inc i) out (cond
                               starts?                                          false
                               (and (= :word kind) (not-an-alias text))         false
                               (= "(" text)                                     false
                               :else                                            in-from?)))))))

(defn- star-of-select?
  "True if the `*` at `i` is a whole list of columns: after SELECT, DISTINCT, a comma or a dot."
  [toks i]
  (and (= "*" (:text (get toks i)))
       (let [before (get toks (dec i))]
         (or (= "." (:text before))
             (= "," (:text before))
             (and (= :word (:kind before)) (#{"select" "distinct" "all"} (:text before)))))))

(defn- tables-of-star
  "The tables that the `*` at `i` reads directly: those of the FROM of its own SELECT, with no subquery. A subquery lists its own columns, and the scan reads them there. `alias.*`
  reads the table of that alias."
  [toks tables i]
  (if (= "." (:text (get toks (dec i))))
    (keep tables [(:text (get toks (- i 2)))])
    (loop [j (inc i), depth 0, in-from? false, out []]
      (let [{:keys [kind text] :as tok} (get toks j)]
        (cond
          (or (nil? tok) (neg? depth)) out
          (= "(" text)                 (recur (inc j) (inc depth) in-from? out)
          (= ")" text)                 (recur (inc j) (dec depth) in-from? out)
          (pos? depth)                 (recur (inc j) depth in-from? out)
          (and (= :word kind) (#{"from" "join"} text)) (let [[table after] (table-at toks (inc j))]
                                                         (recur (or after (inc j)) depth true (cond-> out table (conj table))))
          (and in-from? (= "," text))  (let [[table after] (table-at toks (inc j))]
                                         (recur (or after (inc j)) depth true (cond-> out table (conj table))))
          (and (= :word kind) (#{"where" "group" "order" "having" "limit" "union"} text)) out
          :else                        (recur (inc j) depth in-from? out))))))

(defn check-no-private-member!
  "Cube in dev mode reads a member that it marks private, and a cube that it hides, so the driver does not send a name of one. This scans each query. MBQL always writes a
  table before a column. Native SQL can leave it out, so a name with no table counts for each table that the text reads. A name after a table that is not on the list, such as
  the alias of a subquery, reads a column that the subquery selected, and the scan sees that name there. docs/security.md says what this scan is not."
  [cube-meta sql]
  (let [private   (cube.meta/private-members cube-meta)
        hidden    (cube.meta/private-cubes cube-meta)]
    (when (or (seq private) (seq hidden))
      (let [toks      (tokens sql)
            tables    (table-names toks)
            read-from (set (vals tables))
            aliases   (into #{}
                            (keep (fn [i]
                                    (when (and (= :word (:kind (get toks i))) (= "as" (:text (get toks i))))
                                      (:text (get toks (inc i))))))
                            (range (count toks)))
            ;; The clause of the query at each token.
            clauses   (first (reduce (fn [[out clause] {:keys [kind text]}]
                                       (let [clause (if (and (= :word kind) (#{"select" "from" "where" "group" "order" "having"} text)) text clause)]
                                         [(conj out clause) clause]))
                                     [[] nil]
                                     toks))
            refuse-member! (fn [member table]
                             (refuse! (format (str "%s is a private member of %s in the Cube data model, so it has no values here. Use another column, or make it public in the "
                                                   "model. A Cube in dev mode reads private members, so do not run dev mode in production.")
                                              member table)))]
        (doseq [table read-from
                :when (hidden table)]
          (refuse! (format "%s is private in the Cube data model, so it has no values here. A Cube in dev mode reads private cubes, so do not run dev mode in production."
                           table)))
        (let [with-columns (cube.meta/private-members cube-meta :in-cube-only? true)]
          (doseq [i (range (count toks))
                  :when (star-of-select? toks i)
                  table (tables-of-star toks tables i)
                  :when (seq (get with-columns table))]
            (refuse! (format "%s has private members in the Cube data model, and a Cube in dev mode gives them for *. List the columns." table))))
        (doseq [i (range (count toks))
                :let [{:keys [kind text]} (get toks i)]
                :when (contains? #{:word :ident} kind)]
          (let [before    (get toks (dec i))
                ;; ORDER BY "count" reads the alias that the SELECT gave, not a member.
                alias?    (and (contains? aliases text)
                               (contains? #{"order" "group"} (get clauses i)))
                qualified (= "." (:text before))
                prefix    (when qualified (:text (get toks (- i 2))))
                as?       (and (= :word (:kind before)) (= "as" (:text before)))
                call?     (= "(" (:text (get toks (inc i))))
                dotted?   (= "." (:text (get toks (inc i))))]
            (cond
              (and qualified (get tables prefix))
              (when (contains? (get private (get tables prefix)) text)
                (refuse-member! text (get tables prefix)))

              (or qualified as? call? dotted? alias?)
              nil

              :else
              (when-let [table (first (filter #(contains? (get private %) text) read-from))]
                (refuse-member! text table)))))))))

(def ^:private longest-list
  "The longest list of IN that the driver sends. In a run on a stock Cube with 2 GiB, 2000 values took 12 s, 4000 stopped on the limit of the planner after 29 s, and 8000 after 105 s. See \"Long lists\" in docs/query-compilation.md."
  2000)

(defn- check-size!
  "A text that is too long for the planner of Cube stops here, because a long enough one restarts Cube for each person who reads it."
  [^String sql]
  (when (> (count sql) 4000)
    (let [toks       (tokens sql)
          conditions (count (filter #(or (word? % "or") (word? % "and")) toks))]
      (when (> conditions most-conditions)
        (refuse! (format (str "This query has %d conditions joined with AND or OR, and that is too long for the planner of Cube. It stops on about 50, and a longer text can restart it. Write one column and many values as IN, "
                              "use a range, or add a segment to the data model.")
                         conditions))))
    (let [tree (sqltext/parse sql)]
      (when-not (:error tree)
        (doseq [{:keys [items]} (filter #(= :in (:k %)) (sqltext/nodes tree))
                :when (> (count items) longest-list)]
          (refuse! (format "This query has a list of %d values, and that is too long for the planner of Cube. Cube works up to %d, and it can restart on a longer one. Use a shorter list, a range, or add a segment to the data model."
                           (count items) longest-list)))))))

(defn check-paths!
  "For each query that the query builder wrote."
  [cube-meta sql]
  (check-size! sql)
  (check-one-path! cube-meta (tokens sql)))
