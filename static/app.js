// --- CONFIGURATION & SÉLECTEURS ---
const AUTO_REFRESH_MS = 15 * 60 * 1000;
const MIN_ACTIVE_POSITION_VALUE_USDC = 1;

const modeSelect = document.querySelector("#modeSelect");
const symbolInput = document.querySelector("#symbolInput");
const refreshButton = document.querySelector("#refreshButton") ?? document.querySelector("#marketRefreshButton");
const statusText = document.querySelector("#statusText");
const nextRefreshText = document.querySelector("#nextRefreshText");
const binanceStatusText = document.querySelector("#binanceStatusText");
const binanceSyncButton = document.querySelector("#binanceSyncButton") ?? document.querySelector("#marketSyncButton");
const adminResetButton = document.querySelector("#adminResetButton");
const marketNavButton = document.querySelector("#marketNavButton");
const alertsNavButton = document.querySelector("#alertsNavButton");
const settingsModalButton = document.querySelector("#settingsModalButton");
const settingsSyncButton = document.querySelector("#settingsSyncButton");
const settingsBinanceStatus = document.querySelector("#settingsBinanceStatus");
const addPositionButton = document.querySelector("#addPositionButton");
const syncPositionsButton = document.querySelector("#syncPositionsButton");
const addWatchButton = document.querySelector("#addWatchButton");
const addJournalButton = document.querySelector("#addJournalButton");
const portfolioSummary = document.querySelector("#portfolioSummary");

const modalButtons = {
  positionsModalButton: document.querySelector("#positionsModal"),
  watchModalButton: document.querySelector("#watchModal"),
  journalModalButton: document.querySelector("#journalModal"),
  settingsModalButton: document.querySelector("#settingsModal"),
};

const resultsBody = document.querySelector("#resultsBody");
const detailPanel = document.querySelector("#detailPanel");
const detailTitle = document.querySelector("#detailTitle");
const detailSubtitle = document.querySelector("#detailSubtitle");
const detailContent = document.querySelector("#detailContent");
const closeDetailButton = document.querySelector("#closeDetailButton");
const positionsBody = document.querySelector("#positionsBody");
const watchBody = document.querySelector("#watchBody");
const watchFeedback = document.querySelector("#watchFeedback");
const quickWatchForm = document.querySelector("#quickWatchForm");
const marketFeedback = document.querySelector("#marketFeedback");
const journalList = document.querySelector("#journalList");
const journalStats = document.querySelector("#journalStats");
const journalFilterSymbol = document.querySelector("#journalFilterSymbol");
const journalFilterResult = document.querySelector("#journalFilterResult");
const journalFilterEmotion = document.querySelector("#journalFilterEmotion");
const aiAlertsList = document.querySelector("#aiAlertsList");
const mobileCards = document.querySelector("#mobileCards");
const mobileStatusText = document.querySelector("#mobileStatusText");
const mobileRefreshButton = document.querySelector("#mobileRefreshButton");
const mobileMenuButton = document.querySelector("#mobileMenuButton");
const mobileMenuClose = document.querySelector("#mobileMenuClose");
const mobileMenu = document.querySelector("#mobileMenu");
const mobileMenuOverlay = document.querySelector("#mobileMenuOverlay");
const mobileMarketButton = document.querySelector("#mobileMarketButton");
const mobileJournalButton = document.querySelector("#mobileJournalButton");
const mobileAlertsButton = document.querySelector("#mobileAlertsButton");
const mobileSettingsButton = document.querySelector("#mobileSettingsButton");
const positionForm = document.querySelector("#positionForm");
const watchForm = document.querySelector("#watchForm");
const journalForm = document.querySelector("#journalForm");

// --- ÉTAT GLOBAL DE L'APPLICATION ---
let sessionExpired = false;
let sessionExpiryTimer = null;
let latestRows = [];
let latestPositions = [];
let latestWatchCandidates = [];
let latestJournalEntries = [];
let latestJournalAnalytics = null;
let isRefreshing = false;
let isSyncingBinance = false;
let autoRefreshTimer = null;
let nextRefreshAt = null;
let currentDevice = "desktop";
let refreshDebounceTimer = null;
const historyCache = new Map();
const openMobileCards = new Set();
const dashboardState = {
  marketData: [],
  isRefreshing: false,
  isSyncingBinance: false,
  lastRefresh: null,
  lastBinanceSync: null,
};

// --- FONCTIONS UTILITAIRES & TRADUCTEURS DE STYLES ---
function clearFrontendCache() {
  try {
    localStorage.clear();
    sessionStorage.clear();
  } catch {
    // Reste utilisable si le stockage est bloqué par le navigateur
  }
  latestRows = [];
  latestPositions = [];
  latestWatchCandidates = [];
  latestJournalEntries = [];
  latestJournalAnalytics = null;
  historyCache.clear();
  openMobileCards.clear();
  closeDetailPanel();
}

function detectDevice() {
  const width = window.innerWidth;
  if (width < 768) return "mobile";
  if (width <= 1024) return "tablet";
  return "desktop";
}

function applyDeviceMode() {
  currentDevice = detectDevice();
  document.body.dataset.device = currentDevice;
}

function debounce(fn, delay = 250) {
  let timer = null;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), delay);
  };
}

function scheduleRefresh(force = false) {
  window.clearTimeout(refreshDebounceTimer);
  refreshDebounceTimer = window.setTimeout(() => refreshAll(force), 200);
}

function setButtonLoading(button, isLoading, loadingText) {
  if (!button) return;
  if (isLoading) {
    button.dataset.originalText = button.textContent;
    button.textContent = loadingText;
    button.classList.add("is-loading");
    button.disabled = true;
    return;
  }
  button.textContent = button.dataset.originalText || button.textContent;
  button.classList.remove("is-loading");
  button.disabled = false;
  delete button.dataset.originalText;
}

function openModal(modal) {
  if (!modal) return;
  modal.classList.add("open");
  document.body.classList.add("modal-open");
}

function closeModal(modal) {
  if (!modal) return;
  modal.classList.remove("open");
  if (!document.querySelector(".modal.open") && !detailPanel.classList.contains("open")) {
    document.body.classList.remove("modal-open");
  }
}

function openMobileMenu() {
  mobileMenu?.classList.add("open");
  mobileMenu?.setAttribute("aria-hidden", "false");
  mobileMenuButton?.setAttribute("aria-expanded", "true");
  if (mobileMenuOverlay) mobileMenuOverlay.hidden = false;
  document.body.classList.add("mobile-menu-open");
}

function closeMobileMenu() {
  mobileMenu?.classList.remove("open");
  mobileMenu?.setAttribute("aria-hidden", "true");
  mobileMenuButton?.setAttribute("aria-expanded", "false");
  if (mobileMenuOverlay) mobileMenuOverlay.hidden = true;
  document.body.classList.remove("mobile-menu-open");
}

function closeDetailPanel() {
  detailPanel.classList.remove("open");
  document.body.classList.remove("modal-open");
}

function scrollToSection(selector) {
  document.querySelector(selector)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function focusForm(form, shouldReset = true) {
  if (shouldReset) resetForm(form);
  form.scrollIntoView({ behavior: "smooth", block: "start" });
  const firstInput = form.querySelector("input:not([type='hidden']), select");
  window.setTimeout(() => firstInput?.focus(), 180);
}

function signalClass(signal) {
  if (signal === "ACHAT POTENTIEL") return "buy";
  if (signal === "ÉVITER / VENTE POSSIBLE") return "sell";
  return "wait";
}

function decisionClass(decision) {
  return {
    BUY_READY: "decision-buy-ready",
    BUY_WATCH: "decision-buy-watch",
    WAIT: "decision-wait",
    AVOID: "decision-avoid",
    SELL_WATCH: "decision-sell-watch",
    TAKE_PROFIT: "decision-take-profit",
    CUT_LOSS: "decision-cut-loss",
  }[decision] ?? "decision-wait";
}

function qualityClass(quality) {
  if (quality === "EXCELLENT") return "excellent";
  if (quality === "BON") return "good";
  if (quality === "MOYEN") return "medium";
  if (quality === "MAUVAIS") return "bad";
  if (quality === "INVALID") return "invalid";
  return "neutral";
}

function trendClass(trend) {
  if (trend === "VERY_BEARISH") return "trend-very-bearish";
  if (trend === "BEARISH") return "trend-bearish";
  if (trend === "VERY_BULLISH") return "trend-very-bullish";
  if (trend === "BULLISH") return "trend-bullish";
  return "trend-neutral";
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("fr-FR", { maximumFractionDigits: digits });
}

function financialNumber(value) {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string") {
    const text = value.trim();
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text)) return null;
    // A nonzero decimal below Number's range must not display as zero.
    if (Number(text) === 0 && /[1-9]/.test(text.split(/[eE]/)[0])) return null;
  }
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}

function formatFinancial(value, digits = 2, unavailable = "Indisponible") {
  const number = financialNumber(value);
  if (number === null) return unavailable;
  if (number !== 0 && Math.abs(number) < 10 ** -digits) return number.toExponential(3);
  return formatNumber(number, digits);
}

function formatFinancialPercent(value, digits = 2) {
  if (financialNumber(value) === null) return "Indisponible";
  return formatFinancial(value, digits) + "%";
}

function financialClass(value) {
  const number = financialNumber(value);
  return number === null ? "pnl-neutral" : number > 0 ? "momentum-bullish" : number < 0 ? "momentum-bearish" : "pnl-neutral";
}

