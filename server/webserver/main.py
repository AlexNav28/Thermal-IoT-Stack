import os
import json
import time
import asyncio
import uvicorn

import mysql.connector
import paho.mqtt.client as mqtt

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request, Cookie, Form 
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import bcrypt
import uuid


load_dotenv()

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_BROKER_PORT = 1883
MQTT_TOPIC = os.getenv("MQTT_TOPIC")
MQTT_COMMAND_TOPIC = f"{MQTT_TOPIC}/command" 
MQTT_MESSAGE_TOPIC = f"{MQTT_TOPIC}/data"




# Pydantic validation models
class ReadingIn(BaseModel):
    mac_address: str
    pixels: list[float] = Field(..., description="List of 64 pixel values")
    thermistor:  float                  
    prediction:  str                    
    confidence:  float
    
class CommandIn(BaseModel):
    command: str

class UserIn(BaseModel):
    username: str
    password: str
  

# Database

def get_db():
    conn = mysql.connector.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"]
    )
    try:
        yield conn
    finally:
        conn.close()

# Get current user
def get_current_user(session_token: str | None = Cookie(None), conn=Depends(get_db)):
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT users.id, users.username FROM sessions "
        "JOIN users ON sessions.user_id = users.id "
        "WHERE sessions.session_token = %s", (session_token,),
    )
    user = cursor.fetchone()
    cursor.close()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user  

# Websocket 

ws_clients: list[WebSocket] = []
latest_reading: dict | None = None


async def broadcast_frames():
    global latest_reading
    while True:
        if latest_reading is not None and ws_clients:
            payload = latest_reading
            latest_reading = None
            dead = []
            for client in list(ws_clients):
                try:
                    await client.send_json(payload)
                except Exception:
                    dead.append(client)
            for c in dead:
                if c in ws_clients:
                    ws_clients.remove(c)
        await asyncio.sleep(0.1)


# MQTT implementation

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker with result code {rc}")
    client.subscribe(MQTT_MESSAGE_TOPIC)
    print(f"Subscribed to topic: {MQTT_MESSAGE_TOPIC}")


def on_message(client, userdata, msg):
    global latest_reading
    try:
        json_data = json.loads(msg.payload.decode())
        reading   = ReadingIn(**json_data)
    except Exception as e:
        print(f"[MQTT] Parse error: {e}")
        return

    try:
        conn = mysql.connector.connect(
            host=os.environ["DB_HOST"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            database=os.environ["DB_NAME"],
        )
        cursor = conn.cursor()

        cursor.execute(
            "INSERT IGNORE INTO devices (mac_address) VALUES (%s)",
            (reading.mac_address,),
        )
        cursor.execute(
            """INSERT INTO readings
               (mac_address, thermistor_temp, prediction, confidence, pixels)
               VALUES (%s, %s, %s, %s, %s)""",
            (reading.mac_address, reading.thermistor,
             reading.prediction.upper(), reading.confidence,
             json.dumps(reading.pixels)),
        )
        conn.commit()
        new_id = cursor.lastrowid
        cursor.close()
        conn.close()
        print(f"[DB] Saved reading id={new_id}")

        latest_reading = {
            "event":        "new_reading",
            "id":           new_id,
            "mac_address":  reading.mac_address,
            "thermistor":   reading.thermistor,
            "prediction":   reading.prediction.upper(),
            "confidence":   reading.confidence,
            "pixels":       reading.pixels,
        }

    except Exception as e:
        print(f"[DB] Insert error: {e}")
         

# Initialize MQTT client
mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect 
mqtt_client.on_message = on_message


@asynccontextmanager
async def lifespan(app: FastAPI):

    for _ in range(30):
        try:
            conn = mysql.connector.connect(
            host=os.environ["DB_HOST"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            database=os.environ["DB_NAME"]
            )
            cursor = conn.cursor()
            with open("init.sql") as f:
                for statement in f.read().split(";"):
                    statement = statement.strip()
                    if statement:
                        cursor.execute(statement)
            conn.commit()
            cursor.close()
            conn.close()
            print("[DB] Tables initialised")
            break
        except mysql.connector.Error:
            print("[DB] Waiting for MySQL..")
            time.sleep(1)

    if MQTT_BROKER:
        mqtt_client.connect(MQTT_BROKER, MQTT_BROKER_PORT, 60)
        mqtt_client.loop_start()
        print(f"MQTT client started, connecting to {MQTT_BROKER}")
    else:
        print("Warning: MQTT_BROKER not configured")

    asyncio.create_task(broadcast_frames()) 
    print("[WS] broadcast_frames task started")
 
    yield 
    
    mqtt_client.loop_stop()
    mqtt_client.disconnect()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
VALID_COMMANDS = {"get_one", "start_continuous", "stop"}

# Routes

@app.get("/", response_class=HTMLResponse)
def root(request: Request, session_token: str | None = Cookie(None), conn=Depends(get_db)):
    if session_token:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT users.id FROM sessions "
            "JOIN users ON sessions.user_id = users.id "
            "WHERE sessions.session_token = %s",
            (session_token,),
        )
        user = cursor.fetchone()
        cursor.close()
        if user:
            return templates.TemplateResponse("index.html", {"request": request})
    return RedirectResponse(url="/login", status_code=303)

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/register")
def register(username: str = Form(...), password: str = Form(...), conn=Depends(get_db)):
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
            (username, hashed.decode()),
        )
        conn.commit()
    except mysql.connector.IntegrityError:
        cursor.close()
        raise HTTPException(status_code=409, detail="Username already exists")
    user_id = cursor.lastrowid
    session_token = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO sessions (user_id, session_token) VALUES (%s, %s)",
        (user_id, session_token),
    )
    conn.commit()
    cursor.close()
    response = RedirectResponse(url="/login", status_code=303)
    response.set_cookie(key="session_token", value=session_token, httponly=True)
    return response

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/login")
def login(username: str = Form(...), password: str = Form(...), conn=Depends(get_db)):
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = cursor.fetchone()

    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        cursor.close()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    session_token = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO sessions (user_id, session_token) VALUES (%s, %s)",
        (user["id"], session_token),
    )

    conn.commit()
    cursor.close()

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(key="session_token", value=session_token, httponly=True)
    return response


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str | None = None, conn=Depends(get_db)):
    # Accept token from query param (?token=...) or cookie fallback
    session_token = token or websocket.cookies.get("session_token")
    if not session_token:
        await websocket.close(code=1008)
        return
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT users.id FROM sessions JOIN users ON sessions.user_id = users.id "
        "WHERE sessions.session_token = %s", (session_token,)
    )
    user = cursor.fetchone()
    cursor.close()
    if not user:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    ws_clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()   # keep connection alive
    except WebSocketDisconnect:
        ws_clients.remove(websocket)

