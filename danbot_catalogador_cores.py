from iqoptionapi.stable_api import IQ_Option
import time
import math
import logging
import traceback
from itertools import product

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

MM_PERIODO = 20

FILTROS = ["PURO", "RSI", "MACD", "MM"]

ATIVOS_FALLBACK = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD", "USDCHF",
    "EURJPY", "EURGBP", "GBPJPY",
    "EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC", "AUDUSD-OTC", "NZDUSD-OTC",
    "USDCHF-OTC", "EURGBP-OTC", "EURJPY-OTC", "GBPJPY-OTC", "AUDCAD-OTC",
    "USDSGD-OTC", "USDHKD-OTC", "USDINR-OTC", "USDZAR-OTC"
]

# =========================
# PADRÕES DE CORES
# =========================
PADROES_JSON = [
    {"id": "p01", "nome": "sequencia GGGG", "sequencia": "GGGG", "dir": "call", "tipo": "seq4"},
    {"id": "p02", "nome": "sequencia RRRR", "sequencia": "RRRR", "dir": "put", "tipo": "seq4"},
    {"id": "p03", "nome": "sequencia GGRG", "sequencia": "GGRG", "dir": "call", "tipo": "seq4"},
    {"id": "p04", "nome": "sequencia RRGR", "sequencia": "RRGR", "dir": "put", "tipo": "seq4"},
    {"id": "p05", "nome": "sequencia GRGG", "sequencia": "GRGG", "dir": "call", "tipo": "seq4"},
    {"id": "p06", "nome": "sequencia RGGR", "sequencia": "RGGR", "dir": "put", "tipo": "seq4"},
    {"id": "p07", "nome": "sequencia RGGG", "sequencia": "RGGG", "dir": "call", "tipo": "seq4"},
    {"id": "p08", "nome": "sequencia GRRR", "sequencia": "GRRR", "dir": "put", "tipo": "seq4"},
    {"id": "p09", "nome": "sequencia GGRRG", "sequencia": "GGRRG", "dir": "call", "tipo": "seq5"},
    {"id": "p10", "nome": "sequencia RRGGR", "sequencia": "RRGGR", "dir": "put", "tipo": "seq5"},
    {"id": "p11", "nome": "sequencia GRGGR", "sequencia": "GRGGR", "dir": "call", "tipo": "seq5"},
    {"id": "p12", "nome": "sequencia RGRRG", "sequencia": "RGRRG", "dir": "put", "tipo": "seq5"},
    {"id": "p13", "nome": "sequencia RRGGG", "sequencia": "RRGGG", "dir": "call", "tipo": "seq5"},
    {"id": "p14", "nome": "sequencia GGRRR", "sequencia": "GGRRR", "dir": "put", "tipo": "seq5"},
    {"id": "p15", "nome": "sequencia GRGGG", "sequencia": "GRGGG", "dir": "call", "tipo": "seq5"},
    {"id": "p16", "nome": "sequencia RGRRR", "sequencia": "RGRRR", "dir": "put", "tipo": "seq5"},
    {"id": "p17", "nome": "sequencia GGGGR", "sequencia": "GGGGR", "dir": "call", "tipo": "seq5"},
    {"id": "p18", "nome": "sequencia RRRRG", "sequencia": "RRRRG", "dir": "put", "tipo": "seq5"},
    {"id": "p19", "nome": "sequencia GGRRGG", "sequencia": "GGRRGG", "dir": "call", "tipo": "seq6"},
    {"id": "p20", "nome": "sequencia RRGGRR", "sequencia": "RRGGRR", "dir": "put", "tipo": "seq6"},
    {"id": "p21", "nome": "sequencia GRGGRG", "sequencia": "GRGGRG", "dir": "call", "tipo": "seq6"},
    {"id": "p22", "nome": "sequencia RGRRGR", "sequencia": "RGRRGR", "dir": "put", "tipo": "seq6"},
    {"id": "p23", "nome": "sequencia RRGGGG", "sequencia": "RRGGGG", "dir": "call", "tipo": "seq6"},
    {"id": "p24", "nome": "sequencia GGRRRR", "sequencia": "GGRRRR", "dir": "put", "tipo": "seq6"},
    {"id": "p25", "nome": "sequencia GRGGGG", "sequencia": "GRGGGG", "dir": "call", "tipo": "seq6"},
    {"id": "p26", "nome": "sequencia RGRRRR", "sequencia": "RGRRRR", "dir": "put", "tipo": "seq6"},
    {"id": "p27", "nome": "sequencia GGGRRG", "sequencia": "GGGRRG", "dir": "call", "tipo": "seq6"},
    {"id": "p28", "nome": "sequencia RRRGGR", "sequencia": "RRRGGR", "dir": "put", "tipo": "seq6"},
    {"id": "p29", "nome": "sequencia GGRGGG", "sequencia": "GGRGGG", "dir": "call", "tipo": "seq6"},
    {"id": "p30", "nome": "sequencia RGRRRG", "sequencia": "RGRRRG", "dir": "put", "tipo": "seq6"},
    {"id": "p31", "nome": "sequencia GRGRGG", "sequencia": "GRGRGG", "dir": "call", "tipo": "seq6"},
    {"id": "p32", "nome": "sequencia RGRGRR", "sequencia": "RGRGRR", "dir": "put", "tipo": "seq6"},
    {"id": "p33", "nome": "sequencia RGGRGG", "sequencia": "RGGRGG", "dir": "call", "tipo": "seq6"},
    {"id": "p34", "nome": "sequencia GRRGRR", "sequencia": "GRRGRR", "dir": "put", "tipo": "seq6"},
]

