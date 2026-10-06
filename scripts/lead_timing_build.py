#!/usr/bin/env python3
"""Build the Lead & Booking Timing data files for sharks.emergencycleanings.com/lead-timing.html

Input : one or more raw JSON files saved from the Zapier code action
        WhatConvertsCLIAPI / code_action_whatconvertscliapi__time_heatmap_events
        (either the full tool response or just its inner data object).
Output: data/lead-timing/manifest.js, data/lead-timing/recent.js and any newly frozen
        data/lead-timing/q-YYYYQn.js files, written under --out.

Usage
  initial : python3 lead_timing_build.py --raw r1.json r2.json ... --out site --today 2026-10-06
  nightly : python3 lead_timing_build.py --raw r*.json --out site --today YYYY-MM-DD \
                --live-manifest manifest.js --live-recent recent.js
The script prints CHANGED: <path> for every file that must be published and writes
<out>/publish.json: [{path, expected_sha256, content_b64z}] ready for the Zapier action
GitHubCLIAPI / code_action_githubcliapi__put_file_compressed (repo geckoss217/sharks, branch main).

Rules (EC):
  * Lead = an inquiry. Leads from the same phone/email within 30 days collapse into one
    inquiry, timed at the first lead. Appointments and Transactions are never inquiries.
  * Dropped: Sales Stage "Marketing-Sales Call" (test / internal calls), source "test".
  * Booking = a Transaction item (not Cancelled), timed when the Transaction was created.
  * An inquiry counts as booked if its cluster reaches Booked/Completed or gets a
    non-cancelled Transaction within 120 days.
  * All times are America/Denver local.
No names, phone numbers or emails are written to the output files.
"""
import argparse, json, os, re, sys, datetime as dt
from collections import defaultdict
from zoneinfo import ZoneInfo

DEN = ZoneInfo("America/Denver")
EPOCH = dt.date(2025, 1, 1)
FIRST_DAY = dt.date(2025, 6, 1)          # pre-launch Apr/May 2025 out of scope
CLUSTER_DAYS = 30
BOOK_DAYS = 120
NEVER_LINK = {"460f00af"}                # internal cell (Marketing-Sales Call)
B36 = "0123456789abcdefghijklmnopqrstuvwxyz"

TYPES = ["Call", "Form", "Chat", "Other"]
SOURCES = ["Google Ads", "Bing Ads", "Facebook Ads", "Direct", "Organic", "Other"]
REPS = ["SMR", "KTA", "SAM", "HJR", "SCS", "DTJ", "IBP", "GAB", "DBH", "TBD", "Other"]


def enc(n, w):
    s = ""
    for _ in range(w):
        s = B36[n % 36] + s
        n //= 36
    return s


def type_code(t):
    t = (t or "").lower()
    if "phone" in t or t == "call":
        return 0
    if "form" in t:
        return 1
    if "chat" in t:
        return 2
    return 3


def src_code(s, m):
    s = (s or "").lower(); m = (m or "").lower()
    paid = m in ("cpc", "ppc", "paid", "display")
    if "google" in s and paid: return 0
    if "bing" in s and paid: return 1
    if "facebook" in s and paid: return 2
    if s in ("(direct)", "direct") : return 3
    if m == "organic" or s in ("chatgpt.com",): return 4
    return 5


def rep_code(r):
    r = (r or "").strip().upper()
    if not r: r = "TBD"
    return REPS.index(r) if r in REPS else REPS.index("Other")


def load_rows(paths):
    rows = {}
    for p in paths:
        d = json.load(open(p))
        if "results" in d:
            d = d["results"]["data"]["data"]
        elif "data" in d and "rows" not in d:
            d = d["data"]
        for r in d["rows"]:
            rows[r[0]] = r
    return list(rows.values())


def local(ts):
    t = dt.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    return t.astimezone(DEN)


