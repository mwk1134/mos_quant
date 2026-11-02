"""Read-only, responsive presentation of the web app's existing order rows."""
from html import escape


ORDER_TABLE_CSS = """<style>
.mix-order-board{--accent:#176f65;container-type:inline-size;color:#172b42;background:#fff;border:1px solid #dbe4ec;border-top:3px solid var(--accent);border-radius:14px;overflow:hidden;margin:4px 0 24px;font-size:15px;line-height:1.55}
.mix-order-board.r2{--accent:#365f9e}
.mix-order-overview{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:15px 20px 0}
.mix-order-source{display:inline-block;padding:4px 9px;border-radius:6px;background:#edf3f9;color:#354f6b;font-size:12px;font-weight:650}
.mix-order-counts{font-size:13px;color:#526578;font-variant-numeric:tabular-nums}
.mix-order-message{margin:10px 20px 15px;color:#42566c;font-size:14px;overflow-wrap:anywhere}
.mix-order-columns,.mix-order-main{display:grid;grid-template-columns:minmax(80px,.65fr) minmax(125px,1fr) minmax(180px,1.7fr) minmax(110px,.9fr);gap:16px;align-items:start}
.mix-order-columns{padding:9px 20px;background:#f3f6f9;border-top:1px solid #e4eaf0;border-bottom:1px solid #e4eaf0;color:#526578;font-size:12px;font-weight:650}
.mix-order-columns span:last-child{text-align:right}
.mix-order-item{padding:17px 20px;border-bottom:1px solid #e4eaf0}
.mix-order-item:last-child{border-bottom:0}
.mix-order-item *{box-sizing:border-box;min-width:0}
.mix-order-side{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.mix-order-badge{display:inline-block;padding:5px 11px;border-radius:7px;font-size:14px;font-weight:750;background:#edf1f5;color:#42566c}
.mix-order-badge.buy{background:#e7f4ee;color:#14613f}
.mix-order-badge.sell{background:#ffedeb;color:#a6322d}
.mix-order-receipt{font-size:12px;font-weight:650;color:#7b5717;background:#fff5df;border:1px solid #f0dfb8;border-radius:5px;padding:2px 6px}
.mix-order-draft{font-size:12px;color:#536e8a;background:#f0f4f8;border-radius:5px;padding:2px 6px}
.mix-order-type strong{display:block;font-size:16px;line-height:1.5;font-weight:700}
.mix-order-type small{display:block;color:#526578;font-size:12px;margin-top:2px}
.mix-order-price strong,.mix-order-quantity strong{display:block;font-size:21px;line-height:1.3;font-weight:750;letter-spacing:-.3px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.mix-order-price strong.long-value{font-size:17px;line-height:1.5;letter-spacing:0}
.mix-order-price small,.mix-order-quantity small{display:block;color:#526578;font-size:13px;margin-top:5px;overflow-wrap:anywhere}
.mix-order-quantity{text-align:right}
.mix-order-unit{font-size:14px;margin-left:3px;font-weight:500;letter-spacing:0}
.mix-order-details{margin-top:13px;padding:10px 12px;background:#f6f8fb;border-radius:8px;font-size:13px;line-height:1.65;overflow-wrap:anywhere}
.mix-order-detail{display:flex;align-items:baseline;gap:10px}
.mix-order-detail+.mix-order-detail{margin-top:4px}
.mix-order-detail-label{flex:0 0 28px;color:#617286;font-size:12px;font-weight:650}
.mix-order-detail-value{color:#2e455f;white-space:normal;overflow-wrap:anywhere}
.mix-order-detail.attention .mix-order-detail-value{color:#8b5311;font-weight:650}
.mix-order-mobile-label{display:none}
.mix-order-empty{display:flex;align-items:center;gap:12px;padding:19px 20px;border-top:1px solid #e4eaf0;color:#526578;font-size:14px}
@container(max-width:600px){
 .mix-order-columns{display:none}
 .mix-order-main{grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px 16px}
 .mix-order-type{text-align:right}
 .mix-order-mobile-label{display:block;margin-bottom:5px;font-size:12px;font-weight:600;color:#617286}
}
@media(max-width:640px){
 .mix-order-board{border-radius:12px;font-size:14px}
 .mix-order-overview{padding:14px 15px 0;gap:8px}
 .mix-order-message{margin:10px 15px 14px;font-size:13px;line-height:1.65}
 .mix-order-columns{display:none}
 .mix-order-item{padding:16px 15px;border-top:1px solid #e4eaf0;border-bottom:0}
 .mix-order-main{grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px 16px}
 .mix-order-type{text-align:right}
 .mix-order-type strong{font-size:15px}
 .mix-order-price strong,.mix-order-quantity strong{font-size:21px}
 .mix-order-mobile-label{display:block;margin-bottom:5px;font-size:12px;font-weight:600;color:#617286}
 .mix-order-details{font-size:13px;padding:10px;margin-top:14px}
 .mix-order-detail{gap:7px}
 .mix-order-empty{padding:17px 15px}
}
</style>"""


