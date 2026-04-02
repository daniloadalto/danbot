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

    def test_heartbeat_reconnects_after_five_failures(self):
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
            if sleep_counter['n'] >= 5:
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

            with mock.patch.object(IQ, 'connect_iq', side_effect=fake_connect),                  mock.patch.object(IQ.time, 'sleep', side_effect=fake_sleep),                  mock.patch.object(IQ, 'can_attempt_reconnect', return_value=True):
                IQ.heartbeat_iq()

            self.assertTrue(calls, 'heartbeat deveria tentar reconectar após cinco falhas')
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

    def test_stats_reset_only_current_user_state(self):
        old_alice = app_module._USER_STATES.pop('alice-reset', None)
        old_bob = app_module._USER_STATES.pop('bob-reset', None)
        try:
            app_module.get_user_state('alice-reset').update({'wins': 5, 'losses': 2, 'profit': 11.0, 'log': [{'msg': 'a'}]})
            app_module.get_user_state('bob-reset').update({'wins': 9, 'losses': 4, 'profit': 21.0, 'log': [{'msg': 'b'}]})
            fake_filter = mock.Mock()
            fake_filter.delete.return_value = 7
            with app_module.app.test_request_context('/api/stats/reset', method='POST', json={}):
                with mock.patch.object(app_module, 'current_user', return_value={'sub': 'alice-reset', 'role': 'user'}),                      mock.patch.object(app_module.TradeLog, 'query', mock.Mock(filter_by=mock.Mock(return_value=fake_filter))),                      mock.patch.object(app_module.db.session, 'commit', return_value=None):
                    response = app_module.stats_reset()
            payload = response.get_json()
            self.assertTrue(payload['ok'])
            self.assertEqual(payload['scope'], 'current_user')
            self.assertEqual(app_module.get_user_state('alice-reset')['wins'], 0)
            self.assertEqual(app_module.get_user_state('bob-reset')['wins'], 9)
            self.assertEqual(app_module.get_user_state('bob-reset')['profit'], 21.0)
        finally:
            app_module._USER_STATES.pop('alice-reset', None)
            app_module._USER_STATES.pop('bob-reset', None)
            if old_alice is not None:
                app_module._USER_STATES['alice-reset'] = old_alice
            if old_bob is not None:
                app_module._USER_STATES['bob-reset'] = old_bob

    def test_master_stats_reset_requires_explicit_all_users_flag(self):
        old_alice = app_module._USER_STATES.pop('alice-master-reset', None)
        old_bob = app_module._USER_STATES.pop('bob-master-reset', None)
        try:
            app_module.get_user_state('alice-master-reset').update({'wins': 3, 'losses': 1, 'profit': 4.0})
            app_module.get_user_state('bob-master-reset').update({'wins': 8, 'losses': 2, 'profit': 10.0})
            fake_filter = mock.Mock()
            fake_filter.delete.return_value = 2
            with app_module.app.test_request_context('/api/stats/reset', method='POST', json={}):
                with mock.patch.object(app_module, 'current_user', return_value={'sub': 'alice-master-reset', 'role': 'master'}),                      mock.patch.object(app_module.TradeLog, 'query', mock.Mock(filter_by=mock.Mock(return_value=fake_filter), delete=mock.Mock(return_value=99))),                      mock.patch.object(app_module.db.session, 'commit', return_value=None):
                    response = app_module.stats_reset()
            payload = response.get_json()
            self.assertEqual(payload['scope'], 'current_user')
            self.assertEqual(app_module.get_user_state('alice-master-reset')['wins'], 0)
            self.assertEqual(app_module.get_user_state('bob-master-reset')['wins'], 8)
        finally:
            app_module._USER_STATES.pop('alice-master-reset', None)
            app_module._USER_STATES.pop('bob-master-reset', None)
            if old_alice is not None:
                app_module._USER_STATES['alice-master-reset'] = old_alice
            if old_bob is not None:
                app_module._USER_STATES['bob-master-reset'] = old_bob

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



