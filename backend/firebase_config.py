import os
import json
import firebase_admin
from firebase_admin import credentials

def init_firebase():
    if not firebase_admin._apps:
        cred_json = os.getenv("FIREBASE_CREDENTIALS")

        if not cred_json:
            raise ValueError("Missing FIREBASE_CREDENTIALS")

        cred_dict = json.loads(cred_json)
        cred = credentials.Certificate(cred_dict)

        firebase_admin.initialize_app(cred)