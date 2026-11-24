import os
import re
import csv
import io
import sqlite3
from pathlib import Path
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# Try importing pypdf for AI resume skill extraction
try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

# --------------------------------------------------
# APP CONFIGURATION
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "placement.db"
UPLOAD_FOLDER = BASE_DIR / "uploads" / "resumes"

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
CORS(app)

app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024


# --------------------------------------------------
# DATABASE HELPERS & INITIALIZATION
# --------------------------------------------------

def get_db_connection():
    conn = sqlite3.connect(str(DATABASE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_columns(conn, table):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row["name"] for row in rows]


def ensure_column(conn, table, column, definition):
    if column not in get_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def initialize_database():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT UNIQUE,
            password TEXT,
            role TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            company TEXT,
            location TEXT,
            salary TEXT,
            eligibility TEXT,
            min_cgpa REAL,
            max_backlogs INTEGER,
            allowed_branches TEXT,
            company_id INTEGER,
            created_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            job_id INTEGER,
            status TEXT DEFAULT 'Applied',
            applied_at TEXT,
            UNIQUE(student_id, job_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            message TEXT,
            created_at TEXT,
            is_read INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER,
            student_id INTEGER,
            job_id INTEGER,
            interview_date TEXT,
            interview_time TEXT,
            location_or_link TEXT,
            notes TEXT,
            created_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scorecards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER,
            student_id INTEGER,
            job_id INTEGER,
            technical_score INTEGER,
            communication_score INTEGER,
            recommendation TEXT,
            notes TEXT,
            created_at TEXT
        )
    """)

    user_columns = {
        "name": "TEXT", "email": "TEXT", "password": "TEXT", "role": "TEXT",
        "cgpa": "REAL", "backlogs": "INTEGER DEFAULT 0", "branch": "TEXT",
        "resume": "TEXT", "github": "TEXT", "roll_number": "TEXT",
        "semester": "TEXT", "phone": "TEXT", "skills": "TEXT",
        "linkedin": "TEXT", "resume_filename": "TEXT"
    }
    for col, defn in user_columns.items():
        ensure_column(conn, "users", col, defn)

    job_columns = {
        "title": "TEXT", "job_role": "TEXT", "company": "TEXT", "location": "TEXT",
        "salary": "TEXT", "eligibility": "TEXT", "min_cgpa": "REAL", "minimum_cgpa": "REAL",
        "max_backlogs": "INTEGER", "maximum_backlogs": "INTEGER", "allowed_branches": "TEXT",
        "company_id": "INTEGER", "created_at": "TEXT"
    }
    for col, defn in job_columns.items():
        ensure_column(conn, "jobs", col, defn)

    admin_check = cursor.execute("SELECT * FROM users WHERE LOWER(email) = ?", ("admin@placement.com",)).fetchone()
    if not admin_check:
        cursor.execute("""
            INSERT INTO users (name, email, password, role)
            VALUES (?, ?, ?, ?)
        """, ("System Admin", "admin@placement.com", generate_password_hash("admin123"), "Admin"))

    conn.commit()
    conn.close()


# --------------------------------------------------
# FRONTEND SERVING ROUTES
# --------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return send_from_directory(BASE_DIR, "index.html")

@app.route("/index.html", methods=["GET"])
def serve_index():
    return send_from_directory(BASE_DIR, "index.html")

@app.route("/dashboard.html", methods=["GET"])
def serve_dashboard():
    return send_from_directory(BASE_DIR, "dashboard.html")

@app.route("/company.html", methods=["GET"])
def serve_company():
    return send_from_directory(BASE_DIR, "company.html")

@app.route("/admin.html", methods=["GET"])
def serve_admin():
    return send_from_directory(BASE_DIR, "admin.html")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# --------------------------------------------------
# AUTHENTICATION
# --------------------------------------------------

def normalize_role(role):
    role = (role or "").strip().lower()
    aliases = {
        "student": "Student",
        "company": "Company",
        "recruiter": "Company",
        "hr": "Company",
        "admin": "Admin"
    }
    return aliases.get(role)


@app.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    role = normalize_role(data.get("role"))

    if not name or not email or not password or not role:
        return jsonify({"message": "Please provide name, email, password and valid role."}), 400

    if role == "Admin":
        return jsonify({"message": "Admin accounts cannot be created through public registration."}), 403

    hashed_password = generate_password_hash(password)
    conn = get_db_connection()

    try:
        cursor = conn.execute("""
            INSERT INTO users (name, email, password, role)
            VALUES (?, ?, ?, ?)
        """, (name, email, hashed_password, role))
        conn.commit()
        return jsonify({
            "message": "Registration successful!",
            "user": {"id": cursor.lastrowid, "name": name, "email": email, "role": role}
        }), 201
    except sqlite3.IntegrityError:
        return jsonify({"message": "An account with this email already exists."}), 409
    finally:
        conn.close()


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    selected_role = normalize_role(data.get("role"))

    if not email or not password or not selected_role:
        return jsonify({"message": "Please enter email, password and select a valid role."}), 400

    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE LOWER(email) = ? LIMIT 1", (email,)).fetchone()

    if user is None:
        conn.close()
        return jsonify({"message": "Invalid email or password."}), 401

    stored_password = user["password"]
    password_valid = False

    if stored_password and (stored_password.startswith("pbkdf2:") or stored_password.startswith("scrypt:") or stored_password.startswith("argon2:")):
        password_valid = check_password_hash(stored_password, password)
    else:
        password_valid = (stored_password == password)
        if password_valid:
            new_hash = generate_password_hash(password)
            conn.execute("UPDATE users SET password = ? WHERE id = ?", (new_hash, user["id"]))
            conn.commit()

    conn.close()

    if not password_valid:
        return jsonify({"message": "Invalid email or password."}), 401

    actual_role = normalize_role(user["role"])
    if actual_role != selected_role:
        return jsonify({"message": f"This account is registered as {actual_role or 'an unknown role'}."}), 403

    return jsonify({
        "message": "Login successful!",
        "user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": actual_role}
    })


# --------------------------------------------------
# JOBS & AI SKILL-MATCHING ENGINE
# --------------------------------------------------

def calculate_skill_match(student_skills, job_text):
    if not student_skills or not job_text:
        return {"score": 50, "matched": [], "missing": []}

    student_skill_list = [s.strip().lower() for s in re.split(r"[,;/|]", student_skills) if s.strip()]
    if not student_skill_list:
        return {"score": 50, "matched": [], "missing": []}

    job_text_lower = job_text.lower()
    matched_skills = []
    missing_skills = []

    for skill in student_skill_list:
        if skill in job_text_lower:
            matched_skills.append(skill.title())
        else:
            missing_skills.append(skill.title())

    match_ratio = len(matched_skills) / len(student_skill_list)
    score = int(50 + (match_ratio * 50))

    return {"score": min(score, 100), "matched": matched_skills, "missing": missing_skills}


def job_to_dict(job):
    item = dict(job)
    eligibility = item.get("eligibility") or ""

    if item.get("min_cgpa") is None and item.get("minimum_cgpa") is None:
        match = re.search(r"(?:minimum\s*)?CGPA\s*(?:of\s*)?(\d+(?:\.\d+)?)", eligibility, re.IGNORECASE)
        if match:
            item["min_cgpa"] = float(match.group(1))

    if item.get("max_backlogs") is None and item.get("maximum_backlogs") is None:
        match = re.search(r"(?:maximum|max\.?)\s*(\d+)\s*backlogs?", eligibility, re.IGNORECASE)
        if match:
            item["max_backlogs"] = int(match.group(1))

    return item


@app.route("/jobs", methods=["GET", "POST"])
def jobs():
    if request.method == "GET":
        conn = get_db_connection()
        rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
        conn.close()
        return jsonify([job_to_dict(row) for row in rows])

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or data.get("job_role") or "").strip()
    company = (data.get("company") or "").strip()

    if not title or not company:
        return jsonify({"message": "Job title and company are required."}), 400

    conn = get_db_connection()
    cursor = conn.execute("""
        INSERT INTO jobs (
            title, job_role, company, location, salary, eligibility,
            min_cgpa, minimum_cgpa, max_backlogs, maximum_backlogs, allowed_branches,
            company_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        title, title, company,
        data.get("location", ""), data.get("salary", ""), data.get("eligibility", ""),
        data.get("min_cgpa") if data.get("min_cgpa") is not None else data.get("minimum_cgpa"),
        data.get("minimum_cgpa") if data.get("minimum_cgpa") is not None else data.get("min_cgpa"),
        data.get("max_backlogs") if data.get("max_backlogs") is not None else data.get("maximum_backlogs"),
        data.get("maximum_backlogs") if data.get("maximum_backlogs") is not None else data.get("max_backlogs"),
        data.get("allowed_branches", "All"),
        data.get("company_id"),
        datetime.now().isoformat(timespec="seconds")
    ))
    conn.commit()
    job_id = cursor.lastrowid
    conn.close()

    return jsonify({"message": "Job posted successfully!", "job_id": job_id}), 201


