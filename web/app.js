import { renderApiDocs } from './api-docs.js?v=20261105-player-docs-v20';
const scopeFields=['curriculum_version','school_system','publisher','textbook_version','volume'];
function teachingScope(prefix){return Object.fromEntries(scopeFields.map(k=>[k,document.getElementById(prefix+'-'+k).value.trim()]))}
const $=id=>document.getElementById(id);
renderApiDocs($('view-api-docs'));
const state={editingCoursewareId:null,jobPage:1,jobPageSize:20,jobList:null,config:{},randomQuestions:[],jobs:[],images:[],source:null,imageRequest:0,imagesLoading:false,requiresImages:false,gradeSelectionManual:false,view:'create',exam:[],kb:null};
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function questionGrades(question){return question.grades?.length?question.grades:question.source_grades?.length?question.source_grades:question.grade?[question.grade]:[]}
function gradeLabel(question){return questionGrades(question).join('、')||'年级未标注'}
function selectedGrades(){return [...$('grade-options').querySelectorAll('input:checked')].map(input=>input.value)}
function updateGradeSummary(){const text=selectedGrades().join('、')||'跟随题库年级';$('grade-summary').textContent=text;$('grade-summary').title=text}
function setGrades(grades){
    for(const grade of grades){
        if([...$('grade-options').querySelectorAll('input')].some(input=>input.value===grade))continue;
        const label=document.createElement('label'),input=document.createElement('input'),span=document.createElement('span');
        input.type='checkbox';input.value=grade;input.dataset.grade='';span.textContent=grade;label.append(input,span);$('grade-options').append(label);
    }
    $('grade-options').querySelectorAll('input').forEach(input=>input.checked=grades.includes(input.value));updateGradeSummary();$('grade-picker').open=false;
}
function examParams(extra={}){const params=new URLSearchParams({subject:$('subject').value,region:$('region').value.trim()||'上海',...extra});selectedGrades().forEach(grade=>params.append('grades',grade));return params}
$('grade').addEventListener('change',()=>{state.gradeSelectionManual=true;updateGradeSummary()});
$('grade-clear').onclick=()=>{setGrades([]);$('grade-picker').open=true;$('grade').dispatchEvent(new Event('input',{bubbles:true}));$('grade').dispatchEvent(new Event('change',{bubbles:true}))};
document.addEventListener('click',event=>{if(!$('grade').contains(event.target))$('grade-picker').open=false});
$('grade').addEventListener('keydown',event=>{if(event.key==='Escape'){$('grade-picker').open=false;$('grade-picker').querySelector('summary').focus();event.preventDefault()}});
const split=value=>value.split(/[,，、\n]/).map(s=>s.trim()).filter(Boolean);
const targetGradeNames=[...$('target-grade').options].map(option=>option.value).filter(Boolean);
const targetGradeAliases=Object.fromEntries(targetGradeNames.map((name,i)=>['G'+(i+1),name]));
Object.assign(targetGradeAliases,{'初一':'七年级','初二':'八年级','初三':'九年级','十年级':'高一','十一年级':'高二','十二年级':'高三'});
function readTargetKnowledge(){
    const raw=$('target-curriculum').value.trim();
    if(!raw)return [];
    let values;try{values=JSON.parse(raw)}catch{throw new Error('目标课标 JSON 格式不正确，请检查括号、引号和逗号')}
    const points=normalizeTargetKnowledge(values);if(points.some(p=>p.grade&&p.grade!==$('target-grade').value))throw new Error('目标课标只能使用'+$('target-grade').value+'，请重新分析或移除其他年级条目');return points;
}
function normalizeTargetKnowledge(values){
    if(!Array.isArray(values))throw new Error('目标课标 JSON 须为数组，每项关联一个知识点和它的课标要求');
    return values.map((point,i)=>{
        if(!point||typeof point!=='object'||Array.isArray(point)||typeof point.knowledge_point!=='string'||!point.knowledge_point.trim())throw new Error(`第 ${i+1} 项须填写 knowledge_point`);
        const requirements=point.curriculum_requirements??[];
        if(!Array.isArray(requirements)||requirements.some(r=>typeof r!=='string'||!r.trim()))throw new Error(`第 ${i+1} 项 curriculum_requirements 须为非空文字组成的数组`);
        let value=point.grade;
        if(value===undefined){
            const legacy=[...new Set((Array.isArray(point.grades)?point.grades:[]).filter(g=>typeof g==='string').map(g=>targetGradeAliases[g.trim()]||g.trim()).filter(Boolean))];
            value=legacy.length===1?legacy[0]:'';
        }
        if(typeof value!=='string')throw new Error(`第 ${i+1} 项 grade 只能是一个年级`);
        const grade=targetGradeAliases[value.trim()]||value.trim();
        if(grade&&!targetGradeNames.includes(grade))throw new Error(`第 ${i+1} 项年级须为一年级至高三`);
        if(point.grade_source&&!['knowledge_base','llm','manual','unknown'].includes(point.grade_source))throw new Error(`第 ${i+1} 项 grade_source 不正确`);
        const gradeSource=grade?(point.grade_source&&point.grade_source!=='unknown'?point.grade_source:'manual'):'unknown';
        return {knowledge_point:point.knowledge_point.trim(),grade,grade_source:gradeSource,curriculum_requirements:[...new Set(requirements.map(r=>r.trim()))]};
    });
}
function currentQuestion({includeTargets=true}={}){
    let subQuestions=[];
    if($('sub-questions').value.trim()){try{subQuestions=JSON.parse($('sub-questions').value)}catch{throw new Error('小题结构 JSON 格式不正确')}if(!Array.isArray(subQuestions))throw new Error('小题结构须为 JSON 数组')}
    return {target_grade:$('target-grade').value,target_semester:$('target-semester').value,...(includeTargets?{target_curriculum:readTargetKnowledge()}:{}),question_id:$('question-id').value.trim()||'q-'+crypto.randomUUID(),stem:$('stem').value.trim(),subject:$('subject').value,grade:selectedGrades()[0]||'',grades:selectedGrades(),source_grades:state.source?.source_grades||[],region:$('region').value.trim(),knowledge_points:split($('knowledge-points').value),knowledge_point_ids:state.source?.knowledge_point_ids||[],source:state.source?.source||'',requires_images:state.requiresImages,question_type:$('question-type').value,difficulty:$('difficulty').value,options:$('options').value.split('\n').map(x=>x.trim()).filter(Boolean),images:state.images,sub_questions:subQuestions,answer:$('answer').value.trim(),analysis:$('analysis').value.trim()}
}
function targetKey(point){return JSON.stringify([point.knowledge_point,point.grade||''])}
const targetChoices=new Map();
const targetGenerated=new Map();
const targetCitations=new Map();
let targetCatalog=[],targetCatalogRequest=null,targetAnalysisRequest=null,targetSearchTimer,targetCatalogScope='';
function fitTargetRequirements(){document.querySelectorAll('[data-target-requirements]').forEach(input=>{input.style.height='auto';input.style.height=(input.scrollHeight+2)+'px'})}
window.addEventListener('resize',fitTargetRequirements);
function markTargetsEdited(){if($('mode').value==='sample')$('mode').value='qwen'}
function targetStatus(message,error=false){const node=$('target-analysis-status');node.hidden=!message;node.textContent=message;node.classList.toggle('is-error',error)}
function setTargetKnowledge(points){
    points=normalizeTargetKnowledge(points).filter(p=>!p.grade||p.grade===$('target-grade').value);
    $('target-curriculum').value=points.length?JSON.stringify(points,null,2):'';
    points.forEach(p=>targetChoices.set(targetKey(p),p));
    markTargetsEdited();renderTargetKnowledge();
}
function safeTargetKnowledge(){try{return readTargetKnowledge()}catch{return []}}
function targetGradeTags(point){
    const source={knowledge_base:'库内课标匹配',llm:'旧版 LLM 建议',manual:'手动填写'}[point.grade_source]||'手动标注';
    return `<span class="target-grade-tags">${point.grade?`<span class="target-grade-tag">${esc(point.grade)}</span><small class="target-grade-source">${source}</small>`:'<span class="target-grade-tag is-unknown">请选择一个对应年级</span>'}</span>`;
}
function targetCitation(point){
    const entry=targetCitations.get(targetKey(point));
    if(!entry?.sources?.length)return '';
    const edited=JSON.stringify(entry.curriculum_requirements)!==JSON.stringify(point.curriculum_requirements);
    const links=entry.sources.map(source=>{
        const label=[source.title,source.stage,source.printed_page?`正文第 ${source.printed_page} 页`:'',source.page?`PDF 第 ${source.page} 页`:''].filter(Boolean).join(' · ');
        let url='';try{const parsed=new URL(source.source);if(['http:','https:'].includes(parsed.protocol)){parsed.hash=source.page?'page='+source.page:'';url=parsed.href;}}catch{}
        return url?`<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)} ↗</a>`:esc(label);
    });
    return `<p class="target-citation">${edited?'已编辑；原始参考来源：':'课标原文来源：'}${[...new Set(links)].join('<br>')}<br>目标年级用于本次匹配；学段课标不等于该年级、学期的教材进度。</p>`;
}
function renderTargetOptions(){
    const query=$('target-search').value.trim().toLowerCase();
    const selected=safeTargetKnowledge();
    const choices=new Map(targetCatalog.map(p=>[targetKey(p),p]));
    targetChoices.forEach((p,key)=>choices.set(key,p));
    selected.forEach(p=>choices.set(targetKey(p),p));
    const filtered=[...choices.values()].filter(p=>p.grade===$('target-grade').value&&(!query||p.knowledge_point.toLowerCase().includes(query)));
    $('target-options').innerHTML=filtered.length?filtered.map(p=>`<label class="target-option"><input type="checkbox" value="${esc(targetKey(p))}" ${selected.some(x=>targetKey(x)===targetKey(p))?'checked':''}><span><strong>${esc(p.knowledge_point)}</strong>${targetGradeTags(p)}<small>${p.curriculum_requirements.length?`${p.curriculum_requirements.length} 条课标要求`:'尚未关联库内课标'}</small></span></label>`).join(''):'<p class="field-help">没有匹配的知识点，可添加自定义或使用 LLM 分析获取。</p>';
    $('target-options').querySelectorAll('input').forEach(input=>input.onchange=()=>{
        try{
            const points=readTargetKnowledge();
            if(input.checked){const point=choices.get(input.value);targetChoices.set(targetKey(point),point);points.push(point)}
            else points.splice(points.findIndex(p=>targetKey(p)===input.value),1);
            setTargetKnowledge(points);
        }catch(error){targetStatus(error.message,true);input.checked=!input.checked}
    });
}
function renderTargetKnowledge(){
    const preview=$('target-knowledge-preview');
    try{
        const points=readTargetKnowledge();preview.classList.remove('is-error');
        $('target-summary').textContent=points.length?`已选择 ${points.length} 个知识点`:'选择知识点';
        $('target-count').textContent=points.length?`已选 ${points.length} 项 · 课标要求可编辑`:'尚未选择';
        $('target-clear').hidden=!points.length;
        preview.innerHTML=points.length?points.map((p,i)=>`<article><div class="target-point-heading"><strong>${esc(p.knowledge_point)}</strong><button type="button" class="text-button" data-remove-target="${i}" aria-label="移除 ${esc(p.knowledge_point)}">移除</button></div><div data-grade-tags="${i}">${targetGradeTags(p)}</div><div class="target-grade-editor"><label>目标年级 <small>跟随教学目标</small><select disabled aria-label="本条课标的目标年级"><option>${esc($('target-grade').value)}</option></select></label></div><label class="target-requirements-label">课标要求 <small>每行一条</small><textarea rows="${Math.min(5,Math.max(2,p.curriculum_requirements.length))}" data-target-requirements="${i}" placeholder="可编辑要求，或点击右上角 LLM 分析获取">${esc(p.curriculum_requirements.join('\n'))}</textarea></label>${targetCitation(p)}</article>`).join(''):'<p>将使用原题知识点：'+esc($('knowledge-points').value||'请选题或填写原题知识点')+'</p>';
        preview.querySelectorAll('[data-remove-target]').forEach(b=>b.onclick=()=>{const points=readTargetKnowledge();points.splice(Number(b.dataset.removeTarget),1);setTargetKnowledge(points)});
        preview.querySelectorAll('[data-target-requirements]').forEach(input=>input.oninput=()=>{
            const points=readTargetKnowledge(),point=points[Number(input.dataset.targetRequirements)];
            point.curriculum_requirements=[...new Set(input.value.split('\n').map(s=>s.trim()).filter(Boolean))];
            point.grade_source=point.grade?'manual':'unknown';targetGenerated.delete(targetKey(point));preview.querySelector(`[data-grade-tags="${input.dataset.targetRequirements}"]`).innerHTML=targetGradeTags(point);
            $('target-curriculum').value=JSON.stringify(points,null,2);targetChoices.set(targetKey(point),point);markTargetsEdited();fitTargetRequirements();const citation=input.closest('article').querySelector('.target-citation');if(citation)citation.outerHTML=targetCitation(point);
        });
    }catch(error){preview.classList.add('is-error');preview.textContent=error.message;$('target-summary').textContent='课标内容待修正';$('target-count').textContent='请修正 JSON，或重新分析';$('target-clear').hidden=false}
    renderTargetOptions();requestAnimationFrame(fitTargetRequirements);
}
async function loadTargetCatalog(){
    targetCatalogRequest?.abort();const controller=new AbortController();targetCatalogRequest=controller;
    const grade=$('target-grade').value;
    const scope=$('subject').value+'|'+grade;
    if(scope!==targetCatalogScope){targetChoices.clear();targetCatalog=[];targetCatalogScope=scope}
    if(!grade){targetCatalog=[];$('target-catalog-status').textContent='请先选择目标年级。';renderTargetOptions();return}
    $('target-catalog-status').textContent='正在读取知识点…';$('target-options').setAttribute('aria-busy','true');
    try{
        const result=await api('/api/teaching-targets?'+new URLSearchParams({subject:$('subject').value,target_grade:grade,query:$('target-search').value.trim(),region:$('region').value.trim()||'上海'}),{signal:controller.signal,headers:{'X-Teaching-Targets-Version':'3'}});
        if(controller.signal.aborted)return;
        targetCatalog=normalizeTargetKnowledge(result.items||[]);
        for(const entry of result.curriculum_sources||[])targetCitations.set(targetKey(entry),entry);
        $('target-catalog-status').textContent=result.notice||`按${grade}学段筛选，可搜索更多知识点。`;
        renderTargetOptions();
    }catch(error){if(error.name!=='AbortError'){$('target-catalog-status').textContent=error.message+'；可使用 LLM 分析获取。';targetCatalog=[];renderTargetOptions()}}
    finally{if(targetCatalogRequest===controller)$('target-options').setAttribute('aria-busy','false')}
}
function analysisQuestion(){return currentQuestion({includeTargets:false})}
function cancelTargetAnalysis(message='已取消分析。'){
    if(targetAnalysisRequest){targetAnalysisRequest.abort();targetAnalysisRequest=null;targetStatus(message);$('target-analyze').disabled=false;$('target-analyze').textContent='✧ LLM 分析获取';$('target-analysis-cancel').hidden=true}
}
$('target-analyze').onclick=async()=>{
    let question;
    try{
        question=analysisQuestion();
        if(!question.target_grade)throw new Error('请先选择目标年级。');
        if(!question.stem)throw new Error('请先输入题目，或从题库选择一道题。');
        if(state.imagesLoading)throw new Error('题目图片正在载入，请稍后再分析。');
        if(!state.config.qwen_configured)throw new Error('请先配置千问 API Key。');
    }catch(error){targetStatus(error.message,true);return}
    const controller=new AbortController();targetAnalysisRequest=controller;
    $('target-analyze').disabled=true;$('target-analyze').textContent='正在分析…';$('target-analysis-cancel').hidden=false;
    targetStatus('正在匹配现有库中的课标原文；连接中断会自动重试。');
    try{
        const result=await api('/api/teaching-targets/analyze',{method:'POST',body:JSON.stringify(question),signal:controller.signal,headers:{'X-Teaching-Targets-Version':'3'}});
        if(controller.signal.aborted)return;
        const analyzed=normalizeTargetKnowledge(result.target_curriculum||[]).filter(p=>p.grade===$('target-grade').value);
        for(const entry of result.curriculum_sources||[])targetCitations.set(targetKey(entry),entry);
        let points,recovered=false;
        try{points=normalizeTargetKnowledge(JSON.parse($('target-curriculum').value||'[]')).filter(p=>p.grade_source!=='llm'&&(!p.grade||p.grade===$('target-grade').value))}catch{points=[];recovered=true}
        for(const [key,point] of targetChoices)if(point.grade_source==='llm'||point.grade!==$('target-grade').value){targetChoices.delete(key);targetGenerated.delete(key)}
        for(const point of analyzed){
            const key=targetKey(point);
            let existing=points.find(p=>targetKey(p)===key);
            if(!existing){
                existing=points.find(p=>p.knowledge_point===point.knowledge_point&&!p.grade);
                if(existing){targetChoices.delete(targetKey(existing));Object.assign(existing,point)}
                else points.push(point);
            }else{
                const previous=targetGenerated.has(key)?JSON.parse(targetGenerated.get(key)):null;
                if(!existing.curriculum_requirements.length||JSON.stringify(previous?.curriculum_requirements)===JSON.stringify(existing.curriculum_requirements))existing.curriculum_requirements=point.curriculum_requirements;
                if(!['manual','knowledge_base'].includes(existing.grade_source))existing.grade_source=point.grade_source;
            }
            targetGenerated.set(key,JSON.stringify(point));targetChoices.set(key,existing||point);
        }
        setTargetKnowledge(points);
        const notice=analyzed.length?`已匹配 ${analyzed.length} 项本地课标，按库内原文填充。`+(result.note?' 匹配说明（LLM 分析，非课标原文）：'+result.note:''):(result.note||'未匹配到本地课标，未自动填写要求。');
        targetStatus(notice+(recovered?' 旧课标格式无法解析，已用本次结果恢复。':points.some(p=>p.grade_source==='manual'&&p.curriculum_requirements.length)?' 手动填写的要求保留。':''));
    }catch(error){if(error.name!=='AbortError')targetStatus(error.message,true)}
    finally{if(targetAnalysisRequest===controller){targetAnalysisRequest=null;$('target-analyze').disabled=false;$('target-analyze').textContent='✧ LLM 分析获取';$('target-analysis-cancel').hidden=true}}
};
$('target-analysis-cancel').onclick=()=>cancelTargetAnalysis();
$('target-search').addEventListener('input',()=>{clearTimeout(targetSearchTimer);renderTargetOptions();targetSearchTimer=setTimeout(loadTargetCatalog,300)});
$('target-search').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();clearTimeout(targetSearchTimer);loadTargetCatalog()}});
$('target-picker').addEventListener('toggle',()=>{if($('target-picker').open){loadTargetCatalog();$('target-search').focus()}});
$('target-clear').onclick=()=>setTargetKnowledge([]);
$('target-add').onclick=()=>{
    try{const name=$('target-search').value.trim();if(!name){$('target-search').focus();return}const points=readTargetKnowledge();const grade=$('target-grade').value;if(!points.some(p=>p.knowledge_point===name&&p.grade===grade))points.push({knowledge_point:name,grade,grade_source:grade?'manual':'unknown',curriculum_requirements:[]});setTargetKnowledge(points)}catch(error){targetStatus(error.message,true)}
};
document.addEventListener('click',event=>{if(!$('target-picker').contains(event.target))$('target-picker').open=false});
$('target-picker').addEventListener('keydown',event=>{if(event.key==='Escape'){$('target-picker').open=false;$('target-picker').querySelector('summary').focus()}});
$('target-semester').addEventListener('input',markTargetsEdited);
for(const id of ['target-grade','subject'])$(id).addEventListener('input',()=>{resetQuestionTargets();markTargetsEdited();renderTargetKnowledge();loadTargetCatalog()});
$('target-curriculum').addEventListener('input',()=>{markTargetsEdited();renderTargetKnowledge()});
$('target-curriculum').addEventListener('change',()=>{try{const points=readTargetKnowledge();$('target-curriculum').value=points.length?JSON.stringify(points,null,2):'';}catch{/* Keep incomplete JSON editable; analysis can recover it. */}});
$('knowledge-points').addEventListener('input',renderTargetKnowledge);
for(const id of ['target-grade','target-semester','subject','stem','knowledge-points','options','sub-questions','answer','analysis','region'])$(id).addEventListener('input',()=>cancelTargetAnalysis('题目或目标年级已变化，请重新分析。'));
function targetLabel(question){return question.target_grade?'目标：'+question.target_grade+' · '+(question.target_semester||'全学期'):'历史课件 · 未指定教学目标'}
renderTargetKnowledge();

