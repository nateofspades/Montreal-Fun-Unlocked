const MONTREAL_TZ = 'America/Toronto';
const PAGE_SIZE = 48;

function partsFor(value) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: MONTREAL_TZ, year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(new Date(value));
  return Object.fromEntries(parts.map(part => [part.type, part.value]));
}

function montrealDateKey(value) {
  const p = partsFor(value);
  return `${p.year}-${p.month}-${p.day}`;
}

function eventTimeLabel(event) {
  if (!event.startTimeKnown) return 'Time not listed';
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: MONTREAL_TZ, hour: 'numeric', minute: '2-digit'
  }).format(new Date(event.startsAt));
}

function eventDateLabel(value) {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: MONTREAL_TZ, weekday: 'short', month: 'short', day: 'numeric'
  }).format(new Date(value));
}

function dateKeyFromParts(year, month, day) {
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

function addCalendarDays(key, amount) {
  const [year, month, day] = key.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + amount, 12));
  return dateKeyFromParts(date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate());
}

function weekdayForKey(key) {
  const [year, month, day] = key.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, day, 12)).getUTCDay();
}

function datePresetRange(preset, now = new Date()) {
  const today = montrealDateKey(now);
  if (preset === 'today') return {from: today, to: today};
  if (preset === 'tomorrow') {
    const tomorrow = addCalendarDays(today, 1);
    return {from: tomorrow, to: tomorrow};
  }
  if (preset === 'weekend') {
    const weekday = weekdayForKey(today);
    const daysToFriday = weekday === 0 ? -2 : weekday === 6 ? -1 : 5 - weekday;
    const friday = addCalendarDays(today, daysToFriday);
    return {from: friday, to: addCalendarDays(friday, 2)};
  }
  return {from: '', to: ''};
}

function applyFilters(events, options, now = new Date()) {
  const query = (options.query || '').trim().toLocaleLowerCase();
  const categories = new Set(options.categories || []);
  const venues = new Set(options.venues || []);
  return events.filter(event => {
    const venue = event.venue || {};
    const eventDate = montrealDateKey(event.startsAt);
    const haystack = `${event.title || ''} ${venue.name || ''} ${event.category || ''}`.toLocaleLowerCase();
    if (query && !haystack.includes(query)) return false;
    if (categories.size && !categories.has(event.category)) return false;
    if (venues.size && !venues.has(venue.slug || venue.name)) return false;
    if (options.from && eventDate < options.from) return false;
    if (options.to && eventDate > options.to) return false;
    if (!options.includePast) {
      const isPast = event.startTimeKnown
        ? new Date(event.startsAt) < now
        : eventDate < montrealDateKey(now);
      if (isPast) return false;
    }
    if (!options.includeUnavailable && ['cancelled', 'postponed'].includes(event.status)) return false;
    return true;
  }).sort((a, b) => new Date(a.startsAt) - new Date(b.startsAt) || a.title.localeCompare(b.title));
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

function safeUrl(value) {
  if (!value) return '';
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
  } catch { return ''; }
}

