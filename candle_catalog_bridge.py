import json
import re
from pathlib import Path
import importlib.util

BASE_DIR = Path(__file__).resolve().parent


def _load_module(file_name: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, BASE_DIR / file_name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CANDLES = _load_module('danbot_catalogador_candles.py', 'danbot_catalogador_candles')
_CORES = _load_module('danbot_catalogador_cores.py', 'danbot_catalogador_cores')


def _slugify(text: str) -> str:
    text = str(text or '').strip().lower()
    text = re.sub(r'[^a-z0-9]+', '_', text)
    return text.strip('_') or 'padrao'


DEFAULT_ACCURACY = {
    'martelo': 82,
    'enforcado': 81,
    'estrela cadente': 82,
    'marubozu alta': 80,
    'marubozu baixa': 80,
    'harami': 80,
    'harami cross': 80,
    'engolfo alta': 83,
    'engolfo baixa': 83,
    'piercing line': 82,
    'dark cloud cover': 82,
    'estrela da manhã': 85,
    'estrela da tarde': 85,
    '3 soldados brancos': 81,
    '3 corvos pretos': 81,
    '3 métodos ascendentes': 82,
    'ombro cabeça ombro': 83,
    'fundo duplo': 80,
    'topo duplo': 80,
    'fundo triplo': 81,
    'cunha descendente': 80,
    'cunha ascendente': 80,
    'alargamento altista': 80,
    'alargamento baixista': 80,
    'bandeira altista': 80,
    'bandeira baixista': 80,
    'triangulo ascendente': 80,
    'triangulo descendente': 80,
    'retangulo altista': 80,
    'retangulo baixista': 80,
    'triangulo simetrico de alta': 80,
    'triangulo simetrico de baixa': 80,
    'cup and handle': 80,
}

CATALOG = []
CATALOG_BY_SLUG = {}
NAME_TO_SLUG = {}


def _register(item: dict):
    row = dict(item)
    row['slug'] = str(row['slug'])
    row['label'] = str(row['label'])
    row['direction'] = str(row['direction']).upper()
    row['accuracy'] = int(row.get('accuracy', 80) or 80)
    if row['slug'] in CATALOG_BY_SLUG:
        return
    CATALOG.append(row)
    CATALOG_BY_SLUG[row['slug']] = row
    NAME_TO_SLUG[row['label'].strip().lower()] = row['slug']


for p in list(_CANDLES.PADROES_CLASSICOS) + list(_CANDLES.PADROES_AVANCADOS):
    label = str(p.get('nome', '')).strip()
    _register({
        'slug': f"cndl_{_slugify(label)}",
        'label': label,
        'direction': p.get('dir', 'call'),
        'family': 'candles',
        'kind': p.get('tipo', 'candle'),
        'sequence': None,
        'source_name': label,
        'accuracy': DEFAULT_ACCURACY.get(label, 80),
    })

for p in list(_CORES.PADROES_JSON):
    label = str(p.get('nome', '')).strip()
    _register({
        'slug': f"seq_{p.get('id', _slugify(label))}",
        'label': label,
        'direction': p.get('dir', 'call'),
        'family': 'sequence',
        'kind': p.get('tipo', 'seq'),
        'sequence': p.get('sequencia'),
        'source_name': label,
        'accuracy': 80,
    })

custom_json = BASE_DIR / 'padroes_personalizados_catalogo.json'
if custom_json.exists():
    try:
        payload = json.loads(custom_json.read_text(encoding='utf-8', errors='ignore'))
        for p in payload.get('padroes', []):
            label = str(p.get('nome', '')).strip()
            custom_slug = f"custom_{p.get('id', _slugify(label))}"
            _register({
                'slug': custom_slug,
                'label': label,
                'direction': p.get('direcao', 'call'),
                'family': 'sequence',
                'kind': 'custom_sequence',
                'sequence': p.get('sequencia'),
                'source_name': label,
                'accuracy': 80,
            })
    except Exception:
        pass

CATALOG.sort(key=lambda item: (item['family'], item['label']))


def get_candle_pattern_catalog() -> list:
    return [dict(item) for item in CATALOG]



def normalize_selected_candle_patterns(raw) -> list[str]:
    if raw in (None, '', 'ALL'):
        return []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(',') if p.strip()]
    elif isinstance(raw, dict):
        raw = [k for k, v in raw.items() if v]
    elif not isinstance(raw, (list, tuple, set)):
        raw = []
    out = []
    seen = set()
    for item in raw:
        value = str(item or '').strip()
        if not value:
            continue
        slug = value if value in CATALOG_BY_SLUG else NAME_TO_SLUG.get(value.lower())
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out



def pattern_label(slug: str) -> str:
    meta = CATALOG_BY_SLUG.get(slug)
    return meta['label'] if meta else str(slug or '')



def _build_velas(opens, highs, lows, closes):
    size = min(len(opens), len(highs), len(lows), len(closes))
    velas = []
    for i in range(size):
        velas.append({
            'open': float(opens[i]),
            'close': float(closes[i]),
            'max': float(highs[i]),
            'min': float(lows[i]),
        })
    return velas



def _candle_colors(velas):
    cores = []
    for v in velas:
        o = float(v['open']); c = float(v['close'])
        cores.append('G' if c > o else ('R' if c < o else 'D'))
    return ''.join(cores)



def _sequence_match(velas, sequence: str) -> bool:
    sequence = str(sequence or '').strip().upper()
    if not sequence or len(velas) < len(sequence):
        return False
    colors = _candle_colors(velas[-len(sequence):])
    return colors == sequence



