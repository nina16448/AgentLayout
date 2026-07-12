# A3 外部 Baseline 發現

## 已知專案事實

- A3 正式 Relation track 使用 `layout_agent/sample_ids/a3_relation_n100.json`。
- A3 P-Full 要求所有 placeable foreground 分離，不得把 designer placement 烘進背景。
- 現有 A3 六軸 evaluator 可作 matched evaluation；Underlay 在 P-Full v1 為 N/A。
- 目前機器為 8 張 GTX 1080 Ti 11GB；8B 模型不適合單卡 FP16，須 4-bit 或多卡。
- Relation N=100 asset count 曾盤點為 min=6、mean=13.57、max=37，5 筆超過 25；
  正式流程必須明列這些樣本的處理與失敗率。

## 待核實

- 實際 4-bit inference 在 GTX 1080 Ti 的載入與速度（只有執行 smoke 才能確認）。
- Hugging Face 帳號是否已接受 Llama-3.1-8B manual gate。

## 官方來源核實結果

- Elem2Design repo HEAD：`4665358e0d06aa5d4365e63cd0e4b6df12902666`。
- Adapter revision：`c4f20b5b8496f6627260a5b38d65a736391bfa63`；base model 是
  Llama-3.1-8B，模型卡標示 MIT（base model 另受 Llama license/manual gate）。
- Base model revision：`d04e592bb4f6aa9cfee91e2e20afa771667e1d4b`，manual-gated。
- 官方 builder 已支援 `load_4bit` + NF4，但 inference CLI 沒暴露該參數，並在載入後
  強制 `model.to("cuda")`；必須做最小 patch。
- 官方 `render.py` 以 GT width/height resize 每個元素，違反 A3 P-Full leakage
  contract，不能用它準備輸入。
- 官方 `crello_v1.yaml` 的訓練上限是 25 elements；create_dataset 只對 train/
  validation 過濾，因此 formal test 不應自行丟棄 >25 cases。
- 官方 inference 是五層 progressive generation，預設 temperature=0.7、top_p=0.95、
  num_return=1。

## 設計決策

- 產生獨立 `a3.external-baseline-run.v1`，不偽造成 A3 三候選 L0 bundle。
- final render 使用 A3 R3 renderer；metrics 共用 A3 evaluator 的底層實作。
- 使用官方發布的 predicted Crello layer roles，不使用 GT roles，也不另呼叫 GPT。
- Primary family 為 SGC/TLC/PCA；secondary family 為 Ali/Ove/Rea/Occ；各 family
  分別做 Holm correction。
- generation failures 必須保留在 100 的 denominator 中，metric comparison 使用
  paired-success intersection 並明列 paired N。

## 官方連結

- Repository: https://github.com/microsoft/elem2design
- Model card: https://huggingface.co/microsoft/elem2design
- GT-size leakage location:
  https://github.com/microsoft/elem2design/blob/main/dataset/src/crello/render.py
- Quantized loader:
  https://github.com/microsoft/elem2design/blob/main/llava/model/builder.py
- Official inference:
  https://github.com/microsoft/elem2design/blob/main/llava/infer/infer.py
