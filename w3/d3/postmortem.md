# Postmortem: Cloudflare WAF Regex Catastrophic Backtracking (Reproduction)

**Status:** complete  
**Date:** 2026-06-21  
**Authors:** Truong PVK  
**Severity:** SEV1  
**Duration:** 4 phút 30 giây (08:01:30 UTC → 08:06:00 UTC)

## Summary

Một WAF rule chứa regex với nested quantifiers được deploy đồng loạt lên edge server. Khi nhận request có input adversarial (dạng `x=xxx...`), regex engine rơi vào catastrophic backtracking — thử tổ hợp exponential → CPU 100%. Edge server không thể phục vụ bất kỳ request nào trong suốt thời gian evil regex active. Sự cố được khắc phục bằng cách xóa WAF rule.

Sự cố này tái hiện (reproduction) mô hình lỗi của vụ Cloudflare 2019-07-02, nơi WAF rule tương tự gây outage 27 phút trên toàn bộ edge network, traffic giảm 82%.

## Impact

- Users affected: 100% — toàn bộ request qua edge server đều bị ảnh hưởng
- Revenue impact: Trong production (Cloudflare scale), ước tính hàng triệu USD/giờ. Trong reproduction: không có revenue impact
- SLO budget consumed: ~100% availability budget trong 4.5 phút — nếu SLO 99.95% (window 30 ngày), tiêu hết ~21% monthly error budget
- External communication: Trong reproduction không có. Original Cloudflare: status page updated, blog post chi tiết

## Timeline (UTC)

| Time | Event |
|------|-------|
| 08:00:00 | Baseline: Edge server healthy, avg latency 5ms, CPU 3% |
| 08:01:30 | **Trigger:** WAF rule với evil regex được deploy (evil_regex_deployed=true) |
| 08:01:35 | Request đầu tiên với adversarial payload hit edge server |
| 08:01:37 | CPU spike lên 98.5% — catastrophic backtracking đang tiêu hết CPU |
| 08:01:42 | HTTP 503 error rate tăng lên 0.85/s — edge server trả 503 cho hầu hết request |
| 08:01:50 | Healthcheck fail — edge server không phản hồi được healthcheck endpoint |
| 08:02:15 | p99 latency đạt 12.4s (baseline: 5ms) — tăng 2480 lần |
| 08:03:00 | Traffic drop 94% — chỉ 3/50 request/s thành công |
| 08:05:00 | Root cause xác định: regex nested quantifiers gây exponential backtracking |
| 08:05:30 | **Mitigation:** Evil regex WAF rule bị xóa |
| 08:05:45 | CPU giảm về 4.2% — hệ thống bắt đầu recovery |
| 08:06:00 | **Full recovery:** Healthcheck pass, latency trở về baseline 5ms |

## Root cause

Regex engine (Python `re` module, sử dụng NFA-based backtracking) gặp pattern với nested quantifiers `(?:(?:\"|\d|.*)+(?:.*=.*))`. Khi input có dạng `x=xxx...xxx`, engine phải thử exponential số lượng tổ hợp do:

1. `.*` bên trong `(...)+ ` match được 0 hoặc nhiều ký tự
2. `+` bên ngoài lặp lại group này 1 hoặc nhiều lần
3. Mỗi vị trí trong string có thể thuộc iteration hiện tại HOẶC iteration tiếp theo → tạo ra 2^N tổ hợp

Với input 30 ký tự: ~2^30 = 1 tỷ tổ hợp → CPU bị pegged 100% trong 8-15 giây cho MỖI request. Server không thể xử lý request khác trong thời gian đó.

**Tại sao detection bị delay:** Pipeline AIOps chỉ bắt đầu thấy anomaly sau 25 giây (khi metric scrape cycle kế tiếp bắt đầu). Không có integration với deployment system nên không biết rằng một config push vừa xảy ra tại thời điểm anomaly bắt đầu.

## Contributing factors

- **WAF rule deployment pipeline không có regex complexity check:** Rule với nested quantifiers được chấp nhận mà không qua ReDoS detector. Một static analysis tool (ví dụ: `safe-regex`, `rxxr2`) sẽ flag pattern này ngay lập tức.
- **Global atomic deployment thay vì canary rollout:** Rule được deploy đồng loạt lên 100% edge servers. Nếu canary 1% → 10% → 100%, outage sẽ chỉ ảnh hưởng 1% traffic ban đầu và bị phát hiện trước khi lan rộng.
- **Không có CPU-time limit cho regex execution:** Regex engine chạy unbounded — không có timeout hoặc backtracking limit. Một regex sandbox với wall-clock timeout (ví dụ: 10ms max) sẽ ngắt match sớm và trả safe default.

## Detection

- **Cách phát hiện:** Pipeline AIOps phát hiện qua latency anomaly (p99 tăng 2480x) và error rate spike (503 rate 0.85/s). Detection latency: 25 giây.
- **Có thể phát hiện sớm hơn không?**
  - **Có — Gap 1:** Nếu pipeline tích hợp với deployment/config-push system, nó có thể correlate timing của config push với onset của anomaly trong <5 giây thay vì 25 giây. Deployment event + metric degradation trong window 10 giây = strong signal.
  - **Có — Gap 2:** Pipeline không có application-layer introspection. Nó thấy "CPU 98.5%" nhưng không biết TẠI SAO CPU cao. Nếu có WAF-specific metrics (regex match duration, backtracking count), detector sẽ identify root cause chính xác thay vì chỉ symptom.

## Response

- **What went well:**
  - Rollback nhanh — chỉ cần xóa WAF rule flag, không cần restart service
  - Recovery tự động sau rollback — không cần intervention bổ sung
  - Timeline capture đầy đủ với UTC timestamps
- **What went poorly:**
  - Detection latency 25 giây — trong production scale (Cloudflare), 25 giây = hàng triệu failed request
  - Không có automated rollback — phải manual identify và remove rule
  - Pipeline không explain WHY CPU spiked — chỉ thấy symptom, không thấy cause
- **Where we got lucky:**
  - Reproduction environment nhỏ → rollback nhanh. Trong production với thousands of edge nodes, rollback mất nhiều phút hơn
  - Input adversarial rõ ràng → dễ reproduce. Real-world adversarial input có thể subtle hơn

## Action items

| Item | Owner | Due | Priority |
|------|-------|-----|----------|
| Thêm ReDoS detector vào WAF rule deployment pipeline (static analysis trước deploy) | Platform team | 2026-07-05 | P0 |
| Implement canary rollout cho WAF rules: 1% → 10% → 100% với automatic rollback | Platform team | 2026-07-12 | P0 |
| Thêm regex execution timeout (10ms wall-clock limit) vào WAF middleware | Edge team | 2026-07-05 | P0 |
| Tích hợp deployment events vào AIOps pipeline để correlate config push với anomaly | AIOps team | 2026-07-19 | P1 |
| Thêm WAF-specific metrics (regex_match_duration, backtracking_count) vào monitoring | Edge team | 2026-07-12 | P1 |
| Document regex best practices và add lint rule cấm nested quantifiers | Documentation | 2026-07-26 | P2 |
