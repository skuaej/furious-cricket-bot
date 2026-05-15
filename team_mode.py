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
        "batter_warnings": 0, "bowler_warnings": 0, "dismissed": [],
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
            "wickets": 0, "runs_given": 0, "bowl_hist": [], "bowl_results": []
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

    return res or (None, None)

async def _get_name(context, chat_id, uid, fallback="Player"):
    try:
        u = await get_user(uid)
        return html.escape(u.get("first_name") or u.get("username") or f"{fallback} {uid}")
    except:
        return f"{fallback} {uid}"

async def _announce_crease(chat_id, context, lobby):
    sid = lobby["striker"]; nsid = lobby["non_striker"]
    s_name = await _get_name(context, chat_id, sid, "Striker")
    ns_name = await _get_name(context, chat_id, nsid, "Non-Striker")
    
    # Hide bowler number from group
    msg = (
        f"🏏 <b>Batting Crease:</b>\n"
        f"🔴 Striker: <b>{s_name}</b>\n"
        f"⚪ Non-striker: <b>{ns_name}</b>\n\n"
        f"🔢 Bowler, send your ball (1-6) in DM!"
    )
    await context.bot.send_message(chat_id, msg, parse_mode="HTML")

# ─── /create_team ───
async def create_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can create teams."); return
    if lobby["phase"] != "setup":
        await update.message.reply_text("Teams already created!"); return
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
    lobby = get_lobby(chat_id)
    if not lobby or lobby["phase"] != "joining_a":
        await update.message.reply_text("Team A joining not open."); return
    if user.is_bot:
        await update.message.reply_text("❌ Bots cannot join teams!"); return
    if user.id in lobby["team_a"] or user.id in lobby["team_b"]:
        await update.message.reply_text("You already joined a team!"); return
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
    lobby = get_lobby(chat_id)
    if not lobby or lobby["phase"] != "joining_b":
        await update.message.reply_text("Team B joining not open."); return
    if user.is_bot:
        await update.message.reply_text("❌ Bots cannot join teams!"); return
    if user.id in lobby["team_a"] or user.id in lobby["team_b"]:
        await update.message.reply_text("You already joined a team!"); return
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
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can add players."); return
    
    tid, tname = await _resolve_target(update, context)
    if not tid:
        await update.message.reply_text("❌ User not found. Tag them or reply."); return
    
    if tid in lobby["team_a"] or tid in lobby["team_b"]:
        await update.message.reply_text("User already in a team."); return
        
    lobby[f"team_{team}"].append(tid)
    _init_player(lobby, tid, None, tname)
    await update.message.reply_text(f"✅ {html.escape(tname)} added to Team {team.upper()}.")
    try: await update.message.delete()
    except: pass

# ─── /remove_a /remove_b ───
async def remove_from_a(update, context): await _host_remove(update, context, "a")
async def remove_from_b(update, context): await _host_remove(update, context, "b")

async def _host_remove(update, context, team):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can remove players."); return
    
    tid, tname = await _resolve_target(update, context)
    if not tid:
        await update.message.reply_text("❌ User not found."); return
        
    if tid in lobby[f"team_{team}"]:
        lobby[f"team_{team}"].remove(tid)
        await update.message.reply_text(f"✅ {html.escape(tname)} removed from Team {team.upper()}.")
    else:
        await update.message.reply_text("User not in this team.")
    try: await update.message.delete()
    except: pass

# ─── /addcap_a /addcap_b ───
async def addcap_a(update, context): await _set_captain(update, context, "a")
async def addcap_b(update, context): await _set_captain(update, context, "b")

async def _set_captain(update, context, team):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can set captains."); return
    
    tid, tname = await _resolve_target(update, context)
    if not tid or tid not in lobby[f"team_{team}"]:
        await update.message.reply_text("User must be in the team to be captain."); return
        
    lobby[f"cap_{team}"] = tid
    await update.message.reply_text(f"👑 {html.escape(tname)} is now Captain of Team {team.upper()}!")
    
    if lobby.get("cap_a") and lobby.get("cap_b"):
        await update.message.reply_text(
            "✅ <b>Both captains are set!</b>\nHost, use /toss to decide the match preference.",
            parse_mode="HTML")
    
    try: await update.message.delete()
    except: pass

