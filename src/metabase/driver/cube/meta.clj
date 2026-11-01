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

(defn- api-url
  [details path]
  (let [base (some-> (:cube-rest-url details) str/trim (str/replace #"/+$" ""))]
    (when (str/blank? base)
      (throw (ex-info "The Cube REST URL is not set." {})))
    (str (if (str/ends-with? base "/cubejs-api") base (str base "/cubejs-api")) path)))

(defn- api-call
  "Call a path of the Cube REST API. Returns the response, or throws for another status than 200."
  [send details path options]
  (let [url      (api-url details path)
        token    (api-token details)
        response (send url (merge {:headers            (cond-> {"Accept" "application/json"}
                                                         token (assoc "Authorization" (str "Bearer " token)))
                                   :as                 :text
                                   :throw-exceptions   false
                                   :socket-timeout     http-timeout-ms
                                   :connection-timeout http-timeout-ms}
                                  options))]
    (when-not (= 200 (:status response))
      (throw (ex-info (format "Cube REST API returned HTTP %s for %s" (:status response) url)
                      {:status (:status response)})))
    response))

(defn- api-get
  "GET a path of the Cube REST API. Returns the decoded body."
  [details path query-params]
  (json/decode+kw (:body (api-call http/get details path {:query-params query-params}))))

(defn rest-sql
  "The rows of a SQL text on /v1/cubesql, in the time zone `zone`. This path answers with the schema on the first line, and the data
  after it."
  [details sql zone]
  (let [body  (:body (api-call http/post details "/v1/cubesql"
                               {:content-type :json
                                :body         (json/encode {:query sql, :timezone zone})}))
        lines (map json/decode+kw (remove str/blank? (str/split-lines body)))]
    (when-let [error (some :error lines)]
      (throw (ex-info (str error) {})))
    (into [] (mapcat :data) lines)))

(declare mark-geo-views with-join-target-keys)

(defn- last-name-part
  [member-name]
  (last (str/split (str member-name) #"\.")))

(defn- with-definition-of-parents
  "A `member` of a cube with `extends` has no :sql and no :case in /v1/meta if the cube does not define it again. The parent has them."
  [name->cube cube kind member]
  (if (or (:sql member) (:case member))
    member
    (let [parents (rest (take 20 (take-while some? (iterate #(name->cube (:extends %)) cube))))
          same    (fn [parent] (first (filter #(= (last-name-part (:name %)) (last-name-part (:name member))) (kind parent))))]
      (merge member (some #(not-empty (select-keys (same %) [:sql :case])) parents)))))

(defn- with-inherited-definitions
  "The compiler reads the SQL of a member, for example to find the column of a join or to tell a `switch` dimension from a dimension of a
  column. So each member of a child cube gets the definition that it inherits."
  [cube-meta]
  (let [name->cube (into {} (map (juxt :name identity)) (:cubes cube-meta))
        with-kind  (fn [cube kind]
                     (cond-> cube
                       (kind cube) (update kind (partial mapv #(with-definition-of-parents name->cube cube kind %)))))]
    (update cube-meta :cubes
            (partial mapv (fn [cube]
                            (if (:extends cube)
                              (reduce with-kind cube [:dimensions :measures :segments])
                              cube))))))

(defn fetch-meta
  "GET /cubejs-api/v1/meta?extended=true. Returns the decoded body, with the definitions that a child cube inherits, and with
  `:geo-readable?` on each view if Cube can read a geo dimension of a view. A key that a join targets and that Cube hides is there too, see
  [[with-join-target-keys]]."
  [details]
  (mark-geo-views details (with-join-target-keys (with-inherited-definitions (api-get details "/v1/meta" {"extended" "true"})))))

(defn rest-get
  "GET a path of the Cube REST API with a query."
  [details path query]
  (api-get details path {"query" (json/encode query)}))

(defn rest-load
  "The result of a query on /v1/load. Cube answers `Continue wait` while the query runs."
  [details query]
  (loop [tries 30]
    (let [body (rest-get details "/v1/load" query)]
      (if (and (= "Continue wait" (:error body)) (pos? tries))
        (do (Thread/sleep 1000)
            (recur (dec tries)))
        body))))

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
      ;; A check of private members reads this, so an old model is better than none while the REST API is down.
      (let [fresh (try
                    (fetch-meta details)
                    (catch Exception e
                      (or value (throw e))))]
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
    ("geo" "geo-in-view") :type/Text
    ("geo-latitude" "geo-longitude") :type/Float
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

(def ^:private geo-parts
  "The number columns of a geo dimension."
  [{:part "latitude", :semantic-type :type/Latitude}
   {:part "longitude", :semantic-type :type/Longitude}])

(def ^:private geo-part-types
  (into {} (map (juxt #(str "geo-" (:part %)) :semantic-type)) geo-parts))

(defn geo-part
  "`\"latitude\"` or `\"longitude\"` if `database-type` is one number column of a geo dimension, otherwise nil."
  [database-type]
  (second (re-matches #"cube\.dimension/geo-(latitude|longitude)" (str database-type))))

(defn geo-dimension-name
  "`location__latitude` -> `location`"
  [field-name]
  (str/replace field-name #"__(latitude|longitude)$" ""))

(defn- geo-part-members
  "Cube has one text column for a geo dimension, `latitude,longitude`. A Metabase map needs one number column for each, with
  the Latitude and Longitude semantic types, so a geo dimension also gives these two members."
  [{:keys [title shortTitle description], member-name :name}]
  (for [{:keys [part]} geo-parts
        :let [with-part #(some-> (non-blank %) (str " " part))]]
    (cond-> {:name        (str member-name "__" part)
             :type        (str "geo-" part)
             :description (str (str/capitalize part) " of the geo dimension " (short-name member-name) "."
                               (some->> (non-blank description) (str " ")))}
      (with-part title)      (assoc :title (with-part title))
      (with-part shortTitle) (assoc :shortTitle (with-part shortTitle)))))

(defn- with-geo-parts
  "A dimension, with its number columns if it is a geo dimension. Stock Cube fails each query on a geo dimension of a view
  with \"Cannot read properties of undefined\", so there it stays one hidden column that raw rows show as NULL."
  [cube dimension]
  (cond
    (not= "geo" (:type dimension))                     [dimension]
    (and (= "view" (:type cube))
         (not (:geo-readable? cube)))                  [(assoc dimension :type "geo-in-view")]
    :else                                              (cons dimension (geo-part-members dimension))))

(defn- first-geo-dimension-of-a-view
  [cube-meta]
  (first (for [cube      (:cubes cube-meta)
               :when     (= "view" (:type cube))
               dimension (:dimensions cube)
               :when     (and (= "geo" (:type dimension)) (public? dimension))]
           (:name dimension))))

(defn- reads-geo-dimension?
  "True if Cube writes SQL for this geo dimension. /v1/sql runs nothing. Any other answer gives false, because the hidden
  column works on each Cube."
  [details dimension-name]
  (try
    (some? (not-empty (first (get-in (rest-get details "/v1/sql" {:dimensions [dimension-name], :limit 1}) [:sql :sql]))))
    (catch Exception _ false)))

(defn- mark-geo-views
  [details cube-meta]
  (let [dimension-name (first-geo-dimension-of-a-view cube-meta)]
    (if (and dimension-name (reads-geo-dimension? details dimension-name))
      (update cube-meta :cubes (partial mapv #(cond-> % (= "view" (:type %)) (assoc :geo-readable? true))))
      cube-meta)))

(def private-key-type
  "The database type of a private primary key. Raw rows show NULL for it, and the driver stops each other use, see check-no-private-key!."
  "cube.dimension/private-key")

(defn- dimension->field
  [member]
  (cond-> {:name          (short-name (:name member))
           :database-type (if (public? member) (str "cube.dimension/" (:type member)) private-key-type)
           :base-type     (dimension-base-type member)
           :pk?           (boolean (:primaryKey member))}
    (format->semantic-type member) (assoc :semantic-type (format->semantic-type member))
    (geo-part-types (:type member)) (assoc :semantic-type (geo-part-types (:type member)))
    ;; Cube adds this column for a dimension link. A person has no use for it.
    (or (:synthetic member)
        (= "geo-in-view" (:type member))) (assoc :visibility-type :hidden)
    ;; A foreign key needs its target field. Metabase core cannot compile an implicit join to a sensitive field: it fails with `Missing ::desired-alias->escaped`.
    (not (public? member)) (assoc :visibility-type :hidden)))

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
  (let [comment (when (public? member) (member-description member))]
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
  (let [fields (concat (for [dimension (:dimensions cube)
                             :when (or (public? dimension) (::join-target dimension))
                             m (with-geo-parts cube dimension)]
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
  "The dimension of `cube` that a join reference names: a public dimension, or a primary key, which a foreign key can target if it is
  private."
  [cube column kind]
  (let [dimensions (filter #(or (public? %) (:primaryKey %)) (:dimensions cube))
        by-name    (fn [d] (= (short-name (:name d)) column))
        by-sql     (fn [d] (= (str/replace (str (:sql d)) "`" "") column))]
    (or (when (= kind :sql) (first (filter by-sql dimensions)))
        (first (filter by-name dimensions)))))

(defn- with-join-target-keys
  "A foreign key needs its target field, also if that primary key is private, so such a key gets `::join-target`, and sync keeps it as a hidden field. A private key that no join
  targets has no use in Metabase, so it does not sync. With dev mode off, /v1/meta has no private member, but the join SQL names the key, so the target cube gets it here as a
  private dimension, with `::not-in-cube`. The model is then the same with dev mode on and off. This adds nothing that Cube can read: the driver reads no value of a private
  member, and a Cube with dev mode off refuses it."
  [cube-meta]
  (let [name->cube (into {} (map (juxt :name identity)) (:cubes cube-meta))
        targets    (for [cube  (:cubes cube-meta)
                         {:keys [relationship sql], join-name :name, join-alias :alias} (:joins cube)
                         :let  [[left right] (parse-join-sql sql)
                                side          (fn [[reference column kind]]
                                                (cond
                                                  (#{"CUBE" (:name cube)} reference)  [cube column kind]
                                                  (#{join-name join-alias} reference) [(name->cube join-name) column kind]))
                                a             (when left (side left))
                                b             (when right (side right))
                                [local remote] (if (= (:name (first a)) (:name cube)) [a b] [b a])
                                [from to]     (if (#{"belongsTo" "many_to_one"} relationship) [local remote] [remote local])]
                         :when (and (first from) (first to))
                         :let  [from-dimension (apply find-dimension from)
                                to-dimension   (apply find-dimension to)]
                         :when (and from-dimension
                                    (public? from-dimension)
                                    (if to-dimension
                                      (and (:primaryKey to-dimension) (not (public? to-dimension)))
                                      (not-any? :primaryKey (:dimensions (first to)))))]
                     {:cube   (:name (first to))
                      :member (some-> to-dimension :name)
                      :column (second to)
                      :type   (:type from-dimension)})
        by-cube    (group-by :cube (distinct targets))]
    (update cube-meta :cubes
            (partial mapv (fn [cube]
                            (reduce (fn [cube {:keys [member column], member-type :type}]
                                      (if member
                                        (update cube :dimensions (partial mapv #(cond-> % (= member (:name %)) (assoc ::join-target true))))
                                        (update cube :dimensions (fnil conj [])
                                                {:name         (str (:name cube) "." column)
                                                 :type         member-type
                                                 :sql          column
                                                 :public       false
                                                 :primaryKey   true
                                                 ::join-target true
                                                 ::not-in-cube true})))
                                    cube
                                    (by-cube (:name cube))))))))

(defn private-members
  "A map from the lower case name of a cube to the lower case short names of the members that Cube marks private. With `:in-cube-only? true`, this has no key that only
  [[with-join-target-keys]] adds, because Cube has no column for it in `SELECT *`."
  [cube-meta & {:keys [in-cube-only?]}]
  (into {}
        (keep (fn [{cube-name :name, :keys [dimensions measures segments]}]
                (when-let [members (seq (for [member (concat dimensions measures segments)
                                              :when  (and (not (public? member))
                                                          (not (and in-cube-only? (::not-in-cube member))))]
                                          (str/lower-case (short-name (:name member)))))]
                  [(str/lower-case cube-name) (set members)])))
        (:cubes cube-meta)))

(defn private-cubes
  "The lower case names of the cubes and views that Cube marks private. /v1/meta shows them only in dev mode."
  [cube-meta]
  (into #{} (comp (remove public?) (map (comp str/lower-case :name))) (:cubes cube-meta)))

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
      (when (and from to (not= (first from) (first to)) (public? (nth from 2)) (:primaryKey (nth to 2)))
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

(defn same-source-groups
  "Sets of short member names of a view that are aliases of one member of a cube. Each comes from another join path, or the
  view would not need two names. `/v1/meta` has no join path, so this is the only sign of it."
  [dimensions]
  (into []
        (comp (filter #(next (val %)))
              (map (fn [[_ members]]
                     (into #{} (map #(str/lower-case (last (str/split (:name %) #"\.")))) members))))
        (group-by :aliasMember (filter :aliasMember dimensions))))

(defn same-source-groups-of-cube
  "The groups of `same-source-groups` for a cube or view by its name."
  [cube-meta cube-name]
  (same-source-groups (:dimensions (first (filter #(= cube-name (:name %)) (:cubes cube-meta))))))

(defn switch-dimension-names
  "The short names of the `switch` dimensions of a cube or view. Cube gives each row once for each value of such a dimension. /v1/meta
  shows it as a string with no SQL and no case, and an alias of a view has an `aliasMember`."
  [cube-meta cube-name]
  (let [dimension-named (into {} (comp (mapcat :dimensions) (map (juxt :name identity))) (:cubes cube-meta))
        switch?         (fn switch? [dimension]
                          (if-let [source (dimension-named (:aliasMember dimension))]
                            (switch? source)
                            (and (= "string" (:type dimension)) (nil? (:sql dimension)) (nil? (:case dimension)))))]
    (into #{}
          (comp (filter switch?)
                (map #(str/lower-case (short-name (:name %)))))
          (:dimensions (first (filter #(= cube-name (:name %)) (:cubes cube-meta)))))))

(defn joined-member-of-view?
  "True if this dimension of a view comes from another cube than the first dimension of the view. In an aggregation, Cube adds each row of
  that cube once, as it does for a dimension of a joined cube."
  [cube-meta view-name member-name]
  (let [dimensions (:dimensions (first (filter #(= view-name (:name %)) (:cubes cube-meta))))
        cube-of    #(some-> (:aliasMember %) (str/split #"\.") first)
        root       (cube-of (first dimensions))
        dimension  (first (filter #(= (str/lower-case member-name) (str/lower-case (short-name (:name %)))) dimensions))]
    (boolean (and root (cube-of dimension) (not= root (cube-of dimension))))))

(defn- cube-entry
  [cube-meta cube-name]
  (first (filter #(= cube-name (:name %)) (:cubes cube-meta))))

(defn view?
  "True if this table is a view of Cube."
  [cube-meta table-name]
  (= "view" (:type (cube-entry cube-meta table-name))))

(defn- source-member
  "The member of a cube behind a short name of a table: `kind` is :measures or :dimensions. A view reads the member of its alias."
  [cube-meta table-name member-name kind]
  (let [own     (first (filter #(= (str/lower-case member-name) (str/lower-case (short-name (:name %))))
                               (kind (cube-entry cube-meta table-name))))
        members (into {} (comp (mapcat kind) (map (juxt :name identity))) (:cubes cube-meta))]
    (or (members (:aliasMember own)) own)))

(defn cube-of-member
  "The name of the cube that a member of a table reads."
  [cube-meta table-name member-name kind]
  (some-> (source-member cube-meta table-name member-name kind) :name (str/split #"\.") first))

(defn dimension-reads-a-member?
  "True if the SQL of this dimension reads a member of a cube, as a `sub_query` dimension does. Its value can depend on the other columns and the filters of the query."
  [cube-meta table-name member-name]
  (boolean (re-find #"\{\w+\.\w+\}" (str (:sql (source-member cube-meta table-name member-name :dimensions))))))

(defn roll-up-of-measure
  "The aggregation that gives this measure from its values on smaller groups, or nil if no aggregation can. A measure that reads another measure can change with the groups."
  [cube-meta table-name member-name]
  (let [{:keys [aggType cumulative sql]} (source-member cube-meta table-name member-name :measures)]
    (when-not (or cumulative (str/includes? (str sql) "${"))
      ({"sum" :sum, "count" :sum, "min" :min, "max" :max} aggType))))

(defn count-measure?
  "True if this measure counts."
  [cube-meta table-name member-name]
  (= "count" (:aggType (source-member cube-meta table-name member-name :measures))))

(defn many-to-one-path?
  "True if each row of cube `from` has at most one row of cube `to`, through the joins of the data model."
  [cube-meta from to]
  (let [name->cube (into {} (map (juxt :name identity)) (:cubes cube-meta))]
    (loop [seen #{from}, front [from]]
      (cond
        (contains? seen to) true
        (empty? front)      false
        :else (let [next-cubes (for [cube-name front
                                     {:keys [relationship], join-name :name} (own-and-inherited-joins name->cube (name->cube cube-name))
                                     :when (and (#{"belongsTo" "many_to_one" "hasOne" "one_to_one"} relationship)
                                                (not (seen join-name)))]
                                 join-name)]
                (recur (into seen next-cubes) (vec (distinct next-cubes))))))))

(defn row-count-measure
  "The short name of a measure of this table that counts one row of the first cube of the table with no filter, and the name of that cube, or nil."
  [cube-meta table-name]
  (let [entry     (cube-entry cube-meta table-name)
        cube-of   #(some-> % (str/split #"\.") first)
        root      (if (= "view" (:type entry))
                    (cube-of (:aliasMember (first (:dimensions entry))))
                    table-name)
        plain?    (fn [{:keys [aggType sql filters]}]
                    (and (= "count" aggType) (nil? sql) (empty? filters)))
        measure   (first (filter (fn [measure]
                                   (let [source (source-member cube-meta table-name (short-name (:name measure)) :measures)]
                                     (and (public? measure)
                                          (plain? source)
                                          (= root (cube-of (:name source))))))
                                 (:measures entry)))]
    (when (and root measure)
      [(short-name (:name measure)) root])))

(defn rollup-can-read-other-rows?
  "True if a query can read a part of the rows from this rollup: it has a build range, it is a rollup_lambda, or it allows a range that does
  not fit its buckets."
  [{:keys [type allowNonStrictDateRangeMatch] :as pre-aggregation}]
  (or (= "rollupLambda" type)
      allowNonStrictDateRangeMatch
      (contains? pre-aggregation :refreshRangeStart)
      (contains? pre-aggregation :refreshRangeEnd)))

(defn tables-with-partial-rollups
  "The lower case names of the cubes that have a rollup of [[rollup-can-read-other-rows?]], and of the views on these cubes."
  [cube-meta]
  (let [cubes  (into #{}
                     (comp (filter #(some rollup-can-read-other-rows? (:preAggregations %)))
                           (map :name))
                     (:cubes cube-meta))
        cube-of #(first (str/split (str %) #"\."))]
    (into #{}
          (comp (filter (fn [cube]
                          (or (cubes (:name cube))
                              (some #(cubes (cube-of (:aliasMember %))) (concat (:measures cube) (:dimensions cube))))))
                (map (comp str/lower-case :name)))
          (:cubes cube-meta))))

(defn key-count-measure
  "If this measure has type count and no SQL of its own, so Cube writes `count(pk)`, also with a filter: [cube measure], the short names of a measure with no filter on its
  cube, or nil. A view reads the cube of its alias, because the rows of a view can come from a join."
  [cube-meta member-name]
  (let [measures    (into {} (comp (mapcat :measures) (map (juxt :name identity))) (:cubes cube-meta))
        measure     (measures member-name)
        source      (or (measures (:aliasMember measure)) measure)
        counts-key? (fn [{:keys [aggType sql]}] (and (= "count" aggType) (nil? sql)))
        cube-name   (first (str/split (str (:name source)) #"\."))]
    (when (and source (counts-key? source))
      (when-let [plain (first (remove (comp seq :filters)
                                      (filter counts-key? (:measures (first (filter #(= cube-name (:name %)) (:cubes cube-meta)))))))]
        [cube-name (short-name (:name plain))]))))

(defn pre-aggregation-named
  "The entry of /v1/meta for a pre-aggregation by its name `cube.rollup`."
  [cube-meta pre-aggregation-id]
  (let [[cube-name rollup-name] (str/split (str pre-aggregation-id) #"\.")]
    (first (for [cube  (:cubes cube-meta)
                 :when (= cube-name (:name cube))
                 entry (:preAggregations cube)
                 :when (= rollup-name (:name entry))]
             entry))))

(defn measure-in-query-zone?
  "True if the SQL of this measure reads SQL_UTILS.convertTz, so its value is a time in the zone of the query. A view reads its alias."
  [cube-meta member-name]
  (let [measures (into {} (comp (mapcat :measures) (map (juxt :name identity))) (:cubes cube-meta))
        measure  (measures member-name)
        source   (or (measures (:aliasMember measure)) measure)]
    (boolean (some-> (:sql source) str (str/includes? "convertTz")))))

(defn same-sql-groups
  "Sets of short names of dimensions that read another member with equal SQL, as a `sub_query` dimension does. /v1/meta has no
  other sign of a `sub_query`."
  [dimensions]
  (into []
        (comp (filter #(str/includes? (str (key %)) "${"))
              (filter #(next (val %)))
              (map (fn [[_ members]]
                     (into #{} (map #(str/lower-case (last (str/split (:name %) #"\.")))) members))))
        (group-by :sql (remove :aliasMember dimensions))))

(defn same-sql-groups-of-cube
  "The groups of `same-sql-groups` for a cube or view by its name."
  [cube-meta cube-name]
  (same-sql-groups (:dimensions (first (filter #(= cube-name (:name %)) (:cubes cube-meta))))))

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