function financialStatusLabel(position) {
  const labels = {
    VALID: "Données valides", CLOSED: "Clôturée", COST_UNKNOWN: "Coût inconnu",
    HISTORY_INCOMPLETE: "Historique incomplet",
    TEMPORARILY_UNAVAILABLE: "Données indisponibles",
    LEGACY_UNVERIFIED: "À vérifier par synchronisation",
  };
  return labels[position?.data_status] || (position?.is_estimated ? "Estimé" : "");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDateTime(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("fr-FR");
}

function timeframeScore(item, interval) {
  return item.timeframes?.[interval]?.score ?? "-";
}

function mainPattern(item) {
  const patterns = item.patterns ?? item.timeframes?.["1h"]?.patterns;
  if (!patterns) return "-";
  if (patterns.buy_pressure?.detected) return "Pression acheteuse";
  if (patterns.sell_pressure?.detected) return "Pression vendeuse";
  if (patterns.upper_wick?.detected) return patterns.upper_wick.label;
  if (patterns.lower_wick?.detected) return patterns.lower_wick.label;
  if (patterns.consolidation?.detected) return patterns.consolidation.label;
  return "Aucun pattern majeur";
}

function momentumText(item) {
  const medium = item.momentum?.medium ?? item.timeframes?.["1h"]?.momentum?.medium;
  if (!medium) return "-";
  return `${medium.direction} ${formatNumber(medium.change_pct, 2)}%`;
}

function momentumClass(item) {
  const medium = item.momentum?.medium ?? item.timeframes?.["1h"]?.momentum?.medium;
  return medium?.direction === "BEARISH" ? "momentum-bearish" : "momentum-bullish";
}

function volumeAccelText(item) {
  const volume = item.momentum?.volume_acceleration ?? item.timeframes?.["1h"]?.momentum?.volume_acceleration;
  if (!volume) return "-";
  if (volume.ratio > 2) return `Forte accélération (${formatNumber(volume.ratio, 2)}x)`;
  if (volume.detected) return `Accélération (${formatNumber(volume.ratio, 2)}x)`;
  return `Stable (${formatNumber(volume.ratio, 2)}x)`;
}

function decisionOf(item) {
  return item.decision_engine ?? {};
}

function triggerDecisionLabel(decision) {
  const entryAllowed = decision?.decision === "BUY_READY" && decision?.timing_is_actionable === true;
  const confirmedLabel = "Signal technique confirme";
  if (decision?.timing_label) {
    return decision.timing_status === "CONFIRMED" && !entryAllowed
      ? `${decision.timing_label} (entree non autorisee)`
      : decision.timing_label;
  }
  const label = String(decision?.trigger_label ?? "").toLowerCase();
  const score = Number(decision?.trigger_score);
  if (label === "alpha") return "Analyse Alpha partielle";
  if (Number.isFinite(score)) {
    if (score >= 60) return entryAllowed ? confirmedLabel : `${confirmedLabel} (entree non autorisee)`;
    if (score >= 30) return "Signal en formation";
    return "Pas encore de signal d'entree";
  }
  if (label.includes("confirm")) return entryAllowed ? confirmedLabel : `${confirmedLabel} (entree non autorisee)`;
  if (label.includes("partiel")) return "Signal en formation";
  if (label.includes("absent")) return "Pas encore de signal d'entree";
  return decision?.trigger_label ?? "-";
}

function decisionFactorLabel(factor) {
  return {
    "Trigger absent": "Attendre un signal d'entree",
    "Volume insuffisant": "Volume pas assez convaincant",
    "Trend global bearish": "Tendance encore fragile",
    "Trend global VERY_BEARISH": "Tendance trop baissiere",
    "Risk/reward mauvais": "Risque trop eleve",
    "BTC trend defavorable": "BTC pese sur le contexte",
    "Prix sous MA25 et MA99": "Prix sous les moyennes cles",
    "Proche resistance": "Prix proche d'une resistance",
    "Meche haute importante": "Rejet vendeur visible",
    "Risk/reward favorable": "Risque/reward interessant",
    "Volume en acceleration": "Volume en acceleration",
    "Pression acheteuse": "Pression acheteuse",
    "Breakout confirme": "Breakout confirme",
    "Momentum 1h positif": "Momentum 1h positif",
    "Consolidation propre": "Consolidation propre",
  }[factor] ?? factor;
}

function decisionSummaryLabel(summary) {
  return {
    "Setup interessant, mais le trigger d'entree reste partiel.": "Zone interessante, mais le signal d'entree doit encore se confirmer.",
    "Contexte, setup, trigger et risque sont coherents pour une surveillance active d'achat.": "Contexte, zone et timing sont alignes pour une surveillance active d'achat.",
    "Contexte fragile et risque trop eleve pour chercher une entree.": "Contexte fragile : mieux vaut eviter de chercher une entree pour l'instant.",
    "Signaux mixtes, attendre une confirmation plus nette.": "Signaux mixtes : attendre une confirmation plus nette.",
  }[summary] ?? summary;
}

function rawMainBlockingFactor(item) {
  return decisionOf(item).blocking_factors?.[0] ?? "-";
}

function mainBlockingFactor(item) {
  return decisionFactorLabel(rawMainBlockingFactor(item));
}

function shortText(value, maxLength = 42) {
  if (!value) return "-";
  const text = String(value);
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}...` : text;
}

function historySpark(history) {
  if (!history?.length) return "-";
  return history.slice(0, 8).reverse().map((point) => point.score).join(" → ");
}

function isActiveMarketPosition(position) {
  if (!position || !position.is_active || Number(position.quantity ?? 0) <= 0) return false;
  if (position.source === "MANUAL") return position.status !== "CLOSED";
  return position.status === "ACTIVE" && (position.current_value == null || Number(position.current_value) >= MIN_ACTIVE_POSITION_VALUE_USDC);
}

function positionForSymbol(symbol) {
  return latestPositions.find((position) => (
    position.symbol === symbol
    && isActiveMarketPosition(position)
  ));
}

function watchForSymbol(symbol) {
  return latestWatchCandidates.find((candidate) => candidate.symbol === symbol);
}

// --- RENDU DES COMPOSANTS GRAPHIQUES ---

function marketSourceLabel(source) {
  const normalized = String(source || "").toUpperCase();
  if (normalized === "BINANCE_SPOT" || normalized === "BINANCE" || normalized === "MIXED") return "Spot";
  if (normalized === "BINANCE_ALPHA") return "Alpha";
  if (normalized === "MANUAL") return "Manuel";
  return "Inconnu";
}

function renderMarketSourceBadge(source) {
  const label = marketSourceLabel(source);
  return `<span class="badge ${badgeClass(label)}">${label}</span>`;
}

function renderStatusBadges(item) {
  const badges = [];
  const position = item.position_info;
  const watch = item.watch_info;

  if (position) {
    badges.push(`<span class="badge good">Détenue</span>`);
    badges.push(renderMarketSourceBadge(position.market_source ?? position.source));
  }
  if (watch) {
    badges.push(`<span class="badge medium">Surveillance</span>`);
    badges.push(renderMarketSourceBadge(watch.market_source));
  }
  if (item.opportunity_info) badges.push(`<span class="badge medium">Opportunité</span>`);
  if (!badges.length) badges.push(renderMarketSourceBadge("UNKNOWN"));

  return [...new Set(badges)].join("");
}

function symbolMetaHtml(symbol) {
  const position = positionForSymbol(symbol);
  const watch = watchForSymbol(symbol);
  const badges = [];
  const notes = [];

  if (position) {
    badges.push(`<span class="badge good">Détenue</span>`);
    badges.push(renderMarketSourceBadge(position.market_source ?? position.source));
    notes.push(`PM: ${formatFinancial(position.average_buy_price, 6, "Coût inconnu")}`);
    if (financialStatusLabel(position)) notes.push(financialStatusLabel(position));
    notes.push(`PnL: ${formatFinancialPercent(position.profit_loss_pct ?? position.gain_loss_pct, 2)}`);
    notes.push(`Réalisé: ${formatFinancial(position.realized_pnl, 2)} USDC`);
    if (position.take_profit_1) notes.push(`TP1: ${formatNumber(position.take_profit_1, 6)}`);
  }
  if (watch) {
    badges.push(`<span class="badge medium">Surveillance</span>`);
    badges.push(renderMarketSourceBadge(watch.market_source));
  }

  if (badges.length === 0 && notes.length === 0) return "";

  return `
    <div class="symbol-badges">${badges.join("")}</div>
    <div class="symbol-note">${notes.join(" | ")}</div>
  `;
}

function compactSymbolMetaHtml(symbol) {
  const position = positionForSymbol(symbol);
  const watch = watchForSymbol(symbol);
  const badges = [];
  const notes = [];

  if (position) {
    const pnlPct = position.profit_loss_pct ?? position.gain_loss_pct;
    const pnlClass = Number(pnlPct) > 0 ? "pnl-positive" : Number(pnlPct) < 0 ? "pnl-negative" : "pnl-neutral";

    badges.push(`<span class="badge good">Détenue</span>`);
    badges.push(renderMarketSourceBadge(position.market_source ?? position.source));
    notes.push(`PM: ${formatFinancial(position.average_buy_price, 6, "Coût inconnu")}`);
    if (financialStatusLabel(position)) notes.push(financialStatusLabel(position));
    notes.push(`<span class="${pnlClass}">PnL: ${formatFinancialPercent(pnlPct, 2)}</span>`);
  }
  if (watch) {
    badges.push(`<span class="badge medium">Surveillance</span>`);
    badges.push(renderMarketSourceBadge(watch.market_source));
  }

  if (!badges.length && !notes.length) return "";

  return `
    <div class="symbol-badges">${badges.join("")}</div>
    ${notes.length ? `<div class="symbol-position-line">${notes.join(" | ")}</div>` : ""}
  `;
}

function badgeClass(label) {
  if (label === "Détenue" || label === "Spot") return "good";
  if (label === "Alpha") return "excellent";
  if (label === "Surveillance" || label === "Opportunité") return "medium";
  if (label === "Inconnu") return "invalid";
  return "neutral";
}

function marketSymbolMetaHtml(item) {
  const badges = renderStatusBadges(item);
  const notes = [];
  const position = item.position_info;
  const watch = item.watch_info;
  const opportunity = item.opportunity_info;

  if (position) {
    const pnlPct = position.unrealized_pnl_pct;
    const pnlClass = Number(pnlPct) > 0 ? "pnl-positive" : Number(pnlPct) < 0 ? "pnl-negative" : "pnl-neutral";
    notes.push(`PM: ${formatFinancial(position.average_buy_price, 6, "Coût inconnu")}`);
    if (financialStatusLabel(position)) notes.push(financialStatusLabel(position));
    notes.push(`<span class="${pnlClass}">PnL: ${formatFinancialPercent(pnlPct, 2)} / ${formatFinancial(position.unrealized_pnl, 2)} USDC</span>`);
  }
  if (watch) {
    notes.push(`Visé: ${formatNumber(watch.target_buy_price, 6)}`);
    notes.push(`Dist: ${formatNumber(watch.distance_to_target_pct, 2)}%`);
    notes.push(`Prio: ${escapeHtml(watch.priority ?? "-")}`);
  }
  const sourceInfo = position?.market_source === "BINANCE_ALPHA" ? position : watch?.market_source === "BINANCE_ALPHA" ? watch : null;
  if (sourceInfo) {
    if (sourceInfo.price_change_pct !== undefined && sourceInfo.price_change_pct !== null) {
      notes.push(`Var: ${formatNumber(sourceInfo.price_change_pct, 2)}%`);
    }
    if (sourceInfo.quote_volume !== undefined && sourceInfo.quote_volume !== null) {
      notes.push(`Vol: ${formatNumber(sourceInfo.quote_volume, 0)}$`);
    }
  }
  if (opportunity) {
    notes.push(`Scan: ${escapeHtml(opportunity.scan_score ?? "-")}`);
    notes.push(escapeHtml(shortText(opportunity.reason, 48)));
  }

  return `
    ${badges ? `<div class="symbol-badges">${badges}</div>` : ""}
    ${notes.length ? `<div class="symbol-position-line">${notes.join(" | ")}</div>` : ""}
  `;
}

function marketActionsHtml(item, compact = false) {
  const actions = [];
  const position = item.position_info;
  const hasBinanceSource = (item.sources ?? []).some((source) => ["BINANCE", "MIXED"].includes(source));
  const isManualPosition = position?.id && position.source === "MANUAL";

  if (item.watch_info?.id) {
    actions.push(`<button type="button" class="${compact ? "mobile-detail-button danger-action" : "inline-action danger-action"}" data-market-remove-watch="${escapeHtml(item.watch_info.id)}">Retirer surveillance</button>`);
  }
  if (isManualPosition) {
    actions.push(`<button type="button" class="${compact ? "mobile-detail-button danger-action" : "inline-action danger-action"}" data-market-delete-position="${escapeHtml(position.id)}">Supprimer position</button>`);
  }
  if (!actions.length && hasBinanceSource) {
    actions.push(`<span class="managed-source">Géré par Binance Spot</span>`);
  }
  return actions.join("");
}

function shortDecisionSummary(item) {
  const summary = item.summary_reasons?.[0];
  const resistanceDistance = item.distance_to_resistance_pct;
  const supportDistance = item.distance_to_support_pct;
  const medium = item.momentum?.medium;
  const volume = item.momentum?.volume_acceleration;
  const pattern = mainPattern(item);
  const trend = item.global_trend ?? item.trend_strength?.trend;

  if (trend === "VERY_BEARISH" || trend === "BEARISH") {
    return "Marché globalement bearish malgré certains niveaux locaux.";
  }
  if (resistanceDistance !== null && resistanceDistance !== undefined && resistanceDistance >= 0 && resistanceDistance < 1) {
    return "Momentum à surveiller, mais le prix est proche de la résistance.";
  }
  if (supportDistance !== null && supportDistance !== undefined && supportDistance >= 0 && supportDistance < 2) {
    return "Prix proche du support, setup à surveiller si le volume confirme.";
  }
  if (medium?.direction === "BEARISH") {
    return "Timing fragile, mieux vaut attendre un signal plus propre.";
  }
  if (volume && !volume.detected && pattern === "Consolidation probable") {
    return "Consolidation probable sans confirmation volume.";
  }
  if (summary) return summary;
  return "Aucun signal fort, surveillance préférable.";
}

function marketContextText(item) {
  const trend = item.global_trend ?? item.trend_strength?.trend ?? "NEUTRAL";
  const quality = item.setup_quality ?? "-";
  const medium = item.momentum?.medium ?? item.timeframes?.["1h"]?.momentum?.medium;

  if (["VERY_BEARISH", "BEARISH"].includes(trend) && medium?.direction === "BEARISH") {
    return `Contexte ${trend.toLowerCase()} : le risk/reward ne suffit pas à valider le setup.`;
  }
  if (["BULLISH", "VERY_BULLISH"].includes(trend)) {
    return `Contexte ${trend.toLowerCase()} : la tendance soutient mieux la qualité ${quality}.`;
  }
  return "Contexte neutre : privilégier la confirmation du volume et du momentum.";
}

function scheduleSessionExpiry() {
  const remaining = Number(document.querySelector('meta[name="session-expires-in"]')?.content ?? 0);
  if (!Number.isFinite(remaining) || remaining <= 0) {
    handleSessionExpired();
    return;
  }
  sessionExpiryTimer = setTimeout(handleSessionExpired, remaining * 1000);
}

function handleSessionExpired() {
  if (sessionExpired) return;
  sessionExpired = true;
  clearTimeout(autoRefreshTimer);
  clearTimeout(refreshDebounceTimer);
  clearTimeout(sessionExpiryTimer);
  clearFrontendCache();
  document.querySelectorAll("#resultsBody, #mobileCards, #positionsBody, #watchBody, #journalList, #journalStats, #aiAlertsList, #portfolioSummary, #detailContent")
    .forEach((element) => { element.innerHTML = ""; });
  document.querySelectorAll("#positionForm, #watchForm, #quickWatchForm, #journalForm, #adminResetForm")
    .forEach((form) => form.reset());
  window.location.replace("/login?expired=1");
}

async function apiFetch(url, options = {}) {
  if (sessionExpired) throw new Error("Session expiree. Reconnectez-vous.");
  const target = new URL(url, window.location.origin);
  if (target.origin !== window.location.origin || target.username || target.password || target.hash || !target.pathname.startsWith("/api/")) {
    throw new Error("Requete externe interdite.");
  }
  const headers = { "Content-Type": "application/json", ...options.headers, "X-CSRF-Guard": "1" };
  const response = await fetch(url, {
    ...options,
    headers,
    credentials: "same-origin",
    redirect: "error",
  });
  if (sessionExpired) throw new Error("Session expiree. Reconnectez-vous.");
  if (response.status === 401) {
    handleSessionExpired();
    throw new Error("Session expiree. Reconnectez-vous.");
  }
  const contentType = response.headers.get("content-type") || "";
  const text = await response.text();
  if (sessionExpired) throw new Error("Session expiree. Reconnectez-vous.");
  let payload = null;

  if (text && contentType.includes("application/json")) {
    try {
      payload = JSON.parse(text);
    } catch {
      throw new Error("Réponse API invalide.");
    }
  } else if (text && !contentType.includes("application/json")) {
    throw new Error(response.ok ? "Réponse API non JSON." : "Erreur serveur non lisible.");
  }

  if (!response.ok) {
    throw new Error(payload?.error || payload?.detail || "Erreur API");
  }
  if (!payload && response.status !== 204) {
    throw new Error("Réponse API vide.");
  }
  return payload;
}

function setWatchFeedback(message, type = "neutral") {
  if (!watchFeedback) return;
  watchFeedback.textContent = message;
  watchFeedback.dataset.state = type;
}

function setMarketFeedback(message, type = "neutral") {
  if (!marketFeedback) return;
  marketFeedback.textContent = message;
  marketFeedback.dataset.state = type;
}

async function enrichWithHistory(rows) {
  return Promise.all(rows.map(async (item) => {
    if (item.error) return item;
    if (historyCache.has(item.symbol)) return { ...item, history: historyCache.get(item.symbol) };
    try {
      const history = await apiFetch(`/api/history/${encodeURIComponent(String(item.symbol))}?limit=8`);
      historyCache.set(item.symbol, history);
      return { ...item, history };
    } catch {
      return item;
    }
  }));
}

function reconcileOpenDetail() {
  if (!detailPanel.classList.contains("open")) return;
  const item = latestRows.find((row) => row.symbol === detailTitle.textContent);
  if (item) renderDetail(item);
  else closeDetailPanel();
}

function renderRows(rows) {
  latestRows = [...rows];
  dashboardState.marketData = latestRows;

  if (!latestRows.length) {
    resultsBody.innerHTML = `
      <tr class="empty-row">
        <td colspan="9">
          Aucune donnée marché disponible pour le moment.
          <button type="button" data-launch-scan>Lancer un scan</button>
        </td>
      </tr>
    `;
    if (mobileCards) {
      mobileCards.innerHTML = `
        <article class="market-card empty-card">
          <p>Aucune donnée marché disponible pour le moment.</p>
          <button type="button" class="mobile-detail-button" data-launch-scan>Lancer un scan</button>
        </article>
      `;
    }
    reconcileOpenDetail();
    return;
  }

  resultsBody.innerHTML = latestRows.map((item, index) => {
    if (item.error) {
      return `
        <tr data-row-index="${index}" class="unavailable-row">
          <td><div class="symbol-cell"><strong>${escapeHtml(item.symbol)}</strong>${marketSymbolMetaHtml(item)}</div></td>
          <td><strong>${formatNumber(item.current_price, 6)}</strong></td>
          <td><span class="signal decision-wait">Analyse indisponible</span></td>
          <td>-</td>
          <td><span class="trend-badge trend-neutral">UNKNOWN</span></td>
          <td><span class="badge invalid">Indisponible</span></td>
          <td>-</td>
          <td>-</td>
          <td><span class="summary" title="${escapeHtml(item.error)}">${escapeHtml(item.error)}</span>${marketActionsHtml(item)}</td>
        </tr>
      `;
    }

    const signal = item.global_signal ?? "-";
    const decision = decisionOf(item);
    const trend = item.global_trend ?? item.trend_strength?.trend ?? "-";
    const quality = item.setup_quality ?? item.risk_reward?.quality ?? "-";

    return `
      <tr data-row-index="${index}" class="clickable-row">
        <td><div class="symbol-cell"><strong>${escapeHtml(item.symbol)}</strong>${marketSymbolMetaHtml(item)}</div></td>
        <td><strong>${formatNumber(item.current_price, 6)}</strong></td>
        <td><span class="signal ${decisionClass(decision.decision)}">${escapeHtml(decision.decision_label ?? "-")}</span></td>
        <td><strong>${escapeHtml(decision.confidence ?? "-")}</strong><span class="score-unit">/100</span></td>
        <td><span class="trend-badge ${trendClass(trend)}">${escapeHtml(String(trend).replace('_', ' '))}</span></td>
        <td><span class="badge ${qualityClass(quality)}">${escapeHtml(quality)}</span></td>
        <td>${escapeHtml(triggerDecisionLabel(decision))}</td>
        <td><strong>${escapeHtml(item.global_score ?? "-")}</strong></td>
        <td>
          <span class="summary" title="${escapeHtml(rawMainBlockingFactor(item))}">${escapeHtml(mainBlockingFactor(item))}</span>
          ${marketActionsHtml(item)}
        </td>
      </tr>
    `;
  }).join("");
  renderMobileCards(latestRows);
  reconcileOpenDetail();
}

function sparklineSvg(values) {
  const nums = (values ?? []).map(Number).filter((value) => Number.isFinite(value)).slice(-12);
  if (nums.length < 2) return "";
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const span = max - min || 1;
  const points = nums.map((value, index) => {
    const x = (index / (nums.length - 1)) * 100;
    const y = 28 - ((value - min) / span) * 24;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `<svg class="sparkline" viewBox="0 0 100 32" aria-hidden="true"><polyline points="${points}"></polyline></svg>`;
}

function alertTypeLabel(type) {
  return {
    OPPORTUNITY: "Opportunité",
    WATCH: "Surveillance",
    RISK: "Risque",
    MARKET: "Marché",
  }[type] ?? type;
}

function alertCssToken(value, allowed, fallback) {
  return allowed.includes(value) ? value.toLowerCase() : fallback;
}

function renderAiAlerts(payload) {
  if (!aiAlertsList) return;
  const alerts = payload.alerts ?? [];
  if (!alerts.length) {
    aiAlertsList.innerHTML = `<div class="empty-state">Aucune alerte IA prioritaire.</div>`;
    return;
  }
  aiAlertsList.innerHTML = alerts.map((alert) => `
    <button type="button" class="ai-alert-card ai-${alertCssToken(alert.type, ["OPPORTUNITY", "WATCH", "RISK", "MARKET"], "market")} severity-${alertCssToken(alert.severity, ["LOW", "MEDIUM", "HIGH", "CRITICAL"], "low")}" ${alert.symbol ? `data-decision-symbol="${escapeHtml(alert.symbol)}"` : ""}>
      <div>
        <span class="ai-alert-type">${escapeHtml(alertTypeLabel(alert.type))}</span>
        <strong>${escapeHtml(alert.title)}</strong>
        <small>${escapeHtml(alert.symbol ?? "Marché global")} · ${escapeHtml(alert.confidence ?? "-")} / 100</small>
      </div>
      <p>${escapeHtml(shortText(alert.message, 130))}</p>
    </button>
  `).join("");
}

async function renderScoreEvolution(symbol) {
  const payload = await apiFetch(`/api/score-history/${encodeURIComponent(String(symbol))}?limit=12`);
  return `
    <div class="detail-box">
      <h3>Evolution V5</h3>
      ${sparklineSvg(payload.history)}
      <div class="kv-grid compact">
        <span>Tendance :</span><strong>${escapeHtml(payload.trend)}</strong>
        <span>Acceleration :</span><strong>${escapeHtml(payload.acceleration)}</strong>
      </div>
    </div>
  `;
}

function factorChips(factors) {
  const items = factors?.length ? factors : ["-"];
  return items.slice(0, 4).map((factor) => `<span>${escapeHtml(shortText(decisionFactorLabel(factor), 36))}</span>`).join("");
}

function renderMobileCards(rows) {
  if (!mobileCards) return;

  mobileCards.innerHTML = rows.map((item, index) => {
    if (item.error) {
      return `
        <article class="market-card error-card">
          <div class="market-card-head">
            <strong>${escapeHtml(item.symbol)}</strong>
            <span class="badge invalid">Analyse indisponible</span>
          </div>
          ${marketSymbolMetaHtml(item)}
          <p>${escapeHtml(shortText(item.error, 90))}</p>
        </article>
      `;
    }

    const decision = decisionOf(item);
    const trend = item.global_trend ?? item.trend_strength?.trend ?? "-";
    const setupQuality = item.setup_quality ?? item.risk_reward?.quality ?? "-";
    const expanded = openMobileCards.has(item.symbol);
    const blocking = mainBlockingFactor(item);
    const symbolMeta = marketSymbolMetaHtml(item);

    return `
      <article class="market-card ${expanded ? "open" : ""}" data-mobile-card="${escapeHtml(item.symbol)}">
        <button type="button" class="market-card-toggle" data-mobile-toggle="${escapeHtml(item.symbol)}" aria-expanded="${expanded}">
          <div class="market-card-head">
            <strong>${escapeHtml(item.symbol)}</strong>
            <span class="signal ${decisionClass(decision.decision)}">${escapeHtml(decision.decision_label ?? "-")}</span>
          </div>
          ${symbolMeta}
          <div class="market-card-badges">
            <span class="badge neutral">Confiance ${escapeHtml(decision.confidence ?? "-")}</span>
            <span class="trend-badge ${trendClass(trend)}">${escapeHtml(String(trend).replace("_", " "))}</span>
          </div>
          <div class="market-card-grid">
            <span>Prix</span><strong>${formatNumber(item.current_price, 6)}</strong>
            <span>Setup</span><strong><span class="badge ${qualityClass(setupQuality)}">${escapeHtml(setupQuality)}</span></strong>
            <span>Timing technique</span><strong>${escapeHtml(triggerDecisionLabel(decision))}</strong>
            <span>Blocage</span><strong>${escapeHtml(shortText(blocking, 28))}</strong>
          </div>
        </button>
        <div class="market-card-details">
          <div class="mobile-tf-grid">
            <span>15m <strong>${escapeHtml(timeframeScore(item, "15m"))}</strong></span>
            <span>1h <strong>${escapeHtml(timeframeScore(item, "1h"))}</strong></span>
            <span>4h <strong>${escapeHtml(timeframeScore(item, "4h"))}</strong></span>
            <span>1d <strong>${escapeHtml(timeframeScore(item, "1d"))}</strong></span>
          </div>
          <div class="market-card-grid compact">
            <span>RR</span><strong>${formatNumber(item.risk_reward?.ratio, 2)}</strong>
            <span>Momentum</span><strong class="${momentumClass(item)}">${escapeHtml(shortText(momentumText(item), 24))}</strong>
            <span>Support</span><strong>${formatNumber(item.support, 6)}</strong>
            <span>Resistance</span><strong>${formatNumber(item.resistance, 6)}</strong>
          </div>
          <div class="mobile-factor-row">
            <strong>Positifs</strong>
            <div>${factorChips(decision.positive_factors)}</div>
          </div>
          <div class="mobile-factor-row">
            <strong>Bloquants</strong>
            <div>${factorChips(decision.blocking_factors)}</div>
          </div>
          <button type="button" class="mobile-detail-button" data-mobile-detail="${index}">Details complets</button>
          ${marketActionsHtml(item, true)}
        </div>
      </article>
    `;
  }).join("");
}

let detailRenderId = 0;

function renderDetail(item) {
  if (!item) return;
  const renderId = ++detailRenderId;
  if (item.error) {
    detailTitle.textContent = item.symbol;
    detailSubtitle.textContent = "Analyse marche indisponible";
    detailContent.innerHTML = `
      <div class="detail-main-grid">
        <div class="detail-box portfolio-box">
          <h3>Portefeuille</h3>
          ${item.position_info ? `
            <div class="detail-kv"><span>Source</span><strong>${marketSourceLabel(item.position_info.market_source ?? item.position_info.source)}</strong></div>
            <div class="detail-kv"><span>Quantite</span><strong>${formatFinancial(item.position_info.decimal_values?.remaining_quantity ?? item.position_info.quantity, 8)}</strong></div>
            <div class="detail-kv"><span>Prix moyen</span><strong>${formatFinancial(item.position_info.average_buy_price, 6, "Coût inconnu")}</strong></div>
          ` : `<p>Aucune position active.</p>`}
        </div>
        <div class="detail-box">
          <h3>Analyse</h3>
          <p>${escapeHtml(item.error)}</p>
          <p class="muted">Cette position reste visible dans le cockpit, mais aucune donnee Binance exploitable n'est disponible pour ce symbole.</p>
          <div class="detail-actions">${marketActionsHtml(item, true)}</div>
        </div>
      </div>
    `;
    detailPanel.classList.add("open");
    return;
  }

  const signal = item.global_signal ?? "-";
  const setupQuality = item.setup_quality ?? item.risk_reward?.quality ?? "-";
  const riskQuality = item.risk_reward?.quality ?? "-";
  const trend = item.global_trend ?? item.trend_strength?.trend ?? "-";
  const decision = decisionOf(item);
  const position = positionForSymbol(item.symbol);
  const rsi1h = item.timeframes?.["1h"]?.rsi;
  const trendStrength = item.trend_strength ?? item.timeframes?.["1h"]?.trend_strength;
  const pnlPct = position?.profit_loss_pct ?? position?.gain_loss_pct;
  const pnlAmount = position?.unrealized_pnl ?? position?.gain_loss_amount;
  const pnlClass = Number(pnlPct) > 0 ? "momentum-bullish" : Number(pnlPct) < 0 ? "momentum-bearish" : "pnl-neutral";
  const sourceLabel = marketSourceLabel(position?.market_source ?? position?.source);
  const mainBlocker = mainBlockingFactor(item);
  const positionSection = position ? `
    <div class="detail-box portfolio-box">
      <h3>Portefeuille</h3>
      <div class="kv-grid compact portfolio-grid">
        <span>Source :</span><strong>${escapeHtml(sourceLabel)}</strong>
        <span>Quantité :</span><strong>${formatFinancial(position.decimal_values?.remaining_quantity ?? position.quantity, 6)}</strong>
        <span>Prix moyen :</span><strong>${formatFinancial(position.average_buy_price, 6, "Coût inconnu")}</strong>
        <span>Valeur actuelle :</span><strong>${formatFinancial(position.current_value, 2)} USDC</strong>
        <span>PnL latent % :</span><strong class="${pnlClass}">${formatFinancialPercent(pnlPct, 2)}</strong>
        <span>PnL latent :</span><strong class="${financialClass(pnlAmount)}">${formatFinancial(pnlAmount, 2)} USDC</strong>
        <span>PnL réalisé :</span><strong>${formatFinancial(position.realized_pnl, 2)} USDC</strong>
        <span>TP1 :</span><strong>${formatNumber(position.take_profit_1, 6)}</strong>
        <span>TP2 :</span><strong>${formatNumber(position.take_profit_2, 6)}</strong>
        <span>Stop-loss :</span><strong>${formatNumber(position.stop_loss, 6)}</strong>
      </div>
    </div>
  ` : `
    <div class="detail-box portfolio-box subtle-box">
      <h3>Portefeuille</h3>
      <p class="muted-summary">Aucune position active.</p>
    </div>
  `;

  detailTitle.textContent = item.symbol;
  detailSubtitle.textContent = `${decision.decision_label ?? signal} - Score ${item.global_score}`;
  
  detailContent.innerHTML = `
    <div class="detail-main-grid">
      ${positionSection}
      <div class="detail-box decision-box">
        <h3>Décision</h3>
        <div class="decision-metrics">
          <span class="signal ${decisionClass(decision.decision)}">${escapeHtml(decision.decision_label ?? "-")}</span>
          <span class="badge neutral">Confiance ${escapeHtml(decision.confidence ?? "-")}</span>
          <span class="badge neutral">Score ${escapeHtml(item.global_score ?? "-")}</span>
          <span class="trend-badge ${trendClass(trend)}">${escapeHtml(trend)}</span>
        </div>
        <div class="kv-grid compact decision-kv">
          <span>Blocage principal :</span><strong title="${escapeHtml(mainBlocker)}">${escapeHtml(shortText(mainBlocker, 34))}</strong>
          <span>Timing technique :</span><strong>${escapeHtml(triggerDecisionLabel(decision))}</strong>
          <span>Entree autorisee :</span><strong>${decision.timing_is_actionable === true && decision.decision === "BUY_READY" ? "Oui" : "Non"}</strong>
          <span>Action timing :</span><strong>${escapeHtml(decision.timing_action ?? "-")}</strong>
          <span>Setup :</span><strong><span class="badge ${qualityClass(setupQuality)}">${escapeHtml(setupQuality)}</span></strong>
          <span>Signal actuel :</span><strong><span class="signal ${signalClass(signal)}">${escapeHtml(signal)}</span></strong>
        </div>
        <p class="decision-summary">${escapeHtml(decisionSummaryLabel(decision.reason_summary) ?? shortDecisionSummary(item))}</p>
        <p class="decision-summary muted-summary">${escapeHtml(decision.timing_reason ?? "")}</p>
        <p class="decision-summary muted-summary">${escapeHtml(marketContextText(item))}</p>
        <div class="factor-list inline-factors">
          <strong>Positifs</strong>
          ${(decision.positive_factors ?? ["-"]).slice(0, 4).map((factor) => `<span>${escapeHtml(decisionFactorLabel(factor))}</span>`).join("")}
        </div>
        <div class="factor-list inline-factors">
          <strong>Bloquants</strong>
          ${(decision.blocking_factors ?? ["-"]).slice(0, 4).map((factor) => `<span>${escapeHtml(decisionFactorLabel(factor))}</span>`).join("")}
        </div>
        <div class="detail-actions">
          <button type="button" class="quick-journal" data-symbol="${escapeHtml(item.symbol)}">Note journal</button>
          <button type="button" data-add-watch-symbol="${escapeHtml(item.symbol)}">Ajouter surveillance</button>
          <button type="button" data-add-position-symbol="${escapeHtml(item.symbol)}">Position manuelle</button>
          ${marketActionsHtml(item, true)}
        </div>
      </div>
    </div>
    <div class="detail-box">
      <h3>Niveaux clés</h3>
      <div class="kv-grid">
        <span>Prix :</span><strong>${formatNumber(item.current_price, 6)}</strong>
        <span>Support :</span><strong>${formatNumber(item.support, 6)}</strong>
        <span>Résistance :</span><strong>${formatNumber(item.resistance, 6)}</strong>
        <span>Distance support :</span><strong>${formatNumber(item.distance_to_support_pct, 2)}%</strong>
        <span>Distance rés. :</span><strong>${formatNumber(item.distance_to_resistance_pct, 2)}%</strong>
        <span>Risk/Reward :</span><strong>${formatNumber(item.risk_reward?.ratio, 2)}</strong>
      </div>
      <div class="quality-line">
        <span>Qualité R:R :</span>
        <span class="badge ${qualityClass(riskQuality)}">${escapeHtml(riskQuality)}</span>
      </div>
    </div>
    <div class="detail-box">
      <h3>Timing marché</h3>
      <div class="tf-grid">
        <span>15m <strong>${escapeHtml(timeframeScore(item, "15m"))}</strong></span>
        <span>1h <strong>${escapeHtml(timeframeScore(item, "1h"))}</strong></span>
        <span>4h <strong>${escapeHtml(timeframeScore(item, "4h"))}</strong></span>
        <span>1d <strong>${escapeHtml(timeframeScore(item, "1d"))}</strong></span>
      </div>
      <div class="kv-grid compact detail-metrics-grid">
        <span>RSI :</span><strong>${formatNumber(rsi1h, 2)}</strong>
        <span>Momentum :</span><strong class="${momentumClass(item)}">${escapeHtml(momentumText(item))}</strong>
        <span>Trend strength :</span><strong>${escapeHtml(trendStrength?.trend ?? trend)} ${escapeHtml(trendStrength?.score ?? "")}</strong>
        <span>Pattern :</span><strong>${escapeHtml(mainPattern(item))}</strong>
        <span>Volume :</span><strong>${escapeHtml(volumeAccelText(item))}</strong>
      </div>
    </div>
    <div class="detail-box">
      <h3>Scores techniques</h3>
      <div class="score-grid">
        <span>Context <strong>${escapeHtml(decision.context_score ?? "-")}</strong></span>
        <span>Setup <strong>${escapeHtml(decision.setup_score ?? "-")}</strong></span>
        <span>Trigger <strong>${escapeHtml(decision.trigger_score ?? "-")}</strong></span>
        <span>Risk <strong>${escapeHtml(decision.risk_score ?? "-")}</strong></span>
        <span>Market <strong>${escapeHtml(decision.market_score ?? "-")}</strong></span>
        <span>Global <strong>${escapeHtml(item.global_score ?? "-")}</strong></span>
      </div>
    </div>
  `;
  detailPanel.classList.add("open");
  if (currentDevice !== "desktop") document.body.classList.add("modal-open");
  renderScoreEvolution(item.symbol)
    .then((html) => {
      if (renderId === detailRenderId && detailPanel.classList.contains("open") && detailTitle.textContent === item.symbol) {
        detailContent.insertAdjacentHTML("beforeend", html);
      }
    })
    .catch(() => {});
}

function formToPayload(form) {
  const formData = new FormData(form);
  const payload = {};
  for (const [key, value] of formData.entries()) {
    if (key === "id" || value === "") continue;
    const input = form.elements[key];
    payload[key] = input?.type === "number" ? Number(value) : value;
  }
  return payload;
}

function fillForm(form, item) {
  for (const element of form.elements) {
    if (!element.name) continue;
    element.value = item[element.name] ?? "";
  }
}

function resetForm(form) {
  form.reset();
  if (form.elements.id) form.elements.id.value = "";
}

function renderPositions(positions) {
  const visiblePositions = positions.filter((position) => {
    if (["BINANCE", "MIXED"].includes(position.source)) {
      return position.status === "ACTIVE" && (position.current_value == null || Number(position.current_value) >= MIN_ACTIVE_POSITION_VALUE_USDC);
    }
    return position.status !== "CLOSED";
  });
  latestPositions = visiblePositions;
  if (!visiblePositions.length) {
    positionsBody.innerHTML = `
      <tr>
        <td colspan="11" class="empty-row">
          Aucune position active.
          <button type="button" data-empty-add-position>Ajouter une position manuelle</button>
          <button type="button" data-empty-sync-position>Synchroniser Binance</button>
        </td>
      </tr>
    `;
    return;
  }
  positionsBody.innerHTML = visiblePositions.map((position, index) => `
    <tr>
      <td><strong>${escapeHtml(position.symbol)}</strong></td>
      <td>${renderMarketSourceBadge(position.market_source ?? position.source)}</td>
      <td><span class="badge ${position.status === "ACTIVE" ? "good" : position.status === "DUST" ? "medium" : "neutral"}">${escapeHtml(position.status ?? "-")}</span><small>${financialStatusLabel(position)}</small></td>
      <td>${formatFinancial(position.decimal_values?.remaining_quantity ?? position.quantity, 6)}</td>
      <td>${formatFinancial(position.average_buy_price, 6, "Coût inconnu")}</td>
      <td class="${financialClass(position.gain_loss_pct)}"><strong>${formatFinancial(position.gain_loss_pct, 2)}${position.gain_loss_pct == null ? "" : "%"}</strong></td>
      <td>${formatNumber(position.take_profit_1, 6)}</td>
      <td>${formatNumber(position.take_profit_2, 6)}</td>
      <td>${formatNumber(position.stop_loss, 6)}</td>
      <td><strong>${escapeHtml(position.trend_score ?? "-")}</strong></td>
      <td class="row-actions">
        <button type="button" data-edit-position="${index}" class="compact-action">Éditer</button>
        <button type="button" data-journal-symbol="${escapeHtml(position.symbol)}" class="compact-action">Journal</button>
        <button type="button" data-delete-position="${escapeHtml(position.id)}" class="compact-action danger-action">Suppr.</button>
      </td>
    </tr>
  `).join("");
}

function renderWatchCandidates(candidates) {
  latestWatchCandidates = candidates;
  if (!candidates.length) {
    watchBody.innerHTML = `
      <tr>
        <td colspan="8" class="empty-row">
          Vous ne surveillez aucune crypto pour le moment.
          <button type="button" data-empty-add-watch>Ajouter une crypto</button>
        </td>
      </tr>
    `;
    return;
  }
  watchBody.innerHTML = candidates.map((candidate, index) => `
    <tr>
      <td><strong>${escapeHtml(candidate.symbol)}</strong></td>
      <td>${formatNumber(candidate.current_price, 6)}</td>
      <td>${formatNumber(candidate.target_buy_price, 6)}</td>
      <td>${formatNumber(candidate.invalidation_price, 6)}</td>
      <td><span class="badge ${candidate.priority === "HIGH" ? "bad" : candidate.priority === "LOW" ? "neutral" : "medium"}">${escapeHtml(candidate.priority ?? "-")}</span></td>
      <td>${escapeHtml(candidate.signal ?? "-")}</td>
      <td><strong>${escapeHtml(candidate.score ?? "-")}</strong></td>
      <td class="row-actions">
        <button type="button" data-view-market-symbol="${escapeHtml(candidate.symbol)}" class="compact-action">Analyser</button>
        <button type="button" data-edit-watch="${index}" class="compact-action">Éditer</button>
        <button type="button" data-journal-symbol="${escapeHtml(candidate.symbol)}" class="compact-action">Journal</button>
        <button type="button" data-delete-watch="${escapeHtml(candidate.id)}" class="danger-action compact-action">Retirer</button>
      </td>
    </tr>
  `).join("");
}

function pnlValue(position) {
  const value = position.source === "MANUAL"
    ? position.gain_loss_amount ?? position.unrealized_pnl
    : position.unrealized_pnl ?? position.gain_loss_amount;
  return financialNumber(value);
}

function positionCurrentValue(position) {
  const value = position.current_value;
  return financialNumber(value);
}

function renderPortfolioSummary(pnl, positions = latestPositions) {
  // The server includes closed trades, dust and missing-data flags.
  const portfolioValue = pnl.portfolio_value_usdc;
  const unrealizedPnl = pnl.unrealized_pnl;
  const realizedPnl = pnl.realized_pnl;
  const totalPnl = pnl.total_pnl;
  const activeCount = pnl.active_positions_count ?? positions.filter(isActiveMarketPosition).length;
  const manualPositions = positions.filter((position) => position.source === "MANUAL" && position.status !== "CLOSED" && (position.is_active ?? 1) && financialNumber(position.quantity) > 0);
  const manualValue = manualPositions.every((position) => financialNumber(position.current_value) !== null)
    ? manualPositions.reduce((sum, position) => sum + financialNumber(position.current_value), 0) : null;
  const overlap = manualPositions.some((manual) => positions.some((position) => position.source !== "MANUAL" && position.symbol === manual.symbol && position.status === "ACTIVE"));
  const manualLabel = manualPositions.length ? `Manuel déclaré : ${formatFinancial(manualValue, 2)} USDC${overlap ? " · périmètres à confirmer" : ""}` : "";
  const manualRecorded = financialNumber(pnl.manual_realized_pnl_recorded);
  const manualClosed = Number(pnl.manual_closed_positions_count ?? 0) > 0
    ? manualRecorded !== null ? `Manuel déclaré : ${formatFinancial(manualRecorded, 2)} USDC (non vérifié)`
      : Number(pnl.manual_recorded_results_count ?? 0) > 0 ? `Manuel, sous-total déclaré : ${formatFinancial(pnl.known_manual_realized_pnl_recorded, 2)} USDC (incomplet)`
      : "Manuel déclaré : Indisponible USDC (non vérifié)" : "";
  const reliability = pnl.pnl_status === "EXACT" ? "Calcul vérifié"
    : pnl.pnl_status === "ESTIMATED" ? "Estimation" : "Données incomplètes";
  const known = portfolioValue == null && pnl.known_portfolio_value_usdc != null && pnl.sync_status !== "NEVER"
    ? `Sous-total connu : ${formatFinancial(pnl.known_portfolio_value_usdc, 2)} USDC · ` : "";
  portfolioSummary.innerHTML = `
    <div class="portfolio-metric portfolio-main">
      <span>Valeur portefeuille Binance</span>
      <strong>${formatFinancial(portfolioValue, 2)} USDC</strong>
      <small>${known}${formatFinancial(pnl.portfolio_value_eur, 2)} € · ${formatNumber(activeCount, 0)} positions</small>
      ${manualLabel ? `<small>${manualLabel}</small>` : ""}
    </div>
    <div class="portfolio-metric" data-tooltip="Positions encore ouvertes, poussières comprises">
      <span>PnL latent</span>
      <strong class="${financialClass(unrealizedPnl)}">${formatFinancial(unrealizedPnl, 2)} USDC</strong>
      <small>${reliability}</small>
    </div>
    <div class="portfolio-metric" data-tooltip="Trades déjà clôturés et ventes partielles comprises">
      <span>PnL réalisé</span>
      <strong class="${financialClass(realizedPnl)}">${formatFinancial(realizedPnl, 2)} USDC</strong>
      <small>Trades déjà clôturés et ventes partielles</small>
      ${manualClosed ? `<small>${manualClosed}</small>` : ""}
    </div>
    <div class="portfolio-metric" data-tooltip="Résultat global seulement si les données sont complètes">
      <span>PnL total</span>
      <strong class="${financialClass(totalPnl)}">${formatFinancial(totalPnl, 2)} USDC</strong>
      <small>${reliability}</small>
    </div>
  `;
}

function renderBinanceStatus(status) {
  const states = { COMPLETE: "synchronisation complète", PARTIAL: "synchronisation partielle",
    ERROR: "synchronisation indisponible", BUSY: "synchronisation en cours", NEVER: "non synchronisé" };
  const text = status.configured
    ? `Binance : configuré · ${states[status.sync_status] || "état à vérifier"} · ${formatDateTime(status.last_sync)}`
    : "Binance : déconnecté (non configuré)";
  binanceStatusText.textContent = text;
  if (settingsBinanceStatus) settingsBinanceStatus.textContent = text;
}

function journalResultClass(status) {
  const normalized = String(status || "OPEN").toUpperCase();
  if (normalized === "WIN") return "pnl-positive";
  if (normalized === "LOSS") return "pnl-negative";
  if (normalized === "OPEN") return "status-open";
  return "pnl-neutral";
}

function journalLabel(value) {
  const labels = {
    BUY: "Achat",
    SELL: "Vente",
    HOLD: "Conserver",
    WAIT: "Attendre",
    REINFORCE: "Renforcer",
    CUT_LOSS: "Couper perte",
    SCALP: "Scalp",
    SWING: "Swing",
    LONG_TERM: "Long terme",
    TEST: "Test",
    FOMO: "FOMO",
    REVENGE: "Revanche",
    CONFIDENT: "Confiant",
    FEAR: "Peur",
    IMPATIENT: "Impatient",
    STRESSED: "Stressé",
    CALM: "Calme",
    GREED: "Avidité",
    OPEN: "Ouvert",
    WIN: "Win",
    LOSS: "Loss",
    BREAKEVEN: "Break-even",
    CANCELLED: "Annulé",
  };
  return labels[String(value || "").toUpperCase()] || value || "-";
}

function filteredJournalEntries() {
  const symbolFilter = journalFilterSymbol?.value?.trim().toUpperCase() || "";
  const resultFilter = journalFilterResult?.value || "";
  const emotionFilter = journalFilterEmotion?.value || "";
  return latestJournalEntries.filter((entry) => {
    const symbolMatch = !symbolFilter || String(entry.symbol || "").includes(symbolFilter);
    const resultMatch = !resultFilter || entry.result_status === resultFilter;
    const emotionMatch = !emotionFilter || entry.emotion === emotionFilter;
    return symbolMatch && resultMatch && emotionMatch;
  });
}

function renderJournalStats(analytics) {
  if (!journalStats) return;
  const winrate = analytics?.winrate || {};
  const emotions = analytics?.emotions || {};
  const setups = analytics?.setups || {};
  const aiAccuracy = analytics?.ai_accuracy || {};
  const riskyEmotion = emotions.riskiest_emotion?.emotion ? journalLabel(emotions.riskiest_emotion.emotion) : "-";
  journalStats.innerHTML = `
    <div class="journal-stat-card">
      <span>Winrate</span>
      <strong>${formatNumber(winrate.winrate, 2)}%</strong>
      <small>${escapeHtml(winrate.closed_trades ?? 0)} trades clôturés</small>
    </div>
    <div class="journal-stat-card">
      <span>Gain moyen</span>
      <strong class="pnl-positive">${formatNumber(winrate.avg_gain, 2)}%</strong>
      <small>Trades gagnants</small>
    </div>
    <div class="journal-stat-card">
      <span>Perte moyenne</span>
      <strong class="pnl-negative">${formatNumber(winrate.avg_loss, 2)}%</strong>
      <small>Trades perdants</small>
    </div>
    <div class="journal-stat-card">
      <span>Setup fort</span>
      <strong>${escapeHtml(setups.best_setup?.setup || "-")}</strong>
      <small>IA accuracy ${formatNumber(aiAccuracy.accuracy, 2)}%</small>
    </div>
    <div class="journal-stat-card">
      <span>Émotion risquée</span>
      <strong>${escapeHtml(riskyEmotion)}</strong>
      <small>${formatNumber(emotions.riskiest_emotion?.winrate, 2)}% winrate</small>
    </div>
  `;
}

function journalSnapshotHtml(entry) {
  const snapshot = entry.ai_snapshot || {};
  const market = snapshot.market || {};
  const decision = snapshot.decision || {};
  const regime = snapshot.market_regime || {};
  return `
    <div class="journal-snapshot">
      <div><span>Score IA</span><strong>${formatNumber(entry.ai_score, 0)}</strong></div>
      <div><span>Trend</span><strong>${escapeHtml(entry.ai_trend || market.global_trend || "-")}</strong></div>
      <div><span>Setup</span><strong>${escapeHtml(entry.ai_setup_quality || market.setup_quality || "-")}</strong></div>
      <div><span>Timing</span><strong>${escapeHtml(entry.ai_trigger || triggerDecisionLabel(decision))}</strong></div>
      <div><span>Régime</span><strong>${escapeHtml(entry.ai_market_regime || regime.regime || "-")}</strong></div>
      <div><span>Prix</span><strong>${formatNumber(market.current_price, 6)}</strong></div>
    </div>
    <p class="journal-replay">${escapeHtml(decisionSummaryLabel(decision.reason_summary) || market.market_error || "Snapshot IA sauvegardé au moment de l'entrée.")}</p>
  `;
}

function renderJournal(entries, analytics = latestJournalAnalytics) {
  latestJournalEntries = entries || [];
  latestJournalAnalytics = analytics;
  renderJournalStats(analytics);
  const visibleEntries = filteredJournalEntries();

  if (!visibleEntries.length) {
    journalList.innerHTML = `
      <div class="empty-panel">
        <strong>Aucune décision enregistrée.</strong>
        <span>Ajoute une décision pour commencer à mesurer tes setups, émotions et résultats.</span>
        <button type="button" data-empty-add-journal>Ajouter une entrée</button>
      </div>
    `;
    return;
  }
  journalList.innerHTML = visibleEntries.map((entry) => {
    const pnlClass = Number(entry.pnl_percent) > 0 ? "pnl-positive" : Number(entry.pnl_percent) < 0 ? "pnl-negative" : "pnl-neutral";
    return `
    <div class="journal-entry" data-journal-entry="${escapeHtml(entry.id)}">
      <div class="journal-row-main">
        <div class="journal-symbol">
          <strong>${escapeHtml(entry.symbol)}</strong>
          <small>${formatDateTime(entry.created_at)}</small>
        </div>
        <span class="badge neutral">${escapeHtml(journalLabel(entry.decision_type))}</span>
        <span class="badge medium">${escapeHtml(journalLabel(entry.emotion))}</span>
        <span>${formatNumber(entry.confidence, 0)}%</span>
        <span>${formatNumber(entry.ai_score, 0)}</span>
        <strong class="${journalResultClass(entry.result_status)}">${escapeHtml(journalLabel(entry.result_status))}</strong>
        <strong class="${pnlClass}">${formatNumber(entry.pnl_percent, 2)}%</strong>
        <div class="journal-actions">
          <button type="button" data-toggle-journal="${escapeHtml(entry.id)}">Détail</button>
          <button type="button" data-close-journal="${escapeHtml(entry.id)}" data-result="WIN">Win</button>
          <button type="button" data-close-journal="${escapeHtml(entry.id)}" data-result="LOSS">Loss</button>
          <button type="button" data-delete-journal="${escapeHtml(entry.id)}">Supprimer</button>
        </div>
      </div>
      <div class="journal-row-note">${escapeHtml(entry.reason || entry.notes || "")}</div>
      <div class="journal-entry-detail" id="journalDetail${escapeHtml(entry.id)}" hidden>
        <div class="journal-detail-grid">
          <div>
            <h4>Replay IA</h4>
            ${journalSnapshotHtml(entry)}
          </div>
          <div>
            <h4>Résultat utilisateur</h4>
            <div class="kv-grid compact">
              <span>Type</span><strong>${escapeHtml(journalLabel(entry.trade_type))}</strong>
              <span>Source</span><strong>${marketSourceLabel(entry.market_source)}</strong>
              <span>PnL USDC</span><strong class="${pnlClass}">${formatNumber(entry.pnl_usdc, 2)}</strong>
              <span>Durée</span><strong>${formatNumber(entry.duration_minutes, 0)} min</strong>
            </div>
            <p>${escapeHtml(entry.notes || "Aucune note utilisateur.")}</p>
          </div>
        </div>
      </div>
    </div>
  `;
  }).join("");
}

// --- CHARGEMENT DES FLUX DATA ---

async function loadMarket(force = false) {
  const mode = modeSelect.value;
  const symbol = symbolInput?.value?.trim().toUpperCase() || "BTCUSDC";

  if (mode === "symbol") {
    const payload = await apiFetch(`/api/analyze-multi/${encodeURIComponent(String(symbol))}`);
    renderRows(await enrichWithHistory([payload]));
    return;
  }

  const payload = await apiFetch(`/api/market?force=${force ? "true" : "false"}`);
  const rows = await enrichWithHistory(payload.results ?? []);
  renderRows(rows);
  dashboardState.lastRefresh = payload.updated_at ? new Date(payload.updated_at) : new Date();
  const cacheLabel = payload.source === "cache" ? "cache" : "live";
  statusText.textContent = `Marché : ${payload.counts?.total ?? rows.length} cryptos (${cacheLabel})`;
  if (payload.is_stale) {
    statusText.textContent += " - données anciennes";
  }
}

async function loadPersonalData() {
  const [positions, candidates, journal, journalAnalytics, pnl, binanceStatus] = await Promise.all([
    apiFetch("/api/positions"),
    apiFetch("/api/watch-candidates"),
    apiFetch("/api/journal"),
    apiFetch("/api/journal/analytics"),
    apiFetch("/api/binance/pnl"),
    apiFetch("/api/binance/status"),
  ]);
  renderPositions(positions);
  renderWatchCandidates(candidates);
  renderJournal(journal, journalAnalytics);
  renderPortfolioSummary(pnl, positions);
  renderBinanceStatus(binanceStatus);
}

async function loadV5Data() {
  renderAiAlerts(await apiFetch("/api/ai-alerts"));
}

async function refreshAll(force = false) {
  if (isRefreshing) return;
  isRefreshing = true;
  dashboardState.isRefreshing = true;
  setButtonLoading(refreshButton, true, "Rafraichissement...");
  setButtonLoading(mobileRefreshButton, true, "...");
  statusText.textContent = "Mise à jour en cours...";
  if (mobileStatusText) mobileStatusText.textContent = "Maj...";

  try {
    await loadPersonalData();
    await loadMarket(force);
    await loadV5Data();
    nextRefreshAt = new Date(Date.now() + AUTO_REFRESH_MS);
    nextRefreshText.textContent = `Prochain refresh : ${formatDateTime(nextRefreshAt.toISOString())}`;
    statusText.textContent = `Dernier refresh : ${formatDateTime(new Date().toISOString())}`;
  } catch (error) {
    statusText.textContent = error.message;
    if (mobileStatusText) mobileStatusText.textContent = "Erreur";
  } finally {
    isRefreshing = false;
    dashboardState.isRefreshing = false;
    setButtonLoading(refreshButton, false);
    setButtonLoading(mobileRefreshButton, false);
    if (mobileStatusText && mobileStatusText.textContent !== "Erreur") {
      mobileStatusText.textContent = "Pret";
    }
  }
}

function scheduleAutoRefresh() {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  nextRefreshAt = new Date(Date.now() + AUTO_REFRESH_MS);
  nextRefreshText.textContent = `Prochain refresh : ${formatDateTime(nextRefreshAt.toISOString())}`;
  autoRefreshTimer = setInterval(() => {
    if (!dashboardState.isRefreshing && !dashboardState.isSyncingBinance) refreshAll(true);
  }, AUTO_REFRESH_MS);
}

async function createQuickJournal(symbol, decisionType = "WAIT") {
  await apiFetch("/api/journal", {
    method: "POST",
    body: JSON.stringify({
      symbol,
      decision_type: decisionType,
      trade_type: "TEST",
      emotion: "CALM",
      confidence: 50,
      reason: "Ajout rapide depuis le dashboard",
    }),
  });
  const [journal, analytics] = await Promise.all([
    apiFetch("/api/journal"),
    apiFetch("/api/journal/analytics"),
  ]);
  renderJournal(journal, analytics);
}

async function refreshMarketCockpit() {
  await loadPersonalData();
  await loadMarket(true);
  await loadV5Data();
}

function stripWatchCandidateFromRow(row, candidateId) {
  if (String(row.watch_info?.id) !== String(candidateId)) return row;
  const sources = (row.sources ?? []).filter((source) => source !== "WATCH_CANDIDATE");
  const badges = (row.badges ?? []).filter((badge) => badge !== "Surveillance");
  const updated = {
    ...row,
    sources,
    badges,
    watch_info: null,
  };
  const businessSources = ["POSITION", "WATCH_CANDIDATE", "OPPORTUNITY_SCANNER"];
  const stillVisible = updated.position_info || updated.opportunity_info || sources.some((source) => businessSources.includes(source));
  return stillVisible ? updated : null;
}

function removeWatchCandidateLocally(candidateId) {
  latestWatchCandidates = latestWatchCandidates.filter((candidate) => String(candidate.id) !== String(candidateId));
  renderWatchCandidates(latestWatchCandidates);

  const nextRows = latestRows
    .map((row) => stripWatchCandidateFromRow(row, candidateId))
    .filter(Boolean);
  renderRows(nextRows);
}

async function removeWatchFromMarket(candidateId, setFeedback = setMarketFeedback) {
  const confirmed = window.confirm("Retirer cette crypto de la surveillance ?");
  if (!confirmed) return;
  setFeedback("Retrait de la surveillance...", "loading");
  try {
    await apiFetch(`/api/watch-candidates/${encodeURIComponent(String(candidateId))}`, { method: "DELETE" });
  } catch (error) {
    setFeedback(`Impossible de retirer la surveillance : ${error.message}`, "error");
    return;
  }
  removeWatchCandidateLocally(candidateId);
  setFeedback("Surveillance supprimee.", "success");
  try {
    await refreshMarketCockpit();
  } catch (error) {
    setFeedback(`Surveillance supprimee. Actualisation du cockpit impossible : ${error.message}`, "error");
  }
}

async function deleteManualPositionFromMarket(positionId) {
  const confirmed = window.confirm("Supprimer cette position manuelle ?");
  if (!confirmed) return;
  setMarketFeedback("Suppression de la position...", "loading");
  try {
    await apiFetch(`/api/positions/${encodeURIComponent(String(positionId))}`, { method: "DELETE" });
    setMarketFeedback("Position manuelle supprimée.", "success");
    await refreshMarketCockpit();
  } catch (error) {
    setMarketFeedback(`Impossible de supprimer la position : ${error.message}`, "error");
  }
}

// --- ÉCOUTEURS D'ÉVÉNEMENTS (GESTION ACTION-MENUS, MODALS ET FORMULAIRES) ---

refreshButton.addEventListener("click", () => scheduleRefresh(true));
mobileRefreshButton?.addEventListener("click", () => scheduleRefresh(true));
marketNavButton?.addEventListener("click", () => scrollToSection(".workspace"));
alertsNavButton?.addEventListener("click", () => scrollToSection("#alertsContainer"));
settingsSyncButton?.addEventListener("click", () => binanceSyncButton?.click());
syncPositionsButton?.addEventListener("click", () => binanceSyncButton?.click());
addPositionButton?.addEventListener("click", () => focusForm(positionForm));
addWatchButton?.addEventListener("click", () => focusForm(watchForm));
addJournalButton?.addEventListener("click", () => focusForm(journalForm));
quickWatchForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = quickWatchForm.querySelector("button[type='submit']");
  submitButton.disabled = true;
  setMarketFeedback("Ajout à la surveillance...", "loading");
  try {
    const payload = formToPayload(quickWatchForm);
    const candidate = await apiFetch("/api/watch-candidates", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    quickWatchForm.reset();
    const sourceLabel = marketSourceLabel(candidate.market_source);
    setMarketFeedback(`${candidate.symbol} ajoutée en surveillance ${sourceLabel}.`, "success");
    await refreshMarketCockpit();
  } catch (error) {
    const invalidSymbol = error.message.toLowerCase().includes("symbole invalide")
      || error.message.toLowerCase().includes("invalid symbol");
    setMarketFeedback(
      invalidSymbol
        ? "Symbole invalide. Exemple attendu : BTCUSDC ou BTC."
        : `Impossible d'ajouter la surveillance : ${error.message}`,
      "error",
    );
  } finally {
    submitButton.disabled = false;
  }
});
modeSelect.addEventListener("change", () => scheduleRefresh(false));
symbolInput?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") scheduleRefresh(false);
});

