
const params = new URLSearchParams(location.search);
const cards = [...document.querySelectorAll('.card')];
const chips = [...document.querySelectorAll('.chip[data-filter]')];
const dayChips = [...document.querySelectorAll('.chip[data-day]')];
// Список площадок: на каталоге это районы города, на «За городом» —
// населённые пункты области. Скрипт один, поэтому ключ берём из data-zone.
const zone = document.querySelector('#district, #geo');
const zoneKey = zone ? (zone.dataset.zone || 'district') : '';
const zoneParam = zoneKey === 'geo' ? 'geo' : 'district';
const pastNote = document.querySelector('[data-past]');
const pastToggle = document.querySelector('[data-past-toggle]');
const counter = document.querySelector('[data-count]');
const empty = document.querySelector('.empty');
let showPast = false;

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

if (zone && params.get(zoneParam)) zone.value = params.get(zoneParam);

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
  const z = zone ? zone.value : '';
  if (z) params.set(zoneParam, z); else params.delete(zoneParam);
  const q = params.toString();
  const next = q ? '?' + q : location.pathname;
  // apply() вызывается ещё и по таймеру: без сравнения адрес переписывался бы
  // каждую минуту вхолостую.
  if (next !== location.pathname + location.search) history.replaceState(null, '', next);
}

// Прошедшее событие определяем по часам посетителя: сервер собрал страницу
// утром, а смотрят её вечером, и субботний концерт в воскресенье уже неактуален.
function isPast(card){
  const end = card.dataset.end;
  return Boolean(end) && new Date(end).getTime() < Date.now();
}

function updatePast(past){
  if (!pastNote || !pastToggle) return;
  pastNote.hidden = !past;
  const label = pastNote.querySelector('[data-past-label]');
  if (label) label.textContent = showPast ? 'Прошедшие события показаны.' : 'Прошедшие события скрыты.';
  pastToggle.textContent = (showPast ? 'Скрыть' : 'Показать') + ' (' + past + ')';
  pastToggle.setAttribute('aria-pressed', String(showPast));
}

function apply(){
  const z = zone ? zone.value : '';
  let shown = 0, past = 0;
  for (const card of cards){
    const okFlags = [...active].every(k => card.dataset[k] === 'true');
    const okZone = !z || (card.dataset[zoneKey] || '') === z;
    const okDay = !day || (card.dataset.days || '').split(' ').includes(day);
    const matches = okFlags && okZone && okDay;
    const gone = matches && isPast(card);
    if (gone) past++;
    card.classList.toggle('card--past', gone);
    const visible = matches && (!gone || showPast);
    card.hidden = !visible;
    card.style.display = visible ? '' : 'none';
    if (visible) shown++;
  }
  if (counter) counter.textContent = shown + ' ' + plural(shown);
  if (empty) empty.style.display = shown || past ? 'none' : 'block';
  updatePast(past);
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

if (zone) {
  zone.addEventListener('change', () => {
    apply();
    track(zoneKey, zone.value || 'all');
  });
}

if (pastToggle) {
  pastToggle.addEventListener('click', () => {
    showPast = !showPast;
    apply();
    track('past', showPast ? 'show' : 'hide');
  });
}

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

// Статьи: видно, ведёт ли текст людей в каталог или сразу к организатору.
const article = document.querySelector('.article');
if (article) {
  const parts = location.pathname.split('/').filter(Boolean);
  const slug = parts[parts.length - 1] || 'article';
  article.querySelectorAll('a[href]').forEach(link => {
    link.addEventListener('click', () => {
      const href = link.getAttribute('href') || '';
      track(href.startsWith('/') ? 'article-to-site' : 'article-to-source', slug);
    });
  });
}

document.querySelectorAll('[data-reset]').forEach(btn => btn.addEventListener('click', () => {
  active.clear();
  day = '';
  showPast = false;
  chips.forEach(chip => chip.setAttribute('aria-pressed', 'false'));
  syncDayChips();
  if (zone) zone.value = '';
  apply();
}));

apply();
// Время идёт: страница, открытая в браузере весь вечер, должна сама убрать
// событие, которое только что закончилось.
setInterval(apply, 60000);
