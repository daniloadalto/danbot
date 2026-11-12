"""
GS-ELITE - operacional proprio do Genspark (11/11), motor de CONFLUENCIA com pontuacao.

Diferenca em relacao aos setups binarios do strategy_v2: em vez de "disparou / nao disparou",
cada evidencia independente soma pontos e a entrada so acontece quando o total passa de um
LIMIAR calibrado nos dados reais. Fundamentos (pesquisa 11/11):
  - r/algotrading: o edge existe POR REGIME - mean reversion so no lateral, momentum so na tendencia.
    Um filtro de regime entra no score (a favor soma, contra desconta).
  - Mean reversion com holding curto consistentemente supera (ES Mean Reversion, algotr.substack).
  - RSI + price action melhora mean reversion (tese UCLouvain 2024); Bollinger+RSI quebra em tendencia
    (SSRN 2025) -> o filtro de regime corrige exatamente isso.
  - Validacao out-of-sample obrigatoria: o limiar e escolhido nos primeiros 2/3 do historico e
    VERIFICADO no terco final (nunca visto na calibracao).

DOIS MODOS (o regime decide qual esta ativo):
  REVERSAO  (mercado NAO explodiu: er baixo ou slope moderado)
      Evidencias (CALL - espelho para PUT):
        +3  z <= -2.8            (preco muito longe da media de 20)
        +2  z <= -2.0
        +3  deslocamento 5 velas <= -3.0 ATR   +2 se <= -2.0 ATR
        +3  RSI(7) <= 18        +2 se <= 25
        +2  tocou/estourou a banda inferior de Bollinger (20, 2)
        +2  >= 4 velas vermelhas nas ultimas 5  (+1 se 3)
        +1  vela de confirmacao verde
        +1  corpo >= 50% da vela
        +1  fechou no terco superior (close_pos >= 0.65)
        +1  pavio de rejeicao inferior >= 35% da vela
        +1  volatilidade normal (0.5 <= atr_ratio <= 2.5)
        -2  tendência forte contra (slope < -1.5) -> evita facada
        -1  movimento explosivo (atr_ratio > 3)
      ENTRA com score >= GS_MIN_REVERSAL (calibrado, padrao 8).
  MOMENTUM  (tendencia clara: EMA 21>55>105 e slope forte)
      Evidencias (CALL - espelho para PUT):
        +2  alinhamento de EMAs
        +2  recuo tocou a EMA 21 (<= 0.3 ATR) e fechou verde acima dela
        +1  slope > 0.5
        +1  er > 0.35 (mercado eficiente na direcao)
        +1  RSI(7) entre 52 e 78
        +1  corpo >= 50% e fechou no terco superior
        +1  vela anterior pequena (pausa, nao exaustao: rng[-1] <= 1.6 ATR)
        -2  z >= +2.5 (entrada comprando topinho = perseguição)
      ENTRA com score >= GS_MIN_MOMENTUM (calibrado, padrao 5).

Gestao: expiracao padrao 1 min (holding curto, medido melhor nos dados da conta);
o backtest mede 1 a 5 min e o melhor horizonte por ativo e exposto no 'det'.

Variaveis de ambiente: GS_MIN_REVERSAL (8), GS_MIN_MOMENTUM (5).
"""
import os
import numpy as np

try:
    import strategy_v2 as SV2            # reusa features() (indicadores causais prontos)
    _F_SRC = 'sv2'
except Exception:                        # standalone: features minimalas proprias
    SV2 = None
    _F_SRC = 'local'

MIN_BARS = 130
GS_MIN_REVERSAL = int(os.environ.get('GS_MIN_REVERSAL') or 8)
GS_MIN_MOMENTUM = int(os.environ.get('GS_MIN_MOMENTUM') or 5)
# (12/11) modos que ENTRAM com dinheiro. Calibracao nos dados reais: REVERSAO 60-62% no out-of-sample;
# MOMENTUM 52% = abaixo do empate com payout 86% (53.8%) -> FORA das entradas por padrao.
# O catalogador continua medindo os dois (gs_eval passa modes explicito).
GS_MODES = tuple(m.strip().upper() for m in (os.environ.get('GS_MODES') or 'REVERSAO').split(',') if m.strip())


