(ns metabase.driver.cube.probe
  "Questions to Cube that /v1/meta does not answer. /v1/sql tells how Cube writes a query, and it runs nothing. Each answer is
  at most one minute old, so a query adds no request in most cases. A check that gets no answer stops nothing, except where the
  docstring says so."
  (:require
   [clojure.string :as str]
   [metabase.driver.cube.meta :as cube.meta]
   [metabase.util.json :as json])
  (:import
   (java.time DayOfWeek LocalDate LocalDateTime OffsetDateTime ZoneId ZoneOffset)
   (java.time.format DateTimeFormatter DateTimeParseException)
   (java.time.temporal ChronoUnit TemporalAdjusters)))

(set! *warn-on-reflection* true)

(def ^:private ttl-ms 60000)

(defonce ^:private cache (atom {}))

(defn- cached
  [details question compute]
  (let [cache-key [(select-keys details [:cube-rest-url :host :port]) question]
        now       (System/currentTimeMillis)
        {:keys [at value]} (get @cache cache-key)]
    (if (and at (< (- now at) ttl-ms))
      value
      (let [fresh (compute)]
        (swap! cache assoc cache-key {:at now, :value fresh})
        fresh))))

(defn- sql-of
  "The SQL text that Cube writes for a REST query, and the pre-aggregations that it reads."
  [details query]
  (let [{{[text] :sql, :keys [preAggregations rollupMatchResults]} :sql} (cube.meta/rest-get details "/v1/sql" query)]
    {:text (str text), :pre-aggregations preAggregations, :match-results rollupMatchResults}))

