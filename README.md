# lnviz -- Lightning Network graph analytics

Tools for analyzing an LND `describegraph`-format JSON dump (like
`lightningGraph.json` in this folder) for **connectivity** and **dominance**
metrics, and for tracking how a node's view of the network changes over
successive snapshots.

## What it computes

**Single snapshot** (`lnviz analyze`):
- **Basic stats** -- node/channel counts, total capacity, isolated nodes,
  nodes with no advertised address.
- **Dominance** -- Gini coefficient, HHI, and top-1%/5%/10% share for both
  *degree* (channel count) and *capacity* (sat), plus a Lorenz curve; a
  composite "hub dominance" ranking that averages percentile rank across
  degree, capacity, betweenness centrality, and eigenvector centrality.
- **Connectivity/structure** -- connected components & giant-component
  fraction, clustering coefficient, transitivity, degree assortativity,
  k-core decomposition, approximate diameter & average shortest-path length.
- **Resilience** -- articulation points (single points of failure) ranked by
  how many nodes they'd strand, bridge-channel count, and an **attack
  simulation**: giant-component fraction remaining as nodes are removed
  randomly vs. by highest degree/capacity/betweenness, summarized as a
  0-0.5 robustness index (area under the fragmentation curve).

**Across snapshots** (`lnviz ingest` + `lnviz history-report`):
- A local SQLite store of headline metrics per snapshot, queryable/trendable
  without re-parsing old (large) JSON dumps.
- Every ingested snapshot also gets its **own standalone HTML report**
  (`snapshots/reports/<timestamp>__<label>.html` -- the same report
  `analyze --out` produces), automatically, at ingest time. Snapshots
  ingested before this feature existed get theirs rendered lazily (from
  already-cached metrics, no recomputation) the next time you run
  `history-report`.
- Node/channel/capacity **diff** between any two tracked snapshots (added
  vs. removed nodes and channels, capacity delta).
- A single **history report** (`lnviz history-report`) that visualizes
  metrics over time:
  - Trend charts: node/channel count, total capacity, degree & capacity
    Gini, giant-component share, average path length, degree assortativity,
    articulation-point count, and all four attack-simulation robustness
    indices on one chart.
  - **Evolution overlays** -- not just single numbers over time, but the
    *shape* of the distribution over time: a Lorenz curve per snapshot
    (capacity and degree concentration) and a fragmentation curve per
    snapshot (for a chosen attack strategy), colored oldest→lightest,
    newest→darkest, so you can see concentration/resilience tightening or
    loosening rather than just reading one summary number's trend line.
  - A snapshot table with a **"view" link to each snapshot's own full
    report**, so the trend view and the point-in-time detail view are one
    click apart.
  - History longer than a handful of points doesn't get dumped into the
    overlay charts unreadably -- they're evenly subsampled (default cap 8,
    `--overlay-limit` to change it) and the report says so explicitly
    rather than silently dropping data.

Sanity-checked against `lightningGraph.json`: the top hub/dominance nodes it
surfaces (ACINQ, LNBiG, Bitfinex, 1ML.com ALPHA, CoinGate, ...) match known
major Lightning routing nodes, and the attack simulation reproduces the
expected scale-free-network signature -- the giant component survives
random failure of half the nodes but collapses after removing only the
top ~10-15% by degree/betweenness.

## Setup note (why the wrapper script exists)

This machine's default `python3` has an old `networkx==2.4`, too old for
this tool. `bin/lnviz` auto-detects a working interpreter instead of
hardcoding one: it tries `$LNVIZ_PYTHON` (if you set it), then the `gt`
conda env this was built/tested against, then whatever `python3` is on
`PATH` -- using the first one that actually imports `networkx>=3.0` +
`matplotlib`. It also always runs with `-s` (skip user site-packages),
since on this machine an older numpy/pandas pair sitting in
`~/.local/lib/python3.10/site-packages` otherwise shadows a perfectly good
env's own newer versions and crashes the import. See **Portability** below
for what this means when moving the project elsewhere.

## Portability -- can I just copy this directory and run it anywhere?

**On this machine, anywhere:** yes. `bin/lnviz` is self-locating (it derives
`PYTHONPATH` from its own path, not your current directory), so `cp -r` this
whole folder to a new path and it still works from any cwd, no edits needed.

**On a different machine:** the code (`lnviz/`) is portable, but the *Python
environment* isn't automatic -- there's no `gt` conda env there for
`bin/lnviz` to fall back to. Two options:
1. If that machine's `python3` already has `networkx>=3.0` + `matplotlib`,
   `bin/lnviz` will just find and use it -- nothing to configure.
2. Otherwise: `pip install -r requirements.txt` into some Python 3.8+
   (a venv is fine), then either put it on `PATH` as `python3`, or
   `export LNVIZ_PYTHON=/path/to/that/python3` so the launcher uses it
   without touching your system `python3`.

