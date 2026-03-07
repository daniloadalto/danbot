#!/usr/bin/env python3
"""
Script de patch para ser executado no Railway no startup do DANBOT.
Corrige a compatibilidade do iqoptionapi com websocket-client 1.x.
Este script deve ser chamado antes de importar iq_integration.

Salvar em: /home/user/DANBOT_DEPLOY/patch_iqoptionapi.py
"""
import os, sys, shutil, logging

log = logging.getLogger('iqpatch')

def apply_iqoptionapi_patch():
    """Aplica o patch de compatibilidade no iqoptionapi instalado."""
    import importlib.util
    
    # Encontrar client.py do iqoptionapi
    try:
        spec = importlib.util.find_spec('iqoptionapi')
        if spec is None:
            log.warning("iqoptionapi não encontrado")
            return False
        
        pkg_dir = os.path.dirname(spec.origin)
        client_file = os.path.join(pkg_dir, 'ws', 'client.py')
        
        if not os.path.exists(client_file):
            log.warning(f"ws/client.py não encontrado em {pkg_dir}")
            return False
        
        with open(client_file, 'r') as f:
            src = f.read()
        
        changed = False
        
        # Fix 1: on_close deve aceitar code e msg (websocket 1.x)
        if 'def on_close(wss): # pylint: disable=unused-argument' in src:
            src = src.replace(
                'def on_close(wss): # pylint: disable=unused-argument',
                'def on_close(wss, close_status_code=None, close_msg=None): # ws 1.x compat'
            )
            changed = True
            log.info("✅ Patch: on_close assinatura corrigida")
        
        # Fix 2: Wrappers no WebSocketApp
        if 'on_message=self.on_message' in src and '_on_msg' not in src:
            # Encontrar o WebSocketApp e adicionar wrappers
            old_wss = 'self.wss = websocket.WebSocketApp(\n            self.api.wss_url, on_message=self.on_message,'
            if old_wss in src:
                new_wss = '''# Wrappers compatibilidade ws 0.56+1.x
        _on_msg   = lambda ws, msg:                  self.on_message(msg)
        _on_err   = lambda ws, err:                  self.on_error(ws, err)
        _on_close = lambda ws, code=None, msg=None:  self.on_close(ws, code, msg)
        _on_open  = lambda ws:                        self.on_open(ws)
        self.wss = websocket.WebSocketApp(\n            self.api.wss_url, on_message=_on_msg,'''
                # Substituir callbacks restantes
                # Primeiro pegar o bloco original para substituição correta
                idx_start = src.find(old_wss)
                idx_end = src.find(')', idx_start) + 1
                orig_block = src[idx_start:idx_end]
                new_block = orig_block.replace('on_message=self.on_message,', 'on_message=_on_msg,')
                new_block = new_block.replace('on_error=self.on_error,', 'on_error=_on_err,')
                new_block = new_block.replace('on_close=self.on_close,', 'on_close=_on_close,')
                new_block = new_block.replace('on_open=self.on_open,', 'on_open=_on_open,')
                new_block = new_block.replace('on_open=self.on_open)', 'on_open=_on_open)')
                new_block = '        # Wrappers ws 0.56+1.x\n        _on_msg   = lambda ws, msg: self.on_message(msg)\n        _on_err   = lambda ws, err: self.on_error(ws, err)\n        _on_close = lambda ws, code=None, msg=None: self.on_close(ws, code, msg)\n        _on_open  = lambda ws: self.on_open(ws)\n        ' + new_block
                src = src[:idx_start] + new_block + src[idx_end:]
                changed = True
                log.info("✅ Patch: wrappers compatibilidade adicionados")
        
        # Fix 3: User-Agent antigo
        if 'Chrome/66' in src:
            src = src.replace('Chrome/66.0.3359.139', 'Chrome/120.0.0.0')
            changed = True
            log.info("✅ Patch: User-Agent Chrome/120")
        
        if changed:
            shutil.copy(client_file, client_file + '.original')
            with open(client_file, 'w') as f:
                f.write(src)
            log.info(f"✅ iqoptionapi patched: {client_file}")
        else:
            log.info("ℹ️  iqoptionapi: patch já aplicado ou não necessário")
        
        return True
        
    except Exception as e:
        log.error(f"Erro no patch iqoptionapi: {e}")
        return False


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    apply_iqoptionapi_patch()
