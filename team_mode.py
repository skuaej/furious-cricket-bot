import html, random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import update_user, find_user_by_username, get_user

team_lobbies = {}

def get_lobby(chat_id):
    return team_lobbies.get(chat_id)

def _new_lobby(host_id):
    return {
        "host_id": host_id, "phase": "setup",
        "team_a": [], "team_b": [],
        "cap_a": None, "cap_b": None,
        "overs": 6, "toss_winner": None,
        "batting_team": None, "bowling_team": None, "inning": 1,
        "current_bowler": None, "striker": None, "non_striker": None,
        "balls_in_over": 0,
        "team_a_score": {"runs": 0, "wickets": 0, "balls": 0},
        "team_b_score": {"runs": 0, "wickets": 0, "balls": 0},
        "player_stats": {},
        "delivery": {"bowler_num": None, "status": "waiting_bowler"},
        "batter_warnings": 0, "dismissed": [],
    }

def _init_player(lobby, uid, username=None, first_name=None):
    uid_str = str(uid)
    if username: username = username.lstrip("@").lower()
    if uid_str not in lobby["player_stats"]:
        lobby["player_stats"][uid_str] = {
            "username": username,
            "first_name": first_name or f"Player {uid}",
            "runs": 0, "balls": 0, "fours": 0, "sixes": 0,
            "bat_hist": [], "is_out": False,
            "wickets": 0, "runs_given": 0, "bowl_hist": []
        }
    else:
        if username: lobby["player_stats"][uid_str]["username"] = username
        if first_name: lobby["player_stats"][uid_str]["first_name"] = first_name

async def _resolve_target(update, context):
    """Extremely robust user resolution (Reply > Entities > Database > Lobby)."""
    chat_id = update.effective_chat.id
    text = update.message.text or ""
    res = None
    
    # 1. Priority: Reply
    if update.message.reply_to_message:
        u = update.message.reply_to_message.from_user
        res = (u.id, u.first_name)

    # 2. Priority: Telegram Mention Entities (@username or text_mention)
    if not res and update.message.entities:
        for ent in update.message.entities:
            if ent.type == "text_mention" and ent.user:
                res = (ent.user.id, ent.user.first_name); break
            if ent.type == "mention":
                m_str = text[ent.offset:ent.offset+ent.length].lstrip("@").lower()
                db_u = await find_user_by_username(m_str)
                if db_u:
                    un = db_u.get("username")
                    res = (db_u["user_id"], f"@{un}" if un else db_u.get("first_name")); break
                lobby = get_lobby(chat_id)
                if lobby:
                    for uid_str, s in lobby["player_stats"].items():
                        if s.get("username", "").lower() == m_str:
                            un = s.get("username")
                            res = (int(uid_str), f"@{un}" if un else s["first_name"]); break
            if res: break

    # 3. Priority: Direct Arguments (Plain text or ID)
    if not res and context.args:
        first_arg = context.args[0].lstrip("@")
        if first_arg.isdigit():
            if len(first_arg) <= 2:
                idx = int(first_arg) - 1
                lobby = get_lobby(chat_id)
                if lobby:
                    combined = lobby["team_a"] + lobby["team_b"]
                    if 0 <= idx < len(combined):
                        tid = combined[idx]
                        s = lobby["player_stats"].get(str(tid), {})
                        res = (tid, s.get("first_name", f"Player {tid}"))
            
            if not res:
                uid = int(first_arg)
                try:
                    m = await context.bot.get_chat_member(chat_id, uid)
                    res = (m.user.id, m.user.first_name)
                except:
                    res = (uid, f"User {uid}")

        if not res:
            full_query = " ".join(context.args).lstrip("@").lower()
            db_u = await find_user_by_username(full_query)
            if db_u:
                un = db_u.get("username")
                res = (db_u["user_id"], f"@{un}" if un else db_u.get("first_name"))

        if not res:
            lobby = get_lobby(chat_id)
            if lobby:
                for uid_str, s in lobby["player_stats"].items():
                    un = s.get("username", "").lower()
                    fn = s.get("first_name", "").lower()
                    if full_query == un or full_query == fn or full_query in fn:
                        res = (int(uid_str), s["first_name"]); break

    # Final Bot Check
    if res and res[0]:
        try:
            m = await context.bot.get_chat_member(chat_id, res[0])
            if m.user.is_bot: return None, "Bots not allowed"
        except: pass
    return res if res else (None, "Unknown")

