# 發現與決策：A3 論文證據審查

## 需求

- 使用者提供 `Thesis.tex`，要求忽略其中所有舊數字。
- 目標是判斷目前 A3 正式結果能否支撐論文，並列出仍需補的實驗。
- 不修改論文，不執行付費 API 或模型實驗。

## 目前已知證據

- A3 最終配置為 Vision Analyst、T2 predicted tree、P-Full、R3、L0。
- N=20 Gate C 支持淘汰 L1-Gated；N=20 Gate A 提供 vision 對 text-only 的方向性證據。
- Relation N=100 提供 T0/T2/T3 的 human-reference semantic metrics、正式 SEGA 與 matched COLE。
- General N=100 提供正式 SEGA 與 General-vs-GT matched COLE。
- Full-Crello N=1,971 尚未完成；目前只有 batch 001 readiness，付費 generation 為 0 calls。

## Manuscript 初步索引

- `Thesis.tex` 共 636 行、約 9,773 words；主要結構為 Introduction、Related Work、Proposed Method、Implementation Details、Experimental Results、Conclusion、Future Work。
- 中央方法定位是 training-free、explicit Layout Tree、reason-then-place 的 BCEC foreground layout generation。
- 第 578 行宣稱 principal visual claims 由 human preference judgments 支持；目前 A3 實際沒有完成 human preference study，這是待全文確認的 claim-to-provenance mismatch。
- Implementation 明確區分 Crello-General random N=100 與 Crello-Relation relation-rich N=100，這與現有 A3 正式 tracks 大致一致。

## Manuscript 前半（lines 1–320）

- Abstract（89–103）與 Contribution 3（150–157）仍以舊資料主張：三種尺度 overlap 低於 designer、65/100 wins、91.3% designer ceiling、介於 SEGA-7B/13B。這些數字不屬於目前 A3，必須全部移除或以新 A3 結果重寫。
- Introduction 的可保留核心 contribution 是：explicit sample-specific Layout Tree、reason-then-place、training-free、P-Full/R3 與 deterministic verification；這些可由程式與正式 run provenance 支持。
- 「To the best of our knowledge, first framework」屬 novelty claim，未做文獻完整性查證前不能視為已驗證；建議降為較窄定位或另做文獻查核。
- Introduction 沒有具名 RQ／hypotheses；目前只有三個 contributions。實驗章若要形成可審查的證據鏈，至少需要明列：Tree prediction、Tree causal effect、overall quality、repair effect 四個 RQ。
- Method 把 L1-Gated single repair 寫進正式 pipeline 與輸出符號，但目前 final configuration 已由 Gate C 定為 L0；應改成「evaluated ablation, not final system」或把 main method 改為 L0。
- Problem Formulation 宣稱 placeable assets 可包含 underlays；正式 P-Full v1 評估卻沒有合法 underlay class，Und_l/Und_s 為 N/A。Methods、schema 與 evaluation scope 必須一致。
- Abstract 說四個 LLM agents，正文另有 MLLM Internal Judge；需明確說四個 generation/reasoning agents＋一個 internal selection module，避免 agent count 混亂。

## Manuscript 後半（lines 321–636）

- `Experimental Results`（595 起）與 `Conclusion`（603 起）完全空白；現稿不能進行實質 submission readiness 判定，必須用新 A3 evidence 重建兩節。
- `Implementation Details` 的 fixed model snapshot、General/Relation tracks、P-Full、R3、K=3 與 L0/L1 shared-R0 設計大致對得上 A3；但最終 main configuration 應明確寫成 L0，L1 只保留為 negative Gate C ablation。
- `Internal Judge`（約 560–578）再次宣稱 principal visual claims 由 human preference 支持；實際未執行，屬 blocking claim mismatch。
- Methods 多次將 underlay 當作 ordinary placeable asset，並宣稱 underlay relation／QC／render 已啟用；正式 A3 evaluation 記錄 P-Full v1 無合法 underlay class，需由程式／run contract 再核對後決定刪除 claim 或補實作與實驗。
- Quality Checker 文字宣稱 hard-violation candidate 不 eligible，且三候選皆不 eligible 時 sample fail；General run 卻記錄 selected-B0 QC 31/100 passed、67 `all_qc_failed` degradation。需核對 pipeline 是否實際採 least-violating fallback；若是，Methods 現在不忠實。
- Future Work 合理列出 visual grounding、typography、responsive composition、closed-model dependence，但缺少目前最重要的 validity limitations：無 human preference、same-model COLE judge、Relation selection、General incomplete-universe sampling、underlay N/A、multiple comparisons。
- 「same sample identifiers are shared across all controlled comparisons」只在各 track 內成立；General 與 Relation 是不同 sample tracks，不可讓讀者理解成全實驗共享同一 N=100。

