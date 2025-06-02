import json
import pytest
from flask import url_for

# Test valid input and correct calculations
def test_budget_simulator_valid_input(client, app_context): # app_context for url_for
    payload = {
        "current_cpc": 2.0,
        "current_cvr_clicks": 0.05, # 5%
        "budget_scenarios": [100, 200, 500]
    }
    with app_context: # Ensure url_for has app context
        response = client.post(url_for('optimization.budget_simulator_api'),
                                data=json.dumps(payload),
                                content_type='application/json')

    assert response.status_code == 200
    data = response.get_json()
    assert "scenarios" in data
    assert "assumptions" in data
    assert len(data["scenarios"]) == 3

    # Check calculations for the first scenario (budget: 100)
    scenario1 = data["scenarios"][0]
    assert scenario1["budget"] == 100
    assert scenario1["projected_spend"] == 100
    assert scenario1["projected_clicks"] == pytest.approx(100 / 2.0)  # 50
    assert scenario1["projected_conversions"] == pytest.approx(50 * 0.05) # 2.5
    assert scenario1["projected_cpa"] == pytest.approx(100 / 2.5 if 2.5 > 0 else 0) # 40

    # Check calculations for the third scenario (budget: 500)
    scenario3 = data["scenarios"][2]
    assert scenario3["budget"] == 500
    assert scenario3["projected_spend"] == 500
    assert scenario3["projected_clicks"] == pytest.approx(500 / 2.0) # 250
    assert scenario3["projected_conversions"] == pytest.approx(250 * 0.05) # 12.5
    assert scenario3["projected_cpa"] == pytest.approx(500 / 12.5 if 12.5 > 0 else 0) # 40

# Test invalid inputs (expecting 400 Bad Request)
@pytest.mark.parametrize("invalid_payload, expected_error_message_part", [
    ({}, "Missing JSON payload."),
    ({"current_cpc": "abc", "current_cvr_clicks": 0.05, "budget_scenarios": [100]}, "CPC and CVR must be numbers."),
    ({"current_cpc": 2.0, "current_cvr_clicks": "xyz", "budget_scenarios": [100]}, "CPC and CVR must be numbers."),
    ({"current_cpc": 0, "current_cvr_clicks": 0.05, "budget_scenarios": [100]}, "Current CPC must be greater than zero."),
    ({"current_cpc": -1, "current_cvr_clicks": 0.05, "budget_scenarios": [100]}, "Current CPC must be greater than zero."),
    ({"current_cpc": 2.0, "current_cvr_clicks": 1.01, "budget_scenarios": [100]}, "Current CVR (Click-based) must be between 0 (0%) and 1 (100%)."),
    ({"current_cpc": 2.0, "current_cvr_clicks": -0.01, "budget_scenarios": [100]}, "Current CVR (Click-based) must be between 0 (0%) and 1 (100%)."),
    ({"current_cpc": 2.0, "current_cvr_clicks": 0.05, "budget_scenarios": "not-a-list"}, "budget_scenarios must be a non-empty list of positive numbers."),
    ({"current_cpc": 2.0, "current_cvr_clicks": 0.05, "budget_scenarios": []}, "budget_scenarios must be a non-empty list of positive numbers."),
    ({"current_cpc": 2.0, "current_cvr_clicks": 0.05, "budget_scenarios": [100, "abc", 200]}, "All budget scenarios must be positive numbers."),
    ({"current_cpc": 2.0, "current_cvr_clicks": 0.05, "budget_scenarios": [100, 0, 200]}, "All budget scenarios must be positive numbers."),
    ({"current_cpc": 2.0, "current_cvr_clicks": 0.05, "budget_scenarios": [100, -50, 200]}, "All budget scenarios must be positive numbers."),
    ({"current_cvr_clicks": 0.05, "budget_scenarios": [100]}, "current_cpc and current_cvr_clicks are required."),
    ({"current_cpc": 2.0, "budget_scenarios": [100]}, "current_cpc and current_cvr_clicks are required."),
    ({"current_cpc": 2.0, "current_cvr_clicks": 0.05}, "budget_scenarios must be a non-empty list of positive numbers.")
])
def test_budget_simulator_invalid_inputs(client, app_context, invalid_payload, expected_error_message_part):
    with app_context:
        if not invalid_payload: # For the case where data is None
            response = client.post(url_for('optimization.budget_simulator_api'), content_type='application/json')
        else:
            response = client.post(url_for('optimization.budget_simulator_api'),
                                   data=json.dumps(invalid_payload),
                                   content_type='application/json')
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data
    assert expected_error_message_part in data["error"]

