import pytest
from utils.security import encrypt_token, decrypt_token
from cryptography.fernet import InvalidToken # Import for specific exception

def test_encrypt_decrypt_token(app_context): # app_context to ensure config (FERNET_KEY) is loaded
    original_token = "mysecret_oauth_token_string_for_testing_which_is_quite_long"
    encrypted = encrypt_token(original_token)
    assert encrypted is not None
    assert encrypted != original_token

    decrypted = decrypt_token(encrypted)
    assert decrypted == original_token

def test_encrypt_decrypt_none(app_context):
    assert encrypt_token(None) is None
    assert decrypt_token(None) is None

def test_decrypt_invalid_token_format(app_context):
    # Test decrypting a token that is not a valid Fernet token (e.g., not base64 encoded)
    with pytest.raises(InvalidToken):
        decrypt_token("this_is_not_a_valid_fernet_token_at_all")

def test_decrypt_tampered_token(app_context):
    # Test decrypting a token that might be base64 but not correctly encrypted or tampered
    # A correctly formatted Fernet token has a specific structure.
    # An arbitrary base64 string will likely fail to decrypt.
    # Example: a valid base64 string but not a Fernet token
    invalid_b64_token = "SGVsbG8gV29ybGQh" # "Hello World!" in base64
    with pytest.raises(InvalidToken):
        decrypt_token(invalid_b64_token)

def test_encrypt_empty_string(app_context):
    original_token = ""
    encrypted = encrypt_token(original_token)
    assert encrypted is not None
    assert encrypted != original_token
    decrypted = decrypt_token(encrypted)
    assert decrypted == original_token

def test_decrypt_different_key_scenario_mocked(mocker, app_context):
    # This test is more conceptual as changing the key mid-test is tricky.
    # We can simulate it by encrypting with one key and trying to decrypt with another (mocked).
    from cryptography.fernet import Fernet
    original_token = "token_for_key_test"

    # Encrypt with the app's configured key
    encrypted_with_app_key = encrypt_token(original_token)

    # Simulate trying to decrypt with a different key
    different_key = Fernet.generate_key()
    different_fernet_instance = Fernet(different_key)

    # Mock get_fernet to return this different instance temporarily
    mocker.patch('utils.security.get_fernet', return_value=different_fernet_instance)

    with pytest.raises(InvalidToken):
        decrypt_token(encrypted_with_app_key) # This should fail as it's encrypted with app_key

    # Restore the original mock or let it be if test scope handles it
    # No explicit unpatch needed if mocker scope is function
    # Test that decryption works again with the original key (app_context re-establishes it)
    # To be absolutely sure, we can re-encrypt and decrypt
    encrypted_again = encrypt_token(original_token) # Uses original key from app_context
    decrypted_again = decrypt_token(encrypted_again)
    assert decrypted_again == original_token