def build_events(rows):
    leads = []
    for r in rows:
        (lid, created, ltype, dup, quot, status, src, med, sval, qval, rep, stage, pk, ek) = r[:14]
        if (src or "").lower() == "test" or stage == "Marketing-Sales Call":
            continue
        t = local(created)
        if t.date() < FIRST_DAY:
            continue
        leads.append(dict(id=lid, t=t, type=ltype, quot=quot, src=src, med=med, rep=rep,
                          stage=stage, pk=pk if pk not in NEVER_LINK else "", ek=ek))
    # keys shared by many different people (placeholder emails, office lines) never link
    pk_e, ek_p = defaultdict(set), defaultdict(set)
    for l in leads:
        if l["pk"] and l["ek"]:
            pk_e[l["pk"]].add(l["ek"]); ek_p[l["ek"]].add(l["pk"])
    bad_pk = {k for k, v in pk_e.items() if len(v) > 3}
    bad_ek = {k for k, v in ek_p.items() if len(v) > 3}
    # union-find over shared keys
    parent = {l["id"]: l["id"] for l in leads}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    first = {}
    for l in leads:
        for k in (("p", l["pk"]) if l["pk"] and l["pk"] not in bad_pk else None,
                  ("e", l["ek"]) if l["ek"] and l["ek"] not in bad_ek else None):
            if k is None: continue
            if k in first: parent[find(l["id"])] = find(first[k])
            else: first[k] = l["id"]
    people = defaultdict(list)
    for l in leads:
        people[find(l["id"])].append(l)

    inquiries, bookings = [], []
    for grp in people.values():
        grp.sort(key=lambda l: l["t"])
        txs = [l for l in grp if l["type"] == "Transaction"]
        for tx in txs:
            if tx["stage"] != "Cancelled":
                bookings.append(tx)
        # split the person's non-transaction leads into inquiries (30-day gap rule)
        inq = []
        for l in grp:
            if l["type"] in ("Transaction", "Appointment"):
                continue
            if inq and (l["t"] - inq[-1]["last"]).days < CLUSTER_DAYS:
                inq[-1]["members"].append(l); inq[-1]["last"] = l["t"]
            else:
                inq.append(dict(start=l["t"], last=l["t"], members=[l]))
        for i, q in enumerate(inq):
            end = inq[i + 1]["start"] if i + 1 < len(inq) else q["start"] + dt.timedelta(days=BOOK_DAYS)
            end = min(end, q["start"] + dt.timedelta(days=BOOK_DAYS))
            m0 = q["members"][0]
            booked = any(m["stage"] in ("Booked", "Completed") for m in q["members"]) or \
                     any(q["start"] <= tx["t"] <= end and tx["stage"] != "Cancelled" for tx in txs)
            quot = any(m["quot"] == "Yes" for m in q["members"]) or booked
            rep = next((m["rep"] for m in q["members"] if m["rep"] and m["rep"] != "TBD"), m0["rep"])
            inquiries.append(dict(t=m0["t"], type=m0["type"], src=m0["src"], med=m0["med"],
                                  rep=rep, quot=quot, booked=booked))
    return inquiries, bookings


def pack(e, is_inq):
    day = (e["t"].date() - EPOCH).days
    flags = (1 if e.get("quot") else 0) + (2 if e.get("booked") else 0) if is_inq else 0
    return enc(day, 3) + B36[e["t"].hour] + str(type_code(e["type"])) + \
        str(src_code(e["src"], e["med"])) + B36[rep_code(e["rep"])] + str(flags)


def qkey(d):
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def qstart(d):
    return dt.date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def day_of(packed):
    return EPOCH + dt.timedelta(days=int(packed[:3], 36))


def chunks(s, n=8):
    return [s[i:i + n] for i in range(0, len(s), n)]


def file_js(obj):
    return "LT.add(" + json.dumps(obj, separators=(",", ":")) + ");\n"


