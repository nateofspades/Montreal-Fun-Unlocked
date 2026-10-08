const assert = require('node:assert/strict');
const {
  applyFilters,
  eventTimeLabel,
  montrealDateKey,
  datePresetRange,
  renderCard,
} = require('../docs/app.js');

const base = {
  id: '1', title: 'Jazz Night', category: 'music', status: 'scheduled',
  startsAt: '2026-10-08T01:30:00.000Z', startTimeKnown: true,
  venue: { name: 'Casa', slug: 'casa' }
};

assert.equal(eventTimeLabel({...base, startTimeKnown: false}), 'Time not listed');
assert.match(eventTimeLabel(base), /^9:30 p\.m\.$/i);
assert.equal(montrealDateKey(base.startsAt), '2026-10-07');

const sourcedCard = renderCard({...base, sources: ['Venue calendar', 'Seller & Co.'], url: 'https://example.test/event'});
assert.match(sourcedCard, /Source: Venue calendar · Seller &amp; Co\./);
assert.match(sourcedCard, /Original listing/);

const events = [
  base,
  {...base, id: '2', title: 'Comedy Hour', category: 'comedy', status: 'sold-out'},
  {...base, id: '3', title: 'Past', startsAt: '2026-10-01T01:00:00.000Z'},
  {...base, id: '4', title: 'Delayed', status: 'postponed'},
  {...base, id: '5', title: 'Unknown time today', startsAt: '2026-10-07T16:00:00.000Z', startTimeKnown: false},
];
let result = applyFilters(events, {query: 'comedy', categories: [], venues: [], includePast: false, includeUnavailable: false}, new Date('2026-10-07T20:00:00Z'));
assert.deepEqual(result.map(x => x.id), ['2']);
result = applyFilters(events, {query: '', categories: ['music'], venues: ['casa'], includePast: false, includeUnavailable: false}, new Date('2026-10-07T20:00:00Z'));
assert.deepEqual(result.map(x => x.id), ['5', '1']);

const weekend = datePresetRange('weekend', new Date('2026-10-07T16:00:00Z'));
assert.deepEqual(weekend, {from: '2026-10-09', to: '2026-10-11'});
const sundayWeekend = datePresetRange('weekend', new Date('2026-10-11T16:00:00Z'));
assert.deepEqual(sundayWeekend, {from: '2026-10-09', to: '2026-10-11'});
console.log('website filter tests passed');
