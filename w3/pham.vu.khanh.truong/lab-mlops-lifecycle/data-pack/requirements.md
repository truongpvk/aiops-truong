# requirements.md

## Mô tả yêu cầu từ HANDOUT

Bạn cần xây dựng một pipeline MLOps end-to-end cho bài toán anomaly detection (IsolationForest) theo vòng đời:

1. **Train + Register (pipeline.py)**
   - Train mô hình trên `data/baseline.csv`.
   - Log **params/metrics/artifact** lên MLflow.
   - Đăng ký model trong **MLflow Model Registry** dưới tên `anomaly-detector`.
   - Thiết lập alias **`production`** cho phiên bản v1.

2. **Serve (serve.py)**
   - FastAPI server.
   - Lúc startup tải model từ MLflow registry alias `models:/anomaly-detector@production`.
   - Cung cấp API:
     - `POST /predict` nhận JSON `{features: [...]}` và trả `{prediction: int, score: float, version: str}`.
     - `GET /health/active-version` trả version đang được serve.
     - `POST /reload` reload lại model từ registry (dùng sau khi swap alias).

3. **Drift Monitoring (drift_detector.py)**
   - Dùng **Evidently DataDriftPreset** để tính drift score giữa `baseline` (reference) và `current`.
   - Hàm `detect_drift(reference_df, current_df, threshold)` → trả `score, is_drift, report_path`.
   - Lưu báo cáo HTML Evidently vào `outputs/drift_reports/`.
   - Log drift score lên MLflow để theo dõi xu hướng.

4. **Retrain Orchestrator + Approval Gate (retrain.py)**
   - Load `data/drifted.csv` theo **rolling window** (mặc định 7 ngày gần nhất).
   - Gọi drift detector so với baseline.
   - Nếu drift detected:
     - Train model mới (v2) trên sliding window data.
     - Register v2 với alias **`staging`**.
     - Hiển thị prompt phê duyệt: `Drift detected. Model v2 registered as staging. Promote to production? [y/N]`.
   - Nếu được approve:
     - Promoting `staging` → `production`.
     - Gọi `POST /reload` để serve.py reload model.
   - Log toàn bộ quyết định (decision trail) lên MLflow run (params/metrics/tags).

5. **Blue-green swap / Versioning & Rollback**
   - Dùng **alias** (`production`, `staging`, `archived`) để đảm bảo đường rollback rõ ràng.
   - `serve.py` phụ thuộc vào alias `production` nên không cần thay file trực tiếp.

6. **DESIGN.md (defense)**
   - Bắt buộc trả lời 4 sub-checkpoint, mỗi mục **3–4 câu**, có **con số cụ thể**:
     1) Drift threshold: giá trị, lý do, test trên drifted.csv, hệ quả khi threshold quá thấp.
     2) Drift type: data drift / concept drift / performance drift; Evidently detect cái gì và vì sao phù hợp.
     3) Retrain trigger config: manual hay automatic; ai approve; timeout (nếu manual); nếu cadence—bảo vệ lý do.
     4) Versioning + rollback: dùng alias hay version; rollback trông như thế nào khi v2 underperform; ai có thẩm quyền.

7. **SUBMIT.md (reflection)**
   - 5 câu hỏi, mỗi câu **3–4 câu**, tham chiếu code và số liệu.

## Các acceptance criteria & stress scenarios (cần được xử lý)

- **Acceptance phase 4:** Stress 1 — Drift type misclassification trap
  - `drifted.csv` có cả data drift và concept drift (25% labels flipped).
  - Bắt buộc chạy:
    - `drift_detector.py --check-mode combined --labeled-current data/drifted.csv --model-uri models:/anomaly-detector@production`
  - Output phải in cả:
    - `Drift score` (data)
    - `Perf precision` (performance)
  - DESIGN.md phải giải thích vì sao cần `combined` với ít nhất 1 ví dụ số.

- **Acceptance phase 5:** Stress 2 — Retrain data selection
  - Không được chỉ train v2 trên drift window 7 ngày; cần sliding window (baseline + drift window) để tránh overfit.
  - Bắt buộc chạy:
    - `retrain.py --reference data/baseline.csv --current data/drifted.csv --holdout data/holdout.csv`
  - Output phải có dòng:
    - `Holdout validation — v2 precision: X.XXXX  recall: X.XXXX`
  - Precision v2 phải **≥** precision v1 đo trên cùng holdout.
  - DESIGN.md so sánh sliding window với ít nhất 1 chiến lược khác.

- **Acceptance phase 6:** Stress 3 — Auto-rollback on post-deploy degradation
  - Sau khi promote v2 → `@production`, monitor v2 bằng `data/post_deploy_eval.csv` (200 rows, có nhãn).
  - Nếu precision < 0.65 trong **24** polling cycles:
    - Demote v2 → `@archived`
    - Restore v1 → `@production`.
  - Bắt buộc chạy end-to-end với `--post-deploy-eval data/post_deploy_eval.csv`.
  - Terminal phải in:
    - `post_deploy_monitor Cycle XX/24`
    - Nếu rollback: dòng cuối
      - `Rollback complete. v1 restored to @production. v2 → @archived`
  - `outputs/audit_log.jsonl` phải chứa event `auto_rollback_v2_to_v1` với fields:
    - `demoted_version`, `restored_version`, `trigger_precision`, `cycle`.

## Bảng chia nhỏ các task cần làm

| Task ID | Việc cần làm | Đầu ra mong đợi | Ghi chú/Checklist |
|---|---|---|---|
| T1 | Đọc HANDOUT/README để nắm flow, file bắt buộc | Hiểu yêu cầu end-to-end | Không làm theo sample-solution trước khi hiểu |
| T2 | Implement `pipeline.py` (train + MLflow log + register alias `production`) | Model được đăng ký `anomaly-detector` với `production` | Log params/metrics/artifact đầy đủ |
| T3 | Implement `serve.py` (FastAPI + load model @production + endpoints) | `/predict`, `/health/active-version`, `/reload` hoạt động | `/reload` reload đúng alias mới |
| T4 | Implement `drift_detector.py` (Evidently DataDriftPreset + report + drift score + MLflow) | Drift report HTML + drift score + flag | Hỗ trợ check-mode `data/performance/combined` cho stress |
| T5 | Implement `retrain.py` (drift→train v2→register staging→approve→promote→/reload) | V2 vào `staging` và chỉ promote khi approve | Có decision trail trong MLflow |
| T6 | Sliding window strategy trong retrain (baseline + drift window) | V2 precision trên holdout ≥ v1 | Thỏa acceptance phase 5 |
| T7 | Post-deploy monitoring + Auto-rollback (precision < 0.65 trong 24 cycles) | Audit log `outputs/audit_log.jsonl` + rollback hoàn chỉnh | Thỏa acceptance phase 6 |
| T8 | Viết `DESIGN.md` trả lời 4 sub-checkpoint có số cụ thể | Đủ độ dài + lý do threshold/type/trigger/versioning | Có ví dụ số cho stress 1/6 |
| T9 | Viết `SUBMIT.md` reflection 5 câu | Đủ số câu + tham chiếu code & số | Không chỉ mô tả chung chung |
| T10 | Validation bằng chạy pipeline end-to-end & stress 1–3 | Terminal/output đúng acceptance | Kiểm chứng log/audit log/holdout |

