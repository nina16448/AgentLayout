# A3 AI Annotation Replacement Protocol（SUPERSEDED — 僅作歷史記錄）

> ⚠️ **2026-07-12 作廢**：本檔全部建立在「既有 100 份標註為人類所做、可作
> calibration set」的前提上。同日使用者揭露該前提不成立——全部標註與裁決
> 本來就是 AI 模型（GPT-5.6 sol／Claude Fable 5／Gemini＋GPT 裁決）做的，
> 專案不存在任何人類標註。§1 calibration set、§5 Alternative Annotator Test、
> §6 gate、§10 預算、§11 待批事項全部作廢。現行正確版本見
> `layout_agent/ANNOTATION_PROVENANCE_CORRECTION.md` 與 `A3_EXPERIMENT_LOG.md` §27。
> 本檔保留不刪，作為 A3-13P 提案的歷史記錄。

> 建立日期：2026-07-12
> 作者角色：研究設計／評估方法（CODEX_HANDOFF §6 第 1 列）
> 原狀態：PROPOSED / 0 code / 0 new calls（現：SUPERSEDED）
> 依據：`CODEX_HANDOFF.md` §5 九點最低協定；`new_plam.md` §5–§8；`A3_EXPERIMENT_LOG.md` §20–§25。

---

## 0. 目標與不變條件

**目標**：建立一條平行、可驗證的 AI reference tree 路徑，用既有 100 份多人 human
annotation 校驗「AI annotators 能否取代新增人工標註」。**不是**把 `human_oracle`
改名，也不是重寫任何歷史 artifact。

**不變條件（違反任一即整個 run 作廢）**：

1. 既有 100 份 human oracle trees（`runs/a3/relation100_oracle_trees/`、兩個來源
   run 的 `adjudication/`）與所有 raw annotations（pilot 20：`annotation_T/hui/neiji.json`
   ＋裁決 `annotation_nina.json`；n80：`annotation_hui/neiji/nina.json`）位元組不動，
   作為封存 calibration set。
2. 所有新 artifact 寫入新 namespace（§4），provenance 一律 `ai_reference.v1`；
   任何檔案不得寫入 `human_oracle` source 字串。
3. 不重跑、不覆蓋任何 write-once artifact（含 T0/T2/T3 run、兩條 evaluation 線）。
4. Gate 未全數通過前，論文中 AI reference 只能作 secondary／scaling analysis，
   human reference 仍是 primary。

---

## 1. 封存 calibration set 與既有基線

| 項目 | 值 | 出處 |
| --- | --- | --- |
| Human reference trees | 100/100（pilot 20 + n80 80） | log §20、§22 |
| 每 sample raw 標註 | 3 份（pilot：T/hui/neiji；n80：hui/neiji/nina） | log §20.1、§22.1 |
| 三人 agreement（n80） | same-group Jaccard 0.571、edge Jaccard 0.357、role-type 0.658 | log §22.2 |
| T2 predicted tree vs human oracle | same-group macro F1 0.6044、edge F1 0.3394、type acc 0.7437 | log §25.1 |
| Downstream 結論 | Holm 後 9 檢定 8 顯著（T3vsT2 PCA 唯一未偵測差異）、`T0<T2<T3` | log §25.2 |

這些數字是本協定所有 gate 的錨點，凍結不得改動。

---

## 2. AI annotator 設計

### 2.1 組成

- **三個獨立 MLLM annotators（A1/A2/A3），優先不同模型家族**（例：OpenAI／
  Anthropic／Google 各一）。實際 provider 與 model snapshot 為**待使用者批准事項**
  （§10）；批准後寫入 frozen manifest，執行期不得更換。
- 若使用者只批准單一家族，三次獨立抽樣（不同 seed／temperature 由 manifest 凍結）
  只能稱 **single-model ensemble**，論文命名須如實，且 Gate 通過的宣稱強度降級
  （不得稱 cross-family validated）。

### 2.2 輸入白名單（與人類標註者完全相同的 annotation packet）

每個 annotator、每個 sample 只允許收到：

1. `annotation_packet.json` 內容：user brief、stable asset IDs、每 asset 文字內容
   與 metadata（不含座標）；
