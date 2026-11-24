"""Minimal FIT file decoder (plus a tiny encoder used by tests and demo mode).

Only what the dashboard needs: `record` (per-second samples), `lap` and
`session` messages from Garmin activity files. Pure Python, no dependencies.
Format reference: Garmin FIT SDK protocol (header, definition/data messages,
compressed-timestamp headers, developer fields).
"""

from __future__ import annotations

import io
import struct
import zipfile

FIT_EPOCH = 631065600  # 1989-12-31T00:00:00Z in Unix seconds

# base type number (low 5 bits) -> (struct code, size, invalid value)
_BASE = {
    0x00: ("B", 1, 0xFF), 0x01: ("b", 1, 0x7F), 0x02: ("B", 1, 0xFF),
    0x03: ("h", 2, 0x7FFF), 0x04: ("H", 2, 0xFFFF), 0x05: ("i", 4, 0x7FFFFFFF),
    0x06: ("I", 4, 0xFFFFFFFF), 0x07: ("s", 1, None), 0x08: ("f", 4, None),
    0x09: ("d", 8, None), 0x0A: ("B", 1, 0x00), 0x0B: ("H", 2, 0x0000),
    0x0C: ("I", 4, 0x00000000), 0x0D: ("B", 1, 0xFF), 0x0E: ("q", 8, 0x7FFFFFFFFFFFFFFF),
    0x0F: ("Q", 8, 0xFFFFFFFFFFFFFFFF), 0x10: ("Q", 8, 0),
}

MSG_SESSION, MSG_LAP, MSG_RECORD = 18, 19, 20

# field number -> (name, scale, offset) for the messages we keep
FIELDS = {
    MSG_RECORD: {253: ("timestamp", 1, 0), 2: ("altitude", 5, 500), 3: ("heart_rate", 1, 0), 4: ("cadence", 1, 0), 13: ("temperature", 1, 0),
                 5: ("distance", 100, 0), 6: ("speed", 1000, 0), 7: ("power", 1, 0), 53: ("fractional_cadence", 128, 0),
                 73: ("enhanced_speed", 1000, 0), 78: ("enhanced_altitude", 5, 500)},
    MSG_LAP: {253: ("timestamp", 1, 0), 2: ("start_time", 1, 0), 7: ("total_elapsed_time", 1000, 0),
              8: ("total_timer_time", 1000, 0), 9: ("total_distance", 100, 0), 13: ("avg_speed", 1000, 0),
              110: ("enhanced_avg_speed", 1000, 0), 15: ("avg_heart_rate", 1, 0), 16: ("max_heart_rate", 1, 0),
              21: ("total_ascent", 1, 0), 23: ("intensity", 1, 0), 24: ("lap_trigger", 1, 0), 25: ("sport", 1, 0)},
    MSG_SESSION: {253: ("timestamp", 1, 0), 2: ("start_time", 1, 0), 5: ("sport", 1, 0), 6: ("sub_sport", 1, 0),
                  7: ("total_elapsed_time", 1000, 0), 9: ("total_distance", 100, 0), 16: ("avg_heart_rate", 1, 0)},
}


class FitError(Exception):
    pass


