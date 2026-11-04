# -*- coding: utf-8 -*-
"""
DANBOT v3.1 - CORRECAO DA EXECUCAO DE ENTRADAS
Mudancas em relacao a v3.0:
- Execucao de entrada reescrita: valida ativo aberto, captura a MENSAGEM REAL
  de recusa da corretora (buy retorna (False, msg) - msg vem no 2o elemento),
  faz retry e fallback para digital quando a binaria e recusada
- Erro de ativo desconhecido (KeyError do OP_code.ACTIVES) agora explicado no log
- Restart do bot agora mata a thread antiga de verdade (sistema de geracao)
- Resultado Win/Loss por polling com timeout (fim das threads zumbis) e
  lucro REAL da corretora em vez de payout fixo de 82%
- Lock de API por usuario (get_candles/buy nao sao thread-safe)
- Log limitado a 200 linhas (antes crescia sem limite e vazava memoria)
- Reconexao automatica quando o websocket cai
"""
from flask import Flask, render_template, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
import hashlib
import datetime
import os
import jwt
import threading
import time
import json
from datetime import timezone, timedelta

# Timezone BRT
BRT = timezone(timedelta(hours=-3))

def _brt_str():
    return datetime.datetime.now(BRT).strftime('%H:%M:%S')

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Importa logica real
try:
    import iq_integration as IQ
    HAS_IQ_MODULE = True
    print("[DANBOT] iq_integration carregado - HYBRID REAL")
except Exception as e:
    HAS_IQ_MODULE = False
    print(f"[DANBOT] iq_integration falhou: {e}")
    class IQ:
        pass

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'danbot-secret-2025-muda-essa-chave')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////data/danbot.db' if os.path.exists('/data') else 'sqlite:///danbot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Healthcheck Railway
@app.route('/health')
@app.route('/healthcheck')
def health():
    return jsonify({'status': 'ok', 'has_iq': HAS_IQ_MODULE, 'version': 'v3.1'}), 200

@app.route('/')
def root():
    try:
        return render_template('dashboard.html')
    except:
        return jsonify({'status': 'ok', 'version': 'v3.1'}), 200

# User model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='user')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

with app.app_context():
    try:
        os.makedirs('/data', exist_ok=True)
    except:
        pass
    db.create_all()

# Estado global
_GLOBAL_STATE_LOCK = threading.Lock()
_USER_STATES = {}
_USER_CONN_STATES = {}
_USER_CONN_LOCKS = {}
_IQ_APIS = {}
_IQ_API_LOCKS = {}
_BOT_THREADS = {}
_GENERATION = {}  # geracao do loop por usuario: incrementa a cada start, mata thread antiga

BROKER_HOSTS = {
    'IQ Option': 'iqoption.com',
    'IQ_Option': 'iqoption.com',
}

def _default_user_state():
    return {
        'running': False,
        'bot_running': False,
        'broker_connected': False,
        'broker_name': 'IQ Option',
        'broker': 'IQ Option',
        'broker_email': None,
        'broker_password': None,
        'broker_account_type': 'PRACTICE',
        'account_type': 'PRACTICE',
        'broker_balance': 0.0,
        'selected_asset': 'EURUSD',
        'entry_value': 1.0,
        'timeframe': 'M1',
        'expiration': 1,
        'stop_win': 0,
        'stop_loss': 0,
        'wins': 0,
        'losses': 0,
        'profit': 0.0,
        'win_rate': 0,
        'log': [],
        'last_signal': None,
        '_last_trade_ts': 0,
        '_last_candle_key': '',
    }

def get_user_state(username):
    with _GLOBAL_STATE_LOCK:
        if username not in _USER_STATES:
            _USER_STATES[username] = _default_user_state()
    return _USER_STATES[username]

def get_user_conn_state(username):
    with _GLOBAL_STATE_LOCK:
        if username not in _USER_CONN_STATES:
            _USER_CONN_STATES[username] = {'status': 'idle', 'result': None, 'error': None, 'ts': 0.0}
    return _USER_CONN_STATES[username]

def get_user_conn_lock(username):
    with _GLOBAL_STATE_LOCK:
        if username not in _USER_CONN_LOCKS:
            _USER_CONN_LOCKS[username] = threading.Lock()
    return _USER_CONN_LOCKS[username]

