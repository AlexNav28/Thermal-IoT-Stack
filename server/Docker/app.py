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


load_dotenv("../esp32/.env")

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



@app.post("/read")
def get_single_reading():
    """
    Request a single thermal reading from the ESP32.
    
    Sends "read" command via MQTT and waits for the ESP32 to respond
    with thermal data in JSON format.
    """

    global thermal_data, latest_thermal_data
    # TODO: Send "read" command and return thermal_data (handle None case)
    mqtt_client.publish(MQTT_COMMAND_TOPIC, "read")
    time.sleep(0.5)
    if thermal_data:
        latest_thermal_data = thermal_data
        return JSONResponse(
            status_code=200,
            content=json.loads(latest_thermal_data),
        )
    else:
        return JSONResponse(
            status_code=504,
            content={"error": "Waiting for ESP32 response", "message": "ESP32 has not responded yet! Please wait a moment and try again."}
        )


@app.get("/pixel")
def get_pixel_value(index: int):
    """
    Get a specific pixel temperature value from a fresh reading.
    
    Query parameter:
    - index (0-63): Pixel index in the 8x8 thermal array
    
    """
    global thermal_data, latest_thermal_data
    # TODO: Validate index, check thermal_data exists, and return pixel value
    if (index > 63) or (index < 0): 
        return JSONResponse(
                status_code=400,
                content={"error": f"Index must be on range (0-63), input = {str(index)}"}
            )
    mqtt_client.publish(MQTT_COMMAND_TOPIC, "read")
    time.sleep(0.5)
    if thermal_data:
        latest_thermal_data = thermal_data
        data = json.loads(latest_thermal_data)
        pixels = data["pixels"]

        return {"index": index, "temperature": float(pixels[index])}
    else:
        return JSONResponse(
            status_code=504,
            content={"error": "Waiting for ESP32 response", "message": "ESP32 has not responded yet! Please wait a moment and try again."}
        )
        
    

@app.get("/thermal_graph")
def get_thermal_graph():
    """
    Get a thermal heatmap visualization with fresh thermal data.
    
    Triggers a new reading from the ESP32 and returns a PNG image 
    showing the 8x8 thermal grid as a heatmap.
    Refer to README.md for more details. 
    Use generate_temp_plot from visualize_temp.py to generate the thermal graph.
    Remember to return the StreamingResponse object.
    Remember to use the global variable `thermal_data` to generate the thermal graph.
    The data should is already validated correctly against the Pydantic model.
    """
    # TODO (BONUS): Trigger reading and return PNG thermal heatmap

@app.get("/")
def root():
    """Health check endpoint"""
    return {
        "status": "webserver is running",
        "service": "AMG8833 Thermal Camera Server",
        "mqtt_broker": MQTT_BROKER,
        "mqtt_topic": MQTT_TOPIC,
        "endpoints": {
            "POST /read": "Get a single thermal reading (64 pixels)",
            "GET /pixel?index=N": "Get specific pixel temperature by index (0-63)",
            "GET /thermal_graph": "Get thermal heatmap image (PNG)",
        }
    }

if __name__ == "__main__":
    uvicorn.run("temperature_webserver:app", host="127.0.0.1", port=8000, reload=True)