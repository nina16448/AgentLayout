# Full Result Index (Step 74, F8 N=1,897 capture)

This folder holds per-sample experiment traces for **178** Crello test samples.
Each sample's folder is a self-contained record of the full refinement loop, the
Judge's verdicts, the QC violations, all rendered candidates, and the matched
COLE H2H. The taxonomy below applies to every `<id>/` subfolder.

## Per-sample folder layout

```
<id>/
  README.md                          # auto-generated per-sample index
  gt/designer_gt.jpg                 # V1 designer ground-truth render
  inputs/
    brief.txt                        # natural-language brief fed to Analyst
    asset_list.json                  # structured asset clip list
    spec.json                        # enriched DesignSpec (post-Analyst+AssetAnalyzer)
  rounds/round_NN_<label>/
    selected.png                     # V2/V3 Judge-selected candidate render
    selected.json                    # selected Candidate (bbox + style)
    candidates.json                  # all raw Candidate objects
    candidate_<r_cand>.png           # V4 every raw candidate render (incl QC-filtered)
  final/
    final_render.png                 # V5 last-accepted render
    final_candidate.json             # accepted Candidate (for SEGA / IoU)
    compare_AL_vs_GT.png             # V5 side-by-side AL|GT (paper figure)
  trace/
    sample_meta.json                 # T1 n_rounds, final_decision, total_wall_sec
    per_round_judge.json             # T2 ACCEPT/REJECT + 5-axis scores per round
    feedback_history.md              # T3 Judge feedback prose per round
    feedback_routing.json            # T4 next_target per round
    qc_violations.json               # T5 per-round per-candidate QC violations
    counts.json                      # T6 raw_pool / qc_pass / judge_input per round
    timing.json                      # T7 wall_sec per round
  diagnostic/
    saliency_landscape.json          # D1 bg saliency histogram + low-regions
    element_fingerprint.json        # D2 n_text/n_image/canvas/aspect summary
    sega_metrics.json                # D3 SEGA 6-axis A geometric vs designer GT
    cole_h2h.json                    # D4 matched COLE single-call H2H scores
    per_axis_trace.json              # D5 per-round per-axis score evolution
```

## Aggregate stats (this folder)

See `_aggregate/`:

- `pipeline_stats.json` -- machine-readable S1-S10 + D3 + D4 + D5 dumps
- `loop_distribution.md` -- S1/S2/S10 loop count & convergence
- `reject_reasons_top20.md` -- S3 most common Judge reject flags
- `qc_violations_top20.md` -- S4 most common QC rules fired
- `per_round_convergence.md` -- S7 accept-rate per round
- `cost_walltime_summary.md` -- S6 wall-time distribution
- `sega_aggregate.md` -- D3 SEGA 6-axis A-axis aggregate
- `cole_h2h_aggregate.md` -- D4 matched COLE H2H Smean (paper main B-axis)
- `per_axis_climb.md` -- D5 does refinement climb per axis?

## Pipeline architecture reference

See `layout_agent/PIPELINE.md`. Each `<id>/rounds/round_NN_<label>/` corresponds
to one iteration of the Stage 2 refinement loop documented there.
