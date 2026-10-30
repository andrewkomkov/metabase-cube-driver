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
  "The Cube SQL API exposes all cubes and views in this schema."
  "public")

(def ^:private http-timeout-ms 30000)
(def ^:private token-lifetime-seconds 300)

(defn- api-token
  "A short-lived JWT for the REST API, or nil if the connection has no API secret."
  [details]
  (when-let [secret (not-empty (driver-api/secret-value-as-string :cube details "cube-api-secret"))]
    (let [now (quot (System/currentTimeMillis) 1000)]
      (jwt/sign {:iat now, :exp (+ now token-lifetime-seconds)} secret {:alg :hs256}))))

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

(defn- public?
  [entity]
  (and (not (false? (:public entity)))
       (not (false? (:isVisible entity)))))

(defn- short-name
  "`orders.revenue` -> `revenue`"
  [member-name]
  (subs member-name (inc (str/last-index-of member-name "."))))

(defn- non-blank [s]
  (when (and (string? s) (not (str/blank? s)))
    s))

(defn- format-name
  "A member `format` is either a string or a map, for example {:type \"link\", :label \"Open\"}."
  [member]
  (let [fmt (:format member)]
    (if (map? fmt) (:type fmt) fmt)))

(defn- format->semantic-type
  [member]
  (case (format-name member)
    "currency" :type/Currency
    "percent"  :type/Percentage
    "imageUrl" :type/ImageURL
    "link"     :type/URL
    nil))

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
    (format->semantic-type member) (assoc :semantic-type (format->semantic-type member))))

(defn- measure->field
  [member]
  (let [agg-type (or (:aggType member) (:type member))]
    {:name          (short-name (:name member))
     :database-type (str "cube.measure/" (when (:cumulative member) "cumulative-") agg-type)
     :base-type     (measure-base-type member)
     :pk?           false
     :semantic-type (or (format->semantic-type member)
                        (when (count-agg-types agg-type) :type/Quantity))}))

(defn- segment->field
  [member]
  {:name          (short-name (:name member))
   :database-type "cube.segment"
   :base-type     :type/Boolean
   :pk?           false})

(defn- with-member-info
  [field member]
  (let [comment (non-blank (:description member))]
    (cond-> (assoc field ::title (or (non-blank (:shortTitle member)) (non-blank (:title member))))
      comment (assoc :field-comment comment))))

(defn public-cubes
  "The public cubes and views of a decoded meta response."
  [cube-meta]
  (filterv public? (:cubes cube-meta)))

(defn cube->table
  "Sync metadata for a table. `:display_name` is used when Metabase creates the table."
  [cube]
  (cond-> {:name (:name cube), :schema schema-name}
    (non-blank (:description cube)) (assoc :description (:description cube))
    (non-blank (:title cube))       (assoc :display_name (:title cube))))

(defn cube->fields
  "Sync metadata for the fields of a cube: dimensions, then measures, then segments. Each map also has the namespaced
  key `::title`, the member title. Metabase sync has no input for it, see [[metabase.driver.cube]]."
  [cube]
  (let [fields (concat (for [m (:dimensions cube) :when (public? m)]
                         (with-member-info (dimension->field m) m))
                       (for [m (:measures cube) :when (public? m)]
                         (with-member-info (measure->field m) m))
                       (for [m (:segments cube) :when (public? m)]
                         (with-member-info (segment->field m) m)))]
    (map-indexed (fn [position field]
                   (assoc field
                          :database-position position
                          :table-schema      schema-name
                          :table-name        (:name cube)))
                 fields)))
