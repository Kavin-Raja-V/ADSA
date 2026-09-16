import os, io, hashlib
from flask import Flask, request, jsonify, send_from_directory
from supabase import create_client

app = Flask(__name__, static_folder=None)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
if not SUPABASE_KEY:
    raise RuntimeError("Set SUPABASE_SECRET_KEY in Vercel environment variables.")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET = os.environ.get("SUPABASE_BUCKET", "ledger-blocks")

def sha256(data):
    return hashlib.sha256(data).hexdigest()

def hash_pair(left, right):
    return sha256((left + right).encode())

def merkle_root(hashes):
    if not hashes: return None
    level = list(hashes)
    while len(level) > 1:
        if len(level) % 2: level.append(level[-1])
        level = [hash_pair(level[i], level[i+1]) for i in range(0, len(level), 2)]
    return level[0]

def fmt(n):
    if n < 1024: return f"{n} B"
    if n < 1048576: return f"{n/1024:.1f} KB"
    return f"{n/1048576:.2f} MB"

def get_files():
    return supabase.table("ledger_files").select("*").order("created_at").execute().data

def get_blocks():
    return supabase.table("ledger_blocks").select("*").order("created_at").execute().data

@app.get("/")
def home():
    return send_from_directory(os.path.dirname(__file__), "index.html")

@app.get("/api/state")
def state():
    files = get_files()
    blocks = get_blocks()
    logical = sum(int(f["size"]) for f in files)
    physical = sum(int(b["size"]) for b in blocks)
    saved = max(logical - physical, 0)
    avl = sorted([{"path": f["path"], "height": 1} for f in files], key=lambda x: x["path"])
    return jsonify({
        "stats": {
            "files": len(files),
            "unique_blocks": len(blocks),
            "logical_size": fmt(logical),
            "physical_size": fmt(physical),
            "saved_bytes": fmt(saved),
            "saved_percent": round(saved/logical*100,1) if logical else 0
        },
        "blocks": blocks,
        "files": files,
        "avl": avl
    })

@app.post("/api/upload")
def upload():
    try:
        f = request.files.get("file")

        if not f:
            return jsonify(error="No file supplied"), 400

        data = f.read()
        chunk_size = 64 * 1024
        hashes = []

        for i in range(0, len(data), chunk_size):
            chunk = data[i:i + chunk_size]
            h = sha256(chunk)
            hashes.append(h)

            existing = (
                supabase
                .table("ledger_blocks")
                .select("hash,ref_count")
                .eq("hash", h)
                .maybe_single()
                .execute()
                .data
            )

            if existing:
                supabase.table("ledger_blocks").update({
                    "ref_count": int(existing["ref_count"]) + 1
                }).eq("hash", h).execute()

            else:
                # Upload block to Supabase Storage
                supabase.storage.from_(BUCKET).upload(
                    h,
                    chunk,
                    {
                        "content-type": "application/octet-stream",
                        "upsert": "true"
                    }
                )

                # Save block information
                supabase.table("ledger_blocks").insert({
                    "hash": h,
                    "size": len(chunk),
                    "ref_count": 1,
                    "corrupted": False
                }).execute()

        # Handle empty files
        if not hashes:
            h = sha256(b"")
            hashes = [h]

            existing = (
                supabase
                .table("ledger_blocks")
                .select("hash,ref_count")
                .eq("hash", h)
                .maybe_single()
                .execute()
                .data
            )

            if existing:
                supabase.table("ledger_blocks").update({
                    "ref_count": int(existing["ref_count"]) + 1
                }).eq("hash", h).execute()

            else:
                supabase.storage.from_(BUCKET).upload(
                    h,
                    b"",
                    {
                        "content-type": "application/octet-stream",
                        "upsert": "true"
                    }
                )

                supabase.table("ledger_blocks").insert({
                    "hash": h,
                    "size": 0,
                    "ref_count": 1,
                    "corrupted": False
                }).execute()

        count = len(get_files()) + 1

        filename = f.filename or "unnamed_file"
        safe_filename = filename.replace(" ", "_")

        path = f"/uploads/{count:02d}-{safe_filename}"

        entry = {
            "name": filename,
            "path": path,
            "size": len(data),
            "blocks": len(hashes),
            "block_hashes": hashes,
            "merkle_root": merkle_root(hashes),
            "status": "unverified"
        }

        # Save file record
        result = (
            supabase
            .table("ledger_files")
            .insert(entry)
            .execute()
        )

        return jsonify(entry)

    except Exception as e:
        print("UPLOAD ERROR:", repr(e))

        return jsonify({
            "error": "Upload failed",
            "details": str(e)
        }), 500
@app.post("/api/verify")
def verify():
    path=request.json["path"]
    e=supabase.table("ledger_files").select("*").eq("path",path).single().execute().data
    current=[]
    for h in e["block_hashes"]:
        raw=supabase.storage.from_(BUCKET).download(h)
        current.append(sha256(raw))
    root=merkle_root(current)
    ok=root==e["merkle_root"]
    supabase.table("ledger_files").update({"status":"verified intact" if ok else "tamper detected"}).eq("path",path).execute()
    return jsonify(ok=ok,path=path,recorded_root=e["merkle_root"],recomputed_root=root,
                    bad_blocks=[h for h,r in zip(e["block_hashes"],current) if h!=r])

@app.post("/api/delete")
def delete():
    path=request.json["path"]
    e=supabase.table("ledger_files").select("*").eq("path",path).single().execute().data
    for h in e["block_hashes"]:
        b=supabase.table("ledger_blocks").select("ref_count").eq("hash",h).single().execute().data
        rc=int(b["ref_count"])-1
        if rc<=0:
            supabase.storage.from_(BUCKET).remove([h])
            supabase.table("ledger_blocks").delete().eq("hash",h).execute()
        else:
            supabase.table("ledger_blocks").update({"ref_count":rc}).eq("hash",h).execute()
    supabase.table("ledger_files").delete().eq("path",path).execute()
    return jsonify(deleted=path)

@app.post("/api/corrupt")
def corrupt():
    # For the demo, corruption is performed in the database/storage using a downloaded block.
    h=request.json["hash"]
    raw=bytearray(supabase.storage.from_(BUCKET).download(h))
    if not raw:return jsonify(ok=False)
    raw[0]^=0b00010000
    supabase.storage.from_(BUCKET).remove([h])
    supabase.storage.from_(BUCKET).upload(h,bytes(raw),{"content-type":"application/octet-stream"})
    supabase.table("ledger_blocks").update({"corrupted":True}).eq("hash",h).execute()
    return jsonify(ok=True)

if __name__ == "__main__":
    app.run()
