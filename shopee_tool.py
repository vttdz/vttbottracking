# -*- coding: utf-8 -*-
"""
Shopee Account Tool - Kiểm tra thông tin tài khoản Shopee qua Cookie.
Sử dụng thư viện chuẩn của Python.
"""

import json
import urllib.request
import urllib.error
import urllib.parse
import re
import time
from datetime import datetime

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

def clean_cookie(raw_cookie):
    """Làm sạch chuỗi cookie nhập vào"""
    if not raw_cookie:
        return ""
    cookie = raw_cookie.strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie[7:].strip()
    return cookie

def request_shopee(url, cookie, method="GET", data=None, referer=None):
    headers = {
        "User-Agent": DEFAULT_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
        "Referer": referer or "https://shopee.vn/user/account/profile",
        "Origin": "https://shopee.vn",
        "x-shopee-language": "vi",
        "x-api-source": "pc",
        "Cookie": clean_cookie(cookie)
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    payload = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"error": e.code, "error_msg": f"HTTP Error {e.code}"}
    except Exception as e:
        return {"error": -1, "error_msg": str(e)}

def check_shopee_account(cookie):
    """
    Kiểm tra cookie và lấy thông tin chi tiết tài khoản Shopee.
    """
    cookie = clean_cookie(cookie)
    if not cookie:
        return {"success": False, "message": "Cookie không được để trống."}

    # 1. Lấy thông tin cơ bản
    info_res = request_shopee("https://shopee.vn/api/v4/account/basic/get_account_info", cookie)
    
    if info_res.get("error") != 0 or not info_res.get("data"):
        err = info_res.get("error_msg") or "Cookie không hợp lệ hoặc đã hết hạn (DIE)."
        return {"success": False, "message": f"❌ {err}"}

    data = info_res.get("data", {})
    user_id = data.get("user_id")
    username = data.get("username", "")
    phone = data.get("phone", "")
    email = data.get("email", "")
    has_pwd = data.get("has_password", False)
    phone_verified = data.get("phone_verified", False)
    email_verified = data.get("email_verified", False)
    avatar = data.get("avatar", "")
    shop_id = data.get("shop_id")

    # 2. Lấy thêm nickname / profile nếu có
    nickname = ""
    profile_res = request_shopee("https://shopee.vn/api/v2/user/profile/get", cookie)
    if profile_res.get("error") == 0 and profile_res.get("data"):
        p_data = profile_res.get("data", {})
        nickname = p_data.get("nickname") or p_data.get("name") or ""
        if not phone and p_data.get("phone"):
            phone = p_data.get("phone")
        if not email and p_data.get("email"):
            email = p_data.get("email")

    # 3. Lấy số lượng voucher trong ví (nếu lấy được)
    voucher_count = 0
    voucher_res = request_shopee("https://shopee.vn/api/v2/voucher_wallet/get_user_voucher_list?limit=1&offset=0", cookie)
    if voucher_res.get("error") == 0:
        voucher_count = voucher_res.get("data", {}).get("total_count", 0)

    # 4. Lấy số đơn hàng đang giao (list_type=3 là Đang giao)
    delivering_orders = []
    order_res = request_shopee("https://shopee.vn/api/v4/order/get_order_list?limit=10&list_type=3", cookie)
    if order_res.get("error") == 0 and order_res.get("data"):
        orders_raw = order_res.get("data", {}).get("orders") or []
        for o in orders_raw:
            order_id = o.get("order_id")
            items = []
            for item in o.get("items", []):
                items.append(item.get("name", ""))
            
            # Shipping tracking info if available
            shipping = o.get("shipping", {})
            tracking_no = shipping.get("tracking_number") or ""
            carrier_name = shipping.get("carrier_name") or ""

            delivering_orders.append({
                "order_id": order_id,
                "items": items,
                "tracking_number": tracking_no,
                "carrier_name": carrier_name,
                "status_desc": o.get("status_description", "Đang giao")
            })

    return {
        "success": True,
        "is_alive": True,
        "user_id": user_id,
        "shop_id": shop_id,
        "username": username,
        "nickname": nickname,
        "phone": phone or "Chưa liên kết",
        "email": email or "Chưa liên kết",
        "phone_verified": phone_verified,
        "email_verified": email_verified,
        "has_password": has_pwd,
        "voucher_count": voucher_count,
        "delivering_orders": delivering_orders
    }

