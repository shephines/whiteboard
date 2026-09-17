#!/usr/bin/env python3
"""
Batch lesson converter for the GitHub Action.
Processes every heavy lesson JSON in --lessons-dir, extracting inline images and
model HTML into <repo>/assets/<code>/, and writing slim JSONs to --slim-dir.

Extraction is RECURSIVE. The original version only looked at fixed top-level item
fields (src / img / html), so anything nested was silently left inline — which is
what happened to the "image sequence" item, whose frames live in
it["frames"][i]["src"]. The walker below descends the whole item, so any current or
future nested media is picked up without another change here.

Naming is stable and content-hashed:
  - top-level item fields keep their historic names:  <id>-src-<hash>.png
  - image-sequence frames:                            <id>-frame1-<hash>.png
  - anything else nested:                             <id>-<path>-<hash>.png
Unchanged assets therefore hash to the same filename and git sees no diff; edited
assets get a new name; anything left behind is reported as an orphan.
"""
import json, os, sys, hashlib, base64, re, argparse, urllib.parse, glob

def content_hash(s): return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]

def data_uri_ext(uri):
    m = re.match(r"data:image/([a-zA-Z0-9.+-]+)[;,]", uri)
    if not m: return "bin"
    return {"jpeg":"jpg","svg+xml":"svg"}.get(m.group(1).lower(), m.group(1).lower())

def decode_data_uri(uri):
    header, payload = uri.split(",", 1)
    if ";base64" in header:
        # validate=True so a malformed URI is reported instead of silently decoding to
        # garbage and writing a corrupt image nobody notices until it fails to load.
        # Real data URIs sometimes carry line breaks, so strip whitespace first.
        return base64.b64decode(re.sub(r"\s+", "", payload), validate=True)
    return urllib.parse.unquote(payload).encode("utf-8")

def is_data_image(v):
    return isinstance(v, str) and v.startswith("data:image/")

def is_model_html(s):
    if not isinstance(s, str): return False
    head = s[:200].lower()
    return head.strip().startswith("<!doctype") or "<html" in head

def code_to_folder(code):
    # URL-safe slug: any run of non [A-Za-z0-9-] becomes a single "_", trim edges.
    s = re.sub(r"[^A-Za-z0-9-]+", "_", str(code or "lesson"))
    return s.strip("_") or "lesson"

def safe_part(s):
    """Filename-safe fragment for the path label."""
    return re.sub(r"[^A-Za-z0-9]+", "", str(s)) or "x"


class Extractor:
    """Walks an item, swapping inline media for URLs and writing the files out."""

    # field -> the key its URL goes in (the Studio reads src||srcUrl, img||imgUrl)
    URL_FIELD = {"src": "srcUrl", "img": "imgUrl", "html": "htmlUrl"}

    def __init__(self, assets_dir, base, existing):
        self.assets_dir = assets_dir
        self.base = base
        self.existing = existing
        self.wanted = set()
        self.n_img = 0
        self.n_model = 0
        self.errors = []

    def _write(self, fn, data, binary=True):
        self.wanted.add(fn)
        if fn in self.existing:
            return                              # identical content already on disk
        path = os.path.join(self.assets_dir, fn)
        if binary:
            open(path, "wb").write(data)
        else:
            open(path, "w", encoding="utf-8").write(data)

    def _label(self, trail):
        """Filename middle section, derived from where the value was found."""
        if not trail:
            return "asset"
        # a frame of an image sequence reads far better as "frame3" than "frames-2-src"
        if len(trail) >= 3 and trail[0] == "frames" and isinstance(trail[1], int):
            return "frame%d" % (trail[1] + 1)
        return "-".join(safe_part(t) for t in trail)

    def walk(self, node, iid, trail=()):
        if isinstance(node, dict):
            for key in list(node.keys()):
                val = node.get(key)
                if is_data_image(val):
                    self._take_image(node, key, iid, trail + (key,))
                elif key == "html" and is_model_html(val):
                    self._take_model(node, iid)
                elif isinstance(val, (dict, list)):
                    self.walk(val, iid, trail + (key,))
        elif isinstance(node, list):
            for i, val in enumerate(node):
                if isinstance(val, (dict, list)):
                    self.walk(val, iid, trail + (i,))
                elif is_data_image(val):
                    self._take_list_image(node, i, iid, trail + (i,))

    def _take_image(self, holder, key, iid, trail):
        uri = holder[key]
        try:
            raw = decode_data_uri(uri)
            fn = "%s-%s-%s.%s" % (iid, self._label(trail), content_hash(uri), data_uri_ext(uri))
            self._write(fn, raw)
            holder[self.URL_FIELD.get(key, key + "Url")] = "%s/%s" % (self.base, fn)
            del holder[key]
            self.n_img += 1
        except Exception as e:
            self.errors.append("%s.%s: %s" % (iid, "/".join(str(t) for t in trail), e))

    def _take_list_image(self, lst, i, iid, trail):
        uri = lst[i]
        try:
            raw = decode_data_uri(uri)
            fn = "%s-%s-%s.%s" % (iid, self._label(trail), content_hash(uri), data_uri_ext(uri))
            self._write(fn, raw)
            lst[i] = "%s/%s" % (self.base, fn)
            self.n_img += 1
        except Exception as e:
            self.errors.append("%s[%d]: %s" % (iid, i, e))

    def _take_model(self, holder, iid):
        html = holder["html"]
        try:
            fn = "%s-model-%s.html" % (iid, content_hash(html))
            self._write(fn, html, binary=False)
            holder["htmlUrl"] = "%s/%s" % (self.base, fn)
            del holder["html"]
            self.n_model += 1
        except Exception as e:
            self.errors.append("%s.html: %s" % (iid, e))


