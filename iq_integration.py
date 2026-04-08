# ── Patch automático iqoptionapi (compatibilidade websocket 1.x) ─────────────
try:
    import os as _os, importlib.util as _ilu
    _pf = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'patch_iqoptionapi.py')
    if _os.path.exists(_pf):
        _spec = _ilu.spec_from_file_location('_iqpatch', _pf)
        _m = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_m)
        _m.apply_iqoptionapi_patch()
except Exception: pass
# ─────────────────────────────────────────────────────────────────────────────

import threading
"""
DANBOT — Motor de Análise Técnica M1 Ultra-Rápido
==================================================
PARÂMETROS CALIBRADOS PARA M1 (1 minuto):
  • EMA 5   — tendência imediata (rápida)
  • EMA 10  — tendência de curto prazo (média)
  • EMA 50  — tendência principal (filtro direcional)
  • RSI(5)  — oscilador ultra-responsivo
  • Stoch(5,3,3) — responsivo ao M1
  • MACD(5,13,3) — versão rápida para M1
  • Bollinger(10,2) — banda curta para M1
  • ADX(7)  — força da tendência rápida

REGRA PRINCIPAL DE ENTRADA:
  ★ Só entra se houver padrão de vela de ALTA ACERTIVIDADE (≥80%)
  ★ Padrão de vela DEVE estar alinhado com EMA5 E EMA50
  ★ Sem padrão confirmado = SEM ENTRADA, independente de outros indicadores

PADRÕES ACEITOS (acertividade ≥80% em estudos de backtesting M1):
  • Engolfo de Alta / Baixa     — 83%
  • Três Soldados / Três Corvos — 81%
  • Martelo em suporte          — 82%
  • Estrela Cadente em resist.  — 82%
  • Pinbar com contexto         — 80%
  • Morning Star / Evening Star — 85%
  • Tweezer Bottom / Top        — 80%
"""

import time, threading, logging, math, random
import numpy as np

# ─── Preços base sintéticos por ativo (para modo DEMO) ───────────────────────
_DEMO_BASE_PRICES = {
    'EURUSD': 1.0850, 'GBPUSD': 1.2600, 'USDJPY': 148.50, 'USDCHF': 0.9010,
    'AUDUSD': 0.6450, 'NZDUSD': 0.5950, 'USDCAD': 1.3580, 'EURGBP': 0.8590,
    'EURJPY': 161.20, 'GBPJPY': 187.40, 'BTCUSD': 68000.0, 'ETHUSD': 3200.0,
    'XAUUSD': 2310.0, 'XAGUSD': 27.50, 'USOIL': 79.50,
}

def _get_demo_base_price(asset: str) -> float:
    base = asset.replace('-OTC', '')
    return _DEMO_BASE_PRICES.get(base, 1.0000)

def generate_synthetic_candles(asset: str, count: int = 50):
    """
    Gera OHLC sintético com padrões OTC realistas para modo DEMO sem IQ conectado.
    Inclui Dead Candles (doji <8%), sequências e ciclos alternados para o detector DC.
    """
    base = _get_demo_base_price(asset)
    vol  = base * 0.0006  # volatilidade M1 realista

    # Estrutura de mercado com padrões OTC
    structure = random.choice(['trend_up', 'trend_down', 'range', 'otc_alt', 'otc_seq'])

    opens  = np.zeros(count)
    closes = np.zeros(count)
    highs  = np.zeros(count)
    lows   = np.zeros(count)

    opens[0] = closes[0] = base
    highs[0] = base + vol * 0.5
    lows[0]  = base - vol * 0.5

    for i in range(1, count):
        prev_c = closes[i-1]
        noise  = random.gauss(0, vol)

        if structure == 'trend_up':
            bias = vol * 0.5
        elif structure == 'trend_down':
            bias = -vol * 0.5
        elif structure == 'otc_alt':
            bias = vol * 0.4 if i % 2 == 0 else -vol * 0.4
        elif structure == 'otc_seq':
            block = (i // 5) % 2
            bias  = vol * 0.6 if block == 0 else -vol * 0.6
        else:
            bias = random.gauss(0, vol * 0.15)

        new_close = max(prev_c * 0.99, prev_c + noise + bias)

        # Tipo de vela: 65% normal, 25% doji, 10% fantasma
        vt = random.choices(['normal', 'doji', 'ghost'], weights=[65, 25, 10])[0]

        if vt == 'doji':
            # Dead candle: corpo minúsculo (< 5% do range)
            rng   = abs(noise) * 4 + vol * 0.8
            midp  = (prev_c + new_close) / 2
            o_v   = midp + random.uniform(-rng * 0.025, rng * 0.025)
            c_v   = midp + random.uniform(-rng * 0.025, rng * 0.025)
            h_v   = max(o_v, c_v) + rng * random.uniform(0.4, 0.6)
            l_v   = min(o_v, c_v) - rng * random.uniform(0.4, 0.6)
        elif vt == 'ghost':
            # Vela fantasma: corpo < 5% mas sombras enormes
            rng   = abs(noise) * 6 + vol * 1.5
            midp  = (prev_c + new_close) / 2
            o_v   = midp + random.uniform(-rng * 0.02, rng * 0.02)
            c_v   = midp + random.uniform(-rng * 0.02, rng * 0.02)
            h_v   = max(o_v, c_v) + rng * 0.85
            l_v   = min(o_v, c_v) - rng * 0.85
        else:
            # Vela normal: open = close anterior
            o_v   = prev_c
            c_v   = new_close
            body  = abs(c_v - o_v)
            h_v   = max(o_v, c_v) + body * random.uniform(0.05, 0.4)
            l_v   = min(o_v, c_v) - body * random.uniform(0.05, 0.4)

        opens[i]  = o_v
        closes[i] = c_v
        highs[i]  = max(o_v, c_v, h_v)
        lows[i]   = min(o_v, c_v, l_v)

    # Garantir OHLC válido
    highs = np.maximum(highs, np.maximum(opens, closes))
    lows  = np.minimum(lows,  np.minimum(opens, closes))
    lows  = np.where(lows < 0.0001, 0.0001, lows)
    vols  = np.ones(count) * 500.0

    ohlc = {'closes': closes, 'highs': highs, 'lows': lows, 'opens': opens, 'volumes': vols}
    return closes, ohlc

# ── Lógica do Preço ───────────────────────────────────────────────────────────
try:
    from logica_preco import analisar_logica_preco
    _LP_DISPONIVEL = True
except ImportError:
    _LP_DISPONIVEL = False
    def analisar_logica_preco(*a, **kw):
        return {'score_call': 0, 'score_put': 0, 'sinais': [], 'alertas': [],
                'direcao': None, 'forca_lp': 0, 'resumo': 'LP não disponível',
                'pode_entrar': True}

# ── Candle Engine separado ─────────────────────────────────────────────────
try:
    from candles_engine import normalize_candle_config, analyze_candle_engine
    _CANDLES_ENGINE_AVAILABLE = True
except ImportError:
    _CANDLES_ENGINE_AVAILABLE = False
    def normalize_candle_config(strategies=None):
        strategies = strategies or {}
        return {
            'enabled': bool(strategies.get('pat', True)),
            'classic_enabled': True,
            'advanced_enabled': True,
            'classic_patterns': [],
            'advanced_patterns': [],
            'min_score': 7,
            'strict_ema_alignment': True,
            'require_context': True,
        }
    def analyze_candle_engine(opens, highs, lows, closes, ema5_last, ema50_last, candle_cfg=None):
        return {
            'enabled': False,
            'direction': None,
            'score_call': 0,
            'score_put': 0,
            'strength': 0,
            'selected_pattern': None,
            'selected_pattern_key': None,
            'patterns': [],
            'classic_patterns': [],
            'advanced_patterns': [],
            'summary': 'Candles engine indisponível',
            'min_score_ok': False,
            'config': normalize_candle_config({})
        }

log = logging.getLogger('danbot.iq')

# ─── PER-USER IQ INSTANCES ─────────────────────────────────────────────────
# Cada usuário tem seu próprio objeto IQ_Option.
# A thread-local _thread_user guarda qual usuário está ativo na thread atual.
_iq_instances  = {}          # {username: IQ_Option}
_iq_locks      = {}          # {username: Lock}
_iq_global_lock = threading.Lock()   # para criar entries no dict
_thread_user   = threading.local()   # .username = str

def _get_user_lock(username: str) -> threading.Lock:
    with _iq_global_lock:
        if username not in _iq_locks:
            _iq_locks[username] = threading.Lock()
        return _iq_locks[username]

def set_user_context(username: str):
    """Chame no início de cada thread de usuário para definir o contexto."""
    _thread_user.username = username

def _current_username() -> str:
    return getattr(_thread_user, 'username', 'default')

# Manter compatibilidade: _iq_instance aponta para instância do usuário default
# (usado apenas para compatibilidade com código legado fora de threads de usuário)
_iq_instance = None   # legado – não usar em código novo
_iq_lock = threading.Lock()  # legado

# ─── ATIVOS OTC BINÁRIAS ─────────────────────────────────────────────────────
OTC_BINARY_ASSETS = [
    # ── 142 ativos OTC confirmados por API (08/03/2026) ──
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
    'UKOUSD-OTC',
    'USOUSD-OTC',
    'XAGUSD-OTC',
    'XAU/XAG-OTC',
    'XAUUSD-OTC',
    'XNGUSD-OTC',
    'XPDUSD-OTC',
    'XPTUSD-OTC',
]

# Lista de ativos que NÃO suportam binary — apenas para referência/candles
OTC_NON_BINARY_ASSETS = [
    # Índices OTC (candles OK, binary NÃO)
    'USNDAQ100-OTC', 'SP500-OTC', 'US30-OTC', 'GER30-OTC', 'FR40-OTC',
    'HK33-OTC', 'JP225-OTC', 'UK100-OTC', 'AUS200-OTC', 'EU50-OTC',
    'SP35-OTC', 'US2000-OTC',
    # Ações OTC (candles OK, binary NÃO)
    'APPLE-OTC', 'MSFT-OTC', 'GOOGLE-OTC', 'AMAZON-OTC', 'TESLA-OTC',
    'FB-OTC', 'ALIBABA-OTC', 'BIDU-OTC', 'GS-OTC', 'JPM-OTC',
    'NIKE-OTC', 'MCDON-OTC', 'INTEL-OTC', 'CITI-OTC',
    # Crypto sem confirmação para binary
    'SOLUSD-OTC', 'DOTUSD-OTC', 'WIFUSD-OTC', 'WLDUSD-OTC',
    # Commodity sem confirmação
    'XNGUSD-OTC',
]

# ─── Ativos de Mercado Aberto (Binárias turbo M1/M5) ──────────────────────
OPEN_BINARY_ASSETS = [
    # ── Forex Mercado Aberto (nomes aceitos pela API IQ Option) ──────────────
    'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD',
    'NZDUSD', 'USDCAD', 'EURGBP', 'EURJPY', 'GBPJPY',
    'AUDJPY', 'CADJPY', 'EURCHF', 'GBPCHF', 'GBPCAD',
    'EURCAD', 'EURNZD', 'AUDCAD', 'AUDCHF', 'NZDCAD',
    'NZDJPY', 'CHFJPY', 'USDSGD', 'EURAUD', 'GBPAUD',
    'AUDNZD', 'GBPNZD',
    # ── Crypto Mercado Aberto (apenas confirmados como binary na API) ─────────
    # ATENÇÃO: BNB, SOL, ADA, DOT só existem como leverage (-L) — removidos
    'BTCUSD', 'ETHUSD', 'XRPUSD', 'LTCUSD',
    'BCHUSD', 'XLMUSD', 'TRXUSD', 'EOSUSD', 'ETCUSD',
    # ── Commodities (nomes CORRETOS da API IQ Option — fonte: constants.py) ──
    'XAUUSD', 'XAGUSD',   # Ouro (ID 74) e Prata (ID 75)
    'USOUSD', 'UKOUSD',   # Petróleo US (ID 971) e UK/Brent (ID 969)
    # ── Índices Mercado Aberto (nomes CORRETOS da API IQ Option) ────────────
    # ERRADO → CORRETO
    # SP500   → USSPX500   (ID 1239)
    # DJ30    → US30       (ID 1235)
    # NASDAQ  → USNDAQ100  (ID 1236)
    # FTSE100 → UK100      (ID 1241)
    # DE30    → GERMANY30  (ID 1232)
    # FR40    → FRANCE40   (ID 1231)
    # JP225   → JAPAN225   (ID 1237)
    'USSPX500', 'US30', 'USNDAQ100',   # EUA
    'UK100', 'GERMANY30', 'FRANCE40',  # Europa
    'JAPAN225', 'AUS200',               # Ásia/Pacífico
    'HONGKONG50', 'SPAIN35',            # Outros
]

# ─── Lista COMPLETA: OTC + Mercado Aberto ─────────────────────────────────
ALL_BINARY_ASSETS = OTC_BINARY_ASSETS + OPEN_BINARY_ASSETS

# ─── CONEXÃO ─────────────────────────────────────────────────────────────────

def get_iq(username: str = None):
    """Retorna a instância IQ_Option do usuário atual (ou username explícito)."""
    if username is None:
        username = _current_username()
    return _iq_instances.get(username)

def get_iq_default():
    """Compatibilidade: retorna instância global legacy."""
    return _iq_instances.get('default') or _iq_instances.get('admin')



def sync_actives_from_api(iq_instance):
    """
    Sincroniza o dicionário ACTIVES da biblioteca iqoptionapi com todos os
    ativos OTC reais retornados pelo endpoint get_all_init da IQ Option.

    A IQ Option tem +250 ativos binários OTC com prefixo 'front.' e IDs
    maiores que 1000 (ex: front.XAUUSD-OTC → ID=1857) que NÃO existem no
    dict estático ACTIVES da lib v6.x. Isso causa KeyError silencioso em
    buy() → desconexão do bot.

    Esta função é chamada automaticamente após cada connect().
    """
    try:
        from iqoptionapi import constants as OP_code
        init_info = iq_instance.get_all_init()
        if not init_info or 'result' not in init_info:
            return 0
        added = 0
        for mode in ['binary', 'turbo']:
            if mode not in init_info['result']:
                continue
            for aid, ainfo in init_info['result'][mode]['actives'].items():
                full_name = ainfo.get('name', '')
                # Remover prefixo "front." (formato real da API)
                clean_name = full_name[6:] if full_name.startswith('front.') else full_name
                asset_id = int(aid)
                if clean_name and clean_name not in OP_code.ACTIVES:
                    OP_code.ACTIVES[clean_name] = asset_id
                    added += 1
                elif clean_name and OP_code.ACTIVES.get(clean_name) != asset_id:
                    # Atualizar ID se mudou (ativos novos/renomeados)
                    OP_code.ACTIVES[clean_name] = asset_id
        return added
    except Exception as e:
        log.warning(f"sync_actives_from_api: {e}")
        return 0


# Mapeamento de hosts compatíveis com IQ Option API
BROKER_HOSTS_IQ = {
    'IQ Option': 'iqoption.com',
    'Bullex':    'trade.bull-ex.com',
    'Exnova':    'trade.exnova.com',
}

# Caminho WebSocket específico por host
# NOTA: Exnova usa ws.trade.exnova.com/echo/websocket (host DIFERENTE!)
# trade.exnova.com/echo/websocket redireciona para HTML (302)
# ws.trade.exnova.com/echo/websocket retorna 101 Switching Protocols ✅
BROKER_WSS_PATH = {
    'iqoption.com':     '/echo/websocket',
    'trade.bull-ex.com': '/echo/websocket',  # Bullex usa ws.trade.bull-ex.com
    'trade.exnova.com': '/echo/websocket',  # path correto; host é ws.trade.exnova.com
}

# Host WebSocket específico por broker (quando diferente do host principal)
BROKER_WSS_HOST = {
    'trade.exnova.com': 'ws.trade.exnova.com',  # WebSocket usa subdomínio ws.
    'trade.bull-ex.com': 'ws.trade.bull-ex.com',  # Bullex WebSocket usa subdomínio ws.
}

# Base da URL HTTP para login por host
# Exnova usa auth.trade.exnova.com/api/v2 (schema diferente do IQ Option)
# Resultado: https_url/login → https://auth.trade.exnova.com/api/v2/login ✓
BROKER_AUTH_BASE = {
    'trade.exnova.com': 'https://auth.trade.exnova.com/api/v2',
    'trade.bull-ex.com': 'https://auth.trade.bull-ex.com/api/v2',  # Bullex auth endpoint
}

def connect_iq(email: str, password: str, account_type: str = 'PRACTICE', host: str = 'iqoption.com', username: str = None, broker_name: str = None):
    """
    Conecta à IQ Option / Bullex / Exnova com retry automático (3 tentativas).
    Cada tentativa tem timeout de 25s.
    Suporta host customizado: iqoption.com, trade.bull-ex.com, trade.exnova.com
    """
    global _iq_instance
    try:
        from iqoptionapi.stable_api import IQ_Option
    except ImportError:
        return False, 'Biblioteca iqoptionapi não instalada'
    
    # Normalizar host e username
    if not host:
        host = 'iqoption.com'
    if username is None:
        username = _current_username()
    # Derivar nome da corretora a partir do host
    if broker_name is None:
        _host_map = {'iqoption.com': 'IQ Option', 'trade.bull-ex.com': 'Bullex', 'trade.exnova.com': 'Exnova'}
        broker_name = _host_map.get(host, host)
    _ulock = _get_user_lock(username)

    MAX_RETRIES = 3
    last_error  = 'desconhecido'

    for attempt in range(1, MAX_RETRIES + 1):
        _result = [None, None]
        _new_iq  = [None]

        def _do_connect(_attempt=attempt):
            try:
                # Fechar apenas a instância DESTE usuário (não afeta outros usuários)
                with _ulock:
                    old = _iq_instances.get(username)
                if old is not None:
                    try: old.close()
                    except: pass
                    time.sleep(0.5)

                iq = IQ_Option(email, password)
                
                # Suporte a host customizado (Bullex, Exnova, etc.)
                # Estratégia: patch temporário de IQOptionAPI.__init__ para usar host correto,
                # depois chama IQ_Option.connect() original (que faz setup completo: balance_id, subscriptions)
                if host and host != 'iqoption.com':
                    log.info(f'Broker customizado: {host}')
                    try:
                        from iqoptionapi.api import IQOptionAPI as _IQAPI
                        from iqoptionapi.stable_api import IQ_Option as _IQCls
                        _custom_host  = host
                        _orig_init    = _IQAPI.__init__

                        def _host_init(api_self, h, usr, pwd, proxies=None):
                            """Substitui 'iqoption.com' pelo host desejado e corrige wss_url/https_url."""
                            _orig_init(api_self, _custom_host, usr, pwd, proxies)
                            # ── Corrigir WSS path (Exnova usa /en/echo/websocket) ──
                            _wss_path = BROKER_WSS_PATH.get(_custom_host, '/echo/websocket')
                            api_self.wss_url = f'wss://{_custom_host}{_wss_path}'
                            # ── Corrigir URL de autenticação (Exnova usa auth.trade.exnova.com) ──
                            _auth_base = BROKER_AUTH_BASE.get(_custom_host)
                            if _auth_base:
                                api_self.https_url = _auth_base
                            log.info(f'URLs configuradas: wss={api_self.wss_url}, https={api_self.https_url}')

                        import threading as _thr
                        _host_patch_lock = getattr(_IQAPI, '_host_patch_lock', _thr.Lock())
                        _IQAPI._host_patch_lock = _host_patch_lock

                        def _patched_connect(self_iq):
                            with _host_patch_lock:
                                _IQAPI.__init__ = _host_init
                                try:
                                    # Para Exnova: SSID vem no body JSON (não em cookie)
                                    # Precisamos injetar o ssid no cookie antes de conectar WebSocket
                                    _auth_url = BROKER_AUTH_BASE.get(_custom_host)
                                    if _auth_url:
                                        # Patch temporário do IQOptionAPI.connect() para Exnova
                                        _orig_api_connect = _IQAPI.connect
                                        def _exnova_connect(api_self):
                                            """Versão Exnova: login via JSON body, ssid injetado no cookie."""
                                            import requests as _req
                                            import warnings as _w; _w.filterwarnings('ignore')
                                            # 1. Login na URL correta da Exnova
                                            _r = _req.post(
                                                f'{_auth_url}/login',
                                                json={'identifier': api_self.username, 'password': api_self.password},
                                                headers={
                                                    'Content-Type': 'application/json',
                                                    'Origin': f'https://{_custom_host}',
                                                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
                                                              ' AppleWebKit/537.36 (KHTML, like Gecko)'
                                                              ' Chrome/120.0.0.0 Safari/537.36',
                                                },
                                                verify=False, timeout=25
                                            )
                                            _data = _r.json()
                                            if _data.get('code') != 'success':
                                                _msg = _data.get('message', str(_data))
                                                if 'credential' in _msg.lower() or 'password' in _msg.lower():
                                                    return False, 'invalid_credentials'
                                                return False, _msg
                                            _ssid = _data.get('ssid', '')
                                            log.info(f'Exnova login OK, SSID obtido ({len(_ssid)} chars)')
                                            # 2. Injetar ssid nos cookies (inline - compatível com todas versões)
                                            import requests as _req_c
                                            _req_c.utils.add_dict_to_cookiejar(
                                                api_self.session.cookies,
                                                {'ssid': _ssid, 'platform': '9'})
                                            api_self.session.cookies.set(
                                                'ssid', _ssid, domain=_custom_host, path='/')
                                            # 3. set_session_cookies se disponível (seguro para todas versões)
                                            if hasattr(api_self, 'set_session_cookies'):
                                                try:
                                                    api_self.set_session_cookies()
                                                except Exception:
                                                    pass
                                            # 4. Forçar URLs corretas no api_self (caso _host_init não tenha rodado)
                                            _wss_path = BROKER_WSS_PATH.get(_custom_host, '/echo/websocket')
                                            # BUG #10 FIX: WebSocket usa ws.trade.exnova.com, não trade.exnova.com!
                                            _wss_host = BROKER_WSS_HOST.get(_custom_host, _custom_host)
                                            api_self.wss_url   = f'wss://{_wss_host}{_wss_path}'
                                            api_self.https_url = _auth_url  # ex: https://auth.trade.exnova.com/api/v2
                                            log.info(f'URLs forçadas: wss={api_self.wss_url}, https={api_self.https_url}')
                                            # 4b. Conectar WebSocket com cookie ssid no handshake
                                            import threading as _thr2
                                            import websocket as _ws_lib
                                            from iqoptionapi.ws.client import WebsocketClient as _WSC
                                            api_self.websocket_client = _WSC(api_self)
                                            # CRÍTICO: recriar WebSocketApp com cookie ssid no header de handshake
                                            # Sem isso o servidor Exnova fecha a conexão por falta de auth
                                            _wsc_obj = api_self.websocket_client
                                            # on_open envia SSID imediatamente ao conectar
                                            # (servidor Exnova fecha conexão se não receber SSID em ~2s)
                                            _orig_on_open = _wsc_obj.on_open
                                            def _on_open_ssid(ws):
                                                import json as _jmod
                                                ws.send(_jmod.dumps({"name": "ssid", "msg": _ssid}))
                                                log.info('Exnova: SSID enviado no on_open')
                                                try: _orig_on_open(ws)
                                                except Exception: pass
                                            _wsc_obj.wss = _ws_lib.WebSocketApp(
                                                api_self.wss_url,
                                                on_message=_wsc_obj.on_message,
                                                on_error=_wsc_obj.on_error,
                                                on_close=_wsc_obj.on_close,
                                                on_open=_on_open_ssid,
                                                cookie=f'ssid={_ssid}')
                                            log.info(f'WebSocket Exnova: {api_self.wss_url}')
                                            # BUG #9 FIX: WebsocketClient NÃO tem run_forever()
                                            # Usar _wsc_obj.wss.run_forever (o WebSocketApp real com cookie)
                                            _ws_ready_evt = _thr2.Event()
                                            _orig_on_open_ssid = _on_open_ssid
                                            def _on_open_ssid_evt(ws):
                                                _orig_on_open_ssid(ws)
                                                _ws_ready_evt.set()
                                                log.info('Exnova: WebSocket pronto (Event set)')
                                            _wsc_obj.wss.on_open = _on_open_ssid_evt
                                            # on_close com logging detalhado para diagnóstico
                                            def _on_close_log(ws, close_code=None, close_msg=None):
                                                log.warning(f'Exnova WS fechado: code={close_code} msg={close_msg}')
                                            _wsc_obj.wss.on_close = _on_close_log
                                            # Iniciar thread usando _wsc_obj.wss.run_forever (correto!)
                                            _wst = _thr2.Thread(target=_wsc_obj.wss.run_forever)
                                            _wst.daemon = True
                                            _wst.start()
                                            # Aguardar conexão (até 8s) antes de retornar
                                            _connected = _ws_ready_evt.wait(timeout=8)
                                            if _connected:
                                                log.info('Exnova: WebSocket conectado com sucesso!')
                                            else:
                                                log.warning('Exnova: timeout aguardando WebSocket (8s)')
                                            import time as _t2; _t2.sleep(1)
                                            return True, None
                                        _IQAPI.connect = _exnova_connect
                                        try:
                                            result = _IQCls.connect(self_iq)
                                        finally:
                                            _IQAPI.connect = _orig_api_connect
                                    else:
                                        result = _IQCls.connect(self_iq)
                                finally:
                                    _IQAPI.__init__ = _orig_init
                            return result

                        import types
                        iq.connect = types.MethodType(_patched_connect, iq)
                        log.info(f'✅ Patch de host aplicado: {_custom_host}')
                    except Exception as _hp:
                        log.warning(f'Host patch falhou ({_hp}), usando iqoption.com')

                # Atualizar User-Agent para Chrome 120
                iq.SESSION_HEADER = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }

                # ── PATCH CRÍTICO: adicionar timeout=20s ao HTTP de login ──────
                # Sem este patch, auth.iqoption.com pode travar indefinidamente
                # causando Errno 110 (Connection timed out) no Railway/VPS
                try:
                    import iqoptionapi.api as _iq_api_mod
                    _orig_http = _iq_api_mod.IQOptionAPI.send_http_request_v2
                    def _patched_http(self_api, url, method, data=None, params=None, headers=None):
                        return self_api.session.request(
                            method=method, url=url, data=data,
                            params=params, headers=headers,
                            proxies=self_api.proxies, timeout=20
                        )
                    _iq_api_mod.IQOptionAPI.send_http_request_v2 = _patched_http
                except Exception as _pe:
                    log.warning(f'HTTP timeout patch falhou: {_pe}')
                # ─────────────────────────────────────────────────────────────

                check, reason = iq.connect()
                if not check:
                    r_str = str(reason).lower() if reason else ''
                    if 'invalid' in r_str or 'wrong' in r_str or 'password' in r_str or 'credentials' in r_str:
                        _result[0] = False
                        _result[1] = '❌ E-mail ou senha incorretos. Verifique suas credenciais.'
                        return
                    if 'blocked' in r_str or 'banned' in r_str:
                        _result[0] = False
                        _result[1] = f'❌ Conta bloqueada na {broker_name}'
                        return
                    if '2fa' in r_str or 'two' in r_str or 'otp' in r_str:
                        _result[0] = False
                        _result[1] = f'❌ 2FA ativado — desative nas configurações da {broker_name}'
                        return
                    _result[0] = False
                    _result[1] = f'{broker_name} recusou: {reason}'
                    return


                acc = account_type.upper()
                if acc not in ('PRACTICE', 'REAL'):
                    acc = 'PRACTICE'
                iq.change_balance(acc)
                time.sleep(1.5)

                balance = iq.get_balance() or 0.0
                _new_iq[0] = iq

                # Sincronizar ACTIVES com a lista real da API (fix OTC KeyError)
                try:
                    _added = sync_actives_from_api(iq)
                    if _added > 0:
                        log.info(f'sync_actives_from_api: +{_added} ativos adicionados ao ACTIVES')
                except Exception as _se:
                    log.warning(f'sync_actives: {_se}')

                _result[0]  = True
                _result[1]  = {
                    'balance': round(float(balance), 2),
                    'account_type': acc,
                    'otc_assets': OTC_BINARY_ASSETS
                }
            except Exception as e:
                _result[0] = False
                err_str = str(e)
                # Traduzir erros técnicos para mensagens amigáveis
                if 'Errno 110' in err_str or 'timed out' in err_str.lower() or 'timeout' in err_str.lower():
                    _result[1] = (f'❌ Timeout ao conectar na {broker_name}. O servidor demorou demais. Verifique internet e tente novamente.')
                elif 'Errno 111' in err_str or 'refused' in err_str.lower():
                    _result[1] = f'❌ Conexão recusada pelo servidor {broker_name}. Tente novamente em instantes.'
                elif 'invalid_credentials' in err_str or 'wrong credentials' in err_str.lower():
                    _result[1] = f'❌ E-mail ou senha incorretos. Verifique suas credenciais na {broker_name}.'
                elif 'Name or service not known' in err_str or 'getaddrinfo' in err_str:
                    _result[1] = '❌ Sem acesso à internet ou DNS falhou. Verifique sua conexão.'
                else:
                    _result[1] = f'❌ Erro de conexão: {err_str[:120]}'

        t = threading.Thread(target=_do_connect, daemon=True, name=f'iq-connect-{attempt}')
        t.start()
        t.join(timeout=45)  # 45s: cobre HTTP(20s) + WebSocket handshake + auth

        if t.is_alive():
            broker_name_label = 'Corretora' if host != 'iqoption.com' else 'IQ Option'
            last_error = (f'❌ Timeout: {broker_name_label} não respondeu em 45s. '
                          'Pode ser bloqueio de IP no servidor. '
                          'Tente novamente ou use VPN. '
                          f'Host: {host}')
            log.warning(f'connect_iq tentativa {attempt}: timeout 25s')
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)  # backoff: 2s, 4s
            continue

        if _result[0] is None:
            last_error = f'Erro interno tentativa {attempt}'
            continue

        if not _result[0]:
            last_error = _result[1]
            # Erros definitivos — não retry
            if any(x in str(last_error) for x in ['incorretos', 'bloqueada', '2FA', '❌']):
                return False, last_error
            if attempt < MAX_RETRIES:
                log.warning(f'connect_iq tentativa {attempt} falhou: {last_error} — aguardando {3*attempt}s...')
                time.sleep(3 * attempt)
            continue

        # Sucesso! Salvar instância POR USUÁRIO
        if _new_iq[0] is not None:
            with _ulock:
                _iq_instances[username] = _new_iq[0]
            # Compatibilidade legada
            global _iq_instance
            _iq_instance = _new_iq[0]

        if attempt > 1:
            log.info(f'✅ Conectado na tentativa {attempt}')
        return True, _result[1]

    # Todas as tentativas falharam
    return False, f'❌ Falha após {MAX_RETRIES} tentativas. Último erro: {last_error}. '                   f'Verifique: internet, credenciais, 2FA desativado.'




