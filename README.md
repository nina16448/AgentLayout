# AgentLayout

**Decomposing Content-Aware Layout Generation into Collaborative Agent Workflows**

<p align="center">
[ <b>繁體中文</b> ]
<br>
<b>以 Multi-Agent Workflow 解決內容感知排版生成（Content-Aware Layout Generation）的碩士論文研究專案。</b>
</p>

---

## 專案簡介

AgentLayout 研究的問題是：**在既有背景畫布上，根據自然語言 brief 與一組既有素材（image / text），決定每個元素的座標、大小、層次與視覺屬性**。

排版本質上不是機率性的內容生成問題，而是同時結合語意理解、空間配置與設計約束的複雜決策過程。端對端模型在這類任務上普遍缺乏可控性（Controllability）、可驗證性（Verifiability）與可除錯性（Debuggability）。因此本研究將整個流程拆解為 Multi-Agent Workflow：

> **核心論點：內容感知排版是一個可分工、可觀測、可驗證、可逐步修正的系統問題，而非單純的生成問題。**

本專案以 [MetaGPT](https://github.com/geekan/MetaGPT) multi-agent 框架為基礎開發，所有 AgentLayout 程式碼位於 `metagpt/ext/agentlayout/`，實驗文件與結果位於 `layout_agent/`。

**任務 scope**：不包含背景生成、字型/裝飾合成、影像 inpainting（by-design，對齊 AesthetiQ / LayoutNUWA / PosterLLaVa 等同類 content-aware layout generation 研究）。

---

## 系統架構（A3 Pipeline，現行版本）

A3 是目前的主要架構，由 LLM Agent 與非 LLM 模組（CV / Python）分工組成——LLM 只負責語意理解與推理，幾何驗證與數值計算全部交給確定性模組。

```
Inputs：user brief + canvas + foreground assets（+ 背景圖）
   │
   ▼
Background Analyzer（CV，非 LLM）
   saliency 偵測 → safe zones、主色盤、建議文字顏色
   │
   ▼
Agent 1：Analyst（LLM，vision）
   brief + assets contact sheet → Design Spec（語意結構化規格）
   │
   ▼
Agent 2：Asset Planner（LLM，T2 臂）
   素材語意關係推理 → Layout Tree（群組 + parent-child 層級）
   │
   ▼
Agent 3：Composition Director（LLM）
   → 3 個 composition concepts（整體構圖方向）
   │
   ▼
Agent 4：Coordinate Mapper（LLM，每個 concept 各一次）
   concept → 像素級座標（left/top/width/height/z-order）
   │
   ▼
Quality Checker（Python，非 LLM）
   幾何合法性 + 約束 + 可讀性規則驗證（22+ 條規則）
   │
   ▼
Renderer（Python + PIL，非 LLM）
   → 3 張候選渲染圖
   │
   ▼
Agent 5：Internal Judge（LLM，vision）
   比較 3 張候選 → 選出 Final Layout
```

- **Loop 臂**：預設 L0（單向流程）；L1-Gated（judge critic + 一次 revision）經 Gate C 實驗驗證 compliance 不足，未採用。
- **Tree 臂**：T2（含 Asset Planner / Layout Tree）為主要配置，語意分組指標（SGC / TLC / PCA）與外部 baseline 比較均顯著優於無樹配置。

完整的 agent prompt 見 `layout_agent/AGENT_PROMPTS.md`，Quality Checker 規則見 `layout_agent/QUALITY_CHECKER_RULES.md`。

### 前一代架構（Refinement Loop Pipeline）

A3 之前的四-Agent 架構（Analyst / Asset Planner / Layout Generator / Aesthetic Judge + Refinement Loop）保留於 `metagpt/ext/agentlayout/pipeline.py`（`LayoutPipeline.run()`），架構圖見 `layout_agent/PIPELINE.md`，設計理念見 `layout_agent/README.md` 與 `layout_agent/README(new).md`。

---

## 安裝

> Python 需 3.9 以上、3.12 以下。建議使用 conda：
> `conda create -n meta python=3.9 && conda activate meta`

```bash
git clone <this repo> && cd MetaGPT
pip install -e .
```

### LLM 設定

建立 `~/.metagpt/config2.yaml`（範本見 `config/config2.example.yaml`）：

```yaml
llm:
  api_type: "openai"   # or azure / ollama / groq etc.
  model: "gpt-5.4-mini-2026-03-17"   # A3 各 stage 實際使用的模型（見 layout_agent/configs/）
  base_url: "https://api.openai.com/v1"
  api_key: "YOUR_API_KEY"
```

> 注意：換模型時需同步確認 `metagpt/provider/constant.py` 的 `MULTI_MODAL_MODELS` 是否包含該模型，否則 vision 輸入會靜默退化為純文字。

---

## 使用方式

### A3 pipeline（現行）

```bash
conda activate meta
python layout_agent/run_a3.py --help     # run planner / 初始化 immutable run
```

批次實驗（Full-Crello batch）流程與 resume 機制見 `layout_agent/FULL_CRELLO_BATCH_PLAN.md`。

### Demo

```bash
python layout_agent/run_demo.py          # 單一 sample 的端到端 demo
```

完整的 pipeline 逐步輸出範例（背景分析 → Design Spec → Layout Tree → 3 個 concepts → 座標 → QC → 候選圖 → Judge 選擇），見 `layout_agent/output2/step98_a3_walkthrough/`。

---

## 資料集與評估

- **資料集**：[Crello](https://github.com/CyberAgentAILab/canvas-vae)（多元設計類型版面資料集；完整 test split N=1,897 已跑過）。PKU PosterLayout / CGL 已完成可行性評估但不對標（見 `layout_agent/PKU_FEASIBILITY.md`、`layout_agent/CGL_FEASIBILITY.md`）。
- **評估指標**：
  - **SEGA 6 指標**（Ali / Ove / Und_l / Und_s / Read / Occ）— `metagpt/ext/agentlayout/evaluation/sega_metrics.py`，對齊 audit 見 `layout_agent/METRIC_ALIGNMENT_AUDIT.md`
  - **mIoU** — `metagpt/ext/agentlayout/evaluation/iou.py`
  - **COLE 5 軸 pairwise win-rate**（MLLM judge，vs designer ground-truth）
  - **語意分組指標**（SGC / TLC / PCA）— Layout Tree 語意結構評估
  - **外部 baseline**：Elem2Design（Relation 子集 head-to-head）
- **主要結果**：以 `layout_agent/result.md` 與 `layout_agent/CURRENT_EXPERIMENT_RESULTS.md` 為權威來源；實驗全紀錄見 `layout_agent/A3_EXPERIMENT_LOG.md` 與 `layout_agent/IMPLEMENTATION_LOG.md`。

---

## Repo 結構

```
metagpt/ext/agentlayout/       # AgentLayout 全部程式碼
├── a3_pipeline.py             #   A3 主流程（L0）
├── a3_pipeline_l1.py          #   L1-Gated 臂（實驗用）
├── pipeline.py                #   前一代 Refinement Loop pipeline
├── roles/  actions/  tools/   #   Agent 定義、LLM action、非 LLM 模組（QC / renderer / CV）
├── evaluation/                #   SEGA 指標、mIoU、baselines、saliency
└── schema.py  layout_tree_v3.py

layout_agent/                  # 實驗文件、prompt、結果與分析
├── AGENT_PROMPTS.md           #   A3 各 Agent prompt 總覽
├── QUALITY_CHECKER_RULES.md   #   QC 全部規則 + 門檻 + 校準來源
├── A3_EXPERIMENT_LOG.md       #   A3 實驗流水帳
├── IMPLEMENTATION_LOG.md      #   每個 step 的設計決策與 trade-off
├── result.md                  #   論文引用的權威數據
├── EXPERIMENT_MATRIX.md       #   主實驗與 ablation 對照表
└── output2/                   #   各 step 實驗輸出
```

其餘目錄（`metagpt/roles/`、`metagpt/actions/` 等）為上游 MetaGPT 框架程式碼。

---

## 文件導讀

| 檔案 | 用途 |
|---|---|
| `layout_agent/README.md` | 系統設計藍圖與研究理念（前一代架構） |
| `layout_agent/README(new).md` | 前一代架構的實作現況版 |
| `layout_agent/AGENT_PROMPTS.md` | A3 各 Agent prompt 與呼叫順序 |
| `layout_agent/PIPELINE.md` | 前一代 pipeline 架構圖（Mermaid + ASCII） |
| `layout_agent/A3_EXPERIMENT_LOG.md` | A3 實驗全紀錄 |
| `layout_agent/result.md` | 最新實驗結果與論文可引用數據 |
| `layout_agent/METRIC_ALIGNMENT_AUDIT.md` | SEGA / PKU 指標逐行對齊 audit |

---

## 相關研究定位

| 比較對象 | 關係 |
|---|---|
| [AesthetiQ (CVPR 2025)](https://arxiv.org/abs/2503.00591) | MLLM + DPO 美感對齊；win-rate protocol 對齊對象 |
| [PosterO (CVPR 2025)](https://arxiv.org/abs/2505.07843) | Layout Tree 概念來源；本研究改為 LLM 直接推理、不需訓練 |
| [SEGA (ICCV 2025)](https://arxiv.org/abs/2510.15749) | 幾何指標 head-to-head 對照；coarse-to-fine feedback 範式 |
| [MetaGPT (ICLR 2024)](https://arxiv.org/abs/2308.00352) | Multi-agent 框架基礎；Agent 間結構化 JSON 通訊設計 |

---

## 致謝與引用

本專案 fork 自 [MetaGPT](https://github.com/geekan/MetaGPT) 並在其框架上開發。引用 MetaGPT 請使用：

```bibtex
@inproceedings{hong2024metagpt,
      title={Meta{GPT}: Meta Programming for A Multi-Agent Collaborative Framework},
      author={Sirui Hong and Mingchen Zhuge and Jonathan Chen and Xiawu Zheng and Yuheng Cheng and Jinlin Wang and Ceyao Zhang and Zili Wang and Steven Ka Shing Yau and Zijuan Lin and Liyang Zhou and Chenyu Ran and Lingfeng Xiao and Chenglin Wu and J{\"u}rgen Schmidhuber},
      booktitle={The Twelfth International Conference on Learning Representations},
      year={2024},
      url={https://openreview.net/forum?id=VtmBAGCN7o}
}
```

MetaGPT 原始授權為 MIT License（見 `LICENSE`）。
