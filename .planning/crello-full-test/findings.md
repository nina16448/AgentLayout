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
- 本機有 1,902 筆有效 cache；其中 1,897 筆屬於 pinned official test，另有
  5 筆 split-drift extras，因此 pinned test 真正缺 74 筆。
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

- 已凍結 dataset revision 與完整 test ID hash。
- 尚未補齊 pinned revision 的 74 筆 cache 與 1,706 份既有 cache text sidecar。
- 尚未建立全域 batch manifest 或任何新 run/evaluation 目錄。
- 尚未執行全量 readiness 或精確 token dry-run。
- 尚未取得任何 full-test 付費執行授權。

## 2026-07-12 階段 2 恢復發現

- Codex `MEMORY.md` 對 Crello、AgentLayout、A3 SEGA 與 batch manifest 沒有
  額外命中；本階段以 repository 的 plan、handoff 與 artifacts 為唯一準據。
- `FULL_CRELLO_BATCH_PLAN.md` 與三份 scoped ledger 的範圍、1,971/1,871
  算術、19 個新批次及付費授權邊界一致。
- `next_step.md` 共 1,828 行；大型合併讀取被工具截斷，因此需改用最多
  250 行的分段讀取後，才可宣稱完整恢復。
- `session-catchup.py` exit 0 且無輸出，沒有偵測到需補寫的上一 session
  內容；scoped ledger 已由本 session 明確讀回。
- `next_step.md` 前 1,000 行已用兩個 250-line `sed` 區段一組讀完，沒有
  再次截斷。
- 既有 100 筆的 canonical ID 檔為
  `layout_agent/sample_ids/a3_general_n100.json`，selected-ID SHA-256 為
  `0e5401fb45cb83c573c82be458508e6ace003482b027b667556dfd876aed052c`；
  新 manifest 必須唯讀重用這 100 個 ID，不能重新抽樣。
- 既有正式 run `layout_agent/runs/a3/a3-general-n100-t2-l0-01`、SEGA
  `a3-general-n100-sega-v1` 與 COLE `a3-general-n100-cole-v1` 都是
  write-once，舊付費授權已消耗，不可重跑或覆寫。
- 六軸 evaluator 已知需使用 meta environment 的 direct interpreter，並設
  `TMPDIR=/tmp`、`NUMBA_CACHE_DIR=/tmp/a3-numba-cache`；`conda run` 曾因唯讀
  wrapper/cache 失敗，不能直接沿用為正式長批次啟動方式。
- `next_step.md` 1–1,828 行已全部以 bounded chunks 讀完；目前有效的最後
  checkpoint 是 24，舊 checkpoint 中所有付費 command 都已消耗或被取代。
- 先前量測的 1,902 cache 與約 98 GB 可用空間可能隨工作區變動，正式實作前
  必須即時重測；不能把 checkpoint 21 的數字當成本 session 現況。
- Credential rotation 已由使用者確認完成，但任何 connectivity probe 仍只用
  無 Authorization 的 `/v1/models` HTTP 狀態，不載入或輸出 credential。

## 2026-07-12 15:34 CST 即時 preflight

- Branch/local/upstream/remote 均為 `feat/step76-89-sega-pipeline` /
  `b1338441a224fa3802889a7ca6b24ca4b836c145`。
- `.git` 與 index 可寫、`index.lock` 不存在、staged paths 為 0。
- OpenAI `/v1/models` 在無 Authorization header 下 `curl rc=0`、HTTP 401；
  只證明 DNS/TLS/routing，沒有模型呼叫、tokens 或費用。
- Hugging Face dataset server `curl rc=0`、HTTP 200。
- 本機 `crello_*` cache 仍為 1,902；目前沒有 A3 generation/evaluator/judge
  Python 程序。
- Workspace 可用 `101,291,616 KiB`（約 96.6 GiB），但 filesystem 已使用
  98%；importer 在 pinned membership 與 transfer ceiling 凍結前不得啟動。