class MarketQualitySelectionTests(unittest.TestCase):
    def _smooth_trend_ohlc(self):
        closes = np.array([
            1.1000, 1.1003, 1.1006, 1.1009, 1.1011, 1.1014,
            1.1017, 1.1020, 1.1023, 1.1026, 1.1029, 1.1032,
        ], dtype=float)
        opens = np.r_[closes[0] - 0.0002, closes[:-1]]
        highs = np.maximum(opens, closes) + 0.00018
        lows = np.minimum(opens, closes) - 0.00016
        return opens, highs, lows, closes

    def _spiky_ohlc(self):
        closes = np.array([
            1.1000, 1.1018, 1.1002, 1.1024, 1.1001, 1.1030,
            1.0995, 1.1035, 1.0998, 1.1042, 1.1000, 1.1020,
        ], dtype=float)
        opens = np.r_[1.0998, closes[:-1]]
        highs = np.maximum(opens, closes) + np.array([0.0012, 0.0015, 0.0011, 0.0018, 0.0013, 0.0020, 0.0016, 0.0022, 0.0014, 0.0024, 0.0017, 0.0021])
        lows = np.minimum(opens, closes) - np.array([0.0010, 0.0012, 0.0011, 0.0014, 0.0012, 0.0016, 0.0015, 0.0018, 0.0013, 0.0019, 0.0014, 0.0016])
        return opens, highs, lows, closes

    def test_market_quality_prefers_smooth_trend_over_spiky_market(self):
        smooth = IQ._compute_market_quality_metrics(*self._smooth_trend_ohlc(), trend_hint='up')
        spiky = IQ._compute_market_quality_metrics(*self._spiky_ohlc(), trend_hint='up')
        self.assertTrue(smooth['preferred'])
        self.assertGreater(smooth['quality_score'], spiky['quality_score'])
        self.assertIn(spiky['regime'], ('too_volatile', 'noisy_trend'))

    def test_sort_signal_candidates_prefers_preferred_market_quality(self):
        smooth_sig = {
            'asset': 'SMOOTH',
            'direction': 'CALL',
            'strength': 81,
            'score_call': 8,
            'score_put': 2,
            'trend': 'up',
            'detail': {'modules': {}, 'market_quality': {'preferred': True, 'regime': 'smooth_trend', 'quality_score': 78}},
        }
        noisy_sig = {
            'asset': 'NOISY',
            'direction': 'CALL',
            'strength': 83,
            'score_call': 8,
            'score_put': 2,
            'trend': 'up',
            'detail': {'modules': {}, 'market_quality': {'preferred': False, 'regime': 'too_volatile', 'quality_score': 34, 'too_volatile': True}},
        }
        ranked = app_module._sort_signal_candidates([noisy_sig, smooth_sig])
        self.assertEqual(ranked[0]['asset'], 'SMOOTH')

    def test_run_backtest_prefers_asset_with_cleaner_trend_profile(self):
        fake_results = {
            'SMOOTH-OTC': {
                'asset': 'SMOOTH-OTC',
                'total_sinais': 20,
                'total_wins': 13,
                'overall_win_rate': 65.0,
                'fonte': 'simulado',
                'trend': 'up',
                'trend_label': 'Alta',
                'trend_desc': 'Alta contínua',
                'timeframe_label': 'M1',
                'market_quality_score': 80,
                'market_quality_preferred': True,
                'market_quality_regime': 'smooth_trend',
                'volatility_regime': 'medium',
                'trend_continuity': 0.82,
            },
            'SPIKY-OTC': {
                'asset': 'SPIKY-OTC',
                'total_sinais': 20,
                'total_wins': 13,
                'overall_win_rate': 65.0,
                'fonte': 'simulado',
                'trend': 'up',
                'trend_label': 'Alta',
                'trend_desc': 'Alta irregular',
                'timeframe_label': 'M1',
                'market_quality_score': 32,
                'market_quality_preferred': False,
                'market_quality_regime': 'too_volatile',
                'volatility_regime': 'high',
                'trend_continuity': 0.31,
            },
        }
        with mock.patch.object(IQ, 'run_backtest_real', side_effect=lambda asset, candles=250: fake_results[asset]):
            result = IQ.run_backtest(assets=['SPIKY-OTC', 'SMOOTH-OTC'], candles_per_window=80)
        self.assertEqual(result['ranked'][0]['asset'], 'SMOOTH-OTC')
        self.assertGreater(result['ranked'][0]['selection_score'], result['ranked'][1]['selection_score'])

    def test_scan_assets_hardens_confluence_and_respects_asset_profile_after_losses(self):
        opens, highs, lows, closes = self._smooth_trend_ohlc()
        fake_ohlc = {'opens': opens, 'highs': highs, 'lows': lows, 'closes': closes}
        profile = {
            'confluencia_minima': 5,
            'confluencia_sugerida': 5,
            'strategies_override': {'ma': True, 'macd': True, 'reverse': False},
            'padroes_ativos': ['I3WR + Pullback M15'],
            'market_quality_score': 79,
            'market_quality_preferred': True,
            'market_quality_regime': 'smooth_trend',
            'volatility_regime': 'medium',
            'trend_continuity': 0.84,
            'trend': 'up',
            'best_pattern': 'I3WR + Pullback M15',
            'indicadores': ['EMA5/EMA50', 'MACD'],
        }
        captured = {}

        def fake_analyze(asset, ohlc, strategies=None, min_confluence=0, dc_mode='disabled', base_timeframe=60):
            captured['strategies'] = dict(strategies or {})
            captured['min_confluence'] = min_confluence
            return {
                'asset': asset,
                'direction': 'CALL',
                'strength': 96,
                'pattern': 'I3WR + Pullback M15',
                'reason': 'perfil alinhado',
                'detail': {'market_quality': {'preferred': True, 'quality_score': 82, 'regime': 'smooth_trend'}},
            }

        with mock.patch.object(IQ, 'get_iq', return_value=None),              mock.patch.object(IQ, 'generate_synthetic_candles', return_value=(closes, fake_ohlc)),              mock.patch.object(IQ, 'get_asset_profile', return_value=profile),              mock.patch.object(IQ, 'analyze_asset_full', side_effect=fake_analyze):
            signals = IQ.scan_assets(
                ['EURUSD-OTC'],
                timeframe=60,
                count=50,
                bot_state_ref={'running': True, 'consecutive_losses': 3, 'adaptive_mode': True},
                strategies={'reverse': True, 'bb': True, 'ma': False},
                min_confluence=3,
            )

        self.assertEqual(len(signals), 1)
        self.assertGreaterEqual(captured['min_confluence'], 7)
        self.assertTrue(captured['strategies']['ma'])
        self.assertFalse(captured['strategies']['reverse'])


