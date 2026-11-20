import unittest
from unittest.mock import patch

from app.modem import Modem, ModemError


class FakeSerial:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.buffer = b""
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.writes.append(data)
        self.buffer += next(self.responses)

    def read(self, size):
        result, self.buffer = self.buffer[:size], self.buffer[size:]
        return result


class ModemPinTests(unittest.TestCase):
    def test_unlocks_only_when_required(self):
        ok = b"\r\nOK\r\n"
        device = FakeSerial([ok, ok, ok, b"\r\n+CPIN: SIM PIN\r\n\r\nOK\r\n", ok,
                             b"\r\n+CPIN: READY\r\n\r\nOK\r\n", b"\r\n+CREG: 0,1\r\n\r\nOK\r\n",
                             ok, b"> ", b"\r\n+CMGS: 7\r\n\r\nOK\r\n"])
        with patch("app.modem.serial.Serial", return_value=device), patch("app.modem.time.sleep"):
            self.assertEqual(Modem("port", 115200, "1234").send("00", 1), 7)
        self.assertIn(b'AT+CPIN="1234"\r', device.writes)

    def test_rejected_pin_is_not_retried(self):
        ok = b"\r\nOK\r\n"
        responses = [ok, ok, ok, b"\r\n+CPIN: SIM PIN\r\n\r\nOK\r\n", b"\r\nERROR\r\n",
                     ok, ok, ok, b"\r\n+CPIN: SIM PIN\r\n\r\nOK\r\n"]
        device = FakeSerial(responses)
        modem = Modem("port", 115200, "1234")
        with patch("app.modem.serial.Serial", return_value=device):
            with self.assertRaises(ModemError):
                modem.send("00", 1)
            with self.assertRaises(ModemError):
                modem.send("00", 1)
        self.assertEqual(device.writes.count(b'AT+CPIN="1234"\r'), 1)


if __name__ == "__main__":
    unittest.main()
