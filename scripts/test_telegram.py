import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv, set_key

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
ENV_FILE = ROOT_DIR / ".env"
if not ENV_FILE.exists():
    ENV_FILE.write_text("", encoding="utf-8")

load_dotenv(ENV_FILE)

from scripts.fetch_and_extract import load_database, send_notifications


def auto_detect_chat_id(bot_token: str) -> str:
    """Automatically detect user's Chat ID from the latest message sent to the bot."""
    try:
        resp = requests.get(f"https://api.telegram.org/bot{bot_token}/getUpdates", timeout=10)
        if resp.ok:
            data = resp.json()
            results = data.get("result", [])
            for item in reversed(results):
                msg = item.get("message") or item.get("my_chat_member") or {}
                chat = msg.get("chat", {})
                if chat.get("id"):
                    return str(chat["id"])
    except Exception as e:
        print(f"⚠️ Lỗi khi tự động tìm Chat ID: {e}")
    return ""


def main():
    print("============================================================")
    print("   🤖 CÀI ĐẶT & KIỂM TRA BOT TELEGRAM BÁO GIÁ XE TỰ ĐỘNG")
    print("============================================================")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        token = input("\n👉 Bước 1: Hãy dán mã TELEGRAM_BOT_TOKEN (lấy từ @BotFather) vào đây rồi bấm Enter:\n> ").strip()
        if not token:
            print("❌ Bạn chưa nhập Token. Hãy chạy lại khi đã có Token nhé!")
            return
        set_key(str(ENV_FILE), "TELEGRAM_BOT_TOKEN", token)
        os.environ["TELEGRAM_BOT_TOKEN"] = token

    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        print("\n🔍 Đang tự động tìm Chat ID của bạn...")
        chat_id = auto_detect_chat_id(token)
        if not chat_id:
            print("\n⚠️ Chưa thấy tin nhắn nào gửi tới Bot của bạn.")
            print("👉 Hãy mở Telegram, bấm vào con Bot bạn vừa tạo, bấm nút 'START' (hoặc nhắn chữ 'alo' cho nó).")
            input("Sau khi nhắn 'alo' cho Bot xong, hãy bấm phím ENTER tại đây để tìm lại...")
            chat_id = auto_detect_chat_id(token)

        if not chat_id:
            chat_id = input("Hoặc nhập trực tiếp mã TELEGRAM_CHAT_ID của bạn (nếu biết): ").strip()

        if chat_id:
            set_key(str(ENV_FILE), "TELEGRAM_CHAT_ID", chat_id)
            os.environ["TELEGRAM_CHAT_ID"] = chat_id
            print(f"✅ Đã tự động nhận diện được TELEGRAM_CHAT_ID của bạn là: {chat_id}")
        else:
            print("❌ Vẫn chưa tìm thấy Chat ID. Hãy chắc chắn bạn đã bấm START trong con Bot của mình!")
            return

    print(f"\n📨 Đang gửi thử bản tin Báo Giá Xe (5 video mới nhất) tới Telegram của bạn (Chat ID: {chat_id})...")
    db = load_database()
    cars = db.get("cars", [])
    send_notifications(len(db.get("videos", [])), cars)
    print("\n============================================================")
    print("🎉 HÃY KIỂM TRA ĐIỆN THOẠI! Tin nhắn báo giá đã được gửi vào Telegram.")
    print(f"📌 Thông tin của bạn (dùng để điền lên GitHub lúc sau):")
    print(f"   - TELEGRAM_BOT_TOKEN : {token}")
    print(f"   - TELEGRAM_CHAT_ID   : {chat_id}")
    print("============================================================")


if __name__ == "__main__":
    main()
