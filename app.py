import streamlit as st
import google.generativeai as genai
import os
import json
from datetime import datetime
import pytz
import requests
import streamlit.components.v1 as components
from gtts import gTTS
from io import BytesIO
import tempfile
import base64
import hashlib
import re
from googletrans import Translator  # ✅ added for transliteration
import sqlite3
import uuid
import socket
import datetime as dt
import time
import random

# -----------------------------
# CONFIGURATION
# -----------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or "YOUR_GEMINI_API_KEY"
SERPER_API_KEY = os.getenv("SERPER_API_KEY") or "YOUR_SERPER_API_KEY"
genai.configure(api_key=GEMINI_API_KEY)

# Set timezone to Asia/Kolkata
india_tz = pytz.timezone("Asia/Kolkata")

# -----------------------------
# LOCAL DATABASE FUNCTIONS
# -----------------------------
DB_PATH = "userlog.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            name TEXT,
            session_id TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_user_to_db(name, session_id ):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO user (timestamp, name, session_id) VALUES (?, ?, ?)",
              (datetime.now(india_tz).strftime("%Y-%m-%d %H:%M:%S"), name, session_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT timestamp, name, session_id FROM user ORDER BY id DESC")
    data = c.fetchall()
    conn.close()
    return data

init_db()


# -----------------------------
# GENERAL SETTINGS
# -----------------------------
BOT_NAME = "Neha"
MEMORY_DIR = "user_memories"
os.makedirs(MEMORY_DIR, exist_ok=True)
translator = Translator()  # ✅ initialize once

# -----------------------------
# SESSION-BASED MEMORY FUNCTIONS
# -----------------------------
def get_user_id():
    session_id = st.session_state.get("session_id")
    if not session_id:
        raw = str(st.session_state) + str(datetime.now())
        session_id = hashlib.md5(raw.encode()).hexdigest()[:10]
        st.session_state.session_id = session_id
    return session_id

def get_memory_file():
    user_id = get_user_id()
    return os.path.join(MEMORY_DIR, f"{user_id}.json")

def load_memory():
    mem_file = get_memory_file()
    if os.path.exists(mem_file):
        with open(mem_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "user_name": None,
        "gender": None,
        "chat_history": [],
        "facts": [],
        "timezone": "Asia/Kolkata"
    }

def save_memory(memory):
    mem_file = get_memory_file()
    with open(mem_file, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)

# -----------------------------
# OTHER FUNCTIONS
# -----------------------------
def remember_user_info(memory, user_input):
    text = user_input.lower()
    for phrase in ["mera naam", "i am ", "this is ", "my name is "]:
        if phrase in text:
            try:
                name = text.split(phrase)[1].split()[0].title()
                memory["user_name"] = name
                break
            except:
                pass
    if any(x in text for x in ["i am male", "main ladka hoon", "boy", "man"]):
        memory["gender"] = "male"
    elif any(x in text for x in ["i am female", "main ladki hoon", "girl", "woman"]):
        memory["gender"] = "female"
    save_memory(memory)

def get_now(memory):
    tz_name = memory.get("timezone", "Asia/Kolkata")
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.timezone("Asia/Kolkata")
    return datetime.now(tz).strftime("%A, %d %B %Y %I:%M %p")

def web_search(query):
    if not SERPER_API_KEY:
        return "Live search unavailable."
    try:
        headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
        data = {"q": query}
        r = requests.post("https://google.serper.dev/search", headers=headers, json=data, timeout=12)
        results = r.json()
        if "knowledge" in results and results["knowledge"].get("description"):
            return results["knowledge"]["description"]
        if "organic" in results and results["organic"]:
            return results["organic"][0].get("snippet", "Kuch result nahi mila.")
        return "Kuch relevant result nahi mila 😅"
    except Exception as e:
        return f"Search failed: {e}"

def summarize_profile(memory):
    parts = []
    if memory.get("user_name"):
        parts.append(f"User ka naam {memory['user_name']} hai.")
    if memory.get("facts"):
        parts.append("Recent info: " + "; ".join(memory["facts"][-3:]))
    return " ".join(parts) or "User ke baare mein abhi zyada info nahi hai."

def build_system_prompt(memory):
    now = get_now(memory)
    gender_style = ""
    if memory.get("gender") == "male":
        gender_style = "User male hai, tone slightly neutral rakho."
    elif memory.get("gender") == "female":
        gender_style = "User female hai, tone thoda formal and warm rakho."
    return (
        f"Tum ek friendly female Hinglish chatbot ho jiska naam {BOT_NAME} hai. "
        "Tumhara tone conversational, modern, warm lekin tum short me baat karti ho. "
        "Tum 30 saal ki ho aur Delhi me rahti ho. Respond according to Delhi time zone. "
        "Don't show you are a chatbot, assume imaginary personality of any random female with common interests, life profile etc. "
        "Simple Hindi(roman) aur English mix mein baat karo. Don't use words like - yaar, mast, chill, vibe etc. frequently. "
        "Kabhi bhi user ke gender ko directly mat mention karo. "
        "Do not repeat anything unless asked. Never use pronoun 'tu'. "
        f"Aaj ka date aur time hai {now}. "
        f"{summarize_profile(memory)} {gender_style}"
    )

def summarize_old_memory(memory):
    if len(memory.get("chat_history", [])) < 10:
        return memory
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        past_text = "\n".join(
            [f"User: {c['user']}\n{BOT_NAME}: {c['bot']}" for c in memory["chat_history"][-10:]]
        )
        result = model.generate_content(
            "Summarize key user facts in 3 short Hinglish bullets:\n" + past_text
        )
        summary = (result.text or "").strip()
        if summary:
            memory.setdefault("facts", []).append(summary)
            memory["chat_history"] = memory["chat_history"][-8:]
            save_memory(memory)
    except Exception as e:
        print(f"[Memory summarization error: {e}]")
    return memory


# ----------------------------------
# ONLY THIS IS CHANGED ⬇️
# ----------------------------------
def safe_gemini_call(prompt, model_name="gemini-2.5-flash", max_retries=5):
    for attempt in range(max_retries):
        try:
            model = genai.GenerativeModel(model_name)
            result = model.generate_content(prompt)
            return result.text.strip()
        except Exception as e:
            err = str(e)
            if "429" not in err:
                return f"[Error] {err}"
            
            wait = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(wait)

    return "Speech service is down temporarily. Please check again!"
# ----------------------------------


def generate_reply(memory, user_input):
    if not user_input.strip():
        return "Kuch toh bolo! 😄"
    remember_user_info(memory, user_input)
    if any(w in user_input.lower() for w in ["news", "weather", "price", "rate", "update"]):
        info = web_search(user_input)
        return f"Mujhe live search se pata chala: {info}"

    context = "\n".join(
        [f"You: {c['user']}\n{BOT_NAME}: {c['bot']}" for c in memory.get("chat_history", [])[-8:]]
    )
    prompt = f"{build_system_prompt(memory)}\n\nConversation:\n{context}\n\nYou: {user_input}\n{BOT_NAME}:"

    try:
        # ----------------------------------
        # ONLY THIS IS CHANGED ⬇️
        model = genai.GenerativeModel("gemini-2.5-flash")
        reply = safe_gemini_call(prompt)
        # ----------------------------------

    except Exception as e:
        reply = f"Oops! Thoda issue aaya: {e}"

    memory.setdefault("chat_history", []).append({"user": user_input, "bot": reply})
    if len(memory["chat_history"]) % 20 == 0:
        summarize_old_memory(memory)
    save_memory(memory)
    return reply

# -----------------------------
# STREAMLIT UI
# -----------------------------
st.set_page_config(page_title="Neha – Your Hinglish AI Friend", page_icon="💬")

st.markdown("""
<style>
  .stApp { background-color: #e5ddd5; font-family: 'Roboto', sans-serif !important; }
  h1 { text-align: center; font-weight: 500; font-size: 16px; margin-top: -10px; }
  iframe { margin: 1px 0 !important; }
</style>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet">
""", unsafe_allow_html=True)

st.markdown("""
<h1 style="
    text-align:center;
    font-family:'Roboto', sans-serif;
    font-weight:500;
    font-size:22px;
    margin-top:-10px;
">
💬 Neha – Your Hinglish AI Friend by Hindi Hour
</h1>
""", unsafe_allow_html=True)

# ✅ Load memory
if "memory" not in st.session_state:
    st.session_state.memory = load_memory()

memory = st.session_state.memory

# ✅ Step 1: Ask for user name before chat starts
if not memory.get("user_name"):
    name = st.text_input("Your Name:")
    if st.button("Start Chat"):
        if name.strip():
            memory["user_name"] = name.strip().title()
            save_memory(memory)
            save_user_to_db(memory["user_name"], get_user_id())
            st.rerun()
        else:
            st.warning("Please enter your name to continue.")
    st.stop()

# ✅ Step 2: If name already stored, show chat UI
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": f"Namaste {memory['user_name']}! 😊 Main Neha hun. Main Hinglish me baat kar sakti hun."}
    ]

