#!/usr/bin/env python3
import time
import numpy as np
import iq_integration as IQ


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"OK {msg}")


def build_call_pattern():
    opens = np.array([1.0984, 1.0990, 1.1000, 1.1012, 1.1028, 1.1025, 1.1021], dtype=float)
    closes = np.array([1.0987, 1.1000, 1.1012, 1.1028, 1.1025, 1.1021, 1.1019], dtype=float)
    highs = np.array([1.0989, 1.1004, 1.1016, 1.1032, 1.1030, 1.1027, 1.1023], dtype=float)
    lows = np.array([1.0982, 1.0988, 1.0998, 1.1010, 1.1018, 1.1014, 1.1011], dtype=float)
    return opens, highs, lows, closes


def build_put_pattern():
    opens = np.array([1.1066, 1.1060, 1.1048, 1.1036, 1.1022, 1.1025, 1.1029], dtype=float)
    closes = np.array([1.1063, 1.1048, 1.1036, 1.1022, 1.1025, 1.1029, 1.1031], dtype=float)
    highs = np.array([1.1068, 1.1062, 1.1050, 1.1038, 1.1032, 1.1036, 1.1039], dtype=float)
    lows = np.array([1.1061, 1.1044, 1.1032, 1.1018, 1.1020, 1.1023, 1.1027], dtype=float)
    return opens, highs, lows, closes


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


def test_detector_call():
    opens, highs, lows, closes = build_call_pattern()
    res = IQ.analisar_impulso_3wicks(opens, highs, lows, closes, 'EURUSD')
    assert_true(res['direcao'] == 'CALL', 'I3WR CALL detectado')
    assert_true(res['entry_mode'] == 'wick_touch_retracement', 'CALL usa modo retração')
    assert_true(abs(res['trigger_price'] - lows[-1]) < 1e-9, 'CALL gatilho é mínima da vela anterior')


def test_detector_put():
    opens, highs, lows, closes = build_put_pattern()
    res = IQ.analisar_impulso_3wicks(opens, highs, lows, closes, 'EURUSD')
    assert_true(res['direcao'] == 'PUT', 'I3WR PUT detectado')
    assert_true(res['entry_mode'] == 'wick_touch_retracement', 'PUT usa modo retração')
    assert_true(abs(res['trigger_price'] - highs[-1]) < 1e-9, 'PUT gatilho é máxima da vela anterior')


def test_buy_touch_executes_on_retracement():
    seq = [
        {'from': 100, 'open': 1.1020, 'close': 1.1022, 'min': 1.1018, 'max': 1.1023},
        {'from': 100, 'open': 1.1020, 'close': 1.1019, 'min': 1.1014, 'max': 1.1022},
        {'from': 100, 'open': 1.1020, 'close': 1.1016, 'min': 1.1011, 'max': 1.1021},
    ]
    fake = FakeIQ(seq)
    IQ._iq_instances['tester_touch'] = fake
    IQ.set_user_context('tester_touch')
    original = IQ.seconds_to_next_candle
    IQ.seconds_to_next_candle = lambda timeframe=60: 1.1
    try:
        ok, order_id = IQ.buy_binary_retracement_touch('EURUSD', 2.0, 'call', 1.1011, account_type='REAL')
    finally:
        IQ.seconds_to_next_candle = original
        IQ._iq_instances.pop('tester_touch', None)
    assert_true(ok is True and str(order_id).startswith('order-'), 'Entrada executa ao tocar o pavio')
    assert_true(fake.balance_type == 'REAL', 'Conta REAL aplicada na entrada por retração')
    assert_true(len(fake.buy_calls) == 1, 'Apenas uma ordem enviada no toque')


def test_buy_touch_cancels_when_not_reached():
    seq = [
        {'from': 200, 'open': 1.1020, 'close': 1.1022, 'min': 1.1019, 'max': 1.1023},
        {'from': 200, 'open': 1.1020, 'close': 1.1021, 'min': 1.1018, 'max': 1.1022},
        {'from': 200, 'open': 1.1020, 'close': 1.1020, 'min': 1.1017, 'max': 1.1021},
    ]
    fake = FakeIQ(seq)
    IQ._iq_instances['tester_no_touch'] = fake
    IQ.set_user_context('tester_no_touch')
    original = IQ.seconds_to_next_candle
    IQ.seconds_to_next_candle = lambda timeframe=60: 0.8
    try:
        ok, reason = IQ.buy_binary_retracement_touch('EURUSD', 2.0, 'call', 1.1011, account_type='PRACTICE')
    finally:
        IQ.seconds_to_next_candle = original
        IQ._iq_instances.pop('tester_no_touch', None)
    assert_true(ok is False and 'não tocou' in reason, 'Entrada cancela se a 4ª vela não tocar o pavio')
    assert_true(len(fake.buy_calls) == 0, 'Sem ordem quando não há toque')


if __name__ == '__main__':
    test_detector_call()
    test_detector_put()
    test_buy_touch_executes_on_retracement()
    test_buy_touch_cancels_when_not_reached()
    print('TOTAL_OK=4')
