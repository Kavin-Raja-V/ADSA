import os
import hashlib
import re
from datetime import datetime, timezone

from flask import Flask, jsonify, request, send_from_directory
from supabase import create_client


# =========================================================
# CONFIGURATION
# =========================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()

SUPABASE_KEY = (
    os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
)

SUPABASE_BUCKET = os.environ.get(
    "SUPABASE_BUCKET",
    "ledger-blocks"
).strip()

CHUNK_SIZE = 64 * 1024


if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Missing SUPABASE_URL and SUPABASE_SECRET_KEY "
        "(or SUPABASE_SERVICE_ROLE_KEY)"
    )


supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")


# =========================================================
# GENERAL HELPERS
# =========================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def json_error(message, status=500, details=None):
    result = {
        "error": message
    }

    if details is not None:
        result["details"] = str(details)

    return jsonify(result), status


# =========================================================
# DATABASE HELPERS
# =========================================================

def get_files():

    response = (
        supabase
        .table("ledger_files")
        .select("*")
        .order("path")
        .execute()
    )

    return response.data or []


def get_blocks():

    response = (
        supabase
        .table("ledger_blocks")
        .select("*")
        .order("hash")
        .execute()
    )

    return response.data or []


# =========================================================
# MERKLE TREE
# =========================================================

def merkle_root(hashes):

    if not hashes:
        return ""

    level = list(hashes)

    while len(level) > 1:

        next_level = []

        for i in range(0, len(level), 2):

            left = level[i]

            if i + 1 < len(level):
                right = level[i + 1]
            else:
                right = left

            combined = (
                bytes.fromhex(left)
                + bytes.fromhex(right)
            )

            parent = hashlib.sha256(
                combined
            ).hexdigest()

            next_level.append(parent)

        level = next_level

    return level[0]


# =========================================================
# FILENAME HELPERS
# =========================================================

def safe_filename(filename):

    filename = os.path.basename(
        filename or "unnamed"
    )

    filename = re.sub(
        r"[^A-Za-z0-9._-]",
        "_",
        filename
    )

    filename = filename[:180]

    return filename or "unnamed"


def next_file_path(filename):

    existing = get_files()

    used_numbers = []

    pattern = re.compile(
        r"^/uploads/(\d+)-"
    )

    for row in existing:

        path = row.get("path", "")

        match = pattern.match(path)

        if match:
            used_numbers.append(
                int(match.group(1))
            )

    number = max(
        used_numbers,
        default=0
    ) + 1

    return (
        f"/uploads/"
        f"{number:02d}-"
        f"{safe_filename(filename)}"
    )


# =========================================================
# SUPABASE STORAGE
# =========================================================

def storage_upload(
    path,
    data,
    content_type="application/octet-stream"
):

    return (
        supabase
        .storage
        .from_(SUPABASE_BUCKET)
        .upload(
            path=path,
            file=data,
            file_options={
                "content-type": str(content_type),
                "cache-control": "3600",
                "upsert": "false"
            }
        )
    )


def storage_update(
    path,
    data,
    content_type="application/octet-stream"
):

    return (
        supabase
        .storage
        .from_(SUPABASE_BUCKET)
        .update(
            path=path,
            file=data,
            file_options={
                "content-type": str(content_type),
                "cache-control": "3600",
                "upsert": "true"
            }
        )
    )


def storage_download(path):

    return (
        supabase
        .storage
        .from_(SUPABASE_BUCKET)
        .download(path)
    )


def storage_delete(path):

    return (
        supabase
        .storage
        .from_(SUPABASE_BUCKET)
        .remove([path])
    )


# =========================================================
# FRONTEND
# =========================================================

@app.route("/")
def index():

    return send_from_directory(
        FRONTEND_DIR,
        "index.html"
    )


# =========================================================
# GET STATE
# =========================================================

