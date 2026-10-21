'use strict';
// Native Qt adapter. No screen reading, accessibility, mouse or keyboard APIs.
const core = Process.getModuleByName('Qt5Core.dll');
const widgets = Process.getModuleByName('Qt5Widgets.dll');
function exp(module, prefix) {
  const matches=module.enumerateExports().filter(e=>e.name.startsWith(prefix));
  if(matches.length!==1) throw new Error('Expected unique Qt export: '+prefix);
  return matches[0].address;
}
function fn(module,prefix,ret,args) {return new NativeFunction(exp(module,prefix),ret,args);}
const findWidget=fn(widgets,'?find@QWidget@@','pointer',['pointer']);
const objectName=fn(core,'?objectName@QObject@@','pointer',['pointer','pointer']);
const destroyQString=fn(core,'??1QString@@','void',['pointer']);
const childrenOf=fn(core,'?children@QObject@@','pointer',['pointer']);
const parentOf=fn(core,'?parent@QObject@@','pointer',['pointer']);
const className=fn(core,'?className@QMetaObject@@','pointer',['pointer']);
const methodCount=fn(core,'?methodCount@QMetaObject@@','int',['pointer']);
const methodOffset=fn(core,'?methodOffset@QMetaObject@@','int',['pointer']);
const metaMethod=fn(core,'?method@QMetaObject@@','pointer',['pointer','pointer','int']);
const signature=fn(core,'?methodSignature@QMetaMethod@@','pointer',['pointer','pointer']);
const methodType=fn(core,'?methodType@QMetaMethod@@','int',['pointer']);
const destroyBytes=fn(core,'??1QByteArray@@','void',['pointer']);
const inherits=fn(core,'?inherits@QObject@@','bool',['pointer','pointer']);
const visible=fn(widgets,'?isVisible@QWidget@@','bool',['pointer']);
const enabled=fn(widgets,'?isEnabled@QWidget@@','bool',['pointer']);
const spinValue=fn(widgets,'?value@QDoubleSpinBox@@','double',['pointer']);
const spinMin=fn(widgets,'?minimum@QDoubleSpinBox@@','double',['pointer']);
const spinMax=fn(widgets,'?maximum@QDoubleSpinBox@@','double',['pointer']);
const spinDecimals=fn(widgets,'?decimals@QDoubleSpinBox@@','int',['pointer']);
const labelText=fn(widgets,'?text@QLabel@@','pointer',['pointer','pointer']);
const lineText=fn(widgets,'?text@QLineEdit@@','pointer',['pointer','pointer']);
const comboText=fn(widgets,'?currentText@QComboBox@@','pointer',['pointer','pointer']);
const comboIndex=fn(widgets,'?currentIndex@QComboBox@@','int',['pointer']);
const comboCount=fn(widgets,'?count@QComboBox@@','int',['pointer']);
const comboItemText=fn(widgets,'?itemText@QComboBox@@','pointer',['pointer','pointer','int']);
const tableRows=fn(widgets,'?rowCount@QTableWidget@@QEBA','int',['pointer']);
const tableColumns=fn(widgets,'?columnCount@QTableWidget@@QEBA','int',['pointer']);
const tableItem=fn(widgets,'?item@QTableWidget@@','pointer',['pointer','int','int']);
const tableItemText=fn(widgets,'?text@QTableWidgetItem@@','pointer',['pointer','pointer']);
const tableVerticalHeader=fn(widgets,'?verticalHeaderItem@QTableWidget@@','pointer',['pointer','int']);
const tableSetCurrent=fn(widgets,'?setCurrentCell@QTableWidget@@QEAAXHH@Z','void',['pointer','int','int']);
const treeRoot=fn(widgets,'?invisibleRootItem@QTreeWidget@@','pointer',['pointer']);
const treeChild=fn(widgets,'?child@QTreeWidgetItem@@','pointer',['pointer','int']);
const treeChildCount=fn(widgets,'?childCount@QTreeWidgetItem@@','int',['pointer']);
const treeItemText=fn(widgets,'?text@QTreeWidgetItem@@','pointer',['pointer','pointer','int']);
const treeSetCurrent=fn(widgets,'?setCurrentItem@QTreeWidget@@QEAAXPEAVQTreeWidgetItem@@@Z','void',['pointer','pointer']);
const listCount=fn(widgets,'?count@QListWidget@@','int',['pointer']);
const listItem=fn(widgets,'?item@QListWidget@@','pointer',['pointer','int']);
const listItemText=fn(widgets,'?text@QListWidgetItem@@','pointer',['pointer','pointer']);
const listSetCurrent=fn(widgets,'?setCurrentRow@QListWidget@@QEAAXH@Z','void',['pointer','int']);
const titleText=fn(widgets,'?windowTitle@QWidget@@','pointer',['pointer','pointer']);
const buttonText=fn(widgets,'?text@QAbstractButton@@','pointer',['pointer','pointer']);
const activeModal=fn(widgets,'?activeModalWidget@QApplication@@','pointer',[]);
const stringFromUtf8=fn(core,'?fromUtf8@QString@@SA?AV1@PEBDH@Z','pointer',['pointer','pointer','int']);
const fileDialogSelect=fn(widgets,'?selectFile@QFileDialog@@','void',['pointer','pointer']);
const fileDialogSelected=fn(widgets,'?selectedFiles@QFileDialog@@','pointer',['pointer','pointer']);
const metaDestruct=fn(core,'?destruct@QMetaType@@SAXHPEAX@Z','void',['int','pointer']);
const invoke=fn(core,'?invokeMethod@QMetaObject@@SA_NPEAVQObject@@PEBDW4ConnectionType@Qt@@VQGenericArgument@@',
  'bool',['pointer','pointer','int',...Array(10).fill('pointer')]);