## Authoritative implementation mismatches

- `a3_pipeline.py` 在三個 R0 slots 全部 `qc_passed=false` 時只追加 `all_qc_failed` degradation，仍把所有 slots 與 QC flags／violations 交給 Judge-Select。論文所寫「hard violation 不 eligible；無 eligible 即 sample fail」錯誤，必須改成實際 fail-loud degradation policy，或修改程式後重跑全部正式實驗。
- `PFullAsset.semantic_hint` 合法值只有 `base_background/image/text/text_bitmap`；非背景 raster 一律歸為 `image`，legacy geometry-derived underlay classifier 被忽略。正式 evaluator 因此正確將 Und_l／Und_s 記為 N/A。
- 論文若保留 underlay as ordinary placeable asset、underlay tree relation、QC 與 renderer claim，就需要新增 versioned explicit underlay class、重跑 P-Full/R3／生成／評估；較低成本且與現有證據一致的做法是從 current scope 移除這些主張。

## A3 evidence matrix：Smoke／Gates／Relation N=100

| Claim | Evidence | Verdict |
|---|---|---|
| Pipeline 可執行、tree 在 coordinates 前 frozen、P-Full/R3 無 GT bbox leakage | N=5 smoke 最終 L0 5/5；contract tests／trace／prompt hashes | Supported as implementation claim，不是品質 claim |
| L1-Gated 能改善輸出 | Gate C N=20：19 repairs、B1 只保留 3、strict improvement 1/20、compliance 34.2% | Unsupported；現有證據支持 final L0 與 controlled negative |
| Vision Analyst 優於 text-only | Gate A N=20：edge/type 方向有利，same-group 無利；多比較與小樣本 | Weak／exploratory；可作設計 gate，不宜當 main contribution |
| Human reference tree 品質穩定 | 80-sample raw agreement：same-group Jaccard .571、edge .357、type .658；全數逐分歧 adjudication | Oracle 可用，但 agreement 限制重大；Methods 必須交代 annotators、uncertainty、adjudication |
| Predicted Tree 改善 semantic layout realization | Relation N=100 T2>T0：SGC .7037>.6465、TLC .6711>.6277、PCA .7614>.6930；paired raw p .0032/.0019/.0128 | Supported with qualification；Bonferroni 9 tests 後最穩為 SGC/TLC，PCA claim 應降級 |
| Better tree 帶來更好 semantic realization | T3>T2，SGC raw p=.0022、TLC=.0134、PCA=.064 | Partial；保守 correction 後主要只剩 SGC |
| Tree 改善 geometry／aesthetics | Formal Ali/Ove/Rea/Occ 無改善；COLE T2-vs-T0 Δ=.112, CI [-.099,.329], p=.219 | Unsupported；只能說未偵測到美學提升，且 CI 不支持 equivalence claim |
| A3 接近／超越 designer | Relation COLE T2=5.355 vs GT=6.738，6W/83L/9T，Δ=-1.375 CI [-1.571,-1.176] | Contradicted |
| Formal six-axis 可直接與 SEGA published table 比 | Occ 用 BASNet+ISNet、ISNet 取代 PFPN；Underlay N/A；Rea 無訊號 | Unsupported；只能 matched internal comparison，不可跨論文數值比較 |

## Relation experiment reporting requirements

- 報告 3/300 source failures、各 arm n 與 paired intersections，不能只列成功均值。
- 把 §23.3 raw p 轉為預先聲明的 Holm／Bonferroni／FDR adjusted p，並補 paired difference CI。
- 將 semantic improvement 與 aesthetic/geometry null 分開寫；不能把「未顯著變差」寫成 equivalence 或 no-cost trade-off，除非新增 equivalence margin／test。

## A3 evidence matrix：General N=100

