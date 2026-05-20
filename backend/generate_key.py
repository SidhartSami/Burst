import base64
import hashlib
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

def generate_chrome_key():
    # 1. Generate 2048-bit RSA private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048
    )
    
    # 2. Get Public Key in DER format (SubjectPublicKeyInfo)
    public_key = private_key.public_key()
    der_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    
    # 3. Base64 encode the public key for the manifest "key" field
    public_key_b64 = base64.b64encode(der_bytes).decode('utf-8')
    
    # 4. Calculate Chrome Extension ID from DER public key
    # Chrome extension ID is the first 32 hex characters of the SHA-256 hash,
    # with hex values 0-15 mapped to a-p.
    sha256_hash = hashlib.sha256(der_bytes).hexdigest()
    hex_id = sha256_hash[:32]
    
    extension_id = ""
    for char in hex_id:
        val = int(char, 16)
        extension_id += chr(val + ord('a'))
        
    # 5. Serialize and save the private key in PEM format just in case
    pem_private = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    import os
    private_key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension-chrome", "key.pem")
    with open(private_key_path, "wb") as f:
        f.write(pem_private)
        
    print(f"Private key saved to: {private_key_path}")
    print("\n--- ADD TO manifest.json ---")
    print(f'"key": "{public_key_b64}"')
    print("\n--- GENERATED EXTENSION ID ---")
    print(f"Extension ID: {extension_id}")
    print("\n--- UPDATE installer.iss & com.burst.download.manager.json WITH ---")
    print(f"chrome-extension://{extension_id}/")

if __name__ == "__main__":
    generate_chrome_key()
