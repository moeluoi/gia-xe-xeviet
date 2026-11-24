import os
import re
import sys
import json
import time
import smtplib
import argparse
import tempfile
from difflib import SequenceMatcher
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional

import requests
import yt_dlp
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Ensure UTF-8 console output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CAR_PRICES_JSON = DATA_DIR / "car_prices.json"
CAR_PRICES_JS = DATA_DIR / "data.js"
PROCESSED_VIDEOS_JSON = DATA_DIR / "processed_videos.json"

load_dotenv(ROOT_DIR / ".env")

CHANNEL_URL = os.getenv("YOUTUBE_CHANNEL_URL", "https://www.youtube.com/@xeviet.official/videos")
VN_TZ = timezone(timedelta(hours=7))

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}


# =====================================================================
# Pydantic Schema for Gemini Structured Output
# =====================================================================
class ExtractedCarItem(BaseModel):
    brand: str = Field(description="Tên hãng xe chuẩn hóa (VD: Mazda, Toyota, Hyundai, Ford, Honda, Kia, Mitsubishi, Suzuki, VinFast, Nissan, MG, Geely, Wuling, Peugeot, Subaru, Isuzu, Volkswagen...)")
    model: str = Field(description="Tên dòng xe ngắn gọn chuẩn hóa, không lặp lại tên hãng (VD: CX-5, Tucson, Creta, Territory, Coolray, Bingo, Vios, Accent, Xpander, CR-V, VF 6, City, Ranger, Seltos, Carnival...)")
    trim: str = Field(description="Tên phiên bản ngắn gọn, KHÔNG ghi chữ 'Bản ' ở đầu, KHÔNG ghi lặp dung tích động cơ nếu tên phiên bản đã đủ phân biệt (VD: Premium, Flagship, Executive, Titanium, Titanium X, Trend, Eco, Plus, Đặc biệt, Cao cấp, Hybrid, 1.6 Turbo, 2.0 Xăng Tiêu chuẩn...). Nếu không nói rõ phiên bản thì ghi 'Tiêu chuẩn'")
    year: int = Field(description="Năm sản xuất / VIN (VD: 2024, 2025, 2026). Nếu không nêu rõ, lấy năm hiện tại của video.")
    segment: str = Field(description="Phân khúc xe chuẩn hóa. CHỈ dùng các tên chuẩn sau: 'Sedan hạng A', 'Sedan hạng B', 'Sedan hạng C', 'Sedan hạng D', 'CUV/SUV cỡ A', 'CUV/SUV cỡ B', 'CUV/SUV cỡ C', 'SUV cỡ D', 'MPV', 'Bán tải'. TUYỆT ĐỐI KHÔNG ghi thêm '(Điện)', 'B+', hay '7 chỗ' (VD: CUV cỡ A điện vẫn ghi 'CUV/SUV cỡ A', CUV cỡ B+ vẫn ghi 'CUV/SUV cỡ B', MPV 7 chỗ vẫn ghi 'MPV').")
    listed_price: float = Field(description="Giá niêm yết tính bằng đơn vị TRIỆU VNĐ (VD: 749, 599, 1029). Nếu không nêu rõ, tính từ Giá sau giảm + Số tiền ưu đãi.")
    discount_amount: float = Field(description="Số tiền ưu đãi / giảm giá tiền mặt hoặc hỗ trợ lệ phí trước bạ quy đổi ra tiền mặt, tính bằng đơn vị TRIỆU VNĐ (VD: 40, 55, 80, 0). TUYỆT ĐỐI chỉ lấy con số tiền giảm, KHÔNG ghi chú quà tặng phụ kiện.")
    net_price: float = Field(description="Giá sau giảm (Giá thực tế = Giá niêm yết - Số tiền ưu đãi), tính bằng đơn vị TRIỆU VNĐ (VD: 709, 544).")
    otr_tinh: float = Field(description="Giá lăn bánh tại Tỉnh tính bằng đơn vị TRIỆU VNĐ. Nếu video không đọc số chính xác cho Tỉnh, hãy ước tính chuẩn theo công thức lăn bánh Tỉnh tại Việt Nam (Giá tính thuế trước bạ 10% + đăng ký 2 triệu).")
    otr_hcm: float = Field(description="Giá lăn bánh tại TP.HCM tính bằng đơn vị TRIỆU VNĐ. Nếu video không đọc số chính xác cho TP.HCM, hãy ước tính chuẩn theo công thức lăn bánh TP.HCM (Trước bạ 10% + biển số 20 triệu + phí đăng kiểm/đường bộ ~3 triệu).")
    otr_hn: float = Field(description="Giá lăn bánh tại Hà Nội tính bằng đơn vị TRIỆU VNĐ. Nếu video không đọc số chính xác cho Hà Nội, hãy ước tính chuẩn theo công thức lăn bánh Hà Nội (Trước bạ 12% + biển số 20 triệu + phí đăng kiểm/đường bộ ~3 triệu).")
    timestamp_seconds: int = Field(description="Số giây (tính từ 0) trong video nơi bắt đầu hiển thị bảng giá hoặc nhắc đến chiếc xe này (VD: 125 cho phút 02:05).")
    timestamp_label: str = Field(description="Thời điểm xuất hiện trong video định dạng MM:SS (VD: '02:05', '11:30').")


class VideoExtractionResult(BaseModel):
    has_car_pricing: bool = Field(description="True nếu video có báo giá xe hoặc thông tin khuyến mãi/ưu đãi giá xe. False nếu là video thuần tin tức/chính sách/giao thông không có báo giá xe cụ thể.")
    video_summary: str = Field(description="Tóm tắt ngắn gọn 1-2 câu về nội dung báo giá của video này.")
    cars: List[ExtractedCarItem] = Field(description="Danh sách toàn bộ các mẫu xe/phiên bản xe được báo giá trong video.")


# =====================================================================
# Helper Functions
# =====================================================================
def strip_accents(text: str) -> str:
    text = str(text or "").lower().strip()
    text = re.sub(r"[àáạảãâầấậẩẫăằắặẳẵ]", "a", text)
    text = re.sub(r"[èéẹẻẽêềếệểễ]", "e", text)
    text = re.sub(r"[ìíịỉĩ]", "i", text)
    text = re.sub(r"[òóọỏõôồốộổỗơờớợởỡ]", "o", text)
    text = re.sub(r"[ùúụủũưừứựửữ]", "u", text)
    text = re.sub(r"[ỳýỵỷỹ]", "y", text)
    text = re.sub(r"đ", "d", text)
    return text


