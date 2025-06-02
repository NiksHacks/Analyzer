from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError # Import standard validators.
from models.user import User # Import User model for email validation.

class RegistrationForm(FlaskForm):
    """
    Form for user registration.
    Includes fields for email, password (with confirmation), and full name.
    Custom validation is included to check if an email is already registered.
    """
    # Email field: requires data and must be a valid email format.
    email = StringField('Email', validators=[DataRequired(message="Email is required."), Email(message="Invalid email address.")])
    # Password field: requires data and must be at least 6 characters long.
    password = PasswordField('Password', validators=[DataRequired(message="Password is required."), Length(min=6, message="Password must be at least 6 characters long.")])
    # Confirm Password field: requires data and must match the 'password' field.
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(message="Please confirm your password."), EqualTo('password', message="Passwords must match.")])
    # Full Name field: requires data.
    full_name = StringField('Full Name', validators=[DataRequired(message="Full name is required.")])
    # Submit button for the form.
    submit = SubmitField('Register')

    def validate_email(self, email):
        """
        Custom validator for the email field.
        Checks if the provided email address already exists in the database.

        Args:
            email (wtforms.fields.StringField): The email field from the form.

        Raises:
            ValidationError: If the email is already taken.
        """
        # Query the User table to see if a user with this email already exists.
        user = User.query.filter_by(email=email.data.lower()).first() # Convert to lower for case-insensitive check
        if user:
            # If a user exists, raise a validation error to inform the user.
            raise ValidationError('That email address is already registered. Please choose a different one or log in.')

class LoginForm(FlaskForm):
    """
    Form for user login.
    Includes fields for email, password, and a "Remember Me" option.
    """
    # Email field: requires data and must be a valid email format.
    email = StringField('Email', validators=[DataRequired(message="Email is required."), Email(message="Invalid email address.")])
    # Password field: requires data.
    password = PasswordField('Password', validators=[DataRequired(message="Password is required.")])
    # Remember Me field: boolean field for persistent login session.
    remember_me = BooleanField('Remember Me')
    # Submit button for the form.
    submit = SubmitField('Login')
