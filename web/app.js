
let lastResult = null;
let localBlock = null;
let updateInProgress = false;

function esc(s){return String(s??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]));}

let currentState = null;
let activePage = 'android';
let dropboxSelection = null;
let polling = false;
let sessionId;
try { sessionId = sessionStorage.getItem('esdeSession');
  if(!sessionId){sessionId=crypto.randomUUID();sessionStorage.setItem('esdeSession',sessionId);}
}catch(e){ sessionId=String(Date.now())+String(Math.random()); }

async function api(path, payload){
  const options=payload===undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)};
  const response=await fetch(path,options); const data=await response.json();
  if(!response.ok) throw new Error(data.reason||`HTTP ${response.status}`);
  return data;
}

function renderPage(page){
  activePage=page;
  document.getElementById('androidPage').classList.toggle('hidden',page!=='android');
  document.getElementById('dropboxPage').classList.toggle('hidden',page!=='dropbox');
  document.getElementById('navAndroid').classList.toggle('active',page==='android');
  document.getElementById('navDropbox').classList.toggle('active',page==='dropbox');
}
async function navigate(page){
  renderPage(page);
  try{await api('/api/app/page',{page});}catch(e){document.getElementById('connectionStatus').textContent=String(e);}
}

function renderAndroid(d,busy){
    const pill=document.getElementById('devicePill');
    pill.className='pill '+(d.device.connected?'online':'offline');
    document.getElementById('deviceName').textContent=d.device.connected?'연결됨':'연결 안 됨';
    document.getElementById('deviceTitle').textContent=d.device.name;
    document.getElementById('serial').textContent=d.device.serial||'—';
    const systems=(d.profile.systems||'—').split(/\s+/).filter(Boolean);
    document.getElementById('systems').innerHTML=systems.map(x=>`<span>${esc(x.toUpperCase())}</span>`).join('');
    const editBtn=document.getElementById('editSystemsBtn');
    editBtn.classList.toggle('hidden', !d.device.connected || !d.profile.exists);
    editBtn.dataset.current=(d.profile.systems||'');
    editBtn.dataset.available=JSON.stringify(d.available_systems||[]);
    const progress=d.status||{percent:0,label:'대기 중'};
    document.getElementById('statusText').textContent=progress.label;
    document.getElementById('percent').textContent=progress.percent+'%';
    document.getElementById('barFill').style.width=progress.percent+'%';
    const btn=document.getElementById('startBtn');
    document.getElementById('registerBtn').disabled=busy;
    document.getElementById('saveSystemsBtn').disabled=busy;
    for(const id of ['registerSystems','editSystemsPicker'])document.getElementById(id).querySelectorAll('input').forEach(input=>input.disabled=busy);
    editBtn.disabled=busy;
    const registerBox=document.getElementById('registerBox');
    const needsRegistration=d.device.connected && !d.profile.exists;

    registerBox.classList.toggle('hidden', !needsRegistration);
    if(needsRegistration){
      document.getElementById('registerTitle').textContent=`${d.device.name} · 새 기기`;

      const picker=document.getElementById('registerSystems');
      const currentChecked=new Set(
        [...picker.querySelectorAll('input:checked')].map(x=>x.value)
      );

      const systems=d.available_systems||[];
      if(picker.dataset.signature!==JSON.stringify(systems)){
      picker.dataset.signature=JSON.stringify(systems);
      picker.innerHTML=systems.map(s=>{
        const checked=currentChecked.has(s) || (currentChecked.size===0 && (s==='gb'||s==='gbc'));
        return `<label class="system-option">
          <input type="checkbox" value="${esc(s)}" ${checked?'checked':''}>
          <span>${esc(s.toUpperCase())}</span>
        </label>`;
      }).join('');
      }
    }

    btn.disabled=busy||!d.device.connected||needsRegistration;
    btn.textContent=d.running?'동기화 중…':(needsRegistration?'기기 등록 필요':'동기화 시작');

    if(localBlock){
      document.getElementById('resultBox').classList.remove('hidden');
      document.getElementById('resultTitle').textContent='동기화 차단';
      document.getElementById('resultText').textContent=localBlock;
      document.getElementById('resultText').classList.remove('hidden');
    } else if(d.result && (d.result.kind==='OK' || d.result.kind==='ERROR')){
      lastResult=d.result;
      document.getElementById('resultBox').classList.remove('hidden');
      const ok=d.result.kind==='OK';
      document.getElementById('resultTitle').textContent=ok?'동기화 완료':'동기화 오류';
      document.getElementById('resultText').textContent=d.result.text||'';
    } else {
      lastResult=null;
      document.getElementById('resultBox').classList.add('hidden');
      document.getElementById('resultTitle').textContent='';
      document.getElementById('resultText').textContent='';
    }

}

