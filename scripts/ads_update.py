# Weekly Ads Scorecard merge step. Usage:
#   python3 ads_update.py CURRENT_ads.js google.json wc.json ms.json funnel.json OUT_ads.js
# google.json = raw Zapier GoogleAdsCLIAPI create_report response (customer, segments.week, cost_micros, clicks, impressions)
# wc.json     = raw Zapier WhatConverts weekly_ads_scorecard response
# ms.json     = raw Zapier Google Sheets msads_weekly_cost response (impressions is null until the sheet has an Impressions column)
# funnel.json = raw Zapier WhatConverts weekly_ads_funnel response (ad-attributed leads/quotable/jobs/revenue per platform)
# Replaces/adds each week that is present in BOTH google and wc results. Never writes a week whose source errored.
import json, sys, datetime, zoneinfo
if len(sys.argv) == 6:   # old 5-arg form: no funnel file
    cur, gf, wf, mf, outf = sys.argv[1:6]; ff = None
else:
    cur, gf, wf, mf, ff, outf = sys.argv[1:7]
s = open(cur).read(); A = json.loads(s[s.index('{'):s.rindex('}')+1])
BASE = ["week","g_spend","g_clicks","ms_spend","ms_clicks","ms_days","leads","quotable","q_google","q_bing","leads_google","leads_bing","wrong_number","low_budget","tbd_stage","jobs","revenue","cancelled"]
EXTRA = ["g_impr","ms_impr","gl","gq","gj","grev","bl","bq","bj","brev"]
COLS = BASE + EXTRA
# upgrade older files that don't have the funnel columns yet
if A['COLS'] != COLS:
    old = A['COLS']
    A['WEEKS'] = [[dict(zip(old, w)).get(c) for c in COLS] for w in A['WEEKS']]
    A['COLS'] = COLS
def dig(o, key):
    if isinstance(o, dict):
        if key in o: return o[key]
        for v in o.values():
            r = dig(v, key)
            if r is not None: return r
    if isinstance(o, list):
        for v in o:
            r = dig(v, key)
            if r is not None: return r
    return None
def load(f):
    if not f: return None
    o = json.load(open(f))
    if o.get('isError') or dig(o, 'success') is False: return None
    return o
G, W, M, FN = load(gf), load(wf), load(mf), load(ff)
failed = []
if G is None or W is None: failed.append('google/whatconverts error — no weeks written')
g = {r['segments']['week']: (round(int(r['metrics'].get('costMicros', 0))/1e6, 2), int(r['metrics'].get('clicks', 0)),
     int(r['metrics']['impressions']) if 'impressions' in r['metrics'] else None) for r in (dig(G, 'report') or [])} if G else {}
w = {r['week_start']: r for r in (dig(W, 'weeks') or [])} if W else {}
m = {r['week_start']: r for r in (dig(M, 'weeks') or [])} if M else {}
fn = {r['week_start']: r for r in (dig(FN, 'weeks') or [])} if FN else {}
if M is None: failed.append('microsoft sheet error — Microsoft spend left as previous value')
if FN is None: failed.append('funnel (ad-attributed leads/jobs) error — funnel values left as previous value')
rows = {r[0]: r for r in A['WEEKS']}
I = {c: i for i, c in enumerate(COLS)}
for wk in sorted(set(g) & set(w)):
    x = w[wk]; old = rows.get(wk)
    if wk in m:
        ms = [round(float(m[wk]['cost']), 2), int(m[wk]['clicks']), int(m[wk]['days'])]
        ms_impr = None if m[wk].get('impressions') is None else int(m[wk]['impressions'])
    elif old: ms = old[3:6]; ms_impr = old[I['ms_impr']]
    else: ms = [0.0, 0, 0]; ms_impr = None
    g_impr = g[wk][2] if g[wk][2] is not None else (old[I['g_impr']] if old else None)
    if FN is not None:
        z = {'leads': 0, 'quotable': 0, 'jobs': 0, 'revenue': 0}
        f = fn.get(wk, {}); fg = f.get('google') or z; fb = f.get('bing') or z
        funnel = [int(fg['leads']), int(fg['quotable']), int(fg['jobs']), round(float(fg['revenue']), 2),
                  int(fb['leads']), int(fb['quotable']), int(fb['jobs']), round(float(fb['revenue']), 2)]
    elif old: funnel = old[I['gl']:I['brev']+1]
    else: funnel = [None]*8
    rows[wk] = [wk, g[wk][0], g[wk][1]] + ms + [int(x[k]) for k in ['leads','quotable_yes','quotable_google','quotable_bing','leads_google_cpc','leads_bing_cpc','wrong_number','low_budget','tbd_stage','jobs']] + [round(float(x['revenue']), 2), int(x['cancelled'])] + [g_impr, ms_impr] + funnel
A['WEEKS'] = [rows[k] for k in sorted(rows)]
A['AS_OF'] = A['WEEKS'][-1][0]
A['GEN'] = datetime.datetime.now(zoneinfo.ZoneInfo('America/Denver')).strftime('%b %-d, %-I:%M %p MT')
A['FAILED'] = failed
open(outf, 'w').write('window.ADS=' + json.dumps(A, separators=(',', ':')) + ';\n')
print('weeks:', len(A['WEEKS']), 'as_of:', A['AS_OF'], 'updated:', sorted(set(g) & set(w)), 'failed:', failed)
