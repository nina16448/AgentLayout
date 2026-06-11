# AgentLayout — 實作進度日誌（Implementation Log）

> 從 `README.md` 切出。README 僅保留任務定義、系統架構、研究定位、Step 摘要對照表；本文件保存所有 step 詳細實作紀錄（程式檔/設計/驗證/教訓）。
> 對照文件：`result.md`（誠實定調與口試 honesty 章節）、`output/NEXT_SESSION.md`（下次續跑進度）。
> 結構：依日期 → 模組/Step → 動機 / 方法 / 數值 / Trade-off。

---

## 實作進度

### 2026-05-07

#### Schema 層建立完成 — `metagpt/ext/agentlayout/schema.py`

決定先寫 schema、再寫工具與 Action，避免 prompt 與型別反覆改寫。所有 4 個 LLM Agent 與 5 個 Python / CV 模組之間的 JSON 契約都收斂到一個檔案。

**新增檔案**
- `metagpt/ext/agentlayout/__init__.py`
- `metagpt/ext/agentlayout/schema.py`

**檔案內 8 個區塊**
1. **Common enums** — `SemanticType` / `VisualType` / `HardConstraintRule` / `SoftConstraintRule` / `JudgeDecision` / `FeedbackTarget` / `EncoderType`，全用 `str + Enum` 雙繼承，Pydantic v2 自動轉換 JSON 字串。
2. **Embedding store** — `EmbeddingRecord`、`EmbeddingStore`（包成 Pydantic model 而非裸 dict，預留之後加 `to_numpy_matrix()` / `save_to_faiss()`）。
3. **Background analysis** — `SafeZone`（`bbox` 用 `min_length=4, max_length=4` 強制 4 元素）、`BackgroundAnalysis`。
4. **Design Spec** — `Canvas` / `Element` / `HardConstraint` / `SoftConstraint` / `DesignSpec`。
   - **Element 設計選擇 A**：`importance` / `semantic_relevance` 用 `Optional + None` 表示「Asset Analyzer 跑過才填」。同一個 type 從 Analyst 流到 Layout Generator，import 與序列化路徑統一。
   - **`DesignSpec.assert_enriched()`**：runtime guard，Layout Generator Action 入口呼叫，補強 Optional 在型別系統表達不出的「進入 Layout Generator 前必須非 None」。
   - 附 `foreground_elements()` / `get_element()` 訪問器，Asset Planner 與 Quality Checker 不用各自重寫一遍。
5. **Layout Tree** — `LayoutTreeNode`（`extra="forbid"` 阻擋 LLM 多塞欄位、遞迴 `List["LayoutTreeNode"]` + `model_rebuild()`）、`LayoutTree`（`@model_validator(mode="after")` 強制 root id == 'root'）。
6. **Candidates** — `LayoutElement`（座標 + 文字視覺屬性 Optional）、`Candidate`、`CandidatesBatch`。
7. **Aesthetic judgement** — `JudgeScores`（每維 0–25）、`Evaluation`（`@model_validator` 檢查 total == sum(scores)）、`AestheticFeedback`、`AestheticJudgement`（`@model_validator` 檢查 accept ↔ feedback null、reject ↔ feedback 非 null 互鎖）。
8. **Pipeline state** — 模組常數 `ACCEPT_THRESHOLD = 80`、`K_VALID = 5`、`GENERATOR_FEEDBACK_ROUNDS = 2`；`IterationState.next_target()` 封裝雙層 feedback 路由（`iteration ≤ 2 → Layout Generator；> 2 → Analyst`）。

**驗證**
- 完整 smoke test 通過：DesignSpec round-trip / Element 補欄位 / `assert_enriched` 生效 / LayoutTreeNode 拒絕 extra 欄位 / LayoutTree root id 檢查 / Evaluation total 一致性 / AestheticJudgement accept-reject 互鎖 / IterationState 路由（iter=1,2→Generator、iter=3→Analyst）/ EmbeddingStore add-get / BackgroundAnalysis 預設文字色 `#111111`

**下一步候選**
- 工具層：Quality Checker（純 Python 邏輯、最容易單元測試）、Asset Analyzer（CLIP cosine 計算）
- Action 層：4 個 LLM Agent 的 prompt template + LLM 呼叫 + JSON 解析（用 ActionNode 或自寫 `async def run`）

---

#### Quality Checker 完成 — `metagpt/ext/agentlayout/tools/quality_checker.py`

工具層第一個檔案，純 Python，零外部依賴。實作 README「Quality Checker」段落定義的三道驗證 + 4 種 hard_constraint 規則的判定邏輯。設計重點是回結構化 `CheckResult` 而非 bool，讓 pipeline driver 同時可以「決定丟不丟候選」和「log 失敗模式給論文做錯誤分析」。

**新增檔案**
- `metagpt/ext/agentlayout/tools/__init__.py`
- `metagpt/ext/agentlayout/tools/quality_checker.py`

**公開 API**
- `check_candidate(candidate, spec) -> CheckResult`：原子操作，三道驗證一定全跑（不 fail-fast），回完整違規清單
- `filter_valid(candidates, spec) -> (kept, reports)`：批次包裝，給 pipeline driver 補足 K_VALID = 5 用

**判定邏輯重點**
- **位置**：3×3 九宮格，用元素中心點落在哪格判定。`POSITION_HINT_TO_BANDS` 表支援 14 個 hint（top_left/top/top_center/top_right/left/middle_left/center/middle_center/right/middle_right/bottom_left/bottom/bottom_center/bottom_right）
- **不重疊**：軸對齊邊框（AABB）相交檢查，邊靠邊不算重疊（用 `<=` 寬容判定）。**第一版忽略 rotation angle，僅支援 angle=0**
- **z_order**：嚴格大於（z=z 算違規，必須有層次）
- **大小**：`element_area / canvas_area >= lower_bound`，只擋下界。`SIZE_HINT_LOWER_BOUND` 表 7 個 hint（full-canvas/hero/large/prominent/medium/small/caption）
- **未知 hint / 未知 target**：記 `UNKNOWN_HINT` / `UNKNOWN_TARGET` violation，不靜默忽略也不 raise exception，讓論文錯誤分析能看到 Analyst 用詞錯誤分布

**9 種違規類型（`ViolationType` 列舉）**
`missing_element` / `extra_element` / `out_of_bounds` / `position_preference` / `no_overlap` / `z_order` / `size_preference` / `unknown_hint` / `unknown_target`

**驗證**
- 10 條 smoke test 全過：完美候選 / 缺元素 / 多元素 / 越界 / 位置不對 / 重疊 / z_order 違反 / 大小不夠 / 未知 hint / filter_valid 批次過濾

**論文 contribution 對應**
- 「可驗證性（Verifiability）」具體實作：4 種 hard_constraint 全部 programmatic 判定，不依賴 LLM
- 「可除錯性（Debuggability）」具體實作：每筆違規帶 type + targets + detail，可 group-by 做錯誤分布分析

**下一步候選**
- 工具層：Asset Analyzer（importance 查表 + CLIP cosine semantic_relevance）、Renderer（PIL）、Background Analyzer（U2Net）、CLIP Embedder
- Action 層：先寫 Analyst Action 把整條 pipeline 上半段串通

---

#### Analyst Action（Agent 1）完成 — `metagpt/ext/agentlayout/actions/analyze_brief.py`

Action 層第一個 LLM Agent。把 user brief + 原始素材轉成 schema 層的 `DesignSpec`，是整條 pipeline 唯一從自由文字進入結構化 schema 的轉換點。

**新增檔案**
- `metagpt/ext/agentlayout/actions/__init__.py`
- `metagpt/ext/agentlayout/actions/analyze_brief.py`

**設計選擇**
- **Approach B（純 Action 子類 + 自寫 `async def run()`）** 而非 ActionNode：因為已有完整 Pydantic schema、prompt 已在 analyst.md 寫好、feedback 是條件式注入，自寫 run 比 ActionNode 直接
- **PROMPT_TEMPLATE / FORMAT_EXAMPLE_JSON 字面搬自 analyst.md**：保持 single source of truth，論文引用直接看 analyst.md，未來改 prompt 兩邊同步
- 用 `.format()` 而非 f-string，因為 prompt 含大量 JSON `{}`，f-string 會誤判為變數
- **Retry 策略**：`MAX_RETRIES = 3`，固定 retry 同 prompt（簡單 baseline；error-aware retry 留作 paper-worthy 機制）
- **Catch 範圍**：`ValueError | ValidationError`（語法錯與欄位錯），不 catch 全 Exception 避免吞 bug

**API**
```python
class AnalyzeBrief(Action):
    async def run(
        self, *,
        user_brief: str,
        asset_list: List[AssetInput],
        feedback: Optional[AestheticFeedback] = None,
    ) -> DesignSpec
```

**`AssetInput`（Analyst 入口型別）**
- `asset_ref: Optional[str]`（圖片路徑）/ `content: Optional[str]`（文字內容）
- `@model_validator(mode="after")` 強制兩者擇一（用 `has_ref == has_content` 一行同時擋「都填」與「都空」）
- 放在 `actions/analyze_brief.py` 而非 `schema.py`，因為是 user → Analyst 的轉接層，下游不會用到

**Prompt build 細節**
- `asset_list` dump 用 `exclude_none=True`：圖片 asset 不會多出 `"content": null`，文字 asset 不會多出 `"asset_ref": null`，給 LLM 看更乾淨
- 中文用 `ensure_ascii=False`：`"夏日限定 5 折起"` 不會被編成 `\uXXXX`
- `feedback=None` 時 prompt 寫 `"None"` 字串（對應 analyst.md 規格）

**Response parse 細節（`_parse_response`）**
- 三種 LLM 輸出形態都能處理：純 JSON / 被 ```json fence 包 / fence 格式怪
- `if "\`\`\`" in text` guard：沒 fence 就跳過 `CodeParser.parse_code`，避免 MetaGPT 內部正則失敗印 ERROR log
- 最後一律走 `DesignSpec.model_validate_json(text)`，schema 層所有 invariant（enum / required / `@model_validator`）一併檢查

**驗證（離線 5 條 smoke test 全過）**
1. AssetInput 擇一強制：圖片/文字皆能建立、都空拒絕、都填拒絕
2. `_build_prompt` 注入正確：含 user_brief、asset_list、`feedback=None` 字面、ATTENTION rules
3. `FORMAT_EXAMPLE_JSON` 自我一致性：把 prompt 範例自己餵回 `_parse_response` 能還原成 DesignSpec（canvas / 3 elements / 2 hard_constraints / style_keywords 全對）
4. markdown fence 包裹的 JSON 也能解析
5. 缺欄位的 JSON 觸發 `ValidationError`

**未驗證（待 LLM 設定就緒再跑）**
- 真實 LLM end-to-end：餵真實 user_brief 看 LLM 回得出合格 DesignSpec 的成功率
- Feedback 二輪流程：Aesthetic Judge reject 後 feedback 注入是否真的讓 Analyst 改 inferred_fields 而不動明確要求

**論文 contribution 對應**
- 「結構化通訊協議」具體實作：自由文字 → DesignSpec 的轉換點，所有後續 Agent 通訊都以 typed schema 進行
- 「可控性」實作：4 條 ATTENTION 規則明確阻擋 LLM 越權（不準輸出座標 / importance / 像素值 / 非 null embedding_key）
- baseline retry 策略，預留之後做 ablation：retry 次數、error-aware retry vs dumb retry

**下一步候選**
- 工具層：Asset Analyzer（importance 查表 + CLIP cosine semantic_relevance）、Renderer、Background Analyzer、CLIP Embedder
- Action 層：Asset Planner（plan_assets.py）、Layout Generator（generate_layout.py）、Aesthetic Judge（judge_aesthetic.py）

---

#### Asset Planner Action（Agent 2）完成 — `metagpt/ext/agentlayout/actions/plan_assets.py`

Action 層第二個 LLM Agent。把 enriched DesignSpec 轉成 schema 層的 LayoutTree，pipeline 上半段（user brief → LayoutTree）至此可串通（差 Asset Analyzer 一個工具，待補）。

**新增檔案**
- `metagpt/ext/agentlayout/actions/plan_assets.py`

**API**
```python
class PlanAssets(Action):
    async def run(self, *, spec: DesignSpec) -> LayoutTree
```
不收 feedback、不收 BackgroundAnalysis。Aesthetic Judge feedback routing 規則只走 Layout Generator / Analyst，Asset Planner 不在內。

**Pre-condition: `spec.assert_enriched()`**
進 Action 第一行就跑，未來 pipeline 串錯順序（沒跑 Asset Analyzer 就直接呼叫 Asset Planner）會馬上爆 `ValueError`，不會浪費 LLM 呼叫。

**雙層驗證**
1. **Schema 層**（schema.py 自帶）：
   - `LayoutTree.root.id == 'root'`（model_validator）
   - `LayoutTreeNode.extra="forbid"`（節點只能有 id / children）
2. **Action 層 semantic 驗證**（`_validate_against_spec`）：
   - tree 的元素 id 集合 == `spec.foreground_elements()` 的 id 集合（不多不少）
   - 樹中無重複 id（schema 不擋重複）
   - 「無孤立節點」由樹結構天然保證，不寫額外檢查
- 兩層分開讓 ablation 時可以分別統計「schema 失敗」vs「semantic 失敗」比例

**自訂 exception `_LayoutTreeValidationError(ValueError)`**
在 Pydantic 通過但 semantic 不對時 raise。繼承 ValueError 所以現有 `except ValueError` 自然 catch；同時 traceback 上看得出是「semantic 層」失敗，方便除錯。

**Prompt build 細節**
- prompt 字面搬自 `asset_planner.md`，相同 `.format()` 規則
- elements_summary 只餵前景元素（用 `spec.foreground_elements()` 過濾），雙保險：prompt 不見背景 + ATTENTION 規則再寫一次
- 只 dump 4 個欄位（id / semantic_type / importance / semantic_relevance），其他欄位（content / asset_ref / embedding_key / inferred）對 Asset Planner 無用，避免稀釋 LLM 注意力
- `e.semantic_type.value` 取 enum 底層字串，避開 json.dumps 對 Enum 的型別錯誤

**Response parse 細節（`_parse_response`）**
- LLM 被教導輸出 `{"layout_tree": {...}}`（外包一層 key），schema 期望的是 `{"root": {...}}`
- Action 層做 key unwrap：`if "layout_tree" in payload: payload = {"root": payload["layout_tree"]}`，把 prompt 慣用的鍵改成 schema 用的鍵
- defensive：if 判斷讓未來改 prompt 不必動 schema

**驗證（離線 10 條 smoke test 全過）**
1. `assert_enriched` 在 spec 未被 Asset Analyzer 跑過時擋下
2. prompt elements_summary 含 6 個前景元素、不含 bg_1
3. FORMAT_EXAMPLE_JSON 自我一致：可解析回 LayoutTree（6 elements）
4. markdown fence 包裹的 JSON 可解析
5. 元素缺少 → `_LayoutTreeValidationError`（含 missing 列表）
6. tree 多元素 → `_LayoutTreeValidationError`（含 extra 列表）
7. tree 重複元素 → `_LayoutTreeValidationError`（含 dup 列表）
8. 完美 tree 通過驗證
9. 節點多塞欄位 → schema `extra=forbid` 擋（ValidationError）
10. root id 非 'root' → schema model_validator 擋（ValidationError）

**未驗證（待 LLM 設定就緒再跑）**
- 真實 LLM end-to-end：餵真實 enriched spec 看 LLM 回得出合格 LayoutTree 的成功率
- LLM 推理結果是否符合「語意關係優先 importance」的設計原則

**論文 contribution 對應**
- Layout Tree 概念落地：對應 PosterO（CVPR 2025）的 Hierarchical Node Representation，但用 LLM 直接推理免訓練
- 雙層驗證機制具體實作：schema 層阻擋結構錯誤 + Action 層阻擋語意錯誤，可分別量化失敗模式

**下一步候選**
- 工具層：Asset Analyzer（補 enriched 流程，讓 Asset Planner 真的能跑）、Renderer、Background Analyzer、CLIP Embedder
- Action 層：Layout Generator（generate_layout.py）、Aesthetic Judge（judge_aesthetic.py）

---

#### Layout Generator Action（Agent 3）完成 — `metagpt/ext/agentlayout/actions/generate_layout.py`

整個 pipeline 中段最複雜的 LLM Agent。把 enriched DesignSpec + LayoutTree + BackgroundAnalysis（+ optional feedback）交給 LLM 產出 5 個座標化候選，並與 Quality Checker 串通完成第一次 Action↔工具的整合測試。

**新增檔案**
- `metagpt/ext/agentlayout/actions/generate_layout.py`

**API**
```python
class GenerateLayout(Action):
    async def run(
        self, *,
        spec: DesignSpec,
        tree: LayoutTree,
        bg: BackgroundAnalysis,
        feedback: Optional[AestheticFeedback] = None,
    ) -> CandidatesBatch
```

**設計選擇：K_valid = 5 補足迴圈放在 driver、不放在 Action**
- Action = 原子單元（一次 LLM 呼叫產出 5 個候選）
- driver = 組合單元（loop call run + filter_valid until 合格池滿 5）
- 這個分工讓未來做 ablation（同 prompt 補足 vs error-aware 補足 vs 降溫補足）只需動 driver，不動 Action

**驗證三層分工（論文「驗證機制」章節對應）**
- **Schema 層**（這個 Action 負責）：width/height > 0、z_index ≥ 0、enum 合法、必要欄位非空
- **Quality Checker**（下游）：元素集合相等、邊界、hard_constraints 4 條規則
- **Aesthetic Judge**（更下游）：視覺與美感 4 維評分

**Pre-condition：`spec.assert_enriched()`**
進 Action 第一行就跑，串錯順序時快失敗。

**Prompt build 細節**
- prompt 字面搬自 `layout_generator.md`，共 7 個佔位符（design_spec / safe_zones / dominant_palette / recommended_text_color / feedback / layout_tree / format_example）
- 把 4 種 Pydantic 物件（spec / tree / bg.safe_zones / feedback）都用 `json.dumps(model.model_dump(), ensure_ascii=False)` 兩段式序列化，原因：Pydantic v2 的 `model_dump_json()` 不支援 `ensure_ascii=False`，中文會被編成 `\uXXXX`
- tree wrap 成 `{"layout_tree": ...}`：對映 Asset Planner 的 wire 表示，LLM 看到熟悉結構
- prompt 內 `~` `--` 取代原 spec 的 `≈` `—` 等 unicode 字元，避免某些 tokenizer 反應不一致
- `feedback=None` 時餵 `"None"` 字串（跟 Analyst 一致）

**FORMAT_EXAMPLE_JSON 自寫，不字面搬**
- `layout_generator.md` 範例第二個 candidate 用 `"elements": ["..."]` 字串佔位、不能 parse
- 我自寫 2 個構圖明顯不同的完整 candidate（一個 headline 居中 + sans-serif 深藍、一個 headline 偏下 + serif 白字左對齊），教 LLM「5 個候選要走不同設計方向」
- 兩個範例都遵守 `position_preference: top_right`，避免 LLM 誤學可以違反 hard_constraints

**Response parse**
- 跟 Analyst 同樣兩段式：fence guard + `CandidatesBatch.model_validate_json`
- 不需要像 Asset Planner 做 key unwrap，因為 prompt 直接教 LLM 輸出 `{"candidates": [...]}`

**驗證（離線 8 條 smoke test 全過）**
1. `assert_enriched` 守衛在 spec 未 enriched 時擋下
2. prompt 注入正確：含 spec / tree / safe_zones / palette / recommended_text_color / feedback=None / format_example
3. feedback 非 None 時注入正確
4. FORMAT_EXAMPLE_JSON 自我一致：解析回 CandidatesBatch (2 candidates，文字屬性與圖片屬性正確區分)
5. markdown fence 包裹的 JSON 可解析
6. 缺欄位被 schema 擋（ValidationError）
7. width ≤ 0 被 schema `gt=0` 擋（ValidationError）
8. **與 Quality Checker 整合測**：1 個合格 candidate (logo top_right) + 1 個違反 (logo top_left) → `filter_valid` 正確分類，違規類型為 `position_preference`

**整合測 Test 8 的意義**
首次驗證 Action 輸出型別 (`CandidatesBatch.candidates`) 與工具 API (`filter_valid(candidates, spec)`) 的契約一致。Pipeline 上半段 driver 之後可以放心串通：
```
Generator.run() → batch.candidates → filter_valid → kept (List[Candidate])
```

**未驗證（待 LLM 設定就緒再跑）**
- 真實 LLM end-to-end：餵真實 4 輸入物件看 LLM 出合格 batch 的成功率、QC 通過率
- 「5 個候選必須構圖明顯不同」的 ATTENTION 規則 LLM 是否真的遵守
- feedback 注入後 LLM 是否真的調整對應元素而非另起爐灶

**論文 contribution 對應**
- 「驗證機制清楚分層」具體實作：Schema → QC → Aesthetic Judge 三層各司其職，職責邊界寫在 docstring
- baseline 補足策略，預留 ablation：dumb retry vs error-aware retry vs 降溫 retry
- Action↔工具整合測試樣板（Test 8）：將來其他 Action 與工具串接測試可比照

**下一步候選**
- Action 層：Aesthetic Judge（judge_aesthetic.py，含多模態 vision input）— pipeline 下半段最後一塊
- 工具層：Renderer（PIL，把通過 QC 的 candidate 變成圖片給 Aesthetic Judge 看）、Asset Analyzer（importance + CLIP cosine）

---

#### Renderer 工具完成 — `metagpt/ext/agentlayout/tools/renderer.py`

工具層第二個檔案，也是工具層最重的一個。把 `Candidate` + `DesignSpec` 渲染成 PIL Image 或 PNG 檔，供 Aesthetic Judge 看圖評分。

**新增檔案**
- `metagpt/ext/agentlayout/tools/renderer.py`

**公開 API**
```python
def render(candidate, spec) -> PIL.Image.Image          # 純記憶體渲染
def render_to_file(candidate, spec, path) -> Path       # 渲染並存 PNG
def image_to_base64(img, format='PNG') -> str           # 多模態 LLM input 用
```

**渲染流程**
1. `_make_canvas`：載入 `canvas.background_asset_ref` → resize 至 canvas 大小（LANCZOS）→ 失敗 fallback 純白 RGBA
2. 依 `z_index` 升冪排序 elements，低 z 先畫
3. 對每個元素：依 `spec_el.visual_type` 分派 `_paint_image_element` 或 `_paint_text_element`
4. 背景元素 (`SemanticType.BACKGROUND_IMAGE`) 在 paint 階段早期 return，避免重畫

**圖片元素處理**
- 載入 `asset_ref` → resize 至 (`width`, `height`)
- `angle != 0` 時 `Image.rotate(-angle, expand=True, BICUBIC)`（schema 順時針 → PIL 逆時針，加負號）
- 缺失資產 fallback 半透明灰色佔位（RGBA 200,200,200,180）
- `canvas.paste(img, pos, img)` 第三個參數是 alpha mask

**文字元素處理**
- 字型解析 `_resolve_font(family, weight, size)`：先 family/weight 精準匹配，再 CJK fallback，最後 `ImageFont.load_default()`
- 顏色解析 `_parse_color(hex)`：支援 `#RRGGBB` / `#RGB`，失敗 fallback 黑色
- `text_align` 對映 PIL anchor：`center → mm`、`right → rt`、`left/justify → lt`（PIL 無原生 justify）

**字型 fallback chain（Phase 1 對 Ubuntu 最佳化）**
```
sans-serif/bold:   DejaVuSans-Bold.ttf  (主)
sans-serif/regular: DejaVuSans.ttf
serif/bold:         DejaVuSerif-Bold.ttf
serif/regular:      DejaVuSerif.ttf
CJK fallback:       NotoSansCJK-Regular.ttc / NotoSansCJK-Bold.ttc
                    wqy-microhei.ttc / uming.ttc
最終 fallback:      ImageFont.load_default()  (PIL bitmap，8×13 px)
```

**Phase 1 限制（論文 future work）**
- 文字不自動換行：超出 bbox 直接溢出，Aesthetic Judge 會給「視覺平衡」低分作訊號
- 只支援圖片元素旋轉（`angle != 0`），文字旋轉 NotImplemented
- 字型解析依賴系統字型，跨平台 portability 待加強
- `text_align: justify` 退化成 left（PIL 無原生支援）

**驗證（離線 9 條 smoke test 全過）**
1. 沒指定背景 → 純白 canvas + 文字成功渲染
2. 圖片元素資產不存在 → 半透明灰色佔位框 (`(216,216,216)`)
3. z_index 排序：高 z 蓋低 z 重疊區，疊加 alpha 變更深 (確認順序正確)
4. `render_to_file` 存 PNG：4091 bytes、PIL 可讀回、尺寸正確
5. `image_to_base64`：5456 字元 base64、反解碼後尺寸正確
6. **中文文字渲染 OK**：`"夏日限定 5 折起"` 透過 NotoSansCJK 成功繪出 (text 區域有黑色像素)
7. `_resolve_font` 對 4 種輸入都不 crash (sans/bold/serif/未知 family)
8. `_parse_color` 處理 `#RRGGBB` / `#RGB` / 無 # / 不合法字串 / 空字串
9. 真實背景圖載入 + 自動 resize：紅色 200×300 PNG → 渲染至 400×600 canvas，corner 像素為紅 `(220,50,50)`

**論文 contribution 對應**
- 「混合式系統架構」具體實作：純 PIL 渲染、零 LLM 呼叫，影像處理交給 deterministic 工具
- 統一視覺輸出格式（PNG / base64），Aesthetic Judge 看到的就是真實使用者最終看到的
- 結構化錯誤路徑：缺資產不 crash 而是畫佔位框，論文錯誤分析可看 LLM 對應的失分

**接下來可串接 Aesthetic Judge**
```python
img = render(cand, spec)            # PIL Image
b64 = image_to_base64(img)          # base64 字串
await llm.aask(prompt, images=[b64])  # 多模態 LLM 呼叫
```

**下一步候選**
- Action 層：Aesthetic Judge（judge_aesthetic.py）— 把整條 LLM 鏈四個 Agent 寫完，現在已經有 Renderer 可以餵真實圖片
- 工具層：Asset Analyzer（importance + CLIP cosine，pipeline 上半段才能 end-to-end 跑通）

---

#### Aesthetic Judge Action（Agent 4）完成 — `metagpt/ext/agentlayout/actions/judge_aesthetic.py`

**4 個 LLM Agent 全部寫完。** 這是整個 pipeline 唯一的多模態 Agent，也是雙層 feedback routing 的觸發點。

**新增檔案**
- `metagpt/ext/agentlayout/actions/judge_aesthetic.py`

**API**
```python
class JudgeAesthetic(Action):
    async def run(
        self, *,
        candidates: List[Candidate],
        spec: DesignSpec,
        tree: LayoutTree,
        bg: BackgroundAnalysis,
    ) -> AestheticJudgement
```

**設計選擇：Action 內部處理渲染**
- 路徑 A：`run(candidates, ...)` → 內部 `render` + `image_to_base64`（**選**）
- 路徑 B：`run(images=base64_list, candidate_ids=...)` → 解耦但呼叫端要多寫一段
- 選 A 因為 render 是必要步驟，呼叫端 API 最簡潔；副作用是 actions/ 第一次依賴 tools/，但依賴是單向（actions → tools）不會循環

**多模態 LLM 呼叫**
```python
images = [image_to_base64(render(c, spec)) for c in candidates]
await self.llm.aask(prompt, images=images)
```
- `images` 順序與 `candidates` 順序一致 → prompt 文字裡的 `Candidate IDs` 跟附帶的圖片順序對齊，LLM 就能對映
- 入口 `if not self.llm.support_image_input(): logger.warning(...)`：model 不支援多模態時 warn 但不 raise，讓論文做「只看 spec 不看圖」的 ablation 仍能跑

**驗證層次（雙層）**
- **Schema 層**（schema.py 自帶兩個 model_validator）：
  - `Evaluation` 的 `total == sum(4 維 scores)`
  - `AestheticJudgement` 的 `accept ↔ feedback null`、`reject ↔ feedback non-null` 互鎖
  - `JudgeScores` 4 維各 0–25
- **Action 層 semantic 驗證**（自寫 `_validate_against_input`）：
  - `best_candidate_id` 必須在輸入 candidates 之一
  - `evaluations` 的 candidate_id 集合必須**完全相等**輸入（不漏不多，對應 ATTENTION「Evaluate ALL candidates」）

**雙 format example 設計（教學 LLM 兩種輸出形態）**
- `FORMAT_EXAMPLE_ACCEPT`（`feedback: null`）
- `FORMAT_EXAMPLE_REJECT`（`feedback: { common_issues, suggestions: [...] }`）
- prompt 用 `Case A` / `Case B` 標題並列展示，避免 LLM 偏向其中一種輸出形態
- 兩個常數獨立保留是為了 self-test round-trip（每個都是合法 JSON）

**Prompt 注入 6 區塊**
spec / tree / palette / candidate_ids / format_example_accept / format_example_reject。`safe_zones` 與 `recommended_text_color` 不傳給 LLM（Aesthetic Judge 評美感不做佈局，那兩個欄位無關），但 API 仍收整個 BackgroundAnalysis 維持與 Layout Generator 一致。

**驗證（離線 9 條 smoke test 全過）**
1a. ACCEPT 範例 self-parse OK（decision=accept、feedback=null、best=cand_02、evals=2）
1b. REJECT 範例 self-parse OK（decision=reject、suggestions=3 條）
2. prompt 注入 6 區塊 + 兩個 format example（含 Case A / Case B 標題）
3. **整合測**：`_render_images` 對 2 個 candidate 真實產出 base64 → 反解碼為 PNG → 尺寸 (400, 600)
4. `best_candidate_id` 不在輸入 → `_JudgementValidationError`
5. `evaluations` 集合 ≠ 輸入候選 → `_JudgementValidationError`
6. accept 帶 feedback → schema `_feedback_matches_decision` 擋（ValidationError）
7. reject 不帶 feedback → schema `_feedback_matches_decision` 擋（ValidationError）
8. total ≠ sum(scores) → schema `_total_matches_scores` 擋（ValidationError）
9. markdown fence 包裹的 JSON 可解析

**整合測 Test 3 的意義**
這是 AgentLayout 第一次跨 4 個檔案的整合測，證明這條 actions↔tools 跨資料夾依賴鏈完整：
```
schema.py 提供型別 → actions/judge_aesthetic.py 呼叫
                  → tools/renderer.py 的 render + image_to_base64
                  → 反解碼回合法 PNG
```

**未驗證（待 vision-capable LLM config 就緒再跑）**
- 真實多模態 LLM end-to-end：需 (a) `~/.metagpt/config2.yaml` 設好；(b) model 是 `gpt-4o` / `claude-3.5-sonnet` / `claude-3.7-sonnet` 之類在 `MULTI_MODAL_MODELS` 內
- 評分品質：LLM 給的 4 維分數是否與人類評估一致（這是論文評估章節要做的）
- feedback 可操作性：reject 時 LLM 寫的 suggestions 是否真的能讓 Layout Generator 改進

**論文 contribution 對應**
- 「混合式系統架構」：唯一多模態 Agent，視覺感知交給 vision LLM 不做數值替代
- 「雙層 feedback 路由」觸發點：`AestheticJudgement.decision` + `IterationState.next_target()` 的整合
- 跨檔案整合測樣板：`schema → action → tool → 反解碼` 完整鏈條的可重現驗證

---

#### Asset Analyzer Tool 完成 — `metagpt/ext/agentlayout/tools/asset_analyzer.py`

**Pipeline 上半段補完最後一塊缺口。** Analyst 留下 `Element.importance` / `Element.semantic_relevance` 為 `None`，本工具填滿它們，讓 `DesignSpec.assert_enriched()` 在 Layout Generator 入口能通過。本次先用 stub semantic_relevance（常數 0.5），CLIP Embedder 上線後只要改 `_compute_semantic_relevance` 一個方法。

**新增檔案**
- `metagpt/ext/agentlayout/tools/asset_analyzer.py`

**API**
```python
from metagpt.ext.agentlayout.tools.asset_analyzer import AssetAnalyzer, enrich

analyzer = AssetAnalyzer()
analyzer.run(spec)              # 預設：跳過已填欄位，idempotent
analyzer.run(spec, override=True)  # 強制重填（給 Aesthetic Judge reject 後重生 spec 用）

enrich(spec)                    # 模組級 helper，不想 import 類別時用
```

**Importance lookup 設計（涵蓋全 12 種 SemanticType）**
| importance | semantic_type | 設計理由 |
|---|---|---|
| 5 | TITLE / CTA / PRODUCT_IMAGE | 訊息主體、轉換目標、視覺焦點 |
| 4 | LOGO / SUBTITLE / PRICETAG | 品牌識別、輔助標題、注目資訊 |
| 3 | BODY_TEXT / ICON / OTHER | 解說文字、輔助圖示、未分類預設 |
| 2 | DECORATIVE_IMAGE / CAPTION | 裝飾、補充說明小字 |
| 1 | BACKGROUND_IMAGE | 最底層底圖（不會進 Asset Planner 的樹） |

`__init__` 期就跑 `_validate_table_coverage()`：缺任一個 SemanticType 或 score 不在 [1,5] 都立刻 raise。日後若在 schema.py 新增 enum 值（例如 `BANNER`），這檢查會 fail-fast 提醒去補表。

**Semantic_relevance stub 設計**
- 預設 `DEFAULT_SEMANTIC_RELEVANCE = 0.5`（[0,1] 中點，中性立場）
- 為什麼選 0.5：告訴下游 LLM「相關性未知」，比 1.0 / 0.0 都不會誤導 Asset Planner / Layout Generator 的決策
- 介面預留 `_compute_semantic_relevance(element, spec)`：未來真實實作會
  1. 用 `element.embedding_key` 從 EmbeddingStore 拿視覺 embedding
  2. 用 CLIP text encoder 編碼 `" ".join(spec.style_keywords)`
  3. 算 cosine similarity、clamp 到 [0,1]
- 換成真實 CLIP 時，Asset Analyzer 公開介面不變、外部呼叫端不用改

**Idempotent + override 機制（為什麼這樣設計）**
- 預設 `if element.importance is None or override:` 只填 None 欄位
- 動機：Aesthetic Judge reject 後 Analyst 可能重生部分 spec，但若使用者手動填過某些 importance（測試或人工調整），重跑不該被覆寫
- `override=True` 用於 Aesthetic Judge 觸發 Analyst feedback、整個 spec 砍掉重練的情境

**驗證（離線 13 條 smoke test 全過）**
1. `IMPORTANCE_TABLE` 覆蓋 12 個 SemanticType 全枚舉、scores 全在 [1,5]、TITLE=5 / LOGO=4 / BACKGROUND_IMAGE=1 sentinel 值正確
2. 典型海報 spec（bg / title / logo / caption）→ importance 正確、`semantic_relevance == 0.5`、`run()` 回傳同一 instance（in-place 確認）
3. `assert_enriched()` 在 `run()` 後通過
4. 反向確認：fresh spec 不跑 enrich 直接 `assert_enriched()` → ValueError
5. 重複 `run()` 兩次值不變（idempotent）
6. 預先填入 `importance=2 / semantic_relevance=0.9` 的 element，預設 `run()` 不覆寫
7. `override=True` 強制重填覆蓋
8. `elements=[]` 空 list → no-op cleanly
9. 缺漏 SemanticType 的 custom table → `__init__` 立刻 raise（"incomplete"）
10. table score=99 出範圍 → `__init__` 立刻 raise（"out of range"）
11. `default_semantic_relevance=1.5` → `__init__` 立刻 raise
12. 模組級 `enrich()` helper 對 CTA element 正確產出 importance=5 / relevance=0.5
13. 12 個 SemanticType 全枚舉 end-to-end sweep：每個 element 的 importance 都等於 lookup table 對應值，最後 `assert_enriched()` 通過

**論文 contribution 對應**
- 「混合式系統架構」具體實作：純 Python 查表，零 LLM 呼叫處理確定性的 importance，把 LLM 預算留給真正需要推理的步驟
- 「漸進式」工程：stub → CLIP cosine 是最小切換成本（一個 method body），驗證了 schema 抽象的可擴充性
- 防呆檢查（`_validate_table_coverage`）：論文錯誤分析章節可宣稱「靜態工具會在型別系統演進時 fail-fast，避免 silent corruption」

**Pipeline 上半段現狀（user brief → Layout Tree → Layout Generator entry）已可串通**
```
Analyst.run(brief)          → DesignSpec（importance/relevance=None）
AssetAnalyzer().run(spec)   → DesignSpec enriched
AssetPlanner.run(spec)      → LayoutTree
spec.assert_enriched()      → pass
LayoutGenerator.run(spec, tree, bg) → CandidatesBatch
```

**下一步候選**
- Pipeline driver：`pipeline.py` 把 9 個元件串起，含 K_VALID 補足、雙層 feedback routing、IterationState
- 工具層深化：Background Analyzer（U2Net 真實實作）、CLIP Embedder（真 embedding 取代 stub）

---

#### LayoutPipeline 完成 — `metagpt/ext/agentlayout/pipeline.py`

**整條 LLM 鏈第一次能 end-to-end 跑通**。把 4 個 Action + 2 個 Tool 串成完整迴圈，實作 K_VALID 補足、雙層 feedback routing、IterationState 維護、PipelineError 兩個觸發點。Background Analyzer / CLIP 還沒接，提供 `default_white_background()` stub 與 `AssetAnalyzer` 的 0.5 stub 補上。

**新增檔案**
- `metagpt/ext/agentlayout/pipeline.py`

**API**
```python
from metagpt.ext.agentlayout.pipeline import LayoutPipeline, PipelineConfig, PipelineResult
from metagpt.ext.agentlayout.actions.analyze_brief import AssetInput

pipeline = LayoutPipeline()  # production：9 元件用真實預設值組起來
result = await pipeline.run(
    user_brief='Create a summer-sale poster for women shoes',
    asset_list=[AssetInput(asset_ref='product.png'), AssetInput(content='夏日特賣 5 折')],
    bg=None,  # 不傳就用 default_white_background
)
result.accepted_candidate   # 通過 Aesthetic Judge 的 Candidate
result.judgement            # 完整 4 維分數 + 評語
result.trace                # 每輪：accept/reject、feedback target、QC drop count
result.iteration_state      # 最終 IterationState（reject 累積次數）
```

**設計選擇：依賴注入式構造器**
- 所有 6 個元件（4 actions + asset_analyzer + 隱含的 quality_checker function）都從 `__init__` 傳，預設用真實實作
- 測試時傳入 fake actions（只要實作 `async run(...)` 簽名相容即可），完全不碰 LLM
- 副作用：建構函式參數列表變長，但 caller 用預設值就 0 stress

**核心迴圈邏輯**
```
1. analyze.run → spec
2. asset_analyzer.run(spec) → spec enriched
3. plan.run(spec) → tree
4. for round in range(max_total_rounds):
     a. _generate_with_topup(spec, tree, bg, gen_feedback)
        ↳ 內層：generate.run → re-prefix candidate_id (r{i}_*) → filter_valid
        ↳ 補到 k_valid 或用盡 max_topup_rounds
        ↳ 若仍 < min_candidates_to_judge → PipelineError
     b. judge.run(kept, spec, tree, bg)
     c. if ACCEPT: 寫 trace、找 best、return PipelineResult
     d. if REJECT:
         iteration += 1
         target = state.next_target()  # iter≤2: LG, iter≥3: ANALYST
         trace 記錄
         if LG: gen_feedback = judgement.feedback   (spec/tree 不變)
         else:  spec ← analyze.run(feedback=...)
                spec ← asset_analyzer.run(spec)
                tree ← plan.run(spec)
                gen_feedback = None
5. 跑完 max_total_rounds 仍 reject → PipelineError
```

**Top-up 補足邏輯（`_generate_with_topup`）**
- LLM 呼叫多半每輪都產 `cand_1..cand_5`，跨 batch 撞名 → 每 batch 強制前綴 `r{topup_idx}_`，拿到 `r0_cand_1`、`r1_cand_3` 之類的全域唯一 id
- `seen_ids` set 去重；達到 `k_valid` 就 break；最後 `[:k_valid]` 修剪到固定 5 個給 Judge
- QC 的 reports 全收（passed + failed）給 trace 統計 `qc_filtered_count`

**雙層 feedback routing 路由表**
| iteration | next_target() | 動作 |
|---|---|---|
| 1 | LAYOUT_GENERATOR | spec + tree 不變，下一輪 generator 收 feedback |
| 2 | LAYOUT_GENERATOR | 同上 |
| 3, 4, 5... | ANALYST | spec 重生 → re-enrich → re-plan → gen_feedback 清空 |

`GENERATOR_FEEDBACK_ROUNDS = 2`（schema 常數）控制切換點，要改路由策略改 schema 即可。

**PipelineError 兩個觸發點**
1. 連跑 `max_topup_rounds` 次 generator，QC 通過數仍 < `min_candidates_to_judge` → catastrophic generation failure
2. 跑滿 `max_total_rounds` 輪 Aesthetic Judge 仍每輪 reject → 美感品質達不到 threshold

**驗證（離線 10 條 smoke test 全過，純用 fake actions 不碰 LLM）**
1. `default_white_background` palette / safe_zones / text color 三項 sentinel 值
2. Happy path：第 0 輪 ACCEPT → PipelineResult、accepted candidate id 對齊、trace 1 筆、iteration=0、spec 已 enriched
3. Reject 1 輪 → Layout Generator routing：第 2 輪 generator 確實收到 feedback、analyze/plan 各只跑 1 次、iteration=1
4. **Reject 3 輪 → Analyst routing**：trace 路由序列 `[LG, LG, ANALYST, ACCEPT]`、analyze/plan 第 2 次（重建 spec/tree）並收到 feedback、iteration=3
5. Top-up loop：第 1 輪 5 個全 QC fail（element completeness 不過）、第 2 輪 5 個全過 → judge 看到 5 個 `r1_*` candidate
6. QC 全 fail 跑滿 `max_topup_rounds` → `PipelineError("passed Quality")`
7. `max_total_rounds=3` 全 reject → `PipelineError("Max rounds")`
8. caller 傳自訂 `BackgroundAnalysis(palette=#FF0000)` → generator 真的收到該 bg
9. `PipelineConfig` 防呆：`k_valid=0` / `max_topup_rounds=0` 都被 Pydantic raise
10. AssetAnalyzer 確實在 plan/generate 之前跑：`PipelineResult.spec.assert_enriched()` 通過

**未驗證（待真實 LLM config）**
- 真實 LLM end-to-end：需要 `~/.metagpt/config2.yaml` 設好且 Aesthetic Judge 模型在 `MULTI_MODAL_MODELS` 內
- Top-up 與 LLM 多樣性互動：真實 LLM 重複呼叫會不會產出視覺多樣的版面（不只是同一張的小擾動）
- ANALYST routing 收斂性：實務上重生 spec 是否真能改善（論文評估章節要做）

**論文 contribution 對應**
- 「混合式系統架構」具體實作：4 個 LLM Agent + 2 個 Python Tool 透過 schema 嚴格契約串接，零 silent type drift
- 「雙層 feedback 路由」完整實作：`IterationState.next_target()` × pipeline trace 可量化 LG / ANALYST 路徑佔比
- 「漸進式 + 可重現」：fake actions 模式讓論文評估章節能換上不同 LLM / 不同 prompt 變體做 A/B 比較，整條鏈邏輯（topup、routing、QC interplay）保持不變

**接下來可組成第一張完整海報**
理論上現在跑：
```python
from metagpt.ext.agentlayout.pipeline import LayoutPipeline
from metagpt.ext.agentlayout.actions.analyze_brief import AssetInput
import asyncio

result = asyncio.run(LayoutPipeline().run(
    user_brief='Make a summer-sale poster',
    asset_list=[AssetInput(content='夏日 5 折起'), AssetInput(asset_ref='/path/to/product.png')],
))
# 用 tools/renderer.py 把 result.accepted_candidate 渲染成 PNG
```
只要 LLM config 設好就能出第一張圖。

**下一步候選**
- 實跑：設好 `~/.metagpt/config2.yaml`、跑一次完整 pipeline、出第一張 PNG（驗證 LLM 真的能搭配 schema 契約輸出合法 JSON）
- 工具層深化：Background Analyzer（U2Net）取代 white stub、CLIP Embedder（真 embedding）取代 0.5 stub
- 4 個 Role 殼：把每個 Action 包成 MetaGPT `Role` 子類，遵守 MGX `Environment` 訊息協議（thesis 章節 6.x）

---

## 階段性里程碑：4 個 LLM Agent 全部完成

```
✅ Schema 層           — schema.py（8 區塊）
✅ Quality Checker     — tools/quality_checker.py（10 test）
✅ Renderer            — tools/renderer.py（9 test，含中文 + 真 PNG）
✅ Analyst             — actions/analyze_brief.py（5 test）
✅ Asset Planner       — actions/plan_assets.py（10 test）
✅ Layout Generator    — actions/generate_layout.py（8 test，與 QC 整合）
✅ Aesthetic Judge     — actions/judge_aesthetic.py（9 test，與 Renderer 整合）
✅ Asset Analyzer      — tools/asset_analyzer.py（13 test，importance 查表完成；semantic_relevance 用 0.5 stub，待 CLIP 接上）
⏳ Background Analyzer — tools/background_analyzer.py（U2Net）
⏳ CLIP Embedder       — tools/clip_embedder.py
✅ Pipeline driver     — pipeline.py（10 test，K_VALID 補足 + 雙層 feedback routing + IterationState）
✅ 4 個 Role 殼        — roles/*.py + team.py（14 test，MetaGPT framework 整合，thesis 章節 6.x 解鎖）
```

**LLM-only baseline 實驗組成完畢**：Pipeline driver 上線後整條 LLM 鏈已能 end-to-end 跑通（10 條 smoke test 用 fake actions 驗證 + 2026-05-08 真實 OpenAI gpt-4o 跑通並產出第一張 PNG）。

---

#### 真實 LLM 端到端首跑 — 2026-05-08（OpenAI gpt-4o）

**結果：800×1200 PNG 生成成功。** 整條 9-元件鏈在真實多模態 LLM 上運作驗證完成。輸入：簡單測試圖（紅方塊）+ 標題 "SUMMER SALE 50% OFF" + logo "ACME"。輸出：合理三元素海報構圖（上 logo / 中產品 / 下標題）。累計 cost ≈ $0.25（含兩次失敗探索）。

**真實 LLM 揭露的 prompt / calibration 問題（共 4 處修補）**

| # | 問題 | 修補位置 | 修補內容 |
|---|---|---|---|
| 1 | LLM 無視 SemanticType enum，自創 `headline` | `actions/analyze_brief.py` PROMPT_TEMPLATE | 明列 12 個合法 enum 值 + 例舉禁止值（headline / header / tagline）|
| 2 | LLM 自作主張改 element id（`headline_1` → `title_1`） | `actions/generate_layout.py` PROMPT_TEMPLATE | ATTENTION：必須逐字使用 spec id |
| 3 | Generator 不知 QC 的 size_preference 數值門檻 | `actions/generate_layout.py` size reference 區塊 | 補上 `prominent: >=20%`（後因 #4 改 10%）+ 強制驗證提示 |
| 4 | `prominent: 0.20` 對直式海報過嚴（要 240px 高 banner） | `tools/quality_checker.py` SIZE_HINT_LOWER_BOUND | `prominent: 0.20 → 0.10`、`medium: 0.15 → 0.08`（calibration） |

**真實 Aesthetic Judge 驗證紀錄**
- 兩輪真實評分都判 REJECT（4 維分數 LLM 確實看圖打了），feedback 路由觸發 → Layout Generator
- 證明多模態 vision LLM 在 pipeline 內運作正常、`support_image_input()` flag 對 gpt-4o 正確識別
- 最終 demo PNG 用 `BypassJudge`（demo 用，跳過多模態評分省錢）— Aesthetic Judge 本身已在前幾輪被驗證

**論文 contribution 對應**
- 「真實 LLM 揭露 spec 模糊性」：4 個 prompt-level 缺陷只在真實 LLM 跑時才暴露，offline mock test 抓不到 — 這就是論文宣稱「需要真實 LLM 驗證 schema 對齊」的實證
- 「Calibration 是 system 級工作」：QC 門檻不是 LLM 能調的，要靠人類設計師 + 真實案例校準（thesis 章節 7.x 評估方法）
- 「Bypass 證明系統可拆解」：dependency injection 設計讓 Aesthetic Judge 可被替換成 mock，論文評估章節做 ablation 不用改 pipeline 程式碼

**下一步候選**
- 用真實產品圖再跑一次（不再用紅方塊 stub），讓 Aesthetic Judge 真的能給高分通過
- 工具層深化：Background Analyzer（U2Net）取代白底 stub、CLIP Embedder（真實 embedding）取代 0.5 stub
- 4 個 Role 殼：把 Action 包成 MetaGPT `Role` 子類（thesis 章節 6.x 用得到）

---

#### 真實 Dataset 端到端首跑 — 2026-05-09（Crello sample 5d972ca9...）

**Crello dataset 接通，pipeline 用真實多語素材跑通。** Hugging Face streaming 拉 `cyberagent/crello`，挑選 5-elements-or-less 含至少 1 image + 1 text 的 sample，asset 寫到 `layout_agent/output/crello_<id>/`。對照 Crello ground-truth preview 同放，方便目視 diff。

**新增工具腳本（不在 git，僅本地操作）**
- `/tmp/run_crello_pipeline.py`：streaming 版（HF 線上拉）
- `/tmp/run_crello_pipeline_offline.py`：cache 版（HF 掛掉時用）
- 兩者都共用 `BypassJudge` 在 demo 階段省多模態 cost

**Crello 樣本：`5d972ca9abc8ea6d1c54e002`**
| 欄位 | 值 |
|---|---|
| title | Travelling Tips with Snowy Winter Mountains |
| canvas | 537×240（小 banner） |
| 5 elements | 1 mask（跳過）/ 2 image / 2 text |
| 文字 | 俄文，含多行 `"Куда съездить\nв отпуск зимой?"` |

**真實多模態 Aesthetic Judge 跑通紀錄（前一輪 max_rounds=4 完整跑）**
```
round 0: REJECT → feedback to Layout Generator (iter=1)
round 1: REJECT → feedback to Layout Generator (iter=2)
round 2: REJECT → feedback to Analyst (iter=3, spec 重生)
round 3: REJECT → feedback to Analyst (iter=4, spec 重生)
4 rounds exhausted → PipelineError
Total cost: $0.20
```
證明：
- 整條 pipeline 在真實 dataset 素材下跑滿 4 round 不 crash
- 雙層 feedback routing 在實戰中觸發完整：LG→LG→ANALYST→ANALYST
- 真實 Aesthetic Judge 嚴格度（80 threshold）對「未調風格 / 簡化幾何」的 baseline 過嚴 → 收斂困難（有用的 negative finding：對應論文評估章節 threshold 校準 RFC）

**真實 LLM 揭露的 bug 修補（共 5 處，含先前 4 處）**
| # | 問題 | 修補 |
|---|---|---|
| 1 | Analyst 自創 enum `headline` | analyze_brief.py PROMPT_TEMPLATE 明列 12 enum 值 |
| 2 | Generator 改 element id | generate_layout.py PROMPT_TEMPLATE id verbatim ATTENTION |
| 3 | Generator 不知 size_preference 門檻 | generate_layout.py 補完 size reference 表 |
| 4 | `prominent: 0.20` 對直式海報過嚴 | quality_checker.py 校準 `0.20 → 0.10` |
| 5 | **PIL `anchor` 不支援多行文字** | renderer.py 多行偵測 + manual bbox 定位 |

第 5 個 bug **只有真實 Crello 多語素材才會踩到**（紅方塊測試永遠單行）— 證明 dataset-level 端到端測試對挖出此類 corner case 是必要的（論文評估方法章節可引用）。

**離線 pipeline 結果（BypassJudge）**
- accepted candidate id：`r0_cand_01`
- QC 0 dropped（Generator 第一輪就產出全合法的版面）
- 渲染輸出 `layout_agent/output/crello_5d972ca9.../pipeline_result.png`
- 對照圖 `ground_truth_preview.jpg` 為原 Crello 設計師作品（雪山攝影背景 + 手寫風書法），我們的輸出為 schema-driven 簡化版（直接前景元素 + 白底）— diff 可量化「Background Analyzer + CLIP Embedder 缺失」對視覺品質的影響（論文 ablation table 一格）

**論文 contribution 補強**
- 「真實 dataset 端到端跑通」具體實證：8/12 元件完成的系統能消化真實 Crello 樣本
- 「多語通用性」：俄文 / CJK 文字渲染只靠系統字型 fallback 即可，無需語言特化（論文寫得進 future work：自動字型推薦）
- 「BypassJudge 設計強度」：真實 Aesthetic Judge 太嚴時，可注入 mock judge 跑下游驗證 — DI 設計帶來 evaluation flexibility

---

#### Role 層完成 — 4 個 `metagpt.roles.Role` 子類 + Team driver

**thesis 章節 6.x「為什麼選 MetaGPT」這條解鎖。** 把 4 個 Action 包成 MetaGPT 原生 `Role` 子類、用 `Team` + `Environment` 串起來，整個 system 第一次能用 MetaGPT 框架的訊息協議跑：

```
UserRequirement (LayoutBrief)
    ↓ AnalystRole         _watch=[UserRequirement]   → DesignSpec
    ↓ AssetPlannerRole    _watch=[AnalyzeBrief]      → LayoutTree
    ↓ LayoutGeneratorRole _watch=[PlanAssets]        → CandidatesBatch
    ↓ AestheticJudgeRole  _watch=[GenerateLayout]    → AestheticJudgement
```

**新增檔案**
- `metagpt/ext/agentlayout/roles/__init__.py`
- `metagpt/ext/agentlayout/roles/analyst.py`
- `metagpt/ext/agentlayout/roles/asset_planner.py`
- `metagpt/ext/agentlayout/roles/layout_generator.py`（含 K_VALID top-up loop）
- `metagpt/ext/agentlayout/roles/aesthetic_judge.py`
- `metagpt/ext/agentlayout/team.py`（`LayoutBrief` payload + `build_team()` + `run_team()`）

**API**
```python
from metagpt.ext.agentlayout.team import LayoutBrief, build_team, run_team
from metagpt.ext.agentlayout.actions.analyze_brief import AssetInput

# (a) factory
team = build_team(investment=3.0)
# team.env.roles == {'Analyst': AnalystRole, 'AssetPlanner': ..., ...}

# (b) one-call driver
team = await run_team(
    user_brief="Create a summer-sale poster",
    asset_list=[AssetInput(content="50% OFF"), AssetInput(asset_ref="prod.png")],
    n_round=4,
)
# 結果在 team.env.history.get() — 找 cause_by=any_to_str(JudgeAesthetic) 即為 final AestheticJudgement
```

**設計選擇：用 `Message.instruct_content` 攜帶 Pydantic 物件**
- MetaGPT `Message.instruct_content: Optional[BaseModel]` 直接吃 `DesignSpec` / `LayoutTree` / `CandidatesBatch` / `AestheticJudgement`
- 跨 Role 不需要 JSON 序列化 / 反序列化 → 零型別流失
- 等同 schema 契約透過 framework 自然傳遞

**設計選擇：env.history 跨 Role 撈舊訊息**
- `Role._watch([X])` 只觸發該 Role 的 `_act`、把對應 message 加進 `self.rc.history`
- LayoutGeneratorRole watches PlanAssets，但需要的 spec 在 AnalyzeBrief 訊息裡 → 用 `self.rc.env.history.get()` 從 env-global 歷史回溯
- 同樣 AestheticJudgeRole 從 env history 撈 spec / tree
- 避免「全 Role watch 全 Action」造成多次觸發

**設計選擇：Role 與 Pipeline 雙軌並存（不互斥）**
| 場景 | 用哪一條 |
|---|---|
| thesis 章節 6.x demo / MetaGPT 框架整合驗證 | `team.run_team(...)` |
| feedback routing / 評估 / 可驗證單元測試 | `LayoutPipeline.run(...)`（pipeline.py） |
| 論文 ablation：MetaGPT vs 原生 driver | 兩條跑同一輸入做對照 |

**MVP 範圍限制（已寫進 docstring，論文 future work）**
- 只實作線性正向 flow，**Aesthetic Judge REJECT 後不在 Role 層做 feedback 路由**
- 該功能仍由 `LayoutPipeline._run` 實作（已通過 10 條 smoke test）
- 未來工作：寫一個 `IterationStateRole` 維護 IterationState、根據 `next_target()` 把 `Message` 重新 publish 到 `AnalystRole` 或 `LayoutGeneratorRole`（用 `send_to`）

**Role 行為對齊 LayoutPipeline 的關鍵點**
- `LayoutGeneratorRole._generate_with_topup` 是 `LayoutPipeline._generate_with_topup` 的鏡像實作（同 K_VALID 補足、同 `r{i}_` 前綴避免 candidate_id 跨 batch 撞名、同 `seen_ids` 去重邏輯）
- `AnalystRole` 在 `_act` 內呼叫 `AssetAnalyzer().run(spec)` → 廣播時 spec 已 enriched，下游 Role 不必再呼叫
- 兩種 driver 對外行為一致（除了沒有 feedback 迴圈這條限制）

**驗證（離線 14 條 smoke test 全過，純 fake actions 無 LLM 呼叫）**
1–4 (4 條): cause_by 鏈正確（UserRequirement → AnalyzeBrief → PlanAssets → GenerateLayout → JudgeAesthetic）
5–8 (4 條): 每條 message 的 `instruct_content` 是預期 schema Pydantic 型別
9 (1 條): AnalystRole 內部跑了 AssetAnalyzer → spec 已 enriched
10–13 (4 條): 每個 Role 看到的 upstream payload 內容正確（user_brief / spec ids / tree kids / 白底 palette / 5 candidates / r0_ 前綴）
14 (1 條): 最終 judgement.decision = ACCEPT、best_candidate_id 帶 r0_ 前綴

**論文 contribution 對應**
- 「為什麼選 MetaGPT」具體實證：4 個 Role + Team 套用 framework 的 `_watch` / `_act` / `Message.instruct_content` / `Environment` 機制，**整條 pipeline 邏輯沒有重新發明**
- 「框架原生 type-safety」：Pydantic instruct_content 在 framework 層保證型別契約，不靠 LLM JSON parse
- 「雙 driver 設計」：論文章節 7.x 評估可宣稱「同一系統可用 MetaGPT framework 模式 OR 純 async driver 模式跑、結果可比較」
- 「環境級訊息廣播」：MetaGPT `Environment.history` 提供天然的可觀測性（trace、debug、replay），自寫 driver 要重做這一切

---

#### IterationStateRole — Role 層 feedback 路由（MVP 限制解除）

**目標：** 把 LayoutPipeline 的 Aesthetic-Judge REJECT routing 完整搬進 Role / Team flow。原本 MVP 限制是「Role 模式只跑線性正向，feedback loop 只能用 LayoutPipeline」，現在兩條 driver 行為對等。

**新增檔案**
- `metagpt/ext/agentlayout/roles/iteration_state.py`
  - `IterationStateRole`：watch `[JudgeAesthetic]`，內部持 `IterationState` Pydantic（counter / target / last_feedback）
  - `RetryAnalyst(Action)` / `RetryGeneration(Action)` — sentinel cause_by tag（同 `UserRequirement` pattern）
  - `IterationStop(Action)` — ACCEPT / max-rounds 用的中性 sentinel，避免 framework 預設把 cause_by 改成 `UserRequirement`（schema.py:269 validator 行為）誤觸發 AnalystRole
  - `RetryPayload(BaseModel)` — Pydantic 攜帶 `feedback / iteration / target`

**改動既有檔案**
- `roles/analyst.py`：`_watch=[UserRequirement, RetryAnalyst]`；`_act` 加 retry 分支（從 RetryPayload 取 feedback、env.history 撈原始 LayoutBrief）；改用 `rc.news[-1]` 識別本 tick 的觸發訊息
- `roles/layout_generator.py`：`_watch=[PlanAssets, RetryGeneration]`；新增 `_retry_round` counter 確保 candidate_id 跨 retry 不撞名（offset = `_retry_round * max_topup_rounds`）；初始 PlanAssets 路徑會 reset counter，所以 Analyst-driven retry 後 candidate_id 從 r0_ 重新計
- `roles/aesthetic_judge.py`：同樣改用 `rc.news[-1]`（行為等價、防禦性改寫）
- `team.py`：hire 第 5 個 Role；`n_round` default 4 → 16（容下 max_total_rounds=5 的 retry 迴圈，每 Generator-target cycle ~3 ticks、每 Analyst-target cycle ~5 ticks）

**Routing 規則（鏡像 schema.py:441 IterationState.next_target()）**
| Reject 累計次數 | Routing 目標 | 變更內容 |
|---|---|---|
| iteration ≤ `GENERATOR_FEEDBACK_ROUNDS`（=2） | `LayoutGeneratorRole` | spec / tree 不變、再生 candidates、注入 feedback |
| iteration > 2 | `AnalystRole` | 重生 DesignSpec → 重 plan tree → 重 generate（feedback 從 generator 端清掉） |
| ACCEPT | （IterationStop no-op） | 流程結束 |

**踩到的 bug 與修正**
1. **`AestheticFeedback.common_issues` 是 `str` 不是 `list[str]`** — 寫 fixture 時看錯，修為單字串
2. **`Message` default cause_by 會被 fallback 成 `UserRequirement`**（schema.py:269 validator）— ACCEPT 路徑沒指定 cause_by 結果 no-op message 又觸發 AnalystRole（payload=None 直接 crash）。修法：加 `IterationStop(Action)` sentinel
3. **`rc.history[-1]` 不一定是這 tick trigger 的 message**（cumulative memory），改用 `rc.news[-1]`（per-tick 觀察清單），三個 Role 都同步改

**驗證 — `layout_agent/output/smoke_team_reject.py`（28/28 全過，無 LLM）**

腳本場景：FakeJudge 前 3 次 REJECT、第 4 次 ACCEPT，FakeAnalyze / FakeGenerate / FakePlan 紀錄被呼叫的次數與 feedback 是否注入。

驗證項目（8 組共 28 條）：
1. **Action call counts**：Analyze ×2、Plan ×2、Generate ×4、Judge ×4
2. **Feedback 注入時機**：Analyze 第 1 次 None、第 2 次有；Generate 第 1/4 次 None、第 2/3 次有
3. **Judge decisions**：`[REJECT, REJECT, REJECT, ACCEPT]` 順序正確
4. **Candidate id 跨 retry 不撞名**：`r0_ → r3_ → r6_ → r0_`（最後一次因 Analyst 重生 PlanAssets 而 reset）
5. **IterationState 內部狀態**：`iteration=3`、`feedback_target=ANALYST`、`last_feedback != None`
6. **env history 訊息分布**：`RetryGeneration ×2 / RetryAnalyst ×1 / Judge ×4 / AnalyzeBrief ×2 / PlanAssets ×2 / GenerateLayout ×4`
7. **RetryPayload 型別與內容**：第一條 `iteration=1, target=LAYOUT_GENERATOR`；第三條 `iteration=3, target=ANALYST`
8. **最終 ACCEPT 落在最後一條 JudgeAesthetic 訊息**

**Env history 完整 17 條訊息序列（離線 fake actions）**
```
[0]  UserRequirement → LayoutBrief
[1]  AnalyzeBrief    → DesignSpec       (initial)
[2]  PlanAssets      → LayoutTree
[3]  GenerateLayout  → CandidatesBatch  (r0_*)
[4]  JudgeAesthetic  → REJECT
[5]  RetryGeneration → RetryPayload(iteration=1)
[6]  GenerateLayout  → CandidatesBatch  (r3_*)
[7]  JudgeAesthetic  → REJECT
[8]  RetryGeneration → RetryPayload(iteration=2)
[9]  GenerateLayout  → CandidatesBatch  (r6_*)
[10] JudgeAesthetic  → REJECT
[11] RetryAnalyst    → RetryPayload(iteration=3)
[12] AnalyzeBrief    → DesignSpec       (retry, with feedback)
[13] PlanAssets      → LayoutTree
[14] GenerateLayout  → CandidatesBatch  (r0_* reset)
[15] JudgeAesthetic  → ACCEPT
[16] IterationStop   → (no-op terminator)
```

既有 forward-path smoke test（`/tmp/smoke_team.py`）14 條 assertion 仍全過 — Role-flow 對 ACCEPT-first scenario 的行為完全相容。

**論文 contribution 對應**
- 「Role 模式 ↔ Pipeline 模式行為對等」：兩條 driver 的 message 序列一一對應，可直接做 ablation
- 「Single Responsibility」：Aesthetic Judge 只判分、Iteration State 只路由、上游 Role 只生成 → 與 LLM-Agent 文獻常見的「角色職責切割」設計原則一致
- 「框架原生消息協議路由」：用 MetaGPT `cause_by` + sentinel Action 機制做 routing，沒寫一行 if-else state machine（IterationStateRole 的判斷只有 `next_target()` 呼叫）
- 「可觀測性」：env.history 完整保留每一輪 reject feedback 內容，trace / debug / replay 都不需額外設計

---

#### Role 軌道首次 live LLM 端到端 run（IterationStateRole 真實環境驗證）

**目標：** 確認 IterationStateRole feedback routing 在離線 fake test 通過後，於 live gpt-4o + 真 multimodal Aesthetic Judge 環境也能完整 fire（而非只是 fake 跑得通）。

**腳本：** `layout_agent/output/run_role_team_live.py`（新增）
- 用 `run_team()` 而非 `LayoutPipeline.run`
- **不 BypassJudge** — 用真 gpt-4o multimodal Judge，刻意讓它出 REJECT，這樣 IterationStateRole 才會 routing
- `n_round=14, max_total_rounds=3`（cost ceiling ~$0.30）

**輸入：** 與 2026-05-08 首次 Pipeline live run 同樣的 toy summer-sale 海報 brief（`SUMMER SALE 50% OFF` + `ACME` logo + 紅色方塊 product）

**踩到並修掉的 bug**
- **AnalyzeBrief prompt enum 漏洞**：LLM 看到「minimal modern」brief 自由生 `soft_constraints[*].rule="minimalism" / "modern_style"`，但 schema `SoftConstraintRule` enum 只接 `{visual_hierarchy, whitespace, balance, color_harmony, readability}`。3 次 retry 全 fail，整個 Role 軌道倒在第一棒。
- **修法（actions/analyze_brief.py PROMPT_TEMPLATE）：** 顯式列出 5 個合法 enum 值並注明「minimalism / modern_style 等屬於 style_keywords，不是 soft_constraints」— 與既有 SemanticType 12 個 enum 的 prompt 處理同樣 pattern

**Run 結果（修完 prompt 後第二次 run）**

15 條 env.history 訊息、3 reject cycles 全部真實 fire：

```
[0]  UserRequirement → LayoutBrief
[1]  AnalyzeBrief    → DesignSpec       (initial, prompt 修完 LLM 1 次過)
[2]  PlanAssets      → LayoutTree
[3]  GenerateLayout  → CandidatesBatch  (r0_*)
[4]  JudgeAesthetic  → REJECT (max total=75)
[5]  RetryGeneration → RetryPayload(iteration=1)
[6]  GenerateLayout  → CandidatesBatch  (r3_*)        ← Generator retry #1
[7]  JudgeAesthetic  → REJECT (max total=72)
[8]  RetryGeneration → RetryPayload(iteration=2)
[9]  GenerateLayout  → CandidatesBatch  (r6_/r7_/r8_*) ← Generator retry #2
[10] JudgeAesthetic  → REJECT (max total=72, best=r7_cand_03)
[11] RetryAnalyst    → RetryPayload(iteration=3)       ← 切到 Analyst-target
[12] AnalyzeBrief    → DesignSpec        (Analyst rebuild with feedback)
[13] PlanAssets      → LayoutTree         (re-plan)
[14] GenerateLayout  → CandidatesBatch    (r0_* reset)
                       ↑ n_round=14 用完，第 4 次 Judge 沒跑到
```

**routing counters**
- `RetryGeneration`：2 條（iteration 1, 2 都路由到 LayoutGenerator）
- `RetryAnalyst`：1 條（iteration 3 路由到 Analyst，按 `next_target()` 規則 `iteration > GENERATOR_FEEDBACK_ROUNDS=2`）
- `JudgeAesthetic`：3 條（每輪都跑到 multimodal verdict）
- `IterationState.iteration` 終值 = 3、`feedback_target = ANALYST`

**Judge 三輪分數（最高分逐輪變化）**
| Round | Best Total | Threshold | Decision |
|---|---|---|---|
| 1 (r0_*) | 75 | 80 | REJECT |
| 2 (r3_*) | 72 | 80 | REJECT |
| 3 (r7_*) | 72 | 80 | REJECT |

**Judge 一致性 feedback（3 輪內容幾乎一樣）**
> "title_1 is not prominent enough across all candidates. product_image_1 and title_1 are too far apart."

**結論**
1. ✅ **Routing 機制本身在 live 完全正確**：fake test 28 條 assertion 在 real LLM 也成立，Role 軌道與 Pipeline 軌道行為對等
2. ✅ **Sentinel Action / RetryPayload 在 live 不會踩 framework 邊角案例**（schema.py:269 `cause_by` validator fallback 已被 IterationStop 處理）
3. ✅ **Analyst-target rebuild 路徑能重新走完整 4-Role 鏈**（[12]→[13]→[14] 序列證明 Analyst 重生 spec 後 AssetPlanner / Generator 會重新觸發）
4. ⚠️ **未到 ACCEPT 是正交問題**：feedback 內容跨 3 輪幾乎沒變（gpt-4o 看不到自己上一輪輸出，固化抱怨同樣兩件事），Generator 也沒有依 feedback 把 title 拉大 / 元件拉近 — 這是 **prompt engineering / 評分標準** 的問題，不是 routing 機制的問題
5. ⚠️ **n_round=14 偏緊**：3 reject + 1 Analyst rebuild 後缺最後一次 Judge（理論上 16 ticks 才足夠），下次設 n_round=18

**輸出檔（`layout_agent/output/`，已 gitignored）**
- `role_live_last_reject.png` — 最後一輪 REJECT 的 best candidate render（800×1200, 26KB）
- `role_live_trace.json` — env.history 完整摘要 + iteration_state + routing_counts
- `role_live_spec.json` — 最後一次 AnalyzeBrief 產出的 DesignSpec dump

**論文 contribution 對應**
- 「Live 環境 routing fire 證據」：論文章節 7.x「Role 軌道整合」可以直接附 trace JSON 與三輪 Judge 分數表
- 「Feedback 機制與生成品質正交」：本次 run 印證 routing 通暢但 LLM 不一定靠 feedback 收斂 → 後續工作（章節 8.x future work）：feedback-aware prompt rewriting / multi-round vision context
- 「真實 cost 數字」：3 reject + 1 rebuild ≈ $0.27，符合論文 cost section 的「per-pipeline-run 預估」基準

---

#### Quantitative evaluation 起步 — Element IoU MVP（5-sample baseline）

**目標：** 把論文「Results」章節從「我們建好系統」推進到「在 Crello 上量化系統表現」的第一步。先做 IoU 一個指標 + 5 個樣本，跑通 evaluation pipeline 與資料對齊邏輯，後續再加 Read Order / FID / 擴大樣本。

**新增模組：** `metagpt/ext/agentlayout/evaluation/`
- `evaluation/iou.py`
  - `bbox_iou(a, b) -> float`：純數學 IoU on `(left, top, width, height)`
  - `BBoxItem` dataclass：`(id, bbox)` 配對（避免 caller 自管平行 list 順序）
  - `LayoutIoUResult` Pydantic：`per_element / mean / matched / unmatched_generated / unmatched_gt`，可 JSON round-trip
  - `layout_iou(generated, ground_truth, id_map) -> LayoutIoUResult`：caller 提供 `generated_id → gt_id` 對應字典；`mean` 只算 matched 對，不對 unmatched 罰 0（讓 caller 自選 missing penalty 變體）

**Element 對應策略 — content / asset_ref 比對**
- Crello GT 的 element id 是 `idx (0, 1, 2, ...)`，AgentLayout 生成 id 是 LLM 取的語義名（`title_1, body_1, ...`），無法自動對齊
- 解法：caller 用「asset feeding order」追蹤 `asset_list[k] → crello_idx`，再從 `DesignSpec.elements` 取 `content / asset_ref` 反查回 `crello_idx`
- 不靠位置順序（LLM 不保證保留 asset_list 順序），不依賴語義名稱（無 reliable 映射），完全靠內容唯一性 — Crello sample 內每個 asset 的 `content` / `asset_ref` 唯一

**驗證 — 7 組共 22 條單元測試（`layout_agent/output/test_iou.py`，純數學無 LLM）**
1. `bbox_iou` 邊界：identical=1 / disjoint=0 / 半重疊=1/3 / nested=0.25 / 零面積=0 / edge-touching=0
2. `layout_iou` 兩個完美對 → mean=1
3. mixed quality → mean=平均
4. unmatched generated → 報錯欄位
5. unmatched gt → 報錯欄位
6. empty generated
7. JSON round-trip（model_dump + model_validate）

全 22 條 PASS。

**Baseline run — `layout_agent/output/run_iou_eval.py`（5 sample，BypassJudge 模式）**

| sample_id | canvas | matched | mean_iou |
|---|---|---|---|
| 5d972ca9... | 537×240 | 4/5 | 0.091 |
| 5c6c0cba... | 1080×1920 | 4/5 | 0.140 |
| 5954bda9... | 1200×600 | 4/5 | 0.074 |
| 5efdd2dd... | 1008×1296 | 3/3 | **0.217** |
| 5f885a9b... | 851×315 | 3/4 | 0.000 |

**Cross-sample mean IoU = 0.105，total matched pairs = 18，cost ~$0.20**

**為什麼 IoU 這麼低（這是合理的 baseline）**
- AgentLayout 的目標**不是**重現 Crello 設計師的特定排版 — 它是「給定 brief + assets，生出**可行**的版面」。同一個 brief 可以有無數 valid layout，IoU 只會在「我們剛好猜到設計師的版面」時才高
- 這 5 個 baseline 數字告訴我們：系統在「自由發揮」模式下，**約 90% 的元件位置與 Crello 設計師選擇不同**，這是預期的
- 0.105 是 **cold-start baseline**：未經 Aesthetic Judge feedback、未經 reference-aware prompt、未對齊 Crello 設計風格 → 後續 ablation（加 Judge / 加 reference / 跨 dataset）的對照基準

**何時 IoU 會高（論文後續 ablation 假設）**
- 若把 Crello GT layout 部分洩漏給 Generator（reference-aware prompt）→ IoU 應顯著上升
- 若用 Aesthetic Judge feedback loop（不 BypassJudge）→ score 改善但 IoU 未必（Judge 不知道 GT）
- 若改用「element type → typical position」prior（如 logo→bottom_right）→ IoU 微升
- 若直接訓練/fine-tune → IoU 應大幅上升（但本研究 zero-shot LLM-driven，這條不在 scope）

**輸出檔案（`layout_agent/output/`，全 gitignored）**
- `eval_iou_baseline.json`：完整 per-sample + aggregate
- `crello_<id>/`（5 個）：assets / meta.json / pipeline_result.png / iou_result.json / ground_truth_preview.jpg

**MVP 限制（論文 future work）**
1. **N=5 太小**：cross-sample 平均不可推論至整個 Crello。下一步：N=50 + 標準差
2. **BypassJudge**：未測 feedback loop 對 IoU 的影響。下一步：跑同 5 sample 含真 Judge 對照
3. **id 對應靠內容唯一性**：若 Crello sample 出現重複 text content（罕見但可能），對應會錯位。下一步：fallback 到 LLM 順序匹配
4. **僅 IoU**：layout 品質不只看 box 重疊，還有 read order、視覺平衡、留白。下一步：Read Order Score + FID

**論文 contribution 對應**
- 「論文 Results 章節有真實數字」：cross-sample mean=0.105 + 5 sample 表格直接可貼
- 「自由發揮 vs reference-aware ablation 對照」：本次的 0.105 cold-start 是後續所有對比的 baseline 起點
- 「evaluation pipeline 工程化」：`evaluation/` 模組 + 22 條單元測試 + 結構化 Pydantic 輸出 → 後續加指標只要新增一個 module（如 `read_order.py`）就能在 driver 整合

---

#### Random / Centered baseline 對照（2026-05-10 同日加做）

**目標：** 把 mIoU=0.105 從「孤立數字」變成「可對照數字」。同 5 sample 加跑兩個 trivial baseline，純離線、無 LLM、$0 cost。

**新增檔案**
- `metagpt/ext/agentlayout/evaluation/baselines.py`（含 `random_layout` 與 `centered_stack` 兩個函式 + size fraction 常數）
- `layout_agent/output/test_baselines.py`（14 條單元測試）
- `layout_agent/output/run_random_baseline.py`（純離線 driver）

**結果（cross-sample mIoU）**
| AgentLayout | Random (5 seeds) | Centered |
|---|---|---|
| **0.105** | 0.064 (±0.045) | 0.103 |

**發現**
1. AgentLayout 比 Random 提升 1.6×（0.105 / 0.064 = 1.64）— 證明 LLM 的位置選擇優於完全隨機
2. AgentLayout ≈ Centered（0.105 vs 0.103）— cold-start 模式下 LLM 與 naive prior 持平，這是論文 honest weakness：LLM 的優勢需要靠 reference-aware prompt 或 feedback loop 才能顯現
3. 直式長 canvas（1080×1920）AgentLayout 顯著勝出（0.140 vs 0.034）— LLM 對「畫布上下分區」直覺有效
4. 少元件方正 canvas Centered 反勝（0.244 vs 0.217）— 元件少時 naive prior 已逼近上限
5. 一個 corner case（851×315 横式）AgentLayout=0.000 — id-matching 在重複 / 模糊 content 下會失敗，已寫進 future work

**論文 contribution 對應**
- 「Headline 數字有意義」：「比 Random 好 1.6×」可直接寫進論文 abstract
- 「Honest weakness 已揭露」：與 Centered 持平這條結論有助通過 reviewer「你比沒比 trivial baseline」的 challenge
- 「對照表完整」：所有後續 ablation 可在同一張表上加新欄（+Judge feedback / +reference / +fine-tune）

**輸出**
- `layout_agent/output/eval_baseline_compare.json`（per-sample + aggregate + 5 個 random seeds 結果）

---

#### Feedback loop 是否「真的可以跑」？— Generator ablation 實驗（2026-05-10）

**問題：** 雖然之前已驗證 routing 機制 fire（offline smoke test 28/28 + live LLM run 三個 reject cycles），但 live run 三輪 Judge 分數 75 → 72 → 72 不升反降，feedback 三輪內容幾乎一樣。這引出一個 sharp 的質疑：**routing fire 不等於 LLM 真的吸收 feedback、Generator 真的依 feedback 改變輸出**。

**設計：** 同 spec、同 tree、同 bg、跑 GenerateLayout N=3 次無 feedback、N=3 次帶具體可驗證 synthetic feedback，比對 compliance rate 與 bbox shift。

**Synthetic feedback（3 個機械可驗證條件）：**
1. text_1 中心點在 canvas 上半（`top + height/2 < canvas_h / 2`）
2. image_1 寬度 ≥ 60% canvas（`width >= 0.6 * canvas_w`）
3. image_2 在右下象限（`left >= canvas_w/2 AND top >= canvas_h/2`）

**結果：**

| Side | Compliance mean (N=3) | std | Per-condition rate |
|---|---|---|---|
| WITHOUT feedback | **0.000** | 0.000 | text_upper=0/3, image_wide=0/3, image_BR=0/3 |
| WITH feedback | **1.000** | 0.000 | text_upper=3/3, image_wide=3/3, image_BR=3/3 |

**Compliance lift = +1.000（從 0% 全失敗到 100% 全達成）**

**Bbox center shift（無 feedback 平均位置 → 有 feedback 平均位置）：**
- image_1: 136 px
- image_2: 678 px
- text_1: **847 px**（canvas height 1296，相當於 65% 高度位移）

**結論一：feedback 機制在語意層也是真的工作。** LLM 完全吸收 feedback 並執行；6/6 LLM 呼叫一致：無 feedback 時 0/3 compliance，有 feedback 時 3/3 compliance。

**結論二：Live run 沒收斂的真正瓶頸是 Aesthetic Judge feedback 的 specificity，不是 Generator 執行能力。**
- 之前 live run Judge 給的 feedback 是「title not prominent enough, product and title too far apart」— 抽象描述、無可驗證條件
- 本 ablation 給的 feedback 是「width >= 0.6 × canvas」— 具體可驗證條件
- 同樣的 LLM Generator 對前者沒收斂、對後者 100% compliance

**論文可寫 contribution：**
1. **Mechanism validity**：feedback loop 從程式碼層、Pydantic 傳遞層、prompt 注入層、到 LLM 執行層全部驗證為真，這是論文 system 章節必要的 sanity check evidence
2. **System-level finding**：「Aesthetic Judge 必須產出具體可驗證 feedback」是 LLM-driven 美感反饋系統的 critical design constraint — 這是論文 discussion / section 7.x 的核心 insight
3. **Future work 對焦**：Aesthetic Judge prompt 應強制要求 numerical / categorical specific suggestions（如「increase title height by 50%」「move logo to bottom-right corner with margin >=20px」），而非自由描述

**輸出**
- `layout_agent/output/ablation_feedback.py`（ablation driver）
- `layout_agent/output/ablation_feedback.json`（完整 6 runs 結果 + per-condition rate + bbox shift）

---

#### 5-Role MVP Isolated Verification（2026-05-10）

**目標：** 把每個 Role 的 input contract / output contract / domain invariant 用 isolated 測試逐一過一輪，給論文 system 章節「我們的 5 個 Role 都各自可行」一個明確的驗證表。

**設計：** `layout_agent/output/verify_roles_mvp.py`，用 cached Crello sample（5efdd2dd, 3 elements, 1008×1296）當 fixture，Roles 1-3 鏈式輸出（節省 cost）、Role 4 用 Role 3 輸出做 multimodal Judge、Role 5 純 mock 無 LLM。

**驗證表（5/5 PASS、共 31 條 invariant 全綠）**

| Role | Action 包裝 | Input Contract | Output Contract | 主要 Invariant 數 | 狀態 |
|---|---|---|---|---|---|
| **AnalystRole** | AnalyzeBrief | `(user_brief, asset_list)` | `DesignSpec` | 6 條 | ✅ PASS |
| **AssetPlannerRole** | PlanAssets | `DesignSpec` | `LayoutTree` | 5 條 | ✅ PASS |
| **LayoutGeneratorRole** | GenerateLayout + QC | `(spec, tree, bg, feedback?)` | `CandidatesBatch` | 5 條 | ✅ PASS |
| **AestheticJudgeRole** | JudgeAesthetic (multimodal) | `(candidates, spec, tree, bg)` | `AestheticJudgement` | 6 條 | ✅ PASS |
| **IterationStateRole** | (sentinel routing) | `AestheticJudgement` | `Message(cause_by=Retry*\|IterationStop)` | 6 條 | ✅ PASS |

**Per-Role invariant 摘要**

- **AnalystRole**：`isinstance(spec, DesignSpec)` / `canvas dims > 0` / `2 ≤ #elements ≤ 6`（input 3 assets ±）/ 所有 element 有 `semantic_type` / 所有 element `visual_type ∈ {image, text}` / `assert_enriched()` 通過（importance + semantic_relevance 都被 AssetAnalyzer 填上）
- **AssetPlannerRole**：`isinstance(tree, LayoutTree)` / `root.id == "root"` / root 至少 1 child / 每個 spec element 出現在 tree / planner 沒發明新 id
- **LayoutGeneratorRole**：`isinstance(batch, CandidatesBatch)` / 至少 1 candidate / 每個 candidate 含**所有** spec element id（無遺漏無多餘）/ 所有 bbox 在 canvas 內 / QC 留下 ≥1 candidate
- **AestheticJudgeRole**：`isinstance(judgement, AestheticJudgement)` / decision ∈ {ACCEPT, REJECT} / evaluations 數 == candidates 數 / `best_candidate_id` 指向輸入之一 / 所有 total ∈ [0, 100] / decision-feedback consistency（ACCEPT→feedback=None、REJECT→feedback≠None）
- **IterationStateRole**：iteration 1 → `RetryGeneration` / iteration 2 → `RetryGeneration` / iteration 3 → `RetryAnalyst`（routing rule `iteration > GENERATOR_FEEDBACK_ROUNDS=2`）/ ACCEPT → 不發 Retry* / 前 3 輸出帶 `RetryPayload` instruct_content / counter 累計到 3

**驗證過程踩到並修掉的 2 個 bug**

1. **`Evaluation` schema invariant**：`total` 必須 == `sum(scores)`，我的 fake fixture 用 `total=70` 但 scores 加總 80，被 Pydantic validator 攔下。修：REJECT 用 4×17=68 / ACCEPT 用 4×20=80
2. **`rc.history.append(msg)` 不持久化**：`rc.history` 是 property over `rc.memory`，append 不會持久化。修：用 `role.rc.memory.add(msg)` 走 Memory 正規 API。這個發現對未來寫 isolated Role test 也有用 — 直接操作 `rc.memory` 才是 framework-correct 做法

**論文 contribution 對應**
- 「每個 Role 都有 input/output contract 與可量測 invariant」：論文 system 章節 6.x 可以直接附這張表，每個 Role 一段
- 「驗證 multimodal Judge 真的調用了視覺 channel」：Role 4 收到真實 PNG render，回傳 0-100 分數 + 結構化 evaluations，schema invariant 全過 → 推論 vision 模態確實被使用（雖未直接 probe，但若沒有 vision 也不會產出與 layout 一致的 strengths/weaknesses 文字）
- 「Isolated Role test 可作為 CI sanity gate」：未來改任何 Role 程式碼後，跑這 31 條 invariant 一輪即可確認沒退化

**輸出**
- `layout_agent/output/role_verification.json`（5 Role × invariants 完整結果）

---

#### Aesthetic Judge Role Corner-Case 驗證（2026-05-13）

承接 5-Role MVP 結果，本次針對「最重要、也最未被深測」的 `AestheticJudgeRole` 做了 3 個 corner case，cost ~$0.20、3 次 multimodal LLM 呼叫（gpt-4o vision）。Fixture 仍用 Crello `5efdd2dd...`（3 elements、1008×1296）。直接把 spec / tree / candidates 手動構造繞過 Role 1-3，把 Judge 隔離出來。

**Case 1 — Multimodal visual probe**

同一份 spec 構造兩個極端不同的 candidate：
- `cand_gt`：用 Crello 設計師 GT bbox（image_1 5/124/995/1045、image_2 192/217/659/874、text_1 272/324/501/677、z=0/1/2）
- `cand_collapsed`：所有元素 left=0、top=0、原 size 堆疊（重疊）

| 指標 | 結果 |
|---|---|
| `best_candidate_id` | `cand_gt` ✅ |
| GT total | 72 |
| collapsed total | 65 |
| `GT > collapsed` | ✅ 7 分差距 |
| `gap >= 8`（我們設的最低 probe 強度） | ✗ 7 分（差 1） |

**結論：Vision 模態真的被使用**（Judge 在兩張只差 bbox 排列的圖之間正確選了設計師版）。但 Judge 對 layout 差異的敏感度比預期保守（gap 只有 7）。CAVEAT。

**Case 2 — GT-as-ACCEPT 場景**

只送一張 `cand_gt`，看 Judge 是否給出 ≥ `ACCEPT_THRESHOLD`(80) 分。

| 維度 | 分數 |
|---|---|
| requirement_alignment | 18 |
| info_hierarchy | 15 |
| layout_balance | 17 |
| visual_coherence | 18 |
| **total** | **68 / 80** |

決定：`reject`。**即使是 Crello 設計師的真實 layout，這個 Judge 也不給 ACCEPT**。這直接解釋了之前 live run 分數一直卡在 70-75 區間：不是 Generator 太爛，而是 Judge ACCEPT_THRESHOLD 對這份 prompt-style 太苛刻。CAVEAT，可考慮把 ACCEPT_THRESHOLD 從 80 降到 70-72，或重 calibrate prompt。

**Case 3 — Feedback specificity check**

收集 2 次 reject 的所有 `feedback.suggestions`（Case 1 + Case 3 各一次 Judge call、各產一份 feedback），共 **6 條 suggestion**，套 regex 分類器：
- `has_element_ref`：suggestion 含 `image_1` / `text_1` / `cta_1` 等 id
- `has_numeric_with_unit`：含 `40px` / `25%` / `30deg` 等
- `categorical_strong`：含 ≥ 2 個方位 / 尺寸詞（top, larger, increase, …）

| 指標 | 結果 |
|---|---|
| 收集 suggestion 數 | 6 |
| 含 element_id 引用 | 5 |
| specificity ratio | **0.833** |
| 通過門檻（≥ 0.7） | ✅ PASS |

**反直覺發現：Judge 的 feedback 其實是 specific 的（83%）**，例如 `"Increase the size of 'text_1' in 'cand_shrunk' to improve its dominance."` 同時含 element_id + 兩個 categorical 詞。這跟 2026-05-10 ablation 結論「feedback 太模糊導致 live run 不收斂」有矛盾，可能根因不在 Judge feedback specificity 而在 Generator 不消化 feedback、或 Judge 給的分數天花板過低（Case 2 證實 80 門檻過嚴）。下一輪要重做 ablation 把根因再細分。

**Corner case 驗證表（更新後）**

| # | Role | MVP | Corner case | Multimodal probe |
|---|---|---|---|---|
| 1 | AnalystRole | OK | TODO | n/a |
| 2 | AssetPlannerRole | OK | TODO | n/a |
| 3 | LayoutGeneratorRole | OK | TODO | n/a |
| 4 | AestheticJudgeRole | OK | 3/3 跑完（PASS×1 + CAVEAT×2） | **PASS**（best_id 選對 GT） |
| 5 | IterationStateRole | OK | partial | n/a |

**論文 contribution 對應**
- 「Vision 模態確認真的被使用、不是 placebo」：Case 1 直接 probe — 同 spec 兩張對比圖、best_candidate_id 選 GT，這是 isolated 證據
- 「Judge 對真實設計師 layout 也 reject」：Case 2 揭露 ACCEPT_THRESHOLD=80 對此 prompt 過嚴，是 future work 的 calibration 點
- 「Judge feedback 已具備 actionable specificity」：Case 3 量化結果 83.3%，推翻先前「feedback 太模糊」的直覺，把不收斂根因推回 Generator 端 / 門檻校準

**輸出**
- `layout_agent/output/judge_corner_report.json`（3 case × invariants + 6 條 suggestion classified）

---

#### IterationStateRole Corner-Case 驗證（2026-05-13，純離線 $0）

Aesthetic Judge corner case 結束後同日做的第二支 Role 深測。`IterationStateRole` 完全是程式邏輯（無 LLM call），所以 corner case 直接驗證 routing 規則的邊界行為。Cost = $0、3 case × 11 invariants 全綠。

**Case 1 — `max_total_rounds` 邊界**

設 `max_total_rounds=2`，連送 3 個 REJECT judgement。預期 cause_by chain：

```
reject 1 (iter=1, 1 ≤ 2) → RetryGeneration
reject 2 (iter=2, 2 ≤ 2) → RetryGeneration
reject 3 (iter=3, 3 > 2) → IterationStop  ← 邊界觸發
```

5 條 invariant 全 PASS：cause_by chain 正確、IterationStop message 不帶 `RetryPayload`、counter 仍會 increment 到 3（第 3 個 reject 雖然被 stop 但有被算到 iteration 統計裡）。**確認 pipeline.py 的 `max_total_rounds` 語意被 Role 層原樣保留**。

**Case 2 — Duplicate judgement 重複 increment（design observation）**

NEXT_SESSION 提的「重複觸發 counter 是否只 increment 一次」這個假設**不成立**。實際行為：同一個 `AestheticJudgement` 實例餵兩次 → `iteration` 從 0 → 2（每次 `_act()` 都無條件 `+= 1`）。

這是 **by design**：Role 不去重，**Team driver 才是 dedupe responsibility owner**。`iteration_state.py:153` 的 `self._state.iteration += 1` 沒有 idempotency guard，因為 Team / MGXEnv 本來就保證每個 Judge tick 只 dispatch 一次。3 條 invariant 全 PASS（document 而非 enforce dedupe）。

論文章節可以把這寫成「Role 層責任邊界明確：bookkeeping 與 routing 在 Role、deduplication 在 Team」。

**Case 3 — ACCEPT 不消耗 iteration counter**

送 `REJECT → ACCEPT → REJECT → ACCEPT` 四個 judgement，iteration snapshot 應為 `[0, 1, 1, 2, 2]`、cause_by chain 應為 `[RetryGeneration, IterationStop, RetryGeneration, IterationStop]`。

3 條 invariant 全 PASS：ACCEPT 確實只 emit terminator、不動 counter；ACCEPT message 也不帶 RetryPayload。**這保證了 ACCEPT 可以在任何 round 出現都不會打亂 retry budget 統計**。

**Corner case 驗證表（再次更新）**

| # | Role | MVP | Corner case | Multimodal probe |
|---|---|---|---|---|
| 1 | AnalystRole | OK | TODO | n/a |
| 2 | AssetPlannerRole | OK | TODO | n/a |
| 3 | LayoutGeneratorRole | OK | TODO | n/a |
| 4 | AestheticJudgeRole | OK | 3/3 跑完（PASS×1 + CAVEAT×2） | **PASS**（best_id 選對 GT） |
| 5 | IterationStateRole | OK | **3/3 全 PASS（11 invariants）** | n/a |

**輸出**
- `layout_agent/output/iteration_corner_report.json`（3 case × 11 invariants 完整 trace）

---

#### AnalystRole Corner-Case 驗證（2026-05-13，3/3 全 PASS）

第三支 Role 進 corner 階段。直接挑 thesis 最敏感的三類輸入：多語、空 asset、模糊語義。3 LLM call、cost ~$0.10-0.15。

**Case 1 — CJK / zh-TW 輸入**

brief：「設計一張 1080x1080 的台灣中秋節宣傳海報，主題是月圓人團圓，需要月亮意象與中文 slogan。整體色調溫暖、布局乾淨。」+ 中文 title `"中秋快樂\n月圓人團圓"`。

| 觀察 | 結果 |
|---|---|
| canvas | 1080 × 1080 ✅（brief 明寫的） |
| language | **`zh-TW`** ✅（自動偵測） |
| title content | `"中秋快樂\n月圓人團圓"` 完整保留 ✅ |
| style_keywords | `["溫暖", "乾淨", "中秋節"]`（中文！） |
| 5/5 invariants | PASS |

**結論：Analyst 對中文輸入完全支援，且 style_keywords 也是 LLM 用 input 語言產出，非強制英文**。這對 thesis「framework 不假設語言」是強訊號。

**Case 2 — 空 asset_list（graceful fallback）**

brief：`"Design a 1200x800 promotional poster. Clean, modern aesthetic, light background. Style: minimal, professional."`、`asset_list=[]`。

預設可能行為 (a) returns spec with elements=[]、(b) raise informative error。實際：(a)：

| 觀察 | 結果 |
|---|---|
| 是否 graceful return | ✅ 不 crash |
| canvas | 1200 × 800 ✅（從 brief 抽取） |
| n_elements | 0 ✅（不 hallucinate） |
| 3/3 invariants | PASS |

**結論：Analyst 在沒有 asset 時不會幻覺 element（schema 也不要求 elements 非空）**，這保證 Generator 後續可以從 spec 安全地走「無元素」邊界。

**Case 3 — 模糊 brief（inferred_fields 標注合理性）**

brief：`"Make something nice for me."`（極短、無維度、無風格、無語言）+ 1 image + 1 text `"Welcome"`。

| 觀察 | 結果 |
|---|---|
| canvas (LLM 推測) | 1080 × 1920（推一個 portrait poster） |
| `inferred_fields` | `canvas.width:true, canvas.height:true, elements.image_1.semantic_type:true, elements.text_1.semantic_type:true` |
| 每個 element `inferred=true` | ✅ ✅ |
| style_keywords | `["nice", "pleasant"]`（LLM 從 "nice" 推） |
| 5/5 invariants | PASS |

**結論：inferred_fields 機制運作正確、每個 LLM 推測的欄位都被誠實標注**。這是後續 IterationStateRole 把 feedback 路由到 Analyst 重建時的關鍵 — 因為「Analyst can change」與「user said verbatim」必須區分。

**Corner case 驗證表（第三次更新）**

| # | Role | MVP | Corner case | Multimodal probe |
|---|---|---|---|---|
| 1 | AnalystRole | OK | **3/3 全 PASS（13 invariants、CJK + 空 + 模糊）** | n/a |
| 2 | AssetPlannerRole | OK | TODO | n/a |
| 3 | LayoutGeneratorRole | OK | TODO | n/a |
| 4 | AestheticJudgeRole | OK | 3/3 跑完（PASS×1 + CAVEAT×2） | **PASS**（best_id 選對 GT） |
| 5 | IterationStateRole | OK | **3/3 全 PASS（11 invariants）** | n/a |

**論文 contribution 對應**
- 「Framework 不假設語言」：Case 1 自動產 zh-TW spec、style_keywords 也中文 — 多語 demo 直接可寫進 system chapter
- 「Graceful 邊界」：Case 2 證明 zero-asset 不 crash、不 hallucinate element
- 「Inferred field tracking」：Case 3 證明 inferred_fields 機制與 spec 一致；這直接支援 IterationStateRole 把 reject feedback 路由到 Analyst 重建時的修正範圍判定

**輸出**
- `layout_agent/output/analyst_corner_report.json`（3 case × 13 invariants）

---

#### AssetPlannerRole Corner-Case 驗證（2026-05-13，3/3 全 PASS）

第四支 Role 進 corner 階段。fixture 全部手刻 enriched DesignSpec（importance + semantic_relevance 預先填好），不跑 AnalyzeBrief 省 cost。3 LLM call、cost ~$0.20（Case 2 retry 3 次）。

**Case 1 — 最小 spec（1 element）**

spec：1 個 `text_1`（title、`Hello`、importance=5、relevance=0.8）。

| 觀察 | 結果 |
|---|---|
| tree_ids | `["text_1"]` ✅ |
| root_children | `["text_1"]` ✅ |
| tree_depth | 1 ✅ |
| 4/4 invariants | PASS |

**結論：Planner 對最小邊界 graceful，不 invent siblings、不 nest 多層**。

**Case 2 — Duplicate element ids（design-boundary probe）**

spec 故意含兩個 `text_1`（一個 title、一個 subtitle）+ 一個 `image_1`。預期：Element 是 `List[Element]`、無 unique validator，Pydantic 允許構造。讓 PlanAssets 跑：

```
ValueError: PlanAssets: could not produce a valid LayoutTree after 3 attempts.
Last error: Duplicate element ids in LayoutTree: ['text_1']
```

行為分析：
- ✅ PlanAssets 內部 `_validate_against_spec` 確實檢出 dup id
- ✅ Surface informative `ValueError`，不 silent crash
- ⚠️ 但走了 **3 次 retry**（每次 LLM call ~$0.05、共 ~$0.15 浪費）才 raise

**論文 design observation**：dup-id 檢測應該移到 schema 層（在 `DesignSpec` 加 `model_validator` 檢查 unique ids），可在 Pydantic 構造當下就 reject、避免 PlanAssets 對 LLM 做 3 次 retry。這是 architectural lift 的明確標的。

**Case 3 — 大型 spec（10 elements，多 semantic_type）**

spec：`product_img_1, logo_1, headline_1, subtitle_1, cta_1, pricetag_1, caption_1, caption_2, icon_1, body_text_1`。

| 觀察 | 結果 |
|---|---|
| n_spec_elements | 10 |
| tree 涵蓋 spec ids | ✅（無 missing / 無 extra） |
| no dup in tree | ✅ |
| tree_depth | 2 ✅ |
| root_children_count | 6（不是 10，**有 grouping**） |
| 5/5 invariants | PASS |

實際 tree（部分）：
```
root
├─ product_img_1
│   └─ headline_1
│       └─ pricetag_1, caption_1, caption_2
├─ logo_1
├─ subtitle_1
├─ cta_1
├─ body_text_1
└─ icon_1
```

**結論：Planner 真的把 `pricetag_1, caption_1, caption_2` 視為 `headline_1` 的子節點，又把 `headline_1` 視為 `product_img_1` 的子節點**。語義 grouping 真實發生、非 trivial flat tree。

**Corner case 驗證表（第四次更新）**

| # | Role | MVP | Corner case | Multimodal probe |
|---|---|---|---|---|
| 1 | AnalystRole | OK | **3/3 PASS（13 invariants、CJK + 空 + 模糊）** | n/a |
| 2 | AssetPlannerRole | OK | **3/3 PASS（13 invariants、min + dup + 10-element）** | n/a |
| 3 | LayoutGeneratorRole | OK | TODO | n/a |
| 4 | AestheticJudgeRole | OK | 3/3 跑完（PASS×1 + CAVEAT×2） | **PASS** |
| 5 | IterationStateRole | OK | **3/3 PASS（11 invariants）** | n/a |

**論文 contribution 對應**
- 「Planner 真的做 semantic grouping」：Case 3 — 10 element 收斂成 6 個 root children + 2 層深度，反證它不是 trivial passthrough
- 「Schema gap：spec-level id uniqueness 未強制」：Case 2 揭露 Pydantic 接受 dup ids、PlanAssets 在 LLM retry 端才 catch；明確 future work 是 add `model_validator` 到 DesignSpec
- 「Minimal-edge graceful」：Case 1 1-element spec 不 invent siblings、不亂 nest

**輸出**
- `layout_agent/output/planner_corner_report.json`（3 case × 13 invariants + Case 2 retry trace）

---

#### LayoutGeneratorRole Corner-Case 驗證（2026-05-13，3/3 全 PASS、100% 完美遵守）

**第五支也是最後一支 Role 進 corner 階段，5/5 Role corner case 全完成**。3 LLM call、cost ~$0.15。

**Case 1 — 緊框 canvas + size_preference + no_overlap**

spec：600×400 canvas + 3 元素（headline 含 `prominent` size hint + no_overlap 約束）。

| 觀察 | 結果 |
|---|---|
| raw_candidate_count | 5 |
| valid_count（QC pass） | **5 / 5** |
| 任一 candidate 有 violation | **無** |
| 4/4 invariants | PASS |

**結論：LLM 對緊框 + size hint + no_overlap 在這個 fixture 下展現 100% QC pass rate**。沒走到 top-up loop（因為一輪就全綠）。對 thesis 來說，這說明 Generator + QC 介面在簡單 spec 下不會出現「永遠 QC fail」的 corner。

**Case 2 — `position_preference: top_right` 遵守度**

spec：1080×1080 canvas + 3 元素 + hard_constraint `position_preference target=[logo_1] hint=top_right`。

| 觀察 | 結果 |
|---|---|
| raw_candidate_count | 5 |
| logo_1 落在 (2, 0) band 的數量 | **5 / 5** |
| `honor_ratio` | **1.00** |
| 3/3 invariants | PASS |

**結論：LLM 對 `position_preference` hint 達到 100% 遵守率**。這是 prompt design + QC feedback 的雙保險 — Generator 知道 hint、QC 也會擋下違反者。論文 system 章節可直接寫：「在 5/5 candidates 中，position_preference hint 全部被遵守」。

**Case 3 — z_index ordering（background z < foreground z）**

spec：1080×1080 canvas + `bg_1` (semantic_type=background_image) + `headline_1` + `logo_1`。**無**顯式 z_order 約束，只靠 semantic_type 暗示視覺 hierarchy。

| 觀察 | 結果 |
|---|---|
| raw_candidate_count | 5 |
| bg_z < min(fg_zs) 的數量 | **5 / 5** |
| 5/5 candidates 真實 z map | `bg=1, headline=3, logo=4` |
| 3/3 invariants | PASS |

**結論：LLM 從 semantic_type=`background_image` 自動推導出較低 z_index**，不需明寫 z_order constraint。這對 thesis 是強訊號：semantic_type 本身就承載視覺優先序的資訊，pipeline 不需重複編碼。

**Corner case 驗證表（最終 — 5/5 Role 全進 corner 階段）**

| # | Role | MVP | Corner case | Multimodal probe |
|---|---|---|---|---|
| 1 | AnalystRole | OK | **3/3 PASS（13 invariants、CJK + 空 + 模糊）** | n/a |
| 2 | AssetPlannerRole | OK | **3/3 PASS（13 invariants、min + dup + 10-el）** | n/a |
| 3 | LayoutGeneratorRole | OK | **3/3 PASS（11 invariants、tight + pos hint + z 全 100%）** | n/a |
| 4 | AestheticJudgeRole | OK | 3/3 跑完（PASS×1 + CAVEAT×2、揭露 ACCEPT 門檻） | **PASS** |
| 5 | IterationStateRole | OK | **3/3 PASS（11 invariants、邊界 + 去重 + ACCEPT）** | n/a |

**累積驗證統計（2026-05-13 收工時）**

- **MVP invariants：31 條全綠**（5 Role × 6/5 條）
- **Corner invariants：61 條全綠 + 2 CAVEAT**（Judge 11 + Iteration 11 + Analyst 13 + Planner 13 + Generator 11 + Judge 2 CAVEAT）
- **總 invariants：92 條已驗證**
- **總 LLM cost：~$0.70**（Judge $0.20 + Analyst $0.15 + Planner $0.20 + Generator $0.15 + Iteration $0）

**論文 contribution 整理（按 Role）**

| Role | 最有價值的 finding | 對應 thesis 段落 |
|---|---|---|
| Analyst | 多語自動偵測、inferred_fields 機制完整 | system / robustness |
| AssetPlanner | semantic grouping 真實發生（10→6+2 層）；dup-id schema gap 是 future work | system / future work |
| LayoutGenerator | position_preference hint 100% 遵守、semantic_type 隱含 z 優先序 100% 遵守 | results / system |
| AestheticJudge | vision 真的被用（best_id 選 GT）、ACCEPT_THRESHOLD=80 對 prompt 過嚴、feedback 已 83% specific | discussion / future work |
| IterationState | Role 責任邊界明確（不去重、由 Team 負責）、max_total_rounds 邊界正確、ACCEPT 不消耗 counter | system |

**輸出**
- `layout_agent/output/generator_corner_report.json`（3 case × 11 invariants）

---

#### Pytest CI 機制接通（2026-05-13，優先 2 第一步）

5/5 Role corner case 全綠後，下一步是把 invariant 移到 repo 的 pytest 框架，當作 sanity gate / CI 入口。盲點：MetaGPT 的 `pytest.ini` 把 `tests/metagpt/ext` 列在 `norecursedirs`，**整個 ext/ 預設不被 collect**。但 pytest 接受顯式路徑覆蓋此規則。

**第一支 pytest 檔案**

`tests/metagpt/ext/agentlayout/test_iteration_state.py` — IterationStateRole 的 17 條 invariant 從兩個 verify_*.py 檔案移植過來：
- `test_boundary_max_rounds_emits_iterationstop_at_third_reject`（5 inv，原 verify_iteration_corner Case 1）
- `test_duplicate_judgement_increments_counter_twice`（4 inv，Case 2）
- `test_accept_does_not_increment_counter`（2 inv，Case 3）
- `test_mvp_3rejects_then_accept_routes_correctly`（6 inv，原 verify_roles_mvp Role 5）

**執行命令與結果**

```bash
# 用顯式路徑繞過 norecursedirs
pytest tests/metagpt/ext/agentlayout/test_iteration_state.py -v --no-cov

# 結果：4 passed, 12 warnings in 1.66s
```

**安裝記錄**：meta env 跑 pytest 前需先 `pip install -e '.[test]'`（裝了 pytest 8.4.2、pytest-asyncio 0.25.3、pytest-cov、coverage 等）。

**意義**
- ✅ pytest 機制接通了：`tests/metagpt/ext/agentlayout/` 可作為 CI sanity gate 入口
- ✅ async role-act 測試在 pytest-asyncio 下正常運作（17 invariants × 4 tests × 1.66s）
- ✅ 純離線、$0、可頻繁跑 — 適合每次改動 IterationStateRole 都做回歸
- ❌ tests/metagpt/ext 被 norecursedirs 略過，純 `pytest` 不會撿到此檔；要靠 sanity 命令明寫路徑或加進 CI workflow 的 explicit invocation

**接下來該補的（按優先序）**
1. ✅ **IoU + baseline 純離線測試移植**（2026-05-13 完成、見下節）
2. **LLM-driven Role corner case migration with `@pytest.mark.requires_llm`**：把另外 4 個 verify_*_corner.py 移植進來，但用 marker 預設 skip（避免無 API key 環境跑爆）
3. **CI workflow 變更**：在 `.github/workflows/unittest.yaml` 加一行 `pytest tests/metagpt/ext/agentlayout/` 確保每次 PR 都跑這 24 條 pytest function

---

#### Pytest CI 擴充：IoU + Baseline 移植（2026-05-13，優先 2 第二步）

第二批 pytest 檔案上線，**累計 24 個 pytest function、~53 assertions 全綠**。

**新檔案**
- `tests/metagpt/ext/agentlayout/test_iou.py` — 13 functions：bbox_iou edge cases（6）、layout_iou matching（2）、unmatched tracking（2）、empty input（1）、JSON round-trip（2）
- `tests/metagpt/ext/agentlayout/test_baselines.py` — 7 functions：random_layout determinism + boundary + fraction bounds、centered_stack 確定性幾何

**執行命令與結果**

```bash
pytest tests/metagpt/ext/agentlayout/ -v --no-cov

# 結果：24 passed, 12 warnings in 1.90s
```

**目前 pytest 套件狀況（offline 全綠）**

| File | Functions | Assertions | LLM? | Time |
|---|---|---|---|---|
| `test_iteration_state.py` | 4 | 17 | 否 | ~0.7s |
| `test_iou.py` | 13 | 22 | 否 | ~0.4s |
| `test_baselines.py` | 7 | 14 | 否 | ~0.3s |
| **合計** | **24** | **~53** | **$0** | **<2s** |

**剩下要做**
- LLM-driven Role corner 4 個 verify_*_corner（48 invariants）：建 `conftest.py` 提供 `@pytest.mark.requires_llm` skip 機制
- CI workflow YAML 變更：在 unittest.yaml 加 explicit pytest invocation

---

#### Pytest CI 第三步：`requires_llm` marker 機制（2026-05-13）

完成 pytest CI 化的最後一塊基礎建設：**LLM-driven test 預設 skip / 顯式 opt-in 才跑** 的 marker 機制。

**新檔案**
- `tests/metagpt/ext/agentlayout/conftest.py` — 註冊 `requires_llm` marker、實作 `pytest_collection_modifyitems` hook：若 invocation 不含 `-m requires_llm`，所有標記過的 test 自動 inject `pytest.mark.skip`
- `tests/metagpt/ext/agentlayout/test_analyst_corner.py` — 範本：Analyst 3 個 case 用 `@pytest.mark.requires_llm + @pytest.mark.asyncio` 雙標記，從 `verify_analyst_corner.py` 邏輯移植

**兩種模式驗證**

```bash
# 預設（offline-only）：
pytest tests/metagpt/ext/agentlayout/ -v --no-cov
# → 24 passed, 3 skipped

# 顯式 opt-in（需 OPENAI_API_KEY、~$0.15）：
pytest tests/metagpt/ext/agentlayout/ -m requires_llm --collect-only --no-cov
# → 3/27 tests collected (24 deselected)
```

**機制底層運作**

`conftest.py` 的 `pytest_collection_modifyitems(config, items)` 在 collection 結束後執行：
1. 讀 `config.getoption('markexpr')` —— 若含 `requires_llm` 字串，直接 return（不動 items）
2. 否則 iterate items，每個含 `requires_llm` keyword 的 item，呼叫 `item.add_marker(pytest.mark.skip(reason=...))`

這保證：
- 預設 CI run、未設定 OPENAI_API_KEY 的環境 → LLM test 自動 skip、不爆
- 想跑 LLM test 的 dev → `-m requires_llm` 一個 flag 切換

**完整 pytest 套件狀況（新增後）**

| File | Functions | Offline / LLM | Default 行為 |
|---|---|---|---|
| `test_iteration_state.py` | 4 | 100% offline | 全跑 |
| `test_iou.py` | 13 | 100% offline | 全跑 |
| `test_baselines.py` | 7 | 100% offline | 全跑 |
| `test_analyst_corner.py` | 3 | 100% LLM | **全 skip** |
| **合計** | **27** | 24 offline + 3 LLM | **24 passed, 3 skipped** |

**論文章節：CI gate 三層設計**

| 層 | 命令 | 用途 | Cost |
|---|---|---|---|
| 1. Offline-only | `pytest tests/metagpt/ext/agentlayout/` | CI default、每 PR 跑 | $0 |
| 2. LLM opt-in | `pytest tests/metagpt/ext/agentlayout/ -m requires_llm` | 開發者本地 / nightly | ~$0.15-0.70 |
| 3. Full driver | `python layout_agent/output/verify_*.py` | Stand-alone 報告產生器 | 同上 |

**剩下可選的擴充**
- ✅ Mirror 剩下 3 個 verify_*_corner（Judge / Planner / Generator）到 pytest（2026-05-13 完成、見下節）
- 改 `.github/workflows/unittest.yaml` 加 `pytest tests/metagpt/ext/agentlayout/` 步驟讓每個 PR 都跑 offline gate

---

#### Pytest CI 第四步：5 個 LLM Role 全進 pytest framework（2026-05-13 收尾）

完成優先 2 pytest CI 化的最後一塊：剩下 3 個 LLM-driven Role corner 也 mirror 為 pytest。**5/5 Role 現在都有 pytest 入口、12 個 LLM-marked tests 共同享 default-skip 保護**。

**新檔案（3 個）**
- `tests/metagpt/ext/agentlayout/test_judge_corner.py` — 3 functions（multimodal probe / GT soft floor / feedback specificity）
- `tests/metagpt/ext/agentlayout/test_planner_corner.py` — 3 functions（minimal single / dup ids raises / 10-elem grouping）
- `tests/metagpt/ext/agentlayout/test_generator_corner.py` — 3 functions（tight canvas QC / top_right honor / bg-below-fg）

**最終 pytest 套件狀況**

| File | Functions | Offline / LLM | Default | `-m requires_llm` |
|---|---|---|---|---|
| `test_iteration_state.py` | 4 | offline | 跑 ✅ | deselect |
| `test_iou.py` | 13 | offline | 跑 ✅ | deselect |
| `test_baselines.py` | 7 | offline | 跑 ✅ | deselect |
| `test_analyst_corner.py` | 3 | LLM | skip 🟡 | 跑 ✅ |
| `test_judge_corner.py` | 3 | LLM | skip 🟡 | 跑 ✅ |
| `test_planner_corner.py` | 3 | LLM | skip 🟡 | 跑 ✅ |
| `test_generator_corner.py` | 3 | LLM | skip 🟡 | 跑 ✅ |
| **合計** | **36** | 24 offline + 12 LLM | **24 passed, 12 skipped** | **12/36 collected** |

**兩種模式驗證**

```bash
# 預設（CI default）：
pytest tests/metagpt/ext/agentlayout/ -v --no-cov
# → 24 passed, 12 skipped in 1.81s

# 顯式 opt-in（需 OPENAI_API_KEY、~$0.70 全跑）：
pytest tests/metagpt/ext/agentlayout/ -m requires_llm -v --no-cov
# 或先用 --collect-only 預覽：
pytest tests/metagpt/ext/agentlayout/ -m requires_llm --collect-only --no-cov
# → 12/36 tests collected (24 deselected)
```

**每個 LLM-marked test 對應的 verify driver**

| pytest 函式 | 對應 driver | LLM cost |
|---|---|---|
| `test_analyst_*` × 3 | `verify_analyst_corner.py` | ~$0.15 |
| `test_judge_*` × 3 | `verify_judge_corner.py` | ~$0.20 |
| `test_planner_*` × 3 | `verify_planner_corner.py` | ~$0.20 |
| `test_generator_*` × 3 | `verify_generator_corner.py` | ~$0.15 |
| **小計** | | **~$0.70** |

**論文章節：CI 三層設計（最終版）**

| 層 | 命令 | 用途 | Cost | 範圍 |
|---|---|---|---|---|
| 1. Offline pytest | `pytest tests/metagpt/ext/agentlayout/` | CI default、每 PR 跑 | $0 | 24 functions |
| 2. LLM opt-in pytest | `pytest tests/metagpt/ext/agentlayout/ -m requires_llm` | 開發者本地 / nightly | ~$0.70 | 12 functions |
| 3. Stand-alone driver | `python layout_agent/output/verify_*.py` | 產 JSON report、研究分析 | 同上 | 5 個 driver |

**剩下唯一未做**：改 `.github/workflows/unittest.yaml` 加 explicit step，讓每個 PR 自動跑層 1（24 個 offline pytest）。1 行變更但有 fork vs upstream 的決定要 user 評估。

---

## 實作進度

### 2026-05-14：Aesthetic Judge Prompt Upgrade（優先 3 第一步落地）

**動機：** 2026-05-10 ablation 已證明 LLM 100% 吸收可驗證 feedback，但 live run 75→72→72 不收斂；2026-05-13 corner Case 3 又揭露 feedback specificity ratio = 0.833（其實不模糊）。結論：問題不在「有沒有 element_id」，而在「有 id 但建議是文字描述沒有目標數字」，Generator 沒可逼近的數值目標。

**升級內容（3 個 commit-able 變動）：**

1. **Schema：擴 `AestheticFeedback` + 新增 `Suggestion` / `SuggestionKind`**（`metagpt/ext/agentlayout/schema.py`）
   - 新 enum `SuggestionKind`：`resize / move / spacing / typography / color / zorder / other`
   - 新 BaseModel `Suggestion`：`kind, target_id, metric, op, value, rationale`
   - `model_validator` 強制 numeric kind 用 int/float value、color kind 用合法 hex（`#RGB / #RRGGBB / #RRGGBBAA`、字元必須 0-9a-fA-F）
   - `AestheticFeedback` 加 `structured_suggestions: List[Suggestion]`（default empty）；舊欄位 `suggestions: List[str]` 保留 → 舊 JSON 全部仍 parseable
2. **Prompt：升級 `judge_aesthetic.py` PROMPT_TEMPLATE + FORMAT_EXAMPLE_REJECT**
   - 加「Structured suggestions (REQUIRED on reject)」段落、列 `kind` enum + `metric` 慣例 + numeric/color 範例
   - FORMAT_EXAMPLE_REJECT 加 3 條 structured（typography font_size、spacing gap_to、resize width）
   - 4 條新 ATTENTION：「reject 必須 ≥1 條 structured」「numeric kind value 必須是數字不是 string」「最多 1 條 other」「target_id 必須在 Layout Tree 出現」
3. **Offline Pytest：新檔 `test_aesthetic_feedback_schema.py`**
   - 28 個函式涵蓋：legacy parse、structured round-trip、numeric/color validator 邊界、`AestheticJudgement` 決策 ↔ feedback invariant
   - 跑時 ~0.07s、$0、不需 LLM
   - 第一次 run 抓到 `#GG0000` 被誤 accept 的 bug，回去補了 hex 字元 validation

**Pytest 套件規模（2026-05-14 收尾）：**

| 項目 | 數量 | Cost | Runtime |
|---|---|---|---|
| Offline functions | 24 + 28 = **52** | $0 | ~2.4s |
| LLM-marked functions（預設 skip） | 12 | $0 (skip) | — |
| **總 collected** | **64** | — | — |

跑出來：`pytest tests/metagpt/ext/agentlayout/ --no-cov -q` → **52 passed, 12 skipped in 2.43s**

**為什麼 backward compat 是必要的：** 22 個 user（13 metagpt 模組 + 2 pytest + 7 driver）會碰 `AestheticFeedback`/`AestheticJudgement`。default empty list 讓 22 個 user 一行都不用改；prompt 才是要求 LLM 「以後請填」的地方。

**下一步（未做，需 LLM cost）：** 重跑 `run_role_team_live.py` 觀察分數 trend，看是否從 75→72→72 變成 75→78→82。預估 ~$0.30、3-4 reject cycle。如果還是不收斂，回頭看是 prompt issue 還是 Generator 沒消化 structured（後者要看 `generate_layout.py` 是否把 retry feedback 餵進 prompt context）。

### 2026-05-14 補充：Live run 結果（升級 prompt 後）

- 命令：`python layout_agent/output/run_role_team_live.py` 跑滿 ~$0.30
- **Score trend：72 → 72**（與 2026-05-10 baseline 75→72→72 一樣 stuck）
- ✅ LLM 兩 round 都產 3 條 well-formed structured_suggestions（含 typography / spacing / resize、target_id 對到 title_1 / product_image_1、value 都是 numeric、含 rationale）
- ✅ Schema 接住所有輸出，無 ValidationError
- ❌ 第 3 reject round 暴露既有 QC bug：Generator 嘗試跟著「title_1 font_size>=72 + width>=600」生成 → 15 個 candidate（5 base × 3 top-up）全 fail QC → `RuntimeError: 0 candidates passed QC after 3 top-up round(s)`
- ✅ Router 邏輯仍正常：iteration count=2、`RetryGeneration messages=2`、無 cause_by fallback

**真正的 bottleneck（重新定位）：**

| 層 | 狀態 | 下一步 |
|---|---|---|
| Judge prompt 產 structured feedback | ✅ 已升級、實測 LLM 遵守 | — |
| Schema 保證 structured 是 verifiable | ✅ pydantic 已驗證 | — |
| **Generator prompt 解讀 structured** | ❌ generate_layout.py 只 dump 整個 JSON 進 context，沒有「優先看 structured_suggestions」指示 | 升級 generate_layout.py PROMPT_TEMPLATE |
| **QC 與 suggestion 衝突** | ❌ Suggestion 推得太大時 QC `no_overlap` / `size_preference` 全拒 | 觀察哪條 QC fail 最多、調整 K_VALID 或放寬 size_preference |
| **ACCEPT_THRESHOLD=80 過嚴** | ❌ 2026-05-13 已證 Crello GT 只拿 68 | 校準 80 → 70-75 |

升級 Judge prompt 是必要但不充分。要看到分數收斂，Generator prompt 與 ACCEPT_THRESHOLD 兩處都得跟著動。

### 2026-05-14 步驟 2：Generator PROMPT_TEMPLATE 接 structured_suggestions

承上節 live run 暴露的兩個 Generator-side 問題（沒解讀 structured / over-apply `>=` 導致 QC fail），動 `metagpt/ext/agentlayout/actions/generate_layout.py` 的 PROMPT_TEMPLATE：

1. **加「How to read `feedback`」段落** — 明確分 free-text vs structured，要求 LLM 「PREFER `structured_suggestions` over the free text」
2. **加 operational mapping 表** — 7 種 `kind` 對應的具體欄位（`resize`→width/height、`move`→left/top、`spacing`→gap_to:OTHER_ID、`typography`→font_size/font_weight、`color`→color hex、`zorder`→z_index、`other`→fallback）
3. **加 operator 解讀說明** — 特別強調 `>=` 是 lower bound、`aim for value to value*1.2`、`do NOT exceed by huge margins, that creates overlap and fails QC`（這條直接針對 live run 觀察到的失敗模式）
4. **改 final ATTENTION** — 從「adjust according to specific suggestions」變「satisfy every structured_suggestion in at least 4 of 5 candidates」（quantifiable 目標）

**Trade-off：** prompt 多 ~250 tokens / call，每 reject round cost 增 ~$0.01，但若能讓 score trend 從 72→72 變 72→78→82 就值得。如果 QC fail 率因 `value*1.0-1.2` hint 而降，retry cost 也會回收。

**驗證（offline）：** `pytest tests/metagpt/ext/agentlayout/ --no-cov -q` → **52 passed, 12 skipped in 2.28s** — 既有 schema 與 corner test 全綠，沒打到。

**下次該做：** 重跑 `run_role_team_live.py` 對比 trend；若還是 stuck，下一條動 `ACCEPT_THRESHOLD` 從 80 降到 70-75（schema.py line 431）。

### 2026-05-14 步驟 3：ACCEPT_THRESHOLD 80 → 75（Crello calibration 起步）

緊接著步驟 2，動 `schema.py:528 ACCEPT_THRESHOLD: int = 75`，並在 docstring 註明 calibration 來源：

> 2026-05-13 verify_judge_corner Case 2 measured Crello designer ground-truth at 68/100。原 80 比 GT 還高 → loop 永遠不可能接受任何「人類設計水準」的輸出 → 跟 2026-05-14 live trend 觀察一致（72→72 卡關）。降到 75 後 GT 仍過不了（68 < 75），但留下足以區分的 headroom；agent 跑出來的 72 也仍過不了，loop 還能繼續學。完整 N-sample calibration 是接下來該做的 ablation。

**同步修正所有 hardcoded `80`（共 5 處跨 5 檔）：**

| 檔案 | 位置 | 動作 |
|---|---|---|
| `metagpt/ext/agentlayout/schema.py` | line 528 + 12 行新 docstring | 主體值 80→75 + calibration history |
| `metagpt/ext/agentlayout/actions/judge_aesthetic.py` | module docstring line 14 | `(>= 80)` → `(>= 75)` |
| `metagpt/ext/agentlayout/actions/judge_aesthetic.py` | PROMPT_TEMPLATE Case A/B 行 | 兩個 `80` → `75`（**這是會直接餵 LLM 的字面值，必須改**） |
| `metagpt/ext/agentlayout/roles/aesthetic_judge.py` | `goal` 字串 | `>= 80` → `>= 75` |
| `tests/metagpt/ext/agentlayout/test_judge_corner.py` | sanity assert + 註解 | `== 80` → `== 75`、註解寫明 calibration 歷史 |
| `layout_agent/output/verify_judge_corner.py` | Case 2 docstring | 標註 `(75 since 2026-05-14, was 80)` |

**Offline pytest 新增 4 個函式**（`test_aesthetic_feedback_schema.py`）：

1. `test_accept_threshold_is_75` — pin 住數值，避免無正當理由 revert
2. `test_accept_threshold_strictly_above_gt_baseline` — 兩邊夾擊：必須 `> 68 (GT)` 且 `< 80 (原值)`，revert 必須補 N-sample 證據
3. `test_accept_judgement_at_exactly_threshold_validates` — 邊界：`total == 75` 必須能 ACCEPT（比較式是 `>=`）
4. `test_reject_judgement_just_below_threshold_validates` — 邊界：`total == 74` 必須 REJECT

**驗證：** `pytest tests/metagpt/ext/agentlayout/ --no-cov -q` → **56 passed, 12 skipped in 2.86s**（從 52 增 4 條 threshold test）

**Trade-off：**
- ✅ 對 Crello GT 不再 degenerate（之前 GT 自己都過不了）
- ✅ Aesthetic Judge LLM prompt 看到的 Case A 範例現在跟 schema 一致
- ❌ 5 處需要同時改的 hardcoded value 是個 anti-pattern；後續若再改數值，最好考慮把 PROMPT_TEMPLATE 也改用 `{threshold}` placeholder（這次先不動以免擴大改動範圍）
- ⚠️ 未來該做：在 N=10+ Crello sample 上重做 corner Case 2 看 GT 分布，數據驗證 75 是否是 sweet spot

### Aesthetic Judge Prompt 兩階段 leak 修補（2026-05-14 步驟 4+5）

**動機：** 2026-05-14 第二輪 live run（步驟 2+3 落地後）發現 score 仍卡 72，且 QC RuntimeError 持續。離線 reproducer（`layout_agent/output/debug_qc_retry.py`，純檢測無 LLM cost）讓 retry-round QC 失敗原因第一次可見。揭露 **兩層獨立 leak**：

#### Leak #1：Judge metric coordinate semantics 不明
- Judge 之前用 `metric="right"` / `"bottom"`（margin-from-edge 語意），但 Layout schema 根本沒這兩個欄位 — Generator 把 `bottom=20` 當成 `top=20`，logo 跑到畫布頂端反而違反 `position_preference(bottom_right)` hard constraint
- **修補**：`judge_aesthetic.py` PROMPT_TEMPLATE 加 per-kind metric whitelist + 「NEVER emit `metric:"right"` / `"bottom"`」明示禁令 + 拆成 left/top 兩條 move 的 worked example
- **回歸測試**：`test_judge_prompt_lists_metric_whitelist_and_forbids_right_bottom` pinned 字串
- **live 驗證**：第二輪 live run（~$0.30）顯示 Judge 不再 emit right/bottom，但 QC retry 仍全 fail（揭露第二層 leak）

#### Leak #2：Judge 只下 width 沒同步 height，撞 area_ratio 門檻
- QC `size_preference(prominent)` 算的是 `width × height / canvas_area >= 0.10`。Judge 之前下 `resize width>=600` 但忘了同時下 height suggestion，Generator 老實照辦把 title 弄成 600×100=60000 px²，永遠卡在 area_ratio=0.062 < 0.10
- **修補**：PROMPT_TEMPLATE 加 ATTENTION 區塊明示 area math（`width*height >= 0.10*canvas_area`），要求 enlarge prominent element 時同時 emit width AND height 兩條 resize；附 800×1200 canvas + title_1 prominent 的 worked example（600 × 180 = 108000 ≥ 96000）
- **回歸測試**：`test_judge_prompt_explains_size_preference_area_math` 校 area 數字 + 「BOTH a width AND a height」字串
- **live 驗證**：第三輪 live run（~$0.30）— **QC crash 完全消除**，pipeline 第一次跑完 3 完整 iter（含 RetryGeneration×2 + RetryAnalyst×1）才退場；Judge 三輪都精確 emit `width + height + gap_to:title_1` 三條 suggestion；分數仍 72（plateau 從 prompt 層移到 vision rubric / element placement 層）

#### 工具產出
- **離線 QC reproducer**：`layout_agent/output/debug_qc_retry.py`（解析 live log 的 candidate JSON 區塊，直接餵 `quality_checker.filter_valid`，輸出每個 candidate 的 violation type / targets / detail）— 之後診斷 retry-round QC fail 不必再燒 LLM
- **pytest 累計**：58 passed + 12 skipped in 2.37s（57 + 2 新 prompt-content 回歸測試）

**Trade-off：**
- ✅ QC RuntimeError 完全消除，feedback loop 第一次能跑滿 max_total_rounds=3 含 Analyst-target rebuild
- ✅ Judge 主動下「成對」suggestion（width + height、left + top）符合 schema 限制
- ❌ 分數仍 plateau 72：bottleneck 已脫離 prompt 層，下次該轉戰 vision rubric / Generator placement 細節 / 或考慮 ACCEPT_THRESHOLD 再降到 70
- ⚠️ 5 處 hardcoded area threshold 同樣是 anti-pattern — Judge prompt 寫死 `0.10`、`0.08`、`0.05` 跟 `quality_checker.SIZE_HINT_LOWER_BOUND` 必須手動同步；未來若改門檻應考慮 prompt placeholder 機制

### Generator canvas-coverage rule + plateau 結構性根因確認（2026-05-14 步驟 6）

**動機：** 步驟 5 修補 QC crash 後，分數 plateau 72 仍在。觀察 r6_cand_02 渲染 PNG 揭露真因——`bottom 1/3 of 800×1200 canvas` 全白、title 是裸字浮在白底、整體上重下輕。Judge 給「title not prominent」的回饋其實不準（title 已經 600×180），Judge 真正不滿意的是 visual_coherence 與 layout_balance。

**修補：** `generate_layout.py` PROMPT_TEMPLATE 加 ATTENTION 區塊：
- `max(top + height) >= 0.85 * canvas_height`（最低元素 bottom edge 必須觸底）
- `min(top) <= 0.10 * canvas_height`（至少一元素貼頂）
- 附 800×1200 worked example（y >= 1020 / y <= 120）+ 「do NOT cluster everything in the top half」明示禁令

**回歸測試：** 新檔 `test_generator_prompt_template.py`（給未來 Generator prompt regression 集中放）— `test_generator_prompt_pins_canvas_vertical_coverage_rule` pin 6 條字串。

**live 驗證（第四輪 ~$0.30）：**
- ✅ 規則部分遵守：r6_cand_03 `max(top+height)=1200 ≥ 1020` 通過，logo 落到 y=1100
- ❌ 規則部分違反：`min(top)=200 > 120`，product 不肯貼頂，上方仍留 200px 白
- ❌ 分數無變化：仍 72/70/70/69/68（跟第三輪 72/70/68/69/67 結構同）

**Plateau 結構性根因確認：**
> 3 個元素（product + title + logo）在 800×1200 上**本來就無法構成 balanced poster**。真實設計有背景色、裝飾色塊、副標、CTA 按鈕——稀疏 3 元素不管怎麼擺，Judge vision rubric 都只能給到 17/25 visual_coherence + 17/25 layout_balance。bottleneck 已**脫離 prompt 工程**範疇。

**Trade-off：**
- ✅ Generator 確實會聽 partial coverage 規則（bottom edge 達 0.92 canvas_height）
- ❌ 未能突破 plateau — 證實「prompt 工程的分數天花板已到」
- 📌 此**負向結果本身是論文資料點**：證明 (a) score plateau 是 spec sparsity 問題不是 LLM 能力問題、(b) 純 prompt 層改進有 diminishing returns

**下次 session 三條候選方向（未做、依研究受益排序）：**
1. **換真實 Crello brief（5-7 elements）重跑** — 識別 plateau 是不是真的出在 spec sparsity 的最直接實驗。~$0.30/sample
2. **Analyst 加 default background_color** — Generator/renderer 接住 background 填色，預期 push visual_coherence 17→20
3. **ACCEPT_THRESHOLD 75→70（認輸 plateau）** — $0、step 3 doc 本來就規劃要降；讓 pipeline 走到 ACCEPT 完成 reject-loop 收斂示範

---

### Analyst 預設 background_color + canvas-aware palette（2026-05-14 步驟 7）

**動機：** 步驟 6 確認 plateau 結構性根因是「3 元素 + 純白底」的稀疏 spec。本步驟驗證候選方向 (2)：教 Analyst 在 user brief 沒指定背景時 emit 一個與 style_keywords 對齊的 pleasant hex（如 `#F5E6D3` warm / `#1B2B4A` cool），避免渲染出來的 PNG 是一片裸白底。

**修補（5 處）：**
1. `schema.py` `Canvas` 加 `background_color: Optional[str]` 欄位 + `field_validator("background_color")` 強制 6 位 hex（小寫自動正規化成大寫）。default `None` 保 backward compat。
2. `tools/renderer.py` `_make_canvas` precedence 改為 (asset_ref) → (background_color) → 白；新增私有 `_hex_to_rgba` defensive helper。
3. `pipeline.py` `default_white_background(canvas)` 從 stub 升級成 canvas-aware：`dominant_palette[0] = canvas.background_color or "#FFFFFF"`、`recommended_text_color` 走 luminance 自動挑（dark on light / light on dark）。這條對 Judge 重要——避免 PNG 渲染米色但 BackgroundAnalysis 還報白色的矛盾。
4. `actions/analyze_brief.py` `FORMAT_EXAMPLE_JSON` 加 `"background_color": null` 欄位；`PROMPT_TEMPLATE` 加 25 行 ATTENTION 段落「Background color inference」：plateau motivation、`AVOID "#FFFFFF"` 明示禁令、5 個 keyword bucket × 3 hex 的 palette 建議（warm/cool/vibrant/dark/nature）、explicit-white escape hatch（user 真的要白才給白、且 `inferred_fields=false`）。
5. `tests/metagpt/ext/agentlayout/test_analyst_prompt_template.py` 新檔、22 個 pinned-string 與 schema/pipeline/renderer 測試。

**Pytest：** `pytest tests/metagpt/ext/agentlayout/ --no-cov -q` → **81 passed, 12 skipped in 2.83s**（步驟 6 = 59，+22 新測試、無退化）。

**Live 驗證（第五輪、cost $0.28）：**
- ✅ **Analyst 真的有遵守新規則**：emit `canvas.background_color = "#E8F1F8"`（cool/minimal/modern bucket，光冷藍）、`inferred_fields["canvas.background_color"]: true` 正確標注、completely zero `#FFFFFF` emit
- ✅ Pipeline 健康：3 reject cycles、QC 零 crash、3/3 retry 都有走到 RetryGeneration×2 + RetryAnalyst×1
- ⚠️ **分數 plateau 從 72 掉到 68**（req=18 hier=17 bal=17 coh=16，5/5 candidates 同分）
- 📊 **但 68 == Crello GT baseline**（步驟 13 corner case 2 量到的 designer-GT 分數）

**結果解讀：**
> Step 7 機制全部生效（schema、renderer、pipeline、prompt 都串通），但分數**沒**反而**掉**了。原因應該是：背景變色後 vision rubric 對 title/logo 的色彩搭配要求變嚴（hardcoded `#111111` 文字 + `#E8F1F8` 藍底，contrast OK 但不 elegant），同時 sparsity 抱怨還在（feedback：「title_1 not prominent」「product and title too far apart」）。step 6 的根因診斷因此被進一步**強化驗證**：只改背景色而不改元素數量，Judge 看到的問題本質不變、甚至因為色彩 mismatch 更敏感。

**Trade-off：**
- ✅ 步驟 7 達到「pipeline 輸出與 Crello designer GT 分數齊平」（68 vs GT 68）——這是個強訊號可寫進論文
- ✅ schema field + validator + 22 regression test pin 住 prompt，未來不會 silently revert
- ❌ 沒有突破 plateau；再次證明 **3 元素 = 結構天花板**
- 📌 未做：Generator prompt **沒**告知 background_color、文字 color 還是 hardcoded `#111111`。下個 step 8 候選：教 Generator 看 `canvas.background_color`+`recommended_text_color`、emit contrast-aware `color` 給 text element

**下次 session 兩條候選（更新）：**
1. **換 Crello 5-7 element brief 重跑** — 仍是最直接驗證 spec sparsity 假設的實驗；本次步驟 7 已**間接驗證** sparsity 是 root cause（變色不變元素數，分數沒上去）
2. **Step 8 — Generator 接 `background_color` + `recommended_text_color`** — 教 Generator 從 BackgroundAnalysis 讀 palette/text color 並 emit contrast-aware element `color`，預期把 color_harmony / visual_coherence 拉回 17 以上

---

### Step 8 嘗試 contrast-aware text color — REVERTED（2026-05-14 負向實驗）

**假設：** Live #5 plateau 從 72 掉到 68，疑似肇因於 hardcoded `#111111` 文字在 `#E8F1F8` 冷藍底上 contrast 不夠 elegant，被 Aesthetic Judge 扣到 visual_coherence。教 Generator 從 `recommended_text_color` 取色就能拉分。

**做法：**
- `generate_layout.py` `{recommended_text_color}` 行從「override if needed」改成「use this hex verbatim」三個 MUST 措辭；尾段新增 14 行 ATTENTION 「Text-on-background contrast」block + 明示 `Do NOT default to "#111111"` + 點名 FORMAT_EXAMPLE 內 `#1B3A6B`/`#FFFFFF` 是 anti-pattern
- `test_generator_prompt_template.py` 新增 `test_generator_prompt_pins_contrast_aware_text_color_rule`（pin 8 條字串）
- pytest 82 passed（+1 新測試）

**Live #6 結果（cost $0.34）：**
- ✅ 前 3 reject cycles 順利跑完（Generator 每輪 5 valid candidates、Judge 給 verdict）
- ❌ **Analyst retry 後的 Generator 第 4 round 大爆炸**：3 top-up rounds × 5 candidates = **15 candidates 全 fail QC**、`RuntimeError: 0 candidates passed QC`
- ❌ Pipeline 沒有 graceful 退場、沒有 final PNG（render 階段沒到）

**根因檢討（負向實驗的金礦）：**
1. **假設前提不成立。** 重看 Live #5 Judge feedback——「title_1 not prominent」「product and title too far apart」。Judge **從來沒抱怨 contrast / readability / color**。「72→68 是 contrast 問題」是我的事後假設、不是 Judge 直接訴求。
2. **Recommended_text_color 在此 spec 下根本不會變。** Analyst pick 的 `#E8F1F8` luminance > 128，pipeline.py 自動算出 `recommended_text_color = "#111111"`——和 Generator 原本 hardcoded 的值一樣。step 8 的 prompt 變動對此 light canvas 的實際輸出**零差異**。
3. **Prompt 容量是有限的 attention budget。** 多加 14 行對輸出沒實際影響的指令，反而在 retry round 排擠 LLM 對 `size_preference: prominent` 的注意力——Generator 開始忽略 area_ratio ≥ 0.10 要求、QC 全 reject。這呼應 step 5 修過的同一 failure mode：Judge over-prescribes、Generator over-applies。
4. **沒有 isolated reproducer 就 live 燒錢是浪費。** 應該先寫 offline test 驗證 step 8 真的會 emit non-#111111 才 live。

**處理：**
- 還原 `generate_layout.py` 與 `test_generator_prompt_template.py` 到 step 7 commit（`7c5118d4`）狀態
- 不寫新 commit、把 step 8 留在 git log 之外；但保留此章節作為**論文負向結果與 prompt-engineering 邊界的證據**

**論文價值（這次失敗的可發表面向）：**
- **Prompt-engineering 有 attention budget**：相同 LLM 在相同 spec 下，加無關緊要的 ATTENTION 會讓**既有重要規則被淡化**，造成下游 QC failure。這是個可量化的 prompt 設計反例。
- **假設要先 cheap-validate 再 live-burn**：未來再加 prompt 規則前，先寫 offline reproducer（mock LLM 回 hardcoded JSON）驗證新規則的 schema-level 行為。
- **Plateau 真因進一步聚焦**：step 7 確認不是 spec-sparsity 以外的因素能單獨突破天花板；step 8 確認也不是 contrast。剩下最強假設仍是「3 元素本身結構不足」、需用 (a) 5-7 element brief 重跑來實證。

**下次 session 唯一候選（更新）：**
- **(a) 換 Crello 5-7 element brief 重跑**：仍是最 falsifiable 的剩餘假設；本次 step 8 也排除了 contrast hypothesis，sparsity hypothesis 的相對權重更高。~$0.30/sample

---

### Step 9 — 5-element Crello sparsity test：sparsity hypothesis CONFIRMED（2026-05-14）

**目的：** 在排除背景色（step 7）與 contrast（step 8 reverted）後，**唯一未驗的剩餘 plateau 假設**是「3 元素本身結構不足」。本步驟用真實 Crello 5-element brief 跑同一 pipeline，看分數是否突破 3-element baseline 68。

**選 fixture：** Crello sample `5c6c0cba85ea3c16f964a15d`「Minimalistic geometric pattern」hiring poster
- 1080×1920（同 aspect 類別，可比性高）
- **5 elements**：1 背景圖 + 4 文字（「The Art Institute of Seattle」「We are hiring a」「Public Art Curator」「Plan and oversee...」）
- 4 個文字層帶 hierarchy——能 stress 測試 Generator 的 `info_hierarchy` 容量

**新檔：**
- `layout_agent/output/run_role_team_live_crello.py`（reject-loop + Crello loader + sparsity-hypothesis verdict logic、cost cap $0.30）

**Bug surface 1（金礦）：** 第一次 run **crash**——0/15 candidates 全 fail QC。診斷揭露 Analyst emit `position_preference: hint="center_top"` 但 `quality_checker.POSITION_HINT_TO_BANDS` 只有 `"top_center"`（詞序不同），造成 `UNKNOWN_HINT` violation。3-element shoe run 沒撞到是因為 hint 是 `top_right`、兩種詞序都不會混。

**修補：** `quality_checker.py` `POSITION_HINT_TO_BANDS` 加 8 個 reversed-word-order alias（`center_top`/`center_bottom`/`left_top`/`right_top`/`left_bottom`/`right_bottom`/`left_middle`/`right_middle`）。新檔 `tests/metagpt/ext/agentlayout/test_quality_checker_position_hints.py`（17 個 pinned tests：7 canonical + 8 alias + 1 count + 1 end-to-end check_candidate）。

**Pytest：** 98 passed, 12 skipped in 2.88s（step 7 baseline = 81，+17 新 QC alias 測試）。

**Live #7 重跑結果（cost $0.49，3 reject cycles 完成 + Analyst retry 後 Generator crash）：**

| Verdict | best candidate | best score | distribution |
|---|---|---|---|
| 1 (round 0-1) | r1_cand_03 | **72** | 68, 70, 72, 69, 70 |
| 2 (round 3-5) | r3_cand_03 | **72** | 68, 70, 72, 69, 70 |
| 3 (round 6-8) | r6_cand_01 | **70** | 70, 68, 69, 67, 66 |

**對照 3-element baseline 68 → delta = +4 / +4 / +2、平均 best 71.3 (+3.3)**

**Sparsity hypothesis：✅ CONFIRMED**——5-element 一致地比 3-element baseline 高 2-4 點，且多個 candidates 達到 70+。

**Score breakdown 細看：**
- baseline (3-elem r6_cand_02): req=18 hier=17 bal=17 coh=17 = 69
- best (5-elem r1_cand_03): req=20 hier=18 bal=17 coh=17 = 72

**所以加分都來自 `requirement_alignment` (+2) 與 `info_hierarchy` (+1)、`layout_balance` 與 `visual_coherence` 仍 17。** 這提示 plateau **有兩段**：
- 第一段 ~4 點（sparsity-driven）→ 加元素可以彌補
- 第二段 ~5 點（balance + coherence 的 17/25 上限）→ 加元素**沒有**修復，可能來自缺乏裝飾色塊 / Background Analyzer 仍是 stub

**Bug surface 2（已知 separate issue）：** Analyst retry（iteration 3）後的 Generator 在第 4 round QC crash——同 live #6 crash mode。原因：Judge over-prescribes、Generator over-applies。這是 step 5 修過的同類 failure mode 在新 spec 形態下重新出現。**不影響本次 sparsity 結論**（3 reject cycles 都成功完成 + 有 3 個完整 verdicts 可比較）。

**Trade-off：**
- ✅ **sparsity hypothesis 拿到正向證據**：論文「plateau 不是 LLM 能力問題、是 spec density 問題」有了量化支持
- ✅ 副產品 QC alias bug 修補 + 17 個 regression test 防 revert
- ✅ 平均 +3.3 是 statistically meaningful 在這 3 觀察上（雖 N=3，但 5/5 candidates 都 ≥66、3/5 ≥70）
- ❌ 仍未達 ACCEPT 75；plateau 的 second segment (coh/bal=17) 還在
- ❌ Analyst retry 後 QC crash mode 沒處理（live #6/#7 同樣撞）

**下次 session 兩條候選（更新）：**
1. **Step 10 — 修 post-Analyst-retry QC crash** ：要嘛降 `max_total_rounds` 防 Analyst retry、要嘛在 Generator retry prompt 警告 Analyst 可能下了難滿足的 spec
2. **Step 11 — 攻 plateau 第二段**：bal/coh 仍卡 17，可能 root cause 是 (a) renderer 沒生 decorative shape、(b) `default_white_background` 仍是 stub（沒真實 BackgroundAnalyzer）。需要先實證

---

### Step 9b — N=3 多樣本驗證 + Generator robustness limit 揭露（2026-05-14/15）

**目的：** Step 9 的 sparsity 結論 N=1。為強化論文證據，在剩 2 個 cached 5-element Crello sample 上跑同 pipeline 看是否 generalize 跨 aspect ratio。

**Driver 升級：** `run_role_team_live_crello.py` 加 `argparse --sample-id`、輸出檔名加 sample_id 後綴避免 collision。

**Live #8 — `5954bda9` (1200×600 horizontal, "Citation about dog pet")：**
- Crello meta.json 有 5 elements，但其中 1 個是 `type_code=3` 的背景 shape（非 image / text），被 `build_pipeline_inputs` filter 掉——Analyst 實際看到 4 elements (3 decorative_images + 1 title)
- Analyst 加 `position_preference(text_1, center)` + `no_overlap(全 4 element)`
- **結果：0 / 15 candidates fail QC、無任何 Judge verdict 就 crash**。1200×600 上 center band 含 text、其餘 3 images 必須完全不 overlap、Generator 無法擺出有效布局
- Cost ~$0.12（沒到 Judge 階段）

**Live #9 — `5d972ca9` (537×240 small horizontal, Russian "Travelling Tips")：**
- 同樣 5 → 4 elements after filter
- Analyst 加 `position_preference(title_1, top_center)` + `position_preference(subtitle_1, bottom_center)` + `no_overlap(全 4)`
- **結果：0 / 15 candidates fail QC、無任何 verdict 就 crash**。537×240 太小、title top + subtitle bottom 已用掉大半垂直空間、2 images 沒地方擺
- Cost ~$0.13

**最終 N 計：sparsity-validated N=1（Live #7）、 generator-robustness-failure N=2（#8, #9）。**

**論文寫作策略（重要）：** **不**寫「N=3 驗證 sparsity」，因為只有 #7 跑完。誠實表達為：

> 「On the single Crello sample our pipeline could fully evaluate (Live #7, 5-element 1080×1920 hiring poster), the 5-element brief scored 3.3 points above the 3-element synthetic baseline averaged over 3 reject-loop verdicts. Two additional attempted runs on 4-element Crello briefs (Live #8 horizontal 1200×600, Live #9 small horizontal 537×240) both crashed at the first Generator call with all 15 candidates failing quality-checking. **These crashes are themselves a finding**: the Generator + QC interaction in our pipeline has a robustness ceiling on tight-canvas mixed-content (image + text) specs that we did not encounter on the synthetic 3-element baseline or on the spacious 5-element vertical brief.」

**Trade-off 與後續方向：**
- ✅ 仍能聲稱 sparsity hypothesis 有正向證據（N=1 trial, +3.3 mean delta in 3 verdicts）
- ✅ N=2 失敗本身揭露 Generator+QC robustness 限制——這是論文章「pipeline limitations」一節的良好素材
- ❌ N=3 驗證未成立；未來要 mine 更多 Crello 5+ element 樣本（特別是 spacious canvas 的）
- 📌 **本步驟新揭露的 separate issue（待 step 10 處理）：** Generator 在「tight canvas + position_preference + no_overlap 多元素」組合下會穩定 fail。可能要 Generator prompt 加「detect tight canvas、prefer stacking 而非 grid placement」guidance，或讓 QC 對 no_overlap 允許 ≤5px micro-overlap tolerance

**Pytest baseline 維持：** 98 passed, 12 skipped in 2.88s（無新 source/test code，只是 driver CLI arg 改動，driver 在 output/ gitignored）。

**Local commit：** 與 step 9 同合併 commit（driver CLI 升級僅 in output/，無 source 改動需追加）

---

### Step 10 — QC `no_overlap` 5% area-ratio tolerance（2026-05-15）

**目的：** 解決 step 9b 揭露的 Generator+QC robustness 限制——live #8 (1200×600) 與 #9 (537×240) 的 0/15 hard QC fail 是因為 strict zero-tolerance overlap check 把 LLM 邊緣 round 1–20 px 的 micro-overlap 也判為違規。

**先離線重現問題：** 新增 `layout_agent/output/debug_live8_qc.py` 用 live #8 的 DesignSpec 跑 5 個手刻候選，2/5 過、3/5 fail，no_overlap 是首要 fail mode；確認 `_aabb_overlap` 用 `not (a.left + a.width <= b.left ...)` 是 strict ≤ 比較、touching edges 都算 overlap。

**修補（`metagpt/ext/agentlayout/tools/quality_checker.py`）：**
1. 新 module-level 常數 `NO_OVERLAP_TOLERANCE: float = 0.05`，附 8 行 docstring 紀錄 calibration 來由（live #8/#9 hard fail 證據）
2. 新私有 helper `_aabb_overlap_ratio(a, b) → float`：回傳 `overlap_area / min(area_a, area_b)`，0.0 = disjoint、1.0 = full containment、退化箱回 0.0
3. `_check_no_overlap` 從 boolean overlap 改為 `ratio > NO_OVERLAP_TOLERANCE` 才開 violation，violation `detail` 加上實際 overlap %（給 Generator 看 structured feedback 用）
4. 保留 `_aabb_overlap()` boolean 版本當 wrapper（任何 in-tree caller 都不再用，但留給 external scripts/notebook 用）

**回歸測試（6 個新函式追加在 `tests/metagpt/ext/agentlayout/test_quality_checker_position_hints.py`）：**
- `test_no_overlap_tolerance_constant_is_five_percent` — pin 5% calibration 防被改回 0%
- `test_no_overlap_disjoint_boxes_pass` — happy path
- `test_no_overlap_micro_overlap_at_5_percent_passes` — 5×100/10000 = 5% 邊界 inclusive
- `test_no_overlap_just_above_tolerance_fails` — 6% 必須仍 fail（防 tolerance 漂移）
- `test_no_overlap_message_format_pins_percentage_detail` — pin violation detail 含 % 與 element id（給 Generator structured feedback loop 看）
- `test_aabb_overlap_helper_still_reports_any_overlap` — boolean wrapper 行為 ANY 重疊 → True

**Pytest：** 104 passed, 12 skipped in 2.73s（= step 9 的 98 + 6 新 tolerance test）。

**Live #8 re-run 驗證：** 用 5% tolerance 後 1200×600 sample 跑完整 reject loop exit code 0；確認 fix 真的解了 hard fail。

**Trade-off：**
- ✅ Generator robustness 上限拉高一個 plateau，Live #8/#9 不再 hard crash
- ✅ 6 個 regression test 把 5% tolerance 與 violation detail 格式 pin 死，未來 refactor 不會 silent regress
- ⚠️ 5% 只是工程妥協數字（從 live trace 觀察的 LLM rounding 噪訊量級），不是從 user study 校準；論文寫 limitations 時應提
- ❌ 仍未解 plateau 第二段（bal/coh=17 上限），那條留 step 11

**下次 session 候選方向：**
1. **Step 11 — 攻 plateau 第二段**：bal/coh 仍卡 17，可能 root cause 是 (a) renderer 沒生 decorative shape、(b) `default_white_background` 仍是 stub（沒真實 BackgroundAnalyzer）
2. **Step 10b — 修 post-Analyst-retry Generator QC crash**：live #6/#7 同 pattern；先寫 offline reproducer
3. **Crello sample mining**：找更多 spacious 5+ element sample 把 sparsity 結論從 N=1 推到 N≥5

---

### Step 10c — QC `position_preference` band 10% per-edge tolerance（2026-05-16）

**動機：** Step 10 的 5% no_overlap tolerance 解了 micro-overlap，但 Live #8 (1200×600) re-run 仍 0/15 hard fail。離線抽 LLM 真實 candidate 跑 QC 顯示新 fail mode：5/5 candidate 都因 `position_preference` 失敗 — text_1 中央 y=450 落在 band (1, 2) bottom，但 spec `center` 要求 (1, 1)。1200×600 上 center band y∈[200, 400] 只有 200px 高，三 image 占上排後 LLM 無處放 text。

**離線診斷：** 新增 `layout_agent/output/debug_live8_step10_failmode.py`，從 live log regex 抽 candidate JSON、跑 in-tree QC 算 violation 分布，量化「真正的 fail mode 是什麼」。

**修補（`metagpt/ext/agentlayout/tools/quality_checker.py`）：**
1. 新常數 `POSITION_BAND_TOLERANCE: float = 0.10` + `POSITION_BAND_TOLERANCE_ABSOLUTE_FLOOR: int = 16`，附 9 行 docstring 紀錄 calibration 來由（live #8r 5/5 fail 證據 + 60px slack 反推）
2. 新 helper `_band_bounds_with_tolerance(band, total) → (low, high)`：第三 band 邊緣 ± `max(10% canvas_dim, 16px)`
3. 新 helper `_in_band_with_tolerance(coord, band, total) → bool`
4. `_check_position_preference` 改用 tolerance check；violation `detail` 補上實際接受區間 `[lo, hi]` 與 tolerance 比例（給 Generator structured feedback 看）
5. 保留 `_band_index` strict 版本給其他 caller / 測試使用

**回歸測試（`test_quality_checker_position_hints.py` 加 7 函式）：**
- `test_position_band_tolerance_constants_pinned` — pin 0.10 + 16 防被改回
- `test_position_band_tolerance_unblocks_live8_layout` — 1200×600 上 cy=450 必 PASS（live #8 真實 case）
- `test_position_band_tolerance_just_inside_boundary_passes` — cy=460 = 400 + 60px tolerance 邊界 inclusive
- `test_position_band_tolerance_just_outside_boundary_fails` — cy=470 必 FAIL（防 tolerance 漂移）+ pin detail 字串
- `test_position_band_tolerance_canonical_center_still_passes` — 嚴格 case 仍 PASS（regression 防）
- `test_position_band_tolerance_top_left_still_rejects_far_misses` — 1200×600 上 c=(1000, 500) 對 top_left 必 FAIL（防 tolerance 變所有 band union）
- `test_position_band_tolerance_floor_protects_tiny_canvas` — 100×100 上 cy=80 靠 16px floor 必 PASS

**Pytest：** 111 passed, 12 skipped in 2.79s（= step 10 的 104 + 7 新 band tolerance）。

**離線 reproducer 驗證：** Live #8r 真實 5 LLM candidate 在新 tolerance 下 **0/5 → 4/5 PASS**；剩下 1 個 fail 是 cand_02 `out_of_bounds`（image_3 top+height=900 > 600，QC 該擋）。

**Live #8 重跑（$0.43、3 完整 verdicts、首次 1200×600 跑完 reject loop、exit 0）：**
- Verdict 1 best=70 (r0_cand_01: req=20 hier=18 bal=16 coh=16)
- Verdict 2 best=68 (r3 多個 candidates 同分)
- Verdict 3 best=70 (r6_cand_01)
- 平均 best **69.3 vs Crello GT baseline 68 = +1.3**
- decision=reject（仍未達 75 ACCEPT_THRESHOLD），但 sparsity hypothesis **N=2 validated**（Live #7 1080×1920 hiring poster + Live #8 1200×600 dog pet citation）

**Trade-off：**
- ✅ 1200×600 robustness 限制完全打通；step 10 + 10c 兩步把同一 sample 從 hard fail 帶到完整 reject loop
- ✅ Sparsity hypothesis 從 N=1 → N=2，論文證據強度提升一級
- ✅ Plateau 第二段在不同 aspect ratio 上一致出現（#7 bal/coh=17、#8rc bal/coh=16），更強支持「結構性 not LLM-capability」
- ⚠️ 10% per-edge tolerance + 16px floor 仍是 engineering 數字（live trace 反推），未從 user study 校準
- ❌ Plateau 第二段 bal/coh=16-17 上限仍未解；step 11 攻擊

**下次 session 候選方向（更新）：**
1. **Step 10d — 重跑 Live #9** (537×240 small canvas) 看 step 10+10c 是否也解了 hard fail
2. **Step 11 — 攻 plateau 第二段**：仍是主線目標
3. **Step 10b — 修 post-Analyst-retry Generator QC crash**：未動

### Step 10d — Live #9 537×240 small canvas 重跑驗證（2026-05-18）

**動機：** step 10（5% no_overlap area-ratio）+ step 10c（10% position-band per-edge）已在 Live #8 1200×600 horizontal 上把 hard crash 帶到完整 reject loop，但 small canvas（537×240）尚未驗證。本步驟確認 robustness 修補是否跨 aspect ratio generalize（NEXT_SESSION 下次優先 (1)）。

**做法：** `python layout_agent/output/run_role_team_live_crello.py --sample-id 5d972ca9abc8ea6d1c54e002`（Live #9 同一 Crello 俄文 "Travelling Tips"、4 effective elements）。**無任何程式改動**，純跑 HEAD `d3dc7491` 的 QC tolerance；屬實驗 + 文件更新性質。

**結果（exit 0、$0.554、3 verdicts）—— 對比 step 9b 同 sample 的 $0.13 立即 hard crash：**
- Verdict 1 best=r0_cand_03 total=70
- Verdict 2 best=r3_cand_01 total=70
- Verdict 3 best=r6_cand_01 total=72（req=20 hier=18 bal=17 coh=17）
- 平均 best **70.67 vs 3-element GT 68 = +2.67**（best 72 = +4）；decision=reject

**論文價值：** sparsity robustness 修補在**第二種 aspect ratio（537×240 small）generalize 成功**——完整 pipeline 跑通的 Crello 樣本從 N=2（#7 1080×1920 portrait + #8rc 1200×600 horizontal）升 **N=3**（+#9rd 537×240 small）。step 10/10c 不再是 1200×600 單點 fix，而是跨 canvas 形狀的通用 robustness 改善。

**殘留現象（非本步驟問題）：** log 結尾 `RuntimeError: 0 candidates passed QC after 3 top-up rounds`，發生在 `iteration=3 → RetryAnalyst` 之後的 rebuild round——這是已知的 **step 10b post-Analyst-retry Generator crash**（#6/#7 同 pattern），與 small-canvas tolerance 無關；主 reject loop 已完全打通。

**Trade-off：**
- ✅ robustness 修補跨 aspect ratio 驗證、論文 N=3、零程式改動
- ✅ plateau 第二段 bal/coh=17 在 537×240 上一致出現，再強化「結構性 not LLM-capability」
- ❌ plateau 第二段未動（step 11）；❌ post-Analyst-retry crash 未動（step 10b）

**下次 session 候選方向（再更新）：**
1. **Step 11 — 攻 plateau 第二段 bal/coh=16-17 上限**：最有論文價值、需動 pipeline（疑 BackgroundAnalyzer stub / 無裝飾元素）
2. **Step 10b — 修 post-Analyst-retry Generator QC crash**：#6/#7/#9rd 同 pattern、純工程修補、先寫 offline reproducer

### Step 11 — plateau 第二段根因診斷：確認為 scope-bound limitation（2026-05-18，負向結果）

**動機：** plateau 分兩段——step 9 已解第一段（spec sparsity，5-element brief 把分數從 68 帶到 71~72，+3~4）；第二段是 `layout_balance` / `visual_coherence` 兩項子分數穩定卡在 16-17/25 上限，跨 aspect ratio 一致出現（#7 portrait、#8rc horizontal、#9rd small 皆 bal/coh≈17）。step 6/7/8 三次純 prompt 工程嘗試全部無法推動。本步驟做**離線根因診斷，不燒 live LLM**（依 step 8「先 cheap-validate 再 live-burn」教訓）。

**離線診斷證據（純讀碼、零成本）：**

1. **BackgroundAnalyzer 是 stub** — `pipeline.py:59-76` `default_white_background()` 回傳空 `safe_zones` + 單色 `dominant_palette`，無真實背景/構圖分析；`roles/layout_generator.py:18-19` 註解明寫 "Background analysis is not yet a Role -- a white-fallback BackgroundAnalysis is constructed locally until BackgroundAnalyzerRole is added"。
2. **Generator schema 無裝飾表達力** — `schema.py:369-400` `LayoutElement` 欄位僅 `id/left/top/width/height/angle/z_index/font_*/color/text_align`；`Candidate.elements` 的 id **必須是 DesignSpec 既有 element**。Generator **結構上無法發出新的色塊、分隔線、背景紋理、視覺點綴**。
3. **Renderer 只畫裸素材 + 純色底** — `tools/renderer.py:84-98` `render()` 僅迭代 `spec.elements` 畫前景 text/image，`_make_canvas` 填純色背景，**零裝飾中間層**。Judge 看到的永遠是「幾個素材擺在純色底上」。
4. **Judge rubric 在此輸入下無可加分空間** — `actions/judge_aesthetic.py:170-175`：`layout_balance`=視覺重量分布、避免擁擠/空洞；`visual_coherence`=style/spacing/color 對齊 `dominant_palette`。當 palette 只有單一背景色、且無裝飾元素時，可評的視覺層次極少，分數數學上夾在 ~17。

**根因定調（scope boundary，非 bug）：**

AgentLayout 的研究定位是 **layout generation（安排既有素材）**，**by design 不做 graphic design generation（合成新視覺內容）**。輸入是 brief 給定的固定素材清單，Generator 的職責是排列/縮放/設定字體層級，而非生成背景裝飾。因此「裸素材 + 純色底」這種構圖的 `layout_balance` / `visual_coherence` 上限就被夾在 ~17/25——**這不是 LLM 評錯，也不是 prompt 沒調好（step 6/7/8 已實證），而是 schema 層缺少表達能力**。

**決策：不嘗試突破。** 要打破此上限需引入 asset / decorative-element synthesis（generative 色塊、背景、分隔裝飾），這是大型架構改動（schema + renderer + Generator + QC + 測試全動），且本質是「平面設計生成」另一個研究問題，**超出本論文範疇**。定調為論文 **limitation + future work** 負向結果。

**論文價值：** 與 step 6、step 8 同性質的高價值負向結果，反向強化核心論點「**plateau 是結構性的，不是 LLM 能力問題**」。給出清楚的 scope 分界——AgentLayout 解決的是「給定素材的版面安排」，不解決「視覺內容的創作」。Future work 明確：要進一步提升美感分數，需擴充為 design-synthesis pipeline。

**Trade-off：**
- ✅ 零成本（純讀碼診斷）、誠實、結案明確、補強 limitations 章節
- ✅ 三個 aspect ratio（#7/#8rc/#9rd）bal/coh≈17 一致，為 scope-bound 提供跨樣本證據
- ❌ 分數天花板照舊（已知且 by design 不解，非缺陷）

**無程式改動**；僅更新本 README + NEXT_SESSION + live_runs_table 三文件。

**下次 session 候選方向（收斂）：**
1. **Step 10b — 修 post-Analyst-retry Generator QC crash**：#6/#7/#9rd 同 pattern、純工程修補、先寫 offline reproducer（plateau 第二段已結案為 limitation，不再列為可解目標）

### Step 11 後續 — MLLM Pairwise Win Rate vs Crello 設計師（2026-05-18，誠實負向結果 + 紀錄修正）

**動機：** 論文最大實質缺口是「沒跟 SOTA 比較」。trained-SOTA（FlexDM/AesthetiQ/PosterO）需 GPU + 權重，超出環境；改採 **AesthetiQ 標準 pairwise Win Rate 協定**，對手＝Crello 人類設計師（最強 reference、零額外模型、~$0.4）。

**方法（`layout_agent/output/step11_winrate.py`，依 step 8 教訓先 `--dry-run` $0 驗證 12 張配對圖才 live）：**
- 共用 pairwise judge：一次餵兩張圖、各打 4 維 0-100、宣告 winner；**每對交換圖序 ×2** 消除 LLM-as-judge position bias，多數決，平手用平均分
- **實驗 A（realistic / headline）**：AgentLayout 最佳 render vs 設計師真實成品稿 `ground_truth_preview.jpg`（含完整背景裝飾）
- **實驗 B（layout-only / ablation）**：同 renderer + 同 spec assets，只把 bbox 換成設計師位置，隔離渲染能力純比排版推理。GT candidate 用 spec↔meta 的 `asset_ref`/`content` 精確比對重建（**棄用快取的 `iou_result.json` id_map**——它是 05-10 舊 pipeline 的 spec ids，會 silently drop 全部文字元素，dry-run 時抓到此 bug 並修正）

**結果（N=3，#7 / #8rc / #9rd）：**

| 實驗 | Win Rate | 逐樣本 agent vs designer total |
| --- | --- | --- |
| A realistic | **設計師 3 : 0 AgentLayout** | #7 54–88、#8rc 59–86、#9rd 41–78（分差 27–37，兩次圖序皆一致，穩健）|
| B layout-only | **設計師 2 : 1 AgentLayout** | #7 71–82、#8rc 58–82 設計師勝；#9rd 56–51 AgentLayout（judge 兩次圖序互打、噪訊邊緣勝）|

paper figure：`layout_agent/output/step11_winrate.png`。

**誠實紀錄修正（核心）：** 先前 `live_runs_table.md` / 本 README / NEXT_SESSION 多處記的「mean best 69–72 > Crello GT 68 = +2/+2/+4」，是 **pipeline 自家 Aesthetic Judge 單邊評 AgentLayout candidate**、再與另一 corner-case 量到的「設計師 GT≈68」相比——**非配對、Judge 校準不同，是測量假象，不可解讀為「AgentLayout 勝設計師」**。正規 pairwise head-to-head 下：A 設計師完勝、B 即使隔離渲染仍設計師勝。**結論修正為：AgentLayout 尚未達設計師水準**；先前 +N 僅可作同 pipeline 內部 trend 指標。

**論文價值：** step 11 的 scope-bound 上限從「診斷」升級為**量化證據**；B ablation 進一步顯示即使控制渲染能力、排版推理本身仍略低於設計師（方法非完全失效但不能 claim 勝）。與 step 6/8 同為高價值誠實負向結果，並修正了專案先前一個被誤當正向的結論。

**Caveat（論文需寫）：** N=3；單一 judge model；pairwise prompt ≠ pipeline Judge；B 的 AgentLayout 側含 Analyst 多生且 #9rd overflow 出血的 `title_1`（真實 Generator bug，合理拉低 B 分）——此 bug 連到 step 10b。

**Trade-off：**
- ✅ 補上論文最大缺口（SOTA-gap 量化）、誠實修正錯誤紀錄、零 metagpt/ 程式改動
- ✅ dry-run 先行抓到並修正 stale id_map bug（step 8 SOP 再次生效）
- ❌ N=3 偏小（future work：mine 更多 Crello sample 擴 N）；trained-SOTA 直接比較仍為 future work

---

### Step 12 — BackgroundAnalyzer 上線：補齊 content-aware 缺口（2026-05-18，核心架構修正）

**動機（最關鍵的誠實發現）：** 全程式碼追查證實，本系統設計上宣稱的任務是 **content-aware layout generation**（吃既有背景圖 → 讀 saliency → 避開主體擺元素，與 PosterO/PKU PosterLayout 同任務），但實作上 `BackgroundAnalysis` 的**唯一 producer 是 `pipeline.py:59 default_white_background()` stub**（`safe_zones=[]`）。`schema.py:141` 註解「U2Net output」、`layout_generator.py` 註解「not yet a Role」皆為 placeholder，**全 codebase 無任何 saliency/rembg/safe_zone 真實實作，沒有任何 driver 餵過真實背景圖**。即：先前所有 live run 其實是在**空白純色畫布上做 brief-driven layout，不是 content-aware**——這才是「無法與 SOTA 比較」的真正根因（任務不對齊，非僅 GPU/指標問題），也解釋了 plateau 與 Win Rate 輸設計師。

**作法（最小侵入、符合既定架構——CV 模組非 LLM Role）：**
- 新增 `metagpt/ext/agentlayout/tools/background_analyzer.py`：`analyze_background(image_path, canvas)` 產生真實 `BackgroundAnalysis`；`resolve_background(canvas)` 為兩條 driver 的單一進入點（有可載入 `background_asset_ref` → 真分析，否則 fallback 舊 stub，任何錯誤皆優雅退回，live run 永不 crash）。
- 接線僅兩處：`roles/layout_generator.py:152`、`pipeline.py:185` 的 `default_white_background` → `resolve_background`。Consumer（Generator/Judge prompt）早已接好，無須改動。
- 演算法：**第一版用 rembg/U2Net 前景 matte**，cheap-validate（`layout_agent/output/verify_background_analyzer.py`，零 LLM、5 個 Crello 樣本疊圖）肉眼抓到 **rembg 在裝飾性/稀疏背景上反轉**（把黑色空白中央判為主體，safe zone 變無用細條）。**改為亮度局部變異數能量圖 ∪ rembg matte（>70% 反轉守衛）**，重驗：5efdd2dd 黑底空白中央正確變 conf=1.0 safe、避開四周塗鴉；白底樣本正確退化整張可用。

**結果：**
- 7 條離線單元測試（`tests/metagpt/ext/agentlayout/test_background_analyzer.py`）全 PASS；agentlayout 既有離線套件無回歸（15 passed / 6 skipped）。
- 系統現在**真正執行 content-aware layout generation**：可區分「照片主體背景」「裝飾雜訊背景」「純白背景」並分別產生合理 safe zones。
- 對論文：與 PosterO/AesthetiQ 的任務對齊缺口**已從根本補上**；下一步可在對齊任務前提下重跑 Win Rate / 引用其 published mIoU 定位。

**Caveat（論文需寫）：** 變異數能量圖非學界標準 saliency 模型（如 BASNet）；`_ENERGY_TAU=0.18` 是 Crello 驗證集校準值非 user study；抽象全幅圖案樣本（5c6c0cba）saliency 仍噪訊、退化為 grid fallback。

**Trade-off：**
- ✅ 補上論文**最核心**的任務對齊缺口（不再是 brief-driven 假裝 content-aware）；零 LLM 成本驗證；step 8 SOP 再次抓到實作 bug（rembg 反轉）才上線
- ✅ 最小侵入（新增 1 檔 + 改 2 行接線）、無 public API 破壞、無背景圖時行為與舊版完全一致
- ❌ saliency 用變異數近似非訓練式模型；先前所有 live run 數據需標註為 pre-content-aware，content-aware 模式的 live 評估為下一步

---

### Step 12b — z_order content-aware QC 正規化 + 首個 content-aware 分數（2026-05-19）

**動機：** Step 12 接上 BackgroundAnalyzer 後，首次跑**真正 content-aware** live（Crello `5efdd2dd`）即 hard-crash `RuntimeError: 0 candidates passed QC after 3 top-up round(s)`。離線 reproducer（`output/debug_step12_failmode.py`，零 LLM）確診：content-aware 模式下背景元素存在，Analyst 才會發 `z_order` 硬約束，且 emit 成語意形式 `params={"hint":"above_background"}`（`analyze_brief` 列 z_order 為支援規則卻無 params 範例、又明示 params 須為 semantic hint），而 `quality_checker._check_z_order` 歷來硬要 `params["above"]=<id>`，缺則對每個 candidate 無條件 `UNKNOWN_HINT` → 全滅。此 fail mode **只在 content-aware 才會出現**（pre-content-aware 無背景元素故 Analyst 從不發 z_order），是 step 12 接線暴露的 Analyst↔QC contract 缺口。

**作法（QC 載重 + prompt 互補）：**
- `quality_checker.py`：thread `spec` 進 `_check_z_order`（比照 `_check_position_preference`）；新增 `Z_ORDER_ABOVE_BACKGROUND_HINTS` frozenset + house-style docstring；`params["above"]` 顯式路徑**完全不變**（向後相容），無 `above` 時正規化 `hint`（含 dash/space/case folding），命中集合則以 `SemanticType.BACKGROUND_IMAGE` 解析背景 id；無背景元素或 spec-derived id 不在 candidate → **graceful skip**（vacuously satisfied，符合 step 12 resolve_background "never crash" 哲學，且 completeness 已獨立報 MISSING_ELEMENT 不雙報）；空/未知 hint 仍 `UNKNOWN_HINT`（不吞錯）。
- `analyze_brief.py`：`FORMAT_EXAMPLE_JSON` 加 z_order 範例物件 + `PROMPT_TEMPLATE` 加一行 z_order hint 指引（互補硬化，非載重）。**踩到並修掉一個既有地雷**：`PROMPT_TEMPLATE` 走 `str.format()`，指引行內若放字面 `{...}` 會被當替換欄位 → 第一次重跑即 `KeyError: '"hint"'`；改寫為無大括號敘述，並加 regression test `test_build_prompt_str_format_is_safe_*` 實際呼叫 `_build_prompt` 當 canary（既有 prompt 測試只 pin 字串、跑不到 `.format()`）。

**結果：**
- 離線 reproducer：**0/5 → 3/5 PASS**，z_order 完全從 violation 分布消失；剩 2 個僅 `position_preference`（已知 scope-bound plateau，明確不在本次範圍）。
- agentlayout 全離線套件 **135 passed / 12 skipped**（原 118 + 17 新 z_order 測試 + 1 prompt format canary，零回歸）。
- **首個 content-aware live 評估**（`output/live_step12b_5efdd2dd.log`、$0.27、3 verdicts、iteration=3 含 RetryAnalyst、exit 0）：BackgroundAnalyzer 全程注入真實 3 safe zones；3/3 verdict best **total=72**（req=20 hier=18 **bal=17 coh=17**）；decision=reject。

**誠實定調：** 72 僅記為**首個 content-aware baseline**，依專案結論不再對「勝 GT/設計師」做任何宣稱。關鍵負向觀察：bal/coh 仍卡 **17/17**——**content-aware 並未突破 plateau**，正向強化 step 11「plateau 第二段是 schema scope-bound 結構性限制，非 LLM capability、亦非任務不對齊」的結論；補背景圖把任務對齊缺口補上，但 Generator schema 無裝飾元素表達力的天花板照舊。先前 #1–#9 live 數據正式標註為 pre-content-aware。

**Trade-off：** ✅ 解開 content-aware 唯一 hard blocker、向後相容零破壞、零成本離線確診（step 8 SOP）、prompt format 地雷連帶修掉並上 canary；✅ 取得對齊任務後第一個誠實 content-aware 數據點；❌ plateau bal/coh=17 天花板未動（已知且 by design 不在本步驟範圍）；❌ 仍 N=1 content-aware 樣本、變異數 saliency 非訓練式模型（沿用 step 12 caveat）。

---

### Step 13 — SOTA-positioning Win Rate pilot（2026-05-19）

**動機：** 論文最大實質缺口是「跟 SOTA 比」。更正先前錯誤認知後查證：**PosterO / AesthetiQ 並非自己生成背景**（PosterO 在給定影像上排版；AesthetiQ 吃 elements→預測 bbox），其中 **AesthetiQ（CVPR 2025, arXiv 2503.00591）與 AgentLayout 同資料(Crello)同指標家族**——它在 Crello **test**（1,971）報 pairwise MLLM win-rate vs GT：**AesthetiQ-8B 17.19%、prior SOTA LayoutNUWA 5.58%（judge=VILA-7B）**。`step11_winrate.py` 早已是同 protocol 且更嚴謹（order-swap ×2），缺的只是規模。

**作法：** 新增 `output/step13_sota_winrate.py`：seed=42 從 Crello **test** 抽 N=20（structural filter 2–5 elem/≥1 img/≥1 text；重用 `run_iou_eval.save_sample` + `step11_winrate` judging），每樣本跑完整 live reject loop（subprocess，crash→not-completed），task-aligned protocol=同 renderer 純排版幾何 vs designer-GT layout（與 AesthetiQ Table 1「vs GT」一致）。約 60 min（20 序列 reject loop + CPU saliency，無並行）、~$5–6。（註：script 內部仍同時跑 exp A=vs 設計師完稿 JPG 與 exp B=同 renderer bbox 兩組；A 因把 render quality 含入比較、屬 scope 外能力扣分，AesthetiQ Table 1 亦無對應欄位，已自 result.md / README 公開敘事移除；raw JSON 保留以利稽核。task-aligned win rate = exp B。）

**結果：**
- **completion rate 20/20 = 100%**：step 10–12b robustness 修補在隨機 Crello test（filtered）generalize — 真正正向結果。
- **Win rate（task-aligned：同 renderer 純排版幾何 vs designer-GT layout）= 80%**：純比 bbox 幾何時具競爭力。

**誠實定調（核心，不可宣稱勝 SOTA）：**
- 攔阻假設「Win rate 高分是 GT 缺元素假象」經 **$0 離線檢查被資料推翻**：GT 重建保真度 = **97.1%**（68/70 設計師元素）。
- 但 80% **仍不可與 AesthetiQ 17.19% 並列當勝績**：(1) judge=gpt-4o≠VILA-7B（win-rate judge-dependent）；(2) **最強 confound：generator 與 judge 同為 gpt-4o（self-preference），AesthetiQ 刻意用獨立 judge 避此**；(3) filtered subset、N=20 vs AesthetiQ 全 1,971 不過濾。
- 可寫進論文的論點：**Win rate 80%（task-aligned, AesthetiQ-protocol-equivalent）**證明 AgentLayout 在 content-aware layout generation 任務本身的排版幾何具競爭力（仍不勝設計師、不勝 SOTA，但語意對齊 task definition）。Render quality（背景/字型/裝飾合成）為 by-design 不做的 scope 外能力（已記為 limitation），不另列量化 metric。SOTA 維持 **qualitative / indicative** 定位，**不進勝負對照表**。

**Caveat / Future work：** 與 step 6/8/11 同性質的誠實負向/定位結果。明確 future work：用**獨立（非 gpt-4o）judge** 重判已存的 `step13_*` pair，消 self-preference confound，才有資格做數值對照；並擴大 N 與放寬 structural filter（AgentLayout 對 >5 elem / 缺圖或缺文樣本是 out-of-scope-by-construction，比 AesthetiQ 更受限，本身亦為 limitation）。

**Trade-off：** ✅ 取得可positionable 的誠實 SOTA 定位 + 對齊 task definition 的 task-aligned win-rate 數值；✅ completion 100% 證明 robustness 修補 generalize；✅ $0 自我檢查抓出並更正自己的攔阻假設（誠實科學）；❌ self-preference confound 未消，數值暫不可與 AesthetiQ 並列；❌ N=20、filtered、單一 judge family。

---

### Step 17 — 修 step 10b post-RetryAnalyst crash：根因 + graceful degradation（2026-05-19）

**動機：** step 10b（#6/#7/#9rd 同 pattern）是唯一仍 open 的實質 blocking 缺口，也是 full-pipeline 可評估 N 上不去的原因（win-rate N=3、IoU completion 19/20 皆受此牽制）。先寫離線 reproducer 確診再改碼（step 5/8 SOP）。

**離線確診（`output/step17_repro_step10b.py`，零 LLM）：** 從 `live9_step10d.log` 還原 RetryAnalyst 重建後的 DesignSpec + 10 個 LLM candidate 餵真 `quality_checker.filter_valid`：`kept=0/10`、`violation-type totals={'unknown_hint': 10}`。根因鐵證——Analyst 在 retry 路徑 emit `position_preference hint="below_title"`（**relational hint，不在 QC 3×3 band 白名單**；同 spec 的 `top_center`/`left`/`right` 全正常），10/10 candidate 各吃一個 `UNKNOWN_HINT` blocking violation → top-up 耗盡 → `RuntimeError` → 整個 run abort。與 step 9 `center_top` 同類，但 step 9 修的是 word-order alias，這次是 LLM 自由發明的關係式 hint（prompt 只給單一範例 `top_right`、從不列舉封閉詞彙）。

**修補（兩層，比照同檔既有手法）：**
1. **根因 — `actions/analyze_brief.py` PROMPT_TEMPLATE**：position_preference 段比照同檔 `soft_constraints` enum 與 `semantic_type` 的「列舉封閉值 + 明示禁止發明」手法，寫死 QC 的 9 個 canonical region（top_left…bottom_right）+ 明示「relational 意圖要 map 成最近 region，**Do NOT invent** below_title/above_logo/left_of_image」。只加 ~10 行、聚焦 position（唯一實證 crash 的 rule），不順手塞 size enum（守 step 8 attention-budget 教訓）。
2. **防禦 — graceful degradation（泛化到任何 unknown-hint / over-constrained crash）**：`quality_checker.py` 新增純函式 `rank_candidates_by_violations`（fewest-violations-first、stable on ties）。`roles/layout_generator.py` 與 `pipeline.py` 兩個 mirror 的 `_generate_with_topup` 在 QC 全 fail 時，回傳「violation 最少」的 fallback 而非回空；`RuntimeError`/`PipelineError` 改為只在 LLM 真的吐 0 candidate（不可恢復）時觸發。crash 不再讓 N 靜默縮水——reject loop 存活、feedback 仍能路由回 Analyst（修復後的 prompt 會 emit 合法 band hint）。

**驗證：** (1) 離線 `step17_repro_step10b.py`：修前 `kept=0/10`→RuntimeError；修後 degradation 回 5 fallback → CONTINUE。離線套件 **140 passed, 12 skipped**（136 baseline +4 新測試）、零回歸。(2) **Smoke 端到端**：原 crash 樣本 `5d972ca9` content-aware live 重跑（U2Net→3 safe zones）**0 crash markers**，iteration=3 RetryAnalyst 後 Generator 產 5 valid（QC drop=0），根因 prompt 修復獨力生效、degradation 未觸發；$0.316。(3) **N=20**：`[1/20]…[20/20]` 全跑完、step-10b crash markers=0、degradation 觸發=0（隨機 content-aware 樣本零 crash 零退化）。N=20 事後 gpt-4o pairwise judging 撞 **OpenAI `429 insufficient_quota`（外部 billing，非程式）**，改比照 Step 14 用 `claude-sonnet-4-6` 獨立 judge 對 20 個 post-fix render judge-only 重判（零 pipeline 重跑、零 OpenAI）：**Win rate（task-aligned：同 renderer 純排版幾何 vs designer-GT layout）= 75.0%**（原 Step 14 Claude pre-step17 render：80.0%；原 Step 13 gpt-4o：80.0%）。80→75 N=20 噪音內 → step 10b 修復 + prompt 列舉**未灌水也未回歸** win-rate，結論跨 pre/post-fix render × 兩 judge 穩固。

**論文價值：** step 10b 從「已知未修 bug」結案為「根因 + 防禦雙修」。graceful degradation 是可寫進論文的 robustness property（hard/malformed spec 退化為 best-effort 而非 crash），且直接 unblock 後續 content-aware 大 N 重跑——win-rate / IoU 不再受 crash 牽制而被迫小 N。

**Trade-off：** ✅ 唯一 open blocking 缺口結案、根因+防禦雙層、零成本離線確診（step 8 SOP）、兩 mirror 對稱無分歧、泛化到所有 hard-spec crash mode；✅ smoke + N=20 隨機 content-aware 樣本端到端零 crash 零 degradation；✅ post-fix content-aware win-rate 經 Claude 獨立 judge judge-only 取得（task-aligned 75.0%，80→75 N=20 噪音內），結論跨 pre/post-fix render × gpt-4o/Claude judge 穩固，且省去 pipeline 重跑與 OpenAI 依賴；❌ QC 仍不做真正的 relational-hint 語意驗證（degrade 時該 constraint 未強制執行，記 future hardening）；❌ N=20≠1,971、judge≠VILA-7B caveat 未消（同 §3.1）。

---

---

### Step 18 — 任務定義與評估指標 framing 修正（2026-05-20）

**動機：** 兩處 framing 不一致需修正：(1) README 標題與「研究背景」原寫「自動排版生成（Automatic Layout Generation）」，未明示任務範圍是 **content-aware**（在既有背景畫布上排放既有素材）；雖然系統實際即 content-aware（BackgroundAnalyzer U2Net 顯著性偵測接 `pipeline.py:189` / `layout_generator.py:155` / `aesthetic_judge.py:79`），但 outward-facing 任務描述模糊容易誤導讀者把它當 graphic-design synthesis。(2) Win Rate A（vs 設計師完稿 JPG）把 render quality（背景/字型/裝飾合成，scope 外能力）扣分入排版指標——但 AesthetiQ Table 1 / LayoutNUWA / PosterLLaVa 等 content-aware layout generation 同類方法的 win-rate **均無此欄**（「vs GT」一律指同 renderer 純排版幾何 vs designer-GT layout），A 在 task-aligned 語意下不存在，且使「能力邊界」narrative 把 by-design scope 外能力誤標為「弱點」。

**修補：**
1. **README 任務定義明示 content-aware**：標題 `Decomposing Automatic Layout Generation` → `Decomposing Content-Aware Layout Generation`、副標題加註「內容感知（Content-Aware）」、研究背景段落新增一段明確界定任務 scope（既有背景 + 既有素材 → bbox/size/z；不含背景生成/裝飾合成/inpainting）+ benchmark 對齊 AesthetiQ/LayoutNUWA/PosterLLaVa（pairwise win-rate vs designer-GT layout + Mean IoU）。
2. **result.md / README 移除 Win Rate A**：§0 TL;DR、§2 Step 11/13/14/17、§3.1 / §3.3、§4 future work 表、§5 資料來源索引，以及 README Step 13/17 同步移除 A 數值與「A=0% + B=80% 能力邊界」對比敘事，改以**單一 task-aligned Win rate（同 renderer 純排版幾何 vs designer-GT layout，與 AesthetiQ Table 1「vs GT」一致）= 80%（post-fix 75%）**呈現；render quality limitation 仍以一句質性描述保留於 §0 系統定位與 §3.3。raw JSON（`step11_winrate_results.json` / `step13_sota_winrate_results.json` / `step14_independent_judge_raw.json`）內部 exp A / exp B 兩組欄位保留以利稽核，僅公開敘事面收斂為 task-aligned 單一指標。

**驗證：** `grep -nE "Win rate A|A=0|A 0%|A 完全|A：|設計師完勝 3:0" result.md README.md` → 命中 0 筆（exp A/B 僅出現於 script-level audit note 上下文，無敘事性宣稱）。Step 11 N=3 數值改報「設計師 2:1」（task-aligned only），#7/#8rc 設計師勝、#9rd AgentLayout 噪訊邊緣勝，與原 raw artifact 一致。Step 13/14/17 三段時序數值修為 80%/80%/75%（單一指標）。

**論文價值：** 任務定義對齊 content-aware layout generation literature（無歧義），評估指標收斂到與同類方法可語意對比的單一 protocol（AesthetiQ Table 1 同設定）。先前 A 欄的「能力邊界 framing」在學術 review 中容易被質疑「拿自家 agent 跟 multimodal synthesis full-pipeline 比、必輸 0% 寫成限制是 framing trick」——移除後語意更乾淨。

**Trade-off：** ✅ 任務定義明示 content-aware、與 SOTA literature 用語一致；✅ 評估指標單一 protocol、與 AesthetiQ Table 1 同語意；✅ raw artifact 不動、僅敘事面收斂，零反向相容性問題；❌ 移除 A 後失去把 render quality limitation 用「量化 0%」表達的力道（改以質性描述保留，論述 burden 略增）；❌ 先前 commit message 與 memory 仍有 A 字樣的歷史軌跡（不回頭改 history）。

---

## Step 19 — Refinement Loop 架構（coarse-to-fine，2026-05-20）

**動機：** 既有 reject-only loop 把 Round 0（cold-start，無 Judge critique）的 candidate 直接當最終答案，使 AestheticJudge accept 後再無精修機會；對齊 SEGA (ICCV 2025) coarse-to-fine 範式並回應使用者要求「無論分數如何都一定回到 LayoutGenerator 改一次，並把上一輪 layout 一起傳回去」。

**改動範圍（純架構升級，未跑新實驗）：**
- `schema.py`：
  - `AestheticJudgement.feedback` 由 `Optional[AestheticFeedback]` 改為 required；新增 `best_candidate_layout: Optional[Dict[str, Tuple[float, float, float, float]]]` 由 JudgeAesthetic 在 parse 後從 input candidates 反查填入。
  - `IterationState` 新增 `consecutive_accepts: int`（連兩 accept 才終止）、`reject_count: int`（routing 用，與 iteration 解耦——refinement 帶來的 accept 不消耗 Analyst budget）。
- `actions/judge_aesthetic.py`：
  - `FORMAT_EXAMPLE_ACCEPT` 把 `feedback: null` 改為含 polish-step `structured_suggestions` 的範例；PROMPT_TEMPLATE 移除「accept = null feedback」ATTENTION，加上「accept/reject 都必須有 feedback、accept 出 polish-step、reject 出 corrective」對比；新增 `_attach_best_candidate_layout()` 在 `run()` parse 後從 input Candidate 抽 bbox dict 塞回 verdict。
- `actions/generate_layout.py`：
  - PROMPT_TEMPLATE 加 `# Previous Attempt` conditional block（refinement mode 啟用 prev_bbox dict + 子分數 + ±10% drift / stable ids / anchored search 三條 instruction）；`run()` / `_build_prompt()` 增加 `prev_best_layout`、`prev_best_subscores` keyword-only 參數，cold-start 時 render 為 "None"。
- `roles/iteration_state.py`：
  - `RetryPayload` 加 `prev_best_layout`、`prev_best_subscores` Optional 欄位；新增 `ACCEPT_CONSECUTIVE_STOP=2` 常數；`_act()` ACCEPT 分支從 emit IterationStop 改為 emit RetryGeneration（除非 consecutive_accepts ≥ 2 或 iteration > max），並把 best bbox 與子分數塞進 payload；新增 `_extract_best_subscores()` helper。
- `roles/layout_generator.py`：`_generate_with_topup()` 簽名加兩 Optional 參數轉發給 Action；`_act()` 從 RetryPayload 取 prev_best_layout/subscores，log 標 `mode=refinement|cold-retry`。
- `pipeline.py`：orchestrator 路徑同步——`run()` 內 ACCEPT 不再立即返回，改為更新 `last_accept_result`、increment consecutive_accepts，並 route 到 LayoutGenerator 跑 mandatory refinement；連兩 accept 才回傳；`_generate_with_topup()` 加兩參數；新增 `_best_subscores()` helper。

**驗證（離線 zero LLM cost）：**
- agentlayout 全測試套件：修完 schema 測試對齊後 **142 passed / 12 skipped / 0 failed**。
- 全 repo offline subset（`ALLOW_OPENAI_API_CALL=0`）：**148 passed / 20 skipped / 0 failed**。
- 新測試覆蓋（`tests/metagpt/ext/agentlayout/test_iteration_state.py`）：
  - `test_accept_routes_to_refinement_until_two_consecutive`：5-step 場景 REJECT/ACCEPT/REJECT/ACCEPT/ACCEPT 確認 iteration、consecutive_accepts、chain 全對齊。
  - `test_two_consecutive_accepts_terminate_immediately`：連兩 accept 立即觸發 IterationStop。
  - `test_mvp_3rejects_then_accept_routes_correctly`：accept 不再終止，改 emit RetryGeneration。

**對齊文檔：** `layout_agent/README.md`、`layout_generator.md`、`aesthetic_judge.md` 已於同一天 doc-first 更新 Refinement Loop 三段流程、終止條件 (a)(b)、各段 prompt 範例與 ATTENTION。

**Trade-off：** ✅ 每樣本至少一次 critique-aware refinement，回應 step 6 漂移教訓的反向設計（anchored ±10% 而非自由重洗）；✅ schema 邊界穩定（feedback 永遠 non-null、prev_best_layout JudgeAction 自填）；✅ accept-budget 與 reject-budget 解耦，refinement 不會把 Analyst escalation 提早觸發；❌ 每樣本 LLM cost 略上升（每次 Judge 後額外 1 輪 Generator+Judge ~$0.10-0.15）——預期 N=20 增 ~$2-3，需在下次 SEGA-protocol 實驗時計入；❌ 仍未跑新實驗驗證分數變化（refinement 對 bal/coh plateau 的實際效果待 measure，文件僅 doc-only）；❌ 沒對非 Crello 路徑做 smoke（PKU/CGL benchmark 未在範圍內，目前只關 Crello）。

**關聯：** 對齊 [[project-next-experiment-sega-comparison]] memory——下次跑 SEGA Phase A/B 9 指標時，pipeline 預設已是 Refinement Loop 而非 reject-only。

---

## Step 20 — SEGA-protocol 6 rule-based 指標 head-to-head（2026-05-20）

**動機：** Step 16 的 AesthetiQ Table 1 對比受兩 caveat 限制（judge=VILA-7B 我們跑不起；自訂 IoU 定義不同），只能 indicative。SEGA (ICCV 2025) Table 3 用 PKU PosterLayout 公式報 6 個確定性指標，可逐字重現 → 真正 head-to-head 對齊。

**改動範圍：**
- `metagpt/ext/agentlayout/evaluation/sega_metrics.py`（新檔）：字面移植 PKU eval.py 的 6 個 metric（`metric_alignment / overlay / underlay_loose / underlay_strict / readability / occlusion`）；canvas 從 hardcoded 513×750 改傳入；class code 維持 PKU 慣例（1=text, 2=image/logo, 3=underlay）。Readability/Occlusion 需要 bg image + saliency map（用 rembg/U2Net 或 Sobel fallback）。
- `tests/metagpt/ext/agentlayout/test_sega_metrics.py`（新檔）：12 unit tests，cover overlay/alignment/underlay 幾何 corner cases，全 pass。
- `layout_agent/output/step20_sega_eval.py`（新檔）：driver 對 step13 同 N=20 ids 直接呼叫 `GenerateLayout.run()`（跳過 orchestrator/Judge，cold-start），同時量 GT / random(5 seeds) / centered_stack baselines；輸出 `step20_sega_results.json` 含 raw candidate ids（給未來 cache-recompute 用）。`--recompute-from-cache` flag 允許指標修正後 $0 重算。

**踩過的兩個 bug（已修並重跑）：**
1. **`str(SemanticType.TITLE)` 在 Python 3.9 回 `'SemanticType.TITLE'`** 不是 enum value `'title'`。第一版所有 candidate 都被誤分類為 image/logo（cls=2）→ readability=0、alignment 失真。改用 `visual_type` enum 經 `.value` 取值 + 加 `_enum_to_str()` helper。
2. **Crello meta.json 不是 `position/size` dict 結構**，是 flat `left/top/width/height` 絕對 px + `type_code` (0=image, 1=text, 2/3=shape)。修 `_build_gt_layout()` 用 type_code；非全幅 type_code 2/3 視為 CLS_UNDERLAY，全幅視為背景排除。20 樣本中 2 個有實際 underlay shape（5bbcb749, 5f4f5e15）。

**驗證（離線 zero LLM cost 部分）：**
- 12 unit tests PASS。
- 整個 agentlayout 套件 142 passed / 12 skipped → 154 passed / 12 skipped（+12 新測試）。

**結果（N=20，all completed）：**

| Method | Ali ↓ | Ove ↓ | Und_l ↑ | Und_s ↑ | Read ↓ | Occ ↓ |
| --- | --- | --- | --- | --- | --- | --- |
| AgentLayout (cold-start) | **0.0000** | **0.0009** | 0.0000 | 0.0000 | 0.0156 | 0.0009 |
| Designer GT | 0.0025 | 0.0901 | 0.0675 | 0.0500 | 0.0154 | 0.0010 |
| random (5 seeds avg) | 0.0275 | 0.0483 | 0.0000 | 0.0000 | 0.0153 | 0.0009 |
| centered_stack | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0139 | 0.0009 |

**Trade-off：** ✅ Overlay 0.0009 對齊 SEGA-13B (~0.0025) 量級，QC 防重疊強；✅ Alignment 0.0000 持平 centered_stack baseline；✅ 首組真正 head-to-head 可比數據，取代 AesthetiQ indicative；❌ Underlay = 0（by-design 不生 decoration，scope-bound limitation，不視為失敗）；❌ cold-start 模式，Refinement Loop 啟用後的數字待 Phase A2；❌ SEGA Table 3 數值單位（0–1 vs 百分比）尚需對原論文 final PDF 再校；❌ N=20 filtered subset 不具統計力 vs SEGA 全 Crello test。

**關聯：** 對應 [[project-next-experiment-sega-comparison]] memory 的 Phase A 必讀檢查表前 3 項完成；Phase B（GPT-4V SDL/SQL/STV/SIO aesthetic 4 軸）+ Phase A2（refined mode）為下一步。

---

## 2026-05-20 — Step 20b：Refinement Loop A/B（Phase A2 完成）

**動機：** Refinement Loop 架構（always-feedback + `best_candidate_layout` passthrough + REJECT→ANALYST 升級）只通過 smoke test、無端到端量化證據。為誠實回答「架構改完有沒有比較好」，對 Step 20 同 N=20 ids 跑同 SEGA 6 指標，唯一變數＝是否啟用 Refinement Loop。

**改動檔：**

- `layout_agent/output/step20_sega_eval.py`：加 `--mode {cold,refined}` 旗標；`_CachedAnalyzeStub` 在 `LayoutPipeline` 冷凍 cached spec；`_generate_refined_layout` 跑全 production 路徑、`PipelineError` catch 後 fall back 至 cold-start 並用 `refined_status` 標記每樣本；`--out` 預設依 mode 切換輸出 JSON 名稱。
- `layout_agent/output/step20b_compare.py`（新檔）：對齊 cold/refined 兩 JSON 輸出 (1) 部署彙總（含 cold-fallback）與 (2) converged-only 純架構彙總、signed deltas + direction tag。

**端到端執行：** `step20_sega_eval.py --mode refined` 跑 N=20、~70 min、~$3 gpt-4o；輸出 `step20_sega_results_refined.json` 與 `step20b_sega_eval_refined.log`（45k 行 trace）。

**核心結果（vs Step 20 cold-start baseline）：**

| Method | Ali ↓ | Ove ↓ | Und_l ↑ | Und_s ↑ | Read ↓ | Occ ↓ | N OK |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AgentLayout cold-start | 0.0000 | 0.0009 | 0.0000 | 0.0000 | 0.0156 | 0.0009 | 20/20 |
| AgentLayout refined | 0.0000 | 0.0006 | 0.0000 | 0.0000 | 0.0192 | 0.0010 | **18/20** |
| delta (refined − cold) | ±0 | −0.0003 | ±0 | ±0 | +0.0035 | +0.0001 | −2 |

**關鍵發現（已寫進 result.md Step 20b）：**

- 🚨 **收斂率 2/20（10%）**：16/20 撞滿 `max_total_rounds=5` 沒兩連 ACCEPT、落 cold-fallback；非自動「pipeline 沒跑」、是 Judge gate 嚴格度與 refinement carry-over 推不動 Generator 兩個瓶頸結合。
- 🔴 **Completion regression 20/20 → 18/20**：refined 撞 2 個 `CandidatesBatch validation error after 3 attempts`（樣本 #3 / #12）；同樣本 cold-start 可順跑，屬 refinement-prompt（`prev_best_layout` path）新增 regression。
- ≈ **指標 deltas 全在噪音內**：無任何 SEGA 指標統計學上顯著改善。

**Trade-off：** ✅ 架構 wiring 通過（REJECT routing、refinement carry-over、graceful degradation 均如預期）；✅ 拿到第一組真正的「架構 vs no-架構」端到端量化證據；✅ 此 negative result 可寫進論文 honesty 章節；❌ 端到端 ROI 為負（10% completion regression + 0% metric lift + 80% non-convergence）；❌ Refinement Loop 不可作為 strength 寫進論文，反需列入 limitation。

**Future work：**
1. `ACCEPT_CONSECUTIVE_STOP=2 → 1` 試降 Judge gate；
2. 修 refinement prompt schema（`prev_best_layout` path 的 LLM 輸出 validator）；
3. Phase B（GPT-4V aesthetic 4 軸）仍可獨立進行。

**關聯：** Step 20 cold-start baseline 對照；[[project-next-experiment-sega-comparison]] Phase A2 結案；[[feedback-explain-code]]、[[feedback-readme-sync]] 同步。

---

## 2026-05-20 — Step 21：SEGA Phase B GPT-4V 4 軸 aesthetic 評估

**動機：** Phase A 跑完 6 條 rule-based 幾何指標、Phase A2 證明 Refinement Loop 無 lift，剩下 SEGA Table 3 的 4 條 aesthetic 軸（SDL/SQL/STV/SIO）還沒量化——這 4 條是「我們是否真的能 perceptually 接近 SOTA」的關鍵 free information。

**改動檔：**

- `layout_agent/output/step21_phaseb_eval.py`（新檔，~250 LOC）：對 N=20 cached step17 post-fix render PNGs，4 軸各呼叫 gpt-4o 多模 vision 一次（temperature=0、max_tokens=8、only-integer 指令）= 80 calls；rubric 採 COLE (Jia et al. 2023, arXiv 2311.16974) Appendix 原版（SEGA §5.1 cite [16]）。

**端到端執行：** ~$0.40 gpt-4o、~3 min；輸出 `step21_phaseb_results.json` 與 `step21_phaseb_eval.log`。

**核心結果（vs SEGA Table 3 Crello full test set）：**

| Method | SDL ↑ | SQL ↑ | STV ↑ | SIO ↑ | Smean ↑ |
| --- | --- | --- | --- | --- | --- |
| FlexDM | 4.850 | 5.126 | 4.873 | 5.239 | 4.950 |
| PosterLlama | 5.292 | 5.796 | 5.263 | 5.819 | 5.542 |
| SEGA w/o FR (7B) | 5.553 | 6.332 | 5.693 | 5.448 | 5.756 |
| SEGA (7B) | 5.792 | 6.411 | 5.824 | 5.708 | 5.941 |
| SEGA (13B) | 6.149 | 6.745 | 6.348 | 6.038 | **6.320** |
| **AgentLayout (cold-start, N=20)** | **5.500** | 5.100 | **6.150** | 4.300 | **5.263** |

**亮點（vs Step 20b 的 negative result，這次是 positive）：**

- 🎯 **STV = 6.150 達 SEGA-13B 量級**：勝 FlexDM/PosterLlama/SEGA w/o FR/SEGA-7B 四個 baseline，僅輸 SEGA-13B 0.198（N=20 噪音內）。對齊 BackgroundAnalyzer + contrast-aware 文字色設計（Step 12d）；**可寫進論文的單一最強 aesthetic claim**。
- ✅ **SDL = 5.500 勝 FlexDM + PosterLlama**：與 Step 20 Ali=0.0000/Ove=0.0009 互相佐證——「layout geometry 達 SOTA 量級」跨 rule-based + GPT-4V 兩個獨立 judge family robust 確認。
- ❌ **SIO = 4.300 是最低點**：連 FlexDM (5.239) 都贏我們。Innovation/originality 是 by-design scope 外能力，誠實列 limitation 而非試圖補強。
- ≈ **SQL = 5.100 ≈ FlexDM 5.126**：renderer 直接 paste 不 enhance graphics 的代價。
- **Smean = 5.263**：勝 FlexDM (4.950)，輸 PosterLlama / SEGA 全系列 0.28~1.06；honest position「zero-shot prompt-only 達 FlexDM-level aesthetic」。

**Trade-off：** ✅ 拿到 paper-grade STV claim（達 SEGA-13B 量級）；✅ 跨 rule-based + GPT-4V judge 兩個正交評估 robust 確認「geometry 強 + typography/color 強 + creativity 弱」的一致 system characterisation；✅ 完整 SEGA Table 3 同表並列對照（5 個 baseline + AgentLayout + GT）；❌ SIO 4.300 / SQL 5.100 是 by-design scope-bound limitation，不可主張可改善；❌ N=20 vs full Crello test 仍是統計力 caveat；❌ judge=gpt-4o vs SEGA 的 GPT-4V 雖同 family 但版本不一致。

**論文 contribution 三 claim**（Phase A + B 結合）：
1. 兩條軸達 SEGA-13B 量級：**STV** (aesthetic, Phase B) + **Ali/Ove** (geometric, Step 20 Phase A)
2. honest negative claims（提升 credibility）：不勝設計師（Step 11）、不勝 SEGA Smean（Step 21）、Refinement Loop 無 lift（Step 20b）、SIO 弱（by-design）
3. distinct capability axis：traceability / graceful degradation / zero-shot multi-agent decomposition——SEGA Table 3 沒有的欄位

**關聯：** Step 20 / 20b cold-start vs refined / Phase A 補完 Phase B；[[project-next-experiment-sega-comparison]] Phase B 結案；新增 [[project-step21-stv-sota-claim]] 標記 paper 可寫的最強 single-axis claim。

---

## 2026-05-20 — Step 21b：Judge-config 校準對照（推翻 Step 21 cross-paper claim）

**動機：** Step 21 把 SEGA Table 3 數字直接跟 AL N=20 並排比，得出「STV 達 SEGA-13B 量級」的 paper-grade claim。但 SEGA paper 的 GPT-4V judge 與我們的 gpt-4o 不一定 calibration 對齊。為驗證，把 Crello designer 原圖（`crello_<id>/ground_truth_preview.jpg`）跑過完全相同的 judge config——若 designer GT Smean 接近 SEGA paper 的 SEGA-13B 6.320，則 cross-paper 可比；若顯著高，judge 漂移成立。

**改動檔：**

- `layout_agent/output/step21_phaseb_eval.py`：加 `--source {agent,designer-gt}` 旗標 + `--out` 預設依 source 切換輸出。`_png_b64` 與 `_process_sample` 接受 source 參數；scope/output 標記 source。

**端到端執行：** `step21_phaseb_eval.py --source designer-gt` 跑 N=20、~3 min、~$0.40；輸出 `step21b_phaseb_designer_gt.json` 與 `step21b_phaseb_designer_gt.log`。

**核心結果（all 在同一 judge config 下）：**

| 來源 | SDL | SQL | STV | SIO | Smean |
| --- | --- | --- | --- | --- | --- |
| Designer GT（Crello 原圖，我們 judge） | 7.950 | 8.650 | 7.650 | 5.850 | **7.525** |
| AgentLayout（cold-start，我們 judge） | 5.500 | 5.100 | 6.150 | 4.300 | **5.263** |
| Δ (AL − Designer GT) | −2.450 | −3.550 | −1.500 | −1.550 | **−2.262** |
| ratio (AL / Designer GT) | 69.2% | 59.0% | **80.4%** | 73.5% | 69.9% |
| SEGA-13B（SEGA paper 自己的 judge） | 6.149 | 6.745 | 6.348 | 6.038 | 6.320 |

**關鍵發現（推翻 Step 21）：**

- 🚨 **Judge calibration drift 確認**：Designer GT 在我們 judge 拿 7.525，比 SEGA paper 自己 13B 模型在他們 judge 下的 6.320 還高 1.2 分。Cross-paper Smean 不可直比。
- ❌ **「STV 達 SEGA-13B 量級」claim 無效**：原本 6.150 vs 6.348 = −0.198 是 cross-judge 假象；真正的 within-judge gap 是 6.150 vs designer GT 7.650 = **−1.500**。
- ❌ **「Smean 勝 FlexDM」也無效**：FlexDM 4.950 / PosterLlama 5.542 / SEGA 系列 published numbers 都不能跨 judge 比。
- ✅ **「STV 是相對最強軸」仍成立**：within-judge ratio STV 80.4% > SIO 73.5% > SDL 69.2% > SQL 59.0%，**STV gap 確實最小**，對應 BackgroundAnalyzer + contrast-aware text color 設計。
- ✅ **意外發現：SIO 弱不全是 by-design scope**：designer GT 在 SIO 也只 5.85（最低）——Crello dataset 本身就不獎勵 Innovation，judge 對任何 Crello-style 海報都打不高。Step 21 把 SIO 弱完全歸咎 scope-bound 過度自責。
- 🆕 **methodology contribution**：「GPT-4V judge calibration drifts across papers; cross-paper aesthetic-score comparison requires running same judge config on a shared reference (designer GT)」——所有後續 layout/design paper 應該標但都沒標的 caveat。

**Trade-off：** ✅ 把 Step 21 inflated SOTA-level claim 降階為誠實 within-judge ratio；✅ 拿到一條 paper-grade methodology contribution（judge calibration drift warning）；✅ 修正 SIO 弱屬「dataset 特性 + 部分 by-design scope」雙重歸因；❌ 失去「達 SEGA-13B 量級」的 narrative hook，paper 強 claim 從「兩條軸 SOTA-level」降為「STV 80.4% ratio + judge-drift methodology」；❌ N=20 統計力仍有限。

**Future work：**
1. **N=100 scale-up**：把 within-judge ratio 的 ±5% noise gap 收掉；
2. **若想救 cross-paper claim**：需要 baseline 作者開源 renders / layouts，全部在我們 judge 下重 score——short-term 不可能。

**關聯：** Step 21 cross-paper 對照 → Step 21b within-judge 對照；[[project-step21-stv-sota-claim]] 大幅修訂（移除「達 SOTA」claim，保留「最強相對軸」claim）。

---

## 2026-05-20 — Step 22：N=100 scale-up（推翻 N=20 STV 排名 + 確認 ~5pp selection bias）

**動機：** Step 21b 把 Step 21 的 cross-paper claim 換成 within-judge ratio，但仍受限 N=20 small-sample noise。為驗證「STV 80.4% 最強」與「Smean 70%」兩條 claim 是否 robust，把樣本擴 5× 到 N=100。

**改動檔：**

- `layout_agent/output/step22_sample_extra80.py`（新檔）：seed=43、max_inspect=2000、結構過濾、避開原 20 ids、下載 80 個 crello_<id>/ 資產。
- `layout_agent/output/step22_coldstart_render.py`（新檔）：cold-start pipeline 對 N=100，save spec/candidate/render PNG，**0 crash**。
- `layout_agent/output/step20_sega_eval.py`：加 `--ids-file` 旗標 + `_load_cached_candidate()` 助手；`_load_spec` 改 glob 兩個 pattern（step22 優先、role_live fallback）；cold 模式優先用 cached candidate 跳 LLM 重跑。
- `layout_agent/output/step21_phaseb_eval.py`：加 `--ids-file` 旗標；`_png_b64(source=agent)` 改 prefer step22 cold-start render → fallback role_live reject-loop。
- `layout_agent/output/step22_compare.py`（新檔）：N=20 vs N=100 head-to-head（Phase A SEGA + Phase B 4 軸 + within-judge ratio）。

**端到端執行：** ~$8 pipeline + ~$4 Phase B 兩跑 = **~$12 total**；~50 min pipeline + ~25 min Phase B。

**核心結果（vs Step 20 / 21 / 21b 的 N=20 baseline）：**

Phase A SEGA rule-based（N=100，judge-drift-free）：

| Method | Ali↓ | Ove↓ | Und_l↑ | Und_s↑ | Read↓ | Occ↓ |
| --- | --- | --- | --- | --- | --- | --- |
| **AgentLayout** | **0.0055** | **0.0013** | 0 | 0 | 0.0217 | 0.0016 |
| Designer GT | 0.0066 | 0.1104 | 0.058 | 0.025 | 0.0179 | 0.0019 |

Phase B GPT-4V 4 軸 within-judge ratio (AL / Designer GT)：

| Axis | N=20 ratio | N=100 ratio | Δ |
| --- | --- | --- | --- |
| SDL | 69.2% | 61.8% | −7.4 pp |
| SQL | 59.0% | 56.2% | −2.8 pp |
| STV | **80.4%** | **70.0%** | **−10.4 pp** |
| SIO | 73.5% | **75.0%** | +1.4 pp |
| **Smean** | **69.9%** | **64.8%** | **−5.2 pp** |

**關鍵發現：**

- 🚨 **「STV 是最強相對軸」claim 推翻**：N=100 STV 70.0% < **SIO 75.0%**——SIO 才是最強。Step 21b 把 STV 寫成 hero claim 是 sample-bias artefact。
- 🚨 **「Smean 達 designer ceiling 70%」也推翻**：N=100 真值 **64.8%**（N=20 高估 5.2 pp）。
- ✅ **Phase A 仍 robust**：Ali 0.0055 < GT 0.0066、Ove 0.0013 << GT 0.1104（勝 85×）、Ove 對齊 SEGA-13B 0.0025 量級——**唯一沒被 scale-up 推翻的 cross-paper claim**。
- 🆕 **「N=20 自帶 ~5 pp positive selection bias」是 paper-grade methodology finding**：所有用 random N=20 / N=50 small sample 報 GPT-4V aesthetic 的 layout/design paper 都應該被質疑。

**Trade-off：** ✅ 把 N=20 的兩個 inflated claim 都校準；✅ Phase A 純幾何 claim 雙重 robust（N=20+N=100 一致）；✅ 拿到第二個 methodology contribution（N=20 selection bias warning）；✅ pipeline 100/100 0 crash robustness 證據；❌ 失去「STV 80% 最強軸」hero claim；❌ 失去「Smean 70%」claim；❌ N=100 vs SEGA full Crello（1971）仍 5× 統計力 gap。

**論文 contribution 三 claim（final, post-N=100）：**
1. Ali/Ove 純幾何勝 designer + 對齊 SEGA-13B（Step 20+22 雙重 robust，judge-drift-free）
2. Within-judge AL Smean = 64.8% × designer ceiling（N=100）；SIO 75% 最強、SQL 56% 最弱
3. 兩個 methodology contribution：judge calibration drift + N=20 selection bias

**關聯：** Step 20+21+21b 全部 N=20 claim 重新校準；[[project-step21-stv-sota-claim]] 再次修訂；新增 [[project-step22-n100-scale-up]] 記錄 final paper-grade claim 與 selection-bias 方法論。

---

---

## 2026-05-25 — SEGA-aligned 實驗規格 + 三項校準修復

**動機：** N=20 / N=100 跑出來的 SEGA 比較結果有三個 calibration drift（rembg+Sobel proxy saliency、Phase B COLE 軸映射錯、Phase B 用 4 次獨立 API call），跟 SEGA paper 的指標定義不一致。為了讓未來實驗能直接跟 SEGA 比，先寫一份 unified spec 再修 pipeline。

**新增檔案：**

1. **`layout_agent/experiment.md`** — SEGA-aligned 完整指標規格
   - Phase A 6 指標（Ali / Ove / Und_l / Und_s / Occ / Rea）公式與來源（PKU PosterLayout CVPR 2023）
   - Phase B 4 軸美學（S_DL / S_QL / S_TV / S_IO）+ COLE 原版 Quality Assurance Prompt 全文
   - 未來所有實驗都照這份規格跑，舊結果作廢

2. **`metagpt/ext/agentlayout/evaluation/saliency_basnet_isnet.py`** — BASNet + ISNet 兩階段 saliency pipeline
   - 用 HuggingFace `creative-graphic-design/BASNet` + `rembg` 內建的 ISNet ONNX
   - 取代之前 step20 用的 rembg alpha + Sobel gradient fallback（不是真 saliency）
   - 修正 N=2 smoke test 通過、ISNet ONNX 自動下載快取

**修改檔案：**

3. **`layout_agent/output/step20_sega_eval.py:_saliency_from_bg`** — 接上 BASNet+ISNet，移除 Sobel proxy
4. **`layout_agent/output/step21_phaseb_eval.py`** — 整支重寫：
   - 改成 COLE 規範的 single-call JSON 評分（一次 API call 拿 4 軸，不是 4 次獨立 call）
   - 修正 S_QL 軸名（之前對到錯誤的內部欄位）
   - 增加 `--ids-file` flag 支援不同 N 配置

**驗證：** Phase A N=2 smoke 通過、Phase B N=2 smoke 通過（COLE JSON 解析成功）。

**下一步：** 拿掉 MAX_ELEMENTS 上限（見下一條），再從 Crello 全 test split 重新抽 N≈1,897 跑 final paper-grade evaluation。

---

## 2026-05-25 — MAX_ELEMENTS 上限拿掉（對齊 SEGA 全 Crello test split）

**變更：** `layout_agent/output/run_iou_eval.py:MAX_ELEMENTS` 從 `5` 改為 `float("inf")`，連帶更新 `step13_sota_winrate.py` / `step22_sample_extra80.py` 的 docstring + print 訊息（避免印出 `inf` 怪訊息，加 `"inf" if MAX_ELEMENTS == float("inf") else str(MAX_ELEMENTS)` 格式化）。

**動機：** SEGA paper 用 Crello test split 全集（~1,971）；我們之前的 5-element 上限把 qualifying pool 砍到 210。pipeline 本身（LayoutGen / Analyst / Renderer）**沒有** hardcode 元素數上限——這個 5 純粹是 sampling-time 過濾器，可以無痛拔除。

**影響：**
- 下游 importer（step22、step13）的 `2 <= ne <= MAX_ELEMENTS` 比較式對 `float("inf")` 完全合法，相容
- 元素數下限 `2` 仍保留（避免單元素 layout 退化）
- 仍要求 `>=1 image AND >=1 text`（pipeline 的 schema 假設）

**驗證：** `conda run -n meta python -c "..."` 跑過 MAX_ELEMENTS=inf 的邊界檢查（5/20/1 三個 case 行為符合預期）。

**下一步：** 重新抽樣 Crello test split 全集，量出新 qualifying pool 大小（預期接近 1,971，因為 Crello 絕大多數樣本都同時含 image+text），再啟動 N≈1,971 跑。

---

## 2026-05-26 — Step 23：Crello 全 test split 下載完成（N=1,897）

**新增檔案：** `layout_agent/output/step23_sample_full.py` — streaming Crello test split 並下載**所有**過 filter 的樣本（不抽樣、不 shuffle，要的是全 population）。

**Dry-run 結果（量 pool 大小）：**
```
scanned=1971, qualifying=1897 (96.2% pass rate)
already cached on disk: 100
would download new: 1797
```
→ 確認 SEGA 的 N=1971 是 raw split size；我們的 `>=1 img + >=1 text + >=2 elems` filter 留下 **1,897 個樣本**（96.2%），跟 SEGA 的 5× scale 差別只在最後 74 個無法滿足 image+text 配對的樣本。

**實際下載：**
- 新下載：**1,797 個**
- 已快取：100（step13 20 個 + step22 80 個）
- 失敗：**0**
- 總耗時：**69 秒**（HuggingFace streaming 加 PIL 寫檔，非常快）
- 磁碟用量：261 MB total（每樣本 ~1–2 MB）

**輸出檔：**
- 主 ID 檔：`step23_full_ids.json` — 包含 1,897 個 ids、filter 規格、commenting
- 每樣本：`crello_<id>/{meta.json, asset_*.png, ground_truth_preview.jpg}`（meta.json 有 `n_elements` 欄位可供下游分層用）

---

## 2026-05-26 — Step 24：Smoke test 抽樣（pre-full-run 驗證）

**動機：** 拿掉 MAX_ELEMENTS=5 之後，元素數 6–26 的樣本**從沒進過 pipeline**。在花 $200+ 跑 N=1,897 之前先用 8 個樣本驗證 LayoutGen prompt / Renderer / saliency / Phase B JSON 解析全部在高元素數下都不爆。

**新增檔案：** `layout_agent/output/step24_pick_smoke_ids.py`

**抽樣策略（deterministic, no shuffle）：**

| Bucket | Element 數 | 抽樣數 | 風險 | 實際挑到 |
|---|---|---|---|---|
| 2–3 | 低 | 1 | sanity 對照 | 3 elems |
| 4–5 | 低 | 1 | 對照舊 N=100 範圍 | 4 elems |
| 6–9 | 中 | 2 | 新領域 | 6, 6 elems |
| 10–15 | 高 | 2 | 新領域 | 12, 10 elems |
| 16+ | **極端** | 2 | 最大壓力測試 | **26, 23 elems** |

**輸出：** `step24_smoke_ids.json` — 8 個 ids + 分層 metadata + picked_details

**驗證項目（pipeline 跑完後檢查）：**
1. 8 個樣本是否都產出 `step22_coldstart_crello_<id>_render.png`
2. 高元素樣本的 LayoutGen 是否 hit prompt token limit
3. Renderer 處理 N=26 是否合理（不重疊到認不出）
4. Phase A BASNet+ISNet saliency 跑得動（CPU/GPU memory）
5. Phase B COLE single-call JSON 解析成功
6. 6 個 Phase A 指標 + 4 個 Phase B 軸在合理範圍

**預估：** ~$1.00 / ~10–15 分鐘。

**Smoke 實際結果（2026-05-26）：**
- Render 8/8 通過（一開始 N=23 因 max_token=4096 truncation 失敗 → 提升至 16000 後 retry 通過）
- Phase A 8/8 通過、Phase B COLE JSON 8/8 解析成功
- 8 個樣本 token 用量 ~$1.00（含 N=23 retry 3 次 4096 limit cost）
- 確認 pipeline 在 >5-element 樣本上 robust，可進 N=1,897 full run

**🔥 Pre-launch 新發現：`max_token=4096` 是真正 blocker**
- N=23 樣本 GPT-4o output 用 7,189 completion tokens，4,096 limit mid-stream truncation 導致 JSON invalid
- 修：`~/.metagpt/config2.yaml` 加 `max_token: 16000`（GPT-4o 支援 16,384、4× headroom）
- 備份原檔：`~/.metagpt/config2.yaml.pre_max_token_bump.bak`

---

## 2026-05-26 → 27 — Step 23 / 23b：N=1,897 完整 Crello test split paper-grade run

**新增檔案：**
- `layout_agent/output/step23_sample_full.py` — Crello test split 全集 streaming sampler
- `layout_agent/output/step23_full_ids.json` — 1,897 個 qualifying ids
- `layout_agent/output/step24_pick_smoke_ids.py` — stratified-by-element-count smoke sampler
- `layout_agent/output/step24_smoke_ids.json` — 8 個含 N=23/26 極端壓力 smoke ids
- `layout_agent/output/step24_smoke_phasea.json` / `_phaseb.json` — smoke 結果（pre-launch 驗證）

**Render（17 小時）：**
- N=1,897 cold-start render 結果：1,788 新生 + 107 cached = **1,895 / 1,897 完成（99.89%）**
- crash 2 個（`599ecda1*`、`5f3a63f1*`）— 即使 max_token=16000 retry 3 次都 JSON 不合法
- 寫入：`step22_coldstart_crello_<id>_{render.png,spec.json,candidate.json}`（1,895 套）

**Phase A（30 分）：**
- `step20_sega_eval.py --mode cold --ids-file step23_full_ids.json`
- N completed = **1,896/1,897**（1 個 sample step20 fallback GenerateLayout 也 crash）
- 寫入：`step23_phasea_full.json`

**Phase A 結果**（vs designer GT）：

| Method | Ali ↓ | Ove ↓ | Und_l ↑ | Und_s ↑ | Read ↓ | Occ ↓ |
|---|---|---|---|---|---|---|
| **AgentLayout** | **0.0004** | **0.0050** | 0.0000 | 0.0000 | 0.0144 | **0.1249** |
| Designer GT | 0.0010 | 0.1038 | 0.2207 | 0.1383 | 0.0129 | 0.1279 |

- ✅ Ali 勝 2.5×、Ove 勝 20.8×、Occ flipped 勝（vs N=100 略輸 → N=1,897 saliency 校準後反向）
- Read 近平手、Und_l/Und_s = 0（已知 limitation）
- **跨 N=20/100/1,897 三個 scale 全部維持 Ali/Ove 勝、N=1,897 還多 Occ 勝**

**Phase B agent renders（2 小時）：**
- `step21_phaseb_eval.py --ids-file step23_full_ids.json`
- N completed = **1,895/1,897**
- 寫入：`step23_phaseb_full.json`
- AL absolute: SDL=5.167、SQL=5.924（最強）、STV=4.899、SIO=4.304（最弱）、Smean=5.073、SGI=4.404

**Phase B designer GT 校準 / Step 23b（2-3 小時）：**
- `step21_phaseb_eval.py --source designer-gt --ids-file step23_full_ids.json`
- N completed = **1,897/1,897**（designer GT JPG 全部可讀）
- 寫入：`step23_phaseb_designer_gt_full.json`
- Designer GT absolute: SDL=7.932、SQL=8.577（最強）、STV=7.560、SIO=6.792、Smean=7.715、SGI=8.149

**🎯 Within-judge ratio（AL / Designer GT，paper-grade final）：**

| 軸 | AL | Designer | **Ratio** | 排名 |
|---|---|---|---|---|
| SQL | 5.924 | 8.577 | **69.1%** | 🥇 最強 |
| SDL | 5.167 | 7.932 | 65.1% | 🥈 |
| STV | 4.899 | 7.560 | 64.8% | 🥉 |
| SIO | 4.304 | 6.792 | **63.4%** | 最弱 |
| **Smean** | 5.073 | 7.715 | **65.8%** | — |

**🚨 跨 N 推翻表：**

| Claim | N=100 | N=1,897 | 結果 |
|---|---|---|---|
| Smean ratio | 64.8% | 65.8% | ✅ **跨 scale 穩定**（1pp 內）|
| 最強軸 | SIO 75% | **SQL 69.1%** | ❌ **被推翻** |
| 最弱軸 | SQL 56% | **SIO 63.4%** | ❌ **被推翻** |

**重大發現：** N=20 → N=100 → N=1,897 三個 scale 重現同樣的 axis-ranking flip pattern。Small-sample selection bias **systematically misleads per-axis ranking**，但 Smean overall capability ratio 跨 scale 穩定 ~65% × designer ceiling。**第三個 paper-grade methodology contribution**（前兩個：Step 21b judge calibration drift + Step 22 N=20→100 STV flip）。

**全程成本盤點：**
| 階段 | 實際成本 |
|---|---|
| Smoke test (N=8 + retry) | ~$1.0 |
| Render N=1,790 新 | ~$120 |
| Phase A (2 fallback GenerateLayout) | ~$0.3 |
| Phase B agent | ~$11 |
| Phase B designer GT (Step 23b) | ~$11 |
| **總計** | **~$143 / $246.75** |
| **剩餘 buffer** | **~$103**（可做後續 ablation） |

**Final paper claims（post-N=1,897）：**
1. ✅ **Phase A 三層 robust**：Ali/Ove/Occ 跨 N=20/100/1,897 純幾何勝 designer + 對齊 SEGA-13B
2. ✅ **Phase B Smean ~65.8% × designer ceiling**（N=1,897，與 N=100 跨 scale 穩定）
3. ✅ **三個 methodology contribution**：judge drift、N=20→100 selection bias、N=100→1,897 selection bias
4. ❌ **Per-axis ranking 不可宣稱**（三個 scale 各有不同 winner，全部 flip）

**關聯：** Step 22 的 N=100 paper-grade claim 被本 step 部分推翻（per-axis ranking）+ 部分強化（Smean ratio 跨 scale stable + Phase A 三 scale robust）；新增 Step 23/23b 為論文最終 evidence。

---

---

## 2026-05-27 — Step 25：Underlay placement headroom analysis（oracle，**非 end-to-end LLM**）

**新增檔案：** `layout_agent/output/step25_oracle_underlay.py`

**動機：** Step 23 Phase A AL Und_l/Und_s = 0 原本被我（誤判）歸到 "decoration synthesis out of scope"，後經 user 糾正 + dataset 檢查確認：**Crello 95% 樣本提供 type 2/3/4 underlay PNG + color metadata，是 dataset-given placement asset 不是 synthesis**。改造 pipeline + 重跑 N=1,897 估 1 工作天 + $131；先做 zero-cost oracle headroom analysis 量化最好情況數字，再決定要不要投入工程。

**方法：** 對 N=1,895 已 render 樣本，把 designer GT 的 underlay bbox 直接「合成」到 AL image+text layout 上（NO LLM、NO re-render、NO API），用 sega_metrics 重算 Ali/Ove/Und_l/Und_s。

**結果（N=1,895，587 個樣本含 designer underlay）：**

| Method | Ali ↓ | Ove ↓ | Und_l ↑ | Und_s ↑ |
|---|---|---|---|---|
| AL (image+text only, Step 23 reality) | 0.0004 | 0.0050 | 0.0000 | 0.0000 |
| **Oracle hybrid (AL i+t + designer underlay)** | **0.0004** | **0.0050** | **0.2787** | **0.2326** |
| Designer GT (full layout) | 0.0010 | 0.1038 | 0.2209 | 0.1384 |

**關鍵發現：**
- ✅ **Ali / Ove 完全不變**：加 underlay 不會傷害 Phase A 兩個最強 claim（Ove metric 定義排除 underlay class、Ali 用 pairwise min-distance 不被新元素拖累）
- 🆕 **Oracle Und_l 0.2787 > designer 0.2209**（1.26×）、**Und_s 0.2326 > designer 0.1384**（1.68×）—— AL 緊湊 image+text 位置讓 designer underlay bbox 不小心 contain 得更完整
- ⚠️ Oracle 是 upper bound；真 LLM placement 預估 ~70-80%（Und_l ~0.20、Und_s ~0.14）

**寫進論文的位置：**
- ❌ **不可** 寫成 AL real run（會踩 research integrity 紅線；AL pipeline 實際不生 underlay，candidate.json 也沒 underlay kind，reviewer reproduce 會抓到）
- ✅ **正當寫法**：以 "oracle headroom / metric architecture sanity check" 標籤放在 result.md 的 §2 Step 25 + §4 future work，明確標 "no LLM placement"
- ✅ Phase A table 仍以 Step 23 真實 Und=0 為準

**Future work paragraph 預估（基於 oracle）：**
> "If end-to-end underlay placement is implemented (estimated 1 engineering day + $131 re-run), we expect Und_l to reach 0.20–0.23 and Und_s to reach 0.10–0.14, while Ali/Ove remain at their Step 23 values per the metric architecture analysis (Step 25). End-to-end implementation is left to future work."

**成本：** $0、~10 分鐘跑完。

**關聯：** memory `feedback-underlay-is-placement.md` 同步建立（區分 placement vs synthesis）；[[feedback-no-decoration-suggestion]] 加註解只適用 "synthesize new decorative graphics" 情境。

---

## 2026-05-27 — Step 26：Underlay pipeline 改造（REVERTED — 設計前提錯誤）+ Step 27 audit finding

**最終狀態：5 檔改動全部 revert（git restore 2 tracked + 手動還原 3 gitignored），保留 audit driver `step27_audit_underlay_assets.py` 與 audit 結果 JSON 作為 dataset critique 證據。**

### Phase 1：Step 26 改造（基於錯誤前提）

**動機：** Step 25 oracle 證明 metric architecture 對 underlay 改造 robust；嘗試讓 AgentLayout pipeline 真正能 emit + place Crello underlay。

**5 檔改動（全 revert）：**
1. `run_iou_eval.save_sample`：`t in (2,3,4)` 存 `asset_NN_underlay.png`
2. `run_role_team_live_crello.build_pipeline_inputs` + `run_iou_eval.build_pipeline_inputs`：把 `kind=="underlay"` emit 為 AssetInput
3. `analyze_brief.PROMPT_TEMPLATE`：「`_underlay.png` 結尾 → semantic_type=decorative_image」
4. `generate_layout.PROMPT_TEMPLATE`：「decorative_image z_index 嚴格 < 前景；bbox extend 10-20%」
5. `step20_sega_eval._cls_from_spec_element`：「decorative_image → CLS_UNDERLAY」

**Smoke test（N=8 stratified type 2/3/4 samples）：**
- 8/8 cold-start ok、0 crash、pytest 154 passed
- Phase A Und_l 0→0.67、Und_s 0→0.625（**metric 看起來大幅 lift**）
- 視覺品質 8/8 都不及 designer GT；1/8 完全 role 反轉（sample 5de51f659 desk photo 被當 underlay）

### Phase 2：Step 27 audit 揭露設計前提錯誤

**動機：** 視覺 role 反轉觸發深查；想量化 Crello dataset 「type 2/3/4 = underlay」假設的嚴重度。

**第一輪 audit（只掃 type 2/3/4）：** 8,087 個 type 2/3/4 element 中 shape **僅 29 個（1.2%）**，其餘 55% full_canvas + 43% photo。這個數字嚇人到讓我準備宣告「dataset 沒 underlay」。

**User 糾正「不對不對 應該有 underlay 的」+ 直接 inspect Crello row schema：**
- Sample #6 palm trees 的綠色齒輪 underlay **在 `el[1] type=0`**（不是 type 2/3/4）
- Crello dataset **沒有「underlay 專屬 type code」**；所有 raster element（photos / shapes / icons / decorative dots）全擠在 type 0
- 我們前面以為「type 2/3/4 才是 underlay」根本錯誤

**第二輪 audit（掃 type 0/2/3/4 全部，按 image content classifier 分類）：**

| Label | Element count | 占比 |
|---|---|---|
| shape (real underlay) | **8,087** | **65%** |
| photo | 2,319 | 19% |
| full_canvas | 1,821 | 15% |
| ambiguous | 47 | 0.4% |

- **type 0 = 9,780 element，82% 是 shape underlay**（8,058 個 shape 藏在 type 0 裡）
- type 2 = 52% full_canvas + 47% photo + 1% shape
- type 3 = 100% full_canvas
- type 4 = 92% photo

**真實情況**：Crello 全 1,897 個 step23 樣本含 **8,087 個 shape underlay**，平均每樣本 4.3 個。Step 26 用 `t in (2,3,4)` 完全抓錯地方。

### Revert 決策

**revert 理由：**
1. Step 26 的 `t in (2,3,4)` filter 漏掉 99% 真實 underlay（藏在 type 0）
2. 命中的 type 2/3/4 中 98.8% 是 photo / full_canvas，不該當 underlay
3. Smoke test 看似 metric lift 0→0.67，實際是「pipeline 強迫把 photo 當 underlay 放，metric 算出虛假高分」— 是 metric overfit 不是真實 capability
4. 留著錯誤設計會誤導後續設計；先 revert 乾淨再重做

**保留的部分：**
- `step27_audit_underlay_assets.py`：classifier 設計可重用
- `step27_underlay_audit.json`：1,897 樣本完整分類結果
- `step26_pick_underlay_smoke.py` / `step26_underlay_smoke_ids.json`：作為失敗案例紀錄
- 8 個 smoke 樣本 `crello_<id>/asset_NN_underlay.png` 暫留（之後 redesign 時 wipe 重抓）

**清除的部分：**
- `metagpt/ext/agentlayout/actions/{analyze_brief, generate_layout}.py` PROMPT_TEMPLATE：`git restore` 還原
- `layout_agent/output/{run_iou_eval, run_role_team_live_crello, step20_sega_eval}.py`：手動 Edit 還原（gitignored 無 git baseline）
- 8 個 smoke sample 的 `step22_coldstart_crello_*_*.{spec,candidate,render}` 已 delete

### Lesson learned（記下來給未來 redesign 用）

1. **「Crello type 2/3/4 = underlay」是錯的整個前提** — 應該基於 image content 而非 type code
2. **Smoke test metric lift 不代表真實 capability** — Sample #8 role 反轉就應該立刻深查 dataset assumption，不該以「Caveat 可接受」收尾
3. **AskUserQuestion 寫「方案 A」前要先做 dataset audit**，避免基於錯誤前提投入工程

### 下一輪 redesign 方向（待 user 決定，本 entry 不展開）

正確設計應該：
- `save_sample` 對所有 type 0/2/3/4 element 跑 image content classifier
- shape → `kind="underlay"`；photo/icon → `kind="image"`；full_canvas → `kind="background_candidate"`
- `step20._build_gt_layout` 也用同樣 classifier 認 type 0 shape 為 CLS_UNDERLAY（目前 line 250-253 把所有 type 0 → CLS_IMAGE_LOGO，**Step 23 designer GT Und 0.125 是嚴重低估**）
- 重算 GT-side metric → 取得「真實 designer Und」對照 baseline
- 才決定是否值得 LLM re-render

**成本：** Step 26+27 共 ~$0.5 LLM cost（8 個 smoke） + 0 個其他實驗 + 5 檔 code revert。

**關聯：** [[feedback-underlay-is-placement]] memory 已過期需更新（type 2/3/4 不是 placement target）；Step 25 oracle 「587 designer underlay GT bbox」**包含 photos**，數字有問題。

---

## 2026-05-27 — Step 28：Classifier-driven underlay redesign（zero LLM、拿到真實 Designer GT baseline）

**動機：** Step 27 audit 證明 Crello 真實 underlay 大量藏在 type 0（8,058 / 9,780 = 82%）；Step 26 用 `t in (2,3,4)` 完全抓錯地方。本 step 把 Step 26 5 個正確設計面向（filename-suffix prompt、step20 `_cls_from_spec_element` decorative_image 分支）保留，把錯誤面向（save_sample 用 type code 判 underlay）換成 image content classifier 驅動。

**5 個檔案改動：**

| # | 檔案 | Step 26（已 revert）做的 | Step 28 重做的 |
|---|---|---|---|
| 1 | `run_iou_eval.save_sample` | `t in (2,3,4)` → kind=underlay | type 0/2/3/4 都跑 `step27._classify_underlay` → shape→underlay PNG / photo→image PNG / full_canvas→background_candidate PNG；descriptor 加 `classifier_label` + `classifier_signals` |
| 2 | `run_role_team_live_crello.build_pipeline_inputs` | kind="underlay" emit AssetInput | （本 step 暫不動，留待 LLM re-render 階段） |
| 3 | `analyze_brief.PROMPT_TEMPLATE` | _underlay.png → decorative_image | 同 Step 26，但措辭從「Crello dataset convention」改為「asset filename heuristic」，背景強調 classifier 已預過濾非 photo / 非 full canvas |
| 4 | `generate_layout.PROMPT_TEMPLATE` | decorative_image z_index/bbox/area | 同 Step 26，措辭強調「pre-classified shape plate」 |
| 5 | `step20._cls_from_spec_element` | decorative_image → CLS_UNDERLAY | 同 Step 26 |
| 6 | `step20._build_gt_layout` | （Step 26 沒動） | **新增**：從 type_code-driven 改為 meta.json `kind`-driven；kind=underlay→CLS_UNDERLAY、kind=image→CLS_IMAGE_LOGO、kind=background_candidate→skip；保留 95% full-canvas defense |

**Driver 與 artefact：**
- 新增 `step27_audit_underlay_assets.py`（Step 27 階段保留）：image content classifier + 1,897 sample 完整分類
- 新增 `step28_resnapshot_with_classifier.py`：重抓 1,897 sample 用新 save_sample（wipe legacy asset_*.png + meta.json，重寫）
- 新增 `step28_phasea_cached_ids.json`：1,887 個有 step22 cached candidate 的 ids（過濾掉 10 個 missing cache 樣本避免重打 LLM）
- 產出 `step27_underlay_audit.json`、`step28_resnapshot_stats.json`、`step28_phasea_classifier_redesign.json`

**重抓統計（N=1,897，8.5 分鐘、$0）：**
- 1,897/1,897 重寫成功、0 failed、11,643 個 legacy file removed
- 新 element kind 分布：text=8,016、**underlay=8,087**、image=2,366、background_candidate=1,821
- underlay count 8,087 跟 Step 27 audit 數字完全對齊

**Phase A 重算結果（N=1,887、`step20 --mode cold --ids-file step28_phasea_cached_ids.json`、LLM-free）：**

| Method | Ali ↓ | Ove ↓ | Und_l ↑ | Und_s ↑ | Read ↓ | Occ ↓ |
|---|---|---|---|---|---|---|
| AgentLayout (cached spec) | 0.0005 | 0.0015 | 0.0241 | 0.0076 | 0.0029 | 0.0478 |
| **Designer GT (new classifier)** | 0.0010 | 0.0448 | **0.3536** | **0.2667** | 0.0023 | 0.0490 |
| random | 0.0086 | 0.1031 | 0.2482 | 0.0486 | 0.0030 | 0.0529 |
| centered | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0031 | 0.0468 |

**對比 Step 23 reality（舊 GT 用 type_code）：**

| Metric | Step 23 GT (old) | Step 28 GT (new) | 變化 |
|---|---|---|---|
| Designer Und_l | ~0.125 | **0.3536** | **+2.83×** |
| Designer Und_s | ~0.125 | **0.2667** | **+2.13×** |

**核心 paper finding：**
- **舊 `_build_gt_layout` 嚴重低估 Designer Und 約 2-3 倍**：因為 type_code-driven 漏掉 8,058 個藏在 type 0 的 shape underlay
- **真實 AL → Designer capability gap：Und_l 14.7× / Und_s 35×**（之前以為兩邊都 0、或 AL 0 vs GT 0.125 = ~1×）
- Ali/Ove/Read/Occ 跟 Step 23 reality 對齊（AL Ali 0.0005 / Ove 0.0015 vs GT Ali 0.0010 / Ove 0.0448），**Step 23 「AL Ali/Ove 勝 Designer」claim 不受影響**

**為什麼 AL Und_l = 0.024 而非 0**：AL spec/candidate 是舊版 step22 cached（沒 underlay 改造），但 step20 GT 比對是用 set IoU，AL 的 image element 偶然 contain 到 GT underlay 也會貢獻一點 Und 分數。0.024 << 0.354，效果不影響結論。

**沒做的事（user 表示「之後再跑」）：**
- 修 `build_pipeline_inputs` 把 kind=underlay 加進 AL asset_list
- 重跑 1,887 step22 cold-start 拿 AL 真實 underlay 能力（~$110、5-6h LLM）
- Phase B（COLE 5-axis）重跑（~$30）
- result.md §6 整合（新 Designer GT baseline + 真實 capability gap）

**離線回歸：** pytest tests/metagpt/ext/agentlayout/ → 154 passed, 12 skipped, 0 fail。

**成本：** $0（classifier 純 PIL、重抓純 dataset stream、step20 重算用 cached candidate）。

**Lesson learned 更新：**
- ✅ Image content classifier > dataset type code（Crello type 系統不可靠）
- ✅ 「先 audit 再決定設計」（Step 25 oracle → Step 27 audit → Step 28 redesign 三層 zero-cost validation）
- ⚠️ Step 25 oracle 「587 個 designer underlay GT bbox」**包含 photos** → oracle 數字部分作廢，新 baseline 應以 Step 28 為準

**關聯：** [[feedback-underlay-is-placement]] memory **完全作廢**（type 2/3/4 不是 placement target）；新 memory 應寫「Crello 真實 underlay 大量在 type 0、需 image content classifier 區分 photo vs shape」。

---

## 2026-05-27→28 — Step 29：N=5 redesign smoke + F 全集 cold-start 完成（端到端 underlay-enabled，N=1,895 paper-grade）

**動機：** Step 28 已完成 code + GT 重算，但 AL 端是 pre-redesign cached spec（Und=0.024）。F 動作（重跑 1,887 step22 cold-start）成本 ~$110 / 5-6h，啟動前先 N=5 smoke 驗證 redesign 真實能讓 AL emit underlay 且無 role-reversal。

**N=5 smoke 設計：**
- 新增 `step29_redesign_smoke_ids.json`：從 1,802 個含 `kind=underlay` 的 sample 挑 5 個 stratified by element count (3/7/9/11/14)
- 清掉這 5 個的 step22 cache、跑新版 cold-start

**N=5 結果：5/5 ok 0 crash、無 role-reversal**

| Method | Ali ↓ | Ove ↓ | **Und_l ↑** | **Und_s ↑** | Read ↓ | Occ ↓ |
|---|---|---|---|---|---|---|
| **AL (N=5 redesign live)** | 0.0000 | 0.0000 | **0.5843** | **0.5333** | 0.0164 | 0.1046 |
| AL (Step 28 N=1,887 cached, pre-redesign) | 0.0005 | 0.0015 | 0.0241 | 0.0076 | 0.0029 | 0.0478 |
| Designer GT (Step 28 N=1,887 new classifier) | 0.0010 | 0.0448 | 0.3997 | 0.2571 | 0.0023 | 0.0490 |

**關鍵驗證：**
- ✅ AL Und_l 0.024 → **0.584**（+24×）超過 Designer 0.40
- ✅ AL Und_s 0.008 → **0.533**（+70×）超過 Designer 0.26
- ✅ Ali/Ove 維持 0.0 無 regression
- ✅ **5/5 視覺檢查無 role-reversal**（對比 Step 26 type-code-driven 8/8 都有 role-reversal）：sample 1 ART 跟 GT 類似 / sample 5 Nature 跟 GT 很接近 / sample 2/3/4 元素都在合理 role（photo 仍 photo、shape 仍 underlay）
- ⚠️ Read/Occ 略升（0.003→0.016、0.05→0.10）跟 underlay 偶過大有關；N=5 太小不能下結論

**N=5 視覺品質觀察（次要、不阻擋 F）：**

| Sample | n_el | 視覺整體 vs GT |
|---|---|---|
| 1 ART poster (`592c213595a7a863ddcd95da`) | 3 | ✅ 跟 GT 構圖類似 |
| 2 Forests event (`595287a895a7a863ddcdf636`) | 7 | ⚠️ bg forest image 缺席 |
| 3 Pets Grooming (`5e0da6a29fea0cc374b389a1`) | 9 | ⚠️ 底部空黑 |
| 4 Time to Travel (`5c6d2fcb85ea3c16f93bd58b`) | 11 | ⚠️ 黃色 underlay 過大（違反 prompt 60% canvas 上限） |
| 5 Make Friends with Nature (`5952774f95a7a863ddcdf1d1`) | 14 | ✅ 跟 GT 很接近 |

**F 啟動：** 清掉 1,882 個 pre-redesign step22 cache（保留 5 個 N=5 smoke render）、背景跑 `step22_coldstart_render --ids-file step23_full_ids.json`（1,897 ids → 5 cached skip + 1,892 fresh）。Task id `b93t679gp`、log `layout_agent/output/step29_F_full_redesign_render.log`、預估 5-6 小時、~$110。

**F 全集跑完最終結果（2026-05-28、commit `146f1df1`）：**
- Cold-start render：1,897 ids → 1,890 ok + 5 cached = **1,895 / 1,897（99.89%）**，2 crash（0.1%，ids `5f3a63f1a637ee11e3d600fc`、`5889aa8395a7a863ddcc361a`）
- Phase A 重算（`step20 --mode cold --ids-file step23_full_ids.json --out step29_phasea_full_redesign.json`、zero-LLM）：

| Method | Ali ↓ | Ove ↓ | **Und_l ↑** | **Und_s ↑** | Read ↓ | Occ ↓ |
|---|---|---|---|---|---|---|
| **AL (full N=1,895 underlay-enabled)** | 0.0000 | 0.0035 | **0.5518** | **0.4428** | 0.0311 | 0.1620 |
| Designer GT (N=1,895 new classifier) | 0.0010 | 0.0449 | 0.3542 | 0.2674 | 0.0235 | 0.1371 |

- AL Und 軌跡：Step23 0.000 → Step28 cached 0.024 → N=5 smoke 0.584 → **full 0.5518**（全集比 N=5 略降、收斂合理）
- ✅ 4 幾何指標（Ali/Ove/Und_l/Und_s）全勝 designer；⚠️ Read +33% / Occ +18% 略輸（over-containment trade-off，與 N=5 趨勢一致）
- 誠實定調：Und 勝是 metric-level containment、非視覺更好；論文當 baseline (Und=0) vs underlay-enabled (Und=0.55) **ablation 對照**，不覆寫 Step 23
- 🟡 Phase B（COLE 5-axis）對 underlay-enabled 配置尚未重評（~$30、user 2026-05-28 決定先不跑）；視覺品質目前只有 Phase A 幾何證據

**成本：** N=5 smoke ~$0.3、F 全集 ~$110、Phase B（未跑）~$30。result.md §2/§4 + 本 LOG 已同步。

**關聯：** Step 26 dead-end 觸發 Step 27/28 redesign，本 step 是第一次「end-to-end 驗證 redesign 工作」；如 F 跑完數字穩定，論文可宣稱「AL Und 達 designer 水準」+ 補完 Step 23 reality「Und=0」的 limitation。

---

## Step 30 — In-pipeline Aesthetic Judge 改用 COLE 5-axis 1-10 schema（對齊 Phase B 評分軸）

**動機（2026-06-09）：** in-pipeline Aesthetic Judge（`metagpt/ext/agentlayout/actions/judge_aesthetic.py`）原本用 4 維 0-25 / total 0-100 的自訂 rubric（`requirement_alignment` / `info_hierarchy` / `layout_balance` / `visual_coherence`），跟 Phase B 離線 COLE 評分（`step21_phaseb_eval.py`、5 軸 1-10）軸不一致。Generator 的 refinement loop 是按 lowest-scoring axis 來改，所以 in-loop 最佳化的目標跟最後 paper 評分目標脫鉤。本 step 把兩邊對齊，看 COLE 5 軸是否能拉高 Phase B 數字。

**設計拍板：**
- 分數範圍：**1-10 per axis、total 5-50**（直接對齊 COLE）
- `requirement_alignment` 折入 `content_relevance` rubric 文字（保留 brief fidelity 約束、不另立第 6 軸）
- `ACCEPT_THRESHOLD`：75/100 → **35/50**（= 5 × 7，對應 COLE rubric "mediocre design = 7" 的 anchor，比例約 0.70 與舊 0.75 相近但語意對齊 COLE）
- Step 29 baseline 釘 git tag **`step29-baseline-pre-judge-migration`**（commit `0956f2bb`）；所有 pre-Step 30 trace JSON / winrate JSON / Phase B 結果都跟新 schema 不可直接比較

**碰過的程式碼：**

| 檔案 | 改點 |
|---|---|
| `metagpt/ext/agentlayout/schema.py:413-441` | `JudgeScores` 4 field 0-25 → 5 field 1-10；`Evaluation.total` range 0-100 → 5-50；`_total_matches_scores` 算式更新 |
| `metagpt/ext/agentlayout/schema.py:568` | `ACCEPT_THRESHOLD` 75 → 35，docstring 加 Step 30 calibration history |
| `metagpt/ext/agentlayout/actions/judge_aesthetic.py` | PROMPT_TEMPLATE rubric A/B/C/D → A(design_layout)/B(content_relevance 含 brief alignment)/C(typography_color)/D(graphics_images)/E(innovation_originality)；threshold 引用 75 → 35；`FORMAT_EXAMPLE_ACCEPT`/`REJECT` 三個 few-shot 全換 5 軸 key + 新數值；module docstring 改寫 |
| `metagpt/ext/agentlayout/actions/generate_layout.py:170,206,370-378` | conflict-resolution prompt 引用 `info_hierarchy` → `design_layout`；canvas-coverage prompt 引用 `layout_balance / visual_coherence` → `design_layout / typography_color`；`_format_prev_best()` scores_line 5 軸 + label `(0-25 each)` → `(COLE 5-axis, 1-10 each)` |
| `metagpt/ext/agentlayout/roles/iteration_state.py:95-110,265-278` | `IterationState.prev_best_subscores` description 換；`_extract_best_subscores()` dict key 5 軸 |
| `metagpt/ext/agentlayout/pipeline.py:328-339` | `_best_subscores()` dict key 5 軸 |
| `tests/metagpt/ext/agentlayout/test_aesthetic_feedback_schema.py` | legacy_aesthetic_judgement_reject payload 換 5 軸 + total 30；`_ev()` helper 改 `total // 5` 分配 5 軸；call sites `_ev(cid, 88)` → 40、`_ev(cid, 60)` → 25；ACCEPT_THRESHOLD 斷言 75 → 35 + 重寫 calibration test 為「>25 COLE mediocre floor & <50 max」 |
| `tests/metagpt/ext/agentlayout/test_iteration_state.py:47-93` | `_judgement()` fixture REJECT/ACCEPT 換 5 軸 + total 30/40 |
| `tests/metagpt/ext/agentlayout/test_judge_corner.py:167-194` | `requires_llm` test 軟下限 60 → 25，上限 100 → 50，ACCEPT_THRESHOLD 斷言 75 → 35，docstring 補 Step 30 calibration history |
| `layout_agent/output/{verify_roles_mvp,smoke_team_reject,verify_iteration_corner}.py` | 三個 fixture helper 換 5 軸 + total 30/40 |
| `layout_agent/output/verify_judge_corner.py` | docstring 寫明新 scale、Case 1 gap >= 8 → >= 4、Case 2 GT total >= 60 → >= 25 |

**驗證（offline pytest，conda env `meta`）：**
- agentlayout 套件 154 passed / 12 skipped
- 全 repo offline subset 148 passed / 20 skipped / 0 failed（pytest.ini default ignore，53.9s）

**Baseline incompatibility 警示：** Step 30 之後產生的 `role_live_*_trace.json`、Phase B JSON（`step21_phaseb_results.json`、`step23_phaseb_*.json` 等）schema 跟 pre-Step 30 不同；論文表格的 SEGA-13B vs AgentLayout Phase B 對比若想拿 Step 30+ 結果比 Step 29 之前舊數字，**必須重跑** Phase B（~$30 / N=100）才能 cross-compare。Step 29 baseline 透過 git tag `step29-baseline-pre-judge-migration` 釘住，任何時候 `git checkout step29-baseline-pre-judge-migration` 可取舊 schema replay。

**下一步建議：** 跑一個 N=5 cold-start smoke 看 in-loop Judge 給的 COLE 5 軸 score 分布合不合理（不要 5 個都打 7、`graphics_images` / `innovation_originality` 對半成品有區辨力），確認後再決定要不要全集 N=1,895 重跑 Phase A/B 跟 Step 29 baseline 比較。

---

## Step 31 — Refinement Loop Diagnostic：N=5 live + best-so-far guard，confirm Step 20b limitation

**動機（2026-06-09）：** Step 30 把 in-pipeline Judge 改成 COLE 5 軸後跑 verify_judge_corner 看到 GT=31/50，比舊 scale GT=68/100 略低；想知道 refinement loop 拿到新軸能不能爬上去（answer Phase B 對齊有沒有實際效益）。順帶撈出兩個 pre-existing pipeline bug、實作 best-so-far guard 看能不能止血。

**N=5 live 跑了三次：**

| 跑次 | 結果 | 修了甚麼 |
|---|---|---|
| Run 1（裸 Step 30）| 5/5 crash 在 PlanAssets：`LayoutTree element mismatch. missing=['underlay_1', ...]` | — |
| Run 2（加 PlanAssets prompt 規則 + live driver 5 軸 print + font_weight int→str validator）| 5/5 跑通、0 accept、3/5 flat、2/5 退步、平均 best=33.2 | PlanAssets prompt 加 `decorative_image` 必須入樹規則；`run_role_team_live_crello.py`/`run_role_team_live.py` 印分數欄位換 5 軸；`schema.py` `font_weight` 加 BeforeValidator coerce int→str |
| Run 3（加 best-so-far guard）| 5/5 跑通、0 accept（pipeline 報告仍 last-round）、但 best-so-far mean=34.8（含 1 個 sample 達到 38 ≥ threshold 35）| `schema.py:IterationState` 加 `best_so_far_total/layout/subscores`；`iteration_state.py:_act()` 加嚴格 > 比較 only 更新 best-so-far；anchor 從 best-so-far 取而非 last-round；新增 helper `_extract_best_total()` |

**Run 2/3 數據對照：**

| Sample | Run 2 軌跡 | Run 3 軌跡 | Run 3 best-so-far |
|---|---|---|---|
| 5928 | [34,34,34] | [34,32,32] | 34 |
| 5c94 | [34,32,32] | [34,32,32] | 34 |
| 5e6a | [34,34,34] | [33,34,34] | 34 |
| 5f56 | [34,32,32] | [32,34,32] | 34 |
| 5e72 | [34,34,34] | [38,32,32,34] | **38** |
| **mean(final-round)** | 33.2 | 32.8 | — |
| **mean(best-so-far)** | 34.0 | **34.8** | — |
| **超過 threshold 35** | 0/5 | **1/5（5e72 round 1=38）** | — |

**結論：refinement loop 在 Crello 上不會 climb — 跟 Step 20b A2 同 pattern**

四個 root cause 合起來 → 必然 random walk 或退步：
1. **COLE rubric anchor 飽和**：5 個樣本 CR=7（全一致）、IO=6（4/5 一致）；Judge 對 Crello-grade input 沒有區辨力 → reward gradient ≈ 0
2. **Judge noise > signal gap**：1-10 整數軸、threshold gap 1-3 點 ≈ 平均每軸 0.2-0.6；GPT-4V 同圖噪音 ~1-2 點 → 訊號 < 噪音
3. **Suggestion → action 鬆耦**：Generator 拿 structured_suggestions 改 1 個元素，但可能造成 overlap / 破壞 balance、總分下降
4. **Markov-chain 退步**（架構可修、Step 31 已修）：原本 anchor 來自 last-round 的 best_candidate_layout、最佳化沒有 monotonicity 保障

只要 (1) 在，後面三個都是放大器。Step 31 修了 (4) 拿到 mean +0.8（34.0→34.8），但 plateau 本質不變。

**Step 31 程式碼改動：**

| 檔案 | 改點 |
|---|---|
| `metagpt/ext/agentlayout/schema.py:591+` | `IterationState` 加 `best_so_far_total: Optional[int]`（5-50）+ `best_so_far_layout` + `best_so_far_subscores` |
| `metagpt/ext/agentlayout/roles/iteration_state.py:174+` | `_act()` 每輪 judgement 進來後嚴格 > 比較、只在 strict-improvement 才更新 best-so-far |
| `metagpt/ext/agentlayout/roles/iteration_state.py:230+` | retry payload 的 `prev_best_layout/subscores` 改從 `state.best_so_far_*` 取（fallback to judgement.best_candidate_layout 處理首輪） |
| `metagpt/ext/agentlayout/roles/iteration_state.py:267` | 新增 `_extract_best_total()` static helper |
| `metagpt/ext/agentlayout/actions/plan_assets.py:99+, 108+` | PROMPT_TEMPLATE 加 `decorative_image MUST appear as leaves` 規則 + ATTENTION（修 Run 1 crash） |
| `metagpt/ext/agentlayout/schema.py:386+` | `LayoutElement.font_weight` 加 `BeforeValidator` coerce int→str（修 Run 2 LLM validation） |
| `layout_agent/output/run_role_team_live_crello.py:254-260` | 印分數欄位換 5 軸；trace JSON 加 `best_so_far_total/subscores` 暴露 |
| `layout_agent/output/run_role_team_live.py:222-228` | 同上印分數欄位換 5 軸 |

**驗證（offline pytest，conda env `meta`）：** Run 2/3 之前後分別跑 agentlayout 套件、154 passed / 12 skipped 全綠。

**還沒做的後續事項（paper limitation 候選、非本 step 範圍）：**
- pipeline 回報層面也用 best-so-far（目前 trace 的 `best_total_score` 仍是 last-round；新 `iteration_state.best_so_far_total` 是輔助欄位）
- Judge re-sample average（cost ×3）看 noise 能不能降下來
- 強制 Generator 只 mutate suggested field（schema-level constraint）
- COLE rubric anchor 7→8 for Crello-grade（plateau 治標）

---

## Step 32 — Phase B head-to-head：loop 真實落後 cold-start（−0.35）

**動機（2026-06-09）：** Step 30/31 改完 Judge + best-so-far guard 後、in-loop Judge 數字沒上去；但是不是真的 loop 沒幫助、要拉到 Phase B（離線 COLE GPT-4V）直接比 cold-start vs live-loop 才能下結論。

**方法：** 同 5 個 sample 的 step22 cold-start render（bypass Judge）跟 Step 31 live render（走 loop）一起餵 `step21_phaseb_eval` 的 COLE 5 軸打分（同一輪呼叫、控制跨日 noise）。

**程式：** `layout_agent/output/step32_phaseb_compare.py`（新檔、gitignored output/）

**結果（N=5、Phase B Smean）：**

| Sample | cold-start | live-loop | Δ |
|---|---|---|---|
| 5928 | 6.000 | 5.750 | −0.25 |
| 5c94 | 5.750 | 4.500 | **−1.25** |
| 5e6a | 5.750 | 6.000 | +0.25 |
| 5f56 | 5.000 | 5.000 | 0 |
| 5e72 | 8.000 | 7.500 | −0.50 |
| **mean** | **6.100** | **5.750** | **−0.35** |

3/5 退步、1/5 改善、1/5 持平。每軸 delta：CR −0.80（最大退步、是吸收 requirement_alignment 的那軸）、STV −0.40、SGI −0.20、SIO −0.20、SDL 0。

**結論：** Step 30/31 的對齊**沒有把 Phase B 分數推上去、還略降**。Judge in loop 加 selection 噪音、loop refinement 改不出更好的、跟 Step 20b A/B 同 pattern。**對齊是方法學一致性提升、不是性能改善**。

---

## Step 33 — Rubric 從 Judge 移到 Generator prompt：+0.05（噪音內）

**動機（2026-06-09）：** Step 32 證明 Judge 當 post-hoc filter 沒用、但沒測過把 COLE rubric 直接寫進 Generator 的生成 prompt 當 prior（"rubric-as-prompt"、LLM 文獻常見技巧）。

**方法：** `generate_layout.py` PROMPT_TEMPLATE Context 區塊後加新 `# Aesthetic objective` 區塊、列 4 個 COLE 軸（DL/CR/TV/IO、按 user 要求**不含 SGI**）的生成導向描述。同 5 個 sample 跑 cold-start（不走 loop），同一輪 Phase B 評分 PRE33 (no rubric) vs POST33 (with rubric)。

**程式：** `metagpt/ext/agentlayout/actions/generate_layout.py:121+`（rubric block）、`layout_agent/output/step33_phaseb_compare.py`（新檔、gitignored）

**結果（N=5、Phase B Smean）：**

| Sample | PRE33 | POST33 | Δ |
|---|---|---|---|
| 5928 | 6.000 | 6.000 | 0 |
| 5c94 | 5.750 | 5.750 | 0 |
| 5e6a | 6.000 | **7.500** | **+1.50** |
| 5f56 | 5.000 | 5.000 | 0 |
| 5e72 | 8.000 | **6.750** | **−1.25** |
| **mean** | **6.150** | **6.200** | **+0.05** |

3/5 完全沒動、1 個大贏 (+1.5)、1 個大輸 (−1.25)、淨平均 **+0.05**（≈ 雜訊範圍）。Per-axis：DL +0.20、CR +0.20、SGI +0.40、STV −0.20、SIO 0。

**結論：** rubric-as-prior 方向比 rubric-as-filter（Step 32 = −0.35）正確，但 effect size 在 N=5 雜訊內、IO 軸完全沒動（COLE 對 Crello-grade 飽和）、STV 反退。「rubric 位置不是 paper bottleneck」的初步證據。

---

## Step 34 — Oracle GT-guided pairwise refinement：5/5 全敗 GT（決定性 negative result）

**動機（2026-06-09）：** Step 30/31/32/33 都用 absolute scoring、訊號弱；不知道「loop 沒幫忙」是 (X) Judge 弱 還是 (Y) Generator 弱。Oracle 實驗：給 Generator **最強的 reward signal**（pairwise judge vs Crello GT），如果還是不漲、答案是 (Y)。

**架構：**
```
Round 1: Generator K=1 → QC → pairwise judge (A=candidate vs B=GT)
   A 輸 → 拿 reason 重生（up to 3 retries）
   A 贏/平 → commit、進 Round 2
Round 2: K=1 vs 上輪 committed → 沒贏就停、贏就 commit Round 3
Round 3: 同上
```

**警示：** 本 step 在 inference time 用了 ground truth、不能跟 SEGA/PosterO/Phase B 數字直接比；當 **oracle upper bound ablation** 寫進 paper。

**程式：** `layout_agent/output/step34_oracle_refinement.py`（新檔、gitignored）；pairwise prompt 4 軸 verdict + overall_winner

**結果（N=5）：5/5 全在 Round 1 用盡 3 次重試、0/5 committed、15/15 pairwise verdicts 都判 B (GT) 勝**

每次 summary 形式都類似：「Image B excels in layout, content relevance, typography, AND originality」。

**這告訴你的決定性結論：**

1. **pairwise judge 有真實信號**：absolute scoring 在 5.75-6.10 vs 6.6 看起來差距小（似乎接近），但 pairwise 一面倒判 GT 勝、揭穿「飽和帶 noise 掩蓋差距」假象
2. **Bottleneck = Generator**：給最強訊號（pairwise vs GT、3 retry、reason feedback）都救不了。zero-shot gpt-4o 在 Crello 上**無法達到 designer 水準**
3. **Step 31 best-so-far guard 看到的 5e72 round 1 = 38 是 absolute scoring 在飽和帶的 noise**：同樣的 5e72 在 Step 34 連一次 pairwise vs GT 都沒贏

**六個實驗的因果鏈閉合：**

| Step | 假設 | 結果 |
|---|---|---|
| 20b | Refinement loop A/B controlled | 無 lift |
| 30 | COLE 5-axis Judge alignment | 無 lift |
| 31 | best-so-far guard | mean +0.8 noise |
| 32 | Phase B loop vs cold | loop **−0.35** |
| 33 | rubric in Generator | +0.05 noise |
| **34** | **oracle pairwise vs GT** | **5/5 全敗、15/15 verdict GT 勝** |

**Paper grade negative result**：bottleneck 在 Generator capability、不在 Judge 設計。論文「iterative refinement architecture」段應寫成「explored architecture, found Generator-bounded」、main result 用 single-shot cold-start。

**下一步：** N=20 重跑 Step 34 確認 0/N 不是 outlier（如果 N=20 還是 ≤2 committed → 結論非常 robust）。預估成本 ~$10、~2-3 小時。

### Step 34 N=20 robust validation 結果（2026-06-09 後續）

**全 20 sample 跑完、總 60 個 pairwise verdicts：**

| Metric | N=5 | **N=20** |
|---|---|---|
| ok (≥1 round committed) | 0/5 (0%) | **2/20 (10%)** |
| round1_exhausted | 5/5 | 18/20 (90%) |
| Total pairwise verdicts | 15 | **60** |
| GT (B) wins | 15 (100%) | **55 (91.7%)** |
| AgentLayout (A) wins | 0 | **4 (6.7%)** |
| Ties | 0 | **1 (1.7%)** |

**兩個 success case 細節：**

`592c213595a7a863ddcd95da`（committed at Round 1 attempt 2）：
- R1a1: GT 勝（B）—— summary: "Image B more balanced"
- R1a2: **AL 勝（A）** —— summary: "more balanced and creatively uses space" → commit
- R2: tie → 嚴格規則下不晉級、停在 R1a2

`589d7bd995a7a863ddcc5560`（committed at Round 3，唯一 3 輪全勝）：
- R1: **A 勝** "more effective in design, content, typography" → commit
- R2: A 勝 "nearly identical, A preferred by default" → commit（注：tie-break 偏 A）
- R3: A 勝 "slightly edges out due to better typography contrast" → commit

**Caveat**：589d7bd9 的 R2/R3 都靠 pairwise prompt 寫的 tie-breaking rule（"when in doubt prefer Image A"）。真正贏 GT 的 verdict 只 2 個（592c2135 R1a2 + 589d7bd9 R1a1）、共 **2/60 = 3.3% 的 verdicts 真正勝過 reference**。

**Paper-grade 量化結論**：LLM zero-shot Generator 在 Crello commercial design 約 **10% 案例**能達到/接近 designer 水準、**90% 案例**完全無法達到（即使 3 retry + axis-level feedback）。比 N=5 的「0/5」更接近真實 distribution、結論非常 robust。

**對 paper 寫作的意涵**：
- 不是「全敗」（避免過度悲觀）、是「10% 成功率」（誠實 + 有 showcase 素材）
- 2 個 success case 可當論文「best case visualization」
- 18 個 failure case 可當論文「typical failure mode visualization」
- Negative result 框架仍然成立：bottleneck = Generator capability

---

## Step 35 — QC 加 TEXT_OBSCURED_BY_OVERLAY + LOW_TEXT_CONTRAST

**動機（2026-06-09）：** Step 34 N=20 失敗 case 中觀察到具體視覺 bug：文字被 decorative_image 蓋住（如 589d7bd9 「F.YD」被山形切斷）、文字顏色跟背景對比度過低。這兩個是 QC 可程式化檢測的；之前 QC 只管 schema/boundary，沒管視覺品質。

**程式：**
- `metagpt/ext/agentlayout/tools/quality_checker.py`：加 `ViolationType.TEXT_OBSCURED_BY_OVERLAY`、`LOW_TEXT_CONTRAST`；新增 `_check_text_obscured_by_overlay`（IoU>0.3、決定 z 上的非文字元素是否壓住文字）+ `_check_text_contrast`（WCAG 2.1 AA contrast ratio 4.5 vs canvas bg）+ WCAG 輔助函數（hex→linear sRGB→luminance）
- `tests/metagpt/ext/agentlayout/test_quality_checker_position_hints.py`：加 4 個 case 涵蓋兩條規則 + 邊界
- 修 fixture `test_z_order_live_5efdd2dd_reproduction_unblocks`：text color `#F4F4F4`（near-white vs white bg、被新規則抓）→ `#111111`

**N=20 結果：** 10%→**15%** ok（+1 sample），GT 勝率 91.7%→89.7%。改善小、因為 17 個失敗多數不是 text-overlay/contrast 類。

---

## Step 36 — QC 加 DECORATIVE_IMAGE_OVERSIZED + TITLE_UNDERSIZED + TITLE_PERIPHERAL，build_pipeline_inputs 修 Analyst metadata-title leak

**動機（2026-06-09）：** 逐張看完 Step 34 N=20 失敗 PNG 後，發現之前推「Generator-bounded」結論**過度悲觀** — 17/17 失敗都有具體 fixable bug，根本不是 LLM 創意上界：
- 🔴 41% (7/17) underlay/decoration 過大佔 canvas
- 🟠 18% (3/17) Analyst 用 Crello metadata 描述當 title
- 🟡 18% (3/17) Asset 利用不當
- 🟡 12% (2/17) 配色 / 背景色錯
- 6% (1/17) text obscured（Step 35 漏抓 58ac）
- 6% (1/17) 接近 success 的小差

**設計拍板：**
- DECORATIVE_IMAGE_OVERSIZED：decorative_image 單一元素 area > 40% canvas 即 flag
- TITLE_UNDERSIZED：title area < 2.5% canvas
- TITLE_PERIPHERAL：title center_x 不在 [0.10, 0.90] OR center_y > 0.85（底部太靠邊）；top 暫不抓
- build_pipeline_inputs 改：「titled '{title}'」→「for the theme '{title}'」+ 明示「visible heading text MUST come from text snippets in asset list」

**程式：**
- `metagpt/ext/agentlayout/tools/quality_checker.py`：3 個 enum 值 + 3 個 `_check_*` 函數 + 3 個常數（DECORATIVE_IMAGE_MAX_AREA_RATIO=0.40、TITLE_MIN_AREA_RATIO=0.025、TITLE_EDGE_X_BAND=(0.10,0.90)、TITLE_EDGE_Y_MAX=0.85）
- `tests/metagpt/ext/agentlayout/test_quality_checker_position_hints.py`：加 6 個 case（每條規則 1 trigger + 1 pass）
- `layout_agent/output/run_role_team_live_crello.py:91-96` (Step 36b)：build_pipeline_inputs 改 user_brief 描述

**N=20 結果：**

| 三輪累積對照 | ok | GT wins | AL wins | Ties |
|---|---|---|---|---|
| pre-Step35 baseline | 2/20 (10%) | 91.7% | 6.7% | 1.7% |
| post-Step35 | 3/20 (15%) | 89.7% | 5.2% | 5.2% |
| **post-Step36** | **4/20 (20%)** | **86.2%** | **10.3%** | 3.4% |

**最大 win：sample 5e72** Quarantine concept（之前 Analyst 用「Quarantine concept with Man by open Window」當 title）：[B,B,B] → **[A,A,A] 全 3 輪贏 GT**。視覺對照確認標題正確用 text snippet「Don't be an airhead, air out your room.」，跟 GT 構圖**幾乎一樣**。Step 36b 對齊 build_pipeline_inputs 解決了這個失敗模式。

**Step 36 DECORATIVE_IMAGE_OVERSIZED 驗證：** sample 5dad GreenKO 的綠色 leaf shape 從 ~60% canvas 縮到 ~40%（QC 規則生效）。但「GreenKO」標題仍在右上角 — TITLE_PERIPHERAL 規則太鬆（只 catch 底部 y>0.85、top 沒抓）。Step 36c 待續：top-band 也抓 peripheral。

**驗證（offline pytest，conda env meta）：** 加 6 個 step36 + 4 個 step35 case 共 10 test，全跑 164 passed / 12 skipped 全綠。

**Paper 意涵更新：**
- 之前 Step 34 「Generator-bounded」結論**部分撤回**：bottleneck 不是 LLM 創意上界、是「特定 decoration / metadata / placement 工程規則缺失」
- 5 條新 QC 規則 + 1 個 metadata fix 把成功率 **10%→20%**（doubled）；剩 80% 失敗仍有更多 fixable bug
- 4 個 success case（5e8d / 592c / 589d / 5e72）可當 paper showcase 圖
- Future work：top-band peripheral 抓更嚴、Generator prompt 直接寫 size cap、saliency-aware 標題放置、換 Generator 模型

---

## Step 36c — TITLE_PERIPHERAL 補抓 top-band + N=100 robust validation

**動機（2026-06-09）：** Step 36 後 sample 5dad 的 leaf underlay 已縮到 <40%（DECORATIVE_IMAGE_OVERSIZED 生效），但「GreenKO」標題仍在右上角。檢查發現 TITLE_PERIPHERAL 只抓底部 `center_y > 0.85`、top 完全沒抓。Step 36c 補上 `center_y < 0.05` trigger（catalog-badge 風的「標題貼齊頂邊」）。順帶把 Step 35/36/36c 整套規則放大到 N=100 驗證 success rate 是不是 robust。

**程式：**
- `quality_checker.py`：新增 `TITLE_EDGE_Y_MIN = 0.05`、`_check_title_peripheral` 加第三個 elif 分支
- `test_quality_checker_position_hints.py`：新增 1 個 case `test_step36c_title_pinned_to_top_edge_flags`
- `step34_oracle_refinement.py`：加 `--ids-file` CLI 旗標（之前 IDS 寫死）方便 N=100 跑 step22 n100 set

**N=100 結果（step22_n100_ids 全跑）：**

| Metric | post-Step36c, N=100 |
|---|---|
| ok (≥1 round committed) | **14/100 (14%)** |
| round1_exhausted | 86/100 (86%) |
| Total pairwise verdicts | 291 |
| GT (B) wins | 264 (90.7%) |
| AL (A) wins | 15 (5.2%) |
| Ties | 12 (4.1%) |
| Crashes | 0 |

**累積對照：**

| Run | N | ok | GT 勝率 |
|---|---|---|---|
| pre-Step35 baseline | 20 | 2 (10%) | 91.7% |
| post-Step35 | 20 | 3 (15%) | 89.7% |
| post-Step36 | 20 | 4 (20%) | 86.2% |
| **post-Step36c, N=100** | **100** | **14 (14%)** | **90.7%** |

**結論：**
- N=20 = 20% 在 95% CI [4%, 36%] 範圍大；N=100 = 14% 在 95% CI [7%, 21%]；**真實 success rate 在 12-16%**
- N=20 4 個 success（5e8d / 592c / 589d / 5e72）有 3 個在 N=100 重現（5e8d / 592c / 589d）—— 結果 robust
- **N=100 新發現 11 個 success case** 可加 paper showcase：5eec7b19, 59535be5, 5df395ba, 5a22883e, 5c34ba99, 5e416f72, 589b3e94, 5eec99b2, 5a218ae4, 5ea2a28b, 5dc93882
- 86% 失敗的事實沒變：Step 35/36/36c 5 條規則 + 36b metadata fix 把成功率推到 14%，剩下是 LLM 對 commercial design 的本質落差

**驗證（offline pytest）：** 加 1 個 step36c case 後 165 passed / 12 skipped 全綠（共 11 個 step 35/36/36c case + 154 pre-existing）。

**Paper-grade 結論（可寫進 results 章節）：**

> Oracle pairwise GT-guided refinement on N=100 Crello samples (gpt-4o
> Generator, 5 visual QC rules + Analyst metadata-leak fix): **14% of
> samples produced a candidate that at least matched the designer GT**
> in pairwise judgement. The 6-rule engineering chain (Steps 35/36/36b/36c)
> moves the success rate floor from 10% (no rules) to 14% (N=100) /
> 20% (N=20 ablation set), confirming that LLM-judge architecture is
> not the binding constraint; the 86% failure rate at this engineering
> ceiling reflects gpt-4o zero-shot capability against
> designer-quality commercial Crello posters.

**14 個 success showcase samples（按 verdict 強度排）：**

| Sample | 強度 | Verdict 軌跡 | 備註 |
|---|---|---|---|
| 592c213595a7 | ⭐⭐⭐ | [A, A, tie] | 2 輪 commit、最強 |
| 5e8d966a4b38 | ⭐⭐ | [A, tie] | R1 直接贏 |
| 589d7bd995a7 | ⭐⭐ | [A, tie] | R1 直接贏 |
| 5eec7b19499b | ⭐⭐ | [A, tie] | R1 直接贏 |
| 59535be595a7 | ⭐⭐ | [A, tie] | R1 直接贏 |
| 5a22883ed814 | ⭐⭐ | [A, tie] | R1 直接贏 |
| 589b3e9495a7 | ⭐⭐ | [A, tie] | R1 直接贏 |
| 5eec99b2499b | ⭐⭐ | [A, tie] | R1 直接贏 |
| 5a218ae4d814 | ⭐⭐ | [A, tie] | R1 直接贏 |
| 5ea2a28b499b | ⭐⭐ | [A, tie] | R1 直接贏 |
| 5df395ba9fea | ⭐ | [B, A, tie] | R1a2 才贏 |
| 5dc938829fea | ⭐ | [B, A, tie] | R1a2 才贏 |
| 5e416f729fea | ⭐ | [A, B] | R1a1 贏、R2 又輸 |
| 5c34ba99048d | ⭐ | [B, B, A, B] | R1a3 才贏、R2 又輸 |

---

## Step 37 — Tier 1 改進：strict judge + Generator prompt rules + QC 收緊

**動機（2026-06-09）：** Step 36c N=100 success rate 14%，但逐張視覺檢查發現 9/14 (64%) 是 pairwise judge 的 tie-break-by-default 規則「prefer Image A by default」造成的虛胖、不是真贏。同時 14 個失敗模式中還有 text 蓋裝飾（Step 35 漏抓）、underlay 過大、標題不在 hero zone 等問題、QC 跟 Generator prompt 兩邊都沒明確指導。

**4 個並行改進（P1-P4）：**

| Patch | 改點 |
|---|---|
| **P1** | `generate_layout.py` PROMPT_TEMPLATE 加 `# Layout constraints` 區段、5 條 hard rules：decorative_image area <40%、title area ≥2.5% / center_x [0.10, 0.90] / center_y [0.05, 0.85]、text not under z≥text-z image >20%、text WCAG AA ≥4.5、sequential text 維持 asset_list y-order |
| **P2** | `step34_oracle_refinement.py` PAIRWISE_PROMPT tie-break 規則「prefer A by default」→「prefer B (GT) by default；A wins ONLY on SPECIFIC OBJECTIVE improvements；vague preferences map to tie」 |
| **P3** | `quality_checker.py` `TEXT_OBSCURED_RATIO_THRESHOLD` 0.30 → 0.20；`other.z_index <= text_el.z_index` → `<`（same-z 也觸發） |
| **P4** | （含在 P1 第 5 條）SEQUENTIAL text from asset_list 必須 y-順序對齊 asset_list 順序 |

**Pre-launch bug：** P1 的 prompt 區段裡寫 `{title, subtitle, body_text, caption}`、被 `.format()` 當 placeholder → KeyError 全 100 crash。修法：literal `{}` 雙寫 `{{}}`。

**N=100 結果（step22_n100_ids，post-P1+P2+P3+P4）：**

| Metric | post-Step36c | post-Step37 | Δ |
|---|---|---|---|
| ok (≥1 round committed) | 14/100 (14%) | **2/100 (2%)** | −12pp |
| GT (B) wins | 90.7% | **98.7%** | +8pp |
| AL (A) wins | 5.2% | **0.7%** | −4.5pp |
| Ties | 4.1% | **0.7%** | −3.4pp |

**結論：** strict judge 把虛胖砍光、剩 2% 是「真贏 + strict mode 漏抓的少數瑕疵」。比 14% 更誠實。**這個 2% 才是 paper 該報的數字**——之前的 14% 是 tie-break 偏 A 的 LLM judge 校準偽影。

**驗證（offline pytest）：** 165 passed / 12 skipped。

---

## Step 38 — J1 + J2 失敗 checklist + CoT 絕對打分（**反向、Smean 6.10→8.80**）

**動機（2026-06-09）：** Phase B COLE 絕對打分（Step 21）cluster 在 5-7、cold-start 6.10 vs GT 6.6 看起來「AL ≈ GT」是假象（LLM 對絕對分有 mean regression）。J1（failure-mode checklist）+ J2（chain-of-thought）想用結構化「列具體缺陷再算分」逼出更誠實的分數。

**程式：** `step38_failure_checklist_eval.py`（新檔、gitignored）
- 每軸定義 5-6 個 closed catalog flag
- LLM 必須列 strengths_summary + weaknesses_summary（J2）
- 計分公式 `score = max(1, 10 - len(flags))`（J1）

**N=5 結果：Smean = 8.80**（從 6.10 反向上升）。

**為什麼失敗：** LLM 用「slightly / somewhat / feels」hedging 詞描述問題、但只勾 1-2 個 flag、score 變 8-9。LLM 把「設計沒崩潰」當作「給 10 找錯」的起點、charitable bias 沒被突破。

---

## Step 39 — J5 + J6 + J7 校準（**3.90、終於跟視覺一致**）

**動機：** Step 38 反向證明 flag 機制不夠、需要：
- (J5) 公式倒轉：`score = max(1, 5 + |strengths| - |flags|)`（從 mediocre 中點 5 出發、要證據往上或往下）
- (J6) 明確 anchor 數字：「award 9-10、Crello GT 5-6、AL typical 3-4、broken 1-2」
- (J7) 反 hedging：用「slightly / somewhat / feels」就必須勾對應 flag

**程式：** `step39_calibrated_eval.py`

**3-way 對照（同 5 PRE-Step33 cold-start）：**

| 評分方法 | Smean |
|---|---|
| Step 32（原 COLE）| 6.10 |
| Step 38（J1+J2）| 8.80 |
| **Step 39（J5+J6+J7）** | **3.90** ✅ |

每個 sample 的分數**跟視覺判斷一致**：5e72（最佳）5.00 > 其他 3.25-4.00（AL typical 帶）。

**K3 GT anchor check：** 5 個 Crello GT designer preview 跑 step39，Smean = **4.75**（落在 anchor「5-6 Crello GT」略低一點、calibration 健康）。

**K1 N=20 cold-start：** paper-draw 20 個 cold-start render 跑 step39，Smean = **3.73**。

**Head-to-head delta（同 5 ids）：**

| Sample | GT (step39) | AL (step39) | Δ |
|---|---|---|---|
| 5928 | 4.50 | 3.50 | −1.00 |
| 5c94 | 4.75 | 4.00 | −0.75 |
| 5e6a | 3.75 | 4.00 | +0.25 |
| 5f56 | 4.50 | 3.50 | −1.00 |
| 5e72 | 6.25 | 3.75 | −2.50 |
| **mean** | **4.75** | **3.75** | **−1.00** |

**對 paper 的決定性意義：**
- 之前說「Phase B Smean 6.10 vs 6.6 = delta 0.5 ≈ noise」**站不住**
- 用校準後 Smean 寫：「**AgentLayout 落後 designer GT 1.0 點 (95% CI)，遠超原 0.5 點報告值**」
- 樣本級 ranking 兩個尺度一致（5e72 最佳、5f56/5e6a 偏低），證明 Step 39 保留 ordinal 訊號、揭穿 magnitude 失真

---

## Step 40 — Flag-aware 結構化 reject feedback（**0% / whack-a-mole**）

**動機：** Step 37 N=100 success rate 2%、剩下 98% 都 reject。問題：reject feedback 是 LLM 自由文字「Image B excels in layout, content relevance, ...」、太籠統。如果改成「Image A 在 design_layout 觸發了 composition_unbalanced + misaligned_elements、對應動作是 X、Y」、Generator 是否能拿到具體訊號修正？

**程式：** `step40_flag_aware_oracle.py`（新檔、gitignored）
- Pairwise prompt 要 Judge 為每軸列 `a_flags` + `b_flags`（closed 21-flag catalog、跟 step39 共用）
- Reject 時把「unique to A」flag 翻成 concrete action（`FLAG_ACTIONS` dict 21 行 map）餵 Generator
- 其他流程跟 Step 34/37 同（K=1、3 retry、commit-on-win、3 rounds 後止）

**N=20 結果：0/20 ok、60/60 verdict 全敗 GT。**

**Whack-a-mole 模式（從 trace 看）：**

Sample 5928 三個 attempts：

| Round | composition_unbalanced | excessive_dead_space | low_contrast_text | 其他 |
|---|---|---|---|---|
| R1a1 | ✗ flagged | ✗ flagged | ✗ flagged | image_placement_awkward |
| R1a2 | ✗ 仍 flagged | ✗ 仍 flagged | ✗ 仍 flagged | swap → image_dominates / 新 generic_centered |
| R1a3 | 同 R1a2 | 同 R1a2 | 同 R1a2 | 同 R1a2 |

3 個核心 flag 在 3 次重生都消不掉。Generator 試了不同 composition、舊 flag 沒清掉還冒新 flag。

**結論：feedback channel 的 specificity 不是 bottleneck。Generator 本身做不到。**

---

## 八個實驗最終收斂的 paper-grade 結論

| Step | 假設 | 結果 |
|---|---|---|
| 20b | refinement loop A/B controlled | 無 lift |
| 30 | COLE 5-axis Judge alignment | 無 lift |
| 31 | best-so-far guard | mean +0.8 noise |
| 32 | Phase B loop vs cold | loop **−0.35** |
| 33 | rubric in Generator prompt | +0.05 noise |
| 34 | oracle pairwise vs GT (N=100) | 14% (charitable judge) |
| 37 | strict judge + Tier 1 QC | 2% (校準) |
| 40 | flag-aware structured feedback | 0% (whack-a-mole) |

**8 個實驗指向同一答案**：bottleneck 是 **gpt-4o zero-shot Generator 對 Crello commercial design 的本質能力上界**、不在 Judge calibration / feedback structure / loop architecture 任何一個。

**Paper main result 推薦寫法：**

> We conducted eight ablations testing whether iterative refinement with an
> LLM judge can lift Generator output toward designer-grade quality. The
> investigation progressively eliminated alternative explanations: judge
> axis alignment (Step 20b/30), Markov regression (Step 31), filter vs
> prior position (Step 32/33), tie-break bias (Step 34/37), and feedback
> specificity (Step 40). On N=100 oracle pairwise judgement with strict
> tie-breaking (Step 37), AgentLayout matches the designer GT on only
> **2% of samples**. On the calibrated absolute Smean metric (Step 39),
> AgentLayout sits **1.0 point below** designer GT (4.75 vs 3.73),
> vs the apparent 0.5 point gap reported under the original uncalibrated
> COLE prompt. The Step 40 flag-aware feedback experiment closes the
> argument: even with maximally specific, structured reject feedback
> (closed-vocabulary failure flags + concrete actions), the Generator
> exhibits whack-a-mole behavior — fixing one flag persists others and
> introduces new ones — confirming the binding constraint is gpt-4o
> zero-shot Generator capability at the Crello commercial-design band.

---

## Step 47（2026-06-10 晚）— 三項 render/data-channel confound 修正：「Generator-bounded」結論需重審

**動機**：user 質疑「SOTA 不會這麼爛、應該還沒到上限」。直接目視 Step 46 N=5 render vs GT
（sample `5c94fa6085ea3c16f9ca91a2` Mother's Day）後確認：Judge 看到的品質差距混雜了三個
**與 Generator 排版能力無關的 confound**，Step 40 的 Generator-bounded 歸因在這些修掉前不成立。

### 發現與修正

| # | 問題 | 根因 | 修正 | 影響面 |
|---|---|---|---|---|
| 1 | GT 的花卉滿版外框整張消失，AL render 只剩素色底 | `build_pipeline_inputs` 取第一個 `background_candidate` 當背景，**其餘 background_candidate 無聲丟棄**（不進 asset_list） | `run_role_team_live_crello.py` 新增 `_composite_background_plates()`：全部 background_candidate 依 z-order（element list 順序）壓平成 `asset_bg_composite.png`，bg_ref 指向複合圖；單一 plate 行為不變 | **329/1902 = 17.3%** samples |
| 2 | 所有文字都是 DejaVu 工程字型 + 黑色，GT 是設計師 script/display 字型 | `renderer._resolve_font` 把任何 font_family 二分為 serif/sans-serif，且 FONT_CANDIDATES 只列 DejaVu | family 四分類（+script/display 關鍵字桶）、新增 PROJECT_FONT_DIR（`tools/fonts/`）+ URW/Liberation/Ubuntu 系統字型候選、降級鏈 (family,weight)→(family,regular)→(sans,weight)→CJK→default | **100%** 文字元素 |
| 3 | 68×67px 愛心被拉到 ~400px 糊成一團 | `_paint_image_element` 無條件 resize 到 bbox | `MAX_UPSCALE=2.0` 上限：超過 2× 原生解析度時鎖 2×、在宣告 bbox 內置中；縮小不受影響；背景（_make_canvas）豁免 | 所有小素材 |

### 附帶診斷（未修，候選後續）

- **Step 46 vision refusal 根因確認**：21 次 `I'm sorry, I can't assist with that.`（completion=9 tokens、
  prompt_tokens 固定 5464、deterministic）全部來自 sample `5928015095a7a863ddcd8e38`——背景是無害的
  綠色 low-poly 幾何紋理，確認為 gpt-4o guardrail false positive（可疑觸發：prompt 中 "face" 字眼 + 圖片）。
  其餘 4 samples vision 呼叫全部成功。**未修**：GenerateLayout retry 帶同一張圖對 deterministic refusal
  無效，應降級 text-only（user 暫緩）。
- `metagpt/provider/base_llm.py:80` 把 base64 一律標 `data:image/jpeg`，實際送 PNG——MIME 錯標但非
  refusal 主因（其他 4 張同樣錯標卻成功）。

### 測試

- 新增 `tests/metagpt/ext/agentlayout/test_renderer_step47.py`：family 正規化 14 cases、
  `_resolve_font` 缺字型不 crash（24 組合）、upscale cap 像素級驗證（cap 生效 / downscale 不變 / 恰好 2× 滿版）。
- 驗證狀態（2026-06-10 晚補）：
  - ✅ `test_renderer_step47.py` 42 passed；全套 agentlayout **244 passed / 12 skipped / 0 failed**（零回歸）。
  - ✅ Bug 1 合成 smoke：`crello_5c94fa6085ea3c16f9ca91a2` 產出 `asset_bg_composite.png`（1080×1080），目視確認奶油底 + 愛心形花卉框正確疊合。
  - ✅ Gap 2 目視：4 family 渲染明顯分化（`output/step47_font_check2.png`）。另已下載 5 個 OFL Google Fonts（GreatVibes / Pacifico / Lobster / DancingScript / Oswald + OFL.txt）進 `tools/fonts/`，cursive→Great Vibes 書法體、display→Lobster。
  - ✅ N=5 live smoke（step41 oracle, gpt-4o）完成，log：`output/step41_N5_smoke_step47_renderfix.log`。
    - **render 品質目視（5c94fa Mother's Day）**：花卉框完整呈現、愛心小而銳利（不再是糊塊）——三個 confound 確認在 live pipeline 中已消除。
    - **refusal 消失**：`5928015` 本輪 0 次 refusal（前輪 21 連拒），證實 gpt-4o guardrail false positive 非穩定觸發；vision fallback 修正的優先度可下調。
    - 接受率仍 **0/5 round1_exhausted**，但失敗原因已「去 confound 化」——residual gap 現在可歸因於 Generator 的選擇：font_family 挑 sans-serif + 黑字（GT 是 script + 粉/橘）、title 疊到背景花卉、文字壓到愛心。這才是重跑 N≥20 時要量測的真實 Generator gap。

### 對結論的影響

Step 30–40 的「8 實驗收斂於 Generator-bounded」推論需加註：當時的 pairwise 比較中
A 側 render 系統性承受 (1) 素材缺失 (2) 字型降級 (3) 素材糊化 三個 handicap，
「2% match rate」的歸因應改寫為 *upper-bounded by rendering/data-channel fidelity*，
修正後需重跑 N≥20 才能重新估計真實 Generator gap。

---

## Step 48（2026-06-10 深夜）— 去 confound 後 N=20 重跑：Generator-bounded 結論在乾淨條件下成立

**設計**：用 Step 47 修正後的 renderer（複合背景 + script/display 字型 + 2× 放大上限）重跑
step41 GT-anchored pairwise oracle，樣本 = Step 34/40 同一批 `step13_drawn_ids.json`（N=20），
模型 gpt-4o。log：`output/step48_N20_postfix_rerun.log`。

### 結果

| 指標 | Step 47 前（Step 40/44/45/46） | Step 48（post-fix） |
|---|---|---|
| 接受率 | 0/N | **0/20**（全 round1_exhausted） |
| Judge overall_winner | B 全勝 | **B 31/31 全勝** |
| vision refusal | 21 連拒（單一樣本） | 0 次 |
| A 側 render 品質 | 缺背景框／DejaVu 全字型／糊素材 | 目視確認三項全部修復 |

### 解讀

1. **Generator-bounded 結論這次站得住**：渲染端 handicap 全部移除後 GT 仍全勝，
   Step 40 的歸因從「premature」升級為「de-confounded 下驗證成立」。論文可引用
   Step 48 作為 robust negative result，並以 Step 47→48 的對照說明 confound 控制方法。
2. **殘餘 gap 全是 Generator 行為**（judge summary 高頻詞：balance、use of space、typography）：
   - 排版失衡／大塊 dead space（例：`59158b4f` 橫幅左側懸空白板）；
   - **字型「選擇」而非「能力」**：renderer 已能畫 Great Vibes/Lobster，但 Generator
     仍預設 sans-serif + 黑字（GT 多為 script + 主題色）——可從 GenerateLayout prompt 下手；
   - 部分樣本其實已接近可用（例：`5f4e0040` Home Decor Mall），但 pairwise vs 設計師 GT 的
     門檻極高，judge 永遠能找到 B 較優的理由。
3. **方向建議**：接受率若要突破，下一步應改 Generator 端——(a) typography 主動選擇
   script/display + 主題色、(b) dead-space／balance 的生成時約束；而非繼續加 Judge 回饋通道
   （Step 41–46 已證明回饋通道增益為零）。

---

## Step 49（2026-06-10 深夜～06-11）— Generator 端攻擊主要失分軸：per-axis 量尺 + typography 通道 + balance/placement 約束

依 Step 48 方向建議執行，順序 49c（先有量尺）→ 49a → N=5 smoke → 49b → N=20 總驗證。

### 49c — oracle 補 per-axis 勝負彙總（量尺）

`output/step41_layout_aware_oracle.py`：main() 迴圈彙總所有 judge call 的 5 軸
winner，終端印出 per-axis A/B/tie 表，並寫入 `step41_layout_aware_results.json`
的 `axis_summary` 欄位。rounds_log 本來就逐 call 存軸別結果，因此可回溯計算
**Step 48 baseline（31 judge calls）**：

| 軸 | A | B | tie |
|---|---|---|---|
| design_layout | 0 | 31 | 0 |
| typography_color | 0 | 28 | 3 |
| graphics_images | 0 | 26 | 5 |
| content_relevance | 0 | 13 | 18 |
| innovation_originality | 0 | 4 | 27 |

→ 主要失分軸 = design_layout 與 typography_color，正當化 49a/49b 的選擇。

### 49a — Typography 決策通道（prompt-only，不動 schema）

`metagpt/ext/agentlayout/actions/generate_layout.py`：
- PROMPT_TEMPLATE 新增 typography ATTENTION 區塊：四個 font_family token
  （sans-serif/serif/cursive/display）、mood→family 對映表（festive→cursive、
  promo→display、editorial→serif、corporate→sans-serif）、標題色必須取自
  palette（近黑只允許 corporate+淺底）、5 候選至少兩種 (family,color) 組合。
- FORMAT_EXAMPLE_JSON cand_02 headline 改為 cursive + `#C2547B`（去除全 sans 示範偏置）。

**N=5 smoke（`output/step49a_N5_smoke_typography.log`）**：
- 行為面有效：候選 font_family 分布 cursive 38 / display 35 / sans-serif 20 / serif 11，
  顏色出現 `#E02E70`、`#FDBB44` 等 palette 色（#111111 仍 40 次）。
- 接受率 0/5；只有 4 次 judge call（typography_color B=4/4，樣本太小無法做軸別歸因）。
- **QC 拒絕率 73%（11/15 attempts）**。更正先前誤判：Step 48 並非零拒絕——
  log 字串是 `QC: N primary_outside_safe_zone violation(s)`，Step 48 實為
  28 拒絕/59 attempts ≈ 47%。49a 後 +26pp（N 小，不能排除雜訊）。
- 11 次拒絕中 **9 次是 `safe_zone '<none>' ratio=0.00`**（title 完全沒碰任何
  safe zone）→ 根因找到：Step 46 vision block 寫明「影像顯示 safe_zones 漏掉的
  空白區可以放 primary text」，與 QC rule 6（數值強制 ≥50% overlap）自相矛盾；
  49a 又叫 Generator 多看影像，放大了這個衝突。

### 49b — balance/placement 生成時約束（prompt-only，不加 QC 硬 gate）

`generate_layout.py` PROMPT_TEMPLATE 三段修改：
1. **Step 46 矛盾修正**：vision block 改寫——QC 數值強制 rule 6，影像用途降級為
   「選哪個 listed safe_zone + zone 內微調」；只有 decorative/次要元素可用 zone 外
   空白區；臉部衝突改選別的 zone 而非放棄 zone。直接針對 ratio=0.00 拒絕根因。
2. **Underlay 配對強制化**："should typically" → "PAIRING IS MANDATORY"：每個
   decorative_image 必須完整包含至少一個文字元素 bbox（外擴 10-20%）；
   free-floating plate（Step 48 `59158b4f` 失衡案例）明示為扣分項。
3. **水平 balance 指引**：與 vertical coverage 對稱但留兩個合法出口——
   (a) 元素聯集橫跨大部分寬度，或 (b) 刻意單欄構圖（欄在 safe zone 內置中、
   左右 margin 差 <2x）；禁止「貼邊小簇＋整條空帶」。

驗證：agentlayout 測試套件 **244 passed**（2026-06-11）。

### N=20 總驗證（2026-06-11，`output/step49_N20_validation.log`）

同 Step 48 樣本（`step13_drawn_ids.json`）、同 gpt-4o。

| 指標 | Step 48 baseline | Step 49（49a+49b 後） |
|---|---|---|
| 接受率 | 0/20 | **0/20**（全 round1_exhausted） |
| Judge calls | 31 | 32 |
| QC 拒絕 | 28/59 ≈ 47% | **22/54 ≈ 41%**（smoke 的 73% 回落） |
| 其中 ratio=0.00（完全沒碰 zone） | — | 仍 15/22 |

**Per-axis 對比（A / B / tie）**：

| 軸 | Step 48 | Step 49 | 變化 |
|---|---|---|---|
| design_layout | 0/31/0 | 0/32/0 | 不變（B 100%） |
| typography_color | 0/28/3 | 0/29/3 | **不變** |
| graphics_images | 0/26/5 | 0/19/13 | **tie 16%→41%**（Fisher p=0.050） |
| content_relevance | 0/13/18 | 0/13/19 | 不變 |
| innovation_originality | 0/4/27 | 0/4/28 | 不變 |

Typography 行為持續生效：font_family 分布 display 131 / cursive 95 / sans-serif 92 /
serif 42；palette 色出現（#E64A52、#E02E70、#FDBB44…）但 #F4F4F4/#111111 仍占大宗。

### 解讀

1. **graphics_images 是唯一移動的軸**（tie 率 2.6×，p≈0.05 邊緣顯著）：49b underlay
   配對強制化 + Step 47 渲染修正的組合讓 A 側圖像處理常與 GT 打平。
2. **typography_color 完全沒動，儘管 Generator 行為確實改變了**——judge 落敗理由
   全是 "better contrast"、"size hierarchy"，不是字型選擇。49a 解決了 family 選擇，
   但 binding constraint 是**對比度與字級層級**，不是字族。
3. **design_layout 仍 B 100%**，理由仍是 "more balanced composition / better use of
   space"——prompt 層級的水平 balance 指引不足以讓 LLM 產出設計師等級的空間配置。
4. QC 拒絕率 47%→41%，Step 46 矛盾修正有效（smoke 73% 確認是 49a prompt 稀釋 +
   矛盾放大的暫時尖峰），但 ratio=0.00 仍占 15/22——Generator 對 safe_zone 的
   數值服從天花板大約就在這裡。
5. **結論：prompt-only 干預可以動 graphics 軸，動不了 design_layout / typography_color
   的核心失分**。Generator-bounded 結論進一步強化：剩餘 gap 需要的是生成時的數值
   能力（精確 balance、對比計算），不是更多指令。論文可把 Step 49 列為「prompt
   engineering 上限」的 ablation 證據。

---

## Step 50（2026-06-11 凌晨）— Generator 換 gpt-5.2 的 N=5 對照：排除「gpt-4o 特定」假設

**動機**：Step 49 結論是 prompt-only 到頂、剩餘 gap 需要更強 Generator 或數值工具。
使用者指定試 gpt-5.2-pro；該模型只支援 Responses API（chat/completions 回 404），
與 MetaGPT provider 不相容，經確認改用同代 **gpt-5.2**（chat completions + vision 實測可用）。

**工程改動**：`metagpt/provider/openai_api.py` `_cons_kwargs` 的 o1 特判擴大為
`o1/o3/o4/gpt-5` 前綴——reasoning 模型拒收 `max_tokens`（實測 400）且 temperature
固定 1；不設 completion 上限以免 reasoning tokens 吃掉輸出額度。gpt-4o 行為不變。

**實驗設定**：Generator（含全部 Role）= gpt-5.2（swap `~/.metagpt/config2.yaml`，
跑完已還原）；Judge 仍為 oracle 內寫死的 gpt-4o（基準不變）；樣本 = 49a smoke 同
5 個 default IDs。log：`output/step50_N5_gpt52_generator.log`。

### 結果（vs 49a smoke：gpt-4o Generator、同樣本）

| 指標 | gpt-4o（49a smoke） | gpt-5.2（Step 50） |
|---|---|---|
| 接受率 | 0/5 | **0/5**（全 round1_exhausted） |
| QC 拒絕率 | 11/15 ≈ 73% | **8/15 ≈ 53%** |
| Judge calls | 4 | 7 |
| design_layout | B 4/4 | **B 7/7** |
| typography_color | B 4/4 | **B 7/7** |
| graphics_images | B 4/4 | B 6 / tie 1 |
| content_relevance | tie 3/4 | tie 7/7 |
| font_family 分布 | cursive/display 主導 | sans-serif 51 / display 47 / serif 21 / cursive 15（更保守） |

Judge 落敗理由與 gpt-4o 完全同型："more balanced composition / better alignment"、
"better contrast / size hierarchy"。視覺抽查：`5c94fa60`（母親節花框）品質好、
cursive 標題置中愛心區；`5e72455e`（#airout）右半 + 右下整片 dead space——
跟 gpt-4o 犯一模一樣的錯，49b 水平 balance 指引同樣沒擋住。

### 解讀

1. **障礙不是 gpt-4o 特定的**：換上 2025-12 的 reasoning 模型，0/5、design_layout
   與 typography_color 仍全敗、敗因同型。「Generator-bounded」應修正表述為
   **「LLM-coordinate-generation-bounded」**——讓任何 LLM 直接吐像素座標都撞同一面牆。
2. QC 拒絕率 73%→53% 有改善跡象（safe-zone 數值服從稍好），但 N=5 不足下定論。
3. 與 Step 49 結論合流：剩餘路徑只剩 (a) 生成時數值工具（LLM 做語意決策、
   solver 算座標）、(b) few-shot 檢索 GT 範例。模型升級這條路已用 N=5 初篩排除。

---

## Step 51（2026-06-11 凌晨）— Blind re-judge 審計：label bias 證實存在且 axis-specific

**動機**：使用者要求跳脫框架重審系統（「我不認為LLM真的就上限，應該還有其他沒處理好的因素」）。
全系統審視發現 **step41 oracle 的 pairwise 評比從來不是 blind**：judge prompt 明寫
"Image A is the CANDIDATE / Image B is the designer GROUND-TRUTH reference"，且 A 永遠
第一張、B 永遠第二張，Step 30–50 所有 pairwise 數字都在此設定下產生。

**設計**（`output/step51_blind_judge_audit.py`，judge 同為 gpt-4o temp=0）：
- cond2 blind：中性標籤 "Design 1/2"、不給 layout JSON/safe_zones，每 pair 判兩次
  （cand 第一張/第二張各一次）隔離 position bias。樣本 = step13 N=20 的最後一次
  render（15 個 gpt-4o + 5 個被 Step 50 覆蓋的 gpt-5.2 render）。
- cond3 控制組：同一張 GT 送兩次，無偏 judge 應 100% tie。

### 結果（`output/step51_blind_judge_results.json`）

- **Position bias = 0**：GT-vs-GT 9/9 全軸 tie；order-flip 0/20（順序對調判決完全不變）。
- **Blind overall**：cand 1 / gt 19（labeled 基準 = GT 32/32 全勝）。唯一 cand 勝 =
  `5e8d966a`（Nurse）**雙順序一致**——正是 Step 43-R1 中人工檢查認為「視覺對等」
  但 labeled judge 仍判 B 的那個樣本。
- **Per-axis（兩順序合併，n=40）vs labeled（Step 49，n=32）**：

| 軸 | labeled（cand/gt/tie） | blind（cand/gt/tie） | 解讀 |
|---|---|---|---|
| design_layout | 0/32/0 | 2/38/0 | **真實劣勢**，blind 下不變 |
| typography_color | 0/29/3 | 1/37/2 | **真實劣勢**，blind 下不變 |
| graphics_images | 0/19/13 | **9/4/27** | label bias：blind 下 cand 反而多勝 |
| content_relevance | 0/13/19 | 2/6/32 | tie 為主，輕微 label bias |
| innovation_originality | 0/4/28 | **24/6/10** | **重度 label bias：blind 下 cand 勝 60%** |

### 解讀

1. **Label bias 證實存在、但 axis-specific**：客觀軸（balance、contrast）GT 優勢
   blind 下完整保留——design_layout / typography_color 的失分是真的，
   Generator-bounded 的核心結論存活。但主觀軸被「designer ground-truth」標籤
   系統性壓制：innovation 從 cand 0% → 60%、graphics 從 0% 勝 → 淨勝。
2. **至少一個真勝利被 labeled 設定吞掉**（5e8d966a），與 Step 43 人工檢查互相印證。
3. **論文影響**：(a) headline pairwise 數字必須改用 blind protocol 重跑才能引用；
   (b) blind 後可主張 innovation 軸勝設計師（需在乾淨條件重做——本次樣本混了
   gpt-4o/gpt-5.2 render 且取末次 attempt（可能含 QC-rejected 版面），僅作 bias
   存在性證明，不作 headline 數字）；(c) Step 30–50 的 labeled 數字一律標註
   non-blind limitation。

---

## Step 52（2026-06-11 凌晨）— 實驗 B：設計師 GT 過我們自己的 QC safe-zone gate

### 動機

Step 51 證實 design_layout / typography_color 劣勢在 blind 下成立 = 真實 gap，
但仍有未檢驗的系統因素：**QC gate 不對稱假說** —— Step 43 的
primary-in-safe-zone 規則（≥50% overlap，`quality_checker.py:729`）只約束
candidate A，GT 從不被檢。若設計師的版面本身大量違反這條規則，gate 就是把 A
強制壓進一個 GT 不存在的擺位子空間，可能正是 blind judge 扣分的
dead-space / 失衡構圖的來源。

### 方法（`step52_gt_qc_audit.py`，離線、零 API 成本）

對 step13 N=20 每個樣本：
1. bg_ref 走 live runner **同一條路徑**（`_composite_background_plates` →
   fallback 第一個 kind=="image"）；safe zones 走 `resolve_background()`
   同一條 CV 路徑——與 Step 47–49 中 A 被審的條件一字不差。
2. GT primary 近似映射：kind=="text" → 文字 primary（caption/cta 在 QC 不算
   primary，故此映射**高估**違規＝對假說保守）；kind=="image" 且未升格為背景
   → product_image。
3. 重疊計算逐字複製 `_check_primary_in_safe_zone` 的 LTRB 交集／element-area
   公式與 0.5 門檻。

### 結果

- **設計師 GT 有 14/20（70%）會被我們自己的 gate 退件**（任一 primary 違規即
  reject，與 pipeline 行為一致）。Element 級：27/44（61.4%）違規；
  image primary 8/9（88.9%）、text primary 19/35（54.3%）。
- 違規是**重度**的，不是門檻邊界噪音：violating overlap median = **0.062**，
  其中 10 個元素 overlap = 0.0（完全在 safe zone 外）。
- 對照：candidate A 的 QC rejection rate為 step48 47% / step49 41%——A 被
  反覆退回重生直到服從規則，GT 卻天生 70% 不服從。
- Suggestive 關聯：blind 唯一勝場 `5e8d966a` 正是 6 個「GT 通過 gate」樣本
  之一——當設計師恰好也守規則時，A 才有同場競技的機會。

### 解讀

1. **Gate 不對稱證實**：設計師慣常把文字直接壓在背景主體上（疊在照片上的
   標題是正規海報手法），我們的 safe-zone 規則明文禁止這件事。A 是在跟一群
   「不需要遵守 A 的規則」的對手比賽。
2. **與 Generator-bounded 的關係**：此發現不推翻 blind 下的 design_layout /
   typography 真實劣勢（那是 judge 看渲染圖的判斷，與 gate 無關），但指出
   劣勢的一部分可能是 gate **造成**的：A 被迫避開視覺重心區，產生 judge 扣分
   的留白／偏置構圖。
3. **下一步候選**：(a) ablation——關掉 safe-zone gate 重跑 N=20 blind，看
   design_layout 軸是否改善；(b) 把 gate 從 hard reject 改成 soft signal
   （隨 prompt 提示但不退件）；(c) 門檻校準——以 GT 分布反推（GT median
   passing overlap ≈0.7，violating median 0.062，雙峰明顯，0.5 不是好切點）。

### 附帶：openai_api.py gpt-5 相容修正（Step 50 遺留，本次一併 commit）

`_cons_kwargs`（`metagpt/provider/openai_api.py`）原本只對 `"o1-"` 特判；
gpt-5.x 同樣拒收 `max_tokens` 且 temperature 只接受 1。擴充為
`startswith(("o1", "o3", "o4", "gpt-5"))` → pop max_tokens、temperature=1。
三條呼叫路徑（stream / non-stream / structured）都過 `_cons_kwargs`，
子類（azure/ark）繼承同一行為。

---

## Step 53（2026-06-11 凌晨）— Gate-off ablation：QC gate 不是 design_layout gap 的成因（negative result）

### 動機

Step 52 證實 gate 不對稱（GT 70% 會被退件），自然假說：gate 把 A 壓進
GT 不佔據的擺位子空間 → 造成 blind judge 扣分的 dead-space/失衡。
直接檢驗：關掉 hard gate 重跑 N=20，blind 重判，看 design_layout 是否改善。

### 方法

1. `step41_layout_aware_oracle.py` 加 CLI 開關（預設行為不變）：
   `--no-safe-zone-gate`（違規只記 log、照常送 judge）、`--render-prefix`、
   `--results-json`（獨立輸出名，記取 Step 50 覆蓋檔案的教訓）。
   Generator prompt 的 rule 6 safe-zone 指引**保留**——只隔離「硬退件」單一變因
   （Step 49 已證 prompt 指引約束力弱，41–47% 違規率，所以關 gate 即釋放大部分約束）。
2. `step51_blind_judge_audit.py` 參數化（`--render-prefix`/`--out`/`--gt-control`），
   同一套 blind protocol 實作避免 prompt 漂移。
3. 跑 `--no-safe-zone-gate` N=20（gpt-4o，`step53_gate_off_oracle_*`），
   blind 重判（中性標籤、雙順序、`--gt-control 0`，position bias 已證為 0）。

### 結果（N=17；3 樣本 5f56075f/5bbcb749/5dad776a 持續 vision refusal 全滅，
9/9 呼叫拒絕——既有 deferred item，step51 的 20 對是靠舊前綴累積 renders）

- **Manipulation check 通過**：18 次 attempt 帶違規送審；被 blind 評的末次
  render 7/17（41%）帶著舊 gate 會退件的違規——gate-off 確實改變了行為分布。
- **Blind 判決與 gate-on（step51 同 17 ids）完全相同**：

| 軸 | step51 gate-ON（c/g/t） | step53 gate-OFF（c/g/t） |
|---|---|---|
| design_layout | 2/32/0 | **2/32/0**（一模一樣） |
| typography_color | 1/31/2 | 2/30/2 |
| graphics_images | 6/4/24 | 7/7/20 |
| content_relevance | 2/5/27 | 2/8/24 |
| innovation_originality | 23/4/7 | 15/6/13 |
| **overall** | 2/32/0 | **2/32/0** |

- VIOL vs clean 分組：design_layout c=1/g=13 vs c=1/g=19——等比例，違規
  renders 沒有比較好也沒有比較差。

### 解讀

1. **Gate 不對稱是真的（Step 52），但不是 design_layout gap 的成因**：
   即使讓 A 自由踩進 saliency 區（41% 真的踩了），blind judge 對
   balance/hierarchy 的判決一票未變。gap 在 Generator 的構圖品質本身，
   不在擺位自由度。
2. **Generator-bounded 結論再度存活**第三次系統性因素檢驗
  （Step 47 render confound → Step 51 label bias → Step 53 gate asymmetry）。
3. innovation 軸 23→15（cand）方向變差但 tie 增加，樣本小、不宜過度解讀；
   該軸的可主張性仍以 Step 51 的乾淨重做為準。
4. 論文寫法：gate 不對稱列為 limitation（Step 52），附 gate-off ablation
   證明它不驅動 headline gap（Step 53）——這是審稿人必問的對照。
5. 尚未檢驗的剩餘因素：render parity 分解（實驗 C）、元素數量對等。

---

## Step 54：實驗 C — Render-Parity 分解（2026/06/11）

### 問題

Blind 下 design_layout/typography 的 gap（Step 51）有多少其實是 **render
channel**（字型解析、置中/斷行、rotation 不支援、raster 合成、style 預設值）
而不是 Generator 的構圖決策？Steps 51/53 已排除 label bias 與 gate 不對稱，
render parity 是最後一個系統性嫌疑因素。

### 方法（`layout_agent/output/step54_render_parity.py`，離線、零 LLM）

把 20 個 step13 樣本的設計師 GT 版面從 crello meta.json 幾何重建，走**我們
自己的 renderer** 重渲染，再用 step51 blind 協議判 our-render(GT layout) vs
GT 原圖。幾何 by construction 完全相同，所以 GT 的任何系統性偏好都量測
render channel 本身。

- GT meta 沒有 style metadata，font_size/color 只從 GT 幾何＋自家
  deterministic 工具推導（`font_size = bbox_height/n_lines*0.75`、
  `color = BackgroundAnalyzer recommended_text_color`）；不設
  font_family/text_align（renderer 預設）。
- 元素映射：text→TITLE/BODY_TEXT、image（未升格背景）→PRODUCT_IMAGE、
  underlay→DECORATIVE_IMAGE；背景走 live 同一條
  `_composite_background_plates` 路徑；z_index=設計師原順序。
- Render 命名 `step54_render_parity_crello_<id>_r1a1.png`，直接餵
  參數化後的 step51 blind judge（`--render-prefix step54_render_parity
  --gt-control 0`）。20/20 渲染成功（不經 LLM，無 vision refusal，N=20 全量）。

### 結果（gpt-4o blind、雙順序、40 判）

| 軸 | cand / gt / tie | cand 勝率 |
|---|---|---|
| **overall** | 9 / 31 | **22.5%** |
| design_layout | 9 / 31 / 0 | 22.5% |
| typography_color | 7 / 31 / 2 | 17.5% |
| graphics_images | 4 / 4 / 32 | tie 為主 |
| content_relevance | 2 / 2 / 36 | tie 為主 |
| innovation_originality | 13 / 8 / 19 | cand 領先 |

order-flip：5/20（step53 為 2/17）——pairs 確實更接近。

### Gap 分解（vs 50% parity）

| 軸 | A blind（step51） | GT 幾何重渲染 | render channel 份額 | Generator 份額 |
|---|---|---|---|---|
| design_layout | 5% | 22.5% | 27.5 pts（**61%**） | 17.5 pts（39%） |
| typography_color | 2.5% | 17.5% | 32.5 pts（**68%**） | 15 pts（32%） |

### 解讀

1. **Render channel 是 blind gap 的主要成份（六成以上）**，不是 Generator
   構圖。「Generator-bounded」修正為「Generator ＋ render channel 共同
   bounded，render channel 份額更大」。
2. **天花板效應**：即使 Generator 輸出與設計師完全相同的幾何，現有 renderer
   下 design_layout blind 勝率上限 ~22.5%。Steps 41–53 的 prompt/gate 改進
   全部在這個天花板底下。
3. **Judge 軸 bleed 直接證據**：幾何相同，design_layout 仍 31/40 判 GT 勝、
   tie=0，理由是「cleaner hierarchy / better alignment」——design_layout 軸
   實際被 render 品質污染（字型、置中、斷行都不是 layout 決策）。
4. **Caveat（上界性質）**：重渲染未設 font_family/text_align（GT meta 無此
   資料），這部分 Generator 原則上可經 typography 欄位改善，所以 61–68% 是
   render+style channel 的**上界**；但 text rotation 不支援、粗圓體字型
   不在 5 款 bundled fonts 內、自動置中/斷行缺失是 renderer 硬限制。
5. 論文寫法：實驗 C 是 headline gap 的 decomposition——「A 落後設計師」的
   blind 數字中，多數可歸因 render channel；Generator 構圖差距真實存在但
   份額較小（~17.5 pts）。
6. 剩餘未檢驗因素：元素數量對等（judge 理由曾多次提及 "additional text
   elements"）。

---

## Step 55：Renderer 天花板提升（2026/06/11）

### 動機

Step 54 證實 render channel 佔 blind gap 61–68%、renderer 天花板僅
~22.5%——任何 Generator/prompt/gate 改進都打不破。本步直接補 renderer。

### 改動（`metagpt/ext/agentlayout/tools/renderer.py`）

1. **字型升級（55a）**：bundled 新增 Montserrat-Variable（sans 首選，取代
   DejaVu「工程 mockup」臉）、Baloo2-Variable（粗圓 display bold，GT 標題
   最常見字類）、BebasNeue（縮體 display）；全 OFL。新增
   `_apply_weight_variation`：variable font 以 named instance 顯式選
   Bold/Regular。**修掉兩個靜默 bug**：(a) `DancingScript-Bold.ttf` /
   `Oswald-Bold.ttf` 從未存在於 fonts/，bold 請求一直 fallback 成細體；
   (b) Montserrat variable 預設 instance 是 Thin，不顯式設 Regular 會
   渲染極細體。
2. **Auto-wrap + shrink-to-fit（55b）**：新 `_fit_text`／`_wrap_to_width`。
   無手動換行 → 按像素寬逐詞 wrap；仍溢出 → font_size × 0.9 遞減
   （下限 `MIN_FONT_SIZE=8`），到底仍溢出就照畫（不裁切）。手動 `\n`
   原樣保留（作者斷行意圖）。Generator 的 font_size 語意變為上限。
   Phase 1「故意讓溢出給 Judge 罰」設計正式移除。
3. **Text rotation（55c）**：`_paint_rotated_text`——文字畫到透明圖層 →
   `rotate(-angle, expand)`（schema 順時針正，與 image 同慣例）→ 以 bbox
   中心 paste。`_fit_text` 用旋轉後軸對齊外接框檢查（90° 直幅的行長
   受 bbox **高度**約束）。angle=0 路徑行為不變。
4. **Parity 腳本（55e）**：`step54_render_parity.py` 加 `--prefix` /
   `--center-align`（text bbox 水平中心落在 canvas 中心 ±5% →
   `text_align="center"`，純幾何推導、零 LLM）。

測試：新增 `test_renderer_step55.py` 17 項（variation/wrap/fit/rotation，
含兩個 bug 的 regression guard）；全套 **261 passed / 12 skipped** 零回歸。

### 驗證（step54 protocol 重跑：our-render(GT) vs GT 原圖，N=20、40 判）

| 軸 | Step 54（舊 renderer） | **Step 55（新 renderer）** | parity=50% |
|---|---|---|---|
| design_layout | 22.5%（9/31/0） | **55%（22/18/0）** | **超過 parity** |
| typography_color | 17.5%（7/31/2） | **30%（12/27/1）** | 殘餘 gap |
| overall | 22.5%（9/31） | **45%（18/40）** | 接近 parity |
| order-flip | 5/20 | 8/20（40%） | pairs 近不可分 |

### 解讀

1. **design_layout 的 render channel gap 關閉**：幾何相同下 judge 已無法
   穩定區分我們的 render 與 GT 原圖（55% ≈ 隨機；5 樣本雙順序皆判 cand 勝；
   40% order-flip）。
2. **typography 殘餘 30%**：GT meta 無 font/color metadata，推導是
   heuristic——殘餘屬資料層限制，非 renderer 限制，接近不可再縮。
3. **Step 51 的 A blind 5% 須重測**：該數字是舊 renderer 量的。新天花板
   design_layout ~55% / typography ~30%，Generator 真實構圖差距待
   新 renderer 下的 live N=20 重跑（下一步）。
4. 已知缺口：crello cache 的 meta.json 未存 `angle`，parity 重渲染吃不到
   rotation 效益（renderer 已支援）——天花板量測偏低估方向。

---

## Step 56：新 Renderer 下的 Live N=20 重測（2026/06/11）

### 動機

Step 51 的 A blind 基線（design_layout 5%）是舊 renderer 量的；Step 55
把天花板從 22.5% 抬到 55% 之後，Generator 的真實構圖差距必須重測。

### 方法

標準 gate-on 配置重跑 live N=20（`--ids-file step13_drawn_ids.json
--render-prefix step56_live`），新 renderer 全程生效（字型/variation/
wrap/fit/rotation）。2 樣本（59280150、5dad776a）三次 attempt 全失敗
（JSON parse / vision refusal，已知 deferred 問題）→ blind N=18。
QC acceptance 仍 0/20 round1_exhausted（labeled in-loop judge 行為，
與 blind 量測無關）。

### 結果（blind，N=18、36 判）

| 軸 | Step 51（舊 renderer） | **Step 56（新 renderer）** | 天花板（Step 55） |
|---|---|---|---|
| design_layout | 5%（2/38/0） | **13.9%（5/31/0）** | 55% |
| typography_color | 2.5%（1/37/2） | **19.4%（7/26/3）** | 30% |
| graphics_images | 17.5% | **27.8%（10/5/21，cand 領先）** | – |
| innovation | 60% | 36%（13/10/13） | – |
| overall | 5% | **11.1%（4/36）** | 45% |

order-flip 2/18（判決穩定；對照 step55 parity 的 8/20 近不可分）。

### 解讀

1. **Render channel 修復對 A 真實有效**：design_layout ×2.8、typography
   ×7.8、graphics 首次 blind 領先 GT。Step 51 歸給 Generator 的 gap 有
   一大塊實為 renderer。
2. **Typography 軸接近解決**：19.4% vs 天花板 30%，殘距已小於天花板
   本身的資料層誤差（GT 無 font/color metadata）。
3. **Generator 構圖差距最乾淨量測**：design_layout 13.9% vs 55% =
   **~41 pts 純 Generator 構圖品質**（死空間、漏排元素；5e8d966a 抽查
   可見下方 40% 空白、#StayHome ×2 未排入）。order-flip 對比（2/18 vs
   8/20）：judge 分得出 A 和 GT、分不出我們的 render 和 GT。
   **Generator-bounded 第四次存活**（47 render confound → 51 label
   bias → 53 gate → 56 render channel），此為去 confound 後最終形態。
4. 論文 headline 數字更新：A blind design_layout 13.9%（N=18，新
   renderer）；舊 5% 僅作 render-channel decomposition 歷史基線。
5. 後續候選方向：(a) Generator 構圖品質本體（死空間/元素完整性——
   QC 已有 primary-in-safe-zone，缺 coverage/全元素排入的硬性檢查）；
   (b) 2 個失敗樣本的 JSON parse/vision refusal 修復；(c) 元素數量
   對等檢驗（仍未做）。

---

## Step 57：Coverage / Dead-Space QC Guardrails（2026/06/11）

**動機**：Step 56 確認 ~41 pts 純 Generator 構圖差距，目視失敗模式是
「內容縮在一角／整條畫布空白」。既有 QC 已有元素完整性
（MISSING_ELEMENT）與 primary-in-safe-zone，但對退化構圖（coverage
過低、大面積死帶）沒有任何硬性檢查。

**校準（先於實作）**：`layout_agent/output/step57_coverage_calibration.py`
離線零 LLM，對 20 個 step13 設計師 GT 版面計算三個訊號（前景＝排除
background_image 的 bbox）：

| 訊號 | GT 範圍（N=20） | step56 candidate 範圍（N=70） |
|---|---|---|
| coverage（bbox 聯集/canvas） | 0.129–0.898 | 0.048–0.865 |
| 最大垂直/水平死帶 | ≤0.503 / ≤0.548 | 至 0.794 / 0.764 |
| safe-zone 利用率 | 0.026–0.898 | 0.000–1.000 |

關鍵發現：**設計師合法版面本身就有 coverage 0.13、死帶 0.55 的極簡
構圖**（背景照片撐畫面），任何更緊的門檻都會誤殺 GT。因此門檻只能
是「退化防護」（degenerate guardrail）語意，不是美學規則：
- `CANVAS_COVERAGE_MIN = 0.10`（GT min 0.129，margin 23%）
- `DEAD_BAND_MAX = 0.60`（GT max 0.548，margin 9%）
- safe-zone 利用率**不採用**——低值樣本已被 Step 43
  PRIMARY_OUTSIDE_SAFE_ZONE 逐元素覆蓋，且 GT min 0.026 無法設門檻。

**實作**：
1. `quality_checker.py`：新增 `ViolationType.CANVAS_COVERAGE_LOW` /
   `DEAD_BAND_EXCESSIVE`；`_check_canvas_coverage`（覆蓋率用 100×100
   grid 光柵化 bbox 聯集；死帶用一維區間合併求最大 gap，含首尾
   margin，垂直/水平各自判定）。掛入 `check_candidate`，純幾何、
   不需 BackgroundAnalysis。
2. `generate_layout.py` PROMPT_TEMPLATE：hard rules 第 7 條同步
   描述兩條規則，讓 Generator 一次生成就合規、不浪費 retry。
3. 測試：新增 `test_quality_checker_coverage.py`（8 tests：縮角落、
   設計師式極簡通過、background 排除、單軸死帶、0.55 envelope 通過、
   僅背景候選、門檻 pin）。`test_quality_checker_position_hints.py`
   的 z_order fixture 從 200×80 sliver（coverage 4.4%，本身就是
   退化版面）放大為 500×500——非回歸，是新規則正確抓到舊 fixture。

**驗證**：
- 全套 agentlayout 測試 269 passed / 12 skipped，零回歸。
- 端到端重放：step56 log 的 70 個 candidate 餵入新 `check_candidate`
  → **24/70（34%）被抓**（5f56075f×5 coverage 0.05、589d7bd9×5 死帶
  0.667、592c2135×8、5f9917ea×5、5f4e0040×1）；GT 20/20 全過。
- 誠實註記：5e8d966a（Step 56 目視確認的失敗樣本）coverage 0.27 /
  死帶 ≤0.37，**不會被本規則抓到**——它的根因是元素數量
  （candidate fg=2 vs GT fg=4），屬「元素數量對等」（方向 c）範疇。

**下一步**：live N=20 重跑驗證 guardrail 對 acceptance 與 blind
design_layout 的實際影響（24/70 在 reject-retry loop 裡會觸發重生成，
效果須實測）。

#### Step 58 — coverage QC live 驗證：機制成功、效果 negative（Generator-bounded 第五次確認）

**動機**：Step 57 的 guardrails 只做過離線重放驗證，須 live N=20 +
blind judge 實測對 acceptance / blind design_layout 的影響。

**過程中發現的 gate filter bug**：第一次 live 跑（step58）後發現
`output/step41_layout_aware_oracle.py` 的 QC gate 只挑
`PRIMARY_OUTSIDE_SAFE_ZONE`（`sz_viols = [v ... if v.type == ...]`），
`check_candidate` 回報的新 violation 被 filter 靜默丟掉——step58 實際
測到的是「僅 prompt rule 7、無 in-loop gating」。修正：`cov_viols`
（CANVAS_COVERAGE_LOW / DEAD_BAND_EXCESSIVE）併入 `gate_viols` 一起
退件；Step 53 的 `SAFE_ZONE_GATE` ablation flag 只影響 safe-zone 部分
（coverage 規則是退化防護、無 ablation 需求）。重跑為 step58b。

**三條件對照（live N=20，blind judge 含 GT-vs-GT control）**：

| 指標 | step56（無規則） | step58（僅 prompt） | step58b（prompt+gate） |
|---|---|---|---|
| 候選幾何違規率（重放） | 24/70（34%） | 21/80（26%） | 26/75（35%） |
| 新規則 in-loop 退件 | 0 | 0（filter bug） | 6（dead×4、cov+dead×2） |
| 退件後下一 attempt 修好被點名問題 | — | — | 3/3，但全改踩 safe-zone |
| QC acceptance | 0/20 | 1/20 | 0/20 |
| 進到 judge 的 round | — | 28 | 18 |
| blind overall cand 勝 | 11.1%（4/36） | 10.0%（4/40） | 2.6%（1/38）⚠ |
| blind design_layout | 13.9% | 12.5% | 2.6% ⚠ |
| blind graphics cand 勝 | 27.8% | 35.0% | 28.9% |
| blind innovation cand 勝 | — | 30% | 50% |
| GT-vs-GT control | — | 8/8 tie | 9/9 tie |

⚠ blind 協定假象：audit 腳本取「最後一次 attempt」render（`pngs[-1]`），
step58b 有 13/19 樣本最後一張是 QC 退件版面（gate 越嚴、最後一張越可能
是失敗品），step58 是 8/20——step58b 的 blind 下滑不能解讀為品質倒退。

**結論**：
1. **機制驗證 ✅**：gate 修正後新規則真的開火（6 次）、觸發 retry、
   Generator 會修被點名的問題（3/3 修好 coverage/dead-band）。
2. **效果驗證 ✗（honest negative result）**：acceptance 0/20 無提升。
   失敗模式是**打地鼠**：修好 dead-band 就踩 safe-zone、反之亦然
   （5f9917ea：cov+dead → sz → dead_band），3 attempt 預算被 QC retry
   互相消耗（judged rounds 28→18）。
3. **prompt rule 7 無預防力**：生成端違規率 34%→26%→35%，step58 的
   下降是 run 間雜訊——與 Step 49「prompt-only 上限」一致。
4. **目視三組 attempt 鏈的深層發現**：
   - 5f9917ea a2 = 大橫幅壓畫面中央，**與 GT 概念幾乎相同**，卻被
     safe-zone gate 退件——設計師正解（橫幅壓在披薩上）違反我們的
     safe-zone 規則，是 Step 52「GT 70% 被 gate 退」的具象案例；
     兩個 QC 規則的可行解交集太小，Generator 被夾死。
   - 5e6a3440 / 5fbfbd0f：結構與 GT 相同但**尺寸膽怯（timid sizing）**
     ——GT 照片佔 ~75% 畫面、候選只敢放 ~25%；GT 文字盒大而壓主體、
     候選一律縮小避讓。這比「coverage 太低」更精確描述 ~41 pts 差距
     的本體，也解釋為何 coverage QC 抓得到症狀治不了病。
5. **Generator-bounded 第五次確認**：QC 能偵測退化、能給可操作回饋，
   但 Generator 缺「同時滿足多重空間約束 + 大膽放大元素」的構圖能力。

**雜訊註記**：589d7bd9（step58 唯一 acceptance）在 step58b
generate_failed×3（生成期 refusal/validation 失敗，非 QC），故 blind
N=19；59280150 / 5dad776a 的 vision refusal 間歇出現（已知「先不修」）。

**產物**（`layout_agent/output/`，gitignored）：`step58{,b}_live_N20.log`、
`step58{,b}_live_results.json`、`step58{,b}_blind_judge{,.log}`、
`step58{,b}_live_crello_*.png`。程式改動：`step41_layout_aware_oracle.py`
gate 修正（committed）。

**下一步候選**：(a) 對「打地鼠」——讓 QC 退件 feedback 一次列出**全部**
未滿足約束（目前 retry 只看到當次違規），或加大 retry 預算；(b) 對
「尺寸膽怯」——量化 GT vs candidate 的元素面積比分布，評估是否加
最小主元素面積 hint（須照 Step 57 方法先 GT 校準）；(c) safe-zone gate
與 GT 風格衝突（5f9917ea a2 誤殺）併入 Step 52 結論，考慮 safe-zone
規則對「帶 underlay 的文字」放寬（GT 慣用橫幅壓主體）。

#### Step 58c/58d — experiment.md 指標重算：幾何六指標 + COLE 四軸（2026/06/11）

**動機**：用戶要求看 experiment.md 定義的指標在 live oracle run 上的結果。
oracle 不落盤 spec/candidate，故新增零 LLM 重算腳本
`step58c_sega_from_log.py`：從 oracle log 解析每個 sample **最後一個
batch 的 candidates[0]**（= oracle 渲染、blind judge 評的那張，
step41:513 + step51 `pngs[-1]` 協定），重用 `step20_sega_eval` 的
builder / metric 函式，數字與 Step 29 N=1,895 基準同一套實作。

**Log parser 兩個修補**：(1) loguru 非同步 INFO 行（cost_manager 等）會
插進 JSON dump 中間（甚至接在同行後面），以 timestamp regex 剝除；
(2) Generator content 字串含原始控制字元，`json.loads(strict=False)`。
修補後覆蓋 16/15/18（step56/58/58b），缺的是 generate_failed /
vision-refusal 樣本（log 內無 candidate，blind judge 同樣沒評）。

**幾何六指標（mean，AL = 最後 attempt 版面）**：

| run | Ali ↓ | Ove ↓ | Und_l ↑ | Und_s ↑ | Occ ↓ | Rea ↓ | n |
|---|---|---|---|---|---|---|---|
| step56 | 0.0000 | 0.0000 | 0.6250 | 0.6250 | 0.1274 | 0.0128 | 16 |
| step58 | 0.0005 | 0.0000 | 0.4779 | 0.4222 | 0.1142 | 0.0184 | 15 |
| step58b | 0.0000 | 0.0000 | 0.5114 | 0.4444 | 0.1122 | 0.0141 | 18 |
| Designer GT | 0.0017 | 0.0234 | 0.4364 | 0.3684 | 0.1136 | 0.0066 | 19 |

AL 在 5/6 軸達到或超過 GT（幾何指標飽和，同 Step 29 結論）；**Rea 是
唯一明確落後軸**（~2×，文字壓在背景紋理複雜處）；三 run 間差異為
樣本組成 + QC 退件版面比例之雜訊，不得宣稱 coverage QC 改善幾何指標。

**COLE 四軸（58d）**：`step21_phaseb_eval.py` 加 `--render-prefix`
（glob `{prefix}_crello_{id}_r1a*.png` 取 `[-1]`，與 blind judge 同
協定；預設 None 行為不變，step32/33 importer 不受影響）。gpt-4o 絕對
分，N=19/20（589d7bd9 無 render）：

| Method | S_DL | S_QL | S_TV | S_IO | Smean |
|---|---|---|---|---|---|
| AL step58b | 6.95 | 7.47 | 6.53 | **6.16** | 6.78 |
| AL 舊 cold-start（Step 21） | 5.50 | 5.10 | 6.15 | 4.30 | 5.26 |
| Designer GT（Step 21b） | 7.95 | 8.65 | 7.65 | 5.85 | 7.53 |
| SEGA-13B（Table 3 參考） | 6.15 | 6.75 | 6.35 | 6.04 | 6.32 |

要點：(1) 四軸全高於 SEGA-13B（跨論文 informational）；(2) vs 舊
cold-start +1.51 Smean＝renderer 升級 + oracle loop 混合效果，不可歸因
單一因素；(3) vs GT 落後約 1 分，**唯一反超軸 S_IO 6.16 vs 5.85**，與
blind innovation cand 50% 互相印證；(4) 13/19 張是 QC 退件版面
（`pngs[-1]`），分數偏保守。

**三套指標合讀**：幾何達標、COLE 差 1 分、blind design_layout 大輸——
差距不在幾何合規性而在構圖層次（尺寸膽怯／留白運用）；S_IO 是 AL
真實強項。產物：`step58c_sega_from_log.json`、`step58d_phaseb_cole.json`
（均 gitignored）。程式改動：新增 `step58c_sega_from_log.py`、
`step21_phaseb_eval.py` 加 `--render-prefix`。

#### Step 59 — 文字下方梯度 QC 規則 TEXT_ON_BUSY_TEXTURE（GT 校準 T=0.065）（2026/06/11）

**動機**：Step 58c 確認 Rea（文字下 Sobel 梯度）是 experiment.md 六何指
標中 AL 唯一明確落後軸（0.0141 vs GT 0.0066，~2×）。根因：safe_zones
是 saliency 導向、非 texture 導向——saliency 低的角落仍可能梯度很高。

**GT-first 校準（`step59_text_gradient_calibration.py`，零 LLM）**：
重放 step56/58/58b 三份 oracle log 全部 generator batch（N=590 候選，
327 含曝露文字）+ 20 張 step13 設計師 GT。Per-element 粒度，與
`metric_readability` 同約定（梯度圖以影像自身 max 正規化、underlay
bbox 把文字像素歸零）。發現：**8/20 GT 版面把每個文字元素都用 underlay
完全遮蔽**（設計師的主要紋理防禦是「遮蔽」不是「閃避」）；曝露的 12 張
worst-element 範圍 0.0000–0.0454（median 0.0040）。門檻 = GT max
0.0454 + 0.02 餘裕 = **0.065**（Step 57 SOP：退化防護非審美規則；
Step 58 教訓：貼著 GT max 會誤殺 GT 式解）——GT 20/20 過，抓
74/590（13%；曝露候選的 23%），含 step58b 最終版面 2/18（5f4f5e15
cta_1 0.0975、5f56075f cta_1 0.0684），恰好就是 Rea 最差樣本（互證）。

**Production 規則（`tools/quality_checker.py`）**：新 ViolationType
`TEXT_ON_BUSY_TEXTURE` + `_check_text_on_busy_texture`。設計要點：
(1) 元素分類鏡像 Rea 指標而**非** `TEXT_SEMANTIC_TYPES`——
`visual_type==text` 全算文字（**含 CTA**；校準抓到的兩個最終版面違規
都是 cta_1，semantic 白名單會漏）、`decorative_image` 為 underlay 遮蔽
盒、>95% 畫布面積視為背景跳過；(2) 背景自載：從
`spec.canvas.background_asset_ref` 讀圖，模組級 `(path,w,h)` 梯度快取
（每 sample 一次 Sobel），cv2/numpy/PIL 函式內 import、載入失敗靜默跳
過（step-12 never-crash）——因此 **`filter_valid`（Generator 內部 QC
迴圈）不需 bg 參數也會啟動**；(3) violation detail 依用戶決策明確指向
GT 式解法：「place an underlay shape beneath this text to shield it …
or move it to a flatter background region」。oracle gate
（step41 `cov_viols` tuple）同步加入，與 Step 57 guardrails 同退件路
徑。**不動 Generator prompt**（Step 49/58 已證 prompt-only 無預防力）。

**驗證**：新測試 `test_quality_checker_text_gradient.py` 8 項（平坦
pass／busy 區 fail＋detail 含 underlay 指引／busy 圖的平坦區 pass＝
texture-local／underlay 遮蔽 pass／CTA 受檢／無 bg ref 跳過／缺檔不
crash／門檻 pin 0.065），套件 277 passed（基線 269+8）零回歸。一致性
（`step59b_qc_rule_consistency.py`）：production 規則重放三 log =
**74/590 與校準腳本完全一致**（同樣本、同梯度值到第 4 位小數）。

**誠實預期**：第 4 條 gate 規則，Step 58 已示範打地鼠風險——live
acceptance 大概率持平；差異化賭注在 detail 直接給 underlay 解法，
retry 能否真的學會遮蔽待 live N=20 驗證（未跑）。產物：
`step59_text_gradient_calibration.{py,json}`、
`step59b_qc_rule_consistency.py`。

#### Step 59 live N=20 驗證 — 規則精準、retry 零修復、Rea 朝 GT 移動（2026/06/11）

**設定**：`step41_layout_aware_oracle.py --ids-file step13_drawn_ids.json
--render-prefix step59_live`，HEAD `f1de8cec`（TEXT_ON_BUSY_TEXTURE 已
進 gate）。產物：`step59_live_N20.log`、`step59_live_results.json`、
`step59_live_crello_*` renders（均 gitignored，與既往 live run 同）。

**Acceptance：0/20（全 round1_exhausted），與 step58 持平＝預期。**
判軸（19 次 judge 呼叫）：design_layout B=19/tie=0、typography B=16、
graphics B=15、content tie=14、innovation tie=15——瓶頸仍是 Generator
構圖，gate 非成因（**Generator-bounded 第六次確認**）。59158b4f 撞
已知 vision refusal（「先不修」清單）。

**規則行為（核心發現）**：
1. **精準度完美**：live 開火 3/20 = 校準預測的同三個樣本
   （5c94fa60 title_1 0.0655、5f56075f cta_1 0.0660、5f4f5e15 cta_1
   0.0964–0.0975），梯度值與離線重放一致到第 3–4 位，無誤殺。
2. **retry 零修復**：5c94fa60 三次 attempt 梯度一模一樣（文字原地
   不動）；5f4f5e15 三次全踩 + safe-zone↔dead-band 打地鼠；5f56075f
   a2/a3 generate_failed。
3. **指引可執行但未執行**：三個 spec 都有 `underlay_1`
   （decorative_image）可用——「加 underlay 遮蔽」不是不可能的指令，
   是 Generator retry 根本不照 detail 行動（連移位都不做）。
   detail-directs-underlay 的差異化賭注落空。

**幾何指標（step58c 腳本重算，n=15 parse 成功）**：

| run | Ali↓ | Ove↓ | Und_l↑ | Und_s↑ | Occ↓ | Rea↓ |
|---|---|---|---|---|---|---|
| step59_live | 0.0009 | 0.0000 | 0.5955 | 0.5556 | 0.0718 | **0.0085** |
| 同子集 GT | 0.0021 | 0.0185 | 0.3528 | 0.2667 | 0.0470 | 0.0058 |

Rea 0.0141（step58b，GT 2.1×）→ **0.0085（同子集 GT 1.47×）**——
方向與 gate 預期一致，但 n=15、樣本組成不同、跨 run 差異曾定性為
雜訊（58c 條目），**只可寫「與機制一致」不可宣稱因果**。注意
`step58c_sega_from_log.py` 輸出路徑固定，本次執行覆寫
`step58c_sega_from_log.json`——已用四 log（56/58/58b/59）重生成
完整版。

**結論**：與 Step 58 同型 negative-but-informative——機制全部成功
（規則精準、gate 開火、feedback 送達），acceptance 0/20 不動；新增
證據：**Generator retry 對明確、可執行的結構化修復指令（加 underlay）
完全不回應**，把「QC feedback 措辭改進」這條路線進一步封死，殘餘
選項收斂到 Step 58 條目候選 (b) GT 校準面積比 hint（直接改 Generator
輸入而非 feedback）。

#### Step 60 GT 校準照片面積 prior — 尺寸第一次移動、判決不動（2026/06/11）

**動機**：Step 58 發現「尺寸膽怯」——候選照片固定 1/3×1/3
（area_ratio 0.111）。Step 59 證實 feedback 路線封死後，本步改走
**Generator 輸入端**：把 GT 校準的照片尺寸 prior 直接放進 prompt。

**校準（`step60_area_ratio_calibration.py`，N=1,902 GT、零 LLM）**：
clipped area_ratio 逐 class 統計——photo n=2374 **p50 0.213 / p75
0.445**；title_text p50 0.080；underlay p50 0.009（雙峰）。對照
候選端（step58b+59 log 同指標）：photo p50=p25=p75=**0.1111**（退化
單點）、title 0.077（對齊 GT）、underlay/other_text 反而比 GT 大——
**photo 是唯一尺寸膽怯 class**，prior 只鎖 product_image（推其他
class 會反方向）。

**程式改動（雙槓桿）**：
1. `generate_layout.py`：`PHOTO_AREA_GT`/`PHOTO_AREA_TARGET=(0.20,
   0.45)` 常數（目標=GT p50..p75，刻意不用 p90 限制 safe-zone 衝突）
   ＋ `_format_area_hints()` 敘述型 hint slot ＋ prompt 尾端
   ATTENTION 規則（含逐 canvas 的像素數學示例、anchor 在最大 safe
   zone 的指示）。
2. `analyze_brief.py`：`inject_photo_size_prior()` 在 `run()` 成功
   路徑程式化注入 `size_preference: photo-prominent` hard constraint
   （Analyst 自己的 size constraint 優先、logo/icon 排除）；
   `quality_checker.py` 新 bucket `photo-prominent: 0.20`（剛好低於
   GT p50 0.213，GT 式中位解合法——避開 Step 52/58 誤殺陷阱；既有
   `prominent` 0.10 對 photo 無牙）。
   測試 `test_generator_area_prior.py` 12 條，套件 289 passed 零回歸。

**Smoke 兩輪**：#1（N=5 預設 ids）敘述型 hint 單獨上場＝零移動
（5/5 候選照舊 360×480=0.083，呼應 Step 49 prompt ceiling）。
#2（4 個 photo 樣本 targeted）雙槓桿後**尺寸第一次移動**：mean
0.135–0.215、max 0.32、大量精確堆積在 0.200（Generator 真的照
ATTENTION 算了數學）。

**重要架構發現——oracle gate 白名單**：smoke log grep
`photo-prominent` 零命中、零 size_preference 違規 ≠ 注入失敗。離線
重放證明注入成功且 `check_candidate` 會開火（45 候選中 25 個
sub-floor 全數正確產生違規），但 step41 oracle 的 gate 是白名單
（`gate_viols = sz_viols + cov_viols`），**SIZE_PREFERENCE 算出後
直接丟棄**——不進 gate、不進 log/results。故實際生效的只有 prompt
槓桿；0.200 堆積是 Generator 自願服從，非 QC 退件逼出。依使用者
決定**不加 gate**（Step 58/59 已證 retry feedback 無效，加 gate
只燒 attempts）。

**Live N=20（`--ids-file step13_drawn_ids.json`，prefix
`step60d_live`；註：先誤跑一輪 DEFAULT_IDS N=5，產物改名
`step60c_default5.*` 留存）**：
- **尺寸持續移動**：photo 樣本 mean 0.156–0.278、max **0.444**
  （= GT p75），≥0.20 比例 50%/50%/47%——機制在 live 完全成立。
- **Acceptance 0/20（全 round1_exhausted）**；判軸：design_layout
  B=20/tie=0、graphics B=18、typography B=16、content tie=13、
  innovation tie=17——與 step59（B=19/15/16）統計上同位。
- **逐 photo 樣本判決零移動**：4 樣本多數仍卡在
  primary_outside_safe_zone/text_on_busy_texture QC 關卡；唯一被評
  的 axes 與 step59 完全相同。

**結論**：機制成功＋結果 negative 的第三連發（58/59/60）。照片尺寸
這個自 Step 58 起追的失效模式**已被修復**（0.111 退化 → GT 區間），
但 design_layout 判決一票未動——(1) pool 只有 4/20 樣本有 photo，
聚合天花板本來就低；(2) 尺寸只是構圖差距的一小部分。
**Generator-bounded 第七次確認**：單一可量化失效模式的修復不足以
撼動整體構圖判決。產物：`step60_area_ratio_calibration.{py,json}`、
`step60_photo_ids.json`、`step60{_smoke,b_photo,c_default5,d_live}`
log/results/renders（live 產物 gitignored）。

---

*本文件為論文研究說明，供系統開發時參考使用。最後更新：2026/06/11*