async def remove_cap_a(update, context): await _remove_captain(update, context, "a")
async def remove_cap_b(update, context): await _remove_captain(update, context, "b")

async def _remove_captain(update, context, team):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can remove captains."); return
        
    lobby[f"cap_{team}"] = None
    await update.message.reply_text(f"✅ Captain removed from Team {team.upper()}.")
    try: await update.message.delete()
    except: pass

# ─── /toss ───
async def toss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby:
        await update.message.reply_text("❌ No active team session. Use /play first."); return
    if lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can start the toss."); return
    if not lobby["cap_a"] or not lobby["cap_b"]:
        await update.message.reply_text("❌ Set captains for both teams first!"); return
        
    winner = random.choice(["a", "b"])
    lobby["toss_winner"] = winner
    lobby["phase"] = "toss_choice"
    
    cap_id = lobby[f"cap_{winner}"]
    cap_name = await _get_name(context, chat_id, cap_id, "Captain")
    
    kb = [[InlineKeyboardButton("🏏 Batting", callback_data="toss_bat"),
           InlineKeyboardButton("⚾ Bowling", callback_data="toss_bowl")]]
    await update.message.reply_text(
        f"🪙 <b>Toss Result:</b> Team {winner.upper()} won the toss!\n\n"
        f"👑 <b>Host</b>, choose preference:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    try: await update.message.delete()
    except: pass

async def toss_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = team_lobbies.get(chat_id)
    
    if not lobby:
        try: await query.answer("❌ No active lobby found. Use /play.", show_alert=True)
        except: pass
        return

    # Handle Batting/Bowling selection
    if query.data in ["toss_bat", "toss_bowl"]:
        # Use int() to ensure comparison works
        if int(uid) != int(lobby["host_id"]):
            try: await query.answer("❌ Only the Host can choose the preference!", show_alert=True)
            except: pass
            return
            
        winner = lobby.get("toss_winner")
        if not winner:
            # Fallback if toss data was lost
            winner = random.choice(["a", "b"])
            lobby["toss_winner"] = winner
            
        try: await query.answer()
        except: pass
        
        choice = "batting" if query.data == "toss_bat" else "bowling"
        if choice == "batting":
            lobby["batting_team"] = winner
            lobby["bowling_team"] = "b" if winner == "a" else "a"
        else:
            lobby["bowling_team"] = winner
            lobby["batting_team"] = "b" if winner == "a" else "a"
            
        lobby["phase"] = "overs"
        await query.edit_message_text(
            f"✅ Team {winner.upper()} chose to <b>{choice}</b> first!\n\n"
            f"👋 Host, set match overs using /setovers <num>", parse_mode="HTML")
        return

    # Fallback for older buttons
    try: await query.answer()
    except: pass

# ─── /setovers ───
async def setovers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can set overs."); return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /setovers <number>"); return
        
    num = int(context.args[0])
    if not (1 <= num <= 50):
        await update.message.reply_text("Overs must be between 1 and 50."); return
        
    lobby["overs"] = num
    await update.message.reply_text(f"✅ Match set for <b>{num} overs</b>.\nUse /play_team to start!", parse_mode="HTML")
    try: await update.message.delete()
    except: pass

# ─── /member_list ───
async def member_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lobby = get_lobby(chat_id)
    if not lobby:
        await update.message.reply_text("No active team match."); return
        
    lines = [f"👥 <b>Team Match Lobby</b>\n\n"]
    for team in ["a", "b"]:
        lines.append(f"<b>Team {team.upper()}:</b>\n")
        players = lobby[f"team_{team}"]
        if not players:
            lines.append(" (Empty)\n")
        for i, uid in enumerate(players, 1):
            s = lobby["player_stats"].get(str(uid), {})
            display = html.escape(s.get("first_name", f"Player {uid}"))
            cap_mark = " (C) 👑" if uid == lobby[f"cap_{team}"] else ""
            status = "❌" if s.get("is_out") else "🟢"
            if uid == lobby["striker"]:
                status += " 🏏"
            elif uid == lobby["current_bowler"]:
                status += " 🎯"
                
            lines.append(f"{i}. <b>{display}</b>{cap_mark} — {status}\n")
        lines.append("\n")
        
    await update.message.reply_text("".join(lines), parse_mode="HTML")
    try: await update.message.delete()
    except: pass

