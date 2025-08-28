# main.py
# Agent vs Mole — Telegram social-deduction (MVP)
# WARNING: Keep your BOT_TOKEN secret. If you pasted it publicly, regenerate it via BotFather ASAP.

import asyncio
import json
import os
import random
import string
import time
from typing import Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Chat
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler,
    filters, CallbackQueryHandler
)

# -----------------
# CONFIG (TOKEN)
# -----------------
# You provided this token in chat. IMPORTANT: After testing, regenerate a new token in BotFather
# and replace it here (do NOT share it publicly).
BOT_TOKEN = "8408074707:AAEe7miladjgSS4RyoxDNHFTdtgqfIJ0Fvc"

DATA_FILE = "data.json"
SAVE_INTERVAL = 10  # seconds
BIG_MATCH_PLAYER_THRESHOLD = 7
MEGA_VALUE_IN_NANO = 10_000

# Reward values (can tweak)
NANO_REWARD_SMALL_WIN = 50
NANO_REWARD_SMALL_CONSOLE = 10
MEGA_REWARD_BIG_WIN = 1

TASK_PHASE_SECONDS = 90
MEETING_SECONDS = 60
VOTE_SECONDS = 25

SHOP_ITEMS = {
    "A01": ("Sakura (Common)", 200, "nano"),
    "A02": ("Kazuma (Common)", 200, "nano"),
    "A03": ("Hinata (Uncommon)", 600, "nano"),
    "A04": ("Levi (Rare)", 1, "mega"),
    "A05": ("Zero-Two (Epic)", 2, "mega"),
    "A06": ("Gojo (Legendary)", 3, "mega"),
}

# -----------------
# Persistence store
# -----------------
def now_ts() -> int:
    return int(time.time())

class Store:
    def __init__(self, path: str):
        self.path = path
        self.lock = asyncio.Lock()
        # data model: players, groups, globalscore
        self.data = {
            "players": {},   # user_id -> profile
            "groups": {},    # chat_id -> group game state
            "globalscore": {} # user_id -> score
        }
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                print("Warning: failed loading data.json — starting fresh.")

    async def save_loop(self):
        # periodic saver
        while True:
            await asyncio.sleep(SAVE_INTERVAL)
            await self.save()

    async def save(self):
        async with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    def pget(self, user_id: int) -> dict:
        k = str(user_id)
        p = self.data["players"].setdefault(k, {
            "username": None,
            "nano": 0,
            "mega": 0,
            "inventory": [],
            "wins": 0,
            "losses": 0,
            "last_seen": now_ts(),
        })
        return p

    def gget(self, chat_id: int) -> dict:
        k = str(chat_id)
        g = self.data["groups"].setdefault(k, {
            "lobby": [],
            "phase": "idle",
            "round": 0,
            "roles": {},
            "alive": [],
            "tasks": {},
            "votes": {},
            "meeting_caller": None,
            "eliminated": [],
            "host": None,
            "game_started_ts": None,
            "last_summary": "",
        })
        return g

    def add_global_score(self, uid: int, delta: int):
        k = str(uid)
        self.data["globalscore"][k] = self.data["globalscore"].get(k, 0) + delta

store = Store(DATA_FILE)

# -----------------
# Utilities & tasks
# -----------------
def mention(user) -> str:
    name = user.full_name or (user.username or str(user.id))
    return f"{name}"

def choose_moles(n_players: int) -> int:
    if n_players <= 6:
        return 1
    elif n_players <= 11:
        return 2
    else:
        return max(3, n_players // 4)

def is_big_match(n_players: int) -> bool:
    return n_players >= BIG_MATCH_PLAYER_THRESHOLD

def short_id(n=6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))

def gen_task() -> Tuple[str, str]:
    t = random.choice(["reverse", "odd", "math"])
    if t == "reverse":
        seq = "".join(random.choices("0123456789", k=4))
        prompt = f"Memory: {seq} | Reply the reverse."
        answer = seq[::-1]
        return (prompt, answer)
    if t == "odd":
        words = random.sample(["red", "blue", "green", "cat", "yellow"], 4)
        if "cat" not in words:
            words[0] = "cat"
        prompt = f"Odd-one-out: {', '.join(words)}"
        answer = "cat"
        return (prompt, answer)
    a, b = random.randint(2,9), random.randint(2,9)
    prompt = f"Compute: {a} + {b} = ?"
    answer = str(a+b)
    return (prompt, answer)

