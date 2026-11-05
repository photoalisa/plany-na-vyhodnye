
const params = new URLSearchParams(location.search);
const cards = [...document.querySelectorAll('.card')];
const chips = [...document.querySelectorAll('.chip[data-filter]')];
const dayChips = [...document.querySelectorAll('.chip[data-day]')];
const district = document.querySelector('#district');
const counter = document.querySelector('[data-count]');
const empty = document.querySelector('.empty');

// Действия для счётчика: GoatCounter не ставит cookie и не хранит персональные
// данные, поэтому согласия посетителя не спрашиваем. Если скрипт не загрузился
// (блокировщик), track() просто ничего не делает.
function track(action, label){
  if (!window.goatcounter || !window.goatcounter.count) return;
  window.goatcounter.count({
    path: label ? action + '/' + label : action,
    title: action,
    event: true,
  });
}

// активные фильтры: из URL (?free=1) или из заранее нажатых чипов на лендинге
const active = new Set();
chips.forEach(chip => {
  const key = chip.dataset.filter;
  if (params.get(key) === '1' || chip.getAttribute('aria-pressed') === 'true') {
    active.add(key);
    chip.setAttribute('aria-pressed', 'true');
  }
});

// день выходных: пусто — оба дня, иначе 'sat' или 'sun'
let day = ['sat', 'sun'].includes(params.get('day')) ? params.get('day') : '';
function syncDayChips(){
  dayChips.forEach(c => c.setAttribute('aria-pressed', String(c.dataset.day === day)));
}
syncDayChips();

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
  if (day) params.set('day', day); else params.delete('day');
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
    const okDay = !day || (card.dataset.days || '').split(' ').includes(day);
    const visible = okFlags && okDistrict && okDay;
    card.hidden = !visible;
    card.style.display = visible ? '' : 'none';
    if (visible) shown++;
  }
  if (counter) counter.textContent = shown + ' ' + plural(shown);
  if (empty) empty.style.display = shown ? 'none' : 'block';
  syncUrl();
}

chips.forEach(chip => chip.addEventListener('click', (e) => {
  // Чипы — ссылки на страницы подборок: без JS они ведут на /besplatno/ и т.д.,
  // с JS фильтруют на месте, не перезагружая страницу.
  e.preventDefault();
  const key = chip.dataset.filter;
  if (active.has(key)){
    active.delete(key);
    chip.setAttribute('aria-pressed', 'false');
  } else {
    active.add(key);
    chip.setAttribute('aria-pressed', 'true');
  }
  apply();
  track('filter', key);
}));

dayChips.forEach(chip => chip.addEventListener('click', () => {
  day = day === chip.dataset.day ? '' : chip.dataset.day;
  syncDayChips();
  apply();
  track('day', day || 'both');
}));

if (district) {
  district.addEventListener('change', () => {
// Клик по афише и по «Билеты и подробности» — главный признак того, что
// событие заинтересовало и человек ушёл к организатору.
document.querySelectorAll('.card__link').forEach(link => {
  link.addEventListener('click', () => track('click-card'));
});
document.querySelectorAll('.card__media').forEach(link => {
  link.addEventListener('click', () => track('click-poster'));
});
document.querySelectorAll('.card__report').forEach(link => {
  link.addEventListener('click', () => track('report'));
});

apply();
    track('district', district.value || 'all');
  });
}

document.querySelectorAll('[data-reset]').forEach(btn => btn.addEventListener('click', () => {
  active.clear();
  day = '';
  chips.forEach(chip => chip.setAttribute('aria-pressed', 'false'));
  syncDayChips();
  if (district) district.value = '';
  apply();
}));

apply();
