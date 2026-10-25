import json
import os
import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

DB_FILE = "attendance.db"
JSON_FILE = "attendance_db.json"

def get_current_ist_time() -> str:
    """Returns current local timestamp in standard format: YYYY-MM-DD HH:MM:SS"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def parse_timestamp(raw_ts: Optional[str]) -> tuple[str, str, str]:
    """
    Parses timestamp string into (full_ts, date_str, time_str).
    Falls back to current local time if raw_ts is invalid or 'Local_Scan'.
    """
    now = datetime.now()
    default_date = now.strftime("%Y-%m-%d")
    default_time = now.strftime("%H:%M:%S")
    default_ts = f"{default_date} {default_time}"

    if not raw_ts or raw_ts.strip() in ("", "Local_Scan", "string"):
        return default_ts, default_date, default_time

    clean = raw_ts.strip().replace("T", " ")
    # Try common formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            dt = datetime.strptime(clean, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S"), dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
        except ValueError:
            continue

    # Try regex match for YYYY-MM-DD
    date_match = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})", clean)
    time_match = re.search(r"(\d{2}:\d{2}:\d{2})", clean)
    d = date_match.group(1).replace("/", "-") if date_match else default_date
    t = time_match.group(1) if time_match else default_time
    return f"{d} {t}", d, t


class DatabaseManager:
    def __init__(self, db_path: str = DB_FILE, json_path: str = JSON_FILE):
        self.db_path = db_path
        self.json_path = json_path
        self.init_sqlite()
        self.init_json()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_sqlite(self):
        with self.get_connection() as conn:
            # Create users table with auto ID support
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    uid TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT DEFAULT 'Staff',
                    department TEXT DEFAULT 'General',
                    created_at TEXT
                )
            """)
            # Check if existing users table lacks user_id
            cursor = conn.execute("PRAGMA table_info(users)")
            cols = [row["name"] for row in cursor.fetchall()]
            if "user_id" not in cols:
                # Migrate old schema if needed
                conn.execute("ALTER TABLE users ADD COLUMN user_id TEXT")
            if "department" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN department TEXT DEFAULT 'General'")
            if "created_at" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN created_at TEXT")

            # Create logs table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    date TEXT,
                    time TEXT,
                    device_id TEXT,
                    status TEXT NOT NULL
                )
            """)

            # Add missing date/time columns to logs if upgrading
            cursor = conn.execute("PRAGMA table_info(logs)")
            log_cols = [row["name"] for row in cursor.fetchall()]
            if "date" not in log_cols:
                conn.execute("ALTER TABLE logs ADD COLUMN date TEXT")
            if "time" not in log_cols:
                conn.execute("ALTER TABLE logs ADD COLUMN time TEXT")

            # Backfill date/time for existing rows in logs
            rows = conn.execute("SELECT id, timestamp, date, time FROM logs WHERE date IS NULL OR date = ''").fetchall()
            for r in rows:
                full_ts, d, t = parse_timestamp(r["timestamp"])
                conn.execute("UPDATE logs SET timestamp = ?, date = ?, time = ? WHERE id = ?", (full_ts, d, t, r["id"]))

            conn.commit()

    def init_json(self):
        """Initializes JSON database and syncs with SQLite data if necessary."""
        if not os.path.exists(self.json_path):
            data = {
                "system": "RFID Attendance System",
                "version": "2.0",
                "users": [],
                "logs": [],
                "unregistered_scans": []
            }
            self._save_json(data)

        # Sync existing SQLite data into JSON if JSON logs or users are empty
        data = self._read_json()
        need_save = False

        with self.get_connection() as conn:
            # Sync users
            db_users = conn.execute("SELECT * FROM users").fetchall()
            if db_users and not data.get("users"):
                data["users"] = [
                    {
                        "id": u["user_id"] or f"EMP-{1000 + idx}",
                        "uid": u["uid"].upper(),
                        "name": u["name"],
                        "role": u["role"] or "Staff",
                        "department": u["department"] or "General",
                        "created_at": u["created_at"] or get_current_ist_time()
                    }
                    for idx, u in enumerate(db_users, 1)
                ]
                need_save = True

            # Sync logs
            db_logs = conn.execute("""
                SELECT l.id, l.uid, l.timestamp, l.date, l.time, l.device_id, l.status,
                       u.user_id, u.name
                FROM logs l
                LEFT JOIN users u ON UPPER(l.uid) = UPPER(u.uid)
                ORDER BY l.id ASC
            """).fetchall()

            if db_logs and not data.get("logs"):
                data["logs"] = [
                    {
                        "id": r["id"],
                        "uid": r["uid"].upper(),
                        "user_id": r["user_id"] or "UNREGISTERED",
                        "name": r["name"] or "Unregistered Card",
                        "status": r["status"],
                        "timestamp": r["timestamp"],
                        "date": r["date"],
                        "time": r["time"],
                        "device_id": r["device_id"]
                    }
                    for r in db_logs
                ]
                need_save = True

            # Gather recent unregistered cards
            unreg_cards = conn.execute("""
                SELECT l.uid, MAX(l.timestamp) as last_seen, COUNT(1) as scan_count
                FROM logs l
                LEFT JOIN users u ON UPPER(l.uid) = UPPER(u.uid)
                WHERE u.uid IS NULL AND l.uid != 'string'
                GROUP BY l.uid
                ORDER BY last_seen DESC
            """).fetchall()

            data["unregistered_scans"] = [
                {"uid": r["uid"].upper(), "last_seen": r["last_seen"], "scan_count": r["scan_count"]}
                for r in unreg_cards
            ]
            need_save = True

        if need_save:
            self._save_json(data)

    def _read_json(self) -> Dict[str, Any]:
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"users": [], "logs": [], "unregistered_scans": []}

    def _save_json(self, data: Dict[str, Any]):
        data["last_updated"] = get_current_ist_time()
        temp_file = f"{self.json_path}.tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_file, self.json_path)

    def generate_unique_user_id(self) -> str:
        """
        Generates a unique User ID like EMP-1001, EMP-1002, etc.
        Checks both JSON and SQLite for existing IDs.
        """
        max_num = 1000
        data = self._read_json()
        pattern = re.compile(r"^EMP-(\d+)$", re.IGNORECASE)

        for u in data.get("users", []):
            match = pattern.match(u.get("id", ""))
            if match:
                max_num = max(max_num, int(match.group(1)))

        with self.get_connection() as conn:
            rows = conn.execute("SELECT user_id FROM users").fetchall()
            for r in rows:
                if r["user_id"]:
                    match = pattern.match(r["user_id"])
                    if match:
                        max_num = max(max_num, int(match.group(1)))

        return f"EMP-{max_num + 1}"

    def register_user(
        self,
        name: str,
        uid: str,
        role: str = "Staff",
        department: str = "General",
        custom_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Registers a new card/user. Assigns an automatic unique ID if not provided."""
        uid = uid.strip().upper()
        name = name.strip()
        role = role.strip() or "Staff"
        department = department.strip() or "General"
        user_id = custom_id.strip() if custom_id and custom_id.strip() else self.generate_unique_user_id()
        created_at = get_current_ist_time()

        # Check for duplicate UID or User ID
        with self.get_connection() as conn:
            existing_uid = conn.execute("SELECT user_id, name FROM users WHERE UPPER(uid) = ?", (uid,)).fetchone()
            if existing_uid:
                raise ValueError(f"Card UID '{uid}' is already assigned to {existing_uid['name']} ({existing_uid['user_id']})")

            existing_id = conn.execute("SELECT name FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if existing_id:
                raise ValueError(f"User ID '{user_id}' already exists for {existing_id['name']}")

            conn.execute(
                "INSERT INTO users (user_id, uid, name, role, department, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, uid, name, role, department, created_at)
            )
            conn.commit()

        # Update JSON DB
        data = self._read_json()
        new_user = {
            "id": user_id,
            "uid": uid,
            "name": name,
            "role": role,
            "department": department,
            "created_at": created_at
        }
        data.setdefault("users", []).append(new_user)

        # Remove from unregistered list if present
        data["unregistered_scans"] = [u for u in data.get("unregistered_scans", []) if u["uid"].upper() != uid]

        # Update existing logs in JSON where this UID was previously marked 'UNREGISTERED'
        for log in data.get("logs", []):
            if log.get("uid", "").upper() == uid:
                log["user_id"] = user_id
                log["name"] = name

        self._save_json(data)
        return new_user

    def update_user(self, user_id: str, name: str, role: str, department: str) -> Dict[str, Any]:
        """Updates user details."""
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE users SET name = ?, role = ?, department = ? WHERE user_id = ?",
                (name.strip(), role.strip(), department.strip(), user_id)
            )
            conn.commit()

        data = self._read_json()
        updated_user = None
        for u in data.get("users", []):
            if u["id"] == user_id:
                u["name"] = name.strip()
                u["role"] = role.strip()
                u["department"] = department.strip()
                updated_user = u
                break

        # Also update logs
        if updated_user:
            for log in data.get("logs", []):
                if log.get("user_id") == user_id:
                    log["name"] = name.strip()

        self._save_json(data)
        return updated_user or {}

    def delete_user(self, user_id: str) -> bool:
        """Deletes user registration (logs remain preserved as unregistered)."""
        uid = None
        with self.get_connection() as conn:
            row = conn.execute("SELECT uid FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                uid = row["uid"]
                conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
                conn.commit()

        data = self._read_json()
        data["users"] = [u for u in data.get("users", []) if u["id"] != user_id]

        # In JSON logs, mark user as unregistered
        for log in data.get("logs", []):
            if log.get("user_id") == user_id:
                log["user_id"] = "UNREGISTERED"
                log["name"] = "Unregistered Card"

        self._save_json(data)
        return True

    def get_all_users(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT user_id, uid, name, role, department, created_at FROM users ORDER BY user_id ASC").fetchall()
            return [dict(r) for r in rows]

    def get_user_by_uid(self, uid: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT user_id, uid, name, role, department, created_at FROM users WHERE UPPER(uid) = ?", (uid.strip().upper(),)).fetchone()
            return dict(row) if row else None

    def record_scan(self, uid: str, raw_timestamp: str, device_id: str) -> Dict[str, Any]:
        """
        Records an RFID scan event.
        Calculates whether it is punch IN or punch OUT.
        Stores in SQLite and JSON database.
        """
        uid = uid.strip().upper()
        device_id = device_id.strip() if device_id else "esp32_c3_gate1"
        full_ts, date_str, time_str = parse_timestamp(raw_timestamp)

        # Check if user is registered
        user = self.get_user_by_uid(uid)
        user_id = user["user_id"] if user else "UNREGISTERED"
        user_name = user["name"] if user else "Unregistered Card"
        is_registered = bool(user)

        # Determine next status (toggle IN/OUT based on last scan for this UID)
        with self.get_connection() as conn:
            last = conn.execute("SELECT status FROM logs WHERE UPPER(uid) = ? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
            next_status = "OUT" if (last and last["status"] == "IN") else "IN"

            cur = conn.cursor()
            cur.execute(
                "INSERT INTO logs (uid, timestamp, date, time, device_id, status) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, full_ts, date_str, time_str, device_id, next_status)
            )
            log_id = cur.lastrowid
            conn.commit()

        # Update JSON database
        data = self._read_json()
        log_entry = {
            "id": log_id,
            "uid": uid,
            "user_id": user_id,
            "name": user_name,
            "status": next_status,
            "timestamp": full_ts,
            "date": date_str,
            "time": time_str,
            "device_id": device_id
        }
        data.setdefault("logs", []).append(log_entry)

        # Track unregistered scans
        if not is_registered:
            unreg_list = data.setdefault("unregistered_scans", [])
            existing = next((item for item in unreg_list if item["uid"].upper() == uid), None)
            if existing:
                existing["last_seen"] = full_ts
                existing["scan_count"] = existing.get("scan_count", 1) + 1
            else:
                unreg_list.append({"uid": uid, "last_seen": full_ts, "scan_count": 1})

        self._save_json(data)

        return {
            "log_id": log_id,
            "uid": uid,
            "user_id": user_id,
            "name": user_name,
            "action": next_status,
            "timestamp": full_ts,
            "date": date_str,
            "time": time_str,
            "device_id": device_id,
            "is_registered": is_registered,
            "message": f"{user_name} punched {next_status} at {time_str}"
        }

    def get_logs(self, date: Optional[str] = None, limit: int = 150) -> List[Dict[str, Any]]:
        """Retrieves raw punch logs, optionally filtered by date."""
        with self.get_connection() as conn:
            query = """
                SELECT l.id, l.uid, COALESCE(u.user_id, 'UNREGISTERED') as user_id,
                       COALESCE(u.name, 'Unregistered Card') as name,
                       COALESCE(u.role, 'Visitor') as role,
                       l.timestamp, l.date, l.time, l.device_id, l.status
                FROM logs l
                LEFT JOIN users u ON UPPER(l.uid) = UPPER(u.uid)
            """
            params = []
            if date and date != "ALL":
                query += " WHERE l.date = ? "
                params.append(date)

            query += " ORDER BY l.id DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_daily_summary(self, target_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Calculates daily attendance summary for all cards/users active on target_date.
        Includes first IN punch, last OUT punch, total duration, and punch history.
        """
        if not target_date or target_date == "ALL":
            target_date = datetime.now().strftime("%Y-%m-%d")

        with self.get_connection() as conn:
            # Query all logs for this date in chronological order
            logs = conn.execute("""
                SELECT l.id, l.uid, COALESCE(u.user_id, 'UNREGISTERED') as user_id,
                       COALESCE(u.name, 'Unregistered Card') as name,
                       COALESCE(u.role, 'Visitor') as role,
                       COALESCE(u.department, '-') as department,
                       l.timestamp, l.time, l.status, l.device_id
                FROM logs l
                LEFT JOIN users u ON UPPER(l.uid) = UPPER(u.uid)
                WHERE l.date = ?
                ORDER BY l.id ASC
            """, (target_date,)).fetchall()

        # Group by card UID
        records_by_uid: Dict[str, Dict[str, Any]] = {}
        for row in logs:
            uid = row["uid"].upper()
            if uid not in records_by_uid:
                records_by_uid[uid] = {
                    "uid": uid,
                    "user_id": row["user_id"],
                    "name": row["name"],
                    "role": row["role"],
                    "department": row["department"],
                    "date": target_date,
                    "first_in": None,
                    "last_out": None,
                    "punches": [],
                    "current_status": "OUT",
                    "total_punches": 0
                }

            rec = records_by_uid[uid]
            rec["total_punches"] += 1
            rec["punches"].append({
                "time": row["time"],
                "status": row["status"],
                "device_id": row["device_id"]
            })
            rec["current_status"] = row["status"]

            if row["status"] == "IN" and rec["first_in"] is None:
                rec["first_in"] = row["time"]
            if row["status"] == "OUT":
                rec["last_out"] = row["time"]

        # Calculate durations
        summary_list = []
        for uid, rec in records_by_uid.items():
            # Duration calculation between IN and OUT pairs
            duration_minutes = 0
            in_time_dt = None

            for p in rec["punches"]:
                try:
                    p_dt = datetime.strptime(f"{target_date} {p['time']}", "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue

                if p["status"] == "IN":
                    in_time_dt = p_dt
                elif p["status"] == "OUT" and in_time_dt:
                    diff = (p_dt - in_time_dt).total_seconds() / 60
                    if diff > 0:
                        duration_minutes += diff
                    in_time_dt = None

            # If currently still inside (IN without OUT yet), compute time elapsed until now if target_date is today
            if in_time_dt and target_date == datetime.now().strftime("%Y-%m-%d"):
                now_dt = datetime.now()
                diff = (now_dt - in_time_dt).total_seconds() / 60
                if diff > 0:
                    duration_minutes += diff

            hours = int(duration_minutes // 60)
            mins = int(duration_minutes % 60)
            rec["duration_minutes"] = round(duration_minutes, 1)
            rec["duration_formatted"] = f"{hours}h {mins}m" if duration_minutes > 0 else "-"

            # Overall attendance status
            if rec["current_status"] == "IN":
                rec["status_label"] = "PRESENT (IN)"
                rec["status_badge"] = "IN"
            elif rec["last_out"]:
                rec["status_label"] = "CHECKED OUT"
                rec["status_badge"] = "OUT"
            else:
                rec["status_label"] = "COMPLETED"
                rec["status_badge"] = "DONE"

            summary_list.append(rec)

        # Sort summary: Registered users first, then by name
        summary_list.sort(key=lambda x: (x["user_id"] == "UNREGISTERED", x["name"]))
        return summary_list

    def get_available_dates(self) -> List[str]:
        """Returns sorted list of distinct dates from logs."""
        with self.get_connection() as conn:
            rows = conn.execute("SELECT DISTINCT date FROM logs WHERE date IS NOT NULL AND date != '' ORDER BY date DESC").fetchall()
            dates = [r["date"] for r in rows]
            today = datetime.now().strftime("%Y-%m-%d")
            if today not in dates:
                dates.insert(0, today)
            return dates

    def get_unregistered_cards(self) -> List[Dict[str, Any]]:
        """Returns recently scanned cards that are not registered in the system."""
        with self.get_connection() as conn:
            rows = conn.execute("""
                SELECT l.uid, MAX(l.timestamp) as last_seen, COUNT(1) as scan_count
                FROM logs l
                LEFT JOIN users u ON UPPER(l.uid) = UPPER(u.uid)
                WHERE u.uid IS NULL AND l.uid != 'string'
                GROUP BY l.uid
                ORDER BY last_seen DESC
            """).fetchall()
            return [dict(r) for r in rows]

    def get_stats(self, date: Optional[str] = None) -> Dict[str, Any]:
        """Calculates system stats for the given date (default today)."""
        target_date = date if (date and date != "ALL") else datetime.now().strftime("%Y-%m-%d")

        with self.get_connection() as conn:
            total_users = conn.execute("SELECT COUNT(1) FROM users").fetchone()[0]
            
            # Scans on target date
            scans_today = conn.execute("SELECT COUNT(1) FROM logs WHERE date = ?", (target_date,)).fetchone()[0]
            
            # Distinct attendees on target date
            attendees = conn.execute("SELECT COUNT(DISTINCT uid) FROM logs WHERE date = ?", (target_date,)).fetchone()[0]

            # Latest scan
            latest = conn.execute("""
                SELECT l.uid, COALESCE(u.name, 'Unregistered Card') as name, l.status, l.timestamp
                FROM logs l
                LEFT JOIN users u ON UPPER(l.uid) = UPPER(u.uid)
                ORDER BY l.id DESC LIMIT 1
            """).fetchone()

        summary = self.get_daily_summary(target_date)
        currently_in = sum(1 for s in summary if s["current_status"] == "IN")
        checked_out = sum(1 for s in summary if s["current_status"] == "OUT")

        return {
            "target_date": target_date,
            "total_registered_users": total_users,
            "total_attendees": attendees,
            "currently_in": currently_in,
            "checked_out": checked_out,
            "total_scans_today": scans_today,
            "latest_scan": dict(latest) if latest else None
        }

# Global DB manager singleton
db = DatabaseManager()
