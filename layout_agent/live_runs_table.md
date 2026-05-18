# Live LLM Runs — Result Table（paper-ready）

整理 2026-05-09 起至 2026-05-15 共 **9 場 live LLM 跑 + 1 場 re-run** 的成本、分數軌跡與失敗模式，提供論文 results 與 limitations 章節原始資料。

> **⚠️ 2026-05-18 誠實修正（讀本表前必看）**：本表多列「Best score / mean best X vs Crello GT 68 = +N」是 **pipeline 自家 Aesthetic Judge 單邊評 AgentLayout candidate**、再與另一 corner-case 量到的「設計師 GT≈68」相比——**非配對、Judge 校準不同，是測量假象，不可解讀為「AgentLayout 勝設計師」**。2026-05-18 step 11 的正規 **pairwise head-to-head Win Rate**（交換圖序 ×2、N=3）顯示：A realistic 設計師完勝 **3:0**（分差 27-37）、B layout-only 即使隔離渲染仍 **2:1** 設計師勝。即「Best score」欄僅供同 pipeline 內部 trend 比較，**不是 vs-designer 的有效對照**；vs-designer 結論以 `step11_winrate.png` / `step11_winrate_results.json` 為準。

---

## 1. 一覽表

| #   | 日期       | Driver / 對應 step                   | Brief（fixture）                                                | Canvas      | Cost (USD) | Verdicts | Best score | Score 軌跡         | Termination     | 主要結論 / failure mode                                                                                  |
| --- | ---------- | ------------------------------------ | --------------------------------------------------------------- | ----------- | ---------- | -------- | ---------- | ------------------ | --------------- | -------------------------------------------------------------------------------------------------------- |
| 1   | 2026-05-09 | `run_role_team_live.py` / step 0     | 3-element synthetic poster (`Spring Sale`)                      | 800×1200    | ~0.30      | 3        | 75         | 75 → 72 → 72       | reject          | 第一次 routing 通過；Aesthetic Judge feedback 太模糊，Generator 不收斂                                   |
| 2   | 2026-05-14 | step 4 (Judge metric whitelist)      | 同 #1                                                           | 800×1200    | ~0.30      | (≤2)     | 72         | 72 → QC fail       | RuntimeError    | Judge 不再 emit `right`/`bottom`；但 area-math leak 仍致 QC 全 fail                                      |
| 3   | 2026-05-14 | step 5 (area-math leak fix)          | 同 #1                                                           | 800×1200    | 0.31       | 3        | 72         | 72 → 72 → 72       | reject          | QC RuntimeError 完全消除、3 完整 iter graceful 退場；分數 plateau，bottleneck 轉移至非 prompt 層         |
| 4   | 2026-05-14 | step 6 (canvas-coverage rule)        | 同 #1                                                           | 800×1200    | 0.30       | 3        | 72         | 72 → 70 → 69       | reject          | bottom rule 遵守、top rule 違反；確認「3 element 在 800×1200 上本來就無法 balanced」結構性根因           |
| 5   | 2026-05-14 | step 7 (Analyst background_color)    | 同 #1（Analyst emit `#E8F1F8` cool bucket）                      | 800×1200    | 0.28       | 3        | 72         | 72 → 68 → 68       | reject          | Analyst 真的 emit canvas-aware hex；分數 = Crello designer-GT baseline 68                                |
| 6   | 2026-05-14 | step 8 (contrast-aware text, REVERT) | 同 #1                                                           | 800×1200    | 0.34       | 3 + ½    | 72         | 72 → 70 → 70 → 💥  | RuntimeError    | Analyst retry 後 Generator round 15/15 fail QC；揭露「prompt attention budget」現象，step 8 commit 撤回 |
| 7   | 2026-05-14 | step 9 (Crello sparsity, QC alias)   | Crello `5c6c0cba` hiring poster (5 elements, 1 bg + 4 text)     | 1080×1920   | 0.49       | 3 + ½    | 72         | 72 → 72 → 70 → 💥  | RuntimeError    | **平均 best 71.3 vs baseline 68 = +3.3**；sparsity hypothesis 正向證據（N=1）；揭露 QC alias bug 並修補 |
| 8   | 2026-05-15 | step 9b N=3 (no fix yet)             | Crello `5954bda9` "dog pet citation" (4 effective elements)     | 1200×600    | 0.12       | 0        | —          | 0/15 hard fail     | RuntimeError    | Generator+QC robustness ceiling；no_overlap micro-overlap 把 LLM rounding 全判違規                        |
| 9   | 2026-05-15 | step 9b N=3 (no fix yet)             | Crello `5d972ca9` Russian "Travelling Tips" (4 effective elements) | 537×240   | 0.13       | 0        | —          | 0/15 hard fail     | RuntimeError    | 同 #8；tight canvas + position_preference + no_overlap 多元素組合不可解                                   |
| 8r  | 2026-05-16 | step 10 re-run (5% tolerance only)   | 同 #8                                                           | 1200×600    | 0.12       | 0        | —          | 0/15 hard fail     | RuntimeError    | step 10 解了 no_overlap，但 fail mode 漂到 `position_preference` band；揭發 step 10c 動機               |
| 8rc | 2026-05-16 | step 10c (band 10% tolerance)        | 同 #8                                                           | 1200×600    | 0.43       | 3        | 70         | 70 → 68 → 70       | reject          | **跑完整 reject loop**；5-element 4-effective brief mean best 69.3 vs GT 68 → sparsity N=2 validated |
| 9rd | 2026-05-18 | step 10d (10+10c on small canvas)    | 同 #9                                                           | 537×240     | 0.55       | 3 + ½    | 72         | 70 → 70 → 72 → 💥  | RuntimeError    | **step 10+10c 解開 small-canvas hard crash**；跑完整 reject loop（V3 best 72 req=20 hier=18 bal=17 coh=17）；mean best 70.67 vs GT 68 → robustness 修補在第二 aspect ratio generalize、sparsity N=3；殘留 💥 為已知 step 10b post-Analyst-retry crash 非 tolerance 問題 |
| 12c | 2026-05-19 | step 12b（z_order hint 正規化，**content-aware ON**） | Crello `5efdd2dd` "Citation about Diversity of Skin Color"（3 elements，真實背景圖） | 1008×1296 | 0.27 | 3 | 72 | 72 → 72 → 72 | reject | **🌟 首個真正 content-aware live**：BackgroundAnalyzer 全 3 round 注入真實 3 safe zones（非 stub）；z_order `hint:above_background` QC 正規化解開 0/15 crash（並修掉 PROMPT_TEMPLATE `.format()` `{}` KeyError）；mean best 72，子分數 req=20 hier=18 **bal=17 coh=17**——content-aware **未突破 plateau**，與 step 11「bal/coh≈17 結構性 scope-bound」結論一致；**#1–#9 全為 pre-content-aware，本列起為 content-aware baseline** |