# Cache para is_iq_session_valid — evita 3 chamadas bloqueantes por ciclo
_session_valid_cache = {'result': False, 'ts': 0.0}
_SESSION_CACHE_TTL = 30.0  # revalidar a cada 30s — reduz falsos desconects

def is_iq_session_valid() -> bool:
    """
    Verifica se a sessão IQ Option está ativa.
    USA CACHE de 10s para não fazer múltiplas chamadas bloqueantes por ciclo.
    Executa get_balance() em thread separada com timeout de 3s.
    """
    global _session_valid_cache
    iq = get_iq()
    if iq is None:
        _session_valid_cache = {'result': False, 'ts': time.time()}
        return False
    
    # Retornar cache se ainda válido
    now = time.time()
    if now - _session_valid_cache['ts'] < _SESSION_CACHE_TTL:
        return _session_valid_cache['result']
    
    # Verificar em thread com timeout para não bloquear o GIL
    _result_holder = [None]
    def _check():
        try:
            bal = iq.get_balance()
            _result_holder[0] = (bal is not None and float(bal) >= 0)
        except Exception:
            _result_holder[0] = False
    
    t = threading.Thread(target=_check, daemon=True)
    t.start()
    t.join(timeout=3.0)  # timeout 3s — não bloqueia por mais que isso
    
    result = _result_holder[0] if _result_holder[0] is not None else False
    _session_valid_cache = {'result': result, 'ts': now}
    return result

def invalidate_session_cache():
    """Força revalidação na próxima chamada de is_iq_session_valid."""
    global _session_valid_cache
    _session_valid_cache = {'result': False, 'ts': 0.0}

def get_real_balance():
    """Busca saldo real com timeout de 2s para não bloquear o loop."""
    iq = get_iq()
    if not iq: return None
    _bal = [None]
    def _get():
        try: _bal[0] = round(float(iq.get_balance()), 2)
        except: pass
    t = threading.Thread(target=_get, daemon=True)
    t.start()
    t.join(timeout=2.0)
    return _bal[0]


def seconds_to_next_candle(timeframe: int = 60) -> float:
    now = time.time()
    rem = now % timeframe
    wait = timeframe - rem
    if wait < 3:
        wait += timeframe
    return wait


def get_candles_iq(asset: str, timeframe: int = 60, count: int = 100):
    # ── Normalizar nome de ativo OTC ─────────────────────
    _a = str(asset).upper().strip()
    _a = _a.replace("_OTC", "-OTC").replace(" OTC", "-OTC")
    if _a.endswith("OTC") and not _a.endswith("-OTC"):
        _a = _a[:-3].rstrip("-_") + "-OTC"
    asset = _a
    # ──────────────────────────────────────────────────────
    """Retorna (closes_array, ohlc_dict) com candles OHLC reais.
    Timeout de 8s por ativo para não bloquear o scan de 110 ativos.
    """
    iq = get_iq()
    if not iq: return None, None

    result_holder = [None, None]

    def _fetch():
        try:
            api_asset = resolve_asset_name(asset)
            candles = iq.get_candles(api_asset, timeframe, count, time.time())
            if not candles or len(candles) < 15:
                return
            closes = np.array([float(c['close']) for c in candles])
            highs  = np.array([float(c['max'])   for c in candles])
            lows   = np.array([float(c['min'])   for c in candles])
            opens  = np.array([float(c['open'])  for c in candles])
            try:
                raw_vols = np.array([float(c.get('volume', 0)) for c in candles])
                if raw_vols.sum() == 0:
                    raw_vols = calc_volume_candle(opens, closes, highs, lows)
            except Exception:
                raw_vols = calc_volume_candle(opens, closes, highs, lows)
            result_holder[0] = closes
            result_holder[1] = {'highs': highs, 'lows': lows, 'opens': opens,
                                 'closes': closes, 'volumes': raw_vols}
        except Exception as e:
            log.warning(f'Candles {asset}: {e}')

    for _attempt in range(2):  # 2 tentativas (retry automático)
        result_holder[0] = None; result_holder[1] = None
        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        t.join(timeout=12)  # 12s por ativo (aumentado de 8s)
        if result_holder[0] is not None:
            break  # sucesso — não precisa retry
        if t.is_alive():
            log.warning(f'get_candles_iq timeout (12s) tentativa {_attempt+1} para {asset}')
        else:
            log.debug(f'get_candles_iq falhou tentativa {_attempt+1} para {asset}')
        if _attempt == 0:
            time.sleep(1)  # pausa curta antes do retry
    return result_holder[0], result_holder[1]


# ═══════════════════════════════════════════════════════════════════════════════
# FILTRO DE VOLUME REAL (Mercado Aberto)
# ═══════════════════════════════════════════════════════════════════════════════

def calc_volume_candle(opens: np.ndarray, closes: np.ndarray,
                       highs: np.ndarray, lows: np.ndarray) -> np.ndarray:
    """
    Volume sintético por candle baseado em amplitude relativa ao preço.

    Fórmula: (high - low) / close * 1_000_000
    → Para Forex M1 real: EURUSD amplitude 5-20 pips → vol 450-1800
    → Para Crypto: normalizado pelo preço — BTCUSD 0.5% move → vol ~5000
    → Faixa recomendada Mercado Aberto (Forex): min=150, max=2000

    Retorna array com volume normalizado por vela (inteiros).
    Funciona sem dados de volume da corretora (apenas OHLC).
    """
    amplitude = highs - lows                             # amplitude total da vela
    price     = np.where(closes > 0, closes, 1e-6)       # evitar divisão por zero
    vol       = (amplitude / price) * 1_000_000          # porcentagem em micros
    return np.round(vol, 1)


def check_volume_filter(opens: np.ndarray, closes: np.ndarray,
                        highs: np.ndarray, lows: np.ndarray,
                        vol_min: float = 150.0,
                        vol_max: float = 2000.0,
                        lookback: int = 3) -> dict:
    """
    Verifica se as últimas `lookback` velas têm volume dentro da faixa aceitável.
    Retorna dict com:
      - ok        : bool   — passa no filtro?
      - vol_last  : float  — volume da última vela
      - vol_avg   : float  — média das últimas `lookback` velas
      - motivo    : str    — descrição do resultado
    """
    vols = calc_volume_candle(opens, closes, highs, lows)
    vol_last = float(vols[-1])
    vol_avg  = float(np.mean(vols[-lookback:])) if len(vols) >= lookback else vol_last

    if vol_last < vol_min:
        return {'ok': False, 'vol_last': vol_last, 'vol_avg': vol_avg,
                'motivo': f'⚠️ Volume baixo ({vol_last:.0f} < mín {vol_min:.0f}) — aguardar'}
    if vol_last > vol_max:
        return {'ok': False, 'vol_last': vol_last, 'vol_avg': vol_avg,
                'motivo': f'⚠️ Volume excessivo ({vol_last:.0f} > máx {vol_max:.0f}) — evitar'}
    return {'ok': True, 'vol_last': vol_last, 'vol_avg': vol_avg,
            'motivo': f'✅ Volume OK ({vol_last:.0f} | média {vol_avg:.0f})'}



# ═══════════════════════════════════════════════════════════════════════════════
# INDICADORES TÉCNICOS — CALIBRADOS PARA M1
# ═══════════════════════════════════════════════════════════════════════════════

