# 任務：實作語意分組指標 SGC / TLC / PCA

## 背景與目的

論文的核心主張是「Layout Tree 的語意分組會反映在最終版面上」，但目前六個幾何指標
和 COLE 美學評分都沒有直接測量這件事。這次要新增三個指標，量化「同組元素是否真的
在畫布上靠在一起」，未來會放進論文主表格，與 SEGA / designer GT 對比。

請先閱讀 IMPLEMENTATION_LOG.md 中 LayoutTree、CandidatesBatch、DesignSpec 的
schema 定義，複用現有的資料結構，不要另造平行格式。

## 輸入

每個樣本需要兩份資料：

1. 一棵 LayoutTree(該樣本的語意分組來源)
2. 一份最終版面：元素列表,每個元素有 id 和 bounding box(x, y, width, height,
   像素座標)以及畫布 canvas_width, canvas_height

注意:評測對象可能是 agent 輸出、designer GT、或 baseline(SEGA 等)的輸出。
**同一個樣本,所有方法必須用同一棵樹來評**(公平性關鍵),所以 tree 的來源要做成
可插拔的參數:預設用我們 Asset Planner 產出的 tree,未來可換成人工標註的 tree。
baseline 的版面只要能對上 element id 即可;若 id 對不上,先做一個 id 對齊層
(按 semantic_type + 順序匹配),對不齊的樣本記錄後跳過,不要硬湊。

## 前處理

1. 所有 bounding box 座標除以 canvas 寬高,正規化到 [0,1]。
2. 只計前景元素;背景(bg_*)排除。
3. 分組定義:LayoutTree 中 root 的每一個直接子節點,連同其整棵子樹,構成一個
   group G_k。root 底下的單元素子節點自成一組(singleton group)。
   例如 log 裡的案例:
   root ├─ product_img_1 ─ headline_1 ─ {pricetag_1, caption_1, caption_2}
        ├─ logo_1 ...
   → product_img_1 那整條分支是一個 group(5 個元素),logo_1 是 singleton group。

## 距離函數(所有指標共用)

兩元素間用「外框 L1 間隙距離」,不用中心點距離:

d(e_i, e_j) = max(0, gap_x) + max(0, gap_y)

其中 gap_x = max(x_i, x_j) - min(x_i + w_i, x_j + w_j),即水平方向兩框之間的
空隙(重疊或相接時 ≤ 0,取 max(0, ·) 後為 0);gap_y 同理。
兩框重疊或相貼 → d = 0。請為這個函數寫單元測試:重疊、相貼、水平分離、
垂直分離、對角分離五種案例。

## 指標一:SGC (Semantic Group Compactness)

D_intra = 對每個 |G_k| ≥ 2 的 group,計算組內所有元素兩兩 d 的平均;
          再對這些 group 取平均(group-level 平均,不是 pair-level,
          避免大 group 主導)。
D_inter = 所有跨 group 元素對的 d 的平均(pair-level 平均)。

SGC = D_inter / (D_intra + D_inter + 1e-6)

值域 [0, 1),越接近 1 越好;0.5 代表分組沒有反映在版面上。

邊界情況:

- 全部 group 都是 singleton → D_intra 無定義 → 該樣本 SGC 記為 None 並計入
  skipped_sgc 統計。
- 只有一個 group → D_inter 無定義 → 同上,計入 skipped_sgc。

## 指標二:TLC (Tree Layout Consistency)

取所有三元組 (i, j, l) 滿足:e_i 與 e_j 同 group、e_i 與 e_l 不同 group。

TLC = 這些三元組中,d(e_i, e_j) < d(e_i, e_l) 成立的比例。
平手(d 相等,常見於兩個都是 0)算 0.5 分,不要直接算 0 或 1。

值域 [0, 1],隨機期望 0.5。三元組數為 0 的樣本記 None,計入 skipped_tlc。

效能提醒:元素數 n ≤ 15 左右,三元組是 O(n³) 但絕對量很小,直接暴力算即可,
不要過度優化。先把 n×n 距離矩陣算一次快取起來,三個指標共用。

## 指標三:PCA (Parent-Child Adjacency)

對 LayoutTree 上每一條父子邊 (p, c)(不含 root 的邊):
檢查 d(e_p, e_c) ≤ median over j≠p of d(e_p, e_j) 是否成立。
PCA = 成立的邊數比例。樹上沒有非 root 父子邊的樣本記 None,計入 skipped_pca。

## 輸出格式

1. per-sample JSON:每個樣本一筆 {sample_id, method, sgc, tlc, pca,
   n_elements, n_groups, n_triplets, skip_reasons}
2. aggregate markdown 報表:每個 method 一列,欄位為 SGC/TLC/PCA 的
   mean ± std、有效樣本數、skipped 數。格式比照 result.md 現有表格風格。
3. 額外輸出一份「質性案例挑選清單」:對每個 baseline,列出
   (agent_sgc - baseline_sgc) 最大的前 10 個 sample_id,供論文挑質性對比圖用。

## 驗收測試(先寫測試再寫實作)

1. 手工構造 4 元素、2 group、位置刻意「同組緊貼、異組遠離」的版面
   → SGC 應接近 1、TLC = 1.0。
2. 同樣的 tree,但把版面改成「同組打散、異組相鄰」→ SGC < 0.5、TLC < 0.5。
3. 全部元素疊在同一位置(全部 d = 0)→ TLC = 0.5(全平手),驗證「擠成一團
   不會虛高」的性質。
4. 單 group 樣本 → SGC = None、skip 原因正確記錄。
5. 隨機擺放 1000 次 Monte Carlo → TLC 平均應落在 0.5 ± 0.05,作為 sanity check。

## 不要做的事

- 不要動現有六個幾何指標和 COLE 評分管線的任何程式碼。
- 不要用 LLM 計算任何部分,這三個指標必須是純確定性 Python(與專案
  「幾何歸程式管」的哲學一致)。
- 不要在這一步做人工標註 tree 的整合,只要留好參數介面即可。

完成後在 IMPLEMENTATION_LOG.md 補一節,記錄:公式、邊界情況處理、驗收測試
結果、以及在 N=100 cached 樣本上跑出的第一版數字(agent vs GT)。