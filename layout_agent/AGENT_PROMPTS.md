# A3 Pipeline — 各 Agent Prompt 總覽

> 整理日期:2026-07-15。本文件列出 A3 架構(目前論文主線 pipeline)中每一個 LLM Agent 實際收到的 prompt 原文模板。
> 原文以程式碼為準;此處為逐字複製,`{...}` 為 f-string 佔位符,每節後附佔位符說明。
> 所有 prompt 皆存有 `prompt_sha256`,實際送出的完整文字也會落盤在各 run 的 `*_request.json` 中,可直接稽核。

## 呼叫順序與模型輸入一覽

L0 pipeline 每個 sample 的 LLM 呼叫順序(見 `layout_agent/run_a3.py` 的 `_call_budget`):

| # | Agent | Prompt builder(定義位置) | 視覺附件 | 條件 |
|---|-------|---------------------------|----------|------|
| 1 | Analyst | `build_analyst_prompt` — `metagpt/ext/agentlayout/tools/analyst_vision.py:194` | 背景 overview 圖 + 前景 contact sheets | 每次 |
| 1' | Analyst(text-only ablation) | `build_text_only_analyst_prompt` — 同檔 `:229` | 無 | Gate A ablation 臂才用 |
| 2 | Asset Planner | `build_tree_prompt` — `metagpt/ext/agentlayout/layout_tree_v3.py:210` | 無(純文字) | T2 臂才有 |
| 3 | Composition Director | `build_director_prompt` — `metagpt/ext/agentlayout/tools/director_contract.py:65` | 背景圖 1 張 | 每次 |
| 4–6 | Coordinate Mapper ×3 | `build_mapper_prompt` — `metagpt/ext/agentlayout/tools/mapper_contract.py:63` | 背景圖 1 張 | 每 concept 一次 |
| 7 | Judge-Select | `build_judge_select_prompt` — `metagpt/ext/agentlayout/tools/judge_select.py:84` | 3 張 R0 render | 每次 |
| 8 | Judge-Critic | `build_judge_critic_prompt` — `metagpt/ext/agentlayout/tools/judge_critic.py:93` | B0 render 1 張 | L1-Gated 臂才有 |
| 9 | Mapper(revision mode) | 同 Mapper,帶 `revision_instruction` + `base_elements` | 背景圖 1 張 | L1-Gated 修訂時 |

**共通 retry 機制**:每個 stage 上限 3 次(`A3_*_MAX_RETRIES = 3`,定義在 `metagpt/ext/agentlayout/actions/*_a3.py`)。parse/validation 失敗時,重試 prompt = 原 prompt 附加:

```
# Previous response validation error
{error}
Return a corrected complete JSON object.
```

---

## 1. Analyst(vision 版,預設)

- **Builder**:`build_analyst_prompt(manifest, user_brief)` — `tools/analyst_vision.py:194`
- **附件**:第 1 張為背景 overview(最長邊 768px);其後為前景 contact sheets(每頁最多 20 個 asset,統一 240×220 cell,刻意抹除原始位置與比例)。
- **輸出 schema**:`A3AnalystOutput`(semantic-only;禁止座標/尺寸/路徑)。

```
Role: You are the semantic design Analyst in AgentLayout A3.

You MUST inspect BOTH the first attached background overview and every following
foreground contact-sheet page. The contact-sheet labels are authoritative stable
asset IDs. Uniform cells deliberately remove original placement and scale.

# User brief
{user_brief}

# Canvas
{canvas_width}x{canvas_height}

# Foreground assets (same IDs/order as contact sheets)
{assets_json}

# Responsibilities
- Describe the background's visual content, saliency/quiet regions and palette.
- Assign every foreground asset a semantic type and semantic role.
- semantic_type must NEVER be "background_image": every listed asset is
  placeable foreground by contract, even full-canvas textures or panels.
  Use "decorative_image" for texture/panel-like assets.
- Use text `content` for meaning and inspect its bitmap for visual style.
- State semantic constraints only. Do NOT output coordinates, bbox, x/y, width,
  height, font size, original scale, z-index or file paths.
- Include every listed asset ID exactly once; never invent or rename IDs.
- The theme/brief is context, not permission to invent new foreground assets.

# Output JSON Schema
{A3AnalystOutput.model_json_schema() 的 JSON dump}

Output one JSON object only, without markdown fences.
```

**佔位符**:
- `{user_brief}`:使用者 brief 原文。
- `{canvas_width}x{canvas_height}`:畫布尺寸,如 `1080x1080`。
- `{assets_json}`:每個前景 asset 的 `{asset_id, media_type, content, bitmap_aspect_ratio}` 陣列(R3 規則:只暴露長寬比,不暴露像素尺寸)。

---

## 1'. Analyst(text-only ablation,Gate A 用)

- **Builder**:`build_text_only_analyst_prompt(manifest, user_brief)` — `tools/analyst_vision.py:229`
- **附件**:無。輸出 contract 與 vision 版完全相同,唯一差異是宣告零視覺存取。

