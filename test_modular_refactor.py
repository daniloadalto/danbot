import unittest
from unittest import mock
import numpy as np

import iq_integration as IQ
import app as app_module


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

    def make_down_exhaustion_ohlc(self):
        base = self.make_up_exhaustion_ohlc()['closes']
        closes = float(base.max() + base.min()) - base
        opens = np.r_[closes[0], closes[:-1]]
        highs = np.maximum(opens, closes) + np.linspace(0.004, 0.030, len(closes))
        lows = np.minimum(opens, closes) - np.linspace(0.001, 0.008, len(closes))
        return {'opens': opens, 'highs': highs, 'lows': lows, 'closes': closes}

    def test_i3wr_primary_engine_generates_signal(self):
        sig = IQ.analyze_asset_full(
            'EURUSD-OTC',
            self.make_i3wr_call_ohlc(),
            strategies={
                'i3wr': True,
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

    def test_i3wr_enabled_without_setup_falls_back_to_modular_engine(self):
        sig = IQ.analyze_asset_full(
            'TEST-OTC',
            self.make_up_exhaustion_ohlc(),
            strategies={
                'i3wr': True,
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
        self.assertIsNotNone(sig)
        self.assertEqual(sig['direction'], 'PUT')
        self.assertEqual(sig['detail']['logica_preco']['engine'], 'modular_selectable')
        self.assertEqual(sig['lp_forca'], 0)

    def test_i3wr_disabled_falls_back_to_modular_engine(self):
        sig = IQ.analyze_asset_full(
            'TEST-OTC',
            self.make_up_exhaustion_ohlc(),
            strategies={
                'i3wr': False,
                'ma': False,
                'rsi': True,
                'bb': True,
                'macd': True,
                'dead': False,
                'reverse': True,
                'detector28': False,
            },
            min_confluence=2,
        )
        self.assertIsNotNone(sig)
        self.assertEqual(sig['direction'], 'PUT')
        self.assertEqual(sig['detail']['logica_preco']['engine'], 'modular_selectable')
        self.assertEqual(sig['lp_forca'], 0)

    def test_min_confluence_counts_i3wr_plus_confirmations(self):
        ohlc = self.make_i3wr_call_ohlc()
        strategies = {
            'i3wr': True,
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

    def test_simple_trend_does_not_create_signal_alone(self):
        sig = IQ.analyze_asset_full(
            'EURUSD-OTC',
            self.make_i3wr_call_ohlc(),
            strategies={
                'i3wr': False,
                'ma': False,
                'rsi': False,
                'bb': False,
                'macd': False,
                'simple_trend': True,
                'pullback_m5': False,
                'pullback_m15': False,
                'dead': False,
                'reverse': False,
                'detector28': False,
            },
            min_confluence=1,
        )
        self.assertIsNone(sig)

    def test_guard_blocks_trade_when_rsi_and_bb_are_extreme_against(self):
        strategies = {
            'i3wr': False,
            'ma': True,
            'rsi': True,
            'bb': True,
            'macd': True,
            'simple_trend': True,
            'pullback_m5': False,
            'pullback_m15': False,
            'dead': False,
            'reverse': False,
            'detector28': False,
        }
        with mock.patch.object(IQ, 'calc_rsi', return_value=18.0), mock.patch.object(IQ, 'calc_bollinger', return_value=(None, None, None, 0.05)):
            sig = IQ.analyze_asset_full('TEST-OTC', self.make_down_exhaustion_ohlc(), strategies=strategies, min_confluence=2)
        self.assertIsNone(sig)

    def test_guard_reduces_strength_when_only_one_extreme_counter_signal_exists(self):
        strategies = {
            'i3wr': False,
            'ma': True,
            'rsi': True,
            'bb': True,
            'macd': True,
            'simple_trend': True,
            'pullback_m5': False,
            'pullback_m15': False,
            'dead': False,
            'reverse': False,
            'detector28': False,
        }
        with mock.patch.object(IQ, 'calc_rsi', return_value=18.0), mock.patch.object(IQ, 'calc_bollinger', return_value=(None, None, None, 0.60)):
            sig = IQ.analyze_asset_full('TEST-OTC', self.make_down_exhaustion_ohlc(), strategies=strategies, min_confluence=2)
        self.assertIsNotNone(sig)
        self.assertEqual(sig['direction'], 'PUT')
        self.assertLessEqual(sig['strength'], 89)
        self.assertTrue(sig['detail']['entry_guard']['counterpressure']['strong_rsi_against'])

    def test_trend_priority_relaxes_min_confluence_for_aligned_pullback(self):
        strategies = {
            'i3wr': False,
            'ma': True,
            'rsi': False,
            'bb': False,
            'macd': True,
            'simple_trend': True,
            'pullback_m5': False,
            'pullback_m15': True,
            'dead': False,
            'reverse': False,
            'detector28': False,
        }
        neutral_pullback = {'direction': None, 'score_call': 0, 'score_put': 0, 'razoes': [], 'timeframe': 'M5'}
        strong_m15 = {'direction': 'CALL', 'score_call': 4, 'score_put': 0, 'razoes': ['pullback M15 alinhado'], 'timeframe': 'M15'}
        with mock.patch.object(IQ, '_pullback_module', side_effect=[neutral_pullback, strong_m15]), \
             mock.patch.object(IQ, 'calc_rsi', return_value=56.0), \
             mock.patch.object(IQ, 'calc_bollinger', return_value=(None, None, None, 0.55)), \
             mock.patch.object(IQ, 'calc_macd', side_effect=lambda *_args, **_kwargs: (0.5, 0.2, 0.1)), \
             mock.patch.object(IQ, 'summarize_detected_patterns', return_value={'dominant': {}, 'all': []}):
            sig = IQ.analyze_asset_full('EURUSD-OTC', self.make_i3wr_call_ohlc(), strategies=strategies, min_confluence=4)
        self.assertIsNotNone(sig)
        self.assertEqual(sig['direction'], 'CALL')
        self.assertTrue(sig['detail']['entry_guard']['trend_priority'])
        self.assertEqual(sig['detail']['entry_guard']['effective_min_conf'], 3)

    def test_aligned_candle_pattern_is_exposed_in_pattern_and_reason(self):
        candle_ctx = {
            'dominant': {
                'name': 'dark_cloud_cover',
                'label': 'Dark Cloud Cover',
                'direction': 'PUT',
                'accuracy': 82,
                'desc': '🌑 Dark Cloud Cover (82%) — nuvem bajista',
                'premium': True,
                'is_reversal': True,
                'is_continuation': False,
                'trend_aligned': True,
            },
            'all': []
        }
        with mock.patch.object(IQ, 'summarize_detected_patterns', return_value=candle_ctx), \
             mock.patch.object(IQ, '_reverse_psychology_module', return_value={
                 'direction': 'PUT', 'score_call': 0, 'score_put': 3, 'razoes': ['reverse confirmou PUT']
             }):
            sig = IQ.analyze_asset_full('TEST-OTC', self.make_up_exhaustion_ohlc(), strategies={
                'i3wr': False,
                'ma': False,
                'rsi': True,
                'bb': True,
                'macd': True,
                'dead': False,
                'reverse': True,
                'detector28': False,
            }, min_confluence=2)
        self.assertIsNotNone(sig)
        self.assertIn('Dark Cloud Cover', sig['pattern'])
        self.assertEqual(sig['detail']['candle_pattern']['label'], 'Dark Cloud Cover')
        self.assertIn('CANDLE:', sig['reason'])

    def test_safe_open_time_fallback_handles_missing_underlying(self):
        now = IQ.time.time()

        class FakeIQ:
            def get_all_init(self):
                return {
                    'result': {
                        'turbo': {
                            'actives': {
                                '1': {'name': 'front.EURUSD-OTC', 'enabled': True, 'is_suspended': False},
                            }
                        },
                        'binary': {
                            'actives': {
                                '10': {'name': 'front.EURUSD', 'enabled': True, 'is_suspended': False},
                                '11': {'name': 'front.GBPUSD', 'enabled': True, 'is_suspended': True},
                            }
                        },
                    }
                }

            def get_instruments(self, instrument_type):
                if instrument_type == 'forex':
                    return {
                        'instruments': [
                            {'name': 'EURUSD', 'schedule': [{'open': now - 60, 'close': now + 60}]},
                            {'name': 'GBPUSD', 'schedule': [{'open': now - 600, 'close': now - 300}]},
                            {'name': 'USDJPY', 'schedule': [{'open': now - 60, 'close': now + 60}]},
                        ]
                    }
                return {'instruments': []}

            def get_all_open_time(self):
                raise KeyError('underlying')

        snapshot = IQ._safe_get_all_open_time(FakeIQ())
        self.assertTrue(IQ._is_open_in_snapshot('EURUSD', snapshot))
        self.assertTrue(IQ._is_open_in_snapshot('USDJPY', snapshot))
        self.assertFalse(IQ._is_open_in_snapshot('GBPUSD', snapshot))

        available = IQ._get_available_all_assets_inner(FakeIQ())
        self.assertIn('EURUSD', available)
        self.assertIn('USDJPY', available)
        self.assertIn('EURUSD-OTC', available)
        self.assertNotIn('GBPUSD', available)

    def test_heartbeat_reconnects_after_three_failures(self):
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
            if sleep_counter['n'] >= 3:
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

            self.assertTrue(calls, 'heartbeat deveria tentar reconectar após três falhas')
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


class MultiUserIsolationTests(unittest.TestCase):
    def test_bot_log_prefers_request_user_over_thread_local_context(self):
        old_alice = app_module._USER_STATES.pop('alice-log', None)
        old_bob = app_module._USER_STATES.pop('bob-log', None)
        try:
            IQ.set_user_context('bob-log')
            with app_module.app.test_request_context('/'):
                with mock.patch.object(app_module, 'current_user', return_value={'sub': 'alice-log', 'role': 'user'}):
                    app_module.bot_log('saldo isolado multiusuário', 'info')
            self.assertEqual(app_module.get_user_state('alice-log')['log'][0]['msg'], 'saldo isolado multiusuário')
            self.assertEqual(app_module.get_user_state('bob-log')['log'], [])
        finally:
            app_module._USER_STATES.pop('alice-log', None)
            app_module._USER_STATES.pop('bob-log', None)
            if old_alice is not None:
                app_module._USER_STATES['alice-log'] = old_alice
            if old_bob is not None:
                app_module._USER_STATES['bob-log'] = old_bob
            IQ.set_user_context('default')

    def test_apply_request_iq_context_sets_and_clears_authenticated_user(self):
        IQ.set_user_context('stale-user')
        with app_module.app.test_request_context('/'):
            with mock.patch.object(app_module, 'current_user', return_value={'sub': 'alice-ctx', 'role': 'user'}):
                applied = app_module._apply_request_iq_context()
                self.assertEqual(applied, 'alice-ctx')
                self.assertEqual(IQ._current_username(), 'alice-ctx')
        app_module._clear_request_iq_context()
        self.assertEqual(IQ._current_username(), 'default')

    def test_connect_iq_serializes_concurrent_attempts(self):
        old_instances = dict(IQ._iq_instances)
        old_meta = dict(IQ._iq_user_meta)
        old_cache = dict(IQ._session_valid_cache)
        active = {'now': 0, 'max': 0}
        active_lock = __import__('threading').Lock()
        results = []

        class FakeIQOption:
            def __init__(self, email, password):
                self.email = email
                self.password = password

            def connect(self):
                import time as pytime
                with active_lock:
                    active['now'] += 1
                    active['max'] = max(active['max'], active['now'])
                pytime.sleep(0.05)
                with active_lock:
                    active['now'] -= 1
                return True, None

            def change_balance(self, _acc):
                return True

            def get_balance(self):
                return 100.0

            def close(self):
                return None

        def worker(username):
            results.append(IQ.connect_iq(f'{username}@example.com', 'secret', username=username, host='iqoption.com'))

        try:
            IQ._iq_instances.clear()
            IQ._iq_user_meta.clear()
            IQ._session_valid_cache.clear()
            with mock.patch('iqoptionapi.stable_api.IQ_Option', FakeIQOption), \
                 mock.patch.object(IQ, 'sync_actives_from_api', return_value=0), \
                 mock.patch.object(IQ.time, 'sleep', side_effect=lambda *_args, **_kwargs: None):
                t1 = __import__('threading').Thread(target=worker, args=('user-a',))
                t2 = __import__('threading').Thread(target=worker, args=('user-b',))
                t1.start(); t2.start(); t1.join(); t2.join()

            self.assertEqual(len(results), 2)
            self.assertTrue(all(ok for ok, _payload in results))
            self.assertEqual(active['max'], 1)
            self.assertIn('user-a', IQ._iq_instances)
            self.assertIn('user-b', IQ._iq_instances)
        finally:
            IQ._iq_instances.clear()
            IQ._iq_instances.update(old_instances)
            IQ._iq_user_meta.clear()
            IQ._iq_user_meta.update(old_meta)
            IQ._session_valid_cache.clear()
            IQ._session_valid_cache.update(old_cache)
            IQ.set_user_context('default')


if __name__ == '__main__':
    unittest.main(verbosity=2)
