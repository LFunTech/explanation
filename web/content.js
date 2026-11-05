/* A shell-free lesson renderer. The embedding application owns its controls. */
(()=>{
const {manifest,slides}=window.COURSEWARE;
const $=id=>document.getElementById(id);
let disposed=false;
let index=0,originalPart=0,audio=null,playing=false,paused=false,generation=0,narrationRatio=0,autoNext=false,audioSources={};
let figureHost=false,answerHost=false,motion=true,lastAnimatedPage='',pageAnimations=[];
const originalPages=new Map(),reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
const questionLayout=window.COURSEWARE.questionLayout||{...manifest.question,image_choices:[],image_indices:(manifest.question.images||[]).map((_,i)=>i)};
document.title=manifest.title;
function state(){return {index,part:originalPart,page:pageOffset(index)+originalPart,totalPages:pageOffset(slides.length),playing,paused,autoNext,motion,motionActive:motion&&!reducedMotion.matches,canPlay:manifest.voice_mode!=='none',notes:slides[index].narration||slides[index].notes||'',outline:slides.map((s,i)=>({index:i,title:s.title,kind:s.kind,page:pageOffset(i)}))};}
function notify(type='state',payload=state()){
 const data={channel:'courseware',version:1,type,payload};
 if(window.parent!==window)window.parent.postMessage(data,'*');
 window.dispatchEvent(new CustomEvent('courseware:'+type,{detail:payload}));
}
function ready(){if(disposed)return;notify('ready',{title:manifest.title,voiceMode:manifest.voice_mode,duration:manifest.quality?.audio_duration_seconds,reading:manifest.reading,variants:manifest.variants||[],variantLayouts:window.COURSEWARE.variantLayouts||[],resources:manifest.resources||[],questionLayout,images:manifest.question.images||[],state:state()});}
function message(text){notify('error',{message:text});}
function cancelPageAnimations(){pageAnimations.forEach(animation=>animation.cancel());pageAnimations=[];}
function syncMotion(){
 document.body.classList.toggle('motion-enabled',motion&&!reducedMotion.matches);
 if(!motion||reducedMotion.matches)cancelPageAnimations();
}
function setMotion(value){motion=value!==false;syncMotion();notify();}
function animatePage(){
 const key=`${index}/${originalPart}`;
 if(key===lastAnimatedPage)return;
 lastAnimatedPage=key;cancelPageAnimations();
 if(!motion||reducedMotion.matches||document.hidden)return;
 const nodes=$('stage').querySelectorAll('.slide-kicker,.slide-heading,.key-point,.flow-step,.teaching-figure,.q-main,.q-choice,.q-subquestion');
 nodes.forEach((node,i)=>{
  // Opacity leaves measured geometry intact: formulas, pagination and flow
  // connectors use the same positions throughout the entrance animation.
  if(node.animate)pageAnimations.push(node.animate([{opacity:0},{opacity:1}],
   {duration:320,delay:Math.min(i,6)*55,easing:'ease-out',fill:'backwards'}));
 });
}
reducedMotion.addEventListener('change',()=>{syncMotion();notify();});
syncMotion();
function escape(text){return String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function math(node){if(window.renderMathInElement)renderMathInElement(node,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},{left:'\\(',right:'\\)',display:false},{left:'\\[',right:'\\]',display:true}],throwOnError:false,trust:false,strict:'ignore'});}
function prose(text){return escape(text||'').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');}
function keyPoints(text){
 const chain=text.split('→').map(t=>t.trim());
 if(!/[\n$\\]/.test(text)&&chain.length>=3&&chain.length<=6&&chain.every(t=>t.length>0&&t.length<=30)){
  return `<div class="key-points concept-flow">${chain.map((line,i)=>`<div class="key-point concept-step"><span class="concept-index">${String(i+1).padStart(2,'0')}</span><div>${prose(line)}</div></div>`).join('')}</div>`;
 }
 const rows=[''];for(const token of text.match(/\$\$[\s\S]*?\$\$|\$[^$\n]+\$|[^$]+|\$/g)||[]){if(token.startsWith('$'))rows[rows.length-1]+=token;else{const parts=token.split('\n');rows[rows.length-1]+=parts.shift();rows.push(...parts)}}return `<div class="key-points">${rows.filter(x=>x.trim()).map((line,i)=>`<div class="key-point" style="--reveal-order:${i}">${prose(line)}</div>`).join('')}</div>`;
}
function flow(slide){return `<div class="flow-chart"><svg class="flow-connectors" aria-hidden="true"><defs><marker id="flow-arrow-${slide.id}" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7" fill="#719579"/></marker></defs><g></g></svg><ol class="step-flow flow-count-${slide.flow.length}" aria-label="解题流程">${slide.flow.map((step,i)=>`<li class="flow-step" data-flow-node="${i}"><span class="flow-number">${String(step.number).padStart(2,'0')}</span><div><h2>${escape(step.title.replace(/^(?:第?\s*步骤?\s*\d+|第\s*\d+\s*步)[：:·.\s-]*/,''))}</h2><div>${prose(step.display)}</div></div></li>`).join('')}</ol></div>`;}
function questionContent(slide=slides[index]){
 if(slide.variant){const v=manifest.variants[slide.variant_index];return window.QuestionView.render(window.COURSEWARE.variantLayouts[slide.variant_index],v.images||[],{imageLabel:'变式配图'});}
 return window.QuestionView.render(questionLayout,manifest.question.images||[]);
}
function currentImages(){return slides[index].variant?(manifest.variants[slides[index].variant_index].images||[]):(manifest.question.images||[]);}
function isQuestion(slide){return slide.original||slide.variant;}
function teachingFooter(){const target=manifest.teaching_target||manifest.question;return target.target_grade?target.target_grade:(manifest.question.grades?.length?manifest.question.grades:manifest.question.source_grades?.length?manifest.question.source_grades:[manifest.question.grade]).filter(Boolean).join('、')||'年级未标注';}
function body(slide){
 const d=slide.variant?null:slide.diagram,compactFigure=!!(slide.flow&&d?.kind==='process');
 return `<div class="slide-kicker">${escape(slide.kind)} / ${escape(manifest.question.subject)}</div><div class="slide-heading"><h1>${escape(slide.title)}</h1>${slide.variant?`<button class="variant-answer-button" data-variant-answer="${slide.variant_index}">查看答案</button>`:''}${compactFigure?`<button class="flow-figure-button" data-diagram-page="${slides.indexOf(slide)}">查看配图 ↗</button>`:''}</div><div class="slide-body ${d&&!compactFigure?'with-diagram':''} ${slide.flow?'with-flow':''} ${isQuestion(slide)?'with-original':''}">${isQuestion(slide)?questionContent(slide):slide.flow?flow(slide):slide.image?`<img src="${escape(slide.image)}" alt="原题配图">`:keyPoints(slide.display)}${d&&!compactFigure?`<figure class="teaching-figure"><button class="diagram-zoom" data-diagram-page="${slides.indexOf(slide)}" title="放大知识示意图"><img src="${escape(d.svg)}" alt="${escape(d.alt)}"></button><figcaption>${prose(d.caption)}</figcaption></figure>`:''}</div><div class="slide-footer"><span>${escape(teachingFooter())} · ${escape(manifest.question.subject)}</span><span>${String(slides.indexOf(slide)+1).padStart(2,'0')} / ${String(slides.length).padStart(2,'0')}</span></div>`;
}
function partCount(n){return originalPages.get(n)?.pages.length||1;}
function pageOffset(n){return slides.slice(0,n).reduce((sum,_,i)=>sum+partCount(i),0);}
function updatePagination(){
 const total=pageOffset(slides.length),current=pageOffset(index)+originalPart;
 const footer=$('stage').querySelector('.slide-footer>span:last-child');
 if(footer)footer.innerHTML=(partCount(index)>1?`<span class="question-part">${slides[index].original?'原题':'本节'} ${originalPart+1} / ${partCount(index)}</span>`:'')+`${String(current+1).padStart(2,'0')} / ${String(total).padStart(2,'0')}`;
 notify();
}
function showOriginalPart(n){
 const layout=originalPages.get(index);if(!layout)return;
 originalPart=Math.max(0,Math.min(layout.pages.length-1,n));
 const content=$('stage').querySelector('.slide-body'),original=isQuestion(slides[index]);
 content.innerHTML=layout.pages[originalPart];content.classList.add(original?'question-paged':'content-paged');
 content.style.setProperty(original?'--question-font-size':'--content-font-size',`${original?layout.size/16:layout.size}rem`);
 window.SlideLayout.enlarge(content,original?layout.size/16:layout.size,original);
 window.SlideLayout.fitFigures(content);window.SlideLayout.contain(content);
 content.scrollTop=0;$('slide-viewport').scrollTop=0;
 updatePagination();
 requestAnimationFrame(animatePage);
}
function layoutOriginal(force=false){
 if(disposed)return;
 const content=$('stage').querySelector('.with-original');if(!content)return;
 const compact=document.body.classList.contains('compact-slide');
 const key=`${content.clientWidth}/${content.clientHeight}/${compact}/${getComputedStyle(document.documentElement).fontSize}`;
 if(force||originalPages.get(index)?.key!==key){
  content.innerHTML=questionContent();math(content);
  originalPages.set(index,{...window.QuestionPagination.fit(content,content.innerHTML,{compact}),key});
 }
 showOriginalPart(originalPart);
}
function layoutContent(){
 const content=$('stage').querySelector('.slide-body');if(isQuestion(slides[index])||!content.clientHeight)return;
 const compact=document.body.classList.contains('compact-slide'),root=parseFloat(getComputedStyle(document.documentElement).fontSize);
 content.style.setProperty('--body-height',`${content.clientHeight/root}rem`);
 const key=`${content.clientWidth}/${content.clientHeight}/${compact}/${root}`;
 if(originalPages.get(index)?.key!==key){
  const source=document.createElement('div');source.innerHTML=body(slides[index]);
  content.innerHTML=source.querySelector('.slide-body').innerHTML;math(content);
  originalPages.set(index,{...window.SlidePagination.fit(content,{compact}),key});
 }
 showOriginalPart(originalPart);
}
function layoutStage(){if(disposed)return;layoutOriginal();layoutContent();layoutFlow();}
function movePage(direction){
 const part=originalPart+direction;
 if(part>=0&&part<partCount(index))show(index,part);
 else if(direction>0&&index<slides.length-1)show(index+1);
 else if(direction<0&&index>0)show(index-1,partCount(index-1)-1);
}
function revealNode(step,ratio=0,force=false){
 if(!playing||paused||document.hidden)return;
 const header=$('slide-viewport').getBoundingClientRect().top;
 const footer=$('slide-viewport').getBoundingClientRect().bottom;
 for(let parent=step.parentElement;parent&&parent!==document.body;parent=parent.parentElement){
  if(parent.scrollHeight<=parent.clientHeight+2||!/(auto|scroll)/.test(getComputedStyle(parent).overflowY))continue;
  const area=parent.getBoundingClientRect(),box=step.getBoundingClientRect();
  const scale=area.height/parent.offsetHeight||1;
  const top=Math.max(area.top+parent.clientTop*scale,header)+10;
  const bottom=Math.min(area.top+(parent.clientTop+parent.clientHeight)*scale,footer)-10;
  const available=bottom-top;if(available<=0)continue;
  const oversized=box.height>available;
  if(!force&&!oversized)continue;
  let delta=0;
  if(oversized)delta=box.top-top+Math.max(0,Math.min(1,ratio))*(box.height-available);
  else if(box.top<top||box.bottom>bottom)delta=(box.top+box.bottom-top-bottom)/2;
  if(Math.abs(delta)>2)parent.scrollTo({top:parent.scrollTop+delta/scale,behavior:motion&&!reducedMotion.matches&&!oversized?'smooth':'instant'});
 }
}
function focusNode(node,ratio=0,force=false){
 narrationRatio=ratio;
 if(playing&&!paused&&partCount(index)>1){
  const layout=originalPages.get(index);
  let next;
  if(slides[index].flow){
   const pages=layout.pages.map((html,p)=>html.includes(`data-flow-node="${node}"`)?p:-1).filter(p=>p>=0);
   if(pages.length&&node===slides[index].flow.length-1){
    for(let p=pages[pages.length-1]+1;p<layout.pages.length;p++){
     if(layout.pages[p].includes('teaching-figure')&&!layout.pages[p].includes('data-flow-node'))pages.push(p);
    }
   }
   next=pages[Math.min(pages.length-1,Math.floor(Math.max(0,ratio)*pages.length))]??-1;
  }else next=Math.min(partCount(index)-1,Math.floor(Math.max(0,ratio)*partCount(index)));
  if(next>=0&&next!==originalPart){showOriginalPart(next);layoutFlow();}
 }
 $('stage').querySelectorAll('.flow-step').forEach(step=>{
  const i=Number(step.dataset.flowNode);
  const wasCurrent=step.classList.contains('is-current');
  step.classList.toggle('is-current',i===node);step.classList.toggle('is-complete',i<node);
  if(i===node){step.setAttribute('aria-current','step');revealNode(step,ratio,force||!wasCurrent)}
  else step.removeAttribute('aria-current');
 });
 if(!slides[index].flow&&playing&&!paused&&!document.hidden){
  if(partCount(index)>1){
   const next=Math.min(partCount(index)-1,Math.floor(Math.max(0,ratio)*partCount(index)));
   if(next!==originalPart)showOriginalPart(next);
   return;
  }
  const body=$('stage').querySelector('.slide-body');
  if(body.scrollHeight>body.clientHeight+2&&/(auto|scroll)/.test(getComputedStyle(body).overflowY))body.scrollTo({top:Math.max(0,Math.min(1,ratio))*(body.scrollHeight-body.clientHeight),behavior:'instant'});
  revealNode(body,ratio,force);
 }
}
function layoutFlow(){
 const stage=$('stage'),body=stage.querySelector('.with-flow'),chart=stage.querySelector('.flow-chart');if(!body||!chart)return;
 const svg=chart.querySelector('.flow-connectors'),origin=chart.getBoundingClientRect(),scale=origin.width/chart.offsetWidth||1;
 svg.setAttribute('viewBox',`0 0 ${chart.offsetWidth} ${chart.offsetHeight}`);
 const boxes=[...chart.querySelectorAll('.flow-step')].map(e=>{const r=e.getBoundingClientRect();return {left:(r.left-origin.left)/scale,right:(r.right-origin.left)/scale,top:(r.top-origin.top)/scale,bottom:(r.bottom-origin.top)/scale}});
 svg.querySelector('g').innerHTML=boxes.slice(1).map((b,i)=>{
  const a=boxes[i];let path;
  if(Math.abs(a.top-b.top)<4){const y=(a.top+a.bottom)/2;path=`M ${a.right+3} ${y} H ${b.left-5}`}
  else{const x=(a.left+a.right)/2,x2=(b.left+b.right)/2,y=(a.bottom+b.top)/2;path=`M ${x} ${a.bottom+3} V ${y} H ${x2} V ${b.top-5}`}
  return `<path d="${path}" fill="none" stroke="#719579" stroke-width="1.7" marker-end="url(#flow-arrow-${slides[index].id})"/>`;
 }).join('');
 const active=chart.querySelector('.is-current');if(active)revealNode(active,narrationRatio,true);
}
function stop(){generation++;playing=false;paused=false;if(audio){audio.pause();audio=null}window.speechSynthesis?.cancel();$('stage').classList.remove('presenting','paused');focusNode(-1);notify();}
function show(n,part=0){
 if(!Number.isInteger(n)||!Number.isInteger(part))return;
 stop();index=Math.max(0,Math.min(slides.length-1,n));originalPart=Math.max(0,part);
 cancelPageAnimations();lastAnimatedPage='';
 const slide=slides[index];$('stage').className='slide kind-'+slide.kind+(isQuestion(slide)?' is-original':'');
 $('stage').innerHTML=body(slide);math($('stage'));$('slide-viewport').scrollTop=0;
 updatePagination();requestAnimationFrame(layoutStage);
}
function fitStage(){
 if(disposed)return;
 const viewport=$('slide-viewport'),mount=$('slide-mount'),stage=$('stage'),root=parseFloat(getComputedStyle(document.documentElement).fontSize);
 // Match the exported PPT: lay out once in a 16:9 rem canvas, then scale
 // text, formulas and figures together to fit the space owned by the host.
 const width=80*root,height=45*root,scale=Math.min(viewport.clientWidth/width,viewport.clientHeight/height);
 mount.style.width=`${width*scale}px`;mount.style.height=`${height*scale}px`;
 stage.style.setProperty('--original-stage-height','45rem');stage.style.transform=`scale(${scale})`;
 if(scale>0)window.SlideLayout.updateStage(stage,scale,width*scale);
 requestAnimationFrame(layoutStage);
}
new ResizeObserver(fitStage).observe($('slide-viewport'));
document.fonts?.ready.then(()=>{originalPages.forEach(layout=>layout.key='');fitStage();});
Promise.all([...(manifest.question.images||[]),...(manifest.variants||[]).flatMap(v=>v.images||[])].map(src=>{const image=new Image();image.src=src;return image.decode().catch(()=>{});})).then(()=>{originalPages.forEach(layout=>layout.key='');layoutOriginal(true);});

document.addEventListener('click',event=>{
 const answer=event.target.closest('[data-variant-answer]');
 if(answer){const variantIndex=Number(answer.dataset.variantAnswer);stop();if(answerHost)notify('answer',{variantIndex});else window.CoursewareAnswer.show(manifest.variants[variantIndex],variantIndex);return;}
 const source=event.target.closest('[data-image-index]'),figure=event.target.closest('[data-diagram-page]');if(!source&&!figure)return;
 const d=figure?slides[Number(figure.dataset.diagramPage)].diagram:null,label=slides[index].variant?'变式配图':'原题配图';
 const payload={src:d?d.svg:currentImages()[Number(source.dataset.imageIndex)],alt:d?d.alt:label,caption:d?d.caption:`${label} ${Number(source.dataset.imageIndex)+1}`};
 if(!payload.src)return;stop();
 if(figureHost){notify('figure',payload);return;}
 $('figure-image').src=payload.src;$('figure-image').alt=payload.alt;$('figure-caption').textContent=payload.caption;$('figure-dialog').showModal();
});
$('figure-close').onclick=()=>$('figure-dialog').close();
function finished(token){if(token!==generation)return;playing=false;paused=false;$('stage').classList.remove('presenting','paused');focusNode(-1);notify();if(autoNext&&index<slides.length-1){show(index+1);speak()}}
function startAudio(element,token){
 element.play().catch(error=>{
  // Pausing while the remote audio is buffering aborts play normally.
  if(token!==generation||element!==audio||paused)return;
  stop();message(error.name==='NotAllowedError'?'浏览器阻止了播放，请再次点击播放':'语音暂时无法播放，请重试。');
 });
}
function speak(){
 if(manifest.voice_mode==='none')return;
 if(playing){paused=!paused;$('stage').classList.toggle('paused',paused);notify();if(audio){if(paused)audio.pause();else startAudio(audio,generation)}else if(paused)speechSynthesis.pause();else speechSynthesis.resume();if(!paused){const active=$('stage').querySelector('.is-current');focusNode(active?Number(active.dataset.flowNode):-1,narrationRatio,true)}return}
 const slide=slides[index];playing=true;paused=false;const token=++generation;$('stage').classList.add('presenting');notify();
 const audioUrl=audioSources[slide.id]||slide.audio||slide.audio_file;
 if(audioUrl){audio=new Audio(audioUrl);const currentAudio=audio;const sync=(force=false)=>{if(token!==generation)return;const cue=(slide.cues||[]).find(c=>currentAudio.currentTime>=c.start_seconds&&currentAudio.currentTime<c.end_seconds);focusNode(cue?cue.node:-1,cue?(currentAudio.currentTime-cue.start_seconds)/(cue.end_seconds-cue.start_seconds):currentAudio.currentTime/(currentAudio.duration||1),force)};audio.ontimeupdate=()=>sync();audio.onseeked=()=>sync(true);audio.onended=()=>finished(token);audio.onerror=()=>{if(token===generation){stop();message('语音文件无法播放，请下载讲解包后重试')}};sync();startAudio(audio,token);return}
 if(!window.speechSynthesis){stop();message('当前浏览器没有设备朗读能力，请启用千问语音后重新生成');return}
 const sources=slide.flow?slide.flow.map((s,node)=>({text:s.narration,node})):[{text:slide.narration,node:-1}];const parts=sources.flatMap(s=>{let offset=0;return (s.text.match(/[\s\S]{1,180}(?:[。！？]|$)|[\s\S]{1,180}/g)||[s.text]).map(text=>{const part={text,node:s.node,offset,total:s.text.length};offset+=text.length;return part})});let i=0;
 function next(){if(token!==generation)return;if(i>=parts.length){finished(token);return}const part=parts[i++];focusNode(part.node,part.offset/part.total,true);const utterance=new SpeechSynthesisUtterance(part.text);utterance.lang='zh-CN';utterance.rate=.95;const voice=speechSynthesis.getVoices().find(v=>v.lang.startsWith('zh'));if(voice)utterance.voice=voice;utterance.onboundary=e=>{if(token===generation)focusNode(part.node,(part.offset+(e.charIndex||0))/part.total)};utterance.onend=next;utterance.onerror=event=>{if(token===generation&&event.error!=='canceled'&&event.error!=='interrupted'){stop();message('设备语音不可用，请配置千问语音')}};speechSynthesis.speak(utterance)}next();
}
function seek(page){if(!Number.isInteger(page))return;page=Math.max(0,Math.min(pageOffset(slides.length)-1,page));let n=0;while(n<slides.length-1&&page>=partCount(n)){page-=partCount(n);n++;}show(n,page);}
function print(){stop();cancelPageAnimations();$('print-pages').innerHTML=slides.map(s=>`<section class="slide kind-${escape(s.kind)}">${body(s)}</section>`).join('');math($('print-pages'));window.print();}
const commands={init:config=>{if(typeof config?.figureHost==='boolean')figureHost=config.figureHost;if(typeof config?.answerHost==='boolean')answerHost=config.answerHost;if(config?.audio&&typeof config.audio==='object')audioSources=config.audio;if(typeof config?.motion==='boolean')setMotion(config.motion);if(config?.announce!==false)ready();},next:()=>movePage(1),previous:()=>movePage(-1),goTo:show,seek,play:speak,stop,print,setMotion,setAutoPlay:value=>{autoNext=value===true;notify();},getState:()=>notify()};
window.CoursewareContent=Object.freeze({...commands,getState:state,destroy:()=>{stop();disposed=true;cancelPageAnimations();}});
window.addEventListener('message',event=>{const data=event.data;if(event.source!==window.parent||window.parent===window||data?.channel!=='courseware'||data.version!==1||data.type!=='command'||!Object.hasOwn(commands,data.command))return;commands[data.command](data.value);});
document.addEventListener('keydown',event=>{if(document.querySelector('dialog[open]')||['INPUT','BUTTON','TEXTAREA'].includes(event.target.tagName))return;if(event.key==='ArrowRight'){event.preventDefault();movePage(1)}if(event.key==='ArrowLeft'){event.preventDefault();movePage(-1)}});
window.addEventListener('pagehide',stop);document.addEventListener('visibilitychange',()=>{if(document.hidden)stop()});
show(0);fitStage();requestAnimationFrame(ready);
})();
