# STUB COMPATIBILIDADE - Mantem app.py funcionando com HYBRID
def get_candle_pattern_catalog():
    return {'HYB_EQ_FX': {'accuracy': 100, 'label': 'Hybrid Equilibrio M1 FAST FOREX'}}

def normalize_selected_candle_patterns(x):
    return ['HYB_EQ_FX'] if not x else x

def detect_selected_candle_patterns(*args, **kwargs):
    return []
