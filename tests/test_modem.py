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
    def test_cleanup_archives_before_deleting_received_only(self):
        ok = b"\r\nOK\r\n"
        listing = (b"\r\n+CMGL: 1,1,,3\r\n001122\r\n"
                   b"+CMGL: 2,2,,3\r\n334455\r\n\r\nOK\r\n")
        device = FakeSerial([ok, ok, ok, listing, ok])
        saved = []
        with patch("app.modem.serial.Serial", return_value=device):
            Modem("port", 115200).cleanup_incoming(lambda index, pdu: saved.append((index, pdu)))
        self.assertEqual(saved, [(1, "001122")])
        self.assertIn(b"AT+CMGD=1\r", device.writes)
        self.assertNotIn(b"AT+CMGD=2\r", device.writes)

    def test_cleanup_does_not_delete_if_archive_fails(self):
        ok = b"\r\nOK\r\n"
        device = FakeSerial([ok, ok, ok, b"\r\n+CMGL: 1,0,,3\r\n001122\r\n\r\nOK\r\n"])
        def fail(index, pdu):
            raise OSError("database unavailable")
        with patch("app.modem.serial.Serial", return_value=device):
            with self.assertRaises(OSError):
                Modem("port", 115200).cleanup_incoming(fail)
        self.assertNotIn(b"AT+CMGD=1\r", device.writes)

    def test_status(self):
        ok = b"\r\nOK\r\n"
        device = FakeSerial([ok, ok, b"\r\n+CPIN: READY\r\n\r\nOK\r\n",
                             b"\r\n+CREG: 0,1\r\n\r\nOK\r\n",
                             b"\r\n+CGREG: 0,5\r\n\r\nOK\r\n",
                             b'\r\n+COPS: 0,0,"VEGA"\r\n\r\nOK\r\n',
                             b"\r\n+CSQ: 29,0\r\n\r\nOK\r\n",
                             b'\r\n+CSCA: "+123",145\r\n\r\nOK\r\n',
                             b'\r\n+CPMS: "SM",7,20,"SM",7,20,"SM",7,20\r\n\r\nOK\r\n',
                             b"\r\nSIMCOM\r\n\r\nOK\r\n",
                             b"\r\nSIM800\r\n\r\nOK\r\n",
                             b"\r\nREV1\r\n\r\nOK\r\n",
                             b"\r\n+CBC: 0,85,4100\r\n\r\nOK\r\n"])
        with patch("app.modem.serial.Serial", return_value=device):
            status = Modem("port", 115200).status()
        self.assertEqual(status["operator"], "VEGA")
        self.assertEqual(status["network_registration"], 1)
        self.assertEqual(status["packet_registration"], 5)
        self.assertEqual(status["signal_dbm"], -55)
        self.assertEqual(status["sms_storage"]["used"], 7)
        self.assertEqual(status["sms_service_center"], "+123")
        self.assertEqual(status["manufacturer"], "SIMCOM")
        self.assertEqual(status["power"]["voltage_mv"], 4100)
        self.assertNotIn("identifiers", status)

    def test_status_busy(self):
        modem = Modem("port", 115200)
        with modem.lock:
            self.assertEqual(modem.status()["state"], "busy")

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