def parse_js(path):
    txt = open(path).read()
    m = re.search(r"\((\{.*\})\)", txt, re.S)
    return json.loads(m.group(1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--today", required=True)
    ap.add_argument("--live-manifest")
    ap.add_argument("--live-recent")
    a = ap.parse_args()
    today = dt.date.fromisoformat(a.today)
    cq = qstart(today)
    pq = qstart(cq - dt.timedelta(days=1))   # recent file covers previous + current quarter

    rows = load_rows(a.raw)
    inq, bk = build_events(rows)
    inq_p = sorted(pack(e, True) for e in inq)
    bk_p = sorted(pack(e, False) for e in bk)

    outdir = os.path.join(a.out, "data", "lead-timing")
    os.makedirs(outdir, exist_ok=True)
    changed = []

    def write(name, obj):
        p = os.path.join(outdir, name)
        open(p, "w").write(file_js(obj))
        changed.append(os.path.join("data", "lead-timing", name))

    if a.live_manifest:
        manifest = parse_js(a.live_manifest)
        live_recent = parse_js(a.live_recent)
        lr_from = dt.date.fromisoformat(live_recent["from"])
        # freeze quarters that are leaving the recent window
        old_inq = chunks(live_recent["inq"]); old_bk = chunks(live_recent["bk"])
        q = lr_from
        while q < pq:
            nq = qstart(q + dt.timedelta(days=95))
            key = qkey(q)
            fi = "".join(x for x in old_inq if q <= day_of(x) < nq)
            fb = "".join(x for x in old_bk if q <= day_of(x) < nq)
            if key not in manifest["quarters"]:
                write(f"q-{key}.js", dict(kind="quarter", key=key, **{"from": str(q)}, to=str(nq - dt.timedelta(days=1)), inq=fi, bk=fb))
                manifest["quarters"].append(key)
            q = nq
        # make sure raw pull covers the recent window
        min_raw = min(local(r[1]).date() for r in rows)
        if min_raw > pq - dt.timedelta(days=CLUSTER_DAYS - 1):
            print(f"WARNING: raw pull starts {min_raw}; needs to start by {pq - dt.timedelta(days=CLUSTER_DAYS)}", file=sys.stderr)
    else:
        manifest = dict(quarters=[])
        q = qstart(FIRST_DAY)
        while q < pq:
            nq = qstart(q + dt.timedelta(days=95))
            key = qkey(q)
            fi = "".join(x for x in inq_p if q <= day_of(x) < nq)
            fb = "".join(x for x in bk_p if q <= day_of(x) < nq)
            write(f"q-{key}.js", dict(kind="quarter", key=key, **{"from": str(max(q, FIRST_DAY))}, to=str(nq - dt.timedelta(days=1)), inq=fi, bk=fb))
            manifest["quarters"].append(key)
            q = nq

    ri = "".join(x for x in inq_p if day_of(x) >= pq)
    rb = "".join(x for x in bk_p if day_of(x) >= pq)
    write("recent.js", dict(kind="recent", **{"from": str(pq)}, to=str(today), inq=ri, bk=rb))

    manifest.update(kind="manifest", asOf=dt.datetime.now(DEN).strftime("%Y-%m-%d %H:%M"),
                    dataThrough=str(today), firstDay=str(FIRST_DAY), firstBooking="2025-09-01",
                    types=TYPES, sources=SOURCES, reps=REPS, epoch=str(EPOCH))
    write("manifest.js", manifest)
    print(f"inquiries={len(inq_p)} bookings={len(bk_p)} recent_inq={len(ri)//8} recent_bk={len(rb)//8}")
    # payloads for the GitHub put_file_compressed Zapier action
    import zlib, base64, hashlib
    pub = []
    for c in changed:
        b = open(os.path.join(a.out, c), "rb").read()
        pub.append(dict(path=c, expected_sha256=hashlib.sha256(b).hexdigest(),
                        content_b64z=base64.b64encode(zlib.compress(b, 9)).decode()))
        print("CHANGED:", c)
    json.dump(pub, open(os.path.join(a.out, "publish.json"), "w"), indent=1)
    print("Payloads written to", os.path.join(a.out, "publish.json"))


if __name__ == "__main__":
    main()
