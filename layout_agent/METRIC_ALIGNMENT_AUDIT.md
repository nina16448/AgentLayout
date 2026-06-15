# SEGA / PKU 指標對齊稽核（逐指標 vs 我方實作）

**日期**：2026-06-13　**性質**：把 `evaluation/sega_metrics.py` 逐行對照兩份權威來源，找出「實驗無法對齊」的成因。
**權威來源**：
- PKU PosterLayout `eval.py`（Hsu et al. CVPR 2023，官方 repo `PKU-ICST-MIPL/PosterLayout-CVPR2023`，本 session WebFetch 逐函式取回，canvas 513×750、cls 1=text/2=image/3=underlay/0=invalid）。
- SEGA `arXiv 2510.15749`（ICCV 2025）主文 + CVF 補充材料 §4.1.2 / §4.3（指標定義與 Table 3，本 session WebFetch 逐字取回）。

**一句話**：我方 6 個指標的「骨架」對，但有 **4 個會直接破壞數值可比性的實作差異** + **3 個協定層級的概念落差**（尤其 underlay 與 occlusion）。其中 Alignment 有一個明確的 feature 寫錯，最該先修。

> **修正進度（2026-06-13）— A1–A7 七項 code-level 全部到位**：✅ **A1**（Ali feature→right/bottom）｜✅ **A2**（drop_invalid_elements 前處理）｜✅ **A3**（Occ saliency mean→max，PFPN-vs-ISNet 殘留）｜✅ **A4**（Rea/Occ 分母→全可評估樣本）｜✅ **A5**（Und_s 複製 PKU 右緣 bug，與 Table 3 同協定）｜✅ **A6**（documented deviation：保留正確 float Sobel，不照抄 overflow，因 QC 校準依賴它）｜✅ **A7**（Und None-aware 聚合，只對有 underlay 樣本平均；agent/random/centered 報 N/A）。`test_sega_metrics.py` **18 passed**、step20 已接線、未跑實驗。**P1/P2/P3（協定層級）已結案**（2026-06-13 使用者拍板）：= 同一任務形式差異（asset-placement vs text-only-on-prepared-canvas）的三張臉，**不追同表、零新實驗**；指標只用於內部比較、SEGA 維持 indicative。詳見 §8 結案段與 `IMPLEMENTATION_LOG.md`。

---

## 0. 嚴重度總表

| # | 指標 | 問題 | 類型 | 嚴重度 | 方向影響 |
| --- | --- | --- | --- | --- | --- |
| A1 | Alignment | feature 用 width/height，PKU 用 right/bottom | 實作 bug | 🔴 高（且好修） | Ali 值整段不可比 |
| A2 | 全部幾何 | 缺 `getRidOfInvalid`（<0.1% canvas 的 box 未剔除） | 缺前處理 | 🟠 中 | Ali/Ove 受微小 box 汙染 |
| A3 | Occlusion | saliency 融合 mean(BASNet,ISNet)，PKU 是 max(PFPN,BASNet) | pipeline 偏離 | 🔴 高 | 我方 Occ 系統性偏低 |
| A4 | Readability / Occlusion | 分母用 n_counted（有貢獻樣本），PKU 用全樣本數 | 實作差異 | 🟠 中 | Rea/Occ 偏高（變差） |
| A5 | Underlay strict | 我方四邊都檢查；PKU 有 `xr_2>=xr_2` 永真 bug（右邊不檢查） | 反向 bug | 🟠 中 | 我方 Und_s 比 PKU/SEGA 嚴格→偏低 |
| A6 | Readability | Sobel 我方先轉 float64 再平方；PKU 在 uint8 上平方（疑似 overflow） | dtype | 🟢 低/待查 | Rea 量級可能偏移 |
| A7 | Underlay loose/strict | 逐樣本呼叫對無 underlay 樣本回 0，naive 平均會把它算進分母 | 聚合 | 🟠 中 | Und 偏低 |
| P1 | Underlay 全體 | SEGA 用「GT underlay + 模型 text」算；我方 type-0 classifier 推 underlay，構造不同 | 協定 | 🔴 高 | Und 只能鬆散對照 |
| P2 | Overlay / Occlusion | SEGA 只擺 text；我方擺 text+image，元素更多 | 協定/任務不對稱 | 🔴 高 | 我方 Ove/Occ by construction 偏高 |
| P3 | Content 指標 canvas | SEGA 不 inpaint、非文字層已 render 進 canvas | 協定 | 🟠 中 | 須確認我方 bg 一致 |

