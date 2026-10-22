"""HTTP/envelope regressions using real public shapes and isolated budgets."""
import json
from pathlib import Path

import pytest
import requests

from test_alpha_hardening import rig, Response
from services import binance_alpha as alpha
from services.market_data_errors import MarketDataError

FIXTURES = Path(__file__).parent / 'fixtures' / 'alpha'


def fixture(name):
    return json.loads((FIXTURES / (name+'.json')).read_text())


def markets(remote, rows):
    remote.failures['markets'] = Response({'symbols': [
        {'baseAsset': base, 'quoteAsset': quote, 'symbol': base+quote, 'status': status}
        for base, quote, status in rows]})


@pytest.mark.parametrize('code', [-1121, '-1121'])
def test_invalid_then_valid_never_suspends(rig, code):
    _, budget, remote = rig
    remote.failures[('ticker', 'ALPHA_0USDT')] = Response({'code': code, 'success': False, 'data': None}, envelope=False)
    assert alpha.fetch_alpha_ticker('TOK0')['error_code'] == 'NO_MARKET'
    assert alpha.fetch_alpha_ticker('TOK1')['price_available']
    assert budget.metrics('ALPHA')['retry_after'] == 0


def test_interleaved_invalid_tokens_and_repeated_refresh(rig):
    _, budget, remote = rig
    for i in (0, 2, 4):
        remote.failures[('ticker', f'ALPHA_{i}USDT')] = Response(fixture('invalid_pair'), envelope=False)
    for cycle in range(3):
        for i in range(6):
            row = alpha.fetch_alpha_ticker('TOK'+str(i))
            assert row['price_available'] == bool(i % 2)
    assert budget.metrics('ALPHA')['retry_after'] == 0
    assert sum(kind == 'ticker' for kind, _ in remote.calls) == 6


@pytest.mark.parametrize('first,second', [('USDT', 'USDC'), ('USDC', 'USDT')])
def test_invalid_pair_and_other_quote_valid(rig, first, second):
    _, budget, remote = rig
    markets(remote, [('ALPHA_0', first, 'TRADING'), ('ALPHA_0', second, 'TRADING')])
    # Sorting normally prefers USDT; exercise reverse order explicitly too.
    remote.failures[('ticker', 'ALPHA_0'+first)] = Response(fixture('invalid_pair'), envelope=False)
    resolved = alpha.fetch_alpha_symbol('TOK0')
    from unittest.mock import patch
    with patch.object(alpha, '_pair_candidates', return_value=['ALPHA_0'+first, 'ALPHA_0'+second]):
        assert alpha._select_ticker(resolved).value['current_price'] == 3.25
    assert resolved['price_quote_asset'] == second
    assert budget.metrics('ALPHA')['retry_after'] == 0


def test_catalog_usdc_only_avoids_usdt_and_next_token_works(rig):
    _, _, remote = rig
    markets(remote, [('ALPHA_0', 'USDC', 'TRADING'), ('ALPHA_1', 'USDT', 'TRADING')])
    row = alpha.get_alpha_market_data('TOK0')
    assert row['price_quote_asset'] == 'USDC' and row['candles_available']
    assert ('ticker', 'ALPHA_0USDT') not in remote.calls
    assert alpha.fetch_alpha_ticker('TOK1')['price_available']


def test_empty_or_nontrading_catalog_no_ticker(rig):
    _, _, remote = rig
    markets(remote, [('ALPHA_0', 'USDT', 'DELISTED')])
    for _ in range(3):
        assert alpha.fetch_alpha_ticker('TOK0')['error_code'] == 'NO_MARKET'
    assert not any(k == 'ticker' for k, _ in remote.calls)


@pytest.mark.parametrize('field,code', [('offline', 'OFFLINE'), ('fullyDelisted', 'DELISTED')])
def test_status_and_reactivation(rig, field, code):
    clock, _, remote = rig
    assert alpha.fetch_alpha_ticker('TOK0')['price_available']
    clock.advance(601)
    remote.failures['catalog'] = Response([{'symbol': 'TOK0', 'alphaId': 'ALPHA_0', field: True}])
    if field == 'offline':
        markets(remote, [('ALPHA_0', 'USDT', 'DELISTED')])
    before = len(remote.calls)
    assert alpha.get_alpha_market_data('TOK0')['error_code'] == code
    assert not any(kind in {'ticker', 'candles'} for kind, _ in remote.calls[before:])
    clock.advance(601)
    remote.failures.clear()
    assert alpha.get_alpha_market_data('TOK0')['price_available']


@pytest.mark.parametrize('payload', [
    {'code': '000000', 'success': False, 'data': {}},
    {'code': '000000', 'success': True, 'data': None},
    [], {}, {'success': True, 'data': {}},
])
def test_bad_http200_envelope_never_means_absent(rig, payload):
    _, _, remote = rig
    remote.failures['ticker'] = Response(payload, envelope=False)
    row = alpha.fetch_alpha_ticker('TOK0')
    assert row['valid'] is True and row['current_price'] is None
    assert row['error_code'] == 'INVALID_RESPONSE'


@pytest.mark.parametrize('failure,code', [(Response(status=500), 'SERVICE_UNAVAILABLE'),
    (Response(status=503), 'SERVICE_UNAVAILABLE'), (requests.Timeout(), 'NETWORK_ERROR'),
    (requests.ConnectionError(), 'NETWORK_ERROR'), (Response(status=429), 'QUOTA'),
    (Response(status=418), 'QUOTA')])
