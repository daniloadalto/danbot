"""
DANBOT — Módulo: LÓGICA DO PREÇO
=================================
Implementa a análise avançada de Price Action baseada na metodologia
"Lógica do Preço" para opções binárias M1.

CONCEITOS IMPLEMENTADOS:
  1. Significado dos Pavios (oferta x demanda)
  2. Vela de Comando (Único de Alta / Baixa)
  3. Vela de Força (Final de Taxa / Candle Expirado)
  4. Variação do Comando (Candle Mágico / Controle de Região)
  5. Taxa Dividida (início e fechamento de lote)
  6. Abertura e Fechamento de Lote (macro/micro)
  7. Primeiro Registro (nova instalação de preço)
  8. Posicionamento (reversão vs continuidade)
  9. Nova Alta / Nova Baixa
 10. Regiões de Demanda (pressão de compra / venda)
 11. Pressão de Pavio (cluster de pavios na mesma direção)
 12. Defesa do Preço (bate-e-volta em marcações)
"""

import numpy as np
from typing import Optional


# ═══════════════════════════════════════════════════════════════════════════════
# 1. PAVIOS — OFERTA & DEMANDA
# ═══════════════════════════════════════════════════════════════════════════════

def calc_pavio_info(opens: np.ndarray, highs: np.ndarray,
                    lows: np.ndarray, closes: np.ndarray) -> dict:
    """
    Analisa o último candle para extrair informações de pavios.

    Retorna:
      pavio_sup   : tamanho do pavio superior (demanda de venda)
      pavio_inf   : tamanho do pavio inferior (demanda de compra)
      corpo       : tamanho do corpo (oferta do preço)
      range_total : amplitude total da vela
      razao_sup   : pavio_sup / range_total  (% superior)
      razao_inf   : pavio_inf / range_total  (% inferior)
      razao_corpo : corpo / range_total
      dominio_pavio : 'superior' | 'inferior' | 'equilibrado'
      pressao_compradora : True se pavios inf > sup (demanda de compra)
      pressao_vendedora  : True se pavios sup > inf (demanda de venda)
    """
    o, h, l, c = float(opens[-1]), float(highs[-1]), float(lows[-1]), float(closes[-1])

    corpo = abs(c - o)
    rng   = h - l
    if rng < 1e-10:
        return {
            'pavio_sup': 0, 'pavio_inf': 0, 'corpo': 0,
            'range_total': 0, 'razao_sup': 0, 'razao_inf': 0,
            'razao_corpo': 0, 'dominio_pavio': 'equilibrado',
            'pressao_compradora': False, 'pressao_vendedora': False
        }

    # Pavio superior: de max(o,c) até h
    pavio_sup = h - max(o, c)
    # Pavio inferior: de min(o,c) até l
    pavio_inf = min(o, c) - l

    rs = pavio_sup / rng
    ri = pavio_inf / rng
    rc = corpo / rng

    if pavio_sup > pavio_inf * 1.5:
        dominio = 'superior'
    elif pavio_inf > pavio_sup * 1.5:
        dominio = 'inferior'
    else:
        dominio = 'equilibrado'

    return {
        'pavio_sup'         : round(pavio_sup, 6),
        'pavio_inf'         : round(pavio_inf, 6),
        'corpo'             : round(corpo, 6),
        'range_total'       : round(rng, 6),
        'razao_sup'         : round(rs, 4),
        'razao_inf'         : round(ri, 4),
        'razao_corpo'       : round(rc, 4),
        'dominio_pavio'     : dominio,
        'pressao_compradora': pavio_inf > pavio_sup,   # pavios inferiores = demanda de compra
        'pressao_vendedora' : pavio_sup > pavio_inf,   # pavios superiores = demanda de venda
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. VELA DE COMANDO (ÚNICO)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_vela_comando(opens: np.ndarray, highs: np.ndarray,
                         lows: np.ndarray, closes: np.ndarray,
                         idx: int = -1) -> Optional[dict]:
    """
    Vela de Comando Único:
      • Comando de ALTA  (verde/bullish): nasce sem pavio inferior (careca embaixo)
      • Comando de BAIXA (vermelho/bearish): nasce sem pavio superior (careca em cima)

    Critério: pavio no lado da abertura < 10% do range total.

    Retorna dict ou None.
    """
    o = float(opens[idx])
    h = float(highs[idx])
    l = float(lows[idx])
    c = float(closes[idx])
    rng = h - l
    if rng < 1e-10:
        return None

    bullish = c > o
    bearish = c < o

    pavio_sup = h - max(o, c)
    pavio_inf = min(o, c) - l

    # Comando de Alta: abre de baixo pra cima → não pode ter pavio inferior
    if bullish and (pavio_inf / rng) < 0.10:
        nivel = 2  # Comando Nível 2 padrão
        if (pavio_inf / rng) < 0.02 and (pavio_sup / rng) < 0.02:
            nivel = 1  # Variação/Candle Mágico (careca dos dois lados)
        elif (pavio_sup / rng) < 0.05:
            nivel = 3  # Final de taxa / expirado
        return {
            'tipo'   : 'comando_alta',
            'dir'    : 'CALL',
            'nivel'  : nivel,
            'desc'   : f'Comando de Alta N{nivel} (sem pavio inf.)',
            'abertura': round(o, 6),
            'meio'   : round((o + c) / 2, 6),
            'fechamento': round(c, 6),
        }

    # Comando de Baixa: abre de cima pra baixo → não pode ter pavio superior
    if bearish and (pavio_sup / rng) < 0.10:
        nivel = 2
        if (pavio_sup / rng) < 0.02 and (pavio_inf / rng) < 0.02:
            nivel = 1
        elif (pavio_inf / rng) < 0.05:
            nivel = 3
        return {
            'tipo'   : 'comando_baixa',
            'dir'    : 'PUT',
            'nivel'  : nivel,
            'desc'   : f'Comando de Baixa N{nivel} (sem pavio sup.)',
            'abertura': round(o, 6),
            'meio'   : round((o + c) / 2, 6),
            'fechamento': round(c, 6),
        }

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. VELA DE FORÇA / FINAL DE TAXA
# ═══════════════════════════════════════════════════════════════════════════════

def detect_vela_forca(opens: np.ndarray, highs: np.ndarray,
                       lows: np.ndarray, closes: np.ndarray,
                       idx: int = -1) -> Optional[dict]:
    """
    Vela de Força (Final de Taxa / Candle Expirado):
      • Força de ALTA : candle bullish sem pavio SUPERIOR (fechou na máxima)
      • Força de BAIXA: candle bearish sem pavio INFERIOR (fechou na mínima)

    Indica exaustão e possível reversão no próximo candle.
    """
    o = float(opens[idx])
    h = float(highs[idx])
    l = float(lows[idx])
    c = float(closes[idx])
    rng = h - l
    if rng < 1e-10:
        return None

    bullish = c > o
    bearish = c < o
    pavio_sup = h - max(o, c)
    pavio_inf = min(o, c) - l
    corpo = abs(c - o)

    # Final de Taxa Alta: vela verde sem pavio superior (corpo toca a máxima)
    if bullish and (pavio_sup / rng) < 0.08 and (corpo / rng) > 0.55:
        return {
            'tipo' : 'forca_alta',
            'dir'  : 'CALL',
            'desc' : '💥 Vela Força Alta (Final de Taxa) — possível continuação ou reversão',
            'nivel': 3,
        }

    # Final de Taxa Baixa: vela vermelha sem pavio inferior
    if bearish and (pavio_inf / rng) < 0.08 and (corpo / rng) > 0.55:
        return {
            'tipo' : 'forca_baixa',
            'dir'  : 'PUT',
            'desc' : '💥 Vela Força Baixa (Final de Taxa) — possível continuação ou reversão',
            'nivel': 3,
        }

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TAXA DIVIDIDA
# ═══════════════════════════════════════════════════════════════════════════════

def detect_taxa_dividida(opens: np.ndarray, highs: np.ndarray,
                          lows: np.ndarray, closes: np.ndarray) -> Optional[dict]:
    """
    Taxa Dividida = ponto onde o preço INICIA ou TERMINA um lote.
    Formação: Comando Único + Final de Taxa (ou vice-versa), mesma cor.

    Detecta nos últimos 3 candles.
    Força: FORTE (comando+expirado), MÉDIA (só um deles), FRACA (força+pavio).
    """
    if len(opens) < 3:
        return None

    c1 = detect_vela_comando(opens, highs, lows, closes, idx=-2)
    c0 = detect_vela_forca(opens, highs, lows, closes, idx=-1)

    f1 = detect_vela_forca(opens, highs, lows, closes, idx=-2)
    c0b = detect_vela_comando(opens, highs, lows, closes, idx=-1)

    # Taxa Dividida FORTE: expirado → comando (mesma direção)
    if f1 and c0b and f1['dir'] == c0b['dir']:
        return {
            'tipo'   : 'taxa_dividida_forte',
            'dir'    : f1['dir'],
            'forca'  : 'FORTE',
            'desc'   : f'📊 Taxa Dividida FORTE ({f1["dir"]}) — início/fim de lote',
            'nivel'  : 1,
        }

    # Taxa Dividida MÉDIA: comando → final de taxa
    if c1 and c0 and c1['dir'] == c0['dir']:
        return {
            'tipo'   : 'taxa_dividida_media',
            'dir'    : c1['dir'],
            'forca'  : 'MÉDIA',
            'desc'   : f'📊 Taxa Dividida MÉDIA ({c1["dir"]}) — divisão de preço',
            'nivel'  : 2,
        }

    # Taxa Dividida simples: apenas final de taxa detectado no candle -2
    if f1 and not c0b:
        return {
            'tipo'  : 'taxa_dividida_fraca',
            'dir'   : f1['dir'],
            'forca' : 'FRACA',
            'desc'  : f'📊 Taxa Dividida FRACA ({f1["dir"]})',
            'nivel' : 3,
        }

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ABERTURA E FECHAMENTO DE LOTE
# ═══════════════════════════════════════════════════════════════════════════════

def detect_lote(opens: np.ndarray, highs: np.ndarray,
                lows: np.ndarray, closes: np.ndarray,
                lookback: int = 20) -> dict:
    """
    Identifica o lote ativo (macro-lote).

    Um lote INICIA em um ponto de reversão.
    Um lote FECHA quando o preço retorna ao ponto de abertura do lote.

    Retorna:
      abertura_lote  : preço onde o lote foi aberto
      fechamento_lote: preço alvo de fechamento (ponto de abertura anterior)
      dir_lote       : 'up' | 'down'
      progresso      : % do percurso já feito (0-100)
      proximo_alvo   : próximo nível chave (50% do lote)
      status         : 'aberto' | 'proximo_fechamento' | 'fechado'
    """
    n = min(lookback, len(closes))
    seg = closes[-n:]
    h_seg = highs[-n:]
    l_seg = lows[-n:]

    idx_max = int(np.argmax(h_seg))
    idx_min = int(np.argmin(l_seg))
    price   = float(closes[-1])

    # Último ponto de reversão significativo
    if idx_max > idx_min:
        # Topo ocorreu depois do fundo → tendência de BAIXA (lote de baixa)
        abertura = float(h_seg[idx_max])
        fechamento_alvo = float(l_seg[idx_min]) if idx_min < idx_max else float(np.min(l_seg))
        dir_lote = 'down'
        progresso = max(0, min(100, (abertura - price) / (abertura - fechamento_alvo + 1e-9) * 100))
    else:
        # Fundo ocorreu depois do topo → tendência de ALTA (lote de alta)
        abertura = float(l_seg[idx_min])
        fechamento_alvo = float(h_seg[idx_max]) if idx_max < idx_min else float(np.max(h_seg))
        dir_lote = 'up'
        progresso = max(0, min(100, (price - abertura) / (fechamento_alvo - abertura + 1e-9) * 100))

    meio_lote = (abertura + fechamento_alvo) / 2

    if progresso >= 90:
        status = 'proximo_fechamento'
    elif progresso >= 50:
        status = 'metade'
    else:
        status = 'aberto'

    return {
        'abertura_lote'   : round(abertura, 6),
        'fechamento_alvo' : round(fechamento_alvo, 6),
        'meio_lote'       : round(meio_lote, 6),
        'dir_lote'        : dir_lote,
        'progresso'       : round(progresso, 1),
        'status'          : status,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6. PRIMEIRO REGISTRO (Nova Instalação de Preço)
# ═══════════════════════════════════════════════════════════════════════════════


def detect_primeiro_registro(opens: np.ndarray, highs: np.ndarray,
                               lows: np.ndarray, closes: np.ndarray) -> Optional[dict]:
    """
    Primeiro Registro = primeiro candle de um NOVO movimento/tendência.
    É a "nova instalação de preço" — o candle que iniciou o movimento atual.

    Ajustes desta versão:
      - mantém campos legados por compatibilidade
      - adiciona campos explícitos do candle (abertura_real/fechamento_real)
      - expõe niveis_operacionais sem nomes ambíguos
      - evita sinal quando a confirmação atual contradiz fortemente o registro
    """
    if len(closes) < 4:
        return None

    prev_bullish = closes[-3] > opens[-3]
    curr_bullish = closes[-2] > opens[-2]
    new_bullish  = closes[-1] > opens[-1]

    reversao = (prev_bullish != curr_bullish)

    # Gap mais conservador; em M1 gaps reais são raros, então qualquer gap relevante deve alertar.
    gap_sup = opens[-1] > closes[-2] * 1.0003
    gap_inf = opens[-1] < closes[-2] * 0.9997

    if not reversao and not gap_sup and not gap_inf:
        return None

    o, h, l, c = float(opens[-2]), float(highs[-2]), float(lows[-2]), float(closes[-2])
    rng = h - l
    if rng < 1e-10:
        return None

    meio = (o + c) / 2
    dir_nova = 'CALL' if c > o else 'PUT'
    confirma = (new_bullish and dir_nova == 'CALL') or ((not new_bullish) and dir_nova == 'PUT')

    nivel_corpo_topo = max(o, c)
    nivel_corpo_fundo = min(o, c)

    resultado = {
        'tipo'             : 'primeiro_registro',
        'dir'              : dir_nova,
        'confirma'         : confirma,
        'abertura_real'    : round(o, 6),
        'fechamento_real'  : round(c, 6),
        'maxima'           : round(h, 6),
        'minima'           : round(l, 6),
        'nivel_50pct'      : round(meio, 6),
        'nivel_corpo_topo' : round(nivel_corpo_topo, 6),
        'nivel_corpo_fundo': round(nivel_corpo_fundo, 6),
        # Campos legados mantidos por compatibilidade com o restante do bot/front-end:
        'nivel_abertura'   : round(nivel_corpo_topo if dir_nova == 'PUT' else nivel_corpo_fundo, 6),
        'nivel_fechamento' : round(nivel_corpo_fundo if dir_nova == 'PUT' else nivel_corpo_topo, 6),
        'gap'              : 'superior' if gap_sup else ('inferior' if gap_inf else None),
        'desc'             : f'🆕 Novo Registro {"ALTA" if dir_nova=="CALL" else "BAIXA"} | 50%={meio:.5f}',
    }

    if gap_sup or gap_inf:
        resultado['desc'] += ' ⚠️ GAP — aguardar confirmação'
        resultado['gap_alerta'] = True
    else:
        resultado['gap_alerta'] = False

    return resultado


# ═══════════════════════════════════════════════════════════════════════════════
# 7. PRESSÃO DE PAVIO (Região de Demanda)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_pressao_pavio(opens: np.ndarray, highs: np.ndarray,
                          lows: np.ndarray, closes: np.ndarray,
                          lookback: int = 8) -> Optional[dict]:
    """
    Pressão de Pavio = vários pavios apontando para o mesmo lado.
    Indica região de demanda ativa (suporte ou resistência dinâmica).

    Compra: múltiplos pavios inferiores (vendedores rejeitando preços baixos)
    Venda : múltiplos pavios superiores (compradores rejeitados em preços altos)
    """
    n = min(lookback, len(opens))
    if n < 3:
        return None

    pavios_sup = 0
    pavios_inf = 0
    total_sup  = 0.0
    total_inf  = 0.0
    rng_medio  = 0.0

    for i in range(-n, 0):
        o = float(opens[i]);  h = float(highs[i])
        l = float(lows[i]);   c = float(closes[i])
        rng = h - l
        if rng < 1e-10:
            continue
        ps = (h - max(o, c)) / rng
        pi = (min(o, c) - l) / rng
        if ps > 0.15:
            pavios_sup += 1
            total_sup  += ps
        if pi > 0.15:
            pavios_inf += 1
            total_inf  += pi
        rng_medio += rng

    rng_medio /= n
    limiar = n * 0.55  # maioria dos candles

    if pavios_inf >= limiar and pavios_inf > pavios_sup:
        return {
            'tipo'     : 'pressao_compra',
            'dir'      : 'CALL',
            'contagem' : pavios_inf,
            'forca'    : round(total_inf / pavios_inf, 3) if pavios_inf else 0,
            'desc'     : f'🔋 Pressão de Pavio de COMPRA ({pavios_inf}/{n} velas)',
        }

    if pavios_sup >= limiar and pavios_sup > pavios_inf:
        return {
            'tipo'     : 'pressao_venda',
            'dir'      : 'PUT',
            'contagem' : pavios_sup,
            'forca'    : round(total_sup / pavios_sup, 3) if pavios_sup else 0,
            'desc'     : f'🔋 Pressão de Pavio de VENDA ({pavios_sup}/{n} velas)',
        }

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 8. NOVA ALTA / NOVA BAIXA
# ═══════════════════════════════════════════════════════════════════════════════

def detect_nova_alta_baixa(closes: np.ndarray, highs: np.ndarray,
                            lows: np.ndarray, lookback: int = 15) -> Optional[dict]:
    """
    Nova Alta: candle atual rompe a máxima anterior (continuação de alta)
    Nova Baixa: candle atual rompe a mínima anterior (continuação de baixa)

    Rompimento de máxima/mínima anterior = nova instalação de preço.
    """
    n = min(lookback, len(highs))
    if n < 3:
        return None

    prev_max = float(np.max(highs[-n:-1]))
    prev_min = float(np.min(lows[-n:-1]))
    curr_h   = float(highs[-1])
    curr_l   = float(lows[-1])
    curr_c   = float(closes[-1])

    if curr_h > prev_max:
        return {
            'tipo'     : 'nova_alta',
            'dir'      : 'CALL',
            'nivel'    : round(prev_max, 6),
            'rompimento': round(curr_h - prev_max, 6),
            'desc'     : f'🚀 NOVA ALTA — rompeu máx. anterior ({prev_max:.5f})',
        }

    if curr_l < prev_min:
        return {
            'tipo'     : 'nova_baixa',
            'dir'      : 'PUT',
            'nivel'    : round(prev_min, 6),
            'rompimento': round(prev_min - curr_l, 6),
            'desc'     : f'🔻 NOVA BAIXA — rompeu mín. anterior ({prev_min:.5f})',
        }

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 9. POSICIONAMENTO (Reversão vs Continuidade)
# ═══════════════════════════════════════════════════════════════════════════════


def detect_posicionamento(opens: np.ndarray, highs: np.ndarray,
                           lows: np.ndarray, closes: np.ndarray) -> dict:
    """
    Posicionamento = onde o Big Player está se posicionando.

    Ajustes desta versão:
      - remove dependências ociosas
      - usa o candle de comando para reforçar reversão/continuação
      - trata doji como indecisão
    """
    if len(closes) < 3:
        return {'tipo': 'indefinido', 'dir': None, 'desc': 'Dados insuficientes'}

    def _vela_dir(o: float, c: float) -> int:
        if c > o:
            return 1
        if c < o:
            return -1
        return 0

    d_prev = _vela_dir(float(opens[-3]), float(closes[-3]))
    d_curr = _vela_dir(float(opens[-2]), float(closes[-2]))
    d_now  = _vela_dir(float(opens[-1]), float(closes[-1]))

    o, h, l, c = float(opens[-1]), float(highs[-1]), float(lows[-1]), float(closes[-1])
    rng = h - l
    if rng < 1e-10 or d_now == 0:
        return {'tipo': 'indecisao', 'dir': None, 'desc': '⚖️ Indecisão — doji / range mínimo'}

    ps = (h - max(o, c)) / rng
    pi = (min(o, c) - l) / rng
    if ps < 0.05 and pi < 0.05:
        return {
            'tipo' : 'indecisao',
            'dir'  : None,
            'desc' : '⚖️ Indecisão — Candle Mágico (N1) aguardar confirmação',
        }

    cmd = detect_vela_comando(opens, highs, lows, closes, idx=-1)

    if d_curr != 0 and d_curr != d_now:
        nova_dir = 'CALL' if d_now > 0 else 'PUT'
        desc = f'🔄 Reversão de Posição → {"ALTA" if nova_dir=="CALL" else "BAIXA"}'
        if cmd and cmd.get('dir') == nova_dir:
            desc += ' com comando alinhado'
        return {'tipo': 'reversao', 'dir': nova_dir, 'desc': desc}

    if d_now > 0:
        desc = '➡️ Continuação de ALTA'
        if cmd and cmd.get('dir') == 'CALL':
            desc += ' com comando'
        return {'tipo': 'continuacao', 'dir': 'CALL', 'desc': desc}

    desc = '➡️ Continuação de BAIXA'
    if cmd and cmd.get('dir') == 'PUT':
        desc += ' com comando'
    return {'tipo': 'continuacao', 'dir': 'PUT', 'desc': desc}


# ═══════════════════════════════════════════════════════════════════════════════
# 10. DEFESA DO PREÇO (Bate-e-Volta em Marcações)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_defesa(opens: np.ndarray, highs: np.ndarray,
                  lows: np.ndarray, closes: np.ndarray,
                  ema5: float, ema10: float, ema50: float) -> Optional[dict]:
    """
    Defesa = quando o preço chega em uma marcação (EMA, taxa dividida, 50% de vela)
    e é REJEITADO (pavio longo naquele lado).

    Indica que o Big Player está defendendo aquela região.
    Entrada de pullback (retração) na mesma direção do movimento principal.
    """
    if len(closes) < 4:
        return None

    price = float(closes[-1])
    o, h, l, c = float(opens[-1]), float(highs[-1]), float(lows[-1]), float(closes[-1])
    rng = h - l
    if rng < 1e-10:
        return None

    pavio_sup = h - max(o, c)
    pavio_inf = min(o, c) - l
    tol = rng * 0.3

    # Defesa em EMA5 (mercado voltou pra EMA5 e rejeitou)
    if abs(l - ema5) < tol and pavio_inf > rng * 0.30 and c > o:
        return {
            'tipo' : 'defesa_ema5_alta',
            'dir'  : 'CALL',
            'nivel': round(ema5, 6),
            'desc' : f'🛡️ Defesa EMA5 ({ema5:.5f}) — pavio inf. rejeição → CALL',
        }

    if abs(h - ema5) < tol and pavio_sup > rng * 0.30 and c < o:
        return {
            'tipo' : 'defesa_ema5_baixa',
            'dir'  : 'PUT',
            'nivel': round(ema5, 6),
            'desc' : f'🛡️ Defesa EMA5 ({ema5:.5f}) — pavio sup. rejeição → PUT',
        }

    # Defesa em EMA50 (nível mais forte)
    tol50 = rng * 0.5
    if abs(l - ema50) < tol50 and pavio_inf > rng * 0.35 and c > o:
        return {
            'tipo' : 'defesa_ema50_alta',
            'dir'  : 'CALL',
            'nivel': round(ema50, 6),
            'desc' : f'🛡️🛡️ Defesa EMA50 ({ema50:.5f}) — suporte forte → CALL',
        }

    if abs(h - ema50) < tol50 and pavio_sup > rng * 0.35 and c < o:
        return {
            'tipo' : 'defesa_ema50_baixa',
            'dir'  : 'PUT',
            'nivel': round(ema50, 6),
            'desc' : f'🛡️🛡️ Defesa EMA50 ({ema50:.5f}) — resistência forte → PUT',
        }

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 11. ANÁLISE COMPLETA — LÓGICA DO PREÇO
# ═══════════════════════════════════════════════════════════════════════════════


