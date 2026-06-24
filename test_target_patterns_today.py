import time
import math
import importlib.util
from pathlib import Path

BASE = Path('/home/user/danbot_repo')

spec_iq = importlib.util.spec_from_file_location('iq_integration', BASE / 'iq_integration.py')
iq = importlib.util.module_from_spec(spec_iq)
spec_iq.loader.exec_module(iq)

PATTERN_ONLY = {
    'i3wr': False,
    'ma': False,
    'rsi': False,
    'bb': False,
    'macd': False,
    'simple_trend': False,
    'pullback_m5': False,
    'pullback_m15': False,
    'dead': False,
    'reverse': False,
}

WITH_RSI = dict(PATTERN_ONLY, rsi=True)
WITH_MA = dict(PATTERN_ONLY, ma=True)


def ts_series(count, timeframe=60, live_age_s=1.0):
    now = time.time()
    current_open = now - float(live_age_s)
    start = current_open - ((count - 1) * timeframe)
    return [start + (i * timeframe) for i in range(count)]


def tail_live_from(prev_close):
    o = prev_close
    c = prev_close + 0.00005
    h = max(o, c) + 0.00004
    l = min(o, c) - 0.00004
    return o, h, l, c


def make_base_down(count=58, start=1.3000, step=0.0012):
    opens=[]; highs=[]; lows=[]; closes=[]
    price=start
    for _ in range(count):
        o = price
        c = price - step
        h = max(o, c) + step * 0.15
        l = min(o, c) - step * 0.15
        opens.append(o); highs.append(h); lows.append(l); closes.append(c)
        price = c
    return opens, highs, lows, closes


def make_base_up(count=58, start=1.1000, step=0.0012):
    opens=[]; highs=[]; lows=[]; closes=[]
    price=start
    for _ in range(count):
        o = price
        c = price + step
        h = max(o, c) + step * 0.15
        l = min(o, c) - step * 0.15
        opens.append(o); highs.append(h); lows.append(l); closes.append(c)
        price = c
    return opens, highs, lows, closes


def build_pattern_case(kind):
    if kind == 'martelo':
        opens, highs, lows, closes = make_base_down()
        prev = closes[-1]
        o = prev - 0.00015
        c = prev - 0.00005
        h = max(o, c) + 0.00003
        l = min(o, c) - 0.00080
    elif kind == 'enforcado':
        opens, highs, lows, closes = make_base_up()
        prev = closes[-1]
        o = prev + 0.00015
        c = prev + 0.00005
        h = max(o, c) + 0.00003
        l = min(o, c) - 0.00080
    elif kind == 'estrela_cadente':
        opens, highs, lows, closes = make_base_up()
        prev = closes[-1]
        o = prev + 0.00005
        c = prev + 0.00012
        h = max(o, c) + 0.00085
        l = min(o, c) - 0.00003
    elif kind == 'engolfo_alta':
        opens, highs, lows, closes = make_base_down(count=57)
        prev = closes[-1]
        # vela anterior vermelha pequena
        o1 = prev + 0.00025
        c1 = prev - 0.00010
        h1 = o1 + 0.00005
        l1 = c1 - 0.00005
        opens.append(o1); highs.append(h1); lows.append(l1); closes.append(c1)
        # vela atual verde engolfando corpo anterior
        o2 = c1 - 0.00010
        c2 = o1 + 0.00025
        h2 = c2 + 0.00005
        l2 = o2 - 0.00005
        o, h, l, c = o2, h2, l2, c2
    elif kind == 'engolfo_baixa':
        opens, highs, lows, closes = make_base_up(count=57)
        prev = closes[-1]
        # vela anterior verde pequena
        o1 = prev - 0.00025
        c1 = prev + 0.00010
        h1 = c1 + 0.00005
        l1 = o1 - 0.00005
        opens.append(o1); highs.append(h1); lows.append(l1); closes.append(c1)
        # vela atual vermelha engolfando corpo anterior
        o2 = c1 + 0.00010
        c2 = o1 - 0.00025
        h2 = o2 + 0.00005
        l2 = c2 - 0.00005
        o, h, l, c = o2, h2, l2, c2
    elif kind == 'seq_p01':
        opens, highs, lows, closes = make_base_up(count=55)
        price = closes[-1]
        for _ in 'GGGG':
            o1 = price
            c1 = price + 0.00045
            h1 = c1 + 0.00008
            l1 = o1 - 0.00004
            opens.append(o1); highs.append(h1); lows.append(l1); closes.append(c1)
            price = c1
        o, h, l, c = tail_live_from(price)
        ts = ts_series(len(opens) + 1)
        opens.append(o); highs.append(h); lows.append(l); closes.append(c)
        return {
            'opens': iq.np.array(opens, dtype=float),
            'highs': iq.np.array(highs, dtype=float),
            'lows': iq.np.array(lows, dtype=float),
            'closes': iq.np.array(closes, dtype=float),
            'volumes': iq.np.ones(len(opens), dtype=float) * 500.0,
            'timestamps': iq.np.array(ts, dtype=float),
        }
    elif kind == 'seq_p02':
        opens, highs, lows, closes = make_base_down(count=55)
        price = closes[-1]
        for _ in 'RRRR':
            o1 = price
            c1 = price - 0.00045
            h1 = o1 + 0.00004
            l1 = c1 - 0.00008
            opens.append(o1); highs.append(h1); lows.append(l1); closes.append(c1)
            price = c1
        o, h, l, c = tail_live_from(price)
        ts = ts_series(len(opens) + 1)
        opens.append(o); highs.append(h); lows.append(l); closes.append(c)
        return {
            'opens': iq.np.array(opens, dtype=float),
            'highs': iq.np.array(highs, dtype=float),
            'lows': iq.np.array(lows, dtype=float),
            'closes': iq.np.array(closes, dtype=float),
            'volumes': iq.np.ones(len(opens), dtype=float) * 500.0,
            'timestamps': iq.np.array(ts, dtype=float),
        }
    else:
        raise ValueError(kind)

    opens.append(o); highs.append(h); lows.append(l); closes.append(c)
    live_o, live_h, live_l, live_c = tail_live_from(closes[-1])
    opens.append(live_o); highs.append(live_h); lows.append(live_l); closes.append(live_c)
    ts = ts_series(len(opens))
    return {
        'opens': iq.np.array(opens, dtype=float),
        'highs': iq.np.array(highs, dtype=float),
        'lows': iq.np.array(lows, dtype=float),
        'closes': iq.np.array(closes, dtype=float),
        'volumes': iq.np.ones(len(opens), dtype=float) * 500.0,
        'timestamps': iq.np.array(ts, dtype=float),
    }


