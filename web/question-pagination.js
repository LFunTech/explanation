/* Measure the rendered question, including formulas and images, before paging it. */
(()=>{
function fit(body,html,{compact=false}={}){
    body.classList.remove('question-paged');
    body.innerHTML=html;
    const height=body.clientHeight-2;
    const root=parseFloat(getComputedStyle(document.documentElement).fontSize);
    body.style.setProperty('--question-image-height',`${Math.max(60,Math.min(compact?180:230,height*.55))/root}rem`);
    const policy=window.SlideLayout.typography(body),sizes=policy.sizes.map(size=>size*16);
    const fits=()=>window.SlideLayout.fits(body);
    for(const size of sizes){
        body.style.setProperty('--question-font-size',`${size/16}rem`);
        if(fits())return {pages:[body.innerHTML],size};
    }
    // Keep readable type on continuation pages, instead of shrinking indefinitely.
    const size=policy.minimum*16;
    body.style.setProperty('--question-font-size',`${size/16}rem`);
    body.querySelectorAll('.q-choices').forEach(row=>row.dataset.columns=getComputedStyle(row).gridTemplateColumns.split(' ').length);
    const source=body.querySelector('.question-document').cloneNode(true);
    const sheet=source.cloneNode(false);body.replaceChildren(sheet);
    const pages=[];
    const flush=()=>{if(sheet.childElementCount){pages.push(sheet.outerHTML);sheet.replaceChildren();}};
    const proseSelector='.q-stem,.q-sub-content>.q-prose,.q-choice-content>.q-prose';
    function append(block){
        sheet.append(block);
        if(fits())return;
        block.remove();
        if(sheet.childElementCount){flush();sheet.append(block);if(fits())return;block.remove();}
        if(block.classList.contains('q-choices')&&block.children.length>1){
            for(const option of [...block.children]){const row=block.cloneNode(false);row.classList.add('q-long-options');row.append(option.cloneNode(true));append(row);}
            return;
        }
        const nestedChoices=block.querySelector('.q-subquestion .q-choices');
        if(nestedChoices){
            const continuation=block.cloneNode(true);
            nestedChoices.remove();append(block);
            continuation.querySelector('.q-sub-content>.q-prose')?.remove();
            const label=continuation.querySelector('.q-sub-label');
            if(label)label.insertAdjacentHTML('beforeend','<small class="q-continuation">续</small>');
            // Option rows have their own page boundaries, with no repeated prompt.
            const options=continuation.querySelector('.q-choices');
            for(const option of options.children){const row=options.cloneNode(false);row.classList.add('q-long-options');row.append(option.cloneNode(true));append(row);}
            return;
        }
        // Split a long text block at rendered text boundaries; KaTeX nodes stay atomic.
        const prose=block.matches(proseSelector)?block:block.querySelector(proseSelector);
        if(prose&&prose.textContent.length){
            const tokens=[];
            for(const node of prose.childNodes){
                if(node.nodeType===Node.TEXT_NODE)tokens.push(...Array.from(node.textContent).map(c=>document.createTextNode(c)));
                else tokens.push(node.cloneNode(true));
            }
            if(tokens.length>1){
                let offset=0,continued=false;
                while(offset<tokens.length){
                    const piece=block.cloneNode(true),target=piece.matches(proseSelector)?piece:piece.querySelector(proseSelector);
                    if(continued){const label=piece.querySelector('.q-sub-label');if(label)label.insertAdjacentHTML('beforeend','<small class="q-continuation">续</small>');}
                    target.replaceChildren();sheet.append(piece);
                    let low=1,high=tokens.length-offset,best=0;
                    while(low<=high){
                        const count=Math.floor((low+high)/2);
                        target.replaceChildren(...tokens.slice(offset,offset+count).map(n=>n.cloneNode(true)));
                        if(fits()){best=count;low=count+1;}else high=count-1;
                    }
                    if(!best){piece.remove();break;}
                    target.replaceChildren(...tokens.slice(offset,offset+best).map(n=>n.cloneNode(true)));
                    offset+=best;continued=true;
                    if(offset<tokens.length)flush();
                }
                if(offset===tokens.length)return;
                // An unusually tall indivisible formula is kept in its own page.
                const remainder=block.cloneNode(true),target=remainder.matches(proseSelector)?remainder:remainder.querySelector(proseSelector);
                target.replaceChildren(...tokens.slice(offset).map(n=>n.cloneNode(true)));block=remainder;
            }
        }
        sheet.append(block);
        // Images/formulas cannot be cut between pages. Scale this exceptional page
        // to its available height, preserving the complete original and zoom action.
        if(!fits()){
            const ratio=Math.min(height/sheet.scrollHeight,body.clientWidth/sheet.scrollWidth)*.97;
            sheet.style.zoom=String(Math.min(1,ratio));
            flush();sheet.style.zoom='';
        }
    }
    for(const child of source.children){
        if(child.classList.contains('q-main')){
            sheet.append(child.cloneNode(true));
            if(fits())continue;
            sheet.lastElementChild.remove();
            // Keep stem and image together whenever possible; split only if even
            // an otherwise empty page cannot contain the combined block.
            if(sheet.childElementCount)flush();
            const probe=child.cloneNode(true);sheet.append(probe);
            if(fits())continue;
            probe.remove();
            for(const part of child.children)append(part.cloneNode(true));
        }else if(child.classList.contains('q-subquestions')){
            for(const sub of child.children){
                const wrapper=child.cloneNode(false);wrapper.append(sub.cloneNode(true));append(wrapper);
            }
        }else if(child.classList.contains('q-choices')){
            // Pack whole option rows, so A/B and C/D stay aligned across pages.
            const columns=Number(child.dataset.columns)||2;
            const options=[...child.children];
            for(let i=0;i<options.length;i+=columns){const row=child.cloneNode(false);row.append(...options.slice(i,i+columns).map(n=>n.cloneNode(true)));append(row);}
        }else append(child.cloneNode(true));
    }
    flush();body.innerHTML=pages[0];body.classList.add('question-paged');
    return {pages,size};
}
window.QuestionPagination={fit};
})();
