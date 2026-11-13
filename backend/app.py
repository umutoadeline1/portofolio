import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, g, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token, jwt_required, get_jwt_identity
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app)

app.config["JWT_SECRET_KEY"] = os.getenv(
    "JWT_SECRET_KEY", "change-this-secret-before-deployment"
)
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=8)

jwt = JWTManager(app)

DATABASE = os.getenv("DATABASE", "/app/data/portfolio.db")
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "/app/uploads")
ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "ppt", "pptx", "jpg", "jpeg", "png"}

os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def initialize_database():
    with sqlite3.connect(DATABASE) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'student'
        );

        CREATE TABLE IF NOT EXISTS portfolios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(student_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL,
            lecturer_id INTEGER NOT NULL,
            feedback TEXT NOT NULL,
            grade TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(portfolio_id) REFERENCES portfolios(id),
            FOREIGN KEY(lecturer_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            module_name TEXT NOT NULL,
            rating INTEGER NOT NULL,
            comments TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(student_id) REFERENCES users(id)
        );
        """)

        # Demo lecturer account
        lecturer = db.execute(
            "SELECT id FROM users WHERE email = ?",
            ("lecturer@example.com",)
        ).fetchone()

        if lecturer is None:
            db.execute(
                """INSERT INTO users (name, email, password, role)
                   VALUES (?, ?, ?, ?)""",
                (
                    "Demo Lecturer",
                    "lecturer@example.com",
                    generate_password_hash("Lecturer123!"),
                    "lecturer"
                )
            )


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def role_required(*roles):
    def decorator(function):
        @wraps(function)
        @jwt_required()
        def wrapper(*args, **kwargs):
            user_id = int(get_jwt_identity())
            user = get_db().execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()

            if user is None or user["role"] not in roles:
                return jsonify({"message": "Access denied"}), 403

            return function(*args, **kwargs)
        return wrapper
    return decorator


@app.get("/api/health")
def health():
    return jsonify({"status": "running"})


@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}

    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not name or not email or len(password) < 8:
        return jsonify({
            "message": "Name, email and a password of at least 8 characters are required"
        }), 400

    try:
        db = get_db()
        db.execute(
            """INSERT INTO users (name, email, password, role)
               VALUES (?, ?, ?, 'student')""",
            (name, email, generate_password_hash(password))
        )
        db.commit()

        return jsonify({"message": "Registration successful"}), 201

    except sqlite3.IntegrityError:
        return jsonify({"message": "Email already registered"}), 409


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}

    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    user = get_db().execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()

    if user is None or not check_password_hash(user["password"], password):
        return jsonify({"message": "Invalid email or password"}), 401

    token = create_access_token(identity=str(user["id"]))

    return jsonify({
        "access_token": token,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"]
        }
    })


@app.post("/api/portfolios")
@role_required("student")
def upload_portfolio():
    if "file" not in request.files:
        return jsonify({"message": "Please select a file"}), 400

    file = request.files["file"]
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()

    if not title or not file.filename:
        return jsonify({"message": "Title and file are required"}), 400

    if not allowed_file(file.filename):
        return jsonify({"message": "File type not allowed"}), 400

    original_filename = secure_filename(file.filename)
    stored_filename = f"{uuid.uuid4().hex}_{original_filename}"
    file.save(os.path.join(UPLOAD_FOLDER, stored_filename))

    student_id = int(get_jwt_identity())
    db = get_db()

    db.execute(
        """INSERT INTO portfolios
           (student_id, title, description, filename,
            original_filename, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            student_id,
            title,
            description,
            stored_filename,
            original_filename,
            datetime.utcnow().isoformat()
        )
    )
    db.commit()

    return jsonify({"message": "Portfolio uploaded successfully"}), 201


@app.get("/api/portfolios")
@jwt_required()
def get_portfolios():
    user_id = int(get_jwt_identity())
    user = get_db().execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    if user["role"] == "lecturer":
        portfolios = get_db().execute("""
            SELECT portfolios.*, users.name AS student_name
            FROM portfolios
            JOIN users ON portfolios.student_id = users.id
            ORDER BY portfolios.id DESC
        """).fetchall()
    else:
        portfolios = get_db().execute("""
            SELECT * FROM portfolios
            WHERE student_id = ?
            ORDER BY id DESC
        """, (user_id,)).fetchall()

    return jsonify([dict(item) for item in portfolios])