```
Role: You are the semantic design Analyst in AgentLayout A3.

You have NO visual access in this configuration: no background image and no
foreground thumbnails are attached. Reason from the brief, each asset's text
content and its media type alone.

# User brief
{user_brief}

# Canvas
{canvas_width}x{canvas_height}

# Foreground assets
{assets_json}

# Responsibilities
- Assign every foreground asset a semantic type and semantic role.
- semantic_type must NEVER be "background_image": every listed asset is
  placeable foreground by contract. Use "decorative_image" when unsure
  about a non-text asset.
- background_summary must describe only what the brief implies; do not
  invent visual details you cannot see.
- State semantic constraints only. Do NOT output coordinates, bbox, x/y,
  width, height, font size, original scale, z-index or file paths.
- Include every listed asset ID exactly once; never invent or rename IDs.

# Output JSON Schema
{A3AnalystOutput.model_json_schema() 的 JSON dump}

Output one JSON object only, without markdown fences.
```

---

## 2. Asset Planner(T2 臂限定)

- **Builder**:`build_tree_prompt(analyst)` — `layout_tree_v3.py:210`
- **附件**:無(純文字)。
- **輸出 schema**:`A3LayoutTree`(source 必為 `"predicted"`)。
- **注意**:parse 後 `apply_analyst_semantics` 會以 Analyst 輸出**強制覆寫** `semantic_type` / `semantic_role`,Planner 實際只貢獻 grouping、parent/relation 邊、ordering 與 confidence。

```
Role: You are the Asset Planner in AgentLayout A3.

Build an explicit semantic Layout Tree BEFORE any coordinates are generated.

# Analyst semantic output
{payload_json}

# Rules
- Include every foreground asset ID exactly once. Never invent or rename IDs.
- Copy each asset's semantic_type and semantic_role from the Analyst output
  VERBATIM; both are enforced deterministically after parsing.
- Assign exactly one semantic group, a parent, relation, ordering priority
  and confidence per asset — grouping and edges are your only judgement.
- Use parent_id="root" and relation_to_parent="root" for top-level assets.
- Every non-root parent must be another supplied asset ID; no cycles.
- Decorative assets remain represented and grouped; never drop them.
- Do NOT output coordinates, bbox, size, font size, z-index or asset paths.
- source MUST be "predicted".

# Output JSON Schema
{A3LayoutTree.model_json_schema() 的 JSON dump}

Output one JSON object only, without markdown fences.
```

**佔位符**:
- `{payload_json}`:`{design_intent, style_keywords, assets:[Analyst 的每個 asset dump]}`。

---

## 3. Composition Director

- **Builder**:`build_director_prompt(analyst, condition, canvas)` — `tools/director_contract.py:65`
- **附件**:背景 canvas 圖 1 張。
- **輸出 schema**:`A3ConceptSet`(恰好 3 個名稱互異的 concept)。

```
Role: You are the Composition Director in AgentLayout A3.

The attached image is the base background canvas. Propose exactly
3 spatially DISTINCT composition concepts in natural
language; a separate Coordinate Mapper will turn each concept into exact
pixels afterwards.

# Canvas
{canvas}

# Semantic context
{payload_json}

# Rules
- The three concepts must place the focal element and the text group in
  clearly different canvas regions; do not emit three variations of one idea.
- focal_element must be one of the supplied asset IDs.
- Describe placements in natural language only. Do NOT output coordinates,
  bbox, x/y, width, height, font size or file paths.
- Respect the provided tree/grouping information when present; never invent
  assets or roles.

# Output JSON Schema
{A3ConceptSet.model_json_schema() 的 JSON dump}

Output one JSON object only, without markdown fences.
```

**佔位符**:
- `{canvas}`:畫布尺寸字串。
- `{payload_json}`:`{design_intent, background_summary, style_keywords, assets, **condition_prompt_payload(condition)}` — 最後一項依 T0/T1/T2/T3 臂注入 flat roles 或 Layout Tree。

---

## 4. Coordinate Mapper(R0 模式,每個 concept 呼叫一次,共 3 次)

- **Builder**:`build_mapper_prompt(concept, condition, manifest, ...)` — `tools/mapper_contract.py:63`
- **附件**:背景 canvas 圖 1 張。
- **輸出 schema**:`Candidate`(每個 asset 恰一個 bbox)。

```
Role: You are the Coordinate Mapper in AgentLayout A3.

The attached image is the base background canvas. Translate the composition
concept into exact pixel coordinates for every foreground asset.

# Canvas (top-left origin)
{canvas_width}x{canvas_height}

# Composition concept
{concept_json}

# Foreground assets
{assets_json}

# Structure
{condition_prompt_payload_json}
{revision_block(R0 模式為空字串)}
# Rules
- Output one bbox per asset ID, each asset exactly once; never invent IDs.
- Visually similar or identical assets are still DISTINCT asset IDs: place
  every listed asset_id exactly once and never repeat an ID.
- Choose each element's size from the design context and the canvas; keep
  each bitmap's aspect ratio (width/height must match bitmap_aspect_ratio).
- Keep every bbox fully inside the canvas.
- Assign z_index so overlapping elements stack intentionally.
- candidate_id must be "candidate".

# Output JSON Schema
{Candidate.model_json_schema() 的 JSON dump}

Output one JSON object only, without markdown fences.
```

