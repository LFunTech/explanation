/* Shared, escaped rendering for the editor, question search and offline player. */
(()=>{
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Keep an answer blank together, without inserting markup inside a formula.
const prose=value=>escape(value).replace(/(\$\$[\s\S]*?\$\$|\$[^$\n]*\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\])|([（(][ \u3000]*[）)])/g,(match,math,blank)=>math||`<span class="q-answer-blank">${blank}</span>`);
function render(layout,images=[],{interactive=true,imageLabel='原题配图'}={}){
    function image(index,label){
        if(!images[index])return '';
        const content=`<img src="${escape(images[index])}" alt="${escape(label)}" loading="eager">`;
        return interactive?`<button type="button" class="q-image" data-image-index="${index}" title="放大${escape(label)}">${content}</button>`:`<div class="q-image">${content}</div>`;
    }
    function choices(items){
        if(!items?.length)return '';
        const illustrated=items.some(c=>Number.isInteger(c.image_index));
        const long=items.some(c=>c.text?.length>100||c.text?.includes('\n')||/\$\$|\\\[/.test(c.text||''));
        return `<ol class="q-choices${illustrated?' q-image-options':''}${long?' q-long-options':''}" aria-label="${illustrated?'图片选项':'题目选项'}">${items.map(c=>`<li class="q-choice"><span class="q-choice-label">${escape(c.label)}.</span><div class="q-choice-content">${c.text?`<div class="q-prose">${prose(c.text)}</div>`:''}${Number.isInteger(c.image_index)?image(c.image_index,`选项 ${c.label} 的原图`):!c.text?'<span class="q-image-reference">见原题配图</span>':''}</div></li>`).join('')}</ol>`;
    }
    const gallery=(layout.image_indices||[]).filter(i=>images[i]);
    const main=`<div class="q-main${gallery.length?' q-with-images':''}"><div class="q-stem q-prose">${prose(layout.stem)}</div>${gallery.length?`<div class="q-figures${gallery.length>1?' q-multiple-images':''}">${gallery.map(i=>`<figure>${image(i,`${imageLabel} ${i+1}`)}<figcaption>${escape(imageLabel)} ${i+1}</figcaption></figure>`).join('')}</div>`:''}</div>`;
    const options=layout.choices||layout.options?.map((text,i)=>({label:String.fromCharCode(65+i),text}))||[];
    const subs=(layout.sub_questions||[]).map(sub=>`<section class="q-subquestion"><span class="q-sub-label">（${escape(sub.id)}）</span><div class="q-sub-content"><div class="q-prose">${prose(sub.stem)}</div>${choices(sub.choices)}</div></section>`).join('');
    return `<div class="question-document">${main}${choices(options)}${subs?`<div class="q-subquestions">${subs}</div>`:''}</div>`;
}
window.QuestionView={render};
})();
