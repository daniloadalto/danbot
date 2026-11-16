"""DanBot - estrategia NIVEL_CHAVE (16/11): price action em suporte e resistencia de niveis-chave.

Niveis (cada um so vale a partir do momento em que ficou conhecido - nada olha o futuro):
  - H1: topos e fundos fractais (2 velas de cada lado) das velas de 1 hora; valem por 3 dias
  - H4: o mesmo nas velas de 4 horas; valem por 8 dias
  - Dia anterior (das velas de 5 min): maxima, minima, pivo P, R1, S1, POC por volume e POC por tempo (TPO); valem no dia
Entrada (vela M1 que acabou de fechar):
  CALL = a vela anterior fechou ACIMA do nivel, a vela atual tocou a zona do nivel e NAO fechou abaixo dela,
         RSI 14 <= 35 e a minima tocou a Bollinger de baixo (20, 2)
  PUT  = o espelho na resistencia (RSI >= 65 e a maxima tocou a Bollinger de cima)
  Zona do nivel = 0,15 x a amplitude media das velas M1. Nao exige pavio: a vela pode "morrer" no nivel.
  Expiracao 1 minuto.
Teste em ~590 mil velas M1 reais (5 pares de forex, mar/2020, abr/2020, set-out/2019): 54,1 / 54,3 / 53,8% de acerto,
~3 entradas por hora por ativo. So H4 + POC: 56,8 / 57,8 / 53,4%, ~1 por hora.
"""
import os
import numpy as np

SETUP = 'NIVEL_CHAVE'
ZONE_K = float(os.environ.get('NIVEL_ZONA') or 0.15)           # zona = K x amplitude media M1
RSI_LO = float(os.environ.get('NIVEL_RSI_BAIXO') or 35)
RSI_HI = 100.0 - RSI_LO
# tipos de nivel usados (todos por padrao). Ex.: NIVEL_TIPOS=H4,POC_volume,POC_tempo
_ALL_TYPES = ('H1', 'H4', 'dia_max', 'dia_min', 'pivo_P', 'pivo_R1', 'pivo_S1', 'POC_volume', 'POC_tempo')
_env_types = (os.environ.get('NIVEL_TIPOS') or '').strip()
TYPES = tuple(t for t in (x.strip() for x in _env_types.split(',')) if t in _ALL_TYPES) if _env_types else _ALL_TYPES

H1_LIFE = 3 * 86400
H4_LIFE = 8 * 86400


def _arr(cs, k):
    return np.array([float(x.get(k) or 0.0) for x in cs], float)


def _fractals(cs, tf_sec, life, tag, k=2):
    out = []
    if not cs or len(cs) < 2 * k + 1:
        return out
    cs = sorted(cs, key=lambda x: int(x['from']))
    h, l = _arr(cs, 'max'), _arr(cs, 'min')
    t = [int(x['from']) for x in cs]
    for j in range(k, len(cs) - k):
        wh, wl = h[j - k:j + k + 1], l[j - k:j + k + 1]
        t0 = t[j + k] + tf_sec                       # so e conhecido quando a vela j+k FECHA
        if h[j] == wh.max() and (wh == h[j]).sum() == 1:
            out.append({'p': float(h[j]), 'tag': tag, 't0': t0, 't1': t0 + life})
        if l[j] == wl.min() and (wl == l[j]).sum() == 1:
            out.append({'p': float(l[j]), 'tag': tag, 't0': t0, 't1': t0 + life})
    return out