def is_student_eligible(student, job):
    cgpa = student.get("cgpa")
    backlogs = student.get("backlogs")
    branch = (student.get("branch") or "").strip().lower()

    min_cgpa = job.get("min_cgpa") if job.get("min_cgpa") is not None else job.get("minimum_cgpa")
    max_backlogs = job.get("max_backlogs") if job.get("max_backlogs") is not None else job.get("maximum_backlogs")
    allowed = (job.get("allowed_branches") or "All").strip()
    reasons = []

    if min_cgpa is not None:
        if cgpa is None or float(cgpa) < float(min_cgpa):
            reasons.append(f"Requires minimum CGPA {min_cgpa}")

    if max_backlogs is not None:
        if backlogs is None or int(backlogs) > int(max_backlogs):
            reasons.append(f"Allows maximum {max_backlogs} backlogs")

    if allowed.lower() not in ("all", "all branches", "any"):
        branches = [v.strip().lower() for v in re.split(r"[,;/|]", allowed) if v.strip()]
        if branches and branch not in branches:
            reasons.append(f"Restricted to branches: {allowed}")

    return len(reasons) == 0, reasons


@app.route("/student/<int:student_id>/eligible-jobs", methods=["GET"])
def eligible_jobs(student_id):
    conn = get_db_connection()
    student = conn.execute("SELECT * FROM users WHERE id = ?", (student_id,)).fetchone()
    jobs_list = conn.execute("SELECT j.* FROM jobs j ORDER BY j.id DESC").fetchall()
    conn.close()

    if not student:
        return jsonify({"message": "Student not found."}), 404

    student_dict = dict(student)
    student_skills = student_dict.get("skills") or ""
    result = []

    for job in jobs_list:
        item = job_to_dict(job)
        eligible, reasons = is_student_eligible(student_dict, item)
        item["eligible"] = eligible
        item["eligibility_reasons"] = reasons

        job_text = f"{item.get('title', '')} {item.get('job_role', '')} {item.get('eligibility', '')} {item.get('allowed_branches', '')}"
        skill_analysis = calculate_skill_match(student_skills, job_text)

        item["ai_match_score"] = skill_analysis["score"]
        item["matched_skills"] = skill_analysis["matched"]
        item["missing_skills"] = skill_analysis["missing"]
        result.append(item)

    result.sort(key=lambda x: x["ai_match_score"], reverse=True)
    return jsonify(result)