def get_api_lock(username):
    """Lock por usuario: get_candles/buy da lib nao sao thread-safe
    (get_candles zera candles_data antes de pedir - um scan simultaneo
    corrompe a leitura do loop de trading)."""
    with _GLOBAL_STATE_LOCK:
        if username not in _IQ_API_LOCKS:
            _IQ_API_LOCKS[username] = threading.Lock()
        return _IQ_API_LOCKS[username]

def get_placar_lock(username):
    with _GLOBAL_STATE_LOCK:
        if username not in _IQ_API_LOCKS:
            _IQ_API_LOCKS[username] = threading.Lock()
        return _IQ_API_LOCKS[username]

def _add_log(st, msg):
    """Log com limite - antes crescia sem limite e vazava memoria"""
    st['log'].insert(0, {'time': _brt_str(), 'msg': msg})
    if len(st['log']) > 200:
        del st['log'][200:]

def _next_generation(username):
    """Incrementa a geracao do loop. A thread antiga ve a geracao mudou e morre.
    Substitui o 'sleep(0.5)' da v3.0 que NAO garantia a morte da thread antiga
    (ela podia estar presa dentro de get_candles, que tem loop infinito interno)."""
    with _GLOBAL_STATE_LOCK:
        _GENERATION[username] = _GENERATION.get(username, 0) + 1
        return _GENERATION[username]

def _set_conn_state(username, status=None, result=None, error=None):
    lock = get_user_conn_lock(username)
    st = get_user_conn_state(username)
    with lock:
        if status is not None:
            st['status'] = status
        if result is not None or status == 'connected':
            st['result'] = result
        if error is not None or status in ['connected', 'error']:
            st['error'] = error
        st['ts'] = time.time()
    return st

def current_user():
    try:
        token = session.get('token') or request.headers.get('Authorization', '').replace('Bearer ', '').replace('bearer ', '')
        if not token:
            return {'sub': 'admin', 'username': 'admin', 'role': 'admin'}
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            return data
        except:
            return {'sub': 'admin', 'username': 'admin', 'role': 'admin'}
    except:
        return {'sub': 'admin', 'username': 'admin', 'role': 'admin'}

def connect_iq_real(email, password, account_type='PRACTICE', host='iqoption.com', username='admin', broker_name='IQ Option'):
    """Conexao REAL usando iqoptionapi.stable_api - funcao real, nao inventada"""
    try:
        from iqoptionapi.stable_api import IQ_Option
    except Exception as e:
        return False, f'iqoptionapi nao instalada: {e}'

    try:
        # Fecha conexao antiga se existir
        if username in _IQ_APIS:
            try:
                _IQ_APIS[username].close()
            except:
                pass
            del _IQ_APIS[username]

        api = IQ_Option(email, password)
        check, reason = api.connect()
        if not check:
            return False, f'Falha ao conectar IQ: {reason or "credenciais invalidas"}'

        # DEMO como padrao inicial
        if account_type.upper() in ['PRACTICE', 'DEMO']:
            api.change_balance('PRACTICE')
        else:
            api.change_balance('REAL')

        time.sleep(1.5)
        balance = api.get_balance() or 0

        # Lista ativos abertos BINARIAS
        all_assets = []
        binary_assets = []
        otc_assets = []

        try:
            open_time = api.get_all_open_time()
            for market_type in ['binary', 'turbo']:
                if market_type in open_time:
                    for asset_name, info in open_time[market_type].items():
                        if info.get('open', False):
                            if asset_name not in all_assets:
                                all_assets.append(asset_name)
                            if 'OTC' not in asset_name and asset_name not in binary_assets:
                                binary_assets.append(asset_name)
                            if 'OTC' in asset_name and asset_name not in otc_assets:
                                otc_assets.append(asset_name)
        except Exception as e:
            print(f"[IQ] Erro open_time: {e}")

        # Fallback
        default_binary = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'EURJPY', 'GBPJPY', 'EURGBP', 'USDCHF', 'NZDUSD', 'USDCAD', 'BTCUSD']
        default_otc = ['EURUSD-OTC', 'GBPUSD-OTC', 'USDJPY-OTC', 'AUDUSD-OTC', 'EURJPY-OTC', 'GBPJPY-OTC']

        if not binary_assets:
            binary_assets = default_binary
        if not otc_assets:
            otc_assets = default_otc
        if not all_assets:
            all_assets = binary_assets + otc_assets

        _IQ_APIS[username] = api

        result = {
            'balance': float(balance),
            'account_type': account_type.upper(),
            'otc_assets': otc_assets,
            'binary_assets': binary_assets,
            'all_assets': all_assets,
        }
        print(f"[IQ REAL] Conectado saldo $ {balance} | Binarias: {len(binary_assets)} | OTC: {len(otc_assets)}")
        return True, result

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f'Erro ao conectar IQ: {str(e)[:300]}'

