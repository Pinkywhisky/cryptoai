"""Final independent-review regressions: public HTTP only, simulated time."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime
import json
import sqlite3

import pytest

from test_alpha_hardening import rig, Response
from test_alpha_real_regressions import markets
from services import binance_alpha as alpha, market_cache, personal_data
from services.binance_api_budget import ApiBudget, alpha_retry_after
from services.market_data_errors import MarketDataError


@pytest.mark.parametrize('status', [500, 502, 503, 504])
@pytest.mark.parametrize('date_format', [False, True])
def test_server_retry_after_120_is_shared_and_not_retried(rig, status, date_format):
    clock, budget, remote = rig
    value = format_datetime(datetime.fromtimestamp(clock()+120, timezone.utc), usegmt=True) if date_format else '120'
    remote.failures['ticker'] = Response(status=status, headers={'Retry-After': value})
    result = alpha.fetch_alpha_ticker('TOK0')
    assert result['error_code'] == 'SERVICE_UNAVAILABLE' and result['retry_after'] == 120
    assert sum(k == 'ticker' for k, _ in remote.calls) == 1
    assert budget.metrics('ALPHA')['block_reason'] == 'ALPHA_UNAVAILABLE'
    assert ApiBudget(budget.path, clock=clock).metrics('ALPHA')['retry_after'] == 120
    before = len(remote.calls)
    clock.advance(119)
    assert alpha.fetch_alpha_ticker('TOK1')['error_code'] == 'SERVICE_UNAVAILABLE'
    assert len(remote.calls) == before
    clock.advance(1)
    remote.failures.clear()
    assert alpha.fetch_alpha_ticker('TOK1')['price_available']


@pytest.mark.parametrize('header', [None, 'bad date', '-1', 'NaN', 'Infinity', '1e999',
    '9'*100, '120.5', 'Wed, 01 Jan 2020 00:00:00 GMT'])
def test_invalid_or_missing_retry_after_uses_bounded_backoff(rig, header):
    _, budget, remote = rig
    remote.failures['ticker'] = Response(status=503, headers={} if header is None else {'Retry-After': header})
    result = alpha.fetch_alpha_ticker('TOK0')
    assert result['error_code'] == 'SERVICE_UNAVAILABLE' and result['retry_after'] == 5
    assert sum(k == 'ticker' for k, _ in remote.calls) == 2
    assert budget.metrics('ALPHA')['retry_after'] == 5


def test_large_valid_delay_is_not_silently_truncated(rig):
    _, budget, remote = rig
    remote.failures['ticker'] = Response(status=503, headers={'Retry-After': '31536000'})
    assert alpha.fetch_alpha_ticker('TOK0')['retry_after'] == 31536000
    budget.suspend_alpha('ALPHA_NETWORK', 5)
    assert budget.metrics('ALPHA')['retry_after'] == 31536000


def test_suspend_alpha_respects_120_and_rejects_nonfinite(rig):
    _, budget, _ = rig
    budget.suspend_alpha('ALPHA_UNAVAILABLE', 120)
    assert budget.metrics('ALPHA')['retry_after'] == 120
    with pytest.raises(ValueError):
        budget.suspend_alpha('ALPHA_UNAVAILABLE', float('inf'))


def test_simultaneous_readers_share_service_suspension(rig):
    _, budget, remote = rig
    alpha.fetch_alpha_symbol('TOK0')
    remote.delay = .01
    remote.failures['ticker'] = Response(status=503, headers={'Retry-After': '120'})
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(alpha.fetch_alpha_ticker, ['TOK0']*8))
    assert all(row['error_code'] == 'SERVICE_UNAVAILABLE' for row in results)
    assert sum(k == 'ticker' for k, _ in remote.calls) == 1
    assert budget.metrics('ALPHA')['retry_after'] == 120


def candidates(remote, all_retired=False):
    remote.failures['catalog'] = Response([
        {'symbol': 'BOB', 'alphaId': 'ALPHA_0', 'chainId': '56', 'contractAddress': 'old',
         'offline': all_retired, 'fullyDelisted': all_retired},
        {'symbol': 'BOB', 'alphaId': 'ALPHA_1', 'chainId': '1', 'contractAddress': 'new',
         'offline': False, 'fullyDelisted': False}])


def test_two_active_candidates_require_complete_identity(rig):
    _, _, remote = rig
    candidates(remote)
    row = alpha.fetch_alpha_ticker('BOB')
    assert row['error_code'] == 'AMBIGUOUS_SYMBOL' and row['current_price'] is None
    assert len(row['candidates']) == 2
    partial = alpha.fetch_alpha_ticker('BOB', identity={'alphaId': 'ALPHA_1'})
    assert partial['error_code'] == 'AMBIGUOUS_SYMBOL'
    selected = alpha.fetch_alpha_ticker('BOB', identity={'alphaId': 'ALPHA_1', 'chainId': '1', 'contractAddress': 'new'})
    assert selected['price_available'] and selected['alpha_identity']['contractAddress'] == 'new'


def test_active_and_retired_never_replaces_historical_contract(rig):
    _, _, remote = rig
    candidates(remote, all_retired=True)
    historical = {'symbol': 'BOB', 'market_source': 'BINANCE_ALPHA', 'quantity': 2, 'invested_amount': 10}
    snapshot = personal_data.get_market_snapshot('BOB', market_source='BINANCE_ALPHA')
    assert snapshot['current_price'] is None
    assert historical['symbol'] == 'BOB'
    old = alpha.fetch_alpha_ticker('BOB', identity={'alphaId': 'ALPHA_0', 'chainId': '56', 'contractAddress': 'old'})
    assert old['error_code'] == 'DELISTED' and old['current_price'] is None
    active = alpha.fetch_alpha_ticker('BOB', identity={'alphaId': 'ALPHA_1', 'chainId': '1', 'contractAddress': 'new'})
    assert active['price_available']


def test_no_exploitable_candidate_returns_unavailable(rig):
    _, _, remote = rig
    candidates(remote, all_retired=True)
    remote.failures['catalog'].data['data'][1].update(offline=True, fullyDelisted=True)
    row = alpha.fetch_alpha_ticker('BOB')
    assert row['error_code'] == 'NO_MARKET' and row['current_price'] is None
    assert remote.calls == [('catalog', None)]


def test_offline_trading_conflict_indicative_only_and_recovery(rig, monkeypatch):
    clock, _, remote = rig
    remote.failures['catalog'] = Response([{'symbol': 'TOK0', 'alphaId': 'ALPHA_0', 'offline': True}])
    first = alpha.fetch_alpha_market_data('TOK0')
    assert first['error_code'] == 'OFFLINE_CONFLICT' and first['current_price'] is None
    assert not any(k == 'ticker' for k, _ in remote.calls)
    row = alpha.fetch_alpha_market_data('TOK0', verify_conflict=True)
    assert row['market_data']['last_known_price'] == 3.25
    assert row['market_data']['indicative_only'] is True
    assert row['market_data']['trading_authorized'] is False
    assert row['current_price'] is None and row['ticker'] == {} and not row['analysis_available']
    monkeypatch.setattr(personal_data, 'get_manual_fx_rate', lambda _: pytest.fail('No indicative price FX'))
    assert personal_data._manual_financial_values({'quantity': 2, 'invested_amount': 5}, row)['current_price'] is None
    before = len(remote.calls)
    alpha.fetch_alpha_ticker('TOK0', verify_conflict=True)
    assert len(remote.calls) == before
    clock.advance(601)
    remote.failures.clear()
    assert alpha.fetch_alpha_ticker('TOK0')['price_available']


@pytest.mark.parametrize('code', ['OFFLINE', 'DELISTED', 'NO_MARKET'])
def test_negative_deadline_matches_catalog_and_does_not_refresh_early(rig, code):
    clock, _, remote = rig
    remote.failures['catalog'] = Response([{'symbol': 'TOK0', 'alphaId': 'ALPHA_0',
        'offline': code == 'OFFLINE', 'fullyDelisted': code == 'DELISTED'}])
    markets(remote, [])
    row = alpha.fetch_alpha_ticker('TOK0')
    assert row['error_code'] == code
    assert row['market_data']['refresh_after'] == clock()+600
    before = len(remote.calls)
    clock.advance(599)
    assert alpha.fetch_alpha_ticker('TOK0')['market_data']['refresh_after'] == clock()+1
    assert len(remote.calls) == before
    clock.advance(1)
    remote.failures.clear()
    assert alpha.fetch_alpha_ticker('TOK0')['price_available']


def test_malformed_exchange_envelope_degrades_without_global_suspend(rig):
    _, budget, remote = rig
    remote.failures['markets'] = Response({'bad': True}, envelope=False)
    remote.failures[('ticker', 'ALPHA_0USDT')] = Response({'code': '-1121', 'success': False, 'data': None}, envelope=False)
    row = alpha.fetch_alpha_ticker('TOK0')
    assert row['price_available'] and row['price_quote_asset'] == 'USDC'
    assert budget.metrics('ALPHA')['retry_after'] == 0
    before = len(remote.calls)
    assert alpha.fetch_alpha_ticker('TOK0')['price_available']
    assert len(remote.calls) == before


def test_newer_catalog_invalidates_negative_outer_snapshot(rig, tmp_path, monkeypatch):
    clock, _, remote = rig
    remote.failures['catalog'] = Response([{'symbol': 'TOK0', 'alphaId': 'ALPHA_0', 'fullyDelisted': True}])
    row = alpha.fetch_alpha_ticker('TOK0')
    path = tmp_path/'outer.db'
    market_cache.save_market_payload({'results': [row]}, path)
    monkeypatch.setattr(market_cache.time, 'time', clock)
    assert market_cache.get_market_payload_cache(path) is not None
    remote.failures.clear()
    alpha.get_alpha_tokens(force=True)
    assert market_cache.get_market_payload_cache(path) is None


def test_unchanged_snapshot_causes_no_sqlite_update(tmp_path, monkeypatch):
    path = tmp_path/'outer.db'
    saved = market_cache.save_market_payload({'results': []}, path)
    # An UPDATE trigger detects real writes without relying on connection counters.
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE updates (n INTEGER)')
        connection.execute('CREATE TRIGGER count_updates AFTER UPDATE ON market_cache BEGIN INSERT INTO updates VALUES (1); END')
    assert market_cache.save_alpha_refresh({**saved, 'source': 'cache', 'alpha_refresh_required': True}, path)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM updates').fetchone()[0] == 0


def test_identity_preview_api_is_authenticated_and_read_only(rig):
    import main
    from fastapi.testclient import TestClient
    from tests.auth_client import AuthenticatedClient
    _, _, remote = rig
    candidates(remote, all_retired=True)
    with TestClient(main.app, base_url='http://localhost:8000') as anonymous:
        assert anonymous.get('/api/alpha/candidates?symbol=BOB').status_code == 401
    assert remote.calls == []
    with AuthenticatedClient(main.app) as client:
        choices = client.get('/api/alpha/candidates?symbol=BOB').json()
        assert len(choices['candidates']) == 2 and choices['persistence_supported'] is False
        row = client.get('/api/alpha/quote-preview', params={'symbol': 'BOB', 'alpha_id': 'ALPHA_1', 'chain_id': '1', 'contract_address': 'new'}).json()
        assert row['price_available'] and row['selection_persisted'] is False
    assert personal_data.list_positions(enrich=False) == []
    assert personal_data.list_watch_candidates(enrich=False) == []


def test_conflict_fallback_keeps_validated_currency_on_repeated_preview(rig):
    _, _, remote = rig
    remote.failures['catalog'] = Response([{'symbol': 'TOK0', 'alphaId': 'ALPHA_0', 'offline': True}])
    markets(remote, [('ALPHA_0', 'USDT', 'TRADING'), ('ALPHA_0', 'USDC', 'TRADING')])
    remote.failures[('ticker', 'ALPHA_0USDT')] = Response({'code': '-1121', 'success': False, 'data': None}, envelope=False)
    for _ in range(3):
        row = alpha.fetch_alpha_ticker('TOK0', verify_conflict=True)
        assert row['market_data']['last_known_price_quote_asset'] == 'USDC'
        assert row['current_price'] is None
    assert sum(k == 'ticker' for k, _ in remote.calls) == 2


def test_duplicate_identity_is_one_candidate(rig):
    _, _, remote = rig
    token = {'symbol': 'TOK0', 'alphaId': 'ALPHA_0', 'chainId': '56', 'contractAddress': 'same'}
    remote.failures['catalog'] = Response([token, token])
    assert len(alpha.get_alpha_candidates('TOK0')) == 1
    assert alpha.fetch_alpha_ticker('TOK0', identity=alpha.get_alpha_candidates('TOK0')[0])['price_available']


def test_cockpit_negative_state_skips_rebuild_and_write_until_catalog_changes(rig, monkeypatch):
    import main
    from services.history import DB_PATH
    clock, _, remote = rig
    monkeypatch.setattr(main, 'get_market_candidates', lambda **kwargs: [])
    monkeypatch.setattr(main, 'list_positions', lambda enrich=True: [])
    monkeypatch.setattr(main.scanner, 'scan_watchlist_multi', lambda **kwargs: [])
    monkeypatch.setattr(market_cache.time, 'time', clock)
    personal_data.create_watch_candidate({'symbol': 'TOK0'})
    clock.advance(601)
    remote.failures['catalog'] = Response([{'symbol': 'TOK0', 'alphaId': 'ALPHA_0', 'fullyDelisted': True}])
    first = main.api_market(force=True)
    assert first['results'][0]['market_data']['reason'] == 'DELISTED'
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute('CREATE TABLE writes (n INTEGER)')
        connection.execute('CREATE TRIGGER cockpit_writes AFTER UPDATE ON market_cache BEGIN INSERT INTO writes VALUES (1); END')
    original_refresh = main._refresh_cached_alpha
    refreshes = []
    def refresh(cached):
        refreshes.append(True)
        return original_refresh(cached)
    monkeypatch.setattr(main, '_refresh_cached_alpha', refresh)
    calls = len(remote.calls)
    for seconds in (5, 50, 100):
        clock.advance(seconds)
        assert main.api_market()['results'][0]['market_data']['reason'] == 'DELISTED'
    assert refreshes == [] and len(remote.calls) == calls
    with sqlite3.connect(DB_PATH) as connection:
        assert connection.execute('SELECT COUNT(*) FROM writes').fetchone()[0] == 0
    remote.failures.clear()
    alpha.get_alpha_tokens(force=True)
    refreshed = main.api_market()
    assert refreshes == [True]
    assert refreshed['results'][0]['current_price'] == 3.25


def test_different_tokens_share_service_suspension_across_threads(rig):
    _, budget, remote = rig
    alpha.fetch_alpha_symbol('TOK0')
    remote.delay = .01
    remote.failures['ticker'] = Response(status=503, headers={'Retry-After': '120'})
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(alpha.fetch_alpha_ticker, ['TOK'+str(i) for i in range(8)]))
    assert all(row['error_code'] == 'SERVICE_UNAVAILABLE' for row in rows)
    # Two requests already in flight may finish; none starts after the ban.
    assert 1 <= sum(k == 'ticker' for k, _ in remote.calls) <= 2
    assert budget.metrics('ALPHA')['retry_after'] == 120
    before = len(remote.calls)
    assert alpha.fetch_alpha_ticker('TOK9')['error_code'] == 'SERVICE_UNAVAILABLE'
    assert len(remote.calls) == before


def test_pump_then_dna_then_cnpy_with_real_catalogs(rig):
    from test_alpha_real_regressions import fixture
    _, budget, remote = rig
    remote.failures['catalog'] = Response(fixture('tokens'), envelope=False)
    remote.failures['markets'] = Response(fixture('exchange'), envelope=False)
    rows = [alpha.fetch_alpha_ticker(symbol) for symbol in ('PUMP', 'DNA', 'CNPY')]
    assert rows[0]['error_code'] == 'DELISTED'
    assert rows[1]['price_available'] and rows[2]['price_available']
    assert budget.metrics('ALPHA')['retry_after'] == 0
