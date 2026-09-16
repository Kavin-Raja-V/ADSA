import os
import hashlib
import requests

from flask import Flask, request, jsonify, send_from_directory
from supabase import create_client


# ============================================================
# FLASK
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

BUCKET = os.environ.get(
    "SUPABASE_BUCKET",
    "ledger-blocks"
)


if not SUPABASE_URL:
    raise RuntimeError(
        "SUPABASE_URL is missing."
    )


if not SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_SECRET_KEY is missing."
    )


# Database client
supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# ============================================================
# SUPABASE STORAGE HTTP HELPERS
# ============================================================

STORAGE_URL = (
    SUPABASE_URL.rstrip("/")
    + "/storage/v1/object/"
    + BUCKET
)


def storage_headers(content_type="application/octet-stream"):
    """
    Headers used for direct Supabase Storage requests.

    The secret key stays server-side and is NEVER sent
    to the browser.
    """

    return {
        "apikey": str(SUPABASE_KEY),
        "Authorization": "Bearer " + str(SUPABASE_KEY),
        "Content-Type": str(content_type),
        "x-upsert": "false"
    }


def storage_upload(path, data, content_type="application/octet-stream"):
    """
    Upload raw bytes directly to Supabase Storage.

    This avoids the Python SDK header/upsert issue.
    """

    url = (
        STORAGE_URL.rstrip("/")
        + "/"
        + path
    )

    response = requests.post(
        url,
        headers=storage_headers(
            content_type
        ),
        data=data,
        timeout=120
    )

    if response.status_code >= 400:

        raise RuntimeError(
            "Supabase Storage upload failed "
            f"({response.status_code}): "
            f"{response.text}"
        )

    return response


def storage_download(path):
    """
    Download raw bytes from Supabase Storage.
    """

    url = (
        STORAGE_URL.rstrip("/")
        + "/"
        + path
    )

    headers = {
        "apikey": str(SUPABASE_KEY),
        "Authorization": "Bearer " + str(SUPABASE_KEY)
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=120
    )

    if response.status_code >= 400:

        raise RuntimeError(
            "Supabase Storage download failed "
            f"({response.status_code}): "
            f"{response.text}"
        )

    return response.content


def storage_delete(path):
    """
    Delete an object from Supabase Storage.
    """

    url = (
        STORAGE_URL.rstrip("/")
        + "/"
        + path
    )

    headers = {
        "apikey": str(SUPABASE_KEY),
        "Authorization": "Bearer " + str(SUPABASE_KEY)
    }

    response = requests.delete(
        url,
        headers=headers,
        timeout=120
    )

    if response.status_code >= 400:

        raise RuntimeError(
            "Supabase Storage delete failed "
            f"({response.status_code}): "
            f"{response.text}"
        )

    return response


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

            level.append(
                level[-1]
            )

        next_level = []

        for i in range(
            0,
            len(level),
            2
        ):

            next_level.append(
                hash_pair(
                    level[i],
                    level[i + 1]
                )
            )

        level = next_level

    return level[0]


# ============================================================
# FORMATTING
# ============================================================

def fmt(n):

    n = int(n or 0)

    if n < 1024:
        return f"{n} B"

    if n < 1048576:
        return f"{n / 1024:.1f} KB"

    return f"{n / 1048576:.2f} MB"


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


# ============================================================
# HOME PAGE
# ============================================================

@app.get("/")
def home():

    frontend_dir = os.path.join(
        os.path.dirname(__file__),
        "frontend"
    )

    return send_from_directory(
        frontend_dir,
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
            int(f.get("size") or 0)
            for f in files
        )

        physical = sum(
            int(b.get("size") or 0)
            for b in blocks
        )

        saved = max(
            logical - physical,
            0
        )

        # Real AVL tree is reconstructed
        # by the frontend from the stored paths.

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

                "files":
                    len(files),

                "unique_blocks":
                    len(blocks),

                "logical_size":
                    fmt(logical),

                "physical_size":
                    fmt(physical),

                "saved_bytes":
                    fmt(saved),

                "saved_percent":
                    round(
                        saved / logical * 100,
                        1
                    )
                    if logical
                    else 0
            },

            "blocks":
                blocks,

            "files":
                files,

            "avl":
                avl

        })

    except Exception as e:

        return jsonify({
            "error": "State loading failed",
            "details": str(e)
        }), 500


