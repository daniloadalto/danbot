import numpy as np

import app
import iq_integration as IQ


def _sample_ohlc(size=60):
    closes = np.linspace(1.1000, 1.1060, size)
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    highs = np.maximum(opens, closes) + 0.0003
    lows = np.minimum(opens, closes) - 0.0003
    ohlc = {
        'opens': opens.tolist(),
        'highs': highs.tolist(),
        'lows': lows.tolist(),
        'closes': closes.tolist(),
    }
    return closes.tolist(), ohlc


def test_effective_runtime_strategies_respects_confluence_toggle():
    raw = {'ma': True, 'rsi': True, 'bb': False}
    off = app._effective_runtime_strategies(raw, False)
    on = app._effective_runtime_strategies(raw, True)

    assert off == app.DEFAULT_STRATEGIES
    assert on['ma'] is True
    assert on['rsi'] is True
    assert on['bb'] is False


def test_scan_assets_pattern_only_ignores_profile_strategy_overrides_and_otc_hard_filter(monkeypatch):
    closes, ohlc = _sample_ohlc()
    captured = []

    monkeypatch.setattr(IQ, 'get_iq', lambda: None)
    monkeypatch.setattr(IQ, 'generate_synthetic_candles', lambda asset, count: (closes, ohlc))
    monkeypatch.setattr(IQ, 'get_asset_profile', lambda asset, force_refresh=False, timeframe=60: {
        'confluencia_minima': 5,
        'confluencia_sugerida': 5,
        'strategies_override': {'ma': True, 'rsi': True, 'macd': True},
        'market_quality_score': 41,
        'market_quality_preferred': False,
        'trend': 'sideways',
        'volatility_regime': 'high',
        'market_quality_regime': 'sideways',
        'padroes_ativos': ['martelo'],
    })
    monkeypatch.setattr(IQ, '_bridge_detect_selected_candle_patterns', lambda **kwargs: [{
        'slug': 'cndl_mock',
        'label': 'Mock Pattern',
        'direction': 'CALL',
        'accuracy': 78,
        'premium': False,
    }])

    def fake_analyze(asset, ohlc_payload, strategies=None, min_confluence=3, dc_mode='disabled', base_timeframe=60, selected_candle_patterns=None):
        captured.append(dict(strategies or {}))
        return {
            'asset': asset,
            'direction': 'CALL',
            'strength': 72,
            'pattern': '🕯 Mock Pattern',
            'reason': 'Padrão selecionado detectado: Mock Pattern (78%)',
            'market_quality_score': 41,
            'detail': {
                'catalog_match_count': 1,
                'catalog_primary_hit': {'label': 'Mock Pattern', 'direction': 'CALL'},
                'modules': {},
                'market_quality': {
                    'quality_score': 41,
                    'preferred': False,
                    'regime': 'sideways',
                    'avg_wick_ratio': 0.72,
                },
                'entry_guard': {
                    'blocked': False,
                    'mode': 'candle_catalog_only',
                    'selected_pattern': {'label': 'Mock Pattern', 'direction': 'CALL'},
                    'premium_reversal': False,
                    'trend_priority': False,
                    'timing': {},
                },
                'timing': {},
            },
        }

    monkeypatch.setattr(IQ, 'analyze_asset_full', fake_analyze)

    signals = IQ.scan_assets(
        ['EURUSD-OTC'],
        timeframe=60,
        count=50,
        strategies={},
        min_confluence=4,
        dc_mode='disabled',
        selected_candle_patterns=['cndl_mock'],
    )

    assert len(signals) == 1
    assert signals[0]['direction'] == 'CALL'
    assert captured, 'analyze_asset_full deveria ter sido chamado'
    assert captured[0].get('ma') is False
    assert captured[0].get('rsi') is False
    assert captured[0].get('macd') is False
