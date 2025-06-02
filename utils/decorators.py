from functools import wraps
from flask import redirect, url_for, flash
from flask_login import current_user
from ..models import UserSubscription, SubscriptionStatusEnum, SubscriptionPlan # Adjusted import

def subscription_required(required_level_names=None):
    """
    Decorator to ensure a user has an active subscription.
    Can optionally check for specific plan levels.
    `required_level_names` should be a list of plan names (e.g., ['Pro', 'Enterprise']).
    If None or empty, any active subscription is sufficient.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                # This should ideally be handled by @login_required before this decorator runs.
                # If current_user is not authenticated, current_user.id would cause an error.
                flash('Please log in to access this page.', 'warning')
                return redirect(url_for('auth.login'))

            active_subscription = UserSubscription.query.filter_by(
                user_id=current_user.id,
                status=SubscriptionStatusEnum.ACTIVE
            ).join(SubscriptionPlan).first() # Join with SubscriptionPlan to check name

            if not active_subscription:
                flash('You need an active subscription to access this page.', 'warning')
                return redirect(url_for('auth.subscription_plans_overview')) # Route for plans overview

            if required_level_names and active_subscription.plan.name not in required_level_names:
                flash(f'This page requires a {" or ".join(required_level_names)} subscription. Your current plan is {active_subscription.plan.name}.', 'warning')
                return redirect(url_for('auth.subscription_plans_overview'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator
