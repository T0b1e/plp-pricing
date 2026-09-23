"""Step 2: split the single route cell (column AB) into an ordered list of stops.

    "TFG (ไทยฟู้ดส์ กาญ)+PACA (ALPHA กม.22) (Frozen)  อัตราน้ำมัน(29-29.99)"
      -> stops      = ["TFG (ไทยฟู้ดส์ กาญ)", "PACA (ALPHA กม.22)"]
         conditions = ["Frozen", "fuel:29-29.99"]

Parenthesised groups are either part of a place's name ("(ห้องเย็นแปซิฟิค)") or a
commercial condition ("(Frozen)", "(<30)", "(ลงของ)"). Conditions are removed from
the stop name and kept in their own column.
"""
import re
from collections import Counter

import pandas as pd

from .files import read_csv, write_csv
from .paths import LOCATIONS, TRIPS_PARSED, TRIPS_RAW

FUEL_PREFIX = re.compile(r"(?:อัตรา|เรท)?\s*น้ำมัน\s*\(([^()]*)\)")
# "(<30)", "(30.01-33)", "(28-30.99)", "(<35)", "(>40)"
FUEL_RANGE = re.compile(r"^\s*[<>]?=?\s*\d+(?:\.\d+)?\s*(?:-\s*\d+(?:\.\d+)?)?\s*$")
# Diesel has never remotely approached this in THB/litre; anything above it is a typo (e.g.
# "36.01-3900") or an unrelated number that happens to look like a range (a company's founding
# year "(1979)"), not a real fuel-rate band.
FUEL_RATE_MAX = 100.0
KM_BAND = re.compile(r"^\s*(?:\d+\s*KM\.?\s*-\s*\d+\s*KM\.?|[<>≤≥]=?\s*\d+\s*KM\.?)\s*$", re.I)
DATE_LIKE = re.compile(r"^\s*\d{1,2}\s*-\s*\d{1,2}\s*/\s*\d{2,4}\s*$|\d{1,2}/\d{1,2}/\d{2,4}|^\s*\d{1,2}/\d{2}\s*$")
TIME_LIKE = re.compile(r"\d{1,2}[.:]\d{2}\s*น")

# Paren contents that describe the job, not the place.
CONDITION_WORDS = [
    "frozen", "chill", "reefer", "pre cool", "precool", "dry",
    "ควบคุมอุณหภูมิ", "ทำอุณหภูมิ", "อุณหภูมิ",
    "ลงของ", "ลงสินค้า", "ขนถ่าย", "พนักงาน", "data logger", "ปลั๊กตู้", "ตะกร้า",
    "อ้างอิง", "ราคา", "เดือน", "รับของกลับ", "ยกเลิก", "ตีกลับ", "เด็กติดรถ",
    "ค้างคืน", "รถจัมโบ้", "จัมโบ้", "เที่ยว", "drop", "ค่า",
    "ไม่ควบคุม", "งดใช้", "ขึ้นของ", "บวกเพิ่ม", "ลด ", "กล่อง", "%", "น้ำมัน", "แฮนด์ลิฟท์",
    "รับสินค้า", "รอบ", "เรท", "ระยะทาง", "ลากสินค้า", "บาท",
    "องศา", "trailer", "holiday", "ครึ่งวัน", "เต็มวัน", "เอกสาร", "วิ่งงาน",
]
# Bare trailing words that are job conditions, e.g. "PACA (ALPHA Bangna KM. 22) Frozen".
TRAILING_CONDITION = re.compile(
    r"\s+(frozen|chill|dry|ตะกร้า|รับสินค้ากลับ|รับของกลับ|ลงของ|ขึ้นของ|อัตราราคา"
    r"|(?:ไม่)?(?:ควบคุม|ทำ)อุณหภูมิ|ตีกลับสินค้า\S*|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}\s*W|งดใช้)\s*$",
    re.I,
)
VEHICLE_HINT = re.compile(r"^\W*\S?\d+\s*(?:W|ล้อ)\s*$", re.I)   # "6W", "(ุ6W)" with a stray vowel

# Whole tokens (after splitting) that are fees / counts rather than places.
NON_PLACE = re.compile(
    r"^\s*(?:\d+|\d+\s*drops?|ค่า.*|.*\bdrops?\b.*|เพิ่มจุด.*|จุดส่ง.*|\d+\s*จุด.*|กระจาย.*|เรท.*|ใบค่า.*|เดือน\s*\d.*|จอด.*"
    r"|น้ำหนัก.*|.*\bdro?u?ps?\b.*|โยก.*|โยค.*)\s*$",
    re.I,
)


def classify_paren(content: str) -> str | None:
    """Return a condition label for a paren group, or None if it names the place."""
    c = content.strip()
    low = c.lower()
    if FUEL_RANGE.match(c):
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", c)]
        if not nums or max(nums) <= FUEL_RATE_MAX:
            return f"fuel:{c.replace(' ', '')}"
        # implausible for a diesel price (e.g. a year like "1979") - fall through, treat as part
        # of the place name rather than stripping it as a fuel condition
    if KM_BAND.match(c):
        return f"kmband:{c.replace(' ', '').upper()}"
    if DATE_LIKE.search(c):
        return f"date:{c}"
    if TIME_LIKE.search(c):
        return f"time:{c}"
    if VEHICLE_HINT.match(c):
        return f"vehicle:{c.upper()}"
    if any(w in low for w in CONDITION_WORDS):
        return c
    return None


