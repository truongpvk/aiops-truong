import time
import json
import math
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "http://localhost:8000"
ALERTS_FILE = "dataset/alerts_sample.jsonl"

# =====================================================================
# 1. ĐỌC DỮ LIỆU TỪ ALERTS_SAMPLE.JSONL VÀ ĐÓNG GÓI PAYLOAD
# =====================================================================
def load_real_alerts_payload(file_path):
    if not os.path.exists(file_path):
        print(f"❌ LỖI: Không tìm thấy file dữ liệu test '{file_path}' tại thư mục hiện tại.")
        return None
        
    alerts = []
    print(f"📖 Đang đọc dữ liệu kiểm thử từ '{file_path}'...")
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    alerts.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"⚠️ Bỏ qua dòng lỗi format JSON: {e}")
                    
    print(f"✅ Đã nạp thành công {len(alerts)} alerts thực tế từ file.")
    return {"alerts": alerts}

# Load payload kiểm thử
payload_data = load_real_alerts_payload(ALERTS_FILE)

# =====================================================================
# 2. KIỂM TRA ĐIỂM CUỐI HEALTH CHECK (/healthz & /readyz & /version)
# =====================================================================
def test_health_endpoints():
    print("\n=== [PHẦN 1] KIỂM TRA TÁCH BIỆT HEALTH CHECK ===")
    endpoints = ["/healthz", "/readyz", "/version"]
    
    for endpoint in endpoints:
        try:
            url = f"{BASE_URL}{endpoint}"
            res = requests.get(url, timeout=5)
            print(f"-> GET {endpoint:9} | HTTP {res.status_code} | Response: {res.json()}")
        except Exception as e:
            print(f"-> GET {endpoint:9} | ❌ THẤT BẠI: {e}")
    print("-" * 65)

# =====================================================================
# 3. ĐO LƯỜNG LATENCY TUẦN TỰ (20 REQUESTS ĐỂ TÍNH P50, P99)
# =====================================================================
def test_sequential_latency():
    print("\n=== [PHẦN 2] ĐO LƯỜNG LATENCY THỰC TẾ (20 REQUESTS TUẦN TỰ) ===")
    if not payload_data:
        return
        
    latencies = []
    print("Đang thực hiện gửi 20 requests tuần tự để thu thập số liệu phân vị...")
    
    for i in range(20):
        try:
            res = requests.post(f"{BASE_URL}/incident", json=payload_data, timeout=15)
            if res.status_code == 200:
                # Trích xuất thời gian xử lý thực tế từ Middleware custom thông qua HTTP Header
                server_time = float(res.headers.get("X-Response-Time-Ms", 0))
                latencies.append(server_time)
            else:
                print(f"  -> Request #{i+1:02d} gặp lỗi HTTP Code: {res.status_code}")
        except Exception as e:
            print(f"  -> Request #{i+1:02d} thất bại kết nối: {e}")
            
    if not latencies:
        print("❌ Không thu thập được dữ liệu Latency nào hợp lệ. Vui lòng bật Uvicorn Server trước.")
        return
        
    latencies.sort()
    n = len(latencies)
    
    # Tính toán các chỉ số phân vị Latency theo đúng đặc tả
    p50_idx = max(0, min(n - 1, int(math.ceil(0.50 * n)) - 1))
    p99_idx = max(0, min(n - 1, int(math.ceil(0.99 * n)) - 1))
    
    p50 = latencies[p50_idx]
    p99 = latencies[p99_idx]
    avg_lat = sum(latencies) / n
    
    print(f"\n📊 KẾT QUẢ ĐO LƯỜNG LATENCY (Từ X-Response-Time-Ms):")
    print(f"  - Số lượng mẫu thành công : {n}/20")
    print(f"  - Thời gian nhỏ nhất (Min): {latencies[0]:.2f} ms")
    print(f"  - Thời gian trung bình(Avg): {avg_lat:.2f} ms")
    print(f"  - Phân vị P50             : {p50:.2f} ms")
    print(f"  - Phân vị P99             : {p99:.2f} ms")
    print("-" * 65)

# =====================================================================
# 4. KIỂM THỬ KHẢ NĂNG CHỊU TẢI ĐỒNG THỜI (CONCURRENCY 4 WORKERS)
# =====================================================================
def send_single_request(req_id):
    try:
        start_time = time.perf_counter()
        res = requests.post(f"{BASE_URL}/incident", json=payload_data, timeout=15)
        duration = (time.perf_counter() - start_time) * 1000
        return req_id, res.status_code, duration
    except Exception as e:
        return req_id, None, str(e)

def test_concurrency():
    print("\n=== [PHẦN 3] KIỂM THỬ TẢI ĐỒNG THỜI (4 CONCURRENT WORKERS) ===")
    if not payload_data:
        return
        
    total_requests = 20
    concurrent_workers = 4
    
    success_count = 0
    failure_count = 0
    start_total_time = time.perf_counter()
    
    print(f"Đang kích hoạt đồng thời {concurrent_workers} luồng xử lý tổng cộng {total_requests} requests...")
    
    with ThreadPoolExecutor(max_workers=concurrent_workers) as executor:
        futures = [executor.submit(send_single_request, i) for i in range(total_requests)]
        
        for future in as_completed(futures):
            req_id, status_code, result = future.result()
            if status_code == 200:
                success_count += 1
            else:
                failure_count += 1
                print(f"  ❌ Request #{req_id} LỖI! Status Code: {status_code} | Chi tiết: {result}")
                
    end_total_time = time.perf_counter()
    total_duration_sec = end_total_time - start_total_time
    
    print(f"\n📊 KẾT QUẢ KIỂM THỬ TẢI ĐỒNG THỜI:")
    print(f"  - Tổng số lượng request gửi đi   : {total_requests}")
    print(f"  - Cấu hình số luồng song song    : {concurrent_workers}")
    print(f"  - Số lượng xử lý thành công     : {success_count}")
    print(f"  - Số lượng xử lý thất bại        : {failure_count}")
    print(f"  - Tổng thời gian hoàn thành      : {total_duration_sec:.2f} giây")
    print(f"  - Tốc độ xử lý (Throughput)      : {total_requests / total_duration_sec:.2f} req/s")
    print("=" * 65)

if __name__ == "__main__":
    if payload_data:
        print("=" * 65)
        print("  AIOPS ENGINE - EOD VERIFICATION WITH PRODUCTION SAMPLES")
        print("=" * 65)
        
        # Thực thi chuỗi kịch bản kiểm thử nghiệm thu
        test_health_endpoints()
        test_sequential_latency()
        test_concurrency()