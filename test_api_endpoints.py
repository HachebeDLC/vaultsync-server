import unittest
import requests

SERVER_URL = "http://localhost:8000"

class TestRecoveryAPI(unittest.TestCase):
    def test_setup_endpoint_exists(self):
        resp = requests.post(f"{SERVER_URL}/api/v1/auth/recovery/setup", json={
            "recovery_payload": "test_payload",
            "recovery_salt": "test_salt"
        })
        # Expecting 401 if not logged in, but 404 if not implemented
        self.assertNotEqual(resp.status_code, 404, "Recovery setup endpoint missing")

    def test_payload_endpoint_exists(self):
        resp = requests.post(f"{SERVER_URL}/api/v1/auth/recovery/payload", json={
            "email": "test@example.com"
        })
        self.assertNotEqual(resp.status_code, 404, "Recovery payload endpoint missing")

if __name__ == "__main__":
    unittest.main()
