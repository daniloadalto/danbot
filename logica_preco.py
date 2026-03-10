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

    Identifica:
      - Reversão de posicionamento (mudança de cor da vela)
      - Primeiro candle após nova instalação de preço
      - Gap de abertura (preço abre acima/abaixo do fechamento anterior)

    Retorna informações sobre a nova instalação se detectada.
    """
    if len(closes) < 4:
        return None

    # Detecção de nova posição (mudança de cor)
    prev_bullish = closes[-3] > opens[-3]
    curr_bullish = closes[-2] > opens[-2]
    new_bullish  = closes[-1] > opens[-1]

    # Reversão na penúltima vela
    reversao = (prev_bullish != curr_bullish)

    # Gap de abertura (espaço entre fechamento anterior e abertura atual)
    gap_sup = opens[-1] > closes[-2] * 1.0003  # gap de alta
    gap_inf = opens[-1] < closes[-2] * 0.9997  # gap de baixa

    if not reversao and not gap_sup and not gap_inf:
        return None

    # Nível do primeiro registro
    o, h, l, c = float(opens[-2]), float(highs[-2]), float(lows[-2]), float(closes[-2])
    rng = h - l
    if rng < 1e-10:
        return None

    meio = (o + c) / 2
    dir_nova = 'CALL' if c > o else 'PUT'

    # Verificar se o candle atual confirma a nova direção
    confirma = (new_bullish and dir_nova == 'CALL') or (not new_bullish and dir_nova == 'PUT')

    resultado = {
        'tipo'          : 'primeiro_registro',
        'dir'           : dir_nova,
        'confirma'      : confirma,
        'nivel_abertura': round(o if dir_nova == 'PUT' else c, 6),  # nível de marcação
        'nivel_50pct'   : round(meio, 6),
        'nivel_fechamento': round(c if dir_nova == 'PUT' else o, 6),
        'gap'           : 'superior' if gap_sup else ('inferior' if gap_inf else None),
        'desc'          : f'🆕 Novo Registro {"ALTA" if dir_nova=="CALL" else "BAIXA"} '
                          f'| 50%={meio:.5f}',
    }

    # Se tem gap, alerta para aguardar (não operar em gap)
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

    Tipos:
      • REVERSÃO   : mudança de cor da vela (nova posição)
      • CONTINUAÇÃO: mesma cor, mesma direção
      • INDECISÃO  : variação de comando (careca dos dois lados)
    """
    if len(closes) < 3:
        return {'tipo': 'indefinido', 'dir': None, 'desc': 'Dados insuficientes'}

    c_prev = closes[-3] > opens[-3]   # True = bullish
    c_curr = closes[-2] > opens[-2]
    c_now  = closes[-1] > opens[-1]

    # Comando atual
    cmd = detect_vela_comando(opens, highs, lows, closes, idx=-1)

    # Variação (careca dos dois lados) = indecisão
    o, h, l, c = float(opens[-1]), float(highs[-1]), float(lows[-1]), float(closes[-1])
    rng = h - l
    if rng > 1e-10:
        ps = (h - max(o, c)) / rng
        pi = (min(o, c) - l) / rng
        if ps < 0.05 and pi < 0.05:
            return {
                'tipo' : 'indecisao',
                'dir'  : None,
                'desc' : '⚖️ Indecisão — Candle Mágico (N1) aguardar confirmação',
            }

    # Reversão de posição (mudou a cor)
    if c_curr != c_now:
        nova_dir = 'CALL' if c_now else 'PUT'
        return {
            'tipo' : 'reversao',
            'dir'  : nova_dir,
            'desc' : f'🔄 Reversão de Posição → {"ALTA" if nova_dir=="CALL" else "BAIXA"}',
        }

    # Continuação (mesma cor)
    if c_now:
        return {'tipo': 'continuacao', 'dir': 'CALL', 'desc': '➡️ Continuação de ALTA'}
    else:
        return {'tipo': 'continuacao', 'dir': 'PUT', 'desc': '➡️ Continuação de BAIXA'}


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

    Pesos de cada análise:
      Vela de Comando         : +4 pts (direção definida)
      Taxa Dividida FORTE     : +5 pts
      Taxa Dividida MÉDIA     : +3 pts
      Primeiro Registro confirmado : +3 pts
      Pressão de Pavio        : +3 pts
      Nova Alta/Baixa         : +4 pts
      Posicionamento Reversão : +2 pts
      Posicionamento Continuação: +1 pt
      Defesa EMA5             : +3 pts
      Defesa EMA50            : +5 pts
      Pavio dominante alinhado: +2 pts
    """
    resultado = {
        'score_call'       : 0,
        'score_put'        : 0,
        'sinais'           : [],
        'alertas'          : [],
        'comando'          : None,
        'taxa_dividida'    : None,
        'primeiro_registro': None,
        'pressao_pavio'    : None,
        'nova_alta_baixa'  : None,
        'posicionamento'   : None,
        'defesa'           : None,
        'pavio_info'       : None,
        'lote'             : None,
    }

    sc = 0  # score CALL
    sp = 0  # score PUT

    # ── 1. Informações de Pavio ───────────────────────────────────────────
    pv = calc_pavio_info(opens, highs, lows, closes)
    resultado['pavio_info'] = pv
    if pv['pressao_compradora']:
        sc += 2
        resultado['sinais'].append(f'🔋 Pressão de Compra (pavio inf. domina)')
    elif pv['pressao_vendedora']:
        sp += 2
        resultado['sinais'].append(f'🔋 Pressão de Venda (pavio sup. domina)')

    # ── 2. Lote ───────────────────────────────────────────────────────────
    lote = detect_lote(opens, highs, lows, closes)
    resultado['lote'] = lote
    if lote['status'] == 'proximo_fechamento':
        resultado['alertas'].append(f'⚠️ Lote próximo do fechamento ({lote["progresso"]:.0f}%) — aguardar novo lote')
    elif lote['dir_lote'] == 'up' and lote['status'] == 'aberto':
        sc += 1
    elif lote['dir_lote'] == 'down' and lote['status'] == 'aberto':
        sp += 1

    # ── 3. Vela de Comando ────────────────────────────────────────────────
    cmd = detect_vela_comando(opens, highs, lows, closes, idx=-1)
    resultado['comando'] = cmd
    if cmd:
        if cmd['nivel'] == 1:
            resultado['alertas'].append(f'⚖️ Candle Mágico (N1) — indecisão, aguardar')
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
            resultado['alertas'].append(pr['desc'])
        elif pr['confirma']:
            if pr['dir'] == 'CALL':
                sc += 3
            else:
                sp += 3
            resultado['sinais'].append(pr['desc'])

    # ── 6. Pressão de Pavio ───────────────────────────────────────────────
    ppav = detect_pressao_pavio(opens, highs, lows, closes)
    resultado['pressao_pavio'] = ppav
    if ppav:
        if ppav['dir'] == 'CALL':
            sc += 3
        else:
            sp += 3
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
        resultado['alertas'].append(pos['desc'])
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


    # ── 10b. Detecção de Manipulação (inspirado em Flame Trading) ─────────────
    # Identifica padrões de manipulação para penalizar score ou bloquear entrada
    # Baseado em: Wick Trap, V-Reversal Spike, Broker Spike (OTC Glitch), Pump & Dump
    _manip_score = 0  # 0-100: quanto maior, mais suspeito
    _manip_flags = []

    if len(closes) >= 5:
        _c = closes
        _o = opens
        _h = highs
        _l = lows

        # --- WICK TRAP: pavio > 2.5x corpo na última vela ---
        _corpo_ult = abs(_c[-1] - _o[-1]) or 0.0001
        _pavio_sup = _h[-1] - max(_c[-1], _o[-1])
        _pavio_inf = min(_c[-1], _o[-1]) - _l[-1]
        if max(_pavio_sup, _pavio_inf) > 2.5 * _corpo_ult:
            _manip_score += 20
            _manip_flags.append('⚠️ Wick Trap detectado')
            resultado['alertas'].append('⚠️ Wick Trap — pavio anormalmente longo (manipulação)')

        # --- V-REVERSAL SPIKE: queda brusca + recuperação total (ou vice-versa) ---
        if len(_c) >= 4:
            _dir_1 = _c[-3] - _c[-4]  # movimento anterior
            _dir_2 = _c[-2] - _c[-3]  # spike
            _dir_3 = _c[-1] - _c[-2]  # recuperação
            _spike_mag = abs(_dir_2)
            _prev_range = abs(_c[-4] - _c[-6]) if len(_c) >= 7 else _spike_mag * 0.5
            if _spike_mag > 2.0 * (_prev_range or 0.0001) and (_dir_2 * _dir_3 < 0):
                _manip_score += 25
                _manip_flags.append('⚠️ V-Reversal Spike')
                resultado['alertas'].append('⚠️ V-Reversal Spike — movimento em V suspeito')

        # --- OTC GLITCH / BROKER SPIKE: spike isolado em baixa volatilidade ---
        _ranges = [abs(_h[i] - _l[i]) for i in range(len(_h))]
        if len(_ranges) >= 6:
            _avg_range = sum(_ranges[-6:-1]) / 5 if sum(_ranges[-6:-1]) > 0 else 0.0001
            _last_range = _ranges[-1]
            if _last_range > 3.0 * _avg_range:
                _manip_score += 30
                _manip_flags.append('⚠️ OTC Glitch/Broker Spike')
                resultado['alertas'].append('⚠️ OTC Glitch — spike isolado em baixa volatilidade')

        # --- PUMP & DUMP: 3+ candles seguidas na mesma direção com último revertendo ---
        if len(_c) >= 5:
            _run_up = all(_c[-i-1] > _c[-i-2] for i in range(1, 4))
            _run_dn = all(_c[-i-1] < _c[-i-2] for i in range(1, 4))
            if (_run_up and _c[-1] < _c[-2]) or (_run_dn and _c[-1] > _c[-2]):
                _manip_score += 15
                _manip_flags.append('⚠️ Pump & Dump detectado')

        # --- FAKE GAP FILL: abertura com gap e fechamento dentro da vela anterior ---
        if len(_c) >= 3:
            _gap = abs(_o[-1] - _c[-2])
            _range_prev = abs(_h[-2] - _l[-2]) or 0.0001
            if _gap > 0.5 * _range_prev:
                _manip_score += 15
                _manip_flags.append('⚠️ Fake Gap Fill')

    # Score de manipulação reduz força do sinal
    resultado['manip_score'] = _manip_score
    resultado['manip_flags'] = _manip_flags

    # Se manipulação alta (>= 50), bloquear entrada
    if _manip_score >= 50:
        resultado['pode_entrar'] = False
        resultado['alertas'].append(f'🚫 Manipulação detectada ({_manip_score}/100) — entrada bloqueada')

    # Se manipulação moderada (20-49), penalizar score em 30%
    elif _manip_score >= 20:
        sc = int(sc * 0.7)
        sp = int(sp * 0.7)
        resultado['alertas'].append(f'⚠️ Manipulação moderada ({_manip_score}/100) — score penalizado')

    # ── 10. Consolidar resultado ──────────────────────────────────────────
    resultado['score_call'] = sc
    resultado['score_put']  = sp

    total = sc + sp
    if total < 3:
        resultado['direcao']   = None
        resultado['forca_lp']  = 0
        resultado['resumo']    = 'Lógica do Preço: sem sinal definido'
    elif sc > sp:
        raw = sc / total * 100
        resultado['direcao']  = 'CALL'
        resultado['forca_lp'] = min(99, int(raw + (sc - sp) * 2))
        resultado['resumo']   = f'📈 LP: CALL {resultado["forca_lp"]}% ({sc}pts CALL vs {sp}pts PUT)'
    elif sp > sc:
        raw = sp / total * 100
        resultado['direcao']  = 'PUT'
        resultado['forca_lp'] = min(99, int(raw + (sp - sc) * 2))
        resultado['resumo']   = f'📉 LP: PUT {resultado["forca_lp"]}% ({sp}pts PUT vs {sc}pts CALL)'
    else:
        resultado['direcao']  = None
        resultado['forca_lp'] = 0
        resultado['resumo']   = '⚖️ LP: Empate — sem sinal'

    # Alertas impedem entrada
    resultado['pode_entrar'] = (
        resultado['direcao'] is not None and
        len(resultado['alertas']) == 0 and
        resultado['forca_lp'] >= 40
    )

    return resultado
