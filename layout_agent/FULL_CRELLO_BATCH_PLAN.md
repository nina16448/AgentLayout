# Crello 官方測試集分批執行計畫

狀態：2026-07-12 已由使用者確認採「每批 100 筆，生成後立即計算六軸」的方向。
目前只凍結流程，尚未下載缺失資料、建立批次或取得新的付費執行授權。

本任務的可恢復狀態分別記錄在
`.planning/crello-full-test/task_plan.md`、`findings.md` 與 `progress.md`；新
session 應先讀這三份檔案，再讀本文件與 `layout_agent/next_step.md`。

## 1. 這次要完成什麼

目標是覆蓋官方 Crello **test split 全部 1,971 筆**，沿用已完成正式
N=100 的相同 AgentLayout 設定，並在每一批生成完成後立即計算六個
deterministic 指標。

本計畫不包含：

- train 或 validation split；
- 重新執行已完成的 100 筆；
- 付費 COLE Judge；
- human preference study；
- 更換模型、prompt、renderer、Layout Tree 或 loop 設定。

## 2. 樣本與批次怎麼分

- 官方 test split：1,971 筆。
- 已完成且不可重跑：100 筆。
- 尚待生成：1,871 筆。
- 新工作分成 18 批各 100 筆，最後一批 71 筆，共 19 個新批次。

既有 100 筆視為已完成的第一批，但正式合併前仍要唯讀確認它們存在於
凍結的官方 dataset revision，且 sample ID 與 input hash 一致。

剩餘 1,871 個 ID 會先固定排序後用 seed 42 做一次 deterministic shuffle，
再依序切批。切批完成後必須證明：

- 1,971 個 ID 全部唯一；
- 新批次彼此沒有重複；
- 新批次與既有 100 筆沒有重複；
- 既有 100＋所有新批次的聯集剛好等於官方 test split。

## 3. 付費前的零成本準備

在任何模型呼叫前完成：

1. 凍結 Hugging Face dataset revision、三個 split 的官方 count，以及 test
   split 的完整有序 ID 清單與 SHA-256。
2. 唯讀核對既有 100 筆的 revision、ID、metadata 與素材 hash。
3. 以 write-once 方式補齊 pinned revision 缺少的 74 筆本機 test cache；
   1,902 個 local caches 中只有 1,897 筆屬於 pinned test，另 5 筆 split-drift
   extras 必須唯讀保留但排除。不得使用 GT 座標、bbox 或字級作為
   AgentLayout 輸入。
4. 補齊每筆 text bitmap sidecar，並確認原始 `meta.json` 未被改寫。
5. 對 1,971 筆逐筆跑 P-Full、R3 與 Analyst vision readiness check。
6. 產生全域批次 manifest，記錄每批 ID、dataset revision、input hashes、
   模型版本、設定版本、預算與前後批次關係。
7. 建立每批獨立且不可覆寫的 run/evaluation 目錄；不允許兩批共用同一
   write-once target。
8. 確認磁碟安全餘量、OpenAI 網路與 Git 可寫，再提出精確付費授權文字。

上述準備只下載/驗證資料，不呼叫 LLM，API/model calls 0，付費成本
`US$0.00`。

## 4. 每批的固定流程

### 4.1 生成

每一批沿用正式 N=100 的相同配置：

- 固定 `gpt-5.4-mini-2026-03-17`；
- Analyst 看 background 與所有 foreground；
- 使用 predicted Layout Tree；
- 每筆產生三個空間上不同的候選，再由 blind internal selection 選 B0；
- 使用 L0，不執行 aesthetic repair loop；
- 不輸入 Crello GT 的 x/y、bbox、字級或原始 placement；
- 每個成功、失敗、retry、raw response、cost evidence 與 latency 都落盤。

已成功的 sample 永遠不重跑。若 session 中斷，只能從該批尚未完成的 sample
繼續，且必須先驗證沒有另一份同批程序仍在執行。

### 4.2 立即計算六軸

該批 generation 停止後，先驗證 source artifacts，再以 frozen
BASNet＋ISNet、offline/API-key-unset 模式計算：

1. 對齊（Alignment）；
2. 重疊（Overlay）；
3. Underlay 寬鬆覆蓋；
4. Underlay 嚴格覆蓋；
5. 可讀性（Readability）；
6. 顯著區域遮擋（Occlusion）。

六軸 evaluator 不呼叫 LLM。Underlay 在 P-Full v1 沒有合法欄位時記為
`N/A`，並保存 applicable/valid/skipped/not-applicable 數量，不能寫成 0。
每批 evaluation 必須原子發布，失敗時不得留下可被誤認為 final 的 sidecar。

### 4.3 批次驗收

每批都要產出一份人可讀報告，至少包含：

