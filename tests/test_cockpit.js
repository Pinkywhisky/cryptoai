
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
  vm.runInContext([...new Set(["escapeHtml", "financialNumber", "formatFinancial", "formatFinancialPercent", "financialClass", "financialStatusLabel", "alphaMarketState", "marketDataStatusHtml", "marketPriceHtml", ...names])].map(functionSource).join("\n"), context, { filename: "static/app.js" });
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
    syncPollTimer: null, activeSyncJob: null, syncPreview: null,
    window: { clearTimeout: (id) => calls.push(["clear-sync-poll", id]), location: {
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


// Lot 2B: execute the real rendering functions against hostile API data.
function renderingHarness() {
  const node = () => ({ innerHTML: "", textContent: "", classList: { add() {}, contains() { return false; } } });
  const globals = {
    resultsBody: node(), mobileCards: node(), detailContent: node(), detailTitle: node(),
    detailSubtitle: node(), detailPanel: node(), positionsBody: node(), watchBody: node(),
    journalList: node(), journalStats: node(), aiAlertsList: node(), portfolioSummary: node(),
    watchFeedback: { textContent: "", dataset: {} }, marketFeedback: { textContent: "", dataset: {} },
    latestPositions: [], latestWatchCandidates: [], latestJournalEntries: [], latestRows: [],
    latestJournalAnalytics: null, dashboardState: {}, detailRenderId: 0, currentDevice: "desktop",
    journalFilterSymbol: { value: "" }, journalFilterEmotion: { value: "" }, journalFilterResult: { value: "" },
    openMobileCards: new Set(), historyCache: new Map(), MIN_ACTIVE_POSITION_VALUE_USDC: 1,
    document: { body: { classList: { add() {} } } },
    apiFetch: async () => ({ history: [], trend: "stable", acceleration: "stable" }),
  };
  const end = source.indexOf("// --- CHARGEMENT DES FLUX DATA ---");
  const names = [...source.slice(0, end).matchAll(/^(?:async )?function (\w+)\(/gm)]
    .map((match) => match[1]).filter((name) => name !== "apiFetch");
  return loadFunctions(names, globals);
}

function assertInertHtml(html) {
  for (const tag of html.match(/<(?:[^>"']|"[^"]*"|'[^']*')+>/g) || []) {
    assert.doesNotMatch(tag, /^<(?:script|img|iframe|math)\b/i);
    const attributes = tag.replace(/"[^"]*"|'[^']*'/g, '""');
    assert.doesNotMatch(attributes, /\son\w+\s*=/i);
  }
}

const xssPayloads = [
  "<script>globalThis.compromised=1</script>",
  '"><img src=x onerror="globalThis.compromised=1">',
  "javascript:globalThis.compromised=1",
  "<svg/onload=globalThis.compromised=1>",
  '<iframe srcdoc="<script>globalThis.compromised=1</script>">',
  "& < > \" ' Unicode é 漢字 \u2028",
];

for (const payload of xssPayloads) {
  test("all real renderers treat hostile API values as text: " + payload.slice(0, 25), async () => {
    const c = renderingHarness();
    const decision = {
      decision: "WAIT", decision_label: payload, confidence: payload, reason_summary: payload,
      blocking_factors: [payload], positive_factors: [payload], timing_label: payload,
      timing_action: payload, timing_reason: payload, context_score: payload,
    };
    const item = {
      symbol: payload, current_price: 10, global_score: payload, global_trend: payload,
      setup_quality: payload, decision_engine: decision, patterns: { upper_wick: { detected: true, label: payload } },
      watch_info: { id: payload, priority: payload }, sources: ["WATCH_CANDIDATE"],
      opportunity_info: { scan_score: payload, reason: payload },
    };
    c.renderRows([item]);
    c.renderDetail(item);
    c.renderPositions([{ symbol: payload, id: payload, status: payload, source: "MANUAL", trend_score: payload }]);
    c.renderWatchCandidates([{ symbol: payload, id: payload, priority: payload, signal: payload, score: payload }]);
    c.renderAiAlerts({ alerts: [{ symbol: payload, type: payload, severity: payload, title: payload, message: payload, confidence: payload }] });
    c.renderJournal([{ symbol: payload, id: payload, decision_type: payload, trade_type: payload, emotion: payload,
      result_status: payload, reason: payload, notes: payload, ai_trend: payload,
      ai_snapshot: { decision: { reason_summary: payload } } }]);
    c.renderJournalStats({ winrate: { closed_trades: payload }, setups: { best_setup: { setup: payload } } });
    const html = [c.resultsBody, c.mobileCards, c.detailContent, c.positionsBody, c.watchBody, c.aiAlertsList, c.journalList, c.journalStats]
      .map((element) => element.innerHTML).join("\n");
    assertInertHtml(html);
    assert.doesNotMatch(html, /(?:href|src)=["']javascript:/i);
    assert.ok(html.includes(c.escapeHtml(payload)));
    assert.match(c.aiAlertsList.innerHTML, /ai-market severity-low/);
    c.renderRows([{ ...item, error: payload }]);
    c.renderDetail({ ...item, error: payload });
    assertInertHtml(c.resultsBody.innerHTML + c.detailContent.innerHTML);
    await Promise.resolve();
  });
}

test("hostile error messages are written through textContent", () => {
  const c = renderingHarness();
  const message = '<img src=x onerror="globalThis.compromised=1">';
  c.setMarketFeedback(message, "error");
  c.setWatchFeedback(message, "error");
  assert.equal(c.marketFeedback.textContent, message);
  assert.equal(c.watchFeedback.textContent, message);
  assert.equal(c.marketFeedback.innerHTML, undefined);
});

test("history requests encode hostile symbols as a single path segment", async () => {
  const c = renderingHarness();
  const calls = [];
  c.apiFetch = async (url) => { calls.push(url); return []; };
  const symbol = '../admin/reset?key=<x>&other="y"';
  await c.enrichWithHistory([{ symbol }]);
  assert.equal(calls[0], "/api/history/" + encodeURIComponent(symbol) + "?limit=8");
});

for (const url of ["http://name:password@localhost:8000/api/positions", "/api/positions#secret", "/logout"]) {
  test("API refuses credentials, fragments and non-API targets: " + url, async () => {
    const { context, calls } = guardedClient();
    await assert.rejects(context.apiFetch(url), /externe interdite/);
    assert.equal(calls.length, 0);
  });
}

test("a delayed response cannot refill the UI after session expiry", async () => {
  const { context, calls } = guardedClient();
  let complete;
  context.fetch = () => new Promise((resolve) => { complete = resolve; });
  const pending = context.apiFetch("/api/positions");
  context.handleSessionExpired();
  complete({ status: 200, ok: true, headers: { get: () => "application/json" }, text: async () => '{"private":"data"}' });
  await assert.rejects(pending, /Session expiree/);
});


test("financial unknown values never become zero or a gain", () => {
  const context = loadFunctions(["formatNumber", "pnlValue", "positionCurrentValue"]);
  for (const value of [null, undefined, "", "not-a-number", Infinity]) {
    assert.equal(context.formatFinancial(value), "Indisponible");
  }
  assert.equal(context.formatFinancial(0), "0");
  assert.equal(context.pnlValue({source:"BINANCE",unrealized_pnl:null}), null);
  assert.equal(context.positionCurrentValue({current_value:null}), null);
  assert.equal(context.financialClass(null), "pnl-neutral");
});

test("tiny positive quantities are visible rather than rounded to zero", () => {
  const context = loadFunctions(["formatNumber"]);
  assert.match(context.formatFinancial("0.000000000001", 6), /e-12/);
  assert.notEqual(context.formatFinancial("0.000000000001", 6), "0");
});

test("price outage preserves visibility of an active holding", () => {
  const context = loadFunctions(["isActiveMarketPosition"], {MIN_ACTIVE_POSITION_VALUE_USDC:1});
  assert.equal(context.isActiveMarketPosition({
    source:"BINANCE",status:"ACTIVE",is_active:1,quantity:1,current_value:null,
  }), true);
  assert.equal(context.isActiveMarketPosition({
    source:"BINANCE",status:"CLOSED",is_active:0,quantity:0,current_value:null,
  }), false);
});

test("financial portfolio uses server totals including closed realized trades", () => {
  const portfolioSummary = {innerHTML:""};
  const context = loadFunctions(["formatNumber","renderPortfolioSummary"], {portfolioSummary,latestPositions:[]});
  context.renderPortfolioSummary({
    portfolio_value_usdc:43.5,portfolio_value_eur:null,realized_pnl:150,
    unrealized_pnl:.5,total_pnl:150.5,active_positions_count:1,pnl_status:"EXACT",
  });
  assert.match(portfolioSummary.innerHTML,/150,5/);
  assert.match(portfolioSummary.innerHTML,/43,5/);
  assert.match(portfolioSummary.innerHTML,/Indisponible/);
  assert.doesNotMatch(portfolioSummary.innerHTML,/40,02/); // old invented EUR rate .92
});

test("partial financial data shows the known subtotal without a fictitious total", () => {
  const portfolioSummary = {innerHTML:""};
  const context = loadFunctions(["formatNumber","renderPortfolioSummary"], {portfolioSummary,latestPositions:[]});
  context.renderPortfolioSummary({
    portfolio_value_usdc:null,known_portfolio_value_usdc:110,
    realized_pnl:5,unrealized_pnl:null,total_pnl:null,active_positions_count:2,
    sync_status:"PARTIAL",pnl_status:"INCOMPLETE",
  });
  assert.match(portfolioSummary.innerHTML,/Sous-total connu : 110/);
  assert.match(portfolioSummary.innerHTML,/Données incomplètes/);
  assert.match(portfolioSummary.innerHTML,/Indisponible/);
  assert.doesNotMatch(portfolioSummary.innerHTML,/>0 USDC</);
});

test("position table keeps unknown-cost holdings visible with an explicit label", () => {
  const positionsBody = {innerHTML:""};
  const context = loadFunctions(["formatNumber","renderPositions"], {
    positionsBody,latestPositions:[],MIN_ACTIVE_POSITION_VALUE_USDC:1,
    renderMarketSourceBadge:()=>"<span>Spot</span>",
  });
  context.renderPositions([{
    id:1,symbol:"ABCUSDC",source:"BINANCE",status:"ACTIVE",quantity:1,
    current_value:null,average_buy_price:null,gain_loss_pct:null,
    data_status:"COST_UNKNOWN",market_source:"BINANCE_SPOT",
  }]);
  assert.match(positionsBody.innerHTML,/ABCUSDC/);
  assert.match(positionsBody.innerHTML,/Coût inconnu/);
  assert.match(positionsBody.innerHTML,/Indisponible/);
  assert.doesNotMatch(positionsBody.innerHTML,/momentum-bullish/);
});

test("financial reliability labels cannot inject API-provided markup", () => {
  const context = loadFunctions([]);
  assert.equal(context.financialStatusLabel({data_status:'"><img src=x onerror=alert(1)>'}),"");
});

test("configured Binance keys are distinguished from a successful synchronization", () => {
  const node = {textContent:""};
  const context = loadFunctions(["formatDateTime","renderBinanceStatus"], {
    binanceStatusText:node,settingsBinanceStatus:null,renderSyncJob() {},
  });
  context.renderBinanceStatus({configured:true,sync_status:"ERROR",last_sync:null});
  assert.match(node.textContent,/configuré/);
  assert.match(node.textContent,/indisponible/);
  assert.doesNotMatch(node.textContent,/connecté/);
});


test("malformed financial values and numeric underflow are unavailable rather than zero", () => {
  const context = loadFunctions(["formatNumber","pnlValue","positionCurrentValue"]);
  for (const value of [false,true,[],{}," ","0x1","1e-400"]) {
    assert.equal(context.formatFinancial(value), "Indisponible");
    assert.equal(context.formatFinancialPercent(value), "Indisponible");
    assert.equal(context.financialClass(value), "pnl-neutral");
    assert.equal(context.pnlValue({source:"BINANCE",unrealized_pnl:value}), null);
    assert.equal(context.positionCurrentValue({current_value:value}), null);
  }
  assert.equal(context.formatFinancial("0E-400"), "0");
});


test("manual holdings remain visible separately without doubling the Binance wallet", () => {
  const portfolioSummary = {innerHTML:""};
  const context = loadFunctions(["formatNumber","renderPortfolioSummary"], {portfolioSummary,latestPositions:[]});
  context.renderPortfolioSummary({
    portfolio_value_usdc:110,unrealized_pnl:10,realized_pnl:0,total_pnl:10,
    active_positions_count:1,pnl_status:"ESTIMATED",
  },[
    {source:"BINANCE",symbol:"ABCUSDC",quantity:1,status:"ACTIVE",is_active:1,current_value:110},
    {source:"MANUAL",symbol:"ABCUSDC",quantity:1,status:"MANUAL",is_active:1,current_value:50},
  ]);
  assert.match(portfolioSummary.innerHTML,/Manuel déclaré : 50 USDC/);
  assert.match(portfolioSummary.innerHTML,/périmètres à confirmer/);
  assert.doesNotMatch(portfolioSummary.innerHTML,/>160 USDC</);
});

test("unknown manual valuation and unverified closed result stay explicit", () => {
  const portfolioSummary = {innerHTML:""};
  const context = loadFunctions(["formatNumber","renderPortfolioSummary"], {portfolioSummary,latestPositions:[]});
  context.renderPortfolioSummary({
    portfolio_value_usdc:110,unrealized_pnl:10,realized_pnl:0,total_pnl:10,
    active_positions_count:1,pnl_status:"ESTIMATED",
    manual_closed_positions_count:1,manual_realized_pnl_recorded:9,
  },[{source:"MANUAL",symbol:"XYZUSDC",quantity:1,status:"MANUAL",current_value:null}]);
  assert.match(portfolioSummary.innerHTML,/Manuel déclaré : Indisponible USDC/);
  assert.match(portfolioSummary.innerHTML,/Manuel déclaré : 9 USDC \(non vérifié\)/);
});


test("a known manual closed subtotal remains visible when other manual results are unknown", () => {
  const portfolioSummary = {innerHTML:""};
  const context = loadFunctions(["formatNumber","renderPortfolioSummary"], {portfolioSummary,latestPositions:[]});
  context.renderPortfolioSummary({
    portfolio_value_usdc:110,unrealized_pnl:10,realized_pnl:0,total_pnl:10,
    active_positions_count:1,pnl_status:"ESTIMATED",manual_closed_positions_count:2,
    manual_realized_pnl_recorded:null,manual_recorded_results_count:1,
    known_manual_realized_pnl_recorded:9,
  });
  assert.match(portfolioSummary.innerHTML,/sous-total déclaré : 9 USDC \(incomplet\)/);
});

for (const [status, active] of [["QUEUED", true], ["RUNNING", true], ["CANCELLING", true],
  ["PARTIAL", false], ["COMPLETED", false], ["CANCELLED", false], ["PAUSED_QUOTA", false]]) {
  test("sync job active classification: " + status, () => {
    const context = loadFunctions(["syncJobActive"]);
    assert.equal(context.syncJobActive({ status }), active);
    assert.equal(context.syncJobActive(null), false);
  });
}

test("job progress reports remaining markets and imported trades without claiming completeness", () => {
  const context = loadFunctions(["syncJobText"], { formatDateTime: (x) => x });
  const text = context.syncJobText({ status: "PARTIAL", processed: 3, total: 1666,
    remaining: 1663, inserted: 10, result: { unexplored_markets: 1663 } });
  assert.match(text, /incomplets/);
  assert.match(text, /3\/1666/);
  assert.match(text, /1663 restants/);
  assert.match(text, /10 nouveaux trades/);
  assert.match(text, /1663 marchés non explorés/);
});

test("job quota suspension is explicit and does not report a successful sync", () => {
  const context = loadFunctions(["syncJobText"], { formatDateTime: (x) => x });
  const text = context.syncJobText({ status: "PAUSED_QUOTA", retry_at: 120, updated_at: 100 });
  assert.match(text, /Suspendue par les quotas/);
  assert.match(text, /reprise après/);
  assert.doesNotMatch(text, /terminée/);
});

test("malformed sync counts and hostile unknown statuses do not enter progress text", () => {
  const context = loadFunctions(["syncJobText"]);
  const text = context.syncJobText({ status: "<img onerror=alert(1)>", total: "<script>",
    processed: -1, remaining: NaN, inserted: true });
  assert.doesNotMatch(text, /img|script/);
  assert.match(text, /0\/0/);
});

test("bootstrap preview shows costs and explains the historical discovery limitation", () => {
  const context = loadFunctions(["bootstrapPreviewText"]);
  const text = context.bootstrapPreviewText({ symbols: ["AUSDC", "BUSDC"],
    cost: { private_requests_min: 4, estimated_weight: 80 }, unexplored_markets: 1664 });
  assert.match(text, /2 marchés/);
  assert.match(text, /4 requêtes privées/);
  assert.match(text, /80/);
  assert.match(text, /pages supplémentaires/);
  assert.match(text, /positions déjà vendues/);
});

test("rendered progress uses textContent and exposes cancellation without HTML injection", () => {
  const node = { textContent: "", hidden: true, disabled: false };
  let polls = 0;
  const context = loadFunctions(["syncJobActive", "syncJobText", "renderSyncJob"], {
    syncJobStatus: node, syncCancelButton: { hidden: true }, syncResumeButton: { hidden: true },
    syncBootstrapStart: { disabled: false }, syncPreview: null, activeSyncJob: null,
    binanceSyncButton: {}, settingsSyncButton: {}, setButtonLoading() {},
    scheduleSyncPoll() { polls += 1; }, formatDateTime: (x) => x,
  });
  context.renderSyncJob({ status: "RUNNING", processed: 1, total: 3, remaining: 2, inserted: 1 });
  assert.match(node.textContent, /1\/3/);
  assert.equal(context.syncCancelButton.hidden, false);
  assert.equal(context.syncBootstrapStart.disabled, true);
  assert.equal(polls, 1);
});

test("asynchronous sync start returns after acceptance without waiting for completion", async () => {
  let rendered;
  const requests = [];
  const context = loadFunctions(["syncJobActive", "startSyncJob"], {
    isSyncingBinance: false, activeSyncJob: null, binanceStatusText: { textContent: "" },
    apiFetch: async (url, options) => { requests.push([url, options]); return { job: { status: "QUEUED" } }; },
    renderSyncJob: (job) => { rendered = job; },
  });
  await context.startSyncJob();
  assert.equal(rendered.status, "QUEUED");
  assert.equal(context.isSyncingBinance, false);
  assert.equal(requests.length, 1);
  assert.equal(requests[0][0], "/api/binance/sync");
  assert.equal(requests[0][1].method, "POST");
});

test("poll scheduling deduplicates timers and only polls local status", async () => {
  let callback;
  let timers = 0;
  const urls = [];
  const context = loadFunctions(["syncJobActive", "scheduleSyncPoll"], {
    syncPollTimer: null, activeSyncJob: { status: "RUNNING" }, syncJobStatus: { textContent: "" },
    window: { setTimeout(fn, delay) { callback = fn; timers += 1; assert.equal(delay, 2000); return 42; } },
    apiFetch: async (url) => { urls.push(url); return { job: { status: "RUNNING" } }; },
    renderBinanceStatus() {}, loadPersonalData() {}, loadMarket() {},
  });
  context.scheduleSyncPoll();
  context.scheduleSyncPoll();
  assert.equal(timers, 1);
  await callback();
  assert.deepEqual(urls, ["/api/binance/status"]);
});

test("full bootstrap still requires preview and an explicit browser confirmation", () => {
  assert.match(source, /syncPreview\.requires_confirmation && !window\.confirm/);
  assert.match(source, /preview_id: syncPreview\.id/);
  assert.match(source, /encodeURIComponent\(activeSyncJob\.id\)/);
  assert.doesNotMatch(source, /localStorage.*(?:api_secret|api_key)/i);
});

test("session expiry clears a pending synchronization poll", () => {
  const { context, calls } = guardedClient();
  context.syncPollTimer = 42;
  context.activeSyncJob = { id: "private-job" };
  context.syncPreview = { symbols: ["PRIVATEUSDC"] };
  context.handleSessionExpired();
  assert.ok(calls.some((item) => item[0] === "clear-sync-poll" && item[1] === 42));
  assert.equal(context.syncPollTimer, null);
  assert.equal(context.activeSyncJob, null);
  assert.equal(context.syncPreview, null);
});

test("incremental progress exposes historical coverage date and unrefreshed scope", () => {
  const context = loadFunctions(["syncJobText"], { formatDateTime: (x) => x });
  const text = context.syncJobText({ status: "COMPLETED", result: {
    markets_unrefreshed: 1663, historical_coverage_as_of: "2026-01-01T00:00:00+00:00",
  } });
  assert.match(text, /1663 marchés non réactualisés/);
  assert.match(text, /couverture au 2026-01-01/);
});


for (const [state, reason, label] of [
  ["FRESH", null, "Cours Alpha actuel"],
  ["PARTIAL", "NO_CANDLES", "Analyse Alpha partielle"],
  ["STALE", "STALE_DATA", "Dernière cotation trop ancienne"],
  ["UNAVAILABLE", "QUOTA", "Limite de requêtes temporairement atteinte"],
  ["UNAVAILABLE", "NETWORK_ERROR", "Erreur réseau"],
  ["UNAVAILABLE", "SERVICE_UNAVAILABLE", "Service indisponible"],
  ["UNAVAILABLE", "INVALID_RESPONSE", "Réponse invalide"],
  ["INVALID", "NOT_FOUND", "catalogue confirmé"],
]) {
  test("Alpha status displays " + state + "/" + reason + " safely", () => {
    const context = loadFunctions([], { formatNumber: (v) => String(v ?? "-") });
    const item = { current_price: 3, market_data: {
      state, reason, as_of: Date.now() / 1000 - 1, retry_after: 8,
    } };
    const html = context.marketPriceHtml(item);
    assert.ok(html.includes(label), html);
    if (["STALE", "UNAVAILABLE", "INVALID"].includes(state)) {
      assert.match(html, /<strong>-<\/strong>/);
      assert.doesNotMatch(html, /<strong>3<\/strong>/);
    } else assert.match(html, /<strong>3<\/strong>/);
    assert.doesNotMatch(html, /achat|BUY_READY/i);
  });
}

test("Alpha last known value shows native currency and date, never current price", () => {
  const context = loadFunctions([], { formatNumber: (v) => String(v ?? "-") });
  const html = context.marketPriceHtml({ current_price: 99, market_data: {
    state: "STALE", reason: "QUOTA", as_of: 1700000000, last_known_price: 3.25,
    last_known_price_quote_asset: "USDT", last_known_at: 1700000000,
  } });
  assert.match(html, /Dernière valeur connue/);
  assert.match(html, /USDT au 2023-11-14T22:13:20.000Z/);
  assert.match(html, /inutilisable comme cours actuel/);
  assert.doesNotMatch(html, /<strong>99<\/strong>/);
});

test("a fresh Alpha payload expires in the browser after ten minutes", () => {
  const context = loadFunctions([], { formatNumber: (v) => String(v ?? "-") });
  const item = { current_price: 3, market_data: {
    state: "FRESH", as_of: Date.now() / 1000 - 601, last_known_at: Date.now() / 1000 - 601,
    last_known_price: 3, last_known_price_quote_asset: "USDC",
  } };
  assert.equal(context.alphaMarketState(item).state, "STALE");
  assert.match(context.marketPriceHtml(item), /<strong>-<\/strong>/);
});

test("Alpha display rejects unknown states, invalid dates and injected metadata", () => {
  const context = loadFunctions([], { formatNumber: (v) => String(v ?? "-") });
  for (const as_of of [null, "bad", Infinity, Date.now() / 1000 + 60]) {
    const item = { current_price: 4, market_data: { state: "FRESH", as_of } };
    assert.equal(context.alphaMarketState(item).state, "UNAVAILABLE");
  }
  const html = context.marketDataStatusHtml({ market_data: {
    state: '<img src=x onerror="bad">', reason: '<script>bad</script>',
    last_known_price: 2, last_known_at: 1700000000,
    last_known_price_quote_asset: '<img src=x onerror="bad">',
    retry_after: '<script>bad</script>',
  } });
  assert.doesNotMatch(html, /<img|<script|onerror/);
  assert.match(html, /devise inconnue/);
});

test("Alpha stale diagnostics reach desktop, mobile and the open detail", () => {
  const row = watchRow({ symbol: "TOK0", sources: ["WATCH_CANDIDATE", "BINANCE_ALPHA"],
    current_price: null, decision_engine: { decision: "WAIT" },
    market_data: { state: "STALE", reason: "QUOTA", last_known_price: 3.25,
      last_known_at: 1700000000, last_known_price_quote_asset: "USDT" } });
  const { context } = cockpit([row]);
  context.detailTitle.textContent = "TOK0";
  context.renderRows([row]);
  assert.match(context.resultsBody.innerHTML, /Dernière cotation trop ancienne/);
  assert.match(context.detailContent.innerHTML, /Dernière valeur connue/);
  context.openMobileCards = new Set();
  context.factorChips = () => "";
  vm.runInContext(functionSource("renderMobileCards"), context);
  context.renderMobileCards([row]);
  assert.match(context.mobileCards.innerHTML, /Limite de requêtes temporairement atteinte/);
  assert.match(context.mobileCards.innerHTML, /inutilisable comme cours actuel/);
});

test("a Spot price retains its display without Alpha metadata", () => {
  const context = loadFunctions([], { formatNumber: (v) => String(v ?? "-") });
  assert.equal(context.marketPriceHtml({ current_price: 42 }), "<strong>42</strong>");
});

test("a Spot outage is not mislabeled as Alpha and unknown reasons are ignored", () => {
  const context = loadFunctions([], { formatNumber: (v) => String(v ?? "-") });
  const html = context.marketDataStatusHtml({ market_data: {
    state: "UNAVAILABLE", service: "SPOT", reason: "constructor",
  } });
  assert.match(html, /Spot/);
  assert.doesNotMatch(html, /Alpha|function Object|constructor/);
});


for (const [reason, label] of Object.entries({
  INVALID_PAIR: "Paire de trading non disponible", NO_MARKET: "Aucun marché Alpha disponible",
  OFFLINE: "Token actuellement hors ligne", DELISTED: "Token retiré de Binance Alpha",
  OFFLINE_CONFLICT: "Statut du token et du marché contradictoire",
  QUOTA: "Limite de requêtes temporairement atteinte", STALE: "Dernière cotation trop ancienne",
})) {
  test("structured Alpha reason reaches real cockpit rendering: " + reason, () => {
    const row = watchRow({ symbol: "TEST", sources: ["WATCH_CANDIDATE", "BINANCE_ALPHA"],
      current_price: null, decision_engine: { decision: "WAIT" },
      error: '<img src=x onerror="alert(1)">',
      market_data: { state: "UNAVAILABLE", reason, indicative_only: reason === "OFFLINE_CONFLICT" } });
    const { context } = cockpit([row]);
    context.detailTitle.textContent = "TEST";
    context.renderRows([row]);
    assert.ok(context.resultsBody.innerHTML.includes(label));
    assert.ok(context.detailContent.innerHTML.includes(label));
    assert.doesNotMatch(context.resultsBody.innerHTML, /<img src=x|<script/);
    context.openMobileCards = new Set();
    context.factorChips = () => "";
    vm.runInContext(functionSource("renderMobileCards"), context);
    context.renderMobileCards([row]);
    assert.ok(context.mobileCards.innerHTML.includes(label));
    if (reason === "OFFLINE_CONFLICT") assert.match(context.mobileCards.innerHTML, /Prix indicatif/);
  });
}
