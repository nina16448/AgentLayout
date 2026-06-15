# N=100 worst-case failure showcase

Worst-4 samples per losing axis (Ali/Read/Occ) from Crello N=100 fresh (Step 68). Pair each AL with its GT for visual failure-mode demonstration in paper figures.

Source analysis: `output/b1_root_cause_n100.json` (Step 71 / B1).
Existing best-case showcase: `../n28_high_score/` (28 pairs).

## Failure-mode tags

- **banner-align (Ali)**: 851x315 banner format — agent's alignment collapses on extreme landscape aspect 2.7, height 315px
- **small-canvas-read (Read)**: small canvas (<=600 px) — text overflow, no shrink-fit
- **portrait-hero-occ (Occ)**: portrait poster — agent places text on top of hero image subject (no saliency-aware avoidance)


## Worst-4 on `alignment` — banner-align (851x315)

| rank | id | Δ | ratio | agent | gt | canvas | title | AL | GT |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `5c88efb7` | +0.3058 | 459.65× | 0.3065 | 0.0006667 | 851×315 | Retro roller skates Offer | `ali1_retro_roller_skates_offer_5c88efb7_AL.png` | `ali1_retro_roller_skates_offer_5c88efb7_GT.jpg` |
| 2 | `58898cbf` | +0.2181 | 31.74× | 0.2252 | 0.007096 | 851×315 | Think outside the box citation | `ali2_think_outside_the_box_58898cbf_AL.png` | `ali2_think_outside_the_box_58898cbf_GT.jpg` |
| 3 | `5a218ae4` | +0.153 | 307.70× | 0.1535 | 0.0004987 | 851×315 | Advanced Technologies Research institute | `ali3_advanced_technologies_research_institute_5a218ae4_AL.png` | `ali3_advanced_technologies_research_institute_5a218ae4_GT.jpg` |
| 4 | `59280150` | +0 | — | 0 | 0 | 1500×500 | Civic Crowdfunding Platform | `ali4_civic_crowdfunding_platform_59280150_AL.png` | `ali4_civic_crowdfunding_platform_59280150_GT.jpg` |

## Worst-4 on `readability` — small-canvas-read

| rank | id | Δ | ratio | agent | gt | canvas | title | AL | GT |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `5f98305b` | +0.2182 | — | 0.2182 | 0 | 851×315 | Spanish Paella party celebration | `rea1_spanish_paella_party_celebration_5f98305b_AL.png` | `rea1_spanish_paella_party_celebration_5f98305b_GT.jpg` |
| 2 | `5bbcb749` | +0.2061 | — | 0.2061 | 0 | 300×250 | Whole Grain Bar | `rea2_whole_grain_bar_5bbcb749_AL.png` | `rea2_whole_grain_bar_5bbcb749_GT.jpg` |
| 3 | `595260fe` | +0.1661 | — | 0.1661 | 0 | 419×298 | Luxury silk linen website with Couple re | `rea3_luxury_silk_linen_website_595260fe_AL.png` | `rea3_luxury_silk_linen_website_595260fe_GT.jpg` |
| 4 | `5a328987` | +0.1072 | — | 0.1072 | 0 | 1200×600 | advertisement poster for store of handcr | `rea4_advertisement_poster_for_store_5a328987_AL.png` | `rea4_advertisement_poster_for_store_5a328987_GT.jpg` |

## Worst-4 on `occlusion` — portrait-hero-occ

| rank | id | Δ | ratio | agent | gt | canvas | title | AL | GT |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `5c6d19e0` | +0.6702 | 791.16× | 0.6711 | 0.0008482 | 1080×1920 | Easter Bunny riding bicycle | `occ1_easter_bunny_riding_bicycle_5c6d19e0_AL.png` | `occ1_easter_bunny_riding_bicycle_5c6d19e0_GT.jpg` |
| 2 | `592e7e20` | +0.4084 | 9.09× | 0.4589 | 0.05048 | 1200×628 | Healthy child with a pediatrician | `occ2_healthy_child_with_a_592e7e20_AL.png` | `occ2_healthy_child_with_a_592e7e20_GT.jpg` |
| 3 | `5ea97608` | +0.3649 | 1.87× | 0.7854 | 0.4205 | 1080×1920 | Shoes Sale Female Legs in Sports Shoes | `occ3_shoes_sale_female_legs_5ea97608_AL.png` | `occ3_shoes_sale_female_legs_5ea97608_GT.jpg` |
| 4 | `58a4176f` | +0.3551 | 371800.78× | 0.3551 | 9.551e-07 | 1500×500 | summer vacation poster | `occ4_summer_vacation_poster_58a4176f_AL.png` | `occ4_summer_vacation_poster_58a4176f_GT.jpg` |

## Stats

- N copied: 12 pairs (24 files)
- N missing: 0

## Paper figure suggestions

- **Figure: AgentLayout failure modes** — 3-row x 4-col grid (one row per axis, 4 samples per row), each cell = GT|AL pair
- **Caption**: "AgentLayout's residual gap concentrates on three structural failure modes: extreme-landscape banner alignment (top), small-canvas text readability (middle), and saliency-unaware text-on-hero occlusion (bottom). These are not random noise but specific limitations tied to identifiable input patterns."

---
*最後更新：2026/06/15*
