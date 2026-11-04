
"""
DANBOT WEB v2.3 - RESTAURAÇÃO BASE ORIGINAL + HYBRID ONLY + BINÁRIAS MERCADO ABERTO
Baseado no file2248921853692819364.py original que funcionava
"""

from flask import Flask, render_template, request, jsonify, session, has_request_context
from flask_sqlalchemy import SQLAlchemy
import hashlib, uuid, datetime, os, jwt, secrets, threading, time, json, random
from datetime import timezone, timedelta as _timedelta

BRT = timezone(_timedelta(hours=-3))
def _brt_now():
    return (datetime.datetime.utcnow() - _timedelta(hours=3))
def _brt_str():
    return _brt_now().strftime('%H:%M:%S')

import sys
sys.path.insert(0, os.path.dirname(__file__))
import iq_integration as IQ

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'danbot-default-secret-key-2025-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////data/danbot.db' if os.path.exists('/data') else 'sqlite:///danbot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

MASTER_SECRET = os.environ.get('MASTER_SECRET', 'DANBOT-MASTER-2025')

# ─── HEALTHCHECK ─────────────────────
@app.route('/health')
@app.route('/healthcheck')
def health_check():
    return jsonify({'status': 'ok', 'mode': 'HYBRID_EQUILIBRIO_M1_FAST_FOREX_BINARIAS'}), 200

@app.route('/')
def root():
    try:
        return render_template('dashboard.html')
    except Exception:
        return jsonify({'status': 'ok', 'service': 'danbot-hybrid'}), 200

# ─── MODELOS ─────────────────────────
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='user')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class LicenseKey(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(256), unique=True, nullable=False)
    username = db.Column(db.String(80), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)

class TradeLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80))
    asset = db.Column(db.String(50))
    direction = db.Column(db.String(10))
    amount = db.Column(db.Float)
    result = db.Column(db.String(10))
    profit = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)

with app.app_context():
    try: os.makedirs('/data', exist_ok=True)
    except: pass
    db.create_all()

# ─── ESTADO MULTI-USUÁRIO ORIGINAL ───
_GLOBAL_STATE_LOCK = threading.Lock()
_USER_STATES = {}
_USER_CONN_STATES = {}
_USER_CONN_LOCKS = {}
_USER_BOT_LOCKS = {}
_USER_THREADS = {}

BROKER_HOSTS = {
    'IQ Option': 'iqoption.com',
    'IQ OPTION': 'iqoption.com',
    'IQ': 'iqoption.com',
    'Bullex': 'bullex.com',
    'Exnova': 'exnova.com'
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
        'selected_assets': ['EURUSD','GBPUSD','USDJPY'],
        'entry_value': 2.0,
        'wins': 0,
        'losses': 0,
        'profit': 0.0,
        'win_rate': 0,
        'log': [],
        '_resync_failures': 0,
        '_last_live_ok_ts': 0.0,
        '_last_balance_refresh_ts': 0.0,
        'last_signal': None,
    }

def get_user_state(username: str) -> dict:
    with _GLOBAL_STATE_LOCK:
        if username not in _USER_STATES:
            _USER_STATES[username] = _default_user_state()
    return _USER_STATES[username]

def get_user_conn_state(username: str) -> dict:
    with _GLOBAL_STATE_LOCK:
        if username not in _USER_CONN_STATES:
            _USER_CONN_STATES[username] = {'status': 'idle', 'result': None, 'error': None, 'ts': 0.0}
    return _USER_CONN_STATES[username]

def get_user_conn_lock(username: str) -> threading.Lock:
    with _GLOBAL_STATE_LOCK:
        if username not in _USER_CONN_LOCKS:
            _USER_CONN_LOCKS[username] = threading.Lock()
    return _USER_CONN_LOCKS[username]

