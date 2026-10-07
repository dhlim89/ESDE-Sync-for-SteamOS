
let lastResult = null;
let localBlock = null;

function esc(s){return String(s??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]));}

async function state(){
  try{
    const r=await fetch('/api/state',{cache:'no-store'});
    const d=await r.json();
    const pill=document.getElementById('devicePill');
    pill.className='pill '+(d.device.connected?'online':'offline');
    document.getElementById('deviceName').textContent=d.device.connected?'연결됨':'연결 안 됨';
    document.getElementById('deviceTitle').textContent=d.device.name;
    document.getElementById('serial').textContent=d.device.serial||'—';
    const systems=(d.profile.systems||'—').split(/\s+/).filter(Boolean);
    document.getElementById('systems').innerHTML=systems.map(x=>`<span>${esc(x.toUpperCase())}</span>`).join('');
    const updateBox=document.getElementById('updateBox');
    const upd=d.update||{};
    updateBox.classList.toggle('hidden', !upd.available);
    if(upd.available){
      document.getElementById('updateTitle').textContent=`새 버전 v${upd.latest}이 있습니다.`;
      document.getElementById('updateNotes').textContent=upd.notes||'새 버전을 설치할 수 있습니다.';
    }

    const editBtn=document.getElementById('editSystemsBtn');
    editBtn.classList.toggle('hidden', !d.device.connected || !d.profile.exists);
    editBtn.dataset.current=(d.profile.systems||'');
    editBtn.dataset.available=JSON.stringify(d.available_systems||[]);
    document.getElementById('statusText').textContent=d.status.label;
    document.getElementById('percent').textContent=d.status.percent+'%';
    document.getElementById('barFill').style.width=d.status.percent+'%';
    const btn=document.getElementById('startBtn');
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
      picker.innerHTML=systems.map(s=>{
        const checked=currentChecked.has(s) || (currentChecked.size===0 && (s==='gb'||s==='gbc'));
        return `<label class="system-option">
          <input type="checkbox" value="${esc(s)}" ${checked?'checked':''}>
          <span>${esc(s.toUpperCase())}</span>
        </label>`;
      }).join('');
    }

    btn.disabled=d.running||!d.device.connected||needsRegistration;
    btn.textContent=d.running?'동기화 중…':(needsRegistration?'기기 등록 필요':'동기화 시작');

    if(localBlock){
      document.getElementById('resultBox').classList.remove('hidden');
      document.getElementById('resultTitle').textContent='동기화 차단';
      document.getElementById('resultText').textContent=localBlock;
      document.getElementById('resultText').classList.remove('hidden');
    } else if(d.result){
      lastResult=d.result;
      document.getElementById('resultBox').classList.remove('hidden');
      const ok=d.result.kind==='OK';
      document.getElementById('resultTitle').textContent=ok?'동기화 완료':'동기화 오류';
      document.getElementById('resultText').textContent=d.result.text;
    }
  }catch(e){}
}

async function startSync(){
  localBlock=null;
  document.getElementById('resultBox').classList.add('hidden');
  const r=await fetch('/api/start',{method:'POST'});
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

setInterval(state,500);
state();


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

    const r=await fetch('/api/register',{
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

  const r=await fetch('/api/update-systems',{
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
  btn.disabled=true;
  btn.textContent='업데이트 중…';

  try{
    const r=await fetch('/api/update',{method:'POST'});
    const d=await r.json();

    document.getElementById('resultBox').classList.remove('hidden');
    document.getElementById('resultTitle').textContent=d.ok?'업데이트 시작':'업데이트 실패';
    document.getElementById('resultText').textContent=d.reason||'';
    document.getElementById('resultText').classList.remove('hidden');

    if(d.ok){
      setTimeout(()=>location.reload(), 3500);
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
}
