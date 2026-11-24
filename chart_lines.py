"""(21/11) Desenho do grafico ao vivo do painel: linhas de tendencia, padroes graficos e niveis-chave.
So VISUAL: nao entra na decisao do bot. Usa as velas M1 que o bot ja leu (nenhum pedido a mais na IQ)."""
import numpy as np


def _atr(h, l, n=14):
    r = h - l
    return float(np.mean(r[-n:])) if len(r) else 0.0


def swings(h, l, k=3):
    """Topos e fundos (k velas de cada lado). Retorna (lista de topos, lista de fundos) como indices."""
    hi, lo = [], []
    for j in range(k, len(h) - k):
        wh, wl = h[j - k:j + k + 1], l[j - k:j + k + 1]
        if h[j] == wh.max() and (wh == h[j]).sum() == 1:
            hi.append(j)
        if l[j] == wl.min() and (wl == l[j]).sum() == 1:
            lo.append(j)
    return hi, lo


def _best_line(idx, y, other, close, side, tol, n, min_gap=8):
    """Melhor linha por dois pivos sem ser furada entre eles. Prefere a linha AINDA VALIDA (preco nao fechou
    do outro lado depois dela), com mais toques e mais longa. side=-1: LTB por topos; side=+1: LTA por fundos.
    Retorna (a, b, inclinacao, toques, indice_do_rompimento ou None)."""
    if len(idx) < 2:
        return None
    best, best_sc = None, -1e9
    cand = idx[-10:]
    for bi in range(len(cand)):
        for ai in range(bi):
            a, b = cand[ai], cand[bi]
            if b - a < min_gap:
                continue
            if (side < 0 and not y[a] > y[b]) or (side > 0 and not y[a] < y[b]):
                continue
            slope = (y[b] - y[a]) / (b - a)
            seg = np.arange(a, n)
            line = y[a] + slope * (seg - a)
            inside = other[a:b + 1] - line[:b - a + 1]
            if (side < 0 and np.any(inside > tol)) or (side > 0 and np.any(inside < -tol)):
                continue
            after = close[b + 1:] - line[b - a + 1:]
            brk = np.where(after > tol)[0] if side < 0 else np.where(after < -tol)[0]
            brk_i = int(b + 1 + brk[0]) if len(brk) else None
            end = brk_i if brk_i is not None else n - 1
            touches = sum(1 for x in idx if a <= x <= end and abs(y[x] - (y[a] + slope * (x - a))) <= tol)
            sc = touches * 10 + (end - a) * 0.1 + (50 if brk_i is None else 0) + b * 0.05
            if sc > best_sc:
                best, best_sc = (a, b, slope, touches, brk_i), sc
    return best


