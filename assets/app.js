
const params = new URLSearchParams(location.search);
const cards = [...document.querySelectorAll('.card')];
const chips = [...document.querySelectorAll('.chip[data-filter]')];
const district = document.querySelector('#district');
const counter = document.querySelector('[data-count]');
const empty = document.querySelector('.empty');

// активные фильтры: из URL (?free=1) или из заранее нажатых чипов на лендинге
const active = new Set();
chips.forEach(chip => {
  const key = chip.dataset.filter;
  if (params.get(key) === '1' || chip.getAttribute('aria-pressed') === 'true') {
    active.add(key);
    chip.setAttribute('aria-pressed', 'true');
  }
});
if (district && params.get('district')) district.value = params.get('district');

function plural(n){
  const m = n % 100, d = n % 10;
  if (m > 10 && m < 20) return 'событий';
  if (d === 1) return 'событие';
  if (d >= 2 && d <= 4) return 'события';
  return 'событий';
}

function syncUrl(){
  ['free','child','dog','nature','outside'].forEach(k => {
    if (active.has(k)) params.set(k, '1'); else params.delete(k);
  });
  const d = district ? district.value : '';
  if (d) params.set('district', d); else params.delete('district');
  const q = params.toString();
  history.replaceState(null, '', q ? '?' + q : location.pathname);
}

function apply(){
  const d = district ? district.value : '';
  let shown = 0;
  for (const card of cards){
    const okFlags = [...active].every(k => card.dataset[k] === 'true');
    const okDistrict = !d || card.dataset.district === d;
    const visible = okFlags && okDistrict;
    card.hidden = !visible;
    card.style.display = visible ? '' : 'none';
    if (visible) shown++;
  }
  if (counter) counter.textContent = shown + ' ' + plural(shown);
  if (empty) empty.style.display = shown ? 'none' : 'block';
  syncUrl();
}

chips.forEach(chip => chip.addEventListener('click', () => {
  const key = chip.dataset.filter;
  if (active.has(key)){
    active.delete(key);
    chip.setAttribute('aria-pressed', 'false');
  } else {
    active.add(key);
    chip.setAttribute('aria-pressed', 'true');
  }
  apply();
}));

if (district) {
  district.addEventListener('change', apply);
}

document.querySelectorAll('[data-reset]').forEach(btn => btn.addEventListener('click', () => {
  active.clear();
  chips.forEach(chip => chip.setAttribute('aria-pressed', 'false'));
  if (district) district.value = '';
  apply();
}));

apply();
