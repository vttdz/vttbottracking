# -*- coding: utf-8 -*-
"""
Core Tracking Engine cho SPX Express, GHN và J&T Express.
Sử dụng thư viện chuẩn của Python, không phụ thuộc bên thứ 3.
"""

import json
import urllib.request
import urllib.error
import urllib.parse
import hashlib
import re
from datetime import datetime

def query_spx(tracking_number):
    clean_code = tracking_number.strip().upper()
    url = f"https://spx.vn/shipment/order/open/order/get_order_info?spx_tn={clean_code}&language_code=vi"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://spx.vn/"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode("utf-8"))
            if data.get("retcode") != 0 or not data.get("data"):
                return {"success": False, "message": "SPX không tìm thấy mã vận đơn này hoặc hệ thống bận."}
            
            records = data.get("data", {}).get("sls_tracking_info", {}).get("records", [])
            if not records:
                return {"success": False, "message": "SPX chưa có nhật ký hành trình cho đơn này."}
            
            parsed_records = []
            for r in records:
                t_sec = r.get("actual_time")
                event_time = datetime.fromtimestamp(t_sec).strftime("%d/%m/%Y %H:%M:%S") if t_sec else ""
                status = r.get("buyer_description") or r.get("description") or ""
                
                loc = ""
                cur_loc = r.get("current_location")
                if isinstance(cur_loc, dict):
                    loc = cur_loc.get("location_name") or cur_loc.get("full_address") or ""
                elif isinstance(r.get("location"), str):
                    loc = r.get("location")

                parsed_records.append({
                    "time": event_time,
                    "status": status,
                    "location": loc,
                    "tracking_name": r.get("tracking_name", ""),
                    "milestone": r.get("milestone_name") or r.get("milestone_code") or ""
                })
            
            parsed_records.sort(key=lambda x: x["time"], reverse=True)
            latest = parsed_records[0] if parsed_records else {}
            
            is_completed = any("giao hàng thành công" in r["status"].lower() or "giao thành công" in r["status"].lower() or "delivered" in r["status"].lower() for r in parsed_records)

            return {
                "success": True,
                "carrier": "SPX Express",
                "tracking_number": clean_code,
                "latest_status": latest.get("status", ""),
                "latest_location": latest.get("location", ""),
                "latest_time": latest.get("time", ""),
                "is_completed": is_completed,
                "events": parsed_records
            }
    except Exception as e:
        return {"success": False, "message": f"Lỗi kết nối SPX: {str(e)}"}

def query_ghn(tracking_number, phone_or_verify):
    clean_code = tracking_number.strip().upper()
    phone_or_verify = (phone_or_verify or "").strip()
    
    if not phone_or_verify:
        return {"success": False, "message": "GHN yêu cầu nhập Số điện thoại người nhận hoặc phone_verify."}

    if re.match(r"^[a-fA-F0-9]{64}$", phone_or_verify):
        phone_verify = phone_or_verify.lower()
    else:
        phone = re.sub(r"[\s.\-()]", "", phone_or_verify)
        if phone.startswith("+84"):
            phone = "0" + phone[3:]
        elif phone.startswith("84") and len(phone) >= 10:
            phone = "0" + phone[2:]
        elif phone and not phone.startswith("0"):
            phone = "0" + phone
        
        phone_verify = hashlib.sha256(f"{clean_code}|{phone}".encode("utf-8")).hexdigest()

    url = "https://fe-online-gateway.ghn.vn/order-tracking/public-api/client/tracking-logs"
    payload = json.dumps({"order_code": clean_code, "phone_verify": phone_verify}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://donhang.ghn.vn",
        "Referer": "https://donhang.ghn.vn/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    
    req = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode("utf-8"))
            if data.get("code") != 200:
                if data.get("code_message") in ["PHONE_VERIFY_REQUIRED", "PHONE_VERIFY_FAIL"]:
                    return {"success": False, "message": "Số điện thoại người nhận không khớp với đơn GHN."}
                return {"success": False, "message": data.get("message") or "Lỗi tra cứu GHN."}
            
            logs = data.get("data", {}).get("tracking_logs") or []
            if not logs:
                return {"success": False, "message": "GHN chưa có dữ liệu hành trình cho đơn này."}
            
            parsed_records = []
            for item in logs:
                action_at = item.get("action_at") or ""
                event_time = action_at
                try:
                    dt = datetime.fromisoformat(action_at.replace("Z", "+00:00"))
                    event_time = dt.strftime("%d/%m/%Y %H:%M:%S")
                except Exception:
                    pass

                status = item.get("status_name") or item.get("status") or ""
                loc = ""
                if isinstance(item.get("location"), dict):
                    loc = item.get("location", {}).get("address") or ""

                parsed_records.append({
                    "time": event_time,
                    "status": status,
                    "location": loc,
                    "tracking_name": item.get("action_code", ""),
                    "milestone": item.get("status", "")
                })

            parsed_records.sort(key=lambda x: x["time"], reverse=True)
            latest = parsed_records[0] if parsed_records else {}
            is_completed = any("thành công" in r["status"].lower() or "delivered" in r["status"].lower() for r in parsed_records)

            return {
                "success": True,
                "carrier": "Giao Hàng Nhanh (GHN)",
                "tracking_number": clean_code,
                "latest_status": latest.get("status", ""),
                "latest_location": latest.get("location", ""),
                "latest_time": latest.get("time", ""),
                "is_completed": is_completed,
                "events": parsed_records
            }
    except Exception as e:
        return {"success": False, "message": f"Lỗi kết nối GHN: {str(e)}"}

