// Ads Change Log API. Shared storage in Airtable (base "EC Ads Change Log").
// Env: AIRTABLE_TOKEN (data.records:read + write on the base). Optional CHANGELOG_WRITE_KEY (header X-Team-Key).
//
// GET  /api/changelog                 -> { entries, campaigns, dataThrough }   team entries with live hold/conversion status
// GET  /api/changelog?view=history    -> { entries }   imported Google Ads history (read-only, no rules applied)
// GET  /api/changelog?view=holds      -> { activeHold, campaigns, holds }   read-only feed for the Weekly Ads Scorecard
// GET  /api/changelog?view=audit      -> guardrail checks + Google Ads vs WhatConverts comparison
// POST /api/changelog                 -> create entry      PUT /api/changelog?id=rec... -> update entry
const zlib = require('zlib');

const BASE = process.env.AIRTABLE_BASE_ID || 'appjTPakoB2CAqC0Y';
const T = { entries: 'tbljX72Jp91PEoepl', history: 'tblQOHyuIQeyZmSoB', daily: 'tblkRTtUeRR2imVzR', snap: 'tblLldkliPVBqjpHK' };
const F = { // Entries field IDs (stable even if someone renames a column in Airtable)
  logId: 'fldbLFApYwhRH0eWV', ts: 'fldY5wXTPV7IsjKyT', by: 'fldhK3Du9GswjSc0W', platform: 'fldfEgYtW9n07tWW7',
  campaigns: 'fld710xbrAkun6rHT', category: 'fldtifQTU3SjynLiJ', prev: 'fldUYB9IbkzcQqoyg', next: 'fldrLfXGdHBAJ9Y9i',
  why: 'fldSF7zSqDYTpNdUY', oldAmount: 'fldX9JiCiUErPpdtQ', newAmount: 'fldm7ZnDZWDMyXaSA', deadline: 'fldcBgjTJrmZ97Ol8',
  metric: 'fldylEmYLTUowODfI', result: 'fldkd4jsoUiw7nF2p', status: 'fldWnZKadMQyzKn0X', override: 'fldHzZdLFn7214lXG',
  overrideOf: 'fldHF0P268lFZ7Gfk', updatedAt: 'fldUECtopDjH3ZkAv', campaignIds: 'fldQfyUPcrA27uBPR', seq: 'fldWa5W7vTHi9OIWq',
};
const D = { date: 'fldf3Pmto1yIRUPHq', id: 'fldIh4ZyQstxd4ZmR', name: 'fldZFF7Rm4n4rGdCz', gconv: 'fldZiFS2VVu7r2uTR', cost: 'fldf3k8fuiZqi0lXb', wcl: 'fldQ9HBls4N19H9UL', wcq: 'fldCshW9J6FvZipIb' };
const S = { date: 'fldrYXIHeLKAcjwgS', id: 'fldk0ZYkqQzSKTjzr', name: 'fldDT0DWye6ibp1hf', status: 'fld6laZlNg48o3JVA', channel: 'fldMA1enAiS0pcvBq', bidding: 'fld6bysbFG3ZOtTv9', tcpa: 'fldX1awxJZyB3wweW', troas: 'fldZH28Nx60bVQjIN' };

const TZ = 'America/Denver';
const CATEGORIES = ['Bid', 'Target', 'Conversion', 'Budget', 'Keywords', 'Structure'];
const HOLD_CATS = ['Bid', 'Target', 'Conversion'];
const PLATFORMS = ['Google Ads', 'Microsoft Ads', 'Meta', 'Reddit', 'ChatGPT Ads'];
const STATUSES = ['Planned', 'Live', 'In hold', 'Reviewed – keep', 'Reviewed – revert'];
const HOLD_DAYS = 21, HOLD_CONV = 100, BUDGET_DAYS = 7, BUDGET_PCT = 20;
const ACCOUNT = 'Account level';

