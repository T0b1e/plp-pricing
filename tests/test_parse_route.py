import pytest

from src.parse_route import fuel_bracket, parse_route, split_top_level
from src.vehicles import vehicle_class


@pytest.mark.parametrize("raw, stops", [
    ("BF (อาหารเบทเทอร์ อ้อมน้อย)+PCS(ห้องเย็นแปซิฟิค)",
     ["BF (อาหารเบทเทอร์ อ้อมน้อย)", "PCS (ห้องเย็นแปซิฟิค)"]),
    # '+' inside parentheses is not a separator
    ("TUF+DC Makro Mahachai(รวมพนักงานลงสินค้า+data logger) อัตราน้ำมัน(28-30.99)",
     ["TUF", "DC Makro Mahachai"]),
    # fuel bracket, bare fuel range, Frozen modifier
    ("TFG (ไทยฟู้ดส์ กาญ)+PACA (ALPHA กม.22) (Frozen)  อัตราน้ำมัน(29-29.99)",
     ["TFG (ไทยฟู้ดส์ กาญ)", "PACA (ALPHA กม.22)"]),
    ("LUF (ลัคกี้ ยูเนี่ยน ฟู้ดส์)+(CP RAM)ซีพีแรม ลาดกระบัง(<35)",
     ["LUF (ลัคกี้ ยูเนี่ยน ฟู้ดส์)", "(CP RAM) ซีพีแรม ลาดกระบัง"]),
    # SO numbers, fees and drop counts are not places
    ("BF (อาหารเบทเทอร์ อ้อมน้อย)+5401566542", ["BF (อาหารเบทเทอร์ อ้อมน้อย)"]),
    ("UKF (สยาม ยูเคเอฟ)+ค่าขนถ่ายสินค้า", ["UKF (สยาม ยูเคเอฟ)"]),
    ("PACR รังสิต+7 Drops", ["PACR รังสิต"]),
    # identity parens with KM / กม. survive; condition parens and trailing words don't
    ("PACA (ALPHA Bangna KM. 22)+TUF โรงกุ้ง (รับสินค้า20.00น.)",
     ["PACA (ALPHA Bangna KM. 22)", "TUF โรงกุ้ง"]),
    ("X+บริษัท ซิลลิค ฟาร์มา (บางนา กม.23) ควบคุมอุณหภูมิ", ["X", "บริษัท ซิลลิค ฟาร์มา (บางนา กม.23)"]),
    ("A+DC Makro Wang Noi 08/06/26", ["A", "DC Makro Wang Noi"]),
    ("A+เซ็นทรัลเวิลด์ (<50KM.)", ["A", "เซ็นทรัลเวิลด์"]),
    ("A+คาร์กิลล์ สระบุรี 10W", ["A", "คาร์กิลล์ สระบุรี"]),
    # "-" and "--" spellings normalise to the same name
    ("A+มาร์ส เพ็ทแคร์ (ประเทศไทย) จำกัด -- ชลบุรี", ["A", "มาร์ส เพ็ทแคร์ (ประเทศไทย) จำกัด - ชลบุรี"]),
    # multi-stop keeps order
    ("PCS(ห้องเย็นแปซิฟิค)+MK (เอ็มเคห้องเย็น)+BF (อาหารเบทเทอร์ อ้อมน้อย)",
     ["PCS (ห้องเย็นแปซิฟิค)", "MK (เอ็มเคห้องเย็น)", "BF (อาหารเบทเทอร์ อ้อมน้อย)"]),
    ("", []),
])
def test_stops(raw, stops):
    assert parse_route(raw)["stops"] == stops


def test_conditions_are_kept():
    p = parse_route("TFG (ไทยฟู้ดส์ กาญ)+PACA (ALPHA กม.22) (Frozen)  อัตราน้ำมัน(29-29.99)")
    assert "Frozen" in p["conditions"]
    assert "fuel:29-29.99" in p["conditions"]


@pytest.mark.parametrize("raw, expected", [
    ("TUF+JWD ฉะเชิงเทรา Chill อัตราน้ำมัน(28-30.99)", ("28-30.99", 28.0, 30.99)),
    ("PCS(ห้องเย็นแปซิฟิค)+X (30.01-33)", ("30.01-33", 30.01, 33.0)),
    ("LUF+(CP RAM)ซีพีแรม ลาดกระบัง(<35)", ("<35", None, 35.0)),
    ("UKF (สยาม ยูเคเอฟ)+โชคสมุทร (ไม่กำกับเรทน้ำมัน)", ("not fuel-indexed", None, None)),
    ("BF (อาหารเบทเทอร์ อ้อมน้อย)+PCS(ห้องเย็นแปซิฟิค)", ("", None, None)),
    # typo in the source ("3900" instead of "39.00") - too high to be a real diesel price, skip it
    ("ไทยโยโกเรสำโรง+Ango (เทพารักษ์) อัตราน้ำมัน(36.01-3900)", ("", None, None)),
])
def test_fuel_bracket(raw, expected):
    assert fuel_bracket(parse_route(raw)["conditions"], raw) == expected


def test_year_in_company_name_is_not_a_fuel_rate():
    # "(1979)" is part of the company's name, not a fuel-rate paren
    p = parse_route("PACA (ALPHA Bangna KM. 22)+T.O.Chemicals (1979) Ltd. Pathumthani")
    assert not any(c.startswith("fuel:") for c in p["conditions"])
    assert "T.O.Chemicals (1979) Ltd. Pathumthani" in p["stops"]


def test_split_ignores_plus_in_parens():
    assert split_top_level("A(x+y)+B") == ["A(x+y)", "B"]


@pytest.mark.parametrize("raw, label", [
    ("รถบรรทุก 10 ล้อเย็น (น้ำมัน)", "10W reefer"),
    ("รถบรรทุก 4 ล้อ(น้ำมัน)", "4W dry"),
    ("รถ 4 ล้อ(แห้ง)", "4W dry"),
    ("หัวลาก 22 ล้อ", "22W head"),
    ("รถบรรทุก 10 ล้อ(ก๊าช NGV)", "10W dry NGV"),
    ("S3_DUMMY_HL_TN", None),
])
def test_vehicle_class(raw, label):
    assert vehicle_class(raw) == label
