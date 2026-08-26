# python3 -m venv venv
# source venv/bin/activate
# pip install fastapi uvicorn python-jose mysql-connector-python
# uvicorn main:app --reload


from fastapi import FastAPI, HTTPException, Depends, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import mysql.connector
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import os
from dotenv import load_dotenv
from jose import JWTError, jwt
from typing import Optional

load_dotenv()

app = FastAPI()

# =========================
# CORS
# =========================

frontend_url = os.getenv(
    "FRONTEND_URL",
    "http://127.0.0.1:5500"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "https://request-system-frontend.onrender.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# DATABASE CONNECTION
# =========================

def get_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        ssl_ca=os.getenv("DB_SSL_CA")
    )


# =========================================================
# JWT CONFIGURATION
# =========================================================

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60


# =========================================================
# PYDANTIC MODELS
# =========================================================

class CreateRequest(BaseModel):
    requester_id: int
    dept_id: int
    requested_task: str
    requester_email: str
    format: str
    urgency: str


class AssignRequest(BaseModel):
    request_id: int
    assigned_by: int
    assigned_to: int
    assignment_type: str = "Manual"
    notes: Optional[str] = None


class CompleteRequest(BaseModel):
    request_id: int
    output_description: str
    sql_query: str
    output_file_path: Optional[str] = None

class LoginRequest(BaseModel):
    username: str
    password: str



# =========================================================
# NOTIFICATION HELPER
# =========================================================

def create_notification(
    cursor,
    request_id,
    user_id,
    message,
    notification_type,
    channel="system"
):

    cursor.execute("""
        INSERT INTO Notifications
        (
            request_id,
            user_id,
            message,
            type,
            channel,
            is_read
        )
        VALUES
        (%s, %s, %s, %s, %s, FALSE)
    """, (
        request_id,
        user_id,
        message,
        notification_type,
        channel
    ))

# =========================================================
# EMAIL FUNCTION
# =========================================================

def send_email(to_email, subject, body):

    sender = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASS")

    if not sender or not password:
        print("⚠️ EMAIL_USER or EMAIL_PASS is not configured.")
        return

    msg = MIMEText(body)

    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email

    try:

        server = smtplib.SMTP("smtp.gmail.com", 587)

        server.starttls()

        server.login(sender, password)

        server.sendmail(
            sender,
            to_email,
            msg.as_string()
        )

        server.quit()

        print("✅ Email sent to:", to_email)

    except Exception as e:

        print("❌ Email error:", e)
    



# =========================================================
# JWT TOKEN
# =========================================================


security = HTTPBearer()


def create_access_token(data: dict):

    to_encode = data.copy()

    expire = datetime.utcnow() + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )

    to_encode.update({
        "exp": expire
    })

    return jwt.encode(
        to_encode,
        SECRET_KEY,
        algorithm=ALGORITHM
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):

    token = credentials.credentials

    try:

        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        user_id = payload.get("user_id")
        username = payload.get("username")
        role = payload.get("role")

        if not user_id or not username or not role:

            raise HTTPException(
                status_code=401,
                detail="Invalid authentication token"
            )

        return {
            "user_id": user_id,
            "username": username,
            "role": role
        }

    except JWTError:

        raise HTTPException(
            status_code=401,
            detail="Invalid or expired authentication token"
        )
        
# =========================================================
# 1. LOGIN
# =========================================================

@app.post("/login")
def login(req: LoginRequest):

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT
                user_id,
                bank_user_id,
                username,
                email,
                full_name,
                role,
                dept_id
            FROM Users
            WHERE username = %s
              AND password = %s
        """, (
            req.username,
            req.password
        ))

        user = cursor.fetchone()

    finally:

        cursor.close()
        db.close()

    if not user:

        raise HTTPException(
            status_code=401,
            detail="Invalid username or password"
        )

    token = create_access_token({
        "user_id": user["user_id"],
        "username": user["username"],
        "role": user["role"]
    })

    return {

        "message": "Login successful",

        "access_token": token,

        "token_type": "bearer",

        "user": {

            "user_id": user["user_id"],
            "bank_user_id": user["bank_user_id"],
            "username": user["username"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
            "dept_id": user["dept_id"]

        }

    }
# =========================================================
# CREATE REQUEST
# =========================================================

@app.post("/requests")
def create_request(req: CreateRequest):

    db = get_db()
    cursor = db.cursor(dictionary=True)

    now = datetime.now()

    try:

        # -------------------------------------------------
        # CREATE REQUEST
        # -------------------------------------------------

        cursor.execute("""
            INSERT INTO Requests
            (
                requester_id,
                dept_id,
                requested_task,
                requester_email,
                format,
                urgency,
                status,
                created_at
            )
            VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            req.requester_id,
            req.dept_id,
            req.requested_task,
            req.requester_email,
            req.format,
            req.urgency,
            "Pending",
            now
        ))

        request_id = cursor.lastrowid


        # -------------------------------------------------
        # GET DEPARTMENT GROUP EMAIL
        # -------------------------------------------------

        cursor.execute("""
            SELECT group_email
            FROM Departments
            WHERE dept_id = %s
        """, (req.dept_id,))

        department = cursor.fetchone()


        # -------------------------------------------------
        # GET MANAGERS AND ENGINEERS
        # -------------------------------------------------

        cursor.execute("""
            SELECT user_id
            FROM Users
            WHERE LOWER(role) IN ('engineer', 'manager')
        """)

        recipients = cursor.fetchall()


        # -------------------------------------------------
        # CREATE SYSTEM NOTIFICATIONS
        # -------------------------------------------------

        for recipient in recipients:

            create_notification(
                cursor=cursor,
                request_id=request_id,
                user_id=recipient["user_id"],
                message=(
                    f"New data request #{request_id} "
                    f"has been submitted. "
                    f"Urgency: {req.urgency}."
                ),
                notification_type="NEW_REQUEST",
                channel="system"
            )


        # -------------------------------------------------
        # COMMIT REQUEST + NOTIFICATIONS TOGETHER
        # -------------------------------------------------

        db.commit()


    except Exception as e:

        db.rollback()

        print("❌ CREATE REQUEST ERROR:", e)

        raise HTTPException(
            status_code=500,
            detail="Failed to create request."
        )


    finally:

        cursor.close()
        db.close()


    # =====================================================
    # SEND EMAIL TO DEPARTMENT
    # =====================================================

    if department and department["group_email"]:

        send_email(
            department["group_email"],
            "New Data Request",
            f"""
A new data request has been submitted.

Request ID: {request_id}

Requested Task:
{req.requested_task}

Urgency:
{req.urgency}

Please log into the Data Request Management System
to review the request.
"""
        )


    return {
        "message": "Request created successfully",
        "request_id": request_id
    }
 
# =========================================================
# REQUESTER - MY REQUESTS
# =========================================================

@app.get("/requester/my-requests")
def get_my_requests(
    user=Depends(get_current_user)
):

    # -----------------------------------------------------
    # CHECK ROLE
    # -----------------------------------------------------

    if user["role"].lower() != "requester":

        raise HTTPException(
            status_code=403,
            detail="Only requesters can access their requests"
        )

    # -----------------------------------------------------
    # GET LOGGED-IN USER ID
    # -----------------------------------------------------

    requester_id = int(user["user_id"])

    db = get_db()

    cursor = db.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT
                r.request_id,
                r.requested_task,
                r.requester_email,
                r.format,
                r.urgency,
                r.status,
                r.created_at,
                r.updated_at,
                d.dept_name,
                u.full_name AS engineer_name

            FROM Requests r

            LEFT JOIN Departments d
                ON r.dept_id = d.dept_id

            LEFT JOIN Users u
                ON r.assigned_to = u.user_id

            WHERE r.requester_id = %s

            ORDER BY r.created_at DESC
        """, (requester_id,))

        requests = cursor.fetchall()

    finally:

        cursor.close()
        db.close()

    return requests
# =========================================================
# 3. MANAGER VIEW REQUESTS
# =========================================================

@app.get("/manager/requests")
def manager_requests():

    db = get_db()

    cursor = db.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT
                r.*,
                u.full_name AS requester_name
            FROM Requests r
            JOIN Users u
                ON r.requester_id = u.user_id
            WHERE r.status = 'Pending'
            ORDER BY r.created_at DESC
        """)

        data = cursor.fetchall()

    finally:

        cursor.close()
        db.close()

    return data


# =========================================================
# 4. MANAGER VIEW ALL REQUESTS
# =========================================================

