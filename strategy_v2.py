"""
DANBOT strategy v2 - analise em M1, entrada so quando ha evidencia estatistica.

Ideia central
-------------
1. Dois setups complementares, escolhidos pelo regime do mercado (tendencia x lateral):
     TREND_PULLBACK   - mercado em tendencia clara; entra a favor dela apos um recuo curto.
     RANGE_REVERSION  - mercado lateral; entra contra um extremo (Bollinger + RSI) apos o candle de reversao.
2. A expiracao (1 a 5 min) NAO e chutada: cada sinal gera "operacoes virtuais" nos 5 horizontes e o
   bot mede, por ativo + setup + expiracao, quanto realmente acertou (Wilson lower bound).
   So entra com dinheiro (demo ou real) onde a amostra e grande o bastante E o limite inferior do
   acerto passa do minimo configurado. Se nada passa, ele fica de fora - isso e o comportamento correto
   quando nao existe vantagem.
3. Tudo usa velas FECHADAS (sem repintar) e nenhuma informacao do futuro entra no calculo.

Variaveis de ambiente (todas opcionais)
  STRATEGY_GATE   on (padrao) | off   -> 'off' opera o setup na expiracao padrao sem exigir validacao
  GATE_MIN_N      amostra minima por (ativo, setup, expiracao)   [20]
  GATE_HALF_LIFE  meia-vida (em sinais) do peso dos resultados   [40]
  GATE_MIN_LB     limite inferior Wilson 95% minimo de acerto    [0.55]
  STATS_FILE      caminho do arquivo de estatisticas
"""
import os
import json
import math
import time
import threading
import numpy as np

HORIZONS = (1, 2, 3, 4, 5)
SETUPS = ('TREND_PULLBACK', 'RANGE_REVERSION', 'BREAKOUT_PULLBACK', 'SPIKE_REVERSAL')
DEFAULT_H = {'TREND_PULLBACK': 3, 'RANGE_REVERSION': 2, 'BREAKOUT_PULLBACK': 3, 'SPIKE_REVERSAL': 2}
QUARANTINE_CANDLES = 5   # apos uma anomalia de preco, os setups normais ficam bloqueados por N velas
MIN_BARS = 130          # velas de aquecimento (EMA 105 precisa estabilizar)
RECENT_KEEP = 100
HALF_LIFE = float(os.environ.get('GATE_HALF_LIFE') or 40)   # peso dos sinais cai pela metade a cada N sinais mais antigos
QUICK_N = 10             # parada rapida: ultimos N sinais virtuais abaixo de 50% suspendem a combinacao

GATE = (os.environ.get('STRATEGY_GATE') or 'off').strip().lower()   # filtro estatistico eliminado por padrao (STRATEGY_GATE=on religa)
MIN_N = int(os.environ.get('GATE_MIN_N') or 20)
MIN_LB = float(os.environ.get('GATE_MIN_LB') or 0.55)


# ----------------------------------------------------------------- indicadores (causais)
def _ema(x, n):
    x = np.asarray(x, dtype=float)
    out = np.empty_like(x)
    k = 2.0 / (n + 1)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi(c, n=7):
    c = np.asarray(c, dtype=float)
    d = np.diff(c, prepend=c[0])
    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)
    a = 1.0 / n
    ag = np.zeros_like(c)
    al = np.zeros_like(c)
    for i in range(1, len(c)):
        ag[i] = ag[i - 1] * (1 - a) + g[i] * a
        al[i] = al[i - 1] * (1 - a) + l[i] * a
    r = 100.0 - 100.0 / (1.0 + ag / (al + 1e-12))
    r[(ag + al) < 1e-12] = 50.0
    return r


