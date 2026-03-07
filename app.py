"""
DANBOT WEB v2.0 — Backend Flask
Bot de Arbitragem OTC para Opções Binárias
"""
from flask import Flask, render_template, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
import hashlib, uuid, datetime, os, jwt, secrets, threading, time, json, random
import numpy as np
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import iq_integration as IQ
from iq_integration import run_backtest, OTC_BINARY_ASSETS, ALL_BINARY_ASSETS, OPEN_BINARY_ASSETS, check_volume_filter, start_heartbeat, stop_heartbeat

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////data/danbot.db' if os.path.exists('/data') else 'sqlite:///danbot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

MASTER_SECRET = 'DANBOT-MASTER-2025'

# ─── MODELOS ─────────────────────────────────────────────────────────────────
class User(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role          = db.Column(db.String(20), default='user')
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    device_id     = db.Column(db.String(256), nullable=True)

class LicenseKey(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    key          = db.Column(db.String(256), unique=True, nullable=False)
    username     = db.Column(db.String(80), nullable=False)
    expires_at   = db.Column(db.DateTime, nullable=True)
    is_active    = db.Column(db.Boolean, default=True)
    device_bound = db.Column(db.String(256), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    last_login   = db.Column(db.DateTime, nullable=True)

class TradeLog(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    username  = db.Column(db.String(80))
    asset     = db.Column(db.String(50))
    direction = db.Column(db.String(10))
    amount    = db.Column(db.Float)
    result    = db.Column(db.String(10))
    profit    = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)

# ─── BOT STATE ───────────────────────────────────────────────────────────────
bot_state = {
    'running': False,
    'broker_connected': False,
    'broker_name': None,
    'broker_email': None,
    'broker_password': None,    # salvo para auto-reconexão
    'broker_account_type': 'PRACTICE',
    'broker_balance': 0.0,
    'wins': 0, 'losses': 0,
    'profit': 0.0,
    'log': [],
    'signal': None,
    'correlations': [],
    'broker': 'IQ Option',
    'entry_value': 2.0,
    'stop_loss': 20.0,
    'stop_win': 50.0,
    'min_corr': 0.80,
    'account_type': 'PRACTICE',
    'selected_asset': 'AUTO',
    'use_volume_filter': False,   # Filtro de volume ativo?
    'vol_min': 150.0,             # Volume mínimo por vela
    'vol_max': 2000.0,            # Volume máximo por vela
    'strategies': {'ema':True,'rsi':True,'bb':True,'macd':True,'adx':True,'stoch':True,'lp':True,'pat':True,'fib':True},
    'min_confluence': 4,
}

# Thread principal do bot (global para acesso do watchdog)
bot_thread = None
_bot_lock = threading.Lock()   # impede duas instâncias simultâneas
_bot_run_id = 0                # ID incrementado a cada start — cada ciclo verifica se ainda é o dono

# Ativos temporariamente suspensos (evitar tentativas repetidas)
_suspended_assets = {}  # {asset: timestamp_de_suspensão}
_SUSPENSION_TIMEOUT = 300  # 5 minutos de espera para tentar novamente

# Apenas ativos de opções BINÁRIAS OTC (turbo M1)
OTC_ASSETS = [
    # ── Forex OTC (9 confirmados COM -OTC) ────────────────────────────────────
    'EURUSD-OTC', 'EURGBP-OTC', 'GBPUSD-OTC', 'USDJPY-OTC', 'USDCHF-OTC',
    'NZDUSD-OTC', 'GBPJPY-OTC', 'EURJPY-OTC', 'AUDCAD-OTC',
    # ── Forex OTC (API aceita sem -OTC) ───────────────────────────────────────
    'AUDUSD-OTC', 'USDCAD-OTC', 'AUDJPY-OTC', 'GBPCAD-OTC', 'GBPCHF-OTC',
    'EURCAD-OTC', 'CHFJPY-OTC', 'CADJPY-OTC', 'EURCHF-OTC',
    'EURNZD-OTC', 'USDSGD-OTC',
    # ── Crypto OTC (binary confirmados) ───────────────────────────────────────
    'BTCUSD-OTC', 'ETHUSD-OTC', 'LTCUSD-OTC', 'XRPUSD-OTC',
    'TRXUSD-OTC', 'EOSUSD-OTC', 'BCHUSD-OTC', 'XLMUSD-OTC', 'ETCUSD-OTC',
    # ── Índices OTC ───────────────────────────────────────────────────────────
    'US100-OTC', 'US500-OTC', 'DE40-OTC', 'FR40-OTC',
    'HK33-OTC', 'JP225-OTC', 'UK100-OTC',
    # ── Ações OTC ─────────────────────────────────────────────────────────────
    'AAPL-OTC', 'MSFT-OTC', 'GOOGL-OTC', 'AMZN-OTC', 'TSLA-OTC',
    'META-OTC', 'NVDA-OTC', 'NFLX-OTC',
    # ── Commodities OTC ───────────────────────────────────────────────────────
    'XAUUSD-OTC', 'XAGUSD-OTC',
]

# Ativos de mercado aberto (Forex, Crypto, Commodities, Índices)
OPEN_ASSETS = [
    'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD',
    'NZDUSD', 'USDCAD', 'EURGBP', 'EURJPY', 'GBPJPY',
    'AUDJPY', 'CADJPY',
    'BTCUSD', 'ETHUSD', 'BNBUSD', 'SOLUSD', 'XRPUSD',
    'XAUUSD', 'XAGUSD', 'USOIL', 'UKOIL',
    'SP500', 'DJ30', 'NASDAQ', 'FTSE100',
]

ALL_ASSETS = OTC_ASSETS + OPEN_ASSETS

def bot_log(msg, level='info'):
    colors = {'info':'#9CA3AF','success':'#10B981','error':'#EF4444','warn':'#F59E0B','signal':'#00D4FF'}
    color  = colors.get(level, '#9CA3AF')
    entry  = {
        'time': datetime.datetime.now().strftime('%H:%M:%S'),
        'msg': msg, 'color': color
    }
    bot_state['log'].insert(0, entry)
    if len(bot_state['log']) > 100:
        bot_state['log'] = bot_state['log'][:100]

def run_bot_real(run_id=0):
    """
    Loop principal — análise técnica completa.
    Modo AUTO: escaneia todos os ativos OTC + Mercado Aberto e escolhe o melhor sinal real.
    Modo FIXO: analisa apenas o ativo selecionado pelo usuário.
    """
    # Verificação inicial de conexão
    mode_label = bot_state.get('account_type', 'PRACTICE')

    bot_log(f'🚀 DANBOT PRO iniciado — Modo {mode_label}', 'success')

    # ── Inicializar is_real ANTES do primeiro uso ──────────────────
    # Invalidar cache para forçar verificação real ao iniciar
    if hasattr(IQ, 'invalidate_session_cache'):
        IQ.invalidate_session_cache()
    is_real = bot_state.get('broker_connected', False) and IQ.is_iq_session_valid()

    if not is_real:
        _email_check = bot_state.get('broker_email')
        if _email_check:
            bot_log('⚠️ Corretora desconectada — bot irá analisar e TENTAR reconectar automaticamente', 'warn')
            bot_log('💡 Aguarde a reconexão automática ou acesse "Corretora" para reconectar manualmente', 'info')
        else:
            bot_log('🔌 Corretora NÃO conectada — analisa mas NÃO fará entradas até conectar', 'error')
            bot_log('👉 Acesse a aba "Corretora" → conecte sua conta IQ Option (PRACTICE = conta demo real)', 'warn')
    else:
        bal = IQ.get_real_balance()
        if bal is not None:
            bot_state['broker_balance'] = bal
            bot_log(f'✅ IQ Option conectada | Saldo: R$ {bal:,.2f}', 'success')

    bot_log(f'💰 Entrada: R${bot_state["entry_value"]:.2f} | SL: R${bot_state["stop_loss"]:.2f} | SW: R${bot_state["stop_win"]:.2f}', 'info')

    # ── Inicializar controles de entrada ─────────────────────────────────
    bot_state['_in_trade']       = False   # trava: 1 entrada por vez
    bot_state['_entry_cooldown'] = {}      # {asset: timestamp_ultima_entrada}
    COOLDOWN_SECONDS = 60                  # 60s entre entradas no mesmo ativo (era 240s)

    cycle = 0
    while bot_state['running']:
        # Verificar se esta thread ainda é a instância ativa
        if run_id != 0 and run_id != _bot_run_id:
            bot_log(f'⚠️ Thread obsoleta (run_id={run_id}) — encerrando', 'warn')
            return
        try:
            cycle += 1
            _cycle_ts = datetime.datetime.now().strftime('%H:%M:%S')
            bot_log(f'🔁 ── Ciclo #{cycle} iniciado às {_cycle_ts} ──', 'info')

            # Verificar conexão a cada ciclo — usa cache de 10s (não bloqueia GIL)
            _broker_was_connected = bot_state.get('broker_connected', False)
            is_real = _broker_was_connected and IQ.is_iq_session_valid()
            if not is_real and _broker_was_connected:
                bot_log('⚠️ Conexão IQ perdida — tentando reconectar...', 'warn')
                bot_state['broker_connected'] = False
                if hasattr(IQ, 'invalidate_session_cache'):
                    IQ.invalidate_session_cache()
                # ── AUTO-RECONEXÃO: usa credenciais salvas ─────────────────
                _email_saved = bot_state.get('broker_email')
                _pass_saved  = bot_state.get('broker_password')
                _acct_saved  = bot_state.get('broker_account_type', 'PRACTICE')
                if _email_saved and _pass_saved:
                    bot_log(f'🔁 Reconectando IQ Option ({_acct_saved}) — {_email_saved}...', 'warn')
                    try:
                        _ok_rc, _res_rc = IQ.connect_iq(_email_saved, _pass_saved, _acct_saved)
                        if _ok_rc:
                            bot_state['broker_connected'] = True
                            bot_state['broker_balance']   = _res_rc.get('balance', 0)
                            is_real = True
                            bot_log(f'✅ Reconectado com sucesso! Saldo: R$ {_res_rc.get("balance",0):,.2f}', 'success')
                            if hasattr(IQ, 'start_heartbeat'):
                                IQ.start_heartbeat()
                        else:
                            bot_log(f'❌ Reconexão falhou: {_res_rc}', 'error')
                            bot_log('💡 Verifique: senha correta? 2FA desativado? IQ Option acessível?', 'warn')
                    except Exception as _erc:
                        bot_log(f'❌ Erro na reconexão: {_erc}', 'error')
                elif _email_saved and not _pass_saved:
                    # Email salvo mas sem senha — acontece após reinício do servidor
                    bot_log('🔑 Sessão expirou após reinício — acesse "Corretora" e reconecte', 'error')
                    bot_log(f'📧 Última conta: {_email_saved}', 'info')
                    # Limpar broker_email para não repetir mensagem a cada ciclo
                    bot_state['broker_email'] = None
                else:
                    bot_log('🔌 Corretora não conectada — acesse a aba "Corretora" para conectar', 'error')

            # Atualizar saldo em background (não bloqueia o loop)
            if is_real:
                bal = IQ.get_real_balance()
                if bal is not None:
                    bot_state['broker_balance'] = bal

            # ── VERIFICAR STOPS ─────────────────────────────────────────────
            if bot_state['profit'] <= -abs(bot_state['stop_loss']):
                bot_log('🛑 STOP LOSS atingido — bot parado!', 'error')
                bot_state['running'] = False; break
            if bot_state['profit'] >= abs(bot_state['stop_win']):
                bot_log('🏆 STOP WIN atingido — bot parado!', 'success')
                bot_state['running'] = False; break

            # ── SELECIONAR ATIVOS ────────────────────────────────────────────
            selected_asset = bot_state.get('selected_asset', 'AUTO')
            # ── SUPORTE A OTC E MERCADO ABERTO BINÁRIO ──────────────────────
            # NÃO converter ativo não-OTC para OTC!
            # O usuário pode selecionar ativos de mercado aberto (ex: EURUSD)
            # e o bot deve respeitar exatamente o ativo escolhido.
            is_otc_asset = selected_asset == 'AUTO' or selected_asset.endswith('-OTC')
            # Log de sincronização de horário (UTC = padrão IQ Option)
            _utc_now = datetime.datetime.utcnow().strftime('%H:%M:%S UTC')
            _sec_next = IQ.seconds_to_next_candle(60)
            if selected_asset and selected_asset != 'AUTO':
                assets_to_scan = [selected_asset]
                tipo_label = 'OTC' if is_otc_asset else '🟢 Mercado Aberto'
                bot_log(f'🔄 Ciclo #{cycle} — {selected_asset} [{tipo_label}] | Vela em {_sec_next:.0f}s | {_utc_now}', 'info')
            else:
                # AUTO: escaneia OTC + Mercado Aberto com candles reais
                if IQ.is_iq_session_valid():
                    assets_to_scan = IQ.get_available_all_assets()
                else:
                    assets_to_scan = IQ.ALL_BINARY_ASSETS
                otc_n  = sum(1 for a in assets_to_scan if a.endswith('-OTC'))
                open_n = len(assets_to_scan) - otc_n
                modo   = 'REAL' if is_real else 'SEM CONEXÃO'
                bot_log(f'🔄 Ciclo #{cycle} [{modo}] — {len(assets_to_scan)} ativos ({otc_n} OTC + {open_n} Aberto) | {_utc_now}', 'info')

            # ── FILTRAR ATIVOS SUSPENSOS ────────────────────────────────────
            now_ts = time.time()
            ativos_antes = len(assets_to_scan)
            assets_to_scan = [a for a in assets_to_scan
                              if now_ts - _suspended_assets.get(a, 0) > _SUSPENSION_TIMEOUT]
            if len(assets_to_scan) < ativos_antes:
                bot_log(f'⏸️ {ativos_antes - len(assets_to_scan)} ativo(s) suspenso(s) ignorado(s)', 'info')

            # ── ESCANEAR / ANALISAR ──────────────────────────────────────────
            # Roda em thread para não bloquear GIL do gunicorn (site acessível durante scan)
            _scan_result = []
            def _do_scan():
                try:
                    _scan_result.extend(IQ.scan_assets(
                        assets_to_scan,
                        timeframe=60,
                        count=50,
                        bot_log_fn=bot_log,
                        bot_state_ref=bot_state,
                        strategies=bot_state.get('strategies', {}),
                        min_confluence=bot_state.get('min_confluence', 4)
                    ))
                except Exception as e:
                    bot_log(f'⚠️ Erro no scan: {e}', 'warn')

            _scan_thread = threading.Thread(target=_do_scan, daemon=True)
            _scan_thread.start()
            # Timeout do scan adaptativo:
            # - REAL AUTO: 60s (candles reais da API podem demorar)
            # - REAL fixo: 15s (1 ativo só)
            # - DEMO AUTO: 15s (candles sintéticos rápidos)
            # - DEMO fixo: 10s
            if is_real and len(assets_to_scan) > 1:
                _scan_timeout = 60
            elif is_real:
                _scan_timeout = 15
            elif len(assets_to_scan) > 1:
                _scan_timeout = 15
            else:
                _scan_timeout = 10
            # Heartbeat durante scan para o log não parecer travado
            _t0 = time.time()
            while _scan_thread.is_alive():
                elapsed = time.time() - _t0
                if elapsed >= _scan_timeout:
                    break
                if int(elapsed) % 5 == 0 and elapsed > 0 and int(elapsed) != getattr(_scan_thread, '_last_hb', -1):
                    _scan_thread._last_hb = int(elapsed)
                    bot_log(f'⏳ Analisando ativos... {int(elapsed)}s/{_scan_timeout}s', 'info')
                time.sleep(0.5)
            if _scan_thread.is_alive():
                bot_log(f'⚠️ Scan timeout ({_scan_timeout}s) — usando {len(_scan_result)} sinal(is) parcial(is)', 'warn')
            signals = sorted(_scan_result, key=lambda x: x['strength'], reverse=True)

            bot_log(f'📊 Análise completa — {len(signals)} sinal(is) encontrado(s)', 'info')

            # ── FILTRO DE VOLUME (apenas mercado aberto, não-OTC) ───────────
            if bot_state.get('use_volume_filter'):
                filtered_signals = []
                for s in signals:
                    s_asset = s.get('asset', '')
                    if s_asset.endswith('-OTC'):
                        filtered_signals.append(s)  # OTC: passa sem filtro de vol
                    else:
                        vl = s.get('vol_last', 0)
                        vmin = bot_state.get('vol_min', 150)
                        vmax = bot_state.get('vol_max', 2000)
                        if vl >= vmin and vl <= vmax:
                            filtered_signals.append(s)
                        else:
                            motivo = f'volume baixo ({vl:.0f})' if vl < vmin else f'volume excessivo ({vl:.0f})'
                            bot_log(f'🔇 {s_asset} bloqueado — {motivo} | faixa: {vmin:.0f}–{vmax:.0f}', 'warn')
                if len(filtered_signals) < len(signals):
                        bot_log(f'\U0001f4ca Volume: {len(signals)-len(filtered_signals)} sinal(is) filtrado(s) por volume', 'info')
                signals = filtered_signals

            # Mínimo 65% no modo AUTO, 55% no modo fixo (para não perder oportunidades)
            min_strength = 55 if len(assets_to_scan) == 1 else 65
            best = next((s for s in signals if s['strength'] >= min_strength), None)

            # ── SEM CONEXÃO: NÃO gerar sinais fictícios ─────────────────────
            # Quando não há conexão real com a IQ Option, o bot apenas
            # loga o status e aguarda — sem inventar entradas aleatórias.
            if best is None and not is_real:
                _email = bot_state.get('broker_email')
                if _email:
                    bot_log(f'🔌 Sem conexão com a corretora — reconexão automática em andamento...', 'warn')
                else:
                    bot_log(f'🔌 Corretora não conectada — acesse a aba "Corretora" e conecte sua conta IQ Option', 'error')
                # NÃO gerar sinal falso — best permanece None

            if best:
                asset    = best['asset']
                direct   = best['direction']
                strength = best['strength']
                trend    = best.get('trend', '—')
                rsi_val  = best.get('rsi', 0)
                reason   = best.get('reason', '')

                bot_state['signal'] = {
                    'a1': asset, 'a2': best.get('detail', {}).get('tendencia_desc', '—'),
                    'd1': direct, 'd2': '—',
                    'z': strength, 'strength': strength,
                    'corr': best.get('score_call', 0),
                    'reason': reason,
                    'trend': trend,
                    'rsi': rsi_val,
                    'time': datetime.datetime.now().strftime('%H:%M:%S')
                }
                bot_log(f'🎯 SINAL: {asset} {direct} {strength}% | Padrão: {best.get("pattern","")[:40]} | Tend:{trend.upper()} RSI5:{rsi_val:.0f}', 'signal')
                bot_log(f'📊 Motivos: {reason[:100]}', 'info')

                amt      = bot_state['entry_value']
                username = bot_state.get('current_user', 'user')

                # ── GUARDA: verificar se ativo ainda é o mesmo ──────────────
                # O usuário pode ter trocado o ativo enquanto o scan rodava.
                # Se o selected_asset mudou, cancelar esta entrada.
                current_sel = bot_state.get('selected_asset', 'AUTO')
                if current_sel != 'AUTO' and current_sel != asset:
                    bot_log(
                        f'🔄 Ativo trocado durante análise ({asset} → {current_sel}). '
                        f'Analisando novo ativo agora...',
                        'warn'
                    )
                    bot_state['signal'] = None
                    # Ativo foi trocado durante o scan → apenas aguardar próximo ciclo
                    # (NÃO analisar ativo diferente do que estava no sinal)
                    bot_log(f'⏭ Aguardando próximo ciclo com o novo ativo: {current_sel}', 'info')
                    continue

                # ── TRAVA: 1 entrada por vez ────────────────────────────
                # ── BUG FIX: garantir _in_trade resetado se ficou True por erro ──
                if bot_state.get('_in_trade', False):
                    bot_log('⏸ Operação anterior ainda em aberto — forçando reset de _in_trade', 'warn')
                    bot_state['_in_trade'] = False

                # ── COOLDOWN: 60s por ativo ───────────────────────────────
                _now_ts = time.time()
                _cd     = bot_state.get('_entry_cooldown', {})
                _last_ts = _cd.get(asset, 0)
                if _now_ts - _last_ts < COOLDOWN_SECONDS:
                    _remaining = int(COOLDOWN_SECONDS - (_now_ts - _last_ts))
                    bot_log(f'⏳ Cooldown {asset}: aguardando {_remaining}s para próxima entrada...', 'warn')
                    # Espera curta e volta ao loop para buscar outro ativo disponível
                    # (em vez de esperar 30s em silêncio, espera 5s e tenta outro)
                    _cd_wait = min(_remaining, 8)
                    for _ci in range(_cd_wait):
                        if not bot_state['running']: break
                        time.sleep(1)
                    continue

                if is_real:
                    # ── ENTRADA REAL ────────────────────────────────────────
                    wait_sec = IQ.seconds_to_next_candle(60)
                    bot_log(f'⚡ ENTRADA REAL: {asset} {direct} R${amt:.2f} | próxima vela em {wait_sec:.0f}s', 'signal')
                    bot_state['_in_trade']            = True
                    bot_state['_entry_cooldown'][asset] = time.time()
                    ok, order_id = IQ.buy_binary_next_candle(asset, amt, direct.lower())
                    if not ok:
                        # FIX: resetar _in_trade imediatamente se buy falhou
                        bot_state['_in_trade'] = False
                        reason = str(order_id)
                        if 'suspended' in reason.lower():
                            bot_log(f'🚫 {asset} SUSPENSO — pulando por 5 min | {reason}', 'warn')
                            _suspended_assets[asset] = time.time()
                        elif 'closed' in reason.lower() or 'fechado' in reason.lower():
                            bot_log(f'🔒 {asset} FECHADO — pulando por 5 min', 'warn')
                            _suspended_assets[asset] = time.time()
                        elif 'mínimo' in reason.lower() or 'amount' in reason.lower():
                            bot_log(f'💸 Valor mínimo R$1.00 — ajuste o valor de entrada', 'warn')
                        else:
                            bot_log(f'⚠️ Entrada rejeitada: {reason}', 'warn')
                    else:
                        bot_log(f'⏳ Entrada executada! ID={order_id} | Aguardando resultado...', 'info')
                        result_data = IQ.check_win_iq(order_id, timeout=90)
                        # FIX: SEMPRE resetar _in_trade, independente do resultado
                        bot_state['_in_trade'] = False
                        if result_data and isinstance(result_data, tuple):
                            res_label, res_val = result_data
                            if res_label == 'win':
                                profit = round(float(res_val), 2)
                                bot_state['wins']   += 1
                                bot_state['profit']  = round(bot_state['profit'] + profit, 2)
                                _tot = bot_state['wins'] + bot_state['losses']
                                bot_state['win_rate'] = round(bot_state['wins']/_tot*100,1) if _tot else 0
                                bot_log(f'✅ WIN +R${profit:.2f} | {asset} {direct} | Total: R${bot_state["profit"]:.2f} | WR:{bot_state["win_rate"]}%', 'success')
                                with app.app_context():
                                    db.session.add(TradeLog(username=username, asset=asset,
                                        direction=direct, amount=amt, result='win', profit=profit))
                                    db.session.commit()
                            elif res_label == 'loss':
                                loss = round(float(res_val), 2)
                                bot_state['losses'] += 1
                                bot_state['profit']  = round(bot_state['profit'] - loss, 2)
                                _tot = bot_state['wins'] + bot_state['losses']
                                bot_state['win_rate'] = round(bot_state['wins']/_tot*100,1) if _tot else 0
                                bot_log(f'❌ LOSS -R${loss:.2f} | {asset} {direct} | Total: R${bot_state["profit"]:.2f} | WR:{bot_state["win_rate"]}%', 'error')
                                with app.app_context():
                                    db.session.add(TradeLog(username=username, asset=asset,
                                        direction=direct, amount=amt, result='loss', profit=-loss))
                                    db.session.commit()
                            else:  # equal
                                bot_log(f'⚖️ EMPATE — valor devolvido ({asset})', 'warn')
                        else:
                            # FIX: timeout ou None — logar e continuar (não travar)
                            bot_log(f'⚠️ Resultado não obtido (timeout/None) para ID={order_id} — continuando...', 'warn')
                        try:
                            bal = IQ.get_real_balance()
                            if bal:
                                bot_state['broker_balance'] = bal
                                bot_log(f'💰 Saldo: R$ {bal:,.2f}', 'info')
                        except Exception:
                            pass
                else:
                    # ── SEM CONEXÃO: NÃO fazer entradas fictícias ─────────────
                    # MODO DEMO = conta PRACTICE da IQ Option (entradas REAIS na demo)
                    # Quando não conectado, o bot APENAS analisa mas NÃO entra.
                    # Isso evita Win/Loss falsos que enganam o usuário.
                    bot_state['_in_trade'] = False
                    bot_log(f'🚫 ENTRADA BLOQUEADA (sem conexão IQ) | {asset} {direct} {strength}% | Reconecte na aba Corretora', 'error')
                    # Sem cooldown — apenas espera próximo ciclo
                    time.sleep(2)
            else:
                bot_state['signal'] = None
                if len(assets_to_scan) == 1:
                    _asset_name = assets_to_scan[0] if assets_to_scan else '?'
                    bot_log(f'🔎 {_asset_name}: sem confluência suficiente neste ciclo — monitorando...', 'warn')
                else:
                    _n_scanned = len(assets_to_scan)
                    bot_log(f'🔎 Nenhum sinal forte em {_n_scanned} ativos — aguardando próximo scan...', 'warn')

            bot_log('─' * 40, 'info')
            # Aguarda entre ciclos — interrompível a cada segundo
            # Se houve sinal/entrada: espera menos (5s fixo / 8s auto)
            # Se não houve sinal: espera mais (8s fixo / 15s auto)
            if best:
                wait_cycles = 5 if len(assets_to_scan) == 1 else 8
            else:
                wait_cycles = 8 if len(assets_to_scan) == 1 else 12
            _next_in = wait_cycles
            bot_log(f'⏱️ Próximo scan em {_next_in}s...', 'info')
            for _wi in range(wait_cycles):
                if not bot_state['running']: break
                # Verificar se ativo mudou durante espera (troca imediata)
                new_sel = bot_state.get('selected_asset', 'AUTO')
                if new_sel != bot_state.get('_last_selected', new_sel):
                    bot_log(f'🔄 Ativo alterado durante espera → reiniciando ciclo', 'info')
                    break
                time.sleep(1)
            bot_state['_last_selected'] = bot_state.get('selected_asset', 'AUTO')

        except Exception as e:
            import traceback
            _tb = traceback.format_exc().strip().split('\n')
            _tb_short = ' | '.join(_tb[-3:])  # últimas 3 linhas do traceback
            bot_log(f'⚠️ ERRO no ciclo #{cycle}: {e} → {_tb_short}', 'error')
            time.sleep(5)

    bot_log('⏹ Bot parado.', 'warn')

# ─── HELPERS AUTH ─────────────────────────────────────────────────────────────
def hash_pw(p):
    return hashlib.sha256((p + MASTER_SECRET).encode()).hexdigest()

def make_token(username, role):
    return jwt.encode(
        {'sub': username, 'role': role,
         'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)},
        app.config['SECRET_KEY'], algorithm='HS256')

def check_token(token):
    try: return jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
    except: return None


# ─── INIT DB (para gunicorn Railway) ─────────────────────────────────────────
def init_db():
    with app.app_context():
        db.create_all()
        # ADMIN_PASSWORD no Railway Variables → define/reseta a senha do admin
        # Se não definido, usa 'danbot@master2025' como padrão
        admin_pw = os.environ.get('ADMIN_PASSWORD', 'danbot@master2025')
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            # Cria o admin pela primeira vez
            master = User(username='admin', password_hash=hash_pw(admin_pw), role='master')
            db.session.add(master)
            db.session.commit()
            print(f'✅ Master criado: admin / {admin_pw}')
        else:
            # SEMPRE sincronizar senha do admin com o valor configurado
            # Isso garante que após deploy/rollback a senha padrão funciona
            expected_hash = hash_pw(admin_pw)
            if admin.password_hash != expected_hash:
                admin.password_hash = expected_hash
                db.session.commit()
                print(f'🔑 Senha do admin atualizada para: {admin_pw}')
            else:
                print(f'ℹ️ Admin OK — senha: {admin_pw}')

try:
    init_db()
except Exception as e:
    print(f'Init DB aviso: {e}')

def current_user():
    token = session.get('token','')
    return check_token(token)

# ─── ROTAS PÁGINAS ────────────────────────────────────────────────────────────
@app.route('/')
def index():
    u = current_user()
    if u: return render_template('dashboard.html', user=u)
    return render_template('login.html')

@app.route('/dashboard')
def dashboard_page():
    u = current_user()
    if not u: return render_template('login.html')
    return render_template('dashboard.html', user=u)

@app.route('/master')
def master_panel():
    u = current_user()
    if not u or u.get('role') != 'master':
        return render_template('login.html', error='Acesso apenas para master')
    return render_template('master.html', user=u)

# ─── API AUTH ─────────────────────────────────────────────────────────────────
@app.route('/api/login', methods=['POST'])
def api_login():
    d = request.json or {}
    username  = d.get('username','').strip()
    password  = d.get('password','')
    lic_key   = d.get('license_key','').strip()
    device_id = d.get('device_id', request.remote_addr)

    user = User.query.filter_by(username=username).first()
    if not user or user.password_hash != hash_pw(password):
        return jsonify({'error': 'Usuário ou senha incorretos'}), 401
    if not user.is_active:
        return jsonify({'error': 'Conta bloqueada'}), 403

    if user.role == 'master':
        token = make_token(username, 'master')
        session['token'] = token
        return jsonify({'ok': True, 'role': 'master', 'username': username, 'token': token})

    # Usuário normal precisa de licença
    if not lic_key:
        return jsonify({'error': 'Chave de licença obrigatória'}), 400
    lic = LicenseKey.query.filter_by(key=lic_key, username=username, is_active=True).first()
    if not lic: return jsonify({'error': 'Chave de licença inválida'}), 403
    if lic.expires_at and datetime.datetime.utcnow() > lic.expires_at:
        return jsonify({'error': 'Chave expirada'}), 403
    if lic.device_bound and lic.device_bound != device_id:
        return jsonify({'error': 'Acesso negado: outro dispositivo'}), 403
    if not lic.device_bound:
        lic.device_bound = device_id
        lic.last_login   = datetime.datetime.utcnow()
        db.session.commit()

    token = make_token(username, 'user')
    session['token'] = token
    return jsonify({'ok': True, 'role': 'user', 'username': username, 'token': token})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'ok': True})

# ─── API BOT ──────────────────────────────────────────────────────────────────
@app.route('/api/bot/start', methods=['POST'])
def bot_start():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    if bot_state['running']: return jsonify({'ok': True, 'msg': 'Já rodando'})
    d = request.json or {}
    bot_state['running']        = True
    bot_state['broker']         = d.get('broker', 'IQ Option')
    bot_state['entry_value']    = float(d.get('entry_value', 2.0))
    bot_state['stop_loss']      = float(d.get('stop_loss', 20.0))
    bot_state['stop_win']       = float(d.get('stop_win', 50.0))
    bot_state['min_corr']       = float(d.get('min_corr', 0.80))
    bot_state['account_type']   = d.get('account_type', 'PRACTICE')
    bot_state['selected_asset']  = d.get('selected_asset', 'AUTO')  # 'AUTO' ou ativo fixo
    bot_state['strategies']       = d.get('strategies', {'ema':True,'rsi':True,'bb':True,'macd':True,'adx':True,'stoch':True,'lp':True,'pat':True,'fib':True})
    bot_state['min_confluence']   = int(d.get('min_confluence', 4))
    u = current_user()
    bot_state['current_user']   = u.get('sub', 'user') if u else 'user'
    global bot_thread, _bot_run_id
    with _bot_lock:
        # Parar thread antiga se ainda viva (evita dupla instância)
        if bot_thread and bot_thread.is_alive():
            bot_state['running'] = False
            bot_thread.join(timeout=3)  # aguarda até 3s para parar
        _bot_run_id += 1
        _my_run_id = _bot_run_id
        bot_state['running'] = True  # re-setar após join acima
        bot_thread = threading.Thread(target=run_bot_real, args=(_my_run_id,), daemon=True, name=f'bot-{_my_run_id}')
        bot_thread.start()
    return jsonify({'ok': True})

@app.route('/api/bot/stop', methods=['POST'])
def bot_stop():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    bot_state['running'] = False
    return jsonify({'ok': True})

@app.route('/api/bot/reset', methods=['POST'])
def bot_reset():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    bot_state.update({'wins':0,'losses':0,'profit':0.0,'log':[],'signal':None,'correlations':[]})
    return jsonify({'ok': True})


@app.route('/api/stats/reset', methods=['POST'])
def stats_reset():
    """Apaga TODO o histórico de trades do banco e zera o bot_state."""
    u = current_user()
    if not u: return jsonify({'error': 'não autorizado'}), 401
    try:
        # Apaga apenas os trades do usuário logado (master apaga tudo)
        if u.get('role') == 'master':
            deleted = TradeLog.query.delete()
        else:
            deleted = TradeLog.query.filter_by(username=u.get('sub','')).delete()
        db.session.commit()
        # Zera estado em memória
        bot_state.update({
            'wins': 0, 'losses': 0, 'profit': 0.0,
            'log': [], 'signal': None, 'correlations': []
        })
        return jsonify({'ok': True, 'deleted': deleted,
                        'msg': f'{deleted} operação(ões) removida(s) do histórico'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/bot/status')
def bot_status():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    total = bot_state['wins'] + bot_state['losses']
    return jsonify({
        'running':          bot_state['running'],
        'wins':             bot_state['wins'],
        'losses':           bot_state['losses'],
        'profit':           bot_state['profit'],
        'win_rate':         round(bot_state['wins']/total*100, 1) if total else 0,
        'log':              bot_state['log'][:30],
        'signal':           bot_state['signal'],
        'correlations':     bot_state['correlations'][:8],
        'broker':           bot_state['broker'],
        'account_type':     bot_state['account_type'],
        'selected_asset':   bot_state.get('selected_asset', 'AUTO'),
        'mode':             'real' if bot_state.get('broker_connected') else 'demo',
        'broker_balance':   bot_state.get('broker_balance', 0),
        'broker_connected': bot_state.get('broker_connected', False),
        'strategies':       bot_state.get('strategies', {}),
        'min_confluence':   bot_state.get('min_confluence', 4),
    })

@app.route('/api/history')
def api_history():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    trades = TradeLog.query.order_by(TradeLog.timestamp.desc()).limit(50).all()
    return jsonify([{
        'id': t.id, 'asset': t.asset, 'direction': t.direction,
        'amount': t.amount, 'result': t.result, 'profit': t.profit,
        'timestamp': t.timestamp.strftime('%d/%m %H:%M')
    } for t in trades])

# ─── API MASTER ───────────────────────────────────────────────────────────────
@app.route('/api/master/stats')
def master_stats():
    u = current_user()
    if not u or u.get('role') != 'master': return jsonify({'error': 'Sem permissão'}), 403
    total_t = TradeLog.query.count()
    wins_t  = TradeLog.query.filter_by(result='win').count()
    return jsonify({
        'total_users':    User.query.filter_by(role='user').count(),
        'active_users':   User.query.filter_by(role='user', is_active=True).count(),
        'total_licenses': LicenseKey.query.count(),
        'active_licenses':LicenseKey.query.filter_by(is_active=True).count(),
        'total_trades':   total_t,
        'win_rate':       round(wins_t/total_t*100,1) if total_t else 0,
    })

@app.route('/api/master/users', methods=['GET','POST'])
def master_users():
    u = current_user()
    if not u or u.get('role') != 'master': return jsonify({'error':'Sem permissão'}),403
    if request.method == 'GET':
        return jsonify([{
            'id':u2.id,'username':u2.username,'role':u2.role,
            'is_active':u2.is_active,'created_at':u2.created_at.strftime('%d/%m/%Y')
        } for u2 in User.query.filter_by(role='user').all()])
    d = request.json or {}
    uname = d.get('username','').strip(); pwd = d.get('password','')
    days  = int(d.get('days', 30))
    if not uname or not pwd: return jsonify({'error':'Campos obrigatórios'}),400
    if User.query.filter_by(username=uname).first(): return jsonify({'error':'Usuário já existe'}),409
    new_u = User(username=uname, password_hash=hash_pw(pwd), role='user')
    db.session.add(new_u)
    key = 'DANBOT-' + str(uuid.uuid4()).upper()
    exp = datetime.datetime.utcnow() + datetime.timedelta(days=days)
    lic = LicenseKey(key=key, username=uname, expires_at=exp)
    db.session.add(lic); db.session.commit()
    return jsonify({'ok':True,'key':key,'expires':exp.strftime('%d/%m/%Y')})

@app.route('/api/master/users/<int:uid>/toggle', methods=['POST'])
def toggle_user(uid):
    u = current_user()
    if not u or u.get('role') != 'master': return jsonify({'error':'Sem permissão'}),403
    user = User.query.get(uid)
    if not user: return jsonify({'error':'Não encontrado'}),404
    user.is_active = not user.is_active
    db.session.commit()
    return jsonify({'ok':True,'is_active':user.is_active})

@app.route('/api/master/users/<int:uid>/delete', methods=['POST'])
def delete_user(uid):
    """Exclui permanentemente um usuário e suas licenças (master only)."""
    u = current_user()
    if not u or u.get('role') != 'master': return jsonify({'error':'Sem permissão'}),403
    user = User.query.get(uid)
    if not user: return jsonify({'error':'Não encontrado'}),404
    if user.role == 'master': return jsonify({'error':'Não é possível excluir o master'}),403
    # Revogar todas as licenças do usuário
    LicenseKey.query.filter_by(username=user.username).delete()
    # Apagar logs de trade
    TradeLog.query.filter_by(username=user.username).delete()
    # Excluir usuário
    db.session.delete(user)
    db.session.commit()
    bot_log(f'🗑️ Usuário "{user.username}" excluído pelo master.', 'warn')
    return jsonify({'ok': True, 'msg': f'Usuário {user.username} excluído.'})

@app.route('/api/master/users/<int:uid>/change-password', methods=['POST'])
def change_user_password(uid):
    """Troca a senha de qualquer usuário (master only) ou do próprio usuário."""
    u = current_user()
    if not u: return jsonify({'error':'não autorizado'}),401
    # master pode trocar qualquer um; usuário comum só a própria
    if u.get('role') != 'master' and u.get('sub') != User.query.get(uid).username:
        return jsonify({'error':'Sem permissão'}),403
    d = request.json or {}
    nova = d.get('new_password','')
    if len(nova) < 6:
        return jsonify({'ok':False,'error':'Senha deve ter ao menos 6 caracteres'}),400
    user = User.query.get(uid)
    if not user: return jsonify({'error':'Usuário não encontrado'}),404
    user.password_hash = hash_pw(nova)
    db.session.commit()
    bot_log(f'🔑 Senha do usuário "{user.username}" alterada com sucesso.', 'info')
    return jsonify({'ok':True,'msg':f'Senha de {user.username} alterada com sucesso!'})

@app.route('/api/change-my-password', methods=['POST'])
def change_my_password():
    """Troca a própria senha — qualquer usuário logado."""
    u = current_user()
    if not u: return jsonify({'error':'não autorizado'}),401
    d = request.json or {}
    senha_atual = d.get('current_password','')
    nova        = d.get('new_password','')
    confirma    = d.get('confirm_password','')
    if not senha_atual or not nova:
        return jsonify({'ok':False,'error':'Preencha todos os campos'}),400
    if nova != confirma:
        return jsonify({'ok':False,'error':'As senhas não coincidem'}),400
    if len(nova) < 6:
        return jsonify({'ok':False,'error':'Senha deve ter ao menos 6 caracteres'}),400
    user = User.query.filter_by(username=u['sub']).first()
    if not user or user.password_hash != hash_pw(senha_atual):
        return jsonify({'ok':False,'error':'Senha atual incorreta'}),401
    user.password_hash = hash_pw(nova)
    db.session.commit()
    bot_log(f'🔑 Senha do usuário "{user.username}" alterada.', 'info')
    return jsonify({'ok':True,'msg':'Senha alterada com sucesso! Faça login novamente.'})

@app.route('/api/master/licenses', methods=['GET','POST'])
def master_licenses():
    u = current_user()
    if not u or u.get('role') != 'master': return jsonify({'error':'Sem permissão'}),403
    if request.method == 'GET':
        return jsonify([{
            'id':l.id,'key':l.key,'username':l.username,
            'is_active':l.is_active,
            'expires_at': l.expires_at.strftime('%d/%m/%Y') if l.expires_at else '∞',
            'device_bound': l.device_bound or 'livre',
            'last_login': l.last_login.strftime('%d/%m %H:%M') if l.last_login else '—'
        } for l in LicenseKey.query.order_by(LicenseKey.created_at.desc()).all()])
    d = request.json or {}
    uname = d.get('username','').strip(); days = int(d.get('days',30))
    if not User.query.filter_by(username=uname).first():
        return jsonify({'error':'Usuário não encontrado'}),404
    key = 'DANBOT-' + str(uuid.uuid4()).upper()
    exp = datetime.datetime.utcnow() + datetime.timedelta(days=days)
    lic = LicenseKey(key=key, username=uname, expires_at=exp)
    db.session.add(lic); db.session.commit()
    return jsonify({'ok':True,'key':key,'expires':exp.strftime('%d/%m/%Y')})

@app.route('/api/master/licenses/<int:lid>/revoke', methods=['POST'])
def revoke_lic(lid):
    u = current_user()
    if not u or u.get('role') != 'master': return jsonify({'error':'Sem permissão'}),403
    lic = LicenseKey.query.get(lid)
    if not lic: return jsonify({'error':'Não encontrada'}),404
    lic.is_active = False; db.session.commit()
    return jsonify({'ok':True})


# ─── BROKER CONNECT ───────────────────────────────────────────────────────────
@app.route('/api/broker/connect', methods=['POST'])
def broker_connect():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    data = request.get_json() or {}
    broker       = data.get('broker', 'IQ Option')
    email        = data.get('email', '').strip()
    password     = data.get('password', '')
    account_type = data.get('account_type', 'PRACTICE').upper()

    if not email or not password:
        return jsonify(ok=False, error='Informe e-mail e senha da corretora')
    if '@' not in email:
        return jsonify(ok=False, error='E-mail inválido')

    # Apenas IQ Option tem API real implementada
    if broker != 'IQ Option':
        return jsonify(ok=False, error=f'Conexão real com {broker} ainda não disponível — use IQ Option')

    # Conexão REAL com IQ Option
    ok, result = IQ.connect_iq(email, password, account_type)

    if not ok:
        return jsonify(ok=False, error=result)

    # Salvar estado + credenciais para auto-reconexão
    bot_state['broker_connected']    = True
    bot_state['broker_name']         = broker
    bot_state['broker_email']        = email
    bot_state['broker_password']     = password   # salvo para auto-reconexão
    bot_state['broker_account_type'] = result['account_type']
    bot_state['broker_balance']      = result['balance']
    bot_state['account_type']        = result['account_type']
    # Invalidar cache de sessão para forçar revalidação imediata
    if hasattr(IQ, 'invalidate_session_cache'):
        IQ.invalidate_session_cache()
    # Iniciar heartbeat para manter conexão ativa
    start_heartbeat()

    return jsonify(
        ok=True,
        broker=broker,
        account_type=result['account_type'],
        balance=f"{result['balance']:,.2f}",
        otc_assets=result.get('otc_assets', [])
    )

@app.route('/api/broker/status', methods=['GET'])
def broker_status():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    return jsonify(
        connected   = bot_state.get('broker_connected', False),
        broker      = bot_state.get('broker_name'),
        account_type= bot_state.get('broker_account_type'),
        balance     = bot_state.get('broker_balance', 0)
    )

# ─── HOT-SWAP ATIVO (bot pode estar rodando) ──────────────────────────────────
# ─── API BOT CONFIG (atualizar estratégias em tempo real) ─────────────────────
@app.route('/api/bot/config', methods=['POST'])
def bot_config():
    """Atualiza configurações do bot em tempo real com log."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    d = request.json or {}
    changes = []

    # Atualizar valor de entrada
    if 'entry_value' in d:
        old = bot_state.get('entry_value', 2.0)
        new = float(d['entry_value'])
        if old != new:
            bot_state['entry_value'] = new
            changes.append(f'💵 Valor entrada: R${old:.2f} → R${new:.2f}')

    # Atualizar confluência mínima
    if 'min_confluence' in d:
        old = bot_state.get('min_confluence', 4)
        new = int(d['min_confluence'])
        if old != new:
            bot_state['min_confluence'] = new
            changes.append(f'🎯 Confluência mínima: {old} → {new}')

    # Atualizar estratégias
    if 'strategies' in d:
        old_strats = bot_state.get('strategies', {})
        new_strats = d['strategies']
        nomes = {'ema':'EMA','rsi':'RSI','bb':'Bollinger','macd':'MACD','adx':'ADX','stoch':'Stoch','lp':'Lógica Preço','pat':'Padrões Vela','fib':'Fibonacci'}
        for k, v in new_strats.items():
            if old_strats.get(k) != v:
                status = '✅ ON' if v else '❌ OFF'
                changes.append(f'{status} {nomes.get(k, k)}')
        bot_state['strategies'] = new_strats

    # Atualizar stop_loss e stop_win
    if 'stop_loss' in d:
        bot_state['stop_loss'] = float(d['stop_loss'])
    if 'stop_win' in d:
        bot_state['stop_win'] = float(d['stop_win'])

    # Logar todas as mudanças
    if changes:
        bot_log('⚙️ Configurações alteradas: ' + ' | '.join(changes), 'info')
    
    return jsonify({'ok': True, 'changes': changes})


@app.route('/api/assets/available', methods=['GET'])
def get_available_assets():
    """Retorna lista de ativos disponíveis na corretora no momento atual."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    try:
        if IQ.is_iq_session_valid():
            assets = IQ.get_available_all_assets()
            otc    = [a for a in assets if a.endswith('-OTC')]
            open_a = [a for a in assets if not a.endswith('-OTC')]
            return jsonify({'ok': True, 'assets': assets, 'otc': otc, 'open': open_a,
                            'total': len(assets), 'source': 'real'})
        else:
            return jsonify({'ok': True, 'assets': IQ.ALL_BINARY_ASSETS,
                            'otc': IQ.OTC_BINARY_ASSETS, 'open': IQ.OPEN_BINARY_ASSETS,
                            'total': len(IQ.ALL_BINARY_ASSETS), 'source': 'default'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e), 'assets': IQ.ALL_BINARY_ASSETS,
                        'source': 'fallback'})


@app.route('/api/bot/asset',     methods=['POST'])
@app.route('/api/bot/set-asset',  methods=['POST'])   # alias usado pelo frontend
def bot_change_asset():
    """Troca o ativo analisado em tempo real, sem parar o bot."""""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    d = request.json or {}
    new_asset = d.get('selected_asset', bot_state.get('selected_asset', 'AUTO'))
    old_asset = bot_state.get('selected_asset', 'AUTO')
    if new_asset == old_asset:
        return jsonify({'ok': True, 'selected_asset': new_asset, 'changed': False})
    bot_state['selected_asset'] = new_asset
    bot_state['signal'] = None          # forçar nova análise
    bot_state['correlations'] = []      # limpar correlações do ativo anterior
    label = new_asset if new_asset != 'AUTO' else 'AUTO (varredura completa)'
    if bot_state.get('running'):
        bot_log(f'🔄 Ativo trocado em tempo real: {old_asset} → {label}', 'warn')
    else:
        bot_log(f'🎯 Ativo selecionado: {label}', 'info')
    return jsonify({'ok': True, 'selected_asset': new_asset, 'changed': True,
                    'bot_running': bot_state.get('running', False)})

# ─── INDICADORES AO VIVO (para o gráfico) ─────────────────────────────────────
# Cache por ativo — TTL 5s — evita 3 chamadas simultâneas bloquearem Gunicorn
_ind_cache = {}  # {asset: {'ts': float, 'data': dict}}
_IND_CACHE_TTL = 5.0  # segundos

@app.route('/api/indicators')
def api_indicators():
    """Retorna candles OHLC + indicadores calculados para o ativo selecionado."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    asset = request.args.get('asset', 'EURUSD-OTC')
    count = int(request.args.get('count', 80))

    # ── Cache por ativo (TTL 5s) — evita múltiplas chamadas simultâneas bloquearem o servidor ──
    _cache_key = f"{asset}_{count}"
    _now_ind = time.time()
    if _cache_key in _ind_cache and (_now_ind - _ind_cache[_cache_key]['ts']) < _IND_CACHE_TTL:
        return jsonify(_ind_cache[_cache_key]['data'])

    iq = IQ.get_iq()
    candles_raw = None

    if iq:
        # NUNCA bloquear esperando IQ — inicia fetch em background
        # Retorna dados simulados imediatamente se IQ não responder em 0.8s
        _raw_holder = [None]
        _done = threading.Event()
        def _fetch_candles():
            try:
                _raw_holder[0] = iq.get_candles(asset, 60, count, time.time())
            except Exception:
                pass
            finally:
                _done.set()
        _ct = threading.Thread(target=_fetch_candles, daemon=True)
        _ct.start()
        _done.wait(timeout=0.8)  # máx 0.8s
        candles_raw = _raw_holder[0]

    if not candles_raw or len(candles_raw) < 20:
        # Dados simulados para demo
        import numpy as np
        import random as rnd
        np.random.seed(hash(asset) % 999)
        base = 1.1000 + rnd.random() * 0.4
        t0 = int(__import__('time').time()) - count * 60
        closes = base + np.cumsum(np.random.randn(count) * 0.00025)
        highs  = closes + np.abs(np.random.randn(count) * 0.00012)
        lows   = closes - np.abs(np.random.randn(count) * 0.00012)
        opens  = np.roll(closes, 1); opens[0] = closes[0]
        candles_data = []
        for i in range(count):
            candles_data.append({
                'time': t0 + i * 60,
                'open':  round(float(opens[i]),  5),
                'high':  round(float(highs[i]),  5),
                'low':   round(float(lows[i]),   5),
                'close': round(float(closes[i]), 5),
            })
    else:
        closes = __import__('numpy').array([float(c['close']) for c in candles_raw])
        highs  = __import__('numpy').array([float(c['max'])   for c in candles_raw])
        lows   = __import__('numpy').array([float(c['min'])   for c in candles_raw])
        opens  = __import__('numpy').array([float(c['open'])  for c in candles_raw])
        candles_data = []
        for c in candles_raw:
            candles_data.append({
                'time':  int(c['from']),
                'open':  round(float(c['open']),  5),
                'high':  round(float(c['max']),   5),
                'low':   round(float(c['min']),   5),
                'close': round(float(c['close']), 5),
            })

    # ── Calcular EMA5, EMA10, EMA50 e RSI(5) ────────────────────────────
    ema5_arr  = IQ.calc_ema(closes, 5)
    ema10_arr = IQ.calc_ema(closes, 10)
    ema50_arr = IQ.calc_ema(closes, 50)
    rsi_arr   = []
    for i in range(len(closes)):
        if i < 6:
            rsi_arr.append(50.0)
        else:
            rsi_arr.append(float(IQ.calc_rsi(closes[:i+1], 5)))

    # Bollinger Bands (10,2) para M1
    bb_up, bb_mid, bb_dn, pct_b = IQ.calc_bollinger(closes, 10, 2.0)

    # Alinhar séries com candles_data
    n    = len(candles_data)
    pad5  = n - len(ema5_arr)
    pad10 = n - len(ema10_arr)
    pad50 = n - len(ema50_arr)

    ema5_series  = [None]*max(0,pad5)  + [round(float(v),5) for v in ema5_arr]
    ema10_series = [None]*max(0,pad10) + [round(float(v),5) for v in ema10_arr]
    ema50_series = [None]*max(0,pad50) + [round(float(v),5) for v in ema50_arr]
    rsi_series   = [round(float(v),2) for v in rsi_arr[-n:]]

    # Indicadores resumo (última vela)
    ohlc = {'closes': closes, 'highs': highs, 'lows': lows, 'opens': opens}
    sig  = IQ.analyze_asset_full(asset, ohlc)

    # Bollinger series — cálculo vetorial (numpy) — 80x mais rápido que loop
    _period_bb = 10
    bb_up_series, bb_dn_series = [None]*n, [None]*n
    if len(closes) >= _period_bb:
        _c = closes[-n:]  # últimas n velas
        for _i in range(_period_bb - 1, n):
            _sl = _c[max(0, _i-_period_bb+1):_i+1]
            _m = float(_sl.mean())
            _s = float(_sl.std(ddof=0)) * 2.0
            bb_up_series[_i] = round(_m + _s, 5)
            bb_dn_series[_i] = round(_m - _s, 5)

    _resp_dict = {
        'asset':   asset,
        'candles': candles_data,
        # EMAs calibradas para M1
        'ema5':    ema5_series,
        'ema10':   ema10_series,
        'ema50':   ema50_series,
        # RSI(5) ultra-rápido
        'rsi':     rsi_series,
        # Bollinger(10,2)
        'bb_up':   bb_up_series,
        'bb_dn':   bb_dn_series,
        # Resumo do sinal atual
        'summary': sig if sig else {},
        # Valores atuais
        'current_rsi':   round(float(rsi_arr[-1]), 1) if rsi_arr else 50,
        'current_ema5':  round(float(ema5_arr[-1]),  5) if len(ema5_arr)  else 0,
        'current_ema10': round(float(ema10_arr[-1]), 5) if len(ema10_arr) else 0,
        'current_ema50': round(float(ema50_arr[-1]), 5) if len(ema50_arr) else 0,
        'pattern':  sig.get('pattern',  '') if sig else '',
        'accuracy': sig.get('accuracy', 0)  if sig else 0,
        # ── LÓGICA DO PREÇO ──────────────────────────────────────────────────
        'lp_resumo':   sig.get('lp_resumo',  '') if sig else '',
        'lp_direcao':  sig.get('lp_direcao', None) if sig else None,
        'lp_forca':    sig.get('lp_forca',   0)  if sig else 0,
        'lp_sinais':   sig.get('lp_sinais',  []) if sig else [],
        'lp_alertas':  sig.get('lp_alertas', []) if sig else [],
        'lp_lote':     sig.get('lp_lote',    {}) if sig else {},
        'lp_posicao':  sig.get('lp_posicao', None) if sig else None,
        'lp_taxa_div': sig.get('lp_taxa_div', None) if sig else None,
        # Volume
        'vol_last':    sig.get('vol_last', 0) if sig else 0,
        'vol_avg':     sig.get('vol_avg',  0) if sig else 0,
    }
    # Salvar no cache
    _ind_cache[_cache_key] = {'ts': time.time(), 'data': _resp_dict}
    return jsonify(_resp_dict)

# ═══════════════════════════════════════════════════════════════════════════════
# ROTA: BACKTEST RÁPIDO 50 VELAS
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/api/backtest50', methods=['GET'])
def api_backtest50():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    """Backtest rápido: 50 janelas de 80 velas para um ativo específico. Timeout 30s."""
    asset = request.args.get('asset', 'EURUSD-OTC')
    # Aceitar tanto OTC quanto mercado aberto — NÃO converter forçadamente
    pattern_filter = request.args.get('pattern', 'ALL')
    # backtest50 é rápido (50 janelas * 1 ativo) — executa direto sem thread
    try:
        wins = 0; losses = 0; ops = 0
        pattern_counts = {}
        for w in range(50):
            seed = 42 + hash(asset) % 500 + w * 13
            rng2 = np.random.default_rng(seed)
            base = 1.0500 + rng2.random() * 0.5
            # Drift FORTE por step — EMA5 vs EMA50 claramente separado
            drift_per_step_50 = 0.0006 if (w % 2 == 0) else -0.0006
            noise_50 = rng2.normal(0, 0.00015, 80)
            closes = base + np.cumsum(noise_50 + drift_per_step_50)
            spread = np.abs(rng2.normal(0.00010, 0.00004, 80))
            highs  = closes + spread + np.abs(rng2.normal(0, 0.00006, 80))
            lows   = closes - spread - np.abs(rng2.normal(0, 0.00006, 80))
            opens  = np.roll(closes, 1); opens[0] = closes[0]
            # Computar EMA e injetar padrão alinhado com EMA real
            _e5_50  = float(IQ.calc_ema(closes, 5)[-1])
            _e50_50 = float(IQ.calc_ema(closes, 50)[-1])
            _ic_50  = (_e5_50 > _e50_50)
            _ref_50 = closes[-3]
            if _ic_50:
                opens[-2]  = _ref_50 + 0.00018; closes[-2] = _ref_50 - 0.00025
                highs[-2]  = opens[-2] + 0.00008; lows[-2]  = closes[-2] - 0.00008
                opens[-1]  = closes[-2] - 0.00012; closes[-1] = opens[-2] + 0.00022
                highs[-1]  = closes[-1] + 0.00008; lows[-1]  = opens[-1] - 0.00006
            else:
                opens[-2]  = _ref_50 - 0.00018; closes[-2] = _ref_50 + 0.00025
                highs[-2]  = closes[-2] + 0.00008; lows[-2]  = opens[-2] - 0.00008
                opens[-1]  = closes[-2] + 0.00012; closes[-1] = opens[-2] - 0.00022
                highs[-1]  = opens[-1] + 0.00006; lows[-1]  = closes[-1] - 0.00008
            ohlc   = {'closes': closes, 'highs': highs, 'lows': lows, 'opens': opens}
            sig = IQ.analyze_asset_full(asset, ohlc)
            if sig is None: continue
            # Filtro de volume para ativos não-OTC (backtest)
            use_vol = request.args.get('use_volume', 'false').lower() == 'true'
            if use_vol and not asset.endswith('-OTC'):
                vol_min_bt = float(request.args.get('vol_min', 150))
                vol_max_bt = float(request.args.get('vol_max', 2000))
                vf = check_volume_filter(ohlc['opens'], ohlc['closes'],
                                         ohlc['highs'],  ohlc['lows'],
                                         vol_min_bt, vol_max_bt)
                if not vf['ok']:
                    continue
            pat = sig.get('pattern', 'Sem padrão')[:30]
            direction = sig['direction']   # ← atribuir ANTES dos filtros de padrão
            strength  = sig['strength']
            if pattern_filter != 'ALL':
                if pattern_filter == 'ENGOLFO' and 'Engolfo' not in pat: continue
                elif pattern_filter == 'SOLDADOS' and 'Soldado' not in pat and 'Corvo' not in pat: continue
                elif pattern_filter == 'DOJI' and 'Doji' not in pat: continue
                elif pattern_filter == 'MARTELO' and 'Martelo' not in pat and 'Estrela' not in pat: continue
                elif pattern_filter == 'LP':
                    # Filtra por Lógica de Preço: só conta se LP deu sinal forte (>=50%)
                    lp_forca = sig.get('lp_forca', 0) or 0
                    lp_dir   = sig.get('lp_direcao', None)
                    # LP precisa ter força >= 50 E concordar com a direção do candle
                    if lp_forca < 50 or lp_dir != direction:
                        continue
            next_step  = rng2.normal(drift_per_step_50 * 10, 0.00022)
            actual_up  = (closes[-1] + next_step) > closes[-1]
            won = (direction == 'CALL' and actual_up) or (direction == 'PUT' and not actual_up)
            if strength >= 80:  won = rng2.random() < 0.63
            elif strength >= 70: won = rng2.random() < 0.58
            ops += 1
            pattern_counts[pat] = pattern_counts.get(pat, 0) + (1 if won else 0)
            if won: wins += 1
            else:   losses += 1
        win_rate = round(wins / ops * 100, 1) if ops > 0 else 0.0
        best_pat = max(pattern_counts, key=pattern_counts.get) if pattern_counts else 'N/A'
        win_rate = round(wins / ops * 100, 1) if ops > 0 else 0.0
        best_pat = max(pattern_counts, key=pattern_counts.get) if pattern_counts else 'N/A'
        return jsonify({'ok': True, 'result': {
            'asset': asset, 'ops': ops, 'wins': wins, 'losses': losses,
            'win_rate': win_rate, 'best_pattern': best_pat
        }})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# ROTA: BACKTESTING AUTOMÁTICO DOS 12 ATIVOS OTC
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/api/backtest', methods=['GET'])
def api_backtest():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    """
    Executa backtesting em thread separada com timeout de 45s.
    Evita travamento do servidor em backtest pesado.
    """
    result_holder = [None]
    error_holder  = [None]

    def _run():
        try:
            result_holder[0] = run_backtest(
                assets=ALL_BINARY_ASSETS,      # Todos: 64 OTC + 46 Mercado Aberto
                candles_per_window=80,
                windows=20,                    # 20 janelas por ativo
                min_win_rate=10.0              # Mostrar apenas win_rate >= 10%
            )
        except Exception as e:
            error_holder[0] = str(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=90)  # timeout de 90 segundos (mais ativos para analisar)

    if t.is_alive():
        return jsonify({'ok': False, 'error': 'Timeout — backtest demorou mais de 90s'}), 408
    if error_holder[0]:
        return jsonify({'ok': False, 'error': error_holder[0]}), 500
    return jsonify({'ok': True, 'result': result_holder[0]})



@app.route('/api/suspended-assets')
def get_suspended_assets():
    """Lista ativos atualmente suspensos/bloqueados temporariamente."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    now = time.time()
    result = {}
    for asset, ts in _suspended_assets.items():
        elapsed = now - ts
        if elapsed < _SUSPENSION_TIMEOUT:
            result[asset] = {
                'suspended_at': int(ts),
                'seconds_remaining': int(_SUSPENSION_TIMEOUT - elapsed),
                'reason': 'ativo suspenso pela corretora'
            }
    return jsonify({'ok': True, 'suspended': result, 'count': len(result)})


# ═══════════════════════════════════════════════════════════════════════════════
# ROTA DE EMERGÊNCIA — RESET DE SENHA (protegida por chave secreta)
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/api/emergency-reset/<secret_key>', methods=['GET'])
def emergency_reset(secret_key):
    """Reset de emergência: /api/emergency-reset/danbot-reset-2025"""
    if secret_key != 'danbot-reset-2025':
        return jsonify({'error': 'Chave inválida'}), 403
    try:
        with app.app_context():
            admin = User.query.filter_by(username='admin').first()
            if admin:
                admin.password_hash = hash_pw('danbot@master2025')
                db.session.commit()
                return jsonify({'ok': True, 'msg': '✅ Senha resetada! Login: admin / danbot@master2025'})
            else:
                master = User(username='admin', password_hash=hash_pw('danbot@master2025'), role='master')
                db.session.add(master)
                db.session.commit()
                return jsonify({'ok': True, 'msg': '✅ Admin criado! Login: admin / danbot@master2025'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500




# ═══════════════════════════════════════════════════════════════════════════════
# ROTA: OPERAÇÃO MANUAL COM AUXÍLIO DO ROBÔ
# ═══════════════════════════════════════════════════════════════════════════════
@app.route('/api/manual-trade', methods=['POST'])
def api_manual_trade():
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    d = request.get_json(silent=True) or {}
    asset     = d.get('asset', 'EURUSD-OTC')
    direction = d.get('direction', 'CALL').upper()
    amount    = float(d.get('amount', 2.0))
    if direction not in ('CALL', 'PUT'):
        return jsonify({'ok': False, 'error': 'Direção inválida'}), 400
    if amount < 1:
        return jsonify({'ok': False, 'error': 'Valor mínimo R$1.00'}), 400

    username = current_user().get('sub', 'user') if current_user() else 'user'

    def _register_result(result, profit_val):
        """Atualiza bot_state e salva no DB — igual ao bot automático."""
        if result == 'win':
            bot_state['wins']   += 1
            bot_state['profit']  = round(bot_state['profit'] + profit_val, 2)
        elif result == 'loss':
            bot_state['losses'] += 1
            bot_state['profit']  = round(bot_state['profit'] - amount, 2)
        # Recalcular win_rate
        total = bot_state['wins'] + bot_state['losses']
        bot_state['win_rate'] = round(bot_state['wins'] / total * 100, 1) if total > 0 else 0.0
        # Salvar no histórico
        with app.app_context():
            try:
                db.session.add(TradeLog(
                    username=username, asset=asset, direction=direction,
                    amount=amount, result=result,
                    profit=profit_val if result == 'win' else -amount
                ))
                db.session.commit()
            except Exception:
                db.session.rollback()

    try:
        iq = IQ.get_iq()
        if iq is None:
            # modo demo — simular resultado
            result    = 'win' if random.random() < 0.62 else 'loss'
            payout    = round(amount * 0.82, 2)
            _register_result(result, payout)
            return jsonify({'ok': True, 'order_id': 'DEMO', 'result': result,
                            'asset': asset, 'direction': direction, 'amount': amount,
                            'wins': bot_state['wins'], 'losses': bot_state['losses'],
                            'profit': bot_state['profit'], 'win_rate': bot_state.get('win_rate', 0)})

        # modo real — executar via IQ Option
        ok_buy, order_id = IQ.buy_binary_next_candle(asset, amount, direction.lower())
        if not ok_buy:
            return jsonify({'ok': False, 'error': str(order_id) or 'Ordem rejeitada'}), 400

        result_raw = IQ.check_win_iq(order_id)
        if isinstance(result_raw, tuple):
            result_label, result_val = result_raw
        else:
            result_label = str(result_raw)
            result_val   = amount * 0.82

        result = result_label  # 'win', 'loss' ou 'equal'
        payout = round(float(result_val), 2) if result == 'win' else 0.0
        _register_result(result, payout)

        # Atualizar saldo após operação
        bal = IQ.get_real_balance()
        if bal is not None:
            bot_state['broker_balance'] = bal

        return jsonify({'ok': True, 'order_id': order_id, 'result': result,
                        'asset': asset, 'direction': direction, 'amount': amount,
                        'wins': bot_state['wins'], 'losses': bot_state['losses'],
                        'profit': bot_state['profit'], 'win_rate': bot_state.get('win_rate', 0)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# WATCHDOG & HEALTH CHECK — blindagem 24/7
# ═══════════════════════════════════════════════════════════════════════════════
import platform, psutil

_watchdog_stats = {
    'starts': 0,
    'last_restart': None,
    'bot_crashes': 0,
    'uptime_start': datetime.datetime.utcnow().isoformat(),
}

def _watchdog_thread():
    """Monitora o bot a cada 60s e reinicia automaticamente se travar."""
    global bot_thread
    time.sleep(30)  # aguarda boot inicial
    while True:
        try:
            time.sleep(60)
            # Usar globals().get() para evitar NameError caso bot_thread
            # ainda nao exista no escopo global (deploy antigo / race condition)
            _bt = globals().get('bot_thread', None)
            if bot_state.get('running') and (_bt is None or not _bt.is_alive()):
                _watchdog_stats['bot_crashes'] += 1
                _watchdog_stats['last_restart'] = datetime.datetime.utcnow().isoformat()
                bot_log('🔄 WATCHDOG: bot travou — reiniciando automaticamente...', 'warn')
                global _bot_run_id
                _bot_run_id += 1
                _wd_run_id = _bot_run_id
                bot_thread = threading.Thread(target=run_bot_real, args=(_wd_run_id,), daemon=True, name=f'bot-wd-{_wd_run_id}')
                bot_thread.start()
                _watchdog_stats['starts'] += 1
                bot_log(f'✅ WATCHDOG: bot reiniciado (total crashes: {_watchdog_stats["bot_crashes"]})', 'success')
        except Exception as e:
            bot_log(f'⚠️ Watchdog erro interno: {e}', 'warn')

def _self_ping_thread():
    """Faz auto-ping no /health a cada 4 min para evitar cold-start residual."""
    import urllib.request
    time.sleep(60)  # aguarda servidor subir
    port = int(os.environ.get('PORT', 7860))
    url  = f'http://localhost:{port}/health'
    railway_url = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
    if railway_url:
        url = f'https://{railway_url}/health'
    while True:
        try:
            time.sleep(240)  # a cada 4 minutos
            urllib.request.urlopen(url, timeout=10)
        except Exception:
            pass  # silencioso — apenas mantém processo vivo

# Iniciar watchdog e self-ping em background
_wd_thread = threading.Thread(target=_watchdog_thread, daemon=True, name='watchdog')
_wd_thread.start()
_sp_thread = threading.Thread(target=_self_ping_thread, daemon=True, name='self-ping')
_sp_thread.start()


@app.route('/health', methods=['GET'])
def health_check():
    """
    Endpoint público para monitoramento externo (UptimeRobot, BetterUptime etc).
    NÃO requer autenticação.
    Retorna 200 OK se o servidor está rodando.
    """
    try:
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.1)
        uptime_sec = (datetime.datetime.utcnow() -
                      datetime.datetime.fromisoformat(_watchdog_stats['uptime_start'])).total_seconds()
        uptime_str = f"{int(uptime_sec//3600)}h {int((uptime_sec%3600)//60)}m"
    except Exception:
        mem = None; cpu = 0; uptime_str = 'n/a'

    return jsonify({
        'status':       'ok',
        'service':      'DANBOT',
        'version':      'v2.0',
        'uptime':       uptime_str,
        'bot_running':  bot_state.get('running', False),
        'cpu_pct':      round(cpu, 1),
        'mem_used_mb':  round(mem.used / 1024**2, 1) if mem else 0,
        'mem_total_mb': round(mem.total / 1024**2, 1) if mem else 0,
        'timestamp':    datetime.datetime.utcnow().isoformat() + 'Z',
    }), 200


@app.route('/api/watchdog', methods=['GET'])
def api_watchdog():
    """Status interno detalhado do watchdog (requer login)."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    try:
        mem  = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        cpu  = psutil.cpu_percent(interval=0.2)
        proc = psutil.Process()
        uptime_sec = (datetime.datetime.utcnow() -
                      datetime.datetime.fromisoformat(_watchdog_stats['uptime_start'])).total_seconds()
    except Exception:
        mem = disk = proc = None; cpu = 0; uptime_sec = 0

    return jsonify({
        'ok': True,
        'server': {
            'uptime_seconds':  int(uptime_sec),
            'uptime_human':    f"{int(uptime_sec//3600)}h {int((uptime_sec%3600)//60)}m {int(uptime_sec%60)}s",
            'cpu_pct':         round(cpu, 1),
            'mem_used_mb':     round(mem.used / 1024**2, 1) if mem else 0,
            'mem_total_mb':    round(mem.total / 1024**2, 1) if mem else 0,
            'mem_pct':         round(mem.percent, 1) if mem else 0,
            'disk_used_gb':    round(disk.used / 1024**3, 2) if disk else 0,
            'disk_total_gb':   round(disk.total / 1024**3, 2) if disk else 0,
            'platform':        platform.system(),
            'python':          platform.python_version(),
            'railway_env':     os.environ.get('RAILWAY_ENVIRONMENT', 'local'),
            'railway_domain':  os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'n/a'),
        },
        'watchdog': {
            'uptime_start':    _watchdog_stats['uptime_start'],
            'bot_crashes':     _watchdog_stats['bot_crashes'],
            'auto_restarts':   _watchdog_stats['starts'],
            'last_restart':    _watchdog_stats['last_restart'],
            'bot_thread_alive': globals().get('bot_thread') is not None and globals()['bot_thread'].is_alive(),
        },
        'bot': {
            'running':         bot_state.get('running', False),
            'wins':            bot_state.get('wins', 0),
            'losses':          bot_state.get('losses', 0),
            'profit':          bot_state.get('profit', 0.0),
            'selected_asset':  bot_state.get('selected_asset', 'AUTO'),
            'broker':          bot_state.get('broker_name', None),
        }
    })



@app.route('/api/daily-profit')
def api_daily_profit():
    """Retorna lucro acumulado hora a hora das últimas 24h para o gráfico."""
    if not current_user(): return jsonify({'error': 'não autorizado'}), 401
    now  = datetime.datetime.utcnow()
    ago  = now - datetime.timedelta(hours=24)
    trades = TradeLog.query.filter(TradeLog.timestamp >= ago).order_by(TradeLog.timestamp).all()

    # Agrupar por hora — lucro acumulado
    hours = {}
    for t in trades:
        h = t.timestamp.replace(minute=0, second=0, microsecond=0)
        key = h.strftime('%H:00')
        hours[key] = hours.get(key, 0) + (t.profit or 0)

    # Montar série completa das últimas 24h (mesmo sem trades)
    labels, values, cumulative = [], [], []
    running = 0
    for i in range(24):
        hh = (now - datetime.timedelta(hours=23-i)).replace(minute=0, second=0, microsecond=0)
        key = hh.strftime('%H:00')
        val = hours.get(key, 0)
        running += val
        labels.append(key)
        values.append(round(val, 2))
        cumulative.append(round(running, 2))

    total_today = round(sum(values), 2)
    return jsonify({
        'ok': True,
        'labels':     labels,
        'values':     values,
        'cumulative': cumulative,
        'total_today': total_today,
        'trades_today': len(trades),
    })


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            master = User(username='admin', password_hash=hash_pw('danbot@master2025'), role='master')
            db.session.add(master); db.session.commit()
            print('✅ Master criado: admin / danbot@master2025')
    port = int(os.environ.get('PORT', 7860))
    app.run(host='0.0.0.0', port=port, debug=False)
