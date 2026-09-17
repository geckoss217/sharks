# Weekly Ads Scorecard merge step. Usage:
#   python3 update.py CURRENT_ads.js google.json wc.json ms.json OUT_ads.js
# google.json = raw Zapier GoogleAdsCLIAPI create_report response (customer, segments.week, cost_micros, clicks)
# wc.json     = raw Zapier WhatConverts weekly_ads_scorecard response
# ms.json     = raw Zapier Google Sheets msads_weekly_cost response
# Replaces/adds each week that is present in BOTH google and wc results. Never writes a week whose source errored.
import json, sys, datetime, zoneinfo
cur, gf, wf, mf, outf = sys.argv[1:6]
s = open(cur).read(); A = json.loads(s[s.index('{'):s.rindex('}')+1])
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
    o = json.load(open(f))
    if o.get('isError') or dig(o, 'success') is False: return None
    return o
G, W, M = load(gf), load(wf), load(mf)
failed = []
if G is None or W is None: failed.append('google/whatconverts error — no weeks written')
g = {r['segments']['week']: (round(int(r['metrics'].get('costMicros', 0))/1e6, 2), int(r['metrics'].get('clicks', 0))) for r in (dig(G, 'report') or [])} if G else {}
w = {r['week_start']: r for r in (dig(W, 'weeks') or [])} if W else {}
m = {r['week_start']: r for r in (dig(M, 'weeks') or [])} if M else {}
if M is None: failed.append('microsoft sheet error — Microsoft spend left as previous value')
rows = {r[0]: r for r in A['WEEKS']}
for wk in sorted(set(g) & set(w)):
    x = w[wk]; old = rows.get(wk)
    if wk in m: ms = [round(float(m[wk]['cost']), 2), int(m[wk]['clicks']), int(m[wk]['days'])]
    elif old: ms = old[3:6]
    else: ms = [0.0, 0, 0]
    rows[wk] = [wk, g[wk][0], g[wk][1]] + ms + [int(x[k]) for k in ['leads','quotable_yes','quotable_google','quotable_bing','leads_google_cpc','leads_bing_cpc','wrong_number','low_budget','tbd_stage','jobs']] + [round(float(x['revenue']), 2), int(x['cancelled'])]
A['WEEKS'] = [rows[k] for k in sorted(rows)]
A['AS_OF'] = A['WEEKS'][-1][0]
A['GEN'] = datetime.datetime.now(zoneinfo.ZoneInfo('America/Denver')).strftime('%b %-d, %-I:%M %p MT')
A['FAILED'] = failed
open(outf, 'w').write('window.ADS=' + json.dumps(A, separators=(',', ':')) + ';\n')
print('weeks:', len(A['WEEKS']), 'as_of:', A['AS_OF'], 'updated:', sorted(set(g) & set(w)), 'failed:', failed)
