(ns metabase.driver.cube.correct
  "Corrections of native SQL that `metabase.driver.cube.grammar` admits. The SQL API of Cube reads some ordinary SQL with another meaning, and it gives a number with no error. Each correction writes a form that Cube reads as SQL
  does. docs/query-compilation.md has the list, and `dev/fuzz/native_grammar.py` and `dev/review/release_cases.py` have the truth in ClickHouse. A text with no such construct stays the same, byte for byte, so it keeps its way
  through Cube and its rollup."
  (:require
   [clojure.string :as str]
   [metabase.driver.cube.sqltext :as sqltext])
  (:import
   (java.math BigDecimal MathContext)
   (java.time ZoneId ZoneOffset)))

(set! *warn-on-reflection* true)

(def ^:private ^:dynamic *sql* nil)
(def ^:private ^:dynamic *context* nil)

(def ^:private exact-factor
  "A column in an expression makes Cube write the number that it compares with as it is. Then the database compares two doubles. See `fractions` below."
  " * CAST('1.0' AS double precision)")

(defn- refuse!
  [text]
  (throw (ex-info text {:type :invalid-query})))

(defn- source
  [node]
  (subs *sql* (:s node) (:e node)))

(defn- emit
  [node]
  (sqltext/emit *sql* node))

(def ^:private number-types
  #{"numeric" "decimal" "double precision" "float" "float4" "float8" "real" "double" "money"})

(defn- unwrapped
  "The value inside parentheses and casts."
  [node]
  (case (:k node)
    :paren (recur (:x node))
    :cast  (recur (:x node))
    node))

(defn- unwrapped-number
  "The value inside parentheses, and inside a cast to a type of numbers."
  [node]
  (case (:k node)
    :paren (recur (:x node))
    :cast  (if (contains? number-types (:type node)) (recur (:x node)) node)
    node))

(defn- const-value
  "The value of a number that the text writes, as `44.001`, `CAST(44 AS numeric)`, `-44.5` or `44.0 + 0.001`, as a BigDecimal, or nil. A number in a text, as `'44.001'`, is that BigDecimal in a vector."
  [node]
  (let [node (unwrapped-number node)]
    (case (:k node)
      :num (sqltext/plain-number (:v node))
      :str (when-let [value (sqltext/plain-number (:v node))]
             [value])
      :neg (when-let [value (const-value (:x node))]
             (let [text? (vector? value)
                   ^BigDecimal number (if text? (first value) value)
                   number (if (= "-" (:op node)) (.negate number) number)]
               (if text? [number] number)))
      :bin (when (contains? #{"+" "-" "*"} (:op node))
             (let [l (const-value (:l node))
                   r (const-value (:r node))]
               (when (and (instance? BigDecimal l) (instance? BigDecimal r))
                 (case (:op node)
                   "+" (.add ^BigDecimal l ^BigDecimal r)
                   "-" (.subtract ^BigDecimal l ^BigDecimal r)
                   "*" (.multiply ^BigDecimal l ^BigDecimal r)))))
      :div (let [l (const-value (:l node))
                 r (const-value (:r node))]
             (when (and (instance? BigDecimal l) (instance? BigDecimal r) (not (zero? (.signum ^BigDecimal r))))
               (.divide ^BigDecimal l ^BigDecimal r MathContext/DECIMAL64)))
      nil)))

(defn- number-of
  "{:value BigDecimal, :text? true for a text} if this node is a number in the text, or nil. A parameter is a literal here."
  [node]
  (let [value (const-value node)]
    (cond
      (vector? value) {:value (first value), :text? true}
      value           {:value value})))

(defn- date-trunc-call?
  [node]
  (and (= :call (:k node)) (= "date_trunc" (:name node))))

(defn- fraction?
  "True if this number has a part after the point that is not zero."
  [{:keys [^BigDecimal value]}]
  (and value (pos? (.scale (.stripTrailingZeros value)))))

(defn- literal?
  "True if this node has no column."
  [node]
  (let [node (unwrapped node)]
    (or (contains? #{:num :str :param :bool :typed :interval :null} (:k node))
        (some? (const-value node)))))

;;; The columns.

(defn- aggregate-call?
  [node]
  (and (= :call (:k node))
       (contains? #{"sum" "avg" "min" "max" "count" "measure" "stddev" "stddev_pop" "stddev_samp" "variance" "var_pop" "var_samp"} (:name node))))

(defn- has-aggregate?
  "True if this expression has an aggregation. A subquery has its own."
  [node]
  (or (aggregate-call? node)
      (and (not (contains? #{:sub :query :exists} (:k node)))
           (some has-aggregate? (sqltext/kids node)))))

(defn- own-names
  "The last part of each column in this expression."
  [node]
  (cond
    (= :name (:k node))                          [(peek (:parts node))]
    (contains? #{:sub :query :exists} (:k node)) nil
    :else                                        (mapcat own-names (sqltext/kids node))))

(defn- numeric-node?
  "True if this expression gives a number: a number dimension, a measure, an aggregation or an alias of one of them."
  [node]
  (let [node (unwrapped node)
        {:keys [numbers measures numeric-aliases]} *context*]
    (case (:k node)
      :name (let [column (peek (:parts node))]
              (or (contains? numbers column) (contains? measures column) (contains? numeric-aliases column)))
      :call (or (contains? #{"sum" "avg" "count" "stddev" "stddev_pop" "stddev_samp" "variance" "var_pop" "var_samp"
                             "abs" "round" "floor" "ceil" "ceiling" "trunc" "sqrt" "power" "ln" "log" "exp" "sign" "measure"
                             ;; STRPOS(x, 'A') > 0 is a member filter `contains` for Cube, which ignores case: 2000 where the truth is 0.
                             "strpos" "length"} (:name node))
                (and (contains? #{"min" "max" "coalesce" "nullif" "greatest" "least"} (:name node))
                     (some numeric-node? (:args node))))
      (:div :neg) true
      :bin (contains? #{"+" "-" "*" "%"} (:op node))
      false)))

(defn- derived-aliases
  "The aliases of each select of the text whose value is a number, those that have an aggregation, and those of a time."
  [tree numbers measures times]
  (let [items (filter #(and (= :item (:k %)) (:alias %)) (sqltext/nodes tree))
        time? (fn [{:keys [x]}]
                (let [node (unwrapped x)]
                  (or (and (= :name (:k node)) (contains? times (peek (:parts node))))
                      (and (= :call (:k node)) (= "date_trunc" (:name node))))))]
    (binding [*context* {:numbers numbers, :measures measures, :numeric-aliases #{}}]
      {:numeric-aliases (into #{} (comp (filter #(numeric-node? (:x %))) (map :alias)) items)
       :aggregate-aliases (into #{} (comp (filter #(has-aggregate? (:x %))) (map :alias)) items)
       :time-aliases (into #{} (comp (filter time?) (map :alias)) items)})))

(defn- plain-column
  "The text of the column inside this node if the node has a column of rows, or nil. Cube reads a filter on such a column as a member filter. A measure and an alias of an aggregation are not such
  a column."
  [node]
  (let [node (unwrapped node)]
    (when (= :name (:k node))
      (let [column (peek (:parts node))
            {:keys [measures aggregate-aliases]} *context*]
        (when-not (or (contains? measures column) (contains? aggregate-aliases column))
          (source node))))))

;;; A cast of a column to a type of numbers in a comparison with a number. Cube writes CAST with no Nullable on ClickHouse, so it stops on a NULL: `Cannot convert NULL value to non-Nullable type`. The
;;; comparison has the same meaning with no cast.

(defn- without-number-cast
  [node]
  (let [inner (unwrapped-number node)]
    (if (and (= :name (:k inner)) (some #(= :cast (:k %)) (take-while some? (iterate :x node))))
      inner
      node)))

;;; Numbers with a fraction. Cube sends a number in a filter to ClickHouse as text, and ClickHouse cuts text to the scale of a Decimal column, and it stops on a fraction for an integer column. So
;;; `amount = 44.001` reads as `amount = 44`. A column in an expression makes Cube write the number as it is, so this is the form of each comparison with such a number.

(defn- with-factor
  "`node` as a value in an expression that compares two doubles."
  [node]
  (let [text (emit node)]
    (cond
      (str/includes? text exact-factor)                 node
      (contains? #{:name :call :paren :cast :case} (:k node)) (assoc node :text (str text exact-factor))
      :else                                             (assoc node :text (str "(" text ")" exact-factor)))))

(defn- at-place-of
  "`new` at the place of `old` in the text."
  [new old]
  (assoc new :s (:s old) :e (:e old) :text (emit new)))

(defn- written-number
  "The node of a number, in the form that SQL writes it."
  [node {:keys [^BigDecimal value]}]
  (assoc node :text (.toPlainString value)))

(defn- reads-number?
  "True if a comparison of `column` with this number compares numbers. A text compares numbers only with a column of numbers."
  [column {:keys [text?] :as number}]
  (and number
       (not (literal? column))
       (or (not text?) (numeric-node? column))))

(defn- boolean-as-number
  "`TRUE` or `FALSE` as a number. See the note on true and false."
  [node]
  (let [value (unwrapped node)]
    (if (= :bool (:k value))
      (assoc node :text (if (= "true" (:v value)) "1" "0"))
      node)))

;;; A function of a column next to a literal. Cube reads some of them as a member filter with another meaning, on a list that it does not publish: LEFT(x, n) = 'a' ignores case on ClickHouse, and STRPOS(x, 'a') > 0 too.
;;; A text with || '' and a number with the exact factor are expressions for each list, so the database compares them. A filter on a time keeps DATE_TRUNC, and an aggregation is a filter on a group.

(defn- function-of-column?
  [node]
  (let [node (unwrapped node)]
    (and (= :call (:k node))
         (not (date-trunc-call? node))
         (not (has-aggregate? node)))))

(defn- text-function?
  [node]
  (contains? #{"left" "right" "lower" "upper" "substr" "substring" "concat" "coalesce" "nullif"} (:name (unwrapped node))))

(defn- text-node?
  [node]
  (and (text-function? node)
       (not (numeric-node? node))))

(defn- with-database-text
  [node]
  (assoc node :text (str "(" (emit node) " || '')")))

(defn- in-database
  "`node`, a side of a comparison with a literal on the other side, in a form that the database compares."
  [node]
  (cond
    (not (function-of-column? node)) node
    (text-node? node)                (with-database-text node)
    (numeric-node? node)             (with-factor node)
    :else                            node))

;;; A time literal. Each form of it has one text here, TIMESTAMP 'x', so a date with no time is not the end of that day, as Cube reads it in a date range, and a text reads as a time in the database too.

(defn- time-node?
  [node]
  (let [node (unwrapped node)]
    (or (date-trunc-call? node)
        (and (= :name (:k node))
             (let [column (peek (:parts node))]
               (or (contains? (:times *context*) column)
                   (contains? (:time-aliases *context*) column)))))))

(defn- literal-time
  "The time of this literal, or nil."
  [node]
  (let [node (unwrapped node)]
    (case (:k node)
      :str   (sqltext/parse-time (:v node))
      :typed (sqltext/parse-time (:v node))
      :cast  (literal-time (:x node))
      nil)))

(defn- written-zone
  ^ZoneId []
  (or (:zone *context*) ZoneOffset/UTC))

(defn- time-text
  [parsed ^ZoneId zone]
  (format "TIMESTAMP '%s'" (sqltext/time-in-zone parsed (written-zone) zone)))

(defn- canonical-time
  "This literal as TIMESTAMP in the zone of the session. A date range of Cube reads it there."
  [node]
  (if-let [parsed (literal-time node)]
    (assoc node :text (time-text parsed (written-zone)), :time parsed)
    node))

(defn- fix-comparison
  [{:keys [l r op quantified] :as node}]
  (cond
    (or quantified (not (contains? #{"=" "<>" "!=" "<" "<=" ">" ">="} op)))
    node

    (and (time-node? l) (literal? r))
    (assoc node :r (canonical-time r))

    (and (time-node? r) (literal? l))
    (assoc node :l (canonical-time l))

    :else
    (let [l-number (number-of l)
          r-number (number-of r)
          l        (if (literal? r) (in-database l) l)
          r        (if (literal? l) (in-database r) r)
          node     (assoc node :l l :r r)]
      (cond
        (and (fraction? r-number) (reads-number? l r-number))
        (assoc node :l (at-place-of (with-factor (without-number-cast l)) l) :r (written-number r r-number))

        (and (fraction? l-number) (reads-number? r l-number))
        (assoc node :r (at-place-of (with-factor (without-number-cast r)) r) :l (written-number l l-number))

        (and r-number (reads-number? l r-number) (not= l (without-number-cast l)))
        (assoc node :l (at-place-of (without-number-cast l) l))

        (and l-number (reads-number? r l-number) (not= r (without-number-cast r)))
        (assoc node :r (at-place-of (without-number-cast r) r))

        :else node))))

(defn- fix-in
  [{:keys [x items] :as node}]
  (let [numbers (map number-of items)
        x       (in-database x)
        node    (assoc node :x x)]
    (if (and (seq items) (time-node? x))
      (assoc node :items (mapv canonical-time items))
      (if (and (seq items)
               (not (literal? x))
               (some some? numbers)
               (every? #(reads-number? x %) (filter some? numbers)))
        (let [plain (at-place-of (without-number-cast x) x)]
          (assoc node
                 :x (if (some fraction? numbers) (at-place-of (with-factor plain) x) plain)
               ;; A cast in a list is a type that ClickHouse does not take: `Type mismatch in IN or VALUES section`.
                 :items (mapv (fn [item number] (if number (written-number item number) item)) items numbers)))
        node))))

(defn- fix-between
  [{:keys [x lo hi] :as node}]
  (let [lo-number (number-of lo)
        hi-number (number-of hi)
        either    (filter some? [lo-number hi-number])
        node      (assoc node :x (in-database x))]
    (cond
      (time-node? x)
      (assoc node :lo (canonical-time lo) :hi (canonical-time hi))

      (and (not (literal? x))
           (some fraction? either)
           (every? #(reads-number? x %) either))
      (cond-> (assoc node :x (at-place-of (with-factor (without-number-cast x)) x))
        lo-number (assoc :lo (written-number lo lo-number))
        hi-number (assoc :hi (written-number hi hi-number)))

      :else node)))

;;; Division. Cube runs `/` in two places, with other meanings: above a CubeScan it divides integers with no fraction, and in the database `Decimal / x` keeps the scale of the Decimal, so
;;; 160.00 / 7 is 22.85. Here `/` is a real division on each path, as it is on ClickHouse. To cut the fraction, write FLOOR or TRUNC.

(defn- zero-literal?
  [node]
  (let [number (number-of node)]
    (and number (zero? (.signum ^BigDecimal (:value number))))))

(defn- has-null-if?
  [node]
  (let [node (unwrapped node)]
    (and (= :call (:k node)) (= "nullif" (:name node)))))

(defn- fix-division
  "A divisor that is not a number in the text gets NULLIF, as Metabase writes `/`: a double divided by 0 is infinity, and a filter can read it as a number. A row with 0 in the divisor gives NULL."
  [{:keys [l r] :as node}]
  (when (zero-literal? r)
    (refuse! (format "%s divides by zero. Fix the divisor. A divisor with NULLIF(x, 0) gives NULL on such a row." (source node))))
  (if (or (contains? #{:interval :typed} (:k (unwrapped l)))
          (str/includes? (emit node) exact-factor))
    node
    ;; One factor. With a second one in the divisor, Cube reads two columns with one name on raw rows, and it stops with `Schema contains duplicate unqualified field name`.
    (let [divisor (cond
                    (or (number-of r) (has-null-if? r)) r
                    :else (assoc r :text (format "NULLIF(%s, 0)" (emit r))))]
      (assoc node
             :l (assoc l :text (if (contains? #{:name :call :paren :cast :num} (:k l))
                                 (str (emit l) exact-factor)
                                 (str "(" (emit l) ")" exact-factor)))
             :r divisor))))

;;; LIKE. Cube reads `x LIKE '%a%'` as an operator that ignores case on ClickHouse. A column in an expression keeps the SQL of the text, so the database compares it with its case.

(defn- pattern-text
  "The text of a pattern: a string, or strings joined with ||."
  [node]
  (let [node (unwrapped node)]
    (case (:k node)
      :str (:v node)
      :bin (str (pattern-text (:l node)) (pattern-text (:r node)))
      "")))

(defn- fix-like
  "Each LIKE and ILIKE reads in the database, because Cube reads `_` of a pattern as a character, and a pattern with % as an operator with its own case."
  [{:keys [x p] :as node}]
  (assoc node
         :x (with-database-text x)
         :p (assoc p :text (str "'" (str/replace (pattern-text p) "'" "''") "'"))))

;;; FILTER. Cube drops the FILTER of an aggregation, so COUNT(*) FILTER (WHERE p) reads as COUNT(*). CASE stays in the SQL, so this is the same aggregation with CASE. FILTER keeps
;;; the rows where p is true, as WHERE does, so p gets the corrections of WHERE: CASE WHEN also keeps only true. These forms are exact inside CASE too, and Cube can plan them,
;;; where it has no plan for NOT over LIKE in CASE. The grammar refuses NOT, <> and the other forms of NULL in CASE and in a value. See native.not_like_in_select.

(declare positive)

(def ^:private filter-aggregations
  #{"count" "sum" "avg" "min" "max" "stddev" "stddev_pop" "stddev_samp" "variance" "var_pop" "var_samp"})

(defn- measure-argument
  [{fn-name :name, :keys [args]}]
  (cond
    (= "measure" fn-name) (or (first (mapcat own-names args)) fn-name)
    :else (first (filter (:measures *context*) (mapcat own-names args)))))

(defn- fix-filter
  [{fn-name :name, :keys [args distinct? filter-end e] :as node}]
  (let [function (str/upper-case fn-name)]
    (when-let [measure (measure-argument node)]
      (refuse! (format (str "%s of the measure %s has FILTER. Cube drops this FILTER, so the number would be of all rows, and it cannot add it to a measure. Use WHERE, a measure with this filter in "
                            "the data model, or an aggregation of a dimension with CASE.")
                       function measure)))
    (when-not (and (contains? filter-aggregations fn-name) (= 1 (count args)))
      (refuse! (format "%s with FILTER is not a form that the driver can write for Cube, and Cube drops this FILTER, so the number would be of all rows. Use WHERE, or CASE inside the aggregation."
                       function)))
    (let [arg      (first args)
          value    (if (= :star (:k arg)) "1" (emit arg))
          where    (emit (positive (:filter node)))
          over     (subs *sql* filter-end e)]
      (assoc node :text (format "%s(%sCASE WHEN %s THEN %s END)%s" function (if distinct? "DISTINCT " "") where value over)))))

;;; Other aggregations.

(defn- check-distinct-aggregation!
  [{fn-name :name, :keys [distinct?] :as node}]
  (when (and distinct? (contains? #{"sum" "stddev" "stddev_pop" "stddev_samp" "variance" "var_pop" "var_samp"} fn-name))
    (refuse! (format "Cube drops DISTINCT in %s, so the number would be of all rows. Read the distinct values in a subquery and add them up there: SELECT SUM(x) FROM (SELECT x FROM t GROUP BY 1 LIMIT 2147483647) s."
                     (source node)))))

;;; NULL. Cube reads `x <> v`, `x NOT IN (...)`, `NOT (x = v)` and `x NOT LIKE p` as its operators, and each of them keeps the rows with NULL, where SQL has no such row. In WHERE and HAVING only true
;;; counts, so NOT moves down to the comparisons, and each comparison says IS NOT NULL. FILTER gets the same text. These corrections hold only at the top of WHERE, HAVING and FILTER,
;;; joined with AND, OR and NOT, so the grammar refuses these forms in each other place. See check-top-null-logic! in metabase.driver.cube.grammar.

(declare is-distinct-text fold-chain is-text boolean-column?)

(defn- null-free
  "`text` with IS NOT NULL on the column of `column-node`, or `text` if it has no plain column."
  [text column-node]
  (if-let [column (plain-column column-node)]
    (format "(%s AND %s IS NOT NULL)" text column)
    text))

(def ^:private ^:dynamic *range-bounds*
  "The places of the bounds of WHERE that stay a date range of Cube."
  #{})

(defn- guarded?
  "True if Cube must not make a date range from this bound, because it would read it in another way. See \"Date bounds\" in docs/query-compilation.md."
  [node column op]
  (let [{:keys [zone keeps-fractions? range-columns]} *context*
        range? (contains? *range-bounds* (:s node))]
    (cond
      zone               (not range?)
      keeps-fractions?   false
      (= ">" op)         true
      ;; `x <= t` stays as it is: Cube reads it exactly. In the guarded form, stock Cube compares a DateTime64 at whole seconds, so it keeps the rows after t in that second.
      (= "<" op)         (not (contains? range-columns column))
      :else              false)))

(defn- bound-text
  "`column op time` in a form that Cube reads as SQL does."
  [node column-node op parsed]
  (let [column (source column-node)
        short  (peek (:parts (unwrapped column-node)))
        guard? (guarded? node short op)
        zone   (if (and guard? (:zone *context*)) ZoneOffset/UTC (written-zone))
        text   (time-text parsed zone)]
    (if guard?
      (format "((%s %s %s OR %s IS NULL) AND %s IS NOT NULL)" column op text column column)
      (format "%s %s %s" column op text))))

(defn- time-comparison
  "A comparison of a time column with a literal, as [column op time], with the column first. Nil for another form."
  [{:keys [l r op quantified]}]
  (when-not quantified
    (let [plain? (fn [node] (let [node (unwrapped node)] (and (= :name (:k node)) (time-node? node))))
          flip   {"<" ">", "<=" ">=", ">" "<", ">=" "<=", "=" "=", "<>" "<>", "!=" "!="}]
      (cond
        (and (plain? l) (literal-time r)) [l op (literal-time r)]
        (and (plain? r) (literal-time l)) [r (flip op) (literal-time l)]))))

(defn- time-bound-text
  "The rows where this comparison on a time is true. `=` is two bounds, because next to DATE_TRUNC on the same column Cube reads it as the whole bucket. `<>` has no NULL."
  [node [column op parsed]]
  (case op
    "="  (format "(%s AND %s)" (bound-text node column ">=" parsed) (bound-text node column "<=" parsed))
    ("<>" "!=") (let [text (time-text parsed (if (:zone *context*) ZoneOffset/UTC (written-zone)))]
                  (format "(%s <> %s AND %s IS NOT NULL)" (source column) text (source column)))
    (bound-text node column op parsed)))

(defn- opposite-of-comparison
  [op]
  (get {"=" "<>", "<>" "=", "!=" "=", "<" ">=", "<=" ">", ">" "<=", ">=" "<"} op))

(defn- comparison-text
  "A comparison in WHERE or HAVING, where Cube reads a member filter. TRUE and FALSE on a boolean column are 1 and 0 there. See the note on true and false."
  [{:keys [l r]} op]
  (let [l    (boolean-as-number l)
        r    (boolean-as-number r)
        text (format "%s %s %s" (emit l) op (emit r))]
    (if (contains? #{"<>" "!="} op)
      (cond
        (= :null (:k (unwrapped r))) text
        (= :null (:k (unwrapped l))) text
        (literal? r)                 (null-free text l)
        (literal? l)                 (null-free text r)
        :else                        text)
      text)))

(defn- list-text
  [{:keys [items q]}]
  (if q
    (format "(%s)" (emit q))
    (format "(%s)" (str/join ", " (map emit items)))))

(defn- date-trunc?
  [node]
  (let [node (unwrapped node)]
    (and (= :call (:k node)) (= "date_trunc" (:name node)))))

(defn- not-in-text
  "The rows where `x NOT IN (...)` is true. A NULL in the list means that no row is. Cube reads DATE_TRUNC(u, x) NOT IN (a, b) as notInDateRange and joins the ranges with OR, so two buckets apart
  exclude no row: 5000 where the truth is 4959. Each `<>` is right."
  [{:keys [x items] :as node}]
  (cond
    (some #(= :null (:k (unwrapped %))) items)
    (if-let [column (plain-column x)]
      (format "(%s IS NULL AND %s IS NOT NULL)" column column)
      "1 = 0")

    (and (date-trunc? x) (next items))
    (format "(%s)" (str/join " AND " (map #(format "%s <> %s" (emit x) (emit %)) items)))

    :else
    (let [text (format "%s NOT IN %s" (emit x) (list-text node))]
      (if (:q node) text (null-free text x)))))

(defn- like-text
  [{:keys [x p op esc]} negated]
  (let [text (format "%s %s%s %s%s" (emit x) (if negated "NOT " "") (str/upper-case op) (emit p)
                     (if esc (str " ESCAPE " (emit esc)) ""))]
    (if negated (null-free text x) text)))

(defn- plain-time?
  [node]
  (let [node (unwrapped node)]
    (and (= :name (:k node)) (time-node? node))))

(defn- between-text
  "BETWEEN on a time has two bounds, because Cube reads the end of a date range with no time as the end of that day. The literals here have a time, and the column keeps its form."
  [{:keys [x lo hi] :as node} negated]
  (if (and (plain-time? x) (literal-time lo) (literal-time hi))
    (if negated
      (format "(%s OR %s)" (bound-text {:s -1} x "<" (literal-time lo)) (bound-text {:s -1} x ">" (literal-time hi)))
      (format "(%s AND %s)" (bound-text node x ">=" (literal-time lo)) (bound-text (update node :s inc) x "<=" (literal-time hi))))
    (format "%s %sBETWEEN %s AND %s" (emit x) (if negated "NOT " "") (emit lo) (emit hi))))

(defn- negative
  "The text of the rows where `node` is false, with no NOT over a comparison."
  [node]
  (case (:k node)
    :and (format "(%s OR %s)" (negative (:l node)) (negative (:r node)))
    :or  (format "(%s AND %s)" (negative (:l node)) (negative (:r node)))
    :not (emit (positive (:x node)))
    :paren (negative (:x node))
    :cmp (if-let [op (when-not (:quantified node) (opposite-of-comparison (:op node)))]
           (if-let [[column own-op parsed] (time-comparison node)]
             (if (= "=" own-op)
               (format "(%s OR %s)" (bound-text {:s -1} column "<" parsed) (bound-text {:s -1} column ">" parsed))
               (time-bound-text {:s -1} [column (opposite-of-comparison own-op) parsed]))
             (comparison-text node op))
           (format "NOT (%s)" (emit node)))
    :in  (if (:not? node)
           (format "%s IN %s" (emit (:x node)) (list-text node))
           (not-in-text node))
    :between (between-text node (not (:not? node)))
    :like (like-text node (not (:not? node)))
    :is  (or (is-text node (not (:not? node)) true)
             (format "%s IS %s%s" (emit (:x node)) (if (:not? node) "" "NOT ") (str/upper-case (:what node))))
    :name (if (boolean-column? node)
            (format "%s = 0" (source node))
            (format "NOT %s" (emit node)))
    :is-distinct (is-distinct-text node (not (:not? node)))
    :exists (format "NOT %s" (emit node))
    (format "NOT %s" (emit (if (contains? #{:name :call} (:k node)) node {:k :paren, :s (:s node), :e (:e node), :text (str "(" (emit node) ")")})))))

(defn- positive
  "`node` with the rows where it is true, as Cube reads it."
  [node]
  (case (:k node)
    (:and :or) (if-let [text (if (= :or (:k node))
                               (fold-chain node :or #{"="})
                               (fold-chain node :and #{"<>" "!="}))]
                 (assoc node :text text)
                 (assoc node :l (positive (:l node)) :r (positive (:r node))))
    :paren     (assoc node :x (positive (:x node)))
    :not       (assoc node :text (format "(%s)" (negative (:x node))))
    :between   (if (plain-time? (:x node))
                 (assoc node :text (between-text node (:not? node)))
                 (if (:not? node)
                   (assoc node :text (between-text node true))
                   node))
    :cmp       (if-let [found (time-comparison node)]
                 (assoc node :text (time-bound-text node found))
                 (if (not (:quantified node))
                   (let [text (comparison-text node (:op node))]
                     (if (= text (format "%s %s %s" (emit (:l node)) (:op node) (emit (:r node))))
                       node
                       (assoc node :text text)))
                   node))
    :in        (if (:not? node)
                 (let [text (not-in-text node)]
                   (assoc node :text text))
                 node)
    :like      (if (:not? node)
                 (assoc node :text (like-text node true))
                 node)
    :name      (if (boolean-column? node)
                 (assoc node :text (format "%s = 1" (source node)))
                 node)
    :is        (if-let [text (is-text node (:not? node) true)] (assoc node :text text) node)
    node))

;;; True and false. In the database, Cube writes `x IS NOT TRUE` with no NULL: 429 where the truth is 571. And it sends `x = TRUE` or NOT x as the text 'true' to a boolean dimension with its own SQL, and ClickHouse stops
;;; on it. `x = 1`, `x = 0` and IS NULL are right on each path.

(defn- boolean-column?
  [node]
  (let [node (unwrapped node)]
    (and (= :name (:k node)) (contains? (:booleans *context*) (peek (:parts node))))))

(defn- is-text
  "The text of the rows where `x IS [NOT] TRUE` or `FALSE` is true, or nil for another form. `filter?` is true in WHERE and HAVING, where Cube reads a member filter and needs 1 and 0. In other places
  it plans the comparison itself, and it needs a real boolean."
  [{:keys [x what]} negated filter?]
  (when-let [column (when (contains? #{"true" "false"} what) (plain-column x))]
    (let [word (fn [truth] (if filter? (if truth "1" "0") (if truth "TRUE" "FALSE")))
          value (= "true" what)]
      (if negated
        (format "(%s = %s OR %s IS NULL)" column (word (not value)) column)
        ;; IS has two values, also under a NOT that this text does not see, such as inside CASE. So NULL gives false here, and not NULL.
        (format "(%s = %s AND %s IS NOT NULL)" column (word value) column)))))

;;; IS DISTINCT FROM. Cube writes `a IS DISTINCT FROM b AND c` with no parentheses, as `a IS DISTINCT FROM (b AND c)`. With a literal, plain comparisons have the same two values.

(defn- is-distinct-text
  [{:keys [x r]} negated]
  (if (and (literal? r) (not= :null (:k (unwrapped r))))
    (if negated
      (format "(%s = %s AND %s IS NOT NULL)" (emit x) (emit r) (emit x))
      (format "(%s <> %s OR %s IS NULL)" (emit x) (emit r) (emit x)))
    (format "%s IS %sDISTINCT FROM %s" (emit x) (if negated "NOT " "") (emit r))))

;;; A list of OR. A list of `x = a OR x = b` stops the planner of Cube at about 50 values, and IN goes on.

(def ^:private shortest-fold 8)

(defn- leaves
  "The parts of a chain of `op`, with no parentheses."
  [op node]
  (cond
    (= :paren (:k node)) (recur op (:x node))
    (and (= op (:k node)) (not (:text node))) (concat (leaves op (:l node)) (leaves op (:r node)))
    :else [node]))

(defn- fold-chain
  "`node`, a chain of OR or AND, as IN or NOT IN if each part compares one column with a literal, or nil."
  [node op comparison]
  (let [parts (leaves op node)]
    (when (>= (count parts) shortest-fold)
      (let [column-of (fn [{:keys [k l r] :as part}]
                        (when (and (= :cmp k) (contains? comparison (:op part)) (not (:quantified part)) (literal? r)
                                   (not= :null (:k (unwrapped r))) (plain-column l))
                          (emit l)))
            columns   (map column-of parts)]
        (when (and (every? some? columns) (apply = columns))
          (let [values (str/join ", " (map #(emit (:r %)) parts))
                column (first parts)]
            (if (= :or op)
              (format "%s IN (%s)" (first columns) values)
              (format "(%s NOT IN (%s) AND %s IS NOT NULL)" (first columns) values (plain-column (:l column))))))))))

;;; COUNT on no rows. Cube reads a rollup with SUM, so a count over no rows is NULL where SQL has 0.

(declare with-zero-count)

(defn- count-call?
  [{fn-name :name, :keys [over? args] :as node}]
  (and (= :call (:k node))
       (not over?)
       (or (= "count" fn-name)
           (and (= "measure" fn-name)
                (contains? (:count-measures *context*) (first (mapcat own-names args)))))))

(defn- update-parts
  [f node]
  (reduce-kv (fn [out key value]
               (cond
                 (sqltext/node? value) (assoc out key (f value))
                 (and (vector? value) (some sqltext/node? value)) (assoc out key (mapv #(if (sqltext/node? %) (f %) %) value))
                 :else out))
             node
             node))

(defn- with-zero-count
  "`node` with COALESCE around each count. A subquery has its own select. COALESCE of a count stays as it is."
  [node]
  (cond
    (contains? #{:sub :query :exists} (:k node)) node
    (and (= :call (:k node)) (= "coalesce" (:name node))) node
    (count-call? node) (assoc node :text (format "COALESCE(%s, 0)" (emit node)))
    (:text node) node
    :else (update-parts with-zero-count node)))

;;; A select.

(defn- check-group!
  [{:keys [group]}]
  (when-let [form (:form (first (filter #(= :group-set (:k %)) group)))]
    (refuse! (format "Cube does not compute GROUP BY %s: it drops the rows of the totals with no error, or it cannot plan the query. Write the groups and each total as separate SELECT statements with UNION ALL."
                     form))))

(defn- from-sources
  "The tables and subqueries of a FROM."
  [from-items]
  (mapcat (fn [item]
            (case (:k item)
              :join (concat (from-sources [(:l item)]) (from-sources [(:r item)]))
              :from-paren (from-sources (:items item))
              [item]))
          from-items))

(defn- joins-of
  "The joins of a FROM, with no join inside a subquery."
  [from-items]
  (mapcat (fn [item]
            (case (:k item)
              :join (cons item (joins-of [(:l item) (:r item)]))
              :from-paren (joins-of (:items item))
              nil))
          from-items))

(defn- cross-joins
  "The number of sources that a comma or CROSS JOIN adds."
  [from-items]
  (+ (dec (count from-items))
     (count (filter #(= :cross (:type %)) (joins-of from-items)))))

(defn- model-join-conditions
  "The number of conditions in `where` that join two sources through the data model of Cube."
  [where]
  (count (filter (fn [{:keys [k l r op]}]
                   (and (= :cmp k) (= "=" op)
                        (= "__cubejoinfield" (peek (:parts l)))
                        (= "__cubejoinfield" (peek (:parts r)))))
                 (when where (tree-seq #(not (contains? #{:sub :query :exists} (:k %))) sqltext/kids where)))))

(defn- reads-measure?
  "True if this select has MEASURE(), so its text reads Cube and not plain SQL."
  [{:keys [items]}]
  (some #(and (= :call (:k %)) (= "measure" (:name %))) (mapcat sqltext/nodes items)))

(defn- check-from!
  "Cube ignores a cross product: `orders CROSS JOIN users` gives the count of orders, and two aggregated subqueries read as one query through the data model. MEASURE() of two cubes with CROSS JOIN is the form of
  the docs of Cube for a join of the data model, so it runs. MEASURE() is not SQL, so the text cannot mean a cross product."
  [{:keys [from where] :as select}]
  (let [sources (from-sources from)
        tables  (keep :table sources)]
    (when (and (next sources) (not-every? #(= :from-fn (:k %)) (rest sources)))
      (when (and (pos? (- (cross-joins from) (model-join-conditions where)))
                 (not (and (reads-measure? select)
                           (= (count tables) (count sources))
                           (apply distinct? tables))))
        (refuse! (str "This query has a cross product: a comma in FROM or a CROSS JOIN with no join condition. Cube ignores a cross product, and it reads the sources through the joins of the data model, "
                      "so the number would be wrong. Write a.__cubeJoinField = b.__cubeJoinField in WHERE for a join of the data model, or read each source in its own query.")))
      (when (and (next tables) (not (apply distinct? tables))
                 (pos? (+ (model-join-conditions where)
                          (reduce + (map #(model-join-conditions (:on %)) (joins-of from))))))
        (refuse! (format "This query joins %s to itself through __cubeJoinField. Cube joins a cube once in a query, so it reads one row for both sides, and the number would be wrong."
                         (first (filter #(> (count (filter #{%} tables)) 1) tables))))))))

;;; A subquery of raw rows over a join. Cube reads the outer aggregation and the subquery as one query, so a dimension next to a join reads each row of its cube once: SUM(id) over
;;; (SELECT u.id FROM users u CROSS JOIN orders o ...) gave 20100 where the truth is 498050. A limit makes the subquery a set of rows.

(def ^:private subquery-limit 2147483647)

(defn joined-rows?
  "True if this query reads raw rows through a join or a view."
  [{:keys [terms ops limit]} views]
  (let [{:keys [from group distinct? items] :as select} (first terms)]
    (and (= 1 (count terms))
         (empty? ops)
         (nil? limit)
         (= :select (:k select))
         (nil? group)
         (not distinct?)
         (not-any? #(has-aggregate? (:x %)) items)
         (let [sources (from-sources from)]
           (or (next sources)
               (contains? views (:table (first sources))))))))

(defn- aggregated-rows?
  "True if this query has an aggregation and no limit. Cube pushes a filter of an outer query below such an aggregation, and a limit stops this."
  [{:keys [terms ops limit]}]
  (let [{:keys [group items distinct?] :as select} (first terms)]
    (and (= 1 (count terms))
         (empty? ops)
         (nil? limit)
         (= :select (:k select))
         (or group distinct? (some #(has-aggregate? (:x %)) items)))))

(defn- with-limit
  [{:keys [q] :as node}]
  (if (or (joined-rows? q (:views *context*)) (aggregated-rows? q))
    (assoc node :q (assoc q :text (format "%s LIMIT %d" (emit q) subquery-limit)))
    node))

(defn- top-conditions
  "The conditions of a chain of AND at the top of a WHERE."
  [node]
  (case (:k node)
    :and   (concat (top-conditions (:l node)) (top-conditions (:r node)))
    :paren (top-conditions (:x node))
    [node]))

(defn- date-range-bounds
  "The places of the bounds that make a date range of Cube, each pair at the top of a WHERE: a lower and an upper bound on a column, and no other condition on that column. These stay in the zone of the session. See \"Date bounds\" in docs/query-compilation.md."
  [where]
  (let [{:keys [zone keeps-fractions?]} *context*
        bounds (keep (fn [{:keys [k] :as condition}]
                       (case k
                         :cmp     (when-let [[column op _] (time-comparison condition)]
                                    {:column (peek (:parts (unwrapped column))), :ops [op], :places [(:s condition)]})
                         :between (when (and (not (:not? condition)) (plain-time? (:x condition)) (literal-time (:lo condition)) (literal-time (:hi condition)))
                                    {:column (peek (:parts (unwrapped (:x condition)))), :ops [">=" "<="], :places [(:s condition) (inc (long (:s condition)))]})
                         nil))
                     (top-conditions where))]
    (into #{}
          (mapcat (fn [[_ of-column]]
                    (let [ops (mapcat :ops of-column)]
                      (when (and zone
                                 (= 2 (count ops))
                                 (some #{">=" ">"} ops)
                                 (some #{"<=" "<"} ops)
                                 (or keeps-fractions? (not (some #{">"} ops))))
                        (mapcat :places of-column)))))
          (group-by :column bounds))))

(defn- fix-select
  [{:keys [group having] :as node}]
  (check-group! node)
  (check-from! node)
  (cond-> (update node :from (fn [from]
                               (some->> from (mapv (fn limit-in [item]
                                                     (case (:k item)
                                                       :from-sub (with-limit item)
                                                       :join (assoc item :l (limit-in (:l item)) :r (limit-in (:r item)))
                                                       item))))))
    (:where node)  (update :where (fn [where]
                                    (binding [*range-bounds* (date-range-bounds where)]
                                      (positive where))))
    having         (update :having positive)
    (and (nil? group) (nil? having) (not-any? :over? (mapcat sqltext/nodes (:items node)))) (update :items #(mapv with-zero-count %))))

;;; A query.

(defn- check-order!
  [{:keys [order]}]
  (when-let [item (first (filter #(= :first (:nulls %)) order))]
    (refuse! (format "Cube drops NULLS FIRST, so `%s` would give NULL last, and with LIMIT other rows. Remove NULLS FIRST, or filter with IS NULL in a separate query." (source item)))))

(defn- named-items
  "Cube gives NULL for a column of a UNION when its branches have two aggregations with one name and no alias, as `SELECT COUNT(*) FROM a UNION ALL SELECT COUNT(*) FROM a`. So the first branch names its columns."
  [{:keys [items] :as select}]
  (assoc select :items
         (vec (map-indexed (fn [i {:keys [x alias] :as item}]
                             (if (and (nil? alias) (= :call (:k (unwrapped x))))
                               (assoc item :text (format "%s AS \"%s_%d\"" (emit item) (:name (unwrapped x)) (inc i)))
                               item))
                           items))))

(defn- fix-query
  [{:keys [ops terms] :as node}]
  (check-order! node)
  (let [node (cond-> node
               (:withs node) (update :withs #(mapv with-limit %)))]
    (if (and (seq ops) (= :select (:k (first terms))))
      (assoc node :terms (assoc terms 0 (named-items (first terms))))
      node)))

(defn- fix-node
  [node]
  (case (:k node)
    :cmp     (fix-comparison node)
    :in      (fix-in node)
    :between (fix-between node)
    :div     (fix-division node)
    :like    (fix-like node)
    :is-distinct (assoc node :text (is-distinct-text node (:not? node)))
    :is      (if-let [text (is-text node (:not? node) false)] (assoc node :text text) node)
    ;; A cast of a column to numeric or decimal has the same value, and Cube writes it with no Nullable and with a scale that ClickHouse can stop on.
    :cast    (let [number (when (and (contains? number-types (:type node)) (literal? (:x node))) (number-of node))]
               (cond
                 number (written-number node number)
                 (and (contains? #{"numeric" "decimal"} (:type node)) (not (literal? (:x node)))) (assoc node :text (emit (:x node)))
                 :else node))
    :call    (do (check-distinct-aggregation! node)
                 (if (contains? node :filter) (fix-filter node) node))
    :select  (fix-select node)
    :query   (fix-query node)
    node))

(defn correct
  "`sql` with each correction. The grammar has read this text. `context` has :numbers, :booleans, :times, :measures, :count-measures and :views, each a set of lower case names of the cubes that the text reads, :zone the
  report time zone if it is not UTC, :keeps-fractions? and :range-columns."
  [^String sql {:keys [numbers measures times] :as context}]
  (let [tree (sqltext/parse sql)]
    (when-let [reason (:error tree)]
      (refuse! (format "The driver cannot read this SQL: %s." reason)))
    (binding [*sql*     sql
              *context* (merge context (derived-aliases tree numbers measures times))]
      (let [fixed (sqltext/walk fix-node tree)
            text  (emit fixed)]
        (str (subs sql 0 (:s tree)) text (subs sql (:e tree)))))))
