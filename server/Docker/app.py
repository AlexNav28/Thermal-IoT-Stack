import os
import json
import time
import asyncio
import uvicorn

import mysql.connector
import paho.mqtt.client as mqtt

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from dotenv import load_dotenv


load_dotenv()

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_BROKER_PORT = 1883
MQTT_TOPIC = os.getenv("MQTT_TOPIC")
MQTT_COMMAND_TOPIC = f"{MQTT_TOPIC}/command" 
MQTT_MESSAGE_TOPIC = f"{MQTT_TOPIC}/thermal_data"




# Pydantic validation models
class ReadingIn(BaseModel):
    mac_address: str
    pixels: list[float] = Field(..., description="List of 64 pixel values")
    thermistor:  float                  
    prediction:  str                    
    confidence:  float
    
class CommandIn(BaseModel):
    command: str


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

# Websocket 

ws_clients: list[WebSocket] = []
latest_id:  int | None = None
latest_mac: str | None = None

async def broadcast_frames():
    global latest_id, latest_mac
    while True:
        if latest_id is not None and ws_clients:

            # Only send MAC + id — not the full row
            notification = {
                "event":       "new_reading",
                "mac_address": latest_mac,
                "id":          latest_id
            }

            latest_id  = None
            latest_mac = None

            # Notify all connected browsers
            for client in list(ws_clients):
                try:
                    await client.send_json(notification)
                except Exception:
                    ws_clients.remove(client)

        await asyncio.sleep(0.1) 


# MQTT implementation

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker with result code {rc}")
    client.subscribe(MQTT_MESSAGE_TOPIC)
    print(f"Subscribed to topic: {MQTT_MESSAGE_TOPIC}")


def on_message(client, userdata, msg):
    global latest_id, latest_mac
    data = msg.payload.decode()
    print(f"[Received MQTT message] {data}")
    
    try:
        json_data = json.loads(data)
        reading = ReadingIn(**json_data)

    except Exception as e:
        print(f"[MQTT] Parse error: {e}")
        return
        
    try:
        conn   = mysql.connector.connect(
            host=os.environ["DB_HOST"], user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"], database=os.environ["DB_NAME"]
        )
        cursor = conn.cursor()

        cursor.execute (
            "INSERT IGNORE INTO devices (mac_address) VALUES (%s)",
            (reading.mac_address,)
        )

        cursor.execute(
            """INSERT INTO readings
               (mac_address, thermistor_temp, prediction, confidence, pixels)
               VALUES (%s, %s, %s, %s, %s)""",
            (reading.mac_address, reading.thermistor,
             reading.prediction.upper(), reading.confidence,
             json.dumps(reading.pixels))
        )

        conn.commit()
        new_id = cursor.lastrowid
        cursor.close()
        conn.close()
        print(f"[DB] Saved reading id={new_id}")

    except Exception as e:
        print(f"[DB] Insert error: {e}")
        return
    
    # Store prev iformation
    latest_id = new_id
    latest_mac = reading.mac_address
         

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
templates = Jinja2Templates(directory="templates")

### SEEMS the End of how to construct the backend of the API ####
# Need to fix and ask some tasks of how the verificaiton of data is suppose to happen 



@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()   # keep connection alive
    except WebSocketDisconnect:
        ws_clients.remove(websocket)

VALID_COMMANDS = {"get_one", "start_continuous", "stop"}

@app.post("/api/command")
def send_command(body: CommandIn):
    if body.command not in VALID_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Unknown command: {body.command}")
    if mqtt_client:
        mqtt_client.publish(MQTT_COMMAND_TOPIC, json.dumps({"command": body.command}))
        print(f"[CMD] → {body.command}")
    return {"status": "ok", "command": body.command}

@app.post("/api/readings")
def create_reading(body: ReadingIn, conn=Depends(get_db)):
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
def get_readings(device_mac: Optional[str] = None, conn=Depends(get_db)):
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
        row["pixels"]     = json.loads(row["pixels"])
        row["created_at"] = str(row["created_at"])
    return rows

@app.delete("/api/readings/{reading_id}")
def delete_reading(reading_id: int, conn=Depends(get_db)):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM readings WHERE id = %s", (reading_id,))
    conn.commit()
    deleted = cursor.rowcount
    cursor.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Reading not found")
    return {"status": "deleted", "id": reading_id}

@app.get("/api/devices")
def get_devices(conn=Depends(get_db)):
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM devices ORDER BY id")
    devices = cursor.fetchall()
    cursor.close()
    for d in devices:
        d["created_at"] = str(d["created_at"])
    return devices

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
 ### MAybe ###