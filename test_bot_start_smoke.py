import app


def _fake_user(name='admin'):
    return {'sub': name, 'role': 'master'}


class _FakeThread:
    def __init__(self, *args, **kwargs):
        self._alive = False
        self.target = kwargs.get('target')
        self.kwargs = kwargs.get('kwargs', {})

    def start(self):
        self._alive = True

    def is_alive(self):
        return self._alive


def test_bot_start_smoke_without_manual_asset(monkeypatch):
    username = 'start-smoke-user'
    st = app.get_user_state(username)
    st.update(app._default_user_state())
    st['broker_email'] = None
    st['broker_password'] = None
    st['selected_catalog_patterns_candles'] = ['cndl_martelo']
    st['selected_candle_patterns'] = ['cndl_martelo']

    monkeypatch.setattr(app, 'current_user', lambda: _fake_user(username))
    monkeypatch.setattr(app.threading, 'Thread', _FakeThread)
    monkeypatch.setattr(app, '_run_backtest_for_user', lambda *a, **k: (False, 'debounced'))
    monkeypatch.setattr(app, '_resync_live_broker_state', lambda *a, **k: False)

    client = app.app.test_client()
    resp = client.post('/api/bot/start', json={
        'modo_operacao': 'auto',
        'bot_selector_mode': 'auto_robot',
        'asset_selector_mode': 'auto',
        'selected_catalog_patterns_candles': ['cndl_martelo'],
        'selected_candle_patterns': ['cndl_martelo'],
        'confluence_enabled': False,
        'strategies': {'ma': True, 'rsi': True},
        'selected_asset': 'AUTO',
    })
    data = resp.get_json()

    assert resp.status_code == 200
    assert data['ok'] is True
    assert st['running'] is True
    assert st['manual_only_mode'] is False
    assert st['selected_asset'] == 'AUTO'
    assert st['bot_selector_mode'] == 'auto_robot'
