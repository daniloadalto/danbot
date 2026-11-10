
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
_STEP_LOCKS = {}          # um passo de analise por vez, por usuario (protege contra ciclos duplicados)
_START_LOCK = threading.Lock()

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
    """Adiciona linha no log do painel (limita a 200 linhas). Cada linha recebe um numero sequencial."""
    try:
        st['_log_seq'] = st.get('_log_seq', 0) + 1
        st['log'].insert(0, {'seq': st['_log_seq'], 'time': _brt_str(), 'msg': msg})
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

def _release_candles_wait(api):
    """A iqoptionapi espera a resposta de get_candles num laco que roda ate chegar dado. Se a resposta se perde, a thread
    fica presa nesse laco gastando CPU para sempre (e deixa as proximas leituras lentas). Pondo um valor no lugar da resposta
    o laco termina e a thread morre. Chamar SO depois de um timeout, nunca durante uma leitura em andamento."""
    try:
        api.api.candles.candles_data = []
    except Exception:
        pass

def _safe_close(api):
    """IQ_Option NAO tem close(): o fechamento existe em api.api (IQOptionAPI). Antes o close() falhava em silencio e
    cada reconexao deixava um websocket + threads vivos. Roda em thread para nao travar quem chama."""
    if api is None:
        return
    def _do():
        try:
            inner = getattr(api, 'api', None)
            if inner is not None and hasattr(inner, 'close'):
                inner.close()
            elif hasattr(api, 'close'):
                api.close()
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()

_ASSET_IDS = {}
_ASSET_IDS_LOCK = threading.Lock()

def _asset_variants(asset):
    """Variantes de nome possiveis para o mesmo ativo (USDBRL-OTC / USDBRL / USD-BRL-OTC...)."""
    a = (asset or '').strip()
    out = [a]
    base = a[:-4] if a.endswith('-OTC') else a
    out += [base, base + '-OTC', base.replace('/', ''), base.replace('/', '') + '-OTC',
            base.replace('/', '-'), base.replace('-', '/')]
    return list(dict.fromkeys(x for x in out if x))

def _asset_id(api, asset):
    """Resolve o ID numerico do ativo pra pedir velas. A biblioteca de 2021 so conhece os 9 OTC
    classicos no ACTIVES - os ativos novos (exoticas, cripto OTC, commodities) NAO estao la, e
    foi por isso que a leitura falhou com a lista nova. Resolucao dinamica: 1) ACTIVES com
    variantes de nome; 2) mapeamento que forks mais novos carregam na conexao; 3) dados de
    inicializacao/ativos da corretora, se o fork expuser. Cacheia por conexao."""
    key = (id(api), asset)
    with _ASSET_IDS_LOCK:
        if key in _ASSET_IDS:
            return _ASSET_IDS[key]
    aid = None
    cands = [c.upper() for c in _asset_variants(asset)]
    try:
        from iqoptionapi import constants as _OPC
        for cand in cands:
            aid = _OPC.ACTIVES.get(cand)
            if aid is not None:
                break
    except Exception:
        pass
    if aid is None:
        inner = getattr(api, 'api', None)
        m = getattr(inner, 'instrument_id_mapping', None) if inner is not None else None
        if isinstance(m, dict):
            up = {str(k).upper(): v for k, v in m.items()}
            for cand in cands:
                if cand in up:
                    aid = up[cand]
                    break
    if aid is None:
        inner = getattr(api, 'api', None)
        init = getattr(inner, 'init_data', None) if inner is not None else None
        if isinstance(init, dict):
            inst = init.get('instrument') or {}
            for v in (inst.values() if isinstance(inst, dict) else inst or []):
                try:
                    nm = str((v or {}).get('name', '')).upper()
                except Exception:
                    continue
                if nm and nm in cands:
                    aid = v.get('id')
                    break
    if aid is None:
        getter = getattr(api, 'get_all_assets_info', None)
        if callable(getter):
            try:
                infos = getter() or []
                for it in infos or []:
                    nm = str((it or {}).get('name', '')).upper()
                    if nm and nm in cands:
                        aid = it.get('id')
                        break
            except Exception:
                pass
    if aid is not None:
        with _ASSET_IDS_LOCK:
            _ASSET_IDS[key] = aid
    return aid

def _read_candles_raw(api, asset, interval, count, end, timeout):
    """Leitura de velas SEM o api.get_candles da biblioteca. O da lib (1) espera com 'while ...: pass' (queima CPU) e
    (2) em qualquer erro chama self.connect() = login completo dentro da thread leitora (tempestade de logins).
    Aqui: pede, espera dormindo, e se nao vier resposta levanta TimeoutError. Sem reconexao escondida."""
    inner = getattr(api, 'api', None)
    aid = _asset_id(api, asset)
    if aid is None:
        # ativo novo sem id na biblioteca de 2021: erro CLARO, sem chamar o get_candles da lib
        # (que em KeyError dispara reconexao em loop - foi isso que travou a leitura dos ativos novos)
        raise Exception('ativo sem id na biblioteca: ' + asset)
    if inner is None or not hasattr(inner, 'getcandles') or not hasattr(inner, 'candles'):
        raise Exception('estrutura de conexao inesperada para velas')
    inner.candles.candles_data = None
    inner.getcandles(aid, interval, count, end)
    t_end = time.time() + timeout
    while time.time() < t_end:
        d = inner.candles.candles_data
        if d is not None:
            return d
        time.sleep(0.05)
    raise TimeoutError('timeout')

_API_LOCKS = {}
def _api_lock_for(api):
    """Uma trava por conexao: a iqoptionapi guarda as respostas em variaveis compartilhadas, entao so UMA consulta
    de velas / lista de ativos / payout pode estar em andamento por vez (ordens e resultados nao usam essa trava)."""
    lk = _API_LOCKS.get(id(api))
    if lk is None:
        lk = _API_LOCKS.setdefault(id(api), threading.Lock())
    return lk

def _locked_call(api, fn, timeout, *args, cs=None, is_candles=False):
    """Executa fn(*args) com a trava da conexao. Tempo de espera pela trava conta no timeout.
    Retorna (ok, resultado|'timeout'|'cancelado'|erro)."""
    box = {}
    lk = _api_lock_for(api)
    def _run():
        if not lk.acquire(timeout=max(0.1, timeout)):
            box['err'] = 'timeout'
            return
        try:
            if box.get('abandon'):
                return                       # quem pediu ja desistiu: nao faz pedido inutil
            box['inside'] = True
            box['res'] = fn(*args)
        except Exception as e:
            box['err'] = e
        finally:
            box['inside'] = False
            lk.release()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    deadline = time.time() + timeout
    while t.is_alive():
        why = None
        if cs is not None and cs.get('cancel'):
            why = 'cancelado'
        elif deadline - time.time() <= 0:
            why = 'timeout'
        if why:
            box['abandon'] = True
            if is_candles and box.get('inside'):
                _release_candles_wait(api)   # so mexe na variavel da lib se ESTA thread e quem esta esperando
            return False, why
        t.join(min(0.3, max(0.0, deadline - time.time())))
    if 'err' in box:
        return False, box['err']
    return True, box.get('res')

def _get_candles_safe(api, timeout, *args, cs=None):
    """api.get_candles com trava de conexao, timeout e cancelamento imediato (cs['cancel'])."""
    asset, interval, count, end = args
    ok, res = _locked_call(api, lambda: _read_candles_raw(api, asset, interval, count, end, max(1.0, timeout - 0.7)),
                           timeout, cs=cs, is_candles=True)
    if not ok and isinstance(res, TimeoutError):
        res = 'timeout'
    return ok, res

_OPEN_CACHE = {}


def _scan_open_assets(api, timeout=25):
    """Le os horarios da corretora. Ordens de 1 a 5 min usam o produto TURBO, entao a lista de
    operaveis e a de TURBO aberto. Retorna dict ou None se a corretora nao respondeu."""
    ok, data = _locked_call(api, api.get_all_open_time, timeout)
    if not ok or not isinstance(data, dict):
        return None
    turbo = sorted(a for a, i in (data.get('turbo') or {}).items() if isinstance(i, dict) and i.get('open'))
    binary = sorted(a for a, i in (data.get('binary') or {}).items() if isinstance(i, dict) and i.get('open'))
    digital = sorted(a for a, i in (data.get('digital') or {}).items() if isinstance(i, dict) and i.get('open'))
    tradable = turbo if turbo else binary      # se turbo nao listar nada, cai para binary
    # Mercado aberto opera em BINARIA (pedido do usuario, 09/11): so entram ativos abertos em BINARIA agora.
    if OPEN_PRODUCT == 'digital' and digital:
        open_list = [a for a in digital if 'OTC' not in a]
    else:
        open_list = [a for a in (binary or tradable) if 'OTC' not in a]      # lista de BINARIAS
    # OTC: usa a lista de BINARIAS (a aba onde as ordens de OTC aparecem na corretora); se vier vazia, cai para turbo
    otc_list = [a for a in (binary or tradable) if 'OTC' in a]
    return {
        'binary_assets': open_list,
        'otc_assets': otc_list,
        'turbo_open': turbo, 'binary_open': binary, 'digital_open': digital,
        'source': 'turbo' if turbo else 'binary',
        'ts': time.time(),
    }

def _refresh_assets(username, force=False):
    """Desativado: a lista de ativos e fixa (classicos). Evita chamadas pesadas a IQ."""
    return
    api = _IQ_APIS.get(username)
    if not api:
        return
    conn_st = get_user_conn_state(username)
    result = conn_st.get('result')
    if not isinstance(result, dict):
        return
    if not force and time.time() - result.get('assets_ts', 0) < 180:
        return
    result['assets_ts'] = time.time()          # evita chamadas simultaneas
    info = _scan_open_assets(api)
    if not info:
        return
    if info['binary_assets'] or info['otc_assets']:
        result['binary_assets'] = info['binary_assets'] or result.get('binary_assets', [])
        result['otc_assets'] = info['otc_assets'] or result.get('otc_assets', [])
        result['all_assets'] = result['binary_assets'] + result['otc_assets']
    result['open_info'] = {k: info[k] for k in ('turbo_open', 'binary_open', 'digital_open', 'source')}

def _reconnect_from_loop(username, st):
    """Reconecta usando as credenciais ja guardadas (chamado pelo loop quando a API parece morta)."""
    email = (st.get('broker_email') or '').strip()
    password = st.get('broker_password') or ''
    if not email or not password:
        _add_log(st, '[auto] Sem login guardado (o servidor reiniciou?) - clique em CONECTAR na IQ Option de novo e depois inicie o bot')
        return False
    conn_st = get_user_conn_state(username)
    if conn_st.get('status') == 'connecting' and (time.time() - float(conn_st.get('ts') or 0)) < 180:
        _add_log(st, '[auto] Reconexao ja em andamento (outra tentativa ainda esta conectando)')
        return False
    _OPEN_CACHE.clear()
    # api.connect() da biblioteca pode ficar preso para sempre; sem limite de tempo ele travava o loop do bot inteiro
    _gen = _gen_new(username)
    done, out = _call_with_timeout(connect_iq_real, 75, email, password, st.get('broker_account_type', 'PRACTICE'),
                                   username=username, broker_name=st.get('broker_name', 'IQ Option'), gen=_gen, scan=False)
    if not done:
        _add_log(st, f'[auto] Reconexao falhou: a IQ nao respondeu em 75s ({out}). Tento de novo em instantes.')
        return False
    ok, result = out
    if ok:
        st['broker_connected'] = True
        st['broker_balance'] = result.get('balance', 0)
        _set_conn_state(username, status='connected', result=result, error=None)
        threading.Thread(target=lambda: _refresh_assets(username, force=True), daemon=True).start()   # varredura fora do login
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

_GEN, _GEN_LOCK = {}, threading.Lock()
def _gen_new(u):
    with _GEN_LOCK:
        _GEN[u] = _GEN.get(u, 0) + 1
        return _GEN[u]
def _gen_current(u, gen):
    with _GEN_LOCK:
        return _GEN.get(u, 0) == gen

