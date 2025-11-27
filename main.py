import os
import json
import base64
import asyncio
from datetime import datetime
from typing import Dict, Any, List
import re
from fastapi import FastAPI, WebSocket, Request, Response
from google import genai
from google.genai import types
from dotenv import load_dotenv
from pymongo import MongoClient

# --- 1. CONFIGURATION ---
load_dotenv()
API_KEY = os.environ.get("GOOGLE_API_KEY")
MODEL_ID = "gemini-2.5-flash-native-audio-preview-09-2025"

PORT = int(os.environ.get("PORT", 8080))

# MongoDB Config
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "survey_db")
MONGO_COLLECTION = "responses"

if not API_KEY:
    raise ValueError("GOOGLE_API_KEY is missing in .env file")

app = FastAPI()
client = genai.Client(api_key=API_KEY, http_options={'api_version': 'v1alpha'})

# --- 2. LOAD DATA ---
try:
    with open("questions.json", "r", encoding="utf-8") as f:
        SURVEY_FLOW = json.load(f)
        # Ensure keys are integers for easy lookup
        SURVEY_FLOW = {int(k): v for k, v in SURVEY_FLOW.items()}
    print(f"--> Loaded {len(SURVEY_FLOW)} questions from questions.json")
except FileNotFoundError:
    print("❌ ERROR: questions.json not found. Please create it.")
    SURVEY_FLOW = {}

