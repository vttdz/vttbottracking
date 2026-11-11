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

def get_env_val(key, default=""):
    if not os.path.exists(ENV_FILE):
        return default
    with open(ENV_FILE, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
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
        with open(ENV_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        
        for i, line in enumerate(lines):
            if line.strip().startswith("TELEGRAM_BOT_TOKEN="):
                lines[i] = f"TELEGRAM_BOT_TOKEN={token}\n"
                found = True
                break
    
    if not found:
        lines.append(f"\nTELEGRAM_BOT_TOKEN={token}\n")

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)

# ==================== DATABASE SQLITE ====================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
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
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            chat_id TEXT PRIMARY KEY,
            shopee_cookie TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_user_cookie(chat_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT shopee_cookie FROM user_settings WHERE chat_id = ?", (chat_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row and row[0] else ""

def set_user_cookie(chat_id, cookie):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO user_settings (chat_id, shopee_cookie, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(chat_id) DO UPDATE SET
            shopee_cookie = excluded.shopee_cookie,
            updated_at = CURRENT_TIMESTAMP
    """, (chat_id, cookie))
    conn.commit()
    conn.close()

def delete_user_cookie(chat_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM user_settings WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

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
            "User-Agent": "TelegramBot/1.0"
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

# ==================== FORMATTER TIN NHẮN ====================

def format_tracking_result(res, note=""):
    carrier = res.get("carrier", "Vận chuyển")
    tn = res.get("tracking_number", "")
    status = res.get("latest_status", "Đang cập nhật")
    t = res.get("latest_time", "")
    loc = res.get("latest_location", "")
    is_done = res.get("is_completed", False)

    status_icon = "✅" if is_done else "🚚"

    msg = f"📦 <b>{carrier}</b> | <code>{tn}</code>\n"
    if note:
        msg += f"📝 Ghi chú: <i>{note}</i>\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"{status_icon} <b>Trạng thái:</b> {status}\n"
    if loc:
        msg += f"📍 <b>Vị trí:</b> {loc}\n"
    if t:
        msg += f"⏰ <b>Thời gian:</b> {t}\n"
    
    events = res.get("events", [])
    if events:
        msg += f"\n📜 <b>Lịch sử hành trình:</b>\n"
        for ev in events[:5]:  # Hiển thị 5 sự kiện gần nhất
            ev_loc = f" ({ev['location']})" if ev.get("location") else ""
            msg += f"• <code>[{ev.get('time')}]</code> {ev.get('status')}{ev_loc}\n"
        if len(events) > 5:
            msg += f"<i>... và {len(events) - 5} mốc sự kiện trước đó.</i>\n"

    return msg

def format_shopee_info(data):
    username = data.get("username", "N/A")
    nickname = data.get("nickname") or username
    user_id = data.get("user_id", "N/A")
    shop_id = data.get("shop_id") or "Không có"
    phone = data.get("phone", "Chưa liên kết")
    email = data.get("email", "Chưa liên kết")
    
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
            "• Hoặc nhắn trực tiếp: <code>check &lt;MÃ_VOUCHER&gt;</code>\n\n"
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

    bot.send_message(chat_id, f"🔍 Đang kiểm tra mã voucher <code>{target_code}</code>...")
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
            "👉 <b>1. Tra cứu & Theo dõi đơn hàng:</b>\n"
            "• <code>/add SPXVN063015366786 [ghi chú]</code> (Đơn SPX)\n"
            "• <code>/add ghn GY8RA4EL 0987654321 [ghi chú]</code> (GHN + SĐT nhận)\n"
            "• <code>/add jnt 862195772225 9613 [ghi chú]</code> (J&T + 4 số cuối SĐT)\n"
            "<i>(Mẹo: Bạn chỉ cần gửi mã vận đơn vào chat, bot sẽ tự nhận diện tra cứu)</i>\n\n"
            "👉 <b>2. Quản lý danh sách đơn:</b>\n"
            "• <code>/list</code> : Xem danh sách đơn đang theo dõi\n"
            "• <code>/check</code>: Cập nhật tình trạng tất cả đơn ngay lập tức\n"
            "• <code>/del &lt;mã_đơn&gt;</code>: Hủy theo dõi đơn hàng\n\n"
            "👉 <b>3. Tiện ích Shopee (qua Cookie):</b>\n"
            "• <code>/check_voucher &lt;mã&gt;</code> : Check thông tin, % đã dùng, hạn dùng mã (hoặc <code>/cv &lt;mã&gt;</code>)\n"
            "• <code>/voucher &lt;mã&gt;</code> : Lưu mã voucher vào ví Shopee (hỗ trợ nhập nhiều mã)\n"
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

        bot.send_message(chat_id, f"🔍 Đang kiểm tra đơn hàng <code>{code}</code>...")
        res = track_order(carrier, code, cred)

        if not res.get("success"):
            bot.send_message(chat_id, f"❌ <b>Không tìm thấy đơn hàng:</b>\n{res.get('message')}")
            return

        # Lưu vào database theo dõi
        conn = sqlite3.connect(DB_FILE)
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
        conn.commit()
        conn.close()

        msg = format_tracking_result(res, note)
        msg += "\n🔔 <i>Đã thêm vào danh sách theo dõi. Bot sẽ tự động báo khi có hành trình mới!</i>"
        bot.send_message(chat_id, msg)
        return

    if cmd == "/list":
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT id, carrier, tracking_number, note, last_status, last_time, is_completed FROM tracked_orders WHERE chat_id = ? ORDER BY id DESC", (chat_id,))
        rows = c.fetchall()
        conn.close()

        if not rows:
            bot.send_message(chat_id, "📭 Bạn chưa theo dõi đơn hàng nào.\nDùng <code>/add &lt;mã_vận_đơn&gt;</code> để thêm đơn mới!")
            return

        msg = f"📋 <b>DANH SÁCH ĐƠN ĐANG THEO DÕI ({len(rows)} đơn):</b>\n\n"
        for r in rows:
            icon = "✅" if r[6] else "🚚"
            note_str = f" ({r[3]})" if r[3] else ""
            msg += f"{icon} <b>{r[1]}</b>: <code>{r[2]}</code>{note_str}\n"
            msg += f"   • Trạng thái: {r[4] or 'Chưa cập nhật'}\n"
            msg += f"   • Cập nhật: <i>{r[5] or '-'}</i>\n\n"

        msg += "💡 <i>Gõ <code>/check</code> để cập nhật tất cả hoặc <code>/del &lt;mã&gt;</code> để xóa.</i>"
        bot.send_message(chat_id, msg)
        return

    if cmd in ["/del", "/remove"]:
        if len(parts) < 2:
            bot.send_message(chat_id, "⚠️ Vui lòng nhập mã đơn cần xóa. VD: <code>/del SPXVN063015366786</code>")
            return
        code = parts[1].strip().upper()
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM tracked_orders WHERE chat_id = ? AND tracking_number = ?", (chat_id, code))
        deleted = c.rowcount
        conn.commit()
        conn.close()

        if deleted > 0:
            bot.send_message(chat_id, f"🗑 Đã hủy theo dõi đơn hàng <code>{code}</code>.")
        else:
            bot.send_message(chat_id, f"⚠️ Không tìm thấy đơn hàng <code>{code}</code> trong danh sách của bạn.")
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
        # Kiểm tra xem user có nhập cookie kèm theo không
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
            bot.send_message(chat_id, f"❌ <b>Kiểm tra thất bại:</b>\n{res.get('message')}")
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
            bot.send_message(chat_id, f"❌ Lỗi: {res.get('message')}")
            return

        orders = res.get("orders", [])
        if not orders:
            bot.send_message(chat_id, "📭 Hiện tài khoản Shopee của bạn không có đơn hàng nào đang trong trạng thái giao hàng.")
            return

        msg = f"🚚 <b>TÌM THẤY {len(orders)} ĐƠN HÀNG ĐANG GIAO:</b>\n\n"
        added_count = 0
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        for idx, o in enumerate(orders, 1):
            items_str = ", ".join(o.get("items", [])[:2])
            if len(o.get("items", [])) > 2:
                items_str += f" (+{len(o.get('items')) - 2} sản phẩm khác)"
            
            tn = o.get("tracking_number", "")
            carrier_name = o.get("carrier_name", "Shopee Express")
            
            msg += f"<b>{idx}. {items_str}</b>\n"
            msg += f"   • Đơn vị vận chuyển: {carrier_name}\n"
            if tn:
                msg += f"   • Mã vận đơn: <code>{tn}</code>\n"
                carrier_code = "SPX" if "SPX" in carrier_name.upper() or tn.startswith("SPX") or tn.startswith("VN") else carrier_name
                c.execute("""
                    INSERT INTO tracked_orders (chat_id, carrier, tracking_number, note, last_status, is_completed, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
                    ON CONFLICT(chat_id, carrier, tracking_number) DO NOTHING
                """, (chat_id, carrier_code, tn, items_str[:40], "Đang giao"))
                added_count += 1
            else:
                msg += f"   • Mã vận đơn: <i>(Chờ cập nhật mã)</i>\n"
            msg += "\n"

        conn.commit()
        conn.close()

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
            c = r.get("code")
            ok = r.get("success")
            m_desc = r.get("message", "")
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

    # Nếu gửi trực tiếp tin nhắn (không có dấu /)
    if not text.startswith("/"):
        lower_text = text.lower().strip()
        # Kiểm tra nếu là cú pháp check voucher: check <mã> hoặc cv <mã>
        if lower_text.startswith("check ") or lower_text.startswith("cv "):
            sub_parts = text.split(None, 1)
            target_code = sub_parts[1].strip() if len(sub_parts) > 1 else ""
            if target_code.lower().startswith("voucher "):
                target_code = target_code[8:].strip()
            if target_code:
                handle_voucher_check_flow(bot, chat_id, target_code)
                return

        code = text.strip().upper()
        carrier = detect_carrier(code)
        bot.send_message(chat_id, f"🔍 Đang tra cứu nhanh đơn <code>{code}</code> ({carrier})...")
        res = track_order(carrier, code, "")
        if res.get("success"):
            msg = format_tracking_result(res)
            msg += f"\n💡 <i>Muốn nhận thông báo tự động cho đơn này? Hãy gõ:</i>\n<code>/add {code}</code>"
            bot.send_message(chat_id, msg)
        else:
            bot.send_message(chat_id, f"❌ Không tìm thấy thông tin đơn hàng <code>{code}</code>.\n{res.get('message')}")

def check_user_orders(bot, target_chat_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, carrier, tracking_number, credential, note, last_status, last_time, is_completed FROM tracked_orders WHERE chat_id = ?", (target_chat_id,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        bot.send_message(target_chat_id, "Bạn chưa lưu đơn hàng nào.")
        return

    count_updated = 0
    for r in rows:
        order_id, carrier, code, cred, note, old_status, old_time, is_done = r
        res = track_order(carrier, code, cred)
        if not res.get("success"):
            continue

        new_status = res.get("latest_status", "")
        new_time = res.get("latest_time", "")
        is_completed = 1 if res.get("is_completed") else 0

        if new_status != old_status or new_time != old_time or is_completed != is_done:
            count_updated += 1
            # Update DB
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("""
                UPDATE tracked_orders
                SET last_status = ?, last_location = ?, last_time = ?, is_completed = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_status, res.get("latest_location"), new_time, is_completed, order_id))
            conn.commit()
            conn.close()

            # Báo cho người dùng
            msg = f"🔔 <b>CẬP NHẬT MỚI CHO ĐƠN HÀNG:</b>\n\n" + format_tracking_result(res, note)
            bot.send_message(target_chat_id, msg)

    if count_updated == 0:
        bot.send_message(target_chat_id, "✅ Tất cả các đơn hàng đều đã ở trạng thái mới nhất, chưa có cập nhật hành trình mới.")

# ==================== LUỒNG KIỂM TRA ĐỊNH KỲ ====================

def auto_tracking_worker(bot):
    """Chạy ngầm định kỳ kiểm tra các đơn chưa hoàn thành và báo Telegram"""
    while True:
        try:
            time.sleep(CHECK_INTERVAL_SECONDS)
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT id, chat_id, carrier, tracking_number, credential, note, last_status, last_time FROM tracked_orders WHERE is_completed = 0")
            orders = c.fetchall()
            conn.close()

            for item in orders:
                order_id, chat_id, carrier, code, cred, old_status, old_time = item
                try:
                    res = track_order(carrier, code, cred)
                    if not res.get("success"):
                        continue

                    new_status = res.get("latest_status", "")
                    new_time = res.get("latest_time", "")
                    is_completed = 1 if res.get("is_completed") else 0

                    if new_status != old_status or new_time != old_time:
                        # Có sự kiện mới! Cập nhật database
                        conn = sqlite3.connect(DB_FILE)
                        c = conn.cursor()
                        c.execute("""
                            UPDATE tracked_orders
                            SET last_status = ?, last_location = ?, last_time = ?, is_completed = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (new_status, res.get("latest_location"), new_time, is_completed, order_id))
                        conn.commit()
                        conn.close()

                        # Gửi thông báo đến Telegram
                        msg = "🔔 <b>THÔNG BÁO VẬN ĐƠN CÓ HÀNH TRÌNH MỚI!</b>\n\n" + format_tracking_result(res, item[5])
                        bot.send_message(chat_id, msg)
                        time.sleep(1)
                except Exception as e:
                    print(f"Lỗi kiểm tra đơn {code}: {e}")

        except Exception as e:
            print(f"Lỗi worker: {e}")

# ==================== MAIN ====================

def main():
    print("=" * 60)
    print("      EXPRESS TRACKING BOT - TELEGRAM (BẢN RÚT GỌN)")
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
            # print(f"Lỗi mạng: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()

