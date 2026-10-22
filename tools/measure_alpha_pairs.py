"""Compare the uncommitted 3C adapter snapshot with the corrected adapter offline.

Usage: venv/bin/python tools/measure_alpha_pairs.py --before /tmp/alpha-3c-before/binance_alpha.py --output /tmp/alpha-pairs.json
Only synthetic HTTP and a temporary SQLite budget are used. No account access.
"""
import argparse
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def measure(source, directory):
    os.environ.update(PYTHON_DOTENV_DISABLED='1', CRYPTOAI_DB_PATH=str(directory/'unused.db'),
                      BINANCE_API_KEY='', BINANCE_API_SECRET='')
    for key in list(os.environ):
        if key.startswith('CRYPTOAI_API_'):
            os.environ.pop(key)
    import requests
    from services.binance_api_budget import ApiBudget
    spec = importlib.util.spec_from_file_location('measured_alpha', source)
    alpha = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(alpha)
    alpha.clear_alpha_cache()
    now = [1800000000.]
    alpha._clock = lambda: now[0]
    alpha._sleep = lambda seconds: now.__setitem__(0, now[0]+seconds)
    budget = ApiBudget(directory/(source.parent.name+'.db'), clock=alpha._clock)
    alpha.ApiBudget = lambda: budget
    calls = []
    suspensions = []
    original_suspend = budget.suspend_alpha
    def suspend(*args, **kwargs):
        suspensions.append(args[0])
        return original_suspend(*args, **kwargs)
    budget.suspend_alpha = suspend
    tokens = [('PUMP', 'ALPHA_0'), ('DNA', 'ALPHA_1'), ('XDP', 'ALPHA_2'),
              ('JCT', 'ALPHA_3'), ('BAD', 'ALPHA_4'), ('CNPY', 'ALPHA_5'), ('QUQ', 'ALPHA_6')]
    quotes = {base: ('USDC' if name == 'XDP' else 'USDT') for name, base in tokens}
    class Response:
        status_code = 200
        headers = {}
        def __init__(self, data, invalid=False):
            self.payload = {'code': '-1121' if invalid else '000000', 'success': not invalid, 'data': data}
        def json(self):
            return self.payload
    def get(url, **kwargs):
        params = kwargs.get('params') or {}
        pair = params.get('symbol')
        kind = 'tokens' if 'token/list' in url else 'markets' if 'get-exchange-info' in url else 'candles' if 'klines' in url else 'ticker'
        calls.append((kind, pair))
        if kind == 'tokens':
            return Response([{'symbol': name, 'alphaId': base} for name, base in tokens])
        if kind == 'markets':
            return Response({'symbols': [{'baseAsset': base, 'quoteAsset': quote,
                'symbol': base+quote, 'status': 'TRADING'} for base, quote in quotes.items()]})
        if kind == 'candles':
            ms = int(now[0]*1000)
            return Response([[ms-(120-i)*3600000, '2', '5', '1', '3', '100',
                              ms-(119-i)*3600000-1, '300', '5'] for i in range(120)])
        if pair in {'ALPHA_0USDT', 'ALPHA_4USDT'} or pair == 'ALPHA_2USDT':
            return Response(None, invalid=True)
        return Response({'symbol': pair, 'lastPrice': '2',
            'closeTime': (now[0]-(4920 if pair.startswith('ALPHA_6') else 0))*1000})
    requests.get = get
    rows = [alpha.get_alpha_market_data(name) for name, _ in tokens]
    return {'scenario': [name for name, _ in tokens], 'tokens': len(rows),
            'analyses_available': sum(bool(r.get('candles_available')) for r in rows),
            'calls_per_token_average': round(len(calls)/len(rows), 3),
            'prices_available': sum(bool(r.get('price_available')) for r in rows),
            'prices_unavailable': sum(not r.get('price_available') for r in rows),
            'dated_stale_prices': sum(r.get('market_data', {}).get('last_known_price') is not None
                                     and not r.get('price_available') for r in rows),
            'global_suspensions': len(suspensions), 'http_calls': len(calls),
            'ticker_calls_by_pair': dict(Counter(pair for kind, pair in calls if kind == 'ticker')),
            'local_quota_consumption': budget.metrics('ALPHA')['weight'],
            'market_pairs_discovered': len(quotes) if any(k == 'markets' for k, _ in calls) else 0,
            'absent_usdt_requests_avoided': int(('ticker', 'ALPHA_2USDT') not in calls
                                              and any(k == 'markets' for k, _ in calls))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    def deny(*args, **kwargs):
        raise AssertionError('Real network forbidden')
    socket.getaddrinfo = deny
    socket.socket.connect = deny
    with tempfile.TemporaryDirectory(prefix='alpha-pair-measures-') as temp:
        directory = Path(temp)
        result = {'type': 'SIMULATED_HTTP', 'baseline': 'uncommitted 3C snapshot before corrections',
                  'before': measure(args.before, directory),
                  'after': measure(ROOT/'services/binance_alpha.py', directory)}
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
