"""
DANBOT WEB v2.0 — Backend Flask
Bot de Arbitragem OTC para Opções Binárias
"""
from flask import Flask, render_template, request, jsonify, session, has_request_context
from flask_sqlalchemy import SQLAlchemy
import hashlib, uuid, datetime, os, jwt, secrets, threading, time, json, random, socket
import urllib.request, urllib.error
import re, unicodedata
import difflib
from datetime import timezone, timedelta as _timedelta

BRT = timezone(_timedelta(hours=-3))

def _brt_now():
    """Retorna hora atual no fuso horário de Brasília (UTC-3)"""
    return (datetime.datetime.utcnow() - _timedelta(hours=3))

def _brt_str():
    """Retorna string de hora no fuso de Brasília (UTC-3)"""
    return _brt_now().strftime('%H:%M:%S')
import numpy as np
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import iq_integration as IQ

# ─── IMPORTS RESILIENTES HYBRID ──────────────────────────────────────────
try:
    from iq_integration import run_backtest, run_backtest_real, gerar_perfil_ativo, get_asset_profile, _asset_profiles, OTC_BINARY_ASSETS, ALL_BINARY_ASSETS, OPEN_BINARY_ASSETS, check_volume_filter, start_heartbeat, stop_heartbeat
except ImportError as e:
    # Fallback para modulo hybrid novo
    try:
        from iq_integration import run_backtest_real, get_asset_profile, generate_synthetic_candles
        _asset_profiles = {}
        OTC_BINARY_ASSETS = ['EURUSD-OTC','GBPUSD-OTC','USDJPY-OTC','AUDUSD-OTC']
        ALL_BINARY_ASSETS = OTC_BINARY_ASSETS
        OPEN_BINARY_ASSETS = OTC_BINARY_ASSETS
        def check_volume_filter(*a, **k): return True
        def start_heartbeat(*a, **k): pass
        def stop_heartbeat(*a, **k): pass
        def run_backtest(*a, **k): return {'overall_win_rate': 0}
    except Exception:
        run_backtest_real = lambda *a, **k: {'overall_win_rate': 0}
        get_asset_profile = lambda *a, **k: {}
        generate_synthetic_candles = lambda *a, **k: ([], {})
        _asset_profiles = {}
        OTC_BINARY_ASSETS = ['EURUSD-OTC','GBPUSD-OTC']
        ALL_BINARY_ASSETS = OTC_BINARY_ASSETS
        OPEN_BINARY_ASSETS = OTC_BINARY_ASSETS
        def check_volume_filter(*a, **k): return True
        def start_heartbeat(*a, **k): pass
        def stop_heartbeat(*a, **k): pass
        def run_backtest(*a, **k): return {'overall_win_rate': 0}
        gerar_perfil_ativo = lambda x: x

try:
    import catalogador_runtime as CATALOG
except ImportError:
    class _DummyCatalog:
        def selected_union_for_bot(self, a,b): return ['HYB_EQ_FX']
        def normalize_selected(self, k,v): return v or ['HYB_EQ_FX']
        def get_catalog_payload(self): return {'candles':[{'slug':'HYB_EQ_FX','label':'Hybrid'}], 'cores':[]}
    CATALOG = _DummyCatalog()
# ─────────────────────────────────────────────────────────────────────────


app = Flask(__name__)

@app.route('/health')
@app.route('/healthcheck')
def health_check():
    return jsonify({'status': 'ok', 'service': 'danbot', 'mode': 'HYBRID_EQUILIBRIO_M1_FAST_FOREX'}), 200

@app.route('/')
def root():
    try:
        return render_template('dashboard.html')
    except Exception:
        return jsonify({'status': 'ok', 'service': 'danbot-hybrid', 'mode': 'HYBRID'}), 200

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or os.environ.get('FLASK_SECRET_KEY') or 'danbot-default-secret-key-2025-change-me'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////data/danbot.db' if os.path.exists('/data') else 'sqlite:///danbot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

