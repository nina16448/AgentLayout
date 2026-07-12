# Elem2Design × A3 P-Full 外部 Baseline 實驗實作規格

## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: plan
- Origin Date: 2026-07-12
- Verification Status: UNVERIFIED（尚未下載模型或執行 inference）
- Version Label: a3_elem2design_plan_v1

## 1. 實驗問題與範圍

**問題**：在完全相同的 Crello-Relation N=100、P-Full/R3 assets、canvas、
renderer、human oracle 與 evaluator 下，A3-T2 相較於公開可重跑的
Elem2Design（LaDeCo）是否能更好地實現 semantic grouping 與 layout-tree
relations？

- A3 arm：既有 frozen `a3-rel100-t2-01`。
- External arm：Elem2Design 官方公開 checkpoint，單次 generation，seed=42。
- Primary metrics：SGC、TLC、PCA（higher is better）。
- Secondary metrics：Ali、Ove、Rea、Occ，沿用 A3 evaluator 的方向與定義。
- 不執行 COLE/LVM judge：它需要額外模型或付費 API，且不是完成 matched
  geometry/semantic baseline 的必要條件。
- 結論範圍只限 Relation N=100；不得泛化成全 Crello 或 SOTA。

## 2. 凍結來源與版本

| 項目 | 固定值 |
|---|---|
| Sample IDs | `layout_agent/sample_ids/a3_relation_n100.json` |
| A3 source inputs | `layout_agent/runs/a3/a3-rel100-t2-01/` 的 P-Full/R3 manifests |
| Human oracle | `layout_agent/runs/a3/relation100_oracle_trees/` |
| Elem2Design repo | `microsoft/elem2design@4665358e0d06aa5d4365e63cd0e4b6df12902666` |
| Elem2Design adapter | `microsoft/elem2design@c4f20b5b8496f6627260a5b38d65a736391bfa63` |
| Base model | `meta-llama/Llama-3.1-8B@d04e592bb4f6aa9cfee91e2e20afa771667e1d4b` |
| Generation | official temperature=0.7、top_p=0.95、num_return=1、seed=42 |
| A3 renderer/evaluator | 執行時記錄目前 git commit 與 relevant file SHA-256 |

上述 Llama model 是 manual-gated；若 Hugging Face 帳號未接受授權，這是
第一個 hard blocker。模型與程式均為本機 inference，沒有付費 API call。

## 3. 公平性與防洩漏規則

### 3.1 可提供給 Elem2Design 的資料

- canvas width/height；
- 每個 R3 foreground bitmap；
- text asset 的文字內容；
- asset ID 與 `source_index` 的對應；
- 官方發布的 **predicted** Crello layer roles；
- 由模型先前預測結果產生的 intermediate render。

### 3.2 禁止資料

- GT `left/top/x/y/bbox/width/height/angle/z-index`；
- designer composite 或任一含 GT placement 的 layer image；
- GT layer role；
- A3 predicted tree、human oracle tree、A3 candidate 或 A3 render；
- P-Full manifest 中的 `native_width/native_height` 數值欄位。

官方 `dataset/src/crello/render.py` 不得使用，因為它明確以 GT
`example["width"]` / `example["height"]` resize elements。adapter 只能直接讀
R3 bitmap；建立 test JSON 後必須做 recursive forbidden-key scan，且檢查所有
input image hash 都等於 R3 manifest 中的 hash。

### 3.3 元素覆蓋

- 每個 P-Full placeable asset 必須在輸入出現一次、輸出出現一次。
- 不得因 Elem2Design 訓練時 `max_num=25` 而刪除樣本或元素。
- 目前 N=100 為 min=6、mean=13.43、max=37；5 筆 >25。
- >25 樣本照常執行；若 context/OOM/parse 失敗，記為 explicit failure。
- 報告 selected=100、completed、failed、paired N；不可只報成功樣本平均。

## 4. 預計新增的程式與 artifact contract

所有新程式放在：
`layout_agent/external_baselines/elem2design/`。

### 4.1 `prepare_inputs.py`

輸入：sample ID JSON、A3 source run、官方 `crello_role.pkl`、output dir。

工作：

1. 驗證 sample IDs 順序、P-Full/R3 provenance 與 asset coverage。
2. 用 `source_index` 對應官方 predicted role；缺漏或長度不符就 fail closed。
3. 只用 R3 bitmap、文字內容與 canvas 建立五層 conversation JSON。
4. 建立白色或合法 P-Full background 的初始畫布；不得讀 designer preview。
5. 輸出 `input_manifest.json`、`test.json`、`sample_ids.json` 與所有來源 hashes。

