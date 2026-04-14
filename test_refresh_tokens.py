import os
import requests
import sys

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:5436")

def test_auth_flow():
    email = os.environ.get("TEST_EMAIL", "test_refresh@example.com")
    password = os.environ.get("TEST_PASSWORD", "changeme_test_only")
    
    # 1. Register
    print("1. Registering...")
    resp = requests.post(f"{BASE_URL}/register", json={"email": email, "password": password})
    if resp.status_code != 200:
        print(f"Register failed: {resp.text}")
        if "already exists" not in resp.text:
            return
    else:
        print("Register success")

    # 2. Login
    print("\n2. Logging in...")
    resp = requests.post(f"{BASE_URL}/login", json={"email": email, "password": password})
    if resp.status_code != 200:
        print(f"Login failed: {resp.text}")
        return
    
    data = resp.json()
    access_token = data['token']
    refresh_token = data['refresh_token']
    print(f"Login success. Refresh token: {refresh_token[:10]}...")

    # 3. Refresh
    print("\n3. Refreshing token...")
    resp = requests.post(f"{BASE_URL}/refresh", json={"refresh_token": refresh_token})
    if resp.status_code != 200:
        print(f"Refresh failed: {resp.text}")
        return
    
    new_access_token = resp.json()['token']
    print("Refresh success. New access token obtained.")

    # 4. Use new token
    print("\n4. Using new access token...")
    resp = requests.get(f"{BASE_URL}/auth/me", headers={"Authorization": f"Bearer {new_access_token}"})
    if resp.status_code != 200:
        print(f"Auth me failed: {resp.text}")
        return
    print(f"Auth me success: {resp.json()}")

    # 5. Logout (Revoke)
    print("\n5. Logging out (revoking refresh token)...")
    resp = requests.post(f"{BASE_URL}/logout", json={"refresh_token": refresh_token}, headers={"Authorization": f"Bearer {new_access_token}"})
    if resp.status_code != 200:
        print(f"Logout failed: {resp.text}")
        return
    print("Logout success.")

    # 6. Try refresh again (should fail)
    print("\n6. Trying to refresh again (should fail)...")
    resp = requests.post(f"{BASE_URL}/refresh", json={"refresh_token": refresh_token})
    if resp.status_code == 401:
        print("Refresh failed as expected (revoked).")
    else:
        print(f"Error: Refresh succeeded or failed with unexpected code: {resp.status_code}")

if __name__ == "__main__":
    test_auth_flow()