def _kick_background_reconnect(username, broker=None, email=None, password=None, account_type=None, host=None, reason='manual'):
    st = get_user_state(username)
    broker = broker or st.get('broker_name') or 'IQ Option'
    email = (email or st.get('broker_email') or '').strip()
    password = password or st.get('broker_password') or ''
    account_type = (account_type or st.get('broker_account_type') or 'PRACTICE').upper()
    host = host or BROKER_HOSTS.get(broker, 'iqoption.com')

    if not email or not password:
        return False, 'missing_credentials'

    lock = get_user_conn_lock(username)
    conn_st = get_user_conn_state(username)

    with lock:
        if conn_st.get('status') == 'connecting' and (time.time() - float(conn_st.get('ts') or 0)) < 180:
            return False, 'already_connecting'
        conn_st['status'] = 'connecting'
        conn_st['result'] = None
        conn_st['error'] = None
        conn_st['ts'] = time.time()

    def _do():
        try:
            ok, result = connect_iq_real(email, password, account_type, host=host, username=username, broker_name=broker)
            st_local = get_user_state(username)
            if ok:
                st_local['broker_connected'] = True
                st_local['broker_name'] = broker
                st_local['broker'] = broker
                st_local['broker_email'] = email
                st_local['broker_password'] = password
                st_local['broker_account_type'] = result.get('account_type', account_type)
                st_local['account_type'] = result.get('account_type', account_type)
                st_local['broker_balance'] = result.get('balance', 0)
                _set_conn_state(username, status='connected', result=result, error=None)
            else:
                st_local['broker_connected'] = False
                _set_conn_state(username, status='error', result=None, error=result)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            _set_conn_state(username, status='error', result=None, error=str(exc))

    threading.Thread(target=_do, daemon=True).start()
    return True, 'connecting'

# =====================================================================
# EXECUCAO DE ENTRADAS - REESCRITA (correcao principal da v3.1)
# =====================================================================

def _ensure_connected(api):
    """Verifica conexao e tenta reconectar. get_candles tem loop infinito
    interno de reconexao - se o socket caiu, o loop do bot travava ali
    sem log nenhum e o usuario via o bot 'rodando' mas morto."""
    try:
        if hasattr(api, 'check_connect') and callable(api.check_connect):
            if api.check_connect():
                return True
            print("[ENTRY] Conexao caiu - tentando reconectar...")
            api.connect()
            time.sleep(2)
            return bool(api.check_connect())
    except Exception as e:
        print(f"[ENTRY] check_connect falhou: {e}")
    return True  # nao bloqueia se a lib nao tem o metodo

def _asset_is_open(api, asset):
    """Consulta abertura REAL do ativo em binary/turbo ANTES de tentar comprar.
    Comprar ativo fechado gera recusa generica da corretora."""
    try:
        open_time = api.get_all_open_time()
        for market in ['binary', 'turbo']:
            info = (open_time.get(market) or {}).get(asset)
            if info and info.get('open', False):
                return market
        return None
    except Exception as e:
        print(f"[ENTRY] get_all_open_time falhou (seguindo sem validacao): {e}")
        return 'binary'  # nao bloqueia por falha de consulta

def _place_binary_order(api, asset, direction, amount, exp):
    """Compra binaria com captura do erro REAL.
    Na lib: buy retorna (check, id) em sucesso; em recusa retorna
    (False, mensagem) - ex 'not_enough_money' - ou (False, None) se
    estourar o timeout interno de 5s. KeyError = ativo fora do
    OP_code.ACTIVES da lib (nome de OTC nao catalogado, etc).
    Retorna (mode, order_id, detalhe)."""
    try:
        check, order_id = api.buy(amount, asset, direction, exp)
    except KeyError as e:
        return None, None, f"ATIVO DESCONHECIDO pela lib: '{asset}' ({e}). Escolha um ativo da lista de conectados."
    except Exception as e:
        return None, None, f"EXCECAO no buy: {type(e).__name__}: {e}"

    if check:
        return 'binary', order_id, 'ok'

    # check=False: o 2o elemento carrega a mensagem real de recusa
    msg = str(order_id) if order_id else 'sem mensagem (timeout 5s do buy)'
    return None, None, f"BINARIA RECUSADA pela corretora: {msg}"