def _local_features(o, h, l, c):
    """Features minimas quando strategy_v2 nao esta disponivel (mesma semantica, causais)."""
    o, h, l, c = (np.asarray(x, dtype=float) for x in (o, h, l, c))
    n = len(c)
    def _ema(x, p):
        k = 2.0 / (p + 1)
        out = np.empty_like(x); out[0] = x[0]
        for i in range(1, len(x)):
            out[i] = x[i] * k + out[i - 1] * (1 - k)
        return out
    prev_c = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    atr = _ema(tr, 14)
    m20 = np.convolve(c, np.ones(20) / 20, mode='full')[:n]
    m20[:19] = np.nan
    sd20 = np.array([np.std(c[max(0, i - 19):i + 1]) if i >= 19 else np.nan for i in range(n)])
    d = np.diff(c, prepend=c[0])
    g = np.where(d > 0, d, 0.0); lo = np.where(d < 0, -d, 0.0)
    a = 1.0 / 7
    ag = np.zeros(n); al = np.zeros(n)
    for i in range(1, n):
        ag[i] = ag[i - 1] * (1 - a) + g[i] * a
        al[i] = al[i - 1] * (1 - a) + lo[i] * a
    rsi = 100.0 - 100.0 / (1.0 + ag / (al + 1e-12))
    slope = np.full(n, np.nan); slope[10:] = (np.abs(c[10:] - c[:-10])) / (atr[10:] + 1e-12) * np.sign(c[10:] - c[:-10])
    er = np.full(n, np.nan)
    if n > 30:
        cs = np.cumsum(np.abs(d))
        er[30:] = np.abs(c[30:] - c[:-30]) / (cs[30:] - cs[:-30] + 1e-12)
    return {'o': o, 'h': h, 'l': l, 'c': c, 'atr': atr, 'z': (c - m20) / (sd20 + 1e-18),
            'rsi': rsi, 'e21': _ema(c, 21), 'e55': _ema(c, 55), 'e105': _ema(c, 105),
            'slope': slope, 'er': er, 'bb_up': m20 + 2.0 * sd20, 'bb_lo': m20 - 2.0 * sd20,
            'rng': h - l, 'ap': np.concatenate([[atr[0]], atr[:-1]]), 'spike': np.zeros(n, dtype=bool)}


def features(o, h, l, c):
    return SV2.features(o, h, l, c) if SV2 is not None else _local_features(o, h, l, c)


