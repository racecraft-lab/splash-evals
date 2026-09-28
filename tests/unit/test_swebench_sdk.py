"""Synthetic-only tests: no model service or credentials are needed."""

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from local_evals.swebench_sdk import MAX_OUTPUT, OutputBudget, sdk_chat_response

ROOT = Path(__file__).resolve().parents[2]
TRANSPORT = ROOT / "scripts/swebench-sdk/transport.mjs"
NODE = Path(shutil.which("node") or "/missing-node")


def body():
    return {
        "model": "test-local",
        "messages": [{"role": "user", "content": "synthetic"}],
        "temperature": 1,
        "top_p": 0.95,
        "max_tokens": MAX_OUTPUT,
        "reasoning_effort": "on",
        "stream": False,
    }


def invoke(script, budget=None, seconds=2):
    budget = budget or OutputBudget(MAX_OUTPUT)
    result = sdk_chat_response(
        body(),
        budget=budget,
        deadline=time.monotonic() + seconds,
        node=NODE,
        transport=script,
        abort_grace=0.1,
    )
    return result, budget


def test_budget_refuses_before_launch(tmp_path):
    with pytest.raises(ValueError, match="insufficient"):
        invoke(tmp_path / "does-not-exist.mjs", OutputBudget(MAX_OUTPUT - 1))


@pytest.mark.parametrize(
    "program",
    [
        "process.exit(0)",
        "console.log('invalid')",
        "process.stdout.write('x'.repeat(9*1024*1024))",
        "process.stderr.write('x'.repeat(70000))",
        "console.log(JSON.stringify({status:'completed',usage:{completion_tokens:-1},charged_output_tokens:-1}))",
        "console.log(JSON.stringify({status:'completed',usage:{completion_tokens:1,prompt_tokens:-1},charged_output_tokens:1}))",
        "console.log(JSON.stringify({status:'completed',usage:{completion_tokens:1,prompt_tokens:2,total_tokens:4},charged_output_tokens:1}))",
    ],
)
def test_bad_child_charges_reservation(tmp_path, program):
    script = tmp_path / "child.mjs"
    script.write_text(program)
    result, budget = invoke(script)
    assert result["status"] == "failed"
    assert result["choices"] == []
    assert result["usage"]["completion_tokens"] is None
    assert budget.remaining == 0


