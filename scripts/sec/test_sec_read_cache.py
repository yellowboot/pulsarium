import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from sec_read_cache import ReadCache, FRAME_CACHE, INSIDER_SELECT, insider_row, compact_insider
from prune_read_caches import stale_caches


class Source:
    def __init__(self):
        self.data = {str(i): {"id": str(i), "fetched_at": "v1", "payload_gzip": str(i)*10} for i in range(3)}
        self.bodies = []

    def metadata(self, path):
        return [{"id": r["id"], "fetched_at": r["fetched_at"]} for r in self.data.values()]

    def call(self, path):
        ids = parse_qs(urlsplit(path).query)["id"][0][4:-1].split(",")
        self.bodies.extend(ids)
        return [dict(self.data[key]) for key in ids if key in self.data]


class IncrementalReads(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "cache.sqlite3")
        self.cache = ReadCache(self.path)
        self.source = Source()

    def tearDown(self):
        self.cache.close()
        self.directory.cleanup()

    def read(self):
        return self.cache.rows(self.source.call, self.source.metadata, "sec_market_frames", "",
                               FRAME_CACHE, "id,payload_gzip,fetched_at", batch=2)

    def test_restart_reads_only_new_or_changed_bodies_and_drops_deleted_rows(self):
        self.assertEqual(len(self.read()), 3)
        self.assertEqual(self.source.bodies, ["0", "1", "2"])
        self.cache.close()
        self.cache = ReadCache(self.path)
        self.source.bodies.clear()
        self.assertEqual(len(self.read()), 3)
        self.assertEqual(self.source.bodies, [])
        self.source.data["1"].update(fetched_at="v2", payload_gzip="amended")
        self.source.data["3"] = dict(id="3", fetched_at="v1", payload_gzip="new")
        del self.source.data["0"]
        result = self.read()
        self.assertEqual(self.source.bodies, ["1", "3"])
        self.assertEqual({r["id"] for r in result}, {"1", "2", "3"})
        self.assertEqual(result[0]["payload_gzip"], "amended")

    def test_revision_from_body_wins_when_source_changes_during_read(self):
        def metadata(path):
            result = self.source.metadata(path)
            self.source.data["1"]["fetched_at"] = "v2"
            return result
        self.cache.rows(self.source.call, metadata, "sec_market_frames", "", FRAME_CACHE,
                        "id,payload_gzip,fetched_at")
        self.source.bodies.clear()
        self.read()
        self.assertEqual(self.source.bodies, [])

    def test_missing_delta_never_publishes_partial_history(self):
        with self.assertRaisesRegex(RuntimeError, "Incomplete SEC delta"):
            self.cache.rows(lambda _: [], self.source.metadata, "sec_market_frames", "", FRAME_CACHE,
                            "id,payload_gzip,fetched_at")

    def test_corrupt_record_and_empty_cache_fall_back_to_source(self):
        self.read()
        self.cache.db.execute("update entries set body=x'0001' where id='1'")
        self.cache.db.commit()
        self.source.bodies.clear()
        self.assertEqual(len(self.read()), 3)
        self.assertEqual(self.source.bodies, ["1"])

    def test_corrupt_database_is_rebuilt_from_source(self):
        self.cache.close()
        Path(self.path).write_bytes(b"broken cache")
        self.cache = ReadCache(self.path)
        self.assertEqual(len(self.read()), 3)
        self.assertEqual(self.source.bodies, ["0", "1", "2"])

    def test_failed_delta_read_is_retried_on_next_run(self):
        self.read()
        self.source.data["1"]["fetched_at"] = "v2"
        def unavailable(_): raise TimeoutError()
        with self.assertRaises(TimeoutError):
            self.cache.rows(unavailable, self.source.metadata, "sec_market_frames", "", FRAME_CACHE,
                            "id,payload_gzip,fetched_at")
        self.source.bodies.clear()
        self.assertEqual(len(self.read()), 3)
        self.assertEqual(self.source.bodies, ["1"])

    def test_insider_projection_keeps_the_same_calculated_snapshot(self):
        from test_sec_insider_radar import radar, record
        import datetime as dt
        original = record()
        projected = {k: v for k, v in original.items() if k != "payload"}
        projected.update(("p_" + k, v) for k, v in original["payload"].items()
                         if "p_" + k + ":" in INSIDER_SELECT)
        state = dict(through="2026-10-06", history_from="2026-10-01", sales_history_from="2026-10-01")
        with patch.object(radar, "stamp", return_value="fixed"):
            self.assertEqual(radar.snapshot([original], state, dt.date(2026, 10, 6)),
                             radar.snapshot([insider_row(projected)], state, dt.date(2026, 10, 6)))

    def test_cache_is_scoped_to_source_and_projection(self):
        self.read()
        self.assertIsNone(self.cache.get("other-projection", "0", "v1"))
        self.cache.scope = "other-project"
        self.assertIsNone(self.cache.get(FRAME_CACHE, "0", "v1"))

    def test_size_limit_evicts_old_bodies_without_deleting_source(self):
        self.read()
        self.cache.prepare(max_bytes=100)
        self.assertEqual(len(self.source.data), 3)
        self.source.bodies.clear()
        self.assertEqual(len(self.read()), 3)
        self.assertTrue(self.source.bodies)

    def test_projection_preserves_amendments_and_footnotes_without_xml(self):
        projected = dict(accession="1", filed_date="2026-10-25", status="parsed",
                         p_schema_version=2, p_form="4/A", p_original_date="2026-10-23",
                         p_notes={"F1": "trading plan"}, p_sales=[{"amount": 123}])
        row = insider_row(projected)
        self.assertEqual(row["payload"]["form"], "4/A")
        self.assertEqual(row["payload"]["notes"], {"F1": "trading plan"})
        self.assertEqual(row["payload"]["sales"], [{"amount": 123}])
        self.assertNotIn("ownership_xml_gzip", INSIDER_SELECT)
        row["payload"]["ownership_xml_gzip"] = "raw"
        self.assertNotIn("ownership_xml_gzip", compact_insider(row)["payload"])
        self.assertEqual(row["payload"]["ownership_xml_gzip"], "raw")
        self.assertIsNone(insider_row(dict(accession="2", filed_date="2026-10-25", p_form=None))["payload"])

    def test_cache_cleanup_keeps_two_and_does_not_touch_other_workers_or_refs(self):
        rows = [dict(id=i, key="sec-read-v1-"+str(i), ref="refs/heads/master", created_at=str(i)) for i in range(5)]
        rows += [dict(id=9, key="other-worker", ref="refs/heads/master", created_at="9"),
                 dict(id=10, key="sec-read-v1-branch", ref="refs/heads/feature", created_at="9")]
        self.assertEqual([r["id"] for r in stale_caches(rows, "sec-read-v1-", "refs/heads/master")], [2, 1, 0])

    def test_company_supplement_and_signals_share_one_history_read(self):
        import sec_financial_signals as signals
        class Store:
            def __init__(self): self.calls = 0
            def cached_frames(self, prefix):
                self.calls += 1
                return [{"id": "companyfacts/0000000123", "payload_gzip": '{"doc":{"cik":123}}'}]
        store = Store()
        self.assertIs(signals.histories(store, json.loads), signals.histories(store, json.loads))
        self.assertEqual(store.calls, 1)

    def test_news_snapshot_reuses_unchanged_body_and_refreshes_new_revision(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        import site_build as site
        from io import BytesIO
        calls, revision = [], ["v1"]
        def response(request, **kwargs):
            fields = parse_qs(urlsplit(request.full_url).query)["select"][0]
            calls.append(fields)
            row = {"updated_at": revision[0]}
            if fields != "updated_at": row["payload"] = {"version": revision[0]}
            return BytesIO(json.dumps([row]).encode())
        with patch.dict(os.environ, {"SEC_READ_CACHE": self.path}), patch.object(site.urllib.request, "urlopen", response):
            site.sec_snapshot.cache_clear()
            self.assertEqual(site.sec_snapshot("sec_sector_analytics"), {"version": "v1"})
            site.sec_snapshot.cache_clear()
            self.assertEqual(site.sec_snapshot("sec_sector_analytics"), {"version": "v1"})
            revision[0] = "v2"
            site.sec_snapshot.cache_clear()
            self.assertEqual(site.sec_snapshot("sec_sector_analytics"), {"version": "v2"})
        site.sec_snapshot.cache_clear()
        self.assertEqual(calls, ["updated_at", "payload,updated_at", "updated_at", "updated_at", "payload,updated_at"])


if __name__ == "__main__":
    unittest.main()