def slugify(text: str) -> str:
    text = strip_accents(text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def make_car_id(brand: str, model: str, trim: str, year: Optional[int] = None) -> str:
    return slugify(f"{brand}-{model}-{trim}")


_BRAND_CANONICAL = {
    "vinfast": "VinFast",
    "mg": "MG",
    "byd": "BYD",
    "bmw": "BMW",
    "mini": "MINI",
    "gwm": "GWM",
    "kia": "Kia",
    "geely": "Geely",
    "hyundai": "Hyundai",
    "toyota": "Toyota",
    "ford": "Ford",
    "mazda": "Mazda",
    "honda": "Honda",
    "mitsubishi": "Mitsubishi",
    "suzuki": "Suzuki",
    "nissan": "Nissan",
    "wuling": "Wuling",
    "peugeot": "Peugeot",
    "subaru": "Subaru",
    "isuzu": "Isuzu",
    "volkswagen": "Volkswagen",
    "skoda": "Skoda",
    "omoda": "Omoda",
    "jaecoo": "Jaecoo",
    "lynk & co": "Lynk & Co",
}

_DISTINCT_TIERS = [
    "titanium x", "n line", "gt line", "tieu chuan", "dac biet", "cao cap",
    "standard", "base", "eco", "plus", "pro", "max", "trend", "titanium",
    "deluxe", "luxury", "premium", "flagship", "executive", "signature",
    "athlete", "active", "sport", "glx", "gls", "rs",
]


def clean_car_fields(brand: str, model: str, trim: str) -> tuple:
    """Normalize brand, model, and trim into clean, concise display strings."""
    b = re.sub(r"\s+", " ", str(brand or "").strip())
    b_key = strip_accents(b)
    if b_key in _BRAND_CANONICAL:
        b = _BRAND_CANONICAL[b_key]

    m = re.sub(r"\s+", " ", str(model or "").strip())
    if b and m.lower().startswith(b.lower() + " "):
        m = m[len(b):].strip()

    t = re.sub(r"\s+", " ", str(trim or "").strip())
    for prefix in (f"{b} {m}", m, b):
        if prefix and t.lower().startswith(prefix.lower() + " "):
            t = t[len(prefix):].strip()

    # Strip leading Vietnamese filler words like "Bản " or "Phiên bản "
    t = re.sub(r"^(?:phiên\s+bản|bản)\s+", "", t, flags=re.IGNORECASE).strip()
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    if not t:
        t = "Tiêu chuẩn"

    return b, m, t


def canonical_model_key(model: str) -> str:
    """Collapse spaces/hyphens in model names so 'VF 6' == 'VF6' and 'CX-5' == 'CX5'."""
    return re.sub(r"[^a-z0-9]+", "", strip_accents(model))


def canonical_trim_core(trim: str) -> str:
    """Extract core trim tokens by stripping filler words and engine displacement prefixes (e.g. '1.5T Premium' -> 'premium')."""
    s = strip_accents(trim)
    s = re.sub(r"\b(?:phien\s+ban|ban|dong\s+co|may)\b", " ", s)
    # Remove engine displacement tokens like 1.5, 1.5t, 1.5l, 2.0, 2.0l
    s_no_disp = re.sub(r"\b[1-5]\.\d\s*[tl]?\b", " ", s)
    s_no_disp = re.sub(r"[^a-z0-9+]+", " ", s_no_disp).strip()
    if s_no_disp:
        return s_no_disp
    return re.sub(r"[^a-z0-9+]+", " ", s).strip()


def _extract_token_set(text: str, patterns: dict) -> set:
    found = set()
    for label, pat in patterns.items():
        if re.search(pat, text):
            found.add(label)
    return found


def has_conflicting_specs(trim_a: str, trim_b: str) -> bool:
    """Guardrail: return True if two trims explicitly specify mutually exclusive specs."""
    a = re.sub(r"[^a-z0-9+]+", " ", strip_accents(trim_a)).strip()
    b = re.sub(r"[^a-z0-9+]+", " ", strip_accents(trim_b)).strip()

    spec_groups = [
        {
            "2wd": r"\b(?:1\s*cau|4x2|2wd|fwd|rwd)\b",
            "4wd": r"\b(?:2\s*cau|4x4|4wd|awd)\b",
        },
        {
            "xang": r"\b(?:xang|gasoline|petrol)\b",
            "dau": r"\b(?:dau|diesel)\b",
            "hybrid": r"\b(?:hybrid|hev|phev|ehev)\b",
            "dien": r"\b(?:dien|ev|electric)\b",
        },
        {
            "mt": r"\b(?:mt|so\s*san)\b",
            "at": r"\b(?:at|cvt|dct|so\s*tu\s*dong)\b",
        },
    ]
    for group in spec_groups:
        sa = _extract_token_set(a, group)
        sb = _extract_token_set(b, group)
        if sa and sb and sa != sb:
            return True

    # Check EV range (e.g. 333km vs 410km) and seating (e.g. 5 cho vs 7 cho)
    for pat in (r"\b(\d{3})\s*km\b", r"\b(\d)\s*cho\b"):
        ma = set(re.findall(pat, a))
        mb = set(re.findall(pat, b))
        if ma and mb and ma != mb:
            return True

    # Check distinct trim tiers (match longer phrases first so 'titanium x' doesn't also match 'titanium')
    def _tiers(s: str) -> set:
        rem = s
        found = set()
        for tier in _DISTINCT_TIERS:
            pat = r"\b" + re.escape(tier).replace(r"\ ", r"\s+") + r"\b"
            if re.search(pat, rem):
                found.add(tier)
                rem = re.sub(pat, " ", rem)
        return found

    ta, tb = _tiers(a), _tiers(b)
    if ta and tb and ta != tb:
        return True

    return False


def are_same_car_variant(car_a: dict, car_b: dict) -> bool:
    """
    Determine whether two car entries refer to the same vehicle variant:
    1. Same brand and same canonical model (merging across VIN years).
    2. Same listed_price (> 0).
    3. No conflicting drivetrain/fuel/transmission/tier specs.
    4. Full name similarity >= 90% OR core trim similarity >= 90%.
    """
    if strip_accents(car_a.get("brand", "")) != strip_accents(car_b.get("brand", "")):
        return False
    if canonical_model_key(car_a.get("model", "")) != canonical_model_key(car_b.get("model", "")):
        return False

    lp_a = round(float(car_a.get("listed_price") or 0), 1)
    lp_b = round(float(car_b.get("listed_price") or 0), 1)
    if lp_a <= 0 or lp_b <= 0 or abs(lp_a - lp_b) >= 0.01:
        return False

    trim_a = str(car_a.get("trim") or "")
    trim_b = str(car_b.get("trim") or "")
    if has_conflicting_specs(trim_a, trim_b):
        return False

    full_a = re.sub(r"\s+", " ", strip_accents(f"{car_a.get('brand', '')} {car_a.get('model', '')} {trim_a}")).strip()
    full_b = re.sub(r"\s+", " ", strip_accents(f"{car_b.get('brand', '')} {car_b.get('model', '')} {trim_b}")).strip()
    if SequenceMatcher(None, full_a, full_b).ratio() >= 0.90:
        return True

    core_a = canonical_trim_core(trim_a)
    core_b = canonical_trim_core(trim_b)
    if core_a and core_b and SequenceMatcher(None, core_a, core_b).ratio() >= 0.90:
        return True

    return False


def choose_preferred_trim(trim_a: str, trim_b: str) -> str:
    """Keep the shorter, cleaner display trim name (user preference)."""
    a = re.sub(r"\s+", " ", str(trim_a or "").strip()) or "Tiêu chuẩn"
    b = re.sub(r"\s+", " ", str(trim_b or "").strip()) or "Tiêu chuẩn"
    if len(a) != len(b):
        return a if len(a) < len(b) else b
    # Tie-breaker: fewer digits/symbols, then keep first
    score_a = len(re.findall(r"[^a-zA-ZÀ-ỹ\s]", a))
    score_b = len(re.findall(r"[^a-zA-ZÀ-ỹ\s]", b))
    return a if score_a <= score_b else b


def recompute_car_stats(car_entry: dict) -> dict:
    """Deduplicate history by video_id, sort chronologically, and refresh top-level latest fields."""
    by_vid = {}
    for h in car_entry.get("history", []):
        vid = h.get("video_id", "")
        if vid not in by_vid or (h.get("date", ""), h.get("timestamp_seconds", 0)) >= (
            by_vid[vid].get("date", ""),
            by_vid[vid].get("timestamp_seconds", 0),
        ):
            by_vid[vid] = h

    hist = sorted(by_vid.values(), key=lambda h: (h.get("date", ""), h.get("video_id", "")))
    car_entry["history"] = hist
    if not hist:
        return car_entry

    latest = hist[-1]
    prev = hist[-2] if len(hist) >= 2 else None
    net_change = round(latest["net_price"] - prev["net_price"], 1) if prev else 0.0
    discount_change = round(latest["discount_amount"] - prev["discount_amount"], 1) if prev else 0.0

    car_entry.update({
        "latest_date": latest["date"],
        "listed_price": latest["listed_price"],
        "discount_amount": latest["discount_amount"],
        "discount_pct": latest["discount_pct"],
        "net_price": latest["net_price"],
        "otr_tinh": latest["otr_tinh"],
        "otr_hcm": latest["otr_hcm"],
        "otr_hn": latest["otr_hn"],
        "net_price_change": net_change,
        "discount_change": discount_change,
        "previous_net_price": prev["net_price"] if prev else None,
        "video_id": latest["video_id"],
        "video_title": latest["video_title"],
        "video_url": latest["video_url"],
        "timestamp_seconds": latest["timestamp_seconds"],
        "timestamp_label": latest["timestamp_label"],
    })
    return car_entry


def deduplicate_cars_in_db(db: dict) -> bool:
    """Clean car display names and merge duplicate variants across the entire database."""
    cars = db.get("cars", [])
    if not cars:
        return False

    changed = False
    merged_list = []

    for c in cars:
        old_brand, old_model, old_trim = c.get("brand", ""), c.get("model", ""), c.get("trim", "")
        brand, model, trim = clean_car_fields(old_brand, old_model, old_trim)
        year = int(c.get("year") or datetime.now(VN_TZ).year)
        new_id = make_car_id(brand, model, trim)
        new_seg = normalize_segment(c.get("segment", "Khác"))

        if (brand, model, trim, new_id, new_seg) != (old_brand, old_model, old_trim, c.get("car_id"), c.get("segment")):
            c["brand"] = brand
            c["model"] = model
            c["trim"] = trim
            c["year"] = year
            c["car_id"] = new_id
            c["segment"] = new_seg
            changed = True

        # Try to merge into an already-accepted car entry
        matched_target = None
        for target in merged_list:
            if target["car_id"] == c["car_id"] or are_same_car_variant(target, c):
                matched_target = target
                break

        if matched_target is not None:
            old_target_year = int(matched_target.get("year") or 0)
            merged_year = max(old_target_year, year)
            preferred_trim = choose_preferred_trim(matched_target["trim"], c["trim"])
            if preferred_trim != matched_target["trim"] or c["trim"] != matched_target["trim"] or old_target_year != year:
                vin_info = f"VIN {old_target_year}+{year}->{merged_year}" if old_target_year != year else f"VIN {merged_year}"
                print(
                    f"🔗 Đã gom phiên bản trùng: {brand} {model} '{matched_target['trim']}' + '{c['trim']}' "
                    f"-> '{preferred_trim}' ({vin_info}, Niêm yết {c.get('listed_price'):g}tr)"
                )
            matched_target["trim"] = preferred_trim
            matched_target["year"] = merged_year
            matched_target["car_id"] = make_car_id(matched_target["brand"], matched_target["model"], preferred_trim)
            matched_target["history"] = matched_target.get("history", []) + c.get("history", [])
            recompute_car_stats(matched_target)
            changed = True
        else:
            merged_list.append(c)

    if changed:
        db["cars"] = sorted(
            merged_list,
            key=lambda item: (item.get("latest_date", ""), item.get("discount_amount", 0)),
            reverse=True,
        )
    return changed


def format_timestamp(seconds: int) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def normalize_segment(seg: str) -> str:
    """Standardize car segments (merge CUV/SUV cỡ A (Điện) -> CUV/SUV cỡ A, B+ -> B, MPV 7 chỗ -> MPV)."""
    if not seg:
        return "Khác"
    s = str(seg).strip()
    # Remove parenthetical suffixes like (Điện), (Hybrid), (Xăng), (7 chỗ)
    s = re.sub(r"\s*\([^)]*\)", "", s).strip()
    # Remove '+' or '-' suffixes like CUV/SUV cỡ B+ -> CUV/SUV cỡ B
    s = re.sub(r"([ABCDE])[+-](?=\s|$)", r"\1", s, flags=re.IGNORECASE).strip()
    upper = s.upper()
    if upper.startswith("MPV"):
        return "MPV"
    if "BÁN TẢI" in upper or "PICKUP" in upper:
        return "Bán tải"
    m_cuv = re.search(r"(?:CUV|SUV)(?:/SUV|/CUV)?\s*(?:cỡ|hạng)\s*([ABCDE])", s, flags=re.IGNORECASE)
    if m_cuv:
        size = m_cuv.group(1).upper()
        return f"SUV cỡ {size}" if size in ("D", "E") else f"CUV/SUV cỡ {size}"
    m_sedan = re.search(r"Sedan\s*(?:hạng|cỡ)\s*([ABCDE])", s, flags=re.IGNORECASE)
    if m_sedan:
        return f"Sedan hạng {m_sedan.group(1).upper()}"
    return s


def normalize_prices(item: dict) -> dict:
    """Ensure all 4 financial columns are clean, consistent numbers in Million VND (Triệu VNĐ)."""
    listed = round(float(item.get("listed_price") or 0), 1)
    discount = round(float(item.get("discount_amount") or 0), 1)
    net = round(float(item.get("net_price") or 0), 1)

    # Convert if AI accidentally returned raw VND instead of Million VND
    if listed > 50000:
        listed = round(listed / 1_000_000, 1)
    if discount > 50000:
        discount = round(discount / 1_000_000, 1)
    if net > 50000:
        net = round(net / 1_000_000, 1)

    if listed > 0 and net <= 0:
        net = round(max(0.0, listed - discount), 1)
    elif net > 0 and listed <= 0:
        listed = round(net + discount, 1)
    elif listed > 0 and net > 0 and discount == 0 and listed > net:
        discount = round(listed - net, 1)

    otr_tinh = round(float(item.get("otr_tinh") or 0), 1)
    otr_hcm = round(float(item.get("otr_hcm") or 0), 1)
    otr_hn = round(float(item.get("otr_hn") or 0), 1)

    if otr_tinh > 50000:
        otr_tinh = round(otr_tinh / 1_000_000, 1)
    if otr_hcm > 50000:
        otr_hcm = round(otr_hcm / 1_000_000, 1)
    if otr_hn > 50000:
        otr_hn = round(otr_hn / 1_000_000, 1)

    base_tax_price = listed if listed > 0 else net
    if otr_tinh <= 0 and net > 0:
        otr_tinh = round(net + base_tax_price * 0.10 + 3.0, 1)
    if otr_hcm <= 0 and net > 0:
        otr_hcm = round(net + base_tax_price * 0.10 + 22.0, 1)
    if otr_hn <= 0 and net > 0:
        otr_hn = round(net + base_tax_price * 0.12 + 22.0, 1)

    discount_pct = round((discount / listed) * 100, 1) if listed > 0 else 0.0

    item["segment"] = normalize_segment(item.get("segment", "Khác"))
    item["listed_price"] = listed
    item["discount_amount"] = discount
    item["discount_pct"] = discount_pct
    item["net_price"] = net
    item["otr_tinh"] = otr_tinh
    item["otr_hcm"] = otr_hcm
    item["otr_hn"] = otr_hn
    return item


# =====================================================================
# Step 1: Multi-Layer Resilient Video Discovery & Metadata/Subtitles
# =====================================================================
_RSS_CACHE = None


def fetch_rss_metadata() -> dict:
    """Fetch official YouTube XML RSS feed for @xeviet.official with browser headers."""
    global _RSS_CACHE
    if _RSS_CACHE is not None:
        return _RSS_CACHE

    rss_map = {}
    try:
        import xml.etree.ElementTree as ET
        rss_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCP1bUP1hdVaqjCTtQgBVZhA"
        resp = requests.get(rss_url, headers=BROWSER_HEADERS, timeout=12)
        if resp.ok:
            root = ET.fromstring(resp.text)
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "yt": "http://www.youtube.com/xml/schemas/2015",
                "media": "http://search.yahoo.com/mrss/",
            }
            for entry in root.findall("atom:entry", ns):
                vid_el = entry.find("yt:videoId", ns)
                title_el = entry.find("atom:title", ns)
                pub_el = entry.find("atom:published", ns)
                group_el = entry.find("media:group", ns)
                desc_el = group_el.find("media:description", ns) if group_el is not None else None
                if vid_el is not None and vid_el.text:
                    vid_id = vid_el.text.strip()
                    # Convert UTC published timestamp to Vietnam date (UTC+7)
                    upload_date = datetime.now(VN_TZ).strftime("%Y-%m-%d")
                    if pub_el is not None and pub_el.text:
                        try:
                            dt = datetime.fromisoformat(pub_el.text.replace("Z", "+00:00")).astimezone(VN_TZ)
                            upload_date = dt.strftime("%Y-%m-%d")
                        except Exception:
                            upload_date = pub_el.text[:10]
                    rss_map[vid_id] = {
                        "video_id": vid_id,
                        "title": title_el.text.strip() if (title_el is not None and title_el.text) else "",
                        "upload_date": upload_date,
                        "description": desc_el.text.strip() if (desc_el is not None and desc_el.text) else "",
                        "url": f"https://www.youtube.com/watch?v={vid_id}",
                    }
    except Exception as e:
        print(f"⚠️ Cảnh báo đọc RSS: {e}")

    if rss_map:
        _RSS_CACHE = rss_map
    return rss_map


