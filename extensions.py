from flask_sqlalchemy import SQLAlchemy # ORM for database interactions.
from flask_login import LoginManager    # Manages user sessions for login and logout functionality.
from authlib.integrations.flask_client import OAuth # OAuth client library for integrating with third-party OAuth providers.

# Initialize SQLAlchemy.
# This instance will be further configured and associated with the Flask app
# in the application factory (create_app function in app.py) using db.init_app(app).
# It provides access to SQLAlchemy's ORM capabilities, session management, etc.
db = SQLAlchemy()

# Initialize Flask-Login's LoginManager.
# This instance handles the process of logging users in, logging them out,
# and remembering their sessions. It's configured in create_app.
# For example, login_manager.login_view is set there to specify the login route.
login_manager = LoginManager()

# Initialize Authlib's OAuth client.
# This instance is used to register and manage OAuth 1 and OAuth 2 clients
# for authentication with external services like Google, Facebook/Meta, etc.
# Specific provider configurations (e.g., Google client ID/secret, scopes)
# are registered with this `oauth` object in create_app.
oauth = OAuth()