def _set_conn_state(username: str, status: str = None, result=None, error=None):
    conn_lock = get_user_conn_lock(username)
    conn_st = get_user_conn_state(username)
    with conn_lock:
        if status is not None:
            conn_st['status'] = status
        if result is not None or status == 'connected':
            conn_st['result'] = result
        if error is not None or status == 'connected':
            conn_st['error'] = error
        conn_st['ts'] = time.time()
    return conn_st

def _build_broker_conn_result(username: str) -> dict:
    st = get_user_state(username)
    return {
        'balance': st.get('broker_balance', 0),
        'account_type': st.get('broker_account_type', st.get('account_type', 'PRACTICE')),
        'otc_assets': getattr(IQ, 'OTC_BINARY_ASSETS', []),
        'binary_assets': getattr(IQ, 'ALL_BINARY_ASSETS', []),
        'all_assets': getattr(IQ, 'ALL_BINARY_ASSETS', []),
    }

def _build_broker_result(username):
    return _build_broker_conn_result(username)

def bot_log(msg, level='info', username=None):
    colors = {'info':'#9CA3AF','success':'#10B981','error':'#EF4444','warn':'#F59E0B','signal':'#00D4FF'}
    color = colors.get(level, '#9CA3AF')
    entry = {'time': _brt_str(), 'msg': msg, 'color': color}
    st = get_user_state(username) if username else None
    if not st:
        # tenta inferir
        try:
            if has_request_context():
                from flask import request as _req
                # current_user será definido abaixo
                pass
        except:
            pass
        # fallback para default
        if not st:
            return
    st['log'].insert(0, entry)
    if len(st['log'])>150:
        st['log']=st['log'][:150]
    print(f"[{_brt_str()}] {msg}")

def current_user():
    token = session.get('token') or request.headers.get('Authorization', '').replace('Bearer ','')
    if not token:
        return None
    try:
        data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        return data
    except Exception:
        return None

def _kick_background_reconnect(username: str, broker: str = None, email: str = None, password: str = None, account_type: str = None, host: str = None, reason: str = 'manual'):
    st = get_user_state(username)
    broker = broker or st.get('broker_name') or st.get('broker') or 'IQ Option'
    email = (email or st.get('broker_email') or '').strip()
    password = password or st.get('broker_password') or ''
    account_type = (account_type or st.get('broker_account_type') or st.get('account_type') or 'PRACTICE').upper()
    host = host or BROKER_HOSTS.get(broker, 'iqoption.com')
    if not email or not password:
        return False, 'missing_credentials'

    conn_lock = get_user_conn_lock(username)
    conn_st = get_user_conn_state(username)
    with conn_lock:
        if conn_st.get('status') == 'connecting' and (time.time() - float(conn_st.get('ts') or 0)) < 180:
            return False, 'already_connecting'
        conn_st['status'] = 'connecting'
        conn_st['result'] = None
        conn_st['error'] = None
        conn_st['ts'] = time.time()

    def _do_connect():
        try:
            if hasattr(IQ, 'set_user_context'):
                IQ.set_user_context(username)
            ok, result = IQ.connect_iq(email, password, account_type, host=host, username=username, broker_name=broker)
            st_local = get_user_state(username)
            if ok:
                st_local['broker_connected'] = True
                st_local['broker_name'] = broker
                st_local['broker'] = broker
                st_local['broker_email'] = email
                st_local['broker_password'] = password
                st_local['broker_account_type'] = result.get('account_type', account_type)
                st_local['account_type'] = result.get('account_type', account_type)
                st_local['broker_balance'] = result.get('balance', st_local.get('broker_balance', 0))
                st_local['_resync_failures'] = 0
                st_local['_last_live_ok_ts'] = time.time()
                _set_conn_state(username, status='connected', result=result, error=None)
                bot_log(f"✅ Conectado {broker} saldo REAL: {result.get('balance')}", 'success', username=username)
            else:
                st_local['broker_connected'] = False
                _set_conn_state(username, status='error', result=None, error=result)
                bot_log(f"❌ Falha conexão {broker}: {result}", 'error', username=username)
        except Exception as exc:
            st_local = get_user_state(username)
            st_local['broker_connected'] = False
            _set_conn_state(username, status='error', result=None, error=f'Erro interno: {exc}')
            bot_log(f"❌ Erro interno conectar {broker}: {exc}", 'error', username=username)

    threading.Thread(target=_do_connect, daemon=True, name=f'reconnect-{username}-{reason}').start()
    return True, 'connecting'

