import json
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = "8408074707:AAEe7miladjgSS4RyoxDNHFTdtgqfIJ0Fvc"
DATA_FILE = "data.json"

# Load data from file
def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

# Save data to file
def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(users, f, indent=4)

# Initialize user
def init_user(username):
    if username not in users:
        users[username] = {"nano": 0, "mega": 0, "level": 1}
    save_data()

# Load all users
users = load_data()

# Special user setup
SPECIAL_USER = "@Nightking1515"
if SPECIAL_USER not in users:
    users[SPECIAL_USER] = {"nano": 100000000000, "mega": 1000000000, "level": 300}
    save_data()

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = f"@{update.effective_user.username}" if update.effective_user.username else str(update.effective_user.id)
    init_user(username)
    await update.message.reply_text("Welcome to the Anime Bot! Use /balance, /level, /megagive, /nanogive")

# /balance command
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = f"@{update.effective_user.username}" if update.effective_user.username else str(update.effective_user.id)
    init_user(username)
    user = users[username]
    await update.message.reply_text(f"💰 Balance for {username}\nNano: {user['nano']}\nMega: {user['mega']}")

# /level command
async def level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = f"@{update.effective_user.username}" if update.effective_user.username else str(update.effective_user.id)
    init_user(username)
    user = users[username]
    await update.message.reply_text(f"🏆 {username}'s Level: {user['level']}")

# /megagive command
async def megagive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = f"@{update.effective_user.username}" if update.effective_user.username else str(update.effective_user.id)
    init_user(username)

    if len(context.args) != 2:
        await update.message.reply_text("Usage: /megagive <username> <amount>")
        return

    target = context.args[0]
    amount = int(context.args[1])

    if users[username]["mega"] < amount:
        await update.message.reply_text("❌ Not enough Mega coins!")
        return

    init_user(target)
    users[username]["mega"] -= amount
    users[target]["mega"] += amount
    save_data()
    await update.message.reply_text(f"✅ {amount} Mega coins sent to {target}")

# /nanogive command
async def nanogive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = f"@{update.effective_user.username}" if update.effective_user.username else str(update.effective_user.id)
    init_user(username)

    if len(context.args) != 2:
        await update.message.reply_text("Usage: /nanogive <username> <amount>")
        return

    target = context.args[0]
    amount = int(context.args[1])

    if users[username]["nano"] < amount:
        await update.message.reply_text("❌ Not enough Nano coins!")
        return

    init_user(target)
    users[username]["nano"] -= amount
    users[target]["nano"] += amount
    save_data()
    await update.message.reply_text(f"✅ {amount} Nano coins sent to {target}")

# Main function
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("level", level))
    app.add_handler(CommandHandler("megagive", megagive))
    app.add_handler(CommandHandler("nanogive", nanogive))
    app.run_polling()

if __name__ == "__main__":
    main()
