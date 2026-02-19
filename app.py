from flask import Flask, render_template, request, redirect, session, jsonify
import random
import requests
from datetime import date
import re
import tempfile
import subprocess
import os
import psycopg2
import psycopg2.extras
from urllib.parse import urlparse
from flask_socketio import SocketIO
from flask_socketio import emit, join_room
import uuid

waiting_player = None
battle_rooms = {}




ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY")


# 🔑 PASTE YOUR NEW KEY HERE
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")


app = Flask(__name__)
app.secret_key = "dsa_secret"

socketio = SocketIO(app)


# ---------------- DATABASE ----------------
def get_db():
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise Exception("DATABASE_URL not found. Set it in Render environment variables.")

    result = urlparse(database_url)

    conn = psycopg2.connect(
        database=result.path[1:],
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port,
        sslmode="require"   # 🔥 Required for Render Postgres
    )

    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # ---------------- USERS TABLE ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        name TEXT,
        email TEXT UNIQUE,
        password TEXT,
        score INTEGER DEFAULT 0,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        weak TEXT DEFAULT '',
        streak INTEGER DEFAULT 0,
        last_daily TEXT DEFAULT ''
    );
    """)

    # ---------------- WEAK TOPICS TABLE ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS weak_topics (
        id SERIAL PRIMARY KEY,
        email TEXT,
        topic TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # ---------------- TOPIC PERFORMANCE TABLE ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS topic_performance (
        id SERIAL PRIMARY KEY,
        email TEXT,
        topic TEXT,
        score INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    cur.close()
    conn.close()


# 🔥 Initialize DB on startup
init_db()



# ---------------- HOME ----------------
@app.route("/")
def home():
    return render_template("about.html")

# ---------------- SIGNUP ----------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]

        try:
            conn = get_db()
            cur = conn.cursor()

            cur.execute(
                "INSERT INTO users(name,email,password) VALUES(%s,%s,%s)",
                (name, email, password)
            )

            conn.commit()
            cur.close()
            conn.close()

            return redirect("/login")

        except:
            return "User already exists"

    return render_template("signup.html")


# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "SELECT email FROM users WHERE email=%s AND password=%s",
            (email, password)
        )

        user = cur.fetchone()

        cur.close()
        conn.close()

        if user:
            session["user"] = user[0]
            return redirect("/dashboard")
        else:
            return "Invalid login"

    return render_template("login.html")



# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ---------------- DASHBOARD ----------------
@app.route("/dashboard")
def dashboard():

    if "user" not in session:
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    email = session["user"]

    # 🔹 Get name, score, weak
    cur.execute("""
        SELECT name, score, weak
        FROM users
        WHERE email=%s
    """, (email,))

    user_data = cur.fetchone()

    if user_data:
        name = user_data[0]
        score = user_data[1]
        weak = user_data[2]
    else:
        name = "User"
        score = 0
        weak = "None"

    # 🔹 Readiness (simple stable formula)
    readiness = min(100, int(score * 0.5))

    # 🔹 Most weak topic
    cur.execute("""
        SELECT topic, COUNT(*) 
        FROM weak_topics
        WHERE email=%s
        GROUP BY topic
        ORDER BY COUNT(*) DESC
        LIMIT 1
    """, (email,))

    row = cur.fetchone()
    recommended_topic = row[0] if row else None

    cur.close()
    conn.close()

    return render_template(
        "dashboard.html",
        user=name,   # ✅ now NAME not email
        score=score,
        weak=weak,
        readiness=readiness,
        recommended_topic=recommended_topic
    )



# ---------------- WEAK TOPICS ----------------
@app.route("/weak_topics")
def weak_topics_page():

    if "user" not in session:
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    email = session["user"]

    # 1️⃣ Get weak topic counts
    cur.execute("""
        SELECT topic, COUNT(*) 
        FROM weak_topics
        WHERE email=%s
        GROUP BY topic
        ORDER BY COUNT(*) DESC
    """, (email,))

    rows = cur.fetchall()
    max_count = max([row[1] for row in rows]) if rows else 1

    topics = []

    for row in rows:
        topic = row[0]
        count = row[1]

        # 2️⃣ Get real average score from topic_performance
        cur.execute("""
            SELECT AVG(score)
            FROM topic_performance
            WHERE email=%s AND topic=%s
        """, (email, topic))

        avg_score = cur.fetchone()[0]

        if avg_score is None:
            avg_score = 0

        improvement = int(avg_score * 10)  # Convert average (0–10) to percentage

        # 3️⃣ Mastery logic based on real improvement
        if improvement <= 40:
            mastery = "Beginner"
        elif improvement <= 70:
            mastery = "Intermediate"
        else:
            mastery = "Strong"

        percentage = int((count / max_count) * 100)

        topics.append({
            "topic": topic,
            "count": count,
            "improvement": improvement,
            "mastery": mastery,
            "percentage": percentage
        })

    cur.close()
    conn.close()

    most_weak = topics[0] if topics else None

    return render_template(
        "weak_topics.html",
        topics=topics,
        most_weak=most_weak
    )


# ---------------- TOPIC PLAN ----------------
@app.route("/topic_plan/<path:topic>")
def topic_plan(topic):

    if "user" not in session:
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()

    email = session["user"]

    cur.execute("""
        SELECT score
        FROM topic_performance
        WHERE email=%s AND topic=%s
        ORDER BY created_at DESC
        LIMIT 10
    """, (email, topic))

    score_rows = cur.fetchall()
    scores = [row[0] for row in score_rows][::-1]

    cur.close()
    conn.close()

    # ---------- ANALYTICS LOGIC ----------

    if scores:
        avg_score = round(sum(scores) / len(scores), 1)

        if len(scores) >= 2:
            if scores[-1] > scores[0]:
                trend = "Improving 📈"
            elif scores[-1] < scores[0]:
                trend = "Declining 📉"
            else:
                trend = "Stable ➖"
        else:
            trend = "Insufficient Data"

        consistency = round((sum(1 for s in scores if s >= 7) / len(scores)) * 100)

    else:
        avg_score = 0
        trend = "No Data"
        consistency = 0

    # ---------- AI PLAN ----------

    try:
        prompt = f"""
