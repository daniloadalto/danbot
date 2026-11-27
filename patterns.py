"""(27/11) PADROES DE CANDLE perto de nivel-chave (pedido do usuario).

So valem PERTO de um nivel-chave valido, como confluencia: padrao de compra encostado num suporte, padrao de venda
encostado numa resistencia. A entrada e na ABERTURA da vela seguinte ao padrao (um padrao so existe com a vela fechada).
O catalogador mede cada padrao em cada ativo (1, 2 e 3 min) e o usuario escolhe quais usar em cada ativo.
Nivel-chave continua sempre ligado; padroes so entram nos ativos/padroes marcados pelo usuario.
"""
import os

import numpy as np

# codigo -> (nome em portugues, direcao)
PADROES = {
    'martelo':         ('Martelo', 'CALL'),
    'estrela_cadente': ('Estrela cadente', 'PUT'),
    'engolfo_alta':    ('Engolfo de alta', 'CALL'),
    'engolfo_baixa':   ('Engolfo de baixa', 'PUT'),
    'harami_alta':     ('Harami de alta', 'CALL'),
    'harami_baixa':    ('Harami de baixa', 'PUT'),
    'pinca_fundo':     ('Pinça de fundo', 'CALL'),
    'pinca_topo':      ('Pinça de topo', 'PUT'),
    'estrela_manha':   ('Estrela da manhã', 'CALL'),
    'estrela_noite':   ('Estrela da noite', 'PUT'),
    'tres_soldados':   ('Três soldados brancos', 'CALL'),
    'tres_corvos':     ('Três corvos negros', 'PUT'),
    'perfuracao':      ('Linha de perfuração', 'CALL'),
    'nuvem_negra':     ('Nuvem negra', 'PUT'),
    'doji_fundo':      ('Doji de rejeição (fundo)', 'CALL'),
    'doji_topo':       ('Doji de rejeição (topo)', 'PUT'),
}

# quantas velas formam cada padrao: o encosto no nivel e medido SO nessas velas (27/11: antes olhava sempre 3 velas e um
# doji la em cima "encostava" no nivel pela minima de uma vela anterior - entrada errada no XRPUSD 11:53)
VELAS = {'martelo': 1, 'estrela_cadente': 1, 'doji_fundo': 1, 'doji_topo': 1,
         'engolfo_alta': 2, 'engolfo_baixa': 2, 'harami_alta': 2, 'harami_baixa': 2, 'pinca_fundo': 2, 'pinca_topo': 2,
         'perfuracao': 2, 'nuvem_negra': 2,
         'estrela_manha': 3, 'estrela_noite': 3, 'tres_soldados': 3, 'tres_corvos': 3}
PERTO_Z = float(os.environ.get('PADRAO_PERTO_Z') or 2.0)       # extremo do padrao a ate 2 zonas do nivel
PAD_MIN_N = int(os.environ.get('PADRAO_MIN_SINAIS') or 5)       # catalogo: minimo de sinais para mostrar o padrao
PAD_MIN_WR = float(os.environ.get('PADRAO_MIN_ACERTO') or 60)   # catalogo: acerto minimo para mostrar o padrao
HORIZONTES = (1, 2, 3)


def nome(code):
    return PADROES.get(code, (code, ''))[0]


def detectar(o, h, l, c, i):
    """Padroes que FECHARAM na vela i. Retorna lista de codigos (sem olhar nivel)."""
    if i < 3:
        return []
    out = []
    rng = h[i] - l[i]
    if rng <= 0:
        return out
    body = abs(c[i] - o[i])
    up = h[i] - max(o[i], c[i])
    lo = min(o[i], c[i]) - l[i]
    verde, vermelha = c[i] > o[i], c[i] < o[i]
    pb = abs(c[i - 1] - o[i - 1])
    pverde, pvermelha = c[i - 1] > o[i - 1], c[i - 1] < o[i - 1]
    med = float(np.mean(np.abs(c[i - 20:i] - o[i - 20:i]))) if i >= 20 else max(pb, body)
    caiu = c[i - 1] < c[i - 3]                     # vinha caindo
    subiu = c[i - 1] > c[i - 3]                    # vinha subindo

    # martelo / estrela cadente (pavio >= 2x corpo, pavio oposto pequeno)
    if lo >= 2 * body and up <= 0.35 * max(body, 1e-12) + 0.1 * rng and lo >= 0.55 * rng and caiu:
        out.append('martelo')
    if up >= 2 * body and lo <= 0.35 * max(body, 1e-12) + 0.1 * rng and up >= 0.55 * rng and subiu:
        out.append('estrela_cadente')
    # doji de rejeicao: corpo minimo + pavio longo para um lado
    if body <= 0.1 * rng:
        if lo >= 0.6 * rng:
            out.append('doji_fundo')
        elif up >= 0.6 * rng:
            out.append('doji_topo')
    # engolfo
    if pvermelha and verde and c[i] >= o[i - 1] and o[i] <= c[i - 1] and body > pb:
        out.append('engolfo_alta')
    if pverde and vermelha and c[i] <= o[i - 1] and o[i] >= c[i - 1] and body > pb:
        out.append('engolfo_baixa')
    # harami: vela grande seguida de corpo pequeno dentro dela, cor oposta
    if pvermelha and verde and pb >= med and body <= 0.5 * pb and max(o[i], c[i]) <= o[i - 1] and min(o[i], c[i]) >= c[i - 1]:
        out.append('harami_alta')
    if pverde and vermelha and pb >= med and body <= 0.5 * pb and max(o[i], c[i]) <= c[i - 1] and min(o[i], c[i]) >= o[i - 1]:
        out.append('harami_baixa')
    # pinca: duas minimas (maximas) praticamente iguais, vermelha e depois verde
    tol = 0.1 * max(rng, h[i - 1] - l[i - 1])
    if pvermelha and verde and abs(l[i] - l[i - 1]) <= tol:
        out.append('pinca_fundo')
    if pverde and vermelha and abs(h[i] - h[i - 1]) <= tol:
        out.append('pinca_topo')
    # estrela da manha / da noite (3 velas)
    b2 = abs(c[i - 2] - o[i - 2])
    if c[i - 2] < o[i - 2] and b2 >= med and pb <= 0.35 * b2 and verde and c[i] >= (o[i - 2] + c[i - 2]) / 2:
        out.append('estrela_manha')
    if c[i - 2] > o[i - 2] and b2 >= med and pb <= 0.35 * b2 and vermelha and c[i] <= (o[i - 2] + c[i - 2]) / 2:
        out.append('estrela_noite')
    # tres soldados / tres corvos
    if all(c[k] > o[k] for k in (i - 2, i - 1, i)) and c[i] > c[i - 1] > c[i - 2] and \
            o[i - 1] >= o[i - 2] and o[i] >= o[i - 1] and min(abs(c[k] - o[k]) for k in (i - 2, i - 1, i)) >= 0.5 * med:
        out.append('tres_soldados')
    if all(c[k] < o[k] for k in (i - 2, i - 1, i)) and c[i] < c[i - 1] < c[i - 2] and \
            o[i - 1] <= o[i - 2] and o[i] <= o[i - 1] and min(abs(c[k] - o[k]) for k in (i - 2, i - 1, i)) >= 0.5 * med:
        out.append('tres_corvos')
    # linha de perfuracao / nuvem negra
    if pvermelha and verde and pb >= med and o[i] <= c[i - 1] and (o[i - 1] + c[i - 1]) / 2 <= c[i] < o[i - 1]:
        out.append('perfuracao')
    if pverde and vermelha and pb >= med and o[i] >= c[i - 1] and o[i - 1] < c[i] <= (o[i - 1] + c[i - 1]) / 2:
        out.append('nuvem_negra')
    return out