mobileMenuButton?.addEventListener("click", openMobileMenu);
mobileMenuClose?.addEventListener("click", closeMobileMenu);
mobileMenuOverlay?.addEventListener("click", closeMobileMenu);

mobileMarketButton?.addEventListener("click", () => {
  closeMobileMenu();
  scrollToSection(".workspace");
});
mobileJournalButton?.addEventListener("click", () => {
  closeMobileMenu();
  openModal(modalButtons.journalModalButton);
});
mobileAlertsButton?.addEventListener("click", () => {
  closeMobileMenu();
  document.querySelector("#alertsContainer")?.scrollIntoView({ behavior: "smooth", block: "start" });
});
mobileSettingsButton?.addEventListener("click", () => {
  closeMobileMenu();
  openModal(modalButtons.settingsModalButton);
});

function closeActionMenus() {
  document.querySelectorAll(".action-menu.open").forEach((menu) => {
    menu.classList.remove("open");
    menu.querySelector(".action-menu-trigger")?.setAttribute("aria-expanded", "false");
  });
}

function openActionMenu(menu) {
  closeActionMenus();
  menu.classList.add("open");
  menu.querySelector(".action-menu-trigger")?.setAttribute("aria-expanded", "true");
}

document.querySelectorAll(".action-menu-trigger").forEach((button) => {
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    const menu = button.closest(".action-menu");
    if (menu.classList.contains("open")) {
      closeActionMenus();
    } else {
      openActionMenu(menu);
    }
  });
  button.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      const menu = button.closest(".action-menu");
      openActionMenu(menu);
      menu.querySelector(".action-menu-list button")?.focus();
    }
  });
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".action-menu")) closeActionMenus();

  const tooltipTarget = event.target.closest("[data-tooltip]");
  if (currentDevice !== "desktop") {
    document.querySelectorAll("[data-tooltip].tooltip-open").forEach((item) => {
      if (item !== tooltipTarget) item.classList.remove("tooltip-open");
    });
    if (tooltipTarget) {
      event.preventDefault();
      tooltipTarget.classList.toggle("tooltip-open");
    }
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeActionMenus();
    closeMobileMenu();
  }
});

