(ns metabase.driver.cube.native
  "A cheap scan of SQL text, for shapes where Cube gives a wrong number with no error, and for private members. It splits the text into tokens with a lexer that follows
  the rules of PostgreSQL, removes parentheses and casts around a column or a literal, and reads `column op literal` on either side, BETWEEN and DATE_TRUNC.
  It is not a SQL parser. docs/query-compilation.md lists the shapes that it does not read in \"Known wrong numbers\", and docs/security.md says what the scan of private members is."
  (:require
   [clojure.string :as str]
   [metabase.driver.cube.meta :as cube.meta]
   [metabase.driver.cube.probe :as cube.probe])
  (:import
   (java.time DayOfWeek LocalDate LocalDateTime OffsetDateTime ZoneOffset ZonedDateTime)
   (java.time.temporal ChronoUnit TemporalAdjusters)))

(set! *warn-on-reflection* true)

;;; The lexer. It reads the text as the parser of Cube does, which has the rules of PostgreSQL: a string with '' or, with the prefix E, also a backslash; a quoted name with "";
;;; a string in dollar quotes; a comment with -- or /* */, and these comments nest. A text that the lexer cannot read has a reason in :unreadable. See check-native-private-member!.

(defn- word-start?
  [c]
  (or (Character/isLetter (char c)) (= c \_)))

(defn- word-char?
  [c]
  (or (Character/isLetterOrDigit (char c)) (= c \_) (= c \$)))

(defn- quoted-end
  "The index after the quote that closes the text that opens at `start`, with a doubled quote inside it, or nil. With `backslash?`, a backslash also keeps the next character."
  [^String sql start quote-char backslash?]
  (let [size  (count sql)
        quote (char quote-char)]
    (loop [i (inc (long start))]
      (when (< i size)
        (let [c (.charAt sql i)]
          (cond
            (and backslash? (= c \\))                                   (recur (+ i 2))
            (and (= c quote) (< (inc i) size) (= quote (.charAt sql (inc i)))) (recur (+ i 2))
            (= c quote)                                                 (inc i)
            :else                                                       (recur (inc i))))))))

(defn- comment-end
  "The index after the comment that opens at `start`. /* */ nests. Nil if it has no end."
  [^String sql start]
  (loop [i (+ (long start) 2), depth 1]
    (cond
      (zero? depth)            i
      (>= i (count sql))       nil
      (.startsWith sql "/*" i) (recur (+ i 2) (inc depth))
      (.startsWith sql "*/" i) (recur (+ i 2) (dec depth))
      :else                    (recur (inc i) depth))))

