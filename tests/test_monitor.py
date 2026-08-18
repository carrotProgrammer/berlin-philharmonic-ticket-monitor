import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import requests

import monitor


def payload(available: int, bookable: int) -> dict:
    return {
        "availability": {
            "instances": [
                {
                    "vendor_id": "260201",
                    "availability": {"available": available},
                }
            ],
            "stats": {
                "instances_bookable": bookable,
                "instances_unavailable": 0 if bookable else 1,
            },
        },
        "onSaleSchedules": [
            {"status": "general", "date": "2026-03-26T11:59:00+00:00"}
        ],
    }


def locks(available: int, access: bool = False) -> dict:
    lock_information = []
    if access:
        lock_information.append(
            {
                "quantity": 2,
                "lockType": {
                    "name": "Wheelchair",
                    "availableOnWeb": True,
                    "requiresEligibility": True,
                },
            }
        )
    return {
        "id": monitor.EVENTS["2026-08-30"]["instance_id"],
        "status": {"available": available, "lockInformation": lock_information},
    }


class ClassificationTests(unittest.TestCase):
    event = monitor.EVENTS["2026-08-30"]
    now = datetime(2026, 8, 18, tzinfo=timezone.utc)

    def test_public_inventory_is_available(self):
        result = monitor.classify_payloads(
            self.event, payload(3, 1), locks(3), self.now
        )
        self.assertEqual(result.status, monitor.Status.AVAILABLE)
        self.assertEqual(result.ordinary_available, 3)

    def test_access_only_never_becomes_available(self):
        result = monitor.classify_payloads(
            self.event, payload(0, 0), locks(0, access=True), self.now
        )
        self.assertEqual(result.status, monitor.Status.UNAVAILABLE)
        self.assertEqual(result.access_only_quantity, 2)
        self.assertIn("Access seats", result.reason)

    def test_disagreement_is_unknown(self):
        result = monitor.classify_payloads(
            self.event, payload(2, 1), locks(0), self.now
        )
        self.assertEqual(result.status, monitor.Status.UNKNOWN)


class StateAndDedupTests(unittest.TestCase):
    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            store = monitor.StateStore(path)
            store.data["events"]["2026-08-29"]["last_status"] = "UNAVAILABLE"
            store.save()
            reloaded = monitor.StateStore(path)
            self.assertEqual(
                reloaded.load()["events"]["2026-08-29"]["last_status"],
                "UNAVAILABLE",
            )
            json.loads(path.read_text(encoding="utf-8"))

    def test_notification_dedup_and_rearm(self):
        entry = monitor.default_event_state()
        self.assertTrue(monitor.update_alert_latch(entry, monitor.Status.AVAILABLE))
        self.assertFalse(monitor.update_alert_latch(entry, monitor.Status.AVAILABLE))
        self.assertFalse(monitor.update_alert_latch(entry, monitor.Status.UNKNOWN))
        self.assertFalse(monitor.update_alert_latch(entry, monitor.Status.AVAILABLE))
        self.assertFalse(monitor.update_alert_latch(entry, monitor.Status.UNAVAILABLE))
        self.assertTrue(monitor.update_alert_latch(entry, monitor.Status.AVAILABLE))


class ResilienceAndConfigTests(unittest.TestCase):
    def test_network_error_returns_unknown(self):
        logger = Mock()
        checker = monitor.AvailabilityChecker(logger)
        checker.session.get = Mock(side_effect=requests.ConnectionError("offline"))
        result = checker.check(monitor.EVENTS["2026-08-29"])
        self.assertEqual(result.status, monitor.Status.UNKNOWN)
        self.assertIn("网络错误", result.reason)

    def test_event_configuration(self):
        first = monitor.EVENTS["2026-08-29"]
        second = monitor.EVENTS["2026-08-30"]
        self.assertEqual(first["vendor_id"], "260401")
        self.assertEqual(second["vendor_id"], "260201")
        self.assertIn("elgar-tchaikovsky", first["event_url"])
        self.assertIn("closing-concert", second["event_url"])

    def test_batch_files_use_own_directory(self):
        for name in ("start_monitor.bat", "test_notification.bat", "check_once.bat"):
            text = (monitor.APP_DIR / name).read_text(encoding="utf-8")
            self.assertIn('cd /d "%~dp0"', text)


if __name__ == "__main__":
    unittest.main()
