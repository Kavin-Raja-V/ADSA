# Vercel deployment fix

Vercel's current Flask runtime supports zero-configuration Flask deployments.
The Flask entrypoint is `app.py` at the repository root.

Vercel settings:
- Framework: Flask
- Root Directory: `./`
- No custom Build Command
- No `vercel.json`

Environment variables:
- SUPABASE_URL
- SUPABASE_SECRET_KEY
- SUPABASE_BUCKET=ledger-blocks
