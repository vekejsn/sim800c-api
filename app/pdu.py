"""Encode outbound SMS-SUBMIT PDUs using GSM 03.38 or UTF-16BE."""
import secrets

GSM = "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
EXT = {"\f": 10, "^": 20, "{": 40, "}": 41, "\\": 47, "[": 60, "~": 61, "]": 62, "|": 64, "€": 101}


def _gsm_units(text):
    units = []
    for char in text:
        if char in EXT:
            units.append([27, EXT[char]])
        elif char in GSM and char != "\x1b":
            units.append([GSM.index(char)])
        else:
            return None
    return units


def _chunks(units, limit):
    chunks, current, size = [], [], 0
    for unit in units:
        if size + len(unit) > limit:
            chunks.append(current)
            current, size = [], 0
        current.extend(unit)
        size += len(unit)
    if current:
        chunks.append(current)
    return chunks


def _pack_septets(units, header=b""):
    # A UDH consumes a whole number of septets, with fill bits after it.
    offset = ((len(header) * 8 + 6) // 7) * 7
    bits = int.from_bytes(header, "little")
    for i, value in enumerate(units):
        bits |= value << (offset + 7 * i)
    length = (offset + len(units) * 7 + 7) // 8
    return bits.to_bytes(length, "little")


def encode(recipient, text):
    gsm = _gsm_units(text)
    if gsm is not None:
        units = gsm
        single, multi, dcs = 160, 153, 0
    else:
        # Keep surrogate pairs together when splitting.
        units = [list(char.encode("utf-16-be")) for char in text]
        single, multi, dcs = 140, 134, 8
    total_units = sum(len(unit) for unit in units)
    chunks = _chunks(units, single if total_units <= single else multi)
    if len(chunks) > 255:
        raise ValueError("Message exceeds 255 SMS parts")
    reference = secrets.randbelow(256)
    international = recipient.startswith("+")
    digits = recipient[1:] if international else recipient
    swapped = "".join((digits + ("F" if len(digits) % 2 else ""))[i:i+2][::-1]
                      for i in range(0, len(digits), 2))
    address = bytes([len(digits), 0x91 if international else 0x81]) + bytes.fromhex(swapped)
    result = []
    for index, chunk in enumerate(chunks, 1):
        header = bytes([5, 0, 3, reference, len(chunks), index]) if len(chunks) > 1 else b""
        if dcs == 0:
            data = _pack_septets(chunk, header)
            udl = len(chunk) + (7 if header else 0)
        else:
            data = header + bytes(chunk)
            udl = len(data)
        tpdu = bytes([0x41 if header else 0x01, 0]) + address + bytes([0, dcs, udl]) + data
        result.append({"pdu": (b"\x00" + tpdu).hex().upper(), "length": len(tpdu)})
    return result
