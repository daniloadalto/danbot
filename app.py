            if aid is not None:
                break
    except Exception:
        pass
    if aid is None:
        # (12/11) id oficial da SESSAO (canal v2): resolve os exoticos que a lib de 2021 nao conhece
        with _ASSET_IDS_LOCK:
            m = _SESSION_ID_MAP.get(id(api))
        if isinstance(m, dict):
            for cand in cands:
                v = m.get(cand)
                if v is not None:
                    aid = v
                    break
    if aid is None:
        aid = _asset_id_from_server(api, cands)
    if aid is None:
        inner = getattr(api, 'api', None)
        m = getattr(inner, 'instrument_id_mapping', None) if inner is not None else None
        if isinstance(m, dict):
            up = {str(k).upper(): v for k, v in m.items()}
            for cand in cands:
                if cand in up:
                    aid = up[cand]
                    break
    # ultimo recurso: variante sem OTC no ACTIVES (par regular) - pode divergir do OTC,
    # entao so entra se a corretora nao respondeu
    if aid is None and not is_otc:
        try:
            from iqoptionapi import constants as _OPC
            for cand in cands:
                aid = _OPC.ACTIVES.get(cand)
                if aid is not None:
                    break
        except Exception:
            pass
    with _ASSET_IDS_LOCK:
        _ASSET_IDS[key] = (aid, time.time())     # sucesso e falha entram no cache (falha com TTL)
    return aid

def _read_candles_raw(api, asset, interval, count, end, timeout):
    """Leitura de velas SEM o api.get_candles da biblioteca. O da lib (1) espera com 'while ...: pass' (queima CPU) e
    (2) em qualquer erro chama self.connect() = login completo dentro da thread leitora (tempestade de logins).
