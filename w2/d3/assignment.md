
## 📋 TỔNG QUAN YÊU CẦU QUY ƯỚC NỘP BÀI

* **Nhánh git:** `main`
* **Đường dẫn thư mục:** `aiops-<tên_của_bạn>/w2/d3/` *(Lưu ý chữ thường `w2` và `d3`)*
* **Danh sách các file bắt buộc phải có (Đúng chính xác tên và định dạng hoa/thường):**
1. `serve.py` — File source code chính chạy FastAPI app.
2. `DESIGN.md` — Tài liệu thiết kế hệ thống (Viết HOA tên file).
3. `SUBMIT.md` — Tài liệu nghiệm thu và trả lời câu hỏi checkpoint (Viết HOA tên file).



---

## 🛠️ ĐẶC TẢ CHI TIẾT CÁC BƯỚC TRIỂN KHAI

### Bước 1: Thiết lập môi trường và cấu trúc Skeleton cho `serve.py`

* **Cài đặt các thư viện cần thiết:** Tạo file `requirements.txt` bao gồm: `fastapi`, `uvicorn[standard]`, `pydantic`, `networkx`, `pandas`, `openai`, `prometheus-client`, `pytest`.
* **Định nghĩa Schema dữ liệu đầu vào (`IncidentRequest`) bằng Pydantic:**
* Nhận một danh sách (list) các `Alert`.
* Mỗi `Alert` bắt buộc chứa đúng **8 fields**: `id`, `ts`, `service`, `metric`, `severity`, `value`, `threshold`, `labels`.


* **Định nghĩa Schema dữ liệu đầu ra (`IncidentResponse`) bằng Pydantic:**
* Bao gồm các trường: `clusters` (list), `root_cause` (object/dict), `recommended_actions` (list), `similar_incidents` (list).



### Bước 2: Xây dựng các Endpoint và Middleware nền tảng

* **Endpoint Liveness (`GET /healthz`):** Trả về `{"status": "ok"}` ngay lập tức để Load Balancer kiểm tra tiến trình (process) còn sống. Không thực hiện validate hay gọi DB/Network tại đây.
* **Middleware đo Latency:**
* Viết một FastAPI middleware bao quanh hàm xử lý request bằng cách sử dụng `time.perf_counter()`.
* Gắn thời gian xử lý vào Response Header với key `X-Response-Time-Ms`.
* Ghi log có cấu trúc (Structured Log) dưới dạng: `{method} {path} {status} {duration_ms}`.



### Bước 3: Tích hợp Service Graph và Khởi tạo dữ liệu (Glue Layer)

* Tạo file `pipeline.py` (hoặc tích hợp trực tiếp vào module xử lý) để load dữ liệu **một lần duy nhất ở module-level** khi khởi chạy ứng dụng (giúp tối ưu hóa tốc độ, tránh reload trên từng request):
* Load topology mạng từ `dataset/services.json` vào biến toàn cục `GRAPH` (kiểu dữ liệu `networkx.DiGraph`).
* Load lịch sử sự cố từ `dataset/incidents_history.json` vào biến toàn cục `HISTORY`.


* **Endpoint `/version` (GET):** Trả về metadata để hỗ trợ rollback/versioning gồm: `app` version, `graph_version`, `graph_loaded_at`, `graph_source`, `graph_node_count`, `graph_edge_count` và các cấu hình pipeline (`gap_sec`, `max_hop`, `rca_method`, `llm_model`).

### Bước 4: Khớp nối 3 Layer thành Pipeline hoàn chỉnh (`POST /incident`)

* **Xử lý Logic tại Endpoint:**
* Nhận request từ client, chuyển đổi Pydantic model sang dạng dictionary thuần bằng phương thức `.model_dump()`.
* Kiểm tra nếu danh sách `alerts` truyền vào bị rỗng $\rightarrow$ Lập tức ném lỗi `HTTPException(400)` (Bad Request).


* **Luồng xử lý tuần tự (Flow):**
1. Gọi hàm định tuyến gom cụm `correlate(alerts, GRAPH, gap_sec=120, max_hop=2)` từ Layer 1 để lấy danh sách các cluster. Nếu không có cluster nào $\rightarrow$ Trả về sớm (Return early) với `root_cause = "unknown"`.
2. Chọn cụm lớn nhất (có `alert_count` cao nhất) làm sự cố chính (primary incident).
3. Gọi hàm tìm nguyên nhân gốc rễ `run_rca(primary, alerts, GRAPH, HISTORY)` từ Layer 2 để lấy thông tin chi tiết (`root_cause`, `confidence`, `actions`, `similar_incidents`).
4. Bọc dữ liệu trả về theo đúng định dạng `IncidentResponse`.


