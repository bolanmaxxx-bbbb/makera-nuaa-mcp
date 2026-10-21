"""Real MCP regression on generated slope/curved solids. No device commands."""
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


def height(kind, x, y):
    return 2 + x/16 if kind == 'slope' else 2 + 1.2*math.sin(math.pi*x/16)*math.sin(math.pi*y/12)


def make_solid(folder, kind):
    nx,ny=24,18
    top=[(16*i/nx,12*j/ny,height(kind,16*i/nx,12*j/ny)) for j in range(ny+1) for i in range(nx+1)]
    faces=[]
    for j in range(ny):
        for i in range(nx):
            a=j*(nx+1)+i;b=a+1;c=a+nx+1;d=c+1
            faces.extend(((top[a],top[b],top[d]),(top[a],top[d],top[c])))
    rim=([top[i] for i in range(nx+1)]
         +[top[j*(nx+1)+nx] for j in range(1,ny+1)]
         +[top[ny*(nx+1)+i] for i in range(nx-1,-1,-1)]
         +[top[j*(nx+1)] for j in range(ny-1,0,-1)])
    for a,b in zip(rim,rim[1:]+rim[:1]):
        c=(a[0],a[1],0);d=(b[0],b[1],0)
        faces.extend(((a,c,d),(a,d,b),((8,6,0),d,c)))
    lines=['solid synthetic_'+kind]
    for face in faces:
        lines+=['facet normal 0 0 0','outer loop']+['vertex '+' '.join(map(str,v)) for v in face]+['endloop','endfacet']
    lines+=['endsolid synthetic_'+kind]
    path=folder/(kind+'.stl');path.write_text('\n'.join(lines),encoding='ascii')
    return path,max(v[2] for v in top)


def surface_evidence(project, operation, kind):
    with zipfile.ZipFile(project) as z:scene=z.read('makera.bin')
    checked,changed,undercuts=0,0,0
    for mesh in operation['toolpath_meshes']:
        marker=struct.pack('<I',14)+b'gcode_polyline'+struct.pack('<I',36)+mesh['uuid'].encode('ascii')
        # The kind string is 14 bytes in the verified layout.
        offset=scene.index(marker)+len(marker)
        size=struct.unpack_from('<I',scene,offset+98)[0]
        points=list(struct.iter_unpack('<fff',scene[offset+102:offset+102+size]))
        previous=None
        for x,y,z in points:
            if 14<x<26 and 16<y<24:
                checked+=1
                # Solid base is at stock Z=-6 and local XY center is (20,20).
                if z < -6+height(kind,x-12,y-14)-0.04:undercuts+=1
                if previous and abs(z-previous[2])>1e-5 and math.hypot(x-previous[0],y-previous[1])>1e-5:
                    changed+=1
                previous=(x,y,z)
            else:previous=None
    return {'interior_vertices_checked':checked,'interior_xyz_varying_segments':changed,
            'sampled_vertex_undercuts':undercuts,'scope':'sampled flat-tool tip vertices against analytic surface; not swept-volume simulation'}


