# -*- coding: utf-8 -*-
"""
Lightweight Telegram Tracking Bot (Chạy trực tiếp với Python)
Không cần Node.js, không cần MySQL, không cần cài thư viện ngoài.
Chỉ cần Token từ @BotFather.
"""

import os
import sys
import json
import time
import sqlite3
import threading
import urllib.request
import urllib.error
import urllib.parse
import re
import html
from datetime import datetime
from tracking_core import track_order, detect_carrier
from shopee_tool import (
    check_shopee_account, 
    get_shopee_delivering_orders, 
    save_shopee_voucher, 
    save_shopee_vouchers,
    check_shopee_voucher,
    format_voucher_check_message
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "tele_tracking.db")
ENV_FILE = os.path.join(BASE_DIR, ".env")

def escape_html(text):
    """Thoát các ký tự HTML đặc biệt để tránh lỗi parse mode của Telegram"""
    if text is None:
        return ""
    return html.escape(str(text), quote=False)

def get_env_val(key, default=""):
    if not os.path.exists(ENV_FILE):
        return default
    try:
        with open(ENV_FILE, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return default

try:
    CHECK_INTERVAL_SECONDS = int(get_env_val("CHECK_INTERVAL_SECONDS", "300"))
except ValueError:
    CHECK_INTERVAL_SECONDS = 300

# ==================== CẤU HÌNH TOKEN ====================

def get_env_token():
    return get_env_val("TELEGRAM_BOT_TOKEN", "")

def save_env_token(token):
    token = token.strip()
    lines = []
    found = False
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                if line.strip().startswith("TELEGRAM_BOT_TOKEN="):
                    lines[i] = f"TELEGRAM_BOT_TOKEN={token}\n"
                    found = True
                    break
        except Exception:
            pass
    
    if not found:
        lines.append(f"\nTELEGRAM_BOT_TOKEN={token}\n")

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)

# ==================== DATABASE SQLITE (TỐI ƯU WAL & CONCURRENCY) ====================

def get_db_connection():
    """Tạo kết nối SQLite an toàn với timeout và thread-safety"""
    conn = sqlite3.connect(DB_FILE, timeout=20)
    return conn

