"""Run the dashboard JavaScript against fake DOM/fetch only; never trade."""
import json
import re
import shutil
import subprocess

import pytest

from kmw_engine_dashboard import _HTML


NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is needed for frontend transport checks")

STATUS = {"enabled": False, "running": False, "at": "17:01", "plan": None,
          "events": [], "broker_status": {"orders": []}}

# A small DOM implements only methods used by this dashboard.  All HTTP replies
# are supplied by the test; this process has no real server or brokerage client.
HARNESS = r"""
const fs=require('node:fs'),vm=require('node:vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
class Element {
  constructor(){this.children=[];this.textContent='';this.hidden=true;this.checked=false;this.disabled=false;this.value='17:01';}
  replaceChildren(...items){this.children=[...items];}
  append(...items){this.children.push(...items);}
  addEventListener(){}
}
const nodes=new Map(),calls=[],queue=[],pending={};let reads=0;
const node=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
const response=spec=>({status:spec.status??200,ok:(spec.status??200)>=200&&(spec.status??200)<300,
  text:async()=>{reads++;if(spec.readError)throw Error('secret read error');return spec.text??JSON.stringify(spec.json);}});
const context={console,URL,Date,Set,Error,JSON,Number,String,Array,Object,
  window:{location:{href:input.url||'http://127.0.0.1:8701/'}},
  document:{getElementById:node,querySelector:selector=>({content:selector.includes('engine-preset')?(input.preset||'KMW'):'fake-csrf'}),createElement:()=>new Element(),createTextNode:text=>({textContent:text})},
  setInterval:fn=>{context.poll=fn;},confirm:()=>false,pending,
  fetch:async(url,options={})=>{calls.push({url,method:options.method||'GET',body:options.body,redirect:options.redirect});
    const spec=queue.shift();if(!spec)throw Error('unexpected request');
    if(spec.networkError||(spec.redirect&&options.redirect==='error'))throw Error('secret connection error');
    if(spec.deferred)return new Promise(resolve=>{pending[spec.deferred]=()=>resolve(response(spec));});
    return response(spec);}};
vm.createContext(context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  queue.push(input.initialResponse||{json:input.initial});
  vm.runInContext(input.script,context);
  await tick();calls.length=0;reads=0;queue.push(...input.responses);
  const result=await vm.runInContext('(async()=>{'+input.code+'})()',context);
  await tick();
  process.stdout.write(JSON.stringify({result,calls,reads}));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


def frontend(code, responses=(), *, url=None, initial_response=None, preset="KMW"):
    script = re.search(r"<script>(.*?)</script>", _HTML, flags=re.S).group(1)
    payload = {"script": script, "initial": STATUS, "responses": list(responses),
               "code": code, "url": url, "initialResponse": initial_response, "preset": preset}
    result = subprocess.run([NODE, "-e", HARNESS], input=json.dumps(payload),
                            text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def output():
    return "return {error:$('block').textContent,hidden:$('block').hidden,info:$('message').textContent,enabled:current.enabled,at:current.at,busy,connectionReady,banner:$('connection').textContent,bannerHidden:$('connection').hidden,disabled:Object.fromEntries(['at','save-time','enable','preview','run','disable'].map(id=>[id,$(id).disabled]))};"


@pytest.mark.parametrize("status,text", [(200, ""), (403, ""), (502, "<!doctype html><p>proxy failure secret</p>")])
def test_empty_or_html_mutation_never_claims_success_or_retries(status, text):
    result = frontend("await action('/api/configure',{at:'18:25'},'저장 완료');" + output(),
                      [{"status": status, "text": text}])
    assert len(result["calls"]) == 1
    assert result["calls"][0]["method"] == "POST"
    assert result["reads"] == 1
    ui = result["result"]
    assert f"HTTP {status}" in ui["error"]
    assert "자동으로 재전송하지 않았습니다" in ui["error"]
    assert "secret" not in ui["error"]
    assert "Unexpected end" not in ui["error"]
    assert ui["info"] == ""
    assert ui["enabled"] is False
    assert ui["at"] == "17:01"
    assert ui["busy"] is False
    assert ui["connectionReady"] is False
    assert ui["bannerHidden"] is False
    assert "PC 엔진" in ui["banner"]


@pytest.mark.parametrize("value", [None, [], "wrong", 42, {}, {"ok": True}, {"ok": True, "status": {}}])
def test_valid_json_with_wrong_contract_never_reports_activation(value):
    result = frontend("await action('/api/enable',{confirm_live:true},'활성화 완료');" + output(),
                      [{"text": json.dumps(value)}])
    assert len(result["calls"]) == 1
    assert result["result"]["error"]
    assert result["result"]["info"] == ""
    assert result["result"]["enabled"] is False


def test_valid_configure_success_requires_confirmed_schedule():
    status = dict(STATUS, at="18:25")
    result = frontend("await action('/api/configure',{at:'18:25'},'저장 완료');" + output(),
                      [{"json": {"ok": True, "status": status}}])
    assert result["result"]["info"] == "저장 완료"
    assert result["result"]["error"] == ""
    assert result["result"]["at"] == "18:25"
    assert len(result["calls"]) == 1


@pytest.mark.parametrize("path,body", [("/api/enable", {"confirm_live": True}),
                                     ("/api/configure", {"at": "18:25"})])
def test_success_envelope_with_unchanged_setting_is_not_success(path, body):
    result = frontend(f"await action({json.dumps(path)},{json.dumps(body)},'성공');" + output(),
                      [{"json": {"ok": True, "status": STATUS}}])
    assert "변경된 설정" in result["result"]["error"]
    assert result["result"]["info"] == ""


@pytest.mark.parametrize("status,body", [(403, {"error": "관리 화면을 다시 여세요."}),
                                       (409, {"ok": False, "error": "다른 요청을 처리 중입니다."}),
                                       (200, {"ok": False, "error": "추천 가격이 교차합니다."})])
def test_json_api_error_remains_readable_and_never_retries(status, body):
    result = frontend("await action('/api/enable',{confirm_live:true},'활성화 완료');" + output(),
                      [{"status": status, "json": body}])
    assert result["result"]["error"] == body["error"]
    assert result["result"]["info"] == ""
    assert len(result["calls"]) == 1


@pytest.mark.parametrize("failure", [{"networkError": True}, {"readError": True}])
def test_transport_failure_is_sanitized_and_never_retries(failure):
    result = frontend("await action('/api/enable',{confirm_live:true},'활성화 완료');" + output(), [failure])
    assert "secret" not in result["result"]["error"]
    assert "자동으로 재전송하지 않았습니다" in result["result"]["error"]
    assert len(result["calls"]) == 1


def test_mutation_does_not_follow_or_forward_a_redirect():
    result = frontend("await action('/api/enable',{confirm_live:true},'활성화 완료');" + output(),
                      [{"redirect": True, "json": {"ok": True, "status": dict(STATUS, enabled=True)}}])
    assert result["calls"][0]["redirect"] == "error"
    assert len(result["calls"]) == 1
    assert result["result"]["enabled"] is False
    assert result["result"]["info"] == ""
    assert result["reads"] == 0


def test_command_error_survives_successful_status_poll():
    result = frontend("await action('/api/configure',{at:'18:25'},'저장 완료');await pollStatus();" + output(),
                      [{"status": 403, "text": ""}, {"json": STATUS}])
    assert "HTTP 403" in result["result"]["error"]
    assert result["result"]["hidden"] is False
    assert [call["method"] for call in result["calls"]] == ["POST", "GET"]


def test_new_successful_action_clears_old_command_error():
    result = frontend("await action('/api/configure',{at:'18:25'},'저장 완료');await pollStatus();await action('/api/preview',{},'추천 확인');" + output(),
                      [{"status": 403, "text": ""}, {"json": STATUS},
                       {"json": {"ok": True, "status": STATUS}}])
    assert result["result"]["error"] == ""
    assert result["result"]["info"] == "추천 확인"
    assert [call["method"] for call in result["calls"]] == ["POST", "GET", "POST"]


def test_read_poll_error_is_cleared_by_a_valid_status_without_hiding_engine_error():
    status = dict(STATUS, error="엔진 전송 차단")
    result = frontend("await pollStatus();await pollStatus();" + output(),
                      [{"text": ""}, {"json": status}])
    assert result["result"]["error"] == "엔진 전송 차단"


@pytest.mark.parametrize("url,prefix", [("http://127.0.0.1:8701/", "http://127.0.0.1:8701/?kmw_api=enable"),
                                       ("https://preview.example/private/8701/?ticket=fake&kmw_api=old#part", "https://preview.example/private/8701/?ticket=fake&kmw_api=enable")])
def test_api_uses_current_page_path_and_preserves_proxy_query(url, prefix):
    result = frontend("await action('/api/enable',{confirm_live:true},'활성화 완료');" + output(),
                      [{"json": {"ok": True, "status": dict(STATUS, enabled=True)}}], url=url)
    assert result["calls"][0]["url"] == prefix
    assert result["result"]["enabled"] is True


def test_urgent_stop_can_run_during_busy_action_without_repeating_other_mutations():
    code = "const preview=action('/api/preview',{},'추천 확인');await Promise.resolve();await action('/api/configure',{at:'18:25'},'저장');await action('/api/disable',{},'중지 완료',true);pending.preview();await preview;" + output()
    result = frontend(code, [{"deferred": "preview", "json": {"ok": True, "status": STATUS}},
                             {"json": {"ok": True, "status": STATUS}}])
    assert len(result["calls"]) == 2
    assert "kmw_api=preview" in result["calls"][0]["url"]
    assert "kmw_api=disable" in result["calls"][1]["url"]
    assert result["result"]["busy"] is False


@pytest.mark.parametrize("failure", [
    {"text": ""}, {"status": 404, "json": {"ok": False, "error": "페이지를 찾을 수 없습니다."}},
    {"networkError": True}, {"json": {}},
])
def test_initial_status_failure_disables_commands_and_sends_no_posts(failure):
    code = "$('live-ack').checked=true;updateButtons();await action('/api/configure',{at:'18:25'});await action('/api/enable',{confirm_live:true});await action('/api/preview');await action('/api/run',{confirm_live:true,review_id:'fake'});" + output()
    result = frontend(code, initial_response=failure)
    assert result["calls"] == []
    ui = result["result"]
    assert ui["connectionReady"] is False
    assert ui["bannerHidden"] is False
    assert "PC 엔진" in ui["banner"]
    assert "상태 응답" in ui["banner"]
    assert all(ui["disabled"][name] for name in ("at", "save-time", "enable", "preview", "run"))
    assert ui["enabled"] is False


def test_valid_status_reconnect_enables_controls_without_replaying_blocked_commands():
    code = "$('live-ack').checked=true;await action('/api/configure',{at:'18:25'});await pollStatus();" + output()
    result = frontend(code, [{"json": STATUS}], initial_response={"text": ""})
    assert [call["method"] for call in result["calls"]] == ["GET"]
    ui = result["result"]
    assert ui["connectionReady"] is True
    assert ui["bannerHidden"] is True
    assert all(not ui["disabled"][name] for name in ("at", "save-time", "enable", "preview"))
    assert ui["disabled"]["run"] is True  # Reconnection never enables the engine.
    assert ui["enabled"] is False
    assert ui["at"] == "17:01"


def test_failed_status_poll_locks_commands_until_fresh_status_confirms_connection():
    code = "await pollStatus();await action('/api/configure',{at:'18:25'});await action('/api/preview');" + output()
    result = frontend(code, [{"text": ""}])
    assert [call["method"] for call in result["calls"]] == ["GET"]
    assert result["result"]["connectionReady"] is False
    assert result["result"]["disabled"]["save-time"] is True


def test_uncertain_command_result_blocks_further_posts_until_status_is_confirmed():
    code = "await action('/api/configure',{at:'18:25'});await action('/api/preview');await action('/api/enable',{confirm_live:true});" + output()
    result = frontend(code, [{"text": ""}])
    assert [call["method"] for call in result["calls"]] == ["POST"]
    assert result["result"]["connectionReady"] is False
    assert result["result"]["bannerHidden"] is False


def test_last_known_enabled_engine_keeps_urgent_stop_available_after_connection_failure():
    enabled = dict(STATUS, enabled=True)
    code = "await pollStatus();const stopAvailable=!$('disable').disabled;await action('/api/preview');await action('/api/disable',{},'중지 완료',true);return {stopAvailable,connectionReady,enabled:current.enabled,info:$('message').textContent};"
    result = frontend(code, [{"text": ""}, {"json": {"ok": True, "status": STATUS}}],
                      initial_response={"json": enabled})
    assert result["result"]["stopAvailable"] is True
    assert [call["method"] for call in result["calls"]] == ["GET", "POST"]
    assert "kmw_api=disable" in result["calls"][-1]["url"]
    assert result["result"]["enabled"] is False
    assert result["result"]["connectionReady"] is True
    assert result["result"]["info"] == "중지 완료"


@pytest.mark.parametrize("status", [401, 403, 404, 500, 502, 503])
def test_auth_route_or_gateway_failure_blocks_followup_control_requests(status):
    code = "await action('/api/configure',{at:'18:25'});await action('/api/enable',{confirm_live:true});" + output()
    result = frontend(code, [{"status": status, "json": {"ok": False, "error": "연결 확인 필요"}}])
    assert [call["method"] for call in result["calls"]] == ["POST"]
    assert result["result"]["connectionReady"] is False
    assert result["result"]["disabled"]["enable"] is True


@pytest.mark.parametrize("preset", ["KMW2", "JEH"])
@pytest.mark.parametrize("received", [None, "KMW", "OTHER"])
def test_additional_account_requires_matching_status_marker_before_controls(preset, received):
    status = dict(STATUS)
    if received is not None:
        status["preset"] = received
    result = frontend(output(), initial_response={"json": status}, preset=preset)
    assert result["calls"] == []
    assert result["result"]["connectionReady"] is False
    assert result["result"]["disabled"]["enable"] is True
    assert "선택한 프리셋" in result["result"]["error"]


def test_jeh_matching_status_preserves_stale_preview_but_disables_live_submission():
    status = {**STATUS, "preset": "JEH", "enabled": True, "review_id": "fake-jeh-review",
              "plan": {"preset": "JEH", "status": "ready", "snapshot_fresh": False,
                       "warnings": ["Preview only"], "orders": [{"side": "SELL", "quantity": 10}]}}
    result = frontend(output(), initial_response={"json": status}, preset="JEH")
    assert result["result"]["connectionReady"] is True
    assert result["result"]["disabled"]["preview"] is False
    assert result["result"]["disabled"]["run"] is True
    assert result["calls"] == []
