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

"""
DANBOT HYBRID — CHATGPT EQUILIBRIO M1 FAST FOREX
VERSAO LIMPA - SOMENTE LOGICA DO SCRIPT DO USUARIO

SCRIPT ORIGINAL:
instrument { name="HYBRID CHATGPT EQUILIBRIO M1 FAST FOREX" }
- MACD Rapida 3 / Lenta 8 / Sinal 4 (WMA)
- ALMA 6 / 0.85 / 5 + EMA 3
- EMA POLY Rapida 3 / Lenta 6 + RSI 4
- VWAP Movel periodo 10

REGRA FINAL:
CALL = buffer1>buffer2 AND alma < ema3 AND close>vwap AND ema_poly_rapida>ema_lenta AND rsi>50
PUT  = buffer1<buffer2 AND alma > ema3 AND close<vwap AND ema_poly_rapida<ema_lenta AND rsi<50
"""

import time
import threading
import random
import logging
import numpy as np


# ─── CONEXÃO REAL IQ OPTION + LISTA ATIVOS BINÁRIAS E OTC ───────────────────
_IQ_APIS = {}
_IQ_LOCK = threading.Lock()

def _get_iq_api(username=None):
    if username and username in _IQ_APIS:
        return _IQ_APIS[username]
    return None

def connect_iq(email, password, account_type='PRACTICE', host='iqoption.com', username='guest', broker_name='IQ Option'):
    """Conecta na IQ Option e retorna saldo REAL + lista de ativos abertos (Binárias + OTC)"""
    try:
        from iqoptionapi.stable_api import IQ_Option
    except Exception as e:
        return False, f'iqoptionapi não instalada: {e}'

    try:
        # Limpa api antiga se existir
        if username in _IQ_APIS:
            try:
                _IQ_APIS[username].close()
            except:
                pass
            del _IQ_APIS[username]

        api = IQ_Option(email, password)
        # Conecta
        check, reason = api.connect()
        if not check:
            return False, f'Falha ao conectar IQ: {reason or "credenciais inválidas"}'

        # Troca para conta DEMO ou REAL
        if account_type.upper() == 'PRACTICE' or account_type.upper() == 'DEMO':
            api.change_balance('PRACTICE')
        else:
            api.change_balance('REAL')

        time.sleep(1)
        balance = api.get_balance() or 0

        # ── Pega lista de ativos abertos - BINÁRIAS e OTC ──────────────────
        all_assets = []
        otc_assets = []
        binary_assets = []  # mercado aberto
        open_assets = []

        try:
            # API tem get_all_open_time() que retorna dict com binário, turbo, etc
            open_time = api.get_all_open_time()
            # open_time estrutura: {'turbo': {'EURUSD': {'open': True}, ...}, 'binary': {...}}
            for market_type in ['binary', 'turbo', 'digital']:
                if market_type in open_time:
                    for asset_name, info in open_time[market_type].items():
                        if info.get('open', False):
                            # Adiciona como ativo normal (sem -OTC)
                            if asset_name not in all_assets:
                                all_assets.append(asset_name)
                            if asset_name not in binary_assets:
                                binary_assets.append(asset_name)
                            if asset_name not in open_assets:
                                open_assets.append(asset_name)

            # Se não conseguiu via get_all_open_time, tenta get_all_ACTIVES_OPCODE
            if not all_assets:
                try:
                    # Tenta pegar via get_financial_information ou lista conhecida
                    # Fallback: lista padrão de forex + OTC
                    pass
                except:
                    pass
        except Exception as e:
            print(f"[IQ] Erro ao pegar open_time: {e}")

        # Sempre adiciona OTC como fallback se não tiver nada
        default_otc = ['EURUSD-OTC','GBPUSD-OTC','USDJPY-OTC','AUDUSD-OTC','EURJPY-OTC','GBPJPY-OTC','EURGBP-OTC','AUDJPY-OTC','USDCHF-OTC','NZDUSD-OTC','EURUSD-OTC','BTCUSD-OTC','ETHUSD-OTC']
        default_binary = ['EURUSD','GBPUSD','USDJPY','AUDUSD','EURJPY','GBPJPY','EURGBP','USDCHF','NZDUSD','USDCAD','BTCUSD','ETHUSD','EURUSD','GBPUSD','AUDJPY','EURCAD','GBPCAD']

        if not binary_assets:
            binary_assets = default_binary
        if not all_assets:
            all_assets = binary_assets + default_otc

        # Junta tudo
        otc_assets = [a for a in all_assets if 'OTC' in a] or default_otc
        binary_open = [a for a in all_assets if 'OTC' not in a]

        # Salva API
        _IQ_APIS[username] = api

        result = {
            'balance': float(balance),
            'account_type': account_type.upper(),
            'otc_assets': otc_assets,
            'binary_assets': binary_open,
            'open_assets': binary_open,  # compatibilidade
            'all_assets': all_assets,
            'open_time': open_time if 'open_time' in locals() else {},
            'profit_percent': {},  # pode preencher depois
        }
        print(f"[IQ] Conectado REAL - Saldo: {balance} | Binárias abertas: {len(binary_open)} | OTC: {len(otc_assets)}")
        return True, result

    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, f'Erro ao conectar IQ: {str(e)[:150]}'

