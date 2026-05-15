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

async def _announce_crease(chat_id, context, lobby):
    """Announce striker, non-striker and bowler clearly."""
    s_name = await _get_name(context, chat_id, lobby["striker"], "Striker")
    ns_name = await _get_name(context, chat_id, lobby["non_striker"], "Non-striker")
    b_name = await _get_name(context, chat_id, lobby["current_bowler"], "Bowler")
    sc = lobby[f"team_{lobby['batting_team']}_score"]
    r, w, b = sc["runs"], sc["wickets"], sc["balls"]
    
    bot_info = await context.bot.get_me()
    dm_link = f"https://t.me/{bot_info.username}"
    kb = [[InlineKeyboardButton("📩 Send Bowl in DM", url=dm_link)]]
    
    await context.bot.send_message(chat_id,
        f"🏏 <b>At the crease:</b>\n"
        f"🔴 Striker: <b>{s_name}</b>\n"
        f"⚪ Non-Striker: <b>{ns_name}</b>\n"
        f"🎯 Bowling: <b>{b_name}</b>\n\n"
        f"Score: <b>{r}/{w}</b> ({b//6}.{b%6} ov)\n\n"
        f"<b>Bowler</b> {b_name}: click below to bowl in DM\n"
        f"<b>Batter</b> {s_name}: send 0-6 (0=dot) in group",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="HTML")
    # Start Bowler Timeout Countdown (60s total: 30 -> 20 -> 10)
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
    bowl_team = lobby["team_a"] if lobby["bowling_team"] == "a" else lobby["team_b"]
    if tid not in bowl_team:
        await update.message.reply_text("That player is not in the bowling team!"); return
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
    bat_team = lobby["team_a"] if lobby["batting_team"] == "a" else lobby["team_b"]
    if tid not in bat_team:
        await update.message.reply_text("Not in batting team!"); return
    if tid in lobby.get("dismissed", []):
        await update.message.reply_text("Already out!"); return
    _init_player(lobby, tid)
    tname_esc = html.escape(tname)

    if lobby["striker"] is None:
        lobby["striker"] = tid
        await update.message.reply_text(
            f"🏏 <b>{tname_esc}</b> → <b>Striker end</b> 🔴\nNow choose 2nd batter /batting @user", parse_mode="HTML")
    elif lobby["non_striker"] is None:
        if tid == lobby["striker"]:
            await update.message.reply_text("Already set as striker!"); return
        lobby["non_striker"] = tid
        await update.message.reply_text(
            f"⚪ <b>{tname_esc}</b> → <b>Non-striker end</b>\n📣 Bowling captain: /bowling @user", parse_mode="HTML")
        # If bowler already set, announce and start
        if lobby["current_bowler"]:
            await _announce_crease(chat_id, context, lobby)
    else:
        # Wicket replacement
        lobby["striker"] = tid
        await update.message.reply_text(f"🏏 <b>{tname_esc}</b> comes in at striker end!", parse_mode="HTML")
        if lobby["current_bowler"]:
            await _announce_crease(chat_id, context, lobby)