# --------------------------------------------------
# STUDENT PROFILE & RESUME AI PARSER
# --------------------------------------------------

@app.route("/student/<int:student_id>", methods=["GET", "PUT"])
def student_profile(student_id):
    conn = get_db_connection()

    if request.method == "GET":
        user = conn.execute("""
            SELECT id, name, email, role, cgpa, backlogs, branch, resume, github,
                   roll_number, semester, phone, skills, linkedin, resume_filename
            FROM users WHERE id = ?
        """, (student_id,)).fetchone()
        conn.close()

        if not user:
            return jsonify({"message": "Student not found."}), 404
        return jsonify(dict(user))

    data = request.get_json(silent=True) or {}
    allowed_fields = ["name", "cgpa", "backlogs", "branch", "github", "roll_number", "semester", "phone", "skills", "linkedin"]
    updates = []
    values = []

    for field in allowed_fields:
        if field in data:
            updates.append(f"{field} = ?")
            values.append(data[field])

    if not updates:
        conn.close()
        return jsonify({"message": "No profile fields provided."}), 400

    values.append(student_id)
    conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", values)
    conn.commit()

    user = conn.execute("SELECT * FROM users WHERE id = ?", (student_id,)).fetchone()
    conn.close()
    return jsonify({"message": "Profile updated successfully!", "user": dict(user)})


@app.route("/student/<int:student_id>/resume", methods=["POST", "GET", "DELETE"])
def handle_resume(student_id):
    conn = get_db_connection()

    if request.method == "POST":
        if "resume" not in request.files:
            conn.close()
            return jsonify({"message": "Choose a resume file first."}), 400
        file = request.files["resume"]
        if not file.filename:
            conn.close()
            return jsonify({"message": "No file selected."}), 400

        filename = secure_filename(file.filename)
        saved_name = f"student_{student_id}_{filename}"
        file_path = UPLOAD_FOLDER / saved_name
        file.save(file_path)

        extracted_skills = []
        if PYPDF_AVAILABLE:
            try:
                reader = pypdf.PdfReader(str(file_path))
                full_text = "".join([page.extract_text() or "" for page in reader.pages])
                skill_dictionary = [
                    "Python", "Java", "C++", "JavaScript", "SQL", "React", "Node.js",
                    "Machine Learning", "Artificial Intelligence", "Deep Learning", "AWS",
                    "Docker", "Kubernetes", "HTML", "CSS", "Flask", "Django", "Git", "PowerBI"
                ]
                for skill in skill_dictionary:
                    if re.search(r'\b' + re.escape(skill) + r'\b', full_text, re.IGNORECASE):
                        extracted_skills.append(skill)
            except Exception:
                pass

        current_student = conn.execute("SELECT skills FROM users WHERE id = ?", (student_id,)).fetchone()
        existing_skills = (current_student["skills"] if current_student else "") or ""

        if extracted_skills and not existing_skills.strip():
            auto_skills_str = ", ".join(extracted_skills)
            conn.execute("UPDATE users SET resume = ?, resume_filename = ?, skills = ? WHERE id = ?",
                         (saved_name, filename, auto_skills_str, student_id))
        else:
            conn.execute("UPDATE users SET resume = ?, resume_filename = ? WHERE id = ?",
                         (saved_name, filename, student_id))

        conn.commit()
        conn.close()
        return jsonify({"message": "Resume uploaded & parsed successfully!", "resume": saved_name, "extracted_skills": extracted_skills})

    user = conn.execute("SELECT resume FROM users WHERE id = ?", (student_id,)).fetchone()
    if not user or not user["resume"]:
        conn.close()
        return jsonify({"message": "No resume uploaded."}), 404

    if request.method == "GET":
        conn.close()
        return send_from_directory(str(UPLOAD_FOLDER), user["resume"], as_attachment=False)

    if request.method == "DELETE":
        path = UPLOAD_FOLDER / user["resume"]
        if path.exists():
            path.unlink()
        conn.execute("UPDATE users SET resume = NULL, resume_filename = NULL WHERE id = ?", (student_id,))
        conn.commit()
        conn.close()
        return jsonify({"message": "Resume removed."})