2. `asset_contact_sheet_*.png`（每 sample 1–2 張）；
3. frozen annotation 指示與 HumanAnnotation-等價 schema 說明。

**黑名單（runner 必須以程式硬性阻擋，違反 fail-loud）**：human labels（任何
`annotation_<人名>.json`、adjudication、oracle trees）、designer GT preview／
座標、R3 normalized geometry、T0/T2/T3 layouts 與 renders、任何 metric／score、
其他 annotator 的輸出。

### 2.3 輸出 schema 與 provenance

輸出 `annotation_ai_<annotator_id>.json`，欄位對齊 HumanAnnotation v1（semantic_type
enum、semantic_role 自由文字、same-group、parent-child、per-asset `uncertain`、
`sample_uncertain`），另加：

```json
{
  "source": "ai_reference.v1",
  "annotator_id": "A1",
  "provider": "<frozen>",
  "model_snapshot": "<frozen>",
  "prompt_sha256": "<frozen prompt 逐位元組 hash>",
  "packet_sha256": "...",
  "contact_sheet_sha256": ["..."],
  "request_id": "<provider request id>",
  "sampling": {"temperature": "...", "seed": "..."}
}
```

三個 annotators 使用**同一份 frozen prompt**（翻譯自現行 ANNOTATION_GUIDE 的機器
可讀版），prompt 於實作階段產出、由使用者批准後凍結 SHA-256；執行期任何字元變更
即中止。

---

## 3. Consensus 與 adjudication

### 3.1 第一層：預先凍結的 deterministic consensus 規則（zero-cost）

逐 sample 對三份 AI 標註套用，全部規則此刻凍結：

1. **semantic_type**：三者取眾數；三方互異 → 該 asset 進 escalation。
2. **same-group**：對每一 asset pair，≥2 位 annotators 同組即 majority pair。
   以 majority pairs 建圖；若該圖已傳遞閉合（每個連通分量都是 clique），
   各連通分量即 consensus groups；否則整個 sample 進 escalation。
3. **parent-child edges**：≥2 票的邊入選；若入選邊集合違反樹約束
   （cycle、多父、指向不存在 asset）→ 整個 sample 進 escalation。
4. **uncertain**：asset 層級 ≥2 票 uncertain 即 consensus uncertain；
   `sample_uncertain` 同規則。
5. **semantic_role（自由文字，非 primary 軸）**：取「semantic_type 與 consensus
   一致」的 annotators 中，annotator_id 字典序最小者的 role 文字。零決策、可重現。

### 3.2 第二層：blind escalation adjudicator（第四模型，付費）

- 只處理 §3.1 標記 escalation 的 samples／assets。
- 使用**未參與生成的第四個 model snapshot**（待批准；優先第四家族或同家族更大杯型）。
- 輸入＝原 annotation packet ＋ 三份**匿名化**（去除 provider／model 欄位、隨機
  重排為 X/Y/Z）的 AI 標註；同樣禁止 §2.2 黑名單內容。
- 輸出逐分歧決定＋理由；寫入 `ai_adjudication_record.json`（含所有 §2.3 provenance
  欄位與 per-decision 來源）。
- 所有 disagreement（位置、三方票型、最終決定）完整落盤，對齊 human 線的
  `adjudication_record.json` 粒度。

### 3.3 產出

每 sample 一份 `annotation_ai_consensus.json` ＋ `ai_oracle_trees/<sample_id>.json`
（`source: "ai_reference.v1"`），經與 human 線相同的 `annotation_to_oracle_tree`
結構檢查；all-or-nothing finalize，任何 sample 失敗即整批不發布。

---

## 4. Artifact namespace（全部新建，不觸碰 human 線）

