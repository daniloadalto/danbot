from iqoptionapi.stable_api import IQ_Option
import time
import math
import logging
import traceback

# =========================
# CONFIG
# =========================
TIMEFRAME = 60
TOTAL_VELAS = 260
VELAS_VALIDACAO = 8
MIN_ENTRADAS = 10
ATUALIZAR_A_CADA = 60
MAX_RECONNECT = 5
PAUSA_ENTRE_ATIVOS = 0.15
PAUSA_ENTRE_VALIDACAO = 0.05

TIPOS_PRIORITARIOS = ["binary", "turbo"]

# =========================
# CONFIG INDICADORES
# =========================
RSI_PERIODO = 14
RSI_CALL_LIMITE = 35
RSI_PUT_LIMITE = 65

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

BB_PERIODO = 20
BB_DESVIO = 2.0

MM_PERIODO = 20

FILTROS = ["PURO", "RSI", "MACD", "BOLLINGER", "MM"]

ATIVOS_FALLBACK = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD", "USDCHF",
    "EURJPY", "EURGBP", "GBPJPY",
    "EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC", "AUDUSD-OTC", "NZDUSD-OTC",
    "USDCHF-OTC", "EURGBP-OTC", "EURJPY-OTC", "GBPJPY-OTC", "AUDCAD-OTC",
    "USDSGD-OTC", "USDHKD-OTC", "USDINR-OTC", "USDZAR-OTC"
]

# =========================
# PADRÕES
# =========================
PADROES_CLASSICOS = [
    {"nome": "martelo", "dir": "call", "tipo": "candle"},
    {"nome": "enforcado", "dir": "put", "tipo": "candle"},
    {"nome": "estrela cadente", "dir": "put", "tipo": "candle"},
    {"nome": "marubozu alta", "dir": "call", "tipo": "candle"},
    {"nome": "marubozu baixa", "dir": "put", "tipo": "candle"},
    {"nome": "harami", "dir": "call", "tipo": "multi"},
    {"nome": "harami cross", "dir": "call", "tipo": "multi"},
    {"nome": "engolfo alta", "dir": "call", "tipo": "multi"},
    {"nome": "engolfo baixa", "dir": "put", "tipo": "multi"},
    {"nome": "piercing line", "dir": "call", "tipo": "multi"},
    {"nome": "dark cloud cover", "dir": "put", "tipo": "multi"},
    {"nome": "estrela da manhã", "dir": "call", "tipo": "multi"},
    {"nome": "estrela da tarde", "dir": "put", "tipo": "multi"},
    {"nome": "3 soldados brancos", "dir": "call", "tipo": "multi"},
    {"nome": "3 corvos pretos", "dir": "put", "tipo": "multi"},
    {"nome": "3 métodos ascendentes", "dir": "call", "tipo": "multi"},
]

PADROES_AVANCADOS = [
    {"nome": "ombro cabeça ombro", "dir": "put", "tipo": "estrutura"},
    {"nome": "fundo duplo", "dir": "call", "tipo": "estrutura"},
    {"nome": "topo duplo", "dir": "put", "tipo": "estrutura"},
    {"nome": "fundo triplo", "dir": "call", "tipo": "estrutura"},
    {"nome": "cunha descendente", "dir": "call", "tipo": "estrutura"},
    {"nome": "cunha ascendente", "dir": "put", "tipo": "estrutura"},
    {"nome": "alargamento altista", "dir": "call", "tipo": "estrutura"},
    {"nome": "alargamento baixista", "dir": "put", "tipo": "estrutura"},
    {"nome": "bandeira altista", "dir": "call", "tipo": "estrutura"},
    {"nome": "bandeira baixista", "dir": "put", "tipo": "estrutura"},
    {"nome": "triangulo ascendente", "dir": "call", "tipo": "estrutura"},
    {"nome": "triangulo descendente", "dir": "put", "tipo": "estrutura"},
    {"nome": "retangulo altista", "dir": "call", "tipo": "estrutura"},
    {"nome": "retangulo baixista", "dir": "put", "tipo": "estrutura"},
    {"nome": "triangulo simetrico de alta", "dir": "call", "tipo": "estrutura"},
    {"nome": "triangulo simetrico de baixa", "dir": "put", "tipo": "estrutura"},
    {"nome": "cup and handle", "dir": "call", "tipo": "estrutura"},
]

PADROES_TODOS = PADROES_CLASSICOS + PADROES_AVANCADOS
PADROES_ATIVOS = PADROES_TODOS[:]

MODO_CATALOGACAO_ATUAL = "todos"
COOLDOWN_ESTRUTURA_BARRAS = 8

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

iq = None
EMAIL = ""
SENHA = ""
top_padroes = {}
ACTIVES_OPCODE = {}
ATIVOS_VALIDOS_CACHE = []

# =========================
# MENU
# =========================
def escolher_tipo_catalogacao():
    global PADROES_ATIVOS, MODO_CATALOGACAO_ATUAL

    print("\n📊 TIPO DE CATALOGAÇÃO")
    print("1 - Apenas padrões CLÁSSICOS")
    print("2 - Apenas padrões AVANÇADOS")
    print("3 - TODOS (clássicos + avançados)")

    while True:
        escolha = input("👉 Escolha: ").strip()

        if escolha == "1":
            PADROES_ATIVOS = PADROES_CLASSICOS[:]
            MODO_CATALOGACAO_ATUAL = "classicos"
            print(f"✅ Modo selecionado: CLÁSSICOS ({len(PADROES_ATIVOS)} padrões)")
            return
        elif escolha == "2":
            PADROES_ATIVOS = PADROES_AVANCADOS[:]
            MODO_CATALOGACAO_ATUAL = "avancados"
            print(f"✅ Modo selecionado: AVANÇADOS ({len(PADROES_ATIVOS)} padrões)")
            return
        elif escolha == "3":
            PADROES_ATIVOS = PADROES_TODOS[:]
            MODO_CATALOGACAO_ATUAL = "todos"
            print(f"✅ Modo selecionado: TODOS ({len(PADROES_ATIVOS)} padrões)")
            return
        else:
            print("❌ Opção inválida, tente novamente.")


def escolher_modo_execucao():
    print("\n🔍 MODO DE EXECUÇÃO")
    print("1 - Catalogar TODOS os ativos")
    print("2 - Buscar e catalogar UM ativo específico")

    while True:
        escolha = input("👉 Escolha: ").strip()

        if escolha == "1":
            return "todos", None
        elif escolha == "2":
            termo = input("Digite o nome do ativo (ex: EURUSD, GBPUSD-OTC): ").strip().upper()
            if termo:
                return "unico", termo
            print("❌ Digite um ativo válido.")
        else:
            print("❌ Opção inválida, tente novamente.")

