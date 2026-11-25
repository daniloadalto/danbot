"""(25/11) UMA COPIA DA BIBLIOTECA DA CORRETORA POR USUARIO.

A iqoptionapi (e as corretoras irmas: Avalon, Bullex, Exnova...) guarda a sessao de login (SSID), o id da banca e os
avisos de conexao em variaveis GLOBAIS do modulo `iqoptionapi.global_value`. Com dois usuarios no mesmo servidor, um
pegava a sessao / a banca / o "socket caiu" do outro.

Aqui cada usuario recebe a SUA copia dos modulos da biblioteca (importada de novo, com outro global_value, outro
constants etc.). O codigo da biblioteca e o mesmo, so que cada conta vive na sua caixa:
  - usuario A conectado na IQ e usuario B na Avalon (ou outra conta da IQ) nao se misturam;
  - o MESMO usuario reconectando usa sempre a mesma copia (igual era antes).
Nenhum envio de ordem, leitura de velas ou login foi reescrito: so passam a usar a copia certa.
"""
import sys
import importlib
import threading

P = 'iqoptionapi'
_LOCK = threading.Lock()
_ORIG = {}
_COPIES = {}


def _snapshot():
    return {k: v for k, v in list(sys.modules.items()) if k == P or k.startswith(P + '.')}


def _load_orig():
    if not _ORIG:
        importlib.import_module(P + '.stable_api')
        _ORIG.update(_snapshot())


def _harden(mods):
    """Mesma blindagem do patch_iqoptionapi.py, mas usando o global_value DESTA copia: mensagem com formato novo da
    corretora nao derruba a thread do websocket, e o mutex da lib sempre e liberado."""
    wc = mods[P + '.ws.client'].WebsocketClient
    gv = mods[P + '.global_value']
    if getattr(wc, '_danbot_hardened', False):
        return
    orig = wc.on_message

    def on_message(self, message):
        try:
            return orig(self, message)
        except Exception:
            pass
        finally:
            try:
                gv.ssl_Mutual_exclusion = False
            except Exception:
                pass

    wc.on_message = on_message
    wc._danbot_hardened = True


def _new_copy():
    """Importa a biblioteca de novo (modulos novos, variaveis globais novas) sem mexer na versao ja carregada."""
    with _LOCK:
        _load_orig()
        saved = _snapshot()
        for k in saved:
            sys.modules.pop(k, None)
        try:
            importlib.import_module(P + '.stable_api')
            for m in ('constants', 'global_value', 'api', 'ws.client', 'http.login', 'http.logout', 'http.events'):
                importlib.import_module(P + '.' + m)
            mods = _snapshot()
        finally:
            for k in list(_snapshot()):
                sys.modules.pop(k, None)
            sys.modules.update(saved)
    _harden(mods)
    return mods


def for_user(username):
    """Copia da biblioteca deste usuario (criada na 1a conexao dele e reaproveitada nas reconexoes)."""
    m = _COPIES.get(username)
    if m is None:
        m = _new_copy()
        _COPIES[username] = m
    return m


def mod(api, name):
    """Modulo `name` ('constants', 'global_value', 'stable_api'...) da copia usada por esta conexao."""
    m = getattr(api, '_danbot_lib', None) if api is not None else None
    if m:
        return m[P + '.' + name]
    _load_orig()
    return _ORIG.get(P + '.' + name) or importlib.import_module(P + '.' + name)


def stable_api(lib):
    return lib[P + '.stable_api']


def gv(lib):
    return lib[P + '.global_value']


# cria ja a biblioteca original ao importar (antes de existir qualquer thread do bot)
try:
    _load_orig()
except Exception as _e:
    print(f'[IQLIB] biblioteca da corretora indisponivel: {_e}')
