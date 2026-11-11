# -*- coding: utf-8 -*-
"""
Express Tracking App - Local Standalone (Chạy trực tiếp trên máy tính)
Không cần Node.js, không cần MySQL, không cần Telegram Bot.
Sử dụng 100% thư viện có sẵn của Python.
"""

import sys
import os
import json
import sqlite3
import hashlib
import urllib.request
import urllib.error
import urllib.parse
import re
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import webbrowser
import threading
from tracking_core import track_order, detect_carrier
from shopee_tool import save_shopee_vouchers, check_shopee_account, check_shopee_voucher

PORT = 8080
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_tracking.db")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS saved_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            carrier TEXT NOT NULL,
            tracking_number TEXT NOT NULL,
            credential TEXT,
            note TEXT,
            last_status TEXT,
            last_location TEXT,
            last_time TEXT,
            is_completed INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(carrier, tracking_number)
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ==================== TRACKING PROVIDERS ====================

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
                
                # location
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
            
            # Sort newest first
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

    # If it's already 64-char sha256 hash
    if re.match(r"^[a-fA-F0-9]{64}$", phone_or_verify):
        phone_verify = phone_or_verify.lower()
    else:
        # Normalize VN phone
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
                # Parse date string
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
            # Basic regex extraction of items
            items = re.findall(r'<div class="result-vandon-item[^"]*">([\s\S]*?)</div>\s*</div>', html)
            if not items:
                # Check if invalid or empty
                if "Không tìm thấy" in html or "không có thông tin" in html:
                    return {"success": False, "message": "J&T không tìm thấy mã vận đơn hoặc 4 số cuối SĐT không đúng."}
                return {"success": False, "message": "J&T chưa có dữ liệu hành trình hoặc hệ thống đang bận."}
            
            parsed_records = []
            for item in items:
                text_clean = re.sub(r'<[^>]+>', ' ', item)
                text_clean = ' '.join(text_clean.split())
                
                # find date time
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