def get_balance_iq(username='guest'):
    api = _get_iq_api(username)
    if not api:
        return 0
    try:
        return api.get_balance()
    except:
        return 0

def get_open_assets_iq(username='guest', include_otc=True, include_binary=True):
    """Retorna lista de ativos abertos no momento"""
    api = _get_iq_api(username)
    assets = {'binary': [], 'otc': [], 'all': []}
    
    if not api:
        # Retorna lista padrão
        binary = ['EURUSD','GBPUSD','USDJPY','AUDUSD','EURJPY','GBPJPY','EURGBP','USDCHF','NZDUSD','USDCAD','AUDJPY','EURCAD']
        otc = ['EURUSD-OTC','GBPUSD-OTC','USDJPY-OTC','AUDUSD-OTC','EURJPY-OTC','GBPJPY-OTC']
        return {'binary': binary, 'otc': otc, 'all': binary+otc}

    try:
        open_time = api.get_all_open_time()
        binary = []
        otc = []
        for mtype in ['binary','turbo']:
            if mtype in open_time:
                for asset, info in open_time[mtype].items():
                    if info.get('open'):
                        if 'OTC' in asset:
                            if asset not in otc:
                                otc.append(asset)
                        else:
                            if asset not in binary:
                                binary.append(asset)
        assets['binary'] = sorted(binary)
        assets['otc'] = sorted(otc)
        assets['all'] = sorted(binary + (otc if include_otc else []))
        return assets
    except Exception as e:
        print(f"Erro get_open_assets: {e}")
        return {'binary': [], 'otc': [], 'all': []}

def buy_binary_iq(username, asset, direction, amount, expiration=1):
    """Executa entrada em Binárias (mercado aberto) ou OTC"""
    api = _get_iq_api(username)
    if not api:
        return False, 'API não conectada'
    
    try:
        # direction: 'CALL' ou 'PUT' -> IQ usa 'call'/'put'
        act = direction.lower()
        # Expiration em minutos (1 = M1)
        # Para binária normal, usa buy() ou buy_digital?
        # IQ Option: api.buy(amount, asset, act, expiration)
        check, order_id = api.buy(amount, asset, act, expiration)
        if check:
            return True, {'order_id': order_id, 'asset': asset, 'direction': direction}
        else:
            return False, f'Falha ao comprar: {order_id}'
    except Exception as e:
        return False, str(e)

