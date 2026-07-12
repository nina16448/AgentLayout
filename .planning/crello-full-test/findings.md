# 發現與決策：Crello 官方測試集

## 需求

- 使用者選擇跑完整官方 Crello test split，而不是 train/validation。
- 以每批 100 筆執行；最後不足 100 的 71 筆獨立成批。
- 每批生成後立即計算六軸。
- 已完成項目不得重跑，需人工完成的項目不包含在自動流程。
- 任何付費 API 呼叫前，必須另提精確 calls、token 與美元預算並取得授權。

## 已確認資料

- 官方 `cyberagent/crello` split counts：train 19,479、validation 1,852、
  test 1,971，總計 23,302。
- 本機已有 1,902 筆 test cache，官方 test 尚缺 69 筆。
- 正式 General N=100 已完成且視為 write-once；剩餘 1,871 筆。
- 新工作為 18 批各 100 筆及最後一批 71 筆，共 19 個新批次。
- 最終聚合包含既有 N=100，共 20 個批次、1,971 rows。
- 六軸 evaluator 使用 frozen BASNet＋ISNet，offline/API-key-unset，不呼叫 LLM。
- 六軸為 Alignment、Overlay、Underlay loose、Underlay strict、Readability、
  Occlusion；Underlay 不適用時保留 `N/A` 與 denominator，不可改寫成 0。

## 成本與時間發現

- 既有 N=100 實測約 714 次模型 attempts、3,143 秒，文字 token 約
  2.13M input＋0.46M output，另有 image input。
- 依實測尺度，新 1,871 筆 generation 預期約 US$75–85；US$120 是全域
  硬停止線，不是預期帳單。
- 100 筆單批暫定在 850 attempts 或 US$7 停止；最後 71 筆在 610 attempts
  或 US$5 停止。這些仍不是付費執行授權。
- generation＋六軸＋驗證的總時間初估 22–26 小時。
- 使用者回報 provider dashboard 約 US$87.00；此為帳戶層級觀察，不能直接
  歸因於本計畫。本次規劃沒有新增 API 花費。

## 技術決策

| 決策 | 理由 |
|------|------|
| 先完成 1,971-ID dry-run 才提付費授權 | 先取得真實 prompt/image 尺度與精確 token ceiling |
| 剩餘 ID 固定排序後 seed 42 shuffle | 批次可重現，且不依執行時狀態改變 |
| 每批使用獨立 write-once run/evaluation 目錄 | 防止覆寫、混批與中斷後誤判完成 |
| 每批完整驗收後才解鎖下一批 | 將成本與錯誤限制在單批內 |
| 全域 aggregate 從 per-sample rows 重算 | 避免不同批次 denominator 導致平均值錯誤 |
| `.planning/crello-full-test/` 保存任務狀態 | 配合 planning-with-files-zht，支援重啟與長任務追蹤 |

## 風險與停止條件

- Dataset revision、sample ID 或 input hash 不一致時停止。
- 同一系統錯誤連續 3 次、單批失敗超過 5 筆或任一預算上限達標時停止。
- Readiness 發現 GT leakage、缺素材、bitmap mismatch 或 detector 驗證失敗時停止。
- Staging 非空、同批有並行程序、Git 不可寫或磁碟低於安全門檻時停止。
- 已成功 sample 不重跑；failed/skipped rows 不得靜默刪除或換樣本。

## 目前未完成

- 尚未凍結 dataset revision 與完整 test ID hash。
- 尚未補齊 69 筆本機 cache。
- 尚未建立全域 batch manifest 或任何新 run/evaluation 目錄。
- 尚未執行全量 readiness 或精確 token dry-run。
- 尚未取得任何 full-test 付費執行授權。

## 資源

- 詳細流程：`layout_agent/FULL_CRELLO_BATCH_PLAN.md`
- 持久交接：`layout_agent/next_step.md`
- 本任務總計畫：`.planning/crello-full-test/task_plan.md`
- 本任務進度：`.planning/crello-full-test/progress.md`

## 視覺／瀏覽器發現

- 本次採用技能時沒有新的視覺或瀏覽器內容。

---
*每取得新外部資料或完成兩次瀏覽／搜尋後更新此檔。*