MASTER_SECRET = os.environ.get('MASTER_SECRET', 'DANBOT-MASTER-2025')

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

# ─── ARQUITETURA MULTI-USUÁRIO ────────────────────────────────────────────────
# Cada usuário tem seu próprio estado isolado: bot, broker, placar, log, etc.
# Nenhum dado é compartilhado entre usuários.

_SUSPENSION_TIMEOUT = 900  # 15 minutos de espera para tentar novamente
_ENTRY_EXECUTION_TIMEOUT = 120  # desistir da entrada após 120s e rescanner

DEFAULT_STRATEGIES = {
    'i3wr': False,
    'ma': False,
    'rsi': False,
    'bb': False,
    'macd': False,
    'simple_trend': False,
    'pullback_m5': False,
    'pullback_m15': False,
    'dead': False,
    'reverse': False,
}


def _normalize_runtime_strategies(raw: dict | None) -> dict:
    merged = dict(DEFAULT_STRATEGIES)
    if isinstance(raw, dict):
        for key in list(merged.keys()):
            if key in raw:
                merged[key] = bool(raw.get(key))
    return merged


def _has_any_runtime_strategy(raw: dict | None) -> bool:
    normalized = _normalize_runtime_strategies(raw)
    return any(bool(normalized.get(key)) for key in normalized.keys())


def _effective_runtime_strategies(raw: dict | None, confluence_enabled: bool) -> dict:
    normalized = _normalize_runtime_strategies(raw)
    return normalized if bool(confluence_enabled) else dict(DEFAULT_STRATEGIES)


def _sync_catalog_pattern_union(state: dict) -> list:
    selected = CATALOG.selected_union_for_bot(
        state.get('selected_catalog_patterns_candles', []),
        state.get('selected_catalog_patterns_cores', []),
    )
    state['selected_candle_patterns'] = selected
    return selected


def _normalize_catalog_selections(state: dict) -> None:
    state['selected_catalog_patterns_candles'] = CATALOG.normalize_selected('candles', state.get('selected_catalog_patterns_candles', []))
    state['selected_catalog_patterns_cores'] = CATALOG.normalize_selected('cores', state.get('selected_catalog_patterns_cores', []))
    _sync_catalog_pattern_union(state)


def _ai_text_key(value) -> str:
    return str(value or '').strip().lower()


def _catalog_slug_maps() -> dict:
    payload = CATALOG.get_catalog_payload()
    maps = {'candles': {}, 'cores': {}}
    for kind in ('candles', 'cores'):
        for item in payload.get(kind, []) or []:
            slug = str(item.get('slug') or '').strip()
            label = _ai_text_key(item.get('label') or item.get('nome') or '')
            seq = _ai_text_key(item.get('sequence') or '')
            if slug:
                maps[kind][slug] = slug
            if label:
                maps[kind][label] = slug
            if seq:
                maps[kind][seq] = slug
                maps[kind][f'sequencia {seq}'] = slug
    return maps


def _sanitize_otc_assets(values, limit: int | None = None) -> list[str]:
    out = []
    seen = set()
    for raw in (values or []):
        asset = str(raw or '').strip().upper()
        if not asset or asset == 'AUTO' or not asset.endswith('-OTC') or asset in seen:
            continue
        seen.add(asset)
        out.append(asset)
        if limit and len(out) >= limit:
            break
    return out


def _coerce_otc_choice(asset: str | None) -> str:
    value = str(asset or 'AUTO').strip().upper() or 'AUTO'
    if value == 'AUTO':
        return 'AUTO'
    return value if value.endswith('-OTC') else 'AUTO'


def _normalize_otc_filters(asset_market_filter=None, bt_scope=None, asset_filter=None) -> tuple[str, str, str]:
    return ('otc', 'otc', 'otc_only')


def _guess_ai_scope(state: dict) -> str:
    return 'otc'