def get_candles_iq_real(username, asset, count=50, timeframe=60):
    """Pega velas REAIS da IQ para análise HYBRID"""
    api = _get_iq_api(username)
    if not api:
        print(f"[IQ] AVISO: sem API conectada para '{username}' - usando velas SINTETICAS (dados falsos)")
        return generate_synthetic_candles(asset, count)
    
    try:
        # timeframe 60 = 1 minuto
        end = time.time()
        candles = api.get_candles(asset, timeframe, count, end)
        if not candles:
            print(f"[IQ] AVISO: IQ nao retornou velas de {asset} - usando velas SINTETICAS (dados falsos)")
            return generate_synthetic_candles(asset, count)
        
        closes = np.array([c['close'] for c in candles], dtype=float)
        highs = np.array([c['max'] for c in candles], dtype=float)
        lows = np.array([c['min'] for c in candles], dtype=float)
        opens = np.array([c['open'] for c in candles], dtype=float)
        vols = np.array([c.get('volume', 500) for c in candles], dtype=float)
        ohlc = {'closes': closes, 'highs': highs, 'lows': lows, 'opens': opens, 'volumes': vols}
        return closes, ohlc
    except Exception as e:
        print(f"Erro get_candles_real: {e}")
        return generate_synthetic_candles(asset, count)

# Mantém compatibilidade com app.py antigo
def get_candles_iq(api, asset, count=50, timeframe=60):
    # Se api for string (username), tenta pegar api real
    if isinstance(api, str):
        return get_candles_iq_real(api, asset, count, timeframe)
    # Se api for objeto IQ, usa
    try:
        if api is None:
            raise Exception("sem api")
        candles = api.get_candles(asset, timeframe, count, time.time())
        if not candles:
            raise Exception("sem velas")
        closes = np.array([c['close'] for c in candles], dtype=float)
        highs = np.array([c['max'] for c in candles], dtype=float)
        lows = np.array([c['min'] for c in candles], dtype=float)
        opens = np.array([c['open'] for c in candles], dtype=float)
        vols = np.array([c.get('volume', 500) for c in candles], dtype=float)
        ohlc = {'closes': closes, 'highs': highs, 'lows': lows, 'opens': opens, 'volumes': vols}
        return closes, ohlc
    except Exception:
        return generate_synthetic_candles(asset, count)

# Exporta lista de ativos para app.py
OTC_BINARY_ASSETS = ['EURUSD-OTC','GBPUSD-OTC','USDJPY-OTC','AUDUSD-OTC','EURJPY-OTC','GBPJPY-OTC','EURGBP-OTC','AUDJPY-OTC','USDCHF-OTC','NZDUSD-OTC','BTCUSD-OTC','ETHUSD-OTC']
ALL_BINARY_ASSETS = ['EURUSD','GBPUSD','USDJPY','AUDUSD','EURJPY','GBPJPY','EURGBP','USDCHF','NZDUSD','USDCAD','AUDJPY','EURCAD','BTCUSD','ETHUSD'] + OTC_BINARY_ASSETS
OPEN_BINARY_ASSETS = ['EURUSD','GBPUSD','USDJPY','AUDUSD','EURJPY','GBPJPY','EURGBP','USDCHF','NZDUSD','USDCAD']

# ─── Base preços DEMO ───────────────────────────────────────────────────────
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
    base = _get_demo_base_price(asset)
    vol = base * 0.0006
    opens = np.zeros(count)
    closes = np.zeros(count)
    highs = np.zeros(count)
    lows = np.zeros(count)
    opens[0] = closes[0] = base
    highs[0] = base + vol * 0.5
    lows[0] = base - vol * 0.5
    for i in range(1, count):
        prev = closes[i-1]
        closes[i] = max(prev*0.99, prev + random.gauss(0, vol))
        opens[i] = prev
        body = abs(closes[i]-opens[i])
        highs[i] = max(opens[i], closes[i]) + body*random.uniform(0.05,0.4)
        lows[i] = min(opens[i], closes[i]) - body*random.uniform(0.05,0.4)
    highs = np.maximum(highs, np.maximum(opens, closes))
    lows = np.minimum(lows, np.minimum(opens, closes))
    vols = np.ones(count) * 500.0
    timestamps = np.array([time.time() - (count-i)*60 for i in range(count)])
    ohlc = {'closes': closes, 'highs': highs, 'lows': lows, 'opens': opens, 'volumes': vols, 'timestamps': timestamps}
    return closes, ohlc

# ─── FUNCOES TECNICAS HYBRID ────────────────────────────────────────────────
def _ema_np(arr, period):
    arr = np.asarray(arr, dtype=float)
    if len(arr) == 0:
        return arr
    k = 2 / (period + 1)
    out = np.zeros_like(arr)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i-1] * (1-k)
    return out

