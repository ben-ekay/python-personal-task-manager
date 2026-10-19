import sqlite3
from datetime import date
import os

from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]

csrf = CSRFProtect()
csrf.init_app(app)


def get_db_connection():
    connection = sqlite3.connect("task_manager.db")
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = get_db_connection()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    connection.commit()
    connection.close()


init_db()


def add_priority_column():
    connection = get_db_connection()

    columns = connection.execute(
        "PRAGMA table_info(tasks)"
    ).fetchall()

    column_names = [column["name"] for column in columns]

    if "priority" not in column_names:
        connection.execute(
            """
            ALTER TABLE tasks
            ADD COLUMN priority TEXT NOT NULL DEFAULT 'Medium'
            """
        )
        connection.commit()

    connection.close()


add_priority_column()

def add_deadline_column():
    connection = get_db_connection()

    columns = connection.execute(
        "PRAGMA table_info(tasks)"
    ).fetchall()

    column_names = [column["name"] for column in columns]

    if "deadline" not in column_names:
        connection.execute(
            "ALTER TABLE tasks ADD COLUMN deadline TEXT"
        )
        connection.commit()

    connection.close()


add_deadline_column()

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]

        password_hash = generate_password_hash(password)

        connection = get_db_connection()

        try:
            connection.execute(
                """
                INSERT INTO users (name, email, password_hash)
                VALUES (?, ?, ?)
                """,
                (name, email, password_hash)
            )

            connection.commit()
            connection.close()

            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            connection.close()

            return "This email is already registered."

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        connection = get_db_connection()

        user = connection.execute(
            """
            SELECT * FROM users
            WHERE email = ?
            """,
            (email,)
        ).fetchone()

        connection.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]

            return redirect(url_for("dashboard"))

        return "Invalid email or password"

    return render_template("login.html")

PRIORITIES = ["High", "Medium", "Low"]


def describe_deadline(deadline, today):
    # Turn a stored deadline into a short label such as "Due tomorrow".
    # Returns an empty label instead of crashing if the date cannot be read.
    try:
        days = (date.fromisoformat(deadline) - today).days
    except (TypeError, ValueError):
        return ""

    if days == 0:
        return "Due today"

    if days == 1:
        return "Due tomorrow"

    if days > 1:
        return f"Due in {days} days"

    if days == -1:
        return "1 day overdue"

    return f"{-days} days overdue"


def with_deadline_labels(rows, today):
    return [
        dict(row, deadline_label=describe_deadline(row["deadline"], today))
        for row in rows
    ]


# Deadline queries below use "date(deadline) = deadline" to keep only real
# calendar dates. SQLite's date() returns NULL for text that is not a date
# and rolls impossible dates forward (2026-02-31 becomes 2026-03-03), so the
# comparison is false for empty, missing, malformed and impossible deadlines.

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    today = date.today()

    connection = get_db_connection()

    total_tasks = connection.execute(
        """
        SELECT COUNT(*) FROM tasks
        WHERE user_id = ?
        """,
        (session["user_id"],)
    ).fetchone()[0]

    completed_tasks = connection.execute(
        """
        SELECT COUNT(*) FROM tasks
        WHERE user_id = ? AND completed = 1
        """,
        (session["user_id"],)
    ).fetchone()[0]

    pending_tasks = total_tasks - completed_tasks

    if total_tasks > 0:
        completion_percentage = round(
            (completed_tasks / total_tasks) * 100,
            1
        )
    else:
        completion_percentage = 0

    overdue_tasks = connection.execute(
        """
        SELECT COUNT(*) FROM tasks
        WHERE user_id = ? AND completed = 0
        AND date(deadline) = deadline AND deadline < ?
        """,
        (session["user_id"], today.isoformat())
    ).fetchone()[0]

    priority_rows = connection.execute(
        """
        SELECT priority, COUNT(*) AS total, SUM(completed) AS completed
        FROM tasks
        WHERE user_id = ?
        GROUP BY priority
        """,
        (session["user_id"],)
    ).fetchall()

    upcoming_rows = connection.execute(
        """
        SELECT id, title, priority, deadline FROM tasks
        WHERE user_id = ? AND completed = 0
        AND date(deadline) = deadline AND deadline >= ?
        ORDER BY deadline ASC, id ASC
        LIMIT 5
        """,
        (session["user_id"], today.isoformat())
    ).fetchall()

    overdue_rows = connection.execute(
        """
        SELECT id, title, priority, deadline FROM tasks
        WHERE user_id = ? AND completed = 0
        AND date(deadline) = deadline AND deadline < ?
        ORDER BY deadline ASC, id ASC
        LIMIT 5
        """,
        (session["user_id"], today.isoformat())
    ).fetchall()

    connection.close()

    # Chart data: the priority with the most tasks fills the whole row and
    # the others are scaled against it. Inside each bar, the completed part
    # is that priority's share of finished tasks.
    counts = {row["priority"]: row for row in priority_rows}
    largest = max(
        [counts[name]["total"] for name in PRIORITIES if name in counts],
        default=0
    )

    priority_breakdown = []

    for name in PRIORITIES:
        total = counts[name]["total"] if name in counts else 0
        completed = counts[name]["completed"] if name in counts else 0

        bar_width = round(total / largest * 100) if largest else 0

        # Keep a sliver visible for small non-zero counts.
        if total > 0:
            bar_width = max(bar_width, 2)

        priority_breakdown.append({
            "name": name,
            "total": total,
            "completed": completed,
            "pending": total - completed,
            "bar_width": bar_width,
            "completed_width": round(completed / total * 100) if total else 0
        })

    overdue_list = with_deadline_labels(overdue_rows, today)

    return render_template(
        "dashboard.html",
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        pending_tasks=pending_tasks,
        overdue_tasks=overdue_tasks,
        completion_percentage=completion_percentage,
        priority_breakdown=priority_breakdown,
        upcoming_tasks=with_deadline_labels(upcoming_rows, today),
        overdue_list=overdue_list,
        more_overdue=overdue_tasks - len(overdue_list)
    )


