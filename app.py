
"""
DANBOT v2.4 - AUTO-CONTIDO - NAO DEPENDE DE iq_integration.py
Conecta REAL/DEMO e puxa saldo REAL direto
"""
from flask import Flask, render_template, request, jsonify, session, has_request_context
from flask_sqlalchemy import SQLAlchemy
import hashlib, datetime, os, jwt, threading, time, json, random, re
from datetime import timezone, timedelta as _timedelta

BRT = timezone(_timedelta(hours=-3))
def _brt_str():
    return (datetime.datetime.utcnow() - _timedelta(hours=3)).strftime('%H:%M:%S')

import sys
sys.path.insert(0, os.path.dirname(__file__))

# Try import iq_integration for signals, but not required for connect
try:
    import iq_integration as IQ
    HAS_IQ_MODULE = True
except:
    HAS_IQ_MODULE = False
    class IQ:
        pass

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'danbot-default-secret-key-2025-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////data/danbot.db' if os.path.exists('/data') else 'sqlite:///danbot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
MASTER_SECRET = os.environ.get('MASTER_SECRET', 'DANBOT-MASTER-2025')

@app.route('/health')
@app.route('/healthcheck')
def health():
    return jsonify({'status':'ok','has_iq':HAS_IQ_MODULE,'mode':'HYBRID_REAL_DEMO'}),200

@app.route('/')
def root():
    try: return render_template('dashboard.html')
    except: return jsonify({'status':'ok'}),200

class User(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    username=db.Column(db.String(80),unique=True,nullable=False)
    password_hash=db.Column(db.String(256),nullable=False)
    role=db.Column(db.String(20),default='user')
    is_active=db.Column(db.Boolean,default=True)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow)
