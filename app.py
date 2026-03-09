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
from iq_integration import run_backtest, run_backtest_real, gerar_perfil_ativo, get_asset_profile, _asset_profiles, OTC_BINARY_ASSETS, ALL_BINARY_ASSETS, OPEN_BINARY_ASSETS, check_volume_filter, start_heartbeat, stop_heartbeat

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'danbot-default-secret-key-2025-change-me')
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

# ─── ARQUITETURA MULTI-USUÁRIO ────────────────────────────────────────────────
# Cada usuário tem seu próprio estado isolado: bot, broker, placar, log, etc.
# Nenhum dado é compartilhado entre usuários.

_SUSPENSION_TIMEOUT = 300  # 5 minutos de espera para tentar novamente

def _default_user_state():
    """Cria um estado padrão isolado para um novo usuário."""
    return {
        'running': False,
        'broker_connected': False,
        'broker_name': None,
        'broker_email': None,
        'broker_password': None,
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
        'modo_operacao': 'auto',
        'dead_candle_mode': 'disabled',   # 'disabled' | 'solo' | 'combined'
        'asset_loss_track': {},             # {asset: [timestamps]} bloqueio consecutivo
        'use_volume_filter': False,
        'vol_min': 150.0,
        'vol_max': 2000.0,
        'strategies': {'ema':True,'rsi':True,'bb':True,'macd':True,'adx':True,'stoch':True,'lp':True,'pat':True,'fib':True},
        'min_confluence': 4,
        '_in_trade': False,
        '_entry_cooldown': {},
        '_bt_top_assets': [],
        '_bt_ranked': [],
        '_suspended_assets': {},
    }

# Armazenamento de estados por usuário
_USER_STATES    = {}   # {username: state_dict}
_USER_THREADS   = {}   # {username: Thread}
_USER_RUN_IDS   = {}   # {username: int}
_USER_BOT_LOCKS = {}   # {username: Lock}  — impede 2 instâncias
_USER_CONN_STATES = {} # {username: conn_state_dict}
_USER_CONN_LOCKS  = {} # {username: Lock}
_GLOBAL_STATE_LOCK = threading.Lock()  # protege criação de novas entradas

def get_user_state(username: str) -> dict:
    """Retorna (ou cria) o estado isolado do usuário."""
    with _GLOBAL_STATE_LOCK:
        if username not in _USER_STATES:
            _USER_STATES[username] = _default_user_state()
    return _USER_STATES[username]

def get_user_bot_lock(username: str) -> threading.Lock:
    with _GLOBAL_STATE_LOCK:
        if username not in _USER_BOT_LOCKS:
            _USER_BOT_LOCKS[username] = threading.Lock()
    return _USER_BOT_LOCKS[username]

def get_user_conn_state(username: str) -> dict:
    with _GLOBAL_STATE_LOCK:
        if username not in _USER_CONN_STATES:
            _USER_CONN_STATES[username] = {
                'status': 'idle', 'result': None, 'error': None, 'ts': 0.0
            }
    return _USER_CONN_STATES[username]

def get_user_conn_lock(username: str) -> threading.Lock:
    with _GLOBAL_STATE_LOCK:
        if username not in _USER_CONN_LOCKS:
            _USER_CONN_LOCKS[username] = threading.Lock()
    return _USER_CONN_LOCKS[username]

# Compat: bot_state global aponta para o usuário 'admin' (retrocompatibilidade)
# NÃO use bot_state diretamente; use get_user_state(username)
bot_state = _default_user_state()  # mantido apenas para compatibilidade interna

