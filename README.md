# Voice GenAI

- **Description:** A FastAPI-based WebSocket server that connects a phone/media stream to Google's Gemini Live audio model for conducting telephone surveys. Includes a local `test_client.py` to test audio streams with a microphone and speaker.

**Quick Start**
- **Prerequisites:** Python 3.10+ and a Windows environment (PowerShell). Create and activate a virtual environment:

  - `python -m venv venv`
  - `.\venv\Scripts\activate`

- **Install dependencies:**

  - `pip install -r requirements.txt`

- **Environment variables:**

  - `GOOGLE_API_KEY` — API key for Google GenAI (required).
  - `MONGO_URI` — (optional) MongoDB connection string; default `mongodb://localhost:27017`.
  - `MONGO_DB_NAME` — (optional) default `survey_db`.
  - `PORT` — (optional) HTTP port; default 8080.

**Files & Purpose**
- `main.py` : FastAPI app. Opens `/media-stream` websocket endpoint, forwards audio to Gemini Live, and handles tool calls (the `saveAnswer` tool writes to MongoDB and returns the next question).
- `test_client.py` : Local test client that captures microphone audio (16 kHz) and plays server audio (24 kHz).
- `system_prompt.md` : Markdown system prompt used to instruct the assistant (preferred). The server strips frontmatter and converts MD → plain text; `system_prompt.txt` is used as a fallback.
- `questions.json` : Survey flow (string keys, objects with `next`, `text`, and optional `options`).
- `requirements.txt` : Python requirements (includes optional `markdown` + `beautifulsoup4` for MD parsing).
- `uploadeToGetAnswer.py` : (EXCLUDED) Not documented here per request — do not depend on it.

**Run (local)**
- Run virtual environment:

  - `.\venv\Scripts\activate`

- Start the server:

  - `python main.py`

- Start the test client in another shell (when server is running):

  - `python test_client.py`

**Notes & Behavior**
- `system_prompt.md` is preferred; it supports YAML frontmatter (metadata) and rich instructions. If `markdown` and `beautifulsoup4` are installed, `main.py` will convert MD to plain text. Otherwise a safe fallback stripper is used.
- The server uses a `SESSION_STATE` map to track the last question per session — this helps when the model omits `question_number` in tool calls.
- Age answers (question 3) are normalized server-side: numeric values are parsed from `user_answer` and mapped to the configured age buckets; implausible numbers are flagged as `Geçersiz yaş cevabı`.

**Troubleshooting**
- If the server fails to start due to missing API key: ensure `GOOGLE_API_KEY` is set in your environment or in a `.env` file.
- If microphone audio does not transmit, verify `pyaudio` is correctly installed and that the correct sampling rates are used (MIC=16000 Hz, SPK=24000 Hz).
- Avoid committing `venv/` or large binaries. Add `venv/` to `.gitignore`.

**License**
- MIT-style;
