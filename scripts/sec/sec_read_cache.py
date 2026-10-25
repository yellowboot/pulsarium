"""Disposable cache of public SEC inputs, validated against source revisions.

Supabase remains authoritative. Each run lists small IDs/timestamps, then reads
only missing/changed bodies. No credentials, user data or executable objects are
stored. Losing the cache costs one cold read, never history or a checkpoint.
"""
import argparse
import atexit
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.parse
import zlib

FRAME_CACHE = "frames-v1"
INSIDER_CACHE = "insiders-v1"
INSIDER_FIELDS = ("schema_version", "form", "original_date", "issuer_cik",
                  "symbol", "name", "owners", "purchases", "sales", "notes", "source")
INSIDER_SELECT = ("accession,filed_date,form_type,source_path,status,attempts,error,updated_at,"
                  + ",".join(f"p_{field}:payload->{field}" for field in INSIDER_FIELDS))


def insider_row(row):
    """Reconstruct parsed data without transferring retained ownership XML."""
    result = {k: v for k, v in row.items() if not k.startswith("p_")}
    if row.get("p_form") is not None:
        result["payload"] = {k: row["p_" + k] for k in INSIDER_FIELDS
                             if row.get("p_" + k) is not None}
        result["payload"].update(accession=row["accession"], filed_date=row["filed_date"])
    else:
        result["payload"] = None
    return result


def compact_insider(row):
    result = dict(row)
    if result.get("payload"):
        result["payload"] = {k: v for k, v in result["payload"].items()
                             if k != "ownership_xml_gzip"}
    return result


class ReadCache:
    def __init__(self, path=None):
        self.path = path or os.environ.get("SEC_READ_CACHE")
        self.scope = os.environ.get("SUPABASE_URL", "public-sec")
        self.db = None
        if not self.path:
            return
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            try:
                self.open()
            except sqlite3.DatabaseError:
                self.close()
                Path(self.path).unlink(missing_ok=True)
                self.open()
            atexit.register(self.close)
        except (OSError, sqlite3.DatabaseError):
            self.close()
            print("SEC read cache unavailable; using authoritative source.", flush=True)

    def open(self):
        self.db = sqlite3.connect(self.path)
        self.db.execute("create table if not exists entries (scope text, namespace text, id text, "
                        "revision text, body blob, touched real, primary key(scope, namespace, id))")

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    def get(self, namespace, key, revision):
        if self.db is None or revision is None:
            return None
        try:
            row = self.db.execute("select body from entries where scope=? and namespace=? and id=? and revision=?",
                                  (self.scope, namespace, key, revision)).fetchone()
            return json.loads(zlib.decompress(row[0])) if row else None
        except (sqlite3.DatabaseError, zlib.error, ValueError, TypeError):
            return None

    def put(self, namespace, rows, key="id", revision="fetched_at"):
        if self.db is None:
            return
        try:
            values = [(self.scope, namespace, str(r[key]), r[revision],
                       zlib.compress(json.dumps(r, separators=(",", ":"), allow_nan=False).encode()), time.time())
                      for r in rows if r.get(revision) is not None]
            self.db.executemany("insert or replace into entries values(?,?,?,?,?,?)", values)
            self.db.commit()
        except (sqlite3.DatabaseError, ValueError, TypeError):
            self.db.rollback()
            print("SEC read cache write skipped; source data is unchanged.", flush=True)

    def rows(self, call, pager, table, filters, namespace, select, key="id",
             revision="fetched_at", batch=12, transform=None):
        metadata = pager(f"{table}?select={key},{revision}{filters}&order={key}")
        records, missing = {}, []
        for row in metadata:
            saved = self.get(namespace, str(row[key]), row[revision])
            if saved is None:
                missing.append(str(row[key]))
            else:
                records[str(row[key])] = saved
        for offset in range(0, len(missing), batch):
            ids = missing[offset:offset + batch]
            # IDs come from the source metadata, not from user-supplied SQL.
            encoded = urllib.parse.quote(",".join(ids), safe="/,-")
            page = call(f"{table}?select={select}&{key}=in.({encoded})&order={key}&limit={batch}")
            if {str(r[key]) for r in page} != set(ids):
                raise RuntimeError(f"Incomplete SEC delta for {table}; keep previous snapshot")
            page = [transform(r) for r in page] if transform else page
            self.put(namespace, page, key, revision)
            records.update((str(r[key]), r) for r in page)
        print(f"SEC cache {namespace}: {len(metadata)} versions checked, {len(missing)} bodies read.", flush=True)
        return [records[str(r[key])] for r in metadata]

    def prepare(self, max_bytes=160 * 1024 * 1024):
        """Bound the saved file, including old windows and obsolete namespaces."""
        if self.db is None:
            return
        if Path(self.path).stat().st_size > max_bytes:
            total = self.db.execute("select coalesce(sum(length(body)),0) from entries").fetchone()[0]
            for scope, namespace, key, size in self.db.execute(
                    "select scope,namespace,id,length(body) from entries order by touched").fetchall():
                if total < max_bytes * .75:
                    break
                self.db.execute("delete from entries where scope=? and namespace=? and id=?", (scope, namespace, key))
                total -= size
            self.db.commit()
            self.db.execute("vacuum")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    cache = ReadCache()
    cache.prepare()
    cache.close()
