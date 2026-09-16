# The Ledger — Vercel + Supabase

## 1. Supabase
Open Supabase SQL Editor and run `supabase_setup.sql`.
Make sure the private Storage bucket `ledger-blocks` exists.

## 2. Vercel environment variables
Set these in the Vercel project:
- SUPABASE_URL = your Supabase Project URL
- SUPABASE_SECRET_KEY = your Supabase Secret key
- SUPABASE_BUCKET = ledger-blocks

Do NOT put SUPABASE_SECRET_KEY in frontend JavaScript.

## 3. GitHub
Push this folder to a GitHub repository.

## 4. Vercel
Import the GitHub repository in Vercel and deploy.
The included `vercel.json` maps `/api/*` to the Flask application.

## 5. Test
Open your Vercel URL, load example files, upload a file, verify it, and test bit-flip corruption.