def gs_signals(F, i, modes=None):
    """Lista de sinais (dir, score, det) da vela fechada i. Vazia se nao houver entrada.
    `modes`: quais modos podem disparar (padrao = GS_MODES; o catalogador passa os dois)."""
    allow = set(modes) if modes else set(GS_MODES)
    if i < MIN_BARS:
        return []
    c, o, h, l = F['c'], F['o'], F['h'], F['l']
    atr = F['atr'][i]
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(F['rsi'][i]):
        return []
    z_i = F['z'][i] if np.isfinite(F['z'][i]) else 0.0
    rsi_i = float(F['rsi'][i])
    er_i = F['er'][i] if np.isfinite(F['er'][i]) else 0.5
    slope_i = F['slope'][i] if np.isfinite(F['slope'][i]) else 0.0
    ar = F['atr_ratio'][i] if np.isfinite(F['atr_ratio'][i]) else 1.0
    rng_i = float(F['rng'][i])
    if rng_i < 0.25 * atr:                    # vela minuscula = ruido
        return []
    body_i = abs(float(c[i] - o[i]))
    close_pos = (float(c[i]) - float(l[i])) / (rng_i + 1e-12)
    lower_w = float(min(o[i], c[i]) - l[i]) / rng_i
    upper_w = float(h[i] - max(o[i], c[i])) / rng_i
    st5 = float(c[i] - c[i - 5]) / atr
    reds = int(np.sum(c[i - 4:i + 1] < o[i - 4:i + 1]))
    greens = 5 - reds
    out = []

    # ---------------- REVERSAO (contra esticamento) ----------------
    if 'REVERSAO' in allow and er_i < 0.75:    # regime: mercado nao explodiu (corrida em passo)
        s = 0
        s += 3 if z_i <= -2.8 else (2 if z_i <= -2.0 else 0)
        s += 3 if st5 <= -3.0 else (2 if st5 <= -2.0 else 0)
        s += 3 if rsi_i <= 18 else (2 if rsi_i <= 25 else 0)
        bb_lo = F['bb_lo'][i]
        if np.isfinite(bb_lo) and float(l[i]) <= float(bb_lo) + 0.1 * atr:
            s += 2
        s += 2 if reds >= 4 else (1 if reds >= 3 else 0)
        if c[i] > o[i]:                        # vela de confirmacao (as "evidencias de qualidade")
            s += 1
            s += 1 if body_i >= 0.5 * rng_i else 0
            s += 1 if close_pos >= 0.65 else 0
            s += 1 if lower_w >= 0.35 else 0
        # (11/11) calibracao com dados reais: sem vela de confirmacao a reversao cai pra 48% (o
        # append abaixo ja exige c[i] > o[i], entao a confirmacao e obrigatoria por construcao).
        s += 1 if 0.5 <= ar <= 2.5 else 0
        if slope_i < -1.5:                     # facada: tendencia fortecontra
            s -= 2
        if ar > 3:
            s -= 1
        if s >= GS_MIN_REVERSAL and c[i] > o[i]:
            out.append(('CALL', s, {'modo': 'REVERSAO', 'score': s, 'z': round(float(z_i), 2), 'rsi': round(rsi_i, 1),
                                    'esticou': round(-st5, 1), 'anula_abaixo_de': round(float(min(l[i - 4:i + 1])) - 0.1 * atr, 6)}))
        elif s >= GS_MIN_REVERSAL and c[i] < o[i]:
            # vela vermelha nao confirma reversao de baixa-esticada; mas score alto com vela vermelha
            # e o proprio esticamento acontecendo: NAO entra (sem confirmacao) - so PUT espelhado abaixo
            pass
        s2 = 0
        s2 += 3 if z_i >= 2.8 else (2 if z_i >= 2.0 else 0)
        s2 += 3 if st5 >= 3.0 else (2 if st5 >= 2.0 else 0)
        s2 += 3 if rsi_i >= 82 else (2 if rsi_i >= 75 else 0)
        bb_up = F['bb_up'][i]
        if np.isfinite(bb_up) and float(h[i]) >= float(bb_up) - 0.1 * atr:
            s2 += 2
        s2 += 2 if greens >= 4 else (1 if greens >= 3 else 0)
        if c[i] < o[i]:
            s2 += 1
            s2 += 1 if body_i >= 0.5 * rng_i else 0
            s2 += 1 if close_pos <= 0.35 else 0
            s2 += 1 if upper_w >= 0.35 else 0
        s2 += 1 if 0.5 <= ar <= 2.5 else 0      # espelho: o append exige c[i] < o[i] (confirmacao obrigatoria)
        if slope_i > 1.5:
            s2 -= 2
        if ar > 3:
            s2 -= 1
        if s2 >= GS_MIN_REVERSAL and c[i] < o[i]:
            out.append(('PUT', s2, {'modo': 'REVERSAO', 'score': s2, 'z': round(float(z_i), 2), 'rsi': round(rsi_i, 1),
                                    'esticou': round(st5, 1), 'anula_acima_de': round(float(max(h[i - 4:i + 1])) + 0.1 * atr, 6)}))

    # ---------------- MOMENTUM (continuacao com regime a favor) ----------------
    e21, e55, e105 = F['e21'][i], F['e55'][i], F['e105'][i]
    if 'MOMENTUM' in allow and all(np.isfinite(x) for x in (e21, e55, e105)):
        up = e21 > e55 > e105
        dn = e21 < e55 < e105
        sm = 0
        if up:
            sm += 2
            pull = float(l[i]) <= float(e21) + 0.3 * atr and c[i] > o[i] and c[i] >= e21
            if pull:
                sm += 2
            sm += 1 if slope_i > 0.5 else 0
            sm += 1 if er_i > 0.35 else 0
            sm += 1 if 52 <= rsi_i <= 78 else 0
            sm += 1 if (body_i >= 0.5 * rng_i and close_pos >= 0.65) else 0
            sm += 1 if float(F['rng'][i - 1]) <= 1.6 * F['ap'][i] else 0
            if z_i >= 2.5:                     # comprando topinho: perseguiçao
                sm -= 2
            if sm >= GS_MIN_MOMENTUM:
                out.append(('CALL', sm, {'modo': 'MOMENTUM', 'rsi': round(rsi_i, 1), 'score': sm,
                                         'pullback': bool(sm >= 5 and float(l[i]) <= float(e21) + 0.3 * atr)}))
        sput = 0
        if dn:
            sput += 2
            pull = float(h[i]) >= float(e21) - 0.3 * atr and c[i] < o[i] and c[i] <= e21
            if pull:
                sput += 2
            sput += 1 if slope_i < -0.5 else 0
            sput += 1 if er_i > 0.35 else 0
            sput += 1 if 22 <= rsi_i <= 48 else 0
            sput += 1 if (body_i >= 0.5 * rng_i and close_pos <= 0.35) else 0
            sput += 1 if float(F['rng'][i - 1]) <= 1.6 * F['ap'][i] else 0
            if z_i <= -2.5:
                sput -= 2
            if sput >= GS_MIN_MOMENTUM:
                out.append(('PUT', sput, {'modo': 'MOMENTUM', 'rsi': round(rsi_i, 1), 'score': sput,
                                          'pullback': bool(sput >= 5 and float(h[i]) >= float(e21) - 0.3 * atr)}))
    return out


