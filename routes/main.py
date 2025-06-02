from flask import Blueprint, render_template, request, url_for # Added url_for
from flask_login import login_required # Not strictly needed for this example route, but good practice if it might evolve

main_bp = Blueprint('main', __name__)

@main_bp.route('/show_confirmation')
# @login_required # Optional: if confirmation pages should be protected
def show_confirmation_page():
    # These would typically be passed after an action, or via query parameters for testing
    title = request.args.get('title', 'Action Confirmed')
    message = request.args.get('message', 'Your action has been processed successfully.')
    button_text = request.args.get('button_text', 'Go to Dashboard')
    # Example of a default button_url. This might need to be more dynamic or passed in.
    button_url = request.args.get('button_url', url_for('dashboard.main_dashboard'))

    return render_template('status/confirmation.html',
                           title=title,
                           message=message,
                           button_text=button_text,
                           button_url=button_url)