def test_service_failure_still_suspends(rig, failure, code):
    _, budget, remote = rig
    remote.failures['ticker'] = failure
    assert alpha.fetch_alpha_ticker('TOK0')['error_code'] == code
    assert budget.metrics('ALPHA')['retry_after'] > 0


def test_stale_market_catalog_bounded_fallback(rig):
    clock, _, remote = rig
    alpha.fetch_alpha_ticker('TOK0')
    clock.advance(601)
    remote.failures['markets'] = Response({'symbols': 'malformed'})
    remote.failures[('ticker', 'ALPHA_0USDT')] = Response(fixture('invalid_pair'), envelope=False)
    row = alpha.fetch_alpha_ticker('TOK0')
    assert row['price_available'] and row['price_quote_asset'] == 'USDC'
    assert remote.calls.count(('ticker', 'ALPHA_0USDC')) == 1


def test_82_minute_trade_is_dated_and_not_financial_input(rig):
    clock, _, remote = rig
    remote.failures['ticker'] = Response({'lastPrice': '2', 'closeTime': (clock()-82*60)*1000})
    row = alpha.get_alpha_market_data('TOK0')
    assert row['current_price'] is None and not row['analysis_available']
    state = row['market_data']
    assert state['last_known_price'] == 2 and state['quote_age_seconds'] == 4920
    assert state['last_trade_age_seconds'] is None
    assert state['response_age_seconds'] == 0 and state['cache_age_seconds'] == 0


def test_real_catalog_and_responses(rig):
    clock, budget, remote = rig
    remote.failures['catalog'] = Response(fixture('tokens'), envelope=False)
    remote.failures['markets'] = Response(fixture('exchange'), envelope=False)
    remote.failures[('ticker', 'ALPHA_1247USDT')] = Response(fixture('dna'), envelope=False)
    remote.failures[('ticker', 'ALPHA_1214USDC')] = Response(fixture('xdp'), envelope=False)
    clock.now = fixture('dna')['data']['closeTime']/1000 + 1
    assert alpha.fetch_alpha_ticker('PUMP')['error_code'] == 'DELISTED'
    assert alpha.fetch_alpha_ticker('DNA')['price_available']
    assert alpha.fetch_alpha_ticker('XDP')['price_quote_asset'] == 'USDC'
    assert ('ticker', 'ALPHA_276USDT') not in remote.calls
    assert budget.metrics('ALPHA')['retry_after'] == 0


def test_ambiguous_identity_never_selects_arbitrary_contract(rig):
    _, _, remote = rig
    remote.failures['catalog'] = Response([
        {'symbol': 'AIA', 'alphaId': 'ALPHA_1', 'chainId': '1', 'contractAddress': 'a'},
        {'symbol': 'AIA', 'alphaId': 'ALPHA_1', 'chainId': '1', 'contractAddress': 'b'}])
    with pytest.raises(MarketDataError, match='ambigu'):
        alpha.fetch_alpha_symbol('AIA')
    assert remote.calls == [('catalog', None)]


def test_missing_markets_bounded_fallback_and_negative_expiry(rig):
    clock, _, remote = rig
    remote.failures['markets'] = Response({'symbols': None})
    for quote in ('USDT', 'USDC'):
        remote.failures[('ticker', 'ALPHA_0'+quote)] = Response(fixture('invalid_pair'), envelope=False)
    for _ in range(3):
        assert alpha.fetch_alpha_ticker('TOK0')['error_code'] == 'NO_MARKET'
    assert sum(k == 'ticker' for k, _ in remote.calls) == 2
    clock.advance(601)
    remote.failures.clear()
    assert alpha.fetch_alpha_ticker('TOK0')['price_available']


def test_unknown_token_status_is_not_delisting(rig):
    _, _, remote = rig
    remote.failures['catalog'] = Response([{'symbol': 'TOK0', 'alphaId': 'ALPHA_0',
                                          'offline': 'true', 'fullyDelisted': None}])
    assert alpha.fetch_alpha_ticker('TOK0')['price_available']


def test_validated_pair_invalidation_drops_old_price(rig):
    clock, _, remote = rig
    markets(remote, [('ALPHA_0', 'USDT', 'TRADING'), ('ALPHA_0', 'USDC', 'TRADING')])
    assert alpha.fetch_alpha_ticker('TOK0')['price_quote_asset'] == 'USDT'
    clock.advance(601)
    remote.failures[('ticker', 'ALPHA_0USDT')] = Response(fixture('invalid_pair'), envelope=False)
    result = alpha.fetch_alpha_ticker('TOK0')
    assert result['price_available'] and result['price_quote_asset'] == 'USDC'
    before = len(remote.calls)
    assert alpha.fetch_alpha_ticker('TOK0')['price_quote_asset'] == 'USDC'
    assert len(remote.calls) == before


def test_http400_invalid_pair_is_also_local(rig):
    _, budget, remote = rig
    remote.failures[('ticker', 'ALPHA_0USDT')] = Response(fixture('invalid_pair'), status=400, envelope=False)
    assert alpha.fetch_alpha_ticker('TOK0')['error_code'] == 'NO_MARKET'
    assert alpha.fetch_alpha_ticker('TOK1')['price_available']
    assert budget.metrics('ALPHA')['retry_after'] == 0