- General SEGA 三檔與 COLE 兩檔的 checkpoint-15 SHA-256 全部 `OK`。

## 初步 source/cache inventory

- 適用的 repository instruction 只有 root `AGENTS.md`，沒有 nested override。
- 現有 cache 目錄名為 `crello_<id>`；抽查 `meta.json` 頂層 keys 為
  `canvas_height`、`canvas_width`、`elements`、`id`、`n_elements`、`title`，
  並有 `ground_truth_preview.jpg` 與逐元素 assets。
- `layout_agent/run_a3.py` 的 `snapshot-text-bitmaps` 使用
  `load_dataset(..., streaming=True)`，但現有用途只替已存在 cache 補 text
  sidecars；它不能建立缺少的 `meta.json` cache。
- 另有舊腳本 `layout_agent/output2/step80_snapshot_text_assets.py` 可供理解
  streaming schema，但不能直接假設符合新的 write-once importer 契約。
- 第一次 `rg --files` 未排除大型 artifact 目錄而截斷；未完整顯示的路徑不作
  決策依據，後續改成 core-only inventory。

## Core source 邊界

- `select_a3_general.py` 已提供 canonical JSON、SHA-256、deterministic
  sorted-pool seed-42 shuffle，以及 O_EXCL write-once-or-verify 原語，可重用其
  行為但不能重跑 N=100 selection。
- 舊 `output2/step80_snapshot_text_assets.py` 會把 `asset_ref` 寫回
  `meta.json`，違反本計畫的 immutable-meta 契約，明確禁止重用。
- 現行 `run_a3.py snapshot-text-bitmaps` 使用 raw-size RGBA images 與
  `write_json_once(a3_text_bitmaps.json)`，不修改 `meta.json`；這是 text
  sidecar 的 canonical 實作。
- `run_a3.py` 的 `plan` 不建立 run，`init` 建立 immutable skeleton；
  `prepare-pfull`、`normalize-r3`、`prepare-analyst-vision` 都是零 API，但會
  在已初始化 run 內寫入 write-once summaries/artifacts。
- 正式 N=100 config 的 dataset label 是
  `crello-general-random-n100-v1`；full-test batches 需要新的 frozen config
  snapshot，不能假裝仍是 N=100 split。
- 新 full-test 準備工具應獨立於付費 runner，預設 dry-run，只有明確
  materialize 模式才建立缺失 cache；CLI 不提供 `--allow-api-calls`。

## 官方 Hugging Face revision 與 schema（2026-07-12 查詢）

- 官方 repository API 回報 `cyberagent/crello` SHA
  `7997e2f434ee4aa73cf4cdf22c5954cb175872e1`，last modified
  `2026-02-27T02:45:00Z`，public、ungated、enabled。
- Repo 在該狀態有四個 raw test parquet：
  `data/test-00000-of-00004.parquet` 至 `test-00003-of-00004.parquet`。
- Dataset-server info：test 1,971 examples、1,634,779,960 uncompressed bytes；
  train/validation/test 全部合計 20,099,416,197 bytes，download size
  18,207,073,052 bytes。
- Test 的 dataset-server converted parquet 共四檔，大小分別 384,078,313、
  403,233,847、382,693,771、381,050,924 bytes，合計 1,551,056,855 bytes
  （約 1.44 GiB）。
- Dataset schema 含 `id`、`length`、`canvas_width/height`、`title`、
  `preview`，以及等長的 `type/left/top/width/height/angle/opacity/color/image`
  與 text/font 欄位，足以重建現有 cache。
- Converted parquet URL 指向 `refs/convert/parquet`，不是上述 source repo SHA；
  正式 identity 必須把 source SHA 傳入 `load_dataset(revision=...)` 並保存
  ordered-ID snapshot/hash，不能只記 converted URL。

## 本機 Hugging Face cache 與磁碟決策

- `~/.cache/huggingface/hub/datasets--cyberagent--crello` 只有約 60 KiB 的
  README/ref metadata；沒有 Crello parquet/Arrow。
