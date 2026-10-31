#!/bin/sh
# The runs that find the first tag of a behaviour, and the cases added after them.
set -x
python3 audit.py M1 --case issue_12459_form --case issue_12459_form_no_order
python3 audit.py M1 --only-tags --tag v1.7.26 --tag v1.7.27 --tag v1.8.14 --config pg-on
python3 audit.py M1 --bisect v1.7.27 v1.8.14 --case no_oid --config pg-on
python3 audit.py interval --bisect v1.6.76 v1.8.15 --case raw_plus_day --config ch-off
python3 audit.py propagate --case no_filter_control
python3 audit.py B4 --case breakout_cube_with_time_dim
python3 audit.py N1 --only-tags --tag v1.3.0 --tag v1.5.0
python3 audit.py N2 --only-tags --tag v1.0.0 --tag v1.6.76 --tag v1.7.0
python3 audit.py N2 --config ch-def --case two_months_rest
python3 audit.py M1 --bisect v1.8.6 v1.8.9 --case no_oid --config pg-on
python3 audit.py interval --only-tags --tag v1.7.26 --config ch-on
python3 audit.py interval --case raw_plus_day_order_limit --case aggregated_order_limit
python3 audit.py M1 --case no_oid_limit --case issue_12459_form_limit
