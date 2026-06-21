# AIOps Mini-Platform Spec — Truong PVK

## 1. Platform overview

AIOps Mini-Platform giám sát một stack web application gồm 3 tầng: **frontend** (RUM events), **API gateway + backend services** (HTTP request/response), và **database** (query success/failure). Platform phục vụ SRE team để phát hiện anomaly, correlate alerts, xác định root cause, và giảm MTTR. Scope: monitoring, detection, correlation, RCA cho môi trường development/staging với khả năng scale lên production.

## 2. SLO definition (from W3-D1)

Tham chiếu: `w3/d1/w3-d1-pack/slo_spec.yaml`

| Service | SLI | SLO Target | Error Budget (30d) | Downtime Equivalent |
|---------|-----|------------|--------------------|--------------------|
| **frontend** | Frontend availability (no JS error, no network error) | 98.50% | 77,760 failures / 5,184,000 total events | 648 phút |
| **api** | API availability (non-5xx, non-429 responses) | 99.50% | 103,689 failures / 20,737,800 total events | 216 phút |
| **db** | DB query success rate | 99.90% | 1,726 failures / 1,726,380 total events | 43 phút |

**Burn rate alerts** được cấu hình tại `w3/d1/w3-d1-pack/burn_rate_alerts.yaml` với multi-window approach (5m/1h cho fast burn, 30m/6h cho slow burn).

## 3. Detection + Correlation + RCA stack (from W1+W2)

**Detection layer:** Statistical anomaly detection sử dụng z-score (3σ) trên rolling baseline window. Mỗi metric (latency, error_rate, CPU, memory) được monitor với scrape interval 5 giây. Alert fire khi giá trị vượt 3 standard deviations. Đây là first-pass filter — lightweight, recall cao nhưng precision thấp.

**Correlation layer:** Alerts từ nhiều services được nhóm theo temporal proximity (window 30 giây) và topology adjacency. Alerts fire trong cùng window từ services có edge trong topology graph → grouped thành 1 incident. Giảm alert noise từ N alerts xuống 1 correlated incident.

**RCA layer:** Topology-aware RCA kết hợp 3 signals: (1) topology distance từ edge (upstream-bias), (2) first-drift time (service nào alert trước = likely root), (3) alert volume (tiebreaker). Tham chiếu ADR-001 (§5 bên dưới) về quyết định chuyển sang ensemble detector.

## 4. Reliability validation (from W3-D2)

Tham chiếu: `w3/d2/w3-d2-pack/chaos_report.md`

**Scoreboard:**

| Metric | Value |
|--------|-------|
| Total experiments | 10 |
| Detected | 8/10 |
| RCA correct | 6/8 |
| False alarms | 0 |
| Precision | 1.00 |
| Recall | 0.80 |
| MTTD p50 | 25s |
| MTTD p95 | 42s |

**Top 3 Gaps:**

1. **Meta-Monitoring Blind Spot (Exp 6, 7):** Pipeline miss hoàn toàn auth_clock_skew và log_collector_disk_fill. Detector chỉ monitor application-tier metrics, không có metrics cho clock drift, disk usage, log ingestion lag. **Fix:** Thêm infra-specific detectors.

2. **Retry Storm Misattribution (Exp 10):** RCA pick checkout-svc (noisiest) thay vì upstream root cause. Alert count dominate scoring dù đã có penalty. **Fix:** Tăng downstream penalty weight, thêm Granger causality test.

3. **Lateral Service RCA Weakness (Exp 9):** dns-resolver latency detected nhưng RCA pick sai (api-gateway). Topology graph thiếu lateral dependency edges. **Fix:** Extend topology model với lateral dependencies (DNS, auth, cache).

## 5. Operational pattern (from W3-D3)

**Reproduced outage:** Cloudflare WAF Regex Catastrophic Backtracking (2019-07-02)

- **Failure mode:** Catastrophic backtracking — WAF regex với nested quantifiers deploy đồng loạt, gây CPU 100% trên tất cả edge servers khi nhận adversarial input
- **Key learning:** Pipeline phát hiện được incident (detection latency 25s) nhưng không thể:
  - Xác định nguyên nhân cụ thể của CPU saturation (thiếu application-layer introspection)
  - Correlate anomaly với deployment event (thiếu deployment integration)
  - Phân biệt simultaneous global failure vs cascading failure (topology-aware RCA vô dụng khi tất cả nodes fail cùng lúc)
- **ADR-001 reference:** Quyết định chuyển từ single threshold sang ensemble detector (3σ + Isolation Forest + deployment correlation) để giải quyết gaps trên

## 6. Cost model (from W3-D3)

Tham chiếu: `w3/d3/cost_model.py`

**Output cho current stack** (ước tính cho deployment thực tế):

```
Scenario: Current stack (20 services, lab environment)
  monthly_value: $8,000
  monthly_cost: $15,000
  roi: 0.53
  payback_months: 1.88
  verdict: not_worth_it

Scenario: Projected production (100 services)
  monthly_value: $80,000
  monthly_cost: $25,000
  roi: 3.20
  payback_months: 0.31
  verdict: worth_it
```

**Break-even point:** AIOps platform trở nên worth_it khi:
- ≥ 50 services VÀ ≥ 3 incidents/month VÀ downtime cost ≥ $10k/hour
- HOẶC ≥ 100 services với bất kỳ incident frequency nào có downtime cost ≥ $5k/hour

**Current verdict:** Ở quy mô lab (20 services), AIOps platform chưa justify cost. Nên đầu tư vào observability stack + SLO culture trước. Scale lên ≥ 100 services thì ROI rõ ràng positive.

## 7. Open risks

| # | Risk | Severity | Mitigation Plan |
|---|------|----------|-----------------|
| 1 | **Meta-monitoring blind spot:** Pipeline không monitor chính infra của mình (log collector, Prometheus). Nếu monitoring stack fail → hoàn toàn mù | High | Implement independent health-check circuit: external probe ping monitoring endpoints, alert qua kênh riêng (SMS/PagerDuty) không phụ thuộc monitored infra |
| 2 | **Lateral dependency gap:** Topology graph thiếu lateral services (DNS, auth, cache). RCA pick sai khi lateral service là root cause | Medium | Extend topology model: thêm lateral edges, implement "universal dependency" detection — nếu tất cả services cùng spike → check lateral services trước |
| 3 | **Ensemble detector chưa calibrate:** Voting weights (3σ, IF, deployment correlation) chưa được tune trên real production data. Có thể gây false alarm spike | Medium | Deploy ensemble ở "shadow mode" 2 tuần đầu — alert nhưng không page. Thu thập precision/recall data, tune weights, rồi mới enable production paging |
| 4 | **No automated rollback:** Khi detect deployment-triggered incident, pipeline chỉ alert mà không tự rollback. MTTR vẫn phụ thuộc on-call speed | Medium | Phase 2: integrate với CI/CD pipeline, implement automatic rollback trigger khi ensemble confidence > 0.9 VÀ deployment correlation fires. Cần canary deployment infra trước |
| 5 | **Cold start cho new services:** Isolation Forest cần ≥ 24h baseline data để train. New services trong 24h đầu chỉ có 3σ detector — miss multivariate anomaly | Low | Fallback: new services dùng 3σ + deployment correlation (không cần training data). IF auto-enable sau 24h. Document SOP cho onboarding new services |
