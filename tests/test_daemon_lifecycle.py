import unittest
from unittest import mock

from floability_remote.application.errors import OperationError
from floability_remote.daemon import API_REVISION
from floability_remote.daemon.lifecycle import _retire_incompatible_daemon
from floability_remote.daemon.session import DaemonDescriptor


class DaemonCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.descriptor = DaemonDescriptor(123, 8123, "token", 1.0)
        self.store = object()

    @mock.patch("floability_remote.daemon.lifecycle.stop")
    @mock.patch("floability_remote.daemon.lifecycle.DaemonClient")
    def test_current_revision_is_reused(self, client_type, stop):
        client_type.return_value.meta.return_value = {
            "api_revision": API_REVISION
        }

        self.assertTrue(
            _retire_incompatible_daemon(self.descriptor, self.store)
        )
        stop.assert_not_called()

    @mock.patch("floability_remote.daemon.lifecycle.stop")
    @mock.patch("floability_remote.daemon.lifecycle.DaemonClient")
    def test_idle_old_revision_is_retired(self, client_type, stop):
        client_type.return_value.meta.return_value = {}

        self.assertFalse(
            _retire_incompatible_daemon(self.descriptor, self.store)
        )
        stop.assert_called_once_with(store=self.store)

    @mock.patch("floability_remote.daemon.lifecycle.stop")
    @mock.patch("floability_remote.daemon.lifecycle.DaemonClient")
    def test_active_old_revision_requires_explicit_cleanup(self, client_type, stop):
        client_type.return_value.meta.return_value = {}
        stop.side_effect = OperationError("daemon_busy", "active", status=409)

        with self.assertRaisesRegex(OperationError, "older installation") as raised:
            _retire_incompatible_daemon(self.descriptor, self.store)

        self.assertEqual(raised.exception.code, "daemon_update_required")


if __name__ == "__main__":
    unittest.main()
