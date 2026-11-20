import tempfile
import unittest
from pathlib import Path

from app.database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(str(Path(self.tmp.name) / "test.db"))
        self.db.initialize()

    def test_queue_and_history(self):
        message_id = self.db.create("+441234567890", "Hello")
        self.assertEqual(self.db.claim()["id"], message_id)
        self.assertIsNone(self.db.claim())
        self.db.update(message_id, "sent", reference=42)
        message = self.db.get(message_id)
        self.assertEqual(message["modem_reference"], 42)
        self.assertEqual([event["status"] for event in message["events"]], ["queued", "sending", "sent"])

    def test_interrupted_send_becomes_unknown(self):
        message_id = self.db.create("+441234567890", "Hello")
        self.db.claim()
        self.db.initialize()
        self.assertEqual(self.db.get(message_id)["status"], "unknown")

    def test_incoming_archive(self):
        self.db.archive_incoming(7, "001122")
        self.assertEqual(self.db.list_incoming(10, 0)[0]["pdu"], "001122")


if __name__ == "__main__":
    unittest.main()
