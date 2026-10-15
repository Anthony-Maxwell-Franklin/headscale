# Headscale Metrics Analysis (for Report M1-M4)

## Window Definition

| Stage | Anchor | Cumulative Range (M1/M2) | Segmented Range (M3/M4) |
|---|---|---|---|
| Start | v0.1.0 | v0.1.0 | v0.1.0 |
| Early growth | v0.12.1 | v0.1.0..v0.12.1 | v0.1.0..v0.12.1 |
| Expansion | v0.17.1 | v0.1.0..v0.17.1 | v0.12.1..v0.17.1 |
| Maturity | v0.22.3 | v0.1.0..v0.22.3 | v0.17.1..v0.22.3 |
| Current | v0.28.0 | v0.1.0..v0.28.0 | v0.22.3..v0.28.0 |

## M1 Hotspot Concentration (HHI)

| Stage | Touch Events | Top10 Share (%) | Top1 Share (%) | HHI | Top 3 Hotspots |
|---|---:|---:|---:|---:|---|
| Start | 313 | 51.12 | 9.27 | 0.0376 | cmd/headscale/headscale.go (29); README.md (26); api.go (22) |
| Early growth | 1322 | 40.47 | 6.35 | 0.0238 | app.go (84); api.go (82); machine.go (70) |
| Expansion | 3455 | 31.43 | 5.62 | 0.0164 | app.go (194); machine.go (164); api.go (140) |
| Maturity | 4436 | 27.28 | 4.62 | 0.0129 | app.go (205); machine.go (195); CHANGELOG.md (168) |
| Current | 9314 | 16.89 | 3.47 | 0.0059 | CHANGELOG.md (323); app.go (205); machine.go (195) |

## M2 Knowledge Concentration

| Stage | Commits | Authors | Top1 Share (%) | Top2 Share (%) | HHI | Effective Authors | BusFactor50 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Start | 121 | 7 | 74.38 | 90.08 | 0.5825 | 1.72 | 1 |
| Early growth | 611 | 32 | 50.57 | 68.74 | 0.3114 | 3.21 | 1 |
| Expansion | 1740 | 96 | 42.87 | 64.37 | 0.2419 | 4.13 | 2 |
| Maturity | 2005 | 130 | 42.69 | 61.4 | 0.2333 | 4.29 | 2 |
| Current | 2803 | 238 | 48.16 | 61.54 | 0.2602 | 3.84 | 2 |

## M3 Coordination Latency

| Stage | Handoff Rate (%) | Median Handoff Days | P75 Handoff Days |
|---|---:|---:|---:|
| Start | 23.98 | 4.04 | 13.37 |
| Early growth | 30.44 | 6.3 | 20.23 |
| Expansion | 42.22 | 9.37 | 26.78 |
| Maturity | 34.08 | 15.31 | 43.57 |
| Current | 32.49 | 21.27 | 60.08 |

## M4 Mean Time to Repair

### M4A File-Touch Proxy

| Stage | Samples | MTTR Mean (days) | MTTR Median (days) | P75 (days) |
|---|---:|---:|---:|---:|
| Start | 11 | 8.17 | 0.81 | 8.44 |
| Early growth | 93 | 3.12 | 0.86 | 3.19 |
| Expansion | 156 | 8.36 | 0.76 | 8.45 |
| Maturity | 20 | 15.38 | 2.22 | 24.91 |
| Current | 122 | 19.49 | 5.05 | 19.22 |

### M4B Bug-Only Issues

| Stage | Samples | MTTR Mean (days) | MTTR Median (days) | P75 (days) |
|---|---:|---:|---:|---:|
| Start | 0 | N/A | N/A | N/A |
| Early growth | 5 | 16 | 6.87 | 27.91 |
| Expansion | 103 | 31.86 | 5.6 | 43.17 |
| Maturity | 74 | 64.17 | 12.92 | 82.84 |
| Current | 479 | 81.12 | 20.05 | 102.22 |
