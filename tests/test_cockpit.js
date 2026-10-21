
"use strict";

// Run with: node --test tests/test_cockpit.js (Node >= 18; no packages required).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

let networkAttempts = 0;
function denyNetwork() {
  networkAttempts += 1;
  throw new Error("Network is forbidden in cockpit tests");
}
require("node:net").Socket.prototype.connect = denyNetwork;
require("node:tls").connect = denyNetwork;
for (const moduleName of ["node:http", "node:https"]) {
  const module = require(moduleName);
  module.request = denyNetwork;
  module.get = denyNetwork;
}
require("node:dns").lookup = denyNetwork;
globalThis.fetch = denyNetwork;
test.after(() => {
  assert.equal(networkAttempts, 0);
  console.log("NETWORK_ATTEMPTS=" + networkAttempts);
});

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");

// Execute the application's functions themselves, without booting the browser UI.
function functionSource(name) {
  const start = new RegExp("^(?:async )?function " + name + "\\(", "m").exec(source);
  assert.ok(start, "Missing application function: " + name);
  const tailStart = start.index + start[0].length;
  const next = /^(?:async )?function /m.exec(source.slice(tailStart));
  assert.ok(next, "Missing next function after: " + name);
  return source.slice(start.index, tailStart + next.index);
}

function loadFunctions(names, globals = {}) {
  const context = vm.createContext(globals);
  vm.runInContext(names.map(functionSource).join("\n"), context, { filename: "static/app.js" });
  return context;
}

function watchRow(extra = {}) {
  return {
    symbol: "ETHUSDC",
    sources: ["WATCH_CANDIDATE", "BINANCE_SPOT"],
    badges: ["Surveillance", "Spot"],
    watch_info: { id: 7 },
    position_info: null,
    opportunity_info: null,
    ...extra,
  };
}

for (const metadata of ["BINANCE_SPOT", "BINANCE_ALPHA", "UNKNOWN"]) {
  test("watch deletion removes a row with only " + metadata + " metadata left", () => {
    const context = loadFunctions(["stripWatchCandidateFromRow"]);
    assert.equal(context.stripWatchCandidateFromRow(watchRow({
      sources: ["WATCH_CANDIDATE", metadata],
    }), "7"), null);
  });
}

for (const businessSource of ["POSITION", "OPPORTUNITY_SCANNER"]) {
  test("watch deletion preserves remaining " + businessSource, () => {
    const context = loadFunctions(["stripWatchCandidateFromRow"]);
    const extra = businessSource === "POSITION" ? { position_info: { id: 1 } } : { opportunity_info: { scan_score: 80 } };
    const result = context.stripWatchCandidateFromRow(watchRow({
      sources: ["WATCH_CANDIDATE", "BINANCE_SPOT", businessSource], ...extra,
    }), 7);
    assert.ok(result);
    assert.equal(result.watch_info, null);
    assert.equal(result.sources.includes("WATCH_CANDIDATE"), false);
    assert.equal(result.sources.includes(businessSource), true);
    assert.equal(result.badges.includes("Surveillance"), false);
  });
}

test("deleting another watch does not change the row", () => {
  const context = loadFunctions(["stripWatchCandidateFromRow"]);
  const row = watchRow();
  assert.equal(context.stripWatchCandidateFromRow(row, 8), row);
});

function cockpit(rows) {
  let open = true;
  const calls = { closed: 0, histories: [] };
  const globals = {
    latestRows: rows,
    latestWatchCandidates: [{ id: 7, symbol: "ETHUSDC" }],
    dashboardState: {},
    resultsBody: { innerHTML: "" },
    mobileCards: { innerHTML: "" },
    detailTitle: { textContent: "ETHUSDC" },
    detailSubtitle: { textContent: "" },
    detailContent: { innerHTML: "", insertAdjacentHTML(where, html) { this.innerHTML += html; } },
    detailPanel: { classList: {
      contains: () => open,
      add: () => { open = true; },
    } },
    detailRenderId: 0,
    currentDevice: "desktop",
    closeDetailPanel: () => { calls.closed += 1; open = false; },
    renderWatchCandidates: (items) => { calls.watches = items; },
    renderMobileCards: (items) => { calls.cards = items; },
    decisionOf: (item) => item.decision_engine || {},
    positionForSymbol: () => null,
    formatNumber: (value) => String(value ?? "-"),
    renderScoreEvolution: () => new Promise((resolve) => calls.histories.push(resolve)),
  };
  for (const name of [
    "marketSymbolMetaHtml", "decisionClass", "trendClass", "qualityClass",
    "rawMainBlockingFactor", "mainBlockingFactor", "marketSourceLabel", "signalClass",
    "shortText", "decisionSummaryLabel", "shortDecisionSummary", "marketContextText",
    "decisionFactorLabel", "timeframeScore", "momentumClass", "momentumText",
    "mainPattern", "volumeAccelText",
  ]) globals[name] = () => "";
  const context = loadFunctions([
    "stripWatchCandidateFromRow", "removeWatchCandidateLocally", "renderRows",
    "reconcileOpenDetail", "renderDetail", "triggerDecisionLabel", "marketActionsHtml",
  ], globals);
  return { context, calls };
}

test("removing the last business source clears table, cards and open detail", () => {
  const { context, calls } = cockpit([watchRow()]);
  context.removeWatchCandidateLocally(7);
  assert.equal(context.latestRows.length, 0);
  assert.equal(context.latestWatchCandidates.length, 0);
  assert.match(context.resultsBody.innerHTML, /empty-row/);
  assert.match(context.mobileCards.innerHTML, /empty-card/);
  assert.equal(calls.closed, 1);
});

test("remaining business source refreshes the actual open detail and removes its watch action", () => {
  const row = watchRow({
    sources: ["WATCH_CANDIDATE", "BINANCE_SPOT", "OPPORTUNITY_SCANNER"],
    opportunity_info: { scan_score: 80 },
  });
  const { context, calls } = cockpit([row]);
  context.renderDetail(row);
  assert.match(context.detailContent.innerHTML, /data-market-remove-watch="7"/);
  context.removeWatchCandidateLocally(7);
  assert.equal(calls.closed, 0);
  assert.equal(context.latestRows.length, 1);
  assert.doesNotMatch(context.detailContent.innerHTML, /data-market-remove-watch/);
  // A subsequent server rebuild may disqualify the remaining automatic opportunity.
  context.renderRows([]);
  assert.equal(calls.closed, 1);
});

test("obsolete asynchronous history cannot append to a newer detail render", async () => {
  const { context, calls } = cockpit([watchRow()]);
  context.renderDetail(watchRow());
  context.renderDetail(watchRow({ watch_info: null }));
  calls.histories[0]("OBSOLETE_HISTORY");
  calls.histories[1]("CURRENT_HISTORY");
  await Promise.resolve();
  assert.doesNotMatch(context.detailContent.innerHTML, /OBSOLETE_HISTORY/);
  assert.match(context.detailContent.innerHTML, /CURRENT_HISTORY/);
  context.renderDetail(watchRow());
  context.closeDetailPanel();
  calls.histories[2]("CLOSED_HISTORY");
  await Promise.resolve();
  assert.doesNotMatch(context.detailContent.innerHTML, /CLOSED_HISTORY/);
});

for (const code of ["AVOID", "WAIT", "BUY_WATCH", "CUT_LOSS"]) {
  test("confirmed timing for " + code + " explicitly refuses entry", () => {
    const context = loadFunctions(["triggerDecisionLabel"]);
    assert.match(context.triggerDecisionLabel({
      decision: code, timing_status: "CONFIRMED", timing_label: "Signal technique confirme",
      timing_is_actionable: false,
    }), /entree non autorisee/);
    assert.match(context.triggerDecisionLabel({ decision: code, trigger_score: 70 }), /entree non autorisee/);
  });
}

test("BUY_READY can display authorized timing while the detail keeps a veto visible", () => {
  const { context } = cockpit([]);
  const decision = {
    decision: "BUY_READY", timing_status: "CONFIRMED", timing_label: "Signal technique confirme",
    timing_is_actionable: true,
  };
  assert.equal(context.triggerDecisionLabel(decision), "Signal technique confirme");
  context.renderDetail(watchRow({ decision_engine: {
    ...decision, decision: "AVOID", timing_is_actionable: false,
    timing_reason: "RISK_VETO_REASON", timing_action: "Ne pas chercher d'entree",
  } }));
  assert.match(context.detailContent.innerHTML, /Entree autorisee :<\/span><strong>Non/);
  assert.match(context.detailContent.innerHTML, /RISK_VETO_REASON/);
});

function deletionFlow({ deleteFails = false, refreshFails = false, confirmed = true } = {}) {
  const calls = [];
  const feedback = [];
  const context = loadFunctions(["removeWatchFromMarket", "refreshMarketCockpit"], {
    window: { confirm: () => confirmed },
    setMarketFeedback: (message, kind) => feedback.push({ message, kind }),
    apiFetch: async (url, options) => {
      calls.push([url, options.method]);
      if (deleteFails) throw new Error("DELETE_FAILED");
    },
    removeWatchCandidateLocally: (id) => calls.push(["local", id]),
    loadPersonalData: async () => calls.push(["personal"]),
    loadMarket: async (force) => {
      calls.push(["market", force]);
      if (refreshFails) throw new Error("REFRESH_FAILED");
    },
    loadV5Data: async () => calls.push(["alerts"]),
  });
  return { context, calls, feedback };
}

test("successful deletion removes locally then forces a full cockpit reconciliation", async () => {
  const { context, calls, feedback } = deletionFlow();
  await context.removeWatchFromMarket(7);
  assert.deepEqual(calls, [
    ["/api/watch-candidates/7", "DELETE"], ["local", 7],
    ["personal"], ["market", true], ["alerts"],
  ]);
  assert.equal(feedback.at(-1).kind, "success");
});

test("failed deletion keeps the local watch and does not refresh", async () => {
  const { context, calls, feedback } = deletionFlow({ deleteFails: true });
  await context.removeWatchFromMarket(7);
  assert.deepEqual(calls, [["/api/watch-candidates/7", "DELETE"]]);
  assert.match(feedback.at(-1).message, /Impossible de retirer/);
});

test("failed refresh reports deletion as saved and refresh as failed", async () => {
  const { context, calls, feedback } = deletionFlow({ refreshFails: true });
  await context.removeWatchFromMarket(7);
  assert.ok(calls.some(([operation]) => operation === "local"));
  assert.match(feedback.at(-1).message, /Surveillance supprimee\. Actualisation/);
  assert.doesNotMatch(feedback.at(-1).message, /Impossible de retirer/);
});

test("cancelled confirmation does not delete", async () => {
  const { context, calls } = deletionFlow({ confirmed: false });
  await context.removeWatchFromMarket(7);
  assert.deepEqual(calls, []);
});

test("surveillance management panel uses the same deletion flow as the cockpit", async () => {
  let listener;
  let received;
  const feedback = () => {};
  const context = vm.createContext({
    watchBody: { addEventListener: (type, callback) => { listener = callback; } },
    setWatchFeedback: feedback,
    removeWatchFromMarket: async (...args) => { received = args; },
  });
  const start = source.indexOf('watchBody.addEventListener("click"');
  const end = source.indexOf('journalForm.addEventListener("submit"', start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(source.slice(start, end), context);
  await listener({ target: { closest: (selector) => selector === "[data-delete-watch]"
    ? { dataset: { deleteWatch: "7" } } : null } });
  assert.equal(received[0], "7");
  assert.equal(received[1], feedback);
});


function guardedClient({ status = 200, origin = "http://localhost:8000", remaining = "3600" } = {}) {
  const calls = [];
  const nodes = [{ innerHTML: "PRIVATE_DATA" }, { innerHTML: "PRIVATE_JOURNAL" }];
  const forms = [{ reset: () => calls.push(["reset-form"]) }];
  const context = loadFunctions(["apiFetch", "handleSessionExpired", "scheduleSessionExpiry"], {
    sessionExpired: false,
    sessionExpiryTimer: 3,
    autoRefreshTimer: 1,
    refreshDebounceTimer: 2,
    URL,
    window: { location: {
      origin,
      replace: (url) => calls.push(["redirect", url]),
    } },
    document: {
      querySelector: () => ({ content: remaining }),
      querySelectorAll: (selector) => selector.includes("#positionForm") ? forms : nodes,
    },
    clearTimeout: (id) => calls.push(["clear-timer", id]),
    setTimeout: (callback, delay) => { calls.push(["timer", callback, delay]); return 4; },
    clearFrontendCache: () => calls.push(["clear-cache"]),
    fetch: async (url, options) => {
      calls.push(["fetch", url, options]);
      return {
        status, ok: status >= 200 && status < 300,
        headers: { get: () => "application/json" },
        text: async () => JSON.stringify(status === 403 ? { error: "CSRF refused" } : { ok: true }),
      };
    },
  });
  return { context, calls, nodes };
}

for (const method of ["GET", "POST", "PUT", "PATCH", "DELETE"]) {
  test("API " + method + " uses a non-secret CSRF guard and same-origin cookies", async () => {
    const { context, calls } = guardedClient();
    await context.apiFetch("/api/positions", {
      method, credentials: "include", redirect: "follow",
      headers: { "X-Custom": "kept", "X-CSRF-Guard": "forged" },
    });
    const options = calls.find(([operation]) => operation === "fetch")[2];
    assert.equal(options.headers["X-CSRF-Guard"], "1");
    assert.equal(options.headers["X-Custom"], "kept");
    assert.equal(options.headers["X-CSRF-Token"], undefined);
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.redirect, "error");
  });
}

for (const url of ["https://evil.example/api", "//evil.example/api", "javascript:alert(1)"]) {
  test("API refuses external target " + url, async () => {
    const { context, calls } = guardedClient();
    await assert.rejects(context.apiFetch(url), /externe interdite/);
    assert.equal(calls.length, 0);
  });
}

test("401 erases private UI, resets forms and redirects once without replaying requests", async () => {
  const { context, calls, nodes } = guardedClient({ status: 401 });
  await assert.rejects(context.apiFetch("/api/positions"), /Session expiree/);
  assert.ok(nodes.every((node) => node.innerHTML === ""));
  assert.ok(calls.some(([operation]) => operation === "reset-form"));
  assert.ok(calls.some(([operation]) => operation === "clear-cache"));
  assert.equal(calls.filter(([operation]) => operation === "clear-timer").length, 3);
  assert.deepEqual(calls.find(([operation]) => operation === "redirect"), ["redirect", "/login?expired=1"]);
  await assert.rejects(context.apiFetch("/api/positions"), /Session expiree/);
  assert.equal(calls.filter(([operation]) => operation === "fetch").length, 1);
});

test("CSRF rejection does not silently replay or log out an authenticated session", async () => {
  const { context, calls } = guardedClient({ status: 403 });
  await assert.rejects(context.apiFetch("/api/positions", { method: "POST" }), /CSRF refused/);
  assert.equal(calls.filter(([operation]) => operation === "fetch").length, 1);
  assert.equal(calls.some(([operation]) => operation === "redirect"), false);
});

test("absolute expiry schedules UI clearing even without another API request", () => {
  const { context, calls, nodes } = guardedClient({ remaining: "60" });
  context.scheduleSessionExpiry();
  const timer = calls.find(([operation]) => operation === "timer");
  assert.equal(timer[2], 60000);
  timer[1]();
  assert.ok(nodes.every((node) => node.innerHTML === ""));
  assert.equal(context.sessionExpired, true);
});

test("missing expiry metadata closes the UI instead of keeping an indefinite session", () => {
  const { context } = guardedClient({ remaining: "" });
  context.scheduleSessionExpiry();
  assert.equal(context.sessionExpired, true);
});
