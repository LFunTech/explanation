/* Shell-free iframe bridge. The embedding application owns all UI. */
(()=>{
'use strict';
class Player {
 constructor(iframe,{timeout=12000}={}) {
  if(!(iframe instanceof HTMLIFrameElement))throw new TypeError('需要传入课件 iframe');
  this.iframe=iframe;this.timeout=timeout;this.listeners=new Map();this.state=null;this.metadata=null;
  this.isReady=false;this.destroyed=false;this.config=null;this.timer=null;
  this.receive=event=>{
   const data=event.data;
   if(event.source!==iframe.contentWindow||data?.channel!=='courseware'||data.version!==1)return;
   if(data.type==='ready'){
    clearTimeout(this.timer);this.isReady=true;this.metadata=data.payload;this.state=data.payload.state;
    this.send('init',{audio:this.config?.audio||{},motion:this.config?.motion!==false,figureHost:this.config?.figureHost===true,answerHost:this.config?.answerHost===true,announce:false});
    this.emit('ready',this.metadata);this.emit('state',this.state);
   }else if(data.type==='state'&&this.isReady){this.state=data.payload;this.emit('state',this.state);}
   else if(data.type==='answer'&&this.isReady)this.emit('answer',data.payload);
   else if(data.type==='figure'&&this.isReady)this.emit('figure',data.payload);
   else if(data.type==='error')this.emit('error',data.payload);
  };
  this.loaded=()=>this.send('init',{audio:this.config?.audio||{},motion:this.config?.motion!==false,figureHost:this.config?.figureHost===true,answerHost:this.config?.answerHost===true});
  window.addEventListener('message',this.receive);iframe.addEventListener('load',this.loaded);
 }
 on(type,callback){
  if(!this.listeners.has(type))this.listeners.set(type,new Set());
  this.listeners.get(type).add(callback);
  return ()=>this.listeners.get(type)?.delete(callback);
 }
 emit(type,value){this.listeners.get(type)?.forEach(callback=>callback(value));}
 load(config){
  if(this.destroyed)throw new Error('播放器已销毁');
  if(typeof config==='string')config={contentUrl:config};
  const url=new URL(config.contentUrl,document.baseURI);
  if(!['https:','http:','file:'].includes(url.protocol))throw new Error('课件地址须为 HTTP、HTTPS 或本地文件');
  this.stop();clearTimeout(this.timer);this.config={...config,motion:config.motion!==false};
  this.isReady=false;this.state=null;this.metadata=null;
  this.iframe.src=url.href;this.emit('loading',{contentUrl:url.href});
  this.timer=setTimeout(()=>{
   if(this.isReady||this.destroyed)return;
   if(this.config.fallback){
    const fallback={...this.config.fallback,motion:this.config.motion};
    this.load(fallback);this.emit('fallback',{message:'远程课件连接较慢，已切换到本地副本。'});
   }else this.emit('error',{message:'课件未加载完成，请重新加载。'});
  },this.timeout);
  return this;
 }
 send(command,value){
  if(this.destroyed||(!this.isReady&&command!=='init'))return false;
  // The recommended sandbox has an opaque origin; verify source on receipt.
  this.iframe.contentWindow?.postMessage({channel:'courseware',version:1,type:'command',command,value},'*');
  return true;
 }
 next(){return this.send('next');}
 previous(){return this.send('previous');}
 goTo(index){return this.send('goTo',index);}
 seek(page){return this.send('seek',page);}
 play(){return this.send('play');}
 stop(){return this.send('stop');}
 print(){return this.send('print');}
 setAutoPlay(value){return this.send('setAutoPlay',value===true);}
 setMotion(value){if(this.config)this.config.motion=value!==false;return this.send('setMotion',value!==false);}
 getState(){return this.state;}
 destroy(){
  this.stop();clearTimeout(this.timer);this.destroyed=true;this.isReady=false;
  window.removeEventListener('message',this.receive);this.iframe.removeEventListener('load',this.loaded);this.listeners.clear();
 }
}
window.CoursewareSDK=Object.freeze({Player,version:'1.0.0'});
})();
