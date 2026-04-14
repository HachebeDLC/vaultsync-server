import os
import requests
import json
import threading
import time
import sseclient # pip install sseclient-py

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:5436")

def listen_for_events(user_token, stop_event, results):
    headers = {
        'Authorization': f'Bearer {user_token}',
        'Accept': 'text/event-stream'
    }
    try:
        response = requests.get(f"{BASE_URL}/api/v1/events", headers=headers, stream=True, timeout=10)
        client = sseclient.SSEClient(response)
        for event in client.events():
            print(f"📥 Received event: {event.event} - {event.data}")
            results.append(event)
            if stop_event.is_set():
                break
    except Exception as e:
        if not stop_event.is_set():
            print(f"❌ SSE Error: {e}")

def test_sse_broadcast():
    email = os.environ.get("TEST_EMAIL", "sse_test@example.com")
    password = os.environ.get("TEST_PASSWORD", "changeme_test_only")
    
    # 1. Register/Login
    print("1. Authenticating...")
    requests.post(f"{BASE_URL}/register", json={"email": email, "password": password})
    resp = requests.post(f"{BASE_URL}/login", json={"email": email, "password": password})
    data = resp.json()
    token = data['token']
    
    # 2. Start SSE listener in a thread
    print("2. Starting SSE listener...")
    stop_event = threading.Event()
    results = []
    listener_thread = threading.Thread(target=listen_for_events, args=(token, stop_event, results))
    listener_thread.daemon = True
    listener_thread.start()
    
    # Wait for connection to establish
    time.sleep(2)
    
    # 3. Trigger a test notification via API
    print("3. Triggering test notification...")
    test_msg = f"Hello SSE {time.time()}"
    requests.get(f"{BASE_URL}/api/v1/events/test?message={test_msg}", headers={'Authorization': f'Bearer {token}'})
    
    # 4. Wait for event
    time.sleep(3)
    stop_event.set()
    
    if any(test_msg in str(r.data) for r in results):
        print("\n✅ SUCCESS: SSE event received successfully!")
    else:
        print("\n❌ FAILURE: SSE event not received.")

if __name__ == "__main__":
    test_sse_broadcast()