You are an expert DSA mentor.

A student is weak in: {topic}

Generate:
1. Why students struggle
2. 3 step improvement plan
3. Recommended patterns
4. Common mistakes
"""

        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openai/gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=60
        )

        data = response.json()
        reply = data["choices"][0]["message"]["content"]

    except:
        reply = "AI service temporarily unavailable."

    return render_template(
        "topic_plan.html",
        topic=topic,
        plan=reply,
        scores=scores,
        avg_score=avg_score,
        trend=trend,
        consistency=consistency
    )



# ---------------- CHAT PAGE ----------------
@app.route("/chat")
def chat():
    if "user" not in session:
        return redirect("/login")
    return render_template("chat.html")


# ---------------- FOCUS PRACTICE ----------------
@app.route("/focus_practice/<topic>")
def focus_practice(topic):

    if "user" not in session:
        return redirect("/login")

    session["focus_topic"] = topic
    return redirect("/practice/focus")



# ---------------- PRACTICE SELECT ----------------
@app.route("/practice_select")
def practice_select():
    return render_template("practice_select.html")

@app.route("/practice/<level>")
def practice_level(level):

    if "user" not in session:
        return redirect("/login")

    if level == "focus":
        session["level"] = "medium"   # focus mode default difficulty
    else:
        session["level"] = level
        session.pop("focus_topic", None)  # remove focus mode if normal

    return render_template("practice.html")


@app.route("/set_lang", methods=["POST"])
def set_lang():
    data = request.json
    session["lang"] = data["lang"]
    return jsonify({"ok":True})

# ---------------- GET AI QUESTION ----------------
@app.route("/get_question")
def get_question():

    level = session.get("level","easy")
    lang = session.get("lang","python")

    diff = "EASY" if level=="easy" else "MEDIUM" if level=="medium" else "HARD"
    language = "Python" if lang=="python" else "C++" if lang=="cpp" else "Java"

    prompt = f"Give 1 {diff} DSA coding question for {language}. Only question."

    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model":"openai/gpt-3.5-turbo",
                  "messages":[{"role":"user","content":prompt}]}
        )
        data = response.json()
        question = data["choices"][0]["message"]["content"]
    except:
        question = "Error generating question"

    return jsonify({"question": question})



# ---------- SMART PRACTICE CHECK ----------
@app.route("/check_answer", methods=["POST"])
def check_answer():

    if "user" not in session:
        return jsonify({"feedback": "Login first", "score": 0})

    user_code = request.json.get("answer")
    question = request.json.get("question")
    language = session.get("lang", "python")

    if not user_code:
        return jsonify({"feedback": "Write code first", "score": 0})

    prompt = f"""