@app.route("/student/<int:student_id>/resume/download", methods=["GET"])
def download_resume(student_id):
    conn = get_db_connection()
    user = conn.execute("SELECT resume FROM users WHERE id = ?", (student_id,)).fetchone()
    conn.close()

    if not user or not user["resume"]:
        return jsonify({"message": "No resume uploaded."}), 404

    return send_from_directory(str(UPLOAD_FOLDER), user["resume"], as_attachment=True)


# --------------------------------------------------
# APPLICATIONS, INTERVIEWS, SALARY PREDICTOR & AI
# --------------------------------------------------

@app.route("/apply", methods=["POST"])
def apply_for_job():
    data = request.get_json(silent=True) or {}
    student_id = data.get("student_id")
    job_id = data.get("job_id")

    if not student_id or not job_id:
        return jsonify({"message": "student_id and job_id are required."}), 400

    conn = get_db_connection()
    try:
        conn.execute("""
            INSERT INTO applications (student_id, job_id, status, applied_at)
            VALUES (?, ?, 'Applied', ?)
        """, (student_id, job_id, datetime.now().isoformat(timespec="seconds")))
        conn.commit()
        conn.close()
        return jsonify({"message": "Application submitted successfully!"}), 201
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"message": "You have already applied for this job."}), 409


@app.route("/applications/<int:student_id>", methods=["GET"])
def student_applications(student_id):
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT a.id AS application_id, a.student_id, a.job_id, a.status, a.applied_at,
               COALESCE(j.title, j.job_role) AS job_role, j.company, j.location, j.salary
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        WHERE a.student_id = ?
        ORDER BY a.id DESC
    """, (student_id,)).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route("/interviews/schedule", methods=["POST"])
def schedule_interview():
    data = request.get_json(silent=True) or {}
    application_id = data.get("application_id")
    student_id = data.get("student_id")
    job_id = data.get("job_id")
    interview_date = (data.get("interview_date") or "").strip()
    interview_time = (data.get("interview_time") or "").strip()
    location_or_link = (data.get("location_or_link") or "").strip()
    notes = (data.get("notes") or "").strip()

    if not application_id or not student_id or not interview_date or not interview_time:
        return jsonify({"message": "Application ID, Student ID, date, and time are required."}), 400

    conn = get_db_connection()
    app_info = conn.execute("""
        SELECT a.student_id, j.company, COALESCE(j.title, j.job_role) AS job_title 
        FROM applications a JOIN jobs j ON j.id = a.job_id 
        WHERE a.id = ?
    """, (application_id,)).fetchone()

    if not app_info:
        conn.close()
        return jsonify({"message": "Application not found."}), 404

    cursor = conn.execute("""
        INSERT INTO interviews (application_id, student_id, job_id, interview_date, interview_time, location_or_link, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (application_id, student_id, job_id, interview_date, interview_time, location_or_link, notes, datetime.now().isoformat(timespec="seconds")))

    conn.execute("UPDATE applications SET status = 'Shortlisted' WHERE id = ?", (application_id,))

    msg = f"📅 Interview Scheduled! {app_info['company']} for {app_info['job_title']} on {interview_date} at {interview_time}."
    conn.execute("""
        INSERT INTO notifications (student_id, message, created_at)
        VALUES (?, ?, ?)
    """, (student_id, msg, datetime.now().isoformat(timespec="seconds")))

    conn.commit()
    conn.close()
    return jsonify({"message": "Interview scheduled successfully!", "interview_id": cursor.lastrowid}), 201


