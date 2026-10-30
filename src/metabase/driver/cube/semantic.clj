(ns metabase.driver.cube.semantic
  "Makes the Cube data model into Metabase objects. It runs after each metadata sync, on `:event/sync-metadata-end`.

  Tables and fields: this namespace writes titles, descriptions, semantic types and format settings to `metabase_table`
  and `metabase_field`. Metabase keeps edits of a user in the user-settings tables and shows them over these columns, so
  a write here never hides an edit.

  Measures and segments: each public Cube measure and segment gets one Measure or Segment. The creator is the Metabase
  internal user. The key is a fixed `entity_id` made from the database and the member name. Metabase has no user-settings
  table for these objects. An edit through the API always adds a Revision, and a write here does not, so a Revision means
  that a person changed the object, and this namespace keeps the content of that object. See docs/semantic-sync.md."
  (:require
   [clojure.string :as str]
   [metabase.config.core :as config]
   [metabase.driver.cube.identity :as cube.identity]
   [metabase.driver.cube.meta :as cube.meta]
   [metabase.events.core :as events]
   [metabase.util.log :as log]
   [methodical.core :as methodical]
   [toucan2.core :as t2])
  (:import
   (java.nio.charset StandardCharsets)
   (java.security MessageDigest)
   (java.util Base64)))

(set! *warn-on-reflection* true)

(defn- member-entity-id
  "A fixed entity_id: 21 URL-safe characters from a hash of the database and the member name."
  [database member-name]
  (let [digest (.digest (MessageDigest/getInstance "SHA-256")
                        (.getBytes (str "cube/" (:entity_id database) "/" member-name) StandardCharsets/UTF_8))]
    (subs (.encodeToString (.withoutPadding (Base64/getUrlEncoder)) digest) 0 21)))

;;; --------------------------------------------------- tables and fields ---------------------------------------------------

(defn- update-when-changed!
  "Write only the columns that changed. Returns true if it wrote."
  [model id current wanted]
  (let [changes (into {} (remove (fn [[k v]] (= v (get current k)))) wanted)]
    (when (seq changes)
      (t2/update! model id changes)
      true)))

(defn- wanted-semantic-type
  "A format sets the semantic type. Without a format, this removes only a type that a format set earlier: a type from
  the Metabase classifier stays, and a primary or foreign key stays."
  [{:keys [semantic_type fk_target_field_id]} {:keys [semantic-type]}]
  (cond
    (or fk_target_field_id (= semantic_type :type/PK))     semantic_type
    semantic-type                                          semantic-type
    (cube.meta/managed-semantic-types semantic_type)       nil
    :else                                                  semantic_type))

