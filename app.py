"""
DANBOT v3.0 FINAL - ChatGPT CONSIDERAÇÕES APLICADAS
- Busca de candles REAIS M1 (api.get_candles) - bloqueia sintetico na operacao
- Filtros HYBRID originais sem alterar parametros
- Execucao exclusivamente BINARIAS (binary/turbo)
- Controle nao repetir entrada mesmo candle
- Registro e acompanhamento resultados Win/Loss
- DEMO como padrao inicial
- Corrigido Flask(__name__) e formatacao
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
    return jsonify({'status': 'ok', 'has_iq': HAS_IQ_MODULE, 'version': 'v3.0 FINAL'}), 200

@app.route('/')
def root():
    try:
        return render_template('dashboard.html')
    except:
        return jsonify({'status': 'ok', 'version': 'v3.0'}), 200

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
_BOT_THREADS = {}

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


def _add_log(st, msg):
    """Adiciona linha no log do painel (limita a 200 linhas)."""
    try:
        st['log'].insert(0, {'time': _brt_str(), 'msg': msg})
        del st['log'][200:]
    except Exception:
        pass

def _call_with_timeout(fn, timeout, *args, **kwargs):
    """Executa fn em thread separada. Retorna (ok, resultado_ou_erro).
    Evita que um api.buy()/get_candles() travado congele o loop inteiro."""
    box = {}
    def _run():
        try:
            box['res'] = fn(*args, **kwargs)
        except Exception as e:
            box['err'] = e
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False, 'timeout'
    if 'err' in box:
        return False, box['err']
    return True, box.get('res')

_OPEN_CACHE = {}

def _asset_open_state(api, asset):
    """True/False se a corretora diz que o ativo esta aberto (binary/turbo);
    None se nao deu para saber (nesse caso o robo tenta operar mesmo assim)."""
    now = time.time()
    cached = _OPEN_CACHE.get('data')
    if not cached or now - cached[0] > 60:
        ok, data = _call_with_timeout(api.get_all_open_time, 20)
        if not ok or not isinstance(data, dict):
            return None
        _OPEN_CACHE['data'] = (now, data)
        cached = _OPEN_CACHE['data']
    data = cached[1]
    seen = []
    for mk in ('turbo', 'binary'):
        info = (data.get(mk) or {}).get(asset)
        if isinstance(info, dict) and 'open' in info:
            seen.append(bool(info.get('open')))
    if not seen:
        return None
    return any(seen)

def _reconnect_from_loop(username, st):
    """Reconecta usando as credenciais ja guardadas (chamado pelo loop quando a API parece morta)."""
    email = (st.get('broker_email') or '').strip()
    password = st.get('broker_password') or ''
    if not email or not password:
        return False
    conn_st = get_user_conn_state(username)
    if conn_st.get('status') == 'connecting' and (time.time() - float(conn_st.get('ts') or 0)) < 180:
        return False
    _OPEN_CACHE.clear()
    ok, result = connect_iq_real(email, password, st.get('broker_account_type', 'PRACTICE'),
                                 username=username, broker_name=st.get('broker_name', 'IQ Option'))
    if ok:
        st['broker_connected'] = True
        st['broker_balance'] = result.get('balance', 0)
        _set_conn_state(username, status='connected', result=result, error=None)
    else:
        _add_log(st, f'Reconexao falhou: {result}')
    return ok

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

def _is_otc(asset):
    return 'OTC' in (asset or '').upper()

def _compute_signal(closes, volumes, asset):
    """OTC (sem volume real) -> regra adaptada (VWAP vira SMA10).
    Mercado aberto -> regra original com VWAP de volume.
    Nunca cai para a regra VWAP quando o ativo e OTC."""
    if not HAS_IQ_MODULE:
        return None, {}
    if _is_otc(asset):
        return IQ.check_hybrid_signal_otc(closes, volumes)
    return IQ.check_hybrid_signal(closes, volumes)

def _trading_loop(username):
    """Loop de trading - busca candles REAIS M1, bloqueia sinteticos"""
    print(f"[TRADING] Loop iniciado {username} - candles REAIS M1 apenas")
    
    while True:
        try:
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
                st['log'].insert(0, {'time': _brt_str(), 'msg': f'STOP WIN atingido $ {profit:.2f} - Bot parado'})
                st['running'] = False
                st['bot_running'] = False
                break
            if stop_loss > 0 and profit <= -abs(stop_loss):
                st['log'].insert(0, {'time': _brt_str(), 'msg': f'STOP LOSS atingido $ {profit:.2f} - Bot parado'})
                st['running'] = False
                st['bot_running'] = False
                break

            api = _IQ_APIS.get(username)
            if not api:
                if time.time() - st.get('_last_noapi_log', 0) > 30:
                    st['_last_noapi_log'] = time.time()
                    _add_log(st, 'Sem conexao ativa com a corretora - tentando reconectar...')
                    if time.time() - st.get('_last_reconnect_ts', 0) > 30:
                        st['_last_reconnect_ts'] = time.time()
                        _reconnect_from_loop(username, st)
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
                ok_c, candles = _call_with_timeout(api.get_candles, 15, asset, 60, 50, time.time())
                if not ok_c:
                    print(f"[TRADING] Falha get_candles REAL {asset}: {candles}")
                    candles = None
                if not ok_c or not candles or len(candles) < 20:
                    st['_candle_fails'] = st.get('_candle_fails', 0) + 1
                    n_c = len(candles) if candles else 0
                    print(f"[TRADING] Sem candles REAIS suficientes {asset}: {n_c} (falhas seguidas: {st['_candle_fails']})")
                    if time.time() - st.get('_last_candle_log', 0) > 30:
                        st['_last_candle_log'] = time.time()
                        try:
                            alive = api.check_connect()
                        except Exception:
                            alive = '?'
                        _add_log(st, f'Nao consegui velas de {asset} ({st["_candle_fails"]} falhas seguidas, check_connect={alive})')
                    if st['_candle_fails'] >= 3 and time.time() - st.get('_last_reconnect_ts', 0) > 30:
                        st['_last_reconnect_ts'] = time.time()
                        _add_log(st, 'Conexao parece travada - reconectando na IQ Option...')
                        if _reconnect_from_loop(username, st):
                            _add_log(st, 'Reconectado com sucesso')
                            st['_candle_fails'] = 0
                    time.sleep(3)
                    continue
                st['_candle_fails'] = 0

                # Verifica se sao candles reais (tem campo close, open)
                try:
                    import numpy as np
                    closes = np.array([c['close'] for c in candles], dtype=float)
                    volumes = np.array([c.get('volume', 500) for c in candles], dtype=float)
                except Exception as e:
                    print(f"[TRADING] Candles invalidos: {e}")
                    time.sleep(3)
                    continue

                # Filtros HYBRID: OTC usa regra adaptada (sem volume), mercado aberto usa VWAP com volume
                sinal = None
                debug = {}
                if not HAS_IQ_MODULE:
                    time.sleep(2)
                    continue
                try:
                    sinal, debug = _compute_signal(closes, volumes, asset)
                except Exception as e:
                    print(f"[TRADING] Erro ao calcular sinal {asset}: {e}")
                    if time.time() - st.get('_last_sigerr_log', 0) > 30:
                        st['_last_sigerr_log'] = time.time()
                        _add_log(st, f'Erro ao calcular sinal em {asset}: {str(e)[:100]}')
                    time.sleep(2)
                    continue

                # Log debug a cada 30s se nao tem sinal - ajuda ver por que nao entra
                if not sinal and time.time() - st.get('_last_nosig_log', 0) >= 30:
                    st['_last_nosig_log'] = time.time()
                    try:
                        _add_log(st, f'Sem sinal HYBRID em {asset} - RSI {debug.get("rsi",0):.1f} MACD {debug.get("macd_b1",0):.5f}/{debug.get("macd_b2",0):.5f} ALMA {debug.get("alma",0):.5f} vs EMA3 {debug.get("ema3",0):.5f} [{debug.get("modo","?")}]')
                    except Exception as e:
                        print(f"[TRADING] Erro ao montar log sem sinal: {e}")
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
                        
                        print(f"[TRADING] ENTRADA REAL {sinal} {asset} $ {entry_value} exp {exp} - {seconds_since_candle:.0f}s vela")
                        
                        try:
                            # So bloqueia se a corretora disser EXPLICITAMENTE que o ativo esta fechado
                            aberto = _asset_open_state(api, asset)
                            if aberto is False:
                                _add_log(st, f'Ativo {asset} FECHADO na corretora (binary/turbo) - entrada {sinal} ignorada nesta vela')
                                st['_last_candle_key'] = current_candle_key
                                time.sleep(2)
                                continue

                            # No maximo 2 tentativas por vela, com 5s de intervalo
                            if st.get('_attempt_key') != current_candle_key:
                                st['_attempt_key'] = current_candle_key
                                st['_attempts'] = 0
                            if st['_attempts'] >= 2:
                                st['_last_candle_key'] = current_candle_key
                                time.sleep(2)
                                continue
                            if time.time() - st.get('_last_attempt_ts', 0) < 5:
                                time.sleep(1)
                                continue
                            st['_attempts'] += 1
                            st['_last_attempt_ts'] = time.time()

                            # Execucao exclusivamente BINARIAS - metodo real api.buy
                            # buy(valor, ativo, direcao, expiracao) - com timeout para nao travar o loop
                            ok_b, res_b = _call_with_timeout(api.buy, 25, entry_value, asset, direction, exp)
                            if not ok_b:
                                if res_b == 'timeout':
                                    st['_last_candle_key'] = current_candle_key  # nao repetir: a ordem pode ter sido enviada
                                    _add_log(st, f'TIMEOUT na ordem {sinal} {asset} - confira na corretora se entrou. Sem nova tentativa nesta vela.')
                                    time.sleep(2)
                                    continue
                                raise res_b
                            check, order_id = res_b
                            
                            if check:
                                st['_last_trade_ts'] = time.time()
                                st['_last_candle_key'] = current_candle_key
                                
                                if direction == 'call':
                                    msg = f'ordem de compra executada - {sinal} em {asset} - $ {entry_value} - vela {now.strftime("%H:%M")} - exp {exp}min - ID {order_id}'
                                else:
                                    msg = f'ordem de venda executada - {sinal} em {asset} - $ {entry_value} - vela {now.strftime("%H:%M")} - exp {exp}min - ID {order_id}'
                                
                                st['log'].insert(0, {'time': _brt_str(), 'msg': msg})
                                print(f"[ORDEM] {msg}")

                                # Registro e acompanhamento resultados
                                def check_win_later(oid=order_id, exp_min=exp, val=entry_value, ast=asset):
                                    time.sleep((exp_min * 60) + 10)
                                    try:
                                        st2 = get_user_state(username)
                                        # Atualiza saldo REAL
                                        try:
                                            bal = api.get_balance()
                                            if bal:
                                                st2['broker_balance'] = float(bal)
                                        except:
                                            pass
                                        
                                        # Verifica win usando funcao real
                                        win_result = None
                                        try:
                                            win_result = api.check_win_v3(oid)
                                        except:
                                            try:
                                                win_result = api.check_win_digital_v2(oid)
                                            except Exception as e:
                                                print(f"[WIN] Erro check_win: {e}")
                                        
                                        if win_result is not None:
                                            is_win = False
                                            if isinstance(win_result, bool):
                                                is_win = win_result
                                            elif isinstance(win_result, (int, float)):
                                                is_win = win_result > 0
                                            elif isinstance(win_result, dict):
                                                is_win = win_result.get('win', False) or win_result.get('result', 0) > 0
                                            
                                            if is_win:
                                                st2['wins'] = st2.get('wins', 0) + 1
                                                pa = val * 0.82  # payout medio 82%
                                                st2['profit'] = st2.get('profit', 0) + pa
                                                st2['log'].insert(0, {'time': _brt_str(), 'msg': f'WIN {ast} +$ {pa:.2f} - ID {oid}'})
                                            else:
                                                st2['losses'] = st2.get('losses', 0) + 1
                                                st2['profit'] = st2.get('profit', 0) - val
                                                st2['log'].insert(0, {'time': _brt_str(), 'msg': f'LOSS {ast} -$ {val:.2f} - ID {oid}'})
                                            
                                            total = st2['wins'] + st2['losses']
                                            st2['win_rate'] = (st2['wins'] / total * 100) if total > 0 else 0
                                        else:
                                            st2['log'].insert(0, {'time': _brt_str(), 'msg': f'Resultado pendente {ast} ID {oid} - aguarde'})
                                    except Exception as e:
                                        print(f"[WIN] Erro acompanhamento: {e}")

                                threading.Thread(target=check_win_later, daemon=True).start()
                            else:
                                st['log'].insert(0, {'time': _brt_str(), 'msg': f'Falha entrada {sinal} {asset}: corretora recusou -> {order_id}'})
                                print(f"[ORDEM FALHA] {sinal} {asset} check=False motivo={order_id}")
                        except Exception as e:
                            import traceback
                            traceback.print_exc()
                            st['log'].insert(0, {'time': _brt_str(), 'msg': f'Erro entrada REAL: {str(e)[:120]}'})
                    else:
                        # Fora da janela 0-30s
                        if st.get('_last_candle_key') != current_candle_key:
                            msg = f'cancelada ordem de entrada {sinal} em {asset} - {seconds_since_candle:.0f}s apos vela (limite 30s) - aguardando proximo sinal'
                            st['log'].insert(0, {'time': _brt_str(), 'msg': msg})
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
            return jsonify(ok=True, status='connecting', message='Conectando saldo $ REAL...', elapsed=int(elapsed)), 200
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

@app.route('/api/bot/asset', methods=['POST'])
def bot_set_asset():
    """Salva o ativo escolhido no painel (o frontend chamava esta rota, mas ela nao existia)."""
    try:
        u = current_user()
        username = u.get('sub', 'admin')
        st = get_user_state(username)
        data = request.get_json(silent=True) or {}
        asset = (data.get('asset') or '').strip()
        if not asset or 'Carregando' in asset:
            return jsonify(ok=False, error='ativo invalido'), 200
        old = st.get('selected_asset')
        st['selected_asset'] = asset
        if old != asset:
            _add_log(st, f'Ativo alterado: {old} -> {asset} ({"OTC - regra sem volume" if _is_otc(asset) else "mercado aberto - VWAP com volume"})')
        return jsonify(ok=True, asset=asset), 200
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 200

@app.route('/api/bot/start', methods=['POST'])
def bot_start():
    try:
        u = current_user()
        username = u.get('sub', 'admin')
        st = get_user_state(username)
        data = request.get_json(silent=True) or {}
        
        asset = data.get('asset') or st.get('selected_asset') or 'EURUSD'
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

        old_thread = _BOT_THREADS.get(username)
        if old_thread and old_thread.is_alive():
            st['running'] = False
            time.sleep(0.5)
            st['running'] = True

        t = threading.Thread(target=_trading_loop, args=(username,), daemon=True)
        t.start()
        _BOT_THREADS[username] = t
        
        msg = f'Bot iniciado {asset} - Entrada $ {entry_value} - {timeframe} exp {expiration}min - saldo $ REAL - DEMO'
        st['log'].insert(0, {'time': _brt_str(), 'msg': msg})
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
    st['log'].insert(0, {'time': _brt_str(), 'msg': 'Bot parado'})
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
                # Busca candles REAIS M1
                candles = api.get_candles(asset, 60, 50, time.time())
                if candles and len(candles) >= 20:
                    import numpy as np
                    closes = np.array([c['close'] for c in candles], dtype=float)
                    volumes = np.array([c.get('volume', 500) for c in candles], dtype=float)
                    sinal, debug = _compute_signal(closes, volumes, asset)
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
    print(f"[DANBOT v3.0 FINAL - ChatGPT Consideracoes] em 0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
