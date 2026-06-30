import app


def _fake_user(name='admin'):
    return {'sub': name, 'role': 'master'}


def test_bot_config_persists_selected_patterns_without_manual_asset(monkeypatch):
    username = 'pattern-user'
    st = app.get_user_state(username)
    st['selected_catalog_patterns_candles'] = []
    st['selected_catalog_patterns_cores'] = []
    st['selected_candle_patterns'] = []
    st['modo_operacao'] = 'auto'
    st['bot_selector_mode'] = 'auto_robot'
    st['asset_selector_mode'] = 'auto'
    st['manual_only_mode'] = False

    monkeypatch.setattr(app, 'current_user', lambda: _fake_user(username))
    client = app.app.test_client()

    payload = {
        'modo_operacao': 'auto',
        'bot_selector_mode': 'auto_robot',
        'asset_selector_mode': 'auto',
        'selected_catalog_patterns_candles': ['cndl_martelo', 'cndl_enforcado'],
        'selected_catalog_patterns_cores': ['seq_p01'],
        'selected_candle_patterns': ['cndl_martelo', 'cndl_enforcado', 'seq_p01'],
        'confluence_enabled': False,
        'strategies': {'ma': True, 'rsi': True},
    }
    resp = client.post('/api/bot/config', json=payload)
    data = resp.get_json()

    assert resp.status_code == 200
    assert data['ok'] is True
    assert 'Selecione um ativo manual' not in ' | '.join(data.get('changes', []))
    assert st['selected_catalog_patterns_candles'] == ['cndl_martelo', 'cndl_enforcado']
    assert st['selected_catalog_patterns_cores'] == ['seq_p01']
    assert set(st['selected_candle_patterns']) == {'cndl_martelo', 'cndl_enforcado', 'seq_p01'}
    assert st['confluence_enabled'] is False
    # Estratégias ficam zeradas quando confluência está desligada.
    assert any(st['strategies'].values()) is False
