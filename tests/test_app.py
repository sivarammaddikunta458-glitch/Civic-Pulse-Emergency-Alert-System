import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as civic_pulse


class CivicPulseApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.db_path_patch = patch.object(civic_pulse, "DB_PATH", self.db_path)
        self.db_path_patch.start()
        civic_pulse.initialize_database()
        self.client = civic_pulse.app.test_client()
        response = self.client.post("/api/users", json={"name": "Test Citizen", "phone": "+123456789"})
        self.user_id = response.get_json()["id"]

    def tearDown(self):
        self.db_path_patch.stop()
        self.temp_dir.cleanup()

    def test_create_alert_generates_location_link_from_valid_coordinates(self):
        response = self.client.post(
            "/api/alerts",
            json={
                "user_id": self.user_id,
                "category": "Medical Emergency",
                "latitude": 51.5,
                "longitude": -0.12,
            },
        )

        self.assertEqual(response.status_code, 201)
        alert = response.get_json()["alert"]
        self.assertEqual(alert["status"], "Saved Offline")
        self.assertIn("mlat=51.5", alert["map_url"])

    def test_create_alert_rejects_out_of_range_coordinates(self):
        response = self.client.post(
            "/api/alerts",
            json={
                "user_id": self.user_id,
                "category": "Accident",
                "latitude": 91,
                "longitude": 0,
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_request_id_makes_alert_creation_idempotent(self):
        payload = {
            "user_id": self.user_id,
            "category": "Personal Safety",
            "request_id": "77c65d16-0b83-4d05-bdcb-67e32490bad0",
        }

        first = self.client.post("/api/alerts", json=payload)
        second = self.client.post("/api/alerts", json=payload)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()["duplicate"])
        self.assertEqual(len(self.client.get("/api/alerts").get_json()), 1)

    def test_registration_rejects_duplicate_phone(self):
        response = self.client.post("/api/users", json={"name": "Another Citizen", "phone": "+123456789"})

        self.assertEqual(response.status_code, 409)

    def test_api_rejects_non_object_json(self):
        response = self.client.post("/api/users", json=["not", "an", "object"])

        self.assertEqual(response.status_code, 400)

    def test_health_and_homepage_are_available(self):
        health = self.client.get("/api/health")
        homepage = self.client.get("/")

        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.get_json()["ok"])
        self.assertEqual(homepage.status_code, 200)

    def test_legacy_alert_table_is_migrated(self):
        with civic_pulse.db_connection() as connection:
            connection.execute("DROP INDEX IF EXISTS idx_alerts_client_request_id")
            connection.execute("DROP TABLE alerts")
            connection.execute(
                """
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    location_link TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
                """
            )

        civic_pulse.initialize_database()

        with civic_pulse.db_connection() as connection:
            column_names = {row["name"] for row in connection.execute("PRAGMA table_info(alerts)")}
        self.assertIn("client_request_id", column_names)


if __name__ == "__main__":
    unittest.main()