註：
- 「Verdicts」= Aesthetic Judge 完整 emit 的次數；「3 + ½」= 3 場完整 verdict 後 Analyst retry 觸發、Generator round crash
- 「Termination = RuntimeError」= `LayoutGeneratorRole` raise `RuntimeError("0/K candidates passed QC after N top-up rounds")`
- 「Best score」是同場最佳 candidate 的 Aesthetic Judge total（滿分 100）
- Score 軌跡的箭頭 → 為 verdict-by-verdict best；💥 表示之後 Generator round crash

---

## 2. 分數結構洞察（從 #3 起穩定可比）

從 step 5 起 QC crash 完全消除，可以穩定觀察 Aesthetic Judge 4 子分數的相對軌跡：

| Sub-score          | 滿分 | #3 best | #4 best | #5 best | #7 best |
| ------------------ | ---- | ------- | ------- | ------- | ------- |
| requirement        | 25   | 20      | 18      | 18      | 20      |
| visual_hierarchy   | 25   | 18      | 17      | 17      | 18      |
| layout_balance     | 25   | 17      | 17      | 17      | 17      |
| visual_coherence   | 25   | 17      | 17      | 16      | 17      |
| **total**          | 100  | **72**  | **69**  | **68**  | **72**  |

兩段 plateau：
1. 從 #3 → #5 的「~4 點 sparsity-driven」段落：3 element 不夠撐起 800×1200 海報，bal/coh 永遠 17
2. 從 #5 → #7 的「sparsity 段落突破」：5 element 把 req +2 / hier +1 拉回，但 bal/coh 仍 17 — **plateau 第二段（bal/coh=17 上限），step 11 將攻擊**

---

## 3. Cost 細目（依 step 修補回推、$/run）

| Step       | 對應 #          | Cost (USD)  | 備註                                                          |
| ---------- | --------------- | ----------- | ------------------------------------------------------------- |
| step 2/3   | postfix log     | 0.18        | structured suggestions 落地 + threshold 80→75                  |
| step 4     | #2              | 0.30        | Judge metric whitelist                                        |
| step 5     | #3 (areafix)    | 0.31        | area-math leak fix；首次無 crash 跑完                          |
| step 6     | #4 (coverage)   | 0.30        | canvas coverage rule                                          |
| step 7     | #5              | 0.28        | Analyst background_color                                      |
| step 8     | #6              | 0.34 + REV  | contrast hypothesis 失敗、commit 撤回                          |
| step 9     | #7              | 0.49        | Crello 5-element + QC alias bugfix                            |
| step 9b    | #8              | 0.12        | 1200×600 hard fail                                            |
| step 9b    | #9              | 0.13        | 537×240 hard fail                                             |
| step 10    | #8r             | 0.12        | 5% no_overlap tolerance；解 no_overlap 但 fail mode 漂移        |
| step 10c   | #8rc            | 0.43        | 10% position-band tolerance；首次跑完 1200×600 reject loop      |
| step 10d   | #9rd            | 0.55        | 537×240 small canvas 重跑；step 10+10c 解開 hard crash         |
| **累計**   |                 | **~3.55**   | 9 場 + 3 re-run；含一次失敗實驗（step 8）                      |

