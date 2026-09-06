import os
import datetime
import logging
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import firebase_admin
from firebase_admin import auth as fb_auth, firestore

from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Firebase Admin — uses Application Default Credentials. On Cloud Run this
# is the service account attached to the service; nothing to configure.
# ---------------------------------------------------------------------------
firebase_admin.initialize_app()
db = firestore.client()

# ---------------------------------------------------------------------------
# Gemini client via Vertex AI, authenticated with the same Application
# Default Credentials (the Cloud Run service account) already used for
# Firestore above — no API key involved at all. This sidesteps a currently
# unresolved, widely-reported Google-side bug where newly issued "AQ."
# auth-type Gemini API keys fail with ACCESS_TOKEN_TYPE_UNSUPPORTED on many
# accounts/projects regardless of SDK version or request format.
# ---------------------------------------------------------------------------
GCP_PROJECT = os.environ.get("GCP_PROJECT") or os.environ.get("FIREBASE_PROJECT_ID")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "global")
if not GCP_PROJECT:
    raise RuntimeError(
        "GCP_PROJECT (or FIREBASE_PROJECT_ID) is not set — required to reach "
        "Gemini via Vertex AI."
    )
genai_client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
MODEL_NAME = "gemini-3.6-flash"

REFLECTION_SYSTEM_PROMPT = (
    "You are a thoughtful journaling companion inside the Personal Gemini "
    "Journal app. The user is writing a private journal entry or reflection. "
    "Respond warmly and briefly (2-5 sentences unless asked for more): offer "
    "a genuine reflection, a helpful reframe, a brainstorming idea, or one "
    "good follow-up question, grounded in what they actually wrote and in "
    "the earlier turns of this conversation. Never be clinical or generic, "
    "never diagnose, and don't claim to be a therapist — you're a reflective "
    "companion who listens closely."
)

SUMMARY_SYSTEM_PROMPT = (
    "Summarize the following private journal session in 2-4 sentences. "
    "Capture the emotional throughline and any concrete takeaways or "
    "decisions, in a warm, plain-spoken voice. Synthesize — do not quote "
    "entries verbatim."
)

# Auto-summarize every N messages (3 user turns + 3 replies) so the summary
# stays current without popping up too aggressively during a short entry.
AUTO_SUMMARY_EVERY_N_MESSAGES = 6


def _build_transcript(msgs) -> str:
    return "\n".join(
        f"{'You' if d.to_dict().get('role') == 'user' else 'Gemini'}: {d.to_dict().get('content')}"
        for d in msgs
    )


def _generate_summary(transcript: str) -> str:
    response = genai_client.models.generate_content(
        model=MODEL_NAME,
        contents=transcript,
        config=types.GenerateContentConfig(system_instruction=SUMMARY_SYSTEM_PROMPT),
    )
    return response.text or ""

app = FastAPI(title="Personal Gemini Journal")


# ---------------------------------------------------------------------------
# Global error handling — any unhandled exception (a Firestore hiccup, a
# transient network error, etc.) returns a clean, generic JSON error
# instead of leaking a raw traceback or an opaque platform 500 page. The
# real exception is still logged server-side for debugging via Cloud Run
# logs; only the user-facing message is generic.
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logging.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on our end. Please try again."},
    )


