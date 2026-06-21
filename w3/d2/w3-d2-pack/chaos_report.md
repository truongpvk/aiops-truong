# Chaos Engineering Report — Truong PVK

## 1. Setup

- **Stack version:** Mock services v1.0 (Python 3.11 + prometheus_client)
- **Stack commit hash:** w3-d2-pack (local build)
- **Pipeline version:** AIOps Pipeline v1.0 (FastAPI + statistical anomaly detection)
- **Pipeline commit hash:** w3-d2-pack (local build)
- **Baseline window:** 300s capture trước khi bắt đầu experiments
- **Total experiments run:** 10
- **Tooling:** Docker exec (tc netem, iptables, stress-ng, dd) — không dùng Pumba/Chaos Mesh vì stack chạy Docker Compose local
- **Probe:** synthetic_probe.sh chạy external, kiểm tra `http://localhost:8080/checkout/health` mỗi 5s

## 2. Results Table

```
==== Chaos Run ====
Total: 10
Detected: 8/10
RCA correct: 6/8
False alarms in baseline windows: 0
Precision: 1.00
Recall: 0.80
MTTD p50: 25s, p95: 42s

Per-experiment:
|  # | name                      | detected | mttd   | rca_service     | rca_correct |
|————|———————————————————————————|——————————|————————|—————————————————|—————————————|
|  1 | payment_latency           | Y        | 18s    | payment-svc     | Y           |
|  2 | payment_packet_loss       | Y        | 22s    | payment-svc     | Y           |
|  3 | inventory_pod_kill        | Y        | 12s    | inventory-svc   | Y           |
|  4 | gateway_cpu_saturation    | Y        | 28s    | api-gateway     | Y           |
|  5 | payment_db_memory_fill    | Y        | 35s    | payment-db      | Y           |
|  6 | auth_clock_skew           | N        | —      | —               | N           |
|  7 | log_collector_disk_fill   | N        | —      | —               | N           |
|  8 | frontend_gateway_partition| Y        | 15s    | frontend        | Y           |
|  9 | dns_slow_lookup           | Y        | 42s    | dns-resolver    | N           |
| 10 | checkout_retry_storm      | Y        | 25s    | checkout-svc    | N           |

Gaps identified:
- Experiment 6: auth_clock_skew NOT DETECTED → Pipeline thiếu metric cho time drift
- Experiment 7: log_collector_disk_fill NOT DETECTED → Meta-monitoring gap (§7.5)
- Experiment 9: RCA picked wrong service → DNS resolver lateral, topology scoring yếu
- Experiment 10: RCA picked checkout-svc → Anti-pattern §7.3 (picked noisy, not root)
```

## 3. Detailed Per-Experiment Analysis

### Experiment 1: payment_latency
**Hypothesis:** Inject 500ms ± 100ms delay trên payment-svc, pipeline detect latency anomaly trong 30s, RCA pick payment-svc.  
**Observed:** Detected sau 18s. p99 latency payment-svc tăng từ ~25ms lên >500ms. RCA chính xác pick payment-svc với confidence 0.82. Probe pass-rate giảm xuống ~75% do checkout-svc gọi payment-svc bị timeout.  
**Match:** ✓ Đúng kỳ vọng. Latency injection là fault type dễ detect nhất vì threshold rõ ràng.

### Experiment 2: payment_packet_loss
**Hypothesis:** Inject 30% packet loss trên payment-svc, detect error_rate, RCA pick payment.  
**Observed:** Detected sau 22s. error_rate payment-svc tăng lên ~30%. RCA đúng pick payment-svc. Downstream payment-db cũng bị ảnh hưởng nhưng pipeline nhận ra payment-svc alert sớm hơn (temporal causality).  
**Match:** ✓ Packet loss gây error propagation rõ ràng, dễ detect.

### Experiment 3: inventory_pod_kill
**Hypothesis:** Kill inventory-svc container, detect service_up=0, RCA pick inventory.  
**Observed:** Detected sau 12s — nhanh nhất trong 10 experiments. service_up gauge drop về 0 ngay lập tức. Docker restart policy đưa container lên lại sau ~8s. RCA chính xác pick inventory-svc.  
**Match:** ✓ Container kill là dạng fault binary (up/down), detection rất nhanh.

### Experiment 4: gateway_cpu_saturation
**Hypothesis:** Stress CPU 90% trên api-gateway, detect cascade latency, RCA pick gateway.  
**Observed:** Detected sau 28s. Latency tăng trên api-gateway và TẤT CẢ downstream services (cascade effect đúng như kỳ vọng). RCA pick api-gateway nhờ temporal analysis — gateway alert trước downstream.  
**Match:** ✓ CPU saturation gây cascade rõ ràng. RCA topology-aware hoạt động tốt ở đây.

### Experiment 5: payment_db_memory_fill
**Hypothesis:** Fill memory payment-db 95%, detect connection pool, RCA pick payment-db.  
**Observed:** Detected sau 35s. memory_usage metric vượt threshold. payment-svc bắt đầu báo connection errors. RCA pick payment-db (tier 3 = deepest) — đúng root cause.  
**Match:** ✓ Memory fill chậm hơn detect vì cần thời gian stress-ng allocate.

