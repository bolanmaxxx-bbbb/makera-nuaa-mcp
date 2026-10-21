"""Real MCP project import/stock validation using generated, non-machining fixtures."""
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

ROOT = Path(__file__).resolve().parents[1]


def make_fixtures(folder):
    vertices=[(0,0,0),(8,0,0),(8,6,0),(0,6,0),(0,0,4),(8,0,4),(8,6,4),(0,6,4)]
    faces=[(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),(1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)]
    lines=['solid fixture']
    for face in faces:
        lines+=['facet normal 0 0 0','outer loop']+['vertex '+' '.join(map(str,vertices[i])) for i in face]+['endloop','endfacet']
    lines+=['endsolid fixture']
    stl=folder/'fixture_block.stl';stl.write_text('\n'.join(lines),encoding='ascii')
    svg=folder/'fixture_vectors.svg'
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="20mm" viewBox="0 0 20 20"><rect x="2" y="3" width="5" height="4" fill="none" stroke="black"/><circle cx="13" cy="12" r="2" fill="none" stroke="black"/></svg>',encoding='utf-8')
    return stl,svg


async def run(source, output):
    source=source.resolve(strict=True);output=output.resolve()
    if not output.is_relative_to((ROOT/'cases').resolve()) or output.exists():
        raise ValueError('Use a NEW output directory inside this repository cases/')
    if inspect_project(str(source))['operations']:
        raise ValueError('Source must be a geometry-only MKS, without operations')
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    output.mkdir(parents=True)
    project=output/'project_setup.mks';shutil.copy2(source,project)
    stl,svg=make_fixtures(output)
    report={'scope':'isolated project setup validation','calls':[],
            'machine_commands_sent':False,'machining_ready':False}
    parameters=StdioServerParameters(command=sys.executable,args=['-m','makera_mcp.server'],cwd=str(ROOT),
        env={**os.environ,'PYTHONIOENCODING':'utf-8','MAKERA_CAMERA_URL':'',
             'MAKERA_MCP_CONFIG':str(output/'camera-not-configured.json')})
    async with stdio_client(parameters) as (reader,writer):
        async with ClientSession(reader,writer) as client:
            await client.initialize()
            async def call(name,arguments=None):
                raw=await client.call_tool(name,arguments or {})
                value=json.loads(next(block.text for block in raw.content if block.type=='text'))
                report['calls'].append({'tool':name,'arguments':arguments or {},'isError':bool(raw.isError),'result':value})
                (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                if raw.isError:raise RuntimeError(name+': '+str(value)[:240])
                return value
            caps=await call('get_capabilities')
            names={t.name for t in (await client.list_tools()).tools}
            assert {'import_studio_model','get_studio_stock_options','configure_studio_stock'}<=names
            opened=await call('open_studio_project',{'path':str(project)});assert opened['opened']
            options=await call('get_studio_stock_options')
            category=options['cmbMaterial']['value'];material=options['cmbMaterial_2']['value']
            config={'shape':'box','dimensions_mm':{'x':40,'y':30,'z':6},'material_category':category,'material_name':material}
            assert (await call('configure_studio_stock',{'specification':config}))['verified']
            presets=options['cmbMaterial_3']['options']
            if len(presets)>1:
                assert (await call('configure_studio_stock',{'specification':{'material_category':category,'material_name':material,'stock_specification':presets[1]}}))['verified']
                report['catalog_preset_verified']=True
            assert (await call('configure_studio_stock',{'specification':{'shape':'cylinder','dimensions_mm':{'diameter':20,'z':8}}}))['verified']
            assert (await call('configure_studio_stock',{'specification':config}))['verified']
            rollback=await call('configure_studio_stock',{'specification':{'material_category':category,'material_name':'__MCP_MISSING_MATERIAL__'}})
            assert not rollback['verified'] and rollback['rollback_saved_verified']
            stl_import=await call('import_studio_model',{'path':str(stl),'units':'mm'});assert stl_import['imported'] and stl_import['saved_verified']
            mesh=stl_import['new_models_3d'][0]['meshes'][0]
            assert all(abs(mesh['dimensions_mm'][axis]-value)<1e-5 for axis,value in zip('xyz',(8,6,4)))
            svg_import=await call('import_studio_model',{'path':str(svg),'units':'mm'});assert svg_import['imported'] and len(svg_import['new_svg_geometry'])==2
            vector_sizes=sorted((round(v['bounds_in_serialized_coordinates_mm']['x'][1]-v['bounds_in_serialized_coordinates_mm']['x'][0],4),round(v['bounds_in_serialized_coordinates_mm']['y'][1]-v['bounds_in_serialized_coordinates_mm']['y'][0],4)) for v in svg_import['new_svg_geometry'])
            assert vector_sizes==[(4,4),(5,4)]
            duplicate=await call('import_studio_model',{'path':str(stl),'units':'mm'});assert duplicate['outcome']=='already_present'
            assert (await call('save_studio_project'))['saved']
            report['version']=caps['version'];report['source_unchanged']=hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
            assert report['source_unchanged']
            report['verified']=True
            (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'verified':True,'version':report['version'],'calls':len(report['calls']),
        'stl_dimensions_mm':[8,6,4],'new_svg_shapes':2,'source_unchanged':True,
        'report':str(output/'report.json'),'machine_commands_sent':False},ensure_ascii=False))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--output-directory',type=Path,required=True)
    args=parser.parse_args()
    anyio.run(run,args.project,args.output_directory)