const labels={pending:'等待生成',generating:'生成中',completed:'已完成',failed:'生成失败',partial_failed:'部分失败',not_generated:'未生成'};
let toastTimer;
function toast(message){$('toast').textContent=message;$('toast').style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').style.display='none',4500)}
function math(node){window.renderMathInElement?.(node,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},{left:'\\(',right:'\\)',display:false},{left:'\\[',right:'\\]',display:true}],throwOnError:false,trust:false,strict:'ignore'})}
async function api(path,options={}){let response;try{response=await fetch(path,{...options,headers:{...(options.body&&!(options.body instanceof FormData)?{'Content-Type':'application/json'}:{}),...options.headers}})}catch(error){if(error.name==='AbortError')throw error;throw new Error('服务连接中断，请检查本地服务是否运行')}if(response.status===401){$('login-dialog').showModal();throw new Error('请先登录工作空间')}const data=await response.json().catch(()=>({detail:'服务返回了无法解析的响应'}));if(!response.ok){const message=typeof data.detail==='string'?data.detail:Array.isArray(data.detail)?data.detail.map(x=>x.loc?.slice(1).join('.')+': '+x.msg).join('；'):'请求未成功，请重试';throw new Error(message)}return data}
async function busy(button,action){const original=button.textContent;button.disabled=true;button.textContent='处理中…';try{return await action()}catch(error){toast(error.message)}finally{button.disabled=false;button.textContent=original}}
function go(view){if(!['create','library','knowledge','integration','api-docs'].includes(view))view='create';state.view=view;document.querySelectorAll('.view').forEach(x=>x.hidden=x.id!=='view-'+view);document.querySelectorAll('[data-view]').forEach(x=>x.classList.toggle('active',x.dataset.view===view));$('view-name').textContent={create:'创建课件',library:'课件与任务',knowledge:'知识资源',integration:'作业系统对接','api-docs':'对外接口文档'}[view];location.hash=view;if(view==='library')loadJobs().catch(e=>toast(e.message));if(view==='knowledge')loadKnowledge().catch(e=>toast(e.message))}
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>go(b.dataset.view));document.querySelectorAll('[data-goto]').forEach(b=>b.onclick=()=>{if(b.dataset.goto==='create')setEditingCourseware();go(b.dataset.goto)});window.onhashchange=()=>{const view=location.hash.slice(1);if(view!==state.view)go(view)};
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());$('preview-dialog').addEventListener('close',()=>$('preview-frame').src='about:blank');$('account').onclick=()=>{if(state.config.auth_required)$('login-dialog').showModal();else toast('当前是本地工作空间；正式部署可在服务端设置 API_KEY')};
$('login-form').onsubmit=async event=>{event.preventDefault();await busy(event.submitter,async()=>{await api('/api/session',{method:'POST',body:JSON.stringify({key:$('access-key').value})});$('access-key').value='';$('login-dialog').close();await initialize()})};
function resetQuestionTargets(){
    targetCitations.clear();
    cancelTargetAnalysis();targetCatalogRequest?.abort();clearTimeout(targetSearchTimer);
    targetChoices.clear();targetGenerated.clear();targetCatalog=[];targetCatalogScope='';
    $('target-curriculum').value='';$('target-search').value='';$('target-picker').open=false;
    targetStatus('');
}
function setPreviewOverlay(open){
    const preview=$('preview-dialog');preview.classList.toggle('has-courseware-overlay',open);
    preview.querySelector('.dialog-header').inert=open;
}
window.addEventListener('message',event=>{
    const data=event.data;
    if(event.source!==$('preview-frame').contentWindow||!$('preview-dialog').open||data?.channel!=='courseware'||data.version!==1||data.type!=='overlay'||data.payload?.name!=='answer')return;
    setPreviewOverlay(data.payload.open===true);
});
$('preview-dialog').addEventListener('close',()=>setPreviewOverlay(false));
$('preview-frame').addEventListener('load',()=>setPreviewOverlay(false));
function setEditingCourseware(id=null){
    state.editingCoursewareId=id;$('editing-courseware').hidden=!id;
    $('generate-button').innerHTML=(id?'✧ 更新并重新生成':'✧ 生成讲解课件')+' <span>→</span>';
}
$('save-as-new-courseware').onclick=()=>{setEditingCourseware();toast('将创建一份新课件，原课件保留');};
function fillQuestion(question,{sample=false,sourceNote='',restoreTargets=false}={}){setEditingCourseware();$('generation-requirements').value='';delete $('generation-requirements').dataset.testGrade;resetQuestionTargets();state.imageRequest++;state.imagesLoading=false;state.requiresImages=!!question.requires_images;$('generate-button').disabled=false;$('form-error').hidden=true;const fields={'stem':'stem','subject':'subject','region':'region','question-type':'question_type','difficulty':'difficulty','answer':'answer','analysis':'analysis','question-id':'question_id'};Object.entries(fields).forEach(([id,key])=>$(id).value=question[key]||'');setGrades(questionGrades(question));$('knowledge-points').value=(question.knowledge_points||[]).join('、');$('options').value=(question.options||[]).join('\n');$('sub-questions').value=question.sub_questions?.length?JSON.stringify(question.sub_questions,null,2):'';$('mode').value=sample?'sample':'qwen';$('variant-count').value='2';state.images=[...(question.images||[])];state.source=question;if(sample||restoreTargets){$('target-grade').value=question.target_grade||'';$('target-semester').value=question.target_semester||'全学期';$('target-curriculum').value=question.target_curriculum?.length?JSON.stringify(normalizeTargetKnowledge(question.target_curriculum).filter(p=>!p.grade||p.grade===question.target_grade),null,2):'';}renderTargetKnowledge();loadTargetCatalog();renderImages();$('source-note').hidden=!sourceNote;$('source-note').textContent=sourceNote;countQuestion();go('create');$('stem').focus()}

