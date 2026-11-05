/* Lay out in the fixed 80rem × 45rem canvas; the host only scales the result. */
(()=>{
function scale(){return Number(document.getElementById('stage')?.style.getPropertyValue('--display-scale'))||1;}
function typography(body){
 const text=body.cloneNode(true);text.querySelectorAll('.katex-mathml,figcaption').forEach(n=>n.remove());
 const count=text.textContent.replace(/\s/g,'').length;
 const blocks=body.querySelectorAll('.key-point,.flow-step').length;
 const choices=body.querySelectorAll('.q-choice').length;
 const sparse=count<180&&blocks<=3&&choices<=4&&!body.querySelector('.teaching-figure,.q-figures,.q-image');
 const balanced=count<240&&blocks<=8&&!body.querySelector('.teaching-figure,.q-figures,.q-image');
 body.classList.toggle('sparse-content',sparse);
 body.classList.toggle('balanced-content',balanced);
 const preferred=sparse?(body.classList.contains('with-original')?2.75:body.querySelectorAll('.flow-step').length>1?2.5:3.25):balanced?2.625:2.25;
 const minimum=1.75;
 const sizes=[];
 for(let size=preferred;size>minimum+.04;size-=.125)sizes.push(size);
 sizes.push(minimum);
 return {sizes,minimum,sparse};
}
function fits(body){
 if(body.scrollHeight>body.clientHeight+1||body.scrollWidth>body.clientWidth+1)return false;
 const box=body.getBoundingClientRect(),tolerance=scale();
 // Nested math must not hide overflow in its own horizontally scrolling span.
 return [...body.querySelectorAll('.katex-html .base,.katex-display,img')].every(node=>{
  const rect=node.getBoundingClientRect();
  return rect.width===0||(rect.left>=box.left-tolerance&&rect.right<=box.right+tolerance&&rect.top>=box.top-tolerance&&rect.bottom<=box.bottom+tolerance);
 });
}
function fitFigures(body){
 const root=parseFloat(getComputedStyle(document.documentElement).fontSize);
 body.querySelectorAll('.teaching-figure').forEach(figure=>{
  const caption=figure.querySelector('figcaption'),image=figure.querySelector('img');
  if(!image)return;
  const margin=caption?parseFloat(getComputedStyle(caption).marginTop):0;
  const available=Math.max(1,body.clientHeight-(caption?.offsetHeight||0)-margin-2);
  image.style.maxHeight=`${Math.min(355,available)/root}rem`;
 });
}
function contain(body){
 if(fits(body))return;
 // Only indivisible oversized formulas/figures reach this fallback. Preserve
 // their full-width grid before scaling, otherwise a wrapper becomes one column.
 const style=getComputedStyle(body),wrapper=document.createElement('div');
 Object.assign(wrapper.style,{width:`${body.clientWidth}px`,gridColumn:'1 / -1',minWidth:'0',
  display:style.display==='grid'?'grid':'block',gridTemplateColumns:style.gridTemplateColumns,
  gap:style.gap,alignItems:style.alignItems});
 wrapper.append(...body.childNodes);body.append(wrapper);
 let ratio=Math.min(1,body.clientHeight/wrapper.scrollHeight,body.clientWidth/wrapper.scrollWidth)*.97;
 wrapper.style.zoom=String(ratio);
 for(let attempt=0;attempt<2&&!fits(body);attempt++){
  ratio*=Math.min(1,body.clientHeight/body.scrollHeight,body.clientWidth/body.scrollWidth)*.95;
  wrapper.style.zoom=String(ratio);
 }
}
function enlarge(body,minimum,original=false){
 const property=original?'--question-font-size':'--content-font-size';
 const policy=typography(body);
 // An indivisible oversized formula/image has already been fitted separately.
 if(body.querySelector('[style*="zoom"]'))return;
 for(const size of policy.sizes.filter(size=>size>=minimum)){
  body.style.setProperty(property,`${size}rem`);
  if(fits(body))return;
 }
 body.style.setProperty(property,`${minimum}rem`);
}
function updateStage(stage,factor){
 stage.style.setProperty('--display-scale',String(factor));
}
window.SlideLayout={typography,fits,fitFigures,contain,enlarge,updateStage,scale};
})();