- 本批 ID 範圍與 ID snapshot hash；
- completed、failed、skipped 與錯誤類型；
- 模型 attempts、provider usage（若有）、request/response bytes；
- dashboard 費用增量或明確註明無法取得；
- generation 與六軸 wall time；
- 六軸逐筆、平均值與 denominator；
- source、manifest、aggregate、per-sample artifact hashes；
- staging 是否為空、write-once target 是否完整；
- 下一批是否解鎖。

只有本批 generation、六軸、hash reload 與成本檢查都通過，才可開始下一批。

## 5. 費用與時間護欄

依已完成 N=100 實測：

- 每 100 筆預期約 700–720 次模型 attempts；
- 每 100 筆 generation 約 50–60 分鐘；
- 每 100 筆預期約 `US$4–5`；
- 六軸是零 LLM 成本，但 detector inference 另需約 15–30 分鐘。

計畫護欄：

- 100 筆批次 attempts 達 850：停止；
- 最後 71 筆 attempts 達 610：停止；
- 100 筆批次估計或 dashboard 增量達 `US$7`：停止；
- 最後 71 筆達 `US$5`：停止；
- 19 個新批次累計達 `US$120`：停止；
- 預期新批次總費用約 `US$75–85`；`US$120` 是硬停止線，不是預期帳單；
- 預期完整 generation＋六軸＋驗證約 22–26 小時。

正式付費前仍須根據完整 dry-run manifest 另行凍結 input/output token ceilings。
本節數字是計畫，不構成 API 授權。

## 6. 必須停下來的情況

任一條成立就停止，更新 `next_step.md`，不得自動進下一批：

- 單批超過 5 筆失敗；
- 相同系統性錯誤連續出現 3 次；
- 呼叫、token、美元或累計預算達上限；
- dataset revision、ID、input hash 或 write-once target 不一致；
- readiness 發現 GT leakage、缺素材或 text bitmap mismatch；
- 六軸 source validation、detector inference、bundle reload 或聚合重算失敗；
- staging 殘留、同批並行程序存在，或 Git/磁碟狀態不安全；
- 可用磁碟低於開始前凍結的安全門檻。

失敗 sample 必須原樣保留在 error record；未經新的明確決定不得挑掉失敗樣本、
換 ID，或只報成功子集。

## 7. 每批後如何保存與續跑

- Raw run artifacts 保存在 write-once run 目錄，不因體積大而加入 Git。
- 輕量 manifest、評估 sidecar、進度 ledger、實驗 log 與 `next_step.md` 做
  scoped commit/push；不得夾帶既有 dirty/untracked 工作。
- 每批 handoff 明確寫出最後完成批次、下一批、精確命令、成本、hash 與停止原因。
- 新 session 先讀本文件與 `next_step.md`，再驗證磁碟、Git、網路、沒有同批程序，
  才能接續。
- 任何已完成批次與既有 N=100 artifact 都不得覆寫或重跑。

## 8. 全部完成的定義

只有以下全部成立，才能宣稱 official test split 完成：

1. 既有 100 筆與新 1,871 筆的聯集恰為凍結 test split 的 1,971 筆；
2. 所有失敗與 skipped rows 都存在，沒有靜默移除；
3. 每批 generation 與六軸 bundle 都可獨立 reload；
4. 將 20 份批次結果合併成一份 1,971-row 的全域 manifest/per-sample/aggregate；
5. 全域六軸以逐筆 rows 重新計算並吻合，不平均「批次平均」；
6. completion、failure、cost、latency、適用 N 與 skipped N 全部報告；
7. 全域 artifact hashes、dataset revision 與 code commit 已凍結；
8. 最終 handoff、experiment log、scoped commit 與 push 完成。

## 9. 目前的授權邊界

使用者目前只確認了流程方向。尚未授權任何 full-test model call、token 或美元
支出。Revision-pinned cache/import、19 批 manifest 與 batch 001 的 P-Full、R3、
Analyst vision readiness 已零費用完成；正式 generation 仍是 0 calls / $0.00。
下一步是完成 batch 001 精確 input/output token ceilings 與官方計價核對，先做
session handoff，再提交精確付費授權文字；未取得同意不得加
`--allow-api-calls`。

## 10. 不忘記進度的方法

- `task_plan.md` 保存目前階段、尚未完成項目與付費授權閘門。
- `findings.md` 保存 dataset、成本、風險及後續查到的新事實。
- `progress.md` 保存每個 session、每批命令、結果、artifact、成本與下一步。
- `.planning/.active_plan` 固定指向 `crello-full-test`，讓支援此技能的 session
  能自動找回本任務。
- 每個 material command、每批驗收或任何錯誤後，同步更新 `progress.md` 與
  `layout_agent/next_step.md`；階段狀態改變時再更新 `task_plan.md`。