def test_timeout_reaps_child(tmp_path):
    pid = tmp_path / "pid"
    script = tmp_path / "child.mjs"
    script.write_text(
        "import fs from 'node:fs'; fs.writeFileSync("
        + json.dumps(str(pid))
        + ",String(process.pid)); process.on('SIGTERM',()=>{}); setInterval(()=>{},1000)"
    )
    started = time.monotonic()
    result, budget = invoke(script, seconds=0.3)
    assert time.monotonic() - started < 2
    assert result["choices"] == [] and budget.remaining == 0
    import os

    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_sdk_request_and_result_contract():
    # Exercise the actual exported JS boundary, including all applied fields.
    program = """
import {validateRequest, verifiedResponse, validateBinding} from TRANSPORT;
const request = REQUEST;
validateRequest(request);
if(validateRequest(request)!==request.body)throw Error('history was rewritten');
const badSettings=[['temperature',0],['top_p',1],['max_tokens',10],
 ['reasoning_effort','xhigh'],['stream',true]];
for (const [key,value] of badSettings) {
  let refused=false;
  try { validateRequest({...request,body:{...request.body,[key]:value}}); }
  catch { refused=true; }
  if(!refused) throw Error(key);
}
for(const endpoint of ['ws://localhost:1234','ws://127.0.0.1:9999','wss://example.com']) {
  let refused=false; try {validateRequest({...request,endpoint});} catch {refused=true;}
  if(!refused) throw Error('endpoint');
}
const field=(key,value)=>({key,value});
const result={content:'synthetic',modelInfo:{modelKey:'qwen3.8-27b-splash',
 identifier:'test-local',deviceIdentifier:null,format:'yuzu',instanceReference:'instance-1'},
 predictionConfig:{fields:[field('llm.prediction.temperature',1),field('llm.prediction.topPSampling',{checked:true,value:.95}),field('llm.prediction.maxPredictedTokens',{checked:true,value:65536}),field('llm.prediction.reasoning.enableThinking',true)]},
 loadConfig:{fields:[field('llm.load.contextLength',131072)]},stats:{stopReason:'eosFound',predictedTokensCount:12,promptTokensCount:4}};
const valid=verifiedResponse(result,'test-local','instance-1');
const before={...result.modelInfo,instanceReference:'instance-1',
 contextLength:131072,lastUsedTime:100};
validateBinding(before,{...before,lastUsedTime:200},'test-local');
for (const after of [{...before,instanceReference:'other'},{...before,contextLength:4096}]) {
 let refused=false;try{validateBinding(before,after,'test-local');}catch{refused=true;}
 if(!refused)throw Error('instance binding');
}
if(valid.usage.completion_tokens!==12||valid.charged_output_tokens!==12)throw Error('exact usage');
if(valid.evidence.stop_reason!=='eosFound')throw Error('stop reason evidence');
const unknown=verifiedResponse({...result,stats:{stopReason:'eosFound'}},'test-local','instance-1');
if(unknown.usage.completion_tokens!==null||unknown.charged_output_tokens!==65536)
 throw Error('unknown usage');
const contextLimited=verifiedResponse(
 {...result,stats:{...result.stats,stopReason:'contextLengthReached'}},
 'test-local','instance-1'
);
if(contextLimited.choices[0].finish_reason!=='length'||contextLimited.usage.prompt_tokens!==4||
 contextLimited.evidence.context_length!==131072||contextLimited.evidence.stop_reason!==
 'contextLengthReached')throw Error('context length completion');
const cancelled=verifiedResponse({...result,stats:{stopReason:'userStopped'}},
 'test-local','instance-1');
if(cancelled.choices.length||cancelled.charged_output_tokens!==65536)throw Error('cancel');
const oldShape={...valid,evidence:{...valid.evidence}};
delete oldShape.evidence.stop_reason;
const badResults=[r=>r.modelInfo.deviceIdentifier='remote',r=>r.modelInfo.identifier='wrong',
 r=>r.modelInfo.modelKey='wrong',r=>r.modelInfo.format='gguf',
 r=>r.modelInfo.instanceReference='other',r=>delete r.modelInfo.instanceReference,
 r=>r.stats.stopReason='unexpected',
 r=>r.loadConfig.fields[0].value=1,
 ...[0,1,2,3].map(i=>r=>r.predictionConfig.fields[i].value=null)];
for(const mutate of badResults) {
 const invalid=structuredClone(result);mutate(invalid);let refused=false;
 try {verifiedResponse(invalid,'test-local','instance-1');} catch {refused=true;}
 if(!refused)throw Error('unverified response escaped');
}
console.log(JSON.stringify({valid,unknown,contextLimited,cancelled,oldShape}));
""".replace("TRANSPORT", json.dumps(TRANSPORT.as_uri())).replace(
        "REQUEST", json.dumps({"endpoint": "ws://127.0.0.1:1234", "body": body()})
    )
    completed = subprocess.run(  # noqa: S603 - fixed synthetic JS, never model-generated commands.
        [str(NODE), "--input-type=module", "-e", program],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    responses = json.loads(completed.stdout)
    from local_evals.swebench_sdk import _checked_response

    for response in responses.values():
        checked = _checked_response(json.dumps(response).encode(), "test-local", False)
        assert checked["usage"].get("prompt_tokens") == response["usage"].get("prompt_tokens")
        if response["status"] == "completed":
            assert checked["choices"]
        else:
            assert not checked["choices"]


def test_cancelled_missing_usage_never_zero(tmp_path):
    script = tmp_path / "child.mjs"
    script.write_text(
        "console.log(JSON.stringify({status:'cancelled',choices:[],usage:{completion_tokens:null,prompt_tokens:null,total_tokens:null},charged_output_tokens:65536}))"
    )
    result, budget = invoke(script)
    assert result["choices"] == [] and result["usage"]["completion_tokens"] is None
    assert result["charged_output_tokens"] == MAX_OUTPUT and budget.remaining == 0


def test_unverified_failed_usage_cannot_refund(tmp_path):
    script = tmp_path / "child.mjs"
    script.write_text(
        "console.log(JSON.stringify({status:'failed',choices:[],usage:{completion_tokens:0},charged_output_tokens:0}))"
    )
    result, budget = invoke(script)
    assert result["usage"]["completion_tokens"] is None
    assert budget.remaining == 0


def test_baseexception_keeps_reservation(monkeypatch, tmp_path):
    import local_evals.swebench_sdk as sdk

    script = tmp_path / "child.mjs"
    script.write_text("setInterval(()=>{},1000)")
    budget = OutputBudget(MAX_OUTPUT)

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(sdk, "_exchange", interrupt)
    with pytest.raises(KeyboardInterrupt):
        invoke(script, budget)
    assert budget.remaining == 0


def test_bounded_reap_race_and_failure(monkeypatch):
    from unittest.mock import Mock

    import local_evals.swebench_sdk as sdk

    process = Mock(pid=999999, stdin=None, stdout=None, stderr=None)
    process.poll.return_value = None

    def exited(*args):
        raise ProcessLookupError

    monkeypatch.setattr(sdk.os, "killpg", exited)
    assert sdk._reap(process)
    process.wait.assert_called_once_with(timeout=1.0)
    process.wait.side_effect = subprocess.TimeoutExpired("synthetic", 1)
    assert not sdk._reap(process)


def test_unverified_cleanup_charges_full(monkeypatch, tmp_path):
    import local_evals.swebench_sdk as sdk

    script = tmp_path / "child.mjs"
    script.write_text("process.exit(0)")
    original = sdk._reap

    def unverified(process):
        original(process)
        return False

    monkeypatch.setattr(sdk, "_reap", unverified)
    result, budget = invoke(script)
    assert result["error"] == "child_cleanup_unverified"
    assert result["usage"]["completion_tokens"] is None
    assert result["choices"] == [] and budget.remaining == 0