def fetch_oembed_title(video_id: str) -> str:
    """Fallback #3: Official YouTube oEmbed endpoint (never blocked on cloud IPs)."""
    try:
        url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=8)
        if resp.ok:
            return (resp.json().get("title") or "").strip()
    except Exception:
        pass
    return ""


def fetch_latest_channel_videos(channel_url: str, max_videos: int = 5) -> List[dict]:
    print(f"🔍 Đang quét {max_videos} video mới nhất từ kênh: {channel_url} ...")
    rss_map = fetch_rss_metadata()
    videos_by_id = {}
    ordered_ids = []

    # Source A: RSS Feed (fastest, includes exact publish timestamp & description)
    for vid_id, item in list(rss_map.items())[:max_videos]:
        videos_by_id[vid_id] = dict(item)
        ordered_ids.append(vid_id)

    # Source B: yt-dlp flat extraction (can reach beyond 15 videos if max_videos > 15)
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "playlistend": max_videos,
            "http_headers": BROWSER_HEADERS,
            "extractor_args": {"youtube": {"lang": ["vi"]}},
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
            entries = info.get("entries", []) if info else []
            if entries:
                ordered_ids = []
                for entry in entries[:max_videos]:
                    if not entry or not entry.get("id"):
                        continue
                    vid_id = entry["id"]
                    ordered_ids.append(vid_id)
                    rss_item = rss_map.get(vid_id, {})
                    title = rss_item.get("title") or entry.get("title") or fetch_oembed_title(vid_id)
                    videos_by_id[vid_id] = {
                        "video_id": vid_id,
                        "title": title,
                        "upload_date": rss_item.get("upload_date", ""),
                        "description": rss_item.get("description", ""),
                        "url": f"https://www.youtube.com/watch?v={vid_id}",
                    }
    except Exception as e:
        print(f"⚠️ yt-dlp quét danh sách gặp giới hạn ({e}), dùng danh sách từ RSS Feed...")

    videos = [videos_by_id[vid_id] for vid_id in ordered_ids if vid_id in videos_by_id][:max_videos]
    print(f"✅ Tìm thấy {len(videos)} video trên kênh.")
    return videos


