import datetime
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import os

def generate_self_signed_cert():
    # Create directory
    os.makedirs("nginx/ssl", exist_ok=True)

    # Generate private key
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096,
    )

    # Generate a self-signed certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "api.aerora.io"),
    ])

    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow() - datetime.timedelta(days=1)
    ).not_valid_after(
        # Our certificate will be valid for 1 year
        datetime.datetime.utcnow() + datetime.timedelta(days=365)
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName("api.aerora.io")]),
        critical=False,
    # Sign our certificate with our private key
    ).sign(key, hashes.SHA256())

    # Write key
    with open("nginx/ssl/aerora.key", "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    # Write cert
    with open("nginx/ssl/aerora.crt", "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print("Certificates generated successfully in nginx/ssl/")

if __name__ == "__main__":
    generate_self_signed_cert()