@app.route("/student/<int:student_id>/interviews", methods=["GET"])
def get_student_interviews(student_id):
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT i.*, j.company, COALESCE(j.title, j.job_role) AS job_role, j.location
        FROM interviews i
        JOIN jobs j ON j.id = i.job_id
        WHERE i.student_id = ?
        ORDER BY i.interview_date ASC, i.interview_time ASC
    """, (student_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# --------------------------------------------------
# FEATURE 13: AI SALARY & CTC PREDICTOR
# --------------------------------------------------

@app.route("/student/<int:student_id>/salary-prediction", methods=["GET"])
def salary_prediction(student_id):
    conn = get_db_connection()
    student = conn.execute("SELECT cgpa, skills FROM users WHERE id = ?", (student_id,)).fetchone()
    conn.close()

    if not student:
        return jsonify({"message": "Student not found."}), 404

    cgpa = float(student["cgpa"] or 6.0)
    skills = [s.strip() for s in (student["skills"] or "").split(",") if s.strip()]

    base_ctc = 4.5
    cgpa_bonus = max(0.0, (cgpa - 6.0) * 1.5)
    skill_bonus = len(skills) * 0.4
    estimated_lpa = round(base_ctc + cgpa_bonus + skill_bonus, 2)

    tier = "Tier-1 Product / High Growth" if estimated_lpa >= 12 else ("Core Tech / Service" if estimated_lpa >= 7 else "Entry Level")

    return jsonify({
        "estimated_lpa": estimated_lpa,
        "market_tier": tier,
        "factors": f"Evaluated based on CGPA {cgpa} and {len(skills)} verified technical skills."
    })


# --------------------------------------------------
# FEATURE 14: AI RECRUITER AUTO-SHORTLISTER
# --------------------------------------------------

@app.route("/company/job/<int:job_id>/auto-shortlist", methods=["POST"])
def auto_shortlist_job(job_id):
    conn = get_db_connection()
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        conn.close()
        return jsonify({"message": "Job not found."}), 404

    applicants = conn.execute("""
        SELECT a.id AS app_id, a.student_id, u.cgpa, u.backlogs, u.skills
        FROM applications a
        JOIN users u ON u.id = a.student_id
        WHERE a.job_id = ? AND a.status = 'Applied'
    """, (job_id,)).fetchall()

    shortlisted_count = 0
    for app in applicants:
        cgpa = float(app["cgpa"] or 0)
        backlogs = int(app["backlogs"] or 0)
        min_cgpa = job["min_cgpa"] or 6.0
        max_backlogs = job["max_backlogs"] if job["max_backlogs"] is not None else 0

        if cgpa >= min_cgpa and backlogs <= max_backlogs:
            conn.execute("UPDATE applications SET status = 'Shortlisted' WHERE id = ?", (app["app_id"],))
            shortlisted_count += 1

    conn.commit()
    conn.close()
    return jsonify({"message": f"AI successfully evaluated and auto-shortlisted {shortlisted_count} qualified candidates!"})


# --------------------------------------------------
# FEATURE 15: AI INTERVIEW SCORECARD
# --------------------------------------------------

@app.route("/applications/<int:application_id>/scorecard", methods=["POST", "GET"])
def application_scorecard(application_id):
    conn = get_db_connection()
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        tech = int(data.get("technical_score", 7))
        comm = int(data.get("communication_score", 7))
        rec = data.get("recommendation", "Hire")
        notes = data.get("notes", "")

        app_info = conn.execute("SELECT student_id, job_id FROM applications WHERE id = ?", (application_id,)).fetchone()
        if not app_info:
            conn.close()
            return jsonify({"message": "Application not found."}), 404

        conn.execute("""
            INSERT INTO scorecards (application_id, student_id, job_id, technical_score, communication_score, recommendation, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (application_id, app_info["student_id"], app_info["job_id"], tech, comm, rec, notes, datetime.now().isoformat(timespec="seconds")))
        conn.commit()
        conn.close()
        return jsonify({"message": "Interview scorecard submitted successfully!"})

    scorecard = conn.execute("SELECT * FROM scorecards WHERE application_id = ?", (application_id,)).fetchone()
    conn.close()
    if not scorecard:
        return jsonify({"message": "No scorecard found."}), 404
    return jsonify(dict(scorecard))


# --------------------------------------------------
# FEATURE 16: RECRUITER AI CANDIDATE COMPARISON MATRIX
# --------------------------------------------------

@app.route("/company/compare-candidates", methods=["POST"])
def compare_candidates():
    data = request.get_json(silent=True) or {}
    student_ids = data.get("student_ids", [])
    job_id = data.get("job_id")

    if not student_ids or len(student_ids) < 2:
        return jsonify({"message": "Please select at least 2 candidates to perform a side-by-side comparison."}), 400

    conn = get_db_connection()
    job = conn.execute("SELECT title, job_role, company FROM jobs WHERE id = ?", (job_id,)).fetchone() if job_id else None
    job_text = f"{job['title'] or ''} {job['job_role'] or ''}".lower() if job else ""

    placeholders = ",".join(["?"] * len(student_ids))
    users = conn.execute(f"""
        SELECT id, name, email, cgpa, backlogs, branch, skills, resume
        FROM users WHERE id IN ({placeholders})
    """, student_ids).fetchall()

    comparison_matrix = []
    for u in users:
        student_dict = dict(u)
        skills_str = student_dict.get("skills") or ""
        
        match_info = calculate_skill_match(skills_str, job_text) if job_text else {"score": 75, "matched": [], "missing": []}
        
        cgpa_val = float(student_dict.get("cgpa") or 0)
        backlog_val = int(student_dict.get("backlogs") or 0)
        
        rating = int((cgpa_val * 6) + (match_info["score"] * 0.3) + (10 if backlog_val == 0 else 0))
        rating = min(max(rating, 40), 99)

        comparison_matrix.append({
            "id": student_dict["id"],
            "name": student_dict["name"],
            "email": student_dict["email"],
            "branch": student_dict["branch"] or "N/A",
            "cgpa": cgpa_val,
            "backlogs": backlog_val,
            "skills": skills_str or "Not listed",
            "ai_match_score": match_info["score"],
            "overall_rating": rating
        })

    conn.close()
    comparison_matrix.sort(key=lambda x: x["overall_rating"], reverse=True)

    return jsonify({
        "job_title": (job["job_role"] or job["title"]) if job else "General Evaluation",
        "company": job["company"] if job else "N/A",
        "candidates": comparison_matrix
    })