async def _resolve_all_targets(update, context):
    """Returns a list of (user_id, first_name) from reply, all mentions, and args."""
    chat_id = update.effective_chat.id
    results = []
    # 1. Reply
    if update.message.reply_to_message:
        u = update.message.reply_to_message.from_user
        results.append((u.id, u.first_name))
    # 2. Entities
    if update.message.entities:
        for ent in update.message.entities:
            if ent.type == "text_mention" and ent.user:
                results.append((ent.user.id, ent.user.first_name))
            if ent.type == "mention":
                m = update.message.text[ent.offset:ent.offset+ent.length].lstrip("@").lower()
                # 1. Search Lobby
                found = False
                lobby = get_lobby(chat_id)
                if lobby:
                    for uid_str, s in lobby["player_stats"].items():
                        if s.get("username", "").lower() == m:
                            results.append((int(uid_str), s["first_name"]))
                            found = True; break
                # 2. Search Database
                if not found:
                    db_u = await find_user_by_username(m)
                    if db_u:
                        un = db_u.get("username")
                        results.append((db_u["user_id"], f"@{un}" if un else db_u.get("first_name")))
    # 3. Args
    if context.args:
        for arg in context.args:
            a = arg.lstrip("@").lower()
            if a.isdigit():
                # Index check
                if len(a) <= 2:
                    idx = int(a) - 1
                    lobby = get_lobby(chat_id)
                    if lobby:
                        combined = lobby["team_a"] + lobby["team_b"]
                        if 0 <= idx < len(combined):
                            tid = combined[idx]
                            s = lobby["player_stats"].get(str(tid), {})
                            results.append((tid, s.get("first_name", f"Player {tid}")))
                            continue
                
                # Direct ID
                uid = int(a)
                try:
                    m = await context.bot.get_chat_member(chat_id, uid)
                    if not m.user.is_bot:
                        results.append((m.user.id, m.user.first_name))
                except:
                    results.append((uid, f"User {uid}"))
            else:
                # Search Lobby
                found = False
                lobby = get_lobby(chat_id)
                if lobby:
                    for uid_str, s in lobby["player_stats"].items():
                        if s.get("username", "").lower() == a or s.get("first_name", "").lower() == a:
                            results.append((int(uid_str), s["first_name"]))
                            found = True; break
                # Search Database
                if not found:
                    db_u = await find_user_by_username(a)
                    if db_u:
                        results.append((db_u["user_id"], db_u.get("first_name") or f"@{db_u['username']}"))
    
    seen = set(); final = []
    for uid, name in results:
        if uid and uid not in seen:
            seen.add(uid); final.append((uid, name))
    return final

# --- /host_claim ---
async def _host_claim(update, context):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    tid, tname = await _resolve_target(update, context)
    if not tid:
        await update.message.reply_text("Reply to a user or provide @username/ID.")
        return
    if chat_id not in team_lobbies:
        team_lobbies[chat_id] = _new_lobby(tid)
    else:
        team_lobbies[chat_id]["host_id"] = tid
    await update.message.reply_text(
        f"🖼 <b>{html.escape(tname)}</b> is now the game host!\nUse /create_team to begin. 🏏",
        parse_mode="HTML")

# ─── /create_team ───
async def create_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    try: await update.message.delete()
    except: pass
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can create teams.")
        return
    if lobby["phase"] != "setup":
        await update.message.reply_text("Teams already created!")
        return
    lobby["phase"] = "joining_a"
    await update.message.reply_text(
        "🎉 Team creation underway!\n📣 Join <b>Team A</b> → /join_teamA\n⏳ <b>1 minute</b> to join!",
        parse_mode="HTML")
    context.job_queue.run_once(_close_team_a, 60, chat_id=chat_id, name=f"teamA_{chat_id}")
    try: await update.message.delete()
    except: pass

