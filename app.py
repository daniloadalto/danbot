"""
DANBOT WEB v2.0 — Backend Flask
Bot de Arbitragem OTC para Opções Binárias
"""
from flask import Flask, render_template, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
import hashlib, uuid, datetime, os, jwt, secrets, threading, time, json, random, socket
import urllib.request, urllib.error
from datetime import timezone, timedelta as _timedelta

def _brt_now():
    """Retorna hora atual no fuso horário de Brasília (UTC-3)"""
    return (datetime.datetime.utcnow() - _timedelta(hours=3))

def _brt_str():
    """Retorna string de hora no fuso de Brasília (UTC-3)"""
    return _brt_now().strftime('%H:%M:%S')
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
        'strategies': {'i3wr':True,'ma':True,'rsi':True,'bb':True,'macd':True,'dead':True,'reverse':True,'detector28':True},
        'min_confluence': 3,
        'ui_last_ping': 0.0,
        'auto_stop_on_ui_disconnect': False,
        '_conn_cycle_failures': 0,
        '_in_trade': False,
        '_entry_cooldown': {},
        '_bt_top_assets': [],
        '_bt_ranked': [],
        '_suspended_assets': {},
        '_scan_revision': 0,
        # ── SELETOR DE ATIVOS (v3.3) ──────────────────────────────────────────
        # asset_selector_mode: 'auto' = bot escolhe tudo
        #                      'manual' = varrer apenas assets em asset_pool
        'asset_selector_mode':  'auto',
        # asset_pool: lista de ativos escolhidos manualmente (vazio = usa todos)
        'asset_pool':           [],
        # asset_filter: filtros rápidos aplicados sobre o pool
        # 'otc_only'   = somente ativos -OTC (24h)
        # 'open_only'  = somente mercado aberto (horário comercial)
        # 'all'        = sem filtro (mistura OTC + aberto)
        'asset_filter':         'all',
    }

