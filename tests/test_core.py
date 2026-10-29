"""以下所有构造记录均为测试数据，不进入正式文献数据库。"""
import copy
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from paperradar.core import ROOT, collect_source, deduplicate, export_library, http_get, norm_arxiv, norm_doi, norm_title, normalized_record, parse_arxiv, parse_openalex, save_run, resolve_paper_id
from paperradar.screening import classify


def fixture(source="arxiv", doi="10.1234/test-data-a", arxiv="2301.00001", title="TEST DATA: Blue perovskite light-emitting diodes", author="Test Author"):
    p = normalized_record(source, "TEST-DATA-ONLY", title, [author], "2023-01-02", "TEST DATA ONLY. Blue perovskite LEDs show an operational lifetime change.", {"doi": [doi], "arxiv": [arxiv]}, [], "tests/synthetic-only", "/test")
    p["first_seen"] = p["last_seen"] = "2026-10-21T00:00:00+08:00"
    return p


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tests", prefix="tests-")
        self.dir = Path(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def test_doi_normalization(self):
        self.assertEqual(norm_doi("https://doi.org/10.1234/TEST-A"), "10.1234/test-a")
        self.assertEqual(norm_doi("not-a-doi"), "")

    def test_arxiv_versions(self):
        self.assertEqual(norm_arxiv("https://arxiv.org/abs/2301.00001v3"), "2301.00001")
        self.assertEqual(norm_arxiv("https://arxiv.org/pdf/hep-th/9901001v2"), "hep-th/9901001")

    def test_unicode_title(self):
        self.assertEqual(norm_title("Blue — Light-Emitting Diodes!"), norm_title("Blue light emitting diodes"))

    def test_doi_merge_preserves_sources(self):
        a, b = fixture(), fixture("openalex", arxiv="")
        u, d = deduplicate([a, b])
        self.assertEqual(len(u), 1); self.assertEqual(set(u[0]["sources"]), {"arxiv", "openalex"})
        self.assertEqual(d["merge_rules"], {"doi": 1})
        self.assertEqual(len(u[0]["source_records"]), 2)

    def test_arxiv_merge(self):
        u, d = deduplicate([fixture(doi=""), fixture("openalex", doi="", arxiv="2301.00001v2")])
        self.assertEqual(len(u), 1); self.assertEqual(d["merge_rules"], {"arxiv": 1})

    def test_normalized_title_merge(self):
        u, d = deduplicate([fixture(doi="", arxiv=""), fixture("openalex", doi="", arxiv="", title="TEST DATA Blue Perovskite Light Emitting Diodes")])
        self.assertEqual(len(u), 1); self.assertEqual(d["merge_rules"], {"normalized_title": 1})

    def test_conflicting_doi_flagged(self):
        u, d = deduplicate([fixture(arxiv=""), fixture("openalex", doi="10.1234/test-data-b", arxiv="")])
        self.assertEqual(len(u), 2); self.assertEqual(len(d["suspected_pairs"]), 1)
        self.assertTrue(all(p["duplicate_review"] for p in u))

    def test_title_without_author_confirmation_not_merged(self):
        a, b = fixture(doi="", arxiv=""), fixture("openalex", doi="", arxiv="", author="Different Test Person")
        u, d = deduplicate([a, b]); self.assertEqual(len(u), 2); self.assertEqual(len(d["suspected_pairs"]), 1)

    def test_transitive_identity(self):
        a = fixture(arxiv=""); b = fixture(doi="", title="TEST DATA different version")
        bridge = fixture()
        u, d = deduplicate([a, b, bridge]); self.assertEqual(len(u), 1); self.assertEqual(d["merged_count"], 2)

    def test_persistence_idempotence_and_first_seen(self):
        db = self.dir / "test.sqlite"; p = fixture(); unique, _ = deduplicate([p])
        m1 = {"run_id": "TEST-RUN-1"}; first = save_run(db, unique, m1)
        first[0]["manual_note"] = "TEST DATA user annotation"
        with sqlite3.connect(db) as conn: conn.execute("UPDATE papers SET payload=?", (json.dumps(first[0]),))
        p2 = fixture("openalex"); p2["first_seen"] = p2["last_seen"] = "2026-10-22T00:00:00+08:00"
        m2 = {"run_id": "TEST-RUN-2"}; second = save_run(db, deduplicate([p2])[0], m2)
        self.assertEqual(m2["new_papers"], 0); self.assertEqual(m2["library_total"], 1)
        self.assertEqual(second[0]["paper_id"], first[0]["paper_id"])
        self.assertEqual(second[0]["first_seen"], "2026-10-21T00:00:00+08:00")
        self.assertEqual(second[0]["manual_note"], "TEST DATA user annotation")
        self.assertEqual(second[0]["run_ids"], ["TEST-RUN-1", "TEST-RUN-2"])
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT count(*) FROM runs").fetchone()[0], 2)

    def test_existing_unseen_records_preserved(self):
        db = self.dir / "test.sqlite"
        save_run(db, deduplicate([fixture()])[0], {"run_id": "TEST1"})
        save_run(db, deduplicate([fixture(doi="10.1234/test-new", arxiv="2301.00002", title="TEST DATA unrelated title")])[0], {"run_id": "TEST2"})
        self.assertEqual(len(export_library(db, self.dir)), 2)

    def test_http200_arxiv_error_rejected(self):
        p = self.dir / "error.xml"; p.write_text('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Error</title><summary>TEST malformed query</summary></entry></feed>')
        with self.assertRaises(ValueError): parse_arxiv(p)

    def test_openalex_missing_abstract_is_explicit(self):
        p = self.dir / "openalex.json"; p.write_text(json.dumps({"meta":{"count":1},"results":[{"id":"https://openalex.org/W00000000","title":"TEST DATA","publication_date":"2023-01-01","abstract_inverted_index":None}]}))
        records, _ = parse_openalex(p)
        self.assertIsNone(records[0]["abstract"]); self.assertTrue(records[0]["warnings"])

    def test_openalex_abstract_reconstruction(self):
        p = self.dir / "openalex.json"; p.write_text(json.dumps({"meta":{"count":1},"results":[{"id":"https://openalex.org/W00000000","title":"TEST DATA","abstract_inverted_index":{"test":[1],"synthetic":[0]}}]}))
        self.assertEqual(parse_openalex(p)[0][0]["abstract"], "synthetic test")

    def test_screening_ignores_citation_count(self):
        a, b = fixture(), fixture(); a["citations"]=0; b["citations"]=100000
        for p in [a,b]: p["abstract"]="TEST DATA ONLY. Blue perovskite LEDs have an operational lifetime improvement."
        self.assertEqual(classify(a)["level"], classify(b)["level"])
        self.assertTrue(all(e["quote"] in (a.get(e["field"]) or "") for e in classify(a)["evidence"]))

    def test_missing_abstract_not_recommended_as_verified(self):
        a = fixture(); a["abstract"] = None
        self.assertFalse(classify(a)["recommend_close_reading"])
        self.assertIn("缺少摘要", classify(a)["reason"])

    def test_rate_limit_honors_long_retry_after(self):
        def fake(command, **kwargs):
            Path(command[command.index('--output')+1]).write_text('{"error":"TEST rate limit"}')
            Path(command[command.index('--dump-header')+1]).write_text('HTTP/2 429\nRetry-After: 600\n')
            return SimpleNamespace(stdout='429',stderr='',returncode=0)
        with patch('paperradar.core.subprocess.run', side_effect=fake) as process, patch('paperradar.core.time.sleep') as sleep:
            with self.assertRaises(RuntimeError): http_get('https://api.openalex.org/works', self.dir, 'openalex')
            self.assertEqual(process.call_count,1); sleep.assert_not_called()
        request=json.loads((self.dir/'openalex.request.json').read_text())
        self.assertEqual(request['attempts'][0]['retry_after_seconds'],600)

    def test_credential_transport_does_not_enter_url_or_logs(self):
        synthetic='TEST-CREDENTIAL-NOT-A-REAL-ACCOUNT-KEY'
        def fake(command, **kwargs):
            self.assertNotIn(synthetic,' '.join(command))
            self.assertIn(synthetic,kwargs['input'])
            Path(command[command.index('--output')+1]).write_text('{"meta":{"count":0},"results":[]}')
            Path(command[command.index('--dump-header')+1]).write_text('HTTP/2 200\n')
            return SimpleNamespace(stdout='200',stderr='',returncode=0)
        with patch.dict(os.environ,{'OPENALEX_API_KEY':synthetic}), patch('paperradar.core.subprocess.run',side_effect=fake):
            http_get('https://api.openalex.org/works',self.dir,'openalex')
        self.assertNotIn(synthetic,(self.dir/'openalex.request.json').read_text())

    def test_failed_source_is_not_zero_results(self):
        config={'max_per_source':50,'openalex_search':'TEST DATA ONLY','date_from':'2023-01-01','date_to':'2026-10-21'}
        with patch('paperradar.core.http_get',side_effect=RuntimeError('TEST HTTP 429')):
            records,summary=collect_source('openalex',config,self.dir)
        self.assertEqual(records,[]);self.assertEqual(summary['status'],'failed')
        self.assertIsNone(summary['raw_count']);self.assertIn('429',summary['error'])

    def test_genuine_arxiv_empty_feed_is_successful_parse(self):
        p=self.dir/'empty.xml';p.write_text('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/"><o:totalResults>0</o:totalResults></feed>')
        records,meta=parse_arxiv(p);self.assertEqual(records,[]);self.assertEqual(meta['reported_total'],0)

    def test_new_cross_identifier_merges_persisted_clusters_with_aliases(self):
        db=self.dir/'bridge.sqlite'
        a=fixture(arxiv='');b=fixture(doi='',title='TEST DATA different known version')
        initial=save_run(db,deduplicate([a,b])[0],{'run_id':'TEST-BRIDGE-1'})
        old_ids={p['paper_id'] for p in initial}
        m={'run_id':'TEST-BRIDGE-2'};current=save_run(db,deduplicate([fixture()])[0],m)
        self.assertEqual(m['library_total'],1);self.assertEqual(m['new_papers'],0)
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM papers').fetchone()[0],1)
            self.assertEqual({resolve_paper_id(conn,pid) for pid in old_ids},{current[0]['paper_id']})
        self.assertEqual(len(current[0]['identifiers']['doi']),1)
        self.assertEqual(len(current[0]['identifiers']['arxiv']),1)


if __name__ == "__main__": unittest.main(verbosity=2)