# ─── Ball handling ───
async def handle_team_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    text = update.message.text
    try: num = int(text)
    except: return False
    
    lobby = get_lobby(chat_id)
    if not lobby or lobby["phase"] not in ("live_1st", "live_2nd"): return False
    delivery = lobby["delivery"]

    if uid == lobby["current_bowler"] and delivery["status"] == "waiting_bowler" and 1 <= num <= 6:
        # Warning: Bowler sending in group
        b_name = await _get_name(context, chat_id, uid, "Bowler")
        await context.bot.send_message(chat_id,
            f"❌ <b>{b_name}</b>, you are the <b>Bowler</b>! Send your number in <b>DM</b>, not here.",
            parse_mode="HTML")
        return True # Swallow the message so it's not used as a batter's shot
        
    if uid == lobby["striker"] and delivery["status"] == "waiting_batter" and 0 <= num <= 6:
        _cancel_team_jobs(chat_id, context)
        # 👍 TAG feedback
        s_name = await _get_name(context, chat_id, uid)
        await context.bot.send_message(chat_id, 
            f"👍 <a href='tg://user?id={uid}'>{s_name}</a> <b>played his shot!</b>", 
            parse_mode="HTML")
        return await _process_ball(update, context, lobby, num)
    return False

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

    # Header like solo
    header = f"<b>{s_name}</b> vs <b>{b_name}</b>\n⚾ BOWL: <b>{bowl_num}</b> | 🏏 BAT: <b>{bat_num}</b>\n\n"

    if bowl_num == bat_num:
        bs["bat_hist"].append("W"); bs["is_out"] = True
        bws["wickets"] += 1
        lobby[bat_key]["wickets"] += 1
        lobby["dismissed"].append(sid)
        lobby["striker"] = None
        lobby["delivery"] = {"bowler_num": None, "status": "waiting_bowler"}
        lobby["batter_warnings"] = 0
        r, w, b = lobby[bat_key]["runs"], lobby[bat_key]["wickets"], lobby[bat_key]["balls"]
        await context.bot.send_message(chat_id,
            f"{header}☝️ <b>OUT!</b> {s_name} dismissed!\n"
            f"Score: <b>{r}/{w}</b> ({b//6}.{b%6} ov)", parse_mode="HTML")
        await _check_next(chat_id, context, lobby, wicket=True)
    else:
        runs = bat_num
        bs["runs"] += runs; bs["balls"] += 1; bs["bat_hist"].append(runs)
        if runs == 4: bs["fours"] += 1
        if runs == 6: bs["sixes"] += 1
        bws["runs_given"] += runs
        lobby[bat_key]["runs"] += runs
        lobby["delivery"] = {"bowler_num": None, "status": "waiting_bowler"}
        lobby["batter_warnings"] = 0
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
        msg = (f"{header}{emoji} <b>{runs} run{'s' if runs!=1 else ''}!</b> 👍\n"
               f"💥 <b>Hit by:</b> {s_tag}!"
               f"{' 🔄 Strike rotated!' if rotate else ''}\n"
               f"Score: <b>{r}/{w}</b> ({b//6}.{b%6} ov)\n"
               f"🔴 Striker: {s2_name} | ⚪ Non-striker: {ns_name}")
        await context.bot.send_message(chat_id, msg, parse_mode="HTML")
        
        # Add Thumbs Up reaction to the batter's message
        try: await update.message.set_reaction("👍")
        except: pass

        await _check_next(chat_id, context, lobby, wicket=False)

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

async def _over_card(chat_id, context, lobby):
    bat_t = lobby["batting_team"].upper()
    sc = lobby[f"team_{lobby['batting_team']}_score"]
    r, w, b = sc["runs"], sc["wickets"], sc["balls"]
    rr = round(r/(b/6),2) if b > 0 else 0.0
    lines = [f"╭━─━─≪✠≫─━─━╮\n───⊱ Over {b//6} Scorecard ⊰───\n\n"
             f"🔵 Team {bat_t}: <b>{r}/{w}</b> ({b//6}.0 ov) | RR: {rr}\n"]
    if lobby["inning"] == 2:
        first = lobby[f"team_{lobby['bowling_team']}_score"]["runs"]
        target = first + 1
        needed = target - r
        balls_left = (lobby["overs"] * 6) - b
        req_rr = round(needed / (balls_left/6), 2) if balls_left > 0 else 0.0
        lines.append(f"🎯 Target: <b>{target}</b> | Need <b>{needed}</b> in {balls_left} balls (Req RR: {req_rr})\n\n")
    else:
        lines.append("\n")
    for uid in (lobby["team_a"] if lobby["batting_team"] == "a" else lobby["team_b"]):
        s = lobby["player_stats"].get(str(uid), {})
        if s.get("balls", 0) > 0 or s.get("is_out"):
            name = await _get_name(context, chat_id, uid)
            hist = ", ".join(str(x) for x in s.get("bat_hist", [])) or "—"
            out = " ✝" if s.get("is_out") else " 🏏"
            lines.append(f"✴️ {name}{out} = {s.get('runs',0)}({s.get('balls',0)}) [{hist}]\n")
    bowl_t = lobby["bowling_team"].upper()
    lines.append(f"\n🎯 Team {bowl_t} Bowling:\n")
    for uid in (lobby["team_a"] if lobby["bowling_team"] == "a" else lobby["team_b"]):
        s = lobby["player_stats"].get(str(uid), {})
        if s.get("bowl_hist"):
            name = await _get_name(context, chat_id, uid)
            nb = len(s["bowl_hist"])
            econ = round(s.get("runs_given",0)/(nb/6),2) if nb > 0 else 0
            lines.append(f"🎯 {name} = {s.get('wickets',0)}W-{s.get('runs_given',0)}R Econ:{econ}\n")
    lines.append("╰━─━─≪✠≫─━─━╯")
    await context.bot.send_message(chat_id, "".join(lines), parse_mode="HTML")

