(ns metabase.driver.cube.probe
  "Questions to Cube that /v1/meta does not answer. /v1/sql tells how Cube writes a query, and it runs nothing. Each answer is
  at most one minute old, so a query adds no request in most cases. A check that gets no answer stops nothing, except where the
  docstring says so."
  (:require
   [clojure.string :as str]
   [metabase.driver.cube.meta :as cube.meta])
  (:import
   (java.time LocalDate LocalDateTime OffsetDateTime ZoneOffset)
   (java.time.format DateTimeParseException)))

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
  (let [{{[text] :sql, :keys [preAggregations]} :sql} (cube.meta/rest-get details "/v1/sql" query)]
    {:text (str text), :pre-aggregations preAggregations}))

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
        time-dimension (first (real-time-dimensions cube))]
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
  the source differs from the source of the measure that it reads."
  [details measure-name base time-dimension]
  (let [query (fn [member]
                {:measures       [member]
                 :timeDimensions [{:dimension time-dimension, :granularity "day", :dateRange ["2025-01-01" "2025-01-31"]}]
                 :limit          1})
        text  (:text (sql-of details (query measure-name)))]
    (or (boolean (re-find shifted-time-pattern text))
        (and (some? base)
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
