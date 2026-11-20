import re
import threading
import time

import serial


class ModemError(Exception):
    pass


class ModemUncertainError(Exception):
    """The modem may have accepted the SMS, so automatic retry is unsafe."""


class Modem:
    def __init__(self, port, baudrate, pin=None):
        self.port = port
        self.baudrate = baudrate
        self.pin = pin
        self.pin_rejected = False
        self.lock = threading.Lock()

    def _wait_ready(self, device, timeout=60):
        deadline = time.monotonic() + timeout
        last_status = "no response"
        while time.monotonic() < deadline:
            device.write(b"AT+CPIN?\r")
            response = self._read_until(device, "\r\nOK\r\n", 5)
            if "+CPIN: SIM PIN" in response:
                if not self.pin:
                    raise ModemError("SIM requires a PIN; configure SMS_SIM_PIN")
                if self.pin_rejected:
                    raise ModemError("SIM PIN was rejected earlier; restart only after checking the PIN")
                device.write(f'AT+CPIN="{self.pin}"\r'.encode("ascii"))
                try:
                    self._read_until(device, "\r\nOK\r\n", 10)
                except (ModemError, TimeoutError):
                    self.pin_rejected = True
                    raise ModemError("SIM PIN submission failed or timed out; check the SIM before restarting") from None
                last_status = "SIM unlocking"
            elif "+CPIN: READY" in response:
                device.write(b"AT+CREG?\r")
                registration = self._read_until(device, "\r\nOK\r\n", 5)
                match = re.search(r"\+CREG:\s*\d+\s*,\s*(\d+)", registration)
                if match and match.group(1) in ("1", "5"):
                    return
                last_status = f"network registration: {registration.strip()}"
                if match and match.group(1) == "3":
                    raise ModemError("Network registration denied")
            else:
                last_status = f"SIM status: {response.strip()}"
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        raise ModemError(f"Modem not ready after {timeout} seconds ({last_status})")

    @staticmethod
    def _read_until(device, expected, timeout):
        deadline = time.monotonic() + timeout
        data = bytearray()
        text = ""
        while time.monotonic() < deadline:
            data.extend(device.read(1))
            text = data.decode("ascii", errors="replace")
            if re.search(r"(?:^|\r\n)(?:ERROR|\+CMS ERROR:.*|\+CME ERROR:.*)\r\n", text):
                raise ModemError(text.strip())
            if expected in text:
                return text
        raise TimeoutError(f"Timed out waiting for {expected!r}; received {text!r}")

    def status(self, include_identifiers=False):
        if not self.lock.acquire(blocking=False):
            return {"state": "busy", "detail": "Modem is sending an SMS"}
        try:
            with serial.Serial(self.port, self.baudrate, timeout=0.2, write_timeout=5) as device:
                device.reset_input_buffer()
                responses = {}
                for command in ("AT", "ATE0", "AT+CPIN?", "AT+CREG?", "AT+CGREG?", "AT+COPS?", "AT+CSQ"):
                    device.write((command + "\r").encode("ascii"))
                    responses[command] = self._read_until(device, "\r\nOK\r\n", 5)
                optional = ["AT+CSCA?", "AT+CPMS?", "AT+CGMI", "AT+CGMM", "AT+CGMR", "AT+CBC"]
                if include_identifiers:
                    optional += ["AT+CGSN", "AT+CCID", "AT+CIMI"]
                for command in optional:
                    device.write((command + "\r").encode("ascii"))
                    try:
                        responses[command] = self._read_until(device, "\r\nOK\r\n", 3)
                    except ModemError:
                        responses[command] = ""
                    except TimeoutError:
                        # A late response could be mistaken for the next command's.
                        break

                def match(pattern, command):
                    result = re.search(pattern, responses.get(command, ""))
                    return result.group(1).strip() if result else None
                def number(pattern, command):
                    value = match(pattern, command)
                    return int(value) if value is not None else None

                def line(command):
                    return match(r"(?:^|\r\n)(?!OK\r|AT[+\r])([^\r\n]+)\r\n", command)
                registration = match(r"\+CREG:\s*\d+\s*,\s*(\d+)", "AT+CREG?")
                packet = match(r"\+CGREG:\s*\d+\s*,\s*(\d+)", "AT+CGREG?")
                rssi = match(r"\+CSQ:\s*(\d+)", "AT+CSQ")
                signal = int(rssi) if rssi is not None else None
                status = {
                    "state": "available",
                    "sim_status": match(r"\+CPIN:\s*([^\r\n]+)", "AT+CPIN?"),
                    "network_registration": int(registration) if registration is not None else None,
                    "packet_registration": int(packet) if packet is not None else None,
                    "operator": match(r'\+COPS:\s*\d+\s*,\s*\d+\s*,\s*"([^"]+)"', "AT+COPS?"),
                    "signal_rssi": signal,
                    "signal_dbm": -113 + 2 * signal if signal is not None and 0 <= signal <= 31 else None,
                    "signal_bit_error_rate": number(r"\+CSQ:\s*\d+\s*,\s*(\d+)", "AT+CSQ"),
                    "sms_service_center": match(r'\+CSCA:\s*"([^"]+)"', "AT+CSCA?"),
                    "sms_storage": {
                        "name": match(r'\+CPMS:\s*"([^"]+)"', "AT+CPMS?"),
                        "used": number(r'\+CPMS:\s*"[^"]+"\s*,\s*(\d+)', "AT+CPMS?"),
                        "capacity": number(r'\+CPMS:\s*"[^"]+"\s*,\s*\d+\s*,\s*(\d+)', "AT+CPMS?"),
                    },
                    "manufacturer": line("AT+CGMI"),
                    "model": line("AT+CGMM"),
                    "firmware": line("AT+CGMR"),
                    "power": {
                        "charge_status": number(r"\+CBC:\s*(\d+)", "AT+CBC"),
                        "battery_percent": number(r"\+CBC:\s*\d+\s*,\s*(\d+)", "AT+CBC"),
                        "voltage_mv": number(r"\+CBC:\s*\d+\s*,\s*\d+\s*,\s*(\d+)", "AT+CBC"),
                    },
                }
                if include_identifiers:
                    status["identifiers"] = {
                        "imei": match(r"(?:^|\r\n)(\d{15})\r\n", "AT+CGSN"),
                        "sim_serial": match(r"(?:\+CCID:\s*)?(\d{18,22})", "AT+CCID"),
                        "imsi": match(r"(?:^|\r\n)(\d{5,15})\r\n", "AT+CIMI"),
                    }
                return status
        except (ModemError, serial.SerialException, OSError, TimeoutError) as exc:
            return {"state": "unavailable", "detail": str(exc)}
        finally:
            self.lock.release()

    def send(self, pdu, length):
        with self.lock:
            return self._send(pdu, length)

    def cleanup_incoming(self, archive=None):
        """Archive each received PDU before deleting its SIM slot."""
        with self.lock:
            with serial.Serial(self.port, self.baudrate, timeout=0.2, write_timeout=5) as device:
                device.reset_input_buffer()
                for command in ("AT", "ATE0", "AT+CMGF=0"):
                    device.write((command + "\r").encode("ascii"))
                    self._read_until(device, "\r\nOK\r\n", 5)
                device.write(b"AT+CMGL=4\r")
                response = self._read_until(device, "\r\nOK\r\n", 15)
                pattern = r'\+CMGL:\s*(\d+)\s*,\s*([01])\s*,[^\r\n]*\r\n([0-9A-Fa-f]+)\r\n'
                for match in re.finditer(pattern, response):
                    index, pdu = int(match.group(1)), match.group(3).upper()
                    if archive is not None:
                        archive(index, pdu)
                    device.write(f"AT+CMGD={index}\r".encode("ascii"))
                    self._read_until(device, "\r\nOK\r\n", 5)

    def _send(self, pdu, length):
        submitted = False
        stage = "opening serial port"
        try:
            with serial.Serial(self.port, self.baudrate, timeout=0.2, write_timeout=5) as device:
                device.reset_input_buffer()
                for command in ("AT", "ATE0", "AT+CMEE=2"):
                    stage = command
                    device.write((command + "\r").encode("ascii"))
                    self._read_until(device, "\r\nOK\r\n", 5)
                stage = "waiting for SIM and network"
                self._wait_ready(device)
                stage = "setting PDU mode"
                device.write(b"AT+CMGF=0\r")
                self._read_until(device, "\r\nOK\r\n", 5)
                stage = "requesting SMS submission"
                device.write(f"AT+CMGS={length}\r".encode("ascii"))
                self._read_until(device, "> ", 10)
                # Mark uncertain before writing: a partial write can still submit the SMS.
                submitted = True
                stage = "submitting SMS"
                device.write(pdu.encode("ascii") + b"\x1a")
                response = self._read_until(device, "\r\nOK\r\n", 60)
                match = re.search(r"\+CMGS:\s*(\d+)", response)
                if not match:
                    raise ModemUncertainError("Modem replied OK without a message reference")
                return int(match.group(1))
        except ModemError as exc:
            raise ModemError(f"{stage}: {exc}") from exc
        except ModemUncertainError:
            raise
        except (serial.SerialException, OSError, TimeoutError) as exc:
            if submitted:
                raise ModemUncertainError(f"{stage}: {exc}") from exc
            raise ModemError(f"{stage}: {exc}") from exc