@app.get("/manager/all-requests")
def manager_all_requests():

    db = get_db()

    cursor = db.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT
                r.*,
                requester.full_name AS requester_name,
                engineer.full_name AS engineer_name
            FROM Requests r

            JOIN Users requester
                ON r.requester_id = requester.user_id

            LEFT JOIN Users engineer
                ON r.assigned_to = engineer.user_id

            ORDER BY r.created_at DESC
        """)

        data = cursor.fetchall()

    finally:

        cursor.close()
        db.close()

    return data

# =========================================================
# GET ENGINEERS
# =========================================================

@app.get("/manager/engineers")
def get_engineers():

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT
                user_id,
                username,
                full_name,
                email
            FROM Users
            WHERE LOWER(role) = 'engineer'
            ORDER BY full_name ASC
        """)

        engineers = cursor.fetchall()

    finally:

        cursor.close()
        db.close()

    return engineers


# =========================================================
# 5. MANAGER ASSIGN REQUEST
# =========================================================

@app.post("/manager/assign")
def assign_request(req: AssignRequest):

    db = get_db()

    cursor = db.cursor()

    now = datetime.now()

    try:

        # -------------------------------------------------
        # CHECK REQUEST
        # -------------------------------------------------

        cursor.execute("""
            SELECT assigned_to, status
            FROM Requests
            WHERE request_id = %s
        """, (req.request_id,))

        request = cursor.fetchone()

        if not request:

            raise HTTPException(
                status_code=404,
                detail="Request not found"
            )

        # -------------------------------------------------
        # CHECK IF ALREADY ASSIGNED
        # -------------------------------------------------

        if request[0] is not None:

            raise HTTPException(
                status_code=400,
                detail="Request is already assigned"
            )

        # -------------------------------------------------
        # INSERT ASSIGNMENT
        # -------------------------------------------------

        cursor.execute("""
            INSERT INTO Assignments
            (
                request_id,
                assigned_by,
                assigned_to,
                assignment_type,
                assigned_at,
                notes

            )
            VALUES
            (%s, %s, %s, %s, %s, %s)
        """, (

            req.request_id,

            req.assigned_by,

            req.assigned_to,

            req.assignment_type,

            now,

            req.notes

           

        ))

        # -------------------------------------------------
        # UPDATE REQUEST
        # -------------------------------------------------

        cursor.execute("""
            UPDATE Requests

            SET
                status = 'In Progress',
                assigned_to = %s,
                updated_at = %s

            WHERE request_id = %s
        """, (

            req.assigned_to,

            now,

            req.request_id

        ))

        # -------------------------------------------------
        # GET ENGINEER EMAIL
        # -------------------------------------------------

        cursor.execute("""
            SELECT
                email,
                full_name
            FROM Users
            WHERE user_id = %s
        """, (req.assigned_to,))

        engineer = cursor.fetchone()

        # -------------------------------------------------
        # CREATE NOTIFICATION
        # -------------------------------------------------

        cursor.execute("""
            INSERT INTO Notifications
            (
                request_id,
                user_id,
                message,
                type,
                channel,
                is_read
            )
            VALUES
            (%s, %s, %s, %s, %s, %s)
        """, (

            req.request_id,

            req.assigned_to,

            f"Request {req.request_id} has been assigned to you.",

            "Assignment",

            "System",

            False

        ))

        db.commit()

    except HTTPException:

        db.rollback()

        raise

    except Exception:

        db.rollback()

        raise

    finally:

        cursor.close()
        db.close()

    # -----------------------------------------------------
    # SEND EMAIL TO ENGINEER
    # -----------------------------------------------------

    if engineer:

        send_email(

            engineer[0],

            "New Data Request Assigned",

            f"""
Hello {engineer[1]},

You have been assigned a new data request.

Request ID:
{req.request_id}

Please log into the Data Request Management System
to view and process the request.

Thank you.
Data Engineering Division
"""

        )

    return {

        "message": "Request assigned successfully",

        "request_id": req.request_id,

        "assigned_to": req.assigned_to

    }

# =========================================================
# MANAGER TAKE REQUEST
# =========================================================