```text
layout_agent/runs/a3/a3-airef-annot-n100-01/
  manifest.json                      # frozen providers/snapshots/prompt hash/budget
  samples/<sample_id>/annotation_ai/
    annotation_ai_A1.json / A2 / A3
    annotation_ai_consensus.json
    ai_adjudication_record.json      # 僅 escalated samples
  ai_adjudication/
    ai_oracle_trees/<sample_id>.json # source="ai_reference.v1"
    ai_finalization.json             # 含全檔 SHA-256
layout_agent/evaluations/a3-alt-test/a3.alt-test.v1/<evaluation_id>/
layout_agent/evaluations/a3-relation-stats/a3.relation-stats.v1/
  a3-relation-n100-sgc-tlc-pca-stats-airef-v1/        # AI-ref 重算（§6）
layout_agent/evaluations/a3-tree-accuracy/a3.tree-accuracy.v1/
  a3-relation-n100-t2-tree-accuracy-airef-v1/
```

sample 集合＝`sample_ids/a3_relation_n100.json`（與 human reference 同 100 ID，
逐位元組沿用、hash 記入 manifest）。annotation packets 逐位元組沿用兩個既有
annotation run 的 `annotation_packet.json` 與 contact sheets（記 SHA-256），
不重新生成，保證 AI 與人類看到完全相同的輸入。

---

## 5. Alternative Annotator Test（primary 驗證）

依 alt-test 方法（Calderon et al. 2025）改寫到 tree annotation：**AI（consensus 前
的單一 annotator，非 consensus 結果）逐一頂替每位 human annotator，檢驗它與其餘
human 的對齊是否不劣於被頂替者。**

### 5.1 程序（每批分開跑：pilot 20 與 n80 80 的 annotator 集合不同）

對每個 sample s、每位 human annotator j（該批 3 位）：

1. `align_AI(s,j)` ＝ AI annotator 與「其餘兩位 human」的平均 agreement；
2. `align_H(s,j)` ＝ human j 與同樣那兩位的平均 agreement；
3. instance 差值 `d(s,j) = align_AI(s,j) − align_H(s,j) + ε`。

對每位 j 做 one-sided paired test（exact sign test 為主、Wilcoxon 敏感度分析），
H0: `d ≤ 0`。AI「勝過」j 若 p < 0.05。**winning rate ρ ＝ 勝過的 human 比例；
通過準則 ρ ≥ 0.5**（3 位中至少 2 位）。三個 AI annotators 各自報告，主結論用
表現中位的 annotator（避免挑最好的 cherry-pick）；consensus 版另列參考。

### 5.2 凍結參數

- **Agreement 函數（primary 兩軸）**：same-group pairwise Jaccard、parent-child
  edge Jaccard——與 log §22.2 human 基線同定義同實作（`human_tree_metrics.py`
  的 agreement 路徑）。semantic-type agreement 為 secondary。
  **free-text exact role 不作 primary**（degenerate lower bound，log §25.1）。
- **ε（cost-advantage margin）**：primary ε=0.2（沿 alt-test 原文），敏感度分析
  ε=0.1 與 ε=0 全部報告。若只有 ε=0.2 通過而 ε=0 明顯不通過，論文措辭必須寫
  「在成本優勢邊際下不劣於」而非「達到人類水準」。
- **uncertain 處理**：任一方標 uncertain 的 asset 排除於該 pair 的 primary
  agreement（沿 human 協定），排除量逐批報告。
- **通過定義**：same-group 與 parent-child **兩軸都** ρ ≥ 0.5 才算 Gate-R2 通過；
  semantic-type 只報告不設門檻。

---

## 6. Downstream stability gate

以 `ai_oracle_trees` 取代 human oracle，對**凍結的** T0/T2/T3 layouts（
`a3-rel100-t0/t2/t3-01`，不重生成）決定性重算，與 §25.2 bundle 逐項對照：

1. **方向**：SGC/TLC/PCA 三軸 arm means 維持 `T0 < T2 < T3`。
2. **統計結論**：§25.2 Holm 後 8 個顯著比較，在 AI reference 下重跑同一
   9 檢定 Holm 程序，**8 個全部同方向且 Holm p < 0.05**；
   T3vsT2 PCA 維持「未偵測到差異」或變為同方向顯著（不得反向顯著）。
3. **Tree accuracy 一致性（secondary，僅報告）**：T2 predicted tree 對 AI reference
   的 same-group／edge F1，與對 human reference 的 per-sample 值之 Spearman 相關；
   以及 AI-ref 與 human-ref 的 per-sample SGC/TLC/PCA 相關。無硬門檻，
   低相關（<0.5）須在論文 discussion 揭露。

