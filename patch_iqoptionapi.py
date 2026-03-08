#!/usr/bin/env python3
"""
patch_iqoptionapi.py - Compatibilidade websocket-client com iqoptionapi.

Com websocket-client==0.56 (versao nativa): nenhum patch necessario.
O iqoptionapi foi escrito para websocket-client 0.56 que usa _callback()
inteligente (detecta bound methods vs funcoes regulares).

Este modulo existe apenas para compatibilidade com o import em iq_integration.py.
"""
import logging
log = logging.getLogger("patch_iqoptionapi")


def apply_iqoptionapi_patch():
    """Alias principal chamado pelo iq_integration.py"""
    apply_patch()


def apply_patch():
    """Verifica versao do websocket-client e aplica patches se necessario."""
    try:
        import websocket
        ws_version = getattr(websocket, 'version', None) or getattr(websocket, '__version__', '0.x')
        log.info(f"websocket-client versao: {ws_version}")
        
        # Se for versao 1.x, aplicar patch de compatibilidade
        try:
            major = int(str(ws_version).split('.')[0])
        except Exception:
            major = 0
        
        if major >= 1:
            log.warning(f"websocket-client {ws_version} detectado - aplicando patches 1.x")
            _patch_websocket_client_1x()
        else:
            log.info(f"websocket-client {ws_version} (0.x) - compativel nativo, sem patches")
            
    except Exception as e:
        log.warning(f"patch_iqoptionapi: {e}")


def _patch_websocket_client_1x():
    """Patch para websocket-client 1.x (incompativel com iqoptionapi 0.56 API)."""
    try:
        import iqoptionapi.ws.client as _mod
        import inspect

        cls = _mod.WebsocketClient

        # on_message: websocket-client 1.x passa (ws_app, message) para bound method
        # iqoptionapi espera (self, message) - precisa adicionar wss arg
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
                try:
                    api.set_digital_spot_call_result_v2(msg)
                except Exception:
                    pass
                try:
                    api.set_api_candles(msg)
                except Exception:
                    pass
                gv.ssl_Mutual_exclusion = False
            cls.on_message = on_message
            log.info("patch: on_message 1.x compat aplicado")

        # on_close: 1.x passa (ws, code, msg), iqoptionapi era @staticmethod on_close(wss)
        try:
            src_c = inspect.getsource(cls.on_close)
            if "close_status_code" not in src_c:
                @staticmethod
                def on_close(wss, close_status_code=None, close_msg=None):
                    import iqoptionapi.global_value as gv
                    gv.websocket_is_connected = False
                    gv.check_websocket_if_connect = 0
                cls.on_close = on_close
                log.info("patch: on_close 1.x compat aplicado")
        except Exception as ec:
            log.warning(f"patch on_close: {ec}")

    except Exception as e:
        log.warning(f"patch 1.x falhou: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    apply_patch()
    print("Patch verificado.")