def _crc16(data: bytes, crc: int = 0) -> int:
    table = (0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
             0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400)
    for byte in data:
        tmp = table[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ table[byte & 0xF]
        tmp = table[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ table[(byte >> 4) & 0xF]
    return crc


def unzip_fit(blob: bytes) -> bytes:
    """Garmin's download endpoint returns a ZIP holding the .fit file; accept raw FIT too."""
    if blob[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".fit")] or z.namelist()
            if not names:
                raise FitError("empty archive")
            return z.read(names[0])
    return blob


def decode(data: bytes) -> dict:
    """Return {"records": [...], "laps": [...], "sessions": [...]} with scaled values."""
    out = {"records": [], "laps": [], "sessions": []}
    pos = 0
    while pos + 12 <= len(data):  # files can be chained
        hsize = data[pos]
        if hsize not in (12, 14) or data[pos + 8:pos + 12] != b".FIT":
            if pos == 0:
                raise FitError("not a FIT file")
            break
        data_size = struct.unpack_from("<I", data, pos + 4)[0]
        start, end = pos + hsize, pos + hsize + data_size
        if end > len(data):
            end = len(data)
        _decode_body(data, start, end, out)
        pos = end + 2
    return out


def _decode_body(data: bytes, pos: int, end: int, out: dict) -> None:
    defs: dict[int, tuple] = {}
    last_ts = None
    keep = {MSG_RECORD: out["records"], MSG_LAP: out["laps"], MSG_SESSION: out["sessions"]}
    while pos < end:
        hdr = data[pos]
        pos += 1
        if hdr & 0x80:  # compressed timestamp data message
            local = (hdr >> 5) & 0x3
            offset = hdr & 0x1F
            if last_ts is not None:
                ts = (last_ts & ~0x1F) + offset
                if offset < (last_ts & 0x1F):
                    ts += 0x20
                last_ts = ts
            else:
                ts = None
            d = defs.get(local)
            if d is None:
                raise FitError("data before definition")
            msg, pos = _read_data(data, pos, d)
            if ts is not None and d[0] in keep:
                msg.setdefault("timestamp", ts)
            if d[0] in keep:
                keep[d[0]].append(_named(d[0], msg))
            continue
        local = hdr & 0x0F
        if hdr & 0x40:  # definition message
            arch = data[pos + 1]
            endian = ">" if arch == 1 else "<"
            gnum = struct.unpack_from(endian + "H", data, pos + 2)[0]
            nfields = data[pos + 4]
            pos += 5
            fields = []
            for _ in range(nfields):
                fnum, size, btype = data[pos], data[pos + 1], data[pos + 2]
                fields.append((fnum, size, btype & 0x1F))
                pos += 3
            dev_size = 0
            if hdr & 0x20:
                ndev = data[pos]
                pos += 1
                for _ in range(ndev):
                    dev_size += data[pos + 1]
                    pos += 3
            defs[local] = (gnum, endian, fields, dev_size)
        else:
            d = defs.get(local)
            if d is None:
                raise FitError("data before definition")
            msg, pos = _read_data(data, pos, d)
            if "timestamp" in msg and msg["timestamp"] is not None:
                last_ts = msg["timestamp"]
            if d[0] in keep:
                keep[d[0]].append(_named(d[0], msg))


def _read_data(data: bytes, pos: int, d: tuple):
    gnum, endian, fields, dev_size = d
    wanted = FIELDS.get(gnum, {})
    msg = {}
    for fnum, size, btype in fields:
        raw = data[pos:pos + size]
        pos += size
        if fnum not in wanted and fnum != 253:
            continue
        code, bsize, invalid = _BASE.get(btype, ("B", 1, None))
        if code == "s" or size < bsize:
            continue
        val = struct.unpack_from(endian + code, raw, 0)[0]  # first element if it's an array
        if invalid is not None and val == invalid:
            val = None
        msg[fnum if fnum != 253 else "timestamp"] = val
    return msg, pos + dev_size


def _named(gnum: int, msg: dict) -> dict:
    spec = FIELDS[gnum]
    out = {}
    for k, v in msg.items():
        if k == "timestamp":
            out["timestamp"] = v
            continue
        name, scale, offset = spec[k]
        out[name] = None if v is None else (v / scale - offset if (scale != 1 or offset) else v)
    for k in ("timestamp", "start_time"):
        if out.get(k) is not None:
            out[k] = out[k] + FIT_EPOCH
    return out


# ------------------------------------------------------------------ encoder (tests & demo)

def encode(records: list[dict], laps: list[dict], session: dict) -> bytes:
    """Write a small but valid FIT file (little-endian, normal headers)."""
    body = bytearray()

    def define(local, gnum, fields):
        body.append(0x40 | local)
        body.extend(struct.pack("<BBHB", 0, 0, gnum, len(fields)))
        for fnum, size, btype in fields:
            body.extend(bytes((fnum, size, btype)))

    def write(local, fields, values):
        body.append(local)
        for (fnum, size, btype), v in zip(fields, values):
            code, _, invalid = _BASE[btype & 0x1F]
            body.extend(struct.pack("<" + code, invalid if v is None else v))

    rec_f = [(253, 4, 0x86), (5, 4, 0x86), (73, 4, 0x86), (3, 1, 0x02), (4, 1, 0x02), (78, 4, 0x86), (13, 1, 0x01)]
    lap_f = [(253, 4, 0x86), (2, 4, 0x86), (7, 4, 0x86), (8, 4, 0x86), (9, 4, 0x86), (110, 4, 0x86), (15, 1, 0x02), (16, 1, 0x02), (23, 1, 0x00)]
    ses_f = [(253, 4, 0x86), (2, 4, 0x86), (5, 1, 0x00), (7, 4, 0x86), (9, 4, 0x86)]
    define(0, MSG_RECORD, rec_f)
    for r in records:
        write(0, rec_f, [int(r["t"] - FIT_EPOCH), round(r["dist"] * 100), round(r["speed"] * 1000),
                         r.get("hr"), r.get("cad"), None if r.get("alt") is None else round((r["alt"] + 500) * 5), r.get("temp")])
    define(1, MSG_LAP, lap_f)
    for lap in laps:
        write(1, lap_f, [int(lap["end"] - FIT_EPOCH), int(lap["start"] - FIT_EPOCH), round(lap["sec"] * 1000), round(lap["sec"] * 1000),
                         round(lap["dist"] * 100), round(lap["dist"] / max(lap["sec"], 1) * 1000), lap.get("hr"), lap.get("maxhr"), lap.get("intensity", 0)])
    define(2, MSG_SESSION, ses_f)
    write(2, ses_f, [int(session["end"] - FIT_EPOCH), int(session["start"] - FIT_EPOCH), 1, round(session["sec"] * 1000), round(session["dist"] * 100)])
    header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(body), b".FIT")
    header += struct.pack("<H", _crc16(header))
    blob = header + bytes(body)
    return blob + struct.pack("<H", _crc16(blob))


