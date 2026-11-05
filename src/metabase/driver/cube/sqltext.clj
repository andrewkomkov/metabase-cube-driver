(ns metabase.driver.cube.sqltext
  "A reader of SQL text with no dependency on Metabase: the lexer that follows the rules of PostgreSQL, a recursive-descent parser, and the tree with the place of each part in the text. `metabase.driver.cube.grammar` admits a text on
  this tree, and `metabase.driver.cube.correct` writes it. A text with no correction stays the same, byte for byte."
  (:require
   [clojure.string :as str])
  (:import
   (java.math BigDecimal)
   (java.time LocalDate LocalDateTime OffsetDateTime ZoneId ZoneOffset)
   (java.time.format DateTimeFormatter)))

(set! *warn-on-reflection* true)

;;; The lexer. It reads the text as the parser of Cube does, which has the rules of PostgreSQL: a string with '' or, with the prefix E, also a backslash; a quoted name with "";
;;; a string in dollar quotes; a comment with -- or /* */, and these comments nest. A text that the lexer cannot read has a reason in :unreadable, and the grammar refuses it.

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
     :form  kind
     :text  (case kind
              :plain  (str/replace (subs inner 1 (dec (count inner))) "''" "'")
              :escape (-> (subs inner 2 (dec (count inner)))
                          (str/replace "''" "'")
                          (str/replace #"\\(.)" "$1")))}))

(defn lex
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

            ;; A text with Unicode escapes. `without-unicode-escapes` writes it in plain form first, and a form with UESCAPE stays here.
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
                           (conj out {:kind :string, :form :dollar, :text (subs sql (+ i (count tag)) at), :start i, :end end})
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

(defn- decode-unicode
  "The text of a U& string or name: \\XXXX, \\+XXXXXX and \\\\. Nil for another escape."
  [^String inner]
  (try
    (let [out (StringBuilder.)]
      (loop [i 0]
        (if (>= i (count inner))
          (.toString out)
          (let [c (.charAt inner i)]
            (cond
              (not= c \\) (do (.append out c) (recur (inc i)))
              (.startsWith inner "\\\\" i) (do (.append out \\) (recur (+ i 2)))
              (.startsWith inner "\\+" i) (do (.appendCodePoint out (Integer/parseInt (subs inner (+ i 2) (+ i 8)) 16)) (recur (+ i 8)))
              :else (do (.appendCodePoint out (Integer/parseInt (subs inner (inc i) (+ i 5)) 16)) (recur (+ i 5))))))))
    (catch Exception _ nil)))

(defn without-unicode-escapes
  "`sql` with each U&'x' and U&\"x\" in plain form, as a real parser prints it, so a name has one spelling. A form that this cannot read stays, and the lexer stops on it."
  [^String sql]
  (loop [at 0, out (StringBuilder.)]
    (let [{:keys [tokens]} (lex (subs sql at))
          next-end         (:end (peek tokens))
          stop             (+ at (long (or next-end 0)))
          found            (when (< stop (count sql))
                             (let [matcher (re-matcher #"(?s)\s*(?:--[^\n]*\n|/\*.*?\*/\s*)*[Uu]&(['\"])" sql)]
                               (.region matcher (int stop) (count sql))
                               (when (.lookingAt matcher) [(.end matcher) (first (.group matcher 1))])))]
      (if-let [[quote-end quote-char] found]
        (let [end     (quoted-end sql (dec quote-end) quote-char false)
              decoded (when end (decode-unicode (str/replace (subs sql quote-end (dec end)) (str quote-char quote-char) (str quote-char))))]
          (if decoded
            (do (.append out (subs sql at (- quote-end 3)))
                (.append out quote-char)
                (.append out (str/replace decoded (str quote-char) (str quote-char quote-char)))
                (.append out quote-char)
                (recur (long end) out))
            (do (.append out (subs sql at)) (.toString out))))
        (do (.append out (subs sql at)) (.toString out))))))

(defn tokens
  "The tokens of `sql` as a vector, with no comments. Each `?` has the number of its query parameter."
  [sql]
  (first
   (reduce (fn [[out params] {:keys [text kind] :as tok}]
             (if (and (= :other kind) (= text "?"))
               [(conj out (assoc tok :kind :param, :param params)) (inc params)]
               [(conj out tok) params]))
           [[] 0]
           (:tokens (lex sql)))))

;;; The parser. It reads a query into a tree of maps. Each map has :k, its kind, and :s and :e, its place in the text. A value of a map that is a map with :k, or a vector of such maps, is
;;; a part of the node. `emit` writes a node: the text of the source, with the text of each part that a correction changed. So a correction sets :text on a node and the rest stays the same.

(def ^:private ^:dynamic *toks* nil)
(def ^:private ^:dynamic *at* nil)
(def ^:private ^:dynamic *special-args*
  "A volatile that is true if a call has a word between its arguments or ORDER BY. These have a form that the grammar does not read."
  (volatile! nil))

(defn- parse-error
  [reason]
  (throw (ex-info reason {::parse-error true})))

(defn- tok
  ([] (tok 0))
  ([offset] (get *toks* (+ (long @*at*) (long offset)))))

(defn- advance!
  []
  (let [t (tok)]
    (vswap! *at* inc)
    t))

(defn- word-at?
  ([text] (word-at? 0 text))
  ([offset text]
   (let [t (tok offset)]
     (and (= :word (:kind t)) (= text (:text t))))))

(defn- other-at?
  ([text] (other-at? 0 text))
  ([offset text]
   (let [t (tok offset)]
     (and (= :other (:kind t)) (= text (:text t))))))

(defn- accept-word!
  [text]
  (when (word-at? text) (advance!)))

(defn- accept-other!
  [text]
  (when (other-at? text) (advance!)))

(defn- expect-word!
  [text]
  (or (accept-word! text) (parse-error (str "expected " text " at " (:text (tok))))))

(defn- expect-other!
  [text]
  (or (accept-other! text) (parse-error (str "expected " text " at " (:text (tok))))))

(defn- prev-end
  []
  (:end (get *toks* (dec (long @*at*)))))

(defn- start-of-tok
  []
  (or (:start (tok)) (parse-error "unexpected end of the text")))

(defn- finish
  [start node]
  (assoc node :s start :e (prev-end)))

(def ^:private reserved
  "Words that cannot be a name or an alias with no quotes."
  #{"select" "from" "where" "group" "having" "order" "limit" "offset" "fetch" "union" "intersect" "except" "on" "using" "join" "inner" "left" "right" "full" "cross" "natural"
    "and" "or" "not" "in" "between" "like" "ilike" "similar" "is" "as" "case" "when" "then" "else" "end" "distinct" "all" "null" "true" "false" "over" "filter" "window" "with"
    "asc" "desc" "nulls" "exists" "for" "cast" "lateral" "values" "by" "isnull" "notnull" "any" "some"})

(defn- name-tok?
  [t]
  (or (= :ident (:kind t))
      (and (= :word (:kind t)) (not (contains? reserved (:text t))))))

(defn- type-name!
  "A type, as `double precision`, `numeric(12, 2)` or `timestamp with time zone`. Returns its words."
  []
  (let [first-word (:text (advance!))]
    (when-not first-word (parse-error "a type with no name"))
    (when (other-at? "(")
      (loop [depth 0]
        (let [t (advance!)]
          (when-not t (parse-error "a type with no end"))
          (let [depth (case (:text t) "(" (inc depth) ")" (dec depth) depth)]
            (when (pos? depth) (recur (long depth)))))))
    (cond
      (and (= first-word "double") (word-at? "precision"))
      (do (advance!) "double precision")

      (and (contains? #{"timestamp" "time"} first-word) (or (word-at? "with") (word-at? "without")))
      (do (advance!) (advance!) (advance!) first-word)

      :else first-word)))

(declare parse-query parse-expr parse-add)

(defn- subquery-at?
  "True if the parenthesis at the current token opens a query."
  []
  (or (word-at? 1 "select") (word-at? 1 "with") (word-at? 1 "values")))

(defn- skip-parens!
  "Reads the balanced parentheses at the current token, and gives the span. A window has no correction here."
  []
  (let [start (start-of-tok)]
    (loop [depth 0]
      (let [t (advance!)]
        (when-not t (parse-error "parentheses with no end"))
        (let [depth (if (= :other (:kind t))
                      (case (:text t) "(" (inc depth) ")" (dec depth) depth)
                      depth)]
          (when (pos? depth) (recur (long depth))))))
    {:s start :e (prev-end)}))

(defn- skip-frame!
  "Reads the words of a window frame, up to the parenthesis that closes the window."
  []
  (loop [depth 0]
    (let [t (tok)]
      (when-not t (parse-error "parentheses with no end"))
      (cond
        (and (zero? depth) (other-at? ")")) nil
        (other-at? "(") (do (advance!) (recur (inc depth)))
        (other-at? ")") (do (advance!) (recur (dec depth)))
        :else (do (advance!) (recur depth))))))

(defn- parse-order-item!
  []
  (let [start (start-of-tok)
        x     (parse-expr)
        dir   (cond (accept-word! "asc") :asc (accept-word! "desc") :desc)
        nulls (when (accept-word! "nulls")
                (cond (accept-word! "first") :first
                      (accept-word! "last")  :last
                      :else                  (parse-error "NULLS with no FIRST or LAST")))]
    (finish start {:k :order-item, :x x, :dir dir, :nulls nulls})))

(defn- parse-order-by!
  "The items after ORDER BY."
  []
  (expect-word! "by")
  (loop [out [(parse-order-item!)]]
    (if (accept-other! ",")
      (recur (conj out (parse-order-item!)))
      out)))

(defn- parse-over!
  "The window of a call, after OVER. A frame or a named window has no meaning that the grammar can read, so it has a mark."
  []
  (let [start (start-of-tok)]
    (if-not (accept-other! "(")
      (do (advance!) (finish start {:k :over, :named? true}))
      (let [partition (when (word-at? "partition")
                        (advance!)
                        (expect-word! "by")
                        (loop [out [(parse-expr)]]
                          (if (accept-other! ",")
                            (recur (conj out (parse-expr)))
                            out)))
            order     (when (accept-word! "order") (parse-order-by!))
            frame?    (not (other-at? ")"))]
        (when frame? (skip-frame!))
        (expect-other! ")")
        (finish start {:k :over, :partition partition, :order order, :frame? frame?})))))

(defn- parse-args!
  "The arguments of a function, after its parenthesis. A word like FROM, FOR, AS or USING can stand between them. ORDER BY in an aggregation is read."
  []
  (loop [out []]
    (cond
      (other-at? ")") out
      :else
      (let [x   (if (and (other-at? "*") (or (other-at? 1 ")") (other-at? 1 ",")))
                  (let [t (advance!)] {:k :star, :s (:start t), :e (:end t)})
                  (parse-expr))
            out (conj out x)]
        (cond
          (accept-other! ",") (recur out)
          (some word-at? ["from" "for" "using"]) (do (vreset! *special-args* true) (advance!) (recur out))
          (word-at? "as") (do (vreset! *special-args* true) (advance!) (type-name!) (recur out))
          (word-at? "order") (do (vreset! *special-args* true) (advance!) (parse-order-by!) (recur out))
          :else out)))))

(defn- parse-call-body!
  [start fn-name]
  (let [distinct? (boolean (accept-word! "distinct"))
        all?      (boolean (accept-word! "all"))
        trim-word (when (contains? #{"trim" "btrim" "ltrim" "rtrim"} fn-name)
                    (or (accept-word! "both") (accept-word! "leading") (accept-word! "trailing")))
        args      (if (= "position" fn-name)
                    (let [needle (parse-add)]
                      (expect-word! "in")
                      [needle (parse-add)])
                    (parse-args!))
        _         (expect-other! ")")
        args-end  (prev-end)
        filter-at (when (and (word-at? "filter") (other-at? 1 "(")) (start-of-tok))
        where     (when filter-at
                    (advance!)
                    (expect-other! "(")
                    (expect-word! "where")
                    (let [x (parse-expr)]
                      (expect-other! ")")
                      x))
        filter-end (when filter-at (prev-end))
        window    (when (accept-word! "over") (parse-over!))
        node      (finish start {:k :call, :name fn-name, :args args, :distinct? distinct?, :args-end args-end, :over? (boolean window), :over window
                                 :all? all?, :trim-word (boolean trim-word), :special-args? (boolean @*special-args*)})]
    (cond-> node
      filter-at (assoc :filter where :filter-at filter-at :filter-end filter-end))))

(defn- parse-call!
  [start fn-name]
  (expect-other! "(")
  (binding [*special-args* (volatile! nil)]
    (parse-call-body! start fn-name)))

(defn- parse-case!
  [start]
  (let [operand (when-not (word-at? "when") (parse-expr))
        whens   (loop [out []]
                  (if (accept-word! "when")
                    (let [c (parse-expr)
                          _ (expect-word! "then")
                          r (parse-expr)]
                      (recur (conj out {:k :when, :s (:s c), :e (:e r), :c c, :r r})))
                    out))
        other   (when (accept-word! "else") (parse-expr))]
    (expect-word! "end")
    (finish start {:k :case, :operand operand, :whens whens, :else other})))

(defn- parse-primary!
  []
  (let [start (start-of-tok)
        t     (tok)
        kind  (:kind t)
        text  (:text t)]
    (cond
      (:number? t) (do (advance!) (finish start {:k :num, :v text}))
      (= :string kind) (do (advance!) (finish start {:k :str, :v text, :form (:form t)}))
      (= :param kind) (do (advance!) (finish start {:k :param, :n (:param t)}))

      (= "(" (when (= :other kind) text))
      (if (subquery-at?)
        (do (advance!)
            (let [q (parse-query)]
              (expect-other! ")")
              (finish start {:k :sub, :q q})))
        (do (advance!)
            (let [x (parse-expr)]
              (if (other-at? ",")
                (let [items (loop [out [x]]
                              (if (accept-other! ",")
                                (recur (conj out (parse-expr)))
                                out))]
                  (expect-other! ")")
                  (finish start {:k :tuple, :items items}))
                (do (expect-other! ")")
                    (finish start {:k :paren, :x x}))))))

      (= :other kind) (if (= "$" (first text))
                        (do (advance!) (finish start {:k :param, :n nil}))
                        (parse-error (str "cannot read " text)))

      (= "null" (when (= :word kind) text)) (do (advance!) (finish start {:k :null}))
      (contains? #{"true" "false"} (when (= :word kind) text)) (do (advance!) (finish start {:k :bool, :v text}))
      (= "case" (when (= :word kind) text)) (do (advance!) (parse-case! start))

      (= "cast" (when (= :word kind) text))
      (do (advance!)
          (expect-other! "(")
          (let [x (parse-expr)]
            (expect-word! "as")
            (let [type-words (type-name!)]
              (expect-other! ")")
              (finish start {:k :cast, :x x, :type type-words}))))

      (= "exists" (when (= :word kind) text))
      (do (advance!)
          (expect-other! "(")
          (let [q (parse-query)]
            (expect-other! ")")
            (finish start {:k :exists, :q q})))

      (and (= :word kind) (= "interval" text) (= :string (:kind (tok 1))))
      (let [value (:text (tok 1))]
        (advance!) (advance!)
        (let [unit (when (and (= :word (:kind (tok))) (contains? #{"second" "minute" "hour" "day" "week" "month" "quarter" "year"} (:text (tok))))
                     (:text (advance!)))]
          (finish start {:k :interval, :v value, :unit unit})))

      (and (= :word kind) (contains? #{"timestamp" "timestamptz" "date" "time"} text) (= :string (:kind (tok 1))))
      (let [value (:text (tok 1))]
        (advance!) (advance!)
        (finish start {:k :typed, :type text, :v value}))

      (or (= :ident kind) (and (= :word kind) (not (contains? reserved text))))
      (do (advance!)
          (loop [parts [text]]
            (cond
              (and (other-at? ".") (other-at? 1 "*"))
              (do (advance!) (advance!) (finish start {:k :star, :parts parts}))

              (and (other-at? ".") (contains? #{:word :ident} (:kind (tok 1))))
              (do (advance!) (recur (conj parts (:text (advance!)))))

              (other-at? "(")
              (parse-call! start (peek parts))

              :else
              (finish start {:k :name, :parts parts, :quoted? (= :ident kind)}))))

      ;; A few reserved words are also functions: LEFT, RIGHT and others with a parenthesis after them.
      (and (= :word kind) (other-at? 1 "(") (contains? #{"left" "right" "any" "some" "values"} text))
      (do (advance!) (parse-call! start text))

      :else (parse-error (str "cannot read " text)))))

(defn- parse-postfix!
  []
  (let [start (start-of-tok)]
    (loop [x (parse-primary!)]
      (cond
        (and (other-at? ":") (other-at? 1 ":"))
        (do (advance!) (advance!)
            (let [type-words (type-name!)]
              (recur (finish start {:k :cast, :x x, :type type-words, :colons? true}))))

        (and (word-at? "at") (word-at? 1 "time") (word-at? 2 "zone"))
        (do (advance!) (advance!) (advance!)
            (let [zone (parse-primary!)]
              (recur (finish start {:k :at-zone, :x x, :zone zone}))))

        :else x))))

(defn- parse-unary!
  []
  (let [start (start-of-tok)]
    (if (or (other-at? "-") (other-at? "+"))
      (let [op (:text (advance!))
            x  (parse-unary!)]
        (finish start {:k :neg, :op op, :x x}))
      (parse-postfix!))))

(defn- parse-mul!
  []
  (let [start (start-of-tok)]
    (loop [l (parse-unary!)]
      (cond
        (other-at? "/") (do (advance!)
                            (let [r (parse-unary!)]
                              (recur (finish start {:k :div, :l l, :r r}))))
        (or (other-at? "*") (other-at? "%")) (let [op (:text (advance!))
                                                   r  (parse-unary!)]
                                               (recur (finish start {:k :bin, :op op, :l l, :r r})))
        :else l))))

(defn parse-add
  "An expression with + - || and with an operator that this parser has no rule for."
  []
  (let [start (start-of-tok)]
    (loop [l (parse-mul!)]
      (cond
        (or (other-at? "+") (other-at? "-"))
        (let [op (:text (advance!))
              r  (parse-mul!)]
          (recur (finish start {:k :bin, :op op, :l l, :r r})))

        (and (other-at? "|") (other-at? 1 "|"))
        (do (advance!) (advance!)
            (let [r (parse-mul!)]
              (recur (finish start {:k :bin, :op "||", :l l, :r r}))))

        :else l))))

(def ^:private comparison
  #{"=" "<>" "!=" "<" "<=" ">" ">="})

(defn- comparison-op
  "The comparison at the current token, with `< >` in one token or two. Nil if there is none."
  []
  (let [t (tok)]
    (when (= :other (:kind t))
      (cond
        (contains? comparison (:text t)) (:text t)
        (= "~" (:text t))                "~"
        (and (= "!" (:text t)) (other-at? 1 "~")) "!~"))))

(defn- parse-pred!
  []
  (let [start (start-of-tok)
        l     (parse-add)
        negated (and (word-at? "not")
                     (some #(word-at? 1 %) ["in" "between" "like" "ilike" "similar"]))
        _     (when negated (advance!))]
    (cond
      (accept-word! "in")
      (do (expect-other! "(")
          (if (or (word-at? "select") (word-at? "with") (word-at? "values"))
            (let [q (parse-query)]
              (expect-other! ")")
              (finish start {:k :in, :not? negated, :x l, :q q}))
            (let [items (loop [out [(parse-expr)]]
                          (if (accept-other! ",")
                            (recur (conj out (parse-expr)))
                            out))]
              (expect-other! ")")
              (finish start {:k :in, :not? negated, :x l, :items items}))))

      (accept-word! "between")
      (let [symmetric? (boolean (accept-word! "symmetric"))
            lo  (parse-add)
            _   (expect-word! "and")
            hi  (parse-add)]
        (finish start {:k :between, :not? negated, :symmetric? symmetric?, :x l, :lo lo, :hi hi}))

      (or (word-at? "like") (word-at? "ilike"))
      (let [op (:text (advance!))
            p  (parse-add)
            esc (when (accept-word! "escape") (parse-primary!))]
        (finish start {:k :like, :not? negated, :op op, :x l, :p p, :esc esc}))

      (word-at? "similar")
      (do (advance!) (expect-word! "to")
          (let [p (parse-add)]
            (finish start {:k :similar, :not? negated, :x l, :p p})))

      (accept-word! "is")
      (let [is-not (boolean (accept-word! "not"))]
        (cond
          (accept-word! "null") (finish start {:k :is, :not? is-not, :what "null", :x l})
          (accept-word! "true") (finish start {:k :is, :not? is-not, :what "true", :x l})
          (accept-word! "false") (finish start {:k :is, :not? is-not, :what "false", :x l})
          (accept-word! "unknown") (finish start {:k :is, :not? is-not, :what "unknown", :x l})
          (accept-word! "distinct") (do (expect-word! "from")
                                        (let [r (parse-add)]
                                          (finish start {:k :is-distinct, :not? is-not, :x l, :r r})))
          :else (parse-error "IS with no value")))

      (comparison-op)
      (let [op (comparison-op)]
        (advance!)
        (when (= op "!~") (advance!))
        (when (contains? #{"~" "!~"} op) (accept-other! "*"))
        (let [quantified (when (and (or (word-at? "any") (word-at? "all") (word-at? "some")) (other-at? 1 "("))
                           (:text (advance!)))
              r (parse-add)]
          (finish start {:k :cmp, :op op, :l l, :r r, :quantified quantified})))

      :else l)))

(defn- parse-not!
  []
  (let [start (start-of-tok)]
    (if (accept-word! "not")
      (let [x (parse-not!)]
        (finish start {:k :not, :x x}))
      (parse-pred!))))

(defn- parse-and!
  []
  (let [start (start-of-tok)]
    (loop [l (parse-not!)]
      (if (accept-word! "and")
        (let [r (parse-not!)]
          (recur (finish start {:k :and, :l l, :r r})))
        l))))

(defn parse-expr
  []
  (let [start (start-of-tok)]
    (loop [l (parse-and!)]
      (if (accept-word! "or")
        (let [r (parse-and!)]
          (recur (finish start {:k :or, :l l, :r r})))
        l))))

(defn- alias!
  "An alias after a table, a subquery or a column."
  []
  (cond
    (accept-word! "as") (let [t (advance!)]
                          (when-not (contains? #{:word :ident} (:kind t))
                            (parse-error "AS with no name"))
                          (:text t))
    (name-tok? (tok))   (:text (advance!))))

(defn- skip-column-list!
  []
  (when (other-at? "(")
    (skip-parens!)))

(declare parse-from!)

(defn- parse-from-item!
  []
  (let [start    (start-of-tok)
        lateral? (boolean (or (accept-word! "lateral") (accept-word! "only")))
        finish   (fn [at node] (finish at (assoc node :lateral? lateral?)))]
    (cond
      (and (other-at? "(") (subquery-at?))
      (do (advance!)
          (let [q (parse-query)]
            (expect-other! ")")
            (let [a       (alias!)
                  listed? (boolean (skip-column-list!))]
              (finish start {:k :from-sub, :q q, :alias a, :column-list? listed?}))))

      (other-at? "(")
      (do (advance!)
          (let [inner (parse-from!)]
            (expect-other! ")")
            (let [a (alias!)]
              (finish start {:k :from-paren, :items inner, :alias a}))))

      (or (name-tok? (tok)) (= :ident (:kind (tok))))
      (let [parts (loop [parts [(:text (advance!))]]
                    (if (and (other-at? ".") (contains? #{:word :ident} (:kind (tok 1))))
                      (do (advance!) (recur (conj parts (:text (advance!)))))
                      parts))]
        (if (other-at? "(")
          (let [call (parse-call! start (peek parts))
                a    (alias!)]
            (skip-column-list!)
            (finish start {:k :from-fn, :call call, :alias a}))
          (let [a       (alias!)
                listed? (boolean (skip-column-list!))]
            (finish start {:k :from-table, :parts parts, :table (peek parts), :alias a, :column-list? listed?}))))

      :else (parse-error (str "cannot read the FROM at " (:text (tok)))))))

(defn- parse-joined!
  "A from item with each JOIN after it."
  []
  (let [start (start-of-tok)]
    (loop [l (parse-from-item!)]
      (let [natural (accept-word! "natural")
            kind (cond
                   (accept-word! "cross") :cross
                   (accept-word! "inner") :inner
                   (accept-word! "left")  (do (accept-word! "outer") :left)
                   (accept-word! "right") (do (accept-word! "outer") :right)
                   (accept-word! "full")  (do (accept-word! "outer") :full))]
        (if (or kind (word-at? "join"))
          (do (expect-word! "join")
              (let [r     (parse-from-item!)
                    on    (when (accept-word! "on") (parse-expr))
                    using (when (word-at? "using")
                            (advance!)
                            (skip-parens!))]
                (recur (finish start {:k :join, :type (or kind :inner), :l l, :r r, :on on, :using using, :natural? (boolean natural)}))))
          l)))))

(defn- parse-from!
  []
  (loop [out [(parse-joined!)]]
    (if (accept-other! ",")
      (recur (conj out (parse-joined!)))
      out)))

(defn- parse-group-item!
  []
  (let [start (start-of-tok)]
    (cond
      (and (word-at? "grouping") (word-at? 1 "sets"))
      (do (advance!) (advance!)
          (skip-parens!)
          (finish start {:k :group-set, :form "GROUPING SETS"}))

      (and (word-at? "rollup") (other-at? 1 "("))
      (do (advance!) (skip-parens!) (finish start {:k :group-set, :form "ROLLUP"}))

      (and (word-at? "cube") (other-at? 1 "("))
      (do (advance!) (skip-parens!) (finish start {:k :group-set, :form "CUBE"}))

      :else (parse-expr))))

(defn- parse-select-item!
  []
  (let [start (start-of-tok)]
    (if (and (other-at? "*") (or (other-at? 1 ",") (word-at? 1 "from") (nil? (tok 1)) (other-at? 1 ")") (other-at? 1 ";")))
      (let [t (advance!)]
        {:k :item, :s (:start t), :e (:end t), :x {:k :star, :s (:start t), :e (:end t)}})
      (let [x (parse-expr)
            a (alias!)]
        (finish start {:k :item, :x x, :alias a})))))

(defn- parse-select!
  []
  (let [start (start-of-tok)]
    (expect-word! "select")
    (let [distinct-on (when (and (word-at? "distinct") (word-at? 1 "on"))
                        (advance!) (advance!)
                        (skip-parens!)
                        true)
          distinct?   (boolean (accept-word! "distinct"))
          all?        (boolean (accept-word! "all"))
          items       (if (or (word-at? "from") (nil? (tok)))
                        []
                        (loop [out [(parse-select-item!)]]
                          (if (accept-other! ",")
                            (recur (conj out (parse-select-item!)))
                            out)))
          from        (when (accept-word! "from") (parse-from!))
          where       (when (accept-word! "where") (parse-expr))
          group-all?  (atom false)
          group       (when (word-at? "group")
                        (advance!)
                        (expect-word! "by")
                        (when (accept-word! "all") (reset! group-all? true))
                        (loop [out [(parse-group-item!)]]
                          (if (accept-other! ",")
                            (recur (conj out (parse-group-item!)))
                            out)))
          having      (when (accept-word! "having") (parse-expr))]
      (when (word-at? "window")
        (parse-error "WINDOW"))
      (finish start {:k :select, :distinct-on? (boolean distinct-on), :distinct? distinct?, :all? all?, :group-all? @group-all?, :items items, :from from, :where where, :group group, :having having}))))

(defn- parse-set-term!
  []
  (let [start (start-of-tok)]
    (cond
      (word-at? "select") (parse-select!)
      (word-at? "values") (do (advance!)
                              (loop []
                                (when (other-at? "(") (skip-parens!))
                                (when (accept-other! ",") (recur)))
                              (finish start {:k :values}))
      (other-at? "(")     (do (advance!)
                              (let [q (parse-query)]
                                (expect-other! ")")
                                (finish start {:k :sub, :q q})))
      :else               (parse-error (str "cannot read " (:text (tok)))))))

(defn- parse-with!
  []
  (when (accept-word! "with")
    (loop [out [], recursive? (boolean (accept-word! "recursive"))]
      (let [start (start-of-tok)
            t     (advance!)]
        (when-not (contains? #{:word :ident} (:kind t))
          (parse-error "WITH with no name"))
        (let [listed?   (boolean (skip-column-list!))
              _         (expect-word! "as")
              modifier? (boolean (or (when (word-at? "not") (advance!)) (accept-word! "materialized")))
              _         (accept-word! "materialized")
              _         (expect-other! "(")
              q         (parse-query)
              _         (expect-other! ")")
              out       (conj out (finish start {:k :cte, :name (:text t), :q q, :recursive? recursive?, :column-list? listed?, :modifier? modifier?}))]
          (if (accept-other! ",")
            (recur out recursive?)
            out))))))

(defn parse-query
  []
  (let [fetch? (volatile! false)
        start (start-of-tok)
        withs (parse-with!)
        term  (parse-set-term!)
        terms (loop [out [term], ops []]
                (let [op (cond
                           (word-at? "union") "union"
                           (word-at? "intersect") "intersect"
                           (word-at? "except") "except")]
                  (if op
                    (do (advance!)
                        (let [all? (boolean (accept-word! "all"))]
                          (accept-word! "distinct")
                          (recur (conj out (parse-set-term!)) (conj ops {:op op :all? all?}))))
                    {:terms out :ops ops})))
        order (when (accept-word! "order") (parse-order-by!))
        limit (when (accept-word! "limit")
                (if (accept-word! "all") nil (parse-add)))
        offset (when (accept-word! "offset")
                 (let [x (parse-add)]
                   (or (accept-word! "rows") (accept-word! "row"))
                   x))
        limit2 (when (accept-word! "limit") (parse-add))]
    (when (word-at? "fetch")
      (let [fetch-start (start-of-tok)]
        (advance!)
        (or (accept-word! "first") (accept-word! "next"))
        (let [amount (when-not (or (word-at? "row") (word-at? "rows")) (parse-add))]
          (or (accept-word! "row") (accept-word! "rows"))
          (when-not (accept-word! "only") (parse-error "FETCH with no ONLY"))
          (vreset! fetch? {:s fetch-start, :e (prev-end), :amount amount}))))
    (finish start {:k :query, :withs withs, :terms (:terms terms), :ops (:ops terms), :order order, :limit (or limit limit2), :offset offset, :fetch? @fetch?})))

(defn parse
  "The tree of `sql`, or {:error reason} if the parser cannot read it."
  [sql]
  (let [{:keys [unreadable]} (lex sql)]
    (if unreadable
      {:error unreadable}
      (binding [*toks* (tokens sql)
                *at*   (volatile! 0)]
        (try
          (let [tree (parse-query)]
            (accept-other! ";")
            (if (tok)
              {:error (str "cannot read the text at " (:text (tok)))}
              tree))
          (catch clojure.lang.ExceptionInfo e
            (if (::parse-error (ex-data e))
              {:error (ex-message e)}
              (throw e))))))))

;;; The tree.

(defn node?
  [x]
  (and (map? x) (keyword? (:k x))))

(defn kids
  "The parts of a node, in the order of the text."
  [node]
  (->> (vals node)
       (mapcat (fn [v]
                 (cond
                   (node? v)   [v]
                   (vector? v) (filter node? v)
                   :else       nil)))
       (sort-by :s)))

(defn walk
  "Applies `f` to each part of `node`, first the parts of a part, and then to `node`."
  [f node]
  (f (reduce-kv (fn [out key v]
                  (cond
                    (node? v)   (assoc out key (walk f v))
                    (and (vector? v) (some node? v))
                    (assoc out key (mapv #(if (node? %) (walk f %) %) v))
                    :else out))
                node
                node)))

(defn nodes
  "`node` and each part of it, also inside a subquery."
  [node]
  (tree-seq node? kids node))

(defn emit
  "The text of `node`: the source, with the new text of each part that has one."
  [^String sql node]
  (or (:text node)
      (let [out (StringBuilder.)
            end (reduce (fn [at kid]
                          (.append out (subs sql at (:s kid)))
                          (.append out ^String (emit sql kid))
                          (long (:e kid)))
                        (long (:s node))
                        (kids node))]
        (.append out (subs sql end (:e node)))
        (.toString out))))

(defn plain-number
  "The value of a number text as a BigDecimal, or nil."
  ^BigDecimal [^String text]
  (try
    (BigDecimal. (str/trim text))
    (catch NumberFormatException _ nil)))

;;; A time in the text.

(defn parse-time
  "A time in a text: {:time the clock as written, :offset the offset if the text has one, :date? true for a date with no time}, or nil."
  [^String text]
  (let [text (str/trim text)]
    (try
      (cond
        (re-matches #"\d{4}-\d{2}-\d{2}" text)
        {:time (.atStartOfDay (LocalDate/parse text)), :date? true}

        (re-find #"(Z|[+-]\d{2}(:?\d{2})?)$" text)
        (let [value (OffsetDateTime/parse (str/replace (str/replace text " " "T") #"([+-]\d{2})$" "$1:00"))]
          {:time (.toLocalDateTime value), :offset (.getOffset value)})

        :else
        {:time (LocalDateTime/parse (str/replace text " " "T"))})
      (catch Exception _ nil))))

(defn time-fraction?
  [{^LocalDateTime value :time}]
  (and value (not (zero? (.getNano value)))))

(def ^:private ^DateTimeFormatter whole-second-format (DateTimeFormatter/ofPattern "yyyy-MM-dd HH:mm:ss"))
(def ^:private ^DateTimeFormatter fraction-format (DateTimeFormatter/ofPattern "yyyy-MM-dd HH:mm:ss.SSS"))

(defn time-in-zone
  "The clock of this time in `zone`, as a text for TIMESTAMP. A time with no offset is a time of `written-zone`."
  ^String [{^LocalDateTime value :time, ^ZoneOffset offset :offset} ^ZoneId written-zone ^ZoneId zone]
  (let [clock (.toLocalDateTime (.withZoneSameInstant (if offset
                                                        (.atZone value offset)
                                                        (.atZone value written-zone))
                                                      zone))]
    (.format (if (zero? (.getNano clock)) whole-second-format fraction-format) clock)))
