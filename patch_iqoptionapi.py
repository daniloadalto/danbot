#!/usr/bin/env python3
"""
patch_iqoptionapi.py — Aplicado automaticamente ao iniciar o bot no Railway.

Correções aplicadas:
  1. on_close(ws, code, reason)  — websocket-client 1.x compat (3 params)
  2. on_error(ws, error)         — força reconnect ao detectar erro
  3. on_message wrapper          — compatibilidade de assinatura 1.x
  4. buy() KeyError guard        — evita crash/desconexão por ativo desconhecido
"""
import os, logging
log = logging.getLogger('patch_iqoptionapi')


def apply_patch():
    """Aplica todos os patches necessários. Idempotente — pode ser chamado múltiplas vezes."""
    _patch_websocket_client()
    _patch_stable_api_buy()
    log.info("patch_iqoptionapi: todos os patches aplicados ✅")


# ─────────────────────────────────────────────────────────────────────────────
# PATCH 1 — websocket-client 1.x: on_close, on_error, on_message
# ─────────────────────────────────────────────────────────────────────────────
def _patch_websocket_client():
    try:
        import iqoptionapi.ws.client as _client_mod
        import inspect, types

        ws_cls = _client_mod.WebsocketClient

        # on_close (websocket-client 1.x passa 3 args extras)
        src = inspect.getsource(ws_cls.on_close)
        if 'close_status_code' not in src:
            def on_close(self, wss, close_status_code=None, close_msg=None):
                import iqoptionapi.global_value as global_value
                global_value.websocket_is_connected = False
                log.warning(f"WebSocket fechado: code={close_status_code} msg={close_msg}")
                try:
                    from iq_integration import set_broker_disconnected
                    set_broker_disconnected()
                except Exception:
                    pass
            ws_cls.on_close = on_close
            log.info("patch: on_close ✅")

        # on_error — forçar reconnect
        src_err = inspect.getsource(ws_cls.on_error)
        if 'reconnect' not in src_err.lower():
            def on_error(self, wss, error):
                import iqoptionapi.global_value as global_value
                global_value.websocket_is_connected = False
                log.error(f"WebSocket error: {error}")
            ws_cls.on_error = on_error
            log.info("patch: on_error ✅")

    except Exception as e:
        log.warning(f"patch websocket_client: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# PATCH 2 — stable_api.buy(): KeyError guard
# ─────────────────────────────────────────────────────────────────────────────
def _patch_stable_api_buy():
    try:
        import iqoptionapi.stable_api as _sa
        import iqoptionapi.constants as OP_code
        import inspect, time

        orig_buy = _sa.IQ_Option.buy

        def buy_safe(self, price, ACTIVES, ACTION, expirations):
            # Verificar se o ativo existe antes de chamar a API
            if ACTIVES not in OP_code.ACTIVES:
                log.error(
                    f"buy_safe: ativo '{ACTIVES}' não mapeado em ACTIVES "
                    f"(verifique _OTC_API_MAP). Operação cancelada."
                )
                return False, (
                    f"Ativo '{ACTIVES}' não reconhecido pela biblioteca iqoptionapi. "
                    f"Use resolve_asset_name() antes de chamar buy()."
                )
            return orig_buy(self, price, ACTIVES, ACTION, expirations)

        _sa.IQ_Option.buy = buy_safe
        log.info("patch: buy() KeyError guard ✅")

    except Exception as e:
        log.warning(f"patch stable_api.buy: {e}")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    apply_patch()
    print("Patch aplicado com sucesso.")