def fetch_video_details_and_transcript(vid_stub: dict) -> dict:
    """
    Fetch full video metadata and timestamped Vietnamese transcript.
    Decouples metadata extraction from subtitle download so HTTP 429 on subtitles never wipes out title/description.
    """
    video_id = vid_stub["video_id"]
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    rss_map = fetch_rss_metadata()
    rss_item = rss_map.get(video_id, {})

    details = {
        "video_id": video_id,
        "url": video_url,
        "title": rss_item.get("title") or vid_stub.get("title") or "",
        "description": rss_item.get("description") or vid_stub.get("description") or "",
        "upload_date": rss_item.get("upload_date") or vid_stub.get("upload_date") or datetime.now(VN_TZ).strftime("%Y-%m-%d"),
        "duration": 0,
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "chapters": [],
        "transcript_text": "",
    }

    # Step 1A: Extract metadata ONLY (download=False) so subtitle errors never cause info=None
    try:
        meta_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "http_headers": BROWSER_HEADERS,
            "extractor_args": {"youtube": {"lang": ["vi"]}},
        }
        with yt_dlp.YoutubeDL(meta_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            if info:
                details["title"] = details["title"] or info.get("title") or ""
                details["description"] = details["description"] or info.get("description") or ""
                details["duration"] = int(info.get("duration") or 0)
                if not rss_item.get("upload_date"):
                    raw_date = info.get("upload_date")
                    if raw_date and len(raw_date) == 8:
                        details["upload_date"] = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                if info.get("chapters"):
                    details["chapters"] = [
                        {
                            "title": c.get("title", ""),
                            "start_time": int(c.get("start_time", 0)),
                            "label": format_timestamp(int(c.get("start_time", 0))),
                        }
                        for c in info.get("chapters", [])
                    ]
    except Exception as e:
        print(f"ℹ️ Metadata yt-dlp ({video_id}): dùng dữ liệu RSS/oEmbed ({str(e)[:80]})")

    if not details["title"]:
        details["title"] = fetch_oembed_title(video_id)

    # Step 1B: Try youtube-transcript-api first (lightweight), then yt-dlp subtitle download
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt_api = YouTubeTranscriptApi()
        fetched = ytt_api.fetch(video_id, languages=["vi", "en"])
        lines = []
        for snippet in fetched:
            start_sec = int(getattr(snippet, "start", 0))
            text = getattr(snippet, "text", "").strip()
            if text:
                lines.append(f"[{format_timestamp(start_sec)} ({start_sec}s)] {text}")
        details["transcript_text"] = "\n".join(lines)
    except Exception:
        pass

    if not details["transcript_text"]:
        with tempfile.TemporaryDirectory() as tmpdir:
            sub_opts = {
                "quiet": True,
                "no_warnings": True,
                "ignoreerrors": True,
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["vi"],
                "subtitlesformat": "json3",
                "http_headers": BROWSER_HEADERS,
                "extractor_args": {"youtube": {"lang": ["vi"]}},
                "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
            }
            try:
                with yt_dlp.YoutubeDL(sub_opts) as ydl:
                    ydl.download([video_url])
            except Exception:
                pass

            for fname in os.listdir(tmpdir):
                if fname.endswith(".json3"):
                    sub_path = os.path.join(tmpdir, fname)
                    try:
                        with open(sub_path, "r", encoding="utf-8") as f:
                            sub_data = json.load(f)
                        lines = []
                        for event in sub_data.get("events", []):
                            segs = event.get("segs")
                            if not segs:
                                continue
                            text = "".join(s.get("utf8", "") for s in segs).strip()
                            text = re.sub(r"\s+", " ", text)
                            if not text:
                                continue
                            start_sec = int(event.get("tStartMs", 0) // 1000)
                            lines.append(f"[{format_timestamp(start_sec)} ({start_sec}s)] {text}")
                        details["transcript_text"] = "\n".join(lines)
                    except Exception:
                        pass

    return details


# =====================================================================
# Step 2: Multimodal Extraction with Gemini API + Rate-Limit Retry
# =====================================================================
EXTRACTION_PROMPT = """
Bạn là chuyên gia phân tích dữ liệu giá xe ô tô tại thị trường Việt Nam, chuyên theo dõi kênh YouTube 'Xe Việt' (@xeviet.official).

NHIỆM VỤ:
Hãy phân tích kỹ toàn bộ thông tin của video dưới đây (bao gồm: Hình ảnh bảng giá hiển thị trực tiếp trên màn hình video, Phụ đề lời thoại theo từng giây, Tiêu đề và Mô tả video) để trích xuất bảng báo giá chi tiết của TẤT CẢ các mẫu xe / phiên bản xe được đề cập.

QUY TẮC BẮT BUỘC:
1. Lọc video:
   - Nếu video chỉ nói về tin tức giao thông, luật, đánh giá kỹ thuật thuần túy mà KHÔNG có báo giá xe hay mức giảm giá/ưu đãi nào -> Đặt `has_car_pricing = false` và `cars = []`.
   - Nếu video có nhắc đến giá bán, giá niêm yết, tiền giảm giá/ưu đãi, hoặc giá lăn bánh của bất kỳ mẫu xe nào -> Đặt `has_car_pricing = true` và trích xuất đầy đủ từng phiên bản.

2. Chuẩn hóa 4 cột tài chính (Tất cả tính theo đơn vị: TRIỆU VNĐ, ví dụ 749 triệu thì ghi `749`, 1 tỷ 050 triệu thì ghi `1050`):
   - `listed_price` (Giá niêm yết): Giá niêm yết hãng công bố.
   - `discount_amount` (Số tiền ưu đãi): Tổng số tiền giảm giá tiền mặt (hoặc hỗ trợ 50%/100% lệ phí trước bạ đã quy đổi thành số tiền mặt).
     * LƯU Ý QUAN TRỌNG: TUYỆT ĐỐI KHÔNG lấy thông tin ghi chú quà tặng (như dán phim, trải sàn, cam hành trình...). Chỉ lấy con số tiền giảm bằng số!
   - `net_price` (Giá sau giảm): Bằng `listed_price - discount_amount` (hoặc giá thực tế đại lý/chủ xe chào bán sau khi trừ ưu đãi).
   - `otr_tinh`, `otr_hcm`, `otr_hn` (Giá lăn bánh tại Tỉnh, TP.HCM, Hà Nội):
     * Lấy chính xác từ bảng giá trên màn hình video hoặc lời đọc của MC.
     * Nếu trong video chỉ đọc lăn bánh 1 khu vực hoặc chưa đọc đủ 3 khu vực, hãy tự động tính toán bổ sung khu vực còn thiếu dựa trên Giá sau giảm và lệ phí trước bạ/biển số chuẩn tại Việt Nam để bảng luôn đủ 3 khu vực Tỉnh / TP.HCM / Hà Nội.

3. Mốc thời gian (`timestamp_seconds` và `timestamp_label`):
   - Xác định đúng số giây (`timestamp_seconds`) và mốc phút:giây (`MM:SS`) trong video khi chiếc xe hoặc bảng giá của chiếc xe đó xuất hiện để người dùng bấm vào kiểm chứng ngay.

4. Quy tắc đặt tên phiên bản (`trim`) ngắn gọn và thống nhất:
   - Giữ tên `trim` ngắn gọn, dễ đọc; KHÔNG thêm chữ "Bản " hay "Phiên bản " ở đầu (VD: ghi `Hybrid` thay vì `Bản Hybrid`, ghi `Xăng` thay vì `Bản xăng`).
   - KHÔNG lặp lại tên hãng hoặc tên dòng xe trong `trim`.
   - KHÔNG gắn dung tích động cơ (như `1.5`, `1.5T`, `1.5L`) vào đầu `trim` nếu tên phiên bản đó đã tự phân biệt rõ ràng trong dòng xe (VD: ghi `Premium`, `Flagship`, `Executive`, `Đặc biệt`, `Cao cấp` thay vì `1.5T Premium`, `1.5 Đặc biệt`). Chỉ giữ thông số động cơ/nhiên liệu/dẫn động khi cần để phân biệt các phiên bản khác nhau (VD: `2.0 Xăng Đặc biệt` vs `2.0 Dầu Đặc biệt`, `1.6 Turbo`, `GLS 1 cầu` vs `GLS 2 cầu`).
   - Nếu trong danh sách "CÁC PHIÊN BẢN ĐÃ CÓ SẴN TRONG HỆ THỐNG" bên dưới đã có cùng mẫu xe + cùng giá niêm yết (kể cả khác năm VIN), HÃY DÙNG CHÍNH XÁC tên `brand`, `model`, `trim` đó để dữ liệu gom chung thành 1 dòng lịch sử duy nhất!
"""


def _is_rate_limit_or_transient(err_str: str) -> bool:
    upper = err_str.upper()
    return any(k in upper for k in ["429", "RESOURCE_EXHAUSTED", "QUOTA", "503", "UNAVAILABLE", "DEADLINE_EXCEEDED", "TIMEOUT"])


def extract_car_prices_with_gemini(video_details: dict, api_key: str, existing_cars: Optional[List[dict]] = None) -> Optional[dict]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key.strip("'\" "))
    preferred_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
    candidate_models = [preferred_model]
    for m in [
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-flash-lite-latest",
    ]:
        if m not in candidate_models:
            candidate_models.append(m)

    chapters_str = "\n".join(
        f"- {c['label']} ({c['start_time']}s): {c['title']}"
        for c in video_details.get("chapters", [])
    )
    existing_catalog_str = ""
    if existing_cars:
        catalog_lines = [
            f"- {c.get('brand')} | {c.get('model')} | {c.get('trim')} | VIN {c.get('year')} | Niêm yết {c.get('listed_price'):g}tr"
            for c in sorted(
                existing_cars,
                key=lambda x: (str(x.get("brand", "")), str(x.get("model", "")), int(x.get("year") or 0), str(x.get("trim", ""))),
            )[:120]
        ]
        existing_catalog_str = (
            "\n\nCÁC PHIÊN BẢN ĐÃ CÓ SẴN TRONG HỆ THỐNG (ưu tiên tái sử dụng đúng tên nếu cùng xe và cùng giá niêm yết, kể cả khác năm VIN):\n"
            + "\n".join(catalog_lines)
        )

    context_text = (
        f"THÔNG TIN VIDEO YOUTUBE:\n"
        f"- Tiêu đề: {video_details['title']}\n"
        f"- Ngày đăng: {video_details['upload_date']}\n"
        f"- Link: {video_details['url']}\n"
        f"- Mô tả:\n{video_details['description'][:3500]}\n\n"
        f"- Chapters (Mốc thời gian):\n{chapters_str or 'Không có'}\n\n"
        f"- Phụ đề lời thoại kèm mốc thời gian:\n{video_details['transcript_text'][:45000]}"
        f"{existing_catalog_str}\n"
    )

    has_sufficient_text = len(video_details.get("transcript_text", "").strip()) > 200 or len(video_details.get("description", "").strip()) > 300

    # Pass 1: Try Multimodal (YouTube Video URI + Context Text) across candidate models
    for model_name in candidate_models:
        try:
            print(f"🤖 Đang phân tích video (Hình ảnh + Lời thoại + Mô tả) bằng {model_name}: {video_details['title']} ...")
            response = client.models.generate_content(
                model=model_name,
                contents=[
                    types.Part.from_uri(file_uri=video_details["url"], mime_type="video/*"),
                    EXTRACTION_PROMPT + "\n\n" + context_text,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=VideoExtractionResult,
                    temperature=0.1,
                ),
            )
            if response and response.text:
                return json.loads(response.text)
        except Exception as e:
            err_str = str(e)
            print(f"ℹ️ Model {model_name} (Video URI) báo bận/lỗi ({err_str[:110]}), tự động chuyển model dự phòng...")
            time.sleep(2)

    # Pass 2: Fallback using Transcript + Chapters + Description across candidate models
    if not has_sufficient_text:
        print("⚠️ Không có dữ liệu phụ đề/mô tả văn bản để chạy chế độ dự phòng. Giữ lại video để quét ở lần chạy tiếp theo.")
        return None

    for model_name in candidate_models:
        try:
            print(f"📝 Đang phân tích Phụ đề + Mô tả bằng {model_name}: {video_details['title']} ...")
            response = client.models.generate_content(
                model=model_name,
                contents=[EXTRACTION_PROMPT + "\n\n" + context_text],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=VideoExtractionResult,
                    temperature=0.1,
                ),
            )
            if response and response.text:
                return json.loads(response.text)
        except Exception as e2:
            err_str = str(e2)
            print(f"⚠️ Model {model_name} (Văn bản) báo bận/lỗi ({err_str[:110]}), thử model tiếp theo...")
            time.sleep(2)

    return None