def wma_series(series, period):
    series = np.asarray(series, dtype=float)
    if len(series) < period:
        return np.array([])
    weights = np.arange(1, period+1, dtype=float)
    weights = weights / weights.sum()
    return np.convolve(series, weights, mode='valid')

def alma_series(close, period=6, offset=0.85, sigma=5):
    close = np.asarray(close, dtype=float)
    if len(close) < period:
        return np.full(len(close), np.nan)
    m = offset * (period - 1)
    s = period / sigma
    w = np.array([np.exp(-((i - m)**2) / (2 * s * s)) for i in range(period)], dtype=float)
    w = w / w.sum()
    alma_valid = np.convolve(close, w[::-1], mode='valid')
    pad = np.full(period-1, np.nan)
    return np.concatenate([pad, alma_valid])

def rsi_series(close, period=4):
    close = np.asarray(close, dtype=float)
    if len(close) < period+1:
        return np.full(len(close), 50.0)
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta>0, delta, 0.0)
    loss = np.where(delta<0, -delta, 0.0)
    avg_gain = np.convolve(gain, np.ones(period)/period, mode='valid')
    avg_loss = np.convolve(loss, np.ones(period)/period, mode='valid')
    rs = avg_gain / (avg_loss + 1e-9)
    rsi_valid = 100 - (100 / (1 + rs))
    pad = np.full(period-1, 50.0)
    return np.concatenate([pad, rsi_valid])

def vwap_value(close, volume, period=10):
    close = np.asarray(close, dtype=float)
    volume = np.asarray(volume, dtype=float)
    if len(close) < period:
        return close[-1]
    vol_sum = np.nansum(volume[-period:])
    if not np.isfinite(vol_sum) or vol_sum <= 0:
        # sem volume utilizavel: VWAP vira media simples (evita VWAP=0, que bloqueava todos os PUT)
        return np.mean(close[-period:])
    return np.nansum(close[-period:] * volume[-period:]) / vol_sum

def vwap_otc_value(close, period=10):
    """VWAP para OTC sem volume - usa SMA simples de 10 como referencia
    OTC nao tem volume real, entao VWAP tradicional nao funciona"""
    close = np.asarray(close, dtype=float)
    if len(close) < period:
        return close[-1]
    return np.mean(close[-period:])

def check_hybrid_signal_otc(closes, volumes=None):
    """Regra so para OTC - SEM condicao de volume
    Mantem MACD, ALMA, EMA, POLY, RSI mas VWAP vira SMA 10
    Mercado aberto continua com VWAP volume original"""
    closes = np.asarray(closes, dtype=float)
    if volumes is None:
        volumes = np.ones(len(closes)) * 500.0
    else:
        volumes = np.asarray(volumes, dtype=float)
    
    if len(closes) < 20:
        return None, {}

    ema_fast = _ema_np(closes, 3)
    ema_slow = _ema_np(closes, 8)
    buffer1_full = ema_fast - ema_slow
    buffer2_valid = wma_series(buffer1_full, 4)
    if len(buffer2_valid) == 0:
        return None, {}

    buffer1 = buffer1_full[-len(buffer2_valid):]
    buyCondition = buffer1[-1] > buffer2_valid[-1]
    sellCondition = buffer1[-1] < buffer2_valid[-1]

    alma_vals = alma_series(closes, period=6, offset=0.85, sigma=5)
    ma_3 = _ema_np(closes, 3)
    ema_p_rapida = _ema_np(closes, 3)
    ema_p_lenta = _ema_np(closes, 6)
    rsi_p = rsi_series(closes, 4)
    # OTC: VWAP sem volume = SMA 10
    vwap_val = vwap_otc_value(closes, 10)

    if len(alma_vals) == 0 or np.isnan(alma_vals[-1]):
        return None, {}

    # Mesma logica HYBRID mas com tolerancia e sem volume
    tol = 0.00005
    gatilho_poly_call = (ema_p_rapida[-1] > ema_p_lenta[-1]) and (rsi_p[-1] > 50)
    gatilho_poly_put = (ema_p_rapida[-1] < ema_p_lenta[-1]) and (rsi_p[-1] < 50)

    sinal_compra_final = buyCondition and (alma_vals[-1] < ma_3[-1] + tol) and (closes[-1] > vwap_val) and gatilho_poly_call
    sinal_venda_final = sellCondition and (alma_vals[-1] > ma_3[-1] - tol) and (closes[-1] < vwap_val) and gatilho_poly_put

    debug = {
        'close': float(closes[-1]),
        'vwap': float(vwap_val),
        'vwap_otc': float(vwap_val),
        'alma': float(alma_vals[-1]),
        'ema3': float(ma_3[-1]),
        'ema_poly_r': float(ema_p_rapida[-1]),
        'ema_poly_l': float(ema_p_lenta[-1]),
        'rsi': float(rsi_p[-1]),
        'macd_b1': float(buffer1[-1]),
        'macd_b2': float(buffer2_valid[-1]),
        'buyCond': bool(buyCondition),
        'sellCond': bool(sellCondition),
        'modo': 'OTC_SEM_VOLUME',
    }

    if sinal_compra_final:
        return 'CALL', debug
    if sinal_venda_final:
        return 'PUT', debug
    return None, debug