def _rolling_mean(x, n):
    cs = np.cumsum(np.insert(x, 0, 0.0))
    out = np.full(len(x), np.nan)
    out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def features(o, h, l, c):
    """Calcula todos os indicadores de uma vez, para a serie inteira (cada ponto usa so o passado)."""
    o, h, l, c = (np.asarray(v, dtype=float) for v in (o, h, l, c))
    n = len(c)
    prev_c = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    atr = _ema(tr, 14)
    atr_avg = _rolling_mean(atr, 100)
    absd = np.abs(np.diff(c, prepend=c[0]))
    cs = np.cumsum(absd)
    er = np.full(n, np.nan)
    if n > 30:
        er[30:] = np.abs(c[30:] - c[:-30]) / (cs[30:] - cs[:-30] + 1e-12)
    m20 = _rolling_mean(c, 20)
    m20_2 = _rolling_mean(c * c, 20)
    sd20 = np.sqrt(np.maximum(m20_2 - m20 * m20, 0.0) + 1e-18)
    z = (c - m20) / sd20
    e21, e55, e105 = _ema(c, 21), _ema(c, 55), _ema(c, 105)
    slope = np.full(n, np.nan)
    slope[10:] = (e55[10:] - e55[:-10]) / (atr[10:] + 1e-12)
    # pivos (fractal de 2 velas): so podem ser usados quando ja confirmados (2 velas depois)
    ph = np.zeros(n, dtype=bool)
    pl = np.zeros(n, dtype=bool)
    for k in range(2, n - 2):
        ph[k] = h[k] > h[k - 1] and h[k] > h[k - 2] and h[k] >= h[k + 1] and h[k] >= h[k + 2]
        pl[k] = l[k] < l[k - 1] and l[k] < l[k - 2] and l[k] <= l[k + 1] and l[k] <= l[k + 2]
    # anomalias de preco (comuns em OTC): usam o ATR da vela ANTERIOR para nao se contaminar
    rng = h - l
    ap = np.concatenate([[atr[0]], atr[:-1]]) + 1e-12
    prev_close = np.concatenate([[c[0]], c[:-1]])
    gap = np.abs(o - prev_close) > 0.6 * ap
    upper_w = (h - np.maximum(o, c)) / (rng + 1e-12)
    lower_w = (np.minimum(o, c) - l) / (rng + 1e-12)
    # pico = candle grande COM pavio longo (stop-hunt). Candle grande e cheio de corpo e um impulso/rompimento, nao anomalia.
    spike = (rng > 2.8 * ap) & (np.maximum(upper_w, lower_w) >= 0.45)
    return {
        'o': o, 'h': h, 'l': l, 'c': c, 'atr': atr, 'atr_ratio': atr / (atr_avg + 1e-12),
        'er': er, 'z': z, 'rsi': _rsi(c, 7), 'e21': e21, 'e55': e55, 'e105': e105, 'slope': slope,
        'ph': ph, 'pl': pl, 'spike': spike, 'gap': gap, 'anom': spike | gap, 'upper_w': upper_w, 'lower_w': lower_w,
        'rng': rng, 'ap': ap, 'ph_idx': np.flatnonzero(ph), 'pl_idx': np.flatnonzero(pl),
    }


def manip_reason(F, i):
    """Texto da anomalia de preco nas ultimas velas (ou None). Mostra o que disparou a quarentena."""
    lo = max(0, i - QUARANTINE_CANDLES + 1)
    for k in range(i, lo - 1, -1):
        if F['spike'][k]:
            side = 'pavio superior' if F['upper_w'][k] > F['lower_w'][k] else 'pavio inferior'
            return f"pico de preco ({F['rng'][k] / F['ap'][k]:.1f}x o ATR, {side}) ha {i - k} vela(s)"
        if F['gap'][k]:
            return f"gap entre velas ({abs(F['o'][k] - F['c'][k - 1]) / F['ap'][k]:.1f}x o ATR) ha {i - k} vela(s)"
    return None


