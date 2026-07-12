# 進度日誌：A3 論文證據審查

## 會話：2026-07-12

### 階段 1：論文 intake 與 claim 抽取

- **狀態：** completed
- 已完成：
  - 讀取 academic-research-suite、academic-pipeline 與 planning-with-files 規範。
  - 完整恢復既有 Full-Crello active plan；session catch-up exit 0、無遺漏。
  - 確認 full-Crello N=1,971 尚未完成，不能作為現有論文證據。
  - 建立本次隔離 ledger，未切換 `.planning/.active_plan`。
  - 分段讀取 integrity verification 規範前 460 行；凍結 atomic-claim、experiment provenance、negative-result 與 limitation visibility 檢查。
  - 完整讀取 integrity verification 690 行與 reviewer workflow 430 行；本次模式定為 methodology-focus，並保留「非完整 citation integrity audit」限制。
  - 完整讀取 Field Analyst、EIC、Methodology Reviewer 與通用 review criteria；在看論文前凍結 paper-blind methodology scoring plan。
  - 完整讀取 statistical reporting standards 393 行；凍結 A3 的 descriptive、CI、effect magnitude、multiplicity、denominator 與 null-result 報告要求。
  - 完整讀取 editorial decision、quality rubric、methodology-focus contract、review quality thinking 與 report template；完成 paper-blind D1/D2 scoring precommitment。
  - 取得 `Thesis.tex` 行數、字數與結構索引；初步定位 human-preference claim 與實際 A3 provenance 衝突。
  - 完整讀取 `Thesis.tex` lines 1–320；抽取 abstract／contributions／method claims，定位舊數字、L1-vs-L0、underlay scope 與 missing explicit RQ 問題。
  - 完整讀取 `Thesis.tex` lines 321–636；確認 Results／Conclusion 空白，並定位 human-preference、underlay、L1、QC fallback 與 limitations 的 claim-contract mismatch。
  - 核對 authoritative A3 pipeline 與 P-Full schema；確認 all-QC-failed 仍進 Judge-Select、P-Full 無 underlay label，Methods 需要同步。
  - 核對 N=5 smoke、N=20 Gate A/B/C、annotation agreement 與 Relation N=100；建立第一版 claim-to-evidence verdicts。
  - 核對 General N=100 generation、formal SEGA、QC 與 matched COLE；確認 quality gap、sampling-universe limitation 與 full-Crello 尚未完成。
- API/model calls：0
- 付費成本：US$0.00

### 階段 2：現有 A3 證據盤點

- **狀態：** completed
- 已完成：
  - 核對 Relation／General 的 SEGA 與 COLE aggregate、per-sample row counts、denominators、CI、p 值與 provenance。
  - 四個 bundle 均與 A3 實驗紀錄一致，沒有發現抄錄錯誤。
  - 明確區分已完成 N=100 與未執行的 Full-Crello N=1,971。

### 階段 3：完整性與方法學審查

- **狀態：** completed
- 已完成：
  - 完成 claim-to-evidence matrix 與 methodology-focus D1/D2 gate。
  - 定位 Thesis lines 73、91、100、153–154、323、482、552、578、587、595、603 等關鍵 mismatch。
  - 確認沒有 N=100 direct tree-prediction aggregate，也沒有同協定 external matched baseline。

### 階段 4：補實驗排序

- **狀態：** completed
- 已完成：
  - 分成 P0 zero-cost、P1 claim-blocking 與 P2 claim-dependent 三級。
  - 定義 N=100 tree accuracy、人類 preference、external baseline、fresh General sample 等目的與停止條件。
  - 判定 full 1,971 不是第一 blocker，不能取代 human／independent evaluation。

### 階段 5：交付

- **狀態：** completed
- 已完成：
  - 最終判定為 Major Revision；核心 explicit Layout Tree contribution 可保留，但現稿的 parity／SOTA／repair／underlay／human-preference 主張不可保留。
  - 準備 self-contained 中文交付與精確 artifact／manuscript links。

## 測試結果

| 測試 | 預期 | 實際 | 狀態 |
|---|---|---|---|
| Active-plan isolation | 不改 Full-Crello active plan | `.active_plan` 保持 `crello-full-test` | 通過 |
| Aggregate row counts | Relation SEGA/COLE 300/397；General SEGA/COLE 100/200 | 300/397/100/200 | 通過 |
| Aggregate schema/value reload | 四份 JSON 可解析且數值與 A3 log 一致 | 全部一致 | 通過 |
| Ledger whitespace | 無 trailing whitespace、三檔皆有 EOF newline | 無錯誤 | 通過 |
| Scoped staging | 只含本審查的三份 ledger | 3 files、371 insertions | 通過 |

## 交付狀態

- `Thesis.tex`：唯讀，未修改。
- Full-Crello `.planning/.active_plan`：仍為 `crello-full-test`。
- `layout_agent/next_step.md`：未修改，避免污染另一個 active plan 的恢復入口。
- 本次 API/model calls：0；新增付費成本：US$0.00。
- 其餘工作區既有 tracked／untracked changes：全部未納入本次 staged set。

## 錯誤日誌

| 錯誤 | 嘗試次數 | 解決方案 |
|---|---:|---|
| 學術規範合併讀取遭截斷 | 1 | 改用 bounded chunks |
| Underlay search 含不存在的備選檔，exit 2 | 1 | 使用已命中的 canonical `pfull_preprocessor.py`，不重複錯誤命令 |

## 五問重啟檢查

| 問題 | 答案 |
|---|---|
| 我在哪裡？ | 階段 1：準備讀取論文並抽取 claims |
| 我要去哪裡？ | 完成 evidence mapping、方法審查與補實驗排序 |
| 目標是什麼？ | 判斷目前 A3 數據是否足以支撐論文 |
| 我學到了什麼？ | 見 `findings.md` |
| 我做了什麼？ | 恢復 active plan 並建立隔離審查 ledger |

---
*每個階段完成後更新。*