**One real gotcha, wherever you run it:** `--history-dir` defaults to
`./snapshots`, resolved against your *current working directory when you run
the command* -- not against wherever the project folder lives. So after
copying the project somewhere new, either `cd` into it before running
`lnviz` commands, or pass `--history-dir /full/path/to/snapshots`
explicitly. (This is deliberate, standard CLI behavior -- like `git` needing
to run inside a repo -- not a bug, but it's easy to trip on right after a
move.)

**What travels with a straight `cp -r`:** everything -- your entire tracked
history (`snapshots/history.db`, the archived raw graphs, the cached
per-snapshot metrics and HTML reports) is self-contained under `snapshots/`
with no absolute-path dependencies baked into the data itself, so a copied
project's history keeps working immediately once you `cd` into it (or point
`--history-dir` at the copy).

## Usage

```bash
# One-off analysis with an HTML report + raw JSON metrics dump
./bin/lnviz analyze lightningGraph.json --out report.html --json metrics.json

# Faster iteration (skip the attack simulation / SPOF pass)
./bin/lnviz analyze lightningGraph.json --skip-resilience

# Start tracking history: ingest today's graph dump
./bin/lnviz ingest lightningGraph.json --label 2026-09-06

# ... time passes, you pull a fresh describegraph dump ...
./bin/lnviz ingest lightningGraph_new.json --label 2026-10-06

# See everything tracked so far
./bin/lnviz list

# Backfill a whole directory of dated dumps at once
./bin/lnviz ingest-dir ./graph_dumps --pattern "*.json"

# What changed between two tracked snapshots (by id, from `lnviz list`)
./bin/lnviz diff 1 2

# Trend report across every tracked snapshot (includes the latest diff,
# per-snapshot report links, and Lorenz/attack-curve evolution overlays)
./bin/lnviz history-report --out history_report.html

# Trace resilience to a different attack strategy in the evolution overlay,
# and widen how many snapshots get overlaid before subsampling kicks in
./bin/lnviz history-report --out history_report.html --overlay-strategy betweenness --overlay-limit 12
```

Run `./bin/lnviz <command> --help` for the full flag list (there are knobs
for betweenness-centrality sampling, path-length sampling, random-attack
trial count, top-N table size, and an optional -- costlier -- algebraic
connectivity/Fiedler-value computation).

### Performance knobs

On this graph (~9,400 nodes / 32,000 channels), a full `analyze` (metrics +
resilience) takes **~80s**, dominated by the approximate betweenness
centrality pass and the attack simulation. `--betweenness-k` controls the
sampling: lower is faster/noisier, `0` forces an exact (much slower) value.
`--skip-resilience` skips the attack simulation and articulation-point scan
entirely for a ~10x faster pass when you just want the distribution/dominance
numbers.

## Recommended workflow for tracking history

1. Periodically pull a fresh `describegraph` dump from your node (e.g. via
   `lncli describegraph > lightningGraph_$(date +%F).json`, on whatever
   schedule you like -- daily/weekly are both reasonable).
2. `./bin/lnviz ingest lightningGraph_<date>.json` each time. This is safe to
   re-run (duplicate `captured_at`+source-path pairs are skipped) and
   archives a copy of the raw JSON under `snapshots/raw/` so diffs keep
   working even if you delete the original later.
3. Run `./bin/lnviz history-report` whenever you want an updated trend view.

`snapshots/` (created on first `ingest`) holds everything: `history.db`
(SQLite), `raw/` (archived graph JSONs), `reports/` (full per-snapshot
metrics as JSON *and* that snapshot's own standalone HTML report). It
is *not* committed anywhere by these tools -- back it up yourself if the
history matters to you, since a raw snapshot can be tens of MB each.

## Code layout

```
lnviz/
  loader.py      JSON -> networkx.Graph (+ per-channel raw records)
  metrics.py      single-snapshot connectivity & dominance metrics
  resilience.py   articulation points/bridges + attack-simulation robustness
  history.py      SnapshotStore: SQLite-backed time series + diffing
  report.py       static self-contained HTML report rendering (matplotlib)
  cli.py          `lnviz` command-line interface
bin/lnviz          launcher (pins the right Python env, see note above)
snapshots/         created on first `ingest` (gitignored-worthy; see above)
```

## Notes / known approximations

- Betweenness centrality and average-shortest-path-length are estimated by
  sampling (configurable), not computed exactly, to keep runtime reasonable
  on graphs this size -- see `--betweenness-k`/`--path-samples`.
- The attack simulation uses a **static** ranking (computed once, then
  nodes are removed in that fixed order) rather than recomputing centrality
  after every removal. This is standard practice for these robustness
  curves and far cheaper, but it means "targeted" attack curves are a good
  approximation of an adaptive attacker, not an exact upper bound.
- Multiple channels between the same two nodes are aggregated into one graph
  edge (summed capacity) for connectivity/resilience purposes; per-channel
  capacity distributions still use the un-aggregated channel list.
- Node capacity = sum of capacities of all channels touching that node (its
  total liquidity commitment), used for the capacity dominance/Lorenz/HHI
  metrics -- not the same as that node's own on-chain balance.
