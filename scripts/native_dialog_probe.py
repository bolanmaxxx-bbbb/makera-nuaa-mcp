"""Read a current Qt modal using native getters only. No input injection."""
import json
import sys
import frida

process = next(p for p in frida.get_local_device().enumerate_processes() if p.name.lower() == "makerastudio.exe")
session = frida.attach(process.pid)
try:
    script = session.create_script(r'''
    const c=Process.getModuleByName('Qt5Core.dll'),w=Process.getModuleByName('Qt5Widgets.dll');
    function f(m,p,r,a){const es=m.enumerateExports().filter(e=>e.name.startsWith(p));if(es.length!==1)throw new Error(p);return new NativeFunction(es[0].address,r,a);}
    const active=f(w,'?activeModalWidget@QApplication@@','pointer',[]);
    const children=f(c,'?children@QObject@@','pointer',['pointer']);
    const cname=f(c,'?className@QMetaObject@@','pointer',['pointer']);
    const title=f(w,'?windowTitle@QWidget@@','pointer',['pointer','pointer']);
    const label=f(w,'?text@QLabel@@','pointer',['pointer','pointer']);
    const button=f(w,'?text@QAbstractButton@@','pointer',['pointer','pointer']);
    const inherit=f(c,'?inherits@QObject@@','bool',['pointer','pointer']);
    const dtor=f(c,'??1QString@@','void',['pointer']);
    function text(o,g){let s=Memory.alloc(8);g(o,s);try{let d=s.readPointer();return d.add(d.add(16).readS64().toNumber()).readUtf16String(d.add(4).readS32());}finally{dtor(s);}}
    function cls(o){let meta=new NativeFunction(o.readPointer().readPointer(),'pointer',['pointer'])(o);return cname(meta).readUtf8String();}
    rpc.exports={read(){let o=active();if(o.isNull())return {modal:false};let result={modal:true,pointer:o.toString(),type:cls(o),title:text(o,title),items:[]};
      function visit(p,depth){if(depth>8)return;let type=cls(p);if(inherit(p,Memory.allocUtf8String('QLabel')))result.items.push({type,text:text(p,label)});else if(inherit(p,Memory.allocUtf8String('QAbstractButton')))result.items.push({type,text:text(p,button),pointer:p.toString()});let d=children(p).readPointer(),b=d.add(8).readS32(),e=d.add(12).readS32();for(let i=b;i<e;i++)visit(d.add(16+i*8).readPointer(),depth+1);}visit(o,0);return result;},
      discardKnownTemporary(){
        const state=this.read();
        if(!state.modal||state.type!=='MyMessageBox'||!state.items.some(i=>i.text==='是否保存当前项目？'))throw new Error('Unexpected prompt');
        const buttons=state.items.filter(i=>i.text==='不保存');if(buttons.length!==1)throw new Error('Expected exact discard action');
        const invoke=f(c,'?invokeMethod@QMetaObject@@SA_NPEAVQObject@@PEBDW4ConnectionType@Qt@@VQGenericArgument@@','bool',['pointer','pointer','int',...Array(10).fill('pointer')]);
        const args=Array.from({length:10},()=>{let a=Memory.alloc(16);a.writePointer(NULL);a.add(8).writePointer(NULL);return a;});
        return {queued:invoke(ptr(buttons[0].pointer),Memory.allocUtf8String('click'),2,...args)};
      }};
    ''')
    script.load()
    print(json.dumps(script.exports_sync.read(),ensure_ascii=False,indent=2),flush=True)
    if '--discard-known-temporary' in sys.argv:
        print(json.dumps(script.exports_sync.discard_known_temporary()),flush=True)
finally:
    session.detach()
