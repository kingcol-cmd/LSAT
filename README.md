# LSAT LR Coach (Python + SQLite)

This project provides a lightweight full-stack app for logging LSAT Logical Reasoning attempts and generating guided feedback.

## Features

- Intake form with required fields:
  - `stimulus`
  - `question_stem`
  - `chosen_answer`
  - `chosen_answer_text`
  - `correct_answer`
  - `correct_answer_text`
  - `correct_answer`
  - optional `question_type`
- Backend API endpoint to analyze attempts and return:
  - logic breakdown of the stimulus
  - likely trap-answer pattern from the chosen wrong answer
  - 3–6 guided Socratic questions
- Persistent attempt storage in SQLite (`attempts.db`) keyed by `session_id`
- Results view with:
  - concise diagnosis of wrong-answer attraction
  - explanation of why the correct answer wins
  - guided reflection prompts + takeaway rule
  - follow-up AI coach thread where students can respond to feedback
- Question-type pattern recognition algorithm so students can track which LR types they miss most
- Input validation and error handling for missing fields and malformed answer choices

## Project structure

- `app.py` – HTTP server, analysis logic, validation, SQLite persistence, and pattern analytics
- `static/styles.css` – Basic styling
- `requirements.txt` – no external dependencies are required

## Run locally

```bash
python app.py
```

Then open `http://localhost:5000`.

## API usage

### Endpoint

`POST /api/analyze`

`POST /api/coach` (follow-up Q&A for an existing attempt)

`GET /api/patterns/<session_id>` (question-type miss-pattern analytics for progress tracking)

### Example request payload

```json
{
  "session_id": "student-42",
  "stimulus": "A recent city study found that neighborhoods with more trees have lower summer electricity use. Therefore, planting trees in all neighborhoods will reduce citywide electricity costs.",
  "question_stem": "Which one of the following, if true, most strengthens the argument?",
  "chosen_answer": "B",
  "chosen_answer_text": "Planting trees is expensive, so the city should avoid the policy.",
  "correct_answer": "D",
  "correct_answer_text": "Neighborhoods with more trees in the study were similar in income and building age, limiting confounds.",
  "correct_answer": "D",
  "question_type": "Strengthen"
}
```

### Example response payload (abridged)

```json
{
  "attempt_id": 7,
  "session_id": "student-42",
  "analysis": {
    "logic_breakdown": [
      "Premise 1: A recent city study found that neighborhoods with more trees have lower summer electricity use",
      "Potential conclusion: Therefore, planting trees in all neighborhoods will reduce citywide electricity costs",
      "Check whether the premises fully justify the conclusion or rely on an assumption."
    ],
    "trap_answer_pattern": "Relevance trap: sounds plausible but does not engage the argument's core gap.",
    "guided_socratic_questions": [
      "What is the author trying to prove, and which sentence is that conclusion?",
      "Which premise does the conclusion depend on most heavily?",
      "What unstated assumption links that premise to the conclusion?",
      "How does each answer choice interact with that assumption? (Look for evidence that tightens the link between premises and conclusion.)",
      "Which choice best matches the stem's task without adding new unsupported claims?"
    ],
    "diagnosis": {
      "why_wrong_answer_attractive": "Choice B likely echoed familiar wording or a tempting shortcut, but did not satisfy the exact task in the stem.",
      "why_correct_answer_wins": "Choice D wins because it addresses the argument's core gap more directly than B.",
      "reflection_prompts": [
        "What assumption did I miss before choosing?",
        "What wording in my chosen answer felt persuasive but wasn't responsive to the stem?",
        "How will I test this pattern on the next LR question?"
      ],
      "takeaway_rule": "When stuck, prephrase the logical job first; then eliminate choices that are merely plausible."
    }
  }
}
```

## Reviewing history

Visit `/history/<session_id>` (example: `/history/student-42`) to review earlier attempts.


### Example follow-up request (`/api/coach`)

```json
{
  "attempt_id": 7,
  "student_message": "Why is my answer a scope shift instead of a weaken answer?"
}
```

### Example follow-up response (`/api/coach`)

```json
{
  "attempt_id": 7,
  "coach_reply": "Coach AI: Good reflection. You chose B, but D is correct..."
}
```


### Example pattern-recognition response (`/api/patterns/student-42`)

```json
{
  "session_id": "student-42",
  "patterns": {
    "most_missed_types": [
      {"question_type": "Flaw", "total_attempts": 6, "misses": 4, "correct": 2, "miss_rate": 0.67, "confidence": "high"}
    ],
    "all_type_stats": [],
    "summary": "Question-type pattern recognition ranked by miss rate with sample-size confidence."
  }
}
```
