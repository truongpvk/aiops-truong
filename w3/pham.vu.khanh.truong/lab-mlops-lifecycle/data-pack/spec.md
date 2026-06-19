# spec.md — Mô tả vấn đề business/technical và giải pháp MLOps cho anomaly detection

Tài liệu này mô tả bài lab end-to-end MLOps lifecycle cho **anomaly detection** bằng **IsolationForest**, gồm: train → đăng ký model → serve → theo dõi drift → retrain → blue-green swap bằng MLflow Registry alias → monitor sau deploy và **auto-rollback**.

---

## 0) Mục tiêu kỹ thuật cần đạt (Acceptance ở mức ý tưởng)
- Phát hiện khi production đã “khác” so với training baseline (drift).
- Khi drift được xác nhận → train model mới (v2), đăng ký và đưa qua staging/production với **approval gate**.
- Sau promotion → theo dõi quality; nếu xấu đi vượt ngưỡng → auto-rollback về v1.

---

## 1) Business problem (vì sao cần làm?)
Một đội vận hành đang chạy anomaly detection model cho hệ thống fintech (giám sát bất thường theo `latency_p99`, `error_rate`, `rps`). Gần đây, on-call báo cáo rằng:
- Mô hình **miss nhiều incident thật** (false negative tăng).
- Mô hình **tạo thêm false positives** (false positive tăng).

Nguyên nhân đã được xác định là **model decay**: production data distribution thay đổi do thay đổi traffic, tích hợp bên thứ ba, và rollout processor mới. Vì vậy model v1 không còn phù hợp với thực tế hiện tại và cần được làm mới.

CTO yêu cầu 2 điều bắt buộc:
1. **Build drift monitoring** để biết khi nào production đã diverge so với training baseline.
2. **Build retrain pipeline** để tự động hoặc bán tự động huấn luyện model mới và swap vào production theo cách an toàn, không downtime, có quan sát được và có rollback.

---

## 2) Technical problem (cần giải quyết gì?)
Bài toán có 3 phần kỹ thuật chính:

### 2.1 Drift monitoring: drift là gì và phát hiện bằng cách nào?
Trong lab này có 2 loại drift được dùng:
- **Data drift**: thay đổi phân phối đầu vào feature giữa `baseline` và `current`.
- **Performance/concept drift (proxy)**: khi có nhãn `anomaly_label`, so sánh precision/recall của model theo thời gian.

Vì concept drift có thể không quan sát trực tiếp từ feature, lab dùng **precision suy giảm** như một proxy: precision giảm đồng nghĩa mô hình ngày càng kém trong việc phát hiện anomaly đúng.

### 2.2 Drift trigger: điều kiện nào “xác nhận drift” để bắt đầu retrain?
`drift_detector.py` trả về drift score/flag (và có thể thêm performance degradation) để `retrain.py` ra quyết định.

### 2.3 Retrain & deploy safety: versioning, approval, rollback
- Muốn thay model trong production mà không downtime → dùng **MLflow model registry alias** (production/staging/archived) + endpoint `/reload`.
- Muốn tránh thay “mù” → có **human approval gate** sau khi model mới được set alias `staging`.
- Muốn an toàn sau khi deploy → monitor precision trong 24 cycles; nếu tụt dưới ngưỡng → auto-rollback.

---

## 3) Solution cần thực hiện (giải pháp end-to-end)

### 3.1 Pipeline tổng quan (flow chạy theo vòng đời)
**Input dữ liệu**
- `data/baseline.csv`: reference distribution (dữ liệu bình thường cũ).
- `data/drifted.csv`: production window có drift.
- `data/holdout.csv`: dữ liệu cũ để kiểm tra v2 có overfit drift window không.
- `data/post_deploy_eval.csv`: dữ liệu sau deploy có nhãn để monitor quality.

**Các bước chính**
1. `pipeline.py`: train v1 trên baseline, log lên MLflow, đăng ký model `anomaly-detector`, gán alias `production`.
2. `serve.py`: FastAPI load model theo alias `models:/anomaly-detector@production`, cung cấp:
   - `POST /predict`
   - `GET /health/active-version`
   - `POST /reload` (reload model sau khi alias swap)
