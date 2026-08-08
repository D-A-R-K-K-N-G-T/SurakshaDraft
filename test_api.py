import requests
import json

url = "http://localhost:3000/api/commercial/submit"

# The text form data
data = {
    "product": "Standard Flood Protection",
    "clauses": "Flood damage to stock, machinery, and office electronics is covered.",
    "categories": "Stock,Machinery,Electronics",
    "event_date": "2026-08-01T00:00:00Z",
    "description": "Shop flooded due to heavy rains."
}

# The image to upload
files = {
    "files": ("broken_car.jpg", open("broken_car.jpg", "rb"), "image/jpeg")
}

print(f"Sending POST request to {url}...")
try:
    response = requests.post(url, data=data, files=files)
    response_data = response.json()
    print("\n--- RESPONSE FROM EXPRESS JS ---")
    print(f"Status Code: {response.status_code}")
    print(json.dumps(response_data, indent=2))
    
    if response_data.get("success"):
        claim_id = response_data["data"]["claim_id"]
        status_url = f"http://localhost:3000/api/claim/{claim_id}"
        
        print(f"\nWaiting for AI pipeline to finish processing {claim_id}...")
        import time
        while True:
            time.sleep(3)
            status_res = requests.get(status_url).json()
            status = status_res.get("status")
            print(f"Current Status: {status}...")
            
            if status == "completed":
                print("\n🎉 PIPELINE COMPLETE! 🎉\n")
                print("--- FINAL DRAFT PACK ---")
                draft = status_res.get("result", {}).get("draft_pack", {})
                print(draft.get("main_schedule", "No main schedule"))
                print("\n")
                print(draft.get("rejected_items_annexure", "No rejections"))
                break
            elif status == "failed":
                print("\n❌ Pipeline failed!")
                print(status_res.get("error"))
                break

except Exception as e:
    print(f"Error connecting to Express: {e}")