def connect_iq_real(email, password, account_type='PRACTICE', host='iqoption.com', username='admin', broker_name='IQ Option', gen=None, scan=True):
    """Conexao REAL usando iqoptionapi.stable_api - funcao real, nao inventada"""
    try:
        from iqoptionapi.stable_api import IQ_Option
    except Exception as e:
        return False, f'iqoptionapi nao instalada: {e}'

    try:
        # A conexao antiga so e trocada DEPOIS que a nova funcionar (antes ela era apagada primeiro e, se o login
        # travasse, o bot ficava sem nenhuma conexao)
        _old_api = _IQ_APIS.get(username)

        api = IQ_Option(email, password)
        check, reason = api.connect()
        if not check:
            return False, f'Falha ao conectar IQ: {reason or "credenciais invalidas"}'

        # DEMO como padrao inicial
        _call_with_timeout(api.change_balance, 20, 'PRACTICE' if account_type.upper() in ['PRACTICE', 'DEMO'] else 'REAL')

        time.sleep(1.5)
        _okb, _bal = _call_with_timeout(api.get_balance, 15)
        balance = (_bal if _okb and _bal else 0)
        if gen is not None and not _gen_current(username, gen):
            _safe_close(api)             # uma reconexao mais nova ja venceu: esta ficou obsoleta e nao pode sobrescrever
            return False, 'conexao obsoleta (outra reconexao venceu)'

        # Lista ativos abertos (ordens de 1-5 min usam TURBO)
        all_assets = []
        binary_assets = []
        otc_assets = []
        open_info = {}
        try:
            # lista REAL de ativos abertos (uma unica chamada get_all_open_time no login).
            # Antes era sempre None e o bot operava ativo fechado -> toda ordem recusada parecia "erro de conexao".
            info = _scan_open_assets(api, timeout=25)
            if info is None:
                print("[IQ] get_all_open_time nao respondeu no login - usando lista fixa de classicos")
        except Exception as e:
            print(f"[IQ] Erro open_time: {e}")
            info = None

        # Fallback
        default_binary = list(FALLBACK_OPEN) if 'FALLBACK_OPEN' in globals() else ['EURUSD', 'GBPUSD', 'USDJPY']
        default_otc = list(FALLBACK_OTC) if 'FALLBACK_OTC' in globals() else ['EURUSD-OTC', 'GBPUSD-OTC']

        if not binary_assets:
            binary_assets = default_binary
        if not otc_assets:
            otc_assets = default_otc
        if not all_assets:
            all_assets = binary_assets + otc_assets

        _IQ_APIS[username] = api
        if _old_api is not None and _old_api is not api:
            _safe_close(_old_api)

        result = {
            'balance': float(balance),
            'account_type': account_type.upper(),
            'otc_assets': otc_assets,
            'binary_assets': binary_assets,
            'all_assets': all_assets,
            'open_info': open_info,
            'assets_ts': time.time() if scan else 0,
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
            # login com timeout: api.connect() da biblioteca pode ficar preso para sempre
            done, out = _call_with_timeout(connect_iq_real, 90, email, password, account_type,
                                           host=host, username=username, broker_name=broker)
            ok, result = out if done else (False, 'a IQ nao respondeu em 90s (verifique internet/2FA)')
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


# ======================= EXECUCAO DE ORDENS / ENTRADA =======================
# ENTRY_MODE=open (padrao): sinal calculado com velas FECHADAS e ordem enviada no nascimento da vela.
# ENTRY_MODE=live: modo antigo (recalcula a cada 2s na vela em formacao, janela de 30s).
ENTRY_MODE = (os.environ.get('ENTRY_MODE') or 'open').strip().lower()
ENTRY_MAX_SECONDS = float(os.environ.get('ENTRY_MAX_SECONDS') or 20)  # janela apos o nascimento da vela (8s era curto demais p/ latencia da IQ)
DIGITAL_FALLBACK = False   # NUNCA tentar Digital: o usuario pediu binaria em todo o bot (09/11)
# Produto usado no MERCADO ABERTO. Pedido do usuario (09/11): SO BINARIA - nada de digital, turbo ou blitz.
# OTC ja funciona por api.buy (binaria) e nao foi mexido.
OPEN_PRODUCT = (os.environ.get('OPEN_PRODUCT') or 'binary').strip().lower()  # binary (padrao desde 09/11) | digital
DIGITAL_EXPS = (1, 5)

def _allowed_h(asset):
    """Expiracoes que o produto aceita nesse ativo.
    OTC (binaria): 1 a 3 min (expiracoes mais longas tendem a acertar mais nos setups de reversao/pullback).
    Mercado aberto com produto digital: so 1 ou 5 min (o digital da IQ nao aceita 2/3)."""
    base = tuple(int(x) for x in (os.environ.get('EXP_ALLOWED') or '1,2,3').split(',') if x.strip())
    if not _is_otc(asset) and OPEN_PRODUCT == 'digital':
        return tuple(h for h in base if h in DIGITAL_EXPS) or (1,)
    if not _is_otc(asset) and 5 not in base:
        return base + (5,)     # mercado aberto em BINARIA: a IQ recusa 1 min em alguns ativos - 5 min entra como opcao
    return base

def _fetch_candles(api, st, username, asset, timeout=15, count=50, min_len=20):
    """Busca velas M1 REAIS. Controla falhas seguidas e reconecta sozinho. Retorna lista ou None."""
    ok_c, candles = _get_candles_safe(api, timeout, asset, 60, count, time.time())
    if not ok_c:
        print(f"[TRADING] Falha get_candles REAL {asset}: {candles}")
        candles = None
    if candles and len(candles) >= min_len:
        st['_candle_fails'] = 0
        return candles
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
    return None

def _order_diag(api, st, asset):
    """Roda em segundo plano DEPOIS de uma falha: mostra em quais produtos a corretora diz que o ativo esta aberto."""
    if time.time() - st.get('_last_diag_ts', 0) < 60:
        return
    st['_last_diag_ts'] = time.time()
    ok, data = _locked_call(api, api.get_all_open_time, 30)
    if not ok or not isinstance(data, dict):
        _add_log(st, f'Diagnostico {asset}: nao consegui consultar os horarios da corretora')
        return
    parts = []
    for mk in ('turbo', 'binary', 'digital'):
        info = (data.get(mk) or {}).get(asset)
        if info is None:
            parts.append(f'{mk}=sem dados')
        else:
            parts.append(f'{mk}={"ABERTO" if info.get("open") else "FECHADO"}')
    _add_log(st, f'Diagnostico {asset}: ' + ' | '.join(parts) + ' (binaria de mercado aberto pode recusar 1 min - tente 2, 3 ou 5 min)')
    # detalhe fino: expiracoes que a BINARIA do ativo realmente oferece
    fn = getattr(api, 'get_binary_option_detail', None)
    if fn:
        ok2, det = _locked_call(api, fn, 20, asset)
        if ok2 and isinstance(det, dict):
            b = (det.get(asset) or {}).get('binary') or {}
            exps = b.get('expirations')
            if exps:
                import datetime as _dt
                txt = ', '.join(_dt.datetime.fromtimestamp(int(e), timezone.utc).strftime('%H:%M') for e in list(exps)[:8])
                _add_log(st, f'Diagnostico {asset}: expiracoes BINARIAS disponiveis (UTC) = {txt}')
            else:
                _add_log(st, f'Diagnostico {asset}: binaria sem lista de expiracoes visivel: {str(b)[:120]}')

def _buy_digital(api, asset, direction, amount, exp):
    fn = getattr(api, 'buy_digital_spot_v2', None) or getattr(api, 'buy_digital_spot', None)
    if not fn:
        return False, 'biblioteca sem suporte a digital'
    ok, res = _call_with_timeout(fn, 20, asset, amount, direction, exp)
    if not ok:
        return False, str(res)
    try:
        check, oid = res
    except Exception:
        return False, f'resposta inesperada: {res}'
    return bool(check), oid

def _wait_trade_result(api, oid, kind, max_wait):
    """Espera o fechamento da ordem. Retorna lucro (+win, -loss, 0 empate) ou None se nao conseguiu."""
    deadline = time.time() + max_wait
    if kind == 'digital':
        ok, res = _call_with_timeout(api.check_win_digital_v2, max_wait, oid)
        if ok and isinstance(res, (tuple, list)) and len(res) == 2:
            check, amount = res
            return float(amount) if check else None
        return None
    structure_ok = True
    while time.time() < deadline:
        try:
            od = api.get_async_order(oid)
            closed = (od or {}).get('option-closed') or {}
            if closed:
                msg = closed.get('msg', closed)
                return float(msg['profit_amount']) - float(msg['amount'])
        except AttributeError:
            structure_ok = False
            break
        except Exception:
            pass
        time.sleep(1)
    if not structure_ok:
        ok, res = _call_with_timeout(api.check_win_v3, max_wait, oid)
        if ok and isinstance(res, (int, float)):
            return float(res)
    return None

def _track_result(api, username, oid, kind, exp_min, val, asset, t_entry, sinal='', meta=None):
    try:
        profit_trade = _wait_trade_result(api, oid, kind, exp_min * 60 + 150)
        st2 = get_user_state(username)
        try:
            bal = api.get_balance()
            if bal:
                st2['broker_balance'] = float(bal)
                _res = get_user_conn_state(username).get('result')
                if isinstance(_res, dict):          # mantem o badge do topo em sync com o saldo real
                    _res['balance'] = float(bal)
        except Exception:
            pass
        dur = time.time() - t_entry
        if profit_trade is None:
            _add_log(st2, f'Resultado pendente {asset} ID {oid} - confira na corretora')
            return
        _asset_result(st2, asset, profit_trade, (meta or {}).get('dir') or sinal)
        if meta and _ENGINE:
            try:
                _ENGINE.record_real(asset, meta['setup'], meta['h'], profit_trade)
                _journal([_brt_str(), asset, meta['setup'], meta['dir'], meta['h'], val,
                          'WIN' if profit_trade > 0 else ('LOSS' if profit_trade < 0 else 'EMPATE'),
                          json.dumps(meta.get('det', {}))])
            except Exception as e:
                print(f"[STRAT] erro ao registrar resultado real: {e}")
        if profit_trade > 0:
            st2['wins'] = st2.get('wins', 0) + 1
            st2['profit'] = st2.get('profit', 0) + profit_trade
            _add_log(st2, f'WIN {asset} +$ {profit_trade:.2f} - ID {oid} - fechou {dur:.0f}s apos a entrada')
        elif profit_trade < 0:
            st2['losses'] = st2.get('losses', 0) + 1
            st2['profit'] = st2.get('profit', 0) + profit_trade
            _add_log(st2, f'LOSS {asset} -$ {abs(profit_trade):.2f} - ID {oid} - fechou {dur:.0f}s apos a entrada')
        else:
            _add_log(st2, f'EMPATE {asset} $ 0.00 - ID {oid} - fechou {dur:.0f}s apos a entrada')
        total = st2.get('wins', 0) + st2.get('losses', 0)
        st2['win_rate'] = (st2['wins'] / total * 100) if total > 0 else 0
    except Exception as e:
        print(f"[WIN] Erro acompanhamento: {e}")

def _execute_entry(api, st, username, asset, sinal, entry_value, exp, candle_start_ts, label, meta=None):
    """Envia a ordem. Retorna 'ok', 'recusada', 'timeout' ou 'erro'. Sempre deixa o motivo no log do painel."""
    direction = sinal.lower()  # call / put
    t_send = time.time()
    sec_vela = t_send - candle_start_ts
    print(f"[TRADING] ENTRADA REAL {sinal} {asset} $ {entry_value} exp {exp} - {sec_vela:.1f}s apos nascer a vela")
    try:
        kind = 'binary'
        # Pedido do usuario (09/11): o bot SO entra em BINARIA (api.buy) - nem digital, nem turbo, nem blitz.
        # OTC ja entra por aqui e nao foi mexido; mercado aberto agora usa o mesmo caminho.
        ok_b, res_b = _call_with_timeout(api.buy, 15, entry_value, asset, direction, exp)
        if not ok_b:
            if res_b == 'timeout':
                _add_log(st, f'TIMEOUT na ordem {sinal} {asset} - confira na corretora se entrou. Sem nova tentativa nesta vela.')
                _reconnect_now(st, username, 'ordem sem resposta - reconstruindo a conexao')
                return 'timeout'
            raise res_b
        check, order_id = res_b
        if not check:
            reason = ('sem resposta da corretora em 5s - a ordem PODE ter sido enviada, confira' if order_id is None
                      else str(order_id))
            _add_log(st, f'Falha entrada {sinal} {asset}: corretora recusou -> {reason}')
            print(f"[ORDEM FALHA] {sinal} {asset} check=False motivo={order_id}")
            # Recusa "asset is not available" em mercado aberto = a expiracao pedida NAO existe na binaria
            # desse ativo (no OTC a binaria aceita 1 min; em ativo real nem sempre). Tentar as outras expiracoes
            # BINARIAS (2, 3 e 5 min) na mesma vela - continua 100% binaria (pedido do usuario).
            if order_id is not None and 'not available' in str(order_id).lower() and not _is_otc(asset):
                tried = []
                for alt in (x for x in (1, 2, 3, 5) if x != exp):
                    ok_a, res_a = _call_with_timeout(api.buy, 15, entry_value, asset, direction, alt)
                    if not ok_a:
                        tried.append(f'{alt}min:sem resposta')
                        continue
                    chk_a, oid_a = res_a
                    if chk_a:
                        order_id, exp, kind = oid_a, alt, 'binary'
                        _add_log(st, f'Binaria ACEITA em {asset} com expiracao {alt} min (a de {exp} min era recusada) - nas proximas entradas use essa expiracao no painel')
                        print(f"[ORDEM] expiracao ajustada para {alt} min em {asset}")
                        break
                    tried.append(f'{alt}min:{str(oid_a)[:70]}')
                else:
                    _add_log(st, f'Todas as expiracoes binarias foram recusadas em {asset}: {"; ".join(tried) or "nenhuma testada"}')
            # Sem fallback digital (pedido do usuario: o bot so entra em BINARIA, em qualquer produto)
            if not check:
                threading.Thread(target=_order_diag, args=(api, st, asset), daemon=True).start()
                return 'recusada'
        lado = 'compra' if direction == 'call' else 'venda'
        _add_log(st, f'ordem de {lado} executada{" [" + meta["setup"] + "]" if meta else ""} - {sinal} em {asset} - $ {entry_value} - vela {label} - exp {exp}min - '
                     f'enviada a {sec_vela:.1f}s da vela ({(time.time() - t_send):.1f}s p/ confirmar) - '
                     f'{"DIGITAL " if kind == "digital" else ""}ID {order_id}')
        print(f"[ORDEM] {lado} {sinal} {asset} ID {order_id} em {sec_vela:.1f}s da vela")
        threading.Thread(target=_track_result, args=(api, username, order_id, kind, exp, entry_value, asset, t_send, sinal, meta),
                         daemon=True).start()
        return 'ok'
    except Exception as e:
        import traceback
        traceback.print_exc()
        _add_log(st, f'Erro entrada REAL: {str(e)[:120]}')
        return 'erro'


# ======================= ESTRATEGIA V2 (setups validados estatisticamente) =======================
try:
    import strategy_v2 as SV2
    HAS_V2 = True
except Exception as _e:
    SV2 = None
    HAS_V2 = False
    print(f"[STRAT] strategy_v2 indisponivel: {_e}")
STRATEGY = 'v2'      # HYBRID abandonado: so a estrategia nova (setups TREND_PULLBACK, RANGE_REVERSION, BREAKOUT_PULLBACK, SPIKE_REVERSAL)
_UNUSED_STRATEGY_ENV = (os.environ.get('STRATEGY') or '')          # v2 (padrao) | v1 (HYBRID antigo)
SESSION_UTC = (os.environ.get('SESSION_UTC') or '').strip()          # so mercado aberto; '' = sem filtro de horario
PAYOUT_MIN = float(os.environ.get('PAYOUT_MIN') or 0.80)                 # so aplica se a corretora informar o payout
BOOT_CANDLES = int(os.environ.get('BOOT_CANDLES') or 1500)               # historico M1 usado para validar os setups
_ENGINE = SV2.Engine() if HAS_V2 else None
_PAYOUT_CACHE = {}

def _session_ok(asset):
    """Mercado aberto: so opera na janela (UTC) de maior liquidez. OTC nao tem filtro."""
    if _is_otc(asset) or not SESSION_UTC:
        return True
    try:
        a, b = SESSION_UTC.split('-')
        now = datetime.datetime.now(timezone.utc)
        hr = now.hour + now.minute / 60.0
        return float(a) <= hr < float(b)
    except Exception:
        return True

def _payout_cached(api, asset):
    """Payout lido SO do cache (nunca chama a corretora). Usado dentro da janela de entrada."""
    c = _PAYOUT_CACHE.get(id(api))
    if not c:
        return None
    info = c[1].get(asset)
    if isinstance(info, dict):
        v = info.get('turbo') or info.get('binary')
        try:
            return float(v) if v else None
        except Exception:
            return None
    return None

def _payout(api, asset):
    """Payout do ativo (0-1) ou None se nao foi possivel saber."""
    now = time.time()
    c = _PAYOUT_CACHE.get(id(api))
    if not c or now - c[0] > 300:
        ok, data = _locked_call(api, api.get_all_profit, 15)
        if not ok or not isinstance(data, dict):
            return None
        c = (now, data)
        _PAYOUT_CACHE[id(api)] = c
    info = c[1].get(asset)
    if isinstance(info, dict):
        v = info.get('turbo') or info.get('binary')
        try:
            return float(v) if v else None
        except Exception:
            return None
    return None

def _journal(row):
    """Diario das operacoes reais (CSV) para analisar depois. Nunca derruba o bot."""
    try:
        import csv
        path = os.path.join(os.path.dirname(_ENGINE.path) or '.', 'trades_journal.csv') if _ENGINE else 'trades_journal.csv'
        new = not os.path.exists(path)
        with open(path, 'a', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            if new:
                w.writerow(['hora_brt', 'ativo', 'setup', 'direcao', 'exp_min', 'valor', 'resultado', 'indicadores'])
            w.writerow(row)
    except Exception as e:
        print(f"[JOURNAL] erro: {e}")

def _throttled_log(st, key, msg, every=120):
    if time.time() - st.get(key, 0) >= every:
        st[key] = time.time()
        _add_log(st, msg)

# ======================= SELECAO AUTOMATICA DE ATIVOS =======================
# (10/11) A corretora REMOVEU varios ativos da conta do usuario (motivo da recusa 'active is suspended').
# Lista nova pedida pelo usuario: SO os OTC que aparecem abertos na conta dele, em binarias.
FALLBACK_OTC = [
    # moedas exotas (USD x moeda local)
    'USDBRL-OTC', 'USDARS-OTC', 'USDCLP-OTC', 'USDCOP-OTC', 'USDAED-OTC', 'USDBDT-OTC',
    'USDBOB-OTC', 'USDTRY-OTC', 'USDZAR-OTC', 'USDVND-OTC', 'USDPHP-OTC', 'USDIDR-OTC',
    'USDINR-OTC', 'USDHKD-OTC', 'USDMYR-OTC', 'USDMXN-OTC', 'USDNGN-OTC', 'USDPLN-OTC',
    'USDSAR-OTC', 'USDSGD-OTC', 'USDDOP-OTC', 'PENUSD-OTC',
    # cripto
    'XRPUSD-OTC', 'ETHUSD-OTC', 'SOLUSD-OTC', 'LTCUSD-OTC', 'SHIBUSD-OTC', 'DOTUSD-OTC',
    'DASHUSD-OTC', 'DYDXUSD-OTC', 'HBARUSD-OTC', 'FETUSD-OTC', 'HYPEUSD-OTC', 'FARTCOINUSD-OTC',
    'SEIUSD-OTC', 'RENDERUSD-OTC', 'RAYDIUMUSD-OTC', 'ONDOUSD-OTC', 'TRUMPCOINUSD-OTC',
    'TRONUSD-OTC', 'TAOUSD-OTC', 'SUIUSD-OTC', 'LUNCUSD-OTC', 'PEPEUSD-OTC', 'ARBUSD-OTC',
    'WLDUSD-OTC', 'WIFUSD-OTC', 'SATSUSD-OTC', 'VAULTAUSD-OTC',
    # materias-primas
    'COTTON-OTC', 'COFFEE-OTC', 'SUGAR-OTC', 'PLATINUM-OTC', 'PALLADIUM-OTC', 'URANIUM-OTC',
    # acoes/indices OTC
    'OPENAI-OTC', 'FORMULAONE-OTC',
]   # lista do usuario (10/11) - so OTC em binarias
FALLBACK_OPEN = []   # mercado aberto removido a pedido do usuario (10/11): so OTC

def _asset_mode(st):
    return 'MANUAL'          # modo automatico removido: o bot opera so o ativo escolhido no painel (como ontem)

def _mark_bad(st, asset, seconds, reason):
    st.setdefault('_asset_bad', {})[asset] = (time.time() + seconds, reason)
    _add_log(st, f'[auto] {asset} fora da lista por {int(seconds // 60)} min: {reason}')

def _poison_path():
    return '/data/poison_assets.json' if os.path.isdir('/data') else 'poison_assets.json'

def _poison_load():
    # (so manual: o arquivo poison_assets.json pode ser editado a mao; o bot NAO aprende sozinho, ver POISON_LEARN)
    base = set()
    try:
        with open(_poison_path(), 'r', encoding='utf-8') as f:
            base |= set(json.load(f))
    except Exception:
        pass
    return base

POISON_LEARN = (os.environ.get('POISON_LEARN') or '0').strip() == '1'    # desligado: a queda parece depender de QUANTOS pedidos seguidos, nao do ativo
_POISON = _poison_load()

def _poison_add(asset):
    if asset in _POISON:
        return
    _POISON.add(asset)
    try:
        with open(_poison_path(), 'w', encoding='utf-8') as f:
            json.dump(sorted(_POISON), f)
    except Exception:
        pass

def _universe(api, st, username, mode):
    """Ativos candidatos: abertos em TURBO, do tipo escolhido, sem castigo e com payout aceitavel."""
    if mode == 'MANUAL':                       # so o ativo escolhido, sem quarentena nem filtro de payout
        a0 = st.get('manual_asset') or st.get('selected_asset')
        return [a0] if a0 else []
    res = get_user_conn_state(username).get('result') or {}
    base = list(res.get('otc_assets' if mode == 'OTC' else 'binary_assets') or [])
    if not base:
        base = list(FALLBACK_OTC) if mode == 'MANUAL' or True else []   # (10/11) so OTC: mercado aberto removido
    now = time.time()
    bad = st.get('_asset_bad', {})
    out = []
    for a in base:
        if a in _POISON:
            continue                            # ativo que derruba a conexao ao pedir velas (aprendido nos testes)
        b = bad.get(a)
        if b and b[0] > now:
            continue
        pay = _payout_cached(api, a)
        if pay is not None and pay < PAYOUT_MIN:
            continue
        out.append(a)
    return out

_RECONNECT_LOCK = threading.Lock()

def _reconnect_now(st, username, why):
    """Reconecta (uma thread por vez, no maximo a cada 60s). Retorna True se reconectou."""
    # espera crescente entre tentativas falhas (1, 2, 4, 8, 10 min): insistir no login a cada minuto pode fazer a IQ travar/limitar
    wait = min(60 * (2 ** int(st.get('_rc_fail', 0))), 180) if st.get('_rc_fail') else 60   # teto 3 min: 10 min parado perde o mercado
    if time.time() - st.get('_last_reconnect_ts', 0) <= wait:
        return False
    if not _RECONNECT_LOCK.acquire(blocking=False):
        return False
    st['_global_fail'] = 0
    st['_hv_fail'] = 0
    st['_last_reconnect_ts'] = time.time()
    st['_reconnecting'] = True
    _add_log(st, f'[auto] {why} - reconectando na IQ Option...')

    def _job():
        try:
            ok = _reconnect_from_loop(username, st)
            if ok:
                st['_rc_fail'] = 0
                _add_log(st, '[auto] Reconectado com sucesso')
            else:
                st['_rc_fail'] = int(st.get('_rc_fail', 0)) + 1
                _add_log(st, f'[auto] Proxima tentativa de reconexao em {int(min(60 * (2 ** st["_rc_fail"]), 180) // 60)} min')
        except Exception as ex:
            _add_log(st, f'[auto] Erro na reconexao: {str(ex)[:80]}')
        finally:
            st['_reconnecting'] = False
            _RECONNECT_LOCK.release()
    # em segundo plano: o ciclo do bot nao fica parado esperando o login da IQ (que pode demorar ate 75s)
    threading.Thread(target=_job, daemon=True, name='iq-reconnect').start()
    return False

def _api_alive(api):
    """False so quando a biblioteca diz com clareza que o websocket caiu."""
    fn = getattr(api, 'check_connect', None)
    if not fn:
        return True
    ok, alive = _call_with_timeout(fn, 3)
    return not (ok and alive is False)

_SUSPECT = {}          # ativo -> pontos de "estava sendo lido quando a conexao caiu" (vale ate reiniciar o servidor)

def _blame_recent(st, why):
    """A conexao morreu: culpa de leve os ativos lidos nos ultimos 100s. Um ativo que 'envenena' o socket
    aparece em TODAS as quedas e chega a 3 pontos; os inocentes aparecem de vez em quando."""
    now = time.time()
    seen = [x for (x, t) in list(st.get('_recent_reads', [])) if now - t <= 100]
    seen = list(dict.fromkeys(seen))
    for x in seen:
        _SUSPECT[x] = _SUSPECT.get(x, 0) + 1
    top = sorted(((v, k) for k, v in _SUSPECT.items() if v >= 2), reverse=True)[:5]
    if seen:
        _throttled_log(st, '_suspect_log', '[auto] Quedas de conexao: lidos nos ultimos 100s = ' + ', '.join(seen[:8]) +
                       ('' if not top else ' | mais suspeitos: ' + ', '.join(f'{k}({v})' for v, k in top)), every=20)
    for x in seen:
        if _SUSPECT.get(x, 0) >= 3 and x not in st.get('_asset_bad', {}):
            _mark_bad(st, x, 3600, f'caiu a conexao {_SUSPECT[x]}x logo apos ler este ativo')
            _SUSPECT[x] = 1

def _asset_fail(api, st, username, asset, why, heavy=False):
    """Registra falha ao ler velas. Pedidos grandes (historico) e pequenos contam em contadores separados;
    em ambos, varias falhas seguidas (de ativos diferentes) mostram que a conexao morreu e disparam a reconexao."""
    f = st.setdefault('_asset_fail', {})
    f[asset] = f.get(asset, 0) + 1
    key, limit = ('_hv_fail', 4) if heavy else ('_global_fail', 6)
    st[key] = st.get(key, 0) + 1
    _throttled_log(st, '_failwhy', f'[auto] falha ao ler velas de {asset}: {why} ({"historico" if heavy else "velas recentes"})', every=45)
    if 'closed' in str(why).lower():
        # "Connection is already closed": o websocket caiu de vez -> reconecta na hora (sem culpar o ativo)
        f[asset] = 0
        st['_global_fail'] = 0
        _blame_recent(st, why)
        _reconnect_now(st, username, 'Conexao com a IQ caiu (socket fechado)')
        return
    if not heavy and 'timeout' in str(why) and f[asset] >= 1:
        # Quem esta errado: o ativo ou a conexao? Teste com um ativo que ja respondeu antes.
        good = st.get('_last_good')
        # Se o proprio ativo "bom" e quem falhou, o teste e com OUTRO ativo (senao o teste nunca roda e a
        # conexao entalada fica parecendo problema do ativo - foi o que travou o bot em 09/11).
        probe = good if (good and good != asset) else next((x for x in list(FALLBACK_OTC) if x != asset), None)
        probe_ok = None
        if probe:
            okp, resp = _get_candles_safe(api, 6, probe, 60, 5, time.time())
            probe_ok = bool(okp and resp)
        if probe_ok is False or not _api_alive(api):
            f[asset] = 0
            # se o ativo lido logo antes respondeu bem e agora tudo morreu, ESTE ativo derrubou a conexao
            _lg = st.get('_last_good_ts', 0)
            if POISON_LEARN and good and good != asset and time.time() - _lg <= 25:
                _poison_add(asset)
                _add_log(st, f'[auto] {asset} derrubou a conexao ao pedir velas - removido da lista (guardado para as proximas vezes)')
            _blame_recent(st, why)
            _reconnect_now(st, username, f'Conexao com a IQ nao responde (teste com {good})')
            return
        # a conexao responde, so ESTE ativo nao: pula por 10 min
        f[asset] = 0
        _mark_bad(st, asset, 600, 'nao responde ao pedido de velas')
    elif f[asset] >= 3:
        f[asset] = 0
        _mark_bad(st, asset, 1800, f'sem velas da corretora ({why})')
    if st[key] >= limit:
        _reconnect_now(st, username, 'Varias falhas seguidas')

LIVE_CANDLES = int(os.environ.get('LIVE_CANDLES') or 320)      # velas por leitura (>= MIN_BARS+5 com folga; deixa a EMA 105 convergir)
LIVE_MAX_READS = int(os.environ.get('LIVE_MAX_READS') or 3)      # leituras por vela: a IQ para de responder (e derruba o socket) a partir do 4o pedido seguido
LIVE_PACE = float(os.environ.get('LIVE_PACE') or 1.0)            # pausa entre leituras de ativos diferentes (a IQ derruba o socket com rajadas)

def _fetch_closed(api, st, username, asset, count=None, timeout=7, retry=False):
    """Velas M1 FECHADAS do ativo. Retorna (lista|None, 'ok'|'stale'|'fail')."""
    if not _api_alive(api):
        _throttled_log(st, '_notalive', '[conexao] Socket da IQ fechado - reconectando (se aparecer "proxima tentativa", e o intervalo de espera)', every=30)
        _reconnect_now(st, username, 'Conexao com a IQ caiu')
        return None, 'fail'
    count = count or LIVE_CANDLES
    _rr = st.setdefault('_recent_reads', [])
    _rr.append((asset, time.time()))
    del _rr[:-30]
    t0 = time.time()
    ok, cs = _get_candles_safe(api, timeout, asset, 60, count, time.time())
    if not ok and retry:                                  # fora da janela de entrada da pra tentar de novo
        time.sleep(4)
        ok, cs = _get_candles_safe(api, timeout, asset, 60, count, time.time())
    lat = time.time() - t0
    st['_fetch_lat'] = lat if '_fetch_lat' not in st else 0.7 * st['_fetch_lat'] + 0.3 * lat
    if not ok or not cs or len(cs) < SV2.MIN_BARS + 5:
        _asset_fail(api, st, username, asset, 'resposta vazia' if ok else str(cs)[:40])
        return None, 'fail'
    st['_global_fail'] = 0
    st['_hv_fail'] = 0
    st['_last_good'] = asset
    st['_last_good_ts'] = time.time()
    st.setdefault('_asset_fail', {})[asset] = 0
    now_min = int(time.time() // 60 * 60)
    last_from = cs[-1].get('from')
    if last_from is None:
        return None, 'fail'
    if last_from >= now_min:
        return cs[:-1], 'ok'
    if last_from == now_min - 60 and time.time() % 60 >= 1.0:
        return cs, 'ok'
    return None, 'stale'

def _bootstrap_step(api, st, username, asset, deadline_sec=50):
    """Carrega o historico em blocos de 1000 velas, parando antes do proximo candle. Retorna True quando terminou."""
    boots = st.setdefault('_boot', {})
    b = boots.setdefault(asset, {'end': time.time(), 'got': {}, 'calls': 0})
    BLOCK = 500                       # blocos menores: a versao que funcionava pedia so 50 velas por vez
    need = max(1, (BOOT_CANDLES + BLOCK - 1) // BLOCK)
    finished = False
    if not _api_alive(api):
        _reconnect_now(st, username, 'Conexao com a IQ caiu')
        return False
    while time.time() % 60 < deadline_sec:
        if b['calls'] >= need or len(b['got']) >= BOOT_CANDLES:
            finished = True
            break
        ok, cs = _get_candles_safe(api, 20, asset, 60, BLOCK, b['end'])
        b['calls'] += 1
        if not ok or not cs:
            if not ok:
                b['err'] = str(cs)[:40]
                time.sleep(3)          # a biblioteca nao aceita chamadas sobrepostas: deixa a anterior terminar
            finished = True
            break
        time.sleep(0.6)                # pausa entre pedidos grandes (evita sobrecarregar a conexao)
        for c in cs:
            try:
                b['got'][int(c['from'])] = c
            except Exception:
                pass
        first = min(int(c['from']) for c in cs)
        if first >= b['end'] - 1:
            finished = True
            break
        b['end'] = first
    if not finished:
        return False
    now_min = int(time.time() // 60 * 60)
    hist = [b['got'][k] for k in sorted(b['got']) if k < now_min]
    boots.pop(asset, None)
    if len(hist) < SV2.MIN_BARS + 5:
        _asset_fail(api, st, username, asset, b.get('err') or f'historico de {len(hist)} velas', heavy=True)
        st.setdefault('_booted', {})[asset] = False
        st.setdefault('_boot_retry', {})[asset] = time.time() + 600
        return True
    _ENGINE.ingest(asset, hist)
    st['_hv_fail'] = 0
    st.setdefault('_booted', {})[asset] = True
    st.setdefault('_asset_ts', {})[asset] = time.time()
    return True

# Trava por ativo: LOSS_STREAK_MAX losses seguidos no mesmo ativo = QUARANTINE_MIN minutos fora da lista (os outros seguem)
LOSS_STREAK_MAX = int(os.environ.get('LOSS_STREAK_MAX') or 3)
QUARANTINE_MIN = int(os.environ.get('QUARANTINE_MIN') or 30)
# Trava por direcao (v3.2): DIR_STREAK_MAX losses SEGUIDOS na MESMA direcao (CALL ou PUT) no ativo = pausa de
# DIR_PAUSE_MIN minutos naquele ativo. Padrao visto nos graficos de 08/11: sequencias de 4 losses na mesma direcao
# quando a perna vira ou exaure - o bot para, avisa no painel, e o usuario troca o ativo manualmente.
LOSS_STOP_MAX = int(os.environ.get('LOSS_STOP_MAX') or 3)      # losses seguidos (qualquer direcao) = bot PARA de vez
DIR_STREAK_MAX = int(os.environ.get('DIR_STREAK_MAX') or 4)
DIR_PAUSE_MIN = int(os.environ.get('DIR_PAUSE_MIN') or 10)

def _asset_result(st, asset, profit, direction=''):
    """Memoria de resultados REAIS por ativo: se estiver rendendo mal, o ativo sai da lista por um tempo.
    v3.2: tambem conta losses SEGUIDOS na MESMA direcao - 4 seguidos pausam o ativo (mercado mudou de mao)."""
    if profit == 0:
        return
    streak = st.setdefault('_asset_streak', {})
    dstreak = st.setdefault('_dir_streak', {}).setdefault(asset, {})
    d = (direction or '').strip().upper()
    if d not in ('CALL', 'PUT'):
        d = ''
    if profit > 0:
        streak[asset] = 0
        st['_loss_streak_all'] = 0
        if d:
            dstreak[d] = 0
        st.get('_dir_pause_until', {}).pop(asset, None)   # um win encerra a pausa por direcao
        return
    # --- trava global primeiro: protege a banca em qualquer modo ---
    st['_loss_streak_all'] = int(st.get('_loss_streak_all', 0)) + 1
    if st['_loss_streak_all'] >= LOSS_STOP_MAX:
        st['running'] = False
        st['bot_running'] = False
        st['_loop_gen'] = int(st.get('_loop_gen', 0)) + 1      # o ciclo em andamento sai sozinho
        st['_loss_streak_all'] = 0
        _add_log(st, f'STOP TOTAL: {LOSS_STOP_MAX} losses seguidos - bot PARADO por seguranca. '
                     'NAO volta sozinho: quando quiser, clique Iniciar bot.')
        return
    streak[asset] = streak.get(asset, 0) + 1
    if d:
        other = 'PUT' if d == 'CALL' else 'CALL'
        dstreak[other] = 0
        dstreak[d] = dstreak.get(d, 0) + 1
        if dstreak[d] >= DIR_STREAK_MAX:
            dstreak[d] = 0
            st.setdefault('_dir_pause_until', {})[asset] = time.time() + DIR_PAUSE_MIN * 60
            _add_log(st, f'[pausa] {asset}: {DIR_STREAK_MAX} losses seguidos na mesma direcao ({d}) - pausa de {DIR_PAUSE_MIN} min neste ativo. O mercado mudou de mao - troque o ativo ou aguarde.')
    if st.get('asset_mode') == 'MANUAL':          # manual: voce escolheu o ativo, so aviso (use o Stop Loss para limitar)
        if streak[asset] >= LOSS_STREAK_MAX:
            _add_log(st, f'[manual] {asset}: {streak[asset]} losses seguidos (modo manual - continuo no ativo escolhido)')
        return
    if streak[asset] >= LOSS_STREAK_MAX:
        streak[asset] = 0
        _mark_bad(st, asset, QUARANTINE_MIN * 60, f'{LOSS_STREAK_MAX} losses seguidos - quarentena')

# ======================= CATALOGADOR =======================
# Roda os 4 setups nas ultimas CATALOG_CANDLES velas de cada ativo e mede o % de acerto. Independente do bot:
# so roda com o bot PARADO (a biblioteca da IQ nao aceita chamadas simultaneas) e o bot em modo automatico nunca cataloga.
CATALOG_CANDLES = int(os.environ.get('CATALOG_CANDLES') or 300)
_CATALOG = {}

def _catalog_path():
    return '/data/catalog_last.json' if os.path.isdir('/data') else 'catalog_last.json'

CATALOG_FETCH_TIMEOUT = float(os.environ.get('CATALOG_FETCH_TIMEOUT') or 15)

def _cat_sleep(cs, sec):
    """espera em fatias curtas e sai na hora se cancelarem"""
    end = time.time() + sec
    while time.time() < end and not cs.get('cancel'):
        time.sleep(0.2)

def _chunked_read(api, asset, total, chunk=80, pause=2.5, timeout=12, cs=None):
    """Le `total` velas M1 em PECAS PEQUENAS (chunk por pedido, com pausa entre eles) em vez de um
    pedido gigante - a IQ entala com rajada de pedidos grandes, entao trocamos volume por tempo
    (aprovado pelo usuario: pode demorar ate 60s por ativo). Retorna (velas, err)."""
    end = time.time()
    got, err = {}, ''
    need = (total + chunk - 1) // chunk
    for i in range(need):
        if cs and cs.get('cancel'):
            return [got[k] for k in sorted(got)], 'cancelado'
        ok, res = _get_candles_safe(api, timeout, asset, 60, chunk, end, cs=cs)
        if ok and res:
            for c in res:
                try:
                    got[int(c['from'])] = c
                except Exception:
                    pass
            first = min(int(c['from']) for c in res)
            if first >= end - 1:
                break
            end = first
        else:
            err = 'timeout' if (not ok and res == 'timeout') else ('cancelado' if (not ok and res == 'cancelado') else (str(res)[:50] if not ok else 'resposta vazia'))
            break
        if i < need - 1:
            time.sleep(pause)
    return [got[k] for k in sorted(got)], err

def _catalog_fetch(api, asset, total, cs):
    """Baixa `total` velas M1 em pedacos de 80 (um pedido por vez, com pausa entre eles).
    Cancelavel a qualquer momento. Retorna (velas_fechadas, erro)."""
    block = 80
    need = (total + block - 1) // block
    end = time.time()
    got, err = {}, ''
    for _ in range(need):
        res_cs = None
        for _try in (1, 2):
            if cs.get('cancel'):
                return [], 'cancelado'
            ok, res = _get_candles_safe(api, CATALOG_FETCH_TIMEOUT, asset, 60, block, end, cs=cs)
            if ok and res:
                res_cs = res
                break
            err = 'timeout' if (not ok and res == 'timeout') else ('cancelado' if (not ok and res == 'cancelado') else (str(res)[:50] if not ok else 'resposta vazia'))
            if err == 'cancelado':
                return [], err
            if not ok and not _api_alive(api):
                return [], 'conexao caiu'
            _cat_sleep(cs, 2)
        if not res_cs:
            break
        for c in res_cs:
            try:
                got[int(c['from'])] = c
            except Exception:
                pass
        first = min(int(c['from']) for c in res_cs)
        if first >= end - 1:
            break
        end = first
        _cat_sleep(cs, 2.5)
    now_min = int(time.time() // 60 * 60)
    return [got[k] for k in sorted(got) if k < now_min], err

def _catalog_ranked(rows):
    ok = [dict(r) for r in rows if r.get('status') == 'ok' and r.get('n', 0) > 0]
    ok.sort(key=lambda r: (-r['lb'], -r['n']))
    for i, r in enumerate(ok, 1):
        r['rank'] = i
    rest = [dict(r) for r in rows if not (r.get('status') == 'ok' and r.get('n', 0) > 0)]
    return ok + rest

def _catalog_save(cs):
    try:
        with open(_catalog_path(), 'w', encoding='utf-8') as f:
            json.dump({k: cs.get(k) for k in ('scope', 'rows', 'started', 'finished', 'total', 'done', 'eval_n', 'cancelled', 'error')}, f)
    except Exception as e:
        print(f"[CATALOGO] nao consegui salvar: {e}")

def _catalog_run(username, scope):
    cs = _CATALOG[username]
    st = get_user_state(username)
    try:
        try:
            if scope != 'ONE':      # modo MANUAL nao precisa da lista: menos pedidos = menos pressao na conexao
                _refresh_assets(username, force=True)
        except Exception as e:
            print(f"[CATALOGO] erro ao atualizar lista de ativos: {e}")
        if scope == 'ONE':          # modo MANUAL (pedido do usuario): so o ativo selecionado no painel
            a0 = st.get('manual_asset') or st.get('selected_asset') or 'EURUSD'
            assets = [(a0, 'OTC' if _is_otc(a0) else 'OPEN')]
        else:
            res = get_user_conn_state(username).get('result') or {}
            otc = list(res.get('otc_assets') or []) or list(FALLBACK_OTC)
            opn = list(res.get('binary_assets') or []) or list(FALLBACK_OPEN)
            assets = ([(x, 'OTC') for x in otc] if scope in ('OTC', 'ALL') else []) + ([(x, 'OPEN') for x in opn] if scope in ('OPEN', 'ALL') else [])
        cs['total'] = len(assets)
        _add_log(st, f'[catalogador] Iniciado: {len(assets)} ativo(s) ({scope}), {cs["eval_n"]} velas cada')
        fails = 0
        def _do(asset, mk):
            nonlocal fails
            cs['current'] = asset
            api = _IQ_APIS.get(username)
            if api is None:
                cs['error'] = 'sem conexao com a IQ'
                return None
            if not _api_alive(api):
                _reconnect_now(st, username, 'Catalogador: conexao caiu')
                _cat_sleep(cs, 3)
                for _w in range(40):
                    if not st.get('_reconnecting') or cs.get('cancel'):
                        break
                    _cat_sleep(cs, 1)
                api = _IQ_APIS.get(username)
                if api is None or not _api_alive(api):
                    return {'asset': asset, 'mode': mk, 'status': 'falhou', 'err': 'conexao caiu', 'n': 0, 'w': 0, 'l': 0, 't': 0, 'wr': 0.0, 'lb': 0.0}
            hist, err = _catalog_fetch(api, asset, cs['eval_n'] + SV2.MIN_BARS + 30, cs)
            if cs.get('cancel'):
                return None
            row = {'asset': asset, 'mode': mk}
            r = SV2.catalog_eval(hist, cs['eval_n']) if len(hist) >= SV2.MIN_BARS + 20 else None
            if r is None:
                row.update(status='falhou', err=err or f'so {len(hist)} velas', n=0, w=0, l=0, t=0, wr=0.0, lb=0.0)
                fails += 1
                if fails >= 3:
                    fails = 0
                    _reconnect_now(st, username, 'Catalogador: varias falhas seguidas')
                    _cat_sleep(cs, 3)
                    for _w in range(40):
                        if not st.get('_reconnecting') or cs.get('cancel'):
                            break
                        _cat_sleep(cs, 1)
            else:
                fails = 0
                row.update(status='ok', **r)
            return row
        pending = list(assets)
        for passo in (1, 2):
            retry = []
            for asset, mk in pending:
                if cs.get('cancel'):
                    cs['cancelled'] = True
                    break
                row = _do(asset, mk)
                if row is None:
                    if cs.get('cancel'):
                        cs['cancelled'] = True
                    break
                if row['status'] != 'ok' and passo == 1:
                    retry.append((asset, mk))      # tenta de novo no fim, sem poluir o ranking
                else:
                    cs['rows'].append(row)
                    cs['done'] = len(cs['rows'])
                _cat_sleep(cs, 1.5)
            if cs.get('cancelled') or cs.get('error') or not retry:
                break
            _add_log(st, f'[catalogador] Segunda tentativa em {len(retry)} ativos que falharam')
            pending = retry
    except Exception as e:
        import traceback
        traceback.print_exc()
        cs['error'] = str(e)[:120]
    finally:
        cs['current'] = ''
        cs['finished'] = time.time()
        ok_n = sum(1 for r in cs['rows'] if r.get('status') == 'ok' and r.get('n', 0) > 0)
        _add_log(st, f'[catalogador] {"Cancelado" if cs.get("cancelled") else "Concluido"}: {len(cs["rows"])} ativos lidos, {ok_n} com sinais')
        _catalog_save(cs)
        cs['running'] = False                 # por ultimo: o painel so mostra "concluido" depois de salvar

_DIAG = {}

def _diag_run(username, assets):
    d = _DIAG[username]
    try:
        api = _IQ_APIS.get(username)
        d['alive'] = bool(api and _api_alive(api))
        for asset in assets:
            for count in (50, 200):
                if api is None:
                    break
                t0 = time.time()
                ok, res = _get_candles_safe(api, 12, asset, 60, count, time.time())
                n = len(res) if ok and res else 0
                d['rows'].append({'asset': asset, 'pedidas': count, 'recebidas': n, 'seg': round(time.time() - t0, 1),
                                  'resultado': 'ok' if n else ('timeout' if (not ok and res == 'timeout') else str(res)[:40] if not ok else 'vazio')})
                time.sleep(1)
    except Exception as e:
        d['error'] = str(e)[:120]
    finally:
        d['running'] = False

@app.route('/api/diag/candles', methods=['GET'])
def diag_candles():
    """Diagnostico: abre no navegador (com o bot PARADO). 1a chamada inicia o teste; recarregue para ver o resultado."""
    u = current_user()
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    d = _DIAG.get(username)
    if d and d.get('running'):
        return jsonify(running=True, alive=d.get('alive'), rows=d['rows']), 200
    if st.get('running') or (_CATALOG.get(username) or {}).get('running'):
        return jsonify(ok=False, error='Pare o bot e o catalogador antes do diagnostico.'), 200
    if not _IQ_APIS.get(username):
        return jsonify(ok=False, error='Conecte na corretora primeiro.'), 200
    if d and request.args.get('ver') == '1':
        return jsonify(running=False, alive=d.get('alive'), rows=d['rows'], error=d.get('error')), 200
    assets = [x for x in (request.args.get('assets') or 'EURUSD-OTC,AUDUSD-OTC,GBPJPY-OTC,USDJPY-OTC').split(',') if x]
    _DIAG[username] = {'running': True, 'rows': [], 'alive': None}
    threading.Thread(target=_diag_run, args=(username, assets), daemon=True).start()
    return jsonify(ok=True, msg='Teste iniciado (~1 min). Abra /api/diag/candles?ver=1 para ver o resultado.'), 200

@app.route('/api/catalog/start', methods=['POST'])
def catalog_start():
    u = current_user()
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    data = request.get_json(silent=True) or {}
    scope = str(data.get('scope') or 'ALL').upper()
    if scope not in ('ONE', 'OTC', 'OPEN', 'ALL'):
        scope = 'ONE'
    if scope == 'ONE' and not (st.get('manual_asset') or st.get('selected_asset')):
        return jsonify(ok=False, error='Selecione o ativo no painel (Operar > Ativo) antes de catalogar.'), 200
    if st.get('running') or st.get('bot_running'):
        return jsonify(ok=False, error='Pare o bot antes de catalogar (a IQ nao aceita as duas coisas ao mesmo tempo).'), 200
    if (_SENT.get(username) or {}).get('running'):
        return jsonify(ok=False, error='A varredura de sentimento esta rodando - espere ou cancele (a IQ nao aceita as duas ao mesmo tempo).'), 200
    cur = _CATALOG.get(username)
    if cur and cur.get('running'):
        return jsonify(ok=False, error='O catalogador ja esta rodando.'), 200
    if not _IQ_APIS.get(username):
        return jsonify(ok=False, error='Conecte na corretora primeiro.'), 200
    _CATALOG[username] = {'running': True, 'scope': scope, 'rows': [], 'total': 0, 'done': 0, 'current': '',
                          'started': time.time(), 'finished': None, 'eval_n': CATALOG_CANDLES, 'cancel': False,
                          'cancelled': False, 'error': None}
    threading.Thread(target=_catalog_run, args=(username, scope), daemon=True).start()
    return jsonify(ok=True, scope=scope), 200

@app.route('/api/catalog/cancel', methods=['POST'])
def catalog_cancel():
    u = current_user()
    cs = _CATALOG.get(u.get('sub', 'admin'))
    if cs and cs.get('running'):
        cs['cancel'] = True
        _add_log(get_user_state(u.get('sub', 'admin')), '[catalogador] Cancelando...')
    return jsonify(ok=True), 200

@app.route('/api/catalog/status', methods=['GET'])
def catalog_status():
    u = current_user()
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    cs = _CATALOG.get(username)
    if cs is None:                                   # depois de reiniciar o servidor: mostra o ultimo resultado salvo
        try:
            with open(_catalog_path(), 'r', encoding='utf-8') as f:
                d = json.load(f)
            cs = {'running': False, 'current': '', 'cancel': False, **d}
        except Exception:
            cs = None
    if cs is None:
        return jsonify(ok=True, running=False, rows=[], total=0, done=0, empty=True, bot_running=bool(st.get('running')),
                       eval_n=CATALOG_CANDLES), 200
    return jsonify(ok=True, running=bool(cs.get('running')), scope=cs.get('scope'), total=cs.get('total', 0), done=cs.get('done', 0),
                   current=cs.get('current', ''), rows=_catalog_ranked(cs.get('rows') or []), started=cs.get('started'),
                   finished=cs.get('finished'), error=cs.get('error'), cancelled=bool(cs.get('cancelled')), cancelling=bool(cs.get('cancel') and cs.get('running')),
                   eval_n=cs.get('eval_n', CATALOG_CANDLES), bot_running=bool(st.get('running'))), 200

# ======================= SENTIMENTO DO MERCADO (09/11, feito do zero) =======================
# Varre UM ativo por vez (a IQ entala com pedidos paralelos - era o problema do catalogador velho)
# e pontua de 0 a 100 o quanto o MOMENTO de cada ativo combina com os 5 setups:
#   - regime claro (tendencia empilhada OU lateral de verdade)
#   - preco perto da EMA 21 (pullback possivel, nao perna esticada)
#   - volatilidade saudavel (mercado nem morto nem em furia)
#   - atividade recente dos proprios setups (sinais nas ultimas 90 velas)
# NAO envia ordem nenhuma - so le velas. Requer o bot parado (mesma conexao).
_SENT = {}

def _sent_wilder_rsi(closes, period=14):
    """RSI de Wilder (o mesmo calculo da estrategia), em python puro."""
    deltas = [float(closes[k]) - float(closes[k - 1]) for k in range(1, len(closes))]
    if len(deltas) < period + 1:
        return 50.0
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for k in range(period, len(deltas)):
        ag = (ag * (period - 1) + gains[k]) / period
        al = (al * (period - 1) + losses[k]) / period
    if al < 1e-12:
        return 100.0 if ag > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + ag / al)

def _sent_score(hist):
    """Recebe velas M1 brutas; retorna dict com regime/metricas/score (ou status falhou)."""
    try:
        import numpy as _np
        n = len(hist)
        if n < SV2.MIN_BARS + 30:
            return {'status': 'falhou', 'err': f'so {n} velas'}
        o = _np.array([x['open'] for x in hist], float)
        h = _np.array([x['max'] for x in hist], float)
        l = _np.array([x['min'] for x in hist], float)
        c = _np.array([x['close'] for x in hist], float)
        F = SV2.features(o, h, l, c)
        i = n - 1
        atr = float(F['atr'][i]) or 1e-9
        e21, e55, e105 = float(F['e21'][i]), float(F['e55'][i]), float(F['e105'][i])
        slope = (e21 - float(F['e21'][max(0, i - 3)])) / (3.0 * atr)   # forca da media rapida (ATR por vela)
        er = float(F['er'][i])
        rsi = _sent_wilder_rsi(c[-40:])
        dist = abs(float(c[i]) - e21) / atr                            # esticamento do preco vs EMA 21 (ATRs)
        atr_pct = atr / float(c[i]) * 100.0
        tp_slope = SV2.P['tp_slope'] or 1e-9
        if e21 > e55 > e105 and slope > 0.10:
            regime = 'TENDENCIA DE ALTA'
        elif e21 < e55 < e105 and slope < -0.10:
            regime = 'TENDENCIA DE BAIXA'
        elif er < SV2.P['rr_er']:
            regime = 'LATERAL'
        else:
            regime = 'SEM DIRECAO DEFINIDA'
        score = 0.0
        if regime.startswith('TENDENCIA'):
            score += 30 + min(20.0, abs(slope) / tp_slope * 20.0)      # tendencia CLARA (qualquer lado) vale pontos
        elif regime == 'LATERAL':
            score += 12 + min(14.0, (SV2.P['rr_er'] - er) / (SV2.P['rr_er'] or 1e-9) * 14.0)
        if dist <= 0.6:
            score += 15
        elif dist <= 1.2:
            score += 10
        elif dist <= 2.0:
            score += 5
        if 30 <= rsi <= 70:
            score += 5                                                  # RSI no meio: espaco pro movimento
        cnt = {'CALL': 0, 'PUT': 0}
        per = {}
        for j in range(max(SV2.MIN_BARS, i - 90), i + 1):               # atividade recente dos proprios setups
            for s, d, _ in SV2.signals_at(F, j):
                cnt[d] = cnt.get(d, 0) + 1
                per[s] = per.get(s, 0) + 1
        sig_n = cnt['CALL'] + cnt['PUT']
        score += min(15.0, sig_n * 3.0)
        if 0.010 <= atr_pct <= 0.30:
            score += 10
        elif 0.005 <= atr_pct <= 0.60:
            score += 5
        score = int(round(max(0.0, min(100.0, score))))
        verdict = 'EXCELENTE' if score >= 70 else ('FAVORAVEL' if score >= 55 else ('NEUTRO' if score >= 40 else 'EVITAR'))
        bias = ('puxao CALL' if cnt['CALL'] > cnt['PUT'] * 1.5 else
                ('puxao PUT' if cnt['PUT'] > cnt['CALL'] * 1.5 else 'equilibrado'))
        abbr = {'TREND_PULLBACK': 'TPB', 'TREND_CONT': 'TC', 'RANGE_REVERSION': 'RR',
                'BREAKOUT_PULLBACK': 'BP', 'SPIKE_REVERSAL': 'SR'}
        setups = ' - '.join(abbr.get(k, k) + ' x' + str(v) for k, v in sorted(per.items(), key=lambda x: -x[1])[:3])
        return {'status': 'ok', 'regime': regime, 'score': score, 'verdict': verdict, 'rsi': round(rsi, 1),
                'slope': round(slope, 3), 'er': round(er, 2), 'dist': round(dist, 2), 'atr_pct': round(atr_pct, 3),
                'sig_n': sig_n, 'call': cnt['CALL'], 'put': cnt['PUT'], 'bias': bias, 'setups': setups}
    except Exception as e:
        return {'status': 'falhou', 'err': str(e)[:90]}

def _sent_run(username, scope):
    ss = _SENT[username]
    st = get_user_state(username)
    try:
        if scope == 'ONE':          # modo MANUAL (pedido do usuario): so o ativo selecionado no painel
            a0 = st.get('manual_asset') or st.get('selected_asset') or 'EURUSD'
            assets = [(a0, 'OTC' if _is_otc(a0) else 'OPEN')]
        else:
            res = get_user_conn_state(username).get('result') or {}
            otc = list(res.get('otc_assets') or []) or list(FALLBACK_OTC)
            opn = list(res.get('binary_assets') or []) or list(FALLBACK_OPEN)
            assets = ([(x, 'OTC') for x in otc] if scope in ('OTC', 'ALL') else []) + \
                     ([(x, 'OPEN') for x in opn] if scope in ('OPEN', 'ALL') else [])
        ss['total'] = len(assets)
        _add_log(st, f'[sentimento] Iniciado: {len(assets)} ativo(s) ({scope}) - UM por vez, 340 velas cada')
        fails = 0
        for asset, mk in assets:
            if ss.get('cancel'):
                ss['cancelled'] = True
                break
            ss['current'] = asset
            api = _IQ_APIS.get(username)
            if api is None:
                ss['error'] = 'sem conexao com a IQ'
                break
            candles, err_c = _chunked_read(api, asset, 340, chunk=80, pause=2.5, timeout=12, cs=ss)
            ok_c = len(candles) >= SV2.MIN_BARS + 30
            if not ok_c and not ss.get('cancel'):
                # nao repete o pedido grande no escuro: primeiro testa se a CONEXAO responde (pedido pequeno
                # em outro ativo). Se nao responder, a IQ entalou (depois de uma rajada ela para de responder)
                # -> reconecta na hora em vez de culpar o ativo.
                probe = next((x for x in list(FALLBACK_OTC) + list(FALLBACK_OPEN) if x != asset), None)
                viva = True
                if probe:
                    pok, pres = _get_candles_safe(api, 8, probe, 60, 5, time.time())
                    viva = bool(pok and pres)
                if not viva or not _api_alive(api):
                    _reconnect_now(st, username, 'Sentimento: a conexao parou de responder (teste com ' + (probe or '?') + ')')
                    for _w in range(90):
                        if not st.get('_reconnecting') or ss.get('cancel'):
                            break
                        time.sleep(1)
                    api = _IQ_APIS.get(username)
                    if api is None:
                        ss['error'] = 'sem conexao com a IQ'
                        break
                else:
                    time.sleep(2.0)     # conexao viva: foi o ativo que demorou - segunda chance
                candles, err_c = _chunked_read(api, asset, 340, chunk=80, pause=2.5, timeout=12, cs=ss)
                ok_c = len(candles) >= SV2.MIN_BARS + 30
            if ok_c and candles and len(candles) >= SV2.MIN_BARS + 30:
                fails = 0
                r = _sent_score(candles) or {'status': 'falhou', 'err': 'dados insuficientes'}
            else:
                fails += 1
                r = {'status': 'falhou', 'err': (err_c or 'velas insuficientes')}
                if fails >= 2 and not ss.get('cancel'):
                    fails = 0
                    _reconnect_now(st, username, 'Sentimento: falhas seguidas de velas')
                    for _w in range(40):
                        if not st.get('_reconnecting') or ss.get('cancel'):
                            break
                        time.sleep(1)
            ss['rows'].append({'asset': asset, 'mode': mk, **r})
            ss['done'] += 1
            if r.get('status') == 'ok':
                _add_log(st, f"[sentimento] {asset}: {r['regime']} - score {r['score']} ({r['verdict']}) - "
                             f"RSI {r['rsi']}, forca {r['slope']} ATR/vela, ER {r['er']}, vol {r['atr_pct']}% - "
                             f"{r['sig_n']} sinais nas ultimas 90 velas ({r['bias']})")
            else:
                _add_log(st, f"[sentimento] {asset}: sem dados ({r.get('err')})")
            for _w in range(25):                                        # ~2.5s entre ativos: ritmo calmo pra IQ
                if ss.get('cancel'):
                    break
                time.sleep(0.1)
    except Exception as e:
        ss['error'] = str(e)[:120]
    finally:
        ss['running'] = False
        ss['finished'] = time.time()
        ss['current'] = ''
        oks = sorted([x for x in ss['rows'] if x.get('status') == 'ok'], key=lambda x: -x.get('score', 0))
        top = ' | '.join(x['asset'] + ' ' + str(x['score']) for x in oks[:3])
        _add_log(st, ('[sentimento] ' + ('CANCELADO' if ss.get('cancelled') else 'Concluido') +
                      '. Melhores momentos: ' + (top or 'nenhum ativo com dados')))

@app.route('/api/sentimento/start', methods=['POST'])
def sentimento_start():
    u = current_user()
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    data = request.get_json(silent=True) or {}
    scope = str(data.get('scope') or 'ALL').upper()
    if scope not in ('ONE', 'OTC', 'OPEN', 'ALL'):
        scope = 'ONE'
    if scope == 'ONE' and not (st.get('manual_asset') or st.get('selected_asset')):
        return jsonify(ok=False, error='Selecione o ativo no painel (Operar > Ativo) antes de medir.'), 200
    if st.get('running') or st.get('bot_running'):
        return jsonify(ok=False, error='Pare o bot antes de medir o sentimento (mesma conexao).'), 200
    if (_CATALOG.get(username) or {}).get('running'):
        return jsonify(ok=False, error='O catalogador esta rodando - espere ou cancele.'), 200
    cur = _SENT.get(username)
    if cur and cur.get('running'):
        return jsonify(ok=False, error='A varredura de sentimento ja esta rodando.'), 200
    if not _IQ_APIS.get(username):
        return jsonify(ok=False, error='Conecte na corretora primeiro.'), 200
    _SENT[username] = {'running': True, 'scope': scope, 'rows': [], 'total': 0, 'done': 0, 'current': '',
                       'started': time.time(), 'finished': None, 'cancel': False, 'cancelled': False, 'error': None}
    threading.Thread(target=_sent_run, args=(username, scope), daemon=True).start()
    return jsonify(ok=True, scope=scope), 200

@app.route('/api/sentimento/cancel', methods=['POST'])
def sentimento_cancel():
    u = current_user()
    ss = _SENT.get(u.get('sub', 'admin'))
    if ss and ss.get('running'):
        ss['cancel'] = True
        _add_log(get_user_state(u.get('sub', 'admin')), '[sentimento] Cancelando...')
    return jsonify(ok=True), 200

@app.route('/api/sentimento/status', methods=['GET'])
def sentimento_status():
    username = current_user().get('sub', 'admin')
    st = get_user_state(username)
    ss = _SENT.get(username)
    if ss is None:
        return jsonify(ok=True, running=False, rows=[], total=0, done=0, empty=True,
                       bot_running=bool(st.get('running'))), 200
    rows = sorted([x for x in (ss.get('rows') or []) if x.get('status') == 'ok'], key=lambda x: -x.get('score', 0)) + \
           [x for x in (ss.get('rows') or []) if x.get('status') != 'ok']
    return jsonify(ok=True, running=bool(ss.get('running')), scope=ss.get('scope'), total=ss.get('total', 0),
                   done=ss.get('done', 0), current=ss.get('current', ''), rows=rows, started=ss.get('started'),
                   finished=ss.get('finished'), error=ss.get('error'), cancelled=bool(ss.get('cancelled')),
                   cancelling=bool(ss.get('cancel') and ss.get('running')), bot_running=bool(st.get('running'))), 200

def _auto_window(api, st, username, entry_value, mode, uni, candle_ts):
    """Primeiros segundos da vela: olha os melhores ativos e entra no melhor sinal validado."""
    st['_auto_key'] = candle_ts
    direct = (SV2.GATE == 'off')              # filtro desligado: sem historico, so le velas recentes e procura sinal
    booted = [a for a in uni if st.get('_booted', {}).get(a)]
    pool = list(uni) if direct else booted
    if not pool:
        return
    lat = max(0.25, st.get('_fetch_lat', 0.6))
    n_watch = int(min(LIVE_MAX_READS, max(1, (ENTRY_MAX_SECONDS - 2.0) / (lat + LIVE_PACE))))
    cur = 0
    if direct:                                 # rodizio: n ativos por vela, um de cada vez
        cur = st.get('_rr_direct', 0) % len(pool)
        watch = [pool[(cur + k) % len(pool)] for k in range(min(n_watch, len(pool)))]
        ranked = watch
    else:
        ranked = sorted(booted, key=lambda a: -_ENGINE.asset_score(a))
        watch = ranked[:max(1, n_watch - 1)]
        rest = [a for a in ranked if a not in watch]
        if rest:                               # um ativo extra por vela, em rodizio, para nenhum ficar esquecido
            st['_rr'] = (st.get('_rr', -1) + 1) % len(rest)
            watch.append(rest[st['_rr']])

    focus = ranked[0]
    if direct:
        st['_focus'] = focus
        st['selected_asset'] = focus
    elif focus != st.get('_focus'):
        old = st.get('_focus')
        if old and old not in uni:
            why = 'o anterior fechou ou saiu da lista'
        elif old and st.get('_asset_bad', {}).get(old, (0,))[0] > time.time():
            why = st['_asset_bad'][old][1]
        else:
            why = 'melhor nota agora'
        _add_log(st, f'[auto] Ativo em foco: {focus}' + (f' (antes {old}: {why})' if old else '') +
                 f' | nota {_ENGINE.asset_score(focus):.0f}')
        st['_focus'] = focus
        st['selected_asset'] = focus

    cands = []
    visited = 0
    for asset in watch:
        if time.time() - candle_ts > ENTRY_MAX_SECONDS - 1.5 or int(time.time() // 60 * 60) != candle_ts:
            break                              # acabou o tempo da janela
        visited += 1
        closed, status = _fetch_closed(api, st, username, asset)
        _tries = 0
        while status == 'stale' and _tries < 2 and time.time() - candle_ts < ENTRY_MAX_SECONDS - 1.5:
            _tries += 1                        # a IQ costuma publicar a vela fechada 1-3s depois: ESPERA (2s) em vez de
            time.sleep(2.0)                    # martelar o socket com pedidos seguidos (isso derrubava a conexao)
            closed, status = _fetch_closed(api, st, username, asset)
        if len(watch) > 1:
            time.sleep(LIVE_PACE)              # espaca as leituras: rajada de pedidos faz a IQ fechar o socket
        if status != 'ok':
            _throttled_log(st, '_stat_' + status, f'[analise] {asset}: leitura de velas sem sucesso ({"IQ ainda nao publicou a vela fechada" if status == "stale" else "falha/conexao"}) - tento de novo na proxima vela', every=25)
            continue
        sigs = _ENGINE.ingest(asset, closed)
        st.setdefault('_asset_ts', {})[asset] = time.time()
        try:
            _F = SV2.features([x['open'] for x in closed], [x['max'] for x in closed], [x['min'] for x in closed], [x['close'] for x in closed])
            _i = len(closed) - 1
            _sg = ', '.join(f'{g["setup"]} {g["dir"]}' for g in sigs) or 'nenhum sinal'
            st['_hb'] = {'asset': asset, 'ts': time.time(), 'price': float(closed[-1]['close']), 'rsi': float(_F['rsi'][_i]),
                         'er': float(_F['er'][_i]), 'z': float(_F['z'][_i]), 'ar': float(_F['atr_ratio'][_i]), 'sg': _sg, 'n': len(closed)}
            _h = st['_hb']
            _add_log(st, f'[analise] {asset} | {_h["n"]} velas | preco {_h["price"]:.5f} | RSI {_h["rsi"]:.0f} | forca {_h["er"]:.2f} | desvio {_h["z"]:+.1f} | vol {_h["ar"]:.2f} -> {_sg}')
        except Exception as _e:
            _throttled_log(st, '_hb_err', f'[analise] {asset}: velas lidas ({len(closed)}) mas nao consegui medir: {_e}', every=120)
        mi = _ENGINE.manip_info(asset)
        if mi.get('reason'):
            _bloq = ' - setups normais bloqueados por 5 velas' if os.environ.get('MANIP_BLOCK', '0') == '1' else ''
            _throttled_log(st, f'_manip_{asset}', f'[info] {asset}: {mi["reason"]}{_bloq}', every=300)
        for sg in sigs:
            h, why, lb = _ENGINE.decide_ex(asset, sg['setup'], _allowed_h(asset))
            det = sg['det']
            info = f'{sg["setup"]} {sg["dir"]} em {asset} (RSI {det["rsi"]}, ER {det["er"]}' + (f', {det["tipo"]}' if det.get('tipo') else '') + ')'
            if h is None:
                _throttled_log(st, f'_skip_{asset}_{sg["setup"]}', f'[v2] {info} - NAO entrei: {why}', every=180)
                continue
            cands.append({'asset': asset, 'sg': sg, 'h': h, 'why': why, 'lb': lb, 'info': info,
                          'score': _ENGINE.asset_score(asset)})
    if direct:
        st['_rr_direct'] = (cur + max(1, visited)) % len(pool)
    if not cands:
        if direct:
            _throttled_log(st, '_auto_idle', f'[auto] Procurando sinais em {len(pool)} ativos ({mode}), {len(watch)} por vela, {LIVE_CANDLES} velas cada - nenhum sinal agora (aviso a cada 5 min)', every=300)
        else:
            _throttled_log(st, '_auto_idle', f'[auto] Varredura {mode}: {len(booted)} ativos medidos, nenhum sinal valido agora (aviso a cada 30 min)', every=1800)
        return

    cands.sort(key=lambda x: (-x['lb'], -x['score']))
    best = cands[0]
    asset, sg, h, info = best['asset'], best['sg'], best['h'], best['info']
    if SV2.GATE == 'off' and str(st.get('broker_account_type') or 'PRACTICE').upper() not in ('PRACTICE', 'DEMO'):
        _throttled_log(st, '_real_warn', '[v2] ATENCAO: conta REAL operando sem filtro estatistico (so com a trava de 3 losses por ativo)', every=3600)
    if mode == 'OPEN' and not _session_ok(asset):
        _throttled_log(st, '_v2_sess_log', f'[v2] {info} - fora do horario de liquidez ({SESSION_UTC} UTC), nao entrei', every=300)
        return
    if time.time() < st.get('_busy_until', 0):
        _add_log(st, f'[v2] {info} - ja existe uma operacao aberta, nao entrei')
        return
    late = time.time() - candle_ts
    if late > ENTRY_MAX_SECONDS:
        _add_log(st, f'[v2] {info} - pronto so a {late:.0f}s do nascimento da vela, entrada pulada (limite {ENTRY_MAX_SECONDS:.0f}s)')
        return
    _pt = st.get('_dir_pause_until', {}).get(asset, 0)
    if time.time() < _pt:
        _throttled_log(st, '_dir_pause_log', f'[pausa] {asset} pausado por mais {int((_pt - time.time()) / 60) + 1} min (losses seguidos na mesma direcao) - troque o ativo ou aguarde', every=60)
        return
    if len(cands) > 1:
        _add_log(st, f'[auto] {len(cands)} sinais validos nesta vela; escolhi {asset} (maior limite inferior)')
    _add_log(st, f'[v2] SINAL {info} | {best["why"]}')
    label = datetime.datetime.fromtimestamp(candle_ts, BRT).strftime('%H:%M')
    meta = {'setup': sg['setup'], 'h': h, 'dir': sg['dir'], 'det': sg['det']}
    st['selected_asset'] = asset
    outcome = _execute_entry(api, st, username, asset, sg['dir'], entry_value, h, candle_ts, label, meta=meta)
    if outcome == 'ok':
        st['_busy_until'] = time.time() + h * 60 + 20
    elif outcome == 'timeout':
        st['_busy_until'] = time.time() + h * 60 + 20
        _mark_bad(st, asset, 900, 'ordem sem resposta (pode ter sido enviada)')
    else:
        _mark_bad(st, asset, 900, 'ordem recusada/cancelada pela corretora')

def _auto_background(api, st, username, mode, uni):
    """Fora da janela de entrada: atualiza a lista de ativos, carrega historicos e mantem as medicoes em dia."""
    return           # sem tarefas pesadas em segundo plano (payout, lista de ativos): a IQ derruba a conexao com excesso de pedidos
    sec = time.time() % 60
    if sec > 50:
        return
    res = get_user_conn_state(username).get('result') or {}
    pc = _PAYOUT_CACHE.get(id(api))
    if not pc or time.time() - pc[0] > 300:
        _payout(api, 'EURUSD')              # atualiza o cache de payouts (fora da janela de entrada)
        return
    if time.time() - res.get('assets_ts', 0) >= 600:
        _refresh_assets(username)
        return
    if SV2.GATE == 'off':                       # modo direto: nao carrega historico nem mede nada em segundo plano
        return
    retry = st.get('_boot_retry', {})
    todo = [a for a in uni if not st.get('_booted', {}).get(a) and retry.get(a, 0) <= time.time()]
    if todo:
        # continua primeiro o que ja estava em andamento
        todo.sort(key=lambda a: (a not in st.get('_boot', {}), uni.index(a)))
        a = todo[0]
        if _bootstrap_step(api, st, username, a):
            n_ok = sum(1 for x in uni if st.get('_booted', {}).get(x))
            _throttled_log(st, '_boot_prog', f'[auto] Historico carregado: {n_ok}/{len(uni)} ativos de {mode}', every=25)
            if n_ok == len(uni) or not [x for x in uni if not st.get('_booted', {}).get(x) and retry.get(x, 0) <= time.time()]:
                rows = _ENGINE.summary(None, DIGITAL_EXPS if (OPEN_PRODUCT == 'digital' and mode != 'OTC') else None)
                ok_n = sum(1 for r in rows if r['status'] == 'APROVADO' and r['asset'] in uni)
                _add_log(st, f'[auto] Varredura pronta: {n_ok} ativos medidos, {ok_n} combinacoes APROVADAS no total '
                             f'(exijo >= {SV2.MIN_LB * 100:.0f}% de limite inferior e {SV2.MIN_N}+ sinais; filtro {SV2.GATE})')
        return
    ts = st.setdefault('_asset_ts', {})
    stale = sorted([a for a in uni if st.get('_booted', {}).get(a) and time.time() - ts.get(a, 0) > 240], key=lambda a: ts.get(a, 0))
    if stale:
        closed, status = _fetch_closed(api, st, username, stale[0], retry=True)
        if status == 'ok':
            _ENGINE.ingest(stale[0], closed)
            ts[stale[0]] = time.time()
        elif status == 'stale':
            ts[stale[0]] = time.time() - 200

def _auto_step(api, st, username, entry_value):
    """Um passo do modo automatico (sempre M1, velas fechadas). Retorna quantos segundos dormir."""
    def nxt():
        return max(0.1, min(2.0, 60 - (time.time() % 60) - 0.35))

    mode = _asset_mode(st)
    uni = _universe(api, st, username, mode)
    n_prev = st.get('_uni_n')
    key_uni = (mode, len(uni), uni[0] if (mode == 'MANUAL' and uni) else '')
    if n_prev != key_uni:
        st['_uni_n'] = key_uni
        if mode == 'MANUAL':
            _add_log(st, f'[manual] Operando somente {uni[0] if uni else "(nenhum ativo escolhido)"} - procuro sinais so nele')
    if n_prev != key_uni and mode != 'MANUAL':
        res = get_user_conn_state(username).get('result') or {}
        src = 'binaria' if mode == 'OTC' else ('digital' if OPEN_PRODUCT == 'digital' else 'binaria')
        extra = '' if len(uni) >= 30 else ' (a IQ esta listando menos de 30 agora; nao da para criar ativos que ela nao oferece)'
        _add_log(st, f'[auto] Modo {"OTC" if mode == "OTC" else "Mercado aberto"}: {len(uni)} ativos abertos em {src.upper()} agora, para varrer{extra}')
    g = SV2.GATE
    if st.get('_gate_seen') != g:
        st['_gate_seen'] = g
        _add_log(st, f'[auto] Modo direto: sem filtro estatistico e sem historico; leio {LIVE_CANDLES} velas de um ativo por vez. Trava global: 3 losses seguidos (qualquer direcao) = bot PARA por inteiro - so volta clicando Iniciar' if g == 'off'
                 else '[auto] Filtro LIGADO: carregando o historico dos ativos para medir os setups antes de operar')
    if not uni:
        _throttled_log(st, '_uni_empty', ('[manual] Nenhum ativo escolhido - escolha um ativo na lista e inicie de novo.' if mode == 'MANUAL' else f'[auto] Nenhum ativo aberto agora no modo {mode}. Vou checar de novo em instantes.'), every=120)
        if time.time() - st.get('_uni_force', 0) > 120:
            st['_uni_force'] = time.time()
            _refresh_assets(username, force=True)
        return 5.0

    t = time.time()
    sec = t % 60
    candle_ts = int(t - sec)
    if sec <= ENTRY_MAX_SECONDS and st.get('_auto_key') != candle_ts:
        _auto_window(api, st, username, entry_value, mode, uni, candle_ts)
        return 0.2
    if sec <= ENTRY_MAX_SECONDS + 1.0:
        return 0.3
    _hb = st.get('_hb')
    if _hb and sec >= 28:
        _throttled_log(st, '_hb30', f'[vivo] procurando entrada em {_hb["asset"]} - ultima leitura ha {int(time.time() - _hb["ts"])}s ({_hb["sg"]}) - proxima vela em {int(60 - sec)}s', every=25)
    _auto_background(api, st, username, mode, uni)
    return 0.3 if sec < 50 else nxt()

def _open_mode_step(api, st, username, asset, entry_value, timeframe):
    """Um passo do modo 'open'. Retorna quantos segundos o loop deve dormir."""
    clen = 300 if timeframe == 'M5' else 60
    exp = 5 if timeframe == 'M5' else 1
    t = time.time()
    sec = t % clen
    candle_ts = int(t - sec)
    if st.get('_open_key') == candle_ts:
        # esta vela ja foi tratada: dorme ate ~0.35s antes do nascimento da proxima
        return max(0.1, min(2.0, clen - sec - 0.35))
    if sec > ENTRY_MAX_SECONDS:
        st['_open_key'] = candle_ts      # perdeu a janela desta vela (ex.: bot ligado no meio dela)
        return max(0.1, min(2.0, clen - sec - 0.35))

    candles = _fetch_candles(api, st, username, asset, timeout=8)
    if candles is None:
        return 1.0
    m1_ts = int(t - (t % 60))
    last_from = candles[-1].get('from')
    if last_from is None:
        closed = candles[:-1] if sec < 59 else candles
    elif last_from >= m1_ts:
        closed = candles[:-1]            # ultima = vela que acabou de nascer (em formacao): descarta
    elif last_from == m1_ts - 60 and sec >= 1.0:
        closed = candles                 # corretora ainda nao listou a vela nova; a ultima ja esta fechada
    else:
        return 0.2                       # dados ainda velhos: tenta de novo daqui a pouco
    if len(closed) < 20:
        return 0.5

    import numpy as np
    closes = np.array([c['close'] for c in closed], dtype=float)
    volumes = np.array([c.get('volume', 500) for c in closed], dtype=float)
    try:
        sinal, debug = _compute_signal(closes, volumes, asset)
    except Exception as e:
        print(f"[TRADING] Erro ao calcular sinal {asset}: {e}")
        st['_open_key'] = candle_ts
        _add_log(st, f'Erro ao calcular sinal em {asset}: {str(e)[:100]}')
        return 1.0

    st['_open_key'] = candle_ts          # uma avaliacao por vela, sem repintar
    if not sinal:
        if time.time() - st.get('_last_nosig_log', 0) >= 30:
            st['_last_nosig_log'] = time.time()
            try:
                _add_log(st, f'Sem sinal HYBRID em {asset} (velas fechadas) - RSI {debug.get("rsi",0):.1f} '
                             f'MACD {debug.get("macd_b1",0):.5f}/{debug.get("macd_b2",0):.5f} '
                             f'ALMA {debug.get("alma",0):.5f} vs EMA3 {debug.get("ema3",0):.5f} [{debug.get("modo","?")}]')
            except Exception:
                pass
        return max(0.1, min(2.0, clen - (time.time() % clen) - 0.35))

    st['last_signal'] = sinal
    late = time.time() - candle_ts
    if late > ENTRY_MAX_SECONDS:
        _add_log(st, f'Sinal {sinal} em {asset} ficou pronto so a {late:.0f}s do nascimento da vela - entrada pulada (limite {ENTRY_MAX_SECONDS:.0f}s)')
        return 1.0
    label = datetime.datetime.fromtimestamp(candle_ts, BRT).strftime('%H:%M')
    _execute_entry(api, st, username, asset, sinal, entry_value, exp, candle_ts, label)
    return 1.0

def _trading_loop(username, gen=None):
    """Loop de trading - busca candles REAIS M1, bloqueia sinteticos"""
    print(f"[TRADING] Loop iniciado {username} - candles REAIS M1 apenas")
    
    while True:
        try:
            st = get_user_state(username)
            if not st.get('running', False) or (gen is not None and st.get('_loop_gen') != gen):
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

            if st.get('_reconnecting'):
                time.sleep(1.5)              # reconectando em segundo plano: nao le nem opera ate terminar
                continue
            api = _IQ_APIS.get(username)
            if not api:
                if time.time() - st.get('_last_noapi_log', 0) > 30:
                    st['_last_noapi_log'] = time.time()
                    _add_log(st, 'Sem conexao ativa com a corretora - tentando reconectar...')
                _reconnect_now(st, username, 'Sem conexao ativa')
                time.sleep(3)
                continue

            if ENTRY_MODE == 'open':
                try:
                    _sl = _STEP_LOCKS.setdefault(username, threading.Lock())
                    if not _sl.acquire(blocking=False):
                        time.sleep(1.0)          # outro ciclo esta analisando: este nao faz nada (evita leitura/entrada duplicada)
                        continue
                    try:
                        if gen is not None and st.get('_loop_gen') != gen:
                            break
                        if STRATEGY == 'v2' and HAS_V2:
                            pause = _auto_step(api, st, username, entry_value)
                        else:
                            pause = _open_mode_step(api, st, username, asset, entry_value, timeframe)
                    finally:
                        _sl.release()
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    print(f"[TRADING] Erro modo open: {e}")
                    pause = 2.0
                time.sleep(pause)
                continue

            # ---- MODO 'live' (antigo): recalcula a cada 2s na vela em formacao, janela 0-30s ----
            now = datetime.datetime.now(BRT)
            if timeframe == 'M5':
                candle_min = (now.minute // 5) * 5
                candle_start = now.replace(minute=candle_min, second=0, microsecond=0)
            else:
                candle_start = now.replace(second=0, microsecond=0)
            
            seconds_since_candle = (now - candle_start).total_seconds()
            current_candle_key = f"{now.date()}-{candle_start.hour:02d}-{candle_start.minute:02d}"

            try:
                # BUSCA DE CANDLES REAIS M1 - se falhar, NAO opera (sem velas sinteticas)
                candles = _fetch_candles(api, st, username, asset)
                if candles is None:
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

                        exp = 5 if timeframe == 'M5' else 1
                        outcome = _execute_entry(api, st, username, asset, sinal, entry_value, exp,
                                                 candle_start.timestamp(), now.strftime("%H:%M"))
                        st['_last_candle_key'] = current_candle_key   # uma tentativa por vela
                        if outcome == 'ok':
                            st['_last_trade_ts'] = time.time()
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
        # saldo preferido: o ATUAL (atualizado apos cada ordem); o do login e so fallback
        bal = st.get('broker_balance') or result.get('balance') or 0
        acct = result.get('account_type') or st.get('broker_account_type', 'PRACTICE')
        connected = conn_st.get('status') == 'connected' or st.get('broker_connected', False)
        return jsonify(connected=connected, balance=bal, account_type=acct, broker='IQ Option'), 200
    except Exception as e:
        return jsonify(connected=False, balance=0, error=str(e)), 200

def _hard_reset(username):
    """Zera TUDO do usuario sem redeploy: para o bot, cancela catalogador e sentimento, desconecta da
    corretora e limpa quarentenas, contadores de falha e reconexao presa. E o botao 'Zerar bot' do painel."""
    st = get_user_state(username)
    cs = _CATALOG.get(username)
    ss = _SENT.get(username)
    st['running'] = False
    st['bot_running'] = False
    st['_loop_gen'] = int(st.get('_loop_gen', 0)) + 1
    if cs and cs.get('running'):
        cs['cancel'] = True
    if ss and ss.get('running'):
        ss['cancel'] = True
    for _ in range(40):                     # ate 10s: da tempo das leituras em andamento terminarem
        if not ((cs and cs.get('running')) or (ss and ss.get('running'))):
            break
        time.sleep(0.25)
    if cs:
        cs['running'] = False
    if ss:
        ss['running'] = False
    for k in ('_asset_bad', '_global_fail', '_hv_fail', '_rc_fail', '_reconnecting',
              '_asset_fail', '_recent_reads', '_boot', '_booted', '_boot_retry', '_dir_pause_until',
              '_last_good', '_last_good_ts', '_open_key', '_last_candle_key', '_loss_streak_all',
              '_refuse_n', '_focus', '_auto_key'):
        st.pop(k, None)
    _SUSPECT.clear()
    api = _IQ_APIS.get(username)
    if api is not None:
        try:
            _safe_close(api)
        except Exception:
            pass
        del _IQ_APIS[username]
    with get_user_conn_lock(username):
        conn_st = get_user_conn_state(username)
        conn_st['status'] = 'idle'
        conn_st['result'] = None
        conn_st['error'] = None
    st['broker_connected'] = False

@app.route('/api/broker/disconnect', methods=['POST'])
@app.route('/api/broker/ui/disconnect', methods=['POST'])
def broker_disconnect():
    u = current_user()
    username = u.get('sub', 'admin')
    _hard_reset(username)
    _add_log(get_user_state(username), 'Desconectado da corretora - pode conectar de novo quando quiser.')
    return jsonify(ok=True), 200

@app.route('/api/bot/reset', methods=['POST'])
def bot_reset():
    """Botao 'Zerar bot': para TUDO, desconecta e limpa travas - o 'redeploy' agora fica no painel."""
    u = current_user()
    username = u.get('sub', 'admin')
    _hard_reset(username)
    _add_log(get_user_state(username), 'BOT ZERADO: bot parado, catalogador e sentimento cancelados, '
              'corretora desconectada, quarentenas e travas limpas. Conecte de novo para operar.')
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
            _add_log(st, f'Ativo alterado: {old} -> {asset} ({"OTC - regra sem volume" if _is_otc(asset) else "mercado aberto"})')
            try:
                oi = ((get_user_conn_state(username).get('result') or {}).get('open_info')) or {}
                if oi.get('turbo_open') is not None and asset not in oi['turbo_open']:
                    where = 'so em BINARY' if asset in (oi.get('binary_open') or []) else 'nem em binary'
                    _add_log(st, f'ATENCAO: {asset} NAO esta aberto em TURBO ({where}). Ordens de 1 a 5 min usam TURBO e serao recusadas. Escolha outro ativo.')
            except Exception:
                pass
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
        
        cs_run = _CATALOG.get(username)
        if cs_run and cs_run.get('running'):
            cs_run['cancel'] = True                      # iniciar o bot cancela o catalogador
            for _ in range(60):
                if not cs_run.get('running'):
                    break
                time.sleep(0.25)
            if cs_run.get('running'):
                return jsonify(ok=False, error='Catalogador ainda encerrando - tente de novo em alguns segundos.'), 200
        m_asset = str(data.get('asset') or st.get('manual_asset') or st.get('selected_asset') or '').strip()
        if not m_asset or 'Carregando' in m_asset:
            return jsonify(ok=False, error='Escolha um ativo na lista.'), 200
        st['manual_asset'] = m_asset
        st['selected_asset'] = m_asset
        st['asset_mode'] = 'MANUAL'
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
        st['_loss_streak_all'] = 0          # rodada nova comeca sem perdas acumuladas
        st['_refuse_n'] = 0
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

        # cada start gera uma "geracao": loops antigos (mesmo presos numa leitura) saem sozinhos ao ver outra geracao.
        # Antes, dois loops rodavam juntos e brigavam pela mesma conexao (a lib da IQ nao aceita chamadas simultaneas).
        with _START_LOCK:
            st['_loop_gen'] = int(st.get('_loop_gen', 0)) + 1
            gen = st['_loop_gen']
            old_thread = _BOT_THREADS.get(username)
            if old_thread and old_thread.is_alive():
                old_thread.join(timeout=15)
                if old_thread.is_alive():
                    _add_log(st, 'Aviso: o ciclo anterior ainda esta terminando uma leitura; ele sai sozinho ao concluir.')
            t = threading.Thread(target=_trading_loop, args=(username, gen), daemon=True)
            t.start()
            _BOT_THREADS[username] = t
        
        if STRATEGY == 'v2' and HAS_V2:
            st['_focus'] = None
            st['_auto_key'] = 0
            if _asset_mode(st) == 'MANUAL':
                msg = f'Bot iniciado - modo MANUAL no ativo {st.get("manual_asset")} - Entrada $ {entry_value} - expiracao 1 min'
            else:
                msg = (f'Bot iniciado - modo {"OTC" if _asset_mode(st) == "OTC" else "Mercado aberto"} com selecao automatica de ativos - '
                       f'Entrada $ {entry_value} - expiracao 1 min')
        else:
            msg = f'Bot iniciado {asset} - Entrada $ {entry_value} - {timeframe} exp {expiration}min'
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
    st['_loop_gen'] = int(st.get('_loop_gen', 0)) + 1
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
        mode=_asset_mode(st),
        manual_asset=st.get('manual_asset'),
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

@app.route('/api/bot/mode', methods=['POST'])
def bot_mode():
    """Troca o modo (OTC / mercado aberto) na hora; o bot passa a varrer a outra lista de ativos."""
    u = current_user()
    st = get_user_state(u.get('sub', 'admin'))
    data = request.get_json(silent=True) or {}
    m = 'MANUAL'            # modo automatico removido
    if m == 'MANUAL':
        asset = str(data.get('asset') or st.get('manual_asset') or st.get('selected_asset') or '').strip()
        if not asset:
            return jsonify(ok=False, error='Escolha um ativo na lista.'), 200
        if asset != st.get('manual_asset'):
            st['manual_asset'] = asset
            st['selected_asset'] = asset
            st['_focus'] = None
            if st.get('asset_mode') == 'MANUAL':
                _add_log(st, f'[manual] Ativo alterado para {asset}')
    if st.get('asset_mode') != m:
        st['asset_mode'] = m
        st['_focus'] = None
        _add_log(st, f'[auto] Modo alterado para {"OTC" if m == "OTC" else ("Mercado aberto" if m == "OPEN" else "Manual (" + st.get("manual_asset", "") + ")")}')
    return jsonify(ok=True, mode=m), 200

@app.route('/api/bot/quarantine', methods=['GET'])
def bot_quarantine():
    """Ativos fora da lista agora (3 losses seguidos, ordem recusada, sem velas...) e quanto falta para voltarem."""
    u = current_user()
    st = get_user_state(u.get('sub', 'admin'))
    now = time.time()
    rows = [{'asset': k, 'left_min': round((v[0] - now) / 60, 1), 'reason': v[1]}
            for k, v in (st.get('_asset_bad') or {}).items() if v[0] > now]
    rows.sort(key=lambda r: r['left_min'])
    streak = {k: v for k, v in (st.get('_asset_streak') or {}).items() if v > 0}
    return jsonify(ok=True, rows=rows, streak=streak, max_streak=LOSS_STREAK_MAX, minutes=QUARANTINE_MIN, mode=_asset_mode(st),
                   focus=st.get('_focus') or st.get('selected_asset')), 200

@app.route('/api/bot/setups', methods=['GET'])
def bot_setups():
    """Estatisticas das operacoes virtuais por ativo/setup/expiracao (alimenta a tabela do painel)."""
    u = current_user()
    st = get_user_state(u.get('sub', 'admin'))
    if not (HAS_V2 and _ENGINE):
        return jsonify(ok=False, rows=[], error='strategy_v2 indisponivel'), 200
    asset = request.args.get('asset') or ('all' if (STRATEGY == 'v2' and HAS_V2) else st.get('selected_asset'))
    rows = _ENGINE.summary(None if asset == 'all' else asset, DIGITAL_EXPS if OPEN_PRODUCT == 'digital' else None)
    if asset == 'all':
        mode = _asset_mode(st)
        rows = [r for r in rows if _is_otc(r['asset']) == (mode == 'OTC')]
    return jsonify(ok=True, strategy=STRATEGY, gate=SV2.GATE, min_n=SV2.MIN_N, min_lb=round(SV2.MIN_LB * 100),
                   asset=asset, focus=st.get('_focus') or st.get('selected_asset'), mode=_asset_mode(st), rows=rows[:25]), 200

@app.route('/api/bot/log', methods=['GET'])
def bot_log():
    """Devolve as linhas de log mais novas que ?since=<seq> (o painel chama a cada poucos segundos)."""
    u = current_user()
    st = get_user_state(u.get('sub', 'admin'))
    try:
        since = int(request.args.get('since', 0))
    except Exception:
        since = 0
    rows = [r for r in st['log'] if r.get('seq', 0) > since]
    rows.reverse()  # mais antigas primeiro
    return jsonify(rows=rows[-200:], last=st.get('_log_seq', 0)), 200

@app.route('/api/bot/reset_stats', methods=['POST'])
def bot_reset_stats():
    u = current_user()
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    old_w, old_l = st.get('wins', 0), st.get('losses', 0)
    st['wins'] = 0
    st['losses'] = 0
    st['profit'] = 0.0
    st['win_rate'] = 0
    _add_log(st, f'Placar zerado (eram {old_w} wins / {old_l} losses)')
    return jsonify(ok=True, wins=0, losses=0, win_rate=0, profit=0), 200

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
    try:
        _refresh_assets(username)
    except Exception as e:
        print(f"[IQ] Erro ao atualizar ativos: {e}")
    conn_st = get_user_conn_state(username)
    result = conn_st.get('result') or {}
    binary = result.get('binary_assets') or ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'EURJPY', 'GBPJPY', 'EURGBP', 'USDCHF', 'NZDUSD']
    otc = result.get('otc_assets') or ['EURUSD-OTC', 'GBPUSD-OTC', 'USDJPY-OTC', 'AUDUSD-OTC', 'EURJPY-OTC', 'GBPJPY-OTC']
    all_assets = binary + otc
    return jsonify(assets=all_assets, binary=binary, otc=otc, all=all_assets, selected=st.get('selected_asset', 'EURUSD'),
                   mode=_asset_mode(st), manual_asset=st.get('manual_asset'), n_open=len(binary), n_otc=len(otc)), 200

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
    
    token = jwt.encode({'username': username, 'sub': username, 'role': user.role},
                       app.config['SECRET_KEY'], algorithm='HS256')
    return jsonify(ok=True, token=token, username=username, role=user.role), 200

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json(silent=True) or {}
    username = (data.get('username', '') or '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'error': 'Informe usuario e senha'}), 401
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Usuario ja existe'}), 401
    ph = hashlib.sha256(password.encode()).hexdigest()
    db.session.add(User(username=username, password_hash=ph, role='user'))
    db.session.commit()
    return jsonify(ok=True, message='Usuario criado'), 200

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.pop('token', None)
    return jsonify(ok=True), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
