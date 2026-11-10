
const params = new URLSearchParams(location.search);
const cards = [...document.querySelectorAll('.card')];
const gridEl = document.querySelector('.grid');
const chips = [...document.querySelectorAll('.chip[data-filter]')];
const dayChips = [...document.querySelectorAll('.chip[data-day]')];
// Два списка мест: районы города (#district) и населённые пункты области
// (#geo, он же фильтр «За городом»). Значения опций — просто названия;
// префиксы «d:» и «g:» остались в старых ссылках, их снимаем.
const districtSel = document.querySelector('#district');
const areaSel = document.querySelector('#geo');
function plain(value){
  const raw = value || '';
  const cut = raw.indexOf(':');
  return cut > 0 ? raw.slice(cut + 1) : raw;
}
const pastNote = document.querySelector('[data-past]');
const pastToggle = document.querySelector('[data-past-toggle]');
const counter = document.querySelector('[data-count]');
const empty = document.querySelector('.empty');
const controlsEl = document.querySelector('.controls');
const toggleBtn = document.querySelector('.filters-toggle');
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
  if (params.get(key) === '1' || chip.getAttribute('aria-current') === 'page') {
    active.add(key);
    chip.setAttribute('aria-current', 'page');
  }
});

// день выходных: пусто — оба дня, иначе 'sat' или 'sun'
let day = ['sat', 'sun'].includes(params.get('day')) ? params.get('day') : '';
function syncDayChips(){
  dayChips.forEach(c => c.setAttribute('aria-pressed', String(c.dataset.day === day)));
}
syncDayChips();

function restorePlace(sel, param, prefix){
  if (!sel) return;
  const wanted = plain(params.get(param) || '');
  if (!wanted) return;
  const values = [...sel.options].map(o => o.value);
  const match = [wanted, prefix + ':' + wanted].find(v => values.includes(v));
  if (match) sel.value = match;
}
restorePlace(districtSel, 'district', 'd');
restorePlace(areaSel, 'geo', 'g');

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
  const district = districtSel ? plain(districtSel.value) : '';
  if (district) params.set('district', district); else params.delete('district');
  const area = areaSel ? plain(areaSel.value) : '';
  if (area) params.set('geo', area); else params.delete('geo');
  const q = params.toString();
  const next = q ? '?' + q : location.pathname;
  // apply() вызывается ещё и по таймеру: без сравнения адрес переписывался бы
  // каждую минуту вхолостую.
  if (next !== location.pathname + location.search) history.replaceState(null, '', next);
}

// Состояние шторки: если ни один фильтр не включён, второй строки нет вовсе —
// на телефоне остаются только дни, счётчик и кнопка. Если включён, под ней
// видны только включённые фильтры и «Сбросить», иначе непонятно, почему
// выдача короче полной.
function syncFilters(){
  if (!controlsEl) return;
  const place = Boolean(
    (districtSel && plain(districtSel.value)) || (areaSel && plain(areaSel.value))
  );
  controlsEl.dataset.active = String(active.size > 0 || place);
  [districtSel, areaSel].forEach(sel => {
    if (sel) sel.classList.toggle('is-set', Boolean(plain(sel.value)));
  });
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

function apply(animate){
  const district = districtSel ? plain(districtSel.value) : '';
  const area = areaSel ? plain(areaSel.value) : '';
  let shown = 0, past = 0;
  const visibleCards = [];
  for (const card of cards){
    const okFlags = [...active].every(k => card.dataset[k] === 'true');
    const okZone = (!district || (card.dataset.district || '') === district)
                && (!area || (card.dataset.geo || '') === area);
    const okDay = !day || (card.dataset.days || '').split(' ').includes(day);
    const matches = okFlags && okZone && okDay;
    const gone = matches && isPast(card);
    if (gone) past++;
    card.classList.toggle('card--past', gone);
    const visible = matches && (!gone || showPast);
    card.hidden = !visible;
    card.style.display = visible ? '' : 'none';
    if (visible) { shown++; visibleCards.push(card); }
  }

  // Участники теста принимали неизменный порядок за сломанные фильтры:
  // первая пара карточек подходила под любой фильтр и оставалась на месте.
  // Пересобираем сетку по времени начала и подсвечиваем, что выдача изменилась.
  if (gridEl){
    visibleCards.sort((a, b) =>
      (a.dataset.start || a.dataset.end || '').localeCompare(b.dataset.start || b.dataset.end || '')
    );
    for (const card of visibleCards) gridEl.appendChild(card);
    for (const card of cards) if (card.hidden) gridEl.appendChild(card);
    if (animate){
      gridEl.classList.remove('grid--flash');
      void gridEl.offsetWidth;
      gridEl.classList.add('grid--flash');
    }
  }

  if (counter) {
    counter.textContent = shown === cards.length
      ? shown + ' ' + plural(shown)
      : shown + ' из ' + cards.length;
  }
  if (empty) empty.style.display = shown || past ? 'none' : 'block';
  updatePast(past);
  syncFilters();
  syncUrl();
}

chips.forEach(chip => chip.addEventListener('click', (e) => {
  // Чипы — ссылки на страницы подборок: без JS они ведут на /besplatno/ и т.д.,
  // с JS фильтруют на месте, не перезагружая страницу.
  e.preventDefault();
  const key = chip.dataset.filter;
  if (active.has(key)){
    active.delete(key);
    chip.removeAttribute('aria-current');
  } else {
    active.add(key);
    chip.setAttribute('aria-current', 'page');
  }
  apply(true);
  track('filter', key);
}));

dayChips.forEach(chip => chip.addEventListener('click', () => {
  day = day === chip.dataset.day ? '' : chip.dataset.day;
  syncDayChips();
  apply(true);
  track('day', day || 'both');
}));

[[districtSel, 'district'], [areaSel, 'place']].forEach(([sel, key]) => {
  if (!sel) return;
  sel.addEventListener('change', () => {
    apply(true);
    track(key, plain(sel.value) || 'all');
  });
});

if (controlsEl && toggleBtn){
  toggleBtn.addEventListener('click', () => {
    const open = controlsEl.dataset.open === 'true';
    controlsEl.dataset.open = String(!open);
    toggleBtn.setAttribute('aria-expanded', String(!open));
    toggleBtn.setAttribute('aria-label', !open ? 'Свернуть фильтры' : 'Показать фильтры');
    track('filters', !open ? 'open' : 'close');
  });
}

if (pastToggle) {
  pastToggle.addEventListener('click', () => {
    showPast = !showPast;
    apply(true);
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
  chips.forEach(chip => chip.removeAttribute('aria-current'));
  syncDayChips();
  [districtSel, areaSel].forEach(sel => { if (sel) sel.value = ''; });
  apply(true);
}));

apply();
// Время идёт: страница, открытая в браузере весь вечер, должна сама убрать
// событие, которое только что закончилось.
setInterval(apply, 60000);
