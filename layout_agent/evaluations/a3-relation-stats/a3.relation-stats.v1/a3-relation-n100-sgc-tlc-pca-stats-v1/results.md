# Relation N=100 SGC/TLC/PCA statistical reanalysis

Schema `a3.relation-stats.v1`; sign test = exact two-sided binomial (ties excluded); Holm adjustment over all 9 tests; Bonferroni shown as sensitivity analysis; bootstrap CI = sample-level percentile, seed 20260712, 10,000 resamples.

## Arm summary

| Arm | Frozen N | Completed | Failures | SGC mean (n) | TLC mean (n) | PCA mean (n) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| T0 | 100 | 100 | 0 | 0.6465 (100) | 0.6277 (100) | 0.6930 (100) |
| T2 | 100 | 98 | 2 | 0.7037 (98) | 0.6711 (98) | 0.7614 (98) |
| T3 | 100 | 99 | 1 | 0.7779 (99) | 0.7271 (99) | 0.8215 (99) |

Generation failures: 3/300 — T2 `5d67ed46cf657b21ef7bdad9` (A3L0PipelineError); T2 `5f644f40a637ee11e3669a1c` (ValueError); T3 `5da04604abc8ea6d1cbe2935` (A3L0PipelineError).

## Paired comparisons

| Comparison | Metric | Paired N | W/L/T | Mean diff | 95% CI | p raw | p Holm | p Bonf |
| --- | --- | ---: | --- | ---: | --- | ---: | ---: | ---: |
| T2 vs T0 | SGC | 98 | 64/34/0 | +0.0551 | [+0.0135, +0.0980] | 0.0032 | 0.0128 | 0.0287 |
| T2 vs T0 | TLC | 98 | 63/32/3 | +0.0412 | [+0.0016, +0.0806] | 0.0019 | 0.0115 | 0.0173 |
| T2 vs T0 | PCA | 98 | 47/25/26 | +0.0637 | [+0.0146, +0.1117] | 0.0128 | 0.0383 | 0.1150 |
| T3 vs T0 | SGC | 99 | 74/25/0 | +0.1317 | [+0.0897, +0.1731] | 8.5e-07 | 7.7e-06 | 7.7e-06 |
| T3 vs T0 | TLC | 99 | 70/29/0 | +0.1005 | [+0.0603, +0.1411] | 4.6e-05 | 0.0003 | 0.0004 |
| T3 vs T0 | PCA | 99 | 55/18/26 | +0.1299 | [+0.0822, +0.1778] | 1.7e-05 | 0.0001 | 0.0002 |
| T3 vs T2 | SGC | 97 | 64/33/0 | +0.0743 | [+0.0330, +0.1147] | 0.0022 | 0.0115 | 0.0194 |
| T3 vs T2 | TLC | 97 | 60/35/2 | +0.0570 | [+0.0170, +0.0973] | 0.0134 | 0.0383 | 0.1204 |
| T3 vs T2 | PCA | 97 | 46/29/22 | +0.0597 | [+0.0107, +0.1083] | 0.0639 | 0.0639 | 0.5755 |

## Conservative Results paragraph

Across 100 frozen Relation samples per arm (3/300 generation failures excluded pairwise, so each comparison uses the intersection of samples that completed in both arms), we ran nine paired two-sided sign tests with Holm correction. After Holm adjustment, the following comparisons remained significant at alpha=0.05: T2 vs T0 SGC (64W/34L/0T, mean diff +0.0551, 95% CI [+0.0135, +0.0980], Holm p=0.0128); T2 vs T0 TLC (63W/32L/3T, mean diff +0.0412, 95% CI [+0.0016, +0.0806], Holm p=0.0115); T2 vs T0 PCA (47W/25L/26T, mean diff +0.0637, 95% CI [+0.0146, +0.1117], Holm p=0.0383); T3 vs T0 SGC (74W/25L/0T, mean diff +0.1317, 95% CI [+0.0897, +0.1731], Holm p=7.7e-06); T3 vs T0 TLC (70W/29L/0T, mean diff +0.1005, 95% CI [+0.0603, +0.1411], Holm p=0.0003); T3 vs T0 PCA (55W/18L/26T, mean diff +0.1299, 95% CI [+0.0822, +0.1778], Holm p=0.0001); T3 vs T2 SGC (64W/33L/0T, mean diff +0.0743, 95% CI [+0.0330, +0.1147], Holm p=0.0115); T3 vs T2 TLC (60W/35L/2T, mean diff +0.0570, 95% CI [+0.0170, +0.0973], Holm p=0.0383). For the remaining comparisons (T3 vs T2 PCA) no difference was detected; because no equivalence test was performed, these null results must not be interpreted as evidence that the arms are equivalent.