def _default_ai_pattern_labels() -> list[str]:
    return [
        'martelo',
        'enforcado',
        'estrela cadente',
        'engolfo alta',
        'engolfo baixa',
        'sequencia gggg',
        'sequencia rrrr',
    ]


def _build_ai_fallback_plan(state: dict, scope: str = None) -> dict:
    maps = _catalog_slug_maps()
    candle_slugs = []
    core_slugs = []
    for label in _default_ai_pattern_labels():
        slug = maps['candles'].get(_ai_text_key(label))
        if slug and slug not in candle_slugs:
            candle_slugs.append(slug)
        slug = maps['cores'].get(_ai_text_key(label))
        if slug and slug not in core_slugs:
            core_slugs.append(slug)
    assets = _sanitize_otc_assets((state.get('_bt_top_assets', []) or []), limit=6)
    if not assets:
        assets = _select_backtest_assets('otc', limit=6)[:6]
    strategies = _normalize_runtime_strategies({
        'i3wr': True, 'ma': True, 'rsi': True, 'bb': False, 'macd': True,
        'simple_trend': True, 'pullback_m5': True, 'pullback_m15': False, 'dead': True, 'reverse': False,
    })
    return {
        'profile': str(state.get('ai_autonomy_profile', 'balanced') or 'balanced'),
        'mode': 'fallback',
        'scope': 'otc',
        'assets': assets[:6],
        'asset_market_filter': 'otc',
        'candle_patterns': candle_slugs[:8],
        'core_patterns': core_slugs[:8],
        'strategies': strategies,
        'min_confluence': max(2, int(state.get('min_confluence', 4) or 4)),
        'best_asset': assets[0] if assets else 'AUTO',
        'reason': 'fallback_autonomy_bootstrap',
        'summary': 'Plano inicial da IA criado com padrões-padrão até o backtest inteligente completar.',
        'confidence': 58,
        'source_assets': [],
        'profiles': [],
    }


