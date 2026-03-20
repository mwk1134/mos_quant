# -*- coding: utf-8 -*-
"""Sheet1 vs 매매 상세내역 비교 (KMW_SOXL_backtest_*_추가.xlsx)"""
import re
from pathlib import Path
import openpyxl
from datetime import datetime

BASE = Path(__file__).resolve().parent
FILE = BASE / "KMW_SOXL_backtest_20260320_220258_추가.xlsx"


def cell_to_date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    # yy.mm.dd.(요일)
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{2})", s.replace(" ", ""))
    if m:
        yy, mm, dd = m.groups()
        y = 2000 + int(yy)
        return f"{y:04d}-{int(mm):02d}-{int(dd):02d}"
    return s


def parse_money(x):
    if x is None or x == "":
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).replace(",", "").replace("$", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def read_sheet(ws, header_row=1):
    rows = list(ws.iter_rows(min_row=header_row, values_only=True))
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    # drop trailing Nones in headers
    while headers and headers[-1] == "":
        headers.pop()
    data = []
    for r in rows[1:]:
        if all(c is None or str(c).strip() == "" for c in r[:5]):
            continue
        row = {}
        for i, h in enumerate(headers):
            if not h:
                continue
            row[h] = r[i] if i < len(r) else None
        if row.get("날짜") is not None:
            data.append(row)
    return headers, data


def main():
    wb = openpyxl.load_workbook(FILE, data_only=True)
    ws1 = wb["Sheet1"]
    wsd = wb["매매 상세내역"]
    h1, d1 = read_sheet(ws1)
    hd, dd = read_sheet(wsd)
    wb.close()

    m1 = {cell_to_date(r["날짜"]): r for r in d1}
    md = {cell_to_date(r["날짜"]): r for r in dd}
    dates1 = sorted(set(m1.keys()) - {None})
    datesd = sorted(set(md.keys()) - {None})

    only1 = sorted(set(dates1) - set(datesd))
    onlyd = sorted(set(datesd) - set(dates1))
    common = sorted(set(dates1) & set(datesd))

    print("=== 행 수 ===")
    print(f"Sheet1 유효행: {len(d1)}, 매매 상세내역: {len(dd)}")
    print(f"Sheet1만 날짜: {len(only1)}, 매매만: {len(onlyd)}, 공통: {len(common)}")
    if only1[:5]:
        print("  Sheet1만(샘플):", only1[:5])
    if onlyd[:5]:
        print("  매매만(샘플):", onlyd[:5])

    rsi_diff = []
    mode_diff = []
    asset_diff = []
    first_big_asset = None
    THRESHOLD = 500  # USD

    for dt in common:
        r1, rd = m1[dt], md[dt]

        def fnum(x):
            if x is None:
                return None
            if isinstance(x, (int, float)):
                return float(x)
            return parse_money(x)

        rsi1, rsid = fnum(r1.get("RSI")), fnum(rd.get("RSI"))
        if rsi1 is not None and rsid is not None:
            if abs(rsi1 - rsid) > 0.02:
                rsi_diff.append((dt, rsi1, rsid))
        elif (rsi1 is None) != (rsid is None):
            rsi_diff.append((dt, rsi1, rsid))

        m1m = str(r1.get("모드", "")).strip()
        mdm = str(rd.get("모드", "")).strip()
        if m1m != mdm:
            mode_diff.append((dt, m1m, mdm))

        ta1 = fnum(r1.get("총자산"))
        tad = fnum(rd.get("총자산"))
        if ta1 is not None and tad is not None:
            diff = tad - ta1
            asset_diff.append((dt, ta1, tad, diff))
            if first_big_asset is None and abs(diff) > THRESHOLD:
                first_big_asset = (dt, ta1, tad, diff)

    print("\n=== 주간 RSI 불일치 (|차이|>0.02 또는 한쪽만 값) ===")
    print(f"건수: {len(rsi_diff)}")
    for row in rsi_diff[:15]:
        print(" ", row)
    if len(rsi_diff) > 15:
        print(f"  ... 외 {len(rsi_diff)-15}건")

    print("\n=== 같은 날짜 모드 불일치 ===")
    print(f"건수: {len(mode_diff)}")
    for row in mode_diff[:30]:
        print(" ", row)
    if len(mode_diff) > 30:
        print(f"  ... 외 {len(mode_diff)-30}건")

    print("\n=== 총자산 차이 (매매상세 - Sheet1) ===")
    if asset_diff:
        diffs = [x[3] for x in asset_diff]
        print(f"일수: {len(asset_diff)}, 최대절대차이: ${max(abs(d) for d in diffs):,.0f}")
        worst = max(asset_diff, key=lambda x: abs(x[3]))
        print(f"최대 불일치일: {worst[0]}  Sheet1=${worst[1]:,.0f}  매매=${worst[2]:,.0f}  차이=${worst[3]:,.0f}")
    print(f"첫 ${THRESHOLD}+ 차이 발생일: {first_big_asset}")

    # 매매 이벤트 분기: 매수체결/수량/매수대금
    print("\n=== 매수 체결 불일치 (금액 $50 이상 또는 수량 다름) ===")
    buy_mismatch = []
    for dt in common:
        r1, rd = m1[dt], md[dt]
        b1, bd = parse_money(r1.get("매수체결")), parse_money(rd.get("매수체결"))
        q1, qd = r1.get("수량"), rd.get("수량")
        try:
            q1 = int(q1) if q1 not in (None, "") else 0
        except (TypeError, ValueError):
            q1 = 0
        try:
            qd = int(qd) if qd not in (None, "") else 0
        except (TypeError, ValueError):
            qd = 0
        a1, ad = parse_money(r1.get("매수대금")), parse_money(rd.get("매수대금"))
        if (b1 or 0) > 0 or (bd or 0) > 0 or q1 or qd:
            if q1 != qd or (a1 and ad and abs(a1 - ad) > 50) or (b1 and bd and abs(b1 - bd) > 0.05):
                buy_mismatch.append((dt, b1, bd, q1, qd, a1, ad))
    print(f"건수: {len(buy_mismatch)}")
    for row in buy_mismatch[:25]:
        print(" ", row)
    if len(buy_mismatch) > 25:
        print(f"  ... 외 {len(buy_mismatch)-25}건")
    if buy_mismatch:
        print("  첫 불일치:", buy_mismatch[0])

    # 연속 구간에서 총자산 차이가 커지기 시작하는 지점
    print("\n=== 총자산 차이 누적 패턴 (처음 |차이|>1000인 날 전후 3일) ===")
    for dt, a, b, d in asset_diff:
        if abs(d) > 1000:
            idx = common.index(dt)
            for j in range(max(0, idx - 2), min(len(common), idx + 4)):
                dtx = common[j]
                r1, rd = m1[dtx], md[dtx]
                t1, td = parse_money(r1.get("총자산")), parse_money(rd.get("총자산"))
                print(f"  {dtx}  S1={t1:,.0f}  매매={td:,.0f}  diff={td-t1:,.0f}")
            break


if __name__ == "__main__":
    main()
