'use strict';
const $ = id => document.getElementById(id);
const state = {config:null, threadKey:null, thread:null, runs:[], busy:false, viewer:null, viewerSerial:0, navigation:0, observers:[],  settings:null, localModels:[], checkedAccount:null, library:{docs:null,matches:null,serial:0,timer:null}};
function rememberThread(identity) {
  if(!state.threadKey)return;
  if(identity)localStorage.setItem(state.threadKey,identity);
  else localStorage.removeItem(state.threadKey);
}
function el(tag, attrs={}, ...children) {
  const element=document.createElement(tag);
  for(const [key,value] of Object.entries(attrs)) {
    if(key.startsWith('on')) element.addEventListener(key.slice(2).toLowerCase(),value);
    else if(key==='class') element.className=value;
    else element.setAttribute(key,value);
  }
  for(const child of children.flat(Infinity)) if(child!==null && child!==undefined) element.append(child instanceof Node?child:document.createTextNode(String(child)));
  return element;
}
function message(text) { $('toast').textContent=text; $('toast').hidden=false; clearTimeout(message.timer); message.timer=setTimeout(()=>$('toast').hidden=true,6500); }
async function api(path, body) {
  const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Oracle-Token':state.config.csrf_token},body:JSON.stringify(body)};
  let response=await fetch(path,options);
  if(body!==undefined && response.status===403){
    const fresh=await fetch('/api/config');
    if(fresh.ok){const config=await fresh.json();state.config.csrf_token=config.csrf_token;options.headers['X-Oracle-Token']=config.csrf_token;response=await fetch(path,options);}
  }
  const data=await response.json();
  if(!response.ok) throw new Error(data.error || 'The local request failed');
  return data;
}
function safeLink(url,label) {
  try {const parsed=new URL(url); if(!['https:','http:'].includes(parsed.protocol)) throw Error();
    return el('a',{href:parsed.href,target:'_blank',rel:'noopener noreferrer'},label);
  }catch{return el('span',{},label);}
}
const human = text => String(text||'').replaceAll('_',' ');
function citationLabel(ref) {
  let title=ref.title||'Source document';
  if(title.length>58) title=title.slice(0,55)+'…';
  const pages=ref.page_end && ref.page_end!==ref.page?`${ref.page}–${ref.page_end}`:ref.page;
  return title+(pages?` · p. ${pages}`:'');
}
function cite(ref,run) {
  const index=(run.answer?.quotes||[]).findIndex(q=>q.citation.evidence_id===ref.evidence_id);
  if(index<0) return safeLink(ref.source_url,ref.title||'Source');
  return el('button',{class:'citation-button',onClick:()=>openSource(run.id,index)},'▤ ',citationLabel(run.answer.quotes[index].citation),' ↗');
}
const statusNames={supported:'Research result',partial:'Partial answer',currency_unverified:'Date unverified',conflicting_sources:'Sources differ',clarification_needed:'More information needed',not_found:'No suitable answer found',failed:'No accepted answer'};
function progressLabel(progress) {
  if(progress?.includes('request_classification')||progress?.includes('public_research_check'))return 'Checking the research request…';
  if(/model_started:plan/.test(progress||''))return 'Planning the search…';
  if(progress==='research_search'||/model_started:read/.test(progress||'')||progress==='research_read')return 'Reading candidate passages…';
  if(/model_started:triage/.test(progress||''))return 'Choosing the key passages…';
  if(/model_started:answer/.test(progress||''))return 'Writing the answer…';
  if(/model_started:(?:verify|doubts)/.test(progress||''))return 'Checking each finding against its source…';
  if(/model_started:.*review/.test(progress||''))return 'Checking support, dates, and exceptions…';
  if(/model_started:.*(?:draft|selection)/.test(progress||''))return 'Reading the retrieved sources…';
  if(progress==='additional_retrieval_and_expansion')return 'Looking for additional source material…';
  if(progress==='final_validation')return 'Validating quotations and citations…';
  if(progress==='draft_validation')return 'Preparing the independent review…';
  return 'Searching the corpus and following source connections…';
}
function renderRun(run) {
  const turn=el('article',{class:'turn',id:'run-'+run.id});
  turn.append(el('div',{class:'user-message'},run.question));
  const answer=el('div',{class:'answer'});
  answer.append(el('div',{class:'answer-label'},el('img',{class:'answer-symbol',src:'/trace-logo.svg',alt:''}),'TRACE',run.saved_example?el('small',{},run.saved_example===2?'Saved Bedrock run':'Saved local run'):null));
  if(run.status==='running') {
    answer.append(el('div',{class:'pending'},el('span',{class:'spinner','aria-hidden':'true'}),progressLabel(run.progress)));
    turn.append(answer);return turn;
  }
  if(run.status==='failed') {
    answer.append(el('p',{class:'failure'},'The research run could not be completed. No answer was accepted.'),el('p',{class:'status-description'},run.error||''));
    answer.append(el('button',{class:'text-button',onClick:()=>openDebug(run.id)},'Inspect the debug report ↗'));
    turn.append(answer);return turn;
  }
  const result=run.answer;
  if(!result){turn.append(answer);return turn;}
  answer.append(el('span',{class:'status-chip '+(result.status==='supported'?'':'qualified')},result.request_boundary_only?'Research request limit':statusNames[result.status]||human(result.status)));
  if(result.status==='partial')answer.append(el('p',{class:'status-description'},'The evidence supports part of the question. The unresolved points are listed below.'));
  if(result.request_boundary_only)answer.append(el('p',{},'This request was checked against the research-only boundary. No source search was run.'));
  else if(result.status==='not_found')answer.append(el('p',{},'I could not establish a suitable answer from the supplied evidence. This does not mean that no rule exists.'));
  if(result.findings?.length) {
    answer.append(el('h3',{class:'section-heading'},'What the evidence supports'));
    for(const finding of result.findings)answer.append(el('div',{class:'finding'},el('p',{},finding.text),...(finding.citations||[]).map(ref=>cite(ref,run))));
  }
  if(result.quotes?.length) {
    const language=el('details',{class:'source-language',...(!result.findings?.length?{open:''}:{})},el('summary',{class:'section-heading'},`Verbatim source passages (${result.quotes.length})`));
    for(const [index,quote] of result.quotes.entries()) {
      const content=el('div',{class:'quote-card'});
      content.append(el('button',{class:'text-button',onClick:()=>openSource(run.id,index)},citationLabel(quote.citation),' · View in document ↗'));
      if(quote.text.length>1800)content.append(el('details',{},el('summary',{},'Read full supporting quotation'),el('blockquote',{},quote.text)));
      else content.append(el('blockquote',{},quote.text));
      language.append(content);
    }
    answer.append(language);
  }
  const uncertainties=result.uncertainties||[];
  if(uncertainties.length)answer.append(el('section',{class:'uncertainties'},el('h3',{class:'section-heading'},result.request_boundary_only?'Research boundaries':'What remains unresolved'),el('ul',{},...uncertainties.map(u=>el('li',{},u.description)))));
  const questions=[...(result.clarifying_questions||[]),...uncertainties.map(u=>u.question_for_user).filter(Boolean)];
  if(questions.length)answer.append(el('section',{class:'clarifications'},el('h3',{class:'section-heading'},'More information needed'),el('ul',{},...[...new Set(questions)].map(q=>el('li',{},q)))));
  answer.append(el('div',{class:'response-footer'},el('span',{},result.request_boundary_only?'Public-rule research only · No source search was run':'Based on the supplied corpus · Current applicability is not independently verified'),el('button',{class:'text-button',onClick:()=>openDebug(run.id)},'View debug report ↗')));
  turn.append(answer);return turn;
}
function renderMessages() {
  $('welcome').hidden=state.runs.length>0;
  $('context-row').hidden=!state.runs.length;
  $('messages').replaceChildren(...state.runs.map(renderRun));
  $('conversation-title').textContent=state.runs.length?state.runs[0].question:'New research';
}
async function history() {
  const threads=await api('/api/threads');
  $('history').replaceChildren(...(threads.length?threads.map(t=>el('button',{class:'thread '+(t.id===state.thread?'active':''),title:t.title,onClick:()=>openThread(t.id)},t.title)):[el('p',{class:'history-empty'},'Your conversations will appear here.')]));
  return threads;
}
async function openThread(identity) {
  const navigation=++state.navigation;
  try {
    const runs=await api('/api/threads/'+identity);
    if(navigation!==state.navigation)return;
    state.thread=identity;state.runs=runs;rememberThread(identity);
    if(runs.length)fillScope(runs.at(-1).scope);
    renderMessages();await history();document.querySelector('.workspace').classList.remove('show-history');
  }catch(error){message(error.message);}
}
function newResearch() {
  state.navigation++;state.thread=null;state.runs=[];rememberThread(null);renderMessages();history();
  $('question').value='';$('program').value='New York Medicaid';$('scope-toggle').firstChild.textContent='New York Medicaid ';$('use-history').checked=true;$('question').focus();document.querySelector('.workspace').classList.remove('show-history');
}
function fillScope(scope) {
  if(!scope)return;
  $('program').value=scope.program||'';
}
function busy(value) {state.busy=value;$('send').disabled=value || !state.config;}
function upsert(run) {
  const index=state.runs.findIndex(r=>r.id===run.id);
  if(index<0)state.runs.push(run);else state.runs[index]=run;
  const old=$('run-'+run.id);
  if(old)old.replaceWith(renderRun(run));else renderMessages();
}
async function poll(runId) {
  let previous='';
  try {
    while(true) {
      const run=await api('/api/runs/'+runId);
      if(run.thread_id===state.thread && previous!==run.status+run.progress){upsert(run);previous=run.status+run.progress;}
      if(run.status!=='running')break;
      await new Promise(resolve=>setTimeout(resolve,1200));
    }
    await history();
  }catch(error){message(error.message);}
  finally{busy(false);}
}
async function submit(event) {
  event.preventDefault();if(state.busy)return;
  const question=$('question').value.trim();if(!question)return;
  busy(true);
  try {
    const scope={program:$('program').value.trim()||null};
    const run=await api('/api/ask',{question,thread_id:state.thread,use_history:$('use-history').checked,scope});
    if(state.thread!==run.thread_id){state.thread=run.thread_id;state.runs=[];rememberThread(run.thread_id);}
    fillScope(run.scope);upsert(run);$('question').value='';await history();
    $('chat-scroll').scrollTo({top:$('chat-scroll').scrollHeight,behavior:'smooth'});
    if(run.status==='running')poll(run.id);else busy(false);
  }catch(error){busy(false);message(error.message);}
}
function selectCase(number) {
  updateSavedExample();
  const sample=state.config.scenarios.find(c=>c.id===Number(number));if(!sample)return;
  if(state.runs.length)newResearch();
  $('question').value=sample.question;$('program').value=sample.scope.program;
  $('question').focus();
}
function savedAwsCase() {
  const selected=Number($('examples').value)||1;
  return (state.config.saved_aws_cases||[]).includes(selected)?selected:null;
}
function updateSavedExample() {
  const aws=savedAwsCase(),selected=Number($('examples').value)||1;
  $('saved-example').hidden=!aws && !(selected===1 && state.config.saved_example_available);
  $('saved-example').textContent=aws?'Open saved AWS run ↗':'Open saved local run ↗';
}
async function savedExample() {
  try {
    $('saved-example').disabled=true;
    const run=await api('/api/example',savedAwsCase()?{case:savedAwsCase()}:{});state.thread=run.thread_id;state.runs=[run];fillScope(run.scope);
    rememberThread(run.thread_id);renderMessages();await history();
  }catch(error){message(error.message);}
  finally{$('saved-example').disabled=false;}
}
function jsonDetails(label,value) {return el('details',{},el('summary',{},label),el('pre',{},JSON.stringify(value,null,2)));}
function modelLabel(settings) {
  return settings.provider==='bedrock'?(settings.bedrock_models.find(m=>m.id===settings.bedrock_model)?.name||settings.bedrock_model):settings.local_model||'Choose a local model';
}
function applySettings(settings) {
  state.config.model_settings=settings;state.settings=settings;
  state.config.model=settings.provider==='bedrock'?settings.bedrock_model:settings.local_model;
  $('model-label').textContent=modelLabel(settings)+' · Titan retrieval';
  $('provider-badge').textContent=settings.provider==='bedrock'?'Bedrock':'Local model';
}
function settingsError(text='') {$('settings-error').textContent=text;$('settings-error').hidden=!text;}
function providerFields() {
  const local=$('settings-provider').value!=='bedrock';
  $('local-fields').hidden=!local;$('bedrock-fields').hidden=local;
  for(const input of $('local-fields').querySelectorAll('input,select,button'))input.disabled=!local;
  const model=state.settings?.bedrock_models.find(m=>m.id===$('settings-bedrock-model').value);
  $('settings-price').textContent=model?`Published rate per million tokens: $${model.input_per_million} input / $${model.output_per_million} output. Actual cost depends on usage.`:'';
}
function accountStatus(settings) {
  const auth=settings.authorization;
  const approved=auth?.generation_model_id?`Titan query embeddings, ${auth.generation_model_id}${auth.answer_model_id?' and '+auth.answer_model_id+' (answer writing)':''}`:'Titan query embeddings only';
  $('settings-account').textContent=auth?`Account ${auth.account} · ${settings.aws_enabled?'enabled':'expired'} · allowance $${auth.allowance_usd}\nApproved: ${approved}\nExpires ${new Date(auth.expires_at).toLocaleString()}`:'Check your local AWS SSO profile before enabling paid calls. No access keys are entered here.';
  $('settings-budget').textContent=`This computer: $${settings.budget.committed_upper_usd.toFixed(3)} committed including conservative reservations, of a $${settings.budget.cap_usd.toFixed(2)} total cap. This is not an AWS invoice.`;
}
async function openSettings() {
  const dialog=$('settings-dialog');if(!dialog.open)dialog.showModal();settingsError();
  try {
    const value=await api('/api/settings');state.settings=value;state.checkedAccount=null;state.localModels=[];
    $('settings-provider').value=value.provider;$('settings-local-url').value=value.local_url;
    $('settings-profile').value=value.aws_profile;$('settings-context').value=String(value.context_tokens);
    $('settings-local-ack').checked=value.local_size_acknowledged;$('settings-authorize').checked=false;$('save-settings').textContent='Save settings';
    $('settings-answer-model').checked=value.use_answer_model!==false;
    const writer=value.answer_model;$('settings-answer-model').closest('label').hidden=!writer;
    if(writer)$('settings-answer-model-label').textContent=`Write the final answer with ${writer.name} ($${writer.input_per_million} input / $${writer.output_per_million} output per million tokens; about one cent more per question). The model above still plans the search and screens passages. Changing this needs a new authorization.`;
    $('settings-bedrock-model').replaceChildren(...value.bedrock_models.map(m=>el('option',{value:m.id},m.name)));
    $('settings-bedrock-model').value=value.bedrock_model;
    $('settings-local-model').replaceChildren(el('option',{value:value.local_model||''},value.local_model||'Find models to choose one…'));
    accountStatus(value);providerFields();$('save-settings').disabled=state.busy;
  }catch(error){settingsError(error.message);}
}
async function findLocalModels() {
  settingsError();$('refresh-local-models').disabled=true;
  try {
    const data=await api('/api/settings/discover',{provider:$('settings-provider').value,url:$('settings-local-url').value.trim()});
    state.localModels=data.models;
    $('settings-local-model').replaceChildren(el('option',{value:''},data.models.length?'Choose an installed model…':'No models found'),...data.models.map(m=>{
      const label=m.name+(m.size_bytes?` · ${(m.size_bytes/1024**3).toFixed(1)} GiB`:'')+(m.blocked?' · too large for this machine':'');
      const option=el('option',{value:m.id},label);option.disabled=m.blocked;return option;
    }));
    const previous=state.settings?.local_model;
    if(data.models.some(m=>m.id===previous&&!m.blocked))$('settings-local-model').value=previous;
    $('local-model-note').textContent=data.note+(data.physical_memory_bytes?` Computer RAM: ${(data.physical_memory_bytes/1024**3).toFixed(0)} GiB.`:'');
  }catch(error){settingsError(error.message);}
  finally{$('refresh-local-models').disabled=false;}
}
async function checkAws() {
  settingsError();$('check-aws').disabled=true;state.checkedAccount=null;
  try {
    const data=await api('/api/settings/aws',{profile:$('settings-profile').value.trim()});
    state.checkedAccount=data.account;
    $('settings-account').textContent=`Verified account ${data.account} in ${data.region}. ${data.note}`;
    const selected=$('settings-bedrock-model').value;
    $('settings-bedrock-model').replaceChildren(...data.models.map(m=>el('option',{value:m.id},m.name)));
    if(data.models.some(m=>m.id===selected))$('settings-bedrock-model').value=selected;
    providerFields();
  }catch(error){settingsError(error.message);}
  finally{$('check-aws').disabled=false;}
}
async function saveSettings(event) {
  event.preventDefault();settingsError();$('save-settings').disabled=true;
  try {
    if($('settings-authorize').checked&&!state.checkedAccount)throw Error('Check the AWS account before authorizing paid calls.');
    const value=await api('/api/settings',{provider:$('settings-provider').value,bedrock_model:$('settings-bedrock-model').value,
      local_model:$('settings-local-model').value,local_url:$('settings-provider').value==='bedrock'?state.settings.local_url:$('settings-local-url').value.trim(),
      context_tokens:Number($('settings-context').value),local_size_acknowledged:$('settings-local-ack').checked,
      use_answer_model:$('settings-answer-model').checked,
      aws_profile:$('settings-profile').value.trim(),authorize_aws:$('settings-authorize').checked,
      confirmed_account:state.checkedAccount,allowance_usd:$('settings-allowance').value});
    applySettings(value);$('settings-dialog').close();message('Settings saved. Future answers will use '+modelLabel(value)+'.');
  }catch(error){settingsError(error.message);}
  finally{$('save-settings').disabled=false;}
}
async function disableAws() {
  try {const value=await api('/api/settings/disable',{});applySettings(value);accountStatus(value);$('settings-authorize').checked=false;message('Paid AWS calls disabled.');}
  catch(error){settingsError(error.message);}
}
function table(headers,rows) {return el('table',{},el('thead',{},el('tr',{},...headers.map(h=>el('th',{},h)))),el('tbody',{},...rows.map(r=>el('tr',{},...r.map(c=>el('td',{},c))))));}
async function openDebug(runId) {
  const dialog=$('debug-dialog');$('debug-content').replaceChildren(el('p',{},'Loading the saved research trace…'));dialog.showModal();
  try {
    const report=await api(`/api/runs/${runId}/debug`), content=$('debug-content');
    const download=el('button',{class:'small-button',onClick:()=>{
      const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
      const link=el('a',{href:url,download:'research-debug.json'});link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }},'Download debug JSON');
    content.replaceChildren(el('p',{class:'debug-note'},'Observable execution and brief model review summaries. Private chain-of-thought is not collected. Model assessments do not establish expert correctness.'),download);
    if(report.error)content.append(el('p',{class:'failure'},report.error));
    content.append(el('h3',{},'Documents accessed and connected'));
    content.append(table(['Document','How it was used'],(report.documents||[]).map(d=>[safeLink(d.source_url,d.title||d.document_id),[d.retrieved?'Retrieved':null,d.expanded_context_loaded?'Context loaded':null,d.graph_connected?'Graph connected':null,!d.retrieved&&!d.expanded_context_loaded?'Text not loaded':null].filter(Boolean).join(' · ')])));
    content.append(el('h3',{},'Execution timeline'),table(['Step','Role','Elapsed'],report.events.map(e=>[human(e.stage),e.role||'—',e.elapsed_seconds+'s'])));
    for(const event of report.events.filter(e=>e.retrieval))content.append(jsonDetails('Retrieval · '+human(event.stage),event.retrieval));
    for(const event of report.events.filter(e=>e.stage==='model_completed'&&e.role==='review')) {
      const summary=event.decision_summary;content.append(el('h3',{},'Reviewer summaries'));
      if(summary.review_notes?.length)content.append(el('ul',{},...summary.review_notes.map(n=>el('li',{},n))));
      content.append(el('div',{class:'checks'},...Object.entries(summary.checks||{}).map(([key,value])=>el('span',{class:'check '+(value?'':'failed')},human(key)+': '+(value?'pass':'unresolved')))));
      if(summary.issues?.length)content.append(el('h3',{},'Unresolved review issues'),el('ul',{},...summary.issues.map(n=>el('li',{},n))));
      if(summary.additional_query)content.append(el('p',{},'Additional corpus search: '+summary.additional_query));
    }
    const edges=new Map(),omissions=[];
    for(const event of report.events){for(const edge of event.context_expansion?.relationships||[])edges.set(edge.relationship_id,edge);omissions.push(...event.context_expansion?.omissions||[]);}
    if(edges.size){content.append(el('h3',{},'Relationship map connections'),el('p',{class:'debug-note'},'These are recorded relationship leads, not automatic precedence or currentness decisions.'),table(['Relationship','Target','Status'],[...edges.values()].map(e=>[human(e.relation_type),e.target_reference||e.target_document_id||'Unresolved target',human(e.status)])));}
    if(omissions.length)content.append(jsonDetails(`Context omissions and missing targets (${omissions.length})`,omissions));
    content.append(el('h3',{},'Canonical passages accessed'));
    for(const item of report.evidence||[])content.append(el('details',{class:'debug-evidence'},el('summary',{},`${item.evidence_id} · ${item.title||item.document_id}${item.page?' · page '+item.page:''}`),el('p',{},item.chunk_id),el('blockquote',{},item.text),jsonDetails('Source metadata and offsets',Object.fromEntries(Object.entries(item).filter(([k])=>k!=='text')))));
    content.append(el('h3',{},'Model evidence, instructions, and public outputs'));
    for(const event of report.events.filter(e=>e.stage.startsWith('model_')))content.append(jsonDetails(`${human(event.stage)} · ${event.role||''} · ${event.elapsed_seconds}s`,event));
    content.append(jsonDetails('Full result, validation, and provenance',report.result));
  }catch(error){$('debug-content').replaceChildren(el('p',{class:'failure'},error.message));}
}
function stopObservers() {for(const observer of state.observers)observer.disconnect();state.observers=[];}
async function openSource(runId,index) {
  const serial=++state.viewerSerial;stopObservers();
  const dialog=$('source-dialog');if(!dialog.open)dialog.showModal();
  setBrowse(false);
  $('source-heading').textContent='Loading saved source…';$('document-pages').replaceChildren(el('p',{},'Verifying the saved source and locating the quotation…'));
  $('selected-quote').textContent='';$('highlight-note').textContent='';
  try {
    const base=`/api/runs/${runId}/quotes/${index}`,data=await api(base);
    if(serial!==state.viewerSerial)return;
    state.viewer={...data,base,runId,index,currentPage:data.page};
    $('source-heading').textContent=data.title||'Source document';$('selected-quote').textContent=data.quote;
    const official=safeLink(data.source_url,'Official source ↗');$('official-link').replaceWith(Object.assign(official,{id:'official-link'}));
    $('original-link').href=base+'/original'+(data.page?'#page='+data.page:'');$('original-link').hidden=!data.original_available;
    $('document-pages').classList.remove('zoomed','hide-highlights');$('zoom').textContent='Zoom in';$('zoom').setAttribute('aria-pressed','false');$('toggle-highlights').textContent='Highlights on';$('toggle-highlights').setAttribute('aria-pressed','true');
    if(data.kind==='pdf')renderPdf(data);else renderText(data);
  }catch(error){$('document-pages').replaceChildren(el('p',{class:'failure'},error.message));$('source-heading').textContent='Source unavailable';}
}
function highlightNote(data,page) {
  if(!data.quote){
    if(!data.find)return '';
    const count=data.page_highlights?.[String(page)]?.boxes.length||0;
    return count?count+(count===1?' match':' matches')+' on this page.':'No matches on this page.';
  }
  const selected=data.page_highlights?.[String(page)];
  if(!data.citation_pages?.includes(page))return 'The quoted passage is on PDF page '+data.citation_pages.join(', ')+'.';
  if(!selected?.boxes?.length)return 'The exact passage is shown below. Its location could not be reliably highlighted on this page.';
  return selected.status.includes('partial')?'Some source lines matched. Highlighting is partial; compare the full quotation below.':'Highlighted text matches the cited source passage.';
}
function renderPdf(data) {
  const container=$('document-pages');container.replaceChildren();
  $('page-number').disabled=false;$('page-number').max=data.page_count;$('page-number').value=data.page;$('page-count').textContent='of '+data.page_count;
  for(const id of ['previous-page','next-page','jump-citation','toggle-highlights','zoom'])$(id).disabled=false;
  const imageObserver=new IntersectionObserver(entries=>{
    for(const entry of entries){const image=entry.target.querySelector('img');if(entry.isIntersecting){if(!image.src)image.src=image.dataset.src;}else if(image.hasAttribute('src'))image.removeAttribute('src');}
  },{root:container,rootMargin:'1200px 0px'});
  const visiblePages=new Map();
  const pageObserver=new IntersectionObserver(entries=>{
    for(const entry of entries){if(entry.isIntersecting)visiblePages.set(entry.target,entry);else visiblePages.delete(entry.target);}
    const visible=[...visiblePages.values()].sort((a,b)=>b.intersectionRatio-a.intersectionRatio);
    if(visible[0]){const page=Number(visible[0].target.dataset.page);state.viewer.currentPage=page;$('page-number').value=page;$('highlight-note').textContent=highlightNote(data,page);}
  },{root:container,threshold:[0,.1,.3,.5]});
  state.observers.push(imageObserver,pageObserver);
  for(const layout of data.page_layouts) {
    const page=el('div',{class:'pdf-page',id:'pdf-page-'+layout.page,'data-page':layout.page,'aria-label':'PDF page '+layout.page});
    page.style.aspectRatio=`${layout.width} / ${layout.height}`;
    const image=el('img',{alt:'Saved PDF, page '+layout.page,'data-src':`${state.viewer.base}/page/${layout.page}.png`,width:Math.round(layout.width*1.5),height:Math.round(layout.height*1.5)});
    image.addEventListener('load',()=>page.querySelector('.page-placeholder')?.remove());
    image.addEventListener('error',()=>{if(page.querySelector('.page-placeholder'))page.querySelector('.page-placeholder').textContent='This page could not be loaded. Open the saved original to inspect it.';});
    page.append(el('div',{class:'page-placeholder'},'Page '+layout.page),image);
    const boxes=data.page_highlights?.[String(layout.page)]?.boxes||[];
    if(boxes.length){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox',`0 0 ${layout.width} ${layout.height}`);svg.setAttribute('class','mark-layer');svg.setAttribute('aria-hidden','true');
      for(const [x0,y0,x1,y1] of boxes){const rect=document.createElementNS(svg.namespaceURI,'rect');for(const [k,v] of Object.entries({x:x0,y:y0,width:x1-x0,height:y1-y0}))rect.setAttribute(k,v);svg.append(rect);}page.append(svg);}
    container.append(page);imageObserver.observe(page);pageObserver.observe(page);
  }
  $('highlight-note').textContent=highlightNote(data,data.page);
  requestAnimationFrame(()=>jumpPage(data.page));
}
function renderText(data) {
  $('page-number').value='';$('page-number').disabled=true;$('page-count').textContent='Text source';
  for(const id of ['previous-page','next-page','jump-citation','zoom'])$(id).disabled=true;
  const characters=Array.from(data.text);
  const before=characters.slice(0,data.document_start_char).join(''),after=characters.slice(data.document_end_char).join('');
  const mark=el('mark',{id:'text-citation'},characters.slice(data.document_start_char,data.document_end_char).join(''));
  $('document-pages').replaceChildren(el('article',{class:'source-text'},before,mark,after));
  $('highlight-note').textContent='Exact canonical quotation highlighted in the saved text extraction.';
  requestAnimationFrame(()=>mark.scrollIntoView({block:'center'}));
}
function jumpPage(page) {
  if(!state.viewer?.page_count)return;
  page=Math.min(state.viewer.page_count,Math.max(1,Number(page)||1));
  $('pdf-page-'+page)?.scrollIntoView({block:'start',behavior:'instant'});state.viewer.currentPage=page;$('page-number').value=page;
  $('highlight-note').textContent=highlightNote(state.viewer,page);
}

