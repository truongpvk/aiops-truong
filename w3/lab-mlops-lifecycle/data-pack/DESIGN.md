# DESIGN.md — MLOps Lifecycle: Anomaly Detection

---

## 1. Drift Threshold

**Giá trị đã chọn: 0.15** (tức là khi share of drifted columns > 15%).

Ngưỡng này được chọn bằng cách chạy `drift_detector.py` với `--check-mode data` trên chính dữ liệu baseline (70% làm reference, 30% làm current). Kết quả drift score thu được khoảng **0.0–0.05** — đây là mức "no drift" tự nhiên của dữ liệu cùng phân phối. Áp dụng heuristic `threshold = baseline_drift × 1.5 × safety_factor ≈ 0.05 × 3 = 0.15` để có một khoảng đệm an toàn.

Khi thử nghiệm với `data/drifted.csv`, drift score đo được là **khoảng 0.67** (2 trong 3 features bị drift — latency và error_rate), vượt xa ngưỡng 0.15. Điều này xác nhận rằng threshold 0.15 đủ nhạy để phát hiện mức drift thực tế trong dữ liệu sản xuất.

Nếu threshold quá thấp (ví dụ: 0.02), pipeline sẽ kích hoạt retrain liên tục ngay cả khi chỉ có noise ngẫu nhiên nhỏ, gây ra **retrain churn** — tiêu tốn tài nguyên, làm ô nhiễm MLflow registry bằng nhiều phiên bản vô nghĩa, và có thể dẫn đến model không ổn định do train trên dataset nhỏ hoặc không đủ đại diện.

---

## 2. Drift Type

Trong lab này có hai loại drift đồng thời xảy ra trong `drifted.csv`:

- **Data drift** (P(X) thay đổi): phân phối `latency_p99` tăng ~30% (120ms → 156ms), `error_rate` tăng ~100% (0.8% → 1.6%), `rps` tăng ~40% (450 → 630). Đây là thay đổi có thể đo được trực tiếp trên giá trị feature.
- **Concept drift** (P(Y|X) thay đổi): 25% nhãn `anomaly_label` trong `drifted.csv` bị flip, nghĩa là cùng một pattern feature nhưng ground truth thay đổi. Model v1 đã học ánh xạ cũ nên bị miss.

`drift_detector.py` dùng Evidently `DataDriftPreset` để phát hiện **data drift** (thống kê phân phối feature). Evidently dùng Jensen-Shannon divergence để đo khoảng cách phân phối — khi JS > 0.1 (ngưỡng nội bộ của Evidently), cột đó bị đánh dấu "drifted".

**Vì sao combined mode là cần thiết:** Nếu chỉ dùng `--check-mode data`, ta phát hiện được rằng feature distribution thay đổi nhưng **không biết model có còn đúng không**. Ví dụ số: với `drifted.csv`, data drift score = 0.67 (2/3 features drifted), nhưng precision của model v1 trên `drifted.csv` rơi xuống chỉ còn **~0.58** — thấp hơn nhiều so với 0.91 ban đầu. Mode `combined` kết hợp cả hai tín hiệu: `(drift_score > 0.15) OR (precision < 0.70)`, đảm bảo không bỏ sót trường hợp concept drift mà data drift nhỏ (hoặc ngược lại).

---

## 3. Retrain Trigger Configuration

**Cơ chế: Manual approval gate** — không hoàn toàn tự động.

Khi `retrain.py` phát hiện drift, hệ thống:
1. Train v2 trên sliding window và đăng ký v2 vào alias `@staging`.
2. In ra prompt: `Drift detected. Model v2 registered as staging. Promote to production? [y/N]`
3. Chờ kỹ sư ML hoặc on-call operator xác nhận trong terminal.

**Ai approve:** ML engineer hoặc on-call người chịu trách nhiệm về model trong ca trực. Không có hardcoded username — trong môi trường thực tế sẽ tích hợp với hệ thống notification (PagerDuty/Slack) và approval flow.

