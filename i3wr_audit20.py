#!/usr/bin/env python3
import csv
import json
import time
from pathlib import Path

import numpy as np
import iq_integration as IQ
import app as danapp


OUT_DIR = Path('/home/user/danbot_repo/tmp_outputs/i3wr_audit20')
OUT_DIR.mkdir(parents=True, exist_ok=True)


class FakeIQ:
    def __init__(self, candles_seq):
        self.candles_seq = list(candles_seq)
        self.idx = 0
        self.balance_type = None
        self.buy_calls = []
        self.stream_started = False

    def get_all_open_time(self):
        return {'binary': {'EURUSD': {'open': True}}, 'turbo': {'EURUSD': {'open': True}}}

    def change_balance(self, account_type):
        self.balance_type = account_type

    def start_candles_stream(self, asset, size, maxdict):
        self.stream_started = True
        return True

    def stop_candles_stream(self, asset, size):
        self.stream_started = False
        return True

    def get_realtime_candles(self, asset, size):
        candle = self.candles_seq[min(self.idx, len(self.candles_seq) - 1)]
        self.idx += 1
        return {int(candle['from']): candle}

    def get_candles(self, asset, size, count, now_ts):
        candle = self.candles_seq[min(max(self.idx - 1, 0), len(self.candles_seq) - 1)]
        return [candle]

    def buy(self, amount, asset, direction, expiry):
        self.buy_calls.append((amount, asset, direction, expiry))
        return True, f'order-{len(self.buy_calls)}'


def _call_case(case_id: int, leg_len: int, trigger_ord: int):
    base = 1.0800 + case_id * 0.0037
    opens, highs, lows, closes = [], [], [], []
    p = base
    leg_delta = 0.00075 + (case_id % 3) * 0.00008
    for i in range(leg_len):
        o = p
        c = p + leg_delta + i * 0.00004
        h = c + 0.00022
        l = o - 0.00008
        opens.append(round(o, 6)); highs.append(round(h, 6)); lows.append(round(l, 6)); closes.append(round(c, 6))
        p = c

    wick_map = {
        1: [0.00090, 0.00056, 0.00048],
        2: [0.00050, 0.00096, 0.00060],
        3: [0.00046, 0.00058, 0.00102],
    }
    wick_sizes = wick_map[trigger_ord]
    body = 0.00018 + (case_id % 2) * 0.00003
    rej_base = closes[-1]
    for j in range(3):
        o = rej_base - j * 0.00003
        c = o - body
        h = max(o, c) + 0.00012
        l = min(o, c) - wick_sizes[j]
        opens.append(round(o, 6)); highs.append(round(h, 6)); lows.append(round(l, 6)); closes.append(round(c, 6))

    trigger_idx = leg_len + trigger_ord - 1
    return np.array(opens, dtype=float), np.array(highs, dtype=float), np.array(lows, dtype=float), np.array(closes, dtype=float), round(float(lows[trigger_idx]), 6)


def _put_case(case_id: int, leg_len: int, trigger_ord: int):
    base = 1.2200 + case_id * 0.0041
    opens, highs, lows, closes = [], [], [], []
    p = base
    leg_delta = 0.00078 + (case_id % 3) * 0.00007
    for i in range(leg_len):
        o = p
        c = p - leg_delta - i * 0.00005
        h = o + 0.00008
        l = c - 0.00022
        opens.append(round(o, 6)); highs.append(round(h, 6)); lows.append(round(l, 6)); closes.append(round(c, 6))
        p = c

    wick_map = {
        1: [0.00092, 0.00054, 0.00046],
        2: [0.00052, 0.00098, 0.00058],
        3: [0.00048, 0.00060, 0.00105],
    }
    wick_sizes = wick_map[trigger_ord]
    body = 0.00019 + (case_id % 2) * 0.00002
    rej_base = closes[-1]
    for j in range(3):
        o = rej_base + j * 0.00003
        c = o + body
        h = max(o, c) + wick_sizes[j]
        l = min(o, c) - 0.00012
        opens.append(round(o, 6)); highs.append(round(h, 6)); lows.append(round(l, 6)); closes.append(round(c, 6))

    trigger_idx = leg_len + trigger_ord - 1
    return np.array(opens, dtype=float), np.array(highs, dtype=float), np.array(lows, dtype=float), np.array(closes, dtype=float), round(float(highs[trigger_idx]), 6)