| Claim | Evidence | Verdict |
|---|---|---|
| Final A3 pipeline reliably completes | 100/100 completed、714 attempts、formal source/evaluator reload、foreground coverage 100/100 | Supported |
| Final A3 has strong deterministic quality | selected-B0 QC 31/100 passed；67 `all_qc_failed`；low contrast 44、undersized title 31、out-of-bounds 20 | Contradicted as a broad claim；只能分軸報告 |
| Final A3 approaches designer aesthetics | COLE 5.4675 vs GT 6.6725 = 81.94%；10W/85L/5T；Δ=-1.205 CI [-1.420,-.9925] | Quantified but clearly below GT；不可寫 parity／near-ceiling without an a priori margin |
| General geometry is competitive | A3 有 Ali/Ove/Rea/Occ absolute values，但沒有同 protocol external baseline／GT geometry aggregate | Insufficient for comparative claim |
| General N=100 represents complete official test split | 選樣時 local pool 1,902；後續 pinned audit 更正為 official overlap 1,897、5 extras、74 official missing；selected 100 本身皆 official | Partial external validity；不是完整 official-universe simple random sample |
| Full-Crello robustness | 1,971 cache／batch readiness 已建立，但新 1,871 generation/evaluation 尚未執行 | Not yet evidence |

## General reporting requirements

- Results 應同時呈現 100% completion 與 31% QC-pass／67% degradation，避免用 completion 掩蓋 visual quality。
- `missing_element` 的 17 筆是 background false positive，需修 evaluator/QC 或在表格中分開，不可誤報 foreground failure。
- General-vs-GT COLE 是 same-model blind absolute scoring；可量化差距，但不能替代 independent judge／human preference。
- 抽樣母體應改寫為當時實際可用 official overlap，並揭露 74 個 official samples 在選樣時 selection probability 為 0；完成 full test 或 fresh full-universe sample 才能消除此限制。

## 遇到的問題

| 問題 | 解決方案 |
|---|---|
| Underlay narrow search 同時含不存在的 `tools/pfull.py`，命令 exit 2 | 保留正確 `pfull_preprocessor.py` 命中，不重跑錯誤路徑；後續只用已確認檔案 |

## 初始風險

- 沒有 human preference study。
- Relation subset 是 semantic-rich track，外部效度有限。
- COLE judge 與生成模型同 family/snapshot，存在 same-model evaluation confound。
- 原始 tree 統計報告沒有統一做 multiple-comparison correction。

## Integrity gate 適用檢查

- 將每個 compound claim 拆成可分別判定的 atomic claims；任一子主張沒有證據時，整句不能判為完全支持。
- 每個 experiment-backed claim 必須指向具體 artifact、metric 與 manuscript locator，不能只寫「實驗顯示」。
- skipped／未執行實驗不得被寫成已完成；negative/null results 與 material limitations 必須出現在 Results、Discussion 或 Limitations。
- 本次 integrity 部分只做 disclosure 與 claim-to-provenance fidelity；統計充分性與研究設計優劣由 methodology review 另判。
- 論文舊數據既已被使用者宣告失效，任何依賴舊表格的 claim 目前一律先視為 unsupported，直到換成 A3 artifact。

## Reviewer mode

- 採 `methodology-focus`，由 Field Analyst、EIC 與 Methodology Reviewer 三個角色完成 field framing、整體可投稿性與方法／統計審查。
- 本次不做所有 reference 的逐項 WebSearch，因此不能宣稱 Stage 2.5 integrity PASS；輸出只涵蓋 experiment evidence 與 internal claim consistency。
- 每項批評必須指出 manuscript locator、問題、證據與可執行修正；不直接修改 `Thesis.tex`。

## Paper-blind methodology scoring plan

- RQ–design alignment：若主要主張需要 causal／comparative 證據，但只有單臂展示或不匹配資料軌，判為 block。
- Sampling／external validity：檢查 Relation semantic-rich track 與 General random track 是否被正確區分；若從 Relation 推廣至全 Crello，判為 block 或至少 major warning。
- Statistical validity：要求 effect magnitude、95% CI、明確 denominator、failed/skipped rows 與 multiple-comparison policy；只列 p 值或只列成功樣本判為 warning／block。
- Evaluation independence：若生成模型與唯一 aesthetic judge 相同且沒有 human／independent judge robustness，核心美學 claim 判為不充分。
- Reproducibility：要求 exact model snapshot、sample IDs、protocol、artifact hashes、per-sample records 與 negative results 可追溯。
- EIC coherence：檢查 Title→Abstract→RQ→Conclusion 是否承諾超過 A3 證據；若 claim 是 designer parity、SOTA 或 general aesthetic superiority，預設觸發 over-promise block。

## Statistical reporting minimum for A3