def _breakout_pullback(F, i, atr, det):
    """Rompimento de LTA/LTB ou de suporte/resistencia horizontal, seguido de pullback que respeita a linha.
    A linha e tracada SO com pivos anteriores ao rompimento (senao o topo/fundo do proprio rompimento a contamina)."""
    h, l, c, o = F['h'], F['l'], F['c'], F['o']
    ph_idx, pl_idx = F['ph_idx'], F['pl_idx']

    def last2(idx, j):
        pos = int(np.searchsorted(idx, j - 3, side='right'))     # pivos confirmados antes da vela de rompimento
        if pos < 2:
            return None
        k1, k2 = int(idx[pos - 2]), int(idx[pos - 1])
        return (k1, k2) if k1 >= j - 120 else None

    for j in range(i - 2, max(i - 9, 4), -1):                    # rompimento 2 a 8 velas atras
        aj = F['atr'][j]
        lines = []   # (tipo, k1, valor_base, inclinacao, direcao_do_rompimento, k2)
        pair = last2(ph_idx, j)
        if pair and pair[1] - pair[0] >= 6:
            k1, k2 = pair
            p1, p2 = h[k1], h[k2]
            if p2 < p1 - 0.2 * aj:
                lines.append(('LTB', k1, p1, (p2 - p1) / (k2 - k1), 'UP', k2))
            elif abs(p2 - p1) <= 0.35 * aj:
                lines.append(('RESISTENCIA', k1, (p1 + p2) / 2, 0.0, 'UP', k2))
        pair = last2(pl_idx, j)
        if pair and pair[1] - pair[0] >= 6:
            k1, k2 = pair
            p1, p2 = l[k1], l[k2]
            if p2 > p1 + 0.2 * aj:
                lines.append(('LTA', k1, p1, (p2 - p1) / (k2 - k1), 'DOWN', k2))
            elif abs(p2 - p1) <= 0.35 * aj:
                lines.append(('SUPORTE', k1, (p1 + p2) / 2, 0.0, 'DOWN', k2))
        for tipo, k1, base, slope, brk, k2 in lines:
            if abs(slope) * 10 > 1.5 * aj:       # linha muito inclinada = pouco confiavel
                continue
            v = lambda t: base + slope * (t - k1)
            if brk == 'UP':
                broke = c[j] > v(j) + 0.25 * aj and c[j - 1] <= v(j - 1) + 0.05 * aj
            else:
                broke = c[j] < v(j) - 0.25 * aj and c[j - 1] >= v(j - 1) - 0.05 * aj
            if not broke:
                continue
            held = all((c[t] >= v(t) - 0.15 * aj) if brk == 'UP' else (c[t] <= v(t) + 0.15 * aj)
                       for t in range(j + 1, i + 1))
            if not held:
                continue
            if brk == 'UP' and l[i] <= v(i) + 0.2 * atr and c[i] > o[i] and c[i] >= v(i):
                return 'BREAKOUT_PULLBACK', 'CALL', {**det, 'tipo': tipo, 'rompeu_ha': i - j}
            if brk == 'DOWN' and h[i] >= v(i) - 0.2 * atr and c[i] < o[i] and c[i] <= v(i):
                return 'BREAKOUT_PULLBACK', 'PUT', {**det, 'tipo': tipo, 'rompeu_ha': i - j}
    return None


# Sensibilidade dos setups. 'solto' (padrao) gera bem mais sinais; 'estrito' e o ajuste original.
_STRICT = dict(ar_lo=0.5, ar_hi=2.2, tp_er=0.28, tp_slope=0.25, tp_rsi_dip=38, tp_rsi_back=42, tp_z=1.8,
               rr_er=0.25, rr_slope=0.35, rr_z=2.0, rr_rsi=25)
_LOOSE = dict(ar_lo=0.35, ar_hi=2.6, tp_er=0.18, tp_slope=0.12, tp_rsi_dip=45, tp_rsi_back=46, tp_z=2.2,
              rr_er=0.32, rr_slope=0.5, rr_z=1.4, rr_rsi=33)
SENS = (os.environ.get('STRAT_SENS') or 'solto').strip().lower()
P = _STRICT if SENS == 'estrito' else _LOOSE

