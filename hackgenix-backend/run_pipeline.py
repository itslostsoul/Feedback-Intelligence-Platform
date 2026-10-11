import requests
import time

BASE_URL = "http://127.0.0.1:8000/api/v1"

def process_feedback():
    print("Fetching pending feedback records...")
    pending = []
    offset = 0
    limit = 100
    
    # Fetch in chunks of 100 to respect the backend's strict limit
    while True:
        response = requests.get(f"{BASE_URL}/feedback?limit={limit}&offset={offset}")
        response.raise_for_status()
        
        items = response.json().get("items", [])
        if not items:
            break  # No more items left to fetch
            
        pending.extend([item for item in items if item["processing_status"] in ("received", "failed")])
        offset += limit
        
    print(f"Found {len(pending)} records to process. Starting AI pipeline...")

    for index, item in enumerate(pending, 1):
        fid = item["id"]
        print(f"[{index}/{len(pending)}] Processing {fid}...")
        
        try:
            # 1. Ask Gemini to extract category, feature, and severity
            requests.post(f"{BASE_URL}/feedback/{fid}/analyze", timeout=30).raise_for_status()
            
            # 2. Ask Gemini to generate the 3072-dimension pgvector embedding
            requests.post(f"{BASE_URL}/feedback/{fid}/embed", timeout=30).raise_for_status()
            
            # 3. Find semantic relationships against existing records
            requests.post(f"{BASE_URL}/feedback/{fid}/semantic-analysis", timeout=30).raise_for_status()
            
        except requests.exceptions.RequestException as e:
            print(f"Failed processing {fid}: {e}")
            continue
            
        # 8.5-second sleep to stay strictly under Gemini's 15 RPM free-tier limit (2 calls per loop)
        time.sleep(8.5)

    print("\nAll records processed. Running Issue Discovery (Clustering)...")
    requests.post(f"{BASE_URL}/issues/discover", timeout=60).raise_for_status()

    print("Recalculating aggregate signals for the dashboard...")
    requests.post(f"{BASE_URL}/issues/signals/recalculate", timeout=60).raise_for_status()

    print("✅ Pipeline complete! Refresh your frontend dashboard.")

if __name__ == "__main__":
    process_feedback()