> ✅ 已對齊、不用動的：Und_l 分母＝被蓋住的「非 underlay 元素面積」(`a_inter/a_2`)；Overlay 排除 underlay、÷n_elements；Ali 的 g(x)=−log₁₀(1−x)、min-over-axes、sum-over-elements÷#layouts；Readability 的 text mask + underlay 抹除、Sobel `sqrt((gx²+gy²)/2)/max`；Occlusion 取 cls>0 全元素、不抹 underlay；Ali 用 per-sample native canvas（SEGA 用 Crello 原生解析度，**這點我方反而比硬套 513×750 正確**）。Validity(Val) SEGA Table 3 **沒有**，我方不實作 **無妨**。

---

## 1. Alignment（lower better）— 🔴 A1 明確 bug

`sega_metrics.py:167`：
```python
theda.append([l, t, (l + r) / 2.0, (t + b) / 2.0, r - l, b - t])
#              left top  center_x      center_y    width  height
```
PKU `eval.py`：
```python
theda.append([pos[0], pos[1], (pos[0]+pos[2])/2, (pos[1]+pos[3])/2, pos[2], pos[3]])
#              xl(left) yl(top)  center_x          center_y          xr(right) yr(bottom)
```
**差異**：第 5、6 個 feature 我方是 **寬、高**，PKU 是 **右緣、下緣**。alignment 的語意是「邊緣／中心對齊」，量寬高差 `|w1−w2|` ≠ 量右緣差 `|r1−r2|`。**這讓 Ali 整段數值與 PKU/SEGA 不可比。**
**修法**：`r - l, b - t` → `r, b`（一行）。其餘 alignment 邏輯與 PKU 一致。

---

## 2. Overlay（lower better）— 大致對齊

- 排除 underlay（`cls>0 & cls!=3`）✅、pairwise IoU ✅、÷n_elements（非 C(n,2)）✅、÷#layouts ✅。
- 我方多一個 `if n==0: continue` 防 NaN（PKU 會除零）；實務上 Crello 每張都有元素，**可接受**。
- ⚠️ 真正的不對稱在 **P2**：SEGA 只擺 text，pairwise 只在 text 之間；我方擺 text+image，pair 數更多、重疊機會更高 → **我方 Ove 天生偏高、非品質差**。要嘛只對 text 算 Ove，要嘛明白標註任務不對稱。

---

## 3. Underlay loose（higher better）— 公式對、協定要注意

- `_bbox_inter_oneside(u, o)` = `a_inter / area(o=非underlay)` ✅ **正好對上 PKU** `metrics_inter_oneside(bb1=under, bb2=other)=a∩/a₂`。分母是「被蓋的內容元素面積」，不是 underlay 面積、不是 min 面積——這點以前容易記反，**現況是對的**。
- max-over-others ✅、÷n1 ✅、÷avali(有 underlay 的 layout 數) ✅。
- ⚠️ **A7 聚合陷阱**：`step20/step25` 是「逐 layout 呼叫 `metric_underlay_loose([layout])`」。無 underlay 的 layout 回 `0.0`（line 257-258），與「有 underlay 但沒蓋到」的 0 **無法區分**。若外層對所有樣本 naive 取平均，無 underlay 樣本會被算進分母 → Und 被拉低。**PKU 是只對 avali（有 underlay）樣本平均。** 建議：一次把全部 layouts 傳進去（內部 ÷avali 正確），或逐樣本時自行濾掉無 underlay 樣本。

---

## 4. Underlay strict（higher better）— 🟠 A5 我方比 PKU 嚴格

PKU `is_contain`：
```python
c1 = xl_1 <= xl_2; c2 = yl_1 <= yl_2
c3 = xr_2 >= xr_2   # ← 官方 bug：bb2 跟自己比，永遠 True（本意是 xr_1 >= xr_2）
c4 = yr_1 >= yr_2
```
→ PKU 實際只檢查 **左、上、下** 三邊，右邊從不檢查。
我方 `_is_contain`（line 108）四邊都正確檢查。
**後果**：對**同一個有 underlay 的 layout**（例如 Crello designer GT），我方 Und_s 會**比 PKU/SEGA 低**（更難滿足）。要跟 Table 3 數字對齊，需在比較情境下**複製這個 bug**（移除右緣檢查）或在論文明白標註此偏離。我方目前不發 underlay 時兩者都 0，不影響；**一旦對 GT/注入 underlay 算 Und_s 就會差**。

---

## 5. Readability（lower better）— 🟠 A4 分母 + 🟢 A6 dtype

