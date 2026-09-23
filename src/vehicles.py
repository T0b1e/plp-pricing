"""Map the free-text vehicle column (AG) to a short class label."""
import re

EXCLUDE = {"S3_DUMMY_HL_TN", ""}   # placeholder code / blank, not a truck


def vehicle_class(raw: str) -> str | None:
    raw = (raw or "").strip()
    if raw in EXCLUDE:
        return None
    m = re.search(r"(\d+)\s*ล้อ", raw)
    if not m:
        return None
    wheels = int(m.group(1))
    if "หัวลาก" in raw:
        kind = "head"
    elif "เย็น" in raw:
        kind = "reefer"
    else:
        kind = "dry"
    extra = ""
    if "NGV" in raw.upper():
        extra = " NGV"
    elif "พ่วง" in raw:
        extra = " trailer"
    return f"{wheels}W {kind}{extra}"


def wheels(label: str) -> int:
    return int(label.split("W")[0])
