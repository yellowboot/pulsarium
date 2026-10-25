# Shared SEC data pipeline

`Update SEC data` in this public repository runs at **00:19, 08:19 and 16:19
Europe/Berlin**, including daylight-saving changes. The morning run follows the
overnight EDGAR daily-index build. The existing database dispatcher starts the
public workflow via `workflow_dispatch`; GitHub has no duplicate native schedule.
The database checks Berlin's current hour, so winter/summer offsets are automatic.
Runner availability can still delay job execution.

## Processing order

1. `scripts/sec/refresh.ts` reads the shared US instrument catalog, the permanent
   filing index and saved company histories. It checks submissions metadata after
   six hours and downloads only unseen filings. It does not read portfolios,
   transactions, watchlists or account tables. At most 25 previously uncached
   catalog-company histories are bootstrapped per run; saved histories remain intact.
2. `sec-insider-radar.py` adds newly indexed Form 4 filings to the existing history
   and publishes the buying/selling snapshot. Completed daily indexes remain the
   discovery source; the two extra runs also catch delayed indexes and retry failures.
3. `sec-sector-analytics.py` computes the sector snapshot and Report Highlights
   from the shared frames and company histories. Previously downloaded historical
   windows remain cached. Recent windows have a six-hour refresh threshold.
4. `publish_pages.py` regenerates only the four insider/sector/report landing pages.
   A commit is made only when their contents change. The existing Pages publication
   serves those files; normal news collection keeps its own schedule.

Each processing stage retains its existing last-good-data behavior. Independent
stages can still finish if another stage fails; the overall workflow reports failure
when any collection stage fails. News and SEC publication share one concurrency
group to avoid competing generated-page commits. There are no push/PR triggers,
automatic test runs, APK builds or archive uploads in this workflow.

## Storage and access

The existing Supabase database, SEC tables, full histories, original filings and
checkpoints are reused. They are not copied into Git, cleared or bootstrapped again.
The site reads the existing public snapshots; the cabinet reads the same company
figures. Authentication, user portfolios, transactions, watchlists, notifications,
encrypted backups and the server-side company-page fallback stay in the private
`yellowboot/investor-platform` repository.

The public worker has only `SEC_INGEST_TOKEN` in GitHub Secrets. It calls the
`sec-ingest` Edge gateway, whose allowlist permits specific SEC tables, global
company fundamentals and limited catalog fields. The gateway rejects user tables,
arbitrary RPCs, embedded relationships, schema-switching headers and deletions.
The Supabase administrative key stays inside the Edge environment. The worker token
is checked against a revocable server-only SHA-256 digest and cannot read or change
its own credential record. `SEC_CONTACT` is a repository variable containing the
published service contact; it is not an IMAP password or mailbox credential.

`history.ts`, `summarize.ts` and `sectors.ts` here are the canonical shared modules.
The cabinet's Edge Function imports a pinned public commit, so an unrelated public
push cannot change deployed server logic. Updating the server pin is deliberate.

The former private SEC workflows and their database dispatch schedule are retired;
the public schedule is the only automatic collection path. Company-page cache misses
can still use the existing authenticated Edge fallback, without starting Actions.

## Incremental reads and egress

`refresh.ts` retains its change-aware refresh index (`companyfacts:refresh-index`):
new filings/submissions and code changes trigger reads, with a roughly 20-hour full
check for catalog companies and a weekly check for supplementary targets. It no
longer downloads every saved fundamentals row. Its Deno read permission remains
restricted to `scripts/sec`.

The Python analytics and insider workers use a disposable SQLite cache restored
between scheduled runs. They first list IDs and source timestamps, then fetch only
new or changed bodies in bounded batches. Source metadata is checked every run;
new filings, corrections and failed-filing retries are not delayed by a cache TTL.
Deleted rows and filings outside the current window are excluded from calculations.
The same company histories serve the sector supplement and financial screens once
per run. Newly saved frames/parsed filings also update the local cache.

The insider projection includes parsed transactions, amendments, footnotes and
coverage fields, but excludes original compressed ownership XML. Full source XML
remains in Supabase. Intermediate and final radar snapshots reuse this run's saved
delta rather than rereading the whole filing window.

`fetch_news.py` does not read Supabase. Its page builder reads the two prepared SEC
snapshots: it checks `updated_at` and transfers a body only when that version has
changed. An independent small cache prevents news runs downloading the large SEC
worker cache. Existing per-process snapshot reuse also remains in place.

Both workers use GitHub Actions **cache**, not artifacts/backups. Cache storage has
a [separate 10 GB free allowance per repository](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
Each file is bounded to 160 MiB, and each workflow removes older entries in its own
prefix/ref after a successful save, retaining two. It never removes unrelated
caches. Cache contents are only public SEC inputs/snapshots, with JSON-compressed
bodies rather than executable serialization. No tokens or user data are saved.
On a cold/evicted/corrupt cache, source bodies are fetched again; authoritative
history and collection checkpoints remain untouched. Cache service failures do not
prevent data processing. A cold run still incurs a full input read, and real daily
traffic depends on how many source rows changed. Logs report versions checked and
bodies read so the next scheduled runs can measure the reduction without a manual
refresh. These changes do not remove traffic from backups, browser visitors or
other cabinet jobs.

## Local verification

From this repository:

```sh
DENO_NO_PACKAGE_JSON=1 deno test --allow-env scripts/sec/test_sec_history.ts
DENO_NO_PACKAGE_JSON=1 deno check scripts/sec/refresh.ts
python -m unittest discover -s scripts/sec -p 'test_sec_*.py'
```

Gateway permission/authentication tests live with the private Edge source at
`supabase/functions/sec-ingest/test_gateway.ts`. Test data and HTTP responses are
mocked; these checks do not start workflows, request SEC history or send mail.

To rotate/revoke access, replace/disable the server credential digest and update
the GitHub secret. To roll back collection ownership, first stop the public schedule,
then restore the former private workflow commit and dispatch schedule. Keep the
existing SEC tables and checkpoints throughout; never enable both schedulers.