(defn- cube-named
  [cube-meta cube-name]
  (first (filter #(= (str/lower-case cube-name) (str/lower-case (:name %))) (:cubes cube-meta))))

(defn- real-time-dimensions
  "The time dimensions of a cube that read a column, not a custom granularity of another dimension."
  [cube]
  (remove :aliasMember (filter #(= "time" (:type %)) (:dimensions cube))))

;;; Date ranges. The ClickHouse dialect of stock Cube writes a bound with parseDateTimeBestEffort, which drops the fraction.

(defn keeps-fractions?
  "False if Cube drops the fraction of a date range for this cube. `cube-name` nil asks for the first cube with a time. With no
  answer this is false, because then the driver does more to keep the number right."
  [details cube-meta cube-name]
  (let [cube (or (some->> cube-name (cube-named cube-meta))
                 (first (filter (comp seq real-time-dimensions) (:cubes cube-meta))))
        ;; Each time dimension of a view is an alias.
        time-dimension (or (first (real-time-dimensions cube))
                           (first (filter #(= "time" (:type %)) (:dimensions cube))))]
    (if-not time-dimension
      true
      (cached details [:keeps-fractions (:name cube)]
              (fn []
                (try
                  (not (str/includes?
                        (:text (sql-of details {:dimensions     [(:name time-dimension)]
                                                :timeDimensions [{:dimension (:name time-dimension)
                                                                  :dateRange ["2025-01-01T00:00:00.001" "2025-01-02T00:00:00.000"]}]
                                                :limit          1}))
                        "parseDateTimeBestEffort"))
                  (catch Exception _ false)))))))

;;; FILTER_PARAMS as a value. A cube can pass a filter of Cube to the database as a value, for example to a parameter of a view:
;;; `v(date_from = ${FILTER_PARAMS.c.t.filter((from, to) => from)})`. With no filter that Cube can read there, Cube writes `1 = 1` in that
;;; place, and the database reads it as a value. As a condition, as in `WHERE ${FILTER_PARAMS.c.t.filter('t')}`, this gives all rows and the
;;; outer filter of Cube still applies, so that form needs nothing.

(def ^:private condition-words
  #"(?i)\b(where|and|or|not|on|having|when|then|else)$")

(defn- value-places
  "The number of `1 = 1` in a place of the text where a value goes."
  [text]
  (let [matcher ^java.util.regex.Matcher (re-matcher #"1 = 1" text)]
    (loop [places 0]
      (if (.find matcher)
        (let [before (str/replace (subs text 0 (.start matcher)) #"[\s(]+$" "")]
          (recur (if (re-find condition-words before) places (inc places))))
        places))))

(defn- first-member-query
  [cube]
  (if-let [measure (first (:measures cube))]
    {:measures [(:name measure)], :limit 1}
    {:dimensions [(:name (first (:dimensions cube)))], :limit 1}))

(defn value-read-members
  "A map from the lower case short name of each dimension that the SQL of this cube reads as a value to :time or :other. Each answer is
  cached, and most cubes need one request. With no answer the map is empty."
  [details cube-meta cube-name]
  (let [cube (cube-named cube-meta cube-name)]
    (if-not cube
      {}
      (cached details [:value-read (:name cube)]
              (fn []
                (try
                  (let [query  (first-member-query cube)
                        base   (value-places (:text (sql-of details query)))
                        places (fn [{member :name, kind :type}]
                                 (value-places
                                  (:text (sql-of details
                                                 (if (= "time" kind)
                                                   (assoc query :timeDimensions [{:dimension member, :dateRange ["2025-01-01" "2025-01-31"]}])
                                                   (assoc query :filters [{:member member, :operator "equals", :values ["1"]}]))))))]
                    (if (zero? base)
                      {}
                      (into {}
                            (comp (filter #(< (places %) base))
                                  (map (fn [{member :name, kind :type}]
                                         [(str/lower-case (last (str/split member #"\."))) (if (= "time" kind) :time :other)])))
                            (:dimensions cube))))
                  (catch Exception _ {})))))))

;;; Time shifts. /v1/meta shows a measure with a time shift as a plain measure.

(defn shift-candidate?
  "True if a measure can have a time shift: a number that reads another member."
  [measure]
  (and (= "number" (:aggType measure))
       (str/includes? (str (:sql measure)) "${")))

(def ^:private shifted-time-pattern
  #"(?i)\baddDate\s*\(|\bsubDate\s*\(|\bdate_add\s*\(|\bdate_sub\s*\(|\binterval\b")

(defn- source-text
  "The FROM and WHERE of the first SELECT of a text from Cube, where a shift moves the time."
  [text]
  (some-> (re-find #"(?is)\bFROM\b(.*?)\bGROUP BY\b" text) second (str/replace #"\s+" " ") str/trim))

(defn- shifted-on?
  "True if Cube moves the time for this measure. A calendar shift joins on another column of the calendar, with no interval. Then
  the source differs from the source of the measure that it reads. A measure that reads a measure of another cube has a join, so
  its source differs with no shift: only an interval shows a shift there."
  [details measure-name base time-dimension]
  (let [query (fn [member]
                {:measures       [member]
                 :timeDimensions [{:dimension time-dimension, :granularity "day", :dateRange ["2025-01-01" "2025-01-31"]}]
                 :limit          1})
        text  (:text (sql-of details (query measure-name)))]
    (or (boolean (re-find shifted-time-pattern text))
        (and (some? base)
             (= (first (str/split measure-name #"\.")) (first (str/split base #"\.")))
             (not= (source-text text) (source-text (:text (sql-of details (query base)))))))))

(defn- member-of
  [cube-meta full-name]
  (let [[cube-name] (str/split full-name #"\.")]
    (when-let [cube (cube-named cube-meta cube-name)]
      {:cube cube
       :measure (first (filter #(= full-name (:name %)) (:measures cube)))
       :dimension (first (filter #(= full-name (:name %)) (:dimensions cube)))})))

(declare root-member)

(defn time-shift-candidate?
  "True if the measure with this full name can have a time shift. A measure of a view is an alias of the measure of its cube."
  [cube-meta full-name]
  (boolean (some-> (:measure (member-of cube-meta (root-member cube-meta full-name :measure))) shift-candidate?)))

(defn- root-member
  "The member that an alias of a view stands for. A measure stops at the last one that reads another member, so the alias of a time
  shift is the shift and not the measure that it reads."
  [cube-meta full-name kind]
  (let [member (kind (member-of cube-meta full-name))
        source (:aliasMember member)
        next-member (when source (kind (member-of cube-meta source)))]
    (if (and next-member (or (= kind :dimension) (shift-candidate? next-member)))
      (recur cube-meta source kind)
      full-name)))

(defn- time-dimensions-to-try
  "The time dimensions of this cube and of the cubes that it joins, as a calendar is, by the name in the cube that owns them."
  [cube-meta cube]
  (let [joined (keep #(cube-named cube-meta (:name %)) (:joins cube))]
    (into []
          (comp (mapcat :dimensions)
                (filter #(= "time" (:type %)))
                (map #(root-member cube-meta (:name %) :dimension))
                (distinct)
                (take 10))
          (cons cube joined))))

(defn time-shift?
  "True if this measure of this cube has a time shift. With no answer this is false."
  [details cube-meta cube-name measure-name]
  (let [cube (cube-named cube-meta cube-name)]
    (boolean
     (when (time-shift-candidate? cube-meta measure-name)
       (cached details [:time-shift measure-name]
               (fn []
                 (let [root (root-member cube-meta measure-name :measure)
                       base (:aliasMember (:measure (member-of cube-meta root)))]
                   (boolean
                    (some (fn [time-dimension]
                            (try
                              (shifted-on? details root base time-dimension)
                              (catch Exception _ false)))
                          (time-dimensions-to-try cube-meta cube))))))))))

;;; Rolling windows. /v1/meta shows a rolling window as `cumulative: true`, with no size. The join of the window in /v1/sql shows it.

(def ^:private unit-days
  "The usual length of each unit in days. A window of 30 days is as long as a month, because a person who writes it means this."
  {"minute" 1/1440, "hour" 1/24, "day" 1, "week" 7, "month" 30, "quarter" 90, "year" 365})

(defn- join-of-window
  [text]
  (some-> (re-find #"(?is)\bJOIN\b.*?rolling_source.*?\bON\b(.*?)\bGROUP BY\b" text) second))

(defn- window-days
  "The length of the window in the text of its join: {:days n, :kind :size or :to-date}, or nil for an unbounded window or no answer."
  [join]
  (when join
    (let [[_ amount size-unit]   (re-find #"(?i)INTERVAL\s+'?(\d+)\s+(minute|hour|day|week|month|quarter|year)" join)
          [_ start-of]           (re-find #"(?i)toStartOf(Minute|Hour|Day|Week|Month|Quarter|Year)\b" join)
          [_ trunc]              (re-find #"(?i)date_trunc\s*\(\s*'(minute|hour|day|week|month|quarter|year)'" join)]
      (cond
        amount (let [days (unit-days (str/lower-case size-unit))]
                 {:days (* (Long/parseLong amount) days), :text (str amount " " (str/lower-case size-unit)), :kind :size})
        (or start-of trunc) (let [unit (str/lower-case (or start-of trunc))]
                              {:days (unit-days unit), :text (str unit " to date"), :kind :to-date})))))

(defn- rolling-sql
  [details measure time-dimension unit]
  (cached details [:rolling-sql measure time-dimension unit]
          (fn []
            (try
              (sql-of details {:measures       [measure]
                               :timeDimensions [{:dimension time-dimension, :granularity (name unit)
                                                 :dateRange ["2025-01-01" "2025-06-30"]}]
                               :limit          1})
              (catch Exception _ nil)))))

(def ^:private calendar-units
  "The units with no fixed length."
  #{"month" "quarter" "year"})

(def ^:private window-after-bucket-end
  "A window that starts at the end of a bucket and has a length in months, as `leading: 1 month, offset: end` has."
  #"(?i)add\w*\s*\(\s*\S*date_to\S*\s*,\s*INTERVAL\s+'?\d+\s+(?:month|quarter|year)|date_to\S*\s*\+\s*interval\s+'\d+\s+(?:month|quarter|year)")

(defn- legacy-stock-planner?
  "The legacy planner names the series after the time dimension. Stock Cube writes it with two arrayJoin calls in one SELECT, and these give a
  cross product of the bounds. The dialect writes arrayZip."
  [text]
  (and (boolean (re-find #"\.[A-Za-z0-9_]+_series\b" text))
       (str/includes? text "arrayJoin")
       (not (str/includes? text "arrayZip"))))

(defn rolling-window-reason
  "A text if this rolling window by this bucket gives a wrong number, or nil. With no answer from Cube this stops nothing."
  [details measure time-dimension unit]
  (when-let [{:keys [text pre-aggregations]} (and measure time-dimension (rolling-sql details measure time-dimension unit))]
    (let [short-name (last (str/split measure #"\."))
          join       (join-of-window text)
          calendar?  (calendar-units (name unit))]
      (if (legacy-stock-planner? text)
        (format (str "%s is a rolling window measure. With CUBEJS_TESSERACT_SQL_PLANNER=false, stock Cube gives each bucket one wrong number. "
                     "Remove this setting, or install the ClickHouse dialect, see cube-clickhouse/README.md.")
                short-name)
        (let [{:keys [days kind] window-text :text} (window-days join)]
          (cond
            (and days (< days (get unit-days (name unit) 0)))
            (format (str "%s is a rolling window measure with a window of %s, which is shorter than a %s. Cube does not show one number "
                         "that this can mean for a %s: it can add the windows of the whole bucket. Group by a bucket that is not longer "
                         "than the window.")
                    short-name window-text (name unit) (name unit))

            (and calendar? join (re-find window-after-bucket-end join))
            (format (str "%s is a rolling window measure with a window after the end of a bucket. By a %s, Cube reads whole months for each "
                         "bucket except the last, and it cuts the last one at the end of its window, so a number would be wrong. Group by "
                         "a day, or use a window with `offset: start`.")
                    short-name (name unit))

            (and calendar? (= :size kind) (seq pre-aggregations) (not (calendar-units (last (str/split window-text #" ")))))
            (format (str "%s is a rolling window measure with a window of %s, and a rollup serves it. By a %s, Cube Store reads the wrong days "
                         "for a month that has another length than the month before it, so the number would be wrong. Group by a day or a "
                         "week, use a window in months, or remove this measure from the rollup.")
                    short-name window-text (name unit))))))))

;;; Rolling windows in a zone. The dialect reads the bounds of a bucket in the zone of the query, and stock Cube reads them in UTC.

(defn rolling-zone-reason
  "A text if Cube does not read the bounds of a rolling window in `zone`, or nil. With no answer this stops the query: the number is wrong on stock Cube, so a query
  needs a sign that it is right. This asks for one hour by the minute, because a rollup does not serve that, and a query with a rollup can also read the database."
  [details measure time-dimension zone]
  (let [{:keys [text]} (when (and measure time-dimension)
                         (cached details [:rolling-zone measure time-dimension zone]
                                 (fn []
                                   (try
                                     (sql-of details {:measures       [measure]
                                                      :timeDimensions [{:dimension time-dimension, :granularity "minute"
                                                                        :dateRange ["2025-01-01T00:00:00" "2025-01-01T00:59:59"]}]
                                                      :timezone       zone
                                                      :limit          1})
                                     (catch Exception _ nil)))))]
    (when-not (and text (or (str/includes? text (str "', 3, '" zone "') f,"))
                            (str/includes? text (str "(dates.1, 3, '" zone "')"))))
      (format (str "%s is a rolling window measure. With a report time zone that is not UTC, stock Cube reads the bounds of each window in UTC and adds the wrong months "
                   "together, so the number would be wrong. Install the ClickHouse dialect, see cube-clickhouse/README.md, or remove the report time zone.")
              (last (str/split (str measure) #"\."))))))

(defn time-shift-zone-reason
  "A text if Cube adds the interval of this time shift to a column in UTC under a report time zone, or nil. Stock Cube adds 24 hours next to a clock change of `zone`, so a row moves to
  another day. The dialect adds the day in the zone. With no answer this stops nothing."
  [details measure-name time-dimension zone]
  (when time-dimension
    (let [{:keys [text]} (cached details [:time-shift-zone measure-name time-dimension zone]
                                 (fn []
                                   (try
                                     (sql-of details {:measures       [measure-name]
                                                      :timeDimensions [{:dimension time-dimension, :granularity "day", :dateRange ["2025-01-01" "2025-01-31"]}]
                                                      :timezone       zone
                                                      :limit          1})
                                     (catch Exception _ nil))))]
      (when (and text (re-find #"(?i)\b(?:add|sub)Date\(\s*(?!toDateTime64)" text))
        (format (str "%s has a time shift. With a report time zone that is not UTC, stock Cube adds the interval in UTC, so next to a clock change a row moves to another day, and the "
                     "number would be wrong. Install the ClickHouse dialect, see cube-clickhouse/README.md, or remove the report time zone.")
                (last (str/split (str measure-name) #"\.")))))))

;;; Date columns.

(defn dates-stay-in-zone?
  "True if Cube reads a Date at midnight of the report time zone, as the ClickHouse dialect does with `toDateTime64(x, 3, zone)`. Stock Cube writes
  `toTimeZone(toDateTime64(x, n), zone)`, which reads a Date as midnight UTC, so west of UTC the row moves to the day before. Stock Cube v1.8.19 keeps the
  fraction of a bound, so [[keeps-fractions?]] does not tell the two apart. With no answer this is false, because then the driver does more to keep the number right."
  [details cube-meta]
  (let [cube           (first (filter (comp seq real-time-dimensions) (:cubes cube-meta)))
        time-dimension (first (real-time-dimensions cube))]
    (if-not time-dimension
      true
      (cached details [:dates-stay-in-zone (:name cube)]
              (fn []
                (try
                  ;; A rollup does not serve a bucket of a minute, so Cube writes the SQL of the column.
                  (let [text (:text (sql-of details {:timeDimensions [{:dimension (:name time-dimension), :granularity "minute"
                                                                       :dateRange ["2025-01-01T00:00:00" "2025-01-01T00:59:59"]}]
                                                     :timezone       "America/New_York"
                                                     :limit          1}))]
                    (and (str/includes? text "'America/New_York'")
                         (not (re-find #"toTimeZone\(\s*toDateTime64\(" text))))
                  (catch Exception _ false)))))))

(defn date-only?
  "True if the data model marks the dimension with this full name with `meta: { date_only: true }`. Cube does not show the type of a column."
  [cube-meta full-name]
  (boolean (:date_only (:meta (:dimension (member-of cube-meta (root-member cube-meta full-name :dimension)))))))

;;; Custom granularities. Cube Store rounds toward zero, so a time before the origin of a granularity in months gets the wrong
;;; bin. No released Cube has a fix. See docs/cube-audit.md#gran.

(defn granularity-of-member
  "[cube member granularity] if the dimension with this full name reads a custom granularity, as `sql: {created_at.fiscal_quarter}`
  does, or nil. The cube and the member are those that have the granularity."
  [cube-meta full-name]
  (let [{:keys [dimension]} (member-of cube-meta full-name)]
    (when-let [[_ member granularity] (re-find #"^`?\$\{(?:[^.}]+\.)?([^.}]+)\.([^.}]+)\}`?$" (str (:sql dimension)))]
      (let [source (str/split (str (:aliasMember dimension)) #"\.")]
        (when (= 2 (count source))
          [(first source) member granularity])))))

(defn rollup-on-time?
  "True if a rollup of the cube of this time member has it as its time dimension. A view has the rollups of its cube."
  [cube-meta cube-name member-name]
  (when (and cube-name member-name)
    (let [full-name (str cube-name "." member-name)
          {:keys [cube dimension]} (member-of cube-meta full-name)
          [owner source] (if-let [alias-name (:aliasMember dimension)]
                           (str/split alias-name #"\.")
                           [(:name cube) member-name])
          owner-cube (when owner (cube-named cube-meta owner))]
      (boolean (some #(= source (:timeDimensionReference %)) (:preAggregations owner-cube))))))

(defn- month-interval?
  [{:keys [interval]}]
  (boolean (re-find #"(?i)month|quarter|year" (str interval))))

(defn- parse-instant
  ^LocalDateTime [text]
  (let [text (str/replace (str/trim (str text)) " " "T")]
    (try
      (cond
        (re-matches #"\d{4}-\d{2}-\d{2}" text) (.atStartOfDay (LocalDate/parse text))
        (re-find #"(Z|[+-]\d{2}:?\d{2})$" text) (.toLocalDateTime (.withOffsetSameInstant (OffsetDateTime/parse text) ZoneOffset/UTC))
        :else (LocalDateTime/parse text))
      (catch DateTimeParseException _ nil))))

(defn- first-time
  "The earliest time of a dimension, or nil if Cube gives no answer."
  ^LocalDateTime [details time-dimension]
  (cached details [:first-time time-dimension]
          (fn []
            (try
              (let [{[row] :data} (cube.meta/rest-load details {:dimensions [time-dimension]
                                                                :filters    [{:member time-dimension, :operator "set"}]
                                                                :order      {time-dimension "asc"}
                                                                :limit      1})]
                (some-> (get row (keyword time-dimension)) parse-instant))
              (catch Exception _ nil)))))

(defn- rollup-serves?
  [details measures time-dimension granularity-name]
  (cached details [:rollup-serves (sort measures) time-dimension granularity-name]
          (fn []
            (try
              (seq (:pre-aggregations
                    (sql-of details {:measures       (vec measures)
                                     :timeDimensions [{:dimension time-dimension, :granularity granularity-name}]
                                     :limit          1})))
              (catch Exception _ nil)))))

(defn wrong-bin-reason
  "A text if Cube Store can put a row of this query in the wrong bin of a custom granularity, or nil. `measures` are full names.
  The origin of a granularity with an offset is in the current year, so older rows are before it."
  [details cube-meta cube-name time-member granularity-name measures]
  (let [time-dimension (str cube-name "." time-member)
        granularity    (first (filter #(= granularity-name (:name %))
                                      (cube.meta/time-granularities cube-meta cube-name time-member)))]
    (when (and granularity (month-interval? granularity) (seq measures)
               (rollup-serves? details measures time-dimension granularity-name))
      (let [origin (some-> (:origin granularity) parse-instant)
            start  (when origin (first-time details time-dimension))]
        (when-not (and origin start (not (.isBefore ^LocalDateTime start origin)))
          (format (str "The granularity %s of %s starts inside the data, and a rollup serves this measure. Cube Store puts a time "
                       "before the origin in the wrong bin, so the number would be wrong. Give the granularity an origin before "
                       "all rows, in place of an offset, or use a measure that the rollup does not serve.")
                  granularity-name time-dimension))))))

;;; Measures of type time. Cube makes each time dimension local to the zone of the query, and it leaves a measure of type time as the database prints it.

(defn time-measure-in-zone?
  "True if the SQL that Cube writes for this measure reads the zone of the query, as the ClickHouse dialect does for each measure of type time, so its
  value is a time in that zone. With no answer this is false."
  [details measure zone]
  (cached details [:time-measure-in-zone measure zone]
          (fn []
            (try
              (str/includes? (:text (sql-of details {:measures [measure], :timezone zone, :limit 1})) (str "'" zone "'"))
              (catch Exception _ false)))))

;;; Rollups that read a part of the rows. Cube answers from a rollup with no error, in these cases:
;;;
;;; - The query asks for rows out of the build range of the rollup.
;;; - The rollups of a rollup_lambda have a gap: Cube reads each one up to the start of its last partition.
;;; - The rollup has allow_non_strict_date_range_match, and a bound of the query is inside a bucket.
;;;
;;; /v1/meta names these rollups, so a query on another cube adds no request. For a query on such a cube, EXPLAIN gives the request of Cube, /v1/sql the
;;; rollups that it reads, and /v1/load the partitions. A filter that the rollup cannot serve makes Cube read the source, which has all rows. See [[wrong-rollup]].

(defn- time-of-request
  "A bound of a request as a local time, or nil. Cube reads a bound in the zone of the query, also with a `Z`."
  ^LocalDateTime [text]
  (try
    (let [text (str/replace (str text) #"Z$" "")]
      (if (re-matches #"\d{4}-\d{2}-\d{2}" text)
        (.atStartOfDay (LocalDate/parse text))
        (LocalDateTime/parse text)))
    (catch DateTimeParseException _ nil)))

(defn- end-of-day
  "`2025-03-31` as a last bound is the end of that day."
  ^LocalDateTime [text]
  (if (re-matches #"\d{4}-\d{2}-\d{2}" (str text))
    (.plusNanos (.atStartOfDay (.plusDays (LocalDate/parse (str text)) 1)) -1000000)
    (time-of-request text)))

(defn- filter-bounds
  "The bounds in one filter of a request, as [lower upper]."
  [{:keys [operator values]}]
  (case operator
    "inDateRange"    [(time-of-request (first values)) (end-of-day (last values))]
    ("afterDate" "afterOrOnDate")   [(time-of-request (first values)) nil]
    ("beforeDate" "beforeOrOnDate") [nil (end-of-day (first values))]
    nil))

(defn- request-bounds
  "The lower and the upper bound of the time in a request, as [lower upper]. A bound that the request does not have is nil."
  [{:keys [timeDimensions filters]}]
  (let [ranges (concat (keep (fn [{[lower upper] :dateRange}]
                               (when lower [(time-of-request lower) (end-of-day (or upper lower))]))
                             timeDimensions)
                       (keep filter-bounds filters))]
    [(some first ranges) (some second ranges)]))

(defn- plus-unit
  "`t` plus one `unit` of Cube."
  ^LocalDateTime [^LocalDateTime t unit]
  (case unit
    "second"  (.plusSeconds t 1)
    "minute"  (.plusMinutes t 1)
    "hour"    (.plusHours t 1)
    "day"     (.plusDays t 1)
    "week"    (.plusWeeks t 1)
    "month"   (.plusMonths t 1)
    "quarter" (.plusMonths t 3)
    "year"    (.plusYears t 1)
    nil))

(defn- start-of-unit
  ^LocalDateTime [^LocalDateTime t unit]
  (let [day (.atStartOfDay (.toLocalDate t))]
    (case unit
      "second"  (.truncatedTo t ChronoUnit/SECONDS)
      "minute"  (.truncatedTo t ChronoUnit/MINUTES)
      "hour"    (.truncatedTo t ChronoUnit/HOURS)
      "day"     day
      "week"    (.with day (TemporalAdjusters/previousOrSame DayOfWeek/MONDAY))
      "month"   (.withDayOfMonth day 1)
      "quarter" (.withMonth (.withDayOfMonth day 1) (inc (* 3 (quot (dec (.getMonthValue t)) 3))))
      "year"    (.withDayOfYear day 1)
      nil)))

(defn- fits-buckets?
  "True if the bounds of a query are the start and the end of a bucket of this unit, so a rollup of these buckets has the rows of the query."
  [unit [^LocalDateTime lower ^LocalDateTime upper]]
  (and (or (nil? lower) (= lower (start-of-unit lower unit)))
       (or (nil? upper)
           (let [next-time (.plusNanos upper 1000000)]
             (= next-time (start-of-unit next-time unit))))))

(def ^:private ^DateTimeFormatter partition-format
  (DateTimeFormatter/ofPattern "yyyyMMddHHmmss"))

(defn- partition-start
  "The start of a partition from the end of its table name, as in `20250301`, or `2025030106` for an hour."
  ^LocalDateTime [digits]
  (try
    (LocalDateTime/parse (subs (str digits "0000000000") 0 14) partition-format)
    (catch DateTimeParseException _ nil)))

(defn- partitions-of
  "The periods of the partitions that Cube reads for a rollup, sorted, from a value of `usedPreAggregations`, as [start end]."
  [rollup-table unit target-table]
  (let [pattern (re-pattern (str (java.util.regex.Pattern/quote rollup-table) "(\\d{8,14})_"))]
    (sort-by first
             (keep (fn [[_ digits]]
                     (when-let [start (partition-start digits)]
                       [start (plus-unit start unit)]))
                   (re-seq pattern (str target-table))))))

(defn- first-gap
  "The first time in `[lower upper]` that `periods` do not hold, or nil. `periods` are sorted. An end is the first time after a period."
  [periods ^LocalDateTime lower ^LocalDateTime upper]
  (loop [covered-to lower, periods periods]
    (cond
      (.isAfter covered-to upper)                       nil
      (empty? periods)                                  covered-to
      (.isAfter ^LocalDateTime (ffirst periods) covered-to) covered-to
      :else (let [^LocalDateTime end (second (first periods))]
              (recur (if (.isAfter end covered-to) end covered-to) (rest periods))))))

(defn- extent
  "The first and the last time of a time dimension in the source, in the zone of the query, or nil."
  [details cube-name member zone]
  (cached details [:extent cube-name member zone]
          (fn []
            (try
              (let [[[lower upper]] (cube.meta/rest-sql details
                                                        (format "SELECT MIN(%s), MAX(%s) FROM %s" member member cube-name)
                                                        zone)]
                (when (and lower upper)
                  [(time-of-request lower) (time-of-request upper)]))
              (catch Exception _ nil)))))

(defn- range-bound
  "What the SQL of one end of a build range says: a local time in `zone`, :open if the data or the clock sets it, or :unknown if only the database can tell."
  [[sql] ^String zone]
  (let [sql (str/trim (str sql))]
    (cond
      ;; With no build range, Cube reads the first and the last time of the data.
      (re-find #"(?i)^select\s+(\w+\()?\s*(min|max)\(.*\bfrom\b" sql) :open
      (re-matches #"(?i)select\s+(now\(\)|current_timestamp(\(\))?)" sql) :open
      :else
      (if-let [[_ text _ source-zone] (re-matches #"(?i)select\s+\w*\(?\s*'(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})'\s*(,\s*'([^']+)')?\s*\)?" sql)]
        (try
          (let [t (LocalDateTime/parse (str/replace text " " "T"))]
            (.toLocalDateTime (.withZoneSameInstant (.atZone t (ZoneId/of (or source-zone "UTC"))) (ZoneId/of zone))))
          (catch Exception _ :unknown))
        :unknown))))

(defn- clipped-periods
  "The periods of a rollup that hold rows: a partition at the end of a build range holds rows only up to that end. An end that only the database can tell gives the first or
  the last partition of the rollup no credit. `periods-of-all` gives the partitions of the rollup with no date filter, on such an end only."
  [{[start-query end-query] :preAggregationStartEndQueries} zone periods periods-of-all]
  (let [start  (range-bound start-query zone)
        end    (range-bound end-query zone)
        all    (when (or (= :unknown start) (= :unknown end)) (periods-of-all))
        ^LocalDateTime from (cond
                              (instance? LocalDateTime start) start
                              (= :unknown start) (second (first all)))
        ^LocalDateTime to   (cond
                              ;; A build range ends at a whole second, and it holds that second.
                              (instance? LocalDateTime end) (.plusSeconds ^LocalDateTime end 1)
                              (= :unknown end) (first (last all)))]
    (keep (fn [[^LocalDateTime period-start ^LocalDateTime period-end]]
            (let [period-start (if (and from (.isBefore period-start from)) from period-start)
                  period-end   (if (and to (.isAfter period-end to)) to period-end)]
              (when (.isBefore period-start period-end)
                [period-start period-end])))
          periods)))

(defn- without-time-bounds
  "A request with no bound on a time."
  [request]
  (-> request
      (update :timeDimensions (partial mapv #(dissoc % :dateRange)))
      (update :filters (partial filterv (complement filter-bounds)))))

(defn- period-of-build-range
  "The times of a rollup from its build range, as [start end], or nil if only the database can tell one of its ends. With dev mode off, /v1/load does not name the partitions that Cube reads. Cube builds a partition that a
  query needs, so a rollup holds each row of its build range. An end that the data or the clock sets holds the bound of the query."
  [{[start-query end-query] :preAggregationStartEndQueries} zone ^LocalDateTime lower ^LocalDateTime upper]
  (let [start (range-bound start-query zone)
        end   (range-bound end-query zone)]
    (when-not (or (= :unknown start) (= :unknown end))
      [(if (instance? LocalDateTime start) start lower)
       ;; A build range ends at a whole second, and it holds that second.
       (if (instance? LocalDateTime end) (.plusSeconds ^LocalDateTime end 1) (.plusSeconds upper 1))])))

(defn- used-partition-gap
  "A text if the rollups that a request reads do not hold each row of the bounds of the request, or nil. A bound that the request does not have is the
  first or the last time of the source. With no answer about these times, the rollups may not hold the rows."
  [details cube-meta request zone pre-aggregations bounds]
  (let [partitioned (filter :partitionGranularity pre-aggregations)
        ;; The source holds the rows after the last rollup of such a lambda.
        open-end?   (some :unionWithSourceData pre-aggregations)]
    (when (seq partitioned)
      (let [targets       (fn [query]
                            (map :targetTableName (vals (:usedPreAggregations (cube.meta/rest-load details (assoc query :timezone zone, :limit 1))))))
            periods-in    (fn [{:keys [tableName partitionGranularity]} tables]
                            (sort-by first
                                     (mapcat #(partitions-of (last (str/split tableName #"\.")) partitionGranularity %) tables)))
            used          (targets request)
            [lower upper] bounds
            id            (:preAggregationId (first partitioned))
            [cube-name rollup-name] (str/split (str id) #"\.")
            entry         (cube.meta/pre-aggregation-named cube-meta id)
            whole         (when-not (and lower upper)
                            (extent details cube-name (:timeDimensionReference entry) zone))
            lower         (or lower (first whole))
            upper         (or upper (second whole))
            periods       (if (and (seq used) (every? nil? used))
                            (when (and lower upper)
                              (sort-by first (keep #(period-of-build-range % zone lower upper) partitioned)))
                            (sort-by first
                                     (mapcat (fn [rollup]
                                               (clipped-periods rollup zone (periods-in rollup used)
                                                                #(periods-in rollup (targets (without-time-bounds request)))))
                                             partitioned)))]
        (cond
          (empty? used)
          nil

          (not (and lower upper))
          (format "Cube gives no first or last time for the rollup %s." rollup-name)

          (empty? periods)
          (format "The rollup %s of %s has no rows at %s." rollup-name cube-name lower)

          :else
          (when-let [gap (first-gap periods lower (if open-end?
                                                    ;; An end is the first time after a period. The source holds that time.
                                                    (let [^LocalDateTime last-held (.minusNanos ^LocalDateTime (second (last periods)) 1000000)]
                                                      (if (.isAfter last-held upper) upper last-held))
                                                    upper))]
            (format "The rollup %s of %s has no rows at %s." rollup-name cube-name gap)))))))

(defn- non-strict-reason
  "A text if a rollup of this request allows a range that does not fit its buckets, and the bounds of the request do not fit them, or nil."
  [pre-aggregations match-results bounds]
  (first (for [{:keys [preAggregationId granularity]} pre-aggregations
               :let  [{{:keys [allowNonStrictDateRangeMatch]} :references} (first (filter #(= preAggregationId (:name %)) match-results))]
               :when (and allowNonStrictDateRangeMatch granularity (not (fits-buckets? granularity bounds)))]
           (format "The rollup %s has allow_non_strict_date_range_match, and the range of the query does not fit its buckets of one %s."
                   preAggregationId granularity))))

(defn- lambda-zone-reason
  "A text if a rollup_lambda of this request joins a rollup to its source under a report time zone. Stock Cube ends the rollup and starts the source at times that do not meet
  in such a zone, so it reads some rows twice or not at all, with no error: `lambda_sales` by month in New York gives 319894, and the truth is 319790. Cube with the dialect
  can stop such a query on ClickHouse. The source has all rows, in each zone."
  [pre-aggregations ^String zone]
  (when (and (some :unionWithSourceData pre-aggregations)
             (let [rules (.getRules (ZoneId/of zone))]
               (not (and (.isFixedOffset rules) (zero? (.getTotalSeconds (.getOffset rules java.time.Instant/EPOCH)))))))
    (format "The rollup_lambda %s joins a rollup to its source, and under the report time zone %s these two parts do not meet, so Cube can read a row twice or not at all."
            (:preAggregationId (first pre-aggregations)) zone)))

(defn- requests-of-plan
  "The requests of Cube in the rows of EXPLAIN."
  [rows]
  (let [plan (or (some (fn [[kind text]] (when (= "physical_plan" kind) text)) rows) "")]
    (into [] (keep #(try (json/decode+kw %) (catch Exception _ nil))) (re-seq #"(?m)^\{.*\}$" plan))))

(defn- wrapped-rollup-reason
  "A text if the SQL of a plan with no request reads a rollup with a build range, a gap or a loose range. The driver cannot see what it reads."
  [cube-meta rows]
  (let [plan (or (some (fn [[kind text]] (when (= "physical_plan" kind) text)) rows) "")]
    (first (for [cube  (:cubes cube-meta)
                 entry (:preAggregations cube)
                 :let  [table (str (:name cube) "_" (:name entry))]
                 :when (and (cube.meta/rollup-can-read-other-rows? entry)
                            (re-find (re-pattern (str "\\b" (java.util.regex.Pattern/quote table) "\\b")) plan))]
             (format "The query reads the rollup %s, and Cube does not tell which range it reads." table)))))

(defn wrong-rollup
  "If Cube would read this SQL from a rollup that does not hold each row of the query, a map with `:reason`, a text, and `:rollups`, the entries of /v1/sql. Else nil.
  This asks Cube only if the SQL names a table with such a rollup. A check that gets no answer stops nothing."
  [details cube-meta sql zone]
  (let [tables (cube.meta/tables-with-partial-rollups cube-meta)
        words  (set (map str/lower-case (re-seq #"\w+" (str sql))))]
    (when (some tables words)
      (cached details [:wrong-rollup sql zone]
              (fn []
                (try
                  (let [rows     (cube.meta/rest-sql details (str "EXPLAIN " sql) zone)
                        requests (requests-of-plan rows)]
                    (if (empty? requests)
                      (when-let [reason (wrapped-rollup-reason cube-meta rows)]
                        {:reason reason, :rollups []})
                      (first (for [request requests
                                   :let  [{:keys [pre-aggregations match-results]} (sql-of details (assoc request :timezone zone, :limit 1))
                                          bounds (request-bounds request)
                                          reason (when (seq pre-aggregations)
                                                   (or (lambda-zone-reason pre-aggregations zone)
                                                       (non-strict-reason pre-aggregations match-results bounds)
                                                       (used-partition-gap details cube-meta request zone pre-aggregations bounds)))]
                                   :when reason]
                               {:reason reason, :rollups pre-aggregations, :match-results match-results}))))
                  (catch Exception _ nil)))))))

(defn- rollup-members
  "The full names of the dimensions that the rollups of [[wrong-rollup]] hold."
  [{:keys [rollups match-results]}]
  (let [ids (set (map :preAggregationId rollups))]
    (into #{} (comp (filter #(ids (:name %))) (mapcat #(get-in % [:references :dimensions]))) match-results)))

(defn column-past-rollup
  "A dimension of this table that the rollups of [[wrong-rollup]] do not hold, so a filter on it makes Cube read the source, or nil. It reads the same cube as the
  rollup, so the filter adds no join. A view names the member of the cube in `aliasMember`."
  [cube-meta table-name wrong]
  (let [held      (rollup-members wrong)
        cube      (cube-named cube-meta table-name)
        rollup-of (some-> (or (first (map :preAggregationId (:rollups wrong))) "") (str/split #"\.") first)
        same-cube (fn [{:keys [aliasMember], member :name}]
                    (or (str/blank? rollup-of)
                        (= rollup-of (first (str/split (str (or aliasMember member)) #"\.")))))
        usable    (fn [{:keys [aliasMember], member :name, kind :type, :as dimension}]
                    (and (not (false? (:public dimension)))
                         (not (false? (:isVisible dimension)))
                         (#{"number" "string"} kind)
                         (or (:sql dimension) (:case dimension) aliasMember)
                         (same-cube dimension)
                         (not (held (or aliasMember member)))))
        options   (filter usable (:dimensions cube))]
    (some-> (or (first (filter :primaryKey options))
                (first options)
                ;; A rollup holds a time as its time dimension. A filter on it that is not a date range also makes Cube read the source.
                (first (filter #(and (not (false? (:public %)))
                                     (not (false? (:isVisible %)))
                                     (= "time" (:type %))
                                     (same-cube %))
                               (:dimensions cube))))
            :name
            (str/split #"\.")
            last)))

(def ^:private cannot-plan-texts
  ["Can't detect Cube query" "was used with the aggregate function" "duplicate qualified field name"])

(defn planning-error
  "The text of the error if Cube cannot plan this SQL in a way that another form of the query can avoid, or nil. The answer is at most one minute old. With no answer this is nil."
  [details sql zone]
  (cached details [:planning-error sql zone]
          (fn []
            (try
              (cube.meta/rest-sql details (str "EXPLAIN " sql) zone)
              nil
              (catch Exception e
                (let [text (str (ex-message e))]
                  (when (some #(str/includes? text %) cannot-plan-texts)
                    text)))))))

;;; A count measure counts the primary key. Cube writes `count(pk)`, so a row with a NULL key does not count. The primary key can be private, so the driver compares the
;;; measure with COUNT(*) on the rows.

(defn null-key-rows?
  "True if a measure of type count on this cube counts less than its rows. The answer is at most one minute old. With no answer this is false."
  [details table-name measure-name]
  (cached details [:null-key-rows table-name measure-name]
          (fn []
            (try
              (let [[[rows counted]] (cube.meta/rest-sql details
                                                         (format "SELECT COUNT(*), MEASURE(%s) FROM %s" measure-name table-name)
                                                         "UTC")]
                (and (some? rows) (some? counted) (not= (bigdec rows) (bigdec counted))))
              (catch Exception _ false)))))

;;; A row of a model and a group of Cube. See group-raw-stage in metabase.driver.cube.

(defn same-row-count?
  "False if these two SQL texts, which each give one count, give two counts. With no answer this is true."
  [details rows-sql groups-sql]
  (cached details [:same-row-count rows-sql groups-sql]
          (fn []
            (try
              (let [count-of (fn [sql] (ffirst (cube.meta/rest-sql details sql "UTC")))
                    rows     (count-of rows-sql)
                    groups   (count-of groups-sql)]
                (or (nil? rows) (nil? groups) (= (bigdec rows) (bigdec groups))))
              (catch Exception _ true)))))

;;; The planner. With CUBEJS_TESSERACT_SQL_PLANNER=false, Cube plans with its legacy planner, which drops the default_filters of a view: a count of paid_sales gives 5000 where
;;; the filter keeps 2000. /v1/meta does not list default_filters, so the driver cannot tell which views have them. The two planners write SQL in two forms: Tesseract
;;; writes `SELECT x` and GROUP BY a position, and the legacy planner writes `SELECT` on its own line, or GROUP BY a name.

(defn legacy-planner-text?
  "True if Cube wrote `text` with its legacy planner."
  [text]
  (boolean (or (re-find #"\A\s*SELECT\s*\n" text)
               (re-find #"(?i)\bGROUP BY\s+[`\"]" text))))

(defn legacy-planner?
  "True if this Cube plans with its legacy planner. The answer is at most one minute old. With no answer this is false."
  [details cube-meta]
  (let [cube      (first (filter #(and (not= "view" (:type %)) (seq (:measures %)) (some (fn [d] (= "string" (:type d))) (:dimensions %)))
                                 (:cubes cube-meta)))
        dimension (first (filter #(= "string" (:type %)) (:dimensions cube)))]
    (boolean
     (when cube
       (cached details [:legacy-planner]
               (fn []
                 (try
                   (legacy-planner-text? (:text (sql-of details {:measures   [(:name (first (:measures cube)))]
                                                                 :dimensions [(:name dimension)]
                                                                 :filters    [{:member (:name dimension), :operator "set"}]})))
                   (catch Exception _ false))))))))

;;; The size of a result on Cube Store. With dev mode off, Cube keeps each result in its queue in Cube Store. Cube Store takes one message of at most 64 MiB, the default of CUBESTORE_TRANSPORT_MAX_MESSAGE_SIZE. A larger result gives "Websocket message too large: Request of 93138728 bytes exceeds the maximum message size of 67108864 bytes" in the log of Cube Store
;;; and "Queue storage error" in the log of Cube, and Cube retries with no end, so the query hangs and no error reaches the driver. Cube keeps each row as a JSON object: each value is text, with the alias
;;; cube__member as its key. 1048575 rows of id, user_id and quantity_dim of big_orders made 93138728 bytes, 88.8 bytes a row, and this form gives 89.

(def ^:private largest-message-bytes
  "64 MiB, with a margin of a tenth."
  (long (* 0.9 64 1024 1024)))

(def ^:private shortest-value
  "The length that the estimate gives at least to each value. The first rows of a sample can have short numbers, and an id of a later row has more digits."
  10)

(defn dev-mode?
  "True if Cube accepts a JWT with a wrong signature, as it does in dev mode. The answer is at most one minute old. With no answer this is true, so nothing stops."
  [details cube-meta]
  (let [measure (some #(:name (first (:measures %))) (:cubes cube-meta))]
    (boolean
     (or (nil? measure)
         (cached details [:dev-mode]
                 (fn []
                   (try
                     (cube.meta/accepts-wrong-token? details "/v1/sql" {"query" (json/encode {:measures [measure]})})
                     (catch Exception _ true))))))))

(defn- longest-alias
  "The length of the longest alias cube__member of the cubes and views that `sql` names, or of all of them if it names none. Cube writes this alias as the key of each value."
  [cube-meta sql]
  (let [text    (str/lower-case sql)
        cubes   (:cubes cube-meta)
        named   (filter #(re-find (re-pattern (str "(?<![a-z0-9_])" (java.util.regex.Pattern/quote (str/lower-case (str (:name %)))) "(?![a-z0-9_])")) text) cubes)
        lengths (for [cube (if (seq named) named cubes)
                      member (concat (:dimensions cube) (:measures cube))]
                  ;; cube.member becomes cube__member.
                  (inc (count (str (:name member)))))]
    (reduce max 16 lengths)))

(defn row-bytes
  "The bytes of one row of `sample` in a message of Cube Store, at least. Each value is text with the key `alias`: {\"alias\":\"value\",...},"
  [sample alias]
  (let [columns (count (first sample))
        widths  (for [i (range columns)]
                  (reduce max shortest-value (map #(count (json/encode (nth % i nil))) sample)))]
    (+ 3 (reduce + 0 (map #(+ alias 4 2 %) widths)))))

(defn too-large-result
  "{:rows, :bytes, :rows-that-fit} if the result of `sql` is too large for one message of Cube Store, or nil. `sql` is the text that goes to Cube, with its LIMIT. With no
  answer this is {:unknown message}: a Cube that retries a too large result with no end also gives no answer to this count, and the query would hang."
  [details cube-meta sql]
  (try
    (let [[[rows]] (cube.meta/rest-sql details (format "SELECT COUNT(*) FROM (%s) AS mb_rows" sql) "UTC")
          sample   (cube.meta/rest-sql details (format "SELECT * FROM (%s) AS mb_rows LIMIT 100" sql) "UTC")
          per-row  (row-bytes sample (longest-alias cube-meta sql))
          ;; The SQL API gives a count as text.
          rows     (or (some-> rows str parse-long) 0)
          bytes    (* rows per-row)]
      (when (> bytes largest-message-bytes)
        {:rows rows, :bytes (long bytes), :rows-that-fit (long (quot largest-message-bytes (max 1 per-row)))}))
    (catch Exception e
      {:unknown (or (ex-message e) (str e))})))
