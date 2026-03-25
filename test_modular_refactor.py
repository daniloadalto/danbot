import unittest
from unittest import mock
import numpy as np

import iq_integration as IQ


class ModularRefactorTests(unittest.TestCase):
    def make_i3wr_call_ohlc(self):
        base_closes = np.array([
            1.0978, 1.0979, 1.0980, 1.0981, 1.0982, 1.0982, 1.0983, 1.0984,
            1.0985, 1.0986, 1.0987, 1.0987, 1.0988, 1.0989, 1.0990, 1.0991,
            1.0991, 1.0992, 1.0993, 1.0994, 1.0995, 1.0996, 1.0997,
        ], dtype=float)
        base_opens = np.r_[base_closes[0], base_closes[:-1]]
        base_highs = np.maximum(base_opens, base_closes) + 0.00025
        base_lows = np.minimum(base_opens, base_closes) - 0.00025

        opens_tail = np.array([1.0984, 1.0990, 1.1000, 1.1012, 1.1028, 1.1025, 1.1021], dtype=float)
        closes_tail = np.array([1.0987, 1.1000, 1.1012, 1.1028, 1.1025, 1.1021, 1.1019], dtype=float)
        highs_tail = np.array([1.0989, 1.1004, 1.1016, 1.1032, 1.1030, 1.1027, 1.1023], dtype=float)
        lows_tail = np.array([1.0982, 1.0988, 1.0998, 1.1010, 1.1018, 1.1014, 1.1011], dtype=float)

        opens = np.concatenate([base_opens, opens_tail])
        closes = np.concatenate([base_closes, closes_tail])
        highs = np.concatenate([base_highs, highs_tail])
        lows = np.concatenate([base_lows, lows_tail])
        return {'opens': opens, 'highs': highs, 'lows': lows, 'closes': closes}

    def make_up_exhaustion_ohlc(self):
        closes = np.array([
            1.0000, 1.0010, 1.0020, 1.0030, 1.0040, 1.0055, 1.0070, 1.0090,
            1.0120, 1.0160, 1.0200, 1.0250, 1.0310, 1.0380, 1.0460, 1.0550,
            1.0670, 1.0820, 1.1000, 1.1220, 1.1500, 1.1850, 1.2300, 1.2850,
            1.3500, 1.4200, 1.5000, 1.5900, 1.6900, 1.8200,
        ], dtype=float)
        opens = np.r_[closes[0], closes[:-1]]
        highs = np.maximum(opens, closes) + np.linspace(0.004, 0.030, len(closes))
        lows = np.minimum(opens, closes) - np.linspace(0.001, 0.008, len(closes))
        return {'opens': opens, 'highs': highs, 'lows': lows, 'closes': closes}

    def test_i3wr_primary_engine_generates_signal(self):
        sig = IQ.analyze_asset_full(
            'EURUSD-OTC',
            self.make_i3wr_call_ohlc(),
            strategies={
                'ma': False,
                'rsi': False,
                'bb': False,
                'macd': False,
                'dead': False,
                'reverse': False,
                'detector28': False,
            },
            min_confluence=1,
        )
        self.assertIsNotNone(sig)
        self.assertEqual(sig['direction'], 'CALL')
        self.assertIn('I3WR', sig['pattern'])
        self.assertEqual(sig['detail']['logica_preco']['engine'], 'i3wr_primary')
        self.assertEqual(sig['lp_direcao'], 'CALL')
        self.assertGreater(sig['lp_forca'], 0)
        self.assertEqual(sig['lp_entry_mode'], 'wick_touch_retracement')
        self.assertIsNotNone(sig['lp_trigger_price'])

    def test_without_i3wr_setup_no_signal_even_with_reverse_modules(self):
        sig = IQ.analyze_asset_full(
            'TEST-OTC',
            self.make_up_exhaustion_ohlc(),
            strategies={
                'ma': False,
                'rsi': True,
                'bb': True,
                'macd': True,
                'dead': False,
                'reverse': True,
                'detector28': False,
            },
            min_confluence=1,
        )
        self.assertIsNone(sig)

    def test_min_confluence_counts_i3wr_plus_confirmations(self):
        ohlc = self.make_i3wr_call_ohlc()
        strategies = {
            'ma': False,
            'rsi': False,
            'bb': False,
            'macd': False,
            'dead': True,
            'reverse': True,
            'detector28': False,
        }
        with mock.patch.object(IQ, '_reverse_psychology_module', return_value={
            'direction': 'CALL', 'score_call': 3, 'score_put': 0, 'razoes': ['reverse confirmou CALL']
        }), mock.patch.object(IQ, '_detect_dead_candle_module', return_value={
            'direction': 'CALL', 'score_call': 2, 'score_put': 0, 'razoes': ['dead candle confirmou CALL']
        }):
            sig_ok = IQ.analyze_asset_full('EURUSD-OTC', ohlc, strategies=strategies, min_confluence=3, dc_mode='combined')
            sig_blocked = IQ.analyze_asset_full('EURUSD-OTC', ohlc, strategies=strategies, min_confluence=4, dc_mode='combined')
        self.assertIsNotNone(sig_ok)
        self.assertEqual(sig_ok['direction'], 'CALL')
        self.assertIsNone(sig_blocked)

    def test_heartbeat_reconnects_after_two_failures(self):
        user = 'heartbeat-user'

        class FailingIQ:
            def get_balance(self):
                raise RuntimeError('socket down')

        calls = []
        fake_state = {user: {'broker_connected': True, 'broker_balance': 0.0}}

        def fake_connect(email, password, account_type='PRACTICE', host='iqoption.com', username=None, broker_name=None):
            calls.append({
                'email': email,
                'password': password,
                'account_type': account_type,
                'host': host,
                'username': username,
                'broker_name': broker_name,
            })
            return True, {'balance': 123.45, 'account_type': account_type}

        sleep_counter = {'n': 0}

        def fake_sleep(_seconds):
            sleep_counter['n'] += 1
            if sleep_counter['n'] >= 2:
                IQ._heartbeat_running = False

        old_instances = dict(IQ._iq_instances)
        old_meta = dict(IQ._iq_user_meta)
        old_cache = dict(IQ._session_valid_cache)
        old_state_ref = IQ._bot_state_ref

        try:
            IQ._iq_instances.clear()
            IQ._iq_instances[user] = FailingIQ()
            IQ._iq_user_meta.clear()
            IQ._iq_user_meta[user] = {
                'email': 'user@example.com',
                'password': 'secret',
                'account_type': 'PRACTICE',
                'host': 'iqoption.com',
                'broker_name': 'IQ Option',
            }
            IQ._session_valid_cache.clear()
            IQ._session_valid_cache[user] = {
                'result': True,
                'ts': 0.0,
                'last_ok': IQ.time.time(),
                'fail_count': 0,
            }
            IQ._bot_state_ref = fake_state
            IQ._heartbeat_running = True

            with mock.patch.object(IQ, 'connect_iq', side_effect=fake_connect),                  mock.patch.object(IQ.time, 'sleep', side_effect=fake_sleep):
                IQ.heartbeat_iq()

            self.assertTrue(calls, 'heartbeat deveria tentar reconectar após duas falhas')
            self.assertEqual(calls[0]['username'], user)
            self.assertTrue(fake_state[user]['broker_connected'])
            self.assertEqual(fake_state[user]['broker_balance'], 123.45)
            self.assertTrue(IQ._get_session_cache(user)['result'])
        finally:
            IQ._heartbeat_running = False
            IQ._iq_instances.clear()
            IQ._iq_instances.update(old_instances)
            IQ._iq_user_meta.clear()
            IQ._iq_user_meta.update(old_meta)
            IQ._session_valid_cache.clear()
            IQ._session_valid_cache.update(old_cache)
            IQ._bot_state_ref = old_state_ref


if __name__ == '__main__':
    unittest.main(verbosity=2)
