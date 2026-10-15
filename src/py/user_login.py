
import json
import re
import hmac
import base64
import hashlib

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastapi import Request

from pathlib import Path
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(
    directory=str(BASE_DIR / "template")
)

app = FastAPI()
@app.get("/login-page")
def login_page(request: Request):
    return templates.TemplateResponse(
        "synapse_login_post.html",
        {"request": request}
    )
# Read existing user data only
DATA_FILE = Path(__file__).with_name("users.json")


def load_users():
    with open(DATA_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


# Function 1: Validate username
def validate_username(username: str) -> bool:
    return bool(
        re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username)
    )


# Function 2: Hash password using PBKDF2
# Uses the same salt and iteration count stored in JSON.
def hash_password(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations
    )


# Function 3: Verify password against its stored hash
def verify_password(password: str, stored_data: dict) -> bool:
    try:
        salt = base64.b64decode(stored_data["salt"])
        stored_hash = base64.b64decode(stored_data["hash"])
        iterations = int(stored_data["iterations"])

        calculated_hash = hash_password(
            password, salt, iterations
        )

        return hmac.compare_digest(
            calculated_hash, stored_hash
        )

    except (KeyError, ValueError, TypeError):
        return False


# Request format
class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=128)


# Login API: checks credentials only
@app.post("/login")
def login(credentials: LoginRequest):
    username = credentials.username.strip()

    if not validate_username(username):
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password"
        )

    users = load_users()

    # Find the existing username
    user_data = next(
        (
            data for key, data in users.items()
            if key.casefold() == username.casefold()
        ),
        None
    )

    if user_data is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password"
        )

    if not verify_password(
        credentials.password,
        user_data["password"]
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password"
        )

    return {
        "message": "Login successful",
        "username": user_data.get("username", username)
    }


@app.get("/")
def home():
    return {"message": "Login API is running"}
