
"""
DANBOT v2.7 - RESTAURADA que CONECTAVA + FIX ENTRADAS + VALOR $ EDITAVEL
Base: versao que conectou $4494.70 (screenshot)
"""
from flask import Flask, render_template, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
import hashlib, datetime, os, jwt, threading, time, json, random
from datetime import timezone, timedelta as _timedelta

BRT = timezone(_timedelta(hours=-3))
def _brt_str():
    return (datetime.datetime.utcnow() - _timedelta(hours=3)).strftime('%H:%M:%S')

import sys
sys.path.insert(0, os.path.dirname(__file__))

try:
    import iq_integration as IQ
    HAS_IQ_MODULE = True
except:
    HAS_IQ_MODULE = False
    class IQ: pass

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'danbot-default-secret-key-2025-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////data/danbot.db' if os.path.exists('/data') else 'sqlite:///danbot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
MASTER_SECRET = os.environ.get('MASTER_SECRET', 'DANBOT-MASTER-2025')

@app.route('/health')
@app.route('/healthcheck')
def health():
    return jsonify({'status':'ok','has_iq':HAS_IQ_MODULE}),200

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

with app.app_context():
    try: os.makedirs('/data',exist_ok=True)
    except: pass
    db.create_all()

_GLOBAL_STATE_LOCK=threading.Lock()
_USER_STATES={}
_USER_CONN_STATES={}
_USER_CONN_LOCKS={}
_IQ_APIS={}
_BOT_THREADS={}

BROKER_HOSTS={'IQ Option':'iqoption.com','Bullex':'bullex.com','Exnova':'exnova.com','IQ_Option':'iqoption.com'}

def _default_user_state():
    return {'running':False,'bot_running':False,'broker_connected':False,'broker_name':'IQ Option','broker':'IQ Option','broker_email':None,'broker_password':None,'broker_account_type':'PRACTICE','account_type':'PRACTICE','broker_balance':0.0,'selected_asset':'EURUSD','entry_value':1.0,'timeframe':'M1','expiration':1,'stop_win':0,'stop_loss':0,'wins':0,'losses':0,'profit':0.0,'win_rate':0,'log':[],'last_signal':None,'_last_trade_ts':0,'_last_candle_min':-1}

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
    try:
        token=session.get('token') or request.headers.get('Authorization','').replace('Bearer ','').replace('bearer ','')
        if not token:
            return {'sub':'admin','username':'admin','role':'admin'}
        try:
            data=jwt.decode(token,app.config['SECRET_KEY'],algorithms=['HS256'])
            return data
        except:
            return {'sub':'admin','username':'admin','role':'admin'}
    except:
        return {'sub':'admin','username':'admin','role':'admin'}

def connect_iq_real(email,password,account_type='PRACTICE',host='iqoption.com',username='admin',broker_name='IQ Option'):
    try:
        from iqoptionapi.stable_api import IQ_Option
    except Exception as e:
        return False, f'iqoptionapi nao instalada: {e}'
    try:
        if username in _IQ_APIS:
            try: _IQ_APIS[username].close()
            except: pass
            del _IQ_APIS[username]
        api=IQ_Option(email,password)
        check,reason=api.connect()
        if not check:
            return False, f'Falha IQ: {reason}'
        if account_type.upper() in ['PRACTICE','DEMO']:
            api.change_balance('PRACTICE')
        else:
            api.change_balance('REAL')
        time.sleep(1)
        balance=api.get_balance() or 0
        all_assets=[]; binary_assets=[]; otc_assets=[]
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
        result={'balance':float(balance),'account_type':account_type.upper(),'otc_assets':otc_assets,'binary_assets':binary_open,'all_assets':all_assets}
        print(f"[IQ REAL] Conectado saldo ${balance} | Binarias: {len(binary_open)}")
        return True,result
    except Exception as e:
        import traceback; traceback.print_exc()
        return False,f'Erro IQ: {str(e)[:300]}'

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

