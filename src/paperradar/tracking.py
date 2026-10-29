"""增量入口：复用第一阶段接口/规范化器，不重新合并历史文献簇。"""
from __future__ import annotations

import copy
import datetime as dt
import difflib
import hashlib
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .core import ROOT, TZ, collect_source, connect_db, dump, export_library, norm_arxiv, norm_doi, norm_title, now, stable_id, union
from .screening import classify

DEFAULT_TOPIC = "blue-peled-stability"
SCHEMA = """
CREATE TABLE IF NOT EXISTS topics(topic_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS topic_papers(topic_id TEXT,paper_id TEXT,first_seen TEXT NOT NULL,relevance TEXT NOT NULL,PRIMARY KEY(topic_id,paper_id));
CREATE TABLE IF NOT EXISTS observations(source_key TEXT PRIMARY KEY,paper_id TEXT NOT NULL,payload TEXT NOT NULL,run_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metadata_changes(run_id TEXT,paper_id TEXT,source_key TEXT,before_json TEXT,after_json TEXT,fields TEXT,PRIMARY KEY(run_id,paper_id,source_key));
CREATE TABLE IF NOT EXISTS review_pairs(a TEXT,b TEXT,reason TEXT,first_run TEXT,last_run TEXT,status TEXT NOT NULL DEFAULT '待人工复核',PRIMARY KEY(a,b,reason));
CREATE TABLE IF NOT EXISTS unresolved_observations(source_key TEXT PRIMARY KEY,payload TEXT NOT NULL,candidate_ids TEXT NOT NULL,reason TEXT NOT NULL,run_id TEXT NOT NULL);
"""


def open_db(path):
    conn = connect_db(path)
    conn.executescript(SCHEMA)
    return conn


def papers(conn):
    return {row[0]: json.loads(row[1]) for row in conn.execute("SELECT paper_id,payload FROM papers ORDER BY rowid")}


def source_key(record):
    sr = record["source_records"][-1]
    value = norm_arxiv(sr["source_id"]) if sr["source"] == "arxiv" else sr["source_id"]
    return sr["source"] + ":" + value


def metadata(record):
    sr = record["source_records"][-1]
    return {k: sr.get(k) for k in ("title", "authors", "publication_date", "abstract", "version", "updated", "journal", "work_type", "is_retracted", "doi")}


def register_topic(db_path, topic_id, config, adopt_existing=False):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", topic_id):
        raise ValueError("topic_id须为小写字母、数字、下划线或连字符")
    for key in ("topic_zh", "date_from", "arxiv_query", "openalex_search"):
        if not config.get(key): raise ValueError("主题配置缺少 " + key)
    dt.date.fromisoformat(config["date_from"])
    if not 1 <= config.get("max_per_source", 50) <= 50: raise ValueError("每来源限1—50条")
    conn = open_db(db_path)
    try:
        with conn:
            if conn.execute("SELECT 1 FROM topics WHERE topic_id=?", (topic_id,)).fetchone():
                raise ValueError("主题已登记；不会覆盖现有配置或重建基线")
            cfg = {**config, "registered_at": now(), "schedule_enabled": False}
            conn.execute("INSERT INTO topics VALUES (?,?)", (topic_id, json.dumps(cfg, ensure_ascii=False)))
            if adopt_existing:
                if topic_id != DEFAULT_TOPIC: raise ValueError("只有迁移第一阶段主题时允许接收既有文献")
                for pid, p in papers(conn).items():
                    conn.execute("INSERT INTO topic_papers VALUES (?,?,?,?)", (topic_id, pid, p["first_seen"], json.dumps(p.get("relevance", {}), ensure_ascii=False)))
                    for sr in p["source_records"]:
                        r = {"source_records": [sr]}
                        conn.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?,?)", (source_key(r), pid, json.dumps(metadata(r), ensure_ascii=False), "phase1-migration"))
    finally:
        conn.close()


def topic_config(conn, topic_id):
    row = conn.execute("SELECT payload FROM topics WHERE topic_id=?", (topic_id,)).fetchone()
    if not row: raise ValueError("未登记研究主题：" + topic_id)
    return json.loads(row[0])


