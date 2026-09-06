# Personal Gemini Journal

A private, AI-assisted journaling app. Sign in with Google, write entries,
and Gemini responds with a reflection, a brainstorm, or a good follow-up
question — remembering the whole conversation, not just the last message.
Every entry is stored in Firestore, strictly isolated per user.

Built for a Google Cloud Run Ideathon submission, on top of:
**FastAPI · Firebase Authentication (Google Sign-In) · Cloud Firestore ·
Gemini 3.6 Flash via Vertex AI · Cloud Run**, all in a single container
behind one URL.

## Features

- **Google Sign-In** — Firebase Auth, no passwords stored anywhere.
- **Multi-turn journaling** — Gemini replies with the full conversation as
  context, not a one-shot response.
- **Auto-summaries** — every couple of exchanges, Gemini writes a short
  session summary and saves it; also triggerable manually.
- **Mood tag** — a quick emoji tag per entry, visible in the sidebar.
- **"On this day"** — surfaces an entry from about a week or a month ago
  when starting a new one.
- **Search** — filter past entries by title or summary.
- **Weekly digest** — Gemini reasons across the past week's summaries to
  spot patterns and suggest something to sit with for the week ahead.
- **Export** — download the whole journal as a plain-text file.
- **Strict per-user isolation** — enforced in the backend (every query is
  scoped to a verified Firebase UID) and again in Firestore security
  rules as defense-in-depth.

## Design notes

- **No Gemini API key anywhere.** Gemini is called through **Vertex AI**,
  authenticated with Cloud Run's own service account (Application Default
  Credentials) — the same identity already used for Firestore. There's
  nothing to leak, store, or rotate.
- **A global FastAPI exception handler** catches unhandled errors and
  returns a clean message instead of a raw traceback, while still logging
  the real exception server-side.
- **Firestore rules validate document shape**, not just ownership — e.g.
  a mood value must be one of a fixed emoji set, titles and message
  content have length limits.

## Project structure

```
backend/
  main.py            FastAPI app: auth check, Firestore, Gemini calls
  requirements.txt
static/
  index.html         Landing + dashboard shell
  style.css
  app.js             Firebase sign-in, session list, composer
Dockerfile           Single container, serves both backend + frontend
firestore.rules      Per-user isolation + validation, enforced server-side
.env.example
```

## Running this yourself

You'll need a Firebase project (Authentication + Firestore enabled),
Vertex AI enabled on the same Google Cloud project, and the Cloud Run
service account granted the `roles/aiplatform.user` IAM role.

```bash
# Enable Vertex AI and grant the Cloud Run service account access
gcloud services enable aiplatform.googleapis.com
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# Deploy Firestore rules (or paste firestore.rules into the console's Rules tab)
firebase deploy --only firestore:rules --project YOUR_PROJECT_ID

# Deploy to Cloud Run
gcloud run deploy personal-gemini-journal \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars FIREBASE_API_KEY=...,FIREBASE_AUTH_DOMAIN=...,FIREBASE_PROJECT_ID=...,FIREBASE_APP_ID=...,GCP_LOCATION=global
```

After deploying, add the Cloud Run domain to Firebase's **Authentication →
Authorized domains** list, or Google Sign-In will fail on the live site.

For local development:

```bash
cd backend
pip install -r requirements.txt
gcloud auth application-default login
export GCP_PROJECT=your-project-id
export FIREBASE_API_KEY=... FIREBASE_AUTH_DOMAIN=... FIREBASE_PROJECT_ID=... FIREBASE_APP_ID=...
uvicorn main:app --reload --port 8080
```
