"""
DANBOT — Candle Engine separado
================================
Módulo dedicado para padrões de candles clássicos e avançados.

Objetivo:
- separar padrões de candle das demais estratégias
- permitir ativar/desativar grupos e padrões individuais
- reduzir dupla contagem entre candle_score / wick / sweep
- manter integração simples com o restante do bot

Uso esperado dentro do iq_integration.py:
    from candles_engine import normalize_candle_config, analyze_candle_engine
    cfg = normalize_candle_config(strategies)
    candle_pack = analyze_candle_engine(opens, highs, lows, closes, e5, e50, cfg)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
import numpy as np


DEFAULT_CLASSIC_PATTERNS = [
    'engolfo_alta', 'engolfo_baixa',
    'martelo', 'estrela_cadente',
    'morning_star', 'evening_star',
    'tweezer_bottom', 'tweezer_top',
    'tres_soldados', 'tres_corvos',
    'pinbar_alta', 'pinbar_baixa',
    'harami_alta', 'harami_baixa',
    'three_inside_up', 'three_inside_down',
    'three_outside_up', 'three_outside_down',
    'kicker_alta', 'kicker_baixa',
]

DEFAULT_ADVANCED_PATTERNS = [
    'marubozu_alta', 'marubozu_baixa',
    'breakout_body_alta', 'breakout_body_baixa',
    'inside_break_alta', 'inside_break_baixa',
    'outside_reversal_alta', 'outside_reversal_baixa',
    'trap_top', 'trap_bottom',
    'micro_pullback_alta', 'micro_pullback_baixa',
]

PATTERN_WEIGHTS = {
    'engolfo_alta': 9, 'engolfo_baixa': 9,
    'morning_star': 10, 'evening_star': 10,
    'martelo': 8, 'estrela_cadente': 8,
    'pinbar_alta': 7, 'pinbar_baixa': 7,
    'tweezer_bottom': 7, 'tweezer_top': 7,
    'tres_soldados': 8, 'tres_corvos': 8,
    'harami_alta': 6, 'harami_baixa': 6,
    'three_inside_up': 7, 'three_inside_down': 7,
    'three_outside_up': 8, 'three_outside_down': 8,
    'kicker_alta': 10, 'kicker_baixa': 10,
    'marubozu_alta': 7, 'marubozu_baixa': 7,
    'breakout_body_alta': 7, 'breakout_body_baixa': 7,
    'inside_break_alta': 6, 'inside_break_baixa': 6,
    'outside_reversal_alta': 8, 'outside_reversal_baixa': 8,
    'trap_top': 8, 'trap_bottom': 8,
    'micro_pullback_alta': 5, 'micro_pullback_baixa': 5,
}

PATTERN_LABELS = {
    'engolfo_alta': 'Engolfo de Alta',
    'engolfo_baixa': 'Engolfo de Baixa',
    'morning_star': 'Morning Star',
    'evening_star': 'Evening Star',
    'martelo': 'Martelo',
    'estrela_cadente': 'Estrela Cadente',
    'pinbar_alta': 'Pinbar de Alta',
    'pinbar_baixa': 'Pinbar de Baixa',
    'tweezer_bottom': 'Tweezer Bottom',
    'tweezer_top': 'Tweezer Top',
    'tres_soldados': 'Três Soldados',
    'tres_corvos': 'Três Corvos',
    'harami_alta': 'Harami de Alta',
    'harami_baixa': 'Harami de Baixa',
    'three_inside_up': 'Three Inside Up',
    'three_inside_down': 'Three Inside Down',
    'three_outside_up': 'Three Outside Up',
    'three_outside_down': 'Three Outside Down',
    'kicker_alta': 'Kicker de Alta',
    'kicker_baixa': 'Kicker de Baixa',
    'marubozu_alta': 'Marubozu de Alta',
    'marubozu_baixa': 'Marubozu de Baixa',
    'breakout_body_alta': 'Breakout com Corpo de Alta',
    'breakout_body_baixa': 'Breakout com Corpo de Baixa',
    'inside_break_alta': 'Inside Break de Alta',
    'inside_break_baixa': 'Inside Break de Baixa',
    'outside_reversal_alta': 'Outside Reversal de Alta',
    'outside_reversal_baixa': 'Outside Reversal de Baixa',
    'trap_top': 'Trap Top',
    'trap_bottom': 'Trap Bottom',
    'micro_pullback_alta': 'Micro Pullback de Alta',
    'micro_pullback_baixa': 'Micro Pullback de Baixa',
}


@dataclass
class CandlePattern:
    key: str
    direction: str
    weight: int
    category: str
    description: str


def normalize_candle_config(strategies: Optional[dict]) -> dict:
    """Aceita tanto o formato antigo quanto o novo formato aninhado."""
    strategies = strategies or {}
    legacy_pat = bool(strategies.get('pat', True))
    legacy_enabled = strategies.get('candles_enabled', legacy_pat)
    cfg = strategies.get('candles', {}) if isinstance(strategies.get('candles'), dict) else {}

    enabled = bool(cfg.get('enabled', legacy_enabled))
    classic_enabled = bool(cfg.get('classic_enabled', cfg.get('classicos', True)))
    advanced_enabled = bool(cfg.get('advanced_enabled', cfg.get('avancados', True)))
    min_score = int(cfg.get('min_score', 7))
    strict_ema = bool(cfg.get('strict_ema_alignment', True))
    require_context = bool(cfg.get('require_context', True))

    classic = cfg.get('classic_patterns', cfg.get('classicos_ativos', DEFAULT_CLASSIC_PATTERNS))
    advanced = cfg.get('advanced_patterns', cfg.get('avancados_ativos', DEFAULT_ADVANCED_PATTERNS))
    if not isinstance(classic, list):
        classic = DEFAULT_CLASSIC_PATTERNS
    if not isinstance(advanced, list):
        advanced = DEFAULT_ADVANCED_PATTERNS

    return {
        'enabled': enabled,
        'classic_enabled': classic_enabled,
        'advanced_enabled': advanced_enabled,
        'classic_patterns': list(dict.fromkeys(classic)),
        'advanced_patterns': list(dict.fromkeys(advanced)),
        'min_score': max(4, min(20, min_score)),
        'strict_ema_alignment': strict_ema,
        'require_context': require_context,
    }


def _ohlc(opens, highs, lows, closes):
    o1, h1, l1, c1 = map(float, (opens[-1], highs[-1], lows[-1], closes[-1]))
    o2, h2, l2, c2 = map(float, (opens[-2], highs[-2], lows[-2], closes[-2]))
    o3, h3, l3, c3 = map(float, (opens[-3], highs[-3], lows[-3], closes[-3]))
    return (o1, h1, l1, c1), (o2, h2, l2, c2), (o3, h3, l3, c3)


def _body(o, c):
    return abs(c - o)


def _rng(h, l):
    return max(h - l, 1e-9)


def _upper_wick(o, h, c):
    return h - max(o, c)


def _lower_wick(o, l, c):
    return min(o, c) - l


def _ema_flags(e5: float, e50: float):
    return e5 > e50, e5 < e50


def detect_classic_patterns(opens, highs, lows, closes, ema5_last: float, ema50_last: float) -> Dict[str, CandlePattern]:
    if len(closes) < 3:
        return {}
    (o1, h1, l1, c1), (o2, h2, l2, c2), (o3, h3, l3, c3) = _ohlc(opens, highs, lows, closes)
    bull1, bear1 = c1 > o1, c1 < o1
    bull2, bear2 = c2 > o2, c2 < o2
    bull3, bear3 = c3 > o3, c3 < o3
    body1, body2, body3 = _body(o1, c1), _body(o2, c2), _body(o3, c3)
    rng1, rng2, rng3 = _rng(h1, l1), _rng(h2, l2), _rng(h3, l3)
    uw1, lw1 = _upper_wick(o1, h1, c1), _lower_wick(o1, l1, c1)
    ema_up, ema_dn = _ema_flags(ema5_last, ema50_last)
    res: Dict[str, CandlePattern] = {}

    def add(key, direction, desc):
        res[key] = CandlePattern(key, direction, PATTERN_WEIGHTS.get(key, 6), 'classic', desc)

    # Engulfing
    if bear2 and bull1 and c1 >= o2 and o1 <= c2 and body1 > body2 * 0.8 and ema_up:
        add('engolfo_alta', 'CALL', 'Engolfo comprador alinhado nas EMAs')
    if bull2 and bear1 and c1 <= o2 and o1 >= c2 and body1 > body2 * 0.8 and ema_dn:
        add('engolfo_baixa', 'PUT', 'Engolfo vendedor alinhado nas EMAs')

    # Morning / Evening Star
    body2_ratio = body2 / rng2
    if bear3 and body3 > rng3 * 0.55 and body2_ratio < 0.35 and bull1 and body1 > rng1 * 0.45 and c1 > (o3 + c3) / 2 and ema_up:
        add('morning_star', 'CALL', 'Morning Star com confirmação de alta')
    if bull3 and body3 > rng3 * 0.55 and body2_ratio < 0.35 and bear1 and body1 > rng1 * 0.45 and c1 < (o3 + c3) / 2 and ema_dn:
        add('evening_star', 'PUT', 'Evening Star com confirmação de baixa')

    # Hammer / shooting star / pinbar
    if lw1 >= 2.0 * max(body1, 1e-9) and uw1 <= max(body1, 1e-9) * 0.45 and body1 / rng1 >= 0.12 and bear2 and ema_up:
        add('martelo', 'CALL', 'Martelo em contexto de recuperação')
    if uw1 >= 2.0 * max(body1, 1e-9) and lw1 <= max(body1, 1e-9) * 0.45 and body1 / rng1 >= 0.12 and bull2 and ema_dn:
        add('estrela_cadente', 'PUT', 'Estrela cadente em contexto de enfraquecimento')
    if lw1 > 2.5 * max(body1, 1e-9) and uw1 < max(body1, 1e-9) * 1.2 and body1 / rng1 >= 0.06 and ema_up and 'martelo' not in res:
        add('pinbar_alta', 'CALL', 'Pinbar de alta com rejeição de fundo')
    if uw1 > 2.5 * max(body1, 1e-9) and lw1 < max(body1, 1e-9) * 1.2 and body1 / rng1 >= 0.06 and ema_dn and 'estrela_cadente' not in res:
        add('pinbar_baixa', 'PUT', 'Pinbar de baixa com rejeição de topo')

    # Tweezer / soldiers / crows
    if abs(l1 - l2) / (abs(l2) + 1e-9) < 0.00015 and bear2 and bull1 and body1 > rng1 * 0.28 and ema_up:
        add('tweezer_bottom', 'CALL', 'Tweezer Bottom em suporte curto')
    if abs(h1 - h2) / (abs(h2) + 1e-9) < 0.00015 and bull2 and bear1 and body1 > rng1 * 0.28 and ema_dn:
        add('tweezer_top', 'PUT', 'Tweezer Top em resistência curta')
    if bull1 and bull2 and bull3 and c1 > c2 > c3 and body1 > rng1 * 0.45 and body2 > rng2 * 0.45 and ema_up:
        add('tres_soldados', 'CALL', 'Três soldados com progressão limpa')
    if bear1 and bear2 and bear3 and c1 < c2 < c3 and body1 > rng1 * 0.45 and body2 > rng2 * 0.45 and ema_dn:
        add('tres_corvos', 'PUT', 'Três corvos com progressão limpa')

    # Harami / 3 inside / 3 outside / kicker
    if bear2 and bull1 and body1 < body2 * 0.65 and o1 > min(o2, c2) and c1 < max(o2, c2) and ema_up:
        add('harami_alta', 'CALL', 'Harami de alta')
    if bull2 and bear1 and body1 < body2 * 0.65 and o1 < max(o2, c2) and c1 > min(o2, c2) and ema_dn:
        add('harami_baixa', 'PUT', 'Harami de baixa')
    if bear3 and bull2 and bull1 and c1 > max(o3, c3):
        add('three_inside_up', 'CALL', 'Three Inside Up')
    if bull3 and bear2 and bear1 and c1 < min(o3, c3):
        add('three_inside_down', 'PUT', 'Three Inside Down')
    if bear3 and bull2 and bull1 and o2 <= min(o3, c3) and c2 >= max(o3, c3) and c1 > c2:
        add('three_outside_up', 'CALL', 'Three Outside Up')
    if bull3 and bear2 and bear1 and o2 >= max(o3, c3) and c2 <= min(o3, c3) and c1 < c2:
        add('three_outside_down', 'PUT', 'Three Outside Down')
    if bear2 and bull1 and o1 > max(o2, c2) and body1 > body2 * 0.7:
        add('kicker_alta', 'CALL', 'Kicker de alta')
    if bull2 and bear1 and o1 < min(o2, c2) and body1 > body2 * 0.7:
        add('kicker_baixa', 'PUT', 'Kicker de baixa')

    return res


def detect_advanced_patterns(opens, highs, lows, closes, ema5_last: float, ema50_last: float) -> Dict[str, CandlePattern]:
    if len(closes) < 4:
        return {}
    (o1, h1, l1, c1), (o2, h2, l2, c2), (o3, h3, l3, c3) = _ohlc(opens, highs, lows, closes)
    ema_up, ema_dn = _ema_flags(ema5_last, ema50_last)
    body1, body2 = _body(o1, c1), _body(o2, c2)
    rng1, rng2 = _rng(h1, l1), _rng(h2, l2)
    uw1, lw1 = _upper_wick(o1, h1, c1), _lower_wick(o1, l1, c1)
    res: Dict[str, CandlePattern] = {}

    def add(key, direction, desc):
        res[key] = CandlePattern(key, direction, PATTERN_WEIGHTS.get(key, 6), 'advanced', desc)

    # marubozu / breakout body
    if c1 > o1 and body1 / rng1 > 0.82 and uw1 / rng1 < 0.05 and lw1 / rng1 < 0.08 and ema_up:
        add('marubozu_alta', 'CALL', 'Marubozu comprador')
    if c1 < o1 and body1 / rng1 > 0.82 and uw1 / rng1 < 0.08 and lw1 / rng1 < 0.05 and ema_dn:
        add('marubozu_baixa', 'PUT', 'Marubozu vendedor')
    recent_high = float(np.max(highs[-8:-1])) if len(highs) >= 8 else float(np.max(highs[:-1]))
    recent_low = float(np.min(lows[-8:-1])) if len(lows) >= 8 else float(np.min(lows[:-1]))
    if h1 > recent_high and c1 > recent_high and body1 / rng1 > 0.55 and ema_up:
        add('breakout_body_alta', 'CALL', 'Rompimento com corpo acima da máxima')
    if l1 < recent_low and c1 < recent_low and body1 / rng1 > 0.55 and ema_dn:
        add('breakout_body_baixa', 'PUT', 'Rompimento com corpo abaixo da mínima')

    # inside / outside break-reversal
    if h1 < h2 and l1 > l2 and c1 > o1 and c1 > (h2 + l2) / 2 and ema_up:
        add('inside_break_alta', 'CALL', 'Inside break comprador')
    if h1 < h2 and l1 > l2 and c1 < o1 and c1 < (h2 + l2) / 2 and ema_dn:
        add('inside_break_baixa', 'PUT', 'Inside break vendedor')
    if h1 > h2 and l1 < l2 and c1 > o1 and c2 < o2:
        add('outside_reversal_alta', 'CALL', 'Outside reversal altista')
    if h1 > h2 and l1 < l2 and c1 < o1 and c2 > o2:
        add('outside_reversal_baixa', 'PUT', 'Outside reversal bajista')

    # traps / micro pullback
    if h1 > recent_high and c1 < recent_high and uw1 > body1 * 1.4:
        add('trap_top', 'PUT', 'Trap Top com fechamento de volta')
    if l1 < recent_low and c1 > recent_low and lw1 > body1 * 1.4:
        add('trap_bottom', 'CALL', 'Trap Bottom com fechamento de volta')
    if ema_up and c2 > o2 and c1 > o1 and l1 > min(o2, c2) and body1 < body2 * 0.9:
        add('micro_pullback_alta', 'CALL', 'Micro pullback saudável na alta')
    if ema_dn and c2 < o2 and c1 < o1 and h1 < max(o2, c2) and body1 < body2 * 0.9:
        add('micro_pullback_baixa', 'PUT', 'Micro pullback saudável na baixa')

    return res




def _recent_direction_stats(opens, closes, lookback: int = 4):
    n = min(lookback, len(closes))
    bulls = 0
    bears = 0
    for i in range(-n, 0):
        if closes[i] > opens[i]:
            bulls += 1
        elif closes[i] < opens[i]:
            bears += 1
    return bulls, bears


def _classify_pattern(key: str) -> str:
    reversal = {
        'engolfo_alta','engolfo_baixa','morning_star','evening_star','martelo','estrela_cadente',
        'tweezer_bottom','tweezer_top','harami_alta','harami_baixa','three_inside_up','three_inside_down',
        'three_outside_up','three_outside_down','kicker_alta','kicker_baixa','outside_reversal_alta',
        'outside_reversal_baixa','trap_top','trap_bottom'
    }
    continuation = {
        'tres_soldados','tres_corvos','marubozu_alta','marubozu_baixa','breakout_body_alta','breakout_body_baixa',
        'inside_break_alta','inside_break_baixa','micro_pullback_alta','micro_pullback_baixa'
    }
    if key in reversal:
        return 'reversal'
    if key in continuation:
        return 'continuation'
    return 'neutral'


def evaluate_pattern_context(pattern_key: str, direction: str, opens, highs, lows, closes, ema5_last: float, ema50_last: float) -> dict:
    """Filtro genérico de contexto para TODOS os padrões.

    Não tenta adivinhar o padrão em si; ele só verifica se o contexto do mercado
    ajuda ou contradiz a direção escolhida.
    """
    if len(closes) < 5:
        return {'passed': False, 'score': 0, 'reasons': ['dados insuficientes']}

    score = 0
    reasons = []
    recent = 5
    recent_high = float(np.max(highs[-recent:]))
    recent_low = float(np.min(lows[-recent:]))
    curr_range = max(float(highs[-1] - lows[-1]), 1e-9)
    close_pos = (float(closes[-1]) - float(lows[-1])) / curr_range
    upper_wick = _upper_wick(float(opens[-1]), float(highs[-1]), float(closes[-1])) / curr_range
    lower_wick = _lower_wick(float(opens[-1]), float(lows[-1]), float(closes[-1])) / curr_range
    bulls, bears = _recent_direction_stats(opens[-5:-1], closes[-5:-1], lookback=min(4, len(closes)-1))
    kind = _classify_pattern(pattern_key)
    ema_up = ema5_last > ema50_last
    ema_dn = ema5_last < ema50_last

    if direction == 'CALL':
        if ema_up:
            score += 1; reasons.append('EMA alinhada alta')
        elif ema_dn:
            score -= 1; reasons.append('EMA contrária')
        if close_pos >= 0.58:
            score += 1; reasons.append('fechamento forte')
        elif close_pos <= 0.42:
            score -= 1; reasons.append('fechamento fraco')
        if upper_wick > 0.32:
            score -= 1; reasons.append('rejeição superior')
        if lower_wick > 0.20:
            score += 1; reasons.append('rejeição inferior')
    else:
        if ema_dn:
            score += 1; reasons.append('EMA alinhada baixa')
        elif ema_up:
            score -= 1; reasons.append('EMA contrária')
        if close_pos <= 0.42:
            score += 1; reasons.append('fechamento forte')
        elif close_pos >= 0.58:
            score -= 1; reasons.append('fechamento fraco')
        if lower_wick > 0.32:
            score -= 1; reasons.append('rejeição inferior')
        if upper_wick > 0.20:
            score += 1; reasons.append('rejeição superior')

    # Contexto específico por classe de padrão
    if kind == 'reversal':
        if direction == 'CALL':
            if bears >= 2:
                score += 1; reasons.append('contexto prévio de queda')
            else:
                score -= 1; reasons.append('sem queda prévia')
        else:
            if bulls >= 2:
                score += 1; reasons.append('contexto prévio de alta')
            else:
                score -= 1; reasons.append('sem alta prévia')
    elif kind == 'continuation':
        if direction == 'CALL':
            if ema_up and bulls >= 2:
                score += 2; reasons.append('continuação alinhada')
            else:
                score -= 1; reasons.append('continuação sem alinhamento')
        else:
            if ema_dn and bears >= 2:
                score += 2; reasons.append('continuação alinhada')
            else:
                score -= 1; reasons.append('continuação sem alinhamento')

    # Evitar entradas no extremo oposto do range curtíssimo
    if (recent_high - recent_low) / (abs(float(closes[-1])) + 1e-9) < 0.00035 and kind != 'continuation':
        score -= 1
        reasons.append('range curto/mercado preso')

    passed = score >= 2
    return {'passed': passed, 'score': score, 'reasons': reasons[:6], 'kind': kind}

def analyze_candle_engine(opens, highs, lows, closes, ema5_last: float, ema50_last: float, candle_cfg: Optional[dict] = None) -> dict:
    candle_cfg = normalize_candle_config(candle_cfg or {}) if ('candles' in (candle_cfg or {}) or 'pat' in (candle_cfg or {}) or 'candles_enabled' in (candle_cfg or {})) else {
        'enabled': True,
        'classic_enabled': True,
        'advanced_enabled': True,
        'classic_patterns': DEFAULT_CLASSIC_PATTERNS,
        'advanced_patterns': DEFAULT_ADVANCED_PATTERNS,
        'min_score': 7,
        'strict_ema_alignment': True,
        'require_context': True,
    }

    if not candle_cfg['enabled']:
        return {
            'enabled': False,
            'direction': None,
            'score_call': 0,
            'score_put': 0,
            'strength': 0,
            'selected_pattern': None,
            'patterns': [],
            'classic_patterns': [],
            'advanced_patterns': [],
            'summary': 'Candles desativados',
        }

    classic = detect_classic_patterns(opens, highs, lows, closes, ema5_last, ema50_last) if candle_cfg['classic_enabled'] else {}
    advanced = detect_advanced_patterns(opens, highs, lows, closes, ema5_last, ema50_last) if candle_cfg['advanced_enabled'] else {}

    allowed_classic = set(candle_cfg['classic_patterns'])
    allowed_advanced = set(candle_cfg['advanced_patterns'])

    selected: List[CandlePattern] = []
    for key, pat in classic.items():
        if key in allowed_classic:
            selected.append(pat)
    for key, pat in advanced.items():
        if key in allowed_advanced:
            selected.append(pat)

    score_call = sum(p.weight for p in selected if p.direction == 'CALL')
    score_put = sum(p.weight for p in selected if p.direction == 'PUT')
    top = max(selected, key=lambda p: p.weight, default=None)
    total = score_call + score_put
    if total == 0:
        direction = None
        strength = 0
    elif score_call > score_put:
        direction = 'CALL'
        strength = int(min(95, 50 + (score_call - score_put) * 4 + min(score_call, 12)))
    elif score_put > score_call:
        direction = 'PUT'
        strength = int(min(95, 50 + (score_put - score_call) * 4 + min(score_put, 12)))
    else:
        direction = None
        strength = 0

    selected_pattern = PATTERN_LABELS.get(top.key, top.key) if top else None
    context = evaluate_pattern_context(
        top.key if top else '',
        direction or '',
        opens, highs, lows, closes, ema5_last, ema50_last
    ) if top and direction and candle_cfg.get('require_context', True) else {'passed': True, 'score': 0, 'reasons': [], 'kind': 'neutral'}
    if top and direction and candle_cfg.get('strict_ema_alignment', True):
        if direction == 'CALL' and ema5_last < ema50_last and context.get('kind') != 'reversal':
            context = {'passed': False, 'score': -2, 'reasons': ['EMA desalinhada para CALL'], 'kind': context.get('kind', 'neutral')}
        elif direction == 'PUT' and ema5_last > ema50_last and context.get('kind') != 'reversal':
            context = {'passed': False, 'score': -2, 'reasons': ['EMA desalinhada para PUT'], 'kind': context.get('kind', 'neutral')}

    if context.get('passed', True) and context.get('score', 0) > 0:
        strength = int(min(97, strength + context.get('score', 0) * 3))

    return {
        'enabled': True,
        'direction': direction if context.get('passed', True) else None,
        'score_call': score_call,
        'score_put': score_put,
        'strength': strength if context.get('passed', True) else 0,
        'selected_pattern': selected_pattern,
        'selected_pattern_key': top.key if top else None,
        'patterns': [PATTERN_LABELS.get(p.key, p.key) for p in selected],
        'classic_patterns': [PATTERN_LABELS.get(p.key, p.key) for p in selected if p.category == 'classic'],
        'advanced_patterns': [PATTERN_LABELS.get(p.key, p.key) for p in selected if p.category == 'advanced'],
        'summary': f"Candles: {(direction if context.get('passed', True) else 'BLOQUEADO') or 'NEUTRO'} | {selected_pattern or 'sem padrão'} | {strength if context.get('passed', True) else 0}%",
        'min_score_ok': max(score_call, score_put) >= candle_cfg['min_score'] and context.get('passed', True),
        'context_passed': context.get('passed', True),
        'context_score': context.get('score', 0),
        'context_reasons': context.get('reasons', []),
        'context_kind': context.get('kind', 'neutral'),
        'config': candle_cfg,
    }
