## 1. Đo lường Latency

* **Số liệu thực tế:** Phân vị P50: **2.75 ms** | Phân vị P99: **154.51 ms**.
* **Tỷ trọng thời gian:** Giai đoạn **RCA cục bộ (Duyệt đồ thị bằng PageRank + So khớp lịch sử KNN)** chiếm tỷ trọng lớn nhất. Ở P99, độ trễ tăng cao do hiện tượng khởi động lạnh (Cold Start) khi nạp module và cơ chế phân phối luồng ThreadPool của FastAPI ở request đầu tiên.
* **Khi lượng Alert tăng 10 lần:**
* Hệ thống tăng trưởng **Phi tuyến tính (Non-linear)** vì các thuật toán sắp xếp alert theo thời gian có độ phức tạp $\mathcal{O}(N \log N)$ và thuật toán duyệt đồ thị tiệm cận $\mathcal{O}(N^2)$.
* **Chi phí cố định (Fixed cost):** Việc đọc và nạp file topo `services.json` và `incidents_history.json` từ đĩa vào bộ nhớ chỉ diễn ra 1 lần duy nhất lúc khởi động server.



## 2. Kiểm thử tải Concurrency & Fallback

* **Điểm nghẽn đầu tiên:** **Global Interpreter Lock (GIL)** của Python. Do endpoint xử lý đồng bộ (`def`) chứa tác vụ tính toán nặng trên CPU (CPU-bound), các luồng trong ThreadPool nội bộ của FastAPI bị tranh chấp CPU, khiến throughput bị giới hạn ở mức **~1.98 req/s**.
* **Xử lý khi LLM Provider sập:** * Hệ thống sử dụng cấu hình gốc `graph-topology-knn-heuristic` chạy offline trên memory nên không phụ thuộc cứng vào bên thứ ba.
* **Cơ chế Fallback / Kill-switch:** Được bọc trong khối `try-except`. Nếu LLM lỗi hoặc timeout, hệ thống tự động bỏ qua bước làm mượt văn bản và lấy trực tiếp trường `remediation` từ sự cố lịch sử (KNN) gần nhất để trả về cho SRE, đảm bảo mã lỗi 500 không bị rò rỉ.



## 3. Tách biệt Health Check

* **Thành phần kiểm tra:**
* `/healthz` (Liveness): Kiểm tra cạn để xác nhận tiến trình Python/FastAPI đang sống.
* `/readyz` (Readiness): Kiểm tra sâu xem biến toàn cục `GRAPH` và `HISTORY` đã nạp đủ dữ liệu vào bộ nhớ chưa.


* **Tầm quan trọng:** Tránh vòng lặp khởi động chết chóc (Boot-loop) khi hệ thống mất thời gian nạp dữ liệu lớn lúc ban đầu, đồng thời giúp Load Balancer ngừng điều phối traffic vào Pod khi bộ nhớ đang bị nghẽn mà không cần kill container.
* **Trạng thái `/readyz` khi LLM sập:** **Vượt qua (Pass)**.
* *Lý do:* LLM chỉ là thành phần bổ trợ để làm mượt văn bản khuyến nghị hành động, không phải thành phần sinh tử (Critical path) quyết định lõi phân tích RCA đồ thị. Cho phép `/readyz` Pass giúp hệ thống duy trì tính liên tục (Business Continuity) thay vì từ chối dịch vụ hoàn toàn.