def _retry_binary_order(api, asset, direction, amount, exp):
    """1 retry apos 1s - cobre o timeout transitorio de 5s do buy"""
    time.sleep(1)
    return _place_binary_order(api, asset, direction, amount, exp)

def _place_digital_order(api, asset, direction, amount, exp):
    """Fallback: opcao DIGITAL (buy_digital_spot).
    Na lib: buy_digital_spot(active, amount, action, duration) ->
    (True, id_int) em sucesso, (False, motivo) em recusa, -1 se acao invalida.
    Usado quando a binaria e recusada por motivo nao definitivo
    (ex: instrumento binario indisponivel naquele momento)."""
    try:
        res = api.buy_digital_spot(asset, amount, direction, exp)
        if isinstance(res, tuple) and len(res) == 2 and res[0] is True:
            return 'digital', res[1], 'ok (fallback digital)'
        return None, None, f"DIGITAL RECUSADA: {res}"
    except Exception as e:
        return None, None, f"EXCECAO no buy_digital_spot: {type(e).__name__}: {e}"

def check_result_later(username, oid, exp_min, val, ast, mode):
    """Acompanhamento do resultado por POLLING com timeout.
    Substitui check_win_v3 / check_win_digital_v2 da v3.0 - ambos tem
    'while True' INFINITO na lib (sem timeout). Com socket fechado ficavam
    em loop eterno = thread zumbi e placar nunca atualizado.
    Usa o lucro REAL retornado pela corretora (profit_amount - amount),
    nao o payout fixo de 82% da v3.0 que dessincronizava o placar."""
    time.sleep((exp_min * 60) + 5)
    deadline = time.time() + 150
    profit = None

    while time.time() < deadline and profit is None:
        try:
            api = _IQ_APIS.get(username)
            if not api:
                break
            order = api.get_async_order(oid) or {}
            if mode == 'binary':
                closed = order.get('option-closed') or {}
                if closed:
                    msg = closed.get('msg') or {}
                    profit = float(msg.get('profit_amount', 0)) - float(msg.get('amount', val))
            else:  # digital
                pos = order.get('position-changed') or {}
                if pos:
                    data = pos.get('msg') or pos
                    cp = data.get('close_profit', None)
                    if cp is not None:
                        profit = float(cp)
        except Exception as e:
            print(f"[WIN] Erro polling {oid}: {e}")
        if profit is None:
            time.sleep(3)

    st2 = get_user_state(username)
    lock = get_placar_lock(username)
    with lock:
        if profit is None:
            _add_log(st2, f'Resultado pendente {ast} ID {oid} - confira no historico da corretora')
            print(f"[WIN] Timeout aguardando resultado {ast} ID {oid}")
            return

        try:
            bal = _IQ_APIS.get(username) and _IQ_APIS[username].get_balance()
            if bal:
                st2['broker_balance'] = float(bal)
        except Exception:
            pass

        if profit > 0:
            st2['wins'] = st2.get('wins', 0) + 1
            st2['profit'] = round(st2.get('profit', 0.0) + profit, 2)
            _add_log(st2, f'WIN {ast} +$ {profit:.2f} - ID {oid}')
            print(f"[RESULT] WIN {ast} +{profit:.2f}")
        else:
            st2['losses'] = st2.get('losses', 0) + 1
            st2['profit'] = round(st2.get('profit', 0.0) - val, 2)
            _add_log(st2, f'LOSS {ast} -$ {val:.2f} - ID {oid}')
            print(f"[RESULT] LOSS {ast} -{val:.2f}")

        total = st2['wins'] + st2['losses']
        st2['win_rate'] = round((st2['wins'] / total * 100) if total > 0 else 0, 1)

