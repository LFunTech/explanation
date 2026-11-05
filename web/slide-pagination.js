/* Fit rendered text and formulas first; paginate complete blocks only when needed. */
(()=>{
function fit(body,{compact=false}={}){
 window.SlideLayout.fitFigures(body);
 const original=body.innerHTML;
 const fits=()=>window.SlideLayout.fits(body);
 const {sizes,minimum}=window.SlideLayout.typography(body);
 for(const size of sizes){
  body.style.setProperty('--content-font-size',`${size}rem`);
  if(fits())return {pages:[body.innerHTML],size};
 }
 const size=minimum;
 body.style.setProperty('--content-font-size',`${size}rem`);
 const source=document.createElement('div');source.innerHTML=original;
 const selector=source.querySelector('.step-flow')?'.step-flow':'.key-points';
 const blocks=[...(source.querySelector(selector)?.children||[])];
 if(!blocks.length){
  window.SlideLayout.contain(body);
  return {pages:[body.innerHTML],size};
 }
 source.querySelector(selector).replaceChildren();
 const template=source.innerHTML,pages=[];
 const reset=()=>{body.innerHTML=template;return body.querySelector(selector);};
 let sheet=reset();
 const flush=()=>{if(sheet.childElementCount){pages.push(body.innerHTML);sheet=reset();}};
 function append(block){
  sheet.append(block);
  if(fits())return;
  block.remove();
  if(sheet.childElementCount){flush();sheet.append(block);if(fits())return;block.remove();}
  // A long paragraph is split by text nodes; rendered KaTeX stays indivisible.
  const target=block.matches('.key-point')?block:block.querySelector(':scope > div > div');
  if(target){
   const tokens=[...target.childNodes].flatMap(n=>n.nodeType===Node.TEXT_NODE?Array.from(n.textContent).map(c=>document.createTextNode(c)):[n.cloneNode(true)]);
   let offset=0;
   while(offset<tokens.length){
    const piece=block.cloneNode(true),text=piece.matches('.key-point')?piece:piece.querySelector(':scope > div > div');
    text.replaceChildren();sheet.append(piece);
    let low=1,high=tokens.length-offset,best=0;
    while(low<=high){
     const count=Math.floor((low+high)/2);
     text.replaceChildren(...tokens.slice(offset,offset+count).map(n=>n.cloneNode(true)));
     if(fits()){best=count;low=count+1;}else high=count-1;
    }
    if(!best){piece.remove();break;}
    text.replaceChildren(...tokens.slice(offset,offset+best).map(n=>n.cloneNode(true)));
    offset+=best;
    if(offset<tokens.length)flush();
   }
   if(offset===tokens.length)return;
   target.replaceChildren(...tokens.slice(offset).map(n=>n.cloneNode(true)));
  }
  sheet.append(block);
  // Keep an exceptionally tall indivisible formula complete, with no cropping.
  if(!fits()){
   window.SlideLayout.contain(body);pages.push(body.innerHTML);sheet=reset();
  }
 }
 blocks.forEach(append);flush();body.innerHTML=pages[0];
 return {pages,size};
}
window.SlidePagination={fit};
})();
