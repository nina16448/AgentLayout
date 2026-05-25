# 實驗指標

## Alignment (Ali ↓)
### 原論文：PosterLlama (ECCV 2024) + PKU PosterLayout (CVPR 2023)
* 定義：元素之間的對齊程度
* 算法
    * 對每對元素 (e_i, e_j)，計算它們的 6 個對齊軸（左、中、右、上、中、下）的最小距離差
    * 取最小值代表「最有可能對齊的軸」
    * 整張海報取所有元素對的平均
* 公式 (座標已 normalize 到 [0, 1]（除以 canvas 寬高）)
```
Ali = average over all element pairs of min(
    |x_left_i - x_left_j|,
    |x_center_i - x_center_j|,
    |x_right_i - x_right_j|,
    |y_top_i - y_top_j|,
    |y_center_i - y_center_j|,
    |y_bottom_i - y_bottom_j|
)
```

## Overlay (Ove ↓)
### 原論文：PKU PosterLayout (CVPR 2023)
* 定義：非 underlay 元素之間 的平均 IoU。
* 原文：The Overlap (Ove) represents the average IoU of all pairs of elements except for underlay elements.
* 算法：
```
For each pair of non-underlay elements (e_i, e_j):
    IoU(i,j) = intersection_area(i,j) / union_area(i,j)
Ove = average of all such IoU values
```

## Underlay Effectiveness Loose (Und_l ↑)
### 原論文：PKU PosterLayout (CVPR 2023)
* 定義：每個 underlay 與 non-underlay 元素的最大重疊比例。
* 原文：The UndL calculates the overlap ratio between each non-underlay element and an underlay element, then selects the maximum value among these ratios.
* 算法：
```
For each underlay u:
    For each non-underlay element e:
        ratio(u, e) = overlap_area(u, e) / area(e)
    score(u) = max(ratio over all non-underlay elements)
Und_l = average of score(u) over all underlays
```

## Underlay Effectiveness Strict (Und_s ↑)
### 原論文：PKU PosterLayout (CVPR 2023)
* 定義：嚴格版本——只計算「完全包含」（overlap ratio = 1）的情況。
* 原文：The UndS imposes stricter conditions, only including instances where the overlap ratio is 1.
* 算法：
```
For each underlay u:
    For each non-underlay element e:
        IF overlap_area(u, e) / area(e) == 1.0:
            include
    score(u) = 1 if any non-underlay is fully contained, else 0
Und_s = average over all underlays
```

## Occlusion (Occ ↓)
### 原論文：PKU PosterLayout (CVPR 2023)
* 定義：layout 元素覆蓋到 背景顯著區域 (saliency map) 的比例。
* 原文：The Occlusion (Occ) calculates the average proportion of layout elements covering the saliency regions, identified through saliency maps.
* 算法：
```
Get saliency map S (binary) from background image via BASNet + ISNet (or pfpn+basnet)
For each layout element e:
    occ(e) = sum(S inside bbox(e)) / area(bbox(e))
Occ = average over all elements
```

## Readability (Rea ↓)
### 原論文：PKU PosterLayout (CVPR 2023)
* 定義：text 元素區域的 背景非平整度（顏色梯度）
* 原文：The Readability (Rea) assesses the non-flatness of regions containing plain text elements without underlay decoration by calculating the average gradient of pixels in the layout area within the image space.
* 算法：
```
For each text element e (NOT covered by underlay):
    gradient_image = compute_image_gradient(background)  # e.g. Sobel
    rea(e) = average(gradient_image inside bbox(e))
Rea = average over all eligible text elements
```

## Aesthetic Scores
### 原論文：COLE (Jia et al. 2023)

* S_DL Design and Layout
* S_QL Content Relevance and Effectiveness
* S_TV Typography and Color Scheme
* S_IO Innovation and Originality


* #### COLE 完整 Quality Assurance Prompt 原文
```
You are an autonomous AI Assistant who aids designers by providing 
insightful, objective, and constructive critiques of graphic design 
projects.

Your goals are:
- Deliver comprehensive and unbiased evaluations of graphic designs 
  based on established design principles and industry standards.
- Identify potential areas for improvement and suggest actionable 
  feedback to enhance the overall aesthetic and effectiveness of 
  the designs.
- Maintain a consistent and high standard of critique.
- Utilize coordinate information for data description relative to 
  the upper left corner of the image, with the upper left corner 
  serving as the origin, the right as the positive direction, and 
  the downward as the positive direction.

Please abide by the following rules:
- Strive to score as objectively as possible.
- Grade seriously. A flawless design can earn 10 points, a mediocre 
  design can only earn 7 points, a design with obvious shortcomings 
  can only earn 4 points, and a very poor design can only earn 1-2 
  points.
- Keep your reasoning concise when rating, and describe it as briefly 
  as possible. If the output is too long, it will be truncated.
- Only respond in JSON format, no other information.

Grading criteria:

Design and Layout (1-10): The graphic design should present a clean, 
balanced, and consistent layout. The organization of elements should 
enhance the message, with clear paths for the eye to follow. A score 
of 10 signifies a layout that maximizes readability and visual appeal, 
while a 1 indicates a cluttered, confusing layout with no clear 
hierarchy or flow.

Content Relevance and Effectiveness (1-10): The content should be not 
only relevant to its purpose but also engaging for the intended 
audience, effectively communicating the intended message. A score of 
10 means the content resonates with the target audience, aligns with 
the design's purpose, and enhances the overall message. A score of 1 
indicates the content is irrelevant or does not connect with the 
audience.

Typography and Color Scheme (1-10): Typography and color should work 
together to enhance readability and harmonize with other design 
elements. This includes font selection, size, line spacing, color, and 
placement, as well as the overall color scheme of the design. A score 
of 10 represents excellent use of typography and color that aligns 
with the design's purpose and aesthetic, while a score of 1 indicates 
poor use of these elements that hinders readability or clashes with 
the design.

Graphics and Images (1-10): Any graphics or images used should enhance 
the design rather than distract from it. They should be high quality, 
relevant, and harmonious with other elements. A score of 10 indicates 
graphics or images that enhance the overall design and message, while 
a 1 indicates low-quality, irrelevant, or distracting visuals.

Innovation and Originality (1-10): The design should display an 
original, creative approach. It should not just follow trends but also 
show a unique interpretation of the brief. A score of 10 indicates a 
highly creative and innovative design that stands out in its 
originality, while a score of 1 indicates a lack of creativity or a 
generic approach.
```