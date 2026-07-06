#!/usr/bin/env python3
"""
Batch lesson converter for the GitHub Action.
Processes every heavy lesson JSON in --lessons-dir, extracting inline images and
model HTML into <repo>/assets/<code>/, and writing slim JSONs to --slim-dir.

Same extraction logic as the standalone bhanzu_assets.py, but batch mode + emits
a short summary the workflow uses. Reconciles: content-hashed filenames mean
unchanged assets are rewritten identically (git sees no change), edited assets
get new names, and orphans are reported.
"""
import json, os, sys, hashlib, base64, re, argparse, urllib.parse, glob

def content_hash(s): return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]
def data_uri_ext(uri):
    m = re.match(r"data:image/([a-zA-Z0-9.+-]+)[;,]", uri)
    if not m: return "bin"
    return {"jpeg":"jpg","svg+xml":"svg"}.get(m.group(1).lower(), m.group(1).lower())
def decode_data_uri(uri):
    header, payload = uri.split(",", 1)
    if ";base64" in header: return base64.b64decode(payload)
    return urllib.parse.unquote(payload).encode("utf-8")
def is_model_html(s):
    if not isinstance(s, str): return False
    head = s[:200].lower()
    return head.strip().startswith("<!doctype") or "<html" in head
def code_to_folder(code):
    # URL-safe slug: any run of non [A-Za-z0-9-] becomes a single "_", trim edges.
    s = re.sub(r"[^A-Za-z0-9-]+", "_", str(code or "lesson"))
    return s.strip("_") or "lesson"

def convert_one(path, repo, base_url, slim_dir):
    d = json.load(open(path, encoding="utf-8"))
    code = code_to_folder(d.get("code"))
    base = base_url.rstrip("/") + "/" + code
    assets_dir = os.path.join(repo, "assets", code)
    os.makedirs(assets_dir, exist_ok=True)
    existing = set(os.listdir(assets_dir)) if os.path.isdir(assets_dir) else set()
    wanted=set(); n_img=n_model=0
    for ch in d.get("chunks", []):
        for it in ch.get("items", []):
            iid = it.get("id","item")
            for field,urlf in (("src","srcUrl"),("img","imgUrl")):
                v=it.get(field)
                if isinstance(v,str) and v.startswith("data:image/"):
                    try:
                        raw=decode_data_uri(v); fn=f"{iid}-{field}-{content_hash(v)}.{data_uri_ext(v)}"
                        wanted.add(fn)
                        if fn not in existing:
                            open(os.path.join(assets_dir,fn),"wb").write(raw)
                        it[urlf]=f"{base}/{fn}"; del it[field]; n_img+=1
                    except Exception as e: print(f"  ! {iid}.{field}: {e}")
            h=it.get("html")
            if is_model_html(h):
                fn=f"{iid}-model-{content_hash(h)}.html"; wanted.add(fn)
                if fn not in existing:
                    open(os.path.join(assets_dir,fn),"w",encoding="utf-8").write(h)
                it["htmlUrl"]=f"{base}/{fn}"; del it["html"]; n_model+=1
    os.makedirs(slim_dir, exist_ok=True)
    slim_name=f"{code}_slim.json"
    slim_path=os.path.join(slim_dir, slim_name)
    json.dump(d, open(slim_path,"w",encoding="utf-8"), ensure_ascii=False)
    orphans=sorted(existing-wanted)
    size=len(json.dumps(d,ensure_ascii=False))
    print(f"[{d.get('code')}] imgs={n_img} models={n_model} slim={size/1024:.1f}KB -> {slim_path}")
    if orphans:
        print(f"  ⚠ orphaned in assets/{code}/ (edit/delete left these unused):")
        for o in orphans: print(f"      {o}")
    return code, (d.get("title") or d.get("code") or code), slim_name

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--lessons-dir",required=True)
    ap.add_argument("--repo",required=True)
    ap.add_argument("--base-url",required=True)
    ap.add_argument("--slim-dir",required=True)
    ap.add_argument("--slim-url",default=None,
                    help="Public URL prefix where slim JSONs are served (for the manifest). "
                         "Defaults to base-url's parent + /lessons/slim")
    a=ap.parse_args()
    files=[f for f in glob.glob(os.path.join(a.lessons_dir,"*.json"))]
    if not files:
        print("No lessons found in", a.lessons_dir); return
    print(f"Converting {len(files)} lesson(s)...")
    manifest=[]
    for f in sorted(files):
        code, title, slim_name = convert_one(f, a.repo, a.base_url, a.slim_dir)
        manifest.append({"code":code, "title":title, "slim":slim_name})
    # write the manifest the Studio picker reads
    manifest.sort(key=lambda m: m["code"])
    slim_url = a.slim_url or (a.base_url.rstrip("/").rsplit("/assets",1)[0] + "/lessons/slim")
    out = {"slimBase": slim_url.rstrip("/"), "lessons": manifest}
    manifest_path = os.path.join(a.slim_dir, "index.json")
    json.dump(out, open(manifest_path,"w",encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"manifest: {len(manifest)} lesson(s) -> {manifest_path}")
    print("Done.")

if __name__=="__main__": main()