# ============================================================
# UPLOAD
# ============================================================

@app.post("/api/upload")
def upload():

    try:

        # ----------------------------------------------------
        # GET FILE
        # ----------------------------------------------------

        uploaded_file = request.files.get(
            "file"
        )

        if uploaded_file is None:

            return jsonify({
                "error":
                    "No file supplied"
            }), 400


        filename = (
            uploaded_file.filename
            or "unnamed_file"
        )


        # ----------------------------------------------------
        # READ FILE
        # ----------------------------------------------------

        data = uploaded_file.read()

        total_size = len(data)


        # ----------------------------------------------------
        # CHUNKING
        # ----------------------------------------------------

        chunk_size = 64 * 1024

        block_hashes = []


        # ----------------------------------------------------
        # PROCESS BLOCKS
        # ----------------------------------------------------

        for start in range(
            0,
            total_size,
            chunk_size
        ):

            chunk = data[
                start:
                start + chunk_size
            ]


            # SHA-256
            block_hash = sha256(
                chunk
            )


            block_hashes.append(
                block_hash
            )


            # ------------------------------------------------
            # CHECK IF BLOCK ALREADY EXISTS
            # ------------------------------------------------

            existing_response = (
                supabase
                .table("ledger_blocks")
                .select(
                    "hash,ref_count"
                )
                .eq(
                    "hash",
                    block_hash
                )
                .limit(1)
                .execute()
            )


            existing_rows = (
                existing_response.data
                or []
            )


            if existing_rows:

                existing =
                    existing_rows[0]

                old_ref_count = int(
                    existing.get(
                        "ref_count",
                        0
                    )
                    or 0
                )


                # Same content already stored.
                # Increase reference count.

                (
                    supabase
                    .table("ledger_blocks")
                    .update({
                        "ref_count":
                            old_ref_count + 1
                    })
                    .eq(
                        "hash",
                        block_hash
                    )
                    .execute()
                )


            else:

                # ------------------------------------------------
                # NEW BLOCK
                # ------------------------------------------------

                storage_upload(
                    block_hash,
                    chunk,
                    "application/octet-stream"
                )


                (
                    supabase
                    .table("ledger_blocks")
                    .insert({

                        "hash":
                            block_hash,

                        "size":
                            len(chunk),

                        "ref_count":
                            1,

                        "corrupted":
                            False

                    })
                    .execute()
                )


        # ----------------------------------------------------
        # EMPTY FILE
        # ----------------------------------------------------

        if not block_hashes:

            empty_hash = sha256(
                b""
            )

            block_hashes = [
                empty_hash
            ]


            existing_response = (
                supabase
                .table("ledger_blocks")
                .select(
                    "hash,ref_count"
                )
                .eq(
                    "hash",
                    empty_hash
                )
                .limit(1)
                .execute()
            )


            existing_rows = (
                existing_response.data
                or []
            )


            if existing_rows:

                old_ref_count = int(
                    existing_rows[0].get(
                        "ref_count",
                        0
                    )
                    or 0
                )


                (
                    supabase
                    .table("ledger_blocks")
                    .update({
                        "ref_count":
                            old_ref_count + 1
                    })
                    .eq(
                        "hash",
                        empty_hash
                    )
                    .execute()
                )


            else:

                storage_upload(
                    empty_hash,
                    b"",
                    "application/octet-stream"
                )


                (
                    supabase
                    .table("ledger_blocks")
                    .insert({

                        "hash":
                            empty_hash,

                        "size":
                            0,

                        "ref_count":
                            1,

                        "corrupted":
                            False

                    })
                    .execute()
                )


        # ----------------------------------------------------
        # UNIQUE PATH
        # ----------------------------------------------------

        existing_files =
            get_files()


        next_number =
            len(existing_files) + 1


        safe_filename =
            filename.replace(
                " ",
                "_"
            )


        path = (
            f"/uploads/"
            f"{next_number:02d}-"
            f"{safe_filename}"
        )


        # ----------------------------------------------------
        # MERKLE ROOT
        # ----------------------------------------------------

        root =
            merkle_root(
                block_hashes
            )


        # ----------------------------------------------------
        # CREATE FILE RECORD
        # ----------------------------------------------------

        entry = {

            "name":
                filename,

            "path":
                path,

            "size":
                total_size,

            "blocks":
                len(block_hashes),

            "block_hashes":
                block_hashes,

            "merkle_root":
                root,

            "status":
                "unverified"
        }


        (
            supabase
            .table("ledger_files")
            .insert(entry)
            .execute()
        )


        # ----------------------------------------------------
        # RETURN SUCCESS
        # ----------------------------------------------------

        return jsonify(
            entry
        )


    except Exception as e:

        print(
            "UPLOAD ERROR:",
            repr(e)
        )

        return jsonify({

            "error":
                "Upload failed",

            "details":
                str(e)

        }), 500


