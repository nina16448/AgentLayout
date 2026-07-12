# A3-T2 vs Elem2Design (Relation N=100, matched inputs)

Direction: diff = A3-T2 − Elem2Design. SGC/TLC/PCA higher is better; Ali/Ove lower is better. Holm within families (primary 3 tests, geometry 2 tests; Rea/Occ deferred). Bootstrap seed 20260712, 10,000 resamples.

Arm completion: A3-T2 98/100; Elem2Design 94/100.

| Metric | A3-T2 mean (n) | E2D mean (n) | Paired N | W/L/T | Mean diff | 95% CI | p raw | p Holm |
| --- | ---: | ---: | ---: | --- | ---: | --- | ---: | ---: |
| SGC | 0.7037 (98) | 0.5355 (94) | 93 | 74/19/0 | +0.1684 | [+0.1271, +0.2077] | 7.7e-09 | 1.5e-08 |
| TLC | 0.6711 (98) | 0.5092 (94) | 93 | 81/11/1 | +0.1578 | [+0.1221, +0.1932] | 2.5e-14 | 7.5e-14 |
| PCA | 0.7614 (98) | 0.6450 (94) | 93 | 50/24/19 | +0.1167 | [+0.0617, +0.1713] | 0.0034 | 0.0034 |
| ALI ↓ | 0.0012 (98) | 0.0002 (94) | 93 | 13/3/77 | +0.0010 | [+0.0003, +0.0019] | 0.0213 | 0.0213 |
| OVE ↓ | 0.1173 (98) | 0.2496 (94) | 93 | 9/84/0 | -0.1347 | [-0.1604, -0.1086] | 2.2e-16 | 4.3e-16 |

Failures are excluded pairwise only; both arms' failure counts are reported above and in aggregate.json. Non-significant results mean no difference was detected, not equivalence.