async function selectExamQuestion(row){
    const teachingGrades=state.gradeSelectionManual?selectedGrades():[];
    const note=`来自 exam.public 公共题库 · ${row.grades.length?'题库年级：'+row.grades.join('、'):'题库未标注年级'}。`;
    fillQuestion(row.question,{sourceNote:note});
    if(teachingGrades.length)setGrades(teachingGrades);
    if(!row.question.requires_images)return;
    const request=state.imageRequest;
    state.imagesLoading=true;$('generate-button').disabled=true;
    $('source-note').textContent=note+'正在从题库读取原图…';
    try{
        const result=await api(`/api/exam/questions/${encodeURIComponent(row.id)}/images`);
        if(request!==state.imageRequest)return;
        if(!result.images.length)throw new Error('题库中没有可读取的原图');
        state.images=result.images;
        renderImages();
        if(state.source){$('source-note').hidden=false;$('source-note').textContent=note+`已自动载入 ${result.images.length} 张原图。`}
    }catch(error){
        if(request!==state.imageRequest)return;
        $('form-error').textContent=error.message+' 可重新选题重试，或手动上传原图。';
        $('form-error').hidden=false;
        $('source-note').textContent=note+'原图尚未载入，请重试或补充上传。';
    }finally{
        if(request===state.imageRequest){state.imagesLoading=false;$('generate-button').disabled=false}
    }
}

