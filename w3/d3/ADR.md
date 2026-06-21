# ADR-001: Ensemble anomaly detection (3σ + Isolation Forest + deployment correlation) thay vì single threshold

## Status

Accepted

## Context

AIOps pipeline hiện tại sử dụng single-threshold detector cho mỗi metric (ví dụ: latency > 500ms → alert, error_rate > 5% → alert). Trong quá trình reproduce outage Cloudflare WAF regex 2019 (§9.4), pipeline phát hiện được incident nhưng bộc lộ 2 gaps nghiêm trọng:

1. **Gap GAP-1 (application-layer visibility):** Pipeline không xác định được NGUYÊN NHÂN CPU saturation — chỉ thấy symptom "CPU 98.5%" mà không biết tại sao. Single threshold chỉ trả lời "metric vượt ngưỡng" chứ không phân biệt được CPU spike do regex backtracking vs CPU spike do traffic tăng vs CPU spike do memory pressure.

2. **Gap GAP-2 (deployment correlation):** Pipeline không tích hợp deployment events. Detection latency 25 giây thay vì potential <5 giây nếu correlate config push timing với anomaly onset.

Ngoài ra, từ chaos engineering (W3-D2), experiment 6 (clock skew) và experiment 7 (disk fill) hoàn toàn bị miss vì single threshold chỉ cover một loại metric. Pipeline cần multi-signal approach để cover nhiều failure mode hơn.

**Forces at play:**
- Detection accuracy (precision + recall) cần cao hơn để giảm MTTR
- Compute cost phải chấp nhận được — không thể chạy model nặng cho mỗi metric mỗi 5 giây
- Operational complexity — team nhỏ, không thể maintain quá nhiều model riêng biệt
- False alarm rate — quá nhiều false alarm → alert fatigue → on-call ignore alerts

## Decision

Thay thế single-threshold detector bằng **ensemble detector** gồm 3 tầng:

1. **Statistical detector (3σ / z-score):** Lightweight, chạy trên mọi metric mỗi scrape cycle (5s). Flag anomaly khi giá trị vượt 3 standard deviations so với rolling baseline (1h window). Chi phí thấp, recall cao, precision thấp (dùng làm first-pass filter).

2. **Isolation Forest (IF):** Chạy trên multi-dimensional feature vector (latency + error_rate + CPU + memory + request_rate) mỗi 30 giây. Phát hiện outlier không cần predefined threshold — tốt cho failure mode chưa biết trước (ví dụ: clock skew gây subtle pattern). Chi phí trung bình.

3. **Deployment correlation detector:** Subscribe deployment/config-push events. Khi anomaly được phát hiện bởi tầng 1 hoặc 2, check xem có deployment event nào xảy ra trong window 5 phút trước đó → nếu có, tự động boost severity và tag "likely deployment-triggered". Giải quyết trực tiếp Gap GAP-2.

**Ensemble voting rule:** Alert khi ≥2/3 detectors đồng thuận, HOẶC khi 3σ + deployment correlation cùng fire (deployment-triggered anomaly = high confidence ngay cả chỉ với 2 signal).

## Alternatives considered

### Alternative A: Giữ nguyên single threshold, tune thresholds tốt hơn

**Pros:**
- Đơn giản nhất — không cần thêm code hay infra
- Dễ hiểu, dễ debug khi alert fire sai
- Zero compute overhead bổ sung

**Cons:**
- Không thể detect failure modes chưa biết trước (ví dụ: clock skew, regex backtracking)
- Threshold phải tune manually cho từng metric × từng service → O(N×M) config burden
- Không có deployment correlation → Gap GAP-2 vẫn tồn tại
- Precision thấp vì threshold cố định không adapt theo traffic pattern (rush hour vs off-peak)

**Why rejected:** Không giải quyết được 2 gaps đã identify trong reproduction. Scaling lên 100+ services sẽ không khả thi vì phải maintain hàng trăm threshold configs.

### Alternative B: LSTM Autoencoder (deep learning anomaly detection)

**Pros:**
- Cao nhất về khả năng detect complex temporal patterns
- Tự learn normal behavior — không cần manual threshold
- Có thể detect subtle anomaly mà statistical methods miss (ví dụ: gradually increasing latency over hours)

**Cons:**
- Compute cost cao: cần GPU inference ($3.06/h cho g5.xlarge) — tăng AIOps monthly cost ~$2,200/tháng
- Training time: cần ≥2 tuần data sạch để train — cold start problem cho new services
- Operational burden: model drift, retraining schedule, GPU infra management
- Black-box: khó explain tại sao model flagged anomaly → on-call khó trust → alert fatigue
- Latency: inference time 200-500ms vs <1ms cho 3σ → không đáp ứng real-time detection target <5s

**Why rejected:** Compute cost không justify cho team nhỏ (<30 services). Thêm vào đó, black-box nature làm giảm trust từ on-call team — khi model flag anomaly nhưng không explain được, SRE thường ignore. LSTM-AE phù hợp hơn cho giai đoạn sau khi ensemble đã ổn định và team có đủ data + GPU budget.

## Consequences

### Positive
- **Recall tăng:** Ensemble cover nhiều failure mode hơn. 3σ bắt threshold-based anomaly. IF bắt multivariate outlier (ví dụ: clock skew gây subtle correlation change). Deployment correlation bắt config-triggered incident. Dự kiến recall tăng từ 0.80 (hiện tại) lên ~0.90+.
- **Detection latency giảm:** Deployment correlation detector giúp identify deployment-triggered incidents trong <5 giây thay vì 25 giây (hiện tại). Giải quyết trực tiếp Gap GAP-2 từ Cloudflare reproduction.
- **Graceful degradation:** Nếu một detector component fail, 2 còn lại vẫn hoạt động. Ensemble resilient hơn single detector.

### Negative (trade-offs accepted)
- **Compute cost tăng ~40%:** IF model chạy mỗi 30 giây tiêu tốn CPU bổ sung. Ước tính: +$200-400/tháng cho <100 services. Chấp nhận được vì vẫn thấp hơn LSTM-AE rất nhiều.
- **Operational complexity tăng:** 3 detector components thay vì 1. Cần monitor health của mỗi detector (meta-monitoring). Cần tune ensemble voting weights theo environment. Mitigation: document runbook rõ ràng, implement health dashboard cho detector pipeline.
- **Risk — false alarm rate có thể tăng trong giai đoạn đầu:** Ensemble mới có thể fire nhiều alert hơn đến khi voting weights được calibrate. Mitigation: deploy với "shadow mode" (alert nhưng không page) trong 2 tuần đầu, tune weights dựa trên historical data.