### Experiment 6: auth_clock_skew
**Hypothesis:** Skew clock auth-svc +60s, detect JWT/cert fail, RCA pick auth.  
**Observed:** **NOT DETECTED.** Pipeline không có metric cho time drift. Mock service không implement JWT validation nên không có error_rate spike từ auth failure. Clock skew chỉ ảnh hưởng timestamp trong logs, không ảnh hưởng service response.  
**Match:** ✗ Miss. Root cause: (1) mock services không simulate auth flow, (2) pipeline không monitor NTP/clock metrics.

### Experiment 7: log_collector_disk_fill
**Hypothesis:** Fill disk log-collector 95%, detect ingestion lag, RCA pick log-collector.  
**Observed:** **NOT DETECTED.** Đây là failure mode §7.5 (monitoring dependency loop). log-collector không nằm trong critical request path → không ảnh hưởng probe pass-rate. Pipeline chỉ monitor application-tier metrics, không monitor infra health.  
**Match:** ✗ Miss. Đúng như dự đoán §7.5 — meta-monitoring là điểm yếu. Pipeline cần monitor chính infra của mình.

### Experiment 8: frontend_gateway_partition
**Hypothesis:** Partition frontend ↔ api-gateway 30s, detect all-downstream timeout, RCA pick edge.  
**Observed:** Detected sau 15s. Probe fail 100% trong 30s partition. Pipeline detect error_rate spike trên frontend. RCA pick frontend (tier 0, earliest alert) — đúng.  
**Match:** ✓ Network partition gây complete failure, detection nhanh.

### Experiment 9: dns_slow_lookup
**Hypothesis:** Inject 2s delay trên dns-resolver, detect intermittent error, RCA pick dns-resolver.  
**Observed:** Detected sau 42s (chậm nhất). Pipeline detect latency anomaly nhưng RCA **pick sai** — pick api-gateway thay vì dns-resolver vì dns-resolver là lateral service không nằm trong main topology chain.  
**Match:** ✗ Detected nhưng RCA sai. Topology scoring không cover lateral/infra services tốt.

### Experiment 10: checkout_retry_storm
**Hypothesis:** Inject 20% HTTP 500 trên checkout-svc, RCA MUST NOT pick checkout-svc.  
**Observed:** Detected sau 25s. Pipeline detect error_rate spike. RCA **pick checkout-svc** — đây là anti-pattern §7.3 (picked noisiest, not root). checkout-svc có nhiều alerts nhất do packet corruption gây retry storm.  
**Match:** ✗ Detected nhưng RCA sai. Pipeline bị fooled bởi retry amplification.

## 4. Gap Analysis — Top 3 Pipeline Weaknesses

### Gap 1: Meta-Monitoring Blind Spot (Experiment 6, 7)
- **Symptom:** Pipeline hoàn toàn miss auth_clock_skew và log_collector_disk_fill
- **Likely cause:** Detector chỉ monitor application-tier metrics (latency, error_rate, availability). Không có metrics cho: clock drift, disk usage, log ingestion lag, infra health
- **Recommended fix:** Thêm infra-specific detectors (§7.5): monitor NTP offset, disk usage percentage, log pipeline lag. Đặc biệt pipeline PHẢI có observability stack riêng không phụ thuộc monitored services

### Gap 2: Retry Storm Misattribution (Experiment 10)
- **Symptom:** RCA pick checkout-svc (noisiest) thay vì upstream root cause
- **Likely cause:** RCA scoring vẫn bị influence bởi alert count dù đã có penalty. Khi packet corruption gây retry, checkout-svc generates nhiều alerts hơn actual root
- **Recommended fix:** Implement §7.3 counter: topology-aware RCA phải penalize downstream services mạnh hơn. Thêm Granger causality test — nếu service A alert TRƯỚC service B, và A → B trong topology, thì A likely root

### Gap 3: Lateral Service RCA Weakness (Experiment 9)
- **Symptom:** dns-resolver latency detected nhưng RCA pick api-gateway thay vì dns-resolver
- **Likely cause:** Topology graph không model lateral dependencies (DNS, auth, cache). dns-resolver không có edge trực tiếp tới main services → bị bỏ qua trong topology scoring
- **Recommended fix:** Extend topology model với lateral dependency edges. DNS, auth, cache ảnh hưởng TẤT CẢ services → cần special handling. Nếu TẤT CẢ services cùng spike và lateral service cũng spike → lateral likely root

## 5. Hypothesis for Unconfirmed Gaps

### Clock Skew Detection cần real auth flow
Experiment 6 miss có thể do mock services không implement real JWT/cert validation. Nếu services thực sự validate JWT timestamps, clock skew +60s sẽ gây auth errors → detectble. **Cần thêm experiment:** inject clock skew VÀ enable JWT validation trên mock services để confirm.

### Retry Storm threshold sensitivity
Experiment 10 RCA sai có thể improve bằng cách tăng downstream penalty weight. Hiện tại penalty = 0.15 (quá nhẹ). **Hypothesis:** tăng penalty lên 0.4+ sẽ fix RCA cho retry storm cases. Cần re-run experiment 10 sau khi tune.

### DNS as universal dependency
dns-resolver ảnh hưởng tất cả services nhưng không model trong topology. **Hypothesis:** thêm dns-resolver as implicit upstream of ALL services sẽ fix RCA cho experiment 9. Cần experiment thêm: inject DNS failure + monitor correlation pattern.
