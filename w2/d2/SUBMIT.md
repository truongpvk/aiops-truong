### Câu 1: Confidence của top-1 trong cluster lớn nhất bạn xử lý là bao nhiêu? Nếu phải set threshold để auto-rollback (không cần SRE confirm), bạn pick số nào? Lý do? 
Trong cụm lớn nhất `c-000-000` (12 alerts), ứng viên top-1 `edge-lb` có độ tin cậy (`confidence`) chỉ đạt **0.28**. Nếu cấu hình auto-rollback, tôi chọn ngưỡng **≥ 0.80**. Lý do là mức 0.28 quá thấp do hiện tượng bão cảnh báo (alert storm), khiến điểm của `edge-lb` (1.0) và `checkout-svc` (0.93) bám sát nhau vì lỗi dây chuyền. Đặt ngưỡng cao giúp hệ thống tránh tự động hạ cấp sai lầm gây gián đoạn hạ tầng; các ca độ tự tin thấp dưới 0.80 bắt buộc phải qua kỹ sư SRE phê duyệt thủ công.

---

### Câu 2: Variant bạn chọn cho classifier (A rule-based / B free LLM / C paid LLM). Chạy thực tế ra sao? Trade-off với variant bạn không chọn?
Tôi chọn **Variant A (Rule-based/Heuristic retrieval)**. Chạy thực tế hệ thống hoạt động ổn định, đối sánh chính xác cụm lỗi lớn với sự cố lịch sử `INC-2025-11-08` (tương đồng 1.0), chỉ ra đúng lớp lỗi `connection_pool_exhaustion` và hành động mở rộng pool. 
**Đánh đổi:** Variant A mang lại phản hồi tức thì bằng mili-giây, chi phí bằng không và không bị hiện tượng ảo tưởng (hallucination) như LLM (Variant B/C). Tuy nhiên, nhược điểm là tính linh hoạt thấp, sẽ phải fallback gán nhãn `other` khi gặp dạng sự cố mới chưa từng có trong lịch sử.

---

### Câu 3: Đọc bảng Industry landscape (§6) — pipeline bạn xây gần product nào nhất? Trong domain GeekShop (e-commerce, alert volume cao, service map tương đối ổn định), lựa chọn đó hợp lý hay nên đổi?

 Pipeline này gần nhất với **Dynatrace Davis AI** vì đều dùng bản đồ topo dịch vụ (service graph) làm chân lý để duyệt và truy vết ngược dòng tìm nguyên nhân gốc. Lựa chọn này **hoàn toàn hợp lý** cho domain GeekShop. Do sơ đồ dịch vụ e-commerce tương đối cố định, giải thuật đồ thị chạy rất nhẹ mà không cần học quan hệ nhân quả động phức tạp. Đồng thời, khi lượng alert bùng phát cực lớn lúc cao điểm, tốc độ xử lý dưới 1 giây giúp SRE cô lập lỗi ngay lập tức thay vì chờ đợi tính toán chuỗi thời gian lâu dài.