def signals_at(F, i):
    """Lista de sinais (setup, 'CALL'|'PUT', detalhes) da vela fechada i. O motor escolhe qual usar."""
    if i < MIN_BARS:
        return []
    c, o, h, l = F['c'], F['o'], F['h'], F['l']
    atr, ar = F['atr'][i], F['atr_ratio'][i]
    if not np.isfinite(ar) or not np.isfinite(F['er'][i]) or not np.isfinite(F['slope'][i]):
        return []
    er, slope, z, rsi = F['er'][i], F['slope'][i], F['z'], F['rsi']
    det = {'er': round(float(er), 2), 'slope': round(float(slope), 2), 'rsi': round(float(rsi[i]), 1),
           'z': round(float(z[i]), 2), 'atr_ratio': round(float(ar), 2)}
    out = []

    # ---- SPIKE_REVERSAL: aproveita picos (stop-hunt): pavio longo + fechamento longe da ponta -> entra contra a ponta ----
    if F['spike'][i] and F['rng'][i] <= 9 * F['ap'][i]:
        if F['upper_w'][i] >= 0.55 and (c[i] - l[i]) / (F['rng'][i] + 1e-12) <= 0.35:
            out.append(('SPIKE_REVERSAL', 'PUT', {**det, 'pavio': round(float(F['upper_w'][i]), 2)}))
        elif F['lower_w'][i] >= 0.55 and (h[i] - c[i]) / (F['rng'][i] + 1e-12) <= 0.35:
            out.append(('SPIKE_REVERSAL', 'CALL', {**det, 'pavio': round(float(F['lower_w'][i]), 2)}))

    # ---- protecao: com anomalia de preco recente os setups normais ficam de fora ----
    why = manip_reason(F, i)
    if why:
        for x in out:
            x[2]['manip'] = why
        return out
    if ar < P['ar_lo'] or ar > P['ar_hi']:      # mercado parado ou volatilidade explosiva
        return out

    bp = _breakout_pullback(F, i, atr, det)
    if bp:
        out.append(bp)

    # ---- tendencia: EMA 21 > 55 > 105 (equivale a M5 9/21), inclinacao e eficiencia altas ----
    if er > P['tp_er']:
        up = F['e21'][i] > F['e55'][i] > F['e105'][i] and slope > P['tp_slope']
        dn = F['e21'][i] < F['e55'][i] < F['e105'][i] and slope < -P['tp_slope']
        if up and rsi[i - 4:i].min() < P['tp_rsi_dip'] and rsi[i] > rsi[i - 1] and rsi[i] > P['tp_rsi_back'] and c[i] > o[i] and z[i] < P['tp_z']:
            out.append(('TREND_PULLBACK', 'CALL', det))
        elif dn and rsi[i - 4:i].max() > 100 - P['tp_rsi_dip'] and rsi[i] < rsi[i - 1] and rsi[i] < 100 - P['tp_rsi_back'] and c[i] < o[i] and z[i] > -P['tp_z']:
            out.append(('TREND_PULLBACK', 'PUT', det))
    # ---- lateral: eficiencia baixa e media sem inclinacao; reversao no extremo ----
    elif er < P['rr_er'] and abs(slope) < P['rr_slope']:
        if (z[i - 1] <= -P['rr_z'] or z[i] <= -P['rr_z']) and rsi[i - 1:i + 1].min() < P['rr_rsi'] and c[i] > o[i] and c[i] > c[i - 1]:
            out.append(('RANGE_REVERSION', 'CALL', det))
        elif (z[i - 1] >= P['rr_z'] or z[i] >= P['rr_z']) and rsi[i - 1:i + 1].max() > 100 - P['rr_rsi'] and c[i] < o[i] and c[i] < c[i - 1]:
            out.append(('RANGE_REVERSION', 'PUT', det))
    return out


def signal_at(F, i):
    """Compatibilidade: primeiro sinal da vela (ou None)."""
    r = signals_at(F, i)
    return r[0] if r else None


def _is_otc_name(a):
    return str(a).upper().endswith('-OTC')


# ----------------------------------------------------------------- estatistica
def wilson_lb(w, n, z=1.96):
    if n <= 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - m) / d


