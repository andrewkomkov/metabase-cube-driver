;; Reads a file of native SQL, one JSON string on each line, and writes for each one if the grammar admits it, with no Cube and no Metabase server. See dev/fuzz/README.md.
;; Usage: java -cp <src>:<metabase.jar> clojure.main admit.clj <meta.json> <queries.jsonl> [dialect|stock] [zone]
(require '[clojure.java.io :as io]
         '[metabase.util.json :as json]
         '[metabase.driver.cube.grammar :as grammar]
         '[metabase.driver.cube.native :as native])

(let [[meta-file queries-file variant zone] *command-line-args*
      cube-meta (json/decode+kw (slurp meta-file))
      opts      {:keeps-fractions? (not= "stock" variant), :zone? (some? zone)}]
  (with-open [reader (io/reader queries-file)]
    (doseq [line (line-seq reader)]
      (let [sql (json/decode line)]
        (println (json/encode
                  (try
                    (let [{:keys [used], text :sql} (native/admit-native cube-meta sql [] opts)]
                      {:admitted true, :sql text, :used (sort (map #(subs (str %) 1) used))})
                    (catch clojure.lang.ExceptionInfo e
                      {:admitted false, :refused (:cube/refused (ex-data e)), :message (ex-message e)})
                    (catch Exception e
                      {:admitted false, :crash true, :message (str e)})))))))
  (shutdown-agents))
