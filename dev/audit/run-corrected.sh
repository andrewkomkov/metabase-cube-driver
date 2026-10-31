#!/bin/sh
# Sets that changed after the first full run.
set -x
python3 audit.py propagate
python3 audit.py unbounded
python3 audit.py big --config ch-on+ch=25.3-alpine
python3 audit.py geo --case view_prefix_rest
python3 audit.py gran
python3 audit.py interval
python3 audit.py datetime64
python3 audit.py extract
python3 audit.py B4 --case breakout_plain_order --case breakout_no_filter_plain
for d in B1 B2 B4 M1 M2 m3 m4 m5 unbounded geo gran propagate datetime64 interval extract N1 N2; do python3 audit.py $d --only-tags; done