* **Xử lý ngoại lệ (Failure Handling):** Bọc toàn bộ pipeline trong khối `try/except`. Nếu xảy ra lỗi hệ thống, dùng `logger.error` với `exc_info=True` để lưu lại vết stack trace ở phía server, đồng thời trả về lỗi `HTTPException(500)` với thông báo ngắn gọn, tuyệt đối **không leak mã nguồn/stack trace ra client**.

### Bước 5: Viết tài liệu thiết kế `DESIGN.md`

Tài liệu này phải có dung lượng **tối thiểu từ 100 từ trở lên** và làm rõ các nội dung sau:

* **Sơ đồ/Kiến trúc của pipeline** bên trong endpoint.
* **Phân rã ngân sách thời gian phản hồi (Latency budget breakdown)** dựa trên ước lượng hoặc đo đạc thực tế.
* **Giải pháp cho bài toán Production thực tế:** Chọn phân tích sâu vào 1 trong 2 chủ đề: *Khả năng xử lý đồng thời (Concurrency)* hoặc *Khả năng chịu lỗi (Fault tolerance)* và cách bạn thiết kế mã nguồn để đáp ứng.
* **Đánh giá Trade-off:** Lý do cụ thể vì sao bạn lựa chọn framework FastAPI thay vì Flask hay BentoML cho bài toán này.

### Bước 6: Hoàn thiện nghiệm thu và trả lời EOD Checkpoint trong `SUBMIT.md`

Tiến hành chạy kiểm thử ứng dụng cục bộ bằng lệnh:

```bash
uvicorn serve:app --host 0.0.0.0 --port 8000 --workers 1

```

Sau đó, trả lời chi tiết 3 câu hỏi sau trong `SUBMIT.md` dựa trên dữ liệu bạn **thực tế đo đạc và quan sát được**:

1. **Đo lường Latency:** Thực hiện bắn liên tiếp 20 request với dataset gồm 20 alert thật. Trích xuất giá trị p50 và p99 từ header `X-Response-Time-Ms`. Phân tích xem giai đoạn nào (validate / correlate / RCA / LLM / serialize) chiếm tỷ trọng thời gian lớn nhất? Khi lượng alert đầu vào tăng lên gấp 10 lần ($10\times$), giai đoạn nào sẽ tăng tiến tính (linear scale) và giai đoạn nào có chi phí cố định (fixed cost)?
2. **Kiểm thử khả năng chịu tải (Concurrency & Fallback):** Sử dụng các công cụ kiểm thử như Apache Bench (`ab`), `wrk`, hoặc script Python `ThreadPoolExecutor` để bắn đồng thời cấu hình `4 request concurrent` (Tổng số 20 request) vào endpoint:
```bash
ab -n 20 -c 4 -p body.json -T application/json http://localhost:8000/incident

```


Điểm nghẽn (bottleneck) đầu tiên bạn phát hiện là gì? Trong trường hợp LLM Provider gặp sự cố (down), hệ thống của bạn xử lý ra sao? Bạn đã cấu hình cơ chế chuyển mạch an toàn (fallback path/kill switch) như thế nào?
3. **Tách biệt Health Check:** Trong mã nguồn bạn đang kiểm tra những thành phần gì cho endpoint `/healthz` và `/readyz`? Tại sao việc tách biệt 2 endpoint này lại quan trọng thay vì gộp chung làm một? Khi LLM API phía bên thứ 3 bị sập, endpoint `/readyz` của bạn sẽ trả về trạng thái Thất bại (Fail) hay Vượt qua (Pass)? Hãy giải thích rõ lý do lựa chọn của bạn.

---

## 🎯 TIÊU CHÍ NGHIỆM THU (ACCEPTANCE CRITERIA)

* [ ] Ứng dụng khởi chạy thành công, mượt mà và không vấp lỗi trên môi trường local (máy cấu hình yếu vẫn chạy được với tham số `--workers 1`).
* [ ] Lệnh `curl http://localhost:8000/healthz` trả về đúng mã `200` và JSON `{"status":"ok"}`.
* [ ] Gửi một payload alerts hợp lệ qua phương thức `POST /incident` phải nhận về HTTP code `200` kèm cấu trúc body chuẩn đầy đủ các trường `clusters`, `root_cause`, `recommended_actions`.
* [ ] Gửi payload sai cấu trúc hoặc thiếu trường dữ liệu bắt buộc phải trả về mã lỗi `422 Unprocessable Entity` do Pydantic tự động bắt, **tuyệt đối không để phát sinh lỗi lọt lưới 500 Internal Server Error**.
* [ ] File `DESIGN.md` đạt trên 100 từ, có các luận điểm kỹ thuật rõ ràng và các quyết định thiết kế mang tính thực tế cao (ví dụ: giải thích lý do cấu hình tham số `gap_sec=120s`).