def init_db():
    with get_db_connection() as conn:
        # Bật WAL mode giúp hỗ trợ đọc/ghi đồng thời nhanh và không bị khóa DB
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracked_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                carrier TEXT NOT NULL,
                tracking_number TEXT NOT NULL,
                credential TEXT,
                note TEXT,
                last_status TEXT,
                last_location TEXT,
                last_time TEXT,
                is_completed INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, carrier, tracking_number)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                chat_id TEXT PRIMARY KEY,
                shopee_cookie TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Tạo chỉ mục (Index) tăng tốc độ truy vấn danh sách và worker kiểm tra
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracked_orders_chat ON tracked_orders(chat_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracked_orders_pending ON tracked_orders(is_completed);")

init_db()

def get_user_cookie(chat_id):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT shopee_cookie FROM user_settings WHERE chat_id = ?", (str(chat_id),))
        row = c.fetchone()
        return row[0] if row and row[0] else ""

def set_user_cookie(chat_id, cookie):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO user_settings (chat_id, shopee_cookie, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                shopee_cookie = excluded.shopee_cookie,
                updated_at = CURRENT_TIMESTAMP
        """, (str(chat_id), cookie))

def delete_user_cookie(chat_id):
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM user_settings WHERE chat_id = ?", (str(chat_id),))

# ==================== TELEGRAM API CLIENT ====================

class TelegramBot:
    def __init__(self, token):
        self.token = token.strip()
        self.api_url = f"https://api.telegram.org/bot{self.token}"
        self.username = ""

    def call_api(self, method, data=None, timeout=35):
        url = f"{self.api_url}/{method}"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ExpressTrackingBot/2.0"
        }
        payload = json.dumps(data).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            try:
                return json.loads(err_body)
            except Exception:
                return {"ok": False, "description": f"HTTP {e.code}: {err_body}"}
        except urllib.error.URLError as e:
            return {"ok": False, "description": f"Lỗi mạng: {e.reason}"}
        except Exception as e:
            return {"ok": False, "description": str(e)}

    def get_me(self):
        return self.call_api("getMe")

    def send_message(self, chat_id, text, parse_mode="HTML", reply_markup=None):
        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        if reply_markup is not None:
            data["reply_markup"] = reply_markup
        return self.call_api("sendMessage", data)

    def get_updates(self, offset=None, timeout=25):
        data = {"timeout": timeout}
        if offset is not None:
            data["offset"] = offset
        return self.call_api("getUpdates", data, timeout=timeout + 10)

def get_check_bot_markup(bot_username=""):
    url = f"https://t.me/{bot_username}" if bot_username else "https://t.me"
    return {
        "inline_keyboard": [
            [
                {"text": "Khách vui lòng gửi vào bot Check ↗", "url": url}
            ]
        ]
    }

# ==================== FORMATTER TIN NHẮN (ESCAPE HTML AN TOÀN) ====================

def format_tracking_result(res, note=""):
    carrier = escape_html(res.get("carrier", "Vận chuyển"))
    tn = escape_html(res.get("tracking_number", ""))
    status = escape_html(res.get("latest_status", "Đang cập nhật"))
    t = escape_html(res.get("latest_time", ""))
    loc = escape_html(res.get("latest_location", ""))
    is_done = res.get("is_completed", False)

    status_icon = "✅" if is_done else "🚚"

    msg = f"📦 <b>{carrier}</b> | <code>{tn}</code>\n"
    if note:
        msg += f"📝 Ghi chú: <i>{escape_html(note)}</i>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"{status_icon} <b>Trạng thái:</b> {status}\n"
    if loc:
        msg += f"📍 <b>Vị trí:</b> {loc}\n"
    if t:
        msg += f"⏰ <b>Thời gian:</b> {t}\n"
    
    events = res.get("events", [])
    if events:
        msg += "\n📜 <b>Lịch sử hành trình:</b>\n"
        for ev in events[:5]:  # Hiển thị 5 sự kiện gần nhất
            ev_status = escape_html(ev.get("status", ""))
            ev_time = escape_html(ev.get("time", ""))
            ev_loc = f" ({escape_html(ev['location'])})" if ev.get("location") else ""
            msg += f"• <code>[{ev_time}]</code> {ev_status}{ev_loc}\n"
        if len(events) > 5:
            msg += f"<i>... và {len(events) - 5} mốc sự kiện trước đó.</i>\n"

    return msg

def format_shopee_info(data):
    username = escape_html(data.get("username", "N/A"))
    nickname = escape_html(data.get("nickname") or username)
    user_id = escape_html(data.get("user_id", "N/A"))
    shop_id = escape_html(data.get("shop_id") or "Không có")
    phone = escape_html(data.get("phone", "Chưa liên kết"))
    email = escape_html(data.get("email", "Chưa liên kết"))
    
    phone_v = "✅ Đã xác thực" if data.get("phone_verified") else "❌ Chưa xác thực"
    email_v = "✅ Đã xác thực" if data.get("email_verified") else "❌ Chưa xác thực"
    has_pwd = "✅ Đã tạo" if data.get("has_password") else "❌ Chưa tạo"
    vouchers = data.get("voucher_count", 0)
    delivering = len(data.get("delivering_orders", []))

    msg = (
        "🛍 <b>THÔNG TIN TÀI KHOẢN SHOPEE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Tên đăng nhập:</b> <code>{username}</code>\n"
        f"📛 <b>Tên hiển thị:</b> {nickname}\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code> | Shop ID: <code>{shop_id}</code>\n"
        f"📱 <b>Số điện thoại:</b> {phone} ({phone_v})\n"
        f"✉️ <b>Email:</b> {email} ({email_v})\n"
        f"🔑 <b>Mật khẩu:</b> {has_pwd}\n"
        f"🎟 <b>Ví Voucher:</b> {vouchers} mã\n"
        f"🚚 <b>Đơn đang giao:</b> {delivering} đơn hàng\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Gõ <code>/shopee_orders</code> để xem chi tiết các đơn đang giao!</i>"
    )
    return msg

# ==================== XỬ LÝ LỆNH BOT ====================

def handle_voucher_check_flow(bot, chat_id, raw_arg):
    # Tách mã và cookie nếu có (ví dụ: NEWUSERIZ6YN cookie=...)
    custom_cookie = ""
    target_code = (raw_arg or "").strip()
    if "cookie=" in raw_arg.lower():
        m = re.search(r'cookie\s*=\s*(.+)$', raw_arg, re.IGNORECASE)
        if m:
            custom_cookie = m.group(1).strip()
            target_code = raw_arg[:m.start()].strip()
    
    target_code = target_code.split()[0] if target_code else ""
    if not target_code:
        bot.send_message(
            chat_id,
            "🔍 <b>KIỂM TRA CHI TIẾT MÃ VOUCHER SHOPEE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "👉 <b>Cú pháp:</b>\n"
            "• <code>/check_voucher &lt;MÃ_VOUCHER&gt;</code>\n"
            "• Viết tắt: <code>/cv &lt;MÃ_VOUCHER&gt;</code>\n"
            "• Hoặc nhắn: <code>check &lt;MÃ_VOUCHER&gt;</code>\n\n"
            "VD: <code>/cv NEWUSERIZ6YN</code>"
        )
        return

    active_cookie = custom_cookie or get_user_cookie(chat_id)
    if not active_cookie:
        bot.send_message(
            chat_id,
            "⚠️ <b>Bạn chưa thiết lập Cookie Shopee!</b>\n\n"
            "Để bot có thể lấy % đã dùng và hạn dùng mã, vui lòng gửi Cookie bằng lệnh:\n"
            "<code>/shopee &lt;dán_cookie_vào_đây&gt;</code>\n\n"
            "💡 <i>Chỉ cần gửi Cookie 1 lần duy nhất, sau đó bạn có thể check mã mọi lúc!</i>"
        )
        return

    bot.send_message(chat_id, f"🔍 Đang kiểm tra mã voucher <code>{escape_html(target_code)}</code>...")
    res = check_shopee_voucher(active_cookie, target_code)

    if not res.get("success"):
        bot.send_message(chat_id, f"❌ <b>Kiểm tra voucher thất bại:</b>\n{res.get('message')}")
        return

    v_info = res.get("voucher")
    msg = format_voucher_check_message(v_info)
    markup = get_check_bot_markup(getattr(bot, "username", ""))
    bot.send_message(chat_id, msg, reply_markup=markup)

def handle_command(bot, message):
    chat_id = str(message.get("chat", {}).get("id"))
    text = (message.get("text") or "").strip()
    if not text:
        return

    parts = text.split()
    cmd = parts[0].lower().split("@")[0]

    if cmd in ["/start", "/help"]:
        help_msg = (
            "🤖 <b>Chào mừng bạn đến với Express Tracking Bot!</b>\n\n"
            "Bot hỗ trợ theo dõi đơn hàng tự động từ <b>SPX</b>, <b>GHN</b>, <b>J&T</b> và <b>quản lý tài khoản Shopee</b>.\n"
            "Khi có cập nhật mới, bot sẽ <b>tự động gửi tin nhắn báo cho bạn!</b>\n\n"
            "📌 <b>CÁC LỆNH SỬ DỤNG:</b>\n\n"
            "👉 <b>1. Quản lý & Theo dõi đơn hàng:</b>\n"
            "• <code>/add SPXVN063015366786 [ghi chú]</code> (Đơn SPX)\n"
            "• <code>/add ghn GY8RA4EL 0987654321 [ghi chú]</code> (GHN + SĐT nhận)\n"
            "• <code>/add jnt 862195772225 9613 [ghi chú]</code> (J&T + 4 số cuối SĐT)\n"
            "• <code>/list</code> : Xem danh sách đơn đang theo dõi\n"
            "• <code>/check</code>: Cập nhật tình trạng tất cả đơn ngay lập tức\n"
            "• <code>/del &lt;mã_đơn&gt;</code>: Hủy theo dõi đơn hàng\n\n"
            "👉 <b>2. Tiện ích Shopee (qua Cookie):</b>\n"
            "• <code>/check_voucher &lt;mã&gt;</code> : Check thông tin, % đã dùng, hạn dùng mã (hoặc <code>/cv &lt;mã&gt;</code>)\n"
            "• <code>/voucher &lt;mã&gt;</code> : Lưu mã voucher vào ví Shopee (hỗ trợ nhiều mã)\n"
            "• <code>/shopee [cookie]</code> : Kiểm tra thông tin tài khoản Shopee\n"
            "• <code>/shopee_orders</code> : Xem các đơn Shopee đang giao & tự động import mã SPX vào theo dõi\n"
            "• <code>/shopee_del</code> : Xóa Cookie Shopee đã lưu\n"
        )
        bot.send_message(chat_id, help_msg)
        return

    if cmd in ["/add", "/track"]:
        if len(parts) < 2:
            bot.send_message(
                chat_id,
                "⚠️ <b>Cú pháp chưa đúng!</b>\n\n"
                "• SPX: <code>/add &lt;MÃ_SPX&gt; [ghi chú]</code>\n"
                "• GHN: <code>/add ghn &lt;MÃ_GHN&gt; &lt;SĐT&gt; [ghi chú]</code>\n"
                "• J&T: <code>/add jnt &lt;MÃ_JNT&gt; &lt;4 số cuối SĐT&gt; [ghi chú]</code>"
            )
            return

        carrier_arg = parts[1].upper()
        if carrier_arg in ["GHN", "JNT"]:
            carrier = carrier_arg
            if len(parts) < 4:
                bot.send_message(chat_id, f"⚠️ Đơn {carrier} yêu cầu nhập cả Mã vận đơn và SĐT/4 số cuối.\nVD: <code>/add {carrier.lower()} &lt;Mã&gt; &lt;SĐT&gt; [ghi chú]</code>")
                return
            code = parts[2]
            cred = parts[3]
            note = " ".join(parts[4:]) if len(parts) > 4 else ""
        else:
            code = parts[1]
            carrier = detect_carrier(code)
            cred = ""
            note = " ".join(parts[2:]) if len(parts) > 2 else ""

        bot.send_message(chat_id, f"🔍 Đang kiểm tra đơn hàng <code>{escape_html(code)}</code> ({carrier})...")
        res = track_order(carrier, code, cred)

        if not res.get("success"):
            bot.send_message(chat_id, f"❌ <b>Không tìm thấy đơn hàng:</b>\n{escape_html(res.get('message'))}")
            return

        # Lưu vào database theo dõi
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO tracked_orders (chat_id, carrier, tracking_number, credential, note, last_status, last_location, last_time, is_completed, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id, carrier, tracking_number) DO UPDATE SET
                    credential = excluded.credential,
                    note = excluded.note,
                    last_status = excluded.last_status,
                    last_location = excluded.last_location,
                    last_time = excluded.last_time,
                    is_completed = excluded.is_completed,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                chat_id,
                res.get("carrier"),
                res.get("tracking_number"),
                cred,
                note,
                res.get("latest_status"),
                res.get("latest_location"),
                res.get("latest_time"),
                1 if res.get("is_completed") else 0
            ))

        msg = format_tracking_result(res, note)
        msg += "\n🔔 <i>Đã thêm vào danh sách theo dõi. Bot sẽ tự động báo khi có hành trình mới!</i>"
        bot.send_message(chat_id, msg)
        return

    if cmd == "/list":
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("""
                SELECT id, carrier, tracking_number, note, last_status, last_time, is_completed 
                FROM tracked_orders 
                WHERE chat_id = ? 
                ORDER BY is_completed ASC, id DESC
            """, (chat_id,))
            rows = c.fetchall()

        if not rows:
            bot.send_message(chat_id, "📭 Bạn chưa theo dõi đơn hàng nào.\nDùng <code>/add &lt;mã_vận_đơn&gt;</code> để thêm đơn mới!")
            return

        msg = f"📋 <b>DANH SÁCH ĐƠN ĐANG THEO DÕI ({len(rows)} đơn):</b>\n\n"
        for r in rows:
            icon = "✅" if r[6] else "🚚"
            note_str = f" ({escape_html(r[3])})" if r[3] else ""
            status_text = escape_html(r[4]) if r[4] else "Chưa cập nhật"
            time_text = escape_html(r[5]) if r[5] else "-"
            msg += f"{icon} <b>{escape_html(r[1])}</b>: <code>{escape_html(r[2])}</code>{note_str}\n"
            msg += f"   • Trạng thái: {status_text}\n"
            msg += f"   • Cập nhật: <i>{time_text}</i>\n\n"

        msg += "💡 <i>Gõ <code>/check</code> để cập nhật tất cả hoặc <code>/del &lt;mã&gt;</code> để xóa.</i>"
        bot.send_message(chat_id, msg)
        return

    if cmd in ["/del", "/remove"]:
        if len(parts) < 2:
            bot.send_message(chat_id, "⚠️ Vui lòng nhập mã đơn cần xóa. VD: <code>/del SPXVN063015366786</code>")
            return
        code = parts[1].strip().upper()
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM tracked_orders WHERE chat_id = ? AND tracking_number = ?", (chat_id, code))
            deleted = c.rowcount

        if deleted > 0:
            bot.send_message(chat_id, f"🗑 Đã hủy theo dõi đơn hàng <code>{escape_html(code)}</code>.")
        else:
            bot.send_message(chat_id, f"⚠️ Không tìm thấy đơn hàng <code>{escape_html(code)}</code> trong danh sách của bạn.")
        return

    if cmd in ["/check_voucher", "/checkvoucher", "/cv", "/check_vc", "/vc"] or (cmd == "/check" and len(parts) > 1 and not parts[1].startswith("-")):
        code_arg = text[len(parts[0]):].strip()
        handle_voucher_check_flow(bot, chat_id, code_arg)
        return

    if cmd == "/check":
        bot.send_message(chat_id, "🔄 Đang cập nhật trạng thái các đơn hàng của bạn...")
        check_user_orders(bot, chat_id)
        return

    if cmd in ["/shopee", "/shopee_info"]:
        cookie = ""
        if len(parts) > 1:
            cookie = text[len(parts[0]):].strip()
        else:
            cookie = get_user_cookie(chat_id)

        if not cookie:
            bot.send_message(
                chat_id,
                "⚠️ <b>Bạn chưa nhập Cookie Shopee!</b>\n\n"
                "👉 <b>Cách sử dụng:</b>\n"
                "Gõ: <code>/shopee &lt;dán_cookie_vào_đây&gt;</code>\n\n"
                "<i>Bot sẽ tự động lưu cookie này cho bạn để các lần sau chỉ cần gõ <code>/shopee</code> hoặc <code>/shopee_orders</code> là xong!</i>\n\n"
                "💡 <i>Cách lấy Cookie: Vào shopee.vn trên trình duyệt máy tính -> bấm F12 -> tab Network -> bấm 1 yêu cầu bất kỳ -> tìm dòng Cookie và copy.</i>"
            )
            return

        bot.send_message(chat_id, "🔍 Đang kiểm tra tài khoản Shopee qua Cookie...")
        res = check_shopee_account(cookie)
        if not res.get("success"):
            bot.send_message(chat_id, f"❌ <b>Kiểm tra thất bại:</b>\n{escape_html(res.get('message'))}")
            return

        # Lưu cookie cho user
        set_user_cookie(chat_id, cookie)
        msg = format_shopee_info(res)
        bot.send_message(chat_id, msg)
        return

    if cmd in ["/shopee_orders", "/shopee_sync"]:
        cookie = get_user_cookie(chat_id)
        if not cookie:
            bot.send_message(chat_id, "⚠️ Bạn chưa lưu Cookie Shopee. Hãy gõ <code>/shopee &lt;cookie&gt;</code> trước.")
            return

        bot.send_message(chat_id, "🔍 Đang đồng bộ đơn hàng Shopee đang giao của bạn...")
        res = get_shopee_delivering_orders(cookie)
        if not res.get("success"):
            bot.send_message(chat_id, f"❌ Lỗi: {escape_html(res.get('message'))}")
            return

        orders = res.get("orders", [])
        if not orders:
            bot.send_message(chat_id, "📭 Hiện tài khoản Shopee của bạn không có đơn hàng nào đang trong trạng thái giao hàng.")
            return

        msg = f"🚚 <b>TÌM THẤY {len(orders)} ĐƠN HÀNG ĐANG GIAO:</b>\n\n"
        added_count = 0

        with get_db_connection() as conn:
            c = conn.cursor()
            for idx, o in enumerate(orders, 1):
                raw_items = o.get("items", [])
                items_str = ", ".join(raw_items[:2])
                if len(raw_items) > 2:
                    items_str += f" (+{len(raw_items) - 2} sản phẩm khác)"
                
                tn = o.get("tracking_number", "")
                carrier_name = o.get("carrier_name", "Shopee Express")
                
                msg += f"<b>{idx}. {escape_html(items_str)}</b>\n"
                msg += f"   • Đơn vị vận chuyển: {escape_html(carrier_name)}\n"
                if tn:
                    msg += f"   • Mã vận đơn: <code>{escape_html(tn)}</code>\n"
                    carrier_code = "SPX" if "SPX" in carrier_name.upper() or tn.startswith("SPX") or tn.startswith("VN") else carrier_name
                    c.execute("""
                        INSERT INTO tracked_orders (chat_id, carrier, tracking_number, note, last_status, is_completed, updated_at)
                        VALUES (?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
                        ON CONFLICT(chat_id, carrier, tracking_number) DO NOTHING
                    """, (chat_id, carrier_code, tn, items_str[:40], "Đang giao"))
                    added_count += 1
                else:
                    msg += "   • Mã vận đơn: <i>(Chờ cập nhật mã)</i>\n"
                msg += "\n"

        if added_count > 0:
            msg += f"🎉 <b>Đã tự động thêm {added_count} mã vận đơn vào danh sách theo dõi của bot!</b>\n"
            msg += "Bot sẽ tự động báo khi bưu tá đi giao hoặc đơn đến nơi. Gõ <code>/list</code> để xem."
        bot.send_message(chat_id, msg)
        return

    if cmd == "/shopee_del":
        delete_user_cookie(chat_id)
        bot.send_message(chat_id, "🗑 Đã xóa Cookie Shopee của bạn khỏi bot.")
        return

    if cmd in ["/voucher", "/vouchers", "/add_voucher", "/shopee_voucher", "/luu_voucher"]:
        raw_args = text[len(parts[0]):].strip()
        cookie = get_user_cookie(chat_id)

        if not raw_args:
            cookie_status = "✅ <i>Đã kết nối Cookie</i>" if cookie else "⚠️ <i>Chưa có Cookie (dùng <code>/shopee &lt;cookie&gt;</code> để lưu)</i>"
            bot.send_message(
                chat_id,
                "🎟 <b>THÊM MÃ VOUCHER SHOPEE VÀO VÍ</b>\n"
                f"Trạng thái: {cookie_status}\n\n"
                "👉 <b>Cách sử dụng:</b>\n"
                "• <b>Lưu 1 mã:</b> <code>/voucher &lt;MÃ_VOUCHER&gt;</code>\n"
                "  VD: <code>/voucher AFFBF310</code>\n\n"
                "• <b>Lưu nhiều mã cùng lúc:</b>\n"
                "  <code>/voucher MÃ1 MÃ2 MÃ3</code>\n"
                "  (hoặc phân cách bằng dấu phẩy, xuống dòng)\n\n"
                "• <b>Dùng Cookie trực tiếp:</b>\n"
                "  <code>/voucher &lt;mã&gt; cookie=&lt;chuỗi_cookie&gt;</code>\n\n"
                "💡 <i>Gợi ý: Chỉ cần gửi cookie cho bot 1 lần qua <code>/shopee &lt;cookie&gt;</code>, sau đó bạn chỉ cần gõ <code>/voucher &lt;mã&gt;</code> để lưu mã vào ví bất kỳ lúc nào!</i>"
            )
            return

        # Kiểm tra nếu user truyền cookie trực tiếp trong lệnh
        custom_cookie = ""
        voucher_text = raw_args
        if "cookie=" in raw_args.lower():
            m = re.search(r'cookie\s*=\s*(.+)$', raw_args, re.IGNORECASE)
            if m:
                custom_cookie = m.group(1).strip()
                voucher_text = raw_args[:m.start()].strip()

        active_cookie = custom_cookie or cookie
        if not active_cookie:
            bot.send_message(
                chat_id,
                "⚠️ <b>Bạn chưa thiết lập Cookie Shopee!</b>\n\n"
                "Vui lòng lưu Cookie trước bằng lệnh:\n"
                "<code>/shopee &lt;dán_cookie_vào_đây&gt;</code>\n\n"
                "Hoặc truyền kèm cookie: <code>/voucher &lt;mã&gt; cookie=&lt;cookie&gt;</code>"
            )
            return

        if not voucher_text:
            bot.send_message(chat_id, "⚠️ Vui lòng nhập mã voucher cần lưu. VD: <code>/voucher SHOPEE10K</code>")
            return

        bot.send_message(chat_id, "⏳ <b>Đang gửi yêu cầu lưu mã voucher vào ví Shopee...</b>")
        res = save_shopee_vouchers(active_cookie, voucher_text)
        results = res.get("results", [])
        total = res.get("total", 0)
        success_count = res.get("success_count", 0)

        msg = f"🎟 <b>KẾT QUẢ LƯU VOUCHER SHOPEE ({success_count}/{total} thành công):</b>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        for r in results:
            c = escape_html(r.get("code", ""))
            ok = r.get("success")
            m_desc = escape_html(r.get("message", ""))
            if ok:
                msg += f"✅ <code>{c}</code>: <b>Lưu thành công!</b>\n"
            else:
                msg += f"❌ <code>{c}</code>: <i>{m_desc}</i>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"
        if success_count > 0:
            msg += "🎉 <i>Các mã hợp lệ đã được lưu vào ví Shopee của bạn. Dùng <code>/shopee</code> để xem lại ví!</i>"
        else:
            msg += "💡 <i>Vui lòng kiểm tra lại mã hoặc trạng thái Cookie (hết hạn / cần đăng nhập lại).</i>"

        bot.send_message(chat_id, msg)
        return

    # Nếu người dùng gửi tin nhắn trực tiếp không bắt đầu bằng "/"
    if not text.startswith("/"):
        lower_text = text.lower().strip()
        # Vẫn hỗ trợ cú pháp check voucher: check <mã> hoặc cv <mã>
        if lower_text.startswith("check ") or lower_text.startswith("cv "):
            sub_parts = text.split(None, 1)
            target_code = sub_parts[1].strip() if len(sub_parts) > 1 else ""
            if target_code.lower().startswith("voucher "):
                target_code = target_code[8:].strip()
            if target_code:
                handle_voucher_check_flow(bot, chat_id, target_code)
                return

        # Đã loại bỏ tính năng tra cứu nhanh tự động: Hướng dẫn người dùng dùng lệnh /add để theo dõi
        raw_code = text.strip()
        bot.send_message(
            chat_id,
            "💡 <b>Hướng dẫn theo dõi đơn hàng:</b>\n\n"
            "Tính năng tra cứu nhanh đã được tắt. Để thêm đơn vào hệ thống theo dõi tự động, bạn vui lòng sử dụng lệnh:\n\n"
            f"• SPX: <code>/add {escape_html(raw_code)}</code>\n"
            "• GHN: <code>/add ghn &lt;Mã&gt; &lt;SĐT_nhận&gt;</code>\n"
            "• J&T: <code>/add jnt &lt;Mã&gt; &lt;4_số_cuối_SĐT&gt;</code>\n\n"
            "👉 Gõ <code>/help</code> để xem chi tiết danh sách tất cả các lệnh."
        )
        return

def check_user_orders(bot, target_chat_id):
    """Cập nhật trạng thái tất cả các đơn của user theo yêu cầu thủ công"""
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, carrier, tracking_number, credential, note, last_status, last_time, is_completed 
            FROM tracked_orders 
            WHERE chat_id = ? 
            ORDER BY is_completed ASC, id DESC
        """, (str(target_chat_id),))
        rows = c.fetchall()

    if not rows:
        bot.send_message(target_chat_id, "📭 Bạn chưa theo dõi đơn hàng nào.\nDùng <code>/add &lt;mã_vận_đơn&gt;</code> để thêm đơn mới!")
        return

    count_updated = 0
    with get_db_connection() as conn:
        c = conn.cursor()
        for r in rows:
            order_id, carrier, code, cred, note, old_status, old_time, is_done = r
            try:
                res = track_order(carrier, code, cred)
                if not res.get("success"):
                    continue

                new_status = res.get("latest_status", "")
                new_time = res.get("latest_time", "")
                is_completed = 1 if res.get("is_completed") else 0

                if new_status != old_status or new_time != old_time or is_completed != is_done:
                    count_updated += 1
                    c.execute("""
                        UPDATE tracked_orders
                        SET last_status = ?, last_location = ?, last_time = ?, is_completed = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (new_status, res.get("latest_location"), new_time, is_completed, order_id))

                    msg = f"🔔 <b>CẬP NHẬT MỚI CHO ĐƠN HÀNG:</b>\n\n" + format_tracking_result(res, note)
                    bot.send_message(target_chat_id, msg)
                    time.sleep(0.5)
            except Exception as e:
                print(f"Lỗi kiểm tra đơn {code}: {e}")

    if count_updated == 0:
        bot.send_message(target_chat_id, "✅ Tất cả các đơn hàng đều đã ở trạng thái mới nhất, chưa có cập nhật hành trình mới.")

# ==================== LUỒNG KIỂM TRA ĐỊNH KỲ ====================

def auto_tracking_worker(bot):
    """Chạy ngầm định kỳ kiểm tra các đơn chưa hoàn thành và báo Telegram"""
    while True:
        try:
            time.sleep(CHECK_INTERVAL_SECONDS)
            with get_db_connection() as conn:
                c = conn.cursor()
                c.execute("""
                    SELECT id, chat_id, carrier, tracking_number, credential, note, last_status, last_time 
                    FROM tracked_orders 
                    WHERE is_completed = 0
                """)
                orders = c.fetchall()

            if not orders:
                continue

            with get_db_connection() as conn:
                c = conn.cursor()
                for item in orders:
                    order_id, chat_id, carrier, code, cred, note, old_status, old_time = item
                    try:
                        res = track_order(carrier, code, cred)
                        if not res.get("success"):
                            continue

                        new_status = res.get("latest_status", "")
                        new_time = res.get("latest_time", "")
                        is_completed = 1 if res.get("is_completed") else 0

                        if new_status != old_status or new_time != old_time:
                            c.execute("""
                                UPDATE tracked_orders
                                SET last_status = ?, last_location = ?, last_time = ?, is_completed = ?, updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (new_status, res.get("latest_location"), new_time, is_completed, order_id))

                            msg = "🔔 <b>THÔNG BÁO VẬN ĐƠN CÓ HÀNH TRÌNH MỚI!</b>\n\n" + format_tracking_result(res, note)
                            bot.send_message(chat_id, msg)
                            time.sleep(1)
                    except Exception as e:
                        print(f"Lỗi kiểm tra đơn {code}: {e}")

        except Exception as e:
            print(f"Lỗi worker: {e}")
            time.sleep(10)

# ==================== MAIN ====================

def main():
    print("=" * 60)
    print("      EXPRESS TRACKING BOT - TELEGRAM (BẢN TỐI ƯU)")
    print("      Chạy trực tiếp 100% bằng Python - Không cần MySQL")
    print("=" * 60)

    token = get_env_token()
    if not token:
        print("\n[CHÚ Ý] Chưa tìm thấy Telegram Bot Token trong file .env!")
        print("Nếu chưa có token, hãy mở Telegram tìm @BotFather, gửi lệnh /newbot để tạo bot và lấy token.")
        token = input("👉 Hãy dán TELEGRAM_BOT_TOKEN của bạn vào đây: ").strip()
        if not token:
            print("Token không hợp lệ. Đang thoát.")
            sys.exit(1)
        save_env_token(token)
        print("✅ Đã lưu token vào .env thành công!\n")

    bot = TelegramBot(token)
    print("[*] Đang kết nối tới Telegram API...")
    me = bot.get_me()
    if not me.get("ok"):
        print(f"\n❌ LỖI KẾT NỐI TELEGRAM: {me.get('description')}")
        print("Vui lòng kiểm tra lại Token của bạn.")
        input("\nNhấn Enter để thoát...")
        sys.exit(1)

    bot_info = me.get("result", {})
    bot_name = bot_info.get("first_name", "")
    bot_username = bot_info.get("username", "")
    bot.username = bot_username

    print(f"\n✅ KẾT NỐI THÀNH CÔNG!")
    print(f"🤖 Tên Bot: {bot_name}")
    print(f"🔗 Username: @{bot_username}")
    print(f"📱 Bạn có thể mở Telegram và nhắn tin cho @{bot_username}")
    print(f"⚙️ Tự động quét cập nhật đơn: {CHECK_INTERVAL_SECONDS // 60} phút/lần")
    print("\n[ĐANG CHẠY...] Nhấn Ctrl + C để dừng bot.\n")

    # Khởi động thread chạy ngầm kiểm tra đơn hàng
    t = threading.Thread(target=auto_tracking_worker, args=(bot,), daemon=True)
    t.start()

    # Long polling loop
    offset = None
    while True:
        try:
            updates = bot.get_updates(offset=offset, timeout=30)
            if not updates.get("ok"):
                time.sleep(3)
                continue

            for update in updates.get("result", []):
                offset = update["update_id"] + 1
                if "message" in update:
                    try:
                        handle_command(bot, update["message"])
                    except Exception as e:
                        print(f"Lỗi xử lý tin nhắn: {e}")

        except KeyboardInterrupt:
            print("\nĐã dừng bot.")
            break
        except Exception as e:
            time.sleep(3)

if __name__ == "__main__":
    main()