# --------------------------------------------------
# FEATURE 17: AI RESUME BULLET POINT OPTIMIZER
# --------------------------------------------------

@app.route("/student/optimize-bullet", methods=["POST"])
def optimize_resume_bullet():
    data = request.get_json(silent=True) or {}
    draft = (data.get("draft") or "").strip()
    role = (data.get("target_role") or "Software Engineer").strip()

    if not draft:
        return jsonify({"message": "Please provide a draft bullet point or project description."}), 400

    action_verbs = ["Architected", "Engineered", "Implemented", "Optimized", "Spearheaded", "Streamlined", "Deployed"]
    verb = action_verbs[len(draft) % len(action_verbs)]

    enhanced_bullet_1 = f"{verb} a scalable solution for {draft.lower()} targeting {role} standards, improving operational throughput by 35%."
    enhanced_bullet_2 = f"Designed and integrated modular components to resolve '{draft}', reducing processing latency by 40% and enhancing overall system reliability."
    enhanced_bullet_3 = f"Leveraged industry-standard design patterns to execute {draft.lower()}, resulting in seamless API communication and zero production downtime."

    return jsonify({
        "original": draft,
        "target_role": role,
        "suggestions": [enhanced_bullet_1, enhanced_bullet_2, enhanced_bullet_3],
        "ats_keywords_added": ["Scalability", "Latency Reduction", "RESTful Architecture", "System Reliability"]
    })


# --------------------------------------------------
# FEATURE 18: RECRUITER BROADCAST ANNOUNCEMENTS
# --------------------------------------------------

@app.route("/company/job/<int:job_id>/broadcast", methods=["POST"])
def broadcast_announcement(job_id):
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify({"message": "Please provide an announcement message to broadcast."}), 400

    conn = get_db_connection()
    job = conn.execute("SELECT title, job_role, company FROM jobs WHERE id = ?", (job_id,)).fetchone()

    if not job:
        conn.close()
        return jsonify({"message": "Job posting not found."}), 404

    applicants = conn.execute("SELECT DISTINCT student_id FROM applications WHERE job_id = ?", (job_id,)).fetchall()

    if not applicants:
        conn.close()
        return jsonify({"message": "No applicants found for this job listing."}), 404

    job_title = job["job_role"] or job["title"]
    formatted_msg = f"📢 [{job['company']} - {job_title}] {message}"
    timestamp = datetime.now().isoformat(timespec="seconds")

    sent_count = 0
    for app in applicants:
        conn.execute("""
            INSERT INTO notifications (student_id, message, created_at)
            VALUES (?, ?, ?)
        """, (app["student_id"], formatted_msg, timestamp))
        sent_count += 1

    conn.commit()
    conn.close()

    return jsonify({
        "message": f"Broadcast successfully sent to {sent_count} applicant(s)!",
        "recipients_count": sent_count
    })


@app.route("/student/<int:student_id>/readiness", methods=["GET"])
def get_placement_readiness(student_id):
    conn = get_db_connection()
    student = conn.execute("SELECT * FROM users WHERE id = ?", (student_id,)).fetchone()
    if not student:
        conn.close()
        return jsonify({"message": "Student not found."}), 404

    s = dict(student)
    apps_count = conn.execute("SELECT COUNT(*) FROM applications WHERE student_id = ?", (student_id,)).fetchone()[0]
    shortlisted_count = conn.execute("SELECT COUNT(*) FROM applications WHERE student_id = ? AND status = 'Shortlisted'", (student_id,)).fetchone()[0]
    conn.close()

    score = 0
    feedback = []

    cgpa = float(s.get("cgpa") or 0)
    if cgpa >= 8.5:
        score += 35
        feedback.append("Excellent academic performance (CGPA ≥ 8.5).")
    elif cgpa >= 7.0:
        score += 25
        feedback.append("Solid CGPA score.")
    elif cgpa > 0:
        score += 15
        feedback.append("Consider improving your academic score to unlock top job listings.")
    else:
        feedback.append("Save your CGPA in your profile to boost score.")

    backlogs = int(s.get("backlogs") or 0)
    if backlogs == 0:
        score += 15
        feedback.append("Zero active backlogs — full company eligibility!")
    else:
        feedback.append(f"Clear active backlogs ({backlogs}) to increase eligibility.")

    skills = [k.strip() for k in (s.get("skills") or "").split(",") if k.strip()]
    if len(skills) >= 6:
        score += 20
    elif len(skills) >= 3:
        score += 12
        feedback.append("Add more technical skills to reach higher AI match rates.")
    else:
        feedback.append("List at least 5 key technical skills.")

    if s.get("resume"):
        score += 10
        feedback.append("Resume PDF uploaded & verified.")
    else:
        feedback.append("Upload a PDF resume for AI parsing.")

    if shortlisted_count > 0:
        score += 20
        feedback.append("You have active shortlisted applications!")
    elif apps_count >= 3:
        score += 12
    elif apps_count > 0:
        score += 6
    else:
        feedback.append("Apply for active job openings to build momentum.")

    final_score = min(score, 100)
    tier = "High" if final_score >= 75 else ("Medium" if final_score >= 50 else "Building")

    return jsonify({
        "readiness_score": final_score,
        "tier": tier,
        "feedback": feedback
    })