def split_top_level(s: str, sep: str = "+") -> list[str]:
    """Split on `sep` only outside parentheses, so '(A+B)' stays one piece."""
    parts, buf, depth = [], [], 0
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(depth - 1, 0)
        if ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def normalise(s: str) -> str:
    s = s.replace("​", "").replace("\xa0", " ")
    s = re.sub(r"[‐‑–—]", "-", s)
    s = re.sub(r"\s*-{1,}\s*", " - ", s)          # "-", "--", " -- " -> " - "
    s = re.sub(r"\s*\(\s*", " (", s)
    s = re.sub(r"\s*\)\s*", ") ", s)
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" -,/")
    s = s.replace(" )", ")")
    return s


def strip_conditions(token: str) -> tuple[str, list[str]]:
    """Remove condition paren groups from a token. Handles one level of nesting."""
    conditions = []

    def repl(m):
        label = classify_paren(m.group(1))
        if label is None:
            return "\x00" + m.group(1) + "\x01"   # keep, but hide from the next pass
        conditions.append(label)
        return " "

    # innermost groups first, so nested "(A (Frozen))" works
    inner = re.compile(r"\(([^()]*)\)")
    while inner.search(token):
        token = inner.sub(repl, token)
    token = token.replace("\x00", "(").replace("\x01", ")")
    # an unclosed "(...": e.g. "DC Makro Mahachai(รวมพนักงานลงสินค้า" from bad source data
    m = re.search(r"\(([^()]*)$", token)
    if m and classify_paren(m.group(1)):
        conditions.append(classify_paren(m.group(1)))
        token = token[: m.start()]
    token = normalise(token)
    while (m := TRAILING_CONDITION.search(token)) and m.start() > 0:
        conditions.append(m.group(1))
        token = normalise(token[: m.start()])
    return token, conditions


def fuel_bracket(conditions: list[str], raw: str = "") -> tuple[str, float | None, float | None]:
    """Contract diesel-price band (THB/litre) the rate is pegged to, from 'fuel:28-30.99'.

    Returns (text, low, high). '<30' -> (None, 30); rows marked ไม่กำกับเรทน้ำมัน get
    text 'not fuel-indexed'. The first bracket wins when a route carries several.
    """
    for c in conditions:
        if not c.startswith("fuel:"):
            continue
        text = c[5:]
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]
        if nums and max(nums) > FUEL_RATE_MAX:
            continue   # typo in the source (e.g. "36.01-3900") - not a real diesel price, skip it
        if text.startswith("<") and nums:
            return text, None, nums[0]
        if text.startswith(">") and nums:
            return text, nums[0], None
        if len(nums) >= 2:
            return text, nums[0], nums[1]
        if nums:
            return text, nums[0], nums[0]
    if "ไม่กำกับเรทน้ำมัน" in (raw or ""):
        return "not fuel-indexed", None, None
    return "", None, None


def parse_route(raw: str) -> dict:
    if not raw or not raw.strip():
        return {"stops": [], "conditions": [], "non_place": []}
    s = raw
    conditions = []
    for m in FUEL_PREFIX.finditer(s):
        conditions.append(f"fuel:{m.group(1).replace(' ', '')}")
    s = FUEL_PREFIX.sub(" ", s)

    stops, non_place = [], []
    for part in split_top_level(s):
        name, conds = strip_conditions(part)
        conditions += conds
        if not name:
            continue
        if NON_PLACE.match(name):
            non_place.append(name)
            continue
        if stops and stops[-1] == name:       # "A+A" -> one stop
            continue
        stops.append(name)
    return {"stops": stops, "conditions": conditions, "non_place": non_place}


def main():
    df = pd.read_parquet(TRIPS_RAW)
    parsed = df["route_raw"].map(parse_route)
    df["stops"] = parsed.map(lambda p: p["stops"])
    df["conditions"] = parsed.map(lambda p: p["conditions"])
    df["non_place"] = parsed.map(lambda p: p["non_place"])
    df["stop_count"] = df["stops"].map(len)
    df["origin"] = df["stops"].map(lambda x: x[0] if x else None)
    df["destination"] = df["stops"].map(lambda x: x[-1] if x else None)
    df.to_parquet(TRIPS_PARSED, index=False)

    freq = Counter(t for stops in df["stops"] for t in stops)
    loc = pd.DataFrame(sorted(freq.items(), key=lambda kv: -kv[1]), columns=["token", "frequency"])
    if LOCATIONS.exists():
        # keep manual work (aliases, fixed coordinates) from a previous run
        old = read_csv(LOCATIONS, dtype=str).drop(columns=["frequency"], errors="ignore")
        loc = loc.merge(old, on="token", how="left")
    for col in ["alias_of", "search_query", "lat", "lon", "place_id", "formatted_address",
                "geocode_source", "geocode_confidence", "verified", "note"]:
        if col not in loc:
            loc[col] = ""
    write_csv(loc, LOCATIONS, index=False)

    print(f"[parse] {len(df):,} rows, {len(loc):,} distinct stop names -> {LOCATIONS.name}")
    print("stop_count distribution:")
    print(df["stop_count"].value_counts().sort_index().to_string())
    return df, loc


if __name__ == "__main__":
    main()