# =========================
# CREDENCIAIS
# =========================
def pedir_credenciais():
    email = input("Digite seu email da IQ Option: ").strip()
    senha = input("Digite sua senha da IQ Option: ").strip()
    return email, senha

# =========================
# CONEXÃO
# =========================
def conectar_iq(email, senha):
    print("\n🔗 CONECTANDO...")
    api = IQ_Option(email, senha)

    try:
        api.set_max_reconnect(MAX_RECONNECT)
    except Exception as e:
        print(f"⚠️ set_max_reconnect indisponível nesta versão: {e}")

    try:
        status, reason = api.connect()
        print(f"📡 Status conexão: {status} | Motivo: {reason}")
    except Exception as e:
        print(f"❌ Exceção ao conectar: {e}")
        print(traceback.format_exc())
        return None

    if not status and str(reason).upper() == "2FA":
        print("🔐 2FA habilitado. Um código foi enviado por SMS.")
        codigo = input("Digite o código recebido: ").strip()
        try:
            status, reason = api.connect_2fa(codigo)
            print(f"📡 Status 2FA: {status} | Motivo: {reason}")
        except Exception as e:
            print(f"❌ Erro no 2FA: {e}")
            print(traceback.format_exc())
            return None

    if not status:
        print(f"❌ ERRO AO CONECTAR | Motivo: {reason}")
        return None

    time.sleep(2)

    try:
        conectado = api.check_connect()
        print(f"🔎 check_connect(): {conectado}")
    except Exception as e:
        print(f"❌ Erro ao verificar conexão: {e}")
        print(traceback.format_exc())
        return None

    if not conectado:
        print("❌ CONECTOU, MAS A SESSÃO NÃO FICOU ESTÁVEL")
        return None

    print("✅ CONECTADO\n")
    return api


def garantir_conexao():
    global iq, EMAIL, SENHA

    try:
        if iq is not None and iq.check_connect():
            return True
    except Exception:
        pass

    print("⚠️ Conexão perdida. Tentando reconectar...")

    for tentativa in range(1, MAX_RECONNECT + 1):
        try:
            status, reason = iq.connect()
            time.sleep(1)

            conectado = False
            try:
                conectado = iq.check_connect()
            except Exception:
                conectado = False

            if status and conectado:
                print(f"✅ Reconectado na tentativa {tentativa}")
                return True

            print(f"⚠️ Tentativa {tentativa}/{MAX_RECONNECT} falhou | Motivo: {reason}")
        except Exception as e:
            print(f"⚠️ Erro ao reconectar ({tentativa}/{MAX_RECONNECT}): {e}")
            time.sleep(2)

    print("🔁 Tentando recriar sessão...")
    novo = conectar_iq(EMAIL, SENHA)
    if novo is not None:
        iq = novo
        return True

    return False

# =========================
# MAPA DE ATIVOS
# =========================
def atualizar_opcode_map():
    global ACTIVES_OPCODE

    ACTIVES_OPCODE = {}

    if not garantir_conexao():
        return {}

    try:
        iq.update_ACTIVES_OPCODE()
        time.sleep(1)
    except Exception as e:
        print(f"⚠️ update_ACTIVES_OPCODE falhou: {e}")

    try:
        mapa = iq.get_all_ACTIVES_OPCODE()
        if isinstance(mapa, dict):
            ACTIVES_OPCODE = mapa
    except Exception as e:
        print(f"⚠️ get_all_ACTIVES_OPCODE falhou: {e}")

    print(f"🧩 Ativos conhecidos no opcode map: {len(ACTIVES_OPCODE)}")
    return ACTIVES_OPCODE


def ativo_existe_no_opcode(ativo):
    return ativo in ACTIVES_OPCODE

# =========================
# MERCADO / DESCOBERTA
# =========================
def descobrir_ativos_abertos():
    if not garantir_conexao():
        return []

    try:
        todos = iq.get_all_open_time()
    except Exception as e:
        print(f"⚠️ Erro ao buscar ativos abertos: {e}")
        return []

    candidatos = []

    for tipo in TIPOS_PRIORITARIOS:
        bloco = todos.get(tipo, {})
        if not isinstance(bloco, dict):
            continue

        for nome, dados in bloco.items():
            try:
                if isinstance(dados, dict) and dados.get("open", False):
                    candidatos.append(nome)
            except Exception:
                continue

    vistos = set()
    unicos = []
    for ativo in candidatos:
        if ativo not in vistos:
            vistos.add(ativo)
            unicos.append(ativo)

    return unicos


def pegar_velas(ativo, quantidade=TOTAL_VELAS):
    if not garantir_conexao():
        return []

    try:
        velas = iq.get_candles(ativo, TIMEFRAME, quantidade, time.time() - 1)
        if not isinstance(velas, list):
            return []
        return sorted(velas, key=lambda x: x["from"])
    except Exception:
        return []


def validar_ativos(candidatos):
    validos = []
    rejeitados_opcode = []
    rejeitados_sem_vela = []

    print(f"🧪 Validando ativos para get_candles... ({len(candidatos)} candidatos)")

    for ativo in candidatos:
        if not ativo_existe_no_opcode(ativo):
            rejeitados_opcode.append(ativo)
            continue

        velas = pegar_velas(ativo, VELAS_VALIDACAO)
        if len(velas) >= VELAS_VALIDACAO:
            validos.append(ativo)
        else:
            rejeitados_sem_vela.append(ativo)

        time.sleep(PAUSA_ENTRE_VALIDACAO)

    print(f"✅ Ativos válidos para catalogação: {len(validos)}")
    print(f"⛔ Fora do opcode map: {len(rejeitados_opcode)}")
    print(f"⚠️ Sem velas suficientes: {len(rejeitados_sem_vela)}")

    if rejeitados_opcode:
        print("Exemplos fora do opcode:", ", ".join(rejeitados_opcode[:10]))
    if rejeitados_sem_vela:
        print("Exemplos sem velas:", ", ".join(rejeitados_sem_vela[:10]))

    return validos