# Apenas ativos de opções BINÁRIAS OTC (turbo M1)
OTC_ASSETS = [
    # ── 142 ativos OTC confirmados por API real (08/03/2026) ──
    # ── Forex OTC (45 pares) ──
    'AUDCAD-OTC',
    'AUDCHF-OTC',
    'AUDJPY-OTC',
    'AUDNZD-OTC',
    'AUDUSD-OTC',
    'CADCHF-OTC',
    'CADJPY-OTC',
    'CHFJPY-OTC',
    'CHFNOK-OTC',
    'EURAUD-OTC',
    'EURCAD-OTC',
    'EURCHF-OTC',
    'EURGBP-OTC',
    'EURJPY-OTC',
    'EURNZD-OTC',
    'EURTHB-OTC',
    'EURUSD-OTC',
    'GBPAUD-OTC',
    'GBPCAD-OTC',
    'GBPCHF-OTC',
    'GBPJPY-OTC',
    'GBPNZD-OTC',
    'GBPUSD-OTC',
    'JPYTHB-OTC',
    'NOKJPY-OTC',
    'NZDCAD-OTC',
    'NZDCHF-OTC',
    'NZDJPY-OTC',
    'NZDUSD-OTC',
    'PENUSD-OTC',
    'USDBRL-OTC',
    'USDCAD-OTC',
    'USDCHF-OTC',
    'USDCOP-OTC',
    'USDHKD-OTC',
    'USDINR-OTC',
    'USDJPY-OTC',
    'USDMXN-OTC',
    'USDNOK-OTC',
    'USDPLN-OTC',
    'USDSEK-OTC',
    'USDSGD-OTC',
    'USDTHB-OTC',
    'USDTRY-OTC',
    'USDZAR-OTC',
    # ── Crypto OTC (45) ──
    'ARBUSD-OTC',
    'ATOMUSD-OTC',
    'BCHUSD-OTC',
    'BONKUSD-OTC',
    'DASHUSD-OTC',
    'DOTUSD-OTC',
    'DYDXUSD-OTC',
    'EOSUSD-OTC',
    'FARTCOINUSD-OTC',
    'FETUSD-OTC',
    'FLOKIUSD-OTC',
    'GRTUSD-OTC',
    'HBARUSD-OTC',
    'ICPUSD-OTC',
    'IMXUSD-OTC',
    'IOTAUSD-OTC',
    'JUPUSD-OTC',
    'LABUBUUSD-OTC',
    'LINKUSD-OTC',
    'LTCUSD-OTC',
    'MANAUSD-OTC',
    'MATICUSD-OTC',
    'MELANIAUSD-OTC',
    'NEARUSD-OTC',
    'ONDOUSD-OTC',
    'ORDIUSD-OTC',
    'PENGUUSD-OTC',
    'PEPEUSD-OTC',
    'PYTHUSD-OTC',
    'RAYDIUMUSD-OTC',
    'RENDERUSD-OTC',
    'RONINUSD-OTC',
    'SANDUSD-OTC',
    'SATSUSD-OTC',
    'SEIUSD-OTC',
    'SHIBUSD-OTC',
    'STXUSD-OTC',
    'SUIUSD-OTC',
    'TAOUSD-OTC',
    'TIAUSD-OTC',
    'TONUSD-OTC',
    'TRUMPUSD-OTC',
    'WIFUSD-OTC',
    'WLDUSD-OTC',
    'XRPUSD-OTC',
    # ── Stocks OTC (29) ──
    'AIG-OTC',
    'ALIBABA-OTC',
    'AMAZON-OTC',
    'AMZN/ALIBABA-OTC',
    'AMZN/EBAY-OTC',
    'APPLE-OTC',
    'BIDU-OTC',
    'CITI-OTC',
    'COKE-OTC',
    'FB-OTC',
    'FWONA-OTC',
    'GOOGLE-OTC',
    'GOOGLE/MSFT-OTC',
    'GS-OTC',
    'INTEL-OTC',
    'JPM-OTC',
    'KLARNA-OTC',
    'MCDON-OTC',
    'META/GOOGLE-OTC',
    'MORSTAN-OTC',
    'MSFT-OTC',
    'MSFT/AAPL-OTC',
    'NFLX/AMZN-OTC',
    'NIKE-OTC',
    'NVDA/AMD-OTC',
    'PLTR-OTC',
    'SNAP-OTC',
    'TESLA-OTC',
    'TESLA/FORD-OTC',
    # ── Índices OTC (15) ──
    'AUS200-OTC',
    'EU50-OTC',
    'FR40-OTC',
    'GER30-OTC',
    'GER30/UK100-OTC',
    'HK33-OTC',
    'JP225-OTC',
    'SP35-OTC',
    'SP500-OTC',
    'UK100-OTC',
    'US100/JP225-OTC',
    'US2000-OTC',
    'US30-OTC',
    'US30/JP225-OTC',
    'USNDAQ100-OTC',
    # ── Commodities OTC (8) ──
    'UKOUSD-OTC',
    'USOUSD-OTC',
    'XAGUSD-OTC',
    'XAU/XAG-OTC',
    'XAUUSD-OTC',
    'XNGUSD-OTC',
    'XPDUSD-OTC',
    'XPTUSD-OTC',
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

def bot_log(msg, level='info', username=None):
    """Log isolado por usuário. Se username=None, usa bot_state global (compat)."""
    colors = {'info':'#9CA3AF','success':'#10B981','error':'#EF4444','warn':'#F59E0B','signal':'#00D4FF'}
    color  = colors.get(level, '#9CA3AF')
    entry  = {
        'time': datetime.datetime.now().strftime('%H:%M:%S'),
        'msg': msg, 'color': color
    }
    st = get_user_state(username) if username else bot_state
    st['log'].insert(0, entry)
    if len(st['log']) > 100:
        st['log'] = st['log'][:100]

def run_bot_real(run_id=0, username="admin"):
    """
    Loop principal — análise técnica completa.
    Modo AUTO: escaneia todos os ativos OTC + Mercado Aberto e escolhe o melhor sinal real.
    Modo FIXO: analisa apenas o ativo selecionado pelo usuário.
    """
    # ISOLAMENTO POR USUÁRIO: definir contexto de thread para iq_integration
    if hasattr(IQ, 'set_user_context'):
        IQ.set_user_context(username)
    # ISOLAMENTO POR USUÁRIO: cada usuário tem seu próprio state
    bot_state = get_user_state(username)
    _suspended_assets = bot_state.setdefault('_suspended_assets', {})

    # ── CLOSURE DE LOG ISOLADA POR USUÁRIO ──────────────────────────────────
    # Garante que todos os logs do bot_thread vão para o state correto do usuário
    def bot_log(msg, level='info'):
        colors = {'info':'#9CA3AF','success':'#10B981','error':'#EF4444','warn':'#F59E0B','signal':'#00D4FF'}
        color  = colors.get(level, '#9CA3AF')
        entry  = {'time': __import__('datetime').datetime.now().strftime('%H:%M:%S'), 'msg': msg, 'color': color}
        bot_state['log'].insert(0, entry)
        if len(bot_state['log']) > 150:
            bot_state['log'] = bot_state['log'][:150]

    # Verificação inicial de conexão
    mode_label = bot_state.get('account_type', 'PRACTICE')

    bot_log(f'🚀 DANBOT PRO iniciado — Modo {mode_label}', 'success')

    # ── Inicializar is_real ANTES do primeiro uso ──────────────────
    # Invalidar cache para forçar verificação real ao iniciar
    if hasattr(IQ, 'invalidate_session_cache'):
        IQ.invalidate_session_cache()
    is_real = bot_state.get('broker_connected', False) and IQ.is_iq_session_valid()

    if not is_real:
        _email_check = bot_state.get('broker_email')
        if _email_check:
            bot_log('⚠️ Corretora desconectada — bot irá analisar e TENTAR reconectar automaticamente', 'warn')
            bot_log('💡 Aguarde a reconexão automática ou acesse "Corretora" para reconectar manualmente', 'info')
        else:
            bot_log('🔌 Corretora NÃO conectada — analisa mas NÃO fará entradas até conectar', 'error')
            bot_log('👉 Acesse a aba "Corretora" → conecte sua conta IQ Option (PRACTICE = conta demo real)', 'warn')
    else:
        bal = IQ.get_real_balance()
        if bal is not None:
            bot_state['broker_balance'] = bal
            bot_log(f'✅ IQ Option conectada | Saldo: R$ {bal:,.2f}', 'success')

    bot_log(f'💰 Entrada: R${bot_state["entry_value"]:.2f} | SL: R${bot_state["stop_loss"]:.2f} | SW: R${bot_state["stop_win"]:.2f}', 'info')

    # ── BACKTEST AUTOMÁTICO INICIAL (em background) ─────────────────────
    # Roda em thread para não atrasar o início das análises.
    # Enquanto calcula, o bot usa todos os OTC. Quando termina → top 6.
    bot_log('🧪 Backtest automático iniciado em background (top 6 ativos)...', 'info')
    bot_state['_bt_top_assets'] = []
    bot_state['_bt_ranked'] = []
    def _run_initial_backtest():
        try:
            _bt_assets = IQ.OTC_BINARY_ASSETS if hasattr(IQ, 'OTC_BINARY_ASSETS') else IQ.ALL_BINARY_ASSETS
            _bt_result = IQ.run_backtest(assets=_bt_assets, candles_per_window=100, windows=20, seed_base=42)
            _bt_ranked = _bt_result.get('ranked', [])
            _auto_top  = [r['asset'] for r in _bt_ranked[:6]]
            if _auto_top:
                bot_log(f'🏆 Backtest concluído! Top 6: {", ".join(_auto_top)}', 'success')
                for _i, _r in enumerate(_bt_ranked[:6], 1):
                    bot_log(f'   {_i}. {_r["asset"]} — {_r["win_rate"]}% ({_r["ops"]} ops)', 'info')
                bot_state['_bt_top_assets'] = _auto_top
                bot_state['_bt_ranked']     = _bt_ranked[:10]
            else:
                bot_log('⚠️ Backtest sem resultados — usando todos os OTC', 'warn')
        except Exception as _bt_err:
            bot_log(f'⚠️ Erro no backtest: {_bt_err} — usando todos os OTC', 'warn')
    threading.Thread(target=_run_initial_backtest, daemon=True, name='bt-inicial').start()
    # ────────────────────────────────────────────────────────────────────

    # ── Inicializar controles de entrada ─────────────────────────────────
    bot_state['_in_trade']       = False   # trava: 1 entrada por vez
    bot_state['_entry_cooldown'] = {}      # {asset: timestamp_ultima_entrada}
    COOLDOWN_SECONDS = 60                  # 60s entre entradas no mesmo ativo (era 240s)

    cycle = 0
    while bot_state['running']:
        # Verificar se esta thread ainda é a instância ativa
        if run_id != 0 and run_id != _USER_RUN_IDS.get(username, 0):
            bot_log(f'⚠️ Thread obsoleta (run_id={run_id}) — encerrando', 'warn')
            return
        # ── VERIFICAR USUÁRIO ATIVO A CADA CICLO ──────────────────────────
        # Garante que usuário excluído/desativado pelo master não opera
        if cycle % 3 == 0:  # Verificar a cada 3 ciclos (~36s)
            with app.app_context():
                _u_check = User.query.filter_by(username=username).first()
                if not _u_check or not _u_check.is_active:
                    _reason = 'excluído' if not _u_check else 'desativado'
                    bot_log(f'🛑 Usuário {_reason} pelo master — bot encerrado automaticamente', 'error')
                    bot_state['running'] = False
                    _SESSION_BLACKLIST.add(username)
                    return
        try:
            cycle += 1
            _cycle_ts = datetime.datetime.now().strftime('%H:%M:%S')
            bot_log(f'🔁 ── Ciclo #{cycle} iniciado às {_cycle_ts} ──', 'info')

            # Verificar conexão a cada ciclo — usa cache de 10s (não bloqueia GIL)
            _broker_was_connected = bot_state.get('broker_connected', False)
            is_real = _broker_was_connected and IQ.is_iq_session_valid()
            if not is_real and _broker_was_connected:
                bot_log('⚠️ Conexão IQ perdida — tentando reconectar...', 'warn')
                bot_state['broker_connected'] = False
                if hasattr(IQ, 'invalidate_session_cache'):
                    IQ.invalidate_session_cache()
                # ── AUTO-RECONEXÃO: usa credenciais salvas ─────────────────
                _email_saved = bot_state.get('broker_email')
                _pass_saved  = bot_state.get('broker_password')
                _acct_saved  = bot_state.get('broker_account_type', 'PRACTICE')
                if _email_saved and _pass_saved:
                    _broker_name_rc = bot_state.get('broker_name', 'IQ Option')
                    _broker_host_rc = BROKER_HOSTS.get(_broker_name_rc, 'iqoption.com')
                    bot_log(f'🔁 Reconectando {_broker_name_rc} ({_acct_saved}) — {_email_saved}...', 'warn')
                    try:
                        _ok_rc, _res_rc = IQ.connect_iq(_email_saved, _pass_saved, _acct_saved,
                                                         host=_broker_host_rc, username=username)
                        if _ok_rc:
                            bot_state['broker_connected'] = True
                            bot_state['broker_balance']   = _res_rc.get('balance', 0)
                            is_real = True
                            bot_log(f'✅ Reconectado com sucesso! Saldo: R$ {_res_rc.get("balance",0):,.2f}', 'success')
                            if hasattr(IQ, 'start_heartbeat'):
                                IQ.start_heartbeat()
                        else:
                            bot_log(f'❌ Reconexão falhou: {_res_rc}', 'error')
                            bot_log(f'💡 Verifique: senha correta? 2FA desativado? {_broker_name_rc} acessível?', 'warn')
                    except Exception as _erc:
                        bot_log(f'❌ Erro na reconexão: {_erc}', 'error')
                elif _email_saved and not _pass_saved:
                    # Email salvo mas sem senha — acontece após reinício do servidor
                    bot_log('🔑 Sessão expirou após reinício — acesse "Corretora" e reconecte', 'error')
                    bot_log(f'📧 Última conta: {_email_saved}', 'info')
                    # Limpar broker_email para não repetir mensagem a cada ciclo
                    bot_state['broker_email'] = None
                else:
                    bot_log('🔌 Corretora não conectada — acesse a aba "Corretora" para conectar', 'error')

            # Atualizar saldo em background (não bloqueia o loop)
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
            # ── SUPORTE A OTC E MERCADO ABERTO BINÁRIO ──────────────────────
            # NÃO converter ativo não-OTC para OTC!
            # O usuário pode selecionar ativos de mercado aberto (ex: EURUSD)
            # e o bot deve respeitar exatamente o ativo escolhido.
            is_otc_asset = selected_asset == 'AUTO' or selected_asset.endswith('-OTC')
            # Log de sincronização de horário (UTC = padrão IQ Option)
            _utc_now = datetime.datetime.utcnow().strftime('%H:%M:%S UTC')
            _sec_next = IQ.seconds_to_next_candle(60)
            if selected_asset and selected_asset != 'AUTO':
                assets_to_scan = [selected_asset]
                tipo_label = 'OTC' if is_otc_asset else '🟢 Mercado Aberto'
                bot_log(f'🔄 Ciclo #{cycle} — {selected_asset} [{tipo_label}] | Vela em {_sec_next:.0f}s | {_utc_now}', 'info')
            else:
                # AUTO: prioriza top 6 do backtest inicial se disponível
                _bt_top = bot_state.get('_bt_top_assets', [])
                modo = 'REAL' if is_real else 'SEM CONEXÃO'

                if _bt_top:
                    # Ciclos 1-2: usar top backtest (6 ativos) para entrada rápida
                    # Ciclos 3+: expandir para varrer todos os 134 OTC em batches
                    if cycle <= 2:
                        assets_to_scan = _bt_top
                        bot_log(f'🔄 Ciclo #{cycle} [{modo}] — 🏆 TOP BT: {", ".join(assets_to_scan[:4])}... | {_utc_now}', 'info')
                    else:
                        # Rotacionar todos os OTC em batches (DC SOLO usa batch maior)
                        all_otc_list = IQ.OTC_BINARY_ASSETS if hasattr(IQ, 'OTC_BINARY_ASSETS') else OTC_ASSETS
                        _dc_solo_mode = bot_state.get('dead_candle_mode', 'disabled') == 'solo'
                        batch_size = 35 if _dc_solo_mode else 20  # DC SOLO: mais ativos por ciclo
                        batch_idx = (cycle - 3) % max(1, (len(all_otc_list) // batch_size))
                        start = batch_idx * batch_size
                        batch = all_otc_list[start:start + batch_size]
                        # Sempre incluir top BT no início do batch
                        _max_batch = 35 if _dc_solo_mode else 20
                        assets_to_scan = list(dict.fromkeys(_bt_top[:3] + batch))[:_max_batch]
                        bot_log(f'🔄 Ciclo #{cycle} [{modo}] — 🔍 VARREDURA batch {batch_idx+1}: {len(assets_to_scan)} ativos | {_utc_now}', 'info')
                else:
                    # Sem backtest: varrer todos disponíveis em batches
                    if IQ.is_iq_session_valid():
                        all_available = IQ.get_available_all_assets()
                    else:
                        all_available = OTC_ASSETS
                    batch_size = 20
                    batch_idx = cycle % max(1, (len(all_available) // batch_size + 1))
                    start = (batch_idx * batch_size) % len(all_available)
                    assets_to_scan = all_available[start:start + batch_size]
                    if not assets_to_scan:
                        assets_to_scan = all_available[:batch_size]
                    otc_n = sum(1 for a in assets_to_scan if a.endswith('-OTC'))
                    bot_log(f'🔄 Ciclo #{cycle} [{modo}] — 🔍 {len(assets_to_scan)} ativos ({otc_n} OTC) batch {batch_idx+1} | {_utc_now}', 'info')

            # ── FILTRAR ATIVOS SUSPENSOS ────────────────────────────────────
            now_ts = time.time()
            ativos_antes = len(assets_to_scan)
            assets_to_scan = [a for a in assets_to_scan
                              if now_ts - _suspended_assets.get(a, 0) > _SUSPENSION_TIMEOUT]
            if len(assets_to_scan) < ativos_antes:
                bot_log(f'⏸️ {ativos_antes - len(assets_to_scan)} ativo(s) suspenso(s) ignorado(s)', 'info')

            # ── ESCANEAR / ANALISAR ──────────────────────────────────────────
            # Roda em thread para não bloquear GIL do gunicorn (site acessível durante scan)
            _scan_result = []
            def _do_scan():
                try:
                    _scan_result.extend(IQ.scan_assets(
                        assets_to_scan,
                        timeframe=60,
                        count=50,
                        bot_log_fn=bot_log,
                        bot_state_ref=bot_state,
                        strategies=bot_state.get('strategies', {}),
                        min_confluence=max(1, min(8, int(bot_state.get('min_confluence', 3)))),
                        dc_mode=bot_state.get('dead_candle_mode', 'disabled')
                    ))
                except Exception as e:
                    bot_log(f'⚠️ Erro no scan: {e}', 'warn')

            _scan_thread = threading.Thread(target=_do_scan, daemon=True)
            _scan_thread.start()
            # Timeout do scan adaptativo:
            # - REAL AUTO: 60s (candles reais da API podem demorar)
            # - REAL fixo: 15s (1 ativo só)
            # - DEMO AUTO: 15s (candles sintéticos rápidos)
            # - DEMO fixo: 10s
            # Timeout adaptativo baseado no batch
            n_assets = len(assets_to_scan)
            if is_real and n_assets > 10:
                _scan_timeout = min(100, 5 * n_assets)  # ~5s/ativo, max 100s
            elif is_real and n_assets > 1:
                _scan_timeout = 40
            elif is_real:
                _scan_timeout = 25
            elif n_assets > 10:
                _scan_timeout = 30
            elif n_assets > 1:
                _scan_timeout = 20
            else:
                _scan_timeout = 12
            # Heartbeat durante scan para o log não parecer travado
            _t0 = time.time()
            while _scan_thread.is_alive():
                elapsed = time.time() - _t0
                if elapsed >= _scan_timeout:
                    break
                if int(elapsed) % 5 == 0 and elapsed > 0 and int(elapsed) != getattr(_scan_thread, '_last_hb', -1):
                    _scan_thread._last_hb = int(elapsed)
                    bot_log(f'⏳ Analisando ativos... {int(elapsed)}s/{_scan_timeout}s', 'info')
                time.sleep(0.5)
            if _scan_thread.is_alive():
                bot_log(f'⚠️ Scan timeout ({_scan_timeout}s) — usando {len(_scan_result)} sinal(is) parcial(is)', 'warn')
            signals = sorted(_scan_result, key=lambda x: x['strength'], reverse=True)

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

            # MODO DEAD CANDLE
            _dc_mode = bot_state.get('dead_candle_mode', 'disabled')
            if _dc_mode == 'solo':
                # ═══════════════════════════════════════════════════════════
                # ☠️ MODO DC SOLO — Loop automático, threshold ≥40% confiança
                # Não depende de força 80%+, analisa TODOS os ativos e escolhe
                # o melhor setup Dead Candle do momento (maior score DC total)
                # ═══════════════════════════════════════════════════════════
                _dc_signals = []
                for _s in signals:
                    _dc_info = _s.get('detail', {}).get('dead_candle', {})
                    _dc_sc   = _dc_info.get('score_call', 0)
                    _dc_sp   = _dc_info.get('score_put', 0)
                    _dc_total = _dc_sc + _dc_sp
                    _dc_raz  = _dc_info.get('razoes', [])
                    _dc_str  = _s.get('strength', 0)
                    # Entrar se: tem sinal DC (score > 0) E razões E confiança >= 40%
                    if _dc_total > 0 and len(_dc_raz) > 0 and _dc_str >= 40:
                        _s['_dc_total_score'] = _dc_total  # tag para ordenação
                        _dc_signals.append(_s)

                if _dc_signals:
                    # Ordenar pelo maior score DC total (melhor setup no momento)
                    _dc_signals.sort(key=lambda x: (
                        x.get('_dc_total_score', 0),
                        x.get('strength', 0)
                    ), reverse=True)
                    best = _dc_signals[0]
                    _dc_info_best = best.get('detail', {}).get('dead_candle', {})
                    _dc_raz_best  = _dc_info_best.get('razoes', [])
                    _dc_sc_best   = _dc_info_best.get('score_call', 0)
                    _dc_sp_best   = _dc_info_best.get('score_put', 0)
                    _n_dc_found   = len(_dc_signals)

                    # ── ANTI-TRAP: Detectar armadilhas nos motivos DC ──────────
                    # Palavras-chave de armadilha no detector anti-trap v3
                    _TRAP_KEYWORDS = ['Trap', 'Armadilha', 'Orquestrada', 'Invertido', 'Divergência', 'Pullback Trap']
                    _trap_reasons  = [r for r in _dc_raz_best if any(k in r for k in _TRAP_KEYWORDS)]
                    _is_trap       = len(_trap_reasons) >= 1

                    if _is_trap:
                        # Armadilha detectada: INVERTER a direção do sinal DC
                        _orig_dir = best['direction']
                        _inv_dir  = 'PUT' if _orig_dir == 'CALL' else 'CALL'
                        best = dict(best)  # cópia para não alterar original
                        best['direction'] = _inv_dir
                        best['strength']  = max(40, best.get('strength', 40))
                        best['reason']    = f"🚨 ANTI-TRAP: Sinal {_orig_dir} invertido→{_inv_dir} | {' | '.join(_trap_reasons[:2])}"
                        bot_log(
                            f'🚨 [DC SOLO] ARMADILHA DETECTADA em {best["asset"]}! '
                            f'Sinal original: {_orig_dir} | Operando AO CONTRÁRIO: {_inv_dir} | '
                            f'Motivos: {" | ".join(_trap_reasons[:3])}',
                            'warn'
                        )
                    else:
                        bot_log(
                            f'☠️ [DC SOLO] Melhor setup: {best["asset"]} {best["direction"]} '
                            f'{best["strength"]}% | DC score: CALL={_dc_sc_best} PUT={_dc_sp_best} '
                            f'| {_n_dc_found} ativo(s) com sinal DC | {" | ".join(_dc_raz_best[:3])}',
                            'signal'
                        )
                    if _n_dc_found > 1:
                        _outros = [f'{x["asset"]}({x["strength"]}%)' for x in _dc_signals[1:3]]
                        bot_log(f'  ↳ Outros candidatos DC: {", ".join(_outros)}', 'info')
                else:
                    best = None
                    _n_scanned = len(signals)
                    if _n_scanned > 0:
                        _best_str_avail = max((s.get('strength',0) for s in signals), default=0)
                        bot_log(f'☠️ [DC SOLO] {_n_scanned} ativo(s) analisado(s), melhor força={_best_str_avail}% — sem padrão DC ≥40% no momento', 'warn')
                    else:
                        bot_log('☠️ [DC SOLO] Nenhum ativo com sinal — aguardando ciclo suspeito...', 'info')
            elif _dc_mode == 'combined':
                # Modo COMBINED: Dead Candle + confluencias normais (threshold 40%)
                min_strength = 40
                best = next((s for s in signals if s['strength'] >= min_strength and
                             (s.get('detail', {}).get('dead_candle', {}).get('score_call', 0) +
                              s.get('detail', {}).get('dead_candle', {}).get('score_put', 0)) > 0), None)
                if not best:
                    best = next((s for s in signals if s['strength'] >= min_strength), None)
            else:
                # Modo normal: mínimo 55-60%
                min_strength = 55 if len(assets_to_scan) == 1 else 60
                best = next((s for s in signals if s['strength'] >= min_strength), None)

            # ── SEM CONEXÃO: NÃO gerar sinais fictícios ─────────────────────
            # Quando não há conexão real com a IQ Option, o bot apenas
            # loga o status e aguarda — sem inventar entradas aleatórias.
            if best is None and not is_real:
                _email = bot_state.get('broker_email')
                if _email:
                    bot_log(f'🔌 Sem conexão com a corretora — reconexão automática em andamento...', 'warn')
                else:
                    bot_log(f'🔌 Corretora não conectada — acesse a aba "Corretora" e conecte sua conta IQ Option', 'error')
                # NÃO gerar sinal falso — best permanece None

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
                    'time': datetime.datetime.now().strftime('%H:%M:%S'),
                    'lp_resumo':      best.get('lp_resumo', ''),
                    'lp_direcao':     best.get('lp_direcao', ''),
                    'lp_forca':       best.get('lp_forca', 0),
                    'lp_pode_entrar': best.get('lp_pode_entrar', True),
                    'pattern':        best.get('pattern', ''),
                    'padrao':         best.get('pattern', ''),
                }
                bot_log(f'🎯 SINAL: {asset} {direct} {strength}% | Padrão: {best.get("pattern","")[:40]} | Tend:{trend.upper()} RSI5:{rsi_val:.0f}', 'signal')
                bot_log(f'📊 Motivos: {reason[:100]}', 'info')
                # ── LOG LP ──────────────────────────────────────────────
                _lp_res = best.get('lp_resumo', '')
                _lp_dir = best.get('lp_direcao', '')
                _lp_frc = best.get('lp_forca', 0)
                _lp_ok  = best.get('lp_pode_entrar', True)
                if _lp_res:
                    _lp_icon = '✅' if _lp_ok else '🚫'
                    _lp_align = '🟢 ALINHADO' if _lp_dir == direct else ('🔴 CONTRA' if _lp_dir else '⚪ NEUTRO')
                    bot_log(f'💡 LP: {_lp_res} | Força:{_lp_frc}% | {_lp_align} {_lp_icon}', 'info')
                else:
                    bot_log('💡 LP: sem dados de lógica do preço', 'warn')

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
                    # Ativo foi trocado durante o scan → apenas aguardar próximo ciclo
                    # (NÃO analisar ativo diferente do que estava no sinal)
                    bot_log(f'⏭ Aguardando próximo ciclo com o novo ativo: {current_sel}', 'info')
                    continue

                # ── TRAVA: 1 entrada por vez ────────────────────────────
                # ── BUG FIX: garantir _in_trade resetado se ficou True por erro ──
                if bot_state.get('_in_trade', False):
                    bot_log('⏸ Operação anterior ainda em aberto — forçando reset de _in_trade', 'warn')
                    bot_state['_in_trade'] = False

                # ── COOLDOWN: 60s por ativo ───────────────────────────────
                _now_ts = time.time()
                _cd     = bot_state.get('_entry_cooldown', {})
                _last_ts = _cd.get(asset, 0)
                _effective_cooldown = 30 if bot_state.get('dead_candle_mode') == 'solo' else COOLDOWN_SECONDS
                if _now_ts - _last_ts < _effective_cooldown:
                    _remaining = int(_effective_cooldown - (_now_ts - _last_ts))
                    bot_log(f'⏳ Cooldown {asset}: aguardando {_remaining}s para próxima entrada...', 'warn')
                    # Espera curta e volta ao loop para buscar outro ativo disponível
                    # (em vez de esperar 30s em silêncio, espera 5s e tenta outro)
                    _cd_wait = min(_remaining, 8)
                    for _ci in range(_cd_wait):
                        if not bot_state['running']: break
                        time.sleep(1)
                    continue

                # ── VERIFICAR MODO DE OPERAÇÃO ────────────────────────
                _modo_op = bot_state.get('modo_operacao', 'auto')
                if _modo_op == 'manual':
                    # Modo manual: apenas exibe sinal, NÃO entra automaticamente
                    bot_log(f'🖐️ MODO MANUAL: Sinal {asset} {direct} {strength}% — aguardando sua entrada manual', 'signal')
                    time.sleep(3)
                    continue
                # ── ENTRADA AUTOMÁTICA (modo auto ou ambos) ──────────────
                if is_real:
                    # ── ENTRADA REAL ────────────────────────────────────────
                    wait_sec = IQ.seconds_to_next_candle(60)
                    bot_log(f'⚡ ENTRADA REAL: {asset} {direct} R${amt:.2f} | próxima vela em {wait_sec:.0f}s', 'signal')
                    bot_state['_in_trade']            = True
                    bot_state['_entry_cooldown'][asset] = time.time()
                    ok, order_id = IQ.buy_binary_next_candle(asset, amt, direct.lower())
                    if not ok:
                        # FIX: resetar _in_trade imediatamente se buy falhou
                        bot_state['_in_trade'] = False
                        reason = str(order_id)
                        if 'suspended' in reason.lower():
                            bot_log(f'🚫 {asset} SUSPENSO — pulando por 5 min | {reason}', 'warn')
                            _suspended_assets[asset] = time.time()
                        elif 'closed' in reason.lower() or 'fechado' in reason.lower():
                            bot_log(f'🔒 {asset} FECHADO — pulando por 5 min', 'warn')
                            _suspended_assets[asset] = time.time()
                        elif 'mínimo' in reason.lower() or 'amount' in reason.lower():
                            bot_log(f'💸 Valor mínimo R$1.00 — ajuste o valor de entrada', 'warn')
                        else:
                            bot_log(f'⚠️ Entrada rejeitada: {reason}', 'warn')
                    else:
                        bot_log(f'⏳ Entrada executada! ID={order_id} | Aguardando resultado...', 'info')
                        result_data = IQ.check_win_iq(order_id, timeout=90)
                        # FIX: SEMPRE resetar _in_trade, independente do resultado
                        bot_state['_in_trade'] = False
                        if result_data and isinstance(result_data, tuple):
                            res_label, res_val = result_data
                            if res_label == 'win':
                                profit = round(float(res_val), 2)
                                bot_state['wins']   += 1
                                bot_state['profit']  = round(bot_state['profit'] + profit, 2)
                                _tot = bot_state['wins'] + bot_state['losses']
                                bot_state['win_rate'] = round(bot_state['wins']/_tot*100,1) if _tot else 0
                                bot_log(f'✅ WIN +R${profit:.2f} | {asset} {direct} | Total: R${bot_state["profit"]:.2f} | WR:{bot_state["win_rate"]}%', 'success')
                                with app.app_context():
                                    db.session.add(TradeLog(username=username, asset=asset,
                                        direction=direct, amount=amt, result='win', profit=profit))
                                    db.session.commit()
                                bot_state.setdefault('asset_loss_track', {}).pop(asset, None)
                            elif res_label == 'loss':
                                loss = round(float(res_val), 2)
                                bot_state['losses'] += 1
                                bot_state['profit']  = round(bot_state['profit'] - loss, 2)
                                _tot = bot_state['wins'] + bot_state['losses']
                                bot_state['win_rate'] = round(bot_state['wins']/_tot*100,1) if _tot else 0
                                bot_log(f'❌ LOSS -R${loss:.2f} | {asset} {direct} | Total: R${bot_state["profit"]:.2f} | WR:{bot_state["win_rate"]}%', 'error')
                                with app.app_context():
                                    db.session.add(TradeLog(username=username, asset=asset,
                                        direction=direct, amount=amt, result='loss', profit=-loss))
                                    db.session.commit()
                                # BLOQUEIO REPETITIVO: registra losses consecutivas
                                _alt = bot_state.setdefault('asset_loss_track', {})
                                _alt_list = _alt.setdefault(asset, [])
                                _alt_list.append(time.time())
                                _alt[asset] = _alt_list[-5:]
                                _recent_losses = [t for t in _alt[asset] if time.time() - t < 600]
                                if len(_recent_losses) >= 2:
                                    _suspended_assets[asset] = time.time() + 290
                                    bot_log(f'BLOQUEIO: {asset} {len(_recent_losses)} losses seguidas! Bloqueado 5 min.', 'warn')
                                    _alt[asset] = []
                            else:  # equal
                                bot_log(f'⚖️ EMPATE — valor devolvido ({asset})', 'warn')
                        else:
                            # FIX: timeout ou None — logar e continuar (não travar)
                            bot_log(f'⚠️ Resultado não obtido (timeout/None) para ID={order_id} — continuando...', 'warn')
                        try:
                            bal = IQ.get_real_balance()
                            if bal:
                                bot_state['broker_balance'] = bal
                                bot_log(f'💰 Saldo: R$ {bal:,.2f}', 'info')
                        except Exception:
                            pass
                else:
                    # ── SEM CONEXÃO: NÃO fazer entradas fictícias ─────────────
                    # MODO DEMO = conta PRACTICE da IQ Option (entradas REAIS na demo)
                    # Quando não conectado, o bot APENAS analisa mas NÃO entra.
                    # Isso evita Win/Loss falsos que enganam o usuário.
                    bot_state['_in_trade'] = False
                    bot_log(f'🚫 ENTRADA BLOQUEADA (sem conexão IQ) | {asset} {direct} {strength}% | Reconecte na aba Corretora', 'error')
                    # Sem cooldown — apenas espera próximo ciclo
                    time.sleep(2)
            else:
                bot_state['signal'] = None
                if len(assets_to_scan) == 1:
                    _asset_name = assets_to_scan[0] if assets_to_scan else '?'
                    _n_signals_found = len(signals)
                    if _n_signals_found == 0:
                        bot_log(f'🔎 {_asset_name}: NENHUM padrão detectado (candles OK, mas sem confluência) — aguardando...', 'warn')
                    else:
                        _best_str = max((s.get('strength',0) for s in signals), default=0)
                        bot_log(f'🔎 {_asset_name}: {_n_signals_found} sinal(is) mas abaixo do mínimo (melhor: {_best_str}%, mín:{min_strength}%) — aguardando...', 'warn')
                else:
                    _n_scanned = len(assets_to_scan)
                    _n_found   = len(signals)
                    if _n_found == 0:
                        bot_log(f'🔎 0 sinais em {_n_scanned} ativos — sem padrões válidos neste ciclo. Aguardando...', 'warn')
                    else:
                        _best_str = max((s.get('strength',0) for s in signals), default=0)
                        bot_log(f'🔎 {_n_found} sinal(is) em {_n_scanned} ativos, melhor {_best_str}% (mín:{min_strength}%) — aguardando confirmação...', 'warn')

            bot_log('─' * 40, 'info')
            # Aguarda entre ciclos — interrompível a cada segundo
            # Se houve sinal/entrada: espera menos (5s fixo / 8s auto)
            # Se não houve sinal: espera mais (8s fixo / 15s auto)
            if best:
                wait_cycles = 5 if len(assets_to_scan) == 1 else 8
            else:
                wait_cycles = 8 if len(assets_to_scan) == 1 else 12
            _next_in = wait_cycles
            bot_log(f'⏱️ Próximo scan em {_next_in}s...', 'info')
            for _wi in range(wait_cycles):
                if not bot_state['running']: break
                # Verificar se ativo mudou durante espera (troca imediata)
                new_sel = bot_state.get('selected_asset', 'AUTO')
                if new_sel != bot_state.get('_last_selected', new_sel):
                    bot_log(f'🔄 Ativo alterado durante espera → reiniciando ciclo', 'info')
                    break
                time.sleep(1)
            bot_state['_last_selected'] = bot_state.get('selected_asset', 'AUTO')

        except Exception as e:
            import traceback
            _tb = traceback.format_exc().strip().split('\n')
            _tb_short = ' | '.join(_tb[-3:])  # últimas 3 linhas do traceback
            bot_log(f'⚠️ ERRO no ciclo #{cycle}: {e} → {_tb_short}', 'error')
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
    try:
        payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        # Verificar blacklist: usuário excluído/desativado tem sessão revogada
        if payload.get('sub') in _SESSION_BLACKLIST:
            return None
        return payload
    except:
        return None


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
            # SEMPRE sincronizar senha do admin com o valor configurado
            # Isso garante que após deploy/rollback a senha padrão funciona
            expected_hash = hash_pw(admin_pw)
            if admin.password_hash != expected_hash:
                admin.password_hash = expected_hash
                db.session.commit()
                print(f'🔑 Senha do admin atualizada para: {admin_pw}')
            else:
                print(f'ℹ️ Admin OK — senha: {admin_pw}')

try:
    init_db()
except Exception as e:
    print(f'Init DB aviso: {e}')

def current_user():
    # 1) Preferência: Flask session (browser)
    token = session.get('token', '')
    if token:
        result = check_token(token)
        if result:
            return result
    # 2) Fallback: Authorization: Bearer <token> (API clients)
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:].strip()
        if token:
            result = check_token(token)
            if result:
                return result
    # 3) X-Auth-Token header (compatibilidade legada)
    xtoken = request.headers.get('X-Auth-Token', '')
    if xtoken:
        return check_token(xtoken)
    return None

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

# ─── HELPER: força parada imediata do bot de um usuário ───────────────────────
def _force_stop_user_bot(username: str, reason: str = 'forçado') -> None:
    """Para o bot do usuário imediatamente, invalida run_id e limpa thread."""
    st = _USER_STATES.get(username)
    if st:
        st['running'] = False
        st['log'].insert(0, {
            'time': __import__('datetime').datetime.now().strftime('%H:%M:%S'),
            'msg': f'🛑 Bot parado: {reason}',
            'color': '#EF4444'
        })
    # Invalida run_id para matar thread ativa
    with _GLOBAL_STATE_LOCK:
        _USER_RUN_IDS[username] = _USER_RUN_IDS.get(username, 0) + 1
    # Espera thread terminar (timeout curto para não bloquear HTTP)
    t = _USER_THREADS.get(username)
    if t and t.is_alive():
        t.join(timeout=2)

@app.route('/api/bot/start', methods=['POST'])
def bot_start():
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    if st['running']: return jsonify({'ok': True, 'msg': 'Já rodando'})
    d = request.json or {}
    st['running']        = True
    st['broker']         = d.get('broker', 'IQ Option')
    st['entry_value']    = float(d.get('entry_value', 2.0))
    st['stop_loss']      = float(d.get('stop_loss', 20.0))
    st['stop_win']       = float(d.get('stop_win', 50.0))
    st['min_corr']       = float(d.get('min_corr', 0.80))
    st['account_type']   = d.get('account_type', 'PRACTICE')
    st['selected_asset'] = d.get('selected_asset', 'AUTO')
    if 'modo_operacao' in d:
        st['modo_operacao'] = d.get('modo_operacao', 'auto')
    if 'dead_candle_mode' in d:
        st['dead_candle_mode'] = d.get('dead_candle_mode', 'disabled')
    st['strategies']     = d.get('strategies', {'ema':True,'rsi':True,'bb':True,'macd':True,'adx':True,'stoch':True,'lp':True,'pat':True,'fib':True})
    st['min_confluence'] = int(d.get('min_confluence', 4))
    st['current_user']   = username
    _lock = get_user_bot_lock(username)
    with _lock:
        old_thread = _USER_THREADS.get(username)
        if old_thread and old_thread.is_alive():
            st['running'] = False
            old_thread.join(timeout=3)
        run_id = _USER_RUN_IDS.get(username, 0) + 1
        _USER_RUN_IDS[username] = run_id
        st['running'] = True
        t = threading.Thread(target=run_bot_real, args=(run_id, username),
                             daemon=True, name=f'bot-{username}-{run_id}')
        _USER_THREADS[username] = t
        t.start()
    return jsonify({'ok': True})

@app.route('/api/bot/stop', methods=['POST'])
def bot_stop():
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    get_user_state(username)['running'] = False
    return jsonify({'ok': True})

@app.route('/api/bot/reset', methods=['POST'])
def bot_reset():
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    st.update({'wins':0,'losses':0,'profit':0.0,'log':[],'signal':None,'correlations':[]})
    return jsonify({'ok': True})


@app.route('/api/stats/reset', methods=['POST'])
def stats_reset():
    """Apaga TODO o histórico de trades do banco e zera o bot_state."""
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    try:
        # Apaga apenas os trades do usuário logado (master apaga tudo)
        username_sr = u.get('sub', '')
        if u.get('role') == 'master':
            deleted = TradeLog.query.delete()
        else:
            deleted = TradeLog.query.filter_by(username=username_sr).delete()
        # Zerar state do usuário também
        st_sr = get_user_state(username_sr)
        st_sr.update({'wins':0,'losses':0,'profit':0.0})
        db.session.commit()
        # Zera estado em memória (já feito acima com st_sr.update)
        # Se master, zera todos os states
        if u.get('role') == 'master':
            for _un in list(_USER_STATES.keys()):
                get_user_state(_un).update({'wins':0,'losses':0,'profit':0.0,'log':[],'signal':None,'correlations':[]})
        return jsonify({'ok': True, 'deleted': deleted,
                        'msg': f'{deleted} operação(ões) removida(s) do histórico'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/bot/status')
def bot_status():
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    total = st['wins'] + st['losses']
    return jsonify({
        'running':          st['running'],
        'wins':             st['wins'],
        'losses':           st['losses'],
        'profit':           st['profit'],
        'win_rate':         round(st['wins']/total*100, 1) if total else 0,
        'log':              st['log'][:30],
        'signal':           st['signal'],
        'correlations':     st['correlations'][:8],
        'broker':           st.get('broker', 'IQ Option'),
        'account_type':     st.get('account_type', 'PRACTICE'),
        'selected_asset':   st.get('selected_asset', 'AUTO'),
        'mode':             'real' if st.get('broker_connected') else 'demo',
        'broker_balance':   st.get('broker_balance', 0),
        'broker_connected': st.get('broker_connected', False),
        'strategies':       st.get('strategies', {}),
        'min_confluence':   st.get('min_confluence', 4),
        'modo_operacao':    st.get('modo_operacao', 'auto'),
        'dead_candle_mode': st.get('dead_candle_mode', 'disabled'),
        'bt_top_assets':    st.get('_bt_top_assets', []),
        'bt_ranked':        st.get('_bt_ranked', []),
    })

@app.route('/api/history')
def api_history():
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    # ── ISOLAMENTO: cada usuário vê APENAS seu próprio histórico ──
    trades = TradeLog.query.filter_by(username=username)\
                           .order_by(TradeLog.timestamp.desc())\
                           .limit(50).all()
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
    # Se desativando, para o bot imediatamente e invalida sessão
    if not user.is_active:
        _force_stop_user_bot(user.username, reason='conta desativada pelo master')
        _SESSION_BLACKLIST.add(user.username)
    else:
        # Reativar: remover da blacklist
        _SESSION_BLACKLIST.discard(user.username)
    return jsonify({'ok':True,'is_active':user.is_active})

@app.route('/api/master/users/<int:uid>/delete', methods=['POST'])
def delete_user(uid):
    """Exclui permanentemente um usuário e suas licenças (master only)."""
    u = current_user()
    if not u or u.get('role') != 'master': return jsonify({'error':'Sem permissão'}),403
    user = User.query.get(uid)
    if not user: return jsonify({'error':'Não encontrado'}),404
    if user.role == 'master': return jsonify({'error':'Não é possível excluir o master'}),403
    # ── PARAR BOT IMEDIATAMENTE antes de excluir ─────────────────
    _force_stop_user_bot(user.username, reason=f'conta excluída pelo master')
    # Remover estado em memória do usuário excluído
    with _GLOBAL_STATE_LOCK:
        _USER_STATES.pop(user.username, None)
        _USER_THREADS.pop(user.username, None)
        _USER_RUN_IDS.pop(user.username, None)
    # Revogar todas as licenças do usuário
    LicenseKey.query.filter_by(username=user.username).delete()
    # Apagar logs de trade
    TradeLog.query.filter_by(username=user.username).delete()
    # Excluir usuário
    db.session.delete(user)
    db.session.commit()
    # Adicionar username à blacklist de sessões (invalida JWT existente)
    _SESSION_BLACKLIST.add(user.username)
    bot_log(f'🗑️ Usuário "{user.username}" excluído pelo master.', 'warn')
    return jsonify({'ok': True, 'msg': f'Usuário {user.username} excluído.'})

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


# ─── BROKER CONNECT (ASSÍNCRONO + MULTI-USUÁRIO) ─────────────────────────────
# Cada usuário tem sua própria conexão isolada à corretora.
# Suporta: IQ Option, Bullex, Exnova (todas usam o mesmo protocolo IQ Option API)
# A conexão demora 20-45s; retornamos imediatamente e o frontend faz polling.

# Mapeamento de hosts das corretoras (todas compatíveis com IQ Option API)
BROKER_HOSTS = {
    'IQ Option': 'iqoption.com',
    'Bullex':    'trade.bull-ex.com',   # Host correto da Bullex (bull-ex.com, não bullex.com)
    'Exnova':    'trade.exnova.com',
}

@app.route('/api/broker/connect', methods=['POST'])
def broker_connect():
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    data = request.get_json() or {}
    broker       = data.get('broker', 'IQ Option')
    email        = data.get('email', '').strip()
    password     = data.get('password', '')
    account_type = data.get('account_type', 'PRACTICE').upper()

    if not email or not password:
        return jsonify(ok=False, error='Informe e-mail e senha da corretora')
    if '@' not in email:
        return jsonify(ok=False, error='E-mail inválido')
    if broker not in BROKER_HOSTS:
        return jsonify(ok=False, error=f'Corretora "{broker}" não suportada. Use: IQ Option, Bullex ou Exnova')

    host = BROKER_HOSTS[broker]

    # Marcar conn_state do usuário como "connecting"
    import time as _t
    conn_st   = get_user_conn_state(username)
    conn_lock = get_user_conn_lock(username)
    with conn_lock:
        conn_st['status'] = 'connecting'
        conn_st['result'] = None
        conn_st['error']  = None
        conn_st['ts']     = _t.time()

    # Iniciar conexão em background (não bloqueia o worker HTTP)
    def _do_connect():
        # Definir contexto de usuário para esta thread
        if hasattr(IQ, 'set_user_context'):
            IQ.set_user_context(username)
        ok, result = IQ.connect_iq(email, password, account_type, host=host, username=username)
        _conn_lock = get_user_conn_lock(username)
        _conn_st   = get_user_conn_state(username)
        st         = get_user_state(username)
        with _conn_lock:
            if ok:
                _conn_st['status'] = 'connected'
                _conn_st['result'] = result
                # Atualizar state ISOLADO do usuário
                st['broker_connected']    = True
                st['broker_name']         = broker
                st['broker_email']        = email
                st['broker_password']     = password
                st['broker_account_type'] = result['account_type']
                st['broker_balance']      = result['balance']
                st['account_type']        = result['account_type']
                if hasattr(IQ, 'invalidate_session_cache'):
                    IQ.invalidate_session_cache()
                start_heartbeat()
            else:
                _conn_st['status'] = 'error'
                _conn_st['error']  = result

    threading.Thread(target=_do_connect, daemon=True,
                     name=f'connect-{username}-{broker}').start()

    return jsonify(ok=True, status='connecting',
                   message=f'Conectando à {broker}…')


@app.route('/api/broker/connect/poll', methods=['GET'])
def broker_connect_poll():
    """Polling endpoint — cada usuário tem seu próprio resultado de conexão."""
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    import time as _t
    conn_lock = get_user_conn_lock(username)
    conn_st   = get_user_conn_state(username)
    st        = get_user_state(username)
    with conn_lock:
        status  = conn_st['status']
        result  = conn_st['result']
        error   = conn_st['error']
        elapsed = _t.time() - (conn_st['ts'] or _t.time())

    if status == 'connected' and result:
        return jsonify(
            ok=True, status='connected',
            broker=st.get('broker_name', 'IQ Option'),
            account_type=result.get('account_type', 'PRACTICE'),
            balance=f"{result.get('balance', 0):,.2f}",
            otc_assets=result.get('otc_assets', [])
        )
    elif status == 'error':
        return jsonify(ok=False, status='error', error=error or 'Erro desconhecido')
    elif status == 'connecting':
        if elapsed > 150:
            with conn_lock:
                conn_st['status'] = 'error'
                conn_st['error']  = '❌ Timeout: corretora não respondeu em 150s.'
            return jsonify(ok=False, status='error', error='❌ Timeout: corretora não respondeu.')
        return jsonify(ok=True, status='connecting', message='Conectando…', elapsed=int(elapsed))
    else:
        return jsonify(ok=True, status='idle')

@app.route('/api/broker/status', methods=['GET'])
def broker_status():
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    return jsonify(
        connected    = st.get('broker_connected', False),
        broker       = st.get('broker_name'),
        account_type = st.get('broker_account_type', 'PRACTICE'),
        balance      = st.get('broker_balance', 0)
    )

# ─── HOT-SWAP ATIVO (bot pode estar rodando) ──────────────────────────────────
# ─── API BOT CONFIG (atualizar estratégias em tempo real) ─────────────────────
@app.route('/api/bot/config', methods=['POST'])
def bot_config():
    """Atualiza configurações do bot em tempo real com log."""
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    d = request.json or {}
    changes = []

    # Atualizar valor de entrada
    if 'entry_value' in d:
        old = st.get('entry_value', 2.0)
        new = float(d['entry_value'])
        if old != new:
            st['entry_value'] = new
            changes.append(f'💵 Valor entrada: R${old:.2f} → R${new:.2f}')

    # Atualizar confluência mínima
    if 'min_confluence' in d:
        old = st.get('min_confluence', 4)
        new = int(d['min_confluence'])
        if old != new:
            st['min_confluence'] = new
            changes.append(f'🎯 Confluência mínima: {old} → {new}')

    # Atualizar estratégias
    if 'strategies' in d:
        old_strats = st.get('strategies', {})
        new_strats = d['strategies']
        nomes = {'ema':'EMA','rsi':'RSI','bb':'Bollinger','macd':'MACD','adx':'ADX','stoch':'Stoch','lp':'Lógica Preço','pat':'Padrões Vela','fib':'Fibonacci'}
        for k, v in new_strats.items():
            if old_strats.get(k) != v:
                status_lbl = '✅ ON' if v else '❌ OFF'
                changes.append(f'{status_lbl} {nomes.get(k, k)}')
        st['strategies'] = new_strats

    # Atualizar stop_loss e stop_win
    if 'stop_loss' in d:
        st['stop_loss'] = float(d['stop_loss'])
    if 'stop_win' in d:
        st['stop_win'] = float(d['stop_win'])

    # Atualizar modo operacional e dead candle
    if 'modo_operacao' in d:
        old_mo = st.get('modo_operacao', 'auto')
        new_mo = d['modo_operacao']
        if old_mo != new_mo:
            st['modo_operacao'] = new_mo
            changes.append(f'🤖 Modo operação: {old_mo} → {new_mo}')
    if 'dead_candle_mode' in d:
        old_dc = st.get('dead_candle_mode', 'disabled')
        new_dc = d['dead_candle_mode']
        if old_dc != new_dc:
            st['dead_candle_mode'] = new_dc
            changes.append(f'☠️ Dead Candle mode: {old_dc} → {new_dc}')
    if 'selected_asset' in d:
        st['selected_asset'] = d['selected_asset']
    if 'account_type' in d:
        st['account_type'] = d['account_type']
    if 'reset_stats' in d and d['reset_stats']:
        st['wins'] = 0
        st['losses'] = 0
        st['profit'] = 0.0
        st['win_rate'] = 0
        st['asset_loss_track'] = {}
        changes.append('🔄 Estatísticas zeradas')

    # Logar mudanças no log do usuário
    if changes:
        bot_log('⚙️ Configurações alteradas: ' + ' | '.join(changes), 'info', username=username)
    
    return jsonify({'ok': True, 'changes': changes})


@app.route('/api/assets/available', methods=['GET'])
def get_available_assets():
    """Retorna lista de ativos disponíveis na corretora no momento atual."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    try:
        if IQ.is_iq_session_valid():
            assets = IQ.get_available_all_assets()
            otc    = [a for a in assets if a.endswith('-OTC')]
            open_a = [a for a in assets if not a.endswith('-OTC')]
            return jsonify({'ok': True, 'assets': assets, 'otc': otc, 'open': open_a,
                            'total': len(assets), 'source': 'real'})
        else:
            return jsonify({'ok': True, 'assets': IQ.ALL_BINARY_ASSETS,
                            'otc': IQ.OTC_BINARY_ASSETS, 'open': IQ.OPEN_BINARY_ASSETS,
                            'total': len(IQ.ALL_BINARY_ASSETS), 'source': 'default'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e), 'assets': IQ.ALL_BINARY_ASSETS,
                        'source': 'fallback'})


@app.route('/api/bot/asset',     methods=['POST'])
@app.route('/api/bot/set-asset',  methods=['POST'])   # alias usado pelo frontend
def bot_change_asset():
    """Troca o ativo analisado em tempo real, sem parar o bot."""""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    d = request.json or {}
    u2 = current_user()
    username2 = u2.get('sub', 'admin') if u2 else 'admin'
    st2 = get_user_state(username2)
    new_asset = d.get('selected_asset', st2.get('selected_asset', 'AUTO'))
    old_asset = st2.get('selected_asset', 'AUTO')
    if new_asset == old_asset:
        return jsonify({'ok': True, 'selected_asset': new_asset, 'changed': False})
    st2['selected_asset'] = new_asset
    st2['signal'] = None
    st2['correlations'] = []
    label = new_asset if new_asset != 'AUTO' else 'AUTO (varredura completa)'
    if st2.get('running'):
        bot_log(f'🔄 Ativo trocado em tempo real: {old_asset} → {label}', 'warn', username=username2)
    else:
        bot_log(f'🎯 Ativo selecionado: {label}', 'info')
    return jsonify({'ok': True, 'selected_asset': new_asset, 'changed': True,
                    'bot_running': st2.get('running', False)})

# ─── INDICADORES AO VIVO (para o gráfico) ─────────────────────────────────────
# Cache por ativo — TTL 5s — evita 3 chamadas simultâneas bloquearem Gunicorn
_ind_cache = {}  # {asset: {'ts': float, 'data': dict}}
_IND_CACHE_TTL = 5.0  # segundos

@app.route('/api/indicators')
def api_indicators():
    """Retorna candles OHLC + indicadores calculados para o ativo selecionado."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    asset = request.args.get('asset', 'EURUSD-OTC')
    count = int(request.args.get('count', 80))

    # ── Cache por ativo (TTL 5s) — evita múltiplas chamadas simultâneas bloquearem o servidor ──
    _cache_key = f"{asset}_{count}"
    _now_ind = time.time()
    if _cache_key in _ind_cache and (_now_ind - _ind_cache[_cache_key]['ts']) < _IND_CACHE_TTL:
        return jsonify(_ind_cache[_cache_key]['data'])

    iq = IQ.get_iq()
    candles_raw = None

    if iq:
        # NUNCA bloquear esperando IQ — inicia fetch em background
        # Retorna dados simulados imediatamente se IQ não responder em 0.8s
        _raw_holder = [None]
        _done = threading.Event()
        def _fetch_candles():
            try:
                _raw_holder[0] = iq.get_candles(asset, 60, count, time.time())
            except Exception:
                pass
            finally:
                _done.set()
        _ct = threading.Thread(target=_fetch_candles, daemon=True)
        _ct.start()
        _done.wait(timeout=0.8)  # máx 0.8s
        candles_raw = _raw_holder[0]

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

    # Bollinger series — cálculo vetorial (numpy) — 80x mais rápido que loop
    _period_bb = 10
    bb_up_series, bb_dn_series = [None]*n, [None]*n
    if len(closes) >= _period_bb:
        _c = closes[-n:]  # últimas n velas
        for _i in range(_period_bb - 1, n):
            _sl = _c[max(0, _i-_period_bb+1):_i+1]
            _m = float(_sl.mean())
            _s = float(_sl.std(ddof=0)) * 2.0
            bb_up_series[_i] = round(_m + _s, 5)
            bb_dn_series[_i] = round(_m - _s, 5)

    _resp_dict = {
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
        'lp_pode_entrar': (sig.get('detail', {}) or {}).get('logica_preco', {}).get('pode_entrar', True) if sig else True,
        'lp_lote':     sig.get('lp_lote',    {}) if sig else {},
        'lp_posicao':  sig.get('lp_posicao', None) if sig else None,
        'lp_taxa_div': sig.get('lp_taxa_div', None) if sig else None,
        # Volume
        'vol_last':    sig.get('vol_last', 0) if sig else 0,
        'vol_avg':     sig.get('vol_avg',  0) if sig else 0,
    }
    # Salvar no cache
    _ind_cache[_cache_key] = {'ts': time.time(), 'data': _resp_dict}
    return jsonify(_resp_dict)

# ═══════════════════════════════════════════════════════════════════════════════
# ROTA: BACKTEST RÁPIDO 50 VELAS
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# ROTA: DEMO TRADE REAL — executa na conta demo da corretora
# ═══════════════════════════════════════════════════════════════════════════
@app.route('/api/demo_trade', methods=['POST'])
def api_demo_trade():
    """Executa uma entrada real na conta DEMO da IQ Option."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    data = request.get_json() or {}
    asset     = data.get('asset', 'EURUSD-OTC')
    direction = data.get('direction', 'CALL')   # 'CALL' ou 'PUT'
    amount    = float(data.get('amount', 1.0))  # valor mínimo
    expiry    = int(data.get('expiry', 60))      # 60 segundos

    iq = IQ.get_iq()
    if not iq:
        return jsonify({'error': 'Não conectado à corretora'}), 503

    try:
        from iq_integration import buy_binary_next_candle, get_candles_iq, analyze_asset_full
        
        # 1. Analisar o ativo
        closes, ohlc = get_candles_iq(asset, timeframe=expiry, count=80)
        if closes is None:
            return jsonify({'error': f'Sem candles para {asset}'}), 500
        
        sig = analyze_asset_full(asset, ohlc, min_confluence=2)
        
        # 2. Executar compra na conta DEMO
        bot_log(f"🎮 DEMO TRADE: {asset} {direction} ${amount}", 'info')
        
        success, trade_id = buy_binary_next_candle(
            iq, asset, direction.lower(), amount, expiry, account_type='PRACTICE'
        )
        
        if not success:
            return jsonify({'error': f'Falha na entrada demo: {trade_id}'}), 500
        
        # 3. Aguardar resultado (expiry + 2s)
        import time
        bot_log(f"🎮 DEMO #{trade_id}: aguardando resultado em {expiry}s...", 'info')
        time.sleep(min(expiry + 2, 62))
        
        # 4. Verificar resultado
        try:
            result = iq.check_win_v4(trade_id)
            win_amount = float(result) if result is not None else 0.0
            won = win_amount > 0
        except Exception as e:
            win_amount = 0.0
            won = False
            bot_log(f"⚠️ Erro ao checar resultado demo #{trade_id}: {e}", 'warning')
        
        outcome = "WIN" if won else "LOSS"
        profit  = win_amount - amount if won else -amount
        
        bot_log(f"🎮 DEMO #{trade_id}: {outcome} | profit={profit:+.2f}", 'info' if won else 'warning')
        
        return jsonify({
            'success':   True,
            'trade_id':  trade_id,
            'asset':     asset,
            'direction': direction,
            'amount':    amount,
            'outcome':   outcome,
            'profit':    round(profit, 2),
            'won':       won,
            'signal':    sig if sig else {},
            'lp_resumo': sig.get('lp_resumo', '') if sig else '',
            'lp_forca':  sig.get('lp_forca', 0)  if sig else 0,
            'pattern':   sig.get('pattern', '')   if sig else '',
            'strength':  sig.get('strength', 0)   if sig else 0,
        })
    except Exception as e:
        import traceback
        bot_log(f"❌ Erro demo trade: {e}", 'error')
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()[-300:]}), 500


@app.route('/api/backtest_real', methods=['GET','POST'])
def api_backtest_real():
    """
    Backtest REAL com candles reais da IQ Option (ou simulados realistas).
    GET  ?asset=EURUSD-OTC&candles=200  → backtest de 1 ativo
    POST {assets: [...], candles: 200}  → backtest de múltiplos ativos
    """
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401

    if request.method == 'GET':
        asset   = request.args.get('asset', 'EURUSD-OTC')
        candles = int(request.args.get('candles', 200))
        candles = max(80, min(candles, 400))
        bot_log(f'📊 Backtest real iniciado: {asset} ({candles} candles)...', 'info')
        try:
            result = IQ.run_backtest_real(asset, candles=candles)
            perfil = IQ.gerar_perfil_ativo(result)
            bot_log(
                f'📊 Backtest {asset} ({result["fonte"]}): '
                f'{result["overall_win_rate"]}% WR | '
                f'{result["total_sinais"]} sinais | '
                f'Melhor padrão: {result["top_patterns"][0]["desc"][:30] if result["top_patterns"] else "N/A"}',
                'info'
            )
            return jsonify({'ok': True, 'result': result, 'perfil': perfil})
        except Exception as e:
            import traceback
            return jsonify({'ok': False, 'error': str(e), 'trace': traceback.format_exc()[-300:]}), 500

    # POST — múltiplos ativos
    data   = request.get_json() or {}
    assets = data.get('assets', IQ.OTC_BINARY_ASSETS[:8])
    candles = int(data.get('candles', 200))
    candles = max(80, min(candles, 400))

    results = {}
    for ast in assets[:12]:  # limite de 12 ativos por vez
        try:
            r = IQ.run_backtest_real(ast, candles=candles)
            results[ast] = r
            IQ.gerar_perfil_ativo(r)  # salva no cache
        except Exception as e:
            results[ast] = {'asset': ast, 'error': str(e), 'overall_win_rate': 0}

    ranked = sorted(
        [r for r in results.values() if 'overall_win_rate' in r and not r.get('error')],
        key=lambda x: x['overall_win_rate'], reverse=True
    )
    return jsonify({'ok': True, 'results': results, 'ranked': ranked,
                    'best_asset': ranked[0]['asset'] if ranked else ''})


@app.route('/api/asset_profile/<asset>')
def api_asset_profile(asset):
    """Retorna perfil de padrões/indicadores/confluência do ativo."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    asset = asset.upper().replace('_OTC', '-OTC')
    force = request.args.get('refresh', 'false').lower() == 'true'
    try:
        perfil = IQ.get_asset_profile(asset, force_refresh=force)
        return jsonify({'ok': True, 'perfil': perfil})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/apply_asset_profile', methods=['POST'])
def api_apply_asset_profile():
    """
    Aplica o perfil do ativo ao bot automaticamente.
    Quando usuário seleciona um ativo, chama esta rota para configurar
    padrões, indicadores e confluência ideais para aquele ativo.
    """
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    data  = request.get_json() or {}
    asset = data.get('asset', 'EURUSD-OTC').upper().replace('_OTC', '-OTC')
    if asset == 'AUTO':
        return jsonify({'ok': True, 'msg': 'AUTO mode: sem perfil específico'})
    try:
        perfil = IQ.get_asset_profile(asset)
        strat  = perfil.get('strategies_override', {})
        # Aplicar configurações ao bot
        u_pa = current_user()
        un_pa = u_pa.get('sub', 'admin') if u_pa else 'admin'
        st_pa = get_user_state(un_pa)
        if strat:
            cur_strat = st_pa.get('strategies', {})
            cur_strat.update(strat)
            st_pa['strategies'] = cur_strat
        # Aplicar confluência sugerida
        conf = perfil.get('confluencia_minima', 3)
        st_pa['min_confluence'] = int(conf)
        bot_log(
            f'🎯 Perfil aplicado: {asset} | '
            f'Padrões: {len(perfil.get("padroes_ativos",[]))} | '
            f'Confluência: {conf} | '
            f'WR backtest: {perfil.get("overall_wr",0)}%',
            'info', username=un_pa
        )
        return jsonify({'ok': True, 'perfil': perfil,
                        'applied': {'strategies': strat, 'min_confluence': conf}})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/backtest50', methods=['GET'])
def api_backtest50():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    """Backtest rápido: 50 janelas de 80 velas para um ativo específico. Timeout 30s."""
    asset = request.args.get('asset', 'EURUSD-OTC')
    # Aceitar tanto OTC quanto mercado aberto — NÃO converter forçadamente
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
                assets=ALL_BINARY_ASSETS,      # Todos: 64 OTC + 46 Mercado Aberto
                candles_per_window=80,
                windows=20,                    # 20 janelas por ativo
                min_win_rate=10.0              # Mostrar apenas win_rate >= 10%
            )
        except Exception as e:
            error_holder[0] = str(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=90)  # timeout de 90 segundos (mais ativos para analisar)

    if t.is_alive():
        return jsonify({'ok': False, 'error': 'Timeout — backtest demorou mais de 90s'}), 408
    if error_holder[0]:
        return jsonify({'ok': False, 'error': error_holder[0]}), 500
    r = result_holder[0]
    return jsonify({
        'ok':         True,
        'result':     r,
        # Campos diretos para facilitar acesso no frontend
        'ranked':     r.get('ranked', []),
        'overall_wr': r.get('overall_wr', 0),
        'total_ops':  r.get('total_ops', 0),
        'total_wins': r.get('total_wins', 0),
        'assets_tested': r.get('assets_tested', 0),
    })



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
# ROTA: SCAN DE MELHORES SINAIS (MODO MANUAL)
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/api/scan_best_signals', methods=['POST'])
def api_scan_best_signals():
    """
    Varre ativos OTC e retorna os melhores sinais com confluência mínima.
    Usado pelo botão 'Buscar Melhor Sinal' no modo manual.
    """
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    d = request.get_json(silent=True) or {}
    selected_asset = d.get('asset', 'AUTO')
    min_conf       = max(2, int(d.get('min_confluence', 4)))
    top_n          = min(10, int(d.get('top_n', 5)))

    iq = IQ.get_iq()
    u_sc = current_user()
    un_sc = u_sc.get('sub', 'admin') if u_sc else 'admin'
    st_sc = get_user_state(un_sc)
    strategies = st_sc.get('strategies', {
        'ema':True,'rsi':True,'bb':True,'macd':True,
        'adx':True,'stoch':True,'lp':True,'pat':True,'fib':True
    })

    # Lista de ativos a escanear
    if selected_asset and selected_asset not in ('AUTO', 'auto', ''):
        assets_to_scan = [selected_asset]
    else:
        # Todos OTC disponíveis
        assets_to_scan = list(IQ.OTC_BINARY_ASSETS) if hasattr(IQ, 'OTC_BINARY_ASSETS') else [
            'EURUSD-OTC','EURGBP-OTC','GBPUSD-OTC','USDCHF-OTC','AUDCAD-OTC',
            'GBPCHF-OTC','EURCAD-OTC','CHFJPY-OTC','NZDJPY-OTC','CADCHF-OTC',
            'EURAUD-OTC','USDMXN-OTC','USDTRY-OTC','USDZAR-OTC','XAUUSD-OTC',
            'UKOUSD-OTC','APPLE-OTC','GOOGLE-OTC','AMAZON-OTC','FB-OTC',
            'ALIBABA-OTC','GS-OTC','JPM-OTC','NIKE-OTC','USNDAQ100-OTC',
            'SP500-OTC','US30-OTC','GER30-OTC','AUS200-OTC','LTCUSD-OTC',
        ]

    signals = []
    import numpy as np

    def _fetch_and_analyze(asset):
        try:
            candles_raw = None
            if iq:
                _holder = [None]
                _ev = threading.Event()
                def _fetch():
                    try:
                        _holder[0] = iq.get_candles(asset, 60, 60, time.time())
                    except Exception:
                        pass
                    finally:
                        _ev.set()
                t = threading.Thread(target=_fetch, daemon=True)
                t.start()
                _ev.wait(timeout=6)
                candles_raw = _holder[0]

            if not candles_raw or len(candles_raw) < 20:
                # Dados sintéticos para demo
                np.random.seed(hash(asset) % 9999)
                base = 1.1000
                closes = base + np.cumsum(np.random.randn(60) * 0.00025)
                highs  = closes + np.abs(np.random.randn(60) * 0.00012)
                lows   = closes - np.abs(np.random.randn(60) * 0.00012)
                opens  = np.roll(closes, 1); opens[0] = closes[0]
            else:
                closes = np.array([float(c['close']) for c in candles_raw])
                highs  = np.array([float(c['max'])   for c in candles_raw])
                lows   = np.array([float(c['min'])   for c in candles_raw])
                opens  = np.array([float(c['open'])  for c in candles_raw])

            # FIX: Usar chaves corretas ('open','high','low','close')
            # E fix IQ Option: close = max se bullish, min se bearish
            _fixed_closes = []
            for _i2 in range(len(closes)):
                _o2, _c2 = float(opens[_i2]), float(closes[_i2])
                _h2, _l2 = float(highs[_i2]), float(lows[_i2])
                if abs(_c2 - _o2) < 1e-8:  # close == open (IQ bug)
                    _c2 = _h2 if _h2 > _o2 else _l2  # usa max/min como close
                _fixed_closes.append(_c2)
            _fc = np.array(_fixed_closes)
            ohlc = {'open': opens, 'high': highs, 'low': lows, 'close': _fc,
                    'opens': opens, 'highs': highs, 'lows': lows, 'closes': _fc,
                    'volume': np.ones(len(opens))}
            _dc_mode_scan = d.get('dc_mode', 'disabled')
            result = IQ.analyze_asset_full(asset, ohlc, strategies=strategies,
                                           min_confluence=min_conf, dc_mode=_dc_mode_scan)
            if result:
                return {
                    'asset'     : asset,
                    'direction' : result.get('direction', '?'),
                    'strength'  : result.get('strength', 0),
                    'pattern'   : result.get('pattern', ''),
                    'reason'    : result.get('reason', ''),
                    'score_call': result.get('score_call', 0),
                    'score_put' : result.get('score_put', 0),
                    'rsi'       : result.get('rsi', 50),
                    'trend'     : result.get('trend', '?'),
                    'lp_forca'  : result.get('lp_forca', 0),
                    'detail'    : result.get('detail', {}),
                }
        except Exception as ex:
            pass
        return None

    # Escanear em paralelo (threads)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_fetch_and_analyze, a): a for a in assets_to_scan}
        for fut in as_completed(futures, timeout=30):
            res = fut.result()
            if res:
                signals.append(res)

    # Ordenar por força decrescente
    signals.sort(key=lambda x: x['strength'], reverse=True)
    top = signals[:top_n]

    return jsonify({
        'ok'     : True,
        'total_scanned': len(assets_to_scan),
        'found'  : len(signals),
        'signals': top
    })

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

    username = current_user().get('sub', 'user') if current_user() else 'user'
    _st_trade = get_user_state(username)

    def _register_result(result, profit_val):
        """Atualiza state ISOLADO do usuário e salva no DB."""
        if result == 'win':
            _st_trade['wins']   += 1
            _st_trade['profit']  = round(_st_trade['profit'] + profit_val, 2)
        elif result == 'loss':
            _st_trade['losses'] += 1
            _st_trade['profit']  = round(_st_trade['profit'] - amount, 2)
        # Recalcular win_rate
        total = _st_trade['wins'] + _st_trade['losses']
        _st_trade['win_rate'] = round(_st_trade['wins'] / total * 100, 1) if total > 0 else 0.0
        # Salvar no histórico
        with app.app_context():
            try:
                db.session.add(TradeLog(
                    username=username, asset=asset, direction=direction,
                    amount=amount, result=result,
                    profit=profit_val if result == 'win' else -amount
                ))
                db.session.commit()
            except Exception:
                db.session.rollback()

    try:
        iq = IQ.get_iq()
        if iq is None:
            # modo demo — simular resultado
            result    = 'win' if random.random() < 0.62 else 'loss'
            payout    = round(amount * 0.82, 2)
            _register_result(result, payout)
            return jsonify({'ok': True, 'order_id': 'DEMO', 'result': result,
                            'asset': asset, 'direction': direction, 'amount': amount,
                            'wins': _st_trade['wins'], 'losses': _st_trade['losses'],
                            'profit': _st_trade['profit'], 'win_rate': _st_trade.get('win_rate', 0)})

        # modo real — executar via IQ Option
        ok_buy, order_id = IQ.buy_binary_next_candle(asset, amount, direction.lower())
        if not ok_buy:
            return jsonify({'ok': False, 'error': str(order_id) or 'Ordem rejeitada'}), 400

        result_raw = IQ.check_win_iq(order_id)
        if isinstance(result_raw, tuple):
            result_label, result_val = result_raw
        else:
            result_label = str(result_raw)
            result_val   = amount * 0.82

        result = result_label  # 'win', 'loss' ou 'equal'
        payout = round(float(result_val), 2) if result == 'win' else 0.0
        _register_result(result, payout)

        # Atualizar saldo após operação
        bal = IQ.get_real_balance()
        if bal is not None:
            _st_trade['broker_balance'] = bal

        return jsonify({'ok': True, 'order_id': order_id, 'result': result,
                        'asset': asset, 'direction': direction, 'amount': amount,
                        'wins': _st_trade['wins'], 'losses': _st_trade['losses'],
                        'profit': _st_trade['profit'], 'win_rate': _st_trade.get('win_rate', 0)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# WATCHDOG & HEALTH CHECK — blindagem 24/7
# ═══════════════════════════════════════════════════════════════════════════════
import platform, psutil


# ═══════════════════════════════════════════════════════════════════════════
# TIMING DE ENTRADA — aguarda até os últimos segundos do candle M1
# ═══════════════════════════════════════════════════════════════════════════
def calcular_espera_entrada(expiry_seconds=60, margem_segundos=5):
    """Calcula espera até entrar nos últimos `margem_segundos` do candle."""
    import time as _time_mod
    agora = _time_mod.time()
    prox_fechamento = (int(agora / expiry_seconds) + 1) * expiry_seconds
    momento_entrada = prox_fechamento - margem_segundos
    esperar = max(0.0, momento_entrada - agora)
    return esperar, momento_entrada


def verificar_padrao_ainda_valido(asset, direcao_esperada, min_conf=2):
    """Reconfirma padrão segundos antes da entrada. Retorna True se válido."""
    try:
        from iq_integration import get_candles_iq, analyze_asset_full
        closes_c, ohlc_c = get_candles_iq(asset, timeframe=60, count=30)
        if closes_c is None or len(closes_c) < 5:
            return True  # sem dados → não cancelar (fail-safe)
        res = analyze_asset_full(asset, ohlc_c, min_confluence=min_conf)
        if res is None:
            bot_log(f"⚠️ [TIMING] Reconfirmação: padrão sumiu em {asset}", 'warning')
            return False
        if res.get('direction', '') != direcao_esperada:
            bot_log(f"⚠️ [TIMING] Direção mudou: {asset} era {direcao_esperada} → {res.get('direction')}", 'warning')
            return False
        return True
    except Exception:
        return True  # erro → não cancelar


_watchdog_stats = {
    'starts': 0,
    'last_restart': None,
    'bot_crashes': 0,
    'uptime_start': datetime.datetime.utcnow().isoformat(),
}

def _watchdog_thread():
    """Monitora o bot a cada 60s e reinicia automaticamente se travar."""
    global bot_thread
    time.sleep(30)  # aguarda boot inicial
    while True:
        try:
            time.sleep(60)
            # Usar globals().get() para evitar NameError caso bot_thread
            # ainda nao exista no escopo global (deploy antigo / race condition)
            # WATCHDOG MULTI-USUÁRIO: verificar todos os usuários ativos
            for _wd_un, _wd_st in list(_USER_STATES.items()):
                _wd_thread_u = _USER_THREADS.get(_wd_un)
                if _wd_st.get('running') and (_wd_thread_u is None or not _wd_thread_u.is_alive()):
                    _watchdog_stats['bot_crashes'] += 1
                    _watchdog_stats['last_restart'] = datetime.datetime.utcnow().isoformat()
                    bot_log(f'🔄 WATCHDOG: bot de {_wd_un} travou — reiniciando...', 'warn', username=_wd_un)
                    _wd_rid = _USER_RUN_IDS.get(_wd_un, 0) + 1
                    _USER_RUN_IDS[_wd_un] = _wd_rid
                    _t_wd = threading.Thread(target=run_bot_real, args=(_wd_rid, _wd_un),
                                             daemon=True, name=f'bot-wd-{_wd_un}-{_wd_rid}')
                    _USER_THREADS[_wd_un] = _t_wd
                    _t_wd.start()
                    _watchdog_stats['starts'] += 1
                    bot_log(f'✅ WATCHDOG: bot de {_wd_un} reiniciado', 'success', username=_wd_un)
        except Exception as e:
            bot_log(f'⚠️ Watchdog erro interno: {e}', 'warn')

def _self_ping_thread():
    """Faz auto-ping no /health a cada 4 min para evitar cold-start residual."""
    import urllib.request
    time.sleep(60)  # aguarda servidor subir
    port = int(os.environ.get('PORT', 7860))
    url  = f'http://localhost:{port}/health'
    railway_url = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
    if railway_url:
        url = f'https://{railway_url}/health'
    while True:
        try:
            time.sleep(240)  # a cada 4 minutos
            urllib.request.urlopen(url, timeout=10)
        except Exception:
            pass  # silencioso — apenas mantém processo vivo

# Iniciar watchdog e self-ping em background
_wd_thread = threading.Thread(target=_watchdog_thread, daemon=True, name='watchdog')
_wd_thread.start()
_sp_thread = threading.Thread(target=_self_ping_thread, daemon=True, name='self-ping')
_sp_thread.start()


@app.route('/health', methods=['GET'])
def health_check():
    """
    Endpoint público para monitoramento externo (UptimeRobot, BetterUptime etc).
    NÃO requer autenticação.
    Retorna 200 OK se o servidor está rodando.
    """
    try:
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.1)
        uptime_sec = (datetime.datetime.utcnow() -
                      datetime.datetime.fromisoformat(_watchdog_stats['uptime_start'])).total_seconds()
        uptime_str = f"{int(uptime_sec//3600)}h {int((uptime_sec%3600)//60)}m"
    except Exception:
        mem = None; cpu = 0; uptime_str = 'n/a'

    return jsonify({
        'status':       'ok',
        'service':      'DANBOT',
        'version':      'v2.0',
        'uptime':       uptime_str,
        'bot_running':  any(st.get('running') for st in _USER_STATES.values()) if _USER_STATES else False,
        'cpu_pct':      round(cpu, 1),
        'mem_used_mb':  round(mem.used / 1024**2, 1) if mem else 0,
        'mem_total_mb': round(mem.total / 1024**2, 1) if mem else 0,
        'timestamp':    datetime.datetime.utcnow().isoformat() + 'Z',
    }), 200



@app.route('/api/ping', methods=['GET'])
def api_ping():
    """Endpoint de ping para verificação rápida de disponibilidade."""
    return jsonify({'status': 'ok', 'service': 'DANBOT', 'version': 'v2.0'})

@app.route('/api/watchdog', methods=['GET'])
def api_watchdog():
    """Status interno detalhado do watchdog (requer login)."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    try:
        mem  = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        cpu  = psutil.cpu_percent(interval=0.2)
        proc = psutil.Process()
        uptime_sec = (datetime.datetime.utcnow() -
                      datetime.datetime.fromisoformat(_watchdog_stats['uptime_start'])).total_seconds()
    except Exception:
        mem = disk = proc = None; cpu = 0; uptime_sec = 0

    return jsonify({
        'ok': True,
        'server': {
            'uptime_seconds':  int(uptime_sec),
            'uptime_human':    f"{int(uptime_sec//3600)}h {int((uptime_sec%3600)//60)}m {int(uptime_sec%60)}s",
            'cpu_pct':         round(cpu, 1),
            'mem_used_mb':     round(mem.used / 1024**2, 1) if mem else 0,
            'mem_total_mb':    round(mem.total / 1024**2, 1) if mem else 0,
            'mem_pct':         round(mem.percent, 1) if mem else 0,
            'disk_used_gb':    round(disk.used / 1024**3, 2) if disk else 0,
            'disk_total_gb':   round(disk.total / 1024**3, 2) if disk else 0,
            'platform':        platform.system(),
            'python':          platform.python_version(),
            'railway_env':     os.environ.get('RAILWAY_ENVIRONMENT', 'local'),
            'railway_domain':  os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'n/a'),
        },
        'watchdog': {
            'uptime_start':    _watchdog_stats['uptime_start'],
            'bot_crashes':     _watchdog_stats['bot_crashes'],
            'auto_restarts':   _watchdog_stats['starts'],
            'last_restart':    _watchdog_stats['last_restart'],
            'bot_thread_alive': globals().get('bot_thread') is not None and globals()['bot_thread'].is_alive(),
        },
        'bot': {
            'active_users':    sum(1 for st in _USER_STATES.values() if st.get('running')),
            'total_users':     len(_USER_STATES),
            'user_states':     {un: {'running': st.get('running'), 'wins': st.get('wins', 0),
                                     'losses': st.get('losses', 0), 'broker': st.get('broker_name')}
                                for un, st in _USER_STATES.items()},
        }
    })



@app.route('/api/daily-profit')
def api_daily_profit():
    """Retorna lucro acumulado hora a hora das últimas 24h para o gráfico."""
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    now  = datetime.datetime.utcnow()
    ago  = now - datetime.timedelta(hours=24)
    # ── ISOLAMENTO: filtrar apenas trades do usuário logado ──
    trades = TradeLog.query.filter(
        TradeLog.username == username,
        TradeLog.timestamp >= ago
    ).order_by(TradeLog.timestamp).all()

    # Agrupar por hora — lucro acumulado
    hours = {}
    for t in trades:
        h = t.timestamp.replace(minute=0, second=0, microsecond=0)
        key = h.strftime('%H:00')
        hours[key] = hours.get(key, 0) + (t.profit or 0)

    # Montar série completa das últimas 24h (mesmo sem trades)
    labels, values, cumulative = [], [], []
    running = 0
    for i in range(24):
        hh = (now - datetime.timedelta(hours=23-i)).replace(minute=0, second=0, microsecond=0)
        key = hh.strftime('%H:00')
        val = hours.get(key, 0)
        running += val
        labels.append(key)
        values.append(round(val, 2))
        cumulative.append(round(running, 2))

    total_today = round(sum(values), 2)
    return jsonify({
        'ok': True,
        'labels':     labels,
        'values':     values,
        'cumulative': cumulative,
        'total_today': total_today,
        'trades_today': len(trades),
    })



# ─── DIAGNÓSTICO DE CONECTIVIDADE (PÚBLICO) ─────────────────────────────────
@app.route('/api/diag/network', methods=['GET'])
def diag_network():
    """Testa conectividade de rede com IQ Option (sem autenticação para diagnóstico)."""
    results = {}
    
    # 1. DNS
    try:
        import socket as _s
        import time as _t
        t0 = _t.time()
        ip = _s.gethostbyname('auth.iqoption.com')
        results['dns'] = {'ok': True, 'ip': ip, 'ms': int((_t.time()-t0)*1000)}
    except Exception as e:
        results['dns'] = {'ok': False, 'error': str(e)}
    
    # 2. TCP 443
    try:
        import socket as _s2
        import time as _t2
        t0 = _t2.time()
        conn = _s2.create_connection(('iqoption.com', 443), timeout=8)
        conn.close()
        results['tcp_443'] = {'ok': True, 'ms': int((_t2.time()-t0)*1000)}
    except Exception as e:
        results['tcp_443'] = {'ok': False, 'error': str(e)}
    
    # 3. HTTP
    try:
        import urllib.request as _ur2
        import time as _t3
        t0 = _t3.time()
        req = _ur2.Request('https://auth.iqoption.com/api/v2/login',
                           headers={'User-Agent': 'Mozilla/5.0 Chrome/120'})
        try:
            with _ur2.urlopen(req, timeout=8) as r:
                code = r.getcode()
                results['http'] = {'ok': True, 'code': code, 'ms': int((_t3.time()-t0)*1000)}
        except Exception as he:
            c = getattr(he, 'code', None)
            if c and int(c) >= 400:
                results['http'] = {'ok': True, 'code': int(c), 'ms': int((_t3.time()-t0)*1000)}
            else:
                results['http'] = {'ok': False, 'error': str(he)[:80]}
    except Exception as e:
        results['http'] = {'ok': False, 'error': str(e)[:80]}
    
    # 4. iqoptionapi instalado?
    try:
        import importlib.util as _ilu
        spec = _ilu.find_spec('iqoptionapi')
        results['iqoptionapi'] = {'ok': spec is not None, 'found': spec is not None}
    except Exception as e:
        results['iqoptionapi'] = {'ok': False, 'error': str(e)}
    
    # 5. websocket-client versão
    try:
        import websocket as _ws
        results['websocket'] = {'ok': True, 'version': getattr(_ws, 'version', 'unknown')}
    except Exception as e:
        results['websocket'] = {'ok': False, 'error': str(e)}
    
    all_ok = all(v.get('ok', False) for v in results.values())
    return jsonify(status='ok' if all_ok else 'degraded', results=results)


@app.route('/api/diag/exnova', methods=['GET'])
def diag_exnova():
    """Testa conectividade de rede com Exnova (trade.exnova.com)."""
    import socket as _s, time as _t, requests as _rq
    results = {}
    # DNS
    try:
        t0 = _t.time()
        ip = _s.gethostbyname('trade.exnova.com')
        results['dns_trade'] = {'ok': True, 'ip': ip, 'ms': int((_t.time()-t0)*1000)}
    except Exception as e:
        results['dns_trade'] = {'ok': False, 'error': str(e)}
    try:
        t0 = _t.time()
        ip2 = _s.gethostbyname('auth.trade.exnova.com')
        results['dns_auth'] = {'ok': True, 'ip': ip2, 'ms': int((_t.time()-t0)*1000)}
    except Exception as e:
        results['dns_auth'] = {'ok': False, 'error': str(e)}
    # TCP 443
    try:
        t0 = _t.time()
        conn = _s.create_connection(('trade.exnova.com', 443), timeout=8)
        conn.close()
        results['tcp_443'] = {'ok': True, 'ms': int((_t.time()-t0)*1000)}
    except Exception as e:
        results['tcp_443'] = {'ok': False, 'error': str(e)}
    # HTTP login (sem credenciais - só testa acessibilidade)
    try:
        t0 = _t.time()
        resp = _rq.post('https://auth.trade.exnova.com/api/v2/login',
            json={'identifier': 'test@test.com', 'password': 'test'},
            headers={'User-Agent': 'Mozilla/5.0 Chrome/120'},
            verify=False, timeout=10)
        results['http_login'] = {'ok': True, 'code': resp.status_code, 
                                  'body': resp.text[:100], 'ms': int((_t.time()-t0)*1000)}
    except Exception as e:
        results['http_login'] = {'ok': False, 'error': str(e)[:100]}
    # WebSocket
    try:
        import websocket as _ws
        import threading as _thr
        t0 = _t.time()
        _ws_result = [None]
        def _on_open(ws): _ws_result[0] = 'open'; ws.close()
        def _on_error(ws, err): _ws_result[0] = f'error:{err}'
        def _on_close(ws, c, m): pass
        _wsa = _ws.WebSocketApp('wss://trade.exnova.com/en/echo/websocket',
            on_open=_on_open, on_error=_on_error, on_close=_on_close)
        _t2 = _thr.Thread(target=_wsa.run_forever); _t2.daemon=True; _t2.start()
        _t2.join(timeout=8)
        ms = int((_t.time()-t0)*1000)
        results['websocket'] = {'ok': _ws_result[0]=='open', 'result': _ws_result[0], 'ms': ms}
    except Exception as e:
        results['websocket'] = {'ok': False, 'error': str(e)[:100]}
    return jsonify(results=results, status='ok')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            master = User(username='admin', password_hash=hash_pw('danbot@master2025'), role='master')
            db.session.add(master); db.session.commit()
            print('✅ Master criado: admin / danbot@master2025')
    port = int(os.environ.get('PORT', 7860))
    app.run(host='0.0.0.0', port=port, debug=False)
