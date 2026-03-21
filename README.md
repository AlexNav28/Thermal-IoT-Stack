# IoT Thermal Sensor Dashboard

A full-stack IoT application that streams real-time thermal imaging data from an AMG8833 infrared sensor connected to an ESP32 microcontroller. The system features live heatmap visualization, person detection via an on-device neural network, and persistent data storage — all orchestrated with Docker.

---

## Architecture

```
┌──────────────┐       MQTT        ┌──────────────────┐       MySQL       ┌────────────┐
│   ESP32 +    │ ───────────────▶  │   Python API     │ ───────────────▶  │  Database   │
│  AMG8833     │                   │   (FastAPI)      │                   │  Container  │
│  Sensor      │                   │   Port 8000      │                   │            │
└──────────────┘                   └──────┬───────────┘                   └────────────┘
                                          │
                                     WebSocket
                                          │
                                   ┌──────▼───────────┐
                                   │   Browser UI      │
                                   │   Live Heatmap    │
                                   └──────────────────┘
```

## Features

- **Real-time thermal heatmap** — 8×8 pixel grid streamed from the AMG8833 sensor and rendered on an HTML canvas
- **Person detection** — TinyML neural network running directly on the ESP32 classifies frames as `PRESENT` or `EMPTY` with a confidence score
- **Ambient temperature** — Thermistor readings displayed alongside the heatmap
- **Device management** — Multiple ESP32 devices identified and tracked by MAC address
- **CRUD API** — Full REST interface for readings and device records
- **WebSocket streaming** — Low-latency push updates from server to browser
- **Dockerized deployment** — Webserver and database run as isolated containers via Docker Compose

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Microcontroller | ESP32 (PlatformIO) |
| Sensor | AMG8833 8×8 Thermal Camera |
| ML Inference | TensorFlow Lite Micro (on-device) |
| Messaging | MQTT (EMQX public broker) |
| Backend | Python / FastAPI |
| Real-time | WebSockets |
| Database | MySQL |
| Frontend | HTML, CSS, JavaScript, Canvas API |
| Infrastructure | Docker, Docker Compose |

## Project Structure

```
.
├── esp32/                  # PlatformIO firmware project
│   ├── src/
│   │   └── main.cpp        # Sensor reading, MQTT publish, TFLite inference
│   └── platformio.ini
└── server/
    ├── docker-compose.yml   # Webserver + MySQL containers
    └── webserver/
        ├── app.py           # FastAPI application
        ├── static/          # Frontend assets
        └── ...
```


### Configure environment variables

Creation a `.env` file inside the `server/` directory:

```env
DB_PASSWORD=your_password
DB_NAME= Alex
MQTT_BROKER=broker.emqx.io
MQTT_TOPIC= Mex123
```

## API Reference

### Readings

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/readings` | Store a new sensor reading |
| `GET` | `/api/readings` | Retrieve all readings |
| `GET` | `/api/readings?device_mac=AA:BB:CC:DD:EE:FF` | Filter readings by device |
| `DELETE` | `/api/readings/{id}` | Delete a reading by ID |

### Devices

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/devices` | List all registered ESP32 devices |

### Commands

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/command` | Send a command to the ESP32 |

**Command options:** `get_one`, `start_continuous`, `stop`

### WebSocket

Connect to `ws://localhost:8000/ws` for real-time sensor data streaming.

## Demo

https://youtu.be/9tlgZohMH9o

