# Elem2Design 外部 Baseline 完整實驗結果（A3-13E）

**實驗日期**：2026-07-12 ～ 07-13　|　**流水帳**：`A3_EXPERIMENT_LOG.md` §26–26.2
**Commits**：`e25e7dfd`（harness）→ `36f81671`（主結果）→ `2fd74ee8`（sensitivity）→ `db8a54d7`（Rea/Occ 補齊）

---

## 1. 實驗問題

在**完全相同**的 Crello-Relation N=100 條件下，A3-T2（predicted Layout Tree pipeline）相較於公開可重跑的 Elem2Design（LaDeCo），是否能更好地實現 semantic grouping 與 layout-tree relations？

## 2. 設定與凍結版本

| 項目 | 值 |
|---|---|
| Baseline | Elem2Design / LaDeCo（LLaVA-Llama-3.1-8B LoRA、五層 autoregressive＋中間 render 回饋） |
| Code repo | `github.com/microsoft/elem2design` @ `4665358e` |
| Adapter | HF `microsoft/elem2design` @ `c4f20b5b`（LoRA 168MB＋mm_projector 42MB） |
| Base model | `meta-llama/Llama-3.1-8B` @ `d04e592b`；vision tower `openai/clip-vit-large-patch14-336` |
| Layer roles | 官方 `crello_role.pkl`（覆蓋 100/100、與本機元素數 100/100 對齊） |
| 生成 | temperature 0.7 / top_p 0.95 / num_return 1 / seed 42（官方推論設定） |
| A3 對照臂 | 凍結 `a3-rel100-t2-01`（gpt-5.4-mini-2026-03-17、三候選＋internal selection） |
| 共同條件 | 同 100 個 sample ID、同 R3 bitmaps、同 canvas、同 oracle trees、同 evaluator（`human_tree_metrics`＋`sega_metrics`） |
| 硬體/成本 | 4×GTX 1080 Ti、4-bit NF4、bnb compute dtype fp32；N=100 wall ~70 分鐘、mean 122s/sample；**零付費 API** |

**公平性防洩漏**（fail-closed）：baseline 輸入僅含 R3 bitmap／文字內容／canvas 尺寸／官方 predicted roles；test.json 全檔遞迴 forbidden-key 掃描、R3 逐檔 SHA-256 核對、對話 gpt 輪全部 `"{}"` 佔位——GT 幾何/角色與任何 A3 產物皆不可能進入模型輸入。

**Pascal 硬體 patch（全部記錄於 `infer_patched.py` docstring）**：官方 4-bit 路徑未被測過，修了 transformers kwarg 衝突、量化參數誤初始化、mm_projector 誤量化三個 bug；`use_cache=True`；bnb compute dtype fp16→fp32（1080 Ti 實測 9.25 vs 2.09 tok/s，**4.4×**）。

## 3. 完成率與失敗（全數明列，零剔除、零修補、零重試）

| 臂 | 完成 | 失敗 |
|---|---|---|
| Elem2Design | **94/100** | 6 筆 explicit conversion failure |
| A3-T2（凍結） | 98/100 | 2 筆（CandidateShortfall、Planner 重複 ID） |
| **配對交集** | **93** | |

E2D 失敗明細：`5931132c`(n=23)、`5a21848d`(n=24)、`5d9cad82`(n=26)、`5f3b84c8`(n=37) 輸出截斷/缺漏；`5f644f40`(n=32) 與 `5bbcbdfd`(n=10) 重複元素——其中 5 筆 n≥23，與其訓練上限 `max_num=25` 一致。`5f644f40` 兩臂皆敗（A3 側為 Planner 失敗）。

## 4. 主結果（B0 vs E2D 單發；diff = A3-T2 − E2D）

Bootstrap 95% CI＝sample-level percentile、seed 20260712、10,000 次；sign test＝exact two-sided（排平手）；Holm within family。

| Metric | A3-T2 mean (n) | E2D mean (n) | Paired N | W/L/T | Mean diff [95% CI] | p raw | p Holm |
|---|---:|---:|---:|---|---|---:|---:|
| **SGC** | **0.7037** (98) | 0.5355 (94) | 93 | 74/19/0 | +0.1684 [+0.1271, +0.2077] | 7.7e-09 | **1.5e-08** |
| **TLC** | **0.6711** (98) | 0.5092 (94) | 93 | 81/11/1 | +0.1578 [+0.1221, +0.1932] | 2.5e-14 | **7.5e-14** |
| **PCA** | **0.7614** (98) | 0.6450 (94) | 93 | 50/24/19 | +0.1167 [+0.0617, +0.1713] | 0.0034 | **0.0034** |
| Ali ↓ | 0.0012 (98) | **0.0002** (94) | 93 | 13/3/77 | +0.0010 [+0.0003, +0.0019] | 0.0213 | 0.0213 |
| **Ove ↓** | **0.1173** (98) | 0.2496 (94) | 93 | 9/84/0 | −0.1347 [−0.1604, −0.1086] | 2.2e-16 | **4.3e-16** |