# --- Load system prompt (prefer Markdown with optional YAML frontmatter) ---
try:
    # optional markdown -> text conversion (preferred if libs available)
    try:
        import markdown as _md
        from bs4 import BeautifulSoup as _BS
    except Exception:
        _md = None
        _BS = None

    def load_markdown_to_text(path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        # strip YAML frontmatter if present
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", content, re.S)
        body = m.group(2) if m else content
        if _md and _BS:
            html = _md.markdown(body)
            text = _BS(html, "html.parser").get_text(separator=" ")
        else:
            # simple fallback cleanup for markdown-like tokens
            text = re.sub(r"(^|\n)#{1,6}\s*", " ", body)
            text = re.sub(r"[`*_>\[\]\-]{1,}", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
        return text

    # prefer system_prompt.md
    try:
        SYSTEM_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "system_prompt.md")
        SYSTEM_INSTRUCTIONS = load_markdown_to_text(SYSTEM_PROMPT_PATH)
        print(f"--> Loaded system instructions from {os.path.basename(SYSTEM_PROMPT_PATH)} (Length: {len(SYSTEM_INSTRUCTIONS)} chars)")
    except FileNotFoundError:
        # fallback to legacy txt
        try:
            with open("system_prompt.txt", "r", encoding="utf-8") as _f:
                SYSTEM_INSTRUCTIONS = _f.read().strip()
            print("--> Loaded system instructions from system_prompt.txt (fallback)")
        except FileNotFoundError:
            SYSTEM_INSTRUCTIONS = "You are a survey assistant. Use saveAnswer to record results."
            print("⚠️ Warning: no system prompt found. Using default.")
except Exception as e:
    print(f"⚠️ Error loading system prompt: {e}")
    SYSTEM_INSTRUCTIONS = "You are a survey assistant. Use saveAnswer to record results."

# --- 3. DATABASE SETUP (MongoDB) ---
try:
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client[MONGO_DB_NAME]
    collection = db[MONGO_COLLECTION]
    print(f"--> Connected to MongoDB: {MONGO_DB_NAME}.{MONGO_COLLECTION}")
except Exception as e:
    print(f"❌ MongoDB Connection Error: {e}")
    collection = None

# Session state map: keeps last question id per session when Gemini doesn't provide it
SESSION_STATE: Dict[str, int] = {}

# --- 4. SAVE LOGIC (Single & Batch) ---

def goBackQuestion(current_q_no: int, session_id: str) -> str:
    """Retrieves the previous question when user wants to go back."""
    prev_id = current_q_no - 1
    if prev_id < 1:
        return json.dumps({"status": "error", "message": "Cannot go back further (already at Q1)."})
    
    prev_q_data = SURVEY_FLOW.get(prev_id)
    if not prev_q_data:
        return json.dumps({"status": "error", "message": "Previous question not found."})
    
    # Update session state to keep in sync
    if session_id:
        SESSION_STATE[session_id] = prev_id
        
    print(f"⬅️ GO BACK: Q{current_q_no} -> Q{prev_id}")
    
    return json.dumps({
        "status": "success",
        "q_no": prev_id,
        "question": prev_q_data["text"],
        "question_options": prev_q_data.get("options", [])
    })

def _save_single_answer(params: Dict[str, Any]) -> str:
    """Writes a single answer to MongoDB and returns the next question."""
    try:
        session_id = params.get("session_id")
        phone = params.get("phone")
        # Robustly get q_no from various possible keys
        q_no = int(params.get("question_number") or params.get("q_no") or 0)
        user_ans = params.get("user_answer", "")
        match_ans = params.get("answer", "")
        # sanitize answers: remove stray trailing commas and whitespace from ASR matches
        try:
            match_ans = str(match_ans).strip()
            if match_ans.endswith(','):
                match_ans = match_ans.rstrip(',').strip()
        except Exception:
            match_ans = str(match_ans)
        # normalize certain answers by question to avoid obvious mismatches
        def normalize_answer(q_no: int, user_answer: str, matched: str) -> (str, bool):
            """Normalize matched answer for specific question types.
            Returns (normalized_match, was_normalized_flag).
            """
            ua = (user_answer or "").strip()
            m = (matched or "").strip()
            # Question 3 = Age -> map numeric answers into age buckets
            if q_no == 3:
                # try to extract an integer from the user's raw answer
                import re as _re
                digits = _re.findall(r"\d{1,3}", ua)
                if digits:
                    try:
                        age = int(digits[0])
                    except Exception:
                        return (m, False)
                    # reasonable human age bounds
                    if 10 <= age <= 120:
                        if age <= 17:
                            return ("17 yaş ve altı", True)
                        if 18 <= age <= 24:
                            return ("18-24", True)
                        if 25 <= age <= 34:
                            return ("25-34", True)
                        if 35 <= age <= 44:
                            return ("35-44", True)
                        if 45 <= age <= 54:
                            return ("45-54", True)
                        if 55 <= age <= 64:
                            return ("55-64", True)
                        return ("65 yaş ve üstü", True)
                    else:
                        # implausible numeric -> mark as invalid so caller can handle
                        return ("Geçersiz yaş cevabı", True)
                # no digits found -> keep matched
                return (m, False)
            return (m, False)

        # apply normalization and detect if changed
        try:
            normalized_match, normalized_flag = normalize_answer(q_no, user_ans, match_ans)
            if normalized_flag and normalized_match != match_ans:
                print(f"🔧 Normalized match for Q{q_no}: '{match_ans}' -> '{normalized_match}' (user_answer='{user_ans}')")
                match_ans = normalized_match
        except Exception as _:
            pass
        retry = int(params.get("retry_count", 0))

        # FIX 1: HANDLE START OF SURVEY (Q=0)
        if q_no == 0:
            # This is the "start survey" signal. Do NOT save to DB.
            # Just return Question 1.
            print("🚀 STARTING SURVEY (Q0 -> Q1)")
            next_q_id = 1
            if session_id:
                SESSION_STATE[session_id] = next_q_id
            
            q1_data = SURVEY_FLOW[1]
            return json.dumps({
                "status": "success",
                "q_no": 1,
                "question": q1_data["text"],
                "question_options": q1_data.get("options", [])
            })

        # If question number is missing (0), try to recover from session state
        if q_no == 0 and session_id:
            q_no = int(SESSION_STATE.get(session_id, 0) or 0)
            if q_no == 0:
                # If still 0, treat as start request
                return _save_single_answer({**params, "question_number": 0})

        print(f"📝 SAVING [Single]: Q{q_no} | Answer: {match_ans}")

        if collection is not None:
            document = {
                "session_id": session_id,
                "phone": phone,
                "question_number": q_no,
                "user_answer": user_ans,
                "matched_answer": match_ans,
                "retry_count": retry,
                "timestamp": datetime.utcnow()
            }
            collection.insert_one(document)

        # LOGIC: Check for Survey End (e.g., Q1 Citizenship = Hayır)
        if q_no == 1 and "Hayır" in str(match_ans):
             return json.dumps({
                "status": "end_survey",
                "message": "Anketimiz sadece T.C. vatandaşları içindir. İyi günler."
            })

        # LOGIC: Get Next Question
        current_q_data = SURVEY_FLOW.get(q_no)
        if not current_q_data:
             # If current question not found, try looking for next anyway or end
             return json.dumps({"status": "success", "survey_complete": True})

        next_q_id = current_q_data.get("next")
        # persist next q in session state so we can recover if Gemini omits question_number
        if session_id and next_q_id is not None:
            try:
                SESSION_STATE[session_id] = int(next_q_id)
            except Exception:
                pass
        
        # If next is explicitly null (end of survey) or not found
        if next_q_id is None or next_q_id not in SURVEY_FLOW:
            return json.dumps({"status": "success", "survey_complete": True})

        next_q_data = SURVEY_FLOW[next_q_id]

        # FIX 3: ALIGN KEYS WITH PROMPT (q_no, question, question_options)
        return json.dumps({
            "status": "success",
            "q_no": next_q_id,
            "question": next_q_data["text"],
            "question_options": next_q_data.get("options", [])
        })

    except Exception as e:
        print(f"❌ Logic Error: {e}")
        return json.dumps({"status": "error", "message": str(e)})

def _save_batch_answers(answers_array: List[Dict], answers_dict: Dict, session_id: str, phone: str) -> str:
    """Writes multiple answers to MongoDB in one go."""
    print(f"📚 SAVING [Batch]: {len(answers_array)} items")
    try:
        if collection is not None:
            documents = []
            for item in answers_array:
                documents.append({
                    "session_id": session_id,
                    "phone": phone,
                    "question_number": int(item.get("question_number", item.get("q_no", 0))),
                    "user_answer": item.get("user_answer", ""),
                    "matched_answer": item.get("answer", ""),
                    "retry_count": int(item.get("retry_count", 0)),
                    "timestamp": datetime.utcnow()
                })
            if documents:
                collection.insert_many(documents)
        return json.dumps({"status": "success", "message": "Batch saved"})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

def saveAnswer(parameters: Dict[str, Any]) -> str:
    """
    Master function called by Gemini.
    Handles injected context and batch/single logic.
    """
    # 1. Extract Injected Context (from websocket handler)
    session_id = parameters.get("session_id", "unknown_sess")
    phone = parameters.get("phone", "unknown_phone")

    # 2. Check Batch vs Single
    answers_array = parameters.get("answers", [])
    answers_dict = parameters.get("answers_dict", {})

    if answers_array or answers_dict:
        # If dictionary provided but not array, convert values to list
        if answers_dict and not answers_array:
            answers_array = list(answers_dict.values())
        return _save_batch_answers(answers_array, answers_dict, session_id, phone)
    
    # 3. Default to Single Answer
    return _save_single_answer(parameters)

# --- 5. GEMINI TOOL SCHEMA ---
tools_config = [
    {
        "function_declarations": [
            {
                "name": "saveAnswer",
                "description": "Saves the user's answer(s) to the database and retrieves the next question. MUST be called after every answer.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "question_number": {"type": "INTEGER", "description": "Current Question ID"},
                        "user_answer": {"type": "STRING", "description": "User's exact spoken words"},
                        "answer": {"type": "STRING", "description": "The matched option text or 'Cevap vermedi'"},
                        "retry_count": {"type": "INTEGER", "description": "Number of retry attempts (0-2)"},
                        # Optional batch fields
                        "answers": {
                            "type": "ARRAY",
                            "items": {"type": "OBJECT"},
                            "description": "List of answer objects for batch processing"
                        }
                    },
                    "required": ["question_number", "user_answer", "answer", "retry_count"]
                }
            },
            {
                "name": "goBackQuestion",
                "description": "Returns the details of the previous question when the user wants to go back.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "current_q_no": {"type": "INTEGER", "description": "The current question number the user is on."}
                    },
                    "required": ["current_q_no"]
                }
            }
        ]
    }
]