def run_case(label, slug, strategies, expected_dir):
    ohlc = build_pattern_case(label)
    result = iq.analyze_asset_full(
        'TEST-OTC',
        ohlc,
        strategies=strategies,
        min_confluence=1,
        dc_mode='disabled',
        base_timeframe=60,
        selected_candle_patterns=[slug],
    )
    assert result is not None, f'{label}: sem sinal'
    assert result.get('direction') == expected_dir, f'{label}: direção {result.get("direction")} != {expected_dir}'
    timing = (result.get('detail', {}) or {}).get('timing', {})
    assert timing.get('source') == 'trimmed_live_candle', f'{label}: timing source {timing}'
    delta = time.time() - float(timing.get('entry_open_ts', 0) or 0)
    assert -1.0 <= delta <= 2.5, f'{label}: delta fora da janela {delta}'
    return {
        'pattern': label,
        'slug': slug,
        'direction': result.get('direction'),
        'strength': result.get('strength'),
        'pattern_text': result.get('pattern'),
        'reason': result.get('reason'),
        'timing_delta_s': round(delta, 3),
        'confirmed_modules': ((result.get('detail', {}) or {}).get('entry_guard', {}) or {}).get('confirmed_modules', []),
        'mode': ((result.get('detail', {}) or {}).get('entry_guard', {}) or {}).get('mode'),
    }


def main():
    focused = [
        ('martelo', 'cndl_martelo', PATTERN_ONLY, 'CALL'),
        ('enforcado', 'cndl_enforcado', PATTERN_ONLY, 'PUT'),
        ('estrela_cadente', 'cndl_estrela_cadente', PATTERN_ONLY, 'PUT'),
        ('engolfo_alta', 'cndl_engolfo_alta', PATTERN_ONLY, 'CALL'),
        ('engolfo_baixa', 'cndl_engolfo_baixa', PATTERN_ONLY, 'PUT'),
        ('seq_p01', 'seq_p01', PATTERN_ONLY, 'CALL'),
        ('seq_p02', 'seq_p02', PATTERN_ONLY, 'PUT'),
    ]
    extra_conf = [
        ('martelo', 'cndl_martelo', WITH_RSI, 'CALL'),
        ('estrela_cadente', 'cndl_estrela_cadente', WITH_RSI, 'PUT'),
        ('seq_p01', 'seq_p01', WITH_MA, 'CALL'),
        ('seq_p02', 'seq_p02', WITH_MA, 'PUT'),
    ]
    rows = []
    for case in focused + extra_conf:
        rows.append(run_case(*case))
    print('FOCUSED_PATTERN_REVIEW_OK')
    for row in rows:
        mods = ','.join(row['confirmed_modules']) if row['confirmed_modules'] else '-'
        print(f"{row['pattern']} | {row['slug']} | {row['direction']} | {row['strength']} | mode={row['mode']} | mods={mods} | dt={row['timing_delta_s']}s")
        print(f"  reason: {row['reason'][:160]}")


if __name__ == '__main__':
    main()
