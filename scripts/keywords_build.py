#!/usr/bin/env python3
"""Build the Keyword Scorecard data files for sharks.emergencycleanings.com/keywords.html

Inputs (all optional except at least one --raw dir):
  --raw DIR [DIR ...]   pull folders. Each holds any of:
        ranges.json   {"g_kw": ["YYYY-MM-DD","YYYY-MM-DD"], "g_st": [...], "wc": [...]}
                      date span each source covered. Weeks inside a span are REPLACED by the
                      new pull (so a week with no rows is cleared, not left stale).
        g_kw.tsv      Google keyword_view export (gaql_export_tsv)
        g_st*.tsv     Google search_term_view exports
        wc*.json      WhatConvertsCLIAPI paid_keyword_weekly results ({summary, rows})
  --dims DIR            Google settings: g_kwdims*.tsv, g_campaigns.tsv, g_negatives.tsv, g_campneg.tsv
  --names FILE          JSON {"b|<id>": "name", "g|<id>": "name"} for campaigns Google can't name
  --live DIR            current data/keywords folder (merged with the new pulls)
  --out DIR             where data/keywords files are written (can equal --live)
  --today YYYY-MM-DD

Model: everything is weekly (Monday start). A week belongs to the quarter of its Thursday.
Completed quarters -> q-YYYYQn.js, the latest quarter -> recent.js, plus manifest.js.
Keys: "<p>\t<campaignId>\t<keyword lowercased>"; p = g (Google) or b (Microsoft/Bing).
Spend comes from the main Google Ads account only (299-875-2313). Leads, quotable leads,
jobs and revenue come from WhatConverts paid leads (google|bing cpc), for every account.
No names, phone numbers or emails are written.
"""
import argparse, csv, glob, json, os, re, sys, datetime as dt
from collections import defaultdict

MAIN_ACCOUNT = "2998752313"
WC_FROM = "2025-03-31"      # WhatConverts has no leads before Apr 1 2025
JOBS_FROM = "2025-09-22"    # ...and no transactions before Sept 25 2025
MT = {"EXACT": 1, "PHRASE": 2, "BROAD": 4}
STM = {"EXACT": "E", "NEAR_EXACT": "e", "PHRASE": "P", "NEAR_PHRASE": "p", "BROAD": "B"}

csv.field_size_limit(10**9)


def monday(d):
    d = dt.date.fromisoformat(d) if isinstance(d, str) else d
    return d - dt.timedelta(days=d.weekday())


def qkey(week):
    th = dt.date.fromisoformat(week) + dt.timedelta(days=3)
    return f"{th.year}Q{(th.month - 1) // 3 + 1}"


def weeks_in(a, b):
    w, end, out = monday(a), dt.date.fromisoformat(b), []
    while w <= end:
        out.append(w.isoformat())
        w += dt.timedelta(days=7)
    return out


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def tsv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))


def num(x):
    try:
        return float(x or 0)
    except ValueError:
        return 0.0


def parse_js(path):
    s = open(path, encoding="utf-8").read()
    a, b = s.index("("), s.rindex(")")
    return json.loads(s[a + 1:b])


def file_js(obj):
    return "KW.add(" + json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + ");\n"