PADROES_4 = [p for p in PADROES_JSON if len(p["sequencia"]) == 4]
PADROES_5 = [p for p in PADROES_JSON if len(p["sequencia"]) == 5]
PADROES_6 = [p for p in PADROES_JSON if len(p["sequencia"]) == 6]

PADROES_TODOS = PADROES_JSON[:]
PADROES_ATIVOS = PADROES_TODOS[:]
MODO_SEQUENCIA_ATUAL = "todos"

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
    global PADROES_ATIVOS, MODO_SEQUENCIA_ATUAL

    print("\n📊 SEQUÊNCIAS DE CORES")
    print("1 - Apenas sequências de 4 velas")
    print("2 - Apenas sequências de 5 velas")
    print("3 - Apenas sequências de 6 velas")
    print("4 - TODAS (4 + 5 + 6)")

    while True:
        escolha = input("👉 Escolha: ").strip()

        if escolha == "1":
            PADROES_ATIVOS = PADROES_4[:]
            MODO_SEQUENCIA_ATUAL = "4"
            print(f"✅ Modo selecionado: 4 velas ({len(PADROES_ATIVOS)} padrões do JSON)")
            return
        elif escolha == "2":
            PADROES_ATIVOS = PADROES_5[:]
            MODO_SEQUENCIA_ATUAL = "5"
            print(f"✅ Modo selecionado: 5 velas ({len(PADROES_ATIVOS)} padrões do JSON)")
            return
        elif escolha == "3":
            PADROES_ATIVOS = PADROES_6[:]
            MODO_SEQUENCIA_ATUAL = "6"
            print(f"✅ Modo selecionado: 6 velas ({len(PADROES_ATIVOS)} padrões do JSON)")
            return
        elif escolha == "4":
            PADROES_ATIVOS = PADROES_TODOS[:]
            MODO_SEQUENCIA_ATUAL = "todos"
            print(f"✅ Modo selecionado: TODAS ({len(PADROES_ATIVOS)} padrões do JSON)")
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
# UTILITÁRIOS
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
        "lower_pct": max(lower, 0.0) / rng
    }


def candle_cor(m):
    if m["bull"]:
        return "G"
    if m["bear"]:
        return "R"
    return "D"


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


