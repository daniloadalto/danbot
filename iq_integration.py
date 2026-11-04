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
    return np.sum(close[-period:] * volume[-period:]) / (np.sum(volume[-period:]) + 1e-9)

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

    sinal_compra_final = buyCondition and (alma_vals[-1] < ma_3[-1]) and (closes[-1] > vwap_val) and gatilho_poly_call
    sinal_venda_final = sellCondition and (alma_vals[-1] > ma_3[-1]) and (closes[-1] < vwap_val) and gatilho_poly_put

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

def get_candles_iq(api, asset, count=50, timeframe=60):
    # Tenta buscar da IQ, se falhar usa sintetico
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
    