- `~/.cache/huggingface/datasets` 的 6.1 GiB 全屬
  `creative-graphic-design/pku-poster_layout`，沒有 Crello download metadata。
- 因此第一次 pinned test scan 最壞需傳輸四個 test shards，約 1.44 GiB；
  不能把先前 streaming scan 當成可重用的本機 cache。
- 凍結 materialization 磁碟 hard stop：開始前 available space 必須至少
  80 GiB。目前約 96.6 GiB，約 16.6 GiB 緩衝；低於門檻立即停止且不建立
  staging/final cache。

## Pinned ordered-ID snapshot 完成

- Artifact：`layout_agent/sample_ids/a3_crello_test_n1971_v1/`，包含
  `ordered_ids.json` 與 `dataset_provenance.json`。
- Revision `7997e2f434ee4aa73cf4cdf22c5954cb175872e1`，exact count 1,971，
  unique count 1,971。
- Ordered-ID file SHA-256：
  `c3578fa5c8e0c181887a70f9e78b850b7d6adc52d3f367fe191b5f5292e0974c`。
- Canonical ordered-ID SHA-256：
  `b082ec96e38798de500c8d1c82961bf20912634142218996446f2284c8b2d815`。
- `select_columns(["id"])` 約 3.5 分鐘完成；隔離 temp datasets cache 最後
  只有 60 bytes lock，沒有保存 parquet/image。
- Cache count 前後均 1,902；全體 `meta.json` aggregate SHA-256 前後均
  `8dcfcdd882a3e598a687d4b11cae189434b1b54b7c957b2427d5136f6fece896`。

## Pinned membership/cache inventory

- 1,902 local valid caches 與 1,971 pinned IDs 的交集是 1,897；pinned missing
  是 74，不是先前 count-only 推論的 69。
- Missing-official ordered IDs SHA-256：
  `7fb2a1ce97f2a06082ba5816b82b182b4e478c0c563ccefcfbc42f030d9c5d60`。
- 五個 local extras（唯讀保留、不得刪除、不得加入 official manifest）：
  `5954bda995a7a863ddce14a1`、`5c6c0cba85ea3c16f964a15d`、
  `5d972ca9abc8ea6d1c54e002`、`5efdd2dd499b85dcc75ba0bc`、
  `5f885a9ba637ee11e3498683`；集合 SHA-256
  `34cbc42faa567cb4aee99ef5970c24ccd3a9a9cd848130eb1ca36810451b1b71`。

## Materialization result and independent verification

- 固定 revision 的 materialization 掃描 1,971 rows，新增 74 個
  write-once cache trees 與 1,706 個 canonical text sidecars；remaining 0，
  OpenAI/model calls 0、cost US$0.00。
- 獨立 inventory：official valid 1,971、missing cache 0、missing sidecar 0；
  本地總 cache 1,976（官方 1,971＋保留/excluded extras 5）。
- 74 個新 provenance trees 全部逐檔 size/hash 驗證通過；full-official meta
  snapshot SHA-256 是
  `84ad5f01ad825b7fa2c8f9a1c0dc545737d998e6e0eb46e0c71bb25addffbdf3`。
- 原有 1,897 個 official cache 的 meta snapshot 維持
  `ccc538537b86a1504f1769a7db15f2a7d5c5b8866d96499ab71569ae4af33364`；
  五個 extra 的個別 `meta.json` hash 亦全部不變。無 staging；驗證後可用
  空間 103,610,744,832 bytes（約 96.5 GiB），仍高於 80 GiB hard stop。

## Deterministic batch bundle

- Bundle：`layout_agent/sample_ids/a3_crello_test_batches_v1/`；包含
  `manifest.json`、`run_config.json` 與 19 個 write-once batch ID files。
- Strict build＋reload verify 皆 exit 0：official 1,971、reused 100、new
  1,871、new batches 19；前 18 批各 100，最後一批 71。