async def run(source,output,group,material):
    source=source.resolve(strict=True);output=output.resolve()
    if output.exists() or not output.is_relative_to((ROOT/'cases').resolve()):
        raise ValueError('Use a new output directory under cases/')
    baseline=inspect_project(str(source))
    if baseline['operations'] or baseline['models_3d']:
        raise ValueError('Supply a geometry-only MKS without paths or 3D models; source is preserved')
    output.mkdir(parents=True)
    report={'cases':[],'calls':[],'machine_commands_sent':False,'machining_ready':False,
            'simulation_verified':False,'nc_export_verified':False}
    env={**os.environ,'PYTHONIOENCODING':'utf-8','MAKERA_CAMERA_URL':'','MAKERA_MCP_CONFIG':str(output/'no-camera.json')}
    params=StdioServerParameters(command=sys.executable,args=['-m','makera_mcp.server'],cwd=str(ROOT),env=env)
    async with stdio_client(params) as (reader,writer):
        async with ClientSession(reader,writer) as client:
            await client.initialize()
            async def call(name,args=None,expect_error=False):
                raw=await client.call_tool(name,args or {})
                body=next(b.text for b in raw.content if b.type=='text')
                try:value=json.loads(body)
                except json.JSONDecodeError:value={'message':body}
                report['calls'].append({'tool':name,'arguments':args or {},'isError':bool(raw.isError),'result':value})
                (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                if bool(raw.isError)!=expect_error:raise RuntimeError(name+': '+str(value)[:400])
                return value
            report['version']=(await call('get_capabilities'))['version']
            report['tool_count']=len((await client.list_tools()).tools)
            for kind in ('slope','curved'):
                folder=output/kind;folder.mkdir()
                project=folder/('斜面试算.mks' if kind=='slope' else '曲面试算.mks')
                shutil.copy2(source,project)
                stl,h=make_solid(folder,kind)
                await call('open_studio_project',{'path':str(project)})
                catalog=await call('get_studio_operation_catalog')
                assert len(catalog['operations'])==14 and not catalog['unknown_native_labels']
                assert all(x['available_in_native_chooser'] for x in catalog['operations'])
                await call('configure_studio_stock',{'specification':{'shape':'box','dimensions_mm':{'x':40,'y':40,'z':20}}})
                imported=await call('import_studio_model',{'path':str(stl),'units':'mm'})
                uid=imported['new_models_3d'][0]['meshes'][0]['uuid']
                current=inspect_project(str(project))
                matrix=current['models_3d'][0]['meshes'][0]['matrix_column_major']
                assert all(abs(matrix[r+4*c]-(r==c))<1e-4 for r in range(3) for c in range(3)), 'Fixture orientation must be verified before applying its analytic surface equation'
                await call('set_studio_model_placement',{'model_uuid':uid,'center_mm':{'x':20,'y':20,'z':-6+h/2},
                    'project_sha256':current['sha256']})
                current=inspect_project(str(project));matrix=current['models_3d'][0]['meshes'][0]['matrix_column_major']
                assert all(abs(matrix[r+4*c]-(r==c))<1e-4 for r in range(3) for c in range(3)), matrix
                await call('configure_studio_stock',{'specification':{'shape':'box','dimensions_mm':{'x':40,'y':40,'z':6}}})
                for layered,allowance in ((True,.2),(False,0)):
                    snapshot=inspect_project(str(project))
                    created=await call('create_3d_relief',{'model_uuid':uid,'layered':layered,'allowance_mm':allowance,'project_sha256':snapshot['sha256']})
                    assert created['created_and_saved']
                    index=created['operation_index']
                    await call('register_studio_tool',{'operation_index':index,'group':group,
                        'specification':{'kind':'endmill','name':'Synthetic relief flat 2mm','diameter':2,'shank_diameter':3.175,'flute_length':10},
                        'material_profile':material,'parameters':{'spindle_speed':10000,'feedrate':120,'plunge_feedrate':40,
                        'step_down':.5,'step_over_mm':.2 if not layered else .5}})
                    assert (await call('select_studio_tool',{'operation_index':index,'group':group,'name':'Synthetic relief flat 2mm','material_profile':material}))['verified']
                    calculated=await call('generate_studio_toolpath',{'operation_index':index})
                    assert calculated['verified'], calculated
                current=inspect_project(str(project))
                await call('configure_studio_3d_relief',{'operation_index':1,'layered':False,'allowance_mm':.1,'project_sha256':'0'*64},True)
                assert inspect_project(str(project))['sha256']==current['sha256']
                await call('open_studio_project',{'path':str(project)})
                saved=inspect_project(str(project))
                assert all(o['calculation_evidence_verified'] for o in saved['operations'])
                evidence=surface_evidence(project,saved['operations'][1],kind)
                assert evidence['interior_vertices_checked']>100 and evidence['interior_xyz_varying_segments']>100, evidence
                assert evidence['sampled_vertex_undercuts']==0, evidence
                report['cases'].append({'kind':kind,'project':str(project),'rough_and_surface_saved_reopened':True,
                    'rough_vertices':sum(m['vertex_count'] for m in saved['operations'][0]['toolpath_meshes']),
                    'surface_vertices':sum(m['vertex_count'] for m in saved['operations'][1]['toolpath_meshes']),
                    'surface_evidence':evidence})
            report['source_unchanged']=hashlib.sha256(source.read_bytes()).hexdigest()==baseline['sha256']
            assert report['source_unchanged']
            report['verified']=True
            (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='calls'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',type=Path,required=True)
    p.add_argument('--output-directory',type=Path,required=True)
    p.add_argument('--tool-group',required=True)
    p.add_argument('--material-profile',required=True)
    a=p.parse_args()
    anyio.run(run,a.project,a.output_directory,a.tool_group,a.material_profile)
