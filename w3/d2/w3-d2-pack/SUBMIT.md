# W3-D2 Submission — Truong PVK

## 3 things I learned about my AIOps pipeline

1. **Temporal causality là yếu tố quan trọng nhất trong RCA.** Khi inject fault, service bị inject LUÔN alert trước các downstream services. Bằng cách ưu tiên "service nào alert sớm nhất", RCA chính xác hơn nhiều so với "service nào alert nhiều nhất" (anti-pattern §7.3). Trong 8 experiments detected, 6 lần RCA đúng nhờ temporal analysis. 2 lần sai vì lateral services không nằm trong topology graph.

2. **Meta-monitoring là lỗ hổng lớn nhất.** Pipeline hoàn toàn miss 2/10 experiments (clock skew trên auth-svc và disk fill trên log-collector) vì không monitor infra-layer metrics. Đây chính là failure mode §7.5 (Roblox 2021) — khi monitoring infrastructure bị ảnh hưởng, pipeline mù hoàn toàn. Cần một observability stack độc lập cho chính pipeline.

3. **Retry storm amplification đánh lừa RCA.** Experiment 10 (HTTP 500 inject) tạo retry storm khiến checkout-svc (symptom carrier) tạo nhiều alerts hơn root cause. Dù đã implement penalty cho noisy services, penalty weight 0.15 quá nhẹ. Bài học: cần Granger causality test (service nào drift TRƯỚC) kết hợp topology (upstream vs downstream) thay vì chỉ dựa vào scoring heuristic.

## 1 fault I expected the pipeline to catch but it missed

- **Experiment:** #7 — log_collector_disk_fill (disk fill 95% trên log-collector)
- **Why I expected detection:** log-collector chạy trong cùng Docker network, Prometheus scrape metrics từ nó. Khi disk đầy 95%, tôi kỳ vọng Prometheus detect memory hoặc error metric tăng.
- **Why the pipeline missed (hypothesis):** Pipeline chỉ focus vào application-tier metrics (http_request_duration, http_errors_total). Không có metric nào cho disk usage trên log-collector vì mock service chỉ expose HTTP metrics, không expose node_exporter metrics (disk, filesystem). Thêm vào đó, log-collector không nằm trong request path → probe pass-rate không bị ảnh hưởng → detector không trigger. Đây chính xác là failure mode §7.5: monitoring stack phụ thuộc vào service đang monitor.

## 1 trade-off in pipeline design I want to rethink

**Trade-off: Statistical threshold-based detection vs. Machine Learning anomaly detection.**

Hiện tại pipeline dùng fixed thresholds (p99 > 300ms, error_rate > 5%, v.v.) và relative comparison (3x baseline). Ưu điểm: simple, fast, explainable, không cần training data. Nhược điểm: không adapt theo pattern (peak hours vs off-peak), miss subtle anomalies (experiment 6 — clock skew không breach bất kỳ threshold nào).

Tôi muốn rethink sang hybrid approach: giữ threshold-based cho fast detection (latency, availability), nhưng thêm ML-based detection (Isolation Forest hoặc DBSCAN) cho subtle/novel anomalies. ML model train trên baseline window, detect anomalies mà threshold miss. Trade-off là: ML thêm complexity, cần training pipeline, có thể increase false positives (§7.1 noise floor problem). Nhưng với 2 experiments missed (20% recall loss), việc improve detection coverage quan trọng hơn risk of thêm FP.

## Scoreboard summary

- **detected:** 8/10
- **rca_correct:** 6/8
- **mttd_p50:** 25s
- **false_alarms:** 0
- **verdict:** PASS ✓ (detected ≥ 7/10, RCA correct ≥ 5/detected, FA ≤ 1)
