import os
import hashlib

from flask import Flask, request, jsonify, send_from_directory
from supabase import create_client


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__, static_folder=None)


# ============================================================
# SUPABASE
# ============================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL")

SUPABASE_KEY = (
    os.environ.get("SUPABASE_SECRET_KEY")
    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
)

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is not set.")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_SECRET_KEY is not set.")

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

BUCKET = os.environ.get(
    "SUPABASE_BUCKET",
    "ledger-blocks"
)


# ============================================================
# HASHING
# ============================================================

def sha256(data):
    return hashlib.sha256(data).hexdigest()


def hash_pair(left, right):
    return sha256(
        (left + right).encode("utf-8")
    )


# ============================================================
# MERKLE TREE
# ============================================================

def merkle_root(hashes):

    if not hashes:
        return None

    level = list(hashes)

    while len(level) > 1:

        if len(level) % 2 != 0:
            level.append(level[-1])

        next_level = []

        for i in range(0, len(level), 2):

            next_level.append(
                hash_pair(
                    level[i],
                    level[i + 1]
                )
            )

        level = next_level

    return level[0]


# ============================================================
# FORMAT SIZE
# ============================================================

def fmt(n):

    n = int(n)

    if n < 1024:
        return f"{n} B"

    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"

    return f"{n / (1024 * 1024):.2f} MB"


# ============================================================
# DATABASE HELPERS
# ============================================================

def get_files():

    response = (
        supabase
        .table("ledger_files")
        .select("*")
        .order("created_at")
        .execute()
    )

    return response.data or []


def get_blocks():

    response = (
        supabase
        .table("ledger_blocks")
        .select("*")
        .order("created_at")
        .execute()
    )

    return response.data or []


