"use strict";
// Real DOM/CSP probes using an isolated Edge profile and Node's built-in CDP WebSocket.
// Run on Windows: node tools/verify_security_browser.cjs http://127.0.0.1:PORT
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { spawn } = require("node:child_process");

assert.equal(typeof WebSocket, "function", "The browser probe requires Node >= 22");
const origin = new URL(process.argv[2]).origin;
assert.ok(/^http:\/\/(?:127\.0\.0\.1|localhost):\d+$/.test(origin));
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "cryptoai-edge-profile-"));
const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), "cryptoai-browser-results-"));
const edgePath = process.env.CRYPTOAI_TEST_EDGE || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const flags = [
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--disable-background-networking", "--disable-component-update", "--disable-sync",
  "--disable-domain-reliability", "--metrics-recording-only", "--disable-quic",
  "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
  "--disable-features=MediaRouter,OptimizationHints,AutofillServerCommunication,msEdgeSidebarV2",
  "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost",
  "--proxy-server=http://127.0.0.1:9",
  "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
  "--user-data-dir=" + profile, "about:blank",
];
const edge = spawn(edgePath, flags, { windowsHide: true, stdio: "ignore" });
let browserSocket;
let pageSocket;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitUntil(predicate, label, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { const value = await predicate(); if (value) return value; } catch {}
    await delay(30);
  }
  throw new Error("Timed out: " + label);
}
async function connection(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let sequence = 0;
  const pending = new Map();
  const events = new Map();
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) {
      const callback = pending.get(message.id);
      if (callback) {
        pending.delete(message.id);
        message.error ? callback.reject(new Error(message.error.message)) : callback.resolve(message.result);
      }
    } else if (events.has(message.method)) {
      events.get(message.method)(message.params);
    }
  });
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  return { socket, call, events };
}
async function main() {
  const activePort = path.join(profile, "DevToolsActivePort");
  await waitUntil(() => fs.existsSync(activePort), "Edge debugging port");
  const [port, browserPath] = fs.readFileSync(activePort, "utf8").trim().split(/\r?\n/);
  const browser = await connection("ws://127.0.0.1:" + port + browserPath);
  browserSocket = browser;
  const response = await fetch("http://127.0.0.1:" + port + "/json/list", { redirect: "error" });
  const targets = await response.json();
  const target = targets.find((item) => item.type === "page");
  assert.ok(target);
  const page = await connection(target.webSocketDebuggerUrl);
  pageSocket = page;
  const exceptions = [];
  globalThis.probeExceptions = exceptions;
  const blockedRequests = [];
  page.events.set("Runtime.exceptionThrown", (event) => exceptions.push(event.exceptionDetails.text));
  page.events.set("Fetch.requestPaused", async ({ requestId, request }) => {
    const url = new URL(request.url);
    if (url.origin === origin || url.protocol === "about:") {
      await page.call("Fetch.continueRequest", { requestId });
    } else {
      blockedRequests.push(url.origin);
      await page.call("Fetch.failRequest", { requestId, errorReason: "BlockedByClient" });
    }
  });
  await page.call("Runtime.enable");
  await page.call("Page.enable");
  await page.call("Fetch.enable", { patterns: [{ urlPattern: "*" }] });
  await page.call("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  const evaluate = async (expression) => {
    const result = await page.call("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text + " " + (result.exceptionDetails.exception?.description || ""));
    return result.result.value;
  };
  const checks = [];
  const check = (label, condition) => { assert.ok(condition, label); checks.push(label); };
  await page.call("Page.navigate", { url: origin + "/login" });
  await waitUntil(() => evaluate('document.querySelector("input[name=password]") !== null'), "login form");
  await evaluate('document.querySelector("input[name=password]").value="test-only-cryptoai-http-password-2026"; document.querySelector("form").requestSubmit();');
  await waitUntil(() => evaluate('location.pathname === "/" && typeof renderRows === "function" && document.querySelector("#resultsBody strong") !== null'), "authenticated cockpit");
  await waitUntil(() => evaluate('document.querySelector("#aiAlertsList .ai-alert-card") !== null'), "alert rendering");
  check("real cockpit initialized", await evaluate('typeof apiFetch === "function" && !document.querySelector("#statusText").textContent.includes("Erreur")'));
  check("external CSS loaded", await evaluate('getComputedStyle(document.querySelector(".panel")).borderRadius !== "0px"'));
  check("HTTP-only session inaccessible to JS", !(await evaluate("document.cookie")).includes("cryptoai_session"));
  await evaluate('globalThis.violations=[]; document.addEventListener("securitypolicyviolation", e=>violations.push(e.effectiveDirective));');
  const payloads = [
    "<script>globalThis.compromised=1</script>",
    '"><img src=x onerror="globalThis.compromised=1">',
    "javascript:globalThis.compromised=1",
    "<svg/onload=globalThis.compromised=1>",
    '<iframe srcdoc="<script>globalThis.compromised=1</script>">',
    "' autofocus onfocus='globalThis.compromised=1",
    "& < > \" ' Unicode é 漢字 \u2028",
  ];
  for (const payload of payloads) {
    const result = await evaluate(`(() => {
      const value=${JSON.stringify(payload)};
      const decision={decision:"WAIT",decision_label:value,confidence:value,reason_summary:value,
        timing_label:value,timing_action:value,timing_reason:value,blocking_factors:[value],positive_factors:[value]};
      const item={symbol:value,current_price:10,global_score:55,global_trend:value,setup_quality:value,
        decision_engine:decision,watch_info:{id:value,priority:value},sources:["WATCH_CANDIDATE"],
        opportunity_info:{scan_score:value,reason:value}};
      renderPositions([{id:value,symbol:value,status:"MANUAL",source:"MANUAL",quantity:1,is_active:1,trend_score:value}]);
      renderWatchCandidates([{id:value,symbol:value,priority:value,signal:value,score:value}]);
      renderRows([item]); renderDetail(item);
      renderAiAlerts({alerts:[{type:value,severity:value,title:value,message:value,symbol:value,confidence:value}]});
      renderJournal([{id:value,symbol:value,decision_type:value,emotion:value,result_status:value,reason:value,notes:value,
        ai_snapshot:{decision:{reason_summary:value}}}]);
      const roots=[...document.querySelectorAll("#resultsBody,#mobileCards,#detailContent,#positionsBody,#watchBody,#journalList,#aiAlertsList")];
      const nodes=roots.flatMap(root=>[...root.querySelectorAll("*")]);
      return { exact:document.querySelector("#resultsBody strong").textContent===value,
        active:nodes.some(el=>["SCRIPT","IMG","IFRAME","OBJECT","EMBED"].includes(el.tagName) ||
          [...el.attributes].some(a=>/^on/i.test(a.name) || (/^(href|src)$/i.test(a.name) && /^\\s*javascript:/i.test(a.value)))),
        executed:globalThis.compromised===1};
    })()`);
    check("DOM inert payload " + checks.length, result.exact && !result.active && !result.executed);
  }
  await evaluate(`(async()=>{
    const payload=${JSON.stringify(payloads[1])};
    await apiFetch("/api/journal",{method:"POST",body:JSON.stringify({symbol:"BTCUSDC",reason:payload,notes:payload})});
    renderJournal(await apiFetch("/api/journal"));
  })()`);
  check("persisted hostile journal rendered as text", await evaluate(`document.querySelector("#journalList").textContent.includes(${JSON.stringify(payloads[1])}) && !document.querySelector("#journalList img")`));
  await evaluate('(async()=>{try{await apiFetch("/api/test/reflected")}catch(error){setMarketFeedback(error.message,"error")}})()');
  check("reflected API error stays text", await evaluate('document.querySelector("#marketFeedback").textContent.includes("<img") && !document.querySelector("#marketFeedback img")'));
  check("no CSP violations for normal renderers", (await evaluate("violations.length")) === 0);
  check("no application JavaScript exceptions", exceptions.length === 0);
  const desktop = path.join(artifacts, "cockpit-desktop.png");
  fs.writeFileSync(desktop, Buffer.from((await page.call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false })).data, "base64"));
  await page.call("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await evaluate("applyDeviceMode(); renderMobileCards(latestRows);");
  check("mobile cockpit uses responsive layout", await evaluate('currentDevice === "mobile" && getComputedStyle(document.querySelector("#mobileCards")).display !== "none"'));
  const mobile = path.join(artifacts, "cockpit-mobile.png");
  fs.writeFileSync(mobile, Buffer.from((await page.call("Page.captureScreenshot", { format: "png", captureBeyondViewport: false })).data, "base64"));
  await evaluate(`(() => {
    const node=document.createElement("div");node.id="cspProbe";document.body.append(node);
    node.innerHTML='<img src="/static/csp-missing.png" onerror="globalThis.csp_bypass=1">';
    const script=document.createElement("script");script.textContent="globalThis.csp_bypass=1";node.append(script);
  })()`);
  await waitUntil(() => evaluate("violations.length >= 2"), "CSP blocks inline event and script");
  check("effective CSP blocks executable inline probes", !(await evaluate("globalThis.csp_bypass===1")));
  check("external page requests blocked", blockedRequests.length === 0);
  await evaluate(`document.querySelector("#cspProbe").remove(); document.querySelector('form[action="/logout"]').requestSubmit();`);
  await waitUntil(() => evaluate('location.pathname === "/login"'), "native logout");
  check("native logout returns to login", true);
  console.log(JSON.stringify({ checks_passed: checks.length, checks, application_exceptions: exceptions,
    unexpected_external_requests: blockedRequests, desktop, mobile, browser: "Microsoft Edge headless" }));
}
main().catch(async (error) => {
  if (pageSocket) {
    const diagnostic = await pageSocket.call("Runtime.evaluate", {expression: 'JSON.stringify({path:location.pathname,status:document.querySelector("#statusText")?.textContent,alert:document.querySelector("[role=alert]")?.textContent,script:typeof apiFetch})',returnByValue:true});
    console.error("BROWSER_DIAGNOSTIC="+diagnostic.result.value);
    console.error("APPLICATION_EXCEPTIONS="+JSON.stringify(globalThis.probeExceptions));
  }
  console.error(error.stack); process.exitCode = 1;
}).finally(async () => {
  if (browserSocket) {
    try { await browserSocket.call("Browser.close"); } catch {}
    browserSocket.socket.close();
  }
  if (pageSocket) pageSocket.socket.close();
  await delay(500);
  if (edge.exitCode === null) edge.kill();
  const resolved = fs.existsSync(profile) ? fs.realpathSync.native(profile) : path.resolve(profile);
  const temp = fs.realpathSync.native(os.tmpdir()) + path.sep;
  if (resolved.startsWith(temp) && path.basename(resolved).startsWith("cryptoai-edge-profile-")) {
    try { fs.rmSync(resolved, { recursive: true }); } catch {}
  }
});
