"""Developer-only native creation audit of every chooser type; not toolpath validation."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from makera_mcp.config import Settings
from makera_mcp.native import NativeStudio
from makera_mcp.operation_catalog import OPERATIONS
from makera_mcp.project import inspect_project, project_paths


def run(source, output):
    settings=Settings.load();source=source.resolve(strict=True);output=output.resolve()
    if output.exists() or not output.is_relative_to((settings.root/'cases').resolve()):
        raise ValueError('Use a new output directory inside cases/')
    initial=inspect_project(str(source))
    if initial['operations']:
        raise ValueError('Use a source project with no operations; do not overwrite originals')
    output.mkdir(parents=True)
    studio=NativeStudio(settings)
    report={'operations':[],'calculation_verified':False,'simulation_verified':False,
            'machine_commands_sent':False,'scope':'empty native operation creation/save only'}
    try:
        for index,(native_type,ident,label,*_) in enumerate(OPERATIONS,1):
            project=output/f'工序分类{index:02d}.mks';shutil.copy2(source,project)
            studio.open_project(str(project))
            studio._call('open_path_chooser');studio._wait_modal('AddKnifeTypeDlg')
            studio._call('choose_path_type',label);studio._wait_modal(None);studio._call('barrier')
            saved=studio.save_project()
            paths=project_paths(str(project))
            assert saved['saved'] and len(paths)==1 and paths[0]['type']==native_type
            assert not paths[0].get('gcodemeshuuidmap'), 'A created scaffold is not a calculated toolpath'
            report['operations'].append({'id':ident,'native_type':native_type,'native_label':label,
                'created_saved_verified':True,'calculation_verified':False})
            (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        report['source_unchanged']=hashlib.sha256(source.read_bytes()).hexdigest()==initial['sha256']
        assert report['source_unchanged']
        report['native_inventory_verified']=True
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:studio.close()
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',type=Path,required=True)
    p.add_argument('--output-directory',type=Path,required=True)
    a=p.parse_args();run(a.project,a.output_directory)