### 4.2 `infer_patched.py`

從官方 inference 做最小、可追蹤 patch：

- 新增 `--load-4bit`、`--device-map`、`--seed`、`--resume`；
- 呼叫官方已存在的 `load_pretrained_model(..., load_4bit=True)`；
- 移除 `model.to("cuda")`，避免破壞量化 device map；
- tensors 搬到模型實際 input device，而不是 hard-code `cuda`；
- Pascal GPU 不安裝/啟用 flash-attn；
- 每個 sample 完成即 append JSONL 並 fsync，resume 時依 sample ID 跳過；
- 保存 raw five-turn outputs、seed、GPU、峰值 VRAM、elapsed time 與 exception；
- 不自動 retry generation；單一樣本失敗後記錄並繼續下一筆。

官方 role planning 結果是 baseline 的一部分；不另呼叫 GPT 產生 roles。

### 4.3 `convert_outputs.py`

1. 以 official element `index` 綁回 A3 `asset_id`。
2. `left/top/width/height` 只做 finite-number 與正值驗證，再 round 成 pixel int；
   不用 GT 修補、不預先 clip。
3. `z_index = layer_index * 1000 + order_in_layer`，保持五層前後順序。
4. color/font 欄位有合法值才映射；缺值保留 `null`。
5. duplicate、missing、extra ID 或非正 bbox 令該 sample 失敗。
6. 用 A3 R3 renderer 重新 render final candidate，保存 SHA-256。
7. 輸出獨立 `a3.external-baseline-run.v1`，不得偽造成三候選 A3 L0 result。

預期 run bundle：

```text
layout_agent/runs/external/elem2design-rel100-v1/
├── run_manifest.json
├── sample_ids.json
├── input_manifest.json
├── raw_predictions.jsonl
├── run_summary.json
└── samples/<sample_id>/
    ├── candidate.json
    ├── final_render.png
    ├── sample_record.json
    └── error.json                 # 僅失敗樣本
```

### 4.4 `evaluate_external_baseline.py`

- 新增 generic external extractor，但共用現有 A3 的 bbox clipping、Ali/Ove/Rea/
  Occ、renderer contract 與 frozen detector lineage。
- 從同一 human oracle 呼叫既有 `evaluate_layout_realization` 算 SGC/TLC/PCA。
- write-once 發布 evaluation bundle，保存 source artifact hashes。
- 不修改既有 A3 evaluation bundle，也不把 single-output baseline 填成三個假 slots。

### 4.5 `compare_external_baseline.py`

- 比較 Elem2Design 與 frozen A3-T2。
- 每個 metric 使用兩臂成功樣本的交集；同時獨立報兩臂 failure rate。
- 報 mean、paired mean difference、sample-level bootstrap 95% CI
  （10,000 resamples、seed=20260712）、exact sign test W/L/T。
- SGC/TLC/PCA 為 primary family，Holm correction 3 tests。
- Ali/Ove/Rea/Occ 為 secondary family，Holm correction 4 tests。
- 不以「p > .05」宣稱等效；不把 Relation subset 結果泛化到 General。

## 5. 實作與執行階段

### Gate 0 — 存取、空間與 GPU preflight（15 分鐘）

驗收：

- Hugging Face token 能讀取 manual-gated Llama-3.1-8B；
- `/home` 至少保留 40GB free；
- 至少兩張 GPU 各有約 10GB free；
- clone/model revisions 與 licenses 寫入 manifest；
- 不使用目前有其他程序佔用的 GPU。

**立即停損**：15 分鐘內仍無 Llama access，停止 baseline，不改用非官方模型冒充。

### Gate 1 — 環境與模型載入（45–90 分鐘）

建立獨立 Python 3.10 environment，安裝 official repo、OpenCole、dataset package、
bitsandbytes、peft、transformers；不安裝 flash-attn。下載 adapter/base model 到
指定 cache，第一次只做 load-and-exit，確認 4-bit + device map 能成功載入。

**立即停損**：連續兩種合理配置（單卡 4-bit、雙卡 4-bit）都 OOM 或相依性
無法在 60 分鐘內修復，停止正式實驗並保留 error log。

### Gate 2 — Adapter 實作與單元測試（90–150 分鐘）

最低測試：