# -----------------
# Commands: core
# -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    p = store.pget(user.id)
    p["username"] = user.username
    p["last_seen"] = now_ts()
    await update.message.reply_text(
        "Welcome to Agent vs Mole!\n"
        "Use /host (group) to create lobby, /join to join, /startgame to start.\n"
        "Use /balance to check your Nano/Mega currency."
    )

async def host(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == Chat.PRIVATE:
        await update.message.reply_text("Use /host in a group.")
        return
    g = store.gget(chat.id)
    if g["phase"] != "idle":
        await update.message.reply_text("Already a lobby/game active here.")
        return
    g["lobby"] = []
    g["phase"] = "lobby"
    g["host"] = update.effective_user.id
    await update.message.reply_text(f"Lobby created. Host: {mention(update.effective_user)}\nPlayers: /join")

async def join_lobby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == Chat.PRIVATE:
        await update.message.reply_text("Use /join in a group lobby.")
        return
    g = store.gget(chat.id)
    if g["phase"] != "lobby":
        await update.message.reply_text("No active lobby. Host one with /host")
        return
    if user.id in g["lobby"]:
        await update.message.reply_text("You are already in the lobby.")
        return
    g["lobby"].append(user.id)
    store.pget(user.id)["username"] = user.username
    await update.message.reply_text(f"{mention(user)} joined. Players: {len(g['lobby'])}")

async def startgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == Chat.PRIVATE:
        await update.message.reply_text("Start games in groups.")
        return
    g = store.gget(chat.id)
    if g["phase"] != "lobby":
        await update.message.reply_text("No lobby to start.")
        return
    if g["host"] != user.id:
        await update.message.reply_text("Only host can start.")
        return
    if len(g["lobby"]) < 2:
        await update.message.reply_text("Need at least 2 players.")
        return

    players = list(g["lobby"])
    random.shuffle(players)
    n = len(players)
    n_moles = choose_moles(n)
    mole_ids = set(random.sample(players, k=n_moles))
    roles = {}
    for pid in players:
        roles[pid] = "mole" if pid in mole_ids else "agent"

    g["roles"] = roles
    g["alive"] = players.copy()
    g["eliminated"] = []
    g["round"] = 1
    g["phase"] = "task"
    g["votes"] = {}
    g["tasks"] = {}
    g["game_started_ts"] = now_ts()
    g["last_summary"] = ""

    # assign tasks & DM
    for pid in players:
        store.pget(pid)  # ensure exists
        if roles[pid] == "agent":
            assigned = []
            for _ in range(2):
                prompt, answer = gen_task()
                assigned.append({"id": short_id(), "prompt": prompt, "answer": answer, "done": False})
            g["tasks"][str(pid)] = {"assigned": assigned}
            try:
                await context.bot.send_message(pid,
                    f"🔐 You are AGENT.\nComplete tasks within {TASK_PHASE_SECONDS}s.\n" +
                    "\n".join([f"{t['id']}: {t['prompt']}" for t in assigned]) +
                    "\nReply using: /solve <task_id> <answer>"
                )
            except Exception:
                pass
        else:
            try:
                await context.bot.send_message(pid,
                    f"🕵️ You are MOLE.\nYou can /sabotage <@username> once per round to reshuffle a task."
                )
            except Exception:
                pass

    await update.message.reply_text(f"Game started! Players: {n} | Moles: {n_moles}\nTask phase {TASK_PHASE_SECONDS}s started.")
    context.job_queue.run_once(end_task_phase, TASK_PHASE_SECONDS, data={"chat_id": chat.id}, name=f"task_{chat.id}_{g['round']}")

# /solve for tasks (DM from user)
async def solve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /solve <task_id> <answer>")
        return
    task_id = args[0].strip()
    answer = " ".join(args[1:]).strip()

    # find game where user is alive & in task phase
    gid = None
    for gid_s, gdata in store.data["groups"].items():
        gdata_phase = gdata.get("phase")
        if gdata_phase == "task" and user.id in gdata.get("alive", []):
            gid = int(gid_s)
            break
    if not gid:
        await update.message.reply_text("No active task-phase game found where you are alive.")
        return
    g = store.gget(gid)
    if g["roles"].get(user.id) != "agent":
        await update.message.reply_text("Only Agents have tasks.")
        return
    tasks = g["tasks"].get(str(user.id), {"assigned": []})["assigned"]
    found = None
    for t in tasks:
        if t["id"].lower() == task_id.lower():
            found = t
            break
    if not found:
        await update.message.reply_text("Invalid task id.")
        return
    if found["done"]:
        await update.message.reply_text("Task already completed.")
        return
    if answer.strip().lower() == found["answer"].strip().lower():
        found["done"] = True
        await update.message.reply_text("✅ Correct! Task completed.")
    else:
        await update.message.reply_text("❌ Wrong answer.")

# /sabotage for Mole
async def sabotage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /sabotage <@username or user_id>")
        return
    # find game
    gid = None
    for gid_s, gdata in store.data["groups"].items():
        if gdata.get("phase") == "task" and user.id in gdata.get("alive", []):
            gid = int(gid_s)
            break
    if not gid:
        await update.message.reply_text("No active task-phase or you are not in one.")
        return
    g = store.gget(gid)
    if g["roles"].get(user.id) != "mole":
        await update.message.reply_text("Only Moles can sabotage.")
        return

    target = args[0]
    target_id = None
    if target.startswith("@"):
        uname = target[1:].lower()
        for uid_s, pdata in store.data["players"].items():
            if pdata.get("username") and pdata["username"].lower() == uname:
                target_id = int(uid_s)
                break
    else:
        try:
            target_id = int(target)
        except:
            pass

    if not target_id or target_id not in g["alive"] or g["roles"].get(target_id) != "agent":
        await update.message.reply_text("Target must be alive Agent in this game.")
        return

    tag = f"sab_{user.id}_{g['round']}"
    if tag in g["last_summary"]:
        await update.message.reply_text("You already sabotaged this round.")
        return

    tasks = g["tasks"].get(str(target_id), {"assigned": []})["assigned"]
    idx_choices = [i for i,t in enumerate(tasks) if not t["done"]]
    if not idx_choices:
        await update.message.reply_text("Target has no incomplete tasks.")
        return
    idx = random.choice(idx_choices)
    new_prompt, new_answer = gen_task()
    tasks[idx] = {"id": short_id(), "prompt": new_prompt, "answer": new_answer, "done": False}
    g["last_summary"] += " " + tag

    await update.message.reply_text("Sabotage applied.")
    try:
        await context.bot.send_message(target_id, f"⚠️ A sabotage changed one of your tasks. Check DM for updated tasks.")
    except:
        pass

# end of task phase -> meeting
async def end_task_phase(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data["chat_id"]
    g = store.gget(chat_id)
    if g["phase"] != "task":
        return
    total = 0
    done = 0
    for uid in g["alive"]:
        if g["roles"].get(uid) == "agent":
            tasks = g["tasks"].get(str(uid), {"assigned": []})["assigned"]
            for t in tasks:
                total += 1
                if t["done"]:
                    done += 1
    g["phase"] = "meeting"
    g["votes"] = {}
    g["meeting_caller"] = "auto"
    prog = f"{done}/{total}" if total else "0/0"
    msg = (f"⏰ Task phase over.\nRound {g['round']} — Tasks completed: {prog}\n"
           f"Meeting open for {MEETING_SECONDS}s. Discuss or /report to call meeting.")
    await context.bot.send_message(chat_id, msg)
    context.job_queue.run_once(start_vote_phase, MEETING_SECONDS, data={"chat_id": chat_id}, name=f"meet_{chat_id}_{g['round']}")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    g = store.gget(chat.id)
    if g["phase"] not in ("task", "meeting"):
        await update.message.reply_text("You can only report during Task/Meeting.")
        return
    g["phase"] = "meeting"
    g["meeting_caller"] = update.effective_user.id
    await update.message.reply_text(f"📣 Meeting called by {mention(update.effective_user)}. Discussion for {MEETING_SECONDS}s.")
    context.job_queue.run_once(start_vote_phase, MEETING_SECONDS, data={"chat_id": chat.id}, name=f"meet_{chat.id}_{g['round']}")

async def start_vote_phase(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data["chat_id"]
    g = store.gget(chat_id)
    if g["phase"] != "meeting":
        return
    g["phase"] = "vote"
    g["votes"] = {}
    buttons = []
    for uid in g["alive"]:
        buttons.append([InlineKeyboardButton(f"Vote {uid}", callback_data=f"vote:{uid}")])
    buttons.append([InlineKeyboardButton("Skip", callback_data="vote:skip")])
    kb = InlineKeyboardMarkup(buttons)
    await context.bot.send_message(chat_id, f"🗳️ Voting open for {VOTE_SECONDS}s. Tap to vote.", reply_markup=kb)
    context.job_queue.run_once(end_vote_phase, VOTE_SECONDS, data={"chat_id": chat_id}, name=f"vote_{chat_id}_{g['round']}")

async def on_vote_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith("vote:"):
        return
    chat = query.message.chat
    g = store.gget(chat.id)
    if g["phase"] != "vote":
        await query.edit_message_text("Voting is not active.")
        return
    voter = query.from_user.id
    if voter not in g["alive"]:
        await query.answer("You are not alive.")
        return
    target = data.split(":")[1]
    g["votes"][str(voter)] = None if target == "skip" else int(target)
    await query.answer("Vote recorded.")

async def end_vote_phase(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data["chat_id"]
    g = store.gget(chat_id)
    if g["phase"] != "vote":
        return
    tally = {}
    for v, t in g["votes"].items():
        if t is None:
            continue
        tally[t] = tally.get(t, 0) + 1
    if tally:
        eliminated = max(tally.items(), key=lambda x: x[1])[0]
        if eliminated in g["alive"]:
            g["alive"].remove(eliminated)
            g["eliminated"].append(eliminated)
            role = g["roles"].get(eliminated)
            await context.bot.send_message(chat_id, f"☠️ Player {eliminated} eliminated. ({role.upper()})")
    else:
        await context.bot.send_message(chat_id, "No votes cast. No one eliminated.")

    agents = [uid for uid in g["alive"] if g["roles"].get(uid) == "agent"]
    moles = [uid for uid in g["alive"] if g["roles"].get(uid) == "mole"]

    if not moles:
        await handle_end_game(context, chat_id, winners="agents")
        return
    if len(moles) >= len(agents):
        await handle_end_game(context, chat_id, winners="moles")
        return

    # next round
    g["round"] += 1
    g["phase"] = "task"
    g["last_summary"] = ""
    g["tasks"] = {}
    for uid in g["alive"]:
        if g["roles"].get(uid) == "agent":
            assigned = []
            for _ in range(2):
                prompt, answer = gen_task()
                assigned.append({"id": short_id(), "prompt": prompt, "answer": answer, "done": False})
            g["tasks"][str(uid)] = {"assigned": assigned}
            try:
                await context.bot.send_message(uid,
                    f"Round {g['round']} — New tasks ({TASK_PHASE_SECONDS}s):\n" +
                    "\n".join([f"{t['id']}: {t['prompt']}" for t in assigned]) +
                    "\nReply: /solve <task_id> <answer>"
                )
            except:
                pass
        else:
            try:
                await context.bot.send_message(uid, f"Round {g['round']} — You are MOLE. Use /sabotage once.")
            except:
                pass
    await context.bot.send_message(chat_id, f"➡️ Next Task phase started ({TASK_PHASE_SECONDS}s).")
    context.job_queue.run_once(end_task_phase, TASK_PHASE_SECONDS, data={"chat_id": chat_id}, name=f"task_{chat_id}_{g['round']}")

# End game handler & reward distribution
async def handle_end_game(context: ContextTypes.DEFAULT_TYPE, chat_id: int, winners: str):
    g = store.gget(chat_id)
    g["phase"] = "finished"
    agents_all = [uid for uid,r in g["roles"].items() if r=="agent"]
    moles_all = [uid for uid,r in g["roles"].items() if r=="mole"]
    if winners == "agents":
        winners_list = agents_all
        losers = moles_all
        win_text = "Agents win! 🎉"
    else:
        winners_list = moles_all
        losers = agents_all
        win_text = "Moles win! 🕵️"

    big = is_big_match(len(g["roles"]))
    reward_msg = []
    if big:
        for uid in winners_list:
            p = store.pget(uid)
            p["mega"] += MEGA_REWARD_BIG_WIN
            p["wins"] += 1
            store.add_global_score(uid, 100)
        for uid in losers:
            p = store.pget(uid)
            p["losses"] += 1
            store.add_global_score(uid, 10)
        reward_msg.append(f"Winners received +{MEGA_REWARD_BIG_WIN} Mega each (big match).")
    else:
        for uid in winners_list:
            p = store.pget(uid)
            p["nano"] += NANO_REWARD_SMALL_WIN
            p["wins"] += 1
            store.add_global_score(uid, 60)
        for uid in losers:
            p = store.pget(uid)
            p["nano"] += NANO_REWARD_SMALL_CONSOLE
            p["losses"] += 1
            store.add_global_score(uid, 15)
        reward_msg.append(f"Winners +{NANO_REWARD_SMALL_WIN} Nano | Losers +{NANO_REWARD_SMALL_CONSOLE} Nano (small match).")

    await context.bot.send_message(chat_id, f"🏁 Game Over — {win_text}\n" + "\n".join(reward_msg) +
                                   f"\n1 Mega = {MEGA_VALUE_IN_NANO} Nano\nUse /balance to check your balance.")

    # reset group
    g["lobby"] = []
    g["phase"] = "idle"
    g["roles"] = {}
    g["alive"] = []
    g["eliminated"] = []
    g["votes"] = {}
    g["tasks"] = {}
    g["meeting_caller"] = None

# -----------------
# Economy commands
# -----------------
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    p = store.pget(uid)
    await update.message.reply_text(f"💰 Your Balance:\nNano: {p['nano']}\nMega: {p['mega']}")

# NEW: Level command (derived from globalscore)
async def level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    p = store.pget(uid)
    xp = store.data.get("globalscore", {}).get(str(uid), 0)
    level_val = xp // 100 + 1  # 100 XP per level
    await update.message.reply_text(
        f"⭐ Level: {level_val}\n"
        f"XP: {xp}\n"
        f"Wins: {p['wins']} | Losses: {p['losses']}"
    )

# profile (detailed)
async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    p = store.pget(u.id)
    inv_names = [SHOP_ITEMS[i][0] for i in p["inventory"] if i in SHOP_ITEMS]
    await update.message.reply_text(
        f"👤 {mention(u)}\nNano: {p['nano']} | Mega: {p['mega']}\nWins: {p['wins']} | Losses: {p['losses']}\n"
        f"Characters: {', '.join(inv_names) if inv_names else '—'}"
    )

async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["🛍️ Shop:"]
    for iid,(name,price,cur) in SHOP_ITEMS.items():
        lines.append(f"{iid}: {name} — {price} {cur}")
    lines.append("Buy: /buy <item_id>")
    await update.message.reply_text("\n".join(lines))

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /buy <item_id>")
        return
    iid = args[0].upper()
    if iid not in SHOP_ITEMS:
        await update.message.reply_text("Invalid item id.")
        return
    name, price, cur = SHOP_ITEMS[iid]
    p = store.pget(u.id)
    if cur == "nano":
        if p["nano"] < price:
            await update.message.reply_text("Not enough Nano.")
            return
        p["nano"] -= price
    else:
        if p["mega"] < price:
            await update.message.reply_text("Not enough Mega.")
            return
        p["mega"] -= price
    p["inventory"].append(iid)
    await update.message.reply_text(f"✅ Purchased {name}.")
# ----------------
# Coin Transfer Commands
# ----------------

async def megagive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /megagive <username> <amount>")
        return
    
    username = context.args[0]
    try:
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Amount must be a number.")
        return

    sender = str(update.effective_user.id)
    receiver = username.replace("@", "")

    if sender not in store["users"] or store["users"][sender]["mega"] < amount:
        await update.message.reply_text("You don’t have enough Mega Coins!")
        return

    if receiver not in store["users"]:
        store["users"][receiver] = {"mega": 0, "nano": 0, "level": 1}

    store["users"][sender]["mega"] -= amount
    store["users"][receiver]["mega"] += amount

    await update.message.reply_text(f"✅ Sent {amount} Mega Coins to @{receiver}!")

async def nanogive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /nanogive <username> <amount>")
        return
    
    username = context.args[0]
    try:
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Amount must be a number.")
        return

    sender = str(update.effective_user.id)
    receiver = username.replace("@", "")

    if sender not in store["users"] or store["users"][sender]["nano"] < amount:
        await update.message.reply_text("You don’t have enough Nano Coins!")
        return

    if receiver not in store["users"]:
        store["users"][receiver] = {"mega": 0, "nano": 0, "level": 1}

    store["users"][sender]["nano"] -= amount
    store["users"][receiver]["nano"] += amount

    await update.message.reply_text(f"✅ Sent {amount} Nano Coins to @{receiver}!")
async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    p = store.pget(u.id)
    if not p["inventory"]:
        await update.message.reply_text("Inventory empty.")
        return
    lines = ["🎒 Inventory:"]
    for iid in p["inventory"]:
        lines.append(f"{iid} — {SHOP_ITEMS.get(iid,('Unknown',0))[0]}")
    lines.append("Gift: reply to a user message and use /gift <item_id>")
    await update.message.reply_text("\n".join(lines))

async def gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to the recipient's message and use /gift <item_id>")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /gift <item_id>")
        return
    iid = args[0].upper()
    sender = store.pget(u.id)
    if iid not in sender["inventory"]:
        await update.message.reply_text("You don't own that item.")
        return
    target_id = update.message.reply_to_message.from_user.id
    receiver = store.pget(target_id)
    sender["inventory"].remove(iid)
    receiver["inventory"].append(iid)
    await update.message.reply_text(f"🎁 Gifted {SHOP_ITEMS[iid][0]} to {receiver.get('username') or target_id}.")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # simple local/global approximation: sort players by wins*100 + nano + mega*MEGA_VALUE_IN_NANO
    scores = []
    for uid_s in store.data["players"].keys():
        uid = int(uid_s)
        p = store.pget(uid)
        score = p["wins"]*100 + p["nano"] + p["mega"]*MEGA_VALUE_IN_NANO
        scores.append((score, uid))
    scores.sort(reverse=True)
    top = scores[:10]
    lines = ["📊 Top 10 Players:"]
    for i,(sc, uid) in enumerate(top, start=1):
        p = store.pget(uid)
        lines.append(f"{i}. {p.get('username') or uid} — {sc}")
    await update.message.reply_text("\n".join(lines))

async def globalboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gsc = store.data.get("globalscore", {})
    pairs = [(sc, int(uid)) for uid, sc in gsc.items()]
    pairs.sort(reverse=True)
    top = pairs[:10]
    lines = ["🌍 Global Leaderboard:"]
    for i,(sc, uid) in enumerate(top, start=1):
        p = store.pget(uid)
        lines.append(f"{i}. {p.get('username') or uid} — {sc}")
    if len(lines)==1:
        lines.append("No scores yet.")
    await update.message.reply_text("\n".join(lines))

# Fallback unknown
async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Unknown command.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
start - Start the bot
host - Host a new lobby
join - Join an existing lobby
startgame - Start the hosted game
report - Report a player
solve - Solve a task
sabotage - Sabotage the game
balance - Check your coin balance
level - Show your current level
profile - Show your profile
shop - Open the shop
buy - Buy an item from the shop
inventory - Show your inventory
gift - Gift an item to another player
leaderboard - Show the local leaderboard
globalboard - Show the global leaderboard
megagive - Give MegaCoins to a player
nanogive - Give NanoCoins to a player
"""
    await update.message.reply_text(help_text)

# -----------------
# Startup & main
# -----------------
async def _post_init(app):
    # start periodic saver as background task (works even if job_queue is None)
    app.create_task(store.save_loop())

async def _post_shutdown(app):
    # make sure we flush to disk on shutdown/redeploy
    await store.save()

def main():
    if BOT_TOKEN.startswith("PASTE") or not BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN in the script.")
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    # game commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("host", host))
    app.add_handler(CommandHandler("join", join_lobby))
    app.add_handler(CommandHandler("startgame", startgame))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("solve", solve))
    app.add_handler(CommandHandler("sabotage", sabotage))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("level", level))  # <-- Level command
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("shop", shop))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("inventory", inventory))
    app.add_handler(CommandHandler("gift", gift))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("globalboard", globalboard))
    app.add_handler(CommandHandler("megagive", megagive))
    app.add_handler(CommandHandler("nanogive", nanogive))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(on_vote_button))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    print("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()
