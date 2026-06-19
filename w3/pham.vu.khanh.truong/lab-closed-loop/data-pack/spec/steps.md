Để xây dựng và hoàn thành bài thực hành tự động khắc phục sự cố theo vòng lặp kín này, bạn cần thực hiện theo một quy trình tuần tự từ việc thiết lập môi trường, viết các kịch bản tự động hóa (runbooks), phát triển bộ điều phối chính bằng Python, cho đến cấu hình và chạy các kịch bản kiểm thử.

Dưới đây là mô tả chi tiết từng bước thực hiện:

---

## Bước 1: Thiết lập môi trường và cấu trúc thư mục

Trước tiên, bạn cần tạo đúng cấu trúc thư mục như yêu cầu của bài thực hành để đảm bảo tính tổ chức và chuẩn bị cho việc nộp bài.

1. **Tạo thư mục làm việc**: Tạo một thư mục mang tên bạn (ví dụ: `nguyen-van-a/`).
2. **Tạo các thư mục con**: Bên trong thư mục chính, tạo thư mục `runbooks/`.
3. **Cài đặt thư viện Python**: Khởi tạo môi trường Python (khuyến khích dùng `uv`) và cài đặt các thư viện bắt buộc bằng lệnh:
```bash
uv pip install requests pyyaml prometheus_client

```



---

## Bước 2: Xây dựng các kịch bản tự động hóa (`runbooks/*.sh`)

Bạn cần viết tối thiểu 3 script shell để thực hiện các hành động khắc phục. Các script này phải tuân thủ nghiêm ngặt hai quy tắc: hỗ trợ cờ `--dry-run` và cờ `--service <tên_dịch_vụ>`.

### 1. Script khởi động lại dịch vụ (`runbooks/restart_service.sh`)

* **Nhiệm vụ**: Sử dụng lệnh Docker Compose để khởi động lại container của dịch vụ bị lỗi.
* **Logic**:
* Kiểm tra nếu có cờ `--dry-run`: In ra chuỗi `[DRY-RUN] would execute: docker compose restart <service_name>` và thoát với code `0`.
* Nếu không có cờ `--dry-run`: Chạy lệnh `docker compose restart <service_name>`. Nếu lệnh thành công trả về `0`, ngược lại trả về mã lỗi (non-zero).



### 2. Script xóa bộ nhớ đệm (`runbooks/clear_cache.sh`)

* **Nhiệm vụ**: Giả lập hoặc thực hiện lệnh xóa cache (ví dụ: gọi một API xóa cache hoặc restart redis/memcached liên quan đến dịch vụ).
* **Logic**: Tương tự như trên, xử lý cờ `--dry-run` trước, sau đó mới đến lệnh thực tế.

### 3. Script mở rộng bản sao (`runbooks/scale_replicas.sh`)

* **Nhiệm vụ**: Tăng số lượng instance của một dịch vụ để giảm tải.
* **Logic**: Xử lý logic `--dry-run` và lệnh thực tế `docker compose up -d --scale <service_name>=<number>`.

---

## Bước 3: Tạo file cấu hình `config.yaml`

Tránh việc viết cứng (hardcode) các thông số vào mã nguồn Python. Bạn cần tạo file `config.yaml` chứa các thông tin sau:

* **Alertmanager URL**: `http://localhost:9093`.
* **Prometheus URL**: `http://localhost:9090`.
* **Blast-radius**: Cấu hình `max_actions_per_minute` và `max_restarts_per_service_per_hour`.
* **Runbook Mapping**: Ánh xạ từ `alertname` sang đường dẫn script:
```yaml
runbook_map:
  HighLatency: "runbooks/restart_service.sh"
  HighErrorRate: "runbooks/clear_cache.sh"
  InstanceDown: "runbooks/restart_service.sh"

```


* **Registry**: Danh sách các runbook hợp lệ để phục vụ bước kiểm tra an toàn (chống hallucination).

---

## Bước 4: Phát triển bộ điều phối chính (`closed_loop.py`)

Đây là trái tim của hệ thống. Bạn cần lập trình bằng Python để thực hiện vòng lặp 5 bước an toàn (Detect $\rightarrow$ Decide $\rightarrow$ Act $\rightarrow$ Verify $\rightarrow$ Rollback).

### 1. Khởi tạo và Khởi động Metrics Server

* Import `prometheus_client` và gọi `start_metrics_server()` để đẩy số liệu lên Grafana dashboard.
* Đọc file cấu hình `config.yaml`.

### 2. Bước 1: Phát hiện (DETECT)

* Thiết lập một vòng lặp vô hạn, sử dụng thư viện `requests` để gọi API Alertmanager (`http://localhost:9093/api/v2/alerts`) định kỳ mỗi 15 giây.
* Phân tích cú pháp (parse) dữ liệu JSON nhận được để trích xuất: `alertname`, `service` (từ nhãn/labels của alert), và `severity`.

### 3. Bước 2: Quyết định (DECIDE) & Kiểm tra an toàn

* **Khớp Runbook**: Tìm kiếm script tương ứng từ `runbook_map` dựa trên `alertname`.
* **Phòng chống Hallucination**: Kiểm tra xem script đó có nằm trong danh mục hợp lệ (`runbook_registry`) không. Nếu không, chặn lại, ghi log `DECISION_VALIDATION_FAILED` và không tăng biến đếm lỗi.
* **Kiểm tra Vùng ảnh hưởng (Blast-radius)**: Kiểm tra lịch sử thực thi gần đây để đảm bảo không vượt quá số hành động/phút hoặc số lần restart/giờ. Nếu vượt quá, chuyển tiếp (escalate) và dừng lại.
* **Khóa dịch vụ (Mutex Lock)**: Sử dụng một cơ chế khóa (ví dụ: một tập hợp `set` lưu các dịch vụ đang xử lý) để đảm bảo nếu một dịch vụ đang chạy runbook, các cảnh báo trùng lặp tiếp theo sẽ bị từ chối với log `SERVICE_LOCK_BUSY`. Các dịch vụ khác nhau phải chạy song song mà không chặn nhau.