# =====================================================================
# Step 3: Merge Historical Database, Self-Heal & Compute Fluctuations
# =====================================================================
def load_database() -> dict:
    if CAR_PRICES_JSON.exists():
        try:
            with open(CAR_PRICES_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "channel": {
            "name": "Xe Việt Official",
            "handle": "@xeviet.official",
            "url": "https://www.youtube.com/@xeviet.official",
        },
        "last_updated": "",
        "videos": [],
        "cars": [],
    }


def self_heal_database(db: dict, processed_ids: set) -> bool:
    """
    Repair any existing video entries with missing titles using RSS/oEmbed,
    and unmark any video that previously failed due to empty transcript/metadata so it gets re-extracted.
    """
    rss_map = fetch_rss_metadata()
    changed = False
    valid_videos = []

    for v in db.get("videos", []):
        vid_id = v.get("video_id", "")
        summary = v.get("summary", "")
        # Detect false-negative entry caused by empty transcript/metadata on cloud runner
        if not v.get("has_car_pricing") and ("không có nội dung phụ đề" in summary.lower() or not v.get("title")):
            print(f"🔧 Phát hiện video [{vid_id}] từng bị lỗi thiếu phụ đề trước đó -> Đưa vào danh sách quét bù!")
            processed_ids.discard(vid_id)
            changed = True
            continue

        # Repair missing title / upload_date if cars were already extracted
        if not v.get("title"):
            rss_item = rss_map.get(vid_id, {})
            repaired_title = rss_item.get("title") or fetch_oembed_title(vid_id)
            if repaired_title:
                v["title"] = repaired_title
                if rss_item.get("upload_date"):
                    v["upload_date"] = rss_item["upload_date"]
                print(f"🔧 Đã khôi phục tiêu đề cho video [{vid_id}]: {repaired_title}")
                changed = True

                # Also repair video_title inside car records
                for c in db.get("cars", []):
                    if c.get("video_id") == vid_id and not c.get("video_title"):
                        c["video_title"] = repaired_title
                    for h in c.get("history", []):
                        if h.get("video_id") == vid_id and not h.get("video_title"):
                            h["video_title"] = repaired_title

        valid_videos.append(v)

    if deduplicate_cars_in_db(db):
        changed = True

    if changed:
        db["videos"] = valid_videos
    return changed