def query_jnt(tracking_number, phone_last4):
    clean_code = tracking_number.strip().upper()
    phone_last4 = (phone_last4 or "").strip()
    if not phone_last4 or len(phone_last4) != 4 or not phone_last4.isdigit():
        return {"success": False, "message": "J&T yêu cầu nhập đúng 4 số cuối SĐT người nhận."}

    url = f"https://jtexpress.vn/vi/tracking?type=track&billcode={clean_code}&cellphone={phone_last4}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://jtexpress.vn/vi/tracking"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            html = res.read().decode("utf-8", errors="ignore")
            items = re.findall(r'<div class="result-vandon-item[^"]*">([\s\S]*?)</div>\s*</div>', html)
            if not items:
                if "Không tìm thấy" in html or "không có thông tin" in html:
                    return {"success": False, "message": "J&T không tìm thấy mã vận đơn hoặc 4 số cuối SĐT không đúng."}
                return {"success": False, "message": "J&T chưa có dữ liệu hành trình hoặc hệ thống đang bận."}
            
            parsed_records = []
            for item in items:
                text_clean = re.sub(r'<[^>]+>', ' ', item)
                text_clean = ' '.join(text_clean.split())
                
                dt_match = re.search(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})', text_clean)
                event_time = f"{dt_match.group(1)} {dt_match.group(2)}" if dt_match else ""
                
                parsed_records.append({
                    "time": event_time,
                    "status": text_clean,
                    "location": "",
                    "tracking_name": "J&T Event",
                    "milestone": ""
                })

            latest = parsed_records[0] if parsed_records else {}
            is_completed = any("ký nhận" in r["status"].lower() or "thành công" in r["status"].lower() for r in parsed_records)

            return {
                "success": True,
                "carrier": "J&T Express",
                "tracking_number": clean_code,
                "latest_status": latest.get("status", ""),
                "latest_location": "",
                "latest_time": latest.get("time", ""),
                "is_completed": is_completed,
                "events": parsed_records
            }
    except Exception as e:
        return {"success": False, "message": f"Lỗi kết nối J&T: {str(e)}"}

def detect_carrier(tracking_number):
    tn = tracking_number.strip().upper()
    if tn.startswith("SPX") or tn.startswith("VN") or tn.startswith("SOC"):
        return "SPX"
    if tn.startswith("GY") or tn.startswith("VNGH") or (len(tn) == 8 and tn.isalnum()):
        return "GHN"
    if tn.startswith("JNT") or (len(tn) == 12 and tn.isdigit()):
        return "JNT"
    return "SPX"

def track_order(carrier, code, credential=""):
    carrier = (carrier or "AUTO").upper()
    if carrier == "AUTO":
        carrier = detect_carrier(code)
    
    if carrier == "SPX":
        return query_spx(code)
    elif carrier == "GHN":
        return query_ghn(code, credential)
    elif carrier == "JNT":
        return query_jnt(code, credential)
    else:
        return {"success": False, "message": f"Hãng vận chuyển không hợp lệ: {carrier}"}