You are a strict FAANG coding interviewer.

Programming Language: {language}

Question:
{question}

Student Code:
{user_code}

Analyze carefully and detect weakness based on:

1. Syntax Errors
2. Time Complexity
3. Logic Mistake
4. Edge Case Handling
5. Data Structure Usage

Reply EXACT format:

Score: X/10
Weak Topic: choose ONLY one from below exactly:
Syntax
Time Complexity
Logic
Edge Cases
Data Structures
General DSA

Feedback:
- What is correct
- What is wrong
- What should improve
"""

    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openai/gpt-3.5-turbo",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=60
        )

        data = response.json()
        reply = data["choices"][0]["message"]["content"]

        import re

        score_match = re.search(r'(\d+)/10', reply)
        score = int(score_match.group(1)) if score_match else 5

        weak_match = re.search(r'Weak Topic:\s*(.*)', reply)

        if weak_match:
            weak = weak_match.group(1).strip()
        else:
            weak = "General DSA"

        allowed_topics = [
            "Syntax",
            "Time Complexity",
            "Logic",
            "Edge Cases",
            "Data Structures",
            "General DSA"
        ]

        if weak not in allowed_topics:
            weak = "General DSA"

        xp_gain = score * 3

        conn = get_db()
        cur = conn.cursor()

        # 1️⃣ Update user score/xp + latest weak
        cur.execute(
            "UPDATE users SET score=score+%s, xp=xp+%s, weak=%s WHERE email=%s",
            (score, xp_gain, weak, session["user"])
        )

        # 2️⃣ Store performance history (VERY IMPORTANT)
        cur.execute(
            "INSERT INTO topic_performance (email, topic, score) VALUES (%s, %s, %s)",
            (session["user"], weak, score)
        )

        # 3️⃣ Recovery system
        if score >= 8:
            cur.execute("""
                DELETE FROM weak_topics
                WHERE id = (
                    SELECT id FROM weak_topics
                    WHERE email=%s AND topic=%s
                    LIMIT 1
                )
            """, (session["user"], weak))
        else:
            cur.execute(
                "INSERT INTO weak_topics (email, topic) VALUES (%s, %s)",
                (session["user"], weak)
            )

        conn.commit()
        cur.close()
        conn.close()

    except Exception as e:
        print("AI Error:", e)
        reply = "AI evaluation error"
        score = 0

    return jsonify({"feedback": reply, "score": score})



# ---------------- DAILY CHALLENGE ----------------
def generate_daily_question():
    prompt = "Give one medium DSA coding question for daily challenge. Only question."
    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                     "Content-Type":"application/json"},
            json={"model":"openai/gpt-3.5-turbo",
                  "messages":[{"role":"user","content":prompt}]}
        )
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except:
        return "Explain binary search."

@app.route("/daily")
def daily():
    if "user" not in session:
        return redirect("/login")

    today = str(date.today())

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        "SELECT * FROM users WHERE email=%s",
        (session["user"],)
    )

    user = cur.fetchone()

    if user["last_daily"] == today:
        msg = "You already solved today's challenge 🔥"
        q = None
    else:
        q = generate_daily_question()
        msg = None

    cur.close()
    conn.close()

    return render_template("daily.html", question=q, msg=msg, streak=user["streak"])


@app.route("/submit_daily",methods=["POST"])
def submit_daily():
    if "user" not in session:
        return jsonify({"reply":"login first"})

    today = str(date.today())

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        "SELECT * FROM users WHERE email=%s",
        (session["user"],)
    )

    user = cur.fetchone()

    if user["last_daily"] == today:
        cur.close()
        conn.close()
        return jsonify({"reply":"Already completed today"})

    new_streak = user["streak"] + 1
    bonus = 20 + (new_streak * 5)

    cur.execute("""
        UPDATE users
        SET score=score+%s, xp=xp+%s, streak=%s, last_daily=%s
        WHERE email=%s
    """,
        (bonus, bonus, new_streak, today, session["user"])
    )

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"reply":f"🔥 Daily completed! +{bonus} XP","streak":new_streak})



# ---------- VOICE GENERATION ----------
@app.route("/voice", methods=["POST"])
def voice():

    text = request.json["text"]

    try:
        url = "https://api.elevenlabs.io/v1/text-to-speech/EXAVITQu4vr4xnSDxMaL"

        headers = {
            "xi-api-key": ELEVEN_API_KEY,
            "Content-Type": "application/json"
        }

        data = {
            "text": text,
            "model_id": "eleven_monolingual_v1"
        }

        response = requests.post(url, json=data, headers=headers)

        with open("static/voice.mp3", "wb") as f:
            f.write(response.content)

        return jsonify({"audio":"/static/voice.mp3"})

    except Exception as e:
        return jsonify({"audio":None})



# ---------------- LEADERBOARD ----------------

@app.route("/leaderboard")
def leaderboard():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT name, score, xp FROM users ORDER BY score DESC")
    users = cur.fetchall()

    cur.close()
    conn.close()

    data = []
    rank = 1

    for u in users:
        rating = 0 + (u["score"] * 5) + (u["xp"] * 2)
        data.append({
            "rank": rank,
            "name": u["name"],
            "score": u["score"],
            "xp": u["xp"],
            "rating": rating
        })
        rank += 1

    return render_template("leaderboard.html", users=data)


@app.route("/interview_select")
def interview_select():
    return render_template("interview_select.html")

@app.route("/interview")
def interview():
    return render_template("interview.html")


# ---------------- INTERVIEW SYSTEM ----------------
@app.route("/start_interview", methods=["POST"])
def start_interview():
    data = request.json
    session["company"] = data["company"]
    session["round"] = 1
    session["score"] = 0
    return jsonify({"ok":True})

@app.route("/interview_ai", methods=["POST"])
def interview_ai():

    if "user" not in session:
        return jsonify({"reply":"login first"})

    msg = request.json["msg"]
    company = session.get("company","Google")
    round_no = session.get("round",1)
    score = session.get("score",0)

    # -------- START --------
    if msg == "start":
        session["score"] = 0
        session["round"] = 1
        q = f"Welcome to {company} interview. Tell me about yourself."
        session["last_question"] = q
        return jsonify({"reply":q})

    # -------- AI JUDGE BASED ON QUESTION + ANSWER --------
    last_q = session.get("last_question","")

    judge_prompt = f"""