const mtDate = d => new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
const addDays = (ymd, n) => { const d = new Date(ymd + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const dayDiff = (a, b) => Math.round((new Date(b + 'T12:00:00Z') - new Date(a + 'T12:00:00Z')) / 864e5);
const norm = s => String(s || '').trim().toLowerCase();
const clip = (v, n = 4000) => String(v == null ? '' : v).slice(0, n).trim();
const money = v => parseFloat(String(v).replace(/[^0-9.\-]/g, ''));
const r2 = n => Math.round(n * 100) / 100;

async function at(path, opts = {}) {
  if (!process.env.AIRTABLE_TOKEN) throw Object.assign(new Error('Storage not configured (AIRTABLE_TOKEN missing)'), { code: 503 });
  const r = await fetch(`https://api.airtable.com/v0/${BASE}/${path}`, { ...opts, headers: { Authorization: `Bearer ${process.env.AIRTABLE_TOKEN}`, 'Content-Type': 'application/json' } });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(`Airtable ${r.status}: ${JSON.stringify(j.error || j)}`), { code: 502 });
  return j;
}
async function listAll(table) {
  const out = []; let offset;
  do {
    const j = await at(`${table}?pageSize=100&returnFieldsByFieldId=true${offset ? `&offset=${offset}` : ''}`);
    out.push(...j.records); offset = j.offset;
  } while (offset);
  return out;
}

// ── entries ──
function fromRecord(r) {
  const f = r.fields || {};
  const campaigns = String(f[F.campaigns] || '').split(' ; ').map(x => x.trim()).filter(Boolean);
  return {
    id: r.id, logId: f[F.logId] || '', seq: f[F.seq] || 0, ts: f[F.ts] || r.createdTime, by: f[F.by] || '', platform: f[F.platform] || '',
    campaigns, campaignIds: String(f[F.campaignIds] || '').split(',').map(x => x.trim()).filter(Boolean),
    category: f[F.category] || '', prev: f[F.prev] || '', next: f[F.next] || '', why: f[F.why] || '', metric: f[F.metric] || '',
    oldAmount: f[F.oldAmount] || '', newAmount: f[F.newAmount] || '', deadline: f[F.deadline] || '', result: f[F.result] || '',
    status: f[F.status] || '', override: !!f[F.override], overrideOf: f[F.overrideOf] || '', updatedAt: f[F.updatedAt] || '', imported: false,
  };
}
function toFields(e) {
  const o = {};
  const put = (k, v) => { o[F[k]] = v === '' || v == null ? null : v; };
  ['logId', 'ts', 'by', 'platform', 'category', 'prev', 'next', 'why', 'metric', 'oldAmount', 'newAmount', 'deadline', 'result', 'status', 'overrideOf', 'updatedAt'].forEach(k => { if (k in e) put(k, e[k]); });
  if ('campaigns' in e) put('campaigns', e.campaigns.join(' ; '));
  if ('campaignIds' in e) put('campaignIds', e.campaignIds.join(','));
  if ('override' in e) o[F.override] = !!e.override;
  return o;
}
const isAccount = e => !e.campaigns.length || e.campaigns.some(c => norm(c) === norm(ACCOUNT));
function overlaps(a, b) {
  if (isAccount(a) || isAccount(b)) return true;
  if (a.campaignIds.length && b.campaignIds.length && a.campaignIds.some(x => b.campaignIds.includes(x))) return true;
  return a.campaigns.some(x => b.campaigns.map(norm).includes(norm(x)));
}

// ── daily conversions ──
function loadDailyRows(recs) {
  return recs.map(r => ({ date: r.fields[D.date], id: r.fields[D.id] || '', name: r.fields[D.name] || '', gconv: r.fields[D.gconv] || 0, cost: r.fields[D.cost] || 0, wcl: r.fields[D.wcl] || 0, wcq: r.fields[D.wcq] || 0 }))
    .filter(x => x.date);
}
function convSince(e, daily) {
  const since = addDays(mtDate(new Date(e.ts)), 1); // the day after the change was logged
  const acct = isAccount(e);
  let g = 0, wl = 0, wq = 0, through = '';
  for (const r of daily) {
    if (r.date < since) continue;
    if (r.date > through) through = r.date;
    const match = acct ? true : e.campaignIds.includes(r.id);
    if (!match) continue;
    if (r.id !== 'unattributed') g += r.gconv;
    wl += r.wcl; wq += r.wcq;
  }
  return { since, through, google: r2(g), wcLeads: wl, wcQuotable: wq, basis: acct ? 'all campaigns (WhatConverts incl. unattributed)' : 'affected campaigns' };
}
function decorate(e, daily, today) {
  const hold = HOLD_CATS.includes(e.category);
  const done = String(e.status || '').startsWith('Reviewed');
  e.conv = convSince(e, daily);
  e.hold = hold;
  e.daysLeft = e.deadline ? Math.max(0, dayDiff(today, e.deadline)) : 0;
  e.convLeft = hold ? Math.max(0, r2(HOLD_CONV - e.conv.google)) : 0;
  e.holdActive = hold && !done && (today < e.deadline || e.conv.google < HOLD_CONV);
  e.evalReady = !done && !String(e.result || '').trim() && today >= e.deadline && (!hold || e.conv.google >= HOLD_CONV);
  e.pending = !done && !String(e.result || '').trim() && !e.evalReady; // still inside its evaluation window
  return e;
}

// ── imported history (read-only, old categories mapped, no rules applied) ──
let historyCache = null;
function mapOldCategory(cat, text) {
  if (cat === 'Bid strategy/targets') return /target (cpa|roas)|tcpa|troas/i.test(text) ? 'Target' : 'Bid';
  if (cat === 'Conversion tracking') return 'Conversion';
  if (cat === 'Budget') return 'Budget';
  if (cat === 'Keywords' || cat === 'Negative keywords') return 'Keywords';
  return 'Structure';
}
async function loadHistory() {
  if (historyCache) return historyCache;
  const parts = (await listAll(T.history)).map(r => r.fields).filter(f => f.fldAB3gCswVAyH7eb).sort((a, b) => a.fldTGoeknFAOX7MnZ - b.fldTGoeknFAOX7MnZ);
  if (!parts.length) return [];
  const { keys, rows } = JSON.parse(zlib.brotliDecompressSync(Buffer.from(parts.map(p => p.fldAB3gCswVAyH7eb).join(''), 'base64')).toString());
  historyCache = rows.map(r => {
    const e = Object.fromEntries(keys.map((k, i) => [k, r[i]]));
    return { id: e.id, ts: new Date(e.ts).toISOString(), by: e.by, platform: 'Google Ads', campaigns: e.campaign ? [e.campaign] : [], adGroup: e.adGroup, category: mapOldCategory(e.category, e.change), change: e.change, imported: true, status: 'Imported' };
  });
  return historyCache;
}

async function readBody(req) {
  if (req.body && typeof req.body === 'object') return req.body;
  if (typeof req.body === 'string') return JSON.parse(req.body || '{}');
  const chunks = []; for await (const c of req) chunks.push(c);
  return JSON.parse(Buffer.concat(chunks).toString() || '{}');
}
function validate(e) {
  const errs = [];
  if (!e.by) errs.push('Author / Owner is required');
  if (!PLATFORMS.includes(e.platform)) errs.push('Platform is required');
  if (!e.campaigns.length) errs.push('Affected Campaigns is required');
  if (!CATEGORIES.includes(e.category)) errs.push('Change Category is required');
  if (!e.prev) errs.push('Previous Value is required');
  if (!e.next) errs.push('New Value is required');
  if (!e.why) errs.push('Justification / Objective is required');
  if (!e.metric) errs.push('Defined Success Metric is required');
  if (e.category === 'Budget' && (!(money(e.oldAmount) > 0) || isNaN(money(e.newAmount)))) errs.push('Old and new budget amounts are required');
  if (!STATUSES.includes(e.status)) errs.push('Status is required');
  return errs;
}
function guardrails(e, others, today) {
  const reasons = [];
  for (const x of others) {
    if (x.id === e.id || !overlaps(e, x)) continue;
    if (x.holdActive) reasons.push(`${x.logId || 'entry'} hold (${x.category}) until ${x.deadline}${x.convLeft ? ` + ${x.convLeft} conv` : ''}`);
    else if (x.pending) reasons.push(`${x.logId || 'entry'} not evaluated yet (one change at a time)`);
  }
  if (e.category === 'Budget') {
    const last = others.filter(x => x.id !== e.id && x.category === 'Budget' && overlaps(e, x)).sort((a, b) => b.ts.localeCompare(a.ts))[0];
    if (last && (Date.now() - new Date(last.ts)) / 864e5 < BUDGET_DAYS) reasons.push(`Budget changed ${last.logId} less than ${BUDGET_DAYS} days ago`);
    const o = money(e.oldAmount), n = money(e.newAmount);
    if (o > 0 && Math.abs((n - o) / o * 100) > BUDGET_PCT) reasons.push(`Budget change ${((n - o) / o * 100).toFixed(0)}% (limit ±${BUDGET_PCT}%)`);
  }
  return [...new Set(reasons)];
}

// ── audit ──
function audit(entries, daily, snaps, today) {
  const dates = [...new Set(snaps.map(s => s.date))].sort();
  const latest = dates[dates.length - 1] || '';
  const cur = snaps.filter(s => s.date === latest);
  const pmaxHoarding = cur.filter(s => s.channel === 'PERFORMANCE_MAX' && /hoarding/i.test(s.name)).map(s => ({ ...s, violation: s.status === 'ENABLED' }));
  const byId = {};
  for (const s of snaps) (byId[s.id] = byId[s.id] || []).push(s);
  const unlogged = [];
  for (const list of Object.values(byId)) {
    list.sort((a, b) => a.date.localeCompare(b.date));
    for (let i = 1; i < list.length; i++) {
      const a = list[i - 1], b = list[i], diffs = [];
      if (a.status !== b.status) diffs.push(`status ${a.status} → ${b.status}`);
      if (a.bidding !== b.bidding) diffs.push(`bidding ${a.bidding} → ${b.bidding}`);
      if ((a.tcpa || 0) !== (b.tcpa || 0)) diffs.push(`tCPA $${a.tcpa || 0} → $${b.tcpa || 0}`);
      if ((a.troas || 0) !== (b.troas || 0)) diffs.push(`tROAS ${a.troas || 0} → ${b.troas || 0}`);
      if (!diffs.length) continue;
      const logged = entries.some(e => { const d = mtDate(new Date(e.ts)); return d >= addDays(a.date, -1) && d <= b.date && overlaps(e, { campaigns: [b.name], campaignIds: [b.id] }); });
      if (!logged) unlogged.push({ campaign: b.name, id: b.id, between: `${a.date} → ${b.date}`, change: diffs.join('; ') });
    }
  }
  const through = daily.reduce((m, r) => r.date > m ? r.date : m, '');
  const win = n => {
    const from = addDays(through || today, -(n - 1)), agg = {};
    for (const r of daily) {
      if (r.date < from || r.date > through) continue;
      const k = r.id; const a = agg[k] = agg[k] || { id: r.id, name: r.name, gconv: 0, cost: 0, wcl: 0, wcq: 0 };
      a.gconv += r.gconv; a.cost += r.cost; a.wcl += r.wcl; a.wcq += r.wcq; if (r.name) a.name = r.name;
    }
    const rows = Object.values(agg).map(a => ({ ...a, gconv: r2(a.gconv), cost: r2(a.cost) })).sort((a, b) => (a.id === 'unattributed') - (b.id === 'unattributed') || b.cost - a.cost);
    const tot = rows.reduce((t, a) => ({ gconv: r2(t.gconv + a.gconv), cost: r2(t.cost + a.cost), wcl: t.wcl + a.wcl, wcq: t.wcq + a.wcq }), { gconv: 0, cost: 0, wcl: 0, wcq: 0 });
    return { from, to: through, rows, total: tot };
  };
  return {
    snapshotDate: latest, dataThrough: through,
    checks: [
      { rule: 'Core Campaign Structure: no Performance Max in Hoarding', status: pmaxHoarding.some(x => x.violation) ? 'fail' : 'pass', detail: pmaxHoarding.length ? pmaxHoarding.map(x => `${x.name} (${x.status})`).join('; ') : 'No Hoarding Performance Max campaigns' },
      { rule: 'Change Log: every settings change logged first', status: unlogged.length ? 'fail' : (dates.length > 1 ? 'pass' : 'pending'), detail: dates.length > 1 ? `${unlogged.length} unlogged change(s) since ${dates[0]}` : 'Needs two daily snapshots to compare' },
      { rule: 'Core Campaign Structure: no broad match in Hoarding', status: 'not-checked', detail: 'Keyword match types need Optmyzr (not connected yet)' },
      { rule: 'Automated Rules: daily pause/enable rules off', status: 'not-checked', detail: 'Automated rules need Optmyzr (not connected yet)' },
    ],
    pmaxHoarding, unlogged, compare7: win(7), compare30: win(30),
  };
}

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type,X-Team-Key');
  res.setHeader('X-Robots-Tag', 'noindex');
  if (req.method === 'OPTIONS') return res.status(204).end();
  const q = req.query || {};
  try {
    const today = mtDate(new Date());
    if (req.method === 'GET' && q.view === 'history') {
      res.setHeader('Cache-Control', 'public, s-maxage=3600, stale-while-revalidate=86400');
      return res.status(200).json({ entries: await loadHistory() });
    }
    res.setHeader('Cache-Control', 'no-store');
    const [er, dr, sr] = await Promise.all([listAll(T.entries), listAll(T.daily), req.method === 'GET' ? listAll(T.snap) : Promise.resolve([])]);
    const daily = loadDailyRows(dr);
    const entries = er.map(fromRecord).map(e => decorate(e, daily, today)).sort((a, b) => b.ts.localeCompare(a.ts));
    const dataThrough = daily.reduce((m, r) => r.date > m ? r.date : m, '');

    if (req.method === 'GET') {
      const snaps = sr.map(r => ({ date: r.fields[S.date], id: r.fields[S.id] || '', name: r.fields[S.name] || '', status: r.fields[S.status] || '', channel: r.fields[S.channel] || '', bidding: r.fields[S.bidding] || '', tcpa: r.fields[S.tcpa] || 0, troas: r.fields[S.troas] || 0 })).filter(s => s.date);
      if (q.view === 'audit') return res.status(200).json({ asOf: today, ...audit(entries, daily, snaps, today) });
      if (q.view === 'holds') {
        const c = norm(q.campaign);
        const holds = entries.filter(e => e.holdActive && (!c || isAccount(e) || e.campaigns.some(x => norm(x) === c))).flatMap(e => (isAccount(e) ? [ACCOUNT] : e.campaigns).map(name => ({
          campaign: name, logId: e.logId, category: e.category, holdUntil: e.deadline, daysRemaining: e.daysLeft,
          conversionsSince: e.conv.google, conversionsNeeded: e.convLeft, wcLeadsSince: e.conv.wcLeads,
          note: `Hold until ${e.deadline} and ${HOLD_CONV} conversions, whichever is later`,
          change: `${e.prev} → ${e.next}`, by: e.by, enteredAt: e.ts, id: e.id,
        })));
        const byCampaign = new Map();
        for (const h of holds) { const k = norm(h.campaign); if (!byCampaign.has(k) || h.holdUntil > byCampaign.get(k).holdUntil) byCampaign.set(k, { campaign: h.campaign, holdUntil: h.holdUntil, daysRemaining: h.daysRemaining, conversionsNeeded: h.conversionsNeeded }); }
        return res.status(200).json({ asOf: today, timezone: TZ, dataThrough, activeHold: holds.length > 0, campaigns: [...byCampaign.values()], holds });
      }
      const latestSnap = snaps.reduce((m, s) => s.date > m ? s.date : m, '');
      const camps = new Map();
      for (const s of snaps.filter(s => s.date === latestSnap)) camps.set(s.id, { id: s.id, name: s.name, status: s.status, channel: s.channel });
      for (const r of daily) if (r.id !== 'unattributed' && !camps.has(r.id)) camps.set(r.id, { id: r.id, name: r.name, status: '', channel: '' });
      const order = { ENABLED: 0, PAUSED: 1 };
      const campaigns = [...camps.values()].sort((a, b) => (order[a.status] ?? 2) - (order[b.status] ?? 2) || a.name.localeCompare(b.name));
      return res.status(200).json({ asOf: today, dataThrough, entries, campaigns, rules: { HOLD_DAYS, HOLD_CONV, BUDGET_DAYS, BUDGET_PCT, CATEGORIES, HOLD_CATS } });
    }

    if (req.method !== 'POST' && req.method !== 'PUT') return res.status(405).json({ error: 'Method not allowed' });
    if (process.env.CHANGELOG_WRITE_KEY && req.headers['x-team-key'] !== process.env.CHANGELOG_WRITE_KEY) return res.status(401).json({ error: 'Team key required' });
    const b = await readBody(req);
    let prev = null;
    if (req.method === 'PUT') {
      prev = entries.find(e => e.id === q.id);
      if (!prev) return res.status(404).json({ error: 'Entry not found (imported history is read-only)' });
    }
    const now = new Date().toISOString();
    const list = v => (Array.isArray(v) ? v : String(v || '').split(' ; ')).map(x => clip(x, 300)).filter(Boolean);
    const e = {
      id: prev ? prev.id : '', ts: prev ? prev.ts : now,
      by: clip(b.by, 120), platform: clip(b.platform, 40), campaigns: list(b.campaigns).slice(0, 30), campaignIds: list(b.campaignIds).filter(x => /^\d+$/.test(x)).slice(0, 30),
      category: clip(b.category, 40), prev: clip(b.prev), next: clip(b.next), why: clip(b.why), metric: clip(b.metric, 1000),
      result: clip(b.result), status: clip(b.status, 40),
      oldAmount: b.category === 'Budget' ? clip(b.oldAmount, 40) : '', newAmount: b.category === 'Budget' ? clip(b.newAmount, 40) : '',
    };
    if (e.campaigns.some(c => norm(c) === norm(ACCOUNT))) { e.campaigns = [ACCOUNT]; e.campaignIds = []; }
    const errs = validate(e);
    if (errs.length) return res.status(400).json({ error: errs.join('; ') });
    e.deadline = prev && prev.deadline ? prev.deadline : addDays(mtDate(new Date(e.ts)), HOLD_DAYS);
    e.updatedAt = now;
    let reasons = [];
    if (prev) { e.override = prev.override; e.overrideOf = prev.overrideOf; e.logId = prev.logId; }
    else { reasons = guardrails(e, entries, today); e.override = reasons.length > 0; e.overrideOf = reasons.join(' | ').slice(0, 250); }
    let rec = prev
      ? await at(`${T.entries}/${prev.id}?returnFieldsByFieldId=true`, { method: 'PATCH', body: JSON.stringify({ fields: toFields(e), typecast: true, returnFieldsByFieldId: true }) })
      : await at(T.entries, { method: 'POST', body: JSON.stringify({ fields: toFields(e), typecast: true, returnFieldsByFieldId: true }) });
    if (!prev) { // sequential Log ID per year, ordered by the autonumber
      const year = mtDate(new Date(e.ts)).slice(0, 4), mine = rec.fields[F.seq];
      const all = (await listAll(T.entries)).map(fromRecord).filter(x => mtDate(new Date(x.ts)).slice(0, 4) === year && x.seq <= mine);
      const logId = `CL-${year}-${String(all.length).padStart(3, '0')}`;
      rec = await at(`${T.entries}/${rec.id}`, { method: 'PATCH', body: JSON.stringify({ fields: { [F.logId]: logId }, returnFieldsByFieldId: true }) });
    }
    return res.status(prev ? 200 : 201).json({ entry: decorate(fromRecord(rec), daily, today), overrideReasons: reasons });
  } catch (err) {
    console.error(err);
    return res.status(err.code || 500).json({ error: String(err.message || err) });
  }
};
