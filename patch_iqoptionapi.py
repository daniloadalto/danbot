#!/usr/bin/env python3
"""
patch_iqoptionapi.py — Compatibilidade websocket-client 1.x com iqoptionapi.

Correções:
  1. on_message(self, ws, message)  — websocket-client 1.x passa ws como 2º arg
  2. on_close(self, ws, code, reason) — 1.x passa 3 args extras
  3. on_error(self, ws, error)      — 1.x passa ws como 2º arg
  4. buy() KeyError guard           — evita crash por ativo desconhecido
"""
import logging
log = logging.getLogger("patch_iqoptionapi")


def apply_iqoptionapi_patch():
    """Alias principal chamado pelo iq_integration.py"""
    apply_patch()


def apply_patch():
    """Aplica todos os patches. Idempotente."""
    _patch_websocket_client()
    _patch_stable_api_buy()
    log.info("patch_iqoptionapi: todos os patches aplicados ✅")


def _patch_websocket_client():
    try:
        import iqoptionapi.ws.client as _mod
        import inspect

        cls = _mod.WebsocketClient

        # ── on_message ────────────────────────────────────────────────────────
        # websocket-client 1.x chama: on_message(ws_app, message)
        # iqoptionapi original:        on_message(self, message)   ← falta ws_app!
        src = inspect.getsource(cls.on_message)
        if "wss" not in src and "ws_app" not in src:
            def on_message(self, wss, message):
                import json as _json
                import iqoptionapi.global_value as gv
                gv.ssl_Mutual_exclusion = True
                try:
                    msg = _json.loads(str(message))
                except Exception:
                    return
                api = self.api
                name = msg.get("name", "")
                if name == "timeSync":
                    api.timesync.server_timestamp = msg["msg"]
                elif name == "profile":
                    try:
                        api.profile.msg = msg["msg"]
                        gv.balance_id = msg["msg"].get("balance_id")
                        gv.balance_type = msg["msg"].get("balance_type")
                        gv.balance = msg["msg"].get("balance")
                    except Exception:
                        pass
                elif name == "socket-option-changed":
                    try:
                        api.api_option_init_all_result_v2 = msg["msg"]
                    except Exception:
                        pass
                try:
                    api.set_digital_spot_call_result_v2(msg)
                except Exception:
                    pass
                try:
                    api.set_api_candles(msg)
                except Exception:
                    pass
                try:
                    api.set_api_positions(msg)
                except Exception:
                    pass
                try:
                    api.heartbeat(msg)
                except Exception:
                    pass
                gv.ssl_Mutual_exclusion = False
            cls.on_message = on_message
            log.info("patch: on_message ✅ (websocket-client 1.x compat)")

        # ── on_close ─────────────────────────────────────────────────────────
        src_c = inspect.getsource(cls.on_close)
        if "close_status_code" not in src_c:
            def on_close(self, wss, close_status_code=None, close_msg=None):
                import iqoptionapi.global_value as gv
                gv.websocket_is_connected = False
                log.warning(f"WS fechado: code={close_status_code}")
                try:
                    from iq_integration import set_broker_disconnected
                    set_broker_disconnected()
                except Exception:
                    pass
            cls.on_close = on_close
            log.info("patch: on_close ✅")

        # ── on_error ─────────────────────────────────────────────────────────
        src_e = inspect.getsource(cls.on_error)
        if "wss" not in src_e:
            def on_error(self, wss, error):
                import iqoptionapi.global_value as gv
                gv.websocket_is_connected = False
                log.error(f"WS erro: {error}")
            cls.on_error = on_error
            log.info("patch: on_error ✅")

    except Exception as e:
        log.warning(f"patch websocket_client falhou: {e}")


def _patch_stable_api_buy():
    try:
        import iqoptionapi.stable_api as _sa
        import iqoptionapi.constants as OP_code

        orig_buy = _sa.IQ_Option.buy

        def buy_safe(self, price, ACTIVES, ACTION, expirations):
            if ACTIVES not in OP_code.ACTIVES:
                log.error(f"buy_safe: ativo {ACTIVES} não mapeado. Cancelado.")
                return False, f"Ativo {ACTIVES} não reconhecido."
            return orig_buy(self, price, ACTIVES, ACTION, expirations)

        _sa.IQ_Option.buy = buy_safe
        log.info("patch: buy() guard ✅")
    except Exception as e:
        log.warning(f"patch buy: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    apply_patch()
    print("Patch aplicado.")
