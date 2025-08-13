import os
import random
import string
import asyncio
from datetime import datetime
from typing import Dict, List, Tuple, Optional

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
)

# -----------------------
# Basic Config
# -----------------------
BOT_TOKEN = "8408074707:AAEe7miladjgSS4RyoxDNHFTdtgqfIJ0Fvc"

LETTERS = ["A", "B", "C", "D", "E"]
NUMS = ["1", "2", "3", "4", "5"]
GRID_CELLS = {f"{r}{c}" for r in LETTERS for c in NUMS}

TREASURES_PER_PLAYER = 3
TRAPS_PER_PLAYER = 2
START_HP = 3
WIN_SCORE = 3

# -----------------------
# In-memory Game Store
# -----------------------
rooms: Dict[str, dict] = {}  # code -> room dict
user_room: Dict[int, str] = {}  # user_id -> room code

def code4() -> str:
    return "".join(random.choice(string.ascii_uppercase) for _ in range(4))

def now() -> str:
    return datetime.utcnow().strftime("%H:%M:%S")

# -----------------------
# Helpers
# -----------------------
def room_status_text(room: dict) -> str:
    p1 = room["p1"]
    p2 = room["p2"]
    def ptxt(p):
        return f"{p['name']} | Score {p['score']} | HP {p['hp']}"

    phase = room["state"]
    turn_name = "—"
    if room.get("turn") in (p1["id"], p2["id"]):
        turn_name = p1["name"] if room["turn"] == p1["id"] else p2["name"]

    return (
        f"Room {room['code']} | State: {phase}\n"
        f"Turn: {turn_name}\n"
        f"P1: {ptxt(p1)}\n"
        f"P2: {ptxt(p2)}"
    )

def valid_cell(cell: str) -> bool:
    return cell.upper() in GRID_CELLS

def parse_cells(args: List[str]) -> Tuple[List[str], List[str]]:
    """Return (valid, invalid) lists in upper case, without duplicates, keep order."""
    seen = set()
    valid, invalid = [], []
    for a in args:
        c = a.strip().upper()
        if not c or c in seen:
            continue
        seen.add(c)
        if valid_cell(c):
            valid.append(c)
        else:
            invalid.append(c)
    return valid, invalid

def get_opponent(room: dict, user_id: int) -> dict:
    return room["p2"] if room["p1"]["id"] == user_id else room["p1"]

def get_player(room: dict, user_id: int) -> dict:
    return room["p1"] if room["p1"]["id"] == user_id else room["p2"]

def both_ready(room: dict) -> bool:
    return room["p1"]["ready"] and room["p2"]["ready"]

def ensure_user_in_room(user_id: int) -> Optional[dict]:
    code = user_room.get(user_id)
    if not code:
        return None
    return rooms.get(code)

# -----------------------
# Commands
# -----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to *2-Player Treasure Hunt*!\n\n"
        "Quick guide:\n"
        "1) /play → room banayen (code milega)\n"
        "2) Doosra player /join CODE kare\n"
        "3) Dono placement karein:\n"
        f"   /place_treasures A1 A2 A3  (exact {TREASURES_PER_PLAYER})\n"
        f"   /place_traps B4 C5        (exact {TRAPS_PER_PLAYER})\n"
        "4) /ready likhen\n"
        "5) Game start → turn par /guess B3 type karo\n\n"
        "Status dekhne ko /status, resign ke liye /ff"
    )

async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Agar user kisi room me hai, pehle wahaan se nikaal do
    if user.id in user_room:
        old = user_room[user.id]
        rooms.pop(old, None)
        for uid, rcode in list(user_room.items()):
            if rcode == old:
                user_room.pop(uid, None)

    code = code4()
    rooms[code] = {
        "code": code,
        "state": "waiting",  # waiting -> placing -> playing -> ended
        "created": now(),
        "p1": {
            "id": user.id,
            "name": user.first_name,
            "hp": START_HP,
            "score": 0,
            "treasures": [],
            "traps": [],
            "guessed": set(),
            "ready": False,
        },
        "p2": {
            "id": None,
            "name": "—",
            "hp": START_HP,
            "score": 0,
            "treasures": [],
            "traps": [],
            "guessed": set(),
            "ready": False,
        },
        "turn": None,
        "log": [],
    }
    user_room[user.id] = code
    await update.message.reply_text(
        f"🆕 Room created: *{code}*\n"
        "Dusra player is code se join kare: /join " + code
    )

