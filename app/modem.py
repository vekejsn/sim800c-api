import re
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

    def send(self, pdu, length):
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