function titleCase(value) {
  return String(value || '').replaceAll('-', ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function matchingCountLabel(count) {
  return `${count.toLocaleString('en-CA')} matching event${count === 1 ? '' : 's'}`;
}

function renderCard(event) {
  const venue = event.venue || {};
  const status = event.status || 'unknown';
  const ticket = safeUrl(event.ticketUrl);
  const source = safeUrl(event.url);
  const sourceNames = (event.sources || []).filter(Boolean).map(escapeHtml).join(' · ');
  const age = event.ageRestriction ? `<span>${escapeHtml(event.ageRestriction)}</span>` : '';
  const statusBadge = status === 'sold-out' ? '<span class="badge sold-out">Sold out</span>' : `<span class="badge">${escapeHtml(titleCase(status))}</span>`;
  return `<article class="event-card">
    <div class="card-top"><span class="category">${escapeHtml(titleCase(event.category || 'other'))}</span>${statusBadge}</div>
    <div class="date-line"><strong>${escapeHtml(eventDateLabel(event.startsAt))}</strong><span>${escapeHtml(eventTimeLabel(event))}</span></div>
    <h2>${escapeHtml(event.title || 'Untitled event')}</h2>
    <p class="venue">${escapeHtml(venue.name || 'Venue unknown')}</p>
    <div class="details">${age}<span>${escapeHtml(event.confidence || '')}</span></div>
    ${sourceNames ? `<p class="source-line">Source: ${sourceNames}</p>` : ''}
    <div class="links">
      ${ticket ? `<a class="button primary" href="${escapeHtml(ticket)}" target="_blank" rel="noopener">Tickets</a>` : ''}
      ${source ? `<a class="button" href="${escapeHtml(source)}" target="_blank" rel="noopener">Original listing</a>` : ''}
    </div>
  </article>`;
}

function initSite() {
  const state = {events: [], filtered: [], visible: PAGE_SIZE, metadata: {}};
  const elements = {
    search: document.querySelector('#search'), category: document.querySelector('#category'),
    venue: document.querySelector('#venue'), from: document.querySelector('#from-date'),
    to: document.querySelector('#to-date'), unavailable: document.querySelector('#include-unavailable'),
    past: document.querySelector('#include-past'), cards: document.querySelector('#events'),
    count: document.querySelector('#match-count'), more: document.querySelector('#load-more'),
    updated: document.querySelector('#last-updated'), error: document.querySelector('#error'),
  };

  function options() {
    return {query: elements.search.value, categories: elements.category.value ? [elements.category.value] : [],
      venues: elements.venue.value ? [elements.venue.value] : [], from: elements.from.value,
      to: elements.to.value, includeUnavailable: elements.unavailable.checked, includePast: elements.past.checked};
  }

  function render(reset = true) {
    if (reset) state.visible = PAGE_SIZE;
    state.filtered = applyFilters(state.events, options());
    elements.cards.innerHTML = state.filtered.slice(0, state.visible).map(renderCard).join('');
    elements.count.textContent = matchingCountLabel(state.filtered.length);
    elements.more.hidden = state.visible >= state.filtered.length;
    if (!state.filtered.length) elements.cards.innerHTML = '<div class="empty">No events match these filters.</div>';
  }

  function populateSelect(select, values, label) {
    select.innerHTML = `<option value="">All ${label}</option>` + values.map(value => `<option value="${escapeHtml(value.key)}">${escapeHtml(value.label)}</option>`).join('');
  }

  fetch('./data/events.json', {cache: 'no-cache'}).then(response => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }).then(dataset => {
    state.events = dataset.showtimes || [];
    state.metadata = dataset.metadata || {};
    const categories = [...new Set(state.events.map(event => event.category).filter(Boolean))].sort().map(value => ({key: value, label: titleCase(value)}));
    const venueMap = new Map(state.events.map(event => [event.venue?.slug || event.venue?.name, event.venue?.name]).filter(([key, value]) => key && value));
    const venues = [...venueMap].map(([key, label]) => ({key, label})).sort((a, b) => a.label.localeCompare(b.label));
    populateSelect(elements.category, categories, 'categories');
    populateSelect(elements.venue, venues, 'venues');
    elements.updated.textContent = state.metadata.lastSuccessfulCollection ? `Last collected ${new Intl.DateTimeFormat('en-CA', {dateStyle:'medium', timeStyle:'short', timeZone:MONTREAL_TZ}).format(new Date(state.metadata.lastSuccessfulCollection))} Montreal time` : 'Collection time unavailable';
    render();
  }).catch(error => {
    elements.error.hidden = false;
    elements.error.textContent = `Event data could not be loaded (${error.message}).`;
  });

  document.querySelectorAll('input, select').forEach(element => element.addEventListener(element.type === 'search' ? 'input' : 'change', () => render()));
  document.querySelectorAll('[data-preset]').forEach(button => button.addEventListener('click', () => {
    const range = datePresetRange(button.dataset.preset);
    elements.from.value = range.from; elements.to.value = range.to; render();
  }));
  document.querySelector('#clear-dates').addEventListener('click', () => { elements.from.value = ''; elements.to.value = ''; render(); });
  elements.more.addEventListener('click', () => { state.visible += PAGE_SIZE; render(false); });
}

if (typeof module !== 'undefined') module.exports = {applyFilters, eventTimeLabel, montrealDateKey, datePresetRange, renderCard, matchingCountLabel};
if (typeof document !== 'undefined') initSite();
