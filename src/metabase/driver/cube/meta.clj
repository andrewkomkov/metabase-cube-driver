(ns metabase.driver.cube.meta
  "Reads the Cube data model from the REST API and maps it to Metabase sync metadata.

  Each public cube and view is a table. Each public member is a field. The member kind goes into `:database-type`,
  so that the query compiler can read it from field metadata:

    cube.dimension/<type>     for example cube.dimension/time
    cube.measure/<aggType>    for example cube.measure/sum
    cube.measure/cumulative-<aggType>
    cube.segment"
  (:require
   [buddy.sign.jwt :as jwt]
   [clj-http.client :as http]
   [clojure.string :as str]
   [metabase.driver-api.core :as driver-api]
   [metabase.util.json :as json]))

(set! *warn-on-reflection* true)

(def schema-name
  "Cube has one namespace for cubes and views, so a table has no schema. The Cube SQL API also accepts `public`, but
  with a schema the MCP tool `browse_data` lists no tables unless the agent asks for that schema first."
  nil)

(def ^:private http-timeout-ms 30000)
(def ^:private token-lifetime-seconds 300)

(defn- api-token
  "A short-lived JWT for the REST API, or nil if the connection has no API secret."
  [details]
  (when-let [secret (not-empty (driver-api/secret-value-as-string :cube details "cube-api-secret"))]
    (let [now (quot (System/currentTimeMillis) 1000)]
      ;; The security context of the request is the payload. `user` is the same name that the SQL API sends to checkSqlAuth.
      (jwt/sign (cond-> {:iat now, :exp (+ now token-lifetime-seconds)}
                  (:user details) (assoc :user (:user details)))
                secret
                {:alg :hs256}))))

