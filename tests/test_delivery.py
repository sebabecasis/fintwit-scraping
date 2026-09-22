import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from src.delivery import deliver, deliver_live, save


class DeliveryTests(unittest.TestCase):
    def test_disabled_delivery_is_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.json"
            save(path, {"synthetic": True})
            state = deliver_live(path, no_publish=True, no_email=True)
            self.assertNotIn("email", state)
            self.assertNotIn("publish", state)

    def test_completed_stages_not_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.json"
            save(path, {"synthetic": True})
            publish = Mock(return_value="https://example.test/report")
            send = Mock(return_value={"id": "receipt"})
            deliver(path, publish=publish)
            state = deliver(path, publish=publish, send=send, email_to="reader@example.test")
            self.assertEqual("success", state["email"]["status"])
            self.assertEqual("https://example.test/report", send.call_args.args[1])
            deliver(path, publish=publish, send=send, email_to="reader@example.test")
            publish.assert_called_once()
            send.assert_called_once()

    def test_uncertain_send_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.json"
            save(path, {"synthetic": True})
            send = Mock(side_effect=TimeoutError())
            with self.assertRaises(TimeoutError):
                deliver(path, send=send, email_to="reader@example.test")
            with self.assertRaisesRegex(RuntimeError, "uncertain"):
                deliver(path, send=send, email_to="reader@example.test")
            send.assert_called_once()
            self.assertFalse(path.with_suffix(".delivery.lock").exists())

    def test_modified_report_and_recipient_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.json"
            save(path, {"synthetic": True})
            deliver(path, send=lambda *a: {"id": "receipt"}, email_to="reader@example.test")
            with self.assertRaises(ValueError):
                deliver(path, send=Mock(), email_to="other@example.test")
            save(path, {"synthetic": False})
            with self.assertRaises(ValueError):
                deliver(path)

    def test_active_lock_blocks_concurrent_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.json"
            save(path, {})
            path.with_suffix(".delivery.lock").touch()
            with self.assertRaises(FileExistsError):
                deliver(path)

    def test_scheduler_import_has_no_database_or_execution_side_effect(self):
        path = Path(__file__).resolve().parents[1] / "scheduled_run.py"
        spec = importlib.util.spec_from_file_location("scheduled_for_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(module.main))
