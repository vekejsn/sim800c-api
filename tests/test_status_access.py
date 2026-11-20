import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.main import modem_status


class StatusAccessTests(unittest.TestCase):
    def check_access(self, host, headers, allowed, env=None):
        request = SimpleNamespace(client=SimpleNamespace(host=host), headers=headers)
        with patch.dict("os.environ", env or {}, clear=True), \
             patch("app.main.modem.status", return_value={"state": "available"}) as status, \
             patch("app.main.db.counts", return_value={"queued": 0}):
            result = modem_status(request)
        status.assert_called_once_with(include_identifiers=allowed)
        self.assertEqual(result["queue"]["queued"], 0)

    def test_loopback(self):
        self.check_access("127.0.0.1", {}, True)
        self.check_access("::1", {}, True)

    def test_remote_and_forwarded(self):
        self.check_access("192.0.2.1", {}, False)
        self.check_access("127.0.0.1", {"x-forwarded-for": "192.0.2.1"}, False)

    def test_remote_opt_in(self):
        self.check_access("192.0.2.1", {}, True, {"SMS_EXPOSE_IDENTIFIERS": "true"})


if __name__ == "__main__":
    unittest.main()
