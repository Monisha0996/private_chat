from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime
import random
import string
import re
import os

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    ping_timeout=60,
    ping_interval=25
)

# Memory only. No database. No messages are stored.
online_users = {}
rooms = {}


def clean_text(value, max_len=100):
    value = str(value or "").strip()
    value = re.sub(r"[<>]", "", value)
    return value[:max_len]


def generate_room_code(length=6):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def current_time():
    return datetime.now().strftime("%H:%M")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "rooms": len(rooms),
        "online_users": len(online_users)
    })


# -------------------------------------------------------
# IMPORTANT FIX FOR RENDER
# Create room using normal HTTP instead of Socket.IO.
# -------------------------------------------------------
@app.post("/api/create-room")
def api_create_room():
    data = request.get_json(silent=True) or {}
    username = clean_text(data.get("username"), 30)

    if not username:
        return jsonify({
            "ok": False,
            "message": "Please enter your name."
        }), 400

    room_code = generate_room_code()

    while room_code in rooms:
        room_code = generate_room_code()

    rooms[room_code] = {
        "users": set(),
        "created_by": None
    }

    print("ROOM CREATED BY HTTP:", room_code, flush=True)

    return jsonify({
        "ok": True,
        "room": room_code
    })


# Optional fallback: create room using Socket.IO if needed locally.
@socketio.on("create_private_room")
def create_private_room(data):
    print("CREATE ROOM EVENT RECEIVED:", data, flush=True)

    username = clean_text(data.get("username"), 30)

    if not username:
        emit("error_message", {
            "message": "Please enter your name."
        })
        return

    room_code = generate_room_code()

    while room_code in rooms:
        room_code = generate_room_code()

    rooms[room_code] = {
        "users": set(),
        "created_by": request.sid
    }

    print("ROOM CREATED BY SOCKET:", room_code, flush=True)

    join_user_to_room(username, room_code, is_creator=True)


@socketio.on("join_private_room")
def join_private_room_event(data):
    print("JOIN ROOM EVENT RECEIVED:", data, flush=True)

    username = clean_text(data.get("username"), 30)
    room_code = clean_text(data.get("room"), 20).upper()

    if not username or not room_code:
        emit("error_message", {
            "message": "Name and room code are required."
        })
        return

    if room_code not in rooms:
        emit("error_message", {
            "message": "Room not found. Please check the code."
        })
        return

    if len(rooms[room_code]["users"]) >= 2:
        emit("error_message", {
            "message": "This private room is already full."
        })
        return

    join_user_to_room(username, room_code, is_creator=False)


def join_user_to_room(username, room_code, is_creator=False):
    join_room(room_code)

    online_users[request.sid] = {
        "username": username,
        "room": room_code
    }

    rooms[room_code]["users"].add(request.sid)

    print(f"{username} JOINED ROOM {room_code}", flush=True)

    emit("room_joined", {
        "room": room_code,
        "username": username,
        "is_creator": is_creator
    })

    emit("system_message", {
        "message": f"{username} joined the private chat.",
        "time": current_time()
    }, to=room_code)

    send_online_users(room_code)


@socketio.on("send_message")
def handle_message(data):
    user = online_users.get(request.sid)

    if not user:
        emit("error_message", {
            "message": "You are not inside a room."
        })
        return

    message = clean_text(data.get("message"), 1000)

    if not message:
        return

    room_code = user["room"]

    # Message is sent live only. It is not stored anywhere.
    emit("receive_message", {
        "username": user["username"],
        "message": message,
        "time": current_time()
    }, to=room_code)


@socketio.on("typing")
def handle_typing():
    user = online_users.get(request.sid)

    if user:
        emit("typing_status", {
            "username": user["username"]
        }, to=user["room"], include_self=False)


@socketio.on("logout_chat")
def handle_logout():
    remove_user()


@socketio.on("disconnect")
def handle_disconnect():
    remove_user()


def remove_user():
    user = online_users.pop(request.sid, None)

    if not user:
        return

    username = user["username"]
    room_code = user["room"]

    leave_room(room_code)

    if room_code in rooms:
        rooms[room_code]["users"].discard(request.sid)

        emit("system_message", {
            "message": f"{username} left the chat.",
            "time": current_time()
        }, to=room_code)

        send_online_users(room_code)

        # Delete room completely when empty.
        if len(rooms[room_code]["users"]) == 0:
            del rooms[room_code]
            print("ROOM DELETED:", room_code, flush=True)


def send_online_users(room_code):
    users = []

    if room_code in rooms:
        for sid in rooms[room_code]["users"]:
            user = online_users.get(sid)

            if user:
                users.append(user["username"])

    emit("online_users", {
        "users": sorted(list(set(users)))
    }, to=room_code)


if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=True,
        allow_unsafe_werkzeug=True
    )