def _build_ai_autonomy_plan(username: str, state: dict, ranked_override: list | None = None, scope: str = None, force_profile_refresh: bool = False) -> dict:
    scope = 'otc'
    ranked_src = list(ranked_override if ranked_override is not None else (state.get('_bt_ranked', []) or []))
    ranked = []
    for item in ranked_src:
        asset = str((item or {}).get('asset') or '').strip().upper()
        if asset.endswith('-OTC'):
            ranked.append(item)
    if not ranked:
        return _build_ai_fallback_plan(state, scope=scope)

    profile = str(state.get('ai_autonomy_profile', 'balanced') or 'balanced').strip().lower()
    maps = _catalog_slug_maps()
    timeframe = _normalize_trade_timeframe(state.get('trade_timeframe', 60))
    scored_assets = []
    for item in ranked[:8]:
        asset = str((item or {}).get('asset') or '').strip().upper()
        if not asset:
            continue
        try:
            prof = get_asset_profile(asset, force_refresh=force_profile_refresh, timeframe=timeframe)
        except Exception:
            prof = {
                'asset': asset,
                'overall_wr': float((item or {}).get('win_rate', 0) or 0),
                'market_quality_score': 50,
                'market_quality_preferred': False,
                'trend_continuity': 0.0,
                'padroes_ativos': [],
                'strategies_override': {},
                'confluencia_minima': max(2, int(state.get('min_confluence', 4) or 4)),
                'best_pattern': None,
                'best_pattern_wr': 0,
                'trend_label': 'Indefinida',
                'market_quality_regime': 'unknown',
            }
        wr = float(prof.get('overall_wr', (item or {}).get('win_rate', 0)) or 0)
        best_pattern_wr = float(prof.get('best_pattern_wr', 0) or 0)
        mq = float(prof.get('market_quality_score', 50) or 50)
        pref = 8.0 if prof.get('market_quality_preferred') else 0.0
        continuity = float(prof.get('trend_continuity', 0.0) or 0.0) * 12.0
        ops = min(12.0, float((item or {}).get('ops', 0) or 0) / 4.0)
        profile_bonus = {'safe': 4.0, 'balanced': 0.0, 'aggressive': -2.0}.get(profile, 0.0)
        wr_blend = (wr * 0.72) + (best_pattern_wr * 0.28 if best_pattern_wr > 0 else 0.0)
        score = wr_blend * 0.58 + mq * 0.24 + pref + continuity + ops + profile_bonus
        scored_assets.append({
            'asset': asset,
            'score': round(score, 2),
            'profile': prof,
            'rank': item,
        })
    if not scored_assets:
        return _build_ai_fallback_plan(state, scope=scope)

    scored_assets.sort(key=lambda x: x['score'], reverse=True)
    chosen_assets = [x['asset'] for x in scored_assets[:6]]
    chosen_profiles = [x['profile'] for x in scored_assets[:3]]
    best = scored_assets[0]

    selected_candle_slugs = CATALOG.normalize_selected('candles', state.get('selected_catalog_patterns_candles', []))
    selected_core_slugs = CATALOG.normalize_selected('cores', state.get('selected_catalog_patterns_cores', []))
    candle_slugs = list(selected_candle_slugs)
    core_slugs = list(selected_core_slugs)
    if not candle_slugs and not core_slugs:
        pattern_labels = []
        for prof in chosen_profiles:
            bp = _ai_text_key(prof.get('best_pattern'))
            if bp:
                pattern_labels.append(bp)
            for pat in list(prof.get('padroes_ativos', []) or [])[:5]:
                pat_key = _ai_text_key(pat)
                if pat_key:
                    pattern_labels.append(pat_key)
            for pat in list(prof.get('top_patterns', []) or [])[:4]:
                pat_key = _ai_text_key((pat or {}).get('nome') or (pat or {}).get('pattern') or '')
                if pat_key:
                    pattern_labels.append(pat_key)
        pattern_labels.extend(_default_ai_pattern_labels())
        seen_labels = set()
        for label in pattern_labels:
            if not label or label in seen_labels:
                continue
            seen_labels.add(label)
            cslug = maps['candles'].get(label)
            if cslug and cslug not in candle_slugs:
                candle_slugs.append(cslug)
            sslug = maps['cores'].get(label)
            if sslug and sslug not in core_slugs:
                core_slugs.append(sslug)

    merged_strategies = dict(DEFAULT_STRATEGIES)
    strategy_votes = {k: 0 for k in DEFAULT_STRATEGIES.keys()}
    for prof in chosen_profiles:
        over = prof.get('strategies_override', {}) or {}
        for key in merged_strategies.keys():
            if over.get(key):
                strategy_votes[key] += 1
    for key in merged_strategies.keys():
        threshold = 1 if key in ('i3wr', 'ma', 'rsi', 'simple_trend') else 2
        merged_strategies[key] = strategy_votes.get(key, 0) >= threshold
    merged_strategies['i3wr'] = True
    merged_strategies['ma'] = True
    merged_strategies['rsi'] = merged_strategies['rsi'] or profile != 'safe'
    merged_strategies['dead'] = True

    confluence_enabled = bool(state.get('confluence_enabled', False))
    if confluence_enabled:
        conf_candidates = [int((prof.get('confluencia_minima') or prof.get('confluencia_sugerida') or state.get('min_confluence', 4) or 4)) for prof in chosen_profiles]
        avg_conf = round(sum(conf_candidates) / max(1, len(conf_candidates))) if conf_candidates else int(state.get('min_confluence', 4) or 4)
        if profile == 'safe':
            min_conf = max(4, min(6, avg_conf + 1))
        elif profile == 'aggressive':
            min_conf = max(2, min(4, avg_conf - 1))
        else:
            min_conf = max(3, min(4, avg_conf))
        requested_conf = int(state.get('min_confluence', 0) or 0)
        if bool(state.get('ai_manual_confluence_override', False)) and requested_conf >= 2:
            min_conf = max(min_conf, min(6, requested_conf))
    else:
        # Confluências desligadas: o bot deve operar somente pelos padrões marcados.
        # min_confluence vira 1 (apenas o próprio padrão conta como módulo).
        min_conf = 1

    filter_mode = 'otc'
    confidence = min(96, max(60, int(round((float(best['profile'].get('overall_wr', 0) or 0) * 0.5) + (float(best['profile'].get('market_quality_score', 50) or 50) * 0.35)))))

    return {
        'profile': profile,
        'mode': 'ranked_profiles',
        'scope': scope,
        'assets': chosen_assets[:6],
        'asset_market_filter': filter_mode,
        'candle_patterns': candle_slugs,
        'core_patterns': core_slugs,
        'strategies': _normalize_runtime_strategies(merged_strategies),
        'min_confluence': min_conf,
        'best_asset': best['asset'],
        'reason': 'ranked_catalog_profiles' if _has_selected_catalog_patterns(state) else 'ranked_backtest_profiles',
        'summary': f"IA priorizou {best['asset']} e montou uma cesta dinâmica de {len(chosen_assets[:6])} ativos com base no ranking OTC atual e nos padrões já selecionados nos catalogadores.",
        'confidence': confidence,
        'source_assets': [{
            'asset': item['asset'],
            'score': item['score'],
            'wr': float(item['profile'].get('overall_wr', 0) or 0),
            'best_pattern_wr': float(item['profile'].get('best_pattern_wr', 0) or 0),
            'quality': float(item['profile'].get('market_quality_score', 0) or 0),
            'best_pattern': item['profile'].get('best_pattern'),
            'trend': item['profile'].get('trend_label', item['profile'].get('trend', '—')),
        } for item in scored_assets[:6]],
        'profiles': [{
            'asset': prof.get('asset'),
            'best_pattern': prof.get('best_pattern'),
            'best_pattern_wr': float(prof.get('best_pattern_wr', 0) or 0),
            'overall_wr': float(prof.get('overall_wr', 0) or 0),
            'market_quality_score': float(prof.get('market_quality_score', 0) or 0),
            'trend_label': prof.get('trend_label', prof.get('trend', '—')),
            'market_quality_regime': prof.get('market_quality_regime', 'unknown'),
        } for prof in chosen_profiles],
    }


