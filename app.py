
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
from flask import Flask, render_template, request, jsonify, session, g, send_file
from flask_sqlalchemy import SQLAlchemy
import hashlib
import datetime
import os
import jwt
import re
import secrets
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

import iqlib as IQLIB          # (25/11) uma copia da biblioteca da corretora por usuario

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
def _load_secret_key():
    """(15/11) a chave antiga era fixa no codigo (repositorio publico = qualquer um forjava login).
    Ordem: variavel SECRET_KEY do Railway; senao uma chave aleatoria salva no volume /data (sobrevive a deploy)."""
    k = (os.environ.get('SECRET_KEY') or '').strip()
    if k and k != 'danbot-secret-2025-muda-essa-chave':
        return k
    p = '/data/.secret_key' if os.path.isdir('/data') else '.secret_key'
    try:
        with open(p, 'r') as f:
            k = f.read().strip()
        if len(k) >= 32:
            return k
    except Exception:
        pass
    k = secrets.token_hex(32)
    try:
        with open(p, 'w') as f:
            f.write(k)
    except Exception as e:
        print(f'[AUTH] nao consegui salvar a chave em {p}: {e} (logins caem a cada deploy)')
    return k

app.config['SECRET_KEY'] = _load_secret_key()
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////data/danbot.db' if os.path.exists('/data') else 'sqlite:///danbot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Healthcheck Railway
@app.route('/health')
@app.route('/healthcheck')
def health():
    return jsonify({'status': 'ok', 'has_iq': HAS_IQ_MODULE, 'version': 'v3.0 FINAL'}), 200

# (15/11) LOGIN CORRIGIDO. O token fica no navegador (localStorage) e so vai nos pedidos /api/ (cabecalho
# Authorization) - abrir uma pagina NAO manda o token. Antes, pedido sem token virava "admin" automaticamente,
# entao a raiz sempre abria o painel Master sem login (e qualquer um com o link controlava o bot).
# Agora: "/" e sempre a tela de login (ela mesma pula para o painel se o token salvo for valido);
# os paineis checam o token pelo /api/me e voltam para o login se ele faltar ou for invalido;
# e TODA rota /api/ (menos o login) exige token valido de um usuario ativo (_auth_guard).

# ======================= APP (PWA) + NOTIFICACOES (19/11) =======================
# O painel vira um app instalavel (manifest + service worker) e manda notificacao de WIN / LOSS / EMPATE para
# os celulares que ativaram. Tudo aqui e LATERAL: o envio roda numa thread propria depois que o resultado ja foi
# contabilizado; se a lib de push faltar ou der erro, o bot segue igual (so nao notifica).
try:
    from pywebpush import webpush, WebPushException
    _PUSH_LIB = True
except Exception as _e:
    print(f'[push] pywebpush indisponivel ({_e}) - notificacoes desligadas')
    _PUSH_LIB = False
_DATA_DIR = '/data' if os.path.isdir('/data') else '.'
_VAPID_FILE = os.path.join(_DATA_DIR, '.vapid_private.pem')
_PUSH_FILE = os.path.join(_DATA_DIR, 'push_subs.json')
_PUSH_LOCK = threading.Lock()
_VAPID = {}

def _vapid_keys():
    """Par de chaves VAPID (gerado uma vez e guardado no volume; sem ele as inscricoes antigas param de valer)."""
    if _VAPID:
        return _VAPID
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    import base64
    try:
        with open(_VAPID_FILE, 'rb') as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
    except Exception:
        key = ec.generate_private_key(ec.SECP256R1())
        try:
            with open(_VAPID_FILE, 'wb') as f:
                f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        except Exception as e:
            print(f'[push] nao salvou a chave VAPID: {e}')
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    _VAPID['pem'] = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode()
    _VAPID['pub'] = base64.urlsafe_b64encode(pub).decode().rstrip('=')
    _VAPID['key'] = key
    return _VAPID

def _push_load():
    try:
        with open(_PUSH_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def _push_save(d):
    tmp = _PUSH_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f)
    os.replace(tmp, _PUSH_FILE)

def _push_send(username, title, body, tag='danbot', kind='info'):
    """Manda a notificacao para todos os aparelhos inscritos do usuario (em thread; nunca levanta erro)."""
    if not _PUSH_LIB:
        return
    def _run():
        try:
            with _PUSH_LOCK:
                subs = list((_push_load().get(username) or []))
            if not subs:
                return
            v = _vapid_keys()
            from py_vapid import Vapid02                  # cabecalho 'vapid t=..,k=..' (exigido pela Apple; aceito pelo Google)
            vap = Vapid02(private_key=v['key'])
            data = json.dumps({'title': title, 'body': body, 'tag': tag, 'kind': kind, 'url': '/'})
            dead = []
            for sub in subs:
                try:
                    webpush(subscription_info=sub, data=data, vapid_private_key=vap, ttl=300,
                            vapid_claims={'sub': os.environ.get('VAPID_SUB') or 'mailto:danbot@danbot.app'}, timeout=10)
                except WebPushException as e:
                    code = getattr(getattr(e, 'response', None), 'status_code', None)
                    if code in (404, 410):
                        dead.append(sub.get('endpoint'))       # aparelho desinstalou/desativou: remove
                    else:
                        print(f'[push] falhou ({code}): {str(e)[:120]}')
                except Exception as e:
                    print(f'[push] erro: {str(e)[:120]}')
            if dead:
                with _PUSH_LOCK:
                    d = _push_load()
                    d[username] = [x for x in (d.get(username) or []) if x.get('endpoint') not in dead]
                    _push_save(d)
        except Exception as e:
            print(f'[push] erro geral: {e}')
    threading.Thread(target=_run, daemon=True).start()

def _push_result(username, st, asset, profit, direction='', setup=''):
    """Notificacao de resultado: WIN / LOSS / EMPATE com placar da sessao."""
    try:
        w, l = int(st.get('wins', 0)), int(st.get('losses', 0))
        lucro = float(st.get('profit', 0) or 0)
        info = ' '.join(x for x in (asset, str(direction or '').upper(), f'({setup})' if setup else '') if x)
        placar = f'Placar {w}W/{l}L - lucro $ {lucro:+.2f}'
        if profit > 0:
            _push_send(username, f'WIN +$ {profit:.2f}', f'{info}\n{placar}', tag='res', kind='win')
        elif profit < 0:
            _push_send(username, f'LOSS -$ {abs(profit):.2f}', f'{info}\n{placar}', tag='res', kind='loss')
        else:
            _push_send(username, 'EMPATE $ 0.00', f'{info}\n{placar}', tag='res', kind='draw')
    except Exception as e:
        print(f'[push] resultado: {e}')