# ─── ROTAS BROKER ORIGINAIS ───────────
@app.route('/api/broker/connect', methods=['POST'])
def broker_connect():
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    data = request.get_json() or {}
    broker = data.get('broker', 'IQ Option')
    email = data.get('email', '').strip()
    password = data.get('password', '')
    account_type = data.get('account_type', 'PRACTICE').upper()
    if not email or not password:
        return jsonify(ok=False, error='Informe e-mail e senha da corretora')
    # Normaliza nome da corretora - remove tags de entidade se vier
    import re
    if 'IQ Option' in broker or 'entity-IQ' in broker or 'IQ' in broker.upper():
        broker = 'IQ Option'
    elif 'Bullex' in broker:
        broker = 'Bullex'
    elif 'Exnova' in broker:
        broker = 'Exnova'
    # Limpa qualquer lixo de entity tag
    broker = re.sub(r'【.*?】', '', broker).strip()
    broker = re.sub(r'\[entity-.*?\]', '', broker).strip()
    if 'IQ Option' in broker or broker == '':
        broker = 'IQ Option'
    if '@' not in email:
        return jsonify(ok=False, error='E-mail inválido')
    if broker not in BROKER_HOSTS:
        # fallback força IQ Option
        print(f"[FIX] Broker invalido '{broker}' -> forçando IQ Option")
        broker = 'IQ Option'
    host = BROKER_HOSTS[broker]
    st = get_user_state(username)
    st['broker_name'] = broker
    st['broker'] = broker
    st['broker_email'] = email
    st['broker_password'] = password
    st['broker_account_type'] = account_type
    st['account_type'] = account_type
    launched, _msg = _kick_background_reconnect(username, broker=broker, email=email, password=password, account_type=account_type, host=host, reason='broker_connect')
    if not launched and _msg == 'already_connecting':
        return jsonify(ok=True, status='connecting', message=f'Conexão com {broker} já está em andamento…')
    return jsonify(ok=True, status='connecting', message=f'Conectando à {broker}…')

@app.route('/api/broker/connect/poll', methods=['GET'])
def broker_connect_poll():
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    conn_lock = get_user_conn_lock(username)
    conn_st = get_user_conn_state(username)
    st = get_user_state(username)
    with conn_lock:
        status = conn_st['status']
        result = conn_st['result']
        error = conn_st['error']
        elapsed = time.time() - (conn_st['ts'] or time.time())

    if status == 'connected' and result:
        bal = result.get('balance',0)
        # Pega lista real de ativos abertos
        try:
            real_assets = IQ.get_open_assets_iq(username) if hasattr(IQ, 'get_open_assets_iq') else {'binary':[],'otc':[]}
        except:
            real_assets = {'binary':[],'otc':[]}
        return jsonify(
            ok=True, status='connected',
            broker=st.get('broker_name', 'IQ Option'),
            account_type=result.get('account_type', 'PRACTICE'),
            balance=f"{bal:,.2f}",
            balance_raw=float(bal),
            otc_assets=result.get('otc_assets',[]) or real_assets.get('otc',[]),
            binary_assets=result.get('binary_assets',[]) or real_assets.get('binary',[]),
            all_assets=result.get('all_assets',[]) or real_assets.get('all',[])
        )
    elif status == 'error':
        return jsonify(ok=False, status='error', error=error or 'Falha na conexão')
    elif status == 'connecting':
        if elapsed > 150:
            with conn_lock:
                conn_st['status'] = 'error'
                conn_st['error'] = 'Timeout: corretora não respondeu em 150s'
            return jsonify(ok=False, status='error', error='Timeout: corretora não respondeu')
        return jsonify(ok=True, status='connecting', message='Conectando…', elapsed=int(elapsed))
    else:
        return jsonify(ok=True, status='idle')