def _build_ai_autonomy_payload(state: dict) -> dict:
    plan = dict(state.get('ai_autonomy_plan', {}) or {})
    return {
        'enabled': bool(state.get('ai_autonomy_enabled', False)),
        'profile': str(state.get('ai_autonomy_profile', 'balanced') or 'balanced'),
        'status': str(state.get('ai_autonomy_status', 'off') or 'off'),
        'running': bool(state.get('running', False)),
        'pending_start_confirmation': bool(state.get('ai_start_confirmation_pending', False)),
        'pending_start_confirmation_source': str(state.get('ai_start_confirmation_source', '') or ''),
        'manual_confluence_override': bool(state.get('ai_manual_confluence_override', False)),
        'last_plan_ts': float(state.get('ai_autonomy_last_plan_ts', 0.0) or 0.0),
        'last_refresh_reason': str(state.get('ai_autonomy_last_refresh_reason', '') or ''),
        'plan': plan,
        'history': list(state.get('ai_autonomy_history', []) or [])[:20],
        'long_log': list(state.get('ai_autonomy_log', []) or [])[:160],
        'latest_advice': dict(state.get('ai_latest_advice', {}) or {}),
        'operating_config': {
            'entry_value': round(float(state.get('entry_value', 0.0) or 0.0), 2),
            'trade_timeframe': _normalize_trade_timeframe(state.get('trade_timeframe', 60)),
            'stop_loss': round(float(state.get('stop_loss', 0.0) or 0.0), 2),
            'stop_win': round(float(state.get('stop_win', 0.0) or 0.0), 2),
            'consec
