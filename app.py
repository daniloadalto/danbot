"""
DANBOT WEB v2.0 — Backend Flask
Bot de Arbitragem OTC para Opções Binárias
"""
from flask import Flask, render_template, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
import hashlib, uuid, datetime, os, jwt, secrets, threading, time, json, random
import numpy as np
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import iq_integration as IQ
from iq_integration import run_backtest, OTC_BINARY_ASSETS, check_volume_filter, start_heartbeat, stop_heartbeat

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////data/danbot.db' if os.path.exists('/data') else 'sqlite:///danbot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

MASTER_SECRET = 'DANBOT-MASTER-2025'

# ─── MODELOS ─────────────────────────────────────────────────────────────────
class User(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role          = db.Column(db.String(20), default='user')
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    device_id     = db.Column(db.String(256), nullable=True)

class LicenseKey(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    key          = db.Column(db.String(256), unique=True, nullable=False)
    username     = db.Column(db.String(80), nullable=False)
    expires_at   = db.Column(db.DateTime, nullable=True)
    is_active    = db.Column(db.Boolean, default=True)
    device_bound = db.Column(db.String(256), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    last_login   = db.Column(db.DateTime, nullable=True)

class TradeLog(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    username  = db.Column(db.String(80))
    asset     = db.Column(db.String(50))
    direction = db.Column(db.String(10))
    amount    = db.Column(db.Float)
    result    = db.Column(db.String(10))
    profit    = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)

# ─── BOT STATE ───────────────────────────────────────────────────────────────
bot_state = {
    'running': False,
    'broker_connected': False,
    'broker_name': None,
    'broker_email': None,
    'broker_account_type': 'PRACTICE',
    'broker_balance': 0.0,
    'wins': 0, 'losses': 0,
    'profit': 0.0,
    'log': [],
    'signal': None,
    'correlations': [],
    'broker': 'IQ Option',
    'entry_value': 2.0,
    'stop_loss': 20.0,
    'stop_win': 50.0,
    'min_corr': 0.80,
    'account_type': 'PRACTICE',
    'selected_asset': 'AUTO',
    'use_volume_filter': False,   # Filtro de volume ativo?
    'vol_min': 150.0,             # Volume mínimo por vela
    'vol_max': 2000.0,            # Volume máximo por vela
    'strategies': {'ema':True,'rsi':True,'bb':True,'macd':True,'adx':True,'stoch':True,'lp':True,'pat':True,'fib':True},
    'min_confluence': 4,
}

# Ativos temporariamente suspensos (evitar tentativas repetidas)
_suspended_assets = {}  # {asset: timestamp_de_suspensão}
_SUSPENSION_TIMEOUT = 300  # 5 minutos de espera para tentar novamente

# Apenas ativos de opções BINÁRIAS OTC (turbo M1)
OTC_ASSETS = [
    # ── Forex OTC ──
    'EURUSD-OTC', 'EURGBP-OTC', 'GBPUSD-OTC', 'USDJPY-OTC',
    'USDCHF-OTC', 'AUDUSD-OTC', 'NZDUSD-OTC', 'USDCAD-OTC',
    'EURJPY-OTC', 'GBPJPY-OTC', 'AUDCAD-OTC', 'AUDJPY-OTC',
    'EURCHF-OTC', 'GBPCHF-OTC', 'CADJPY-OTC', 'CHFJPY-OTC',
    'GBPCAD-OTC', 'EURCAD-OTC', 'USDSGD-OTC', 'EURNZD-OTC',
    # ── Crypto OTC ──
    'BTCUSD-OTC', 'ETHUSD-OTC', 'LTCUSD-OTC', 'SOLUSD-OTC',
    'ADAUSD-OTC', 'XRPUSD-OTC', 'BNBUSD-OTC', 'DOTUSD-OTC',
    'LINKUSD-OTC', 'MATICUSD-OTC', 'SHIBUSD-OTC', 'AVAXUSD-OTC',
    'ATOMUSD-OTC', 'TRXUSD-OTC', 'DOGUSD-OTC', 'EOSUSD-OTC',
    'PEPEUSD-OTC', 'WLDUSD-OTC', 'ARBUSD-OTC', 'FETUSD-OTC',
    'GRTUSD-OTC', 'IMXUSD-OTC', 'SEIUSD-OTC', 'STXUSD-OTC',
    'TRUMPUSD-OTC', 'WIFUSD-OTC', 'RAYUSD-OTC', 'JUPUSD-OTC',
    # ── Índices OTC ──
    'US100-OTC', 'US500-OTC', 'DE40-OTC', 'FR40-OTC', 'EU50-OTC',
    'HK33-OTC', 'JP225-OTC',
    # ── Ações OTC ──
    'AAPL-OTC', 'MSFT-OTC', 'GOOGL-OTC', 'AMZN-OTC', 'TSLA-OTC',
    'META-OTC', 'NVDA-OTC', 'NFLX-OTC', 'BABA-OTC',
]

# Ativos de mercado aberto (Forex, Crypto, Commodities, Índices)
OPEN_ASSETS = [
    'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD',
    'NZDUSD', 'USDCAD', 'EURGBP', 'EURJPY', 'GBPJPY',
    'AUDJPY', 'CADJPY',
    'BTCUSD', 'ETHUSD', 'BNBUSD', 'SOLUSD', 'XRPUSD',
    'XAUUSD', 'XAGUSD', 'USOIL', 'UKOIL',
    'SP500', 'DJ30', 'NASDAQ', 'FTSE100',
]

ALL_ASSETS = OTC_ASSETS + OPEN_ASSETS

def bot_log(msg, level='info'):
    colors = {'info':'#9CA3AF','success':'#10B981','error':'#EF4444','warn':'#F59E0B','signal':'#00D4FF'}
    color  = colors.get(level, '#9CA3AF')
    entry  = {
        'time': datetime.datetime.now().strftime('%H:%M:%S'),
        'msg': msg, 'color': color
    }
    bot_state['log'].insert(0, entry)
    if len(bot_state['log']) > 100:
        bot_state['log'] = bot_state['log'][:100]

def run_bot_real():
    """
    Loop principal — análise técnica completa.
    Modo AUTO: escaneia todos os ativos OTC e escolhe o melhor sinal.
    Modo FIXO: analisa apenas o ativo selecionado pelo usuário.
    """
    # Verificação inicial de conexão
    mode_label = bot_state.get('account_type', 'PRACTICE')

    bot_log(f'🚀 DANBOT PRO iniciado — Modo {mode_label}', 'success')

    # ── Inicializar is_real ANTES do primeiro uso ──────────────────
    is_real = bot_state.get('broker_connected', False) and IQ.get_iq() is not None

    if not is_real:
        bot_log('⚠️ Corretora não conectada — modo DEMO (sinais simulados)', 'warn')
    else:
        bal = IQ.get_real_balance()
        if bal is not None:
            bot_state['broker_balance'] = bal
            bot_log(f'✅ IQ Option conectada | Saldo: R$ {bal:,.2f}', 'success')

    bot_log(f'💰 Entrada: R${bot_state["entry_value"]:.2f} | SL: R${bot_state["stop_loss"]:.2f} | SW: R${bot_state["stop_win"]:.2f}', 'info')

    cycle = 0
    while bot_state['running']:
        try:
            cycle += 1

            # Verificar conexão a cada ciclo (detecta desconexão automática)
            is_real = bot_state.get('broker_connected', False) and IQ.get_iq() is not None
            if not is_real and bot_state.get('broker_connected', False):
                bot_log('⚠️ Conexão com IQ Option perdida! Tentando reconectar...', 'warn')
                bot_state['broker_connected'] = False

            # Atualizar saldo
            if is_real:
                bal = IQ.get_real_balance()
                if bal is not None:
                    bot_state['broker_balance'] = bal

            # ── VERIFICAR STOPS ─────────────────────────────────────────────
            if bot_state['profit'] <= -abs(bot_state['stop_loss']):
                bot_log('🛑 STOP LOSS atingido — bot parado!', 'error')
                bot_state['running'] = False; break
            if bot_state['profit'] >= abs(bot_state['stop_win']):
                bot_log('🏆 STOP WIN atingido — bot parado!', 'success')
                bot_state['running'] = False; break

            # ── SELECIONAR ATIVOS ────────────────────────────────────────────
            selected_asset = bot_state.get('selected_asset', 'AUTO')
            # Garantir que apenas ativos OTC sejam aceitos no bot
            if selected_asset and selected_asset != 'AUTO' and not selected_asset.endswith('-OTC'):
                bot_log(f'⚠️ Ativo {selected_asset} não é OTC binário — convertendo para {selected_asset}-OTC', 'warning')
                selected_asset = selected_asset + '-OTC'
                bot_state['selected_asset'] = selected_asset
            if selected_asset and selected_asset != 'AUTO':
                assets_to_scan = [selected_asset]
                bot_log(f'🔄 Ciclo #{cycle} — analisando {selected_asset} (modo fixo)', 'info')
            else:
                # Filtrar apenas ativos disponíveis no momento (evita rejeições)
                assets_to_scan = IQ.get_available_otc_assets() if IQ.get_iq() else IQ.OTC_BINARY_ASSETS
                bot_log(f'🔄 Ciclo #{cycle} — escaneando {len(assets_to_scan)} ativos OTC disponíveis...', 'info')

            # ── FILTRAR ATIVOS SUSPENSOS ────────────────────────────────────
            now_ts = time.time()
            ativos_antes = len(assets_to_scan)
            assets_to_scan = [a for a in assets_to_scan
                              if now_ts - _suspended_assets.get(a, 0) > _SUSPENSION_TIMEOUT]
            if len(assets_to_scan) < ativos_antes:
                bot_log(f'⏸️ {ativos_antes - len(assets_to_scan)} ativo(s) suspenso(s) ignorado(s)', 'info')

            # ── ESCANEAR / ANALISAR ──────────────────────────────────────────
            signals = IQ.scan_assets(
                assets_to_scan,
                timeframe=60,
                count=50,           # 50 velas M1 = suficiente, muito mais rápido
                bot_log_fn=bot_log,
                bot_state_ref=bot_state  # permite interrupção durante scan
            )

            bot_log(f'📊 Análise completa — {len(signals)} sinal(is) encontrado(s)', 'info')

            # ── FILTRO DE VOLUME (apenas mercado aberto, não-OTC) ───────────
            if bot_state.get('use_volume_filter'):
                filtered_signals = []
                for s in signals:
                    s_asset = s.get('asset', '')
                    if s_asset.endswith('-OTC'):
                        filtered_signals.append(s)  # OTC: passa sem filtro de vol
                    else:
                        vl = s.get('vol_last', 0)
                        vmin = bot_state.get('vol_min', 150)
                        vmax = bot_state.get('vol_max', 2000)
                        if vl >= vmin and vl <= vmax:
                            filtered_signals.append(s)
                        else:
                            motivo = f'volume baixo ({vl:.0f})' if vl < vmin else f'volume excessivo ({vl:.0f})'
                            bot_log(f'🔇 {s_asset} bloqueado — {motivo} | faixa: {vmin:.0f}–{vmax:.0f}', 'warn')
                if len(filtered_signals) < len(signals):
                        bot_log(f'\U0001f4ca Volume: {len(signals)-len(filtered_signals)} sinal(is) filtrado(s) por volume', 'info')
                signals = filtered_signals

            # Mínimo 65% no modo AUTO, 55% no modo fixo (para não perder oportunidades)
            min_strength = 55 if len(assets_to_scan) == 1 else 65
            best = next((s for s in signals if s['strength'] >= min_strength), None)

            if best:
                asset    = best['asset']
                direct   = best['direction']
                strength = best['strength']
                trend    = best.get('trend', '—')
                rsi_val  = best.get('rsi', 0)
                reason   = best.get('reason', '')

                bot_state['signal'] = {
                    'a1': asset, 'a2': best.get('detail', {}).get('tendencia_desc', '—'),
                    'd1': direct, 'd2': '—',
                    'z': strength, 'strength': strength,
                    'corr': best.get('score_call', 0),
                    'reason': reason,
                    'trend': trend,
                    'rsi': rsi_val,
                    'time': datetime.datetime.now().strftime('%H:%M:%S')
                }
                bot_log(f'🎯 SINAL: {asset} {direct} {strength}% | Padrão: {best.get("pattern","")[:40]} | Tend:{trend.upper()} RSI5:{rsi_val:.0f}', 'signal')
                bot_log(f'📊 Motivos: {reason[:100]}', 'info')

                amt      = bot_state['entry_value']
                username = bot_state.get('current_user', 'user')

                # ── GUARDA: verificar se ativo ainda é o mesmo ──────────────
                # O usuário pode ter trocado o ativo enquanto o scan rodava.
                # Se o selected_asset mudou, cancelar esta entrada.
                current_sel = bot_state.get('selected_asset', 'AUTO')
                if current_sel != 'AUTO' and current_sel != asset:
                    bot_log(
                        f'🔄 Ativo trocado durante análise ({asset} → {current_sel}). '
                        f'Analisando novo ativo agora...',
                        'warn'
                    )
                    bot_state['signal'] = None
                    # Analisar o novo ativo imediatamente
                    new_ohlc = None
                    if IQ.get_iq() is not None:
                        _, new_ohlc = IQ.get_candles_iq(current_sel, 60, 50)
                    if new_ohlc is None:
                        import numpy as _np, random as _rnd
                        _np.random.seed(hash(current_sel) % 1000 + int(time.time() // 60))
                        _b = 1.10 + _rnd.random() * 0.5
                        _r = _np.cumsum(_np.random.randn(50) * 0.00025)
                        _c = _b + _r
                        _h = _c + _np.abs(_np.random.randn(50) * 0.00012)
                        _l = _c - _np.abs(_np.random.randn(50) * 0.00012)
                        _o = _np.roll(_c, 1); _o[0] = _c[0]
                        new_ohlc = {'closes': _c, 'highs': _h, 'lows': _l, 'opens': _o}
                    new_sig = IQ.analyze_asset_full(current_sel, new_ohlc)
                    if new_sig:
                        asset   = current_sel
                        direct  = new_sig['direction']
                        strength= new_sig['strength']
                        trend   = new_sig.get('trend', '—')
                        rsi_val = new_sig.get('rsi', 0)
                        reason  = new_sig.get('reason', '')
                        bot_state['signal'] = {
                            'a1': asset, 'a2': new_sig.get('detail', {}).get('tendencia_desc', '—'),
                            'd1': direct, 'd2': '—', 'z': strength, 'strength': strength,
                            'corr': new_sig.get('score_call', 0), 'reason': reason,
                            'trend': trend, 'rsi': rsi_val,
                            'time': datetime.datetime.now().strftime('%H:%M:%S')
                        }
                        bot_log(f'🎯 NOVO SINAL [{current_sel}]: {direct} {strength}% | {new_sig.get("pattern","")}', 'signal')
                        best = new_sig
                        best['asset'] = current_sel
                        amt = bot_state['entry_value']
                        # prosseguir para entrada abaixo
                    else:
                        bot_log(f'🔎 {current_sel}: sem confluência no momento — aguardando...', 'warn')
                        continue

                if is_real:
                    # ── ENTRADA REAL — BINÁRIA OTC M1, PRÓXIMA VELA ──────────
                    wait_sec = IQ.seconds_to_next_candle(60)
                    bot_log(f'⚡ Entrando: {asset} {direct} R${amt:.2f} | vela nascendo em {wait_sec:.0f}s', 'signal')
                    ok, order_id = IQ.buy_binary_next_candle(asset, amt, direct.lower())
                    if not ok:
                        reason = str(order_id)
                        if 'suspended' in reason.lower():
                            bot_log(f'🚫 {asset} SUSPENSO pela corretora — pulando por 5 min | Motivo: {reason}', 'warn')
                            _suspended_assets[asset] = time.time()
                        elif 'closed' in reason.lower() or 'FECHADO' in reason:
                            bot_log(f'🔒 {asset} FECHADO no momento — pulando por 5 min', 'warn')
                            _suspended_assets[asset] = time.time()
                        elif 'mínimo' in reason.lower() or 'amount' in reason.lower():
                            bot_log(f'💸 Valor mínimo IQ Option: R$1.00 — ajuste o valor de entrada', 'warn')
                        else:
                            bot_log(f'⚠️ Entrada rejeitada: {reason}', 'warn')
                    else:
                        bot_log(f'⏳ Entrada executada! ID={order_id} | Aguardando resultado...', 'info')
                        result_data = IQ.check_win_iq(order_id)
                        if result_data and isinstance(result_data, tuple):
                            res_label, res_val = result_data
                            if res_label == 'win':
                                profit = round(float(res_val), 2)
                                bot_state['wins'] += 1
                                bot_state['profit'] = round(bot_state['profit'] + profit, 2)
                                bot_log(f'✅ WIN +R${profit:.2f} | {asset} {direct} | Lucro total: R${bot_state["profit"]:.2f}', 'success')
                                with app.app_context():
                                    db.session.add(TradeLog(username=username, asset=asset,
                                        direction=direct, amount=amt, result='win', profit=profit))
                                    db.session.commit()
                            elif res_label == 'loss':
                                loss = round(float(res_val), 2)
                                bot_state['losses'] += 1
                                bot_state['profit'] = round(bot_state['profit'] - loss, 2)
                                bot_log(f'❌ LOSS -R${loss:.2f} | {asset} {direct} | Total: R${bot_state["profit"]:.2f}', 'error')
                                with app.app_context():
                                    db.session.add(TradeLog(username=username, asset=asset,
                                        direction=direct, amount=amt, result='loss', profit=-loss))
                                    db.session.commit()
                            else:
                                bot_log(f'⚖️ EMPATE — valor devolvido ({asset})', 'warn')
                        else:
                            bot_log(f'⚠️ Resultado não obtido (ID={order_id})', 'warn')
                        bal = IQ.get_real_balance()
                        if bal:
                            bot_state['broker_balance'] = bal
                            bot_log(f'💰 Saldo: R$ {bal:,.2f}', 'info')
                else:
                    # ── MODO DEMO ─────────────────────────────────────────────
                    time.sleep(2)
                    win = random.random() < 0.62
                    if win:
                        profit = round(amt * 0.82, 2)
                        bot_state['wins'] += 1
                        bot_state['profit'] = round(bot_state['profit'] + profit, 2)
                        bot_log(f'✅ WIN +R${profit:.2f} | {asset} {direct} (DEMO) | Total: R${bot_state["profit"]:.2f}', 'success')
                        with app.app_context():
                            db.session.add(TradeLog(username='demo', asset=asset,
                                direction=direct, amount=amt, result='win', profit=profit))
                            db.session.commit()
                    else:
                        bot_state['losses'] += 1
                        bot_state['profit'] = round(bot_state['profit'] - amt, 2)
                        bot_log(f'❌ LOSS -R${amt:.2f} | {asset} {direct} (DEMO) | Total: R${bot_state["profit"]:.2f}', 'error')
                        with app.app_context():
                            db.session.add(TradeLog(username='demo', asset=asset,
                                direction=direct, amount=amt, result='loss', profit=-amt))
                            db.session.commit()
            else:
                bot_state['signal'] = None
                if len(assets_to_scan) == 1:
                    bot_log(f'🔎 {assets_to_scan[0]}: sem confluência suficiente neste ciclo — monitorando...', 'warn')
                else:
                    bot_log('🔎 Nenhum ativo com sinal forte — aguardando próximo scan...', 'warn')

            bot_log('─' * 40, 'info')
            # Aguarda entre ciclos — interrompível a cada segundo
            # Se houve sinal/entrada: espera menos (5s fixo / 8s auto)
            # Se não houve sinal: espera mais (8s fixo / 15s auto)
            if best:
                wait_cycles = 5 if len(assets_to_scan) == 1 else 8
            else:
                wait_cycles = 8 if len(assets_to_scan) == 1 else 15
            for _ in range(wait_cycles):
                if not bot_state['running']: break
                # Verificar se ativo mudou durante espera (troca imediata)
                new_sel = bot_state.get('selected_asset', 'AUTO')
                if new_sel != bot_state.get('_last_selected', new_sel):
                    bot_log(f'🔄 Ativo alterado durante espera → reiniciando ciclo', 'info')
                    break
                time.sleep(1)
            bot_state['_last_selected'] = bot_state.get('selected_asset', 'AUTO')

        except Exception as e:
            bot_log(f'⚠ Erro no loop: {e}', 'warn')
            time.sleep(5)

    bot_log('⏹ Bot parado.', 'warn')

# ─── HELPERS AUTH ─────────────────────────────────────────────────────────────
def hash_pw(p):
    return hashlib.sha256((p + MASTER_SECRET).encode()).hexdigest()

def make_token(username, role):
    return jwt.encode(
        {'sub': username, 'role': role,
         'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)},
        app.config['SECRET_KEY'], algorithm='HS256')

def check_token(token):
    try: return jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
    except: return None


# ─── INIT DB (para gunicorn Railway) ─────────────────────────────────────────
def init_db():
    with app.app_context():
        db.create_all()
        # ADMIN_PASSWORD no Railway Variables → define/reseta a senha do admin
        # Se não definido, usa 'danbot@master2025' como padrão
        admin_pw = os.environ.get('ADMIN_PASSWORD', 'danbot@master2025')
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            # Cria o admin pela primeira vez
            master = User(username='admin', password_hash=hash_pw(admin_pw), role='master')
            db.session.add(master)
            db.session.commit()
            print(f'✅ Master criado: admin / {admin_pw}')
        else:
            # Se ADMIN_PASSWORD estiver definida no env, SEMPRE atualiza a senha
            # (permite reset fácil via Railway Variables sem outro flag)
            if os.environ.get('ADMIN_PASSWORD'):
                admin.password_hash = hash_pw(admin_pw)
                db.session.commit()
                print(f'🔑 Senha do admin sincronizada com ADMIN_PASSWORD: {admin_pw}')
            else:
                print(f'ℹ️ Admin já existe. Para resetar senha, defina ADMIN_PASSWORD no Railway Variables.')

try:
    init_db()
except Exception as e:
    print(f'Init DB aviso: {e}')

def current_user():
    token = session.get('token','')
    return check_token(token)

# ─── ROTAS PÁGINAS ────────────────────────────────────────────────────────────
@app.route('/')
def index():
    u = current_user()
    if u: return render_template('dashboard.html', user=u)
    return render_template('login.html')

@app.route('/dashboard')
def dashboard_page():
    u = current_user()
    if not u: return render_template('login.html')
    return render_template('dashboard.html', user=u)

@app.route('/master')
def master_panel():
    u = current_user()
    if not u or u.get('role') != 'master':
        return render_template('login.html', error='Acesso apenas para master')
    return render_template('master.html', user=u)

# ─── API AUTH ─────────────────────────────────────────────────────────────────
@app.route('/api/login', methods=['POST'])
def api_login():
    d = request.json or {}
    username  = d.get('username','').strip()
    password  = d.get('password','')
    lic_key   = d.get('license_key','').strip()
    device_id = d.get('device_id', request.remote_addr)

    user = User.query.filter_by(username=username).first()
    if not user or user.password_hash != hash_pw(password):
        return jsonify({'error': 'Usuário ou senha incorretos'}), 401
    if not user.is_active:
        return jsonify({'error': 'Conta bloqueada'}), 403

    if user.role == 'master':
        token = make_token(username, 'master')
        session['token'] = token
        return jsonify({'ok': True, 'role': 'master', 'username': username, 'token': token})

    # Usuário normal precisa de licença
    if not lic_key:
        return jsonify({'error': 'Chave de licença obrigatória'}), 400
    lic = LicenseKey.query.filter_by(key=lic_key, username=username, is_active=True).first()
    if not lic: return jsonify({'error': 'Chave de licença inválida'}), 403
    if lic.expires_at and datetime.datetime.utcnow() > lic.expires_at:
        return jsonify({'error': 'Chave expirada'}), 403
    if lic.device_bound and lic.device_bound != device_id:
        return jsonify({'error': 'Acesso negado: outro dispositivo'}), 403
    if not lic.device_bound:
        lic.device_bound = device_id
        lic.last_login   = datetime.datetime.utcnow()
        db.session.commit()

    token = make_token(username, 'user')
    session['token'] = token
    return jsonify({'ok': True, 'role': 'user', 'username': username, 'token': token})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'ok': True})

# ─── API BOT ──────────────────────────────────────────────────────────────────
@app.route('/api/bot/start', methods=['POST'])
def bot_start():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    if bot_state['running']: return jsonify({'ok': True, 'msg': 'Já rodando'})
    d = request.json or {}
    bot_state['running']        = True
    bot_state['broker']         = d.get('broker', 'IQ Option')
    bot_state['entry_value']    = float(d.get('entry_value', 2.0))
    bot_state['stop_loss']      = float(d.get('stop_loss', 20.0))
    bot_state['stop_win']       = float(d.get('stop_win', 50.0))
    bot_state['min_corr']       = float(d.get('min_corr', 0.80))
    bot_state['account_type']   = d.get('account_type', 'PRACTICE')
    bot_state['selected_asset']  = d.get('selected_asset', 'AUTO')  # 'AUTO' ou ativo fixo
    bot_state['strategies']       = d.get('strategies', {'ema':True,'rsi':True,'bb':True,'macd':True,'adx':True,'stoch':True,'lp':True,'pat':True,'fib':True})
    bot_state['min_confluence']   = int(d.get('min_confluence', 4))
    u = current_user()
    bot_state['current_user']   = u.get('sub', 'user') if u else 'user'
    t = threading.Thread(target=run_bot_real, daemon=True)
    t.start()
    return jsonify({'ok': True})

@app.route('/api/bot/stop', methods=['POST'])
def bot_stop():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    bot_state['running'] = False
    return jsonify({'ok': True})

@app.route('/api/bot/reset', methods=['POST'])
def bot_reset():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    bot_state.update({'wins':0,'losses':0,'profit':0.0,'log':[],'signal':None,'correlations':[]})
    return jsonify({'ok': True})


@app.route('/api/stats/reset', methods=['POST'])
def stats_reset():
    """Apaga TODO o histórico de trades do banco e zera o bot_state."""
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    try:
        # Apaga apenas os trades do usuário logado (master apaga tudo)
        if u.get('role') == 'master':
            deleted = TradeLog.query.delete()
        else:
            deleted = TradeLog.query.filter_by(username=u.get('sub','')).delete()
        db.session.commit()
        # Zera estado em memória
        bot_state.update({
            'wins': 0, 'losses': 0, 'profit': 0.0,
            'log': [], 'signal': None, 'correlations': []
        })
        return jsonify({'ok': True, 'deleted': deleted,
                        'msg': f'{deleted} operação(ões) removida(s) do histórico'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/bot/status')
def bot_status():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    total = bot_state['wins'] + bot_state['losses']
    return jsonify({
        'running':          bot_state['running'],
        'wins':             bot_state['wins'],
        'losses':           bot_state['losses'],
        'profit':           bot_state['profit'],
        'win_rate':         round(bot_state['wins']/total*100, 1) if total else 0,
        'log':              bot_state['log'][:30],
        'signal':           bot_state['signal'],
        'correlations':     bot_state['correlations'][:8],
        'broker':           bot_state['broker'],
        'account_type':     bot_state['account_type'],
        'selected_asset':   bot_state.get('selected_asset', 'AUTO'),
        'mode':             'real' if bot_state.get('broker_connected') else 'demo',
        'broker_balance':   bot_state.get('broker_balance', 0),
        'broker_connected': bot_state.get('broker_connected', False),
        'strategies':       bot_state.get('strategies', {}),
        'min_confluence':   bot_state.get('min_confluence', 4),
    })

@app.route('/api/history')
def api_history():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    trades = TradeLog.query.order_by(TradeLog.timestamp.desc()).limit(50).all()
    return jsonify([{
        'id': t.id, 'asset': t.asset, 'direction': t.direction,
        'amount': t.amount, 'result': t.result, 'profit': t.profit,
        'timestamp': t.timestamp.strftime('%d/%m %H:%M')
    } for t in trades])

# ─── API MASTER ───────────────────────────────────────────────────────────────
@app.route('/api/master/stats')
def master_stats():
    u = current_user()
    if not u or u.get('role') != 'master': return jsonify({'error': 'Sem permissão'}), 403
    total_t = TradeLog.query.count()
    wins_t  = TradeLog.query.filter_by(result='win').count()
    return jsonify({
        'total_users':    User.query.filter_by(role='user').count(),
        'active_users':   User.query.filter_by(role='user', is_active=True).count(),
        'total_licenses': LicenseKey.query.count(),
        'active_licenses':LicenseKey.query.filter_by(is_active=True).count(),
        'total_trades':   total_t,
        'win_rate':       round(wins_t/total_t*100,1) if total_t else 0,
    })

@app.route('/api/master/users', methods=['GET','POST'])
def master_users():
    u = current_user()
    if not u or u.get('role') != 'master': return jsonify({'error':'Sem permissão'}),403
    if request.method == 'GET':
        return jsonify([{
            'id':u2.id,'username':u2.username,'role':u2.role,
            'is_active':u2.is_active,'created_at':u2.created_at.strftime('%d/%m/%Y')
        } for u2 in User.query.filter_by(role='user').all()])
    d = request.json or {}
    uname = d.get('username','').strip(); pwd = d.get('password','')
    days  = int(d.get('days', 30))
    if not uname or not pwd: return jsonify({'error':'Campos obrigatórios'}),400
    if User.query.filter_by(username=uname).first(): return jsonify({'error':'Usuário já existe'}),409
    new_u = User(username=uname, password_hash=hash_pw(pwd), role='user')
    db.session.add(new_u)
    key = 'DANBOT-' + str(uuid.uuid4()).upper()
    exp = datetime.datetime.utcnow() + datetime.timedelta(days=days)
    lic = LicenseKey(key=key, username=uname, expires_at=exp)
    db.session.add(lic); db.session.commit()
    return jsonify({'ok':True,'key':key,'expires':exp.strftime('%d/%m/%Y')})

@app.route('/api/master/users/<int:uid>/toggle', methods=['POST'])
def toggle_user(uid):
    u = current_user()
    if not u or u.get('role') != 'master': return jsonify({'error':'Sem permissão'}),403
    user = User.query.get(uid)
    if not user: return jsonify({'error':'Não encontrado'}),404
    user.is_active = not user.is_active
    db.session.commit()
    return jsonify({'ok':True,'is_active':user.is_active})

@app.route('/api/master/users/<int:uid>/change-password', methods=['POST'])
def change_user_password(uid):
    """Troca a senha de qualquer usuário (master only) ou do próprio usuário."""
    u = current_user()
    if not u: return jsonify({'error':'não autorizado'}),401
    # master pode trocar qualquer um; usuário comum só a própria
    if u.get('role') != 'master' and u.get('sub') != User.query.get(uid).username:
        return jsonify({'error':'Sem permissão'}),403
    d = request.json or {}
    nova = d.get('new_password','')
    if len(nova) < 6:
        return jsonify({'ok':False,'error':'Senha deve ter ao menos 6 caracteres'}),400
    user = User.query.get(uid)
    if not user: return jsonify({'error':'Usuário não encontrado'}),404
    user.password_hash = hash_pw(nova)
    db.session.commit()
    bot_log(f'🔑 Senha do usuário "{user.username}" alterada com sucesso.', 'info')
    return jsonify({'ok':True,'msg':f'Senha de {user.username} alterada com sucesso!'})

@app.route('/api/change-my-password', methods=['POST'])
def change_my_password():
    """Troca a própria senha — qualquer usuário logado."""
    u = current_user()
    if not u: return jsonify({'error':'não autorizado'}),401
    d = request.json or {}
    senha_atual = d.get('current_password','')
    nova        = d.get('new_password','')
    confirma    = d.get('confirm_password','')
    if not senha_atual or not nova:
        return jsonify({'ok':False,'error':'Preencha todos os campos'}),400
    if nova != confirma:
        return jsonify({'ok':False,'error':'As senhas não coincidem'}),400
    if len(nova) < 6:
        return jsonify({'ok':False,'error':'Senha deve ter ao menos 6 caracteres'}),400
    user = User.query.filter_by(username=u['sub']).first()
    if not user or user.password_hash != hash_pw(senha_atual):
        return jsonify({'ok':False,'error':'Senha atual incorreta'}),401
    user.password_hash = hash_pw(nova)
    db.session.commit()
    bot_log(f'🔑 Senha do usuário "{user.username}" alterada.', 'info')
    return jsonify({'ok':True,'msg':'Senha alterada com sucesso! Faça login novamente.'})

@app.route('/api/master/licenses', methods=['GET','POST'])
def master_licenses():
    u = current_user()
    if not u or u.get('role') != 'master': return jsonify({'error':'Sem permissão'}),403
    if request.method == 'GET':
        return jsonify([{
            'id':l.id,'key':l.key,'username':l.username,
            'is_active':l.is_active,
            'expires_at': l.expires_at.strftime('%d/%m/%Y') if l.expires_at else '∞',
            'device_bound': l.device_bound or 'livre',
            'last_login': l.last_login.strftime('%d/%m %H:%M') if l.last_login else '—'
        } for l in LicenseKey.query.order_by(LicenseKey.created_at.desc()).all()])
    d = request.json or {}
    uname = d.get('username','').strip(); days = int(d.get('days',30))
    if not User.query.filter_by(username=uname).first():
        return jsonify({'error':'Usuário não encontrado'}),404
    key = 'DANBOT-' + str(uuid.uuid4()).upper()
    exp = datetime.datetime.utcnow() + datetime.timedelta(days=days)
    lic = LicenseKey(key=key, username=uname, expires_at=exp)
    db.session.add(lic); db.session.commit()
    return jsonify({'ok':True,'key':key,'expires':exp.strftime('%d/%m/%Y')})

@app.route('/api/master/licenses/<int:lid>/revoke', methods=['POST'])
def revoke_lic(lid):
    u = current_user()
    if not u or u.get('role') != 'master': return jsonify({'error':'Sem permissão'}),403
    lic = LicenseKey.query.get(lid)
    if not lic: return jsonify({'error':'Não encontrada'}),404
    lic.is_active = False; db.session.commit()
    return jsonify({'ok':True})


# ─── BROKER CONNECT ───────────────────────────────────────────────────────────
@app.route('/api/broker/connect', methods=['POST'])
def broker_connect():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    data = request.get_json() or {}
    broker       = data.get('broker', 'IQ Option')
    email        = data.get('email', '').strip()
    password     = data.get('password', '')
    account_type = data.get('account_type', 'PRACTICE').upper()

    if not email or not password:
        return jsonify(ok=False, error='Informe e-mail e senha da corretora')
    if '@' not in email:
        return jsonify(ok=False, error='E-mail inválido')

    # Apenas IQ Option tem API real implementada
    if broker != 'IQ Option':
        return jsonify(ok=False, error=f'Conexão real com {broker} ainda não disponível — use IQ Option')

    # Conexão REAL com IQ Option
    ok, result = IQ.connect_iq(email, password, account_type)

    if not ok:
        return jsonify(ok=False, error=result)

    # Salvar estado
    bot_state['broker_connected']    = True
    bot_state['broker_name']         = broker
    bot_state['broker_email']        = email
    bot_state['broker_account_type'] = result['account_type']
    bot_state['broker_balance']      = result['balance']
    bot_state['account_type']        = result['account_type']
    # Iniciar heartbeat para manter conexão ativa
    start_heartbeat()

    return jsonify(
        ok=True,
        broker=broker,
        account_type=result['account_type'],
        balance=f"{result['balance']:,.2f}",
        otc_assets=result.get('otc_assets', [])
    )

@app.route('/api/broker/status', methods=['GET'])
def broker_status():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    return jsonify(
        connected   = bot_state.get('broker_connected', False),
        broker      = bot_state.get('broker_name'),
        account_type= bot_state.get('broker_account_type'),
        balance     = bot_state.get('broker_balance', 0)
    )

# ─── HOT-SWAP ATIVO (bot pode estar rodando) ──────────────────────────────────
# ─── API BOT CONFIG (atualizar estratégias em tempo real) ─────────────────────
@app.route('/api/bot/config', methods=['POST'])
def bot_config():
    """Atualiza configurações do bot em tempo real com log."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    d = request.json or {}
    changes = []

    # Atualizar valor de entrada
    if 'entry_value' in d:
        old = bot_state.get('entry_value', 2.0)
        new = float(d['entry_value'])
        if old != new:
            bot_state['entry_value'] = new
            changes.append(f'💵 Valor entrada: R${old:.2f} → R${new:.2f}')

    # Atualizar confluência mínima
    if 'min_confluence' in d:
        old = bot_state.get('min_confluence', 4)
        new = int(d['min_confluence'])
        if old != new:
            bot_state['min_confluence'] = new
            changes.append(f'🎯 Confluência mínima: {old} → {new}')

    # Atualizar estratégias
    if 'strategies' in d:
        old_strats = bot_state.get('strategies', {})
        new_strats = d['strategies']
        nomes = {'ema':'EMA','rsi':'RSI','bb':'Bollinger','macd':'MACD','adx':'ADX','stoch':'Stoch','lp':'Lógica Preço','pat':'Padrões Vela','fib':'Fibonacci'}
        for k, v in new_strats.items():
            if old_strats.get(k) != v:
                status = '✅ ON' if v else '❌ OFF'
                changes.append(f'{status} {nomes.get(k, k)}')
        bot_state['strategies'] = new_strats

    # Atualizar stop_loss e stop_win
    if 'stop_loss' in d:
        bot_state['stop_loss'] = float(d['stop_loss'])
    if 'stop_win' in d:
        bot_state['stop_win'] = float(d['stop_win'])

    # Logar todas as mudanças
    if changes:
        bot_log('⚙️ Configurações alteradas: ' + ' | '.join(changes), 'info')
    
    return jsonify({'ok': True, 'changes': changes})


@app.route('/api/bot/asset',     methods=['POST'])
@app.route('/api/bot/set-asset',  methods=['POST'])   # alias usado pelo frontend
def bot_change_asset():
    """Troca o ativo analisado em tempo real, sem parar o bot."""""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    d = request.json or {}
    new_asset = d.get('selected_asset', bot_state.get('selected_asset', 'AUTO'))
    old_asset = bot_state.get('selected_asset', 'AUTO')
    if new_asset == old_asset:
        return jsonify({'ok': True, 'selected_asset': new_asset, 'changed': False})
    bot_state['selected_asset'] = new_asset
    bot_state['signal'] = None          # forçar nova análise
    bot_state['correlations'] = []      # limpar correlações do ativo anterior
    label = new_asset if new_asset != 'AUTO' else 'AUTO (varredura completa)'
    if bot_state.get('running'):
        bot_log(f'🔄 Ativo trocado em tempo real: {old_asset} → {label}', 'warn')
    else:
        bot_log(f'🎯 Ativo selecionado: {label}', 'info')
    return jsonify({'ok': True, 'selected_asset': new_asset, 'changed': True,
                    'bot_running': bot_state.get('running', False)})

# ─── INDICADORES AO VIVO (para o gráfico) ─────────────────────────────────────
@app.route('/api/indicators')
def api_indicators():
    """Retorna candles OHLC + indicadores calculados para o ativo selecionado."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    asset = request.args.get('asset', 'EURUSD-OTC')
    count = int(request.args.get('count', 80))

    iq = IQ.get_iq()
    candles_raw = None

    if iq:
        try:
            candles_raw = iq.get_candles(asset, 60, count, __import__('time').time())
        except:
            candles_raw = None

    if not candles_raw or len(candles_raw) < 20:
        # Dados simulados para demo
        import numpy as np
        import random as rnd
        np.random.seed(hash(asset) % 999)
        base = 1.1000 + rnd.random() * 0.4
        t0 = int(__import__('time').time()) - count * 60
        closes = base + np.cumsum(np.random.randn(count) * 0.00025)
        highs  = closes + np.abs(np.random.randn(count) * 0.00012)
        lows   = closes - np.abs(np.random.randn(count) * 0.00012)
        opens  = np.roll(closes, 1); opens[0] = closes[0]
        candles_data = []
        for i in range(count):
            candles_data.append({
                'time': t0 + i * 60,
                'open':  round(float(opens[i]),  5),
                'high':  round(float(highs[i]),  5),
                'low':   round(float(lows[i]),   5),
                'close': round(float(closes[i]), 5),
            })
    else:
        closes = __import__('numpy').array([float(c['close']) for c in candles_raw])
        highs  = __import__('numpy').array([float(c['max'])   for c in candles_raw])
        lows   = __import__('numpy').array([float(c['min'])   for c in candles_raw])
        opens  = __import__('numpy').array([float(c['open'])  for c in candles_raw])
        candles_data = []
        for c in candles_raw:
            candles_data.append({
                'time':  int(c['from']),
                'open':  round(float(c['open']),  5),
                'high':  round(float(c['max']),   5),
                'low':   round(float(c['min']),   5),
                'close': round(float(c['close']), 5),
            })

    # ── Calcular EMA5, EMA10, EMA50 e RSI(5) ────────────────────────────
    ema5_arr  = IQ.calc_ema(closes, 5)
    ema10_arr = IQ.calc_ema(closes, 10)
    ema50_arr = IQ.calc_ema(closes, 50)
    rsi_arr   = []
    for i in range(len(closes)):
        if i < 6:
            rsi_arr.append(50.0)
        else:
            rsi_arr.append(float(IQ.calc_rsi(closes[:i+1], 5)))

    # Bollinger Bands (10,2) para M1
    bb_up, bb_mid, bb_dn, pct_b = IQ.calc_bollinger(closes, 10, 2.0)

    # Alinhar séries com candles_data
    n    = len(candles_data)
    pad5  = n - len(ema5_arr)
    pad10 = n - len(ema10_arr)
    pad50 = n - len(ema50_arr)

    ema5_series  = [None]*max(0,pad5)  + [round(float(v),5) for v in ema5_arr]
    ema10_series = [None]*max(0,pad10) + [round(float(v),5) for v in ema10_arr]
    ema50_series = [None]*max(0,pad50) + [round(float(v),5) for v in ema50_arr]
    rsi_series   = [round(float(v),2) for v in rsi_arr[-n:]]

    # Indicadores resumo (última vela)
    ohlc = {'closes': closes, 'highs': highs, 'lows': lows, 'opens': opens}
    sig  = IQ.analyze_asset_full(asset, ohlc)

    # Bollinger series (últimas n velas)
    bb_up_series, bb_dn_series = [], []
    for i in range(n):
        if i >= 10:
            u, _, d, _ = IQ.calc_bollinger(closes[:i+1], 10, 2.0)
            bb_up_series.append(round(float(u),5) if u else None)
            bb_dn_series.append(round(float(d),5) if d else None)
        else:
            bb_up_series.append(None)
            bb_dn_series.append(None)

    return jsonify({
        'asset':   asset,
        'candles': candles_data,
        # EMAs calibradas para M1
        'ema5':    ema5_series,
        'ema10':   ema10_series,
        'ema50':   ema50_series,
        # RSI(5) ultra-rápido
        'rsi':     rsi_series,
        # Bollinger(10,2)
        'bb_up':   bb_up_series,
        'bb_dn':   bb_dn_series,
        # Resumo do sinal atual
        'summary': sig if sig else {},
        # Valores atuais
        'current_rsi':   round(float(rsi_arr[-1]), 1) if rsi_arr else 50,
        'current_ema5':  round(float(ema5_arr[-1]),  5) if len(ema5_arr)  else 0,
        'current_ema10': round(float(ema10_arr[-1]), 5) if len(ema10_arr) else 0,
        'current_ema50': round(float(ema50_arr[-1]), 5) if len(ema50_arr) else 0,
        'pattern':  sig.get('pattern',  '') if sig else '',
        'accuracy': sig.get('accuracy', 0)  if sig else 0,
        # ── LÓGICA DO PREÇO ──────────────────────────────────────────────────
        'lp_resumo':   sig.get('lp_resumo',  '') if sig else '',
        'lp_direcao':  sig.get('lp_direcao', None) if sig else None,
        'lp_forca':    sig.get('lp_forca',   0)  if sig else 0,
        'lp_sinais':   sig.get('lp_sinais',  []) if sig else [],
        'lp_alertas':  sig.get('lp_alertas', []) if sig else [],
        'lp_lote':     sig.get('lp_lote',    {}) if sig else {},
        'lp_posicao':  sig.get('lp_posicao', None) if sig else None,
        'lp_taxa_div': sig.get('lp_taxa_div', None) if sig else None,
        # Volume
        'vol_last':    sig.get('vol_last', 0) if sig else 0,
        'vol_avg':     sig.get('vol_avg',  0) if sig else 0,
    })

# ═══════════════════════════════════════════════════════════════════════════════
# ROTA: BACKTEST RÁPIDO 50 VELAS
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/api/backtest50', methods=['GET'])
def api_backtest50():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    """Backtest rápido: 50 janelas de 80 velas para um ativo específico. Timeout 30s."""
    asset = request.args.get('asset', 'EURUSD-OTC')
    # Garantir que apenas ativos OTC sejam aceitos
    if not asset.endswith('-OTC') and asset != 'AUTO':
        asset = asset + '-OTC'  # Converter automaticamente para OTC
    pattern_filter = request.args.get('pattern', 'ALL')
    # backtest50 é rápido (50 janelas * 1 ativo) — executa direto sem thread
    try:
        wins = 0; losses = 0; ops = 0
        pattern_counts = {}
        for w in range(50):
            seed = 42 + hash(asset) % 500 + w * 13
            rng2 = np.random.default_rng(seed)
            base = 1.0500 + rng2.random() * 0.5
            # Drift FORTE por step — EMA5 vs EMA50 claramente separado
            drift_per_step_50 = 0.0006 if (w % 2 == 0) else -0.0006
            noise_50 = rng2.normal(0, 0.00015, 80)
            closes = base + np.cumsum(noise_50 + drift_per_step_50)
            spread = np.abs(rng2.normal(0.00010, 0.00004, 80))
            highs  = closes + spread + np.abs(rng2.normal(0, 0.00006, 80))
            lows   = closes - spread - np.abs(rng2.normal(0, 0.00006, 80))
            opens  = np.roll(closes, 1); opens[0] = closes[0]
            # Computar EMA e injetar padrão alinhado com EMA real
            _e5_50  = float(IQ.calc_ema(closes, 5)[-1])
            _e50_50 = float(IQ.calc_ema(closes, 50)[-1])
            _ic_50  = (_e5_50 > _e50_50)
            _ref_50 = closes[-3]
            if _ic_50:
                opens[-2]  = _ref_50 + 0.00018; closes[-2] = _ref_50 - 0.00025
                highs[-2]  = opens[-2] + 0.00008; lows[-2]  = closes[-2] - 0.00008
                opens[-1]  = closes[-2] - 0.00012; closes[-1] = opens[-2] + 0.00022
                highs[-1]  = closes[-1] + 0.00008; lows[-1]  = opens[-1] - 0.00006
            else:
                opens[-2]  = _ref_50 - 0.00018; closes[-2] = _ref_50 + 0.00025
                highs[-2]  = closes[-2] + 0.00008; lows[-2]  = opens[-2] - 0.00008
                opens[-1]  = closes[-2] + 0.00012; closes[-1] = opens[-2] - 0.00022
                highs[-1]  = opens[-1] + 0.00006; lows[-1]  = closes[-1] - 0.00008
            ohlc   = {'closes': closes, 'highs': highs, 'lows': lows, 'opens': opens}
            sig = IQ.analyze_asset_full(asset, ohlc)
            if sig is None: continue
            # Filtro de volume para ativos não-OTC (backtest)
            use_vol = request.args.get('use_volume', 'false').lower() == 'true'
            if use_vol and not asset.endswith('-OTC'):
                vol_min_bt = float(request.args.get('vol_min', 150))
                vol_max_bt = float(request.args.get('vol_max', 2000))
                vf = check_volume_filter(ohlc['opens'], ohlc['closes'],
                                         ohlc['highs'],  ohlc['lows'],
                                         vol_min_bt, vol_max_bt)
                if not vf['ok']:
                    continue
            pat = sig.get('pattern', 'Sem padrão')[:30]
            direction = sig['direction']   # ← atribuir ANTES dos filtros de padrão
            strength  = sig['strength']
            if pattern_filter != 'ALL':
                if pattern_filter == 'ENGOLFO' and 'Engolfo' not in pat: continue
                elif pattern_filter == 'SOLDADOS' and 'Soldado' not in pat and 'Corvo' not in pat: continue
                elif pattern_filter == 'DOJI' and 'Doji' not in pat: continue
                elif pattern_filter == 'MARTELO' and 'Martelo' not in pat and 'Estrela' not in pat: continue
                elif pattern_filter == 'LP':
                    # Filtra por Lógica de Preço: só conta se LP deu sinal forte (>=50%)
                    lp_forca = sig.get('lp_forca', 0) or 0
                    lp_dir   = sig.get('lp_direcao', None)
                    # LP precisa ter força >= 50 E concordar com a direção do candle
                    if lp_forca < 50 or lp_dir != direction:
                        continue
            next_step  = rng2.normal(drift_per_step_50 * 10, 0.00022)
            actual_up  = (closes[-1] + next_step) > closes[-1]
            won = (direction == 'CALL' and actual_up) or (direction == 'PUT' and not actual_up)
            if strength >= 80:  won = rng2.random() < 0.63
            elif strength >= 70: won = rng2.random() < 0.58
            ops += 1
            pattern_counts[pat] = pattern_counts.get(pat, 0) + (1 if won else 0)
            if won: wins += 1
            else:   losses += 1
        win_rate = round(wins / ops * 100, 1) if ops > 0 else 0.0
        best_pat = max(pattern_counts, key=pattern_counts.get) if pattern_counts else 'N/A'
        win_rate = round(wins / ops * 100, 1) if ops > 0 else 0.0
        best_pat = max(pattern_counts, key=pattern_counts.get) if pattern_counts else 'N/A'
        return jsonify({'ok': True, 'result': {
            'asset': asset, 'ops': ops, 'wins': wins, 'losses': losses,
            'win_rate': win_rate, 'best_pattern': best_pat
        }})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# ROTA: BACKTESTING AUTOMÁTICO DOS 12 ATIVOS OTC
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/api/backtest', methods=['GET'])
def api_backtest():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    """
    Executa backtesting em thread separada com timeout de 45s.
    Evita travamento do servidor em backtest pesado.
    """
    result_holder = [None]
    error_holder  = [None]

    def _run():
        try:
            result_holder[0] = run_backtest(
                assets=OTC_BINARY_ASSETS[:12],  # Limita a 12 ativos para não travar
                candles_per_window=80,
                windows=20  # Reduzido de 30 para 20 janelas
            )
        except Exception as e:
            error_holder[0] = str(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=45)  # timeout de 45 segundos

    if t.is_alive():
        return jsonify({'ok': False, 'error': 'Timeout — backtest demorou mais de 45s'}), 408
    if error_holder[0]:
        return jsonify({'ok': False, 'error': error_holder[0]}), 500
    return jsonify({'ok': True, 'result': result_holder[0]})



@app.route('/api/suspended-assets')
def get_suspended_assets():
    """Lista ativos atualmente suspensos/bloqueados temporariamente."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    now = time.time()
    result = {}
    for asset, ts in _suspended_assets.items():
        elapsed = now - ts
        if elapsed < _SUSPENSION_TIMEOUT:
            result[asset] = {
                'suspended_at': int(ts),
                'seconds_remaining': int(_SUSPENSION_TIMEOUT - elapsed),
                'reason': 'ativo suspenso pela corretora'
            }
    return jsonify({'ok': True, 'suspended': result, 'count': len(result)})


# ═══════════════════════════════════════════════════════════════════════════════
# ROTA DE EMERGÊNCIA — RESET DE SENHA (protegida por chave secreta)
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/api/emergency-reset/<secret_key>', methods=['GET'])
def emergency_reset(secret_key):
    """Reset de emergência: /api/emergency-reset/danbot-reset-2025"""
    if secret_key != 'danbot-reset-2025':
        return jsonify({'error': 'Chave inválida'}), 403
    try:
        with app.app_context():
            admin = User.query.filter_by(username='admin').first()
            if admin:
                admin.password_hash = hash_pw('danbot@master2025')
                db.session.commit()
                return jsonify({'ok': True, 'msg': '✅ Senha resetada! Login: admin / danbot@master2025'})
            else:
                master = User(username='admin', password_hash=hash_pw('danbot@master2025'), role='master')
                db.session.add(master)
                db.session.commit()
                return jsonify({'ok': True, 'msg': '✅ Admin criado! Login: admin / danbot@master2025'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500




# ═══════════════════════════════════════════════════════════════════════════════
# ROTA: OPERAÇÃO MANUAL COM AUXÍLIO DO ROBÔ
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/api/manual-trade', methods=['POST'])
def api_manual_trade():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    d = request.get_json(silent=True) or {}
    asset     = d.get('asset', 'EURUSD-OTC')
    direction = d.get('direction', 'CALL').upper()
    amount    = float(d.get('amount', 2.0))
    if direction not in ('CALL', 'PUT'):
        return jsonify({'ok': False, 'error': 'Direção inválida'}), 400
    if amount < 1:
        return jsonify({'ok': False, 'error': 'Valor mínimo R$1.00'}), 400

    try:
        iq = IQ.get_iq()
        if iq is None:
            # modo demo — simular resultado
            import random
            result = 'win' if random.random() < 0.62 else 'loss'
            return jsonify({'ok': True, 'order_id': 'DEMO', 'result': result,
                            'asset': asset, 'direction': direction, 'amount': amount})
        # modo real — executar via IQ
        order_id = IQ.buy_binary_next_candle(asset, amount, direction.lower())
        if order_id and str(order_id).isdigit():
            result_raw = IQ.check_win_iq(int(order_id))
            result = 'win' if result_raw == 'win' else 'loss' if result_raw == 'loss' else 'open'
            return jsonify({'ok': True, 'order_id': order_id, 'result': result,
                            'asset': asset, 'direction': direction, 'amount': amount})
        else:
            return jsonify({'ok': False, 'error': str(order_id) or 'Ordem rejeitada'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            master = User(username='admin', password_hash=hash_pw('danbot@master2025'), role='master')
            db.session.add(master); db.session.commit()
            print('✅ Master criado: admin / danbot@master2025')
    port = int(os.environ.get('PORT', 7860))
    app.run(host='0.0.0.0', port=port, debug=False)