1＋2 全過＝**Gate-R3 通過**。任何一項不過即 gate 失敗：AI reference 降級為
secondary analysis，不得宣稱可取代人工。

**Gate 總表**：

| Gate | 內容 | 通過準則 | 成本 |
| --- | --- | --- | --- |
| R1（diagnostic） | AI–AI inter-annotator agreement（same-group／edge Jaccard） | 無門檻，與 human 0.571／0.357 並列報告 | $0 |
| R2（primary） | Alternative Annotator Test §5 | 兩 primary 軸 ρ≥0.5（ε=0.2） | $0（用已買標註） |
| R3（primary） | Downstream stability §6 | 方向＋8/9 Holm 結論穩定 | $0 |

**論文措辭規則**：R2＋R3 全過 → 可寫「AI-generated annotations 經既有
multi-human calibration set 驗證，downstream conclusions stable」。任一未過 →
AI reference 僅 secondary／scaling analysis。**無論結果如何**，不得稱現有 T3
（human-tree 生成）為 human-free；要宣稱 oracle arm 去人工化必須另跑
`T3-AI` 新 generation run（本協定範圍外，另行提案）。

---

## 7. MLLM-panel preference protocol（獨立階段，可延後）

替代已永久跳過的 human preference study（log §23.7 裁示 1）；結果一律稱
**MLLM-panel preference**，不得寫成 human preference。

- **配對**：50 pairs＝25 Relation（T2 vs T0，凍結 render）＋ 25 General
  （final B0 vs designer GT），sample 以凍結 seed 自各自 N=100 抽取、抽樣先於
  任何 judge call。
- **Panel**：3 個獨立 MLLM judges（優先不同家族、且與 §2 annotators 及生成
  pipeline 的 `gpt-5.4-mini` 不同 snapshot；至少不得與被評 arm 的生成模型相同）。
- **Blind A/B ＋位置交換**：每 pair 每 judge 問兩次（A/B 與 B/A），無方法名稱、
  無 arm 標籤；同 judge 兩次矛盾記為 position-sensitive tie。
- **三問**：overall preference、readability、semantic grouping clarity
  （對齊 new_plam §7.4）。
- **報告**：逐 judge win/tie/loss ＋ exact sign test ＋ bootstrap CI；
  另報 judge 間一致性（pairwise agreement／Fleiss κ）。不得只報 pooled 結果。

---

## 8. 預計新增／修改檔案（實作角色範圍，全部離線可測）

新增：

| 檔案 | 用途 |
| --- | --- |
| `metagpt/ext/agentlayout/tools/annotation_ai.py` | AiAnnotation schema、packet 白名單載入、黑名單 fail-loud guard、AI annotator runner（付費 gate 沿 `a3_paid_budget.py`） |
| `metagpt/ext/agentlayout/tools/ai_consensus.py` | §3.1 deterministic consensus＋escalation 判定＋§3.2 adjudicator runner＋finalizer |
| `metagpt/ext/agentlayout/evaluation/a3_alt_test.py` | §5 alt-test（複用 `human_tree_metrics.py` agreement 函數） |
| `layout_agent/evaluate_a3_alt_test.py` | alt-test CLI（read-only、write-once bundle） |
| `tests/metagpt/ext/agentlayout/test_annotation_ai.py`、`test_ai_consensus.py`、`test_a3_alt_test.py` | 離線測試（§9） |

修改（最小侵入）：

| 檔案 | 變更 |
| --- | --- |
| `layout_agent/run_a3.py` | 新增 `prepare-ai-annotation`／`run-ai-annotation`／`finalize-ai-adjudication` 子命令（重用既有 preflight／budget／write-once 骨架） |
| `metagpt/ext/agentlayout/tools/human_tree_metrics.py` | 若 agreement 函數為私有則抽出可重用介面（行為不變，既有 24＋ tests 全綠為前提） |
| `layout_agent/evaluate_a3_tree_accuracy.py`、`analyze_a3_relation_stats.py` | 新增 `--oracle-trees-from` 指向 ai_oracle_trees 的參數（預設不變） |

