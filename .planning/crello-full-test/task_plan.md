# 任務計畫：Crello 官方測試集分批生成與六軸評估

## 目標

在不重跑既有正式 N=100 的前提下，完成 Crello 官方 test split 全部
1,971 筆：剩餘 1,871 筆分成 18 批 100 筆及最後 71 筆；每批生成後立即
完成六軸 deterministic 評估、驗收、成本紀錄與可恢復交接。

## 目前階段

階段 2：零成本準備與完整 dry-run（進行中）

## 各階段

### 階段 1：範圍、流程與護欄凍結

- [x] 範圍固定為官方 test split N=1,971
- [x] 既有 N=100 固定為唯讀且不可重跑
- [x] 剩餘工作切成 18 批 100 筆加最後 71 筆
- [x] 每批生成後立即計算六軸
- [x] 凍結停止條件、成本護欄與恢復原則
- [x] 將完整流程寫入 `layout_agent/FULL_CRELLO_BATCH_PLAN.md`
- **狀態：** complete

### 階段 2：零成本準備與完整 dry-run

- [x] 凍結 Hugging Face dataset revision 與官方有序 ID snapshot
- [x] 唯讀核對既有 N=100 的 sample ID、revision 與 input hashes
- [x] 實作 write-once cache import，補齊 pinned revision 缺少的 74 筆 test samples
- [x] 補齊 text bitmap sidecars，不改寫原始 `meta.json`
- [ ] 對全部 1,971 筆執行 P-Full、R3 與 Analyst vision readiness
- [x] 產生全域 deterministic batch manifest 與每批 write-once 目標
- [ ] 以 dry-run 算出精確 calls、input tokens、output tokens 與美元上限
- [ ] 完成磁碟、網路、Git 可寫及無並行同批程序的檢查
- **狀態：** in_progress

### 階段 3：逐批生成與六軸評估

- [ ] 先向使用者提出精確的第一批及全域付費預算
- [ ] 取得明確付費執行授權後才啟動模型呼叫
- [ ] 依序完成 18 批 100 筆與最後 71 筆
- [ ] 每批生成停止後，以 offline/API-key-unset 模式計算六軸
- [ ] 每批通過 hash reload、成本、staging 與完整性檢查後才解鎖下一批
- [ ] 每批更新進度、`next_step.md`，做 scoped commit 並 push
- **狀態：** pending（付費部分受授權閘門阻擋）

### 階段 4：全域合併與驗證

- [ ] 合併既有 100 與新 1,871 筆成 1,971-row per-sample 結果
- [ ] 從逐筆 rows 重算六軸 aggregate，不平均批次平均值
- [ ] 保留 failed、skipped、not-applicable rows 與正確 denominator
- [ ] 驗證 dataset revision、code commit 與所有 artifact hashes
- **狀態：** pending

### 階段 5：最終交付

- [ ] 更新實驗紀錄與 `layout_agent/next_step.md`
- [ ] 確認所有輕量交付物已 scoped commit 並 push
- [ ] 回報總成本、時間、成功/失敗數、六軸結果與剩餘人工項目
- **狀態：** pending

## 付費授權閘門

目前沒有任何 full-test 付費模型呼叫授權。階段 2 可以執行，但不得呼叫
OpenAI 模型。階段 3 必須等 dry-run 產生精確的批次 ID、模型、最大 calls、
input/output tokens、單批美元與累計美元上限，並取得使用者明確同意。

## 重啟時的讀取順序

1. 讀本檔確認目前階段與未完成項目。
2. 讀同目錄的 `findings.md` 與 `progress.md`。
3. 讀 `layout_agent/FULL_CRELLO_BATCH_PLAN.md` 與 `layout_agent/next_step.md`。
4. 唯讀檢查 Git、磁碟、網路與執行中程序。
5. 只從最後一個已驗收 checkpoint 的安全續跑命令繼續。

## 關鍵問題

1. 階段 2 尚需凍結哪一個 dataset revision？
2. 既有 N=100 的精確 ID snapshot 與 input hash 是否完全屬於該 revision？
3. 完整 dry-run 算出的單批與全域精確 token ceilings 是多少？

## 已做決策

| 決策 | 理由 |
|------|------|
| 只做官方 test 1,971 筆 | 保持正式評估範圍一致，避免 train/validation 的儲存與協定擴張 |
| 每批 100，最後 71 | 控制成本與失敗半徑，讓每批都能獨立驗收及續跑 |
| 既有 100 唯讀重用 | 避免重複付費與破壞 write-once 證據 |
| 每批緊接六軸 | 趁 source artifacts 完整時立即驗證，下一批不會掩蓋問題 |
| COLE 與人工偏好不在本計畫 | COLE 另需付費授權；人工項目不能自動完成 |
| 使用 `.planning/crello-full-test/` | 讓本任務可恢復，又不與專案其他規劃衝突 |

## 遇到的錯誤

| 錯誤 | 嘗試次數 | 解決方案 |
|------|---------|---------|
| `git check-ignore -q` 同時傳入兩個 pathname，Git 拒絕執行 | 1 | 改成逐檔呼叫，不重複相同命令 |
| 聚焦驗證錯把五問都假設為 `\| 我...`，實際是 4 個「我」加 1 個「目標」 | 1 | 改成分別驗證 4 個 `\| 我` 與 1 個 `\| 目標是什麼` |
| Commit 後 Git 提示 `.git/gc.log` 記錄過多 unreachable loose objects | 1 | 不影響本任務；保留 log，不自行執行 destructive `git prune`，僅向使用者回報 |
| 一次讀取 `next_step.md` 851–1900 行造成工具輸出截斷 | 1 | 改成每次最多 250 行，逐段讀到 EOF，不重複大型輸出命令 |
| Repository inventory 未排除 `runs/full_result`，列出數萬 artifact 路徑並截斷 | 1 | 改用明確 glob exclusions，只搜尋核心 source/config/tests |
| Web open 拒絕 Hugging Face API URL，回報 `URL ... is not safe to open` | 1 | 不重試 web open；改用已通過 HTTP gate 的 unauthenticated `curl`＋`jq` 只讀 metadata |
| HF-cache findings patch 的 `next_step.md` context 錨點不精確，原子拒絕 | 1 | 先用 `rg` 定位實際文字，再以窄錨點套用；沒有部分寫入 |
| `layout_agent/output/` 根目錄仍含大量結果檔，檔名/rg 輸出截斷 | 1 | 停止目錄列舉，只讀已定位的 `step13/22/26` 腳本 |
| Implementation checkpoint patch 再次使用不精確 `next_step.md` 片語錨點 | 2 | 改用檔尾 checkpoint 24 與最後 `Next task` 的唯一相鄰區塊 |
| 新工具 hardcode 的 text-sidecar version 與 canonical P-Full 常數不一致 | 1 | 改為直接 import canonical constant，新增一致性與 tamper tests |
| 測試結果 checkpoint 再次以可變敘述句定位 `next_step.md` 而失配 | 3 | 永久改用檔尾固定 heading 區塊，並拆開 ledger/next_step patches |
| Pinned membership 多檔修正 patch 使用過時 findings 錨點而原子拒絕 | 1 | 不再跨檔整批；每檔先讀實際鄰文再套用小 patch |

## 備註

- 詳細執行契約以 `layout_agent/FULL_CRELLO_BATCH_PLAN.md` 為準。
- 每完成一個階段或每一批，都同步更新本檔狀態與 `progress.md`。
- 外部網頁/API 回傳只寫入 `findings.md`，不把外部指令放進本檔。