@app.post("/api/command")
def send_command(body: CommandIn, current_user=Depends(get_current_user)):
    if body.command not in VALID_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Unknown command: {body.command}")
    mqtt_client.publish(MQTT_COMMAND_TOPIC, json.dumps({"command": body.command}))
    print(f"[CMD] → {body.command}")
    return {"status": "ok", "command": body.command}

@app.post("/api/readings")
def create_reading(body: ReadingIn, conn=Depends(get_db), current_user=Depends(get_current_user)):
    cursor = conn.cursor()
    cursor.execute(
        "INSERT IGNORE INTO devices (mac_address) VALUES (%s)",
        (body.mac_address,)
    )
    cursor.execute(
        """INSERT INTO readings
           (mac_address, thermistor_temp, prediction, confidence, pixels)
           VALUES (%s, %s, %s, %s, %s)""",
        (body.mac_address, body.thermistor,
         body.prediction.upper(), body.confidence,
         json.dumps(body.pixels))
    )
    conn.commit()
    new_id = cursor.lastrowid
    cursor.close()
    return {"id": new_id}


@app.get("/api/readings")
def get_readings(device_mac: Optional[str] = None, conn=Depends(get_db), current_user=Depends(get_current_user)):
    cursor = conn.cursor(dictionary=True)
    if device_mac:
        cursor.execute(
            "SELECT * FROM readings WHERE mac_address = %s ORDER BY id DESC",
            (device_mac,)
        )
    else:
        cursor.execute("SELECT * FROM readings ORDER BY id DESC")
    rows = cursor.fetchall()
    cursor.close()
    for row in rows:
        row["pixels"]  = json.loads(row["pixels"])
        row["created_at"] = str(row["created_at"])
    return rows

@app.delete("/api/readings/{reading_id}")
def delete_reading(reading_id: int, conn=Depends(get_db), current_user=Depends(get_current_user)):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM readings WHERE id = %s", (reading_id,))
    conn.commit()
    deleted = cursor.rowcount
    cursor.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Reading not found")
    return {"status": "deleted", "id": reading_id}

@app.get("/api/devices")
def get_devices(conn=Depends(get_db), current_user=Depends(get_current_user)):
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM devices ORDER BY id")
    devices = cursor.fetchall()
    cursor.close()
    for d in devices:
        d["created_at"] = str(d["created_at"])
    return devices


## TA 9
@app.post("/api/register")
def create_user(body: UserIn, conn=Depends(get_db)):
    hashed = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt())
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
            (body.username, hashed.decode()),
        )
        conn.commit()

    except mysql.connector.IntegrityError:
        cursor.close()
        raise HTTPException(status_code=409, detail="Username already exists")
    user_id = cursor.lastrowid
    session_token = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO sessions (user_id, session_token) VALUES (%s, %s)",
        (user_id, session_token),
    )
    conn.commit()
    cursor.close()

    response = JSONResponse(
        status_code=201,
        content={"message": "User created", "username": body.username})
    response.set_cookie(key="session_token", value=session_token, httponly=True)
    return response

@app.post("/api/login")
def login_user(body: UserIn, conn=Depends(get_db)):
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE username = %s", (body.username,))
    user = cursor.fetchone()

    if not user or not bcrypt.checkpw(body.password.encode(), user["password_hash"].encode()):
        cursor.close()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    session_token = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO sessions (user_id, session_token) VALUES (%s, %s)",
        (user["id"], session_token),
    )

    conn.commit()
    cursor.close()

    response = JSONResponse(
        status_code=200,
        content={"message": "Login successful", "username": user["username"]})
    response.set_cookie(key="session_token", value=session_token, httponly=True)
    return response


@app.post("/api/logout")
def logout(session_token: str | None = Cookie(None), conn=Depends(get_db)):
    if session_token:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE session_token = %s", (session_token,))
        conn.commit()
        cursor.close()
    response = JSONResponse(content={"message": "Logged out"})
    response.delete_cookie("session_token")
    return response



if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)