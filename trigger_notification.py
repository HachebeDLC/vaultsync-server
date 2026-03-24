import requests
import sys
import json

# Configuration
BASE_URL = "http://localhost:5436" # Change this if your remote IP is different

def send_notification(email, password, message, target_device=None):
    print(f"🔑 Logging in as {email}...")
    try:
        login_resp = requests.post(f"{BASE_URL}/login", json={
            "email": email,
            "password": password
        })
        
        if login_resp.status_code != 200:
            print(f"❌ Login failed: {login_resp.text}")
            return

        token = login_resp.json().get("token")
        print("✅ Login successful.")

        print(f"📡 Sending notification: '{message}'...")
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "message": message,
            "target_device": target_device
        }
        
        notify_resp = requests.post(f"{BASE_URL}/events/test", json=payload, headers=headers)
        
        if notify_resp.status_code == 200:
            print("🚀 Notification successfully queued on server!")
        else:
            print(f"❌ Failed to queue notification: {notify_resp.text}")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python3 trigger_notification.py <email> <password> <message> [target_device]")
    else:
        email = sys.argv[1]
        password = sys.argv[2]
        message = sys.argv[3]
        target_device = sys.argv[4] if len(sys.argv) > 4 else None
        send_notification(email, password, message, target_device)