@app.get("/api/state")
def api_state():

    try:

        files = get_files()
        blocks = get_blocks()

        logical_bytes = sum(
            int(row.get("size") or 0)
            for row in files
        )

        unique_bytes = sum(
            int(row.get("size") or 0)
            for row in blocks
        )

        return jsonify({

            "stats": {

                "files": len(files),

                "blocks": len(blocks),

                "logical_bytes": logical_bytes,

                "unique_bytes": unique_bytes,

                "dedup_saved": max(
                    logical_bytes - unique_bytes,
                    0
                )
            },

            "files": files,

            "blocks": blocks,

            "avl": [
                row.get("path", "")
                for row in files
            ]
        })

    except Exception as exc:

        return json_error(
            "Could not load Ledger state",
            details=exc
        )


# =========================================================
# UPLOAD
# =========================================================

@app.post("/api/upload")
def api_upload():

    try:

        uploaded = request.files.get("file")

        if uploaded is None:

            return json_error(
                "No file received. Expected form field 'file'.",
                400
            )

        original_name = (
            uploaded.filename
            or "unnamed"
        )

        content_type = (
            uploaded.mimetype
            or "application/octet-stream"
        )

        raw = uploaded.read()

        if raw is None:
            raw = b""

        block_hashes = []

        offset = 0

        # -------------------------------------------------
        # Split file into 64 KB blocks
        # -------------------------------------------------

        while offset < len(raw):

            block = raw[
                offset:
                offset + CHUNK_SIZE
            ]

            offset += CHUNK_SIZE

            block_hash = hashlib.sha256(
                block
            ).hexdigest()

            block_hashes.append(
                block_hash
            )

            # Check whether block already exists
            existing_response = (
                supabase
                .table("ledger_blocks")
                .select("*")
                .eq("hash", block_hash)
                .limit(1)
                .execute()
            )

            if existing_response.data:

                existing = (
                    existing_response.data[0]
                )

                new_ref_count = (
                    int(
                        existing.get(
                            "ref_count"
                        ) or 0
                    )
                    + 1
                )

                (
                    supabase
                    .table("ledger_blocks")
                    .update({
                        "ref_count": new_ref_count,
                        "corrupted": False
                    })
                    .eq(
                        "hash",
                        block_hash
                    )
                    .execute()
                )

            else:

                storage_path = (
                    f"blocks/{block_hash}"
                )

                # Store the new content block
                storage_upload(
                    storage_path,
                    block,
                    "application/octet-stream"
                )

                # Store metadata in PostgreSQL
                (
                    supabase
                    .table("ledger_blocks")
                    .insert({
                        "hash": block_hash,
                        "size": len(block),
                        "ref_count": 1,
                        "corrupted": False,
                        "created_at": now_iso()
                    })
                    .execute()
                )

        # -------------------------------------------------
        # Create Ledger file entry
        # -------------------------------------------------

        file_path = next_file_path(
            original_name
        )

        root = merkle_root(
            block_hashes
        )

        (
            supabase
            .table("ledger_files")
            .insert({
                "path": file_path,
                "name": original_name,
                "size": len(raw),
                "blocks": len(block_hashes),
                "block_hashes": block_hashes,
                "merkle_root": root,
                "status": "INTACT",
                "created_at": now_iso()
            })
            .execute()
        )

        return jsonify({

            "ok": True,

            "path": file_path,

            "name": original_name,

            "size": len(raw),

            "blocks": len(block_hashes),

            "merkle_root": root
        })

    except Exception as exc:

        return json_error(
            "Upload failed",
            details=exc
        )


# =========================================================
# VERIFY
# =========================================================

