# Cube audit

This audit tries to refute the 18 defects with the verdict `cube-bug` or `cube-clickhouse-bug` in [cube-findings.md](cube-findings.md). The default position for each defect is that it is not a bug. The cases and the data here are new. They do not reuse the cases of the findings.

## How this was checked

- **Versions:** Cube `v1.8.15`, ClickHouse `26.8.36-alpine`, Postgres `17-alpine`. Some cases also ran on ClickHouse `25.3-alpine` and on older Cube tags.
- **Configs:** each case ran on ClickHouse and Postgres, with `CUBEJS_TESSERACT_SQL_PLANNER` on and off, unless the table says otherwise.
- **Query shape:** the shape comes from `EXPLAIN` of each SQL API case. `regular` is a plan with only a `CubeScan`. `post-processing` is a plan with other nodes over a `CubeScan`. `pushdown` is a plan with `CubeScanWrappedSql`. `not-planned` means that `EXPLAIN` failed too. `no EXPLAIN` is a case with parameters and no literal form to explain. For a REST case, the table says if the response has `usedPreAggregations`.
- **Expected values:** {{CASES}} cases, written before the runs from a small table that you can read in `dev/audit/cases/`. Each amount is a power of 10 where this helps, so that a sum names its rows. Where direct SQL makes sense, the table also shows its result. ClickHouse output is not the truth when its semantics differ: for example, `sum` of no rows is 0 on ClickHouse and NULL in the SQL standard.
- **Corrected cases:** the runs found errors in some of the first expected values, and in some model definitions. I corrected these before the final run, and the case text gives the reason. In the tables, `diff` means that the run gives another value than the derived one.
- **Master:** on 2026-10-30, `cube-js/cube` master is [`78ecad294`](https://github.com/cube-js/cube/commit/78ecad294), 20 commits after `v1.8.15`. I did not build or run master. I read it. Of the 13 source files that the defects name, 12 are identical in `v1.8.15` and master. The change in the 13th, `BaseQuery.ts`, does not touch a path here. So a result on `v1.8.15` applies to master for these paths. Code links go to `78ecad294`.
- **Issues and pull requests:** the links are to `cube-js/cube`. A pull request that only adds a failing test says that the Cube team knows about the defect. It does not fix it.
- **Stack:** compose project `mcd-audit` on `nettop.local`, ports 4600 and 4601 on the loopback interface, `mem_limit: 1g` per service. One set ran at a time. I removed the project, its volumes and the images that it pulled after the runs.

## Limits

- I did not build or run master, or the open pull requests. A statement about master comes from the code.
- Postgres is the only other database. A note about other data sources comes from the code.
- A rollup hit is visible for REST only. For the SQL API, a case on a cube with a rollup does not prove a hit.
- `v1.8.7` has no image on Docker Hub, so a start at `v1.8.7` and at `v1.8.8` look the same.
- "I found no pull request" means no match in my searches. It is not proof.

## Run

```sh
cd dev/audit
python3 audit.py B1                        # all configs of one defect, writes results/
python3 audit.py all                       # all defects
python3 audit.py B1 --config ch-on --case plain_gt_lte
python3 audit.py N1 --only-tags            # the older Cube tags of this defect
python3 audit.py --down                    # removes the stack, its volumes and the pulled images
python3 render.py                          # writes this file from cases/, results/ and answers/
```

Run `audit.py` on a host with Docker. `render.py` needs only Python. The tables below come from `dev/audit/results/`.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `confirmed` | It survived all five questions. |
| `confirmed-but-narrow` | The defect is real, but its scope is smaller than the findings say, or it is known and documented, or it needs a rare setting. |
| `intended` | A Cube doc, test or maintainer comment says that this is the behaviour. |
| `misuse` | The query or the model is outside the documented use. |
| `already-fixed` | Master or a merged change fixes it. |
| `not-reproduced` | My cases do not show it. |