# ==================== WEB HANDLER & UI ====================

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Express Tracking - Tra cứu vận đơn trực tiếp</title>
  <style>
    :root {
      --primary: #ee4d2d;
      --primary-hover: #d73211;
      --bg: #f8fafc;
      --card-bg: #ffffff;
      --text: #1e293b;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --success: #16a34a;
      --blue: #2563eb;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    body { background-color: var(--bg); color: var(--text); line-height: 1.5; padding: 20px 12px; }
    .container { max-width: 800px; margin: 0 auto; }
    
    /* Header */
    .header { text-align: center; margin-bottom: 24px; }
    .header h1 { font-size: 26px; color: var(--primary); display: flex; align-items: center; justify-content: center; gap: 8px; }
    .header p { color: var(--text-muted); font-size: 14px; margin-top: 4px; }
    .badge-offline { background: #e0f2fe; color: #0284c7; font-size: 12px; padding: 3px 10px; border-radius: 99px; font-weight: 600; display: inline-block; margin-top: 6px; }

    /* Card */
    .card { background: var(--card-bg); border-radius: 12px; border: 1px solid var(--border); box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); padding: 20px; margin-bottom: 20px; }
    
    /* Form */
    .form-group { margin-bottom: 16px; }
    label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; color: #334155; }
    input, select { width: 100%; padding: 10px 14px; border: 1px solid var(--border); border-radius: 8px; font-size: 15px; outline: none; transition: border-color 0.2s; }
    input:focus, select:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(238, 77, 45, 0.15); }
    .row { display: flex; gap: 12px; }
    .col-carrier { flex: 1; }
    .col-code { flex: 2; }
    .col-cred { flex: 1.5; }
    
    /* Buttons */
    .btn { background: var(--primary); color: white; border: none; border-radius: 8px; padding: 12px 20px; font-size: 15px; font-weight: 600; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 8px; transition: background 0.2s; width: 100%; }
    .btn:hover { background: var(--primary-hover); }
    .btn:disabled { opacity: 0.6; cursor: not-allowed; }
    .btn-secondary { background: #f1f5f9; color: #334155; border: 1px solid var(--border); }
    .btn-secondary:hover { background: #e2e8f0; }

    /* Status Result Box */
    .result-box { display: none; margin-top: 20px; }
    .status-summary { background: #fff7ed; border: 1px solid #ffedd5; border-radius: 10px; padding: 16px; margin-bottom: 20px; }
    .status-title { font-size: 16px; font-weight: 700; color: #c2410c; margin-bottom: 4px; }
    .status-meta { font-size: 13px; color: var(--text-muted); display: flex; flex-wrap: wrap; gap: 16px; margin-top: 8px; }
    
    /* Timeline */
    .timeline { position: relative; padding-left: 24px; border-left: 2px solid #e2e8f0; margin-left: 8px; }
    .timeline-item { position: relative; margin-bottom: 20px; }
    .timeline-item:last-child { margin-bottom: 0; }
    .timeline-dot { position: absolute; left: -31px; top: 2px; width: 12px; height: 12px; border-radius: 50%; background: #94a3b8; border: 2px solid white; box-shadow: 0 0 0 2px #cbd5e1; }
    .timeline-item:first-child .timeline-dot { background: var(--primary); box-shadow: 0 0 0 3px rgba(238, 77, 45, 0.2); }
    .timeline-time { font-size: 12px; color: var(--text-muted); font-weight: 600; }
    .timeline-content { font-size: 14px; font-weight: 500; color: #1e293b; margin-top: 2px; }
    .timeline-location { font-size: 12px; color: #475569; margin-top: 2px; }

    /* Saved Orders Tab */
    .tabs { display: flex; gap: 8px; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
    .tab-btn { background: none; border: none; font-size: 14px; font-weight: 600; padding: 8px 16px; border-radius: 6px; cursor: pointer; color: var(--text-muted); }
    .tab-btn.active { background: #fee2e2; color: var(--primary); }

    /* Table */
    .table-container { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); }
    th { background: #f8fafc; color: var(--text-muted); font-weight: 600; }
    .badge-carrier { padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; background: #f1f5f9; }
    .badge-spx { background: #fff1f2; color: #e11d48; }
    .badge-ghn { background: #eff6ff; color: #2563eb; }
    .badge-jnt { background: #fef2f2; color: #dc2626; }
    .btn-action { padding: 4px 8px; border-radius: 4px; border: 1px solid var(--border); font-size: 12px; cursor: pointer; background: white; }
    .btn-action:hover { background: #f1f5f9; }

    /* Alert */
    .alert-error { background: #fef2f2; border: 1px solid #fee2e2; color: #991b1b; padding: 12px; border-radius: 8px; font-size: 14px; margin-top: 16px; display: none; }
    .spinner { display: inline-block; width: 18px; height: 18px; border: 3px solid rgba(255,255,255,.3); border-radius: 50%; border-top-color: #fff; animation: spin 0.8s ease-in-out infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>

  <div class="container">
    <div class="header">
      <h1>📦 Express Tracking Bot (Bản Cục Bộ)</h1>
      <p>Tra cứu trực tiếp hành trình đơn hàng SPX, GHN, J&T ngay trên máy tính</p>
      <div class="badge-offline">⚡ Chạy trực tiếp 100% - Không cần MySQL - Không cần Telegram Bot</div>
    </div>

    <div class="tabs">
      <button class="tab-btn active" id="tabBtnTrack" onclick="switchTab('track')">🔍 Tra cứu đơn hàng</button>
      <button class="tab-btn" id="tabBtnSaved" onclick="switchTab('saved')">📋 Đơn đã lưu (<span id="savedCount">0</span>)</button>
      <button class="tab-btn" id="tabBtnShopee" onclick="switchTab('shopee')">🎟 Lưu Voucher Shopee</button>
    </div>

    <!-- TAB 1: TRA CỨU -->
    <div id="tab-track">
      <div class="card">
        <form id="trackForm" onsubmit="handleTrack(event)">
          <div class="row">
            <div class="col-carrier">
              <label>Đơn vị vận chuyển</label>
              <select id="carrier" onchange="handleCarrierChange()">
                <option value="AUTO">⚡ Tự động nhận diện</option>
                <option value="SPX">Shopee Express (SPX)</option>
                <option value="GHN">Giao Hàng Nhanh (GHN)</option>
                <option value="JNT">J&T Express</option>
              </select>
            </div>
            <div class="col-code">
              <label>Mã vận đơn</label>
              <input type="text" id="trackingNumber" placeholder="VD: SPXVN063015366786 hoặc GY8RA4EL" required autofocus>
            </div>
          </div>

          <div class="form-group" id="credGroup" style="display: none; margin-top: 14px;">
            <label id="credLabel">Xác thực người nhận</label>
            <input type="text" id="credential" placeholder="">
          </div>

          <div style="margin-top: 16px; display: flex; gap: 10px;">
            <button type="submit" class="btn" id="btnSubmit">
              <span id="btnText">Tra cứu ngay</span>
              <span class="spinner" id="btnSpinner" style="display: none;"></span>
            </button>
          </div>
        </form>

        <div class="alert-error" id="errorBox"></div>
      </div>

      <!-- KẾT QUẢ TRA CỨU -->
      <div class="card result-box" id="resultCard">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
          <h2 style="font-size: 18px;" id="resCarrier">SPX Express</h2>
          <button class="btn-action" onclick="saveCurrentOrder()" id="btnSaveOrder">⭐ Lưu vào danh sách theo dõi</button>
        </div>

        <div class="status-summary">
          <div class="status-title" id="resStatus">Trạng thái mới nhất</div>
          <div class="status-meta">
            <div>📌 Mã đơn: <b id="resCode"></b></div>
            <div>⏰ Cập nhật: <span id="resTime"></span></div>
            <div id="resLocWrap" style="display: none;">📍 Vị trí: <span id="resLoc"></span></div>
          </div>
        </div>

        <h3 style="font-size: 15px; margin-bottom: 16px; color: #475569;">Lịch sử vận chuyển chi tiết:</h3>
        <div class="timeline" id="timelineList"></div>
      </div>
    </div>

    <!-- TAB 2: ĐƠN ĐÃ LƯU -->
    <div id="tab-saved" style="display: none;">
      <div class="card">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
          <h2 style="font-size: 17px;">Danh sách đơn hàng đang theo dõi</h2>
          <button class="btn-action" onclick="loadSavedOrders()">🔄 Làm mới</button>
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>Hãng</th>
                <th>Mã vận đơn</th>
                <th>Ghi chú</th>
                <th>Trạng thái mới nhất</th>
                <th>Cập nhật</th>
                <th>Thao tác</th>
              </tr>
            </thead>
            <tbody id="savedTableBody">
              <tr><td colspan="6" style="text-align: center; color: #94a3b8; padding: 24px;">Chưa có đơn hàng nào được lưu.</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- TAB 3: SHOPEE VOUCHER -->
    <div id="tab-shopee" style="display: none;">
      <div class="card">
        <h3 style="margin-bottom: 8px; color: #ee4d2d; display: flex; align-items: center; gap: 8px;">
          🎟 Thêm Voucher Vào Ví Shopee Qua Cookie
        </h3>
        <p style="font-size: 13px; color: #64748b; margin-bottom: 16px;">
          Nhập Cookie tài khoản Shopee và danh sách mã voucher để tự động gửi yêu cầu lưu vào ví Shopee của bạn.
        </p>

        <form id="voucherForm" onsubmit="handleSaveVoucher(event)">
          <div class="form-group">
            <label>Cookie tài khoản Shopee:</label>
            <textarea id="shopeeCookie" rows="3" placeholder="Dán chuỗi Cookie Shopee vào đây (bắt đầu bằng SPC_EC=... hoặc dán toàn bộ cookie)" required style="width: 100%; padding: 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 13px; font-family: monospace;"></textarea>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 6px;">
              <span style="font-size: 12px; color: #94a3b8;">💡 Trình duyệt sẽ tự động ghi nhớ Cookie trên máy này.</span>
              <button type="button" class="btn-action" onclick="handleCheckCookie()" style="font-weight: 600; color: #0284c7; padding: 4px 10px; font-size: 12px;">🔍 Kiểm tra Cookie & Ví</button>
            </div>
            <div id="accountInfoBox" style="display: none; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 12px; margin-top: 10px; font-size: 13px;"></div>
          </div>

          <div class="form-group">
            <label>Mã Voucher cần lưu hoặc kiểm tra:</label>
            <textarea id="voucherCodes" rows="2" placeholder="Nhập một hoặc nhiều mã (phân cách bằng dấu cách, phẩy hoặc xuống dòng). VD: NEWUSERIZ6YN FREESHIP50K" required style="width: 100%; padding: 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px;"></textarea>
          </div>

          <div style="display: flex; gap: 10px;">
            <button type="submit" id="btnSaveVoucher" class="btn" style="flex: 1;">
              <span id="btnVoucherSpinner" class="spinner" style="display: none;"></span>
              <span id="btnVoucherText">Thêm Vào Ví Shopee</span>
            </button>
            <button type="button" id="btnCheckVoucher" class="btn btn-secondary" onclick="handleCheckVoucherDetail()" style="flex: 1;">
              <span id="btnCheckSpinner" class="spinner" style="display: none;"></span>
              <span id="btnCheckText">🔍 Check Chi Tiết Voucher</span>
            </button>
          </div>
        </form>

        <div id="voucherAlert" class="alert-error" style="display: none;"></div>

        <!-- Voucher Check Detail Card -->
        <div id="voucherDetailCard" style="display: none; margin-top: 16px; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 10px; padding: 16px; font-size: 14px; line-height: 1.8;">
          <div style="font-weight: 700; color: #166534; font-size: 16px; margin-bottom: 8px;">🏷 VOUCHER: <span id="vcCode" style="font-family: monospace;"></span></div>
          <div>👜 <span id="vcDiscount"></span></div>
          <div>📊 <b>Đã dùng:</b> <span id="vcUsed"></span></div>
          <div><span id="vcStatusIcon">✅</span> <b>Trạng thái:</b> <span id="vcStatusText"></span></div>
          <div>📅 <b>Hạn:</b> <span id="vcExpiry"></span></div>
          <div>🎁 <b>Chương Trình:</b> <span id="vcCampaign"></span></div>
        </div>

        <div id="voucherResultBox" style="display: none; margin-top: 20px;">
          <h4 id="voucherSummaryTitle" style="font-size: 15px; margin-bottom: 12px; font-weight: 700;"></h4>
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th style="width: 25%;">Mã Voucher</th>
                  <th style="width: 25%;">Trạng thái</th>
                  <th>Phản hồi Shopee</th>
                </tr>
              </thead>
              <tbody id="voucherResultTable"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

  </div>

  <script>
    let currentResult = null;

    function handleCarrierChange() {
      const c = document.getElementById('carrier').value;
      const credGroup = document.getElementById('credGroup');
      const credLabel = document.getElementById('credLabel');
      const credInput = document.getElementById('credential');
      
      if (c === 'GHN') {
        credGroup.style.display = 'block';
        credLabel.innerText = 'Số điện thoại người nhận (Bắt buộc cho GHN):';
        credInput.placeholder = 'Nhập số điện thoại nhận hàng (VD: 0987654321)';
      } else if (c === 'JNT') {
        credGroup.style.display = 'block';
        credLabel.innerText = '4 số cuối SĐT người nhận (Bắt buộc cho J&T):';
        credInput.placeholder = 'Nhập 4 số cuối SĐT (VD: 9613)';
      } else {
        credGroup.style.display = 'none';
      }
    }

    async function handleTrack(e) {
      e.preventDefault();
      const carrier = document.getElementById('carrier').value;
      const code = document.getElementById('trackingNumber').value.trim();
      const credential = document.getElementById('credential').value.trim();
      const errorBox = document.getElementById('errorBox');
      const resultCard = document.getElementById('resultCard');
      const btnSubmit = document.getElementById('btnSubmit');
      const btnText = document.getElementById('btnText');
      const btnSpinner = document.getElementById('btnSpinner');

      errorBox.style.display = 'none';
      resultCard.style.display = 'none';
      btnSubmit.disabled = true;
      btnText.innerText = 'Đang tra cứu...';
      btnSpinner.style.display = 'inline-block';

      try {
        const query = new URLSearchParams({ carrier, code, credential });
        const res = await fetch('/api/track?' + query.toString());
        const data = await res.json();

        if (!data.success) {
          errorBox.innerText = data.message || 'Không tìm thấy dữ liệu vận đơn.';
          errorBox.style.display = 'block';
          return;
        }

        currentResult = data;
        displayResult(data);
      } catch (err) {
        errorBox.innerText = 'Lỗi kết nối máy chủ cục bộ: ' + err.message;
        errorBox.style.display = 'block';
      } finally {
        btnSubmit.disabled = false;
        btnText.innerText = 'Tra cứu ngay';
        btnSpinner.style.display = 'none';
      }
    }

    function displayResult(data) {
      document.getElementById('resCarrier').innerText = data.carrier;
      document.getElementById('resCode').innerText = data.tracking_number;
      document.getElementById('resStatus').innerText = data.latest_status || 'Không rõ trạng thái';
      document.getElementById('resTime').innerText = data.latest_time || 'Chưa có mốc thời gian';
      
      const locWrap = document.getElementById('resLocWrap');
      if (data.latest_location) {
        document.getElementById('resLoc').innerText = data.latest_location;
        locWrap.style.display = 'block';
      } else {
        locWrap.style.display = 'none';
      }

      const list = document.getElementById('timelineList');
      list.innerHTML = '';

      if (data.events && data.events.length > 0) {
        data.events.forEach(item => {
          const div = document.createElement('div');
          div.className = 'timeline-item';
          div.innerHTML = `
            <div class="timeline-dot"></div>
            <div class="timeline-time">${item.time}</div>
            <div class="timeline-content">${item.status}</div>
            ${item.location ? `<div class="timeline-location">📍 ${item.location}</div>` : ''}
          `;
          list.appendChild(div);
        });
      } else {
        list.innerHTML = '<div style="color: #94a3b8; font-size: 13px;">Chưa có chi tiết lộ trình.</div>';
      }

      document.getElementById('resultCard').style.display = 'block';
    }

    async function saveCurrentOrder() {
      if (!currentResult) return;
      const note = prompt('Nhập ghi chú cho đơn hàng này (tùy chọn, ví dụ: Quần áo, Phụ kiện...):', '');
      const btn = document.getElementById('btnSaveOrder');

      try {
        const res = await fetch('/api/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            carrier: currentResult.carrier,
            tracking_number: currentResult.tracking_number,
            credential: document.getElementById('credential').value.trim(),
            note: note || '',
            last_status: currentResult.latest_status,
            last_location: currentResult.latest_location,
            last_time: currentResult.latest_time,
            is_completed: currentResult.is_completed ? 1 : 0
          })
        });
        const d = await res.json();
        if (d.success) {
          alert('Đã lưu vào danh sách theo dõi!');
          loadSavedOrders();
        } else {
          alert(d.message || 'Lỗi lưu đơn');
        }
      } catch (e) {
        alert('Lỗi lưu đơn: ' + e.message);
      }
    }

    async function loadSavedOrders() {
      try {
        const res = await fetch('/api/history');
        const list = await res.json();
        document.getElementById('savedCount').innerText = list.length;
        const tbody = document.getElementById('savedTableBody');
        tbody.innerHTML = '';

        if (!list || list.length === 0) {
          tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #94a3b8; padding: 24px;">Chưa có đơn hàng nào được lưu.</td></tr>';
          return;
        }

        list.forEach(o => {
          const tr = document.createElement('tr');
          const carrierBadge = o.carrier.includes('SPX') ? 'badge-spx' : (o.carrier.includes('GHN') ? 'badge-ghn' : 'badge-jnt');
          tr.innerHTML = `
            <td><span class="badge-carrier ${carrierBadge}">${o.carrier}</span></td>
            <td><b>${o.tracking_number}</b></td>
            <td>${o.note || '-'}</td>
            <td><span style="font-weight: 500;">${o.last_status || '-'}</span></td>
            <td style="color: #64748b; font-size: 12px;">${o.last_time || '-'}</td>
            <td style="white-space: nowrap;">
              <button class="btn-action" onclick="quickTrack('${o.carrier}', '${o.tracking_number}', '${o.credential || ''}')">Xem</button>
              <button class="btn-action" style="color: #dc2626;" onclick="deleteOrder(${o.id})">Xóa</button>
            </td>
          `;
          tbody.appendChild(tr);
        });
      } catch (e) {
        console.error('Lỗi tải danh sách', e);
      }
    }

    function quickTrack(carrier, code, cred) {
      switchTab('track');
      let selectVal = 'AUTO';
      if (carrier.includes('SPX')) selectVal = 'SPX';
      else if (carrier.includes('GHN')) selectVal = 'GHN';
      else if (carrier.includes('JNT')) selectVal = 'JNT';
      
      document.getElementById('carrier').value = selectVal;
      handleCarrierChange();
      document.getElementById('trackingNumber').value = code;
      document.getElementById('credential').value = cred;
      document.getElementById('trackForm').requestSubmit();
    }

    async function deleteOrder(id) {
      if (!confirm('Bạn có chắc muốn xóa đơn này khỏi danh sách theo dõi?')) return;
      await fetch('/api/delete?id=' + id, { method: 'DELETE' });
      loadSavedOrders();
    }

    function switchTab(tab) {
      const btnTrack = document.getElementById('tabBtnTrack');
      const btnSaved = document.getElementById('tabBtnSaved');
      const btnShopee = document.getElementById('tabBtnShopee');
      const tabTrack = document.getElementById('tab-track');
      const tabSaved = document.getElementById('tab-saved');
      const tabShopee = document.getElementById('tab-shopee');

      btnTrack.classList.remove('active');
      btnSaved.classList.remove('active');
      btnShopee.classList.remove('active');
      tabTrack.style.display = 'none';
      tabSaved.style.display = 'none';
      tabShopee.style.display = 'none';

      if (tab === 'track') {
        btnTrack.classList.add('active');
        tabTrack.style.display = 'block';
      } else if (tab === 'saved') {
        btnSaved.classList.add('active');
        tabSaved.style.display = 'block';
        loadSavedOrders();
      } else if (tab === 'shopee') {
        btnShopee.classList.add('active');
        tabShopee.style.display = 'block';
        const savedCookie = localStorage.getItem('shopee_cookie_local') || '';
        if (savedCookie && !document.getElementById('shopeeCookie').value) {
          document.getElementById('shopeeCookie').value = savedCookie;
        }
      }
    }

    async function handleSaveVoucher(e) {
      e.preventDefault();
      const cookie = document.getElementById('shopeeCookie').value.trim();
      const codes = document.getElementById('voucherCodes').value.trim();
      const alertBox = document.getElementById('voucherAlert');
      const resultBox = document.getElementById('voucherResultBox');
      const titleBox = document.getElementById('voucherSummaryTitle');
      const tableBody = document.getElementById('voucherResultTable');
      const btn = document.getElementById('btnSaveVoucher');
      const btnText = document.getElementById('btnVoucherText');
      const spinner = document.getElementById('btnVoucherSpinner');

      alertBox.style.display = 'none';
      resultBox.style.display = 'none';
      btn.disabled = true;
      btnText.innerText = 'Đang gửi yêu cầu...';
      spinner.style.display = 'inline-block';

      try {
        localStorage.setItem('shopee_cookie_local', cookie);
        const res = await fetch('/api/shopee/save_voucher', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cookie, codes })
        });
        const data = await res.json();

        if (!data.results || data.results.length === 0) {
          alertBox.innerText = data.message || 'Không có mã voucher nào được xử lý.';
          alertBox.style.display = 'block';
          return;
        }

        tableBody.innerHTML = '';
        data.results.forEach(item => {
          const tr = document.createElement('tr');
          const isSuccess = item.success;
          const badgeClass = isSuccess ? 'badge-ghn' : 'badge-jnt';
          const statusText = isSuccess ? '✅ Thành công' : '❌ Thất bại';
          tr.innerHTML = `
            <td><code><b>${item.code}</b></code></td>
            <td><span class="badge-carrier ${badgeClass}">${statusText}</span></td>
            <td>${item.message || '-'}</td>
          `;
          tableBody.appendChild(tr);
        });

        titleBox.innerHTML = `Tổng kết: <b style="color: #16a34a;">${data.success_count} thành công</b> / <b>${data.total} mã</b> (${data.failed_count} thất bại)`;
        resultBox.style.display = 'block';
      } catch (err) {
        alertBox.innerText = 'Lỗi kết nối máy chủ: ' + err.message;
        alertBox.style.display = 'block';
      } finally {
        btn.disabled = false;
        btnText.innerText = 'Thêm Vào Ví Shopee';
        spinner.style.display = 'none';
      }
    }

    async function handleCheckCookie() {
      const cookie = document.getElementById('shopeeCookie').value.trim();
      const infoBox = document.getElementById('accountInfoBox');
      const alertBox = document.getElementById('voucherAlert');
      alertBox.style.display = 'none';

      if (!cookie) {
        alert('Vui lòng dán Cookie Shopee vào ô trên trước khi kiểm tra!');
        return;
      }

      infoBox.style.display = 'block';
      infoBox.style.background = '#f8fafc';
      infoBox.style.borderColor = '#cbd5e1';
      infoBox.innerHTML = '⏳ Đang kiểm tra trạng thái Cookie và ví voucher...';

      try {
        localStorage.setItem('shopee_cookie_local', cookie);
        const res = await fetch('/api/shopee/check_account', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cookie })
        });
        const d = await res.json();

        if (d.success) {
          infoBox.style.background = '#f0fdf4';
          infoBox.style.borderColor = '#bbf7d0';
          infoBox.innerHTML = `
            <div style="font-weight: 700; color: #166534; margin-bottom: 6px;">✅ Cookie hợp lệ - Đang hoạt động!</div>
            <div>👤 <b>Tài khoản:</b> <code>${d.username}</code> ${d.nickname ? '(' + d.nickname + ')' : ''}</div>
            <div>🎟 <b>Số voucher trong ví:</b> <b style="color: #ea580c; font-size: 14px;">${d.voucher_count}</b> mã</div>
            <div>🚚 <b>Đơn đang giao:</b> ${d.delivering_orders ? d.delivering_orders.length : 0} đơn hàng</div>
          `;
        } else {
          infoBox.style.background = '#fef2f2';
          infoBox.style.borderColor = '#fee2e2';
          infoBox.innerHTML = `<span style="color: #991b1b; font-weight: 600;">❌ ${d.message || 'Cookie không hợp lệ hoặc đã hết hạn.'}</span>`;
        }
      } catch (e) {
        infoBox.style.background = '#fef2f2';
        infoBox.style.borderColor = '#fee2e2';
        infoBox.innerHTML = `<span style="color: #991b1b;">❌ Lỗi kết nối: ${e.message}</span>`;
      }
    }

    async function handleCheckVoucherDetail() {
      const cookie = document.getElementById('shopeeCookie').value.trim();
      const rawCodes = document.getElementById('voucherCodes').value.trim();
      const alertBox = document.getElementById('voucherAlert');
      const detailCard = document.getElementById('voucherDetailCard');
      const btn = document.getElementById('btnCheckVoucher');
      const btnText = document.getElementById('btnCheckText');
      const spinner = document.getElementById('btnCheckSpinner');

      alertBox.style.display = 'none';
      detailCard.style.display = 'none';

      if (!cookie) {
        alert('Vui lòng dán Cookie Shopee vào ô trên trước khi check mã!');
        return;
      }
      if (!rawCodes) {
        alert('Vui lòng nhập mã voucher cần kiểm tra!');
        return;
      }

      const code = rawCodes.split(/[ ,;\n\r]+/)[0].trim();
      btn.disabled = true;
      btnText.innerText = 'Đang kiểm tra...';
      spinner.style.display = 'inline-block';

      try {
        localStorage.setItem('shopee_cookie_local', cookie);
        const res = await fetch('/api/shopee/check_voucher', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cookie, code })
        });
        const data = await res.json();

        if (!data.success) {
          alertBox.innerText = data.message || 'Không thể kiểm tra mã voucher này.';
          alertBox.style.display = 'block';
          return;
        }

        const v = data.voucher;
        document.getElementById('vcCode').innerText = v.code;
        document.getElementById('vcDiscount').innerText = v.discount_desc;
        document.getElementById('vcUsed').innerText = v.used_percentage + '%';
        document.getElementById('vcStatusIcon').innerText = v.status_icon;
        document.getElementById('vcStatusText').innerText = v.status_text;
        document.getElementById('vcExpiry').innerText = v.expiry_str;
        document.getElementById('vcCampaign').innerText = v.campaign;
        detailCard.style.display = 'block';
      } catch (err) {
        alertBox.innerText = 'Lỗi kết nối máy chủ: ' + err.message;
        alertBox.style.display = 'block';
      } finally {
        btn.disabled = false;
        btnText.innerText = '🔍 Check Chi Tiết Voucher';
        spinner.style.display = 'none';
      }
    }

    // Initial load
    loadSavedOrders();
  </script>
</body>
</html>
"""

class TrackingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
            return

        if path == "/api/track":
            params = urllib.parse.parse_qs(parsed.query)
            carrier = params.get("carrier", ["AUTO"])[0]
            code = params.get("code", [""])[0]
            credential = params.get("credential", [""])[0]

            res = track_order(carrier, code, credential)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/history":
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT id, carrier, tracking_number, credential, note, last_status, last_location, last_time, is_completed FROM saved_orders ORDER BY id DESC")
            rows = c.fetchall()
            conn.close()

            orders = []
            for r in rows:
                orders.append({
                    "id": r[0],
                    "carrier": r[1],
                    "tracking_number": r[2],
                    "credential": r[3],
                    "note": r[4],
                    "last_status": r[5],
                    "last_location": r[6],
                    "last_time": r[7],
                    "is_completed": r[8]
                })

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(orders, ensure_ascii=False).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/save":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            data = json.loads(body)

            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("""
                INSERT INTO saved_orders (carrier, tracking_number, credential, note, last_status, last_location, last_time, is_completed, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(carrier, tracking_number) DO UPDATE SET
                    credential = excluded.credential,
                    note = excluded.note,
                    last_status = excluded.last_status,
                    last_location = excluded.last_location,
                    last_time = excluded.last_time,
                    is_completed = excluded.is_completed,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                data.get("carrier"),
                data.get("tracking_number"),
                data.get("credential", ""),
                data.get("note", ""),
                data.get("last_status", ""),
                data.get("last_location", ""),
                data.get("last_time", ""),
                data.get("is_completed", 0)
            ))
            conn.commit()
            conn.close()

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            return

        if parsed.path == "/api/shopee/save_voucher":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            data = json.loads(body)
            cookie = data.get("cookie", "")
            codes = data.get("codes", "")
            res = save_shopee_vouchers(cookie, codes)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        if parsed.path == "/api/shopee/check_account":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            data = json.loads(body)
            cookie = data.get("cookie", "")
            res = check_shopee_account(cookie)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        if parsed.path == "/api/shopee/check_voucher":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            data = json.loads(body)
            cookie = data.get("cookie", "")
            code = data.get("code", "")
            res = check_shopee_voucher(cookie, code)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/delete":
            params = urllib.parse.parse_qs(parsed.query)
            order_id = params.get("id", [None])[0]
            if order_id:
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("DELETE FROM saved_orders WHERE id = ?", (order_id,))
                conn.commit()
                conn.close()

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        # Silent logger for clean console
        return

def open_browser():
    webbrowser.open(f"http://localhost:{PORT}")

def main():
    global PORT
    server = None
    for p in range(8080, 8100):
        try:
            server = HTTPServer(("127.0.0.1", p), TrackingHandler)
            PORT = p
            break
        except OSError:
            continue

    if not server:
        print("[LỖI] Không thể mở cổng từ 8080 đến 8100.")
        return

    print("=" * 60)
    print(f"  EXPRESS TRACKING BOT - LOCAL APP DANG CHAY TAI:")
    print(f"  👉 http://localhost:{PORT}")
    print("=" * 60)
    print("Trinh duyet se tu dong mo len...")
    print("Nhan Ctrl + C de dung ung dung.\n")

    threading.Timer(1.0, open_browser).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDa dung may chu.")
        server.server_close()

if __name__ == "__main__":
    main()