@app.post("/manager/accept/{request_id}")
def manager_accept_request(
    request_id: int,
    user=Depends(get_current_user)
):

    if user["role"].lower() != "manager":

        raise HTTPException(
            status_code=403,
            detail="Only managers can take requests"
        )

    db = get_db()
    cursor = db.cursor()

    # DEBUG
    cursor.execute("""
        SELECT request_id, status, assigned_to
        FROM Requests
        WHERE request_id = %s
    """, (request_id,))

    debug_request = cursor.fetchone()

    print("====================================")
    print("MANAGER ACCEPT DEBUG")
    print("Request ID:", request_id)
    print("Database sees:", debug_request)
    print("DB HOST:", os.getenv("DB_HOST"))
    print("DB PORT:", os.getenv("DB_PORT"))
    print("DB NAME:", os.getenv("DB_NAME"))
    print("====================================")

    now = datetime.now()

    try:

        cursor.execute("""
            UPDATE Requests
            SET
                assigned_to = %s,
                status = 'In Progress',
                updated_at = %s
            WHERE request_id = %s
              AND assigned_to IS NULL
              AND status = 'Pending'
        """, (
            int(user["user_id"]),
            now,
            int(request_id)
        ))

        if cursor.rowcount == 0:

            db.rollback()

            raise HTTPException(
                status_code=409,
                detail="Request is already assigned or is no longer pending"
            )

        cursor.execute("""
            INSERT INTO Assignments
            (
                request_id,
                assigned_by,
                assigned_to,
                assignment_type,
                assigned_at,
                notes
            )
            VALUES
            (%s, %s, %s, %s, %s, %s)
        """, (
            request_id,
            user["user_id"],
            user["user_id"],
            "Self",
            now,
            "Taken by manager"
        ))

        cursor.execute("""
            INSERT INTO Notifications
            (
                request_id,
                user_id,
                message,
                type,
                channel,
                is_read
            )
            VALUES
            (%s, %s, %s, %s, %s, %s)
        """, (
            request_id,
            user["user_id"],
            f"You took request {request_id}.",
            "Assignment",
            "System",
            False
        ))

        db.commit()

    except HTTPException:

        db.rollback()
        raise

    except Exception as e:

        db.rollback()

        print("❌ MANAGER TAKE REQUEST ERROR:", e)

        raise HTTPException(
            status_code=500,
            detail="Failed to take request"
        )

    finally:

        cursor.close()
        db.close()

   return {
    "message": "Request accepted by you",
    "request_id": request_id,
    "assigned_to": user["user_id"],
    "status": "In Progress"
}


# =========================================================
# 6. ENGINEER VIEW ASSIGNED REQUESTS
# =========================================================

@app.get("/engineer/requests/{engineer_id}")
def engineer_requests(engineer_id: int):

    db = get_db()

    cursor = db.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT
                r.*,
                u.full_name AS requester_name
            FROM Requests r

            JOIN Users u
                ON r.requester_id = u.user_id

            WHERE r.assigned_to = %s
             

            ORDER BY r.created_at DESC
        """, (engineer_id,))

        data = cursor.fetchall()

    finally:

        cursor.close()
        db.close()

    return data


@app.get("/engineer/request/{request_id}/output")
def get_request_output(request_id: int):

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT
                ro.request_id,
                ro.assigned_to,
                ro.output_description,
                ro.sql_query,
                ro.output_file_path,
                ro.created_at,

                u.full_name AS engineer_name

            FROM RequestOutputs ro

            LEFT JOIN Users u
                ON ro.assigned_to = u.user_id

            WHERE ro.request_id = %s

            ORDER BY ro.created_at DESC

            LIMIT 1
        """, (request_id,))

        output = cursor.fetchone()

        if not output:

            raise HTTPException(
                status_code=404,
                detail="No output found for this request."
            )

        return output

    finally:

        cursor.close()
        db.close()

# =========================================================
# ENGINEER VIEW ALL REQUESTS
# =========================================================

@app.get("/engineer/all-requests")
def engineer_all_requests(
    user=Depends(get_current_user)
):

    # -----------------------------------------------------
    # CHECK ROLE
    # -----------------------------------------------------

    if user["role"].lower() != "engineer":

        raise HTTPException(
            status_code=403,
            detail="Only engineers can view all requests"
        )

    db = get_db()

    cursor = db.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT
                r.request_id,
                r.requested_task,
                r.requester_email,
                r.format,
                r.urgency,
                r.status,
                r.assigned_to,
                r.created_at,
                r.updated_at,

                requester.full_name AS requester_name,

                engineer.full_name AS engineer_name,

                ro.output_description,
                ro.sql_query,
                ro.output_file_path,
                ro.created_at AS output_created_at

            FROM Requests r

            JOIN Users requester
                ON r.requester_id = requester.user_id

            LEFT JOIN Users engineer
                ON r.assigned_to = engineer.user_id

            LEFT JOIN RequestOutputs ro
                ON r.request_id = ro.request_id

            ORDER BY r.created_at DESC
        """)

        data = cursor.fetchall()

    finally:

        cursor.close()
        db.close()

    return data


# =========================================================
# 7. ENGINEER VIEW UNASSIGNED REQUESTS
# =========================================================

@app.get("/requests/unassigned")
def get_unassigned():

    db = get_db()

    cursor = db.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT
                r.*,
                u.full_name AS requester_name
            FROM Requests r

            JOIN Users u
                ON r.requester_id = u.user_id

            WHERE r.assigned_to IS NULL
              AND r.status = 'Pending'

            ORDER BY r.created_at ASC
        """)

        data = cursor.fetchall()

    finally:

        cursor.close()
        db.close()

    return data


