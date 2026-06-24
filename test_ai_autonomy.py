import pathlib
import time
import app as appmod


def build_fake_profile(asset, best_pattern, wr, quality, regime='smooth_trend', preferred=True):
    return {
        'asset': asset,
        'overall_wr': wr,
        'market_quality_score': quality,
        'market_quality_preferred': preferred,
        'trend_continuity': 0.72,
        'padroes_ativos': [best_pattern, 'martelo', 'sequencia GGGG'],
        'top_patterns': [
            {'nome': best_pattern},
            {'nome': 'engolfo alta'},
            {'nome': 'sequencia RRRR'},
        ],
        'strategies_override': {
            'i3wr': True,
            'ma': True,
            'rsi': True,
            'bb': False,
            'macd': True,
            'simple_trend': True,
            'pullback_m5': True,
            'pullback_m15': False,
            'dead': True,
            'reverse': regime == 'range',
        },
        'confluencia_minima': 3,
        'best_pattern': best_pattern,
        'best_pattern_wr': wr,
        'trend_label': 'Alta forte',
        'market_quality_regime': regime,
    }


def run_tests():
    state = appmod._default_user_state()
    assert state['ai_autonomy_enabled'] is False
    assert state['ai_autonomy_profile'] == 'balanced'
    assert state['ai_autonomy_status'] == 'off'

    ranked = [
        {'asset': 'EURUSD-OTC', 'win_rate': 73.0, 'ops': 22},
        {'asset': 'GBPUSD-OTC', 'win_rate': 69.0, 'ops': 18},
        {'asset': 'USDJPY-OTC', 'win_rate': 67.0, 'ops': 17},
    ]

    fake_profiles = {
        'EURUSD-OTC': build_fake_profile('EURUSD-OTC', 'martelo', 73.0, 87),
        'GBPUSD-OTC': build_fake_profile('GBPUSD-OTC', 'engolfo alta', 69.0, 82),
        'USDJPY-OTC': build_fake_profile('USDJPY-OTC', 'sequencia RRRR', 67.0, 78, regime='range', preferred=False),
    }

    original_get_profile = appmod.get_asset_profile
    original_run_backtest = appmod.run_backtest
    try:
        appmod.get_asset_profile = lambda asset, force_refresh=False, timeframe=60: fake_profiles[asset]
        appmod.run_backtest = lambda assets, candles_per_window=70, windows=6, min_win_rate=10.0: {'ranked': ranked}

        state['ai_autonomy_enabled'] = True
        state['ai_autonomy_profile'] = 'balanced'
        state['trade_timeframe'] = 60

        plan = appmod._build_ai_autonomy_plan('tester', state, ranked_override=ranked)
        assert plan['assets'][:2] == ['EURUSD-OTC', 'GBPUSD-OTC']
        assert plan['best_asset'] == 'EURUSD-OTC'
        assert all(str(a).endswith('-OTC') for a in plan['assets'])
        assert 'cndl_martelo' in plan['candle_patterns']
        assert 'seq_p02' in plan['core_patterns']
        assert plan['strategies']['i3wr'] is True
        assert plan['min_confluence'] >= 2

        appmod._apply_ai_autonomy_plan(state, plan, reason='unit-test')
        assert state['modo_operacao'] == 'auto'
        assert state['bot_selector_mode'] == 'auto_user'
        assert state['asset_selector_mode'] == 'manual'
        assert state['user_asset_pool'][0] == 'EURUSD-OTC'
        assert 'cndl_martelo' in state['selected_catalog_patterns_candles']
        assert 'seq_p02' in state['selected_catalog_patterns_cores']
        assert state['ai_autonomy_status'] == 'active'
        assert state['selected_candle_patterns']
        assert state['asset_market_filter'] == 'otc'
        assert state['bt_scope'] == 'otc'
        assert state['ai_autonomy_log']

        payload = appmod._refresh_ai_autonomy_plan('tester', state, reason='unit-refresh', force_backtest=True)
        assert payload['enabled'] is True
        assert payload['plan']['best_asset'] == 'EURUSD-OTC'
        assert payload['plan']['assets']
        assert payload['operating_config']['trade_timeframe'] == 60
        assert payload['long_log']

        client = appmod.app.test_client()
        with client as c:
            lr = c.post('/api/login', json={'username': 'admin', 'password': 'danbot@master2025'})
            assert lr.status_code == 200
            admin_state = appmod.get_user_state('admin')
            admin_state.clear()
            admin_state.update(appmod._default_user_state())
            admin_state['ai_autonomy_enabled'] = True
            admin_state['ai_autonomy_profile'] = 'balanced'
            admin_state['_bt_ranked'] = ranked[:]
            admin_state['_bt_top_assets'] = ['EURUSD-OTC', 'GBPUSD-OTC', 'USDJPY-OTC']
            appmod._refresh_ai_autonomy_plan('admin', admin_state, reason='chat-test', force_backtest=False)

            r1 = c.post('/api/ai/chat', json={'message': 'entrada 5'})
            assert r1.status_code == 200
            assert 'Patrão' in r1.get_json()['reply']
            assert appmod.get_user_state('admin')['entry_value'] == 5.0

            r2 = c.post('/api/ai/chat', json={'message': 'timeframe m5'})
            assert r2.status_code == 200
            assert appmod.get_user_state('admin')['trade_timeframe'] == 300

            r3 = c.post('/api/ai/chat', json={'message': 'stop loss 25'})
            assert r3.status_code == 200
            assert appmod.get_user_state('admin')['stop_loss'] == 25.0

            r4 = c.post('/api/ai/chat', json={'message': 'stop gain 70'})
            assert r4.status_code == 200
            assert appmod.get_user_state('admin')['stop_win'] == 70.0

            r5 = c.post('/api/ai/chat', json={'message': 'por que trocou de ativo?'})
            assert r5.status_code == 200
            assert r5.get_json()['ok'] is True

            r6 = c.post('/api/ai/chat', json={'message': 'confluencia 5'})
            assert r6.status_code == 200
            assert appmod.get_user_state('admin')['min_confluence'] == 5

            r7 = c.post('/api/ai/chat', json={'message': 'remover padrão martelo'})
            assert r7.status_code == 200
            assert 'cndl_martelo' not in appmod.get_user_state('admin')['selected_catalog_patterns_candles']

            r8 = c.post('/api/ai/chat', json={'message': 'adicionar padrão martelo'})
            assert r8.status_code == 200
            assert 'cndl_martelo' in appmod.get_user_state('admin')['selected_catalog_patterns_candles']

            r9 = c.post('/api/ai/chat', json={'message': 'explique plano'})
            assert r9.status_code == 200
            assert 'Conflu' in r9.get_json()['reply'] or 'Melhor ativo' in r9.get_json()['reply']

            before_conf = appmod.get_user_state('admin')['min_confluence']
            r10 = c.post('/api/ai/chat', json={'message': 'trabalhe mais defensivamente'})
            assert r10.status_code == 200
            assert appmod.get_user_state('admin')['ai_autonomy_profile'] == 'safe'
            assert appmod.get_user_state('admin')['min_confluence'] >= before_conf

            r11 = c.post('/api/ai/chat', json={'message': 'trocar cesta'})
            assert r11.status_code == 200
            assert appmod.get_user_state('admin')['ai_autonomy_last_refresh_reason'] in ('chat_swap_basket', 'chat_swap_basket_fallback')

            r12 = c.post('/api/ai/chat', json={'message': 'reduza agressividade após 2 losses'})
            assert r12.status_code == 200
            assert appmod.get_user_state('admin')['ai_reduce_aggressiveness_after_2_losses'] is True

            r13 = c.post('/api/ai/chat', json={'message': 'se bater meta me avise e pare'})
            assert r13.status_code == 200
            assert appmod.get_user_state('admin')['ai_auto_stop_on_goal_hit'] is True

            r14 = c.post('/api/ai/chat', json={'message': 'se tomar 4 losses pare sozinho'})
            assert r14.status_code == 200
            assert appmod.get_user_state('admin')['ai_auto_stop_on_loss_streak'] == 4

            r15 = c.post('/api/ai/chat', json={'message': 'status'})
            assert r15.status_code == 200
            assert 'auto-stop meta=' in r15.get_json()['reply']
            assert 'auto-stop por losses=' in r15.get_json()['reply']

            admin_state['ai_autonomy_profile'] = 'aggressive'
            admin_state['min_confluence'] = 4
            admin_state['manual_only_mode'] = False
            admin_state['consecutive_losses'] = 2
            assert appmod._handle_consecutive_loss_reassessment('admin', admin_state) is True
            assert appmod.get_user_state('admin')['ai_autonomy_profile'] in ('balanced', 'safe')
            assert appmod.get_user_state('admin')['min_confluence'] >= 5

            admin_state['wins'] = 2
            admin_state['losses'] = 5
            admin_state['consecutive_losses'] = 3
            admin_state['profit'] = -22.0
            admin_state['stop_loss'] = 25.0
            advice = appmod._maybe_emit_ai_score_advice(admin_state, username='admin', force=False)
            assert 'Patrão' in advice
            assert '3 losses' in advice or '3 losses seguidos' in advice
            assert (admin_state.get('ai_latest_advice', {}) or {}).get('msg', '').startswith('Patrão')

            admin_state['running'] = True
            admin_state['profit'] = 70.0
            admin_state['stop_win'] = 70.0
            admin_state['ai_auto_stop_on_goal_hit'] = True
            assert appmod._maybe_trigger_ai_auto_stop('admin', admin_state, origin='unit-goal') is True
            assert admin_state['running'] is False
            assert 'Patrão' in (admin_state.get('ai_latest_advice', {}) or {}).get('msg', '')
            assert any('meta batida' in str(item.get('msg', '')).lower() for item in admin_state.get('ai_autonomy_history', []))

            admin_state['running'] = True
            admin_state['profit'] = -25.0
            admin_state['stop_loss'] = 25.0
            assert appmod._maybe_trigger_core_stop_pause('admin', admin_state) is True
            assert admin_state['running'] is False
            assert 'stop loss' in str((admin_state.get('ai_latest_advice', {}) or {}).get('msg', '')).lower()
            assert any('stop loss configurado' in str(item.get('msg', '')).lower() for item in admin_state.get('ai_autonomy_history', []))
            assert any(item.get('kind') == 'user' for item in appmod.get_user_state('admin').get('ai_autonomy_log', []))

        html = pathlib.Path('/home/user/danbot_repo/templates/dashboard.html').read_text()
        assert 'tab-ai-autonomy' in html
        assert 'btn-ai-autonomy-enable' in html
        assert 'toggleAiAutonomy(true)' in html
        assert 'ai-autonomy-log' in html
        assert 'ai-chat-input' in html
        assert 'sendAiChatMessage()' in html
        assert 'ai-autonomy-advice' in html
        assert 'auto-stop meta ON' in html
        assert 'auto-stop na meta ligado' in html
        print('AI_AUTONOMY_TEST_OK')
        print('BEST_ASSET', payload['plan']['best_asset'])
        print('POOL', ','.join(payload['plan']['assets']))
        print('PATTERNS', len(payload['plan']['candle_patterns']) + len(payload['plan']['core_patterns']))
    finally:
        appmod.get_asset_profile = original_get_profile
        appmod.run_backtest = original_run_backtest


if __name__ == '__main__':
    run_tests()