for msg in st.session_state.messages:
    role = "user" if msg["role"] == "user" else "bot"
    name = "You" if role == "user" else "Neha"
    bubble_html = f"""
    <div style='
        background-color: {"#dcf8c6" if role=="user" else "#ffffff"};
        padding: 8px 14px;
        border-radius: 14px;
        max-width: 78%;
        margin: 4px 0;
        font-size: 15px;
        color: #111111;
        line-height: 1.4;
        box-shadow: 0 1px 2px rgba(0,0,0,0.08);
    '>
        <b>{name}:</b> {msg["content"]}
    </div>
    """
    st.markdown(bubble_html, unsafe_allow_html=True)

    if role == "bot":
        try:
            clean_text = re.sub(r'[^\w\s,-?.!]', '', msg["content"])
            tts = gTTS(text=clean_text, lang="hi", tld='co.in', slow=False)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
                tts.save(fp.name)
                audio_bytes = open(fp.name, "rb").read()
                audio_base64 = base64.b64encode(audio_bytes).decode()
                st.markdown(
                    f"""
                    <audio controls style='margin-top:-6px;'>
                        <source src="data:audio/mp3;base64,{audio_base64}" type="audio/mp3">
                    </audio>
                    """,
                    unsafe_allow_html=True
                )
        except Exception as e:
            st.warning(f"Speech issue: Service Down")

user_input = st.chat_input("Type your message here...")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.spinner("Neha type kar rahi hai... 💭"):
        reply = generate_reply(memory, user_input)
    if reply and reply.strip().lower().startswith("neha:"):
        reply = reply.split(":", 1)[1].strip()
    st.session_state.messages.append({"role": "assistant", "content": reply})
    save_memory(memory)
    st.rerun()