def get_block(hash_value):

    response = (
        supabase
        .table("ledger_blocks")
        .select(
            "hash,ref_count,size,corrupted"
        )
        .eq("hash", hash_value)
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        return None

    return rows[0]


# ============================================================
# HOME PAGE
# ============================================================

@app.get("/")
def home():

    return send_from_directory(
        os.path.dirname(__file__),
        "index.html"
    )


# ============================================================
# STATE
# ============================================================

@app.get("/api/state")
def state():

    try:

        files = get_files()
        blocks = get_blocks()

        logical = sum(
            int(f.get("size", 0))
            for f in files
        )

        physical = sum(
            int(b.get("size", 0))
            for b in blocks
        )

        saved = max(
            logical - physical,
            0
        )

        avl = sorted(
            [
                {
                    "path": f["path"],
                    "height": 1
                }
                for f in files
            ],
            key=lambda x: x["path"]
        )

        return jsonify({

            "stats": {

                "files": len(files),

                "unique_blocks": len(blocks),

                "logical_size": fmt(logical),

                "physical_size": fmt(physical),

                "saved_bytes": fmt(saved),

                "saved_percent":
                    round(
                        saved / logical * 100,
                        1
                    )
                    if logical
                    else 0
            },

            "blocks": blocks,

            "files": files,

            "avl": avl
        })

    except Exception as e:

        print(
            "STATE ERROR:",
            repr(e)
        )

        return jsonify({
            "error": "Could not load state",
            "details": str(e)
        }), 500


# ============================================================
# UPLOAD
# ============================================================

@app.post("/api/upload")
def upload():

    try:

        # ------------------------------------------------------
        # GET FILE
        # ------------------------------------------------------

        f = request.files.get("file")

        if not f:

            return jsonify({
                "error": "No file supplied"
            }), 400

        filename = f.filename or "unnamed_file"

        safe_filename = filename.replace(
            " ",
            "_"
        )

        data = f.read()

        print(
            "UPLOAD:",
            filename,
            "SIZE:",
            len(data)
        )

        # ------------------------------------------------------
        # CHUNKING
        # ------------------------------------------------------

        CHUNK_SIZE = 64 * 1024

        hashes = []

        for i in range(
            0,
            len(data),
            CHUNK_SIZE
        ):

            chunk = data[
                i:i + CHUNK_SIZE
            ]

            # SHA-256
            h = sha256(chunk)

            hashes.append(h)

            print(
                "BLOCK HASH:",
                h
            )

            # --------------------------------------------------
            # CHECK EXISTING BLOCK
            # --------------------------------------------------

            existing = get_block(h)

            if existing:

                print(
                    "BLOCK ALREADY EXISTS"
                )

                new_ref_count = (
                    int(
                        existing["ref_count"]
                    ) + 1
                )

                (
                    supabase
                    .table("ledger_blocks")
                    .update({
                        "ref_count":
                            new_ref_count
                    })
                    .eq("hash", h)
                    .execute()
                )

            else:

                print(
                    "NEW BLOCK - UPLOADING"
                )

                # ------------------------------------------------
                # STORAGE UPLOAD
                # ------------------------------------------------

                supabase \
                    .storage \
                    .from_(BUCKET) \
                    .upload(
                        h,
                        chunk,
                        {
                            "content-type":
                                "application/octet-stream"
                        }
                    )

                print(
                    "STORAGE UPLOAD OK:",
                    h
                )

                # ------------------------------------------------
                # DATABASE RECORD
                # ------------------------------------------------

                (
                    supabase
                    .table("ledger_blocks")
                    .insert({
                        "hash": h,
                        "size": len(chunk),
                        "ref_count": 1,
                        "corrupted": False
                    })
                    .execute()
                )

                print(
                    "BLOCK DATABASE RECORD OK:",
                    h
                )

        # ======================================================
        # EMPTY FILE
        # ======================================================

        if not hashes:

            h = sha256(b"")

            hashes = [h]

            existing = get_block(h)

            if existing:

                new_ref_count = (
                    int(
                        existing["ref_count"]
                    ) + 1
                )

                (
                    supabase
                    .table("ledger_blocks")
                    .update({
                        "ref_count":
                            new_ref_count
                    })
                    .eq("hash", h)
                    .execute()
                )

            else:

                supabase \
                    .storage \
                    .from_(BUCKET) \
                    .upload(
                        h,
                        b"",
                        {
                            "content-type":
                                "application/octet-stream"
                        }
                    )

                (
                    supabase
                    .table("ledger_blocks")
                    .insert({
                        "hash": h,
                        "size": 0,
                        "ref_count": 1,
                        "corrupted": False
                    })
                    .execute()
                )

        # ======================================================
        # FILE PATH
        # ======================================================

        files = get_files()

        count = len(files) + 1

        path = (
            f"/uploads/"
            f"{count:02d}-"
            f"{safe_filename}"
        )

        # ======================================================
        # MERKLE ROOT
        # ======================================================

        root = merkle_root(hashes)

        # ======================================================
        # FILE RECORD
        # ======================================================

        entry = {

            "name": filename,

            "path": path,

            "size": len(data),

            "blocks": len(hashes),

            "block_hashes": hashes,

            "merkle_root": root,

            "status": "unverified"
        }

        (
            supabase
            .table("ledger_files")
            .insert(entry)
            .execute()
        )

        print(
            "FILE CREATED:",
            path
        )

        return jsonify(entry)

    except Exception as e:

        print(
            "================================"
        )

        print(
            "UPLOAD ERROR:",
            repr(e)
        )

        print(
            "================================"
        )

        return jsonify({

            "error": "Upload failed",

            "details": str(e)

        }), 500


# ============================================================
# VERIFY
# ============================================================

@app.post("/api/verify")
def verify():

    try:

        body = (
            request.get_json(
                silent=True
            )
            or {}
        )

        path = body.get("path")

        if not path:

            return jsonify({
                "error":
                    "File path is required"
            }), 400

        response = (
            supabase
            .table("ledger_files")
            .select("*")
            .eq("path", path)
            .limit(1)
            .execute()
        )

        rows = response.data or []

        if not rows:

            return jsonify({
                "error":
                    "File not found"
            }), 404

        entry = rows[0]

        current_hashes = []

        for h in entry["block_hashes"]:

            raw = (
                supabase
                .storage
                .from_(BUCKET)
                .download(h)
            )

            current_hashes.append(
                sha256(raw)
            )

        new_root = merkle_root(
            current_hashes
        )

        recorded_root = (
            entry["merkle_root"]
        )

        ok = (
            new_root ==
            recorded_root
        )

        bad_blocks = [

            original

            for original, current

            in zip(
                entry["block_hashes"],
                current_hashes
            )

            if original != current
        ]

        (
            supabase
            .table("ledger_files")
            .update({
                "status":
                    "verified intact"
                    if ok
                    else
                    "tamper detected"
            })
            .eq("path", path)
            .execute()
        )

        return jsonify({

            "ok": ok,

            "path": path,

            "recorded_root":
                recorded_root,

            "recomputed_root":
                new_root,

            "bad_blocks":
                bad_blocks
        })

    except Exception as e:

        print(
            "VERIFY ERROR:",
            repr(e)
        )

        return jsonify({

            "error":
                "Verification failed",

            "details":
                str(e)

        }), 500


# ============================================================
# DELETE
# ============================================================

@app.post("/api/delete")
def delete():

    try:

        body = (
            request.get_json(
                silent=True
            )
            or {}
        )

        path = body.get("path")

        if not path:

            return jsonify({
                "error":
                    "File path is required"
            }), 400

        response = (
            supabase
            .table("ledger_files")
            .select("*")
            .eq("path", path)
            .limit(1)
            .execute()
        )

        rows = response.data or []

        if not rows:

            return jsonify({
                "error":
                    "File not found"
            }), 404

        entry = rows[0]

        for h in entry["block_hashes"]:

            block = get_block(h)

            if not block:
                continue

            new_ref_count = (
                int(
                    block["ref_count"]
                ) - 1
            )

            if new_ref_count <= 0:

                # Remove Storage object
                (
                    supabase
                    .storage
                    .from_(BUCKET)
                    .remove([h])
                )

                # Remove database record
                (
                    supabase
                    .table("ledger_blocks")
                    .delete()
                    .eq("hash", h)
                    .execute()
                )

            else:

                (
                    supabase
                    .table("ledger_blocks")
                    .update({
                        "ref_count":
                            new_ref_count
                    })
                    .eq("hash", h)
                    .execute()
                )

        # Delete file
        (
            supabase
            .table("ledger_files")
            .delete()
            .eq("path", path)
            .execute()
        )

        return jsonify({
            "deleted": path
        })

    except Exception as e:

        print(
            "DELETE ERROR:",
            repr(e)
        )

        return jsonify({

            "error":
                "Delete failed",

            "details":
                str(e)

        }), 500


# ============================================================
# CORRUPT BLOCK
# ============================================================

@app.post("/api/corrupt")
def corrupt():

    try:

        body = (
            request.get_json(
                silent=True
            )
            or {}
        )

        h = body.get("hash")

        if not h:

            return jsonify({
                "error":
                    "Block hash is required"
            }), 400

        # Download block
        raw = bytearray(

            supabase
            .storage
            .from_(BUCKET)
            .download(h)

        )

        if not raw:

            return jsonify({
                "ok": False,
                "error":
                    "Block is empty"
            })

        # Change first byte
        raw[0] ^= 0b00010000

        # Remove old object
        (
            supabase
            .storage
            .from_(BUCKET)
            .remove([h])
        )

        # Upload corrupted object
        # NO UPSERT NEEDED
        (
            supabase
            .storage
            .from_(BUCKET)
            .upload(
                h,
                bytes(raw),
                {
                    "content-type":
                        "application/octet-stream"
                }
            )
        )

        # Mark corrupted
        (
            supabase
            .table("ledger_blocks")
            .update({
                "corrupted": True
            })
            .eq("hash", h)
            .execute()
        )

        return jsonify({
            "ok": True,
            "hash": h
        })

    except Exception as e:

        print(
            "CORRUPT ERROR:",
            repr(e)
        )

        return jsonify({

            "error":
                "Corruption operation failed",

            "details":
                str(e)

        }), 500


# ============================================================
# LOCAL RUN
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
        )
