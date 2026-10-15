Python Personal Task Manager

A Python-based web application developed as part of a five-week university mini-project for the PFD200 Professional Development module.

Project Overview

The purpose of this project is to develop a personal task management application that helps users organise their daily activities, manage deadlines and monitor their progress.

The application allows users to create an account, log in securely and manage their own tasks through a web-based dashboard.

Features

* User registration, login and logout
* Password hashing
* Personal task management for each registered user
* Create, view, edit and delete tasks
* Mark tasks as completed
* Assign Low, Medium or High priority
* Set task deadlines
* Filter tasks by status and priority
* Dashboard showing total, completed and pending tasks
* Task completion percentage

Technologies Used

* Python: Server-side programming language
* Flask: Web application framework
* SQLite: Database for storing users and tasks
* HTML: Structure of the web pages
* CSS: Website styling and layout
* Flask-WTF: CSRF protection
* Werkzeug: Secure password hashing and verification
* Git: Version control
* GitHub: Source code repository

Security Features

The application includes several security measures:

* Passwords are stored as hashes rather than plain text.
* Flask sessions are used to manage authenticated users.
* Users must log in before accessing their dashboard.
* Database queries restrict task access to the authenticated user.
* Parameterised SQL queries help protect against SQL injection.
* CSRF tokens protect forms that modify application data.
* The Flask secret key is stored outside the source code.
* The local database and environment configuration are excluded from GitHub.

Project Status

The core application features have been implemented. Final testing, documentation and evaluation are being completed as part of the five-week development plan.

Installation and Setup

Follow these steps to run the application locally.

1. Clone the repository

git clone https://github.com/ben-ekay/python-personal-task-manager.git
cd python-personal-task-manager

The repository is private, so GitHub access is required.

2. Create a virtual environment

python3 -m venv .venv

Activate the virtual environment on macOS or Linux:

source .venv/bin/activate

3. Install the dependencies

python -m pip install -r requirements.txt

4. Configure the secret key

Create a .env file in the project directory and add:

FLASK_SECRET_KEY=your_generated_secret_key

Generate a secure secret key using Python’s secrets module. Do not publish the actual key.

5. Run the application

python -m flask --app app run --debug

Open the following address in a web browser:

http://127.0.0.1:5000

The SQLite database is created automatically when the application starts.

Note: Debug mode is intended for local development only and must not be used in production.