import contextlib
import importlib
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import src


class OrchestratorTests(unittest.TestCase):
    def load_runner(self):
        with patch.dict(sys.modules, {"dotenv": types.SimpleNamespace(load_dotenv=lambda **kw: None)}):
            return importlib.import_module("run_weekly")

    def test_no_email_also_suppresses_credit_alert(self):
        runner = self.load_runner()
        email = Mock()
        fetch = Mock()
        fetch.sync_roster.side_effect = RuntimeError("synthetic credit failure")
        store = Mock()
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = [0]
        store.get_db.side_effect = lambda: contextlib.nullcontext(conn)
        analysis = types.SimpleNamespace(**{name: Mock() for name in (
            "score_mentions", "divergence_summaries", "weekly_narrative", "new_ticker_summaries")})
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, "_ROOT", Path(directory)), \
            patch.object(runner, "classify_credit_error", return_value="getxapi"), \
            patch.dict(sys.modules, {"src.analyze_claude": analysis}), \
            patch.multiple(src, store=store, fetch=fetch, extract=Mock(), analyze_compute=Mock(), report_md=Mock(), report_html=Mock(), gist=Mock(), email=email, create=True), \
            patch.object(sys, "argv", ["run_weekly.py", "--week", "2026-09-14", "--no-email", "--no-publish"]), \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                runner.main()
            self.assertEqual(1, result.exception.code)
            email.send_alert_email.assert_not_called()
            email.send_weekly_email.assert_not_called()
            self.assertEqual("error", store.log_run.call_args.kwargs["status"])

    def test_existing_bundle_stops_before_paid_imports(self):
        runner = self.load_runner()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            (root / "reports/2026-09-14.bundle.json").write_text("{}")
            with patch.object(runner, "_ROOT", root), patch.object(sys, "argv", ["run_weekly.py", "--week", "2026-09-14"]):
                with self.assertRaisesRegex(RuntimeError, "no paid work started"):
                    runner.main()

    def test_week_requires_monday(self):
        with self.assertRaises(ValueError):
            self.load_runner()._week_window("2026-09-15")