- 每個主要連續 metric 應報 N、mean、分布／SD 或適合的 robust summary；只有 aggregate mean 不足以判斷穩定性。
- Paired comparisons 除 exact sign-test p 值外，至少報 paired mean／median difference 與 bootstrap 95% CI；若主張實質提升，需說明 effect magnitude。
- 同一資料上三軸乘多組 arm comparisons 必須預先選 Holm／Bonferroni／FDR 其一，並同時保留 raw p 與 adjusted p。
- 非顯著比較不能直接解讀為「相同」；應報 CI／equivalence margin 或 sensitivity analysis，否則只能寫「未偵測到差異」。
- failed、skipped、not-applicable、ties 與 denominator 必須完整揭露；General 與 Relation 不可因資料軌不同而直接作跨軌效果比較。
- A3 不需要套用常態 t-test assumption；現有 sign test 與 bootstrap 合理，但仍需說明 pairing、bootstrap seed／replicates 與多重比較處理。

## Methodology-focus sprint contract precommitment

### D1 methodology_rigor

- `what_to_look_for`：核心 RQ 與 A3 experimental matrix 是否一一對應；matched controls、sample tracks、failure accounting、statistical uncertainty、judge independence、reproducibility artifacts 是否齊全。
- `what_triggers_block`：中央 contribution 依賴目前不存在的 human preference／matched baseline／full-test 結果；或 manuscript 把 null／不同協定／同模型 judge 證據寫成 general superiority、designer parity 或 causal aesthetic gain。
- `what_triggers_warn`：核心方向已有 N=100 證據，但 effect/CI/multiplicity／外部效度報告不足，且可由 reanalysis、claim narrowing 或一個有限新實驗補救。

### D2 writing_and_structure

- `what_to_look_for`：Title、Abstract、Introduction、Methods、Results、Discussion、Conclusion 是否使用同一 A3 架構版本與同一 evidence vocabulary。
- `what_triggers_block`：論文大部分仍描述舊架構／舊 protocol，使讀者無法知道真正被評估的方法；Results 與 Conclusion 的核心敘事需要重建。
- `what_triggers_warn`：架構可辨識但表格、段落或 limitations 尚未同步，能以局部重寫修正。

### Contract decision rule

- 任一 D1 `block` → Reject 或 Major Revision；任一 D1 `warn` → 至少 Major Revision；只有兩位 reviewer 的 D1 都 `pass` 才可能 Accept。

## 技術決策

| 決策 | 理由 |
|---|---|
| 以 claim-to-evidence matrix 為核心輸出 | 直接回答「夠不夠支撐」而不是泛泛審稿 |
| 小樣本 gates 與 N=100 正式證據分層 | 避免以探索性結果覆蓋正式結果 |
| 補實驗按 reviewer-blocking risk 排序 | 節省時間與付費成本 |

## 原始 aggregate 最終核對

- Relation SEGA bundle：300 rows；T0 100、T2 98+2 skipped、T3 99+1 skipped。正式 aggregate 的 Ali／Ove／Rea／Occ 與實驗紀錄一致；Und_l／Und_s 是 N/A，不是 0。
- Relation COLE bundle：397/397 ok；GT 100、T0 100、T2 98、T3 99。T2−T0 與 T3−T0 的 S_mean4 CI 都跨 0；三臂對 GT 的 CI 都完全低於 0。
- General SEGA bundle：100/100 evaluated；六軸數值與紀錄一致；Underlay 同樣 N/A。
- General COLE bundle：200/200 ok；General−GT Δ=−1.205，95% CI [−1.420, −0.9925]，10W/85L/5T。
- SEGA manifest 明記 Occ 採 frozen BASNet＋ISNet 且 ISNet replaces PFPN；只能作 A3 內部 matched comparison，不能宣稱和 published SEGA bit-exact 或直接數值可比。

## 尚未完成但現有資料可零成本補算

- 全庫未找到 Relation N=100 predicted Layout Tree 對 human oracle 的正式 aggregate。Gate A 只有 N=20；N=100 的 SGC/TLC/PCA 是 final layout realization，不是 tree prediction accuracy。
- T2 run 有 99 棵有效 predicted trees，human oracle 有 100 棵；現成 `evaluate_tree_prediction()` 可在 99 個交集上計算 same-group P/R/F1、parent-child P/R/F1、semantic-type accuracy，並將 1/100 Planner failure 納入 denominator／failure report。
- 全庫未找到任何外部生成方法在相同 A3 P-Full／R3／renderer／sample IDs／evaluator 下的 matched baseline。T0/T2/T3 是內部消融，designer GT 是上限，都不是外部 baseline。

## 最終 reviewer verdict

