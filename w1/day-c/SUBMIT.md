# Bài Nộp Assigment W1-D3

## Sơ đồ kiến trúc (Architecture Diagram)

Kiến trúc Data Layer End-to-End được thiết kế cho việc Phát hiện Bất thường trên một Payment Service (Dịch vụ Thanh toán).

![Sơ đồ kiến trúc](architecture.md)

*Lưu ý: Vui lòng xem tệp [architecture.md](file:///d:/Codespace-Learning/X_Brain/aiops-truong/w1/day-c/architecture.md) để xem chi tiết biểu đồ Mermaid vẽ lại toàn bộ Service, Collection, Transport, Processing, Storage, và Query/ML layer.*

## Ước tính chi phí (Cost Estimation)

Dưới đây là bảng phân tích chi phí cho các mức độ mở rộng khác nhau (tiers) dựa trên đầu ra từ `cost_model.py`:

| Tier   |   Services |   Log(GB/day) |   Metric(events/sec) |   Build_Storage_Log($) |   Build_Storage_Metric($) |   Build_Compute($) |   Total_Build($) |   Total_Buy_SaaS($) |
|:-------|-----------:|--------------:|---------------------:|-----------------------:|--------------------------:|-------------------:|-----------------:|--------------------:|
| Small  |         10 |            50 |               100000 |                    450 |                       200 |                370 |             1020 |                3060 |
| Medium |        100 |           500 |              1000000 |                   4500 |                      2000 |               3700 |            10200 |               40000 |
| Large  |       1000 |          5000 |             10000000 |                  45000 |                     20000 |              37000 |           102000 |              459000 |

## Tóm tắt quyết định ADR

**ADR-001**: Đưa Apache Kafka vào làm Transport Layer (Tầng Vận chuyển) giữa OpenTelemetry Collector và Storage.
- **Bối cảnh**: Các đợt lưu lượng log tăng vọt gây ra tình trạng rớt event trong Elasticsearch, làm giảm độ chính xác của mô hình phát hiện bất thường.
- **Quyết định**: Đặt một Kafka cluster vào giữa để đóng vai trò làm buffer (bộ đệm) bền bỉ.
- **Hậu quả**: Cải thiện độ tin cậy (không còn bị rớt event), tách biệt quá trình xử lý (cho phép dễ dàng gắn thêm các Flink pipeline), tăng độ trễ lên một chút (+10-20ms), và giữ chi phí ròng ở mức trung hòa vì tiết kiệm được tiền nâng cấp storage vào giờ cao điểm nhưng lại bù vào tiền infra cho Kafka. Đội ngũ SRE phải gánh thêm công sức vận hành Kafka.

## Reflection (Suy ngẫm): Build hay Buy

**Kịch bản**: Bạn là Platform Engineer cho một startup có 50 service và vừa mới gọi vốn Series A thành công.

**Khuyến nghị**: **BUY** (ví dụ: Datadog, New Relic).

**Lý do?**
1. **Time to Market & Sự tập trung**: Ở giai đoạn Series A, ưu tiên hàng đầu của đội ngũ kỹ sư là phát hành các tính năng sản phẩm để tìm kiếm product-market fit hoặc mở rộng doanh thu, chứ không phải là xây dựng các công cụ nội bộ. Việc xây dựng một observability stack tùy chỉnh tốn từ 3-6 tháng.
2. **Hạn chế về đội ngũ**: Việc vận hành Kafka, Elasticsearch, và Flink đòi hỏi ít nhất 2-3 kỹ sư SRE chuyên trách. Các startup Series A hiếm khi có đủ nguồn lực nền tảng chuyên trách như vậy.
3. **Yếu tố chi phí**: Mặc dù chi phí hạ tầng thô của giải pháp "Build" (Tự xây) thấp hơn, nhưng chi phí nhân sự cho 2 SRE vượt xa mức phí đăng ký SaaS $15K-$25K. Mua (Buy) cơ bản là mang lại hiệu quả sử dụng vốn cao hơn ở giai đoạn này. Chúng ta có thể xem xét lại phương án "Build" khi đạt mức ~500+ service và lúc đó chi phí SaaS sẽ trở thành một nỗi đau lớn.