# Test edge cases for calculations
def test_budget_simulator_edge_cases(client, app_context):
    with app_context:
        # Zero CVR
        payload_zero_cvr = {
            "current_cpc": 1.0,
            "current_cvr_clicks": 0.0,
            "budget_scenarios": [100]
        }
        response_zero_cvr = client.post(url_for('optimization.budget_simulator_api'), data=json.dumps(payload_zero_cvr), content_type='application/json')
        assert response_zero_cvr.status_code == 200
        data_zero_cvr = response_zero_cvr.get_json()["scenarios"][0]
        assert data_zero_cvr["projected_conversions"] == 0
        assert data_zero_cvr["projected_cpa"] == 0

        # High CVR (100%)
        payload_high_cvr = {
            "current_cpc": 1.0,
            "current_cvr_clicks": 1.0, # 100% CVR
            "budget_scenarios": [100]
        }
        response_high_cvr = client.post(url_for('optimization.budget_simulator_api'), data=json.dumps(payload_high_cvr), content_type='application/json')
        assert response_high_cvr.status_code == 200
        data_high_cvr = response_high_cvr.get_json()["scenarios"][0]
        assert data_high_cvr["projected_clicks"] == 100
        assert data_high_cvr["projected_conversions"] == 100
        assert data_high_cvr["projected_cpa"] == 1.0

    # Test case where budget_scenarios is provided but is an empty list
    payload_empty_scenarios = {
        "current_cpc": 1.0,
        "current_cvr_clicks": 0.05,
        "budget_scenarios": []
    }
    with app_context:
        response_empty_scenarios = client.post(url_for('optimization.budget_simulator_api'),
                                               data=json.dumps(payload_empty_scenarios),
                                               content_type='application/json')
    assert response_empty_scenarios.status_code == 400
    data_empty_scenarios = response_empty_scenarios.get_json()
    assert "error" in data_empty_scenarios
    assert "budget_scenarios must be a non-empty list" in data_empty_scenarios["error"]

    # Test case where budget_scenarios has non-numeric values
    payload_non_numeric_scenarios = {
        "current_cpc": 1.0,
        "current_cvr_clicks": 0.05,
        "budget_scenarios": [100, "test", 200]
    }
    with app_context:
        response_non_numeric_scenarios = client.post(url_for('optimization.budget_simulator_api'),
                                                    data=json.dumps(payload_non_numeric_scenarios),
                                                    content_type='application/json')
    assert response_non_numeric_scenarios.status_code == 400
    data_non_numeric_scenarios = response_non_numeric_scenarios.get_json()
    assert "error" in data_non_numeric_scenarios
    assert "All budget scenarios must be positive numbers." in data_non_numeric_scenarios["error"]


    # Test for missing keys - refined expected messages
    payload_missing_cpc = {"current_cvr_clicks": 0.05, "budget_scenarios": [100]}
    with app_context:
        response = client.post(url_for('optimization.budget_simulator_api'), data=json.dumps(payload_missing_cpc), content_type='application/json')
    assert response.status_code == 400
    assert "current_cpc and current_cvr_clicks are required." in response.get_json()["error"]

    payload_missing_cvr = {"current_cpc": 2.0, "budget_scenarios": [100]}
    with app_context:
        response = client.post(url_for('optimization.budget_simulator_api'), data=json.dumps(payload_missing_cvr), content_type='application/json')
    assert response.status_code == 400
    assert "current_cpc and current_cvr_clicks are required." in response.get_json()["error"]

    payload_missing_scenarios = {"current_cpc": 2.0, "current_cvr_clicks": 0.05}
    with app_context:
        response = client.post(url_for('optimization.budget_simulator_api'), data=json.dumps(payload_missing_scenarios), content_type='application/json')
    assert response.status_code == 400
    assert "budget_scenarios must be a non-empty list" in response.get_json()["error"]