def nivel_perto(h, l, c, i, levels, t_i, z, d, nv=3):
    """Nivel-chave valido encostado no padrao (nas nv velas dele): suporte (CALL) / resistencia (PUT). Nivel ou None."""
    if not levels or not (z > 0):
        return None
    k0 = i - max(1, int(nv)) + 1
    lo3, hi3 = float(np.min(l[k0:i + 1])), float(np.max(h[k0:i + 1]))
    best = None
    for lv in levels:
        if not (lv['t0'] <= t_i < lv['t1']):
            continue
        p = lv['p']
        if d == 'CALL':
            ok = lo3 <= p + PERTO_Z * z and lo3 >= p - PERTO_Z * z and c[i] >= p - z
            dist = abs(lo3 - p)
        else:
            ok = hi3 >= p - PERTO_Z * z and hi3 <= p + PERTO_Z * z and c[i] <= p + z
            dist = abs(hi3 - p)
        if ok and (best is None or dist < best[0]):
            best = (dist, lv)
    return best[1] if best else None


def sinais_na_vela(F, i, levels, t_i):
    """Padroes da vela fechada i encostados num nivel-chave. F do strategy_v2 com kl_zone (KL.prepare).
    Retorna lista de dicts {code, nome, dir, nivel, tag}."""
    o, h, l, c = F['o'], F['h'], F['l'], F['c']
    z = float(F['kl_zone'][i]) if np.isfinite(F['kl_zone'][i]) else 0.0
    out = []
    for code in detectar(o, h, l, c, i):
        d = PADROES[code][1]
        lv = nivel_perto(h, l, c, i, levels, t_i, z, d, VELAS.get(code, 3))
        if lv is not None:
            out.append({'code': code, 'nome': nome(code), 'dir': d, 'nivel': float(lv['p']), 'tag': lv['tag']})
    return out


def catalogar(F, t, levels, start, n):
    """Mede cada padrao (perto de nivel) nas velas start..n-1. Retorna lista ordenada dos melhores:
    [{code, nome, dir, n, w, l, wr:{1,2,3}, best_h, best_wr}] - so os com >= PAD_MIN_N sinais e >= PAD_MIN_WR%."""
    c = F['c']
    acc = {}
    for i in range(max(start, 21), n - 1):
        for s in sinais_na_vela(F, i, levels, int(t[i])):
            a = acc.setdefault(s['code'], {h: [0, 0] for h in HORIZONTES})
            for hz in HORIZONTES:
                if i + hz < n:
                    dd = float(c[i + hz] - c[i])
                    if abs(dd) >= 1e-12:
                        a[hz][0 if ((dd > 0) == (s['dir'] == 'CALL')) else 1] += 1
    res = []
    for code, a in acc.items():
        wr = {hz: (round(100.0 * a[hz][0] / (a[hz][0] + a[hz][1]), 1) if (a[hz][0] + a[hz][1]) else 0.0) for hz in HORIZONTES}
        cand = [hz for hz in HORIZONTES if a[hz][0] + a[hz][1] >= PAD_MIN_N]
        if not cand:
            continue
        bh = max(cand, key=lambda hz: (wr[hz], -hz))
        nn = a[bh][0] + a[bh][1]
        if wr[bh] < PAD_MIN_WR:
            continue
        res.append({'code': code, 'nome': nome(code), 'dir': PADROES[code][1], 'n': nn, 'w': a[bh][0], 'l': a[bh][1],
                    'wr': {str(k): v for k, v in wr.items()}, 'best_h': bh, 'best_wr': wr[bh]})
    res.sort(key=lambda r: (-r['best_wr'], -r['n']))
    return res