async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        return await update.message.reply_text("Usage: /join CODE")
    code = context.args[0].upper()
    room = rooms.get(code)
    if not room or room["state"] != "waiting":
        return await update.message.reply_text("Room not found ya already started.")
    if room["p1"]["id"] == user.id:
        return await update.message.reply_text("Aap already is room ke host ho.")

    # Set p2
    room["p2"]["id"] = user.id
    room["p2"]["name"] = user.first_name
    room["state"] = "placing"
    user_room[user.id] = code

    txt = (
        f"👥 Match ready! Room {code}\n\n"
        "Placement phase shuru:\n"
        f"- Treasures set karo: /place_treasures A1 A2 A3  (exact {TREASURES_PER_PLAYER})\n"
        f"- Traps set karo: /place_traps B4 C5            (exact {TRAPS_PER_PLAYER})\n"
        "- Jab ho jaye to /ready likho"
    )
    # Notify both
    for pid in (room["p1"]["id"], room["p2"]["id"]):
        try:
            await context.bot.send_message(chat_id=pid, text=txt)
        except Exception:
            pass

async def place_treasures(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    room = ensure_user_in_room(user.id)
    if not room or room["state"] != "placing":
        return await update.message.reply_text("Aap placement phase me kisi room me nahi ho.")
    cells, bad = parse_cells(context.args)
    if bad:
        return await update.message.reply_text(f"❌ Invalid cells: {' '.join(bad)}")
    if len(cells) != TREASURES_PER_PLAYER:
        return await update.message.reply_text(
            f"Please exactly {TREASURES_PER_PLAYER} cells do, e.g. /place_treasures A1 B2 C3"
        )
    player = get_player(room, user.id)
    if set(cells) & set(player.get("traps", [])):
        return await update.message.reply_text("Treasure and trap same cell nahi ho sakte.")
    player["treasures"] = cells
    player["ready"] = False
    await update.message.reply_text(f"✅ Treasures set: {' '.join(cells)}\nAb /place_traps karo.")

async def place_traps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    room = ensure_user_in_room(user.id)
    if not room or room["state"] != "placing":
        return await update.message.reply_text("Aap placement phase me kisi room me nahi ho.")
    cells, bad = parse_cells(context.args)
    if bad:
        return await update.message.reply_text(f"❌ Invalid cells: {' '.join(bad)}")
    if len(cells) != TRAPS_PER_PLAYER:
        return await update.message.reply_text(
            f"Please exactly {TRAPS_PER_PLAYER} cells do, e.g. /place_traps A4 B5"
        )
    player = get_player(room, user.id)
    if set(cells) & set(player.get("treasures", [])):
        return await update.message.reply_text("Trap and treasure same cell nahi ho sakte.")
    player["traps"] = cells
    player["ready"] = False
    await update.message.reply_text(f"✅ Traps set: {' '.join(cells)}\nAkhri step: /ready")

async def ready(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    room = ensure_user_in_room(user.id)
    if not room or room["state"] != "placing":
        return await update.message.reply_text("Abhi ready karne ka time nahi hai.")
    player = get_player(room, user.id)
    if len(player["treasures"]) != TREASURES_PER_PLAYER or len(player["traps"]) != TRAPS_PER_PLAYER:
        return await update.message.reply_text("Pehle treasures aur traps sahi se set karo.")
    player["ready"] = True
    await update.message.reply_text("✅ You are READY.")

    if both_ready(room):
        room["state"] = "playing"
        # Randomly choose who starts
        room["turn"] = random.choice([room["p1"]["id"], room["p2"]["id"]])
        for pid in (room["p1"]["id"], room["p2"]["id"]):
            try:
                await context.bot.send_message(
                    chat_id=pid,
                    text="🎮 Game start!\n"
                         f"Turn: {get_player(room, room['turn'])['name']}\n"
                         "Guess like: /guess B3"
                )
            except Exception:
                pass

async def guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    room = ensure_user_in_room(user.id)
    if not room or room["state"] != "playing":
        return await update.message.reply_text("Koi running game nahi mila.")
    if room["turn"] != user.id:
        return await update.message.reply_text("Abhi aapka turn nahi hai.")

    if not context.args:
        return await update.message.reply_text("Usage: /guess B3")

    cell = context.args[0].upper().strip()
    if not valid_cell(cell):
        return await update.message.reply_text("❌ Invalid cell. Example: A1..E5")

    me = get_player(room, user.id)
    opp = get_opponent(room, user.id)

    if cell in me["guessed"]:
        return await update.message.reply_text("Ye cell aap pehle try kar chuke ho. Doosra do.")
    me["guessed"].add(cell)

    result = "miss"
    extra_turn = False
    end_now = False

    if cell in opp["treasures"]:
        me["score"] += 1
        result = "TREASURE ⭐ (+1 point)"
        extra_turn = True
        if me["score"] >= WIN_SCORE:
            room["state"] = "ended"
            end_now = True
    elif cell in opp["traps"]:
        me["hp"] -= 1
        result = "TRAP 💥 (-1 HP)"
        if me["hp"] <= 0:
            room["state"] = "ended"
            end_now = True

    room["log"].append({"by": user.id, "cell": cell, "res": result, "ts": now()})

    if end_now:
        winner = me if me["hp"] > 0 or me["score"] >= WIN_SCORE else opp
        msg = (
            f"🔔 {user.first_name} guessed {cell}: {result}\n\n"
            f"🏁 Game Over! Winner: {winner['name']}\n\n" + room_status_text(room)
        )
        for pid in (room["p1"]["id"], room["p2"]["id"]):
            try:
                await context.bot.send_message(chat_id=pid, text=msg)
            except Exception:
                pass
        return

    # Continue game
    if not extra_turn:
        room["turn"] = opp["id"]

    msg_me = f"🎯 You guessed {cell}: {result}\n" + room_status_text(room)
    msg_opp = f"⚠️ {me['name']} guessed {cell}: {result}\n" + room_status_text(room)
    try:
        await context.bot.send_message(chat_id=me["id"], text=msg_me)
    except Exception:
        pass
    try:
        await context.bot.send_message(chat_id=opp["id"], text=msg_opp)
    except Exception:
        pass

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    room = ensure_user_in_room(user.id)
    if not room:
        return await update.message.reply_text("Aap kisi room me nahi ho.")
    await update.message.reply_text(room_status_text(room))

async def ff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    room = ensure_user_in_room(user.id)
    if not room:
        return await update.message.reply_text("Koi room nahi mila.")
    if room["state"] == "ended":
        return await update.message.reply_text("Game already end ho chuka hai.")
    opp = get_opponent(room, user.id)
    room["state"] = "ended"
    for pid in (room["p1"]["id"], room["p2"]["id"]):
        try:
            await context.bot.send_message(
                chat_id=pid,
                text=f"🏳️ {user.first_name} resigned.\nWinner: {opp['name']}"
            )
        except Exception:
            pass

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/play – new room\n"
        "/join CODE – join room\n"
        f"/place_treasures <{TREASURES_PER_PLAYER} cells>\n"
        f"/place_traps <{TRAPS_PER_PLAYER} cells>\n"
        "/ready – done with placement\n"
        "/guess <cell> – play turn\n"
        "/status – match status\n"
        "/ff – resign"
    )

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("play", play))
    app.add_handler(CommandHandler("join", join))
    app.add_handler(CommandHandler("place_treasures", place_treasures))
    app.add_handler(CommandHandler("place_traps", place_traps))
    app.add_handler(CommandHandler("ready", ready))
    app.add_handler(CommandHandler("guess", guess))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("ff", ff))

    # Ignore all other messages politely
    app.add_handler(MessageHandler(filters.ALL, lambda u, c: u.message.reply_text("Use commands like /play, /join, /guess B3")))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()