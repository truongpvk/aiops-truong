# Detection Approach — DESIGN.md

## Approach tôi dùng

**Ensemble stateful detection** gồm 3 lớp độc lập:
1. **Rolling Z-Score** — per-metric, tính từ window 60 samples gần nhất
2. **Hard Absolute Thresholds** — immune với baseline poisoning
3. **Isolation Forest** — multivariate catch-all (warmup 100, retrain mỗi 50)

Kết hợp với **streak counters** để trend analysis và root-cause classification, **alert suppression** cooldown 300s per fault type.

---

## Tại sao chọn approach này

Ban đầu tôi dùng EWMA để học baseline. Trong lab thực tế phát hiện bug nghiêm trọng: nếu fault inject ngay từ sample đầu (trước warmup), EWMA học luôn giá trị bất thường làm "bình thường" → z-score mãi thấp dù `upstream_timeout_rate=72%`, `5xx=37%`.

Rolling z-score giải quyết vấn đề này vì window có kích thước cố định — majority samples bình thường trong window sẽ kéo mean về vùng normal, anomaly tự nổi bật. Nhưng nếu fault bắt đầu từ sample 1 và window chưa đủ 20 samples thì cả rolling z-score cũng không đáng tin. Vì vậy tôi thêm hard threshold làm lớp thứ hai: không cần học baseline, chỉ cần giá trị tuyệt đối vượt ngưỡng từ spec là fire.

Hai lớp bổ sung cho nhau:
- Rolling z-score tốt khi fault đến sau khi baseline đã ổn định
- Hard threshold tốt khi fault inject sớm hoặc z-score bị nhiễm

---

## Cách hoạt động

**Mỗi POST /ingest, theo thứ tự:**

1. **Score trước, append sau** — với mỗi metric, tính `rolling_zscore(window, value)` từ window hiện tại (chưa có sample mới), rồi mới `window.append(value)`. Tránh contamination hoàn toàn.

2. **Isolation Forest** — score feature vector 8 chiều trước khi append vào training set. Retrain định kỳ mỗi 50 samples sau warmup 100.

3. **Trend counters** — update `memory_rising_streak`, `timeout_rising_streak`, `rps_spike_streak` bằng cách so sánh với sample liền trước.

4. **Composite scoring** per fault type — cộng điểm từ nhiều signal độc lập (z-score + hard threshold + streak), ngưỡng fire ≥ 2 điểm:

| Fault | Signals |
|---|---|
| `memory_leak` | mem z-score, GC z-score, rising streak >15, mem util >70%, GC >30ms |
| `traffic_spike` | RPS z-score, queue z-score, latency z-score, spike streak ≥3, RPS >3× baseline, queue >50 |
| `dependency_timeout` | timeout z-score, 5xx z-score, latency z-score, timeout streak ≥8, timeout_rate >10%, 5xx >10% |

5. **Alert suppression** — `last_alert_time[type]`, cooldown 300s. Không fire gì trước 20 samples đầu.

6. **Isolation Forest catch-all** — chỉ fire nếu không detector nào khác trigger và score > 0.65.

---

## Parameters tôi chọn

| Parameter | Giá trị | Lý do |
|---|---|---|
| `zscore_window = 60` | 60 samples | ~30 phút prod time ở speed=10 — đủ để học baseline ngắn hạn, đủ nhạy với spike |
| `METRIC_WINDOW = 300` | 300 | Giữ 300 samples cho trend analysis và debug |
| Warmup no-fire = 20 | 20 | Đủ để rolling z-score có ít nhất 20 samples (ngưỡng tối thiểu của hàm) |
| Composite threshold ≥ 2 | 2 | Require ít nhất 2 signals độc lập — giảm false positive, vẫn đủ nhạy |
| Hard timeout threshold = 10% | 10% | Spec nói normal là 0–0.4%; 10% là 25× normal — rõ ràng anomaly |
| Hard 5xx threshold = 10% | 10% | Spec nói normal là 0–0.8%; 10% là 12× normal |
| Hard queue threshold = 50 | 50 | Spec nói normal là 2–10; 50 là 5× max normal |
| `memory_rising_streak ≥ 20` | 20 | memory_leak inject tuyến tính — cần đủ samples để phân biệt với noise |
| `timeout_rising_streak ≥ 8` | 8 | dependency_timeout ramp nhanh hơn (~15 phút prod) — threshold thấp hơn |
| `ALERT_COOLDOWN = 300s` | 300 | Theo spec. Ngăn alert storm khi fault đang active |
| `iso WARMUP = 100` | 100 | Isolation Forest cần đủ normal samples để học ranh giới decision boundary |
| `RETRAIN_EVERY = 50` | 50 | Adapt với data mới mà không train quá thường (overhead) |

---

## Bài học từ debug thực tế

Trong lab, generator inject fault từ sample đầu tiên (do `fault_start_real_seconds` nhỏ). EWMA học baseline từ anomaly nên z-score = 0 dù metrics rõ ràng bất thường. Sau khi switch sang rolling z-score + hard threshold, pipeline fire alert ngay lần đầu `upstream_timeout_rate > 10%`.

Điều này dạy một nguyên tắc quan trọng: **detector không nên phụ thuộc hoàn toàn vào learned baseline** trong môi trường không có guarantee về thời điểm fault. Hard threshold từ domain knowledge (bảng spec) là lớp phòng thủ cần thiết.

---

## Cải thiện nếu có thêm thời gian

1. **Changepoint detection (PELT/BOCPD)** — tốt hơn streak counters cho gradual drift, ít cần tuning threshold thủ công
2. **Seasonal decomposition** — tách diurnal pattern ra trước khi tính z-score, giảm false positive vào giờ peak traffic
3. **Drain3 log clustering** — hiện tôi dùng regex naive (`<N>`). Drain3 cho log novelty detection chính xác hơn, đặc biệt với fault chưa biết
4. **Kalman filter** thay rolling mean — model measurement noise tốt hơn, phù hợp với metrics có variance không đồng đều
5. **Adaptive threshold** — thay vì hard-code `> 10%`, học phân phối từ lịch sử và tự chỉnh ngưỡng theo thời gian

---

## Kiến trúc tổng quan

```
POST /ingest
     │
     ▼
asyncio.Lock (thread-safe)
     │
     ▼
StreamingState (singleton, toàn bộ request share)
     │
     ├── Rolling Z-Score (per-metric, 60-sample window)
     │     score → append (không bao giờ ngược lại)
     │
     ├── Hard Thresholds (absolute, từ domain spec)
     │
     ├── Streak Counters (trend / root-cause)
     │
     ├── Isolation Forest (multivariate, warmup=100)
     │
     └── Composite Scorer
           │
           ├── ≥2 signals → fire_alert()
           ├── Cooldown check (300s per type)
           └── alerts.jsonl
```

State persist vào `state_snapshot.pkl` mỗi 60s — EWMA states, rolling windows, Isolation Forest training data survive server restart.