document.querySelectorAll(".action-menu-list").forEach((menuList) => {
  menuList.addEventListener("click", (event) => {
    event.stopPropagation();
    closeActionMenus();
  });
  menuList.addEventListener("keydown", (event) => {
    const buttons = [...menuList.querySelectorAll("button")];
    const currentIndex = buttons.indexOf(document.activeElement);
    if (event.key === "ArrowDown") {
      event.preventDefault();
      buttons[(currentIndex + 1) % buttons.length]?.focus();
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      buttons[(currentIndex - 1 + buttons.length) % buttons.length]?.focus();
    }
  });
});

resultsBody.addEventListener("click", (event) => {
  const launchScanButton = event.target.closest("[data-launch-scan]");
  if (launchScanButton) {
    event.stopPropagation();
    scheduleRefresh(true);
    return;
  }
  const removeWatchButton = event.target.closest("[data-market-remove-watch]");
  if (removeWatchButton) {
    event.stopPropagation();
    removeWatchFromMarket(removeWatchButton.dataset.marketRemoveWatch);
    return;
  }
  const deletePositionButton = event.target.closest("[data-market-delete-position]");
  if (deletePositionButton) {
    event.stopPropagation();
    deleteManualPositionFromMarket(deletePositionButton.dataset.marketDeletePosition);
    return;
  }
  const row = event.target.closest("tr[data-row-index]");
  if (!row) return;
  renderDetail(latestRows[Number(row.dataset.rowIndex)]);
});