- 領域／稿型：CS/AI content-aware graphic layout generation；定量 systems/method thesis；目前是 Results 與 Conclusion 尚待重建的 revision draft。
- EIC：**Major Revision**（confidence 4/5）。核心 contribution 可辨識且有 N=100 因果消融，但 Abstract、Contributions、Methods、Results 與實際 A3 版本未同步。
- Methodology-focus：**Major Revision**（confidence 5/5）。D1 目前有 block：不存在的 human-preference evidence、無 external matched baseline 卻有跨方法主張、同模型 aesthetic judge、未校正多重比較；但可由 claim narrowing、零成本 reanalysis 與一個有限 human study 補救，不需推翻整套架構。
- 估計面向分數（非完整 citation review）：originality 72/100、methodological rigor 60/100、evidence sufficiency 55/100、coherence 52/100、writing clarity 80/100；整體約 62/100，屬可修成合格論文、但不能照現稿提交。
- 本次不是完整 reference/citation integrity audit，也沒有外部文獻檢索；`first framework` 等 novelty claim 仍需另行查核。

## 最小必要補實驗與停止條件

### P0：提交前必做，零付費

1. **Relation N=100 direct tree accuracy**：固定現有 100 IDs；99 個成功樣本算 macro mean＋bootstrap 95% CI，1 個 Planner failure 明列。Primary 建議 same-group F1；secondary 為 parent-child F1、semantic-type accuracy；exact free-text role 不作 primary。
2. **既有 Relation 統計重分析**：預先指定一個 primary endpoint，或對 9 個 paired tests 使用 Holm correction；補 paired effect difference／bootstrap CI、各 arm n、pairwise intersection 與 3/300 failures。若用保守 Bonferroni，T2>T0 只穩健保留 SGC/TLC；T3>T0 三軸；T3>T2 主要保留 SGC。
3. **同步 manuscript contract**：Final system 明寫 L0；L1 是 negative ablation；Underlay 移出 current scope；QC 寫成 all-QC-failed 帶標記進 Judge-Select；刪除已完成人測、designer parity、SOTA／SEGA direct-comparison 句子。

### P1：若要保留「視覺品質／人可感知改善」主張，必做

4. **Blind human preference**：固定 Relation 100，主比較 T2 vs T0；隨機左右位置、隱藏方法、每樣本至少 3 位評分者。Primary 問「相關元素是否更成組／關係更清楚」，overall aesthetic 另列 secondary。固定完成 100×3 judgments 後停止，報 tie、rater agreement 與 sample-clustered CI／mixed-effects analysis。若要談 designer gap，再另做 T2 vs GT；不可混成同一 primary test。
5. **Independent evaluation robustness**：若人測暫時做不到，可用與 generator 不同 family/snapshot 的 judge 重評作 robustness；只能當補強，不能冒充 human preference。
6. **External matched baseline（有 SOTA／優於 prior art 主張時必做）**：至少一個可重跑的 BCEC baseline，使用同 100 IDs、P-Full/R3 或清楚對齊的 input contract、同 renderer/evaluator。若無法做到，刪除 direct-superiority claim，只保留 T0 internal ablation 與 GT gap。

### P2：強烈建議或由主張決定

7. **General sampling 修正**：Full-Crello readiness 完成後，至少重新從完整 official 1,971 抽一個 seeded N=100；若成本允許再跑 full 1,971。這改善外部效度，但不能取代 human/baseline。
8. **L1 N=100**：只有保留「incrementally correctable／repair improves quality」為 contribution 時才跑；否則把 Gate C negative result 如實報告即可。
9. **Monolithic/single-agent ablation**：只有標題與核心 contribution 繼續強調 multi-agent 優勢時才需要；較省成本的做法是把主軸改成 explicit Layout Tree。
10. **Underlay implementation／evaluation**：只有 current scope 繼續承諾 underlay 時才需要 versioned schema＋全流程重跑；建議本論文先刪除此 scope。

## 可提交的最窄核心結論

AgentLayout 是一個 training-free、inspectable 的 reason-then-place pipeline；其 predicted Layout Tree 在 Relation N=100 的 matched internal ablation 中，改善 final layout 對 human reference structure 的 semantic realization。現有結果沒有證明 tree 改善整體美學或幾何，也顯示系統仍顯著落後 designer GT；這些 negative results 應作為限制而非隱藏。

## 資源

- `/home/hui0705/.codex/attachments/f6579b0b-3db1-4a80-a51e-ad6d75ad78a5/Thesis.tex`
- `layout_agent/A3_EXPERIMENT_LOG.md`
- `layout_agent/evaluations/a3-sega/`
- `layout_agent/evaluations/a3-cole/`

## 視覺／瀏覽器發現

- 本次不使用瀏覽器或外部服務。

---
*每兩次重要檔案查閱後更新。*
