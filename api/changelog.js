// Ads Change Log API. Shared, team-writable storage in Airtable (base "EC Ads Change Log").
// Needs Vercel env var AIRTABLE_TOKEN (personal access token: data.records:read + data.records:write on that base).
// Optional env var CHANGELOG_WRITE_KEY: if set, saves need header X-Team-Key.
//
// GET  /api/changelog                 -> { entries }            team-entered rows
// GET  /api/changelog?view=history    -> { entries }            imported Google Ads history (read-only)
// GET  /api/changelog?view=holds      -> { activeHold, campaigns, holds }   read-only, for the Weekly Ads Scorecard
// POST /api/changelog                 -> create entry
// PUT  /api/changelog?id=<recordId>   -> update entry
// POST /api/changelog?action=import-history  -> one-time load of the compressed history (refused once loaded)
const zlib = require('zlib');

const BASE = process.env.AIRTABLE_BASE_ID || 'appjTPakoB2CAqC0Y';
const T_ENTRIES = 'tbljX72Jp91PEoepl';
const T_HISTORY = 'tblQOHyuIQeyZmSoB';
const TZ = 'America/Denver';
const HOLD_CATS = ['Bid strategy/targets', 'Conversion tracking', 'AI Max/automation'];
const METRIC_CATS = ['Bid strategy/targets', 'Conversion tracking', 'Budget'];
const CATEGORIES = ['Bid strategy/targets', 'Conversion tracking', 'Budget', 'Pause/enable', 'Keywords', 'Negative keywords', 'Ads/assets', 'Location', 'Audience', 'AI Max/automation', 'Rules', 'Other'];
const PLATFORMS = ['Google Ads', 'Microsoft Ads', 'Meta', 'Reddit', 'ChatGPT Ads'];
const STATUSES = ['Planned', 'Live', 'In hold', 'Reviewed – keep', 'Reviewed – revert'];
const HOLD_DAYS = 21;
const F = { // Airtable field names
  label: 'Entry ID', ts: 'Date/time', by: 'Changed by', platform: 'Platform', campaign: 'Campaign', category: 'Category',
  change: 'What changed', why: 'Why', oldAmount: 'Old budget', newAmount: 'New budget', holdUntil: 'Hold until',
  metric: 'Success metric + target', result: 'Result at review', status: 'Status', override: 'Override',
  overrideOf: 'Override of', updatedAt: 'Updated at',
};