document.addEventListener("click", (event) => {
  const launchScanButton = event.target.closest("[data-launch-scan]");
  if (launchScanButton) {
    event.preventDefault();
    scheduleRefresh(true);
    return;
  }
  const mobileToggle = event.target.closest("[data-mobile-toggle]");
  const removeWatchButton = event.target.closest("[data-market-remove-watch]");
  if (removeWatchButton) {
    removeWatchFromMarket(removeWatchButton.dataset.marketRemoveWatch);
    return;
  }
  const deletePositionButton = event.target.closest("[data-market-delete-position]");
  if (deletePositionButton) {
    deleteManualPositionFromMarket(deletePositionButton.dataset.marketDeletePosition);
    return;
  }
  if (mobileToggle) {
    const symbol = mobileToggle.dataset.mobileToggle;
    if (openMobileCards.has(symbol)) {
      openMobileCards.delete(symbol);
    } else {
      openMobileCards.add(symbol);
    }
    renderMobileCards(latestRows);
    return;
  }

  const mobileDetail = event.target.closest("[data-mobile-detail]");
  if (mobileDetail) {
    renderDetail(latestRows[Number(mobileDetail.dataset.mobileDetail)]);
    return;
  }

  const button = event.target.closest("[data-decision-symbol]");
  if (!button) return;
  const item = latestRows.find((row) => row.symbol === button.dataset.decisionSymbol);
  if (item) renderDetail(item);
});

