from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required
# Removed current_user as it's not used in this specific endpoint's logic now
# from ..models import db, CampaignData, AdPlatformIntegration, PlatformNameEnum # Not needed for this version
# from datetime import datetime, timedelta, date # Not needed for this version
# from sqlalchemy import func # Not needed for this version
# import numpy as np # Not needed for this version

optimization_bp = Blueprint('optimization', __name__, url_prefix='/optimization')


@optimization_bp.route('/api/budget_simulator', methods=['POST'])
@login_required
def budget_simulator_api():
    data = request.get_json()

    if not data:
        return jsonify({"error": "Missing JSON payload."}), 400

    current_cpc = data.get('current_cpc')
    current_cvr_clicks = data.get('current_cvr_clicks') # Expecting as decimal, e.g., 0.05 for 5%
    budget_scenarios_input = data.get('budget_scenarios')

    # Validate inputs
    if current_cpc is None or current_cvr_clicks is None:
        return jsonify({"error": "current_cpc and current_cvr_clicks are required."}), 400

    if not isinstance(current_cpc, (int, float)) or not isinstance(current_cvr_clicks, (int, float)):
        return jsonify({"error": "CPC and CVR must be numbers."}), 400

    if not isinstance(budget_scenarios_input, list) or not budget_scenarios_input: # Ensure it's a list and not empty
        return jsonify({"error": "budget_scenarios must be a non-empty list of positive numbers."}), 400

    if not all(isinstance(b, (int, float)) and b > 0 for b in budget_scenarios_input):
        return jsonify({"error": "All budget scenarios must be positive numbers."}), 400

    if current_cpc <= 0:
        return jsonify({"error": "Current CPC must be greater than zero."}), 400

    if not (0 <= current_cvr_clicks <= 1): # Assuming CVR is passed as a decimal (e.g., 0.05 for 5%)
        return jsonify({"error": "Current CVR (Click-based) must be between 0 (0%) and 1 (100%)."}), 400


    results = {"scenarios": [], "assumptions": "Projections assume Cost Per Click (CPC) and Click-based Conversion Rate (CVR) remain constant at the provided values. Actual results may vary."}

    for budget in budget_scenarios_input:
        projected_clicks = 0
        projected_conversions = 0
        projected_cpa = 0

        # current_cpc > 0 is already validated
        projected_clicks = budget / current_cpc

        projected_conversions = projected_clicks * current_cvr_clicks
        projected_spend = budget # We are simulating spending this full budget

        if projected_conversions > 0:
            projected_cpa = budget / projected_conversions

        results["scenarios"].append({
            "budget": round(budget, 2),
            "projected_clicks": round(projected_clicks, 0), # Clicks are whole numbers
            "projected_spend": round(projected_spend, 2),
            "projected_conversions": round(projected_conversions, 2), # Conversions can be fractional in projections
            "projected_cpa": round(projected_cpa, 2)
        })

    return jsonify(results)