async def _close_team_a(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    lobby = get_lobby(chat_id)
    if not lobby or lobby["phase"] != "joining_a":
        return
    lobby["phase"] = "joining_b"
    await context.bot.send_message(chat_id,
        "⏰ Time's up for Team A!\n📣 Join <b>Team B</b> → /join_teamB\n⏳ <b>1 minute</b> to join!",
        parse_mode="HTML")
    context.job_queue.run_once(_close_team_b, 60, chat_id=chat_id, name=f"teamB_{chat_id}")

async def _close_team_b(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    lobby = get_lobby(chat_id)
    if not lobby or lobby["phase"] != "joining_b":
        return
    lobby["phase"] = "captain"
    host = await context.bot.get_chat_member(chat_id, lobby["host_id"])
    hname = html.escape(host.user.first_name)
    await context.bot.send_message(chat_id,
        f"👋 <b>{hname}</b>, members joined!\n🔵 Team A: {len(lobby['team_a'])} | 🔴 Team B: {len(lobby['team_b'])}\n\n"
        f"Set captains:\n/addcap_a @user → Team A\n/addcap_b @user → Team B",
        parse_mode="HTML")

# ─── /join_teamA ───
async def join_team_a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    try: await update.message.delete()
    except: pass
    lobby = get_lobby(chat_id)
    if not lobby or lobby["phase"] != "joining_a":
        await update.message.reply_text("Team A joining not open.")
        return
    if user.is_bot:
        await update.message.reply_text("❌ Bots cannot join teams!"); return
    if user.id in lobby["team_a"] or user.id in lobby["team_b"]:
        await update.message.reply_text("You already joined a team!")
        return
    lobby["team_a"].append(user.id)
    _init_player(lobby, user.id, user.username, user.first_name)
    await get_user(user.id, user.username, user.first_name)
    await update.message.reply_text(f"✈️ <b>{html.escape(user.first_name)}</b> joined <b>Team A</b>!", parse_mode="HTML")
    try: await update.message.delete()
    except: pass

# ─── /join_teamB ───
async def join_team_b(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    try: await update.message.delete()
    except: pass
    lobby = get_lobby(chat_id)
    if not lobby or lobby["phase"] != "joining_b":
        await update.message.reply_text("Team B joining not open.")
        return
    if user.is_bot:
        await update.message.reply_text("❌ Bots cannot join teams!"); return
    if user.id in lobby["team_a"] or user.id in lobby["team_b"]:
        await update.message.reply_text("You already joined a team!")
        return
    lobby["team_b"].append(user.id)
    _init_player(lobby, user.id, user.username, user.first_name)
    await get_user(user.id, user.username, user.first_name)
    await update.message.reply_text(f"🚀 <b>{html.escape(user.first_name)}</b> joined <b>Team B</b>!", parse_mode="HTML")
    try: await update.message.delete()
    except: pass

# ─── /add_a /add_b ───
async def add_to_a(update, context): await _host_add(update, context, "a")
async def add_to_b(update, context): await _host_add(update, context, "b")

async def _host_add(update, context, team):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    try: await update.message.delete()
    except: pass
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can add players."); return
    targets = await _resolve_all_targets(update, context)
    if not targets or targets[0][0] is None:
        await update.message.reply_text("❌ User not found. <b>TIP:</b> Reply to their message with /add or have them type something in the group first.", parse_mode="HTML"); return
    
    added_names = []
    for tid, tname in targets:
        # Prevent same player in both teams
        if team == "a" and tid in lobby["team_b"]: lobby["team_b"].remove(tid)
        if team == "b" and tid in lobby["team_a"]: lobby["team_a"].remove(tid)
        
        target_list = lobby["team_a"] if team == "a" else lobby["team_b"]
        if tid not in target_list:
            # Final verification of player type
            try:
                m = await context.bot.get_chat_member(chat_id, tid)
                if m.user.is_bot: continue
            except: pass
            
            target_list.append(tid)
            pure_name = tname.lstrip("@")
            _init_player(lobby, tid, pure_name if tname.startswith("@") else None, tname)
            added_names.append(tname)
    
    if added_names:
        await update.message.reply_text(f"✅ Added to Team {'A' if team=='a' else 'B'}: {', '.join(added_names)}", parse_mode="HTML")
    else:
        await update.message.reply_text("Players already in that team.")

# ─── /remove_a /remove_b ───
async def remove_from_a(update, context): await _host_remove(update, context, "a")
async def remove_from_b(update, context): await _host_remove(update, context, "b")

async def _host_remove(update, context, team):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    try: await update.message.delete()
    except: pass
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can remove players."); return
    targets = await _resolve_all_targets(update, context)
    if not targets or targets[0][0] is None:
        await update.message.reply_text("❌ User not found. <b>TIP:</b> Reply to their message with the command for 100% success.", parse_mode="HTML"); return
    
    removed_names = []
    target_list = lobby["team_a"] if team == "a" else lobby["team_b"]
    for tid, tname in targets:
        if tid in target_list:
            target_list.remove(tid)
            removed_names.append(tname)
    
    if removed_names:
        await update.message.reply_text(f"❌ Removed from Team {'A' if team=='a' else 'B'}: {', '.join(removed_names)}", parse_mode="HTML")
    else:
        await update.message.reply_text(f"Those players were not in Team {'A' if team=='a' else 'B'}.")

# ─── /addcap_a /addcap_b ───
async def addcap_a(update, context): await _set_captain(update, context, "a")
async def addcap_b(update, context): await _set_captain(update, context, "b")

async def _set_captain(update, context, team):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    try: await update.message.delete()
    except: pass
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can set captains."); return
    tid, tname = await _resolve_target(update, context)
    if not tid:
        await update.message.reply_text("❌ User not found. <b>TIP:</b> Reply to their message with /addcap.", parse_mode="HTML"); return
    team_list = lobby["team_a"] if team == "a" else lobby["team_b"]
    if tid not in team_list:
        team_list.append(tid)
        _init_player(lobby, tid)
    if team == "a": lobby["cap_a"] = tid
    else: lobby["cap_b"] = tid
    await update.message.reply_text(f"🎩 <b>{html.escape(tname)}</b> is now captain of Team {'A' if team=='a' else 'B'}!", parse_mode="HTML")

# ─── /remove_cap_a /remove_cap_b ───
async def remove_cap_a(update, context): await _remove_captain(update, context, "a")
async def remove_cap_b(update, context): await _remove_captain(update, context, "b")

async def _remove_captain(update, context, team):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    try: await update.message.delete()
    except: pass
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can remove captains."); return
    key = "cap_a" if team == "a" else "cap_b"
    lobby[key] = None
    await update.message.reply_text(f"✅ Team {'A' if team=='a' else 'B'} captain removed.")

# ─── /toss ───
async def toss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    try: await update.message.delete()
    except: pass
    lobby = get_lobby(chat_id)
    if not lobby:
        await update.message.reply_text("❌ No active team session. Use /play first."); return
    if lobby["host_id"] != uid:
        cm = await context.bot.get_chat_member(chat_id, uid)
        if cm.status not in ['administrator', 'creator']:
            await update.message.reply_text("❌ Only the host or admins can do the toss."); return
    if not lobby["cap_a"] or not lobby["cap_b"]:
        await update.message.reply_text("⚠️ Set both captains first:\n/addcap_a @user\n/addcap_b @user"); return
    result = random.choice(["Heads", "Tails"])
    winner_team = random.choice(["a", "b"])
    lobby["toss_winner"] = winner_team
    lobby["toss_result"] = result
    cap_id = lobby["cap_a"] if winner_team == "a" else lobby["cap_b"]
    try:
        cap = await context.bot.get_chat_member(chat_id, cap_id)
        cap_name = html.escape(cap.user.first_name)
    except Exception:
        cap_name = f"Captain (ID:{cap_id})"
    t_name = winner_team.upper()
    kb = [[InlineKeyboardButton("🏏 Bat First", callback_data=f"toss_bat_{winner_team}"),
           InlineKeyboardButton("🎯 Bowl First", callback_data=f"toss_bowl_{winner_team}")]]
    await update.message.reply_text(
        f"🪙 The coin shows: <b>{result}!</b>\n\n"
        f"🏆 Team {t_name} (<b>{cap_name}</b>) won the toss!\n"
        f"<b>{cap_name}</b>, choose Bat or Bowl:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def toss_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # must be first
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby:
        await query.edit_message_text("No active match."); return
    winner_team = lobby.get("toss_winner")
    if not winner_team:
        await query.edit_message_text("Toss error — run /toss again."); return
    cap_id = int(lobby["cap_a"] if winner_team == "a" else lobby["cap_b"])
    host_id = int(lobby["host_id"])
    if int(uid) != host_id:
        await context.bot.send_message(chat_id, "⚠️ Only the host can choose Bat or Bowl!")
        return
    # callback data format: toss_bat_a or toss_bowl_b
    parts = query.data.split("_")  # ["toss", "bat", "a"] or ["toss", "bowl", "b"]
    choice = parts[1]  # "bat" or "bowl"
    if choice == "bat":
        lobby["batting_team"] = winner_team
        lobby["bowling_team"] = "b" if winner_team == "a" else "a"
    else:
        lobby["bowling_team"] = winner_team
        lobby["batting_team"] = "b" if winner_team == "a" else "a"
    bat_t = lobby["batting_team"].upper()
    bowl_t = lobby["bowling_team"].upper()
    try:
        cap_name = html.escape((await context.bot.get_chat_member(chat_id, cap_id)).user.first_name)
    except:
        cap_name = f"Captain"
    lobby["phase"] = "overs"
    await query.edit_message_text(
        f"🏏 <b>{cap_name}</b> chose to <b>{'Bat' if choice=='bat' else 'Bowl'}</b> first.\n\n"
        f"🏏 Batting: Team {bat_t}\n🧤 Bowling: Team {bowl_t}\n\n"
        f"📣 Host: /setovers &lt;number&gt; (e.g. /setovers 6)",
        parse_mode="HTML")

# ─── /setovers ───
async def setovers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    try: await update.message.delete()
    except: pass
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can set overs."); return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /setovers <number>"); return
    n = int(context.args[0])
    if n < 1 or n > 50:
        await update.message.reply_text("Overs must be 1-50."); return
    lobby["overs"] = n
    lobby["phase"] = "ready"
    await update.message.reply_text(f"🎉 OHOO! Let's play a <b>{n} overs</b> Match!!\n📣 Host, use /play_team to start!", parse_mode="HTML")

# ─── /member_list ───
async def member_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try: await update.message.delete()
    except: pass
    lobby = get_lobby(chat_id)
    if not lobby:
        await update.message.reply_text("No active team match."); return
    
    bat_t = (lobby.get("batting_team") or "None").upper()
    bowl_t = (lobby.get("bowling_team") or "None").upper()
    inning = lobby.get("inning", "None")
    
    # Host
    try:
        host_m = await context.bot.get_chat_member(chat_id, lobby["host_id"])
        host_name = html.escape(host_m.user.first_name)
        host_tag = host_name
    except:
        host_tag = "Host"
    
    # Cap names
    async def cap_name(uid):
        if not uid: return "Not set"
        try:
            m = await context.bot.get_chat_member(chat_id, uid)
            return html.escape(m.user.first_name)
        except: return f"Player {uid}"
    
    cap_a = await cap_name(lobby["cap_a"])
    cap_b = await cap_name(lobby["cap_b"])
    
    striker = lobby.get("striker")
    non_striker = lobby.get("non_striker")
    cur_bowler = lobby.get("current_bowler")
    dismissed = lobby.get("dismissed", [])
    
    lines = [
        f"👽 <b>Game Host:</b> {host_tag}\n\n",
        f"🏏 Batting: Team {bat_t} (Innings {inning})\n",
        f"🎯 Bowling: Team {bowl_t}\n\n",
        f"🎩 Team A: {cap_a}\n",
        f"👒 Team B: {cap_b}\n\n",
        f"🔵 <b>Team A</b>\n",
    ]
    
    for i, uid in enumerate(lobby["team_a"], 1):
        try:
            m = await context.bot.get_chat_member(chat_id, uid)
            display = html.escape(m.user.first_name)
        except:
            display = f"Player {uid}"
        
        cap_mark = " [🧢]" if uid == lobby["cap_a"] else ""
        if uid in dismissed:
            status = " ❌ (Out)"
        else:
            status = " ✅ (Not Out)"
        
        # Add icons for current batter/bowler
        if uid == striker or uid == non_striker:
            status += " 🏏"
        elif uid == cur_bowler:
            status += " 🎯"
            
        lines.append(f"{i}. <b>{display}</b>{cap_mark} — {status}\n")
    
    lines.append(f"\n🔴 <b>Team B</b>\n")
    
    for i, uid in enumerate(lobby["team_b"], 1):
        try:
            m = await context.bot.get_chat_member(chat_id, uid)
            display = html.escape(m.user.first_name)
        except:
            display = f"Player {uid}"
        
        cap_mark = " [🧢]" if uid == lobby["cap_b"] else ""
        if uid in dismissed:
            status = " ❌ (Out)"
        else:
            status = " ✅ (Not Out)"
            
        if uid == striker or uid == non_striker:
            status += " 🏏"
        elif uid == cur_bowler:
            status += " 🎯"
            
        lines.append(f"{i}. <b>{display}</b>{cap_mark} — {status}\n")
    
    await update.message.reply_text("".join(lines), parse_mode="HTML")

# ─── /end_team ───
async def end_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    try: await update.message.delete()
    except: pass
    lobby = get_lobby(chat_id)
    if not lobby:
        await update.message.reply_text("No active team match."); return
    if lobby["host_id"] != uid:
        cm = await context.bot.get_chat_member(chat_id, uid)
        if cm.status not in ['administrator', 'creator']:
            await update.message.reply_text("❌ Only the host or admins can end the team match."); return
    kb = [[InlineKeyboardButton("✅ YES, END", callback_data="tend_yes"),
           InlineKeyboardButton("❌ NO", callback_data="tend_no")]]
    await update.message.reply_text(
        "❓ <b>End the team match?</b> This cannot be undone.",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def confirm_end_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby:
        await query.edit_message_text("No active match."); return
    
    if query.data == "tclaim_host":
        lobby["host_id"] = uid
        name = html.escape(update.effective_user.first_name)
        await query.edit_message_text(
            f"👑 <b>{name}</b> is now the game host!\nUse /create_team to start. 🏏",
            parse_mode="HTML")
        return

    if lobby["host_id"] != uid:
        cm = await context.bot.get_chat_member(chat_id, uid)
        if cm.status not in ['administrator', 'creator']:
            await query.answer("Only host/admin can confirm.", show_alert=True); return
    if query.data == "tend_yes":
        del team_lobbies[chat_id]
        await query.edit_message_text("🏁 Team match ended by host/admin.")
    else:
        await query.edit_message_text("❌ Cancelled. Match continues!")

async def hostchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lobby = get_lobby(chat_id)
    if not lobby:
        await update.message.reply_text("No active team match."); return
    
    # Check if user is current host or admin
    uid = update.effective_user.id
    cm = await context.bot.get_chat_member(chat_id, uid)
    if lobby["host_id"] != uid and cm.status not in ['administrator', 'creator']:
        await update.message.reply_text("❌ Only the host or admins can change the host."); return

    # Resolve target correctly (unpack tuple)
    tid, tname = await _resolve_target(update, context)
    if not tid:
        await update.message.reply_text("❌ Usage: /hostchange @username or reply to a message"); return
        
    lobby["host_id"] = tid
    tname_esc = html.escape(tname)
    tag = f'<a href="tg://user?id={tid}">{tname_esc}</a>'
        
    await update.message.reply_text(f"👑 Host changed! {tag} is now the game host.", parse_mode="HTML")