closeDetailButton.addEventListener("click", closeDetailPanel);

Object.entries(modalButtons).forEach(([buttonId, modal]) => {
  document.querySelector(`#${buttonId}`)?.addEventListener("click", () => openModal(modal));
});

document.querySelectorAll("[data-close-modal]").forEach((button) => {
  button.addEventListener("click", () => closeModal(button.closest(".modal")));
});

document.querySelectorAll(".modal").forEach((modal) => {
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal(modal);
  });
});

binanceSyncButton.addEventListener("click", async () => {
  if (isSyncingBinance) return;
  isSyncingBinance = true;
  dashboardState.isSyncingBinance = true;
  setButtonLoading(binanceSyncButton, true, "Synchronisation...");
  setButtonLoading(settingsSyncButton, true, "Synchronisation...");
  binanceStatusText.textContent = "Synchronisation Binance en cours...";
  try {
    const result = await apiFetch("/api/binance/sync", { method: "POST" });
    dashboardState.lastBinanceSync = result.last_sync ? new Date(result.last_sync) : new Date();
    binanceStatusText.textContent = result.synced
      ? `Synchronisation Binance OK · ${formatDateTime(result.last_sync)}`
      : result.message;
    await loadPersonalData();
    await loadMarket(true);
    await loadV5Data();
  } catch (error) {
    binanceStatusText.textContent = error.message;
  } finally {
    isSyncingBinance = false;
    dashboardState.isSyncingBinance = false;
    setButtonLoading(binanceSyncButton, false);
    setButtonLoading(settingsSyncButton, false);
  }
});