- Sobel `sqrt((gx²+gy²)/2)`、/max 正規化 ✅；text mask、underlay 抹除 ✅；ksize=3 ✅。
- 🟠 **A4 分母**：PKU `metrics += grad/area`（僅在非零時累加），但 `return metrics/len(img_names)`（**全部圖**）。我方 `n_counted += 1` 只算有貢獻的，`return total/n_counted`。→ 有「無文字/被 underlay 全抹」的樣本時，我方分母較小、**Rea 偏高（看起來更差）**。修：改成除以總樣本數。
- 🟢 **A6 dtype**：我方 `grad_x.astype(np.float64)**2`（line 315）；PKU 在 `cv2.Sobel(...,-1,...)` 的 uint8 結果上直接平方，numpy uint8 平方會 **overflow mod 256**。我方數學上「正確」、PKU 可能是 quirk。**待查**：若要逐位對齊 PKU 數字可能要重現它的 dtype 行為；但這通常不值得為了對齊去重現一個 overflow，建議標註而非照抄。

---

## 6. Occlusion（lower better）— 🔴 A3 saliency 不同源 + 🟠 A4 分母

- mask 取 cls>0 全元素、不抹 underlay ✅；score=sum(sal·mask)/area ✅。
- 🔴 **A3 saliency 融合**：
  - PKU：`np.maximum(PFPN_pred, BASNet)`（**max**、第二顆是 **PFPN**）。
  - 我方 `saliency_basnet_isnet.py`：`(BASNet + ISNet)/2`（**mean**、第二顆是 **ISNet/rembg isnet-general-use**）。
  - 兩處差異（max→mean、PFPN→ISNet）使 saliency 系統性偏低 → **我方 Occ 偏低（看起來更好）**。SEGA 補充材料**未指名** saliency 模型（只說 "the saliency map S"），所以 SEGA 自己也含糊；但 PKU lineage 明確是 `max(PFPN,BASNet)`。**最低限度先把 mean→max**；要嚴格對齊 PKU 還需 PFPN。
- 🟠 **A4 分母**：同 Readability，除以 n_counted 而非全樣本數，偏高/偏移。

---

## 7. 缺的前處理：getRidOfInvalid（🟠 A2）

PKU 跑幾何指標前先 `getRidOfInvalid`：把 clamp 到畫布後**面積 < 5.13·7.50·10 = 384.75 px²**（≈ canvas 面積 **0.1%**）的 box 的 cls 設為 0（不刪 box，只讓後續 `cls>0` mask 跳過）。我方 `step20/step25` 直接餵原始 layout、**無此步**。
**後果**：微小/退化 box 會多算進 Ali（多一組對齊項）與 Occ/Rea 的遮罩。**修法**：在丟進 `sega_metrics` 前，剔除 `area < 0.001 × canvas_w × canvas_h` 的元素（依 native canvas 換算，不是硬套 384.75）。

---

## 8. 協定層級落差（最影響「能不能同表」）

### P1 — underlay 判斷方式（🔴 最關鍵）
SEGA 在 Crello **不預測 underlay**：underlay 已 render 進 canvas，Und 指標用「**GT underlay 框 + 模型擺的 text 框**」計算（補充材料 §4.1.2：以「偵測覆蓋文字的封閉曲線」取得 underlay GT 草稿、**再人工檢查校正**）。
我方 underlay 來自 **type-0 image-content classifier**（見 [[project-crello-underlay-in-type0]]），構造與 SEGA 的手工封閉曲線偵測**不同**。
**後果**：(1) 我方早期「Und=0 honest reading」（`sega_metrics.py:25-28` docstring）是**過時**說法——Step 29 已實際算出 Und_l 0.5518（docstring 該更新）；(2) 即使注入 underlay，我方 Und 與 SEGA 0.93 仍**只能鬆散對照**，因 underlay GT 來源不同；(3) 要逼近 SEGA 協定，應「**GT underlay + 我方 text**」而非「我方推的 underlay」。**這是 underlay 對標的根本限制，須在論文明寫，不可同格宣稱。**

### P2 — text-only vs text+image（🔴）
SEGA 只擺 text（image/logo/underlay 都在 canvas）。我方 placement 擺 text+image。→ Ove（pair 數）、Occ（覆蓋面積）我方天生較大，**非品質差異**。要公平比，Ove/Occ 應「只對 text 元素」算，或明白標註任務不對稱。

### P3 — canvas 內容不同（🟠 已查證 = 真差異）
SEGA 補充材料：Crello canvas **不 inpaint**、**所有非文字層（照片+shape+underlay）render 進去**，故 Read/Occ 比 PKU/CGL 大一個量級（Occ ≈0.38–0.49 vs ≈0.12–0.24）。
**我方查證（2026-06-13，`run_role_team_live_crello.py:63 _composite_background_plates`）**：canvas **只壓 `kind=="background_candidate"` 的整版底圖 plate**（純色/漸層/紋理底）；照片/logo/shape 是**分開的 placed element（CLS_IMAGE_LOGO）不進 canvas**。→ 我方 saliency 在「近乎平坦底圖」上算 → **Occ 被人為壓低、與 SEGA（canvas 內含顯著產品照）非同基準**。

