import calendar
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

# Normally task_manager.db. The automated tests point TASKFLOW_DATABASE at a
# temporary file so they never touch real data. It is read here, before the
# tables are created below, because that happens as soon as app.py is imported.
DATABASE = os.environ.get("TASKFLOW_DATABASE", "task_manager.db")


def get_db_connection():
    connection = sqlite3.connect(DATABASE)
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

# The calendar only shows years in this range, and deadlines must fall inside
# it. It keeps Python's calendar module well away from its limits (December
# 9999 would need days in the year 10000).
CALENDAR_FIRST_YEAR = 1900
CALENDAR_LAST_YEAR = 2200


def is_valid_deadline(value):
    # A real calendar date written exactly as YYYY-MM-DD, within the years the
    # calendar can show. Python 3.11+ also reads forms such as 20261019, so the
    # parsed date must turn back into exactly the same text.
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return False

    if parsed.isoformat() != value:
        return False

    return CALENDAR_FIRST_YEAR <= parsed.year <= CALENDAR_LAST_YEAR


def validate_task_fields(form):
    # Check the title, priority and deadline sent by the Add or Edit form.
    # Returns the cleaned values and a dictionary of error messages by field.
    title = form.get("title", "").strip()
    priority = form.get("priority", "")
    deadline = form.get("deadline", "").strip()

    # The "No deadline" checkbox always wins, so clearing works in every browser.
    if form.get("no_deadline"):
        deadline = ""

    errors = {}

    if not title or len(title) > 100:
        errors["title"] = "Task title must contain between 1 and 100 characters."

    if priority not in PRIORITIES:
        errors["priority"] = "Please choose a valid priority."

    if deadline and not is_valid_deadline(deadline):
        errors["deadline"] = (
            f"Please enter a real date between {CALENDAR_FIRST_YEAR} "
            f"and {CALENDAR_LAST_YEAR}, or leave the deadline empty."
        )

    values = {"title": title, "priority": priority, "deadline": deadline}

    return values, errors


def task_filters(source):
    # My Tasks filters from a form or URL. Only known values are passed on,
    # so nothing else can be injected into a redirect.
    filters = {}

    status = source.get("status")
    priority = source.get("priority")

    if status in ["pending", "completed"]:
        filters["status"] = status

    if priority in PRIORITIES:
        filters["priority"] = priority

    return filters


def calendar_month_params(source):
    # {"year": ..., "month": ...} when both are whole numbers in range,
    # otherwise an empty dictionary.
    try:
        year = int(source.get("year"))
        month = int(source.get("month"))
    except (TypeError, ValueError):
        return {}

    if not 1 <= month <= 12:
        return {}

    if not CALENDAR_FIRST_YEAR <= year <= CALENDAR_LAST_YEAR:
        return {}

    return {"year": year, "month": month}


