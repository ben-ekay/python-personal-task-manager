"""Dashboard (analytics) and My Tasks (management) are separate pages.

Run all tests from the project folder with:  python -m unittest -v
"""
import re
import unittest
from urllib.parse import parse_qs, urlparse

from tests.helpers import app, csrf_from, query_one


def location(response):
    parsed = urlparse(response.headers["Location"])
    return parsed.path, parse_qs(parsed.query)


class PageTests(unittest.TestCase):
    counter = 0

    def new_user(self):
        PageTests.counter += 1
        client = app.test_client()
        email = f"pages{PageTests.counter}@example.com"
        token = csrf_from(client.get("/register").get_data(as_text=True))
        client.post("/register", data={"csrf_token": token, "name": "Pages",
                                       "email": email, "password": "pw-123456"})
        token = csrf_from(client.get("/login").get_data(as_text=True))
        response = client.post("/login", data={"csrf_token": token, "email": email,
                                               "password": "pw-123456"})
        self.assertEqual(location(response)[0], "/dashboard")
        return client

    def token(self, client):
        return csrf_from(client.get("/tasks").get_data(as_text=True))

    def add_task(self, client, title, priority="Medium", deadline=""):
        response = client.post("/tasks", data={"csrf_token": self.token(client), "title": title,
                                               "priority": priority, "deadline": deadline})
        self.assertEqual(location(response)[0], "/tasks")
        return query_one("SELECT id FROM tasks WHERE title = ? ORDER BY id DESC", (title,))["id"]

    def html(self, client, path):
        return client.get(path).get_data(as_text=True)

    def stats(self, client):
        return re.findall(r'<p class="stat-value">([^<]+)</p>', self.html(client, "/dashboard"))

    # --- Access control --------------------------------------------------

    def test_public_pages_render(self):
        client = app.test_client()
        for path in ["/", "/login", "/register"]:
            self.assertEqual(client.get(path).status_code, 200, path)

    def test_pages_require_login(self):
        anonymous = app.test_client()
        for path in ["/dashboard", "/tasks"]:
            self.assertEqual(location(anonymous.get(path))[0], "/login", path)

        token = csrf_from(self.html(anonymous, "/login"))
        response = anonymous.post("/tasks", data={"csrf_token": token, "title": "x",
                                                  "priority": "Low", "deadline": ""})
        self.assertEqual(location(response)[0], "/login")

    def test_dashboard_is_get_only(self):
        client = self.new_user()
        response = client.post("/dashboard", data={"csrf_token": self.token(client),
                                                   "title": "Should not be created",
                                                   "priority": "Low", "deadline": ""})
        self.assertEqual(response.status_code, 405)
        self.assertIsNone(query_one("SELECT id FROM tasks WHERE title = ?",
                                    ("Should not be created",)))

    def test_add_task_requires_csrf_and_valid_title(self):
        client = self.new_user()
        response = client.post("/tasks", data={"title": "No token", "priority": "Low",
                                               "deadline": ""})
        self.assertEqual(response.status_code, 400)
        for bad_title in ["   ", "x" * 101]:
            response = client.post("/tasks", data={"csrf_token": self.token(client),
                                                   "title": bad_title, "priority": "Low",
                                                   "deadline": ""})
            self.assertEqual(response.status_code, 400)

    # --- Page content ----------------------------------------------------

    def test_dashboard_shows_statistics_only(self):
        client = self.new_user()
        self.add_task(client, "Dashboard check")
        html = self.html(client, "/dashboard")
        self.assertEqual(self.stats(client), ["1", "0", "1", "0", "0.0%"])
        self.assertIn('class="progress-fill"', html)
        self.assertIn('href="/tasks"', html)
        for task_ui in ["add-task-form", "filter-form", "task-card", "Dashboard check",
                        "preserve-scroll.js"]:
            self.assertNotIn(task_ui, html, task_ui)

    def test_tasks_page_has_management_features(self):
        client = self.new_user()
        self.add_task(client, "Overdue task", "High", "2020-01-01")
        html = self.html(client, "/tasks")
        self.assertIn('action="/tasks" class="card add-task-form"', html)
        self.assertIn('method="GET" action="/tasks" class="card filter-form"', html)
        self.assertIn("Overdue task", html)
        self.assertIn("Overdue", html)
        self.assertIn("priority-high", html)
        self.assertNotIn('class="stat-value"', html)

    def test_sidebar_links_and_active_page(self):
        client = self.new_user()
        for path, active in [("/dashboard", "Dashboard"), ("/tasks", "My Tasks")]:
            html = self.html(client, path)
            self.assertIn('href="/dashboard"\n                    class="nav-link', html)
            self.assertIn('href="/tasks"\n                    class="nav-link', html)
            self.assertNotIn("#tasks", html.split("<main")[0])
            active_link = re.search(r'class="nav-link active"\s+aria-current="page">.*?<span>([^<]+)</span>',
                                    html, re.S)
            self.assertEqual(active_link.group(1), active, path)
            self.assertEqual(html.count('aria-current="page"'), 1)

    def test_scroll_preservation_on_tasks_page(self):
        client = self.new_user()
        task_id = self.add_task(client, "Scroll task")
        self.add_task(client, "Done task")
        done_id = query_one("SELECT id FROM tasks WHERE title = ?", ("Done task",))["id"]
        client.post(f"/complete/{done_id}", data={"csrf_token": self.token(client)})

        html = self.html(client, "/tasks")
        self.assertIn("/static/preserve-scroll.js", html)
        for action in [f"/complete/{task_id}", f"/undo/{done_id}"]:
            form = re.search(rf'<form method="POST" action="{action}"\s+class="([^"]+)"([^>]*)>', html)
            self.assertIn("data-preserve-scroll", form.group(2), action)
        delete = re.search(rf'<form method="POST" action="/delete/{task_id}"[^>]*>', html).group(0)
        self.assertNotIn("data-preserve-scroll", delete)

    # --- Redirects and filters --------------------------------------------

    def test_actions_return_to_tasks_with_filters(self):
        client = self.new_user()
        keep = {"status": "pending", "priority": "High"}
        task_id = self.add_task(client, "Filter redirect", "High")

        response = client.post(f"/complete/{task_id}", data={"csrf_token": self.token(client), **keep})
        self.assertEqual(location(response), ("/tasks", {"status": ["pending"], "priority": ["High"]}))

        response = client.post(f"/undo/{task_id}", data={"csrf_token": self.token(client), **keep})
        self.assertEqual(location(response), ("/tasks", {"status": ["pending"], "priority": ["High"]}))

        response = client.post(f"/delete/{task_id}", data={"csrf_token": self.token(client), **keep})
        self.assertEqual(location(response), ("/tasks", {"status": ["pending"], "priority": ["High"]}))
        self.assertIsNone(query_one("SELECT id FROM tasks WHERE id = ?", (task_id,)))

    def test_filter_values_are_rendered_into_action_forms(self):
        client = self.new_user()
        self.add_task(client, "Hidden filter", "Low")
        html = self.html(client, "/tasks?status=pending&priority=Low")
        self.assertEqual(html.count('name="status" value="pending"'), 2)   # complete + delete
        self.assertEqual(html.count('name="priority" value="Low"'), 2)

    def test_invalid_filters_are_dropped(self):
        client = self.new_user()
        task_id = self.add_task(client, "Bad filters")
        response = client.post(f"/delete/{task_id}", data={
            "csrf_token": self.token(client), "status": "//evil.example", "priority": "DROP"})
        self.assertEqual(location(response), ("/tasks", {}))

    def test_filters_on_tasks_page(self):
        client = self.new_user()
        self.add_task(client, "Low pending", "Low")
        done = self.add_task(client, "High done", "High")
        client.post(f"/complete/{done}", data={"csrf_token": self.token(client)})

        html = self.html(client, "/tasks?status=pending")
        self.assertIn("Low pending", html)
        self.assertNotIn("High done", html)
        html = self.html(client, "/tasks?status=completed&priority=High")
        self.assertIn("High done", html)
        self.assertNotIn("Low pending", html)

    def test_edit_returns_to_tasks(self):
        client = self.new_user()
        task_id = self.add_task(client, "Edit me")
        edit_html = self.html(client, f"/edit/{task_id}")
        self.assertIn('href="/tasks" class="button secondary">Cancel', edit_html)
        response = client.post(f"/edit/{task_id}", data={"csrf_token": csrf_from(edit_html),
                                                         "title": "Edited", "priority": "Medium",
                                                         "deadline": ""})
        self.assertEqual(location(response)[0], "/tasks")
        self.assertEqual(query_one("SELECT title FROM tasks WHERE id = ?", (task_id,))[0], "Edited")

    def test_statistics_follow_task_changes(self):
        client = self.new_user()
        first = self.add_task(client, "Stat one")
        self.add_task(client, "Stat two")
        client.post(f"/complete/{first}", data={"csrf_token": self.token(client)})
        self.assertEqual(self.stats(client), ["2", "1", "1", "0", "50.0%"])
        client.post(f"/delete/{first}", data={"csrf_token": self.token(client)})
        self.assertEqual(self.stats(client), ["1", "0", "1", "0", "0.0%"])

    # --- Ownership ----------------------------------------------------------

    def test_users_only_see_and_change_their_own_tasks(self):
        owner = self.new_user()
        task_id = self.add_task(owner, "Private task")

        other = self.new_user()
        self.assertNotIn("Private task", self.html(other, "/tasks"))
        self.assertEqual(self.stats(other), ["0", "0", "0", "0", "0%"])
        other.post(f"/complete/{task_id}", data={"csrf_token": self.token(other)})
        other.post(f"/delete/{task_id}", data={"csrf_token": self.token(other)})
        self.assertEqual(other.get(f"/edit/{task_id}").status_code, 404)

        row = query_one("SELECT completed FROM tasks WHERE id = ?", (task_id,))
        self.assertIsNotNone(row)
        self.assertEqual(row["completed"], 0)

    def test_logout(self):
        client = self.new_user()
        client.get("/logout")
        self.assertEqual(location(client.get("/tasks"))[0], "/login")


if __name__ == "__main__":
    unittest.main(verbosity=1)