@app.post("/api/verify")
def api_verify():

    try:

        body = (
            request.get_json(
                silent=True
            )
            or {}
        )

        path = body.get("path")

        if not path:

            return json_error(
                "Missing file path",
                400
            )

        rows = (
            supabase
            .table("ledger_files")
            .select("*")
            .eq("path", path)
            .limit(1)
            .execute()
        )

        if not rows.data:

            return json_error(
                "File not found",
                404
            )

        file_row = rows.data[0]

        hashes = (
            file_row.get(
                "block_hashes"
            )
            or []
        )

        verified_hashes = []

        corrupted = False

        for block_hash in hashes:

            try:

                data = storage_download(
                    f"blocks/{block_hash}"
                )

            except Exception:

                corrupted = True

                break

            actual_hash = hashlib.sha256(
                data
            ).hexdigest()

            verified_hashes.append(
                actual_hash
            )

            if actual_hash != block_hash:

                corrupted = True

                break

        calculated_root = ""

        if (
            not corrupted
            and len(verified_hashes)
            == len(hashes)
        ):

            calculated_root = merkle_root(
                verified_hashes
            )

        intact = (
            not corrupted
            and len(verified_hashes)
            == len(hashes)
            and calculated_root
            == file_row.get(
                "merkle_root"
            )
        )

        status = (
            "INTACT"
            if intact
            else "CORRUPTED"
        )

        (
            supabase
            .table("ledger_files")
            .update({
                "status": status
            })
            .eq(
                "path",
                path
            )
            .execute()
        )

        return jsonify({

            "ok": intact,

            "path": path,

            "status": status,

            "expected_merkle_root":
                file_row.get(
                    "merkle_root"
                ),

            "calculated_merkle_root":
                calculated_root
        })

    except Exception as exc:

        return json_error(
            "Verification failed",
            details=exc
        )


# =========================================================
# DELETE
# =========================================================

@app.post("/api/delete")
def api_delete():

    try:

        body = (
            request.get_json(
                silent=True
            )
            or {}
        )

        path = body.get("path")

        if not path:

            return json_error(
                "Missing file path",
                400
            )

        rows = (
            supabase
            .table("ledger_files")
            .select("*")
            .eq("path", path)
            .limit(1)
            .execute()
        )

        if not rows.data:

            return json_error(
                "File not found",
                404
            )

        file_row = rows.data[0]

        hashes = (
            file_row.get(
                "block_hashes"
            )
            or []
        )

        for block_hash in hashes:

            block_rows = (
                supabase
                .table("ledger_blocks")
                .select("*")
                .eq(
                    "hash",
                    block_hash
                )
                .limit(1)
                .execute()
            )

            if not block_rows.data:
                continue

            block_row = block_rows.data[0]

            ref_count = int(
                block_row.get(
                    "ref_count"
                )
                or 0
            )

            if ref_count <= 1:

                storage_delete(
                    f"blocks/{block_hash}"
                )

                (
                    supabase
                    .table("ledger_blocks")
                    .delete()
                    .eq(
                        "hash",
                        block_hash
                    )
                    .execute()
                )

            else:

                (
                    supabase
                    .table("ledger_blocks")
                    .update({
                        "ref_count":
                            ref_count - 1
                    })
                    .eq(
                        "hash",
                        block_hash
                    )
                    .execute()
                )

        (
            supabase
            .table("ledger_files")
            .delete()
            .eq(
                "path",
                path
            )
            .execute()
        )

        return jsonify({
            "ok": True,
            "path": path
        })

    except Exception as exc:

        return json_error(
            "Delete failed",
            details=exc
        )


# =========================================================
# CORRUPT BLOCK
# =========================================================

@app.post("/api/corrupt")
def api_corrupt():

    try:

        body = (
            request.get_json(
                silent=True
            )
            or {}
        )

        block_hash = body.get(
            "hash"
        )

        if not block_hash:

            return json_error(
                "Missing block hash",
                400
            )

        data = storage_download(
            f"blocks/{block_hash}"
        )

        if not data:

            data = b"\x00"

        else:

            corrupted = bytearray(
                data
            )

            corrupted[0] ^= 0xFF

            data = bytes(
                corrupted
            )

        storage_update(
            f"blocks/{block_hash}",
            data,
            "application/octet-stream"
        )

        (
            supabase
            .table("ledger_blocks")
            .update({
                "corrupted": True
            })
            .eq(
                "hash",
                block_hash
            )
            .execute()
        )

        return jsonify({

            "ok": True,

            "hash": block_hash,

            "message":
                "Block corrupted successfully"
        })

    except Exception as exc:

        return json_error(
            "Corruption test failed",
            details=exc
        )


# =========================================================
# LOCAL DEVELOPMENT
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                5000
            )
        )
      )