def _daily(cs_m5):
    out = []
    if not cs_m5:
        return out
    days = {}
    for x in cs_m5:
        days.setdefault(int(x['from']) // 86400, []).append(x)
    for d, lst in days.items():
        if len(lst) < 200:                           # dia incompleto: sem nivel
            continue
        lst = sorted(lst, key=lambda x: int(x['from']))
        h, l, c, v = _arr(lst, 'max'), _arr(lst, 'min'), _arr(lst, 'close'), _arr(lst, 'volume')
        H, L, C = float(h.max()), float(l.min()), float(c[-1])
        if H <= L:
            continue
        P = (H + L + C) / 3.0
        bins = np.linspace(L, H, 41)
        idx = np.clip(np.digitize((h + l) / 2.0, bins) - 1, 0, 39)
        tpo = np.bincount(idx, minlength=40)
        t0, t1 = (d + 1) * 86400, (d + 2) * 86400   # valem no dia SEGUINTE
        lv = [('dia_max', H), ('dia_min', L), ('pivo_P', P), ('pivo_R1', 2 * P - L), ('pivo_S1', 2 * P - H),
              ('POC_tempo', float((bins[tpo.argmax()] + bins[tpo.argmax() + 1]) / 2))]
        if v.sum() > 0:
            vol = np.bincount(idx, weights=v, minlength=40)
            lv.append(('POC_volume', float((bins[vol.argmax()] + bins[vol.argmax() + 1]) / 2)))
        for tag, p in lv:
            out.append({'p': float(p), 'tag': tag, 't0': t0, 't1': t1})
    return out


def build_levels(h1=None, h4=None, m5=None):
    """Recebe velas (dicts da IQ: from/open/max/min/close/volume) de H1, H4 e M5 e devolve a lista de niveis."""
    lv = _fractals(h1 or [], 3600, H1_LIFE, 'H1') + _fractals(h4 or [], 14400, H4_LIFE, 'H4') + _daily(m5 or [])
    return [x for x in lv if x['tag'] in TYPES]


# ---------------- indicadores (iguais aos do teste) ----------------
def _rma(x, n):
    out = np.full(len(x), np.nan)
    a = 1.0 / n
    prev = np.nan
    for i, v in enumerate(x):
        prev = v if np.isnan(prev) else prev + a * (v - prev)
        out[i] = prev
    return out


def prepare(F):
    """Acrescenta ao F (features do strategy_v2) o que a estrategia usa: RSI 14 de Wilder, Bollinger 20/2 e a zona."""
    if 'kl_rsi' in F:
        return F
    c, h, l = F['c'], F['h'], F['l']
    d = np.diff(c, prepend=c[0])
    ag, al = _rma(np.maximum(d, 0), 14), _rma(np.maximum(-d, 0), 14)
    with np.errstate(divide='ignore', invalid='ignore'):
        F['kl_rsi'] = 100 - 100 / (1 + ag / (al + 1e-12))
    n = len(c)
    m = np.full(n, np.nan)
    sd = np.full(n, np.nan)
    if n >= 20:
        from numpy.lib.stride_tricks import sliding_window_view as sw
        w = sw(c, 20)
        m[19:] = w.mean(axis=1)
        sd[19:] = w.std(axis=1)
    F['kl_bb_lo'], F['kl_bb_hi'] = m - 2 * sd, m + 2 * sd
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    F['kl_zone'] = _rma(tr, 300) * ZONE_K
    return F


def signal_at(F, i, levels, t_i):
    """Sinal na vela i (fechada) que comecou no instante t_i (segundos). Retorna (dir, tags, preco_nivel) ou None."""
    if not levels or i < 21:
        return None
    prepare(F)
    c, h, l = F['c'], F['h'], F['l']
    z, r = F['kl_zone'][i], F['kl_rsi'][i]
    if not np.isfinite(z) or not np.isfinite(r) or z <= 0:
        return None
    sup, res = [], []
    for lv in levels:
        if not (lv['t0'] <= t_i < lv['t1']):
            continue
        p = lv['p']
        if c[i - 1] > p and l[i] <= p + z and c[i] >= p - z:
            sup.append(lv)
        elif c[i - 1] < p and h[i] >= p - z and c[i] <= p + z:
            res.append(lv)
    if sup and r <= RSI_LO and l[i] <= F['kl_bb_lo'][i]:
        return 'CALL', sorted({x['tag'] for x in sup}), min(x['p'] for x in sup)
    if res and r >= RSI_HI and h[i] >= F['kl_bb_hi'][i]:
        return 'PUT', sorted({x['tag'] for x in res}), max(x['p'] for x in res)
    return None


def nearest(F, i, levels, t_i):
    """Para o medidor de sentimento: nivel valido mais proximo do preco atual (distancia em zonas)."""
    prepare(F)
    c = float(F['c'][i])
    z = float(F['kl_zone'][i]) if np.isfinite(F['kl_zone'][i]) else 0.0
    best = None
    for lv in levels or []:
        if not (lv['t0'] <= t_i < lv['t1']):
            continue
        d = abs(c - lv['p'])
        if best is None or d < best[0]:
            best = (d, lv)
    if best is None or z <= 0:
        return None
    lv = best[1]
    near_tags = sorted({x['tag'] for x in levels if x['t0'] <= t_i < x['t1'] and abs(x['p'] - lv['p']) <= z})
    return {'dist_z': best[0] / z, 'p': lv['p'], 'lado': 'SUPORTE' if lv['p'] <= c else 'RESISTENCIA', 'tags': near_tags}