def montar_rotacao_ativos():
    global ATIVOS_VALIDOS_CACHE

    atualizar_opcode_map()
    candidatos = descobrir_ativos_abertos()

    if not candidatos:
        print("⚠️ Não foi possível descobrir ativos automaticamente. Tentando fallback.")
        candidatos = ATIVOS_FALLBACK[:]

    validos = validar_ativos(candidatos)

    if len(validos) < 5:
        print("⚠️ Poucos ativos válidos. Reforçando com fallback.")
        reforco = []
        for ativo in ATIVOS_FALLBACK:
            if ativo_existe_no_opcode(ativo):
                velas = pegar_velas(ativo, VELAS_VALIDACAO)
                if len(velas) >= VELAS_VALIDACAO and ativo not in validos:
                    reforco.append(ativo)
            time.sleep(PAUSA_ENTRE_VALIDACAO)
        validos.extend(reforco)

    vistos = set()
    final = []
    for ativo in validos:
        if ativo not in vistos:
            vistos.add(ativo)
            final.append(ativo)

    ATIVOS_VALIDOS_CACHE = final[:]
    return final


def buscar_ativo_especifico(termo):
    atualizar_opcode_map()
    candidatos = descobrir_ativos_abertos()

    if not candidatos:
        candidatos = ATIVOS_FALLBACK[:]

    termo = termo.upper().strip()

    if termo in candidatos:
        return termo

    if termo in ACTIVES_OPCODE:
        velas = pegar_velas(termo, VELAS_VALIDACAO)
        if len(velas) >= VELAS_VALIDACAO:
            return termo

    correspondencias = [a for a in candidatos if termo in a.upper()]

    if len(correspondencias) == 1:
        return correspondencias[0]

    if len(correspondencias) > 1:
        print("\n🔎 Encontrados vários ativos parecidos:")
        for i, ativo in enumerate(correspondencias[:20], 1):
            print(f"{i} - {ativo}")

        while True:
            escolha = input("👉 Escolha o número do ativo: ").strip()
            if escolha.isdigit():
                idx = int(escolha)
                if 1 <= idx <= len(correspondencias[:20]):
                    return correspondencias[idx - 1]
            print("❌ Opção inválida.")

    return None

# =========================
# UTILITÁRIOS DE CANDLE
# =========================
def candle_metrics(v):
    o = float(v["open"])
    c = float(v["close"])
    h = float(v["max"])
    l = float(v["min"])

    body = abs(c - o)
    rng = max(h - l, 1e-8)
    upper = h - max(o, c)
    lower = min(o, c) - l
    bull = c > o
    bear = c < o

    return {
        "open": o, "close": c, "high": h, "low": l,
        "body": body, "range": rng,
        "upper": max(upper, 0.0), "lower": max(lower, 0.0),
        "bull": bull, "bear": bear,
        "body_pct": body / rng,
        "upper_pct": max(upper, 0.0) / rng,
        "lower_pct": max(lower, 0.0) / rng,
        "mid": (o + c) / 2.0
    }


def is_small_body(m, max_pct=0.25):
    return m["body_pct"] <= max_pct


def is_long_body(m, min_pct=0.55):
    return m["body_pct"] >= min_pct


def is_doji(m):
    return m["body_pct"] <= 0.10


def is_marubozu_bull(m):
    return m["bull"] and m["body_pct"] >= 0.85 and m["upper_pct"] <= 0.07 and m["lower_pct"] <= 0.07


def is_marubozu_bear(m):
    return m["bear"] and m["body_pct"] >= 0.85 and m["upper_pct"] <= 0.07 and m["lower_pct"] <= 0.07


def is_hammer(m):
    return (
        is_small_body(m, 0.35)
        and m["lower"] >= m["body"] * 2.2
        and m["upper"] <= m["body"] * 0.6
        and m["lower_pct"] >= 0.45
    )


def is_hanging_man(m):
    return is_hammer(m)


def is_shooting_star(m):
    return (
        is_small_body(m, 0.35)
        and m["upper"] >= m["body"] * 2.2
        and m["lower"] <= m["body"] * 0.6
        and m["upper_pct"] >= 0.45
    )


def bullish_engulfing(prev, curr):
    prev_low_body = min(prev["open"], prev["close"])
    prev_high_body = max(prev["open"], prev["close"])
    curr_low_body = min(curr["open"], curr["close"])
    curr_high_body = max(curr["open"], curr["close"])
    return (
        prev["bear"] and curr["bull"]
        and curr_low_body <= prev_low_body
        and curr_high_body >= prev_high_body
        and curr["body"] > prev["body"] * 1.05
    )


def bearish_engulfing(prev, curr):
    prev_low_body = min(prev["open"], prev["close"])
    prev_high_body = max(prev["open"], prev["close"])
    curr_low_body = min(curr["open"], curr["close"])
    curr_high_body = max(curr["open"], curr["close"])
    return (
        prev["bull"] and curr["bear"]
        and curr_low_body <= prev_low_body
        and curr_high_body >= prev_high_body
        and curr["body"] > prev["body"] * 1.05
    )


def bullish_harami(prev, curr):
    prev_low_body = min(prev["open"], prev["close"])
    prev_high_body = max(prev["open"], prev["close"])
    curr_low_body = min(curr["open"], curr["close"])
    curr_high_body = max(curr["open"], curr["close"])
    return (
        prev["bear"]
        and is_long_body(prev, 0.5)
        and curr["bull"]
        and curr_low_body >= prev_low_body
        and curr_high_body <= prev_high_body
        and curr["body"] < prev["body"] * 0.7
    )


def bullish_harami_cross(prev, curr):
    prev_low_body = min(prev["open"], prev["close"])
    prev_high_body = max(prev["open"], prev["close"])
    curr_low_body = min(curr["open"], curr["close"])
    curr_high_body = max(curr["open"], curr["close"])
    return (
        prev["bear"]
        and is_long_body(prev, 0.5)
        and is_doji(curr)
        and curr_low_body >= prev_low_body
        and curr_high_body <= prev_high_body
    )


def piercing_line(prev, curr):
    return (
        prev["bear"] and is_long_body(prev, 0.5)
        and curr["bull"] and is_long_body(curr, 0.45)
        and curr["close"] > prev["mid"]
        and curr["close"] < prev["open"]
    )


def dark_cloud_cover(prev, curr):
    return (
        prev["bull"] and is_long_body(prev, 0.5)
        and curr["bear"] and is_long_body(curr, 0.45)
        and curr["close"] < prev["mid"]
        and curr["close"] > prev["open"]
    )


def morning_star(a, b, c):
    return (
        a["bear"] and is_long_body(a, 0.5)
        and is_small_body(b, 0.30)
        and c["bull"] and is_long_body(c, 0.45)
        and c["close"] > a["mid"]
    )


def evening_star(a, b, c):
    return (
        a["bull"] and is_long_body(a, 0.5)
        and is_small_body(b, 0.30)
        and c["bear"] and is_long_body(c, 0.45)
        and c["close"] < a["mid"]
    )