class Store:
    """week -> source -> {key: [metrics]}"""
    def __init__(self):
        self.kw = defaultdict(dict)    # week -> key -> [cost_cents, clicks, impr, conv100, matchmask]
        self.st = defaultdict(dict)    # week -> (key, term, stmatch) -> [cost_cents, clicks, impr, conv100]
        self.wc = defaultdict(dict)    # week -> key -> [leads, quotable, jobs, revenue, cancelled]
        self.cp = defaultdict(dict)    # week -> campaign id -> [cost_cents, clicks, impr, conv100] (Google campaign totals)
        self.label = {}                # key -> display text (original casing)

    # ---- load live files ------------------------------------------------
    def load_live(self, d):
        for p in sorted(glob.glob(os.path.join(d, "q-*.js")) + glob.glob(os.path.join(d, "recent.js"))):
            o = parse_js(p)
            K, T, W = o["k"], o["t"], o["weeks"]
            for r in o["kw"]:
                self.kw[W[r[0]]][K[r[1]]] = r[2:7]
            for r in o["st"]:
                self.st[W[r[0]]][(K[r[1]], T[r[2]], r[3])] = r[4:8]
            for r in o["wc"]:
                self.wc[W[r[0]]][K[r[1]]] = r[2:7]
            for r in o.get("cp", []):
                self.cp[W[r[0]]][r[1]] = r[2:6]
            for k, v in o.get("lab", {}).items():
                self.label.setdefault(K[int(k)], v)

    # ---- new pulls --------------------------------------------------------
    def clear(self, src, a, b):
        tgt = getattr(self, src)
        for w in weeks_in(a, b):
            tgt.pop(w, None)

    def add_g_kw(self, rows):
        for r in rows:
            w = r["segments.week"]
            text = r.get("ad_group_criterion.keyword.text", "")
            k = f"g\t{r['campaign.id']}\t{norm(text)}"
            self.label.setdefault(k, text.strip())
            m = self.kw[w].setdefault(k, [0, 0, 0, 0, 0])
            m[0] += round(num(r["metrics.cost_micros"]) / 1e4)
            m[1] += int(num(r["metrics.clicks"]))
            m[2] += int(num(r["metrics.impressions"]))
            m[3] += round(num(r["metrics.conversions"]) * 100)
            m[4] |= MT.get(r.get("ad_group_criterion.keyword.match_type", ""), 0)

    def add_g_camp(self, rows):
        for r in rows:
            m = self.cp[r["segments.week"]].setdefault(r["campaign.id"], [0, 0, 0, 0])
            m[0] += round(num(r["metrics.cost_micros"]) / 1e4)
            m[1] += int(num(r["metrics.clicks"]))
            m[2] += int(num(r["metrics.impressions"]))
            m[3] += round(num(r["metrics.conversions"]) * 100)

    def add_g_st(self, rows):
        for r in rows:
            w = r["segments.week"]
            kt = r.get("segments.keyword.info.text", "")
            k = f"g\t{r['campaign.id']}\t{norm(kt)}"
            if kt:
                self.label.setdefault(k, kt.strip())
            term = norm(r.get("search_term_view.search_term", ""))
            sm = STM.get(r.get("segments.search_term_match_type", ""), "?")
            m = self.st[w].setdefault((k, term, sm), [0, 0, 0, 0])
            m[0] += round(num(r["metrics.cost_micros"]) / 1e4)
            m[1] += int(num(r["metrics.clicks"]))
            m[2] += int(num(r["metrics.impressions"]))
            m[3] += round(num(r["metrics.conversions"]) * 100)

    def add_wc(self, obj):
        if "results" in obj:
            obj = obj["results"]["data"]["data"]
        if "data" in obj and "rows" not in obj:
            obj = obj["data"]
        for week, plat, camp, kw, leads, quot, jobs, rev, canc in obj["rows"]:
            if norm(camp) == "test numbers" or norm(kw) == "test number":
                continue    # internal test calls
            p = "b" if plat == "bing" else "g"
            k = f"{p}\t{camp or ''}\t{norm(kw)}"
            m = self.wc[week].setdefault(k, [0, 0, 0, 0, 0])
            for i, v in enumerate((leads, quot, jobs, rev, canc)):
                m[i] += round(v or 0)


def load_raw(store, d):
    rng = json.load(open(os.path.join(d, "ranges.json")))
    if "g_kw" in rng and os.path.exists(os.path.join(d, "g_kw.tsv")):
        store.clear("kw", *rng["g_kw"]); store.add_g_kw(tsv(os.path.join(d, "g_kw.tsv")))
    cp_files = sorted(glob.glob(os.path.join(d, "g_camp*.tsv")))
    if "g_camp" in rng and cp_files:
        store.clear("cp", *rng["g_camp"])
        for p in cp_files:
            store.add_g_camp(tsv(p))
    st_files = sorted(glob.glob(os.path.join(d, "g_st*.tsv")))
    if "g_st" in rng and st_files:
        store.clear("st", *rng["g_st"])
        for p in st_files:
            store.add_g_st(tsv(p))
    wc_files = sorted(glob.glob(os.path.join(d, "wc*.json")))
    if "wc" in rng and wc_files:
        store.clear("wc", *rng["wc"])
        for p in wc_files:
            store.add_wc(json.load(open(p)))


