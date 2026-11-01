"""Writes a spec as a Metabase query in the MBQL form of /api/dataset. It reads the ids of tables, fields, measures and segments from Metabase, through the Ctx of dev/review/run_grid.py."""
import json

import catalog as C


class InnerError(Exception):
    """Metabase stopped the first stage, when the runner asked for its columns."""


class Builder:
    def __init__(self, ctx, spec):
        self.ctx, self.spec = ctx, spec
        self.src = C.SOURCES[spec["source"]]
        self.table = self.src["table"]
        self.cols = None

    def ref(self, r):
        opts = {}
        if r.get("unit"):
            opts["temporal-unit"] = r["unit"]
        if r.get("bin"):
            opts["binning"] = {"strategy": "bin-width", "bin-width": float(r["bin"])}
        if "i" in r:
            col = self.cols[r["i"]]
            return ["field", col["name"], dict(opts, **{"base-type": col["base_type"]})]
        name = r["c"]
        if r.get("j"):
            to = self.spec["join"]["to"]
            opts["join-alias"] = "J"
            return ["field", self.ctx.field(self.src["joins"][to].get("cube", to), name)["id"], opts]
        col = self.src["cols"][name]
        if col.get("via"):
            fk = col["via"]
            field = self.ctx.field(fk, name.split(".", 1)[1])
            opts["source-field"] = self.ctx.fk(self.table, fk)["id"]
            return ["field", field["id"], opts]
        return ["field", self.ctx.field(self.table, name)["id"], opts or None]

    def filter(self, f):
        op = f["op"]
        if op in ("and", "or"):
            return [op] + [self.filter(a) for a in f["a"]]
        if op == "not":
            return ["not", self.filter(f["a"][0])]
        if op == "segment":
            return ["segment", self.ctx.ref("s:%s.%s" % (self.table, f["s"]))]
        field = self.ref(f)
        if op in ("is-null", "not-null", "is-empty", "not-empty"):
            return [op, field]
        if op == "time-interval":
            return ["time-interval", self.ref({k: v for k, v in f.items() if k in ("c", "i", "j")}), f["n"], f["unit"]]
        out = [op, field] + list(f["v"])
        if f.get("ci"):
            out.append({"case-sensitive": False})
        return out

    def agg(self, a):
        if "m" in a:
            return ["measure", self.ctx.measures[(self.table, a["m"])]]
        if a.get("op") == "/":
            return ["/", self.agg(a["a"]), self.agg(a["b"])]
        f = a["f"]
        if f == "count" and "c" not in a and "i" not in a:
            return ["count"]
        if f == "count-where" or f == "share":
            return [f, self.filter(a["p"])]
        field = self.ref({k: v for k, v in a.items() if k in ("c", "i")})
        if f == "sum-where":
            return [f, field, self.filter(a["p"])]
        return [f, field]

    def stage(self, st):
        out = {}
        if st.get("agg"):
            out["aggregation"] = [self.agg(a) for a in st["agg"]]
        if st.get("by"):
            out["breakout"] = [self.ref(b) for b in st["by"]]
        if st.get("where"):
            filters = [self.filter(f) for f in st["where"]]
            out["filter"] = filters[0] if len(filters) == 1 else ["and"] + filters
        if st.get("fields") and st is self.spec:
            out["fields"] = [self.ref({"c": n}) for n in st["fields"]] + [["field", self.ctx.field(self.table, n)["id"], None] for n in st.get("measure_fields", [])]
        if st.get("order"):
            out["order-by"] = [self.order(o, st) for o in st["order"]]
        if st.get("limit"):
            out["limit"] = st["limit"]
        return out

    def order(self, o, st):
        if "agg" in o:
            return [o["dir"], ["aggregation", o["agg"]]]
        if "by" in o:
            return [o["dir"], self.ref(st["by"][o["by"]])]
        if "field" in o:
            return [o["dir"], self.ref({"c": st["fields"][o["field"]]})]
        return [o["dir"], self.ref({"i": o["i"]})]

    def join(self):
        j = self.spec["join"]
        join = self.src["joins"][j["to"]]
        cube = join.get("cube", j["to"])
        return [{"source-table": self.ctx.tables[cube]["id"], "alias": "J", "strategy": j["strategy"], "fields": "none",
                 "condition": ["=", ["field", self.ctx.field(self.table, join["on"])["id"], None], ["field", self.ctx.field(cube, "id")["id"], {"join-alias": "J"}]]}]

    def build(self):
        spec = self.spec
        inner = dict(self.stage(spec), **{"source-table": self.ctx.tables[self.table]["id"]})
        if spec.get("join"):
            inner["joins"] = self.join()
        if not spec.get("stage"):
            return self.ctx.dq(inner)
        st = spec["stage"]
        try:
            self.cols = self.ctx.columns(inner)
        except RuntimeError as e:
            raise InnerError(str(e)) from None
        outer = self.stage(st)
        if st.get("fields"):
            outer["fields"] = [self.ref({"i": i}) for i in st["fields"]]
        if st["via"] == "inline":
            return self.ctx.dq(dict(outer, **{"source-query": inner}))
        card = self.ctx.card(inner, st["via"])
        return self.ctx.dq(dict(outer, **{"source-table": "card__%d" % card}))


def build(ctx, spec):
    return Builder(ctx, spec).build()


def text(query):
    return json.dumps(query, sort_keys=True)