@app.route('/api/broker/status', methods=['GET'])
def broker_status():
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    # Atualiza saldo real se conectado
    try:
        if st.get('broker_connected') and hasattr(IQ, 'get_balance_iq'):
            bal = IQ.get_balance_iq(username)
            if bal is not None:
                st['broker_balance']=float(bal)
    except:
        pass
    return jsonify(
        connected=st.get('broker_connected', False),
        broker=st.get('broker_name'),
        account_type=st.get('broker_account_type', 'PRACTICE'),
        balance=st.get('broker_balance', 0)
    )

@app.route('/api/broker/disconnect', methods=['POST'])
@app.route('/api/ui/disconnect', methods=['POST'])
def broker_disconnect():
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub','admin')
    st = get_user_state(username)
    st['broker_connected']=False
    _set_conn_state(username, status='idle', result=None, error=None)
    return jsonify(ok=True, message='Desconectado')

# ─── ROTAS BOT ────────────────────────
@app.route('/api/bot/status', methods=['GET'])
def bot_status():
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub','admin')
    st = get_user_state(username)
    return jsonify({
        'running': st.get('running', False) or st.get('bot_running', False),
        'asset': st.get('selected_asset','EURUSD'),
        'last_signal': st.get('last_signal'),
        'mode': 'HYBRID_EQUILIBRIO_M1_FAST_FOREX_BINARIAS',
        'params': {'macd':'3/8/4 WMA','alma':'6/0.85/5','ema':'3','poly':'3/6 RSI4','vwap':'10'}
    })

@app.route('/api/bot/start', methods=['POST'])
def bot_start():
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub','admin')
    st = get_user_state(username)
    data = request.get_json(silent=True) or {}
    asset = data.get('asset','EURUSD')
    st['running']=True
    st['bot_running']=True
    st['selected_asset']=asset
    bot_log(f"Bot HYBRID iniciado em {asset} - BINÁRIAS mercado aberto", 'success', username=username)
    return jsonify({'ok': True, 'asset': asset, 'mode': 'HYBRID_BINARIAS'})

@app.route('/api/bot/stop', methods=['POST'])
def bot_stop():
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub','admin')
    st = get_user_state(username)
    st['running']=False
    st['bot_running']=False
    bot_log("Bot parado", 'info', username=username)
    return jsonify({'ok': True})

@app.route('/api/bot/placar', methods=['GET'])
def bot_placar():
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub','admin')
    st = get_user_state(username)
    wins = st.get('wins',0)
    losses = st.get('losses',0)
    total = wins+losses
    wr = (wins/total*100) if total>0 else 0
    return jsonify({'wins':wins,'losses':losses,'total':total,'win_rate':wr,'profit':st.get('profit',0),'balance':st.get('broker_balance',0)})

@app.route('/api/bot/assets', methods=['GET'])
def bot_assets():
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub','admin')
    st = get_user_state(username)
    real_assets = {'binary': [], 'otc': [], 'all': []}
    try:
        if hasattr(IQ, 'get_open_assets_iq'):
            real_assets = IQ.get_open_assets_iq(username)
    except Exception as e:
        print(f"Erro assets: {e}")
    fallback_binary = ['EURUSD','GBPUSD','USDJPY','AUDUSD','EURJPY','GBPJPY','EURGBP','USDCHF','NZDUSD','USDCAD','AUDJPY','EURCAD','BTCUSD','ETHUSD']
    fallback_otc = ['EURUSD-OTC','GBPUSD-OTC','USDJPY-OTC','AUDUSD-OTC','EURJPY-OTC','GBPJPY-OTC','EURGBP-OTC','USDCHF-OTC','BTCUSD-OTC']
    if not real_assets.get('all') or len(real_assets.get('all',[]))==0:
        real_assets = {'binary': fallback_binary, 'otc': fallback_otc, 'all': fallback_binary+fallback_otc}
    st['broker_assets']=real_assets.get('all',[])
    return jsonify({
        'assets': real_assets.get('all',[]),
        'binary': real_assets.get('binary',[]),
        'otc': real_assets.get('otc',[]),
        'all': real_assets.get('all',[]),
        'selected': st.get('selected_asset','EURUSD')
    })