const user32=Process.getModuleByName('user32.dll');
const getWindowThread=new NativeFunction(user32.getExportByName('GetWindowThreadProcessId'),'uint',['pointer','pointer']);
const postMessage=new NativeFunction(user32.getExportByName('PostMessageW'),'bool',['pointer','uint','pointer','pointer']);
const guiJobs=[];
let inGuiJob=false;
let managedFileDialogThread=0;
// Use Qt's own file picker for this one managed import, leaving other dialogs unchanged.
const filePickerHook=Interceptor.attach(exp(widgets,'?getOpenFileNames@QFileDialog@@'),{
  onEnter(args){
    if(managedFileDialogThread===this.threadId){
      args[6]=ptr(args[6].toUInt32()|0x10); // QFileDialog::DontUseNativeDialog
      managedFileDialogThread=0;
    }
  }
});
// Run after the message polling API has returned. DispatchMessageW can remain
// on the stack for an entire nested Qt modal loop; unloading that hook is unsafe.
const pollAddresses=[user32.getExportByName('PeekMessageW')];
// Native Windows file dialogs can poll win32u directly rather than the public wrappers.
const win32u=Process.getModuleByName('win32u.dll');
for(const api of ['NtUserPeekMessage']){
  const address=win32u.findExportByName(api);if(address)pollAddresses.push(address);
}
const messageHooks=pollAddresses.map(address=>Interceptor.attach(address,{
  onLeave(){
    if(inGuiJob)return;
    const index=guiJobs.findIndex(j=>j.thread===this.threadId);
    if(index<0)return;
    const job=guiJobs.splice(index,1)[0];
    inGuiJob=true;
    // Complete RPC only after this native interception frame has returned.
    // Otherwise a short-lived client can unload the script from under the GUI thread.
    try{const result=job.work();setImmediate(()=>job.resolve(result));}
    catch(error){setImmediate(()=>job.reject(error));}finally{inGuiJob=false;}
  }
}));
function onGui(hwnd,work){
  return new Promise((resolve,reject)=>{
    const job={thread:getWindowThread(ptr(hwnd),NULL),work,resolve,reject};
    if(!job.thread){reject(new Error('Studio window disappeared'));return;}
    guiJobs.push(job);
    if(!postMessage(ptr(hwnd),0,NULL,NULL)){guiJobs.splice(guiJobs.indexOf(job),1);reject(new Error('Cannot wake Studio event loop'));return;}
    setTimeout(()=>{const index=guiJobs.indexOf(job);if(index>=0){guiJobs.splice(index,1);reject(new Error('Studio main-thread job timed out without execution'));}},5000);
  });
}
function isA(object,type){return inherits(object,Memory.allocUtf8String(type));}
function getText(object,getter){
  const result=Memory.alloc(8);getter(object,result);
  try{return readQString(result);}finally{destroyQString(result);}
}
function queueMethod(object,method,args=[],connection=2){
  const keep=[],generic=[];
  for(let i=0;i<10;i++){
    const value=Memory.alloc(16);value.writePointer(NULL);value.add(8).writePointer(NULL);
    if(i<args.length){
      const arg=args[i],type=Memory.allocUtf8String(arg.type);keep.push(type);
      let data;
      if(arg.type==='QString'){
        data=Memory.alloc(8);const utf8=Memory.allocUtf8String(arg.value);keep.push(utf8);
        stringFromUtf8(data,utf8,-1);keep.push({qstring:data});
      }else if(arg.type==='double'){data=Memory.alloc(8);data.writeDouble(arg.value);}
      else if(arg.type==='float'){data=Memory.alloc(4);data.writeFloat(arg.value);}
      else if(arg.type==='int'){data=Memory.alloc(4);data.writeS32(arg.value);}
      else if(arg.type==='bool'){data=Memory.alloc(1);data.writeU8(arg.value?1:0);}
      else if(arg.type==='QListWidgetItem*'){data=Memory.alloc(8);data.writePointer(arg.value);if(connection!==1)throw new Error('Item pointer must be used synchronously');}
      else if(arg.type==='std::vector<std::string>'){
        // MSVC x64 release ABI: vector = begin/end/capacity; string = 16-byte
        // storage, size, capacity. Only a synchronous const input view is built.
        const storage=Memory.alloc(Math.max(1,arg.value.length*32));keep.push(storage);
        arg.value.forEach((text,index)=>{
          const bytes=Memory.allocUtf8String(text),length=unescape(encodeURIComponent(text)).length;
          if(length<=15)throw new Error('Expected UUID string, not short-string storage');
          keep.push(bytes);const item=storage.add(index*32);item.writePointer(bytes);
          item.add(16).writeU64(length);item.add(24).writeU64(length);
        });
        data=Memory.alloc(24);data.writePointer(storage);data.add(8).writePointer(storage.add(arg.value.length*32));
        data.add(16).writePointer(storage.add(arg.value.length*32));
        if(connection!==1)throw new Error('Vector inputs must be copied synchronously on the GUI thread');
      }
      else if(arg.type==='QList<int>'){
        if(connection!==1||!Array.isArray(arg.value)||arg.value.length>50||arg.value.some(v=>!Number.isInteger(v)||v<0))throw new Error('Expected synchronous model IDs');
        const storage=Memory.alloc(16+8*arg.value.length);keep.push(storage);
        // Qt5 x64 static input data; inline int nodes have pointer-sized stride.
        storage.writeS32(-1);storage.add(4).writeS32(arg.value.length);storage.add(8).writeS32(0);storage.add(12).writeS32(arg.value.length);
        arg.value.forEach((v,i)=>storage.add(16+8*i).writeS32(v));
        data=Memory.alloc(8);data.writePointer(storage);
      }
      else throw new Error('Unsupported native argument type');
      keep.push(data);value.writePointer(data);value.add(8).writePointer(type);
    }
    generic.push(value);
  }
  try{return invoke(object,Memory.allocUtf8String(method),connection,...generic);}
  finally{for(const item of keep)if(item.qstring)destroyQString(item.qstring);}
}
function rootOf(hwnd){const root=findWidget(ptr(hwnd));if(root.isNull())throw new Error('No Studio QWidget for HWND');if(nameOf(root)!=='MainWindow'||classOf(root)!=='MainWindow')throw new Error('Wrong Studio root: '+nameOf(root)+' / '+classOf(root));return root;}
function readState(root){
  const objects=treeOf(root),controls=[];
  for(const item of objects){
    const object=ptr(item.pointer);
    if(!isA(object,'QWidget') || !visible(object))continue;
    if(!item.path.includes('/widgetDesign/') && !/lblDeviceState|lblProgressValue|label_(Work|Machine)Point[XYZA]|label_WorkPrincipalAxis/.test(item.name))continue;
    let value=null;
    if(isA(object,'QDoubleSpinBox'))value=spinValue(object);
    else if(isA(object,'QLabel'))value=getText(object,labelText);
    else if(isA(object,'QComboBox'))value=getText(object,comboText);
    else if(isA(object,'QLineEdit'))value=getText(object,lineText);
    else if(isA(object,'QAbstractButton'))value=getText(object,buttonText);
    controls.push({path:item.path,name:item.name,type:item.type,value,enabled:enabled(object)});
  }
  return {source:'native_qt_object_api',observed_at:new Date().toISOString(),window_title:getText(root,titleText),controls,
    controller_telemetry_age_s:null,source_freshness:'Qt properties are current; controller timestamps unknown'};
}
function metaOf(object) {return new NativeFunction(object.readPointer().readPointer(),'pointer',['pointer'])(object);}
function classOf(object) {return className(metaOf(object)).readUtf8String();}
function childObjects(object) {
  const list=childrenOf(object).readPointer();
  const begin=list.add(8).readS32(),end=list.add(12).readS32();
  if(begin<0 || end<begin || end-begin>20000) throw new Error('Invalid QObject list');
  const result=[];
  for(let i=begin;i<end;i++)result.push(list.add(16+i*8).readPointer());
  return result;
}
function methodsOf(object) {
  const meta=metaOf(object),result=[];
  for(let i=methodOffset(meta);i<methodCount(meta);i++) {
    const method=Memory.alloc(16);metaMethod(meta,method,i);
    const bytes=Memory.alloc(8);signature(method,bytes);
    try {
      const data=bytes.readPointer(),size=data.add(4).readS32();
      result.push({index:i,type:methodType(method),signature:data.add(data.add(16).readS64().toNumber()).readUtf8String(size)});
    } finally {destroyBytes(bytes);}
  }
  return result;
}
function treeOf(root,maxDepth=30) {
  const result=[];
  function visit(object,parentPath,depth) {
    if(depth>maxDepth || result.length>=8000)return;
    const name=nameOf(object),type=classOf(object);
    const objectPath=parentPath+'/'+(name || '['+type+']');
    result.push({pointer:object.toString(),name,type,path:objectPath});
    for(const child of childObjects(object))visit(child,objectPath,depth+1);
  }
  visit(root,'',0);return result;
}
function readQString(value) {
  const data=value.readPointer();
  if(data.isNull()) return '';
  const length=data.add(4).readS32();
  if(length<0 || length>100000) throw new Error('Invalid QString length');
  return data.add(data.add(16).readS64().toNumber()).readUtf16String(length);
}
function nameOf(object) {
  const result=Memory.alloc(8); objectName(object,result);
  try {return readQString(result);} finally {destroyQString(result);}
}
rpc.exports = {
  placeSingleModel(hwnd,modelId,center,rotation){return onGui(hwnd,()=>{
    if(!activeModal().isNull()||!Number.isInteger(modelId)||modelId<0||![center,rotation].every(v=>Array.isArray(v)&&v.length===3&&v.every(Number.isFinite))||rotation.filter(v=>v!==0).length>1)throw new Error('Invalid model placement');
    const objects=treeOf(rootOf(hwnd)),areas=objects.filter(o=>o.name==='widgetDesign'&&o.type==='CutterPathDesignArea'),dialogs=objects.filter(o=>o.type==='MoveDlg'),actions=objects.filter(o=>o.name==='actionMove'&&o.type==='QAction');
    if(areas.length!==1||dialogs.length!==1||actions.length!==1)throw new Error('Native model placement editor unavailable');
    const area=ptr(areas[0].pointer),dialog=ptr(dialogs[0].pointer),fields=treeOf(dialog),targets=[];
    for(const [i,axis] of ['X','Y','Z'].entries()){
      const spins=fields.filter(o=>o.name==='doubleSpinBox'&&o.path.includes('/lineEdt_Move_'+axis+'/'));
      const wrappers=fields.filter(o=>o.name==='lineEdt_Move_'+axis&&o.type==='UnitDoubleSpinBox');
      if(spins.length!==1||wrappers.length!==1)throw new Error('Model center editor field unavailable');
      const spin=ptr(spins[0].pointer),scale=10**spinDecimals(spin),value=center[i];
      if(value<spinMin(spin)||value>spinMax(spin)||Math.abs(value*scale-Math.round(value*scale))>1e-6)throw new Error('Model center outside native range/precision');
      targets.push({spin,wrapper:ptr(wrappers[0].pointer)});
    }
    // trigger() establishes Studio's native transform mode; calling the area slot
    // directly can change its cached coordinates while leaving the model unchanged.
    if(!queueMethod(ptr(actions[0].pointer),'trigger',[],1))throw new Error('Cannot enter native move mode');
    if(!queueMethod(area,'sltSelectLayerOrModel',[{type:'QList<int>',value:[modelId]},{type:'bool',value:true}],1))throw new Error('Native model selection failed');
    if(rotation.some(v=>v!==0)&&!queueMethod(area,'sltRotateObjWorld',rotation.map(value=>({type:'float',value})),1))throw new Error('Native model rotation rejected');
    if(!queueMethod(dialog,'on_radioButton_center_clicked',[],1)||!queueMethod(dialog,'on_radioButton_3D_Center_clicked',[],1))throw new Error('Cannot select model center anchor');
    const invoked=targets.map((t,i)=>queueMethod(t.spin,'setValue',[{type:'double',value:center[i]}],1));
    for(const t of targets)invoked.push(queueMethod(t.wrapper,'editingFinished',[],1));
    invoked.push(queueMethod(dialog,'sltPosChanged',[],1));
    const readback=targets.map(t=>spinValue(t.spin));
    invoked.push(queueMethod(dialog,'sltCloseWindow',[],1));
    return {invoked:invoked.every(Boolean),editor_center_mm:readback,rotation_delta_deg:rotation};
  });},
  cancelManagedFileDialog(hwnd){return onGui(hwnd,()=>{
    const modal=activeModal();if(modal.isNull()||classOf(modal)!=='QFileDialog')throw new Error('Expected file dialog');
    return {queued:queueMethod(modal,'reject',[],1)};
  });},
  selectImportFile(hwnd,path){return onGui(hwnd,()=>{
    const modal=activeModal();if(modal.isNull()||classOf(modal)!=='QFileDialog')throw new Error('Expected native file chooser');
    if(typeof path!=='string'||!path.length)throw new Error('Expected an explicit local file path');
    const value=Memory.alloc(8),utf8=Memory.allocUtf8String(path);stringFromUtf8(value,utf8,-1);
    try{fileDialogSelect(modal,value);}finally{destroyQString(value);}
    const fields=treeOf(modal).filter(o=>o.name==='fileNameEdit'&&isA(ptr(o.pointer),'QLineEdit'));
    if(fields.length!==1)throw new Error('Expected managed Qt file-name field');
    if(!queueMethod(ptr(fields[0].pointer),'setText',[{type:'QString',value:path}],1))throw new Error('File-name setter failed');
    const list=Memory.alloc(8);fileDialogSelected(modal,list);let selected=[];
    try{
      const data=list.readPointer(),begin=data.add(8).readS32(),end=data.add(12).readS32();
      if(begin<0||end<begin||end-begin>10)throw new Error('Unexpected file selection list');
      for(let i=begin;i<end;i++)selected.push(readQString(data.add(16+i*8)));
    }finally{metaDestruct(11,list);}
    const normalized=s=>s.replace(/\\/g,'/').toLowerCase();
    if(selected.length!==1||normalized(selected[0])!==normalized(path))throw new Error('Native file chooser did not select the requested path: '+JSON.stringify(selected));
    return {selected,queued:queueMethod(modal,'accept',[],1)};
  });},
  openModelImport(hwnd){return onGui(hwnd,()=>{
    const root=rootOf(hwnd);if(!activeModal().isNull())throw new Error('Resolve current dialog before import');
    const areas=treeOf(root).filter(o=>o.name==='widgetDesign'&&o.type==='CutterPathDesignArea');
    if(areas.length!==1)throw new Error('Expected one CAM design area');
    managedFileDialogThread=getWindowThread(ptr(hwnd),NULL);
    return {queued:queueMethod(ptr(areas[0].pointer),'sltOpen3DModel',[],2)};
  });},
  openStockEditor(hwnd){return onGui(hwnd,()=>{
    if(!activeModal().isNull())throw new Error('Resolve current dialog before stock editing');
    const buttons=treeOf(rootOf(hwnd)).filter(o=>o.name==='btnImgProInfoLabelsSetUp'&&o.path.includes('/PathListWidget/')&&isA(ptr(o.pointer),'QPushButton'));
    if(buttons.length!==1)throw new Error('Expected one current project stock settings action');
    return {queued:queueMethod(ptr(buttons[0].pointer),'click',[],2)};
  });},
  stockOptions(hwnd){return onGui(hwnd,()=>{
    const editors=treeOf(rootOf(hwnd)).filter(o=>o.name==='wgtCusProjectInfo'&&o.type==='ProjectBasicInfoWidget');
    if(editors.length!==1)throw new Error('Expected one project stock editor');
    const editor=ptr(editors[0].pointer),objects=treeOf(editor),result={editor_visible:visible(editor)};
    for(const name of ['cmbShape','cmbMaterial','cmbMaterial_2','cmbMaterial_3']){
      const matches=objects.filter(o=>o.name===name);if(matches.length!==1||!isA(ptr(matches[0].pointer),'QComboBox'))throw new Error('Expected stock selector '+name);
      const p=ptr(matches[0].pointer),options=[];
      for(let i=0;i<comboCount(p);i++){const text=Memory.alloc(8);comboItemText(p,text,i);try{options.push(readQString(text));}finally{destroyQString(text);}}
      result[name]={value:getText(p,comboText),options,visible:visible(p),enabled:enabled(p)};
    }
    return result;
  });},
  configureStockEditor(hwnd,spec){return onGui(hwnd,()=>{
    if(!activeModal().isNull())throw new Error('Resolve dialog before stock configuration');
    const editors=treeOf(rootOf(hwnd)).filter(o=>o.name==='wgtCusProjectInfo'&&o.type==='ProjectBasicInfoWidget');
    if(editors.length!==1)throw new Error('Expected project stock editor');
    const editor=ptr(editors[0].pointer),objects=treeOf(editor);
    const find=name=>{const a=objects.filter(o=>o.name===name);if(a.length!==1)throw new Error('Missing stock field '+name);return ptr(a[0].pointer);};
    const names={x:'dspbLength',y:'dspbWidth',z:'dspbHeight',diameter:'dspbDiameter'},targets={};
    for(const [key,name] of Object.entries(names)){
      const wrapper=find(name),spins=childObjects(wrapper).filter(p=>isA(p,'QDoubleSpinBox'));
      if(spins.length!==1)throw new Error('Unexpected stock size control');
      targets[key]={wrapper,spin:spins[0],old:spinValue(spins[0])};
      if(spec.dimensions_mm[key]!==undefined){
        const v=spec.dimensions_mm[key],scale=10**spinDecimals(spins[0]);
        if(!Number.isFinite(v)||v<=0||v<spinMin(spins[0])||v>spinMax(spins[0])||Math.abs(v*scale-Math.round(v*scale))>1e-6)throw new Error('Stock dimension outside native range/precision: '+key);
      }
    }
    const boxes=Object.fromEntries(['cmbShape','cmbMaterial','cmbMaterial_2','cmbMaterial_3'].map(name=>[name,find(name)]));
    const before=Object.fromEntries(Object.entries(boxes).map(([name,p])=>[name,{index:comboIndex(p),text:getText(p,comboText)}]));
    const exact=(box,text)=>{let rows=[];for(let i=0;i<comboCount(box);i++){const t=Memory.alloc(8);comboItemText(box,t,i);try{if(readQString(t)===text)rows.push(i);}finally{destroyQString(t);}}if(rows.length!==1)throw new Error('Stock catalog item unavailable: '+text);return rows[0];};
    const choose=(name,index,slot)=>{if(!queueMethod(boxes[name],'setCurrentIndex',[{type:'int',value:index}],1))throw new Error('Stock selector failed');if(slot&&!queueMethod(editor,slot,[{type:'int',value:index}],1))throw new Error('Stock commit failed');};
    const writeSize=(key,value)=>{const t=targets[key];if(!queueMethod(t.spin,'setValue',[{type:'double',value}],1)||!queueMethod(t.wrapper,'editingFinished',[],1))throw new Error('Stock dimension commit failed');};
    if(spec.material_category)exact(boxes.cmbMaterial,spec.material_category);
    if(spec.shape&&!['box','cylinder'].includes(spec.shape))throw new Error('Unsupported stock shape');
    try{
      if(spec.shape){
        const desired=spec.shape==='box'?1:0;
        if(comboCount(boxes.cmbShape)!==2)throw new Error('Unexpected stock shape selector');
        choose('cmbShape',desired,'on_cmbShape_activated');
      }
      if(spec.material_category){
        choose('cmbMaterial',exact(boxes.cmbMaterial,spec.material_category),'sltCmbMaterialIndexChanged');
        choose('cmbMaterial_2',exact(boxes.cmbMaterial_2,spec.material_name),'sltCmbMaterial_2IndexChanged');
      }
      if(spec.stock_specification){choose('cmbMaterial_3',exact(boxes.cmbMaterial_3,spec.stock_specification),'sltCmbMaterial_3IndexChanged');}
      else if(Object.keys(spec.dimensions_mm).length){choose('cmbMaterial_3',0,'sltCmbMaterial_3IndexChanged');}
      for(const [key,value] of Object.entries(spec.dimensions_mm))writeSize(key,value);
      return {verified:true,dimensions_mm:Object.fromEntries(Object.entries(targets).map(([key,t])=>[key,spinValue(t.spin)])),shape_index:comboIndex(boxes.cmbShape),material_category:getText(boxes.cmbMaterial,comboText),material_name:getText(boxes.cmbMaterial_2,comboText),stock_specification:getText(boxes.cmbMaterial_3,comboText)};
    }catch(error){
      try{
        choose('cmbMaterial',before.cmbMaterial.index,'sltCmbMaterialIndexChanged');
        choose('cmbMaterial_2',exact(boxes.cmbMaterial_2,before.cmbMaterial_2.text),'sltCmbMaterial_2IndexChanged');
        choose('cmbMaterial_3',exact(boxes.cmbMaterial_3,before.cmbMaterial_3.text),'sltCmbMaterial_3IndexChanged');
        choose('cmbShape',before.cmbShape.index,'on_cmbShape_activated');
        for(const [key,target] of Object.entries(targets))writeSize(key,target.old);
        return {verified:false,rolled_back:true,reason:String(error)};
      }catch(rollbackError){throw new Error('Stock configuration failed and rollback is incomplete: '+String(error)+' / '+String(rollbackError));}
    }
  });},
  closeStockEditor(hwnd){return onGui(hwnd,()=>{
    const editors=treeOf(rootOf(hwnd)).filter(o=>o.name==='wgtCusProjectInfo'&&o.type==='ProjectBasicInfoWidget');
    if(editors.length!==1||!activeModal().isNull())throw new Error('Stock editor unavailable');
    return {queued:queueMethod(ptr(editors[0].pointer),'sltCloseWindow',[],1)};
  });},
  configureContourTabs(hwnd,count,width,height,shape){return onGui(hwnd,()=>{
    if(!activeModal().isNull()||!Number.isInteger(count)||count<1||count>100||![width,height].every(x=>Number.isFinite(x)&&x>0&&x<=10)||!['rectangle','triangle'].includes(shape))throw new Error('Invalid automatic tab settings or active dialog');
    const editors=treeOf(rootOf(hwnd)).filter(o=>o.type==='Contour2DPathWgt'&&visible(ptr(o.pointer)));
    if(editors.length!==1)throw new Error('Expected one visible 2D contour detail editor');
    const editor=ptr(editors[0].pointer),objects=treeOf(editor);
    const find=name=>{const matches=objects.filter(o=>o.name===name);if(matches.length!==1)throw new Error('Missing tab field '+name);return ptr(matches[0].pointer);};
    const fields={lineEdit_tabs_width:width,lineEdit_tabs_height:height,lineEdit_nums:count},targets=[];
    for(const [name,value] of Object.entries(fields)){
      const wrapper=find(name),spins=childObjects(wrapper).filter(p=>isA(p,'QDoubleSpinBox'));
      if(spins.length!==1||value<spinMin(spins[0])||value>spinMax(spins[0]))throw new Error('Tab parameter outside native field range: '+name);
      const scale=10**spinDecimals(spins[0]);
      if(Math.abs(value*scale-Math.round(value*scale))>1e-6)throw new Error('Tab parameter is not representable at native field precision: '+name);
      targets.push({name,wrapper,spin:spins[0],value});
    }
    const shapeBox=find('comboBox_tabs_shape');
    if(!isA(shapeBox,'QComboBox')||comboCount(shapeBox)!==2)throw new Error('Unexpected support shape selector');
    const results=[queueMethod(find('radioButton_default'),'setChecked',[{type:'bool',value:true}],1),queueMethod(editor,'on_radioButton_default_released',[],1)];
    if(getText(find('comboBox_tab'),comboText)!=='数量')throw new Error('Only automatic count-based support layout is verified');
    results.push(queueMethod(shapeBox,'setCurrentIndex',[{type:'int',value:shape==='rectangle'?1:0}],1));
    for(const target of targets){
      results.push(queueMethod(target.spin,'setValue',[{type:'double',value:target.value}],1));
      results.push(queueMethod(target.wrapper,'editingFinished',[],1));
    }
    results.push(queueMethod(editor,'on_lineEdit_tabs_width_editingFinished',[],1));
    results.push(queueMethod(editor,'on_pushButton_add_tab_clicked',[],1));
    return {invoked:results,readback:Object.fromEntries(targets.map(t=>[t.name,spinValue(t.spin)])),shape:getText(shapeBox,comboText)};
  });},
  calculateContourDetails(hwnd){return onGui(hwnd,()=>{
    const editors=treeOf(rootOf(hwnd)).filter(o=>o.type==='Contour2DPathWgt'&&visible(ptr(o.pointer)));
    if(editors.length!==1||!activeModal().isNull())throw new Error('Expected visible contour detail page');
    return {queued:queueMethod(ptr(editors[0].pointer),'on_btnMainCalculate_released',[],2)};
  });},
  openOperationDetails(hwnd,index){return onGui(hwnd,()=>{
    const paths=treeOf(rootOf(hwnd)).filter(o=>o.type==='KnifePathDragWdg');
    if(!Number.isInteger(index)||index<0||index>=paths.length||!activeModal().isNull())throw new Error('Operation unavailable or dialog active');
    return {queued:queueMethod(ptr(paths[index].pointer),'on_btnImgShowDetail_clicked',[],1)};
  });},
  saveBeforeReopeningSameTest(hwnd,stem){return onGui(hwnd,()=>{
    const root=rootOf(hwnd),modal=activeModal();
    if(getText(root,titleText)!=='MakeraStudio-['+stem+']'||modal.isNull()||classOf(modal)!=='MyMessageBox')throw new Error('Unexpected project save dialog');
    const objects=treeOf(modal);
    if(!objects.some(o=>isA(ptr(o.pointer),'QLabel')&&getText(ptr(o.pointer),labelText)==='是否保存当前项目？'))throw new Error('Unexpected dialog text');
    const buttons=objects.filter(o=>isA(ptr(o.pointer),'QPushButton')&&getText(ptr(o.pointer),buttonText)==='保存');
    if(buttons.length!==1)throw new Error('Expected save action');
    return {queued:queueMethod(ptr(buttons[0].pointer),'click')};
  });},
  applyToolMaterial(hwnd){return onGui(hwnd,()=>{
    const modal=activeModal();if(modal.isNull()||classOf(modal)!=='CutterEditDlg')throw new Error('Expected native tool editor');
    return {queued:queueMethod(modal,'on_pushButtonApply_clicked')};
  });},
  configureToolTestMaterial(hwnd,material,parameters){return onGui(hwnd,()=>{
    const modal=activeModal();if(modal.isNull()||classOf(modal)!=='CutterEditDlg')throw new Error('Expected native tool editor');
    const objects=treeOf(modal),find=name=>{const matches=objects.filter(o=>o.name===name);if(matches.length!==1)throw new Error('Expected tool field '+name);return ptr(matches[0].pointer);};
    const list=find('listWidget');if(!isA(list,'QListWidget'))throw new Error('Expected material list');
    let rows=[];for(let i=0;i<listCount(list);i++)if(getText(listItem(list,i),listItemText)===material)rows.push(i);
    if(!rows.length&&material==='铜'){
      const groups=[];for(let i=0;i<listCount(list);i++)if(getText(listItem(list,i),listItemText)==='铜合金')groups.push(i);
      if(groups.length===1)queueMethod(list,'onItemClicked',[{type:'QListWidgetItem*',value:listItem(list,groups[0])}],1);
      for(let i=0;i<listCount(list);i++)if(getText(listItem(list,i),listItemText)===material)rows.push(i);
    }
    if(rows.length!==1)throw new Error('Expected exact material name; available: '+Array.from({length:listCount(list)},(_,i)=>getText(listItem(list,i),listItemText)).join(','));
    listSetCurrent(list,rows[0]);
    const results=[queueMethod(find('checkBoxEnable'),'setChecked',[{type:'bool',value:true}],1),queueMethod(modal,'on_checkBoxEnable_clicked',[],1)];
    const fields={spindle_speed:'lineEdit_spindle_speed',feedrate:'lineEdit_feedrate',plunge_feedrate:'lineEdit_plunge_feedrate',step_down:'lineEdit_step_down',step_over_mm:'lineEdit_step_over_mm'};
    if(Object.keys(parameters).sort().join(',')!==Object.keys(fields).sort().join(','))throw new Error('Expected all explicit offline test parameters');
    for(const [key,name] of Object.entries(fields)){
      const wrapper=find(name),spin=childObjects(wrapper).filter(p=>isA(p,'QDoubleSpinBox'));
      if(spin.length!==1||!Number.isFinite(parameters[key])||parameters[key]<=0||parameters[key]<spinMin(spin[0])||parameters[key]>spinMax(spin[0]))throw new Error('Invalid offline test parameter '+key);
      results.push(queueMethod(spin[0],'setValue',[{type:'double',value:parameters[key]}],1));
      results.push(queueMethod(wrapper,'editingFinished',[],1));
    }
    results.push(queueMethod(modal,'on_lineEdit_step_over_mm_editingFinished',[],1));
    return {invoked:results,material,parameters,save_enabled:enabled(find('pushButton_ok')),purpose:'offline API test; machining parameters not validated for cutting'};
  });},
  acknowledgeToolInfo(hwnd){return onGui(hwnd,()=>{
    const modal=activeModal();if(modal.isNull()||classOf(modal)!=='MyMessageBox')throw new Error('Expected tool information prompt');
    const objects=treeOf(modal);
    if(!objects.some(o=>isA(ptr(o.pointer),'QLabel')&&getText(ptr(o.pointer),labelText)==='请选择刀具及其属性'))throw new Error('Unexpected tool prompt');
    const buttons=objects.filter(o=>isA(ptr(o.pointer),'QPushButton')&&getText(ptr(o.pointer),buttonText)==='确认');
    if(buttons.length!==1)throw new Error('Expected confirmation action');
    return {queued:queueMethod(ptr(buttons[0].pointer),'click')};
  });},
  toolEditParametersPage(hwnd){return onGui(hwnd,()=>{
    const modal=activeModal();if(modal.isNull()||classOf(modal)!=='CutterEditDlg')throw new Error('Expected native tool editor');
    return {queued:queueMethod(modal,'on_pushButton_2_clicked')};
  });},
  configureToolDialog(hwnd,spec){return onGui(hwnd,()=>{
    const modal=activeModal();if(modal.isNull()||classOf(modal)!=='CutterEditDlg')throw new Error('Expected native tool editor');
    if(!['endmill','drill'].includes(spec.kind)||typeof spec.name!=='string'||!spec.name||spec.name.length>80)throw new Error('Invalid tool description');
    const objects=treeOf(modal),find=name=>{const matches=objects.filter(o=>o.name===name);if(matches.length!==1)throw new Error('Expected tool field '+name);return ptr(matches[0].pointer);};
    const fields={diameter:'lineEdit_diameter',shank_diameter:'lineEdit_shank_diameter',flute_length:'lineEdit_fluter_length'};
    for(const key of Object.keys(fields))if(!Number.isFinite(spec[key])||spec[key]<=0||spec[key]>100)throw new Error('Invalid tool dimension');
    const results=[queueMethod(find('comboBox'),'setCurrentIndex',[{type:'int',value:spec.kind==='endmill'?1:5}],1),
      queueMethod(find('lineEdit'),'setText',[{type:'QString',value:spec.name}],1),
      queueMethod(find('rbnMetal'),'setChecked',[{type:'bool',value:true}],1),queueMethod(modal,'on_rbnMetal_clicked',[],1)];
    for(const [key,name] of Object.entries(fields)){
      const wrapper=find(name),spin=childObjects(wrapper).filter(p=>isA(p,'QDoubleSpinBox'));
      if(spin.length!==1||spec[key]<spinMin(spin[0])||spec[key]>spinMax(spin[0]))throw new Error('Tool dimension outside field range');
      results.push(queueMethod(spin[0],'setValue',[{type:'double',value:spec[key]}],1));
      results.push(queueMethod(wrapper,'editingFinished',[],1));
    }
    return {invoked:results,save_enabled:enabled(find('pushButton_ok'))};
  });},
  saveToolDialog(hwnd){return onGui(hwnd,()=>{
    const modal=activeModal();if(modal.isNull()||classOf(modal)!=='CutterEditDlg')throw new Error('Expected native tool editor');
    const ok=treeOf(modal).filter(o=>o.name==='pushButton_ok');
    if(ok.length!==1||!enabled(ptr(ok[0].pointer)))throw new Error('Native tool editor validation is not satisfied');
    return {queued:queueMethod(modal,'on_pushButton_ok_released')};
  });},
  selectToolGroup(hwnd,name){return onGui(hwnd,()=>{
    const modal=activeModal();if(modal.isNull()||classOf(modal)!=='CutterLibWgt')throw new Error('Expected tool library');
    const trees=treeOf(modal).filter(o=>o.name==='treeWidget'&&isA(ptr(o.pointer),'QTreeWidget'));
    if(trees.length!==1)throw new Error('Expected tool group tree');
    const tree=ptr(trees[0].pointer),matches=[];
    function visit(item,depth){if(depth>12)throw new Error('Tool tree too deep');for(let i=0;i<treeChildCount(item);i++){
      const child=treeChild(item,i),s=Memory.alloc(8);treeItemText(child,s,0);let text;try{text=readQString(s);}finally{destroyQString(s);}if(text===name)matches.push(child);visit(child,depth+1);}}
    visit(treeRoot(tree),0);if(matches.length!==1)throw new Error('Expected one exact tool group name');
    treeSetCurrent(tree,matches[0]);return {selected:true,group:name};
  });},
  selectLibraryTool(hwnd,name,material='铜'){return onGui(hwnd,()=>{
    const modal=activeModal();if(modal.isNull()||classOf(modal)!=='CutterLibWgt')throw new Error('Expected tool library');
    const tables=treeOf(modal).filter(o=>o.name==='tableWidget_tool'&&isA(ptr(o.pointer),'QTableWidget'));
    if(tables.length!==1)throw new Error('Expected tool table');
    const table=ptr(tables[0].pointer),rows=[];for(let i=0;i<tableRows(table);i++){
      const item=tableItem(table,i,0);if(!item.isNull()&&getText(item,tableItemText)===name)rows.push(i);
    }
    if(rows.length!==1)throw new Error('Expected one exact tool name in selected group');
    tableSetCurrent(table,rows[0],0);
    const materials=treeOf(modal).filter(o=>o.name==='tableWidget_material'&&isA(ptr(o.pointer),'QTableWidget'));
    let materialSelected=false;
    if(materials.length===1){const table=ptr(materials[0].pointer),rows=[];for(let i=0;i<tableRows(table);i++){
      const item=tableItem(table,i,0);if(!item.isNull()&&getText(item,tableItemText)===material)rows.push(i);
    }if(rows.length===1){tableSetCurrent(table,rows[0],0);materialSelected=true;}}
    return {selected:true,row:rows[0],name,material_selected:materialSelected,material};
  });},
  toolLibraryAction(hwnd,action){return onGui(hwnd,()=>{
    const methods={new_group:'on_action_new_tools_top_triggered',add_tool:'on_btnBrdrAddTool_clicked',edit_tool:'on_btnBrdrEditTool_clicked',choose:'on_btnTxtMainChoose_clicked',close:'on_pushButton_cancel_clicked'};
    if(!methods[action])throw new Error('Unsupported tool library action');
    const modal=activeModal();if(modal.isNull()||classOf(modal)!=='CutterLibWgt')throw new Error('Expected tool library');
    return {queued:queueMethod(modal,methods[action])};
  });},
  toolTables(hwnd){return onGui(hwnd,()=>{
    const modal=activeModal();if(modal.isNull()||classOf(modal)!=='CutterLibWgt')throw new Error('Expected tool library');
    return treeOf(modal).filter(o=>isA(ptr(o.pointer),'QTableWidget')).map(o=>{
      const object=ptr(o.pointer),rows=tableRows(object),columns=tableColumns(object),values=[];
      if(rows>2000||columns>100)throw new Error('Tool table too large');
      for(let row=0;row<rows;row++){const cells=[];for(let col=0;col<columns;col++){const item=tableItem(object,row,col);cells.push(item.isNull()?null:getText(item,tableItemText));}values.push(cells);}
      const row_labels=[];for(let row=0;row<rows;row++){const header=tableVerticalHeader(object,row);row_labels.push(header.isNull()?null:getText(header,tableItemText));}
      return {name:o.name,rows,columns,values,row_labels};
    });
  });},
  openToolEditor(hwnd,index){return onGui(hwnd,()=>{
    const paths=treeOf(rootOf(hwnd)).filter(o=>o.type==='KnifePathDragWdg');
    if(!Number.isInteger(index)||index<0||index>=paths.length)throw new Error('Path index not found');
    const editors=treeOf(ptr(paths[index].pointer)).filter(o=>o.type==='CutterCmbWgt');
    if(editors.length!==1)throw new Error('Expected a single tool editor');
    return {queued:queueMethod(ptr(editors[0].pointer),'on_btnImgEdit_clicked')};
  });},
  pathSummary(hwnd){return onGui(hwnd,()=>{
    const modal=activeModal();
    return {modal_type:modal.isNull()?null:classOf(modal),
      paths:treeOf(rootOf(hwnd)).filter(o=>o.type==='KnifePathDragWdg').map((o,index)=>{
        const objects=treeOf(ptr(o.pointer));
        const value=(name,getter)=>{const x=objects.filter(o=>o.name===name);return x.length===1?getText(ptr(x[0].pointer),getter):null;};
        const depths=objects.filter(o=>o.name==='doubleSpinBox'&&/\/(dspbStartDepth|dspbEndDepth)\//.test(o.path)).map(o=>({field:o.path.includes('/dspbStartDepth/')?'start':'end',value:spinValue(ptr(o.pointer))}));
        return {index,title:value('lineEdt_knifeWorkTitle',lineText),geometry_selection:value('btnChooseObj',buttonText),depths,
          tool:value('comboBox',comboText),is_contour_2d:objects.some(o=>o.name==='contour2DPosition')};
      })};
  });},
  configureContour(hwnd,index,uuids,endDepth,kind){return onGui(hwnd,()=>{
    if(!['contour','drilling','pocket'].includes(kind))throw new Error('Unsupported planar operation kind');
    if(!Number.isInteger(index)||index<0||!Array.isArray(uuids)||!uuids.length||uuids.length>50||uuids.some(s=>typeof s!=='string'||!/^[A-Za-z0-9-]{36}$/.test(s)))throw new Error('Invalid path or geometry identifiers');
    if(!Number.isFinite(endDepth)||endDepth<=0||endDepth>10)throw new Error('Invalid test contour depth');
    const root=rootOf(hwnd),paths=treeOf(root).filter(o=>o.type==='KnifePathDragWdg');
    if(index>=paths.length)throw new Error('Path index not found');
    const path=ptr(paths[index].pointer),objects=treeOf(path);
    const choice=objects.filter(o=>o.type==='ChooseObjBtnWgt');
    const depth=objects.filter(o=>o.name==='doubleSpinBox'&&o.path.includes('/dspbEndDepth/'));
    if(choice.length!==1||depth.length!==1)throw new Error('Unexpected contour editor');
    if(kind==='contour'&&!objects.some(o=>o.name==='contour2DPosition'&&visible(ptr(o.pointer))))throw new Error('Expected visible 2D contour operation');
    const results=[queueMethod(ptr(choice[0].pointer),'sltChooseDlgFinished',[{type:'std::vector<std::string>',value:uuids},{type:'int',value:uuids.length}],1),
      queueMethod(ptr(depth[0].pointer),'setValue',[{type:'double',value:endDepth}],1),
      queueMethod(path,'on_dspbEndDepth_editingFinished',[],1)];
    return {invoked:results,geometry_uuids:uuids,end_depth_mm:endDepth};
  });},
  setOperationDepths(hwnd,index,startDepth,endDepth){return onGui(hwnd,()=>{
    if(!Number.isInteger(index)||index<0||!Number.isFinite(startDepth)||!Number.isFinite(endDepth)||startDepth<0||endDepth<=startDepth||endDepth>10)throw new Error('Invalid planar depth interval');
    const paths=treeOf(rootOf(hwnd)).filter(o=>o.type==='KnifePathDragWdg');
    if(index>=paths.length||!activeModal().isNull())throw new Error('Operation unavailable or dialog active');
    const path=ptr(paths[index].pointer),objects=treeOf(path),targets={};
    for(const field of ['Start','End']){
      const matches=objects.filter(o=>o.name==='doubleSpinBox'&&o.path.includes('/dspb'+field+'Depth/'));
      if(matches.length!==1||!isA(ptr(matches[0].pointer),'QDoubleSpinBox'))throw new Error('Expected one planar depth field');
      const object=ptr(matches[0].pointer),value=field==='Start'?startDepth:endDepth;
      if(value<spinMin(object)||value>spinMax(object)||(field==='Start'&&spinMin(object)>0))throw new Error('Depth outside native field range');
      const scale=10**spinDecimals(object);
      if(Math.abs(value*scale-Math.round(value*scale))>1e-6)throw new Error('Depth is not representable at the native field precision; choose and validate a rounded depth explicitly');
      targets[field]=object;
    }
    const results=[];
    // Reset start before changing end, then apply the requested interval. This
    // avoids transient start>end constraints when editing an existing interval.
    results.push(queueMethod(targets.Start,'setValue',[{type:'double',value:0}],1));
    results.push(queueMethod(path,'on_dspbStartDepth_editingFinished',[],1));
    results.push(queueMethod(targets.End,'setValue',[{type:'double',value:endDepth}],1));
    results.push(queueMethod(path,'on_dspbEndDepth_editingFinished',[],1));
    if(startDepth!==0){
      results.push(queueMethod(targets.Start,'setValue',[{type:'double',value:startDepth}],1));
      results.push(queueMethod(path,'on_dspbStartDepth_editingFinished',[],1));
    }
    return {invoked:results,start_depth_mm:spinValue(targets.Start),end_depth_mm:spinValue(targets.End)};
  });},
  generatePath(hwnd,index){return onGui(hwnd,()=>{
    const paths=treeOf(rootOf(hwnd)).filter(o=>o.type==='KnifePathDragWdg');
    if(!Number.isInteger(index)||index<0||index>=paths.length)throw new Error('Path index not found');
    return {queued:queueMethod(ptr(paths[index].pointer),'on_btnApplyPath_clicked')};
  });},
  windowIdentity(hwnd){const object=findWidget(ptr(hwnd));return object.isNull()?null:{name:nameOf(object),type:classOf(object)};},
  showStudio(hwnd){return {queued:queueMethod(rootOf(hwnd),'show')};},
  choosePathType(hwnd,label){return onGui(hwnd,()=>{
    if(!['2D轮廓','2D区域清理','2D钻孔'].includes(label))throw new Error('Unsupported CAM operation');
    const modal=activeModal();if(modal.isNull()||classOf(modal)!=='AddKnifeTypeDlg')throw new Error('Expected path type chooser');
    const labels=treeOf(modal).filter(o=>isA(ptr(o.pointer),'QLabel')&&getText(ptr(o.pointer),labelText)===label);
    if(labels.length!==1)throw new Error('Expected exact operation label');
    const buttons=childObjects(parentOf(ptr(labels[0].pointer))).filter(o=>isA(o,'QPushButton'));
    if(buttons.length!==1)throw new Error('Expected operation action');
    return {queued:queueMethod(buttons[0],'click'),operation:label};
  });},
  openPathChooser(hwnd){
    const matches=treeOf(rootOf(hwnd)).filter(o=>o.type==='KnifePathControl'&&o.name==='PathListWidget');
    if(matches.length!==1)throw new Error('Expected one path controller');
    return {queued:queueMethod(ptr(matches[0].pointer),'on_btnTxtAddPath_clicked')};
  },
  camPanel(hwnd){return onGui(hwnd,()=>{
    const modal=activeModal(),root=modal.isNull()?rootOf(hwnd):modal;
    return {modal:!modal.isNull(),root:classOf(root),items:treeOf(root).filter(o=>isA(ptr(o.pointer),'QWidget')&&visible(ptr(o.pointer))).map(o=>{
      const p=ptr(o.pointer);let value=null;
      if(isA(p,'QLabel'))value=getText(p,labelText);
      else if(isA(p,'QAbstractButton'))value=getText(p,buttonText);
      else if(isA(p,'QDoubleSpinBox'))value=spinValue(p);
      else if(isA(p,'QComboBox'))value=getText(p,comboText);
      else if(isA(p,'QLineEdit'))value=getText(p,lineText);
      let options;
      if(isA(p,'QComboBox')){options=[];for(let i=0;i<comboCount(p);i++){const s=Memory.alloc(8);comboItemText(p,s,i);try{options.push(readQString(s));}finally{destroyQString(s);}}}
      return {...o,value,options,enabled:enabled(p),methods:methodsOf(p)};
    })};
  });},
  identity(){return {executable:Process.getModuleByName('MakeraStudio.exe').path,architecture:Process.arch,qt_core:core.path,backend:'native_qt'};},
  barrier(hwnd){return {completed:queueMethod(rootOf(hwnd),'update',[],3)};},
  state(hwnd){return onGui(hwnd,()=>readState(rootOf(hwnd)));},
  describeCam(hwnd){return onGui(hwnd,()=>{
    const root=rootOf(hwnd),objects=treeOf(root);
    return objects.filter(o=>o.path.includes('/widgetDesign')&&
      (/WidgetDesign|ProjectBasicInfoWidget|PathListWidget|ExportPathsWidget|SchemeStepWidget|Path.*Wgt|Simulation|Priview|Preview|^Design/.test(o.type)||/PathListWidget|widgetDesign$|widgetCusProjectInfo/.test(o.name)))
      .map(o=>({...o,methods:methodsOf(ptr(o.pointer))}));
  });},
  openProject(hwnd,path){
    const root=rootOf(hwnd);
    return {queued:queueMethod(root,'sltOpenProject',[{type:'QString',value:path}]),method:'MainWindow.sltOpenProject(QString)'};
  },
  saveProject(hwnd){return onGui(hwnd,()=>{
    const root=rootOf(hwnd),buttons=treeOf(root).filter(o=>o.name==='btnSave'&&isA(ptr(o.pointer),'QPushButton'));
    if(buttons.length!==1||!enabled(ptr(buttons[0].pointer)))throw new Error('Studio save action unavailable');
    return {queued:queueMethod(ptr(buttons[0].pointer),'click'),method:'Studio.btnSave native QAbstractButton.click()'};
  });},
  stockFields(hwnd){return onGui(hwnd,()=>treeOf(rootOf(hwnd)).filter(o=>o.name==='doubleSpinBox'&&/\/(dspbLength|dspbWidth|dspbHeight)\//.test(o.path))
    .map(o=>({path:o.path,value:spinValue(ptr(o.pointer)),visible:visible(ptr(o.pointer))})));},
  describeStock(hwnd){return onGui(hwnd,()=>treeOf(rootOf(hwnd)).filter(o=>/^(dspbLength|dspbWidth|dspbHeight|wgtCusProjectInfo)$/.test(o.name))
    .map(o=>({...o,methods:methodsOf(ptr(o.pointer))})));},
  setStock(hwnd,dimensions){return onGui(hwnd,()=>{
    const root=rootOf(hwnd),objects=treeOf(root),matches=[];
    for(const [axis,ancestor] of Object.entries({x:'dspbLength',y:'dspbWidth',z:'dspbHeight'})){
      if(dimensions[axis]===undefined)continue;
      let found=objects.filter(o=>o.path.includes('/widgetDesign/')&&o.path.includes('/'+ancestor+'/')&&o.name==='doubleSpinBox'&&visible(ptr(o.pointer)));
      if(found.length===0)found=objects.filter(o=>o.path.includes('/widgetCusProjectInfo/wgtCusProjectInfo/')&&o.path.includes('/'+ancestor+'/')&&o.name==='doubleSpinBox');
      if(found.length!==1)throw new Error('Expected one stock field for '+axis);
      const object=ptr(found[0].pointer),value=dimensions[axis];
      if(!isA(object,'QDoubleSpinBox')||!Number.isFinite(value)||value<spinMin(object)||value>spinMax(object))throw new Error('Stock value outside native field range');
      const wrapper=objects.find(o=>o.path===found[0].path.replace(/\/doubleSpinBox$/,''));
      if(!wrapper||wrapper.type!=='UnitDoubleSpinBox')throw new Error('Unexpected stock editor wrapper');
      matches.push({axis,object,wrapper:ptr(wrapper.pointer),value,path:found[0].path});
    }
    if(!matches.length)throw new Error('No stock dimensions provided');
    // Validate every target before queuing any mutation.
    return matches.map(item=>({axis:item.axis,path:item.path,requested:item.value,
      queued:queueMethod(item.object,'setValue',[{type:'double',value:item.value}],1),
      commit_queued:queueMethod(item.wrapper,'editingFinished',[],1)}));
  });},
  readObjects(hwnd) {
    return onGui(hwnd,()=>{const root=rootOf(hwnd);return {rootClass:classOf(root),rootMethods:methodsOf(root),objects:treeOf(root)};});
  },
  inspectRoot(hwnd) {
    const root=findWidget(ptr(hwnd));
    if(root.isNull()) throw new Error('No Qt QWidget associated with target HWND');
    const table=root.readPointer();
    return {pointer:root.toString(),name:nameOf(root),vtable:[0,1,2,3].map(i=>{
      const address=table.add(i*8).readPointer();
      let pc=address,code=[];
      for(let j=0;j<10;j++){const instruction=Instruction.parse(pc);code.push(instruction.toString());pc=instruction.next;}
      return {index:i,address:address.toString(),code};
    })};
  }
};