function renderDropbox(d,busy){
  if(dropboxSelection===null) dropboxSelection=new Set(d.selected_systems||[]);
  const systems=d.systems||[];
  document.getElementById('dropboxTarget').textContent=d.target||'';
  const list=document.getElementById('dropboxSystems');
  const signature=JSON.stringify(systems.map(s=>[s.name,s.has_rom]));
  if(list.dataset.signature!==signature){
    list.dataset.signature=signature;
    list.innerHTML=systems.map(s=>`<label class="dropbox-row"><input type="checkbox" value="${esc(s.name)}" ${dropboxSelection.has(s.name)?'checked':''}><span>${esc(s.name)}</span><small>${s.has_rom?'ROM 있음':'ROM 없음'}</small></label>`).join('');
    list.querySelectorAll('input').forEach(input=>input.onchange=()=>{
      if(input.checked)dropboxSelection.add(input.value);else dropboxSelection.delete(input.value);
      updateDropboxCount(systems);
    });
  }
  list.querySelectorAll('input').forEach(input=>input.disabled=busy&&!currentState?.test_mode);
  updateDropboxCount(systems);
  document.getElementById('dropboxStartBtn').disabled=busy;
  for(const id of ['dbAllBtn','dbNoneBtn','dbRomBtn'])document.getElementById(id).disabled=busy&&!currentState?.test_mode;
  const job=d.job;
  document.getElementById('dropboxProgress').textContent=job?`${job.label} · ${job.progress.percent}%`:'대기 중';
  if(job && job.state!=='running'){
    document.getElementById('dropboxResultBox').classList.remove('hidden');
    document.getElementById('dropboxResultTitle').textContent=job.summary||job.state;
    document.getElementById('dropboxResultText').textContent=job.detail||job.error||'';
  }
}
function updateDropboxCount(systems){
  document.getElementById('dropboxCounts').textContent=`전체 ${systems.length}개 · ROM 감지 ${systems.filter(s=>s.has_rom).length}개 · 선택 ${systems.filter(s=>dropboxSelection.has(s.name)).length}개`;
}
function selectDropbox(mode){
  if(currentState?.active_job)return;
  const systems=currentState?.dropbox.systems||[];
  dropboxSelection=new Set(systems.filter(s=>mode==='all'||(mode==='rom'&&s.has_rom)).map(s=>s.name));
  document.getElementById('dropboxSystems').querySelectorAll('input').forEach(input=>input.checked=dropboxSelection.has(input.value));
  updateDropboxCount(systems);
}
function toggleDropboxDetails(){document.getElementById('dropboxResultText').classList.toggle('hidden');}
async function startDropbox(){
  document.getElementById('dropboxStartBtn').disabled=true;
  try{await api('/api/dropbox/start',{systems:[...(dropboxSelection||[])]});await state();}
  catch(e){document.getElementById('dropboxResultBox').classList.remove('hidden');
    document.getElementById('dropboxResultTitle').textContent='실행 실패';
    document.getElementById('dropboxResultText').textContent=String(e);
    document.getElementById('dropboxResultText').classList.remove('hidden');await state();}
}

