
import os
import time
import secrets
import smtplib

from pathlib import Path
from email.message import EmailMessage
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field
from src.py.db_connection import get_db_connection

from src.py.registration import (
    send_registration_otp,
    verify_registration_otp,
    RegistrationRequest,
    VerifyOTPRequest as RegistrationVerifyOTPRequest,
)
from src.py.workspace import (
    create_session,
    get_current_user_id,
    router as workspace_router,
)

app = FastAPI(title="Synapse Login")

BASE_DIR = Path(__file__).resolve().parents[2]

templates = Jinja2Templates(
    directory=str(BASE_DIR / "template")
)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict before deployment
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(workspace_router)

OTP_EXPIRY = 300
pending_otps: dict[str, dict[str, Any]] = {}


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    email: EmailStr

class LoginVerifyOTPRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    email: EmailStr
    otp: str = Field(pattern=r"^[0-9]{6}$")




def find_user_by_email(email):
    email = email.strip().casefold()

    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT id, username, email
            FROM users
            WHERE LOWER(email) = %s
            LIMIT 1
            """,
            (email,),
        ).fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "username": row[1],
        "email": row[2],
    }


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
def workspace_page(
    request: Request,
    user_id: int = Depends(get_current_user_id),
):
    return templates.TemplateResponse(
        request=request,
        name="workspace.html",
    )


@app.post("/send-otp")
def send_otp(credentials: LoginRequest):
    email = str(credentials.email).strip().casefold()
    user = find_user_by_email(email)

    if not user or (
        user["username"].casefold()
        != credentials.username.strip().casefold()
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid username or email.",
        )

    now = time.time()
    previous = pending_otps.get(email)

    if previous and now - previous["last_sent"] < 30:
        raise HTTPException(
            status_code=429,
            detail="Please wait 30 seconds before requesting another OTP.",
        )

    otp = f"{secrets.randbelow(1_000_000):06d}"
    send_email_otp(email, otp)

    pending_otps[email] = {
        "otp": otp,
        "expires_at": now + OTP_EXPIRY,
        "last_sent": now,
        "attempts": 0,
        "username": user["username"],
        "user_id": user["id"],
    }

    return {"message": "Username and email verified. OTP sent to your email."}


@app.post("/verify-otp")
def verify_otp(data: LoginVerifyOTPRequest, response: Response):
    email = str(data.email).strip().casefold()
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

    if (
        record["username"].casefold()
        != data.username.strip().casefold()
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid username or OTP.",
        )

    if not secrets.compare_digest(data.otp, record["otp"]):
        raise HTTPException(
            status_code=401,
            detail="Incorrect OTP.",
        )

    username = record["username"]
    user_id = record["user_id"]
    pending_otps.pop(email, None)
    create_session(response, user_id)

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