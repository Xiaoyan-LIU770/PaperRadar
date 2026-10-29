"""离线校验依赖、锁定解析器、示例和 Python 语法，不写数据库。"""
import ast
import json
import shutil
import sys
from paperradar.core import ROOT, skill_module

def main():
    assert sys.version_info >= (3, 11), '需要 Python 3.11+'
    assert shutil.which('curl'), '需要 curl'
    for filename in ('arxiv_atom.py','openalex_abstract.py'):
        skill_module(filename)
    for path in ROOT.rglob('*.py'):
        if '.venv' not in path.parts and '.git' not in path.parts:
            ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
    for path in (ROOT/'config/examples').glob('*.json'):
        config=json.loads(path.read_text())
        assert all(config.get(k) for k in ('topic_zh','date_from','arxiv_query','openalex_search'))
    assert (ROOT/'.agents/skills/research-literature-agent/SKILL.md').is_file()
    print('安装校验通过：Python、curl、解析器哈希、示例配置、源码语法和 Skill 文件。')

if __name__=='__main__': main()