class BrokerResilienceTests(unittest.TestCase):
    def test_preserve_broker_connection_after_recent_success_and_candle_timeout(self):
        user = 'resilience-user'
        old_cache = dict(IQ._session_valid_cache)
        old_transport = dict(IQ._transport_health) if hasattr(IQ, '_transport_health') else {}
        try:
            IQ._session_valid_cache.clear()
            IQ._transport_health.clear()
            now = IQ.time.time()
            IQ._session_valid_cache[user] = {
                'result': True,
                'ts': now - 120,
                'last_ok': now - 60,
                'fail_count': 0,
            }
            IQ._mark_candle_timeout(user)
            self.assertTrue(IQ.should_preserve_broker_connection(user))
        finally:
            IQ._session_valid_cache.clear()
            IQ._session_valid_cache.update(old_cache)
            IQ._transport_health.clear()
            IQ._transport_health.update(old_transport)

    def test_resync_live_broker_state_keeps_connected_flag_during_transient_instability(self):
        username = 'app-preserve-user'
        old_state = app_module._USER_STATES.pop(username, None)
        old_cache = dict(IQ._session_valid_cache)
        old_transport = dict(IQ._transport_health) if hasattr(IQ, '_transport_health') else {}
        try:
            st = app_module.get_user_state(username)
            st['broker_connected'] = True
            now = IQ.time.time()
            IQ._session_valid_cache.clear()
            IQ._transport_health.clear()
            IQ._session_valid_cache[username] = {'result': True, 'ts': now - 999, 'last_ok': now - 30, 'fail_count': 0}
            IQ._mark_candle_timeout(username)
            with mock.patch.object(IQ, 'set_user_context'), \
                 mock.patch.object(IQ, 'is_iq_session_valid', return_value=False):
                ok = app_module._resync_live_broker_state(username)
            self.assertTrue(ok)
            self.assertTrue(st['broker_connected'])
        finally:
            app_module._USER_STATES.pop(username, None)
            if old_state is not None:
                app_module._USER_STATES[username] = old_state
            IQ._session_valid_cache.clear()
            IQ._session_valid_cache.update(old_cache)
            IQ._transport_health.clear()
            IQ._transport_health.update(old_transport)

    def test_execute_binary_buy_recovers_from_balance_context_loss(self):
        class FakeIQ:
            def __init__(self):
                self.buy_calls = 0
                self.change_calls = []

            def change_balance(self, account_type):
                self.change_calls.append(account_type)
                return True

            def get_balance(self):
                return 100.0

            def buy(self, amount, asset, direction, mode):
                self.buy_calls += 1
                if self.buy_calls <= 2:
                    return False, 'User balance not found'
                return True, 777

        iq = FakeIQ()
        IQ.set_user_context('recover-balance-user')
        ok, order_id = IQ._execute_binary_buy(iq, 'EURUSD-OTC', 2.0, 'call', 1, account_type='PRACTICE')
        self.assertTrue(ok)
        self.assertEqual(order_id, 777)
        self.assertGreaterEqual(len(iq.change_calls), 1)

    def test_execute_binary_buy_forces_reconnect_when_local_balance_rearm_is_not_enough(self):
        user = 'recover-balance-force-user'

        class BrokenIQ:
            def __init__(self):
                self.buy_calls = 0
                self.change_calls = []

            def change_balance(self, account_type):
                self.change_calls.append(account_type)
                return True

            def get_balance(self):
                return 100.0

            def buy(self, amount, asset, direction, mode):
                self.buy_calls += 1
                return False, 'User balance not found'

        class FreshIQ:
            def __init__(self):
                self.buy_calls = 0
                self.change_calls = []

            def change_balance(self, account_type):
                self.change_calls.append(account_type)
                return True

            def get_balance(self):
                return 150.0

            def buy(self, amount, asset, direction, mode):
                self.buy_calls += 1
                return True, 888

        old_instances = dict(IQ._iq_instances)
        old_meta = dict(IQ._iq_user_meta)
        old_cache = dict(IQ._session_valid_cache)
        old_transport = dict(IQ._transport_health) if hasattr(IQ, '_transport_health') else {}
        reconnect_calls = []

        def fake_connect(email, password, account_type='PRACTICE', host='iqoption.com', username=None, broker_name=None):
            reconnect_calls.append({'username': username, 'account_type': account_type})
            IQ._iq_instances[username] = FreshIQ()
            return True, {'balance': 150.0, 'account_type': account_type}

        try:
            IQ._iq_instances.clear()
            IQ._iq_user_meta.clear()
            IQ._session_valid_cache.clear()
            IQ._transport_health.clear()
            IQ._iq_instances[user] = BrokenIQ()
            IQ._iq_user_meta[user] = {
                'email': 'user@example.com',
                'password': 'secret',
                'account_type': 'PRACTICE',
                'host': 'iqoption.com',
                'broker_name': 'IQ Option',
            }
            IQ.set_user_context(user)
            with mock.patch.object(IQ, 'connect_iq', side_effect=fake_connect):
                ok, order_id = IQ._execute_binary_buy(IQ._iq_instances[user], 'EURUSD-OTC', 2.0, 'call', 1, account_type='PRACTICE')
            self.assertTrue(ok)
            self.assertEqual(order_id, 888)
            self.assertEqual(len(reconnect_calls), 1)
        finally:
            IQ._iq_instances.clear()
            IQ._iq_instances.update(old_instances)
            IQ._iq_user_meta.clear()
            IQ._iq_user_meta.update(old_meta)
            IQ._session_valid_cache.clear()
            IQ._session_valid_cache.update(old_cache)
            IQ._transport_health.clear()
            IQ._transport_health.update(old_transport)

    def test_background_reconnect_surfaces_internal_exception_as_error_state(self):
        username = 'reconnect-crash-user'
        old_state = app_module._USER_STATES.pop(username, None)
        old_conn = app_module._USER_CONN_STATES.pop(username, None)
        old_lock = app_module._USER_CONN_LOCKS.pop(username, None)
        try:
            st = app_module.get_user_state(username)
            st['broker_name'] = 'IQ Option'
            st['broker_email'] = 'user@example.com'
            st['broker_password'] = 'secret'
            st['broker_account_type'] = 'PRACTICE'
            with mock.patch.object(app_module.IQ, 'set_user_context'), \
                 mock.patch.object(app_module.IQ, 'connect_iq', side_effect=RuntimeError('boom connect')):
                launched, why = app_module._kick_background_reconnect(username, reason='test')
                self.assertTrue(launched)
                self.assertEqual(why, 'connecting')
                for _ in range(20):
                    conn_st = app_module.get_user_conn_state(username)
                    if conn_st.get('status') == 'error':
                        break
                    time.sleep(0.05)
                conn_st = app_module.get_user_conn_state(username)
                self.assertEqual(conn_st.get('status'), 'error')
                self.assertIn('Erro interno ao conectar', conn_st.get('error', ''))
                self.assertFalse(st.get('broker_connected', False))
        finally:
            app_module._USER_STATES.pop(username, None)
            app_module._USER_CONN_STATES.pop(username, None)
            app_module._USER_CONN_LOCKS.pop(username, None)
            if old_state is not None:
                app_module._USER_STATES[username] = old_state
            if old_conn is not None:
                app_module._USER_CONN_STATES[username] = old_conn
            if old_lock is not None:
                app_module._USER_CONN_LOCKS[username] = old_lock

    def test_ui_disconnect_keeps_bot_running_when_auto_stop_disabled(self):
        username = 'ui-keep-running'
        old_state = app_module._USER_STATES.pop(username, None)
        try:
            st = app_module.get_user_state(username)
            st['running'] = True
            st['auto_stop_on_ui_disconnect'] = False
            with app_module.app.test_request_context('/api/ui/disconnect', method='POST'):
                with mock.patch.object(app_module, 'current_user', return_value={'sub': username, 'role': 'user'}), \
                     mock.patch.object(app_module, '_force_stop_user_bot') as stop_mock:
                    resp = app_module.ui_disconnect()
            self.assertEqual(resp.status_code, 200)
            self.assertFalse(stop_mock.called)
            self.assertTrue(st['running'])
            self.assertEqual(st['ui_last_ping'], 0.0)
            self.assertIn('seguirá operando', st['log'][0]['msg'])
        finally:
            app_module._USER_STATES.pop(username, None)
            if old_state is not None:
                app_module._USER_STATES[username] = old_state

    def test_i3wr_guard_blocks_hard_overbought_continuation(self):
        with mock.patch.object(IQ, 'calc_rsi', return_value=92.0), \
             mock.patch.object(IQ, 'calc_bollinger', return_value=(None, None, None, 0.97)):
            sig = IQ.analyze_asset_full(
                'EURUSD-OTC',
                ModularRefactorTests().make_i3wr_call_ohlc(),
                strategies={
                    'i3wr': True,
                    'ma': True,
                    'rsi': True,
                    'bb': True,
                    'macd': False,
                    'dead': False,
                    'reverse': False,
                    'detector28': False,
                },
                min_confluence=1,
            )
        self.assertIsNone(sig)

    def test_heartbeat_reconnects_after_five_failures_with_new_threshold(self):
        user = 'heartbeat-user-threshold'

        class FailingIQ:
            def get_balance(self):
                raise RuntimeError('socket down')

        calls = []
        fake_state = {user: {'broker_connected': True, 'broker_balance': 0.0}}

        def fake_connect(email, password, account_type='PRACTICE', host='iqoption.com', username=None, broker_name=None):
            calls.append({'username': username, 'email': email})
            return True, {'balance': 222.0, 'account_type': account_type}

        sleep_counter = {'n': 0}
        def fake_sleep(_seconds):
            sleep_counter['n'] += 1
            if sleep_counter['n'] >= 5:
                IQ._heartbeat_running = False

        old_instances = dict(IQ._iq_instances)
        old_meta = dict(IQ._iq_user_meta)
        old_cache = dict(IQ._session_valid_cache)
        old_transport = dict(IQ._transport_health) if hasattr(IQ, '_transport_health') else {}
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
            IQ._transport_health.clear()
            IQ._session_valid_cache[user] = {
                'result': True,
                'ts': 0.0,
                'last_ok': IQ.time.time() - 1800,
                'fail_count': 0,
            }
            IQ._bot_state_ref = fake_state
            IQ._heartbeat_running = True

            with mock.patch.object(IQ, 'connect_iq', side_effect=fake_connect), \
                 mock.patch.object(IQ.time, 'sleep', side_effect=fake_sleep), \
                 mock.patch.object(IQ, 'can_attempt_reconnect', return_value=True):
                IQ.heartbeat_iq()

            self.assertTrue(calls, 'heartbeat deveria tentar reconectar após cinco falhas')
            self.assertEqual(calls[0]['username'], user)
            self.assertTrue(fake_state[user]['broker_connected'])
            self.assertEqual(fake_state[user]['broker_balance'], 222.0)
        finally:
            IQ._heartbeat_running = False
            IQ._iq_instances.clear()
            IQ._iq_instances.update(old_instances)
            IQ._iq_user_meta.clear()
            IQ._iq_user_meta.update(old_meta)
            IQ._session_valid_cache.clear()
            IQ._session_valid_cache.update(old_cache)
            IQ._transport_health.clear()
            IQ._transport_health.update(old_transport)
            IQ._bot_state_ref = old_state_ref


    def test_martingale_pending_losses_only_count_loss_at_limit(self):
        state = app_module._default_user_state()
        state['martingale_enabled'] = True
        state['martingale_levels'] = 2
        state['entry_value'] = 10.0

        mg = app_module._get_martingale_state(state)
        mg['pending_losses'] += 1
        mg['pending_loss_amount'] = round(mg.get('pending_loss_amount', 0.0) + 10.0, 2)
        step1 = app_module._arm_or_advance_martingale(state, 'EURUSD-OTC', 10.0)
        self.assertTrue(step1['activated'])
        self.assertEqual(step1['level'], 1)
        self.assertEqual(step1['pending_losses'], 1)

        mg = app_module._get_martingale_state(state)
        mg['pending_losses'] += 1
        mg['pending_loss_amount'] = round(mg.get('pending_loss_amount', 0.0) + 22.0, 2)
        step2 = app_module._arm_or_advance_martingale(state, 'GBPUSD-OTC', 22.0)
        self.assertTrue(step2['activated'])
        self.assertEqual(step2['level'], 2)
        self.assertEqual(step2['pending_losses'], 2)

        mg = app_module._get_martingale_state(state)
        mg['pending_losses'] += 1
        mg['pending_loss_amount'] = round(mg.get('pending_loss_amount', 0.0) + 48.4, 2)
        step3 = app_module._arm_or_advance_martingale(state, 'AUDUSD-OTC', 48.4)
        self.assertTrue(step3['finished'])
        self.assertEqual(step3['level'], 2)
        self.assertEqual(step3['pending_losses'], 3)
        self.assertAlmostEqual(step3['pending_loss_amount'], 80.4)
        self.assertFalse(app_module._martingale_status_payload(state)['active'])

    def test_martingale_status_reports_pending_losses_for_recovered_win(self):
        state = app_module._default_user_state()
        state['martingale_enabled'] = True
        state['martingale_levels'] = 7
        state['entry_value'] = 10.0

        mg = app_module._get_martingale_state(state)
        mg.update({
            'active': True,
            'level': 2,
            'recent_assets': ['EURUSD-OTC', 'GBPUSD-OTC'],
            'last_asset': 'GBPUSD-OTC',
            'last_amount': 22.0,
            'started_at': 123.0,
            'pending_losses': 2,
            'pending_loss_amount': 32.0,
        })

        payload = app_module._martingale_status_payload(state)
        self.assertTrue(payload['active'])
        self.assertEqual(payload['current_level'], 2)
        self.assertEqual(payload['pending_losses'], 2)
        self.assertAlmostEqual(payload['pending_loss_amount'], 32.0)
        self.assertGreater(payload['next_amount'], payload['base_entry'])

        app_module._reset_martingale_state(state)
        reset_payload = app_module._martingale_status_payload(state)
        self.assertEqual(reset_payload['pending_losses'], 0)
        self.assertAlmostEqual(reset_payload['pending_loss_amount'], 0.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
