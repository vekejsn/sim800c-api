import unittest

from app.pdu import encode


class PduTests(unittest.TestCase):
    def test_known_gsm_encoding(self):
        part = encode("+1234567890", "hello")[0]
        self.assertEqual(part["pdu"], "0001000A912143658709000005E8329BFD06")
        self.assertEqual(part["length"], len(bytes.fromhex(part["pdu"])) - 1)

    def test_national_number(self):
        pdu = bytes.fromhex(encode("069123456", "hello")[0]["pdu"])
        self.assertEqual(pdu[3:6], bytes([9, 0x81, 0x60]))

    def test_gsm_multipart_header_and_limits(self):
        parts = encode("+1234567890", "A" * 161)
        self.assertEqual(len(parts), 2)
        first, second = [bytes.fromhex(part["pdu"]) for part in parts]
        self.assertEqual(first[1], 0x41)
        self.assertEqual(first[12], 160)  # seven header septets plus 153 text septets
        self.assertEqual(first[13:16], b"\x05\x00\x03")
        self.assertEqual(first[16], second[16])
        self.assertEqual(first[17:19], b"\x02\x01")
        self.assertEqual(second[17:19], b"\x02\x02")
        self.assertLessEqual(len(first) - 13, 140)

    def test_extension_character_not_split(self):
        parts = encode("+1234567890", "A" * 152 + "^" + "A" * 7)
        self.assertEqual(len(parts), 2)
        self.assertEqual(bytes.fromhex(parts[0]["pdu"])[12], 159)

    def test_unicode_and_emoji_split(self):
        parts = encode("+1234567890", "🙂" * 36)
        self.assertEqual(len(parts), 2)
        first = bytes.fromhex(parts[0]["pdu"])
        self.assertEqual(first[11], 8)
        self.assertEqual(first[12], 138)  # UDH plus 33 complete emoji
        self.assertEqual(len(first[19:]), 132)


if __name__ == "__main__":
    unittest.main()
