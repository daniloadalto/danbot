import time

import app


def _fake_user(name='admin'):
    return {'sub': name, 'role': 'master'}


class _DeadThread:
    def is_alive(self):
        return False


def test_sync_user_bot_running_state_preserves_boot_window():
    username = 'boot-window-user'
    st = app.get_user_state(username)
    st['running'] = True
    st['_bot_thread_starting'] = True
    st['_bot_thread_start_ts'] = time.time()
    app._USER_THREADS[username] = _DeadThread()

    alive = app._sync_user_bot_running_state(username)

    assert alive is True
    assert st['running'] is True
    assert st['_bot_thread_starting'] is True


def test_manual_choice_is_valid_accepts_auto_mode_with_patterns():
    st = app._default_user_state()
    st['modo_operacao'] = 'auto'
    st['bot_selector_mode'] = 'auto_robot'
    st['selected_catalog_patterns_candles'] = ['cndl_martelo']
    st['selected_catalog_patterns_cores'] = []
    st['selected_candle_patterns'] = ['cndl_martelo']

    ok, msg = app._manual_choice_is_valid(st)

    assert ok is True
    assert msg == ''
    assert st['selected_asset'] == 'AUTO'
    assert st['manual_only_mode'] is False
    assert st['bot_selector_mode'] == 'auto_robot'


def test_set_asset_uses_auto_user_pool_instead_of_manual(monkeypatch):
    username = 'asset-pool-user'
    st = app.get_user_state(username)
    st['selected_asset'] = 'AUTO'
    st['bot_selector_mode'] = 'auto_robot'
    st['manual_only_mode'] = False

    monkeypatch.setattr(app, 'current_user', lambda: _fake_user(username))
    client = app.app.test_client()

    resp = client.post('/api/bot/set-asset', json={'selected_asset': 'EURUSD-OTC'})
    data = resp.get_json()

    assert resp.status_code == 200
    assert data['ok'] is True
    assert st['selected_asset'] == 'AUTO'
    assert st['modo_operacao'] == 'ambos'
    assert st['bot_selector_mode'] == 'auto_user'
    assert st['asset_selector_mode'] == 'manual'
    assert st['manual_only_mode'] is False
    assert st['user_asset_pool'] == ['EURUSD-OTC']
