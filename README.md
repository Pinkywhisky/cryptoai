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

## Acces personnel V5.3 ? lots 2A et 2B

L'application est fermee tant que `CRYPTOAI_AUTH_PASSWORD_HASH` n'est pas configure. Aucun mot de passe par defaut, compte supplementaire ou secret de session persistant n'est cree. La configuration existante `.env` et la base personnelle ne sont pas modifiees par ce lot.

Generer un hash Argon2id avec saisie masquee, puis le fournir uniquement a l'environnement du serveur :

```bash
export CRYPTOAI_AUTH_PASSWORD_HASH="$(venv/bin/python -B tools/hash_password.py)"
venv/bin/python -B tools/serve.py
```

Le mot de passe (12 a 1024 caracteres) ne figure pas dans la commande ou son historique. Le hash reste cote serveur. Le cockpit local fonctionne sur `http://127.0.0.1:8000` et `http://localhost:8000`. Pour un autre port ou HTTPS, fournir les origines exactes, sans chemin :

```bash
export CRYPTOAI_ALLOWED_ORIGINS='https://crypto.example.com'
```

Derriere un reverse proxy HTTPS, fournir `CRYPTOAI_TRUSTED_PROXIES` avec l'adresse precise du proxy et garder les en-tetes proxy Uvicorn desactives via le lanceur ci-dessous. HTTP est accepte uniquement pour des origines loopback. Une origine ou un Host inattendu est refuse.

Les sessions revocables sont conservees en memoire, avec un identifiant aleatoire dans un cookie signe par Starlette/itsdangerous. Le cookie est HttpOnly, SameSite=Strict, sans Domain et avec Secure sous HTTPS. La cle de signature est generee en memoire au demarrage. Utiliser un seul worker : les sessions ne sont pas partagees entre processus et un redemarrage deconnecte les navigateurs.

La duree absolue est de 3600 secondes, configurable avec `CRYPTOAI_SESSION_SECONDS` (60 a 86400). Les activites ne prolongent pas cette duree. La deconnexion revoque immediatement la session serveur ; un ancien cookie ne permet plus de revenir. A expiration, le cockpit efface ses donnees affichees et ses formulaires, puis retourne a la connexion. Une reponse API 401 produit le meme comportement ; aucun appel de modification n'est rejoue automatiquement.

Les formulaires de connexion, deconnexion et maintenance utilisent un jeton CSRF aleatoire lie a la session. Les appels JavaScript utilisent l'en-tete fixe non secret `X-CSRF-Guard: 1`, avec controle d'origine/Referer et de Fetch Metadata. Aucune permission CORS n'est accordee : un site tiers ne peut pas obtenir le preflight requis pour cet en-tete. Le JavaScript ne lit aucun cookie, mot de passe ou jeton CSRF de session et ne persiste aucun secret. Les anciens GET qui ecrivent dans le cache ou l'historique demandent aussi cette protection.

Les clients API authentifies peuvent utiliser `X-CSRF-Token` avec le jeton d'un formulaire de leur propre session ; les operations sensibles de maintenance exigent ce jeton, pas seulement l'en-tete fixe. Les champs sensibles ne sont jamais places dans les URL. Les erreurs d'authentification restent generiques et les valeurs de credentials serveur sont masquees dans les erreurs de validation. Les traces brutes des exceptions API ne sont plus journalisees.

Le reset et le nettoyage des symboles invalides sont desactives par defaut. Les activer demande `CRYPTOAI_ALLOW_DESTRUCTIVE=1`, un redemarrage, une session valide, un jeton CSRF valide, la ressaisie du mot de passe et une confirmation explicite `RESET` ou `CLEANUP`. Le formulaire de reset reste desactive tant que cette option n'est pas activee. La reparation des sources, les modifications personnelles et la synchronisation demandent une session et le CSRF ; aucune synchronisation privee n'est automatique.

Les tentatives de mot de passe sont limitees a cinq par adresse normalisee sur une fenetre glissante de 60 secondes (IPv4 et IPv6 /64, connexion et reautorisation). Un succes ne remet a zero que son adresse. Les echecs distribues ajoutent un delai borne a une seconde, sans verrouillage global par compteur; deux verifications Argon2 simultanees maximum sont admises. Une saturation temporaire repond 429 avec Retry-After. Les registres sont expires et bornes (4096 adresses, 60 echecs globaux, 128 sessions de chaque type et huit sessions anonymes par adresse); une adresse n'evince pas les sessions en attente des autres par un simple flood de pages de connexion. Les sessions authentifiees ont leur propre capacite.

Dependances ajoutees :

