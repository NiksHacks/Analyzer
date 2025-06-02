from cryptography.fernet import Fernet # Symmetric encryption library.
from flask import current_app # To access application configuration (e.g., FERNET_KEY).

def get_fernet():
    """
    Initializes and returns a Fernet instance for encryption/decryption.

    It retrieves the Fernet key from the application's configuration.
    The FERNET_KEY must be a URL-safe base64-encoded 32-byte key.
    This key should be generated once and kept secret.

    Raises:
        ValueError: If FERNET_KEY is not configured in the application.

    Returns:
        cryptography.fernet.Fernet: An initialized Fernet cipher suite instance.
    """
    # Retrieve the Fernet key from the Flask app's configuration.
    key = current_app.config.get('FERNET_KEY')
    if not key:
        # Critical configuration error if the key is missing.
        current_app.logger.critical("FERNET_KEY is not configured in the application. Encryption/decryption will fail.")
        raise ValueError("FERNET_KEY not configured properly. Please set it in your application configuration.")

    # The key is expected to be bytes. The config.py should ensure it's stored as bytes
    # (e.g., by calling .encode() on a string key or loading from an environment variable as bytes).
    # If there's a possibility that `key` is a string here, it would need `key.encode('utf-8')`.
    # However, assuming config.py handles this, direct use is fine.
    return Fernet(key)

def encrypt_token(token):
    """
    Encrypts a given plain-text token using Fernet symmetric encryption.

    Args:
        token (str or None): The plain-text token to encrypt. If None, returns None.

    Returns:
        str or None: The encrypted token, encoded as a UTF-8 string.
                     Returns None if the input token was None.
    """
    if token is None:
        return None # Return None if there's no token to encrypt.

    fernet = get_fernet() # Get a Fernet instance.
    # Encrypt the token:
    # 1. Encode the plain-text token string to bytes (Fernet operates on bytes).
    # 2. Encrypt the bytes using the Fernet instance.
    # 3. Decode the resulting encrypted bytes back to a UTF-8 string for storage (e.g., in a database Text field).
    return fernet.encrypt(token.encode('utf-8')).decode('utf-8')

def decrypt_token(encrypted_token):
    """
    Decrypts a given Fernet-encrypted token back to its plain-text form.

    Args:
        encrypted_token (str or None): The encrypted token (UTF-8 string) to decrypt.
                                       If None, returns None.

    Returns:
        str or None: The decrypted, plain-text token.
                     Returns None if the input encrypted_token was None.
    Raises:
        cryptography.fernet.InvalidToken: If the token is invalid, cannot be decrypted
                                          (e.g., wrong key, corrupted data, not a valid Fernet token).
                                          This should be handled by the caller.
    """
    if encrypted_token is None:
        return None # Return None if there's no token to decrypt.

    fernet = get_fernet() # Get a Fernet instance.
    # Decrypt the token:
    # 1. Encode the encrypted token string (which was stored as UTF-8) back to bytes.
    # 2. Decrypt the bytes using the Fernet instance.
    # 3. Decode the resulting decrypted bytes back to a UTF-8 string to get the original plain-text token.
    return fernet.decrypt(encrypted_token.encode('utf-8')).decode('utf-8')
