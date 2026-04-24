import copy
import json
import threading
import unicodedata
import re
from pathlib import Path
from typing import Iterable

import iq_integration as IQ
import danbot_catalogador_candles as candles_mod
import danbot_catalogador_cores as cores_mod

_RUNTIME_LOCK = threading.Lock()


def _slugify(value: str) -> str:
    value = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^a-zA-Z0-9]+', '_', value).strip('_').lower()
    return value or 'padrao'


def _clone_patterns(patterns: Iterable[dict]) -> list[dict]:
    return [copy.deepcopy(p) for p in (patterns or [])]


CANDLE_PATTERNS = _clone_patterns(getattr(candles_mod, 'PADROES_TODOS', []))
SEQUENCE_PATTERNS = _clone_patterns(getattr(cores_mod, 'PADROES_TODOS', getattr(cores_mod, 'PADROES_JSON', [])))

_CUSTOM_PATTERNS_FILE = Path(__file__).resolve().parent / 'padroes_personalizados_catalogo.json'
if _CUSTOM_PATTERNS_FILE.exists():
    try:
        _payload = json.loads(_CUSTOM_PATTERNS_FILE.read_text(encoding='utf-8', errors='ignore'))
        for _item in (_payload.get('padroes', []) if isinstance(_payload, dict) else []):
            if isinstance(_item, dict):
                seq = dict(_item)
                seq.setdefault('id', _slugify(seq.get('nome', '')))
                seq.setdefault('dir', seq.get('direcao', seq.get('dir', 'call')))
                seq.setdefault('tipo', 'custom_sequence')
                SEQUENCE_PATTERNS.append(seq)
    except Exception:
        pass

for item in CANDLE_PATTERNS:
    item['slug'] = f"cndl_{_slugify(item.get('nome', ''))}"
    item['label'] = item.get('nome', '')
    item['engine'] = 'candles'

for item in SEQUENCE_PATTERNS:
    item['slug'] = f"seq_{item.get('id', _slugify(item.get('nome', '')))}"
    item['label'] = item.get('nome', '')
    item['engine'] = 'cores'

CANDLE_INDEX = {item['slug']: item for item in CANDLE_PATTERNS}
SEQUENCE_INDEX = {item['slug']: item for item in SEQUENCE_PATTERNS}


def get_catalog_payload() -> dict:
    return {
        'candles': [
            {
                'slug': item['slug'],
                'label': item.get('label') or item.get('nome', ''),
                'direction': item.get('dir', ''),
                'type': item.get('tipo', ''),
                'engine': 'candles',
            }
            for item in CANDLE_PATTERNS
        ],
        'cores': [
            {
                'slug': item['slug'],
                'label': item.get('label') or item.get('nome', ''),
                'direction': item.get('dir', ''),
                'type': item.get('tipo', ''),
                'sequence': item.get('sequencia', ''),
                'engine': 'cores',
            }
            for item in SEQUENCE_PATTERNS
        ],
    }


def normalize_selected(kind: str, values: Iterable[str] | None) -> list[str]:
    index = CANDLE_INDEX if kind == 'candles' else SEQUENCE_INDEX
    out = []
    seen = set()
    for value in (values or []):
        key = str(value or '').strip()
        if key and key in index and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def selected_union_for_bot(selected_candles: Iterable[str] | None, selected_cores: Iterable[str] | None) -> list[str]:
    out = []
    seen = set()
    for value in list(normalize_selected('candles', selected_candles)) + list(normalize_selected('cores', selected_cores)):
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _filtered_patterns(kind: str, selected: Iterable[str] | None) -> list[dict]:
    normalized = normalize_selected(kind, selected)
    if kind == 'candles':
        index = CANDLE_INDEX
        base = CANDLE_PATTERNS
    else:
        index = SEQUENCE_INDEX
        base = SEQUENCE_PATTERNS
    if not normalized:
        return []
    return [copy.deepcopy(index[slug]) for slug in normalized if slug in index] or [copy.deepcopy(p) for p in base]


def execute_catalogador(kind: str, username: str, asset: str, candles_count: int = 260, timeframe: int = 60, selected: Iterable[str] | None = None) -> dict:
    kind = 'candles' if kind == 'candles' else 'cores'
    normalized = normalize_selected(kind, selected)
    if not normalized:
        raise ValueError('Selecione ao menos um padrão para este catalogador.')

    iq = IQ.get_iq(username)
    if not iq:
        raise RuntimeError('Conecte a corretora antes de executar o catalogador.')

    module = candles_mod if kind == 'candles' else cores_mod
    patterns = _filtered_patterns(kind, normalized)

    with _RUNTIME_LOCK:
        original_timeframe = getattr(module, 'TIMEFRAME', 60)
        original_total_velas = getattr(module, 'TOTAL_VELAS', 260)
        original_patterns = copy.deepcopy(getattr(module, 'PADROES_ATIVOS', []))
        original_top = copy.deepcopy(getattr(module, 'top_padroes', {}))
        original_iq = getattr(module, 'iq', None)
        try:
            module.TIMEFRAME = int(timeframe or 60)
            module.TOTAL_VELAS = max(30, int(candles_count or 260))
            module.PADROES_ATIVOS = [
                {
                    key: value
                    for key, value in item.items()
                    if key in ('id', 'nome', 'sequencia', 'dir', 'tipo')
                }
                for item in patterns
            ]
            module.top_padroes = {}
            module.iq = iq
            result, err = module.analisar_ativo(asset)
            if err:
                raise RuntimeError(err)
            if not result:
                raise RuntimeError('Nenhum resultado foi produzido pelo catalogador.')
        finally:
            module.TIMEFRAME = original_timeframe
            module.TOTAL_VELAS = original_total_velas
            module.PADROES_ATIVOS = original_patterns
            module.top_padroes = original_top
            module.iq = original_iq

    top4 = result.get('top4_local', []) or []
    return {
        'engine': kind,
        'asset': result.get('ativo', asset),
        'timeframe': int(timeframe or 60),
        'candles': max(30, int(candles_count or 260)),
        'selected_patterns': normalized,
        'selected_count': len(normalized),
        'summary': {
            'wins': result.get('wins', 0),
            'losses': result.get('losses', 0),
            'entries': result.get('entradas', 0),
            'wr': result.get('wr', 0.0),
            'score': result.get('score', 0.0),
            'score_top4': result.get('score_top4', 0.0),
        },
        'top_patterns': top4,
        'ranking_local': result.get('ranking_local', [])[:12],
    }