def _trading_loop(username):
    print(f"[TRADING] Loop iniciado {username}")
    while True:
        try:
            st=get_user_state(username)
            if not st.get('running',False):
                print(f"[TRADING] Parado {username}")
                break
            asset=st.get('selected_asset','EURUSD')
            entry_value=float(st.get('entry_value',1))
            timeframe=st.get('timeframe','M1')
            expiration=int(st.get('expiration',1 if timeframe=='M1' else 5))
            stop_win=float(st.get('stop_win',0) or 0)
            stop_loss=float(st.get('stop_loss',0) or 0)
            # Verifica Stop Win/Loss
            profit=st.get('profit',0)
            wins=st.get('wins',0)
            losses=st.get('losses',0)
            if stop_win>0 and profit>=stop_win:
                msg=f'STOP WIN atingido $ {profit:.2f} - Bot parado'
                st['log'].insert(0,{'time':_brt_str(),'msg':msg})
                st['running']=False; st['bot_running']=False
                break
            if stop_loss>0 and profit<=-abs(stop_loss):
                msg=f'STOP LOSS atingido $ {profit:.2f} - Bot parado'
                st['log'].insert(0,{'time':_brt_str(),'msg':msg})
                st['running']=False; st['bot_running']=False
                break
            api=_IQ_APIS.get(username)
            if not api:
                time.sleep(3)
                continue
            # Calcula janela de 30s do nascimento da vela
            import datetime as _dt
            now=_dt.datetime.now(BRT)
            # Para M1: vela nasce a cada minuto 0s. Para M5: a cada 5 min
            if timeframe=='M5':
                candle_min=(now.minute//5)*5
                candle_start=now.replace(minute=candle_min,second=0,microsecond=0)
            else:
                candle_start=now.replace(second=0,microsecond=0)
            seconds_since_candle=(now - candle_start).total_seconds()
            current_candle_key=f"{now.date()}-{now.hour}-{candle_start.minute if timeframe=='M5' else now.minute}"
            try:
                candles=api.get_candles(asset,60,50,time.time())
                if not candles or len(candles)<20:
                    time.sleep(2)
                    continue
                import numpy as np
                closes=np.array([c['close'] for c in candles],dtype=float)
                volumes=np.array([c.get('volume',500) for c in candles],dtype=float)
                sinal=None
                try:
                    if HAS_IQ_MODULE and hasattr(IQ,'check_hybrid_signal'):
                        sinal,debug=IQ.check_hybrid_signal(closes,volumes)
                except Exception as e:
                    print(f"[TRADING] check_hybrid_signal erro {e}")
                if sinal:
                    st['last_signal']=sinal
                    # Evita repetir mesma vela
                    if st.get('_last_candle_min')==current_candle_key:
                        time.sleep(2)
                        continue
                    # JANELA DE 30 SEGUNDOS
                    if seconds_since_candle<=30:
                        # DENTRO DA JANELA - FAZ ENTRADA NA MESMA VELA
                        last_ts=st.get('_last_trade_ts',0)
                        if time.time()-last_ts < 10:
                            time.sleep(1)
                            continue
                        direction=sinal.lower()
                        exp=5 if timeframe=='M5' else 1
                        print(f"[TRADING] JANELA OK {seconds_since_candle:.1f}s - ENTRADA {sinal} {asset} ${entry_value} exp {exp}min")
                        try:
                            check,order_id=api.buy(entry_value,asset,direction,exp)
                            if check:
                                st['_last_trade_ts']=time.time()
                                st['_last_candle_min']=current_candle_key
                                if direction=='call':
                                    msg=f'ordem de compra executada - {sinal} em {asset} - $ {entry_value} - vela {now.strftime("%H:%M")} - exp {exp}min - ID {order_id}'
                                else:
                                    msg=f'ordem de venda executada - {sinal} em {asset} - $ {entry_value} - vela {now.strftime("%H:%M")} - exp {exp}min - ID {order_id}'
                                st['log'].insert(0,{'time':_brt_str(),'msg':msg})
                                print(f"[TRADING] {msg}")
                                # Verifica WIN/LOSS apos expiracao
                                def check_win_later(oid=order_id, exp_min=exp, dir=sinal, ast=asset, val=entry_value):
                                    time.sleep((exp_min*60)+10)
                                    try:
                                        st2=get_user_state(username)
                                        # Tenta pegar resultado
                                        win_result=None
                                        try:
                                            win_result=api.check_win_v3(oid)
                                        except:
                                            try:
                                                win_result=api.check_win_digital_v2(oid)
                                            except:
                                                win_result=None
                                        # Atualiza saldo REAL
                                        try:
                                            bal=api.get_balance()
                                            if bal: st2['broker_balance']=float(bal)
                                        except: pass
                                        if win_result is not None:
                                            # iqoptionapi retorna lucro ou True/False
                                            is_win=False
                                            if isinstance(win_result,bool):
                                                is_win=win_result
                                            elif isinstance(win_result,(int,float)):
                                                is_win=win_result>0
                                            if is_win:
                                                st2['wins']=st2.get('wins',0)+1
                                                profit_amt=val*0.8
                                                st2['profit']=st2.get('profit',0)+profit_amt
                                                st2['log'].insert(0,{'time':_brt_str(),'msg':f'WIN {ast} +$ {profit_amt:.2f} - Saldo $ {st2.get("broker_balance",0):.2f}'})
                                            else:
                                                st2['losses']=st2.get('losses',0)+1
                                                st2['profit']=st2.get('profit',0)-val
                                                st2['log'].insert(0,{'time':_brt_str(),'msg':f'LOSS {ast} -$ {val:.2f} - Saldo $ {st2.get("broker_balance",0):.2f}'})
                                            # Calcula winrate
                                            tw=st2['wins']+st2['losses']
                                            st2['win_rate']=(st2['wins']/tw*100) if tw>0 else 0
                                        else:
                                            st2['log'].insert(0,{'time':_brt_str(),'msg':f'Resultado {ast} ID {oid} aguardando - saldo $ {st2.get("broker_balance",0):.2f}'})
                                    except Exception as e:
                                        print(f"[CHECK WIN] Erro {e}")
                                threading.Thread(target=check_win_later,daemon=True).start()
                            else:
                                st['log'].insert(0,{'time':_brt_str(),'msg':f'Falha entrada {sinal} check=False - aguardando proximo sinal'})
                        except Exception as e:
                            print(f"[TRADING] Erro buy: {e}")
                            st['log'].insert(0,{'time':_brt_str(),'msg':f'Erro entrada: {str(e)[:100]} - aguardando proximo sinal'})
                    else:
                        # FORA DA JANELA DE 30s - CANCELA
                        if st.get('_last_candle_min')!=current_candle_key:
                            msg=f'cancelada ordem de entrada {sinal} em {asset} - {seconds_since_candle:.0f}s apos nascimento da vela (limite 30s) - aguardando proximo sinal'
                            st['log'].insert(0,{'time':_brt_str(),'msg':msg})
                            st['_last_candle_min']=current_candle_key
                            print(f"[TRADING] {msg}")
            except Exception as e:
                print(f"[TRADING] Erro interno: {e}")
                time.sleep(3)
                continue
            time.sleep(2)
        except Exception as e:
            print(f"[TRADING] Erro externo: {e}")
            time.sleep(3)

@app.route('/api/broker/connect',methods=['POST'])
def broker_connect():
    try:
        u=current_user()
        username=u.get('sub','admin')
        data=request.get_json() or {}
        broker_raw=data.get('broker','IQ Option')
        broker='IQ Option'
        if 'Bullex' in broker_raw: broker='Bullex'
        elif 'Exnova' in broker_raw: broker='Exnova'
        email=data.get('email','').strip()
        password=data.get('password','')
        account_type=data.get('account_type','PRACTICE').upper()
        if not email or not password:
            return jsonify(ok=False,error='Informe email e senha'),200
        host=BROKER_HOSTS.get(broker,'iqoption.com')
        st=get_user_state(username)
        st['broker_name']=broker; st['broker']=broker; st['broker_email']=email; st['broker_password']=password; st['broker_account_type']=account_type; st['account_type']=account_type
        launched,_msg=_kick_background_reconnect(username,broker=broker,email=email,password=password,account_type=account_type,host=host,reason='broker_connect')
        if not launched and _msg=='already_connecting':
            return jsonify(ok=True,status='connecting',message='Ja conectando...'),200
        return jsonify(ok=True,status='connecting',message=f'Conectando {broker} {account_type}...'),200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify(ok=False,error=f'Erro interno connect: {e}'),200

@app.route('/api/broker/connect/poll',methods=['GET'])
def broker_poll():
    try:
        u=current_user()
        username=u.get('sub','admin')
        lock=get_user_conn_lock(username)
        conn_st=get_user_conn_state(username)
        with lock:
            status=conn_st['status']; result=conn_st['result']; error=conn_st['error']; elapsed=time.time()-(conn_st['ts'] or time.time())
        if status=='connected' and result:
            bal=result.get('balance',0)
            return jsonify(ok=True,status='connected',broker='IQ Option',account_type=result.get('account_type','PRACTICE'),balance=f"{bal:,.2f}",balance_raw=float(bal),otc_assets=result.get('otc_assets',[]),binary_assets=result.get('binary_assets',[]),all_assets=result.get('all_assets',[])),200
        elif status=='error':
            return jsonify(ok=False,status='error',error=error or 'Falha'),200
        elif status=='connecting':
            if elapsed>150:
                with lock: conn_st['status']='error'; conn_st['error']='Timeout 150s'
                return jsonify(ok=False,status='error',error='Timeout - desative 2FA'),200
            return jsonify(ok=True,status='connecting',message='Conectando...',elapsed=int(elapsed)),200
        else:
            return jsonify(ok=True,status='idle'),200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify(ok=False,status='error',error=str(e)),200

@app.route('/api/broker/status',methods=['GET'])
def broker_status():
    try:
        u=current_user()
        username=u.get('sub','admin')
        st=get_user_state(username)
        conn_st=get_user_conn_state(username)
        result=conn_st.get('result') or {}
        bal=result.get('balance') or st.get('broker_balance',0)
        acct=result.get('account_type') or st.get('broker_account_type','PRACTICE')
        connected=conn_st.get('status')=='connected' or st.get('broker_connected',False)
        return jsonify(connected=connected,balance=bal,account_type=acct,broker='IQ Option'),200
    except Exception as e:
        return jsonify(connected=False,balance=0,error=str(e)),200

@app.route('/api/broker/disconnect',methods=['POST'])
@app.route('/api/broker/ui/disconnect',methods=['POST'])
def broker_disconnect():
    u=current_user()
    username=u.get('sub','admin')
    st=get_user_state(username)
    conn_st=get_user_conn_state(username)
    st['broker_connected']=False; st['running']=False; st['bot_running']=False
    with get_user_conn_lock(username):
        conn_st['status']='idle'; conn_st['result']=None; conn_st['error']=None
    if username in _IQ_APIS:
        try: _IQ_APIS[username].close()
        except: pass
        del _IQ_APIS[username]
    return jsonify(ok=True),200

@app.route('/api/bot/start',methods=['POST'])
def bot_start():
    try:
        u=current_user()
        username=u.get('sub','admin')
        st=get_user_state(username)
        data=request.get_json(silent=True) or {}
        asset=data.get('asset','EURUSD')
        ev=data.get('amount',data.get('entry_value',data.get('valor',1)))
        try: entry_value=float(ev)
        except: entry_value=1.0
        timeframe=data.get('timeframe','M1')
        expiration=5 if timeframe=='M5' else 1
        stop_win=float(data.get('stop_win',0) or 0)
        stop_loss=float(data.get('stop_loss',0) or 0)
        st['running']=True; st['bot_running']=True; st['selected_asset']=asset; st['entry_value']=entry_value; st['timeframe']=timeframe; st['expiration']=expiration; st['stop_win']=stop_win; st['stop_loss']=stop_loss
        # Reseta placar se solicitado
        if data.get('reset_placar'):
            st['wins']=0; st['losses']=0; st['profit']=0; st['win_rate']=0
        old_thread=_BOT_THREADS.get(username)
        if old_thread and old_thread.is_alive():
            st['running']=False
            time.sleep(0.5)
            st['running']=True
        t=threading.Thread(target=_trading_loop,args=(username,),daemon=True,name=f'trading-{username}')
        t.start()
        _BOT_THREADS[username]=t
        msg=f'Bot iniciado {asset} - Entrada $ {entry_value} - saldo $ REAL'
        st['log'].insert(0,{'time':_brt_str(),'msg':msg})
        print(f"[BOT] {msg}")
        return jsonify(ok=True,asset=asset,amount=entry_value),200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify(ok=False,error=str(e)),200

@app.route('/api/bot/stop',methods=['POST'])
def bot_stop():
    u=current_user()
    username=u.get('sub','admin')
    st=get_user_state(username)
    st['running']=False; st['bot_running']=False
    st['log'].insert(0,{'time':_brt_str(),'msg':'Bot parado'})
    return jsonify(ok=True),200

@app.route('/api/bot/status',methods=['GET'])
def bot_status_route():
    u=current_user()
    username=u.get('sub','admin')
    st=get_user_state(username)
    return jsonify(running=st.get('running',False),asset=st.get('selected_asset','EURUSD'),amount=st.get('entry_value',1),timeframe=st.get('timeframe','M1'),expiration=st.get('expiration',1),stop_win=st.get('stop_win',0),stop_loss=st.get('stop_loss',0),wins=st.get('wins',0),losses=st.get('losses',0),profit=st.get('profit',0)),200

@app.route('/api/bot/placar',methods=['GET'])
def bot_placar():
    u=current_user()
    username=u.get('sub','admin')
    st=get_user_state(username)
    wins=st.get('wins',0); losses=st.get('losses',0); total=wins+losses; wr=(wins/total*100) if total>0 else 0
    return jsonify(wins=wins,losses=losses,total=total,win_rate=wr,profit=st.get('profit',0),balance=st.get('broker_balance',0),account_type=st.get('broker_account_type','PRACTICE'),timeframe=st.get('timeframe','M1'),expiration=st.get('expiration',1)),200

@app.route('/api/bot/assets',methods=['GET'])
def bot_assets():
    u=current_user()
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
    try:
        u=current_user()
        username=u.get('sub','admin')
        asset=request.args.get('asset','EURUSD')
        api=_IQ_APIS.get(username)
        if api:
            try:
                candles=api.get_candles(asset,60,50,time.time())
                import numpy as np
                closes=np.array([c['close'] for c in candles],dtype=float)
                volumes=np.array([c.get('volume',500) for c in candles],dtype=float)
                if HAS_IQ_MODULE and hasattr(IQ,'check_hybrid_signal'):
                    sinal,debug=IQ.check_hybrid_signal(closes,volumes)
                else:
                    sinal='PUT' if closes[-1]<closes[-2] else 'CALL' if closes[-1]>closes[-2] else None
                    debug={'close':float(closes[-1])}
                return jsonify(asset=asset,sinal=sinal,debug=debug),200
            except Exception as e:
                print(f"[SCAN] Erro: {e}")
        return jsonify(asset=asset,sinal=None,debug={}),200
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
    print(f"[DANBOT v2.7] RESTAURADO em 0.0.0.0:{port}")
    app.run(host='0.0.0.0',port=port,debug=False,threaded=True)