def return_target(source):
    # Where Save and Cancel on the Edit page lead. return_to is a page name
    # from a fixed list, never a web address, and every extra value is checked
    # again here. url_for() builds the address, so it always stays in TaskFlow.
    page = source.get("return_to")

    if page == "calendar":
        params = calendar_month_params({
            "year": source.get("return_year"),
            "month": source.get("return_month")
        })
        url = url_for("calendar_page", **params)

    elif page == "dashboard":
        params = {}
        url = url_for("dashboard")

    else:
        page = "tasks"
        params = task_filters({
            "status": source.get("return_status"),
            "priority": source.get("return_priority")
        })
        url = url_for("tasks", **params)

    # Hidden fields that carry the same, already checked, values through Save.
    fields = {"return_to": page}

    for name, value in params.items():
        fields["return_" + name] = value

    return {"page": page, "url": url, "fields": fields}



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
        values, errors = validate_task_fields(request.form)

        if errors:
            return " ".join(errors.values()), 400

        connection = get_db_connection()

        connection.execute(
            """
            INSERT INTO tasks (user_id, title, priority, deadline)
            VALUES (?, ?, ?, ?)
            """,
            (session["user_id"], values["title"], values["priority"], values["deadline"])
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


def read_calendar_month(today):
    # Read ?year=...&month=... from the URL. Anything missing, not a number or
    # out of range falls back to the current month instead of causing an error.
    params = calendar_month_params(request.args)

    if not params:
        return today.year, today.month

    return params["year"], params["month"]


def task_status(task, today):
    # The same rules as the Dashboard: completed tasks are completed, pending
    # tasks with a deadline before today are overdue, and everything else
    # (including tasks due today) is pending. Only called for tasks whose
    # deadline is a real date, so comparing the YYYY-MM-DD text is safe.
    if task["completed"] == 1:
        return "completed"

    if task["completed"] == 0 and task["deadline"] < today.isoformat():
        return "overdue"

    return "pending"


@app.route("/calendar")
def calendar_page():
    if "user_id" not in session:
        return redirect(url_for("login"))

    today = date.today()
    year, month = read_calendar_month(today)

    # Full Monday-to-Sunday weeks, including days from the months either side.
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    first_shown = weeks[0][0]
    last_shown = weeks[-1][-1]

    connection = get_db_connection()

    rows = connection.execute(
        """
        SELECT id, title, priority, deadline, completed FROM tasks
        WHERE user_id = ?
        AND date(deadline) = deadline
        AND deadline BETWEEN ? AND ?
        ORDER BY deadline ASC, completed ASC,
            CASE priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END,
            id ASC
        """,
        (session["user_id"], first_shown.isoformat(), last_shown.isoformat())
    ).fetchall()

    # Tasks that cannot appear on any calendar: no deadline at all, or a
    # deadline that is not a real date. They are counted separately.
    deadline_counts = connection.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN deadline IS NULL OR deadline = ''
                THEN 1 ELSE 0 END), 0) AS missing,
            COALESCE(SUM(CASE WHEN deadline != '' AND date(deadline) IS NOT deadline
                THEN 1 ELSE 0 END), 0) AS invalid
        FROM tasks
        WHERE user_id = ?
        """,
        (session["user_id"],)
    ).fetchone()

    connection.close()

    tasks_by_day = {}

    for row in rows:
        task = dict(row, status=task_status(row, today))
        tasks_by_day.setdefault(row["deadline"], []).append(task)

    month_prefix = f"{year:04d}-{month:02d}-"
    month_tasks = [
        task
        for day_tasks in tasks_by_day.values()
        for task in day_tasks
        if task["deadline"].startswith(month_prefix)
    ]

    previous_year, previous_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    return render_template(
        "calendar.html",
        weeks=weeks,
        year=year,
        month=month,
        month_name=calendar.month_name[month],
        month_start=date(year, month, 1),
        today=today,
        tasks_by_day=tasks_by_day,
        has_visible_tasks=bool(rows),
        month_total=len(month_tasks),
        month_overdue=sum(1 for task in month_tasks if task["status"] == "overdue"),
        month_completed=sum(1 for task in month_tasks if task["status"] == "completed"),
        missing_deadlines=deadline_counts["missing"],
        invalid_deadlines=deadline_counts["invalid"],
        previous_link=(
            url_for("calendar_page", year=previous_year, month=previous_month)
            if previous_year >= CALENDAR_FIRST_YEAR else None
        ),
        next_link=(
            url_for("calendar_page", year=next_year, month=next_month)
            if next_year <= CALENDAR_LAST_YEAR else None
        )
    )


def redirect_to_tasks():
    # Return to My Tasks with the filters the user had applied.
    return redirect(url_for("tasks", **task_filters(request.form)))


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

    # Links to this page say where they came from in the URL; the form
    # carries the same (re-checked) values through Save.
    back = return_target(request.form if request.method == "POST" else request.args)

    # A saved deadline that is not a real date is shown as a warning rather
    # than being put into the date field, which could not display it.
    saved_deadline = task["deadline"] or ""
    invalid_saved_deadline = (
        saved_deadline if saved_deadline and not is_valid_deadline(saved_deadline) else ""
    )

    if request.method == "POST":
        values, errors = validate_task_fields(request.form)

        if errors:
            connection.close()

            return render_template(
                "edit_task.html",
                task=task,
                form=dict(values, no_deadline=bool(request.form.get("no_deadline"))),
                errors=errors,
                back=back,
                invalid_saved_deadline=invalid_saved_deadline
            ), 400

        # Only these three fields can change; completed and user_id cannot.
        connection.execute(
            """
            UPDATE tasks
            SET title = ?, priority = ?, deadline = ?
            WHERE id = ? AND user_id = ?
            """,
            (values["title"], values["priority"], values["deadline"],
             task_id, session["user_id"])
        )

        connection.commit()
        connection.close()

        return redirect(back["url"])

    connection.close()

    return render_template(
        "edit_task.html",
        task=task,
        form={
            "title": task["title"],
            "priority": task["priority"],
            "deadline": "" if invalid_saved_deadline else saved_deadline,
            "no_deadline": False
        },
        errors={},
        back=back,
        invalid_saved_deadline=invalid_saved_deadline
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