def analisar_logica_preco(opens: np.ndarray, highs: np.ndarray,
                           lows: np.ndarray, closes: np.ndarray,
                           ema5: float, ema10: float, ema50: float) -> dict:
    """
    Executa TODA a análise da Lógica do Preço e retorna um dicionário
    com todos os sinais encontrados, pontuação e direção sugerida.

    Melhorias desta versão:
      - separa alertas informativos e bloqueantes
      - reduz duplicidade de score entre pavio dominante e pressão de pavio
      - torna o filtro de manipulação menos agressivo via tetos por categoria
      - mantém compatibilidade com a chave legada `alertas`
    """
    resultado = {
        'score_call'          : 0,
        'score_put'           : 0,
        'sinais'              : [],
        'alertas'             : [],   # compatibilidade
        'alertas_informativos': [],
        'alertas_bloqueantes' : [],
        'comando'             : None,
        'taxa_dividida'       : None,
        'primeiro_registro'   : None,
        'pressao_pavio'       : None,
        'nova_alta_baixa'     : None,
        'posicionamento'      : None,
        'defesa'              : None,
        'pavio_info'          : None,
        'lote'                : None,
    }

    sc = 0
    sp = 0

    def add_info(msg: str) -> None:
        if msg not in resultado['alertas_informativos']:
            resultado['alertas_informativos'].append(msg)

    def add_block(msg: str) -> None:
        if msg not in resultado['alertas_bloqueantes']:
            resultado['alertas_bloqueantes'].append(msg)

    # ── 1. Informações de Pavio ───────────────────────────────────────────
    pv = calc_pavio_info(opens, highs, lows, closes)
    resultado['pavio_info'] = pv
    wick_bias_dir = None
    if pv['pressao_compradora']:
        sc += 2
        wick_bias_dir = 'CALL'
        resultado['sinais'].append('🔋 Pressão de Compra (pavio inf. domina)')
    elif pv['pressao_vendedora']:
        sp += 2
        wick_bias_dir = 'PUT'
        resultado['sinais'].append('🔋 Pressão de Venda (pavio sup. domina)')

    # ── 2. Lote ───────────────────────────────────────────────────────────
    lote = detect_lote(opens, highs, lows, closes)
    resultado['lote'] = lote
    if lote['status'] == 'proximo_fechamento':
        add_info(f'⚠️ Lote próximo do fechamento ({lote["progresso"]:.0f}%) — aguardar novo lote')
    elif lote['dir_lote'] == 'up' and lote['status'] == 'aberto':
        sc += 1
    elif lote['dir_lote'] == 'down' and lote['status'] == 'aberto':
        sp += 1

    # ── 3. Vela de Comando ────────────────────────────────────────────────
    cmd = detect_vela_comando(opens, highs, lows, closes, idx=-1)
    resultado['comando'] = cmd
    if cmd:
        if cmd['nivel'] == 1:
            add_info('⚖️ Candle Mágico (N1) — indecisão, aguardar')
        elif cmd['dir'] == 'CALL':
            sc += 4
            resultado['sinais'].append(cmd['desc'])
        elif cmd['dir'] == 'PUT':
            sp += 4
            resultado['sinais'].append(cmd['desc'])

    # ── 4. Taxa Dividida ──────────────────────────────────────────────────
    td = detect_taxa_dividida(opens, highs, lows, closes)
    resultado['taxa_dividida'] = td
    if td:
        pontos = {'FORTE': 5, 'MÉDIA': 3, 'FRACA': 2}
        pts = pontos.get(td.get('forca', 'FRACA'), 2)
        if td['dir'] == 'CALL':
            sc += pts
        else:
            sp += pts
        resultado['sinais'].append(td['desc'])

    # ── 5. Primeiro Registro ──────────────────────────────────────────────
    pr = detect_primeiro_registro(opens, highs, lows, closes)
    resultado['primeiro_registro'] = pr
    if pr:
        if pr.get('gap_alerta'):
            add_info(pr['desc'])
        elif pr['confirma']:
            if pr['dir'] == 'CALL':
                sc += 3
            else:
                sp += 3
            resultado['sinais'].append(pr['desc'])
        else:
            add_info('⚠️ Primeiro Registro sem confirmação do candle atual')

    # ── 6. Pressão de Pavio ───────────────────────────────────────────────
    ppav = detect_pressao_pavio(opens, highs, lows, closes)
    resultado['pressao_pavio'] = ppav
    if ppav:
        pts = 3
        # evita dupla contagem integral quando o último candle já deu o mesmo viés
        if ppav['dir'] == wick_bias_dir:
            pts = 1
        if ppav['dir'] == 'CALL':
            sc += pts
        else:
            sp += pts
        resultado['sinais'].append(ppav['desc'])

    # ── 7. Nova Alta / Nova Baixa ─────────────────────────────────────────
    nab = detect_nova_alta_baixa(closes, highs, lows)
    resultado['nova_alta_baixa'] = nab
    if nab:
        if nab['dir'] == 'CALL':
            sc += 4
        else:
            sp += 4
        resultado['sinais'].append(nab['desc'])

    # ── 8. Posicionamento ─────────────────────────────────────────────────
    pos = detect_posicionamento(opens, highs, lows, closes)
    resultado['posicionamento'] = pos
    if pos['tipo'] == 'indecisao':
        add_info(pos['desc'])
    elif pos['tipo'] == 'reversao':
        if pos['dir'] == 'CALL':
            sc += 2
        else:
            sp += 2
        resultado['sinais'].append(pos['desc'])
    elif pos['tipo'] == 'continuacao':
        if pos['dir'] == 'CALL':
            sc += 1
        else:
            sp += 1

    # ── 9. Defesa ─────────────────────────────────────────────────────────
    dfn = detect_defesa(opens, highs, lows, closes, ema5, ema10, ema50)
    resultado['defesa'] = dfn
    if dfn:
        pts = 5 if 'ema50' in dfn['tipo'] else 3
        if dfn['dir'] == 'CALL':
            sc += pts
        else:
            sp += pts
        resultado['sinais'].append(dfn['desc'])

    # ═══════════════════════════════════════════════════════════════════════
    # BLOCO DE DETECÇÃO DE MANIPULAÇÃO
    # Ajustado com teto por categoria para evitar inflação de score.
    # ═══════════════════════════════════════════════════════════════════════
    _manip_score = 0
    _manip_flags = []
    _mc = {}

    _cat_common = 0
    _cat_abusive = 0
    _cat_suspicious = 0

    def _push_manip(key: str, pts: int, desc: str, flag: str, categoria: str, alerta: str = None) -> None:
        nonlocal _cat_common, _cat_abusive, _cat_suspicious
        _mc[key] = {'score': pts, 'desc': desc, 'categoria': categoria}
        _manip_flags.append(flag)
        if alerta:
            add_info(alerta)
        if categoria == 'comum':
            _cat_common += pts
        elif categoria == 'abusiva':
            _cat_abusive += pts
        else:
            _cat_suspicious += pts

    _n = len(closes)
    if _n >= 5:
        _c = closes
        _o = opens
        _h = highs
        _l = lows

        _corpos = [abs(_c[i] - _o[i]) for i in range(_n)]
        _ranges = [abs(_h[i] - _l[i]) for i in range(_n)]
        _pavs_sup = [_h[i] - max(_c[i], _o[i]) for i in range(_n)]
        _pavs_inf = [min(_c[i], _o[i]) - _l[i] for i in range(_n)]
        _diffs = [_c[i] - _c[i - 1] for i in range(1, _n)]

        _atr5 = (sum(_ranges[-6:-1]) / 5) if _n >= 6 and sum(_ranges[-6:-1]) > 0 else (_ranges[-1] or 0.0001)
        _body_avg5 = (sum(_corpos[-6:-1]) / 5) if _n >= 6 else (_corpos[-1] or 0.0001)
        _std5 = 0.0001
        if _n >= 6:
            _mean5 = sum(_diffs[-5:]) / 5 if len(_diffs) >= 5 else 0
            _var5 = sum((x - _mean5) ** 2 for x in (_diffs[-5:] if len(_diffs) >= 5 else _diffs)) / max(1, min(5, len(_diffs)))
            _std5 = _var5 ** 0.5 or 0.0001

        _corpo_ult = _corpos[-1] or 0.0001
        _pav_dom = max(_pavs_sup[-1], _pavs_inf[-1])

        if _pav_dom > 2.5 * _corpo_ult:
            _push_manip('wick_trap', 20, '🔴 Wick Trap — pavio anormalmente longo', '🔴 Wick Trap', 'comum',
                        '⚠️ Wick Trap — pavio > 2.5× corpo (manipulação de reversão)')

        if _n >= 3:
            _gap = abs(_o[-1] - _c[-2])
            _rprev = _ranges[-2] or 0.0001
            _dentro = (_l[-2] <= _c[-1] <= _h[-2])
            if _gap > 0.4 * _rprev and _dentro:
                _push_manip('fake_gap_fill', 18, '🔴 Fake Gap Fill — gap falso fechado', '🔴 Fake Gap Fill',
                            'comum', '⚠️ Fake Gap Fill — gap falso na abertura com fechamento reabsorvido')

        if _n >= 4 and len(_diffs) >= 3:
            _spike = abs(_diffs[-2]); _ret = abs(_diffs[-1])
            if _spike > 2.0 * _atr5 and (_diffs[-2] * _diffs[-1] < 0) and _ret > 0.5 * _spike:
                _push_manip('v_reversal', 25, '🔴 V-Reversal Spike — movimento em V', '🔴 V-Reversal Spike',
                            'comum', '⚠️ V-Reversal Spike — spike e reversão total suspeitos')

        if _n >= 5 and len(_diffs) >= 4:
            _run_up = sum(1 for i in range(2, 5) if len(_diffs) > i and _diffs[-i] > 0) >= 2
            _run_dn = sum(1 for i in range(2, 5) if len(_diffs) > i and _diffs[-i] < 0) >= 2
            if (_run_up and _diffs[-1] < -0.3 * _atr5) or (_run_dn and _diffs[-1] > 0.3 * _atr5):
                _push_manip('pump_dump', 18, '🔴 Pump & Dump — bombeamento + queda', '🔴 Pump & Dump',
                            'comum', '⚠️ Pump & Dump — bombeamento artificial seguido de queda abrupta')

        _ratio_corpo_range = _corpos[-1] / (_ranges[-1] or 0.0001)
        if _ranges[-1] > 1.5 * _atr5 and _ratio_corpo_range < 0.10:
            _push_manip('wash_trading', 15, '🔴 Wash Trading — range alto, corpo mínimo', '🔴 Wash Trading',
                        'comum', '⚠️ Wash Trading — alta amplitude com corpo mínimo (volume fictício)')

        if _n >= 9 and len(_diffs) >= 8:
            _fase1 = sum(1 for x in _diffs[-8:-5] if x > 0) >= 2
            _fase2 = sum(1 for x in _diffs[-5:-2] if x < 0) >= 2
            _fase3 = sum(1 for x in _diffs[-2:] if x > 0) >= 1
            if _fase1 and _fase2 and _fase3:
                _push_manip('pump_dump_cycle', 20, '🔴 Pump Dump Cycle — ciclo completo', '🔴 Pump Dump Cycle',
                            'comum', '⚠️ Pump Dump Cycle — ciclo completo de bombeamento detectado')

        if _ranges[-1] > 3.0 * _atr5 and _corpos[-1] < 0.3 * _ranges[-1]:
            _push_manip('otc_glitch', 30, '🟠 OTC Glitch — spike isolado', '🟠 OTC Glitch',
                        'abusiva', '⚠️ OTC Glitch — spike anormal de corretora (vela fantasma)')
        elif max(_pavs_sup[-1], _pavs_inf[-1]) > 2.5 * _atr5 and _corpos[-1] > 0.20 * _ranges[-1]:
            _push_manip('broker_spike', 28, '🟠 Broker Spike — pico isolado de corretora', '🟠 Broker Spike',
                        'abusiva', '⚠️ Broker Spike — pico isolado em período de baixa volatilidade')

        if _n >= 5:
            _tiny = sum(1 for i in range(-5, 0) if _corpos[i] < 0.15 * _atr5)
            if _tiny >= 4:
                _push_manip('spoof_detection', 22, '🟠 Spoof Detection — corpos mínimos consecutivos',
                            '🟠 Spoof Detection', 'abusiva',
                            '⚠️ Spoof Detection — sequência artificial de corpos mínimos')

        if _n >= 4 and len(_diffs) >= 3:
            _all_bear = all(_diffs[-(i + 1)] < 0 for i in range(3))
            _accel = abs(_diffs[-1]) > abs(_diffs[-2]) > abs(_diffs[-3])
            if _all_bear and _accel:
                _push_manip('liquidation_cascade', 25, '🟠 Liquidation Cascade — queda acelerada',
                            '🟠 Liquidation Cascade', 'abusiva',
                            '⚠️ Liquidation Cascade — cascata de vendas forçadas massivas')

        if _n >= 6:
            _max5 = max(_h[-6:-1]); _min5 = min(_l[-6:-1])
            _hunted_up = _h[-1] > _max5 * 1.001 and _c[-1] < _max5
            _hunted_dn = _l[-1] < _min5 * 0.999 and _c[-1] > _min5
            if _hunted_up or _hunted_dn:
                _push_manip('stop_loss_hunt', 28, '🟠 Stop Loss Hunting — caça a stops',
                            '🟠 Stop Loss Hunting', 'abusiva',
                            '⚠️ Stop Loss Hunting — spike além do range recente com reversão imediata')

        if _n >= 6:
            _atr_gbl_q = (sum(_ranges) / _n) or 0.0001
            _micro_ranges = sum(1 for i in range(-6, 0) if _ranges[i] < 0.08 * _atr_gbl_q)
            if _micro_ranges >= 4:
                _push_manip('quote_stuffing', 20, '🟠 Quote Stuffing — ordens fantasma',
                            '🟠 Quote Stuffing', 'abusiva',
                            '⚠️ Quote Stuffing — muitos candles com range mínimo (ordens fantasma)')

        if _n >= 4 and len(_diffs) >= 3:
            _crash_mag = abs(_diffs[-2]); _recovery = _diffs[-1]
            if _crash_mag > 1.5 * _std5 and (_diffs[-2] < 0) and (_recovery > 0.4 * _crash_mag):
                _push_manip('flash_crash', 30, '🟠 Flash Crash — queda relâmpago + recuperação',
                            '🟠 Flash Crash', 'abusiva',
                            '⚠️ Flash Crash — queda violenta com recuperação parcial suspeita')

        if _n >= 8 and len(_diffs) >= 7:
            _calm_std = (sum(abs(x) for x in _diffs[-7:-3]) / 4) or 0.0001
            _explosion = abs(_diffs[-3]); _fade = abs(_diffs[-1]) < 0.4 * _explosion
            if _explosion > 3.0 * _calm_std and _fade:
                _push_manip('momentum_ignition', 22, '🟠 Momentum Ignition — explosão + fade',
                            '🟠 Momentum Ignition', 'abusiva',
                            '⚠️ Momentum Ignition — explosão de movimento após calma + fade rápido')

        if _n >= 5:
            _bodies_dec = all(_corpos[-(i + 1)] < _corpos[-(i + 2)] * 0.95 for i in range(2, 5))
            _rev_brusca = _corpos[-1] > 1.3 * _corpos[-2]
            if _bodies_dec and _rev_brusca:
                _push_manip('layering', 20, '🟠 Layering — corpos decrescentes + spike final',
                            '🟠 Layering', 'abusiva',
                            '⚠️ Layering — compressão de corpos com reversão brusca (manipulação)')

        if _n >= 5 and len(_diffs) >= 4:
            _alt = sum(1 for i in range(-4, -1) if _diffs[i] * _diffs[i + 1] < 0)
            _small_spikes = all(abs(_diffs[i]) < 2.0 * _atr5 for i in range(-4, 0))
            if _alt >= 3 and _small_spikes:
                _push_manip('micro_spikes', 12, '🟣 Micro-spikes Repetitivos — alternância pequena',
                            '🟣 Micro-spike Repetitivo', 'suspeita',
                            '⚠️ Micro-spikes Repetitivos — alternâncias em pequena escala')

        _feed_err = False
        for _i in range(-min(3, _n), 0):
            if _h[_i] < _l[_i]:
                _feed_err = True
                break
            if not (_l[_i] <= _o[_i] <= _h[_i]) or not (_l[_i] <= _c[_i] <= _h[_i]):
                _feed_err = True
                break
        if _feed_err:
            _push_manip('feed_glitch', 35, '🟣 Feed Glitch — dados OHLC inconsistentes',
                        '🟣 Feed Glitch', 'suspeita',
                        '⚠️ Feed Glitch — dados de preço inconsistentes (high < low ou open fora do range)')

        if _corpos[-1] > 2.0 * _body_avg5 and _pavs_inf[-1] > _corpos[-1]:
            _push_manip('whale_accum', 12, '🟣 Whale Accumulation — compra institucional silenciosa',
                        '🟣 Whale Accumulation', 'suspeita')

        if _n >= 7 and len(_diffs) >= 6:
            _hft_alt = sum(1 for i in range(-6, -1) if _diffs[i] * _diffs[i + 1] < 0)
            if _hft_alt >= 5:
                _push_manip('hft', 15, '🟣 HFT — alternância de alta frequência',
                            '🟣 HFT — High Frequency Trading', 'suspeita',
                            '⚠️ High Frequency Trading — alternâncias de direção anormais')

        if _n >= 2:
            _ob_gap = abs(_o[-1] - _c[-2])
            if _ob_gap > 1.2 * _atr5:
                _push_manip('order_book_manip', 18, '🟣 Order Book Manipulation — gap anormal',
                            '🟣 Order Book Manipulation', 'suspeita',
                            '⚠️ Order Book Manipulation — gap de abertura > 1.2× ATR')

        if _ranges[-1] > 2.5 * _atr5 and (_corpos[-1] / (_ranges[-1] or 0.0001)) < 0.08:
            _push_manip('synthetic_vol', 18, '🟣 Synthetic Volume Spike — range largo + corpo mínimo',
                        '🟣 Synthetic Volume Spike', 'suspeita')

        if _n >= 3:
            _rb1 = _ranges[-2] or 0.0001
            _rb2 = _ranges[-3] or 0.0001
            _sim_range = 1 - abs(_rb1 - _rb2) / max(_rb1, _rb2)
            _sim_dir = ((_c[-2] - _o[-2]) * (_c[-3] - _o[-3])) > 0
            if _sim_range > 0.95 and _sim_dir:
                _push_manip('algo_manip', 15, '🟣 Algorithmic Manipulation — velas idênticas repetidas',
                            '🟣 Algorithmic Manipulation', 'suspeita')

        if _n >= 5:
            _spoof_dec = all(_corpos[-(i + 1)] < _corpos[-(i + 2)] * 0.85 for i in range(1, 4))
            if _spoof_dec:
                _push_manip('spoofing_serie', 16, '🟣 Spoofing em Série — compressão linear de corpos',
                            '🟣 Spoofing em Série', 'suspeita')

        if _n >= 2:
            _range_prev = _ranges[-2] or 0.0001
            if _c[-2] > _o[-2]:
                _overlap = max(0, _c[-2] - _c[-1]) / _range_prev
            else:
                _overlap = max(0, _c[-1] - _c[-2]) / _range_prev
            if _overlap > 0.80:
                _push_manip('rev_forcada', 14, '🟣 Reversão Forçada — nega > 80% da vela anterior',
                            '🟣 Reversão Forçada', 'suspeita')

        if _n >= 2:
            _rb = _ranges[-1] or 0.0001
            _rbp = _ranges[-2] or 0.0001
            _mirror_range = 1 - abs(_rb - _rbp) / max(_rb, _rbp)
            _mirror_dir = ((_c[-1] - _o[-1]) * (_c[-2] - _o[-2])) < 0
            if _mirror_range > 0.90 and _mirror_dir:
                _push_manip('candle_espelho', 14, '🟣 Candle Espelho — vela espelhada da anterior',
                            '🟣 Candle Espelho', 'suspeita')

        if _n >= 6 and len(_diffs) >= 5:
            _atr_global = (sum(_ranges) / _n) or 0.0001
            _ticks_micro = sum(1 for x in _diffs[-5:] if abs(x) < 0.05 * _atr_global)
            if _ticks_micro >= 5:
                _push_manip('tick_manip', 12, '🟣 Tick Manipulation — variação mínima repetida',
                            '🟣 Tick Manipulation', 'suspeita',
                            '⚠️ Tick Manipulation — variação mínima de preço suspeita')

        if _n >= 7:
            _ref_price = sum(_c[-7:]) / 7
            _anchor_dev = max(abs(_c[i] - _ref_price) for i in range(-7, 0))
            _atr_gbl = (sum(_ranges) / _n) or 0.0001
            if _anchor_dev < 0.05 * _atr_gbl:
                _push_manip('price_anchor', 10, '🟣 Price Anchoring — preço ancorado artificialmente',
                            '🟣 Price Anchoring', 'suspeita')

        if _n >= 7 and len(_diffs) >= 6:
            _cluster_ref = sum(_c[-7:-2]) / 5
            _tight_cluster = all(abs(_c[i] - _cluster_ref) < 0.03 * _atr5 for i in range(-7, -2))
            _explosion_final = abs(_diffs[-1]) > 2.0 * _atr5
            if _tight_cluster and _explosion_final:
                _push_manip('cluster_manip', 20, '🟣 Cluster Manipulation — consolidação + explosão',
                            '🟣 Cluster Manipulation', 'suspeita',
                            '⚠️ Cluster Manipulation — consolidação artificial + explosão final')

    # tetos por categoria para o mesmo evento não explodir a pontuação
    _cat_common = min(_cat_common, 30)
    _cat_abusive = min(_cat_abusive, 45)
    _cat_suspicious = min(_cat_suspicious, 20)

    _manip_score = min(100, _cat_common + _cat_abusive + _cat_suspicious)
    resultado['manip_score'] = _manip_score
    resultado['manip_flags'] = _manip_flags
    resultado['manip_cats'] = _mc
    resultado['manip_resumo'] = {
        'comum': _cat_common,
        'abusiva': _cat_abusive,
        'suspeita': _cat_suspicious,
    }

    _criticas = [k for k in _mc if k in (
        'otc_glitch', 'broker_spike', 'liquidation_cascade',
        'stop_loss_hunt', 'flash_crash', 'feed_glitch'
    )]

    if _manip_score >= 80 or len(_criticas) >= 3:
        add_block(f'🚫 MANIPULAÇÃO CRÍTICA ({_manip_score}/100) — {len(_manip_flags)} detectadas | Entrada bloqueada')
    elif _manip_score >= 55:
        sc = int(sc * 0.78)
        sp = int(sp * 0.78)
        add_info(f'⚠️ Manipulação Alta ({_manip_score}/100) — {len(_manip_flags)} detectadas | Score -30%')
    elif _manip_score >= 35:
        sc = int(sc * 0.90)
        sp = int(sp * 0.90)
        add_info(f'⚠️ Manipulação Moderada ({_manip_score}/100) — {len(_manip_flags)} detectadas | Score -15%')

    if _manip_flags:
        resultado['sinais'].append(
            f'🕵️ {len(_manip_flags)} manipulação(ões): ' + ' | '.join(_manip_flags[:3]) +
            (f' +{len(_manip_flags) - 3} mais' if len(_manip_flags) > 3 else '')
        )

    # ── Consolidar resultado ──────────────────────────────────────────────
    resultado['score_call'] = sc
    resultado['score_put'] = sp

    total = sc + sp
    if total < 3:
        resultado['direcao'] = None
        resultado['forca_lp'] = 0
        resultado['resumo'] = 'Lógica do Preço: sem sinal definido'
    elif sc > sp:
        raw = sc / total * 100
        resultado['direcao'] = 'CALL'
        resultado['forca_lp'] = min(99, int(raw + (sc - sp) * 2))
        resultado['resumo'] = f'📈 LP: CALL {resultado["forca_lp"]}% ({sc}pts CALL vs {sp}pts PUT)'
    elif sp > sc:
        raw = sp / total * 100
        resultado['direcao'] = 'PUT'
        resultado['forca_lp'] = min(99, int(raw + (sp - sc) * 2))
        resultado['resumo'] = f'📉 LP: PUT {resultado["forca_lp"]}% ({sp}pts PUT vs {sc}pts CALL)'
    else:
        resultado['direcao'] = None
        resultado['forca_lp'] = 0
        resultado['resumo'] = '⚖️ LP: Empate — sem sinal'

    resultado['alertas'] = resultado['alertas_bloqueantes'] + resultado['alertas_informativos']
    resultado['pode_entrar'] = (
        resultado['direcao'] is not None and
        len(resultado['alertas_bloqueantes']) == 0 and
        resultado['forca_lp'] >= 38
    )

    return resultado
