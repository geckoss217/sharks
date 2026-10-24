# Weekly Ads Scorecard merge step.
#
# Usage (current):
#   python3 ads_update.py --cur cur.js --google google.json --wc wc.json --funnel funnel.json \
#       --google2 g2.tsv --meta meta.json --ms-tsv ms_camp.tsv [--ms-sheet ms.json] --out ads.js
# Legacy positional forms still work:
#   python3 ads_update.py CUR google.json wc.json ms.json OUT
#   python3 ads_update.py CUR google.json wc.json ms.json funnel.json OUT
#
# Inputs (all raw Zapier responses unless noted):
#   --google   GoogleAdsCLIAPI create_report, main account 2998752313 (customer, segments.week, cost_micros, clicks, impressions)
#   --wc       WhatConverts weekly_ads_scorecard
#   --funnel   WhatConverts weekly_ads_funnel (buckets google = all Google paid, google2 = 2nd account campaigns, bing, facebook)
#   --google2  decoded TSV from GoogleAdsCLIAPI gaql_export_tsv, customer 3438697076:
#              SELECT segments.week, metrics.cost_micros, metrics.clicks, metrics.impressions FROM customer WHERE ...
#              (a .json raw response with a "tsv" field also works)
#   --meta     FacebookLeadAdsCLIAPI meta_weekly_insights (all ad accounts are summed)
#   --ms-tsv   decoded TSV from MicrosoftAdvertisingCLIAPI msads_report_submit (campaign) + msads_zip_to_tsv
#   --ms-sheet MicrosoftAdvertisingCLIAPI msads_weekly_account (fallback when --ms-tsv is missing). The retired
#              GoogleSheetsV2CLIAPI msads_weekly_cost sheet response is ignored (Microsoft values are kept as they were).
#
# Replaces/adds each week present in BOTH the main Google and WhatConverts results. A source that errored or
# was not supplied keeps its previous values for that week, and an error is recorded in FAILED.
import json, sys, csv, io, datetime, zoneinfo, argparse

BASE = ["week","g_spend","g_clicks","ms_spend","ms_clicks","ms_days","leads","quotable","q_google","q_bing","leads_google","leads_bing","wrong_number","low_budget","tbd_stage","jobs","revenue","cancelled"]
EXTRA = ["g_impr","ms_impr","gl","gq","gj","grev","bl","bq","bj","brev"]
EXTRA2 = ["g2_spend","g2_clicks","g2_impr","fb_spend","fb_clicks","fb_lclicks","fb_impr","g2l","g2q","g2j","g2rev","fl","fq","fj","frev"]
COLS = BASE + EXTRA + EXTRA2

def parse_args(argv):
    if argv and not argv[0].startswith("--"):
        a = argparse.Namespace(google2=None, meta=None, ms_tsv=None, funnel=None)
        if len(argv) == 5: a.cur, a.google, a.wc, a.ms_sheet, a.out = argv
        else: a.cur, a.google, a.wc, a.ms_sheet, a.funnel, a.out = argv[:6]
        return a
    p = argparse.ArgumentParser()
    for k in ("cur", "google", "wc", "out"): p.add_argument("--" + k, required=True)
    for k in ("funnel", "google2", "meta", "ms-tsv", "ms-sheet"): p.add_argument("--" + k)
    return p.parse_args(argv)

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
    try: o = json.load(open(f))
    except Exception: return None
    if o.get("isError") or dig(o, "success") is False: return None
    return o

def load_tsv(f):
    if not f: return None
    try: txt = open(f).read()
    except Exception: return None
    if txt.lstrip().startswith("{"):
        o = load(f); txt = dig(o, "tsv") if o else None
        if not txt: return None
    return list(csv.DictReader(io.StringIO(txt), delimiter="\t"))

def monday(d):
    d = datetime.date.fromisoformat(d[:10]); return (d - datetime.timedelta(days=d.weekday())).isoformat()

a = parse_args(sys.argv[1:])
s = open(a.cur).read(); A = json.loads(s[s.index("{"):s.rindex("}") + 1])
if A["COLS"] != COLS:                      # upgrade older files
    old = A["COLS"]
    A["WEEKS"] = [[dict(zip(old, w)).get(c) for c in COLS] for w in A["WEEKS"]]
    A["COLS"] = COLS
I = {c: i for i, c in enumerate(COLS)}

G, W = load(a.google), load(a.wc)
FN = load(a.funnel)
G2 = load_tsv(a.google2)
META = load(a.meta)
MST = load_tsv(a.ms_tsv)
MSS = load(a.ms_sheet) if a.ms_sheet else None
# The Tracker_2025 sheet (msads_weekly_cost) is retired as a Microsoft source (2026-10-24). Only an --ms-sheet file that
# came from the Microsoft Ads API (msads_weekly_account, "source": "microsoft_ads_api") is used; a sheet file is
# ignored so it can't overwrite API numbers, and those weeks keep their previous values.
MS_SHEET_IGNORED = MSS is not None and dig(MSS, "source") != "microsoft_ads_api"
if MS_SHEET_IGNORED:
    MSS = None
