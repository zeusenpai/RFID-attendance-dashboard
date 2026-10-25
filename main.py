import os
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from database import db, parse_timestamp
from export_service import create_excel_report

app = FastAPI(
    title="RFID Attendance & Access System",
    description="IoT RFID Attendance System with Real-Time Dashboard, JSON DB & Excel Export",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Secret Key for ESP32 Hardware Authentications
API_SECRET = "esp32_local_test_99"
security = HTTPBearer(auto_error=False)


# Pydantic Schemas
class ScanEvent(BaseModel):
    uid: str
    timestamp: Optional[str] = ""
    device_id: Optional[str] = "esp32_c3_gate1"


class UserRegistration(BaseModel):
    name: str
    uid: str
    role: Optional[str] = "Staff"
    department: Optional[str] = "General"
    custom_id: Optional[str] = None


class UserUpdate(BaseModel):
    name: str
    role: str
    department: str


# ================= API ENDPOINTS =================

@app.post("/api/attendance")
def log_attendance(event: ScanEvent, credentials: Optional[HTTPAuthorizationCredentials] = Security(security)):
    """
    Endpoint called by ESP32 RFID Reader upon scanning an RFID card.
    Authenticates bearer token, logs IN/OUT punch, updates JSON and SQLite database.
    """
    # Verify auth if provided, otherwise allow local dev if needed
    if credentials:
        if credentials.credentials != API_SECRET:
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")
    
    # Process scan in database
    result = db.record_scan(
        uid=event.uid,
        raw_timestamp=event.timestamp or "",
        device_id=event.device_id or "esp32_c3_gate1"
    )
    
    print(f"[SCAN] {result['message']} | UID: {result['uid']} | Device: {result['device_id']}")
    return {
        "status": "success",
        "action": result["action"],
        "uid": result["uid"],
        "user_id": result["user_id"],
        "name": result["name"],
        "is_registered": result["is_registered"],
        "timestamp": result["timestamp"],
        "time": result["time"],
        "message": result["message"]
    }


@app.get("/api/logs")
def get_logs(date: Optional[str] = Query(None, description="Filter date YYYY-MM-DD or ALL"), limit: int = 150):
    """Returns punch logs, optionally filtered by date."""
    return db.get_logs(date=date, limit=limit)


@app.get("/api/attendance/summary")
def get_daily_summary(date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")):
    """Returns daily attendance summary per user for the selected date."""
    target_date = date if date else datetime.now().strftime("%Y-%m-%d")
    return db.get_daily_summary(target_date)


@app.get("/api/stats")
def get_statistics(date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")):
    """Returns overview statistics for dashboard cards."""
    return db.get_stats(date)


@app.get("/api/users")
def get_users():
    """Returns list of all registered users."""
    return db.get_all_users()


@app.get("/api/users/next-id")
def get_next_user_id():
    """Returns the next suggested auto-generated unique User ID."""
    return {"next_id": db.generate_unique_user_id()}


@app.post("/api/users")
def register_user(user: UserRegistration):
    """Registers a new RFID card and user with automatic or custom User ID."""
    try:
        new_user = db.register_user(
            name=user.name,
            uid=user.uid,
            role=user.role or "Staff",
            department=user.department or "General",
            custom_id=user.custom_id
        )
        return {"status": "success", "user": new_user, "message": f"User {new_user['name']} registered with ID {new_user['id']}"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/users/{user_id}")
def update_user(user_id: str, user: UserUpdate):
    """Updates user details."""
    updated = db.update_user(user_id=user_id, name=user.name, role=user.role, department=user.department)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "success", "user": updated}


@app.delete("/api/users/{user_id}")
def delete_user(user_id: str):
    """Deletes user registration."""
    db.delete_user(user_id)
    return {"status": "success", "message": f"User {user_id} deleted"}


@app.get("/api/unregistered-cards")
def get_unregistered_cards():
    """Returns recently scanned cards that are not yet assigned to any registered user."""
    return db.get_unregistered_cards()


@app.get("/api/dates")
def get_available_dates():
    """Returns list of distinct dates present in attendance logs."""
    return db.get_available_dates()


@app.get("/api/database-json")
def get_database_json():
    """Returns raw JSON database for dashboard inspector."""
    return db._read_json()


@app.get("/api/export/excel")
def export_excel(date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format or ALL")):
    """
    Generates and streams a formatted Excel spreadsheet (.xlsx)
    of attendance and detailed punch logs for the requested date.
    """
    target_date = date if date else datetime.now().strftime("%Y-%m-%d")
    excel_buffer = create_excel_report(target_date)
    
    filename = f"Attendance_Report_{target_date}.xlsx"
    headers = {
        "Content-Disposition": f"attachment; filename=\"{filename}\"",
        "Access-Control-Expose-Headers": "Content-Disposition"
    }
    return StreamingResponse(
        excel_buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers
    )


# ================= DASHBOARD UI =================

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RFID Attendance & Access Suite</title>
    <!-- Google Fonts: Plus Jakarta Sans -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    fontFamily: {
                        sans: ['"Plus Jakarta Sans"', 'sans-serif'],
                        mono: ['"JetBrains Mono"', 'monospace'],
                    },
                    colors: {
                        slate: {
                            850: '#0f172a',
                            900: '#0b1120',
                            950: '#060913',
                        },
                        brand: {
                            50: '#ecfdf5',
                            500: '#10b981',
                            600: '#059669',
                        }
                    }
                }
            }
        }
    </script>
    <style>
        /* Glassmorphism & Custom Scrollbar */
        ::-webkit-scrollbar { width: 8px; height: 8px; }
        ::-webkit-scrollbar-track { background: #0b1120; }
        ::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #334155; }
        
        .glass-panel {
            background: rgba(15, 23, 42, 0.75);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.08);
        }
        
        .pulse-beacon {
            box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
            animation: pulse-ring 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
        }
        @keyframes pulse-ring {
            0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
            70% { box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }
            100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }

        .tab-btn.active {
            background: #1e293b;
            color: #38bdf8;
            border-bottom: 2px solid #38bdf8;
        }
    </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen flex flex-col font-sans antialiased selection:bg-emerald-500 selection:text-white">

    <!-- Top Navigation Header -->
    <header class="border-b border-slate-800/80 bg-slate-900/90 sticky top-0 z-40 backdrop-blur-md">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
            <!-- Brand & Status -->
            <div class="flex items-center space-x-4">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-emerald-500 to-cyan-500 flex items-center justify-center shadow-lg shadow-emerald-500/20">
                    <svg class="w-6 h-6 text-slate-950" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"></path>
                    </svg>
                </div>
                <div>
                    <div class="flex items-center space-x-2">
                        <h1 class="text-lg font-bold tracking-tight text-white">RFID Attendance System</h1>
                        <span class="text-[10px] font-semibold uppercase tracking-wider bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded-full">v2.0 PRO</span>
                    </div>
                    <p class="text-xs text-slate-400">IoT ESP32 Smart Access & Punch Management</p>
                </div>
            </div>

            <!-- Live Status & Actions -->
            <div class="flex items-center space-x-3">
                <div class="hidden md:flex items-center space-x-2 text-xs bg-slate-800/80 border border-slate-700/60 px-3 py-1.5 rounded-lg text-slate-300">
                    <span class="w-2 h-2 rounded-full bg-emerald-400 pulse-beacon"></span>
                    <span id="live-clock" class="font-mono font-medium text-slate-200">--:--:--</span>
                </div>
                
                <button onclick="openRegisterModal()" class="flex items-center space-x-2 bg-gradient-to-r from-emerald-500 to-teal-500 hover:from-emerald-400 hover:to-teal-400 text-slate-950 font-semibold text-xs sm:text-sm px-3.5 py-2 rounded-lg shadow-lg shadow-emerald-500/20 transition-all transform active:scale-95">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z"></path></svg>
                    <span>Register Card</span>
                </button>
            </div>
        </div>
    </header>

    <!-- Main Container -->
    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 w-full flex-1 space-y-6">

        <!-- Unregistered Card Alert Banner (Dynamically shown) -->
        <div id="unregistered-banner" class="hidden rounded-xl bg-amber-500/10 border border-amber-500/30 p-4 transition-all">
            <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
                <div class="flex items-center space-x-3">
                    <div class="p-2 rounded-lg bg-amber-500/20 text-amber-400">
                        <svg class="w-5 h-5 animate-bounce" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path></svg>
                    </div>
                    <div>
                        <h4 class="text-sm font-semibold text-amber-300">New Unregistered RFID Card Scanned!</h4>
                        <p class="text-xs text-slate-300 mt-0.5">Card UID: <span id="banner-card-uid" class="font-mono font-bold text-amber-200 bg-amber-950/60 px-1.5 py-0.5 rounded border border-amber-500/40"></span> &bull; Scanned at <span id="banner-card-time" class="text-slate-300"></span></p>
                    </div>
                </div>
                <div class="flex items-center space-x-2">
                    <button id="banner-quick-register-btn" class="bg-amber-500 hover:bg-amber-400 text-slate-950 text-xs font-bold px-3 py-1.5 rounded-lg transition shadow">
                        Quick Register This Card
                    </button>
                    <button onclick="dismissUnregisteredBanner()" class="text-slate-400 hover:text-slate-200 text-xs px-2 py-1.5">
                        Dismiss
                    </button>
                </div>
            </div>
        </div>

        <!-- KPI Metrics Grid -->
        <div class="grid grid-cols-2 md:grid-cols-5 gap-4">
            <!-- Card 1 -->
            <div class="glass-panel rounded-xl p-4 border border-slate-800 relative overflow-hidden">
                <div class="flex justify-between items-start">
                    <div>
                        <p class="text-xs font-medium text-slate-400">Registered Users</p>
                        <h3 id="stat-registered" class="text-2xl font-bold text-white mt-1">--</h3>
                    </div>
                    <div class="p-2 rounded-lg bg-indigo-500/10 text-indigo-400">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"></path></svg>
                    </div>
                </div>
                <div class="mt-2 text-[11px] text-slate-400 flex items-center">
                    <span>Card IDs with unique badges</span>
                </div>
            </div>

            <!-- Card 2 -->
            <div class="glass-panel rounded-xl p-4 border border-slate-800 relative overflow-hidden">
                <div class="flex justify-between items-start">
                    <div>
                        <p class="text-xs font-medium text-slate-400">Active Attendees</p>
                        <h3 id="stat-attendees" class="text-2xl font-bold text-emerald-400 mt-1">--</h3>
                    </div>
                    <div class="p-2 rounded-lg bg-emerald-500/10 text-emerald-400">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                    </div>
                </div>
                <div class="mt-2 text-[11px] text-slate-400">
                    <span id="stat-target-date-label">For selected date</span>
                </div>
            </div>

            <!-- Card 3 -->
            <div class="glass-panel rounded-xl p-4 border border-slate-800 relative overflow-hidden">
                <div class="flex justify-between items-start">
                    <div>
                        <p class="text-xs font-medium text-slate-400">Currently Inside</p>
                        <h3 id="stat-inside" class="text-2xl font-bold text-teal-400 mt-1">--</h3>
                    </div>
                    <div class="p-2 rounded-lg bg-teal-500/10 text-teal-400">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 16l-4-4m0 0l4-4m-4 4h14m-5 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h7a3 3 0 013 3v1"></path></svg>
                    </div>
                </div>
                <div class="mt-2 text-[11px] text-teal-300 flex items-center space-x-1">
                    <span class="w-1.5 h-1.5 rounded-full bg-teal-400"></span>
                    <span>Punched IN status</span>
                </div>
            </div>

            <!-- Card 4 -->
            <div class="glass-panel rounded-xl p-4 border border-slate-800 relative overflow-hidden">
                <div class="flex justify-between items-start">
                    <div>
                        <p class="text-xs font-medium text-slate-400">Punched OUT</p>
                        <h3 id="stat-outside" class="text-2xl font-bold text-amber-400 mt-1">--</h3>
                    </div>
                    <div class="p-2 rounded-lg bg-amber-500/10 text-amber-400">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"></path></svg>
                    </div>
                </div>
                <div class="mt-2 text-[11px] text-slate-400">
                    <span>Checked out of facility</span>
                </div>
            </div>

            <!-- Card 5 -->
            <div class="col-span-2 md:col-span-1 glass-panel rounded-xl p-4 border border-slate-800 relative overflow-hidden">
                <div class="flex justify-between items-start">
                    <div>
                        <p class="text-xs font-medium text-slate-400">Total Scans</p>
                        <h3 id="stat-scans" class="text-2xl font-bold text-cyan-400 mt-1">--</h3>
                    </div>
                    <div class="p-2 rounded-lg bg-cyan-500/10 text-cyan-400">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                    </div>
                </div>
                <div class="mt-2 text-[11px] text-cyan-300 font-mono truncate" id="latest-scan-label">
                    Waiting for card...
                </div>
            </div>
        </div>

        <!-- Filter & Action Control Toolbar -->
        <div class="glass-panel rounded-xl p-4 border border-slate-800 flex flex-wrap items-center justify-between gap-4">
            <!-- Date Filter Section -->
            <div class="flex flex-wrap items-center gap-2">
                <span class="text-xs font-medium text-slate-400 flex items-center space-x-1.5">
                    <svg class="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
                    <span>Attendance Date:</span>
                </span>
                
                <div class="flex items-center space-x-1 bg-slate-900 border border-slate-700/80 rounded-lg p-1">
                    <button onclick="setDateFilter('TODAY')" id="quick-today" class="text-xs px-2.5 py-1 rounded-md font-medium text-emerald-400 bg-slate-800 transition">Today</button>
                    <button onclick="setDateFilter('YESTERDAY')" id="quick-yesterday" class="text-xs px-2.5 py-1 rounded-md font-medium text-slate-400 hover:text-slate-200 transition">Yesterday</button>
                    <button onclick="setDateFilter('ALL')" id="quick-all" class="text-xs px-2.5 py-1 rounded-md font-medium text-slate-400 hover:text-slate-200 transition">All Time</button>
                </div>

                <!-- Custom Date Input -->
                <input type="date" id="date-picker" onchange="onCustomDateChange(this.value)" class="bg-slate-900 text-slate-200 border border-slate-700/80 rounded-lg px-3 py-1.5 text-xs focus:ring-2 focus:ring-emerald-500 focus:outline-none">
            </div>

            <!-- Actions: Excel Export & Refresh -->
            <div class="flex items-center space-x-3">
                <button onclick="exportToExcel()" class="flex items-center space-x-2 bg-slate-800 hover:bg-slate-700 text-emerald-400 hover:text-emerald-300 border border-emerald-500/30 hover:border-emerald-500/60 font-semibold text-xs px-3.5 py-2 rounded-lg transition-all shadow-sm">
                    <svg class="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                    </svg>
                    <span>Export Excel (.xlsx)</span>
                </button>

                <button onclick="refreshAll()" class="p-2 bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-200 border border-slate-700 rounded-lg transition" title="Refresh Now">
                    <svg id="refresh-icon" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                </button>
            </div>
        </div>

        <!-- Navigation Tabs -->
        <div class="flex items-center space-x-2 border-b border-slate-800">
            <button onclick="switchTab('summary')" id="tab-btn-summary" class="tab-btn active px-4 py-2.5 text-xs sm:text-sm font-semibold rounded-t-lg transition flex items-center space-x-2">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"></path></svg>
                <span>Daily Attendance Summary</span>
            </button>
            <button onclick="switchTab('logs')" id="tab-btn-logs" class="tab-btn text-slate-400 hover:text-slate-200 px-4 py-2.5 text-xs sm:text-sm font-semibold rounded-t-lg transition flex items-center space-x-2">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
                <span>Live Punch Stream</span>
            </button>
            <button onclick="switchTab('users')" id="tab-btn-users" class="tab-btn text-slate-400 hover:text-slate-200 px-4 py-2.5 text-xs sm:text-sm font-semibold rounded-t-lg transition flex items-center space-x-2">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>
                <span>Registered Users (<span id="tab-users-count">0</span>)</span>
            </button>
            <button onclick="switchTab('json')" id="tab-btn-json" class="tab-btn text-slate-400 hover:text-slate-200 px-4 py-2.5 text-xs sm:text-sm font-semibold rounded-t-lg transition flex items-center space-x-2">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16m-7 6h7"></path></svg>
                <span>JSON Database</span>
            </button>
        </div>

        <!-- TAB 1: Daily Attendance Summary -->
        <div id="tab-content-summary" class="glass-panel rounded-xl overflow-hidden border border-slate-800 shadow-xl">
            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="bg-slate-900/80 text-slate-400 text-xs uppercase tracking-wider border-b border-slate-800">
                            <th class="p-4 font-semibold">User ID</th>
                            <th class="p-4 font-semibold">Name & Role</th>
                            <th class="p-4 font-semibold">Card UID</th>
                            <th class="p-4 font-semibold">Punch IN</th>
                            <th class="p-4 font-semibold">Punch OUT</th>
                            <th class="p-4 font-semibold">Work Duration</th>
                            <th class="p-4 font-semibold">Status</th>
                            <th class="p-4 font-semibold text-center">Punches</th>
                        </tr>
                    </thead>
                    <tbody id="summary-table-body" class="divide-y divide-slate-800/60 text-xs sm:text-sm">
                        <tr>
                            <td colspan="8" class="p-8 text-center text-slate-500">Loading daily attendance records...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- TAB 2: Live Punch Stream -->
        <div id="tab-content-logs" class="hidden glass-panel rounded-xl overflow-hidden border border-slate-800 shadow-xl">
            <div class="p-3 bg-slate-900/60 border-b border-slate-800 flex justify-between items-center text-xs text-slate-400">
                <span>Showing recent punch events (Auto-refreshed every 2.5s)</span>
                <span class="font-mono" id="log-count-indicator">0 logs</span>
            </div>
            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="bg-slate-900/80 text-slate-400 text-xs uppercase tracking-wider border-b border-slate-800">
                            <th class="p-4 font-semibold">Log ID</th>
                            <th class="p-4 font-semibold">Action</th>
                            <th class="p-4 font-semibold">User ID</th>
                            <th class="p-4 font-semibold">Name</th>
                            <th class="p-4 font-semibold">Card UID</th>
                            <th class="p-4 font-semibold">Timestamp</th>
                            <th class="p-4 font-semibold">Device</th>
                        </tr>
                    </thead>
                    <tbody id="logs-table-body" class="divide-y divide-slate-800/60 text-xs sm:text-sm font-sans">
                        <tr>
                            <td colspan="7" class="p-8 text-center text-slate-500">Loading live punch logs...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- TAB 3: Registered Users -->
        <div id="tab-content-users" class="hidden space-y-4">
            <div class="flex justify-between items-center">
                <p class="text-xs text-slate-400">Manage all registered RFID cards and users. Each user has an automatic unique ID.</p>
                <button onclick="openRegisterModal()" class="bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold px-3 py-1.5 rounded-lg transition">
                    + Add New User
                </button>
            </div>
            <div class="glass-panel rounded-xl overflow-hidden border border-slate-800 shadow-xl">
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="bg-slate-900/80 text-slate-400 text-xs uppercase tracking-wider border-b border-slate-800">
                                <th class="p-4 font-semibold">User ID</th>
                                <th class="p-4 font-semibold">Full Name</th>
                                <th class="p-4 font-semibold">Role</th>
                                <th class="p-4 font-semibold">Department</th>
                                <th class="p-4 font-semibold">RFID UID</th>
                                <th class="p-4 font-semibold">Registered At</th>
                                <th class="p-4 font-semibold text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody id="users-table-body" class="divide-y divide-slate-800/60 text-xs sm:text-sm">
                            <tr>
                                <td colspan="7" class="p-8 text-center text-slate-500">Loading registered users...</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- TAB 4: JSON Database Inspector -->
        <div id="tab-content-json" class="hidden glass-panel rounded-xl p-5 border border-slate-800 shadow-xl space-y-3">
            <div class="flex justify-between items-center">
                <div>
                    <h3 class="text-sm font-semibold text-white">attendance_db.json Inspector</h3>
                    <p class="text-xs text-slate-400">Live on-disk JSON database storing all users and raw punch logs</p>
                </div>
                <a href="/api/database-json" target="_blank" class="text-xs font-medium text-emerald-400 hover:underline">
                    Open Raw JSON &rarr;
                </a>
            </div>
            <pre id="json-viewer" class="bg-slate-950 p-4 rounded-lg font-mono text-xs text-emerald-300 max-h-[500px] overflow-auto border border-slate-800 select-all"></pre>
        </div>

    </main>

    <!-- Modal: Register New User / Card -->
    <div id="register-modal" class="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm hidden flex items-center justify-center p-4">
        <div class="glass-panel bg-slate-900 border border-slate-700/80 rounded-2xl max-w-lg w-full p-6 shadow-2xl relative">
            <div class="flex justify-between items-center mb-5">
                <div>
                    <h3 class="text-lg font-bold text-white">Register New RFID User</h3>
                    <p class="text-xs text-slate-400">Assign an RFID card to a user with an auto-generated unique ID</p>
                </div>
                <button onclick="closeRegisterModal()" class="text-slate-400 hover:text-white p-1 rounded-lg">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                </button>
            </div>

            <form id="register-form" onsubmit="handleRegisterSubmit(event)" class="space-y-4 text-xs sm:text-sm">
                <!-- Auto ID Display -->
                <div>
                    <div class="flex justify-between items-center mb-1">
                        <label class="block font-medium text-slate-300">Unique User ID (Auto Generated)</label>
                        <span class="text-[10px] text-emerald-400 font-semibold bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/30">Auto Generated</span>
                    </div>
                    <input type="text" id="reg-user-id" required class="w-full bg-slate-950/80 border border-slate-700 rounded-lg px-3.5 py-2 font-mono text-emerald-300 font-semibold focus:ring-2 focus:ring-emerald-500 focus:outline-none">
                    <p class="text-[11px] text-slate-500 mt-1">Automatically computed from the highest sequence. You may customize if needed.</p>
                </div>

                <!-- RFID Card UID -->
                <div>
                    <div class="flex justify-between items-center mb-1">
                        <label class="block font-medium text-slate-300">RFID Card UID *</label>
                        <button type="button" onclick="autoFillLastUnregistered()" id="autofill-btn" class="text-[11px] text-teal-400 hover:text-teal-300 font-medium">
                            ⚡ Auto-fill Last Tapped Card
                        </button>
                    </div>
                    <input type="text" id="reg-uid" required placeholder="e.g. 09:9A:FC:1F" class="w-full bg-slate-950/80 border border-slate-700 rounded-lg px-3.5 py-2 font-mono text-amber-300 uppercase focus:ring-2 focus:ring-emerald-500 focus:outline-none">
                    
                    <!-- Quick Dropdown from unregistered scans -->
                    <div id="unreg-dropdown-container" class="mt-2 hidden">
                        <label class="text-[11px] text-slate-400">Or pick from recent taps:</label>
                        <select id="unreg-dropdown" onchange="document.getElementById('reg-uid').value = this.value" class="mt-1 w-full bg-slate-950 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-amber-300 font-mono">
                            <option value="">-- Select a detected card --</option>
                        </select>
                    </div>
                </div>

                <!-- Name -->
                <div>
                    <label class="block font-medium text-slate-300 mb-1">Full Name *</label>
                    <input type="text" id="reg-name" required placeholder="e.g. Alexander Pierce" class="w-full bg-slate-950/80 border border-slate-700 rounded-lg px-3.5 py-2 text-white focus:ring-2 focus:ring-emerald-500 focus:outline-none">
                </div>

                <!-- Role & Department -->
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block font-medium text-slate-300 mb-1">Role / Designation</label>
                        <input type="text" id="reg-role" placeholder="e.g. Staff / Engineer" value="Staff" class="w-full bg-slate-950/80 border border-slate-700 rounded-lg px-3.5 py-2 text-white focus:ring-2 focus:ring-emerald-500 focus:outline-none">
                    </div>
                    <div>
                        <label class="block font-medium text-slate-300 mb-1">Department</label>
                        <input type="text" id="reg-dept" placeholder="e.g. Technology" value="General" class="w-full bg-slate-950/80 border border-slate-700 rounded-lg px-3.5 py-2 text-white focus:ring-2 focus:ring-emerald-500 focus:outline-none">
                    </div>
                </div>

                <!-- Actions -->
                <div class="pt-3 flex items-center justify-end space-x-3">
                    <button type="button" onclick="closeRegisterModal()" class="px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold transition">
                        Cancel
                    </button>
                    <button type="submit" class="px-5 py-2 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-xs shadow-lg shadow-emerald-500/20 transition">
                        Save & Register Card
                    </button>
                </div>
            </form>
        </div>
    </div>

    <!-- Toast Notification -->
    <div id="toast" class="fixed bottom-6 right-6 z-50 transform translate-y-20 opacity-0 transition-all duration-300 flex items-center space-x-3 bg-slate-900 border border-emerald-500/50 text-white px-4 py-3 rounded-xl shadow-2xl">
        <span id="toast-icon" class="text-emerald-400">✓</span>
        <span id="toast-msg" class="text-xs sm:text-sm font-medium">Notification</span>
    </div>

    <!-- Frontend Script -->
    <script>
        // State
        let currentDateFilter = new Date().toISOString().split('T')[0];
        let activeTab = 'summary';
        let latestKnownScanId = 0;
        let unregisteredList = [];

        // Real-time Clock
        function updateClock() {
            const now = new Date();
            const timeStr = now.toLocaleTimeString('en-US', { hour12: false });
            document.getElementById('live-clock').textContent = timeStr;
        }
        setInterval(updateClock, 1000);
        updateClock();

        // Toast Helper
        function showToast(message, isError = false) {
            const toast = document.getElementById('toast');
            const msgEl = document.getElementById('toast-msg');
            const iconEl = document.getElementById('toast-icon');
            
            toast.className = `fixed bottom-6 right-6 z-50 transform transition-all duration-300 flex items-center space-x-3 bg-slate-900 border px-4 py-3 rounded-xl shadow-2xl ${
                isError ? 'border-rose-500/60 text-rose-200' : 'border-emerald-500/60 text-white'
            }`;
            iconEl.textContent = isError ? '✕' : '✓';
            iconEl.className = isError ? 'text-rose-400 font-bold' : 'text-emerald-400 font-bold';
            msgEl.textContent = message;

            toast.classList.remove('translate-y-20', 'opacity-0');
            setTimeout(() => {
                toast.classList.add('translate-y-20', 'opacity-0');
            }, 3500);
        }

        // Tab Switching
        function switchTab(tab) {
            activeTab = tab;
            ['summary', 'logs', 'users', 'json'].forEach(t => {
                const btn = document.getElementById(`tab-btn-${t}`);
                const content = document.getElementById(`tab-content-${t}`);
                if (t === tab) {
                    btn.classList.add('active', 'text-sky-400');
                    btn.classList.remove('text-slate-400');
                    content.classList.remove('hidden');
                } else {
                    btn.classList.remove('active', 'text-sky-400');
                    btn.classList.add('text-slate-400');
                    content.classList.add('hidden');
                }
            });

            if (tab === 'summary') fetchDailySummary();
            if (tab === 'logs') fetchLogs();
            if (tab === 'users') fetchUsers();
            if (tab === 'json') fetchJsonDatabase();
        }

        // Date Filter Handlers
        function setDateFilter(type) {
            const today = new Date();
            const datePicker = document.getElementById('date-picker');
            
            document.getElementById('quick-today').className = 'text-xs px-2.5 py-1 rounded-md font-medium text-slate-400 hover:text-slate-200 transition';
            document.getElementById('quick-yesterday').className = 'text-xs px-2.5 py-1 rounded-md font-medium text-slate-400 hover:text-slate-200 transition';
            document.getElementById('quick-all').className = 'text-xs px-2.5 py-1 rounded-md font-medium text-slate-400 hover:text-slate-200 transition';

            if (type === 'TODAY') {
                currentDateFilter = today.toISOString().split('T')[0];
                datePicker.value = currentDateFilter;
                document.getElementById('quick-today').className = 'text-xs px-2.5 py-1 rounded-md font-medium text-emerald-400 bg-slate-800 transition';
            } else if (type === 'YESTERDAY') {
                const y = new Date();
                y.setDate(today.getDate() - 1);
                currentDateFilter = y.toISOString().split('T')[0];
                datePicker.value = currentDateFilter;
                document.getElementById('quick-yesterday').className = 'text-xs px-2.5 py-1 rounded-md font-medium text-emerald-400 bg-slate-800 transition';
            } else if (type === 'ALL') {
                currentDateFilter = 'ALL';
                datePicker.value = '';
                document.getElementById('quick-all').className = 'text-xs px-2.5 py-1 rounded-md font-medium text-emerald-400 bg-slate-800 transition';
            }

            refreshAll();
        }

        function onCustomDateChange(val) {
            if (!val) return;
            currentDateFilter = val;
            document.getElementById('quick-today').className = 'text-xs px-2.5 py-1 rounded-md font-medium text-slate-400 hover:text-slate-200 transition';
            document.getElementById('quick-yesterday').className = 'text-xs px-2.5 py-1 rounded-md font-medium text-slate-400 hover:text-slate-200 transition';
            document.getElementById('quick-all').className = 'text-xs px-2.5 py-1 rounded-md font-medium text-slate-400 hover:text-slate-200 transition';
            refreshAll();
        }

        // Fetch Stats
        async function fetchStats() {
            try {
                const url = `/api/stats?date=${currentDateFilter}`;
                const res = await fetch(url);
                const data = await res.json();
                
                document.getElementById('stat-registered').textContent = data.total_registered_users;
                document.getElementById('stat-attendees').textContent = data.total_attendees;
                document.getElementById('stat-inside').textContent = data.currently_in;
                document.getElementById('stat-outside').textContent = data.checked_out;
                document.getElementById('stat-scans').textContent = data.total_scans_today;
                document.getElementById('stat-target-date-label').textContent = currentDateFilter === 'ALL' ? 'All recorded history' : `Date: ${currentDateFilter}`;

                if (data.latest_scan) {
                    const ls = data.latest_scan;
                    document.getElementById('latest-scan-label').innerHTML = `
                        <span class="${ls.status === 'IN' ? 'text-emerald-400' : 'text-amber-400'} font-bold">${ls.status}</span>
                        ${ls.name} (${ls.uid})
                    `;
                }
            } catch (err) {
                console.error("Stats fetch error:", err);
            }
        }

        // Fetch Daily Attendance Summary
        async function fetchDailySummary() {
            try {
                const url = `/api/attendance/summary?date=${currentDateFilter}`;
                const res = await fetch(url);
                const data = await res.json();
                const tbody = document.getElementById('summary-table-body');

                if (!data || data.length === 0) {
                    tbody.innerHTML = `
                        <tr>
                            <td colspan="8" class="p-8 text-center text-slate-500">
                                No attendance records logged for date <strong>${currentDateFilter}</strong>.
                            </td>
                        </tr>
                    `;
                    return;
                }

                tbody.innerHTML = data.map(row => {
                    const isUnreg = row.user_id === 'UNREGISTERED';
                    const badgeClass = row.current_status === 'IN' 
                        ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' 
                        : 'bg-amber-500/20 text-amber-300 border border-amber-500/30';
                    
                    const idBadge = isUnreg
                        ? `<button onclick="openRegisterModalWithUID('${row.uid}')" class="text-xs bg-rose-500/20 text-rose-300 border border-rose-500/40 px-2 py-0.5 rounded hover:bg-rose-500/30 font-medium">Unregistered (Tap to add)</button>`
                        : `<span class="font-mono font-bold text-sky-400 bg-sky-950/60 px-2 py-0.5 rounded border border-sky-500/30">${row.user_id}</span>`;

                    return `
                        <tr class="hover:bg-slate-800/40 transition">
                            <td class="p-4">${idBadge}</td>
                            <td class="p-4">
                                <div class="font-semibold text-white">${row.name}</div>
                                <div class="text-[11px] text-slate-400">${row.role} &bull; ${row.department}</div>
                            </td>
                            <td class="p-4 font-mono text-amber-300">${row.uid}</td>
                            <td class="p-4 font-mono text-emerald-400 font-medium">${row.first_in || '--:--:--'}</td>
                            <td class="p-4 font-mono text-slate-300 font-medium">${row.last_out || '--:--:--'}</td>
                            <td class="p-4 font-medium text-slate-200">
                                <span class="bg-slate-800 px-2 py-1 rounded text-xs border border-slate-700">${row.duration_formatted}</span>
                            </td>
                            <td class="p-4">
                                <span class="px-2.5 py-1 rounded-full text-xs font-bold ${badgeClass}">
                                    ${row.status_label}
                                </span>
                            </td>
                            <td class="p-4 text-center">
                                <span class="text-xs font-mono bg-slate-800 text-slate-300 px-2 py-0.5 rounded border border-slate-700 font-semibold">
                                    ${row.total_punches}
                                </span>
                            </td>
                        </tr>
                    `;
                }).join('');
            } catch (err) {
                console.error("Summary fetch error:", err);
            }
        }

        // Fetch Live Punch Logs
        async function fetchLogs() {
            try {
                const url = `/api/logs?date=${currentDateFilter}&limit=100`;
                const res = await fetch(url);
                const data = await res.json();
                const tbody = document.getElementById('logs-table-body');
                document.getElementById('log-count-indicator').textContent = `${data.length} logs`;

                if (!data || data.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="7" class="p-8 text-center text-slate-500">No punch logs found for this date.</td></tr>`;
                    return;
                }

                tbody.innerHTML = data.map(row => {
                    const isUnreg = row.user_id === 'UNREGISTERED';
                    const statusBadge = row.status === 'IN'
                        ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                        : 'bg-rose-500/20 text-rose-300 border border-rose-500/40';

                    return `
                        <tr class="hover:bg-slate-800/40 transition">
                            <td class="p-4 font-mono text-slate-500">#${row.id}</td>
                            <td class="p-4">
                                <span class="px-2.5 py-1 rounded text-xs font-bold uppercase tracking-wider ${statusBadge}">
                                    ${row.status}
                                </span>
                            </td>
                            <td class="p-4">
                                ${isUnreg 
                                    ? `<button onclick="openRegisterModalWithUID('${row.uid}')" class="text-xs text-rose-400 hover:underline">Unregistered</button>`
                                    : `<span class="font-mono text-sky-400 font-semibold">${row.user_id}</span>`}
                            </td>
                            <td class="p-4 font-medium text-white">${row.name}</td>
                            <td class="p-4 font-mono text-amber-300">${row.uid}</td>
                            <td class="p-4 font-mono text-slate-300 text-xs">${row.timestamp}</td>
                            <td class="p-4 font-mono text-slate-400 text-xs">${row.device_id}</td>
                        </tr>
                    `;
                }).join('');
            } catch (err) {
                console.error("Logs fetch error:", err);
            }
        }

        // Fetch Registered Users
        async function fetchUsers() {
            try {
                const res = await fetch('/api/users');
                const users = await res.json();
                document.getElementById('tab-users-count').textContent = users.length;
                const tbody = document.getElementById('users-table-body');

                if (!users || users.length === 0) {
                    tbody.innerHTML = `
                        <tr>
                            <td colspan="7" class="p-8 text-center text-slate-500">
                                No users registered yet. Click <strong>Register Card</strong> to add your first user!
                            </td>
                        </tr>
                    `;
                    return;
                }

                tbody.innerHTML = users.map(u => `
                    <tr class="hover:bg-slate-800/40 transition">
                        <td class="p-4 font-mono font-bold text-sky-400">
                            <span class="bg-sky-950/60 px-2 py-0.5 rounded border border-sky-500/30">${u.user_id}</span>
                        </td>
                        <td class="p-4 font-semibold text-white">${u.name}</td>
                        <td class="p-4 text-slate-300">${u.role || 'Staff'}</td>
                        <td class="p-4 text-slate-400">${u.department || 'General'}</td>
                        <td class="p-4 font-mono text-amber-300">${u.uid}</td>
                        <td class="p-4 text-xs font-mono text-slate-400">${u.created_at || '-'}</td>
                        <td class="p-4 text-right">
                            <button onclick="deleteUser('${u.user_id}', '${u.name}')" class="text-xs text-rose-400 hover:text-rose-300 bg-rose-500/10 hover:bg-rose-500/20 px-2.5 py-1 rounded border border-rose-500/30 transition">
                                Delete
                            </button>
                        </td>
                    </tr>
                `).join('');
            } catch (err) {
                console.error("Users fetch error:", err);
            }
        }

        // Fetch JSON Database for tab 4
        async function fetchJsonDatabase() {
            try {
                const res = await fetch('/api/database-json');
                const data = await res.json();
                document.getElementById('json-viewer').textContent = JSON.stringify(data, null, 2);
            } catch (err) {
                console.error("JSON fetch error:", err);
            }
        }

        // Fetch Unregistered Cards & Banner
        async function checkUnregisteredCards() {
            try {
                const res = await fetch('/api/unregistered-cards');
                unregisteredList = await res.json();
                
                const banner = document.getElementById('unregistered-banner');
                const dropdown = document.getElementById('unreg-dropdown');
                const dropdownContainer = document.getElementById('unreg-dropdown-container');

                if (unregisteredList && unregisteredList.length > 0) {
                    const latest = unregisteredList[0];
                    document.getElementById('banner-card-uid').textContent = latest.uid;
                    document.getElementById('banner-card-time').textContent = latest.last_seen;
                    document.getElementById('banner-quick-register-btn').onclick = () => openRegisterModalWithUID(latest.uid);
                    banner.classList.remove('hidden');

                    // Populate modal dropdown
                    dropdown.innerHTML = '<option value="">-- Select a detected card --</option>' +
                        unregisteredList.map(item => `
                            <option value="${item.uid}">${item.uid} (Tapped: ${item.last_seen})</option>
                        `).join('');
                    dropdownContainer.classList.remove('hidden');
                } else {
                    banner.classList.add('hidden');
                    dropdownContainer.classList.add('hidden');
                }
            } catch (err) {
                console.error("Check unregistered error:", err);
            }
        }

        function dismissUnregisteredBanner() {
            document.getElementById('unregistered-banner').classList.add('hidden');
        }

        // Excel Export Trigger
        function exportToExcel() {
            const url = `/api/export/excel?date=${currentDateFilter}`;
            showToast(`Generating Excel export for ${currentDateFilter}...`);
            window.location.href = url;
        }

        // User Registration Modal
        async function openRegisterModal() {
            try {
                const res = await fetch('/api/users/next-id');
                const data = await res.json();
                document.getElementById('reg-user-id').value = data.next_id;
            } catch (e) {
                document.getElementById('reg-user-id').value = 'EMP-1001';
            }
            document.getElementById('reg-name').value = '';
            document.getElementById('reg-uid').value = '';
            document.getElementById('reg-role').value = 'Staff';
            document.getElementById('reg-dept').value = 'General';
            document.getElementById('register-modal').classList.remove('hidden');
        }

        async function openRegisterModalWithUID(uid) {
            await openRegisterModal();
            document.getElementById('reg-uid').value = uid;
            document.getElementById('reg-name').focus();
        }

        function closeRegisterModal() {
            document.getElementById('register-modal').classList.add('hidden');
        }

        function autoFillLastUnregistered() {
            if (unregisteredList && unregisteredList.length > 0) {
                document.getElementById('reg-uid').value = unregisteredList[0].uid;
                showToast(`Filled with card: ${unregisteredList[0].uid}`);
            } else {
                showToast("No recent unregistered cards detected yet. Tap a card on your ESP32!", true);
            }
        }

        async function handleRegisterSubmit(e) {
            e.preventDefault();
            const custom_id = document.getElementById('reg-user-id').value.trim();
            const uid = document.getElementById('reg-uid').value.trim().toUpperCase();
            const name = document.getElementById('reg-name').value.trim();
            const role = document.getElementById('reg-role').value.trim() || 'Staff';
            const department = document.getElementById('reg-dept').value.trim() || 'General';

            if (!uid || !name) {
                showToast("Name and Card UID are required", true);
                return;
            }

            try {
                const res = await fetch('/api/users', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ custom_id, uid, name, role, department })
                });

                const data = await res.json();
                if (!res.ok) {
                    throw new Error(data.detail || "Registration failed");
                }

                closeRegisterModal();
                showToast(`User ${name} registered successfully with ID ${data.user.id}!`);
                refreshAll();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        async function deleteUser(userId, name) {
            if (!confirm(`Are you sure you want to unregister ${name} (${userId})? Existing attendance logs will be preserved.`)) {
                return;
            }
            try {
                const res = await fetch(`/api/users/${userId}`, { method: 'DELETE' });
                if (res.ok) {
                    showToast(`User ${userId} deleted`);
                    refreshAll();
                } else {
                    showToast("Failed to delete user", true);
                }
            } catch (e) {
                showToast(e.message, true);
            }
        }

        // Global Refresh
        async function refreshAll() {
            const icon = document.getElementById('refresh-icon');
            icon.classList.add('animate-spin');
            
            await Promise.all([
                fetchStats(),
                checkUnregisteredCards(),
                activeTab === 'summary' ? fetchDailySummary() : Promise.resolve(),
                activeTab === 'logs' ? fetchLogs() : Promise.resolve(),
                activeTab === 'users' ? fetchUsers() : Promise.resolve(),
                activeTab === 'json' ? fetchJsonDatabase() : Promise.resolve(),
            ]);

            setTimeout(() => icon.classList.remove('animate-spin'), 400);
        }

        // Initialize Page
        document.getElementById('date-picker').value = currentDateFilter;
        refreshAll();
        // Polling loop every 2.5s for real-time ESP32 scans
        setInterval(refreshAll, 2500);
    </script>
</body>
</html>
    """