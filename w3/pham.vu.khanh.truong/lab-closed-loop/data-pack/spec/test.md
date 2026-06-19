Sau khi bạn đã hoàn thiện mã nguồn Python cho bộ điều phối (`closed_loop.py`) và các script trong thư mục `runbooks/`, bạn có thể tiến hành chạy thử nghiệm hệ thống theo các bước chi tiết dưới đây:

---

## Bước 1: Khởi động toàn bộ Stack hệ thống

Trước tiên, bạn cần kích hoạt môi trường Docker chứa 5 dịch vụ của Ronki cùng với Prometheus và Alertmanager.

1. Mở một cửa sổ Terminal mới.
2. Di chuyển vào thư mục dự án (nơi có thư mục `data-pack` hoặc chứa các script khởi động).
3. Chạy lệnh sau để khởi động:
```bash
bash data-pack/scripts/start_stack.sh

```


4. **Kiểm tra trạng thái hệ thống**: Đảm bảo các dịch vụ đã sẵn sàng bằng cách dùng `curl` kiểm tra các endpoint sau:
* Prometheus: `curl http://localhost:9090/-/healthy`
* Alertmanager: `curl http://localhost:9093/-/healthy`
* API Gateway: `curl http://localhost:8080/health`



---

## Bước 2: Khởi chạy bộ điều phối (Orchestrator)

Khi các dịch vụ nền đã chạy ổn định, bạn tiến hành bật bộ điều phối vòng lặp kín để nó bắt đầu quét và lắng nghe cảnh báo.

1. **Giữ nguyên Terminal 1** (hoặc mở một Terminal mới nếu Terminal cũ đang hiển thị log của Docker).
2. Chạy bộ điều phối bằng công cụ `uv` kết hợp với file cấu hình YAML của bạn:
```bash
uv run python closed_loop.py --config config.yaml

```


> **Mẹo**: Nếu bạn muốn chạy thử nghiệm toàn bộ luồng mà không gây tác động vật lý (không thực sự restart hay can thiệp container), hãy thêm cờ `--dry-run` ở cấp độ orchestrator:
> `uv run python closed_loop.py --config config.yaml --dry-run`



---

## Bước 3: Mô phỏng sự cố để kiểm tra (Inject Fault)

Mở **Terminal 2** để chủ động tạo ra các kịch bản lỗi (chaos scenarios) và quan sát cách bộ điều phối tự động xử lý:

### Kịch bản 1: Thử nghiệm hành động thành công (Lỗi độ trễ)

Bơm lỗi tăng độ trễ 500ms vào dịch vụ thanh toán:

```bash
bash data-pack/scripts/inject_fault.sh latency payment-svc 500ms

```

* **Quan sát Terminal 1**: Bộ điều phối phải bắt được alert `HighLatency`, thực hiện kiểm tra an toàn, gọi `restart_service.sh`, xác minh độ trễ giảm xuống và ghi log `ACTION_SUCCESS`.

### Kịch bản 2: Thử nghiệm hoàn tác (Giết chết dịch vụ)

Tắt hoàn toàn dịch vụ checkout và chặn khởi động lại:

```bash
bash data-pack/scripts/inject_fault.sh kill checkout-svc

```

* **Quan sát Terminal 1**: Bộ điều phối phát hiện `InstanceDown`, tiến hành restart nhưng thất bại (hoặc xác minh thấy container vẫn sập), sau đó tự động kích hoạt quy trình hoàn tác và ghi log `ROLLBACK_TRIGGERED`.

### Kịch bản 3: Thử nghiệm ngắt mạch (Circuit Breaker)

Chạy lệnh inject lỗi liên tiếp 3 lần (theo hướng dẫn trong `data/expected.json`) để cố tình tạo ra 3 lần xác minh thất bại liên tục.

* **Quan sát Terminal 1**: Hệ thống phải tự động ngắt mạch, từ chối nhận thêm hành động và ghi nhận log `CIRCUIT_BREAKER_HALT`.

---

## Bước 4: Theo dõi trực quan trên Dashboard (Tùy chọn)

Nếu bạn muốn nhìn dòng dữ liệu trực quan hơn thay vì đọc log JSON ở Terminal 1:

1. Truy cập vào trình duyệt theo địa chỉ: **`http://localhost:3000`**.
2. Chọn dashboard **"AIOps Closed-Loop"**.
3. Bạn sẽ thấy các biểu đồ về trạng thái Cầu dao (Circuit-breaker), khóa dịch vụ (Mutex state), số lượng hành động thành công/hoàn tác cập nhật theo thời gian thực tương ứng với các kịch bản bạn vừa test ở Bước 3.

---

## Bước 5: Dọn dẹp hệ thống sau khi test xong

Sau khi đã thu thập đủ log để dán vào file `SUBMIT.md`, hãy tắt hệ thống để giải phóng tài nguyên máy bằng cách chạy lệnh:

```bash
bash data-pack/scripts/stop_stack.sh

```

Lệnh này sẽ dừng toàn bộ các container và xóa sạch các ổ đĩa dữ liệu (volumes) đã tạo để đưa môi trường về trạng thái ban đầu.