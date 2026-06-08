# SUBMIT.md

## 1. Giá trị `gap_sec`

**Chọn:** `gap_sec = 30` giây

**Lý do:**

* Các alert cách nhau một khoảng thời gian rất ngắn (<= 40s)
* Tuning gap_sec 10, 20, 30 và 30 cho ra tỷ lệ giảm tải cao nhất

---

## 2. Giá trị `max_hop`

**Chọn:** `max_hop = 2`

**Lý do:**

* Topology của hệ thống có các dependency theo chuỗi:

  ```
  edge-lb
      ↓
  checkout-svc
      ↓
  payment-svc
  ```

* Khi xảy ra lỗi tại `payment-svc`, alert có thể lan sang `checkout-svc` rồi tới `edge-lb`.

* Với `max_hop = 1`, một số alert liên quan sẽ bị tách thành các cluster riêng.

* Với `max_hop = 2`, các service nằm trong cùng đường lan truyền sự cố vẫn được gom lại.

* Nếu tăng quá cao (3–4 hop), nguy cơ gom nhầm các service không liên quan sẽ tăng lên.

Do đó `max_hop = 2` phù hợp với kích thước và độ sâu dependency của hệ thống mẫu.

---

## 3. Một alert bị miss khỏi cluster chính

**Alert:** `a-0013`

**Service:** `recommender-svc`

**Nguyên nhân:**

* Alert này xuất hiện độc lập tại `recommender-svc`.

* Trong dữ liệu có ghi chú:

  ```
  "unrelated — concurrent batch retrain"
  ```

* Service này không nằm trên critical path của giao dịch thanh toán.

* Không có alert đồng thời từ các service lân cận trong topology.

* Vì vậy thuật toán không tìm được quan hệ thời gian hoặc topology đủ mạnh để gộp nó vào cluster sự cố chính.

Kết quả là `a-0013` tạo thành cluster riêng thay vì được ghép với các cluster khác.

---

## 4. Khi tăng lên 10,000 alert

Các vị trí có khả năng chậm nhất:

### a. Tính khoảng cách topology

Trong hàm `topology_group()`, thuật toán kiểm tra khoảng cách giữa các service có alert:

```python
nx.shortest_path_length(...)
```

Nếu số lượng service hoặc số lần kiểm tra tăng mạnh, đây sẽ là chi phí lớn nhất.

---

### b. So sánh cặp service

Thuật toán hiện tại duyệt nhiều cặp service để quyết định Union-Find:

```python
for s1 in services:
    for s2 in services:
```

Độ phức tạp gần:

```
O(S²)
```

với `S` là số service có alert trong session.

Khi dữ liệu lớn hơn, phần này tăng nhanh nhất.

---

### c. Gom nhóm theo session

Mỗi session cần:

1. Sắp xếp alert theo thời gian.
2. Tách session.
3. Chạy topology grouping.

Chi phí sắp xếp:

```
O(N log N)
```

với `N` là số alert.

Khi tăng lên 10.000 alert, bước này bắt đầu đáng kể nhưng vẫn thường nhỏ hơn chi phí topology.

# Trade-offs
---
Thuật toán hiện tại tính quan hệ dựa trên topology graph tại thời điểm clustering. Cách làm này đủ nhanh với vài trăm alert nhưng khi quy mô tăng lên hàng chục nghìn alert, chi phí tính khoảng cách giữa các service và so sánh nhiều cặp service sẽ tăng đáng kể. Việc tiền tính ma trận khoảng cách hoặc xây dựng index topology sẽ cải thiện hiệu năng nhưng phải đánh đổi thêm bộ nhớ và độ phức tạp của hệ thống.

# EOD Checkpoint

## 1. Vì sao fingerprint cho dedup không include timestamp hay value?

Fingerprint được dùng để nhận diện loại alert thay vì một lần xuất hiện cụ thể của alert. Nếu đưa timestamp vào fingerprint, mỗi alert sẽ có fingerprint khác nhau và cơ chế dedup gần như mất tác dụng. Nếu đưa value vào fingerprint, các alert cùng metric nhưng khác giá trị (ví dụ CPU 91% và 93%) cũng bị xem là khác nhau. Kết quả là số lượng duplicate alert tăng mạnh và reduction ratio giảm đáng kể.

---

## 2. Sự khác biệt giữa “duplicate” và “correlated” alert là gì?

Duplicate alert là nhiều alert thực chất mô tả cùng một vấn đề trên cùng service và cùng metric. Ví dụ nhiều lần `payment-svc|latency_p99_ms|crit` xuất hiện liên tiếp. Correlated alert là các alert khác nhau nhưng có liên quan bởi cùng một sự cố gốc. Trong dataset, alert tại `payment-svc`, `checkout-svc` và `edge-lb` trong incident pool exhaustion không phải duplicate nhưng được xem là correlated vì chúng nằm trên cùng dependency path.

---

## 3. gap_sec = 30 vs gap_sec = 600

* **gap_sec = 30:** Incident lớn dễ bị tách thành nhiều cluster nhỏ do alert đến cách nhau quá xa về thời gian.
* **gap_sec = 600:** Nhiều sự cố độc lập xảy ra gần nhau có thể bị gộp nhầm thành một cluster lớn.

---

## 4. Recommender-svc có được gom vào cluster chính không?

Không. Mặc dù alert xảy ra gần thời điểm incident chính, `recommender-svc` không nằm trên dependency path của luồng checkout/payment đang gặp sự cố. Dataset cũng mô tả đây là một batch retrain độc lập. Vì không có liên hệ topology đủ mạnh với các service trong critical path nên correlator giữ alert này ở cluster riêng thay vì gom vào cluster chính.

---

## 5. Limitation lớn nhất của topology grouping là gì? Cách khắc phục?

Topology grouping giả định rằng mọi mối liên hệ đều được mô tả trong service graph. Trong thực tế có thể tồn tại các dependency động, shared resource hoặc bottleneck hạ tầng không xuất hiện trong topology. Khi đó các alert thực sự liên quan có thể không được gom cùng cluster. Một hướng khắc phục là kết hợp topology với historical correlation hoặc causal scoring dựa trên dữ liệu sự cố trước đây để phát hiện các quan hệ ngoài graph tĩnh.