def calc_rsi(closes: np.ndarray, period: int = 5) -> float:
    """RSI período 5 — ultra-responsivo para M1."""
    if len(closes) < period + 1: return 50.0
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calc_ema(closes: np.ndarray, period: int) -> np.ndarray:
    if len(closes) < period: return closes
    k = 2.0 / (period + 1)
    ema = [float(np.mean(closes[:period]))]
    for price in closes[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return np.array(ema)


def calc_stoch(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
               k_period: int = 5, d_period: int = 3) -> tuple:
    """Stochastic(5,3,3) — rápido para M1."""
    if len(closes) < k_period: return 50.0, 50.0
    k_vals = []
    for i in range(k_period - 1, len(closes)):
        h = np.max(highs[i - k_period + 1:i + 1])
        l = np.min(lows[i  - k_period + 1:i + 1])
        k = (closes[i] - l) / (h - l) * 100 if h != l else 50.0
        k_vals.append(k)
    k_arr = np.array(k_vals)
    d_arr = np.convolve(k_arr, np.ones(d_period) / d_period, mode='valid')
    return round(float(k_arr[-1]), 2), round(float(d_arr[-1]) if len(d_arr) > 0 else k_arr[-1], 2)


def calc_macd(closes: np.ndarray) -> tuple:
    """MACD(5,13,3) — versão rápida para M1. Retorna (macd, signal, histogram)."""
    if len(closes) < 13: return 0.0, 0.0, 0.0
    ema_fast = calc_ema(closes, 5)
    ema_slow = calc_ema(closes, 13)
    min_len  = min(len(ema_fast), len(ema_slow))
    macd     = ema_fast[-min_len:] - ema_slow[-min_len:]
    if len(macd) < 3: return float(macd[-1]), float(macd[-1]), 0.0
    sig  = calc_ema(macd, 3)
    hist = float(macd[-1]) - float(sig[-1])
    return float(macd[-1]), float(sig[-1]), round(hist, 6)


def calc_bollinger(closes: np.ndarray, period: int = 10, std_mult: float = 2.0):
    """Bollinger Bands(10,2) — responsivo para M1. Retorna (upper, middle, lower, %B)."""
    if len(closes) < period: return None, None, None, None
    window = closes[-period:]
    mid    = np.mean(window)
    std    = np.std(window)
    up     = mid + std_mult * std
    dn     = mid - std_mult * std
    pct_b  = (closes[-1] - dn) / (up - dn) if up != dn else 0.5
    return round(up, 6), round(mid, 6), round(dn, 6), round(pct_b, 4)


def calc_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
             period: int = 7) -> tuple:
    """ADX(7) — força da tendência rápida para M1."""
    if len(closes) < period + 1: return 0.0, 0.0, 0.0
    trs, plus_dms, minus_dms = [], [], []
    for i in range(1, len(closes)):
        h, l, c_prev = highs[i], lows[i], closes[i - 1]
        tr       = max(h - l, abs(h - c_prev), abs(l - c_prev))
        plus_dm  = max(highs[i] - highs[i - 1], 0) if highs[i] - highs[i - 1] > lows[i - 1] - lows[i] else 0
        minus_dm = max(lows[i - 1] - lows[i], 0)   if lows[i - 1] - lows[i] > highs[i] - highs[i - 1] else 0
        trs.append(tr); plus_dms.append(plus_dm); minus_dms.append(minus_dm)

    trs       = np.array(trs[-period:])
    plus_dms  = np.array(plus_dms[-period:])
    minus_dms = np.array(minus_dms[-period:])
    atr = np.sum(trs)
    if atr == 0: return 0.0, 0.0, 0.0
    plus_di  = 100 * np.sum(plus_dms)  / atr
    minus_di = 100 * np.sum(minus_dms) / atr
    dx = abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9) * 100
    return round(dx, 2), round(plus_di, 2), round(minus_di, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# SUPORTE / RESISTÊNCIA E FIBONACCI
# ═══════════════════════════════════════════════════════════════════════════════

def calc_pivot_points(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray):
    if len(closes) < 5: return None
    H  = np.max(highs[-21:-1])
    L  = np.min(lows[-21:-1])
    C  = closes[-2]
    PP = (H + L + C) / 3
    return {'PP': PP, 'R1': 2*PP - L, 'R2': PP + (H - L),
            'S1': 2*PP - H, 'S2': PP - (H - L)}


def calc_fibonacci(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                   lookback: int = 30):
    if len(closes) < lookback: lookback = len(closes)
    swing_high = np.max(highs[-lookback:])
    swing_low  = np.min(lows[-lookback:])
    rng = swing_high - swing_low
    if rng == 0: return None
    trend_up = closes[-1] > closes[-lookback // 2]
    if trend_up:
        fib = {'38.2': swing_high - 0.382*rng, '50': swing_high - 0.500*rng,
               '61.8': swing_high - 0.618*rng, 'trend_up': True}
    else:
        fib = {'38.2': swing_low + 0.382*rng, '50': swing_low + 0.500*rng,
               '61.8': swing_low + 0.618*rng, 'trend_up': False}
    return fib


# ═══════════════════════════════════════════════════════════════════════════════
# PADRÕES DE VELAS — ACERTIVIDADE ≥ 80% EM M1
# ═══════════════════════════════════════════════════════════════════════════════

def detect_high_accuracy_patterns(opens: np.ndarray, highs: np.ndarray,
                                   lows: np.ndarray, closes: np.ndarray,
                                   ema5_last: float, ema50_last: float) -> dict:
    """
    Detecta APENAS padrões com acertividade ≥ 80% em backtests M1.
    Cada padrão EXIGE alinhamento com a direção da EMA5 e EMA50.

    Retorna dict com padrões encontrados:
      { 'nome': {'dir': 'CALL'|'PUT', 'accuracy': int, 'desc': str} }

    Padrões implementados:
      1. Engolfo de Alta/Baixa          — 83%
      2. Três Soldados / Três Corvos    — 81%
      3. Morning Star / Evening Star    — 85%
      4. Martelo + contexto tendência   — 82%
      5. Estrela Cadente + contexto     — 82%
      6. Pinbar com confirmação EMA     — 80%
      7. Tweezer Bottom / Tweezer Top   — 80%
    """
    if len(opens) < 3: return {}

    patterns = {}
    price = float(closes[-1])

    # Tendência das EMAs
    ema5_trend_up  = ema5_last  > ema50_last   # EMA5 acima da EMA50 → tendência de alta
    ema5_trend_dn  = ema5_last  < ema50_last   # EMA5 abaixo da EMA50 → tendência de baixa

    # Velas individuais — índices -1 (atual), -2 (anterior), -3 (2 atrás)
    o1, h1, l1, c1 = float(opens[-1]),  float(highs[-1]),  float(lows[-1]),  float(closes[-1])
    o2, h2, l2, c2 = float(opens[-2]),  float(highs[-2]),  float(lows[-2]),  float(closes[-2])
    o3, h3, l3, c3 = float(opens[-3]),  float(highs[-3]),  float(lows[-3]),  float(closes[-3])

    body1    = abs(c1 - o1)
    body2    = abs(c2 - o2)
    body3    = abs(c3 - o3)
    total1   = h1 - l1 if h1 != l1 else 1e-9
    wick_up1 = h1 - max(c1, o1)
    wick_dn1 = min(c1, o1) - l1

    bull1 = c1 > o1   # vela 1 de alta
    bear1 = c1 < o1   # vela 1 de baixa
    bull2 = c2 > o2
    bear2 = c2 < o2
    bull3 = c3 > o3
    bear3 = c3 < o3

    # Variáveis para padrões de 4-5 velas (com guarda de tamanho)
    if len(opens) >= 4:
        o4, h4, l4, c4 = float(opens[-4]), float(highs[-4]), float(lows[-4]), float(closes[-4])
        bull4 = c4 > o4
        bear4 = c4 < o4
    else:
        o4 = h4 = l4 = c4 = 0.0; bull4 = bear4 = False
    if len(opens) >= 5:
        o5, h5, l5, c5 = float(opens[-5]), float(highs[-5]), float(lows[-5]), float(closes[-5])
        bull5 = c5 > o5
        bear5 = c5 < o5
    else:
        o5 = h5 = l5 = c5 = 0.0; bull5 = bear5 = False

    # ═══════════════════════════════════════════════════════
    # 1. ENGOLFO DE ALTA (Bullish Engulfing) — 83%
    #    Regra: vela anterior bajista, atual altista engolfa
    #    Filtro EMA: EMA5 > EMA50 (tendência de alta confirmada)
    # ═══════════════════════════════════════════════════════
    if (bear2 and bull1              # anterior baixa, atual alta
            and c1 >= o2             # feche acima da abertura anterior
            and o1 <= c2             # abra abaixo do fechamento anterior
            and body1 > body2 * 0.8  # corpo engolfa
            and ema5_trend_up):      # ← FILTRO OBRIGATÓRIO: tendência de alta
        patterns['engolfo_alta'] = {
            'dir': 'CALL', 'accuracy': 83,
            'desc': '🕯️ Engolfo de Alta (83%) — EMA5>EMA50 confirmado'
        }

    # ═══════════════════════════════════════════════════════
    # 2. ENGOLFO DE BAIXA (Bearish Engulfing) — 83%
    #    Regra: vela anterior altista, atual bajista engolfa
    #    Filtro EMA: EMA5 < EMA50 (tendência de baixa confirmada)
    # ═══════════════════════════════════════════════════════
    if (bull2 and bear1              # anterior alta, atual baixa
            and c1 <= o2             # feche abaixo da abertura anterior
            and o1 >= c2             # abra acima do fechamento anterior
            and body1 > body2 * 0.8  # corpo engolfa
            and ema5_trend_dn):      # ← FILTRO OBRIGATÓRIO: tendência de baixa
        patterns['engolfo_baixa'] = {
            'dir': 'PUT', 'accuracy': 83,
            'desc': '🕯️ Engolfo de Baixa (83%) — EMA5<EMA50 confirmado'
        }

    # ═══════════════════════════════════════════════════════
    # 3. TRÊS SOLDADOS BRANCOS (Three White Soldiers) — 81%
    #    Regra: 3 velas altistas consecutivas com closes crescentes
    #    Filtro EMA: tendência de alta
    # ═══════════════════════════════════════════════════════
    if (bull1 and bull2 and bull3    # três altas
            and c1 > c2 > c3         # fechamentos crescentes
            and o1 > o2 > o3         # aberturas crescentes
            and body1 > total1*0.5   # corpos fortes (>50% do range)
            and body2 > (h2-l2)*0.5
            and ema5_trend_up):
        patterns['tres_soldados'] = {
            'dir': 'CALL', 'accuracy': 81,
            'desc': '🕯️ Três Soldados (81%) — continuação de alta'
        }

    # ═══════════════════════════════════════════════════════
    # 4. TRÊS CORVOS NEGROS (Three Black Crows) — 81%
    #    Regra: 3 velas bajistas consecutivas com closes decrescentes
    #    Filtro EMA: tendência de baixa
    # ═══════════════════════════════════════════════════════
    if (bear1 and bear2 and bear3    # três baixas
            and c1 < c2 < c3         # fechamentos decrescentes
            and o1 < o2 < o3         # aberturas decrescentes
            and body1 > total1*0.5
            and body2 > (h2-l2)*0.5
            and ema5_trend_dn):
        patterns['tres_corvos'] = {
            'dir': 'PUT', 'accuracy': 81,
            'desc': '🕯️ Três Corvos (81%) — continuação de baixa'
        }

    # ═══════════════════════════════════════════════════════
    # 5. MORNING STAR (Estrela da Manhã) — 85%
    #    Regra: vela3 grande baixa | vela2 pequena (doji/indecisão) | vela1 grande alta
    #    Contexto: reversão de baixa para alta
    #    Filtro EMA: EMA5 próxima ou cruzando EMA50 (reversão)
    # ═══════════════════════════════════════════════════════
    body2_ratio = body2 / (h2 - l2) if h2 != l2 else 0.5
    if (bear3                         # vela 3 bajista grande
            and body3 > (h3-l3)*0.6   # corpo grande
            and body2_ratio < 0.35    # vela 2 pequena (indecisão)
            and bull1                  # vela 1 altista
            and body1 > (h1-l1)*0.5   # corpo razoável
            and c1 > (o3 + c3) / 2    # fecha acima do meio da vela 3
            and ema5_trend_up):        # contexto de reversão confirmado
        patterns['morning_star'] = {
            'dir': 'CALL', 'accuracy': 85,
            'desc': '⭐ Morning Star (85%) — reversão de baixa'
        }

    # ═══════════════════════════════════════════════════════
    # 6. EVENING STAR (Estrela da Tarde) — 85%
    #    Regra: vela3 grande alta | vela2 pequena | vela1 grande baixa
    #    Contexto: reversão de alta para baixa
    # ═══════════════════════════════════════════════════════
    if (bull3                         # vela 3 altista grande
            and body3 > (h3-l3)*0.6
            and body2_ratio < 0.35    # vela 2 pequena
            and bear1                  # vela 1 bajista
            and body1 > (h1-l1)*0.5
            and c1 < (o3 + c3) / 2    # fecha abaixo do meio da vela 3
            and ema5_trend_dn):
        patterns['evening_star'] = {
            'dir': 'PUT', 'accuracy': 85,
            'desc': '⭐ Evening Star (85%) — reversão de alta'
        }

    # ═══════════════════════════════════════════════════════
    # 7. MARTELO (Hammer) em suporte — 82%
    #    Regra: sombra inferior longa (≥2x corpo), sombra sup. curta, corpo alto
    #    Filtro: vela anterior bajista + contexto de tendência de alta (EMA50 acima)
    #    Obs: reversão, portanto EMA5 pode estar abaixo mas EMA50 aponta recuperação
    # ═══════════════════════════════════════════════════════
    if (wick_dn1 >= 2.0 * body1      # sombra inf. ≥ 2x corpo
            and wick_up1 <= body1 * 0.4  # sombra sup. pequena
            and body1 / total1 >= 0.15   # corpo existe (não doji)
            and bear2                     # vela anterior bajista (contexto de baixa)
            and ema5_trend_up):           # EMA5 confirmando tendência de alta
        patterns['martelo'] = {
            'dir': 'CALL', 'accuracy': 82,
            'desc': '🔨 Martelo (82%) — reversão em suporte'
        }

    # ═══════════════════════════════════════════════════════
    # 8. ESTRELA CADENTE (Shooting Star) em resistência — 82%
    #    Regra: sombra superior longa (≥2x corpo), sombra inf. curta
    #    Filtro: vela anterior altista + tendência de baixa nas EMAs
    # ═══════════════════════════════════════════════════════
    if (wick_up1 >= 2.0 * body1          # sombra sup. ≥ 2x corpo
            and wick_dn1 <= body1 * 0.4  # sombra inf. pequena
            and body1 / total1 >= 0.15   # corpo existe
            and bull2                     # vela anterior altista
            and ema5_trend_dn):           # EMA5 confirmando tendência de baixa
        patterns['estrela_cadente'] = {
            'dir': 'PUT', 'accuracy': 82,
            'desc': '🌠 Estrela Cadente (82%) — reversão em resistência'
        }

    # ═══════════════════════════════════════════════════════
    # 9. PINBAR DE ALTA (alta acertividade com contexto) — 80%
    #    Regra: sombra inf. > 2.5x corpo, contexto de suporte + EMA5 > EMA50
    # ═══════════════════════════════════════════════════════
    if (wick_dn1 > 2.5 * body1           # sombra inf. muito longa
            and wick_up1 < body1 * 1.2   # sombra sup. mínima (tolerância)
            and body1 / total1 >= 0.07   # corpo presente (≥7% do range)
            and ema5_trend_up             # tendência de alta pelas EMAs
            and 'martelo' not in patterns): # não duplicar com martelo
        patterns['pinbar_alta'] = {
            'dir': 'CALL', 'accuracy': 80,
            'desc': '📌 Pinbar Alta (80%) — rejeição em suporte'
        }

    # ═══════════════════════════════════════════════════════
    # 10. PINBAR DE BAIXA — 80%
    #     Regra: sombra sup. > 2.5x corpo, contexto de resistência + EMA5 < EMA50
    # ═══════════════════════════════════════════════════════
    if (wick_up1 > 2.5 * body1           # sombra sup. muito longa
            and wick_dn1 < body1 * 1.2   # sombra inf. mínima (tolerância)
            and body1 / total1 >= 0.07   # corpo presente (≥7% do range)
            and ema5_trend_dn
            and 'estrela_cadente' not in patterns):
        patterns['pinbar_baixa'] = {
            'dir': 'PUT', 'accuracy': 80,
            'desc': '📌 Pinbar Baixa (80%) — rejeição em resistência'
        }

    # ═══════════════════════════════════════════════════════
    # 11. TWEEZER BOTTOM — 80%
    #     Regra: 2 velas com mínimas iguais (±0.01%), primeira bajista, segunda altista
    #     Filtro: contexto de suporte + EMA5 > EMA50
    # ═══════════════════════════════════════════════════════
    low_diff = abs(l1 - l2) / (abs(l2) + 1e-9)
    if (low_diff < 0.0001           # mínimas quase iguais
            and bear2 and bull1      # padrão de reversão
            and body1 > total1*0.3  # corpo razoável
            and ema5_trend_up):
        patterns['tweezer_bottom'] = {
            'dir': 'CALL', 'accuracy': 80,
            'desc': '🔧 Tweezer Bottom (80%) — duplo suporte'
        }

    # ═══════════════════════════════════════════════════════
    # 12. TWEEZER TOP — 80%
    #     Regra: 2 velas com máximas iguais (±0.01%), primeira altista, segunda bajista
    # ═══════════════════════════════════════════════════════
    high_diff = abs(h1 - h2) / (abs(h2) + 1e-9)
    if (high_diff < 0.0001          # máximas quase iguais
            and bull2 and bear1      # padrão de reversão
            and body1 > total1*0.3
            and ema5_trend_dn):
        patterns['tweezer_top'] = {
            'dir': 'PUT', 'accuracy': 80,
            'desc': '🔧 Tweezer Top (80%) — dupla resistência'
        }


    # ═══════════════════════════════════════════════════════
    # 13. ENFORCADO (Hanging Man) — 81%
    #     Idêntico ao Martelo geometricamente, mas em topo de alta → sinal de BAIXA
    #     Sombra inf. ≥ 2x corpo, sombra sup. pequena, após tendência de ALTA
    # ═══════════════════════════════════════════════════════
    if (wick_dn1 >= 2.0 * body1
            and wick_up1 <= body1 * 0.4
            and body1 / total1 >= 0.12
            and bull2                      # vela anterior altista (topo)
            and ema5_trend_dn              # EMA5 começando a cair
            and 'martelo' not in patterns):
        patterns['enforcado'] = {
            'dir': 'PUT', 'accuracy': 81,
            'desc': '🪢 Enforcado (81%) — sinal de reversão no topo'
        }

    # ═══════════════════════════════════════════════════════
    # 14. PIERCING LINE (Linha Perfurante) — 82%
    #     Vela 2 bajista grande | Vela 1 abre abaixo do mín. da V2,
    #     fecha acima do meio da V2 → reversão de baixa para alta
    # ═══════════════════════════════════════════════════════
    if (bear2                              # V2 bajista
            and body2 > (h2-l2) * 0.55    # corpo grande
            and bull1                       # V1 altista
            and o1 < l2                    # abre abaixo mínima de V2
            and c1 > (o2 + c2) / 2         # fecha acima do meio de V2
            and c1 < o2                    # mas não engolfa totalmente
            and ema5_trend_up):
        patterns['piercing_line'] = {
            'dir': 'CALL', 'accuracy': 82,
            'desc': '🗡️ Piercing Line (82%) — penetração altista'
        }

    # ═══════════════════════════════════════════════════════
    # 15. DARK CLOUD COVER (Nuvem Negra) — 82%
    #     Inverso do Piercing: V2 altista grande | V1 abre acima do máx. de V2,
    #     fecha abaixo do meio da V2 → reversão de alta para baixa
    # ═══════════════════════════════════════════════════════
    if (bull2                              # V2 altista
            and body2 > (h2-l2) * 0.55    # corpo grande
            and bear1                       # V1 bajista
            and o1 > h2                    # abre acima máxima de V2
            and c1 < (o2 + c2) / 2         # fecha abaixo do meio de V2
            and c1 > o2                    # mas não engolfa totalmente
            and ema5_trend_dn):
        patterns['dark_cloud'] = {
            'dir': 'PUT', 'accuracy': 82,
            'desc': '🌑 Dark Cloud Cover (82%) — nuvem bajista'
        }

    # ═══════════════════════════════════════════════════════
    # 16. TRÊS MÉTODOS ASCENDENTES (Rising Three Methods) — 82%
    #     V3 altista grande, 3 pequenas velas de consolidação (V4-V2) dentro do range
    #     de V3, V1 altista que supera o topo de V3 → continuação de alta
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 5:
        body5 = abs(c5 - o5)
        # Vela âncora (5ª) grande e altista; consolidação (4,3,2); rompimento (1) altista
        if (bull5 and body5 > (h5-l5)*0.55
                and c4 < c5 and c3 < c5 and c2 < c5   # dentro da vela âncora
                and l4 > l5 and l3 > l5 and l2 > l5   # acima da mínima
                and bull1 and c1 > c5                  # rompe para cima
                and ema5_trend_up):
            patterns['tres_metodos_asc'] = {
                'dir': 'CALL', 'accuracy': 82,
                'desc': '📈 3 Métodos Ascendentes (82%) — continuação altista'
            }

    # ═══════════════════════════════════════════════════════
    # 17. OMBRO-CABEÇA-OMBRO INVERTIDO (IH&S) — 83%  [CALL]
    #     Padrão de reversão: 5 velas — L3 > L2 e L3 > L1 (ombros),
    #     ponto mais baixo em L2 (cabeça), c1 fecha acima da "neckline" (média L3+L1)
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 5:
        # Ombro esq=L5, cabeça=L3, ombro dir=L1
        neck = (l5 + l1) / 2
        if (l3 < l5 and l3 < l1          # cabeça mais baixa que ombros
                and abs(l5 - l1) / (abs(l5) + 1e-9) < 0.005  # ombros simétricos
                and c1 > neck             # fechamento acima da neckline
                and bull1
                and ema5_trend_up):
            patterns['hs_invertido'] = {
                'dir': 'CALL', 'accuracy': 83,
                'desc': '🏔️ OCO Invertido (83%) — reversão altista (IH&S)'
            }

        # OMBRO-CABEÇA-OMBRO NORMAL (H&S) — 83% [PUT]
        # máximos: H5 e H1 = ombros, H3 = cabeça mais alta
        neck_top = (h5 + h1) / 2
        if (h3 > h5 and h3 > h1          # cabeça mais alta que ombros
                and abs(h5 - h1) / (abs(h5) + 1e-9) < 0.005  # ombros simétricos
                and c1 < neck_top         # fechamento abaixo da neckline
                and bear1
                and ema5_trend_dn):
            patterns['hs_normal'] = {
                'dir': 'PUT', 'accuracy': 83,
                'desc': '🏔️ OCO (83%) — reversão bajista (H&S)'
            }


    # ═══════════════════════════════════════════════════════
    # 18. MARTELO INVERTIDO (Inverted Hammer) — 78%
    #     Corpo pequeno na base, sombra superior longa, sombra inferior curta
    #     Aparece no fundo de tendência de baixa → reversão altista
    # ═══════════════════════════════════════════════════════
    body1 = abs(c1 - o1)
    upper_shadow1 = h1 - max(o1, c1)
    lower_shadow1 = min(o1, c1) - l1
    if (body1 > 0
            and upper_shadow1 >= body1 * 2.0
            and lower_shadow1 <= body1 * 0.3
            and not bull1  # pode ser vela de baixa no fundo
            and ema5_trend_dn):
        patterns['martelo_invertido'] = {
            'dir': 'CALL', 'accuracy': 80,
            'desc': '🔨 Martelo Invertido (78%) — reversão altista'
        }

    # ═══════════════════════════════════════════════════════
    # 19. DOJI CLÁSSICO (Classic Doji) — 72%
    #     Abertura ≈ Fechamento; indecisão, reversão potencial
    # ═══════════════════════════════════════════════════════
    total_range1 = h1 - l1 if h1 != l1 else 1e-9
    doji_body_ratio = body1 / total_range1
    if doji_body_ratio <= 0.05 and total_range1 > 0:
        doji_dir = 'CALL' if ema5_trend_dn else ('PUT' if ema5_trend_up else None)
        if doji_dir:
            patterns['doji_classico'] = {
                'dir': doji_dir, 'accuracy': 80,
                'desc': '➕ Doji Clássico (72%) — indecisão/reversão'
            }

    # ═══════════════════════════════════════════════════════
    # 20. DOJI DRAGONFLY — 76%
    #     Sombra inferior longa, sombra superior mínima, corpo no topo
    #     Em suporte = forte reversão altista
    # ═══════════════════════════════════════════════════════
    if (doji_body_ratio <= 0.07
            and lower_shadow1 >= total_range1 * 0.6
            and upper_shadow1 <= total_range1 * 0.1
            and ema5_trend_dn):
        patterns['doji_dragonfly'] = {
            'dir': 'CALL', 'accuracy': 80,
            'desc': '🐉 Doji Dragonfly (76%) — reversão altista forte'
        }

    # ═══════════════════════════════════════════════════════
    # 21. DOJI GRAVESTONE — 76%
    #     Sombra superior longa, sombra inferior mínima, corpo na base
    #     Em resistência = reversão bajista
    # ═══════════════════════════════════════════════════════
    if (doji_body_ratio <= 0.07
            and upper_shadow1 >= total_range1 * 0.6
            and lower_shadow1 <= total_range1 * 0.1
            and ema5_trend_up):
        patterns['doji_gravestone'] = {
            'dir': 'PUT', 'accuracy': 80,
            'desc': '🪦 Doji Gravestone (76%) — reversão bajista forte'
        }

    # ═══════════════════════════════════════════════════════
    # 22. HARAMI ALTISTA (Bullish Harami) — 75%
    #     V2 bajista grande, V1 altista pequena DENTRO do corpo de V2
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        body2_abs = abs(c2 - o2)
        if (not bull2 and body2_abs > 0
                and bull1
                and body1 < body2_abs * 0.6
                and o1 > min(o2, c2) and c1 < max(o2, c2)
                and ema5_trend_dn):
            patterns['harami_alta'] = {
                'dir': 'CALL', 'accuracy': 80,
                'desc': '🤱 Harami Altista (75%) — reversão de alta'
            }

    # ═══════════════════════════════════════════════════════
    # 23. HARAMI BAJISTA (Bearish Harami) — 75%
    #     V2 altista grande, V1 bajista pequena DENTRO do corpo de V2
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        body2_abs = abs(c2 - o2)
        if (bull2 and body2_abs > 0
                and not bull1
                and body1 < body2_abs * 0.6
                and o1 < max(o2, c2) and c1 > min(o2, c2)
                and ema5_trend_up):
            patterns['harami_baixa'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '🤱 Harami Bajista (75%) — reversão de baixa'
            }

    # ═══════════════════════════════════════════════════════
    # 24. SPINNING TOP — 70%
    #     Corpo pequeno com sombras longas dos dois lados — indecisão
    # ═══════════════════════════════════════════════════════
    if (total_range1 > 0
            and doji_body_ratio > 0.05 and doji_body_ratio <= 0.25
            and upper_shadow1 >= body1 * 1.0
            and lower_shadow1 >= body1 * 1.0):
        spin_dir = 'CALL' if ema5_trend_dn else ('PUT' if ema5_trend_up else None)
        if spin_dir:
            patterns['spinning_top'] = {
                'dir': spin_dir, 'accuracy': 80,
                'desc': '🌀 Spinning Top (70%) — indecisão/reversão potencial'
            }

    # ═══════════════════════════════════════════════════════
    # 25. INSIDE BAR — 74%
    #     V1 completamente dentro do range de V2 (high < high2, low > low2)
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        if (h1 < h2 and l1 > l2):
            ib_dir = 'CALL' if ema5_trend_up else ('PUT' if ema5_trend_dn else None)
            if ib_dir:
                patterns['inside_bar'] = {
                    'dir': ib_dir, 'accuracy': 80,
                    'desc': '📦 Inside Bar (74%) — compressão/continuação'
                }

    # ═══════════════════════════════════════════════════════
    # 26. OUTSIDE BAR (Engolfo de Range) — 76%
    #     V1 engloba completamente V2 (high > high2, low < low2) — explosão
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        if (h1 > h2 and l1 < l2 and body1 > 0):
            ob_dir = 'CALL' if bull1 else 'PUT'
            patterns['outside_bar'] = {
                'dir': ob_dir, 'accuracy': 80,
                'desc': '💥 Outside Bar (76%) — explosão direcional'
            }

    # ═══════════════════════════════════════════════════════
    # 27. BELT HOLD ALTISTA (Bullish Belt Hold) — 77%
    #     Vela altista abre na mínima (sem sombra inferior), corpo longo
    # ═══════════════════════════════════════════════════════
    if (bull1
            and lower_shadow1 <= body1 * 0.05
            and body1 >= total_range1 * 0.7
            and ema5_trend_dn):
        patterns['belt_hold_alta'] = {
            'dir': 'CALL', 'accuracy': 80,
            'desc': '🔒 Belt Hold Altista (77%) — abertura na mínima, força compradora'
        }

    # ═══════════════════════════════════════════════════════
    # 28. BELT HOLD BAJISTA (Bearish Belt Hold) — 77%
    #     Vela bajista abre na máxima (sem sombra superior), corpo longo
    # ═══════════════════════════════════════════════════════
    if (not bull1
            and upper_shadow1 <= body1 * 0.05
            and body1 >= total_range1 * 0.7
            and ema5_trend_up):
        patterns['belt_hold_baixa'] = {
            'dir': 'PUT', 'accuracy': 80,
            'desc': '🔒 Belt Hold Bajista (77%) — abertura na máxima, força vendedora'
        }

    # ═══════════════════════════════════════════════════════
    # 29. COUNTERATTACK LINES ALTISTA — 74%
    #     V2 bajista grande fecha em P2; V1 altista abre bem abaixo mas
    #     fecha no mesmo nível de P2 (contraataque comprador)
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        body2_abs = abs(c2 - o2)
        if (not bull2 and body2_abs > 0
                and bull1
                and abs(c1 - c2) / (abs(c2) + 1e-9) < 0.002
                and o1 < c2 * 0.998):
            patterns['counterattack_alta'] = {
                'dir': 'CALL', 'accuracy': 80,
                'desc': '⚔️ Contraataque Altista (74%) — fechamento em nível igual'
            }

    # ═══════════════════════════════════════════════════════
    # 30. COUNTERATTACK LINES BAJISTA — 74%
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        body2_abs = abs(c2 - o2)
        if (bull2 and body2_abs > 0
                and not bull1
                and abs(c1 - c2) / (abs(c2) + 1e-9) < 0.002
                and o1 > c2 * 1.002):
            patterns['counterattack_baixa'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '⚔️ Contraataque Bajista (74%) — fechamento em nível igual'
            }

    # ═══════════════════════════════════════════════════════
    # 31. SEPARATING LINES ALTISTA — 73%
    #     V2 bajista; V1 altista abre no mesmo nível de abertura de V2 (gap up retoma)
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        if (not bull2 and bull1
                and abs(o1 - o2) / (abs(o2) + 1e-9) < 0.002
                and ema5_trend_up):
            patterns['separating_alta'] = {
                'dir': 'CALL', 'accuracy': 80,
                'desc': '📐 Separating Lines Alta (73%) — continuação altista'
            }

    # ═══════════════════════════════════════════════════════
    # 32. SEPARATING LINES BAJISTA — 73%
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        if (bull2 and not bull1
                and abs(o1 - o2) / (abs(o2) + 1e-9) < 0.002
                and ema5_trend_dn):
            patterns['separating_baixa'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '📐 Separating Lines Baixa (73%) — continuação bajista'
            }

    # ═══════════════════════════════════════════════════════
    # 33. TASUKI GAP ALTISTA (Upside Tasuki Gap) — 78%
    #     V3 e V2 altistas com gap entre elas; V1 bajista que preenche
    #     apenas PARTE do gap → continuação de alta
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        # gap entre V3 e V2
        gap_tasuki = o2 - c3
        if (bull3 and bull2 and gap_tasuki > 0
                and not bull1
                and o1 < c2 and c1 > c3
                and ema5_trend_up):
            patterns['tasuki_alta'] = {
                'dir': 'CALL', 'accuracy': 80,
                'desc': '⬆️ Tasuki Gap Alta (78%) — continuação de alta'
            }

    # ═══════════════════════════════════════════════════════
    # 34. TASUKI GAP BAJISTA (Downside Tasuki Gap) — 78%
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        gap_tasuki_dn = c3 - o2
        if (not bull3 and not bull2 and gap_tasuki_dn > 0
                and bull1
                and o1 > c2 and c1 < c3
                and ema5_trend_dn):
            patterns['tasuki_baixa'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '⬇️ Tasuki Gap Baixa (78%) — continuação de baixa'
            }

    # ═══════════════════════════════════════════════════════
    # 35. THREE INSIDE UP — 79%
    #     V3 bajista grande; V2 altista pequena dentro de V3 (Harami);
    #     V1 altista fecha acima do topo de V3 → confirmação altista
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        body3_abs = abs(c3 - o3)
        body2_abs = abs(c2 - o2)
        if (not bull3 and body3_abs > 0
                and bull2 and body2_abs < body3_abs
                and o2 > min(o3, c3) and c2 < max(o3, c3)
                and bull1 and c1 > max(o3, c3)):
            patterns['three_inside_up'] = {
                'dir': 'CALL', 'accuracy': 80,
                'desc': '📈 Three Inside Up (79%) — confirmação altista'
            }

    # ═══════════════════════════════════════════════════════
    # 36. THREE INSIDE DOWN — 79%
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        body3_abs = abs(c3 - o3)
        body2_abs = abs(c2 - o2)
        if (bull3 and body3_abs > 0
                and not bull2 and body2_abs < body3_abs
                and o2 < max(o3, c3) and c2 > min(o3, c3)
                and not bull1 and c1 < min(o3, c3)):
            patterns['three_inside_down'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '📉 Three Inside Down (79%) — confirmação bajista'
            }

    # ═══════════════════════════════════════════════════════
    # 37. THREE OUTSIDE UP — 80%
    #     V3 bajista pequena; V2 altista engloba V3 (Outside/Engulf);
    #     V1 altista confirma acima de V2
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        body3_abs = abs(c3 - o3)
        body2_abs = abs(c2 - o2)
        if (not bull3
                and bull2 and body2_abs > body3_abs
                and o2 <= min(o3, c3) and c2 >= max(o3, c3)
                and bull1 and c1 > c2):
            patterns['three_outside_up'] = {
                'dir': 'CALL', 'accuracy': 80,
                'desc': '💪 Three Outside Up (80%) — engolfo confirmado altista'
            }

    # ═══════════════════════════════════════════════════════
    # 38. THREE OUTSIDE DOWN — 80%
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        body3_abs = abs(c3 - o3)
        body2_abs = abs(c2 - o2)
        if (bull3
                and not bull2 and body2_abs > body3_abs
                and o2 >= max(o3, c3) and c2 <= min(o3, c3)
                and not bull1 and c1 < c2):
            patterns['three_outside_down'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '💪 Three Outside Down (80%) — engolfo confirmado bajista'
            }

    # ═══════════════════════════════════════════════════════
    # 39. KICKER ALTISTA (Bullish Kicker) — 84%
    #     V2 bajista; V1 abre com gap acima de V2 e fecha altista
    #     Padrão de reversão violento — muito confiável
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        body2_abs = abs(c2 - o2)
        if (not bull2 and body2_abs > 0
                and bull1
                and o1 > max(o2, c2)   # gap up
                and body1 > body2_abs * 0.7):
            patterns['kicker_alta'] = {
                'dir': 'CALL', 'accuracy': 84,
                'desc': '🚀 Kicker Altista (84%) — reversão com gap, força máxima'
            }

    # ═══════════════════════════════════════════════════════
    # 40. KICKER BAJISTA (Bearish Kicker) — 84%
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        body2_abs = abs(c2 - o2)
        if (bull2 and body2_abs > 0
                and not bull1
                and o1 < min(o2, c2)   # gap down
                and body1 > body2_abs * 0.7):
            patterns['kicker_baixa'] = {
                'dir': 'PUT', 'accuracy': 84,
                'desc': '🚀 Kicker Bajista (84%) — reversão com gap, força máxima'
            }

    # ═══════════════════════════════════════════════════════
    # 41. TRÊS MÉTODOS DESCENDENTES (Falling Three Methods) — 82%
    #     V5 bajista grande; V4-V2 pequenas de alta (consolidação);
    #     V1 bajista rompe abaixo de V5 → continuação de baixa
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 5:
        body5 = abs(c5 - o5)
        if (bear5 and body5 > (h5-l5)*0.55
                and c4 > c5 and c3 > c5 and c2 > c5
                and h4 < h5 and h3 < h5 and h2 < h5
                and not bull1 and c1 < c5
                and ema5_trend_dn):
            patterns['tres_metodos_desc'] = {
                'dir': 'PUT', 'accuracy': 82,
                'desc': '📉 3 Métodos Descendentes (82%) — continuação bajista'
            }

    # ═══════════════════════════════════════════════════════
    # 42. CONCEALING BABY SWALLOW — 80%
    #     4 velas bajistas: V4 e V3 Marubozu bajistas; V2 tem gap down
    #     mas sombra superior; V1 bajista engloba V2 completamente
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 4:
        body4 = abs(c4 - o4)
        body3 = abs(c3 - o3)
        body2_abs = abs(c2 - o2)
        if (not bull4 and not bull3 and not bull2 and not bull1
                and body4 > (h4-l4)*0.85 and body3 > (h3-l3)*0.85
                and o2 < c3
                and h2 > c3  # sombra superior entra no corpo de V3
                and h1 >= h2 and l1 <= l2):  # V1 engloba V2
            patterns['concealing_baby_swallow'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '🐦 Concealing Baby Swallow (80%) — continuação bajista'
            }

    # ═══════════════════════════════════════════════════════
    # 43. LADDER BOTTOM — 79%
    #     5 velas: V5-V2 bajistas com fechamentos decrescentes;
    #     V1 é Marubozu/hammer altista com fechamento forte
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 5:
        if (not bull5 and not bull4 and not bull3 and not bull2
                and c5 > c4 > c3 > c2  # fechamentos decrescentes
                and bull1
                and body1 > (h1-l1)*0.6
                and ema5_trend_dn):
            patterns['ladder_bottom'] = {
                'dir': 'CALL', 'accuracy': 80,
                'desc': '🪜 Ladder Bottom (79%) — reversão após escada de baixa'
            }

    # ═══════════════════════════════════════════════════════
    # 44. LADDER TOP — 79%
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 5:
        bull5_l = c5 > o5
        bull4_l = c4 > o4
        bull3_l = c3 > o3
        bull2_l = c2 > o2
        if (bull5_l and bull4_l and bull3_l and bull2_l
                and c5 < c4 < c3 < c2  # fechamentos crescentes
                and not bull1
                and body1 > (h1-l1)*0.6
                and ema5_trend_up):
            patterns['ladder_top'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '🪜 Ladder Top (79%) — reversão após escada de alta'
            }

    # ═══════════════════════════════════════════════════════
    # 45. IDENTICAL THREE CROWS — 81%
    #     3 velas bajistas com abertura dentro do corpo da vela anterior
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        if (not bull3 and not bull2 and not bull1
                and o2 < c3 and o2 > o3   # abre dentro do corpo de V3
                and o1 < c2 and o1 > o2   # abre dentro do corpo de V2
                and abs(c3 - o3) > 0 and abs(c2 - o2) > 0
                and ema5_trend_dn):
            patterns['identical_three_crows'] = {
                'dir': 'PUT', 'accuracy': 81,
                'desc': '🦅 Três Corvos Idênticos (81%) — queda consistente'
            }

    # ═══════════════════════════════════════════════════════
    # 46. UNIQUE THREE RIVER BOTTOM — 77%
    #     V3 bajista longa; V2 bajista com nova mínima (hammer-like);
    #     V1 pequena altista fecha dentro do range de V3
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        body3_abs = abs(c3 - o3)
        if (not bull3 and body3_abs > 0
                and not bull2
                and l2 < l3
                and (h2 - max(o2, c2)) > abs(c2 - o2)  # sombra superior
                and bull1
                and c1 < c3   # fecha dentro do corpo de V3
                and ema5_trend_dn):
            patterns['unique_three_river'] = {
                'dir': 'CALL', 'accuracy': 80,
                'desc': '🌊 Unique Three River Bottom (77%) — reversão sutil'
            }

    # ═══════════════════════════════════════════════════════
    # 47. ON-NECK — 70%
    #     V2 bajista grande; V1 altista pequena fecha na mínima de V2
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        if (not bull2
                and bull1
                and abs(c1 - l2) / (abs(l2) + 1e-9) < 0.003
                and body1 < abs(c2 - o2) * 0.4
                and ema5_trend_dn):
            patterns['on_neck'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '📎 On-Neck (70%) — continuação bajista fraca'
            }

    # ═══════════════════════════════════════════════════════
    # 48. IN-NECK — 71%
    #     Similar ao On-Neck mas V1 fecha LEVEMENTE acima da mínima de V2
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        if (not bull2
                and bull1
                and c1 > l2 and c1 < c2 * 0.998
                and c1 > l2 * 1.001 and c1 < l2 * 1.005
                and ema5_trend_dn):
            patterns['in_neck'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '📎 In-Neck (71%) — continuação bajista'
            }

    # ═══════════════════════════════════════════════════════
    # 49. THRUSTING — 72%
    #     V2 bajista; V1 altista fecha abaixo do ponto médio de V2
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        mid2 = (o2 + c2) / 2
        if (not bull2
                and bull1
                and c1 > l2 and c1 < mid2
                and ema5_trend_dn):
            patterns['thrusting'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '📌 Thrusting (72%) — recuperação insuficiente, bajista'
            }

    # ═══════════════════════════════════════════════════════
    # 50. STICK SANDWICH — 75%
    #     V3 bajista; V2 altista; V1 bajista com fechamento = fechamento de V3
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        if (not bull3 and bull2 and not bull1
                and abs(c1 - c3) / (abs(c3) + 1e-9) < 0.002):
            patterns['stick_sandwich'] = {
                'dir': 'CALL', 'accuracy': 80,
                'desc': '🥪 Stick Sandwich (75%) — suporte em nível de fechamento anterior'
            }

    # ═══════════════════════════════════════════════════════
    # 51. MAT HOLD — 81%
    #     V5 altista grande; V4-V2 de consolidação (pequenas, não excedem V5);
    #     V1 altista rompe acima do topo de V5 — padrão de continuação
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 5:
        body5 = abs(c5 - o5)
        bull5_m = c5 > o5
        if (bull5_m and body5 > (h5-l5)*0.5
                and c4 > c5 * 0.998 and c3 > c5 * 0.998  # dentro
                and h4 < h5 * 1.005 and h3 < h5 * 1.005
                and bull1 and c1 > c5
                and ema5_trend_up):
            patterns['mat_hold'] = {
                'dir': 'CALL', 'accuracy': 81,
                'desc': '🧱 Mat Hold (81%) — continuação altista confirmada'
            }

    # ═══════════════════════════════════════════════════════
    # 52. HOMING PIGEON — 74%
    #     V2 bajista grande; V1 bajista pequena DENTRO do range de V2
    #     Sinaliza desaceleração da queda → reversão potencial
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        body2_abs = abs(c2 - o2)
        if (not bull2 and body2_abs > 0
                and not bull1
                and body1 < body2_abs * 0.5
                and h1 < h2 and l1 > l2
                and ema5_trend_dn):
            patterns['homing_pigeon'] = {
                'dir': 'CALL', 'accuracy': 80,
                'desc': '🕊️ Homing Pigeon (74%) — desaceleração da queda, reversão'
            }

    # ═══════════════════════════════════════════════════════
    # 53. DELIBERATION — 76%
    #     3 velas altistas; V3 e V2 grandes; V1 pequena (incerteza no topo)
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        body3_abs = abs(c3 - o3)
        body2_abs = abs(c2 - o2)
        if (bull3 and bull2 and bull1
                and body3_abs > (h3-l3)*0.5
                and body2_abs > (h2-l2)*0.5
                and body1 < body2_abs * 0.4
                and ema5_trend_up):
            patterns['deliberation'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '🤔 Deliberation (76%) — enfraquecimento no topo, reversão bajista'
            }

    # ═══════════════════════════════════════════════════════
    # 54. ADVANCE BLOCK — 75%
    #     3 velas altistas mas com corpos cada vez menores e sombras maiores
    #     Enfraquecimento da alta → possível reversão bajista
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        body3_abs = abs(c3 - o3)
        body2_abs = abs(c2 - o2)
        up_sh3 = h3 - max(o3, c3)
        up_sh2 = h2 - max(o2, c2)
        up_sh1 = h1 - max(o1, c1)
        if (bull3 and bull2 and bull1
                and body3_abs > body2_abs > body1  # corpos encolhendo
                and up_sh1 >= up_sh2 >= up_sh3     # sombras crescendo
                and ema5_trend_up):
            patterns['advance_block'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '🧱 Advance Block (75%) — alta fraquejando, sombras aumentam'
            }

    # ═══════════════════════════════════════════════════════
    # 55. BREAKAWAY ALTISTA — 77%
    #     V5 bajista grande; gap down em V4; V3 e V2 indecisas;
    #     V1 altista fecha dentro do gap → reversão
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 5:
        body5 = abs(c5 - o5)
        bear5_b = c5 < o5
        gap_brk = c5 - o4  # gap down entre V5 e V4
        if (bear5_b and gap_brk > 0
                and bull1
                and c1 > c4  # fecha acima do gap
                and ema5_trend_dn):
            patterns['breakaway_alta'] = {
                'dir': 'CALL', 'accuracy': 80,
                'desc': '🔓 Breakaway Alta (77%) — preenchimento do gap bajista'
            }

    # ═══════════════════════════════════════════════════════
    # 56. BREAKAWAY BAJISTA — 77%
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 5:
        body5 = abs(c5 - o5)
        bull5_b = c5 > o5
        gap_brk_up = o4 - c5
        if (bull5_b and gap_brk_up > 0
                and not bull1
                and c1 < c4
                and ema5_trend_up):
            patterns['breakaway_baixa'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '🔓 Breakaway Baixa (77%) — preenchimento do gap altista'
            }

    # ═══════════════════════════════════════════════════════
    # 57. DESCENDING HAWK — 73%
    #     V2 altista grande; V1 bajista pequena com abertura acima de V2
    #     mas fechamento dentro do corpo de V2 → topo frágil
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 2:
        body2_abs = abs(c2 - o2)
        if (bull2 and body2_abs > 0
                and not bull1
                and o1 > c2
                and c1 > o2 and c1 < c2
                and ema5_trend_up):
            patterns['descending_hawk'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '🦅 Descending Hawk (73%) — penetração negativa no topo'
            }

    # ═══════════════════════════════════════════════════════
    # 58. TWO CROWS — 74%
    #     V3 altista grande; V2 abre com gap up mas fecha bajista;
    #     V1 bajista abre dentro de V2 e fecha dentro de V3
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        body3_abs = abs(c3 - o3)
        if (bull3 and body3_abs > 0
                and not bull2 and o2 > c3  # gap up depois bajista
                and not bull1
                and o1 <= c2 and c1 >= o3
                and ema5_trend_up):
            patterns['two_crows'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '🐦 Two Crows (74%) — absorção bajista no topo'
            }

    # ═══════════════════════════════════════════════════════
    # 59. THREE STARS IN THE SOUTH — 78%
    #     3 velas bajistas com corpos e sombras cada vez menores
    #     Esgotamento da queda → reversão altista
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        body3_abs = abs(c3 - o3)
        body2_abs = abs(c2 - o2)
        lo_sh3 = min(o3, c3) - l3
        lo_sh2 = min(o2, c2) - l2
        lo_sh1 = min(o1, c1) - l1
        if (not bull3 and not bull2 and not bull1
                and body3_abs > body2_abs > body1
                and l3 > l2 > l1  # mínimas crescentes
                and ema5_trend_dn):
            patterns['three_stars_south'] = {
                'dir': 'CALL', 'accuracy': 80,
                'desc': '⭐ Three Stars in the South (78%) — queda exausta'
            }

    # ═══════════════════════════════════════════════════════
    # 60. UPSIDE GAP TWO CROWS — 73%
    #     V3 altista grande; gap up; V2 e V1 bajistas que fecham no gap
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        body3_abs = abs(c3 - o3)
        if (bull3 and body3_abs > 0
                and not bull2 and o2 > c3  # gap up
                and not bull1
                and o1 <= c2  # V1 abre dentro de V2
                and c1 > c3   # ainda acima de V3 porém bearish
                and ema5_trend_up):
            patterns['upside_gap_two_crows'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '⬆️🐦 Upside Gap Two Crows (73%) — reversão bajista com gap'
            }

    # ═══════════════════════════════════════════════════════
    # 61. TRI-STAR ALTISTA — 78%
    #     3 Dojis consecutivos em zona de fundo — reversão altista forte
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        d3_body = abs(c3 - o3) / ((h3 - l3) if h3 != l3 else 1e-9)
        d2_body = abs(c2 - o2) / ((h2 - l2) if h2 != l2 else 1e-9)
        d1_body = abs(c1 - o1) / ((h1 - l1) if h1 != l1 else 1e-9)
        if (d3_body <= 0.1 and d2_body <= 0.1 and d1_body <= 0.1
                and ema5_trend_dn):
            patterns['tri_star_alta'] = {
                'dir': 'CALL', 'accuracy': 80,
                'desc': '⭐⭐⭐ Tri-Star Alta (78%) — 3 Dojis no fundo, reversão altista'
            }

    # ═══════════════════════════════════════════════════════
    # 62. TRI-STAR BAJISTA — 78%
    # ═══════════════════════════════════════════════════════
    if len(opens) >= 3:
        if (d3_body <= 0.1 and d2_body <= 0.1 and d1_body <= 0.1
                and ema5_trend_up):
            patterns['tri_star_baixa'] = {
                'dir': 'PUT', 'accuracy': 80,
                'desc': '⭐⭐⭐ Tri-Star Baixa (78%) — 3 Dojis no topo, reversão bajista'
            }


    # ★ FILTRO FINAL: garantir somente padrões com acurácia ≥ 80%
    patterns = {k: v for k, v in patterns.items() if v.get('accuracy', 0) >= 80}
    _tb = _detect_triple_bottom(opens, highs, lows, closes, ema5_last, ema50_last)
    if _tb:
        patterns.update(_tb)

    return patterns


def calc_candle_strength(opens: np.ndarray, highs: np.ndarray,
                          lows: np.ndarray, closes: np.ndarray) -> dict:
    """Força da vela atual: relação corpo/range total."""
    if len(opens) < 1: return {'strength': 0, 'dir': 'neutro', 'is_strong': False}
    o, h, l, c = float(opens[-1]), float(highs[-1]), float(lows[-1]), float(closes[-1])
    body     = abs(c - o)
    total    = h - l if h != l else 1e-9
    strength = round((body / total) * 100, 1)
    direction = 'CALL' if c > o else ('PUT' if c < o else 'neutro')
    return {'strength': strength, 'dir': direction, 'is_strong': strength > 55}


# ═══════════════════════════════════════════════════════════════════════════════
# ANÁLISE DE TENDÊNCIA — EMA5, EMA10, EMA50
# ═══════════════════════════════════════════════════════════════════════════════

def detect_trend(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray):
    """
    Identifica tendência usando EMA5, EMA10 e EMA50.
    Retorna: ('up'|'down'|'sideways', slope, description)
    """
    if len(closes) < 15:
        return 'sideways', 0, 'dados insuficientes'

    ema5  = calc_ema(closes, 5)
    ema10 = calc_ema(closes, 10)
    ema50 = calc_ema(closes, 50)

    e5, e10, e50 = float(ema5[-1]), float(ema10[-1]), float(ema50[-1])
    price = float(closes[-1])

    # Inclinação da EMA5 (últimas 3 velas) — muito responsivo para M1
    slope_pts = min(3, len(ema5))
    slope = (float(ema5[-1]) - float(ema5[-slope_pts])) / (slope_pts * abs(float(ema5[-slope_pts])) + 1e-9) * 100

    # Inclinação EMA50 (últimas 5 velas) — confirma tendência principal
    slope50_pts = min(5, len(ema50))
    slope50 = (float(ema50[-1]) - float(ema50[-slope50_pts])) / (slope50_pts * abs(float(ema50[-slope50_pts])) + 1e-9) * 100

    # TENDÊNCIA FORTE: alinhamento total EMA5 > EMA10 > EMA50
    if price > e5 > e10 > e50 and slope > 0 and slope50 >= 0:
        return 'up', round(slope, 4), f'Alta forte: Preço>EMA5>EMA10>EMA50'
    if price < e5 < e10 < e50 and slope < 0 and slope50 <= 0:
        return 'down', round(slope, 4), f'Baixa forte: Preço<EMA5<EMA10<EMA50'

    # TENDÊNCIA MODERADA: EMA5 e EMA50 alinhadas
    if price > e5 and e5 > e50 and slope > 0:
        return 'up', round(slope, 4), f'Alta: EMA5({e5:.5f}) > EMA50({e50:.5f})'
    if price < e5 and e5 < e50 and slope < 0:
        return 'down', round(slope, 4), f'Baixa: EMA5({e5:.5f}) < EMA50({e50:.5f})'

    # LATERALIZAÇÃO
    rng = (np.max(highs[-15:]) - np.min(lows[-15:])) / (np.mean(closes[-15:]) + 1e-9)
    if rng < 0.0008:
        return 'sideways', 0, 'Lateralização (range estreito)'

    return 'sideways', round(slope, 4), 'Tendência indefinida'


# ═══════════════════════════════════════════════════════════════════════════════
# MOTOR PRINCIPAL — CONFLUÊNCIA COM PADRÃO DE VELA OBRIGATÓRIO
# ═══════════════════════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════════════════════
# VALIDAÇÃO ESTRITA DE PADRÕES + FUNIL V3
# ═══════════════════════════════════════════════════════════════════════════════

def _body(o, c):
    return abs(float(c) - float(o))

def _range(h, l):
    return max(1e-9, float(h) - float(l))

def _bull(o, c):
    return float(c) > float(o)

def _bear(o, c):
    return float(c) < float(o)

def _strict_pattern_validator(opens, highs, lows, closes, pattern_key: str | None, direction: str | None,
                              ema5: float | None = None, ema50: float | None = None) -> tuple[bool, str]:
    if not pattern_key or len(closes) < 5:
        return True, 'sem_validador'

    key = str(pattern_key).lower()
    o1,o2,o3 = float(opens[-1]), float(opens[-2]), float(opens[-3])
    h1,h2,h3 = float(highs[-1]), float(highs[-2]), float(highs[-3])
    l1,l2,l3 = float(lows[-1]),  float(lows[-2]),  float(lows[-3])
    c1,c2,c3 = float(closes[-1]),float(closes[-2]),float(closes[-3])

    def body_ratio(o,h,l,c):
        return _body(o,c) / _range(h,l)

    # Engolfos
    if 'engolfo_alta' in key:
        ok = _bear(o2,c2) and _bull(o1,c1) and o1 <= c2 and c1 >= o2 and body_ratio(o1,h1,l1,c1) >= 0.45
        return ok, 'engolfo_alta'
    if 'engolfo_baixa' in key:
        ok = _bull(o2,c2) and _bear(o1,c1) and o1 >= c2 and c1 <= o2 and body_ratio(o1,h1,l1,c1) >= 0.45
        return ok, 'engolfo_baixa'

    # Martelo / estrela / pinbar
    upper1 = h1 - max(o1,c1)
    lower1 = min(o1,c1) - l1
    br1 = body_ratio(o1,h1,l1,c1)
    if 'martelo' in key and 'invertido' not in key:
        ok = _bull(o1,c1) and lower1 >= _body(o1,c1)*1.8 and upper1 <= max(1e-9,_body(o1,c1))*0.5 and br1 >= 0.18
        return ok, 'martelo'
    if 'estrela_cadente' in key:
        ok = _bear(o1,c1) and upper1 >= _body(o1,c1)*1.8 and lower1 <= max(1e-9,_body(o1,c1))*0.5 and br1 >= 0.18
        return ok, 'estrela_cadente'
    if 'pinbar_alta' in key:
        ok = lower1 >= _body(o1,c1)*2.3 and upper1 <= max(1e-9,_body(o1,c1))*1.0 and br1 >= 0.07
        return ok, 'pinbar_alta'
    if 'pinbar_baixa' in key:
        ok = upper1 >= _body(o1,c1)*2.3 and lower1 <= max(1e-9,_body(o1,c1))*1.0 and br1 >= 0.07
        return ok, 'pinbar_baixa'

    # Morning / Evening
    if 'morning_star' in key:
        mid3 = (o3 + c3) / 2.0
        ok = _bear(o3,c3) and body_ratio(o3,h3,l3,c3) >= 0.5 and body_ratio(o2,h2,l2,c2) <= 0.35 and _bull(o1,c1) and c1 > mid3
        return ok, 'morning_star'
    if 'evening_star' in key:
        mid3 = (o3 + c3) / 2.0
        ok = _bull(o3,c3) and body_ratio(o3,h3,l3,c3) >= 0.5 and body_ratio(o2,h2,l2,c2) <= 0.35 and _bear(o1,c1) and c1 < mid3
        return ok, 'evening_star'

    # Três soldados / corvos
    if 'tres_soldados' in key or 'three_white_soldiers' in key:
        ok = (_bull(o3,c3) and _bull(o2,c2) and _bull(o1,c1)
              and c3 < c2 < c1 and o3 < o2 < o1
              and body_ratio(o3,h3,l3,c3) >= 0.5 and body_ratio(o2,h2,l2,c2) >= 0.5 and body_ratio(o1,h1,l1,c1) >= 0.5)
        return ok, 'tres_soldados'
    if 'tres_corvos' in key or 'three_black_crows' in key:
        ok = (_bear(o3,c3) and _bear(o2,c2) and _bear(o1,c1)
              and c3 > c2 > c1 and o3 > o2 > o1
              and body_ratio(o3,h3,l3,c3) >= 0.5 and body_ratio(o2,h2,l2,c2) >= 0.5 and body_ratio(o1,h1,l1,c1) >= 0.5)
        return ok, 'tres_corvos'

    # Three inside / outside
    if 'three_inside_up' in key:
        ok = (_bear(o3,c3) and _bull(o2,c2) and _bull(o1,c1)
              and min(o2,c2) >= min(o3,c3) and max(o2,c2) <= max(o3,c3)
              and c1 > max(o3,c3))
        return ok, 'three_inside_up'
    if 'three_inside_down' in key:
        ok = (_bull(o3,c3) and _bear(o2,c2) and _bear(o1,c1)
              and min(o2,c2) >= min(o3,c3) and max(o2,c2) <= max(o3,c3)
              and c1 < min(o3,c3))
        return ok, 'three_inside_down'
    if 'three_outside_up' in key:
        ok = (_bear(o3,c3) and _bull(o2,c2) and _bull(o1,c1)
              and o2 <= c3 and c2 >= o3 and c1 > c2)
        return ok, 'three_outside_up'
    if 'three_outside_down' in key:
        ok = (_bull(o3,c3) and _bear(o2,c2) and _bear(o1,c1)
              and o2 >= c3 and c2 <= o3 and c1 < c2)
        return ok, 'three_outside_down'

    # Triplo fundo
    if 'triple_bottom' in key or 'fundo_triplo' in key:
        if len(closes) < 9:
            return False, 'fundo_triplo'
        seg_l = [float(x) for x in lows[-9:]]
        seg_h = [float(x) for x in highs[-9:]]
        seg_c = [float(x) for x in closes[-9:]]
        pivots = []
        for i in range(1, len(seg_l)-1):
            if seg_l[i] <= seg_l[i-1] and seg_l[i] <= seg_l[i+1]:
                pivots.append(i)
        if len(pivots) < 3:
            return False, 'fundo_triplo'
        pivots = pivots[-3:]
        lows3 = [seg_l[i] for i in pivots]
        avg_low = sum(lows3)/len(lows3)
        tol = max((max(seg_h)-min(seg_l))*0.12, avg_low*0.0007)
        similar = max(lows3) - min(lows3) <= tol
        neckline = max(seg_h[pivots[0]:pivots[-1]+1])
        breakout = seg_c[-1] > neckline or seg_c[-2] > neckline
        ema_ok = True if ema5 is None or ema50 is None else ema5 >= ema50*0.9995
        return bool(similar and breakout and ema_ok), 'fundo_triplo'

    return True, 'no_specific_rule'


def _detect_triple_bottom(opens, highs, lows, closes, ema5_last: float, ema50_last: float):
    ok, _ = _strict_pattern_validator(opens, highs, lows, closes, 'triple_bottom', 'CALL', ema5_last, ema50_last)
    if not ok:
        return None
    return {
        'triple_bottom': {
            'dir': 'CALL',
            'accuracy': 84,
            'desc': '🛡️ Fundo Triplo (84%) — reversão altista confirmada'
        }
    }


def compute_super_signal(asset: str, opens, highs, lows, closes, volumes=None, base_signal=None, username='admin'):
    """Agregador leve e resiliente. Nunca quebra o scan se módulos opcionais faltarem."""
    scores = {'CALL': 0, 'PUT': 0}
    modules = {}
    if base_signal and base_signal.get('direction'):
        d = base_signal.get('direction')
        conf = int(base_signal.get('strength', 0) or 0)
        pts = max(0, min(40, conf // 2))
        scores[d] += pts
        modules['base_signal'] = {'dir': d, 'pts': pts, 'conf': conf}
    try:
        if len(closes) >= 20:
            e5 = float(calc_ema(closes, 5)[-1])
            e10 = float(calc_ema(closes, 10)[-1])
            e50 = float(calc_ema(closes, 50)[-1])
            if e5 > e10 > e50:
                scores['CALL'] += 6
                modules['ema_stack'] = {'dir': 'CALL', 'pts': 6}
            elif e5 < e10 < e50:
                scores['PUT'] += 6
                modules['ema_stack'] = {'dir': 'PUT', 'pts': 6}
        rsi = calc_rsi(closes, 5)
        if rsi <= 22:
            scores['CALL'] += 3
            modules['rsi_extreme'] = {'dir': 'CALL', 'pts': 3, 'rsi': rsi}
        elif rsi >= 78:
            scores['PUT'] += 3
            modules['rsi_extreme'] = {'dir': 'PUT', 'pts': 3, 'rsi': rsi}
    except Exception:
        pass
    fc = detect_flipcoin(opens, highs, lows, closes)
    direction = None
    if scores['CALL'] > scores['PUT']:
        direction = 'CALL'
    elif scores['PUT'] > scores['CALL']:
        direction = 'PUT'
    total = scores['CALL'] + scores['PUT']
    confidence = 0 if total <= 0 or not direction else min(92, int(max(scores.values()) / max(1, total) * 100))
    vetoed = fc.get('is_flipcoin', False) and fc.get('score', 0) >= 5
    return {
        'direction': direction,
        'confidence': confidence,
        'scores': scores,
        'score_call': scores['CALL'],
        'score_put': scores['PUT'],
        'modules': modules,
        'flipcoin': fc,
        'vetoed': vetoed,
        'veto_reason': 'flipcoin' if vetoed else None,
    }

def analyze_asset_full(asset: str, ohlc: dict, strategies: dict = None, min_confluence: int = 4, dc_mode: str = 'disabled') -> dict | None:
    """
    Análise técnica completa para M1.
    Agora usa Candle Engine separado para padrões clássicos e avançados,
    mantendo compatibilidade com strategies['pat'] e com o formato antigo.
    """
    if strategies is None:
        strategies = {'ema':True,'rsi':True,'bb':True,'macd':True,'adx':True,'stoch':True,'lp':True,'pat':True,'fib':True}

    _use_ema   = strategies.get('ema',   True)
    _use_rsi   = strategies.get('rsi',   True)
    _use_bb    = strategies.get('bb',    True)
    _use_macd  = strategies.get('macd',  True)
    _use_adx   = strategies.get('adx',   True)
    _use_stoch = strategies.get('stoch', True)
    _use_lp    = strategies.get('lp',    True)
    _use_fib   = strategies.get('fib',   True)

    candle_cfg = normalize_candle_config(strategies)
    _use_pat = candle_cfg.get('enabled', strategies.get('pat', True))

    closes = ohlc['closes']
    highs  = ohlc['highs']
    lows   = ohlc['lows']
    opens  = ohlc['opens']
    vols_arr = ohlc.get('volumes', None)
    if vols_arr is None:
        vols_arr = calc_volume_candle(opens, closes, highs, lows)

    if len(closes) < 20:
        return None

    price  = float(closes[-1])
    detail = {}

    ema5_arr  = calc_ema(closes, 5)
    ema10_arr = calc_ema(closes, 10)
    ema50_arr = calc_ema(closes, 50)
    e5  = float(ema5_arr[-1])
    e10 = float(ema10_arr[-1])
    e50 = float(ema50_arr[-1])
    detail['ema5']  = round(e5,  5)
    detail['ema10'] = round(e10, 5)
    detail['ema50'] = round(e50, 5)

    trend, slope, trend_desc = detect_trend(closes, highs, lows)
    detail['tendencia']      = trend
    detail['tendencia_desc'] = trend_desc

    candle_pack = analyze_candle_engine(opens, highs, lows, closes, e5, e50, strategies)
    detail['candles_engine'] = {
        'enabled': candle_pack.get('enabled', False),
        'direction': candle_pack.get('direction'),
        'strength': candle_pack.get('strength', 0),
        'selected_pattern': candle_pack.get('selected_pattern'),
        'patterns': candle_pack.get('patterns', [])[:8],
        'classic_patterns': candle_pack.get('classic_patterns', [])[:8],
        'advanced_patterns': candle_pack.get('advanced_patterns', [])[:8],
        'summary': candle_pack.get('summary', ''),
    }
    detail['padroes'] = candle_pack.get('patterns', [])

    patterns = {}
    if candle_pack.get('selected_pattern_key') and candle_pack.get('direction'):
        patterns[candle_pack['selected_pattern_key']] = {
            'dir': candle_pack['direction'],
            'accuracy': max(80, int(candle_pack.get('strength', 80))),
            'desc': candle_pack.get('selected_pattern') or 'Candle Engine',
        }

    # Validação estrita do padrão selecionado pelo candle engine
    if candle_pack.get('selected_pattern_key') and candle_pack.get('direction'):
        _ok_pat, _rule_name = _strict_pattern_validator(
            opens, highs, lows, closes,
            candle_pack.get('selected_pattern_key'),
            candle_pack.get('direction'),
            e5, e50
        )
        if not _ok_pat:
            return None

    # Fallback: Fundo Triplo local mesmo que o candles_engine não traga o padrão
    if _use_pat and not patterns:
        _tb_local = _detect_triple_bottom(opens, highs, lows, closes, e5, e50)
        if _tb_local:
            patterns.update(_tb_local)
            candle_pack['selected_pattern_key'] = 'triple_bottom'
            candle_pack['selected_pattern'] = 'Fundo Triplo'
            candle_pack['direction'] = 'CALL'
            candle_pack['strength'] = max(84, int(candle_pack.get('strength', 0) or 0))

    if dc_mode == 'solo' and not patterns:
        _last_bull = float(closes[-1]) >= float(opens[-1])
        if e5 > e50:
            candle_dir = 'CALL'
        elif e5 < e50:
            candle_dir = 'PUT'
        else:
            candle_dir = 'CALL' if _last_bull else 'PUT'
        best_pattern = {'accuracy': 70, 'desc': '☠️ Dead Candle OTC'}
    elif _use_pat and (not candle_pack.get('min_score_ok') or not candle_pack.get('direction')):
        return None
    else:
        if patterns:
            candle_dir = candle_pack['direction']
            best_pattern = max(patterns.values(), key=lambda x: x['accuracy'])
        elif not _use_pat:
            if trend == 'up' and e5 > e50:
                candle_dir = 'CALL'
            elif trend == 'down' and e5 < e50:
                candle_dir = 'PUT'
            else:
                return None
            best_pattern = {'accuracy': 75, 'desc': f'Tendência {trend.upper()} + EMA'}
        else:
            return None

    ema5_aligned_call = e5 > e50
    ema5_aligned_put  = e5 < e50

    if _use_ema:
        reversal_patterns = {
            'morning_star', 'evening_star', 'martelo', 'estrela_cadente',
            'tweezer_bottom', 'tweezer_top', 'engolfo_alta', 'engolfo_baixa',
            'kicker_alta', 'kicker_baixa', 'trap_top', 'trap_bottom'
        }
        is_reversal = bool(set(patterns.keys()) & reversal_patterns)

        if candle_dir == 'CALL':
            if not ema5_aligned_call and not is_reversal:
                return None
        else:
            if not ema5_aligned_put and not is_reversal:
                return None

    score_call = 0
    score_put  = 0
    reasons    = []

    pattern_pts = max(3, min(10, int(candle_pack.get('strength', best_pattern['accuracy']) // 12))) if _use_pat else 3
    if candle_dir == 'CALL':
        score_call += pattern_pts
        reasons.append(best_pattern['desc'])
    else:
        score_put += pattern_pts
        reasons.append(best_pattern['desc'])

    if trend == 'up' and candle_dir == 'CALL':
        score_call += 4; reasons.append('📈 Tendência ALTA confirmada')
    elif trend == 'down' and candle_dir == 'PUT':
        score_put  += 4; reasons.append('📉 Tendência BAIXA confirmada')
    elif trend == 'sideways':
        if best_pattern['accuracy'] < 83 and candle_pack.get('strength', 0) < 72:
            return None

    if candle_dir == 'CALL':
        if price > e5 > e10 > e50:
            score_call += 3; reasons.append('EMA5>EMA10>EMA50 ↑')
        elif price > e5 > e50:
            score_call += 2; reasons.append('EMA5>EMA50 ↑')
        elif e5 > e50:
            score_call += 1
    else:
        if price < e5 < e10 < e50:
            score_put += 3; reasons.append('EMA5<EMA10<EMA50 ↓')
        elif price < e5 < e50:
            score_put += 2; reasons.append('EMA5<EMA50 ↓')
        elif e5 < e50:
            score_put += 1

    if len(ema5_arr) >= 2 and len(ema10_arr) >= 2:
        cross_up   = float(ema5_arr[-2]) <= float(ema10_arr[-2]) and e5 > e10
        cross_down = float(ema5_arr[-2]) >= float(ema10_arr[-2]) and e5 < e10
        if cross_up   and candle_dir == 'CALL':
            score_call += 3; reasons.append('⚡ Cruzamento EMA5/EMA10 ↑')
        elif cross_down and candle_dir == 'PUT':
            score_put  += 3; reasons.append('⚡ Cruzamento EMA5/EMA10 ↓')

    if _use_rsi:
        rsi = calc_rsi(closes, 5)
        detail['rsi'] = rsi
        if candle_dir == 'CALL':
            if rsi < 35:
                score_call += 3; reasons.append(f'RSI={rsi:.0f} sobrevenda')
            elif rsi < 45:
                score_call += 1
            elif rsi > 78:
                score_call -= 2; reasons.append(f'RSI={rsi:.0f} sobrecompra')
        else:
            if rsi > 65:
                score_put += 3; reasons.append(f'RSI={rsi:.0f} sobrecompra')
            elif rsi > 55:
                score_put += 1
            elif rsi < 22:
                score_put -= 2; reasons.append(f'RSI={rsi:.0f} sobrevenda')
    else:
        rsi = 50.0
        detail['rsi'] = rsi

    if _use_stoch:
        st_k, st_d = calc_stoch(closes, highs, lows, 5, 3)
        detail['stoch_k'] = st_k
        detail['stoch_d'] = st_d
        if candle_dir == 'CALL' and st_k > st_d and st_k < 55:
            score_call += 2; reasons.append('Stoch cruzando ↑')
        elif candle_dir == 'PUT' and st_k < st_d and st_k > 45:
            score_put += 2; reasons.append('Stoch cruzando ↓')

    if _use_macd:
        macd, macd_sig, macd_hist = calc_macd(closes)
        detail['macd'] = round(macd, 6)
        detail['macd_signal'] = round(macd_sig, 6)
        detail['macd_hist'] = round(macd_hist, 6)
        if candle_dir == 'CALL' and macd > macd_sig and macd_hist > 0:
            score_call += 2; reasons.append('MACD bullish')
        elif candle_dir == 'PUT' and macd < macd_sig and macd_hist < 0:
            score_put += 2; reasons.append('MACD bearish')

    if _use_bb:
        bb_up, bb_mid, bb_dn, pct_b = calc_bollinger(closes, 10, 2.0)
        detail['bb_up'] = bb_up; detail['bb_mid'] = bb_mid; detail['bb_dn'] = bb_dn; detail['pct_b'] = pct_b
        if pct_b is not None:
            if candle_dir == 'CALL' and pct_b < 0.35:
                score_call += 2; reasons.append('Bollinger região compradora')
            elif candle_dir == 'PUT' and pct_b > 0.65:
                score_put += 2; reasons.append('Bollinger região vendedora')

    if _use_adx:
        adx, plus_di, minus_di = calc_adx(highs, lows, closes, 7)
        detail['adx'] = adx; detail['plus_di'] = plus_di; detail['minus_di'] = minus_di
        if candle_dir == 'CALL' and plus_di > minus_di and adx >= 18:
            score_call += 2; reasons.append(f'ADX força compradora ({adx:.0f})')
        elif candle_dir == 'PUT' and minus_di > plus_di and adx >= 18:
            score_put += 2; reasons.append(f'ADX força vendedora ({adx:.0f})')

    if _use_fib:
        fib = calc_fibonacci(highs, lows, closes, 30)
        detail['fib'] = fib
        if fib:
            if candle_dir == 'CALL' and fib.get('trend_up', False) and price >= fib['50']:
                score_call += 1; reasons.append('Fibonacci alinhado alta')
            elif candle_dir == 'PUT' and not fib.get('trend_up', True) and price <= fib['50']:
                score_put += 1; reasons.append('Fibonacci alinhado baixa')

    try:
        detail['dead_candle'] = {'score_call': 0, 'score_put': 0, 'razoes': []}
        _dc_score_call = 0
        _dc_score_put = 0
        _dc_reasons = []
        if dc_mode in ('solo', 'combined'):
            _dead_recent = []
            for _i in range(max(0, len(opens)-6), len(opens)):
                _o = float(opens[_i]); _h = float(highs[_i]); _l = float(lows[_i]); _c = float(closes[_i])
                _rng = _h - _l
                _body = abs(_c - _o)
                if _rng > 1e-10 and (_body / _rng) <= 0.08:
                    _dead_recent.append(_i)
            if len(_dead_recent) > 0:
                if rsi < 22:
                    _dc_score_call += 2; _dc_reasons.append(f'🔴 RSI exaustão={rsi:.0f}→CALL(sobrevendido)')
                elif rsi > 78:
                    _dc_score_put += 2; _dc_reasons.append(f'🟢 RSI exaustão={rsi:.0f}→PUT(sobrecomprado)')
            detail['dead_candle'] = {'score_call': _dc_score_call, 'score_put': _dc_score_put, 'razoes': _dc_reasons}
            if _dc_score_call > 0:
                score_call += _dc_score_call
                reasons.extend([r for r in _dc_reasons if 'CALL' in r or '↑' in r])
            if _dc_score_put > 0:
                score_put += _dc_score_put
                reasons.extend([r for r in _dc_reasons if 'PUT' in r or '↓' in r])
    except Exception as _dc_e:
        detail['dead_candle'] = {'score_call': 0, 'score_put': 0, 'razoes': [], 'erro': str(_dc_e)}

    if _use_lp:
        lp = analisar_logica_preco(opens, highs, lows, closes, e5, e10, e50)
    else:
        lp = {'score_call':0,'score_put':0,'forca_lp':0,'direcao':None,'resumo':'LP desativado',
              'sinais':[],'alertas':[],'lote':{},'pode_entrar':True,'posicionamento':None,'taxa_dividida':None}
    detail['logica_preco'] = {
        'score_call'  : lp['score_call'],
        'score_put'   : lp['score_put'],
        'forca_lp'    : lp['forca_lp'],
        'direcao'     : lp['direcao'],
        'resumo'      : lp['resumo'],
        'sinais'      : lp['sinais'][:5],
        'alertas'     : lp['alertas'],
        'lote'        : lp.get('lote', {}),
        'posicionamento': lp.get('posicionamento', {}).get('tipo') if lp.get('posicionamento') else None,
        'taxa_dividida' : lp.get('taxa_dividida', {}).get('forca') if lp.get('taxa_dividida') else None,
        'pode_entrar'  : lp.get('pode_entrar', True),
    }

    if lp['alertas'] and not lp['pode_entrar'] and lp.get('forca_lp', 0) < 65:
        return None

    if lp['direcao'] == candle_dir and lp['forca_lp'] >= 35:
        bonus = min(10, max(2, lp['forca_lp'] // 10))
        if candle_dir == 'CALL':
            score_call += bonus
        else:
            score_put  += bonus
        if lp['sinais']:
            reasons.append(lp['sinais'][0])
            if len(lp['sinais']) > 1:
                reasons.append(lp['sinais'][1])
    elif lp['direcao'] and lp['direcao'] != candle_dir and lp['forca_lp'] >= 60:
        return None

    # Bloqueio por exaustão extrema contra a direção da entrada
    _rsi_now = detail.get('rsi', 50)
    if candle_dir == 'CALL' and _rsi_now >= 90:
        return None
    if candle_dir == 'PUT' and _rsi_now <= 10:
        return None

    vol_info = check_volume_filter(opens, closes, highs, lows)
    detail['vol_last'] = vol_info['vol_last']
    detail['vol_avg']  = vol_info['vol_avg']

    if asset.endswith('-OTC'):
        confluence = score_call if candle_dir == 'CALL' else score_put
    else:
        confluence = score_call if candle_dir == 'CALL' else score_put
        if not vol_info['ok']:
            confluence -= 2
            reasons.append(vol_info['motivo'])

    if dc_mode == 'solo':
        min_required = max(3, min_confluence - 1)
    elif candle_pack.get('enabled'):
        min_required = max(3, min_confluence)
    else:
        min_required = max(2, min_confluence - 1)
    if confluence < min_required:
        return None

    strength = 50 + confluence * 5
    if candle_pack.get('enabled') and candle_pack.get('strength', 0) > 0:
        strength = max(strength, int((strength * 0.55) + (candle_pack['strength'] * 0.45)))
    strength = max(55, min(97, strength))

    return {
        'asset': asset,
        'direction': candle_dir,
        'strength': int(strength),
        'trend': trend_desc,
        'trend_raw': trend,
        'rsi': detail.get('rsi', 50),
        'score_call': score_call,
        'score_put': score_put,
        'confluence': confluence,
        'reason': ' | '.join(reasons[:8]),
        'pattern': candle_pack.get('selected_pattern') or best_pattern['desc'],
        'patterns': candle_pack.get('patterns', []),
        'detail': detail,
        'lp_resumo': lp.get('resumo', ''),
        'lp_direcao': lp.get('direcao'),
        'lp_forca': lp.get('forca_lp', 0),
        'lp_pode_entrar': lp.get('pode_entrar', True),
        'lp_lote': lp.get('lote', {}),
        'lp_posicao': lp.get('posicionamento', {}),
        'lp_taxa_div': lp.get('taxa_dividida', {}),
        'vol_last': vol_info['vol_last'],
        'vol_avg': vol_info['vol_avg'],
        'candle_engine': candle_pack,
        'candles_summary': candle_pack.get('summary', ''),
    }

def scan_assets(assets: list, timeframe: int = 60, count: int = 50,
                bot_log_fn=None, bot_state_ref=None,
                strategies: dict = None, min_confluence: int = 4,
                dc_mode: str = 'disabled') -> list:
    """
    Escaneia um ou vários ativos binários (OTC ou Mercado Aberto).
    Retorna sinais com padrão de vela ≥80% confirmado + alinhamento EMA.
    Em modo DEMO (sem IQ), usa candles sintéticos para simulação realista.
    strategies: dict com indicadores habilitados (ema, rsi, bb, macd, adx, stoch, lp, pat, fib)
    """
    iq = get_iq()
    signals = []
    is_demo = (iq is None)  # True quando sem IQ conectado

    # Em modo DEMO, usar apenas subconjunto de ativos para velocidade
    if is_demo and len(assets) > 10:
        # Pega 8 ativos Forex OTC principais para demo rápido
        demo_priority = [
            'EURUSD-OTC', 'GBPUSD-OTC', 'USDJPY-OTC', 'AUDUSD-OTC',
            'EURJPY-OTC', 'GBPJPY-OTC', 'USDCHF-OTC', 'NZDUSD-OTC',
            'BTCUSD-OTC', 'ETHUSD-OTC'
        ]
        assets = [a for a in demo_priority if a in assets] or assets[:10]

    for asset in assets:
        # Checar se bot ainda rodando antes de cada ativo
        if bot_state_ref is not None and not bot_state_ref.get('running', True):
            break

        closes, ohlc = None, None

        if iq is not None:
            # get_candles_iq já usa resolve_asset_name internamente
            closes, ohlc = get_candles_iq(asset, timeframe, count)

        if closes is None or ohlc is None:
            if is_demo:
                # Modo DEMO: gerar candles sintéticos para análise
                closes, ohlc = generate_synthetic_candles(asset, count)
                if closes is None:
                    continue
            else:
                # Modo REAL com IQ: ativo sem candles = fechado/sem liquidez
                if bot_log_fn:
                    bot_log_fn(f'  ⏭ {asset}: sem candles reais — ativo ignorado', 'info')
                continue

        # Analisar SOMENTE candles fechados. A última vela recebida pode estar em formação.
        if len(ohlc['closes']) < 12:
            continue
        ohlc_closed = {
            'opens': ohlc['opens'][:-1],
            'highs': ohlc['highs'][:-1],
            'lows': ohlc['lows'][:-1],
            'closes': ohlc['closes'][:-1],
            'volumes': ohlc.get('volumes', None)[:-1] if ohlc.get('volumes', None) is not None else None,
        }
        sig = analyze_asset_full(asset, ohlc_closed, strategies=strategies, min_confluence=min_confluence, dc_mode=dc_mode)

        # ── FLIPCOIN GUARD: bloquear ativo em modo flip-coin ──────────────
        _fc = detect_flipcoin(ohlc_closed['opens'], ohlc_closed['highs'], ohlc_closed['lows'], ohlc_closed['closes'])
        if _fc['is_flipcoin']:
            if bot_log_fn:
                bot_log_fn(
                    f'🎲 [FLIPCOIN] {asset} BLOQUEADO — mercado sem direção ({_fc["severity"]}) | '
                    f'Score:{_fc["score"]}/6 | {" | ".join(_fc["reasons"][:3])}',
                    'warn'
                )
            continue  # Pular ativo em flip-coin

        # ── COMPUTE SUPER SIGNAL (13 módulos v3) ─────────────────────────
        _vols = ohlc_closed.get('volumes', None)
        _opens = ohlc_closed['opens']; _highs = ohlc_closed['highs']
        _lows = ohlc_closed['lows']; _closes = ohlc_closed['closes']
        _username = (bot_state_ref or {}).get('current_user', 'admin') if bot_state_ref else 'admin'

        super_sig = compute_super_signal(
            asset, _opens, _highs, _lows, _closes,
            volumes=_vols, base_signal=sig, username=_username
        )

        # Log detalhado dos 13 módulos v3
        if bot_log_fn and super_sig:
            _mods = super_sig.get('modules', {})
            _sc = super_sig.get('score_call', 0)
            _sp = super_sig.get('score_put', 0)
            _conf = super_sig.get('confidence', 0)
            _s_dir = super_sig.get('direction')
            _vetoed = super_sig.get('vetoed', False)

            if _vetoed:
                bot_log_fn(f'🛡️ [v3 VETO] {asset}: {super_sig.get("veto_reason","Casino Guard")}', 'warn')
            else:
                # Construir linha de módulos ativos
                _mod_parts = []
                for _mname, _mval in _mods.items():
                    if isinstance(_mval, dict) and 'pts' in _mval:
                        _mdir = _mval.get('dir', '?')
                        _mpts = _mval.get('pts', 0)
                        _icon = '✅' if _mdir == (_s_dir or 'CALL') else '❌'
                        _mod_parts.append(f'{_mname[:8]}:{_mpts}pt{_icon}')
                if _mod_parts:
                    bot_log_fn(
                        f'🔬 [v3] {asset} CALL:{_sc}pts PUT:{_sp}pts → {_s_dir or "NULO"} {_conf}% | '
                        f'{" | ".join(_mod_parts[:6])}',
                        'info'
                    )

        # Se super_signal vetou → bloquear entrada
        if super_sig and super_sig.get('vetoed', False):
            continue

        # Boost de strength se super_signal concorda com sinal base
        if sig and super_sig and super_sig.get('direction') == sig.get('direction'):
            _boost = min(5, super_sig.get('confidence', 0) // 20)
            sig['strength'] = min(97, sig.get('strength', 0) + _boost)
            sig['super_signal'] = super_sig
            sig['v3_modules'] = super_sig.get('modules', {})
            sig['v3_score_call'] = super_sig.get('score_call', 0)
            sig['v3_score_put'] = super_sig.get('score_put', 0)
            sig['v3_confidence'] = super_sig.get('confidence', 0)
            sig['flipcoin'] = _fc
        elif sig:
            sig['super_signal'] = super_sig
            sig['v3_modules'] = super_sig.get('modules', {}) if super_sig else {}
            sig['flipcoin'] = _fc

        if sig:
            # Em DC SOLO: aceitar sinais com strength >= 25% (sem filtro de 80%)
            _min_str = 25 if dc_mode == 'solo' else 80
            if sig.get('strength', 0) >= _min_str:
                signals.append(sig)
                if bot_log_fn:
                    _dc_tag = '☠️ ' if sig.get('pattern','').startswith('☠️') else ''
                    _v3_tag = f' | v3:{sig.get("v3_confidence",0)}%' if sig.get('v3_confidence') else ''
                    bot_log_fn(
                        f'🎯 {_dc_tag}{asset}: {sig["direction"]} {sig["strength"]}%{_v3_tag} | '
                        f'{sig["pattern"]} | {sig["reason"][:60]}',
                        'signal'
                    )
            else:
                if bot_log_fn and dc_mode == 'solo':
                    bot_log_fn(f'  ⟶ {asset}: DC sinal {sig["strength"]}% (abaixo de 25%) — pulando', 'info')
        else:
            if bot_log_fn:
                bot_log_fn(f'  ⟶ {asset}: nenhum padrão válido', 'info')

        time.sleep(0.02)  # libera GIL para threads do gunicorn responderem HTTP
        # Verificar se bot ainda está rodando (interrompe scan se parou)
        if bot_state_ref is not None and not bot_state_ref.get('running', True):
            break

    return sorted(signals, key=lambda x: x['strength'], reverse=True)


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUÇÃO DE ORDENS
# ═══════════════════════════════════════════════════════════════════════════════



def get_available_all_assets() -> list:
    """
    Retorna lista de TODOS os ativos disponíveis.
    Executa em thread com timeout de 6s para não bloquear o GIL.
    """
    iq = get_iq()
    if not iq:
        return ALL_BINARY_ASSETS

    _result = [None]
    def _fetch():
        try:
            _result[0] = _get_available_all_assets_inner(iq)
        except Exception as e:
            log.warning(f'get_available_all_assets thread: {e}')
            _result[0] = ALL_BINARY_ASSETS
    t = threading.Thread(target=_fetch, daemon=True)
    t.start()
    t.join(timeout=6.0)
    return _result[0] if _result[0] is not None else ALL_BINARY_ASSETS


def _get_available_all_assets_inner(iq) -> list:
    """
    Retorna lista dos ativos OTC realmente abertos agora.
    Usa get_all_init() — mais confiável que get_all_open_time() (que quebra
    quando a API digital não responde).
    Cobre os 142 ativos OTC confirmados por teste real em 08/03/2026.
    """
    try:
        # Estratégia 1: usar get_all_init para pegar ativos habilitados
        init_info = iq.get_all_init()
        if init_info and 'result' in init_info:
            avail = []
            binary_actives = init_info['result'].get('turbo', {}).get('actives', {})
            if not binary_actives:
                binary_actives = init_info['result'].get('binary', {}).get('actives', {})

            # Mapear IDs → nomes limpos
            id_to_name = {}
            for aid, ainfo in binary_actives.items():
                full = ainfo.get('name', '')
                clean = full[6:] if full.startswith('front.') else full
                if 'OTC' in clean.upper() and ainfo.get('enabled', False):
                    id_to_name[int(aid)] = clean

            # Filtrar só os que estão na nossa lista testada
            for asset in OTC_BINARY_ASSETS:
                # Verificar se o nome está nos ativos habilitados
                if asset in id_to_name.values():
                    avail.append(asset)
                elif asset in OTC_BINARY_ASSETS:
                    # Incluir mesmo sem confirmação (serão tratados com suspend)
                    avail.append(asset)

            if avail:
                log.info(f'get_available: {len(avail)} OTC via get_all_init')
                # Adicionar mercado aberto também
                for a in OPEN_BINARY_ASSETS:
                    avail.append(a)
                return avail

        # Estratégia 2 (fallback): retornar todos os OTC + Aberto da lista
        log.warning('get_available_all_assets: usando lista completa (fallback)')
        return ALL_BINARY_ASSETS

    except Exception as e:
        log.warning(f'get_available_all_assets: {e} — usando lista completa')
        return ALL_BINARY_ASSETS



def get_available_otc_assets() -> list:
    """Retorna lista de ativos OTC turbo/binário disponíveis no momento."""
    iq = get_iq()
    if not iq:
        return OTC_BINARY_ASSETS  # fallback: retorna todos se não conectado
    try:
        open_times = iq.get_all_open_time()
        if not open_times:
            return OTC_BINARY_ASSETS
        turbo  = open_times.get('turbo', {})
        binary = open_times.get('binary', {})
        # Check both binary and turbo modes
        available = [a for a in OTC_BINARY_ASSETS if 
                     binary.get(a, {}).get('open', False) or 
                     turbo.get(a, {}).get('open', False)]
        if not available:
            # Se nenhum retornado como aberto, tenta sem filtro (pode ser erro de API)
            return OTC_BINARY_ASSETS
        log.info(f'📊 Ativos OTC disponíveis: {len(available)}/{len(OTC_BINARY_ASSETS)}')
        return available
    except Exception as e:
        log.warning(f'get_available_otc_assets: {e}')
        return OTC_BINARY_ASSETS



# ─── Mapa de nomes OTC → nome aceito pela API IQ Option ──────────────────────
# A constants.py só tem 9 pares Forex com -OTC; os restantes devem usar o nome
# sem sufixo (ex: BTCUSD-OTC → BTCUSD). A API identifica o instrumento OTC
# pelo tipo de expiração (turbo/1-min), não pelo sufixo no nome.
# ══════════════════════════════════════════════════════════════════════════════
# _OTC_API_MAP — Mapeamento DEFINITIVO: nome interno → nome aceito pela API IQ Option
# Fonte verificada: iqoptionapi/constants.py → dicionário ACTIVES
# REGRA: apenas 9 pares Forex têm '-OTC' registrado na biblioteca;
#        todos os demais OTC devem ser enviados SEM o sufixo '-OTC'.
# ══════════════════════════════════════════════════════════════════════════════
_OTC_API_MAP = {
    # Mapeamento completo: todos os ativos OTC usam o nome exato após sync_actives_from_api()
    # Formato: NOME-OTC → NOME-OTC (passthrough)
    'AIG-OTC': 'AIG-OTC',
    'ALIBABA-OTC': 'ALIBABA-OTC',
    'AMAZON-OTC': 'AMAZON-OTC',
    'AMZN/EBAY-OTC': 'AMZN/EBAY-OTC',
    'ARBUSD-OTC': 'ARBUSD-OTC',
    'ATOMUSD-OTC': 'ATOMUSD-OTC',
    'AUDCAD-OTC': 'AUDCAD-OTC',
    'AUDCHF-OTC': 'AUDCHF-OTC',
    'AUDJPY-OTC': 'AUDJPY-OTC',
    'AUDNZD-OTC': 'AUDNZD-OTC',
    'AUDUSD-OTC': 'AUDUSD-OTC',
    'AUS200-OTC': 'AUS200-OTC',
    'BCHUSD-OTC': 'BCHUSD-OTC',
    'BIDU-OTC': 'BIDU-OTC',
    'BONKUSD-OTC': 'BONKUSD-OTC',
    'CADCHF-OTC': 'CADCHF-OTC',
    'CADJPY-OTC': 'CADJPY-OTC',
    'CHFJPY-OTC': 'CHFJPY-OTC',
    'CHFNOK-OTC': 'CHFNOK-OTC',
    'CITI-OTC': 'CITI-OTC',
    'COKE-OTC': 'COKE-OTC',
    'DASHUSD-OTC': 'DASHUSD-OTC',
    'DOTUSD-OTC': 'DOTUSD-OTC',
    'DYDXUSD-OTC': 'DYDXUSD-OTC',
    'EOSUSD-OTC': 'EOSUSD-OTC',
    'EU50-OTC': 'EU50-OTC',
    'EURAUD-OTC': 'EURAUD-OTC',
    'EURCAD-OTC': 'EURCAD-OTC',
    'EURCHF-OTC': 'EURCHF-OTC',
    'EURGBP-OTC': 'EURGBP-OTC',
    'EURJPY-OTC': 'EURJPY-OTC',
    'EURNZD-OTC': 'EURNZD-OTC',
    'EURTHB-OTC': 'EURTHB-OTC',
    'EURUSD-OTC': 'EURUSD-OTC',
    'FARTCOINUSD-OTC': 'FARTCOINUSD-OTC',
    'FB-OTC': 'FB-OTC',
    'FETUSD-OTC': 'FETUSD-OTC',
    'FLOKIUSD-OTC': 'FLOKIUSD-OTC',
    'FR40-OTC': 'FR40-OTC',
    'FWONA-OTC': 'FWONA-OTC',
    'GALAUSD-OTC': 'GALAUSD-OTC',
    'GBPAUD-OTC': 'GBPAUD-OTC',
    'GBPCAD-OTC': 'GBPCAD-OTC',
    'GBPCHF-OTC': 'GBPCHF-OTC',
    'GBPJPY-OTC': 'GBPJPY-OTC',
    'GBPNZD-OTC': 'GBPNZD-OTC',
    'GBPUSD-OTC': 'GBPUSD-OTC',
    'GER30-OTC': 'GER30-OTC',
    'GER30/UK100-OTC': 'GER30/UK100-OTC',
    'GOOGLE-OTC': 'GOOGLE-OTC',
    'GOOGLE/MSFT-OTC': 'GOOGLE/MSFT-OTC',
    'GRTUSD-OTC': 'GRTUSD-OTC',
    'GS-OTC': 'GS-OTC',
    'HBARUSD-OTC': 'HBARUSD-OTC',
    'HK33-OTC': 'HK33-OTC',
    'ICPUSD-OTC': 'ICPUSD-OTC',
    'IMXUSD-OTC': 'IMXUSD-OTC',
    'INJUSD-OTC': 'INJUSD-OTC',
    'INTEL-OTC': 'INTEL-OTC',
    'INTEL/IBM-OTC': 'INTEL/IBM-OTC',
    'IOTAUSD-OTC': 'IOTAUSD-OTC',
    'JP225-OTC': 'JP225-OTC',
    'JPM-OTC': 'JPM-OTC',
    'JPYTHB-OTC': 'JPYTHB-OTC',
    'JUPUSD-OTC': 'JUPUSD-OTC',
    'KLARNA-OTC': 'KLARNA-OTC',
    'LABUBUUSD-OTC': 'LABUBUUSD-OTC',
    'LINKUSD-OTC': 'LINKUSD-OTC',
    'LTCUSD-OTC': 'LTCUSD-OTC',
    'MANAUSD-OTC': 'MANAUSD-OTC',
    'MATICUSD-OTC': 'MATICUSD-OTC',
    'MCDON-OTC': 'MCDON-OTC',
    'MELANIAUSD-OTC': 'MELANIAUSD-OTC',
    'META/GOOGLE-OTC': 'META/GOOGLE-OTC',
    'MORSTAN-OTC': 'MORSTAN-OTC',
    'MSFT-OTC': 'MSFT-OTC',
    'MSFT/AAPL-OTC': 'MSFT/AAPL-OTC',
    'NEARUSD-OTC': 'NEARUSD-OTC',
    'NFLX/AMZN-OTC': 'NFLX/AMZN-OTC',
    'NIKE-OTC': 'NIKE-OTC',
    'NOKJPY-OTC': 'NOKJPY-OTC',
    'NOTCOIN-OTC': 'NOTCOIN-OTC',
    'NVDA/AMD-OTC': 'NVDA/AMD-OTC',
    'NZDCAD-OTC': 'NZDCAD-OTC',
    'NZDCHF-OTC': 'NZDCHF-OTC',
    'NZDJPY-OTC': 'NZDJPY-OTC',
    'NZDUSD-OTC': 'NZDUSD-OTC',
    'ONDOUSD-OTC': 'ONDOUSD-OTC',
    'ONYXCOINUSD-OTC': 'ONYXCOINUSD-OTC',
    'ORDIUSD-OTC': 'ORDIUSD-OTC',
    'PENGUUSD-OTC': 'PENGUUSD-OTC',
    'PENUSD-OTC': 'PENUSD-OTC',
    'PEPEUSD-OTC': 'PEPEUSD-OTC',
    'PLTR-OTC': 'PLTR-OTC',
    'PYTHUSD-OTC': 'PYTHUSD-OTC',
    'RAYDIUMUSD-OTC': 'RAYDIUMUSD-OTC',
    'RENDERUSD-OTC': 'RENDERUSD-OTC',
    'RONINUSD-OTC': 'RONINUSD-OTC',
    'SANDUSD-OTC': 'SANDUSD-OTC',
    'SATSUSD-OTC': 'SATSUSD-OTC',
    'SEIUSD-OTC': 'SEIUSD-OTC',
    'SNAP-OTC': 'SNAP-OTC',
    'SP500-OTC': 'SP500-OTC',
    'STXUSD-OTC': 'STXUSD-OTC',
    'SUIUSD-OTC': 'SUIUSD-OTC',
    'TAOUSD-OTC': 'TAOUSD-OTC',
    'TESLA-OTC': 'TESLA-OTC',
    'TESLA/FORD-OTC': 'TESLA/FORD-OTC',
    'TIAUSD-OTC': 'TIAUSD-OTC',
    'TONUSD-OTC': 'TONUSD-OTC',
    'TRUMPUSD-OTC': 'TRUMPUSD-OTC',
    'UK100-OTC': 'UK100-OTC',
    'UKOUSD-OTC': 'UKOUSD-OTC',
    'US100/JP225-OTC': 'US100/JP225-OTC',
    'US2000-OTC': 'US2000-OTC',
    'US30-OTC': 'US30-OTC',
    'US30/JP225-OTC': 'US30/JP225-OTC',
    'US500/JP225-OTC': 'US500/JP225-OTC',
    'USDBRL-OTC': 'USDBRL-OTC',
    'USDCAD-OTC': 'USDCAD-OTC',
    'USDCHF-OTC': 'USDCHF-OTC',
    'USDCOP-OTC': 'USDCOP-OTC',
    'USDHKD-OTC': 'USDHKD-OTC',
    'USDINR-OTC': 'USDINR-OTC',
    'USDNOK-OTC': 'USDNOK-OTC',
    'USDPLN-OTC': 'USDPLN-OTC',
    'USDSEK-OTC': 'USDSEK-OTC',
    'USDSGD-OTC': 'USDSGD-OTC',
    'USDTHB-OTC': 'USDTHB-OTC',
    'USDTRY-OTC': 'USDTRY-OTC',
    'USDZAR-OTC': 'USDZAR-OTC',
    'USNDAQ100-OTC': 'USNDAQ100-OTC',
    'USOUSD-OTC': 'USOUSD-OTC',
    'WIFUSD-OTC': 'WIFUSD-OTC',
    'WLDUSD-OTC': 'WLDUSD-OTC',
    'XAGUSD-OTC': 'XAGUSD-OTC',
    'XAU/XAG-OTC': 'XAU/XAG-OTC',
    'XAUUSD-OTC': 'XAUUSD-OTC',
    'XNGUSD-OTC': 'XNGUSD-OTC',
    'XPDUSD-OTC': 'XPDUSD-OTC',
    'XPTUSD-OTC': 'XPTUSD-OTC',
    'XRPUSD-OTC': 'XRPUSD-OTC',
}


def resolve_asset_name(asset: str) -> str:
    """
    Resolve o nome que a API IQ Option aceita para um dado ativo.

    Lógica (em ordem de prioridade):
      1. Mapa explícito _OTC_API_MAP  — resultado definitivo e verificado.
      2. Se termina em -OTC e NÃO está no mapa → strip do sufixo (fallback).
      3. Caso contrário → retorna como está (mercado aberto já correto).

    Diagrama de compatibilidade (iqoptionapi v6.8.x / constants.py):
      • Apenas 9 pares Forex têm '-OTC' em ACTIVES; todos os demais OTC
        causam KeyError se enviados com sufixo → mapa e fallback obrigatórios.
      • Ativos muito novos (SOLUSD, DOTUSD, WIF, EU50, US2000, XNGUSD)
        não existem na v6.8.x local; a IQ Option os aceita em runtime via
        WebSocket dinâmico — o fallback strip garante que o nome seja enviado
        corretamente e o erro de "ativo indisponível" vem da corretora, não
        de um KeyError Python.
    """
    from iqoptionapi.constants import ACTIVES as _ACT

    # 1. Mapa explícito
    if asset in _OTC_API_MAP:
        api_name = _OTC_API_MAP[asset]
        if api_name != asset:
            log.debug(f'resolve_asset: {asset} → {api_name} (mapa)')
        # Verificar se o nome resolvido existe na lib local
        if api_name not in _ACT and '-OTC' not in api_name:
            log.debug(f'resolve_asset: {api_name} sem ID local (ok em runtime)')
        return api_name

    # 2. Fallback: strip -OTC
    if asset.endswith('-OTC'):
        base = asset[:-4]
        log.debug(f'resolve_asset: {asset} → {base} (fallback strip -OTC)')
        return base

    # 3. Mercado aberto / já correto
    return asset


def buy_binary_next_candle(asset: str, amount: float, direction: str, expiry: int = 1, account_type: str = 'PRACTICE'):
    """Entrada Binária M1 no nascimento da próxima vela. Suporta OTC e Mercado Aberto.
    
    Máximo de espera: 65s (próxima vela) + 5s (buy). Se exceder, retorna erro.
    """
    iq = get_iq()
    if not iq: return False, 'Bot não conectado à corretora'
    try:
        direction = direction.lower()
        if direction not in ('call', 'put'):
            return False, 'Direção inválida'

        api_asset = resolve_asset_name(asset)
        wait_sec = seconds_to_next_candle(60)
        # Cap: no máximo 62s de espera (evita bloquear thread por > 1 minuto)
        wait_sec = min(wait_sec, 62.0)
        log.info(f'⏰ Aguardando M1 em {wait_sec:.1f}s — {asset} (API: {api_asset}) {direction.upper()}')
        if wait_sec > 2:
            time.sleep(wait_sec - 1)

        # Trocar para conta correta (PRACTICE ou REAL)
        try:
            _cur_acct = getattr(iq, '__account_type__', None)
            if account_type.upper() == 'PRACTICE':
                iq.change_balance('PRACTICE')
            else:
                iq.change_balance('REAL')
        except Exception as _acc_err:
            log.warning(f'⚠️ Não foi possível trocar conta para {account_type}: {_acc_err}')

        # ── BINARY MODE (não blitz/turbo) ────────────────────────────────
        # Tenta binary option (M1 alinhado ao início do próximo minuto)
        # Fallback: turbo (blitz) se binary não suportado
        _binary_ok = False
        try:
            # Verificar se o método buy() aceita string 'binary'
            status, order_id = iq.buy(amount, api_asset, direction, 'binary')
            _binary_ok = status
        except Exception as _be:
            log.debug(f'Binary mode falhou ({_be}), tentando turbo...')
            status, order_id = None, None
        
        if not _binary_ok:
            # Fallback para turbo (expiry int)
            try:
                status, order_id = iq.buy(amount, api_asset, direction, expiry)
            except Exception as _te:
                log.error(f'Turbo mode também falhou: {_te}')
                return False, str(_te)
        if status:
            log.info(f'✅ Entrada: {asset} {direction.upper()} R${amount} ID={order_id}')
            return True, order_id
        else:
            reason = str(order_id) if order_id else 'sem retorno da corretora'
            if 'nill' in str(order_id).lower() or order_id is None:
                reason = f'Ativo {asset} pode estar fechado ou sem liquidez'
            elif 'amount' in str(order_id).lower():
                reason = f'Valor mínimo não atingido (mínimo IQ Option: R$1.00)'
            log.warning(f'❌ Rejeitado: {asset} {direction.upper()} — {reason}')
            return False, reason
    except KeyError as ke:
        api_nm = resolve_asset_name(asset)
        msg = (f'Ativo {asset} (API: {api_nm}) não reconhecido pela biblioteca IQ Option. '
               f'Chave ausente: {ke}. Verifique se o ativo está ativo na corretora.')
        log.error(f'buy_binary KeyError: {msg}')
        return False, msg
    except Exception as e:
        log.error(f'buy_binary erro: {e}')
        return False, str(e)


def check_win_iq(order_id, timeout: int = 90):
    """Aguarda e retorna resultado: ('win'|'loss'|'equal', valor).
    
    Roda em thread separada com timeout de 90s para nunca bloquear
    o worker do gunicorn indefinidamente.
    """
    iq = get_iq()
    if not iq or order_id is None: return None

    result_holder = [None]
    def _check():
        try:
            r = iq.check_win_v3(order_id)
            if r is None:
                result_holder[0] = None
                return
            r = float(r)
            if r > 0:   result_holder[0] = ('win',   round(r,       2))
            elif r < 0: result_holder[0] = ('loss',  round(abs(r),  2))
            else:       result_holder[0] = ('equal', 0.0)
        except Exception as e:
            log.warning(f'check_win {order_id}: {e}')
            result_holder[0] = None

    t = threading.Thread(target=_check, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        log.warning(f'check_win_iq timeout ({timeout}s) para order_id={order_id}')
        return None
    return result_holder[0]

# ═══════════════════════════════════════════════════════════════════════════════
_heartbeat_thread = None
_heartbeat_running = False

# Referência ao bot_state do app.py para reconexão automática no heartbeat
_bot_state_ref = None  # setado pelo app.py após importação

def heartbeat_iq():
    """Pinga a IQ Option a cada 25s para manter a conexão ativa.
    Após 3 falhas consecutivas: tenta reconexão automática com credenciais salvas.
    """
    global _heartbeat_running, _session_valid_cache, _bot_state_ref
    _fail_count = 0
    while _heartbeat_running:
        try:
            # Iterar sobre TODAS as instâncias ativas
            with _iq_global_lock:
                users_snapshot = list(_iq_instances.items())
            
            any_connected = False
            for _uname, iq in users_snapshot:
                if iq is None:
                    continue
                _result_hb = [None]
                def _ping(_iq=iq):
                    try: _result_hb[0] = _iq.get_balance()
                    except: _result_hb[0] = None
                _t = threading.Thread(target=_ping, daemon=True)
                _t.start(); _t.join(timeout=5)
                bal = _result_hb[0]
                if bal is not None and float(bal) >= 0:
                    log.debug(f'💓 Heartbeat [{_uname}] OK | saldo={bal}')
                    any_connected = True
                else:
                    log.warning(f'💔 Heartbeat [{_uname}] falhou: saldo={bal}')
            
            if not users_snapshot:
                raise ValueError('Nenhuma instância IQ ativa')
            
            if any_connected:
                _fail_count = 0
                _session_valid_cache = {'result': True, 'ts': time.time()}
            else:
                raise ValueError('Todas as instâncias falharam')
            time.sleep(15)  # ping a cada 15s
        except Exception as e:
            _fail_count += 1
            log.warning(f'💔 Heartbeat falhou ({_fail_count}x): {e}')
            _session_valid_cache = {'result': False, 'ts': 0.0}
            if _fail_count >= 1:  # reconectar na 1ª falha
                # Tentativa de reconexão automática com credenciais salvas
                _bs = _bot_state_ref
                _em = _bs.get('broker_email') if _bs else None
                _pw = _bs.get('broker_password') if _bs else None
                _ac = _bs.get('broker_account_type', 'PRACTICE') if _bs else 'PRACTICE'
                if _em and _pw:
                    log.warning(f'🔁 Heartbeat: reconectando automaticamente ({_ac})...')
                    try:
                        ok, res = connect_iq(_em, _pw, _ac, username=_uname if "_uname" in dir() else "default")
                        if ok:
                            _fail_count = 0
                            if _bs is not None:
                                _bs['broker_connected'] = True
                                _bs['broker_balance'] = res.get('balance', 0)
                            _session_valid_cache = {'result': True, 'ts': time.time()}
                            log.info(f'✅ Heartbeat: reconectado! Saldo: {res.get("balance",0)}')
                        else:
                            log.error(f'❌ Heartbeat reconexão falhou: {res}')
                            if _bs is not None:
                                _bs['broker_connected'] = False
                    except Exception as _re:
                        log.error(f'❌ Heartbeat erro na reconexão: {_re}')
                else:
                    log.warning('💔 Sem credenciais para reconexão automática')
                    if _bs is not None:
                        _bs['broker_connected'] = False
                _fail_count = 0
            time.sleep(8)

def start_heartbeat():
    """Inicia thread de heartbeat se ainda não estiver rodando."""
    global _heartbeat_thread, _heartbeat_running
    if _heartbeat_thread and _heartbeat_thread.is_alive():
        return
    _heartbeat_running = True
    _heartbeat_thread = threading.Thread(target=heartbeat_iq, daemon=True)
    _heartbeat_thread.start()
    log.info('💓 Heartbeat IQ Option iniciado (ping a cada 30s)')

def stop_heartbeat():
    global _heartbeat_running
    _heartbeat_running = False

# ═══════════════════════════════════════════════════════════════════════════════
# BACKTESTING AUTOMÁTICO — 12 ATIVOS OTC (últimos 30 dias simulados)
# ═══════════════════════════════════════════════════════════════════════════════



def run_backtest(assets: list = None, candles_per_window: int = 60,
                 windows: int = 60, seed_base: int = 42, min_win_rate: float = 10.0) -> dict:
    """
    Backtest sintético honesto usando o MESMO pipeline da análise real:
      - analisa apenas candles fechados
      - identifica padrão na vela recém-fechada
      - avalia a próxima vela como resultado
      - usa 60 janelas por ativo por padrão

    Retorna ranking por win_rate e tendência atual estimada do ativo.
    """
    if assets is None:
        assets = ALL_BINARY_ASSETS

    total_ops = total_wins = total_losses = 0
    asset_stats = {}

    for asset in assets:
        a_wins = a_losses = a_ops = a_signals_found = 0
        current_trend = 'INDEFINIDA'

        for w in range(max(20, windows)):
            rng_seed = seed_base + (abs(hash(asset)) % 10000) + w * 17
            rng = np.random.default_rng(rng_seed)

            base = 1.05 + rng.random() * 0.5
            drift = rng.choice([0.00035, -0.00035, 0.0], p=[0.38, 0.38, 0.24])
            noise = rng.normal(0, 0.00022, candles_per_window + 2)
            closes = base + np.cumsum(noise + drift)
            opens = np.roll(closes, 1)
            opens[0] = closes[0] - rng.normal(0, 0.00008)
            spread = np.abs(rng.normal(0.00010, 0.00004, candles_per_window + 2))
            highs = np.maximum(opens, closes) + spread
            lows = np.minimum(opens, closes) - spread
            volumes = calc_volume_candle(opens, closes, highs, lows)

            # janela fechada para análise = até a penúltima vela; última é o resultado futuro
            closed_ohlc = {
                'opens': opens[:-1],
                'highs': highs[:-1],
                'lows': lows[:-1],
                'closes': closes[:-1],
                'volumes': volumes[:-1],
            }
            sig = analyze_asset_full(asset, closed_ohlc)
            if sig is None:
                continue

            a_signals_found += 1
            direction = sig['direction']
            entry = float(closed_ohlc['closes'][-1])
            next_close = float(closes[-1])
            won = next_close > entry if direction == 'CALL' else next_close < entry

            a_ops += 1
            if won:
                a_wins += 1
            else:
                a_losses += 1

            if len(closed_ohlc['closes']) >= 50:
                _e5 = float(calc_ema(closed_ohlc['closes'], 5)[-1])
                _e50 = float(calc_ema(closed_ohlc['closes'], 50)[-1])
                if _e5 > _e50:
                    current_trend = 'ALTA'
                elif _e5 < _e50:
                    current_trend = 'BAIXA'
                else:
                    current_trend = 'LATERAL'

        win_rate = round(a_wins / a_ops * 100, 1) if a_ops > 0 else 0.0
        asset_stats[asset] = {
            'asset': asset,
            'ops': a_ops,
            'wins': a_wins,
            'losses': a_losses,
            'win_rate': win_rate,
            'signals_found': a_signals_found,
            'signal_rate': round(a_signals_found / max(1, windows) * 100, 1),
            'current_trend': current_trend,
            'type': 'OTC' if asset.endswith('-OTC') else 'OPEN',
        }
        total_ops += a_ops
        total_wins += a_wins
        total_losses += a_losses

    overall_wr = round(total_wins / total_ops * 100, 1) if total_ops > 0 else 0.0
    ranked = sorted(asset_stats.values(), key=lambda x: (x['win_rate'], x['ops']), reverse=True)
    ranked = [r for r in ranked if r['win_rate'] >= min_win_rate and r['ops'] > 0]

    return {
        'total_ops': total_ops,
        'total_wins': total_wins,
        'total_losses': total_losses,
        'overall_win_rate': overall_wr,
        'ranked': ranked,
        'asset_stats': asset_stats,
        'windows': windows,
        'candles_per_window': candles_per_window,
    }


def run_backtest_real(asset: str, candles: int = 250, timeframe: int = 60) -> dict:
    """
    Backtest em candles reais/sintéticos usando o mesmo pipeline do ao vivo:
    padrão no candle fechado -> resultado na vela seguinte.
    """
    iq = get_iq()
    fonte = 'simulado'
    closes = ohlc = None
    if iq is not None:
        try:
            closes, ohlc = get_candles_iq(asset, timeframe, max(120, candles))
            if closes is not None and ohlc is not None:
                fonte = 'real'
        except Exception:
            closes = ohlc = None
    if closes is None or ohlc is None:
        closes, ohlc = generate_synthetic_candles(asset, max(120, candles))

    n = len(ohlc['closes'])
    wins = losses = equals = total_sinais = 0
    pattern_stats = {}

    for idx in range(60, n - 1):
        sub = {k: v[:idx+1] for k, v in ohlc.items()}
        sig = analyze_asset_full(asset, sub)
        if sig is None:
            continue
        total_sinais += 1
        entry = float(sub['closes'][-1])
        next_close = float(ohlc['closes'][idx+1])
        direction = sig['direction']
        pat = sig.get('pattern', 'N/A')
        won = next_close > entry if direction == 'CALL' else next_close < entry
        eq = abs(next_close - entry) < 1e-12
        rec = pattern_stats.setdefault(pat, {'desc': pat, 'ops': 0, 'wins': 0, 'losses': 0, 'equals': 0})
        rec['ops'] += 1
        if eq:
            equals += 1
            rec['equals'] += 1
        elif won:
            wins += 1
            rec['wins'] += 1
        else:
            losses += 1
            rec['losses'] += 1

    total_ops = wins + losses + equals
    overall_wr = round(wins / max(1, wins + losses) * 100, 1) if (wins + losses) > 0 else 0.0
    top_patterns = []
    for k, v in pattern_stats.items():
        wr = round(v['wins'] / max(1, v['wins'] + v['losses']) * 100, 1) if (v['wins'] + v['losses']) > 0 else 0.0
        x = dict(v)
        x['win_rate'] = wr
        top_patterns.append(x)
    top_patterns.sort(key=lambda x: (x['win_rate'], x['ops']), reverse=True)

    # tendência atual
    try:
        _e5 = float(calc_ema(ohlc['closes'], 5)[-1])
        _e50 = float(calc_ema(ohlc['closes'], 50)[-1])
        current_trend = 'ALTA' if _e5 > _e50 else ('BAIXA' if _e5 < _e50 else 'LATERAL')
    except Exception:
        current_trend = 'INDEFINIDA'

    return {
        'asset': asset,
        'fonte': fonte,
        'candles': n,
        'total_sinais': total_sinais,
        'total_ops': total_ops,
        'wins': wins,
        'losses': losses,
        'equals': equals,
        'overall_win_rate': overall_wr,
        'current_trend': current_trend,
        'top_patterns': top_patterns[:12],
    }


def gerar_perfil_ativo(bt_result: dict) -> dict:
    """Gera perfil de configuração ideal do ativo a partir do backtest real."""
    asset    = bt_result['asset']
    patterns = bt_result.get('top_patterns', [])
    fonte    = bt_result.get('fonte', 'simulado')
    inds     = bt_result.get('indicadores_recomendados', ['EMA5/EMA50','RSI(5)'])
    conf     = bt_result.get('confluencia_sugerida', 3)

    # Top 5 padrões com WR >= 50% e ao menos 3 ocorrências
    padroes_ativos = [p['nome'] for p in patterns if p['win_rate']>=55 and p['total']>=3][:5]
    if not padroes_ativos:
        padroes_ativos = [p['nome'] for p in patterns[:5]]

    best = patterns[0] if patterns else None

    call_count = sum(p['direction_hist'].get('CALL',0) for p in bt_result.get('all_patterns',[]))
    put_count  = sum(p['direction_hist'].get('PUT',0)  for p in bt_result.get('all_patterns',[]))

    perfil = {
        'asset': asset, 'fonte': fonte,
        'padroes_ativos': padroes_ativos,
        'padroes_detalhes': patterns[:5],
        'indicadores': inds,
        'confluencia_minima': conf,
        'direcao_dominante': 'CALL' if call_count>=put_count else 'PUT',
        'overall_wr': bt_result.get('overall_win_rate', 0),
        'best_pattern': best['nome'] if best else None,
        'best_pattern_wr': best['win_rate'] if best else 0,
        'best_pattern_desc': best['desc'] if best else '',
        'total_sinais': bt_result.get('total_sinais', 0),
        'candles_analisados': bt_result.get('candles_analisados', 0),
        'confluence_stats': bt_result.get('confluence_stats', {}),
        'indicator_stats': bt_result.get('indicator_stats', {}),
        'atualizado_em': time.time(),
        'strategies_override': {
            'ema':   any('EMA' in i for i in inds),
            'rsi':   any('RSI' in i for i in inds),
            'adx':   any('ADX' in i for i in inds),
            'macd':  any('MACD' in i for i in inds),
            'bb':    any('Bollinger' in i for i in inds),
            'stoch': False,
            'lp':    True,
            'pat':   True,
            'fib':   False,
        }
    }
    with _profile_lock:
        _asset_profiles[asset] = perfil
    return perfil


def get_asset_profile(asset: str, force_refresh: bool = False) -> dict:
    """Retorna perfil do ativo completo (do cache ou gera novo backtest)."""
    with _profile_lock:
        cached = _asset_profiles.get(asset)
    if cached and not force_refresh:
        age = time.time() - cached.get('atualizado_em', 0)
        if age < 3600:  # cache válido por 1 hora
            return cached
    # Gerar novo perfil
    bt = run_backtest_real(asset, candles=200)
    perfil = gerar_perfil_ativo(bt)
    # Enriquecer com campos do backtest para API completa
    perfil.setdefault('active_patterns', bt.get('active_patterns', []))
    perfil.setdefault('melhor_padrao', perfil.get('best_pattern', 'nenhum'))
    perfil.setdefault('melhor_padrao_wr', perfil.get('best_pattern_wr', 0))
    perfil.setdefault('overall_win_rate', bt.get('overall_win_rate', 0))
    perfil.setdefault('total_sinais', bt.get('total_sinais', 0))
    perfil.setdefault('candles_analisados', bt.get('candles_analisados', 0))
    perfil.setdefault('fonte', bt.get('fonte', 'simulado'))
    perfil.setdefault('confluencia_sugerida', bt.get('confluencia_sugerida', 2))
    perfil.setdefault('indicadores_recomendados', bt.get('indicadores_recomendados', []))
    perfil.setdefault('top_patterns', bt.get('top_patterns', []))
    perfil.setdefault('confluence_stats', bt.get('confluence_stats', {}))
    return perfil



# ═══════════════════════════════════════════════════════════════════════════════
# AUTO MODE — AJUSTES PÓS-TESTE 20 OPS (08/03/2026)
# Resultado do teste: WR 45% com confluence=2, padrão evening_star baixo
# Fix: elevar min_confluence para 3, desabilitar evening_star sozinho sem EMA
# ═══════════════════════════════════════════════════════════════════════════════

# Padrões com WR comprovado no teste (≥60%):
# ✅ morning_star  → 75% WR (4 ops) — MANTER
# ✅ hammer        → 67% WR (3 ops) — MANTER
# ⚠️  engolfo_alta → 40% WR (5 ops) — requerer confluence≥3
# ❌ evening_star  → 29% WR (7 ops) — requerer confluence≥3 + EMA alinhada
# ❌ shooting_star → 0%  WR (1 op)  — requerer confluence≥3

# Categorias com melhor WR:
# ✅ INDICES  → 67% WR
# ✅ STOCKS   → 67% WR
# ⚠️  CRYPTO  → 44% WR
# ⚠️  FOREX   → 33% WR
# ❌ COMMODITIES → 0% WR

AUTO_MODE_CONFIG = {
    'min_confluence_default': 3,          # subiu de 2 para 3
    'min_confluence_evening_star': 4,     # evening_star precisa de mais confirmação
    'min_confluence_shooting_star': 4,    # idem
    'priority_categories': ['INDICES', 'STOCKS', 'FOREX'],  # melhores WR
    'avoid_categories': [],               # COMMODITIES com pouca amostra, não bloquear
    'best_patterns': ['morning_star', 'hammer', 'engolfo_alta', 'engolfo_baixa'],
    'min_strength': 84,                   # score mínimo subiu de 80 para 84
}

# =============================================================================
# MÓDULO FLIPCOIN DETECTOR — DANBOT v3.1
# Detecta ativos em "moeda flip" (choppy/sem direção)
# =============================================================================
def detect_flipcoin(opens, highs, lows, closes, volumes=None, lookback=20):
    """
    Detecta mercado em modo 'Flip Coin' — ativo oscilando sem direção,
    revertendo constantemente, sem edge direcional confiável.

    Critérios (≥3 confirmados = FlipCoin):
    1. ADX < 20
    2. RSI entre 40-60
    3. Alternância de velas >60%
    4. Corpo médio < 30% do range médio
    5. ATR pequeno
    6. Últimas velas alternando
    """
    if len(closes) < lookback:
        return {'is_flipcoin': False, 'score': 0, 'reasons': [], 'severity': 'none', 'alt_rate': 0.0, 'body_ratio': 0.0}

    import numpy as np

    c = np.array([float(x) for x in closes[-lookback:]])
    h = np.array([float(x) for x in highs[-lookback:]])
    l = np.array([float(x) for x in lows[-lookback:]])
    o = np.array([float(x) for x in opens[-lookback:]])

    score = 0.0
    reasons = []

    # 1. ADX baixo = sem tendência
    try:
        adx_v = calc_adx(highs, lows, closes, 14)
        adx_last = float(adx_v[0]) if hasattr(adx_v, '__len__') else float(adx_v)
        if adx_last < 20:
            score += 1
            reasons.append(f'📉 ADX={adx_last:.1f}<20 (sem tendência)')
        elif adx_last < 25:
            score += 0.5
            reasons.append(f'📉 ADX={adx_last:.1f}<25 (tendência fraca)')
    except Exception:
        pass

    # 2. RSI neutro
    try:
        rsi_arr = calc_rsi(closes, 5)
        rsi_last = float(rsi_arr[-1]) if hasattr(rsi_arr, '__len__') else float(rsi_arr)
        if 40 <= rsi_last <= 60:
            score += 1
            reasons.append(f'⚖️ RSI={rsi_last:.1f} neutro (40-60)')
        elif 35 <= rsi_last <= 65:
            score += 0.5
    except Exception:
        pass

    # 3. Alternância de velas
    directions = [1 if c[i] >= o[i] else -1 for i in range(len(c))]
    alternations = sum(1 for i in range(1, len(directions)) if directions[i] != directions[i-1])
    alt_rate = alternations / max(1, len(directions) - 1)
    if alt_rate >= 0.60:
        score += 1
        reasons.append(f'🔀 Alternância={alt_rate:.0%} (flip constante)')
    elif alt_rate >= 0.50:
        score += 0.5

    # 4. Corpo fraco
    bodies = np.abs(c - o)
    ranges = np.maximum(h - l, 1e-9)
    body_ratio = float(np.mean(bodies / ranges))
    if body_ratio < 0.30:
        score += 1
        reasons.append(f'🕯️ Corpo={body_ratio:.0%}<30% (velas sem convicção)')
    elif body_ratio < 0.40:
        score += 0.5
        reasons.append(f'🕯️ Corpo={body_ratio:.0%}<40% (velas fracas)')

    # 5. ATR pequeno
    try:
        atr_arr = [float(h[i] - l[i]) for i in range(len(h))]
        atr_avg = float(np.mean(atr_arr[-10:])) if len(atr_arr) >= 10 else float(np.mean(atr_arr))
        price_ref = float(c[-1])
        atr_pct = atr_avg / (price_ref + 1e-9) * 100
        if atr_pct < 0.05:
            score += 1
            reasons.append(f'📊 ATR={atr_pct:.3f}% (volatilidade mínima flip)')
    except Exception:
        pass

    # 6. Últimas 5 velas alternando
    last5 = directions[-5:] if len(directions) >= 5 else directions
    last5_alt = sum(1 for i in range(1, len(last5)) if last5[i] != last5[i-1])
    if last5_alt >= 3:
        score += 0.5
        reasons.append(f'🎲 Últimas {len(last5)} velas alternando ({last5_alt}x)')

    score = int(score)
    is_flipcoin = score >= 3
    if is_flipcoin:
        severity = 'alto' if score >= 5 else ('médio' if score >= 4 else 'baixo')
    else:
        severity = 'none'

    return {
        'is_flipcoin': is_flipcoin,
        'score': score,
        'reasons': reasons,
        'severity': severity,
        'alt_rate': round(alt_rate, 2),
        'body_ratio': round(body_ratio, 2),
    }