(defn- sync-field!
  "With per-user data, a dimension has no stored list of values, because Metabase keeps one list for all people. A filter
  then asks Cube as the person who uses it."
  [database field {::cube.meta/keys [title kind settings], :keys [field-comment], :as member}]
  (update-when-changed!
   :model/Field (:id field) field
   (cond-> {:description   field-comment
            :semantic_type (wanted-semantic-type field member)
            :settings      settings}
     title                           (assoc :display_name title)
     (#{:measure :segment} kind)     (assoc :has_field_values :none)
     (and (= kind :dimension)
          (cube.identity/attribute (:details database))) (assoc :has_field_values :search))))

(defn- remove-stored-field-values!
  [database field-ids]
  (when (and (cube.identity/attribute (:details database)) (seq field-ids))
    (t2/delete! :model/FieldValues :field_id [:in field-ids])))

(defn- clear-stale-foreign-keys!
  "Metabase sync only adds foreign keys. This removes those whose Cube join went away."
  [database field-ids]
  (let [wanted (into #{}
                     (map (fn [{[fk-table fk-column] :fk}] [fk-table fk-column]))
                     (cube.meta/foreign-keys (:cube-meta database)))]
    (doseq [{:keys [id table_id fk_target_field_id], column :name} (when (seq field-ids)
                                                                     (t2/select :model/Field
                                                                                :id [:in field-ids]
                                                                                :fk_target_field_id [:not= nil]))
            :let  [table-name (t2/select-one-fn :name :model/Table :id table_id)]
            :when (and fk_target_field_id (not (wanted [table-name column])))]
      (t2/update! :model/Field id {:fk_target_field_id nil, :semantic_type nil}))))

;;; --------------------------------------------------- measures and segments ---------------------------------------------------

(def ^:private aggregation-of-measure
  "The aggregation clause that holds a measure. The driver compiles each of them to MEASURE(), so this clause sets only
  the text that Metabase shows and the type of the result. `:max` accepts every column type."
  {"sum"                 :sum
   "count"               :sum
   "countDistinct"       :sum
   "countDistinctApprox" :sum
   "runningTotal"        :sum
   "avg"                 :avg
   "min"                 :min
   "max"                 :max})

(defn- mbql-query
  [database table-id stage]
  {:lib/type :mbql/query
   :database (:id database)
   :stages   [(merge {:lib/type :mbql.stage/mbql, :source-table table-id} stage)]})

(defn- field-ref
  [field-id]
  [:field {:lib/uuid (str (random-uuid))} field-id])

(defn- measure-definition
  "The display name is the measure title. Without it, the column in a result is \"Sum of Revenue\"."
  [database table-id field-id title member]
  (mbql-query database table-id
              {:aggregation [[(aggregation-of-measure (:aggType member) :max)
                              {:lib/uuid (str (random-uuid)), :display-name title}
                              (field-ref field-id)]]}))

(defn- segment-definition
  [database table-id field-id _title _member]
  (mbql-query database table-id
              {:filters [[:= {:lib/uuid (str (random-uuid))} (field-ref field-id) true]]}))

(defn- as-keyword [x] (some-> x keyword))

(defn- definition-signature
  "What a definition says, without the generated UUIDs: the clause, the field and the title. Nil if it has another shape."
  [{:keys [stages]}]
  (let [{:keys [aggregation filters]} (first stages)
        clause (or (first aggregation) (first filters))
        ref    (when clause (nth clause 2 nil))]
    (when (and (vector? ref) (= :field (as-keyword (first ref))))
      [(as-keyword (first clause)) (nth ref 2) (:display-name (second clause) (get (second clause) "display-name"))])))

(def ^:private entity-kinds
  [{:kind :measure, :model :model/Measure, :revision-model "Measure", :definition measure-definition}
   {:kind :segment, :model :model/Segment, :revision-model "Segment", :definition segment-definition}])

(defn- with-owner
  "A search result for a measure or a segment shows only its name and description, and many tables can have a Revenue.
  This text tells which table it is, and gives the id that a query needs as source-table."
  [description {table-id :id, table-name :name, display-name :display_name}]
  (let [owner (format "Table: %s (%s, id %d)." (or display-name table-name) table-name table-id)]
    (if (str/blank? description)
      owner
      (str description "\n\n" owner))))

(defn- wanted-entities
  "The Measure or Segment for each public member of this kind that has a field."
  [database {:keys [kind definition]} table+members]
  (for [[table members]                            table+members
        {::cube.meta/keys [title member], :keys [field-comment field-id] :as field} members
        :when (and field-id (= kind (::cube.meta/kind field)))]
    {:entity_id   (member-entity-id database (:name member))
     :table_id    (:id table)
     :name        title
     :description (with-owner field-comment table)
     :definition  (definition database (:id table) field-id title member)}))

(def ^:private reset-message
  "A person writes this as the revision message to give an object back to Cube."
  "cube-sync:reset")

(defn- latest-revisions
  "The last Revision of each object, by object id."
  [revision-model ids]
  (when (seq ids)
    (into {}
          (map (juxt :model_id identity))
          (t2/select :model/Revision :model revision-model :model_id [:in ids] {:order-by [[:id :asc]]}))))

(defn- update-owned!
  "An object without a Revision belongs to Cube, so it follows the model."
  [model current wanted]
  (let [same-definition? (= (definition-signature (:definition current)) (definition-signature (:definition wanted)))
        changes          (cond-> {:name (:name wanted), :description (:description wanted), :archived false}
                           (not same-definition?) (assoc :definition (:definition wanted)))]
    (update-when-changed! model (:id current) current changes)))

(defn- sync-kind!
  [database {:keys [kind model revision-model] :as spec} table+members all-table-ids]
  (let [wanted    (wanted-entities database spec table+members)
        existing  (when (seq all-table-ids)
                    (t2/select model :creator_id config/internal-mb-user-id :table_id [:in all-table-ids]))
        by-key    (into {} (map (juxt :entity_id identity)) existing)
        revisions (latest-revisions revision-model (map :id existing))
        wanted?   (into #{} (map :entity_id) wanted)
        stats     (atom {:created 0, :updated 0, :archived 0, :kept 0})
        count!    #(swap! stats update % inc)]
    (doseq [{:keys [entity_id] :as entity} wanted]
      (try
        (let [current  (by-key entity_id)
              revision (when current (revisions (:id current)))]
          (cond
            (nil? current)
            (do (t2/insert! model (assoc entity :creator_id config/internal-mb-user-id))
                (count! :created))

            (or (nil? revision) (= reset-message (:message revision)))
            (when (update-owned! model current entity)
              (count! :updated))

            ;; The member came back. The last person who touched the object left it active, so this code archived it.
            (and (:archived current) (false? (get-in revision [:object :archived])))
            (do (t2/update! model (:id current) {:archived false})
                (count! :updated))

            :else
            (count! :kept)))
        (catch Throwable e
          (log/warnf e "Could not sync the Cube %s %s" (name kind) (:name entity)))))
    ;; A removed member has no field to query, so its object is archived even if a person edited it.
    (doseq [{:keys [id entity_id archived]} existing
            :when (and (not archived) (not (wanted? entity_id)))]
      (t2/update! model id {:archived true})
      (count! :archived))
    (log/infof "Cube semantic sync of database %s: %ss %s" (:id database) (name kind) (pr-str @stats))))

;;; ------------------------------------------------------- agent guidance -------------------------------------------------------

(def ^:private guidance-start
  "The start of the text below. A description with another start is from a person, and sync keeps it."
  "Cube semantic layer.")

(def ^:private guidance
  (str guidance-start " Each table is a cube or a view. Its description lists its measures, dimensions and segments, and "
       "browse_data with action get_fields gives their ids. Prefer a view if one fits the question. "
       "A measure already has its aggregation in the data model: in execute_query write [\"measure\", {}, <measure id>] in "
       "aggregation, with the table of the measure as source-table, and do not sum or count its column again. "
       "A segment is a filter: write [\"segment\", {}, <segment id>] in filters. "
       "A column of a table in related_tables works directly as [\"field\", {}, <field id>], and get_fields of that table "
       "gives the id. "
       "In execute_sql write MEASURE(name) with GROUP BY, and read a joined cube with "
       "FROM a CROSS JOIN b WHERE a.__cubeJoinField = b.__cubeJoinField."))

(defn- write-guidance!
  "`browse_data` with action list_databases returns this text. The MCP skill files are in the Metabase image, so this
  metadata is the place for guidance about Cube."
  [{:keys [id description]}]
  (when (or (str/blank? description)
            (and (str/starts-with? description guidance-start) (not= description guidance)))
    (t2/update! :model/Database id {:description guidance})))

;;; ------------------------------------------------------- entry point -------------------------------------------------------

(defn sync-semantics!
  "Bring the Metabase objects of `database` up to date with the Cube data model."
  [database]
  (write-guidance! database)
  (let [cube-meta   (cube.meta/fetch-meta (:details database))
        database    (assoc database :cube-meta cube-meta)
        cubes       (cube.meta/public-cubes cube-meta)
        name->cube  (into {} (map (juxt :name identity)) cubes)
        tables      (t2/select :model/Table :db_id (:id database))
        active      (filter :active tables)
        fields      (when (seq active)
                      (t2/select :model/Field :table_id [:in (map :id active)] :active true))
        table-id->fields (group-by :table_id fields)
        table+members
        (vec
         (for [{table-id :id, table-name :name, :as table} active
               :let  [cube (name->cube table-name)]
               :when cube]
           (let [name->field (into {} (map (juxt :name identity)) (table-id->fields table-id))]
             (update-when-changed! :model/Table table-id table
                                   (cond-> {:description (cube.meta/cube-description cube)}
                                     (not (str/blank? (:title cube))) (assoc :display_name (:title cube))))
             [table (vec (for [member (cube.meta/cube->fields cube)
                               :let  [field (name->field (:name member))]
                               :when field]
                           (assoc member :field-id (:id field))))])))]
    (clear-stale-foreign-keys! database (map :id fields))
    ;; Read the fields again, because the step above and Metabase FK sync change them.
    (let [fresh (when (seq fields) (into {} (map (juxt :id identity)) (t2/select :model/Field :id [:in (map :id fields)])))]
      (doseq [[_table members] table+members
              {:keys [field-id] :as member} members]
        (sync-field! database (fresh field-id) member))
      (remove-stored-field-values! database (keys fresh)))
    (let [table-ids (map :id tables)]
      (doseq [spec entity-kinds]
        (sync-kind! database spec table+members table-ids)))))

(events/derive! ::metadata-synced :metabase/event)
(events/derive! :event/sync-metadata-end ::metadata-synced)

(methodical/defmethod events/publish-event! ::metadata-synced
  [_topic {database-id :database_id}]
  (when-let [database (t2/select-one :model/Database :id database-id :engine "cube")]
    (try
      (sync-semantics! database)
      (catch Throwable e
        (log/warnf e "Could not sync the Cube data model to database %s" database-id)))))
