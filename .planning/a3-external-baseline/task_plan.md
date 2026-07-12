# A3 外部 Baseline 實驗計畫

## 目標

在不使用 Crello designer GT placement 的前提下，將 Microsoft Elem2Design
接到 A3 的 P-Full 輸入與共用 renderer/evaluator，先完成 Relation N=5 smoke，
通過後再執行凍結的 Relation N=100 matched external baseline。

## 狀態

| 階段 | 狀態 | 驗收條件 |
|---|---|---|
| 1. 盤點 A3 與 Elem2Design contracts | complete | 固定輸入、輸出、硬體與授權限制 |
| 2. 定義公平轉接與防洩漏規則 | complete | GT leakage audit 可自動失敗 |
| 3. 定義 N=5 smoke 與 N=100 正式流程 | complete | 命令、artifact、timeout、停損條件完整 |
| 4. 定義共用評估與統計比較 | complete | primary/secondary metrics、paired analysis 完整 |
| 5. 文件檢查與交付 | complete | 四份規劃檔一致且可交給 Claude Code 執行 |

## 固定決策

- 外部 baseline：Microsoft Elem2Design（LaDeCo）；SEGA 不作 matched baseline。
- 主 track：Crello-Relation frozen N=100；先以其前 5 筆建立 smoke snapshot。
- 所有 foreground assets 都必須保留，超過模型原生 element 上限時不得靜默丟棄。
- 不得使用 GT x/y、bbox、width/height、layer order 或合成後 designer layout。
- 只有 smoke 全部通過才可啟動 N=100；任何付費 API 都不在本計畫內。

## 遇到的錯誤

| 錯誤 | 嘗試次數 | 處理方式 |
|---|---:|---|
| `git diff --cached --check` 發現 `experiment_plan.md` 檔尾多一空白行 | 1 | 移除檔尾空白行後重新檢查 |
