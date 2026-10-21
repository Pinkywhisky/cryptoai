# Crypto AI Assistant v1.0.0

Assistant personnel d'analyse crypto basé sur Binance public et, optionnellement, sur ton compte Binance en lecture seule.

La release applicative demarre en `v1.0.0` pour faciliter les futures releases Git.

Le moteur interne V5 transforme V4 en assistant decisionnel oriente attention utilisateur. Le cockpit Marche fusionne portefeuille, surveillance et candidats qualifies, puis remonte seulement les evenements importants dans une section unique `Alertes IA`.

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

## Acces personnel V5.3 ? lot 2A

L'application est fermee tant que `CRYPTOAI_AUTH_PASSWORD_HASH` n'est pas configure. Aucun mot de passe par defaut, compte supplementaire ou secret de session persistant n'est cree. La configuration existante `.env` et la base personnelle ne sont pas modifiees par ce lot.

Generer un hash Argon2id avec saisie masquee, puis le fournir uniquement a l'environnement du serveur :

```bash
export CRYPTOAI_AUTH_PASSWORD_HASH="$(venv/bin/python -B tools/hash_password.py)"
venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

Le mot de passe (12 a 1024 caracteres) ne figure pas dans la commande ou son historique. Le hash reste cote serveur. Le cockpit local fonctionne sur `http://127.0.0.1:8000` et `http://localhost:8000`. Pour un autre port ou HTTPS, fournir les origines exactes, sans chemin :

```bash
export CRYPTOAI_ALLOWED_ORIGINS='https://crypto.example.com'
```

Derriere un reverse proxy HTTPS, configurer Uvicorn pour ne faire confiance aux en-tetes proxy qu'a l'adresse de ce proxy (par exemple `--proxy-headers --forwarded-allow-ips=127.0.0.1`). HTTP est accepte uniquement sur les adresses loopback. Une origine ou un Host inattendu est refuse.

Les sessions revocables sont conservees en memoire, avec un identifiant aleatoire dans un cookie signe par Starlette/itsdangerous. Le cookie est HttpOnly, SameSite=Strict, sans Domain et avec Secure sous HTTPS. La cle de signature est generee en memoire au demarrage. Utiliser un seul worker : les sessions ne sont pas partagees entre processus et un redemarrage deconnecte les navigateurs.

La duree absolue est de 3600 secondes, configurable avec `CRYPTOAI_SESSION_SECONDS` (60 a 86400). Les activites ne prolongent pas cette duree. La deconnexion revoque immediatement la session serveur ; un ancien cookie ne permet plus de revenir. A expiration, le cockpit efface ses donnees affichees et ses formulaires, puis retourne a la connexion. Une reponse API 401 produit le meme comportement ; aucun appel de modification n'est rejoue automatiquement.

Les formulaires de connexion, deconnexion et maintenance utilisent un jeton CSRF aleatoire lie a la session. Les appels JavaScript utilisent l'en-tete fixe non secret `X-CSRF-Guard: 1`, avec controle d'origine/Referer et de Fetch Metadata. Aucune permission CORS n'est accordee : un site tiers ne peut pas obtenir le preflight requis pour cet en-tete. Le JavaScript ne lit aucun cookie, mot de passe ou jeton CSRF de session et ne persiste aucun secret. Les anciens GET qui ecrivent dans le cache ou l'historique demandent aussi cette protection.

Les clients API authentifies peuvent utiliser `X-CSRF-Token` avec le jeton d'un formulaire de leur propre session ; les operations sensibles de maintenance exigent ce jeton, pas seulement l'en-tete fixe. Les champs sensibles ne sont jamais places dans les URL. Les erreurs d'authentification restent generiques et les valeurs de credentials serveur sont masquees dans les erreurs de validation. Les traces brutes des exceptions API ne sont plus journalisees.

Le reset et le nettoyage des symboles invalides sont desactives par defaut. Les activer demande `CRYPTOAI_ALLOW_DESTRUCTIVE=1`, un redemarrage, une session valide, un jeton CSRF valide, la ressaisie du mot de passe et une confirmation explicite `RESET` ou `CLEANUP`. Le formulaire de reset reste desactive tant que cette option n'est pas activee. La reparation des sources, les modifications personnelles et la synchronisation demandent une session et le CSRF ; aucune synchronisation privee n'est automatique.