3. `drift_detector.py`: so baseline vs current để lấy drift score và/hoặc precision.
4. `retrain.py`: orchestrator
   - nếu drift flag bật → train v2 trên sliding window (`baseline + drifted`)
   - đăng ký v2 và gán alias `staging`
   - chờ approval gate (trừ khi `--auto-approve`)
   - nếu approve → swap `staging` → `production` + `/reload`
   - monitor post-deploy và auto-rollback nếu precision < 0.65.

---

## 4) Drift detection chi tiết (điều kiện “drift được xác nhận”) 
Trong `sample-solution/drift_detector.py`:

### 4.1 Data drift
- `drift_score = share_of_drifted_columns` từ Evidently `DataDriftPreset`.
- Drift flag (data) khi:
  - **`drift_score > threshold`**
- Mặc định trong lab:
  - `DEFAULT_THRESHOLD = 0.15`
  - ⇒ Drift khi **`drift_score > 0.15`**

### 4.2 Performance drift (proxy bằng precision)
- Khi có nhãn `anomaly_label`, tính precision của model hiện tại.
- Degraded flag (performance) khi:
  - **`precision < perf_threshold`**
- Mặc định:
  - `DEFAULT_PERF_THRESHOLD = 0.70`

### 4.3 Combined mode
- Khi chạy `--check-mode combined`:
  - drift flag được kích hoạt nếu:
    - **(data drift) OR (performance degraded)**
  - ⇒ Drift nếu:
    - **`(drift_score > 0.15) OR (precision < 0.70)`**

> Ý nghĩa MLOps: “drift được xác nhận” chính là tín hiệu kích hoạt `retrain.py` train model v2.

---

## 5) Mô tả module (dễ hiểu)

### 5.1 `sample-solution/pipeline.py`
Train IsolationForest v1 trên `baseline.csv`, log params/metrics/artifact lên MLflow, đăng ký vào model registry dưới tên `anomaly-detector`, và set alias `production`.

### 5.2 `sample-solution/serve.py`
Serve model từ alias cố định `models:/anomaly-detector@production`. Khi alias `production` đổi sang v2, dùng `POST /reload` để load model mới mà không cần redeploy.

### 5.3 `sample-solution/drift_detector.py`
Dùng Evidently DataDriftPreset để lấy drift score và dùng nhãn `anomaly_label` (khi có) để đo precision/recall. Kết quả trả về dùng để quyết định drift flag.

### 5.4 `sample-solution/retrain.py`
Orchestrator:
- detect drift → nếu drift train v2 (sliding window) → đăng ký + set alias `staging` → approval gate → promote `staging` → `production` → serve reload → post-deploy monitoring → auto-rollback nếu precision giảm.

---

## 6) Tóm tắt đóng vai trò business + kỹ thuật (một đoạn)
Production đã thay đổi nên model v1 gây sai lệch trong phát hiện anomaly. Do đó hệ thống cần drift monitoring để quyết định khi nào phải retrain. Trong lab, drift được xác nhận dựa trên (data drift bằng drift score) và/hoặc (performance degradation bằng precision). Khi drift được xác nhận, pipeline huấn luyện model v2, đưa qua staging, chờ approval rồi swap vào production theo blue-green alias và serve reload. Sau deploy, pipeline theo dõi precision để bảo vệ chất lượng; nếu v2 suy giảm vượt ngưỡng thì tự rollback về v1 để giữ độ tin cậy hệ thống.

---

## 7) Ghi chú cho phần DESIGN.md và SUBMIT.md (liên kết với spec)
- `DESIGN.md` cần trả lời vì sao chọn ngưỡng drift/performance và giải thích tương ứng với logic kích hoạt retrain.
- `DESIGN.md`/`SUBMIT.md` cần nhấn mạnh drift monitoring là để **trigger retrain có kiểm soát**, không chỉ để hiển thị dashboard.
- Phần drift trigger phải mô tả đúng điều kiện: combined = data drift OR precision degradation.