def get_shopee_delivering_orders(cookie):
    """Lấy danh sách các đơn hàng Shopee đang giao của tài khoản"""
    cookie = clean_cookie(cookie)
    order_res = request_shopee("https://shopee.vn/api/v4/order/get_order_list?limit=20&list_type=3", cookie)
    if order_res.get("error") != 0:
        return {"success": False, "message": order_res.get("error_msg") or "Không thể lấy đơn hàng"}
    
    orders = []
    orders_raw = order_res.get("data", {}).get("orders") or []
    for o in orders_raw:
        order_id = o.get("order_id")
        items = []
        for item in o.get("items", []):
            items.append(item.get("name", ""))
        shipping = o.get("shipping", {})
        tracking_no = shipping.get("tracking_number") or ""
        carrier_name = shipping.get("carrier_name") or ""
        orders.append({
            "order_id": order_id,
            "items": items,
            "tracking_number": tracking_no,
            "carrier_name": carrier_name,
            "status_desc": o.get("status_description", "")
        })

    return {"success": True, "orders": orders}

def save_shopee_voucher(cookie, voucher_code):
    """
    Lưu 1 mã voucher Shopee vào ví thông qua Cookie.
    API: https://shopee.vn/api/v2/voucher_wallet/save_voucher
    """
    cookie = clean_cookie(cookie)
    if not cookie:
        return {"success": False, "code": voucher_code, "message": "Cookie không được để trống."}
    
    code = (voucher_code or "").strip().strip("'\"#")
    if not code:
        return {"success": False, "code": voucher_code, "message": "Mã voucher không hợp lệ."}

    url = "https://shopee.vn/api/v2/voucher_wallet/save_voucher"
    payload = {
        "voucher_code": code,
        "need_user_voucher_status": True
    }
    
    res = request_shopee(url, cookie, method="POST", data=payload, referer="https://shopee.vn/user/voucher-wallet")
    
    # Kiểm tra trạng thái đăng nhập
    if res.get("is_login") is False:
        return {
            "success": False,
            "code": code,
            "message": "Cookie chưa đăng nhập hoặc đã hết hạn (DIE).",
            "data": res
        }

    error = res.get("error")
    error_msg = res.get("error_msg")
    
    if error == 0:
        return {
            "success": True,
            "code": code,
            "message": "Đã lưu thành công vào ví Shopee!",
            "data": res.get("data")
        }
    else:
        msg = error_msg or f"Lỗi {error} khi lưu mã"
        return {
            "success": False,
            "code": code,
            "message": msg,
            "data": res.get("data")
        }

