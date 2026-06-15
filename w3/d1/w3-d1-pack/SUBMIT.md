# W3-D1 Submission — Antigravity

## 3 thứ tôi học được
1. Phân biệt rõ ràng giữa SLI, SLO và SLA. SLO là cam kết nội bộ, cần có buffer so với thực tế hệ thống và lỏng hơn so với SLA để không bị phạt tiền.
2. Việc sử dụng Multi-Window Multi-Burn-Rate (MWMBR) giúp giải quyết vấn đề của alert dựa trên 1 window đơn (quá ồn với window ngắn và quá chậm với window dài).
3. Không phải error nào cũng nên tính vào fail. Ví dụ các mã 4xx (không phải 429) chủ yếu là lỗi từ phía user và nếu tính vào sẽ làm nhiễu SLO.

## 1 thứ vẫn chưa rõ
Cách kết hợp MWMBR với anomaly detection khi service có tính chu kỳ (traffic đêm quá thấp so với ban ngày) liệu có làm tỷ lệ error bị khuếch đại vào ban đêm khi có 1-2 request fail hay không?

## 1 trade-off trong SLO decision của tôi mà tôi không chắc
Tôi đã quyết định thay đổi window của Tier 1 xuống còn 5m/1m thay vì dùng mặc định của Google (1h/5m). Việc này giúp giảm MTTD xuống 0 giây cho các sự cố ngắn và đạt chuẩn validation, tuy nhiên ở thực tế có thể sinh ra false positive nếu hệ thống có những spike lỗi diễn ra trong chưa tới 1 phút.

## Validation report
- noise_reduction_pct: 86.4%
- mttd_delta_s: 0s
- false_negative: 0
- verdict: pass
