# PKU-PosterLayout 可行性評估（scoping memo，不含實作）

**日期**：2026-06-13　**性質**：翻案評估，回應「能不能跑 PKU、最少要怎麼改」
**前置決策**：2026-06-04「不跑 PKU」（architectural commitment trade-off，depth×narrow vs breadth×shallow）。本 memo **不推翻該決策**，只把工程現況重新盤清、量化每條路徑的成本，供日後拍板。

---

## 0. TL;DR

- **機械上「能不能跑」= 能。** 走固定模板 + BypassJudge + repo 內 `sega_metrics`，約 **150–200 行**一個 `run_pku_batch.py`、**零核心重構**，可跑 997 樣本的 Val/Ove/Rea/Occ。
- **但這個數字是「閹割版 AgentLayout 跑 PKU」**：元素集是假的、underlay 兩軸 by construction = 0、content-aware Judge / refinement loop 沒上場。**不能宣稱 SOTA 對標**，只能當 indicative。
- **要做到忠實的 generative SOTA 比較**，必須補 ① element proposer ＋ ② underlay 合成——後者超出 schema scope（no-decoration 邊界），正是 2026-06-04 否決的那塊。
- **與 2026-06-04 memo 的關鍵差異（須更新認知）**：當時記「eval 要靠外部 `eval.sh pku my.pt`」。**現況：PKU/SEGA content-aware 指標套件與 PKU-spec saliency 已在 repo 內**（見 §2），評估端不需外部 harness。plumbing 比舊結論樂觀。

---

## 1. PKU 任務本質（為何與 Crello 互斥）

PKU-PosterLayout 是 **generative content-aware layout**：輸入只有 canvas 底圖（商品已合成在背景）＋ saliency map，模型要**自己決定**要放幾個 box、各是哪一類（text / logo / underlay），並輸出座標。**沒有 design asset 可吃。**

AgentLayout 走 **placement**：吃 Crello 已給的 asset（圖＋文字內容），Analyst 分析 asset 語意、Generator 擺已知元素、Judge 對渲染後成品做 content-aware 美學評分。兩者的 I/O 承諾互斥——這是 depth×narrow vs breadth×shallow 的根本選擇。

---

## 2. 現況盤點：已在 repo、可直接用（本 session 親驗）

| 元件 | 檔案 | PKU 用途 | 狀態 |
| --- | --- | --- | --- |
| PKU/SEGA 指標套件 | `evaluation/sega_metrics.py` | alignment / overlay / underlay loose+strict / readability / occlusion；class `CLS_TEXT=1 / CLS_IMAGE_LOGO=2 / CLS_UNDERLAY=3` **正好 PKU 1/2/3**；Layout=`List[(cls, xyxy)]` | ✅ 檔頭明寫「SEGA defers to PKU underlay/occlusion definitions」 |
| PKU saliency | `evaluation/saliency_basnet_isnet.py` | Occ 指標的 BASNet+ISNet 兩段 SOD，回傳 `(H,W)` float32 ∈[0,1] | ✅ 已照 PKU spec |
| 幾何生成 | `tools/constraint_solver.py:567 solve_placement` | 純構造數學擺 bbox、**不讀 asset_ref** | ✅ canvas-only |
| LLM 生成 | `actions/generate_layout.py` | emit (left,top,w,h,z,font)、生成時不讀 asset 檔 | ✅ canvas-only |
| QC（≈Validity） | `tools/quality_checker.py` | box 在界內、非退化、coverage/dead-band | ✅ canvas-only |
| 背景 saliency | `tools/background_analyzer.py` | U2Net safe-zone（給 solver） | ✅ |
| schema | `schema.py` Element `asset_ref: Optional[str]=None` | 允許建抽象 typed 元素、不綁 asset | ✅ Optional |

**結論**：評估端與幾何端都齊了。唯一硬卡的是 **Analyst**（`actions/analyze_brief.py` 要讀 `asset_list`）。

---

## 3. 最小改動清單（若日後決定跑「最小可跑版」）