def eff_wn(r):
    """(vitorias, amostra) efetivas: resultados recentes pesam mais (decaimento exponencial por idade).
    Se a combinacao tem mais sinais que a janela guardada, o excedente antigo entra com peso baixo e fixo."""
    rec = r.get('recent') or []
    n_tot = r['w'] + r['l']
    if not rec or n_tot == 0:
        return float(r['w']), float(n_tot)
    k = len(rec)
    wsum = nsum = 0.0
    for age, v in enumerate(reversed(rec)):
        wt = 0.5 ** (age / HALF_LIFE)
        wsum += wt * v
        nsum += wt
    extra = n_tot - k
    if extra > 0:                      # sinais mais velhos que a janela: entram com o peso do ultimo elemento
        wt = 0.5 ** (k / HALF_LIFE)
        old_w = max(0, r['w'] - sum(rec))
        wsum += wt * old_w
        nsum += wt * extra
    return wsum, nsum


def _eligible(r):
    """(aprovado?, motivo_se_nao). Alem da amostra e do limite inferior, suspende celulas que pioraram."""
    n = r['w'] + r['l']
    if n < MIN_N:
        return False, 'aprendendo'
    we, ne = eff_wn(r)
    if wilson_lb(we, ne) < MIN_LB:
        return False, 'reprovado'
    rec = r.get('recent', [])
    if len(rec) >= QUICK_N and sum(rec[-QUICK_N:]) / QUICK_N < 0.5:
        return False, f'suspenso (ultimos {QUICK_N} sinais abaixo de 50%)'
    if len(rec) >= 20 and sum(rec[-20:]) / 20 < 0.5:
        return False, 'suspenso (ultimos 20 sinais abaixo de 50%)'
    rn = r.get('rw', 0) + r.get('rl', 0)
    if rn >= 10 and r.get('rw', 0) / rn < 0.45:
        return False, 'suspenso (ordens reais abaixo de 45%)'
    return True, ''


def _default_path():
    p = os.environ.get('STATS_FILE')
    if p:
        return p
    return '/data/strategy_stats.json' if os.path.isdir('/data') else 'strategy_stats.json'


def catalog_eval(candles, eval_n=300):
    """Catalogador: roda os 4 setups nas ultimas `eval_n` velas FECHADAS de um ativo e mede quantos sinais acertaram
    (entrada no fechamento da vela do sinal, resultado na expiracao padrao de cada setup). Nao mexe nas estatisticas do motor."""
    if not candles or len(candles) < MIN_BARS + 20:
        return None
    F = features([x['open'] for x in candles], [x['max'] for x in candles],
                 [x['min'] for x in candles], [x['close'] for x in candles])
    c = F['c']
    n = len(c)
    start = max(MIN_BARS, n - eval_n)
    w = l = t = 0
    per = {}
    by_h = {h: [0, 0] for h in HORIZONS}
    for i in range(start, n - 1):
        for setup, d, _det in signals_at(F, i):
            hd = DEFAULT_H[setup]
            if i + hd < n:
                diff = float(c[i + hd] - c[i])
                if abs(diff) < 1e-12:
                    t += 1
                else:
                    win = diff > 0 if d == 'CALL' else diff < 0
                    p = per.setdefault(setup, [0, 0])
                    p[0 if win else 1] += 1
                    if win:
                        w += 1
                    else:
                        l += 1
            for h in HORIZONS:
                if i + h < n:
                    dh = float(c[i + h] - c[i])
                    if abs(dh) >= 1e-12:
                        by_h[h][0 if ((dh > 0) == (d == 'CALL')) else 1] += 1
    nn = w + l
    best_h, best_lb = None, -1.0
    for h, (hw, hl) in by_h.items():
        if hw + hl >= 3:
            lbh = wilson_lb(hw, hw + hl)
            if lbh > best_lb:
                best_h, best_lb = h, lbh
    return {'n': nn, 'w': w, 'l': l, 't': t,
            'wr': round(w / nn * 100, 1) if nn else 0.0,
            'lb': round(wilson_lb(w, nn) * 100, 1) if nn else 0.0,
            'per': {k: {'w': v[0], 'l': v[1]} for k, v in per.items()},
            'best_h': best_h,
            'best_h_wr': round(by_h[best_h][0] / (by_h[best_h][0] + by_h[best_h][1]) * 100, 1) if best_h else None,
            'evaluated': n - start, 'candles': n}