async def _end_1st(chat_id, context, lobby):
    lobby["phase"] = "between_innings"
    bat_t = lobby["batting_team"].upper()
    sc = lobby[f"team_{lobby['batting_team']}_score"]
    r, w, b = sc["runs"], sc["wickets"], sc["balls"]
    await _over_card(chat_id, context, lobby)
    host_name = await _get_name(context, chat_id, lobby["host_id"], "Host")
    await context.bot.send_message(chat_id,
        f"🏁 <b>1st Innings Over!</b>\nTeam {bat_t}: <b>{r}/{w}</b> ({b//6}.{b%6} ov)\n\n"
        f"📣 <b>{host_name}</b>: /swap to start 2nd innings!", parse_mode="HTML")

# ─── /swap ───
async def swap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can swap."); return
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

# ─── /score (team) ───
async def score_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lobby = get_lobby(chat_id)
    if not lobby or lobby["phase"] not in ("live_1st","live_2nd","between_innings"):
        await update.message.reply_text("No active team match."); return
    
    sa, sb = lobby["team_a_score"], lobby["team_b_score"]
    r1, w1, b1 = sa["runs"], sa["wickets"], sa["balls"]
    r2, w2, b2 = sb["runs"], sb["wickets"], sb["balls"]
    
    lines = [f"📊 <b>Team Match Scoreboard</b>\n",
             f"🔵 Team A: <b>{r1}/{w1}</b> ({b1//6}.{b1%6} ov)\n",
             f"🔴 Team B: <b>{r2}/{w2}</b> ({b2//6}.{b2%6} ov)\n"]
             
    if lobby["inning"] == 2:
        bat_t = lobby["batting_team"].upper()
        sc = lobby[f"team_{lobby['batting_team']}_score"]
        first = lobby[f"team_{lobby['bowling_team']}_score"]["runs"]
        needed = first - sc["runs"] + 1
        left = lobby["overs"]*6 - sc["balls"]
        req = round(needed/(left/6),2) if left > 0 else 0
        lines.append(f"\n🎯 Team {bat_t} needs <b>{needed}</b> runs in {left} balls (Req RR: {req})\n")
    
    lines.append("\n🏏 <b>Batting Stats:</b>\n")
    # Show active batters first
    for tid in [lobby["striker"], lobby["non_striker"]]:
        if tid:
            s = lobby["player_stats"].get(str(tid), {})
            name = await _get_name(context, chat_id, tid)
            hist = " ".join(str(x) for x in s.get("bat_hist", []))
            lines.append(f"• <b>{name}*</b>: {s.get('runs',0)}({s.get('balls',0)}) | Shots: [{hist}]\n")
            
    lines.append("\n🎯 <b>Current Bowler:</b>\n")
    if lobby["current_bowler"]:
        bid = lobby["current_bowler"]
        bs = lobby["player_stats"].get(str(bid), {})
        name = await _get_name(context, chat_id, bid)
        nb = len(bs.get("bowl_hist",[]))
        econ = round(bs.get("runs_given",0)/(nb/6),2) if nb>0 else 0
        # Calculate over runs
        over_balls = lobby["balls_in_over"]
        recent = bs.get("bowl_hist", [])[-over_balls:] if over_balls > 0 else []
        this_over = " ".join(str(x) for x in recent)
        lines.append(f"• <b>{name}</b>: {bs.get('wickets',0)}/{bs.get('runs_given',0)} (Econ: {econ})\n")
        lines.append(f"📦 <b>This Over:</b> [{this_over}]\n")

    await update.message.reply_text("".join(lines), parse_mode="HTML")

