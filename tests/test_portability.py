"""公开版通用主题与离线流程回归；全部为合成数据。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from paperradar.core import ROOT
from paperradar.tracking import relevance, register_topic, run_tracking
from paperradar.search_strategy import collect_optimized
from offline_demo import run_demo

class PortabilityTests(unittest.TestCase):
    def test_offline_pipeline(self):
        run_demo()

    def test_generic_config_overrides_legacy_topic_name(self):
        result=relevance({'title':'TEST DATA blue perovskite LED'},'blue-peled-stability',{'screening_version':'generic'})
        self.assertEqual(result['level'],'待筛选')

    def test_generic_window_query_does_not_use_blue_recipe(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as temp:
            out=Path(temp);db=out/'test.sqlite'
            cfg=json.loads((ROOT/'config/examples/molecular-gnn.json').read_text())
            register_topic(db,'test-generic',cfg)
            ok={'source':'test','status':'success','raw_count':0}
            with patch('paperradar.credentials.load_openalex_key',return_value=(None,'mock')),patch('paperradar.tracking.collect_source',return_value=([],ok)),patch('paperradar.search_strategy.collect_optimized',return_value=([],ok)) as collect:
                run_tracking(db,out,'test-generic',strategy='blue-peled-v2')
            self.assertEqual(collect.call_args.kwargs['groups'],cfg['openalex_groups'])

    def test_invalid_group_cannot_escape_output_directory(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as temp:
            cfg={'date_from':'2025-01-01','date_to':'2025-01-02','max_per_source':1}
            with self.assertRaises(ValueError):
                collect_optimized(cfg,Path(temp),groups=[{'id':'../../escape','query':'test'}])

    def test_vendor_hash_tampering_rejected(self):
        from paperradar.core import skill_module
        original=Path.read_bytes
        def read(path):
            data=original(path)
            return data+b'\n' if path.name=='arxiv_atom.py' else data
        with patch.object(Path,'read_bytes',read):
            with self.assertRaises(RuntimeError):skill_module('arxiv_atom.py')