@app.get("/api/portfolios/<int:portfolio_id>/file")
@jwt_required()
def download_portfolio(portfolio_id):
    user_id = int(get_jwt_identity())
    db = get_db()

    user = db.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    portfolio = db.execute(
        "SELECT * FROM portfolios WHERE id = ?", (portfolio_id,)
    ).fetchone()

    if portfolio is None:
        return jsonify({"message": "Portfolio not found"}), 404

    if user["role"] != "lecturer" and portfolio["student_id"] != user_id:
        return jsonify({"message": "Access denied"}), 403

    return send_from_directory(
        UPLOAD_FOLDER,
        portfolio["filename"],
        as_attachment=True,
        download_name=portfolio["original_filename"]
    )


@app.post("/api/portfolios/<int:portfolio_id>/review")
@role_required("lecturer")
def review_portfolio(portfolio_id):
    data = request.get_json(silent=True) or {}

    feedback = data.get("feedback", "").strip()
    grade = data.get("grade", "").strip()

    if not feedback:
        return jsonify({"message": "Feedback is required"}), 400

    db = get_db()

    portfolio = db.execute(
        "SELECT id FROM portfolios WHERE id = ?", (portfolio_id,)
    ).fetchone()

    if portfolio is None:
        return jsonify({"message": "Portfolio not found"}), 404

    lecturer_id = int(get_jwt_identity())

    db.execute(
        """INSERT INTO reviews
           (portfolio_id, lecturer_id, feedback, grade, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            portfolio_id,
            lecturer_id,
            feedback,
            grade,
            datetime.utcnow().isoformat()
        )
    )
    db.commit()

    return jsonify({"message": "Review submitted successfully"}), 201


@app.get("/api/reviews")
@jwt_required()
def get_reviews():
    user_id = int(get_jwt_identity())
    db = get_db()

    user = db.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    if user["role"] == "lecturer":
        reviews = db.execute("""
            SELECT reviews.*, portfolios.title,
                   users.name AS student_name
            FROM reviews
            JOIN portfolios ON reviews.portfolio_id = portfolios.id
            JOIN users ON portfolios.student_id = users.id
            ORDER BY reviews.id DESC
        """).fetchall()
    else:
        reviews = db.execute("""
            SELECT reviews.*, portfolios.title
            FROM reviews
            JOIN portfolios ON reviews.portfolio_id = portfolios.id
            WHERE portfolios.student_id = ?
            ORDER BY reviews.id DESC
        """, (user_id,)).fetchall()

    return jsonify([dict(item) for item in reviews])


@app.post("/api/evaluations")
@role_required("student")
def submit_evaluation():
    data = request.get_json(silent=True) or {}

    module_name = data.get("module_name", "").strip()
    comments = data.get("comments", "").strip()

    try:
        rating = int(data.get("rating"))
    except (TypeError, ValueError):
        return jsonify({"message": "Rating must be a number from 1 to 5"}), 400

    if not module_name or rating < 1 or rating > 5:
        return jsonify({"message": "Enter a module and a rating from 1 to 5"}), 400

    student_id = int(get_jwt_identity())
    db = get_db()

    db.execute(
        """INSERT INTO evaluations
           (student_id, module_name, rating, comments, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            student_id,
            module_name,
            rating,
            comments,
            datetime.utcnow().isoformat()
        )
    )
    db.commit()

    return jsonify({"message": "Module evaluation submitted"}), 201


@app.get("/api/evaluations")
@role_required("lecturer")
def get_evaluations():
    evaluations = get_db().execute("""
        SELECT evaluations.*, users.name AS student_name
        FROM evaluations
        JOIN users ON evaluations.student_id = users.id
        ORDER BY evaluations.id DESC
    """).fetchall()

    return jsonify([dict(item) for item in evaluations])


initialize_database()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)