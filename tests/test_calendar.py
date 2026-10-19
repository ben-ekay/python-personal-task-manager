"""Calendar page: grid, task status, navigation and security.

Run all tests from the project folder with:  python -m unittest -v
"""
import re
import unittest
from contextlib import closing
from datetime import date, timedelta
from html import unescape
from urllib.parse import parse_qs, urlparse

from tests.helpers import app, csrf_from, db, TODAY, day


def cells(html):
    """Return [(date, classes, [(status, task_id, title), ...]), ...] for every day cell."""
    result = []
    for classes, body in re.findall(r'<td class="calendar-day([^"]*)"(.*?)</td>', html, re.S):
        when = re.search(r'datetime="([^"]+)"', body).group(1)
        tasks = [(status, int(task_id), unescape(title).strip())
                 for status, task_id, title in re.findall(
                     r'<li class="calendar-task status-(\w+)">\s*<a href="/edit/(\d+)\?return_to=calendar&amp;return_year=\d+&amp;return_month=\d+".*?'
                     r'class="calendar-task-title">(.*?)</span>', body, re.S)]
        result.append((when, classes.split(), tasks))
    return result


def statuses(html):
    return {task_id: status for _, _, tasks in cells(html) for status, task_id, _ in tasks}


class CalendarTests(unittest.TestCase):
    counter = 0

    def new_user(self):
        CalendarTests.counter += 1
        client = app.test_client()
        email = f"cal{CalendarTests.counter}@example.com"
        token = csrf_from(client.get("/register").get_data(as_text=True))
        client.post("/register", data={"csrf_token": token, "name": "Cal",
                                       "email": email, "password": "pw-123456"})
        token = csrf_from(client.get("/login").get_data(as_text=True))
        client.post("/login", data={"csrf_token": token, "email": email,
                                    "password": "pw-123456"})
        with closing(db()) as connection:
            user_id = connection.execute("SELECT id FROM users WHERE email = ?",
                                         (email,)).fetchone()["id"]
        return client, user_id

    def insert(self, user_id, title, priority="Medium", deadline=None, completed=0):
        with closing(db()) as connection:
            cursor = connection.execute(
                "INSERT INTO tasks (user_id, title, priority, deadline, completed) "
                "VALUES (?, ?, ?, ?, ?)", (user_id, title, priority, deadline, completed))
            connection.commit()
            return cursor.lastrowid

    def page(self, client, query=""):
        response = client.get("/calendar" + query)
        self.assertEqual(response.status_code, 200, query)
        return response.get_data(as_text=True)

    def heading(self, html):
        return re.search(r'id="month-heading" class="calendar-title">([^<]+)<', html).group(1)

    def nav_link(self, html, label):
        match = re.search(rf'<a href="([^"]+)" class="button secondary">\s*(?:<span aria-hidden="true">←</span> )?{label}', html)
        if not match:
            return None
        parsed = urlparse(unescape(match.group(1)))
        return parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}

    # --- Access ------------------------------------------------------------

    def test_requires_login_and_is_get_only(self):
        response = app.test_client().get("/calendar")
        self.assertEqual(urlparse(response.headers["Location"]).path, "/login")
        client, _ = self.new_user()
        token = csrf_from(client.get("/tasks").get_data(as_text=True))
        self.assertEqual(client.post("/calendar", data={"csrf_token": token}).status_code, 405)

    def test_sidebar_link_active_only_on_calendar(self):
        client, _ = self.new_user()
        for path, active in [("/calendar", "Calendar"), ("/dashboard", "Dashboard"),
                             ("/tasks", "My Tasks")]:
            html = client.get(path).get_data(as_text=True)
            self.assertIn('href="/calendar"\n                    class="nav-link', html)
            current = re.search(r'class="nav-link active"\s+aria-current="page">.*?<span>([^<]+)</span>',
                                html, re.S).group(1)
            self.assertEqual(current, active, path)
            self.assertEqual(html.count('aria-current="page"'), 1)

    # --- Grid --------------------------------------------------------------

    def test_default_is_current_month_with_today_marked(self):
        client, _ = self.new_user()
        html = self.page(client)
        self.assertEqual(self.heading(html), f"{TODAY:%B} {TODAY.year}")
        today_cells = [c for c in cells(html) if "is-today" in c[1]]
        self.assertEqual([c[0] for c in today_cells], [TODAY.isoformat()])
        self.assertEqual(html.count('aria-current="date"'), 1)

    def test_monday_first_grid_and_neighbouring_days(self):
        client, _ = self.new_user()
        html = self.page(client, "?year=2026&month=10")
        self.assertEqual(self.heading(html), "October 2026")
        header = re.findall(r'<th scope="col">\s*<span aria-hidden="true">(\w+)</span>', html)
        self.assertEqual(header, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        grid = cells(html)
        self.assertEqual(len(grid), 35)
        self.assertEqual((grid[0][0], grid[-1][0]), ("2026-09-28", "2026-11-01"))
        outside = [c[0] for c in grid if "is-outside" in c[1]]
        self.assertEqual(outside, ["2026-09-28", "2026-09-29", "2026-09-30", "2026-11-01"])
        self.assertIn('<caption class="visually-hidden">Tasks for October 2026</caption>', html)

    def test_month_lengths(self):
        client, _ = self.new_user()
        feb_2021 = cells(self.page(client, "?year=2021&month=2"))
        self.assertEqual((len(feb_2021), feb_2021[0][0], feb_2021[-1][0]), (28, "2021-02-01", "2021-02-28"))
        feb_2024 = [c[0] for c in cells(self.page(client, "?year=2024&month=2"))]
        self.assertIn("2024-02-29", feb_2024)
        self.assertNotIn("2024-02-30", feb_2024)

    def test_tasks_placed_on_their_deadline_including_neighbouring_days(self):
        client, user = self.new_user()
        in_month = self.insert(user, "In October", deadline="2026-10-15")
        before = self.insert(user, "Late September", deadline="2026-09-29")
        after = self.insert(user, "First of November", deadline="2026-11-01")
        hidden = self.insert(user, "Too early", deadline="2026-09-27")
        html = self.page(client, "?year=2026&month=10")
        placed = {task_id: when for when, _, tasks in cells(html) for _, task_id, _ in tasks}
        self.assertEqual(placed, {in_month: "2026-10-15", before: "2026-09-29", after: "2026-11-01"})
        self.assertNotIn(hidden, placed)
        self.assertIn("Previous month</span>", html)
        self.assertIn("Next month</span>", html)
        # Neighbouring-month days only count in their own month's summary.
        self.assertRegex(html, r"1 task due in October")

    def test_order_within_a_day(self):
        client, user = self.new_user()
        done = self.insert(user, "Done high", "High", "2026-10-15", completed=1)
        low = self.insert(user, "Pending low", "Low", "2026-10-15")
        high = self.insert(user, "Pending high", "High", "2026-10-15")
        medium = self.insert(user, "Pending medium", "Medium", "2026-10-15")
        html = self.page(client, "?year=2026&month=10")
        day_tasks = next(tasks for when, _, tasks in cells(html) if when == "2026-10-15")
        self.assertEqual([t[1] for t in day_tasks], [high, medium, low, done])

    def test_busy_day_shows_three_then_more(self):
        client, user = self.new_user()
        ids = [self.insert(user, f"Busy {i}", deadline="2026-10-20") for i in range(5)]
        html = self.page(client, "?year=2026&month=10")
        body = re.search(r'<td class="calendar-day[^"]*"[^>]*>\s*<div class="day-heading">\s*'
                         r'<time class="day-number" datetime="2026-10-20".*?</td>', html, re.S).group(0)
        visible, more = body.split("<details", 1)
        self.assertEqual(len(re.findall(r'class="calendar-task ', visible)), 3)
        self.assertEqual(len(re.findall(r'class="calendar-task ', more)), 2)
        self.assertRegex(more, r"\+ 2 more<span class=\"visually-hidden\"> on 20 October")
        self.assertEqual(sorted(statuses(html)), sorted(ids))

    def test_empty_period_message(self):
        client, _ = self.new_user()
        html = self.page(client, "?year=2026&month=10")
        self.assertIn("No tasks with deadlines in this period.", html)
        self.assertRegex(html, r"0 tasks due in October\s+· 0 overdue · 0 completed")
        self.assertNotIn('class="calendar-notes"', html)

    # --- Status (must match the Dashboard) --------------------------------

    def test_status_rules(self):
        client, user = self.new_user()
        overdue = self.insert(user, "Overdue", deadline=day(-1))
        due_today = self.insert(user, "Due today", deadline=day(0))
        future = self.insert(user, "Future", deadline=day(1))
        done_past = self.insert(user, "Done past", deadline=day(-1), completed=1)
        done_today = self.insert(user, "Done today", deadline=day(0), completed=1)
        found = {}
        for when in {day(-1), day(0), day(1)}:
            d = date.fromisoformat(when)
            found.update(statuses(self.page(client, f"?year={d.year}&month={d.month}")))
        self.assertEqual(found, {overdue: "overdue", due_today: "pending", future: "pending",
                                 done_past: "completed", done_today: "completed"})

    def test_status_is_shown_without_relying_on_colour(self):
        client, user = self.new_user()
        self.insert(user, "Late", "High", day(-3))
        self.insert(user, "Finished", "Low", day(-3), completed=1)
        d = TODAY - timedelta(days=3)
        html = self.page(client, f"?year={d.year}&month={d.month}")
        self.assertRegex(html, r'aria-hidden="true">!</span>\s*<span class="visually-hidden">Overdue: </span>')
        self.assertRegex(html, r'aria-hidden="true">✓</span>\s*<span class="visually-hidden">Completed: </span>')
        self.assertIn('priority-value priority-high">', html)
        self.assertIn('title="Late (High priority, overdue)"', html)

    def test_statuses_consistent_with_dashboard(self):
        client, user = self.new_user()
        plan = [(-30, 0), (-4, 0), (-1, 0), (-1, 1), (0, 0), (0, 1), (2, 0), (6, 0), (25, 1)]
        ids = {self.insert(user, f"T{offset}/{done}", deadline=day(offset), completed=done): (offset, done)
               for offset, done in plan}
        calendar_status = {}
        for task_id, (offset, _) in ids.items():
            d = TODAY + timedelta(days=offset)
            calendar_status.update(statuses(self.page(client, f"?year={d.year}&month={d.month}")))
        self.assertEqual(set(calendar_status), set(ids))

        dashboard = client.get("/dashboard").get_data(as_text=True)

        def dashboard_ids(section):
            part = dashboard[dashboard.index(f'id="{section}"'):]
            part = part[:part.index("</section>")]
            return {int(i) for i in re.findall(r'href="/edit/(\d+)\?return_to=dashboard"', part)}

        overdue_count = int(re.findall(r'<p class="stat-value[^"]*">([^<]+)</p>', dashboard)[3])
        cal_overdue = {i for i, s in calendar_status.items() if s == "overdue"}
        cal_pending = {i for i, s in calendar_status.items() if s == "pending"}
        self.assertEqual(overdue_count, len(cal_overdue))
        self.assertEqual(dashboard_ids("overdue-heading"), cal_overdue)
        self.assertEqual(dashboard_ids("upcoming-heading"), cal_pending)

    # --- Deadlines that cannot be placed -------------------------------------

    def test_missing_and_invalid_deadlines_are_reported_separately(self):
        client, user = self.new_user()
        self.insert(user, "Null deadline", deadline=None)
        self.insert(user, "Empty deadline", deadline="")
        self.insert(user, "Impossible", deadline="2026-02-31")
        self.insert(user, "Junk", deadline="abc")
        self.insert(user, "Spaces", deadline="   ")
        html = self.page(client, "?year=2026&month=3")
        self.assertRegex(html, r"2 tasks have no deadline\.")
        self.assertRegex(html, r'calendar-note-warning">\s*3 tasks have\s+an invalid deadline date and cannot be shown')
        self.assertEqual(statuses(html), {})
        for title in ["Impossible", "Junk", "Spaces"]:
            self.assertNotIn(f">{title}<", html)

    def test_impossible_dates_do_not_count_in_their_month(self):
        # 2026-02-30 and 2026-02-31 sort inside February's date range, so only
        # the real-date check keeps them out of February's summary.
        client, user = self.new_user()
        self.insert(user, "Feb 30", deadline="2026-02-30")
        self.insert(user, "Feb 31", deadline="2026-02-31", completed=1)
        html = self.page(client, "?year=2026&month=2")
        self.assertRegex(html, r"0 tasks due in February\s+· 0 overdue · 0 completed")
        self.assertIn("No tasks with deadlines in this period.", html)
        self.assertRegex(html, r"2 tasks have\s+an invalid deadline date")

    def test_singular_notes(self):
        client, user = self.new_user()
        self.insert(user, "Null deadline", deadline=None)
        self.insert(user, "Impossible", deadline="2026-04-31")
        html = self.page(client)
        self.assertRegex(html, r"1 task has no deadline\.")
        self.assertRegex(html, r"1 task has\s+an invalid deadline date")

    # --- Navigation and input validation -------------------------------------

    def test_previous_and_next_links_wrap_years(self):
        client, _ = self.new_user()
        html = self.page(client, "?year=2026&month=12")
        self.assertEqual(self.nav_link(html, "Previous"), ("/calendar", {"year": "2026", "month": "11"}))
        self.assertEqual(self.nav_link(html, "Next"), ("/calendar", {"year": "2027", "month": "1"}))
        html = self.page(client, "?year=2027&month=1")
        self.assertEqual(self.nav_link(html, "Previous"), ("/calendar", {"year": "2026", "month": "12"}))
        self.assertEqual(self.nav_link(html, "Today"), ("/calendar", {}))

    def test_invalid_parameters_fall_back_to_current_month(self):
        client, _ = self.new_user()
        current = f"{TODAY:%B} {TODAY.year}"
        for query in ["?month=0&year=2026", "?month=13&year=2026", "?year=abc&month=5",
                      "?year=2026", "?month=5", "?year=99999&month=1", "?year=9999&month=12",
                      "?year=1899&month=12", "?year=2201&month=1", "?year=-1&month=1",
                      "?year=2026&month=5.5", "?year=" + "9" * 5000 + "&month=1"]:
            self.assertEqual(self.heading(self.page(client, query)), current, query[:40])

    def test_boundary_years_hide_links(self):
        client, _ = self.new_user()
        first = self.page(client, "?year=1900&month=1")
        self.assertEqual(self.heading(first), "January 1900")
        self.assertIsNone(self.nav_link(first, "Previous"))
        self.assertIsNotNone(self.nav_link(first, "Next"))
        last = self.page(client, "?year=2200&month=12")
        self.assertEqual(self.heading(last), "December 2200")
        self.assertIsNone(self.nav_link(last, "Next"))
        self.assertIsNotNone(self.nav_link(last, "Previous"))

    # --- Security ------------------------------------------------------------

    def test_only_own_tasks(self):
        owner, owner_id = self.new_user()
        mine = self.insert(owner_id, "Mine", deadline="2026-10-10")
        self.insert(owner_id, "Mine no deadline", deadline=None)
        other, other_id = self.new_user()
        theirs = self.insert(other_id, "Theirs", deadline="2026-10-10")
        html = self.page(other, "?year=2026&month=10")
        self.assertEqual(set(statuses(html)), {theirs})
        self.assertNotIn("Mine", html)
        self.assertNotIn("no deadline", html)
        self.assertRegex(html, r"1 task due in October")
        self.assertEqual(set(statuses(self.page(owner, "?year=2026&month=10"))), {mine})
        self.assertEqual(other.get(f"/edit/{mine}").status_code, 404)

    def test_titles_escaped_in_text_and_tooltip(self):
        client, user = self.new_user()
        self.insert(user, '"><script>alert(1)</script>', deadline="2026-10-10")
        html = self.page(client, "?year=2026&month=10")
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn('title="&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt; (Medium priority, overdue)"'
                      if date(2026, 10, 10) < TODAY else
                      'title="&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt; (Medium priority, pending)"', html)

    def test_links_open_edit_page(self):
        client, user = self.new_user()
        task_id = self.insert(user, "Open me", deadline="2026-10-10")
        html = self.page(client, "?year=2026&month=10")
        self.assertIn(f'<a href="/edit/{task_id}?return_to=calendar&amp;return_year=2026&amp;return_month=10"\n'
                      f'            class="calendar-task-link"', html)
        self.assertEqual(client.get(f"/edit/{task_id}").status_code, 200)
        self.assertNotIn("<form", html.split('id="main-content"')[1])


if __name__ == "__main__":
    unittest.main(verbosity=1)
