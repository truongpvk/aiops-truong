Dưới đây là nội dung chi tiết của tệp **`DESIGN.md`** được thiết kế hoàn chỉnh, bám sát cấu trúc mã nguồn trong `serve.py` và đáp ứng toàn bộ các tiêu chí kỹ thuật trong đặc tả yêu cầu của bạn.

---

# 1. KIẾN TRÚC PIPELINE XỬ LÝ SỰ CỐ (PIPELINE ARCHITECTURE)

Hệ thống Core Serving Engine tiếp nhận chuỗi Batch Alerts phân tán thông qua một Pipeline xử lý tuần tự khép kín, được chia làm các tầng xử lý biệt lập nhằm tối ưu hóa tính mô-đun và hiệu năng tính toán tại Endpoint `POST /incident`.

Sơ đồ tuần tự của dòng dữ liệu bên trong hệ thống được cấu trúc như sau:

```text
[ Client Request ] 
       │
       ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 0. TẦNG KHỚP NỐI & KIỂM TRA (VALIDATION LAYER)                         │
│    - Pydantic tự động validate cấu trúc 8 trường dữ liệu bắt buộc.     │
│    - Thực hiện chuyển đổi sang Dictionary thuần (.model_dump()).       │
│    - Kiểm tra danh sách rỗng (Empty Guard) -> Ném lỗi 400 khẩn cấp.    │
└────────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 1. TẦNG ĐỊNH TUYẾN GOM CỤM (CORRELATION LAYER - LAYER 1)               │
│    - Gom cụm theo thời gian (Sliding Window Gap: gap_sec=120).         │
│    - Gom cụm theo khoảng cách hạ tầng mạng (Union-Find: max_hop=2).    │
│    - Trích xuất Primary Cluster (Cụm có mật độ bùng nổ alert cao nhất) │
└────────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────────── ┐
│ 2. TẦNG SUY DIỄN NGUYÊN NHÂN GỐC RỄ (RCA ENGINE LAYER - LAYER 2)        │
│    - Đảo ngược đồ thị hạ tầng (Reverse Topology Graph Traversal).       │
│    - Tính toán trọng số kết hợp: 0.6 * PageRank + 0.4 * Temporal Score. │
│    - Định vị Candidate Root Cause & Tính toán mức độ tự tin (Confidence)│
│    - Truy vấn kNN Heuristic đối sánh với dữ liệu lịch sử (HISTORY DB).  │
└──────────────────────────────────────────────────────────────────────── ┘
       │
       ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 3. TẦNG ĐÓNG GÓI & PHẢN HỒI (OUTPUT & SAFE FAIL-SAFE LAYER)            │
│    - Bọc dữ liệu trả về theo schema IncidentResponse chuẩn.            │
│    - Khối try/except bảo vệ hệ thống tuyệt đối không rò rỉ mã nguồn.   │
└────────────────────────────────────────────────────────────────────────┘
       │
       ▼
[ Client Response ] (X-Response-Time-Ms / Prometheus Metrics Logged)

```

---

# 2. PHÂN RÃ NGÂN SÁCH THỜI GIAN PHẢN HỒI (LATENCY BUDGET BREAKDOWN)

Để đáp ứng các yêu cầu khắt khe của hệ thống giám sát thời gian thực (AIOps), tổng ngân sách thời gian phản hồi mục tiêu cho mỗi request tiêu chuẩn (dưới 50 alerts) được thiết lập nghiêm ngặt trong khoảng **$30\text{ms} - 50\text{ms}$**.

Dưới đây là bảng phân rã chi tiết thời gian xử lý dự kiến của từng giai đoạn trong Pipeline:

| Giai đoạn xử lý (Pipeline Stage) | Thời gian ước lượng (ms) | Tỷ trọng (%) | Bản chất chi phí thuật toán (Complexity Scale) |
| --- | --- | --- | --- |
| **Ingress Validation & Guard** | $1.0 - 2.0\text{ms}$ | $\sim 4\%$ | Tuyến tính theo số lượng alert: $O(N)$ |
| **Correlation Layer (Layer 1)** | $5.0 - 12.0\text{ms}$ | $\sim 24\%$ | Sắp xếp và Union-Find: $O(N \log N + V \cdot \log V)$ |
| **RCA Engine Layer (Layer 2)** | $15.0 - 30.0\text{ms}$ | $\sim 60\%$ | Thuật toán PageRank đảo và kNN Heuristic: $O(I \cdot M)$ |
| **Serialization & Middleware** | $1.0 - 3.0\text{ms}$ | $\sim 6\%$ | Định dạng JSON và xuất log cấu trúc: $O(C)$ |
| **Tổng mục tiêu (Target)** | **$< 50\text{ms}$** | **$100\%$** | **Hiệu năng xử lý tối ưu trên bộ nhớ (In-Memory)** |

