# DESIGN.md - Ronki Closed-Loop Orchestrator (Custom Implementation)

## 1. Decision engine: Rule-based hay LLM-based?
**Lựa chọn:** Rule-based.
**Lý do:**
- **Tính Deterministic:** Với 3 luật cố định (`HighLatency`, `HighErrorRate`, `InstanceDown`), việc ánh xạ trực tiếp sang các shell script giúp hệ thống đạt độ tin cậy tuyệt đối và thời gian phản hồi (latency) cực thấp (<1ms) mà không phụ thuộc vào độ trễ của API hay rủi ro ảo giác (hallucination) của LLM.
- **Tính khả thi:** Rule-based vẫn có thể chặn được các request không hợp lệ thông qua việc đối chiếu với `runbook_registry`.

## 2. Blast-radius config
**Cấu hình:**
```yaml
blast_radius:
  max_actions_per_minute: 3
  max_restarts_per_service_per_hour: 5
```
**Lý do:**
- Mức 3 hành động mỗi phút là đủ nhỏ để ngăn chặn *thundering herd* khi có báo động đồng loạt ở cả 5 dịch vụ, nhưng vẫn đủ linh hoạt để xử lý các vấn đề mạng nhanh chóng.
- Giới hạn 5 lần khởi động lại cho 1 dịch vụ trong 1 giờ giúp ngăn vòng lặp lỗi không thể phục hồi (như lỗi cấu hình, thiếu dependency), chuyển quyền xử lý cho con người (Circuit Breaker).

## 3. Verify step
**Metric kiểm tra:** 
1. `latency_p99` (ms)
2. `up` (boolean, 1 hoặc 0)

**Threshold & Timeout:**
- Thời gian Timeout là 60s, cho phép Docker compose restart và metrics ổn định lại sau ~15-20s.
- Polling diễn ra 10s một lần, khớp với interval scrape của Prometheus. 
- Yêu cầu `verify_min_samples = 3` để ngăn chặn hiện tượng pass ảo do Prometheus có độ trễ hoặc mới update. P99 Latency phải thấp hơn ngưỡng `500ms` và trạng thái `up` phải bằng `1`.

## 4. Circuit breaker reset
**Cơ chế Reset:** Bán tự động. Biến đếm `consecutive_failures` được làm mới về 0 nếu một hành động Act -> Verify thành công (ACTION_SUCCESS). Nếu biến đếm này chạm mức 3 liên tiếp, hệ thống vào trạng thái HALT đối với service đó cho tới khi kỹ sư can thiệp khởi động lại ứng dụng. Điều này ngăn việc rollback liên tục gây cạn kiệt tài nguyên.