def load_dims(d, live_manifest):
    camps = dict(live_manifest.get("camps", {})) if live_manifest else {}
    info = dict(live_manifest.get("info", {})) if live_manifest else {}
    neg = (live_manifest or {}).get("_neg", {})
    if not d:
        return camps, info, neg
    p = os.path.join(d, "g_campaigns.tsv")
    cstat = {}
    if os.path.exists(p):
        for r in tsv(p):
            cid = r["campaign.id"]
            cstat[cid] = r.get("campaign.status", "")
            camps[f"g\t{cid}"] = [r.get("campaign.name", ""), r.get("campaign.status", ""),
                                  r.get("campaign.advertising_channel_type", ""), "main",
                                  r.get("campaign.bidding_strategy_type", "")]
    files = sorted(glob.glob(os.path.join(d, "g_kwdims*.tsv")))
    if files:
        acc = defaultdict(lambda: {"live": 0, "qs": [], "mt": 0, "ag": set(), "any": 0})
        for p in files:
            for r in tsv(p):
                k = f"g\t{r['campaign.id']}\t{norm(r.get('ad_group_criterion.keyword.text', ''))}"
                a = acc[k]
                a["any"] += 1
                live = (r.get("ad_group_criterion.status") == "ENABLED" and r.get("ad_group.status") == "ENABLED"
                        and cstat.get(r["campaign.id"], "ENABLED") == "ENABLED")
                if r.get("ad_group_criterion.status") != "REMOVED":
                    a["mt"] |= MT.get(r.get("ad_group_criterion.keyword.match_type", ""), 0)
                    a["ag"].add(r.get("ad_group.name", ""))
                if live:
                    a["live"] += 1
                    q = r.get("ad_group_criterion.quality_info.quality_score", "")
                    if q:
                        a["qs"].append(int(num(q)))
                elif r.get("ad_group_criterion.status") == "PAUSED" or r.get("ad_group.status") == "PAUSED":
                    a.setdefault("paused", 1)
        info = {}
        for k, a in acc.items():
            status = "on" if a["live"] else ("paused" if a.get("paused") or a["mt"] else "removed")
            info[k] = [status, max(a["qs"]) if a["qs"] else None, a["mt"], sorted(x for x in a["ag"] if x)[:6]]
    negs = defaultdict(list)
    for name in ("g_negatives.tsv", "g_campneg.tsv"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            for r in tsv(p):
                t = norm(r.get("ad_group_criterion.keyword.text") or r.get("campaign_criterion.keyword.text") or "")
                if t:
                    negs[r["campaign.id"]].append(t)
    if negs:
        neg = {cid: sorted(set(v)) for cid, v in negs.items()}
    return camps, info, neg


def emit(store, out, today, camps, info, neg, names):
    os.makedirs(out, exist_ok=True)
    all_weeks = sorted(set(store.kw) | set(store.st) | set(store.wc))
    last_full = monday(today) - dt.timedelta(days=7)
    all_weeks = [w for w in all_weeks if w <= last_full.isoformat()]
    if not all_weeks:
        sys.exit("no data")
    quarters = sorted({qkey(w) for w in all_weeks})
    recent_q = quarters[-1]
    changed = []

    # campaigns seen in leads but unknown to the main Google account
    seen = set()
    for w in store.cp:
        for cid in store.cp[w]:
            seen.add(f"g\t{cid}")
    for src in (store.kw, store.st, store.wc):
        for w in src:
            for k in src[w]:
                kk = k[0] if isinstance(k, tuple) else k
                p, cid, _ = kk.split("\t")
                seen.add(f"{p}\t{cid}")
    for ck in sorted(seen):
        p, cid = ck.split("\t")
        nm = names.get(f"{p}|{cid}")
        if ck in camps and camps[ck][0]:
            continue
        if not cid:
            camps[ck] = ["(Microsoft lead, no campaign)" if p == "b" else "(Google lead, no campaign)", "", "", "unknown", ""]
        elif p == "b":
            camps[ck] = [nm or f"Microsoft campaign {cid}", "", "SEARCH", "microsoft", ""]
        elif not cid.isdigit():
            camps[ck] = [nm or cid, "", "", "other", ""]
        else:
            camps[ck] = [nm or f"Google campaign {cid} (2nd account)", "", "", "secondary", ""]

    def write(name, obj):
        path = os.path.join(out, name)
        s = file_js(obj)
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if s != old:
            open(path, "w", encoding="utf-8").write(s)
            changed.append(path)

    for q in quarters:
        W = [w for w in all_weeks if qkey(w) == q]
        wi = {w: i for i, w in enumerate(W)}
        K, ki, T, ti = [], {}, [], {}
        def kid(k):
            if k not in ki:
                ki[k] = len(K); K.append(k)
            return ki[k]
        def tid(t):
            if t not in ti:
                ti[t] = len(T); T.append(t)
            return ti[t]
        kw = [[wi[w], kid(k)] + m for w in W for k, m in sorted(store.kw.get(w, {}).items())]
        st = [[wi[w], kid(k), tid(t), sm] + m for w in W for (k, t, sm), m in sorted(store.st.get(w, {}).items())]
        wc = [[wi[w], kid(k)] + m for w in W for k, m in sorted(store.wc.get(w, {}).items())]
        cp = [[wi[w], cid] + m for w in W for cid, m in sorted(store.cp.get(w, {}).items())]
        # campaign spend not on any keyword (Performance Max, dynamic search ads, display) -> "(no keyword)" row
        rk = []
        for w in W:
            kwsum = defaultdict(lambda: [0, 0, 0, 0])
            for k, m in store.kw.get(w, {}).items():
                cid = k.split("\t")[1]
                for i in range(4):
                    kwsum[cid][i] += m[i]
            for cid, m in sorted(store.cp.get(w, {}).items()):
                res = [m[i] - kwsum[cid][i] for i in range(4)]
                if res[0] > 50 or res[2] > 0:
                    rk.append([wi[w], kid(f"g\t{cid}\t")] + [max(0, x) for x in res])
        lab = {str(i): store.label[k] for i, k in enumerate(K) if k in store.label and store.label[k] != k.split("\t")[2]}
        obj = {"kind": "q", "q": q, "weeks": W, "k": K, "t": T, "kw": kw, "st": st, "wc": wc, "cp": cp, "rk": rk, "lab": lab}
        write("recent.js" if q == recent_q else f"q-{q}.js", obj)
    stale = os.path.join(out, f"q-{recent_q}.js")
    if os.path.exists(stale):
        os.remove(stale); changed.append(stale + " (removed)")

    # keep settings only for keywords that have data or are live, so the manifest stays small
    seen_keys = set()
    for src in (store.kw, store.wc):
        for w in src:
            seen_keys.update(src[w].keys())
    for w in store.st:
        seen_keys.update(k for k, _, _ in store.st[w])
    info = {k: v for k, v in info.items() if k in seen_keys or v[0] == "on"}
    st_camps = {k[0].split("\t")[1] for w in store.st for k in store.st[w]}
    write("neg.js", {"kind": "neg", "q": "neg", "neg": {c: v for c, v in neg.items() if c in st_camps}})
    man = {"kind": "manifest", "asOf": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "dataThrough": (dt.date.fromisoformat(all_weeks[-1]) + dt.timedelta(days=6)).isoformat(),
           "firstWeek": all_weeks[0], "weeks": all_weeks, "quarters": quarters, "recent": recent_q,
           "wcFrom": WC_FROM, "jobsFrom": JOBS_FROM, "mainAccount": MAIN_ACCOUNT,
           "camps": camps, "info": info}
    write("manifest.js", man)
    for c in changed:
        print("CHANGED:", c)
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", nargs="*", default=[])
    ap.add_argument("--dims")
    ap.add_argument("--names")
    ap.add_argument("--live")
    ap.add_argument("--out", required=True)
    ap.add_argument("--today", required=True)
    a = ap.parse_args()
    store = Store()
    live_man = None
    if a.live and os.path.exists(os.path.join(a.live, "manifest.js")):
        store.load_live(a.live)
        live_man = parse_js(os.path.join(a.live, "manifest.js"))
        if os.path.exists(os.path.join(a.live, "neg.js")):
            live_man["_neg"] = parse_js(os.path.join(a.live, "neg.js")).get("neg", {})
    for d in a.raw:
        load_raw(store, d)
    camps, info, neg = load_dims(a.dims, live_man)
    names = json.load(open(a.names)) if a.names else {}
    emit(store, a.out, dt.date.fromisoformat(a.today), camps, info, neg, names)


if __name__ == "__main__":
    main()