# --- 6. SERVER & WEBSOCKET HANDLERS ---
@app.post("/incoming-call")
async def handle_incoming_call(request: Request):
    """Twilio Webhook: Instructions to connect to WebSocket."""
    host = request.url.hostname
    # We use a TwiML response to tell Twilio to open a media stream
    xml_response = f"""
    <Response>
        <Connect>
            <Stream url="wss://{host}/media-stream" />
        </Connect>
    </Response>
    """
    return Response(content=xml_response, media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Main WebSocket loop for Audio I/O."""
    await websocket.accept()
    print("--> WebSocket Connected")
    
    # Context generation (In production, get from Twilio 'start' msg)
    current_session = "sess_" + str(int(datetime.now().timestamp()))
    current_phone = "unknown"

    # Gemini Config
    config = {
        "response_modalities": ["AUDIO"],
        "tools": tools_config,
        "system_instruction": SYSTEM_INSTRUCTIONS,
        "speech_config": {
            "voice_config": {"prebuilt_voice_config": {"voice_name": "Charon"}}
        }
    }

    async with client.aio.live.connect(model=MODEL_ID, config=config) as session:
        print("--> Gemini Live Connected")
        
        # FIX: INIT SESSION TO 0, NOT 1
        SESSION_STATE[current_session] = 0
        await session.send_client_content(
            turns=[types.Content(parts=[types.Part(text="System: The call has started. Begin the survey now.")])],
            turn_complete=True
        )

        async def receive_from_client():
            nonlocal current_session
            try:
                while True:
                    msg = await websocket.receive_text()
                    data = json.loads(msg)

                    if data.get("event") == "media":
                        chunk = base64.b64decode(data["media"]["payload"])
                        await session.send_realtime_input(
                            audio=types.Blob(mime_type="audio/pcm", data=chunk)
                        )
                        
            except Exception as e:
                print(f"Client Receive Error: {e}")

        async def receive_from_gemini():
            try:
                while True:
                    async for response in session.receive():
                        # A. Audio Output
                        if response.server_content and response.server_content.model_turn:
                            for part in response.server_content.model_turn.parts:
                                if part.inline_data:
                                    # PURE PASSTHROUGH (No AudioOp Conversion for Local Test)
                                    # Send raw 24k PCM back to test_client
                                    payload = base64.b64encode(part.inline_data.data).decode("utf-8")
                                    await websocket.send_json({
                                        "event": "media",
                                        "media": {"payload": payload}
                                    })

                        # B. Tool Calls
                        if response.tool_call:
                            for fc in response.tool_call.function_calls:
                                
                                # HANDLE SAVE ANSWER
                                if fc.name == "saveAnswer":
                                    args = fc.args
                                    print(f"--> Tool Called: saveAnswer for Q{args.get('question_number')}")
                                    
                                    # INJECT CONTEXT
                                    full_params = {
                                        # Standard fields
                                        "question_number": int(args.get("question_number", 0)),
                                        "user_answer": args.get("user_answer", ""),
                                        "answer": args.get("answer", ""),
                                        "retry_count": int(args.get("retry_count", 0)),
                                        # Batch fields
                                        "answers": args.get("answers", []),
                                        "answers_dict": args.get("answers_dict", {}),
                                        # Injected fields
                                        "session_id": current_session,
                                        "phone": current_phone
                                    }
                                    
                                    # EXECUTE LOGIC
                                    result_json = saveAnswer(full_params)

                                    # RESPOND TO GEMINI using dedicated tool response API
                                    await session.send_tool_response(
                                        function_responses=[types.FunctionResponse(
                                            name=fc.name, id=fc.id, response=json.loads(result_json)
                                        )]
                                    )
                                
                                # FIX 2: HANDLE GO BACK QUESTION
                                elif fc.name == "goBackQuestion":
                                    args = fc.args
                                    print(f"--> Tool Called: goBackQuestion from Q{args.get('current_q_no')}")
                                    
                                    current_q = int(args.get("current_q_no", 0))
                                    result_json = goBackQuestion(current_q, current_session)
                                    
                                    await session.send_tool_response(
                                        function_responses=[types.FunctionResponse(
                                            name=fc.name, id=fc.id, response=json.loads(result_json)
                                        )]
                                    )
            except Exception as e:
                print(f"Gemini Receive Error: {e}")

        await asyncio.gather(receive_from_client(), receive_from_gemini())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)