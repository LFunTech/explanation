/* Shared by standalone content and the outer player, outside the slide canvas. */
(()=>{
const dialog=document.createElement('dialog');dialog.id='answer-dialog';dialog.setAttribute('aria-labelledby','answer-heading');
dialog.innerHTML='<article class="answer-panel"><header><div><span class="answer-eyebrow">练习反馈</span><h2 id="answer-heading"></h2></div><button class="answer-close" type="button" aria-label="关闭答案" title="返回题目">×</button></header><main><section class="answer-summary"><span class="answer-check" aria-hidden="true">✓</span><div><h3>正确答案</h3><div class="answer-result"></div></div></section><section class="answer-explanation"><h3>解题解析</h3><div class="answer-analysis"></div></section></main><footer><span>理解每一步的依据，再回到题目试一试。</span><button class="answer-back" type="button">返回题目</button></footer></article>';
document.body.append(dialog);
const notifyOverlay=()=>{if(window.parent!==window)window.parent.postMessage({channel:'courseware',version:1,type:'overlay',payload:{name:'answer',open:dialog.open}},'*');};
dialog.querySelectorAll('button').forEach(button=>button.onclick=()=>dialog.close());
dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close();});
dialog.addEventListener('close',notifyOverlay);
function analysis(text){
 const root=dialog.querySelector('.answer-analysis');root.replaceChildren();
 // Preserve the original wording and formulas. Source headings become visual
 // sections; no model rewrite or answer inference happens in the renderer.
 for(const part of (text||'暂无解析').split(/(【[^】\n]{1,12}】)/g).filter(t=>t.trim())){
  const heading=/^【[^】\n]{1,12}】$/.test(part),node=document.createElement(heading?'h4':'p');
  node.textContent=heading?part.slice(1,-1):part.trim();root.append(node);
 }
}
function show(variant,index){
 if(!variant||!Number.isInteger(index)||index<0)return;
 dialog.querySelector('h2').textContent=`变式题 ${index+1} · 答案与解析`;
 dialog.querySelector('.answer-result').textContent=variant.answer||'暂无答案';analysis(variant.analysis);
 window.renderMathInElement?.(dialog.querySelector('main'),{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},{left:'\\(',right:'\\)',display:false},{left:'\\[',right:'\\]',display:true}],throwOnError:false,trust:false,strict:'ignore'});
 if(!dialog.open)dialog.showModal();dialog.querySelector('main').scrollTop=0;notifyOverlay();
}
window.CoursewareAnswer=Object.freeze({show});
})();
