import re
import os
import json
import time
import secrets
import hashlib
import hmac
import smtplib
from pathlib import Path
from email.message import EmailMessage
from threading import Lock
from fastapi import FastAPI, HTTPException, Request
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field


app = FastAPI(title="Synapse Registration")

# Path of the directory - register.html
BASE_DIR = Path(__file__).resolve().parents[2]

DATA_FILE = BASE_DIR / "user.json"

templates = Jinja2Templates(
    directory=str(BASE_DIR / "template")
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

#OTP details send to the user

OTP_EXPIRY = 300          # 5 min
OTP_RESEND_DELAY = 30     # 30 sec
MAX_OTP_ATTEMPTS = 5

pending_registrations = {}

registration_lock = Lock()
file_lock = Lock()

#requesting template to send username and the email enter by the user
class RegistrationRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    email: EmailStr


class VerifyOTPRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    email: EmailStr
    otp: str = Field(pattern=r"^[0-9]{6}$")


#checking username is correct or not
def validate_username(username: str) -> bool:
    return bool(
        re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username)
    )


#loading and uploading data to the database
def load_users():
    if not DATA_FILE.exists():
        return {}

    with open(DATA_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def save_users(users):
    # Write to a temporary file, then replace the original.
    temp_file = DATA_FILE.with_suffix(".tmp")

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(users, file, indent=4)

    os.replace(temp_file, DATA_FILE)


def find_existing_user(users, username, email):
    username_key = username.casefold()
    email_key = email.casefold()

    for key, user in users.items():
        if not isinstance(user, dict):
            continue

        existing_username = user.get("username", key)
        existing_email = user.get("email", "")

        if str(existing_username).casefold() == username_key:
            return "username"

        if str(existing_email).casefold() == email_key:
            return "email"

    return None


#creating the otp to send
def send_otp_email(email: str, otp: str):
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    if not smtp_user or not smtp_password:
        raise HTTPException(
            status_code=500,
            detail="SMTP email configuration is missing."
        )

    message = EmailMessage()

    message["Subject"] = "Synapse Email Verification"
    message["From"] = smtp_user
    message["To"] = email

    message.set_content(
        f"""
Synapse

Your email verification OTP is: {otp}

This OTP expires in 5 minutes.

"""
    )
    

    try:
        with smtplib.SMTP(
            smtp_host, smtp_port, timeout=15
        ) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(message)

    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not send verification email."
        ) from exc


#creating API key for to the html page
@app.get("/register-page")
def registration_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="register.html"
    )


#taking the user sequest for the otp and sending the otp after veryfing the username and email
@app.post("/registration/send-otp")
def send_registration_otp(data: RegistrationRequest):

    username = data.username.strip()
    email = str(data.email).strip().casefold()

    if not validate_username(username):
        raise HTTPException(
            status_code=400,
            detail="Username must contain 3-40 letters, numbers, dots, underscores, or hyphens."
        )

    # Check whether username or email is already registered.
    with file_lock:
        users = load_users()
        duplicate = find_existing_user(users, username, email)

    if duplicate == "username":
        raise HTTPException(
            status_code=409,
            detail="This username is already registered."
        )

    if duplicate == "email":
        raise HTTPException(
            status_code=409,
            detail="This email is already registered."
        )

    now = time.time()

    with registration_lock:
        previous = pending_registrations.get(email)

        if previous and now - previous["last_sent"] < OTP_RESEND_DELAY:
            raise HTTPException(
                status_code=429,
                detail="Please wait 30 seconds before requesting another OTP."
            )

        otp = f"{secrets.randbelow(1_000_000):06d}"

        # Send the email before creating the pending registration.
        send_otp_email(email, otp)

        pending_registrations[email] = {
            "username": username,
            "otp_hash": hashlib.sha256(
                otp.encode("utf-8")
            ).hexdigest(),
            "expires_at": now + OTP_EXPIRY,
            "last_sent": now,
            "attempts": 0
        }

    return {
        "message": "OTP sent successfully. Please check your email."
    }


#verify the otp before registering the user
@app.post("/registration/verify-otp")
def verify_registration_otp(data: VerifyOTPRequest):

    username = data.username.strip()
    email = str(data.email).strip().casefold()

    if not validate_username(username):
        raise HTTPException(
            status_code=400,
            detail="Invalid username."
        )

    with registration_lock:

        record = pending_registrations.get(email)

        if not record:
            raise HTTPException(
                status_code=400,
                detail="No pending registration. Request a new OTP."
            )

        if time.time() > record["expires_at"]:
            pending_registrations.pop(email, None)

            raise HTTPException(
                status_code=400,
                detail="OTP expired. Please request a new OTP."
            )

        record["attempts"] += 1

        if record["attempts"] > MAX_OTP_ATTEMPTS:
            pending_registrations.pop(email, None)

            raise HTTPException(
                status_code=429,
                detail="Too many incorrect attempts. Request a new OTP."
            )

        if record["username"] != username:
            raise HTTPException(
                status_code=400,
                detail="Registration details do not match."
            )

        submitted_hash = hashlib.sha256(
            data.otp.encode("utf-8")
        ).hexdigest()

        if not hmac.compare_digest(
            submitted_hash, record["otp_hash"]
        ):
            raise HTTPException(
                status_code=400,
                detail="Incorrect OTP."
            )

        # OTP is correct. Now save the verified user.
        with file_lock:
            users = load_users()
            duplicate = find_existing_user(users, username, email)

            if duplicate:
                pending_registrations.pop(email, None)

                detail = (
                    "This username is already registered."
                    if duplicate == "username"
                    else "This email is already registered."
                )
                raise HTTPException(
                    status_code=409,
                    detail=detail
                )

            # The admin will assign the password later.
            users[username] = {
                "username": username,
                "email": email,
                "email_verified": True,
                "password": 1234
            }

            save_users(users)

        # OTP cannot be reused.
        pending_registrations.pop(email, None)

    return {
        "message": "Email verified and registration successful. The admin can now assign your password.",
        "username": username
    }


@app.get("/")
def home():
    return {
        "message": "Synapse registration API is running."
    }