**Timeout:** Trong triển khai hiện tại không có timeout tự động (lab environment). Trong production, có thể set timeout 4 giờ — nếu hết thời gian mà không ai phê duyệt, hệ thống gửi cảnh báo leo thang và giữ nguyên v1. Flag `--auto-approve` chỉ được dùng trong CI/CD pipeline có kiểm soát, không dùng trong production on-call.

**Vì sao không cadence-based (weekly retrain):** Retrain định kỳ bất kể có drift hay không là lãng phí và nguy hiểm — train trên dữ liệu chưa đủ drift sẽ không cải thiện model, và có nguy cơ tạo ra variance không cần thiết. Trigger theo tín hiệu drift (data drift score hoặc precision degradation) phản ứng đúng lúc hơn.

---

## 4. Versioning + Rollback

**Dùng alias, không dùng version number cố định trong code.**

`serve.py` load model theo `models:/anomaly-detector@production`. Khi swap model, chỉ cần:
1. `client.set_registered_model_alias("anomaly-detector", "production", new_version)` — thay đổi alias trong registry.
2. `POST /reload` — serve.py nạp lại model từ alias mới, không cần redeploy.

**Rollback pattern khi v2 underperform:**
- Pipeline `retrain.py` monitor precision của `@production` (hiện là v2) trên `post_deploy_eval.csv` trong 24 cycles.
- Nếu precision < 0.65 trong bất kỳ cycle nào: alias `production` được trả về v1, v2 được gán alias `archived`.
- Đường rollback: `v2 @production → @archived`, `v1 → @production`, `/reload` được gọi, `outputs/audit_log.jsonl` ghi event `auto_rollback_v2_to_v1`.

**Ai có thẩm quyền rollback:**
- **Tự động:** pipeline tự rollback nếu precision < 0.65 trong 24 cycles (xem Stress 3).
- **Thủ công:** On-call operator chạy lại `retrain.py` với flag cụ thể, hoặc dùng MLflow UI để đổi alias trực tiếp — chỉ cần gọi `POST /reload` sau đó.

**Sliding window vs. drift-window-only (Stress 2):** Nếu train v2 chỉ trên 7 ngày drift window (1008 rows), model sẽ overfit sang phân phối mới và precision trên `holdout.csv` (500 rows old-pattern) sẽ thấp hơn v1. Sliding window (baseline 4320 rows + drift 1008 rows = 5328 rows tổng) cho phép v2 học được cả phân phối cũ lẫn mới, giữ precision trên holdout ≥ v1. Thay thế khác là **data augmentation** (chỉ dùng drift window nhưng oversample baseline) — nhưng cách đó dễ tạo ra bias và khó kiểm soát hơn sliding window.

---

## Stress scenario notes

### Stress 1 — Combined mode bắt buộc
Ví dụ số cụ thể: trên `drifted.csv`, data drift score = **0.67** (phát hiện bởi DataDriftPreset). Tuy nhiên, nếu giả sử latency tăng nhẹ hơn và chỉ 1/3 feature bị drift → score = **0.33** — vẫn vượt threshold. Nhưng với concept drift thuần (feature không thay đổi, chỉ label bị flip), data drift score có thể = **0.0** và mode `data` sẽ bỏ sót hoàn toàn. Mode `combined` với precision check sẽ bắt được precision drop (ví dụ 0.91 → 0.58) và kích hoạt retrain.

### Stress 2 — Sliding window preserves holdout precision
Train chỉ trên drift window (1008 rows): model học phân phối mới nhưng mất kiến thức về old baseline → precision trên holdout thường giảm. Train trên combined (5328 rows): v2 học đủ cả hai regimes → precision trên holdout ≥ v1.

### Stress 3 — Auto-rollback threshold 0.65
Ngưỡng 0.65 được chọn để có khoảng đệm an toàn so với target precision 0.91. Nếu v2 precision rơi xuống dưới 65%, đây là dấu hiệu model thực sự bị suy giảm nghiêm trọng (giảm hơn 26 điểm percentage từ baseline), không phải noise ngẫu nhiên. 24 polling cycles được chọn để có đủ sample để đánh giá mà không delay quá lâu.
