import html, random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji
from telegram.ext import ContextTypes
from team_mode import team_lobbies, get_lobby, _init_player, _resolve_target

def _cancel_team_jobs(chat_id, context):
    for n in [f"tbowl_{chat_id}", f"tbat_{chat_id}"]:
        for j in context.job_queue.get_jobs_by_name(n): j.schedule_removal()

async def _get_name(context, chat_id, uid, default="Player"):
    try: return html.escape((await context.bot.get_chat_member(chat_id, uid)).user.first_name)
    except: return default

COMMENTARY = {
    0: ["A solid defensive stroke. (Dot Ball)", "No run there, straight to the fielder. (Dot Ball)", "Dot ball! Building pressure.", "Well played, but no run. (Dot Ball)", "Deadly dot ball!", "The bowler is keeping it tight. (Dot Ball)", "Straight into the pads, no run taken.", "Beaten! That was close. (Dot Ball)"],
    1: ["Just a single, keeps the strike rotating.", "Pushed into the gap for one.", "Easy run, well judged.", "A quick single taken.", "Tapped and ran for one.", "The fielder does well but they get a single.", "Just a nudge for a run.", "One run added to the total."],
    2: ["Excellent running between the wickets for two!", "Driven through the covers for a couple.", "They take two! Good hustle.", "Nicely placed for a double.", "That's two! Great work in the deep.", "Two runs! The pressure is on.", "They race back for the second run.", "Classic placement for two."],
    3: ["Superb placement! They race back for the third.", "Deep into the outfield, three runs taken.", "Magnificent running! That's three.", "They scamper through for three!", "Three runs! That's brilliant running.", "Exhausting but they got three!", "Fielding error allows a third run."],
    4: ["CRACKED away for FOUR! 🏏", "Pure class! The ball races to the boundary.", "Beautifully timed! That's a boundary.", "Four runs! What a magnificent shot!", "A perfect drive for four!", "Boundary! The crowd is loving it.", "Right in the gap, four runs!", "Timed to perfection for four."],
    5: ["Overthrows! A rare five runs for the batting side.", "Five runs! Chaos in the field.", "Unbelievable! They get five runs!", "Messy fielding results in five runs."],
    6: ["HUUUGE! That's out of the park! SIX! 🚀", "Maximum! A monstrous hit!", "Into the stands! What a shot!", "Cleared the ropes with ease! SIX!", "That's going, going... GONE! SIX!", "A massive hit over cow-corner! SIX!", "Total destruction! That's a six!", "High and handsome! Six runs!"],
    "W": ["BOWLED HIM! A massive breakthrough! ☝️", "OUT! The finger goes up!", "WICKET! A huge blow for the batting side!", "Caught! That's the end of the innings for him.", "Stunned silence! He's out.", "Big wicket! The bowler is delighted.", "A perfect delivery! Out!", "The stumps are rattled! He's gone."]
}

def get_commentary(num):
    return random.choice(COMMENTARY.get(num, ["Nice shot!"]))