def save_database(db: dict):
    db["last_updated"] = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    with open(CAR_PRICES_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    js_content = f"window.CAR_PRICE_DATA = {json.dumps(db, ensure_ascii=False, indent=2)};\n"
    with open(CAR_PRICES_JS, "w", encoding="utf-8") as f:
        f.write(js_content)


def merge_extractions_into_db(db: dict, video_details: dict, extraction: dict) -> List[dict]:
    """Merge extracted cars from a video into the historical database and return newly updated car records."""
    video_id = video_details["video_id"]
    upload_date = video_details["upload_date"]

    # Update or insert video entry
    db["videos"] = [v for v in db.get("videos", []) if v.get("video_id") != video_id]
    db["videos"].append({
        "video_id": video_id,
        "title": video_details["title"],
        "url": video_details["url"],
        "upload_date": upload_date,
        "thumbnail": video_details["thumbnail"],
        "has_car_pricing": extraction.get("has_car_pricing", False),
        "summary": extraction.get("video_summary", ""),
        "extracted_count": len(extraction.get("cars", [])),
    })
    db["videos"].sort(key=lambda x: (x.get("upload_date", ""), x.get("video_id", "")), reverse=True)

    if not extraction.get("has_car_pricing") or not extraction.get("cars"):
        return []

    cars_by_id = {c["car_id"]: c for c in db.get("cars", [])}
    updated_items = []

    for raw_car in extraction.get("cars", []):
        car = normalize_prices(dict(raw_car))
        brand, model, trim = clean_car_fields(
            car.get("brand", ""),
            car.get("model", ""),
            car.get("trim", "Tiêu chuẩn"),
        )
        year = int(car.get("year") or datetime.now(VN_TZ).year)
        car_id = make_car_id(brand, model, trim)

        ts_sec = max(0, int(car.get("timestamp_seconds") or 0))
        ts_label = car.get("timestamp_label") or format_timestamp(ts_sec)
        video_link_at_ts = f"https://www.youtube.com/watch?v={video_id}&t={ts_sec}s"

        snapshot = {
            "date": upload_date,
            "year": year,
            "video_id": video_id,
            "video_title": video_details["title"],
            "video_url": video_link_at_ts,
            "timestamp_seconds": ts_sec,
            "timestamp_label": ts_label,
            "listed_price": car["listed_price"],
            "discount_amount": car["discount_amount"],
            "discount_pct": car["discount_pct"],
            "net_price": car["net_price"],
            "otr_tinh": car["otr_tinh"],
            "otr_hcm": car["otr_hcm"],
            "otr_hn": car["otr_hn"],
        }

        incoming_probe = {
            "brand": brand,
            "model": model,
            "trim": trim,
            "year": year,
            "listed_price": car["listed_price"],
        }

        matched_id = car_id if car_id in cars_by_id else None
        if matched_id is None:
            for existing_id, existing_entry in cars_by_id.items():
                if are_same_car_variant(existing_entry, incoming_probe):
                    matched_id = existing_id
                    break

        if matched_id is None:
            car_entry = {
                "car_id": car_id,
                "brand": brand,
                "model": model,
                "trim": trim,
                "year": year,
                "segment": car.get("segment", "Khác"),
                "history": [snapshot],
            }
            recompute_car_stats(car_entry)
            cars_by_id[car_id] = car_entry
        else:
            car_entry = cars_by_id.pop(matched_id)
            old_entry_year = int(car_entry.get("year") or 0)
            merged_year = max(old_entry_year, year)
            preferred_trim = choose_preferred_trim(car_entry.get("trim", ""), trim)
            if preferred_trim != car_entry.get("trim", "") or trim != car_entry.get("trim", "") or old_entry_year != year:
                vin_info = f"VIN {old_entry_year}+{year}->{merged_year}" if old_entry_year != year else f"VIN {merged_year}"
                print(
                    f"🔗 Gom phiên bản mới trùng khớp: {brand} {model} '{car_entry.get('trim')}' + '{trim}' "
                    f"-> '{preferred_trim}' ({vin_info}, Niêm yết {car['listed_price']:g}tr)"
                )
            car_entry["brand"] = brand
            car_entry["model"] = model
            car_entry["trim"] = preferred_trim
            car_entry["year"] = merged_year
            car_entry["car_id"] = make_car_id(brand, model, preferred_trim)
            car_entry["segment"] = normalize_segment(car.get("segment") or car_entry.get("segment", "Khác"))
            history = [h for h in car_entry.get("history", []) if h.get("video_id") != video_id]
            history.append(snapshot)
            car_entry["history"] = history
            recompute_car_stats(car_entry)
            cars_by_id[car_entry["car_id"]] = car_entry

        updated_items.append(car_entry)

    db["cars"] = sorted(
        cars_by_id.values(),
        key=lambda c: (c.get("latest_date", ""), c.get("discount_amount", 0)),
        reverse=True,
    )
    deduplicate_cars_in_db(db)
    return updated_items


# =====================================================================
# Step 4: Notifications (Telegram & Email)
# =====================================================================
def send_notifications(new_videos_count: int, updated_cars: List[dict]):
    if not updated_cars:
        return

    top_deals = sorted(updated_cars, key=lambda c: c.get("discount_amount", 0), reverse=True)[:8]
    dashboard_url = os.getenv("DASHBOARD_URL", "").strip() or "https://moeluoi.github.io/gia-xe-xeviet/"

    lines = [
        f"🚗 *CẬP NHẬT GIÁ XE MỚI (@xeviet.official)*",
        f"📅 Thời gian: {datetime.now(VN_TZ).strftime('%d/%m/%Y %H:%M')}",
        f"🎬 Số video mới quét: {new_videos_count} | Số mẫu xe cập nhật: {len(updated_cars)}",
        "",
        "🔥 *TOP KÈO THƠM / ƯU ĐÃI SÂU NHẤT:*",
    ]
    for i, c in enumerate(top_deals, 1):
        delta_str = ""
        if c.get("net_price_change", 0) < 0:
            delta_str = f" (📉 Giảm thêm {abs(c['net_price_change']):g}tr)"
        elif c.get("net_price_change", 0) > 0:
            delta_str = f" (📈 Tăng {c['net_price_change']:g}tr)"
        lines.append(
            f"{i}. *{c['brand']} {c['model']} {c['trim']} ({c['year']})*\n"
            f"   • Niêm yết: {c['listed_price']:g}tr | *Ưu đãi: -{c['discount_amount']:g}tr*\n"
            f"   • Giá sau giảm: *{c['net_price']:g}tr*{delta_str}\n"
            f"   • Lăn bánh: Tỉnh {c['otr_tinh']:g}tr | HCM {c['otr_hcm']:g}tr | HN {c['otr_hn']:g}tr\n"
            f"   • [Xem video tại {c['timestamp_label']}]({c['video_url']})"
        )

    if dashboard_url:
        lines.append(f"\n📱 [Mở Dashboard Theo Dõi Giá Xe]({dashboard_url})")

    message_md = "\n".join(lines)

    # 1. Telegram Notification
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip("'\" ")
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip("'\" ")
    if tg_token and tg_chat_id:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                json={
                    "chat_id": tg_chat_id,
                    "text": message_md,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if resp.ok:
                print("📨 Đã gửi thông báo qua Telegram thành công!")
            else:
                print(f"⚠️ Gửi Telegram thất bại: {resp.text}")
        except Exception as e:
            print(f"⚠️ Lỗi khi gửi Telegram: {e}")

    # 2. Email Notification (SMTP)
    smtp_user = os.getenv("EMAIL_ADDRESS", "").strip()
    smtp_pass = os.getenv("EMAIL_APP_PASSWORD", "").strip()
    email_to = os.getenv("EMAIL_RECEIVER", smtp_user).strip()
    if smtp_user and smtp_pass and email_to:
        try:
            msg = MIMEMultipart()
            msg["From"] = smtp_user
            msg["To"] = email_to
            msg["Subject"] = f"[Xe Việt Dashboard] Cập nhật {len(updated_cars)} báo giá xe mới ({datetime.now(VN_TZ).strftime('%d/%m/%Y')})"
            msg.attach(MIMEText(message_md.replace("*", ""), "plain", "utf-8"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            print("📧 Đã gửi thông báo qua Email thành công!")
        except Exception as e:
            print(f"⚠️ Lỗi khi gửi Email: {e}")


# =====================================================================
# Main CLI Entrypoint
# =====================================================================
def main():
    parser = argparse.ArgumentParser(description="Thu thập & bóc tách báo giá xe từ kênh YouTube @xeviet.official")
    parser.add_argument("--max-videos", type=int, default=int(os.getenv("MAX_VIDEOS", "5")), help="Số lượng video mới nhất cần kiểm tra (mặc định: 5)")
    parser.add_argument("--force", action="store_true", help="Quét lại cả các video đã xử lý trước đó")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("⚠️ Chưa tìm thấy GEMINI_API_KEY trong biến môi trường hoặc file .env.")
        print("👉 Hệ thống sẽ giữ nguyên dữ liệu mẫu/hiện có để bạn vẫn xem được Dashboard ngay.")
        return

    db = load_database()
    processed_ids = set()
    if PROCESSED_VIDEOS_JSON.exists() and not args.force:
        try:
            with open(PROCESSED_VIDEOS_JSON, "r", encoding="utf-8") as f:
                processed_ids = set(json.load(f))
        except Exception:
            pass

    # Self-heal any missing video titles or false-negative entries from prior runs
    if self_heal_database(db, processed_ids):
        save_database(db)
        with open(PROCESSED_VIDEOS_JSON, "w", encoding="utf-8") as f:
            json.dump(sorted(list(processed_ids)), f, ensure_ascii=False, indent=2)

    videos = fetch_latest_channel_videos(CHANNEL_URL, max_videos=args.max_videos)
    videos_to_process = [v for v in reversed(videos) if v["video_id"] not in processed_ids]

    if not videos_to_process:
        print("✅ Không có video mới nào cần xử lý. Dữ liệu đã ở trạng thái mới nhất!")
        return

    all_updated_cars = []
    processed_count = 0

    for idx, vid in enumerate(videos_to_process):
        vid_id = vid["video_id"]
        print(f"\n------------------------------------------------------------")
        print(f"🎬 [{idx + 1}/{len(videos_to_process)}] Đang xử lý video [{vid_id}]: {vid.get('title', '')}")
        details = fetch_video_details_and_transcript(vid)
        extraction = extract_car_prices_with_gemini(details, api_key, existing_cars=db.get("cars", []))
        if extraction is None:
            print(f"⚠️ Tạm hoãn video [{vid_id}] để lần chạy kế tiếp quét lại (không đánh dấu đã xử lý).")
            continue

        # Extra safety: if AI returned has_car_pricing=False SOLELY because transcript & description were empty, do not mark processed
        summary_lower = (extraction.get("video_summary") or "").lower()
        if not extraction.get("has_car_pricing") and ("không có nội dung phụ đề" in summary_lower or not details.get("title")):
            print(f"⚠️ Video [{vid_id}] thiếu dữ liệu văn bản, giữ lại cho lần quét tiếp theo.")
            continue

        if extraction.get("has_car_pricing"):
            print(f"✅ Trích xuất được {len(extraction.get('cars', []))} mẫu xe từ video: {details['title']}!")
            updated = merge_extractions_into_db(db, details, extraction)
            all_updated_cars.extend(updated)
        else:
            print("⏭️ Video thuần tin tức (không có báo giá xe), tự động bỏ qua.")
            merge_extractions_into_db(db, details, extraction)

        processed_ids.add(vid_id)
        processed_count += 1
        save_database(db)
        with open(PROCESSED_VIDEOS_JSON, "w", encoding="utf-8") as f:
            json.dump(sorted(list(processed_ids)), f, ensure_ascii=False, indent=2)

        # Pause 15s between videos if there are more videos in the queue to respect Gemini Free Tier TPM limits
        if idx < len(videos_to_process) - 1:
            print("⏳ Nghỉ 15 giây trước khi xử lý video tiếp theo để tránh chạm giới hạn tốc độ Gemini...")
            time.sleep(15)

    if all_updated_cars:
        send_notifications(processed_count, all_updated_cars)

    print(f"\n🎉 Hoàn tất! Đã cập nhật tổng cộng {len(db.get('cars', []))} mẫu xe trong Dashboard.")


if __name__ == "__main__":
    main()