# ---------------------------------------------------------------------------
# Auth dependency — verifies the Firebase ID token sent by the frontend
# after Google Sign-In. Every Firestore read/write below is scoped to this
# uid, which is what gives us per-user isolation.
# ---------------------------------------------------------------------------
async def get_uid(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    id_token = authorization.split(" ", 1)[1]
    try:
        decoded = fb_auth.verify_id_token(id_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return decoded["uid"]


class NewMessage(BaseModel):
    content: str


class MoodUpdate(BaseModel):
    mood: str


class TitleUpdate(BaseModel):
    title: str


MOODS = {"😊", "😐", "😔", "😤", "✨"}


# ---------------------------------------------------------------------------
# Sessions (a "session" = one journal thread / multi-turn reflection)
# ---------------------------------------------------------------------------
@app.post("/api/sessions")
async def create_session(uid: str = Depends(get_uid)):
    now = datetime.datetime.now(datetime.timezone.utc)
    doc_ref = db.collection("users").document(uid).collection("sessions").document()
    doc_ref.set(
        {
            "title": "Untitled entry",
            "summary": None,
            "mood": None,
            "createdAt": now,
            "updatedAt": now,
        }
    )
    return {"id": doc_ref.id}


@app.patch("/api/sessions/{session_id}/mood")
async def set_mood(session_id: str, body: MoodUpdate, uid: str = Depends(get_uid)):
    if body.mood not in MOODS:
        raise HTTPException(status_code=400, detail="Unrecognized mood")
    session_ref = db.collection("users").document(uid).collection("sessions").document(session_id)
    if not session_ref.get().exists:
        raise HTTPException(status_code=404, detail="Session not found")
    session_ref.update({"mood": body.mood})
    return {"mood": body.mood}


@app.patch("/api/sessions/{session_id}/title")
async def rename_session(session_id: str, body: TitleUpdate, uid: str = Depends(get_uid)):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title can't be empty")
    session_ref = db.collection("users").document(uid).collection("sessions").document(session_id)
    if not session_ref.get().exists:
        raise HTTPException(status_code=404, detail="Session not found")
    session_ref.update({"title": title[:80]})
    return {"title": title[:80]}


@app.get("/api/sessions")
async def list_sessions(uid: str = Depends(get_uid)):
    sessions_ref = (
        db.collection("users")
        .document(uid)
        .collection("sessions")
        .order_by("updatedAt", direction=firestore.Query.DESCENDING)
    )
    out = []
    for doc in sessions_ref.stream():
        d = doc.to_dict()
        out.append(
            {
                "id": doc.id,
                "title": d.get("title") or "Untitled entry",
                "summary": d.get("summary"),
                "mood": d.get("mood"),
                "createdAt": d.get("createdAt").isoformat() if d.get("createdAt") else None,
                "updatedAt": d.get("updatedAt").isoformat() if d.get("updatedAt") else None,
            }
        )
    return out


@app.get("/api/sessions/{session_id}/messages")
async def get_messages(session_id: str, uid: str = Depends(get_uid)):
    session_ref = db.collection("users").document(uid).collection("sessions").document(session_id)
    if not session_ref.get().exists:
        raise HTTPException(status_code=404, detail="Session not found")
    msgs_ref = session_ref.collection("messages").order_by("timestamp")
    out = []
    for doc in msgs_ref.stream():
        d = doc.to_dict()
        out.append(
            {
                "id": doc.id,
                "role": d.get("role"),
                "content": d.get("content"),
                "timestamp": d.get("timestamp").isoformat() if d.get("timestamp") else None,
            }
        )
    return out


@app.post("/api/sessions/{session_id}/messages")
async def post_message(session_id: str, body: NewMessage, uid: str = Depends(get_uid)):
    if not body.content or not body.content.strip():
        raise HTTPException(status_code=400, detail="Message content is required")

    session_ref = db.collection("users").document(uid).collection("sessions").document(session_id)
    session_snap = session_ref.get()
    if not session_snap.exists:
        raise HTTPException(status_code=404, detail="Session not found")

    messages_ref = session_ref.collection("messages")

    # Rebuild prior turns so Gemini has real multi-turn context.
    history = []
    for doc in messages_ref.order_by("timestamp").stream():
        d = doc.to_dict()
        role = "user" if d.get("role") == "user" else "model"
        history.append(types.Content(role=role, parts=[types.Part(text=d.get("content", ""))]))
    history.append(types.Content(role="user", parts=[types.Part(text=body.content)]))

    try:
        response = genai_client.models.generate_content(
            model=MODEL_NAME,
            contents=history,
            config=types.GenerateContentConfig(system_instruction=REFLECTION_SYSTEM_PROMPT),
        )
        reply_text = response.text or "I'm not sure what to say to that yet — could you say a bit more?"
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini request failed: {e}")

    now = datetime.datetime.now(datetime.timezone.utc)
    messages_ref.add({"role": "user", "content": body.content, "timestamp": now})
    now2 = datetime.datetime.now(datetime.timezone.utc)
    messages_ref.add({"role": "model", "content": reply_text, "timestamp": now2})

    total_messages = len(history) + 1  # history already includes the new user turn
    update_fields = {"updatedAt": now2}
    existing = session_snap.to_dict()
    if not existing.get("title") or existing.get("title") == "Untitled entry":
        update_fields["title"] = body.content.strip()[:60]

    new_summary = None
    if total_messages % AUTO_SUMMARY_EVERY_N_MESSAGES == 0:
        try:
            all_msgs = list(messages_ref.order_by("timestamp").stream())
            new_summary = _generate_summary(_build_transcript(all_msgs))
            update_fields["summary"] = new_summary
        except Exception:
            # Auto-summary is a nice-to-have; a failure here shouldn't
            # break the core reply the user is waiting on.
            pass

    session_ref.update(update_fields)

    return {"role": "model", "content": reply_text, "summary": new_summary}


@app.post("/api/sessions/{session_id}/summarize")
async def summarize_session(session_id: str, uid: str = Depends(get_uid)):
    session_ref = db.collection("users").document(uid).collection("sessions").document(session_id)
    if not session_ref.get().exists:
        raise HTTPException(status_code=404, detail="Session not found")

    msgs = list(session_ref.collection("messages").order_by("timestamp").stream())
    if not msgs:
        raise HTTPException(status_code=400, detail="Nothing to summarize yet")

    try:
        summary = _generate_summary(_build_transcript(msgs))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini request failed: {e}")

    session_ref.update({"summary": summary, "updatedAt": datetime.datetime.now(datetime.timezone.utc)})
    return {"summary": summary}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, uid: str = Depends(get_uid)):
    session_ref = db.collection("users").document(uid).collection("sessions").document(session_id)
    if not session_ref.get().exists:
        raise HTTPException(status_code=404, detail="Session not found")
    for doc in session_ref.collection("messages").stream():
        doc.reference.delete()
    session_ref.delete()
    return {"ok": True}


# ---------------------------------------------------------------------------
# "On this day" — surface an old entry from roughly a week or a month ago
# when the user starts a new one.
# ---------------------------------------------------------------------------
@app.get("/api/on-this-day")
async def on_this_day(uid: str = Depends(get_uid)):
    sessions_ref = db.collection("users").document(uid).collection("sessions")
    now = datetime.datetime.now(datetime.timezone.utc)

    candidates = []
    for doc in sessions_ref.stream():
        d = doc.to_dict()
        created = d.get("createdAt")
        if not created:
            continue
        age_days = (now - created).days
        candidates.append((age_days, doc.id, d))

    # Prefer something from ~1 week ago, then ~1 month ago, within a
    # forgiving window either way so it still finds something useful.
    for target, tolerance in [(7, 2), (30, 4)]:
        match = min(
            (c for c in candidates if abs(c[0] - target) <= tolerance),
            key=lambda c: abs(c[0] - target),
            default=None,
        )
        if match:
            _, doc_id, d = match
            return {
                "found": True,
                "session": {
                    "id": doc_id,
                    "title": d.get("title") or "Untitled entry",
                    "summary": d.get("summary"),
                    "daysAgo": match[0],
                },
            }

    return {"found": False}


# ---------------------------------------------------------------------------
# Export — download the full journal as plain text.
# ---------------------------------------------------------------------------
@app.get("/api/export")
async def export_journal(uid: str = Depends(get_uid)):
    from fastapi.responses import PlainTextResponse

    sessions_ref = (
        db.collection("users").document(uid).collection("sessions").order_by("createdAt")
    )
    lines = ["PERSONAL GEMINI JOURNAL — EXPORT", "=" * 40, ""]
    for doc in sessions_ref.stream():
        d = doc.to_dict()
        created = d.get("createdAt")
        lines.append(f"## {d.get('title') or 'Untitled entry'}")
        if created:
            lines.append(created.strftime("%B %d, %Y"))
        if d.get("mood"):
            lines.append(f"Mood: {d.get('mood')}")
        if d.get("summary"):
            lines.append(f"Summary: {d.get('summary')}")
        lines.append("")
        for msg_doc in doc.reference.collection("messages").order_by("timestamp").stream():
            m = msg_doc.to_dict()
            speaker = "You" if m.get("role") == "user" else "Gemini"
            lines.append(f"{speaker}: {m.get('content')}")
        lines.append("")
        lines.append("-" * 40)
        lines.append("")

    content = "\n".join(lines)
    return PlainTextResponse(
        content,
        headers={"Content-Disposition": "attachment; filename=journal-export.txt"},
    )


# ---------------------------------------------------------------------------
# Weekly digest — Gemini reasons across the past week's summaries.
# ---------------------------------------------------------------------------
DIGEST_SYSTEM_PROMPT = (
    "You are reviewing a week of someone's private journal entries, given "
    "as a list of per-entry summaries. Write a short weekly digest (3-5 "
    "sentences): name the throughline or pattern across entries, note "
    "anything that shifted over the week, and end with one gentle, "
    "specific question or nudge for the week ahead. Warm, plain-spoken, "
    "never clinical."
)


@app.post("/api/weekly-digest")
async def weekly_digest(uid: str = Depends(get_uid)):
    now = datetime.datetime.now(datetime.timezone.utc)
    week_ago = now - datetime.timedelta(days=7)

    sessions_ref = (
        db.collection("users")
        .document(uid)
        .collection("sessions")
        .where("createdAt", ">=", week_ago)
        .order_by("createdAt")
    )

    entries_text = []
    for doc in sessions_ref.stream():
        d = doc.to_dict()
        summary = d.get("summary")
        if not summary:
            # No auto-summary yet (short session) — fall back to messages.
            msgs = list(doc.reference.collection("messages").order_by("timestamp").stream())
            if not msgs:
                continue
            summary = _build_transcript(msgs)[:600]
        created = d.get("createdAt")
        date_str = created.strftime("%b %d") if created else ""
        entries_text.append(f"{date_str} — {d.get('title') or 'Untitled entry'}: {summary}")

    if not entries_text:
        raise HTTPException(status_code=400, detail="No entries from the past week yet")

    transcript = "\n".join(entries_text)

    try:
        response = genai_client.models.generate_content(
            model=MODEL_NAME,
            contents=transcript,
            config=types.GenerateContentConfig(system_instruction=DIGEST_SYSTEM_PROMPT),
        )
        digest_text = response.text or ""
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini request failed: {e}")

    digest_ref = db.collection("users").document(uid).collection("digests").document()
    digest_ref.set(
        {
            "content": digest_text,
            "weekStart": week_ago,
            "weekEnd": now,
            "createdAt": now,
        }
    )

    return {"content": digest_text, "entryCount": len(entries_text)}


# ---------------------------------------------------------------------------
# Static frontend — served from the same container / same Cloud Run URL,
# to satisfy the single-deployment-link requirement.
# ---------------------------------------------------------------------------
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/config.json")
async def config():
    # Firebase web config is not a secret — it's designed to ship to the
    # browser. It identifies the project; access is still enforced by
    # Firebase Auth + Firestore security rules server-side.
    return {
        "apiKey": os.environ.get("FIREBASE_API_KEY", ""),
        "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
        "projectId": os.environ.get("FIREBASE_PROJECT_ID", ""),
        "appId": os.environ.get("FIREBASE_APP_ID", ""),
    }