async def _end_match(chat_id, context, lobby):
    lobby["phase"] = "ended"
    sa, sb = lobby["team_a_score"], lobby["team_b_score"]
    ra, wa, ba = sa["runs"], sa["wickets"], sa["balls"]
    rb, wb, bb = sb["runs"], sb["wickets"], sb["balls"]
    if ra > rb: result = f"🏆 <b>Team A wins by {ra-rb} runs!</b>"
    elif rb > ra: result = f"🏆 <b>Team B wins by {len(lobby['team_b'])-wb} wickets!</b>"
    else: result = "🤝 <b>It's a TIE!</b>"
    
    # Detailed stats for summary
    lines = [
        f"🏁 <b>MATCH OVER! Detailed Scorecard</b>\n\n",
        f"🔵 <b>Team A: {ra}/{wa} ({ba//6}.{ba%6} ov)</b>\n"
    ]
    for uid in lobby["team_a"]:
        s = lobby["player_stats"].get(str(uid), {})
        name = await _get_name(context, chat_id, uid)
        out = "✝" if s.get("is_out") else "🏏"
        sr = round((s.get("runs",0)/s.get("balls",1))*100,2) if s.get("balls",0)>0 else 0
        lines.append(f"  • {name}: {s.get('runs',0)}({s.get('balls',0)}) SR:{sr} {out}\n")
    
    lines.append(f"\n🔴 <b>Team B: {rb}/{wb} ({bb//6}.{bb%6} ov)</b>\n")
    for uid in lobby["team_b"]:
        s = lobby["player_stats"].get(str(uid), {})
        name = await _get_name(context, chat_id, uid)
        out = "✝" if s.get("is_out") else "🏏"
        sr = round((s.get("runs",0)/s.get("balls",1))*100,2) if s.get("balls",0)>0 else 0
        lines.append(f"  • {name}: {s.get('runs',0)}({s.get('balls',0)}) SR:{sr} {out}\n")
    
    lines.append(f"\n🎯 <b>Bowling Figures</b>\n")
    for uid in lobby["team_a"] + lobby["team_b"]:
        s = lobby["player_stats"].get(str(uid), {})
        if s.get("bowl_hist"):
            name = await _get_name(context, chat_id, uid)
            nb = len(s["bowl_hist"])
            ec = round(s.get("runs_given",0)/(nb/6),2) if nb>0 else 0
            lines.append(f"  • {name}: {s.get('wickets',0)}W-{s.get('runs_given',0)}R (Econ:{ec})\n")

    lines.append(f"\n{result}\n")
    
    # Find MOM
    best_b_uid, best_b_sr = None, -1.0
    best_w_uid, best_w_ec = None, 9999.0
    for uid_s, s in lobby["player_stats"].items():
        sr = round((s.get("runs",0)/s.get("balls",1))*100,2) if s.get("balls",0)>0 else 0
        nb = len(s.get("bowl_hist",[]))
        ec = round(s.get("runs_given",0)/(nb/6),2) if nb>0 else 9999
        if sr > best_b_sr: best_b_sr=sr; best_b_uid=int(uid_s)
        if ec < best_w_ec: best_w_ec=ec; best_w_uid=int(uid_s)
    bh = await _get_name(context, chat_id, best_b_uid) if best_b_uid else "N/A"
    wh = await _get_name(context, chat_id, best_w_uid) if best_w_uid else "N/A"
    
    lines.append(f"\n🏆 <b>Man of the Match</b>\n")
    lines.append(f"🏏 Batting Hero: {bh} (SR: {best_b_sr})\n")
    lines.append(f"🎯 Bowling Hero: {wh} (Econ: {best_w_ec})")
    
    await context.bot.send_message(chat_id, "".join(lines), parse_mode="HTML")
    del team_lobbies[chat_id]

