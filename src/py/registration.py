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
from src.py.db_connection import get_db_connection


app = FastAPI(title="Synapse Registration")

# Path of the directory - register.html
BASE_DIR = Path(__file__).resolve().parents[2]



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

def hash_password(password: str) -> dict:
    salt = secrets.token_bytes(16)
    iterations = 600_000

    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )

    import base64

    return {
        "algorithm": "pbkdf2_sha256",
        "salt": base64.b64encode(salt).decode("utf-8"),
        "hash": base64.b64encode(hashed).decode("utf-8"),
        "iterations": iterations,
    }



def find_existing_user(username: str, email: str):
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT
                CASE
                    WHEN LOWER(username) = LOWER(%s)
                        THEN 'username'
                    WHEN LOWER(email) = LOWER(%s)
                        THEN 'email'
                END
            FROM users
            WHERE LOWER(username) = LOWER(%s)
               OR LOWER(email) = LOWER(%s)
            LIMIT 1;
            """,
            (username, email, username, email),
        ).fetchone()

    return row[0] if row else None

#creating the otp to send
def send_otp_email(email: str, otp: str):
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    print("DEBUG SMTP_USER set:", bool(smtp_user))
    print("DEBUG SMTP_PASSWORD set:", bool(smtp_password))

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
    duplicate = find_existing_user(username, email)

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
                # OTP is correct. Save the user in PostgreSQL.
        duplicate = find_existing_user(username, email)

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

        password_data = hash_password("1234")
        password_hash = json.dumps(password_data)

        try:
            with get_db_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO users (username, email, password_hash)
                    VALUES (%s, %s, %s)
                    """,
                    (username, email, password_hash),
                )
        except Exception as exc:
            pending_registrations.pop(email, None)
            raise HTTPException(
                status_code=500,
                detail="Could not save user in database."
            ) from exc

        # OTP cannot be reused.
        pending_registrations.pop(email, None)

    return {
        "message": "Email verified and registration successful. Your temporary password is 1234.",
        "username": username
    }


@app.get("/")
def home():
    return {
        "message": "Synapse registration API is running."
    }