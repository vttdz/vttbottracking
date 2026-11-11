# -*- coding: utf-8 -*-
"""
Công cụ tra cứu nhanh mã vận đơn SPX / GHN / J&T qua dòng lệnh (CLI).
Cách dùng:
    python tra_cuu_nhanh.py SPXVN063015366786
    python tra_cuu_nhanh.py ghn GY8RA4EL 0987654321
    python tra_cuu_nhanh.py jnt 862195772225 9613
"""
import sys
from app_local import track_order

def main():
    if len(sys.argv) < 2:
        print("\nSử dụng:")
        print("  python tra_cuu_nhanh.py <MÃ_VẬN_ĐƠN>")
        print("  python tra_cuu_nhanh.py <HÃNG: spx|ghn|jnt> <MÃ_VẬN_ĐƠN> [SĐT/4 số cuối]\n")
        print("Ví dụ:")
        print("  python tra_cuu_nhanh.py SPXVN063015366786")
        print("  python tra_cuu_nhanh.py ghn GY8RA4EL 0987654321")
        print("  python tra_cuu_nhanh.py jnt 862195772225 9613\n")
        code = input("Nhập mã vận đơn cần tra cứu: ").strip()
        if not code:
            return
        carrier = "AUTO"
        cred = ""
    else:
        arg1 = sys.argv[1].upper()
        if arg1 in ["SPX", "GHN", "JNT"]:
            carrier = arg1
            code = sys.argv[2] if len(sys.argv) > 2 else ""
            cred = sys.argv[3] if len(sys.argv) > 3 else ""
        else:
            carrier = "AUTO"
            code = sys.argv[1]
            cred = sys.argv[2] if len(sys.argv) > 2 else ""

    print(f"\n🔍 Đang tra cứu đơn hàng: {code}...")
    res = track_order(carrier, code, cred)

    if not res.get("success"):
        print(f"\n❌ Thất bại: {res.get('message')}")
        return

    print("=" * 60)
    print(f"📦 Đơn vị vận chuyển : {res.get('carrier')}")
    print(f"📌 Mã vận đơn        : {res.get('tracking_number')}")
    print(f"⚡ Trạng thái        : {res.get('latest_status')}")
    print(f"⏰ Thời gian         : {res.get('latest_time')}")
    if res.get("latest_location"):
        print(f"📍 Vị trí hiện tại   : {res.get('latest_location')}")
    print("=" * 60)
    print("\n📜 CHI TIẾT HÀNH TRÌNH:")
    for event in res.get("events", []):
        loc = f" ({event['location']})" if event.get("location") else ""
        print(f" • [{event.get('time')}] {event.get('status')}{loc}")
    print()

if __name__ == "__main__":
    main()