def gs_eval(candles, eval_n=300, horizons=(1, 2, 3, 4, 5)):
    """Backtest do GS-ELITE sobre velas fechadas. Retorna por modo: n, wr por horizonte e score medio.
    Compativel com o formato que o catalogador do app consome."""
    if not candles or len(candles) < MIN_BARS + 20:
        return None
    F = features([x['open'] for x in candles], [x['max'] for x in candles],
                 [x['min'] for x in candles], [x['close'] for x in candles])
    c = F['c']; n = len(c)
    start = max(MIN_BARS, n - eval_n)
    modes = {}
    for i in range(start, n - max(horizons)):
        for d, s, det in gs_signals(F, i, modes=('REVERSAO', 'MOMENTUM')):   # catalogo mede os DOIS modos
            m = modes.setdefault(det['modo'], {'n': 0, 'by_h': {h: [0, 0] for h in horizons}, 'scores': []})
            m['n'] += 1
            m['scores'].append(s)
            for hh in horizons:
                if i + hh < n:
                    diff = float(c[i + hh] - c[i])
                    if abs(diff) > 1e-12:
                        win = (diff > 0) if d == 'CALL' else (diff < 0)
                        m['by_h'][hh][0 if win else 1] += 1
    res = {}
    for modo, m in modes.items():
        best_h, best_lb, best_wr = None, -1.0, 0.0
        for hh, (w, l) in m['by_h'].items():
            nn = w + l
            if nn >= 3:
                p = w / nn
                lb = (p + 1.96 ** 2 / (2 * nn) - 1.96 * np.sqrt(p * (1 - p) / nn + 1.96 ** 2 / (4 * nn * nn))) / (1 + 1.96 ** 2 / nn)
                if lb > best_lb:
                    best_h, best_lb, best_wr = hh, lb, p * 100
        res[modo] = {'n': m['n'],
                     'wr1m': round(m['by_h'][1][0] / max(1, sum(m['by_h'][1])) * 100, 1),
                     'wr2m': round(m['by_h'][2][0] / max(1, sum(m['by_h'][2])) * 100, 1),
                     'wr3m': round(m['by_h'][3][0] / max(1, sum(m['by_h'][3])) * 100, 1),
                     'best_h': best_h, 'best_wr': round(best_wr, 1),
                     'score_medio': round(float(np.mean(m['scores'])), 1) if m['scores'] else None}
    return {'n': sum(m['n'] for m in modes.values()), 'modes': res}
