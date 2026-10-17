# Synapse
Mulit user file management system


to run the server
python -m uvicorn src.py.user_login:app --reload

Verified registrations are saved in `user.json` in the repository root. The
file is created when the first registration is successfully verified.