# W2-D1: Alert Correlation — Từ Noise Sang Signal

> **Bối cảnh:** Sáng thứ 2, 02:14. Pager kêu. Dashboard: 47 alert đỏ trong 90 giây. Payment latency cao. Checkout 5xx tăng. Edge LB p99 vượt 3s. DB CPU 95%. Cart-redis evict. Recommender OOM.
>
> Câu hỏi đầu tiên: **"Cái nào đang gây ra cái còn lại?"**
>
> Câu trả lời W2: *"Chúng ta correlate trước, sau đó RCA."*

> ⚠️ **Rule of thumb:** Correlation không tìm root cause. Correlation **rút gọn số việc** phải làm RCA. Đừng nhầm 2 việc này.

---

## Mục lục

1. [Vấn Đề: Alert Flood](#1-vấn-đề-alert-flood)
2. [Layer 1 — Dedup](#2-layer-1--dedup)
3. [Layer 2 — Time-Window Correlation](#3-layer-2--time-window-correlation)
4. [Layer 3 — Topology-Aware Correlation](#4-layer-3--topology-aware-correlation)
5. [Layer 4 (Bonus) — Semantic Correlation](#5-layer-4-bonus--semantic-correlation)
6. [Production Patterns](#6-production-patterns)
7. [Embedded Exercise](#7-embedded-exercise)
8. [EOD Checkpoint](#8-eod-checkpoint)
9. [Resources](#9-resources)

---

## 1. Vấn Đề: Alert Flood

### 1.1 Alert Fatigue Là Gì

Một on-call engineer trung bình nhận **20–50 alert / ngày**. Khảo sát VictorOps 2023 (800 engineer):

| Số liệu | Kết quả |
|---|---|
| Engineer nhận > 10 alert / ca trực | 67% |
| Đã từng **tắt** notification vì noisy | 45% |
| MTTR tăng khi alert flood (> 5/phút) | **2.4×** so với baseline |

Khi 1 service hỏng, nó **không tự một mình** — nó kéo upstream và downstream. Mỗi alert tự nó *đúng*, nhưng tổng hợp lại tạo cảm giác "everything is broken". Engineer cần biết: **cái nào là nguyên nhân, cái nào là triệu chứng**.

### 1.2 Bốn Nguyên Nhân Chính của Alert Flood

| Nguyên nhân | Ví dụ | Cách correlation giải quyết |
|---|---|---|
| **Duplicate** — cùng alert fire nhiều lần | Latency alert fire mỗi 30s suốt 10 phút (20 lần) | **Dedup** bằng fingerprint, giữ 1 + count |
| **Cascading** — 1 service hỏng kéo theo dependency | Payment timeout → checkout timeout → edge 5xx | **Topology-aware grouping** |
| **Threshold sensitivity** — metric dao động quanh ngưỡng | CPU 79.5 → 80.5 → 79.8 → 80.2 fire/clear liên tục | **Flapping suppression** |
| **Correlated symptoms** — nhiều metric cùng service alarm | CPU + latency + error_rate tăng cùng lúc | **Time-window grouping** |

### 1.3 Mục Tiêu Cuối Ngày

Cho **200 alert** đầu vào, output **3–7 cluster** trong đó:

- Mỗi cluster có ≥ 2 alert cùng gốc (dedup key, thời gian, hoặc đường đi trong service graph)
- Cluster metadata: `cluster_id`, `alert_count`, `services`, `time_range`, `severity` (max)
- **0 alert orphan** — nếu 1 alert không match, vẫn output thành cluster size=1

200 → 3–7 cluster = **giảm 96–98% items** mà RCA phải xử lý.

---

## 2. Layer 1 — Dedup

Layer đơn giản nhất: **cùng 1 alert fire lại → không tạo cluster mới, chỉ tăng counter**.

### 2.1 Fingerprint Là Gì

Fingerprint = subset field định danh "đây cùng 1 loại alert". Phần lớn field thay đổi mỗi lần fire (`timestamp`, `value`), nhưng một subset **không đổi**.

```python
def fingerprint(alert: dict) -> str:
    """
    Tạo unique key cho alert. 2 alert có cùng fingerprint = duplicate.
    
    - PHẢI có: service, metric, severity
    - KHÔNG nên có: timestamp, value (chúng thay đổi mỗi lần)
    - Tùy chọn: labels.env, labels.region
    """
    return f"{alert['service']}|{alert['metric']}|{alert['severity']}"
```

> **Tại sao không include `labels.host`?** Trong K8s, mỗi pod là 1 host khác nhau. Nếu include host → 3 pod của cùng service alert → 3 fingerprint khác nhau → dedup mất tác dụng.

### 2.2 Dedup With State

Dedup cần **state**: dictionary lưu `fingerprint → cluster`. Khi alert mới đến, check fingerprint; nếu có → update; nếu không → tạo entry mới.

```python
from collections import defaultdict
from datetime import datetime

class Deduper:
    def __init__(self):
        self.store: dict[str, dict] = {}  # fingerprint → cluster info
    
    def push(self, alert: dict) -> str:
        fp = fingerprint(alert)
        ts = datetime.fromisoformat(alert['ts'].replace('Z', '+00:00'))
        
        if fp not in self.store:
            self.store[fp] = {
                'cluster_id': fp,
                'count': 1,
                'first_seen': ts,
                'last_seen': ts,
                'alerts': [alert['id']],
                'max_value': alert['value'],
            }
        else:
            c = self.store[fp]
            c['count'] += 1
            c['last_seen'] = ts
            c['alerts'].append(alert['id'])
            c['max_value'] = max(c['max_value'], alert['value'])
        
        return fp
    
    def clusters(self) -> list[dict]:
        return list(self.store.values())
```

> ⚠️ **Cảnh báo:** `self.store` không có giới hạn — grow vô tận. Trên production, sau 24h có 100k+ entries. Cần TTL eviction. Xem [Section 6.3](#63-memory--ttl).

### 2.3 Khi Dedup Không Đủ

Dedup chỉ gom **alert giống hệt nhau**. Nó không gom:

- `payment latency alert` + `payment error_rate alert` → 2 cluster khác nhau (cùng service, khác metric)
- `payment alert` + `checkout alert` → 2 cluster khác nhau (khác service, cùng cause)

→ Cần thêm 2 layer nữa.

---

## 3. Layer 2 — Time-Window Correlation

**Insight:** Incident thường xảy ra trong cửa sổ thời gian ngắn. Nếu 5 service cùng alert trong 2 phút → có thể share root cause. Nếu spread over 2 giờ → có thể không liên quan.

### 3.1 Sliding Window Cơ Bản

```python
from collections import deque
from datetime import datetime, timedelta

def time_window_groups(alerts: list[dict], window_sec: int = 300) -> list[list[dict]]:
    """
    Group alerts arriving within window_sec của nhau.
    
    Logic: dùng deque buffer 5 phút. Mỗi alert mới:
      1. Pop những alert cũ hơn (now - window_sec)
      2. Còn lại trong buffer = "cùng window" với alert hiện tại
    """
    groups = []
    buffer = deque()  # (ts, alert)
    
    for alert in alerts:
        ts = datetime.fromisoformat(alert['ts'].replace('Z', '+00:00'))
        cutoff = ts - timedelta(seconds=window_sec)
        
        while buffer and buffer[0][0] < cutoff:
            buffer.popleft()
        
        buffer.append((ts, alert))
        groups.append([a for _, a in buffer])
    
    return groups
```

> **Vấn đề:** Code trên trả về 1 group *cho mỗi alert* (overlapping). Trong thực tế cần **non-overlapping** — mỗi alert thuộc đúng 1 group.

### 3.2 Tumbling vs Sliding vs Session Window

| Window type | Mô tả | Khi nào dùng |
|---|---|---|
| **Tumbling** | Fixed-size, non-overlapping. E.g. 0–5, 5–10, 10–15 phút | Group rõ ràng, mỗi alert thuộc đúng 1 window. **Default choice.** |
| **Sliding** | Mỗi alert tạo 1 window backward. Overlapping | Khi cần "có alert nào gần đây" — live alerting |
| **Session** | Window kết thúc khi không có alert mới trong N giây | Khi incident có "burst" rõ ràng — dynamic length |

**Cho lab này, dùng Session Window:**

```python
def session_groups(alerts: list[dict], gap_sec: int = 120) -> list[list[dict]]:
    """
    Mỗi group là 1 'session'. Session kết thúc khi không alert nào trong gap_sec giây.
    
    Vì sao session tốt hơn tumbling:
    - Incident burst: 30 alert trong 90s → 1 session tự nhiên
    - Tumbling 5min: incident span 4:30–5:30 → bị cắt thành 2 window
    - Session tự adapt kích thước theo burst pattern
    """
    if not alerts:
        return []
    
    sorted_alerts = sorted(alerts, key=lambda a: a['ts'])
    groups = [[sorted_alerts[0]]]
    
    for alert in sorted_alerts[1:]:
        ts = datetime.fromisoformat(alert['ts'].replace('Z', '+00:00'))
        last_ts = datetime.fromisoformat(groups[-1][-1]['ts'].replace('Z', '+00:00'))
        
        if (ts - last_ts).total_seconds() <= gap_sec:
            groups[-1].append(alert)
        else:
            groups.append([alert])
    
    return groups
```

### 3.3 Chọn `gap_sec` Thế Nào

| `gap_sec` | Hậu quả | Khi nào dùng |
|---|---|---|
| 30s | Group rất nhỏ, incident dài bị tách | Alert flood thực sự < 30s spread |
| **120s (2 phút)** | **Sweet spot cho hầu hết production system** | **Default cho W2 lab** |
| 300s (5 phút) | Group lớn hơn, có thể merge 2 incident không liên quan | Service degrade chậm |
| 600s+ | Bắt incident kéo dài | Cảnh giác false correlation |

> 💡 **Production wisdom:** Đo `gap_sec` bằng histogram của `time_since_last_alert` trong 30 ngày qua. Chọn giá trị ở **95th percentile của intra-incident gap**.

---

## 4. Layer 3 — Topology-Aware Correlation

Time-window gom theo **khi nào**. Topology gom theo **chúng có connected không**.

### 4.1 Service Graph Là Gì

Service graph là directed graph: **Node** = service, **Edge A → B** = service A gọi service B.

```
edge-lb → checkout-svc
checkout-svc → payment-svc
checkout-svc → cart-svc
checkout-svc → inventory-svc
cart-svc → cart-redis
payment-svc → payments-db
```

Khi `payment-svc` hỏng, **propagation pattern**: lỗi lan **upstream** (về phía caller).

- `payments-db` — không bị ảnh hưởng (payment-svc *dùng* nó, không phải ngược lại)
- `checkout-svc` — bị ảnh hưởng (depend on payment-svc)
- `edge-lb` — bị ảnh hưởng (transitive cascade)

### 4.2 Build Graph Với NetworkX

```python
import networkx as nx
import json

def build_graph(services_json_path: str) -> nx.DiGraph:
    """
    Build directed graph: A → B nghĩa là A gọi B.
    Khi RCA traverse, đi NGƯỢC edge — nếu A alert thì B có thể là root cause.
    """
    g = nx.DiGraph()
    data = json.loads(open(services_json_path).read())
    
    for svc in data['services']:
        g.add_node(svc['name'], **{k: v for k, v in svc.items() if k != 'name'})
    
    for store in data['stores']:
        g.add_node(store['name'], **{k: v for k, v in store.items() if k != 'name'})
    
    for edge in data['edges']:
        g.add_edge(edge['from'], edge['to'], type=edge['type'])
    
    return g
```

### 4.3 Topology Grouping Logic

Có 2 cách gom:

- **Cách 1 — Connected component:** Lấy subgraph chỉ chứa service có alert. Mỗi connected component = 1 cluster. Đơn giản nhưng có thể quá rộng.
- **Cách 2 — Path-based:** Hai alert cùng cluster nếu có path ≤ N hop nối chúng. **N=2 thường tốt.**

```python
def topology_group(alerts: list[dict], graph: nx.DiGraph, max_hop: int = 2) -> list[list[dict]]:
    """
    Group alerts nếu service của chúng cách nhau ≤ max_hop trên service graph.
    Dùng undirected version vì cascade có thể đi cả 2 chiều.
    """
    if not alerts:
        return []
    
    undirected = graph.to_undirected()
    by_service = defaultdict(list)
    for a in alerts:
        by_service[a['service']].append(a)
    
    services_with_alerts = list(by_service.keys())
    
    # Union-Find
    parent = {s: s for s in services_with_alerts}
    
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    
    def union(x, y):
        parent[find(x)] = find(y)
    
    for i, s1 in enumerate(services_with_alerts):
        for s2 in services_with_alerts[i+1:]:
            try:
                dist = nx.shortest_path_length(undirected, s1, s2)
                if dist <= max_hop:
                    union(s1, s2)
            except nx.NetworkXNoPath:
                continue  # Không connected → không group
    
    groups_dict = defaultdict(list)
    for s in services_with_alerts:
        groups_dict[find(s)].extend(by_service[s])
    
    return list(groups_dict.values())
```

**Mental model test:** 47 alert, 8 service distinct:

- `payment-svc`, `checkout-svc`, `edge-lb` → connected component A (cascade chain)
- `recommender-svc` → component B (isolated)
- `search-svc` → component C (isolated)
- Output: **3 group**

### 4.4 Kết Hợp Time-Window + Topology

Mỗi layer alone không đủ:

- **Time-window only:** gom alert cùng giờ nhưng có thể không liên quan (recommender retrain + payment crash trùng giờ)
- **Topology only:** gom alert cùng cascade chain nhưng có thể cách nhau 6 giờ

**Combined logic:** 2 alert cùng cluster nếu **vừa cùng time-window vừa cùng topology component**.

```python
def correlate(alerts: list[dict], graph: nx.DiGraph, gap_sec: int = 120, max_hop: int = 2):
    """
    Pipeline:
      1. Sort alert by timestamp
      2. Cho mỗi session (time-window), apply topology grouping
      3. Output clusters
    """
    sessions = session_groups(alerts, gap_sec=gap_sec)
    
    all_clusters = []
    for session_idx, session_alerts in enumerate(sessions):
        topo_groups = topology_group(session_alerts, graph, max_hop=max_hop)
        for group_idx, group in enumerate(topo_groups):
            all_clusters.append({
                'cluster_id': f'c-{session_idx:03d}-{group_idx:03d}',
                'alert_count': len(group),
                'services': sorted(set(a['service'] for a in group)),
                'alert_ids': [a['id'] for a in group],
                'time_range': [min(a['ts'] for a in group), max(a['ts'] for a in group)],
                'max_severity': max(a['severity'] for a in group),
            })
    
    return all_clusters
```

---

## 5. Layer 4 (Bonus) — Semantic Correlation

> Đến đây đã đủ pass lab. Layer này là production-level improvement.

### 5.1 Insight

Đôi khi 2 alert có fingerprint khác nhau nhưng nội dung tương tự:

- `payment-svc db_pool_used_ratio = 0.95` (warn)
- `payment-svc db_connection_count = 49/50` (crit)

Chúng đo **cùng 1 hiện tượng** (DB pool gần cạn) nhưng metric name khác. Dedup miss. Time-window + topology gom được, nhưng không biết chúng "cùng nói 1 chuyện".

### 5.2 Approach Đơn Giản — Keyword Overlap

```python
def text_similarity(alert_a: dict, alert_b: dict) -> float:
    """Jaccard similarity trên tokenized metric name + labels.note."""
    def tokens(a):
        text = f"{a['metric']} {a.get('labels', {}).get('note', '')}"
        return set(text.lower().replace('_', ' ').split())
    
    ta, tb = tokens(alert_a), tokens(alert_b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
```

### 5.3 Approach Nâng Cao — Embedding

Dùng sentence-transformer encode `metric + labels.note + service.team` thành vector. Cosine similarity > 0.8 → "semantically related".

*Không bắt buộc cho lab — đề cập để biết direction.*

---

## 6. Production Patterns

### 6.1 Alertmanager (Prometheus Ecosystem)

```yaml
route:
  group_by: ['alertname', 'cluster', 'service']
  group_wait: 30s        # Đợi 30s gom thêm alert giống nhau
  group_interval: 5m     # Gom alert vào group cũ trong 5 phút
  repeat_interval: 4h    # Re-fire group đã active sau 4h
```

Đây là **dedup + time-window + simple grouping** ở mức infrastructure. **Không có topology** — bạn phải tự build.

### 6.2 Tại Sao Vẫn Cần Code Layer Riêng

Alertmanager grouping hoạt động ở **mỗi route**, không cross-route. Topology-aware correlation ở mức platform-wide. Đây là việc của **alert correlator** layer riêng — có sản phẩm thương mại (BigPanda, Moogsoft), nhưng cốt lõi không khác gì bạn vừa build.

### 6.3 Memory + TTL

```python
def evict_stale(store: dict, ttl_sec: int = 3600):
    """Xoá entries cũ. Gọi mỗi 5 phút bằng scheduler."""
    now = datetime.now(timezone.utc)
    stale = [k for k, v in store.items() 
             if (now - v['last_seen']).total_seconds() > ttl_sec]
    for k in stale:
        del store[k]
```

| Thành phần | TTL strategy |
|---|---|
| Dedup store | Xoá entry > 1 giờ không update (`last_seen`) |
| Session groups | Giữ session **active** — đã close thì emit và xoá |
| Topology graph | Load 1 lần, reload mỗi N phút từ service registry |

### 6.4 Flapping Suppression

```python
def is_flapping(events: list[str], window: int = 10) -> bool:
    """events = ['fire', 'clear', 'fire', 'clear', ...] in last 10 minutes."""
    return events[-window:].count('fire') >= 5  # ≥ 5 fire trong 10 phút
```

---

## 7. Embedded Exercise

**Task:** Code `correlate.py` cho lab dataset.

> ⚠️ **Quy ước nộp bài (auto-grader rất chặt):**
> - Branch `main` (không phải feature branch)
> - Path: `aiops-<tên>/w2/d1/` — `w2` và `d1` đều lowercase
> - File: `assignment.ipynb` + `SUBMIT.md` (HOA) + `results/cluster_summary.json`
> - Sai 1 trong 4 thứ trên → điểm tự động = 1.

### 7.1 Input

- `lab/dataset/alerts_sample.jsonl` (20 alert) hoặc full `alerts.jsonl` (200 alert)
- `lab/dataset/services.json` (service graph)

### 7.2 Output Format

```json
{
  "input_alerts": 20,
  "output_clusters": 3,
  "reduction_ratio": 0.85,
  "clusters": [
    {
      "cluster_id": "c-001-000",
      "alert_count": 14,
      "services": ["payment-svc", "checkout-svc", "edge-lb"],
      "time_range": ["2026-06-12T09:42:01Z", "2026-06-12T09:48:30Z"],
      "max_severity": "crit",
      "fingerprints": ["payment-svc|latency_p99_ms|crit", "..."]
    }
  ]
}
```

### 7.3 Steps

1. Tạo folder `aiops-<your-name>/w2/d1/`
2. Tạo notebook `assignment.ipynb` import các function trên
3. Load `services.json` + `alerts_sample.jsonl`
4. Run `correlate()` pipeline
5. Write output JSON to `results/cluster_summary.json`
6. Write `SUBMIT.md` với:
   - Bạn chọn `gap_sec` bao nhiêu, vì sao
   - Bạn chọn `max_hop` bao nhiêu, vì sao
   - 1 alert ID đã bị "miss" — tại sao?
   - Nếu có 10.000 alert thay vì 200, code sẽ chậm ở đâu?

### 7.4 Acceptance Criteria

| Tiêu chí | Yêu cầu |
|---|---|
| Notebook | Chạy được, ≥ 3 cell có output |
| `cluster_summary.json` | Tồn tại + valid JSON |
| Cluster metadata | Có cả `services` list và `time_range` |
| `reduction_ratio` | = `1 - output_clusters / input_alerts` ≥ **0.5** |
| `SUBMIT.md` | ≥ 100 từ, ≥ 1 design trade-off được thảo luận |

---

## 8. EOD Checkpoint

Trả lời ngắn (~50–100 từ mỗi câu) trong `SUBMIT.md`:

1. **Vì sao fingerprint không include `timestamp` hay `value`?** Cho ví dụ nếu include thì hệ thống behave ra sao.

2. **Sự khác biệt giữa "duplicate" và "correlated" alert là gì?** Ví dụ cụ thể từ lab dataset.

3. **`gap_sec = 30` vs `gap_sec = 600` — mỗi cái ảnh hưởng output thế nào?** 1 dòng cho mỗi case.

4. **Trong scenario chính (payment-svc pool exhaustion), recommender-svc cũng alert (batch retrain). Correlator có gom recommender vào cluster chính không? Vì sao?**
   > 💡 Đây là câu "soul" của bài — trả lời được mạch lạc → hiểu topology-aware correlation.

5. **Limitation lớn nhất của topology grouping?** Suggest 1 cách khắc phục.

---

## 9. Resources

- [Prometheus Alertmanager](https://prometheus.io/docs/alerting/latest/alertmanager/) — routing + grouping reference
- **"You're About To Get Paged"** — Charity Majors blog series. Honest về alert fatigue.
- **Drain3** (W1-D2) — tinh thần rất giống: đều là "gom many → few có ý nghĩa"
- [BigPanda blog](https://www.bigpanda.io/blog/) — case study về alert correlation industry

---

## Sang Ngày Mai (D2)

Clusters output từ hôm nay sẽ là **input của D2**. Câu hỏi: *"Cluster này gây ra bởi service nào?"*

Bạn sẽ học:

- Service dependency graph traversal cho RCA
- Centrality scoring (PageRank-style)
- Causal inference (Granger / PC algorithm)
- LLM-augmented RCA (retrieve incident history, hỏi LLM)

Mục tiêu cuối D2: trả lời được — *"payment-svc, hay checkout-svc, hay edge-lb là root cause của cluster lúc 09:42?"*