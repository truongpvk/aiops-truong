# W3-D1: SLO, Error Budget, Burn-Rate Alerting

> ← [AIOps W3 — Reliability Engineering & Postmortem](https://khanhnn00.github.io/learning-notes/xbrain/aiops-w3/)
>
> 11 min read · 2227 words

## Mục lục

1. [Định nghĩa](#1-định-nghĩa)
2. [SLI selection theo service type](#2-sli-selection-theo-service-type)
3. [SLO target ladder](#3-slo-target-ladder)
4. [Error budget](#4-error-budget)
5. [Burn rate](#5-burn-rate)
6. [Single-window alert failure modes](#6-single-window-alert-failure-modes)
7. [Multi-Window Multi-Burn-Rate (MWMBR)](#7-multi-window-multi-burn-rate-mwmbr)
8. [Khi nào dùng SLO-based vs anomaly detection](#8-khi-nào-dùng-slo-based-vs-anomaly-detection)
9. [Bài tập](#9-bài-tập)
10. [Anti-patterns](#10-anti-patterns)
11. [Deliverable summary](#11-deliverable-summary)
12. [References](#12-references)

---

## 1. Định nghĩa

| Khái niệm | Là gì | Phạm vi | Phạt khi miss |
|---|---|---|---|
| **SLI** — Service Level Indicator | Một số đo, range 0-1, đo từ phía user, proportional với user pain | Engineer-level metric | — |
| **SLO** — Service Level Objective | Target cho SLI, ví dụ 99.9% trong 30-day window | Internal commitment | Release freeze, reliability work |
| **SLA** — Service Level Agreement | Cam kết với customer, hợp đồng | Legal | Tiền (refund, credit) |

Quan hệ: **SLA < SLO < hiện tại**. SLA luôn lỏng hơn SLO; SLO luôn lỏng hơn measurement hiện tại (để có buffer fix).

> Source: Beyer et al., *Site Reliability Engineering*, O'Reilly 2016, Chapter 4. [sre.google/sre-book/service-level-objectives](https://sre.google/sre-book/service-level-objectives/)

---

## 2. SLI selection theo service type

Google SRE Workbook Chapter 2 phân service thành 4 loại, mỗi loại có metric chuẩn:

| Service type | SLI categories | Ví dụ formula |
|---|---|---|
| Request-response (API, web) | Availability, Latency | `count(2xx,3xx,4xx_not_429 AND latency < 200ms) / count(all)` |
| Data processing (ETL, stream) | Freshness, Correctness, Coverage, Throughput | `count(events processed within 5min lag) / count(all events)` |
| Storage (S3-like, DB) | Durability, Availability | `count(read OR write success) / count(all)` |
| Batch job (cron) | Completeness, Runtime | `count(completed within 1h SLA) / count(scheduled)` |

### 2.1 SLI quality criteria

Mỗi SLI phải pass 3 test:

1. **Measurable** — có metric thật từ system, không phải khái niệm trừu tượng.
2. **User-side** — đo từ phía user (load balancer log, RUM, synthetic probe). Đo từ phía app server miss network drop.
3. **Proportional** — SLI giảm khi user pain, không giảm khi user OK.

### 2.2 Anti-pattern: CPU làm SLI

CPU không proportional với user pain:

- CPU 80%, latency 150ms → user OK, nhưng SLI báo "không OK".
- CPU 30%, deadlock, latency 5s → user khổ, nhưng SLI báo "OK".

CPU là **saturation signal**, không phải **user happiness metric**. Giữ cho capacity dashboard, không dùng làm SLI.

### 2.3 Latency: percentile selection

| Percentile | Capture | Dùng khi |
|---|---|---|
| p50 (median) | Trung vị | Throughput-bound system (data pipeline) |
| p95 | Top 5% slowest | Báo cáo general |
| p99 | Top 1% — tail real user pain | Default cho user-facing |
| p99.9 | Top 0.1% | Tier-1 service (search, login) |

Default cho user-facing service: **p99**.

### 2.4 Availability: status code accounting

| HTTP range | Đếm vào fail? | Lý do |
|---|---|---|
| 5xx | ✅ | Server error |
| 429 | ✅ | Rate-limited bởi system → system reject user |
| 4xx (không 429) | ❌ | User-side error (bad request) |
| 2xx, 3xx | ❌ | Success |

---

## 3. SLO target ladder

### 3.1 Số 9 và downtime budget

30-day window:

| SLO | Downtime/tháng | Downtime/tuần | Downtime/ngày |
|---|---|---|---|
| 99% | 7h 18m | 1h 41m | 14m |
| 99.5% | 3h 39m | 50m | 7m |
| 99.9% | 43m | 10m | 1m 26s |
| 99.95% | 21m 36s | 5m | 43s |
| 99.99% | 4m 19s | 1m | 8.6s |
| 99.999% | 26s | 6s | 0.86s |

### 3.2 Cost ladder

Mỗi tier thêm 1 số 9 thường nhân chi phí infra + ops headcount lên 3-10×:

| SLO | Architecture cần |
|---|---|
| 99% | 1 instance, manual recovery |
| 99.9% | Multi-instance, LB, auto-failover |
| 99.99% | Multi-AZ, automated runbook, 24/7 on-call |
| 99.999% | Multi-region active-active, dedicated SRE team |

> Source: Beyer et al., Chapter 4, "The Nines of Availability."

---

## 4. Error budget

```
budget = (1 - SLO) × total_events
```

### 4.1 Đơn vị budget

| Đơn vị | Khi nào dùng |
|---|---|
| Số request fail | Service có request count rõ |
| Phút downtime | Service binary up/down |
| Số window fail | Stream pipeline, batch |

### 4.2 Ví dụ tính budget

SLO 99.9%, traffic 1M req/day, 30-day window:

```
budget = 0.001 × 1M × 30 = 30,000 request fail / month
```

Convert sang downtime equivalent (nếu sự cố 100% fail tại 12 req/s peak):

```
downtime budget = 30,000 / 12 = 2,500s ≈ 41 phút / tháng
```

### 4.3 Burn-down chart

```
% budget
consumed
100% ┤                                ╱─── exhausted
     │                          ╱────╱
 75% ┤                    ╱────╱
     │              ╱────╱   ← burn rate ≈ 1.5 (normal range)
 50% ┤        ╱────╱
     │  ╱────╱
 25% ┤─╱
     │
   0%└────┬────┬────┬────┬────┬────┬─── day
        D5   D10  D15  D20  D25  D30
```

Slope = burn rate. Slope 1.0 nghĩa là đốt budget đúng tốc độ baseline.

> Source: Google SRE Workbook Chapter 2 "Implementing SLOs." [sre.google/workbook/implementing-slos](https://sre.google/workbook/implementing-slos/)

---

## 5. Burn rate

```
burn_rate = error_rate (trong window) / (1 - SLO)
```

### 5.1 Burn rate → time to exhaust

Với SLO 99.9%, 30-day window:

| Burn rate | Time to exhaust budget |
|---|---|
| 1 | 30 ngày (baseline) |
| 2 | 15 ngày |
| 6 | 5 ngày |
| 14.4 | 50 giờ |
| 36 | 20 giờ |
| 144 | 5 giờ |
| 1440 | 30 phút |

### 5.2 Tại sao burn rate hơn raw error rate

Raw "5% error rate" không trả lời "có nên page không":

- SLO 99% → 5% error → burn rate 5 → vừa phải.
- SLO 99.9% → 5% error → burn rate 50 → page ngay.

Burn rate normalize theo SLO → 1 ngưỡng dùng được cho mọi service.

### 5.3 Burn rate trên Prometheus

```promql
(
  sum(rate(http_requests_total{status=~"5..|429"}[1h]))
  / sum(rate(http_requests_total[1h]))
) / (1 - 0.999)
```

---

## 6. Single-window alert failure modes

| Window | False positive | MTTD | Stop-firing lag |
|---|---|---|---|
| 1 phút | Cao (spike-prone) | 1-2 phút | <1 phút |
| 5 phút | Trung bình | 3-5 phút | 5 phút |
| 1 giờ | Thấp | 5-15 phút | 1 giờ |
| 6 giờ | Rất thấp | 30+ phút | 6 giờ |

Không có window đơn perfect: short = noisy, long = chậm + dính lâu sau khi hết sự cố.

---

## 7. Multi-Window Multi-Burn-Rate (MWMBR)

> Source: Google SRE Workbook Chapter 5 "Alerting on SLOs," Section 6. [sre.google/workbook/alerting-on-slos](https://sre.google/workbook/alerting-on-slos/)

### 7.1 Công thức

Alert fire khi **CẢ HAI** condition true cùng lúc:

1. **Long window** burn rate ≥ T (sự cố đủ lớn để đáng action)
2. **Short window** burn rate ≥ T (sự cố vẫn đang diễn ra ngay bây giờ)

Quy tắc tỉ lệ: **short window ≈ long / 12**.

### 7.2 Tier table chuẩn Google SRE

| Tier | Long window | Short window | Burn rate threshold | Budget burned khi fire | Action |
|---|---|---|---|---|---|
| 1 — urgent page | 1h | 5min | 14.4 | 2% trong 1h | Page on-call ngay |
| 2 — page | 6h | 30min | 6 | 5% trong 6h | Page on-call ngay |
| 3 — ticket | 3 ngày | 6h | 1 | 10% trong 3 ngày | Ticket, giờ hành chính |

Công thức derive threshold:

```
T = budget_fraction / window_fraction_of_SLO_period
```

Ví dụ Tier 1: muốn 2% budget burned in 1h → `T = 0.02 / (1h / 720h) = 14.4`.

### 7.3 Recovery behavior

Khi sự cố hết:

- Short window về dưới threshold trong ~5 phút → AND condition false → alert stop.
- Long window vẫn còn cao thêm 30-60 phút, nhưng AND đã false → không spam.

Đây là lý do AND quan trọng: alert recover nhanh.

### 7.4 Prometheus implementation

```yaml
groups:
- name: slo-alerts
  rules:
  - alert: SLO_BurnRate_Tier1
    expr: |
      (
        sum(rate(http_requests_total{status=~"5..|429"}[1h]))
        / sum(rate(http_requests_total[1h]))
      ) / (1 - 0.999) >= 14.4
      AND
      (
        sum(rate(http_requests_total{status=~"5..|429"}[5m]))
        / sum(rate(http_requests_total[5m]))
      ) / (1 - 0.999) >= 14.4
    labels: {severity: page, tier: "1"}
    annotations:
      summary: "Burn rate ≥ 14.4 over both 1h and 5m windows"
      runbook: "https://runbooks.example.com/slo-burn"

  - alert: SLO_BurnRate_Tier2
    expr: |
      (sum(rate(http_requests_total{status=~"5..|429"}[6h]))
       / sum(rate(http_requests_total[6h]))) / (1 - 0.999) >= 6
      AND
      (sum(rate(http_requests_total{status=~"5..|429"}[30m]))
       / sum(rate(http_requests_total[30m]))) / (1 - 0.999) >= 6
    labels: {severity: page, tier: "2"}

  - alert: SLO_BurnRate_Tier3
    expr: |
      (sum(rate(http_requests_total{status=~"5..|429"}[3d]))
       / sum(rate(http_requests_total[3d]))) / (1 - 0.999) >= 1
      AND
      (sum(rate(http_requests_total{status=~"5..|429"}[6h]))
       / sum(rate(http_requests_total[6h]))) / (1 - 0.999) >= 1
    labels: {severity: ticket, tier: "3"}
```

---

## 8. Khi nào dùng SLO-based vs anomaly detection

```
Metric X
├── Proportional với user pain?
│   ├── YES → SLI candidate → SLO-based MWMBR alert
│   └── NO → continue
├── Saturation signal (CPU, mem, queue, FD count)?
│   ├── YES → ticket alert (low priority) hoặc capacity dashboard, không page
│   └── NO → continue
└── Leading indicator (drift, gradual change)?
    └── YES → anomaly detection (W1) trên metric, alert ticket khi sustained
```

| Metric | SLI? | Loại alert |
|---|---|---|
| HTTP 5xx rate | ✅ | SLO MWMBR |
| Request latency p99 | ✅ | SLO MWMBR |
| Kafka consumer lag | ✅ (freshness SLI) | SLO MWMBR |
| CPU usage | ❌ | Saturation, no page |
| Memory usage | ❌ | Saturation, no page |
| Disk free % | ❌ | Operational ticket |
| Log error cluster volume (Drain3) | ❌ | Anomaly alert, no page |

---

## 9. Bài tập

### 9.0 Setup

Download pack:

```bash
wget https://khanhnn00.github.io/learning-notes/aiops-w3/lab/w3-d1-pack.zip
unzip w3-d1-pack.zip -d w3-d1-pack/
cd w3-d1-pack/
```

Install deps + generate 3-day synthetic data (chạy 1 lần, ~30s, output ~300MB):

```bash
uv venv --python 3.12
uv pip install pyyaml
uv run python generate_data.py
```

### 9.1 Cấu trúc pack

```
w3-d1-pack/
├── generate_data.py          # sinh 3-day synthetic log (đã chạy ở §9.0)
├── docker-compose.yml        # Prometheus 2.50 + Alertmanager 0.27 + Grafana 10.4
├── configs/                  # prometheus.yml + alertmanager.yml
├── scripts/
│   ├── compute_baseline.py   # extract baseline SLI từ log
│   ├── validate.py           # compare alert rules với incident_window.csv
│   └── prometheus_replay.sh  # (optional) push metric vào Prometheus
├── sample-solution/          # SLO + alert YAML mẫu — tham khảo, KHÔNG copy
└── data/                      # sinh sau khi chạy generate_data.py
    ├── access_log.jsonl      # ~2M event nginx access log
    ├── db_query_log.jsonl    # ~170k pg_stat_statements sample
    ├── frontend_rum.jsonl    # ~520k RUM page load event
    ├── topology.yaml         # service map: frontend → api → db
    └── incident_window.csv   # 5 ground-truth incident
```

Stack giả định: 3-tier service — React frontend (CDN) + FastAPI 4-instance + Postgres primary/replica. Peak ~15 req/s sampled traffic, e-commerce context.

**Log schema** (mỗi dòng 1 JSON event):

```json
// access_log.jsonl
{"ts": "2026-06-01T00:00:00+00:00", "method": "GET", "path": "/api/orders",
 "status": 200, "latency_ms": 142}

// db_query_log.jsonl
{"ts": "2026-06-01T00:00:00+00:00", "query": "SELECT ...",
 "duration_ms": 38, "success": true, "rows": 17}

// frontend_rum.jsonl
{"ts": "2026-06-01T00:00:00+00:00", "page": "/products",
 "dom_ready_ms": 1240, "js_error": false, "network_error": false}
```

**incident_window.csv** (ground truth, dùng để validate):

```csv
incident_id,layer,severity,start_utc,end_utc,fail_rate_multiplier
1,api,tier1,2026-06-01T03:00:00+00:00,2026-06-01T03:08:00+00:00,100
```

### 9.2 Bước 1 — Compute baseline

```bash
uv run python scripts/compute_baseline.py --data data/ --out baseline.json
```

Output:

```json
{
  "frontend": {"success_rate_p50": 0.992, "success_rate_p99": 0.974, "events_per_day": 171428},
  "api":      {"success_rate_p50": 0.997, "success_rate_p99": 0.989, "events_per_day": 714285},
  "db":       {"success_rate_p50": 0.999, "success_rate_p99": 0.996, "events_per_day": 114285}
}
```

Dùng `baseline.json` làm input cho mọi quyết định ở step sau.

### 9.3 Bước 2 — Viết `slo_spec.yaml`

Schema bắt buộc (mỗi service 1 entry, tổng 3 entry):

```yaml
version: 1
services:
  - name: <frontend|api|db>
    sli:
      name: <ví dụ frontend_availability>
      kind: <availability|latency|freshness|throughput>
      formula: "<mô tả plain text: numerator / denominator>"
      promql_good: "<promql đếm event thoả SLO>"
      promql_total: "<promql đếm tổng event>"
      source: <log file path hoặc Prometheus metric>
    slo:
      target: <float ∈ [0.9, 0.9999]>     # e.g. 0.999 = 99.9%
      window_days: 30
    budget:
      total_events_per_month: <int>        # từ baseline.json × 30
      allowed_failures_per_month: <int>    # = (1 - target) × total
      downtime_minutes_equivalent: <int>   # = allowed_failures / req_per_minute
```

### 9.4 Bước 3 — Viết `burn_rate_alerts.yaml`

3 tier MWMBR cho mỗi service → tổng **9 alert rule**. Template §7.4.

Verify syntax:

```bash
promtool check rules burn_rate_alerts.yaml
# Expected: 9 rules found, 0 errors
```

### 9.5 Bước 4 — Validate trên replay data

```bash
bash scripts/prometheus_replay.sh           # replay 7-day log vào Prometheus
python scripts/validate.py \
  --rules burn_rate_alerts.yaml \
  --truth data/incident_window.csv \
  --baseline-rule "error_rate > 0.01 for 5m" \
  --out validation_report.json
```

Output schema:

```json
{
  "static_baseline": {"fired": 43, "tp": 4, "fp": 39, "fn": 0, "mttd_p50_s": 252},
  "your_mwmbr":      {"fired": 6,  "tp": 4, "fp": 2,  "fn": 0, "mttd_p50_s": 287},
  "noise_reduction_pct": 86.0,
  "mttd_delta_s": 35,
  "verdict": "pass"
}
```

**Acceptance:** `noise_reduction_pct ≥ 70` AND `mttd_delta_s < 60` AND `your_mwmbr.fn = 0`.

Nếu fail → tune threshold, đổi window size, hoặc đổi SLO target. Iterate.

### 9.6 Bước 5 — Viết `DESIGN.md`

Trả lời 5 câu hỏi, mỗi câu 100-200 từ, **bắt buộc reference số từ `baseline.json` hoặc `validation_report.json`**:

1. **SLI choice cho frontend.** Tại sao chọn metric X thay vì Y? Frontend RUM cho 4 candidate signal (page load time, DOM ready, JS error rate, network error rate). Chọn cái nào, vì sao loại 3 cái còn lại?
2. **SLO target cho api.** Tại sao 99.9% chứ không 99% hoặc 99.99%? Cost của mỗi tier (§3.2) so với baseline hiện tại 99.7% (từ baseline.json).
3. **Latency threshold p99.** Bạn cut latency ở mốc nào (200ms? 500ms? 1s?)? Plot distribution latency 7-day (text/table OK), defend choice.
4. **4xx exclusion.** Tại sao loại 4xx ra khỏi error count (trừ 429)? Có log endpoint nào có rate 4xx > 5% mà *không phải* hệ thống lỗi không? Reference data.
5. **MWMBR tuning.** Dùng Google default (14.4, 6, 1) hay tune? Nếu tune, dựa vào ảnh hưởng đến `noise_reduction_pct` và `fn` thế nào?

### 9.7 Bước 6 — Viết `SUBMIT.md`

```markdown
# W3-D1 Submission — <your name>

## 3 thứ tôi học được
1. ...
2. ...
3. ...

## 1 thứ vẫn chưa rõ
...

## 1 trade-off trong SLO decision của tôi mà tôi không chắc
...

## Validation report
- noise_reduction_pct: __%
- mttd_delta_s: __s
- false_negative: __
- verdict: __
```

### 9.8 Acceptance checklist

- [ ] `slo_spec.yaml` parse được, có đủ 3 service, target ∈ [0.9, 0.9999]
- [ ] `burn_rate_alerts.yaml` có 9 rule, `promtool check rules` pass với 0 error
- [ ] `validation_report.json` đạt acceptance ở §9.5 (noise ≥ 70%, MTTD delta < 60s, FN = 0)
- [ ] `DESIGN.md` trả lời cả 5 câu, mỗi câu có ít nhất 1 số từ baseline/validation
- [ ] `SUBMIT.md` đủ 4 section

---

## 10. Anti-patterns

| Anti-pattern | Hậu quả |
|---|---|
| CPU làm SLI | False positive khi user OK; false negative khi user khổ |
| SLO 99.999% cho mọi service | Miss SLO ngay tháng đầu → bị ignore |
| Single-window alert | Spam khi window ngắn; chậm + dính lâu khi window dài |
| Đếm 4xx vào fail | SLI bị bot/scraper kéo xuống |
| 1 SLO cho cả 3-tier service | Mất visibility từng layer |
| SLO chỉ trong YAML, không có dashboard | Không ai review → SLO chết |
| Bỏ short window trong MWMBR | Alert dính 1-6h sau khi sự cố hết |

---

## 11. Deliverable summary

| File | Mô tả ngắn | Spec chi tiết |
|---|---|---|
| `slo_spec.yaml` | 3 service × SLI+SLO+budget | §9.3 |
| `burn_rate_alerts.yaml` | 9 Prometheus alert rule (3 tier × 3 service) | §9.4 |
| `validation_report.json` | Kết quả compare MWMBR vs static baseline | §9.5 |
| `DESIGN.md` | Trả lời 5 câu defend choice | §9.6 |
| `SUBMIT.md` | Reflection 4-section | §9.7 |

Đường dẫn nộp: `aiops-<tên>/w3/d1/`.

---

## 12. References

| Source | Topic | URL |
|---|---|---|
| Beyer et al. *SRE Book* Ch 4 | SLO foundations | https://sre.google/sre-book/service-level-objectives/ |
| *SRE Workbook* Ch 2 | Implementing SLOs | https://sre.google/workbook/implementing-slos/ |
| *SRE Workbook* Ch 5 | Alerting on SLOs (MWMBR công thức gốc) | https://sre.google/workbook/alerting-on-slos/ |
| Coursera Google Cloud | SRE: Measuring and Managing Reliability (4-week) | https://www.coursera.org/learn/site-reliability-engineering-slos |
| Prometheus docs | Alerting rules syntax | https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/ |
| Grafana Labs | SLO dashboard examples | https://grafana.com/grafana/dashboards/?dataSource=prometheus&search=slo |