/* ---------- Document library (sidebar) ---------- */
const BROWSE_FLAG='oracle.sidebarTab';
function setBrowse(on) {
  $('source-dialog').classList.toggle('browse',on);
  for(const id of ['doc-find','doc-about'])$(id).hidden=!on;
  for(const id of ['quote-eyebrow','selected-quote','jump-citation'])$(id).hidden=on;
  if(!on)$('highlight-note').hidden=false;
}
function setTab(name,focus=false) {
  for(const tab of ['research','documents']){
    const selected=tab===name;
    $('tab-'+tab).setAttribute('aria-selected',String(selected));$('tab-'+tab).tabIndex=selected?0:-1;$('panel-'+tab).hidden=!selected;
  }
  try{localStorage.setItem(BROWSE_FLAG,name);}catch{}
  if(name==='documents'){if(!state.library.docs)loadLibrary();if(focus)$('doc-search').focus();}
}
async function loadLibrary() {
  const target=$('doc-results');
  if(!state.library.docs)target.replaceChildren(el('p',{class:'doc-empty'},'Loading documents…'));
  try {
    const data=await api('/api/documents');
    state.library.docs=data.documents;state.library.categories=data.categories;
    $('doc-count').textContent=data.documents.length;$('doc-count').hidden=false;
    renderLibrary();
  }catch(error){target.replaceChildren(el('p',{class:'doc-empty'},'Documents could not be loaded: '+error.message));}
}
function snippetParts(snippet) {
  const parts=[];let marked=false,current='';
  const flush=()=>{if(current)parts.push(marked?el('mark',{},current):current);current='';};
  for(const ch of snippet){
    if(ch==='\u0001'){flush();marked=true;}
    else if(ch==='\u0002'){flush();marked=false;}
    else current+=ch;
  }
  flush();
  // The longest run of adjacent highlighted words becomes the phrase to find inside the document.
  let best='',phrase=[];
  const close=()=>{if(phrase.join(' ').length>best.length)best=phrase.join(' ');phrase=[];};
  for(const part of parts){
    if(part instanceof Node)phrase.push(part.textContent);
    else if(!part.trim()&&phrase.length)continue;
    else close();
  }
  close();
  return {parts,phrase:best};
}
function docItem(doc,extra={}) {
  const active=state.viewer?.docId===doc.id&&$('source-dialog').open;
  const kind=doc.kind==='pdf'?'PDF':'Web';
  const button=el('button',{class:'doc-item'+(active?' active':''),type:'button','data-id':doc.id,title:doc.title,onClick:()=>openDocument(doc,extra.open||{})},
    el('span',{class:'doc-title'},doc.title),
    extra.snippet?el('span',{class:'doc-snippet'},...extra.snippet):null,
    el('span',{class:'doc-meta'},el('span',{class:'doc-kind '+doc.kind},kind),doc.host,extra.page?` · p. ${extra.page}`:''));
  return button;
}
function renderLibrary() {
  const target=$('doc-results'),docs=state.library.docs;if(!docs)return;
  const query=$('doc-search').value.trim(),nodes=[];
  if(!query){
    for(const category of state.library.categories){
      const group=docs.filter(doc=>doc.category===category.id);if(!group.length)continue;
      nodes.push(el('div',{class:'doc-group'},el('span',{},category.label),el('small',{},group.length)));
      nodes.push(...group.map(doc=>docItem(doc)));
    }
  }else{
    const tokens=query.toLowerCase().split(/\s+/);
    const titled=docs.filter(doc=>{const hay=doc.title.toLowerCase();return tokens.every(token=>hay.includes(token));});
    const matches=state.library.matches?.query===query?state.library.matches.items:null;
    if(titled.length){
      nodes.push(el('div',{class:'doc-group'},el('span',{},'Titles'),el('small',{},titled.length)));
      nodes.push(...titled.map(doc=>docItem(doc,{open:{find:''}})));
    }
    if(query.length>=3){
      if(matches===null)nodes.push(el('div',{class:'doc-group'},el('span',{},'Searching the text…')));
      else if(matches.length){
        nodes.push(el('div',{class:'doc-group'},el('span',{},'Mentioned in text'),el('small',{},matches.length)));
        for(const hit of matches){
          const {parts,phrase}=snippetParts(hit.snippet);
          nodes.push(docItem(hit,{snippet:parts,page:hit.page,open:{find:phrase||query,page:hit.page||undefined}}));
        }
      }
    }
    if(!titled.length&&matches!==null&&!matches.length)nodes.push(el('p',{class:'doc-empty'},`No document matches “${query}”.`));
    else if(!titled.length&&matches===null&&query.length<3)nodes.push(el('p',{class:'doc-empty'},'Keep typing, or clear the box to browse all documents.'));
  }
  target.replaceChildren(...nodes);
}
function searchLibrary() {
  const query=$('doc-search').value.trim();
  renderLibrary();
  clearTimeout(state.library.timer);
  if(query.length<3)return;
  const serial=++state.library.serial;
  state.library.timer=setTimeout(async()=>{
    try{
      const data=await api('/api/documents?q='+encodeURIComponent(query));
      if(serial!==state.library.serial)return;
      state.library.matches={query,items:data.matches};renderLibrary();
    }catch(error){if(serial===state.library.serial){state.library.matches={query,items:[]};renderLibrary();}}
  },220);
}
function documentAbout(item) {
  const categories=Object.fromEntries((state.library.categories||[]).map(c=>[c.id,c.label]));
  const note=el('div',{},el('div',{class:'about-row'},el('span',{},'Type'),el('b',{},categories[item.category]||'Source')),
    el('div',{class:'about-row'},el('span',{},'Format'),el('b',{},item.kind==='pdf'?'PDF':'Web page, saved text')),
    el('div',{class:'about-row'},el('span',{},'From'),el('b',{},item.host)));
  $('doc-about').replaceChildren(el('div',{class:'eyebrow'},'ABOUT THIS DOCUMENT'),note);
}
async function openDocument(item,{page,find=''}={}) {
  const serial=++state.viewerSerial;stopObservers();
  const dialog=$('source-dialog');if(!dialog.open)dialog.showModal();
  document.querySelector('.workspace').classList.remove('show-history');
  setBrowse(true);
  $('source-heading').textContent=item.title;$('doc-find-input').value=find;$('find-status').textContent='';
  $('document-pages').replaceChildren(el('p',{class:'doc-empty'},'Opening document…'));
  try {
    const base='/api/documents/'+item.id,query=new URLSearchParams();
    if(page)query.set('page',page);if(find)query.set('find',find);
    const data=await api(base+(query.toString()?'?'+query:''));
    if(serial!==state.viewerSerial)return;
    state.viewer={...data,base,docId:item.id,item,currentPage:data.page||1,matchIndex:-1};
    $('source-heading').textContent=item.title;
    const official=safeLink(data.source_url,'Official source ↗');$('official-link').replaceWith(Object.assign(official,{id:'official-link'}));
    $('original-link').href=base+'/original'+(data.page?'#page='+data.page:'');$('original-link').hidden=!data.original_available;
    $('document-pages').classList.remove('zoomed','hide-highlights');$('zoom').textContent='Zoom in';$('zoom').setAttribute('aria-pressed','false');$('toggle-highlights').textContent='Highlights on';$('toggle-highlights').setAttribute('aria-pressed','true');
    documentAbout(item);
    if(data.kind==='pdf'){
      renderPdf(data);
      if(find){
        const pages=data.find_pages||[],start=page||1,first=pages.find(n=>n>=start)||pages[0];
        $('find-status').textContent=pages.length?`${data.find_count} ${data.find_count===1?'match':'matches'} · ${pages.length} ${pages.length===1?'page':'pages'}`:'No matches';
        $('find-prev').disabled=$('find-next').disabled=!pages.length;
        if(first)requestAnimationFrame(()=>jumpPage(first));
      }
    }else renderTextDocument(data,find);
    for(const button of document.querySelectorAll('.doc-item'))button.classList.toggle('active',button.dataset.id===item.id);
  }catch(error){if(serial===state.viewerSerial){$('document-pages').replaceChildren(el('p',{class:'failure'},error.message));$('source-heading').textContent='Document unavailable';}}
}
function renderTextDocument(data,find) {
  $('page-number').value='';$('page-number').disabled=true;$('page-count').textContent='Text source';
  for(const id of ['previous-page','next-page','zoom'])$(id).disabled=true;
  const article=el('article',{class:'source-text'}),text=data.text,marks=[];
  if(find){
    const lower=text.toLowerCase(),needle=find.toLowerCase();let at=0,from=0;
    while(marks.length<500&&(at=lower.indexOf(needle,from))!==-1){
      article.append(text.slice(from,at));const mark=el('mark',{},text.slice(at,at+needle.length));article.append(mark);marks.push(mark);from=at+needle.length;
    }
    article.append(text.slice(from));
  }else article.append(text);
  state.viewer.marks=marks;
  $('document-pages').replaceChildren(article);
  $('highlight-note').textContent='';
  $('find-status').textContent=find?(marks.length?`${marks.length}${marks.length>=500?'+':''} ${marks.length===1?'match':'matches'}`:'No matches'):'';
  $('find-prev').disabled=$('find-next').disabled=marks.length<2;
  if(marks.length)stepMatch(1);
}
function stepMatch(direction) {
  const viewer=state.viewer;if(!viewer?.docId)return;
  if(viewer.kind==='pdf'){
    const pages=viewer.find_pages||[];if(!pages.length)return;
    const here=viewer.currentPage;
    const next=direction>0?(pages.find(n=>n>here)||pages[0]):([...pages].reverse().find(n=>n<here)||pages[pages.length-1]);
    jumpPage(next);return;
  }
  const marks=viewer.marks||[];if(!marks.length)return;
  marks[viewer.matchIndex]?.classList.remove('current');
  viewer.matchIndex=(viewer.matchIndex+direction+marks.length)%marks.length;
  const mark=marks[viewer.matchIndex];mark.classList.add('current');mark.scrollIntoView({block:'center'});
  $('find-status').textContent=`${viewer.matchIndex+1} of ${marks.length}${marks.length>=500?'+':''}`;
}
function submitFind(event) {
  event.preventDefault();
  const viewer=state.viewer;if(!viewer?.docId)return;
  const find=$('doc-find-input').value.trim();
  if(viewer.kind==='pdf')openDocument(viewer.item,{page:viewer.currentPage,find});
  else{renderTextDocument(viewer,find);}
}
function libraryKeys(event) {
  const items=[...document.querySelectorAll('#doc-results .doc-item')];
  if(event.target===$('doc-search')){
    if(event.key==='ArrowDown'&&items.length){event.preventDefault();items[0].focus();}
    else if(event.key==='Enter'&&items.length){event.preventDefault();items[0].click();}
    else if(event.key==='Escape'&&$('doc-search').value){event.preventDefault();$('doc-search').value='';searchLibrary();}
  }else if(event.target.classList?.contains('doc-item')){
    const at=items.indexOf(event.target);
    if(event.key==='ArrowDown'&&items[at+1]){event.preventDefault();items[at+1].focus();}
    else if(event.key==='ArrowUp'){event.preventDefault();(items[at-1]||$('doc-search')).focus();}
  }
}

