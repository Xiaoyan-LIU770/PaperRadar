"""公开接口检索、保守去重和事务化存储；不调用任何模型接口。"""
from __future__ import annotations

import copy
import csv
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import runpy
import sqlite3
import subprocess
import time
import unicodedata
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
TZ = ZoneInfo("Asia/Shanghai")


def now():
    return dt.datetime.now(TZ).isoformat(timespec="seconds")


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def norm_doi(value):
    value = urllib.parse.unquote(str(value or "").strip()).lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)
    return value.rstrip(" .,;") if re.match(r"^10\.\d{4,9}/\S+$", value) else ""


def norm_arxiv(value):
    value = urllib.parse.unquote(str(value or "")).strip()
    match = re.search(r"(?:arxiv[.:/]|/abs/|/pdf/)?((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7}))(?:v\d+)?", value, re.I)
    return match.group(1).lower() if match else ""


def norm_title(value):
    text = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(c for c in text if c.isalnum())


def stable_id(record):
    ids = record["identifiers"]
    seed = next((k + ":" + ids[k][0] for k in ("doi", "arxiv", "openalex") if ids.get(k)), None)
    seed = seed or "title:" + norm_title(record["title"]) + str(record.get("authors", []))
    return "PR-" + hashlib.sha256(seed.encode()).hexdigest()[:14]


