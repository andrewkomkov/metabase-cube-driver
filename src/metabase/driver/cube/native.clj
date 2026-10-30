(ns metabase.driver.cube.native
  "A cheap scan of SQL text, for shapes where Cube gives a wrong number with no error. It splits the text into tokens and
  reads only `column op literal`, BETWEEN and DATE_TRUNC. It is not a SQL parser. A shape that it does not read reaches
  Cube as before, and docs/query-compilation.md lists those shapes in \"Known wrong numbers\"."
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
                 :same-source (cube.meta/same-source-groups dimensions)}]))
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
                       (str/join " and " both) table)))))

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

(defn- comparisons
  "Each `column op literal` and `column [NOT] BETWEEN a AND b` on a column of `columns`, with the index of the column."
  [toks params columns]
  (keep (fn [i]
          (let [tok (get toks i)]
            (when (and (name-token? tok) (contains? columns (:text tok)))
              (let [op      (get toks (inc i))
                    negated (word? op "not")
                    op-at   (if negated (+ i 2) (inc i))
                    op      (get toks op-at)]
                (cond
                  (word? op "between")
                  (when-let [low (read-literal toks params (inc op-at))]
                    (when (word? (get toks (:next low)) "and")
                      (when-let [high (read-literal toks params (inc (:next low)))]
                        {:column (:text tok), :op :between, :negated negated, :low low, :high high, :at i})))

                  (and (not negated) (contains? #{"<" "<=" ">" ">=" "="} (:text op)) (= :other (:kind op)))
                  {:column (:text tok), :op (keyword (:text op)), :literal (read-literal toks params (inc op-at)), :at i})))))
        (range (count toks))))

(defn- time-columns
  [index toks]
  (into #{} (mapcat #(:time (get index %))) (tables toks)))

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

(defn- check-strict-bound!
  "`col > a AND col <= b` makes a date range. Cube starts it 1 ms after `a`, and ClickHouse drops the milliseconds."
  [index toks params]
  (let [found (comparisons toks params (time-columns index toks))]
    (doseq [{:keys [column at]} (filter #(and (= :> (:op %)) (:literal %)) found)
            upper (filter #(and (= column (:column %)) (contains? #{:< :<=} (:op %)) (:literal %)) found)
            :when (not (or-between? toks at (:at upper)))]
      (refuse! (format (str "Cube reads > on %s as >= when the same filter has an upper bound, so the number would include "
                            "the bound. Write >= with the next second, for example %s >= TIMESTAMP '2025-01-01 00:00:01'.")
                       column column)))))

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

;;; --------------------------------------------------- entry points ------------------------------------------------

(defn check-paths!
  "For each query, also a compiled one."
  [cube-meta sql]
  (check-one-path! (cube-index cube-meta) (tokens sql)))

(defn check-native!
  "For the text that a person or an agent wrote."
  [cube-meta sql params]
  (let [index (cube-index cube-meta)
        toks  (tokens sql)]
    (check-one-path! index toks)
    (check-rolling! index toks params)
    (check-between! index toks params)
    (check-strict-bound! index toks params)))
