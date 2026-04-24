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
_ALL_SCAN_TOKENS = {'ALL', 'TODOS', 'TODAS', '*'}


def _slugify(value: str) -> str:
    value = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^a-zA-Z0-9]+', '_', value).strip('_').lower()
    return value or 'padrao'


def _clone_patterns(patterns: Iterable[dict]) -> list[dict]:
    return [copy.deepcopy(p) for p in (patterns or [])]


def _unique_assets(values: Iterable[str] | None) -> list[str]:
    out = []
    seen = set()
    for value in (values or []):
        asset = str(value or '').strip().upper()
        if not asset or asset == 'AUTO' or asset in seen:
            continue
        seen.add(asset)
        out.append(asset)
    return out


def _normalize_asset_choice(asset: str | None) -> str:
    value = str(asset or '').strip().upper()
    return 'ALL' if value in _ALL_SCAN_TOKENS else value


def _iq_session_valid(username: str) -> bool:
    try:
        return bool(IQ.is_iq_session_valid(username))
    except TypeError:
        try:
            return bool(IQ.is_iq_session_valid())
        except Exception:
            return False
    except Exception:
        return False


def get_catalog_assets(username: str | None = None) -> list[str]:
    assets: list[str] = []
    if username:
        try:
            if hasattr(IQ, 'set_user_context'):
                IQ.set_user_context(username)
        except Exception:
            pass
        try:
            if _iq_session_valid(username) and hasattr(IQ, 'get_available_all_assets'):
                assets = list(IQ.get_available_all_assets() or [])
        except Exception:
            assets = []

    if not assets:
        assets = list(getattr(IQ, 'ALL_BINARY_ASSETS', []) or [])
    if not assets:
        assets = list(getattr(IQ, 'OTC_BINARY_ASSETS', []) or []) + list(getattr(IQ, 'OPEN_BINARY_ASSETS', []) or [])
    return _unique_assets(assets)


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


def _run_catalogador_once(kind: str, iq, asset: str, candles_count: int, timeframe: int, patterns: list[dict], normalized: list[str]) -> dict:
    module = candles_mod if kind == 'candles' else cores_mod
    asset = _normalize_asset_choice(asset)
    if not asset or asset == 'ALL':
        raise ValueError('Informe um ativo válido para executar o catalogador.')

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
        'selected_patterns': list(normalized),
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


def _result_rank_key(item: dict) -> tuple:
    summary = item.get('summary', {}) or {}
    wins = int(summary.get('wins', 0) or 0)
    losses = int(summary.get('losses', 0) or 0)
    entries = int(summary.get('entries', 0) or 0)
    wr = float(summary.get('wr', 0.0) or 0.0)
    score = float(summary.get('score_top4', 0.0) or summary.get('score', 0.0) or 0.0)
    return (wr, score, entries, wins, -losses)


def execute_catalogador_scan(kind: str, username: str, assets: Iterable[str], candles_count: int = 260, timeframe: int = 60, selected: Iterable[str] | None = None) -> dict:
    kind = 'candles' if kind == 'candles' else 'cores'
    normalized = normalize_selected(kind, selected)
    if not normalized:
        raise ValueError('Selecione ao menos um padrão para este catalogador.')

    iq = IQ.get_iq(username)
    if not iq:
        raise RuntimeError('Conecte a corretora antes de executar o catalogador.')

    patterns = _filtered_patterns(kind, normalized)
    asset_list = _unique_assets(assets)
    if not asset_list:
        raise RuntimeError('Nenhum ativo disponível foi encontrado para o modo TODOS.')

    results = []
    errors = []
    for asset in asset_list:
        try:
            results.append(_run_catalogador_once(kind, iq, asset, candles_count, timeframe, patterns, normalized))
        except Exception as exc:
            errors.append({'asset': asset, 'error': str(exc)})

    if not results:
        sample = '; '.join(f"{item['asset']}: {item['error']}" for item in errors[:5])
        raise RuntimeError(sample or 'Não foi possível catalogar nenhum ativo no modo TODOS.')

    ranked_results = sorted(results, key=_result_rank_key, reverse=True)
    best = copy.deepcopy(ranked_results[0])
    best['scan_mode'] = 'all'
    best['requested_asset'] = 'ALL'
    best['best_asset'] = best.get('asset')
    best['assets_requested'] = len(asset_list)
    best['assets_tested'] = len(results)
    best['assets_failed'] = len(errors)
    best['failed_assets'] = errors[:12]
    best['ranked_assets'] = [
        {
            'asset': item.get('asset'),
            'wr': float((item.get('summary') or {}).get('wr', 0.0) or 0.0),
            'entries': int((item.get('summary') or {}).get('entries', 0) or 0),
            'wins': int((item.get('summary') or {}).get('wins', 0) or 0),
            'losses': int((item.get('summary') or {}).get('losses', 0) or 0),
            'score': float((item.get('summary') or {}).get('score_top4', 0.0) or (item.get('summary') or {}).get('score', 0.0) or 0.0),
            'top_pattern': ((item.get('top_patterns') or [{}])[0] or {}).get('padrao') or ((item.get('top_patterns') or [{}])[0] or {}).get('nome') or '-',
        }
        for item in ranked_results[:15]
    ]
    return best


def execute_catalogador(kind: str, username: str, asset: str, candles_count: int = 260, timeframe: int = 60, selected: Iterable[str] | None = None) -> dict:
    kind = 'candles' if kind == 'candles' else 'cores'
    normalized = normalize_selected(kind, selected)
    if not normalized:
        raise ValueError('Selecione ao menos um padrão para este catalogador.')

    iq = IQ.get_iq(username)
    if not iq:
        raise RuntimeError('Conecte a corretora antes de executar o catalogador.')

    patterns = _filtered_patterns(kind, normalized)
    asset = _normalize_asset_choice(asset)
    if not asset:
        raise ValueError('Escolha um ativo ou use TODOS para o catalogador.')
    if asset == 'ALL':
        return execute_catalogador_scan(kind, username, get_catalog_assets(username), candles_count=candles_count, timeframe=timeframe, selected=normalized)
    return _run_catalogador_once(kind, iq, asset, candles_count, timeframe, patterns, normalized)