@app.route("/tasks", methods=["GET", "POST"])
def tasks():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        title = request.form["title"].strip()

        if not title or len(title) > 100:
            return "Task title must contain between 1 and 100 characters.", 400

        priority = request.form["priority"]
        deadline = request.form["deadline"]

        connection = get_db_connection()

        connection.execute(
            """
            INSERT INTO tasks (user_id, title, priority, deadline)
            VALUES (?, ?, ?, ?)
            """,
            (session["user_id"], title, priority, deadline)
        )

        connection.commit()
        connection.close()

        return redirect(url_for("tasks"))

    connection = get_db_connection()

    status = request.args.get("status", "all")
    priority = request.args.get("priority", "all")

    query = "SELECT * FROM tasks WHERE user_id = ?"
    parameters = [session["user_id"]]

    if status == "pending":
        query += " AND completed = 0"

    elif status == "completed":
        query += " AND completed = 1"

    if priority in ["Low", "Medium", "High"]:
        query += " AND priority = ?"
        parameters.append(priority)

    query += " ORDER BY id DESC"

    user_tasks = connection.execute(
        query,
        parameters
    ).fetchall()

    connection.close()

    return render_template(
        "tasks.html",
        tasks=user_tasks,
        selected_status=status,
        selected_priority=priority,
        today=date.today().isoformat()
    )


def redirect_to_tasks():
    # Return to My Tasks with the filters the user had applied.
    # Only known values are passed on, so the form cannot inject anything else.
    filters = {}

    status = request.form.get("status")
    priority = request.form.get("priority")

    if status in ["pending", "completed"]:
        filters["status"] = status

    if priority in ["Low", "Medium", "High"]:
        filters["priority"] = priority

    return redirect(url_for("tasks", **filters))


@app.route("/complete/<int:task_id>", methods=["POST"])
def complete_task(task_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_db_connection()

    connection.execute(
        """
        UPDATE tasks
        SET completed = 1
        WHERE id = ? AND user_id = ?
        """,
        (task_id, session["user_id"])
    )

    connection.commit()
    connection.close()

    return redirect_to_tasks()


@app.route("/undo/<int:task_id>", methods=["POST"])
def undo_task(task_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_db_connection()

    connection.execute(
        """
        UPDATE tasks
        SET completed = 0
        WHERE id = ? AND user_id = ?
        """,
        (task_id, session["user_id"])
    )

    connection.commit()
    connection.close()

    return redirect_to_tasks()


@app.route("/edit/<int:task_id>", methods=["GET", "POST"])
def edit_task(task_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_db_connection()

    task = connection.execute(
        """
        SELECT * FROM tasks
        WHERE id = ? AND user_id = ?
        """,
        (task_id, session["user_id"])
    ).fetchone()

    if task is None:
        connection.close()
        return "Task not found", 404

    if request.method == "POST":
        title = request.form["title"]

        connection.execute(
            """
            UPDATE tasks
            SET title = ?
            WHERE id = ? AND user_id = ?
            """,
            (title, task_id, session["user_id"])
        )

        connection.commit()
        connection.close()

        return redirect(url_for("tasks"))

    connection.close()

    return render_template(
        "edit_task.html",
        task=task
    )

    
@app.route("/delete/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_db_connection()

    connection.execute(
        """
        DELETE FROM tasks
        WHERE id = ? AND user_id = ?
        """,
        (task_id, session["user_id"])
    )

    connection.commit()
    connection.close()

    return redirect_to_tasks()

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))