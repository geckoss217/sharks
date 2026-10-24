# Keyword Scorecard — weekly refresh runbook

Page: https://sharks.emergencycleanings.com/keywords.html
Data: `data/keywords/` (manifest.js, recent.js, q-YYYYQn.js, neg.js). Builder: `scripts/keywords_build.py`.
Runs every Monday after 6:00 AM MT. Everything below is read-only against Google Ads and WhatConverts.

## 0. Setup
- Attach and clone the repo: add_repo geckoss217/sharks (push access), then
  `git clone --depth 1 https://github.com/geckoss217/sharks <work>/sharks`.
- `TODAY` = today's date in America/Denver. `END` = the Sunday before TODAY. `START` = END − 20 days (a Monday).
  Re-pulling 3 weeks picks up late conversions, late jobs and WhatConverts edits.
- Make a pull folder `<work>/pull/` and a settings folder `<work>/dims/`.

## 1. Google Ads (main account 299-875-2313) — Zapier action GoogleAdsCLIAPI / code_action_googleadscliapi__gaql_export_tsv
Params: {"query": "<GAQL>"} (never "plain": true). Returns row_count, sha256, b64z.
Getting b64z to disk without retyping: if the tool result was too large to show inline, the harness saved it to a
tool-results .txt file — read b64z and sha256 from that file with python. Otherwise write the b64z string to a file.
The blob can arrive as several separately padded base64 chunks; the decoder handles that.
Decode + verify: `python3 sharks/scripts/keywords_decode.py <file.b64> <sha256> <out.tsv>` (must print OK, rows = row_count).

- pull/g_kw.tsv:
  SELECT segments.week, campaign.id, ad_group.id, ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, metrics.cost_micros, metrics.clicks, metrics.impressions, metrics.conversions, metrics.conversions_value FROM keyword_view WHERE segments.date BETWEEN 'START' AND 'END' AND metrics.impressions > 0
- pull/g_st_1.tsv:
  SELECT segments.week, campaign.id, search_term_view.search_term, segments.search_term_match_type, segments.keyword.info.text, segments.keyword.info.match_type, metrics.cost_micros, metrics.clicks, metrics.impressions, metrics.conversions FROM search_term_view WHERE segments.date BETWEEN 'START' AND 'END' AND metrics.clicks > 0
- dims/g_campaigns.tsv:
  SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, campaign.bidding_strategy_type FROM campaign
- dims/g_kwdims.tsv:
  SELECT campaign.id, ad_group.id, ad_group.name, ad_group.status, ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, ad_group_criterion.status, ad_group_criterion.quality_info.quality_score FROM ad_group_criterion WHERE ad_group_criterion.type = 'KEYWORD' AND ad_group_criterion.negative = FALSE
- First Monday of the month only (negative lists; skip other weeks — the builder keeps the last ones):
  dims/g_negatives.tsv: SELECT campaign.id, ad_group.id, ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type FROM ad_group_criterion WHERE ad_group_criterion.type = 'KEYWORD' AND ad_group_criterion.negative = TRUE AND ad_group_criterion.status != 'REMOVED'
  dims/g_campneg.tsv: SELECT campaign.id, campaign_criterion.keyword.text, campaign_criterion.keyword.match_type FROM campaign_criterion WHERE campaign_criterion.type = 'KEYWORD' AND campaign_criterion.negative = TRUE
  (Pull both or neither — the builder replaces the whole negative list when either file is present.)

## 2. WhatConverts — Zapier action WhatConvertsCLIAPI / code_action_whatconvertscliapi__paid_keyword_weekly
Params: {"start_date": "START", "end_date": "END", "lookback_days": 30}. Save the inner data object
({"summary":…, "rows":…}) to pull/wc_1.json. Check with python: per platform, the row sums of leads, quotable,
jobs, revenue and cancelled equal the summary. If the call errors, retry once, then split the range in half (wc_1, wc_2).

## 3. Build
- pull/ranges.json: {"g_kw": ["START","END"], "g_st": ["START","END"], "wc": ["START","END"]}
  Only list a source whose files are present and verified; a listed source REPLACES those weeks.
- `python3 sharks/scripts/keywords_build.py --live sharks/data/keywords --raw pull --dims dims --names sharks/data/keywords/campaign-names.json --out sharks/data/keywords --today TODAY`
  It prints CHANGED: lines. A new quarter rolls the old recent.js into q-YYYYQn.js automatically.
- Sanity check before committing: the newest week's Google cost in pull/g_kw.tsv is within a few dollars of the
  Weekly Ads Scorecard's g_spend for that week (data/ads.js), and WhatConverts google leads match its gl column.

## 4. Publish
- `git add data/keywords && git commit -m "Keyword scorecard refresh <END>"` then `git push origin HEAD:main`
  (fetch + rebase first if main moved). Vercel deploys main.
- If git push is refused, publish each changed file with Zapier GitHubCLIAPI / code_action_githubcliapi__put_file_compressed
  (repo geckoss217/sharks, branch main, content = base64 zlib of the file, expected_sha256 of the file).
- Verify: fetch https://sharks.emergencycleanings.com/data/keywords/manifest.js and check dataThrough = END.

## 5. Report (bullets only, 3–5 lines)
Weeks refreshed, Google spend + paid leads + jobs for the newest week, anything that failed.