def query_config(conn, topic_id, mode, full_range=False, date_to=None):
    cfg = topic_config(conn, topic_id)
    end = date_to or dt.datetime.now(TZ).date().isoformat()
    start = cfg["date_from"]
    if mode != "baseline" and not full_range:
        runs = [json.loads(x[0]) for x in conn.execute("SELECT payload FROM runs ORDER BY rowid DESC")]
        complete = next((r for r in runs if r.get("topic_id") == topic_id and r.get("status") == "success" and not r.get("test_data")), None)
        anchor = (complete or {}).get("topic", {}).get("date_to")
        # 首次增量以第一阶段成功截止日为锚点；失败运行绝不推进锚点。
        if not anchor and topic_id == DEFAULT_TOPIC:
            anchor = next((r.get("topic", {}).get("date_to") for r in runs if r.get("status") == "success" and not r.get("topic_id")), None)
        if anchor:
            start = max(start, (dt.date.fromisoformat(anchor) - dt.timedelta(days=cfg.get("overlap_days", 35))).isoformat())
    if start > end: raise ValueError("起始日期晚于截止日期")
    cfg.update(date_from=start, date_to=end, max_per_source=cfg.get("max_per_source", 50))
    date_clause = "submittedDate:[" + start.replace("-", "") + "0000 TO " + end.replace("-", "") + "2359]"
    base = re.sub(r"\s+AND\s+submittedDate:\[[^\]]+\]", "", cfg["arxiv_query"], flags=re.I)
    cfg["arxiv_query"] = "(" + base + ") AND " + date_clause
    if mode != "baseline" and not full_range:
        cfg.update(arxiv_sort="submittedDate", openalex_sort="publication_date:desc")
    return cfg


def relevance(record, topic_id, config):
    if config.get("screening_version") == "blue-peled-v1":
        return classify(record)
    # 新主题的科研判断交给当前Codex；不能套用蓝光专题规则。
    return {"level": "待筛选", "status": "待Codex逐条复核", "reason": "新主题需按其纳入标准阅读标题/摘要；尚未作语义判断。", "evidence": [{"field": "title", "quote": record["title"]}], "recommend_close_reading": False}


def conflict(a, b):
    for scheme in ("doi", "arxiv"):
        aa, bb = set(a["identifiers"].get(scheme, [])), set(b["identifiers"].get(scheme, []))
        if aa and bb and aa.isdisjoint(bb): return True
    return False