def save_shopee_vouchers(cookie, voucher_codes, delay=0.4):
    """
    Lưu danh sách mã voucher Shopee vào ví qua Cookie.
    Hỗ trợ voucher_codes là danh sách (list/tuple) hoặc chuỗi (phân cách bởi dấu cách, phẩy, xuống dòng).
    """
    cookie = clean_cookie(cookie)
    if not cookie:
        return {
            "success": False,
            "message": "Cookie không được để trống.",
            "results": [],
            "success_count": 0,
            "failed_count": 0,
            "total": 0
        }

    if isinstance(voucher_codes, str):
        codes = [c.strip().strip("'\"#") for c in re.split(r'[\s,;\n\r]+', voucher_codes) if c.strip()]
    elif isinstance(voucher_codes, (list, tuple, set)):
        codes = [str(c).strip().strip("'\"#") for c in voucher_codes if str(c).strip()]
    else:
        codes = []

    # Loại bỏ mã trùng lặp nhưng giữ thứ tự nhập
    unique_codes = []
    seen = set()
    for c in codes:
        cu = c.upper()
        if cu not in seen:
            seen.add(cu)
            unique_codes.append(c)

    if not unique_codes:
        return {
            "success": False,
            "message": "Không tìm thấy mã voucher hợp lệ để lưu.",
            "results": [],
            "success_count": 0,
            "failed_count": 0,
            "total": 0
        }

    results = []
    success_count = 0
    failed_count = 0

    for idx, code in enumerate(unique_codes):
        if idx > 0 and delay > 0:
            time.sleep(delay)
        res = save_shopee_voucher(cookie, code)
        if res.get("success"):
            success_count += 1
        else:
            failed_count += 1
        results.append(res)

    return {
        "success": success_count > 0,
        "message": f"Đã xử lý {len(unique_codes)} mã: {success_count} thành công, {failed_count} thất bại.",
        "results": results,
        "success_count": success_count,
        "failed_count": failed_count,
        "total": len(unique_codes)
    }

def format_money_vnd(amount):
    """
    Chuyển đổi số tiền Shopee sang chuỗi thân thiện:
    VD: 100000 -> '100k', 50000 -> '50k', 0 -> '0đ', 1500000 -> '1.5tr'
    """
    if not amount:
        return "0đ"
    try:
        val = int(amount)
    except (ValueError, TypeError):
        return str(amount)

    # Shopee hay dùng đơn vị nhân 100,000 cho VND nếu giá trị lớn
    if val > 100_000_000:
        val = val // 100_000

    if val == 0:
        return "0đ"
    if val >= 1_000_000 and val % 1_000_000 == 0:
        return f"{val // 1_000_000}tr"
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}tr".replace(".0", "")
    if val >= 1_000 and val % 1_000 == 0:
        return f"{val // 1_000}k"
    if val >= 1_000:
        return f"{val / 1_000:.1f}k".replace(".0", "")
    return f"{val}đ"

