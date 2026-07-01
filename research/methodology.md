# Methodology

Every metric the paper reports is defined here so a reviewer can verify the derivation from the public `data/aggregates/*.jsonl` streams. Formulas are given in both mathematical notation and pandas-style pseudocode.

## 1. Time series aggregation

Let `M(e, t)` = number of times entity `e` was mentioned on day `t`, computed by grouping `entity_mentions.jsonl` rows.

```python
mentions.groupby(["day", "entity"]).size().unstack(fill_value=0)
```

The mention matrix is dense (`entity × day`) with zero-fills so subsequent time-series operators produce continuous series.

## 2. Velocity

Signed day-over-day change:

$$v(e, t) = M(e, t) - M(e, t-1)$$

- Positive = attention growing that day
- Negative = attention decaying
- Zero-fill at series start ensures the first day's velocity is `M(e, t0) - 0`

Optional smoothing (reported as separate columns):

- **7-day EMA velocity**: exponential moving average with span=7 to damp weekend noise
- **Rolling mean velocity**: `v_bar_7(e, t) = mean(v(e, t-6..t))`

```python
velocity = mentions.diff(axis=1)
velocity_ema7 = velocity.ewm(span=7, axis=1).mean()
```

## 3. Acceleration

Signed change of velocity:

$$a(e, t) = v(e, t) - v(e, t-1)$$

Interpretation:
- Large positive `a` while `v > 0` = attention accelerating up (buildup)
- Large positive `a` while `v < 0` = decay slowing (bottoming out)
- Large negative `a` while `v > 0` = growth slowing (peaking)
- Large negative `a` while `v < 0` = decay accelerating (crash)

```python
acceleration = velocity.diff(axis=1)
acceleration_ema28 = acceleration.ewm(span=28, axis=1).mean()
```

The 28-day smoothed acceleration is used to identify **regime shifts** (the paper's Hypothesis 2 test).

## 4. Burst detection

Following Kleinberg (2002) — simplified for daily bins:

An entity is in **burst state** on day `t` when

$$z(e, t) = \frac{v(e, t) - \mu_v(e)}{\sigma_v(e)} \geq 2$$

where `μ_v(e)` and `σ_v(e)` are that entity's mean and stdev of velocity across all observed days. Burst durations are recorded for network-evolution alignment (H3).

## 5. Network snapshot at time t

Nodes:

$$N(t) = \{ e : \sum_{s \leq t} M(e, s) \geq k_{\min} \}$$

where `k_min = 3` by default (removes one-off appearances).

Edges:

$$E(t) = \{ (e_a, e_b, w) : w = |\text{clusters}_{s \leq t}(e_a) \cap \text{clusters}_{s \leq t}(e_b)| \geq w_{\min} \}$$

with `w_min = 1` (any shared cluster counts). Aggregated from `entity_cooccurrence.jsonl`.

## 6. Network metrics

For a snapshot graph `G(t) = (N(t), E(t))`:

- **Density**: $\rho(t) = |E(t)| / \binom{|N(t)|}{2}$
- **Average clustering coefficient**: mean local clustering coeff across nodes (networkx `average_clustering`)
- **Number of connected components**: `nx.number_connected_components(G)`
- **Modularity** (Louvain): community assignment via `nx.community.louvain_communities`; modularity via `nx.community.modularity`

Per-node metrics stored in the snapshot's `network_edges.parquet` as a separate `network_nodes.parquet` when node counts are large enough:

- **Degree** `k(e, t) = |{ f : (e, f, ·) ∈ E(t) }|`
- **Weighted degree** `s(e, t) = Σ_{f} w(e, f, t)`
- **Betweenness centrality** — networkx `betweenness_centrality`
- **PageRank** — networkx `pagerank(alpha=0.85)`

## 7. Community evolution tracking

Between consecutive snapshots `G(t-1)` and `G(t)`:

1. Compute Louvain communities on each snapshot.
2. Match communities across time by Jaccard similarity of node sets ≥ 0.5.
3. Record: `same` (matched), `split` (one community becomes multiple), `merge` (multiple become one), `emerge` (new community with no antecedent), `dissolve` (no successor).

Change count over a 14-day window is the paper's H3 test statistic.

## 8. Δ report (auto-generated per snapshot)

Given `snapshot(t)` and `snapshot(t-1)`, the auto-generator emits `report.md` containing:

- **New entities** (present in `N(t)`, absent from `N(t-1)`)
- **Top 5 velocity gainers** (largest positive `v(e, t)`)
- **Top 5 velocity losers** (largest negative `v(e, t)`)
- **Top 5 acceleration events** (largest `|a(e, t)|` combined with `sign(v)` interpretation)
- **Community count delta**
- **Density delta**

The report is a natural-language digest for the notes archive; it does not replace the parquet files.

## 9. Rolling aggregations

For `daily.parquet` → `weekly.parquet`:

- Sum mentions, sum abs(velocity), mean acceleration per ISO week per entity

For `weekly.parquet` → `monthly.parquet`:

- Sum mentions, sum abs(velocity), mean acceleration per calendar month per entity

These are `entity × time` long-format frames ready for seaborn / matplotlib.

## References

- Kleinberg, J. (2002). *Bursty and hierarchical structure in streams.* KDD.
- Blondel et al. (2008). *Fast unfolding of communities in large networks.* JStat Mech.
- Palla et al. (2007). *Quantifying social group evolution.* Nature.

Full citations pending; this file is updated as the paper draft advances.
