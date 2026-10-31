"""The shapes of the grid that stay wrong. Each entry has a cause that the driver and the dialect cannot reach. `make grid` and the conformance check
`grid.compact` fail on a wrong shape that is not here, and `make grid` fails on an entry that no shape shows. Do not add an entry to get a pass."""
import re

LIMITS = [
    {"id": "long-decimal", "variants": ("dialect", "stock"), "shapes": r"^edge/raw_dec_big$",
     "reason": "The Cube SQL API has the type Double for each number, so a long decimal below 2^53 loses digits before the driver reads it. The value fits a Double and /v1/meta does not show the column type, so the driver cannot see this, and a dialect does not change the type of the SQL API."},
    {"id": "stock-raw-datetime64", "variants": ("stock",), "shapes": r"^edge/raw_ts64$",
     "reason": "Stock Cube writes a selected time with precision 0, so a raw DateTime64 shows whole seconds. /v1/meta does not show the column type, so the driver cannot tell it from a DateTime. The dialect is the fix: this shape passes with it."},
    {"id": "stock-unmarked-date", "variants": ("stock",), "shapes": r"^(tz/(America/New_York|Pacific/Apia|Asia/Kathmandu|Europe/Berlin)/Date32 |edge_tz/.*date32)",
     "reason": "Stock Cube reads a Date as a time at midnight UTC under a report time zone. /v1/meta does not show the column type, so the driver can stop this only for a dimension with meta date_only. Date32 here has no mark, as in a model that nobody marked. The dialect is the fix: this shape passes with it."},
    {"id": "stock-native-half-open-datetime64", "variants": ("stock",), "shapes": r"^edge/ts64_native_half_open$",
     "reason": "Stock Cube ends the range of native x >= a AND x < b at the last whole second, so a DateTime64 loses the rows of that second. /v1/meta does not show the column type, and the same text is right on a DateTime, so the driver would have to stop each native upper bound on stock Cube. The dialect is the fix: this shape passes with it."},
]


def limit_of(shape_id, variant):
    for limit in LIMITS:
        if variant in limit["variants"] and re.search(limit["shapes"], shape_id):
            return limit
    return None


# A raw error of Cube has these texts. Each message of the driver tells what to do, and it does not start with them.
RAW = re.compile(r"^(ERROR: |Error executing query|Query failed: (Error|\"?ERROR)|.*Can't find rewrite|.*Internal Error)")


def clear_error(text):
    return not RAW.search(text.strip())
