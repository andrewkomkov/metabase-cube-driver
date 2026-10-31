(ns metabase.driver.cube.native
  "A cheap scan of SQL text, for shapes where Cube gives a wrong number with no error. It splits the text into tokens, removes
  parentheses and casts around a column or a literal, and reads `column op literal` on either side, BETWEEN and DATE_TRUNC.
  It is not a SQL parser. docs/query-compilation.md lists the shapes that it does not read in \"Known wrong numbers\"."
  (:require
   [clojure.string :as str]
   [metabase.driver.cube.meta :as cube.meta])
  (:import
   (java.time DayOfWeek LocalDate LocalDateTime OffsetDateTime ZoneOffset ZonedDateTime)
   (java.time.temporal ChronoUnit TemporalAdjusters)))

(set! *warn-on-reflection* true)

(def ^:private token-pattern
  #"(?s)--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[A-Za-z_][A-Za-z0-9_$]*|\d[\d.]*|<=|>=|<>|!=|\S")

(defn- token
  [text]
  (let [c (first text)]
    (cond
      (= c \') {:kind :string, :text (str/replace (subs text 1 (dec (count text))) "''" "'")}
      (= c \") {:kind :ident, :text (str/lower-case (str/replace (subs text 1 (dec (count text))) "\"\"" "\""))}
      (or (Character/isLetter (char c)) (= c \_)) {:kind :word, :text (str/lower-case text)}
      :else {:kind :other, :text text})))

(defn- comment-text?
  [^String text]
  (or (str/starts-with? text "--") (str/starts-with? text "/*")))

(defn- tokens
  "The tokens of `sql` as a vector, with no comments. Each `?` has the number of its query parameter."
  [sql]
  (first
   (reduce (fn [[out params] text]
             (if (comment-text? text)
               [out params]
               (if (= text "?")
                 [(conj out {:kind :param, :text "?", :param params}) (inc params)]
                 [(conj out (token text)) params])))
           [[] 0]
           (re-seq token-pattern sql))))

;;; Parentheses and casts around one value do not change it. The scan reads the value.

