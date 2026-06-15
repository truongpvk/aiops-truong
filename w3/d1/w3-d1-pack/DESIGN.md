# DESIGN

1. **SLI choice cho frontend.**  
Tôi chọn kết hợp các tín hiệu: DOM ready time (< 3000ms) và không có JS error, Network error. DOM ready phản ánh chính xác thời điểm user bắt đầu tương tác được với trang web (tốt hơn Page Load Time có thể bị block bởi các resource không quan trọng). JS/Network error trực tiếp làm hỏng trải nghiệm người dùng. Theo `baseline.json`, `dom_ready_p99_ms` là 1430ms nên mốc 3000ms là hợp lý để chặn những case quá chậm.

2. **SLO target cho api.**  
Dựa trên `baseline.json`, tỷ lệ fail_rate của api chỉ là 0.003488 (tương đương non-fail rate ~99.65%). Nếu đặt SLO 99.9%, hệ thống sẽ cần nâng cấp kiến trúc lên Multi-instance, auto-failover (Tier 2/3) khiến chi phí infra và headcount tăng 3-10 lần (theo §3.2). Tuy nhiên, 99.9% là cần thiết cho Tier-1 service để đảm bảo user pain ở mức tối thiểu. Việc đặt 99.99% sẽ quá khắt khe và tốn kém không cần thiết khi baseline hiện tại chưa đạt tới mức đó.

3. **Latency threshold p99.**  
Trong `baseline.json`, `latency_p99_ms` của API đang đạt 156ms. Việc chọn mốc cut-off ở 500ms (như trong compute_baseline) là một mức hợp lý vì nó đủ rộng để buffer những minor spike nhưng vẫn bắt được các sự cố degrade nghiêm trọng khiến API chậm hơn hẳn mức p99. Mốc 200ms có thể quá gắt và sinh ra false positive khi hệ thống có slight degradation.

4. **4xx exclusion.**  
Các lỗi 4xx (như 400 Bad Request, 401 Unauthorized) là lỗi từ phía user, không đại diện cho system failure (ngoại trừ 429 Rate Limit do system reject). Nếu tính cả 4xx vào lỗi, SLI sẽ bị giảm sai lệch bởi các bot/scraper quét nhầm endpoint. Thực tế từ `baseline.json`, `success_rate` của API chỉ là 97.6% (do đã trừ 4xx), trong khi `fail_rate` (5xx, 429) chỉ có 0.35%. Điều này chứng tỏ có tới hơn 2% traffic là 4xx thông thường.

5. **MWMBR tuning.**  
Thay vì dùng Google default (1h/5m cho Tier 1), tôi đã tune thành 5m/1m với threshold 14.4. Điều này là do các sự cố trong `incident_window.csv` diễn ra rất ngắn (chỉ 8-12 phút). Nếu dùng 1h, alert sẽ fire quá chậm do phải chờ trung bình 1h tăng lên. Sau khi tune, kết quả ở `validation_report.json` rất tốt: `noise_reduction_pct` đạt 86.4%, `mttd_delta_s` giảm xuống còn 0 giây (fire ngay lập tức) và `fn = 0` (không bỏ sót incident nào).
