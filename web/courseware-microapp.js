/* Native Shadow DOM integration. No iframe, eval, host routing or global exports. */
import {createContent,createShell} from './microapp-runtime.js';
const instances=new WeakMap();
let assetsPromise,speechOwner;
function speechFor(owner){
 const speech=window.speechSynthesis;
 if(!speech)return undefined;
 return {getVoices:()=>speech.getVoices(),speak:value=>{if(speechOwner!==owner){speech.cancel();speechOwner=owner;}speech.speak(value);},
  cancel:()=>{if(speechOwner===owner){speech.cancel();speechOwner=null;}},
  pause:()=>{if(speechOwner===owner)speech.pause();},resume:()=>{if(speechOwner===owner)speech.resume();}};
}
function frameEvent(target,source,data){
 const event=new Event('message');Object.defineProperties(event,{source:{value:source},data:{value:data}});target.dispatchEvent(event);
}
function makeScope(root,body,onEvent){
 const cleanup=[],rafs=new Set(),timers=new Set(),observers=new Set(),audio=new Set();let destroyed=false;
 const bus=new EventTarget(),values={},speech=speechFor(body);
 function listen(target,type,callback,options){target.addEventListener(type,callback,options);cleanup.push(()=>target.removeEventListener(type,callback,options));}
 const scopedDocument=new Proxy(document,{get(target,key){
  if(key==='body')return body;
  if(key==='getElementById')return id=>root.getElementById(id);
  if(key==='querySelector'||key==='querySelectorAll')return root[key].bind(root);
  if(key==='activeElement')return root.activeElement;
  if(key==='title')return '';
  if(key==='addEventListener')return (type,callback,options)=>listen(type==='visibilitychange'?document:body,type,callback,options);
  if(key==='removeEventListener')return (type,callback,options)=>(type==='visibilitychange'?document:body).removeEventListener(type,callback,options);
  const value=Reflect.get(target,key,target);return typeof value==='function'?value.bind(target):value;
 },set(target,key,value){if(key==='title')return true;return false;}});
 const scopedWindow=new Proxy(bus,{get(target,key){
  if(key in values)return values[key];
  if(key==='document')return scopedDocument;
  if(key==='speechSynthesis')return speech;
  if(key==='addEventListener')return (type,callback,options)=>listen(type==='pagehide'?window:bus,type,callback,options);
  if(key==='removeEventListener')return (type,callback,options)=>(type==='pagehide'?window:bus).removeEventListener(type,callback,options);
  if(key==='dispatchEvent')return event=>{if(destroyed)return false;if(event.type.startsWith('courseware:'))onEvent?.(event.type.slice(11),event.detail);return bus.dispatchEvent(event);};
  if(key==='matchMedia')return query=>{
   const mql=window.matchMedia(query),min=/^\(min-width:([\d.]+)rem\)$/.exec(query);
   if(min){
    const eventTarget=new EventTarget();let matches=body.clientWidth>=Number(min[1])*parseFloat(getComputedStyle(document.documentElement).fontSize);
    const observer=new ResizeObserver(()=>{const next=body.clientWidth>=Number(min[1])*parseFloat(getComputedStyle(document.documentElement).fontSize);if(next===matches)return;matches=next;const event=new Event('change');Object.defineProperty(event,'matches',{value:matches});eventTarget.dispatchEvent(event);});observer.observe(body);observers.add(observer);
    return {get matches(){return matches;},addEventListener:(...args)=>listen(eventTarget,...args),removeEventListener:eventTarget.removeEventListener.bind(eventTarget)};
   }
   return {get matches(){return mql.matches;},addEventListener:(...args)=>listen(mql,...args),removeEventListener:mql.removeEventListener.bind(mql)};
  }
  const value=window[key];return typeof value==='function'?value.bind(window):value;
 },set(target,key,value){values[key]=value;return true;}});
 scopedWindow.parent=scopedWindow;
 const scope={window:scopedWindow,document:scopedDocument,
  ResizeObserver:class extends ResizeObserver{constructor(callback){super((...args)=>{if(!destroyed)callback(...args);});observers.add(this);}},
  Audio:class extends Audio{constructor(...args){super(...args);audio.add(this);}},
  requestAnimationFrame:callback=>{if(destroyed)return 0;const id=requestAnimationFrame(time=>{rafs.delete(id);if(!destroyed)callback(time);});rafs.add(id);return id;},
  cancelAnimationFrame:id=>{rafs.delete(id);cancelAnimationFrame(id);},
  setTimeout:(callback,delay)=>{if(destroyed)return 0;const id=setTimeout(()=>{timers.delete(id);if(!destroyed)callback();},delay);timers.add(id);return id;},
  clearTimeout:id=>{timers.delete(id);clearTimeout(id);},
  destroy(){if(destroyed)return;scopedWindow.CoursewareContent?.destroy();destroyed=true;
   root.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());cleanup.forEach(fn=>fn());
   observers.forEach(observer=>observer.disconnect());rafs.forEach(cancelAnimationFrame);timers.forEach(clearTimeout);
   audio.forEach(element=>{element.pause();element.removeAttribute('src');element.load();});speech?.cancel();
  }};
 return scope;
}
function addRoot(host,css,html){
 const root=host.attachShadow({mode:'open'}),style=document.createElement('style'),body=document.createElement('div');
 style.textContent=css;body.className='micro-body';body.innerHTML=html;root.append(style,body);return {root,body};
}
async function fetchJson(url,signal){
 const response=await fetch(url,{signal,credentials:'same-origin'});
 if(!response.ok){const error=await response.json().catch(()=>({}));throw new Error(typeof error.detail==='string'?error.detail:`课件加载失败（${response.status}）`);}
 return response.json();
}
async function prepareFonts(files){
 // Shadow-root @font-face rules are not registered by all browsers. Load the
 // bundled fonts once under private family names, preserving host font faces.
 const faces=[...files.contentCss.matchAll(/@font-face\s*\{([^}]+)\}/g)];
 await Promise.all(faces.map(async([,rule])=>{
  const property=name=>rule.match(new RegExp(name+':([^;]+)'))?.[1].trim();
  const family=property('font-family')?.replace(/["']/g,'');
  if(!family?.startsWith('KaTeX_'))return;
  const font=new FontFace('Courseware_'+family,rule.match(/src:(url\([^)]*\)(?:\s*format\([^)]*\))?)/)?.[1],{style:property('font-style')||'normal',weight:property('font-weight')||'normal'});
  await font.load();document.fonts.add(font);
 }));
 return {...files,shellCss:files.shellCss.replace(/\bKaTeX_/g,'Courseware_KaTeX_'),contentCss:files.contentCss.replace(/\bKaTeX_/g,'Courseware_KaTeX_')};
}
function assets(){return assetsPromise||=(fetchJson(new URL('./microapp-assets.json',import.meta.url)).then(prepareFonts).catch(error=>{assetsPromise=null;throw error;}));}
function contentUrl(value){if(typeof value!=='string'||!value)throw new Error('请提供 result.microapp_data_url');const url=new URL(value,document.baseURI);if(!['http:','https:'].includes(url.protocol))throw new Error('dataUrl 须为 HTTP 或 HTTPS 数据地址');return url;}
export async function bootstrap(){await assets();}
export async function mount({container,dataUrl,onEvent,motion=true}={}){
 if(typeof container==='string')container=document.querySelector(container);
 if(!(container instanceof HTMLElement))throw new TypeError('请提供微应用挂载容器 container');
 instances.get(container)?.unmount();
 let props={dataUrl,onEvent,motion},version=0,controller,host,scopes=[],runtime,disposed=false;
 const notify=(type,payload)=>{if(disposed)return;props.onEvent?.({type,payload});container.dispatchEvent(new CustomEvent('courseware:'+type,{detail:payload}));};
 function clear(){scopes.forEach(scope=>scope.destroy());scopes=[];runtime=null;host?.remove();host=null;}
 const handle={
  async update(changes={}){
   if(disposed)throw new Error('微应用已卸载');props={...props,...changes};const ticket=++version;controller?.abort();controller=new AbortController();clear();
   notify('loading',{});
   try{
    const url=contentUrl(props.dataUrl);const [data,files]=await Promise.all([fetchJson(url,controller.signal),assets()]);
    if(disposed||ticket!==version)return handle;
    if(!data?.manifest||!Array.isArray(data.slides)||!data.slides.length)throw new Error('课件数据不完整');
    const absolute=value=>value?new URL(value,url).href:value;
    data.audio=Object.fromEntries(Object.entries(data.audio||{}).map(([id,value])=>[id,absolute(value)]));
    // Older packages may use relative audio references; the service normally supplies signed URLs.
    data.slides.forEach(slide=>{if(slide.audio_file&&!data.audio[slide.id])slide.audio_file=absolute(slide.audio_file);});
    host=document.createElement('div');host.dataset.coursewareMicroapp='';host.style.cssText='display:block;width:100%;height:100%;min-height:0';container.append(host);
    const shell=addRoot(host,files.shellCss,files.shellHtml),frame=shell.root.getElementById('lesson-content');
    const content=addRoot(frame,files.contentCss,'<main id="slide-viewport"><div id="slide-mount"><section class="slide" id="stage" aria-live="polite"></section></div></main><dialog id="figure-dialog"><button id="figure-close">关闭 ×</button><img id="figure-image" alt=""><p id="figure-caption"></p></dialog><section id="print-pages"></section>');
    const shellScope=makeScope(shell.root,shell.body),contentScope=makeScope(content.root,content.body,notify);scopes=[contentScope,shellScope];
    contentScope.window.COURSEWARE=data;contentScope.window.parent=shellScope.window;
    shellScope.window.postMessage=message=>frameEvent(shellScope.window,contentScope.window,message);
    contentScope.window.postMessage=message=>frameEvent(contentScope.window,shellScope.window,message);
    shellScope.window.COURSEWARE_HOST={contentUrl:url.href,audio:data.audio,motion:props.motion};
    shellScope.window.location={reload:()=>handle.update()};
    contentScope.window.print=()=>{
     const popup=window.open('','_blank','noopener=false');if(!popup){notify('error',{message:'浏览器阻止了打印窗口'});return;}
     popup.opener=null;const style=popup.document.createElement('style');style.textContent=files.contentCss;popup.document.head.append(style);
     popup.document.body.innerHTML=content.root.getElementById('print-pages').innerHTML;popup.document.title=data.manifest.title;
     popup.addEventListener('afterprint',()=>popup.close(),{once:true});popup.document.fonts.ready.then(()=>popup.print());
    };
    Object.defineProperty(frame,'contentWindow',{value:contentScope.window});
    Object.defineProperty(frame,'src',{set(){
     if(runtime)return;runtime=createContent(contentScope);frame.dispatchEvent(new Event('load'));
    }});
    createShell(shellScope);
    // Each content root handles its own arrows, so they do not reach the outer shell twice.
    content.body.addEventListener('keydown',event=>{if(event.defaultPrevented)event.stopPropagation();});
    notify('mounted',{packageId:data.manifest.package_id});return handle;
   }catch(error){
    if(disposed||ticket!==version||error.name==='AbortError')return handle;
    clear();notify('error',{message:error.message});throw error;
   }
  },
  next:()=>runtime?.next(),previous:()=>runtime?.previous(),goTo:index=>runtime?.goTo(index),
  play:()=>runtime?.play(),stop:()=>runtime?.stop(),setAutoPlay:value=>runtime?.setAutoPlay(value),setMotion:value=>runtime?.setMotion(value),
  getState:()=>runtime?.getState()||null,
  unmount(){if(disposed)return;controller?.abort();version++;clear();notify('unmounted',{});disposed=true;if(instances.get(container)===handle)instances.delete(container);}
 };
 instances.set(container,handle);
 try{await handle.update();return handle;}catch(error){handle.unmount();throw error;}
}
export async function update({container,...props}){const handle=instances.get(typeof container==='string'?document.querySelector(container):container);if(!handle)throw new Error('微应用尚未挂载');return handle.update(props);}
export function unmount({container}){instances.get(typeof container==='string'?document.querySelector(container):container)?.unmount();}