# =========================================================
# 8. ENGINEER ACCEPT REQUEST
# =========================================================

@app.post("/engineer/accept/{request_id}")
def accept_request(
    request_id: int,
    user=Depends(get_current_user)
):

    # -----------------------------------------------------
    # CHECK ROLE
    # -----------------------------------------------------

    if user["role"].lower() != "engineer":

        raise HTTPException(
            status_code=403,
            detail="Only engineers can accept requests"
        )

    db = get_db()

    cursor = db.cursor()

    now = datetime.now()

    try:

        # -------------------------------------------------
        # ATOMIC ASSIGNMENT
        #
        # This prevents two engineers from accepting
        # the same request at the same time.
        # -------------------------------------------------

        cursor.execute("""
            UPDATE Requests

            SET
                assigned_to = %s,
                status = 'In Progress',
                updated_at = %s

            WHERE request_id = %s
              AND assigned_to IS NULL
              AND status = 'Pending'
        """, (

            int(user["user_id"]),

            now,

            int(request_id)

        ))

        # -------------------------------------------------
        # REQUEST WAS ALREADY TAKEN
        # -------------------------------------------------

        if cursor.rowcount == 0:

            db.rollback()

            return {

                "error":
                "Request is already taken or does not exist"

            }

        # -------------------------------------------------
        # CREATE ASSIGNMENT RECORD
        # -------------------------------------------------

        cursor.execute("""
            INSERT INTO Assignments
            (
                request_id,
                assigned_by,
                assigned_to,
                assignment_type,
                assigned_at,
                notes
            
            )
            VALUES
            (%s, %s, %s, %s, %s, %s)
        """, (

            request_id,

            user["user_id"],

            user["user_id"],

            "Self",

            now,

            "Accepted by engineer"

            

        ))

        # -------------------------------------------------
        # CREATE NOTIFICATION
        # -------------------------------------------------

        cursor.execute("""
            INSERT INTO Notifications
            (
                request_id,
                user_id,
                message,
                type,
                channel,
                is_read
            )
            VALUES
            (%s, %s, %s, %s, %s, %s)
        """, (

            request_id,

            user["user_id"],

            f"You accepted request {request_id}.",

            "Assignment",

            "System",

            False

        ))

        db.commit()

    except Exception:

        db.rollback()

        raise

    finally:

        cursor.close()
        db.close()

    return {

        "message": "Request accepted successfully",

        "request_id": request_id,

        "assigned_to": user["user_id"]

    }

# =========================================================
# 9. ENGINEER COMPLETE REQUEST
# =========================================================