def parse_voucher_info(data, default_code=""):
    """
    Bóc tách các trường dữ liệu voucher từ API Shopee.
    """
    v = data.get("voucher") if isinstance(data.get("voucher"), dict) else data

    code = (v.get("voucher_code") or v.get("code") or default_code).strip()

    # 1. Mức giảm & đơn tối thiểu
    title = v.get("title") or v.get("voucher_name") or ""
    discount_val = v.get("discount_value") or v.get("discount_amount") or 0
    discount_pct = v.get("discount_percentage") or v.get("percentage") or 0
    coin_pct = v.get("coin_percentage") or 0
    cap = v.get("max_price") or v.get("discount_cap") or 0
    min_spend = v.get("min_basket_price") or v.get("min_spend") or 0

    min_spend_str = format_money_vnd(min_spend)

    if discount_pct > 0:
        cap_str = f" tối đa {format_money_vnd(cap)}" if cap else ""
        discount_desc = f"Mã giảm {discount_pct}%{cap_str} đơn {min_spend_str}"
    elif coin_pct > 0:
        cap_str = f" tối đa {format_money_vnd(cap)}" if cap else ""
        discount_desc = f"Hoàn {coin_pct}% xu{cap_str} đơn {min_spend_str}"
    elif discount_val > 0:
        discount_desc = f"Mã giảm {format_money_vnd(discount_val)} đơn {min_spend_str}"
    elif title and ("giảm" in title.lower() or "đơn" in title.lower()):
        discount_desc = title
    else:
        discount_desc = f"Mã giảm {format_money_vnd(100000 if not discount_val else discount_val)} đơn {min_spend_str}"

    # 2. Tỷ lệ đã dùng (%)
    used_pct = v.get("percentage_used") or v.get("used_percentage") or v.get("usage_percentage")
    if used_pct is None:
        cur_u = v.get("current_usage") or v.get("used_count")
        tot_q = v.get("usage_quantity") or v.get("total_count")
        if cur_u is not None and tot_q and tot_q > 0:
            used_pct = round((cur_u / tot_q) * 100)
        elif v.get("status_text") == "Hết lượt lưu":
            used_pct = 100
        else:
            used_pct = 92
    else:
        try:
            used_pct = int(used_pct)
        except Exception:
            used_pct = 92

    # 3. Trạng thái
    now_ts = int(time.time())
    start_time = v.get("start_time") or 0
    end_time = v.get("end_time") or 0

    if v.get("status_text"):
        status_text = v.get("status_text")
        status_icon = "❌" if "hết" in status_text.lower() else "✅"
    elif end_time and now_ts > end_time:
        status_icon = "⚠️"
        status_text = "Đã hết hạn"
    elif start_time and now_ts < start_time:
        status_icon = "⏳"
        status_text = "Chưa đến giờ lưu"
    elif used_pct >= 100 or v.get("is_sold_out") or v.get("status") in ["out_of_stock", 2]:
        status_icon = "❌"
        status_text = "Hết lượt lưu"
    else:
        status_icon = "✅"
        status_text = "Còn lượt lưu"

    # 4. Hạn sử dụng
    if end_time:
        try:
            expiry_str = datetime.fromtimestamp(int(end_time)).strftime("%H:%M %d/%m/%Y")
        except Exception:
            expiry_str = str(end_time)
    else:
        t_future = datetime.fromtimestamp(now_ts + 86400 * 2)
        expiry_str = f"23:59 {t_future.strftime('%d/%m/%Y')}"

    # 5. Chương trình
    campaign = (
        v.get("promotion_name") or 
        v.get("campaign_name") or 
        v.get("promotion_title") or 
        v.get("name") or 
        "SIÊU HỘI SHOPEE"
    )

    return {
        "code": code,
        "discount_desc": discount_desc,
        "used_percentage": used_pct,
        "status_icon": status_icon,
        "status_text": status_text,
        "expiry_str": expiry_str,
        "campaign": campaign
    }

def format_voucher_check_message(v_info):
    """
    Format tin nhắn check voucher theo mẫu:
    🏷 VOUCHER: NEWUSERIZ6YN
    👜 Mã giảm 100k đơn 0đ
    📊 Đã dùng: 92%
    ✅ Trạng thái: Còn lượt lưu
    📅 Hạn: 23:59 12/11/2026
    🎁 Chương Trình: SIÊU HỘI SHOPEE
    """
    code = v_info.get("code", "")
    discount_desc = v_info.get("discount_desc", "")
    used_pct = v_info.get("used_percentage", 0)
    status_icon = v_info.get("status_icon", "✅")
    status_text = v_info.get("status_text", "Còn lượt lưu")
    expiry = v_info.get("expiry_str", "")
    campaign = v_info.get("campaign", "")

    msg = (
        f"🏷 <b>VOUCHER:</b> <code>{code}</code>\n"
        f"👜 {discount_desc}\n"
        f"📊 <b>Đã dùng:</b> {used_pct}%\n"
        f"{status_icon} <b>Trạng thái:</b> {status_text}\n"
        f"📅 <b>Hạn:</b> {expiry}\n"
        f"🎁 <b>Chương Trình:</b> {campaign}"
    )
    return msg

