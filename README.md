# SIM800 SMS API

Send SMS through a USB SIM800 modem over HTTP. Messages are queued in SQLite,
with a history and a status for each SMS part. Unicode and multipart messages
are supported.

## Get started

You'll need Python 3.10+, a SIM800 modem, and a SIM with SMS service. The modem
also needs a reliable power supply and access to a 2G network.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

SMS_PORT=/dev/ttyUSB0 uvicorn app.main:app --no-proxy-headers --host 127.0.0.1 --port 8000
```

Find your modem's port with `ls /dev/ttyUSB* /dev/ttyACM*`. Your user needs
access to it (often through the `dialout` group on Linux). If the device exposes
several ports, use the one that responds to AT commands. Run only one API process
per modem and database.

If your SIM needs a PIN, enter it before starting the server:

```sh
read -rs SMS_SIM_PIN
export SMS_SIM_PIN
```

The PIN won't appear on screen or be stored in the database. The API submits it
only when the SIM asks for it. If submission fails or times out, check the PIN
before restarting; repeated wrong attempts can lock the SIM.

## Send a message

```sh
curl -X POST http://127.0.0.1:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"recipient":"+441234567890","body":"Hello from SIM800"}'
```

The response contains a message ID. Use it to check progress:

```sh
curl http://127.0.0.1:8000/messages/1
curl 'http://127.0.0.1:8000/messages?limit=50&offset=0'
curl http://127.0.0.1:8000/modem/status
curl 'http://127.0.0.1:8000/incoming?limit=50&offset=0'
```

You can also use the interactive API docs at **http://127.0.0.1:8000/docs**.
Numbers can be international (`+441234567890`) or national (`069123456`);
national routing depends on the SIM's network.

Long messages are split into SMS parts, which carriers usually charge
separately. The limit is 255 parts. Character limits depend on the encoding:
GSM-7 fits 160 characters in one SMS or 153 per part; Unicode fits up to 70
UTF-16 code units in one SMS or 67 per part. Some characters count as two units.

## Message status

| Status | Meaning |
| --- | --- |
| `queued` | Saved and waiting to send. |
| `sending` | Being processed by the modem. |
| `sent` | The modem accepted every part; this does **not** confirm delivery. |
| `failed` | The modem rejected a part or an error occurred before submission. |
| `unknown` | A part may have been submitted, but its outcome couldn't be confirmed. |

The detail endpoint shows each part and its modem reference. If a part fails,
later parts aren't sent; earlier parts may already have been accepted. There
are no automatic retries, so check before resending to avoid duplicates.
Interrupted sends become `unknown` after a restart.

The worker waits up to 60 seconds for SIM readiness and network registration
before sending each part. Registration doesn't guarantee that SMS will work.
Delivery reports aren't implemented.

## Incoming SMS

When idle, the API checks SIM storage roughly every 30 seconds. By default it
saves each received SMS part as a raw PDU in SQLite, then deletes that part
from the SIM. `GET /incoming` lists archived PDUs; the API doesn't decode or
reassemble them. The list operation can mark unread SIM messages as read.

Set `SMS_INCOMING_MODE=discard` to delete received SMS without saving them, or
`SMS_INCOMING_MODE=off` to leave them on the SIM. Cleanup starts when the
server starts, including for messages already stored on the SIM. If deletion
fails after archiving, a later attempt can create a duplicate archive entry.
Outgoing messages stored on the SIM are left alone.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `SMS_PORT` | `/dev/ttyUSB0` | Modem serial port |
| `SMS_BAUDRATE` | `115200` | Serial baud rate |
| `SMS_DB` | `messages.db` | SQLite database path |
| `SMS_SIM_PIN` | unset | SIM PIN, if required |
| `SMS_INCOMING_MODE` | `archive` | `archive`, `discard`, or `off` |
| `SMS_EXPOSE_IDENTIFIERS` | unset | Set to `true` to include modem and SIM identifiers for remote clients |

The modem status endpoint reports SIM and network status, signal, storage,
device details and queue counts. Unsupported readings are `null`. IMEI, SIM
serial and IMSI are included for localhost clients only unless
`SMS_EXPOSE_IDENTIFIERS=true`.

## Security

The API has **no authentication or rate limiting**. Keep it on localhost or
protect it with separate access controls. Message contents and phone numbers
are stored in plaintext; protect the database and backups.

Localhost-only identifiers depend on the real client address. Run Uvicorn with
`--no-proxy-headers`, and don't place the API behind a proxy or port forwarder
that makes remote clients appear local. The API treats `Forwarded` and
`X-Forwarded-For` requests as remote, but a proxy could remove those headers.
Environment variables, including the SIM PIN, may be visible to other processes
with sufficient permissions.

## License

[GPL-3.0-or-later](LICENSE).
