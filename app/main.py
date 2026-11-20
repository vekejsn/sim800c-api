import os
import re
import threading
import ipaddress
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from .database import Database
from .modem import Modem, ModemError, ModemUncertainError
from .pdu import encode

db = Database(os.getenv("SMS_DB", "messages.db"))
sim_pin = os.getenv("SMS_SIM_PIN")
if sim_pin and not re.fullmatch(r"[0-9]{4,8}", sim_pin):
    raise ValueError("SMS_SIM_PIN must contain 4 to 8 digits")
modem = Modem(os.getenv("SMS_PORT", "/dev/ttyUSB0"), int(os.getenv("SMS_BAUDRATE", "115200")), sim_pin)
incoming_mode = os.getenv("SMS_INCOMING_MODE", "archive").lower()
if incoming_mode not in ("archive", "discard", "off"):
    raise ValueError("SMS_INCOMING_MODE must be archive, discard, or off")
logger = logging.getLogger(__name__)


class MessageRequest(BaseModel):
    recipient: str
    body: str

    @field_validator("recipient")
    @classmethod
    def validate_recipient(cls, value):
        if not re.fullmatch(r"(?:\+[1-9][0-9]{1,14}|[0-9]{1,20})", value):
            raise ValueError("Use digits with an optional leading + (up to 15 international or 20 national digits)")
        return value

    @field_validator("body")
    @classmethod
    def validate_body(cls, value):
        if not value:
            raise ValueError("Message must not be empty")
        try:
            encode("+123", value)
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("Invalid text or message exceeds 255 SMS parts") from exc
        return value


def worker(stop):
    next_cleanup = time.monotonic()
    while not stop.is_set():
        message = db.claim()
        if message is None:
            if incoming_mode != "off" and time.monotonic() >= next_cleanup:
                next_cleanup = time.monotonic() + 30
                try:
                    modem.cleanup_incoming(db.archive_incoming if incoming_mode == "archive" else None)
                except Exception:
                    logger.exception("Incoming SMS cleanup failed; will retry")
            stop.wait(1)
            continue
        parts = encode(message["recipient"], message["body"])
        db.prepare_parts(message["id"], len(parts))
        for number, part in enumerate(parts, 1):
            db.update_part(message["id"], number, "sending")
            try:
                reference = modem.send(part["pdu"], part["length"])
            except (ModemError, ModemUncertainError) as exc:
                status = "unknown" if isinstance(exc, ModemUncertainError) else "failed"
                db.update_part(message["id"], number, status, error=str(exc))
                db.update(message["id"], status, f"Part {number}/{len(parts)}: {exc}")
                break
            else:
                db.update_part(message["id"], number, "sent", reference=reference)
        else:
            db.update(message["id"], "sent", reference=reference if len(parts) == 1 else None)


@asynccontextmanager
async def lifespan(app):
    db.initialize()
    stop = threading.Event()
    thread = threading.Thread(target=worker, args=(stop,), daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=75)


app = FastAPI(title="SIM800 SMS API", lifespan=lifespan)


@app.get("/modem/status")
def modem_status(request: Request):
    allow_remote = os.getenv("SMS_EXPOSE_IDENTIFIERS", "").lower() in ("1", "true", "yes")
    try:
        local = bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
    except ValueError:
        local = False
    # Proxies can rewrite the client address; never treat forwarded requests as local.
    if "forwarded" in request.headers or "x-forwarded-for" in request.headers:
        local = False
    result = modem.status(include_identifiers=local or allow_remote)
    result["queue"] = db.counts()
    return result


@app.post("/messages", status_code=202)
def create_message(message: MessageRequest):
    message_id = db.create(message.recipient, message.body)
    return db.get(message_id)


@app.get("/messages")
def list_messages(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    return db.list(limit, offset)


@app.get("/incoming")
def list_incoming(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    return db.list_incoming(limit, offset)


@app.get("/messages/{message_id}")
def get_message(message_id: int):
    message = db.get(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return message