- Manifest SHA-256：
  `3b334f24bba80e7d76b7699e6df6409d9629038c7149e4df54d79587e3503b13`；
  dataset/order hashes 與 pinned snapshot 相同，shuffle algorithm 是
  sorted remaining IDs＋seed 42＋chunks 100。
- `paid_generation_authorized=false`，bundle 自身 API/model calls 0、cost
  US$0.00；19 個 run/evaluation targets 在 publication 時均不存在。

## Readiness disk estimate

- 實際既有完成 run 是 `layout_agent/runs/a3/a3-general-n100-t2-l0-01/`；
  舊描述名 `a3-general-n100-cole-v1` 不存在，不得用作 resume path。
- 完成 N=100 run 的 `du` 是 201,976 KiB（samples 201,864 KiB）；以整個
  run 最保守線性外推 1,871 筆約 3.7 GiB。
- 估算前可用 101,182,164 KiB（約 96.5 GiB）；完成全部 readiness 後仍
  約 92.8 GiB，高於 80 GiB hard stop。仍須在每批開始前重查磁碟。
- 1,897 個 overlapping caches 中 1,706 個缺 canonical text sidecar；集合
  SHA-256 `5e10bc67d2d6a89fcf50916759ec9f711163a0668048dca47404ac3d3a57c611`。
- 新 74 筆會同時建立 sidecar，因此 full materialization target union 為
  1,780 個 dataset rows（74 missing cache＋1,706 existing sidecar）。
- Existing N=100 的 100 IDs 全部仍在 pinned official test，file SHA-256
  `0e5401fb45cb83c573c82be458508e6ace003482b027b667556dfd876aed052c`；
  生成批次算術 1,871 = 18×100＋71 不變。
- Pinned-overlap meta snapshot SHA-256：
  `ccc538537b86a1504f1769a7db15f2a7d5c5b8866d96499ab71569ae4af33364`。

## Cache materializer 線索

- Git 歷史沒有保存最早的 bulk cache 建立器；目前 repository 只保留後續
  streaming/sampling scripts。
- 現有元素 metadata 除 raw `idx/type_code/left/top/width/height/content` 外，
  還有 `classifier_label`、`classifier_signals`、`kind`、`asset_ref`。
- 抽查分類例：full-canvas type 3 → `background_candidate`；photo type 2 →
  `image`；低色數 shape type 0 → `underlay`。這些欄位不可省略。
- 已定位 `layout_agent/output/step13_sota_winrate.py`、
  `step22_sample_extra80.py`、`step26_pick_underlay_smoke.py` 的
  `save_sample`/streaming 路徑；下一步只讀這三檔。
- `output/` 根目錄本身也有大量結果檔，第二次 broad listing 截斷；後續禁止
  列舉該目錄，僅使用精確檔名。

## Canonical row→cache 映射

- `layout_agent/output/run_iou_eval.py::save_sample` 是既有 cache producer，
  但它 `mkdir(exist_ok=True)` 後直接覆寫 assets、preview、`meta.json`；正式
  full-test importer 禁止直接呼叫。
- Producer 對 type 1 保存 `content` 並設 `kind=text`；對 type 0/2/3/4 且
  有 image 的元素呼叫 `step27_audit_underlay_assets._classify_underlay`。
- 分類決策樹：area ratio ≥0.95 → `full_canvas`；unique colors >256 →
  `photo`；≤16 → `shape`；≤64 且 alpha std>0.05 → `shape`；≤64 其餘 →
  `ambiguous`；其他 → `photo`。
- `shape` 存 `asset_NN_underlay.png`/`kind=underlay`；`full_canvas` 存
  `asset_NN_background.png`/`kind=background_candidate`；photo/ambiguous 存
  `asset_NN_image.png`/`kind=image`。Preview 存 `ground_truth_preview.jpg`。
- 為保持與 1,897 筆 pinned-overlap cache 相容，新 importer 可唯讀重用同一 pure private
  classifier，但必須自己實作 sibling staging、完整驗證與 atomic rename；
  final 已存在時只允許 verify，不覆寫。
