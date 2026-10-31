(ns metabase.driver.cube.identity
  "Sends the Metabase person who runs a query to Cube, as the SQL API user name.

  The connection field `cube-user-attribute` turns this on. Its value is `email`, or the name of a Metabase user
  attribute. Each query then opens its own connection with that value as the user name. Cube calls `checkSqlAuth` with
  it, and the security context of that connection comes from there.

  The driver does not use `SET user` or `__user`, for two reasons. They need `CUBEJS_SQL_SUPER_USER`, and a person who
  can write native SQL can then use `__user` to read as another person. A session of a normal user cannot switch.
  Also, `SET user` works in one direction only, so a connection pool cannot give the connection back. See docs/security.md."
  (:require
   [clojure.string :as str]
   [metabase.api.common :as api]
   [toucan2.core :as t2]))

(set! *warn-on-reflection* true)

(defn attribute
  "The value of the connection field `cube-user-attribute`, or nil if the connection has one Cube user for all people."
  [details]
  (some-> (:cube-user-attribute details) str/trim not-empty))

(defn- denied
  [message]
  (ex-info message {:type :missing-required-permissions, :status-code 403}))

(defn- attribute-value
  [attribute-name {:keys [email login_attributes jwt_attributes]}]
  (if (= attribute-name "email")
    email
    (some-> (get (merge jwt_attributes login_attributes) attribute-name) str not-empty)))

(defn sql-user
  "The SQL API user name for the current person. Nil if the connection has no `cube-user-attribute`, and nil if no person
  is signed in, which is the case for sync. An attribute with no value is an error, so the query cannot run as somebody else."
  [details]
  (when-let [attribute-name (attribute details)]
    (when-let [user-id api/*current-user-id*]
      (let [user (t2/select-one [:model/User :email :login_attributes :jwt_attributes] :id user-id)]
        (or (attribute-value attribute-name user)
            (throw (denied (format "Your Metabase account has no value for the attribute %s, which this Cube connection needs. Ask an admin to set it."
                                   attribute-name))))))))

(defn assert-person!
  "With per-user data on, a query must have a person. Public links and static embeds have none, and the account of the
  connection would show them all rows."
  [details]
  (when (and (attribute details) (nil? api/*current-user-id*))
    (throw (denied "This Cube connection gives data per person, so a query must have a signed-in person. A public link or a static embed cannot use it."))))
