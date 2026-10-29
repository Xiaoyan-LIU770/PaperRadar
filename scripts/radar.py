#!/usr/bin/env python3
"""项目统一入口；避免脚本名与paperradar包同名导致导入遮蔽。"""
import argparse
import json
from pathlib import Path

from paperradar.core import ROOT, dump
from paperradar.tracking import DEFAULT_TOPIC, apply_reviews, open_db, papers, register_topic, run_tracking
from paperradar.weekly import generate_weekly


def main():
    parser=argparse.ArgumentParser(description="PaperRadar增量追踪")
    parser.add_argument("--db",type=Path,default=ROOT/"database/papers.sqlite")
    parser.add_argument("--output-root",type=Path,default=ROOT)
    sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("register");p.add_argument("topic_id");p.add_argument("--config",type=Path,required=True)
    sub.add_parser("topics")
    p=sub.add_parser("track");p.add_argument("--topic",default=DEFAULT_TOPIC);p.add_argument("--all-topics",action="store_true");p.add_argument("--mode",choices=["baseline","manual","scheduled-simulation","scheduled","scheduled-test"],default="manual");p.add_argument("--full-range",action="store_true");p.add_argument("--snapshot",type=Path);p.add_argument('--strategy',choices=['legacy-v1','blue-peled-v2'])
    p=sub.add_parser("history");p.add_argument("--topic",default=DEFAULT_TOPIC)
    p=sub.add_parser("report");p.add_argument("--topic",default=DEFAULT_TOPIC);p.add_argument("--run");p.add_argument("--output",type=Path)
    p=sub.add_parser("show");p.add_argument("paper_id")
    p=sub.add_parser("review");p.add_argument("--topic",default=DEFAULT_TOPIC);p.add_argument("--file",type=Path,required=True)
    args=parser.parse_args()
    for path in (args.db,args.output_root):
        if not path.resolve().is_relative_to(ROOT): raise ValueError("文件操作须限定在PaperRadar项目内")
    for field in ("config", "file", "snapshot", "output"):
        path = getattr(args, field, None)
        if path is not None and not path.resolve().is_relative_to(ROOT):
            raise ValueError("输入与输出须限定在PaperRadar项目内：" + field)
    args.db.parent.mkdir(parents=True,exist_ok=True);args.output_root.mkdir(parents=True,exist_ok=True)
    if args.command=="register":
        register_topic(args.db,args.topic_id,json.loads(args.config.read_text()));result={"registered":args.topic_id}
    elif args.command=="track":
        conn=open_db(args.db)
        ids=[r[0] for r in conn.execute("SELECT topic_id FROM topics ORDER BY topic_id")] if args.all_topics else [args.topic]
        conn.close()
        if not ids: raise ValueError("没有登记研究主题")
        result=[run_tracking(args.db,args.output_root,t,args.mode,args.full_range,args.snapshot,args.strategy) for t in ids]
        print(json.dumps([{k:m[k] for k in ("run_id","topic_id","status","input_count","new_papers","existing_papers_seen","metadata_changed_papers","library_total")} for m in result],ensure_ascii=False))
        return 0 if all(m["status"]=="success" for m in result) else 2
    elif args.command=="review":
        apply_reviews(args.db,args.topic,args.file);result={"review_file_applied":str(args.file)}
    else:
        conn=open_db(args.db)
        try:
            if args.command=="topics": result=[{"topic_id":t,**json.loads(p)} for t,p in conn.execute("SELECT * FROM topics")]
            elif args.command=="history":
                result=[json.loads(p) for p, in conn.execute("SELECT payload FROM runs ORDER BY rowid") if json.loads(p).get("topic_id",DEFAULT_TOPIC)==args.topic]
                result=[{k:m.get(k) for k in ("run_id","created_at","mode","status","new_papers","new_paper_ids","metadata_changed_papers","sources")} for m in result]
            elif args.command=="show":
                result=papers(conn)[args.paper_id]
                result["latest_source_observations"]=[{"source_key":s,"metadata":json.loads(p),"run_id":r} for s,p,r in conn.execute("SELECT source_key,payload,run_id FROM observations WHERE paper_id=?",(args.paper_id,))]
                result["analysis_cards"]=[c for c in __import__('paperradar.weekly',fromlist=['analysis_cards']).analysis_cards(args.output_root/'reports/weekly_analysis.json').values() if c['paper_id']==args.paper_id]
            else:
                run_id=args.run
                if not run_id:
                    run_id=next((r for r,p in conn.execute("SELECT run_id,payload FROM runs ORDER BY rowid DESC") if json.loads(p).get("topic_id")==args.topic),None)
                if not run_id: raise ValueError("该主题没有第二阶段运行")
                output=args.output or args.output_root/"reports/weekly"/(run_id+".md")
                if not output.resolve().is_relative_to(ROOT): raise ValueError("报告须保存于项目内")
                result=generate_weekly(args.db,run_id,output)
        finally: conn.close()
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=="__main__": raise SystemExit(main())