async def _bowl_timeout_team(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    time_left = context.job.data.get("time_left", 60)
    lobby = get_lobby(chat_id)
    if not lobby or lobby["delivery"]["status"] != "waiting_bowler": return
    
    b_name = await _get_name(context, chat_id, lobby["current_bowler"], "Bowler")
    
    if time_left > 10:
        next_time = 20 if time_left == 30 else 10
        await context.bot.send_message(chat_id, f"⏳ <b>Bowler Timeout:</b> {b_name}, {time_left}s left to bowl in DM!", parse_mode="HTML")
        context.job_queue.run_once(_bowl_timeout_team, next_time, chat_id=chat_id, data={"time_left": time_left - next_time}, name=f"tbowl_{chat_id}")
    else:
        # Final timeout: Auto ball
        auto = random.randint(1, 6)
        lobby["delivery"]["bowler_num"] = auto
        lobby["delivery"]["status"] = "waiting_batter"
        
        s_name = await _get_name(context, chat_id, lobby["striker"], "Batter")
        sid = lobby["striker"]
        await context.bot.send_message(chat_id,
            f"⏰ <b>Bowler timeout!</b> ⚾ Auto ball delivered: <b>{auto}</b>\n"
            f"🏏 <a href='tg://user?id={sid}'>{s_name}</a> send your shot in group!", parse_mode="HTML")
        context.job_queue.run_once(_bat_timeout_team, 30, chat_id=chat_id, data={"time_left": 30}, name=f"tbat_{chat_id}")

async def _bat_timeout_team(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    time_left = context.job.data.get("time_left", 60)
    lobby = get_lobby(chat_id)
    if not lobby or lobby["delivery"]["status"] != "waiting_batter": return
    
    sid = lobby["striker"]; sk = str(sid)
    s_name = await _get_name(context, chat_id, sid, "Batter")

    if time_left > 10:
        next_time = 20 if time_left == 30 else 10
        await context.bot.send_message(chat_id, f"⏳ <b>Batter Timeout:</b> {s_name}, {time_left}s left to play in group!", parse_mode="HTML")
        context.job_queue.run_once(_bat_timeout_team, next_time, chat_id=chat_id, data={"time_left": time_left - next_time}, name=f"tbat_{chat_id}")
    else:
        # Penalty/Out
        warns = lobby.get("batter_warnings", 0)
        bat_key = f"team_{lobby['batting_team']}_score"
        s = lobby["player_stats"].get(sk, {})
        if warns >= 1:
            s.setdefault("bat_hist",[]).append("W"); s["is_out"] = True
            lobby[bat_key]["wickets"] += 1
            lobby["dismissed"].append(sid); lobby["striker"] = None
            lobby["delivery"] = {"bowler_num":None,"status":"waiting_bowler"}
            lobby["batter_warnings"] = 0
            await context.bot.send_message(chat_id, f"⏰ <b>{s_name} timed out — OUT!</b>", parse_mode="HTML")
            await _check_next(chat_id, context, lobby, wicket=True)
        else:
            s["runs"] = max(0, s.get("runs",0) - 6)
            s.setdefault("bat_hist",[]).append(-6)
            lobby["player_stats"][sk] = s
            lobby[bat_key]["runs"] = max(0, lobby[bat_key]["runs"] - 6)
            lobby["batter_warnings"] = warns + 1
            lobby["delivery"] = {"bowler_num":None,"status":"waiting_bowler"}
            await context.bot.send_message(chat_id,
                f"⏰ <b>{s_name} timeout!</b> -6 penalty ({warns+1}/2 warnings)", parse_mode="HTML")
            await _announce_crease(chat_id, context, lobby)
