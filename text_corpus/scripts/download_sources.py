#!/usr/bin/env python3
"""Download an explicit, reviewable source manifest. Never crawl arbitrary links."""
from __future__ import annotations
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 50 * 1024 * 1024
USER_AGENT = "PublicHealthResearchCorpus/0.1 (text-only academic proof of concept)"

def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]

def content_kind(data, content_type, url):
    if data.startswith(b"%PDF-"): return "application/pdf", ".pdf"
    if data.startswith(b"PK\x03\x04"):
        ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
        types = {".docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".pptx":"application/vnd.openxmlformats-officedocument.presentationml.presentation"}
        if ext in types: return types[ext], ext
        raise ValueError("Archive or unsupported ZIP container; requires explicit text extraction plan")
    ct = content_type.split(";", 1)[0].strip().lower()
    if ct in ("text/html", "application/xhtml+xml") or re.search(br"<(?:!doctype\s+html|html)(?:\s|>)", data[:4096], re.I):
        return "text/html", ".html"
    if ct in ("application/xml", "text/xml") or data.lstrip().startswith(b"<?xml"):
        return "application/xml", ".xml"
    if ct.startswith("text/") or ct in ("application/json",):
        return ct, {"text/csv":".csv", "application/json":".json"}.get(ct, ".txt")
    raise ValueError("Unsupported non-text media type: " + ct)

def check_response(data, media_type):
    if not data: raise ValueError("Empty response")
    if media_type == "text/html":
        sample = data[:30000].decode("utf-8", "replace")
        title = " ".join(re.findall(r"<(?:title|h1)[^>]*>(.*?)</(?:title|h1)>", sample, re.I | re.S))
        title = re.sub("<[^>]+>", " ", title).strip()
        blockers = r"access denied|request rejected|robot verification|just a moment|verify (?:that )?you are human|page not found|403 forbidden|404 not found|service unavailable"
        if re.search(blockers, title, re.I):
            raise ValueError("Non-content response title: " + title[:200])

def acquire(source, previous, timeout):
    source = dict(source)
    sid = source["source_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sid): raise ValueError("Unsafe source_id: " + sid)
    prior = previous.get(sid)
    if prior and prior.get("status") == "downloaded":
        p = Path(prior["raw_path"])
        if not p.is_absolute(): p = ROOT / p
        if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == prior.get("sha256"):
            return {**prior, **{k:v for k,v in source.items() if k not in ("status", "raw_path")}, "status":"downloaded", "raw_path":str(p.relative_to(ROOT)), "reused_existing_download":True}
    attempts=[]
    # A fetch_url is used only for an explicitly observed representation link
    # (for example, the publisher's View Full Text link). Keep source_url as
    # the exact customer-listed URL and record the actual request in attempts.
    urls = [source.get("fetch_url", source["source_url"])] + source.get("alternate_urls", [])
    for url in urls:
        when = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            if urllib.parse.urlparse(url).scheme not in ("http", "https"):
                raise ValueError("Only explicit HTTP(S) sources are supported")
            req = urllib.request.Request(url, headers={"User-Agent":USER_AGENT, "Accept":"text/html,application/pdf,application/xml,text/plain,*/*;q=0.5"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES: raise ValueError("Source exceeds 50 MiB acquisition limit")
                resolved = response.geturl()
                media_type, extension = content_kind(data, response.headers.get("Content-Type", ""), resolved)
                check_response(data, media_type)
                digest = hashlib.sha256(data).hexdigest()
                path = ROOT / "raw" / f"{sid}--{digest[:12]}{extension}"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                header_keys = ("Content-Type", "Content-Length", "Last-Modified", "ETag", "Date")
                headers = {k:response.headers[k] for k in header_keys if k in response.headers}
                attempts.append({"url":url,"status":response.status,"retrieved_at":when})
                return {**source, "status":"downloaded", "resolved_url":resolved, "retrieved_at":when, "raw_path":str(path.relative_to(ROOT)), "media_type":media_type, "size_bytes":len(data), "sha256":digest, "http_headers":headers, "attempts":attempts, "metadata":{**source.get("metadata",{}), "http_last_modified":headers.get("Last-Modified"), "legal_effective_date":None, "operative_status":"not_determined", "source_list_reconciliation":source.get("metadata",{}).get("source_list_reconciliation", "pending_original_linked_inventory")}}
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            attempts.append({"url":url,"retrieved_at":when,"error":str(exc),"http_status":getattr(exc,"code",None)})
    return {**source, "status":"failed", "attempts":attempts, "failure_reason":attempts[-1]["error"], "retrieved_at":attempts[-1]["retrieved_at"]}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="+", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--output", type=Path, default=ROOT/"manifests"/"acquisition.jsonl")
    args=parser.parse_args()
    sources={}
    for manifest in args.manifests:
        for source in read_jsonl(manifest):
            if source.get("source_url"): sources[source["source_id"]]=source
    previous={r["source_id"]:r for r in read_jsonl(args.output)} if args.output.exists() else {}
    records=dict(previous)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs={pool.submit(acquire,s,previous,args.timeout):sid for sid,s in sources.items()}
        for job in concurrent.futures.as_completed(jobs):
            sid=jobs[job]
            try: result=job.result()
            except Exception as exc: result={**sources[sid],"status":"failed","failure_reason":str(exc)}
            records[sid]=result
            temp=args.output.with_suffix(".tmp")
            temp.write_text("".join(json.dumps(records[k],ensure_ascii=False)+"\n" for k in sorted(records)),encoding="utf-8")
            temp.replace(args.output)
            print(json.dumps({"source_id":sid,"status":result["status"],"bytes":result.get("size_bytes"),"error":result.get("failure_reason")}),flush=True)
    print(json.dumps({"sources":len(records),"downloaded":sum(r["status"]=="downloaded" for r in records.values()),"failed":sum(r["status"]!="downloaded" for r in records.values())}),flush=True)

if __name__ == "__main__": main()