document.querySelector("#adminResetForm")?.addEventListener("submit", (event) => {
  if (!window.confirm("Supprimer les donnees locales ? Cette operation est irreversible.")) {
    event.preventDefault();
  }
});

detailContent.addEventListener("click", async (event) => {
  const watchButton = event.target.closest("[data-add-watch-symbol]");
  if (watchButton) {
    openModal(modalButtons.watchModalButton);
    resetForm(watchForm);
    watchForm.elements.symbol.value = watchButton.dataset.addWatchSymbol;
    watchForm.elements.reason.value = "Ajout depuis le détail marché";
    focusForm(watchForm, false);
    return;
  }

  const positionButton = event.target.closest("[data-add-position-symbol]");
  if (positionButton) {
    openModal(modalButtons.positionsModalButton);
    resetForm(positionForm);
    positionForm.elements.symbol.value = positionButton.dataset.addPositionSymbol;
    focusForm(positionForm, false);
    return;
  }

  const button = event.target.closest("[data-symbol]");
  if (!button) return;
  await createQuickJournal(button.dataset.symbol);
});

positionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = formToPayload(positionForm);
  const id = positionForm.elements.id.value;
  await apiFetch(id ? `/api/positions/${encodeURIComponent(String(id))}` : "/api/positions", {
    method: id ? "PUT" : "POST",
    body: JSON.stringify(payload),
  });
  resetForm(positionForm);
  await loadPersonalData();
});

