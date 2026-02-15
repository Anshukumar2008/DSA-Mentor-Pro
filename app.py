from flask import Flask, render_template, request, redirect, session, jsonify
import sqlite3
import random
import requests
from datetime import date
import os


ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY")


# 🔑 PASTE YOUR NEW KEY HERE
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")



app = Flask(__name__)
app.secret_key = "dsa_secret"

# ---------------- DATABASE ----------------
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE,
        password TEXT,
        score INTEGER DEFAULT 0,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        weak TEXT DEFAULT 'None',
        streak INTEGER DEFAULT 0,
        last_daily TEXT DEFAULT ''
    )""")

    conn.commit()
    conn.close()

init_db()

# ---------------- HOME ----------------
@app.route("/")
def home():
    return render_template("about.html")

# ---------------- SIGNUP ----------------
@app.route("/signup", methods=["GET","POST"])
def signup():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]

        try:
            conn = get_db()
            conn.execute("INSERT INTO users(name,email,password) VALUES(?,?,?)",
                         (name,email,password))
            conn.commit()
            conn.close()
            return redirect("/login")
        except:
            return "User already exists"

    return render_template("signup.html")

# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=? AND password=?",
                            (email,password)).fetchone()
        conn.close()

        if user:
            session["user"] = user["email"]
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
    user = conn.execute("SELECT * FROM users WHERE email=?",
                        (session["user"],)).fetchone()
    conn.close()

    return render_template("dashboard.html",
                           user=user["name"],
                           score=user["score"],
                           weak=user["weak"])

# ---------------- CHAT AI ----------------
@app.route("/chat")
def chat():
    if "user" not in session:
        return redirect("/login")
    return render_template("chat.html")

@app.route("/ask", methods=["POST"])
def ask():
    msg = request.json["message"]

    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openai/gpt-3.5-turbo",
                "messages": [
                    {"role": "system", "content": "You are a DSA mentor."},
                    {"role": "user", "content": msg}
                ]
            }
        )
        data = response.json()
        reply = data["choices"][0]["message"]["content"]
    except:
        reply = "AI error"

    return jsonify({"reply": reply})

# ---------------- PRACTICE SELECT ----------------
@app.route("/practice_select")
def practice_select():
    return render_template("practice_select.html")

@app.route("/practice/<level>")
def practice_level(level):
    if "user" not in session:
        return redirect("/login")
    session["level"] = level
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
        return jsonify({"feedback":"Login first","score":0})

    user_code = request.json.get("answer")
    question = request.json.get("question")

    if not user_code:
        return jsonify({"feedback":"Write code first","score":0})

    prompt = f"""
You are a strict FAANG coding interviewer.

Question:
{question}

Student Code:
{user_code}

Reply EXACT format:

Score: X/10
Weak Topic: topic
Feedback:
- correct
- wrong
- improve
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
                "messages":[{"role":"user","content":prompt}]
            },
            timeout=60
        )

        data = response.json()
        reply = data["choices"][0]["message"]["content"]

        import re
        score_match = re.search(r'(\d+)/10', reply)
        score = int(score_match.group(1)) if score_match else 5

        weak_match = re.search(r'Weak Topic:\s*(.*)', reply)
        weak = weak_match.group(1).strip() if weak_match else "DSA"

        xp_gain = score * 3

        conn = get_db()
        conn.execute(
            "UPDATE users SET score=score+?, xp=xp+?, weak=? WHERE email=?",
            (score, xp_gain, weak, session["user"])
        )
        conn.commit()
        conn.close()

    except:
        reply = "AI evaluation error"
        score = 0

    return jsonify({"feedback":reply,"score":score})



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

    today=str(date.today())
    conn=get_db()
    user=conn.execute("SELECT * FROM users WHERE email=?",
                      (session["user"],)).fetchone()

    if user["last_daily"]==today:
        msg="You already solved today's challenge 🔥"
        q=None
    else:
        q=generate_daily_question()
        msg=None

    conn.close()
    return render_template("daily.html",question=q,msg=msg,streak=user["streak"])

@app.route("/submit_daily",methods=["POST"])
def submit_daily():
    if "user" not in session:
        return jsonify({"reply":"login first"})

    today=str(date.today())
    conn=get_db()
    user=conn.execute("SELECT * FROM users WHERE email=?",
                      (session["user"],)).fetchone()

    if user["last_daily"]==today:
        return jsonify({"reply":"Already completed today"})

    new_streak=user["streak"]+1
    bonus=20+(new_streak*5)

    conn.execute("""UPDATE users 
    SET score=score+?, xp=xp+?, streak=?, last_daily=? 
    WHERE email=?""",
                 (bonus,bonus,new_streak,today,session["user"]))
    conn.commit()
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
    conn=get_db()
    users=conn.execute("SELECT name,score,xp FROM users ORDER BY score DESC").fetchall()
    conn.close()

    data=[]
    rank=1
    for u in users:
        rating=800+(u["score"]*5)+(u["xp"]*2)
        data.append({"rank":rank,"name":u["name"],
                     "score":u["score"],"xp":u["xp"],"rating":rating})
        rank+=1

    return render_template("leaderboard.html",users=data)


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
        conn.execute("UPDATE users SET score=score+?, xp=xp+? WHERE email=?",
                     (score,score,session["user"]))
        conn.commit()
        conn.close()

        return jsonify({
            "reply": judge_reply + "\n\nInterview Finished.",
            "result": result,
            "score": score
        })


# -------- ADMIN PANEL --------
@app.route("/admin")
def admin():

    # login check
    if "user" not in session:
        return redirect("/login")

    # ONLY YOU CAN OPEN ADMIN
    if session["user"] != "anshuraj02092006@gmail.com":
        return redirect("/dashboard")   # admin exist bhi nahi dikhega

    conn=get_db()
    users=conn.execute("SELECT * FROM users ORDER BY score DESC").fetchall()

    total_users=len(users)
    total_score=sum([u["score"] for u in users])
    total_xp=sum([u["xp"] for u in users])

    conn.close()

    return render_template("admin.html",
                           users=users,
                           total_users=total_users,
                           total_score=total_score,
                           total_xp=total_xp)




# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(debug=True)



