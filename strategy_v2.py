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
  GATE_MIN_N      amostra minima por (ativo, setup, expiracao)   [40]
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
SETUPS = ('TREND_PULLBACK', 'RANGE_REVERSION')
DEFAULT_H = {'TREND_PULLBACK': 3, 'RANGE_REVERSION': 2}
MIN_BARS = 130          # velas de aquecimento (EMA 105 precisa estabilizar)
RECENT_KEEP = 60

GATE = (os.environ.get('STRATEGY_GATE') or 'on').strip().lower()
MIN_N = int(os.environ.get('GATE_MIN_N') or 40)
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
    return {
        'o': o, 'h': h, 'l': l, 'c': c, 'atr': atr, 'atr_ratio': atr / (atr_avg + 1e-12),
        'er': er, 'z': z, 'rsi': _rsi(c, 7), 'e21': e21, 'e55': e55, 'e105': e105, 'slope': slope,
    }


def signal_at(F, i):
    """Retorna (setup, 'CALL'|'PUT', detalhes) para a vela fechada i, ou None."""
    if i < MIN_BARS:
        return None
    c, o, h, l = F['c'], F['o'], F['h'], F['l']
    atr, ar = F['atr'][i], F['atr_ratio'][i]
    if not np.isfinite(ar) or not np.isfinite(F['er'][i]) or not np.isfinite(F['slope'][i]):
        return None
    # filtro de volatilidade: ignora mercado parado, spikes de noticia e vela gigante
    if ar < 0.5 or ar > 2.2 or (h[i] - l[i]) > 3.0 * atr:
        return None
    er, slope, z, rsi = F['er'][i], F['slope'][i], F['z'], F['rsi']
    det = {'er': round(float(er), 2), 'slope': round(float(slope), 2), 'rsi': round(float(rsi[i]), 1),
           'z': round(float(z[i]), 2), 'atr_ratio': round(float(ar), 2)}

    # ---- tendencia: EMA 21 > 55 > 105 (equivale a M5 9/21), inclinacao e eficiencia altas ----
    if er > 0.28:
        up = F['e21'][i] > F['e55'][i] > F['e105'][i] and slope > 0.25
        dn = F['e21'][i] < F['e55'][i] < F['e105'][i] and slope < -0.25
        if up and rsi[i - 4:i].min() < 38 and rsi[i] > rsi[i - 1] and rsi[i] > 42 and c[i] > o[i] and z[i] < 1.8:
            return 'TREND_PULLBACK', 'CALL', det
        if dn and rsi[i - 4:i].max() > 62 and rsi[i] < rsi[i - 1] and rsi[i] < 58 and c[i] < o[i] and z[i] > -1.8:
            return 'TREND_PULLBACK', 'PUT', det
    # ---- lateral: eficiencia baixa e media sem inclinacao; reversao no extremo ----
    elif er < 0.25 and abs(slope) < 0.35:
        if (z[i - 1] <= -2.0 or z[i] <= -2.0) and rsi[i - 1:i + 1].min() < 25 and c[i] > o[i] and c[i] > c[i - 1]:
            return 'RANGE_REVERSION', 'CALL', det
        if (z[i - 1] >= 2.0 or z[i] >= 2.0) and rsi[i - 1:i + 1].max() > 75 and c[i] < o[i] and c[i] < c[i - 1]:
            return 'RANGE_REVERSION', 'PUT', det
    return None


# ----------------------------------------------------------------- estatistica
def wilson_lb(w, n, z=1.96):
    if n <= 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - m) / d


def _eligible(r):
    """(aprovado?, motivo_se_nao). Alem da amostra e do limite inferior, suspende celulas que pioraram."""
    n = r['w'] + r['l']
    if n < MIN_N:
        return False, 'aprendendo'
    if wilson_lb(r['w'], n) < MIN_LB:
        return False, 'reprovado'
    rec = r.get('recent', [])
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
        Atualiza as operacoes virtuais e devolve o sinal da ultima vela (se houver e for novo)."""
        if len(candles) < MIN_BARS + 5:
            return None
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

            # 2) avalia velas novas
            live = None
            for i in range(MIN_BARS, n):
                if ts[i] <= st['processed_ts']:
                    continue
                sg = signal_at(F, i)
                if not sg:
                    continue
                setup, direction, det = sg
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
                    live = {'setup': setup, 'dir': direction, 'ts': ts[i], 'det': det, 'price': float(c[i])}
            st['processed_ts'] = ts[-1]
        self.save()
        return live

    # --- decisao ---
    def decide(self, asset, setup):
        """Retorna (expiracao_ou_None, texto_explicando)."""
        with self.lock:
            best = None
            seen = []
            for h in HORIZONS:
                r = self.stats.get(f'{asset}|{setup}|{h}')
                if not r:
                    continue
                n = r['w'] + r['l']
                lb = wilson_lb(r['w'], n)
                wr = r['w'] / n * 100 if n else 0
                seen.append((h, n, wr, lb))
                if _eligible(r)[0] and (best is None or lb > best[3]):
                    best = (h, n, wr, lb)
        if GATE == 'off':
            h = DEFAULT_H[setup]
            return h, f'gate DESLIGADO - expiracao padrao {h} min (sem validacao estatistica)'
        if best:
            return best[0], f'validado: {best[2]:.0f}% em {best[1]} sinais (limite inferior {best[3] * 100:.0f}%) exp {best[0]} min'
        if seen:
            h, n, wr, lb = max(seen, key=lambda x: x[3])
            return None, f'ainda sem validacao (melhor exp {h} min: {wr:.0f}% em {n} sinais, limite inferior {lb * 100:.0f}%; exijo >= {MIN_LB * 100:.0f}% com no minimo {MIN_N} sinais)'
        return None, 'sem historico desse setup neste ativo ainda'

    def summary(self, asset=None):
        rows = []
        with self.lock:
            for key, r in self.stats.items():
                a, setup, h = key.split('|')
                if asset and a != asset:
                    continue
                n = r['w'] + r['l']
                if n == 0 and not (r['rw'] + r['rl']):
                    continue
                lb = wilson_lb(r['w'], n)
                ok, why = _eligible(r)
                rn = r['rw'] + r['rl']
                rows.append({'asset': a, 'setup': setup, 'exp': int(h), 'n': n,
                             'wr': round(r['w'] / n * 100, 1) if n else 0, 'lb': round(lb * 100, 1),
                             'status': 'APROVADO' if ok else why,
                             'real_n': rn, 'real_wr': round(r['rw'] / rn * 100, 1) if rn else None})
        rows.sort(key=lambda x: (x['status'] != 'APROVADO', -x['lb']))
        return rows

    def reset(self):
        with self.lock:
            self.stats.clear()
            self.assets.clear()
            self.save(force=True)
