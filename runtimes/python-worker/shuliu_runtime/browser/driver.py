"""Private NDJSON entrypoint. Only the trusted host launches this installed code.

The actor API never creates a browser-use Agent, LLM, cloud browser or Secret client.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import sys

# Keep upstream diagnostics out of the protocol and evidence channel.
PROTOCOL = sys.stdout
sys.stdout = sys.stderr
logging.disable(logging.CRITICAL)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shuliu_runtime.browser.diagnostics import failure_diagnostic
from shuliu_runtime.browser.verification import BrowserError, check_url, safe_text


PAGE = """() => {
 const clone = document.body?.cloneNode(true);
 clone?.querySelectorAll('script,style,noscript,input,textarea,select,[data-sensitive],[hidden],[aria-hidden="true"]').forEach(e=>e.remove());
 return {url:location.href,account:document.querySelector('[data-account]')?.getAttribute('data-account')||'',
 text:(clone?.textContent||'').slice(0,8000),scroll_y:window.scrollY};
}"""
ELEMENT = """() => {
 const e=this; const sensitive=!!e.closest('[data-sensitive]')||['password','file','hidden'].includes(e.type);
 const visible=!!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
 return {tag:e.tagName.toLowerCase(),type:e.type||'',sensitive,
 visible:visible && e.isConnected && !e.disabled,
 binding:{href:e.tagName==='A'?e.href:'',target:e.target||'',name:e.name||'',
 form_action:e.form?(e.hasAttribute('formaction')?e.formAction:e.form.action):'',
 form_method:e.form?(e.hasAttribute('formmethod')?e.formMethod:e.form.method):'',
 form_target:e.form?(e.hasAttribute('formtarget')?e.formTarget:e.form.target):''},
 label:sensitive?'':(e.getAttribute('aria-label')||e.innerText||e.getAttribute('placeholder')||'').slice(0,256)};
}"""

PREFLIGHT = """(expected, operation) => {
 const info=(""" + ELEMENT + """)();
 if(location.href!==expected.page.url)return {error:'BROWSER_TARGET_CHANGED'};
 if((document.querySelector('[data-account]')?.getAttribute('data-account')||'')!==expected.page.account)
   return {error:'BROWSER_ACCOUNT_CHANGED'};
 if(!info.visible||info.sensitive)return {error:'BROWSER_ELEMENT_STALE'};
 for(const key of ['tag','type','label','binding']){
   if(JSON.stringify(info[key])!==JSON.stringify(expected.element[key]))return {error:'BROWSER_ELEMENT_STALE'};
 }
 if(operation==='type')return document.activeElement===this?{}:{error:'BROWSER_ELEMENT_STALE'};
 const r=this.getBoundingClientRect();
 const left=Math.max(0,r.left),right=Math.min(innerWidth,r.right);
 const top=Math.max(0,r.top),bottom=Math.min(innerHeight,r.bottom);
 if(right<=left||bottom<=top)return {error:'BROWSER_ELEMENT_STALE'};
 const x=(left+right)/2,y=(top+bottom)/2,hit=document.elementFromPoint(x,y);
 if(!hit||!(hit===this||this.contains(hit)))return {error:'BROWSER_ELEMENT_OBSCURED'};
 return {x,y};
}"""


_LAUNCH_ARGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-extensions",
    "--disable-component-update",
    "--disable-sync",
    "--no-pings",
]


def launch_options() -> dict:
    """Chrome launch kwargs for browser-use's Browser constructor.

    The Chromium sandbox stays ON by default. Hosted CI runners (Linux
    containers, macOS images) often lack the Chromium SUID sandbox helper,
    where a sandboxed headless Chrome fails to start or hangs; CI may set
    SHULIU_BROWSER_ALLOW_NO_SANDBOX=1 to opt out explicitly. That flag is a
    per-environment deployment choice — it is never enabled by default and the
    CDP-level navigation guards in this driver do not depend on the Chromium
    sandbox.
    """
    options: dict = {"chromium_sandbox": True, "args": list(_LAUNCH_ARGS)}
    if os.environ.get("SHULIU_BROWSER_ALLOW_NO_SANDBOX") == "1":
        options["chromium_sandbox"] = False
        options["args"] = options["args"] + [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
        ]
    return options


class Driver:
    def __init__(self):
        self.browser = self.page = None
        self.allowed = frozenset()
        self.pending = set()
        self.stage = "idle"
        self.main_frame_id = None
        self.navigation_denied = False
        self.request_id = None

    def report_progress(self, stage):
        self.stage = stage
        if self.request_id is not None:
            PROTOCOL.write(json.dumps({"id": self.request_id, "progress": {"stage": stage}}) + "\n")
            PROTOCOL.flush()

    async def request(self, operation, payload):
        if operation == "start":
            self.report_progress("import_browser")
            from browser_use import Browser
            self.allowed = frozenset(payload["allowed_origins"])
            self.report_progress("construct_browser")
            self.browser = Browser(headless=True, executable_path=payload["executable_path"],
                user_data_dir=payload["profile"], downloads_path=str(Path(payload["profile"]) / "downloads"),
                use_cloud=False, enable_default_extensions=False, accept_downloads=False,
                disable_security=False, keep_alive=False,
                ignore_default_args=True, **launch_options())
            self.report_progress("start_browser")
            await self.browser.start()
            self.report_progress("create_page")
            self.page = await self.browser.new_page()
            session_id = await self.page.session_id
            client = self.browser.cdp_client
            frame_tree = await client.send.Page.getFrameTree(session_id=session_id)
            self.main_frame_id = frame_tree["frameTree"]["frame"]["id"]
            self.report_progress("register_guards")
            client.register.Fetch.requestPaused(self._intercept)
            client.register.Target.attachedToTarget(self._target_attached)
            self.report_progress("configure_targets")
            await client.send.Target.setAutoAttach({"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True})
            self.report_progress("configure_network_guard")
            await client.send.Fetch.enable({"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}, session_id=session_id)
            await client.send.Network.setBypassServiceWorker({"bypass": True}, session_id=session_id)
            self.report_progress("disable_downloads")
            await client.send.Browser.setDownloadBehavior({"behavior": "deny"})
            self.report_progress("read_browser_version")
            version = await client.send.Browser.getVersion()
            return {"browser_version": version["product"], "process_id": os.getpid(),
                    "browser_pid": self.browser._local_browser_watchdog.browser_pid}
        if operation == "observe":
            return await self.observe()
        if operation == "navigate":
            check_url(payload["url"], self.allowed)
            self.navigation_denied = False
            try:
                self.report_progress("navigate_goto")
                await self.page.goto(payload["url"])
            except Exception:
                if self.navigation_denied:
                    raise BrowserError("BROWSER_TARGET_DENIED") from None
                raise
            if self.navigation_denied:
                raise BrowserError("BROWSER_TARGET_DENIED")
            self.report_progress("navigate_wait_ready")
            await self.wait_ready(payload["url"])
        elif operation in ("click", "type"):
            element = await self.page.get_element(payload["node_id"])
            info = json.loads(await element.evaluate(ELEMENT))
            if info["sensitive"]:
                raise BrowserError("BROWSER_SENSITIVE_ELEMENT")
            if not info["visible"]:
                raise BrowserError("BROWSER_ELEMENT_STALE")
            if operation == "click":
                await element.evaluate("() => {this.scrollIntoView({block:'center',inline:'center'});return true}")
                point = await self.preflight(element, payload, operation)
                # No upstream coordinate fallbacks: dispatch the click once.
                session_id = await self.page.session_id
                for event_type in ("mousePressed", "mouseReleased"):
                    await self.browser.cdp_client.send.Input.dispatchMouseEvent(
                        {"type": event_type, "x": point["x"], "y": point["y"], "button": "left", "clickCount": 1},
                        session_id=session_id)
            else:
                # One logical replacement, one input dispatch. Element.fill's
                # watchdog may clear/retype and emit additional framework events.
                # Selection does not emit input/change; never fall back or retry
                # once insertText may have reached the page.
                prepared = json.loads(await element.evaluate("""() => {
                  const supported=this.tagName==='TEXTAREA'||(this.tagName==='INPUT'&&
                    ['text','search','tel','url','email'].includes(this.type));
                  if(!supported||this.readOnly||!this.isConnected)return {ready:false};
                  this.focus();
                  if(document.activeElement!==this)return {ready:false};
                  this.select();
                  return {ready:this.isConnected&&document.activeElement===this};
                }"""))
                if not prepared["ready"]:
                    raise BrowserError("BROWSER_INPUT_UNSUPPORTED")
                await self.preflight(element, payload, operation)
                await self.browser.cdp_client.send.Input.insertText(
                    {"text": payload["text"]}, session_id=await self.page.session_id)
            # Poll observations only; never repeat the action on uncertainty.
            for _ in range(60):
                try:
                    view = await self.page.evaluate(PAGE)
                    if payload["expected_text"] in json.loads(view)["text"]:
                        break
                except Exception:
                    pass  # navigation may briefly destroy the JS execution context
                await asyncio.sleep(0.05)
        elif operation == "scroll":
            current = json.loads(await self.page.evaluate(PAGE))
            if current["url"] != payload["expected_page"]["url"]:
                raise BrowserError("BROWSER_TARGET_CHANGED")
            if current["account"] != payload["expected_page"]["account"]:
                raise BrowserError("BROWSER_ACCOUNT_CHANGED")
            # Dispatch once: upstream Mouse.scroll has fallback retries.
            await self.browser.cdp_client.send.Input.dispatchMouseEvent({"type": "mouseWheel", "x": 400, "y": 300,
                "deltaX": 0, "deltaY": payload["delta_y"]}, session_id=await self.page.session_id)
            await asyncio.sleep(0.15)
        else:
            raise BrowserError("BROWSER_PROTOCOL_INVALID")
        return {}

    async def preflight(self, element, payload, operation):
        result = json.loads(await element.evaluate(PREFLIGHT,
            {"page": payload["expected_page"], "element": payload["expected_element"]}, operation))
        if result.get("error"):
            raise BrowserError(result["error"])
        return result

    def _intercept(self, event, session_id):
        # cdp-use awaits callbacks in its receive loop. A callback must schedule
        # sends separately so that the same loop can receive their responses.
        task = asyncio.create_task(self._continue_request(event, session_id))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    def _target_attached(self, event, session_id):
        task = asyncio.create_task(self._guard_target(event))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def _guard_target(self, event):
        client = self.browser.cdp_client
        target = event["targetInfo"]["targetId"]
        if target != self.page._target_id:
            if event["targetInfo"].get("type") == "iframe":
                # Cross-origin iframes get an out-of-process target. Closing it
                # used to be safe, but current Chromium leaves the frame's
                # renderer stuck in its debugger pause, which deadlocks every
                # later CDP evaluate on the main frame. Resume the frame and
                # guard its network through its own session instead; requests
                # still hit the registered requestPaused interceptor.
                if event.get("waitingForDebugger"):
                    await client.send.Runtime.runIfWaitingForDebugger(session_id=event["sessionId"])
                await client.send.Fetch.enable({"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}, session_id=event["sessionId"])
                return
            # New tabs/workers are outside the approved single-page session.
            # They stay paused and are closed before script execution resumes.
            await client.send.Fetch.enable({"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}, session_id=event["sessionId"])
            await client.send.Target.closeTarget({"targetId": target})
        elif event.get("waitingForDebugger"):
            await client.send.Runtime.runIfWaitingForDebugger(session_id=event["sessionId"])

    async def _continue_request(self, event, session_id):
        try:
            check_url(event["request"]["url"], self.allowed)
        except BrowserError:
            if (event.get("resourceType") == "Document"
                    and event.get("frameId") == self.main_frame_id):
                self.navigation_denied = True
            await self.browser.cdp_client.send.Fetch.failRequest({"requestId": event["requestId"], "errorReason": "BlockedByClient"}, session_id=session_id)
        else:
            await self.browser.cdp_client.send.Fetch.continueRequest({"requestId": event["requestId"]}, session_id=session_id)

    async def wait_ready(self, url):
        for _ in range(100):
            state = json.loads(await self.page.evaluate("() => ({url:location.href,ready:document.readyState})"))
            check_url(state["url"], self.allowed)
            if state["url"] == url and state["ready"] in ("interactive", "complete"):
                return
            await asyncio.sleep(0.05)
        raise TimeoutError()

    async def observe(self):
        self.report_progress("observe_dom_settle")
        # Give asynchronous DOM updates a bounded quiet window. A page that never
        # settles still yields a snapshot; its refs must be rechecked before act.
        await self.page.evaluate("""() => new Promise(resolve => {
          let quiet; const observer=new MutationObserver(reset);
          function done(){clearTimeout(quiet);clearTimeout(limit);observer.disconnect();resolve(true)}
          function reset(){clearTimeout(quiet);quiet=setTimeout(done,200)}
          const limit=setTimeout(done,1000);observer.observe(document,{childList:true,subtree:true,attributes:true});reset();
        })""")
        self.report_progress("observe_snapshot")
        value = json.loads(await self.page.evaluate(PAGE))
        check_url(value["url"], self.allowed)
        value["text"] = safe_text(value["text"])
        value["elements"] = []
        for element in (await self.page.get_elements_by_css_selector("button,a,input,textarea,select,[role='button']"))[:100]:
            info = json.loads(await element.evaluate(ELEMENT))
            if info.pop("visible") and not info["sensitive"]:
                # browser-use actor's backend ID is stable only within this live document.
                info["node_id"] = element._backend_node_id
                info["label"] = safe_text(info["label"], 256)
                value["elements"].append(info)
        return value


async def main():
    driver = Driver()
    while True:
        raw = await asyncio.to_thread(sys.stdin.buffer.readline, 65537)
        if not raw or len(raw) > 65536 or not raw.endswith(b"\n"):
            return
        frame = json.loads(raw)
        driver.request_id = frame["id"]
        try:
            result = await driver.request(frame["operation"], frame["payload"])
            response = {"id": frame["id"], "result": result}
        except Exception as error:
            response = {"id": frame["id"], "error": error.code if isinstance(error, BrowserError) else
                       ("BROWSER_START_FAILED" if frame["operation"] == "start" else "BROWSER_CONNECTION_LOST")}
            if not isinstance(error, BrowserError):
                # Diagnostics contain only fixed stage, exception type and an
                # allowlisted category; exception text and browser stderr stay private.
                response["diagnostic"] = failure_diagnostic(driver.stage, error)
            if frame["operation"] == "start" and not isinstance(error, BrowserError):
                response["stage"] = driver.stage
        PROTOCOL.write(json.dumps(response, ensure_ascii=True) + "\n")
        PROTOCOL.flush()
        driver.request_id = None


if __name__ == "__main__":
    asyncio.run(main())
