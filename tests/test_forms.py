import pytest
from forms import RegistrationForm, LoginForm
from models.user import User # For testing unique email validation

# Note: The app_context fixture is automatically used by tests that need it due to autouse=False (default)
# and Flask-WTF forms generally operate within an app context when validating.
# The 'db' fixture is explicitly passed to tests that interact with the database.

def test_registration_form_valid_data(app_context):
    """Test RegistrationForm with all valid data."""
    form = RegistrationForm(
        full_name="Test User",
        email="newuser@example.com",
        password="password123",
        confirm_password="password123"
    )
    assert form.validate() == True
    assert not form.errors

def test_registration_form_missing_full_name(app_context):
    """Test RegistrationForm for missing full_name."""
    form = RegistrationForm(email="test@example.com", password="password123", confirm_password="password123")
    assert form.validate() == False
    assert "full_name" in form.errors
    # Default message for DataRequired might vary slightly based on WTForms version or custom messages
    assert "This field is required." in form.errors["full_name"] or \
           "Full name is required." in form.errors["full_name"]


def test_registration_form_invalid_email_format(app_context):
    """Test RegistrationForm for invalid email format."""
    form = RegistrationForm(full_name="Test User", email="invalid-email", password="password123", confirm_password="password123")
    assert form.validate() == False
    assert "email" in form.errors
    assert "Invalid email address." in form.errors["email"]

def test_registration_form_password_too_short(app_context):
    """Test RegistrationForm for password being too short."""
    form = RegistrationForm(full_name="Test User", email="test@example.com", password="123", confirm_password="123")
    assert form.validate() == False
    assert "password" in form.errors
    # The exact message can depend on how Length validator is configured or WTForms defaults
    assert "Password must be at least 6 characters long." in form.errors["password"] or \
           "Field must be between 6 and 128 characters long." in form.errors["password"] # Check common variations

def test_registration_form_passwords_do_not_match(app_context):
    """Test RegistrationForm for password confirmation mismatch."""
    form = RegistrationForm(
        full_name="Test User", email="test@example.com",
        password="password123", confirm_password="password456"
    )
    assert form.validate() == False
    assert "confirm_password" in form.errors
    assert "Passwords must match." in form.errors["confirm_password"] or \
           "Field must be equal to password." in form.errors["confirm_password"] # Check common variations


def test_registration_form_email_already_exists(app_context, db): # Uses db fixture
    """Test RegistrationForm for an email that already exists in the database."""
    # Add a user to the test database
    existing_user = User(email="exists@example.com", full_name="Existing User")
    existing_user.set_password("password") # Set password to make it a valid user object
    db.session.add(existing_user)
    db.session.commit()

    form = RegistrationForm(
        full_name="New User",
        email="exists@example.com", # This email already exists
        password="password123",
        confirm_password="password123"
    )
    assert form.validate() == False
    assert "email" in form.errors
    assert "That email address is already registered. Please choose a different one or log in." in form.errors["email"]

# --- LoginForm Tests ---

def test_login_form_valid_data(app_context):
    """Test LoginForm with valid data."""
    form = LoginForm(email="user@example.com", password="password123", remember_me=True)
    # LoginForm's validate method primarily checks for presence and email format by default.
    # Actual user authentication (checking if user exists and password is correct) happens in the route.
    assert form.validate() == True
    assert not form.errors

def test_login_form_missing_email(app_context):
    """Test LoginForm for missing email."""
    form = LoginForm(password="password123")
    assert form.validate() == False
    assert "email" in form.errors
    assert "This field is required." in form.errors["email"] or \
           "Email is required." in form.errors["email"]


def test_login_form_missing_password(app_context):
    """Test LoginForm for missing password."""
    form = LoginForm(email="user@example.com")
    assert form.validate() == False
    assert "password" in form.errors
    assert "This field is required." in form.errors["password"] or \
           "Password is required." in form.errors["password"]

def test_login_form_invalid_email_format(app_context):
    """Test LoginForm for invalid email format."""
    form = LoginForm(email="invalid", password="password123")
    assert form.validate() == False
    assert "email" in form.errors
    assert "Invalid email address." in form.errors["email"]