| # | 缺口 | 最小做法 | 估行數 |
| --- | --- | --- | --- |
| ① | **元素集要自己生**（generative 缺口） | 固定模板：每樣本 1 title + 1 body + 1 logo（+選配 1 underlay）。**這是 placement 假裝 generative**，非真任務 | ~10 |
| ② | bypass Analyst、注入 DesignSpec | 手搭抽象 typed 元素（`asset_ref=None`、content=placeholder）、canvas bg 指 PKU 圖；AssetAnalyzer 可照跑（lookup 表）或 stub | ~30 |
| ③ | PKU loader | HF `creative-graphic-design/PKU-PosterLayout` ralf-style、test=**997**、座標 **513×750** native pixel（**載入時須複驗，數據來自 2026-06-04 盤點**） | ~50 |
| ④ | 輸出→指標 glue | `LayoutElement → (cls, xyxy)`、scale 513×750、餵 `sega_metrics.*`；class remap text/logo/underlay→1/2/3 | ~30 |
| ⑤ | driver + BypassJudge | 串 ②③④、跑 997、聚合 mean | ~40 |

**合計約 150–200 行、一個 `run_pku_batch.py`、零核心重構。** 預估一天可出數字。

---

## 4. 結構性天花板（決定數字有沒有意義）

1. **Underlay 兩軸 by construction = 0**：`sega_metrics.py:26` 自註「we never emit underlay shapes → `metric_underlay_loose/strict` 回 0」。PKU 招牌指標含 Und_l/Und_s，schema 無裝飾元素表達力（[[feedback-underlay-is-placement]] / no-decoration 邊界）→ **兩軸固定 0**。同 Crello plateau 同源。
2. **Content-aware Judge / refinement loop 無訊號**：無真 asset，渲染只有空 box，美學 judge 失去依據。須 **BypassJudge**，等於只跑幾何 Generator——**系統最 novel 的部分（K-candidate 美學挑選、oracle 迴圈、構圖師）全沒上場**。
3. **元素集是假的**（固定模板）：除非補 ① 的 LLM element-proposer，否則跑的不是 PKU generative，是「對幻影 box 做 constrained placement」。補 proposer = 啟動 B 階段 generative 改造（2026-06-04 否決點）。

---

## 5. 數字怎麼定位（誠實 framing）

- 最小可跑版的 Val/Ove/Rea/Occ 只能寫成 **indicative / 系統在純幾何任務的落點**，**不可**與 SEGA Table 3 / PosterO 同格、不可宣稱對標。Und=0 須明白標註為 scope 邊界、非 bug。
- 跨 paper 數值本就因 underlay labeling 與 judge drift 不可直接同格（沿用 Crello SEGA 對照的同一 caveat）。
- 真正能寫進論文的，仍是 2026-06-04 的正向 framing：**architectural commitment trade-off（depth vs breadth）**，不寫成 limitation。

---

## 6. 路徑成本一覽（供拍板）

| 路徑 | 工程 | 產出 | 可宣稱 |
| --- | --- | --- | --- |
| **A. 最小可跑** | ~150–200 行、零重構、~1 天 | 997 樣本 Val/Ove/Rea/Occ（Und=0） | indicative 落點；**不**對標 SOTA |
| **B. 忠實 generative** | A ＋ element-proposer(~50–80 行) ＋ underlay 合成（**超出 schema scope、大改**） | 接近 PKU 真任務 | 仍受 judge-no-signal 限；underlay 合成屬另一研究問題 |
| **C. 不跑** | 0 | 維持 2026-06-04 決策 | depth×narrow 正向定位 |

**建議**：若只是想「看一眼系統在純幾何 PKU 上的落點」→ A 划算且誠實標 indicative。若目標是「能跟 SEGA/PosterO 同表對標」→ 需 B，且 underlay 那段碰到 schema scope 天花板，回到 2026-06-04 的同一個架構抉擇，不建議為對標而做。

---

## 7. 已查清、未動工的對齊細節（翻案直接可用，源自 2026-06-04，載入時複驗）

- HF `creative-graphic-design/PKU-PosterLayout`，`ralf-style` config，test=**997**。
- box 格式 HF `[x1,y1,x2,y2]` → 內部 `(left,top,w,h)`；class HF `0/1/2` → PKU `1/2/3`（+1 並 skip INVALID=3）。
- 座標雙邊 **513×750** native pixel、左上原點+寬高。
- 外部 harness（**可選、非必要**）：`export DATASET_ROOT=...; source eval.sh pku <my.pt>`；in-repo `sega_metrics` 已可替代。

**關聯**：[[project-no-pku-postero-alignment]]（原始決策）、[[feedback-underlay-is-placement]]（underlay scope 邊界）、[[project-generator-bounded-line-closed]]（judge-no-signal 同源於 Generator-bounded 收斂）。