def analyze(cs, levels=None):
    """cs: velas da IQ (from/open/max/min/close). Retorna linhas, padroes e niveis prontos para o grafico."""
    cs = sorted(cs or [], key=lambda x: int(x['from']))
    if len(cs) < 30:
        return {'lines': [], 'patterns': [], 'levels': []}
    t = np.array([int(x['from']) for x in cs])
    h = np.array([float(x['max']) for x in cs]); l = np.array([float(x['min']) for x in cs])
    c = np.array([float(x['close']) for x in cs])
    n = len(c)
    atr = _atr(h, l) or float(np.mean(h - l)) or 1e-9
    tol = 0.15 * atr
    hi, lo = swings(h, l)
    ext = 12                                         # projeta a linha 12 velas para frente
    lines = []
    found = {}
    for kind, idx, y, other, side in (('baixa', hi, h, h, -1), ('alta', lo, l, l, 1)):
        r = _best_line(idx, y, other, c, side, tol, n)
        if not r:
            continue
        a, b, slope, touches, brk = r
        if brk is not None and n - 1 - brk > 30:
            continue                                 # rompida faz tempo: nao polui o grafico
        def at(i):
            return float(y[a] + slope * (i - a))
        e = brk if brk is not None else n - 1 + ext
        t2 = int(t[e]) if e < n else int(t[-1] + 60 * (e - n + 1))
        lines.append({'kind': kind, 't1': int(t[a]), 'p1': at(a), 't2': t2, 'p2': at(e),
                      'touches': int(touches), 'broken': brk is not None,
                      'label': ('LTB' if side < 0 else 'LTA') + (' rompida' if brk is not None else '')})
        found[kind] = (a, b, slope, brk is not None)
    pats = []
    # topo / fundo duplo
    for nome, idx, y, opp, side in (('Topo duplo', hi, h, l, -1), ('Fundo duplo', lo, l, h, 1)):
        if len(idx) >= 2:
            a, b = idx[-2], idx[-1]
            if b - a >= 5 and abs(y[a] - y[b]) <= 0.35 * atr:
                mid = opp[a:b + 1]
                neck = float(mid.min() if side < 0 else mid.max())
                if abs(y[b] - neck) >= 1.2 * atr and n - 1 - b <= 25:
                    pats.append({'name': nome, 't': int(t[b]), 'p': float(y[b]), 'side': side,
                                 'box': {'t0': int(t[a]), 't1': int(t[b]), 'hi': float(h[a:b + 1].max()), 'lo': float(l[a:b + 1].min())},
                                 'desc': f'{nome} - linha do pescoco em {neck:.5f}',
                                 'neck': {'t1': int(t[a]), 't2': int(t[-1] + 60 * 6), 'p': neck}})
    # OCO / OCO invertido (ombro-cabeca-ombro)
    for nome, idx, y, side in (('OCO', hi, h, -1), ('OCO invertido', lo, l, 1)):
        if len(idx) >= 3:
            a, m, b = idx[-3], idx[-2], idx[-1]
            head = (y[m] > max(y[a], y[b])) if side < 0 else (y[m] < min(y[a], y[b]))
            if head and abs(y[a] - y[b]) <= 0.6 * atr and abs(y[m] - (y[a] + y[b]) / 2) >= 0.8 * atr and n - 1 - b <= 25:
                pats.append({'name': nome, 't': int(t[m]), 'p': float(y[m]), 'side': side,
                             'box': {'t0': int(t[a]), 't1': int(t[b]), 'hi': float(h[a:b + 1].max()), 'lo': float(l[a:b + 1].min())},
                             'desc': f'{nome} - cabeca em {y[m]:.5f}'})
    # triangulos e canais (as duas linhas juntas)
    if 'baixa' in found and 'alta' in found and not found['baixa'][3] and not found['alta'][3]:
        sd, su = found['baixa'][2], found['alta'][2]
        flat = atr / 60.0                             # "reta": anda menos de 1 ATR em 60 velas
        start = max(found['baixa'][0], found['alta'][0])
        if abs(sd) <= flat and su > flat:
            nome = 'Triangulo ascendente'
        elif abs(su) <= flat and sd < -flat:
            nome = 'Triangulo descendente'
        elif sd < -flat and su > flat:
            nome = 'Triangulo simetrico'
        elif sd > flat and su > flat and abs(sd - su) <= 0.5 * max(abs(sd), abs(su)):
            nome = 'Canal de alta'
        elif sd < -flat and su < -flat and abs(sd - su) <= 0.5 * max(abs(sd), abs(su)):
            nome = 'Canal de baixa'
        else:
            nome = ''
        if nome:
            pats.append({'name': nome, 't': int(t[start]), 'p': float(h[start] if 'baixa' in nome or 'descendente' in nome else l[start]),
                         'box': {'t0': int(t[start]), 't1': int(t[-1]), 'hi': float(h[start:].max()), 'lo': float(l[start:].min())},
                         'side': 0, 'desc': nome + ' entre a LTB e a LTA'})
    # niveis-chave validos agora, dentro da faixa do grafico (juntando os que estao colados)
    lvs = []
    tn = int(t[-1])
    lo_p, hi_p = float(l.min()) - 5 * atr, float(h.max()) + 5 * atr
    for x in sorted((x for x in (levels or []) if x.get('t0', 0) <= tn < x.get('t1', 1 << 40) and lo_p <= x['p'] <= hi_p),
                    key=lambda x: x['p']):
        if lvs and abs(x['p'] - lvs[-1]['p']) <= tol:
            if x['tag'] not in lvs[-1]['tags']:
                lvs[-1]['tags'].append(x['tag'])
            continue
        lvs.append({'p': float(x['p']), 'tags': [x['tag']]})
    last = float(c[-1])                              # so os 3 mais perto acima e os 3 mais perto abaixo do preco
    below = [x for x in lvs if x['p'] <= last][-3:]
    above = [x for x in lvs if x['p'] > last][:3]
    lvs = below + above
    for x in lvs:
        x['label'] = '+'.join(x['tags'])
        x['strong'] = any(tg.startswith(('H4', 'POC')) for tg in x['tags'])
    return {'lines': lines, 'patterns': pats, 'levels': lvs}