function renderCommon(d){
  const busy=!!d.active_job||!!d.test_mode; const upd=d.update||{};
  document.getElementById('versionBadge').textContent='v'+d.version+(d.test_mode?' · READ ONLY TEST':'');
  document.getElementById('commonUpdateStatus').textContent=upd.active?'앱 업데이트 중':upd.checking?'업데이트 확인 중':!upd.ok?'업데이트 확인 실패':upd.available?`v${upd.latest} 업데이트 가능`:'최신 버전';
  document.getElementById('updateBtn').disabled=busy||!upd.available;
  document.getElementById('checkUpdateBtn').disabled=upd.active||updateInProgress;
  document.getElementById('activeJobStatus').textContent=d.active_job?`현재 실행 작업: ${d.active_job.kind} · ${d.active_job.label} · ${d.active_job.progress.percent}%`:'현재 실행 작업: 없음';
  for(const [kind,id] of [['android','recentAndroid'],['dropbox','recentDropbox']]){
    const job=d[kind]?.recent_job||(d[kind]?.job?.state!=='running'?d[kind]?.job:null);
    document.getElementById(id).textContent=`최근 ${kind} 결과: `+(job?`${job.state} · ${job.summary||job.label}`:'없음');
  }
}
async function state(){
  if(polling||updateInProgress)return;
  polling=true;
  try{
    const d=await api('/api/state');currentState=d;
    renderPage(d.active_page||activePage);
    renderCommon(d);renderAndroid(d.android,!!d.active_job||!!d.test_mode);renderDropbox(d.dropbox,!!d.active_job||!!d.test_mode);
    document.getElementById('connectionStatus').textContent='';
  }catch(e){document.getElementById('connectionStatus').textContent='backend 연결 확인 중: '+String(e);}
  finally{polling=false;}
}
async function heartbeat(){
  try{await api('/api/app/session',{session:sessionId});}catch(e){document.getElementById('connectionStatus').textContent=String(e);}
}
async function checkUpdate(){
  document.getElementById('checkUpdateBtn').disabled=true;
  try{await api('/api/update/check',{});await state();}
  catch(e){document.getElementById('commonUpdateStatus').textContent=String(e);}
  finally{document.getElementById('checkUpdateBtn').disabled=false;}
}

async function startSync(){
  localBlock=null;
  document.getElementById('resultBox').classList.add('hidden');
  const r=await fetch('/api/android/start',{method:'POST'});
  const d=await r.json();
  if(!d.ok){
    localBlock=d.reason||'동기화를 시작할 수 없습니다.';
    document.getElementById('resultBox').classList.remove('hidden');
    document.getElementById('resultTitle').textContent='동기화 차단';
    document.getElementById('resultText').textContent=localBlock;
    document.getElementById('resultText').classList.remove('hidden');
    return;
  }
  localBlock=null;
  state();
}

function toggleDetails(){
  document.getElementById('resultText').classList.toggle('hidden');
}

setInterval(state,1000);
setInterval(heartbeat,3000);
heartbeat();state();
if(typeof window!=='undefined'){
  window.addEventListener('pagehide',()=>{
    const payload=new Blob([JSON.stringify({session:sessionId})],{type:'application/json'});
    navigator.sendBeacon('/api/app/session/close',payload);
  });
  window.addEventListener('pageshow',heartbeat);
}


async function copyResult(){
  const text=document.getElementById('resultText').textContent||'';
  if(!text) return;
  let ok=false;
  try{
    await navigator.clipboard.writeText(text);
    ok=true;
  }catch(e){
    try{
      const ta=document.createElement('textarea');
      ta.value=text;
      ta.style.position='fixed';
      ta.style.opacity='0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      ok=document.execCommand('copy');
      ta.remove();
    }catch(_){}
  }
  const btn=document.getElementById('copyBtn');
  if(ok){
    const old=btn.textContent;
    btn.textContent='복사됨';
    btn.classList.add('copied');
    setTimeout(()=>{btn.textContent=old;btn.classList.remove('copied');},1400);
  }
}


async function registerDevice(){
  const btn=document.getElementById('registerBtn');
  const old=btn.textContent;
  btn.disabled=true;
  btn.textContent='등록 중…';

  try{
    const systems=[...document.querySelectorAll('#registerSystems input:checked')].map(x=>x.value);
    if(!systems.length){
      localBlock='동기화할 시스템을 하나 이상 선택해 주세요.';
      document.getElementById('resultBox').classList.remove('hidden');
      document.getElementById('resultTitle').textContent='기기 등록 실패';
      document.getElementById('resultText').textContent=localBlock;
      document.getElementById('resultText').classList.remove('hidden');
      return;
    }

    const r=await fetch('/api/android/register',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({systems})
    });
    const d=await r.json();

    if(!d.ok){
      localBlock=d.reason||'기기 등록에 실패했습니다.';
      document.getElementById('resultBox').classList.remove('hidden');
      document.getElementById('resultTitle').textContent='기기 등록 실패';
      document.getElementById('resultText').textContent=localBlock;
      document.getElementById('resultText').classList.remove('hidden');
    }else{
      localBlock=null;
      document.getElementById('resultBox').classList.remove('hidden');
      document.getElementById('resultTitle').textContent='기기 등록 완료';
      document.getElementById('resultText').textContent=d.reason;
      document.getElementById('resultText').classList.remove('hidden');
      await state();
    }
  }catch(e){
    localBlock='기기 등록 요청 중 오류가 발생했습니다.';
  }finally{
    btn.disabled=false;
    btn.textContent=old;
  }
}


