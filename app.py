import os
import hashlib

from flask import Flask, request, jsonify, send_from_directory
from supabase import create_client


# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__, static_folder=None)


# ============================================================
# SUPABASE CONFIGURATION
# ============================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL")

SUPABASE_KEY = (
    os.environ.get("SUPABASE_SECRET_KEY")
    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
)

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is not set in Vercel environment variables.")

if not SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_SECRET_KEY is not set in Vercel environment variables."
    )

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

BUCKET = os.environ.get("SUPABASE_BUCKET", "ledger-blocks")


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def sha256(data):
    """Return SHA-256 hash of bytes."""
    return hashlib.sha256(data).hexdigest()


def hash_pair(left, right):
    """Hash two Merkle tree nodes together."""
    return sha256((left + right).encode("utf-8"))


def merkle_root(hashes):
    """Calculate Merkle root from a list of block hashes."""

    if not hashes:
        return None

    level = list(hashes)

    while len(level) > 1:

        # Duplicate last node when number of nodes is odd
        if len(level) % 2 != 0:
            level.append(level[-1])

        next_level = []

        for i in range(0, len(level), 2):
            next_level.append(
                hash_pair(level[i], level[i + 1])
            )

        level = next_level

    return level[0]


def fmt(n):
    """Format bytes into readable units."""

    n = int(n)

    if n < 1024:
        return f"{n} B"

    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"

    return f"{n / (1024 * 1024):.2f} MB"


def get_files():
    """Get all files from ledger_files."""

    response = (
        supabase
        .table("ledger_files")
        .select("*")
        .order("created_at")
        .execute()
    )

    return response.data or []


def get_blocks():
    """Get all blocks from ledger_blocks."""

    response = (
        supabase
        .table("ledger_blocks")
        .select("*")
        .order("created_at")
        .execute()
    )

    return response.data or []


def get_block(hash_value):
    """
    Find a block by SHA-256 hash.

    This intentionally does NOT use maybe_single(),
    because the deployed Supabase client previously
    produced a NoneType error with that method.
    """

    response = (
        supabase
        .table("ledger_blocks")
        .select("hash,ref_count,size,corrupted")
        .eq("hash", hash_value)
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if len(rows) == 0:
        return None

    return rows[0]


# ============================================================
# HOME PAGE
# ============================================================

@app.get("/")
def home():
    """
    Serve index.html from the repository root.

    Your GitHub structure currently has index.html
    beside app.py, so we serve it directly from here.
    """

    return send_from_directory(
        os.path.dirname(__file__),
        "index.html"
    )


# ============================================================
# STATE API
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

        saved = max(logical - physical, 0)

        # Simple representation of the namespace
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
                "saved_percent": (
                    round(saved / logical * 100, 1)
                    if logical
                    else 0
                )
            },

            "blocks": blocks,
            "files": files,
            "avl": avl
        })

    except Exception as e:

        print("STATE ERROR:", repr(e))

        return jsonify({
            "error": "Could not load Ledger state",
            "details": str(e)
        }), 500


# ============================================================
# UPLOAD API
# ============================================================

@app.post("/api/upload")
def upload():

    try:

        # ----------------------------------------------------
        # Get uploaded file
        # ----------------------------------------------------

        f = request.files.get("file")

        if not f:
            return jsonify({
                "error": "No file supplied"
            }), 400

        filename = f.filename or "unnamed_file"

        # Replace spaces in filename
        safe_filename = filename.replace(" ", "_")

        # Read file
        data = f.read()

        print(
            f"UPLOAD START: {filename} "
            f"({len(data)} bytes)"
        )

        # ----------------------------------------------------
        # Split file into 64 KB blocks
        # ----------------------------------------------------

        CHUNK_SIZE = 64 * 1024

        hashes = []

        for i in range(0, len(data), CHUNK_SIZE):

            chunk = data[i:i + CHUNK_SIZE]

            # Calculate content hash
            h = sha256(chunk)

            hashes.append(h)

            print(
                f"BLOCK: {h} "
                f"({len(chunk)} bytes)"
            )

            # ------------------------------------------------
            # Check whether block already exists
            # ------------------------------------------------

            existing = get_block(h)

            if existing:

                print(
                    f"BLOCK EXISTS: {h}, "
                    f"ref_count={existing['ref_count']}"
                )

                # Increase reference count
                new_ref_count = (
                    int(existing["ref_count"]) + 1
                )

                supabase \
                    .table("ledger_blocks") \
                    .update({
                        "ref_count": new_ref_count
                    }) \
                    .eq("hash", h) \
                    .execute()

            else:

                print(
                    f"NEW BLOCK: uploading {h} "
                    f"to Supabase Storage"
                )

                # ------------------------------------------------
                # Upload block to Supabase Storage
                # ------------------------------------------------

                supabase \
                    .storage \
                    .from_(BUCKET) \
                    .upload(
                        h,
                        chunk,
                        {
                            "content-type":
                                "application/octet-stream",
                            "upsert": "true"
                        }
                    )

                print(
                    f"STORAGE UPLOAD SUCCESS: {h}"
                )

                # ------------------------------------------------
                # Insert block metadata into database
                # ------------------------------------------------

                supabase \
                    .table("ledger_blocks") \
                    .insert({
                        "hash": h,
                        "size": len(chunk),
                        "ref_count": 1,
                        "corrupted": False
                    }) \
                    .execute()

                print(
                    f"DATABASE INSERT SUCCESS: {h}"
                )

        # ----------------------------------------------------
        # Handle empty files
        # ----------------------------------------------------

        if not hashes:

            print("EMPTY FILE")

            h = sha256(b"")

            hashes = [h]

            existing = get_block(h)

            if existing:

                new_ref_count = (
                    int(existing["ref_count"]) + 1
                )

                supabase \
                    .table("ledger_blocks") \
                    .update({
                        "ref_count": new_ref_count
                    }) \
                    .eq("hash", h) \
                    .execute()

            else:

                supabase \
                    .storage \
                    .from_(BUCKET) \
                    .upload(
                        h,
                        b"",
                        {
                            "content-type":
                                "application/octet-stream",
                            "upsert": True
                        }
                    )

                supabase \
                    .table("ledger_blocks") \
                    .insert({
                        "hash": h,
                        "size": 0,
                        "ref_count": 1,
                        "corrupted": False
                    }) \
                    .execute()

        # ----------------------------------------------------
        # Generate file path
        # ----------------------------------------------------

        current_files = get_files()

        count = len(current_files) + 1

        path = (
            f"/uploads/"
            f"{count:02d}-"
            f"{safe_filename}"
        )

        # ----------------------------------------------------
        # Calculate Merkle root
        # ----------------------------------------------------

        root = merkle_root(hashes)

        # ----------------------------------------------------
        # Create file record
        # ----------------------------------------------------

        entry = {
            "name": filename,
            "path": path,
            "size": len(data),
            "blocks": len(hashes),
            "block_hashes": hashes,
            "merkle_root": root,
            "status": "unverified"
        }

        # ----------------------------------------------------
        # Insert file record
        # ----------------------------------------------------

        result = (
            supabase
            .table("ledger_files")
            .insert(entry)
            .execute()
        )

        print(
            f"FILE INSERT SUCCESS: {path}"
        )

        return jsonify(entry), 200

    except Exception as e:

        print(
            "========================================"
        )

        print(
            "UPLOAD ERROR:",
            repr(e)
        )

        print(
            "========================================"
        )

        return jsonify({
            "error": "Upload failed",
            "details": str(e)
        }), 500