---

## 4. Failure mode 統計

| Failure mode                          | 出現於       | Resolution                                                                |
| ------------------------------------- | ------------ | ------------------------------------------------------------------------- |
| Aesthetic Judge feedback 模糊         | #1           | step 2 structured suggestions schema + step 3 threshold calibration       |
| Judge emit `metric=right/bottom`      | #2           | step 4 PROMPT_TEMPLATE metric whitelist                                   |
| QC `size_preference` area-math leak   | #2 (殘留)    | step 5 PROMPT 加 width × height ≥ 0.10×canvas_area                        |
| Plateau 第一段（spec sparsity）       | #3, #4, #5   | step 9 用 5-element brief 從 68 → 71.3                                    |
| Plateau 第二段（bal/coh=16-17 上限）  | #3 起        | **scope-bound limitation（step 11 結案）** — Generator schema 無裝飾元素表達力；by design 不做 graphic-design synthesis，非 bug、不解；#7/#8rc/#9rd 跨 aspect ratio 一致 |
| Post-Analyst-retry Generator crash    | #6, #7, #9rd | **未解** — step 10b 候選；#9rd 確認 3 verdict 後仍於 rebuild round crash    |
| QC `no_overlap` strict-tolerance fail | #8, #9       | step 10 改 5% area-ratio tolerance（#8r + #9rd 確認 no_overlap 不再 fail）  |
| QC `position_preference` band 邊界硬   | #8r          | step 10c 改 10% per-edge tolerance（#8rc 1200×600 + #9rd 537×240 皆跑完）   |
| QC alias `center_top` ≠ `top_center`  | #7（首次發現）| step 9 副產品：8 alias + 17 regression test                                |

---

## 5. Paper 寫作指引

- **Results 章節**：表 1 + 表 2 直接套用，配 #1/#3/#7 的 PNG 三張對比圖（baseline → mid → 5-element）
- **Limitations 章節**：
  - N=1 sparsity validation（#7）— 誠實表達「on the single Crello sample our pipeline could fully evaluate」
  - Generator+QC robustness ceiling on tight canvas（#8/#9）— 已被 step 10 部分緩解但 5% 是 engineering compromise
  - Plateau 第二段 bal/coh=17（未解）— 推測來自 renderer 沒生 decorative shape / `default_white_background` 仍是 stub
- **Lessons learned**：
  - Prompt attention budget 真實存在（#6）— 多 14 行無關 ATTENTION 排擠 size_preference 注意力
  - Cheap-validate 再 live-burn — step 8 沒先 offline reproducer 就燒 $0.34
  - Multi-sample validation 揭露 single-sample 看不到的 robustness 限制（#8/#9 vs #7）

---

## 6. 重跑與資料來源檔

| #     | 主要 log / artifact                                                                                                |
| ----- | ------------------------------------------------------------------------------------------------------------------ |
| 1     | （早期未獨立保存 log，trace.json 在 `output/role_live_trace.json` 結構保留）                                       |
| 2     | （無獨立 log；postfix log 涵蓋 step 2/3 跑）                                                                       |
| 3     | `output/role_live_2026-05-14_areafix.log` + `output/role_live_last_reject.png`                                    |
| 4     | `output/role_live_2026-05-14_coverage.log`                                                                         |
| 5–6   | （與 #4 共用；step 8 commit 撤回後 log 仍保留，可從 git reflog 撈）                                                |
| 7     | `output/role_live_crello_*.{json,png}` (`5c6c0cba` 後綴)                                                           |
| 8     | `output/role_live_crello_*.{json,png}` (預設 `5954bda9` 早期 sample；driver 後升級 `--sample-id`)                  |
| 9     | `output/role_live_crello_5d972ca9abc8ea6d1c54e002_*.{json,png}`                                                    |
| 12c   | `output/live_step12b_5efdd2dd.log` + `output/role_live_crello_5efdd2dd499b85dcc75ba0bc_{trace,spec}.json` + `_last_reject.png`；離線根因 `output/debug_step12_failmode.py` |
| 8r    | （driver 末次跑後同檔覆蓋；下次重跑可加 `_step10` 後綴避免覆蓋）                                                   |
| 全部 commit 紀錄 | `git log --oneline -- metagpt/ext/agentlayout/`（dcf6c75f → 229b8e86 共約 8 個 step commit）            |

---

**最後更新：2026-05-19（加入 #12c 首個 content-aware live；#1–#9 標註為 pre-content-aware）**