## 5. Selection-asymmetry sensitivity（移除 A3 三候選優勢）

| 變體 | SGC | TLC | PCA | Ali↓ | Ove↓ |
|---|---|---|---|---|---|
| **first candidate**（字面單發） | 77/16、+0.182、p=2.9e-10 | 75/18、+0.177、p=3.8e-09 | 49/21、+0.122、p=1.1e-03 | 8/3/82、+0.0005、ns (p=0.23) | 11/82、−0.128、p=2.8e-14 |
| **mean over 3**（單發期望值） | 77/16、+0.172、p=1.9e-10 | 82/11、+0.161、p=4.3e-14 | 55/30、+0.112、p=8.8e-03 | 27/3/63、+0.0011、p=8.4e-06（E2D 較好） | 7/86、−0.124、p=4.2e-18 |

**判讀**：語意三軸的優勢完全不依賴 candidate selection——單發對單發仍全軸 Holm 顯著，效果量與 B0 版相當甚至略大（selection 並未挑高語意指標）。

## 6. Rea/Occ 補齊（同背景＋凍結 BASNet+ISNet、只換框）

PKU 協定的 Rea/Occ 是背景圖＋元素框的函數；背景兩臂完全相同，A3-T2 值逐字取自凍結 formal SEGA bundle。

| 軸 | A3-T2 | E2D | W/L/T | Mean diff [95% CI] | Holm(4) p |
|---|---:|---:|---|---|---:|
| **Occ ↓** | **0.005629** (98) | 0.007339 (93) | 16/75/2 | −0.00166 [−0.00216, −0.00114] | **5.3e-10** |
| Rea ↓ | 0.0 (98) | 0.0 (93) | 0/0/93 | 0 | 無資訊軸（平坦背景、Sobel 零訊號） |

**完整幾何家族 Holm(4)**（取代主 bundle 暫行 Holm(2)）：Ove **6.5e-16**（A3）、Occ **5.3e-10**（A3）、Ali 0.0213（E2D）、Rea 無資訊。

## 7. 六軸總計分板

| 軸 | 結論 |
|---|---|
| SGC / TLC / PCA（主指標） | **A3 全勝**（Holm ≤0.0034；無 selection 仍全勝） |
| Ove（重疊） | **A3 大勝**（p=6.5e-16；E2D 重疊為 A3 的 2.1 倍） |
| Occ（顯著區遮擋） | **A3 勝**（p=5.3e-10） |
| Ali（對齊） | E2D 微勝（幅度 +0.001 級、77–82/93 平手） |
| Rea（可讀性） | 無訊號（兩臂全 0，協定性質） |
| Und_l / Und_s | 全場 N/A（P-Full v1 無合法 underlay 欄位，A3 三臂亦同） |

**一句話**：五個有訊號的軸中四個顯著利 A3-T2，唯一例外是幅度 0.001 級的 Alignment。

## 8. 引用時必述的限制

1. 結論僅限 **Crello-Relation N=100**，不得外推為全 Crello 或 SOTA 宣稱。
2. E2D 為單一 seed（42）單次生成；抽樣變異未量化（sensitivity §5 已排除 selection 不對稱的主要疑慮）。
3. Oracle trees 為**多模型 consensus** 標註（非人類標註），SGC/TLC/PCA 的 ground truth 品質受此限制。
4. 5 筆 n≥23 的 E2D 失敗與其訓練上限 `max_num=25` 相關——屬模型能力範圍限制，已照協定明列不剔除。
5. Und 兩軸為協定性 N/A，並非測得 0。

## 9. Artifacts 與重現

| Bundle | 路徑（`layout_agent/evaluations/a3-external/`）| aggregate / per-sample SHA-256（前 12 碼） |
|---|---|---|
| 評測 | `a3.external-baseline-eval.v1/elem2design-rel100-v1/` | `b65932a56dbd` / `ddb3a5b1271c` |
| 主比較 | `a3.external-baseline-compare.v1/a3-t2-vs-elem2design-rel100-v1/` | `c3861cd22c78` / `3137f8120c99` |
| Sensitivity | `a3.external-baseline-sensitivity.v1/a3-t2-nosel-vs-elem2design-rel100-v1/` | `cf2c0d52e80d` / `5b6c3d1ba2eb` |
| Rea/Occ 補齊 | `a3.external-baseline-supplement.v1/e2d-rea-occ-rel100-v1/` | `194f03b243b0` / `d36ff929d15f` |

全部 bundle write-once，manifest 內含輸入/程式/artifact 完整 hash。Raw run artifacts（five-turn outputs、中間 renders、per-sample candidate/error）在 `layout_agent/runs/external/elem2design-rel100-v1/`（不入 git）。程式：`layout_agent/external_baselines/elem2design/`（prepare→infer→convert→evaluate→compare→sensitivity→supplement，13＋protocol 單元測試）。重跑環境：conda env `e2d`（py3.10）、1080 Ti 必設 `E2D_BNB_COMPUTE_DTYPE=float32`。
