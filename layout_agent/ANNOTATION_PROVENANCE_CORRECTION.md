# Annotation Provenance 更正（authoritative correction sidecar）

> 日期：2026-07-12
> 揭露者：使用者本人（nina16448），於本日 session 口述
> 效力：本檔與 `A3_EXPERIMENT_LOG.md` §27 為 annotation provenance 的**唯一正確版本**，
> 覆蓋所有既有紀錄（log §16–§22 的敘述、oracle tree 檔內的 `source="human_oracle"`
> 字串、`CODEX_HANDOFF.md` 舊版 §3.2）。凍結 artifacts 本身位元組不動
> （保持 finalization SHA-256 鏈完整）；更正以本 sidecar 為準。

---

## 1. 更正內容

**專案中不存在任何人類標註。** 先前記錄為「人工／三人標註」的全部 reference tree
標註與裁決，實際均為使用者在**各自全新的 chat session** 中操作 AI 模型產生，
指示內容為 `a3-gateab-pilot-n20-01/ANNOTATION_GUIDE.md`，輸入僅有
annotation packet（brief、asset IDs、文字內容）與 asset contact sheets
（依 guide 設計，無設計師成品圖、無座標、無背景圖；此點為操作者自述，
無機器層級 log 可稽核）。

### 代號 → 實際模型對照表

| 代號 | n80（`a3-relation-annot-n80-01`） | pilot（`a3-gateab-pilot-n20-01`） |
| --- | --- | --- |
| `hui` | GPT-5.6 sol | GPT-5.6 sol（待使用者確認） |
| `neiji` | Claude Fable 5 | Claude Fable 5（待使用者確認） |
| `nina` | Gemini（版本待確認） | —（raw 標註者為 T/hui/neiji） |
| `T` | — | GPT（確切 snapshot 待確認） |
| 裁決（pilot `annotation_nina.json` 全量重標；n80 逐分歧裁決） | GPT（確切 snapshot 待確認） | 同左 |

待確認欄位由使用者補充後更新本表；在此之前引用一律註明「exact snapshot
not recorded」。

### 既有紀錄中因此失效的陳述

1. log §16–§22 中所有「人工標註」「三位標註者」「human adjudication」
   「使用者（adjudicator nina）完整獨立重新標註」等**把標註主體描述為人類**的
   文字——實際主體是 AI 模型，人類角色是操作者（開 session、貼入 packet、
   轉錄輸出、修正 schema 錯誤）。
2. 120 棵 oracle trees（pilot 20＋n80 80，合併於 `relation100_oracle_trees/`
   100 棵）檔內 `source="human_oracle"` 字串——為歷史誤標。本檔定義正確語意：
   `human_oracle`（legacy 字串）實際 ≡ **`ai_reference.multi-model.v0`**
   （跨家族三模型標註＋GPT 裁決、人類操作）。
3. `CODEX_HANDOFF.md` §3.2「human reference trees 100/100」與
   `AI_REFERENCE_PROTOCOL.md` §1「封存 human calibration set」的前提。

## 2. 不受影響的部分

- **所有實驗數值不變、不重跑**：T0/T2/T3 generation、SGC/TLC/PCA（§23.3、§25.2）、
  tree accuracy（§25.1）、Gate A/B（§21）、SEGA/PKU 與 COLE 兩條評測線
  （§23.8/23.9/§24，均不依賴 reference tree 的來源性質）。
- 所有 write-once artifacts、hash、finalization records 位元組不動。
- 標註流程的盲測設計本身（不看 GT、不看彼此、獨立 session）仍成立。

## 3. 重新解讀規則（論文與後續引用一律遵守）

1. **T3 臂**：由「human tree oracle 上限」改稱
   「**跨家族多模型 consensus reference tree 注入**」。`T0 < T2 < T3` 梯度
   （§23.3、§25.2 Holm 8/9 顯著）仍為真實結果，新敘事為：
   單模型即時預測的 tree（T2, gpt-5.4-mini 單次)＜三個跨家族模型獨立標註
   ＋裁決形成的 consensus tree（T3）——**tree 品質（ensemble/consensus）帶來
   顯著語意組織增益**。
2. **標註者間一致性**（n80：same-group Jaccard 0.571、edge Jaccard 0.357、
   role-type 0.658）：由「三人 agreement」改稱 **inter-model agreement
   （GPT-5.6 sol × Fable 5 × Gemini）**。這是 reference 可信度的主要證據。
3. **uncertain 分布**（nina 0、hui 23、neiji 527）：屬**模型別行為差異**
   （Gemini 從不標 uncertain、Fable 50.4% uncertain），論文引用時不得描述為
   標註者個性。
4. **Alternative Annotator Test 永久取消**：無人類標註可作基準，
   `AI_REFERENCE_PROTOCOL.md` §5 作廢。論文**不得**宣稱
   「AI 標註經人類校準驗證」或任何 human-annotated/human-validated 字眼。
5. 建議論文措辭（英文）：
   *"reference layout trees were produced by three independent cross-family
   MLLM annotators (GPT-5.6 sol, Claude Fable 5, Gemini), each operating in an
   isolated session on identical annotation packets (brief, asset IDs, text
   content, contact sheets; no designer ground truth or coordinates), with
   disagreements consolidated by a GPT-based adjudication pass; the pipeline
   was operated and curated by the authors."*

## 4. 必須揭露的 limitations

1. **裁決模型不獨立**：裁決由 GPT 執行，與標註者之一（GPT-5.6 sol）同家族，
   存在 self-preference bias 風險；裁決傾向哪一票未做統計（可事後補算：
   裁決結果與三票的逐項吻合率，zero-cost）。
2. **Provenance 不完整**：各 session 的確切 prompt 全文、模型 snapshot 版號、
   sampling 參數未存檔；指示以 ANNOTATION_GUIDE.md 為準之陳述不可機器稽核。
3. **輸入合規為操作者自述**：模型只見 packet＋contact sheet 一事無 API log 佐證。
4. **reference 非人類金標**：所有以此 reference 計算的指標（SGC/TLC/PCA、
   tree accuracy）衡量的是「與跨家族模型共識的一致性」，不是「與人類語意
   判斷的一致性」。

## 5. 後續動作

- [x] 本 sidecar 建立；log §27 更正條目；handoff §3.2/§3.4/§5 改寫。
- [ ] 使用者補充對照表待確認欄位（pilot hui/neiji 對映、T 與裁決的確切模型、
      Gemini 版本）。
- [ ] （建議、zero-cost）裁決一致性統計：n80 485 個分歧中，裁決採 GPT-5.6 sol
      票的比率 vs 採 Fable/Gemini 票的比率，量化 self-preference bias。
- [ ] 論文寫作角色依 §3 措辭規則全面替換 human 字眼。