# ============================================================
# VERIFY
# ============================================================

@app.post("/api/verify")
def verify():

    try:

        body =
            request.get_json(
                silent=True
            ) or {}


        path =
            body.get("path")


        if not path:

            return jsonify({
                "error":
                    "Path is required"
            }), 400


        response = (
            supabase
            .table("ledger_files")
            .select("*")
            .eq(
                "path",
                path
            )
            .limit(1)
            .execute()
        )


        rows =
            response.data or []


        if not rows:

            return jsonify({
                "error":
                    "File not found"
            }), 404


        entry =
            rows[0]


        current_hashes = []


        for block_hash in (
            entry["block_hashes"]
        ):

            raw =
                storage_download(
                    block_hash
                )


            current_hashes.append(
                sha256(raw)
            )


        recomputed_root =
            merkle_root(
                current_hashes
            )


        recorded_root =
            entry["merkle_root"]


        ok =
            recomputed_root == recorded_root


        bad_blocks = [

            old_hash

            for old_hash,
                current_hash

            in zip(
                entry["block_hashes"],
                current_hashes
            )

            if old_hash != current_hash

        ]


        status =
            (
                "verified intact"
                if ok
                else
                "tamper detected"
            )


        (
            supabase
            .table("ledger_files")
            .update({
                "status":
                    status
            })
            .eq(
                "path",
                path
            )
            .execute()
        )


        return jsonify({

            "ok":
                ok,

            "path":
                path,

            "recorded_root":
                recorded_root,

            "recomputed_root":
                recomputed_root,

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

        body =
            request.get_json(
                silent=True
            ) or {}


        path =
            body.get("path")


        if not path:

            return jsonify({
                "error":
                    "Path is required"
            }), 400


        response = (
            supabase
            .table("ledger_files")
            .select("*")
            .eq(
                "path",
                path
            )
            .limit(1)
            .execute()
        )


        rows =
            response.data or []


        if not rows:

            return jsonify({
                "error":
                    "File not found"
            }), 404


        entry =
            rows[0]


        for block_hash in (
            entry["block_hashes"]
        ):

            response = (
                supabase
                .table("ledger_blocks")
                .select(
                    "ref_count"
                )
                .eq(
                    "hash",
                    block_hash
                )
                .limit(1)
                .execute()
            )


            rows =
                response.data or []


            if not rows:

                continue


            ref_count =
                int(
                    rows[0].get(
                        "ref_count",
                        1
                    )
                    or 1
                )


            new_ref_count =
                ref_count - 1


            if new_ref_count <= 0:

                # Delete physical block
                storage_delete(
                    block_hash
                )


                # Delete database record
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
                