def save_tracking_run(db_path, records, manifest):
    """一个事务保存新增、命中、源版本及差异；不删除/合并历史papers行。"""
    conn = open_db(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM runs WHERE run_id=?", (manifest["run_id"],)).fetchone():
            raise ValueError("run_id已存在，不能覆盖历史运行")
        topic_id = manifest["topic_id"]; cfg = topic_config(conn, topic_id)
        library = papers(conn)
        before = {r[0] for r in conn.execute("SELECT paper_id FROM topic_papers WHERE topic_id=?", (topic_id,))}
        if manifest["mode"] == "baseline" and before: raise ValueError("主题已有基线；请执行manual更新")
        manifest["previous_topic_ids"] = sorted(before)
        seen, new_global, new_topic, changed, unresolved = set(), set(), set(), set(), set()
        matches = []
        for original in records:
            r = copy.deepcopy(original)
            for scheme, fn in (("doi", norm_doi), ("arxiv", norm_arxiv)):
                r["identifiers"][scheme] = sorted(set(filter(None, map(fn, r["identifiers"].get(scheme, [])))))
            key = source_key(r)
            candidates = set()
            for pid, p in library.items():
                if any(set(p["identifiers"].get(s, [])) & set(r["identifiers"].get(s, [])) for s in ("doi", "arxiv", "openalex")):
                    candidates.add(pid)
            observed = conn.execute("SELECT paper_id FROM observations WHERE source_key=?", (key,)).fetchone()
            if observed: candidates.add(observed[0])
            if len(candidates) > 1 or (candidates and conflict(library[next(iter(candidates))], r)):
                reason = "跨历史条目身份桥接或标识冲突；可能为预印本/正式版，未合并、未计为确定新增"
                conn.execute("INSERT OR REPLACE INTO unresolved_observations VALUES (?,?,?,?,?)", (key, json.dumps(r, ensure_ascii=False), json.dumps(sorted(candidates)), reason, manifest["run_id"]))
                unresolved.add(key)
                continue
            pid = next(iter(candidates), None)
            is_new = pid is None
            if is_new:
                pid = stable_id(r)
                if pid in library:
                    pid += "-" + hashlib.sha256(key.encode()).hexdigest()[:6]
                r.update(paper_id=pid, first_seen=manifest["created_at"], last_seen=manifest["created_at"], run_ids=[])
                r["relevance"] = relevance(r, topic_id, cfg)
                library[pid] = r; new_global.add(pid)
            p = library[pid]
            old_observation = conn.execute("SELECT payload FROM observations WHERE source_key=?", (key,)).fetchone()
            latest = metadata(r)
            if old_observation:
                prior = json.loads(old_observation[0]); fields = [k for k in latest if latest[k] != prior.get(k)]
                if fields:
                    conn.execute("INSERT OR REPLACE INTO metadata_changes VALUES (?,?,?,?,?,?)", (manifest["run_id"], pid, key, json.dumps(prior, ensure_ascii=False), json.dumps(latest, ensure_ascii=False), json.dumps(fields)))
                    p["metadata_review_required"] = True; changed.add(pid)
            conn.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?,?)", (key, pid, json.dumps(latest, ensure_ascii=False), manifest["run_id"]))
            conn.execute("DELETE FROM unresolved_observations WHERE source_key=?", (key,))
            # 保留原科研摘要和审核字段；新增来源链与标识，差异在observations中供复核。
            for field in ("sources", "links", "source_records", "warnings"):
                p[field] = union(p.get(field, []), r.get(field, []))
            for scheme, values in r["identifiers"].items():
                p["identifiers"][scheme] = union(p["identifiers"].get(scheme, []), values)
            p["doi"] = next(iter(p["identifiers"].get("doi", [])), None)
            p["arxiv_id"] = next(iter(p["identifiers"].get("arxiv", [])), None)
            p["last_seen"] = max(p["last_seen"], manifest["created_at"])
            p["run_ids"] = union(p.get("run_ids", []), [manifest["run_id"]])
            conn.execute("INSERT OR REPLACE INTO papers VALUES (?,?)", (pid, json.dumps(p, ensure_ascii=False)))
            for scheme, values in p["identifiers"].items():
                for value in values: conn.execute("INSERT OR IGNORE INTO identifiers VALUES (?,?,?)", (scheme, value, pid))
            if pid not in before and pid not in new_topic:
                rel = relevance(p, topic_id, cfg)
                conn.execute("INSERT INTO topic_papers VALUES (?,?,?,?)", (topic_id, pid, manifest["created_at"], json.dumps(rel, ensure_ascii=False)))
                new_topic.add(pid)
            seen.add(pid)
            matches.append({"source_key": key, "paper_id": pid, "decision": "new_library_record" if is_new else "existing_library_record"})
        # 标题相似只记录关系；跨DOI版本不自动合并。复核状态不会被更新覆盖。
        for pid in seen:
            for other, q in library.items():
                if other == pid: continue
                score = difflib.SequenceMatcher(None, norm_title(library[pid]["title"]), norm_title(q["title"])).ratio()
                if score >= .93:
                    a, b = sorted((pid, other)); reason = "标题相似度≥0.93；版本/语种/同一研究关系待核实，未合并"
                    conn.execute("INSERT INTO review_pairs(a,b,reason,first_run,last_run) VALUES (?,?,?,?,?) ON CONFLICT(a,b,reason) DO UPDATE SET last_run=excluded.last_run", (a, b, reason, manifest["run_id"], manifest["run_id"]))
        successful = sum(s["status"] == "success" or s.get("successful_request_count",0)>0 for s in manifest["sources"])
        status = "success" if all(s["status"]=="success" for s in manifest["sources"]) and successful else "partial_failure" if successful else "failed"
        counts = {k: 0 for k in ("高", "中", "低", "待筛选")}
        for pid in new_topic:
            rel = json.loads(conn.execute("SELECT relevance FROM topic_papers WHERE topic_id=? AND paper_id=?", (topic_id, pid)).fetchone()[0])
            level = rel.get("level", "待筛选"); counts[level] = counts.get(level, 0) + 1
        manifest.update(status=status, new_papers=len(new_topic) if successful else None, new_library_records=len(new_global) if successful else None, new_paper_ids=sorted(new_topic), new_library_ids=sorted(new_global), paper_ids=sorted(seen), existing_papers_seen=len(seen-new_topic), unique_count=len(seen), input_count=len(records), metadata_changed_papers=len(changed), metadata_changed_ids=sorted(changed), unresolved_count=len(unresolved), new_relevance_counts=counts, library_total=len(library), matches=matches, finished_at=now(), periodic_schedule_created=False)
        conn.execute("INSERT INTO runs VALUES (?,?)", (manifest["run_id"], json.dumps(manifest, ensure_ascii=False)))
        conn.commit()
        return manifest
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def run_tracking(db_path, output_root, topic_id, mode="manual", full_range=False, snapshot=None, strategy=None):
    output_root = Path(output_root)
    if Path(db_path).resolve() != (ROOT / "database/papers.sqlite").resolve() and output_root.resolve() == ROOT:
        raise ValueError("隔离库必须配套隔离输出目录，禁止覆盖正式导出")
    if snapshot and Path(db_path).resolve() == (ROOT / "database/papers.sqlite").resolve():
        raise ValueError("历史回放禁止写入正式数据库")
    if mode == 'scheduled-test' and Path(db_path).resolve() == (ROOT / 'database/papers.sqlite').resolve():
        raise ValueError('定时验收禁止写入正式数据库')
    conn = open_db(db_path)
    try: cfg = query_config(conn, topic_id, mode, full_range)
    finally: conn.close()
    run_id = dt.datetime.now(TZ).strftime("%Y%m%dT%H%M%S") + "-p2-" + uuid.uuid4().hex[:6]
    directory = output_root / "results" / run_id; directory.mkdir(parents=True)
    dump(directory / "config.json", cfg)
    started = now()
    from .search_strategy import active_strategy, collect_optimized
    chosen_strategy = strategy or ('legacy-v1' if snapshot or mode=='baseline' or full_range else active_strategy(topic_id))
    credential_source = 'not_loaded_for_replay'
    if snapshot:
        bundle = json.loads(Path(snapshot).read_text())
        records, details = bundle["records"], bundle["sources"]
        dump(directory / "replay_provenance.json", {"test_data": True, "snapshot": str(snapshot), "sha256": hashlib.sha256(Path(snapshot).read_bytes()).hexdigest(), "note": "测试快照回放，不代表当前新增发表"})
    else:
        from .credentials import load_openalex_key
        import os
        key,credential_source = load_openalex_key()
        previous_key=os.environ.get('OPENALEX_API_KEY')
        if key:os.environ['OPENALEX_API_KEY']=key
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                arxiv_future=pool.submit(collect_source,'arxiv',cfg,directory)
                openalex_future=pool.submit(collect_optimized,cfg,directory/'openalex-queries',groups=cfg.get('openalex_groups') or ([{'id':'topic','query':cfg['openalex_search']}] if cfg.get('screening_version')!='blue-peled-v1' else None),window_days=3650 if full_range else 7,adaptive=not full_range) if chosen_strategy=='blue-peled-v2' else pool.submit(collect_source,'openalex',cfg,directory)
                result=[arxiv_future.result(),openalex_future.result()]
        finally:
            if previous_key is None:os.environ.pop('OPENALEX_API_KEY',None)
            else:os.environ['OPENALEX_API_KEY']=previous_key
        records, details = [p for rr, _ in result for p in rr], [s for _, s in result]
    manifest = {"run_id": run_id, "created_at": started, "topic_id": topic_id, "topic": cfg, "mode": mode, "test_data": bool(snapshot) or mode=='scheduled-test', "sources": details, "full_range": full_range,"search_strategy":chosen_strategy,"credential_source":credential_source}
    dump(directory / "all.normalized.json", records)
    save_tracking_run(db_path, records, manifest)
    dump(directory / "manifest.json", manifest)
    (output_root / "logs").mkdir(exist_ok=True)
    with (output_root / "logs/tracking_runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(manifest, ensure_ascii=False) + "\n")
    export_dir = output_root / "database"; export_dir.mkdir(exist_ok=True)
    export_library(db_path, export_dir)
    from .weekly import generate_weekly
    generate_weekly(db_path, run_id, output_root / "reports/weekly" / (run_id + ".md"))
    return manifest


def apply_reviews(db_path, topic_id, path):
    reviews = json.loads(Path(path).read_text()); conn = open_db(db_path)
    try:
        with conn:
            lib = papers(conn)
            for pid, decision in reviews.items():
                row = conn.execute("SELECT relevance FROM topic_papers WHERE topic_id=? AND paper_id=?", (topic_id,pid)).fetchone()
                if not row: raise ValueError("文献不属于该主题：" + pid)
                old = json.loads(row[0])
                if old.get("status") == "Codex已逐条复核": raise ValueError("已有审核不可由批量新增审核覆盖：" + pid)
                if decision.get("level") not in ("高", "中", "低"): raise ValueError("无效等级")
                if not decision.get("evidence") or not decision.get("reason"): raise ValueError("缺少证据或理由")
                for e in decision["evidence"]:
                    if not e.get("quote") or e["quote"] not in (lib[pid].get(e["field"]) or ""): raise ValueError("证据不在已保存文献中")
                decision = {**decision, "status": "Codex已逐条复核", "screened_at": now(), "method": "Codex阅读保存的标题/摘要逐条复核"}
                conn.execute("UPDATE topic_papers SET relevance=? WHERE topic_id=? AND paper_id=?", (json.dumps(decision, ensure_ascii=False),topic_id,pid))
                if topic_id == DEFAULT_TOPIC and lib[pid].get("relevance",{}).get("status") != "Codex已逐条复核":
                    lib[pid]["relevance"] = decision
                    conn.execute("UPDATE papers SET payload=? WHERE paper_id=?", (json.dumps(lib[pid], ensure_ascii=False),pid))
    finally: conn.close()
