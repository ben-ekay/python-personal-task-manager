"""Dashboard analytics: statistics, priority chart and deadlines.

Run all tests from the project folder with:  python -m unittest -v
"""
import re
import unittest
from contextlib import closing
from html import unescape
from urllib.parse import urlparse

from tests.helpers import app, csrf_from, db, TODAY, day

from app import describe_deadline  # after tests.helpers, which sets up the test database


class DashboardTests(unittest.TestCase):
    counter = 0

    def new_user(self):
        DashboardTests.counter += 1
        client = app.test_client()
        email = f"dash{DashboardTests.counter}@example.com"
        token = csrf_from(client.get("/register").get_data(as_text=True))
        client.post("/register", data={"csrf_token": token, "name": "Dash",
                                       "email": email, "password": "pw-123456"})
        token = csrf_from(client.get("/login").get_data(as_text=True))
        client.post("/login", data={"csrf_token": token, "email": email,
                                    "password": "pw-123456"})
        with closing(db()) as connection:
            user_id = connection.execute("SELECT id FROM users WHERE email = ?",
                                         (email,)).fetchone()["id"]
        return client, user_id

    def insert(self, user_id, title, priority="Medium", deadline=None, completed=0):
        # Direct inserts let the tests create NULL, junk and impossible deadlines.
        with closing(db()) as connection:
            cursor = connection.execute(
                "INSERT INTO tasks (user_id, title, priority, deadline, completed) "
                "VALUES (?, ?, ?, ?, ?)", (user_id, title, priority, deadline, completed))
            connection.commit()
            return cursor.lastrowid

    def dashboard(self, client):
        response = client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def stats(self, html):
        return re.findall(r'<p class="stat-value[^"]*">([^<]+)</p>', html)

    def section(self, html, heading_id):
        start = html.index(f'id="{heading_id}"')
        end = html.find("</section>", start)
        return html[start:end]

    def titles(self, section_html):
        found = re.findall(r'class="deadline-title">\s*<span class="visually-hidden">Edit task: </span>(.*?)\s*</a>',
                           section_html, re.S)
        return [unescape(t) for t in found]

    def chart(self, html):
        rows = re.findall(
            r'priority-(high|medium|low)">(\w+)</span>.*?class="chart-bar" style="width: (\d+)%;">'
            r'\s*<div class="chart-bar-done" style="width: (\d+)%;">.*?class="chart-numbers">\s*(.*?)\s*</span>',
            html, re.S)
        return [(name, int(bar), int(done), " ".join(numbers.split())) for _, name, bar, done, numbers in rows]

    # --- Statistics ------------------------------------------------------

    def test_statistics_including_overdue(self):
        client, user = self.new_user()
        self.insert(user, "Overdue pending", deadline=day(-3))
        self.insert(user, "Overdue but done", deadline=day(-3), completed=1)
        self.insert(user, "Due today", deadline=day(0))
        self.insert(user, "Future", deadline=day(5))
        self.insert(user, "No deadline NULL", deadline=None)
        self.insert(user, "No deadline empty", deadline="")
        html = self.dashboard(client)
        self.assertEqual(self.stats(html), ["6", "1", "5", "1", "16.7%"])
        self.assertIn('class="stat-value stat-value-alert">1<', html)

    def test_invalid_and_impossible_deadlines_are_ignored_safely(self):
        client, user = self.new_user()
        for bad in ["2026-02-31", "2025-02-29", "2026-13-01", "2026-04-31", "abc",
                    "2026-2-1", " 2026-01-01", "2026-01-01 10:00", "1999-12-32"]:
            self.insert(user, f"Bad {bad}", deadline=bad)
        self.insert(user, "Leap day is real", deadline="2024-02-29")

        html = self.dashboard(client)
        self.assertEqual(self.stats(html)[3], "1")   # only the real leap day is overdue
        self.assertEqual(self.titles(self.section(html, "overdue-heading")), ["Leap day is real"])
        self.assertNotIn("Bad ", self.section(html, "upcoming-heading"))

    def test_year_zero_does_not_crash(self):
        # SQLite accepts 0000-01-01 but Python's date cannot represent it.
        self.assertEqual(describe_deadline("0000-01-01", TODAY), "")
        self.assertEqual(describe_deadline(None, TODAY), "")
        client, user = self.new_user()
        self.insert(user, "Year zero", deadline="0000-01-01")
        html = self.dashboard(client)
        self.assertIn("Year zero", self.section(html, "overdue-heading"))

    def test_empty_dashboard(self):
        client, _ = self.new_user()
        html = self.dashboard(client)
        self.assertEqual(self.stats(html), ["0", "0", "0", "0", "0%"])
        self.assertNotIn("stat-value-alert", html)
        self.assertIn("You have no tasks yet.", html)
        self.assertIn('href="/tasks">Add your first task', html)
        self.assertNotIn('class="chart-list"', html)
        self.assertIn("No upcoming deadlines.", html)
        self.assertIn("Nothing overdue. Nice work!", html)

    # --- Deadline lists ---------------------------------------------------

    def test_upcoming_deadlines_nearest_five(self):
        client, user = self.new_user()
        for offset in [9, 2, 0, 30, 1, 7, 4]:
            self.insert(user, f"Due {offset}", deadline=day(offset))
        self.insert(user, "Completed soon", deadline=day(1), completed=1)
        self.insert(user, "Overdue", deadline=day(-1))

        upcoming = self.section(self.dashboard(client), "upcoming-heading")
        self.assertEqual(self.titles(upcoming), ["Due 0", "Due 1", "Due 2", "Due 4", "Due 7"])
        for label in ["Due today", "Due tomorrow", "Due in 2 days", "Due in 7 days"]:
            self.assertIn(label, upcoming)
        self.assertIn(f'datetime="{day(0)}"', upcoming)
        d = TODAY
        self.assertIn(f"{d:%d}/{d:%m}/{d:%Y}", upcoming)

    def test_upcoming_tie_breaks_by_creation_order(self):
        client, user = self.new_user()
        first = self.insert(user, "First same day", deadline=day(3))
        self.insert(user, "Second same day", deadline=day(3))
        upcoming = self.section(self.dashboard(client), "upcoming-heading")
        self.assertEqual(self.titles(upcoming), ["First same day", "Second same day"])
        self.assertIn(f'href="/edit/{first}?return_to=dashboard"', upcoming)

    def test_overdue_section_oldest_first_with_more_link(self):
        client, user = self.new_user()
        for offset in [-1, -10, -3, -40, -2, -5, -7]:
            self.insert(user, f"Late {-offset}", deadline=day(offset))
        self.insert(user, "Late but done", deadline=day(-20), completed=1)

        html = self.dashboard(client)
        overdue = self.section(html, "overdue-heading")
        self.assertEqual(self.titles(overdue), ["Late 40", "Late 10", "Late 7", "Late 5", "Late 3"])
        self.assertIn("40 days overdue", overdue)
        self.assertIn('href="/tasks?status=pending"', overdue)
        self.assertRegex(overdue, r"\+ 2 more overdue tasks")
        self.assertEqual(self.stats(html)[3], "7")

    def test_one_day_overdue_label_and_singular_more(self):
        client, user = self.new_user()
        for offset in [-1, -2, -3, -4, -5, -6]:
            self.insert(user, f"L{-offset}", deadline=day(offset))
        overdue = self.section(self.dashboard(client), "overdue-heading")
        self.assertNotIn("1 day overdue", overdue)       # L1 is the 6th oldest, so not listed
        self.assertRegex(overdue, r"\+ 1 more overdue task\b")
        self.assertEqual(describe_deadline(day(-1), TODAY), "1 day overdue")

    # --- Priority chart ---------------------------------------------------

    def test_priority_chart_counts_and_widths(self):
        client, user = self.new_user()
        for i in range(5):
            self.insert(user, f"H{i}", "High", completed=1 if i < 2 else 0)
        for i in range(8):
            self.insert(user, f"M{i}", "Medium", completed=1 if i < 3 else 0)
        self.insert(user, "L0", "Low", completed=1)

        self.assertEqual(self.chart(self.dashboard(client)), [
            ("High", 62, 40, "5 tasks · 2 done · 3 to do"),     # 5/8 = 62.5 -> 62 (round half to even)
            ("Medium", 100, 38, "8 tasks · 3 done · 5 to do"),  # 3/8 = 37.5 -> 38
            ("Low", 12, 100, "1 task · 1 done · 0 to do"),      # 1/8 = 12.5 -> 12
        ])

    def test_priority_chart_missing_priorities_and_minimum_width(self):
        client, user = self.new_user()
        for i in range(120):
            self.insert(user, f"M{i}", "Medium")
        self.insert(user, "One high", "High")
        self.assertEqual(self.chart(self.dashboard(client)), [
            ("High", 2, 0, "1 task · 0 done · 1 to do"),        # 1/120 rounds to 1 -> minimum 2
            ("Medium", 100, 0, "120 tasks · 0 done · 120 to do"),
            ("Low", 0, 0, "0 tasks · 0 done · 0 to do"),
        ])

    def test_chart_names_are_coloured_text_and_bars_hidden_from_screen_readers(self):
        client, user = self.new_user()
        self.insert(user, "Any", "High")
        html = self.dashboard(client)
        self.assertIn('class="chart-label priority-value priority-high">High</span>', html)
        self.assertEqual(html.count('class="chart-track" aria-hidden="true"'), 3)
        self.assertIn('class="chart-legend" aria-hidden="true"', html)

    # --- Security and read-only behaviour --------------------------------

    def test_only_own_tasks_are_counted_and_listed(self):
        owner, owner_id = self.new_user()
        self.insert(owner_id, "Owner overdue", "High", deadline=day(-2))
        self.insert(owner_id, "Owner upcoming", "Low", deadline=day(2))

        other, other_id = self.new_user()
        self.insert(other_id, "Other task", "Medium")
        html = self.dashboard(other)
        self.assertEqual(self.stats(html), ["1", "0", "1", "0", "0.0%"])
        self.assertNotIn("Owner", html)
        self.assertEqual([row[3] for row in self.chart(html)],
                         ["0 tasks · 0 done · 0 to do", "1 task · 0 done · 1 to do",
                          "0 tasks · 0 done · 0 to do"])

        owner_html = self.dashboard(owner)
        self.assertNotIn("Other task", owner_html)
        self.assertEqual(self.stats(owner_html), ["2", "0", "2", "1", "0.0%"])

    def test_titles_are_escaped(self):
        client, user = self.new_user()
        self.insert(user, "<img src=x onerror=alert(1)>", deadline=day(1))
        self.insert(user, "<script>alert(2)</script>", deadline=day(-1))
        html = self.dashboard(client)
        self.assertNotIn("<img src=x", html)
        self.assertNotIn("<script>alert(2)", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)

    def test_dashboard_is_read_only_and_requires_login(self):
        client, user = self.new_user()
        self.insert(user, "Read only", deadline=day(1))
        html = self.dashboard(client)
        self.assertNotIn("<form", html.split('id="main-content"')[1])
        self.assertNotIn("csrf_token", html)
        token = csrf_from(client.get("/tasks").get_data(as_text=True))
        self.assertEqual(client.post("/dashboard", data={"csrf_token": token}).status_code, 405)
        response = app.test_client().get("/dashboard")
        self.assertEqual(urlparse(response.headers["Location"]).path, "/login")


if __name__ == "__main__":
    unittest.main(verbosity=1)