- `itsdangerous>=2.2,<3`, requis pour les sessions signees de [Starlette](https://www.starlette.io/middleware/#sessionmiddleware).
- `pwdlib[argon2]>=0.3,<0.4`, pour le hachage/verificateur Argon2 recommande par [FastAPI](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/#install-pwdlib).

Le synchronizer token des formulaires et l'en-tete AJAX suivent les [recommandations CSRF OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html). Aucune primitive de chiffrement, de signature ou de hachage n'est reimplementee.

Les fixtures existantes se connectent vraiment par le formulaire et transmettent le CSRF ; aucun contournement de production n'est ajoute. Les tests de securite utilisent des clients anonymes independants, les vraies routes et le meme middleware. Executer les tests Python et JavaScript avec les commandes de la section Tests. La base reste temporaire, les cles Binance sont neutralisees et le reseau est bloque.

### Renforcement V5.3 — lot 2B

Le rendu HTML des donnees API est echappe dans les tableaux, cartes, details, alertes et journal. Les champs textuels simples utilisent `textContent`; les valeurs d'attribut sont aussi echappees, et les segments d'URL sont encodes. Les rares fragments HTML conserves sont construits par des helpers internes explicites. Aucun HTML fourni par l'API n'est considere comme fiable.

La CSP impose `default-src 'none'`, `script-src 'self'`, `style-src 'self'` et `connect-src 'self'`, sans `unsafe-inline` ni `unsafe-eval`. Les styles precedemment en ligne sont dans la feuille CSS. Objets, frames, workers, integration dans une iframe et base URL sont interdits. Les formulaires restent sur la meme origine. Les erreurs gardent les memes protections. La politique `Referrer-Policy: same-origin` preserve l'Origin des formulaires natifs sans envoyer de referent aux sites externes. `no-referrer` aurait provoque `Origin: null` et bloque la connexion native : voir le [standard Fetch](https://fetch.spec.whatwg.org/#append-a-request-origin-header). `Origin: null` reste refuse.

Les ecritures utilisent des modeles Pydantic : champs inconnus refuses, types numeriques JSON sans booleens ou chaines, valeurs finies, quantite positive, couts positifs ou nuls, niveaux optionnels positifs, pourcentages et confiance bornes, textes limites a 4 000 caracteres, symboles limites a 40, identifiants SQLite positifs. Les choix connus sont normalises en majuscules; les anciennes valeurs `ATTENTE`, `ACHAT` et `VENTE` du journal restent acceptees. Une position exige symbole, quantite, prix moyen et investissement. Un PUT reste partiel : omettre un champ le conserve; `null` peut effacer un niveau optionnel, mais pas l'identite ou les montants obligatoires. Le journal accepte toujours des PnL negatifs. Les historiques ne sont pas migres. Les floats et tous les calculs financiers restent inchanges; leur precision comptable releve du lot financier.

Les corps sont limites a 64 Kio, y compris sans Content-Length, et les formulaires d'identification a 4 Kio. Les objets JSON a cles dupliquees, les encodages invalides et les structures excessivement imbriquees sont refuses avant le service metier. Une validation invalide retourne 422 sans ecriture partielle. Les champs AI historiques acceptes par l'API restent des metadonnees : le service construit toujours son propre snapshot.

Les evenements de securite sont des objets JSON horodates : route sous forme de modele, methode, statut, classe d'exception et identifiant aleatoire `X-Request-ID`. Les valeurs du corps, les query strings, cookies, jetons et messages bruts d'exception sont exclus. Le lanceur desactive l'access log Uvicorn, qui pourrait enregistrer une URL contenant un secret. Les echecs tardifs d'une reponse deja commencee ferment la reponse; aucun detail interne n'est renvoye et aucun traceback sensible n'est journalise.

#### Configuration locale et reverse proxy

Le lanceur recommande est `venv/bin/python -B tools/serve.py`. Il fixe un worker, 64 requetes concurrentes maximum, cinq secondes de keep-alive, aucun access log brut et **proxy_headers=False**. La confiance proxy appartient exclusivement a l'application. Une ecoute hors loopback exige des origines HTTPS et des proxies explicitement approuves.

Variables supplementaires, fournies a l'environnement du serveur :

| Variable | Defaut / regle |
| --- | --- |
| `CRYPTOAI_ENV` | `development`; `production` exige HTTPS et un hash configure |
| `CRYPTOAI_TRUSTED_PROXIES` | Vide; adresses/CIDR explicites, jamais `*`, IPv4 /0 ou IPv6 /0 |
| `CRYPTOAI_ALLOWED_ORIGINS` | Origines exactes; les deux adresses locales port 8000 par defaut |
| `CRYPTOAI_SESSION_SECONDS` | 3600, duree absolue de 60 a 86400 secondes |
| `CRYPTOAI_ALLOW_DESTRUCTIVE` | 0; conserver desactive hors maintenance volontaire |

En production, `/docs`, `/redoc`, `/docs/oauth2-redirect` et `/openapi.json` ne sont pas exposes, meme apres connexion. En developpement, les pages de documentation redirigent vers le schema JSON local authentifie; aucun script Swagger/ReDoc distant n'est charge.

Ne pas activer le traitement des proxies dans Uvicorn en plus de celui de l'application : il ferait perdre l'adresse du pair de transport avant sa verification. Le lanceur configure aussi un flux JSON INFO pour crypto_ai.security, independant du niveau du logger racine. Pour un lancement Uvicorn manuel, configurer ce logger au niveau INFO avec une sortie console, puis utiliser explicitement `--no-proxy-headers --no-access-log --workers 1` et appliquer les memes limites que le lanceur. L'application ne peut pas recuperer une adresse deja remplacee par un serveur ASGI mal configure.

Un proxy approuve peut fournir `X-Forwarded-For` et `X-Forwarded-Proto`. La chaine d'adresses est parcourue depuis le dernier saut jusqu'au premier pair non approuve; IPv4 mappee en IPv6 est normalisee. Les en-tetes de clients directs sont ignores; `Forwarded` et `X-Forwarded-Host` ne sont jamais utilises. Une chaine invalide ou dupliquee provenant d'un proxy approuve est refusee.

Pour un futur Compose + Nginx/Caddy : conserver le backend sur un reseau prive, ne pas publier son port vers Internet, approuver l'adresse precise du proxy, terminer HTTPS au proxy et conserver le Host original. Le proxy doit remplacer les en-tetes X-Forwarded-For/Proto par ses propres valeurs, refuser les Host inconnus, borner tailles et delais et ajouter sa propre protection contre les floods. Conserver CSP et HSTS sans politiques contradictoires; HSTS est emis par l'application pour les requetes HTTPS reconnues. Les secrets restent hors image, fichiers JavaScript et URL. Le volume SQLite et ses sauvegardes restent prives et accessibles uniquement au compte de service.

Permissions conseillees a examiner manuellement : `chmod 600 .env` et droits restrictifs du repertoire SQLite/sauvegardes. Ces commandes ne sont pas executees par les outils de validation et ne remplacent aucun fichier personnel. Ne pas recopier un exemple par-dessus un .env existant.

Les versions de FastAPI, Starlette, Pydantic, Uvicorn et bibliotheques HTTP/templates declarees correspondent aux versions mineures testees. Pydantic et Starlette deviennent des dependances directes explicites car leurs API sont utilisees directement; ils etaient deja installes transitivement. Aucune bibliotheque runtime supplementaire n'est installee. Revalider les mises a jour et les avis de vulnerabilites avant une exposition publique; cette campagne n'est pas un audit exhaustif de la chaine de dependances.

#### Validation reproductible

Depuis la racine, dans WSL :

```bash
venv/bin/python -B tools/verify_offline.py
venv/bin/python -B tools/verify_offline.py --coverage
node tests/test_cockpit.js
venv/bin/python -B tools/verify_security_http.py
venv/bin/python -B tools/verify_security_mutations.py --node /chemin/vers/node
```

La couverture utilise le module standard `trace` : lignes Python applicatives, threads inclus, pas couverture de branches. Ses fichiers sont ecrits sous /tmp. Les tests HTTP lancent puis arretent Uvicorn avec SQLite temporaire, aucune lecture du .env et des stubs pour les donnees externes. Seules les connexions loopback du pilote sont autorisees; le serveur ne peut faire aucun appel sortant. Les routes /api/test existent uniquement dans cette fixture de tests, jamais dans main ou le lanceur de production. Les mutations copient seulement les sources dans /tmp, y reintroduisent les defauts puis detruisent la copie; les sources originales ne sont jamais mutees.

Les tests JavaScript demandent Node >= 18; la sonde de navigateur reel utilise WebSocket natif et demande Node >= 22 (version 24 testee). Si Node n'est disponible que sous Windows, utiliser son executable Windows pour les tests JavaScript; --node peut recevoir son chemin /mnt/c/... depuis WSL.

Pour le DOM reel sous Windows : lancer `tools/verify_security_http.py --serve-browser`, puis `node tools/verify_security_browser.cjs http://127.0.0.1:PORT` avec l'URL affichee. Le navigateur Edge utilise un profil jetable, un proxy sortant volontairement indisponible, une interception CDP limitant la page a cette origine locale et aucune bibliotheque ajoutee. Le mot de passe affiche par cette fixture est exclusivement une valeur de test. Creer ensuite le fichier stop affiche pour terminer le serveur. Les captures desktop/mobile sont placees dans le repertoire temporaire affiche, jamais dans le depot.

Limites d'exploitation : un seul processus; quotas partages entre clients d'un meme NAT ou /64 IPv6; une attaque distribuee continue peut encore saturer reseau/CPU. Les limites applicatives ne remplacent pas les limites du proxy. Aucune execution privee Binance ni aucun ordre n'est necessaire a ces tests.

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
| GET | `/scan` | Analyse / selection de marche, contexte personnel selon la route | Session + CSRF meme en GET |
| GET | `/login` | Formulaire de connexion / etat de configuration | Public, aucune donnee personnelle |
| POST | `/login` | Creation et rotation de session | Jeton CSRF preconnexion + mot de passe + limitation des tentatives |
| POST | `/logout` | Revocation de session | Session + jeton CSRF |
| GET | `/api/auth/session` | Etat et duree restante, sans secret | Session |
| GET/HEAD | `/docs`, `/docs/oauth2-redirect`, `/redoc`, `/openapi.json` | Documentation et schema | Session |
| GET/HEAD | `/static/*` | JavaScript et CSS applicatifs | Public, aucun fichier personnel |

## Lancement

```bash
venv/bin/python -B tools/serve.py
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

Binance Alpha est utilise comme source de marche pour les tokens non disponibles sur Spot. Le resolver conserve le symbole natif (`CKP`, `MORPHO`) et utilise le `alphaId` de Binance Alpha pour appeler ticker et klines. Quand les candles Alpha sont disponibles, le cockpit calcule MA7/MA25/MA99, momentum et trend simple. Si elles ne sont pas disponibles, les données de marché disponibles restent affichées. Une valorisation et un PnL monétaires exigent une cotation vérifiée, sa conversion éventuelle et un coût documenté ou déclaré ; sinon ils restent indisponibles.

```text
MIN_POSITION_AMOUNT = 0.00000001
MIN_ACTIVE_POSITION_VALUE_USDC = 1.00
```

Un résidu comme `ETH ≈ 0.18 USDC`, `DOGE ≈ 0.05 USDC` ou `SOL ≈ 0.07 USDC` est donc classé en `DUST`. Il n'apparaît pas comme `Détenue`, n'est pas inclus dans le résumé portefeuille principal et ne génère pas d'alerte portefeuille. Les poussières restent consultables via `GET /api/binance/dust`.

## Moteur PnL Binance

Le PnL Binance suit le FIFO décimal et les statuts documentés dans la section V5.3 lot 3. La quantité affichée vient du dernier solde confirmé, avec une incertitude explicite lorsque l'historique ne le réconcilie pas :

- les achats créent des lots de coût ;
- les ventes consomment les lots les plus anciens ;
- le `realized_pnl` vient uniquement des ventes ;
- l'`unrealized_pnl` vient uniquement de la quantité restante ;
- le `total_pnl` additionne réalisé et latent.

Différences :

- `realized_pnl` : gains/pertes des ventes déjà effectuées ;
- `unrealized_pnl` : gain/perte latent de la quantité actuellement détenue ;
- `total_pnl` : réalisé + latent.

Si la quantité reconstruite depuis les trades diffère du solde confirmé, le solde reste prioritaire, le coût restant et le PnL non justifiables sont indisponibles ; une simple projection du coût moyen n'est pas présentée comme acquise. Les pourcentages latents sont masqués quand le coût restant est trop faible pour éviter des valeurs absurdes sur de la poussière.

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

## Fiabilisation financière V5.3 — lot 3

La synchronisation reste exclusivement en lecture : aucun endpoint de passage d'ordre ou de retrait n'est utilisable par le client. Les transactions sont identifiées par `(exchange, symbol, trade_id)`. Binance Spot réel et testnet ont des espaces distincts pour les trades, soldes, positions et curseurs. Une base correspond à un seul compte par réseau ; pour changer de compte Binance, utiliser une autre base.

La première collecte commence à `fromId=0`, jamais au dernier trade existant. Les pages de 1 000 maximum sont enregistrées avec leur curseur dans une même transaction, jusqu'à une page vide explicite. Les reprises repartent du curseur durable ; un chevauchement doit correspondre à des transactions déjà vérifiées. Les doublons contradictoires, pages sans progression et budgets dépassés produisent un état d'erreur explicite. Les transactions héritées sont conservées et recontrôlées lors du premier parcours complet.

Le bootstrap complet optionnel parcourt le catalogue Spot pour les cotations USDC, USDT, EUR et BTC, même sans solde actuel : les positions déjà vendues sont ainsi recherchées. La synchronisation normale suit le périmètre incrémental documenté dans la section lot 3B. Les marchés historiques stockés restent interrogés. `CRYPTOAI_HISTORY_SYMBOLS` peut fournir des paires historiques supplémentaires séparées par des virgules, sans modifier le fichier .env. Un marché retiré du catalogue et jamais connu localement ne peut pas être découvert automatiquement ; l'API myTrades exige un symbole. Les historiques indisponibles, cotations non reconnues et conversions manquantes sont signalés. COMPLETE décrit la collecte dans ce périmètre, pas une preuve d'exhaustivité de toute la vie du compte.

Les GET utilisent des tentatives et délais bornés. Un 429 avec Retry-After supérieur au budget n'est pas retenté avant ce délai : la tentative s'arrête et restitue l'attente conseillée. Après épuisement des retries, une panne globale, un bannissement ou un problème d'authentification coupe la boucle des marchés. Le lot 3B remplace l'espacement fixe par un budget partagé fondé sur les poids, fenêtres publiées et en-têtes observés. La première collecte de milliers de marchés peut être longue. Les lectures publiques de prix et le catalogue utilisent le même réseau que le client réel/testnet.

Un verrou SQLite avec propriétaire et expiration protège les synchronisations entre threads/processus. Chaque écriture de page et publication du portefeuille vérifie le propriétaire. Les pages déjà validées survivent à une interruption. Les soldes et les positions dérivées sont publiés atomiquement après une nouvelle réponse complète du compte. Une liste absente, malformée, un prix manquant ou une analyse indisponible ne vaut jamais fermeture. Seule une quantité confirmée nulle avec historique soldé et complet est classée CLOSED ; les retraits possibles ou historiques incomplets restent explicitement incertains. Les positions manuelles et leurs notes/niveaux ne sont pas écrasées. Les anciennes origines MIXED ne permettent pas de récupérer une composante manuelle déjà perdue avant ce lot.

Le FIFO utilise Decimal dans un contexte local de 256 chiffres. Les trades et soldes natifs gardent leurs chaînes décimales SQLite ; les colonnes REAL et nombres JSON de compatibilité ne sont pas les valeurs comptables faisant autorité. `decimal_values` conserve les valeurs sans arrondi d'affichage à huit décimales. Les anciens REAL sont marqués LEGACY_FLOAT tant qu'ils n'ont pas été revérifiés. Les entrées non finies et pathologiques sont refusées explicitement.

Les frais en actif acheté réduisent la quantité reçue ; ceux en actif vendu consomment aussi des lots. Les frais en cotation augmentent le coût ou réduisent le produit. Les paiements en troisième devise et les dépenses d'une cotation crypto consomment son inventaire. Une conversion historique nécessite une valeur explicitement documentée pour la devise, la cotation et l'horodatage concernés ; aucun prix actuel, interpolation ou parité USDT/USDC n'est substitué. La table historical_fx_rates conserve cette provenance par réseau ; ce lot ne télécharge pas automatiquement ces taux. Les conversions rendent l'évaluation estimative. Des exécutions de plusieurs marchés au même milliseconde ne sont pas ordonnées arbitrairement comme certaines.

Les mouvements d'inventaire explicitement fournis au moteur comptable acceptent les entrées à coût documenté (devise obligatoire), les entrées à coût inconnu et les sorties sans réaliser une vente. La collecte automatique reste Spot : elle ne collecte pas tous les dépôts, retraits, transferts internes, récompenses, Earn ou opérations Convert. Un écart de quantité, même petit, interdit d'inventer le coût. Même sans écart, les mouvements qui se compensent ne peuvent être exclus : les résultats issus de Spot seul restent ESTIMATED avec `funding_history_status=NOT_DOCUMENTED`. EXACT n'est employé pour les références que lorsque les données fournies sont complètes. Ce moteur n'est pas une comptabilité fiscale.

Le PnL réalisé inclut les ventes partielles, positions clôturées et poussières. Les positions manuelles gardent un périmètre déclaré distinct afin d'éviter les doublons avec Binance. Leur valorisation est également décimale et convertie en USDC seulement si la cotation et le taux sont connus. Les réalisés manuels historiques non nuls restent visibles comme déclarés non vérifiés ; un zéro par défaut sans preuve de vente demeure inconnu. Les positions et prix manquants ne sont pas transformés en zéros ; les totaux incomplets sont null, avec sous-totaux connus séparés. Les liquidités USDT/EUR sont valorisées seulement avec un cours observé, comptées une seule fois, et leur coût de change non documenté rend le PnL global incomplet. Le taux EUR fixe de 0,92 est supprimé ; une contre-valeur sans taux vérifié apparaît indisponible. Les prix Alpha sont convertis depuis leur devise effective ; un fallback de prix de token sans cotation vérifiée n'est pas une valorisation USDC.

### Migration future : copie isolée et validation indépendante

Aucune migration de `database/history.db` personnelle n'est autorisée par l'outil. Une base existante ne migre pas automatiquement au démarrage. Le démarrage sur l'ancien schéma échoue explicitement : ne pas relancer l'application sur la base personnelle avec ce code avant une procédure approuvée. Les nouvelles bases temporaires sont initialisées normalement.

Après validation indépendante et autorisation distincte de l'utilisateur :

1. Arrêter les écritures sur la base source ; vérifier qu'aucun WAL/SHM/journal actif ne subsiste. Ne jamais supprimer ces fichiers pour contourner cette vérification.
2. Faire une sauvegarde cohérente et une copie de travail isolée dans un répertoire privé (0700, fichiers 0600). Ne pas exporter de lignes, notes, clés ou montants dans un rapport.
3. Contrôler la copie, puis appliquer et recontrôler uniquement cette copie :

```bash
venv/bin/python -B tools/migrate_financial.py --database /chemin/prive/candidate.sqlite --check
venv/bin/python -B tools/migrate_financial.py --database /chemin/prive/candidate.sqlite --apply
venv/bin/python -B tools/migrate_financial.py --database /chemin/prive/candidate.sqlite --check
```

Le premier contrôle retourne MIGRATION_REQUIRED pour le schéma ancien sans écrire. L'application crée une sauvegarde SQLite vérifiée avant le DDL d'une copie existante. La migration v1 reconstruit sans suppression de lignes les contraintes des trades/soldes et les coûts de position devenus nullables ; elle conserve identifiants, séquences, index/triggers compatibles et tables historiques. Elle ajoute curseurs, verrou, état par réseau, provenance FX et statuts comptables. Les contraintes composites, types TEXT, intégrité, clés étrangères et comptes de lignes sont vérifiés. Un échec annule la transaction, conserve la sauvegarde et ne restaure ni ne détruit silencieusement de données.

4. Comparer les comptes historiques avant/après et vérifier les contraintes sur des données synthétiques séparées. Ne pas lancer les tests sur la copie personnelle.
5. La contre-vérification ne doit afficher que des comptes/statuts. Choisir une éventuelle mise en service de la copie validée par `CRYPTOAI_DB_PATH` dans l'environnement du serveur, sans écraser .env ni la base originale, uniquement après accord explicite.
6. Une première synchronisation privée réelle demeure une action distincte à autoriser par le propriétaire. Le backfill depuis zéro peut retrouver des transactions anciennement perdues par collision si Binance conserve leur marché ; la migration seule ne peut pas les reconstituer.

### Validation financière reproductible

Toutes les bases utilisées par les commandes ci-dessous sont temporaires et synthétiques. Le réseau sortant et les accès aux fichiers personnels sont audités et interdits :

```bash
venv/bin/python -B tools/verify_offline.py
venv/bin/python -B tools/verify_offline.py --coverage
node tests/test_cockpit.js
venv/bin/python -B tools/verify_financial_mutations.py --node /chemin/vers/node
venv/bin/python -B tools/verify_security_mutations.py --node /chemin/vers/node
```

Les mutations travaillent uniquement dans des copies jetables de sources autorisées, vérifient d'abord la référence, puis contrôlent que les originaux n'ont pas changé. Les références comptables couvrent FIFO, frais, cotations multiples, mouvements, inconnus et très petits montants. Les tests d'intégration couvrent reprise, verrou périmé, concurrence, reset protégé, migration non destructive, réseau réel/testnet séparé et réponses FastAPI authentifiées. La sonde HTTP de sécurité reste un complément sur loopback uniquement, avec serveur sans aucune sortie réseau.

## Synchronisation V5.3 — lot 3B

Le bouton « Synchroniser Binance » crée maintenant un travail incrémental et retourne immédiatement HTTP 202. Le travail s'exécute dans un thread déclenché explicitement. SQLite impose un seul travail actif, y compris entre processus et entre réel/testnet ; les écritures financières restent protégées par le lease à propriétaire du lot 3. Aucun service Redis/Celery ni aucune nouvelle dépendance n'est ajouté.

### Première utilisation et couverture

Une synchronisation normale ne parcourt jamais systématiquement le catalogue. Elle suit les marchés historiquement utilisés, les positions Binance, les actifs détenus, les devises de frais connues et les marchés explicitement ajoutés. Les nouveaux frais découverts pendant une collecte peuvent nécessiter l'incrémental suivant pour rechercher l'acquisition de leur devise. Un catalogue de 1 666 marchés ne justifie pas 1 666 appels myTrades à chaque clic.

L'import historique est distinct et volontaire :
1. Ouvrir « Importer un historique Binance » ; fournir des marchés connus ou utiliser l'historique déjà stocké comme amorce.
2. Prévisualiser le nombre de marchés, le minimum de requêtes et le poids estimé. Cette étape utilise au plus un catalogue public, aucune requête privée.
3. L'exploration de tout le catalogue est optionnelle et exige une prévisualisation puis une confirmation explicite. Elle conserve également les anciens marchés stockés et les marchés fournis.
4. Les pages et curseurs sont enregistrés progressivement. Annuler ou reprendre ne supprime aucun historique validé.

myTrades exige un symbole : les soldes actuels ne révèlent pas les marchés déjà entièrement vendus. Les marchés non explorés restent comptés et l'historique n'est pas déclaré complet par simple absence de solde. Les marchés BREAK ne sont pas supprimés : leur historique est conservé et leur statut est réexaminé. Catalogue et statut sont réévalués après six heures, ou immédiatement lors d'une prévisualisation/collecte utilisant un catalogue renouvelé. Les erreurs transitoires ont une attente de revue de 60 secondes ; les marchés BREAK/non reconnus, six heures. Une reprise explicite peut retenter les marchés concernés ; les suspensions globales de quota restent prioritaires.

La couverture initiale établie et la réactualisation sont deux informations différentes. Un marché auparavant vide peut recevoir ensuite un aller-retour entièrement vendu. L'API expose donc historical_coverage_as_of, markets_unrefreshed, refresh_scope et les marchés encore inexplorés. Si une partie du catalogue n'a pas été réactualisée, le réalisé global non justifiable reste null ; known_realized_pnl conserve le sous-total documenté. Un travail COMPLETED décrit l'exécution de son périmètre, pas une preuve d'exhaustivité actuelle de tout le compte. Un bootstrap complet ultérieur réactualise l'ensemble du périmètre confirmé. Les limites du lot 3 sur funding et conversions historiques restent applicables.

### Quotas communs au cockpit

Tous les transports Binance de l'application passent par binance_api_budget : client privé, prix publics, klines, catalogue, scanner de propositions et Alpha. Les réservations de poids et de requêtes brutes sont atomiques dans SQLite et partagées entre threads/processus utilisant cette base. Les alias Spot api1-4, api-gcp et data-api partagent le même suivi IP que api.binance.com ; le réseau testnet est distinct.

Le transport publie les rateLimits valides dès toute réponse exchangeInfo publique réussie, sans clé Binance et sans requête supplémentaire. REQUEST_WEIGHT et RAW_REQUESTS gardent leurs propres valeurs et intervalles ; ORDERS ne limite pas ces lectures. Les informations officielles remplacent les valeurs de repli, y compris lorsqu'elles sont inférieures. Une table malformée ou sans REQUEST_WEIGHT conserve la dernière politique valable. Si RAW_REQUESTS manque, sa dernière limite publiée est préservée. Les catalogues conservés en cache peuvent initialiser un budget vide, mais jamais remplacer une observation officielle déjà connue. Les réponses plus anciennes sont ignorées ; à instant identique, la limite la plus stricte est retenue. Le cache public regroupe les chargements simultanés à froid.

CRYPTOAI_API_SAFETY configure la fraction maximale utilisable, 0.8 par défaut, entre 0.1 et 0.95. Les replis s'appliquent uniquement tant qu'aucune limite officielle correspondante n'est disponible :

| Budget | Poids/minute avant marge | Requêtes brutes avant marge | Préfixe de configuration |
| --- | ---: | ---: | --- |
| Spot réel | 6000 | 61000 / 5 minutes | CRYPTOAI_API_SPOT |
| Spot testnet | 600 | 600 / minute | CRYPTOAI_API_TESTNET |
| Alpha (politique locale) | sans poids publié | 60 / minute et 10 / seconde | CRYPTOAI_API_ALPHA |
| Wallet accountSnapshot réel | 12000 | 600 / minute | CRYPTOAI_API_SNAPSHOT |
| Wallet accountSnapshot testnet, non utilisé | 600 | 600 / minute | CRYPTOAI_API_TESTNET_SNAPSHOT |

Pour chaque préfixe hors Alpha : suffixe _WEIGHT_PER_MINUTE ou _RAW_REQUESTS, entier de 1 à 1000000000. Exemple de configuration du processus : CRYPTOAI_API_SPOT_WEIGHT_PER_MINUTE=3000 ; cela fixe le repli, pas un remplacement des limites officielles. Aucune modification du .env n'est nécessaire aux tests. La configuration invalide refuse les nouveaux appels ; les changements de repli conservent la consommation et les suspensions persistées.

Les valeurs Spot 6000 et RAW_REQUESTS 61000/5 minutes correspondent aux exemples publiés par Binance ; la réponse API réelle reste prioritaire. Les replis testnet/Alpha et les plafonds RAW hors Spot sont des politiques locales prudentes. Wallet /sapi/v1/accountSnapshot (poids IP 2400) a son propre budget par endpoint et ses en-têtes X-SAPI-USED-IP-WEIGHT-* ; il ne consomme pas le plafond /api/v3. Aucun autre endpoint Wallet n'est pris en charge par le gestionnaire. Alpha compte désormais les requêtes locales, sans poids inventé ni import des limites Spot ; sa politique indépendante est détaillée ci-dessous.

Les en-têtes X-MBX-USED-WEIGHT-* renseignent la consommation absolue du quota IP, y compris celle d'autres logiciels. Le maximum distant observé et les coûts locaux ne diminuent jamais dans une même fenêtre. Les réservations encore incertaines s'ajoutent à ce maximum, puis sont confirmées par leur propre réponse ; cela évite de conserver un surcomptage une fois les requêtes parallèles terminées. L'absence d'en-tête ou un timeout ne rembourse pas la requête. Des doublons contradictoires utilisent la plus grande valeur valide. Un en-tête d'intervalle inconnu est mesuré sans inventer une limite numérique de 600. Les fenêtres UTC sont renouvelées à leurs bornes, les requêtes en vol sont conservées ; une réponse datée de la fenêtre précédente ne pollue pas la nouvelle. Sans date fiable, l'observation reste prudente dans la fenêtre de réception.

Lorsqu'une nouvelle limite officielle introduit un intervalle non encore contrôlé, les compteurs actifs connus sont conservés prudemment jusqu'à sa première borne, même si cet intervalle est plus court. Cette transition peut temporairement refuser des requêtes : elle évite de remettre la consommation à zéro lors du changement de politique. Sans historique complet ni en-tête distant correspondant, la distribution passée entre intervalles ne peut pas être reconstituée exactement.

Le gestionnaire n'attend pas la fin d'un quota : il refuse avant HTTP avec un délai de reprise. Un cache de cockpit valable est lu sans appel Binance ni réservation. Si un complément de contexte BTC rencontre un quota, la ligne devient indisponible sans décision inventée et sans faire échouer tout le cockpit. Les redirections HTTP ne sont pas suivies, afin d'éviter un second appel non réservé ou la retransmission de paramètres signés. Les estimations d'aperçu utilisent la limite de poids connue la plus contraignante et son intervalle ; elles restent des moyennes théoriques sans prédiction de la consommation extérieure.

Sources : [limites et en-têtes Spot](https://developers.binance.com/en/docs/products/spot/rest-api#limits), [exchangeInfo](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/general), [poids des endpoints publics](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market), [limites distinctes /api et /sapi](https://developers.binance.com/en/docs/products/advanced-earn/general-info), [accountSnapshot](https://developers.binance.com/en/docs/catalog/core-trading-wallet/api/rest-api/account).

Un 429 suspend immédiatement les appels du réseau concerné, sans retry immédiat. Un 418 et son Retry-After sont conservés, sans changement de clé ou d'hôte pour les contourner. Sans délai valide : 60 secondes pour 429, trois jours pour 418. Un reset des données métier ne supprime pas ces suspensions. Un dépassement local ou distant place le travail en PAUSED_QUOTA ; la reprise est explicite après le délai, sans requête privée automatique au redémarrage. Les GET 5xx/timeouts conservent seulement leurs retries bornés du lot 3.

Les autres logiciels utilisant la même IP restent hors du contrôle local : les en-têtes observés et la marge réduisent ce risque, sans prétendre coordonner une application externe. Les caches du resolver public et du catalogue de synchronisation restent distincts ; un chargement public supplémentaire du catalogue peut encore survenir à froid, mais il est comptabilisé.

### Suivi, annulation et reprise

- POST /api/binance/sync : incrémental ; réponse 202 avec un identifiant de travail.
- POST /api/binance/sync/preview : symbols et full_scan ; prévisualisation de bootstrap.
- POST /api/binance/sync avec mode=bootstrap, preview_id et confirmed si catalogue complet.
- GET /api/binance/sync/jobs/{id} : progression persistante, marchés traités/restants, pages validées, nouveaux trades, doublons évités, activité et erreurs.
- POST /api/binance/sync/jobs/{id}/cancel : annulation coopérative.
- POST /api/binance/sync avec resume_id : reprise du travail stocké.
- GET /api/binance/status : état financier, travail courant et consommation API, sans appel Binance.

Toutes ces routes conservent l'authentification et les mutations exigent le CSRF. Les identifiants de travail ne sont pas des secrets d'authentification. Aucun secret n'est transmis au JavaScript ou placé dans une URL.

Les pages validées et leurs compteurs sont atomiques avec le curseur. Une interruption pendant la validation d'une page ne l'enregistre pas partiellement. Une reprise ne réinterroge pas les marchés DONE du même travail ; les transactions arrivées après leur traitement sont récupérées lors de l'incrémental suivant. L'interface affiche la couverture ancienne pour éviter de prétendre à un instantané simultané de tous les marchés.

Après crash, un travail dont le lease et le heartbeat ont expiré est marqué INTERRUPTED (au plus 90 secondes d'attente de sécurité) ; aucun appel privé ne redémarre seul. L'arrêt normal signale l'annulation et attend la libération du thread. Un appel HTTP déjà en vol termine dans son timeout avant le prochain contrôle d'annulation. Les dernières données fiables ne sont jamais transformées en clôture pour ce motif.

### SQLite et déploiement futur

L'extension de synchronisation a sa propre version 1 dans sync_schema_migrations, en complément de la version financière 1. Elle ajoute catalogue, états de revue, travaux/marchés, prévisualisations, budget partagé et champs de couverture. Les migrations restent explicitement réservées aux copies isolées autorisées. Même une copie déjà au schéma financier du lot 3 exige cette extension avant emploi.

init_db ne prend plus BEGIN IMMEDIATE sur les ouvertures normales : lecture des versions et du cookie de schéma, validation structurale en cas de changement, connexions refermées. Les migrations restent sous transaction et verrou d'initialisation. La procédure --check / --apply / --check de tools/migrate_financial.py valide les deux versions et conserve une sauvegarde préalable. Ne jamais l'exécuter sur la base personnelle ni supprimer ses WAL/SHM pour contourner une vérification.

Pour un futur Docker Compose : conserver un volume SQLite persistant et un seul écrivain logique de synchronisation. Aucune synchronisation privée n'est lancée au démarrage. Un arrêt brutal interrompt le travail ; la reprise nécessite une action explicite après expiration des leases.

### Reproduction hors réseau

```bash
venv/bin/python -B tools/verify_offline.py
venv/bin/python -B tools/verify_offline.py --coverage
node tests/test_cockpit.js
venv/bin/python -B tools/verify_sync_mutations.py --node /chemin/vers/node
venv/bin/python -B tools/verify_financial_mutations.py --node /chemin/vers/node
venv/bin/python -B tools/verify_security_mutations.py --node /chemin/vers/node
venv/bin/python -B tools/simulate_sync_cost.py --markets 1666 --known 3
```

Le simulateur n'utilise aucun secret ni HTTP. Il compare l'orchestrateur et init_db du commit financier 78b2358 au nouveau comportement sur des copies synthétiques ; l'outillage financier partagé courant est identique entre les deux variantes. Il distingue requêtes observées, poids documentés estimés, durée réseau simulée et temps machine mesuré. La comparaison nécessite cet objet Git local ; elle ne fait aucun fetch. Les rapports et sauvegardes de revue restent hors du dépôt.


### Binance Alpha : politique locale et fraîcheur (lot 3C)

La [documentation officielle Alpha Market Data](https://developers.binance.com/en/docs/catalog/advanced-trading-alpha-trading/api/rest-api/market-data), consultée le 22 octobre 2026, décrit les endpoints et leurs formats, mais ne fournit pas de plafond chiffré ni de poids de requête exploitable pour les trois appels utilisés ici. La description de get-exchange-info évoque les limites, sans table rateLimits dans son schéma/exemple. Le plafond Spot de 6000 ne s'applique donc pas par hypothèse à Alpha.

Endpoints publics utilisés :

| Usage | Chemin sous https://www.binance.com |
| --- | --- |
| Catalogue partagé | /bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list |
| Cotation | /bapi/defi/v1/public/alpha-trade/ticker |
| Bougies | /bapi/defi/v1/public/alpha-trade/klines |

Politique locale, indépendante de Spot :

| Paramètre du processus | Défaut avant marge | Avec la marge 0,8 |
| --- | ---: | ---: |
| CRYPTOAI_API_ALPHA_RAW_REQUESTS | 60 requêtes/minute | 48 |
| CRYPTOAI_API_ALPHA_BURST_PER_SECOND | 10 requêtes/seconde | 8 |

Ce sont des limites locales ajustables, pas des quotas annoncés par Binance. Une unité représente un appel HTTP réservé, quelle que soit la route Alpha. Les métriques conservent le champ historique weight pour compatibilité, avec accounting_unit=LOCAL_REQUESTS ; il ne désigne aucun poids officiel. Le paramètre CRYPTOAI_API_ALPHA_WEIGHT_PER_MINUTE de l'ancienne estimation est retiré et ignoré ; utiliser les deux paramètres de requêtes ci-dessus. Ils acceptent les mêmes entiers que les replis communs ; une configuration dont la marge ne permet aucun appel échoue fermée.

Le défaut permet 21 appels à froid pour dix tokens : un catalogue, dix cotations, dix séries de bougies. Les protections de rafale imposent jusqu'à deux pauses locales d'une seconde dans ce scénario instantané. Les fenêtres sont alignées UTC, avec les réservations atomiques partagées entre processus. Deux transports Alpha au maximum s'exécutent simultanément par processus. Des appels à des clés différentes ne sont pas fusionnés ; plusieurs processus gardent leurs propres caches et leur propre sémaphore, tout en partageant le budget SQLite. La politique n'augmente pas automatiquement en cas de refus et ne coordonne pas les autres applications de la même IP.

429 et 418, ainsi que Retry-After numérique ou date HTTP, conservent les suspensions persistantes communes, sans retry immédiat. En leur absence : replis communs de 60 secondes / trois jours. Un éventuel couple simple RateLimit-Remaining=0 et RateLimit-Reset en secondes suspend les futurs appels ; les formes absentes, complexes ou invalides n'inventent aucun plafond. Ce support est défensif et ne prétend pas que Binance publie ces en-têtes sur Alpha. X-MBX-USED-WEIGHT-* n'est jamais interprété comme un quota Alpha.

Un timeout, une connexion interrompue ou un HTTP 5xx autorise au plus une nouvelle tentative après 250 ms, chaque tentative étant réservée. L'épuisement d'une fenêtre locale d'une seconde autorise au plus une attente d'une seconde et une nouvelle tentative ; les refus plus longs retournent immédiatement un délai. Après un échec de transport, un circuit Alpha partagé suspend brièvement les autres appels. Les échecs de chaque clé reçoivent également un délai exponentiel plafonné à 60 secondes, sans raccourcir un Retry-After serveur. Aucun ordre ni appel privé n'est nécessaire au fonctionnement d'Alpha.

Le catalogue, les cotations et les bougies sont chargés à la demande, mutualisés entre threads pour une même clé et conservés 600 secondes. Les paramètres des bougies et l'identifiant réel de la paire appartiennent à la clé de cache. Le cache est borné à 512 entrées, copie les valeurs et ne lance aucun travail réseau en arrière-plan. Les renouvellements se répartissent selon l'âge de chaque entrée et la capacité disponible. Une panne ne prolonge jamais leur horodatage.

États renvoyés dans market_data :

- FRESH : cotation courante et bougies exploitables.
- PARTIAL : cotation courante, analyse/bougies indisponibles ; aucune analyse technique n'est inventée.
- STALE : dernière cotation connue conservée uniquement pour information.
- UNAVAILABLE : aucune cotation exploitable.
- INVALID : absence confirmée dans un catalogue valide et courant.

Les causes distinctes comprennent QUOTA, NETWORK_ERROR, SERVICE_UNAVAILABLE, INVALID_RESPONSE, STALE_DATA et AMBIGUOUS_SYMBOL. Le résolveur retourne valid=null lorsqu'il ne peut pas conclure ; seuls un format invalide ou une absence confirmée donnent valid=false. Les créations/mises à jour retournent alors une erreur structurée 429/503, ou 400 pour un symbole ambigu. Les nettoyages et réparations reportent les symboles incertains au lieu de supprimer ou reclasser leurs données.

La fin du ticker (closeTime) sert d'horodatage lorsqu'elle est fournie. Sinon la date de réception est explicitement identifiée par RECEIVED_AT ; ce n'est pas l'heure d'une dernière transaction. Une cotation de 600 secondes ou plus n'est plus actuelle. Des bougies trop anciennes, incohérentes ou impossibles à exploiter numériquement n'alimentent pas l'analyse. Le prix non daté du catalogue n'est jamais un secours de cotation.

Pendant 1800 secondes supplémentaires au maximum, le cache peut exposer une dernière valeur connue avec sa devise native et sa date. current_price devient null et ticker est vide ; ce prix n'entre donc pas dans les valorisations ni les décisions. Une photographie comptable synchronisée conserve ses montants et sa date historique ; elle n'est pas présentée comme une nouvelle cotation Alpha. Les formules de PnL, conversion, scoring et le choix WAIT du cockpit Alpha restent inchangés.

Le tableau, les cartes mobiles, les détails et les listes personnelles affichent cet état avec échappement HTML. Le navigateur refuse aussi d'afficher comme actuel un payload reçu depuis plus de dix minutes. Le cache global ne prolonge pas la fraîcheur d'Alpha : une expiration ou une reprise renouvelle uniquement ses lignes, en préservant les lignes Spot et leur date de scan. Une invalidation concurrente empêche la résurrection du cache.

Vérifications reproductibles, exclusivement HTTP factice et SQLite temporaire :

```bash
venv/bin/python -B tools/verify_offline.py
node tests/test_cockpit.js
venv/bin/python -B tools/measure_alpha.py --output /tmp/alpha-measures.json
venv/bin/python -B tools/verify_alpha_mutations.py --node /chemin/vers/node --output /tmp/alpha-mutations.json
```

Le banc de mesure compare le commit 3B 379c4f6 et les sources locales sur des copies jetables. Les temps muraux sont ceux du banc local avec latence HTTP simulée de 1 ms ; les attentes de quota sont simulées séparément, pas des performances Binance mesurées. Il inclut dix tokens avec vingt appels Spot factices concurrents et une indisponibilité. Les mutations ne modifient jamais les sources de travail.
