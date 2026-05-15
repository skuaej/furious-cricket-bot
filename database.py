import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
client = AsyncIOMotorClient(MONGO_URI)
db = client['cricket_legacy']
users_col = db['users']
matches_col = db['matches']
settings_col = db['settings']

async def get_sudo_users():
    settings = await settings_col.find_one({"type": "sudo_users"})
    if not settings:
        return []
    return settings.get("user_ids", [])

async def add_sudo_user(user_id):
    await settings_col.update_one(
        {"type": "sudo_users"},
        {"$addToSet": {"user_ids": user_id}},
        upsert=True
    )

async def remove_sudo_user(user_id):
    await settings_col.update_one(
        {"type": "sudo_users"},
        {"$pull": {"user_ids": user_id}}
    )

async def get_user(user_id, username=None, first_name=None):
    user = await users_col.find_one({"user_id": user_id})
    if not user:
        user = {
            "user_id": user_id,
            "username": username.lower() if username else "",
            "first_name": first_name or "",
            "total_runs": 0, "total_wickets": 0, "matches_played": 0, "wins": 0,
            "achievements": []
        }
        await users_col.insert_one(user)
    else:
        updates = {}
        if username and user.get("username") != username.lower():
            updates["username"] = username.lower()
            user["username"] = username.lower()
        if first_name and user.get("first_name") != first_name:
            updates["first_name"] = first_name
            user["first_name"] = first_name
        if updates:
            await users_col.update_one({"user_id": user_id}, {"$set": updates})
    return user

async def update_user(user_id, data):
    await users_col.update_one({"user_id": user_id}, {"$set": data}, upsert=True)

async def create_match(match_id, lobby_players):
    # Build per-player scoreboard with detailed tracking
    scoreboard = {}
    for uid in lobby_players:
        scoreboard[str(uid)] = {
            "runs": 0,
            "balls_faced": 0,
            "fours": 0,
            "sixes": 0,
            "is_out": False,
            "bat_history": [],      # list of shot numbers or "W" for out
            "balls_bowled": 0,
            "runs_given": 0,
            "wickets_taken": 0,
            "bowl_history": [],     # list of ball numbers bowled
            "bowl_count_this_turn": 0,  # resets when bowler rotates
            "bowl_results": [],        # list of results (e.g. 'W', '0', '1', etc)
        }

    match = {
        "match_id": match_id,
        "lobby_players": lobby_players,
        "current_batsman": None,
        "current_bowler": None,
        "bowler_index": 1,          # index in lobby_players for next bowler
        "scoreboard": scoreboard,
        "match_status": "Lobby",
        "current_delivery": {
            "bowler_num": None,
            "status": "waiting_bowler"
        },
        "batter_timeout_count": 0,  # consecutive timeouts for current batter
        "bowler_timeout_count": 0,  # consecutive timeouts for current bowler
    }
    # Remove old match for this chat if any
    await matches_col.delete_many({"match_id": match_id})
    await matches_col.insert_one(match)
    return match

async def get_match(match_id):
    return await matches_col.find_one({"match_id": match_id})

async def update_match(match_id, data):
    await matches_col.update_one({"match_id": match_id}, {"$set": data})

async def end_match(match_id):
    await matches_col.update_one({"match_id": match_id}, {"$set": {"match_status": "Ended"}})

async def find_user_by_username(username):
    un = username.lstrip("@").lower()
    return await users_col.find_one({"username": {"$regex": f"^{un}$", "$options": "i"}})
