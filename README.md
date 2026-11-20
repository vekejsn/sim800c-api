# SIM800 SMS API

A local HTTP API that stores outbound SMS requests in SQLite and sends them
one at a time through a SIM800 modem. Each message has an ID and an event
history.

## License

Licensed under the GNU General Public License, version 3 or any later version
(`GPL-3.0-or-later`). See [LICENSE](LICENSE).

## Setup

1. Install Python 3.10 or newer and create a virtual environment:

   ```sh
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Insert a working SIM with SMS service,
   and connect the modem. Identify its serial AT-command port with
   `ls /dev/ttyUSB* /dev/ttyACM*`. Your user needs permission to access the port
   (on many Linux systems, membership of the `dialout` group; log in again after
   changing group membership). Ensure the modem has a suitable power supply.
3. Start **one** API process:

   ```sh
   SMS_PORT=/dev/ttyUSB0 SMS_BAUDRATE=115200 uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

   Set `SMS_DB` to change the SQLite file path (default `messages.db`). Use
   the baud rate configured on your modem. Some USB devices expose multiple
   serial ports; choose the one that responds to AT commands. Do not use
   multiple Uvicorn workers or instances against the same modem/database.

   If the SIM requires a PIN, set `SMS_SIM_PIN` before starting the API. To
   avoid putting it in shell history, use `read -rs SMS_SIM_PIN; export SMS_SIM_PIN`
   and type the PIN when prompted. The worker submits it only when the modem
   reports `SIM PIN`, on the next send. A rejected or timed-out PIN submission
   is not retried during that process's lifetime. Check the PIN before restarting:
   repeated incorrect attempts can lock the SIM and require its PUK. Environment
   variables may be visible to other processes with sufficient permissions.

## Use

```sh
curl -X POST http://127.0.0.1:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"recipient":"+441234567890","body":"Hello from SIM800"}'
curl http://127.0.0.1:8000/messages/1
curl 'http://127.0.0.1:8000/messages?limit=50&offset=0'
```

Interactive API documentation is at `http://127.0.0.1:8000/docs`.
Recipients can use international E.164 format (for example `+386...`) or a
digits-only national number. National number routing depends on the SIM's
network; international format is more portable. GSM-7 messages can contain up
to 160 septets in one SMS, or 153 per part; GSM extension characters count as
two septets. Other text is encoded as UTF-16BE, with room for 70 code units in
one SMS or 67 per part. Some characters, including emoji, use two code units.
Messages are limited to 255 parts. Each part is billed as a separate SMS by
most carriers. Phone and carrier support for concatenation and emoji can vary.

## Status and safety

- `queued`: persisted, waiting to send.
- `sending`: claimed by the worker.
- `sent`: the modem accepted every part and returned a reference for each.
  This is **not** confirmation of delivery to the recipient.
- `failed`: the modem rejected the request or an error occurred before submission.
- `unknown`: submission may have occurred, but its result could not be confirmed.
  A restart also marks interrupted `sending` messages as `unknown`.

Queued messages survive restarts. There is no automatic retry: review failures
and uncertain outcomes before submitting again to avoid duplicate SMS. The
message detail includes each part's status and modem reference; if a part fails,
subsequent parts remain queued but are not sent. Earlier parts may already have
been accepted. Delivery
reports, inbound SMS, authentication, encryption, and rate limiting are not
implemented. The API stores phone numbers and message contents in plaintext;
keep it bound to localhost and protect the database and backups.

Before submitting each part, the worker waits up to 60 seconds for the SIM to
be ready and for home or roaming network registration. Registration alone does
not guarantee that outgoing SMS service is available. Serial communication
errors and a rejected registration fail the attempt without retrying it.