# ─── /end_team ───
async def end_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby:
        await update.message.reply_text("No active team match."); return
    
    # Host or Admin can end
    if lobby["host_id"] != uid:
        cm = await context.bot.get_chat_member(chat_id, uid)
        if cm.status not in ['administrator', 'creator']:
            await update.message.reply_text("❌ Only the host or admins can end the match."); return
            
    kb = [[InlineKeyboardButton("✅ YES, END", callback_data="tend_yes"),
           InlineKeyboardButton("❌ NO", callback_data="tend_no")]]
    await update.message.reply_text(
        "❓ <b>End the team match?</b> This cannot be undone.",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    try: await update.message.delete()
    except: pass

async def confirm_end_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby:
        try: await query.edit_message_text("No active match."); return
        except: return
    
    if query.data == "tclaim_host":
        lobby["host_id"] = uid
        name = html.escape(update.effective_user.first_name)
        text = f"👑 <b>{name}</b> is now the game host!\nUse /create_team to start. 🏏"
        try:
            await query.edit_message_text(text, parse_mode="HTML")
        except:
            try: await query.edit_message_caption(caption=text, parse_mode="HTML")
            except: await context.bot.send_message(chat_id, text, parse_mode="HTML")
        return

    # Check permissions for end match
    if lobby["host_id"] != uid:
        cm = await context.bot.get_chat_member(chat_id, uid)
        if cm.status not in ['administrator', 'creator']:
            await query.answer("Only host/admin can confirm.", show_alert=True); return
            
    if query.data == "tend_yes":
        del team_lobbies[chat_id]
        text = "🏁 Team match ended by host/admin."
    else:
        text = "❌ Cancelled. Match continues!"
    
    try:
        await query.edit_message_text(text)
    except:
        try: await query.edit_message_caption(caption=text)
        except: await context.bot.send_message(chat_id, text)

# ─── Host Management ───
async def hostchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby:
        await update.message.reply_text("❌ No active team match."); return
    if lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can transfer host status."); return
    
    tid, tname = await _resolve_target(update, context)
    if not tid:
        kb = [[InlineKeyboardButton("👑 Claim Host", callback_data="tclaim_host")]]
        await update.message.reply_text("👑 <b>Host Transfer!</b> Click below to claim the host position:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    else:
        lobby["host_id"] = tid
        tname_esc = html.escape(tname)
        tag = f'<a href="tg://user?id={tid}">{tname_esc}</a>'
        await update.message.reply_text(f"👑 Host changed! {tag} is now the game host.", parse_mode="HTML")
    
    try: await update.message.delete()
    except: pass

async def vote4host_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby:
        await update.message.reply_text("❌ No active team match."); return
    
    if "host_votes" not in lobby: lobby["host_votes"] = set()
    lobby["host_votes"].add(uid)
    
    count = len(lobby["host_votes"])
    if count >= 2:
        lobby["host_votes"] = set() # Reset
        kb = [[InlineKeyboardButton("👑 Claim Host", callback_data="tclaim_host")]]
        await update.message.reply_text(
            "🗳 <b>Votes Complete!</b>\nThe host position is now open for anyone to claim.",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    else:
        kb = [[InlineKeyboardButton(f"🗳 Vote for Host Change ({count}/2)", callback_data="tvote_host")]]
        await update.message.reply_text(
            f"🗳 <b>Host Change Request!</b>\nNeed <b>2 votes</b> to open the host position.",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    
    try: await update.message.delete()
    except: pass

async def host_vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby:
        await query.answer("No active lobby.", show_alert=True); return
    
    if "host_votes" not in lobby: lobby["host_votes"] = set()
    if uid in lobby["host_votes"]:
        await query.answer("You already voted!", show_alert=True); return
        
    lobby["host_votes"].add(uid)
    count = len(lobby["host_votes"])
    
    if count >= 2:
        lobby["host_votes"] = set()
        kb = [[InlineKeyboardButton("👑 Claim Host", callback_data="tclaim_host")]]
        await query.edit_message_text(
            "🗳 <b>Votes Complete!</b>\nThe host position is now open for anyone to claim.",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    else:
        kb = [[InlineKeyboardButton(f"🗳 Vote for Host Change ({count}/2)", callback_data="tvote_host")]]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))
    await query.answer("Vote counted!")

async def hostchange_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Wrapper for consistency
    await hostchange(update, context)