**不動**：`annotation.py`、`adjudication.py` 的 human 路徑行為；`layout_tree_v3.py`；
所有既有 run／evaluation artifacts。

## 9. 離線測試計畫（zero-cost，實作完成的驗收條件）

1. AiAnnotation schema round-trip 與 provenance 必填欄位驗證。
2. 黑名單 guard：packet 目錄含 `annotation_hui.json`／oracle tree／R3 geometry
   時 runner 拒絕啟動（fail-loud）。
3. Consensus 決定性：同輸入雙跑 byte-identical；眾數／majority-pair 傳遞閉合／
   樹約束／uncertain 各規則的正反 fixture。
4. Escalation 判定：三方互異 type、非傳遞 same-group 圖、cycle edge 各觸發。
5. alt-test 數學：手工可驗的合成 fixture（已知 align 值 → 已知 ρ 與 p）；
   ε 掃描單調性。
6. `--oracle-trees-from` 切換不影響 human 預設路徑（既有 753 tests 全綠）。
7. Write-once：對已存在 bundle 重跑必須 fail、不覆蓋。
8. 預算 gate：無 `--allow-api-calls` 時 preflight 印 budget 後 exit 2、零網路呼叫。

---

## 10. 精確預算提案（每 stage 各自授權，未批准前一律 0 call）

估算依據：packet 平均 1.4KB／max 3.2KB；contact sheets 1–2 張/sample（n80 實測
89 張/80 samples）；human 標註輸出平均 4.1KB／max 11.5KB（≈1,100／3,000 tokens）；
影像以 2,500 tokens/張、指示+schema 以 2,000 tokens 計。

| Stage | 內容 | Calls（nominal→cap） | Input cap | Output cap | USD 估算→cap |
| --- | --- | --- | ---: | ---: | --- |
| S1 | 3 annotators × 100 samples | 300 → **390**（1.3× retry） | **3.2M** | **0.9M** | ~$2.5–6 → **$8** |
| S2 | blind escalation adjudicator（估 ≤50 samples） | 50 → **65** | **0.75M** | **0.25M** | ~$0.6–1.5 → **$2** |
| S3 | consensus／finalize／R1 | 0 | 0 | 0 | $0 |
| S4 | alt-test（R2） | 0 | 0 | 0 | $0 |
| S5 | downstream 重算（R3） | 0 | 0 | 0 | $0 |
| S6 | MLLM preference panel（§7，可延後） | 300 → **330**（50×3×2＋probe/retry） | **2.1M** | **0.2M** | ~$2.5–4 → **$5** |

- USD 估算基於 gpt-5.4-mini-class 有效混合單價（§23.9/§24.5 ledger ≈US$1.2–1.35/M
  blended）；跨家族 annotators 單價可能 2–5×，**USD cap 於 provider 凍結時逐
  provider 換算後重新提交確認**。
- 全案付費上限提案：**S1+S2 ≤ US$10；S6 另案 ≤ US$5**。任一 cap（calls/input/
  output/USD）觸頂即中止、保留 write-once 進度，不自動追加。

## 11. 待使用者批准事項（實作前必須逐項定案）

1. **三個 annotator providers／model snapshots**（是否跨家族；若否，接受
   single-model ensemble 降級命名）。
2. **第四 adjudicator model**。
3. **是否允許將 Crello contact sheets 上傳至 OpenAI 以外的供應商**
   （Anthropic／Google 等；資料授權與隱私由使用者判斷）。
4. frozen prompt 文本（實作階段產出草稿後、付費前送批）。
5. §10 各 stage 預算與啟動順序（建議：S1→S2→S3–S5 出 gate 結果後，再決定 S6）。
6. alt-test ε 主值 0.2 是否接受（保守替代：0.1 為主值）。

---

## 12. 建議執行順序

```text
使用者批准 §11 → 實作角色（§8 檔案、§9 測試全綠、零付費）
→ 方法審核角色（bias/leakage/統計 audit）
→ 使用者授權 S1+S2 預算 → AI annotation run（write-once）
→ S3–S5 zero-cost gates（R1/R2/R3）
→ gate 結果回報 → 使用者決定 S6 與論文措辭層級
```
