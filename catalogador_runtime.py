
# STUB catalogador_runtime para HYBRID
def selected_union_for_bot(a,b):
    return ['HYB_EQ_FX']

def normalize_selected(kind, v):
    return v if v else ['HYB_EQ_FX']

def get_catalog_payload():
    return {
        'candles': [{'slug':'HYB_EQ_FX','label':'Hybrid Equilibrio','accuracy':100}],
        'cores': []
    }

def get_catalog_stats():
    return {'total':1}
