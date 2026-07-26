# 目前狀態（2026-07-25 更新）

上一份 handoff `CODEX_HANDOFF.md` 的任務（Elem2Design external baseline）
已全部完成並結案，該檔已於 repo 清理時刪除；如需回看內容：

```bash
git show 60b893792^:layout_agent/CODEX_HANDOFF.md
```

## 近期完成

- Full-Crello batch-001：98/100 完成（+2 skip），結果在 `runs/a3/a3-crello-test-batch-001-n100-t2-l0-v1/`
- Elem2Design baseline：見 `ELEM2DESIGN_BASELINE_RESULTS.md`、`evaluations/a3-external/`（勿重跑）
- 根目錄 `README.md` 已改寫為 AgentLayout 專案總覽（commit `65556ab1d`）
- 2026-07-25 repo 清理：刪除舊 pipeline 產物約 9GB（full_result per-sample trace、
  舊 demo、pku_run、sega_pre、step 中間 archive、渲染 log、過時文件；
  tracked 檔刪除見 commit `60b893792`）。彙整數據保留於
  `full_result/_aggregate/` 與 `full_result/INDEX.md`。

## 下一步候選

- 下一批 Full-Crello batch 需使用者新付費授權（resume 機制已就緒：
  `--resume-ledger` / `--skip-sample`）
- 論文寫作素材：`output2/step98_a3_walkthrough/`（兩份 A3 walkthrough）、
  `result.md`、`CURRENT_EXPERIMENT_RESULTS.md`