You are a strict FAANG interviewer.

Evaluate based on QUESTION and ANSWER.

Give ONLY this format:
Score: X/10
Feedback: one short line only.

Question: {last_q}
Answer: {msg}

Rules:
Irrelevant → 1-2
Basic idea → 3-5
Good → 6-8
Excellent → 9-10
"""

    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type":"application/json"
            },
            json={
                "model":"openai/gpt-3.5-turbo",
                "messages":[{"role":"user","content":judge_prompt}]
            },
            timeout=40
        )

        data = response.json()
        judge_reply = data["choices"][0]["message"]["content"]

        import re
        match = re.search(r'(\d+)/10', judge_reply)
        gained = int(match.group(1)) if match else 4

    except:
        judge_reply = "Score: 5/10\nFeedback: Could not evaluate properly."
        gained = 5

    score += gained
    session["score"] = score

    # -------- NEXT QUESTIONS --------
    if round_no == 1:
        question = "Explain time complexity of binary search."
        session["round"] = 2
        session["last_question"] = question
        return jsonify({"reply": judge_reply + "\n\nNext Question:\n" + question})

    elif round_no == 2:
        question = "How would you reverse a linked list?"
        session["round"] = 3
        session["last_question"] = question
        return jsonify({"reply": judge_reply + "\n\nNext Question:\n" + question})

    elif round_no == 3:
        question = "Why should we hire you?"
        session["round"] = 4
        session["last_question"] = question
        return jsonify({"reply": judge_reply + "\n\nFinal Question:\n" + question})

    else:
        # -------- FINAL RESULT --------
        if score >= 28:
            result = "SELECTED 🎉"
        elif score >= 18:
            result = "WAITLIST 🟡"
        else:
            result = "REJECTED ❌"

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "UPDATE users SET score=score+%s, xp=xp+%s WHERE email=%s",
            (score, score, session["user"])
        )

        conn.commit()
        cur.close()
        conn.close()


        return jsonify({
            "reply": judge_reply + "\n\nInterview Finished.",
            "result": result,
            "score": score
        })


# -------- ADMIN PANEL --------
@app.route("/admin")
def admin():

    if "user" not in session:
        return redirect("/login")

    # Only you can access admin
    if session["user"] != "anshuraj02092006@gmail.com":
        return redirect("/dashboard")

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        "SELECT name, email, score, xp FROM users ORDER BY score DESC"
    )

    users = cur.fetchall()

    total_users = len(users)
    total_score = sum(u["score"] for u in users)
    total_xp = sum(u["xp"] for u in users)

    cur.close()
    conn.close()

    return render_template(
        "admin.html",
        users=users,
        total_users=total_users,
        total_score=total_score,
        total_xp=total_xp
    )

@app.route("/delete_user/<int:user_id>")
def delete_user(user_id):

    if "user" not in session:
        return redirect("/login")

    if session["user"] != "anshuraj02092006@gmail.com":
        return redirect("/dashboard")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
    conn.commit()

    cur.close()
    conn.close()

    return redirect("/admin")


# ---------------- BATTLE QUESTION GENERATOR ----------------

def generate_battle_question(language):

    prompt = f"""
