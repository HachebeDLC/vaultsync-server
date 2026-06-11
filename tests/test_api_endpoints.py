import unittest
import requests

SERVER_URL = "http://localhost:5436"

class TestRecoveryAPI(unittest.TestCase):
    def test_setup_endpoint_exists(self):
        resp = requests.post(f"{SERVER_URL}/api/v1/auth/recovery/setup", json={
            "recovery_payload": "test_payload",
            "recovery_salt": "test_salt"
        })
        self.assertEqual(resp.status_code, 401, "Expected 401 Unauthorized for setup without token")

    def test_payload_endpoint_exists(self):
        resp = requests.post(f"{SERVER_URL}/api/v1/auth/recovery/payload", json={
            "email": "test@example.com"
        })
        self.assertEqual(resp.status_code, 404, "Expected 404 for missing user")
        self.assertIn("Recovery information not found", resp.text)

if __name__ == "__main__":
    unittest.main()
