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


def _harden_websocket_message(cls):
    """Blinda o on_message em QUALQUER versao da lib (0.56 e 1.x). O original (a) nao tem try/except:
    qualquer mensagem com formato novo da corretora (KeyError etc.) MATA a thread do websocket - e o
    socket fecha ~30s depois de cada reconexao, em loop; e (b) trava ssl_Mutual_exclusion=True no
    inicio sem finally - com a thread morta, o mutex fica True para sempre e TODOS os envios da lib
    ficam presos no 'while ssl_Mutual_exclusion' (o bot congela sem erro aparente). Aqui: cada
    mensagem problematica e ignorada e o mutex e sempre liberado."""
    if getattr(cls, '_danbot_hardened', False):
        return
    orig = cls.on_message
    import iqoptionapi.global_value as _gv

    def on_message(self, message):
        try:
            return orig(self, message)
        except Exception as _e:
            try:
                log.warning("patch: mensagem da corretora ignorada (%s) - thread do websocket protegida", str(_e)[:90])
            except Exception:
                pass
        finally:
            try:
                _gv.ssl_Mutual_exclusion = False       # nunca deixa o mutex preso
            except Exception:
                pass

    cls.on_message = on_message
    cls._danbot_hardened = True
    log.info("patch: on_message blindado - erro numa mensagem nao fecha mais o socket")


def harden_instance(api):
    """Blinda tambem instancias JA criadas (o WebSocketApp captura o callback no __init__, entao o
    patch de classe so vale para conexoes novas). Idempotente - pode chamar a cada connect()."""
    try:
        inner = getattr(api, 'api', None) or api
        wc = getattr(inner, 'wss', None)              # WebsocketClient da lib
        app_ws = getattr(wc, 'wss', None)             # WebSocketApp real
        if wc is None or app_ws is None:
            return
        _harden_websocket_message(type(wc))
        if not getattr(app_ws, '_danbot_hardened', False):
            app_ws.on_message = wc.on_message          # reassocia o callback (agora blindado)
            app_ws._danbot_hardened = True
    except Exception as e:
        log.warning(f"patch: harden_instance falhou: {e}")


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

        # (10/11) blindagem do on_message em TODAS as versoes - ve _harden_websocket_message
        import iqoptionapi.ws.client as _wsc
        _harden_websocket_message(_wsc.WebsocketClient)

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