(defn- meta-url
  [details]
  (let [base (some-> (:cube-rest-url details) str/trim (str/replace #"/+$" ""))]
    (when (str/blank? base)
      (throw (ex-info "The Cube REST URL is not set." {})))
    (str (if (str/ends-with? base "/cubejs-api") base (str base "/cubejs-api"))
         "/v1/meta?extended=true")))

(defn fetch-meta
  "GET /cubejs-api/v1/meta?extended=true. Returns the decoded body."
  [details]
  (let [url      (meta-url details)
        token    (api-token details)
        response (http/get url {:headers            (cond-> {"Accept" "application/json"}
                                                      token (assoc "Authorization" (str "Bearer " token)))
                                :as                 :text
                                :throw-exceptions   false
                                :socket-timeout     http-timeout-ms
                                :connection-timeout http-timeout-ms})]
    (when-not (= 200 (:status response))
      (throw (ex-info (format "Cube REST API returned HTTP %s for %s" (:status response) url)
                      {:status (:status response)})))
    (json/decode+kw (:body response))))

(def ^:private cached-meta-ttl-ms 60000)

(defonce ^:private meta-cache (atom {}))

(defn cached-meta
  "The Cube data model, at most one minute old. The query compiler reads it, and it must not add a request per query."
  [details]
  (let [cache-key (select-keys details [:cube-rest-url :host :port])
        now       (System/currentTimeMillis)
        {:keys [at value]} (get @meta-cache cache-key)]
    (if (and value (< (- now at) cached-meta-ttl-ms))
      value
      (let [fresh (fetch-meta details)]
        (swap! meta-cache assoc cache-key {:at now, :value fresh})
        fresh))))

(defn- non-blank [s]
  (when (and (string? s) (not (str/blank? s)))
    s))

(defn- public?
  [entity]
  (and (not (false? (:public entity)))
       (not (false? (:isVisible entity)))))

(defn- short-name
  "`orders.revenue` -> `revenue`"
  [member-name]
  (subs member-name (inc (str/last-index-of member-name "."))))

(defn- format-name
  "A member `format` is either a string or a map, for example {:type \"link\", :label \"Open\"}."
  [member]
  (let [fmt (:format member)]
    (if (map? fmt) (:type fmt) fmt)))

(defn- format-specifier
  "The d3 specifier of a numeric format, for example `$,.2~f`."
  [member]
  (non-blank (get-in member [:formatDescription :specifier])))

(defn- format->semantic-type
  "The semantic type of a member format. A custom numeric format has no name that tells this, so read its specifier."
  [member]
  (let [specifier (format-specifier member)]
    (case (format-name member)
      "currency"       :type/Currency
      "percent"        :type/Percentage
      "imageUrl"       :type/ImageURL
      "link"           :type/URL
      "custom-numeric" (cond
                         (str/includes? specifier "%") :type/Percentage
                         (str/includes? specifier "$") :type/Currency)
      nil)))

(def managed-semantic-types
  "The semantic types that only a Cube format sets. Sync can clear these when the format goes away."
  #{:type/Currency :type/Percentage :type/ImageURL :type/URL})

(defn- format->settings
  "Metabase column settings for a numeric member format. Metabase has no setting for abbreviation (`s`) or for
  parentheses on negative numbers (`(`). The description of the member keeps the specifier for these."
  [member]
  (when-let [specifier (when (:format member) (format-specifier member))]
    (let [decimals (some-> (second (re-find #"\.(\d+)" specifier)) parse-long)
          currency (or (non-blank (:currency member)) (get-in member [:formatDescription :currency]))
          style    (cond
                     (str/includes? specifier "%") "percent"
                     (str/includes? specifier "$") "currency"
                     :else                         "decimal")]
      (cond-> {:number_style style}
        decimals                         (assoc :decimals decimals)
        currency                         (assoc :currency currency)
        (not (str/includes? specifier ",")) (assoc :number_separators ".")))))

(defn- dimension-base-type
  [{member-type :type}]
  (case member-type
    "string"  :type/Text
    "number"  :type/Decimal
    "boolean" :type/Boolean
    "time"    :type/DateTime
    "geo"     :type/Text
    :type/*))

(def ^:private count-agg-types #{"count" "countDistinct" "countDistinctApprox"})

;; The driver compiles this clause on a measure column to MEASURE(). It compiles another aggregation of that column to the
;; same SQL function, and Cube then computes it from the rows or gives an error.
(def aggregation-of-measure
  "The aggregation clause that holds a measure, from its aggType. This clause sets only the text that Metabase shows and
  the type of the result. `:max` accepts every column type."
  {"sum"                 :sum
   "count"               :sum
   "countDistinct"       :sum
   "countDistinctApprox" :sum
   "runningTotal"        :sum
   "avg"                 :avg
   "min"                 :min
   "max"                 :max})

(defn- measure-base-type
  [{member-type :type, agg-type :aggType}]
  (case member-type
    "number"  (if (count-agg-types agg-type) :type/BigInteger :type/Decimal)
    "string"  :type/Text
    "boolean" :type/Boolean
    "time"    :type/DateTime
    :type/*))

(defn- dimension->field
  [member]
  (cond-> {:name          (short-name (:name member))
           :database-type (str "cube.dimension/" (:type member))
           :base-type     (dimension-base-type member)
           :pk?           (boolean (:primaryKey member))}
    (format->semantic-type member) (assoc :semantic-type (format->semantic-type member))
    ;; Cube adds this column for a dimension link. A person has no use for it.
    (:synthetic member)            (assoc :visibility-type :hidden)))

(defn- measure->field
  [member]
  (let [agg-type (or (:aggType member) (:type member))]
    {:name          (short-name (:name member))
     :database-type (str "cube.measure/" (when (:cumulative member) "cumulative-") agg-type)
     :base-type     (measure-base-type member)
     :pk?           false
     :visibility-type :hidden
     :semantic-type (or (format->semantic-type member)
                        (when (count-agg-types agg-type) :type/Quantity))}))

(defn- segment->field
  [member]
  {:name          (short-name (:name member))
   :database-type "cube.segment"
   :base-type     :type/Boolean
   :pk?           false
   :visibility-type :hidden})

(defn- member-names
  "`[\"orders.status\" \"orders.size\"]` -> `status, size`"
  [names]
  (str/join ", " (map short-name names)))

(defn- json-line
  [label value]
  (when (seq value)
    (str label ": " (json/encode value) ".")))

(defn- link-labels
  [member]
  (when-let [labels (seq (keep #(non-blank (:label %)) (:links member)))]
    (str "Links: " (str/join ", " labels) ".")))

(defn- format-note
  "The format of a member as text. Only a part of a Cube format has an equivalent in Metabase."
  [member]
  (let [fmt (:format member)]
    (when (map? fmt)
      (case (:type fmt)
        "link"           (when-let [label (non-blank (:label fmt))]
                           (str "Link label: " label "."))
        "custom-time"    (str "Time format: " (:value fmt) ".")
        "custom-numeric" (str "Number format: " (:value fmt) ".")
        nil))))

(defn- drill-note
  [member]
  (when-let [members (seq (:drillMembers member))]
    (str "To drill into this measure, use: " (member-names members) ".")))

(defn member-description
  "The description of a member, followed by what Metabase has no column for: format text, links, drill members, meta."
  [member]
  (not-empty
   (str/join "\n\n" (keep identity [(non-blank (:description member))
                                     (format-note member)
                                     (link-labels member)
                                     (drill-note member)
                                     (json-line "Meta" (:meta member))]))))

(defn- with-member-info
  [field member kind]
  (let [comment (member-description member)]
    (cond-> (assoc field
                   ::kind     kind
                   ::member   member
                   ::title    (or (non-blank (:shortTitle member)) (non-blank (:title member)))
                   ::settings (format->settings member))
      comment (assoc :field-comment comment))))

(defn- hierarchy-line
  [{:keys [title levels], hierarchy-name :name}]
  (str (or (non-blank title) (short-name hierarchy-name)) ": " (str/join " > " (map short-name levels))))

(defn- folder-text
  "A folder as text. A nested folder is in brackets: `Money: net_sales, [Ratios: count]`."
  [{folder-name :name, members :members}]
  (str folder-name ": "
       (str/join ", " (map #(if (map? %) (str "[" (folder-text %) "]") (short-name %)) members))))

(defn- view-group-text
  "`revenue_views` -> `Revenue views (revenue_views)`"
  [group]
  (str (str/capitalize (str/replace group "_" " ")) " (" group ")"))

(def ^:private max-members-in-text
  "A very wide cube must not make each search result long."
  60)

(defn- member-list-line
  "`Measures: Revenue, Order count.`, with the title of each public member. A title is the name that people use, so a
  search for it finds this table. Fields have no entry in the Metabase search index."
  [label members]
  (when-let [members (seq (filter public? members))]
    (let [titles (map #(or (non-blank (:shortTitle %)) (non-blank (:title %)) (short-name (:name %))) members)
          shown  (take max-members-in-text titles)
          more   (- (count titles) (count shown))]
      (str label ": " (str/join ", " shown) (when (pos? more) (format ", and %d more" more)) "."))))

(defn- joins-line
  [cube]
  (when-let [joins (seq (:joins cube))]
    (str "Joins: " (str/join ", " (distinct (map :name joins))) ".")))

(defn cube-description
  "The description of a cube or view, followed by what Metabase has no object for: hierarchies, folders, view groups
  and meta, and by the members of the cube. Agents read this text with search and with the table metadata."
  [cube]
  (let [hierarchies (filter public? (:hierarchies cube))
        folders     (or (seq (:nestedFolders cube)) (:folders cube))]
    (not-empty
     (str/join "\n\n"
               (keep identity
                     [(non-blank (:description cube))
                      (when (seq hierarchies) (str "Hierarchies: " (str/join "; " (map hierarchy-line hierarchies)) "."))
                      (when (seq folders)     (str "Folders: " (str/join "; " (map folder-text folders)) "."))
                      (when (seq (:viewGroups cube))
                        (str "View groups: " (str/join ", " (map view-group-text (:viewGroups cube))) "."))
                      (json-line "Meta" (:meta cube))
                      (member-list-line "Measures" (:measures cube))
                      (member-list-line "Dimensions" (:dimensions cube))
                      (member-list-line "Segments" (:segments cube))
                      (joins-line cube)])))))

(defn public-cubes
  "The public cubes and views of a decoded meta response."
  [cube-meta]
  (filterv public? (:cubes cube-meta)))

(defn cube->table
  "Sync metadata for a table. `:display_name` is used when Metabase creates the table."
  [cube]
  (let [description (cube-description cube)]
    (cond-> {:name (:name cube), :schema schema-name}
      description               (assoc :description description)
      (non-blank (:title cube)) (assoc :display_name (:title cube)))))

(defn cube->fields
  "Sync metadata for the fields of a cube: dimensions, then measures, then segments. Each map also has namespaced keys
  that Metabase sync has no input for: `::kind`, `::member`, `::title` and `::settings`.
  See [[metabase.driver.cube.semantic]]."
  [cube]
  (let [fields (concat (for [m (:dimensions cube) :when (public? m)]
                         (with-member-info (dimension->field m) m :dimension))
                       (for [m (:measures cube) :when (public? m)]
                         (with-member-info (measure->field m) m :measure))
                       (for [m (:segments cube) :when (public? m)]
                         (with-member-info (segment->field m) m :segment)))]
    (map-indexed (fn [position field]
                   (assoc field
                          :database-position position
                          :table-schema      schema-name
                          :table-name        (:name cube)))
                 fields)))

(defn sync-field
  "A map from [[cube->fields]] without the keys that are not part of the Metabase sync schema."
  [field]
  (dissoc field ::kind ::member ::title ::settings))

;;; ----------------------------------------------- foreign keys -----------------------------------------------

(defn- parse-join-side
  "One side of a join condition, as [cube-reference column kind]. `kind` is :sql for `${CUBE}.user_id`, where the name is
  a SQL column, and :member for `${users.id}`. Returns nil for an expression, because an expression is not a foreign key."
  [side]
  (let [side (str/trim side)]
    (if-let [[_ cube column] (re-matches #"\$?\{(\w+)\}\.(\w+)" side)]
      [cube column :sql]
      (when-let [[_ cube member] (re-matches #"\$?\{(\w+)\.(\w+)\}" side)]
        [cube member :member]))))

(defn- parse-join-sql
  "`` `${CUBE}.user_id = ${users.id}` `` -> [[\"CUBE\" \"user_id\" :sql] [\"users\" \"id\" :member]], or nil if the
  condition is not one equality of two plain references."
  [join-sql]
  (when-let [[_ left right] (re-matches #"([^=<>!]+)=([^=<>!]+)" (str/replace (str join-sql) "`" ""))]
    (let [left (parse-join-side left), right (parse-join-side right)]
      (when (and left right)
        [left right]))))

(defn- find-dimension
  "The public dimension of `cube` that a join reference names."
  [cube column kind]
  (let [dimensions (filter public? (:dimensions cube))
        by-name    (fn [d] (= (short-name (:name d)) column))
        by-sql     (fn [d] (= (str/replace (str (:sql d)) "`" "") column))]
    (or (when (= kind :sql) (first (filter by-sql dimensions)))
        (first (filter by-name dimensions)))))

(defn- own-and-inherited-joins
  "Cube returns the joins of a parent only on the parent, and `extends` names it."
  [name->cube cube]
  (loop [cube cube, joins [], seen #{}]
    (if (or (nil? cube) (seen (:name cube)))
      joins
      (recur (name->cube (:extends cube)) (into joins (:joins cube)) (conj seen (:name cube))))))

(defn- join->fk
  "The foreign key that one join of `cube` means, as {:fk [cube member], :pk [cube member]}, or nil. A many-to-one join
  has the key on the left. A one-to-many or one-to-one join has it on the other cube. The target must be a primary key."
  [name->cube cube {:keys [relationship sql], join-name :name, join-alias :alias}]
  (when-let [[left right] (parse-join-sql sql)]
    (let [resolve-side (fn [[reference column kind]]
                         (let [target (cond
                                        (#{"CUBE" (:name cube)} reference)     cube
                                        (#{join-name join-alias} reference)    (name->cube join-name)
                                        :else                                  nil)]
                           (when target
                             (when-let [dimension (find-dimension target column kind)]
                               [(:name target) (short-name (:name dimension)) dimension]))))
          [local remote] (let [a (resolve-side left), b (resolve-side right)]
                           (if (= (first a) (:name cube)) [a b] [b a]))
          [from to]      (if (#{"belongsTo" "many_to_one"} relationship) [local remote] [remote local])]
      (when (and from to (not= (first from) (first to)) (:primaryKey (nth to 2)))
        {:fk (subvec from 0 2), :pk (subvec to 0 2)}))))

(defn- join->pair
  "The two sides of one join as a set of two [cube member] vectors, or nil if the join SQL is not one plain equality."
  [name->cube cube {:keys [sql], join-name :name, join-alias :alias}]
  (when-let [[left right] (parse-join-sql sql)]
    (let [resolve-side (fn [[reference column kind]]
                         (when-let [target (cond
                                             (#{"CUBE" (:name cube)} reference)  cube
                                             (#{join-name join-alias} reference) (name->cube join-name))]
                           (when-let [dimension (find-dimension target column kind)]
                             [(:name target) (short-name (:name dimension))])))
          a            (resolve-side left)
          b            (resolve-side right)]
      (when (and a b)
        #{a b}))))

(defn join-pairs
  "All [cube member] pairs that a join of the data model compares, in both directions."
  [cube-meta]
  (let [name->cube (into {} (map (juxt :name identity)) (:cubes cube-meta))]
    (into #{}
          (keep identity)
          (for [cube (:cubes cube-meta)
                join (own-and-inherited-joins name->cube cube)]
            (join->pair name->cube cube join)))))

(defn time-granularities
  "The custom granularities of a time dimension, as Cube lists them, or nil."
  [cube-meta cube-name member-name]
  (let [full-name (str cube-name "." member-name)]
    (some (fn [{:keys [dimensions], cube :name}]
            (when (= cube cube-name)
              (some (fn [dimension]
                      (when (= full-name (:name dimension))
                        (:granularities dimension)))
                    dimensions)))
          (:cubes cube-meta))))

(defn foreign-keys
  "All foreign keys that the joins of the public cubes mean, with one target per key column, ordered by key."
  [cube-meta]
  (let [name->cube (into {} (map (juxt :name identity)) (:cubes cube-meta))
        public     (into #{} (map :name) (public-cubes cube-meta))]
    (->> (for [cube  (public-cubes cube-meta)
               join  (own-and-inherited-joins name->cube cube)
               :let  [fk (join->fk name->cube cube join)]
               :when (and fk (public (first (:fk fk))) (public (first (:pk fk))))]
           fk)
         (sort-by (juxt :fk :pk))
         (reduce (fn [result {:keys [fk] :as entry}]
                   (if (contains? result fk) result (assoc result fk entry)))
                 (sorted-map))
         vals
         vec)))
