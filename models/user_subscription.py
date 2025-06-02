import enum
from datetime import datetime
from extensions import db # Import the SQLAlchemy instance.

class SubscriptionStatusEnum(enum.Enum):
    """
    Enumeration for the possible statuses of a user's subscription.
    This helps maintain consistency and avoids using raw strings for status values.
    """
    ACTIVE = 'active'        # Subscription is currently active and paid.
    CANCELLED = 'cancelled'  # Subscription has been cancelled by the user or system.
    PAST_DUE = 'past_due'    # Payment is overdue, subscription might still be accessible for a grace period.
    TRIALING = 'trialing'    # User is currently in a free trial period.
    INCOMPLETE = 'incomplete' # For Stripe, if initial payment fails or requires further action.
    INCOMPLETE_EXPIRED = 'incomplete_expired' # Incomplete payment expired.
    UNPAID = 'unpaid'        # Similar to past_due, often used by Stripe when payment collection fails.

    @staticmethod
    def from_stripe_status(stripe_status_str):
        """
        Maps a Stripe subscription status string to a SubscriptionStatusEnum member.
        Args:
            stripe_status_str (str): The status string from Stripe (e.g., "active", "trialing", "canceled").
        Returns:
            SubscriptionStatusEnum or None: The corresponding enum member, or None if no direct match.
        """
        # Stripe status reference: https://stripe.com/docs/api/subscriptions/object#subscription_object-status
        mapping = {
            'active': SubscriptionStatusEnum.ACTIVE,
            'trialing': SubscriptionStatusEnum.TRIALING,
            'past_due': SubscriptionStatusEnum.PAST_DUE,
            'canceled': SubscriptionStatusEnum.CANCELLED, # Stripe uses "canceled"
            'unpaid': SubscriptionStatusEnum.UNPAID,
            'incomplete': SubscriptionStatusEnum.INCOMPLETE,
            'incomplete_expired': SubscriptionStatusEnum.INCOMPLETE_EXPIRED,
            # Stripe 'ended' status is also typically mapped to CANCELLED if not more specific.
        }
        return mapping.get(stripe_status_str.lower(), None)


class UserSubscription(db.Model):
    """
    Represents a subscription instance for a user to a specific SubscriptionPlan.

    This model links a user to a plan and tracks the subscription's lifecycle,
    including its start date, end date (or trial end date), current status,
    and the corresponding Stripe Subscription ID.
    """
    __tablename__ = 'user_subscriptions' # Specifies the database table name.

    id = db.Column(db.Integer, primary_key=True) # Unique identifier for the user subscription record.

    # --- Foreign Keys and Relationships ---
    # Links this subscription to a specific user. users.id is the primary key of the 'users' table.
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    # Links this subscription to a specific subscription plan. subscription_plans.id is the primary key of the 'subscription_plans' table.
    plan_id = db.Column(db.Integer, db.ForeignKey('subscription_plans.id'), nullable=False, index=True)

    # --- Subscription Lifecycle Dates ---
    start_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow) # Date and time when the subscription started (or trial started).
    # Date and time when the subscription is scheduled to end (or ended).
    # For active subscriptions, this usually represents the end of the current billing period or trial period.
    # Nullable because some subscriptions might be indefinite until explicitly cancelled, though usually set.
    end_date = db.Column(db.DateTime, nullable=True)

    # --- Status and Stripe ID ---
    # Current status of the subscription, using the SubscriptionStatusEnum.
    # Defaults to TRIALING, assuming new subscriptions might start with a trial.
    status = db.Column(db.Enum(SubscriptionStatusEnum), nullable=False, default=SubscriptionStatusEnum.TRIALING, index=True)
    # Stores the Stripe Subscription ID. This is crucial for linking local records with Stripe objects
    # and for managing the subscription via Stripe API (e.g., cancellations, updates from webhooks).
    # Should be unique as one Stripe subscription maps to one UserSubscription record here.
    stripe_subscription_id = db.Column(db.String(100), unique=True, nullable=True, index=True)

    # --- Timestamps ---
    created_at = db.Column(db.DateTime, default=datetime.utcnow) # Timestamp of when this subscription record was created.
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) # Timestamp of the last update to this record.

    # --- Relationship to SubscriptionPlan ---
    # Defines a many-to-one relationship with SubscriptionPlan.
    # 'plan' allows easy access to the details of the subscribed plan (e.g., user_subscription.plan.name).
    # This is automatically back-referenced by SubscriptionPlan if a 'user_subscriptions' relationship is defined there.
    plan = db.relationship('SubscriptionPlan')

    def __repr__(self):
        """
        Provides a string representation of the UserSubscription object, useful for debugging.
        """
        return f'<UserSubscription {self.user_id} - Plan {self.plan_id} - Status {self.status.value}>'