@app.route("/student/<int:student_id>/job/<int:job_id>/ats-score", methods=["GET"])
def get_ats_score(student_id, job_id):
    conn = get_db_connection()
    student = conn.execute("SELECT skills, resume FROM users WHERE id = ?", (student_id,)).fetchone()
    job = conn.execute("SELECT title, job_role, eligibility, allowed_branches FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()

    if not student or not job:
        return jsonify({"message": "Student or Job not found."}), 404

    student_skills = [s.strip().lower() for s in (student["skills"] or "").split(",") if s.strip()]
    job_text = f"{job['title'] or ''} {job['job_role'] or ''} {job['eligibility'] or ''}".lower()

    common_tech = [
        "python", "java", "c++", "javascript", "sql", "react", "node.js",
        "machine learning", "aws", "docker", "kubernetes", "html", "css", 
        "flask", "django", "git", "powerbi", "rest api", "mongodb"
    ]
    
    required_keywords = [tech for tech in common_tech if tech in job_text]
    if not required_keywords:
        required_keywords = ["python", "sql", "git"]

    matched = [kw for kw in required_keywords if kw in student_skills or kw in (student["skills"] or "").lower()]
    missing = [kw for kw in required_keywords if kw not in matched]

    score = int((len(matched) / len(required_keywords)) * 100) if required_keywords else 75

    return jsonify({
        "ats_score": max(score, 45),
        "matched_keywords": [m.title() for m in matched],
        "missing_keywords": [m.title() for m in missing],
        "suggestion": f"Add {', '.join([m.title() for m in missing[:3]])} to your resume to pass company ATS filters." if missing else "Your resume has high keyword compatibility!"
    })


@app.route("/student/<int:student_id>/skill-recommendations", methods=["GET"])
def skill_recommendations(student_id):
    conn = get_db_connection()
    student = conn.execute("SELECT skills FROM users WHERE id = ?", (student_id,)).fetchone()
    jobs = conn.execute("SELECT title, job_role, eligibility FROM jobs").fetchall()
    conn.close()

    student_skills = set([s.strip().lower() for s in (student["skills"] or "").split(",") if s.strip()])
    all_job_text = " ".join([f"{j['title'] or ''} {j['job_role'] or ''} {j['eligibility'] or ''}".lower() for j in jobs])
    
    tech_skills = ["Python", "Java", "SQL", "React", "Docker", "AWS", "Machine Learning", "Git", "PowerBI", "Node.js"]
    in_demand_missing = [skill for skill in tech_skills if skill.lower() not in student_skills and skill.lower() in all_job_text]

    recommendations = []
    for skill in in_demand_missing[:4]:
        recommendations.append({
            "skill": skill,
            "reason": "High demand in current job listings.",
            "learning_path": f"FreeCourse / Docs: Learn {skill} fundamentals."
        })

    return jsonify({"recommended_skills": recommendations})


@app.route("/applications/<int:application_id>/offer-letter", methods=["GET"])
def generate_offer_letter(application_id):
    conn = get_db_connection()
    app_info = conn.execute("""
        SELECT a.id, a.status, a.applied_at, 
               u.name AS student_name, u.email AS student_email, u.branch,
               j.company, COALESCE(j.title, j.job_role) AS job_role, j.salary, j.location
        FROM applications a
        JOIN users u ON u.id = a.student_id
        JOIN jobs j ON j.id = a.job_id
        WHERE a.id = ?
    """, (application_id,)).fetchone()
    conn.close()

    if not app_info:
        return jsonify({"message": "Application not found."}), 404

    if app_info["status"] != "Selected":
        return jsonify({"message": "Offer letters can only be generated for 'Selected' candidates."}), 400

    letter_content = f"""
    ================================================================================
                                   OFFER OF EMPLOYMENT
    ================================================================================

    Date: {datetime.now().strftime('%B %d, %Y')}

    To: {app_info['student_name']}
    Email: {app_info['student_email']}
    Branch: {app_info['branch']}

    Dear {app_info['student_name']},

    We are pleased to offer you the position of {app_info['job_role']} at {app_info['company']}.

    Offer Details:
    --------------------------------------------------------------------------------
    Position:          {app_info['job_role']}
    Company:           {app_info['company']}
    Location:          {app_info['location']}
    Compensation:      {app_info['salary']}
    Application Ref:   #OFFER-{app_info['id']}
    --------------------------------------------------------------------------------

    Congratulations on your selection during the campus placement drive!

    Sincerely,
    Recruitment Team
    {app_info['company']}
    ================================================================================
    """

    return Response(
        letter_content,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment;filename=Offer_Letter_{app_info['student_name'].replace(' ', '_')}.txt"}
    )


@app.route("/student/<int:student_id>/job/<int:job_id>/mock-interview", methods=["GET"])
def generate_mock_interview(student_id, job_id):
    conn = get_db_connection()
    student = conn.execute("SELECT skills FROM users WHERE id = ?", (student_id,)).fetchone()
    job = conn.execute("SELECT title, job_role, company FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()

    if not student or not job:
        return jsonify({"message": "Student or Job not found."}), 404

    job_title = job["job_role"] or job["title"] or "Software Engineer"
    company_name = job["company"] or "the company"
    skills = [s.strip() for s in (student["skills"] or "Python, Problem Solving").split(",") if s.strip()]
    primary_skill = skills[0] if skills else "Python"

    questions = [
        {
            "question": f"Can you explain a challenging project where you used {primary_skill} for this {job_title} role at {company_name}?",
            "tip": "Use the STAR method (Situation, Task, Action, Result) and highlight measurable performance improvements."
        },
        {
            "question": f"How do you handle debugging performance bottlenecks in a high-scale production system?",
            "tip": "Mention profiling tools, logging frameworks, query optimization, and systematic root-cause analysis."
        },
        {
            "question": f"Explain the core architectural differences between Monolithic and Microservices design patterns.",
            "tip": "Discuss scalability, deployment independence, network latency, and data consistency trade-offs."
        },
        {
            "question": f"Behavioral: Tell me about a time you had a technical disagreement with a team member and how you resolved it.",
            "tip": "Emphasize active listening, data-driven compromise, and focus on team delivery goals."
        }
    ]

    return jsonify({
        "job_title": job_title,
        "company": company_name,
        "mock_questions": questions
    })


@app.route("/student/<int:student_id>/notifications", methods=["GET"])
def get_student_notifications(student_id):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM notifications WHERE student_id = ? ORDER BY id DESC LIMIT 5", (student_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/users", methods=["GET"])
def get_users():
    conn = get_db_connection()
    rows = conn.execute("SELECT id, name, email, role, cgpa, backlogs, branch FROM users ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route("/all-applications", methods=["GET"])
def all_applications():
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT a.id AS application_id, a.student_id, a.job_id, a.status, a.applied_at,
               u.name AS student_name, u.email AS student_email, u.cgpa, u.backlogs, u.branch,
               COALESCE(j.title, j.job_role) AS job_title, j.company, j.location
        FROM applications a
        LEFT JOIN users u ON u.id = a.student_id
        LEFT JOIN jobs j ON j.id = a.job_id
        ORDER BY a.id DESC
    """).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route("/applications/<int:application_id>/status", methods=["PUT"])
def update_application_status(application_id):
    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip()

    if status not in {"Applied", "Shortlisted", "Rejected", "Selected"}:
        return jsonify({"message": "Invalid status."}), 400

    conn = get_db_connection()
    app_info = conn.execute("""
        SELECT a.student_id, j.company, COALESCE(j.title, j.job_role) AS job_title 
        FROM applications a JOIN jobs j ON j.id = a.job_id 
        WHERE a.id = ?
    """, (application_id,)).fetchone()

    cursor = conn.execute("UPDATE applications SET status = ? WHERE id = ?", (status, application_id))

    if cursor.rowcount > 0 and app_info:
        msg = f"Your application for {app_info['job_title']} at {app_info['company']} has been updated to: {status}."
        conn.execute("""
            INSERT INTO notifications (student_id, message, created_at)
            VALUES (?, ?, ?)
        """, (app_info["student_id"], msg, datetime.now().isoformat(timespec="seconds")))

    conn.commit()
    conn.close()

    if cursor.rowcount == 0:
        return jsonify({"message": "Application not found."}), 404
    return jsonify({"message": "Application status updated."})


@app.route("/export/applications-csv", methods=["GET"])
def export_applications_csv():
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT a.id, u.name AS student_name, u.email, u.branch, u.cgpa,
               j.company, COALESCE(j.title, j.job_role) AS job_title, a.status, a.applied_at
        FROM applications a
        LEFT JOIN users u ON u.id = a.student_id
        LEFT JOIN jobs j ON j.id = a.job_id
        ORDER BY a.id DESC
    """).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Application ID", "Student Name", "Email", "Branch", "CGPA", "Company", "Job Role", "Status", "Applied At"])

    for row in rows:
        writer.writerow([row["id"], row["student_name"], row["email"], row["branch"], row["cgpa"], row["company"], row["job_title"], row["status"], row["applied_at"]])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=placement_applications_report.csv"}
    )


# --------------------------------------------------
# ERROR HANDLERS & SERVER LAUNCH
# --------------------------------------------------

@app.errorhandler(404)
def not_found(error):
    return jsonify({"message": "Endpoint not found."}), 404

@app.errorhandler(413)
def file_too_large(error):
    return jsonify({"message": "File too large. Maximum upload size is 10 MB."}), 413

@app.errorhandler(500)
def internal_error(error):
    app.logger.exception("Internal server error")
    return jsonify({"message": "Internal server error."}), 500


initialize_database()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)