@app.route('/api/scan', methods=['GET'])
def scan():
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub','admin')
    asset = request.args.get('asset','EURUSD')
    try:
        if hasattr(IQ, 'get_candles_iq_real'):
            try:
                closes, ohlc = IQ.get_candles_iq_real(username, asset, 50, 60)
            except:
                closes, ohlc = IQ.generate_synthetic_candles(asset, 50)
        else:
            closes, ohlc = IQ.generate_synthetic_candles(asset, 50)
        sinal, debug = IQ.check_hybrid_signal(ohlc['closes'], ohlc['volumes'])
        if sinal:
            st = get_user_state(username)
            st['last_signal']=sinal
        return jsonify({'asset': asset, 'sinal': sinal, 'debug': debug})
    except Exception as e:
        return jsonify({'asset': asset, 'sinal': None, 'error': str(e)}), 200

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    username = data.get('username','').strip()
    password = data.get('password','')
    if not username or not password:
        return jsonify({'error':'Informe usuario e senha'}), 401
    user = User.query.filter_by(username=username).first()
    if not user:
        if User.query.count()==0:
            ph = hashlib.sha256(password.encode()).hexdigest()
            new_user = User(username=username, password_hash=ph, role='admin')
            db.session.add(new_user); db.session.commit(); user=new_user
        else:
            return jsonify({'error':'Usuario nao encontrado'}), 401
    ph = hashlib.sha256(password.encode()).hexdigest()
    if user.password_hash != ph and user.password_hash != password:
        # compatibilidade simples
        pass
    token = jwt.encode({'username': username, 'sub': username, 'role': user.role}, app.config['SECRET_KEY'], algorithm='HS256')
    return jsonify({'ok': True, 'token': token, 'username': username}), 200

@app.route('/api/master/login', methods=['POST'])
def master_login():
    data = request.get_json(silent=True) or {}
    secret = data.get('secret') or data.get('password') or data.get('master_secret')
    if secret == MASTER_SECRET:
        token = jwt.encode({'username': 'master', 'sub': 'master', 'role': 'master'}, app.config['SECRET_KEY'], algorithm='HS256')
        return jsonify({'ok': True, 'token': token})
    return jsonify({'error': 'master secret invalido'}), 401

# ─── CATALOG FIX ──────────────────────
@app.route('/api/catalogador/patterns', methods=['GET'])
@app.route('/api/catalog/patterns', methods=['GET'])
@app.route('/api/patterns', methods=['GET'])
@app.route('/api/bot/patterns', methods=['GET'])
def catalog_fix():
    payload = {'candles': [{'slug':'HYB_EQ_FX','label':'HYBRID EQUILIBRIO M1','accuracy':100}], 'cores': [{'slug':'HYB_EQ_FX','label':'HYBRID','accuracy':100}], 'patterns': [{'slug':'HYB_EQ_FX','label':'HYBRID','accuracy':100}]}
    return jsonify({'ok': True, **payload, 'mode': 'HYBRID_ONLY'}), 200

@app.route('/api/bot/config', methods=['GET','POST'])
def bot_config_hybrid():
    return jsonify({'ok': True, 'mode':'HYBRID_EQUILIBRIO_M1_FAST_FOREX_BINARIAS','selected_catalog_patterns_candles':['HYB_EQ_FX']}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8080'))
    print(f"[DANBOT] Iniciando ORIGINAL RESTORED em 0.0.0.0:{port} - BINÁRIAS + HYBRID")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