function countQuestion(){$('question-count').textContent=`${$('stem').value.length} / 20000`}
$('stem').addEventListener('input',countQuestion);
for(const id of ['stem','subject','grade','region','knowledge-points','question-type','difficulty','answer','analysis','options','sub-questions','question-id']){$(id).addEventListener('input',()=>{setEditingCourseware();if($('mode').value==='sample'){$('mode').value='qwen';toast('题目或教学条件已修改，已切换为千问生成')}if(state.source&&id!=='grade'){state.source=null;$('source-note').hidden=true}})}
function renderImages(){setEditingCourseware();cancelTargetAnalysis('题目图片已变化，请重新分析。');scheduleQuestionPreview();$('image-list').innerHTML=state.images.map((url,i)=>`<div class="image-chip"><img src="${esc(url)}" alt="题目图片 ${i+1}"><button type="button" data-remove-image="${i}" aria-label="移除图片 ${i+1}">✕</button></div>`).join('');document.querySelectorAll('[data-remove-image]').forEach(b=>b.onclick=()=>{state.source=null;$('source-note').hidden=true;state.images.splice(Number(b.dataset.removeImage),1);renderImages()})}
$('image-upload').onchange=async event=>{if(!event.target.files.length)return;state.source=null;$('source-note').hidden=true;state.imageRequest++;state.imagesLoading=false;$('generate-button').disabled=false;try{for(const file of event.target.files){if(state.images.length>=8)throw new Error('每题最多 8 张图片');const data=new FormData();data.append('file',file);const result=await api('/api/images',{method:'POST',body:data});state.images.push(result.data_url)}$('mode').value='qwen';renderImages();if(state.images.length)$('form-error').hidden=true}catch(e){toast(e.message)}event.target.value=''};
async function submitCourseware(question,options,{assignment_id='',title=''}={}){
    const headers={'Idempotency-Key':crypto.randomUUID()};
    if(options.mode!=='sample'&&question.question_id.startsWith('exam:public:')&&question.source==='exam/public/question/'+question.question_id.slice(12)){
        const body={question_id:question.question_id,target_grade:question.target_grade,
            target_semester:question.target_semester||'全学期',target_curriculum:question.target_curriculum||[],options,
            ...(assignment_id?{assignment_id}:{}),...(title?{title}:{})};
        return api('/api/courseware',{method:'POST',headers,body:JSON.stringify(body)});
    }
    // Keep manual/sample input available in the local test page.
    const body={assignment_id:assignment_id||'lesson-'+Date.now(),title:title||'题目讲解',questions:[question],options};
    return api('/api/jobs?single_question=true',{method:'POST',headers,body:JSON.stringify(body)});
}
function updateCourseware(id,body={}){
    return api('/api/courseware/'+encodeURIComponent(id),{method:'PATCH',headers:{'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify(body)});
}
$('generate-form').onsubmit=async event=>{
    event.preventDefault();$('form-error').hidden=true;if(state.imagesLoading)return;
    const button=event.submitter;
    try{
        const question=currentQuestion();if(!question.target_grade)throw new Error('请选择目标年级');
        if($('mode').value==='qwen'&&!state.config.qwen_configured)throw new Error('请先配置千问 API Key 后生成课件');
        const options={mode:$('mode').value,depth:$('depth').value,variant_count:Number($('variant-count').value),voice:$('voice').checked,force:false,generation_requirements:$('generation-requirements').value.trim()};
        button.disabled=true;button.textContent='正在提交…';$('question-id').value=question.question_id;
        const title=(question.target_curriculum[0]?.knowledge_point||question.knowledge_points[0]||question.subject)+' · 题目讲解';
        if(state.editingCoursewareId){
            await updateCourseware(state.editingCoursewareId,{target_grade:question.target_grade,target_semester:question.target_semester,
                target_curriculum:question.target_curriculum,options,title});
            toast('已提交更新，将替换当前课件的生成结果');setEditingCourseware();
        }else{
            await submitCourseware(question,options,{assignment_id:$('assignment-id').value.trim(),title});
            toast('已创建新课件，可在列表查看生成进度');
        }
        state.jobPage=1;go('library');
    }catch(error){$('form-error').textContent=error.message;$('form-error').hidden=false}
    finally{button.disabled=false;setEditingCourseware(state.editingCoursewareId)}
};

let questionPreviewTimer,questionPreviewVersion=0;
function scheduleQuestionPreview(){
    const version=++questionPreviewVersion;
    clearTimeout(questionPreviewTimer);
    questionPreviewTimer=setTimeout(()=>updateQuestionPreview(version),250);
}
async function updateQuestionPreview(version){
    const preview=$('question-preview'),content=$('question-preview-content');
    const stem=$('stem').value.trim();
    preview.hidden=!stem;
    if(!stem)return;
    try{
        const subs=$('sub-questions').value.trim()?JSON.parse($('sub-questions').value):[];
        if(!Array.isArray(subs))throw new Error('小题结构须为 JSON 数组');
        const body={question_id:'layout-preview',stem,question_type:$('question-type').value,
            source:state.source?.source||'',options:$('options').value.split('\n').map(s=>s.trim()).filter(Boolean),
            sub_questions:subs,images:state.images.map(()=>'')};
        const layout=await api('/api/questions/layout',{method:'POST',body:JSON.stringify(body)});
        if(version!==questionPreviewVersion)return;
        content.innerHTML=window.QuestionView.render(layout,state.images,{interactive:false});
        math(content);
    }catch(error){
        if(version===questionPreviewVersion)content.innerHTML=`<div class="question-preview-error">${esc(error.message)}</div>`;
    }
}
for(const id of ['stem','options','sub-questions','question-type'])$(id).addEventListener('input',scheduleQuestionPreview);

function stats(items){return items.map(([value,label])=>`<div class="stat-card"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('')}
let jobsRequest=0,jobsController,jobSearchTimer;
async function loadJobs(){
    const request=++jobsRequest;jobsController?.abort();jobsController=new AbortController();
    const params=new URLSearchParams({page:state.jobPage,page_size:state.jobPageSize,query:$('job-search').value.trim(),
        grade:$('job-grade-filter').value,knowledge_point:$('job-knowledge-filter').value,status:$('job-filter').value});
    try{
        const result=await api('/api/courseware?'+params,{signal:jobsController.signal});
        if(request!==jobsRequest)return;
        state.jobList=result;state.jobs=result.items;state.jobPage=result.pagination.page;
        $('job-count').textContent=result.stats.total;renderJobs();
    }catch(error){if(error.name!=='AbortError')throw error;}
}
function refreshJobFilters(){clearTimeout(jobSearchTimer);state.jobPage=1;loadJobs().catch(e=>toast(e.message));}
function renderJobPagination(){
    const {page,page_size,total,pages}=state.jobList.pagination;
    $('job-page-summary').textContent=total?`共 ${total} 份 · 显示 ${(page-1)*page_size+1}–${Math.min(page*page_size,total)} 份`:'共 0 份';
    $('job-page-number').textContent=`${page} / ${pages}`;
    $('job-page-prev').disabled=page<=1;$('job-page-next').disabled=page>=pages;
}
async function changeJobPage(offset){
    state.jobPage+=offset;
    try{await loadJobs();document.querySelector('#view-library .filter-row')?.scrollIntoView({block:'start'});}catch(error){toast(error.message);}
}
$('job-page-prev').onclick=()=>changeJobPage(-1);$('job-page-next').onclick=()=>changeJobPage(1);
$('job-page-size').onchange=()=>{state.jobPageSize=Number($('job-page-size').value);refreshJobFilters()};
// Teaching targets take precedence; old lessons can still be found by their source metadata.
function jobGrades(item){const q=item.question||{};return q.target_grade?[q.target_grade]:questionGrades(q).length?questionGrades(q):['未标注年级'];}
function jobTitle(job){
    const grades=[...new Set(job.items.flatMap(jobGrades))];
    return grades.map(grade=>`<span class="job-grade">${esc(grade)}</span>`).join('')+esc(job.title);
}
function jobKnowledge(item){const q=item.question||{},targets=q.target_curriculum||[];return [...new Set((targets.length?targets.map(p=>p.knowledge_point):q.knowledge_points||[]).filter(Boolean))];}
function syncJobFilters(filters){
    const choices=[['job-grade-filter','全部年级',[...filters.grades].sort((a,b)=>(targetGradeNames.indexOf(a)<0?99:targetGradeNames.indexOf(a))-(targetGradeNames.indexOf(b)<0?99:targetGradeNames.indexOf(b)))],['job-knowledge-filter','全部知识点',[...filters.knowledge_points].sort((a,b)=>a.localeCompare(b,'zh-CN'))]];
    for(const [id,label,values] of choices){
        const select=$(id),selected=select.value;
        if(selected&&!values.includes(selected))values.push(selected);
        const html='<option value="">'+label+'</option>'+values.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join('');
        if(select.innerHTML!==html){select.innerHTML=html;select.value=selected;}
    }
}
function renderJobs(){const openEvents=new Set([...document.querySelectorAll('[data-diagnostics][open]')].map(e=>e.dataset.diagnostics));
const counts=state.jobList.stats;$('task-stats').innerHTML=stats([[counts.total,'课件总数'],[counts.completed,'已完成课件'],[counts.active,'正在生成'],[counts.failed,'待重试课件']]);
syncJobFilters(state.jobList.filters);renderJobPagination();const jobs=state.jobs;
if(!jobs.length){
    $('job-list').innerHTML=counts.total?'<div class="empty-state"><h3>没有符合筛选条件的课件</h3><p>可以调整年级、知识点、状态或搜索内容。</p><button class="button secondary" id="clear-job-filters">清除筛选</button></div>':'<div class="empty-state"><div class="empty-icon">▤</div><h3>这里将保存你的讲解课件</h3><p>输入题目，或从题库挑选一道题开始。</p><button class="button primary" id="empty-create">创建第一份课件 →</button></div>';
    if($('empty-create'))$('empty-create').onclick=()=>go('create');
    if($('clear-job-filters'))$('clear-job-filters').onclick=()=>{['job-search','job-grade-filter','job-knowledge-filter'].forEach(id=>$(id).value='');$('job-filter').value='all';refreshJobFilters()};
    return;
}$('job-list').innerHTML=jobs.map(job=>`<article class="job-card"><div class="job-header"><div><h3>${jobTitle(job)}</h3><p>课件 ID：${esc(job.items[0].id)}<br>${esc(job.assignment_id)} · ${new Date(job.created_at).toLocaleString('zh-CN',{hour12:false})} · ${job.items.length} 道题</p></div><span class="status ${job.status}">${labels[job.status]}</span></div>${job.items.map(item=>`<div class="item-row"><div class="item-info"><strong>${esc(item.question.stem)}</strong><div class="item-meta"><span>${esc(item.question.subject)} · ${esc(targetLabel(item.question))}</span><span>${esc(item.status==='completed'&&item.result?.storage?'已完成':item.stage)}</span>${item.result?`${item.status!=='completed'?'<span>上次生成结果</span>':''}<span>${item.result.slide_count} 页</span><span>${item.result.mode==='sample'?'内置样例':'千问生成'}</span>${item.result.review_type==='not_reviewed'?'<span>未独立复核</span>':''}${item.result.storage?`<span title="${esc(item.result.storage.bucket+'/'+item.result.storage.prefix+'/')}">已上传 MinIO</span>`:''}<span>${item.result.voice_mode==='qwen'?'已打包语音':item.result.voice_mode==='browser'?'设备朗读':'无语音'}</span>`:''}${item.reused?'<span>复用已有内容</span>':''}</div>${['pending','generating'].includes(item.status)?`<div class="progress-track"><i style="width:${item.progress}%"></i></div>`:''}${item.error?`<div class="item-error">${esc(item.error.message)}</div>`:''}${item.events?.length?`<details class="generation-events" data-diagnostics="${esc(item.id)}"><summary>生成记录 · 第 ${item.attempts} 次尝试</summary>${item.events.map(e=>`<p><time>${new Date(e.created_at).toLocaleTimeString('zh-CN',{hour12:false})}</time> ${esc(e.message)}</p>`).join('')}</details>`:''}</div><div class="item-actions">${item.result?`<button class="button primary small" data-preview="${esc(item.result.player_url||item.result.html_url)}" data-title="${esc(item.result.title)}">▶ 预览课件</button><a class="button secondary small" href="${esc(item.result.pptx_url)}">PPTX ↓</a><a class="button secondary small" href="${esc(item.result.zip_url)}">讲解包 ↓</a>`:`<span class="status ${item.status}">${labels[item.status]}</span>`}</div></div>`).join('')}${job.callbacks.map(c=>`<div class="item-meta">完成通知：${c.status==='delivered'?'已送达':c.status==='failed'?'通知失败':'等待通知'} · ${c.attempts} 次${c.status==='failed'?` <button class="text-button" data-callback="${c.id}">重新通知</button>`:''}</div>`).join('')}<div class="job-actions">${['failed','partial_failed'].includes(job.status)?`<button class="text-button" data-retry="${job.id}">↻ 重试失败题目</button>`:''}${!['pending','generating'].includes(job.status)?`<button class="text-button" data-regenerate="${esc(job.items[0].id)}">重新生成</button><button class="text-button" data-edit-target="${esc(job.items[0].id)}">调整教学目标</button>`:''}</div></article>`).join('');document.querySelectorAll('[data-diagnostics]').forEach(e=>e.open=openEvents.has(e.dataset.diagnostics));math($('job-list'));document.querySelectorAll('[data-preview]').forEach(b=>b.onclick=()=>{$('preview-title').textContent=b.dataset.title;$('preview-frame').src=b.dataset.preview;$('preview-dialog').showModal()});document.querySelectorAll('[data-retry]').forEach(b=>b.onclick=()=>busy(b,async()=>{const job=state.jobs.find(j=>j.id===b.dataset.retry);if(job?.items.some(i=>!i.question.target_grade)){editTeachingTarget(job);return;}await api(`/api/jobs/${b.dataset.retry}/retry`,{method:'POST'});await loadJobs()}));document.querySelectorAll('[data-regenerate]').forEach(b=>b.onclick=()=>busy(b,async()=>{const job=state.jobs.find(j=>j.items[0].id===b.dataset.regenerate);if(!job.items[0].question.target_grade){editTeachingTarget(job);return;}await updateCourseware(job.items[0].id);toast('已开始重新生成，完成后更新这条课件');await loadJobs()}));document.querySelectorAll('[data-edit-target]').forEach(b=>b.onclick=()=>editTeachingTarget(state.jobs.find(j=>j.items[0].id===b.dataset.editTarget)));document.querySelectorAll('[data-callback]').forEach(b=>b.onclick=()=>busy(b,async()=>{await api(`/api/callbacks/${b.dataset.callback}/retry`,{method:'POST'});await loadJobs()}))}
function editTeachingTarget(job){fillQuestion(job.items[0].question,{restoreTargets:true,sourceNote:'调整本题的教学目标，提交后更新同一条课件记录。'});$('assignment-id').value=job.assignment_id;$('depth').value=job.request.options.depth;$('variant-count').value=String(job.request.options.variant_count);$('voice').checked=job.request.options.voice;$('generation-requirements').value=job.request.options.generation_requirements||'';setEditingCourseware(job.items[0].id);$('target-grade').focus();}
$('job-grade-filter').onchange=refreshJobFilters;$('job-knowledge-filter').onchange=refreshJobFilters;$('job-search').oninput=()=>{clearTimeout(jobSearchTimer);jobsController?.abort();jobsRequest++;jobSearchTimer=setTimeout(refreshJobFilters,300)};$('job-filter').onchange=refreshJobFilters;$('refresh-jobs').onclick=()=>busy($('refresh-jobs'),loadJobs);
$('open-exam').onclick=()=>{if(!state.config.exam_configured){toast('请先配置 exam 数据库连接');return}$('exam-dialog').showModal();$('exam-results').innerHTML='<p class="results-placeholder">可按题干关键词或知识点查找当前学科的题目。</p>'};
$('exam-search-form').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{const params=examParams({query:$('exam-query').value});state.exam=await api('/api/exam/questions?'+params);$('exam-results').innerHTML=state.exam.length?state.exam.map((row,i)=>`<div class="exam-result">${window.QuestionView.render(row.layout||{stem:row.stem},[],{interactive:false})}${row.image_urls.length||row.has_attachments?'<p class="warning">此题含图片附件，选入后自动读取原图。</p>':''}<div class="result-bottom"><span>${esc(row.labels.join('、')||'未关联知识点')}<br>${esc(row.grade_scope)} · 原始年级：${esc(row.grades.join('、')||'未标注')}</span><button class="button secondary small" data-select-question="${i}">选用此题</button></div></div>`).join(''):'<p class="results-placeholder">暂无匹配题目，可以换一个知识点或关键词。</p>';math($('exam-results'));document.querySelectorAll('[data-select-question]').forEach(b=>b.onclick=()=>{const row=state.exam[Number(b.dataset.selectQuestion)];$('exam-dialog').close();selectExamQuestion(row)})})};

let randomRequest=0,randomController,randomDebounce;
async function loadRandomQuestions({excludeCurrent=true}={}){
    const request=++randomRequest;
    randomController?.abort();randomController=new AbortController();
    const previous=state.randomQuestions.map(row=>row.id);
    const subject=$('subject').value;
    const list=$('random-question-list'),refresh=$('refresh-random');
    state.randomQuestions=[];
    list.replaceChildren();list.setAttribute('aria-busy','true');
    refresh.disabled=true;refresh.textContent='读取中…';
    $('random-status').textContent=`正在读取${subject}题目…`;
    try{
        if(!state.config.exam_configured){$('random-status').textContent='配置 exam 题库连接后，可在这里随机选题。';return}
        const params=examParams({limit:'3'}),targetGrade=$('target-grade').value;
        if(targetGrade){params.delete('grades');params.append('grades',targetGrade);}
        if(excludeCurrent&&previous.length)params.set('exclude',previous.join(','));
        const rows=await api('/api/exam/questions/random?'+params,{signal:randomController.signal});
        if(request!==randomRequest)return;
        state.randomQuestions=rows;
        $('random-status').textContent=rows.some(row=>row.cached)?'题库连接暂不稳定，显示最近读取的真实题目，已按所选年级筛选。':rows.length?(targetGrade?`按目标年级${targetGrade}抽取，点击后带入题库原题。`:'按所选学科与题库年级抽取，点击后带入题库原题。'):'该学科与年级暂无可用题目，可切换教学范围。';
        list.innerHTML=rows.map((row,index)=>{
            const question=row.question;
            const title=row.stem.startsWith('以下为题库小题')?question.sub_questions.map(q=>q.stem).join('；'):row.stem;
            const scope=row.grades.length?row.grades.join('、'):'题库未标注年级';
            return `<button type="button" class="sample-item random-question" data-random-question="${index}" data-question-id="${esc(question.question_id)}"><span class="sample-symbol">${String(index+1).padStart(2,'0')}</span><span class="random-question-copy"><strong>${esc(title)}</strong><small>${esc(question.subject)} · ${esc(question.difficulty)}<br>${esc(scope)}${question.requires_images?' · 含原图':''}</small></span><span class="arrow" aria-hidden="true">↗</span></button>`;
        }).join('');
        math(list);
        list.querySelectorAll('[data-random-question]').forEach(button=>button.onclick=()=>{
            const row=rows[Number(button.dataset.randomQuestion)];
            selectExamQuestion(row);
        });
    }catch(error){if(request===randomRequest&&error.name!=='AbortError')$('random-status').textContent=error.message+' 可点击“换一批”重试。'}
    finally{if(request===randomRequest){list.setAttribute('aria-busy','false');refresh.disabled=false;refresh.textContent='↻ 换一批'}}
}
$('refresh-random').onclick=()=>loadRandomQuestions();
for(const id of ['target-grade','subject','grade','region'])$(id).addEventListener('change',()=>{clearTimeout(randomDebounce);randomController?.abort();randomDebounce=setTimeout(()=>loadRandomQuestions({excludeCurrent:false}),350);});

async function loadKnowledge(){const [resources,kb]=await Promise.all([api('/api/resources'),api('/api/kb/stats')]);state.kb=kb;$('kb-stats').innerHTML=stats([[kb.nodes||0,'已导入知识节点'],[kb.edges||0,'知识关联关系'],[kb.chunks||0,'知识片段'],[kb.embedded||0,'已完成向量化']]);$('resource-count').textContent=`${resources.length} 项`;$('resource-list').innerHTML=resources.map(r=>`<div class="resource-card"><span class="resource-tag">${esc(r.subject)} · ${esc(r.is_sample?'内置样例':'已登记资源')}</span><h3>${esc(r.title)}</h3><p>${esc(r.grades.join('、'))} · ${esc(r.region)}</p><p>来源：${esc(r.source)} · v${esc(r.version)}</p></div>`).join('');$('kb-task').hidden=!kb.configured&&kb.task.status==='idle';if(kb.configured){$('kb-task').hidden=false;$('kb-task').textContent=kb.task.status==='running'?`知识库任务运行中，已处理 ${kb.task.processed} 个片段。可离开此页，任务会继续。`:kb.task.status==='failed'?kb.task.error:`${kb.engine}。图谱节点用于知识定位；教材、课标与参考正文分别索引。`}$('sync-graph').disabled=kb.task.status==='running';$('index-kb').disabled=kb.task.status==='running'}
$('sync-graph').onclick=()=>busy($('sync-graph'),async()=>{await api('/api/kb/sync',{method:'POST'});toast('开始从 exam 同步知识图谱');await loadKnowledge()});$('index-kb').onclick=()=>busy($('index-kb'),async()=>{await api('/api/kb/index',{method:'POST'});toast('已启动千问向量化，已有向量将复用');await loadKnowledge()});
$('kb-search-form').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{const params=new URLSearchParams({...teachingScope('search-scope'),query:$('kb-query').value,subject:$('kb-subject').value,grade:$('kb-grade').value});const rows=await api('/api/kb/search?'+params);$('kb-results').className='';$('kb-results').innerHTML=rows.length?rows.map(r=>`<div class="kb-hit"><h4>${esc(r.metadata.title)} <span class="resource-tag">${r.source_type==='graph'?'图谱索引词':r.source_type==='curriculum'?(r.metadata.extraction==='qwen_ocr'?'课标 OCR':'课标原文'):'教学资料'}</span></h4><p>${esc(r.content)}</p><small>相似度 ${(r.score*100).toFixed(1)}% · ${r.match_scope==='grade'?'精确年级':'学段匹配，年级待核对'}${r.metadata.page?' · 第 '+r.metadata.page+' 页':''}${r.metadata.extraction==='qwen_ocr'?' · 机器识别，公式需核对':''} · ${esc([r.metadata.curriculum_version&&'课标 '+r.metadata.curriculum_version,r.metadata.school_system,r.metadata.publisher,r.metadata.textbook_version,r.metadata.volume].filter(Boolean).join(' · '))} · ${esc(r.metadata.source||'exam')}</small></div>`).join(''):'<p class="results-placeholder">没有满足范围的已向量化内容。可先导入资料，再生成向量。</p>';math($('kb-results'))})};
$('load-graph').onclick=()=>busy($('load-graph'),async()=>{const params=new URLSearchParams({subject:$('kb-subject').value,query:$('kb-query').value});const graph=await api('/api/kb/graph?'+params);$('graph-list').innerHTML=graph.nodes.length?graph.nodes.map(n=>`<div class="graph-node"><div>${esc(n.name)}<small>${esc(n.subject)} · ${{primary:'小学',junior:'初中',senior:'高中'}[n.phase]||esc(n.phase)} · ${n.grade_code?esc(n.grade_code):'具体年级待映射'}${graph.edges.filter(e=>e.source_id===n.id||e.target_id===n.id).map(e=>' · '+esc(e.relation_type)).join('')}</small></div></div>`).join(''):'<p class="results-placeholder">暂无节点，请先同步知识图谱。</p>'});
$('open-import').onclick=()=>$('import-dialog').showModal();$('document-form').onsubmit=event=>{event.preventDefault();busy(event.submitter,async()=>{const data=new FormData();data.append('file',$('document-file').files[0]);data.append('metadata',JSON.stringify({...teachingScope('document-scope'),title:$('document-title').value,source_type:$('document-type').value,subject:$('document-subject').value,version:$('document-version').value,region:$('document-region').value,grades:split($('document-grades').value),phase:$('document-phase').value,source:$('document-source').value}));const result=await api('/api/kb/documents/import',{method:'POST',body:data});$('import-dialog').close();toast(`已导入 ${result.chunks} 个片段，可开始生成向量`);await loadKnowledge()})};
$('resource-import').onchange=async event=>{try{const file=event.target.files[0];if(!file)return;const value=JSON.parse(await file.text());const result=await api('/api/resources/import',{method:'POST',body:JSON.stringify(Array.isArray(value)?{resources:value}:value)});toast(`已导入 ${result.imported} 项资源`);await loadKnowledge()}catch(e){toast(e.message)}event.target.value=''};
const example={question_id:'756ac6cd-befa-4a40-9972-f0013f6f7444',target_grade:'七年级',target_semester:'全学期',target_curriculum:[{knowledge_point:'一元一次方程',grade:'七年级',curriculum_requirements:['理解方程解的含义','用等式性质说明每一步变形']}]};$('api-example').textContent=JSON.stringify(example,null,2);$('copy-api').onclick=async()=>{try{await navigator.clipboard.writeText(JSON.stringify(example,null,2));toast('已复制生成请求')}catch{toast('浏览器未开放剪贴板，可直接选中请求内容复制')}};
async function initialize(){state.config=await api('/api/config');$('provider-label').textContent=state.config.qwen_configured?'千问已配置':'千问待配置';$('provider-dot').className='small-dot '+(state.config.qwen_configured?'ready':'warn');$('voice-description').textContent={qwen:'千问合成音频，打包后可离线播放',browser:'使用设备中文语音，逐页朗读讲稿',none:'服务端暂未启用语音'}[state.config.tts_mode];if(state.config.auth_required&&!state.config.authenticated){$('login-dialog').showModal();return}loadRandomQuestions({excludeCurrent:false});await loadJobs();go(location.hash.slice(1)||'create')}
let polling=false;setInterval(async()=>{if(polling||document.hidden||!state.config.authenticated&&state.config.auth_required)return;polling=true;try{if(state.view==='library')await loadJobs();if(state.view==='knowledge'&&state.kb?.task.status==='running')await loadKnowledge()}catch(e){/* 保留当前页面，手动刷新可显示完整错误。 */}finally{polling=false}},2500);
initialize().catch(e=>toast(e.message));

const crossGradeRequirements={
 '四年级':'面向四年级，使用假设法和整数四则运算。先假设35只全是鸡，比较实际脚数多出的24只脚，再解释每换成一只兔会多2只脚。不设未知数、不列方程，用鸡兔实物或简洁图示说明，逐步验证头数和脚数。',
 '七年级':'面向七年级，使用一元一次方程解决同一问题。设兔有x只，鸡有35-x只，由脚数列2(35-x)+4x=94；解释每一步等式变形依据，再代回头数与脚数检验。'
};
document.querySelectorAll('[data-cross-grade]').forEach(button=>button.onclick=async()=>{const markup=button.innerHTML;try{await busy(button,async()=>{
 const question=await api('/static/cross-grade-example.json?v=20261105-player-docs-v20');
 await selectExamQuestion({question,grades:question.grades});
 const grade=button.dataset.crossGrade;$('target-grade').value=grade;
 $('target-grade').dispatchEvent(new Event('input',{bubbles:true}));$('target-grade').dispatchEvent(new Event('change',{bubbles:true}));
 $('generation-requirements').value=crossGradeRequirements[grade];$('generation-requirements').dataset.testGrade=grade;
 $('generation-requirements-details').open=true;
 targetStatus(`已载入${grade}测试方案。目标课标可按需从本地库匹配；解法要求已填入“补充生成要求”。`);
 $('target-grade').scrollIntoView({block:'center',behavior:'smooth'});toast(`已填入${grade}测试题，可点击生成课件`);
});}finally{button.innerHTML=markup;}});
$('generation-requirements').addEventListener('input',()=>{delete $('generation-requirements').dataset.testGrade;markTargetsEdited();});
$('target-grade').addEventListener('input',()=>{const input=$('generation-requirements');if(input.dataset.testGrade&&input.dataset.testGrade!==$('target-grade').value){input.value='';delete input.dataset.testGrade;}});
