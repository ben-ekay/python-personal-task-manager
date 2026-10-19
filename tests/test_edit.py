"""Editing title, priority and deadline, shared validation and the safe
return_to mechanism.

Run all tests from the project folder with:  python -m unittest -v
"""
import re
import unittest
from contextlib import closing
from html import unescape
from urllib.parse import parse_qs, urlparse

from tests.helpers import app, csrf_from, db


def row(task_id):
    with closing(db()) as connection:
        return connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()


def location(response):
    parsed = urlparse(response.headers["Location"])
    return parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}


def hidden_fields(html):
    return dict(re.findall(r'<input type="hidden" name="(return_\w+)" value="([^"]*)">', html))


def cancel_href(html):
    return unescape(re.search(r'<a href="([^"]*)" class="button secondary">Cancel</a>', html).group(1))


class EditTests(unittest.TestCase):
    counter = 0

    def new_user(self):
        EditTests.counter += 1
        client = app.test_client()
        email = f"edit{EditTests.counter}@example.com"
        token = csrf_from(client.get("/register").get_data(as_text=True))
        client.post("/register", data={"csrf_token": token, "name": "Edit",
                                       "email": email, "password": "pw-123456"})
        token = csrf_from(client.get("/login").get_data(as_text=True))
        client.post("/login", data={"csrf_token": token, "email": email,
                                    "password": "pw-123456"})
        with closing(db()) as connection:
            user_id = connection.execute("SELECT id FROM users WHERE email = ?",
                                         (email,)).fetchone()["id"]
        return client, user_id

    def insert(self, user_id, title="Task", priority="Medium", deadline="2026-10-15", completed=0):
        with closing(db()) as connection:
            cursor = connection.execute(
                "INSERT INTO tasks (user_id, title, priority, deadline, completed) "
                "VALUES (?, ?, ?, ?, ?)", (user_id, title, priority, deadline, completed))
            connection.commit()
            return cursor.lastrowid

    def edit_page(self, client, task_id, query=""):
        response = client.get(f"/edit/{task_id}{query}")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def save(self, client, task_id, query="", **fields):
        html = self.edit_page(client, task_id, query)
        data = {"csrf_token": csrf_from(html), **hidden_fields(html),
                "title": "Saved title", "priority": "Medium", "deadline": "2026-10-20"}
        data.update(fields)
        data = {k: v for k, v in data.items() if v is not None}
        return client.post(f"/edit/{task_id}", data=data)

    # --- Showing the form ------------------------------------------------------

    def test_form_shows_current_values(self):
        client, user = self.new_user()
        task_id = self.insert(user, 'Quote " and <tag>', "High", "2026-10-15")
        html = self.edit_page(client, task_id)
        self.assertIn('value="Quote &#34; and &lt;tag&gt;" maxlength="100" required', html)
        self.assertRegex(html, r'<option value="High" selected>High</option>')
        self.assertEqual(len(re.findall(r"<option [^>]*selected", html)), 1)
        self.assertIn('name="deadline" value="2026-10-15"', html)
        self.assertNotRegex(html, r'id="no_deadline"[^>]*checked')
        self.assertNotIn("form-warning", html)

    def test_missing_deadline_shows_empty_field(self):
        client, user = self.new_user()
        for value in [None, ""]:
            task_id = self.insert(user, deadline=value)
            html = self.edit_page(client, task_id)
            self.assertIn('name="deadline" value=""', html)
            self.assertNotIn("form-warning", html)

    def test_invalid_saved_deadline_shows_warning(self):
        client, user = self.new_user()
        task_id = self.insert(user, deadline="2026-02-31")
        html = self.edit_page(client, task_id)
        self.assertIn('name="deadline" value=""', html)
        self.assertIn("The saved deadline “2026-02-31” is not a valid date.", html)
        self.assertIn('aria-describedby="saved-deadline-warning"', html)

    # --- Saving ---------------------------------------------------------------

    def test_save_updates_title_priority_and_deadline(self):
        client, user = self.new_user()
        task_id = self.insert(user, "Old", "Low", "2026-10-15")
        response = self.save(client, task_id, title="  New title  ", priority="High",
                             deadline="2026-12-24")
        self.assertEqual(response.status_code, 302)
        saved = row(task_id)
        self.assertEqual((saved["title"], saved["priority"], saved["deadline"]),
                         ("New title", "High", "2026-12-24"))

    def test_clear_deadline_with_empty_field_or_checkbox(self):
        client, user = self.new_user()
        first = self.insert(user, deadline="2026-10-15")
        self.save(client, first, deadline="")
        self.assertEqual(row(first)["deadline"], "")

        second = self.insert(user, deadline="2026-10-15")
        self.save(client, second, deadline="2026-11-01", no_deadline="1")
        self.assertEqual(row(second)["deadline"], "")

        calendar = client.get("/calendar?year=2026&month=10").get_data(as_text=True)
        self.assertRegex(calendar, r"2 tasks have no deadline\.")
        self.assertNotIn("invalid deadline", calendar)

    def test_saving_fixes_an_invalid_saved_deadline(self):
        client, user = self.new_user()
        task_id = self.insert(user, deadline="abc")
        self.save(client, task_id, deadline="2026-10-30")
        self.assertEqual(row(task_id)["deadline"], "2026-10-30")

    def test_only_title_priority_and_deadline_can_change(self):
        client, user = self.new_user()
        other_client, other_user = self.new_user()
        task_id = self.insert(user, completed=0)
        self.save(client, task_id, completed="1", user_id=str(other_user), id="999")
        saved = row(task_id)
        self.assertEqual((saved["id"], saved["completed"], saved["user_id"]), (task_id, 0, user))

    # --- Validation -------------------------------------------------------------

    def assert_rejected(self, client, task_id, message, **fields):
        before = dict(row(task_id))
        response = self.save(client, task_id, **fields)
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 400, fields)
        self.assertIn('class="form-errors" role="alert"', html)
        self.assertIn(message, html)
        self.assertEqual(dict(row(task_id)), before, fields)
        return html

    def test_title_validation(self):
        client, user = self.new_user()
        task_id = self.insert(user)
        message = "Task title must contain between 1 and 100 characters."
        for title in ["", "     ", "x" * 101]:
            html = self.assert_rejected(client, task_id, message, title=title)
            self.assertRegex(html, r'id="title"[^>]*\s+aria-invalid="true"')
        self.assert_rejected(client, task_id, message, title=None)
        self.save(client, task_id, title="x" * 100)
        self.assertEqual(row(task_id)["title"], "x" * 100)

    def test_priority_validation(self):
        client, user = self.new_user()
        task_id = self.insert(user)
        for priority in ["Urgent", "high", "", " High", None]:
            self.assert_rejected(client, task_id, "Please choose a valid priority.", priority=priority)

    def test_deadline_validation(self):
        client, user = self.new_user()
        task_id = self.insert(user)
        message = "Please enter a real date between 1900 and 2200"
        for deadline in ["2026-02-31", "2025-02-29", "abc", "20261019", "2026-1-5",
                         "2026-10-19T10:00", "1899-12-31", "2201-01-01", "0000-01-01"]:
            html = self.assert_rejected(client, task_id, message, deadline=deadline)
            self.assertRegex(html, r'id="deadline"[^>]*\s+aria-invalid="true"')
        for deadline in ["2024-02-29", "1900-01-01", "2200-12-31", " 2026-10-19 "]:
            self.save(client, task_id, deadline=deadline)
            self.assertEqual(row(task_id)["deadline"], deadline.strip())

    def test_errors_keep_the_users_input(self):
        client, user = self.new_user()
        task_id = self.insert(user, "Original", "Low", "2026-10-15")
        html = self.assert_rejected(client, task_id, "Please choose a valid priority.",
                                    title="Typed title", priority="Urgent", deadline="2026-11-11",
                                    no_deadline="1")
        self.assertIn('value="Typed title"', html)
        self.assertRegex(html, r'id="no_deadline"[^>]*\s+checked')

    def test_add_task_uses_the_same_validation(self):
        client, user = self.new_user()
        token = csrf_from(client.get("/tasks").get_data(as_text=True))

        def add(**fields):
            data = {"csrf_token": token, "title": "Added", "priority": "Low", "deadline": ""}
            data.update(fields)
            return client.post("/tasks", data={k: v for k, v in data.items() if v is not None})

        for fields in [{"priority": "Urgent"}, {"priority": None}, {"deadline": "2026-02-31"},
                       {"deadline": "20261019"}, {"title": " "}, {"title": "x" * 101}]:
            self.assertEqual(add(**fields).status_code, 400, fields)
        with closing(db()) as connection:
            count = connection.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", (user,)).fetchone()[0]
        self.assertEqual(count, 0)

        self.assertEqual(add(deadline=None).status_code, 302)
        self.assertEqual(add(title="  Dated  ", deadline="2026-10-19").status_code, 302)
        with closing(db()) as connection:
            stored = connection.execute("SELECT title, deadline FROM tasks WHERE user_id = ? ORDER BY id",
                                        (user,)).fetchall()
        self.assertEqual([tuple(r) for r in stored], [("Added", ""), ("Dated", "2026-10-19")])
        self.assertIn('maxlength="100" required', client.get("/tasks").get_data(as_text=True))

    # --- Return navigation --------------------------------------------------------

    def test_return_to_calendar_month(self):
        client, user = self.new_user()
        task_id = self.insert(user)
        query = "?return_to=calendar&return_year=2026&return_month=10"
        html = self.edit_page(client, task_id, query)
        self.assertEqual(hidden_fields(html), {"return_to": "calendar", "return_year": "2026",
                                               "return_month": "10"})
        self.assertEqual(cancel_href(html), "/calendar?year=2026&month=10")
        self.assertEqual(location(self.save(client, task_id, query)),
                         ("/calendar", {"year": "2026", "month": "10"}))

    def test_return_to_tasks_with_filters(self):
        client, user = self.new_user()
        task_id = self.insert(user)
        query = "?return_to=tasks&return_status=pending&return_priority=High"
        html = self.edit_page(client, task_id, query)
        self.assertEqual(cancel_href(html), "/tasks?status=pending&priority=High")
        self.assertEqual(location(self.save(client, task_id, query)),
                         ("/tasks", {"status": "pending", "priority": "High"}))

    def test_return_to_dashboard(self):
        client, user = self.new_user()
        task_id = self.insert(user)
        html = self.edit_page(client, task_id, "?return_to=dashboard")
        self.assertEqual(cancel_href(html), "/dashboard")
        self.assertEqual(location(self.save(client, task_id, "?return_to=dashboard")), ("/dashboard", {}))

    def test_defaults_and_invalid_context(self):
        client, user = self.new_user()
        task_id = self.insert(user)
        cases = {
            "": ("/tasks", {}),
            "?return_to=tasks&return_status=all&return_priority=all": ("/tasks", {}),
            "?return_to=tasks&return_status=deleted&return_priority=Urgent": ("/tasks", {}),
            "?return_to=calendar&return_year=2026&return_month=13": ("/calendar", {}),
            "?return_to=calendar&return_year=9999&return_month=12": ("/calendar", {}),
            "?return_to=calendar&return_year=abc": ("/calendar", {}),
            "?return_to=calendar": ("/calendar", {}),
        }
        for query, expected in cases.items():
            self.assertEqual(location(self.save(client, task_id, query)), expected, query)

    def test_open_redirect_attempts_go_to_my_tasks(self):
        client, user = self.new_user()
        task_id = self.insert(user)
        attacks = ["https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)",
                   "%2F%2Fevil.example", "https:%2F%2Fevil.example", "CALENDAR", " calendar",
                   "calendar_page", "/calendar", "logout", "delete_task"]
        for attack in attacks:
            html = self.edit_page(client, task_id, "?return_to=" + attack)
            self.assertEqual(cancel_href(html), "/tasks", attack)
            self.assertEqual(hidden_fields(html), {"return_to": "tasks"}, attack)
            response = client.post(f"/edit/{task_id}", data={
                "csrf_token": csrf_from(html), "return_to": attack, "title": "x",
                "priority": "Low", "deadline": ""})
            self.assertEqual(response.headers["Location"], "/tasks", attack)

    def test_tampered_hidden_fields_are_rechecked(self):
        client, user = self.new_user()
        task_id = self.insert(user)
        html = self.edit_page(client, task_id, "?return_to=calendar&return_year=2026&return_month=10")
        response = client.post(f"/edit/{task_id}", data={
            "csrf_token": csrf_from(html), "return_to": "calendar", "return_year": "2026",
            "return_month": "99", "title": "x", "priority": "Low", "deadline": ""})
        self.assertEqual(location(response), ("/calendar", {}))

    def test_links_carry_return_context(self):
        client, user = self.new_user()
        from datetime import date, timedelta
        soon = (date.today() + timedelta(days=1)).isoformat()
        task_id = self.insert(user, "Linked", "High", soon)

        tasks_html = client.get("/tasks?status=pending&priority=High").get_data(as_text=True)
        self.assertIn(f'href="/edit/{task_id}?return_to=tasks&amp;return_status=pending&amp;return_priority=High"',
                      tasks_html)
        dashboard_html = client.get("/dashboard").get_data(as_text=True)
        self.assertIn(f'href="/edit/{task_id}?return_to=dashboard"', dashboard_html)
        d = date.fromisoformat(soon)
        calendar_html = client.get(f"/calendar?year={d.year}&month={d.month}").get_data(as_text=True)
        self.assertIn(f'href="/edit/{task_id}?return_to=calendar&amp;return_year={d.year}'
                      f'&amp;return_month={d.month}"', calendar_html)

    def test_sidebar_highlights_the_page_you_came_from(self):
        client, user = self.new_user()
        task_id = self.insert(user)
        for query, active in [("", "My Tasks"), ("?return_to=calendar", "Calendar"),
                              ("?return_to=dashboard", "Dashboard"), ("?return_to=evil", "My Tasks")]:
            html = self.edit_page(client, task_id, query)
            current = re.search(r'class="nav-link active"\s+aria-current="page">.*?<span>([^<]+)</span>',
                                html, re.S).group(1)
            self.assertEqual(current, active, query)

    # --- Security -------------------------------------------------------------------

    def test_other_users_cannot_view_or_edit(self):
        owner, owner_id = self.new_user()
        task_id = self.insert(owner_id, "Private", "Low", "2026-10-15")
        attacker, _ = self.new_user()
        self.assertEqual(attacker.get(f"/edit/{task_id}").status_code, 404)
        token = csrf_from(attacker.get("/tasks").get_data(as_text=True))
        response = attacker.post(f"/edit/{task_id}", data={
            "csrf_token": token, "title": "Hacked", "priority": "High", "deadline": ""})
        self.assertEqual(response.status_code, 404)
        saved = row(task_id)
        self.assertEqual((saved["title"], saved["priority"], saved["deadline"]),
                         ("Private", "Low", "2026-10-15"))

    def test_csrf_and_login_required(self):
        client, user = self.new_user()
        task_id = self.insert(user, "Keep")
        response = client.post(f"/edit/{task_id}", data={"title": "No token", "priority": "Low",
                                                         "deadline": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(row(task_id)["title"], "Keep")

        anonymous = app.test_client()
        self.assertEqual(location(anonymous.get(f"/edit/{task_id}"))[0], "/login")
        token = csrf_from(anonymous.get("/login").get_data(as_text=True))
        response = anonymous.post(f"/edit/{task_id}", data={"csrf_token": token, "title": "Anon",
                                                             "priority": "Low", "deadline": ""})
        self.assertEqual(location(response)[0], "/login")
        self.assertEqual(row(task_id)["title"], "Keep")

    def test_error_messages_and_values_are_escaped(self):
        client, user = self.new_user()
        task_id = self.insert(user, deadline="<script>alert(1)</script>")
        html = self.edit_page(client, task_id)
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        response = self.save(client, task_id, title='"><img src=x onerror=alert(2)>', priority="bad")
        self.assertNotIn("<img src=x", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main(verbosity=1)