# ------------------------------------------------------------------ workout files (sideload to /GARMIN/NewFiles)

MSG_FILE_ID, MSG_WORKOUT, MSG_WORKOUT_STEP = 0, 26, 27
# FIT profile enums
DUR_TIME, DUR_OPEN, DUR_REPEAT = 0, 5, 6
TGT_HR, TGT_OPEN = 1, 2
INT_ACTIVE, INT_REST, INT_WARMUP, INT_COOLDOWN, INT_RECOVERY = 0, 1, 2, 3, 4


def encode_workout(name: str, steps: list[dict], created_unix: int) -> bytes:
    """Write a FIT workout file.

    steps: dicts with kind="time" (sec, intensity, optional hr=(low, high), label)
    or kind="repeat" (from_step, times). HR targets are custom bpm ranges.
    """
    body = bytearray()

    def define(local, gnum, fields):
        body.append(0x40 | local)
        body.extend(struct.pack("<BBHB", 0, 0, gnum, len(fields)))
        for fnum, size, btype in fields:
            body.extend(bytes((fnum, size, btype)))

    def write(local, fields, values):
        body.append(local)
        for (fnum, size, btype), v in zip(fields, values):
            if btype == 0x07:
                raw = (v or "").encode("utf-8")[: size - 1]
                body.extend(raw + b"\0" * (size - len(raw)))
                continue
            code, _, invalid = _BASE[btype & 0x1F]
            body.extend(struct.pack("<" + code, invalid if v is None else v))

    fid = [(0, 1, 0x00), (1, 2, 0x84), (2, 2, 0x84), (3, 4, 0x8C), (4, 4, 0x86)]
    define(0, MSG_FILE_ID, fid)
    write(0, fid, [5, 255, 0, (created_unix & 0xFFFFFFF) or 1, created_unix - FIT_EPOCH])  # type=workout, manufacturer=development

    name_len = 32
    wkt = [(4, 1, 0x00), (6, 2, 0x84), (8, name_len, 0x07)]
    define(1, MSG_WORKOUT, wkt)
    write(1, wkt, [1, len(steps), name])  # sport=running

    st = [(254, 2, 0x84), (0, 24, 0x07), (1, 1, 0x00), (2, 4, 0x86), (3, 1, 0x00), (4, 4, 0x86), (5, 4, 0x86), (6, 4, 0x86), (7, 1, 0x00)]
    define(2, MSG_WORKOUT_STEP, st)
    for i, s in enumerate(steps):
        if s["kind"] == "repeat":
            write(2, st, [i, "", DUR_REPEAT, s["from_step"], None, s["times"], None, None, None])
            continue
        hr = s.get("hr")
        if hr:  # custom HR: values above 100 are bpm + 100
            vals = [i, s.get("label", ""), DUR_TIME, int(s["sec"] * 1000), TGT_HR, 0, hr[0] + 100, hr[1] + 100, s["intensity"]]
        else:
            vals = [i, s.get("label", ""), DUR_TIME, int(s["sec"] * 1000), TGT_OPEN, None, None, None, s["intensity"]]
        write(2, st, vals)

    header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(body), b".FIT")
    header += struct.pack("<H", _crc16(header))
    blob = header + bytes(body)
    return blob + struct.pack("<H", _crc16(blob))