# ============================================================
# VERIFY API
# ============================================================

@app.post("/api/verify")
def verify():

    try:

        body = request.get_json(silent=True) or {}

        path = body.get("path")

        if not path:
            return jsonify({
                "error": "File path is required"
            }), 400

        # Find file
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
                "error": "File not found"
            }), 404

        entry = rows[0]

        current = []

        # Recalculate every block hash
        for h in entry["block_hashes"]:

            raw = (
                supabase
                .storage
                .from_(BUCKET)
                .download(h)
            )

            current.append(
                sha256(raw)
            )

        # Recalculate Merkle root
        root = merkle_root(current)

        recorded_root = entry["merkle_root"]

        ok = root == recorded_root

        # Find corrupted blocks
        bad_blocks = [
            original_hash
            for original_hash, recomputed_hash
            in zip(entry["block_hashes"], current)
            if original_hash != recomputed_hash
        ]

        # Update status
        supabase \
            .table("ledger_files") \
            .update({
                "status":
                    "verified intact"
                    if ok
                    else "tamper detected"
            }) \
            .eq("path", path) \
            .execute()

        return jsonify({
            "ok": ok,
            "path": path,
            "recorded_root": recorded_root,
            "recomputed_root": root,
            "bad_blocks": bad_blocks
        })

    except Exception as e:

        print(
            "VERIFY ERROR:",
            repr(e)
        )

        return jsonify({
            "error": "Verification failed",
            "details": str(e)
        }), 500


# ============================================================
# DELETE API
# ============================================================

@app.post("/api/delete")
def delete():

    try:

        body = request.get_json(silent=True) or {}

        path = body.get("path")

        if not path:
            return jsonify({
                "error": "File path is required"
            }), 400

        # Find file
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
                "error": "File not found"
            }), 404

        entry = rows[0]

        # Process every block
        for h in entry["block_hashes"]:

            block = get_block(h)

            if not block:
                continue

            new_ref_count = (
                int(block["ref_count"]) - 1
            )

            if new_ref_count <= 0:

                # Remove from Storage
                try:

                    supabase \
                        .storage \
                        .from_(BUCKET) \
                        .remove([h])

                except Exception as storage_error:

                    print(
                        "STORAGE DELETE WARNING:",
                        repr(storage_error)
                    )

                # Remove database record
                supabase \
                    .table("ledger_blocks") \
                    .delete() \
                    .eq("hash", h) \
                    .execute()

            else:

                # Decrease reference count
                supabase \
                    .table("ledger_blocks") \
                    .update({
                        "ref_count": new_ref_count
                    }) \
                    .eq("hash", h) \
                    .execute()

        # Delete file record
        supabase \
            .table("ledger_files") \
            .delete() \
            .eq("path", path) \
            .execute()

        return jsonify({
            "deleted": path
        })

    except Exception as e:

        print(
            "DELETE ERROR:",
            repr(e)
        )

        return jsonify({
            "error": "Delete failed",
            "details": str(e)
        }), 500


# ============================================================
# CORRUPT API
# ============================================================

@app.post("/api/corrupt")
def corrupt():

    try:

        body = request.get_json(silent=True) or {}

        h = body.get("hash")

        if not h:
            return jsonify({
                "error": "Block hash is required"
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
                "error": "Block is empty"
            })

        # Modify one byte
        raw[0] ^= 0b00010000

        # Remove old object
        supabase \
            .storage \
            .from_(BUCKET) \
            .remove([h])

        # Upload corrupted object
        supabase \
            .storage \
            .from_(BUCKET) \
            .upload(
                h,
                bytes(raw),
                {
                    "content-type":
                        "application/octet-stream",
                    "upsert": True
                }
            )

        # Mark block corrupted
        supabase \
            .table("ledger_blocks") \
            .update({
                "corrupted": True
            }) \
            .eq("hash", h) \
            .execute()

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
            "error": "Corruption operation failed",
            "details": str(e)
        }), 500


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
        )