$('tab-research').addEventListener('click',()=>setTab('research'));
$('tab-documents').addEventListener('click',()=>setTab('documents'));
for(const id of ['tab-research','tab-documents'])$(id).addEventListener('keydown',event=>{if(event.key==='ArrowRight'||event.key==='ArrowLeft'){const name=id==='tab-research'?'documents':'research';setTab(name);$('tab-'+name).focus();}});
$('doc-search').addEventListener('input',searchLibrary);
$('panel-documents').addEventListener('keydown',libraryKeys);
$('doc-find').addEventListener('submit',submitFind);
$('find-next').addEventListener('click',()=>stepMatch(1));
$('find-prev').addEventListener('click',()=>stepMatch(-1));
document.addEventListener('keydown',event=>{
  if(event.key==='/'&&!event.metaKey&&!event.ctrlKey&&!event.altKey&&!/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName)&&!document.querySelector('dialog[open]')){
    event.preventDefault();document.querySelector('.workspace').classList.add('show-history');setTab('documents',true);
  }
});
$('question-form').addEventListener('submit',submit);
$('open-settings').addEventListener('click',openSettings);
$('close-settings').addEventListener('click',()=>$('settings-dialog').close());
$('settings-form').addEventListener('submit',saveSettings);
$('refresh-local-models').addEventListener('click',findLocalModels);
$('check-aws').addEventListener('click',checkAws);
$('disable-aws').addEventListener('click',disableAws);
$('settings-bedrock-model').addEventListener('change',providerFields);
$('settings-provider').addEventListener('change',()=>{
  $('settings-local-ack').checked=false;
  const provider=$('settings-provider').value;
  if(provider==='ollama')$('settings-local-url').value='http://127.0.0.1:11434';
  else if(provider!=='bedrock')$('settings-local-url').value='http://127.0.0.1:1234';
  $('settings-local-model').replaceChildren(el('option',{value:''},'Find models to choose one…'));providerFields();
});
$('settings-local-url').addEventListener('input',()=>{$('settings-local-ack').checked=false;});
$('settings-local-model').addEventListener('change',()=>{$('settings-local-ack').checked=false;});
$('settings-profile').addEventListener('input',()=>{state.checkedAccount=null;$('settings-authorize').checked=false;});
$('settings-authorize').addEventListener('change',()=>{$('save-settings').textContent=$('settings-authorize').checked?'Save and authorize AWS':'Save settings';});
$('question').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('question-form').requestSubmit();}});
$('new-chat').addEventListener('click',newResearch);
$('mobile-menu').addEventListener('click',()=>document.querySelector('.workspace').classList.toggle('show-history'));
$('scope-toggle').addEventListener('click',()=>{$('scope-panel').hidden=!$('scope-panel').hidden;$('scope-toggle').setAttribute('aria-expanded',String(!$('scope-panel').hidden));});
$('program').addEventListener('input',()=>{$('scope-toggle').firstChild.textContent=$('program').value||'Research scope';});
$('examples').addEventListener('change',event=>selectCase(event.target.value));
$('saved-example').addEventListener('click',savedExample);
$('close-debug').addEventListener('click',()=>$('debug-dialog').close());
$('close-source').addEventListener('click',()=>$('source-dialog').close());
$('source-dialog').addEventListener('close',()=>{state.viewerSerial++;stopObservers();for(const button of document.querySelectorAll('.doc-item.active'))button.classList.remove('active');});
$('previous-page').addEventListener('click',()=>jumpPage(state.viewer.currentPage-1));
$('next-page').addEventListener('click',()=>jumpPage(state.viewer.currentPage+1));
$('page-number').addEventListener('change',event=>jumpPage(event.target.value));
$('jump-citation').addEventListener('click',()=>jumpPage(state.viewer.citation_pages[0]));
$('toggle-highlights').addEventListener('click',()=>{const off=$('document-pages').classList.toggle('hide-highlights');$('toggle-highlights').textContent=off?'Highlights off':'Highlights on';$('toggle-highlights').setAttribute('aria-pressed',String(!off));});
$('zoom').addEventListener('click',()=>{const zoomed=$('document-pages').classList.toggle('zoomed');$('zoom').textContent=zoomed?'Fit width':'Zoom in';$('zoom').setAttribute('aria-pressed',String(zoomed));requestAnimationFrame(()=>jumpPage(state.viewer.currentPage));});
(async()=>{
  try {
    state.config=await api('/api/config');applySettings(state.config.model_settings);$('send').disabled=false;
    state.threadKey='oracle.activeThread:'+state.config.history_namespace;
    const exampleGroups=[
      ['Client reference questions',sample=>Boolean(sample.advocate_case)],
      ['System limits',sample=>sample.test_kind==='negative_boundary'],
      ['Other examples (synthetic)',sample=>!sample.advocate_case && sample.test_kind!=='negative_boundary']
    ];
    for(const [label,includes] of exampleGroups){
      const group=el('optgroup',{label});
      for(const sample of state.config.scenarios.filter(includes))group.append(el('option',{value:sample.id},sample.demo_label||sample.id+'. '+sample.title));
      if(group.children.length)$('examples').append(group);
    }
    updateSavedExample();
    loadLibrary();
    let tab='research';try{tab=localStorage.getItem(BROWSE_FLAG)||'research';}catch{}
    if(tab==='documents')setTab('documents');
    const threads=await history();
    const last=localStorage.getItem(state.threadKey)||localStorage.getItem('oracle.activeThread');
    localStorage.removeItem('oracle.activeThread');
    if(last && threads.some(thread=>thread.id===last))await openThread(last);
    else rememberThread(null);
    const running=state.runs.find(r=>r.status==='running');if(running){busy(true);poll(running.id);}
  }catch(error){message('Could not connect to the local research server: '+error.message);}
})();