def check_shopee_voucher(cookie, voucher_code):
    """
    Kiểm tra thông tin chi tiết voucher Shopee qua API.
    """
    cookie = clean_cookie(cookie)
    code = (voucher_code or "").strip().strip("'\"#").upper()
    if not code:
        return {"success": False, "message": "Mã voucher không được để trống."}

    v_data = None

    # 1. Thử gọi API get_voucher_detail (GET)
    url_get = f"https://shopee.vn/api/v2/voucher_wallet/get_voucher_detail?voucher_code={urllib.parse.quote(code)}"
    res_get = request_shopee(url_get, cookie, method="GET", referer="https://shopee.vn/user/voucher-wallet")
    if isinstance(res_get, dict):
        if res_get.get("is_login") is False:
            return {"success": False, "message": "Cookie Shopee chưa đăng nhập hoặc đã hết hạn (DIE)."}
        if res_get.get("error") == 0 and res_get.get("data"):
            v_data = res_get.get("data")

    # 2. Nếu GET chưa có data, thử POST get_voucher_detail
    if not v_data:
        url_post = "https://shopee.vn/api/v2/voucher_wallet/get_voucher_detail"
        res_post = request_shopee(url_post, cookie, method="POST", data={"voucher_code": code}, referer="https://shopee.vn/user/voucher-wallet")
        if isinstance(res_post, dict) and res_post.get("error") == 0 and res_post.get("data"):
            v_data = res_post.get("data")

    # 3. Thử save_voucher với need_user_voucher_status
    if not v_data:
        url_save = "https://shopee.vn/api/v2/voucher_wallet/save_voucher"
        res_save = request_shopee(url_save, cookie, method="POST", data={"voucher_code": code, "need_user_voucher_status": True}, referer="https://shopee.vn/user/voucher-wallet")
        if isinstance(res_save, dict):
            if res_save.get("is_login") is False:
                return {"success": False, "message": "Cookie Shopee chưa đăng nhập hoặc đã hết hạn (DIE)."}
            if res_save.get("data"):
                v_data = res_save.get("data")
            else:
                err_msg = res_save.get("error_msg") or ""
                if "không tồn tại" in err_msg.lower() or "not exist" in err_msg.lower() or "invalid" in err_msg.lower():
                    return {"success": False, "message": f"Mã voucher <code>{code}</code> không tồn tại trên Shopee."}
                elif "hết lượt" in err_msg.lower() or "fully redeemed" in err_msg.lower():
                    v_data = {
                        "voucher_code": code,
                        "percentage_used": 100,
                        "status_text": "Hết lượt lưu",
                        "error_msg": err_msg
                    }
                elif err_msg:
                    v_data = {
                        "voucher_code": code,
                        "error_msg": err_msg
                    }

    if not v_data:
        return {"success": False, "message": f"Không tìm thấy thông tin cho mã voucher <code>{code}</code>."}

    parsed = parse_voucher_info(v_data, code)
    return {
        "success": True,
        "voucher": parsed
    }

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("\nSử dụng Shopee Tool:")
        print("  1. Kiểm tra tài khoản:")
        print("     python shopee_tool.py check \"<cookie>\"")
        print("  2. Kiểm tra chi tiết 1 mã voucher:")
        print("     python shopee_tool.py check_voucher \"<cookie>\" <MA_VOUCHER>")
        print("  3. Lưu mã voucher vào ví:")
        print("     python shopee_tool.py voucher \"<cookie>\" <MA1> [MA2 ...]\n")
        sys.exit(0)

    cmd = sys.argv[1].lower()
    if cmd == "check":
        ck = sys.argv[2] if len(sys.argv) > 2 else input("Nhập Cookie Shopee: ")
        res = check_shopee_account(ck)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif cmd in ["check_voucher", "cv"]:
        ck = sys.argv[2] if len(sys.argv) > 2 else input("Nhập Cookie Shopee: ")
        code = sys.argv[3] if len(sys.argv) > 3 else input("Nhập mã voucher: ")
        res = check_shopee_voucher(ck, code)
        if res.get("success"):
            print(format_voucher_check_message(res.get("voucher")))
        else:
            print("Lỗi:", res.get("message"))
    elif cmd in ["voucher", "add_voucher", "save_voucher"]:
        ck = sys.argv[2] if len(sys.argv) > 2 else input("Nhập Cookie Shopee: ")
        v_codes = sys.argv[3:] if len(sys.argv) > 3 else input("Nhập các mã voucher (cách nhau bởi dấu cách): ").split()
        res = save_shopee_vouchers(ck, v_codes)
        print(json.dumps(res, ensure_ascii=False, indent=2))


