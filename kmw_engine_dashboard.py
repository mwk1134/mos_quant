"""Loopback-only dashboard for a user-controlled KMW order engine.

Importing this module does not start a server or contact a brokerage.  The
dashboard delegates all planning and execution to the engine supplied by the
caller, and requires a browser acknowledgement for each live action.
"""
from __future__ import annotations

import hmac
import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit


_HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="__CSRF_TOKEN__">
<title>KMW 자동매매</title>
<style>
:root{color-scheme:light;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;color:#172b42;background:#edf2f7}
*{box-sizing:border-box}body{margin:0}main{max-width:1050px;margin:0 auto;padding:28px 22px 45px}h1{font-size:27px;margin:0 0 7px}h2{font-size:19px;margin:0 0 14px}p{line-height:1.65;margin:6px 0}.muted{color:#526578;font-size:14px}.card{background:white;border:1px solid #dbe3eb;border-radius:15px;padding:22px;margin-top:18px;box-shadow:0 3px 15px #20344a06}.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.space{justify-content:space-between}.status{border-radius:30px;padding:6px 13px;background:#e9edf1;font-size:14px;font-weight:700}.status.active{background:#e0f3e8;color:#157743}.status.running{background:#e2edff;color:#1755a3}.label{display:block;color:#526578;font-size:13px;margin-bottom:5px}.value{font-size:16px;font-weight:600}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-top:21px}button{border:1px solid #ccd7e2;border-radius:9px;padding:11px 16px;font:inherit;font-size:14px;font-weight:650;background:white;color:#23384e;cursor:pointer}button.primary{background:#145fbe;color:white;border-color:#145fbe}button.danger{color:#b32929;border-color:#e5c5c5}button:disabled{opacity:.45;cursor:not-allowed}button:not(:disabled):hover{filter:brightness(.96)}.ack{display:flex;gap:10px;align-items:flex-start;margin:20px 0 16px;font-size:14px;line-height:1.55}.ack input{margin-top:4px;width:17px;height:17px;accent-color:#145fbe}input[type=time]{font:inherit;padding:10px;border:1px solid #ccd7e2;border-radius:8px;background:white;color:#23384e}.notice{border-radius:9px;padding:12px 14px;line-height:1.6;font-size:14px;margin-top:14px;white-space:pre-wrap;overflow-wrap:anywhere}.notice.info{background:#eef5fd;color:#214e7e}.notice.error{background:#fff0ec;color:#9d3620}.notice[hidden]{display:none}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:14px}th{text-align:left;color:#5b6d7e;font-size:12px;font-weight:650;padding:11px 9px;border-bottom:1px solid #dbe3eb}td{padding:13px 9px;border-bottom:1px solid #edf1f5}th.num,td.num{text-align:right}.buy{color:#b83b36;font-weight:700}.sell{color:#2465ae;font-weight:700}.empty{text-align:center;color:#6a7a8b;padding:25px}.events{list-style:none;padding:0;margin:0}.events li{padding:11px 0;border-bottom:1px solid #edf1f5;line-height:1.55;font-size:14px;overflow-wrap:anywhere}.event-time{font-size:12px;color:#6a7a8b;margin-right:9px}.footer{margin-top:20px;color:#6a7a8b;font-size:12px}#receipt{font-size:13px;line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere;margin:12px 0 0}#basis{margin-bottom:12px}@media(max-width:600px){main{padding:20px 13px 35px}.card{padding:17px}h1{font-size:23px}.stats{grid-template-columns:1fr;gap:12px}.actions button{flex:1}td,th{padding-left:6px;padding-right:6px}}
</style>
</head>
<body><main>
<header><h1>KMW 자동매매</h1><p class="muted">웹앱과 같은 KMW 추천을 확인하고, 지정한 한국 시간에 메리츠 API로 주문합니다.</p></header>
<section class="card">
<div class="row space"><h2 style="margin:0">엔진 상태</h2><span id="engine-state" class="status">비활성</span></div>
<div class="stats"><div><span class="label">자동 주문 시간 · 한국 시간</span><span id="schedule" class="value">—</span></div><div><span class="label">다음 실행</span><span id="next-run" class="value">—</span></div><div><span class="label">최근 실행</span><span id="last-run" class="value">—</span></div></div>
<div class="row" style="margin-top:20px"><label for="at" class="muted">주문 시간</label><input id="at" type="time" value="17:01" required><button id="save-time" type="button">시간 저장</button></div>
<label class="ack"><input id="live-ack" type="checkbox"><span>실제 계좌 주문 활성화에 동의합니다. 활성화하면 중지할 때까지 지정한 시간에 KMW 추천 주문을 전송합니다.</span></label>
<div class="row actions"><button id="enable" class="primary" disabled>활성화</button><button id="disable" class="danger" disabled>중지</button><button id="preview">추천 새로고침</button><button id="run" disabled>지금 주문</button></div>
<div id="message" class="notice info" role="status" hidden></div>
<div id="block" class="notice error" role="alert" hidden></div>
<p class="muted" style="margin-top:16px">추천 가격과 수량을 그대로 전송합니다. LOC는 종가 조건에 따라 체결되며, 주문 접수만으로 체결이 완료되지는 않습니다.</p>
<p class="muted">현재 엔진은 미국 정규장 개장 전 추천을 사용합니다. 설정한 시각이 개장 이후이면 주문을 건너뜁니다.</p>
</section>
<section class="card"><div class="row space"><h2>KMW 추천 주문표</h2><span id="plan-status" class="muted">추천을 새로고침하세요.</span></div><p id="basis" class="muted">기준일 — · 주문일 —</p><div class="table-wrap"><table><thead><tr><th>구분</th><th>종목</th><th class="num">회차</th><th class="num">수량</th><th>주문 방식</th><th class="num">가격 (USD)</th></tr></thead><tbody id="orders"><tr><td colspan="6" class="empty">아직 확인한 추천이 없습니다.</td></tr></tbody></table></div><div id="warnings" class="notice info" hidden></div></section>
<section class="card"><h2>주문 접수 · 체결 상태</h2><p class="muted">접수 결과와 체결 내역은 다릅니다. 아래 체결 수량은 최근 API 조회 결과이며, 메리츠 앱에서도 확인할 수 있습니다.</p><p id="broker-checked" class="muted">최근 조회 —</p><div class="table-wrap"><table><thead><tr><th>주문번호</th><th>구분</th><th>종목</th><th class="num">주문 수량</th><th class="num">체결 수량</th><th>상태</th></tr></thead><tbody id="broker-orders"><tr><td colspan="6" class="empty">아직 조회한 주문이 없습니다.</td></tr></tbody></table></div><div id="receipt" hidden></div></section>
<section class="card"><h2>실행 기록</h2><ul id="events" class="events"><li class="muted">실행 기록이 없습니다.</li></ul></section>
<p class="footer">이 화면은 이 PC의 127.0.0.1에서만 열립니다. 중지는 이후 자동 주문을 멈추며 이미 접수한 주문을 취소하지 않습니다.</p>
</main>
<script>
const token=document.querySelector('meta[name="csrf-token"]').content;
const $=id=>document.getElementById(id);
let current={enabled:false,running:false,plan:null},busy=false,pendingRequests=0,initialTime=true;
let commandError='',statusError='',engineError='';
function dateText(value){if(!value)return '—';if(typeof value==='object')value=value.finished_at||value.started_at||value.at||value.timestamp||value.time;if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?String(value):d.toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});}
function renderErrors(){const text=[...new Set([commandError,statusError,engineError].filter(Boolean))].join('\n\n');$('block').textContent=text;$('block').hidden=!text;}
function message(text,isError=false){if(isError){commandError=text;renderErrors();return;}const node=$('message');node.textContent=text;node.hidden=!text;}
function apiUrl(path){const url=new URL(window.location.href);url.searchParams.set('kmw_api',path.split('/').pop());url.hash='';return url.href;}
function transportMessage(detail,mutation=false){return detail+' 관리 화면을 다시 열어 최신 상태를 확인하세요.'+(mutation?' 처리 결과를 확인하기 전에는 같은 요청을 다시 누르지 마세요. 요청을 자동으로 재전송하지 않았습니다.':'');}
function isObject(value){return value!==null&&typeof value==='object'&&!Array.isArray(value);}
async function readApiResponse(response,mutation=false){const http='HTTP '+response.status;let text;try{text=await response.text();}catch{throw new Error(transportMessage('엔진 응답을 읽지 못했습니다 ('+http+').',mutation));}if(!text.trim())throw new Error(transportMessage('엔진 응답이 비어 있습니다 ('+http+').',mutation));let result;try{result=JSON.parse(text);}catch{throw new Error(transportMessage('엔진 대신 다른 페이지가 응답했습니다 ('+http+').',mutation));}if(!isObject(result))throw new Error(transportMessage('엔진 응답 형식이 올바르지 않습니다 ('+http+').',mutation));if(!response.ok||result.ok===false||result.error){const detail=typeof result.error==='string'&&result.error.trim()?result.error:'요청을 처리하지 못했습니다 ('+http+').';throw new Error(detail);}return result;}
async function apiRequest(path,options={},mutation=false){let response;try{response=await fetch(apiUrl(path),mutation?{...options,redirect:'error'}:options);}catch{throw new Error(transportMessage('엔진에 연결하지 못했습니다.',mutation));}return readApiResponse(response,mutation);}
function checkedStatus(status,mutation=false){if(!isObject(status)||typeof status.enabled!=='boolean'||typeof status.running!=='boolean'||typeof status.at!=='string'||!/^([01]\d|2[0-3]):[0-5]\d$/.test(status.at))throw new Error(transportMessage('엔진 상태를 확인할 수 없습니다.',mutation));return status;}
function updateButtons(){const running=busy||current.running;$('enable').disabled=running||current.enabled||!$('live-ack').checked;$('disable').disabled=!current.enabled;$('preview').disabled=running;$('run').disabled=running||!current.enabled||!current.review_id||!current.plan||current.plan.status!=='ready'||!(current.plan.orders||[]).length;$('save-time').disabled=running;}
function stateText(state){return {DIRECT_ACCEPTED:'주문 접수 완료',RESERVED:'예약 접수',OPEN:'미체결',FILLED:'체결 완료',PARTIALLY_FILLED:'일부 체결',CANCELLED:'취소 완료',BLOCKED:'전송 차단',REJECTED:'거절',UNKNOWN:'결과 확인 필요',ERROR:'오류',STOPPED:'중지',SKIPPED:'주문 대상 없음',NOT_SENT:'미전송',COMPLETED:'실행 완료'}[state]||state||'—';}
function eventText(event){if(typeof event==='string')return event;return event.text||event.message||event.error||event.detail||stateText(event.state)||event.type||'실행 기록';}
function render(status){current=status;const badge=$('engine-state');badge.textContent=status.running?'실행 중':status.enabled?'활성':'비활성';badge.className='status'+(status.running?' running':status.enabled?' active':'');$('schedule').textContent=(status.at||'17:01')+' 매일';$('next-run').textContent=dateText(status.next_run);$('last-run').textContent=dateText(status.last_run);if(initialTime&&status.at){$('at').value=status.at;initialTime=false;}
 const plan=status.plan||{};$('plan-status').textContent=plan.status==='ready'?'추천 '+(plan.orders||[]).length+'건':plan.reason||'추천을 새로고침하세요.';$('basis').textContent='기준일 '+(plan.basis_date||'—')+' · 주문일 '+(plan.order_date||'—');const body=$('orders');body.replaceChildren();for(const order of plan.orders||[]){const row=document.createElement('tr');for(const [index,value] of [order.side==='BUY'?'매수':order.side==='SELL'?'매도':order.side,order.ticker,order.round,order.quantity,order.order_type,order.order_type==='MOC'?'종가':order.price].entries()){const cell=document.createElement('td');cell.textContent=value??'—';if([2,3,5].includes(index))cell.className='num';if(index===0)cell.className=order.side==='BUY'?'buy':'sell';row.append(cell);}body.append(row);}if(!body.children.length){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=6;cell.className='empty';cell.textContent='전송할 추천 주문이 없습니다.';row.append(cell);body.append(row);}const warnings=Array.isArray(plan.warnings)?plan.warnings:[];$('warnings').textContent=warnings.join('\n');$('warnings').hidden=!warnings.length;const error=status.error||status.last_error||status.blocked_reason||status.last_run?.error||status.broker_status?.error;engineError=error?String(error):'';renderErrors();
 const broker=status.broker_status||{},brokerRows=broker.orders||[],brokerBody=$('broker-orders');$('broker-checked').textContent='최근 조회 '+dateText(broker.checked_at);brokerBody.replaceChildren();for(const order of brokerRows){const row=document.createElement('tr');for(const [index,value] of [order.order_no,order.side==='BUY'?'매수':order.side==='SELL'?'매도':order.side,order.ticker,order.quantity,order.filled_quantity,stateText(order.state)].entries()){const cell=document.createElement('td');cell.textContent=value??'—';if([3,4].includes(index))cell.className='num';row.append(cell);}brokerBody.append(row);}if(!brokerBody.children.length){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=6;cell.className='empty';cell.textContent='아직 조회한 주문이 없습니다.';row.append(cell);brokerBody.append(row);}
 const receipt=$('receipt');receipt.hidden=!status.last_run;if(status.last_run){const run=status.last_run;receipt.textContent=typeof run==='string'?run:`${dateText(run.finished_at)} · ${stateText(run.state)} · ${(run.orders||[]).length}건${run.error?'\n'+run.error:''}`;}const events=$('events');events.replaceChildren();for(const event of (status.events||[]).slice(-20).reverse()){const li=document.createElement('li'),when=document.createElement('span');when.className='event-time';when.textContent=dateText(typeof event==='object'&&(event.at||event.timestamp||event.time));li.append(when,document.createTextNode(eventText(event)));events.append(li);}if(!events.children.length){const li=document.createElement('li');li.className='muted';li.textContent='실행 기록이 없습니다.';events.append(li);}updateButtons();}