function openSystemEditor(){
  const btn=document.getElementById('editSystemsBtn');
  const current=new Set((btn.dataset.current||'').split(/\s+/).filter(Boolean));
  let available=[];
  try{ available=JSON.parse(btn.dataset.available||'[]'); }catch(e){}

  const picker=document.getElementById('editSystemsPicker');
  picker.innerHTML=available.map(s=>`
    <label class="system-option">
      <input type="checkbox" value="${esc(s)}" ${current.has(s)?'checked':''}>
      <span>${esc(s.toUpperCase())}</span>
    </label>
  `).join('');

  document.getElementById('systemEditor').classList.remove('hidden');
}

function closeSystemEditor(){
  document.getElementById('systemEditor').classList.add('hidden');
}

async function saveSystems(){
  const systems=[...document.querySelectorAll('#editSystemsPicker input:checked')].map(x=>x.value);
  if(!systems.length){
    localBlock='동기화할 시스템을 하나 이상 선택해 주세요.';
    document.getElementById('resultBox').classList.remove('hidden');
    document.getElementById('resultTitle').textContent='저장 실패';
    document.getElementById('resultText').textContent=localBlock;
    document.getElementById('resultText').classList.remove('hidden');
    return;
  }

  const r=await fetch('/api/android/systems',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({systems})
  });
  const d=await r.json();

  document.getElementById('resultBox').classList.remove('hidden');
  document.getElementById('resultTitle').textContent=d.ok?'시스템 변경 완료':'저장 실패';
  document.getElementById('resultText').textContent=d.reason||'';
  document.getElementById('resultText').classList.remove('hidden');

  if(d.ok){
    closeSystemEditor();
    await state();
  }
}


async function installUpdate(){
  const btn=document.getElementById('updateBtn');
  updateInProgress=true;
  if(currentState){
    const pending={...currentState,active_job:{kind:'update',label:'준비 중',progress:{percent:0}},update:{...currentState.update,active:true}};
    renderCommon(pending);renderAndroid(currentState.android,true);renderDropbox(currentState.dropbox,true);
  }
  btn.disabled=true;
  document.getElementById('startBtn').disabled=true;
  document.getElementById('dropboxStartBtn').disabled=true;
  btn.textContent='업데이트 중…';

  try{
    const r=await fetch('/api/update/install',{method:'POST'});
    const d=await r.json();

    document.getElementById('resultBox').classList.remove('hidden');
    document.getElementById('resultTitle').textContent=d.ok?'업데이트 시작':'업데이트 실패';
    document.getElementById('resultText').textContent=d.reason||'';
    document.getElementById('resultText').classList.remove('hidden');

    if(d.ok){
      // The launcher opens the new port; never reload the obsolete URL.
      watchUpdate();
      return;
    }
  }catch(e){
    document.getElementById('resultBox').classList.remove('hidden');
    document.getElementById('resultTitle').textContent='업데이트 실패';
    document.getElementById('resultText').textContent=String(e);
    document.getElementById('resultText').classList.remove('hidden');
  }

  btn.disabled=false;
  btn.textContent='업데이트';
  updateInProgress=false;
  state();
}

async function watchUpdate(){
  try{
    const r=await fetch('/api/update/status',{cache:'no-store'});
    const d=await r.json();
    if(d.ok===false){
      document.getElementById('resultTitle').textContent='업데이트 실패';
      document.getElementById('resultText').textContent=d.reason||'업데이트에 실패했습니다.';
      updateInProgress=false;
      document.getElementById('updateBtn').disabled=false;
      document.getElementById('updateBtn').textContent='업데이트';
      state();
      return;
    }
  }catch(e){
    // The installer stopped this backend. Keep the page readable while the
    // launcher opens the new window instead of navigating to a dead port.
    document.getElementById('resultTitle').textContent='업데이트 재시작 중';
    document.getElementById('resultText').textContent='업데이트 후 새 창이 자동으로 열립니다. 새 창에서 계속 사용해 주세요. 새 창이 열리지 않으면 앱 메뉴에서 ES-DE Sync를 실행해 주세요.';
    return;
  }
  setTimeout(watchUpdate,1000);
}