def union(a, b):
    result = copy.deepcopy(a)
    seen = {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in result}
    for x in b:
        key = json.dumps(x, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            result.append(copy.deepcopy(x)); seen.add(key)
    return result


def merge(a, b):
    a["aliases"] = union(a.get("aliases", []), b.get("aliases", []))
    if b.get("paper_id") and a.get("paper_id") and b["paper_id"] != a["paper_id"]:
        a["aliases"] = union(a["aliases"], [b["paper_id"]])
    for field in ("sources", "links", "source_records", "warnings", "run_ids", "duplicate_review"):
        a[field] = union(a.get(field, []), b.get(field, []))
    for scheme, values in b["identifiers"].items():
        a["identifiers"][scheme] = union(a["identifiers"].get(scheme, []), values)
    if len(b.get("abstract") or "") > len(a.get("abstract") or ""):
        a["abstract"] = b["abstract"]
        a["abstract_source"] = b.get("abstract_source")
    if len(b.get("authors", [])) > len(a.get("authors", [])):
        a["authors"] = b["authors"]
    if not a.get("publication_date"):
        a["publication_date"] = b.get("publication_date")
    if b.get("first_seen"):
        a["first_seen"] = min(a.get("first_seen") or b["first_seen"], b["first_seen"])
        a["last_seen"] = max(a.get("last_seen") or b["last_seen"], b["last_seen"])
    a["doi"] = next(iter(a["identifiers"].get("doi", [])), None)
    a["arxiv_id"] = next(iter(a["identifiers"].get("arxiv", [])), None)
    if len(a["identifiers"].get("doi", [])) > 1:
        a["warnings"] = union(a["warnings"], ["同一标识簇出现多个DOI，需核对版本关系"])
    return a


def identity_reason(a, b):
    for scheme in ("doi", "arxiv", "openalex"):
        if set(a["identifiers"].get(scheme, [])) & set(b["identifiers"].get(scheme, [])):
            return scheme
    if not norm_title(a["title"]) or norm_title(a["title"]) != norm_title(b["title"]):
        return None
    for scheme in ("doi", "arxiv"):
        ai, bi = a["identifiers"].get(scheme, []), b["identifiers"].get(scheme, [])
        if ai and bi and not set(ai) & set(bi):
            return None
    aa = {norm_title(x) for x in a.get("authors", [])}
    ba = {norm_title(x) for x in b.get("authors", [])}
    ay, by = (a.get("publication_date") or "")[:4], (b.get("publication_date") or "")[:4]
    if aa & ba and ay.isdigit() and by.isdigit() and abs(int(ay) - int(by)) <= 1:
        return "normalized_title"
    return None


def deduplicate(records):
    groups, events = [], []
    for original in records:
        record = copy.deepcopy(original)
        # 合并所有匹配簇，处理 DOI 与 arXiv 编号形成的传递关联。
        while True:
            match = next(((i, identity_reason(g, record)) for i, g in enumerate(groups) if identity_reason(g, record)), None)
            if match is None:
                break
            index, reason = match
            previous = groups.pop(index)
            events.append({"rule": reason, "kept": previous.get("paper_id") or stable_id(previous), "merged_source_ids": [x["source_id"] for x in record.get("source_records", [])]})
            record = merge(previous, record)
        record.setdefault("paper_id", stable_id(record))
        groups.append(record)
    pairs = []
    for i, a in enumerate(groups):
        at = norm_title(a["title"])
        for b in groups[i + 1:]:
            bt = norm_title(b["title"])
            score = difflib.SequenceMatcher(None, at, bt).ratio() if at and bt else 0
            if score >= 0.93:
                reason = "规范化标题相同但身份信息不足或冲突" if at == bt else "标题高度相似，可能为预印本/正式版或不同研究"
                pair = {"paper_ids": [a["paper_id"], b["paper_id"]], "title_similarity": round(score, 4), "reason": reason, "status": "待人工复核，未合并"}
                pairs.append(pair)
                for p, other in ((a, b), (b, a)):
                    p["duplicate_review"] = union(p.get("duplicate_review", []), [{"other_paper_id": other["paper_id"], "reason": reason, "status": "待人工复核"}])
    return groups, {"input_count": len(records), "unique_count": len(groups), "merged_count": len(records) - len(groups), "merge_rules": dict(Counter(e["rule"] for e in events)), "merge_events": events, "suspected_pairs": pairs}


def skill_module(filename):
    lock = json.loads((ROOT / "config/skills.lock.json").read_text())
    path = (ROOT / lock["installed_root"]) / "paper-lookup/scripts" / filename
    for name in (filename, "_common.py"):
        p = path.parent / name
        expected = lock["files"]["paper-lookup/scripts/" + name]
        if hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Skill文件与锁定版本不符：" + str(p))
    return runpy.run_path(str(path))


def http_get(url, directory, source):
    directory = Path(directory)
    attempts = []
    for attempt in (1, 2):
        raw = directory / f"{source}.attempt-{attempt}.raw"
        headers = directory / f"{source}.attempt-{attempt}.headers"
        command = ["curl", "--disable", "--silent", "--show-error", "--location", "--max-redirs", "3", "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "15", "--max-time", "45", "--max-filesize", "33554432", "--user-agent", "PaperRadar/0.1", "--output", str(raw), "--dump-header", str(headers), "--write-out", "%{http_code}", "--url", url]
        key = os.environ.get("OPENALEX_API_KEY") if source == "openalex" else None
        # 密钥只经环境变量和标准输入传递，不出现在命令参数、URL或日志中。
        header_input = "Authorization: Bearer " + key + "\n" if key else None
        if key: command += ["--header", "@-"]
        started = now()
        result = subprocess.run(command, input=header_input, capture_output=True, text=True, timeout=50)
        status = int(result.stdout.strip()[-3:]) if result.stdout.strip()[-3:].isdigit() else 0
        header_text = headers.read_text(errors="replace") if headers.exists() else ""
        headers.unlink(missing_ok=True)
        retry = re.findall(r"(?im)^retry-after:\s*(\d+)", header_text)
        info = {"attempt": attempt, "started_at": started, "finished_at": now(), "http_status": status, "curl_exit_code": result.returncode, "stderr": result.stderr.strip(), "url": url, "raw_file": str(raw.relative_to(ROOT)), "bytes": raw.stat().st_size if raw.exists() else 0, "retry_after_seconds": int(retry[-1]) if retry else None}
        if raw.exists(): info["sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
        attempts.append(info)
        dump(directory / f"{source}.request.json", {"source": source, "attempts": attempts, "credentials_used": bool(key), "credential_transport": "stdin_header" if key else "none"})
        if result.returncode == 0 and status == 200:
            return raw, attempts
        if attempt == 1 and status in (0, 429, 502, 503, 504):
            delay = int(retry[-1]) if retry else 5
            if delay > 15: break
            time.sleep(max(3.1 if source == "arxiv" else 1, delay))
        else:
            break
    raise RuntimeError(f"{source}请求失败；HTTP {status}，curl退出码{result.returncode}，详见请求日志")


def normalized_record(source, source_id, title, authors, date, abstract, identifiers, links, raw_file, pointer, warnings=None, **extra):
    timestamp = now()
    ids = {"doi": [], "arxiv": [], "openalex": []}
    ids.update(identifiers)
    ids["doi"] = list(dict.fromkeys(filter(None, (norm_doi(v) for v in ids["doi"]))))
    ids["arxiv"] = list(dict.fromkeys(filter(None, (norm_arxiv(v) for v in ids["arxiv"]))))
    return {"title": title, "authors": authors, "publication_date": date, "abstract": abstract or None, "abstract_source": source if abstract else None, "doi": next(iter(ids["doi"]), None), "arxiv_id": next(iter(ids["arxiv"]), None), "identifiers": ids, "sources": [source], "links": list(dict.fromkeys(x for x in links if x)), "source_records": [{"source": source, "source_id": source_id, "raw_file": raw_file, "pointer": pointer, "title": title, "authors": authors, "publication_date": date, "abstract": abstract or None, **extra}], "warnings": warnings or [], "duplicate_review": [], "first_seen": timestamp, "last_seen": timestamp, "run_ids": []}


def parse_arxiv(raw):
    module = skill_module("arxiv_atom.py")
    text = Path(raw).read_text()
    if text.strip().startswith("Rate exceeded"): raise ValueError("arXiv返回限流文本")
    feed = ET.fromstring(text); ns = module["NS"]
    if feed.tag != "{" + ns["atom"] + "}feed": raise ValueError("不是有效Atom feed")
    records = []
    for i, entry in enumerate(feed.findall("atom:entry", ns)):
        if (entry.findtext("atom:title", namespaces=ns) or "").strip() == "Error":
            raise ValueError("arXiv HTTP200错误feed：" + entry.findtext("atom:summary", "", ns))
        p = module["parse_entry"](entry)
        if not p["arxiv_id"] or not p["title"]: raise ValueError("arXiv记录缺少标题/编号")
        records.append(normalized_record("arxiv", p["arxiv_id_versioned"], p["title"], p["authors"], (p["published"] or "")[:10], p["abstract"], {"arxiv": [p["arxiv_id"]], "doi": [p["doi"]]}, [p["abstract_url"] or "https://arxiv.org/abs/" + p["arxiv_id"], p["pdf_url"]], str(Path(raw).relative_to(ROOT)), f"Atom/entry[{i + 1}]", version=p["version"], updated=p["updated"], journal=p["journal_ref"]))
    total = feed.findtext("opensearch:totalResults", "", ns)
    return records, {"reported_total": int(total) if total.isdigit() else None, "query_as_executed": feed.findtext("atom:title", "", ns)}


def parse_openalex(raw):
    module = skill_module("openalex_abstract.py")
    payload = json.loads(Path(raw).read_text())
    if "error" in payload or not isinstance(payload.get("results"), list) or "meta" not in payload:
        raise ValueError("OpenAlex错误对象或缺少results/meta")
    records = []
    for i, work in enumerate(payload["results"]):
        p = module["summarize"](work)
        if not p["id"] or not p["title"]: raise ValueError("OpenAlex记录缺少标题/编号")
        locations = work.get("locations") or []
        links = [p["id"], work.get("doi")]
        for loc in locations:
            links += [loc.get("landing_page_url"), loc.get("pdf_url")]
        arxiv = [norm_arxiv(v) for v in links if v and ("arxiv.org/" in v.lower() or "10.48550/arxiv." in v.lower())]
        warnings = p.get("abstract_warnings", [])
        if not p.get("abstract"): warnings = union(warnings, ["OpenAlex本次未提供摘要；请求没有select字段，不能当作已取得全文"])
        if work.get("is_retracted"): warnings = union(warnings, ["OpenAlex标记为已撤稿，必须人工核实"])
        journal = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
        records.append(normalized_record("openalex", p["id"], p["title"], [a["author"]["display_name"] for a in work.get("authorships", []) if a.get("author", {}).get("display_name")], work.get("publication_date"), p.get("abstract"), {"doi": [p["doi"]], "arxiv": arxiv, "openalex": [p["id"]]}, links, str(Path(raw).relative_to(ROOT)), f"/results/{i}", warnings=warnings, work_type=work.get("type"), journal=journal, is_retracted=work.get("is_retracted"), doi=p["doi"]))
    return records, {"reported_total": payload["meta"].get("count"), "meta": payload["meta"]}


def collect_source(source, config, directory):
    if source == "arxiv":
        params = {"search_query": config["arxiv_query"], "start": 0, "max_results": config["max_per_source"], "sortBy": config.get("arxiv_sort", "relevance"), "sortOrder": "descending"}
        endpoint = "https://export.arxiv.org/api/query"; parser = parse_arxiv
    else:
        search_field = config.get("openalex_search_field", "search")
        if search_field not in ("search", "search.title_and_abstract", "search.title_abstract_keywords"):
            raise ValueError("未支持的OpenAlex检索字段")
        params = {search_field: config["openalex_search"], "filter": f"from_publication_date:{config['date_from']},to_publication_date:{config['date_to']}", "per_page": config["max_per_source"], "page": 1, "sort": config.get("openalex_sort", "relevance_score:desc")}
        endpoint = "https://api.openalex.org/works"; parser = parse_openalex
    url = endpoint + "?" + urllib.parse.urlencode(params)
    details = {"source": source, "endpoint": endpoint, "parameters": params, "requested_url": url, "started_at": now()}
    try:
        raw, attempts = http_get(url, directory, source)
        records, extra = parser(raw)
        if len(records) > config["max_per_source"]: raise ValueError("接口返回超过配置上限")
        in_range, excluded = [], []
        for record in records:
            date = record.get("publication_date")
            if date and config["date_from"] <= date <= config["date_to"]:
                in_range.append(record)
            else:
                excluded.append({"title": record["title"], "publication_date": date, "reason": "日期缺失或不在范围内"})
        details.update(status="success", raw_count=len(records), eligible_date_count=len(in_range), date_excluded=excluded, missing_abstract_count=sum(not r["abstract"] for r in in_range), **extra)
        details["stopped_at_limit"] = bool(extra.get("reported_total") is not None and extra["reported_total"] > len(records))
        dump(directory / f"{source}.normalized.json", in_range)
        return in_range, details
    except Exception as exc:
        details.update(status="failed", error=str(exc), raw_count=None, eligible_date_count=0)
        return [], details
    finally:
        details["finished_at"] = now()
        dump(directory / f"{source}.summary.json", details)


def connect_db(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS papers (paper_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS identifiers (scheme TEXT, value TEXT, paper_id TEXT, PRIMARY KEY(scheme,value,paper_id))")
    conn.execute("CREATE TABLE IF NOT EXISTS aliases (alias_id TEXT PRIMARY KEY, canonical_id TEXT NOT NULL)")
    return conn


def save_run(db_path, records, manifest):
    conn = connect_db(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM papers ORDER BY rowid")]
        old_ids = {r["paper_id"] for r in existing}
        for p in records: p["run_ids"] = union(p.get("run_ids", []), [manifest["run_id"]])
        combined, _ = deduplicate(existing + records)
        current = [p for p in combined if manifest["run_id"] in p["run_ids"]]
        manifest["new_papers"] = sum(p["paper_id"] not in old_ids for p in current)
        manifest["existing_papers_seen"] = len(current) - manifest["new_papers"]
        manifest["library_total"] = len(combined)
        manifest["paper_ids"] = [p["paper_id"] for p in current]
        # 后续补充DOI/arXiv交叉标识时，旧的两个簇可能连通；保存旧ID别名，防止旧记录残留为重复项。
        live_ids = {p["paper_id"] for p in combined}
        for retired in old_ids - live_ids:
            canonical = next((p["paper_id"] for p in combined if retired in p.get("aliases", [])), None)
            if canonical is None: raise RuntimeError("历史文献ID缺少可追溯的合并目标")
            conn.execute("INSERT OR REPLACE INTO aliases VALUES (?,?)", (retired, canonical))
            conn.execute("UPDATE aliases SET canonical_id=? WHERE canonical_id=?", (canonical, retired))
            conn.execute("DELETE FROM papers WHERE paper_id=?", (retired,))
        conn.execute("DELETE FROM identifiers")
        for p in combined:
            conn.execute("INSERT OR REPLACE INTO papers VALUES (?,?)", (p["paper_id"], json.dumps(p, ensure_ascii=False)))
            for scheme, values in p["identifiers"].items():
                for value in values: conn.execute("INSERT OR IGNORE INTO identifiers VALUES (?,?,?)", (scheme, value, p["paper_id"]))
        conn.execute("INSERT OR REPLACE INTO runs VALUES (?,?)", (manifest["run_id"], json.dumps(manifest, ensure_ascii=False)))
        conn.commit()
        return current
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def resolve_paper_id(conn, paper_id):
    if conn.execute("SELECT 1 FROM papers WHERE paper_id=?", (paper_id,)).fetchone(): return paper_id
    row = conn.execute("SELECT canonical_id FROM aliases WHERE alias_id=?", (paper_id,)).fetchone()
    if row: return row[0]
    raise KeyError("文献ID不存在：" + paper_id)


def export_library(db_path, directory):
    conn = connect_db(db_path)
    papers = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM papers ORDER BY rowid")]
    conn.close()
    dump(Path(directory) / "papers.json", {"schema_version": 1, "exported_at": now(), "papers": papers})
    with (Path(directory) / "papers.csv").open("w", newline="", encoding="utf-8-sig") as f:
        fields = ["paper_id", "title", "authors", "publication_date", "abstract", "doi", "arxiv_id", "sources", "first_seen", "last_seen", "relevance", "reason", "evidence", "recommend_close_reading", "duplicate_review", "links"]
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for p in papers:
            r = p.get("relevance", {})
            writer.writerow({**{k: p.get(k, "") for k in fields if k not in ("relevance", "reason", "evidence", "recommend_close_reading")}, "authors": "; ".join(p["authors"]), "sources": "; ".join(p["sources"]), "links": "; ".join(p["links"]), "duplicate_review": json.dumps(p["duplicate_review"], ensure_ascii=False), "relevance": r.get("level", "待筛选"), "reason": r.get("reason", ""), "evidence": json.dumps(r.get("evidence", []), ensure_ascii=False), "recommend_close_reading": r.get("recommend_close_reading", False)})
    return papers


def collect():
    config = json.loads((ROOT / "config/topic.json").read_text())
    if not 1 <= config["max_per_source"] <= 50: raise ValueError("本阶段每来源最多50条")
    run_id = dt.datetime.now(TZ).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    directory = ROOT / "results" / run_id; directory.mkdir()
    dump(directory / "config.json", config)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda source: collect_source(source, config, directory), ("arxiv", "openalex")))
    records = [p for result, _ in results for p in result]
    dump(directory / "all.normalized.json", records)
    unique, dedup = deduplicate(records)
    dump(directory / "deduplication.json", dedup)
    manifest = {"run_id": run_id, "created_at": now(), "topic": config, "sources": [d for _, d in results], "input_count": len(records), "unique_count": len(unique), "duplicate_count": dedup["merged_count"], "suspected_duplicate_pairs": len(dedup["suspected_pairs"]), "status": "success" if all(d["status"] == "success" for _, d in results) else "partial_failure", "mode": "live", "periodic_schedule_created": False}
    current = save_run(ROOT / "database/papers.sqlite", unique, manifest)
    dump(directory / "papers.json", current)
    dump(directory / "manifest.json", manifest)
    export_library(ROOT / "database/papers.sqlite", ROOT / "database")
    dump(ROOT / "logs/latest_run.json", {"run_id": run_id, "manifest": str((directory / "manifest.json").relative_to(ROOT))})
    with (ROOT / "logs/runs.jsonl").open("a", encoding="utf-8") as f: f.write(json.dumps(manifest, ensure_ascii=False) + "\n")
    print(json.dumps({k: manifest[k] for k in ("run_id", "status", "input_count", "unique_count", "duplicate_count", "new_papers", "existing_papers_seen", "library_total")}, ensure_ascii=False))
    return 0 if manifest["status"] == "success" else 2