class LicenseKey(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    key=db.Column(db.String(256),unique=True,nullable=False)
    username=db.Column(db.String(80),nullable=False)
    expires_at=db.Column(db.DateTime,nullable=True)
    is_active=db.Column(db.Boolean,default=True)
class TradeLog(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    username=db.Column(db.String(80))
    asset=db.Column(db.String(50))
    direction=db.Column(db.String(10))
    amount=db.Column(db.Float)
    result=db.Column(db.String(10))
    profit=db.Column(db.Float)
    timestamp=db.Column(db.DateTime,default=datetime.datetime.utcnow)

with app.app_context():
    try: os.makedirs('/data',exist_ok=True)
    except: pass
    db.create_all()

_GLOBAL_STATE_LOCK=threading.Lock()
_USER_STATES={}
_USER_CONN_STATES={}
_USER_CONN_LOCKS={}
_IQ_APIS={}

BROKER_HOSTS={'IQ Option':'iqoption.com','Bullex':'bullex.com','Exnova':'exnova.com','IQ_Option':'iqoption.com'}

def _default_user_state():
    return {'running':False,'bot_running':False,'broker_connected':False,'broker_name':'IQ Option','broker':'IQ Option','broker_email':None,'broker_password':None,'broker_account_type':'PRACTICE','account_type':'PRACTICE','broker_balance':0.0,'selected_asset':'EURUSD','entry_value':2.0,'wins':0,'losses':0,'profit':0.0,'win_rate':0,'log':[],'_resync_failures':0,'_last_live_ok_ts':0.0,'_last_balance_refresh_ts':0.0,'last_signal':None}

def get_user_state(username):
    with _GLOBAL_STATE_LOCK:
        if username not in _USER_STATES:
            _USER_STATES[username]=_default_user_state()
    return _USER_STATES[username]

def get_user_conn_state(username):
    with _GLOBAL_STATE_LOCK:
        if username not in _USER_CONN_STATES:
            _USER_CONN_STATES[username]={'status':'idle','result':None,'error':None,'ts':0.0}
    return _USER_CONN_STATES[username]

def get_user_conn_lock(username):
    with _GLOBAL_STATE_LOCK:
        if username not in _USER_CONN_LOCKS:
            _USER_CONN_LOCKS[username]=threading.Lock()
    return _USER_CONN_LOCKS[username]

def _set_conn_state(username,status=None,result=None,error=None):
    lock=get_user_conn_lock(username)
    st=get_user_conn_state(username)
    with lock:
        if status is not None: st['status']=status
        if result is not None or status=='connected': st['result']=result
        if error is not None or status=='connected': st['error']=error
        st['ts']=time.time()
    return st

def current_user():
    token=session.get('token') or request.headers.get('Authorization','').replace('Bearer ','')
    if not token: return None
    try: return jwt.decode(token,app.config['SECRET_KEY'],algorithms=['HS256'])
    except: return None

# ─── CONEXÃO REAL DIRETO AQUI DENTRO (NAO USA iq_integration.py) ───
def connect_iq_real(email,password,account_type='PRACTICE',host='iqoption.com',username='guest',broker_name='IQ Option'):
    try:
        from iqoptionapi.stable_api import IQ_Option
    except Exception as e:
        return False, f'iqoptionapi nao instalada no Railway - adicione no requirements.txt: {e}'
    try:
        if username in _IQ_APIS:
            try: _IQ_APIS[username].close()
            except: pass
            del _IQ_APIS[username]
        api=IQ_Option(email,password)
        check,reason=api.connect()
        if not check:
            return False, f'Falha IQ: {reason} - verifique email/senha e desative 2FA'
        if account_type.upper() in ['PRACTICE','DEMO']:
            api.change_balance('PRACTICE')
        else:
            api.change_balance('REAL')
        time.sleep(1)
        balance=api.get_balance() or 0
        all_assets=[]
        binary_assets=[]
        otc_assets=[]
        try:
            open_time=api.get_all_open_time()
            for mtype in ['binary','turbo','digital']:
                if mtype in open_time:
                    for asset_name,info in open_time[mtype].items():
                        if info.get('open',False):
                            if asset_name not in all_assets: all_assets.append(asset_name)
                            if 'OTC' not in asset_name and asset_name not in binary_assets: binary_assets.append(asset_name)
        except Exception as e:
            print(f"[IQ] erro open_time: {e}")
        default_binary=['EURUSD','GBPUSD','USDJPY','AUDUSD','EURJPY','GBPJPY','EURGBP','USDCHF','NZDUSD','USDCAD','BTCUSD','ETHUSD']
        default_otc=['EURUSD-OTC','GBPUSD-OTC','USDJPY-OTC','AUDUSD-OTC','EURJPY-OTC','GBPJPY-OTC']
        if not binary_assets: binary_assets=default_binary
        if not all_assets: all_assets=binary_assets+default_otc
        otc_assets=[a for a in all_assets if 'OTC' in a] or default_otc
        binary_open=[a for a in all_assets if 'OTC' not in a]
        _IQ_APIS[username]=api
        result={'balance':float(balance),'account_type':account_type.upper(),'otc_assets':otc_assets,'binary_assets':binary_open,'all_assets':all_assets,'open_time':{}}
        print(f"[IQ REAL] Conectado saldo {balance} | Binarias: {len(binary_open)}")
        return True,result
    except Exception as e:
        import traceback; traceback.print_exc()
        return False,f'Erro IQ: {str(e)[:200]}'

def _kick_background_reconnect(username,broker=None,email=None,password=None,account_type=None,host=None,reason='manual'):
    st=get_user_state(username)
    broker=broker or st.get('broker_name') or 'IQ Option'
    email=(email or st.get('broker_email') or '').strip()
    password=password or st.get('broker_password') or ''
    account_type=(account_type or st.get('broker_account_type') or 'PRACTICE').upper()
    host=host or BROKER_HOSTS.get(broker,'iqoption.com')
    if not email or not password: return False,'missing_credentials'
    lock=get_user_conn_lock(username)
    conn_st=get_user_conn_state(username)
    with lock:
        if conn_st.get('status')=='connecting' and (time.time()-float(conn_st.get('ts') or 0))<180:
            return False,'already_connecting'
        conn_st['status']='connecting'; conn_st['result']=None; conn_st['error']=None; conn_st['ts']=time.time()
    def _do():
        try:
            ok,result=connect_iq_real(email,password,account_type,host=host,username=username,broker_name=broker)
            st_local=get_user_state(username)
            if ok:
                st_local['broker_connected']=True; st_local['broker_name']=broker; st_local['broker']=broker
                st_local['broker_email']=email; st_local['broker_password']=password
                st_local['broker_account_type']=result.get('account_type',account_type)
                st_local['account_type']=result.get('account_type',account_type)
                st_local['broker_balance']=result.get('balance',0)
                _set_conn_state(username,status='connected',result=result,error=None)
            else:
                st_local['broker_connected']=False
                _set_conn_state(username,status='error',result=None,error=result)
        except Exception as exc:
            _set_conn_state(username,status='error',result=None,error=str(exc))
    threading.Thread(target=_do,daemon=True,name=f'reconnect-{username}').start()
    return True,'connecting'

@app.route('/api/broker/connect',methods=['POST'])
def broker_connect():
    u=current_user()
    if not u: return jsonify({'error':'nao autorizado'}),401
    username=u.get('sub','admin')
    data=request.get_json() or {}
    broker_raw=data.get('broker','IQ Option')
    # LIMPA ENTITY TAG
    broker='IQ Option'
    if 'Bullex' in broker_raw: broker='Bullex'
    elif 'Exnova' in broker_raw: broker='Exnova'
    else: broker='IQ Option'
    email=data.get('email','').strip()
    password=data.get('password','')
    account_type=data.get('account_type','PRACTICE').upper()
    if not email or not password: return jsonify(ok=False,error='Informe email e senha'),200
    if '@' not in email: return jsonify(ok=False,error='Email invalido'),200
    host=BROKER_HOSTS.get(broker,'iqoption.com')
    st=get_user_state(username)
    st['broker_name']=broker; st['broker']=broker; st['broker_email']=email; st['broker_password']=password; st['broker_account_type']=account_type; st['account_type']=account_type
    launched,_msg=_kick_background_reconnect(username,broker=broker,email=email,password=password,account_type=account_type,host=host,reason='broker_connect')
    if not launched and _msg=='already_connecting':
        return jsonify(ok=True,status='connecting',message='Conexao ja em andamento...'),200
    return jsonify(ok=True,status='connecting',message=f'Conectando {broker} {account_type}...'),200

@app.route('/api/broker/connect/poll',methods=['GET'])
def broker_poll():
    u=current_user()
    if not u: return jsonify({'error':'nao autorizado'}),401
    username=u.get('sub','admin')
    lock=get_user_conn_lock(username)
    conn_st=get_user_conn_state(username)
    with lock:
        status=conn_st['status']; result=conn_st['result']; error=conn_st['error']; elapsed=time.time()-(conn_st['ts'] or time.time())
    if status=='connected' and result:
        bal=result.get('balance',0)
        return jsonify(ok=True,status='connected',broker='IQ Option',account_type=result.get('account_type','PRACTICE'),balance=f"{bal:,.2f}",balance_raw=float(bal),otc_assets=result.get('otc_assets',[]),binary_assets=result.get('binary_assets',[]),all_assets=result.get('all_assets',[])),200
    elif status=='error':
        return jsonify(ok=False,status='error',error=error or 'Falha conexao'),200
    elif status=='connecting':
        if elapsed>150:
            with lock: conn_st['status']='error'; conn_st['error']='Timeout 150s'
            return jsonify(ok=False,status='error',error='Timeout corretora nao respondeu - desative 2FA'),200
        return jsonify(ok=True,status='connecting',message='Conectando...',elapsed=int(elapsed)),200
    else:
        return jsonify(ok=True,status='idle'),200

@app.route('/api/broker/status',methods=['GET'])
def broker_status():
    u=current_user()
    if not u: return jsonify({'error':'nao autorizado'}),401
    username=u.get('sub','admin')
    st=get_user_state(username)
    # Atualiza saldo REAL se conectado
    try:
        if st.get('broker_connected') and username in _IQ_APIS:
            bal=_IQ_APIS[username].get_balance()
            if bal is not None: st['broker_balance']=float(bal)
    except: pass
    return jsonify(connected=st.get('broker_connected',False),broker=st.get('broker_name'),account_type=st.get('broker_account_type','PRACTICE'),balance=st.get('broker_balance',0)),200

@app.route('/api/broker/disconnect',methods=['POST'])
@app.route('/api/ui/disconnect',methods=['POST'])
def broker_disconnect():
    u=current_user()
    if not u: return jsonify({'error':'nao autorizado'}),401
    username=u.get('sub','admin')
    st=get_user_state(username)
    st['broker_connected']=False
    if username in _IQ_APIS:
        try: _IQ_APIS[username].close()
        except: pass
        del _IQ_APIS[username]
    _set_conn_state(username,status='idle',result=None,error=None)
    return jsonify(ok=True),200

@app.route('/api/bot/status',methods=['GET'])
def bot_status():
    u=current_user()
    if not u: return jsonify({'error':'nao autorizado'}),401
    username=u.get('sub','admin')
    st=get_user_state(username)
    return jsonify(running=st.get('running',False),asset=st.get('selected_asset','EURUSD'),last_signal=st.get('last_signal'),mode='HYBRID_REAL_DEMO')

@app.route('/api/bot/start',methods=['POST'])
def bot_start():
    u=current_user()
    if not u: return jsonify({'error':'nao autorizado'}),401
    username=u.get('sub','admin')
    st=get_user_state(username)
    data=request.get_json(silent=True) or {}
    asset=data.get('asset','EURUSD')
    st['running']=True; st['selected_asset']=asset
    return jsonify({'ok':True,'asset':asset}),200

@app.route('/api/bot/stop',methods=['POST'])
def bot_stop():
    u=current_user()
    if not u: return jsonify({'error':'nao autorizado'}),401
    username=u.get('sub','admin')
    st=get_user_state(username)
    st['running']=False
    return jsonify({'ok':True}),200

@app.route('/api/bot/placar',methods=['GET'])
def bot_placar():
    u=current_user()
    if not u: return jsonify({'error':'nao autorizado'}),401
    username=u.get('sub','admin')
    st=get_user_state(username)
    wins=st.get('wins',0); losses=st.get('losses',0); total=wins+losses; wr=(wins/total*100) if total>0 else 0
    return jsonify(wins=wins,losses=losses,total=total,win_rate=wr,profit=st.get('profit',0),balance=st.get('broker_balance',0),account_type=st.get('broker_account_type','PRACTICE')),200

@app.route('/api/bot/assets',methods=['GET'])
def bot_assets():
    u=current_user()
    if not u: return jsonify({'error':'nao autorizado'}),401
    username=u.get('sub','admin')
    st=get_user_state(username)
    conn_st=get_user_conn_state(username)
    result=conn_st.get('result') or {}
    binary=result.get('binary_assets') or ['EURUSD','GBPUSD','USDJPY','AUDUSD','EURJPY','GBPJPY','EURGBP','USDCHF','NZDUSD','USDCAD','BTCUSD','ETHUSD']
    otc=result.get('otc_assets') or ['EURUSD-OTC','GBPUSD-OTC','USDJPY-OTC','AUDUSD-OTC','EURJPY-OTC','GBPJPY-OTC']
    all_assets=binary+otc
    return jsonify(assets=all_assets,binary=binary,otc=otc,all=all_assets,selected=st.get('selected_asset','EURUSD')),200

@app.route('/api/scan',methods=['GET'])
def scan():
    u=current_user()
    if not u: return jsonify({'error':'nao autorizado'}),401
    username=u.get('sub','admin')
    asset=request.args.get('asset','EURUSD')
    try:
        if HAS_IQ_MODULE and hasattr(IQ,'check_hybrid_signal'):
            try:
                closes,ohlc=IQ.get_candles_iq(_IQ_APIS.get(username),asset,count=50,timeframe=60) if username in _IQ_APIS else IQ.generate_synthetic_candles(asset,50)
                sinal,debug=IQ.check_hybrid_signal(ohlc['closes'],ohlc['volumes'])
            except:
                sinal,debug=None,{}
        else:
            sinal,debug=None,{}
        return jsonify(asset=asset,sinal=sinal,debug=debug),200
    except Exception as e:
        return jsonify(asset=asset,sinal=None,error=str(e)),200

@app.route('/api/login',methods=['POST'])
def api_login():
    data=request.get_json(silent=True) or {}
    username=data.get('username','').strip(); password=data.get('password','')
    if not username or not password: return jsonify({'error':'Informe usuario e senha'}),401
    user=User.query.filter_by(username=username).first()
    if not user:
        if User.query.count()==0:
            ph=hashlib.sha256(password.encode()).hexdigest()
            new_user=User(username=username,password_hash=ph,role='admin')
            db.session.add(new_user); db.session.commit(); user=new_user
        else: return jsonify({'error':'Usuario nao encontrado'}),401
    ph=hashlib.sha256(password.encode()).hexdigest()
    token=jwt.encode({'username':username,'sub':username,'role':user.role},app.config['SECRET_KEY'],algorithm='HS256')
    return jsonify({'ok':True,'token':token,'username':username}),200

@app.route('/api/master/login',methods=['POST'])
def master_login():
    data=request.get_json(silent=True) or {}
    secret=data.get('secret') or data.get('password') or data.get('master_secret')
    if secret==MASTER_SECRET:
        token=jwt.encode({'username':'master','sub':'master','role':'master'},app.config['SECRET_KEY'],algorithm='HS256')
        return jsonify({'ok':True,'token':token})
    return jsonify({'error':'master secret invalido'}),401

@app.route('/api/catalogador/patterns',methods=['GET'])
@app.route('/api/catalog/patterns',methods=['GET'])
@app.route('/api/patterns',methods=['GET'])
@app.route('/api/bot/patterns',methods=['GET'])
def catalog_fix():
    return jsonify({'ok':True,'candles':[{'slug':'HYB_EQ_FX','label':'HYBRID','accuracy':100}],'cores':[{'slug':'HYB_EQ_FX','label':'HYBRID','accuracy':100}],'mode':'HYBRID_ONLY'}),200

@app.route('/api/bot/config',methods=['GET','POST'])
def bot_config():
    return jsonify({'ok':True,'mode':'HYBRID_REAL_DEMO'}),200

if __name__=='__main__':
    port=int(os.environ.get('PORT','8080'))
    print(f"[DANBOT v2.4] AUTO-CONTIDO REAL/DEMO em 0.0.0.0:{port}")
    app.run(host='0.0.0.0',port=port,debug=False,threaded=True)
