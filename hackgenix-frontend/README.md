This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Running the full stack (frontend + Hackgenix backend)

This UI is wired against the real Hackgenix Feedback Intelligence API (see
`hackgenix-backend/docs/frontend-integration.md` for the full contract).

1. **Start the backend** (from `hackgenix-backend/`, requires PostgreSQL + pgvector):
   ```bash
   python -m venv venv
   venv\Scripts\activate            # Windows; `source venv/bin/activate` elsewhere
   pip install -r requirements.txt
   copy .env.example .env           # edit DATABASE_URL at minimum
   alembic upgrade head
   uvicorn main:app --reload        # http://localhost:8000
   ```
   Its default `CORS_ORIGINS` already allows `http://localhost:3000`, so no backend
   config changes are needed for local dev.

2. **Populate some data.** A fresh database has no Product Issues yet — either:
   - run `python scripts/seed_demo_data.py` from the backend for a realistic, ready-made
     dataset (a "Checkout" issue with a real trend reversal, resolved gap, etc.), or
   - ingest feedback from this app's **Raw Firehose** page and run it through
     analysis → embedding → semantic-analysis (needs `GEMINI_API_KEY` configured on
     the backend) before **Run issue discovery** will find anything to cluster.

3. **Start the frontend:**
   ```bash
   npm install
   npm run dev
   ```
   `NEXT_PUBLIC_API_BASE_URL` in `.env.local` points at the backend (defaults to
   `http://localhost:8000`) — change it if the backend runs elsewhere.

### What's wired up
- **Impact Grid** (`/`) — real Product Issues, paginated. Selecting one creates (or
  reuses) a Decision for it, evaluates it, and renders a grounded, cited
  Decision Brief — falling back to the raw deterministic evidence facts if
  `GEMINI_API_KEY` isn't configured. "Challenge this decision" re-evaluates and
  shows what changed since the last evaluation; "View raw sources" pulls the
  underlying feedback text.
- **Raw Firehose** (`/firehose`) — the unfiltered feedback list with filters, plus
  a live ingestion form and pipeline-tool buttons (issue discovery, signal
  recalculation).
- No numbers are ever fabricated client-side — every metric shown comes directly
  from a backend field, matching this project's "no blended score" design.

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