async function refresh(){const result=await apiRequest('/api/status',{cache:'no-store'});const status=checkedStatus(result);statusError='';render(status);}
async function pollStatus(){try{await refresh();}catch(error){statusError=error.message;renderErrors();}}
async function action(path,body={},success='',urgent=false){if(busy&&!urgent)return;pendingRequests++;busy=true;commandError='';renderErrors();updateButtons();message('');try{const result=await apiRequest(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(body)},true);if(result.ok!==true)throw new Error(transportMessage('요청 처리 결과를 확인할 수 없습니다.',true));const status=checkedStatus(result.status,true);if((path==='/api/enable'&&!status.enabled)||(path==='/api/disable'&&status.enabled)||(path==='/api/configure'&&status.at!==body.at))throw new Error(transportMessage('변경된 설정을 확인할 수 없습니다.',true));render(status);if(success)message(success);}catch(error){message(error.message,true);}finally{pendingRequests--;busy=pendingRequests>0;updateButtons();}}
$('live-ack').addEventListener('change',updateButtons);
$('enable').onclick=()=>{if($('live-ack').checked)action('/api/enable',{confirm_live:true},'자동 주문을 활성화했습니다. 중지할 때까지 지정 시간에 실행합니다.');};
$('disable').onclick=()=>action('/api/disable',{},'자동 주문을 중지했습니다. 이미 접수한 주문은 유지됩니다.',true);
$('preview').onclick=()=>action('/api/preview',{},'KMW 추천을 새로 확인했습니다.');
$('save-time').onclick=()=>{const at=$('at').value;if(!/^([01]\d|2[0-3]):[0-5]\d$/.test(at)){message('주문 시간을 HH:MM 형식으로 입력하세요.',true);return;}action('/api/configure',{at},'주문 시간을 저장했습니다.');};
$('run').onclick=()=>{const plan=current.plan||{},orders=plan.orders||[],review_id=current.review_id;if(!current.enabled||!review_id||plan.status!=='ready'||!orders.length)return;const lines=orders.map(o=>`${o.side==='BUY'?'매수':'매도'} ${o.ticker} ${o.quantity}주 · ${o.order_type} ${o.order_type==='MOC'?'종가':'$'+o.price} · ${o.round}회차`).join('\n');if(confirm(`실제 계좌에 다음 KMW 주문 ${orders.length}건을 지금 전송합니다.\n주문일: ${plan.order_date||'—'}\n\n${lines}\n\n계속하시겠습니까?`))action('/api/run',{confirm_live:true,review_id},'실행 결과를 확인하세요. 주문 접수와 체결은 다릅니다.');};
pollStatus();setInterval(pollStatus,5000);
</script></body></html>"""


class DashboardServer(ThreadingHTTPServer):
    """A localhost HTTP server.  Construction never activates its engine."""

    daemon_threads = True

    def __init__(self, engine: Any, host: str = "127.0.0.1", port: int = 8701):
        if host != "127.0.0.1":
            raise ValueError("KMW dashboard must bind to 127.0.0.1")
        self.engine = engine
        self.csrf_token = secrets.token_urlsafe(32)
        self.mutation_lock = threading.Lock()
        super().__init__((host, port), _Handler)
        self.allowed_host = f"127.0.0.1:{self.server_port}"
        self.origin = f"http://{self.allowed_host}"


class _Handler(BaseHTTPRequestHandler):
    server: DashboardServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, _format: str, *args: Any) -> None:
        # Request logging is intentionally quiet; engine events are the audit UI.
        pass

    def _respond(self, code: int, body: Any, *, html: bool = False) -> None:
        payload = body.encode("utf-8") if html else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-KMW-Engine", "1")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(payload)
        # Only route names and result codes are logged. Never include URLs,
        # request bodies, origin addresses, CSRF tokens, or account data.
        route = self._route()
        if route in {"/api/configure", "/api/enable", "/api/disable", "/api/run"}:
            print(f"KMW control: {self.command} {route} -> HTTP {code}, {len(payload)} bytes", flush=True)

    def _error(self, code: int, message: str) -> None:
        self._respond(code, {"ok": False, "error": message})

    def _route(self) -> str:
        """Keep API requests on the page URL when viewed through a path proxy.

        Query routing does not change HTTP methods: activation and submission
        are still POST-only with the original origin/CSRF checks.
        """
        parsed = urlsplit(self.path)
        if parsed.path == "/":
            values = parse_qs(parsed.query).get("kmw_api", [])
            if len(values) == 1 and values[0] in {"status", "preview", "configure", "enable", "disable", "run"}:
                return "/api/" + values[0]
        return parsed.path

    def _host_valid(self) -> bool:
        # Reject duplicate Host headers and DNS rebinding to this loopback port.
        if self.headers.get_all("Host") != [self.server.allowed_host]:
            self._error(403, "허용되지 않은 주소입니다. 127.0.0.1 대시보드에서 실행하세요.")
            return False
        return True

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None:
            return self.headers.get_all("Origin") == [self.server.origin]
        referer = self.headers.get("Referer")
        if not referer or len(self.headers.get_all("Referer", [])) != 1:
            return False
        try:
            parsed = urlsplit(referer)
            return parsed.scheme == "http" and parsed.netloc == self.server.allowed_host and not parsed.username and not parsed.password
        except ValueError:
            return False

    def do_GET(self) -> None:
        if not self._host_valid():
            return
        route = self._route()
        if route == "/":
            self._respond(200, _HTML.replace("__CSRF_TOKEN__", self.server.csrf_token), html=True)
        elif route == "/api/status":
            try:
                self._respond(200, self.server.engine.status())
            except Exception:
                self._error(500, "엔진 상태를 읽을 수 없습니다.")
        else:
            self._error(404, "페이지를 찾을 수 없습니다.")

    def do_POST(self) -> None:
        if not self._host_valid():
            return
        if not self._same_origin():
            self._error(403, "대시보드와 같은 주소에서 요청해야 합니다.")
            return
        tokens = self.headers.get_all("X-CSRF-Token", [])
        if len(tokens) != 1 or not hmac.compare_digest(tokens[0].encode("utf-8"), self.server.csrf_token.encode("ascii")):
            self._error(403, "요청 인증에 실패했습니다. 대시보드를 새로고침하세요.")
            return
        methods = {"/api/preview": "preview", "/api/enable": "enable", "/api/disable": "disable", "/api/run": "run_now", "/api/configure": "configure"}
        method = methods.get(self._route())
        if method is None:
            self._error(404, "지원하지 않는 요청입니다.")
            return
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                raise ValueError
            length = int(lengths[0])
            if not 0 < length <= 16384:
                raise ValueError
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            self._error(400, "JSON 요청 형식이 올바르지 않습니다.")
            return
        if method in {"enable", "run_now"} and body.get("confirm_live") is not True:
            self._error(400, "실제 계좌 주문에 대한 명시적인 확인이 필요합니다.")
            return
        if method == "run_now" and (not isinstance(body.get("review_id"), str) or not body["review_id"].strip()):
            self._error(400, "추천 주문표를 새로 확인한 후 실행하세요.")
            return
        if method == "configure" and (not isinstance(body.get("at"), str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", body["at"])):
            self._error(400, "주문 시간을 HH:MM 형식으로 입력하세요.")
            return
        # Disabling must be available while a long order batch is running.
        # The engine uses its own state lock and checks this stop between orders.
        acquired = method != "disable"
        if acquired and not self.server.mutation_lock.acquire(blocking=False):
            self._error(409, "다른 요청을 처리 중입니다. 완료 후 상태를 확인하세요.")
            return
        try:
            function = getattr(self.server.engine, method)
            if method == "configure":
                result = function(at=body["at"])
            elif method == "run_now":
                result = function(expected_review_id=body["review_id"])
            else:
                result = function()
            code = 200
            response = {"ok": True, "result": result, "status": self.server.engine.status()}
        except Exception as exc:
            # Only the engine's explicit public errors are safe to display.
            # Arbitrary transport exceptions can contain tokens or request URLs.
            from kmw_engine import EngineStateError
            from kmw_engine_backend import EngineBlocked
            message = str(exc) if isinstance(exc, (EngineStateError, EngineBlocked)) else "엔진 요청을 처리하지 못했습니다. 실행 기록을 확인하세요."
            code = 409
            response = {"ok": False, "error": message}
        finally:
            if acquired:
                self.server.mutation_lock.release()
        # Receiving a completed response must mean the mutation lock is free.
        # This also avoids rejecting a quick follow-up after a failed request.
        self._respond(code, response)


def create_server(engine: Any, host: str = "127.0.0.1", port: int = 8701) -> DashboardServer:
    """Return an unstarted loopback server for the supplied engine."""
    return DashboardServer(engine, host=host, port=port)
