#!/usr/bin/env python3
"""
10 entradas com mínimo 5 confluências.
Reconecta antes de cada trade. Análise com dados sintéticos variados.
"""
import sys, time, json
sys.path.insert(0,'/home/user/DANBOT_DEPLOY')
import numpy as np
from iqoptionapi.stable_api import IQ_Option
from iq_integration import analyze_asset_full

EMAIL='danilo.traderdolar@outlook.com'; PASSWORD='310519@'
ENTRY=2.0; MIN_CONF=5
STRATEGIES={'ema':True,'rsi':True,'bb':True,'macd':True,'adx':True,'stoch':True,'lp':True,'pat':True,'fib':True}
ASSETS=['AUDCAD-OTC','GBPCHF-OTC','EURCAD-OTC','CHFJPY-OTC','NZDJPY-OTC',
        'CADCHF-OTC','EURAUD-OTC','USDMXN-OTC','USDTRY-OTC','USDZAR-OTC',
        'LTCUSD-OTC','USNDAQ100-OTC','GER30-OTC','APPLE-OTC','FB-OTC',
        'ALIBABA-OTC','GS-OTC','JPM-OTC','NIKE-OTC','XAUUSD-OTC',
        'EURUSD-OTC','EURGBP-OTC','GBPUSD-OTC','USDCHF-OTC','SP500-OTC']

def connect():
    iq=IQ_Option(EMAIL,PASSWORD); ch,_=iq.connect()
    if not ch: return None
    iq.change_balance('PRACTICE'); time.sleep(2); return iq

def best_signal(seed):
    best=None; bf=0
    for asset in ASSETS:
        s=(hash(asset)%9999)+seed*17
        np.random.seed(s)
        td=1 if s%3!=0 else -1
        base=1.05+(hash(asset)%80)*0.01
        c=base+np.cumsum(np.random.randn(60)*0.00028+td*0.0003)
        h=c+np.abs(np.random.randn(60))*0.0002
        l=c-np.abs(np.random.randn(60))*0.0002
        o=np.roll(c,1); o[0]=c[0]
        ohlc={'opens':o,'highs':h,'lows':l,'closes':c}
        r=analyze_asset_full(asset,ohlc,strategies=STRATEGIES,min_confluence=MIN_CONF)
        if r:
            sc=r.get('score_call',0)+r.get('score_put',0)
            if sc>bf: bf=sc; best={'asset':asset,'sc':sc,**r}
    return best

entradas=[]; bal_ini=None; seed=1
print(f'\n{"="*55}\n  10 ENTRADAS | min_confluence={MIN_CONF} | R${ENTRY}\n{"="*55}\n')
print('🔌 Conexão inicial...')
iq0=connect()
if iq0: bal_ini=round(float(iq0.get_balance()),2); iq0.close(); print(f'✅ Saldo inicial: R${bal_ini:.2f}')
else: print('⚠️  Sem conexão. Estimando saldo.'); bal_ini=7838.53

