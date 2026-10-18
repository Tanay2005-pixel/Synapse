
import os
import json
import time
import secrets
import smtplib

from pathlib import Path
from email.message import EmailMessage
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
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
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)
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


class LoginOTPRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    email: EmailStr


class VerifyOTPRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    email: EmailStr
    otp: str = Field(pattern=r"^\d{6}$")


def load_users():
    if not DATA_FILE.exists():
        return {}

    with open(DATA_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def find_user_by_username_and_email(username, email):
    username = username.strip().casefold()
    email = email.strip().casefold()

    for key, user in load_users().items():
        if not isinstance(user, dict):
            continue

        registered_username = str(user.get("username", key)).strip()
        registered_email = str(user.get("email", "")).strip()
        if (registered_username.casefold() == username
                and registered_email.casefold() == email):
            return user

    return None


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


@app.get("/workspace-page")
def workspace_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="workspace.html",
    )


@app.post("/send-otp")
def send_otp(credentials: LoginOTPRequest):
    username = credentials.username.strip()
    email = str(credentials.email).strip().casefold()

    # Step 1: Match both submitted fields against the registered account.
    user = find_user_by_username_and_email(username, email)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid username or email.",
        )

    # Step 2: Apply a resend cooldown.
    now = time.time()
    previous = pending_otps.get(email)

    if previous and now - previous["last_sent"] < 30:
        raise HTTPException(
            status_code=429,
            detail="Please wait 30 seconds before requesting another OTP.",
        )

    # Step 3: Generate and email a six-digit OTP.
    otp = f"{secrets.randbelow(1_000_000):06d}"

    # Send first; only save a pending OTP if sending succeeds
    send_email_otp(email, otp)

    pending_otps[email] = {
        "otp": otp,
        "expires_at": now + OTP_EXPIRY,
        "last_sent": now,
        "attempts": 0,
        "username": str(user.get("username") or username).strip(),
    }

    return {"message": "OTP sent to your email."}


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

    if record["username"].strip().casefold() != request.username.strip().casefold():
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