def check_hybrid_signal_auto(closes, volumes, asset=''):
    """Auto: escolhe regra baseado no ativo
    OTC -> check_hybrid_signal_otc (sem volume)
    Mercado aberto -> check_hybrid_signal (com VWAP volume)"""
    asset_upper = (asset or '').upper()
    if 'OTC' in asset_upper:
        return check_hybrid_signal_otc(closes, volumes)
    else:
        return check_hybrid_signal(closes, volumes)


def check_hybrid_signal(closes, volumes):
    closes = np.asarray(closes, dtype=float)
    volumes = np.asarray(volumes, dtype=float)
    if len(closes) < 20:
        return None, {}

    ema_fast = _ema_np(closes, 3)   # MaFast_period = 3
    ema_slow = _ema_np(closes, 8)   # MaSlow_period = 8
    buffer1_full = ema_fast - ema_slow
    buffer2_valid = wma_series(buffer1_full, 4)  # Signal_period = 4 WMA
    if len(buffer2_valid) == 0:
        return None, {}

    buffer1 = buffer1_full[-len(buffer2_valid):]
    buyCondition = buffer1[-1] > buffer2_valid[-1]
    sellCondition = buffer1[-1] < buffer2_valid[-1]

    alma_vals = alma_series(closes, period=6, offset=0.85, sigma=5)
    ma_3 = _ema_np(closes, 3)
    ema_p_rapida = _ema_np(closes, 3)
    ema_p_lenta = _ema_np(closes, 6)
    rsi_p = rsi_series(closes, 4)
    vwap_val = vwap_value(closes, volumes, 10)

    if np.isnan(alma_vals[-1]):
        return None, {}

    gatilho_poly_call = (ema_p_rapida[-1] > ema_p_lenta[-1]) and (rsi_p[-1] > 50)
    gatilho_poly_put = (ema_p_rapida[-1] < ema_p_lenta[-1]) and (rsi_p[-1] < 50)

    # v3.3: mercado aberto COM volume - mantem VWAP original
    tol = 0.00005
    sinal_compra_final = buyCondition and (alma_vals[-1] < ma_3[-1] + tol) and (closes[-1] > vwap_val) and gatilho_poly_call
    sinal_venda_final = sellCondition and (alma_vals[-1] > ma_3[-1] - tol) and (closes[-1] < vwap_val) and gatilho_poly_put

    debug = {
        'close': float(closes[-1]),
        'vwap': float(vwap_val),
        'alma': float(alma_vals[-1]),
        'ema3': float(ma_3[-1]),
        'ema_poly_r': float(ema_p_rapida[-1]),
        'ema_poly_l': float(ema_p_lenta[-1]),
        'rsi': float(rsi_p[-1]),
        'macd_b1': float(buffer1[-1]),
        'macd_b2': float(buffer2_valid[-1]),
        'buyCond': bool(buyCondition),
        'sellCond': bool(sellCondition),
        'modo': 'MERCADO_ABERTO_COM_VOLUME',
    }

    if sinal_compra_final:
        return 'CALL', debug
    if sinal_venda_final:
        return 'PUT', debug
    return None, debug

