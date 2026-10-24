#!/usr/bin/env python3
"""Replace the Ads Scorecard's Microsoft columns with Microsoft Ads API numbers.

Usage: python3 ads_ms_update.py data/ads.js ms.json data/ads.js

ms.json = the result of Zapier action MicrosoftAdvertisingCLIAPI /
          code_action_microsoftadvertisingcliapi__msads_weekly_account
          ({success, start_date, end_date, weeks: [{week_start, cost, clicks, impressions, days}]}).
          The raw Zapier response (with results.data.data) also works.
          start_date / end_date are the range that was requested; add them if the action didn't echo them.

Every scorecard week whose Monday falls inside start_date..end_date gets ms_spend, ms_clicks,
ms_days and ms_impr from the API. Weeks inside the range with no Microsoft rows had no spend and
are set to 0. Weeks outside the range are left alone. Nothing is written if the pull failed.
This replaces the old Tracker_2025 "Microsoft Ads Data" sheet (msads_weekly_cost).
"""
import json, sys, datetime as dt

cur, mf, outf = sys.argv[1:4]
s = open(cur).read()
A = json.loads(s[s.index("{"):s.rindex("}") + 1])
o = json.load(open(mf))
for k in ("results", "data", "data"):
    if isinstance(o, dict) and k in o and "weeks" not in o:
        o = o[k]
if not o.get("success") or "weeks" not in o:
    sys.exit("Microsoft pull failed — ads.js left unchanged")
start, end = o.get("start_date"), o.get("end_date")
if not start or not end:
    sys.exit("ms.json needs start_date and end_date (the range that was requested)")
mon = lambda d: (dt.date.fromisoformat(d) - dt.timedelta(days=dt.date.fromisoformat(d).weekday())).isoformat()
lo, hi = mon(start), mon(end)
m = {w["week_start"]: w for w in o["weeks"]}
C = A["COLS"]; I = {c: i for i, c in enumerate(C)}
changed = 0
for row in A["WEEKS"]:
    wk = row[0]
    if not (lo <= wk <= hi):
        continue
    x = m.get(wk, {"cost": 0, "clicks": 0, "days": 0, "impressions": 0})
    new = [round(float(x["cost"]), 2), int(x["clicks"]), int(x["days"]), int(x["impressions"])]
    old = [row[I["ms_spend"]], row[I["ms_clicks"]], row[I["ms_days"]], row[I["ms_impr"]]]
    if new != old:
        row[I["ms_spend"]], row[I["ms_clicks"]], row[I["ms_days"]], row[I["ms_impr"]] = new
        changed += 1
pre = s[:s.index("{")]
open(outf, "w").write(pre + json.dumps(A, separators=(",", ":")) + ";\n")
print(f"weeks updated: {changed} (range {lo}..{hi})")