def three_white_soldiers(a, b, c):
    return (
        a["bull"] and b["bull"] and c["bull"]
        and is_long_body(a, 0.45) and is_long_body(b, 0.45) and is_long_body(c, 0.45)
        and b["close"] > a["close"] and c["close"] > b["close"]
        and b["open"] >= a["open"] - a["body"] * 0.4
        and c["open"] >= b["open"] - b["body"] * 0.4
    )


def three_black_crows(a, b, c):
    return (
        a["bear"] and b["bear"] and c["bear"]
        and is_long_body(a, 0.45) and is_long_body(b, 0.45) and is_long_body(c, 0.45)
        and b["close"] < a["close"] and c["close"] < b["close"]
        and b["open"] <= a["open"] + a["body"] * 0.4
        and c["open"] <= b["open"] + b["body"] * 0.4
    )


def rising_three_methods(a, b, c, d):
    return (
        a["bull"] and d["bull"]
        and is_long_body(a, 0.5) and is_long_body(d, 0.5)
        and b["bear"] and c["bear"]
        and b["high"] <= a["high"] and b["low"] >= a["low"]
        and c["high"] <= a["high"] and c["low"] >= a["low"]
        and d["close"] > a["close"]
    )

# =========================
# UTILITÁRIOS ESTRUTURAIS
# =========================
def closes(window):
    return [float(v["close"]) for v in window]

def highs(window):
    return [float(v["max"]) for v in window]

def lows(window):
    return [float(v["min"]) for v in window]

def linear_slope(values):
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den else 0.0


def rel_tol(a, b):
    base = max(abs(a), abs(b), 1e-8)
    return abs(a - b) / base


def local_extremes(vals):
    tops, bottoms = [], []
    for i in range(1, len(vals) - 1):
        if vals[i] > vals[i - 1] and vals[i] > vals[i + 1]:
            tops.append((i, vals[i]))
        if vals[i] < vals[i - 1] and vals[i] < vals[i + 1]:
            bottoms.append((i, vals[i]))
    return tops, bottoms


def double_bottom(window):
    ls = lows(window)
    hs = highs(window)
    tops, bottoms = local_extremes(ls)
    if len(bottoms) < 2:
        return False
    b1, b2 = bottoms[-2], bottoms[-1]
    if rel_tol(b1[1], b2[1]) > 0.015:
        return False
    mid_high = max(hs[b1[0]:b2[0] + 1]) if b2[0] > b1[0] else max(hs)
    last_close = closes(window)[-1]
    return last_close > mid_high * 0.995


def double_top(window):
    hs = highs(window)
    ls = lows(window)
    tops, bottoms = local_extremes(hs)
    if len(tops) < 2:
        return False
    t1, t2 = tops[-2], tops[-1]
    if rel_tol(t1[1], t2[1]) > 0.015:
        return False
    mid_low = min(ls[t1[0]:t2[0] + 1]) if t2[0] > t1[0] else min(ls)
    last_close = closes(window)[-1]
    return last_close < mid_low * 1.005


def triple_bottom(window):
    ls = lows(window)
    hs = highs(window)
    tops, bottoms = local_extremes(ls)
    if len(bottoms) < 3:
        return False
    b = bottoms[-3:]
    values = [x[1] for x in b]
    if max(values) == 0:
        return False
    if (max(values) - min(values)) / max(values) > 0.02:
        return False
    left, right = b[0][0], b[-1][0]
    neckline = max(hs[left:right + 1])
    return closes(window)[-1] > neckline * 0.995


def head_shoulders(window):
    hs = highs(window)
    tops, bottoms = local_extremes(hs)
    if len(tops) < 3:
        return False
    s1, head, s2 = tops[-3], tops[-2], tops[-1]
    if not (head[1] > s1[1] * 1.01 and head[1] > s2[1] * 1.01):
        return False
    if rel_tol(s1[1], s2[1]) > 0.03:
        return False
    last_close = closes(window)[-1]
    neckline_approx = min(lows(window)[s1[0]:s2[0] + 1])
    return last_close < neckline_approx * 1.005


def falling_wedge(window):
    hs = highs(window)
    ls = lows(window)
    slope_h = linear_slope(hs)
    slope_l = linear_slope(ls)
    width_start = hs[0] - ls[0]
    width_end = hs[-1] - ls[-1]
    return slope_h < 0 and slope_l < 0 and abs(slope_l) < abs(slope_h) and width_end < width_start * 0.9


def rising_wedge(window):
    hs = highs(window)
    ls = lows(window)
    slope_h = linear_slope(hs)
    slope_l = linear_slope(ls)
    width_start = hs[0] - ls[0]
    width_end = hs[-1] - ls[-1]
    return slope_h > 0 and slope_l > 0 and abs(slope_h) < abs(slope_l) and width_end < width_start * 0.9


def broadening_bullish(window):
    hs = highs(window)
    ls = lows(window)
    slope_h = linear_slope(hs)
    slope_l = linear_slope(ls)
    width_start = hs[0] - ls[0]
    width_end = hs[-1] - ls[-1]
    return slope_h > 0 and slope_l < 0 and width_end > width_start * 1.15 and closes(window)[-1] > closes(window)[0]


def broadening_bearish(window):
    hs = highs(window)
    ls = lows(window)
    slope_h = linear_slope(hs)
    slope_l = linear_slope(ls)
    width_start = hs[0] - ls[0]
    width_end = hs[-1] - ls[-1]
    return slope_h > 0 and slope_l < 0 and width_end > width_start * 1.15 and closes(window)[-1] < closes(window)[0]