> *Ghi chú:*
> * $N$: Số lượng cảnh báo đầu vào.
> * $V$: Số lượng node dịch vụ xuất hiện trong cụm.
> * $I$: Số lượng vòng lặp hội tụ của thuật toán PageRank trên phân vùng đồ thị con.
> * $M$: Kích thước tập dữ liệu lịch sử sự cố (`HISTORY`).
> 
> 

---

# 3. KHẢ NĂNG CHỊU LỖI (FAULT TOLERANCE)

* **Cơ chế nạp dữ liệu cô lập kết hợp Dự phòng cục bộ (Fallback Stub):** Đồ thị mạng lưới (`GRAPH`) và lịch sử sự cố (`HISTORY`) được tải duy nhất **một lần ở module-level** ngay khi khởi chạy ứng dụng. Nếu tệp tin cấu hình hệ thống `services.json` hoặc `incidents_history.json` bị mất hoặc hư hại, hệ thống sẽ tự động kích hoạt cấu hình topo mạng mặc định (`fallback-stub`). Điều này ngăn chặn hoàn toàn việc crash tiến trình lúc khởi động, đảm bảo ứng dụng luôn sẵn sàng phục vụ.
* **Phòng vệ tầng duyệt Đồ thị hạ tầng (Defensive Graph Traversal):** Trong các kịch bản thực tế, một số dịch vụ mới triển khai có thể phát sinh cảnh báo trước khi kịp cập nhật vào bản đồ topo hạ tầng tổng thể. Tại hàm `topology_group`, hệ thống thực hiện kiểm tra điều kiện nghiêm ngặt bằng phương thức `undirected.has_node(s1)`. Nếu không tồn tại node trong đồ thị, thuật toán sẽ tự động bỏ qua thay vì ném lỗi `KeyError`, giúp cô lập xung đột dữ liệu.
* **Cơ chế Fail-safe cô lập hoàn toàn lỗi máy chủ:** Toàn bộ tiến trình tính toán thuật toán phức tạp tại Endpoint `POST /incident` được bao bọc trong một khối `try/except Exception` toàn cục. Khi xảy ra bất kỳ sự cố bất thường nào vượt ngoài tầm kiểm soát (ví dụ: lỗi tràn bộ nhớ hoặc lỗi thư viện đồ thị), hệ thống sẽ:
1. Ghi nhận log có cấu trúc chi tiết kèm Stack Trace (`exc_info=True`) nội bộ để phục vụ kỹ sư SRE.
2. Lập tức trả về mã lỗi chuẩn `HTTPException(500)` với thông báo đã được mã hóa, **tuyệt đối không để lộ mã nguồn hoặc cấu trúc thư mục ra phía Client**.



---

# 4. ĐÁNH GIÁ TRADE-OFF FRAMEWORK (FASTAPI VS. FLASK VS. BENTOML)

Việc quyết định lựa chọn **FastAPI** làm nền tảng cốt lõi cho dịch vụ phục vụ mô hình AIOps này dựa trên việc phân tích kỹ lưỡng các ưu khuyết điểm và sự phù hợp kiến trúc (Architecture Fit Matrix):

* **So với Flask (WSGI truyền thống):** Flask vận hành dựa trên cơ chế chặn đồng bộ (Synchronous Blocking). Khi chịu tải cao với hàng loạt Batch Alerts đổ về cùng lúc, Flask sẽ nhanh chóng làm nghẽn các luồng xử lý (Thread Pool). FastAPI dựa trên nền tảng ASGI (Uvicorn), hỗ trợ xử lý bất đồng bộ (Asynchronous) nguyên sinh, giúp đạt hiệu năng thông lượng vượt trội tương đương với Go hoặc Node.js mà không làm tăng chi phí hạ tầng phần cứng. Bên cạnh đó, khả năng tự động validate dữ liệu cực mạnh của Pydantic tích hợp trong FastAPI giúp giảm $90\%$ mã nguồn kiểm tra thủ công.
* **So với BentoML (Chuyên dụng cho Machine Learning):** BentoML là một framework tuyệt vời cho việc đóng gói các mô hình học máy nặng (Heavy Tensor Inference) như PyTorch, TensorFlow nhờ cơ chế Auto-batching và tích hợp Runner biệt lập. Tuy nhiên, bài toán AIOps Incident Engine hiện tại bản chất là một **Pipeline thuật toán cấu trúc dữ liệu và suy diễn toán học trên bộ nhớ** (Sử dụng `NetworkX`, `Pandas` và các công thức Heuristic kết hợp). Hệ thống không thực hiện nạp các file trọng số nhị phân khổng lồ ($\text{GB}$). Do đó, sử dụng BentoML sẽ khiến hệ thống chịu một mức "thuế" overhead cực kỳ nặng nề về kích thước Docker Image, thời gian khởi động nguội (Cold Start time) dài và cấu trúc lập trình phức tạp hóa không cần thiết.

---