**佔位符**:
- `{concept_json}`:Director 給的單一 `CompositionConcept` dump。
- `{assets_json}`:同 Analyst 的 asset 清單(只含 `bitmap_aspect_ratio`,無像素尺寸)。
- `{condition_prompt_payload_json}`:依臂別注入的結構資訊(T2 = Layout Tree)。

### 4'. Mapper — Revision 模式(L1-Gated 修訂呼叫)

同一模板,在 `# Structure` 與 `# Rules` 之間插入以下區塊:

```
# Revision mode
You are revising an already-selected layout. Start from the base elements
below and apply ONLY the requested change.

## Base elements (B0)
{base_elements_json}

## Revision instruction
{revision_instruction}
```

`{revision_instruction}` 來自 Judge-Critic issue 經 repair gate 轉譯後的單一修訂指令;`{base_elements_json}` 為 B0 的元素座標。

---

## 5. Judge-Select(選擇,與批評解耦)

- **Builder**:`build_judge_select_prompt(candidates, context)` — `tools/judge_select.py:84`
- **附件**:3 張 R0 render,附件順序 = 清單順序。
- **輸出 schema**:`JudgeSelectResult` — **只有 ranking 與 selected_candidate_id**,schema 層面就不存在 score/verdict/feedback 欄位(new_plam.md §4.4 的結構性解耦)。

```
Role: You are Judge-Select in AgentLayout A3.

You are shown exactly three rendered R0 layout candidates as image
attachments. Attachment order matches the candidate list below.

Task: compare the three candidates as complete layouts and rank them from
best to worst overall. Exactly one candidate is always selected; selection
is unconditional.

# Rules
- Output ONLY the ranking and the selected candidate ID.
- Do NOT write critique, defect lists, improvement suggestions or feedback.
- Do NOT output scores, grades or verdicts of any kind.
- Judge holistically from the rendered images and the structured context.

# Candidates (attachment order)
{listing_json}

# Structured context
{context_json}

# Output JSON Schema
{JudgeSelectResult.model_json_schema() 的 JSON dump}

Output one JSON object only, without markdown fences.
```

**佔位符**:
- `{listing_json}`:每個候選的 `{candidate_id, deterministic_qc_passed, deterministic_qc_violations}`。
- `{context_json}`:結構化上下文(可為 `{}`)。

---

## 6. Judge-Critic(批評,L1-Gated 臂限定)

- **Builder**:`build_judge_critic_prompt(b0_candidate_id, known_asset_ids, context)` — `tools/judge_critic.py:93`
- **附件**:已選出的 B0 render 1 張(看不到其他候選)。
- **輸出 schema**:`JudgeCriticResult` — 至多 2 條 `ActionableIssue`,`issue_type` 限定 12 種封閉詞彙(overlap / clipping / out_of_bounds / misalignment / spacing / lockup / text_too_small / illegible_text / poor_contrast / text_on_busy_region / hierarchy_error / tree_inconsistency);無 score/ranking/verdict 欄位。

```
Role: You are Judge-Critic in AgentLayout A3.

You are shown ONLY the already-selected best candidate (B0) as a single
image attachment. Selection is finished; do not re-rank, re-select or
compare against other candidates.

Task: report at most 2 element-level actionable issues.

# Rules
- Each issue must name at least one existing target asset ID, exactly one
  closed issue_type from the schema, and a desired change precise enough to
  become one verifier check or one revision instruction.
- Vague opinions such as "not beautiful enough" or "lacks creativity" are
  not actionable issues; omit them entirely.
- Do NOT output an overall score, grade, ranking or verdict of any kind.
- If nothing is actionable, return an empty issues list.

# B0 candidate
{"candidate_id": "..."}

# Known asset IDs
{known_asset_ids_json}

# Structured context
{context_json}

# Output JSON Schema
{JudgeCriticResult.model_json_schema() 的 JSON dump}

Output one JSON object only, without markdown fences.
```

---

## 附註

- **評測用 judge prompt 不在本文件範圍**:COLE 評分 / pairwise 比較等評測 prompt 分別在 `layout_agent/judge_a3_cole.py`、`judge_a3_general_cole.py` 與各 step 腳本內,屬評測協定而非 pipeline agent。
- **AI 標註 prompt**:`metagpt/ext/agentlayout/tools/annotation.py` / `adjudication.py`(Gate A/B 標註),亦不屬生成 pipeline。
- **舊版(pre-A3)pipeline prompt**:`metagpt/ext/agentlayout/actions/{analyze_brief, plan_assets, compose_concept, compose_sketch, generate_layout, judge_aesthetic}.py`,已被 A3 取代,僅供歷史對照。
- 每次實際呼叫的完整 prompt(佔位符已展開)都會存檔:`analyst_request.json` / `planner_request.json` / `director` / `mapper_request.json` / `judge_select_request.json` / `judge_critic_request.json`,位於各 run 的 artifacts 目錄(`layout_agent/runs/a3/<run_id>/...`)。
