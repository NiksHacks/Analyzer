import pytest
from app import create_app
from config import Config
from extensions import db as _db # Alias to avoid fixture name conflict

class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:' # Use in-memory SQLite for tests
    WTF_CSRF_ENABLED = False # Disable CSRF for form testing convenience
    SECRET_KEY = 'test-secret-key-for-forms' # WTForms/Flask-Login require a SECRET_KEY for session context
    # Generate a valid Fernet key for testing if any encryption/decryption is indirectly triggered
    # This key needs to be a URL-safe base64-encoded 32-byte key.
    # Example: Fernet.generate_key() -> b'...'
    # For testing, we can use a fixed one.
    FERNET_KEY = b'c2VzY3JldF9rZXlfZm9yX3Rlc3RpbmdfZmFzcw==' # Placeholder, ensure it's 32 url-safe bytes

@pytest.fixture(scope='session')
def app():
    """
    Session-wide test Flask application.
    Ensures the app is created once per test session with TestConfig.
    """
    app_instance = create_app(config_class=TestConfig)
    return app_instance

@pytest.fixture(scope='function')
def app_context(app):
    """
    Function-scoped application context.
    Pushes an app context before each test that needs it and pops it afterwards.
    This is crucial for tests that interact with Flask's application context globals
    like `current_app` or extensions initialized with `init_app`.
    """
    with app.app_context():
        yield

@pytest.fixture(scope='function')
def db(app_context): # db fixture now correctly depends on app_context
    """
    Function-scoped database fixture.
    Creates all database tables before each test and drops them afterwards.
    This ensures a clean database state for each test.
    It yields the database instance for use in tests.
    """
    _db.create_all() # Create tables based on models
    yield _db          # Provide the database session/object to the test
    _db.session.remove() # Ensure session is closed
    _db.drop_all()     # Drop all tables to clean up

@pytest.fixture(scope='session') # Client can be session-scoped if app is.
def client(app):
    """
    Test client fixture for making requests to the application.
    Can be session-scoped if the app itself is session-scoped and state between
    requests made by the client is not an issue across tests.
    """
    return app.test_client()