def preparar_indicadores(velas):
    closes_list = [float(v["close"]) for v in velas]

    rsi = calc_rsi(closes_list, RSI_PERIODO)
    macd_line, signal_line, hist = calc_macd(closes_list, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    mm20 = sma_lista(closes_list, MM_PERIODO)

    return {
        "close": closes_list,
        "rsi": rsi,
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_hist": hist,
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
    out["MM"] = filtro_mm_ok(ind, idx, direcao)
    return out


def init_stats():
    stats = {}
    for p in PADROES_ATIVOS:
        stats[p["nome"]] = {}
        for filtro in FILTROS:
            stats[p["nome"]][filtro] = {
                "wins": 0,
                "losses": 0,
                "entradas": 0,
                "wr": 0.0,
                "call": 0,
                "put": 0
            }
    return stats


def calcular_score(wr, entradas):
    if entradas <= 0:
        return 0.0
    return round(wr * math.log(entradas + 1), 2)


def registrar_resultado(stats, nome, idx, direcao, proxima_cor, filtros_ok):
    if proxima_cor == "D":
        return

    for filtro, ok in filtros_ok.items():
        if not ok:
            continue

        bloco = stats[nome][filtro]
        bloco["entradas"] += 1
        if direcao == "call":
            bloco["call"] += 1
            if proxima_cor == "G":
                bloco["wins"] += 1
            elif proxima_cor == "R":
                bloco["losses"] += 1
        elif direcao == "put":
            bloco["put"] += 1
            if proxima_cor == "R":
                bloco["wins"] += 1
            elif proxima_cor == "G":
                bloco["losses"] += 1


def fechar_stats(stats):
    for nome in stats:
        for filtro in stats[nome]:
            e = stats[nome][filtro]["entradas"]
            w = stats[nome][filtro]["wins"]
            stats[nome][filtro]["wr"] = round((w / e) * 100, 2) if e else 0.0


def melhor_variante_do_padrao(nome, direcao_fixa, tipo, stats_padrao):
    candidatos = []

    base = stats_padrao["PURO"]
    if base["entradas"] >= MIN_ENTRADAS:
        candidatos.append({
            "nome": nome,
            "filtro": "PURO",
            "wins": base["wins"],
            "losses": base["losses"],
            "wr": base["wr"],
            "entradas": base["entradas"],
            "score": calcular_score(base["wr"], base["entradas"]),
            "dir": direcao_fixa,
            "tipo": tipo
        })

    for filtro in FILTROS:
        if filtro == "PURO":
            continue
        s = stats_padrao[filtro]
        if s["entradas"] < MIN_ENTRADAS:
            continue
        score_filtro = calcular_score(s["wr"], s["entradas"])
        score_base = calcular_score(base["wr"], base["entradas"]) if base["entradas"] >= MIN_ENTRADAS else -1
        if base["entradas"] >= MIN_ENTRADAS and s["wr"] <= base["wr"] and score_filtro <= score_base:
            continue
        candidatos.append({
            "nome": nome,
            "filtro": filtro,
            "wins": s["wins"],
            "losses": s["losses"],
            "wr": s["wr"],
            "entradas": s["entradas"],
            "score": score_filtro,
            "dir": direcao_fixa,
            "tipo": tipo
        })

    if not candidatos:
        return None

    candidatos.sort(key=lambda x: (x["score"], x["wins"], -x["losses"], x["wr"], x["entradas"]), reverse=True)
    return candidatos[0]

# =========================
# DETECTOR DE SEQUÊNCIA
# =========================
def detectar_sequencias_cores(velas):
    stats = init_stats()

    m = [candle_metrics(v) for v in velas]
    ind = preparar_indicadores(velas)
    cores = [candle_cor(x) for x in m]

    for p in PADROES_ATIVOS:
        nome = p["nome"]
        sequencia = p["sequencia"]
        direcao = p["dir"]
        tamanho = len(sequencia)

        for i in range(tamanho - 1, len(cores) - 1):
            janela = cores[i - tamanho + 1:i + 1]
            if "D" in janela:
                continue

            seq_atual = "".join(janela)
            if seq_atual != sequencia:
                continue

            prox_cor = cores[i + 1]
            filtros = filtros_aprovados(ind, i, direcao)
            registrar_resultado(stats, nome, i, direcao, prox_cor, filtros)

    fechar_stats(stats)
    return stats


def atualizar_top_padroes(ativo, stats):
    global top_padroes
    lista_nova = []
    for p in PADROES_ATIVOS:
        melhor = melhor_variante_do_padrao(p["nome"], p["dir"], p["tipo"], stats[p["nome"]])
        if melhor is not None:
            lista_nova.append(melhor)
    lista_nova.sort(key=lambda x: (x["score"], x["wins"], -x["losses"], x["wr"], x["entradas"]), reverse=True)
    top_padroes[ativo] = lista_nova[:4]


def analisar_ativo(ativo):
    try:
        velas = pegar_velas(ativo, TOTAL_VELAS)
        if len(velas) < 30:
            return None, f"⚠️ {ativo}: sem dados suficientes"

        stats = detectar_sequencias_cores(velas)
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

        ranking_local.sort(key=lambda x: (x["score"], x["wins"], -x["losses"], x["wr"], x["entradas"]), reverse=True)
        top4_local = ranking_local[:4]
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
    print(f"\n📊 {ativo} (SEQUÊNCIAS DE CORES)")
    print("=" * 90)

    top4 = resultado.get("top4_local", [])
    if not top4:
        print("Sem sequências elegíveis.")
    else:
        for item in top4:
            padrao = formatar_nome_padrao(item["padrao"])
            print(
                f"{padrao:<12} [{item['tipo']} | {item['filtro']} | {item['dir'].upper()}] → "
                f"{item['wins']}x{item['losses']} | WR:{item['wr']}% | E:{item['entradas']} | SCORE:{item['score']}"
            )

    print(
        f"\n🏆 RESUMO TOP4: {resultado['wins']}x{resultado['losses']} | "
        f"WR: {resultado['wr']}% | SCORE TOP4: {resultado['score_top4']}"
    )


def imprimir_top3_ativos_com_top4_padroes(ranking_ativos):
    print("\n🏆 TOP 3 ATIVOS + 4 MELHORES SEQUÊNCIAS")
    print("=" * 90)

    if not ranking_ativos:
        print("⚠️ Nenhum ativo gerou análise válida neste ciclo.")
        return

    top3 = ranking_ativos[:3]

    for pos, resultado in enumerate(top3, 1):
        print(f"\n{pos}º {resultado['ativo']}")
        print("-" * 70)

        top4 = resultado.get("top4_local", [])
        if not top4:
            print("Sem sequências elegíveis para este ativo.")
            continue

        for item in top4:
            padrao = formatar_nome_padrao(item["padrao"])
            print(
                f"{padrao} [{item['dir'].upper()} | {item['filtro']}] "
                f"{item['wins']}x{item['losses']} WIN"
            )

# =========================
# LOOP PRINCIPAL
# =========================
def main():
    global iq, EMAIL, SENHA, top_padroes

    print("================================================================")
    print(" CATALOGADOR IQ OPTION - SEQUÊNCIAS DO JSON + RANKING ")
    print("================================================================")

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
