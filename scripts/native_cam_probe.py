"""Developer console for native CAM API validation. No screen/input automation."""
import json
import sys
import time
from pathlib import Path
from makera_mcp.native import NativeStudio
from makera_mcp.config import Settings

studio = NativeStudio(Settings.load())
print('READY', flush=True)
try:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request['method']
            if method == 'exit':
                break
            if method in ('open_project', 'save_project', 'read'):
                result = getattr(studio, method)(*request.get('args', []))
            else:
                result = studio._call(method, *request.get('args', []))
            if request.get('save'):
                Path(request['save']).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            if request.get('brief') and isinstance(result,dict) and 'items' in result:
                result = {'modal':result['modal'],'root':result['root'],'items':[
                    {**{k:v for k,v in o.items() if k!='methods'},'methods':[m['signature'] for m in o['methods']] if not o['type'].startswith('Q') else []}
                    for o in result['items'] if o['value'] or not o['type'].startswith('Q')]}
            print(json.dumps(result,ensure_ascii=False),flush=True)
        except Exception as exc:
            print(json.dumps({'error':str(exc)},ensure_ascii=False),flush=True)
finally:
    time.sleep(.2)
    studio.close()
