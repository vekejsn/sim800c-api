import os
import re
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, field_validator

from .database import Database
from .modem import Modem, ModemError, ModemUncertainError
from .pdu import encode

db = Database(os.getenv("SMS_DB", "messages.db"))
sim_pin = os.getenv("SMS_SIM_PIN")
if sim_pin and not re.fullmatch(r"[0-9]{4,8}", sim_pin):
    raise ValueError("SMS_SIM_PIN must contain 4 to 8 digits")
modem = Modem(os.getenv("SMS_PORT", "/dev/ttyUSB0"), int(os.getenv("SMS_BAUDRATE", "115200")), sim_pin)


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
    while not stop.is_set():
        message = db.claim()
        if message is None:
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


@app.post("/messages", status_code=202)
def create_message(message: MessageRequest):
    message_id = db.create(message.recipient, message.body)
    return db.get(message_id)


@app.get("/messages")
def list_messages(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    return db.list(limit, offset)


@app.get("/messages/{message_id}")
def get_message(message_id: int):
    message = db.get(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return message