### 4. Bước 3: Chạy thử (DRY-RUN)

* Gọi script runbook bằng thư viện `subprocess` của Python kèm theo tham số `--dry-run` và `--service <name>`.
* Nếu exit code của bước dry-run $\neq 0$, hủy bỏ hành động và ghi log lỗi.

### 5. Bước 4: Hành động (ACT)

* Nếu bước chạy thử vượt qua, tiến hành gọi lại script runbook đó nhưng **không** truyền cờ `--dry-run`.
* Thiết lập một khoảng thời gian chờ (timeout) cho lệnh `subprocess` để tránh việc script bị treo vô hạn.

### 6. Bước 5: Xác minh (VERIFY) & Hoàn tác (ROLLBACK)

* **Xác minh**: Đọc file `baseline.json` để lấy ngưỡng chuẩn. Dùng `requests` gọi API Prometheus để truy vấn metric của dịch vụ đó ít nhất 3 lần trong vòng 60 giây.
* **Xử lý kết quả**:
* **Nếu Đạt (PASS)**: Giải phóng khóa dịch vụ, ghi log `ACTION_SUCCESS`, reset bộ đếm lỗi liên tiếp về 0.
* **Nếu Lỗi (FAIL)**: Kích hoạt ngay lập tức **Quy trình hoàn tác (ROLLBACK)** bằng cách gọi script hoàn tác tương ứng. Tăng biến đếm lỗi liên tiếp (`failure_count`).
* **Cầu dao ngắt mạch (Circuit Breaker)**: Nếu `failure_count >= 3`, chuyển trạng thái hệ thống thành `CIRCUIT_OPEN`, dừng toàn bộ tiến trình tự động hóa và ghi log `CIRCUIT_BREAKER_HALT`.



---

## Bước 5: Chạy hệ thống và Thực hiện 3 kịch bản hỗn loạn ban đầu

Sau khi code xong, bạn tiến hành nghiệm thu thực tế:

1. **Khởi động Stack**: Chạy lệnh `bash data-pack/scripts/start_stack.sh` để bật toàn bộ dịch vụ, Prometheus và Alertmanager.
2. **Chạy bộ điều phối**: Mở Terminal 1 và chạy `uv run python closed_loop.py --config config.yaml`.
3. **Kiểm thử Kịch bản 1 (Thành công)**: Mở Terminal 2, chạy lệnh inject lỗi latency vào `payment-svc`. Theo dõi Terminal 1 để đảm bảo bộ điều phối tự bắt được alert, chạy thử, restart dịch vụ, xác minh thành công và ghi log `ACTION_SUCCESS`.
4. **Kiểm thử Kịch bản 2 (Hoàn tác)**: Inject lỗi giết chết `checkout-svc`. Bộ điều phối sẽ restart thất bại, bước xác minh không đạt, tự động trigger rollback và ghi log `ROLLBACK_TRIGGERED`.
5. **Kiểm thử Kịch bản 3 (Cầu dao)**: Inject lỗi liên tiếp 3 lần theo hướng dẫn. Kiểm tra xem hệ thống có ngắt mạch và log ra `CIRCUIT_BREAKER_HALT` hay không.

---

## Bước 6: Phát triển nâng cao và Kiểm thử áp lực (Để đạt điểm Xuất sắc)

Để đạt điểm tối đa, bạn cần tinh chỉnh code của `closed_loop.py` và cấu hình thêm trong `config.yaml` để vượt qua 3 bài kiểm tra áp lực nâng cao:

1. **Xử lý Triển khai nhiều bước (Kịch bản 4)**: Cấu hình ánh xạ một mảng các bước (ví dụ: Step A $\rightarrow$ Step B $\rightarrow$ Step C) trong YAML. Viết logic nếu Step C lỗi, hệ thống phải chạy lệnh hoàn tác ngược lại (Rollback B $\rightarrow$ Rollback A) theo mô hình giao dịch (transactional).
2. **Kiểm tra Đua đồng thời (Kịch bản 5)**: Sử dụng luồng (Threading hoặc Asyncio trong Python) để khi nhận được nhiều alert cùng lúc, bộ điều phối xử lý song song chúng trên các tiến trình độc lập, đồng thời kích hoạt cơ chế `SERVICE_LOCK_BUSY` nếu trùng dịch vụ.
3. **Bảo vệ chống ảo tưởng LLM (Kịch bản 6)**: Viết hàm nghiêm ngặt để so sánh kết quả trả về (dù từ file quy tắc hay từ gọi API Claude) với danh sách các runbook thực tế có trên đĩa máy tính trước khi thực thi.

---

## Bước 7: Hoàn thiện tài liệu và Nộp bài

1. **Viết file `DESIGN.md**`: Trả lời chi tiết 4 câu hỏi trong yêu cầu (đưa ra các con số cụ thể về ngưỡng, thời gian timeout, lý do chọn giải pháp rule-based hay LLM).
2. **Viết file `SUBMIT.md**`: Sao chép toàn bộ các đoạn log JSON có cấu trúc (structured JSON logs) sinh ra từ stdout trong quá trình bạn chạy thành công các kịch bản kiểm thử vào file này.
3. **Đóng gói**: Đảm bảo cấu trúc thư mục sạch sẽ, đúng tên và sẵn sàng nộp bài.