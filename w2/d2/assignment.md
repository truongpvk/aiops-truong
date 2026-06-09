### 1. Mục tiêu bài tập
Xây dựng một pipeline RCA tự động để từ một cluster gồm nhiều alert, chỉ ra đúng dịch vụ nào là nguyên nhân gốc, phân loại lỗi và đề xuất hành động xử lý.

### 2. Cấu trúc thư mục và Quy ước nộp bài
Đây là yêu cầu bắt buộc để hệ thống chấm điểm tự động (auto-grader) có thể ghi nhận kết quả (sai tên sẽ bị 1 điểm):
*   **Đường dẫn:** `aiops-<tên_của_bạn>/w2/d2/` (Lưu ý: `w2` và `d2` viết thường).
*   **Các file cần có:**
    *   `assignment.ipynb`: Notebook chứa toàn bộ mã nguồn xử lý.
    *   `FINDINGS.md`: Báo cáo phân tích (viết HOA tên file).
    *   `SUBMIT.md`: Trả lời các câu hỏi checkpoint (viết HOA tên file).
    *   `results/rca_output.json`: File kết quả đầu ra sau khi chạy code.

### 3. Dữ liệu đầu vào (Input)
Bạn cần sử dụng các tập tin sau (lưu tại `aiops-<tên>/w2/d2/dataset/`):
1.  **`cluster_summary.json`**: Kết quả đầu ra từ bài tập ngày W2-D1.
2.  **`alerts_sample.jsonl`**: Dữ liệu alert thô.
3.  **`services.json`**: Sơ đồ cấu trúc dịch vụ (service graph).
4.  **`incidents_history.json`**: Lịch sử 30 sự cố trước đó để thực hiện so khớp (retrieval).

### 4. Các bước thực hiện chính (Required)
Bạn phải thực hiện pipeline qua các bước sau mà không bắt buộc dùng API key (dùng logic graph + retrieval thuần):
*   **Bước 1: Xây dựng Graph:** Sử dụng thư viện `networkx` để dựng lại service graph từ file `services.json`.
*   **Bước 2: Graph Traversal & Temporal Scorer:** 
    *   Duyệt đồ thị để tìm các dịch vụ ở vị trí "sâu nhất" (terminal nodes) trong cluster.
    *   Kết hợp với thời gian phát cảnh báo (dịch vụ alert sớm nhất được ưu tiên) để đưa ra danh sách **Top-K ứng viên**.
*   **Bước 3: Retrieval (So khớp lịch sử):** Sử dụng thuật toán kNN-style hoặc so khớp từ khóa (keyword similarity) để tìm 3 sự cố tương tự trong quá khứ từ `incidents_history.json`.
*   **Bước 4: Classifier:** Lấy thông tin về loại lỗi (`class`) và hành động xử lý (`actions`) từ sự cố lịch sử tương tự nhất để gán cho cluster hiện tại.
*   **Bước 5: Fallback logic:** Nếu việc so khớp không tìm thấy kết quả, phải có cơ chế tự động gán class là "other" và yêu cầu "Investigate manually" để đảm bảo hệ thống không bị lỗi.

### 5. Yêu cầu về đầu ra và Báo cáo
*   **`rca_output.json`**: Phải chứa đầy đủ các trường: `graph_top3`, `root_cause`, và `class`.
*   **`FINDINGS.md`**: Phải dài ít nhất **100 từ**, phân tích rõ root cause của cluster chính là gì, tại sao, và mức độ tin tưởng (confidence) đối với kết quả đó.
*   **Tiêu chí đạt (Acceptance Criteria):** Notebook phải chạy được, có ít nhất 3 cell có output, và mã nguồn phải thể hiện việc sử dụng các hàm của `networkx` cũng như logic so khớp (retrieval).
