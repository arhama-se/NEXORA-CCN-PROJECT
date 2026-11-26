# NEXORA
### Smart City Emergency Communication and Network Management System

Nexora connects citizens, IoT sensors and emergency departments (Fire, Hospital, Police, Traffic) on one secure city network, coordinated by a Command Center.

## Features
- **Citizen reporting** through the website or an embedded Google Form, with a tracking ID emailed to the reporter
- **Live tracking** with the tracking ID (Reported → Assigned → On the way → Resolved)
- **IoT sensor alerts** over MQTT (Mosquitto)
- **Automatic routing**: fire/smoke → Fire, medical → Hospital, accident → Police + Hospital, traffic → Traffic
- **Department dashboards** with a strict Accept → Resolve flow, activity log and live alerts
- **Deadlines and escalation**: every case must be accepted and resolved within a time limit; missed deadlines are escalated to the Command Center, and cases nobody accepts expire (they stay on record and the citizen is advised to call Rescue 1122 / Police 15)
- **Command Center dashboard** (view only): system status, resolution progress, open cases per department, reports by source
- **Monthly report**: cases registered, resolved, resolution rate, cases by department and type, resolved by which team, downloadable as PDF
- **Staff profiles**: change display name and password (PBKDF2 hashed)

### Deadlines
| Emergency | Accept within | Resolve within |
|---|---|---|
| Fire | 3 min | 60 min |
| Accident | 3 min | 45 min |
| Medical | 2 min | 30 min |
| Smoke | 5 min | 60 min |
| Traffic | 10 min | 90 min |

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
Install once:
```
py -m pip install fastapi uvicorn paho-mqtt
cd backend
py create_db.py
```
Optional Google Form sync: put your Apps Script web app link in `backend/gform_url.txt` (this file is private and not on GitHub).

Terminal 1 (MQTT broker):
```
cd "C:\Program Files\mosquitto"
mosquitto -c C:\NEXORA\nexora.conf -v
```
Terminal 2 (backend + website):
```
cd C:\NEXORA\backend
py -m uvicorn main:app --host 0.0.0.0 --port 8000
```
Open **http://localhost:8000** (or **http://10.10.70.10:8000** from the city network).

Test a sensor alert (from the Mosquitto folder):
```
mosquitto_pub -h 127.0.0.1 -t nexora/alerts/fire -m "{\"type\":\"fire\",\"location\":\"Sector 4\",\"severity\":\"high\"}"
```

## Team
- **Arhama**: network design (Cisco routing, VLANs, OSPF, ACLs, QoS, GNS3)
- **Aiman**: software (MQTT broker, Python backend, database, website and dashboards)

> Student project for the CCN lab, Bahria University Islamabad. A simulation, not connected to real emergency services.
