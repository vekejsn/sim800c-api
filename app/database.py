import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recipient TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    modem_reference INTEGER,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    detail TEXT,
                    FOREIGN KEY(message_id) REFERENCES messages(id)
                );
                CREATE TABLE IF NOT EXISTS parts (
                    message_id INTEGER NOT NULL,
                    part_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    modem_reference INTEGER,
                    error TEXT,
                    PRIMARY KEY (message_id, part_number),
                    FOREIGN KEY(message_id) REFERENCES messages(id)
                );
                CREATE TABLE IF NOT EXISTS incoming_sms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stored_at TEXT NOT NULL,
                    sim_index INTEGER NOT NULL,
                    pdu TEXT NOT NULL
                );
            """)
            # A process crash during transmission leaves the result unknown.
            rows = db.execute("SELECT id FROM messages WHERE status = 'sending'").fetchall()
            for row in rows:
                db.execute("UPDATE parts SET status='unknown', error=? WHERE message_id=? AND status='sending'",
                           ("Process stopped during transmission", row["id"]))
                self._update(db, row["id"], "unknown", "Process stopped during transmission; inspect before retrying.")

    def _update(self, db, message_id, status, detail=None, reference=None):
        timestamp = now()
        db.execute(
            "UPDATE messages SET status=?, updated_at=?, error=?, modem_reference=? WHERE id=?",
            (status, timestamp, detail if status in ("failed", "unknown") else None, reference, message_id),
        )
        db.execute(
            "INSERT INTO events (message_id, status, timestamp, detail) VALUES (?, ?, ?, ?)",
            (message_id, status, timestamp, detail),
        )

    def create(self, recipient, body):
        timestamp = now()
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO messages (recipient, body, status, created_at, updated_at) VALUES (?, ?, 'queued', ?, ?)",
                (recipient, body, timestamp, timestamp),
            )
            db.execute(
                "INSERT INTO events (message_id, status, timestamp) VALUES (?, 'queued', ?)",
                (cursor.lastrowid, timestamp),
            )
            return cursor.lastrowid

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM messages WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            self._update(db, row["id"], "sending")
            return dict(row)

    def update(self, message_id, status, detail=None, reference=None):
        with self.connect() as db:
            self._update(db, message_id, status, detail, reference)

    def prepare_parts(self, message_id, count):
        with self.connect() as db:
            db.executemany("INSERT INTO parts (message_id, part_number, status) VALUES (?, ?, 'queued')",
                           [(message_id, number) for number in range(1, count + 1)])

    def update_part(self, message_id, number, status, reference=None, error=None):
        with self.connect() as db:
            db.execute("UPDATE parts SET status=?, modem_reference=?, error=? WHERE message_id=? AND part_number=?",
                       (status, reference, error, message_id, number))

    def get(self, message_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["events"] = [dict(event) for event in db.execute(
                "SELECT status, timestamp, detail FROM events WHERE message_id=? ORDER BY id", (message_id,)
            )]
            result["parts"] = [dict(part) for part in db.execute(
                "SELECT part_number, status, modem_reference, error FROM parts WHERE message_id=? ORDER BY part_number",
                (message_id,)
            )]
            return result

    def list(self, limit, offset):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM messages ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            )]

    def counts(self):
        with self.connect() as db:
            counts = {status: 0 for status in ("queued", "sending", "sent", "failed", "unknown")}
            counts.update({row["status"]: row["total"] for row in db.execute(
                "SELECT status, COUNT(*) AS total FROM messages GROUP BY status"
            )})
            return counts

    def archive_incoming(self, index, pdu):
        with self.connect() as db:
            db.execute("INSERT INTO incoming_sms (stored_at, sim_index, pdu) VALUES (?, ?, ?)",
                       (now(), index, pdu))

    def list_incoming(self, limit, offset):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM incoming_sms ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            )]