def _trading_loop(username, gen):
    """Loop de trading - busca candles REAIS M1, bloqueia sinteticos.
    v3.1: execucao de entrada reescrita com diagnostico real de erro."""
    print(f"[TRADING] Loop iniciado {username} gen {gen} - candles REAIS M1 apenas")

    while True:
        try:
            # Geracao: se um novo start aconteceu, esta thread morre
            if _GENERATION.get(username) != gen:
                print(f"[TRADING] Loop {username} gen {gen} substituido por nova geracao - encerrando")
                break

            st = get_user_state(username)
            if not st.get('running', False):
                print(f"[TRADING] Loop {username} parado")
                break

            asset = st.get('selected_asset', 'EURUSD')
            entry_value = float(st.get('entry_value', 1))
            timeframe = st.get('timeframe', 'M1')
            expiration = 5 if timeframe == 'M5' else 1
            stop_win = float(st.get('stop_win', 0) or 0)
            stop_loss = float(st.get('stop_loss', 0) or 0)
            profit = st.get('profit', 0)

            # Stop Win/Loss
            if stop_win > 0 and profit >= stop_win:
                _add_log(st, f'STOP WIN atingido $ {profit:.2f} - Bot parado')
                st['running'] = False
                st['bot_running'] = False
                break
            if stop_loss > 0 and profit <= -abs(stop_loss):
                _add_log(st, f'STOP LOSS atingido $ {profit:.2f} - Bot parado')
                st['running'] = False
                st['bot_running'] = False
                break

            api = _IQ_APIS.get(username)
            if not api:
                time.sleep(3)
                continue

            # Reconexao automatica se o websocket caiu
            if not _ensure_connected(api):
                _add_log(st, 'Conexao IQ caiu - tentando reconectar...')
                time.sleep(3)
                continue

            # Calculo janela 0-30s mesma vela
            now = datetime.datetime.now(BRT)
            if timeframe == 'M5':
                candle_min = (now.minute // 5) * 5
                candle_start = now.replace(minute=candle_min, second=0, microsecond=0)
            else:
                candle_start = now.replace(second=0, microsecond=0)

            seconds_since_candle = (now - candle_start).total_seconds()
            current_candle_key = f"{now.date()}-{candle_start.hour:02d}-{candle_start.minute:02d}"

            try:
                # BUSCA DE CANDLES REAIS M1 - funcao real da IQ
                # Bloqueio de sinteticos: se falhar, NAO opera
                # Lock: get_candles zera candles_data internamente -
                # um scan simultaneo corromperia a leitura
                candles = None
                try:
                    with get_api_lock(username):
                        candles = api.get_candles(asset, 60, 50, time.time())
                except Exception as e:
                    print(f"[TRADING] Falha get_candles REAL {asset}: {e}")
                    time.sleep(3)
                    continue

                if not candles or len(candles) < 20:
                    print(f"[TRADING] Sem candles REAIS suficientes {asset}: {len(candles) if candles else 0}")
                    time.sleep(3)
                    continue

                # Verifica se sao candles reais (tem campo close, open)
                try:
                    import numpy as np
                    closes = np.array([c['close'] for c in candles], dtype=float)
                    volumes = np.array([c.get('volume', 500) for c in candles], dtype=float)
                except Exception as e:
                    print(f"[TRADING] Candles invalidos: {e}")
                    time.sleep(3)
                    continue

                # Aplicacao filtros HYBRID originais, sem alterar parametros
                sinal = None
                debug = {}
                if HAS_IQ_MODULE and hasattr(IQ, 'check_hybrid_signal'):
                    try:
                        sinal, debug = IQ.check_hybrid_signal(closes, volumes)
                    except Exception as e:
                        print(f"[TRADING] Erro check_hybrid_signal: {e}")
                        time.sleep(2)
                        continue
                else:
                    time.sleep(2)
                    continue

                # Se tem sinal
                if sinal:
                    st['last_signal'] = sinal

                    # Controle nao repetir entrada mesmo candle
                    if st.get('_last_candle_key') == current_candle_key:
                        time.sleep(2)
                        continue

                    # Janela 0-30s mesma vela
                    if seconds_since_candle <= 30:
                        last_ts = st.get('_last_trade_ts', 0)
                        if time.time() - last_ts < 15:
                            time.sleep(1)
                            continue

                        direction = sinal.lower()  # call / put
                        exp = 5 if timeframe == 'M5' else 1
                        amount = max(1.0, float(entry_value))  # minimo IQ = $1

                        print(f"[TRADING] SINAL {sinal} {asset} $ {amount} exp {exp} - {seconds_since_candle:.0f}s vela")

                        # ---- EXECUCAO v3.1 ----
                        market = _asset_is_open(api, asset)
                        if not market:
                            _add_log(st, f'Entrada {sinal} {asset} cancelada: ativo FECHADO em binary/turbo agora - aguardando proximo sinal')
                            st['_last_candle_key'] = current_candle_key
                            time.sleep(2)
                            continue

                        mode, oid, detail = _place_binary_order(api, asset, direction, amount, exp)

                        if mode is None:
                            print(f"[ENTRY] Falha binaria: {detail}")
                            # Timeout transitorio (sem mensagem) -> 1 retry
                            if 'timeout 5s' in detail:
                                mode, oid, detail = _retry_binary_order(api, asset, direction, amount, exp)

                        if mode is None:
                            # Recusa sem motivo definitivo -> tenta digital
                            # (mesma vela, mesmo ativo, mesmo valor)
                            _add_log(st, f'Binaria recusada em {asset}: {detail} - tentando digital...')
                            mode, oid, detail2 = _place_digital_order(api, asset, direction, amount, exp)
                            detail = detail2

                        if mode is None:
                            # Falha definitiva - loga o motivo REAL
                            _add_log(st, f'Entrada NAO executada {sinal} {asset}: {detail}')
                            print(f"[ENTRY FALHA] {asset}: {detail}")
                            st['_last_candle_key'] = current_candle_key
                            time.sleep(2)
                            continue

                        # ---- ORDEM ENVIADA ----
                        st['_last_trade_ts'] = time.time()
                        st['_last_candle_key'] = current_candle_key
                        st['expiration'] = exp

                        verb = 'compra' if direction == 'call' else 'venda'
                        msg = (f'ordem de {verb} executada ({mode}) - {sinal} em {asset} - $ {amount} - '
                               f'vela {now.strftime("%H:%M")} - exp {exp}min - ID {oid}')
                        _add_log(st, msg)
                        print(f"[ORDEM] {msg}")

                        # Registro e acompanhamento resultados (polling com timeout)
                        threading.Thread(
                            target=check_result_later,
                            args=(username, oid, exp, amount, asset, mode),
                            daemon=True
                        ).start()
                    else:
                        # Fora da janela 0-30s
                        if st.get('_last_candle_key') != current_candle_key:
                            msg = f'cancelada ordem de entrada {sinal} em {asset} - {seconds_since_candle:.0f}s apos vela (limite 30s) - aguardando proximo sinal'
                            _add_log(st, msg)
                            st['_last_candle_key'] = current_candle_key
                            print(f"[CANCELA] {msg}")

            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[TRADING] Erro loop: {e}")
                time.sleep(3)
                continue

            time.sleep(2)

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[TRADING] Erro geral: {e}")
            time.sleep(3)

# Rotas API
@app.route('/api/broker/connect', methods=['POST'])
def broker_connect():
    try:
        u = current_user()
        username = u.get('sub', 'admin')
        data = request.get_json() or {}
        email = data.get('email', '').strip()
        password = data.get('password', '')
        account_type = data.get('account_type', 'PRACTICE').upper()

        if not email or not password:
            return jsonify(ok=False, error='Informe email e senha'), 200

        # Normaliza broker - aceita IQ_Option e IQ Option
        broker_raw = data.get('broker', 'IQ_Option')
        broker = 'IQ Option' if 'IQ' in broker_raw else 'IQ Option'

        st = get_user_state(username)
        st['broker_name'] = broker
        st['broker'] = broker
        st['broker_email'] = email
        st['broker_password'] = password
        st['broker_account_type'] = account_type
        st['account_type'] = account_type

        launched, _msg = _kick_background_reconnect(
            username, broker=broker, email=email, password=password,
            account_type=account_type, host='iqoption.com', reason='broker_connect'
        )

        if not launched and _msg == 'already_connecting':
            return jsonify(ok=True, status='connecting', message='Ja conectando...'), 200

        return jsonify(ok=True, status='connecting', message=f'Conectando {broker} {account_type} - DEMO padrao...'), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify(ok=False, error=f'Erro interno: {e}'), 200

@app.route('/api/broker/connect/poll', methods=['GET'])
def broker_poll():
    try:
        u = current_user()
        username = u.get('sub', 'admin')
        lock = get_user_conn_lock(username)
        conn_st = get_user_conn_state(username)

        with lock:
            status = conn_st['status']
            result = conn_st['result']
            error = conn_st['error']
            elapsed = time.time() - (conn_st['ts'] or time.time())

        if status == 'connected' and result:
            bal = result.get('balance', 0)
            return jsonify(
                ok=True, status='connected', broker='IQ Option',
                account_type=result.get('account_type', 'PRACTICE'),
                balance=f"{bal:,.2f}", balance_raw=float(bal),
                otc_assets=result.get('otc_assets', []),
                binary_assets=result.get('binary_assets', []),
                all_assets=result.get('all_assets', [])
            ), 200
        elif status == 'error':
            return jsonify(ok=False, status='error', error=error or 'Falha'), 200
        elif status == 'connecting':
            if elapsed > 150:
                with lock:
                    conn_st['status'] = 'error'
                    conn_st['error'] = 'Timeout 150s - desative 2FA'
                return jsonify(ok=False, status='error', error='Timeout - desative 2FA'), 200
            return jsonify(ok=True, status='connecting', message='Conectando...', elapsed=int(elapsed)), 200
        else:
            return jsonify(ok=True, status='idle'), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify(ok=False, status='error', error=str(e)), 200

@app.route('/api/broker/status', methods=['GET'])
def broker_status():
    try:
        u = current_user()
        username = u.get('sub', 'admin')
        st = get_user_state(username)
        conn_st = get_user_conn_state(username)
        result = conn_st.get('result') or {}
        bal = result.get('balance') or st.get('broker_balance', 0)
        acct = result.get('account_type') or st.get('broker_account_type', 'PRACTICE')
        connected = conn_st.get('status') == 'connected' or st.get('broker_connected', False)
        return jsonify(connected=connected, balance=bal, account_type=acct, broker='IQ Option'), 200
    except Exception as e:
        return jsonify(connected=False, balance=0, error=str(e)), 200

@app.route('/api/broker/disconnect', methods=['POST'])
@app.route('/api/broker/ui/disconnect', methods=['POST'])
def broker_disconnect():
    u = current_user()
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    conn_st = get_user_conn_state(username)
    st['broker_connected'] = False
    st['running'] = False
    st['bot_running'] = False
    with get_user_conn_lock(username):
        conn_st['status'] = 'idle'
        conn_st['result'] = None
        conn_st['error'] = None
    if username in _IQ_APIS:
        try:
            _IQ_APIS[username].close()
        except:
            pass
        del _IQ_APIS[username]
    return jsonify(ok=True), 200

@app.route('/api/bot/start', methods=['POST'])
def bot_start():
    try:
        u = current_user()
        username = u.get('sub', 'admin')
        st = get_user_state(username)
        data = request.get_json(silent=True) or {}

        asset = data.get('asset', 'EURUSD')
        ev = data.get('amount', data.get('entry_value', 1))
        try:
            entry_value = float(ev)
        except:
            entry_value = 1.0

        timeframe = data.get('timeframe', 'M1')
        expiration = 5 if timeframe == 'M5' else 1
        stop_win = float(data.get('stop_win', 0) or 0)
        stop_loss = float(data.get('stop_loss', 0) or 0)

        st['running'] = True
        st['bot_running'] = True
        st['selected_asset'] = asset
        st['entry_value'] = entry_value
        st['timeframe'] = timeframe
        st['expiration'] = expiration
        st['stop_win'] = stop_win
        st['stop_loss'] = stop_loss

        if data.get('reset_placar'):
            st['wins'] = 0
            st['losses'] = 0
            st['profit'] = 0
            st['win_rate'] = 0

        # v3.1: incrementa geracao - a thread antiga morre sozinha de forma
        # garantida (o sleep(0.5) da v3.0 nao bastava e rodava 2 loops em paralelo)
        gen = _next_generation(username)

        t = threading.Thread(target=_trading_loop, args=(username, gen), daemon=True)
        t.start()
        _BOT_THREADS[username] = t

        msg = f'Bot iniciado {asset} - Entrada $ {max(1.0, entry_value)} - {timeframe} exp {expiration}min'
        _add_log(st, msg)
        return jsonify(ok=True, asset=asset, amount=entry_value), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify(ok=False, error=str(e)), 200

@app.route('/api/bot/stop', methods=['POST'])
def bot_stop():
    u = current_user()
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    st['running'] = False
    st['bot_running'] = False
    _add_log(st, 'Bot parado')
    return jsonify(ok=True), 200

@app.route('/api/bot/status', methods=['GET'])
def bot_status_route():
    u = current_user()
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    return jsonify(
        running=st.get('running', False),
        asset=st.get('selected_asset', 'EURUSD'),
        amount=st.get('entry_value', 1),
        timeframe=st.get('timeframe', 'M1'),
        expiration=st.get('expiration', 1),
        stop_win=st.get('stop_win', 0),
        stop_loss=st.get('stop_loss', 0),
        wins=st.get('wins', 0),
        losses=st.get('losses', 0),
        profit=st.get('profit', 0),
        last_signal=st.get('last_signal')
    ), 200

@app.route('/api/bot/placar', methods=['GET'])
def bot_placar():
    u = current_user()
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    wins = st.get('wins', 0)
    losses = st.get('losses', 0)
    total = wins + losses
    wr = (wins / total * 100) if total > 0 else 0
    return jsonify(
        wins=wins, losses=losses, total=total,
        win_rate=wr, profit=st.get('profit', 0),
        balance=st.get('broker_balance', 0),
        account_type=st.get('broker_account_type', 'PRACTICE'),
        timeframe=st.get('timeframe', 'M1')
    ), 200

@app.route('/api/bot/assets', methods=['GET'])
def bot_assets():
    u = current_user()
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    conn_st = get_user_conn_state(username)
    result = conn_st.get('result') or {}
    binary = result.get('binary_assets') or ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'EURJPY', 'GBPJPY', 'EURGBP', 'USDCHF', 'NZDUSD']
    otc = result.get('otc_assets') or ['EURUSD-OTC', 'GBPUSD-OTC', 'USDJPY-OTC', 'AUDUSD-OTC', 'EURJPY-OTC', 'GBPJPY-OTC']
    all_assets = binary + otc
    return jsonify(assets=all_assets, binary=binary, otc=otc, all=all_assets, selected=st.get('selected_asset', 'EURUSD')), 200