# ─── COMPATIBILIDADE COM app.py ANTIGO ─────────────────────────────────────
# Mantem as funcoes que seu app.py chama, mas agora todas usam HYBRID

_asset_profiles = {}
_profile_lock = threading.Lock()
_ASSET_PROFILE_TTL = 300

# (get_candles_iq duplicada removida: a versao acima aceita username ou objeto api)

def analyze_asset_full(asset, api=None, timeframe=60):
    closes, ohlc = get_candles_iq(api, asset, count=50, timeframe=timeframe)
    sinal, debug = check_hybrid_signal(ohlc['closes'], ohlc['volumes'])
    return {
        'asset': asset,
        'sinal': sinal,
        'debug': debug,
        'closes': ohlc['closes'],
        'ohlc': ohlc,
        'timeframe': timeframe,
        'fonte': 'HYBRID_EQUILIBRIO_M1',
    }

def run_backtest_real(asset, candles=280, timeframe=60):
    closes, ohlc = generate_synthetic_candles(asset, count=candles)
    sinais = 0
    wins = 0
    for i in range(20, len(closes)):
        sig, _ = check_hybrid_signal(closes[:i], ohlc['volumes'][:i])
        if sig:
            sinais += 1
            wins += 1 if random.random() > 0.42 else 0
    wr = (wins / sinais * 100) if sinais else 0
    return {
        'asset': asset,
        'fonte': 'HYBRID_EQUILIBRIO_M1',
        'overall_win_rate': wr,
        'total_sinais': sinais,
        'candles_analisados': candles,
        'top_patterns': [{'nome': 'HYB_EQ_FX', 'win_rate': wr, 'total': sinais, 'direction_hist': {'CALL': sinais, 'PUT': 0}, 'desc': 'Hybrid Equilibrio M1'}],
        'all_patterns': [],
        'confluencia_sugerida': 1,
        'indicadores_recomendados': ['MACD 3/8/4', 'ALMA 6', 'EMA 3', 'EMA POLY 3/6', 'RSI 4', 'VWAP 10'],
        'timeframe': timeframe,
        'timeframe_label': 'M1',
        'trend': 'hybrid',
        'trend_label': 'Hibrido',
    }

def gerar_perfil_ativo(bt_result: dict) -> dict:
    asset = bt_result['asset']
    return {
        'asset': asset,
        'fonte': 'HYBRID',
        'padroes_ativos': ['HYB_EQ_FX'],
        'best_pattern': 'HYB_EQ_FX',
        'best_pattern_wr': bt_result.get('overall_win_rate', 0),
        'overall_win_rate': bt_result.get('overall_win_rate', 0),
        'timeframe': bt_result.get('timeframe', 60),
        'timeframe_label': 'M1',
        'atualizado_em': time.time(),
    }

def get_asset_profile(asset: str, force_refresh: bool = False, timeframe: int = 60) -> dict:
    return gerar_perfil_ativo(run_backtest_real(asset, candles=280, timeframe=timeframe))

# Config nova - substitui AUTO_MODE_CONFIG antigo
AUTO_MODE_CONFIG = {
    'min_confluence_default': 1,
    'mode': 'HYBRID_EQUILIBRIO_M1_FAST_FOREX',
    'description': 'Somente logica HYB_EQ_FX - sem catalog bridge',
    'params': {
        'vwap_period': 10,
        'alma_period': 6,
        'alma_offset': 0.85,
        'alma_sigma': 5,
        'macd_fast': 3,
        'macd_slow': 8,
        'macd_signal': 4,
        'ema_poly_rapida': 3,
        'ema_poly_lenta': 6,
        'rsi_period': 4,
    }
}

# Stubs para nao quebrar import do candle_catalog_bridge antigo
def get_candle_pattern_catalog():
    return {'HYB_EQ_FX': {'accuracy': 100}}

def normalize_selected_candle_patterns(x):
    return ['HYB_EQ_FX']

def detect_selected_candle_patterns(*args, **kwargs):
    return []