- 現有 classifier 沒有直接單元測試；新 importer tests 必須覆蓋所有 label、
  filename/kind mapping、meta immutability、staging cleanup 與 collision refusal。

## 2026-07-12 batch-001 官方 OpenAI 計價／vision 規則

- 官方模型頁確認 frozen snapshot 是 `gpt-5.4-mini-2026-03-17`，屬 reasoning
  model；context window 400,000、max input 272,000、max output 128,000 tokens，
  支援 Chat Completions 與 text/image input。
- 官方 Standard token pricing（每 1M tokens）：input US$0.75、cached input
  US$0.075、output US$4.50。Batch/Flex/Priority 是不同 tier，本 runner 沒有
  指定它們，因此授權核算只採 Standard，且不預先假設 prompt-cache 折扣。
- Images & vision 文件說明：省略 `detail` 會使用 `auto`；`gpt-5.4-mini` 支援
  low/high/auto。High 以 32×32 patches 計算，受 1,536-patch 與 2048px 最長
  邊限制，最後乘以 1.62 得 billed image-token units。官方公式為
  `ceil(width/32) × ceil(height/32)`，超過 budget 時按文件公式等比例縮小。
- 現有 MetaGPT message 沒有送 `detail`，所以實際是 `auto`；官方文件沒有為
  每張圖片預先承諾 auto 的選擇，不能把逐圖 high 計算冒充目前 request 的
  精確值。正式 run 必須顯式送 high，或把 runtime provider usage 作為唯一
  結算值並以保守 high 上限做 reservation。
- 官方來源：
  - `https://developers.openai.com/api/docs/models/gpt-5.4-mini`
  - `https://developers.openai.com/api/docs/pricing`
  - `https://developers.openai.com/api/docs/guides/images-vision`

## Batch 001 離線 accounting 證據與候選 ceilings

- 100 份已備妥 Analyst prompts 合計 559,587 UTF-8 bytes、147,826 個
  `o200k_base` proxy tokens；每 sample 有 9–10 個 nominal image inputs。
- 依官方 `gpt-5.4-mini` high-detail patch 公式，batch 001 nominal image input
  合計 774,360 billed token units；若七個 stages 全部各走三次 schema attempts，
  image-only 上界為 2,323,080。最大已備妥 image 960×1100，最大 raw patch
  count 1,050，均低於 1,536-patch budget。
- 完成 General N=100 的 700 base prompts 合計 1,869,562 個 o200k proxy
  tokens；其 Analyst prompt 合計 146,899，與 batch 001 的 147,826 高度接近。
- General N=100 可見 attempts 為 Planner 106、Director 100、Mapper 308、
  Judge 100，再加 100 份 Analyst output proxy，共 714。output proxy 合計
  445,497；stage maxima 為 Analyst 2,575、Planner 2,785、Director 1,156、
  Mapper 1,486、Judge 44。
- 候選 per-call `max_completion_tokens`：Analyst 4,096、Planner 4,096、
  Director 2,048、Mapper 2,048、Judge 512。它們對既有可見 maxima 留有
  headroom，且官方說該參數同時限制 visible、non-visible 與 reasoning tokens。
- 候選 batch hard ceilings：850 actual HTTP calls、4,500,000 input tokens、
  800,000 output tokens、US$7.00。以不採 cached discount 的 Standard rates，
  token ceilings 的代數成本是 US$6.975，低於美元 cap。這些數字在 runner
  完成 lock/reserve/settle/disable-hidden-retry enforcement 前不構成授權提案。

## 資源

- 詳細流程：`layout_agent/FULL_CRELLO_BATCH_PLAN.md`
- 持久交接：`layout_agent/next_step.md`
- 本任務總計畫：`.planning/crello-full-test/task_plan.md`
- 本任務進度：`.planning/crello-full-test/progress.md`

## 視覺／瀏覽器發現

- 本次採用技能時沒有新的視覺或瀏覽器內容。

---
*每取得新外部資料或完成兩次瀏覽／搜尋後更新此檔。*