# Armazenamento de estados por usuário
_USER_STATES    = {}   # {username: state_dict}
_USER_THREADS   = {}   # {username: Thread}
_USER_RUN_IDS   = {}   # {username: int}
_USER_BOT_LOCKS = {}   # {username: Lock}  — impede 2 instâncias
_USER_CONN_STATES = {} # {username: conn_state_dict}
_USER_CONN_LOCKS  = {} # {username: Lock}
_GLOBAL_STATE_LOCK = threading.Lock()  # protege criação de novas entradas
_SESSION_BLACKLIST = set()             # usernames com sessão revogada (logout/delete)
IQ._bot_state_ref = _USER_STATES

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
        'time': _brt_str(),
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
    bot_state['current_user'] = username
    bot_state['ui_last_ping'] = time.time()
    _suspended_assets = bot_state.setdefault('_suspended_assets', {})

    def _ui_alive(max_idle: int = 600) -> bool:
        if not bot_state.get('auto_stop_on_ui_disconnect', True):
            return True
        _last_ping = float(bot_state.get('ui_last_ping') or 0)
        if _last_ping <= 0:
            return True
        return (time.time() - _last_ping) <= max_idle

    def _should_abort_trade_wait() -> bool:
        if not bot_state.get('running', False):
            return True
        if run_id != 0 and run_id != _USER_RUN_IDS.get(username, 0):
            return True
        return not _ui_alive(600)

    # ── CLOSURE DE LOG ISOLADA POR USUÁRIO ──────────────────────────────────
    # Garante que todos os logs do bot_thread vão para o state correto do usuário
    def bot_log(msg, level='info'):
        colors = {'info':'#9CA3AF','success':'#10B981','error':'#EF4444','warn':'#F59E0B','signal':'#00D4FF'}
        color  = colors.get(level, '#9CA3AF')
        entry  = {'time': _brt_str(), 'msg': msg, 'color': color}
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
    def _run_initial_backtest(scope_override=None):
        if hasattr(IQ, 'set_user_context'):
            IQ.set_user_context(username)
        try:
            # bt_scope: 'otc'=apenas OTC, 'open'=apenas mercado aberto, 'all'=ambos
            _bt_scope = scope_override or bot_state.get('bt_scope', 'all')
            _all_bt = IQ.ALL_BINARY_ASSETS if hasattr(IQ, 'ALL_BINARY_ASSETS') else []
            _otc_bt  = IQ.OTC_BINARY_ASSETS if hasattr(IQ, 'OTC_BINARY_ASSETS') else [a for a in _all_bt if a.endswith('-OTC')]
            _open_bt = [a for a in _all_bt if not a.endswith('-OTC')]
            if _bt_scope == 'otc':
                _bt_assets = _otc_bt
                bot_log(f'🔬 Backtest: modo OTC ({len(_bt_assets)} ativos)', 'info')
            elif _bt_scope == 'open':
                _bt_assets = _open_bt or (IQ.OPEN_BINARY_ASSETS if hasattr(IQ,'OPEN_BINARY_ASSETS') else _all_bt)
                bot_log(f'🔬 Backtest: modo Mercado Aberto ({len(_bt_assets)} ativos)', 'info')
            else:
                _bt_assets = _all_bt or _otc_bt
                bot_log(f'🔬 Backtest: modo Todos ({len(_bt_assets)} ativos)', 'info')
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
        if not _ui_alive(600):
            bot_log('⚠️ Dashboard sem ping há mais de 10 minutos — entradas serão pausadas até a UI voltar', 'warn')
            time.sleep(2)
            continue
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
            _cycle_ts = _brt_str()
            bot_log(f'🔁 ── Ciclo #{cycle} iniciado às {_cycle_ts} ──', 'info')

            # Verificar conexão a cada ciclo com histerese — evita flapping/reconexão em falso positivo
            _broker_was_connected = bot_state.get('broker_connected', False)
            _live_session_ok = bool(IQ.is_iq_session_valid())
            if not _broker_was_connected and _live_session_ok:
                _resync_live_broker_state(username)
                bot_log('✅ Sessão IQ detectada e resincronizada automaticamente', 'success')
                _broker_was_connected = True
            _session_ok = _broker_was_connected and _live_session_ok
            _conn_fail_cycles = int(bot_state.get('_conn_cycle_failures', 0) or 0)
            is_real = _session_ok
            if _broker_was_connected and _session_ok:
                bot_state['_conn_cycle_failures'] = 0
            elif _broker_was_connected and not _session_ok:
                _conn_fail_cycles += 1
                bot_state['_conn_cycle_failures'] = _conn_fail_cycles
                if _conn_fail_cycles < 3:
                    bot_log(f'⚠️ Sessão IQ instável ({_conn_fail_cycles}/3) — aguardando novo ping antes de reconectar', 'warn')
                    is_real = False
                else:
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
                                bot_state['_conn_cycle_failures'] = 0
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
            _utc_now = _brt_now().strftime('%H:%M:%S BRT')
            _sec_next = IQ.seconds_to_next_candle(60)
            # ── MODO DE SELEÇÃO DE ATIVOS (v3.3) ─────────────────────────────
            _sel_mode        = bot_state.get('asset_selector_mode', 'auto')    # 'auto'|'manual'
            _bot_sel_mode    = bot_state.get('bot_selector_mode', 'auto_robot')  # 'auto_robot'|'auto_user'
            _asset_pool      = bot_state.get('asset_pool', [])                   # pool antigo (compatível)
            _user_asset_pool = bot_state.get('user_asset_pool', [])              # até 6 ativos do usuário
            _asset_filt      = bot_state.get('asset_filter', 'all')              # 'otc_only'|'open_only'|'all'
            _mkt_filt        = bot_state.get('asset_market_filter', 'all')       # 'otc'|'open'|'all'
            modo = 'REAL' if is_real else 'SEM CONEXÃO'

            def _apply_filter(asset_list, filt):
                """Aplica filtro OTC/Aberto/Todos sobre uma lista de ativos."""
                if filt in ('otc_only', 'otc'):
                    return [a for a in asset_list if a.endswith('-OTC')]
                if filt in ('open_only', 'open'):
                    return [a for a in asset_list if not a.endswith('-OTC')]
                return list(asset_list)  # 'all' — sem filtro

            def _interleave_market_assets(open_assets, otc_assets):
                open_assets = list(dict.fromkeys(open_assets or []))
                otc_assets = list(dict.fromkeys(otc_assets or []))
                prefer_open = 8 <= _brt_now().hour < 18
                primary, secondary = (open_assets, otc_assets) if prefer_open else (otc_assets, open_assets)
                mixed = []
                for i in range(max(len(primary), len(secondary))):
                    if i < len(primary):
                        mixed.append(primary[i])
                    if i < len(secondary):
                        mixed.append(secondary[i])
                return mixed

            # ── Determinar pool efetivo ─────────────────────────────────────────
            # Prioridade: user_asset_pool (modo auto_user) > selected_asset fixo > pool antigo > auto
            _effective_pool = _user_asset_pool if _user_asset_pool else _asset_pool

            # ── MODO AUTO-USUÁRIO (usuário escolhe ativos, robô analisa todos) ─────
            if _bot_sel_mode == 'auto_user' and _user_asset_pool:
                # Usuário escolheu até 6 ativos — bot analisa TODOS eles em cada ciclo
                _eff = _apply_filter(_user_asset_pool, _mkt_filt)
                if not _eff:
                    _eff = list(_user_asset_pool)
                assets_to_scan = _eff
                _n_otc = sum(1 for a in assets_to_scan if a.endswith('-OTC'))
                _n_open = len(assets_to_scan) - _n_otc
                bot_log(
                    f'🔄 Ciclo #{cycle} [{modo}] — 🎯 USUÁRIO: {len(assets_to_scan)} ativos '
                    f'({_n_otc} OTC + {_n_open} Aberto) | {_utc_now}',
                    'info'
                )

            elif selected_asset and selected_asset != 'AUTO':
                # ── ATIVO ÚNICO FIXO ───────────────────────────────────────────
                assets_to_scan = [selected_asset]
                tipo_label = 'OTC' if is_otc_asset else '🟢 Mercado Aberto'
                bot_log(f'🔄 Ciclo #{cycle} — {selected_asset} [{tipo_label}] | Vela em {_sec_next:.0f}s | {_utc_now}', 'info')

            elif _sel_mode == 'manual' and _effective_pool:
                # ── MODO MANUAL: pool definido pelo usuário ────────────────────
                _pool_filtered = _apply_filter(_asset_pool, _asset_filt)
                if not _pool_filtered:
                    # Se filtro esvaziar o pool, usar pool original
                    _pool_filtered = list(_asset_pool)
                _dc_solo_mode = bot_state.get('dead_candle_mode', 'disabled') == 'solo'
                batch_size = 35 if _dc_solo_mode else 20
                batch_idx  = cycle % max(1, (len(_pool_filtered) // batch_size + 1))
                start      = (batch_idx * batch_size) % max(1, len(_pool_filtered))
                assets_to_scan = _pool_filtered[start:start + batch_size]
                if not assets_to_scan:
                    assets_to_scan = _pool_filtered[:batch_size]
                _pool_otc_n = sum(1 for a in assets_to_scan if a.endswith('-OTC'))
                _pool_open_n = len(assets_to_scan) - _pool_otc_n
                filt_label = {'otc_only':'📡 OTC','open_only':'🟢 Aberto','all':'🌐 Todos'}.get(_asset_filt,'')
                bot_log(
                    f'🔄 Ciclo #{cycle} [{modo}] — 🎯 MANUAL {filt_label}: {len(assets_to_scan)} ativos '
                    f'({_pool_otc_n} OTC + {_pool_open_n} Aberto) batch {batch_idx+1} | {_utc_now}',
                    'info'
                )

            else:
                # ── MODO AUTO: bot escolhe melhor pool automaticamente ─────────
                _bt_top = bot_state.get('_bt_top_assets', [])

                # FIX: usar _mkt_filt (asset_market_filter) quando _asset_filt for 'all'
                # _asset_filt: 'otc_only'|'open_only'|'all'
                # _mkt_filt:   'otc'|'open'|'all'
                _eff_filt = _asset_filt if _asset_filt != 'all' else _mkt_filt

                # Definir pool base: ALL = OTC + Aberto ou filtrado
                if _eff_filt in ('open_only', 'open'):
                    _base_pool = list(IQ.OPEN_BINARY_ASSETS) if hasattr(IQ, 'OPEN_BINARY_ASSETS') else []
                elif _eff_filt in ('otc_only', 'otc'):
                    _base_pool = list(IQ.OTC_BINARY_ASSETS) if hasattr(IQ, 'OTC_BINARY_ASSETS') else []
                else:
                    # 'all' — OTC tem prioridade na madrugada, mistura durante dia
                    _base_pool = _interleave_market_assets(
                                  list(IQ.OPEN_BINARY_ASSETS) if hasattr(IQ, 'OPEN_BINARY_ASSETS') else [],
                                  list(IQ.OTC_BINARY_ASSETS) if hasattr(IQ, 'OTC_BINARY_ASSETS') else [])

                if _bt_top:
                    # Ciclos 1-2: top backtest para entrada rápida
                    if cycle <= 2:
                        _bt_top_filt = _apply_filter(_bt_top, _eff_filt) or _apply_filter(_bt_top, _mkt_filt) or _bt_top
                        assets_to_scan = _bt_top_filt
                        bot_log(
                            f'🔄 Ciclo #{cycle} [{modo}] — 🏆 TOP BT: {", ".join(assets_to_scan[:4])}... | {_utc_now}',
                            'info'
                        )
                    else:
                        _dc_solo_mode = bot_state.get('dead_candle_mode', 'disabled') == 'solo'
                        batch_size = 35 if _dc_solo_mode else 20
                        all_otc_list = _base_pool or (IQ.OTC_BINARY_ASSETS if hasattr(IQ,'OTC_BINARY_ASSETS') else [])
                        batch_idx = (cycle - 3) % max(1, (len(all_otc_list) // batch_size))
                        start = batch_idx * batch_size
                        batch = all_otc_list[start:start + batch_size]
                        _max_batch = 35 if _dc_solo_mode else 20
                        _bt_top_filt = _apply_filter(_bt_top[:3], _eff_filt) or _apply_filter(_bt_top[:3], _mkt_filt) or _bt_top[:3]
                        assets_to_scan = list(dict.fromkeys(_bt_top_filt + batch))[:_max_batch]
                        bot_log(
                            f'🔄 Ciclo #{cycle} [{modo}] — 🔍 AUTO batch {batch_idx+1}: '
                            f'{len(assets_to_scan)} ativos | {_utc_now}',
                            'info'
                        )
                else:
                    if IQ.is_iq_session_valid():
                        all_available = IQ.get_available_all_assets()
                        all_available = _apply_filter(all_available, _eff_filt) or _apply_filter(all_available, _mkt_filt) or all_available
                    else:
                        all_available = _base_pool or []
                    batch_size = 20
                    batch_idx  = cycle % max(1, (len(all_available) // batch_size + 1))
                    start      = (batch_idx * batch_size) % max(1, len(all_available))
                    assets_to_scan = all_available[start:start + batch_size]
                    if not assets_to_scan:
                        assets_to_scan = all_available[:batch_size]
                    otc_n = sum(1 for a in assets_to_scan if a.endswith('-OTC'))
                    bot_log(
                        f'🔄 Ciclo #{cycle} [{modo}] — 🔍 AUTO {len(assets_to_scan)} ativos '
                        f'({otc_n} OTC) batch {batch_idx+1} | {_utc_now}',
                        'info'
                    )

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
            _scan_revision = int(bot_state.get('_scan_revision', 0) or 0)
            _scan_interrupted = False
            def _do_scan():
                if hasattr(IQ, 'set_user_context'):
                    IQ.set_user_context(username)
                try:
                    # min_confluence adaptativo: 3 para mercado aberto, padrão para OTC
                    _base_conf = max(1, min(7, int(bot_state.get('min_confluence', 3))))
                    _open_assets_in_scan = [a for a in assets_to_scan if not a.endswith('-OTC')]
                    _all_open = len(assets_to_scan) > 0 and len(_open_assets_in_scan) == len(assets_to_scan)
                    _has_open = len(_open_assets_in_scan) > 0
                    # Se todos (ou maioria) são mercado aberto, reduzir confluência mínima
                    _scan_confluence = min(_base_conf, 3) if _all_open else _base_conf
                    _scan_result.extend(IQ.scan_assets(
                        assets_to_scan,
                        timeframe=60,
                        count=50,
                        bot_log_fn=bot_log,
                        bot_state_ref=bot_state,
                        scan_revision=_scan_revision,
                        strategies=bot_state.get('strategies', {}),
                        min_confluence=_scan_confluence,
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
                if not _ui_alive(90):
                    bot_log('🛑 Dashboard fechado durante o scan — cancelando ciclo por segurança', 'warn')
                    bot_state['running'] = False
                    break
                if int(bot_state.get('_scan_revision', 0) or 0) != _scan_revision:
                    _scan_interrupted = True
                    bot_log('🔄 Seleção de ativo alterada durante o scan — reiniciando análise imediatamente', 'warn')
                    break
                if elapsed >= _scan_timeout:
                    break
                if int(elapsed) % 5 == 0 and elapsed > 0 and int(elapsed) != getattr(_scan_thread, '_last_hb', -1):
                    _scan_thread._last_hb = int(elapsed)
                    bot_log(f'⏳ Analisando ativos... {int(elapsed)}s/{_scan_timeout}s', 'info')
                time.sleep(0.5)
            if _scan_thread.is_alive() and not _scan_interrupted:
                bot_log(f'⚠️ Scan timeout ({_scan_timeout}s) — usando {len(_scan_result)} sinal(is) parcial(is)', 'warn')
            if _scan_interrupted:
                time.sleep(0.2)
                continue
            if int(bot_state.get('_scan_revision', 0) or 0) != _scan_revision:
                bot_log('🔄 Seleção alterada ao final do scan — descartando sinais do ciclo anterior', 'warn')
                time.sleep(0.2)
                continue
            signals = sorted(_scan_result, key=lambda x: x['strength'], reverse=True)

            if selected_asset and selected_asset != 'AUTO':
                _signals_before_fix = len(signals)
                signals = [s for s in signals if s.get('asset') == selected_asset]
                if _signals_before_fix != len(signals):
                    bot_log(f'🎯 Filtro manual ativo: descartados {_signals_before_fix - len(signals)} sinal(is) fora de {selected_asset}', 'info')

            if is_real and signals:
                _blocked_trade_assets = []
                _tradeable_signals = []
                for _sig in signals:
                    _sig_asset = _sig.get('asset', '')
                    _is_open_now = IQ.is_binary_open(_sig_asset)
                    if _is_open_now is False:
                        _blocked_trade_assets.append(_sig_asset)
                    else:
                        _tradeable_signals.append(_sig)
                if _blocked_trade_assets:
                    bot_log(f'🚫 {len(_blocked_trade_assets)} sinal(is) ignorado(s) por ativo fechado/suspenso: {", ".join(_blocked_trade_assets[:4])}', 'warn')
                signals = _tradeable_signals

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
                candidate_signals = [
                    s for s in signals
                    if s['strength'] >= min_strength and
                    (s.get('detail', {}).get('dead_candle', {}).get('score_call', 0) +
                     s.get('detail', {}).get('dead_candle', {}).get('score_put', 0)) > 0
                ]
                if not candidate_signals:
                    candidate_signals = [s for s in signals if s['strength'] >= min_strength]
                best = candidate_signals[0] if candidate_signals else None
            else:
                # Modo normal: mínimo 55-60%
                min_strength = 55 if len(assets_to_scan) == 1 else 60
                candidate_signals = [s for s in signals if s['strength'] >= min_strength]
                best = candidate_signals[0] if candidate_signals else None

            if 'candidate_signals' not in locals():
                candidate_signals = [best] if best else []

            _force_fast_rescan = False

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

                # ── Coletar dados v3 do sinal ─────────────────────────────
                _v3_sig = best.get('super_signal', {}) or {}
                _v3_mods = best.get('v3_modules', {}) or {}
                _flipcoin_data = best.get('flipcoin', {}) or {}
                _v3_conf = best.get('v3_confidence', 0)
                _v3_sc = best.get('v3_score_call', 0)
                _v3_sp = best.get('v3_score_put', 0)

                # Resumo textual dos módulos v3 ativos
                _v3_summary = []
                for _mn, _mv in _v3_mods.items():
                    if isinstance(_mv, dict) and 'pts' in _mv and _mv.get('pts', 0) > 0:
                        _mdir = _mv.get('dir', '?')
                        _mpts = _mv.get('pts', 0)
                        _icon = '✅' if _mdir == direct else '⚡'
                        _v3_summary.append(f'{_mn}:{_mpts}pt{_icon}')

                _lp_lote = best.get('lp_lote', {}) or {}
                _lp_trigger_price = best.get('lp_trigger_price', _lp_lote.get('trigger_price'))
                _lp_entry_mode = best.get('lp_entry_mode', _lp_lote.get('entry_mode'))
                _lp_trigger_label = best.get('lp_trigger_label', _lp_lote.get('trigger_label'))
                _lp_trigger_wick_size = best.get('lp_trigger_wick_size', _lp_lote.get('trigger_wick_size'))
                bot_state['signal'] = {
                    'a1': asset, 'a2': best.get('detail', {}).get('tendencia_desc', '—'),
                    'd1': direct, 'd2': '—',
                    'z': strength, 'strength': strength,
                    'corr': best.get('score_call', 0),
                    'reason': reason,
                    'trend': trend,
                    'rsi': rsi_val,
                    'time': _brt_str(),
                    'lp_resumo':      best.get('lp_resumo', ''),
                    'lp_direcao':     best.get('lp_direcao', ''),
                    'lp_forca':       best.get('lp_forca', 0),
                    'lp_pode_entrar': best.get('lp_pode_entrar', True),
                    'lp_lote':        _lp_lote,
                    'lp_entry_mode':  _lp_entry_mode,
                    'lp_trigger_price': _lp_trigger_price,
                    'lp_trigger_label': _lp_trigger_label,
                    'lp_trigger_wick_size': _lp_trigger_wick_size,
                    'pattern':        best.get('pattern', ''),
                    'padrao':         best.get('pattern', ''),
                    # ── MÓDULOS v3 ───────────────────────────────────────────
                    'v3_confidence':  _v3_conf,
                    'v3_score_call':  _v3_sc,
                    'v3_score_put':   _v3_sp,
                    'v3_modules':     _v3_mods,
                    'v3_summary':     ' | '.join(_v3_summary[:8]),
                    'v3_aligned':     _v3_sig.get('aligned_modules', 0),
                    'v3_total':       _v3_sig.get('total_modules', 0),
                    # ── FLIPCOIN ─────────────────────────────────────────────
                    'flipcoin_ok':    not _flipcoin_data.get('is_flipcoin', False),
                }
                bot_log(f'🎯 SINAL: {asset} {direct} {strength}% | Padrão: {best.get("pattern","")[:40]} | Tend:{trend.upper()} RSI5:{rsi_val:.0f}', 'signal')
                bot_log(f'📊 Motivos: {reason[:100]}', 'info')
                # ── LOG MÓDULOS v3 ────────────────────────────────────────
                _v3_mods_log = best.get('v3_modules', {})
                _v3_c = best.get('v3_confidence', 0)
                _v3_dir = best.get('super_signal', {}).get('direction') if best.get('super_signal') else None
                if _v3_mods_log:
                    _parts = []
                    for _mn, _mv in _v3_mods_log.items():
                        if isinstance(_mv, dict) and 'pts' in _mv:
                            _mpts = _mv.get('pts', 0)
                            _mdir = _mv.get('dir', '?')
                            _icon = '✅' if _mdir == direct else '⚡'
                            _parts.append(f'{_mn[:10]}:{_mpts}pt{_icon}')
                    if _parts:
                        bot_log(f'🔬 v3 Módulos ({_v3_c}% confiança | {_v3_dir}): {" | ".join(_parts[:7])}', 'info')
                    # Casino guard e flipcoin
                    _cg = _v3_mods_log.get('casino_guard', {})
                    if _cg and not _cg.get('veto', False):
                        bot_log(f'🎰 Casino Guard OK | streak={_cg.get("streak",0)}', 'info')
                    _fc_log = best.get('flipcoin', {})
                    bot_log(f'🎲 FlipCoin: {"⚠️ DETECTADO" if _fc_log.get("is_flipcoin") else "✅ LIMPO"} | score={_fc_log.get("score",0)}/6', 'info')
                # ── LOG MÓDULO IMPULSO + 3 WICKS ─────────────────────────
                _lp_res = best.get('lp_resumo', '')
                _lp_dir = best.get('lp_direcao', '')
                _lp_frc = best.get('lp_forca', 0)
                _lp_ok  = best.get('lp_pode_entrar', True)
                _lp_trigger_price = best.get('lp_trigger_price', _lp_lote.get('trigger_price'))
                _lp_entry_mode = best.get('lp_entry_mode', _lp_lote.get('entry_mode'))
                _lp_trigger_label = best.get('lp_trigger_label', _lp_lote.get('trigger_label'))
                _lp_trigger_wick_size = best.get('lp_trigger_wick_size', _lp_lote.get('trigger_wick_size'))
                if _lp_res:
                    _lp_icon = '✅' if _lp_ok else '🚫'
                    _lp_align = '🟢 ALINHADO' if _lp_dir == direct else ('🔴 CONTRA' if _lp_dir else '⚪ NEUTRO')
                    _lp_extra = ''
                    if isinstance(_lp_trigger_price, (int, float)):
                        _lp_extra += f' | gatilho={_lp_trigger_price:.5f}'
                    if _lp_trigger_label:
                        _lp_extra += f' | {_lp_trigger_label}'
                    if isinstance(_lp_trigger_wick_size, (int, float)) and _lp_trigger_wick_size > 0:
                        _lp_extra += f' | wick={_lp_trigger_wick_size:.5f}'
                    bot_log(f'⚡ I3WR: {_lp_res} | Força:{_lp_frc}% | {_lp_align} {_lp_icon}{_lp_extra}', 'info')
                else:
                    bot_log('⚡ I3WR: sem setup Impulso + 3 Wicks no momento', 'warn')

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
                    # ══════════════════════════════════════════════════════════
                    # 🛡️ SAFETY LOCK — verificação de conexão imediatamente
                    # antes do trade (is_real foi definido no início do ciclo,
                    # mas o scan pode ter levado 30-60s — conexão pode ter caído)
                    # ══════════════════════════════════════════════════════════
                    if hasattr(IQ, 'invalidate_session_cache'):
                        IQ.invalidate_session_cache()
                    _conn_agora = bot_state.get('broker_connected', False) and IQ.is_iq_session_valid()
                    if not _conn_agora:
                        bot_log('🚫 [SAFETY LOCK] Sessão IQ instável neste exato momento — entrada adiada para evitar falso positivo', 'warn')
                        bot_state['broker_connected'] = False
                        if hasattr(IQ, 'invalidate_session_cache'):
                            IQ.invalidate_session_cache(username)
                        _launched_sl, _msg_sl = _kick_background_reconnect(username, reason='safety_lock')
                        if _launched_sl:
                            bot_log('🔁 Reconexão automática iniciada pelo safety lock', 'warn')
                        elif _msg_sl == 'already_connecting':
                            bot_log('⏳ Reconexão já estava em andamento — aguardando próxima verificação', 'info')
                        else:
                            bot_log('⚠️ Safety lock sem reconexão imediata — mantendo ciclo vivo para novo teste', 'warn')
                        time.sleep(2)
                        continue
                    # ── CHECK RUNNING ANTES DA ENTRADA (fix: bot para mas entra) ─
                    if not bot_state.get('running', False):
                        bot_log(f'🛑 Bot parado durante scan — entrada em {asset} CANCELADA', 'warn')
                        break
                    # ── ENTRADA REAL ────────────────────────────────────────
                    _trade_account = (bot_state.get('broker_account_type') or bot_state.get('account_type') or 'PRACTICE').upper()
                    wait_sec = IQ.seconds_to_next_candle(60)
                    _use_i3wr_touch = (
                        len(assets_to_scan) == 1
                        and bot_state.get('selected_asset', 'AUTO') != 'AUTO'
                        and _lp_entry_mode == 'wick_touch_retracement'
                        and isinstance(_lp_trigger_price, (int, float))
                        and _lp_dir == direct
                        and hasattr(IQ, 'buy_binary_retracement_touch')
                    )
                    if _use_i3wr_touch:
                        _lp_trigger_desc = _lp_trigger_label or 'melhor pavio das 3 velas'
                        bot_log(
                            f'🎯 ENTRADA REAL [{_trade_account}] por retração I3WR: {asset} {direct} R${amt:.2f} | '
                            f'aguardando toque em {_lp_trigger_price:.5f} ({_lp_trigger_desc}) até o fechamento atual',
                            'signal'
                        )
                    else:
                        bot_log(f'⚡ ENTRADA REAL [{_trade_account}]: {asset} {direct} R${amt:.2f} | próxima vela em {wait_sec:.0f}s', 'signal')
                    bot_state['_in_trade']              = True
                    bot_state['_entry_cooldown'][asset] = time.time()
                    if _use_i3wr_touch:
                        ok, order_id = IQ.buy_binary_retracement_touch(
                            asset,
                            amt,
                            direct.lower(),
                            _lp_trigger_price,
                            account_type=_trade_account,
                            should_abort=_should_abort_trade_wait,
                            trigger_label=_lp_trigger_label,
                            progress_cb=bot_log
                        )
                    else:
                        ok, order_id = IQ.buy_binary_next_candle(
                            asset,
                            amt,
                            direct.lower(),
                            account_type=_trade_account,
                            should_abort=_should_abort_trade_wait,
                            progress_cb=bot_log
                        )
                    if not ok:
                        # FIX: resetar _in_trade imediatamente se buy falhou
                        bot_state['_in_trade'] = False
                        reason = str(order_id)
                        if 'suspended' in reason.lower():
                            bot_log(f'🚫 {asset} SUSPENSO — pulando por 5 min | {reason}', 'warn')
                            _suspended_assets[asset] = time.time()
                            if bot_state.get('selected_asset', 'AUTO') == 'AUTO':
                                _force_fast_rescan = True
                                bot_log('↪️ Ativo suspenso no topo do ranking — novo scan imediato para buscar o próximo sinal válido', 'warn')
                        elif 'closed' in reason.lower() or 'fechado' in reason.lower():
                            bot_log(f'🔒 {asset} FECHADO — pulando por 5 min', 'warn')
                            _suspended_assets[asset] = time.time()
                            if bot_state.get('selected_asset', 'AUTO') == 'AUTO':
                                _force_fast_rescan = True
                                bot_log('↪️ Ativo fechado no topo do ranking — novo scan imediato para buscar alternativa', 'warn')
                        elif 'mínimo' in reason.lower() or 'amount' in reason.lower():
                            bot_log(f'💸 Valor mínimo R$1.00 — ajuste o valor de entrada', 'warn')
                        else:
                            bot_log(f'⚠️ Entrada rejeitada: {reason}', 'warn')
                    else:
                        bot_log(f'⏳ Entrada executada! ID={order_id} | Aguardando resultado...', 'info')
                        result_data = IQ.check_win_iq(order_id, timeout=90, progress_cb=bot_log)
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
            if _force_fast_rescan:
                wait_cycles = 1
            elif best:
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
            'time': _brt_str(),
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


def _kick_background_reconnect(username: str, broker: str = None, email: str = None,
                               password: str = None, account_type: str = None,
                               host: str = None, reason: str = 'manual'):
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
            if hasattr(IQ, 'invalidate_session_cache'):
                IQ.invalidate_session_cache(username)
            _set_conn_state(username, status='connected', result=result, error=None)
            if hasattr(IQ, 'start_heartbeat'):
                IQ.start_heartbeat()
        else:
            st_local['broker_connected'] = False
            _set_conn_state(username, status='error', result=None, error=result)

    threading.Thread(target=_do_connect, daemon=True, name=f'reconnect-{username}-{reason}').start()
    return True, 'connecting'


def _select_backtest_assets(scope: str = 'all', limit: int = None):
    if IQ and hasattr(IQ, 'ALL_BINARY_ASSETS') and IQ.ALL_BINARY_ASSETS:
        _all = list(IQ.ALL_BINARY_ASSETS)
    else:
        _all = list(ALL_BINARY_ASSETS or OTC_ASSETS)
    _otc = [a for a in _all if str(a).endswith('-OTC')]
    _open = [a for a in _all if not str(a).endswith('-OTC')]
    if scope == 'otc':
        assets = _otc or _all
    elif scope == 'open':
        assets = _open or _all[:20]
    else:
        if _otc and _open:
            cap = limit or 36
            half = max(6, cap // 2)
            assets = _otc[:half] + _open[:max(6, cap - half)]
        else:
            assets = _all
    if limit and len(assets) > limit:
        assets = assets[:limit]
    return assets or list(OTC_ASSETS[:30])


def _run_backtest_for_user(username: str, scope: str = None, reason: str = 'manual', force: bool = False):
    st = get_user_state(username)
    scope = scope or st.get('bt_scope', 'all')
    now_ts = time.time()
    last_scope = st.get('_bt_last_scope')
    last_ts = float(st.get('_bt_last_ts') or 0.0)
    if st.get('_bt_running'):
        return False, 'running'
    if not force and last_scope == scope and (now_ts - last_ts) < 20:
        return False, 'debounced'
    st['_bt_running'] = True
    st['_bt_last_scope'] = scope
    st['_bt_last_ts'] = now_ts

    def _job():
        try:
            _ust = get_user_state(username)
            _limit = 24 if scope == 'otc' else (18 if scope == 'open' else 30)
            _assets = _select_backtest_assets(scope, limit=_limit)
            bot_log(f'🔬 Backtest {reason} iniciando ({scope}): {len(_assets)} ativos...', 'info', username=username)
            if IQ and hasattr(IQ, 'run_backtest'):
                _res = IQ.run_backtest(assets=_assets, candles_per_window=80, windows=10, seed_base=42)
            else:
                from iq_integration import run_backtest as _run_bt_fn
                _res = _run_bt_fn(assets=_assets, candles_per_window=80, windows=10, seed_base=42)
            _ranked = _res.get('ranked', [])
            _top6 = [r['asset'] for r in _ranked[:6]]
            if _top6:
                _ust['_bt_top_assets'] = _top6
                _ust['_bt_ranked'] = _ranked[:10]
                bot_state['_bt_top_assets'] = _top6
                bot_state['_bt_ranked'] = _ranked[:10]
                bot_log(f'🏆 Backtest {reason} ({scope}) top6: {", ".join(_top6)}', 'success', username=username)
                for _i, _r in enumerate(_ranked[:6], 1):
                    bot_log(f'   {_i}. {_r["asset"]} — {_r["win_rate"]}% WR ({_r["ops"]} ops)', 'info', username=username)
            else:
                bot_log(f'⚠️ Backtest {reason} ({scope}) sem resultados', 'warn', username=username)
        except Exception as _e:
            bot_log(f'⚠️ Backtest {reason} erro: {_e}', 'warn', username=username)
        finally:
            _ust = get_user_state(username)
            _ust['_bt_running'] = False

    threading.Thread(target=_job, daemon=True, name=f'bt-{username}-{reason}-{scope}').start()
    return True, 'started'


def _resync_live_broker_state(username: str):
    """Sincroniza o state do usuário com a sessão real da IQ quando possível."""
    st = get_user_state(username)
    try:
        if hasattr(IQ, 'set_user_context'):
            IQ.set_user_context(username)
        live_ok = bool(IQ.is_iq_session_valid(username))
        if live_ok:
            st['broker_connected'] = True
            bal = IQ.get_real_balance()
            if bal is not None:
                st['broker_balance'] = bal
            result = {
                'balance': st.get('broker_balance', 0),
                'account_type': st.get('broker_account_type', st.get('account_type', 'PRACTICE')),
                'otc_assets': getattr(IQ, 'OTC_BINARY_ASSETS', [])
            }
            _set_conn_state(username, status='connected', result=result, error=None)
            if hasattr(IQ, 'start_heartbeat'):
                IQ.start_heartbeat()
            return True
        st['broker_connected'] = False
        conn_st = get_user_conn_state(username)
        if conn_st.get('status') == 'connected':
            _set_conn_state(username, status='idle', result=None, error=None)
        return False
    except Exception:
        return bool(st.get('broker_connected', False))


@app.route('/api/bot/start', methods=['POST'])
def bot_start():
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    if st['running']: return jsonify({'ok': True, 'msg': 'Já rodando'})
    d = request.json or {}
    st['running']        = True
    st['ui_last_ping']   = time.time()
    if 'auto_stop_on_ui_disconnect' in d:
        st['auto_stop_on_ui_disconnect'] = bool(d.get('auto_stop_on_ui_disconnect'))
    st['broker']         = d.get('broker', 'IQ Option')
    st['entry_value']    = float(d.get('entry_value', 2.0))
    st['stop_loss']      = float(d.get('stop_loss', 20.0))
    st['stop_win']       = float(d.get('stop_win', 50.0))
    st['min_corr']       = float(d.get('min_corr', 0.80))
    st['account_type']   = d.get('account_type', 'PRACTICE')
    if 'bot_selector_mode' in d and d['bot_selector_mode'] in ('auto_robot', 'auto_user', 'manual'):
        st['bot_selector_mode'] = d['bot_selector_mode']
    if 'asset_market_filter' in d and d['asset_market_filter'] in ('otc', 'open', 'all'):
        st['asset_market_filter'] = d['asset_market_filter']
    if 'bt_scope' in d and d['bt_scope'] in ('otc', 'open', 'all'):
        st['bt_scope'] = d['bt_scope']
    _requested_asset = str(d.get('selected_asset', st.get('selected_asset', 'AUTO')) or 'AUTO').strip().upper()
    if _requested_asset and _requested_asset != 'AUTO':
        st['bot_selector_mode'] = 'manual'
        st['asset_selector_mode'] = 'manual'
        st['selected_asset'] = _requested_asset
    elif st.get('bot_selector_mode') in ('auto_robot', 'auto_user'):
        st['asset_selector_mode'] = 'auto'
        st['selected_asset'] = 'AUTO'
    else:
        st['selected_asset'] = _requested_asset
    if 'modo_operacao' in d:
        st['modo_operacao'] = d.get('modo_operacao', 'auto')
    if 'dead_candle_mode' in d:
        st['dead_candle_mode'] = d.get('dead_candle_mode', 'disabled')
    # ── SELETOR DE ATIVOS ────────────────────────────────────────────────────────
    if 'asset_selector_mode' in d:
        mode_val = d['asset_selector_mode']
        if mode_val in ('auto', 'manual'):
            st['asset_selector_mode'] = mode_val
    if 'asset_pool' in d:
        pool_val = d['asset_pool']
        if isinstance(pool_val, list):
            # Sanitizar: apenas strings não vazias
            st['asset_pool'] = [str(a).strip().upper() for a in pool_val if str(a).strip()]
    if 'asset_filter' in d:
        filt_val = d['asset_filter']
        if filt_val in ('otc_only', 'open_only', 'all'):
            st['asset_filter'] = filt_val
    if st.get('selected_asset', 'AUTO') != 'AUTO':
        st['bot_selector_mode'] = 'manual'
        st['asset_selector_mode'] = 'manual'
    st['_scan_revision'] = int(st.get('_scan_revision', 0) or 0) + 1
    st['strategies']     = d.get('strategies', {'i3wr':True,'ma':True,'rsi':True,'bb':True,'macd':True,'dead':True,'reverse':True,'detector28':True})
    st['min_confluence'] = int(d.get('min_confluence', 3))
    st['current_user']   = username
    _live_ok = _resync_live_broker_state(username)
    if not _live_ok and st.get('broker_email') and st.get('broker_password'):
        _launched, _msg = _kick_background_reconnect(username, reason='bot_start')
        if _launched:
            bot_log('🔁 Reconexão automática iniciada antes do primeiro ciclo', 'warn', username=username)
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
    _force_stop_user_bot(username, reason='parado pelo usuário')
    return jsonify({'ok': True})

@app.route('/api/ui/ping', methods=['POST'])
def ui_ping():
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    st['ui_last_ping'] = time.time()
    return jsonify({'ok': True, 'ts': st['ui_last_ping']})

@app.route('/api/ui/disconnect', methods=['POST'])
def ui_disconnect():
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    st['ui_last_ping'] = 0.0
    if st.get('running'):
        _force_stop_user_bot(username, reason='dashboard fechado/desconectado')
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
@app.route('/api/status')
def bot_status():
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    if st.get('broker_connected') or st.get('broker_email'):
        _resync_live_broker_state(username)
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
        'min_confluence':   st.get('min_confluence', 3),
        'modo_operacao':    st.get('modo_operacao', 'auto'),
        'dead_candle_mode': st.get('dead_candle_mode', 'disabled'),
        'asset_selector_mode':  st.get('asset_selector_mode', 'auto'),
        'bot_selector_mode':    st.get('bot_selector_mode', 'auto_robot'),
        'ui_last_ping':         st.get('ui_last_ping', 0.0),
        'asset_pool':           st.get('asset_pool', []),
        'user_asset_pool':      st.get('user_asset_pool', []),
        'asset_filter':         st.get('asset_filter', 'all'),
        'asset_market_filter':  st.get('asset_market_filter', 'all'),
        'asset_pool_size':      len(st.get('asset_pool', [])),
        'bt_scope':             st.get('bt_scope', 'all'),
        'bt_top_assets':        st.get('_bt_top_assets', []),
        'bt_ranked':            st.get('_bt_ranked', []),
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

@app.route('/api/master/diag/iq-connect', methods=['POST'])
def master_diag_iq_connect():
    u = current_user()
    if not u or u.get('role') != 'master':
        return jsonify({'error':'Sem permissão'}),403

    d = request.get_json() or {}
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    broker = (d.get('broker') or st.get('broker') or 'IQ Option').strip() or 'IQ Option'
    email = (d.get('email') or st.get('broker_email') or '').strip()
    password = d.get('password') or st.get('broker_password') or ''
    account_type = (d.get('account_type') or st.get('broker_account_type') or 'PRACTICE').upper()
    host = BROKER_HOSTS.get(broker, 'iqoption.com')

    if not email or not password:
        return jsonify({'ok': False, 'error': 'Informe e-mail e senha da corretora'}), 400

    ip_info = {'ok': False, 'ip': None, 'error': None}
    for _url in ('https://api.ipify.org?format=json', 'https://ifconfig.me/all.json'):
        try:
            with urllib.request.urlopen(_url, timeout=12) as _resp:
                _raw = _resp.read().decode('utf-8', 'ignore')
                ip_info = {'ok': True, 'source': _url, 'raw': _raw[:500]}
                try:
                    _j = json.loads(_raw)
                    ip_info['ip'] = _j.get('ip_addr') or _j.get('ip')
                except Exception:
                    pass
                break
        except Exception as e:
            ip_info = {'ok': False, 'ip': None, 'error': repr(e), 'source': _url}

    socket_info = {'host': host, 'ok': False, 'resolved_ip': None, 'error': None}
    try:
        _resolved = socket.gethostbyname(host)
        socket_info['resolved_ip'] = _resolved
        _sock = socket.create_connection((host, 443), timeout=12)
        try:
            socket_info['local_addr'] = list(_sock.getsockname())
            socket_info['peer_addr'] = list(_sock.getpeername())
            socket_info['ok'] = True
        finally:
            _sock.close()
    except Exception as e:
        socket_info['error'] = repr(e)

    started = time.time()
    ok, reason = IQ.connect_iq(email, password, account_type=account_type, host=host, username=username, broker_name=broker)
    elapsed = round(time.time() - started, 2)

    result = {
        'ok': bool(ok),
        'broker': broker,
        'host': host,
        'account_type': account_type,
        'elapsed_s': elapsed,
        'reason': reason,
        'ip_info': ip_info,
        'socket_info': socket_info,
    }

    try:
        if ok:
            st['broker_connected'] = True
            st['broker'] = broker
            st['broker_name'] = broker
            st['broker_email'] = email
            st['broker_password'] = password
            st['broker_account_type'] = account_type
            st['account_type'] = account_type
            try:
                st['broker_balance'] = float(IQ.get_balance_iq(username=username) or 0.0)
            except Exception:
                pass
            result['balance'] = st.get('broker_balance')
            try:
                result['check_connect'] = bool(IQ.check_connect_iq(username=username))
            except Exception as e:
                result['check_connect_error'] = repr(e)
        else:
            st['broker_connected'] = False
    except Exception as e:
        result['state_update_error'] = repr(e)

    try:
        bot_log(f'🧪 Diagnóstico IQ ({broker}) via API master: ok={bool(ok)} host={host} tempo={elapsed}s', 'info', username=username)
    except Exception:
        pass

    return jsonify(result)

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
    st = get_user_state(username)
    st['broker_name'] = broker
    st['broker'] = broker
    st['broker_email'] = email
    st['broker_password'] = password
    st['broker_account_type'] = account_type
    st['account_type'] = account_type
    launched, _msg = _kick_background_reconnect(
        username,
        broker=broker,
        email=email,
        password=password,
        account_type=account_type,
        host=host,
        reason='broker_connect'
    )
    if not launched and _msg == 'already_connecting':
        return jsonify(ok=True, status='connecting', message=f'Conexão com {broker} já está em andamento…')

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

    if status != 'connecting' and (st.get('broker_connected') or st.get('broker_email')):
        if _resync_live_broker_state(username):
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
        return jsonify(ok=False, status='error', error=error or 'Falha na conexão com a corretora. Verifique e-mail, senha ou indisponibilidade temporária do servidor.')
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
    _resync_live_broker_state(username)
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
        old = st.get('min_confluence', 3)
        new = int(d['min_confluence'])
        if old != new:
            st['min_confluence'] = new
            changes.append(f'🎯 Confluência mínima: {old} → {new}')

    # Atualizar estratégias
    if 'strategies' in d:
        old_strats = st.get('strategies', {})
        new_strats = d['strategies']
        nomes = {'i3wr':'I3WR','ma':'Médias Móveis','rsi':'RSI','bb':'Bollinger','macd':'MACD','dead':'Dead Candle','reverse':'Reverse Psychology','detector28':'Detector 28'}
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
    """Troca o ativo analisado em tempo real, sem parar o bot."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    d = request.json or {}
    u2 = current_user()
    username2 = u2.get('sub', 'admin') if u2 else 'admin'
    st2 = get_user_state(username2)
    new_asset = d.get('selected_asset', st2.get('selected_asset', 'AUTO'))
    old_asset = st2.get('selected_asset', 'AUTO')
    forced_mode = d.get('bot_selector_mode')

    if new_asset != 'AUTO':
        st2['selected_asset'] = new_asset
        st2['bot_selector_mode'] = 'manual'
        st2['asset_selector_mode'] = 'manual'
    else:
        st2['selected_asset'] = 'AUTO'
        if forced_mode in ('auto_robot', 'auto_user'):
            st2['bot_selector_mode'] = forced_mode
        elif st2.get('bot_selector_mode') == 'manual':
            st2['bot_selector_mode'] = 'auto_robot'
        if st2.get('asset_selector_mode') == 'manual':
            st2['asset_selector_mode'] = 'auto'

    if st2['selected_asset'] == old_asset and forced_mode in (None, st2.get('bot_selector_mode')):
        return jsonify({'ok': True, 'selected_asset': st2['selected_asset'], 'changed': False,
                        'bot_selector_mode': st2.get('bot_selector_mode', 'auto_robot')})

    st2['_scan_revision'] = int(st2.get('_scan_revision', 0) or 0) + 1
    st2['signal'] = None
    st2['correlations'] = []
    label = st2['selected_asset'] if st2['selected_asset'] != 'AUTO' else 'AUTO (varredura completa)'
    if st2.get('running'):
        bot_log(f'🔄 Ativo trocado em tempo real: {old_asset} → {label}', 'warn', username=username2)
    else:
        bot_log(f'🎯 Ativo selecionado: {label}', 'info', username=username2)
    return jsonify({'ok': True, 'selected_asset': st2['selected_asset'], 'changed': True,
                    'bot_running': st2.get('running', False),
                    'bot_selector_mode': st2.get('bot_selector_mode', 'auto_robot')})

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
        'lp_entry_mode': sig.get('lp_entry_mode', None) if sig else None,
        'lp_trigger_price': sig.get('lp_trigger_price', None) if sig else None,
        'lp_trigger_label': sig.get('lp_trigger_label', None) if sig else None,
        'lp_trigger_wick_size': sig.get('lp_trigger_wick_size', None) if sig else None,
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
    """Backtest real de 1 ativo: usa candles reais IQ Option ou simulados realistas.
    Retorna: win_rate, ops, wins, losses, best_pattern.
    """
    asset = request.args.get('asset', 'EURUSD-OTC')
    candles = int(request.args.get('candles', 250))
    candles = max(80, min(candles, 400))
    try:
        result = IQ.run_backtest_real(asset, candles=candles)
        top_pats = result.get('top_patterns', [])
        best_pat = top_pats[0]['desc'] if top_pats else 'N/A'
        ops   = result.get('total_sinais', 0)
        wins  = result.get('total_wins',   0)
        losses = ops - wins
        wr    = result.get('overall_win_rate', 0.0)
        return jsonify({'ok': True, 'result': {
            'asset':        asset,
            'ops':          ops,
            'wins':         wins,
            'losses':       losses,
            'win_rate':     wr,
            'best_pattern': best_pat,
            'fonte':        result.get('fonte', 'simulado'),
            'candles':      result.get('candles_analisados', candles),
            'confluencia':  result.get('confluencia_sugerida', 2),
            'top_patterns': top_pats[:5],
        }})
    except Exception as e:
        import traceback
        return jsonify({'ok': False, 'error': str(e), 'trace': traceback.format_exc()[-200:]}), 500




# ═══════════════════════════════════════════════════════════════════════════════
# ROTA: BACKTESTING AUTOMÁTICO DOS 12 ATIVOS OTC
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/api/backtest', methods=['GET'])
def api_backtest():
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    st = get_user_state(username)
    scope = (request.args.get('bt_scope') or st.get('bt_scope', 'all') or 'all').strip().lower()
    if scope not in ('all', 'otc', 'open'):
        scope = 'all'
    """
    Backtest rápido para o botão do dashboard.
    Usa o escopo selecionado e uma amostra menor para evitar timeout.
    """
    if st.get('_bt_running') and st.get('_bt_ranked'):
        cached = list(st.get('_bt_ranked') or [])
        return jsonify({
            'ok': True,
            'cached': True,
            'scope': scope,
            'result': {'ranked': cached},
            'ranked': cached,
            'overall_wr': round(sum(float(r.get('win_rate', 0)) for r in cached[:6]) / max(1, min(6, len(cached))), 1) if cached else 0,
            'total_ops': sum(int(r.get('ops', 0)) for r in cached),
            'total_wins': sum(int(r.get('wins', 0)) for r in cached),
            'assets_tested': len(cached),
        })

    assets = _select_backtest_assets(scope, limit=(24 if scope == 'otc' else (18 if scope == 'open' else 30)))
    result_holder = [None]
    error_holder  = [None]

    def _run():
        try:
            result_holder[0] = run_backtest(
                assets=assets,
                candles_per_window=80,
                windows=8,
                min_win_rate=10.0
            )
        except Exception as e:
            error_holder[0] = str(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=55)

    if t.is_alive():
        return jsonify({'ok': False, 'error': f'Timeout — backtest ({scope}) demorou mais de 55s', 'scope': scope, 'assets_tested': len(assets)}), 408
    if error_holder[0]:
        return jsonify({'ok': False, 'error': error_holder[0], 'scope': scope}), 500
    r = result_holder[0] or {}
    ranked = list(r.get('ranked', []) or [])
    if ranked:
        st['_bt_ranked'] = ranked[:10]
        st['_bt_top_assets'] = [a.get('asset') for a in ranked[:6] if a.get('asset')]
    return jsonify({
        'ok':         True,
        'scope':      scope,
        'result':     r,
        'ranked':     ranked,
        'overall_wr': r.get('overall_wr', 0),
        'total_ops':  r.get('total_ops', 0),
        'total_wins': r.get('total_wins', 0),
        'assets_tested': r.get('assets_tested', len(assets)),
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
    min_conf       = max(1, int(d.get('min_confluence', 3)))
    top_n          = min(10, int(d.get('top_n', 5)))

    iq = IQ.get_iq()
    u_sc = current_user()
    un_sc = u_sc.get('sub', 'admin') if u_sc else 'admin'
    st_sc = get_user_state(un_sc)
    strategies = st_sc.get('strategies', {
        'i3wr':True,'ma':True,'rsi':True,'bb':True,'macd':True,
        'dead':True,'reverse':True,'detector28':True
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

    # FIX BUG: definir contexto do usuário para get_iq() retornar instância correta
    if hasattr(IQ, 'set_user_context'):
        IQ.set_user_context(username)

    try:
        iq = IQ.get_iq()
        if iq is None:
            # Sem conexão real — retornar erro em vez de simular
            return jsonify({
                'ok': False,
                'error': '⚠️ Sem conexão com a corretora. Acesse "Corretora" e conecte-se antes de operar manualmente.'
            }), 503

        # modo real — executar via IQ Option
        _trade_account = (_st_trade.get('broker_account_type') or _st_trade.get('account_type') or 'PRACTICE').upper()
        ok_buy, order_id = IQ.buy_binary_next_candle(asset, amount, direction.lower(), account_type=_trade_account)
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

@app.route('/api/debug-auth')
def debug_auth():
    """Diagnóstico de autenticação JWT - remover após debug."""
    token_hdr = request.headers.get('Authorization','').replace('Bearer ','').strip()
    if not token_hdr:
        token_hdr = request.headers.get('X-Auth-Token','')
    secret = app.config.get('SECRET_KEY','')
    result = {'secret_len': len(secret), 'token_len': len(token_hdr), 
              'secret_prefix': secret[:8], 'blacklist': list(_SESSION_BLACKLIST)}
    if not token_hdr:
        result['error'] = 'no token'
        return jsonify(result)
    try:
        import jwt as _jwt
        payload = _jwt.decode(token_hdr, secret, algorithms=['HS256'])
        result['ok'] = True
        result['payload'] = payload
        result['blacklisted'] = payload.get('sub') in _SESSION_BLACKLIST
    except Exception as e:
        result['ok'] = False
        result['error'] = str(e)
        result['exc_type'] = type(e).__name__
    return jsonify(result)

@app.route('/api/clear-blacklist')
def clear_blacklist():
    """Limpa o blacklist de sessões - usar apenas para debug."""
    old = list(_SESSION_BLACKLIST)
    _SESSION_BLACKLIST.clear()
    return jsonify({'ok': True, 'cleared': old, 'blacklist_now': list(_SESSION_BLACKLIST)})

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



# ═══════════════════════════════════════════════════════════════════════════════
# 🎯 ASSET SELECTOR — endpoints de suporte ao seletor de ativos
# ═══════════════════════════════════════════════════════════════════════════════

# Lista completa de ativos categorizados (para popular o seletor na UI)
_ALL_ASSET_CATEGORIES = None   # cache — populado na 1ª chamada

def _build_asset_categories():
    """Monta catálogo de ativos categorizados para o seletor manual."""
    global _ALL_ASSET_CATEGORIES
    otc_list  = list(IQ.OTC_BINARY_ASSETS)  if hasattr(IQ, 'OTC_BINARY_ASSETS')  else []
    open_list = list(IQ.OPEN_BINARY_ASSETS) if hasattr(IQ, 'OPEN_BINARY_ASSETS') else []

    def _cat(name):
        n = name.replace('-OTC','').replace('OTC','')
        if any(c in n for c in ['USD','EUR','GBP','JPY','AUD','NZD','CAD','CHF','PLN','SEK','NOK','DKK','TRY','MXN','SGD','HKD','ZAR']):
            if any(c in n for c in ['BTC','ETH','LTC','XRP','ADA','SOL','DOT','LINK','MATIC','AVAX','DOGE','SHIB',
                                     'NEAR','MANA','SAND','FIL','ATOM','UNI','SUSHI','AAVE','COMP','YFI','SNX',
                                     'GALA','IMX','APE','LDO','OP','ARB','SUI','SEI','TIA','BLUR','PYTH','JUP',
                                     'LABUBU','MELANIA','PEPE','TRUMP','BONK','WIF','POPCAT','BOME','MEW','NEIRO',
                                     'RAY','RAYDIUM','SATS','SAT','ORDI','RUNE','MATICA']):
                return 'Cripto OTC' if name.endswith('-OTC') else 'Cripto Aberto'
            return 'Forex OTC' if name.endswith('-OTC') else 'Forex Aberto'
        if any(c in n for c in ['AAPL','GOOGL','AMZN','MSFT','FB','TSLA','NFLX','NVDA','BABA','BAC','JPM',
                                  'GS','MS','C','WFC','AIG','AMD','INTC','QCOM','IBM']):
            return 'Ações OTC'
        if any(c in n for c in ['GER30','UK100','FR40','EU50','JP225','US500','US30','USTEC']):
            return 'Índices OTC'
        if any(c in n for c in ['XAUUSD','XAGUSD','XPTUSD','GOLD','SILVER','OIL','CRUDE']):
            return 'Commodities OTC'
        return 'Outros OTC' if name.endswith('-OTC') else 'Outros Aberto'

    categories = {}
    for a in otc_list:
        cat = _cat(a)
        categories.setdefault(cat, []).append({'name': a, 'type': 'OTC'})
    for a in open_list:
        cat = _cat(a)
        categories.setdefault(cat, []).append({'name': a, 'type': 'OPEN'})

    _ALL_ASSET_CATEGORIES = {
        'categories': categories,
        'total_otc': len(otc_list),
        'total_open': len(open_list),
        'total': len(otc_list) + len(open_list),
    }
    return _ALL_ASSET_CATEGORIES


@app.route('/api/assets/list', methods=['GET'])
def api_assets_list():
    """Lista todos os ativos disponíveis por categoria para o seletor manual."""
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    cats = _build_asset_categories()
    return jsonify(cats)


@app.route('/api/assets/selector', methods=['GET', 'POST'])
def api_assets_selector():
    """
    GET  — retorna config atual do seletor do usuário
    POST — atualiza seletor: mode, pool, filter
    """
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    username = u.get('sub', 'admin')
    st = get_user_state(username)

    if request.method == 'GET':
        return jsonify({
            'ok': True,
            'asset_selector_mode':  st.get('asset_selector_mode', 'auto'),
            'bot_selector_mode':    st.get('bot_selector_mode', 'auto_robot'),
            'asset_pool':           st.get('asset_pool', []),
            'asset_filter':         st.get('asset_filter', 'all'),
            'asset_pool_size':      len(st.get('asset_pool', [])),
            'user_asset_pool':      st.get('user_asset_pool', []),
            'user_pool_size':       len(st.get('user_asset_pool', [])),
            'asset_market_filter':  st.get('asset_market_filter', 'all'),
            'bt_scope':             st.get('bt_scope', 'all'),
            'selected_asset':       st.get('selected_asset', 'AUTO'),
        })

    d = request.get_json(silent=True) or {}
    changes = []

    if 'asset_selector_mode' in d:
        m = d['asset_selector_mode']
        if m in ('auto', 'manual'):
            st['asset_selector_mode'] = m
            changes.append(f'mode={m}')

    if 'asset_pool' in d:
        pool = d['asset_pool']
        if isinstance(pool, list):
            clean = [str(a).strip().upper() for a in pool if str(a).strip()]
            st['asset_pool'] = clean
            changes.append(f'pool={len(clean)} ativos')

    if 'asset_filter' in d:
        f2 = d['asset_filter']
        if f2 in ('otc_only', 'open_only', 'all'):
            st['asset_filter'] = f2
            changes.append(f'filter={f2}')

    # Atalhos rápidos por categoria
    if 'add_category' in d:
        cat_name = d['add_category']
        cats = _build_asset_categories()
        cat_assets = [a['name'] for a in cats.get('categories', {}).get(cat_name, [])]
        existing = st.get('asset_pool', [])
        merged = list(dict.fromkeys(existing + cat_assets))
        st['asset_pool'] = merged
        changes.append(f'add_category={cat_name}({len(cat_assets)} ativos)')

    if 'remove_category' in d:
        cat_name = d['remove_category']
        cats = _build_asset_categories()
        cat_assets = set(a['name'] for a in cats.get('categories', {}).get(cat_name, []))
        st['asset_pool'] = [a for a in st.get('asset_pool', []) if a not in cat_assets]
        changes.append(f'remove_category={cat_name}')

    if 'clear_pool' in d and d['clear_pool']:
        st['asset_pool'] = []
        changes.append('pool=limpo')

    if changes:
        mode_label = '🎯 MANUAL' if st.get('asset_selector_mode') == 'manual' else '🤖 AUTO'
        filt_label = {'otc_only':'📡 OTC','open_only':'🟢 Aberto','all':'🌐 Todos'}.get(st.get('asset_filter','all'),'')
        pool_sz = len(st.get('asset_pool', []))
        bot_log(
            f'⚙️ Seletor atualizado: {mode_label} {filt_label} | pool={pool_sz} ativos | {", ".join(changes)}',
            'info', username=username
        )

    # Handle new fields in POST
    if 'bot_selector_mode' in d:
        m2 = d['bot_selector_mode']
        if m2 in ('auto_robot', 'auto_user', 'manual'):
            st['bot_selector_mode'] = m2
            changes.append(f'bot_mode={m2}')
    if 'user_asset_pool' in d:
        pool2 = d['user_asset_pool']
        if isinstance(pool2, list):
            clean2 = [str(a).strip().upper() for a in pool2 if str(a).strip()]
            st['user_asset_pool'] = clean2[:6]
            changes.append(f'user_pool={len(clean2[:6])} ativos')
    if 'asset_market_filter' in d:
        fmkt = d['asset_market_filter']
        if fmkt in ('otc', 'open', 'all'):
            st['asset_market_filter'] = fmkt
            changes.append(f'market_filter={fmkt}')
    if 'bt_scope' in d:
        bts = d['bt_scope']
        if bts in ('otc', 'open', 'all'):
            st['bt_scope'] = bts
            changes.append(f'bt_scope={bts}')

    # ── Sync bot_state global (retrocompat) ──────────────────────────────
    global bot_state
    _bt_scope_changed = False
    for _sk in ('bot_selector_mode', 'user_asset_pool', 'asset_market_filter', 'bt_scope'):
        if _sk in d:
            _old_val = bot_state.get(_sk)
            bot_state[_sk] = st.get(_sk, bot_state.get(_sk))
            if _sk == 'bt_scope' and bot_state[_sk] != _old_val:
                _bt_scope_changed = True

    # Se bt_scope mudou → dispara novo backtest automático (com debounce)
    if _bt_scope_changed and IQ:
        _sc = bot_state.get('bt_scope', 'all')
        _run_backtest_for_user(username, scope=_sc, reason='automático', force=False)

    if 'selected_asset' in d:
        _req_asset = str(d.get('selected_asset') or 'AUTO').strip().upper()
        if _req_asset and _req_asset != 'AUTO':
            st['selected_asset'] = _req_asset
            st['bot_selector_mode'] = 'manual'
            st['asset_selector_mode'] = 'manual'
    if st.get('bot_selector_mode') in ('auto_robot', 'auto_user'):
        st['asset_selector_mode'] = 'auto'
        st['selected_asset'] = 'AUTO'
    elif st.get('selected_asset', 'AUTO') != 'AUTO':
        st['asset_selector_mode'] = 'manual'
    if changes or 'selected_asset' in d or 'bot_selector_mode' in d or 'asset_selector_mode' in d:
        st['_scan_revision'] = int(st.get('_scan_revision', 0) or 0) + 1

    return jsonify({
        'ok': True,
        'changes': changes,
        'asset_selector_mode': st.get('asset_selector_mode', 'auto'),
        'bot_selector_mode':   st.get('bot_selector_mode', 'auto_robot'),
        'asset_pool':          st.get('asset_pool', []),
        'user_asset_pool':     st.get('user_asset_pool', []),
        'asset_filter':        st.get('asset_filter', 'all'),
        'asset_market_filter': st.get('asset_market_filter', 'all'),
        'asset_pool_size':     len(st.get('asset_pool', [])),
        'bt_scope':            st.get('bt_scope', 'all'),
        'selected_asset':      st.get('selected_asset', 'AUTO'),
    })


@app.route('/api/backtest/force', methods=['POST'])
def api_backtest_force():
    """Força re-execução do backtest com o bt_scope atual. Funciona sem broker (usa dados simulados)."""
    u = current_user()
    if not u: return jsonify({'error':'não autorizado'}), 401
    username = u.get('sub','admin')
    _user_st_ref = get_user_state(u.get('sub','admin'))
    _sc = _user_st_ref.get('bt_scope', bot_state.get('bt_scope','all'))
    started, why = _run_backtest_for_user(username, scope=_sc, reason='forçado', force=False)
    if not started and why in ('running', 'debounced'):
        return jsonify({'ok': True, 'msg': f'Backtest já em andamento/recente (escopo={_sc})', 'bt_scope': _sc, 'skipped': True})
    return jsonify({'ok': True, 'msg': f'Backtest iniciado (escopo={_sc})', 'bt_scope': _sc})


# ═══════════════════════════════════════════════════════════════════════════════
# 🔍 BROKER BUG TRACKER — Rastreador de Brechas e Anomalias
# Varre ativos OTC e abertos em busca de comportamentos anômalos da corretora:
# candle congelado, repetição de padrão, atraso, movimento fora do comum
# ═══════════════════════════════════════════════════════════════════════════════

_bug_tracker_results = []   # cache do último scan
_bug_tracker_running  = False
_bug_tracker_log      = []

def _brt_bug():
    import datetime
    from datetime import timedelta
    return (datetime.datetime.utcnow() - timedelta(hours=3)).strftime('%H:%M:%S')

def _run_bug_tracker_scan(assets_list, bot_log_fn=None):
    """
    Varre ativos em busca de 6 tipos de anomalias de corretora:
    1. 🧊 Candle Congelado   — preço não muda por N candles consecutivos
    2. 🔁 Repetição de Corpo — corpo de vela idêntico 3x ou mais seguidas
    3. 📋 Padrão Cópia       — sequência OHLC quase idêntica aparece 2x+
    4. ⚡ Spike Isolado       — variação >3x ATR em 1 candle
    5. 🕐 Divergência Forex   — OTC vs Forex real com diff > 0.5%
    6. 🃏 FlipCoin Extremo    — alternância 100% sem nenhuma tendência
    """
    import numpy as np
    from collections import Counter

    global _bug_tracker_results, _bug_tracker_log
    _bug_tracker_results = []
    _bug_tracker_log = []

    def log_bt(msg, level='info'):
        _bug_tracker_log.append({'time': _brt_bug(), 'msg': msg, 'level': level})
        if bot_log_fn:
            bot_log_fn(f'[BUG] {msg}', level)

    log_bt(f'🔍 Iniciando Bug Tracker em {len(assets_list)} ativos...', 'info')

    for asset in assets_list:
        try:
            closes, ohlc = IQ.get_candles_iq(asset, 60, 40)
            if closes is None or ohlc is None or len(closes) < 15:
                continue

            opens  = np.array([c['open']  for c in ohlc])
            highs  = np.array([c['max']   for c in ohlc])
            lows   = np.array([c['min']   for c in ohlc])
            cls    = np.array([c['close'] for c in ohlc])
            bodies = np.abs(cls - opens)
            ranges = highs - lows
            atr    = float(np.mean(ranges[-14:])) if len(ranges) >= 14 else float(np.mean(ranges))

            bugs_found = []

            # 1. CANDLE CONGELADO — close idêntico por 4+ candles
            last5_closes = [round(c, 5) for c in cls[-6:]]
            freeze_count = max(sum(1 for c in last5_closes if abs(c - last5_closes[-1]) < 0.000001),
                               sum(1 for c in last5_closes if abs(c - last5_closes[0]) < 0.000001))
            if freeze_count >= 4:
                bugs_found.append({
                    'type': 'frozen_candle',
                    'icon': '🧊',
                    'label': 'Candle Congelado',
                    'detail': f'{freeze_count} candles com close idêntico ({last5_closes[-1]:.5f})',
                    'severity': 'HIGH'
                })

            # 2. REPETIÇÃO DE CORPO — corpo quase igual 3x consecutivo
            last_bodies = [round(b, 5) for b in bodies[-6:]]
            body_counts = Counter([round(b, 4) for b in last_bodies])
            max_repeat = max(body_counts.values()) if body_counts else 0
            if max_repeat >= 3:
                repeated_val = [k for k, v in body_counts.items() if v == max_repeat][0]
                bugs_found.append({
                    'type': 'body_repeat',
                    'icon': '🔁',
                    'label': 'Corpo Repetido',
                    'detail': f'Corpo {repeated_val:.5f} repetido {max_repeat}x seguidas',
                    'severity': 'MEDIUM'
                })

            # 3. PADRÃO CÓPIA — sequência OHLC quase idêntica aparece 2x
            seq_len = 3
            if len(ohlc) >= seq_len * 2 + 2:
                last_seq = [(round(ohlc[-i]['open'],4), round(ohlc[-i]['close'],4)) for i in range(1, seq_len+1)]
                for start in range(seq_len+1, len(ohlc) - seq_len):
                    prev_seq = [(round(ohlc[-(start+i)]['open'],4), round(ohlc[-(start+i)]['close'],4)) for i in range(seq_len)]
                    diffs = [abs(last_seq[j][0]-prev_seq[j][0]) + abs(last_seq[j][1]-prev_seq[j][1]) for j in range(seq_len)]
                    if all(d < atr * 0.1 for d in diffs):
                        bugs_found.append({
                            'type': 'pattern_copy',
                            'icon': '📋',
                            'label': 'Padrão Cópia',
                            'detail': f'Sequência de {seq_len} candles quase idêntica detectada (diff<10%ATR)',
                            'severity': 'HIGH'
                        })
                        break

            # 4. SPIKE ISOLADO — variação > 3.5x ATR em 1 candle
            if atr > 0:
                last_range = ranges[-1]
                if last_range > atr * 3.5:
                    direction = '↑' if cls[-1] > opens[-1] else '↓'
                    bugs_found.append({
                        'type': 'isolated_spike',
                        'icon': '⚡',
                        'label': 'Spike Isolado',
                        'detail': f'Range {last_range:.5f} = {last_range/atr:.1f}x ATR {direction}',
                        'severity': 'HIGH'
                    })

            # 5. FLIPCOIN EXTREMO — alternância 100% por 8+ candles
            last8 = cls[-9:] if len(cls) >= 9 else cls
            directions = []
            for j in range(1, len(last8)):
                directions.append(1 if last8[j] > last8[j-1] else -1)
            alt_count = sum(1 for j in range(1, len(directions)) if directions[j] != directions[j-1])
            alt_ratio  = alt_count / max(len(directions) - 1, 1)
            if alt_ratio >= 0.85 and len(directions) >= 6:
                bugs_found.append({
                    'type': 'extreme_flipcoin',
                    'icon': '🃏',
                    'label': 'FlipCoin Extremo',
                    'detail': f'Alternância {alt_ratio*100:.0f}% em {len(directions)} movimentos — sem direção',
                    'severity': 'MEDIUM'
                })

            # 6. MOVIMENTO FORA DO COMUM — range do último candle > 5x média
            if atr > 0 and len(ranges) >= 5:
                recent_avg_range = float(np.mean(ranges[-5:]))
                last_r = ranges[-1]
                if last_r > recent_avg_range * 5:
                    bugs_found.append({
                        'type': 'abnormal_move',
                        'icon': '🚨',
                        'label': 'Movimento Anormal',
                        'detail': f'Range último candle = {last_r/recent_avg_range:.1f}x média recente',
                        'severity': 'HIGH'
                    })

            if bugs_found:
                severity_order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
                top_bug = sorted(bugs_found, key=lambda x: severity_order.get(x['severity'], 2))[0]
                result = {
                    'asset': asset,
                    'bugs': bugs_found,
                    'bug_count': len(bugs_found),
                    'top_bug': top_bug,
                    'close_atual': float(cls[-1]),
                    'atr': round(atr, 6),
                    'scanned_at': _brt_bug()
                }
                _bug_tracker_results.append(result)
                icons = ' '.join(b['icon'] for b in bugs_found)
                log_bt(f'⚠️ {asset}: {len(bugs_found)} bug(s) {icons} — {top_bug["label"]}', 'warn')
            else:
                log_bt(f'  ✅ {asset}: normal', 'info')

        except Exception as e:
            log_bt(f'  ❌ {asset}: erro — {str(e)[:60]}', 'error')

    # Ordenar por quantidade de bugs e severidade
    severity_order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
    _bug_tracker_results.sort(key=lambda x: (
        -x['bug_count'],
        severity_order.get(x['top_bug']['severity'], 2)
    ))

    log_bt(f'✅ Scan concluído: {len(_bug_tracker_results)}/{len(assets_list)} ativos com anomalias', 'info')
    return _bug_tracker_results




# ─── AUTH DECORATOR ──────────────────────────────────────────────────────────
from functools import wraps
def require_auth(f):
    """Decorator JWT/session para proteger endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = ''
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
        if not token:
            token = session.get('token', '')
        u = check_token(token)
        if not u:
            return jsonify({'error': 'Nao autorizado'}), 401
        return f(*args, **kwargs)
    return decorated
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/api/assets/pool', methods=['GET','POST'])
@require_auth
def assets_pool():
    """GET: retorna pool de ativos do usuário (até 6).
    POST: define pool de ativos para modo auto_user.
    Body: {user_asset_pool: ['EURUSD','GBPUSD',...], bot_selector_mode: 'auto_user', asset_market_filter: 'all'}
    """
    _tok = (request.headers.get('Authorization','')[7:] or session.get('token',''))
    _u   = check_token(_tok)
    _uname = _u['sub'] if _u else 'admin'
    bot_state = get_user_state(_uname)
    if request.method == 'GET':
        return jsonify({
            'ok': True,
            'user_asset_pool': bot_state.get('user_asset_pool',[]),
            'bot_selector_mode': bot_state.get('bot_selector_mode','auto_robot'),
            'asset_market_filter': bot_state.get('asset_market_filter','all'),
            'max_assets': 6
        })
    data = request.json or {}
    if 'user_asset_pool' in data:
        pool = data['user_asset_pool']
        if isinstance(pool, list):
            pool = [str(a).upper().strip() for a in pool if a]
            bot_state['user_asset_pool'] = pool[:6]
            bot_log(f'🎯 Pool de ativos definido: {bot_state["user_asset_pool"]}', 'info')
    if 'bot_selector_mode' in data:
        mode = data['bot_selector_mode']
        if mode in ('auto_robot','auto_user'):
            bot_state['bot_selector_mode'] = mode
    if 'asset_market_filter' in data:
        filt = data['asset_market_filter']
        if filt in ('otc','open','all'):
            bot_state['asset_market_filter'] = filt
    # Atualizar selected_asset: AUTO em qualquer modo automático
    if bot_state.get('bot_selector_mode') in ('auto_robot', 'auto_user'):
        bot_state['selected_asset'] = 'AUTO'
    return jsonify({
        'ok': True,
        'user_asset_pool': bot_state.get('user_asset_pool',[]),
        'bot_selector_mode': bot_state.get('bot_selector_mode','auto_robot'),
        'asset_market_filter': bot_state.get('asset_market_filter','all'),
        'msg': f'Pool atualizado: {len(bot_state.get("user_asset_pool",[]))} ativo(s)'
    })


@app.route('/api/bug-tracker/scan', methods=['POST'])
def bug_tracker_scan():
    """Inicia varredura de bugs/anomalias em ativos OTC e abertos"""
    global _bug_tracker_running
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401

    if _bug_tracker_running:
        return jsonify({'ok': False, 'message': 'Scan já em andamento...', 'log': _bug_tracker_log[-10:]}), 200

    d = request.get_json(silent=True) or {}
    scan_type = d.get('scan_type', 'otc')  # 'otc' | 'open' | 'all'

    if scan_type == 'open':
        assets = list(IQ.OPEN_BINARY_ASSETS)
    elif scan_type == 'all':
        assets = list(IQ.OTC_BINARY_ASSETS) + list(IQ.OPEN_BINARY_ASSETS)
    else:
        assets = list(IQ.OTC_BINARY_ASSETS)

    def _scan_thread():
        global _bug_tracker_running
        _bug_tracker_running = True
        try:
            _run_bug_tracker_scan(assets[:60])  # max 60 ativos por scan
        finally:
            _bug_tracker_running = False

    t = threading.Thread(target=_scan_thread, daemon=True)
    t.start()

    return jsonify({
        'ok': True,
        'message': f'🔍 Bug Tracker iniciado: {len(assets[:60])} ativos ({scan_type})',
        'total_assets': len(assets[:60]),
        'scan_type': scan_type
    })


@app.route('/api/bug-tracker/results', methods=['GET'])
def bug_tracker_results():
    """Retorna resultados do último scan de bugs"""
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401

    return jsonify({
        'running': _bug_tracker_running,
        'results': _bug_tracker_results,
        'total_bugs': len(_bug_tracker_results),
        'log': _bug_tracker_log[-30:],
        'scanned_at': _bug_tracker_results[0]['scanned_at'] if _bug_tracker_results else None
    })



# ─── RAILWAY REDEPLOY ENDPOINT ──────────────────────────────────────────────
@app.route('/api/railway/info', methods=['GET'])
def railway_info():
    """Retorna info do ambiente Railway para diagnóstico e redeploy."""
    import os
    return jsonify({
        'ok': True,
        'RAILWAY_SERVICE_ID':    os.environ.get('RAILWAY_SERVICE_ID', ''),
        'RAILWAY_PROJECT_ID':    os.environ.get('RAILWAY_PROJECT_ID', ''),
        'RAILWAY_ENVIRONMENT_ID':os.environ.get('RAILWAY_ENVIRONMENT_ID', ''),
        'RAILWAY_DEPLOYMENT_ID': os.environ.get('RAILWAY_DEPLOYMENT_ID', ''),
        'RAILWAY_ENVIRONMENT':   os.environ.get('RAILWAY_ENVIRONMENT', ''),
        'RAILWAY_PUBLIC_DOMAIN': os.environ.get('RAILWAY_PUBLIC_DOMAIN', ''),
        'has_railway_token':     bool(os.environ.get('RAILWAY_TOKEN', '')),
    })

@app.route('/api/railway/redeploy', methods=['POST'])
@require_auth
def railway_redeploy():
    """Força redeploy via Railway GraphQL API usando RAILWAY_TOKEN do ambiente."""
    import os
    railway_token = os.environ.get('RAILWAY_TOKEN', '')
    service_id    = os.environ.get('RAILWAY_SERVICE_ID', '')
    env_id        = os.environ.get('RAILWAY_ENVIRONMENT_ID', '')

    if not railway_token:
        return jsonify({'ok': False, 'error': 'RAILWAY_TOKEN não configurado no ambiente Railway'}), 400
    if not service_id:
        return jsonify({'ok': False, 'error': 'RAILWAY_SERVICE_ID não disponível'}), 400

    gql = 'https://backboard.railway.app/graphql/v2'
    mutation = '''
    mutation serviceInstanceRedeploy($serviceId: String!, $environmentId: String!) {
      serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
    }
    '''
    resp = __import__('requests').post(gql,
        json={'query': mutation, 'variables': {'serviceId': service_id, 'environmentId': env_id}},
        headers={'Authorization': f'Bearer {railway_token}', 'Content-Type': 'application/json'},
        timeout=15
    )
    data = resp.json()
    if resp.status_code == 200 and 'errors' not in data:
        bot_log('🚀 Railway redeploy acionado via API!', 'success')
        return jsonify({'ok': True, 'msg': 'Redeploy iniciado!', 'data': data})
    return jsonify({'ok': False, 'error': str(data.get('errors', data)), 'status': resp.status_code}), 400


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            master = User(username='admin', password_hash=hash_pw('danbot@master2025'), role='master')
            db.session.add(master); db.session.commit()
            print('✅ Master criado: admin / danbot@master2025')
    port = int(os.environ.get('PORT', 7860))
    app.run(host='0.0.0.0', port=port, debug=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 🚨 BUG TRACKER MONITOR 24H — Alerta Contínuo em Background
# Roda a cada 10 minutos automaticamente, registra alertas no log do bot,
# guarda histórico de anomalias e expõe endpoint de status/config
# ═══════════════════════════════════════════════════════════════════════════════

_bt_monitor_config = {
    'enabled':       True,          # ligado por padrão
    'interval_min':  10,            # intervalo entre scans (minutos)
    'scan_type':     'otc',         # 'otc' | 'open' | 'all'
    'min_bugs':      1,             # mínimo de bugs para alertar
    'alert_high_only': False,       # True = só alertar severidade HIGH
}

_bt_monitor_history  = []   # histórico de até 200 alertas
_bt_monitor_running  = False
_bt_monitor_last_run = None
_bt_monitor_next_run = None
_bt_monitor_stats    = {'total_scans': 0, 'total_alerts': 0, 'assets_flagged': set()}

_bt_monitor_thread_ref = None   # referência ao thread daemon

def _bt_monitor_loop():
    """
    Loop 24h do Bug Tracker Monitor.
    Roda indefinidamente em background com intervalo configurável.
    Registra alertas direto no log do bot (admin) e no histórico próprio.
    """
    global _bt_monitor_running, _bt_monitor_last_run, _bt_monitor_next_run
    global _bt_monitor_history, _bt_monitor_stats

    import datetime as _dt
    from datetime import timedelta as _td

    # Aguarda 2 min após boot para IQ Option conectar
    time.sleep(120)

    while True:
        try:
            interval_sec = _bt_monitor_config.get('interval_min', 10) * 60

            # Verificar se está habilitado
            if not _bt_monitor_config.get('enabled', True):
                time.sleep(30)
                continue

            # Verificar se IQ está conectado (sem broker = sem candles reais)
            iq_connected = False
            for _st_un in list(_USER_STATES.values()):
                if _st_un.get('broker_connected'):
                    iq_connected = True
                    break

            if not iq_connected:
                # Sem broker: aguardar e tentar novamente
                _bt_monitor_next_run = (_dt.datetime.utcnow() + _td(seconds=60)).strftime('%H:%M BRT')
                time.sleep(60)
                continue

            # ── EXECUTAR SCAN ────────────────────────────────────────────────
            _bt_monitor_running = True
            _bt_monitor_last_run = (_dt.datetime.utcnow() - _td(hours=3)).strftime('%H:%M:%S')
            _bt_monitor_stats['total_scans'] += 1

            scan_type = _bt_monitor_config.get('scan_type', 'otc')
            if scan_type == 'open':
                assets = list(IQ.OPEN_BINARY_ASSETS)
            elif scan_type == 'all':
                assets = list(IQ.OTC_BINARY_ASSETS) + list(IQ.OPEN_BINARY_ASSETS)
            else:
                assets = list(IQ.OTC_BINARY_ASSETS)

            bot_log(f'🔍 [BUG MONITOR] Scan automático #{_bt_monitor_stats["total_scans"]} — {len(assets[:60])} ativos ({scan_type.upper()})', 'info')

            results = _run_bug_tracker_scan(assets[:60])

            # ── PROCESSAR ALERTAS ────────────────────────────────────────────
            min_bugs      = _bt_monitor_config.get('min_bugs', 1)
            high_only     = _bt_monitor_config.get('alert_high_only', False)

            new_alerts = 0
            for res in results:
                # Filtrar por severidade se configurado
                if high_only:
                    has_high = any(b['severity'] == 'HIGH' for b in res.get('bugs', []))
                    if not has_high:
                        continue

                if res.get('bug_count', 0) < min_bugs:
                    continue

                # Montar alerta
                icons = ' '.join(b['icon'] for b in res.get('bugs', []))
                bug_labels = ' + '.join(b['label'] for b in res.get('bugs', []))
                alert = {
                    'time':       (_dt.datetime.utcnow() - _td(hours=3)).strftime('%H:%M:%S'),
                    'asset':      res['asset'],
                    'bug_count':  res['bug_count'],
                    'icons':      icons,
                    'labels':     bug_labels,
                    'top_bug':    res['top_bug']['label'],
                    'severity':   res['top_bug']['severity'],
                    'detail':     res['top_bug']['detail'],
                    'atr':        res.get('atr', 0),
                    'scan_num':   _bt_monitor_stats['total_scans'],
                }
                _bt_monitor_history.insert(0, alert)
                _bt_monitor_stats['total_alerts'] += 1
                _bt_monitor_stats['assets_flagged'].add(res['asset'])
                new_alerts += 1

                # Logar no painel principal do bot
                sev_color = 'error' if res['top_bug']['severity'] == 'HIGH' else 'warn'
                bot_log(
                    f'🚨 [BUG] {res["asset"]}: {icons} {bug_labels} | {res["top_bug"]["detail"][:60]}',
                    sev_color
                )

            # Limitar histórico a 200 entradas
            if len(_bt_monitor_history) > 200:
                _bt_monitor_history = _bt_monitor_history[:200]

            # Resumo do scan
            if new_alerts == 0:
                bot_log(f'✅ [BUG MONITOR] Scan #{_bt_monitor_stats["total_scans"]} limpo — nenhuma anomalia detectada', 'info')
            else:
                bot_log(f'⚠️ [BUG MONITOR] {new_alerts} anomalia(s) detectada(s) em {new_alerts} ativo(s)', 'warn')

        except Exception as e:
            bot_log(f'❌ [BUG MONITOR] Erro interno: {str(e)[:80]}', 'error')

        finally:
            _bt_monitor_running = False
            next_dt = _dt.datetime.utcnow() + _td(seconds=interval_sec) - _td(hours=3)
            _bt_monitor_next_run = next_dt.strftime('%H:%M:%S BRT')

        # Aguardar próximo ciclo
        time.sleep(interval_sec)


# ── Iniciar monitor 24h em background no boot ────────────────────────────────
_bt_monitor_thread_ref = threading.Thread(
    target=_bt_monitor_loop,
    daemon=True,
    name='bug-monitor-24h'
)
_bt_monitor_thread_ref.start()


@app.route('/api/bug-tracker/monitor', methods=['GET'])
def bug_tracker_monitor_status():
    """Status e histórico de alertas do monitor 24h"""
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401
    return jsonify({
        'enabled':       _bt_monitor_config['enabled'],
        'interval_min':  _bt_monitor_config['interval_min'],
        'scan_type':     _bt_monitor_config['scan_type'],
        'running':       _bt_monitor_running,
        'last_run':      _bt_monitor_last_run,
        'next_run':      _bt_monitor_next_run,
        'total_scans':   _bt_monitor_stats['total_scans'],
        'total_alerts':  _bt_monitor_stats['total_alerts'],
        'assets_flagged': sorted(list(_bt_monitor_stats['assets_flagged'])),
        'history':       _bt_monitor_history[:50],   # últimos 50 alertas
    })


@app.route('/api/bug-tracker/monitor/config', methods=['POST'])
def bug_tracker_monitor_config():
    """Configura o monitor 24h (intervalo, tipo, habilitar/desabilitar)"""
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401
    d = request.get_json(silent=True) or {}
    changes = []
    if 'enabled' in d:
        _bt_monitor_config['enabled'] = bool(d['enabled'])
        changes.append(f"enabled={'ON' if d['enabled'] else 'OFF'}")
    if 'interval_min' in d:
        val = max(2, min(60, int(d['interval_min'])))
        _bt_monitor_config['interval_min'] = val
        changes.append(f"interval={val}min")
    if 'scan_type' in d and d['scan_type'] in ('otc','open','all'):
        _bt_monitor_config['scan_type'] = d['scan_type']
        changes.append(f"scan_type={d['scan_type']}")
    if 'min_bugs' in d:
        _bt_monitor_config['min_bugs'] = max(1, int(d['min_bugs']))
        changes.append(f"min_bugs={d['min_bugs']}")
    if 'alert_high_only' in d:
        _bt_monitor_config['alert_high_only'] = bool(d['alert_high_only'])
        changes.append(f"high_only={d['alert_high_only']}")
    bot_log(f'⚙️ [BUG MONITOR] Config atualizado: {", ".join(changes)}', 'info')
    return jsonify({'ok': True, 'config': _bt_monitor_config, 'changes': changes})


@app.route('/api/bug-tracker/monitor/history', methods=['GET'])
def bug_tracker_monitor_history():
    """Histórico completo de alertas do monitor"""
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401
    limit = int(request.args.get('limit', 100))
    return jsonify({
        'total': len(_bt_monitor_history),
        'history': _bt_monitor_history[:limit],
        'assets_flagged': sorted(list(_bt_monitor_stats['assets_flagged'])),
    })


@app.route('/api/bug-tracker/monitor/clear', methods=['POST'])
def bug_tracker_monitor_clear():
    """Limpa histórico de alertas"""
    u = current_user()
    if not u:
        return jsonify({'error': 'não autorizado'}), 401
    global _bt_monitor_history
    count = len(_bt_monitor_history)
    _bt_monitor_history = []
    _bt_monitor_stats['total_alerts'] = 0
    _bt_monitor_stats['assets_flagged'] = set()
    bot_log(f'🗑️ [BUG MONITOR] Histórico limpo ({count} alertas removidos)', 'info')
    return jsonify({'ok': True, 'cleared': count})
# [danbot-deploy3] redeploy trigger 810416c8