def bullish_flag(window):
    cl = closes(window)
    first = cl[: max(5, len(cl) // 3)]
    last = cl[-max(5, len(cl) // 3):]
    slope_first = linear_slope(first)
    slope_last = linear_slope(last)
    return slope_first > 0 and slope_last <= 0 and cl[-1] > sum(cl) / len(cl)


def bearish_flag(window):
    cl = closes(window)
    first = cl[: max(5, len(cl) // 3)]
    last = cl[-max(5, len(cl) // 3):]
    slope_first = linear_slope(first)
    slope_last = linear_slope(last)
    return slope_first < 0 and slope_last >= 0 and cl[-1] < sum(cl) / len(cl)


def ascending_triangle(window):
    hs = highs(window)
    ls = lows(window)
    top_band = max(hs[-8:])
    near_top_count = sum(1 for x in hs[-12:] if rel_tol(x, top_band) <= 0.01)
    return near_top_count >= 3 and linear_slope(ls[-12:]) > 0


def descending_triangle(window):
    hs = highs(window)
    ls = lows(window)
    low_band = min(ls[-8:])
    near_low_count = sum(1 for x in ls[-12:] if rel_tol(x, low_band) <= 0.01)
    return near_low_count >= 3 and linear_slope(hs[-12:]) < 0


def bullish_rectangle(window):
    hs = highs(window)
    ls = lows(window)
    high_span = max(hs) - min(hs)
    low_span = max(ls) - min(ls)
    avg_close = sum(closes(window)) / len(window)
    return high_span / avg_close < 0.03 and low_span / avg_close < 0.03 and closes(window)[-1] > avg_close


def bearish_rectangle(window):
    hs = highs(window)
    ls = lows(window)
    high_span = max(hs) - min(hs)
    low_span = max(ls) - min(ls)
    avg_close = sum(closes(window)) / len(window)
    return high_span / avg_close < 0.03 and low_span / avg_close < 0.03 and closes(window)[-1] < avg_close


def symmetrical_triangle_bull(window):
    hs = highs(window)
    ls = lows(window)
    return linear_slope(hs) < 0 and linear_slope(ls) > 0 and closes(window)[-1] > closes(window)[-3]


def symmetrical_triangle_bear(window):
    hs = highs(window)
    ls = lows(window)
    return linear_slope(hs) < 0 and linear_slope(ls) > 0 and closes(window)[-1] < closes(window)[-3]


def cup_and_handle(window):
    cl = closes(window)
    n = len(cl)
    if n < 20:
        return False
    left = cl[: n // 3]
    middle = cl[n // 3: 2 * n // 3]
    right = cl[2 * n // 3:]
    if not left or not middle or not right:
        return False
    left_high = max(left)
    mid_low = min(middle)
    right_high = max(right)
    tail = right[-max(3, len(right) // 3):]
    if not tail:
        return False
    return (
        left_high > mid_low * 1.03
        and right_high >= left_high * 0.97
        and linear_slope(tail) <= 0.0
        and cl[-1] >= sum(right) / len(right)
    )

# =========================
# INDICADORES
# =========================
def sma_lista(valores, periodo):
    out = [None] * len(valores)
    if periodo <= 0:
        return out
    soma = 0.0
    for i, v in enumerate(valores):
        soma += v
        if i >= periodo:
            soma -= valores[i - periodo]
        if i >= periodo - 1:
            out[i] = soma / periodo
    return out


def ema_lista(valores, periodo):
    out = [None] * len(valores)
    if not valores or periodo <= 0:
        return out

    k = 2 / (periodo + 1)
    ema_atual = None

    for i, v in enumerate(valores):
        if i == periodo - 1:
            ema_atual = sum(valores[:periodo]) / periodo
            out[i] = ema_atual
        elif i >= periodo:
            ema_atual = (v - ema_atual) * k + ema_atual
            out[i] = ema_atual

    return out


def calc_rsi(closes_list, periodo=14):
    rsi = [None] * len(closes_list)
    if len(closes_list) <= periodo:
        return rsi

    ganhos = [0.0] * len(closes_list)
    perdas = [0.0] * len(closes_list)

    for i in range(1, len(closes_list)):
        delta = closes_list[i] - closes_list[i - 1]
        ganhos[i] = max(delta, 0.0)
        perdas[i] = max(-delta, 0.0)

    avg_gain = sum(ganhos[1:periodo + 1]) / periodo
    avg_loss = sum(perdas[1:periodo + 1]) / periodo

    if avg_loss == 0:
        rsi[periodo] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[periodo] = 100 - (100 / (1 + rs))

    for i in range(periodo + 1, len(closes_list)):
        avg_gain = ((avg_gain * (periodo - 1)) + ganhos[i]) / periodo
        avg_loss = ((avg_loss * (periodo - 1)) + perdas[i]) / periodo

        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100 - (100 / (1 + rs))

    return rsi


def calc_macd(closes_list, fast=12, slow=26, signal=9):
    ema_fast = ema_lista(closes_list, fast)
    ema_slow = ema_lista(closes_list, slow)

    macd_line = [None] * len(closes_list)
    for i in range(len(closes_list)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]

    macd_validos = [x if x is not None else 0.0 for x in macd_line]
    signal_line = ema_lista(macd_validos, signal)

    hist = [None] * len(closes_list)
    for i in range(len(closes_list)):
        if macd_line[i] is not None and signal_line[i] is not None:
            hist[i] = macd_line[i] - signal_line[i]

    return macd_line, signal_line, hist


def calc_bollinger(closes_list, periodo=20, desvio=2.0):
    media = sma_lista(closes_list, periodo)
    upper = [None] * len(closes_list)
    lower = [None] * len(closes_list)

    for i in range(len(closes_list)):
        if i >= periodo - 1 and media[i] is not None:
            janela = closes_list[i - periodo + 1:i + 1]
            m = media[i]
            var = sum((x - m) ** 2 for x in janela) / periodo
            std = math.sqrt(var)
            upper[i] = m + desvio * std
            lower[i] = m - desvio * std

    return media, upper, lower


def preparar_indicadores(velas):
    closes_list = [float(v["close"]) for v in velas]
    highs_list = [float(v["max"]) for v in velas]
    lows_list = [float(v["min"]) for v in velas]

    rsi = calc_rsi(closes_list, RSI_PERIODO)
    macd_line, signal_line, hist = calc_macd(closes_list, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    bb_mid, bb_upper, bb_lower = calc_bollinger(closes_list, BB_PERIODO, BB_DESVIO)
    mm20 = sma_lista(closes_list, MM_PERIODO)

    return {
        "close": closes_list,
        "high": highs_list,
        "low": lows_list,
        "rsi": rsi,
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_hist": hist,
        "bb_mid": bb_mid,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "mm20": mm20,
    }


def filtro_rsi_ok(ind, idx, direcao):
    valor = ind["rsi"][idx]
    if valor is None:
        return False
    if direcao == "call":
        return valor <= RSI_CALL_LIMITE
    return valor >= RSI_PUT_LIMITE


def filtro_macd_ok(ind, idx, direcao):
    macd = ind["macd"][idx]
    sinal = ind["macd_signal"][idx]
    hist = ind["macd_hist"][idx]

    if macd is None or sinal is None or hist is None:
        return False

    if direcao == "call":
        return macd > sinal and hist > 0
    return macd < sinal and hist < 0


def filtro_bollinger_ok(ind, idx, direcao):
    upper = ind["bb_upper"][idx]
    lower = ind["bb_lower"][idx]
    close_ = ind["close"][idx]
    high_ = ind["high"][idx]
    low_ = ind["low"][idx]

    if upper is None or lower is None:
        return False

    if direcao == "call":
        return close_ <= lower or low_ <= lower
    return close_ >= upper or high_ >= upper


def filtro_mm_ok(ind, idx, direcao):
    mm = ind["mm20"][idx]
    if mm is None:
        return False

    close_ = ind["close"][idx]
    mm_prev = ind["mm20"][idx - 1] if idx - 1 >= 0 else None

    if direcao == "call":
        if mm_prev is not None:
            return close_ > mm and mm >= mm_prev
        return close_ > mm

    if mm_prev is not None:
        return close_ < mm and mm <= mm_prev
    return close_ < mm


def filtros_aprovados(ind, idx, direcao):
    out = {"PURO": True}

    out["RSI"] = filtro_rsi_ok(ind, idx, direcao)
    out["MACD"] = filtro_macd_ok(ind, idx, direcao)
    out["BOLLINGER"] = filtro_bollinger_ok(ind, idx, direcao)
    out["MM"] = filtro_mm_ok(ind, idx, direcao)

    return out

# =========================
# ESTATÍSTICA
# =========================
def init_stats():
    stats = {}
    for p in PADROES_ATIVOS:
        stats[p["nome"]] = {}
        for filtro in FILTROS:
            stats[p["nome"]][filtro] = {
                "wins": 0,
                "losses": 0,
                "entradas": 0,
                "wr": 0.0
            }
    return stats


def calcular_score(wr, entradas):
    if entradas <= 0:
        return 0.0
    return round(wr * math.log(entradas + 1), 2)


def registrar_resultado(stats, nome, idx, direcao, cores, filtros_ok):
    if idx + 1 >= len(cores):
        return
    if nome not in stats:
        return

    resultado = cores[idx + 1]

    if resultado == "D":
        return

    for filtro, ok in filtros_ok.items():
        if not ok:
            continue

        bloco = stats[nome][filtro]
        bloco["entradas"] += 1

        if direcao == "call":
            if resultado == "G":
                bloco["wins"] += 1
            elif resultado == "R":
                bloco["losses"] += 1

        elif direcao == "put":
            if resultado == "R":
                bloco["wins"] += 1
            elif resultado == "G":
                bloco["losses"] += 1


def fechar_stats(stats):
    for nome in stats:
        for filtro in stats[nome]:
            e = stats[nome][filtro]["entradas"]
            w = stats[nome][filtro]["wins"]
            stats[nome][filtro]["wr"] = round((w / e) * 100, 2) if e else 0.0


def melhor_variante_do_padrao(nome, direcao, tipo, stats_padrao):
    candidatos = []

    base = stats_padrao["PURO"]
    base_valida = base["entradas"] >= MIN_ENTRADAS

    if base_valida:
        candidatos.append({
            "nome": nome,
            "filtro": "PURO",
            "wins": base["wins"],
            "losses": base["losses"],
            "wr": base["wr"],
            "entradas": base["entradas"],
            "score": calcular_score(base["wr"], base["entradas"]),
            "dir": direcao,
            "tipo": tipo
        })

    for filtro in FILTROS:
        if filtro == "PURO":
            continue

        s = stats_padrao[filtro]
        if s["entradas"] < MIN_ENTRADAS:
            continue

        score_filtro = calcular_score(s["wr"], s["entradas"])

        if base_valida:
            score_base = calcular_score(base["wr"], base["entradas"])
            if s["wr"] < base["wr"] and score_filtro <= score_base:
                continue

        candidatos.append({
            "nome": nome,
            "filtro": filtro,
            "wins": s["wins"],
            "losses": s["losses"],
            "wr": s["wr"],
            "entradas": s["entradas"],
            "score": score_filtro,
            "dir": direcao,
            "tipo": tipo
        })

    if not candidatos:
        return None

    candidatos.sort(key=lambda x: (x["score"], x["wins"], -x["losses"], x["wr"]), reverse=True)
    return candidatos[0]

# =========================
# BALANCEAMENTO / CONTROLE
# =========================
def selecionar_top_balanceado(ranking_local):
    ranking_local.sort(
        key=lambda x: (
            x["score"],
            x["wins"],
            -x["losses"],
            x["wr"],
            x["entradas"]
        ),
        reverse=True
    )
    return ranking_local[:4]


def registrar_resultado_com_cooldown(stats, ultimo_registro, nome, idx, direcao, cores, filtros_ok):
    ultimo_idx = ultimo_registro.get(nome, -999999)
    if idx - ultimo_idx < COOLDOWN_ESTRUTURA_BARRAS:
        return
    registrar_resultado(stats, nome, idx, direcao, cores, filtros_ok)
    ultimo_registro[nome] = idx

# =========================
# DETECTORES
# =========================
def detectar_candles_reais(velas):
    stats = init_stats()

    m = [candle_metrics(v) for v in velas]
    ind = preparar_indicadores(velas)
    cores = [("G" if x["bull"] else "R" if x["bear"] else "D") for x in m]

    nomes_ativos = {p["nome"] for p in PADROES_ATIVOS}
    ultimo_registro_estrutura = {}

    for i in range(len(m)):
        curr = m[i]

        if "martelo" in nomes_ativos and is_hammer(curr):
            registrar_resultado(stats, "martelo", i, "call", cores, filtros_aprovados(ind, i, "call"))

        if "enforcado" in nomes_ativos and is_hanging_man(curr):
            registrar_resultado(stats, "enforcado", i, "put", cores, filtros_aprovados(ind, i, "put"))

        if "estrela cadente" in nomes_ativos and is_shooting_star(curr):
            registrar_resultado(stats, "estrela cadente", i, "put", cores, filtros_aprovados(ind, i, "put"))

        if "marubozu alta" in nomes_ativos and is_marubozu_bull(curr):
            registrar_resultado(stats, "marubozu alta", i, "call", cores, filtros_aprovados(ind, i, "call"))

        if "marubozu baixa" in nomes_ativos and is_marubozu_bear(curr):
            registrar_resultado(stats, "marubozu baixa", i, "put", cores, filtros_aprovados(ind, i, "put"))

        if i >= 1:
            prev = m[i - 1]

            if "harami" in nomes_ativos and bullish_harami(prev, curr):
                registrar_resultado(stats, "harami", i, "call", cores, filtros_aprovados(ind, i, "call"))

            if "harami cross" in nomes_ativos and bullish_harami_cross(prev, curr):
                registrar_resultado(stats, "harami cross", i, "call", cores, filtros_aprovados(ind, i, "call"))

            if "engolfo alta" in nomes_ativos and bullish_engulfing(prev, curr):
                registrar_resultado(stats, "engolfo alta", i, "call", cores, filtros_aprovados(ind, i, "call"))

            if "engolfo baixa" in nomes_ativos and bearish_engulfing(prev, curr):
                registrar_resultado(stats, "engolfo baixa", i, "put", cores, filtros_aprovados(ind, i, "put"))

            if "piercing line" in nomes_ativos and piercing_line(prev, curr):
                registrar_resultado(stats, "piercing line", i, "call", cores, filtros_aprovados(ind, i, "call"))

            if "dark cloud cover" in nomes_ativos and dark_cloud_cover(prev, curr):
                registrar_resultado(stats, "dark cloud cover", i, "put", cores, filtros_aprovados(ind, i, "put"))

        if i >= 2:
            a, b, c = m[i - 2], m[i - 1], m[i]

            if "estrela da manhã" in nomes_ativos and morning_star(a, b, c):
                registrar_resultado(stats, "estrela da manhã", i, "call", cores, filtros_aprovados(ind, i, "call"))

            if "estrela da tarde" in nomes_ativos and evening_star(a, b, c):
                registrar_resultado(stats, "estrela da tarde", i, "put", cores, filtros_aprovados(ind, i, "put"))

            if "3 soldados brancos" in nomes_ativos and three_white_soldiers(a, b, c):
                registrar_resultado(stats, "3 soldados brancos", i, "call", cores, filtros_aprovados(ind, i, "call"))

            if "3 corvos pretos" in nomes_ativos and three_black_crows(a, b, c):
                registrar_resultado(stats, "3 corvos pretos", i, "put", cores, filtros_aprovados(ind, i, "put"))

        if i >= 3:
            a, b, c, d = m[i - 3], m[i - 2], m[i - 1], m[i]
            if "3 métodos ascendentes" in nomes_ativos and rising_three_methods(a, b, c, d):
                registrar_resultado(stats, "3 métodos ascendentes", i, "call", cores, filtros_aprovados(ind, i, "call"))

    struct_janelas = [24, 30, 36]
    for struct_lookback in struct_janelas:
        for i in range(struct_lookback - 1, len(velas) - 1):
            window = velas[i - struct_lookback + 1:i + 1]

            if "ombro cabeça ombro" in nomes_ativos and head_shoulders(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "ombro cabeça ombro", i, "put", cores, filtros_aprovados(ind, i, "put"))

            if "fundo duplo" in nomes_ativos and double_bottom(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "fundo duplo", i, "call", cores, filtros_aprovados(ind, i, "call"))

            if "topo duplo" in nomes_ativos and double_top(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "topo duplo", i, "put", cores, filtros_aprovados(ind, i, "put"))

            if "fundo triplo" in nomes_ativos and triple_bottom(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "fundo triplo", i, "call", cores, filtros_aprovados(ind, i, "call"))

            if "cunha descendente" in nomes_ativos and falling_wedge(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "cunha descendente", i, "call", cores, filtros_aprovados(ind, i, "call"))

            if "cunha ascendente" in nomes_ativos and rising_wedge(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "cunha ascendente", i, "put", cores, filtros_aprovados(ind, i, "put"))

            if "alargamento altista" in nomes_ativos and broadening_bullish(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "alargamento altista", i, "call", cores, filtros_aprovados(ind, i, "call"))

            if "alargamento baixista" in nomes_ativos and broadening_bearish(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "alargamento baixista", i, "put", cores, filtros_aprovados(ind, i, "put"))

            if "bandeira altista" in nomes_ativos and bullish_flag(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "bandeira altista", i, "call", cores, filtros_aprovados(ind, i, "call"))

            if "bandeira baixista" in nomes_ativos and bearish_flag(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "bandeira baixista", i, "put", cores, filtros_aprovados(ind, i, "put"))

            if "triangulo ascendente" in nomes_ativos and ascending_triangle(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "triangulo ascendente", i, "call", cores, filtros_aprovados(ind, i, "call"))

            if "triangulo descendente" in nomes_ativos and descending_triangle(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "triangulo descendente", i, "put", cores, filtros_aprovados(ind, i, "put"))

            if "retangulo altista" in nomes_ativos and bullish_rectangle(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "retangulo altista", i, "call", cores, filtros_aprovados(ind, i, "call"))

            if "retangulo baixista" in nomes_ativos and bearish_rectangle(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "retangulo baixista", i, "put", cores, filtros_aprovados(ind, i, "put"))

            if "triangulo simetrico de alta" in nomes_ativos and symmetrical_triangle_bull(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "triangulo simetrico de alta", i, "call", cores, filtros_aprovados(ind, i, "call"))

            if "triangulo simetrico de baixa" in nomes_ativos and symmetrical_triangle_bear(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "triangulo simetrico de baixa", i, "put", cores, filtros_aprovados(ind, i, "put"))

            if "cup and handle" in nomes_ativos and cup_and_handle(window):
                registrar_resultado_com_cooldown(stats, ultimo_registro_estrutura, "cup and handle", i, "call", cores, filtros_aprovados(ind, i, "call"))

    fechar_stats(stats)
    return stats


def atualizar_top_padroes(ativo, stats):
    global top_padroes

    lista_nova = []

    for p in PADROES_ATIVOS:
        melhor = melhor_variante_do_padrao(p["nome"], p["dir"], p["tipo"], stats[p["nome"]])
        if melhor is None:
            continue
        lista_nova.append(melhor)

    lista_nova.sort(key=lambda x: (x["score"], x["wins"], -x["losses"], x["wr"]), reverse=True)
    top_padroes[ativo] = lista_nova[:4]


def analisar_ativo(ativo):
    try:
        velas = pegar_velas(ativo, TOTAL_VELAS)

        if len(velas) < 30:
            return None, f"⚠️ {ativo}: sem dados suficientes"

        stats = detectar_candles_reais(velas)
        atualizar_top_padroes(ativo, stats)

        ranking_local = []
        for p in PADROES_ATIVOS:
            melhor = melhor_variante_do_padrao(p["nome"], p["dir"], p["tipo"], stats[p["nome"]])
            if melhor is not None:
                ranking_local.append({
                    "ativo": ativo,
                    "padrao": melhor["nome"],
                    "filtro": melhor["filtro"],
                    "wins": melhor["wins"],
                    "losses": melhor["losses"],
                    "wr": melhor["wr"],
                    "entradas": melhor["entradas"],
                    "score": melhor["score"],
                    "tipo": melhor["tipo"],
                    "dir": melhor["dir"]
                })

        ranking_local.sort(key=lambda x: (x["score"], x["wins"], -x["losses"], x["wr"]), reverse=True)
        top4_local = selecionar_top_balanceado(ranking_local)
        score_top4 = round(sum(item["score"] for item in top4_local), 2)

        total_wins = sum(item["wins"] for item in top4_local)
        total_losses = sum(item["losses"] for item in top4_local)
        total = total_wins + total_losses
        wr_total = round((total_wins / total) * 100, 2) if total else 0.0
        score_total = calcular_score(wr_total, total)

        return {
            "ativo": ativo,
            "wr": wr_total,
            "wins": total_wins,
            "losses": total_losses,
            "entradas": total,
            "score": score_total,
            "score_top4": score_top4,
            "stats": stats,
            "ranking_local": ranking_local,
            "top4_local": top4_local
        }, None

    except Exception as e:
        return None, f"❌ Erro ao analisar {ativo}: {e}"

# =========================
# OUTPUT
# =========================
def formatar_nome_padrao(nome):
    return nome.upper()


def imprimir_resumo_ativo(resultado):
    ativo = resultado["ativo"]

    print(f"\n📊 {ativo} (ATUALIZADO)")
    print("=" * 80)

    top4 = resultado.get("top4_local", [])
    if not top4:
        print("Sem padrões elegíveis.")
    else:
        for item in top4:
            padrao = formatar_nome_padrao(item["padrao"])
            print(
                f"{padrao:<30} [{item['filtro']}] → "
                f"{item['wins']}x{item['losses']} | WR:{item['wr']}% | E:{item['entradas']} | SCORE:{item['score']}"
            )

    print(
        f"\n🏆 RESUMO TOP4: {resultado['wins']}x{resultado['losses']} | "
        f"WR: {resultado['wr']}% | SCORE TOP4: {resultado['score_top4']}"
    )


def imprimir_top3_ativos_com_top4_padroes(ranking_ativos):
    print("\n🏆 TOP 3 ATIVOS + 4 MELHORES PADRÕES")
    print("=" * 80)

    if not ranking_ativos:
        print("⚠️ Nenhum ativo gerou análise válida neste ciclo.")
        return

    top3 = ranking_ativos[:3]

    for pos, resultado in enumerate(top3, 1):
        print(f"\n{pos}º {resultado['ativo']}")
        print("-" * 60)

        top4 = resultado.get("top4_local", [])
        if not top4:
            print("Sem padrões elegíveis para este ativo.")
            continue

        for item in top4:
            padrao = formatar_nome_padrao(item["padrao"])
            print(f"{padrao} [{item['filtro']}] {item['wins']}x{item['losses']} WIN")

# =========================
# LOOP PRINCIPAL
# =========================
def main():
    global iq, EMAIL, SENHA, top_padroes

    print("==============================================================")
    print(" CATALOGADOR IQ OPTION - PADRÕES + MODO MATEMÁTICO PURO ")
    print("==============================================================")

    EMAIL, SENHA = pedir_credenciais()
    escolher_tipo_catalogacao()
    modo_execucao, ativo_pesquisado = escolher_modo_execucao()

    iq = conectar_iq(EMAIL, SENHA)
    if iq is None:
        print("❌ Não foi possível iniciar o catalogador.")
        input("Pressione Enter para encerrar...")
        return

    if modo_execucao == "unico":
        ativo_resolvido = buscar_ativo_especifico(ativo_pesquisado)
        if not ativo_resolvido:
            print(f"❌ Ativo não encontrado: {ativo_pesquisado}")
            input("Pressione Enter para encerrar...")
            return
        print(f"✅ Ativo selecionado: {ativo_resolvido}")
        ativos_fixos = [ativo_resolvido]
    else:
        ativos_fixos = None

    while True:
        try:
            top_padroes = {}

            if ativos_fixos is None:
                print("🔄 Atualizando lista de ativos válidos da IQ...")
                ATIVOS = montar_rotacao_ativos()

                if not ATIVOS:
                    print("❌ Nenhum ativo válido encontrado.")
                    input("Pressione Enter para tentar novamente...")
                    continue

                print(f"✅ Ativos finais na rotação: {len(ATIVOS)}")
                print("📋 Primeiros ativos:", ", ".join(ATIVOS[:20]))
            else:
                ATIVOS = ativos_fixos
                print(f"🔎 Catalogando ativo específico: {ATIVOS[0]}")

            ranking_ativos = []

            for ativo in ATIVOS:
                resultado, erro = analisar_ativo(ativo)

                if erro:
                    print(erro)
                    time.sleep(PAUSA_ENTRE_ATIVOS)
                    continue

                ranking_ativos.append(resultado)
                imprimir_resumo_ativo(resultado)
                time.sleep(PAUSA_ENTRE_ATIVOS)

            ranking_ativos.sort(
                key=lambda x: (x["score_top4"], x["score"], x["wins"]),
                reverse=True
            )

            if ativos_fixos is None:
                imprimir_top3_ativos_com_top4_padroes(ranking_ativos)
                print(f"\n⏱ Atualizando em {ATUALIZAR_A_CADA}s...\n")
                time.sleep(ATUALIZAR_A_CADA)
                continue

            print("\n📌 O QUE DESEJA FAZER AGORA?")
            print("1 - Catalogar o MESMO ativo novamente")
            print("2 - Catalogar OUTRO ativo")
            print("3 - Voltar para TODOS os ativos")
            print("0 - Sair")

            escolha_final = input("👉 Escolha: ").strip()

            if escolha_final == "1":
                print("🔁 Repetindo catalogação do mesmo ativo...\n")
                continue

            elif escolha_final == "2":
                novo_ativo = input("Digite o novo ativo: ").strip().upper()
                ativo_resolvido = buscar_ativo_especifico(novo_ativo)
                if not ativo_resolvido:
                    print(f"❌ Ativo não encontrado: {novo_ativo}")
                    continue
                ativos_fixos = [ativo_resolvido]
                print(f"✅ Novo ativo selecionado: {ativo_resolvido}")
                continue

            elif escolha_final == "3":
                ativos_fixos = None
                print("🔄 Voltando para modo TODOS os ativos...\n")
                continue

            elif escolha_final == "0":
                print("🛑 Encerrado pelo usuário.")
                input("Pressione Enter para fechar...")
                return

            else:
                print("❌ Opção inválida. Repetindo menu...\n")
                continue

        except KeyboardInterrupt:
            print("\n🛑 Encerrado pelo usuário.")
            input("Pressione Enter para fechar...")
            return

        except Exception as e:
            print(f"\n❌ Erro fatal no loop principal: {e}")
            print(traceback.format_exc())
            input("Pressione Enter para tentar continuar...")
            time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Erro fatal ao iniciar: {e}")
        print(traceback.format_exc())
        input("Pressione Enter para encerrar...")
