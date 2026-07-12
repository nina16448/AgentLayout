# A3 外部 Baseline 規劃進度

## 2026-07-12

- 啟動 experiment-agent plan mode 與 planning-with-files-zht。
- 盤點現有 A3 日誌、sample IDs、評估 bundle 與工作樹狀態。
- 建立獨立 `.planning/a3-external-baseline/`，不切換或覆寫其他 active plan。
- 目前只做本機規劃與唯讀研究；0 API calls、$0.00，未下載模型。
- 核實官方 repo/model/base revisions、manual gate、4-bit loader 與 inference 限制。
- 核實官方 element renderer 使用 GT width/height，因此明列禁止沿用。
- 實測 Relation N=100 placeable counts：min 6、mean 13.43、max 37、5 筆 >25。
- 完成 `experiment_plan.md`：adapter files、獨立 run schema、測試、N=1/N=5/N=100
  gates、paired statistics、timeout 與 deadline stop-loss。
- `git diff --check` 通過，四份規劃檔皆存在且非空。
- `layout_agent/next_step.md` 已有非本任務的未提交修改，為避免混入他人工作，
  本次不修改；恢復入口為本目錄的 `task_plan.md` 與 `experiment_plan.md`。
- 第一次 staged diff check 發現 `experiment_plan.md` 檔尾多一空白行；已移除，
  未改變實驗內容，等待重新驗證。
