"""Read-only Qt ABI/IPC probe. No Computer Use, input injection or machine commands."""
import json
from pathlib import Path

import frida

device = frida.get_local_device()
matches = [process for process in device.enumerate_processes() if process.name.lower() == "makerastudio.exe"]
if len(matches) != 1:
    raise RuntimeError(f"Expected one MakeraStudio process, found {len(matches)}")
session = device.attach(matches[0].pid)
try:
    script = session.create_script(r'''
    const core = Process.getModuleByName('Qt5Core.dll');
    const widgets = Process.getModuleByName('Qt5Widgets.dll');
    rpc.exports = {
      abi() {
        const wanted = ['?objectName@QObject@@', '?children@QObject@@', '?className@QMetaObject@@',
          '?methodCount@QMetaObject@@', '?method@QMetaObject@@', '?methodSignature@QMetaMethod@@',
          '?invokeMethod@QMetaObject@@', '?fromUtf8@QString@@', '??1QString@@', '??1QByteArray@@',
          '?methodType@QMetaMethod@@', '?methodOffset@QMetaObject@@', '?topLevelWidgets@QApplication@@'];
        const exports = [...core.enumerateExports(), ...widgets.enumerateExports()]
          .filter(e=>wanted.some(w=>e.name.startsWith(w)));
        return exports.map(e=>{
          let code=[];
          if(e.type==='function' && /objectName@QObject|topLevelWidgets|method@QMetaObject/.test(e.name)) {
            let pc=e.address;
            for(let i=0;i<12;i++){let instruction=Instruction.parse(pc);code.push(instruction.toString());pc=instruction.next;}
          }
          return {name:e.name,address:e.address.toString(),code};
        });
      }
    };
    ''')
    script.load()
    result = {"pid": matches[0].pid, "exports": script.exports_sync.abi()}
    path = Path(__file__).resolve().parents[1] / "runtime" / "native-abi-probe.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
finally:
    session.detach()
