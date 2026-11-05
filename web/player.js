/* Player shell, separate from content; also available as packaged player.html. */
(()=>{
const $=id=>document.getElementById(id),frame=$('lesson-content');
let manifest={},questionLayout={},current=null,ready=false;
let source=window.COURSEWARE_HOST,usingFallback=false,loadingTimer;
function loadContent(){
 if(typeof source.contentHtml==='string')frame.srcdoc=source.contentHtml;
 else{frame.removeAttribute('srcdoc');frame.src=source.contentUrl;}
}
function fitContent(){
 const container=frame.parentElement,style=getComputedStyle(container);
 const width=container.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight);
 const height=container.clientHeight-parseFloat(style.paddingTop)-parseFloat(style.paddingBottom);
 const fitted=Math.max(0,Math.min(width,height*16/9));
 frame.style.width=`${fitted}px`;frame.style.height=`${fitted*9/16}px`;
}
new ResizeObserver(fitContent).observe(frame.parentElement);
['speak','prev','next','progress','notes-toggle','reading-jump','print'].forEach(id=>$(id).disabled=true);
function watchLoading(){
 clearTimeout(loadingTimer);
 loadingTimer=setTimeout(()=>{
  if(ready)return;
  if(!usingFallback&&window.COURSEWARE_HOST.fallback){
   usingFallback=true;source=window.COURSEWARE_HOST.fallback;
   $('content-status').querySelector('span').textContent='MinIO 连接较慢，正在打开本地副本…';
   loadContent();watchLoading();return;
  }
  $('content-status').querySelector('span').textContent='课件连接未完成，请重新加载。';$('content-reload').hidden=false;
 },12000);
}
$('content-reload').onclick=()=>window.location.reload();
document.body.classList.toggle('embedded-player',window.parent!==window);
function escape(text){return String(text??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function prose(text){return escape(text||'').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');}
function math(node){window.renderMathInElement?.(node,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},{left:'\\(',right:'\\)',display:false},{left:'\\[',right:'\\]',display:true}],throwOnError:false,trust:false,strict:'ignore'});}
function original(){return window.QuestionView.render(questionLayout,manifest.question.images||[]);}
function command(name,value){frame.contentWindow?.postMessage({channel:'courseware',version:1,type:'command',command:name,value},'*');}
const wide=window.matchMedia('(min-width:64rem)');
function setOutline(open){document.body.classList.toggle('outline-visible',open);$('outline-toggle').setAttribute('aria-expanded',String(open));if(!open&&$('outline-panel').contains(document.activeElement))$('outline-toggle').focus();$('outline-panel').inert=!open;if(open)requestAnimationFrame(()=>$('outline').querySelector('[aria-current="page"]')?.scrollIntoView({block:'nearest'}));}
setOutline(wide.matches);wide.addEventListener('change',event=>setOutline(event.matches));
function update(state){
 const changed=!current||current.index!==state.index;current=state;
 $('page-number').textContent=`${state.page+1} / ${state.totalPages}`;
 $('progress').max=Math.max(0,state.totalPages-1);$('progress').value=state.page;
 $('prev').disabled=state.page===0;$('next').disabled=state.page===state.totalPages-1;
 $('speak').disabled=!state.canPlay;$('speak').textContent=state.playing?(state.paused?'▶ 继续讲解':'⏸ 暂停讲解'):'▶ 播放讲解';
 $('auto-next').checked=state.autoNext;$('outline-count').textContent=`${state.totalPages} 页`;
 $('source-mode').textContent=`${state.totalPages} 页${manifest.duration?' · '+Math.round(manifest.duration)+' 秒':''}${usingFallback?' · 本地副本':''}`;
 if(changed){$('notes').textContent=state.notes;math($('notes'));}
 [...$('outline').children].forEach((b,i)=>{b.firstElementChild.textContent=String(state.outline[i].page+1).padStart(2,'0');b.classList.toggle('active',i===state.index);if(i===state.index){b.setAttribute('aria-current','page');if(changed)b.scrollIntoView({block:'nearest'});}else b.removeAttribute('aria-current');});
}
function initialize(data){
 command('init',{audio:source.audio||{},motion:source.motion!==false,figureHost:true,answerHost:true,announce:false});
 clearTimeout(loadingTimer);$('content-status').hidden=true;
 ['progress','notes-toggle','reading-jump','print'].forEach(id=>$(id).disabled=false);
 ready=true;manifest={...data,question:{images:data.images}};questionLayout=data.questionLayout;
 document.title=data.title;$('course-title').textContent=data.title;math($('course-title'));
 $('voice-mode').textContent={qwen:'逐页语音文件',browser:'设备朗读',none:'未启用语音'}[data.voiceMode];
 $('outline').replaceChildren();
 data.state.outline.forEach(slide=>{const button=document.createElement('button');button.title=slide.title;const label=slide.title.replace(/^(?:知识点|解题步骤|步骤)\s*[一二三四五六七八九十\d]+\s*[:：、.·]\s*/,'');button.innerHTML=`<span class="outline-number">${String(slide.page+1).padStart(2,'0')}</span><span class="outline-label">${escape(label)}</span>`;button.onclick=()=>{command('goTo',slide.index);if(!wide.matches)setOutline(false)};$('outline').append(button);});
 math($('outline'));current=null;reading();update(data.state);
}
let timer;function message(text){$('player-message').textContent=text;$('player-message').style.display='block';clearTimeout(timer);timer=setTimeout(()=>$('player-message').style.display='none',5000);}
window.addEventListener('message',event=>{const data=event.data;if(event.source!==frame.contentWindow||data?.channel!=='courseware'||data.version!==1)return;if(data.type==='ready')initialize(data.payload);else if(data.type==='state'&&ready)update(data.payload);else if(data.type==='answer'&&ready){const i=data.payload?.variantIndex;if(Number.isInteger(i)&&i>=0&&i<manifest.variants.length){command('stop');window.CoursewareAnswer.show(manifest.variants[i],i);}}else if(data.type==='figure'&&ready)openFigure(data.payload);else if(data.type==='error')message(data.payload.message);});
function knowledgeSource(point){if(!point.why)return '';const sources=(point.resource_ids||[]).map(id=>(manifest.resources||[]).find(r=>r.id===id)).filter(Boolean);return `<div class="knowledge-source">${sources.length?'依据：'+sources.map(s=>escape(s.title)).join('、'):'学科知识补充 · 暂无对应课标片段'}</div>`;}
function reading(){const r=manifest.reading;if(!r){$('reading-jump').hidden=true;return}const points=(values)=>values.map((p,i)=>`<article class="reading-point"><h3>${i+1}. ${escape(p.title)}</h3><div>${prose(p.display)}</div>${p.why?`<h4>为什么</h4><div>${prose(p.why)}</div>`:''}${p.application?`<h4>用到本题</h4><div>${prose(p.application)}</div>`:''}<div class="reading-notes">${prose(p.notes)}</div>${knowledgeSource(p)}</article>`).join('');
 $('reading').innerHTML=`<div class="reading-heading"><span>回看与练习</span><h2>把依据看明白，把方法练熟。</h2><p>展开查看完整题目、知识依据和每一步推导。</p></div><details><summary>原题与全部配图</summary><div class="reading-content">${original()}</div></details><details><summary>核心知识 · 为什么、怎么用</summary><div class="reading-content">${points(r.knowledge_points)}</div></details><details id="full-solution" open><summary>正确答案与详细解题过程</summary><div class="reading-content"><div class="reading-answer">${prose(r.answer)}</div>${points(r.solution_steps)}</div></details><details><summary>易错提醒与方法总结</summary><div class="reading-content">${points(r.pitfalls)}<ul>${r.summary.map(t=>`<li>${prose(t)}</li>`).join('')}</ul></div></details>${(manifest.variants||[]).length?`<details><summary>相关变式题 · ${manifest.variants.length} 道</summary><div class="reading-content">${manifest.variants.map((v,i)=>`<article class="reading-point" data-variant-index="${i}"><h3>变式 ${i+1} <small>${v.source==='resource'?'来自题库':'自拟训练'}</small></h3><div>${manifest.variantLayouts?.[i]?window.QuestionView.render(manifest.variantLayouts[i],v.images||[],{imageLabel:'变式配图'}):prose(v.stem)}</div><details><summary>查看答案与解析</summary><div class="variant-answer"><b>${prose(v.answer)}</b><div>${prose(v.analysis)}</div></div></details></article>`).join('')}</div></details>`:''}`;math($('reading'));}
function openFigure(figure){
 if(!figure||typeof figure.src!=='string'||!(/^(?:data:image\/|https?:\/\/|blob:)/i.test(figure.src)))return;
 command('stop');$('figure-image').src=figure.src;$('figure-image').alt=figure.alt||'课件配图';$('figure-caption').textContent=figure.caption||'';
 if(!$('figure-dialog').open)$('figure-dialog').showModal();
}
$('figure-close').onclick=()=>$('figure-dialog').close();
$('figure-dialog').addEventListener('close',()=>{$('figure-image').removeAttribute('src');});
$('reading').addEventListener('click',event=>{
 const image=event.target.closest('[data-image-index]');if(!image)return;
 const variant=image.closest('[data-variant-index]'),images=variant?manifest.variants[Number(variant.dataset.variantIndex)].images:manifest.question.images;
 openFigure({src:images?.[Number(image.dataset.imageIndex)],alt:image.querySelector('img').alt,caption:image.querySelector('img').alt});
});
$('prev').onclick=()=>command('previous');$('next').onclick=()=>command('next');$('speak').onclick=()=>command('play');$('progress').oninput=event=>command('seek',Number(event.target.value));$('auto-next').onchange=event=>command('setAutoPlay',event.target.checked);
$('outline-toggle').onclick=()=>setOutline(!document.body.classList.contains('outline-visible'));$('outline-close').onclick=() =>setOutline(false);$('outline-backdrop').onclick=()=>setOutline(false);
$('notes-toggle').onclick=()=>$('notes-dialog').showModal();$('reading-jump').onclick=()=>{command('stop');$('full-solution').open=true;$('reading-dialog').showModal();};
document.querySelectorAll('[data-close-dialog]').forEach(b=>b.onclick=()=>$(b.dataset.closeDialog).close());$('print').onclick=()=>command('print');
document.addEventListener('keydown',event=>{if(event.defaultPrevented||document.querySelector('dialog[open]'))return;if(event.key==='Escape'&&!wide.matches&&document.body.classList.contains('outline-visible')){event.preventDefault();setOutline(false);return;}if(['INPUT','BUTTON','TEXTAREA'].includes(event.target.tagName))return;if(event.key==='ArrowRight'){event.preventDefault();command('next')}if(event.key==='ArrowLeft'){event.preventDefault();command('previous')}});
window.addEventListener('pagehide',()=>command('stop'));document.addEventListener('visibilitychange',()=>{if(document.hidden)command('stop')});
frame.addEventListener('load',()=>command('init',{audio:source.audio||{},motion:source.motion!==false,figureHost:true,answerHost:true}));loadContent();watchLoading();
})();