def render_order_table_html(table: dict, strategy: str) -> str:
    """Render existing terms without estimating fills, remaining shares or money."""
    table = table if isinstance(table, dict) else {}
    rows = table.get("rows")
    rows = rows if isinstance(rows, list) else []
    rows = [row for row in rows if isinstance(row, dict)]
    label = str(table.get("label") or ("ATR70 주문표" if strategy == "ATR" else "R2 주문표"))
    source = str(table.get("source") or "표시용 주문안")
    counts = []
    for side in ("매수", "매도"):
        count = sum(row.get("매매") == side for row in rows)
        if count:
            counts.append(f"{side} {count}건")
    count_text = " · ".join(counts) if counts else "현재 주문 없음"
    kind_labels = {"MARKET": "시장가", "LOC": "종가 지정가", "MOO": "시가 시장가", "MOC": "종가 시장가"}
    parts = [
        f'<section class="mix-order-board {"atr" if strategy == "ATR" else "r2"}" data-order-strategy="{escape(strategy)}" aria-label="{escape(label)}">',
        f'<div class="mix-order-overview"><span class="mix-order-source">{escape(source)}</span><span class="mix-order-counts">{escape(count_text)}</span></div>',
    ]
    if table.get("message"):
        parts.append(f'<p class="mix-order-message">{escape(str(table["message"]))}</p>')
    active = [row for row in rows if row.get("매매") in {"매수", "매도"}]
    if not active and not any(row.get("수량") not in (None, "", "-") for row in rows):
        parts.append('<div class="mix-order-empty"><span class="mix-order-badge">대기</span><span>현재 주문이 없습니다.</span></div>')
    else:
        parts.append('<div class="mix-order-columns" aria-hidden="true"><span>매매</span><span>주문 유형</span><span>기준가격</span><span>주문수량</span></div>')
        for index, row in enumerate(rows, 1):
            side = str(row.get("매매") or "대기")
            badge_class = "buy" if side == "매수" else "sell" if side == "매도" else ""
            kind = str(row.get("유형") or "-")
            price = str(row.get("기준가격") or "-")
            price_note = ""
            if kind == "MARKET":
                price_note = str(row.get("가격설명") or price)
                price = "시장가"
            quantity = row.get("수량")
            numeric_quantity = type(quantity) is int and quantity > 0
            quantity_note = ""
            if numeric_quantity:
                quantity_text = f"{quantity:,}"
            else:
                original = str(quantity or "-")
                quantity_text = "미정" if original in {"시가 확정 후 산정", "미정"} else original
                quantity_note = original if quantity_text != original else ""
            condition = str(row.get("조건") or "")
            status = str(row.get("상태") or row.get("조건·상태") or "")
            attention = any(word in status for word in ("확인 필요", "접수 확인 중", "부분체결", "주문 거절"))
            parts.append(f'<article class="mix-order-item" aria-label="{escape(label)} {index}번째 주문">')
            parts.append('<div class="mix-order-main">')
            receipt = ('<span class="mix-order-receipt">저장 주문</span>' if row.get("기존주문")
                       else '<span class="mix-order-draft">주문안</span>' if side in {"매수", "매도"} else "")
            parts.append(f'<div class="mix-order-side"><span class="mix-order-badge {badge_class}">{escape(side)}</span>{receipt}</div>')
            parts.append(f'<div class="mix-order-type"><strong>{escape(kind)}</strong><small>{escape(kind_labels.get(kind, ""))}</small></div>')
            price_class = "long-value" if len(price) > 16 else ""
            note_html = f'<small>{escape(price_note)}</small>' if price_note else ""
            parts.append(f'<div class="mix-order-price"><span class="mix-order-mobile-label">기준가격</span><strong class="{price_class}">{escape(price)}</strong>{note_html}</div>')
            unit = '<span class="mix-order-unit">주</span>' if numeric_quantity else ""
            note_html = f'<small>{escape(quantity_note)}</small>' if quantity_note else ""
            parts.append(f'<div class="mix-order-quantity"><span class="mix-order-mobile-label">주문수량</span><strong>{escape(quantity_text)}{unit}</strong>{note_html}</div></div>')
            if condition or status:
                parts.append('<div class="mix-order-details">')
                for title, value in (("조건", condition), ("상태", status)):
                    if value:
                        style = " attention" if title == "상태" and attention else ""
                        parts.append(f'<div class="mix-order-detail{style}"><span class="mix-order-detail-label">{title}</span><span class="mix-order-detail-value">{escape(value)}</span></div>')
                parts.append('</div>')
            parts.append('</article>')
    parts.append('</section>')
    return "".join(parts)