def _touch_sequence(direction: str, trigger_price: float):
    if direction == 'CALL':
        return [
            {'from': 100, 'open': trigger_price + 0.00040, 'close': trigger_price + 0.00028, 'low': trigger_price - 0.00002, 'high': trigger_price + 0.00052},
            {'from': 100, 'open': trigger_price + 0.00035, 'close': trigger_price + 0.00018, 'low': trigger_price - 0.00001, 'high': trigger_price + 0.00044},
        ]
    return [
        {'from': 200, 'open': trigger_price - 0.00040, 'close': trigger_price - 0.00026, 'low': trigger_price - 0.00052, 'high': trigger_price + 0.00002},
        {'from': 200, 'open': trigger_price - 0.00036, 'close': trigger_price - 0.00014, 'low': trigger_price - 0.00045, 'high': trigger_price + 0.00001},
    ]


def _no_touch_sequence(direction: str, trigger_price: float):
    if direction == 'CALL':
        return [
            {'from': 300, 'open': trigger_price + 0.00050, 'close': trigger_price + 0.00042, 'low': trigger_price + 0.00020, 'high': trigger_price + 0.00058},
            {'from': 300, 'open': trigger_price + 0.00046, 'close': trigger_price + 0.00038, 'low': trigger_price + 0.00021, 'high': trigger_price + 0.00054},
        ]
    return [
        {'from': 400, 'open': trigger_price - 0.00052, 'close': trigger_price - 0.00046, 'low': trigger_price - 0.00064, 'high': trigger_price - 0.00020},
        {'from': 400, 'open': trigger_price - 0.00048, 'close': trigger_price - 0.00040, 'low': trigger_price - 0.00060, 'high': trigger_price - 0.00022},
    ]


def _run_touch_execution(case_name: str, direction: str, trigger_price: float, account_type: str = 'PRACTICE'):
    fake = FakeIQ(_touch_sequence(direction, trigger_price))
    IQ._iq_instances[case_name] = fake
    IQ.set_user_context(case_name)
    original = IQ.seconds_to_next_candle
    IQ.seconds_to_next_candle = lambda timeframe=60: 1.1
    try:
        ok, order_id = IQ.buy_binary_retracement_touch('EURUSD', 1.0, direction.lower(), trigger_price, account_type=account_type)
    finally:
        IQ.seconds_to_next_candle = original
        IQ._iq_instances.pop(case_name, None)
    return ok, order_id, fake


def _run_no_touch_control(case_name: str, direction: str, trigger_price: float):
    fake = FakeIQ(_no_touch_sequence(direction, trigger_price))
    IQ._iq_instances[case_name] = fake
    IQ.set_user_context(case_name)
    original = IQ.seconds_to_next_candle
    IQ.seconds_to_next_candle = lambda timeframe=60: 0.8
    try:
        ok, reason = IQ.buy_binary_retracement_touch('EURUSD', 1.0, direction.lower(), trigger_price, account_type='PRACTICE')
    finally:
        IQ.seconds_to_next_candle = original
        IQ._iq_instances.pop(case_name, None)
    return ok, reason, fake


def _make_auto_candidates(direction: str, trigger_price: float, lp_force: int, case_id: int):
    i3wr_signal = {
        'asset': f'I3WR-{case_id}',
        'direction': direction,
        'strength': 86,
        'score_call': 14 if direction == 'CALL' else 2,
        'score_put': 14 if direction == 'PUT' else 2,
        'lp_entry_mode': 'wick_touch_retracement',
        'lp_trigger_price': trigger_price,
        'lp_direcao': direction,
        'lp_pode_entrar': True,
        'lp_forca': lp_force,
    }
    plain_signal = {
        'asset': f'PLAIN-{case_id}',
        'direction': direction,
        'strength': 88,
        'score_call': 16 if direction == 'CALL' else 3,
        'score_put': 16 if direction == 'PUT' else 3,
        'lp_entry_mode': None,
        'lp_trigger_price': None,
        'lp_direcao': None,
        'lp_pode_entrar': True,
        'lp_forca': 0,
    }
    ranked = danapp._sort_signal_candidates([plain_signal, i3wr_signal])
    return ranked


