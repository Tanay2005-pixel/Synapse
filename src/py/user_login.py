
import os
import json
import time
import hmac
import base64
import secrets
import hashlib
import smtplib

from pathlib import Path
from email.message import EmailMessage
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field

from src.py.registration import (
    send_registration_otp,
    verify_registration_otp,
    RegistrationRequest,
    VerifyOTPRequest as RegistrationVerifyOTPRequest,
)

app = FastAPI(title="Synapse Login")

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_FILE = BASE_DIR / "user.json"
templates = Jinja2Templates(
    directory=str(BASE_DIR / "template")
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict before deployment
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

OTP_EXPIRY = 300
pending_otps: dict[str, dict[str, Any]] = {}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class VerifyOTPRequest(BaseModel):

    email: EmailStr
    otp: str = Field(pattern=r"^\d{6}$")

class LoginVerifyOTPRequest(BaseModel):
    email: EmailStr
    otp: str = Field(pattern=r"^[0-9]{6}$")

def load_users():
    if not DATA_FILE.exists():
        return {}

    with open(DATA_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def find_user_by_email(email):
    email = email.strip().casefold()

    for user in load_users().values():
        if (
            isinstance(user, dict)
            and str(user.get("email", "")).strip().casefold() == email
        ):
            return user

    return None


def verify_password(password, stored):
    try:
        if stored.get("algorithm") != "pbkdf2_sha256":
            return False

        salt = base64.b64decode(stored["salt"], validate=True)
        expected = base64.b64decode(stored["hash"], validate=True)
        iterations = int(stored["iterations"])

        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )

        return hmac.compare_digest(actual, expected)

    except (KeyError, TypeError, ValueError):
        return False


def send_email_otp(email, otp):
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    if not smtp_user or not smtp_password:
        raise HTTPException(
            status_code=500,
            detail="Email SMTP is not configured.",
        )

    message = EmailMessage()
    message["Subject"] = "Your Synapse Login OTP"
    message["From"] = smtp_user
    message["To"] = email
    message.set_content(
        f"Your Synapse login OTP is {otp}.\n"
        "It expires in 5 minutes. Do not share this code."
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not send OTP. Check your email configuration.",
        ) from exc


@app.get("/")
def home():
    return {"message": "Synapse login API is running"}


@app.get("/login-page")
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="synapse_login_post.html",
    )


@app.post("/send-otp")
def send_otp(credentials: LoginRequest):
    email = str(credentials.email).strip().casefold()

    # Step 1: Find existing user by registered email
    user = find_user_by_email(email)

    # Step 2: Verify password before sending OTP
    if not user or not verify_password(
        credentials.password,
        user.get("password", {}),
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
        )

    # Step 3: Apply a resend cooldown
    now = time.time()
    previous = pending_otps.get(email)

    if previous and now - previous["last_sent"] < 30:
        raise HTTPException(
            status_code=429,
            detail="Please wait 30 seconds before requesting another OTP.",
        )

    # Step 4: Generate and email a six-digit OTP
    otp = f"{secrets.randbelow(1_000_000):06d}"

    # Send first; only save a pending OTP if sending succeeds
    send_email_otp(email, otp)

    pending_otps[email] = {
        "otp": otp,
        "expires_at": now + OTP_EXPIRY,
        "last_sent": now,
        "attempts": 0,
        "username": user.get("username", ""),
    }

    return {"message": "Credentials verified. OTP sent to your email."}


@app.post("/verify-otp")
def verify_otp(request: VerifyOTPRequest):
    email = str(request.email).strip().casefold()
    record = pending_otps.get(email)

    if not record or time.time() > record["expires_at"]:
        pending_otps.pop(email, None)
        raise HTTPException(
            status_code=401,
            detail="OTP expired or invalid. Request a new OTP.",
        )

    record["attempts"] += 1

    if record["attempts"] > 5:
        pending_otps.pop(email, None)
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Request a new OTP.",
        )

    if not secrets.compare_digest(request.otp, record["otp"]):
        raise HTTPException(
            status_code=401,
            detail="Incorrect OTP.",
        )

    username = record["username"]
    pending_otps.pop(email, None)

    return {
        "message": "Login successful!",
        "username": username,
    }
@app.post("/registration/send-otp")
def register_send_otp(data: RegistrationRequest):
    return send_registration_otp(data)


@app.post("/registration/verify-otp")
def register_verify_otp(data: RegistrationVerifyOTPRequest):
    return verify_registration_otp(data)

@app.get("/register-page")
def register_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="register.html"
    )