const mtDate = d => new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
function addDays(ymd, n) { const d = new Date(ymd + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
const holdUntilFor = (cat, ts) => HOLD_CATS.includes(cat) ? addDays(mtDate(new Date(ts)), HOLD_DAYS) : '';
const isActiveHold = (e, today) => e.holdUntil && e.holdUntil >= today && !String(e.status || '').startsWith('Reviewed');
const norm = s => String(s || '').trim().toLowerCase();
const clip = (v, n = 4000) => String(v == null ? '' : v).slice(0, n).trim();

async function at(path, opts = {}) {
  if (!process.env.AIRTABLE_TOKEN) throw Object.assign(new Error('Storage not configured (AIRTABLE_TOKEN missing)'), { code: 503 });
  const r = await fetch(`https://api.airtable.com/v0/${BASE}/${path}`, {
    ...opts, headers: { Authorization: `Bearer ${process.env.AIRTABLE_TOKEN}`, 'Content-Type': 'application/json' },
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(`Airtable ${r.status}: ${JSON.stringify(j.error || j)}`), { code: 502 });
  return j;
}
async function listAll(table, params = '') {
  const out = []; let offset;
  do {
    const j = await at(`${table}?pageSize=100${params}${offset ? `&offset=${offset}` : ''}`);
    out.push(...j.records); offset = j.offset;
  } while (offset);
  return out;
}
function fromRecord(r) {
  const f = r.fields;
  return {
    id: r.id, ts: f[F.ts] || r.createdTime, by: f[F.by] || '', platform: f[F.platform] || '', campaign: f[F.campaign] || '',
    category: f[F.category] || '', change: f[F.change] || '', why: f[F.why] || '', oldAmount: f[F.oldAmount] || '',
    newAmount: f[F.newAmount] || '', holdUntil: f[F.holdUntil] || '', metric: f[F.metric] || '', result: f[F.result] || '',
    status: f[F.status] || '', override: !!f[F.override], overrideOf: f[F.overrideOf] || '', updatedAt: f[F.updatedAt] || '',
    imported: false,
  };
}
function toFields(e) {
  const o = {};
  for (const [k, name] of Object.entries(F)) if (k in e) o[name] = e[k] === '' ? null : e[k];
  return o;
}

let historyCache = null;
async function loadHistory() {
  if (historyCache) return historyCache;
  const parts = (await listAll(T_HISTORY)).map(r => r.fields).filter(f => f.Data).sort((a, b) => a.Part - b.Part);
  if (!parts.length) return [];
  const { keys, rows } = JSON.parse(zlib.brotliDecompressSync(Buffer.from(parts.map(p => p.Data).join(''), 'base64')).toString());
  historyCache = rows.map(r => {
    const e = Object.fromEntries(keys.map((k, i) => [k, r[i]]));
    return { ...e, ts: new Date(e.ts).toISOString(), platform: 'Google Ads', why: '', metric: '', result: '', status: 'Live', imported: true, holdUntil: holdUntilFor(e.category, e.ts) };
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
  if (!e.by) errs.push('Changed by is required');
  if (!PLATFORMS.includes(e.platform)) errs.push('Platform is required');
  if (!e.campaign) errs.push('Campaign is required');
  if (!CATEGORIES.includes(e.category)) errs.push('Category is required');
  if (!e.change) errs.push('What changed is required');
  if (!e.why) errs.push('Why is required');
  if (METRIC_CATS.includes(e.category) && !e.metric) errs.push('Success metric + target is required for this category');
  if (e.category === 'Budget' && (!e.oldAmount || !e.newAmount)) errs.push('Old and new budget amounts are required');
  if (!STATUSES.includes(e.status)) errs.push('Status is required');
  return errs;
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
    if (req.method === 'GET') {
      if (q.view === 'history') {
        res.setHeader('Cache-Control', 'public, s-maxage=3600, stale-while-revalidate=86400');
        return res.status(200).json({ entries: await loadHistory() });
      }
      res.setHeader('Cache-Control', 'no-store');
      const entries = (await listAll(T_ENTRIES)).map(fromRecord).sort((a, b) => b.ts.localeCompare(a.ts));
      if (q.view !== 'holds') return res.status(200).json({ asOf: today, entries });

      const all = entries.concat((await loadHistory()).filter(e => e.holdUntil));
      const c = norm(q.campaign);
      const holds = all.filter(e => isActiveHold(e, today) && (!c || norm(e.campaign) === c)).map(e => ({
        campaign: e.campaign, platform: e.platform, category: e.category, holdUntil: e.holdUntil,
        daysRemaining: Math.round((new Date(e.holdUntil) - new Date(today)) / 864e5),
        note: 'Hold until this date and 100 conversions, whichever is later',
        change: e.change, by: e.by, enteredAt: e.ts, imported: !!e.imported, id: e.id,
      })).sort((a, b) => b.holdUntil.localeCompare(a.holdUntil));
      const byCampaign = new Map(); // one row per campaign: the hold that ends last
      for (const h of holds) {
        const k = norm(h.campaign);
        if (!byCampaign.has(k)) byCampaign.set(k, { campaign: h.campaign || '(account level)', holdUntil: h.holdUntil, daysRemaining: h.daysRemaining, holdCount: 0 });
        byCampaign.get(k).holdCount++;
      }
      return res.status(200).json({ asOf: today, timezone: TZ, activeHold: holds.length > 0, campaigns: [...byCampaign.values()], holds });
    }

    res.setHeader('Cache-Control', 'no-store');
    if (req.method !== 'POST' && req.method !== 'PUT') return res.status(405).json({ error: 'Method not allowed' });
    if (process.env.CHANGELOG_WRITE_KEY && req.headers['x-team-key'] !== process.env.CHANGELOG_WRITE_KEY)
      return res.status(401).json({ error: 'Team key required' });
    const b = await readBody(req);

    if (q.action === 'import-history') { // one time only
      const existing = await listAll(T_HISTORY);
      if (existing.length) return res.status(409).json({ error: 'History already imported' });
      const data = String(b.data || ''); const rows = Number(b.rows) || 0;
      const check = JSON.parse(zlib.brotliDecompressSync(Buffer.from(data, 'base64')).toString());
      if (!Array.isArray(check.rows) || check.rows.length !== rows) return res.status(400).json({ error: 'Row count mismatch' });
      const CH = 95000, recs = [];
      for (let i = 0; i * CH < data.length; i++) recs.push({ fields: { Part: i + 1, Data: data.slice(i * CH, (i + 1) * CH), 'Row count': rows } });
      for (let i = 0; i < recs.length; i += 10) await at(T_HISTORY, { method: 'POST', body: JSON.stringify({ records: recs.slice(i, i + 10) }) });
      historyCache = null;
      return res.status(201).json({ imported: rows, parts: recs.length });
    }

    let prev = null;
    if (req.method === 'PUT') {
      if (!/^rec[A-Za-z0-9]{14}$/.test(q.id || '')) return res.status(404).json({ error: 'Entry not found (imported history is read-only)' });
      prev = fromRecord(await at(`${T_ENTRIES}/${q.id}`));
    }
    const now = new Date().toISOString();
    const e = {
      ts: prev ? prev.ts : now,
      by: clip(b.by, 120), platform: clip(b.platform, 40), campaign: clip(b.campaign, 300), category: clip(b.category, 40),
      change: clip(b.change), why: clip(b.why), metric: clip(b.metric, 1000), result: clip(b.result), status: clip(b.status, 40),
      oldAmount: b.category === 'Budget' ? clip(b.oldAmount, 40) : '', newAmount: b.category === 'Budget' ? clip(b.newAmount, 40) : '',
    };
    const errs = validate(e);
    if (errs.length) return res.status(400).json({ error: errs.join('; ') });
    e.holdUntil = prev && prev.category === e.category && prev.holdUntil ? prev.holdUntil : holdUntilFor(e.category, e.ts);
    e.updatedAt = now;
    e.label = `${mtDate(new Date(e.ts))} · ${e.campaign} · ${e.category}`.slice(0, 250);

    let conflicts = [];
    if (prev) { e.override = prev.override; e.overrideOf = prev.overrideOf; }
    else {
      const entries = (await listAll(T_ENTRIES)).map(fromRecord);
      conflicts = entries.concat(await loadHistory()).filter(x => norm(x.campaign) === norm(e.campaign) && isActiveHold(x, today));
      e.override = conflicts.length > 0;
      e.overrideOf = conflicts.slice(0, 10).map(x => x.id).join(', ');
    }
    const rec = prev
      ? await at(`${T_ENTRIES}/${prev.id}`, { method: 'PATCH', body: JSON.stringify({ fields: toFields(e), typecast: true }) })
      : await at(T_ENTRIES, { method: 'POST', body: JSON.stringify({ fields: toFields(e), typecast: true }) });
    return res.status(prev ? 200 : 201).json({ entry: fromRecord(rec), conflicts: conflicts.length });
  } catch (err) {
    console.error(err);
    return res.status(err.code || 500).json({ error: String(err.message || err) });
  }
};
