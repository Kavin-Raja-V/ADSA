# The Ledger — Full-Stack Mini Project

## Run
1. Install Python 3.10+.
2. Create a virtual environment:
   `python -m venv .venv`
3. Activate it.
4. Install:
   `pip install -r requirements.txt`
5. Start:
   `python backend/app.py`
6. Open:
   `http://127.0.0.1:5000/`

## Components
- frontend/index.html — Ledger UI
- backend/app.py — Flask API
- backend/cas_engine.py — SHA-256 CAS, chunk storage, deduplication/reference counting
- backend/merkle_tree.py — Merkle root calculation
- backend/avl_tree.py — self-balancing AVL namespace
- backend/storage_engine.py — file metadata and verification
- storage/blocks — persistent content blocks

## Demonstration
Use "Load example files", then inspect blocks. The two Q3 report files contain identical content and demonstrate block reuse. Use "flip a bit" on a block and then "Verify integrity" to demonstrate tamper detection.