1. forbidden GT keys/data never enter test JSON；
2. R3 input hashes exact match；
3. `source_index ↔ asset_id ↔ model index` round trip；
4. duplicate/missing/extra elements fail closed；
5. >25 elements are not truncated；
6. bbox finite/positive validation；
7. z-order deterministic；
8. write-once/resume 不覆寫已完成 sample；
9. same candidate rendered twice has identical SHA-256；
10. evaluator recomputation matches persisted aggregate。

### Gate 3 — N=1 integration probe（10–20 分鐘）

只跑 Relation 第一筆。必須完成五層 generation、18/18 asset coverage、合法 candidate、
final render 與一筆 metric record；任何失敗先修，不得直接放大到 N=5。

### Gate 4 — Frozen N=5 smoke（20–45 分鐘）

Smoke IDs 固定為 `a3_relation_n100.json` 前 5 筆並另存 hash snapshot。

通過條件：

- 5/5 inference process 完成；
- 至少 4/5 產生 coverage-complete、可 render、可 evaluate 的 candidate；
- 0 GT leakage；0 silent truncation；
- 每筆 raw output/candidate/render/error 都可追溯；
- 以實測前 5 筆速度估算 N=100 ETA。

若只有 4/5，允許進 N=100，但必須先理解該 failure 且確認不是系統性 schema bug。
≤3/5 則停止，不跑 N=100。

### Gate 5 — N=100 正式 inference（預估 3–8 小時）

- 使用 write-once run ID `elem2design-rel100-v1`；
- GPU monitoring 只看指定 PID、VRAM 與 output JSONL 成長；
- hard timeout 10 小時；沒有人工改 output、沒有 silent retry；
- 跑完 10 筆時重新估 ETA；若 ETA 無法在論文封版前留出至少 2 小時分析時間，
  停止並把現有結果標為 exploratory pilot，不能寫成 N=100 formal baseline。

### Gate 6 — Evaluation、統計與論文表格（45–90 分鐘）

1. 驗證 bundle hashes、coverage 與失敗數。
2. 共用 evaluator 算 7 個 metrics。
3. 產生 paired statistics、Holm-adjusted p-values、95% CIs。
4. 產出 `results.md`、`results.tex`、`aggregate.json`、`per_sample.jsonl`。
5. 論文同時報 absolute means、paired N、failures 與 protocol deviations。

## 6. 預計命令介面（程式完成後）

```bash
python layout_agent/external_baselines/elem2design/prepare_inputs.py \
  --sample-ids layout_agent/sample_ids/a3_relation_n100.json \
  --source-run layout_agent/runs/a3/a3-rel100-t2-01 \
  --elem2design-root /home/hui0705/external/elem2design \
  --output-root layout_agent/runs/external/elem2design-rel100-v1

CUDA_VISIBLE_DEVICES=0,1 python \
  layout_agent/external_baselines/elem2design/infer_patched.py \
  --model-name-or-path microsoft/elem2design \
  --data-path layout_agent/runs/external/elem2design-rel100-v1/test.json \
  --output-dir layout_agent/runs/external/elem2design-rel100-v1 \
  --load-4bit --device-map auto --seed 42 --resume

python layout_agent/external_baselines/elem2design/convert_outputs.py \
  --run-dir layout_agent/runs/external/elem2design-rel100-v1

python layout_agent/external_baselines/elem2design/evaluate_external_baseline.py \
  --run-dir layout_agent/runs/external/elem2design-rel100-v1 \
  --oracle-dir layout_agent/runs/a3/relation100_oracle_trees \
  --evaluation-id elem2design-rel100-v1

python layout_agent/external_baselines/elem2design/compare_external_baseline.py \
  --baseline-evaluation elem2design-rel100-v1 \
  --a3-run layout_agent/runs/a3/a3-rel100-t2-01 \
  --output-id a3-t2-vs-elem2design-rel100-v1
```

這些是**預計實作的 CLI contract**，目前檔案尚未建立，不可直接執行。

## 7. 明天中午期限下的決策

- 值得立刻做，但必須嚴格 gate；真正的不確定點是 gated model access、Pascal
  4-bit 相容性與 500 次 layer-generation 的速度。
- 最晚在 N=5 smoke 後作一次 go/no-go；不要為了 baseline 犧牲論文 Results、
  Discussion、Limitations 與編譯時間。
- 若 formal N=100 未完成，只能把 N=5/N=20 寫成 exploratory feasibility，
  論文主結論仍以 T0/T2/T3 與 tree-accuracy 結果為主。
