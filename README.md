# Crypto AI Assistant V5

Assistant personnel d'analyse crypto basé sur Binance public et, optionnellement, sur ton compte Binance en lecture seule.

V5 transforme V4 en assistant decisionnel oriente attention utilisateur. Le cockpit Marche fusionne portefeuille, surveillance, watchlist et candidats qualifies, puis remonte seulement les evenements importants dans une section unique `Alertes IA`.

V4 a introduit le moteur decisionnel explicite. V5 ajoute les Alertes IA, le scanner interne d'opportunites, l'historique des scores, les triggers avances, le regime de marche, la correlation BTC, le support Binance Alpha et le backtesting de signaux.

## Ce que le projet ne fait pas

- Pas de trading automatique.
- Pas d'achat ou de vente automatique.
- Pas d'ordre Binance.
- Pas de permissions trading.
- Pas de permissions withdrawal/retrait.
- Pas de machine learning.
- Pas d'IA prédictive.
- Pas de Telegram, Discord ou WebSocket.

## Avertissement

Ce projet est uniquement une aide a la decision.
Il ne constitue pas un conseil financier.
Il n'execute aucun ordre automatiquement.

## Installation

```bash
python3.13 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration Binance lecture seule

```bash
cp .env.example .env
```

Variables attendues :

```text
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_USE_TESTNET=false
MIN_ACTIVE_POSITION_VALUE_USDC=1.00
```

La clé API doit être en lecture seule uniquement. Ne jamais activer les permissions trading ou retrait. Le fichier `.env` est ignoré par Git et ne doit jamais être partagé.

Si aucune clé n'est configurée, le dashboard et le scan marché continuent de fonctionner. Le statut affichera : `API Binance privée non configurée`.

## Lancement

```bash
uvicorn main:app --reload
```

Dashboard :

```text
http://127.0.0.1:8000/
```

Tests :

```bash
pytest
```

## Architecture V5

Nouveaux modules :

- `services/alerts_engine.py` : consolidation des alertes opportunite, surveillance, risque et marche.
- `services/opportunity_scanner.py` : scan leger de paires Binance USDC, filtrage liquidite/stables/levier, score d'opportunite interne.
- `services/market_universe.py` : fusion positions, surveillance, opportunites scanner et watchlist pour construire le cockpit Marche.
- `services/binance_alpha.py` : support Alpha operationnel pour token list, ticker, candles, trend simple, volume, prix et PnL latent.
- `services/score_history.py` : snapshots SQLite des scores decisionnels et acceleration.
- `services/advanced_triggers.py` : squeeze, fake breakout, rejet resistance/support, divergences, absorption, acceleration, exhaustion candle, reclaim.
- `services/btc_correlation.py` : force relative altcoin vs BTC et ajustement de contexte.
- `services/market_regime.py` : classification `RISK_ON`, `RISK_OFF`, `NEUTRAL`, `PANIC`, `SPECULATIVE`.
- `services/backtesting.py` : simulation de signaux, winrate, gain/perte moyen, profit factor, expectancy, drawdown.

Tables SQLite V5 :

- `score_snapshots`
- `backtest_results`
- `market_cache`

## Marche cockpit principal

La vue `Marche` est la vue principale du dashboard. Elle fusionne automatiquement les positions actives Binance, les positions manuelles actives, les cryptos en surveillance, les opportunites automatiques qualifiees et la watchlist principale.

Une crypto n'apparait qu'une seule fois. Les badges indiquent l'origine de la ligne : `Détenue`, `Surveillance`, `Opportunité`, `Watchlist`, puis la source de marche `Spot`, `Alpha`, `Manuel` ou `Inconnu`.

La page Marche permet d'ajouter rapidement une crypto a surveiller sans ouvrir de modal. Les vues Positions et Watchlist restent disponibles comme formulaires de gestion avances depuis les actions contextuelles, mais le cockpit affiche l'essentiel directement.

Au chargement de la page, le dashboard appelle automatiquement `/api/market`. Si un cache marche existe en SQLite, il est retourne immediatement avec `source="cache"` et `updated_at`. Si aucun cache n'existe, le backend reconstruit le cockpit puis persiste le resultat. Le bouton `Rafraichir` force une reconstruction live.

Les actions de suppression sont contextuelles : une position manuelle affiche `Supprimer position`, une crypto en surveillance affiche `Retirer surveillance`, et une position Binance est seulement marquee comme geree par Binance. Une ligne qui possede plusieurs sources conserve les sources restantes apres suppression.

Le resume portefeuille separe `Valeur portefeuille`, `PnL latent`, `PnL realise` et `PnL total`. Le latent correspond aux positions ouvertes, le realise aux trades clotures, et le total additionne les deux.

## Alertes IA

La section `Alertes IA` remplace les anciens widgets separes Opportunites, Risques, Scanner et Heatmap. Elle affiche peu d'elements, mais uniquement ceux qui meritent l'attention.

Types d'alertes :

- `OPPORTUNITY` : breakout potentiel, momentum en amelioration, support defendu, reprise de volume.
- `WATCH` : setup en construction, trigger proche, approche support/resistance, compression.
- `RISK` : cassure support, deterioration rapide, pression vendeuse, drawdown important.
- `MARKET` : regime global defensif, BTC sous pression, retour de momentum ou reprise du volume.

Les alertes sont triees par priorite produit : risques, opportunites, surveillance, puis contexte marche. Les alertes faibles ou redondantes sont filtrees pour limiter le bruit.

## Scanner opportunites interne

Le scanner V5 reste utilise en interne par le cockpit Marche et les Alertes IA :

1. Scan leger sur un univers USDC limite, base sur volume 24h, variation, volatilite, breakout potentiel et force relative.
2. Analyse complete uniquement sur la watchlist principale et les meilleurs candidats.
3. Filtrage qualitatif par le moteur decisionnel avant affichage comme candidat.

Une opportunite automatique signifie : "cela merite mon attention". Elle ne signifie pas : "cela va monter".

Pour rester visible dans Marche comme opportunite, une crypto doit rester constructive apres analyse : `BUY_READY`, `BUY_WATCH` ou `WAIT` constructif, avec scores contexte/setup/marche minimaux et sans risque catastrophique.

## Regime de marche

Le regime de marche resume le contexte global :

- `RISK_ON` : marche favorable aux setups confirmes.
- `RISK_OFF` : marche defensif.
- `NEUTRAL` : signaux mixtes.
- `PANIC` : pression forte, priorite a la protection.
- `SPECULATIVE` : marche actif mais selectif.

Le regime de marche est integre aux Alertes IA sous forme d'alerte `MARKET` quand le contexte global merite l'attention.

## Evolution des scores

Chaque scan sauvegarde un snapshot decisionnel :

- score global ;
- context/setup/trigger/risk/market ;
- confiance ;
- decision ;
- horodatage.

`calculate_score_acceleration()` indique si le score est `IMPROVING`, `DEGRADING` ou `FLAT`, avec acceleration `STRONG`, `MODERATE`, `WEAK`, `NEGATIVE` ou `NONE`.

## Backtesting V5

Le backtesting mesure la qualite historique des signaux :

- `BUY_READY`
- `BUY_WATCH`
- `TAKE_PROFIT`
- `CUT_LOSS`

Metriques calculees :

- winrate ;
- average gain ;
- average loss ;
- profit factor ;
- expectancy ;
- max drawdown.

Ces resultats servent a evaluer le moteur. Ils ne constituent pas une garantie de performance future.

## Moteur decisionnel V4

Le moteur V4 retourne une decision structuree par symbole :

```json
{
  "symbol": "BTCUSDC",
  "decision": "WAIT",
  "decision_label": "Attendre",
  "confidence": 62,
  "context_score": 48,
  "setup_score": 55,
  "trigger_score": 20,
  "risk_score": 70,
  "market_score": 45,
  "reason_summary": "Contexte fragile, risk/reward correct mais aucun trigger d'entree confirme.",
  "blocking_factors": ["Trigger absent", "Volume insuffisant"],
  "positive_factors": ["Support proche", "Risk/reward favorable"]
}
```

Decisions possibles :

- `BUY_WATCH` : surveiller achat.
- `BUY_READY` : achat potentiel, uniquement si le trigger est confirme.
- `WAIT` : attendre.
- `AVOID` : eviter.
- `SELL_WATCH` : surveiller vente sur une position detenue.
- `TAKE_PROFIT` : prise de benefice possible.
- `CUT_LOSS` : reduction du risque possible.

Difference essentielle :

- Un setup est une zone potentiellement interessante : support, resistance, risk/reward, consolidation, structure de marche.
- Un trigger est une confirmation de timing : breakout, cassure MA25, cassure resistance, volume, pression acheteuse, rebond support.

Regle fondamentale : `BUY_READY` est impossible si `trigger_score < 60`, meme avec un excellent risk/reward.

Scores V4 :

- `context_score` : trend global, alignement des moyennes mobiles, prix vs MA25/MA99, momentum multi-timeframe, volume.
- `setup_score` : support/resistance, risk/reward, proximite support, distance resistance, consolidation et structure.
- `trigger_score` : confirmation d'entree par breakout, MA25, resistance, volume, cloture verte forte ou rebond support.
- `risk_score` : distance stop-loss, ratio risk/reward, resistance proche, volatilite recente, meche haute et faux breakout possible.
- `market_score` : tendance BTC, momentum BTC, proportion de cryptos bullish/bearish dans la watchlist.
- `confidence` : coherence des signaux entre eux. Ce n'est pas une probabilite de gain.

Labels de confiance :

- `0-39` : faible.
- `40-69` : moyenne.
- `70-100` : forte.

## Fonctionnalites V5

- Scan marché multi-timeframe : `15m`, `1h`, `4h`, `1d`.
- Support, résistance, risk/reward, momentum, volume acceleration et patterns.
- Moteur decisionnel V4 avec facteurs positifs et facteurs bloquants.
- Alertes IA unifiees : opportunite, surveillance, risque et contexte marche.
- Opportunites qualifiees dans le cockpit : `BUY_READY`, `BUY_WATCH`, ou `WAIT` constructif.
- Support Binance Alpha pour les tokens non disponibles sur Spot avec prix, valeur estimee et PnL latent.
- Dashboard responsive mobile avec cartes compactes sous 768 px.
- Menu mobile slide-in allege : Marche, journal, alertes et parametres.
- Cache backend du scan global pour éviter de spammer Binance.
- Auto-refresh dashboard toutes les 15 minutes.
- Tooltips au-dessus des titres de colonnes.
- Actions contextuelles :
  - surveillance rapide depuis Marche ;
  - retrait de surveillance depuis la ligne ou la carte ;
  - ajout position/surveillance/journal depuis le detail ou les formulaires avances ;
  - synchronisation Binance depuis le cockpit.
- Badges discrets dans le scan :
  - Détenue
  - Surveillance
  - Opportunité
  - Spot
  - Alpha
  - Manuel
  - Inconnu
- Synchronisation Binance lecture seule :
  - soldes
  - trades
  - positions estimées
  - prix moyen estimé
  - PnL latent/réalisé
  - détection des résidus/dust

## Support mobile

Le dashboard V4 adapte automatiquement son interface :

- `mobile < 768 px` : le tableau marche devient une liste de cartes compactes et expandables.
- `tablet 768-1024 px` : la mise en page passe en colonnes empilees pour garder la lecture confortable.
- `desktop > 1024 px` : le tableau complet reste disponible.

Sur mobile, chaque carte affiche en priorite le symbole, la decision, la confiance, le prix, la tendance, le setup, le trigger et le principal facteur bloquant. Les details secondaires sont masques par defaut et s'ouvrent au tap : scores 15m/1h/4h/1d, RR, momentum, support/resistance, facteurs positifs et facteurs bloquants.

Le header mobile est sticky et donne acces au refresh et au menu slide-in. Les modals prennent presque toute la hauteur disponible, avec scroll interne et bouton de fermeture visible. Les tooltips desktop restent au hover ; sur mobile, ils s'ouvrent au tap et se ferment au tap ailleurs.

Limites connues : les grands tableaux internes des modals restent scrollables horizontalement quand beaucoup de colonnes sont necessaires, mais le scroll est limite a la modal et ne casse pas la page principale.

## Source de vérité des positions

Les positions actives viennent uniquement de `GET /api/v3/account` : une crypto est considérée détenue seulement si son solde réel `free + locked` existe et si sa valeur USDC atteint le seuil actif.

Les trades anciens ne créent jamais une position active à eux seuls. Si `DOGE` a un historique de trades mais un solde actuel à zéro, `DOGEUSDC` n'est pas affichée comme détenue. Les trades restent disponibles pour l'historique et le PnL réalisé.

Statuts :

- `ACTIVE` : position Binance réellement détenue et valeur actuelle >= seuil actif.
- `DUST` : solde Binance réel, mais valeur actuelle trop faible pour être une position exploitable.
- `CLOSED` : ancien symbole Binance sans solde actif.
- `MANUAL` : position saisie manuellement.

Une position manuelle peut utiliser un symbole qui n'existe pas sur Binance Spot. Dans ce cas, elle reste visible dans le cockpit. Si le token existe sur Binance Alpha, la source devient `Alpha`; sinon elle reste `Manuel` avec `Analyse indisponible` / `Paire Binance introuvable`.

Seuils utilisés par défaut :

Binance Alpha est utilise comme source de marche pour les tokens non disponibles sur Spot. Le resolver conserve le symbole natif (`CKP`, `MORPHO`) et utilise le `alphaId` de Binance Alpha pour appeler ticker et klines. Quand les candles Alpha sont disponibles, le cockpit calcule MA7/MA25/MA99, momentum et trend simple. Si elles ne sont pas disponibles, le prix, le volume, la valeur estimee et le PnL latent restent affiches.

```text
MIN_POSITION_AMOUNT = 0.00000001
MIN_ACTIVE_POSITION_VALUE_USDC = 1.00
```

Un résidu comme `ETH ≈ 0.18 USDC`, `DOGE ≈ 0.05 USDC` ou `SOL ≈ 0.07 USDC` est donc classé en `DUST`. Il n'apparaît pas comme `Détenue`, n'est pas inclus dans le résumé portefeuille principal et ne génère pas d'alerte portefeuille. Les poussières restent consultables via `GET /api/binance/dust`.

## Moteur PnL Binance

Le PnL Binance est reconstruit à partir de l'historique de trades avec un FIFO simplifié, mais la quantité affichée vient toujours du solde Binance actuel :

- les achats créent des lots de coût ;
- les ventes consomment les lots les plus anciens ;
- le `realized_pnl` vient uniquement des ventes ;
- l'`unrealized_pnl` vient uniquement de la quantité restante ;
- le `total_pnl` additionne réalisé et latent.

Différences :

- `realized_pnl` : gains/pertes des ventes déjà effectuées ;
- `unrealized_pnl` : gain/perte latent de la quantité actuellement détenue ;
- `total_pnl` : réalisé + latent.

Si la quantité reconstruite depuis les trades diffère du solde Binance réel, le solde Binance reste prioritaire et le calcul est marqué `is_estimated = true`. Les pourcentages latents sont masqués quand le coût restant est trop faible pour éviter des valeurs absurdes sur de la poussière.

## Routes API Binance privées

```text
GET /api/binance/status
POST /api/binance/sync
GET /api/binance/balances
GET /api/binance/trades/{symbol}
GET /api/binance/positions
GET /api/binance/pnl
GET /api/binance/account-summary
GET /api/binance/closed-trades
GET /api/binance/dust
```

## Routes API personnelles

Positions :

```text
GET /api/positions
GET /api/positions/active
GET /api/positions/inactive
POST /api/positions
PUT /api/positions/{id}
DELETE /api/positions/{id}
```

Cryptos à surveiller :

```text
GET /api/watch-candidates
POST /api/watch-candidates
PUT /api/watch-candidates/{id}
DELETE /api/watch-candidates/{id}
```

Journal :

```text
GET /api/journal
POST /api/journal
DELETE /api/journal/{id}
```

Marché :

```text
GET /api/market
GET /api/watchlist
GET /api/analyze/{symbol}?interval=1h
GET /api/analyze-multi/{symbol}
GET /api/scan?force=false
GET /api/decision/{symbol}
GET /api/decisions
GET /api/ai-alerts
GET /api/history/{symbol}
GET /api/top-momentum
GET /api/top-setups
GET /api/alerts
GET /api/score-history/{symbol}
GET /api/backtesting/results
GET /api/relative-strength/{symbol}
```

Administration locale :

```text
POST /api/admin/reset
POST /api/admin/reset?reset_watchlist=true
POST /api/admin/cleanup-invalid-symbols
POST /api/admin/repair-market-sources
```

Le reset supprime les données personnelles et historiques locales :

- `positions`
- `watch_candidates`
- `trade_journal`
- `market_history`
- `binance_balances`
- `binance_trades`
- état de synchronisation Binance
- cache backend du scan

Il remet aussi à zéro les états en mémoire comme le scan en cache et la synchronisation en cours. Côté dashboard, l'action `Reset local` vide aussi `localStorage` et `sessionStorage`.

Le marché peut réapparaître après reset : c'est normal. Il vient de la watchlist et des données live Binance publiques, pas de SQLite. Pour repartir sans watchlist, utiliser `reset_watchlist=true`; sinon la watchlist par défaut est restaurée.

## Base locale SQLite

La base est créée automatiquement :

```text
database/history.db
```

Tables principales :

- `market_history`
- `positions`
- `watch_candidates`
- `trade_journal`
- `binance_balances`
- `binance_trades`
- `binance_sync_state`

## Limites des estimations

Les prix moyens et PnL sont calculés à partir des trades récupérés. Si l'historique est incomplet ou si des mouvements externes existent, les résultats sont des estimations. Le projet n'invente jamais les données manquantes et marque les calculs incomplets comme estimés quand c'est détecté.

## Robustesse symboles invalides

Les symboles invalides ne doivent jamais casser le dashboard. Les watch candidates invalides sont refusees a l'ajout. Les anciennes entrees invalides sont retournees en JSON propre, avec `analysis_available=false` et `market_error` lorsque l'analyse est impossible.

Les positions manuelles invalides sont preservees pour ne pas perdre l'historique personnel. Les positions Binance invalides ou incoherentes peuvent etre marquees invalides par l'outil de nettoyage admin.

## Limites de l'analyse

Le moteur V5 est une aide a la lecture du marche. Il ne predit pas le futur, ne calcule pas une probabilite de gain, ne remplace pas une strategie personnelle et peut produire des signaux contradictoires lorsque le marche est instable ou lorsque les donnees sont insuffisantes. Les decisions doivent etre relues avec les niveaux, la taille de position, le contexte BTC et ton risque personnel.

## Avertissement

Ce projet est uniquement une aide à la décision. Il ne constitue pas un conseil financier. Il n'exécute aucun achat ou vente automatique. Il n'exécute aucun ordre. Utiliser uniquement une clé API Binance en lecture seule.