async def _announce_crease(chat_id, context, lobby):
    """Announce striker, non-striker and bowler clearly."""
    sid = lobby["striker"]; nsid = lobby["non_striker"]; bid = lobby["current_bowler"]
    s_tag = f"<a href='tg://user?id={sid}'>{await _get_name(context, chat_id, sid)}</a>" if sid else "None"
    ns_tag = f"<a href='tg://user?id={nsid}'>{await _get_name(context, chat_id, nsid)}</a>" if nsid else "None"
    b_tag = f"<a href='tg://user?id={bid}'>{await _get_name(context, chat_id, bid)}</a>" if bid else "None"
    
    sa, sb = lobby["team_a_score"], lobby["team_b_score"]
    r1, w1, b1 = sa["runs"], sa["wickets"], sa["balls"]
    r2, w2, b2 = sb["runs"], sb["wickets"], sb["balls"]
    
    chase_info = ""
    if lobby["inning"] == 2:
        target = lobby[f"team_{lobby['bowling_team']}_score"]["runs"] + 1
        curr_runs = lobby[f"team_{lobby['batting_team']}_score"]["runs"]
        curr_balls = lobby[f"team_{lobby['batting_team']}_score"]["balls"]
        total_balls = lobby["overs"] * 6
        runs_left = target - curr_runs
        balls_left = total_balls - curr_balls
        chase_info = f"🎯 <b>Target: {target}</b> | 🏃 <b>{runs_left} runs left</b> from <b>{balls_left} balls</b>\n\n"

    bot_info = await context.bot.get_me()
    dm_link = f"https://t.me/{bot_info.username}"
    kb = [[InlineKeyboardButton("📩 Send Bowl in DM", url=dm_link)]]
    
    # Hidden tags for all players in lobby
    all_players = lobby["team_a"] + lobby["team_b"]
    tags = " ".join([f"<a href='tg://user?id={p}'>\u200b</a>" for p in all_players])

    msg = (
        f"🏏 <b>At the crease:</b>\n"
        f"🔴 Striker: <b>{s_tag}</b>\n"
        f"⚪ Non-Striker: <b>{ns_tag}</b>\n"
        f"🎯 Bowling: <b>{b_tag}</b>\n\n"
        f"👥 <b>Team A Score:</b> {r1}/{w1} ({b1//6}.{b1%6} ov)\n"
        f"👥 <b>Team B Score:</b> {r2}/{w2} ({b2//6}.{b2%6} ov)\n"
        f"⏳ <b>Match Overs: {lobby['overs']}</b>\n\n"
        f"{chase_info}"
        f"🎯 <b>{b_tag}</b> Send number 1-6 in DM!\n"
        f"🏏 <b>{s_tag}</b> Send 0-6 in group\n{tags}"
    )

    await context.bot.send_message(chat_id, msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    # Start Bowler Timeout Countdown
    context.job_queue.run_once(_bowl_timeout_team, 30, chat_id=chat_id, data={"time_left": 30}, name=f"tbowl_{chat_id}")

# ─── /play_team ───
async def play_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can start."); return
    if lobby["phase"] != "ready":
        await update.message.reply_text("⚠️ Complete: /create_team → /toss → /setovers first."); return
    if len(lobby["team_a"]) < 1 or len(lobby["team_b"]) < 1:
        await update.message.reply_text("⚠️ Both teams need at least 1 player!"); return
    lobby["phase"] = "live_1st"
    bat_t = lobby["batting_team"].upper()
    bowl_t = lobby["bowling_team"].upper()
    bat_cap_id = lobby["cap_a"] if lobby["batting_team"] == "a" else lobby["cap_b"]
    bowl_cap_id = lobby["cap_a"] if lobby["bowling_team"] == "a" else lobby["cap_b"]
    bat_cap_name = await _get_name(context, chat_id, bat_cap_id, "Batting Captain")
    bowl_cap_name = await _get_name(context, chat_id, bowl_cap_id, "Bowling Captain")
    await update.message.reply_text(
        f"🏏 <b>Team {bat_t} bats first!</b>\n🎯 Team {bowl_t} bowls!\n\n"
        f"📣 <b>{bat_cap_name}</b>: choose opener 1 → /batting @user\n"
        f"📣 <b>{bowl_cap_name}</b>: choose bowler → /bowling @user",
        parse_mode="HTML")

# ─── /bowling ───
async def bowling(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby or lobby["phase"] not in ("live_1st", "live_2nd"): return
    bowl_cap_id = lobby["cap_a"] if lobby["bowling_team"] == "a" else lobby["cap_b"]
    host_id = lobby["host_id"]
    if int(uid) != int(bowl_cap_id or 0) and int(uid) != int(host_id):
        await update.message.reply_text("❌ Only the bowling captain or host can choose the bowler."); return
    tid, tname = await _resolve_target(update, context)
    if not tid:
        await update.message.reply_text("Reply to player or use @username/ID."); return
    bowl_team_name = "Team A" if lobby["bowling_team"] == "a" else "Team B"
    bowl_team = lobby["team_a"] if lobby["bowling_team"] == "a" else lobby["team_b"]
    if tid not in bowl_team:
        await update.message.reply_text(f"❌ <b>{html.escape(tname)}</b> is not in <b>{bowl_team_name}</b> (the current bowling team)!", parse_mode="HTML")
        return
    lobby["current_bowler"] = tid
    lobby["balls_in_over"] = 0
    lobby["delivery"] = {"bowler_num": None, "status": "waiting_bowler"}
    await update.message.reply_text(f"🎯 <b>{html.escape(tname)}</b> is bowling!", parse_mode="HTML")
    if lobby["striker"] and lobby["non_striker"]:
        await _announce_crease(chat_id, context, lobby)

# ─── /batting ───
async def batting_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby or lobby["phase"] not in ("live_1st", "live_2nd"): return
    bat_cap_id = lobby["cap_a"] if lobby["batting_team"] == "a" else lobby["cap_b"]
    host_id = lobby["host_id"]
    if int(uid) != int(bat_cap_id or 0) and int(uid) != int(host_id):
        await update.message.reply_text("❌ Only the batting captain or host can choose batters."); return
    tid, tname = await _resolve_target(update, context)
    if not tid:
        await update.message.reply_text("Reply to player or use @username/ID."); return
    bat_team_name = "Team A" if lobby["batting_team"] == "a" else "Team B"
    bat_team = lobby["team_a"] if lobby["batting_team"] == "a" else lobby["team_b"]
    if tid not in bat_team:
        await update.message.reply_text(f"❌ <b>{html.escape(tname)}</b> is not in <b>{bat_team_name}</b> (the current batting team)!", parse_mode="HTML")
        return
    if tid in lobby.get("dismissed", []):
        await update.message.reply_text("Already out!"); return
    _init_player(lobby, tid)
    tname_esc = html.escape(tname)

    if lobby["striker"] is None:
        lobby["striker"] = tid
        await update.message.reply_text(
            f"🏏 <a href='tg://user?id={tid}'><b>{tname_esc}</b></a> → <b>Striker end</b> 🔴\nNow choose 2nd batter /batting @user", parse_mode="HTML")
    elif lobby["non_striker"] is None:
        if tid == lobby["striker"]:
            await update.message.reply_text("Already set as striker!"); return
        lobby["non_striker"] = tid
        await update.message.reply_text(
            f"⚪ <a href='tg://user?id={tid}'><b>{tname_esc}</b></a> → <b>Non-striker end</b>\n📣 Bowling captain: /bowling @user", parse_mode="HTML")
        # If bowler already set, announce and start
        if lobby["current_bowler"]:
            await _announce_crease(chat_id, context, lobby)
    else:
        # Wicket replacement or error if both alive
        if lobby["striker"] and lobby["non_striker"]:
            await update.message.reply_text("❌ Both batters are currently at the crease. No more batters needed right now!"); return
        
        lobby["striker"] = tid
        await update.message.reply_text(f"🏏 <a href='tg://user?id={tid}'><b>{tname_esc}</b></a> comes in at striker end!", parse_mode="HTML")
        if lobby["current_bowler"]:
            await _announce_crease(chat_id, context, lobby)

# ─── Ball handling ───
async def handle_team_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    text = update.message.text
    if text not in ["0", "1", "2", "3", "4", "5", "6"]:
        return False
    try: num = int(text)
    except: return False
    
    lobby = get_lobby(chat_id)
    if not lobby or lobby["phase"] not in ("live_1st", "live_2nd"): return False
    delivery = lobby["delivery"]

    if uid == lobby["current_bowler"] and delivery["status"] == "waiting_bowler" and 0 <= num <= 6:
        # Warning: Bowler sending in group
        b_name = await _get_name(context, chat_id, uid, "Bowler")
        await context.bot.send_message(chat_id,
            f"❌ <b>{b_name}</b>, you are the <b>Bowler</b>! Send your number in <b>DM</b>, not here.",
            parse_mode="HTML")
        return True # Swallow the message so it's not used as a batter's shot
        
    if uid == lobby["striker"] and delivery["status"] == "waiting_batter" and 0 <= num <= 6:
        _cancel_team_jobs(chat_id, context)
        # 👍 TAG feedback + Reaction
        s_name = await _get_name(context, chat_id, uid)
        try:
            await update.message.set_reaction(reaction=[ReactionTypeEmoji(emoji="👍")])
        except: pass
        
        await update.message.reply_text(
            f"👍 <a href='tg://user?id={uid}'>{s_name}</a>", 
            parse_mode="HTML")
        await _process_ball(update, context, lobby, num)
        return True
    return False

async def _check_milestones_team(chat_id, context, name, score, is_batting=True):
    if is_batting:
        if score == 50:
            await context.bot.send_message(chat_id, f"🔥 <b>HALF CENTURY!</b> {name} reaches <b>50 runs</b>! Great knock! 👏", parse_mode="HTML")
        elif score == 100:
            await context.bot.send_message(chat_id, f"🏆 <b>CENTURY!</b> {name} reaches <b>100 runs</b>! Stunning performance! 👑", parse_mode="HTML")
    else:
        if score == 3:
            await context.bot.send_message(chat_id, f"🎯 <b>3 WICKET HAUL!</b> {name} has taken <b>3 wickets</b>! 🏏", parse_mode="HTML")
        elif score == 5:
            await context.bot.send_message(chat_id, f"🎖 <b>FIVE WICKET HAUL!</b> {name} has claimed <b>5 wickets</b>! 🏅", parse_mode="HTML")

async def _check_hattrick_team(chat_id, context, name, results):
    if len(results) >= 3 and all(r == "W" for r in results[-3:]):
        await context.bot.send_message(chat_id, f"🎩 <b>HAT-TRICK!</b> {name} has taken <b>3 wickets in 3 balls</b>! Unbelievable! ⚡️🏏", parse_mode="HTML")
    elif len(results) >= 2 and all(r == "W" for r in results[-2:]):
        await context.bot.send_message(chat_id, f"🔥 <b>CONSECUTIVE WICKETS!</b> {name} has taken <b>2 wickets in 2 balls</b>! He's on a hat-trick! ⚡️🏏", parse_mode="HTML")

async def _process_ball(update, context, lobby, bat_num):
    chat_id = update.effective_chat.id
    bowl_num = lobby["delivery"]["bowler_num"]
    sid = lobby["striker"]; bid = lobby["current_bowler"]
    sk = str(sid); bk = str(bid)
    bat_key = f"team_{lobby['batting_team']}_score"
    bs = lobby["player_stats"].setdefault(sk, {"runs":0,"balls":0,"fours":0,"sixes":0,"bat_hist":[],"is_out":False,"wickets":0,"runs_given":0,"bowl_hist":[]})
    bws = lobby["player_stats"].setdefault(bk, {"runs":0,"balls":0,"fours":0,"sixes":0,"bat_hist":[],"is_out":False,"wickets":0,"runs_given":0,"bowl_hist":[]})
    bws["bowl_hist"].append(bowl_num)
    lobby[bat_key]["balls"] += 1
    lobby["balls_in_over"] += 1
    s_name = await _get_name(context, chat_id, sid, "Batter")
    b_name = await _get_name(context, chat_id, bid, "Bowler")
    num_emojis = {0: "0️⃣", 1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣"}
    b_emoji = num_emojis.get(bowl_num, str(bowl_num))
    bt_emoji = num_emojis.get(bat_num, str(bat_num))

    # Header like solo (bowler number hidden)
    header = f"<b>{s_name}</b> vs <b>{b_name}</b>\n⚾ BOWL: <b>❓</b> | 🏏 BAT: <b>{bt_emoji}</b>" + (f" (Dot Ball)" if bat_num == 0 else "") + "\n\n"

    if bowl_num == bat_num:
        bs["bat_hist"].append("W"); bs["is_out"] = True
        bws["wickets"] += 1
        bws.setdefault("bowl_results", []).append("W")
        
        # Check milestones
        await _check_hattrick_team(chat_id, context, b_name, bws["bowl_results"])
        await _check_milestones_team(chat_id, context, b_name, bws["wickets"], is_batting=False)
        lobby[bat_key]["wickets"] += 1
        lobby["dismissed"].append(sid)
        lobby["striker"] = None
        lobby["batter_warnings"] = 0
        lobby["bowler_warnings"] = 0
        r, w, b = lobby[bat_key]["runs"], lobby[bat_key]["wickets"], lobby[bat_key]["balls"]
        comm = get_commentary("W")
        await context.bot.send_message(chat_id,
            f"{header}☝️ <b>OUT!</b> {s_name} dismissed!\n\n"
            f"<i>{comm}</i>\n"
            f"Score: <b>{r}/{w}</b> ({b//6}.{b%6} ov)", parse_mode="HTML")
        await _check_next(chat_id, context, lobby, wicket=True)
        return True
    else:
        runs = bat_num
        bs["runs"] += runs; bs["balls"] += 1; bs["bat_hist"].append(runs)
        if runs == 4: bs["fours"] += 1
        if runs == 6: bs["sixes"] += 1
        bws["runs_given"] += runs
        bws.setdefault("bowl_results", []).append(runs)
        
        # Check milestones
        await _check_milestones_team(chat_id, context, s_name, bs["runs"], is_batting=True)
        lobby[bat_key]["runs"] += runs
        lobby["delivery"] = {"bowler_num": None, "status": "waiting_bowler"}
        lobby["batter_warnings"] = 0
        lobby["bowler_warnings"] = 0
        r, w, b = lobby[bat_key]["runs"], lobby[bat_key]["wickets"], lobby[bat_key]["balls"]
        rotate = runs % 2 == 1
        if rotate:
            lobby["striker"], lobby["non_striker"] = lobby["non_striker"], lobby["striker"]
        
        ns_id = lobby["non_striker"]
        ns_name = await _get_name(context, chat_id, ns_id, "")
        s2_id = lobby["striker"]
        s2_name = await _get_name(context, chat_id, s2_id, "")
        
        s_tag = f'<a href="tg://user?id={sid}"><b>{s_name}</b></a>'
        emoji = "🔵" if lobby["batting_team"] == "a" else "🔴"
        comm = get_commentary(runs)
        run_label = "" if runs == 0 else ("🔥 <b>FOUR!</b> " if runs == 4 else ("🏆 <b>SIX!</b> " if runs == 6 else ""))
        run_text = "" if runs >= 4 else f" scores <b>{runs} runs!</b>"
        msg = (f"{header}{emoji} {run_label}{s_tag}{run_text} 👍\n"
               f"━━━━━━━━━━━━━━━\n"
               f"<i>{comm}</i>\n"
               f"{' 🔄 Strike rotated!' if rotate else ''}\n"
               f"Score: <b>{r}/{w}</b> ({b//6}.{b%6} ov)\n"
               f"🔴 Striker: {s2_name} | ⚪ Non-striker: {ns_name}")
        await context.bot.send_message(chat_id, msg, parse_mode="HTML")
        


        await _check_next(chat_id, context, lobby, wicket=False)
        return True

async def _check_next(chat_id, context, lobby, wicket=False):
    bat_key = f"team_{lobby['batting_team']}_score"
    sc = lobby[bat_key]
    bat_list = lobby["team_a"] if lobby["batting_team"] == "a" else lobby["team_b"]
    
    # 2nd Innings Win Check
    if lobby["inning"] == 2:
        first_runs = lobby[f"team_{lobby['bowling_team']}_score"]["runs"]
        if sc["runs"] > first_runs:
            await _end_match(chat_id, context, lobby)
            return

    all_out = len(lobby["dismissed"]) >= len(bat_list) - 1
    overs_done = sc["balls"] >= lobby["overs"] * 6
    balls_in_over = lobby["balls_in_over"]

    if all_out or overs_done:
        if lobby["inning"] == 1: await _end_1st(chat_id, context, lobby)
        else: await _end_match(chat_id, context, lobby)
        return

    if balls_in_over >= 6:
        lobby["balls_in_over"] = 0
        if not wicket:  # rotate strike at over end
            lobby["striker"], lobby["non_striker"] = lobby["non_striker"], lobby["striker"]
        await _over_card(chat_id, context, lobby)
        bowl_cap_id = lobby["cap_a"] if lobby["bowling_team"] == "a" else lobby["cap_b"]
        cap_name = await _get_name(context, chat_id, bowl_cap_id, "Captain")
        await context.bot.send_message(chat_id,
            f"🏁 Over complete!\n📣 <b>{cap_name}</b>: /bowling @user to choose next bowler", parse_mode="HTML")
        return

    if wicket:
        bat_cap_id = lobby["cap_a"] if lobby["batting_team"] == "a" else lobby["cap_b"]
        cap_name = await _get_name(context, chat_id, bat_cap_id, "Captain")
        ns_name = await _get_name(context, chat_id, lobby["non_striker"], "Non-striker")
        await context.bot.send_message(chat_id,
            f"☝️ Wicket! ⚪ {ns_name} moves to striker end.\n"
            f"📣 <b>{cap_name}</b>: /batting @user to send in next batter", parse_mode="HTML")
        # Non-striker becomes striker
        lobby["striker"] = lobby["non_striker"]
        lobby["non_striker"] = None
        return

    await _announce_crease(chat_id, context, lobby)

async def _build_team_scoreboard(chat_id, context, lobby, mode="over"):
    sa, sb_sc = lobby["team_a_score"], lobby["team_b_score"]
    r1, w1, b1 = sa["runs"], sa["wickets"], sa["balls"]
    r2, w2, b2 = sb_sc["runs"], sb_sc["wickets"], sb_sc["balls"]
    
    host_id = lobby["host_id"]
    try:
        hm = await context.bot.get_chat_member(chat_id, host_id)
        hname = html.escape(hm.user.first_name)
    except: hname = "Host"

    if mode == "final":
        lines = [f"╭━─━─━─━─≪✠≫─━─━─━─━╮\n\n",
                 f"───────⊱ Tᴇᴀᴍ - A ⊰──────\n\n"]
    else:
        lines = [f"📊 Game #{str(chat_id).replace('-100', '')} Scoreboard\n\n",
                 f"╭━─━─━─━─≪✠≫─━─━─━─━╮\n\n",
                 f"───────⊱ Tᴇᴀᴍ - A ⊰──────\n\n"]
    
    for uid in lobby["team_a"]:
        s = lobby["player_stats"].get(str(uid), {})
        name = await _get_name(context, chat_id, uid)
        runs = s.get("runs", 0)
        balls = s.get("balls", 0)
        hist_b = s.get("bat_hist", [])
        hist = ", ".join(str(x) for x in hist_b) if hist_b else None
        
        # Out mark: ❌ for out, ✅ for alive
        status_emoji = "❌" if s.get("is_out") else "✅"
        lines.append(f"{status_emoji} {name} = {runs}({balls})\n")
        lines.append(f"  ╰⊚ ID : {uid}\n")
        if hist:
            lines.append(f"    ╰⊚ ({hist})\n")
        lines.append("\n")

    lines.append(f"╭──────── • ◆ • ─────────\n"
                 f"ᴛᴇᴀᴍ A sᴄᴏʀᴇ = {r1}/{w1} ʀᴜɴs | ᴏᴠᴇʀs: {b1//6}.{b1%6}\n"
                 f"╰──────── • ◆ • ─────────\n\n")

    lines.append("× •-•-•-•-•-••-•-•⟮ 🏏 ⟯•-•-•-•-•-•-•-•-• ×\n\n")
    lines.append(f"───────⊱ Tᴇᴀᴍ - B ⊰──────\n\n")
    
    for uid in lobby["team_b"]:
        s = lobby["player_stats"].get(str(uid), {})
        name = await _get_name(context, chat_id, uid)
        runs = s.get("runs", 0)
        balls = s.get("balls", 0)
        hist_b = s.get("bat_hist", [])
        hist = ", ".join(str(x) for x in hist_b) if hist_b else None
        # Out mark: ❌ for out, ✅ for alive
        status_emoji = "❌" if s.get("is_out") else "✅"
        lines.append(f"{status_emoji} {name} = {runs}({balls})\n")
        lines.append(f"  ╰⊚ ID : {uid}\n")
        if hist:
            lines.append(f"    ╰⊚ ({hist})\n")
        lines.append("\n")
        
    lines.append(f"╭──────── • ◆ • ─────────\n"
                 f"ᴛᴇᴀᴍ ʙ sᴄᴏʀᴇ = {r2}/{w2} ʀᴜɴs | ᴏᴠᴇʀs: {b2//6}.{b2%6}\n"
                 f"╰──────── • ◆ • ─────────\n\n")

    lines.append("༺═────────────────═༻\n\n")
    lines.append(f"👑Host: {hname}\n")
    return "".join(lines)

async def _over_card(chat_id, context, lobby):
    sc_lines = await _build_team_scoreboard(chat_id, context, lobby, "over")
    await context.bot.send_message(chat_id, sc_lines, parse_mode="HTML")

async def _end_1st(chat_id, context, lobby):
    lobby["phase"] = "between_innings"
    bat_t = lobby["batting_team"].upper()
    sc = lobby[f"team_{lobby['batting_team']}_score"]
    r, w, b = sc["runs"], sc["wickets"], sc["balls"]
    await _over_card(chat_id, context, lobby)
    host_name = await _get_name(chat_id, lobby["host_id"], "Host")
    await context.bot.send_message(chat_id,
        f"🏁 <b>1st Innings Over!</b>\nTeam {bat_t}: <b>{r}/{w}</b> ({b//6}.{b%6} ov)\n\n"
        f"📣 <b>{host_name}</b>: /swap to start 2nd innings!", parse_mode="HTML")

# ─── /swap ───
async def swap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby:
        await update.message.reply_text("No active match."); return
    if lobby["phase"] != "between_innings":
        await update.message.reply_text("Not the right time!"); return
    lobby["batting_team"], lobby["bowling_team"] = lobby["bowling_team"], lobby["batting_team"]
    lobby.update({"phase":"live_2nd","inning":2,"striker":None,"non_striker":None,
                  "current_bowler":None,"dismissed":[],"balls_in_over":0,
                  "batter_warnings":0,"delivery":{"bowler_num":None,"status":"waiting_bowler"}})
    bat_t = lobby["batting_team"].upper()
    target = lobby[f"team_{lobby['bowling_team']}_score"]["runs"] + 1
    bat_cap_id = lobby["cap_a"] if lobby["batting_team"] == "a" else lobby["cap_b"]
    bowl_cap_id = lobby["cap_a"] if lobby["bowling_team"] == "a" else lobby["cap_b"]
    bc = await _get_name(context, chat_id, bat_cap_id, "Batting Captain")
    wc = await _get_name(context, chat_id, bowl_cap_id, "Bowling Captain")
    await update.message.reply_text(
        f"🔁 <b>2nd Innings!</b>\nTeam {bat_t} needs <b>{target} runs</b> to win!\n\n"
        f"📣 <b>{bc}</b>: /batting @user (openers)\n"
        f"📣 <b>{wc}</b>: /bowling @user", parse_mode="HTML")

async def score_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lobby = get_lobby(chat_id)
    if not lobby or lobby["phase"] not in ("live_1st","live_2nd","between_innings"):
        await update.message.reply_text("No active team match."); return
    
    sa, sb_sc = lobby["team_a_score"], lobby["team_b_score"]
    r1, w1, b1 = sa["runs"], sa["wickets"], sa["balls"]
    r2, w2, b2 = sb_sc["runs"], sb_sc["wickets"], sb_sc["balls"]
    bat_t = lobby["batting_team"]
    bowl_t = lobby["bowling_team"]
    overs = lobby["overs"]
    crr1 = round(r1/(b1/6),2) if b1>0 else 0.0
    crr2 = round(r2/(b2/6),2) if b2>0 else 0.0
    
    host_id = lobby["host_id"]
    try:
        hm = await context.bot.get_chat_member(chat_id, host_id)
        hname = html.escape(hm.user.first_name)
    except: hname = "Host"
    
    s_id = lobby.get("striker")
    ns_id = lobby.get("non_striker")
    bowl_id = lobby.get("current_bowler")
    
    bat_lines = []
    for pid in [s_id, ns_id]:
        if pid:
            s = lobby["player_stats"].get(str(pid), {})
            pname = await _get_name(context, chat_id, pid)
            csr = round((s.get("runs",0)/s.get("balls",1))*100,2) if s.get("balls",0)>0 else 0
            bat_lines.append(f"🏏 {pname} = {s.get('runs',0)}({s.get('balls',0)})\n╰⊚(𝗖𝗦𝗥: {csr})\n")
    
    bowl_line = ""
    if bowl_id:
        bname = await _get_name(context, chat_id, bowl_id)
        bowl_line = f"⚾ {bname}\n"
    
    target_section = ""
    if lobby["inning"] == 2:
        first_sc = lobby[f"team_{bowl_t}_score"]
        target = first_sc["runs"] + 1
        balls_rem = overs*6 - lobby[f"team_{bat_t}_score"]["balls"]
        runs_needed = target - lobby[f"team_{bat_t}_score"]["runs"]
        rrr = round(runs_needed/(balls_rem/6),2) if balls_rem>0 else 0
        target_section = (
            f"────┈┄┄╌╌╌╌┄┄┈────\n"
            f"🎯 𝗧𝗮𝗿𝗴𝗲𝘁: {target} Runs\n"
            f"╰⊚ Remaining: {balls_rem} Balls ({overs}.0 ov)\n"
            f"📈 𝗥𝗥𝗥: {rrr}\n"
            f"────┈┄┄╌╌╌╌┄┄┈────\n"
        )
    
    msg = (
        f"────┈┄┄╌╌╌╌┄┄┈────\n"
        f"𝗕𝗮𝘁𝘁𝗶𝗻𝗴 𝗧𝗲𝗮𝗺 - {bat_t.upper()}\n\n"
        + "".join(bat_lines) +
        f"────┈┄┄╌╌╌╌┄┄┈────\n"
        f"𝗕𝗼𝘄𝗹𝗶𝗻𝗴 𝗧𝗲𝗮𝗺 - {bowl_t.upper()}\n\n"
        + bowl_line +
        f"\n────┈┄┄╌╌╌╌┄┄┈────\n"
        f"👥 𝗧𝗲𝗮𝗺 - A: {r1}/{w1} | {b1//6}.{b1%6} ov\n╰⊚ 𝗖𝗥𝗥: {crr1:.2f}\n"
        f"⊱⋅ ──────────── ⋅⊰\n"
        f"👥 𝗧𝗲𝗮𝗺 - B: {r2}/{w2} | {b2//6}.{b2%6} ov\n╰⊚ 𝗖𝗥𝗥: {crr2:.2f}\n"
        f"────┈┄┄╌╌╌╌┄┄┈────\n"
        + target_section +
        f"╾ ⏳ 𝗧𝗼𝘁𝗮𝗹 𝗢𝘃𝗲𝗿𝘀: {overs}\n"
        f"╾ 📯 𝗛𝗼𝘀𝘁: {hname}"
    )
    HEADER_IMAGE = "https://i.ibb.co/S40hfh1v/file-00000000088c7207af8fda45c0342247.png"
    await context.bot.send_photo(chat_id, photo=HEADER_IMAGE)
    await context.bot.send_message(chat_id, msg, parse_mode="HTML")


async def _end_match(chat_id, context, lobby):
    lobby["phase"] = "ended"
    sa, sb = lobby["team_a_score"], lobby["team_b_score"]
    ra, wa, ba = sa["runs"], sa["wickets"], sa["balls"]
    rb, wb, bb = sb["runs"], sb["wickets"], sb["balls"]
    if ra > rb: result = "Team A"
    elif rb > ra: result = "Team B"
    else: result = "Tie"
    
    from telegram.helpers import create_deep_linked_url
    bot_info = await context.bot.get_me()
    
    lines = [
        f"🏆 Game #{str(chat_id).replace('-100', '')} Results 🏆\n",
        f"Winner: {result}\n\n"
    ]
    
    if hasattr(context.bot, "username"):
        link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/1"
        lines.append(f"🔗 Game Link: {link}\n\n")
        lines.append(f"𝐅𝐮𝐫𝐢𝐨𝐮𝐬 𝐂𝐫𝐢𝐜𝐤𝐞𝐭 𝐆𝐚𝐦𝐞 𝐁𝐨𝐭:\n")
        
    lines.append("Here's the scorecard after the match:\n\n")
    
    # We can reuse _build_team_scoreboard logic for the final result
    sc_lines = await _build_team_scoreboard(chat_id, context, lobby, "final")
    lines.append(sc_lines)
    
    await context.bot.send_message(chat_id, "".join(lines), parse_mode="HTML")
    del team_lobbies[chat_id]
    
    # Update DB Stats
    from database import get_user, update_user
    for uid_s, s in lobby["player_stats"].items():
        uid = int(uid_s)
        u = await get_user(uid)
        runs = s.get("runs", 0)
        
        updates = {
            "total_runs": u.get("total_runs", 0) + runs,
            "total_balls": u.get("total_balls", 0) + s.get("balls", 0),
            "total_wickets": u.get("total_wickets", 0) + s.get("wickets", 0),
            "runs_conceded": u.get("runs_conceded", 0) + s.get("runs_given", 0),
            "balls_bowled": u.get("balls_bowled", 0) + len(s.get("bowl_hist", [])),
            "fours": u.get("fours", 0) + s.get("fours", 0),
            "sixes": u.get("sixes", 0) + s.get("sixes", 0),
            "matches_played": u.get("matches_played", 0) + 1,
        }
        
        if runs > u.get("highest_score", 0):
            updates["highest_score"] = runs
            updates["highest_score_balls"] = s.get("balls", 0)
            
        if runs >= 100:
            updates["centuries"] = u.get("centuries", 0) + 1
        elif runs >= 50:
            updates["fifties"] = u.get("fifties", 0) + 1
            
        if runs == 0 and s.get("is_out"):
            updates["ducks"] = u.get("ducks", 0) + 1
            
        # MOM check (same logic as before)
        best_b_uid, best_b_sr = None, -1.0
        best_w_uid, best_w_ec = None, 9999.0
        for b_uid_s, b_s in lobby["player_stats"].items():
            sr = round((b_s.get("runs",0)/b_s.get("balls",1))*100,2) if b_s.get("balls",0)>0 else 0
            nb = len(b_s.get("bowl_hist",[]))
            ec = round(b_s.get("runs_given",0)/(nb/6),2) if nb>0 else 9999
            if sr > best_b_sr: best_b_sr=sr; best_b_uid=int(b_uid_s)
            if ec < best_w_ec: best_w_ec=ec; best_w_uid=int(b_uid_s)
            
        if uid == best_b_uid:
            updates["mom_bat"] = u.get("mom_bat", 0) + 1
        if uid == best_w_uid:
            updates["mom_bowl"] = u.get("mom_bowl", 0) + 1
            
        # Captain checks
        is_cap_a = (uid == lobby["cap_a"])
        is_cap_b = (uid == lobby["cap_b"])
        if is_cap_a or is_cap_b:
            if (is_cap_a and result == "Team A") or (is_cap_b and result == "Team B"):
                updates["captain_wins"] = u.get("captain_wins", 0) + 1
            elif result != "Tie":
                updates["captain_losses"] = u.get("captain_losses", 0) + 1
                
        await update_user(uid, updates)

async def _bowl_timeout_team(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    time_left = context.job.data.get("time_left", 60)
    lobby = get_lobby(chat_id)
    if not lobby or lobby["delivery"]["status"] != "waiting_bowler": return
    
    bid = lobby["current_bowler"]
    b_name = await _get_name(context, chat_id, bid, "Bowler")
    s_name = await _get_name(context, chat_id, lobby["striker"], "Batter")
    
    if time_left == 30:
        await context.bot.send_message(chat_id, f"⏳ <b>Bowler Timeout:</b> {b_name}, 30s left!", parse_mode="HTML")
        context.job_queue.run_once(_bowl_timeout_team, 15, chat_id=chat_id, data={"time_left": 15}, name=f"tbowl_{chat_id}")
    elif time_left == 15:
        await context.bot.send_message(chat_id, f"⏳ <b>Bowler Timeout:</b> {b_name}, 15s left!", parse_mode="HTML")
        context.job_queue.run_once(_bowl_timeout_team, 10, chat_id=chat_id, data={"time_left": 5}, name=f"tbowl_{chat_id}")
    elif time_left == 5:
        await context.bot.send_message(chat_id, f"⚠️ <b>Bowler Timeout:</b> {b_name}, 5s remaining! Hurry up!", parse_mode="HTML")
        context.job_queue.run_once(_bowl_timeout_team, 5, chat_id=chat_id, data={"time_left": 0}, name=f"tbowl_{chat_id}")
    else:
        # Final timeout
        warns = lobby.get("bowler_warnings", 0)
        if warns >= 2:
            # 3rd timeout = REPLACED
            await context.bot.send_message(chat_id, f"⏰ <b>{b_name} timed out 3 times — REPLACED!</b>", parse_mode="HTML")
            lobby["current_bowler"] = None
            lobby["bowler_warnings"] = 0
            lobby["delivery"] = {"bowler_num": None, "status": "waiting_bowler"}
            bowl_cap_id = lobby["cap_a"] if lobby["bowling_team"] == "a" else lobby["cap_b"]
            cap_name = await _get_name(context, chat_id, bowl_cap_id, "Captain")
            await context.bot.send_message(chat_id, f"📣 <b>{cap_name}</b>: /bowling @user to choose a replacement bowler", parse_mode="HTML")
        else:
            # Warning + Auto-ball
            auto = random.randint(1, 6)
            lobby["delivery"]["bowler_num"] = auto
            lobby["delivery"]["status"] = "waiting_batter"
            lobby["bowler_warnings"] = warns + 1
            
            try:
                await context.bot.send_message(bid, 
                    f"⚾ <b>YOUR TURN TO BOWL (Team Match)!</b>\n"
                    f"Batter: {s_name}\n"
                    f"Send a number <b>1–6</b> in this chat.", parse_mode="HTML")
            except: pass
            
            await context.bot.send_message(chat_id,
                f"⏰ <b>Bowler timeout!</b> ({warns+1}/2 warnings) ⚾ Auto ball: <b>{auto}</b>\n"
                f"🏏 <a href='tg://user?id={lobby['striker']}'>{s_name}</a> send your shot in group!", parse_mode="HTML")
            context.job_queue.run_once(_bat_timeout_team, 30, chat_id=chat_id, data={"time_left": 30}, name=f"tbat_{chat_id}")

async def _bat_timeout_team(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    time_left = context.job.data.get("time_left", 60)
    lobby = get_lobby(chat_id)
    if not lobby or lobby["delivery"]["status"] != "waiting_batter": return
    
    sid = lobby["striker"]; sk = str(sid)
    s_name = await _get_name(context, chat_id, sid, "Batter")

    if time_left == 30:
        await context.bot.send_message(chat_id, f"⏳ <b>Batter Timeout:</b> <a href='tg://user?id={sid}'>{s_name}</a>, 30s left!", parse_mode="HTML")
        context.job_queue.run_once(_bat_timeout_team, 15, chat_id=chat_id, data={"time_left": 15}, name=f"tbat_{chat_id}")
    elif time_left == 15:
        await context.bot.send_message(chat_id, f"⏳ <b>Batter Timeout:</b> <a href='tg://user?id={sid}'>{s_name}</a>, 15s left!", parse_mode="HTML")
        context.job_queue.run_once(_bat_timeout_team, 10, chat_id=chat_id, data={"time_left": 5}, name=f"tbat_{chat_id}")
    elif time_left == 5:
        await context.bot.send_message(chat_id, f"⚠️ <b>Batter Timeout:</b> <a href='tg://user?id={sid}'>{s_name}</a>, 5s remaining! Send your shot!", parse_mode="HTML")
        context.job_queue.run_once(_bat_timeout_team, 5, chat_id=chat_id, data={"time_left": 0}, name=f"tbat_{chat_id}")
    else:
        # Increment and check warnings
        warns = lobby.get("batter_warnings", 0) + 1
        lobby["batter_warnings"] = warns
        
        bat_key = f"team_{lobby['batting_team']}_score"
        sk = str(lobby["striker"])
        if warns >= 2:
            # OUT on 2nd timeout
            lobby["player_stats"][sk]["is_out"] = True
            lobby["player_stats"][sk].setdefault("bat_hist", []).append("W")
            lobby[bat_key]["wickets"] += 1
            lobby["dismissed"].append(int(sk))
            lobby["striker"] = None
            lobby["delivery"] = {"bowler_num": None, "status": "waiting_bowler"}
            lobby["batter_warnings"] = 0
            
            await context.bot.send_message(chat_id, f"⏰ <b>{s_name} timed out 2 times — OUT!</b>", parse_mode="HTML")
            
            cap_id = lobby["cap_a"] if lobby["batting_team"] == "a" else lobby["cap_b"]
            cap_name = await _get_name(context, chat_id, cap_id, "Captain")
            await context.bot.send_message(chat_id, f"📣 <b>{cap_name}/Host</b>: choose next batter → /batting @user", parse_mode="HTML")
            await _check_next(chat_id, context, lobby, wicket=True)
        else:
            # -6 Penalty + Warning
            lobby["player_stats"][sk]["runs"] = max(0, lobby["player_stats"][sk].get("runs", 0) - 6)
            lobby["player_stats"][sk].setdefault("bat_hist", []).append(-6)
            lobby[bat_key]["runs"] = max(0, lobby[bat_key]["runs"] - 6)
            
            lobby["delivery"] = {"bowler_num":None,"status":"waiting_bowler"}
            await context.bot.send_message(chat_id,
                f"⏰ <b>{s_name} timeout!</b> -6 penalty (Warning {warns}/1)", parse_mode="HTML")
            await _announce_crease(chat_id, context, lobby)