n=0
while n<10 and seed<80:
    seed+=1
    sig=best_signal(seed)
    if not sig: continue
    asset=sig['asset']; direc=sig['direction']; forca=sig['strength']
    sc=sig['sc']; pattern=sig.get('pattern','—'); reason=sig.get('reason','')
    reasons_list=[x.strip() for x in reason.split('|') if x.strip()]
    print(f'[{n+1:02d}] {asset} | {direc} | {forca}% | score={sc} | {pattern[:40]}')
    for i,rs in enumerate(reasons_list[:5]): print(f'    {i+1}. {rs[:55]}')
    print(f'    🔌 Reconectando...')
    iq=connect()
    if not iq:
        print(f'    ❌ Falha na conexão'); entradas.append({'entrada':n+1,'asset':asset,'dir':direc,'forca':forca,'score':sc,'padrao':pattern,'resultado':'erro','lucro':0}); n+=1; continue
    print(f'    💰 Comprando {direc} R${ENTRY}...')
    try: check,oid=iq.buy(ENTRY,asset,direc.lower(),1)
    except Exception as e: print(f'    ❌ {e}'); iq.close(); entradas.append({'entrada':n+1,'asset':asset,'dir':direc,'forca':forca,'score':sc,'padrao':pattern,'resultado':'erro','lucro':0}); n+=1; continue
    if not check:
        print(f'    ⛔ Rejeitado'); iq.close(); entradas.append({'entrada':n+1,'asset':asset,'dir':direc,'forca':forca,'score':sc,'padrao':pattern,'resultado':'rejeitado','lucro':0}); n+=1; continue
    print(f'    ⏳ Ordem {oid} aceita | aguardando 65s...')
    time.sleep(65)
    try: lraw=float(iq.check_win_v4(oid))
    except: lraw=None
    try: bal_pos=round(float(iq.get_balance()),2)
    except: bal_pos=0
    iq.close()
    if lraw is None: res,lucro,em='timeout',0.0,'⏱️'
    elif lraw>0: res,lucro,em='win',round(lraw,2),'✅'
    elif lraw==0: res,lucro,em='empate',0.0,'🟡'
    else: res,lucro,em='loss',round(lraw,2),'❌'
    print(f'    {em} {res.upper()} | R${lucro:+.2f} | saldo: R${bal_pos:.2f}')
    entradas.append({'entrada':n+1,'asset':asset,'dir':direc,'forca':forca,'score':sc,'padrao':pattern,'resultado':res,'lucro':lucro,'saldo_pos':bal_pos,'reasons':reasons_list[:5]})
    n+=1; print()
    time.sleep(3)

iq_f=connect(); bal_fin=round(float(iq_f.get_balance()),2) if iq_f else (entradas[-1].get('saldo_pos',bal_ini) if entradas else bal_ini); 
if iq_f: iq_f.close()
wins=sum(1 for e in entradas if e['resultado']=='win')
losses=sum(1 for e in entradas if e['resultado']=='loss')
rejeit=sum(1 for e in entradas if e['resultado']=='rejeitado')
erros=sum(1 for e in entradas if e['resultado']=='erro')
lucro_t=sum(e['lucro'] for e in entradas)
wr=wins/(wins+losses)*100 if wins+losses>0 else 0

print(f'{"="*55}\n  RELATÓRIO — 10 ENTRADAS | min_confluence={MIN_CONF}\n{"="*55}')
print(f'  Data    : {time.strftime("%d/%m/%Y %H:%M")}')
print(f'  Saldo ini: R${bal_ini:.2f} | Saldo fim: R${bal_fin:.2f}')
print(f'  Resultado: R${lucro_t:+.2f}')
print(f'  Wins={wins} Loss={losses} Rej={rejeit} Err={erros} WR={wr:.1f}%')
print(f'\n  {"#":>2}  {"Ativo":<18} {"Dir":<5} {"Força":>6} {"Score":>6} {"Resultado":<10} {"Lucro":>8}')
print('  '+'-'*65)
for e in entradas:
    icon={'win':'✅','loss':'❌','rejeitado':'⛔','erro':'⚠️','timeout':'⏱️'}.get(e['resultado'],'?')
    print(f'  {e["entrada"]:>2}  {e["asset"]:<18} {e["dir"]:<5} {e["forca"]:>5}%  {e["score"]:>5}  {icon}{e["resultado"]:<9} R${e["lucro"]:>+6.2f}')

print(f'\n  Padrões detectados: {set(e["padrao"][:35] for e in entradas if e.get("padrao","—")!="—")}')
rel={'data':time.strftime('%Y-%m-%d %H:%M'),'saldo_inicial':bal_ini,'saldo_final':bal_fin,'lucro':round(lucro_t,2),'wins':wins,'losses':losses,'rejeicoes':rejeit,'erros':erros,'win_rate':round(wr,1),'min_conf':MIN_CONF,'entradas':entradas}
json.dump(rel,open('/tmp/relatorio_10.json','w',encoding='utf-8'),ensure_ascii=False,indent=2)
print(f'\n  💾 Salvo em /tmp/relatorio_10.json\n{"="*55}')
