"""(25/11) Corretoras da mesma plataforma da IQ Option (white label Quadcode).

Avalon, Bullex, Exnova, Polarium, Casa Trade, Broker10, Optinary e Shiver usam a MESMA API da IQ Option; muda so o
endereco (websocket, login, logout e eventos). Conferido nas bibliotecas de cada uma (arquivos que o usuario mandou):
o resto do codigo e igual ao da iqoptionapi.

A IQ Option continua EXATAMENTE como antes: make_api('IQ Option', ...) devolve o IQ_Option original da biblioteca,
sem nenhuma troca. So as outras corretoras passam pela classe abaixo, que troca os enderecos SO na instancia dela
(nada global), entao usuarios em corretoras diferentes no mesmo servidor nao se misturam.
"""
import threading

# nome no painel -> dominio da corretora (enderecos: ws.trade.<d>, auth.trade.<d>, event.trade.<d>)
BROKERS = {
    'IQ Option': None,                      # original da biblioteca (iqoption.com) - nao mexe
    'Avalon': 'avalonbroker.com',
    'Bullex': 'bull-ex.com',
    'Exnova': 'exnova.com',
    'Polarium': 'polariumbroker.com',
    'Casa Trade': 'casatrade.com',
    'Broker10': 'broker10.com',
    'Optinary': 'optinary.com',
    'Shiver': 'shiverbroker.com',
}

ALIASES = {k.lower().replace(' ', '').replace('_', ''): k for k in BROKERS}


def normalize(name):
    """'IQ_Option', 'iq option', 'avalon' ... -> nome oficial do painel (padrao: IQ Option)."""
    k = str(name or '').lower().replace(' ', '').replace('_', '').replace('-', '')
    return ALIASES.get(k, 'IQ Option')


def hosts(name):
    d = BROKERS.get(normalize(name))
    if not d:
        return {'ws': 'iqoption.com', 'auth': 'auth.iqoption.com', 'event': 'event.iqoption.com', 'site': 'iqoption.com'}
    return {'ws': 'ws.trade.' + d, 'auth': 'auth.trade.' + d, 'event': 'event.trade.' + d, 'site': 'trade.' + d}


_TL = threading.local()          # corretora do connect() em andamento NESTA thread
_DISPATCH = set()
_DISPATCH_LOCK = threading.Lock()


def _M(lib, name):
    """modulo da biblioteca: da copia do usuario (iqlib) ou o original"""
    if lib:
        return lib['iqoptionapi.' + name]
    import importlib
    return importlib.import_module('iqoptionapi.' + name)


def _install_dispatch(lib=None):
    """Na 1a conexao de outra corretora: o nome IQOptionAPI do stable_api (desta copia da biblioteca) passa a ser um
    repassador que, nesta thread, cria a API da corretora; em qualquer outro caso cria o IQOptionAPI original com os
    mesmos argumentos (a IQ Option continua identica)."""
    _sa = _M(lib, 'stable_api')
    with _DISPATCH_LOCK:
        if id(_sa) in _DISPATCH:
            return
        orig = _sa.IQOptionAPI

        def _factory(host, username, password, *a, **k):
            cls = getattr(_TL, 'cls', None)
            if cls is not None:
                return cls(host, username, password, *a, **k)
            return orig(host, username, password, *a, **k)

        _sa.IQOptionAPI = _factory
        _DISPATCH.add(id(_sa))


_CLASSES = {}


def _broker_classes(name, lib=None):
    """Cria (uma vez por copia da biblioteca) as classes da corretora: IQOptionAPI/IQ_Option com os enderecos dela."""
    ck = (name, id(_M(lib, 'stable_api')))
    if ck in _CLASSES:
        return _CLASSES[ck]
    IQOptionAPI = _M(lib, 'api').IQOptionAPI
    IQ_Option = _M(lib, 'stable_api').IQ_Option
    Login = _M(lib, 'http.login').Login
    Logout = _M(lib, 'http.logout').Logout
    Events = _M(lib, 'http.events').Events
    h = hosts(name)

    class _Login(Login):
        def _post(self, data=None, headers=None):
            return self.api.send_http_request_v2(method="POST", url="https://%s/api/v2/login" % h['auth'], data=data, headers=headers)

    class _Logout(Logout):
        def _post(self, data=None, headers=None):
            return self.api.send_http_request_v2(method="POST", url="https://%s/api/v1.0/logout" % h['auth'], data=data, headers=headers)

    class _Events(Events):
        def send_http(self, method, data=None, headers=None):
            return self.api.send_http_request_v2(method=method, url="https://%s/api/v1/events" % h['event'], data=data)

    class BrokerAPI(IQOptionAPI):
        broker_name = name

        def __init__(self, host, username, password, proxies=None):
            # ignora o 'iqoption.com' fixo do IQ_Option.connect() e usa o websocket desta corretora
            super().__init__(h['ws'], username, password, proxies=proxies) if proxies is not None else super().__init__(h['ws'], username, password)

        @property
        def login(self):
            return _Login(self)

        @property
        def logout(self):
            return _Logout(self)

        @property
        def events(self):
            return _Events(self)

    class BrokerOption(IQ_Option):
        broker_name = name

        def connect(self):
            # o IQ_Option.connect() da biblioteca cria 'IQOptionAPI("iqoption.com", ...)': nesta thread, durante
            # este connect, o repassador cria a BrokerAPI (enderecos desta corretora). Outras threads nao sao afetadas.
            _install_dispatch(lib)
            _TL.cls = BrokerAPI
            try:
                return super().connect()
            finally:
                _TL.cls = None

    _CLASSES[ck] = BrokerOption
    return BrokerOption


def make_api(name, email, password, lib=None):
    """Objeto de conexao da corretora. IQ Option = IQ_Option da biblioteca, sem nenhuma mudanca.
    `lib` = copia da biblioteca do usuario (iqlib.for_user); sem ela usa a original."""
    name = normalize(name)
    if not BROKERS.get(name):
        return _M(lib, 'stable_api').IQ_Option(email, password)
    return _broker_classes(name, lib)(email, password)