def main():
    started_at = time.strftime('%Y-%m-%d %H:%M:%S')
    rows = []

    for idx in range(20):
        direction = 'CALL' if idx % 2 == 0 else 'PUT'
        leg_len = 3 + (idx % 3)
        trigger_ord = 1 + (idx % 3)
        if direction == 'CALL':
            opens, highs, lows, closes, expected_trigger = _call_case(idx + 1, leg_len, trigger_ord)
        else:
            opens, highs, lows, closes, expected_trigger = _put_case(idx + 1, leg_len, trigger_ord)

        detector = IQ.analisar_impulso_3wicks(opens, highs, lows, closes, 'EURUSD')
        detect_ok = (
            detector.get('direcao') == direction and
            detector.get('entry_mode') == 'wick_touch_retracement' and
            round(float(detector.get('trigger_price') or 0.0), 6) == expected_trigger and
            int(detector.get('trigger_candle_ordinal') or 0) == trigger_ord and
            f'{leg_len} velas' in ' '.join(detector.get('sinais', []) or [])
        )

        exec_ok, order_id, fake = _run_touch_execution(f'i3wr_case_{idx+1}', direction, expected_trigger)
        route_ok = bool(exec_ok) and len(fake.buy_calls) == 1 and fake.balance_type == 'PRACTICE'

        ranked = _make_auto_candidates(direction, expected_trigger, int(detector.get('forca_lp') or 0), idx + 1)
        auto_ok = bool(ranked) and ranked[0]['asset'] == f'I3WR-{idx+1}' and danapp._signal_has_i3wr_touch(ranked[0])

        rows.append({
            'case': idx + 1,
            'direction': direction,
            'leg_len': leg_len,
            'trigger_ordinal': trigger_ord,
            'expected_trigger': expected_trigger,
            'detected_direction': detector.get('direcao'),
            'detected_entry_mode': detector.get('entry_mode'),
            'detected_trigger': round(float(detector.get('trigger_price') or 0.0), 6),
            'detected_trigger_label': detector.get('trigger_label'),
            'detected_strength': int(detector.get('forca_lp') or 0),
            'detector_ok': detect_ok,
            'execution_ok': bool(exec_ok),
            'routing_ok': route_ok,
            'auto_priority_ok': auto_ok,
            'auto_top_asset': ranked[0]['asset'] if ranked else '',
            'order_id': order_id,
            'signals': ' | '.join(detector.get('sinais', []) or []),
            'summary': detector.get('resumo'),
        })

    control_call = _run_no_touch_control('i3wr_control_call', 'CALL', 1.10110)
    control_put = _run_no_touch_control('i3wr_control_put', 'PUT', 1.20340)
    controls = {
        'call_no_touch_cancelled': (control_call[0] is False and len(control_call[2].buy_calls) == 0 and 'não tocou' in str(control_call[1])),
        'put_no_touch_cancelled': (control_put[0] is False and len(control_put[2].buy_calls) == 0 and 'não tocou' in str(control_put[1])),
        'call_reason': str(control_call[1]),
        'put_reason': str(control_put[1]),
    }

    summary = {
        'started_at': started_at,
        'finished_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'audit_name': 'i3wr_audit20',
        'target_entries': 20,
        'detected_ok': sum(1 for r in rows if r['detector_ok']),
        'executed_ok': sum(1 for r in rows if r['execution_ok']),
        'routing_ok': sum(1 for r in rows if r['routing_ok']),
        'auto_priority_ok': sum(1 for r in rows if r['auto_priority_ok']),
        'call_cases': sum(1 for r in rows if r['direction'] == 'CALL'),
        'put_cases': sum(1 for r in rows if r['direction'] == 'PUT'),
        'avg_strength': round(sum(r['detected_strength'] for r in rows) / len(rows), 2),
        'controls': controls,
    }

    json_path = OUT_DIR / 'i3wr_audit20_report.json'
    csv_path = OUT_DIR / 'i3wr_audit20_report.csv'
    with json_path.open('w', encoding='utf-8') as f:
        json.dump({'summary': summary, 'cases': rows}, f, ensure_ascii=False, indent=2)

    with csv_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(json.dumps({'summary': summary, 'json_path': str(json_path), 'csv_path': str(csv_path)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
