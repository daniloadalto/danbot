import time
import math
from pathlib import Path
import importlib.util

BASE = Path('/home/user/danbot_repo')

# Load modules directly from repo
spec_iq = importlib.util.spec_from_file_location('iq_integration', BASE / 'iq_integration.py')
iq = importlib.util.module_from_spec(spec_iq)
spec_iq.loader.exec_module(iq)

spec_bridge = importlib.util.spec_from_file_location('candle_catalog_bridge', BASE / 'candle_catalog_bridge.py')
bridge = importlib.util.module_from_spec(spec_bridge)
spec_bridge.loader.exec_module(bridge)


def build_downtrend_with_live_tail(count=60, timeframe=60, live_age_s=1.0):
    now = time.time()
    current_open = now - float(live_age_s)
    start = current_open - ((count - 1) * timeframe)
    opens = []
    closes = []
    highs = []
    lows = []
    timestamps = []
    price = 1.3000
    for i in range(count - 2):
        o = price
        c = price - 0.0012
        h = max(o, c) + 0.00015
        l = min(o, c) - 0.00015
        opens.append(o); closes.append(c); highs.append(h); lows.append(l)
        timestamps.append(start + i * timeframe)
        price = c
    # candle de padrão fechado (forte baixa)
    o = price
    c = price - 0.0028
    h = o + 0.00012
    l = c - 0.00012
    opens.append(o); closes.append(c); highs.append(h); lows.append(l)
    timestamps.append(current_open - timeframe)
    # candle em formação (deve ser ignorado pela análise)
    o = c
    c2 = c - 0.0002
    h = max(o, c2) + 0.00008
    l = min(o, c2) - 0.00008
    opens.append(o); closes.append(c2); highs.append(h); lows.append(l)
    timestamps.append(current_open)
    return {
        'opens': iq.np.array(opens, dtype=float),
        'closes': iq.np.array(closes, dtype=float),
        'highs': iq.np.array(highs, dtype=float),
        'lows': iq.np.array(lows, dtype=float),
        'volumes': iq.np.ones(count, dtype=float) * 500.0,
        'timestamps': iq.np.array(timestamps, dtype=float),
    }


def test_all_catalog_patterns_do_not_break():
    patterns = bridge.get_candle_pattern_catalog()
    assert patterns, 'catalog empty'
    ohlc = build_downtrend_with_live_tail(live_age_s=1.0)
    slugs = [p['slug'] for p in patterns]
    # structural validation: every pattern slug can be analyzed individually without exception
    for slug in slugs:
        out = iq.analyze_asset_full(
            'TEST-OTC',
            ohlc,
            strategies={'ma': False, 'rsi': False, 'bb': False, 'macd': False, 'simple_trend': False, 'pullback_m5': False, 'pullback_m15': False, 'dead': False, 'reverse': False, 'i3wr': False},
            min_confluence=1,
            dc_mode='disabled',
            base_timeframe=60,
            selected_candle_patterns=[slug],
        )
        assert out is None or isinstance(out, dict), f'pattern failed: {slug}'
    print('CATALOG_REVIEW_OK', len(slugs))


def test_timing_uses_closed_candle_and_current_open():
    ohlc = build_downtrend_with_live_tail(live_age_s=1.0)
    result = iq.analyze_asset_full(
        'TEST-OTC',
        ohlc,
        strategies={'ma': False, 'rsi': False, 'bb': False, 'macd': False, 'simple_trend': False, 'pullback_m5': False, 'pullback_m15': False, 'dead': False, 'reverse': False, 'i3wr': False},
        min_confluence=1,
        dc_mode='disabled',
        base_timeframe=60,
        selected_candle_patterns=['seq_p02'],
    )
    assert result is not None, 'expected selected pattern signal'
    timing = (result.get('detail', {}) or {}).get('timing', {})
    assert timing.get('source') == 'trimmed_live_candle', timing
    entry_open_ts = float(timing.get('entry_open_ts', 0) or 0)
    delay = time.time() - entry_open_ts
    assert -1.0 <= delay <= 2.5, f'entry delay outside expected window: {delay}'
    print('TIMING_REVIEW_OK', round(delay, 3), timing.get('source'))


def test_buy_executes_immediately_inside_grace_window():
    calls = []
    class DummyIQ: ...
    orig_get_iq = iq.get_iq
    orig_is_open = iq.is_binary_open
    orig_resolve = iq.resolve_asset_name
    orig_switch = iq._switch_account_type
    orig_exec = iq._execute_binary_buy
    try:
        iq.get_iq = lambda *args, **kwargs: DummyIQ()
        iq.is_binary_open = lambda *args, **kwargs: True
        iq.resolve_asset_name = lambda asset: asset
        iq._switch_account_type = lambda *args, **kwargs: None
        def fake_exec(iq_obj, asset, amount, direction, expiry, account_type='PRACTICE', progress_cb=None):
            calls.append((asset, amount, direction, expiry, account_type))
            return True, 'ORDER123'
        iq._execute_binary_buy = fake_exec
        ok, oid = iq.buy_binary_next_candle(
            'TEST-OTC', 2.0, 'put', expiry=1, account_type='PRACTICE',
            candle_timeframe=60, target_entry_ts=time.time() - 1.0, late_grace_seconds=2.2
        )
        assert ok and oid == 'ORDER123', (ok, oid)
        assert calls, 'buy not executed'
        print('ENTRY_GRACE_OK', len(calls))
    finally:
        iq.get_iq = orig_get_iq
        iq.is_binary_open = orig_is_open
        iq.resolve_asset_name = orig_resolve
        iq._switch_account_type = orig_switch
        iq._execute_binary_buy = orig_exec


if __name__ == '__main__':
    test_all_catalog_patterns_do_not_break()
    test_timing_uses_closed_candle_and_current_open()
    test_buy_executes_immediately_inside_grace_window()
