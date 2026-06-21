# W3-D3 Submission — Truong PVK

## Outage chosen

- **ID:** 3
- **Name:** Cloudflare WAF Regex Catastrophic Backtracking — 2019-07-02
- **Why this one:** Catastrophic backtracking là một failure mode vừa thú vị vừa thực tiễn — nó minh họa rõ cách một lỗi nhỏ ở tầng application (regex pattern) có thể gây sập toàn bộ hệ thống trong vài giây. Pattern này đặc biệt relevant cho AIOps vì nó thách thức topology-aware RCA: tất cả nodes fail đồng thời (không có cascading sequence để phân tích), buộc pipeline phải có approach khác.
- **Failure mode:** Catastrophic backtracking (regex) + operator action without guardrail (global atomic deploy)

## 3 things I learned from this outage

1. **Regex là attack surface ẩn:** Một regex trông vô hại có thể tạo exponential time complexity. Bất kỳ system nào chấp nhận user input và match với regex đều cần ReDoS protection. Lesson: luôn chạy static analysis (safe-regex, rxxr2) trước khi deploy regex rule, và set wall-clock timeout cho mọi regex execution.

2. **Global atomic deploy là anti-pattern nguy hiểm:** Deploy đồng loạt 100% traffic = maximum blast radius. Cloudflare lẽ ra chỉ mất 1% traffic nếu canary trước. Lesson: mọi config change (không chỉ code) phải qua canary rollout pipeline với automatic rollback khi metric degrade.

3. **Topology-aware RCA có blind spot với simultaneous failure:** Khi TẤT CẢ nodes fail cùng lúc (do global deploy), RCA topology không có cascading sequence để phân tích — nó degrade thành count-based ranking. Pipeline cần detector riêng cho pattern "correlated simultaneous failure across all instances" và cần integrate deployment events để nhận ra trigger.

## 1 thing my pipeline would still miss if this outage happened for real

- **Pattern:** Application-layer root cause identification (WHY CPU is high)
- **Why miss:** Pipeline chỉ monitor infrastructure metrics (CPU, latency, error rate). Nó sẽ detect "CPU 98.5%, latency 12s, 503 rate high" nhưng không thể phân biệt: CPU spike do regex backtracking vs CPU spike do traffic surge vs CPU spike do memory pressure vs CPU spike do infinite loop. Trong Cloudflare case, biết rằng WAF regex là root cause đòi hỏi application-layer introspection mà pipeline không có.
- **Mitigation idea:** Thêm application-specific metrics (regex_match_duration, waf_rule_id active, backtracking_count) vào monitoring stack. Khi CPU spike + regex_match_duration spike cùng lúc → auto-tag "likely regex issue" và suggest kiểm tra WAF rules vừa deploy gần đây.

## 1 decision in my ADR I'm not fully sure about

Ensemble voting rule "≥2/3 detectors đồng thuận" có thể quá strict hoặc quá lax tuỳ environment:
- **Quá strict:** Nếu chỉ có 3σ detect anomaly nhưng IF và deployment correlation không fire (vì IF chưa train đủ data, và không có deployment event) → incident bị miss dù 3σ đúng.
- **Quá lax:** Nếu giảm xuống "≥1/3" → false alarm rate tăng vì 3σ alone có precision thấp.

Tôi đã thêm exception rule "3σ + deployment correlation = alert" nhưng chưa chắc exception rules sẽ cover hết edge cases. Có thể cần adaptive voting threshold — bắt đầu lax (≥1/3) cho new services, tăng dần lên strict (≥2/3) khi đủ data calibrate IF. Cần thêm production data để validate.

## Cost model verdict for my stack

- **ROI:** 0.53
- **Payback:** 1.88 months
- **Verdict:** not_worth_it

Với quy mô hiện tại (20 services, 2 incidents/month, $10k/h downtime), AIOps platform chưa justify $15k/month cost. Recommendation: đầu tư vào observability stack cơ bản (Prometheus + Grafana + alerting rules) + SLO culture + on-call process trước. Khi scale lên ≥ 100 services với downtime cost ≥ $20k/h, ROI tăng lên 3.2 → clearly worth_it.
