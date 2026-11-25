# NEXORA
### Smart City Emergency Communication and Network Management System

Nexora connects citizens, IoT sensors and emergency departments (Fire, Hospital, Police, Traffic) on one secure city network, coordinated by a Command Center.

## Features
- **Citizen reporting** through the website or an embedded Google Form, with a tracking ID emailed to the reporter
- **Live tracking** with the tracking ID (Reported → Assigned → On the way → Resolved)
- **IoT sensor alerts** over MQTT (Mosquitto)
- **Automatic routing**: fire/smoke → Fire, medical → Hospital, accident → Police + Hospital, traffic → Traffic
- **Department dashboards** with a strict Accept → Resolve flow, activity log and live alerts
- **Command Center dashboard** (view only): system status, response times, resolution progress, reports by source
- **Staff profiles**: change display name and password (PBKDF2 hashed)

## Network (Cisco Packet Tracer / GNS3)
| VLAN | Use | Subnet |
|---|---|---|
| 10 | Command Center | 10.10.10.0/24 |
| 20 | Police | 10.10.20.0/24 |
| 30 | Hospital | 10.10.30.0/24 |
| 40 | Fire | 10.10.40.0/24 |
| 50 | Traffic | 10.10.50.0/24 |
| 60 | IoT sensors | 10.10.60.0/24 |
| 70 | Nexora server (10.10.70.10) | 10.10.70.0/24 |

OSPF, inter-VLAN routing on the core switch, ACLs that let departments reach only the server (port 8000) and sensors only MQTT (port 1883), and QoS marking for emergency MQTT traffic. Device configs are in `configs/`.

## Software
Python FastAPI · SQLite · paho-mqtt · Mosquitto · HTML / CSS / JavaScript · Google Forms + Apps Script

## How to run (Windows)
```
pip install fastapi uvicorn paho-mqtt
cd backend
py create_db.py
```
Terminal 1:
```
mosquitto -c C:\NEXORA\nexora.conf -v
```
Terminal 2:
```
cd C:\NEXORA\backend
py -m uvicorn main:app --host 0.0.0.0 --port 8000
```
Open **http://localhost:8000**

Test a sensor alert:
```
mosquitto_pub -h 127.0.0.1 -t nexora/alerts/fire -m "{\"type\":\"fire\",\"location\":\"Sector 4\",\"severity\":\"high\"}"
```