def convert_one(path, repo, base_url, slim_dir):
    d = json.load(open(path, encoding="utf-8"))
    code = code_to_folder(d.get("code"))
    base = base_url.rstrip("/") + "/" + code
    assets_dir = os.path.join(repo, "assets", code)
    os.makedirs(assets_dir, exist_ok=True)
    existing = set(os.listdir(assets_dir)) if os.path.isdir(assets_dir) else set()

    ex = Extractor(assets_dir, base, existing)
    for ch in d.get("chunks", []):
        for it in ch.get("items", []):
            ex.walk(it, it.get("id", "item"))

    os.makedirs(slim_dir, exist_ok=True)
    slim_name = "%s_slim.json" % code
    slim_path = os.path.join(slim_dir, slim_name)
    blob = json.dumps(d, ensure_ascii=False)
    open(slim_path, "w", encoding="utf-8").write(blob)

    size = len(blob)
    print("[%s] imgs=%d models=%d slim=%.1fKB -> %s"
          % (d.get("code"), ex.n_img, ex.n_model, size / 1024, slim_path))
    for e in ex.errors:
        print("  ! %s" % e)

    # A slim lesson should be small. If media is still embedded the Studio will choke
    # on it, so say so loudly rather than publishing a silent dud.
    leftovers = blob.count("data:image/")
    if leftovers:
        print("  !! %d inline image(s) survived extraction in %s" % (leftovers, slim_name))
    if size > 2 * 1024 * 1024:
        print("  !! slim JSON is %.1fMB — something heavy is still embedded." % (size / 1048576.0))

    orphans = sorted(existing - ex.wanted)
    if orphans:
        print("  ⚠ orphaned in assets/%s/ (edit/delete left these unused):" % code)
        for o in orphans:
            print("      %s" % o)

    return code, (d.get("title") or d.get("code") or code), slim_name, ex.errors, leftovers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lessons-dir", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--slim-dir", required=True)
    ap.add_argument("--slim-url", default=None,
                    help="Public URL prefix where slim JSONs are served (for the manifest). "
                         "Defaults to base-url's parent + /lessons/slim")
    ap.add_argument("--strict", action="store_true",
                    help="Exit non-zero if any lesson failed to extract cleanly.")
    a = ap.parse_args()

    files = glob.glob(os.path.join(a.lessons_dir, "*.json"))
    if not files:
        print("No lessons found in", a.lessons_dir)
        return 0

    print("Converting %d lesson(s)..." % len(files))
    manifest = []
    failed = 0
    for f in sorted(files):
        try:
            code, title, slim_name, errors, leftovers = convert_one(f, a.repo, a.base_url, a.slim_dir)
        except Exception as e:
            # one malformed lesson must not stop the others from publishing
            print("  !! %s failed to convert: %s" % (os.path.basename(f), e))
            failed += 1
            continue
        if errors or leftovers:
            failed += 1
        manifest.append({"code": code, "title": title, "slim": slim_name})

    # write the manifest the Studio picker reads
    manifest.sort(key=lambda m: m["code"])
    slim_url = a.slim_url or (a.base_url.rstrip("/").rsplit("/assets", 1)[0] + "/lessons/slim")
    out = {"slimBase": slim_url.rstrip("/"), "lessons": manifest}
    manifest_path = os.path.join(a.slim_dir, "index.json")
    json.dump(out, open(manifest_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("manifest: %d lesson(s) -> %s" % (len(manifest), manifest_path))

    if failed:
        print("Done, with %d lesson(s) needing attention." % failed)
        return 1 if a.strict else 0
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