---

### ✅ P1/P2/P3 結案決定（2026-06-13，使用者拍板）
P1/P2/P3 不是可各自打補丁的 bug，而是 **「asset-placement（我方：吃 asset、全部都擺）vs text-only-on-prepared-canvas（SEGA：只擺 text）」同一個任務形式差異的三張臉**——即 2026-06-04「architectural commitment trade-off」那條線。
**決定：結案為 documented protocol difference，不追同表、零新實驗。**
- 指標**只用於內部比較**（AgentLayout vs designer GT vs random/centered，同一套已修指標、同一 canvas 定義；即 Step 15/20/29 的用法）——數字乾淨、可寫論文。
- **SEGA 維持 indicative related-work 定位**（同 AesthetiQ Table 1 等級），永不 head-to-head；理由即本節三張臉，寫進論文 limitation 一段。
- 要「真同表」唯一路徑 = SEGA-mode（composite 非文字層進 canvas＋AL 退化成只擺 text＋注入 GT underlay）= 2026-06-04 否決的 B 階段、會抹掉 depth 賣點，**不做**。
- 可選未做：eval 加 text-only 的 Ove/Occ 變體（~10 行）誠實揭露 P2 量級——使用者未要求，待提。

---

## 9. SEGA Table 3 基準（Crello，供對照，本 session 逐字取回）

報告欄位：Ali↓ Ove↓ Und_l↑ Und_s↑ Read↓ Occ↓ ＋ GPT-4V SDL/SQL/STV/SIO/SMean↑ ＋ Time。**無 Val**。Crello 上 baseline 只有 **FlexDM、PosterLlama**（CGL-GAN/LayoutDM/RALF 只在 PKU/CGL 的 Table 2）。

| Method | Ali↓ | Ove↓ | Und_l↑ | Und_s↑ | Read↓ | Occ↓ | SMean↑ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FlexDM | 0.0122 | 0.1139 | 0.6889 | 0.5034 | 0.0516 | 0.4850 | 4.950 |
| PosterLlama | 0.0099 | 0.0238 | 0.9204 | 0.7378 | 0.0395 | 0.4041 | 5.542 |
| SEGA 7B | 0.0086 | 0.0040 | 0.9337 | 0.8978 | 0.0282 | 0.3964 | 5.941 |
| SEGA 13B | 0.0095 | 0.0025 | 0.9541 | 0.9270 | 0.0260 | 0.3907 | 6.320 |
| **GT** | 0.0100 | 0.0116 | 0.9643 | 0.8187 | 0.0259 | 0.3797 | 6.828 |

> 注意：rule-based 上 SEGA-13B 在 Ove/Read 已微幅超過 GT（過擬合指標），但 GPT-4V SMean 仍 GT(6.828) > 13B(6.320)——與我方「rule-based 合規 ≠ 美學偏好」(Step 66) 同調，可互相佐證。

---

## 10. 建議處理順序（純診斷，未改 code）

1. 🔴 **A1**（Ali width/height→right/bottom）：一行、明確錯、先修。
2. 🔴 **A3**（Occ saliency mean→max，理想連 PFPN 一起）：影響 Occ 可比性。
3. 🔴 **P1/P2**（underlay 協定 + text-only）：不是 code bug 是**對標協定**，决定 Und/Ove/Occ 能否同表；論文須明寫限制。
4. 🟠 **A2 / A4 / A5 / A7**：getRidOfInvalid 前處理、Rea/Occ 分母、Und_s bug 複製與否、Und 聚合濾無-underlay 樣本。
5. 🟢 **A6**：Sobel dtype，標註即可。
6. **docstring 更新**：`sega_metrics.py:25-28`「Und=0 honest reading」已被 Step 29 推翻，需改。

> 任何「與 SEGA Table 3 同格對標」在 P1/P2 未處理前都不成立；現階段我方 SEGA 6 指標只能當 **indicative**（沿用既有 caveat）。修掉 A1/A3/A4 可讓我方**自家前後比較（如 Step 29 / Step 66）**內部一致、數值乾淨，這部分值得做。

**關聯**：[[project-crello-underlay-in-type0]]（underlay 來源 = type-0 classifier，與 SEGA 手工 GT 不同）、[[project-sota-aesthetiq-same-data-metric]]、[[project-no-pku-postero-alignment]]（跨資料對標的同一架構抉擇）、[[project-step66-constraint-solver]]（rule-based 合規≠美學偏好，與 SEGA-13B 過擬合 rule-based 同調）。