positionsBody.addEventListener("click", async (event) => {
  const editButton = event.target.closest("[data-edit-position]");
  const deleteButton = event.target.closest("[data-delete-position]");
  const journalButton = event.target.closest("[data-journal-symbol]");
  const detailButton = event.target.closest("[data-view-market-symbol]");
  const emptyAddButton = event.target.closest("[data-empty-add-position]");
  const emptySyncButton = event.target.closest("[data-empty-sync-position]");

  if (emptyAddButton) focusForm(positionForm);
  if (emptySyncButton) binanceSyncButton?.click();
  if (detailButton) {
    const item = latestRows.find((row) => row.symbol === detailButton.dataset.viewMarketSymbol);
    if (item) renderDetail(item);
  }
  if (editButton) fillForm(positionForm, latestPositions[Number(editButton.dataset.editPosition)]);
  if (deleteButton) {
    const confirmed = window.confirm("Supprimer cette position manuelle ?");
    if (!confirmed) return;
    await apiFetch(`/api/positions/${encodeURIComponent(String(deleteButton.dataset.deletePosition))}`, { method: "DELETE" });
    await loadPersonalData();
  }
  if (journalButton) await createQuickJournal(journalButton.dataset.journalSymbol);
});

watchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = formToPayload(watchForm);
  const id = watchForm.elements.id.value;
  const submitButton = watchForm.querySelector("button[type='submit']");
  submitButton.disabled = true;
  setWatchFeedback(id ? "Modification en cours..." : "Ajout en cours...", "loading");
  try {
    await apiFetch(id ? `/api/watch-candidates/${encodeURIComponent(String(id))}` : "/api/watch-candidates", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    resetForm(watchForm);
    setWatchFeedback(id ? "Crypto mise à jour." : "Crypto ajoutée.", "success");
    await loadPersonalData();
    await loadMarket(false);
    await loadV5Data();
  } catch (error) {
    const invalidSymbol = error.message.toLowerCase().includes("symbole invalide")
      || error.message.toLowerCase().includes("invalid symbol");
    setWatchFeedback(
      invalidSymbol
        ? "Symbole invalide. Exemple attendu : BTCUSDC ou BTC."
        : `Impossible d'ajouter la crypto : ${error.message}`,
      "error",
    );
  } finally {
    submitButton.disabled = false;
  }
});

watchBody.addEventListener("click", async (event) => {
  const editButton = event.target.closest("[data-edit-watch]");
  const deleteButton = event.target.closest("[data-delete-watch]");
  const journalButton = event.target.closest("[data-journal-symbol]");
  const detailButton = event.target.closest("[data-view-market-symbol]");
  const emptyAddButton = event.target.closest("[data-empty-add-watch]");

  if (emptyAddButton) focusForm(watchForm);
  if (editButton) {
    fillForm(watchForm, latestWatchCandidates[Number(editButton.dataset.editWatch)]);
    setWatchFeedback("Modification prête.", "neutral");
  }
  if (detailButton) {
    const item = latestRows.find((row) => row.symbol === detailButton.dataset.viewMarketSymbol);
    if (item) renderDetail(item);
  }
  if (deleteButton) {
    await removeWatchFromMarket(deleteButton.dataset.deleteWatch, setWatchFeedback);
  }
  if (journalButton) await createQuickJournal(journalButton.dataset.journalSymbol);
});

journalForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await apiFetch("/api/journal", {
    method: "POST",
    body: JSON.stringify(formToPayload(journalForm)),
  });
  resetForm(journalForm);
  const [journal, analytics] = await Promise.all([
    apiFetch("/api/journal"),
    apiFetch("/api/journal/analytics"),
  ]);
  renderJournal(journal, analytics);
});

journalList.addEventListener("click", async (event) => {
  const emptyAddButton = event.target.closest("[data-empty-add-journal]");
  if (emptyAddButton) {
    focusForm(journalForm);
    return;
  }
  const toggleButton = event.target.closest("[data-toggle-journal]");
  if (toggleButton) {
    const detail = document.getElementById(`journalDetail${toggleButton.dataset.toggleJournal}`);
    if (detail) detail.hidden = !detail.hidden;
    return;
  }
  const closeButton = event.target.closest("[data-close-journal]");
  if (closeButton) {
    await apiFetch(`/api/journal/${encodeURIComponent(String(closeButton.dataset.closeJournal))}`, {
      method: "PUT",
      body: JSON.stringify({ result_status: closeButton.dataset.result }),
    });
    const [journal, analytics] = await Promise.all([
      apiFetch("/api/journal"),
      apiFetch("/api/journal/analytics"),
    ]);
    renderJournal(journal, analytics);
    return;
  }
  const deleteButton = event.target.closest("[data-delete-journal]");
  if (!deleteButton) return;
  const confirmed = window.confirm("Supprimer cette entrée de journal ?");
  if (!confirmed) return;
  await apiFetch(`/api/journal/${encodeURIComponent(String(deleteButton.dataset.deleteJournal))}`, { method: "DELETE" });
  const [journal, analytics] = await Promise.all([
    apiFetch("/api/journal"),
    apiFetch("/api/journal/analytics"),
  ]);
  renderJournal(journal, analytics);
});

[journalFilterSymbol, journalFilterResult, journalFilterEmotion].forEach((filter) => {
  filter?.addEventListener("input", () => renderJournal(latestJournalEntries, latestJournalAnalytics));
  filter?.addEventListener("change", () => renderJournal(latestJournalEntries, latestJournalAnalytics));
});

// --- ENCLENCHEMENT DE L'APPLICATION ---
async function initDashboard() {
  scheduleSessionExpiry();
  if (sessionExpired) return;
  applyDeviceMode();
  statusText.textContent = "Chargement du marché...";
  if (mobileStatusText) mobileStatusText.textContent = "Chargement...";
  await refreshAll(false);
  scheduleAutoRefresh();
}

window.addEventListener("resize", debounce(() => {
  const previousDevice = currentDevice;
  applyDeviceMode();
  if (previousDevice !== currentDevice) {
    renderRows(latestRows);
  }
}, 200));
initDashboard();

// Détection automatique du mode selon le contenu du champ Symbole
symbolInput?.addEventListener("input", () => {
  if (symbolInput.value.trim() === "") {
    modeSelect.value = "scan";
  } else {
    modeSelect.value = "symbol";
  }
});


window.addEventListener("pageshow", (event) => {
  if (event.persisted) window.location.reload();
});