class Engine:
    """Guarda estatisticas das operacoes virtuais e decide se um sinal pode virar ordem real."""

    def __init__(self, path=None):
        self.path = path or _default_path()
        self.lock = threading.RLock()
        self.stats = {}     # "ativo|setup|h" -> {'w','l','t','recent':[],'rw','rl'}
        self.assets = {}    # ativo -> {'processed_ts', 'pending':[...]}
        self._last_save = 0
        self._load()

    # --- persistencia ---
    def _load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                d = json.load(f)
            self.stats = d.get('stats', {})
            self.assets = d.get('assets', {})
        except Exception:
            pass

    def save(self, force=False):
        if not force and time.time() - self._last_save < 60:
            return
        self._last_save = time.time()
        try:
            with self.lock:
                tmp = self.path + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump({'stats': self.stats, 'assets': self.assets}, f)
                os.replace(tmp, self.path)
        except Exception as e:
            print(f"[STRAT] nao consegui salvar estatisticas: {e}")

    # --- contabilidade ---
    def _rec(self, asset, setup, h):
        return self.stats.setdefault(f'{asset}|{setup}|{h}', {'w': 0, 'l': 0, 't': 0, 'recent': [], 'rw': 0, 'rl': 0})

    def _add_virtual_dir(self, asset, setup, h, direction, entry, exit_):
        r = self._rec(asset, setup, h)
        if abs(exit_ - entry) < 1e-12:
            r['t'] += 1
            return
        win = (exit_ > entry) if direction == 'CALL' else (exit_ < entry)
        r['w' if win else 'l'] += 1
        r['recent'].append(1 if win else 0)
        del r['recent'][:-RECENT_KEEP]

    def record_real(self, asset, setup, h, result):
        """result: lucro (>0 win, <0 loss, 0 empate)."""
        with self.lock:
            r = self._rec(asset, setup, h)
            if result > 0:
                r['rw'] += 1
            elif result < 0:
                r['rl'] += 1
            self.save(force=True)

    # --- processamento de velas ---
    def ingest(self, asset, candles):
        """candles: lista (ordem crescente) de dicts com from/open/max/min/close, SO velas fechadas.
        Atualiza as operacoes virtuais e devolve a LISTA de sinais novos da ultima vela (vazia se nao houver)."""
        if len(candles) < MIN_BARS + 5:
            return []
        with self.lock:
            ts = [int(x['from']) for x in candles]
            F = features([x['open'] for x in candles], [x['max'] for x in candles],
                         [x['min'] for x in candles], [x['close'] for x in candles])
            c = F['c']
            n = len(c)
            idx = {t: k for k, t in enumerate(ts)}
            st = self.assets.setdefault(asset, {'processed_ts': 0, 'pending': []})

            # 1) resolve pendentes de chamadas anteriores
            still = []
            for p in st['pending']:
                left = []
                for h in p['left']:
                    k = idx.get(p['ts'] + 60 * h)
                    if k is None:
                        left.append(h)
                    else:
                        self._add_virtual_dir(asset, p['setup'], h, p['dir'], p['entry'], float(c[k]))
                if left and ts[-1] - p['ts'] < 60 * 12:
                    still.append({**p, 'left': left})
            st['pending'] = still

            # 2) avalia velas novas (cada setup que dispara gera suas operacoes virtuais)
            live = []
            for i in range(MIN_BARS, n):
                if ts[i] <= st['processed_ts']:
                    continue
                for setup, direction, det in signals_at(F, i):
                    left = []
                    for h in HORIZONS:
                        k = idx.get(ts[i] + 60 * h)
                        if k is not None and k < n:
                            self._add_virtual_dir(asset, setup, h, direction, float(c[i]), float(c[k]))
                        elif ts[i] + 60 * h > ts[-1]:
                            left.append(h)
                    if left:
                        st['pending'].append({'ts': ts[i], 'setup': setup, 'dir': direction, 'entry': float(c[i]), 'left': left})
                    if i == n - 1:
                        live.append({'setup': setup, 'dir': direction, 'ts': ts[i], 'det': det, 'price': float(c[i])})
            st['last_manip'] = {'ts': ts[-1], 'reason': manip_reason(F, n - 1) or ''}
            st['processed_ts'] = ts[-1]
        self.save()
        return live

    # --- decisao ---
    def decide(self, asset, setup):
        h, why, _ = self.decide_ex(asset, setup)
        return h, why

    def decide_ex(self, asset, setup, allowed=None):
        """Retorna (expiracao_ou_None, texto_explicando, limite_inferior_em_%). `allowed`: expiracoes que o produto aceita."""
        hs = tuple(allowed) if allowed else HORIZONS
        with self.lock:
            best = None
            seen = []
            for h in hs:
                r = self.stats.get(f'{asset}|{setup}|{h}')
                if not r:
                    continue
                n = r['w'] + r['l']
                lb = wilson_lb(*eff_wn(r))
                wr = r['w'] / n * 100 if n else 0
                seen.append((h, n, wr, lb))
                if _eligible(r)[0] and (best is None or lb > best[3]):
                    best = (h, n, wr, lb)
        if GATE == 'off':
            h = DEFAULT_H[setup]
            if h not in hs:
                h = min(hs, key=lambda x: (abs(x - h), x))
            return h, f'gate DESLIGADO - expiracao padrao {h} min (sem validacao estatistica)', 0.0
        if best:
            return best[0], f'validado: {best[2]:.0f}% em {best[1]} sinais (limite inferior {best[3] * 100:.0f}%) exp {best[0]} min', best[3] * 100
        if seen:
            h, n, wr, lb = max(seen, key=lambda x: x[3])
            return None, (f'ainda sem validacao (melhor exp {h} min: {wr:.0f}% em {n} sinais, limite inferior {lb * 100:.0f}%; '
                          f'exijo >= {MIN_LB * 100:.0f}% com no minimo {MIN_N} sinais)'), 0.0
        return None, 'sem historico desse setup neste ativo ainda', 0.0

    def asset_score(self, asset):
        """Nota para ranquear ativos: combinacao aprovada vale 100+limite inferior; as demais, o limite inferior ponderado pela amostra."""
        best = 0.0
        with self.lock:
            for setup in SETUPS:
                for h in HORIZONS:
                    r = self.stats.get(f'{asset}|{setup}|{h}')
                    if not r:
                        continue
                    n = r['w'] + r['l']
                    if n < 8:
                        continue
                    lb = wilson_lb(*eff_wn(r)) * 100
                    sc = (100 + lb) if _eligible(r)[0] else lb * min(1.0, n / MIN_N)
                    best = max(best, sc)
        return best

    def manip_info(self, asset):
        with self.lock:
            return (self.assets.get(asset) or {}).get('last_manip') or {}

    def summary(self, asset=None, allowed=None):
        rows = []
        with self.lock:
            for key, r in self.stats.items():
                a, setup, h = key.split('|')
                if asset and a != asset:
                    continue
                if allowed and int(h) not in allowed and not _is_otc_name(a):
                    continue
                n = r['w'] + r['l']
                if n == 0 and not (r['rw'] + r['rl']):
                    continue
                lb = wilson_lb(*eff_wn(r))
                ok, why = _eligible(r)
                rec = r.get('recent', [])[-QUICK_N:]
                rn = r['rw'] + r['rl']
                rows.append({'asset': a, 'setup': setup, 'exp': int(h), 'n': n,
                             'wr': round(r['w'] / n * 100, 1) if n else 0, 'lb': round(lb * 100, 1),
                             'status': 'APROVADO' if ok else why, 'rec': (f'{sum(rec)}/{len(rec)}' if rec else '-'),
                             'real_n': rn, 'real_wr': round(r['rw'] / rn * 100, 1) if rn else None})
        rows.sort(key=lambda x: (x['status'] != 'APROVADO', -x['lb']))
        return rows

    def reset(self):
        with self.lock:
            self.stats.clear()
            self.assets.clear()
            self.save(force=True)
