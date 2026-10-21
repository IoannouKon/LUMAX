'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const format = value => value == null ? '—' : Number(value).toLocaleString();
const terminal = new Set(['PASS','FAIL','TIMEOUT','ERROR','BUILD_FAIL','STOPPED','UNVERIFIED','INTERRUPTED','ESTIMATED']);
const badge = status => `<span class="pill ${esc(status).toLowerCase()}">${esc(status)}</span>`;
const date = value => value ? new Date(value).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
const state = {designs:[],suites:[],tests:[],runs:[],history:[],selected:null,view:'builder',suiteId:null,logPath:null,planSequence:0};
let token = new URLSearchParams(location.search).get('token') || sessionStorage.getItem('lumax-token') || '';
if(token) sessionStorage.setItem('lumax-token',token);
if(location.search) history.replaceState(null,'',location.pathname);

async function api(path, data) {
  const response = await fetch(path, {method:data === undefined ? 'GET':'POST',
    headers:{'X-Lumax-Token':token,...(data === undefined ? {}:{'Content-Type':'application/json'})},
    ...(data === undefined ? {}:{body:JSON.stringify(data)})});
  const result = await response.json();
  if(!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}
let toastTimer;
function toast(message) {
  $('toast').textContent=message; $('toast').classList.remove('hidden');
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').classList.add('hidden'),5000);
}
function changeView(view) {
  state.view=view;
  document.querySelectorAll('.view').forEach(el=>el.classList.toggle('hidden',el.id!==view));
  document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active',el.dataset.view===view));
  $('breadcrumb').textContent={builder:'Workspace / Suite builder',runs:'Workspace / Runs',history:'Workspace / Saved logs'}[view];
  if(view==='history') loadHistory().catch(e=>toast(e.message));
  if(view==='runs') renderRuns();
}
function selectedDesigns() { return [...document.querySelectorAll('#designs input:checked')].map(el=>el.value); }
function renderDesigns(selected=selectedDesigns()) {
  const custom=['custom','llama_fast'].includes($('suite-kind').value);
  if(!selected.length) selected=['b8_rf8'];
  $('designs').innerHTML=state.designs.map(d=>`<label class="design-option"><span class="design-title"><input type="checkbox" value="${esc(d.id)}" ${selected.includes(d.id)?'checked':''} ${!custom&&!d.builtin?'disabled':''}>${esc(d.name)}</span><small>b ${d.b} · RF ${d.rf}<br>Capacity ${format(d.max_k)} × ${format(d.max_m)}</small></label>`).join('');
  $('designs').querySelectorAll('input').forEach(el=>el.addEventListener('change',requestPlan));
}
const patternNames={random:'Random signed',zero_x:'Zero input',zero_w:'Zero weights',zero_both:'Both zero',min_min:'Minimum × minimum',max_max:'Maximum × maximum',min_max:'Minimum × maximum',max_min:'Maximum × minimum',alternating:'Alternating signs',onehot_x:'One input active',onehot_w:'One weight active'};
function syncTests() {
  state.tests=[...document.querySelectorAll('.matrix-test')].map(el=>({
    name:el.querySelector('[data-field=name]').value,k:el.querySelector('[data-field=k]').value,
    m:el.querySelector('[data-field=m]').value,pattern:el.querySelector('[data-field=pattern]').value,
    weights:[...el.querySelectorAll('[data-weight]:checked')].map(x=>Number(x.value)),
    repeats:el.querySelector('[data-field=repeats]').value,seed:el.querySelector('[data-field=seed]').value}));
}
function renderTests() {
  $('tests').innerHTML=state.tests.map((t,i)=>`<div class="matrix-test" data-index="${i}"><div class="test-top"><span class="test-index">${String(i+1).padStart(2,'0')}</span><input data-field="name" aria-label="Test name" value="${esc(t.name)}" placeholder="Test name"><span class="multiply">K</span><input data-field="k" class="dimension" type="number" min="1" aria-label="Input length K" value="${esc(t.k)}"><span class="multiply">M</span><input data-field="m" class="dimension" type="number" min="1" aria-label="Output length M" value="${esc(t.m)}"><button class="icon-button remove-test" aria-label="Remove test ${i+1}">×</button></div><div class="test-bottom"><select data-field="pattern" aria-label="Data pattern">${Object.entries(patternNames).map(([v,n])=>`<option value="${v}" ${v===t.pattern?'selected':''}>${n}</option>`).join('')}</select><div class="weights">W ${[8,4,2].map(w=>`<label><input data-weight type="checkbox" value="${w}" ${t.weights.includes(w)?'checked':''}>${w}</label>`).join('')}</div><label>Runs <input data-field="repeats" type="number" min="1" max="100" value="${esc(t.repeats||1)}" aria-label="Repeats"></label><label>Seed <input class="seed" data-field="seed" placeholder="Random" value="${esc(t.seed||'')}" inputmode="numeric" aria-label="Optional reproducible seed"></label></div></div>`).join('');
  $('tests').querySelectorAll('input,select').forEach(el=>el.addEventListener('input',requestPlan));
  $('tests').querySelectorAll('.remove-test').forEach((el,i)=>el.addEventListener('click',()=>{syncTests();state.tests.splice(i,1);renderTests();requestPlan();}));
}
function addTest(k=16,m=8) {
  syncTests();state.tests.push({name:`${k}×${m}`,k,m,weights:[8,4,2],pattern:'random',repeats:1,seed:''});
  renderTests();requestPlan();
}
function suitePayload() {
  syncTests();
  const kind=$('suite-kind').value;
  return {id:state.suiteId,name:$('suite-name').value,kind,design_ids:selectedDesigns(),tests:state.tests,
    options:{mode:$('mode').value,reference:$('reference').value,cycles:$('cycles').checked,
      guards:$('guards').checked,matrices:$('matrices').checked,integrity:$('integrity').checked,
      stop_on_fail:$('stop-on-fail').checked,timeout:Math.round(Number($('timeout-hours').value)*3600),
      build_timeout:Math.round(Number($('build-hours').value)*3600),jobs:Number($('build-jobs').value),
      workers:Number($('workers').value),tile_k:$('mode').value==='tile'?Number($('tile-k').value||0):0,tile_m:$('mode').value==='tile'?Number($('tile-m').value||0):0,
      t:Number($('context-t').value),tcs:[...document.querySelectorAll('#tc-picker input:checked')].map(el=>el.value)}};
}
let planTimer;
function requestPlan() {
  clearTimeout(planTimer);planTimer=setTimeout(updatePlan,250);
}
async function updatePlan() {
  const sequence=++state.planSequence;
  try {
    const plan=await api('/api/plan',suitePayload());
    if(sequence!==state.planSequence)return;
    $('plan-cases').textContent=format(plan.cases);$('plan-ops').textContent=format(plan.operations);$('plan-builds').textContent=format(plan.builds);
    $('plan-note').textContent=plan.note;
    const tiled=plan.detail.filter(d=>d.tiles>1);
    $('plan-details').innerHTML=tiled.slice(0,5).map(d=>`<div class="plan-tile"><strong>${esc(d.test)}</strong> · ${esc(d.design)}<br>${plan.estimated?`Sample ${d.sample_k}×${d.sample_m} · 1 measured tile × ${format(d.tiles)} required tiles`:`${format(d.tiles)} HW operations per test${d.reduction?' · partial outputs added in host software':''}`}</div>`).join('')+(tiled.length>5?`<p class="hint">+ ${tiled.length-5} more tiled shapes</p>`:'');
    $('plan-error').classList.add('hidden');$('run').disabled=false;$('save').disabled=false;
    if($('reference').value==='none'&&['custom','llama_fast'].includes($('suite-kind').value))$('plan-note').textContent+=' Comparison is disabled: results will be UNVERIFIED, never PASS.';
  }catch(error){
    if(sequence!==state.planSequence)return;
    ['plan-cases','plan-ops','plan-builds'].forEach(id=>$(id).textContent='—');
    $('plan-error').textContent=error.message;$('plan-error').classList.remove('hidden');$('run').disabled=true;$('save').disabled=true;
  }
}
function kindChanged() {
  const custom=$('suite-kind').value==='custom';
  const fast=$('suite-kind').value==='llama_fast';
  $('matrix-card').classList.toggle('hidden',!custom);
  ['custom-options','advanced-custom'].forEach(id=>$(id).classList.toggle('hidden',!custom&&!fast));
  $('builtin-note').classList.toggle('hidden',custom);
  $('builtin-note').textContent=fast?'Six Llama2 shapes, input16 and W8/W4/W2. Each runs one largest-fitting tile for the selected design. Full cycle totals are extrapolated; only sampled outputs are verified.':'Runs the original script with its original coverage, Python reference and cycle checks.';
  $('placement-field').classList.toggle('hidden',fast);
  $('cycles').disabled=fast;
  if(fast){$('mode').value='native';$('cycles').checked=true;}
  toggleTiles();
  $('tc-picker').classList.toggle('hidden',$('suite-kind').value!=='test_cases');
  $('workers-field').classList.toggle('hidden',!['regression','test_cases'].includes($('suite-kind').value));
  $('t-field').classList.toggle('hidden',!['llama_shapes','llama_fast'].includes($('suite-kind').value));
  const selected=selectedDesigns().filter(id=>custom||fast||state.designs.find(d=>d.id===id)?.builtin);
  renderDesigns(selected);requestPlan();
}
async function saveSuite() {
  try {const saved=await api('/api/suites',suitePayload());state.suiteId=saved.id;state.suites=state.suites.filter(s=>s.id!==saved.id).concat(saved);toast('Suite saved.');}catch(e){toast(e.message);}
}
async function runSuite() {
  $('run').disabled=true;
  try {
    const job=await api('/api/runs',suitePayload());state.selected=job.id;state.runs.unshift(job);
    changeView('runs');renderRuns();toast('Suite queued. It will wait for any active verification run.');
  }catch(e){toast(e.message);}finally{requestPlan();}
}
function elapsed(job) {
  if(!job.started)return'Not started';
  const seconds=Math.max(0,Math.round(((job.ended?new Date(job.ended):new Date())-new Date(job.started))/1000));
  return `${Math.floor(seconds/3600)}h ${Math.floor(seconds%3600/60)}m`;
}
function resultRow(r, fast) {
  const full=r.full_k!=null;
  const dimensions=r.k?`1×${r.k} · ${r.k}×${r.m}`:'Original suite';
  const sample=full?`<br><span class="hint">Full: 1×${r.full_k} · ${r.full_k}×${r.full_m}<br>${format(r.tiles_needed)} required tiles · sample ${esc(r.sample_status)}</span>`:'';
  const total=fast?`<td>${r.estimated_cycles!=null?`≈ ${format(r.estimated_cycles)}<br><span class="hint">${format(r.cycles)} × ${format(r.tiles_needed)}</span>`:full&&r.tiles_needed===1?format(r.cycles):'—'}</td>`:'';
  return `<tr><td>${esc(r.name)}<br><span class="hint">${esc(r.design)}</span></td><td>${dimensions}${sample}</td><td>${r.wb?`16 / ${r.wb}`:'16 / 8,4,2'}</td><td>${badge(r.status)}</td><td>${r.matches!=null&&r.status!=='UNVERIFIED'?`${format(r.matches)}/${format(r.elements)}`:r.counts?esc(JSON.stringify(r.counts)):'—'}</td><td>${format(r.cycles)}</td>${total}</tr>${r.mismatches?.length?`<tr><td colspan="${fast?7:6}"><div class="mismatch">${esc(r.mismatches.map(m=>`Output ${m.index}: HW=${m.hw}, reference=${m.reference}`).join('\n'))}</div></td></tr>`:''}`;
}
function renderRuns() {
  const active=state.runs.filter(r=>!terminal.has(r.status)).length;
  $('run-count').textContent=active||state.runs.length;
  $('run-list').innerHTML=state.runs.length?state.runs.map(r=>`<article class="run-item ${state.selected===r.id?'selected':''}" data-id="${esc(r.id)}"><div class="item-top">${badge(r.status)}<small class="hint">${esc(date(r.created))}</small></div><h3>${esc(r.name)}</h3><p>${format(r.completed)} / ${format(r.total)} tests · ${esc(elapsed(r))}</p></article>`).join(''):'<div class="card empty"><h2>No runs yet</h2><p class="muted">Create a suite to get started.</p></div>';
  $('run-list').querySelectorAll('.run-item').forEach(el=>el.addEventListener('click',()=>{state.selected=el.dataset.id;state.logPath=null;renderRuns();loadDetail().catch(e=>toast(e.message));}));
}
async function loadDetail() {
  if(!state.selected)return;
  const run=await api('/api/runs/'+state.selected);
  if(run.id!==state.selected)return;
  const previous=$('run-detail').querySelector('.log-output');
  const scroll=previous?previous.scrollTop:0;
  const atBottom=previous?previous.scrollHeight-previous.clientHeight-previous.scrollTop<35:true;
  const rows=run.results||[];
  const fast=run.suite?.kind==='llama_fast';
  const counts={};rows.forEach(r=>{if(r.counts)Object.entries(r.counts).forEach(([k,v])=>counts[k]=(counts[k]||0)+v);else counts[r.status]=(counts[r.status]||0)+1;});
  const percent=run.total?Math.min(100,100*run.completed/run.total):0;
  $('run-detail').classList.remove('empty');
  $('run-detail').innerHTML=`<div class="card-heading"><div><h2 class="run-title">${esc(run.name)}</h2><p class="hint">${esc(run.id)} · ${esc(elapsed(run))}</p></div>${badge(run.status)}</div><div class="progress"><div style="width:${percent}%"></div></div><p class="hint">${format(run.completed)} / ${format(run.total)} logical tests · ${format(run.operations_done)} / ${format(run.operations_total)} HW operations</p><div class="run-phase"><strong>${esc(run.phase)}</strong>${run.current?`<br>${esc(run.current)}`:''}${run.tiles?`<br>Operation ${run.tile} / ${run.tiles} for this test`:''}${run.process?`<br>Simulator / build CPU time: ${format(run.process.cpu_seconds)} seconds · process ${esc(run.process.state)}`:''}</div><div class="result-stats"><div><strong>${counts.PASS||0}</strong><small>PASS</small></div><div><strong>${(counts.FAIL||0)+(counts.ERROR||0)+(counts.BUILD_FAIL||0)}</strong><small>FAIL / ERROR</small></div><div><strong>${counts.TIMEOUT||0}</strong><small>TIMEOUT</small></div><div><strong>${counts.UNVERIFIED||0}</strong><small>UNVERIFIED</small></div>${fast?`<div><strong>${counts.ESTIMATED||0}</strong><small>ESTIMATED</small></div>`:''}</div>${fast?'<p class="notice">≈ totals are measured tile cycles × required tile count. Edge tiles use full-tile cost; only sampled outputs are verified. A PASS with one required tile verifies the full shape.</p>':''}${rows.length?`<div class="table-wrap"><table><thead><tr><th>Test / design</th><th>X · W</th><th>Bits</th><th>Result</th><th>${fast?'Sample matches':'Exact matches'}</th><th>${fast?'Measured tile cycles':'HW cycles'}</th>${fast?'<th>Full HW cycles</th>':''}</tr></thead><tbody>${rows.map(r=>resultRow(r,fast)).join('')}</tbody></table></div>`:'<p class="hint">Results appear when an operation completes. A live phase is not a correctness result.</p>'}<div class="actions">${!terminal.has(run.status)?'<button class="button secondary danger" id="cancel-run">Stop this run</button>':''}<button class="button secondary" id="reuse-run">Edit a copy</button></div><div class="log-panel"><div class="log-header"><h2>Execution log</h2><a class="text-button" id="download-log" target="_blank" rel="noopener">Open full log ↗</a></div><select id="run-log-select" aria-label="Choose log"></select><pre class="log-output" id="run-log">Loading…</pre></div>`;
  if($('cancel-run'))$('cancel-run').onclick=async()=>{try{await api('/api/runs/'+run.id+'/stop',{});toast('Stop requested.');await pollRuns();}catch(e){toast(e.message);}};
  $('reuse-run').onclick=()=>loadSuite({...run.suite,id:null,name:run.name+' copy'});
  let paths=[run.log];
  for(const linked of run.linked_logs||[]){
    if(linked.endsWith('.log'))paths.push(linked);
    else{
      if(!state.history.length)state.history=await api('/api/history');
      const entry=state.history.find(h=>h.path===linked);if(entry)paths.push(...entry.files);
    }
  }
  paths=[...new Set(paths)];
  if(!paths.includes(state.logPath))state.logPath=paths[0];
  $('run-log-select').innerHTML=paths.map(p=>`<option value="${esc(p)}" ${p===state.logPath?'selected':''}>${esc(p)}</option>`).join('');
  $('run-log-select').onchange=()=>{state.logPath=$('run-log-select').value;showRunLog();};
  await showRunLog();
  if($('run-log'))$('run-log').scrollTop=atBottom?$('run-log').scrollHeight:scroll;
}
async function showRunLog() {
  if(!state.logPath)return;
  const target=$('run-log');if(!target)return;
  $('download-log').href='/api/download?path='+encodeURIComponent(state.logPath);
  try {const result=await api('/api/log?path='+encodeURIComponent(state.logPath));target.textContent=(result.truncated?'[Showing the latest 128 KiB; open the full log above.]\n\n':'')+result.text;}catch(e){target.textContent='Log appears when the worker starts.';}
}
function renderExternal(items) {
  $('external').innerHTML=items.map(r=>`<div class="external-banner"><span><span class="dot"></span> <strong>${esc(r.name)}</strong> is running · ${r.completed} completed${r.counts?` · ${esc(Object.entries(r.counts).map(([k,v])=>`${v} ${k}`).join(', '))}`:''}. New suites wait for its configuration lock.</span>${r.log?`<button class="text-button external-log" data-path="${esc(r.log)}">View log ↗</button>`:''}</div>`).join('');
  $('external').querySelectorAll('.external-log').forEach(el=>el.onclick=()=>{changeView('history');if(el.dataset.path.endsWith('.log'))openHistoryLog(el.dataset.path);});
}
async function pollRuns() {
  const result=await api('/api/runs');state.runs=result.runs;renderExternal(result.external);renderRuns();
  $('connection').textContent='Connected · updated '+new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  if(state.view==='runs'&&state.selected)await loadDetail();
}
async function loadHistory() {
  state.history=await api('/api/history');
  $('history-list').innerHTML=state.history.map((h,i)=>`<article class="card history-card"><div class="card-heading"><h2>${esc(h.name)}</h2>${h.active?badge('RUNNING'):badge(h.counts.TIMEOUT?'TIMEOUT':h.completed?'SAVED':'INCOMPLETE')}</div><p class="hint">${esc(h.path)}</p><div class="counts">${Object.entries(h.counts).map(([k,v])=>`<span class="${k==='PASS'?'good':'bad'}"><b>${format(v)}</b> ${esc(k)}</span>`).join('')||'<span>No completed logical tests</span>'}</div>${h.tiles?`<p class="hint">Native HW tiles: ${esc(Object.entries(h.tiles).map(([k,v])=>`${v} ${k}`).join(', '))}. Tile passes do not mean the full logical matrix passed.</p>`:''}<details><summary>Browse ${h.files.length} log files</summary><div class="file-list">${h.files.map(p=>`<button class="file-button" data-path="${esc(p)}">${esc(p.slice(h.path.length+1)||p.split('/').pop())}</button>`).join('')}</div></details></article>`).join('')||'<div class="card empty"><h2>No saved logs yet</h2></div>';
  $('history-list').querySelectorAll('.file-button').forEach(el=>el.onclick=()=>openHistoryLog(el.dataset.path));
}
async function openHistoryLog(path) {
  try{
    const log=await api('/api/log?path='+encodeURIComponent(path));
    $('history-log').classList.remove('hidden');
    $('history-log').innerHTML=`<div class="log-header"><h2>${esc(path.split('/').pop())}</h2><a class="text-button" href="/api/download?path=${encodeURIComponent(path)}" target="_blank" rel="noopener">Open full log ↗</a></div><p class="hint">${esc(path)} · ${format(log.bytes)} bytes</p><pre class="log-output"></pre>`;
    $('history-log').querySelector('pre').textContent=(log.truncated?'[Showing the latest 128 KiB]\n\n':'')+log.text;
    $('history-log').scrollIntoView({behavior:'smooth',block:'start'});
  }catch(e){toast(e.message);}
}
function loadSuite(suite) {
  state.suiteId=suite.id||null;$('suite-name').value=suite.name;$('suite-kind').value=suite.kind||'custom';
  state.tests=suite.tests?.length?JSON.parse(JSON.stringify(suite.tests)):[{name:'16×8',k:16,m:8,weights:[8,4,2],pattern:'random',repeats:1}];
  const o=suite.options||{};
  $('mode').value=o.mode||'auto';$('reference').value=o.reference||'python';
  for(const [id,key,def] of [['cycles','cycles',true],['guards','guards',true],['matrices','matrices',false],['integrity','integrity',false],['stop-on-fail','stop_on_fail',false]])$(id).checked=o[key]??def;
  $('timeout-hours').value=(o.timeout||21600)/3600;$('build-hours').value=(o.build_timeout||7200)/3600;
  $('build-jobs').value=o.jobs||8;$('workers').value=o.workers||6;$('tile-k').value=o.tile_k||'';$('tile-m').value=o.tile_m||'';$('context-t').value=o.t||16;
  document.querySelectorAll('#tc-picker input').forEach(el=>el.checked=(o.tcs||[]).includes(el.value));
  renderTests();renderDesigns(suite.design_ids||['b8_rf8']);kindChanged();toggleTiles();changeView('builder');
  $('suites-dialog').close();
}
function toggleTiles() {$('tile-options').classList.toggle('hidden',$('mode').value!=='tile');requestPlan();}
function wire() {
  document.querySelectorAll('[data-view]').forEach(el=>el.onclick=()=>changeView(el.dataset.view));
  document.querySelectorAll('[data-go]').forEach(el=>el.onclick=()=>changeView(el.dataset.go));
  document.querySelectorAll('[data-close]').forEach(el=>el.onclick=()=>$(el.dataset.close).close());
  $('add-test').onclick=()=>addTest();
  $('shape-preset').onchange=()=>{if($('shape-preset').value){const [k,m]=$('shape-preset').value.split(',').map(Number);addTest(k,m);$('shape-preset').value='';}};
  $('suite-kind').onchange=kindChanged;$('mode').onchange=toggleTiles;
  document.querySelectorAll('#builder input,#builder select').forEach(el=>el.addEventListener('input',requestPlan));
  $('save').onclick=saveSuite;$('run').onclick=runSuite;
  $('new-design').onclick=()=>{const d=state.designs.find(d=>selectedDesigns().includes(d.id))||state.designs[0];$('design-b').value=d.b;$('design-rf').value=d.rf;$('design-k').value=d.max_k;$('design-m').value=d.max_m;$('design-name').value='';$('design-dialog').showModal();};
  $('design-form').onsubmit=async event=>{event.preventDefault();try{const d=await api('/api/designs',{name:$('design-name').value,b:Number($('design-b').value),rf:Number($('design-rf').value),max_k:Number($('design-k').value),max_m:Number($('design-m').value)});state.designs.push(d);renderDesigns([d.id]);$('design-dialog').close();requestPlan();toast('Design saved. It will be built when you run a suite.');}catch(e){toast(e.message);}};
  $('load-suite').onclick=()=>{$('saved-suites').innerHTML=state.suites.length?state.suites.map(s=>`<div class="saved-suite"><div><strong>${esc(s.name)}</strong><p class="hint">${esc(s.kind)} · ${s.design_ids.length} designs</p></div><button class="button secondary open-suite" data-id="${esc(s.id)}">Open</button></div>`).join(''):'<p class="muted">Save a suite to find it here next time.</p>';$('saved-suites').querySelectorAll('.open-suite').forEach(el=>el.onclick=()=>loadSuite(state.suites.find(s=>s.id===el.dataset.id)));$('suites-dialog').showModal();};
  $('refresh-history').onclick=()=>loadHistory().catch(e=>toast(e.message));
}
async function initialize() {
  try{
    const data=await api('/api/initial');state.designs=data.designs;state.suites=data.suites;
    $('tc-picker').innerHTML=Array.from({length:11},(_,i)=>`<label><input type="checkbox" value="TC${String(i+1).padStart(2,'0')}">TC${String(i+1).padStart(2,'0')}</label>`).join('')+'<p class="hint">Leave unchecked to run all TC01–TC11.</p>';
    state.tests=[{name:'Small random',k:16,m:8,weights:[8,4,2],pattern:'random',repeats:1,seed:''}];
    wire();renderTests();renderDesigns(['b8_rf8']);kindChanged();await updatePlan();await pollRuns();
    setInterval(()=>pollRuns().catch(()=>{$('connection').textContent='Disconnected · reconnecting…';}),2500);
  }catch(error){document.querySelector('main').innerHTML=`<div class="access-error"><h1>Open your private dashboard URL</h1><p>${esc(error.message)}</p><p>From the server, print the URL with:</p><code>python3 verification/gui.py status</code><p>The URL contains your access key. No account or password setup is needed.</p></div>`;}
}
initialize();