failed = []
if G is None or W is None: failed.append("google/whatconverts error — no weeks written")
if a.funnel and FN is None: failed.append("funnel (ad-attributed leads/jobs) error — funnel values left as previous value")
if a.google2 is not None and G2 is None: failed.append("Google 2nd account error — its spend left as previous value")
if a.meta is not None and META is None: failed.append("Meta Ads error — Meta spend left as previous value")
if MS_SHEET_IGNORED and MST is None:
    failed.append("Microsoft: retired Tracker sheet input ignored — Microsoft values kept; the Monday keyword refresh fills them from the API")
elif MST is None and (a.ms_sheet is None or MSS is None):
    failed.append("Microsoft Ads error — Microsoft spend left as previous value")

g = {r["segments"]["week"]: (round(int(r["metrics"].get("costMicros", 0)) / 1e6, 2), int(r["metrics"].get("clicks", 0)),
     int(r["metrics"]["impressions"]) if "impressions" in r["metrics"] else None) for r in (dig(G, "report") or [])} if G else {}
w = {r["week_start"]: r for r in (dig(W, "weeks") or [])} if W else {}
fn = {r["week_start"]: r for r in (dig(FN, "weeks") or [])} if FN else {}
g2 = {}
for r in G2 or []:
    x = g2.setdefault(r["segments.week"], [0.0, 0, 0])
    x[0] += int(r["metrics.cost_micros"] or 0) / 1e6; x[1] += int(r["metrics.clicks"] or 0); x[2] += int(r["metrics.impressions"] or 0)
fb = {}
for r in (dig(META, "weeks") or []) if META else []:
    x = fb.setdefault(r["week_start"], [0.0, 0, 0, 0])
    x[0] += float(r.get("spend") or 0); x[1] += int(r.get("clicks") or 0); x[2] += int(r.get("link_clicks") or 0); x[3] += int(r.get("impressions") or 0)
ms = {}
if MST is not None:
    days = {}
    for r in MST:
        wk = monday(r["TimePeriod"]); x = ms.setdefault(wk, [0.0, 0, 0])
        x[0] += float(r.get("Spend") or 0); x[1] += int(float(r.get("Clicks") or 0)); x[2] += int(float(r.get("Impressions") or 0))
        days.setdefault(wk, set()).add(r["TimePeriod"][:10])
    ms = {k: [round(v[0], 2), v[1], len(days[k]), v[2]] for k, v in ms.items()}
elif MSS is not None:
    ms = {r["week_start"]: [round(float(r["cost"]), 2), int(r["clicks"]), int(r["days"]),
          None if r.get("impressions") is None else int(r["impressions"])] for r in (dig(MSS, "weeks") or [])}

def bucket(f, k):
    z = {"leads": 0, "quotable": 0, "jobs": 0, "revenue": 0}
    b = f.get(k) or z
    return [int(b["leads"]), int(b["quotable"]), int(b["jobs"]), round(float(b["revenue"]), 2)]

rows = {r[0]: r for r in A["WEEKS"]}
updated = sorted(set(g) & set(w))
for wk in updated:
    x = w[wk]; old = rows.get(wk) or [None] * len(COLS)
    new = [wk, g[wk][0], g[wk][1]]
    # Microsoft: API report covers every week in the requested range (missing week = no spend)
    if MST is not None: m = ms.get(wk, [0.0, 0, 0, 0])
    elif MSS is not None and wk in ms: m = ms[wk]
    else: m = [old[I["ms_spend"]] or 0.0, old[I["ms_clicks"]] or 0, old[I["ms_days"]] or 0, old[I["ms_impr"]]]
    new += m[:3]
    new += [int(x[k]) for k in ["leads","quotable_yes","quotable_google","quotable_bing","leads_google_cpc","leads_bing_cpc","wrong_number","low_budget","tbd_stage","jobs"]]
    new += [round(float(x["revenue"]), 2), int(x["cancelled"])]
    g_impr = g[wk][2] if g[wk][2] is not None else old[I["g_impr"]]
    new += [g_impr, m[3]]
    if FN is not None:
        f = fn.get(wk, {})
        new += bucket(f, "google") + bucket(f, "bing")
        ext_leads = bucket(f, "google2") + bucket(f, "facebook")
    else:
        new += old[I["gl"]:I["brev"] + 1]
        ext_leads = old[I["g2l"]:I["g2rev"] + 1] + old[I["fl"]:I["frev"] + 1]
    if G2 is not None: v = g2.get(wk, [0.0, 0, 0]); new += [round(v[0], 2), v[1], v[2]]
    else: new += old[I["g2_spend"]:I["g2_impr"] + 1]
    if META is not None: v = fb.get(wk, [0.0, 0, 0, 0]); new += [round(v[0], 2), v[1], v[2], v[3]]
    else: new += old[I["fb_spend"]:I["fb_impr"] + 1]
    new += ext_leads
    assert len(new) == len(COLS), (len(new), len(COLS))
    rows[wk] = new
A["WEEKS"] = [rows[k] for k in sorted(rows)]
A["AS_OF"] = A["WEEKS"][-1][0]
A["GEN"] = datetime.datetime.now(zoneinfo.ZoneInfo("America/Denver")).strftime("%b %-d, %-I:%M %p MT")
A["FAILED"] = failed
open(a.out, "w").write("window.ADS=" + json.dumps(A, separators=(",", ":")) + ";\n")
print("weeks:", len(A["WEEKS"]), "as_of:", A["AS_OF"], "updated:", updated, "failed:", failed)
