"""Real STDIO MCP regression with generated rectangle/circle edges; no device calls."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import sys
import zipfile

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from makera_mcp.project import inspect_project

ROOT = Path(__file__).resolve().parents[1]


def chamfer_motion_evidence(project, op, geometry, shape):
    """Compare saved tip motion with analytic V/edge offsets; not a swept simulation."""
    matrix = geometry['node_matrix_column_major']
    if any(abs(matrix[r+4*c]-(r==c))>1e-5 for r in range(3) for c in range(3)):
        raise ValueError('Analytic fixture requires a translation-only SVG transform')
    b = geometry['bounds_in_serialized_coordinates_mm']
    x0,x1 = (v+matrix[12] for v in b['x']); y0,y1 = (v+matrix[13] for v in b['y'])
    tool,settings = op['tools'][0],op['chamfer_settings']
    offset = tool['tipDiameter']/2+settings['chamferTipOffset']*math.tan(math.radians(tool['angle']/2))
    signed = -offset if settings['contourPosition']==1 else offset
    expected_z = -op['depth']['startDepth']-op['depth']['endDepth']-settings['chamferTipOffset']
    checked,matched,errors,under_depth,min_z = 0,0,[],0,float('inf')
    with zipfile.ZipFile(project) as z: scene = z.read('makera.bin')
    for mesh in op['toolpath_meshes']:
        marker = struct.pack('<I',14)+b'gcode_polyline'+struct.pack('<I',36)+mesh['uuid'].encode('ascii')
        end = scene.index(marker)+len(marker); size = struct.unpack_from('<I',scene,end+98)[0]
        for x,y,z in struct.iter_unpack('<fff',scene[end+102:end+102+size]):
            min_z = min(min_z,z)
            under_depth += z < expected_z-.0005
            if abs(z-expected_z)>.0005: continue
            if shape=='circle': distance = math.hypot(x-(x0+x1)/2,y-(y0+y1)/2)-(x1-x0)/2
            else:
                dx,dy = max(x0-x,0,x-x1),max(y0-y,0,y-y1)
                distance = math.hypot(dx,dy) if dx or dy else max(x0-x,x-x1,y0-y,y-y1)
            error = abs(distance-signed)
            checked += 1; matched += error<.025; errors.append(error)
    return {'floor_vertices_checked':checked,'expected_tip_z_mm':expected_z,'expected_signed_edge_offset_mm':signed,
            'edge_match_fraction':matched/max(1,checked),'max_edge_error_mm':max(errors,default=None),
            'min_tip_z_mm':min_z if math.isfinite(min_z) else None,'under_depth_vertices':under_depth,
            'scope':'saved V-tool tip vertices at final depth against analytic edge offsets; not collision/stock-removal simulation'}


async def run(source,output,group,name,material):
    source,output = source.resolve(strict=True),output.resolve()
    if output.exists() or not output.is_relative_to((ROOT/'cases').resolve()):
        raise ValueError('Use a new output directory under cases/')
    initial = inspect_project(str(source))
    if initial['operations'] or initial['models_3d']:
        raise ValueError('Supply an operation-free/model-free MKS; source will be preserved')
    output.mkdir(parents=True)
    project = output/'倒角试算.mks'; shutil.copy2(source,project)
    svg = output/'edges.svg'
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="16mm" viewBox="0 0 20 16">'
        '<rect x="4" y="3" width="12" height="10" fill="none" stroke="black"/>'
        '<circle cx="10" cy="8" r="3" fill="none" stroke="black"/></svg>',encoding='utf-8')
    report = {'cases':[],'calls':[],'verified':False,'machine_commands_sent':False,
        'simulation_verified':False,'nc_export_verified':False,'machining_ready':False}
    def save_report():
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    env = {**os.environ,'PYTHONIOENCODING':'utf-8','MAKERA_CAMERA_URL':'','MAKERA_MCP_CONFIG':str(output/'no-camera.json')}
    params = StdioServerParameters(command=sys.executable,args=['-m','makera_mcp.server'],cwd=str(ROOT),env=env)
    async with stdio_client(params) as (reader,writer):
        async with ClientSession(reader,writer) as client:
            await client.initialize()
            async def call(tool,args=None,expect_error=False):
                raw = await client.call_tool(tool,args or {})
                body = next(b.text for b in raw.content if b.type=='text')
                try: result = json.loads(body)
                except json.JSONDecodeError: result = {'message':body}
                report['calls'].append({'tool':tool,'arguments':args or {},'isError':bool(raw.isError),'result':result}); save_report()
                if bool(raw.isError)!=expect_error: raise RuntimeError(tool+': '+str(result)[:500])
                return result
            report['version'] = (await call('get_capabilities'))['version']
            report['tool_count'] = len((await client.list_tools()).tools)
            assert (await call('open_studio_project',{'path':str(project)}))['opened']
            assert (await call('configure_studio_stock',{'specification':{'shape':'box','dimensions_mm':{'x':40,'y':40,'z':3}}}))['verified']
            imported = await call('import_studio_model',{'path':str(svg),'units':'mm'})
            assert imported['imported'] and len(imported['new_svg_geometry'])==2
            geoms = sorted(imported['new_svg_geometry'],key=lambda g:g['vertex_count'])
            for shape,geom in zip(('rectangle','circle'),geoms):
                for side in ('inside','outside'):
                    created = await call('create_2d_chamfer',{'geometry_uuids':[geom['uuid']],'chamfer_depth_mm':.3})
                    assert created['created_and_saved'],created
                    index = created['operation_index']
                    assert (await call('select_studio_tool',{'operation_index':index,'group':group,'name':name,'material_profile':material}))['verified']
                    snapshot = inspect_project(str(project))
                    args = {'operation_index':index,'start_depth_mm':0,'chamfer_depth_mm':.3,'tip_offset_mm':.2,
                        'side':side,'direction':'climb','project_sha256':snapshot['sha256']}
                    await call('configure_studio_2d_chamfer',{**args,'project_sha256':'0'*64},True)
                    assert inspect_project(str(project))['sha256']==snapshot['sha256']
                    assert (await call('configure_studio_2d_chamfer',args))['verified']
                    calculated = await call('generate_studio_toolpath',{'operation_index':index})
                    if calculated.get('outcome')=='chamfer_saved_but_tool_parameters_need_reselection':
                        assert calculated['chamfer_settings_saved_verified']
                        assert (await call('select_studio_tool',{'operation_index':index,'group':group,'name':name,'material_profile':material}))['verified']
                        calculated = await call('generate_studio_toolpath',{'operation_index':index})
                    assert calculated['verified'],calculated
                    evidence = chamfer_motion_evidence(project,calculated['operation'],geom,shape)
                    assert evidence['floor_vertices_checked']>=4 and evidence['edge_match_fraction']>.95 and evidence['under_depth_vertices']==0,evidence
                    report['cases'].append({'shape':shape,'side':side,'operation_index':index,'motion_evidence':evidence})
            snapshot = inspect_project(str(project)); old_ids = [m['uuid'] for m in snapshot['operations'][-1]['toolpath_meshes']]
            args = {'operation_index':3,'start_depth_mm':.1,'chamfer_depth_mm':.35,'tip_offset_mm':.15,
                'side':'outside','direction':'conventional','project_sha256':snapshot['sha256']}
            assert (await call('configure_studio_2d_chamfer',args))['verified']
            regenerated = await call('generate_studio_toolpath',{'operation_index':3})
            if regenerated.get('outcome')=='chamfer_saved_but_tool_parameters_need_reselection':
                assert (await call('select_studio_tool',{'operation_index':3,'group':group,'name':name,'material_profile':material}))['verified']
                regenerated = await call('generate_studio_toolpath',{'operation_index':3})
            assert regenerated['verified'] and [m['uuid'] for m in regenerated['operation']['toolpath_meshes']]!=old_ids
            evidence = chamfer_motion_evidence(project,regenerated['operation'],geoms[1],'circle')
            assert evidence['floor_vertices_checked']>=4 and evidence['edge_match_fraction']>.95 and evidence['under_depth_vertices']==0,evidence
            report['revision_evidence'] = evidence
            assert (await call('open_studio_project',{'path':str(project)}))['opened']
            reopened = inspect_project(str(project))
            assert len(reopened['operations'])==4 and all(op['calculation_evidence_verified'] for op in reopened['operations'])
            report.update(verified=True,save_reopen_verified=True,source_unchanged=hashlib.sha256(source.read_bytes()).hexdigest()==initial['sha256'])
            assert report['source_unchanged']; save_report()
    print(json.dumps({k:v for k,v in report.items() if k!='calls'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,type=Path)
    parser.add_argument('--output-directory',required=True,type=Path)
    parser.add_argument('--tool-group',required=True)
    parser.add_argument('--tool-name',required=True)
    parser.add_argument('--material-profile',required=True)
    args = parser.parse_args()
    anyio.run(run,args.project,args.output_directory,args.tool_group,args.tool_name,args.material_profile)