(defn- dollar-tag
  "The opening `$tag$` at `start`, or nil."
  [^String sql start]
  (let [matcher (re-matcher #"\$(?:[\p{L}_][\p{L}\p{N}_]*)?\$" sql)]
    (.region matcher (int start) (count sql))
    (when (.lookingAt matcher) (.group matcher))))

(defn- string-of
  [^String sql start end kind]
  (let [inner (subs sql start end)]
    {:kind  :string
     :text  (case kind
              :plain  (str/replace (subs inner 1 (dec (count inner))) "''" "'")
              :escape (-> (subs inner 2 (dec (count inner)))
                          (str/replace "''" "'")
                          (str/replace #"\\(.)" "$1")))}))

(defn- lex
  "{:tokens the tokens of `sql` with their positions, and no comments, :unreadable the reason if the text is not one that the scan can read}."
  [^String sql]
  (let [size (count sql)]
    (loop [i 0, out [], unreadable nil]
      (if (>= i size)
        {:tokens out, :unreadable unreadable}
        (let [c    (.charAt sql i)
              next-c (when (< (inc i) size) (.charAt sql (inc i)))]
          (cond
            (Character/isWhitespace c)
            (recur (inc i) out unreadable)

            (.startsWith sql "--" i)
            (let [end (let [at (.indexOf sql "\n" i)] (if (neg? at) size at))]
              (recur (long end) out (or unreadable
                                        ;; PostgreSQL ends this comment at a carriage return, and the parser of Cube does not.
                                        (when (re-find #"\r(?!\n)" (subs sql i (min size (inc end)))) "a carriage return in a comment"))))

            (.startsWith sql "/*" i)
            (if-let [end (comment-end sql i)]
              (recur (long end) out unreadable)
              {:tokens out, :unreadable (or unreadable "a comment with no end")})

            (and (#{\E \e} c) (= next-c \'))
            (if-let [end (quoted-end sql (inc i) \' true)]
              (recur (long end) (conj out (assoc (string-of sql i end :escape) :start i :end end)) unreadable)
              {:tokens out, :unreadable (or unreadable "a string with no end")})

            (and (#{\U \u} c) (= next-c \&) (< (+ i 2) size) (#{\' \"} (.charAt sql (+ i 2))))
            (let [end (quoted-end sql (+ i 2) (.charAt sql (+ i 2)) false)]
              {:tokens out, :unreadable (or unreadable (if end "a name or a string with Unicode escapes" "a string with no end"))})

            (= c \')
            (if-let [end (quoted-end sql i \' false)]
              (recur (long end)
                     (conj out (assoc (string-of sql i end :plain) :start i :end end))
                     ;; Standard SQL reads a backslash as a character, and other dialects read the quote after it as a part of the string.
                     (or unreadable (when (str/includes? (subs sql i end) "\\'") "a backslash before a quote in a string")))
              {:tokens out, :unreadable (or unreadable "a string with no end")})

            (= c \")
            (if-let [end (quoted-end sql i \" false)]
              (recur (long end)
                     (conj out {:kind  :ident
                                :text  (str/lower-case (str/replace (subs sql (inc i) (dec end)) "\"\"" "\""))
                                :start i
                                :end   end})
                     unreadable)
              {:tokens out, :unreadable (or unreadable "a name with no end")})

            (= c \$)
            (if-let [tag (dollar-tag sql i)]
              (let [at (.indexOf sql ^String tag (int (+ i (count tag))))]
                (if (neg? at)
                  {:tokens out, :unreadable (or unreadable "a string in dollar quotes with no end")}
                  (let [end (+ at (count tag))]
                    (recur (long end)
                           (conj out {:kind :string, :text (subs sql (+ i (count tag)) at), :start i, :end end})
                           unreadable))))
              (let [end (loop [j (inc i)] (if (and (< j size) (Character/isDigit (.charAt sql j))) (recur (inc j)) j))]
                (recur (long end) (conj out {:kind :other, :text (subs sql i end), :start i, :end end}) unreadable)))

            (word-start? c)
            (let [end (loop [j (inc i)] (if (and (< j size) (word-char? (.charAt sql j))) (recur (inc j)) j))]
              (recur (long end) (conj out {:kind :word, :text (str/lower-case (subs sql i end)), :start i, :end end}) unreadable))

            (or (Character/isDigit c)
                (and (= c \.) next-c (Character/isDigit (char next-c))
                     (not (contains? #{:word :ident} (:kind (peek out))))))
            (let [matcher (re-matcher #"\d*\.?\d*(?:[eE][+-]?\d+)?" sql)]
              (.region matcher (int i) size)
              (.lookingAt matcher)
              (let [end (.end matcher)]
                (recur (long end) (conj out {:kind :other, :number? true, :text (.group matcher), :start i, :end end}) unreadable)))

            (some #(.startsWith sql ^String % i) ["<=" ">=" "<>" "!="])
            (recur (+ i 2) (conj out {:kind :other, :text (subs sql i (+ i 2)), :start i, :end (+ i 2)}) unreadable)

            :else
            (recur (inc i) (conj out {:kind :other, :text (str c), :start i, :end (inc i)}) unreadable)))))))

(defn- tokens
  "The tokens of `sql` as a vector, with no comments. Each `?` has the number of its query parameter."
  [sql]
  (first
   (reduce (fn [[out params] {:keys [text kind] :as tok}]
             (if (and (= :other kind) (= text "?"))
               [(conj out (assoc tok :kind :param, :param params)) (inc params)]
               [(conj out tok) params]))
           [[] 0]
           (:tokens (lex sql)))))

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
                 :count-measures (into #{}
                                       (comp (filter #(contains? #{"count" "countDistinct" "countDistinctApprox"} (:aggType %)))
                                             (map (comp short-name :name)))
                                       measures)
                 :dimensions (into #{} (map (comp short-name :name)) dimensions)
                 :numbers (into #{} (comp (filter #(= "number" (:type %))) (map (comp short-name :name))) dimensions)
                 :switches (cube.meta/switch-dimension-names cube-meta cube-name)
                 :date-only (into #{}
                                  (comp (filter #(cube.probe/date-only? cube-meta (:name %))) (map (comp short-name :name)))
                                  dimensions)
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

(def ^:private filter-end
  "Words that end a WHERE."
  #{"group" "order" "limit" "having" "union"})

(defn- where-indexes
  "The indexes of the tokens of each WHERE."
  [toks]
  (mapcat (fn [i]
            (when (word? (get toks i) "where")
              (take-while #(let [tok (get toks %)]
                             (not (and (= :word (:kind tok)) (contains? filter-end (:text tok)))))
                          (range (inc i) (count toks)))))
          (range (count toks))))

(defn- time-function-in-filter
  "The name of a time column inside DATE_TRUNC in a WHERE, or nil. The scan reads DATE_TRUNC only as the bucket of the query."
  [toks columns]
  (let [trunc (trunc-column-indexes toks columns)]
    (some (fn [i] (when (contains? trunc i) (:text (get toks i)))) (where-indexes toks))))

(defn- bound-with-arithmetic
  "The name of a time column compared with a literal that has more text after it, such as `+ INTERVAL '14 day'`, or nil."
  [toks params columns]
  (some (fn [{:keys [column literal high at]}]
          (when-let [end (:next (or high literal))]
            (let [tok (get toks end)]
              (when (and tok
                         (> end at)
                         (not (= ")" (:text tok)))
                         (not (= ";" (:text tok)))
                         (not (contains? #{"and" "or" "group" "order" "limit" "having" "union"} (:text tok))))
                column))))
        (comparisons toks params columns)))

(defn- used-rolling
  [index toks]
  (let [used (used-names toks)]
    (into (sorted-set)
          (comp (mapcat #(:rolling (get index %))) (filter used))
          (tables toks))))

(defn- check-rolling!
  [index toks params {:keys [rolling-reason rolling-zone-reason]}]
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
        (when-let [other (or (time-function-in-filter toks columns)
                             (bound-with-arithmetic toks params columns)
                             (other-time-use toks params columns))]
          (refuse! (format (str "%s is a rolling window measure, and Cube computes it for each whole bucket. This scan cannot read "
                                "how the query uses %s, so it cannot show that the filter keeps whole buckets. Write the filter "
                                "with >= and <, and use only DATE_TRUNC on this column.")
                           measure other)))
        (when-not (bucket-start unit (LocalDateTime/of 2025 1 1 0 0))
          (refuse! (format (str "%s is a rolling window measure, and this scan cannot read the bucket '%s' of native SQL. "
                                "Use a bucket of day, week, month, quarter or year, or the query builder.")
                           measure unit)))
        (when-let [table (first (filter #(contains? (:rolling (get index %)) measure) (tables toks)))]
          (let [cube-name (:cube-name (get index table))]
            (doseq [question [rolling-zone-reason rolling-reason]
                    :when    question]
              (when-let [reason (question (str cube-name "." measure) (str cube-name "." column) (keyword unit))]
                (refuse! reason)))))
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

;;; ----------------------------------------------- shapes that Cube reads otherwise ---------------------------------------

(declare closing-paren)

(defn- check-equal-time-in-bucket!
  "With DATE_TRUNC on the same column, Cube reads `x = t` as the bucket that starts at `t`, and it returns the whole bucket."
  [index toks params]
  (let [columns (time-columns index toks)
        units   (date-trunc-units toks columns)]
    (doseq [{:keys [op column]} (comparisons toks params columns)
            :when (and (= := op) (contains? units column))]
      (refuse! (format (str "With DATE_TRUNC on %s, Cube reads %s = t as the whole bucket that starts at t, so the number would be wrong. "
                            "Write %s >= t AND %s <= t.")
                       column column column column)))))

(defn- wildcard-with-letters?
  [pattern]
  (and (re-find #"[%_]" pattern) (re-find #"\p{L}" pattern)))

(defn- check-text-case!
  "Cube reads these forms as its operators, and on ClickHouse they ignore case. SQL and Metabase read them as case sensitive."
  [toks]
  (let [message (fn [text]
                  (format (str "Cube reads %s as a filter that ignores case, so the number can differ from SQL. Write ILIKE to ignore case. To "
                               "keep the case, write STRPOS(column, 'text') >= 1 to find a text, or STRPOS(column, 'text') = 1 for the start of a "
                               "column.")
                          text))]
    (doseq [i (range (count toks))
            :let [tok (get toks i)]]
      (when (and (word? tok "like")
                 (not= ")" (:text (get toks (dec i))))
                 (= :string (:kind (get toks (inc i))))
                 (wildcard-with-letters? (:text (get toks (inc i)))))
        (refuse! (message (str "LIKE '" (:text (get toks (inc i))) "'"))))
      (when (and (= :word (:kind tok)) (contains? #{"strpos" "left" "right"} (:text tok)) (= "(" (:text (get toks (inc i)))))
        (when-let [close (closing-paren toks (inc i))]
          (let [op    (:text (get toks (inc close)))
                value (:text (get toks (+ close 2)))]
            (when (or (and (= "strpos" (:text tok)) (= ">" op) (= "0" value))
                      (and (not= "strpos" (:text tok)) (= "=" op)))
              (refuse! (message (str (str/upper-case (:text tok)) "() " op " " value))))))))))

(defn- text-before-group?
  [toks]
  (not-any? #(or (word? % "group") (word? % "measure")) toks))

(defn- star?
  "True if a top SELECT reads all columns."
  [toks]
  (some (fn [i]
          (and (= "*" (:text (get toks i)))
               (or (word? (get toks (dec i)) "select")
                   (and (= "." (:text (get toks (dec i)))) (word? (get toks (- i 3)) "select")))))
        (range (count toks))))

(defn- check-raw-select!
  "A raw select gives each row once for each value of a `switch` dimension. SELECT * also reads a rolling window measure, and then Cube
  fails with a message about a date range."
  [index toks]
  (when (text-before-group? toks)
    (let [used (used-names toks)]
      (doseq [table (tables toks)
              :let  [{:keys [switches rolling]} (get index table)]]
        (when (and (star? toks) (seq rolling))
          (refuse! (format (str "SELECT * on %s also reads its rolling window measures, such as %s, and these need a time bucket and a date "
                                "filter. Name the columns that you need.")
                           table (first (sort rolling)))))
        (when-let [switch (or (when (star? toks) (first (sort switches)))
                              (first (sort (filter used switches))))]
          (refuse! (format (str "%s of %s is a switch dimension. A select of raw rows gives each row once for each of its values, so the rows "
                                "would be wrong. Group by it with a measure, or leave out this column.")
                           switch table)))))))

;;; An aggregation of a dimension of a joined cube.

(defn- table-aliases
  "A map from each name that can stand before a column to its table, and the first table of the text under :first."
  [toks]
  (reduce (fn [found i]
            (if (or (word? (get toks i) "from") (word? (get toks i) "join"))
              (if-let [[table next-at] (read-table toks (inc i))]
                (let [alias-name (let [before (get toks (dec next-at))] (:text before))]
                  (cond-> (assoc found table table alias-name table)
                    (nil? (:first found)) (assoc :first table)))
                found)
              found))
          {}
          (range (count toks))))

(def ^:private row-aggregations
  "The aggregations that give another number if a row counts once and not for each row of the query."
  #{"sum" "avg" "count" "stddev" "stddev_pop" "stddev_samp" "variance" "var_pop" "var_samp"})

(defn- argument-column
  "[qualifier column] of the plain column that the call at `i` reads, as in SUM(a.b), SUM(b) or COUNT(DISTINCT b), or nil. DISTINCT does not depend on how often a row counts."
  [toks i]
  (when (and (= :word (:kind (get toks i)))
             (contains? row-aggregations (:text (get toks i)))
             (= "(" (:text (get toks (inc i)))))
    (let [at (if (word? (get toks (+ i 2)) "all") (+ i 3) (+ i 2))]
      (cond
        (and (name-token? (get toks at)) (= "." (:text (get toks (inc at)))) (name-token? (get toks (+ at 2))) (= ")" (:text (get toks (+ at 3)))))
        [(:text (get toks at)) (:text (get toks (+ at 2)))]

        (and (name-token? (get toks at)) (= ")" (:text (get toks (inc at)))))
        [nil (:text (get toks at))]))))

(defn- check-total-through-join!
  "Cube reads each row of a joined cube once. See compile.join.total_of_dimension_refused."
  [index toks joined-member-of-view?]
  (let [aliases (table-aliases toks)
        start   (:first aliases)
        read    (tables toks)]
    (doseq [i     (range (count toks))
            :let  [[qualifier column] (argument-column toks i)]
            :when column]
      (let [table (cond
                    qualifier                                   (get aliases qualifier)
                    (contains? (:dimensions (get index start)) column) start
                    :else                                       (first (filter #(contains? (:dimensions (get index %)) column) read)))]
        (when (and table
                   (not= table start)
                   (next read)
                   (contains? (:dimensions (get index table)) column))
          (refuse! (format (str "Cube cannot add up the dimension %s of %s over the rows of %s. It reads each row of the joined cube once, so "
                                "the number would be wrong. Add a measure to the data model, or start from %s.")
                           column table start table)))
        (when (and (= table start)
                   joined-member-of-view?
                   (joined-member-of-view? (:cube-name (get index table)) column))
          (refuse! (format (str "Cube cannot add up the column %s of the view %s over the rows of the view, because this column comes from a joined cube, and Cube reads each of "
                                "its rows once. So the number would be wrong. Add a measure to the data model, or start from the cube of this column.")
                           column table)))))))

;;; A report time zone.

(defn- check-report-zone!
  "Cube reads the two bounds of a date range in the zone of the session, and each other bound as UTC. The driver cannot tell what a literal in
  native SQL means in another form, so only that form goes through."
  [index toks params]
  (let [columns (time-columns index toks)
        found   (comparisons toks params columns)
        message (fn [column]
                  (format (str "With a report time zone that is not UTC, Cube reads a literal on %s in this zone only if the filter has a lower "
                               "and an upper bound and no OR or NOT. In other forms it reads it as UTC, so the number would be wrong. Write "
                               "%s >= a AND %s < b, or remove the report time zone.")
                          column column column))]
    (when-let [column (time-function-in-filter toks columns)]
      (refuse! (message column)))
    (doseq [[column bounds] (group-by :column found)
            :let  [ops (set (map :op bounds))]
            :when (or (some #(or (word? % "or") (word? % "not")) toks)
                      (not (or (:between ops)
                               (and (some ops [:> :>=]) (some ops [:< :<=]))))
                      (:= ops))]
      (refuse! (message column)))
    (doseq [table (tables toks)
            :let  [{:keys [date-only]} (get index table)]
            column (filter (used-names toks) date-only)]
      (refuse! (format (str "%s is a Date column, and stock Cube reads it as a time at midnight UTC. With a report time zone that is not UTC, "
                            "each row can move to another day, so the number would be wrong. Remove the report time zone, or install the "
                            "ClickHouse dialect, see cube-clickhouse/README.md.")
                       column)))))

(defn- check-value-read!
  "The SQL of a cube can read a filter of Cube as a value. See `cube.probe/value-read-members`."
  [index toks params value-read]
  (doseq [table (tables toks)
          :let  [cube-name (:cube-name (get index table))]
          :when cube-name
          [member kind] (value-read cube-name)]
    (let [found (comparisons toks params #{member})
          ops   (set (map :op found))
          other (some #(or (word? % "or") (word? % "not")) toks)]
      (when (or other
                (if (= :time kind)
                  (not (or (:between ops) (and (some ops [:> :>=]) (some ops [:< :<=]))))
                  (not= #{:=} ops)))
        (refuse! (format (if (= :time kind)
                           (str "The SQL of %s reads the date range of %s as a value, through FILTER_PARAMS. Cube gives that value only for a "
                                "lower and an upper bound, with no OR and no NOT. With another filter or none, the database reads 1 = 1 "
                                "there, so the number would be wrong. Write %s >= a AND %s < b.")
                           (str "The SQL of %s reads the filter on %s as a value, through FILTER_PARAMS. Cube gives that value only for one "
                                "equal value. With another filter or none, the database reads 1 = 1 there, so the number would be wrong. "
                                "Write %s = a, with no OR and no NOT."))
                         cube-name member member member))))))

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

;;; ---------------------------------------------- private members ----------------------------------------------

(def ^:private not-an-alias
  #{"where" "group" "order" "having" "limit" "on" "join" "cross" "left" "right" "inner" "full" "outer" "union" "using" "offset" "select" "from" "as"})

(defn- part-of-name?
  [tok]
  (contains? #{:word :ident} (:kind tok)))

(defn- table-at
  "The last part of the name of a table at index `i`, and the index after it. `a.b.c` gives `c`."
  [toks i]
  (when (part-of-name? (get toks i))
    (loop [i i]
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
            (recur (if alias-name (inc alias-at) after)
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

;;; The scan of a text that a person or an agent wrote. A wrong guess here must refuse, and never let a name through:
;;;
;;; - A table counts as read if the text names it anywhere, so no form of FROM can hide it.
;;; - `a.x` reads x of the tables that `a` can mean, and `a` can mean each table of each FROM with that alias. If the scan does not know `a`, it can mean each table of the text.
;;; - A name with no table can mean each table of the text.
;;; - `NULL AS id` over a table with a private `id` is a refusal, because Cube reads a filter on that column of a subquery as a filter on the member.

(defn- refuse-unreadable!
  [reason]
  (refuse! (str "This Cube has private members, and the scan that keeps them private cannot read this SQL: " reason ". Write it in plain SQL. "
                "A Cube in dev mode reads private members, so do not run dev mode in production.")))

(defn- balanced-parens?
  [toks]
  (loop [[tok & more] toks, depth 0]
    (cond
      (nil? tok)  (zero? depth)
      (neg? depth) false
      (= :other (:kind tok)) (recur more (case (:text tok) "(" (inc depth) ")" (dec depth) depth))
      :else (recur more depth))))

(def ^:private subquery-start
  #{"select" "with" "values"})

(defn- mentioned-tables
  "The lower case names of the cubes in `toks` as names, with no call. A column with the name of a cube also counts, so this is more than the tables of the text."
  [known toks]
  (into #{}
        (keep-indexed (fn [i {:keys [text] :as tok}]
                        (when (and (name-token? tok)
                                   (contains? known text)
                                   (not= "(" (:text (get toks (inc i)))))
                          text)))
        toks))

(defn- subquery-aliases
  "A map from the alias of each subquery to the tables that its text names. The tokens of an alias stand after the parenthesis that closes it."
  [known toks]
  (reduce (fn [found i]
            (if (and (= "(" (:text (get toks i)))
                     (= :other (:kind (get toks i)))
                     (contains? subquery-start (:text (get toks (inc i)))))
              (if-let [close (closing-paren toks i)]
                (let [at        (if (word? (get toks (inc close)) "as") (+ close 2) (inc close))
                      alias-tok (get toks at)]
                  (if (and (name-token? alias-tok)
                           (or (= :ident (:kind alias-tok)) (not (not-an-alias (:text alias-tok)))))
                    (update found (:text alias-tok) (fnil into #{}) (mentioned-tables known (subvec toks i close)))
                    found))
                found)
              found))
          {}
          (range (count toks))))

(defn- table-aliases-of-from
  "A map from each table and alias of each FROM and JOIN to the tables that it can mean. A name that two FROM lists use means both."
  [toks]
  (reduce (fn [found i]
            (let [tok (get toks i)]
              (if (or (word? tok "from") (word? tok "join"))
                (loop [found found, at (inc i)]
                  (let [at (if (or (word? (get toks at) "only") (word? (get toks at) "lateral")) (inc at) at)]
                    (if-let [[table after] (table-at toks at)]
                      (let [as?       (word? (get toks after) "as")
                            alias-at  (if as? (inc after) after)
                            alias-tok (get toks alias-at)
                            aliased?  (and (name-token? alias-tok)
                                           (or as? (= :ident (:kind alias-tok)) (not (not-an-alias (:text alias-tok)))))
                            found     (cond-> (update found table (fnil conj #{}) table)
                                        aliased? (update (:text alias-tok) (fnil conj #{}) table))
                            next-at   (if aliased? (inc alias-at) after)]
                        (if (= "," (:text (get toks next-at)))
                          (recur found (inc next-at))
                          found))
                      found)))
                found)))
          {}
          (range (count toks))))

(defn- select-star?
  "True if the `*` at `i` selects columns. It is not an operator or the argument of COUNT(*)."
  [toks i]
  (and (= "*" (:text (get toks i)))
       (let [before (get toks (dec i))
             after  (get toks (inc i))]
         (or (= "." (:text before))
             (and (= :word (:kind before)) (#{"select" "distinct" "all"} (:text before)))
             (and (not= "(" (:text before))
                  (or (nil? after)
                      (contains? #{"," ";"} (:text after))
                      (word? after "from")
                      (word? after "except")
                      (word? after "exclude")
                      (word? after "replace")))))))

(defn- star-tables
  "The tables that the `*` at `i` reads directly: `a.*` gives each table of `a`, and `*` each table of the FROM of its own SELECT. A subquery lists its own columns, and the scan reads them there."
  [toks aliases i]
  (if (= "." (:text (get toks (dec i))))
    (get aliases (:text (get toks (- i 2))) #{(:text (get toks (- i 2)))})
    (loop [j (inc i), in-from? false, out #{}]
      (let [{:keys [kind text] :as tok} (get toks j)]
        (cond
          (nil? tok) out
          (and (= "(" text) (= :other kind))
          (let [close (closing-paren toks j)]
            (cond
              (nil? close)                                       out
              ;; A subquery, or the arguments of a function: its FROM is not a FROM of this SELECT, as in SUBSTRING(x FROM 1).
              (or (contains? subquery-start (:text (get toks (inc j))))
                  (name-token? (get toks (dec j))))              (recur (inc (long close)) in-from? out)
              :else                                              (recur (inc j) in-from? out)))
          (and (= ")" text) (= :other kind)) (recur (inc j) in-from? out)
          (and (= :word kind) (#{"from" "join"} text))
          (let [at (if (or (word? (get toks (inc j)) "only") (word? (get toks (inc j)) "lateral")) (+ j 2) (inc j))]
            (if-let [[table after] (table-at toks at)]
              (recur (long after) true (conj out table))
              (recur (inc j) true out)))
          (and in-from? (= "," text))
          (if-let [[table after] (table-at toks (inc j))]
            (recur (long after) true (conj out table))
            (recur (inc j) true out))
          (and (= :word kind) (#{"where" "group" "order" "having" "limit" "union" "except" "intersect" "offset" "fetch" "window"} text)) out
          (and (= ";" text) (= :other kind)) out
          :else (recur (inc j) in-from? out))))))

(defn check-native-private-member!
  "The scan of the private members for native SQL. It reads each name of the text and it refuses if it cannot read the text. See the note above, and docs/security.md, which says
  what this scan is: dev mode off is the real boundary, and this is a defence on top. Compiled SQL has a known form, so it goes through check-no-private-member! only."
  [cube-meta sql]
  (let [private      (cube.meta/private-members cube-meta)
        hidden       (cube.meta/private-cubes cube-meta)
        with-columns (cube.meta/private-members cube-meta :in-cube-only? true)]
    (when (or (seq private) (seq hidden))
      (let [{:keys [unreadable]} (lex sql)
            toks      (tokens sql)
            known     (into #{} (map (comp str/lower-case :name)) (:cubes cube-meta))
            read-from (mentioned-tables known toks)
            of-from   (table-aliases-of-from toks)
            aliases   (merge-with into of-from (subquery-aliases known toks))
            private?  (fn [tables text] (first (filter #(contains? (get private %) text) tables)))
            refuse-member! (fn [member table]
                             (refuse! (format (str "%s is a private member of %s in the Cube data model, so it has no values here. Use another column, or make it public in the "
                                                   "model. A Cube in dev mode reads private members, so do not run dev mode in production.")
                                              member table)))]
        ;; The lexer can stop at this point, so the text can hide each table after it.
        (when unreadable
          (refuse-unreadable! unreadable))
        (when (some #(or (hidden %) (seq (get private %))) read-from)
          (when-not (balanced-parens? toks)
            (refuse-unreadable! "its parentheses do not match"))
          (let [first-tok (first toks)]
            (when-not (or (word? first-tok "select") (word? first-tok "with") (= "(" (:text first-tok)))
              (refuse-unreadable! "it does not start with SELECT or WITH"))))
        (doseq [table read-from
                :when (hidden table)]
          (refuse! (format "%s is private in the Cube data model, so it has no values here. A Cube in dev mode reads private cubes, so do not run dev mode in production."
                           table)))
        (doseq [i     (range (count toks))
                :when (select-star? toks i)
                table (star-tables toks of-from i)
                :when (seq (get with-columns table))]
          (refuse! (format "%s has private members in the Cube data model, and a Cube in dev mode gives them for *. List the columns." table)))
        (doseq [i     (range (count toks))
                :let  [{:keys [kind text] :as tok} (get toks i)]
                :when (contains? #{:word :ident} kind)]
          (let [before (get toks (dec i))]
            (cond
              ;; A type in `x::type`.
              (and (= ":" (:text before)) (= ":" (:text (get toks (- i 2))))) nil

              (= "." (:text before))
              (let [prefix (:text (get toks (- i 2)))]
                (when-let [table (private? (get aliases prefix read-from) text)]
                  (refuse-member! text table)))

              (word? before "as")
              (when-let [table (private? read-from text)]
                ;; `a.id AS id` of a table with a public `id` is that column. The check of `a.id` read it.
                (when-not (and (= "." (:text (get toks (- i 2 1))))
                               (= text (:text (get toks (- i 2)))))
                  (refuse! (format (str "%s is a private member of %s in the Cube data model, and Cube reads a filter on a column of a subquery with this name as a filter on the member. "
                                        "Use another name for this column.")
                                   text table))))

              (= "(" (:text (get toks (inc i)))) nil
              (= "." (:text (get toks (inc i)))) nil

              :else
              (when-let [table (private? read-from text)]
                (when (name-token? tok)
                  (refuse-member! text table))))))))))

(defn- fraction-text?
  "True if this number text has a part after the point that is not zero."
  [text]
  (try
    (pos? (.scale (.stripTrailingZeros (BigDecimal. ^String text))))
    (catch NumberFormatException _ false)))

(defn- fraction-at
  "The index after the number at `i`, with a sign, if it has a fraction, or nil."
  [toks params i]
  (let [at  (if (contains? #{"-" "+"} (:text (get toks i))) (inc i) i)
        tok (get toks at)]
    (cond
      (and (:number? tok) (fraction-text? (:text tok))) (inc at)
      (= :param (:kind tok))
      (let [value (nth params (:param tok) nil)]
        (when (and (number? value) (not= (double value) (Math/rint (double value))))
          (inc at))))))

(defn- list-has-fraction?
  "True if the list that opens at `open` has a number with a fraction."
  [toks params open]
  (when-let [close (when (= "(" (:text (get toks open))) (closing-paren toks open))]
    (some #(fraction-at toks params %) (range (inc open) close))))

(defn- number-end
  "The index after the number or the parameter at `i`, with a sign, or nil."
  [toks i]
  (let [at  (if (contains? #{"-" "+"} (:text (get toks i))) (inc i) i)
        tok (get toks at)]
    (when (or (:number? tok) (= :param (:kind tok)))
      (inc at))))

(defn- compares-with-fraction?
  "True if the column at `i` compares with a number that has a fraction: with an operator on either side, IN or BETWEEN."
  [toks params i]
  (let [comparison #{"=" "<>" "!=" "<" "<=" ">" ">="}
        at         (if (word? (get toks (inc i)) "not") (+ i 2) (inc i))
        op         (get toks at)
        start      (if (= "." (:text (get toks (dec i)))) (- i 3) (dec i))
        before     (get toks start)]
    (or (and (word? op "in")
             (list-has-fraction? toks params (inc at)))
        (and (word? op "between")
             (or (fraction-at toks params (inc at))
                 (when-let [low-end (number-end toks (inc at))]
                   (and (word? (get toks low-end) "and")
                        (fraction-at toks params (inc low-end))))))
        (and (= :other (:kind op))
             (contains? comparison (:text op))
             (fraction-at toks params (inc at)))
        (and (= :other (:kind before))
             (contains? comparison (:text before))
             (fraction-at toks params (dec start))))))

(defn exact-fractions
  "Cube sends a number in a filter to ClickHouse as text, and ClickHouse cuts text to the scale of a Decimal column, and it stops on a fraction for an integer column. So `amount = 44.001` reads
  as `amount = 44`. A column in an expression makes Cube write the number as it is, and it compares two doubles, as the compiled form does. This writes that form next to a number dimension
  that compares with a number with a fraction."
  [cube-meta ^String sql params]
  (let [toks    (tokens sql)
        index   (cube-index cube-meta)
        numbers (into #{} (mapcat #(:numbers (get index %))) (tables toks))]
    (if (empty? numbers)
      sql
      (let [ends (for [i     (range (count toks))
                       :let  [tok (get toks i)]
                       :when (and (name-token? tok)
                                  (contains? numbers (:text tok))
                                  (not (word? (get toks (dec i)) "as"))
                                  (not= "(" (:text (get toks (inc i))))
                                  (not= "." (:text (get toks (inc i))))
                                  (compares-with-fraction? toks (or params []) i))]
                   (:end tok))]
        (reduce (fn [^String text end]
                  (str (subs text 0 end) " * CAST('1.0' AS double precision)" (subs text end)))
                sql
                (reverse ends))))))

(defn check-paths!
  "For each query, also a compiled one."
  [cube-meta sql]
  (check-one-path! (cube-index cube-meta) (tokens sql)))

(defn check-native!
  "For the text that a person or an agent wrote. `questions` has the answers that need Cube: :keeps-fractions? a boolean,
  :time-shift? a function of the cube and the full name of a measure, :wrong-bin-reason a function of the cube, the time
  member, the granularity and the full names of the measures, :rolling-reason a function of the full names of a measure and a time
  member and the unit, :value-read a function of a cube that gives `cube.probe/value-read-members`, :joined-member-of-view? a function of a view and a column, and :report-zone? a boolean."
  [cube-meta sql params {:keys [keeps-fractions? time-shift? value-read wrong-bin-reason report-zone?] :as questions}]
  (let [index (cond->> (cube-index cube-meta)
                keeps-fractions? (into {} (map (fn [[k v]] [k (dissoc v :date-only)]))))
        toks  (unwrap (tokens sql))]
    (check-one-path! index toks)
    (check-rolling! index toks params questions)
    (check-between! index toks params)
    (check-subquery-filters! index toks)
    (check-equal-time-in-bucket! index toks params)
    (check-text-case! toks)
    (check-raw-select! index toks)
    (check-total-through-join! index toks (:joined-member-of-view? questions))
    (when value-read
      (check-value-read! index toks params value-read))
    (when report-zone?
      (check-report-zone! index toks params))
    (when-not keeps-fractions?
      (check-strict-bound! index toks params)
      (check-fraction! index toks params))
    (when time-shift?
      (check-time-shift! index toks params time-shift?))
    (when wrong-bin-reason
      (check-granularity! index toks wrong-bin-reason))))

;;; ---------------------------------------------- COUNT on no rows ------------------------------------------------

(defn- located-tokens
  "The tokens of `sql` with their positions in the text, with no comments."
  [^String sql]
  (tokens sql))

(defn- count-call?
  "True if the call that opens at `open` is a count: COUNT(...), or MEASURE() of a measure that counts."
  [toks i count-measures]
  (or (word? (get toks i) "count")
      (and (word? (get toks i) "measure")
           (contains? count-measures (:text (get toks (- (or (closing-paren toks (inc i)) 0) 1)))))))

(defn count-on-no-rows
  "Cube reads a rollup with SUM, so a count over no rows is NULL where SQL has 0. This writes COALESCE around each count of a text with one
  SELECT and no grouping, the same as a compiled query. With more than one SELECT, the scan cannot show that the text has one row."
  [cube-meta ^String sql]
  (let [toks (located-tokens sql)
        selects (count (filter #(word? % "select") toks))]
    (if (or (not= 1 selects)
            (some #(or (word? % "group") (word? % "over") (word? % "union") (word? % "having")) toks))
      sql
      (let [index          (cube-index cube-meta)
            count-measures (into #{} (mapcat #(:count-measures (get index %))) (tables toks))
            calls          (for [i (range (count toks))
                                 :when (and (= "(" (:text (get toks (inc i))))
                                            (count-call? toks i count-measures)
                                            (not (and (= "(" (:text (get toks (dec i))))
                                                      (word? (get toks (- i 2)) "coalesce"))))
                                 :let [close (closing-paren toks (inc i))]
                                 :when close]
                             [(:start (get toks i)) (:end (get toks close))])]
        (reduce (fn [^String text [start end]]
                  (str (subs text 0 start) "COALESCE(" (subs text start end) ", 0)" (subs text end)))
                sql
                (reverse calls))))))