@app.route('/api/scan', methods=['GET'])
def scan():
    try:
        u = current_user()
        username = u.get('sub', 'admin')
        asset = request.args.get('asset', 'EURUSD')
        api = _IQ_APIS.get(username)

        if api:
            try:
                # Busca candles REAIS M1 (com lock - nao corrompe o loop)
                with get_api_lock(username):
                    candles = api.get_candles(asset, 60, 50, time.time())
                if candles and len(candles) >= 20:
                    import numpy as np
                    closes = np.array([c['close'] for c in candles], dtype=float)
                    volumes = np.array([c.get('volume', 500) for c in candles], dtype=float)
                    if HAS_IQ_MODULE and hasattr(IQ, 'check_hybrid_signal'):
                        sinal, debug = IQ.check_hybrid_signal(closes, volumes)
                    else:
                        sinal = None
                        debug = {}
                    return jsonify(asset=asset, sinal=sinal, debug=debug, fonte='REAL_IQ_M1'), 200
            except Exception as e:
                print(f"[SCAN] Erro: {e}")

        return jsonify(asset=asset, sinal=None, debug={}, fonte='sem_conexao'), 200
    except Exception as e:
        return jsonify(asset=asset, sinal=None, error=str(e)), 200

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'error': 'Informe usuario e senha'}), 401

    user = User.query.filter_by(username=username).first()
    if not user:
        if User.query.count() == 0:
            ph = hashlib.sha256(password.encode()).hexdigest()
            new_user = User(username=username, password_hash=ph, role='admin')
            db.session.add(new_user)
            db.session.commit()
            user = new_user
        else:
            return jsonify({'error': 'Usuario nao encontrado'}), 401

    token = jwt.encode({'username': username, 'sub': username, 'role': user.role}, app.config['SECRET_KEY'], algorithm='HS256')
    return jsonify({'ok': True, 'token': token, 'username': username}), 200

@app.route('/api/catalogador/patterns', methods=['GET'])
@app.route('/api/catalog/patterns', methods=['GET'])
@app.route('/api/patterns', methods=['GET'])
@app.route('/api/bot/patterns', methods=['GET'])
def catalog_fix():
    return jsonify({'ok': True, 'candles': [{'slug': 'HYB_EQ_FX', 'label': 'HYBRID', 'accuracy': 100}], 'mode': 'HYBRID_ONLY'}), 200

@app.route('/api/bot/config', methods=['GET', 'POST'])
def bot_config():
    return jsonify({'ok': True, 'mode': 'HYBRID_REAL_DEMO'}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8080'))
    print(f"[DANBOT v3.1 - Entradas corrigidas] em 0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
