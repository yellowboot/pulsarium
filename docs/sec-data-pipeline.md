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