@app.route('/manifest.webmanifest')
def pwa_manifest():
    m = {'name': 'DanBot Pro', 'short_name': 'DanBot', 'description': 'DanBot Pro - auto trader M1',
         'start_url': '/', 'scope': '/', 'display': 'standalone', 'orientation': 'portrait',
         'background_color': '#04080a', 'theme_color': '#050a08', 'lang': 'pt-BR', 'id': '/',
         'icons': [{'src': '/static/icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
                   {'src': '/static/icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
                   {'src': '/static/maskable-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'}]}
    r = app.response_class(json.dumps(m), mimetype='application/manifest+json')
    r.headers['Cache-Control'] = 'no-cache'
    return r

@app.route('/sw.js')
def pwa_sw():
    r = send_file(os.path.join(app.root_path, 'static', 'sw.js'), mimetype='application/javascript')
    r.headers['Cache-Control'] = 'no-cache'
    r.headers['Service-Worker-Allowed'] = '/'
    return r

@app.route('/api/push/key', methods=['GET'])
def push_key():
    if not _PUSH_LIB:
        return jsonify(ok=False, error='Notificacoes indisponiveis no servidor (falta a biblioteca pywebpush).'), 200
    return jsonify(ok=True, key=_vapid_keys()['pub']), 200

@app.route('/api/push/subscribe', methods=['POST'])
def push_subscribe():
    u = current_user(); username = u.get('sub', 'admin')
    sub = (request.get_json(silent=True) or {}).get('subscription') or {}
    if not isinstance(sub, dict) or not sub.get('endpoint') or not (sub.get('keys') or {}).get('p256dh'):
        return jsonify(ok=False, error='Inscricao invalida'), 200
    sub = {'endpoint': sub['endpoint'], 'keys': {'p256dh': sub['keys']['p256dh'], 'auth': sub['keys'].get('auth', '')}}
    with _PUSH_LOCK:
        d = _push_load()
        for k in list(d):                                   # o mesmo aparelho so fica com um usuario
            d[k] = [x for x in d[k] if x.get('endpoint') != sub['endpoint']]
        d.setdefault(username, []).append(sub)
        d[username] = d[username][-10:]
        _push_save(d)
    return jsonify(ok=True), 200

@app.route('/api/push/unsubscribe', methods=['POST'])
def push_unsubscribe():
    u = current_user(); username = u.get('sub', 'admin')
    ep = (request.get_json(silent=True) or {}).get('endpoint') or ''
    with _PUSH_LOCK:
        d = _push_load()
        d[username] = [x for x in (d.get(username) or []) if x.get('endpoint') != ep]
        _push_save(d)
    return jsonify(ok=True), 200

@app.route('/api/push/test', methods=['POST'])
def push_test():
    u = current_user(); username = u.get('sub', 'admin')
    n = len(_push_load().get(username) or [])
    if not n:
        return jsonify(ok=False, error='Nenhum aparelho com notificacao ativada.'), 200
    _push_send(username, 'DanBot', 'Notificacoes funcionando! Voce vai receber WIN, LOSS e EMPATE aqui.', tag='test', kind='info')
    return jsonify(ok=True, devices=n), 200

@app.route('/')
@app.route('/login')
def root():
    return render_template('login.html')

@app.route('/dashboard')
def dashboard_page():
    return render_template('dashboard.html')

@app.route('/master')
def master_page():
    return render_template('master.html')

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
    db.create_all()            # (15/11) cria as tabelas ANTES de semear (antes vinha depois e o seed falhava em banco novo)
    # (12/11) semeia o usuario MASTER com senha VAZIA: ele define a senha no primeiro acesso
    try:
        if not User.query.filter_by(username='daniloadaltosantana').first():
            db.session.add(User(username='daniloadaltosantana', password_hash='', role='admin'))
            db.session.commit()
    except Exception as _e:
        print('[AUTH] seed master:', _e)

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
_ASSET_ID_NEG_TTL = 300          # "nao achou id" tambem e cacheado, mas expira (5 min) pra tentar de novo
_SESSION_ID_MAP = {}             # (12/11) nome -> id oficial da corretora (canal v2), por conexao

def _asset_variants(asset):
    """Variantes de nome possiveis para o mesmo ativo (USDBRL-OTC / USDBRL / USD-BRL-OTC...)."""
    a = (asset or '').strip()
    out = [a]
    base = a[:-4] if a.endswith('-OTC') else a
    out += [base, base + '-OTC', base.replace('/', ''), base.replace('/', '') + '-OTC',
            base.replace('/', '-'), base.replace('-', '/')]
    return list(dict.fromkeys(x for x in out if x))

def _find_actives_maps(obj, out):
    """Varre a resposta da corretora procurando TODOS os dicionarios 'actives' (a estrutura real
    varia entre produtos; em vez de chutar o caminho, acha qualquer um que existir)."""
    if isinstance(obj, dict):
        acts = obj.get('actives')
        if isinstance(acts, dict):
            out.append(acts)
        for v in obj.values():
            _find_actives_maps(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _find_actives_maps(v, out)

def _asset_id_from_server(api, cands, timeout=4.0):
    """Pergunta a CORRETORA o id do ativo, DIRETO no websocket (get_api_option_init_all +
    get-instruments) com timeout proprio e curto. NAO usa api.get_all_init()/get_instruments()
    da biblioteca: eles ficam em loop infinito reconectando/logando dentro da leitura de velas
    (era isso que travava cada leitura por 30-60s). Retorna o id ou None."""
    inner = getattr(api, 'api', None)
    if inner is None:
        return None
    seen = []
    # 1) init das opcoes (binarias/turbo/digital - os OTC de opcao vivem aqui)
    try:
        inner.api_option_init_all_result = None
        inner.get_api_option_init_all()
        t0 = time.time()
        while time.time() - t0 < timeout and not inner.api_option_init_all_result:
            time.sleep(0.1)
        if inner.api_option_init_all_result:
            _find_actives_maps(inner.api_option_init_all_result, seen)
    except Exception:
        pass
    # 2) listas de instrumentos da corretora (a lista vem no inner.instruments, setada pelo websocket)
    for tp in ('forex', 'crypto', 'cfd', 'commodities', 'stocks'):
        try:
            inner.instruments = None
            inner.get_instruments(tp)
            t0 = time.time()
            while time.time() - t0 < timeout and not inner.instruments:
                time.sleep(0.1)
            data = inner.instruments
            if isinstance(data, list):
                for ins in data:
                    nm = str((ins or {}).get('name', '')).upper()
                    if nm in cands:
                        aid = ins.get('active_id') or ins.get('id')
                        if aid is not None:
                            return aid
            elif data:
                _find_actives_maps(data, seen)
        except Exception:
            continue
    for acts in seen:
        for cand in cands:
            v = acts.get(cand)
            if v is not None:
                aid = v.get('id') if isinstance(v, dict) else v
                if aid is not None:
                    return aid
    return None

def _asset_id(api, asset):
    """Resolve o ID numerico do ativo pra pedir velas. A biblioteca de 2021 so conhece os 9 OTC
    classicos no ACTIVES - os ativos novos (exoticas, cripto OTC, commodities) NAO estao la, e
    foi por isso que a leitura falhou com a lista nova. Ordem: 1) nome EXATO do ativo no ACTIVES;
    2) a CORRETORA (get_all_init + get_instruments) - o id verdadeiro do OTC; 3) variantes de nome
    no ACTIVES (ultimo recurso: id do par regular, sem OTC). Cacheia por conexao."""
    key = (id(api), asset)
    with _ASSET_IDS_LOCK:
        hit = _ASSET_IDS.get(key)
        if hit is not None:
            val, ts = hit
            if val is not None or time.time() - ts < _ASSET_ID_NEG_TTL:
                return val           # id achado cacheia pra sempre; "nao achou" cacheia com TTL
    aid = None
    cands = [c.upper() for c in _asset_variants(asset)]
    exact = [c for c in cands if c.endswith('-OTC')]
    is_otc = asset.upper().endswith('-OTC')
    try:
        _OPC = IQLIB.mod(api, 'constants')
        # OTC: so nomes -OTC no ACTIVES (o id do par regular diverge das velas OTC - leria outro grafico)
        for cand in (exact if is_otc else cands):
            aid = _OPC.ACTIVES.get(cand)
            if aid is not None:
                break
    except Exception:
        pass
    if aid is None:
        # (12/11) id oficial da SESSAO (canal v2): resolve os exoticos que a lib de 2021 nao conhece
        with _ASSET_IDS_LOCK:
            m = _SESSION_ID_MAP.get(id(api))
        if isinstance(m, dict):
            for cand in cands:
                v = m.get(cand)
                if v is not None:
                    aid = v
                    break
    if aid is None:
        aid = _asset_id_from_server(api, cands)
    if aid is None:
        inner = getattr(api, 'api', None)
        m = getattr(inner, 'instrument_id_mapping', None) if inner is not None else None
        if isinstance(m, dict):
            up = {str(k).upper(): v for k, v in m.items()}
            for cand in cands:
                if cand in up:
                    aid = up[cand]
                    break
    # ultimo recurso: variante sem OTC no ACTIVES (par regular) - pode divergir do OTC,
    # entao so entra se a corretora nao respondeu
    if aid is None and not is_otc:
        try:
            _OPC = IQLIB.mod(api, 'constants')
            for cand in cands:
                aid = _OPC.ACTIVES.get(cand)
                if aid is not None:
                    break
        except Exception:
            pass
    with _ASSET_IDS_LOCK:
        _ASSET_IDS[key] = (aid, time.time())     # sucesso e falha entram no cache (falha com TTL)
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


def _session_lists(api, timeout=8.0):
    """(12/11) Lista de ativos operaveis nesta sessao via canal v2 (get-initialization-data),
    SEPARADA em OTC e mercado aberto (nao-OTC) - a corretora diz ANTES da ordem o que esta aberto
    (enabled + !is_suspended). Retorna (otc, mercado_aberto, ids) ou (None, None, {})."""
    inner = getattr(api, 'api', None)
    if inner is None:
        return None, None, {}
    try:
        inner.api_option_init_all_result_v2 = None
        if not hasattr(inner, 'get_api_option_init_all_v2'):
            return None, None, {}
        inner.get_api_option_init_all_v2()
        t0 = time.time()
        while time.time() - t0 < timeout and not inner.api_option_init_all_result_v2:
            time.sleep(0.2)
        data = inner.api_option_init_all_result_v2
        if not data:
            return None, None, {}
        otc, open_mkt, ids = [], [], {}
        for sec in ('binary', 'turbo'):
            acts = (data.get(sec) or {}).get('actives') or {}
            for _aid, v in acts.items():
                if not isinstance(v, dict):
                    continue
                nm_raw = str(v.get('name', '')).strip()
                parts = nm_raw.split('.')
                nm = (parts[1] if len(parts) > 1 else parts[0]).strip().upper()   # formato: 'prefixo.ATIVO'
                if not nm or v.get('id') is None:
                    continue
                if not (v.get('enabled') and not v.get('is_suspended')):
                    continue
                ids[nm] = v.get('id')
                if 'OTC' in nm:
                    otc.append(nm)
                else:
                    open_mkt.append(nm)
        with _ASSET_IDS_LOCK:
            _SESSION_ID_MAP[id(api)] = ids
        return (sorted(set(otc)) or None, sorted(set(open_mkt)) or None, ids)
    except Exception:
        return None, None, {}

def _otc_session_list(api, timeout=8.0):
    """Compatibilidade: so a lista de OTC da sessao."""
    otc, _mkt, _ids = _session_lists(api, timeout)
    return otc

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

def _token_user():
    """Le o token do cabecalho Authorization e confere no banco (usuario existe e esta ativo).
    Retorna {'sub','username','role'} ou {} se nao houver login valido. O papel vem do BANCO, nao do token."""
    try:
        token = request.headers.get('Authorization', '').replace('Bearer ', '').replace('bearer ', '').strip()
        if not token:
            return {}
        data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        name = data.get('sub') or data.get('username')
        usr = User.query.filter_by(username=name).first() if name else None
        if not usr or not usr.is_active or not usr.password_hash:
            return {}
        return {'sub': usr.username, 'username': usr.username, 'role': usr.role or 'user'}
    except Exception:
        return {}

def current_user():
    """(15/11) Antes, pedido sem token virava 'admin' (acesso total sem login). Agora so existe usuario com token valido;
    as rotas /api/ ja sao barradas no _auth_guard, entao aqui o usuario sempre existe."""
    u = getattr(g, 'user', None)
    if u is None:
        u = _token_user()
        g.user = u
    return u

_AUTH_OPEN = ('/api/login',)

@app.before_request
def _auth_guard():
    path = request.path or ''
    if not path.startswith('/api/') or path in _AUTH_OPEN or request.method == 'OPTIONS':
        return None
    if not current_user().get('sub'):
        return jsonify(ok=False, error='Sessao expirada ou sem login - entre de novo'), 401
    return None

@app.route('/api/me', methods=['GET'])
def api_me():
    u = current_user()
    return jsonify(ok=True, username=u['sub'], role=u['role']), 200

_GEN, _GEN_LOCK = {}, threading.Lock()
def _gen_new(u):
    with _GEN_LOCK:
        _GEN[u] = _GEN.get(u, 0) + 1
        return _GEN[u]
def _gen_current(u, gen):
    with _GEN_LOCK:
        return _GEN.get(u, 0) == gen

# (25/11) Cada usuario tem a SUA copia da biblioteca (iqlib.py): sessao, banca e avisos de conexao separados.
# Dentro da copia do usuario, a sessao guardada so e reaproveitada na MESMA conta e MESMA corretora (trocou de e-mail
# ou de corretora = login do zero). O reconectar do proprio usuario continua igual.
_SSID_OWNER = {}

def connect_iq_real(email, password, account_type='PRACTICE', host='iqoption.com', username='admin', broker_name='IQ Option', gen=None, scan=True):
    """Conexao REAL usando iqoptionapi.stable_api - funcao real, nao inventada"""
    try:
        _lib = IQLIB.for_user(username)                # (25/11) copia da biblioteca SO deste usuario
        IQ_Option = IQLIB.stable_api(_lib).IQ_Option
    except Exception as e:
        return False, f'iqoptionapi nao instalada: {e}'

    try:
        # A conexao antiga so e trocada DEPOIS que a nova funcionar (antes ela era apagada primeiro e, se o login
        # travasse, o bot ficava sem nenhuma conexao)
        _old_api = _IQ_APIS.get(username)

        # (25/11) outras corretoras da mesma plataforma (Avalon, Bullex, Exnova, Polarium...): so muda o endereco.
        # IQ Option: exatamente o IQ_Option original, como sempre foi.
        try:
            import brokers as _BR
            _bn = _BR.normalize(broker_name)
        except Exception as _e:
            _BR, _bn = None, 'IQ Option'
            print(f'[CORRETORAS] brokers.py indisponivel ({_e}) - so IQ Option')
        api = _BR.make_api(_bn, email, password, _lib) if (_BR is not None and _bn != 'IQ Option') else IQ_Option(email, password)
        api._danbot_lib = _lib
        # (10/11) blindagem do websocket: o on_message da lib (2021) morre com mensagem de formato
        # novo da corretora e mata a thread -> socket fecha sozinho ~30s apos CADA reconexao, e o
        # mutex da lib fica preso (todo envio trava). Aplicado aqui tambem para conexoes ja criadas.
        try:
            from patch_iqoptionapi import harden_instance as _harden_ws, is_hardened as _ws_ok
            _gv = IQLIB.gv(_lib)
            _gv.check_websocket_if_error = False       # erro velho de sessao anterior nao bloqueia o login novo
        except ImportError:
            _st_log = get_user_state(username)
            _add_log(_st_log, '[conexao] AVISO GRAVE: patch_iqoptionapi.py nao encontrado - o socket vai cair em loop. Suba o arquivo junto com o app.py.')
        except Exception:
            pass
        try:
            _gv_s = IQLIB.gv(_lib)                     # variaveis da copia DESTE usuario
            _key = (str(email).strip().lower(), _bn)
            if _SSID_OWNER.get(username) != _key:
                _gv_s.SSID = None                      # sessao de OUTRA conta: nunca reaproveitar
                _gv_s.balance_id = None
        except Exception:
            _key = None
        # Conecta
        check, reason = api.connect()
        if check and _key is not None:
            _SSID_OWNER[username] = _key
        # (11/11) a verificacao da blindagem so faz sentido DEPOIS do connect() (o websocket nasce ali;
        # antes disso is_hardened sempre diz "falso" e dava alarme de arquivo velho sem ser)
        try:
            from patch_iqoptionapi import is_hardened as _ws_ok, harden_instance as _harden_ws
            _harden_ws(api)                            # (12/11) faltava aplicar NA INSTANCIA: sem isso o
            _st_log = get_user_state(username)         # cheque acusava 'DESATUALIZADO' em conexao nova
            if check and _ws_ok(api):
                _throttled_log(_st_log, '_ws_hard', '[conexao] Blindagem do websocket ATIVA (socket protegido contra mensagens novas da corretora)', every=600)
            elif check:
                _add_log(_st_log, '[conexao] AVISO GRAVE: blindagem indisponivel (patch_iqoptionapi.py antigo ou sem websocket) - o socket pode cair em loop.')
        except Exception:
            pass
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

        # (12/11) Lista da SESSAO via canal v2: OTC abertos + MERCADO ABERTO (nao-OTC) abertos agora,
        # ambos com id oficial. Mercado aberto so durante o expediente real (de noite so OTC rodam).
        sess_otc, sess_open, _ids = None, None, {}
        try:
            sess_otc, sess_open, _ids = _session_lists(api, timeout=8.0)
        except Exception as _e:
            print(f'[IQ] _session_lists falhou: {_e}')
        if sess_otc:
            otc_assets = list(sess_otc)
            print(f'[IQ REAL] OTC da sessao (is_suspended=false): {len(otc_assets)} ativos')
        else:
            print('[IQ] lista de OTC da sessao indisponivel - usando os classicos da biblioteca')
        if sess_open:
            binary_assets = list(sess_open)
            print(f'[IQ REAL] Mercado aberto (nao-OTC) agora: {len(binary_assets)} ativos')

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
    if not ok_c and isinstance(candles, str) and 'ativo sem id' in candles:
        _mark_bad(st, asset, 3600, 'ativo sem id na biblioteca')   # pula rapido: nao e rede, nao reconecta
        return None
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

def _chart_mark(username, asset, t_entry, direction, res=None, det=None, val=None, exp=None):
    """(21/11) seta da entrada no grafico do painel (so visual). res None = aguardando resultado."""
    if not CHART_ON:
        return
    try:
        st_ = get_user_state(username)
        lst = st_.setdefault('_chart_tr', {}).setdefault(asset, [])
        t_sig = int(t_entry // 60 * 60) - 60          # vela do sinal (a que fechou antes da entrada)
        for m in lst:
            if m['t'] == t_sig:
                if res is not None:
                    m['res'] = res
                return
        m = {'t': t_sig, 'dir': str(direction or '').upper(), 'res': res, 'val': val, 'exp': exp}
        if det:                                        # (23/11) motivo da entrada, para a voz do painel
            m['rsi'] = det.get('rsi'); m['niveis'] = list(det.get('niveis') or [])
            _vx = re.search(r'vela ([0-9.]+)x', str(det.get('tipo') or ''))
            m['vx'] = float(_vx.group(1)) if _vx else None
        lst.append(m)
        del lst[:-40]
    except Exception:
        pass

def _track_result(api, username, oid, kind, exp_min, val, asset, t_entry, sinal='', meta=None):
    try:
        _chart_mark(username, asset, t_entry, (meta or {}).get('dir') or sinal, det=(meta or {}).get('det'), val=val, exp=exp_min)
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
        _chart_mark(username, asset, t_entry, (meta or {}).get('dir') or sinal,
                    'WIN' if profit_trade > 0 else ('LOSS' if profit_trade < 0 else 'EMPATE'))
        if meta and _ENGINE:
            try:
                _ENGINE.record_real(asset, meta['setup'], meta['h'], profit_trade)
                _journal([_brt_str(), asset, meta['setup'], meta['dir'], meta['h'], val,
                          'WIN' if profit_trade > 0 else ('LOSS' if profit_trade < 0 else 'EMPATE'),
                          json.dumps(meta.get('det', {}))], username)
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
        _push_result(username, st2, asset, profit_trade, (meta or {}).get('dir') or sinal, (meta or {}).get('setup') or '')   # (19/11) notificacao
    except Exception as e:
        print(f"[WIN] Erro acompanhamento: {e}")

def _ensure_lib_asset_id(api, asset):
    """(12/11) O api.buy da lib faz OP_code.ACTIVES[asset] e da KeyError nos exoticos (dicionario
    estatico de 2021). Aqui injetamos o ID OFICIAL da corretora (canal v2, _SESSION_ID_MAP) no
    dicionario da lib antes da ordem - mesma correcao de dados da leitura de velas."""
    try:
        with _ASSET_IDS_LOCK:
            m = _SESSION_ID_MAP.get(id(api))
        aid = (m or {}).get(str(asset).upper())
        if aid is None:
            return False
        _OP = IQLIB.mod(api, 'constants')     # o 'OP_code' da lib (da copia deste usuario)
        if _OP.ACTIVES.get(asset) != aid:
            _OP.ACTIVES[str(asset).upper()] = aid
        return True
    except Exception:
        return False

def _execute_entry(api, st, username, asset, sinal, entry_value, exp, candle_start_ts, label, meta=None):
    """Envia a ordem. Retorna 'ok', 'recusada', 'timeout' ou 'erro'. Sempre deixa o motivo no log do painel."""
    direction = sinal.lower()  # call / put
    # (12/11) martingale: se o ativo perdeu a ultima, a entrada sobe de nivel (x fator, cap nos niveis)
    try:
        _mg_on = bool(st.get('mg_enabled'))
        _mg_lvl = int((st.get('_mg_level') or {}).get(asset, 0)) if _mg_on else 0
        if _mg_on and _mg_lvl > 0:
            _mg_factor = float(st.get('mg_factor') or 2.0)
            entry_value = round(entry_value * (_mg_factor ** _mg_lvl), 2)
            _add_log(st, f'[mg] {asset}: ENTRADA nivel {_mg_lvl} - valor ajustado para $ {entry_value}')
    except Exception:
        pass
    t_send = time.time()
    sec_vela = t_send - candle_start_ts
    print(f"[TRADING] ENTRADA REAL {sinal} {asset} $ {entry_value} exp {exp} - {sec_vela:.1f}s apos nascer a vela")
    try:
        kind = 'binary'
        # (12/11) exoticos: injeta o id oficial da corretora no dicionario da lib ANTES da ordem
        # (sem isso, o buy da lib levanta KeyError com o nome do ativo - 'Erro entrada REAL: X')
        if not _ensure_lib_asset_id(api, asset):
            _throttled_log(st, '_no_id_' + asset, f'[entrada] {asset}: id oficial ainda nao conhecido (a lista da sessao nao carregou) - nova tentativa na proxima vela', every=120)
            return 'recusada'
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
    import key_levels as KL
    HAS_V2 = True
except Exception as _e:
    SV2 = None
    HAS_V2 = False
    print(f"[STRAT] strategy_v2 indisponivel: {_e}")
# (23/11) chave geral do grafico/voz: variavel GRAFICO=0 no Railway desliga tudo (cache, setas e rotas) sem trocar arquivo
CHART_ON = (os.environ.get('GRAFICO') or '1').strip().lower() not in ('0', 'false', 'nao', 'off')
try:
    import chart_lines as CHL                 # (21/11) grafico ao vivo do painel (so visual)
except Exception as _e:
    CHL = None
    print(f"[CHART] chart_lines indisponivel: {_e}")
try:
    import gs_elite as GS                     # (11/11) operacional de confluencia proprio
    HAS_GS = True
except Exception as _e:
    GS = None
    HAS_GS = False
    print(f"[STRAT] gs_elite indisponivel: {_e}")
STRATEGY = 'v2'      # HYBRID abandonado: so a estrategia nova (setups TREND_PULLBACK, RANGE_REVERSION, BREAKOUT_PULLBACK, SPIKE_REVERSAL)
_UNUSED_STRATEGY_ENV = (os.environ.get('STRATEGY') or '')          # v2 (padrao) | v1 (HYBRID antigo)
SESSION_UTC = (os.environ.get('SESSION_UTC') or '').strip()          # so mercado aberto; '' = sem filtro de horario
PAYOUT_MIN = float(os.environ.get('PAYOUT_MIN') or 0.80)                 # so aplica se a corretora informar o payout
# (11/11) setups que entram com DINHEIRO REAL. Medidos na sua conta (8 OTC x 640 velas, payout 90% = empate 52.6%):
#   BREAKOUT_PULLBACK 62% (1min, perfil solto) e RANGE_REVERSION 59% (firme) = APROVADOS.
#   TREND_CONT 47% (n=141), TREND_PULLBACK 48% (n=65), OVERSTRETCH 46% (n=13), SPIKE_REVERSAL sem sinais
#   = FORA das entradas (amostra grande o bastante pra dizer que estao abaixo do empate), mas seguem
#   rodando no catalogo/virtual pra reavaliar com mais dados.
# (14/11) SETUP_ALLOW atualizado com o catalogo OTC de 14/11 (soma dos melhores ativos): entram com dinheiro as 5 que
# ficaram acima de 60% - TREND_PULLBACK ~75%, META_ENGOLFO ~75%, OVERSTRETCH ~65%, META_REJEICAO ~63%, TREND_CONT ~61%.
# META_ESTICADO, BREAKOUT_PULLBACK e RANGE_REVERSION sairam (abaixo de 50%) e tambem estao em SETUPS_OFF (strategy_v2).
# (16/11) estrategias antigas REMOVIDAS: o bot opera so NIVEL_CHAVE (suporte/resistencia em niveis-chave, key_levels.py)
SETUP_ALLOW = set(x.strip() for x in (os.environ.get('SETUP_ALLOW') or 'NIVEL_CHAVE').split(',') if x.strip())
print(f"[STRAT] estrategias que abrem ordem (SETUP_ALLOW): {', '.join(sorted(SETUP_ALLOW))}")
GS_EXPIRY = int(os.environ.get('GS_EXPIRY') or 2)                        # (12/11) GS-ELITE REMOVIDO das entradas a pedido do usuario (muitos losses reais) - continua medido no catalogo
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

def _journal(row, username=None):
    """Diario das operacoes reais (CSV) para analisar depois. Nunca derruba o bot. (25/11) um arquivo por usuario
    (o Master continua no trades_journal.csv antigo)."""
    try:
        import csv
        fn = 'trades_journal.csv' if (not username or _is_master_name(username)) else \
             'trades_journal_' + re.sub(r'[^A-Za-z0-9_-]', '_', str(username)) + '.csv'
        path = os.path.join(os.path.dirname(_ENGINE.path) or '.', fn) if _ENGINE else fn
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
# (10/11) Lista de OTC NATIVOS da corretora: EXATAMENTE os 9 que a biblioteca conhece no ACTIVES
# (checado um a um). USDCAD-OTC e NZDJPY-OTC foram removidos - NAO estao no dicionario da lib e
# falhavam ('ativo sem id' / timeout) dentro do sentimento e do catalogador.
FALLBACK_OTC = ['EURUSD-OTC', 'GBPUSD-OTC', 'USDJPY-OTC', 'AUDUSD-OTC', 'EURJPY-OTC', 'GBPJPY-OTC',
                'EURGBP-OTC', 'AUDCAD-OTC', 'NZDUSD-OTC', 'USDCHF-OTC']
FALLBACK_OPEN = []   # mercado aberto removido a pedido do usuario (10/11): so OTC

# (16/11) limite de ativos por sessao (manual e auto). Antes era 3 fixo.
MAX_ASSETS = max(1, int(os.environ.get('MAX_ATIVOS') or 10))

def _asset_mode(st):
    if st.get('_mode_top3'):
        return 'TOP3'
    return 'MANUAL'          # manual: ativo(s) escolhido(s) no painel (ate MAX_ASSETS)

def _refresh_top3(api, st, username, force=False):
    """(13/11) Escolhe os 3 melhores ativos pelo MEDIDOR DE SENTIMENTO medido NA HORA (o mesmo
    _sent_score do painel: 340 velas, regime + desvio + volatilidade + sinais recentes). O
    comportamento anterior (nota do motor / catalogacao em arquivo) fazia o TOP3 pegar ativos
    aleatorios quando a catalogacao estava velha - exatamente o que o usuario observou. Exclui
    quarentena/recusa e usa payout como desempate. Recalculado a cada 10 min (ou force)."""
    now = time.time()
    if not force and now - float(st.get('_top3_ts') or 0) < 600:
        return
    st['_top3_ts'] = now
    res = get_user_conn_state(username).get('result') or {}
    # (16/11) mercado do modo auto: 'OTC' (so OTC) ou 'OPEN' (so mercado aberto) - escolhido no painel
    _mkt = st.get('_auto_mkt') or 'OTC'
    if _mkt == 'OPEN':
        base = [a for a in (res.get('binary_assets') or []) if not _is_otc(a)]
    else:
        base = list(res.get('otc_assets') or []) or list(FALLBACK_OTC)
    if not base:
        _add_log(st, '[top3] Nenhum ativo ' + ('de mercado aberto' if _mkt == 'OPEN' else 'OTC') + ' aberto agora na corretora')
    bad = st.get('_asset_bad', {})
    # (13/11) ranking pelo SENTIMENTO MEDIDO NA HORA - o mesmo score do painel (0-100)
    cands = []
    for a in base:
        if a in _POISON or (bad.get(a) and bad[a][0] > now):
            continue
        pay = _payout_cached(api, a)
        if pay is not None and pay < PAYOUT_MIN:
            continue
        cands.append([a, None, pay or 80])
    # mede o sentimento dos candidatos (UM por vez, como no painel) - limitado pra nao travar
    cands.sort(key=lambda x: -(x[2]))
    medidos = 0
    for item in cands[:max(12, MAX_ASSETS + 4)]:
        a = item[0]
        ok_c, candles = _get_candles_safe(api, 12, a, 60, 340, time.time())
        if ok_c and candles:
            try:
                _s = _sent_score(candles, SV2.LEVELS.get(a))
                if _s and _s.get('status') == 'ok':
                    item[1] = int(_s.get('score', 0))
                    medidos += 1
            except Exception:
                pass
        time.sleep(1.0)
    medidos_rows = [c for c in cands if c[1] is not None]
    if medidos_rows:
        medidos_rows.sort(key=lambda x: (-x[1], -x[2]))
        new3 = [c[0] for c in medidos_rows[:MAX_ASSETS]]
        _add_log(st, f'[top3] Score de sentimento medido em {medidos} ativos agora - top {len(new3)}: '
                     + ', '.join(f'{c[0]} ({c[1]})' for c in medidos_rows[:MAX_ASSETS]))
    else:
        # sentimento indisponivel: mantem os ativos atuais (nao sorteia)
        new3 = st.get('_top3') or []
        _add_log(st, '[top3] Sentimento nao respondeu agora - mantenho os ativos atuais e tento de novo na proxima vela')
    old3 = st.get('_top3') or []
    if new3 and new3 != old3:
        _add_log(st, '[top3] ' + (f'Equipe trocada (antes: {", ".join(old3)})' if old3 else 'Equipe inicial definida'))
        st['_top3'] = new3

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
    if mode == 'MANUAL':                       # os ativos escolhidos no painel (1 a MAX_ASSETS), sem filtro de payout
        multi = [a for a in (st.get('manual_assets') or []) if a][:MAX_ASSETS]
        if multi:
            now = time.time()
            bad = st.get('_asset_bad', {})
            live = [a for a in multi if not (bad.get(a) and bad[a][0] > now) and a not in _POISON]
            if live:
                return live
            _throttled_log(st, '_multi_bad', f'[manual] {", ".join(multi)} recusado(s)/em quarentena - aguardando liberacao', every=120)
        a0 = st.get('manual_asset') or st.get('selected_asset')
        return [a0] if a0 else []
    if mode == 'TOP3':                         # (12/11) os 3 melhores escolhidos pelo proprio bot
        bad = st.get('_asset_bad', {})
        _now = time.time()
        _some_bad = any(bad.get(a, (0, ''))[0] > _now for a in (st.get('_top3') or []))
        _refresh_top3(api, st, username, force=_some_bad)   # membro caiu? troca AGORA pelo proximo
        t3 = [a for a in (st.get('_top3') or []) if not (bad.get(a, (0, ''))[0] > _now)][:MAX_ASSETS]
        return t3
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
    st['_reconnecting_ts'] = time.time()
    _add_log(st, f'[auto] {why} - reconectando na IQ Option...')

    def _job():
        try:
            ok = _reconnect_from_loop(username, st)
            if ok:
                # (10/11) login OK nao garante socket de velas: testa com uma leitura pequena
                api2 = _IQ_APIS.get(username)
                ok_t, res_t = _get_candles_safe(api2, 12, 'EURUSD-OTC', 60, 30, time.time()) if api2 else (False, 'sem api')
                if ok_t and res_t:
                    st['_rc_fail'] = 0
                    _add_log(st, '[auto] Reconectado com sucesso (velas respondendo)')
                else:
                    _add_log(st, f'[auto] Reconectado, mas as velas ainda nao responderam ({str(res_t)[:40]}) - nova tentativa em instantes')
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
    """False so quando o websocket DESTA conexao caiu.
    (25/11) Antes olhava so o aviso global da biblioteca (check_websocket_if_connect). Esse aviso e um so para a
    conexao inteira e o socket VELHO, ao ser fechado depois de uma reconexao, marcava 'caiu' -> a conexao NOVA era
    dada como morta -> reconectava de novo -> loop a cada vela (Reconectado... e 1s depois 'Socket da IQ fechado').
    Agora olha o socket desta conexao (WebSocketApp.sock.connected); o aviso global so vale se nao der para ver."""
    try:
        wsapp = getattr(getattr(getattr(api, 'api', None), 'websocket_client', None), 'wss', None)
        if wsapp is not None and getattr(wsapp, 'keep_running', None) is not None:
            sk = getattr(wsapp, 'sock', None)
            return bool(wsapp.keep_running and sk is not None and getattr(sk, 'connected', False))
    except Exception:
        pass
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
LIVE_MAX_READS = int(os.environ.get('LIVE_MAX_READS') or 6)      # (19/11) leituras por vela: era 3 por causa da API antiga que derrubava o socket no 4o pedido; com a API atualizada, 6
LIVE_PACE = float(os.environ.get('LIVE_PACE') or 0.5)            # (19/11) pausa entre leituras de ativos diferentes (era 1.0): 6 leituras cabem nos primeiros ~7s da vela
RODIZIO_ESPERTO = (os.environ.get('RODIZIO_ESPERTO') or '1').strip() not in ('0', 'false', 'nao', 'off')   # (19/11) le primeiro os ativos mais perto de sinal

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
        err_txt = 'resposta vazia' if ok else str(cs)[:40]
        if 'ativo sem id' in err_txt:
            _mark_bad(st, asset, 3600, 'ativo sem id na biblioteca')   # pula rapido: nao e falha de rede
        else:
            _asset_fail(api, st, username, asset, err_txt)
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
        st.get('_mg_level', {}).pop(asset, None)          # (12/11) martingale: ganho reseta o nivel
        if d:
            dstreak[d] = 0
        st.get('_dir_pause_until', {}).pop(asset, None)   # um win encerra a pausa por direcao
        return
    # --- martingale: perda sobe o nivel (proxima entrada maior), cap nos niveis escolhidos ---
    _mg = st.get('_mg_level', {})
    _mg_cur = int(_mg.get(asset, 0))
    _mg_max = max(0, min(3, int(st.get('mg_max') or 0))) if st.get('mg_enabled') else 0
    if _mg_max and _mg_cur < _mg_max:
        _mg[asset] = _mg_cur + 1
        _add_log(st, f'[mg] {asset}: perdeu - proxima entrada no nivel {_mg_cur + 1}/{_mg_max} (x{st.get("mg_factor") or 2})')
    elif _mg_max:
        _mg.pop(asset, None)                              # esgotou os niveis: volta ao valor base
    # --- trava global primeiro: protege a banca em qualquer modo ---
    st['_loss_streak_all'] = int(st.get('_loss_streak_all', 0)) + 1
    if st['_loss_streak_all'] >= LOSS_STOP_MAX:
        if not st.get('_lock3_enabled', True):
            st['_loss_streak_all'] = 0            # (12/11) trava desligada no painel: segue rodando
            _add_log(st, f'[trava] {LOSS_STOP_MAX} losses seguidos - trava DESATIVADA no painel, o bot continua')
        else:
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
CATALOG_CANDLES = int(os.environ.get('CATALOG_CANDLES') or 600)   # (16/11) 600 velas = 10 h: a NIVEL_CHAVE da poucos sinais por hora
_CATALOG = {}

def _catalog_path(username=None):
    """(25/11) um arquivo de catalogo POR USUARIO (antes era um so para todos: o usuario novo via o catalogo do Master).
    O Master continua no arquivo antigo (catalog_last.json) para nao perder o ultimo catalogo."""
    d = '/data' if os.path.isdir('/data') else '.'
    if not username or _is_master_name(username):
        return os.path.join(d, 'catalog_last.json')
    return os.path.join(d, 'catalog_last_' + re.sub(r'[^A-Za-z0-9_-]', '_', str(username)) + '.json')

def _is_master_name(username):
    try:
        with app.app_context():                    # roda tambem nas threads do catalogo/resultado
            u = User.query.filter_by(username=username).first()
            return bool(u and u.role == 'admin')
    except Exception:
        return username == 'daniloadaltosantana'

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

CATALOG_BLOCK = max(80, min(1000, int(os.environ.get('CATALOG_BLOCK') or 400)))   # (24/11) velas por pedido no catalogador (era 80)
CATALOG_PAUSE = float(os.environ.get('CATALOG_PAUSE') or 1.0)                       # (24/11) pausa entre pedidos (era 2.5s)

def _catalog_fetch(api, asset, total, cs):
    """Baixa `total` velas M1 do ativo, um pedido por vez. (24/11) Pedidos de CATALOG_BLOCK velas (era 80): as MESMAS
    velas em 2 pedidos em vez de 10 - o resultado do catalogo nao muda, so chega antes. Se a IQ entalar, o pedaco cai
    pela metade (minimo 80, o tamanho antigo) e tenta de novo. Cancelavel. Retorna (velas_fechadas, erro)."""
    block = CATALOG_BLOCK
    end = time.time()
    got, err = {}, ''
    tries = 0
    total = ((total + 79) // 80) * 80                  # mesma quantidade de velas que o metodo antigo (multiplo de 80)
    while len(got) < total - 10:
        if cs.get('cancel'):
            return [], 'cancelado'
        ok, res = _get_candles_safe(api, CATALOG_FETCH_TIMEOUT, asset, 60, block, end, cs=cs)
        if ok and res:
            tries = 0
            for c in res:
                try:
                    got[int(c['from'])] = c
                except Exception:
                    pass
            first = min(int(c['from']) for c in res)
            if first >= end - 1:
                break                                  # a corretora nao tem velas mais antigas
            end = first
            if len(got) < total:
                _cat_sleep(cs, CATALOG_PAUSE)
            continue
        err = 'timeout' if (not ok and res == 'timeout') else ('cancelado' if (not ok and res == 'cancelado') else (str(res)[:50] if not ok else 'resposta vazia'))
        if err == 'cancelado':
            return [], err
        if not ok and not _api_alive(api):
            return [], 'conexao caiu'
        tries += 1
        if tries >= 3:
            break
        block = max(80, block // 2)                    # IQ entalou: pedidos menores
        _cat_sleep(cs, 2)
    now_min = int(time.time() // 60 * 60)
    out = [got[k] for k in sorted(got) if k < now_min]
    n_old = 80 + (total // 80 - 1) * 79 - 1            # exatamente as velas que o metodo antigo (blocos de 80) usava:
    return out[-n_old:], err                           # mesmo aquecimento dos indicadores = mesmo resultado do catalogo

# (15/11) historico das estrategias para o DESEMPATE.
# Base geral (_SETUP_PRIOR) = soma dos melhores ativos do catalogo OTC de 14/11, misturada com o catalogo do ativo.
_SETUP_PRIOR = {'TREND_PULLBACK': 71 / 95, 'META_REJEICAO': 215 / 340, 'META_ENGOLFO': 24 / 32,
                'TREND_CONT': 178 / 291, 'OVERSTRETCH': 15 / 23}
_CAT_FILE_CACHE = {}          # (25/11) por arquivo (cada usuario tem o seu)

def _asset_key(a):
    return re.sub(r'[^A-Z0-9]', '', str(a or '').upper())

def _catalog_rows_for(username):
    cs = _CATALOG.get(username) or {}
    if cs.get('rows'):
        return cs['rows']
    try:
        p = _catalog_path(username)
        mt = os.path.getmtime(p)
        c = _CAT_FILE_CACHE.get(p)
        if not c or c['mtime'] != mt:
            with open(p, 'r', encoding='utf-8') as f:
                c = {'mtime': mt, 'rows': (json.load(f) or {}).get('rows') or []}
            _CAT_FILE_CACHE[p] = c
        return c['rows']
    except Exception:
        return []

# (25/11) EXPIRACAO DO CATALOGO: o bot entra com a 'Melhor exp' que o ULTIMO catalogo mostrou para o ativo (1 a 5 min).
# Nao e regra fixa: cada nova catalogacao atualiza. Catalogo velho (mais de CAT_EXP_MAX_H horas), ativo nao catalogado ou
# com poucos sinais = volta para a expiracao padrao (1 min). CAT_EXP=0 no Railway desliga e volta tudo para 1 min.
CAT_EXP = (os.environ.get('CAT_EXP') or '1').strip().lower() not in ('0', 'false', 'nao', 'off')
CAT_EXP_MAX_H = float(os.environ.get('CAT_EXP_MAX_H') or 12)
CAT_EXP_MIN_N = int(os.environ.get('CAT_EXP_MIN_N') or 5)

def _catalog_best_h(username, asset):
    """(exp, acerto%, sinais, horas_desde_o_catalogo) da 'Melhor exp' do ultimo catalogo, ou (None, motivo)."""
    if not CAT_EXP:
        return None, 'expiracao do catalogo desligada (CAT_EXP=0)'
    rows = _catalog_rows_for(username)
    k = _asset_key(asset)
    row = next((r for r in rows if _asset_key(r.get('asset')) == k and r.get('status') == 'ok'), None)
    if not row:
        return None, 'ativo fora do ultimo catalogo'
    ts = row.get('ts')
    if not ts:
        cs = _CATALOG.get(username) or {}
        ts = cs.get('started')
        if not ts:
            try:
                ts = os.path.getmtime(_catalog_path(username))
            except Exception:
                ts = 0
    age = (time.time() - float(ts or 0)) / 3600.0
    if age > CAT_EXP_MAX_H:
        return None, f'catalogo de {age:.0f}h atras (limite {CAT_EXP_MAX_H:g}h) - catalogue de novo'
    bh = row.get('best_h')
    if not bh or int(row.get('n') or 0) < CAT_EXP_MIN_N:
        return None, f'poucos sinais no catalogo ({row.get("n", 0)}; preciso de {CAT_EXP_MIN_N})'
    bh = int(bh)
    if bh < 1 or bh > 5:
        return None, f'melhor exp {bh} min fora do permitido (1 a 5)'
    return (bh, row.get('best_h_wr'), int(row.get('n') or 0), age), ''

_PRIOR_K = 20   # peso do historico geral = 20 sinais "imaginarios"; com poucos sinais no ativo, vale mais o geral

def _setup_hist_lb(username, asset, setup):
    """Acerto estimado do setup NESTE ativo: mistura o catalogo do ativo com o historico geral
    ((acertos + K*geral) / (sinais + K)) - assim 3/4 num ativo nao passa na frente de 36/49."""
    prior = _SETUP_PRIOR.get(setup, 0.5)
    try:
        k = _asset_key(asset)
        for r in _catalog_rows_for(username):
            if _asset_key(r.get('asset')) == k:
                v = (r.get('per') or {}).get(setup) or {}
                w, l = int(v.get('w', 0)), int(v.get('l', 0))
                return (w + _PRIOR_K * prior) / (w + l + _PRIOR_K)
    except Exception:
        pass
    return prior

def _catalog_ranked(rows):
    ok = [dict(r) for r in rows if r.get('status') == 'ok' and r.get('n', 0) > 0]
    ok.sort(key=lambda r: (-r['lb'], -r['n']))
    for i, r in enumerate(ok, 1):
        r['rank'] = i
    rest = [dict(r) for r in rows if not (r.get('status') == 'ok' and r.get('n', 0) > 0)]
    return ok + rest

def _catalog_save(cs, username=None):
    try:
        with open(_catalog_path(username), 'w', encoding='utf-8') as f:
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
            # (16/11) niveis-chave do ativo (H1, H4, M5): o catalogo mede a estrategia NIVEL_CHAVE com eles
            lv = None
            if len(hist) >= SV2.MIN_BARS + 20:
                _klr = st.get('_kl', {}).get(asset) or {}
                if SV2.LEVELS.get(asset) and time.time() - _klr.get('ts', 0) < KL_REFRESH_SEC and not _klr.get('err'):
                    lv, err_lv = SV2.LEVELS[asset], ''        # (24/11) niveis ainda frescos (< 15 min): nao baixa de novo
                else:
                    lv, err_lv = _kl_levels_from_api(api, asset, timeout=CATALOG_FETCH_TIMEOUT, cs=cs)
                if cs.get('cancel'):
                    return None
                if lv is None:
                    err = err or ('niveis: ' + err_lv)
                else:
                    SV2.LEVELS[asset] = lv                                   # o bot ja comeca com os niveis prontos
                    st.setdefault('_kl', {})[asset] = {'ts': time.time(), 'n': len(lv), 'err': ''}
                    row['niveis'] = _kl_resumo(lv)
            r = SV2.catalog_eval(hist, cs['eval_n'], levels=lv) if (lv is not None and len(hist) >= SV2.MIN_BARS + 20) else None
            gs_res = None                    # (16/11) GS-ELITE removido junto com as estrategias antigas
            if r is None:
                row.update(status='falhou', err=err or f'so {len(hist)} velas', n=0, w=0, l=0, t=0, wr=0.0, lb=0.0)
                if 'ativo sem id' not in (err or ''):
                    fails += 1          # ativo desconhecido nao e falha de rede: nao dispara reconexao
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
                if gs_res:
                    _gm = (gs_res.get('modes') or {}).get('REVERSAO') or {}
                    if _gm.get('n'):
                        row['gs'] = _gm
                        _add_log(st, f'[catalogador] {asset} GS-ELITE REVERSAO: {_gm["n"]} sinais | wr 1m {_gm["wr1m"]}% | 2m {_gm["wr2m"]}% | 3m {_gm["wr3m"]}% | melhor exp {_gm["best_h"]}min {_gm["best_wr"]}%')
                # resumo POR SETUP no log: sinais, win rate e RSI medio dos disparos
                _ps = []
                for _s, _v in (r.get('per') or {}).items():
                    _n = _v.get('w', 0) + _v.get('l', 0)
                    if _n:
                        _wr = _v['w'] / _n * 100
                        _rm = _v.get('rsi_medio')
                        _ps.append(f'{_s}: {_n} sinais {_wr:.0f}%' + (f' (RSI medio {_rm:.1f})' if _rm is not None else ''))
                if _ps:
                    _add_log(st, f'[catalogador] {asset} -> ' + ' | '.join(_ps))
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
                    row['ts'] = time.time()            # (24/11) hora em que este ativo foi catalogado
                    cs['rows'].append(row)
                    cs['done'] = len(cs['rows'])
                _cat_sleep(cs, 1.0)
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
        _catalog_save(cs, username)
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
    if scope not in ('ONE', 'OTC', 'OPEN', 'ALL', 'TOP3'):
        scope = 'ONE'
    if scope == 'TOP3':
        top = [a for a in (st.get('manual_assets') or [])[:MAX_ASSETS] if a]
        if not top:
            return jsonify(ok=False, error='Escolha os ativos no painel (Operar > Ativos) ou ative o modo TOP3 automatico.'), 200
    if scope == 'ONE' and not (st.get('manual_asset') or st.get('selected_asset')):
        return jsonify(ok=False, error='Selecione o ativo no painel (Operar > Ativo) antes de catalogar.'), 200
    if st.get('running') or st.get('bot_running'):
        return jsonify(ok=False, error='Pare o bot antes de catalogar (a IQ nao aceita as duas coisas ao mesmo tempo).'), 200
    if (_SENT.get(username) or {}).get('running'):
        return jsonify(ok=False, error='A varredura de sentimento esta rodando - espere ou cancele (a IQ nao aceita as duas ao mesmo tempo).'), 200
    if (_EXPORT.get(username) or {}).get('running'):
        return jsonify(ok=False, error='A exportacao de velas esta rodando - espere ou cancele.'), 200
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
            with open(_catalog_path(username), 'r', encoding='utf-8') as f:
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


# ======================= EXPORTAR VELAS (18/11) =======================
# Baixa o historico de velas M1 dos ativos escolhidos (ate MAX_ASSETS) e gera um .zip com um CSV para testes
# fora do bot (ex.: estrategias em OTC, que nao tem historico publico). So LE velas, pelo mesmo caminho do
# catalogador (_get_candles_safe): nao mexe em conexao, login nem ordens. So roda com o bot parado.
# Pedidos de EXPORT_BLOCK velas com pausa de EXPORT_PAUSE s; se a IQ falhar, o pedaco cai pela metade (minimo 80).
EXPORT_BLOCK = max(80, min(1000, int(os.environ.get('EXPORT_BLOCK') or 500)))
EXPORT_PAUSE = float(os.environ.get('EXPORT_PAUSE') or 1.5)
EXPORT_MAX_DAYS = 60
_EXPORT = {}

def _export_dir():
    d = '/data/exports' if os.path.isdir('/data') else 'exports'
    os.makedirs(d, exist_ok=True)
    return d

def _export_file(username):
    return os.path.join(_export_dir(), 'velas_' + re.sub(r'[^A-Za-z0-9_-]', '_', str(username)) + '.zip')

def _export_wait_conn(ex, st, username, why):
    _reconnect_now(st, username, why)
    _cat_sleep(ex, 3)
    for _w in range(40):
        if not st.get('_reconnecting') or ex.get('cancel'):
            break
        _cat_sleep(ex, 1)
    api = _IQ_APIS.get(username)
    return api if (api is not None and _api_alive(api)) else None

def _export_run(username, assets, days):
    import zipfile
    ex = _EXPORT[username]
    st = get_user_state(username)
    tmp = os.path.join(_export_dir(), 'velas_' + re.sub(r'[^A-Za-z0-9_-]', '_', str(username)) + '.csv.tmp')
    span = days * 86400
    try:
        _add_log(st, f'[exportar] Iniciado: {len(assets)} ativo(s), {days} dias de velas M1 cada')
        now_min = int(time.time() // 60 * 60)
        start_ts = now_min - span
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write('asset,from,open,max,min,close,volume\n')
            for k, asset in enumerate(assets):
                if ex.get('cancel'):
                    break
                ex.update(current=asset, idx=k, asset_pct=0)
                api = _IQ_APIS.get(username)
                if api is None or not _api_alive(api):
                    api = _export_wait_conn(ex, st, username, 'Exportar velas: conexao caiu')
                    if api is None:
                        ex['errors'].append(f'{asset}: sem conexao')
                        continue
                end = time.time()
                block = EXPORT_BLOCK
                got, fails, err = {}, 0, ''
                while end > start_ts and not ex.get('cancel'):
                    ok, res = _get_candles_safe(api, CATALOG_FETCH_TIMEOUT, asset, 60, block, end, cs=ex)
                    if ok and res:
                        for c in res:
                            try:
                                got[int(c['from'])] = c
                            except Exception:
                                pass
                        first = min(int(c['from']) for c in res)
                        if first >= end - 1:
                            break                              # a corretora nao tem velas mais antigas
                        end = first
                        fails = 0
                        ex['asset_pct'] = int(min(100, (now_min - end) / span * 100))
                        ex['asset_n'] = len(got)
                        _cat_sleep(ex, EXPORT_PAUSE)
                        continue
                    if ex.get('cancel'):
                        break
                    fails += 1
                    err = 'timeout' if (not ok and res == 'timeout') else (str(res)[:50] if not ok else 'resposta vazia')
                    if not ok and not _api_alive(api):
                        api = _export_wait_conn(ex, st, username, 'Exportar velas: conexao caiu')
                        if api is None:
                            break
                    if ok and fails >= 2:
                        err = 'fim do historico'                 # resposta vazia de novo: a corretora nao tem mais velas
                        break
                    if block > 80:
                        block = max(80, block // 2)              # IQ entalou: pedidos menores
                    if fails >= 4:
                        break
                    _cat_sleep(ex, 3)
                rows = [got[t] for t in sorted(got) if start_ts <= t < now_min]
                for c in rows:
                    f.write(f'{asset},{int(c["from"])},{c.get("open")},{c.get("max")},{c.get("min")},{c.get("close")},{c.get("volume", 0)}\n')
                f.flush()
                dias = (len(rows) / 1440.0)
                ex['per'][asset] = len(rows)
                ex['done'] = k + 1
                if rows and err == 'fim do historico':
                    ex['errors'].append(f'{asset}: a corretora so tem ~{dias:.1f} dias')
                elif rows and fails >= 4:
                    ex['errors'].append(f'{asset}: parou em {dias:.1f} dias ({err})')
                elif not rows:
                    ex['errors'].append(f'{asset}: nenhuma vela ({err or "sem dados"})')
                _add_log(st, f'[exportar] {asset}: {len(rows)} velas (~{dias:.1f} dias)' + (f' - parou: {err}' if fails >= 4 else ''))
        total = sum(ex['per'].values())
        if total:
            out = _export_file(username)
            with zipfile.ZipFile(out + '.tmp', 'w', zipfile.ZIP_DEFLATED) as z:
                z.write(tmp, arcname='velas_m1.csv')
            os.replace(out + '.tmp', out)
            ex['file_ok'] = True
            ex['file_size'] = os.path.getsize(out)
        ex['cancelled'] = bool(ex.get('cancel'))
        _add_log(st, f'[exportar] {"Cancelado" if ex["cancelled"] else "Concluido"}: {total} velas de {len([a for a in ex["per"] if ex["per"][a]])} ativo(s)'
                     + (' - arquivo pronto para baixar' if total else ''))
    except Exception as e:
        ex['error'] = str(e)[:120]
        _add_log(st, f'[exportar] erro: {e}')
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
        ex['running'] = False
        ex['finished'] = time.time()
        ex['current'] = ''

@app.route('/api/export/start', methods=['POST'])
def export_start():
    u = current_user()
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    data = request.get_json(silent=True) or {}
    assets = []
    for a in (data.get('assets') or []):
        a = str(a).strip()
        if a and a not in assets:
            assets.append(a)
    assets = assets[:MAX_ASSETS]
    try:
        days = max(1, min(EXPORT_MAX_DAYS, int(data.get('days') or 30)))
    except Exception:
        days = 30
    if not assets:
        return jsonify(ok=False, error='Escolha os ativos na aba Operar (ativo de cima + Mais ativos).'), 200
    if st.get('running') or st.get('bot_running'):
        return jsonify(ok=False, error='Pare o bot antes de exportar (a IQ nao aceita as duas coisas ao mesmo tempo).'), 200
    if (_CATALOG.get(username) or {}).get('running') or (_SENT.get(username) or {}).get('running'):
        return jsonify(ok=False, error='Catalogador ou sentimento rodando - espere ou cancele.'), 200
    cur = _EXPORT.get(username)
    if cur and cur.get('running'):
        return jsonify(ok=False, error='A exportacao ja esta rodando.'), 200
    if not _IQ_APIS.get(username):
        return jsonify(ok=False, error='Conecte na corretora primeiro.'), 200
    _EXPORT[username] = {'running': True, 'assets': assets, 'days': days, 'total': len(assets), 'done': 0, 'idx': 0,
                         'current': '', 'asset_pct': 0, 'asset_n': 0, 'per': {}, 'errors': [], 'started': time.time(),
                         'finished': None, 'cancel': False, 'cancelled': False, 'error': None, 'file_ok': False, 'file_size': 0}
    threading.Thread(target=_export_run, args=(username, assets, days), daemon=True).start()
    return jsonify(ok=True, assets=assets, days=days), 200

@app.route('/api/export/cancel', methods=['POST'])
def export_cancel():
    u = current_user()
    ex = _EXPORT.get(u.get('sub', 'admin'))
    if ex and ex.get('running'):
        ex['cancel'] = True
        _add_log(get_user_state(u.get('sub', 'admin')), '[exportar] Cancelando... (o que ja foi baixado fica no arquivo)')
    return jsonify(ok=True), 200

@app.route('/api/export/status', methods=['GET'])
def export_status():
    u = current_user()
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    ex = _EXPORT.get(username) or {}
    path = _export_file(username)
    has_file = os.path.isfile(path)
    return jsonify(ok=True, running=bool(ex.get('running')), total=ex.get('total', 0), done=ex.get('done', 0),
                   current=ex.get('current', ''), asset_pct=ex.get('asset_pct', 0), asset_n=ex.get('asset_n', 0),
                   per=ex.get('per', {}), errors=ex.get('errors', []), days=ex.get('days'), cancelled=bool(ex.get('cancelled')),
                   cancelling=bool(ex.get('cancel') and ex.get('running')), error=ex.get('error'), started=ex.get('started'),
                   has_file=has_file, file_size=(os.path.getsize(path) if has_file else 0),
                   file_time=(os.path.getmtime(path) if has_file else None), bot_running=bool(st.get('running'))), 200

@app.route('/api/export/download', methods=['GET'])
def export_download():
    u = current_user()
    path = _export_file(u.get('sub', 'admin'))
    if not os.path.isfile(path):
        return jsonify(ok=False, error='Nenhum arquivo exportado ainda.'), 404
    return send_file(path, as_attachment=True, mimetype='application/zip',
                     download_name='danbot_velas_' + time.strftime('%Y%m%d_%H%M', time.localtime(os.path.getmtime(path))) + '.zip')

# ======================= SENTIMENTO DO MERCADO (09/11, feito do zero) =======================
# Varre UM ativo por vez (a IQ entala com pedidos paralelos - era o problema do catalogador velho)
# e pontua de 0 a 100 o quanto o MOMENTO de cada ativo combina com os 5 setups:
#   - regime de TENDENCIA clara (desde 15/11 o lateral nao pontua: a RANGE_REVERSION foi desligada)
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

def _sent_score(hist, levels=None):
    """(16/11) Sentimento para a estrategia NIVEL_CHAVE: quanto o MOMENTO do ativo esta perto de uma entrada.
    Pontua de 0 a 100: preco perto de um nivel-chave (40), nivel forte H4/POC (15), RSI 14 esticado na direcao
    da reversao (30) e Bollinger esticada (15). Mostra tambem os sinais e o acerto da NIVEL_CHAVE nas ultimas velas."""
    try:
        import numpy as _np
        n = len(hist)
        if n < SV2.MIN_BARS + 30:
            return {'status': 'falhou', 'err': f'so {n} velas'}
        if not levels:
            return {'status': 'falhou', 'err': 'sem niveis-chave (velas H1/H4/M5)'}
        F = SV2.features(_np.array([x['open'] for x in hist], float), _np.array([x['max'] for x in hist], float),
                         _np.array([x['min'] for x in hist], float), _np.array([x['close'] for x in hist], float))
        F['t'] = [int(x['from']) for x in hist]
        F['kl_levels'] = levels
        KL.prepare(F)
        i = n - 1
        rsi = float(F['kl_rsi'][i])
        c = float(F['c'][i])
        lo, hi = float(F['kl_bb_lo'][i]), float(F['kl_bb_hi'][i])
        pb = (c - lo) / (hi - lo) if hi > lo else 0.5                    # %B: 0 = banda de baixo, 1 = banda de cima
        near = KL.nearest(F, i, levels, F['t'][i])
        score = 0.0
        regime, lado = 'LONGE DOS NIVEIS', ''
        if near:
            dz = near['dist_z']
            lado = near['lado']
            score += 40 if dz <= 1 else (30 if dz <= 3 else (15 if dz <= 6 else 0))
            forte = any(t.startswith('H4') or t.startswith('POC') for t in near['tags'])
            score += 15 if forte else 8
            if lado == 'SUPORTE':          # reversao para CALL: RSI baixo e preco na banda de baixo ajudam
                score += 30 if rsi <= KL.RSI_LO else (15 if rsi <= 45 else 0)
                score += 15 if pb <= 0.05 else (8 if pb <= 0.25 else 0)
            else:
                score += 30 if rsi >= KL.RSI_HI else (15 if rsi >= 55 else 0)
                score += 15 if pb >= 0.95 else (8 if pb >= 0.75 else 0)
            if dz <= 6:
                regime = f'PERTO DE {lado} ({"+".join(near["tags"])})'
        score = int(round(max(0.0, min(100.0, score))))
        verdict = 'EXCELENTE' if score >= 70 else ('FAVORAVEL' if score >= 55 else ('NEUTRO' if score >= 40 else 'EVITAR'))
        cat = SV2.catalog_eval(hist, n - SV2.MIN_BARS - 1, levels=levels) or {}
        cnt = {'CALL': 0, 'PUT': 0}
        for j in range(SV2.MIN_BARS, n):
            for _s, d, _ in SV2.signals_at(F, j):
                cnt[d] += 1
        sig_n = cnt['CALL'] + cnt['PUT']
        bias = ('puxao CALL' if cnt['CALL'] > cnt['PUT'] * 1.5 else ('puxao PUT' if cnt['PUT'] > cnt['CALL'] * 1.5 else 'equilibrado'))
        acerto = (f"{cat['w']}/{cat['n']} ({cat['wr']}%)" if cat.get('n') else 'sem sinais')
        return {'status': 'ok', 'regime': regime, 'score': score, 'verdict': verdict, 'rsi': round(rsi, 1),
                'dist': round(near['dist_z'], 1) if near else None, 'pb': round(pb, 2),
                'nivel': round(near['p'], 6) if near else None, 'niveis': _kl_resumo(levels),
                'sig_n': sig_n, 'call': cnt['CALL'], 'put': cnt['PUT'], 'bias': bias, 'acerto': acerto,
                # campos antigos mantidos para o painel nao quebrar
                'slope': round(near['dist_z'], 1) if near else '-', 'er': round(pb, 2), 'atr_pct': '-', 'setups': ''}
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
        _add_log(st, f'[sentimento] Iniciado: {len(assets)} ativo(s) ({scope}) - UM por vez, 340 velas M1 + niveis M15/H1/H4/M5')
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
                lv = SV2.LEVELS.get(asset)
                if not lv or time.time() - st.get('_kl', {}).get(asset, {}).get('ts', 0) > KL_REFRESH_SEC:
                    lv, _e_lv = _kl_levels_from_api(api, asset, timeout=12, cs=ss)
                    if lv is not None:
                        SV2.LEVELS[asset] = lv
                        st.setdefault('_kl', {})[asset] = {'ts': time.time(), 'n': len(lv), 'err': ''}
                r = _sent_score(candles, lv) or {'status': 'falhou', 'err': 'dados insuficientes'}
            elif str(err_c or '').startswith('ativo sem id'):
                # ativo que nem a corretora tem no mapeamento: pula rapido, SEM reconectar (rede esta boa)
                r = {'status': 'falhou', 'err': err_c}
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
                             f"RSI14 {r['rsi']}, %B {r['pb']}, nivel a {r['dist']} zonas - NIVEL_CHAVE nas ultimas velas: "
                             f"{r['acerto']} ({r['bias']}) - niveis: {r['niveis']}")
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
    if scope not in ('ONE', 'OTC', 'OPEN', 'ALL', 'TOP3'):
        scope = 'ONE'
    if scope == 'TOP3':
        top = [a for a in (st.get('manual_assets') or [])[:MAX_ASSETS] if a]
        if not top:
            return jsonify(ok=False, error='Escolha os ativos no painel (Operar > Ativos) ou ative o modo TOP3 automatico.'), 200
    if scope == 'ONE' and not (st.get('manual_asset') or st.get('selected_asset')):
        return jsonify(ok=False, error='Selecione o ativo no painel (Operar > Ativo) antes de medir.'), 200
    if st.get('running') or st.get('bot_running'):
        return jsonify(ok=False, error='Pare o bot antes de medir o sentimento (mesma conexao).'), 200
    if (_CATALOG.get(username) or {}).get('running'):
        return jsonify(ok=False, error='O catalogador esta rodando - espere ou cancele.'), 200
    if (_EXPORT.get(username) or {}).get('running'):
        return jsonify(ok=False, error='A exportacao de velas esta rodando - espere ou cancele.'), 200
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

# (26/11) operacoes simultaneas: ate MAX_SIMULTANEAS ao mesmo tempo, sempre em ativos DIFERENTES
try:
    MAX_SIMULTANEAS = max(1, int(os.environ.get('MAX_SIMULTANEAS') or 3))
except ValueError:
    MAX_SIMULTANEAS = 3


def _busy_clean(st):
    """Ativos com operacao em andamento {ativo: ate_quando}; remove as que ja terminaram."""
    d = st.setdefault('_busy', {})
    now = time.time()
    for k in [k for k, v in d.items() if v <= now]:
        d.pop(k, None)
    return d


def _auto_window(api, st, username, entry_value, mode, uni, candle_ts):
    """Primeiros segundos da vela: olha os melhores ativos e entra no melhor sinal validado."""
    st['_auto_key'] = candle_ts
    direct = (SV2.GATE == 'off')              # filtro desligado: sem historico, so le velas recentes e procura sinal
    booted = [a for a in uni if st.get('_booted', {}).get(a)]
    pool = list(uni) if direct else booted
    # (12/11) ativo recusado pela corretora ('active is suspended') NAO gera nova ordem ate a suspensao acabar:
    # antes o MANUAL ignorava a marca e insistia a cada vela (uma recusa por minuto no log)
    _bad = st.get('_asset_bad', {})
    _now = time.time()
    pool = [a for a in pool if _bad.get(a, (0, ''))[0] <= _now]
    if not pool:
        _a0 = (uni or ['?'])[0]
        _until = int((_bad.get(_a0, (0, ''))[0] - _now) // 60) + 1
        _throttled_log(st, '_pool_bad', f'[auto] {_a0} recusada pela corretora - aguardando {_until} min antes de tentar de novo', every=120)
        return
    if not pool:
        return
    lat = max(0.25, st.get('_fetch_lat', 0.6))
    n_watch = int(min(LIVE_MAX_READS, max(1, (ENTRY_MAX_SECONDS - 2.0) / (lat + LIVE_PACE))))
    cur = 0
    if direct and RODIZIO_ESPERTO and len(pool) > n_watch:
        # (19/11) RODIZIO ESPERTO: as mesmas n leituras por vela, mas le primeiro os ativos que estavam mais perto
        # de sinal na ULTIMA leitura deles (RSI esticado + preco perto da banda) e guarda 1 vaga para o ativo lido
        # ha mais tempo (ninguem fica esquecido). Simulacao 10 ativos: +34% entradas no mercado aberto, +23% no OTC,
        # com o mesmo acerto. RODIZIO_ESPERTO=0 volta para a fila simples.
        cur = st.get('_rr_direct', 0) % len(pool)
        near = st.setdefault('_near', {})
        _t = time.time()
        def _nota(a):
            sc, ts = near.get(a, (0.0, 0.0))
            return sc + 0.02 * min((_t - ts) / 60.0, 60.0)
        best = sorted(pool, key=lambda a: -_nota(a))[:max(1, n_watch - 1)]
        if n_watch > 1:
            old = sorted([a for a in pool if a not in best], key=lambda a: near.get(a, (0.0, 0.0))[1])
            watch = best + old[:n_watch - len(best)]
        else:
            watch = best
        ranked = watch
        _throttled_log(st, '_rodizio', '[rodizio] lendo primeiro os mais perto de sinal: ' +
                       ', '.join(f'{a} ({near.get(a, (0.0, 0.0))[0]:.1f})' for a in watch), every=300)
    elif direct:                               # rodizio: n ativos por vela, um de cada vez
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

    # (26/11) ENTRADA IMEDIATA: assim que um ativo da sinal, a ordem sai NA HORA (antes o bot lia os 6 ativos da
    # vela e so depois escolhia - a ordem saia a ~6,5s da vela e pegava o preco ja andado; ex. XPDUSD 14:10).
    # Ate MAX_SIMULTANEAS operacoes ao mesmo tempo, cada uma num ativo diferente.
    _used = set()
    _stop = [False]

    def _entrar(best):
        asset, sg, h, info = best['asset'], best['sg'], best['h'], best['info']
        if asset in _used:
            return 'next'
        busy = _busy_clean(st)
        if len(busy) >= MAX_SIMULTANEAS:
            _add_log(st, f'[v2] {info} - ja existem {len(busy)} operacoes abertas ({", ".join(sorted(busy))}; maximo {MAX_SIMULTANEAS}), nao entrei')
            return 'stop'
        if asset in busy:
            _add_log(st, f'[v2] {info} - ja existe uma operacao aberta neste ativo, nao entrei')
            return 'next'
        if SV2.GATE == 'off' and str(st.get('broker_account_type') or 'PRACTICE').upper() not in ('PRACTICE', 'DEMO'):
            _throttled_log(st, '_real_warn', '[v2] ATENCAO: conta REAL operando sem filtro estatistico (so com a trava de 3 losses por ativo)', every=3600)
        if mode == 'OPEN' and not _session_ok(asset):
            _throttled_log(st, '_v2_sess_log', f'[v2] {info} - fora do horario de liquidez ({SESSION_UTC} UTC), nao entrei', every=300)
            return 'next'
        late = time.time() - candle_ts
        if late > ENTRY_MAX_SECONDS:
            _add_log(st, f'[v2] {info} - pronto so a {late:.0f}s do nascimento da vela, entrada pulada (limite {ENTRY_MAX_SECONDS:.0f}s)')
            return 'stop'
        _pt = st.get('_dir_pause_until', {}).get(asset, 0)
        if time.time() < _pt:
            _throttled_log(st, '_dir_pause_log', f'[pausa] {asset} pausado por mais {int((_pt - time.time()) / 60) + 1} min (losses seguidos na mesma direcao) - troque o ativo ou aguarde', every=60)
            return 'next'
        if busy:
            _add_log(st, f'[auto] {len(busy)} operacao(oes) em andamento ({", ".join(sorted(busy))}) - abrindo mais uma em {asset} (maximo {MAX_SIMULTANEAS})')
        _add_log(st, f'[v2] SINAL {info} | {best["why"]}')
        label = datetime.datetime.fromtimestamp(candle_ts, BRT).strftime('%H:%M')
        meta = {'setup': sg['setup'], 'h': h, 'dir': sg['dir'], 'det': sg['det']}
        st['selected_asset'] = asset
        outcome = _execute_entry(api, st, username, asset, sg['dir'], entry_value, h, candle_ts, label, meta=meta)
        if outcome == 'ok':
            busy[asset] = time.time() + h * 60 + 20
            _used.add(asset)
        elif outcome == 'timeout':
            busy[asset] = time.time() + h * 60 + 20
            _used.add(asset)
            _mark_bad(st, asset, 900, 'ordem sem resposta (pode ter sido enviada)')
        else:
            _mark_bad(st, asset, 900, 'ordem recusada/cancelada pela corretora')
        return 'ok'

    cands = []
    visited = 0
    for asset in watch:
        if visited > 0 and len(watch) > 1:
            time.sleep(LIVE_PACE)              # espaca as leituras: rajada de pedidos faz a IQ fechar o socket
        if time.time() - candle_ts > ENTRY_MAX_SECONDS - 1.5 or int(time.time() // 60 * 60) != candle_ts:
            break                              # acabou o tempo da janela
        visited += 1
        closed, status = _fetch_closed(api, st, username, asset)
        _tries = 0
        while status == 'stale' and _tries < 2 and time.time() - candle_ts < ENTRY_MAX_SECONDS - 1.5:
            _tries += 1                        # a IQ costuma publicar a vela fechada 1-3s depois: ESPERA (2s) em vez de
            time.sleep(2.0)                    # martelar o socket com pedidos seguidos (isso derrubava a conexao)
            closed, status = _fetch_closed(api, st, username, asset)
        if status != 'ok':
            _throttled_log(st, '_stat_' + status, f'[analise] {asset}: leitura de velas sem sucesso ({"IQ ainda nao publicou a vela fechada" if status == "stale" else "falha/conexao"}) - tento de novo na proxima vela', every=25)
            continue
        sigs = _ENGINE.ingest(asset, closed)
        # (16/11) GS-ELITE removido junto com as estrategias antigas (nem e mais medido)
        if not SV2.LEVELS.get(asset):
            _throttled_log(st, f'_kl_wait_{asset}', f'[niveis] {asset}: aguardando carregar os niveis-chave (M15/H1/H4/M5) - sem niveis o bot nao entra', every=120)
        st.setdefault('_asset_ts', {})[asset] = time.time()
        if CHART_ON:
            st.setdefault('_chart', {})[asset] = {'ts': time.time(), 'c': closed[-180:]}   # (21/11) grafico do painel (sem pedido extra)
        try:
            _F = SV2.features([x['open'] for x in closed], [x['max'] for x in closed], [x['min'] for x in closed], [x['close'] for x in closed])
            _i = len(closed) - 1
            _sg = ', '.join(f'{g["setup"]} {g["dir"]}' for g in sigs) or 'nenhum sinal'
            st['_hb'] = {'asset': asset, 'ts': time.time(), 'price': float(closed[-1]['close']), 'rsi': float(_F['rsi'][_i]),
                         'er': float(_F['er'][_i]), 'z': float(_F['z'][_i]), 'ar': float(_F['atr_ratio'][_i]), 'sg': _sg, 'n': len(closed)}
            _h = st['_hb']
            # (16/11) leitura da NIVEL_CHAVE: RSI 14, Bollinger e o nivel-chave mais perto do preco
            KL.prepare(_F)
            try:                                   # (19/11) nota de 'perto de sinal' para o rodizio esperto
                _lo, _hi, _c = float(_F['kl_bb_lo'][_i]), float(_F['kl_bb_hi'][_i]), float(closed[_i]['close'])
                _pb = (_c - (_lo + _hi) / 2) / ((_hi - _lo) / 2) if _hi > _lo else 0.0
                _rs = float(_F['kl_rsi'][_i])
                _sc = max(0.0, abs(_rs - 50) - 10) / 15 + max(0.0, abs(_pb) - 0.4) / 0.6
                st.setdefault('_near', {})[asset] = (_sc if _sc == _sc else 0.0, time.time())
            except Exception:
                pass
            _near = KL.nearest(_F, _i, SV2.LEVELS.get(asset), int(closed[_i]['from']))
            _nt = (f'{_near["lado"].lower()} {"+".join(_near["tags"])} {_near["p"]:.5f} a {_near["dist_z"]:.1f} zonas'
                   if _near else 'sem niveis carregados')
            _bb = ('tocou BB de baixo' if closed[_i]['min'] <= _F['kl_bb_lo'][_i] else
                   ('tocou BB de cima' if closed[_i]['max'] >= _F['kl_bb_hi'][_i] else 'dentro da BB'))
            _vx = _F.get('kl_bx')
            _vt = (f' | vela {_vx[_i]:.1f}x' + (f' (precisa {KL.VELA_MIN:g}x)' if KL.VELA_MIN > 0 else '')) if _vx is not None and float(_vx[_i]) == float(_vx[_i]) else ''
            _add_log(st, f'[analise] {asset} | preco {_h["price"]:.5f} | RSI14 {_F["kl_rsi"][_i]:.0f} | {_bb}{_vt} | nivel mais perto: {_nt} -> {_sg}')
        except Exception as _e:
            _throttled_log(st, '_hb_err', f'[analise] {asset}: velas lidas ({len(closed)}) mas nao consegui medir: {_e}', every=120)
        mi = _ENGINE.manip_info(asset)
        if mi.get('reason'):
            _bloq = ' - setups normais bloqueados por 5 velas' if os.environ.get('MANIP_BLOCK', '0') == '1' else ''
            _throttled_log(st, f'_manip_{asset}', f'[info] {asset}: {mi["reason"]}{_bloq}', every=300)
        for sg in sigs:
            # (11/11) SETUP_ALLOW: so os setups APROVADOS nos dados reais entram com dinheiro
            # (BREAKOUT_PULLBACK 62% em 1min, RANGE_REVERSION firme 59% - empate com payout 90% e 52.6%).
            # Os outros continuam medidos (catalogo + estatisticas virtuais) mas NAO geram ordem.
            if sg['setup'].startswith('META_') and (os.environ.get('META_HORA_FILTER') or '1') == '1' and int(candle_ts // 60) % 60 in (59, 0):
                _throttled_log(st, f'_meta_hora_{asset}', f'[meta] {sg["setup"]} {sg["dir"]} em {asset}: virada de hora (OTC manipula) - nao entrei', every=300)
                continue
            if sg['setup'] not in SETUP_ALLOW:
                _throttled_log(st, f'_allow_{asset}_{sg["setup"]}', f'[v2] {sg["setup"]} {sg["dir"]} em {asset}: sinal medido mas FORA da lista de entrada (SETUP_ALLOW)', every=300)
                continue
            h, why, lb = _ENGINE.decide_ex(asset, sg['setup'], _allowed_h(asset))
            if h is not None:
                _bh, _bwhy = _catalog_best_h(username, asset)
                if _bh:
                    h = _bh[0]
                    why = f'expiracao do catalogo: {h} min ({_bh[1]}% em {_bh[2]} sinais, catalogo de {_bh[3] * 60:.0f} min atras)'
                else:
                    why = f'{why} | expiracao padrao {h} min ({_bwhy})'
            det = sg['det']
            info = f'{sg["setup"]} {sg["dir"]} em {asset} (RSI {det.get("rsi", "-")}, ER {det.get("er", "-")}' + (f', {det["tipo"]}' if det.get('tipo') else '') + ')'
            if h is None:
                _throttled_log(st, f'_skip_{asset}_{sg["setup"]}', f'[v2] {info} - NAO entrei: {why}', every=180)
                continue
            cands.append({'asset': asset, 'sg': sg, 'h': h, 'why': why, 'lb': lb, 'info': info,
                          'score': _ENGINE.asset_score(asset)})
            if not _stop[0] and _entrar(cands[-1]) == 'stop':
                _stop[0] = True                # sem vaga (MAX_SIMULTANEAS) ou sem tempo: segue lendo, mas nao entra mais
    if direct:
        st['_rr_direct'] = (cur + max(1, visited)) % len(pool)
    if not cands:
        if direct:
            _throttled_log(st, '_auto_idle', f'[auto] Procurando sinais em {len(pool)} ativos ({mode}), {len(watch)} por vela, {LIVE_CANDLES} velas cada - nenhum sinal agora (aviso a cada 5 min)', every=300)
        else:
            _throttled_log(st, '_auto_idle', f'[auto] Varredura {mode}: {len(booted)} ativos medidos, nenhum sinal valido agora (aviso a cada 30 min)', every=1800)
        return


# ---------------- (16/11) niveis-chave por ativo: velas H1, H4 e M5 da IQ ----------------
KL_REFRESH_SEC = int(os.environ.get('NIVEL_REFRESH_SEC') or 900)     # recalcula os niveis de cada ativo a cada 15 min

def _kl_levels_from_api(api, asset, timeout=10, cs=None):
    """Baixa H1 (120 velas = 5 dias), H4 (60 = 10 dias) e M5 (600 = ~2 dias) e monta os niveis. Retorna (niveis, erro)."""
    now = time.time()
    got = {}
    for tf, cnt in ((3600, 120), (14400, 60), (300, 600)):
        ok, res = _get_candles_safe(api, timeout, asset, tf, cnt, now, cs=cs) if cs is not None else _get_candles_safe(api, timeout, asset, tf, cnt, now)
        if not ok or not res:
            return None, f'velas {tf // 60} min: ' + (str(res)[:40] if not ok else 'vazio')
        got[tf] = res
        if cs is not None:
            _cat_sleep(cs, 1.0)
        else:
            time.sleep(0.4)
    return KL.build_levels(got[3600], got[14400], got[300]), ''

def _kl_resumo(lv):
    cnt = {}
    for x in lv or []:
        cnt[x['tag']] = cnt.get(x['tag'], 0) + 1
    return ', '.join(f'{k} {v}' for k, v in sorted(cnt.items())) or 'nenhum'

def _kl_refresh(api, st, username, uni):
    """Fora da janela de entrada: atualiza os niveis de UM ativo por vez (o mais desatualizado)."""
    sec = time.time() % 60
    if sec < 10 or sec > 40 or time.time() - st.get('_kl_last_try', 0) < 15:
        return
    kl = st.setdefault('_kl', {})
    stale = [a for a in uni if time.time() - kl.get(a, {}).get('ts', 0) > KL_REFRESH_SEC]
    if not stale:
        return
    asset = min(stale, key=lambda a: kl.get(a, {}).get('ts', 0))
    st['_kl_last_try'] = time.time()
    lv, err = _kl_levels_from_api(api, asset)
    if lv is None:
        kl[asset] = {'ts': time.time() - KL_REFRESH_SEC + 120, 'n': len(SV2.LEVELS.get(asset) or []), 'err': err}
        _throttled_log(st, f'_kl_err_{asset}', f'[niveis] {asset}: nao consegui ler as velas H1/H4/M5 ({err}) - tento de novo em 2 min', every=300)
        return
    SV2.LEVELS[asset] = lv
    kl[asset] = {'ts': time.time(), 'n': len(lv), 'err': ''}
    _add_log(st, f'[niveis] {asset}: {len(lv)} niveis-chave ativos ({_kl_resumo(lv)})')

def _auto_background(api, st, username, mode, uni):
    """Fora da janela de entrada: atualiza a lista de ativos, carrega historicos e mantem as medicoes em dia."""
    try:
        _kl_refresh(api, st, username, uni)   # (16/11) niveis-chave (poucos pedidos, um ativo por vez)
    except Exception as _e:
        _throttled_log(st, '_kl_exc', f'[niveis] erro ao atualizar niveis: {_e}', every=300)
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
                if time.time() - float(st.get('_reconnecting_ts') or 0) > 120:
                    _add_log(st, '[auto] Reconexao presa ha mais de 2 min - destravando o ciclo')
                    st['_reconnecting'] = False
                    st['_last_reconnect_ts'] = 0
                    continue
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
        try:
            import brokers as _BR
            broker = _BR.normalize(broker_raw)          # (25/11) IQ Option, Avalon, Bullex, Exnova, Polarium...
        except Exception:
            broker = 'IQ Option'
        
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
                ok=True, status='connected', broker=get_user_state(username).get('broker_name') or 'IQ Option',
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
        return jsonify(connected=connected, balance=bal, account_type=acct, broker=st.get('broker_name') or 'IQ Option'), 200
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
    for k in ('_asset_bad', '_global_fail', '_hv_fail', '_rc_fail', '_reconnecting', '_reconnecting_ts',
              '_asset_fail', '_recent_reads', '_boot', '_booted', '_boot_retry', '_dir_pause_until',
              '_last_good', '_last_good_ts', '_open_key', '_last_candle_key', '_loss_streak_all',
              '_refuse_n', '_focus', '_auto_key'):
        st.pop(k, None)
    _SUSPECT.clear()
    st['_last_reconnect_ts'] = 0
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

@app.route('/api/lock3', methods=['GET', 'POST'])
def lock3_toggle():
    u = current_user()
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        if 'enabled' in data:
            st['_lock3_enabled'] = bool(data.get('enabled'))
        else:
            st['_lock3_enabled'] = not st.get('_lock3_enabled', True)
        on = bool(st.get('_lock3_enabled', True))
        _add_log(st, '[trava] ' + ('ATIVADA: 3 losses seguidos param o bot (protecao da banca)' if on
                                   else 'DESATIVADA: o bot continua apos 3 losses (modo teste - use stop loss)'))
        return jsonify(ok=True, enabled=on)
    return jsonify(ok=True, enabled=bool(st.get('_lock3_enabled', True)))

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
        ss_run = _SENT.get(username)
        if ss_run and ss_run.get('running'):
            ss_run['cancel'] = True                      # iniciar o bot cancela o sentimento tambem
        ex_run = _EXPORT.get(username)
        if ex_run and ex_run.get('running'):
            ex_run['cancel'] = True                      # (18/11) iniciar o bot cancela a exportacao de velas
        if st.get('_reconnecting') and time.time() - float(st.get('_reconnecting_ts') or 0) > 90:
            st['_reconnecting'] = False                  # reconexao presa de um ciclo velho nao segura o novo
        m_asset = str(data.get('asset') or st.get('manual_asset') or st.get('selected_asset') or '').strip()
        # (12/11) modos: TOP3 (o bot escolhe os 3 melhores da ultima catalogacao) e MANUAL MULTI
        # (o usuario escolhe ate 3 ativos). Ambos novos; o MANUAL de 1 ativo segue como estava.
        run_mode = str(data.get('run_mode') or 'MANUAL').upper()
        st['_mode_top3'] = (run_mode == 'TOP3')
        assets_in = [str(a).strip() for a in (data.get('assets') or []) if str(a).strip()][:MAX_ASSETS]   # (16/11) nomes vem da lista: sem upper()
        # (25/11) so ativos que existem NA CORRETORA DESTE USUARIO (com corretoras diferentes, um ativo de outra
        # corretora - ex. TESLA-OP - nao tem id aqui: pedir velas dele derruba o socket a cada vela)
        try:
            _res = get_user_conn_state(username).get('result') or {}
            _known = set(_res.get('otc_assets') or []) | set(_res.get('binary_assets') or [])
            if _known:
                _fora = [a for a in assets_in if a not in _known]
                if data.get('asset') and str(data.get('asset')).strip() not in _known:
                    _fora.append(str(data.get('asset')).strip())
                if _fora:
                    assets_in = [a for a in assets_in if a in _known]
                    _add_log(st, f'[manual] Ignorei {", ".join(dict.fromkeys(_fora))}: nao existe(m) em {st.get("broker_name") or "IQ Option"} (lista de outra corretora?)')
                    if data.get('asset') and str(data.get('asset')).strip() not in _known:
                        data['asset'] = assets_in[0] if assets_in else ''
                        m_asset = data['asset']
                if m_asset and m_asset not in _known:
                    m_asset = ''
        except Exception:
            pass
        if st['_mode_top3']:
            _mk = str(data.get('auto_market') or 'OTC').upper()
            st['_auto_mkt'] = 'OPEN' if _mk == 'OPEN' else 'OTC'
            if st.get('_auto_mkt_last') != st['_auto_mkt']:
                st['_top3'] = []                         # trocou de mercado: escolhe a equipe de novo
                st['_top3_ts'] = 0
            st['_auto_mkt_last'] = st['_auto_mkt']
            st['manual_assets'] = []
            st['manual_asset'] = ''
            st['selected_asset'] = ''
            _add_log(st, f'[top3] Modo AUTOMATICO ligado ({"so MERCADO ABERTO" if st["_auto_mkt"] == "OPEN" else "so OTC"}): '
                         f'o bot escolhe ate {MAX_ASSETS} ativos pelo sentimento e roda todos')
        elif assets_in:
            st['manual_assets'] = assets_in
            st['manual_asset'] = assets_in[0]
            st['selected_asset'] = assets_in[0]
            _add_log(st, f'[manual] Operando {len(assets_in)} ativos: {", ".join(assets_in)}')
        else:
            if not m_asset or 'Carregando' in m_asset:
                return jsonify(ok=False, error='Escolha um ativo na lista.'), 200
            st['manual_assets'] = [m_asset]
            st['manual_asset'] = m_asset
            st['selected_asset'] = m_asset
        st['asset_mode'] = 'MANUAL'
        asset = data.get('asset') or st.get('selected_asset') or 'EURUSD'
        # (12/11) martingale configuravel no painel (niveis 1-3, fator x)
        st['mg_enabled'] = bool(data.get('mg_enabled'))
        st['mg_max'] = max(0, min(3, int(data.get('mg_max') or 0))) if st['mg_enabled'] else 0
        st['mg_factor'] = float(data.get('mg_factor') or 2.0)
        if st['mg_enabled'] and st['mg_max']:
            _add_log(st, f'[mg] Martingale ATIVO: ate {st["mg_max"]} niveis, fator x{st["mg_factor"]}')
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

@app.route('/api/chart', methods=['GET'])
def chart_route():
    """(21/11) Grafico ao vivo do painel: velas M1 que o bot JA leu + niveis-chave + LTA/LTB + padroes + entradas.
    Nao faz nenhum pedido a corretora (so le o que esta na memoria)."""
    st = get_user_state(current_user().get('sub', 'admin'))
    cache = st.get('_chart') or {}
    for _a in [a for a in cache if time.time() - cache[a]['ts'] > 3600]:
        cache.pop(_a, None)                           # (24/11) ativo que o bot parou de ler ha 1h sai da lista
    _cur = set(st.get('manual_assets') or []) | set(st.get('_top3') or [])
    assets = sorted(cache, key=lambda a: (a not in _cur, -cache[a]['ts']))   # ativos em operacao primeiro
    asset = request.args.get('asset') or ''
    follow = asset in ('', 'auto')
    if follow:                                     # modo AUTO: segue o ativo da entrada mais recente (ultimos 10 min)
        _tr = st.get('_chart_tr') or {}
        _rec = [(m['t'], a) for a, lst in _tr.items() if a in cache for m in lst if m['t'] >= time.time() - 660]
        asset = max(_rec)[1] if _rec else ''
    if asset not in cache:
        asset = st.get('_focus') if st.get('_focus') in cache else (assets[0] if assets else '')
    if not asset:
        return jsonify(ok=True, running=bool(st.get('running')), assets=[], asset='', candles=[]), 200
    cs = cache[asset]['c']
    lv = SV2.LEVELS.get(asset) if HAS_V2 else None
    try:
        an = CHL.analyze(cs, lv) if CHL else {'lines': [], 'patterns': [], 'levels': []}
    except Exception as e:
        an = {'lines': [], 'patterns': [], 'levels': [], 'err': str(e)[:80]}
    candles = [{'t': int(x['from']), 'o': float(x['open']), 'h': float(x['max']), 'l': float(x['min']), 'c': float(x['close'])}
               for x in sorted(cs, key=lambda x: int(x['from']))]
    t0 = candles[0]['t'] if candles else 0
    trades = [m for m in (st.get('_chart_tr') or {}).get(asset, []) if m['t'] >= t0]
    return jsonify(ok=True, running=bool(st.get('running')), assets=assets, asset=asset, follow=follow,
                   age=int(time.time() - cache[asset]['ts']), candles=candles, trades=trades, **an), 200

# ======================= (24/11) GRAFICO TIQUE A TIQUE (autorizado pelo usuario) =======================
# Assina o canal "vela ao vivo" (candle-generated) da IQ SO para o ativo que esta na tela do grafico. Nao mexe em
# login, reconexao, leitura de velas do bot nem ordens: e uma assinatura a mais no mesmo socket, e o bot segue lendo
# como antes. Cuidados: (1) so assina/troca FORA da janela de entrada (depois dos primeiros ENTRY_MAX_SECONDS da vela);
# (2) antes de assinar, garante na lib o id do ativo e o tamanho da fila (sem isso a mensagem da IQ quebraria o socket);
# (3) ninguem olhando o grafico por 30s = cancela a assinatura sozinho. Variavel TEMPO_REAL=0 no Railway desliga.
TEMPO_REAL = (os.environ.get('TEMPO_REAL') or '1').strip().lower() not in ('0', 'false', 'nao', 'off')
_TICK = {}
_TICK_LOCK = threading.Lock()
_TICK_WD = {'on': False}

def _tick_unsub(t):
    api = t.get('api_obj')
    try:
        _call_with_timeout(api.api.unsubscribe, 3, t['aid'], 60)
    except Exception:
        pass

def _tick_watchdog():
    while True:
        time.sleep(10)
        try:
            with _TICK_LOCK:
                for u, t in list(_TICK.items()):
                    st = get_user_state(u)
                    if time.time() - t.get('last_req', 0) > 30 or not st.get('running') or _IQ_APIS.get(u) is not t.get('api_obj'):
                        _TICK.pop(u, None)
                        if _IQ_APIS.get(u) is t.get('api_obj'):
                            threading.Thread(target=_tick_unsub, args=(t,), daemon=True).start()
        except Exception:
            pass

def _tick_aid(api, asset):
    """id do ativo SO pelo que ja esta em memoria (cache da leitura de velas / lista da sessao): nunca pergunta a IQ aqui."""
    with _ASSET_IDS_LOCK:
        hit = _ASSET_IDS.get((id(api), asset))
        m = _SESSION_ID_MAP.get(id(api)) or {}
    aid = hit[0] if hit and hit[0] is not None else m.get(str(asset).upper())
    return int(aid) if aid is not None else None

def _tick_sub(api, asset):
    _OP = IQLIB.mod(api, 'constants')
    aid = _tick_aid(api, asset)
    if aid is None:
        return None, 'id do ativo ainda nao carregado'
    vals = list(_OP.ACTIVES.values())
    if aid not in vals:
        _OP.ACTIVES[str(asset).upper()] = aid
        vals = list(_OP.ACTIVES.values())
    name = list(_OP.ACTIVES.keys())[vals.index(aid)]      # o MESMO nome que a lib vai achar quando a vela chegar
    inner = api.api
    inner.real_time_candles_maxdict_table[name][60] = 5   # fila da lib: sem isso a mensagem da IQ quebraria o socket
    inner.real_time_candles[name][60] = {}
    ok, res = _call_with_timeout(inner.subscribe, 3, aid, 60)
    if not ok:
        return None, 'assinatura sem resposta (' + str(res)[:30] + ')'
    return {'asset': asset, 'aid': aid, 'name': name, 'api_obj': api, 'sub_ts': time.time(), 'last_req': time.time()}, ''

@app.route('/api/chart/tick', methods=['GET'])
def chart_tick_route():
    username = current_user().get('sub', 'admin')
    st = get_user_state(username)
    asset = (request.args.get('asset') or '').strip()
    if not (TEMPO_REAL and CHART_ON):
        return jsonify(ok=True, live=False, why='tempo real desligado (TEMPO_REAL=0)'), 200
    api = _IQ_APIS.get(username)
    if not asset or api is None or not st.get('running') or st.get('_reconnecting'):
        return jsonify(ok=True, live=False, why='bot parado ou reconectando'), 200
    if not _TICK_WD['on']:
        _TICK_WD['on'] = True
        threading.Thread(target=_tick_watchdog, daemon=True, name='tick-watchdog').start()
    with _TICK_LOCK:
        t = _TICK.get(username)
        if t and t.get('api_obj') is not api:
            t = None                                       # reconectou: a assinatura antiga morreu junto
        if not t or t['asset'] != asset:
            if time.time() % 60 <= ENTRY_MAX_SECONDS + 1:
                return jsonify(ok=True, live=False, why='aguardando o fim da janela de entrada para ligar o ao vivo'), 200
            if t:
                threading.Thread(target=_tick_unsub, args=(t,), daemon=True).start()
                _TICK.pop(username, None)
            t, err = _tick_sub(api, asset)
            if not t:
                return jsonify(ok=True, live=False, why=err), 200
            _TICK[username] = t
        t['last_req'] = time.time()
    try:
        d = api.api.real_time_candles[t['name']][60]
        ks = list(d.keys())
        if not ks:
            return jsonify(ok=True, live=False, why='ligando o ao vivo...'), 200
        c = d[max(ks)]
        return jsonify(ok=True, live=True, asset=asset, t=int(c['from']), o=float(c['open']), h=float(c['max']),
                       l=float(c['min']), c=float(c['close']), at=c.get('at')), 200
    except Exception as e:
        return jsonify(ok=True, live=False, why=str(e)[:60]), 200

@app.route('/api/chart/events', methods=['GET'])
def chart_events_route():
    """(23/11) Ultimas entradas de todos os ativos (para a voz do painel narrar). So le a memoria."""
    st = get_user_state(current_user().get('sub', 'admin'))
    ev = [dict(m, asset=a) for a, lst in (st.get('_chart_tr') or {}).items() for m in lst]
    ev.sort(key=lambda m: m['t'])
    return jsonify(ok=True, running=bool(st.get('running')), events=ev[-15:]), 200

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
    username = (data.get('username', '') or '').strip()
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
    # (12/11) Master: primeira senha DEFINIDA no primeiro acesso (nada de senha fixa no codigo)
    if not user.password_hash:
        user.password_hash = hashlib.sha256(password.encode()).hexdigest()
        user.role = user.role or 'admin'
        db.session.commit()
    elif user.password_hash != hashlib.sha256(password.encode()).hexdigest():
        return jsonify({'error': 'Senha incorreta'}), 401
    if not user.is_active:
        return jsonify({'error': 'Usuario desativado pelo Master'}), 401

    token = jwt.encode({'username': username, 'sub': username, 'role': user.role},
                       app.config['SECRET_KEY'], algorithm='HS256')
    return jsonify(ok=True, token=token, username=username, role=user.role), 200

@app.route('/api/register', methods=['POST'])
def api_register():
    # (15/11) cadastro aberto deixava qualquer um criar conta: agora so o Master (o painel Master ja cria usuarios)
    if current_user().get('role') != 'admin':
        return jsonify({'error': 'Somente o Master cria usuarios'}), 403
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

# ============ (12/11) PAINEL MASTER: criar/excluir usuarios com chave aleatoria ============
def _require_master():
    u = current_user()
    if u.get('role') != 'admin':
        return None
    return u.get('sub', '')

def _random_key():
    return 'DAN-' + secrets.token_hex(4).upper()

@app.route('/api/master/stats')
def master_stats():
    if not _require_master():
        return jsonify({'error': 'Acesso restrito ao Master'}), 403
    users = User.query.all()
    return jsonify(ok=True, total=len(users), active=sum(1 for x in users if x.is_active),
                   masters=sum(1 for x in users if x.role == 'admin'))

@app.route('/api/master/users', methods=['GET', 'POST'])
def master_users():
    me = _require_master()
    if not me:
        return jsonify({'error': 'Acesso restrito ao Master'}), 403
    if request.method == 'GET':
        rows = [{'username': x.username, 'role': x.role, 'active': x.is_active,
                 'created': ((x.created_at - timedelta(hours=3)).strftime('%d/%m %H:%M') if x.created_at else ''),   # horario de Brasilia
                 'has_password': bool(x.password_hash)} for x in User.query.order_by(User.created_at.desc())]
        return jsonify(ok=True, rows=rows, me=me)
    data = request.get_json(silent=True) or {}
    nu = (data.get('username', '') or '').strip()
    if not nu or ' ' in nu:
        return jsonify(ok=False, error='Nome de usuario invalido'), 200
    if User.query.filter_by(username=nu).first():
        return jsonify(ok=False, error='Usuario ja existe'), 200
    key = _random_key()
    db.session.add(User(username=nu, password_hash=hashlib.sha256(key.encode()).hexdigest(), role='user'))
    db.session.commit()
    _add_log(get_user_state(me), f'[master] Usuario criado: {nu} (chave de acesso gerada)')
    return jsonify(ok=True, username=nu, access_key=key), 200

@app.route('/api/master/users/<username>/delete', methods=['POST'])
def master_user_delete(username):
    me = _require_master()
    if not me:
        return jsonify({'error': 'Acesso restrito ao Master'}), 403
    if username == me:
        return jsonify(ok=False, error='O Master nao pode excluir a si mesmo'), 200
    usr = User.query.filter_by(username=username).first()
    if not usr:
        return jsonify(ok=False, error='Usuario nao encontrado'), 200
    if usr.role == 'admin':
        return jsonify(ok=False, error='Nao e possivel excluir outro Master'), 200
    # isola os dados do usuario excluido (estado/conexao somem com ele)
    st = get_user_state(username)
    st['running'] = False
    st['bot_running'] = False
    _IQ_APIS.pop(username, None)
    _USER_STATES.pop(username, None)
    db.session.delete(usr)
    db.session.commit()
    _add_log(get_user_state(me), f'[master] Usuario EXCLUIDO: {username} (area dele isolada e apagada)')
    return jsonify(ok=True), 200

@app.route('/api/master/users/<username>/toggle', methods=['POST'])
def master_user_toggle(username):
    me = _require_master()
    if not me:
        return jsonify({'error': 'Acesso restrito ao Master'}), 403
    usr = User.query.filter_by(username=username).first()
    if not usr or usr.role == 'admin':
        return jsonify(ok=False, error='Alvo invalido'), 200
    usr.is_active = not usr.is_active
    if not usr.is_active:
        st = get_user_state(username)
        st['running'] = False
        st['bot_running'] = False
    db.session.commit()
    return jsonify(ok=True, active=usr.is_active), 200

@app.route('/api/master/licenses', methods=['GET'])
def master_licenses():
    if not _require_master():
        return jsonify({'error': 'Acesso restrito ao Master'}), 403
    return jsonify(ok=True, rows=[], note='licencas ligadas aos usuarios (futuro)')

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.pop('token', None)
    return jsonify(ok=True), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)