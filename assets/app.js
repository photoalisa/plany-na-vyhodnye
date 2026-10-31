
const params = new URLSearchParams(location.search);
const cards = [...document.querySelectorAll('.card')];
const chips = [...document.querySelectorAll('.chip[data-filter]')];
const district = document.querySelector('#district');
const counter = document.querySelector('[data-count]');
const empty = document.querySelector('.empty');

function plural(n){const m=n%100,d=n%10;if(m>10&&m<20)return 'событий';
  if(d===1)return 'событие';if(d>=2&&d<=4)return 'события';return 'событий';}
function apply(){
  const active = chips.map(c=>c.dataset.filter).filter(k=>c_truth(k));
  const d = district ? district.value : (params.get('district')||'');
  let shown=0;
  for(const card of cards){
    const okFlags = active.every(k=>card.dataset[k]==='true');
    const okDistrict = !d || card.dataset.district===d;
    const visible = okFlags && okDistrict;
    card.hidden = !visible;
    if(visible) shown++;
  }
  if(counter) counter.textContent = shown + ' ' + plural(shown);
  if(empty) empty.style.display = shown ? 'none' : 'block';
  const q = params.toString();
  history.replaceState(null,'', q ? '?'+q : location.pathname);
}
function c_truth(k){ return params.get(k)==='1' || (chips.find(c=>c.dataset.filter===k)||{}).getAttribute?.('aria-pressed')==='true' && !params.has('reset'); }
chips.forEach(c=>c.addEventListener('click',()=>{
  const k=c.dataset.filter;
  const pressed = c.getAttribute('aria-pressed')==='true';
  c.setAttribute('aria-pressed', pressed?'false':'true');
  if(pressed) params.delete(k); else params.set(k,'1');
  apply();
}));
if(district) district.addEventListener('change',()=>{
  district.value?params.set('district',district.value):params.delete('district');apply();});
document.querySelectorAll('[data-reset]').forEach(btn=>btn.addEventListener('click',()=>{
  ['free','child','dog','nature','outside','district'].forEach(k=>params.delete(k));
  chips.forEach(c=>c.setAttribute('aria-pressed','false'));
  if(district) district.value='';
  apply();
}));
apply();
