"""Verify single-model placement through real MCP with generated geometry only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from makera_mcp.project import inspect_project
from validate_project_setup_mcp import make_fixtures

ROOT = Path(__file__).resolve().parents[1]


async def run(source, output):
    source=source.resolve(strict=True);output=output.resolve()
    if not output.is_relative_to((ROOT/'cases').resolve()) or output.exists():
        raise ValueError('Use a NEW output folder inside this repository cases/')
    original=inspect_project(str(source))
    if original['operations'] or original['models_3d']:
        raise ValueError('Source must have no operations or existing 3D models')
    output.mkdir(parents=True)
    project=output/'placement_check.mks';shutil.copy2(source,project)
    stl,_=make_fixtures(output)
    report={'scope':'synthetic single-model placement','calls':[],
            'machining_ready':False,'machine_commands_sent':False}
    env={**os.environ,'PYTHONIOENCODING':'utf-8','MAKERA_CAMERA_URL':'',
         'MAKERA_MCP_CONFIG':str(output/'no-camera.json')}
    parameters=StdioServerParameters(command=sys.executable,args=['-m','makera_mcp.server'],
                                     cwd=str(ROOT),env=env)
    async with stdio_client(parameters) as (reader,writer):
        async with ClientSession(reader,writer) as client:
            await client.initialize()

            async def call(name, arguments=None, expect_error=False):
                raw=await client.call_tool(name,arguments or {})
                body=next(b.text for b in raw.content if b.type=='text')
                try:
                    value=json.loads(body)
                except json.JSONDecodeError:
                    value={'message':body}
                report['calls'].append({'tool':name,'arguments':arguments or {},
                                        'isError':bool(raw.isError),'result':value})
                (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                if bool(raw.isError)!=expect_error:
                    raise RuntimeError(name+': '+str(value)[:240])
                return value

            caps=await call('get_capabilities')
            assert 'native_single_model_placement_saved_matrix_verification' in caps['supported']
            report['version']=caps['version']
            report['tool_count']=len((await client.list_tools()).tools)
            assert (await call('open_studio_project',{'path':str(project)}))['opened']
            assert (await call('configure_studio_stock',{'specification':{
                'shape':'box','dimensions_mm':{'x':80,'y':60,'z':30}}}))['verified']
            imported=await call('import_studio_model',{'path':str(stl),'units':'mm'})
            assert imported['saved_verified']
            uid=imported['new_models_3d'][0]['meshes'][0]['uuid']
            moves=[(40,{'x':0,'y':90,'z':0}),
                   (41.25,{'x':90,'y':0,'z':0}),
                   (40,{'x':0,'y':0,'z':-90}),
                   (40,None)]
            for x,rotation in moves:
                snapshot=inspect_project(str(project))
                args={'model_uuid':uid,'center_mm':{'x':x,'y':30,'z':-15},
                      'project_sha256':snapshot['sha256']}
                if rotation is not None:
                    args['world_rotation_deg']=rotation
                placed=await call('set_studio_model_placement',args)
                assert placed['verified'] and placed['unrelated_inputs_unchanged']
            # Changing Z alone must persist; XY/rotation success cannot stand in for it.
            for z in (-14,-15):
                snapshot=inspect_project(str(project))
                placed=await call('set_studio_model_placement',{'model_uuid':uid,
                    'center_mm':{'x':40,'y':30,'z':z},'project_sha256':snapshot['sha256']})
                assert placed['verified'] and placed['native_result']['center_anchors_checked']
                assert (await call('open_studio_project',{'path':str(project)}))['opened']
                saved_center=inspect_project(str(project))['models_3d'][0]['meshes'][0]['center_after_node_transform_mm']
                assert abs(saved_center['z']-z)<1e-4, saved_center
            stale=inspect_project(str(project))
            await call('set_studio_model_placement',{'model_uuid':uid,
                'center_mm':{'x':40,'y':30,'z':-15},'project_sha256':'0'*64},expect_error=True)
            assert inspect_project(str(project))['sha256']==stale['sha256']
            assert (await call('open_studio_project',{'path':str(project)}))['opened']
            final=await call('inspect_studio_project',{'path':str(project)})
            center=final['models_3d'][0]['meshes'][0]['center_after_node_transform_mm']
            assert all(abs(center[k]-v)<1e-4 for k,v in {'x':40,'y':30,'z':-15}.items())
            report.update({'placement_survives_reopen':True,'stale_request_rejected':True,
                           'source_unchanged':hashlib.sha256(source.read_bytes()).hexdigest()==original['sha256'],
                           'verified':True})
            assert report['source_unchanged']
            (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='calls'},ensure_ascii=False))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--output-directory',type=Path,required=True)
    args=parser.parse_args()
    anyio.run(run,args.project,args.output_directory)
