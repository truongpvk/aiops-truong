# SUBMIT.md — Reflection

---

## 1. Drift threshold: lựa chọn và validation

Tôi đã chọn ngưỡng drift **0.15** (share of drifted columns). Để validation, tôi chạy `drift_detector.py` với `--check-mode data` trên baseline data tự split 70/30 và thu được drift score ~0.03–0.05 trong điều kiện "no drift". Sau đó chạy trên `drifted.csv` và đo được score ~0.67 — vượt ngưỡng rõ ràng. Ngưỡng 0.15 = baseline_score × 3 tạo ra safety margin đủ lớn để tránh false positive do noise, nhưng vẫn đủ nhạy để phát hiện drift thực tế như trong lab.

---

## 2. Xử lý khi v2 sau retrain kém hơn v1

Nếu v2 underperform, pipeline có hai lớp bảo vệ. Thứ nhất, trước khi promote, `retrain.py` chạy holdout validation in ra `Holdout validation — v2 precision: X.XXXX  recall: X.XXXX` và cảnh báo nếu v2 precision thấp hơn v1 — operator có thể từ chối promote tại approval gate. Thứ hai, sau khi promote, pipeline chạy post-deploy monitoring trong 24 polling cycles trên `post_deploy_eval.csv`; nếu precision < 0.65 trong bất kỳ cycle nào, `retrain.py` tự động gán lại alias `@production` về v1 và gán v2 sang `@archived`, ghi event `auto_rollback_v2_to_v1` vào `outputs/audit_log.jsonl`, và gọi `POST /reload` để serve.py nạp lại v1 — không có downtime.

---

## 3. Data drift vs concept drift — và Evidently detect cái gì

**Data drift** là khi phân phối đầu vào P(X) thay đổi — ví dụ `latency_p99` tăng từ mean 120ms lên 156ms. Evidently `DataDriftPreset` sử dụng thống kê như Jensen-Shannon divergence hoặc Wasserstein distance để so sánh phân phối feature giữa reference và current — đây chính là data drift detection. **Concept drift** là khi ánh xạ P(Y|X) thay đổi — cùng input features nhưng ground truth label thay đổi; trong `drifted.csv` có 25% label bị flip. Evidently mặc định **không** phát hiện concept drift vì nó không nhìn vào label — đó là lý do phải dùng `--check-mode combined` kết hợp thêm performance check (precision/recall) dựa trên `anomaly_label` để bắt được cả hai loại drift.

---

## 4. Blue-green swap quan trọng hơn replace file trực tiếp

Blue-green swap dùng MLflow Registry alias (`@production`, `@staging`) thay vì ghi đè file model. Khi `serve.py` nhận lệnh `POST /reload`, nó gọi `mlflow.pyfunc.load_model("models:/anomaly-detector@production")` — nếu alias đã trỏ sang v2 thì v2 được load, nếu rollback thì alias trỏ về v1 và v1 được load lại. Quan trọng hơn, trong quá trình chuyển đổi alias không có thời gian downtime vì alias swap trong registry là atomic — trong khi ghi đè file trực tiếp có thể dẫn đến race condition nếu có request đến đúng lúc file đang được overwrite. Ngoài ra, alias cho phép rollback tức thời chỉ bằng một lệnh `set_registered_model_alias` mà không cần giữ backup file riêng.

---

## 5. Tự động hóa approval gate: metric và threshold

Nếu phải tự động hóa approval gate (không cần human), tôi sẽ dùng **precision trên holdout set** như điều kiện promote. Cụ thể: v2 được tự động promote nếu `v2_holdout_precision >= v1_holdout_precision * 0.98` — tức v2 không được tệ hơn v1 quá 2 điểm percentage trên tập dữ liệu cũ (holdout.csv). Thêm vào đó có thể kết hợp điều kiện `drift_score > threshold` AND `v2_precision >= 0.85` để đảm bảo chỉ promote khi v2 đủ chất lượng tuyệt đối chứ không chỉ tương đối so với v1. Nếu cả hai điều kiện thỏa mãn, pipeline sẽ tự động promote và ghi log audit event `auto_promoted` — giữ nguyên lớp post-deploy monitoring và auto-rollback để làm safety net sau promotion.