Les echecs de mot de passe sont limites a cinq par fenetre glissante de 60 secondes, globalement pour le compte personnel (connexion et reautorisation). Les sessions anonymes et authentifiees ont des limites de memoire separees pour ne pas deconnecter l'utilisateur lors de nombreuses ouvertures de la page de connexion.

Dependances ajoutees :

- `itsdangerous>=2.2,<3`, requis pour les sessions signees de [Starlette](https://www.starlette.io/middleware/#sessionmiddleware).
- `pwdlib[argon2]>=0.3,<0.4`, pour le hachage/verificateur Argon2 recommande par [FastAPI](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/#install-pwdlib).

Le synchronizer token des formulaires et l'en-tete AJAX suivent les [recommandations CSRF OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html). Aucune primitive de chiffrement, de signature ou de hachage n'est reimplementee.

Les fixtures existantes se connectent vraiment par le formulaire et transmettent le CSRF ; aucun contournement de production n'est ajoute. Les tests de securite utilisent des clients anonymes independants, les vraies routes et le meme middleware. Executer les tests Python et JavaScript avec les commandes de la section Tests. La base reste temporaire, les cles Binance sont neutralisees et le reseau est bloque.

### Inventaire des routes et des effets

Toutes les routes metier, les anciennes routes et la documentation sont privees par defaut. La protection s'applique aussi aux futures routes ; aucune liste d'exceptions metier ne permet de la contourner. Seuls la page de connexion et les assets statiques sont publics.

| Methode | Route | Donnees / effet | Protection supplementaire |
| --- | --- | --- | --- |
| GET | `/` | Cockpit, portefeuille, surveillances et formulaires | Session |
| GET | `/api/watchlist` | Analyse / selection de marche, contexte personnel selon la route | Session |
| GET | `/api/analyze/{symbol}` | Analyse / selection de marche, contexte personnel selon la route | Session |
| GET | `/api/analyze-multi/{symbol}` | Analyse / selection de marche, contexte personnel selon la route | Session |
| GET | `/api/scan` | Univers personnel et ecriture possible cache / historique | Session + CSRF meme en GET |
| GET | `/api/market` | Univers personnel et ecriture possible cache / historique | Session + CSRF meme en GET |
| GET | `/api/ai-alerts` | Univers personnel et ecriture possible cache / historique | Session + CSRF meme en GET |
| GET | `/api/history/{symbol}` | Historique et resultats locaux | Session |
| GET | `/api/top-momentum` | Analyse / selection de marche, contexte personnel selon la route | Session |
| GET | `/api/top-setups` | Analyse / selection de marche, contexte personnel selon la route | Session |
| GET | `/api/decision/{symbol}` | Analyse / selection de marche, contexte personnel selon la route | Session |
| GET | `/api/decision-v5/{symbol}` | Analyse / selection de marche, contexte personnel selon la route | Session |
| GET | `/api/decisions` | Univers personnel et ecriture possible cache / historique | Session + CSRF meme en GET |
| GET | `/api/score-history/{symbol}` | Historique et resultats locaux | Session |
| GET | `/api/backtesting/results` | Historique et resultats locaux | Session |
| GET | `/api/relative-strength/{symbol}` | Univers personnel et ecriture possible cache / historique | Session + CSRF meme en GET |
| GET | `/api/positions` | Positions et portefeuille personnels | Session |
| GET | `/api/positions/active` | Positions et portefeuille personnels | Session |
| GET | `/api/positions/inactive` | Positions et portefeuille personnels | Session |
| POST | `/api/positions` | Creation / modification / suppression de donnees personnelles | Session + CSRF |
| PUT | `/api/positions/{position_id}` | Creation / modification / suppression de donnees personnelles | Session + CSRF |
| DELETE | `/api/positions/{position_id}` | Creation / modification / suppression de donnees personnelles | Session + CSRF |
| GET | `/api/watch-candidates` | Surveillances personnelles | Session |
| POST | `/api/watch-candidates` | Creation / modification / suppression de donnees personnelles | Session + CSRF |
| PUT | `/api/watch-candidates/{candidate_id}` | Creation / modification / suppression de donnees personnelles | Session + CSRF |
| DELETE | `/api/watch-candidates/{candidate_id}` | Creation / modification / suppression de donnees personnelles | Session + CSRF |
| GET | `/api/journal` | Journal personnel / statistiques | Session |
| POST | `/api/journal` | Creation / modification / suppression de donnees personnelles | Session + CSRF |
| PUT | `/api/journal/{entry_id}` | Creation / modification / suppression de donnees personnelles | Session + CSRF |
| DELETE | `/api/journal/{entry_id}` | Creation / modification / suppression de donnees personnelles | Session + CSRF |
| GET | `/api/journal/stats` | Journal personnel / statistiques | Session |
| GET | `/api/journal/analytics` | Journal personnel / statistiques | Session |
| GET | `/api/alerts` | Alertes sur positions et surveillances | Session |
| GET | `/api/binance/status` | Donnees du compte Binance et historique stocke | Session |
| POST | `/api/binance/sync` | Synchronisation privee | Session + CSRF |
| GET | `/api/binance/balances` | Donnees du compte Binance et historique stocke | Session |
| GET | `/api/binance/trades/{symbol}` | Donnees du compte Binance et historique stocke | Session |
| GET | `/api/binance/positions` | Donnees du compte Binance et historique stocke | Session |
| GET | `/api/binance/pnl` | Donnees du compte Binance et historique stocke | Session |
| GET | `/api/binance/account-summary` | Donnees du compte Binance et historique stocke | Session |
| GET | `/api/binance/closed-trades` | Donnees du compte Binance et historique stocke | Session |
| GET | `/api/binance/dust` | Donnees du compte Binance et historique stocke | Session |
| POST | `/api/admin/reset` | Suppression de donnees / maintenance destructive | Session + jeton CSRF + activation explicite + confirmation + mot de passe |
| POST | `/api/admin/cleanup-invalid-symbols` | Suppression de donnees / maintenance destructive | Session + jeton CSRF + activation explicite + confirmation + mot de passe |
| POST | `/api/admin/repair-market-sources` | Mise a jour des sources de marche personnelles | Session + CSRF |
| GET | `/analyze/{symbol}` | Analyse / selection de marche, contexte personnel selon la route | Session |
| GET | `/scan` | Analyse / selection de marche, contexte personnel selon la route | Session |
| GET | `/login` | Formulaire de connexion / etat de configuration | Public, aucune donnee personnelle |
| POST | `/login` | Creation et rotation de session | Jeton CSRF preconnexion + mot de passe + limitation des tentatives |
| POST | `/logout` | Revocation de session | Session + jeton CSRF |
| GET | `/api/auth/session` | Etat et duree restante, sans secret | Session |
| GET/HEAD | `/docs`, `/docs/oauth2-redirect`, `/redoc`, `/openapi.json` | Documentation et schema | Session |
| GET/HEAD | `/static/*` | JavaScript et CSS applicatifs | Public, aucun fichier personnel |

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

Les tests utilisent automatiquement une base SQLite temporaire, y compris lors
de l'import de FastAPI. Ils ignorent le fichier `.env`, neutralisent les cles
Binance et bloquent les connexions reseau des le chargement de pytest. Chaque
test repart d'une base applicative temporaire vide. Les autres bases de test
doivent rester dans les repertoires temporaires de pytest.

Pour eviter aussi les caches Python et pytest :

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pytest -q -p no:cacheprovider
```

Les tests unitaires du cockpit utilisent Node.js >= 18, sans paquet supplementaire,
et bloquent aussi les connexions reseau :

```bash
node tests/test_cockpit.js
```

Hors des tests, `CRYPTOAI_DB_PATH` permet de choisir une base de developpement.
Sans cette variable, le chemin habituel `database/history.db` est conserve.

## Architecture V5

Nouveaux modules :

- `services/alerts_engine.py` : consolidation des alertes opportunite, surveillance, risque et marche.
- `services/opportunity_scanner.py` : scan leger de paires Binance USDC, filtrage liquidite/stables/levier, score d'opportunite interne.
- `services/market_universe.py` : fusion positions, surveillance et opportunites scanner pour construire le cockpit Marche.
- `services/binance_alpha.py` : support Alpha operationnel pour token list, ticker, candles, trend simple, volume, prix et PnL latent.
- `services/score_history.py` : snapshots SQLite des scores decisionnels et acceleration.
- `services/advanced_triggers.py` : squeeze, fake breakout, rejet resistance/support, divergences, absorption, acceleration, exhaustion candle, reclaim.
- `services/btc_correlation.py` : force relative altcoin vs BTC et ajustement de contexte.
- `services/market_regime.py` : classification `RISK_ON`, `RISK_OFF`, `NEUTRAL`, `PANIC`, `SPECULATIVE`.
- `services/backtesting.py` : simulation de signaux, winrate, gain/perte moyen, profit factor, expectancy, drawdown.
- `services/journal_analytics.py` : analyse du journal trading, winrate, emotions, setups, accuracy IA et patterns utilisateur.

Tables SQLite V5 :

- `score_snapshots`
- `backtest_results`
- `market_cache`
- `journal_entries`

## Marche cockpit principal

La vue `Marche` est la vue principale du dashboard. Elle fusionne automatiquement les positions actives Binance, les positions manuelles actives, les cryptos en surveillance et les opportunites automatiques qualifiees.

Une crypto n'apparait qu'une seule fois. Les badges indiquent l'origine de la ligne : `Détenue`, `Surveillance`, `Opportunité`, puis la source de marche `Spot`, `Alpha`, `Manuel` ou `Inconnu`.

La page Marche permet d'ajouter rapidement une crypto a surveiller sans ouvrir de modal. Les vues Positions et Surveillance restent disponibles comme formulaires de gestion avances depuis les actions contextuelles, mais le cockpit affiche l'essentiel directement.

Au chargement de la page, le dashboard appelle automatiquement `/api/market`. Si un cache marche existe en SQLite, il est retourne immediatement avec `source="cache"` et `updated_at`. Si aucun cache n'existe, le backend reconstruit le cockpit puis persiste le resultat. Le bouton `Rafraichir` force une reconstruction live.

Les actions de suppression sont contextuelles : une position manuelle affiche `Supprimer position`, une crypto en surveillance affiche `Retirer surveillance`, et une position Binance est seulement marquee comme geree par Binance. Une ligne qui possede plusieurs sources conserve les sources restantes apres suppression.

Seules les sources metier `POSITION`, `WATCH_CANDIDATE` et `OPPORTUNITY_SCANNER` justifient une ligne. Une metadata comme `BINANCE_SPOT` ne la maintient pas apres retrait de surveillance. Le tableau, les cartes et le detail ouvert sont reconcilies apres suppression.

Les anciens caches sans version compatible ou contenant `WATCHLIST` sont ignores puis reconstruits. Les mutations de surveillance et l'invalidation du cache SQLite partagent une transaction : si l'invalidation echoue, la mutation est annulee egalement.

Le resume portefeuille separe `Valeur portefeuille`, `PnL latent`, `PnL realise` et `PnL total`. Le latent correspond aux positions ouvertes, le realise aux trades clotures, et le total additionne les deux.

## Alertes IA

La section `Alertes IA` remplace les anciens widgets separes Opportunites, Risques, Scanner et Heatmap. Elle affiche peu d'elements, mais uniquement ceux qui meritent l'attention.

Types d'alertes :

- `OPPORTUNITY` : breakout potentiel, momentum en amelioration, support defendu, reprise de volume.
- `WATCH` : setup en construction, trigger proche, approche support/resistance, compression.
- `RISK` : cassure support, deterioration rapide, pression vendeuse, drawdown important.
- `MARKET` : regime global defensif, BTC sous pression, retour de momentum ou reprise du volume.

Les alertes sont triees par priorite produit : risques, opportunites, surveillance, puis contexte marche. Les alertes faibles ou redondantes sont filtrees pour limiter le bruit.

## Journal Intelligence V5.2

Le journal n'est plus une simple note manuelle. Chaque entree sauvegarde une decision personnelle et un snapshot IA complet au moment de l'action :

- symbole et source de marche (`Spot`, `Alpha`, `Manuel`, `Inconnu`) ;
- type de decision : `BUY`, `SELL`, `HOLD`, `WAIT`, `REINFORCE`, `CUT_LOSS` ;
- type de trade : `SCALP`, `SWING`, `LONG_TERM`, `TEST`, `FOMO`, `REVENGE` ;
- emotion : `CONFIDENT`, `FOMO`, `FEAR`, `IMPATIENT`, `STRESSED`, `CALM`, `GREED`, `REVENGE` ;
- resultat : `OPEN`, `WIN`, `LOSS`, `BREAKEVEN`, `CANCELLED` ;
- PnL %, PnL USDC et duree ;
- score IA, trend, setup quality, trigger, regime de marche et snapshot JSON rejouable.

Le dashboard affiche des statistiques de progression :

- winrate global ;
- gain moyen et perte moyenne ;
- meilleur setup ;
- emotion la plus risquee ;
- comparaison entre decision IA et resultat reel.

Objectif : relire ses decisions, identifier ses biais emotionnels, comprendre les bons/mauvais setups et ameliorer progressivement l'utilisation du moteur decisionnel.

## Scanner opportunites interne

Le scanner V5 reste utilise en interne par le cockpit Marche et les Alertes IA :

1. Scan leger sur un univers USDC limite, base sur volume 24h, variation, volatilite, breakout potentiel et force relative.
2. Analyse complete uniquement sur les cryptos en surveillance, les positions et les meilleurs candidats automatiques.
3. Filtrage qualitatif par le moteur decisionnel avant affichage comme candidat.

Une opportunite automatique signifie : "candidat d'achat a examiner". Elle ne signifie pas : "cela va monter".

Pour rester visible dans Marche comme opportunite, une crypto doit rester constructive apres analyse et retourner uniquement `BUY_READY` ou `BUY_WATCH`. Une decision `WAIT`, `AVOID`, `SELL_WATCH`, `TAKE_PROFIT` ou `CUT_LOSS` ne doit pas etre affichee comme opportunite automatique.

Ces decisions ne dispensent jamais des autres criteres de qualification (scores, confiance, tendance et absence de fake breakout). Le cockpit garde le moteur `build_decision` existant ; `build_decision_v5` reste explicite.

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

`timing_status` et `timing_is_technical_signal` decrivent le signal technique. Un signal `CONFIRMED` ne permet pas, a lui seul, une entree : `timing_is_actionable` n'est vrai que pour un signal confirme accompagne de la decision finale `BUY_READY`. Un veto de risque ou une decision de gestion prime toujours. Les scores et les regles de classification des decisions restent inchanges.

Scores V4 :

- `context_score` : trend global, alignement des moyennes mobiles, prix vs MA25/MA99, momentum multi-timeframe, volume.
- `setup_score` : support/resistance, risk/reward, proximite support, distance resistance, consolidation et structure.
- `trigger_score` : confirmation d'entree par breakout, MA25, resistance, volume, cloture verte forte ou rebond support.
- `risk_score` : distance stop-loss, ratio risk/reward, resistance proche, volatilite recente, meche haute et faux breakout possible.
- `market_score` : tendance BTC, momentum BTC, proportion de cryptos bullish/bearish dans les resultats analyses.
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
- Opportunites qualifiees dans le cockpit : `BUY_READY` ou `BUY_WATCH`.
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
PUT /api/journal/{id}
DELETE /api/journal/{id}
GET /api/journal/stats
GET /api/journal/analytics
```

Marché :

```text
GET /api/market
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

Les routes historiques `/api/scan`, `/api/decisions`, `/api/top-momentum`, `/api/top-setups` et `/scan` gardent une watchlist explicite si elle est configuree. Sinon, elles analysent l'univers positions/surveillances/candidats Spot, avec priorite aux entrees utilisateur. `/api/relative-strength/{symbol}` ajoute le contexte BTC a cet univers. Le cache du scanner depend des symboles et de la limite de bougies demandes.

Administration locale :

```text
POST /api/admin/reset
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

Le marché peut réapparaître après reset : c'est normal. Il vient des positions, des cryptos en surveillance et des propositions live Binance publiques, pas uniquement de SQLite.

## Base locale SQLite

La base est créée automatiquement :

```text
database/history.db
```

Tables principales :

- `market_history`
- `positions`
- `watch_candidates`
- `journal_entries`
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