def _structure_match(name: str, velas: list) -> bool:
    if len(velas) < 4:
        return False
    for window_len in (24, 30, 36):
        if len(velas) < window_len:
            continue
        window = velas[-window_len:]
        if name == 'ombro cabeça ombro' and _CANDLES.head_shoulders(window):
            return True
        if name == 'fundo duplo' and _CANDLES.double_bottom(window):
            return True
        if name == 'topo duplo' and _CANDLES.double_top(window):
            return True
        if name == 'fundo triplo' and _CANDLES.triple_bottom(window):
            return True
        if name == 'cunha descendente' and _CANDLES.falling_wedge(window):
            return True
        if name == 'cunha ascendente' and _CANDLES.rising_wedge(window):
            return True
        if name == 'alargamento altista' and _CANDLES.broadening_bullish(window):
            return True
        if name == 'alargamento baixista' and _CANDLES.broadening_bearish(window):
            return True
        if name == 'bandeira altista' and _CANDLES.bullish_flag(window):
            return True
        if name == 'bandeira baixista' and _CANDLES.bearish_flag(window):
            return True
        if name == 'triangulo ascendente' and _CANDLES.ascending_triangle(window):
            return True
        if name == 'triangulo descendente' and _CANDLES.descending_triangle(window):
            return True
        if name == 'retangulo altista' and _CANDLES.bullish_rectangle(window):
            return True
        if name == 'retangulo baixista' and _CANDLES.bearish_rectangle(window):
            return True
        if name == 'triangulo simetrico de alta' and _CANDLES.symmetrical_triangle_bull(window):
            return True
        if name == 'triangulo simetrico de baixa' and _CANDLES.symmetrical_triangle_bear(window):
            return True
        if name == 'cup and handle' and _CANDLES.cup_and_handle(window):
            return True
    return False



def _detect_single_candle(name: str, metrics_curr: dict) -> bool:
    if name == 'martelo':
        return _CANDLES.is_hammer(metrics_curr)
    if name == 'enforcado':
        return _CANDLES.is_hanging_man(metrics_curr)
    if name == 'estrela cadente':
        return _CANDLES.is_shooting_star(metrics_curr)
    if name == 'marubozu alta':
        return _CANDLES.is_marubozu_bull(metrics_curr)
    if name == 'marubozu baixa':
        return _CANDLES.is_marubozu_bear(metrics_curr)
    return False



def _detect_multi_candle(name: str, metrics: list) -> bool:
    if len(metrics) < 2:
        return False
    curr = metrics[-1]
    prev = metrics[-2]
    if name == 'harami':
        return _CANDLES.bullish_harami(prev, curr)
    if name == 'harami cross':
        return _CANDLES.bullish_harami_cross(prev, curr)
    if name == 'engolfo alta':
        return _CANDLES.bullish_engulfing(prev, curr)
    if name == 'engolfo baixa':
        return _CANDLES.bearish_engulfing(prev, curr)
    if name == 'piercing line':
        return _CANDLES.piercing_line(prev, curr)
    if name == 'dark cloud cover':
        return _CANDLES.dark_cloud_cover(prev, curr)
    if len(metrics) >= 3:
        a, b, c = metrics[-3], metrics[-2], metrics[-1]
        if name == 'estrela da manhã':
            return _CANDLES.morning_star(a, b, c)
        if name == 'estrela da tarde':
            return _CANDLES.evening_star(a, b, c)
        if name == '3 soldados brancos':
            return _CANDLES.three_white_soldiers(a, b, c)
        if name == '3 corvos pretos':
            return _CANDLES.three_black_crows(a, b, c)
    if len(metrics) >= 4:
        a, b, c, d = metrics[-4], metrics[-3], metrics[-2], metrics[-1]
        if name == '3 métodos ascendentes':
            return _CANDLES.rising_three_methods(a, b, c, d)
    return False



def detect_selected_candle_patterns(opens, highs, lows, closes, selected_patterns=None) -> list:
    selected = normalize_selected_candle_patterns(selected_patterns)
    if not selected:
        return []
    velas = _build_velas(opens, highs, lows, closes)
    if not velas:
        return []
    metrics = [_CANDLES.candle_metrics(v) for v in velas]
    curr = metrics[-1]
    found = []
    for slug in selected:
        meta = CATALOG_BY_SLUG.get(slug)
        if not meta:
            continue
        label = meta['label']
        ok = False
        if meta['family'] == 'sequence':
            ok = _sequence_match(velas, meta.get('sequence'))
        elif meta['kind'] == 'estrutura':
            ok = _structure_match(label, velas)
        elif meta['kind'] == 'candle':
            ok = _detect_single_candle(label, curr)
        else:
            ok = _detect_multi_candle(label, metrics)
        if ok:
            item = dict(meta)
            item['direction'] = 'CALL' if meta['direction'].upper() == 'CALL' else 'PUT'
            item['premium'] = bool(item['accuracy'] >= 82 or item['family'] == 'candles' and item['kind'] == 'estrutura')
            item['is_reversal'] = item['direction'] in ('CALL', 'PUT') and item['kind'] in ('candle', 'multi', 'estrutura')
            item['is_continuation'] = item['label'] in {'3 soldados brancos', '3 corvos pretos', '3 métodos ascendentes', 'bandeira altista', 'bandeira baixista'}
            item['desc'] = f"{item['label']} ({item['accuracy']}%)"
            found.append(item)
    found.sort(key=lambda item: (item.get('accuracy', 0), item.get('family') != 'sequence', item.get('label', '')), reverse=True)
    return found