(defn- name-before-paren?
  "True if the parenthesis at `i` opens the arguments of a function, or the list of IN."
  [toks i]
  (let [before (get toks (dec i))]
    (and (contains? #{:word :ident} (:kind before))
         (not (contains? #{"where" "and" "or" "not" "on" "having" "when" "then" "else" "between" "select"} (:text before))))))

(defn- value-end
  "The index after the column or literal at `i`, or nil. A column can have a table before it."
  [toks i]
  (let [tok (get toks i)]
    (cond
      (= :string (:kind tok)) (inc i)
      (= :param (:kind tok))  (inc i)
      (and (= :word (:kind tok)) (#{"timestamp" "timestamptz" "date"} (:text tok)) (= :string (:kind (get toks (inc i))))) (+ i 2)
      (contains? #{:word :ident} (:kind tok)) (loop [i i]
                                                (if (and (= "." (:text (get toks (inc i))))
                                                         (contains? #{:word :ident} (:kind (get toks (+ i 2)))))
                                                  (recur (+ i 2))
                                                  (inc i))))))

(def ^:private not-a-value
  "Words that can stand after an opening parenthesis, so the parentheses are not around one value."
  #{"select" "distinct" "case" "not" "null" "true" "false"})

(defn- without-wrapper
  "`toks` with one wrapper around a value at index `i` removed, or nil if none starts there."
  [toks i]
  (let [tok (get toks i)
        sub (fn [from to] (subvec toks from to))]
    (cond
      ;; ( value )
      (and (= "(" (:text tok)) (= :other (:kind tok)) (not (contains? not-a-value (:text (get toks (inc i))))))
      (when-let [end (value-end toks (inc i))]
        (when (= ")" (:text (get toks end)))
          (when-not (name-before-paren? toks i)
            (vec (concat (sub 0 i) (sub (inc i) end) (sub (inc end) (count toks)))))))

      ;; CAST ( value AS type )
      (and (= :word (:kind tok)) (= "cast" (:text tok)) (= "(" (:text (get toks (inc i)))))
      (when-let [end (value-end toks (+ i 2))]
        (when (and (= :word (:kind (get toks end))) (= "as" (:text (get toks end))))
          (when-let [close (first (filter #(= ")" (:text (get toks %))) (range (inc end) (min (+ end 5) (count toks)))))]
            (vec (concat (sub 0 i) (sub (+ i 2) end) (sub (inc close) (count toks)))))))

      ;; value :: type
      (and (= ":" (:text tok)) (= ":" (:text (get toks (inc i)))) (contains? #{:word :ident} (:kind (get toks (+ i 2)))))
      (vec (concat (sub 0 i) (sub (+ i 3) (count toks)))))))

(defn- unwrap
  "`toks` with no parentheses or casts around one value."
  [toks]
  (loop [toks toks, i 0]
    (if (>= i (count toks))
      toks
      (if-let [shorter (without-wrapper toks i)]
        (recur shorter (max 0 (- i 2)))
        (recur toks (inc i))))))

(def ^:private keywords
  "Words that can follow FROM, so they are not a table."
  #{"select" "lateral" "unnest"})

(defn- word?
  [tok text]
  (and (= :word (:kind tok)) (= text (:text tok))))

(defn- name-token?
  [tok]
  (contains? #{:word :ident} (:kind tok)))

(defn- read-table
  "Reads `a.b.c [AS] alias` at index `i`. Returns [table next-index], or nil."
  [toks i]
  (let [tok (get toks i)]
    (when (and (name-token? tok) (not (contains? keywords (:text tok))))
      (loop [i i]
        (if (and (= "." (:text (get toks (inc i)))) (name-token? (get toks (+ i 2))))
          (recur (+ i 2))
          (let [after (inc i)
                alias-at (if (word? (get toks after) "as") (inc after) after)
                aliased? (and (name-token? (get toks alias-at))
                              (not (contains? #{"where" "group" "order" "limit" "cross" "inner" "left" "right" "full"
                                                "join" "on" "union" "having"}
                                              (:text (get toks alias-at)))))]
            [(:text (get toks i)) (if aliased? (inc alias-at) after)]))))))

(defn- tables
  "The names after FROM and JOIN, and after the commas of a FROM list."
  [toks]
  (reduce (fn [found i]
            (let [tok (get toks i)]
              (if (or (word? tok "from") (word? tok "join"))
                (loop [found found, at (inc i)]
                  (if-let [[table next-at] (read-table toks at)]
                    (if (= "," (:text (get toks next-at)))
                      (recur (conj found table) (inc next-at))
                      (conj found table))
                    found))
                found)))
          #{}
          (range (count toks))))

(defn- used-names
  "The names in the text that are not an alias, so not the word after AS."
  [toks]
  (into #{}
        (keep-indexed (fn [i tok]
                        (when (and (name-token? tok) (not (word? (get toks (dec i)) "as")))
                          (:text tok))))
        toks))

(defn- short-name
  [member-name]
  (str/lower-case (last (str/split (str member-name) #"\."))))

(defn- cube-index
  "A map from a lower case cube name to the members that the scan needs."
  [cube-meta]
  (into {}
        (map (fn [{cube-name :name, :keys [dimensions measures]}]
               [(str/lower-case cube-name)
                {:time     (into #{} (comp (filter #(= "time" (:type %))) (map (comp short-name :name))) dimensions)
                 :rolling  (into #{} (comp (filter :cumulative) (map (comp short-name :name))) measures)
                 :measures (into #{} (map (comp short-name :name)) measures)
                 :same-source (cube.meta/same-source-groups dimensions)
                 :same-sql (cube.meta/same-sql-groups dimensions)
                 :cube-name cube-name}]))
        (:cubes cube-meta)))

(defn- refuse!
  [text]
  (throw (ex-info text {:type :invalid-query})))

;;; ------------------------------------------------ one join path ------------------------------------------------

(defn- check-one-path!
  "Cube joins a cube once in a query. A view with two join paths to one cube gives the same value for both columns."
  [index toks]
  (let [used (used-names toks)]
    (doseq [table (tables toks)
            group (:same-source (get index table))
            :let  [both (sort (filter used group))]
            :when (next both)]
      (refuse! (format (str "The columns %s of %s come from one cube through different join paths. Cube joins that cube "
                            "once in a query, so both columns would show the same value and the number would be wrong. "
                            "Use one of these columns in a query, or use a join alias in the data model.")
                       (str/join " and " both) table)))
    (when (some #(word? % "where") toks)
      (doseq [table (tables toks)
              group (:same-sql (get index table))
              :let  [both (sort (filter used group))]
              :when (next both)]
        (refuse! (format (str "The columns %s of %s have equal SQL. With a filter in the query, Cube reads the first subquery for "
                              "both columns, so one number would be wrong. Use one of these columns in a query, or give one "
                              "of them other SQL in the data model, such as {orders.count} + 0.")
                         (str/join " and " both) table))))))

;;; --------------------------------------------------- bounds ----------------------------------------------------

(defn- date-only?
  [text]
  (boolean (re-matches #"\d{4}-\d{2}-\d{2}" text)))

(defn- parse-time
  "A time text as UTC, or nil."
  ^LocalDateTime [^String text]
  (let [text (str/trim text)]
    (try
      (cond
        (date-only? text)
        (.atStartOfDay (LocalDate/parse text))

        (re-find #"(Z|[+-]\d{2}(:?\d{2})?)$" text)
        (.toLocalDateTime (.withOffsetSameInstant (OffsetDateTime/parse (str/replace text " " "T")) ZoneOffset/UTC))

        :else
        (LocalDateTime/parse (str/replace text " " "T")))
      (catch Exception _ nil))))

(defn- param-time
  ^LocalDateTime [value]
  (condp instance? value
    LocalDateTime  value
    LocalDate      (.atStartOfDay ^LocalDate value)
    OffsetDateTime (.toLocalDateTime (.withOffsetSameInstant ^OffsetDateTime value ZoneOffset/UTC))
    ZonedDateTime  (.toLocalDateTime (.withZoneSameInstant ^ZonedDateTime value ZoneOffset/UTC))
    String         (parse-time value)
    nil))

(defn- read-literal
  "The time literal at index `i`: {:time LocalDateTime, :plain-date? true for a bare date text, :next index}, or nil.
  With a type word, as in TIMESTAMP 'x' or DATE 'x', Cube reads the literal as SQL does."
  [toks params i]
  (let [tok (get toks i)]
    (cond
      (= :string (:kind tok))
      {:time (parse-time (:text tok)), :plain-date? (date-only? (:text tok)), :next (inc i)}

      (and (#{"timestamp" "timestamptz" "date"} (:text tok))
           (= :string (:kind (get toks (inc i)))))
      {:time (parse-time (:text (get toks (inc i)))), :plain-date? false, :next (+ i 2)}

      (= :param (:kind tok))
      (let [value (nth params (:param tok) nil)]
        {:time (param-time value), :plain-date? (instance? LocalDate value), :next (inc i)}))))

(def ^:private flipped-op
  {"<" :>, "<=" :>=, ">" :<, ">=" :<=, "=" :=})

(defn- literal-before
  "The literal that ends at index `i`: a text with or without a type word, or a parameter."
  [toks params i]
  (let [tok (get toks i)]
    (cond
      (= :param (:kind tok))
      (read-literal toks params i)

      (= :string (:kind tok))
      (read-literal toks params (if (#{"timestamp" "timestamptz" "date"} (:text (get toks (dec i)))) (dec i) i)))))

(defn- comparisons
  "Each `column op literal`, `literal op column` and `column [NOT] BETWEEN a AND b` on a column of `columns`, with the index of the
  column. `:literal` has no `:time` if the other side is not a literal that the scan can read."
  [toks params columns]
  (keep (fn [i]
          (let [tok (get toks i)]
            (when (and (name-token? tok) (contains? columns (:text tok)))
              (let [op      (get toks (inc i))
                    negated (word? op "not")
                    op-at   (if negated (+ i 2) (inc i))
                    op      (get toks op-at)
                    before  (get toks (dec i))
                    table?  (= "." (:text before))
                    start   (if table? (- i 3) (dec i))
                    before  (get toks start)]
                (cond
                  (word? op "between")
                  (when-let [low (read-literal toks params (inc op-at))]
                    (when (word? (get toks (:next low)) "and")
                      (when-let [high (read-literal toks params (inc (:next low)))]
                        {:column (:text tok), :op :between, :negated negated, :low low, :high high, :at i})))

                  (and (not negated) (contains? #{"<" "<=" ">" ">=" "="} (:text op)) (= :other (:kind op)))
                  {:column (:text tok), :op (keyword (:text op)), :literal (read-literal toks params (inc op-at)), :at i}

                  (and (= :other (:kind before)) (flipped-op (:text before)) (literal-before toks params (dec start)))
                  {:column (:text tok), :op (flipped-op (:text before)), :literal (literal-before toks params (dec start)), :at i})))))
        (range (count toks))))

(defn- time-columns
  "The time columns of the tables of the text, and their aliases in the text, such as `c` in `created_at AS c`."
  [index toks]
  (let [columns (into #{} (mapcat #(:time (get index %))) (tables toks))]
    (into columns
          (keep (fn [i]
                  (when (and (contains? columns (:text (get toks i)))
                             (word? (get toks (inc i)) "as")
                             (name-token? (get toks (+ i 2))))
                    (:text (get toks (+ i 2))))))
          (range (count toks)))))

(defn- check-between!
  "Cube pads a date with no time at the end of a date range to the end of that day. SQL reads it as midnight."
  [index toks params]
  (doseq [{:keys [op high column]} (comparisons toks params (time-columns index toks))
          :when (and (= :between op) (:plain-date? high))]
    (refuse! (format (str "Cube reads the end of BETWEEN on %s as the end of that day when it has no time, but SQL reads it "
                          "as midnight, so the number would differ from SQL. Write the time: BETWEEN TIMESTAMP "
                          "'2025-01-01 00:00:00' AND TIMESTAMP '2025-01-31 00:00:00', or use >= and < the next day.")
                     column))))

(defn- or-between?
  [toks a b]
  (some #(word? % "or") (subvec toks (min a b) (max a b))))

(defn- fraction?
  [^LocalDateTime t]
  (and t (not (zero? (.getNano t)))))

(defn- check-strict-bound!
  "`col > a AND col <= b` makes a date range. Cube starts it 1 ms after `a`, and ClickHouse drops the milliseconds. The other side
  of the comparison does not need to be a literal, because the driver cannot read it."
  [index toks params]
  (let [found (comparisons toks params (time-columns index toks))]
    (doseq [{:keys [column at]} (filter #(= :> (:op %)) found)
            upper (filter #(and (= column (:column %)) (contains? #{:< :<=} (:op %))) found)
            :when (not (or-between? toks at (:at upper)))]
      (refuse! (format (str "Cube reads > on %s as >= when the same filter has an upper bound, so the number would include "
                            "the bound. Write >= with the next second, for example %s >= TIMESTAMP '2025-01-01 00:00:01', or "
                            "install the ClickHouse dialect, see cube-clickhouse/README.md.")
                       column column)))))

(defn- check-fraction!
  "ClickHouse drops the fraction of a time in a filter, so a bound moves and rows of a DateTime64 go in or out."
  [index toks params]
  (doseq [{:keys [column literal low high]} (comparisons toks params (time-columns index toks))
          :when (some (comp fraction? :time) [literal low high])]
    (refuse! (format (str "Cube drops the fraction of a second in a filter on %s, so the number would be wrong. Use whole seconds, "
                          "or install the ClickHouse dialect, see cube-clickhouse/README.md.")
                     column))))

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

(defn- date-trunc-units
  "A map from a time column to the units of its DATE_TRUNC in the text."
  [toks columns]
  (reduce (fn [found i]
            (let [unit   (get toks (+ i 2))
                  column (first (filter #(contains? columns (:text %))
                                        (take-while #(not= ")" (:text %)) (subvec toks (min (count toks) (+ i 3))))))]
              (if (and (word? (get toks i) "date_trunc") (= :string (:kind unit)) column)
                (update found (:text column) (fnil conj #{}) (str/lower-case (:text unit)))
                found)))
          {}
          (range (count toks))))

(defn- trunc-column-indexes
  "The indexes of the time columns inside DATE_TRUNC."
  [toks columns]
  (into #{}
        (mapcat (fn [i]
                  (when (word? (get toks i) "date_trunc")
                    (let [after (subvec toks (min (count toks) (inc i)))
                          size  (count (take-while #(not= ")" (:text %)) after))]
                      (filter #(contains? columns (:text (get toks %))) (range (inc i) (+ i 1 size)))))))
        (range (count toks))))

(defn- other-time-use
  "The name of a time column in a place that the scan cannot read, such as IN or a function, or nil."
  [toks params columns]
  (let [read-at (into (trunc-column-indexes toks columns) (map :at) (comparisons toks params columns))]
    (some (fn [i]
            (let [tok (get toks i)]
              (when (and (name-token? tok)
                         (contains? columns (:text tok))
                         (not (contains? read-at i))
                         (not (word? (get toks (dec i)) "as")))
                (:text tok))))
          (range (count toks)))))

(defn- used-rolling
  [index toks]
  (let [used (used-names toks)]
    (into (sorted-set)
          (comp (mapcat #(:rolling (get index %))) (filter used))
          (tables toks))))

(defn- check-rolling!
  [index toks params]
  (when-let [measure (first (used-rolling index toks))]
    (let [columns (time-columns index toks)
          units   (date-trunc-units toks columns)]
      (when (empty? units)
        (refuse! (format (str "%s is a rolling window measure. With no time bucket, Cube returns the total of all rows, not "
                              "the window. Group by DATE_TRUNC on a time column and add a date filter on whole buckets.")
                         measure)))
      (let [[column column-units] (first units)
            unit                  (first column-units)]
        (when (some #(or (word? % "or") (word? % "not")) toks)
          (refuse! (format (str "%s is a rolling window measure, and Cube computes it for each whole bucket. This scan cannot "
                                "show that a filter with OR or NOT keeps whole buckets, so write the filter with >= and <.")
                           measure)))
        (when-let [other (other-time-use toks params columns)]
          (refuse! (format (str "%s is a rolling window measure, and Cube computes it for each whole bucket. This scan cannot read "
                                "how the query uses %s, so it cannot show that the filter keeps whole buckets. Write the filter "
                                "with >= and <, and use only DATE_TRUNC on this column.")
                           measure other)))
        (when-not (bucket-start unit (LocalDateTime/of 2025 1 1 0 0))
          (refuse! (format (str "%s is a rolling window measure, and this scan cannot read the bucket '%s' of native SQL. "
                                "Use a bucket of day, week, month, quarter or year, or the query builder.")
                           measure unit)))
        (doseq [{:keys [op literal low high negated]} (comparisons toks params #{column})]
          (let [ok? (case op
                      :between (and (not negated)
                                    (bound-on-boundary? unit :>= (:time low))
                                    (bound-on-boundary? unit :<= (:time high)))
                      :=       (and (:time literal)
                                    (bound-on-boundary? unit :>= (:time literal))
                                    (bound-on-boundary? unit :<= (:time literal)))
                      (bound-on-boundary? unit op (:time literal)))]
            (when-not ok?
              (refuse! (format (str "%s is a rolling window measure, and Cube computes it for each whole %s. This date filter "
                                    "starts or ends inside a %s, and Cube does not apply that part, so the number would count "
                                    "rows outside the filter. Use a filter on whole %ss.")
                               measure unit unit unit)))))))))

;;; ------------------------------------------------- filters on a subquery ---------------------------------------------

(defn- closing-paren
  "The index of the parenthesis that closes the one at `open`, or nil."
  [toks open]
  (loop [i open, depth 0]
    (when-let [tok (get toks i)]
      (let [depth (case (:text tok)
                    "(" (inc depth)
                    ")" (dec depth)
                    depth)]
        (if (and (zero? depth) (= ")" (:text tok)))
          i
          (recur (inc i) (long depth)))))))

(defn- outer-where
  "The tokens of the WHERE right after the subquery that closes at `close`, or nil."
  [toks close]
  (let [after    (inc close)
        after    (if (word? (get toks after) "as") (inc after) after)
        where-at (if (word? (get toks after) "where")
                   after
                   (when (word? (get toks (inc after)) "where") (inc after)))]
    (when where-at
      (subvec toks (inc where-at)
              (or (first (filter (fn [i]
                                   (let [tok (get toks i)]
                                     (or (some #(word? tok %) ["group" "order" "limit" "having"]) (= ")" (:text tok)))))
                                 (range (inc where-at) (count toks))))
                  (count toks))))))

(defn- check-filter-over!
  "`inner` has the tokens of a subquery, and `where` the tokens of the filter on it. Cube pushes an outer filter below the
  aggregation of a subquery, so the rows of the subquery change. A limit in the subquery stops this. Over a subquery with raw rows,
  Cube reads a filter on a measure column as a filter on the aggregate."
  [index inner where]
  (let [aggregated? (some #(or (word? % "group") (word? % "measure")) inner)
        limited?    (some #(word? % "limit") inner)
        measures    (into #{} (mapcat #(:measures (get index %))) (tables inner))
        on-measure  (first (filter #(and (name-token? %) (contains? measures (:text %))) where))]
    (when (and aggregated? (not limited?))
      (refuse! (str "Cube pushes a filter of an outer query below the aggregation of its subquery, so the rows of the subquery "
                    "change and the number would be wrong. Add LIMIT 2147483647 to the subquery.")))
    (when (and on-measure (not aggregated?))
      (refuse! (format (str "Cube reads the filter on the measure column %s as a filter on the aggregate and not on rows, so the "
                            "number would be wrong. Aggregate %s with GROUP BY and LIMIT in the subquery, and filter on its "
                            "result.")
                       (:text on-measure) (:text on-measure))))))

(defn- common-table-bodies
  "A map from the name of each query of WITH to the tokens of its body."
  [toks]
  (into {}
        (keep (fn [i]
                (when (and (word? (get toks i) "as")
                           (= "(" (:text (get toks (inc i))))
                           (word? (get toks (+ i 2)) "select")
                           (name-token? (get toks (dec i))))
                  (when-let [close (closing-paren toks (inc i))]
                    [(:text (get toks (dec i))) (subvec toks (+ i 2) close)]))))
        (range 1 (count toks))))

(defn- check-subquery-filters!
  [index toks]
  (let [bodies (common-table-bodies toks)]
    (doseq [i (range (count toks))
            :when (word? (get toks i) "from")
            :let  [next-tok (get toks (inc i))]]
      (cond
        (and (= "(" (:text next-tok)) (word? (get toks (+ i 2)) "select"))
        (when-let [close (closing-paren toks (inc i))]
          (when-let [where (seq (outer-where toks close))]
            (check-filter-over! index (subvec toks (+ i 2) close) where)))

        (contains? bodies (:text next-tok))
        (when-let [where (seq (outer-where toks (inc i)))]
          (check-filter-over! index (get bodies (:text next-tok)) where))))))

;;; ------------------------------------------------- time shift measures ----------------------------------------------

(defn- time-filter-or-bucket?
  [toks params columns]
  (or (seq (date-trunc-units toks columns))
      (seq (comparisons toks params columns))))

(defn- used-measures
  "[cube short-name] of the measures of the tables of the text that the text names."
  [index toks]
  (let [used (used-names toks)]
    (for [table (tables toks)
          :let  [{:keys [measures cube-name]} (get index table)]
          measure (filter used measures)]
      [cube-name measure])))

(defn- check-time-shift!
  [index toks params time-shift?]
  (let [columns (time-columns index toks)]
    (when-not (time-filter-or-bucket? toks params columns)
      (doseq [[cube-name measure] (used-measures index toks)
              :when (time-shift? cube-name (str cube-name "." measure))]
        (refuse! (format (str "%s has a time shift. With no time bucket and no date filter, Cube returns the total of all rows, and "
                              "a shift of that is not a number that you can read. Group by DATE_TRUNC on a time column, or add a "
                              "date filter.")
                         measure))))))

;;; ------------------------------------------------- custom granularities -----------------------------------------------

(defn- check-granularity!
  [index toks wrong-bin-reason]
  (let [columns (time-columns index toks)
        units   (date-trunc-units toks columns)]
    (when (seq units)
      (let [measures (used-measures index toks)]
        (doseq [table (tables toks)
                :let  [{:keys [time cube-name]} (get index table)]
                [column column-units] units
                :when (contains? time column)
                unit  column-units
                :let  [own (for [[measure-cube measure] measures
                                 :when (= measure-cube cube-name)]
                             (str measure-cube "." measure))]]
          (when-let [reason (wrong-bin-reason cube-name column unit own)]
            (refuse! reason)))))))

;;; --------------------------------------------------- entry points ------------------------------------------------

(defn check-paths!
  "For each query, also a compiled one."
  [cube-meta sql]
  (check-one-path! (cube-index cube-meta) (tokens sql)))

(defn check-native!
  "For the text that a person or an agent wrote. `questions` has the answers that need Cube: :keeps-fractions? a boolean,
  :time-shift? a function of the cube and the full name of a measure, and :wrong-bin-reason a function of the cube, the time
  member, the granularity and the full names of the measures."
  [cube-meta sql params {:keys [keeps-fractions? time-shift? wrong-bin-reason]}]
  (let [index (cube-index cube-meta)
        toks  (unwrap (tokens sql))]
    (check-one-path! index toks)
    (check-rolling! index toks params)
    (check-between! index toks params)
    (check-subquery-filters! index toks)
    (when-not keeps-fractions?
      (check-strict-bound! index toks params)
      (check-fraction! index toks params))
    (when time-shift?
      (check-time-shift! index toks params time-shift?))
    (when wrong-bin-reason
      (check-granularity! index toks wrong-bin-reason))))
