"""Completing and undoing tasks.

Run all tests from the project folder with:  python -m unittest -v
"""
import re
import unittest
from contextlib import closing
from urllib.parse import parse_qs, urlparse

from tests.helpers import app, csrf_from, db


def completed_flag(task_id):
    with closing(db()) as connection:
        return connection.execute(
            "SELECT completed FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()["completed"]


class UndoTaskTests(unittest.TestCase):
    counter = 0

    def new_user(self):
        UndoTaskTests.counter += 1
        client = app.test_client()
        email = f"user{UndoTaskTests.counter}@example.com"
        token = csrf_from(client.get("/register").get_data(as_text=True))
        client.post("/register", data={"csrf_token": token, "name": "Tester",
                                       "email": email, "password": "pw-123456"})
        token = csrf_from(client.get("/login").get_data(as_text=True))
        client.post("/login", data={"csrf_token": token, "email": email,
                                    "password": "pw-123456"})
        return client

    def token(self, client):
        return csrf_from(client.get("/tasks").get_data(as_text=True))

    def add_task(self, client, title, priority="Medium"):
        client.post("/tasks", data={"csrf_token": self.token(client), "title": title,
                                    "priority": priority, "deadline": ""})
        with closing(db()) as connection:
            return connection.execute(
                "SELECT id FROM tasks WHERE title = ? ORDER BY id DESC", (title,)
            ).fetchone()["id"]

    def stats(self, client):
        html = client.get("/dashboard").get_data(as_text=True)
        return re.findall(r'<p class="stat-value">([^<]+)</p>', html)

    def page(self, client, query=""):
        return client.get("/tasks" + query).get_data(as_text=True)

    # --- Behaviour -------------------------------------------------------

    def test_complete_then_undo_updates_status_and_statistics(self):
        client = self.new_user()
        task_a = self.add_task(client, "Task A")
        self.add_task(client, "Task B")
        self.assertEqual(self.stats(client), ["2", "0", "2", "0", "0.0%"])

        client.post(f"/complete/{task_a}", data={"csrf_token": self.token(client)})
        self.assertEqual(completed_flag(task_a), 1)
        self.assertEqual(self.stats(client), ["2", "1", "1", "0", "50.0%"])

        client.post(f"/undo/{task_a}", data={"csrf_token": self.token(client)})
        self.assertEqual(completed_flag(task_a), 0)
        self.assertEqual(self.stats(client), ["2", "0", "2", "0", "0.0%"])

    def test_completed_task_shows_undo_instead_of_complete(self):
        client = self.new_user()
        task_id = self.add_task(client, "Show buttons")
        html = self.page(client)
        self.assertIn(f'action="/complete/{task_id}"', html)
        self.assertNotIn(f'action="/undo/{task_id}"', html)

        client.post(f"/complete/{task_id}", data={"csrf_token": self.token(client)})
        html = self.page(client)
        self.assertIn(f'action="/undo/{task_id}"', html)
        self.assertNotIn(f'action="/complete/{task_id}"', html)
        self.assertIn("✓ Completed", html)

    def test_undo_works_with_status_and_priority_filters(self):
        client = self.new_user()
        task_id = self.add_task(client, "Filtered task", priority="High")
        client.post(f"/complete/{task_id}", data={"csrf_token": self.token(client)})

        html = self.page(client, "?status=completed&priority=High")
        self.assertIn("Filtered task", html)
        self.assertIn('name="status" value="completed"', html)
        self.assertIn('name="priority" value="High"', html)

        response = client.post(f"/undo/{task_id}", data={
            "csrf_token": self.token(client), "status": "completed", "priority": "High"})
        self.assertEqual(urlparse(response.headers["Location"]).path, "/tasks")
        query = parse_qs(urlparse(response.headers["Location"]).query)
        self.assertEqual(query, {"status": ["completed"], "priority": ["High"]})

        self.assertNotIn("Filtered task", self.page(client, "?status=completed"))
        self.assertIn("Filtered task", self.page(client, "?status=pending&priority=High"))

    def test_unknown_filter_values_are_dropped_from_redirect(self):
        client = self.new_user()
        task_id = self.add_task(client, "Redirect safety")
        response = client.post(f"/complete/{task_id}", data={
            "csrf_token": self.token(client),
            "status": "https://evil.example", "priority": "<script>"})
        self.assertEqual(urlparse(response.headers["Location"]).path, "/tasks")
        self.assertEqual(urlparse(response.headers["Location"]).query, "")

    # --- Security --------------------------------------------------------

    def test_undo_rejects_get_requests(self):
        client = self.new_user()
        task_id = self.add_task(client, "GET check")
        client.post(f"/complete/{task_id}", data={"csrf_token": self.token(client)})
        self.assertEqual(client.get(f"/undo/{task_id}").status_code, 405)
        self.assertEqual(completed_flag(task_id), 1)

    def test_undo_requires_csrf_token(self):
        client = self.new_user()
        task_id = self.add_task(client, "CSRF check")
        client.post(f"/complete/{task_id}", data={"csrf_token": self.token(client)})
        self.assertEqual(client.post(f"/undo/{task_id}").status_code, 400)
        self.assertEqual(completed_flag(task_id), 1)

    def test_undo_requires_login(self):
        owner = self.new_user()
        task_id = self.add_task(owner, "Login check")
        owner.post(f"/complete/{task_id}", data={"csrf_token": self.token(owner)})

        anonymous = app.test_client()
        token = csrf_from(anonymous.get("/login").get_data(as_text=True))
        response = anonymous.post(f"/undo/{task_id}", data={"csrf_token": token})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlparse(response.headers["Location"]).path, "/login")
        self.assertEqual(completed_flag(task_id), 1)

    def test_user_cannot_undo_another_users_task(self):
        owner = self.new_user()
        task_id = self.add_task(owner, "Ownership check")
        owner.post(f"/complete/{task_id}", data={"csrf_token": self.token(owner)})

        attacker = self.new_user()
        attacker.post(f"/undo/{task_id}", data={"csrf_token": self.token(attacker)})
        self.assertEqual(completed_flag(task_id), 1)

    def test_undo_of_missing_task_is_harmless(self):
        client = self.new_user()
        response = client.post("/undo/999999", data={"csrf_token": self.token(client)})
        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main(verbosity=2)
