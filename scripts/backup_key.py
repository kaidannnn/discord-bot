"""Run privately; store output in a password manager and server .env only."""
from cryptography.fernet import Fernet
if __name__=='__main__': print(Fernet.generate_key().decode())
