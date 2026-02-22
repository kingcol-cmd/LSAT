from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "attempts.db"
STATIC_CSS = (BASE_DIR / "static" / "styles.css").read_text(encoding="utf-8")

REQUIRED_FIELDS = ["stimulus", "question_stem", "chosen_answer", "chosen_answer_text", "correct_answer", "correct_answer_text"]
VALID_ANSWER_CHOICES = {"A", "B", "C", "D", "E"}

TRAP_PATTERNS = {
    "scope_shift": "Scope shift: the wrong answer introduces a broader or narrower claim than the stimulus supports.",
    "reversal": "Reversal: it flips a sufficient/necessary relationship or reverses causality.",
    "extreme": "Extreme language trap: uses words like 'always' or 'never' that overstate the argument.",
    "irrelevant": "Relevance trap: sounds plausible but does not engage the argument's core gap.",
}

QUESTION_TYPE_HINTS = {
    "strengthen": "Look for evidence that tightens the link between premises and conclusion.",
    "weaken": "Look for information that undermines the key assumption.",
    "flaw": "Identify the exact reasoning gap and choose the option naming that mistake.",
}


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            analysis TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempt_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (attempt_id) REFERENCES attempts(id)
        )
        """
    )
    conn.commit()
    conn.close()


def infer_logic_breakdown(stimulus: str) -> list[str]:
    sentences = [s.strip() for s in stimulus.replace("?", ".").split(".") if s.strip()]
    if not sentences:
        return ["No clear sentences were found; restate the argument with explicit premise and conclusion."]
    if len(sentences) == 1:
        return [
            f"Claim detected: {sentences[0]}",
            "Likely missing premise: identify what must be true for this claim to hold.",
        ]
    breakdown = [f"Premise {i}: {s}" for i, s in enumerate(sentences[:-1], start=1)]
    breakdown.append(f"Potential conclusion: {sentences[-1]}")
    breakdown.append("Check whether the premises fully justify the conclusion or rely on an assumption.")
    return breakdown


def infer_trap_pattern(chosen_answer: str, correct_answer: str, question_stem: str, question_type: str) -> tuple[str, str]:
    if chosen_answer == correct_answer:
        return (
            "No trap detected because the selected answer matches the correct answer.",
            "Great work. Focus on making your reasoning explicit so you can replicate it.",
        )
    stem_lower = question_stem.lower()
    q_type = question_type.lower().strip()
    if "except" in stem_lower or "least" in stem_lower:
        key = "reversal"
    elif q_type in {"strengthen", "weaken", "flaw"}:
        key = "irrelevant"
    elif chosen_answer in {"A", "E"}:
        key = "extreme"
    else:
        key = "scope_shift"
    diagnosis = TRAP_PATTERNS[key]
    why_attractive = f"Choice {chosen_answer} likely echoed familiar wording or a tempting shortcut, but did not satisfy the exact task in the stem."
    return diagnosis, why_attractive


def build_socratic_questions(question_type: str) -> list[str]:
    hint = QUESTION_TYPE_HINTS.get(question_type.lower().strip(), "Focus on the exact burden in the question stem.")
    return [
        "What is the author trying to prove, and which sentence is that conclusion?",
        "Which premise does the conclusion depend on most heavily?",
        "What unstated assumption links that premise to the conclusion?",
        f"How does each answer choice interact with that assumption? ({hint})",
        "Which choice best matches the stem's task without adding new unsupported claims?",
    ]


def validate_payload(payload: dict[str, Any]) -> tuple[bool, str | None]:
    missing = [f for f in REQUIRED_FIELDS if not str(payload.get(f, "")).strip()]
    if missing:
        return False, f"Missing required fields: {', '.join(missing)}"
    chosen = str(payload.get("chosen_answer", "")).strip().upper()
    correct = str(payload.get("correct_answer", "")).strip().upper()
    if chosen not in VALID_ANSWER_CHOICES or correct not in VALID_ANSWER_CHOICES:
        return False, "Answer choices must be one of: A, B, C, D, E."
    if len(str(payload.get("chosen_answer_text", "")).strip()) < 3:
        return False, "chosen_answer_text must include the actual answer content."
    if len(str(payload.get("correct_answer_text", "")).strip()) < 3:
        return False, "correct_answer_text must include the actual answer content."
    return True, None


def analyze_payload(payload: dict[str, Any]) -> dict[str, Any]:
    q_type = str(payload.get("question_type", "")).strip() or "General LR"
    chosen = str(payload["chosen_answer"]).strip().upper()
    correct = str(payload["correct_answer"]).strip().upper()
    logic_breakdown = infer_logic_breakdown(str(payload["stimulus"]))
    trap_pattern, why_attractive = infer_trap_pattern(chosen, correct, str(payload["question_stem"]), q_type)
    if chosen == correct:
        correct_reason = f"Choice {correct} wins because it directly meets the stem and fits the argument structure."
        takeaway = "Name the conclusion, assumption, and task before evaluating choices."
    else:
        correct_reason = f"Choice {correct} wins because it addresses the argument's core gap more directly than {chosen}."
        takeaway = "When stuck, prephrase the logical job first; then eliminate choices that are merely plausible."
    return {
        "logic_breakdown": logic_breakdown,
        "trap_answer_pattern": trap_pattern,
        "guided_socratic_questions": build_socratic_questions(q_type),
        "diagnosis": {
            "why_wrong_answer_attractive": why_attractive,
            "why_correct_answer_wins": correct_reason,
            "reflection_prompts": [
                "What assumption did I miss before choosing?",
                "What wording in my chosen answer felt persuasive but wasn't responsive to the stem?",
                "How will I test this pattern on the next LR question?",
            ],
            "takeaway_rule": takeaway,
        },
    }


def build_coach_reply(attempt: dict[str, Any], student_message: str) -> str:
    payload = attempt["payload"]
    analysis = attempt["analysis"]
    chosen = str(payload.get("chosen_answer", "?")).upper()
    correct = str(payload.get("correct_answer", "?")).upper()
    trap = analysis.get("trap_answer_pattern", "")
    return (
        "Coach AI: Good reflection. "
        f"You chose {chosen}, but {correct} is correct. "
        f"Main trap: {trap} "
        "Next step: restate the conclusion in your own words, then name the assumption before checking choices. "
        f"Try this now: {analysis['guided_socratic_questions'][0]}"
        f" Then: {student_message[:120]}"
    )


def save_attempt(session_id: str, payload: dict[str, Any], analysis: dict[str, Any]) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO attempts (session_id, payload, analysis, created_at) VALUES (?, ?, ?, ?)",
        (session_id, json.dumps(payload), json.dumps(analysis), datetime.utcnow().isoformat()),
    )
    conn.commit()
    rid = int(cur.lastrowid)
    conn.close()
    return rid


def save_feedback_message(attempt_id: int, session_id: str, role: str, message: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO feedback_messages (attempt_id, session_id, role, message, created_at) VALUES (?, ?, ?, ?, ?)",
        (attempt_id, session_id, role, message, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_attempt(attempt_id: int) -> dict[str, Any] | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row["id"], "session_id": row["session_id"], "payload": json.loads(row["payload"]), "analysis": json.loads(row["analysis"]), "created_at": row["created_at"]}


def get_history(session_id: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, created_at, payload FROM attempts WHERE session_id = ? ORDER BY id DESC", (session_id,)).fetchall()
    conn.close()
    return [{"id": r["id"], "created_at": r["created_at"], "payload": json.loads(r["payload"])} for r in rows]


def get_feedback_messages(attempt_id: int) -> list[dict[str, str]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT role, message, created_at FROM feedback_messages WHERE attempt_id = ? ORDER BY id ASC",
        (attempt_id,),
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "message": r["message"], "created_at": r["created_at"]} for r in rows]

def normalize_question_type(raw: str) -> str:
    value = (raw or "").strip().lower()
    aliases = {
        "strengthen": "Strengthen",
        "weaken": "Weaken",
        "flaw": "Flaw",
        "assumption": "Assumption",
        "necessary assumption": "Assumption",
        "sufficient assumption": "Assumption",
        "inference": "Inference",
        "must be true": "Inference",
        "resolve": "Resolve/Explain",
        "explain": "Resolve/Explain",
        "paradox": "Resolve/Explain",
    }
    for k, label in aliases.items():
        if k in value:
            return label
    return raw.strip() if raw.strip() else "General LR"


def calculate_question_type_patterns(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    stats: dict[str, dict[str, int]] = {}
    for attempt in attempts:
        payload = attempt["payload"]
        q_type = normalize_question_type(str(payload.get("question_type", "")))
        chosen = str(payload.get("chosen_answer", "")).strip().upper()
        correct = str(payload.get("correct_answer", "")).strip().upper()
        bucket = stats.setdefault(q_type, {"total": 0, "misses": 0, "correct": 0})
        bucket["total"] += 1
        if chosen == correct:
            bucket["correct"] += 1
        else:
            bucket["misses"] += 1

    patterns = []
    for q_type, counts in stats.items():
        miss_rate = (counts["misses"] / counts["total"]) if counts["total"] else 0.0
        confidence = "high" if counts["total"] >= 5 else ("medium" if counts["total"] >= 3 else "low")
        patterns.append(
            {
                "question_type": q_type,
                "total_attempts": counts["total"],
                "misses": counts["misses"],
                "correct": counts["correct"],
                "miss_rate": round(miss_rate, 2),
                "confidence": confidence,
            }
        )

    patterns.sort(key=lambda x: (x["miss_rate"], x["misses"], x["total_attempts"]), reverse=True)
    return {
        "most_missed_types": patterns[:3],
        "all_type_stats": patterns,
        "summary": "Question-type pattern recognition ranked by miss rate with sample-size confidence.",
    }



def page(title: str, body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title}</title><link rel='stylesheet' href='/static/styles.css'></head><body><main class='container'>{body}</main></body></html>"


def render_index(error: str = "", previous: dict[str, Any] | None = None) -> str:
    prev = previous or {}
    eblock = f"<div class='error'>{html.escape(error)}</div>" if error else ""
    return page("LSAT Logical Reasoning Review Owl", f"""
    <header class='title-wrap'>
      <div class='owl-logo' aria-hidden='true'>
        <svg viewBox='0 0 120 120' role='img'>
          <circle cx='60' cy='60' r='56' class='owl-bg'/>
          <ellipse cx='60' cy='68' rx='38' ry='30' class='owl-face'/>
          <circle cx='45' cy='58' r='12' class='owl-eye'/>
          <circle cx='75' cy='58' r='12' class='owl-eye'/>
          <circle cx='45' cy='58' r='5' class='owl-pupil'/>
          <circle cx='75' cy='58' r='5' class='owl-pupil'/>
          <polygon points='60,62 52,76 68,76' class='owl-beak'/>
          <path d='M30 46 L45 28 L52 46 Z' class='owl-ear'/>
          <path d='M90 46 L75 28 L68 46 Z' class='owl-ear'/>
        </svg>
      </div>
      <div>
        <h1>LSAT Logical Reasoning Review Owl</h1>
        <p>Submit your LR attempt to get a reasoning diagnosis and guided questions.</p>
      </div>
    </header>
    {eblock}
    <form method='post' action='/submit' class='card'>
      <label>Session ID (for review history)<input name='session_id' value='{html.escape(str(prev.get("session_id", "")))}' placeholder='e.g., student-123'></label>
      <label>Stimulus *<textarea name='stimulus' required>{html.escape(str(prev.get("stimulus", "")))}</textarea></label>
      <label>Question Stem *<textarea name='question_stem' required>{html.escape(str(prev.get("question_stem", "")))}</textarea></label>
      <div class='row'>
        <label>Chosen Answer Letter *<input name='chosen_answer' maxlength='1' required value='{html.escape(str(prev.get("chosen_answer", "")))}' placeholder='A-E'></label>
        <label>Chosen Answer Text *<input name='chosen_answer_text' required value='{html.escape(str(prev.get("chosen_answer_text", "")))}' placeholder='Paste the chosen answer text'></label>
        <label>Correct Answer Letter *<input name='correct_answer' maxlength='1' required value='{html.escape(str(prev.get("correct_answer", "")))}' placeholder='A-E'></label>
        <label>Correct Answer Text *<input name='correct_answer_text' required value='{html.escape(str(prev.get("correct_answer_text", "")))}' placeholder='Paste the correct answer text'></label>
        <label>Question Type<input name='question_type' value='{html.escape(str(prev.get("question_type", "")))}' placeholder='Strengthen / Weaken / Flaw'></label>
      </div>
      <button type='submit'>Analyze Attempt</button>
    </form>
    """)


def render_results(attempt: dict[str, Any]) -> str:
    a = attempt["analysis"]
    logic = "".join([f"<li>{html.escape(i)}</li>" for i in a["logic_breakdown"]])
    socratic = "".join([f"<li>{html.escape(i)}</li>" for i in a["guided_socratic_questions"]])
    prompts = "".join([f"<li>{html.escape(i)}</li>" for i in a["diagnosis"]["reflection_prompts"]])
    messages = get_feedback_messages(attempt["id"])
    thread = "".join([
        f"<li><strong>{html.escape(m['role'])}:</strong> {html.escape(m['message'])}<br><small>{html.escape(m['created_at'])}</small></li>"
        for m in messages
    ]) or "<li>No follow-up messages yet. Ask the AI coach a question below.</li>"

    return page("Attempt Results", f"""
    <h1>Attempt #{attempt['id']} Results</h1>
    <p><strong>Session:</strong> {html.escape(attempt['session_id'])} · <strong>Saved:</strong> {html.escape(attempt['created_at'])}</p>
    <section class='card'><h2>Logic Breakdown</h2><ul>{logic}</ul></section>
    <section class='card'><h2>Diagnosis</h2>
      <p><strong>Trap pattern:</strong> {html.escape(a['trap_answer_pattern'])}</p>
      <p><strong>Chosen answer:</strong> {html.escape(attempt['payload'].get('chosen_answer',''))} — {html.escape(attempt['payload'].get('chosen_answer_text',''))}</p>
      <p><strong>Correct answer:</strong> {html.escape(attempt['payload'].get('correct_answer',''))} — {html.escape(attempt['payload'].get('correct_answer_text',''))}</p>
      <p><strong>Why wrong answer felt attractive:</strong> {html.escape(a['diagnosis']['why_wrong_answer_attractive'])}</p>
      <p><strong>Why correct answer wins:</strong> {html.escape(a['diagnosis']['why_correct_answer_wins'])}</p>
    </section>
    <section class='card'><h2>Guided Socratic Questions</h2><ol>{socratic}</ol></section>
    <section class='card'><h2>Reflection + Takeaway</h2><ul>{prompts}</ul><p><strong>Takeaway rule:</strong> {html.escape(a['diagnosis']['takeaway_rule'])}</p></section>
    <section class='card'>
      <h2>Follow-up with AI Coach</h2>
      <p>Yes—students can answer feedback here. Each follow-up is stored with this attempt.</p>
      <ul>{thread}</ul>
      <form method='post' action='/results/{attempt['id']}/respond'>
        <label>Your reflection question
          <textarea name='student_message' required placeholder='Example: Why is my answer a scope shift?'></textarea>
        </label>
        <button type='submit'>Send to AI Coach</button>
      </form>
    </section>
    <a href='/history/{quote(attempt['session_id'])}'>View session history</a><br><a href='/'>Analyze another question</a>
    """)


def render_history(session_id: str, attempts: list[dict[str, Any]]) -> str:
    if attempts:
        items = "".join([f"<li><a href='/results/{a['id']}'>Attempt #{a['id']}</a> — {html.escape(a['created_at'])} — Type {html.escape(str(a['payload'].get('question_type','General LR')))} — Chosen {html.escape(str(a['payload'].get('chosen_answer','')))} / Correct {html.escape(str(a['payload'].get('correct_answer','')))}</li>" for a in attempts])
        content = f"<ul class='card'>{items}</ul>"
        pattern_data = calculate_question_type_patterns(attempts)
        rows = "".join([
            f"<tr><td>{html.escape(row['question_type'])}</td><td>{row['total_attempts']}</td><td>{row['misses']}</td><td>{row['correct']}</td><td>{int(row['miss_rate']*100)}%</td><td>{html.escape(row['confidence'])}</td></tr>"
            for row in pattern_data["all_type_stats"]
        ])
        pattern_block = f"""
        <section class='card'>
          <h2>Pattern Recognition: Most Missed Question Types</h2>
          <p>{html.escape(pattern_data['summary'])}</p>
          <table>
            <thead><tr><th>Type</th><th>Attempts</th><th>Misses</th><th>Correct</th><th>Miss rate</th><th>Confidence</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </section>
        """
    else:
        content = "<p class='card'>No attempts yet for this session.</p>"
        pattern_block = ""
    return page("History", f"<h1>History: {html.escape(session_id)}</h1>{content}{pattern_block}<a href='/'>Back to intake form</a>")



class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send(HTTPStatus.OK, render_index())
        elif path == "/static/styles.css":
            self._send(HTTPStatus.OK, STATIC_CSS, "text/css; charset=utf-8")
        elif path.startswith("/results/"):
            try:
                aid = int(path.split("/")[2])
            except (ValueError, IndexError):
                self._send(HTTPStatus.BAD_REQUEST, page("Error", "<p class='error'>Invalid attempt id.</p>"))
                return
            attempt = get_attempt(aid)
            if not attempt:
                self._send(HTTPStatus.NOT_FOUND, page("Error", "<p class='error'>Attempt not found.</p>"))
                return
            self._send(HTTPStatus.OK, render_results(attempt))
        elif path.startswith("/history/"):
            session_id = unquote(path.split("/history/")[-1])
            self._send(HTTPStatus.OK, render_history(session_id, get_history(session_id)))
        elif path.startswith("/api/patterns/"):
            session_id = unquote(path.split("/api/patterns/")[-1])
            attempts = get_history(session_id)
            self._send(HTTPStatus.OK, json.dumps({"session_id": session_id, "patterns": calculate_question_type_patterns(attempts)}), "application/json")
        else:
            self._send(HTTPStatus.NOT_FOUND, page("Not found", "<p class='error'>Route not found.</p>"))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)

        if parsed.path == "/api/analyze":
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({"error": "Malformed JSON payload."}), "application/json")
                return
            valid, error = validate_payload(payload)
            if not valid:
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({"error": error}), "application/json")
                return
            session_id = str(payload.get("session_id", "anonymous")).strip() or "anonymous"
            analysis = analyze_payload(payload)
            attempt_id = save_attempt(session_id, payload, analysis)
            self._send(HTTPStatus.OK, json.dumps({"attempt_id": attempt_id, "session_id": session_id, "analysis": analysis}), "application/json")
            return

        if parsed.path == "/api/coach":
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({"error": "Malformed JSON payload."}), "application/json")
                return
            attempt_id = int(payload.get("attempt_id", 0)) if str(payload.get("attempt_id", "")).isdigit() else 0
            student_message = str(payload.get("student_message", "")).strip()
            if not attempt_id or not student_message:
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({"error": "attempt_id and student_message are required."}), "application/json")
                return
            attempt = get_attempt(attempt_id)
            if not attempt:
                self._send(HTTPStatus.NOT_FOUND, json.dumps({"error": "Attempt not found."}), "application/json")
                return
            save_feedback_message(attempt_id, attempt["session_id"], "student", student_message)
            coach_reply = build_coach_reply(attempt, student_message)
            save_feedback_message(attempt_id, attempt["session_id"], "coach", coach_reply)
            self._send(HTTPStatus.OK, json.dumps({"attempt_id": attempt_id, "coach_reply": coach_reply}), "application/json")
            return

        if parsed.path.startswith("/results/") and parsed.path.endswith("/respond"):
            bits = parsed.path.strip("/").split("/")
            if len(bits) != 3:
                self._send(HTTPStatus.BAD_REQUEST, page("Error", "<p class='error'>Invalid route format.</p>"))
                return
            try:
                attempt_id = int(bits[1])
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, page("Error", "<p class='error'>Invalid attempt id.</p>"))
                return
            attempt = get_attempt(attempt_id)
            if not attempt:
                self._send(HTTPStatus.NOT_FOUND, page("Error", "<p class='error'>Attempt not found.</p>"))
                return
            form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8"), keep_blank_values=True).items()}
            student_message = form.get("student_message", "").strip()
            if not student_message:
                self._send(HTTPStatus.BAD_REQUEST, page("Error", "<p class='error'>Please enter a follow-up message.</p>"))
                return
            save_feedback_message(attempt_id, attempt["session_id"], "student", student_message)
            coach_reply = build_coach_reply(attempt, student_message)
            save_feedback_message(attempt_id, attempt["session_id"], "coach", coach_reply)
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", f"/results/{attempt_id}")
            self.end_headers()
            return

        if parsed.path == "/submit":
            form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8"), keep_blank_values=True).items()}
            payload = {
                "session_id": form.get("session_id", "anonymous").strip() or "anonymous",
                "stimulus": form.get("stimulus", ""),
                "question_stem": form.get("question_stem", ""),
                "chosen_answer": form.get("chosen_answer", ""),
                "chosen_answer_text": form.get("chosen_answer_text", ""),
                "correct_answer": form.get("correct_answer", ""),
                "correct_answer_text": form.get("correct_answer_text", ""),
                "question_type": form.get("question_type", ""),
            }
            valid, error = validate_payload(payload)
            if not valid:
                self._send(HTTPStatus.BAD_REQUEST, render_index(error, payload))
                return
            analysis = analyze_payload(payload)
            aid = save_attempt(payload["session_id"], payload, analysis)
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", f"/results/{aid}")
            self.end_headers()
            return

        self._send(HTTPStatus.NOT_FOUND, page("Not found", "<p class='error'>Route not found.</p>"))


def run() -> None:
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", 5000), Handler)
    print("Serving on http://0.0.0.0:5000")
    server.serve_forever()


if __name__ == "__main__":
    run()