@app.post("/engineer/complete")
def complete_request(
    req: CompleteRequest,
    user=Depends(get_current_user)
):

    # -----------------------------------------------------
    # CHECK ROLE
    # -----------------------------------------------------

    if user["role"].lower() not in ["engineer", "manager"]:

        raise HTTPException(
            status_code=403,
            detail="Only engineers or managers can complete requests"
        )

    db = get_db()

    cursor = db.cursor(dictionary=True)

    now = datetime.now()

    try:

        # -------------------------------------------------
        # GET REQUEST
        # -------------------------------------------------

        cursor.execute("""
            SELECT
                request_id,
                requester_id,
                requester_email,
                assigned_to,
                status
            FROM Requests
            WHERE request_id = %s
        """, (req.request_id,))

        request = cursor.fetchone()

        if not request:

            raise HTTPException(
                status_code=404,
                detail="Request not found"
            )

        # -------------------------------------------------
        # CHECK ASSIGNMENT
        # -------------------------------------------------

        if request["assigned_to"] != int(user["user_id"]):

            raise HTTPException(
                status_code=403,
                detail="You are not assigned to this request"
            )

        # -------------------------------------------------
        # CHECK STATUS
        # -------------------------------------------------

        if request["status"] != "In Progress":

            raise HTTPException(
                status_code=400,
                detail="Only requests that are In Progress can be completed"
            )

        # -------------------------------------------------
        # SAVE REQUEST OUTPUT
        # -------------------------------------------------

        cursor.execute("""
            INSERT INTO RequestOutputs
            (
                request_id,
                assigned_to,
                output_description,
                sql_query,
                output_file_path,
                created_at
            )
            VALUES
            (%s, %s, %s, %s, %s, %s)
        """, (

            req.request_id,

            user["user_id"],

            req.output_description,

            req.sql_query,

            req.output_file_path,

            now

        ))

        # -------------------------------------------------
        # UPDATE REQUEST STATUS
        # -------------------------------------------------

        cursor.execute("""
            UPDATE Requests

            SET
                status = 'Completed',
                updated_at = %s

            WHERE request_id = %s
              AND assigned_to = %s
              AND status = 'In Progress'
        """, (

            now,

            req.request_id,

            user["user_id"]

        ))

        if cursor.rowcount == 0:

            raise HTTPException(
                status_code=409,
                detail="Request could not be completed"
            )

        # -------------------------------------------------
        # CREATE NOTIFICATION
        # -------------------------------------------------

        cursor.execute("""
            INSERT INTO Notifications
            (
                request_id,
                user_id,
                message,
                type,
                channel,
                is_read
            )
            VALUES
            (%s, %s, %s, %s, %s, %s)
        """, (

            req.request_id,

            request["requester_id"],

            f"Request {req.request_id} has been completed.",

            "Completion",

            "System",

            False

        ))

        # -------------------------------------------------
        # COMMIT EVERYTHING
        # -------------------------------------------------

        db.commit()

    except HTTPException:

        db.rollback()

        raise

    except Exception:

        db.rollback()

        raise

    finally:

        cursor.close()

        db.close()

    # -----------------------------------------------------
    # SEND EMAIL
    # -----------------------------------------------------

    if request["requester_email"]:

        try:

            send_email(

                request["requester_email"],

                "Data Request Completed",

                f"""
Your data request has been completed.

Request ID:
{req.request_id}

Output:
{req.output_description}

Please contact the Data Engineering Division
if you have any questions.
"""

            )

        except Exception as email_error:

            print(
                "Email notification failed:",
                email_error
            )

    return {

        "message":
        "Request completed successfully",

        "request_id":
        req.request_id,

        "status":
        "Completed"

    }

# =========================================================
# GET MY NOTIFICATIONS
# =========================================================

@app.get("/notifications/me")
def get_my_notifications(
    user=Depends(get_current_user)
):

    user_id = int(user["user_id"])

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT
                notification_id,
                request_id,
                message,
                type,
                channel,
                is_read
            FROM Notifications
            WHERE user_id = %s
            ORDER BY notification_id DESC
        """, (user_id,))

        notifications = cursor.fetchall()

    finally:

        cursor.close()
        db.close()

    return notifications


# =========================================================
# GET UNREAD NOTIFICATION COUNT
# =========================================================

@app.get("/notifications/unread-count")
def get_unread_notification_count(
    user=Depends(get_current_user)
):

    user_id = int(user["user_id"])

    db = get_db()
    cursor = db.cursor()

    try:

        cursor.execute("""
            SELECT COUNT(*)
            FROM Notifications
            WHERE user_id = %s
              AND is_read = FALSE
        """, (user_id,))

        count = cursor.fetchone()[0]

    finally:

        cursor.close()
        db.close()

    return {
        "unread_count": count
    }



# =========================================================
# 11. MARK NOTIFICATION AS READ
# =========================================================

@app.put("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    user=Depends(get_current_user)
):

    db = get_db()

    cursor = db.cursor()

    try:

        cursor.execute("""
            UPDATE Notifications

            SET is_read = TRUE

            WHERE notification_id = %s
              AND user_id = %s
        """, (
            notification_id,
            user["user_id"]
        ))

        if cursor.rowcount == 0:

            raise HTTPException(
                status_code=404,
                detail="Notification not found"
            )

        db.commit()

    except HTTPException:

        db.rollback()
        raise

    finally:

        cursor.close()
        db.close()

    return {
        "message": "Notification marked as read"
    }

# =========================================================
# 12. HEALTH CHECK
# =========================================================

@app.get("/")
def root():

    return {

        "message":
        "Internal Data Request Management System API is running",

        "status": "OK"

    }