Generate one DSA coding problem for {language}.

Return STRICT JSON in this format:

{{
 "question": "problem statement",
 "tests": [
   {{"input": "input1", "output": "expected1"}},
   {{"input": "input2", "output": "expected2"}},
   {{"input": "input3", "output": "expected3"}}
 ]
}}

Only return JSON. No explanation.
"""

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "openai/gpt-3.5-turbo",
            "messages": [{"role": "user", "content": prompt}]
        }
    )

    data = response.json()

    try:
        content = data["choices"][0]["message"]["content"]
        import json
        return json.loads(content)
    except:
        # fallback question if AI fails
        return {
            "question": "Reverse an array.",
            "tests": [
                {"input": "1 2 3 4 5", "output": "5 4 3 2 1"}
            ]
        }



# -------- BATTLE MODE --------

waiting_players = {
    "python": None,
    "java": None,
    "cpp": None
}

battle_rooms = {}
battle_timers = {}


@socketio.on("join_battle")
def handle_join(data):
    global waiting_players

    language = data.get("language")

    if language not in waiting_players:
        language = "python"

    if waiting_players[language] is None:
        waiting_players[language] = request.sid
        emit("waiting")
    else:
        room_id = str(uuid.uuid4())

        question_data = generate_battle_question(language)

        battle_rooms[room_id] = {
            "players": [waiting_players[language], request.sid],
            "submissions": {},
            "language": language,
            "question": question_data["question"],
            "tests": question_data["tests"]
        }

        battle_timers[room_id] = 1200

        join_room(room_id, sid=waiting_players[language])
        join_room(room_id)

        # ONLY send room id (question template se load hoga)
        socketio.emit("battle_start", {
            "room": room_id
        }, room=room_id)

        socketio.start_background_task(start_timer, room_id)

        waiting_players[language] = None



@app.route("/battle_test")
def battle_test():
    return render_template("battle_test.html")


# ---------------- BATTLE ROOM ----------------

@app.route("/battle_room/<room_id>")
def battle_room(room_id):

    room_data = battle_rooms.get(room_id)

    if not room_data:
        return "Room not found"

    return render_template(
        "battle_room.html",
        room_id=room_id,
        question=room_data["question"],
        language=room_data["language"]
    )


# ---------------- JOIN ROOM ----------------

@socketio.on("join_room")
def handle_room(data):
    room = data.get("room")

    join_room(room)

    print("JOINED ROOM:", room)
    print("CURRENT SID:", request.sid)



# ---------------- SUBMIT CODE ----------------

@socketio.on("submit_code")
def handle_submit(data):
    room = data.get("room")
    code = data.get("code")

    print("SUBMIT FROM:", request.sid)
    print("ROOM:", room)

    battle_rooms[room]["submissions"][request.sid] = code

    print("COUNT:", len(battle_rooms[room]["submissions"]))



    # notify opponent
    emit("opponent_submitted", room=room, skip_sid=request.sid)


    # if both submitted
    if len(battle_rooms[room]["submissions"]) == 2:
        socketio.start_background_task(judge_battle, room)


# ---------------- TIMER ENGINE ----------------

def start_timer(room_id):
    while True:

        # STOP if room deleted
        if room_id not in battle_timers:
            break

        if battle_timers[room_id] <= 0:
            break

        socketio.sleep(1)
        battle_timers[room_id] -= 1

        socketio.emit("timer_update", {
            "time": battle_timers[room_id]
        }, room=room_id)

    # If timer ended naturally → judge
    if room_id in battle_rooms:
        judge_battle(room_id)



# ---------------- AI JUDGE ----------------

# ---------------- REAL TEST CASE JUDGE ----------------
def judge_battle(room_id):

    room_data = battle_rooms[room_id]
    submissions = room_data["submissions"]
    tests = room_data["tests"]

    sids = list(submissions.keys())

    if len(sids) < 2:
        winner = sids[0]
    else:
        p1, p2 = sids[0], sids[1]

        score1 = run_tests(submissions[p1], tests)
        score2 = run_tests(submissions[p2], tests)

        print("SCORE1:", score1)
        print("SCORE2:", score2)

        if score1 > score2:
            winner = p1
        elif score2 > score1:
            winner = p2
        else:
            winner = None

    for sid in sids:
        socketio.emit("battle_result", {
            "winner": winner,
            "your_id": sid
        }, room=sid)
    battle_timers.pop(room_id, None)



def run_tests(code, tests):

    score = 0

    # 🔥 auto detect function name
    match = re.search(r'def\s+(\w+)\s*\(', code)
    if not match:
        return 0

    function_name = match.group(1)

    for test in tests:

        wrapped_code = f"""
{code}

if __name__ == "__main__":
    import sys
    data = sys.stdin.read().strip()
    numbers = list(map(int, data.split()))
    result = {function_name}(numbers)
    print(result)
"""

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as f:
                f.write(wrapped_code.encode())
                file_name = f.name

            process = subprocess.run(
                ["python", file_name],
                input=str(test["input"]),
                text=True,
                capture_output=True,
                timeout=5
            )

            output = process.stdout.strip()
            expected = str(test["output"]).strip()

            if output == expected:
                score += 1

        except Exception as e:
            print("Judge Error:", e)

        finally:
            try:
                os.remove(file_name)
            except:
                pass

    return score


# ---------------- RUN ----------------
if __name__ == "__main__":
    socketio.run(app, debug=True)

