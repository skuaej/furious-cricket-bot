import os, logging, html, random, time, threading, http.server, socketserver, asyncio
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, ChatMemberHandler
from database import (
    get_user, update_user, create_match, get_match, update_match, end_match,
    matches_col, users_col, get_sudo_users, add_sudo_user, remove_sudo_user
)
from team_mode import (
    team_lobbies, get_lobby, hostchange, create_team,
    join_team_a, join_team_b, add_to_a, add_to_b,
    remove_from_a, remove_from_b,
    addcap_a, addcap_b, remove_cap_a, remove_cap_b,
    toss, toss_choice, setovers, member_list,
    vote4host_change, host_vote_callback,
    end_team, confirm_end_team, _new_lobby
)
from team_game import (
    play_team, bowling, batting_cmd, swap, score_team,
    handle_team_number, _cancel_team_jobs, _get_name as _get_team_name,
    _bat_timeout_team, _bowl_timeout_team
)

def run_web():
    try:
        port = int(os.environ.get('PORT', 8000))
        handler = http.server.SimpleHTTPRequestHandler
        with socketserver.TCPServer(("0.0.0.0", port), handler) as httpd:
            logger.info(f"✅ Web server started on port {port}")
            httpd.serve_forever()
    except Exception as e:
        logger.error(f"❌ Web server failed: {e}")

load_dotenv()
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_CHAT_LINK = os.getenv("SUPPORT_CHAT_LINK")
LOG_CHANNEL_ID = os.getenv("LOG_CHAT_ID")
OWNER_ID = int(os.getenv("OWNER_ID", 0))
sudo_users = {OWNER_ID}

async def init_sudo():
    db_sudos = await get_sudo_users()
    for s in db_sudos:
        sudo_users.add(s)

active_lobbies = {}
play_votes = {}
banned_users = {}  # {user_id: unban_timestamp}
authorized_voters = {} # {chat_id: [voter_ids]}

HEADER_IMAGE = "https://i.ibb.co/S40hfh1v/file-00000000088c7207af8fda45c0342247.png"

# ─── HELPERS ───
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

async def edit_any(query, text, kb=None):
    """Helper to edit either a text message or a photo caption."""
    try:
        await query.edit_message_caption(caption=text, reply_markup=kb, parse_mode="HTML")
    except:
        try:
            await query.edit_message_text(text=text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to edit message: {e}")

async def voting_expired_cb(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    if chat_id in play_votes:
        vote_data = play_votes[chat_id]
        msg_id = vote_data.get("msg_id")
        del play_votes[chat_id]
        
        text = "⏰ <b>Voting Expired!</b> The match request has timed out."
        if msg_id:
            try:
                await context.bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=text, parse_mode="HTML")
            except:
                try:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode="HTML")
                except:
                    await context.bot.send_message(chat_id, text, parse_mode="HTML")
        else:
            await context.bot.send_message(chat_id, text, parse_mode="HTML")

def get_commentary(num):
    return random.choice(COMMENTARY.get(num, ["Nice shot!"]))

async def get_name(uid):
    u = await get_user(uid)
    return u.get("first_name") or u.get("username", f"Player {uid}") or f"Player {uid}"

async def next_bowler(match):
    """Pick next bowler from lobby_players who is not the current batsman."""
    players = match["lobby_players"]
    idx = match.get("bowler_index", 1)
    for i in range(len(players)):
        candidate = players[(idx + i) % len(players)]
        if candidate != match["current_batsman"]:
            return candidate, (idx + i + 1) % len(players)
    return None, idx

async def next_batsman(match):
    """Pick next batsman who is not yet out."""
    sb = match["scoreboard"]
    for uid in match["lobby_players"]:
        if not sb[str(uid)]["is_out"] and uid != match.get("current_batsman"):
            return uid
    return None

async def check_milestones(chat_id, context, name, score, is_batting=True):
    if is_batting:
        if score == 50:
            await context.bot.send_message(chat_id, f"🔥 <b>HALF CENTURY!</b> {name} reaches <b>50 runs</b>! What a knock! 👏", parse_mode="HTML")
        elif score == 100:
            await context.bot.send_message(chat_id, f"🏆 <b>CENTURY!</b> {name} reaches <b>100 runs</b>! An absolute masterclass! 👑", parse_mode="HTML")
        elif score > 100 and score % 100 == 0:
            await context.bot.send_message(chat_id, f"👑 <b>{score} RUNS!</b> {name} is unstoppable! Another milestone reached! 🚀", parse_mode="HTML")
    else:
        # Bowling
        if score == 3:
            await context.bot.send_message(chat_id, f"🎯 <b>3 WICKET HAUL!</b> {name} is on fire with <b>3 wickets</b>! 🏏", parse_mode="HTML")
        elif score == 5:
            await context.bot.send_message(chat_id, f"🎖 <b>FIVE WICKET HAUL!</b> {name} has claimed <b>5 wickets</b>! A legendary spell! 🏅", parse_mode="HTML")

async def check_hattrick(chat_id, context, name, results):
    if len(results) >= 3 and all(r == "W" for r in results[-3:]):
        await context.bot.send_message(chat_id, f"🎩 <b>HAT-TRICK!</b> {name} has taken <b>3 wickets in 3 balls</b>! Incredible! ⚡️🏏", parse_mode="HTML")
    elif len(results) >= 2 and all(r == "W" for r in results[-2:]):
        await context.bot.send_message(chat_id, f"🔥 <b>CONSECUTIVE WICKETS!</b> {name} has taken <b>2 wickets in 2 balls</b>! He's on a hat-trick! ⚡️🏏", parse_mode="HTML")

async def process_ball(chat_id, bowler_num, batter_num, context, match, is_auto_bowl=False):
    """Core ball processing logic."""
    batsman_id = match["current_batsman"]
    bowler_id = match["current_bowler"]
    sb = match["scoreboard"]
    bk = str(batsman_id)
    bwk = str(bowler_id)

    sb[bk]["balls_faced"] += 1
    sb[bwk]["balls_bowled"] += 1
    sb[bwk]["bowl_count_this_turn"] += 1
    sb[bwk]["bowl_history"].append(bowler_num)

    if bowler_num == batter_num:
        # OUT
        sb[bk]["is_out"] = True
        sb[bk]["bat_history"].append("W")
        sb[bwk]["wickets_taken"] += 1
        sb[bwk].setdefault("bowl_results", []).append("W")
        
        name = html.escape(await get_name(batsman_id))
        b_name = html.escape(await get_name(bowler_id))
        
        comm = get_commentary("W")
        await context.bot.send_message(chat_id,
            f"☝️ <b>OUT!</b> {name} is out! (Shot: {batter_num}, Ball: ❓)\n\n"
            f"<i>{comm}</i>", parse_mode="HTML")
            
        # Check bowling milestones
        await check_hattrick(chat_id, context, b_name, sb[bwk]["bowl_results"])
        await check_milestones(chat_id, context, b_name, sb[bwk]["wickets_taken"], is_batting=False)

        # Reset bowler turn count
        sb[bwk]["bowl_count_this_turn"] = 0
        await update_match(chat_id, {"scoreboard": sb, "batter_timeout_count": 0, "bowler_timeout_count": 0,
            "current_delivery": {"bowler_num": None, "status": "waiting_bowler"}})

        # Find next batsman
        nxt = await next_batsman(match)
        if nxt is None:
            await finish_match(chat_id, context)
            return
        # Pick new bowler too
        nb, ni = await next_bowler({"lobby_players": match["lobby_players"], "current_batsman": nxt,
            "bowler_index": match.get("bowler_index", 1)})
        await update_match(chat_id, {"current_batsman": nxt, "current_bowler": nb, "bowler_index": ni})
        cancel_turn_jobs(chat_id, context)
        await notify_turn(chat_id, nxt, nb, context)
    else:
        # RUNS
        runs = batter_num
        sb[bk]["runs"] += runs
        sb[bk]["bat_history"].append(batter_num)
        sb[bwk]["runs_given"] += runs
        sb[bwk].setdefault("bowl_results", []).append(runs)
        
        if runs == 4:
            sb[bk]["fours"] += 1
        elif runs == 6:
            sb[bk]["sixes"] += 1
            
        # Check batting milestones
        name = html.escape(await get_name(batsman_id))
        await check_milestones(chat_id, context, name, sb[bk]["runs"], is_batting=True)

        upd = {"scoreboard": sb, "batter_timeout_count": 0, "bowler_timeout_count": 0,
            "current_delivery": {"bowler_num": None, "status": "waiting_bowler"}}


        await update_match(chat_id, upd)
        name = html.escape(await get_name(batsman_id))
        b_name = html.escape(await get_name(bowler_id))
        tag = f'<a href="tg://user?id={batsman_id}"><b>{name}</b></a>'
        comm = get_commentary(runs)
        
        # Improved formatting
        run_label = " (Dot Ball)" if runs == 0 else ("🔥 <b>FOUR!</b> " if runs == 4 else ("🏆 <b>SIX!</b> " if runs == 6 else ""))
        run_text = "" if runs >= 4 else f" scores <b>{runs} runs!</b>"
        num_emojis = {0: "0️⃣", 1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣"}
        bt_emoji = num_emojis.get(batter_num, str(batter_num))
        await context.bot.send_message(chat_id,
            f"<b>{name}</b> vs <b>{b_name}</b>\n⚾ BOWL: <b>❓</b> | 🏏 BAT: <b>{bt_emoji}</b>\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{run_label}🏏 {tag}{run_text} 👍\n"
            f"━━━━━━━━━━━━━━━\n"
            f"<i>{comm}</i>\n"
            f"Score: <b>{sb[bk]['runs']}</b>({sb[bk]['balls_faced']})", parse_mode="HTML")
        cancel_turn_jobs(chat_id, context)
        
        # Bowler Rotation: every 3 balls in solo
        if sb[bwk]["bowl_count_this_turn"] >= 3:
            sb[bwk]["bowl_count_this_turn"] = 0
            nb, ni = await next_bowler({"lobby_players": match["lobby_players"], "current_batsman": batsman_id,
                "bowler_index": match.get("bowler_index", 1)})
            await update_match(chat_id, {"current_bowler": nb, "bowler_index": ni, "scoreboard": sb})
            await context.bot.send_message(chat_id, f"🔄 <b>Bowler Rotation!</b> New bowler is coming in...")
            await notify_turn(chat_id, batsman_id, nb, context)
        else:
            await notify_turn(chat_id, batsman_id, bowler_id, context)

def cancel_turn_jobs(chat_id, context):
    for j in context.job_queue.get_jobs_by_name(f"bowl_timeout_{chat_id}"):
        j.schedule_removal()
    for j in context.job_queue.get_jobs_by_name(f"bat_timeout_{chat_id}"):
        j.schedule_removal()

async def finish_match(chat_id, context):
    """End match and show full scorecard with MOM."""
    match = await get_match(chat_id)
    if not match:
        return
    await end_match(chat_id)
    cancel_turn_jobs(chat_id, context)

    sb = match["scoreboard"]
    players = match["lobby_players"]

    # State icons: current batter=🟠, out=⚪, alive=🟣 etc.
    state_emojis = ["⚪", "🟠", "🟣", "🔵", "🔴", "🟡", "🟢"]

    lines = ["<b>📊 Current Solo Score</b>\n\n",
             "─────⊱ Sᴏʟᴏ Pʟᴀʏᴇʀ ⊰────\n\n"]

    best_bat_uid, best_bat_sr = None, -1.0
    best_bowl_uid, best_bowl_econ = None, 9999.0

    for idx, uid in enumerate(players, 1):
        s = sb[str(uid)]
        name = html.escape(await get_name(uid))
        bat_hist = ", ".join(str(x) for x in s["bat_history"]) if s["bat_history"] else "-"
        bowl_hist = ", ".join(str(x) for x in s["bowl_history"]) if s["bowl_history"] else "-"
        sr = round((s["runs"] / s["balls_faced"]) * 100, 2) if s["balls_faced"] > 0 else 0
        econ = round(s["runs_given"] / (s["balls_bowled"] / 6), 2) if s["balls_bowled"] > 0 else 0
        dot = state_emojis[idx % len(state_emojis)]
        lines.append(
            f"{idx}. {dot} {name} = <b>{s['runs']}</b>({s['balls_faced']})\n"
            f"    ╰⊚ 4️⃣s: <b>{s['fours']:02d}</b>, 6️⃣s: <b>{s['sixes']:02d}</b> - ID: <code>{uid}</code>\n"
            f"      ╰⊚ Bat: ({bat_hist})\n"
            f"      ╰⊚ Bowl: ({bowl_hist})\n\n"
        )
        if sr > best_bat_sr:
            best_bat_sr = sr
            best_bat_uid = uid
        if s["balls_bowled"] > 0 and econ < best_bowl_econ:
            best_bowl_econ = econ
            best_bowl_uid = uid

    # MOM section
    bat_hero = html.escape(await get_name(best_bat_uid)) if best_bat_uid else "N/A"
    bowl_hero = html.escape(await get_name(best_bowl_uid)) if best_bowl_uid else "N/A"
    bat_s = sb[str(best_bat_uid)] if best_bat_uid else {}
    bh_s = sb[str(best_bowl_uid)] if best_bowl_uid else {}
    b_sr = round((bat_s.get('runs',0)/bat_s.get('balls_faced',1))*100,2) if bat_s.get('balls_faced',0)>0 else 0
    b_ec = round(bh_s.get('runs_given',0)/(bh_s.get('balls_bowled',1)/6),2) if bh_s.get('balls_bowled',0)>0 else 0
    lines.append(f"――――――――――――――――――\n🏆 <b>GAME OVER — HEROES</b>\n")
    lines.append(f"🏏 <b>Batting Hero:</b> {bat_hero} | {bat_s.get('runs',0)} runs (SR: {b_sr})\n")
    lines.append(f"🎯 <b>Bowling Hero:</b> {bowl_hero} | {bh_s.get('wickets_taken',0)} wkts (Econ: {b_ec})\n")

    await context.bot.send_message(chat_id, "".join(lines), parse_mode="HTML")

    # Update DB Stats
    for uid in players:
        s = sb[str(uid)]
        u = await get_user(uid)
        runs = s["runs"]
        
        updates = {
            "total_runs": u.get("total_runs", 0) + runs,
            "total_balls": u.get("total_balls", 0) + s["balls_faced"],
            "total_wickets": u.get("total_wickets", 0) + s["wickets_taken"],
            "runs_conceded": u.get("runs_conceded", 0) + s["runs_given"],
            "balls_bowled": u.get("balls_bowled", 0) + s["balls_bowled"],
            "fours": u.get("fours", 0) + s["fours"],
            "sixes": u.get("sixes", 0) + s["sixes"],
            "matches_played": u.get("matches_played", 0) + 1,
        }
        
        if runs >= 100:
            updates["centuries"] = u.get("centuries", 0) + 1
        elif runs >= 50:
            updates["fifties"] = u.get("fifties", 0) + 1
            
        if runs == 0 and s["is_out"]:
            updates["ducks"] = u.get("ducks", 0) + 1
            
        if uid == best_bat_uid:
            updates["mom_bat"] = u.get("mom_bat", 0) + 1
        if uid == best_bowl_uid:
            updates["mom_bowl"] = u.get("mom_bowl", 0) + 1
            
        await update_user(uid, updates)


# ─── TIMEOUT CALLBACKS ───
async def bowl_timeout_cb(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    data = context.job.data or {"time_left": 60}
    time_left = data["time_left"]
    
    match = await get_match(chat_id)
    if not match or match["match_status"] != "Live":
        return
    delivery = match.get("current_delivery", {})
    if delivery.get("status") != "waiting_bowler":
        return
    
    if time_left == 30:
        await context.bot.send_message(chat_id, f"⏳ <b>Bowler Timeout:</b> 30s left!", parse_mode="HTML")
        context.job_queue.run_once(bowl_timeout_cb, 15, chat_id=chat_id, 
                                   data={"time_left": 15}, name=f"bowl_timeout_{chat_id}")
    elif time_left == 15:
        await context.bot.send_message(chat_id, f"⏳ <b>Bowler Timeout:</b> 15s left!", parse_mode="HTML")
        context.job_queue.run_once(bowl_timeout_cb, 10, chat_id=chat_id, 
                                   data={"time_left": 5}, name=f"bowl_timeout_{chat_id}")
    elif time_left == 5:
        await context.bot.send_message(chat_id, f"⚠️ <b>Bowler Timeout:</b> 5s remaining! Hurry up!", parse_mode="HTML")
        context.job_queue.run_once(bowl_timeout_cb, 5, chat_id=chat_id, 
                                   data={"time_left": 0}, name=f"bowl_timeout_{chat_id}")
    else:
        # Final timeout
        bc = match.get("bowler_timeout_count", 0)
        bowler_id = match["current_bowler"]
        bowler_name = html.escape(await get_name(bowler_id))
        
        if bc >= 2:
            # 3rd timeout = REPLACED
            await context.bot.send_message(chat_id, f"⏰ <b>{bowler_name} timed out 3 times — REPLACED!</b>", parse_mode="HTML")
            nb, ni = await next_bowler({"lobby_players": match["lobby_players"], "current_batsman": match["current_batsman"],
                "bowler_index": match.get("bowler_index", 1)})
            await update_match(chat_id, {"current_bowler": nb, "bowler_index": ni, "bowler_timeout_count": 0})
            await notify_turn(chat_id, match["current_batsman"], nb, context)
        else:
            # Warning + Auto-ball
            auto_num = random.randint(1, 6)
            await update_match(chat_id, {
                "current_delivery.bowler_num": auto_num, 
                "current_delivery.status": "waiting_batter",
                "bowler_timeout_count": bc + 1
            })
            await context.bot.send_message(chat_id,
                f"⏰ <b>Bowler timeout!</b> {bowler_name} ({bc+1}/2 warnings)\n🤖 Auto-ball: <b>{auto_num}</b>",
                parse_mode="HTML")
            # Notify batter
            batsman = await get_user(match["current_batsman"])
            await context.bot.send_message(chat_id,
                f"⚾️ <b>Ball Delivered (Auto)!</b>\n🏏 Batter <a href='tg://user?id={match['current_batsman']}'>{html.escape(batsman.get('first_name', 'Batter'))}</a>, send your shot (0-6)!",
                parse_mode="HTML")
            # Start batter timeout
            context.job_queue.run_once(bat_timeout_cb, 30, chat_id=chat_id, data={"time_left": 30}, name=f"bat_timeout_{chat_id}")

async def bat_timeout_cb(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    data = context.job.data or {"time_left": 60}
    time_left = data["time_left"]

    match = await get_match(chat_id)
    if not match or match["match_status"] != "Live":
        return
    delivery = match.get("current_delivery", {})
    if delivery.get("status") != "waiting_batter":
        return
    
    batsman_id = match["current_batsman"]
    name = html.escape(await get_name(batsman_id))

    if time_left == 30:
        await context.bot.send_message(chat_id, f"⏳ <b>Batter Timeout:</b> <a href='tg://user?id={batsman_id}'>{name}</a>, 30s left!", parse_mode="HTML")
        context.job_queue.run_once(bat_timeout_cb, 15, chat_id=chat_id, 
                                   data={"time_left": 15}, name=f"bat_timeout_{chat_id}")
    elif time_left == 15:
        await context.bot.send_message(chat_id, f"⏳ <b>Batter Timeout:</b> <a href='tg://user?id={batsman_id}'>{name}</a>, 15s left!", parse_mode="HTML")
        context.job_queue.run_once(bat_timeout_cb, 10, chat_id=chat_id, 
                                   data={"time_left": 5}, name=f"bat_timeout_{chat_id}")
    elif time_left == 5:
        await context.bot.send_message(chat_id, f"⚠️ <b>Batter Timeout:</b> <a href='tg://user?id={batsman_id}'>{name}</a>, 5s remaining! Send your shot!", parse_mode="HTML")
        context.job_queue.run_once(bat_timeout_cb, 5, chat_id=chat_id, 
                                   data={"time_left": 0}, name=f"bat_timeout_{chat_id}")
    else:
        # Final timeout
        tc = match.get("batter_timeout_count", 0)
        bk = str(batsman_id)
        sb = match["scoreboard"]

        if tc >= 2:
            # 3rd timeout = OUT + BAN
            sb[bk]["is_out"] = True
            sb[bk]["bat_history"].append("W")
            banned_users[batsman_id] = time.time() + 120  # 2 min ban
            await update_match(chat_id, {"scoreboard": sb, "batter_timeout_count": 0,
                "current_delivery": {"bowler_num": None, "status": "waiting_bowler"}})
            await context.bot.send_message(chat_id,
                f"⏰ <b>{name} timed out 3 times — OUT + BANNED 2 min!</b>", parse_mode="HTML")
            nxt = await next_batsman(match)
            if nxt is None:
                await finish_match(chat_id, context)
                return
            nb, ni = await next_bowler({"lobby_players": match["lobby_players"], "current_batsman": nxt,
                "bowler_index": match.get("bowler_index", 1)})
            await update_match(chat_id, {"current_batsman": nxt, "current_bowler": nb, "bowler_index": ni})
            await notify_turn(chat_id, nxt, nb, context)
        else:
            # Penalty -6 + Warning
            sb[bk]["runs"] = max(0, sb[bk]["runs"] - 6)
            sb[bk]["bat_history"].append(-6)
            await update_match(chat_id, {
                "scoreboard": sb, 
                "batter_timeout_count": tc + 1,
                "current_delivery": {"bowler_num": None, "status": "waiting_bowler"}
            })
            await context.bot.send_message(chat_id,
                f"⏰ <b>{name} timeout!</b> -6 penalty ({tc+1}/2 warnings)", parse_mode="HTML")
            
            await notify_turn(chat_id, batsman_id, match["current_bowler"], context)

# ─── COMMANDS ───
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = False
    u = await users_col.find_one({"user_id": user.id})
    if not u:
        is_new = True
        await get_user(user.id, user.username, user.first_name)
    
    kb = [[InlineKeyboardButton("Support 🆘", url=SUPPORT_CHAT_LINK or "https://t.me/support")]]
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=HEADER_IMAGE,
        caption=f"🏏 Welcome to <b>Furious Cricket Game</b>, {html.escape(user.first_name)}!\n\nUse /help to see all commands.",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="HTML"
    )
    
    # Log start
    if is_new and LOG_CHANNEL_ID:
        try:
            await context.bot.send_message(LOG_CHANNEL_ID,
                f"🆕 <b>New User Started!</b>\n"
                f"👤 Name: {html.escape(user.first_name)}\n"
                f"🆔 ID: <code>{user.id}</code>\n"
                f"🔗 Username: @{user.username if user.username else 'None'}",
                parse_mode="HTML")
        except: pass

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🏆 <b>Furious Cricket Game - Help Menu</b> 🏆\n\n"
        "🏏 <b>SOLO MODE COMMANDS:</b>\n"
        "• /play - Request a match (needs 2 votes)\n"
        "• /join - Join an open solo lobby\n"
        "• /leave_solo - Leave the solo lobby\n"
        "• /forcestart - Admin only: Force open lobby\n"
        "• /score - View live solo scoreboard\n"
        "• /solo_list - View players and status\n"
        "• /userinfo - View your global career stats\n"
        "• /top - View global top runs leaderboard\n"
        "• /end_solo - Admin only: Terminate current solo game\n\n"
        "👥 <b>TEAM MODE COMMANDS:</b>\n"
        "• /create_team - Create a team match lobby\n"
        "• /join_teamA / /join_teamB - Join a team\n"
        "• /add_a / /add_b - Host/Admin: Add players\n"
        "• /remove_a / /remove_b - Host/Admin: Remove players\n"
        "• /hostchange - Host: Transfer game host\n"
        "• /vote4host_change - Vote for host change (2 votes)\n"
        "• /toss - Host only: Start the match toss\n"
        "• /setovers - Host only: Set match duration\n"
        "• /resetover - Host only: Reset match settings\n"
        "• /play_team - Start the match after setup\n"
        "• /swap - Host only: Start 2nd innings\n"
        "• /end_team - Admin only: Terminate team match\n\n"
        "📊 <b>ADMIN COMMANDS:</b>\n"
        "• /ping - Check bot response time\n"
        "• /stats - Owner/Sudo: View bot analytics\n"
        "• /broadcast - Owner/Sudo: Message all users\n"
        "• /addsudo / /rmsudo - Owner only: Manage admins"
    )
    await context.bot.send_message(update.effective_chat.id, help_text, parse_mode="HTML")
    try: await update.message.delete()
    except: pass

async def lobby_countdown(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    tl = context.job.data['time_left']
    if chat_id not in active_lobbies:
        return
    if tl == 0:
        lobby = active_lobbies[chat_id]
        if len(lobby["players"]) < 2:
            await context.bot.send_message(chat_id, "⚠️ Not enough players joined. Minimum 2 players required. Solo match cancelled.")
            del active_lobbies[chat_id]
        else:
            await start_game_logic(chat_id, context)
        return
    lobby = active_lobbies[chat_id]
    mentions = []
    for pid in lobby["players"]:
        pn = html.escape(await get_name(pid))
        mentions.append(f'<a href="tg://user?id={pid}">{pn}</a>')
    plist = ", ".join(mentions)

    if tl == 120:
        await context.bot.send_message(chat_id, f"⏳ <b>2 minutes left</b> to join!\n\n👥 Players: {plist}\n\n👉 /join to enter | /leave_solo to exit", parse_mode="HTML")
    elif tl == 60:
        await context.bot.send_message(chat_id, f"⏳ <b>1 minute left</b> to join!\n\n👥 Players: {plist}\n\n👉 /join to enter | /leave_solo to exit", parse_mode="HTML")
    elif tl == 30:
        await context.bot.send_message(chat_id, f"⏳ <b>30 seconds left</b> to join!\n\n👥 Players: {plist}\n\n👉 /join to enter", parse_mode="HTML")
    elif tl == 10:
        await context.bot.send_message(chat_id, f"⚠️ <b>10 seconds remaining!</b> Hurry up!\n\n👥 Players: {plist}", parse_mode="HTML")

async def start_game_logic(chat_id, context):
    if chat_id not in active_lobbies:
        return
    lobby = active_lobbies[chat_id]
    lobby["status"] = "live"
    # Cancel lobby timer jobs
    for j in context.job_queue.get_jobs_by_name(f"lobby_{chat_id}"):
        j.schedule_removal()

    mentions = []
    for uid in lobby["players"]:
        n = html.escape(await get_name(uid))
        mentions.append(f'<a href="tg://user?id={uid}">{n}</a>')
    await context.bot.send_message(chat_id,
        f"✅ <b>Game Starting!</b>\n\nPlayers: {', '.join(mentions)}\n\nGood luck! 🏏",
        parse_mode="HTML")

    match_data = await create_match(chat_id, lobby["players"])
    del active_lobbies[chat_id]

    players = match_data["lobby_players"]
    bat_id = players[0]
    bowl_id = players[1] if len(players) > 1 else players[0]
    await update_match(chat_id, {"current_batsman": bat_id, "current_bowler": bowl_id,
        "bowler_index": 2 % len(players), "match_status": "Live"})
    await notify_turn(chat_id, bat_id, bowl_id, context)

async def notify_turn(chat_id, batsman_id, bowler_id, context):
    bat_name = html.escape(await get_name(batsman_id))
    bowl_name = html.escape(await get_name(bowler_id))
    bot_info = await context.bot.get_me()
    kb = [[InlineKeyboardButton("📩 Send Bowl in DM", url=f"https://t.me/{bot_info.username}?start=bowl")]]

    bat_tag = f'<a href="tg://user?id={batsman_id}"><b>{bat_name}</b></a>'
    bowl_tag = f'<a href="tg://user?id={bowler_id}"><b>{bowl_name}</b></a>'

    await context.bot.send_message(chat_id,
        f"🏏 👉 {bat_tag} is batting\n"
        f"⚾ 👉 {bowl_tag} is bowling\n\n"
        f"🔢 Bowler, send a number <b>1-6</b> in DM!",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    try:
        await context.bot.send_message(bowler_id,
            f"⚾ <b>YOUR TURN TO BOWL!</b>\n\n"
            f"Batter: {bat_name}\n"
            f"Send a number <b>1–6</b> in this chat.", parse_mode="HTML")
    except Exception:
        await context.bot.send_message(chat_id,
            f"⚠️ Could not DM {bowl_tag}. Tell them to start the bot in PM first!",
            parse_mode="HTML")
    # Start 60s bowler timeout
    context.job_queue.run_once(bowl_timeout_cb, 30, chat_id=chat_id, data={"time_left": 30}, name=f"bowl_timeout_{chat_id}")

async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private":
        await update.message.reply_text("Use /play in a group!")
        return
    
    # Initialize votes for this chat ONLY if not already voting
    if chat_id not in play_votes:
        play_votes[chat_id] = {"voters": set(), "msg_id": None}
        context.job_queue.run_once(voting_expired_cb, 120, chat_id=chat_id, name=f"vote_expire_{chat_id}")
    
    count = len(play_votes[chat_id]["voters"])
    kb = [[InlineKeyboardButton(f"🏏 Vote to Play ({count}/2)", callback_data="vote_play")]]
    sent = await context.bot.send_photo(
        chat_id=chat_id,
        photo=HEADER_IMAGE,
        caption="🏟 <b>Match Request!</b>\n\nNeed <b>2 players</b> to vote to start the lobby.",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="HTML"
    )
    if play_votes[chat_id]["msg_id"] is None:
        play_votes[chat_id]["msg_id"] = sent.message_id
    try: await update.message.delete()
    except: pass

async def forcestart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    
    # Admin check
    cm = await context.bot.get_chat_member(chat_id, uid)
    if cm.status not in ['administrator', 'creator']:
        await update.message.reply_text("❌ Only admins can use /forcestart."); return

    # Skip votes
    for j in context.job_queue.get_jobs_by_name(f"vote_expire_{chat_id}"):
        j.schedule_removal()
    if chat_id in play_votes: del play_votes[chat_id]
    
    kb = [[InlineKeyboardButton("👤 Solo Mode", callback_data="mode_solo"),
           InlineKeyboardButton("👥 Team Mode", callback_data="mode_team")]]
    await update.message.reply_text("⚡ <b>Forced Start!</b> Choose your game mode:", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    try: await update.message.delete()
    except: pass

async def play_mode_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Solo/Team button selection."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    await query.answer()
    if query.data == "mode_solo":
        voters = authorized_voters.pop(chat_id, [])
        await _start_solo_lobby(update, context, voters=voters)
    elif query.data == "mode_team":
        if chat_id in team_lobbies:
            await edit_any(query, "⚠️ A team session is already active!")
            return
        from team_mode import _new_lobby
        team_lobbies[chat_id] = _new_lobby(uid)
        kb = [[InlineKeyboardButton("👑 Claim Host", callback_data="tclaim_host")]]
        await edit_any(query, "👥 <b>Team Mode Selected!</b>\n\nWho wants to be the game host? Click below:", InlineKeyboardMarkup(kb))

async def _start_solo_lobby(update, context, voters=None):
    """Original solo lobby creation logic."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    m = await get_match(chat_id)
    if (m and m["match_status"] == "Live") or chat_id in active_lobbies:
        await edit_any(query, "⚠️ A match or lobby is already active! Use /end_solo or /end_team to terminate it first.")
        return
    
    if voters:
        # Pre-authorized by 2-vote system
        players_to_join = list(set(voters + [uid])) # Ensure clicker is also in
        active_lobbies[chat_id] = {"host": players_to_join[0], "players": players_to_join, "votes": [], "status": "waiting", "open": True}
        
        mentions = []
        for pid in players_to_join:
            pn = html.escape(await get_name(pid))
            mentions.append(f'<a href="tg://user?id={pid}">{pn}</a>')
        plist = ", ".join(mentions)

        kb = [[InlineKeyboardButton("Join Game 🏏", callback_data="join_game")]]
        await edit_any(query,
            f"✅ <b>Votes Complete! Lobby Opened!</b>\n\n"
            f"👥 <b>Joined:</b> {plist}\n"
            f"⏳ <b>Time Left:</b> 2 minutes\n\n"
            f"👉 /join to enter | /leave_solo to exit",
            InlineKeyboardMarkup(kb))
        for delay, sl in [(0, 120), (60, 60), (90, 30), (110, 10), (120, 0)]:
            context.job_queue.run_once(lobby_countdown, delay, data={'time_left': sl},
                chat_id=chat_id, name=f"lobby_{chat_id}")
        return

    cm = await context.bot.get_chat_member(chat_id, uid)
    is_admin = cm.status in ['administrator', 'creator']
    if is_admin:
        active_lobbies[chat_id] = {"host": uid, "players": [uid], "votes": [], "status": "waiting", "open": True}
        kb = [[InlineKeyboardButton("Join Game 🏏", callback_data="join_game")]]
        await edit_any(query,
            f"🏏 <b>Match Lobby Opened!</b>\nPlayers joined: 1 | Min 2 needed\nJoining period: 2 minutes — game starts after!",
            InlineKeyboardMarkup(kb))
        for delay, sl in [(0, 120), (60, 60), (90, 30), (110, 10), (120, 0)]:
            context.job_queue.run_once(lobby_countdown, delay, data={'time_left': sl},
                chat_id=chat_id, name=f"lobby_{chat_id}")
    else:
        active_lobbies[chat_id] = {"host": uid, "players": [], "votes": [uid], "status": "waiting", "open": False}
        kb = [[InlineKeyboardButton("Vote to Open Lobby (1/2) 🗳", callback_data="vote_open_lobby")]]
        await edit_any(query,
            f"🏏 <b>{html.escape(update.effective_user.first_name)}</b> wants to start!\n🗳 2 votes needed.",
            InlineKeyboardMarkup(kb))

    m = await get_match(chat_id)
    if (m and m["match_status"] == "Live") or chat_id in active_lobbies:
        await update.message.reply_text("⚠️ A match or lobby is already active! Use /end_solo or /end_team to terminate it first.")
        return

    cm = await context.bot.get_chat_member(chat_id, uid)
    is_admin = cm.status in ['administrator', 'creator']

    if is_admin:
        active_lobbies[chat_id] = {"host": uid, "players": [uid], "votes": [], "status": "waiting", "open": True}
        uname = html.escape(await get_name(uid))
        utag = f'<a href="tg://user?id={uid}">{uname}</a>'
        
        kb = [[InlineKeyboardButton("Join Game 🏏", callback_data="join_game")]]
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=HEADER_IMAGE,
            caption=f"🏏 <b>Match Lobby Opened!</b>\n\n"
                    f"👑 <b>Host:</b> {utag}\n"
                    f"⏳ <b>Time Left:</b> 2 minutes\n\n"
                    f"👉 /join to enter | /leave_solo to exit",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML"
        )
        for delay, sl in [(0, 120), (60, 60), (90, 30), (110, 10), (120, 0)]:
            context.job_queue.run_once(lobby_countdown, delay, data={'time_left': sl},
                chat_id=chat_id, name=f"lobby_{chat_id}")
    else:
        # Member: needs 2 votes before lobby opens
        active_lobbies[chat_id] = {"host": uid, "players": [], "votes": [uid], "status": "waiting", "open": False}
        kb = [[InlineKeyboardButton("Vote to Open Lobby (1/2) 🗳", callback_data="vote_open_lobby")]]
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=HEADER_IMAGE,
            caption=f"🏏 <b>{html.escape(update.effective_user.first_name)}</b> wants to start a Cricket match!\n"
                    f"🗳 2 votes needed to open the lobby. Vote below!",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML"
        )
async def joingame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    
    if chat_id not in active_lobbies:
        return
    
    m = await get_match(chat_id)
    if m and m["match_status"] == "Live":
        msg = "⚠️ A match is already live in this chat! Use /end_solo or /end_team to terminate it first."
        if update.message: await update.message.reply_text(msg)
        return
    
    if chat_id not in active_lobbies:
        msg = "No active lobby. Use /play to start one."
        if update.message: await update.message.reply_text(msg)
        return
        
    lobby = active_lobbies[chat_id]
    if not lobby.get("open", False):
        msg = "Lobby is not open yet. Need more votes!"
        if update.message: await update.message.reply_text(msg)
        return

    user = update.effective_user
    if user.id not in lobby["players"]:
        lobby["players"].append(user.id)
        ud = await get_user(user.id)
        if not ud.get("username"):
            await update_user(user.id, {"username": user.first_name})
        count = len(lobby["players"])
        msg = f"✅ <b>{html.escape(user.first_name)}</b> joined! ({count} player{'s' if count > 1 else ''} in lobby)"
        
        if update.callback_query:
            await context.bot.send_message(chat_id, msg, parse_mode="HTML")
        else:
            await update.message.reply_text(msg, parse_mode="HTML")
    else:
        msg = "Already in lobby!"
        if update.message: await update.message.reply_text(msg)
        elif update.callback_query: await update.callback_query.answer(msg, show_alert=True)
    
    try: await update.message.delete()
    except: pass
    return

async def leave_solo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    if chat_id not in active_lobbies:
        await update.message.reply_text("No active lobby to leave."); return
    lobby = active_lobbies[chat_id]
    if uid in lobby["players"]:
        lobby["players"].remove(uid)
        await update.message.reply_text(f"✅ <b>{html.escape(update.effective_user.first_name)}</b> left the lobby.", parse_mode="HTML")
        if not lobby["players"]:
            del active_lobbies[chat_id]
            # Cancel jobs
            for j in context.job_queue.get_jobs_by_name(f"lobby_{chat_id}"):
                j.schedule_removal()
            await update.message.reply_text("Lobby closed as it became empty.")
    else:
        await update.message.reply_text(f"✅ <b>{html.escape(n)}</b> has left the lobby.", parse_mode="HTML")
    try: await update.message.delete()
    except: pass

async def vote_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    if chat_id not in active_lobbies:
        try: await q.answer("Lobby no longer active."); return
        except: return
    lobby = active_lobbies[chat_id]
    
    # Handle both Opening lobby and Starting match votes
    if q.data == "vote_open_lobby":
        if uid in lobby["votes"]:
            try: await q.answer("You already voted!", show_alert=True); return
            except: return
        lobby["votes"].append(uid)
        cv = len(lobby["votes"])
        if cv >= 2:
            lobby["open"] = True
            lobby["players"] = lobby["votes"].copy()
            await q.answer("Lobby Opened!")
            await q.edit_message_text(f"✅ <b>Lobby Opened!</b>\nPlayers: {cv}\nJoining Period: 2 minutes.", parse_mode="HTML")
            kb = [[InlineKeyboardButton("Join Game 🏏", callback_data="join_game")]]
            await context.bot.send_message(chat_id, "🏟 <b>Solo Match Lobby is now OPEN!</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
            # Start countdown
            for delay, sl in [(0, 120), (60, 60), (90, 30), (110, 10), (120, 0)]:
                context.job_queue.run_once(lobby_countdown, delay, data={'time_left': sl},
                    chat_id=chat_id, name=f"lobby_{chat_id}")
        else:
            await q.answer(f"Vote recorded ({cv}/2)")
            await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"Vote to Open Lobby ({cv}/2) 🗳", callback_data="vote_open_lobby")]]))
    else:
        # Original vote to start (for backward compat if button exists)
        try: await q.answer("Voting to start is now automatic after 2 players join!")
        except: pass

async def join_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    d = query.data
    try:
        if d == "join_game":
            try: await query.answer()
            except: pass
            await joingame(update, context)
        elif d == "vote_start" or d == "vote_open_lobby":
            await vote_start(update, context)
        elif d.startswith("confirm_end_"):
            await confirm_end_action(update, context)
        elif d == "vote_play":
            uid = update.effective_user.id
            
            # If voting has expired or never started, reject
            if chat_id not in play_votes:
                await query.answer("⏰ Voting has expired or already completed!", show_alert=True)
                return
            
            vote_data = play_votes[chat_id]
            if uid in vote_data["voters"]:
                await query.answer("You already voted!", show_alert=True)
                return

            vote_data["voters"].add(uid)
            count = len(vote_data["voters"])
            
            if count >= 2:
                # Cancel the expiry timer since votes are complete
                for j in context.job_queue.get_jobs_by_name(f"vote_expire_{chat_id}"):
                    j.schedule_removal()
                authorized_voters[chat_id] = list(vote_data["voters"])
                del play_votes[chat_id]
                
                kb = [
                    [InlineKeyboardButton("👤 Solo Mode", callback_data="mode_solo"),
                     InlineKeyboardButton("👥 Team Mode", callback_data="mode_team")]
                ]
                try:
                    await query.edit_message_caption(
                        caption="🏁 <b>Votes Complete!</b>\nSelect your game mode to begin:",
                        reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
                except:
                    await query.edit_message_text(
                        "🏁 <b>Votes Complete!</b>\nSelect your game mode to begin:",
                        reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
            else:
                kb = [[InlineKeyboardButton(f"🏏 Vote to Play ({count}/2)", callback_data="vote_play")]]
                try:
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))
                except:
                    pass

        elif d.startswith("toss_"):
            await toss_choice(update, context)
        elif d.startswith("mode_"):
            await play_mode_select(update, context)
        elif d == "tvote_host":
            await host_vote_callback(update, context)
        elif d.startswith("tend_") or d == "tclaim_host":
            await confirm_end_team(update, context)
        else:
            # Fallback to answer any unknown queries to stop spinner
            try: await query.answer()
            except: pass
            
    except Exception as e:
        logger.error(f"Error in join_button: {e}")
        try: await query.answer("❌ An error occurred. Try again.", show_alert=True)
        except: pass
    finally:
        # Extra safety: Ensure the spinner is stopped if not answered
        try: await query.answer()
        except: pass

async def member_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    # 1. Team Mode Check
    lobby = get_lobby(chat_id)
    if lobby:
        from team_mode import member_list as team_member_list
        await team_member_list(update, context)
        return
        
    # 2. Solo Mode Check
    match = await get_match(chat_id)
    if not match:
        await update.message.reply_text("No active game in this chat.")
        return
        
    sb = match["scoreboard"]
    lines = [f"📊 <b>Solo Match - Players List</b>\n\n"]
    for i, pid in enumerate(match["lobby_players"], 1):
        name = html.escape(await get_name(pid))
        is_out = sb[str(pid)]["is_out"]
        status = "❌" if is_out else "🟢"
        if not is_out:
            if pid == match["current_batsman"]:
                status += " 🏏"
            elif pid == match["current_bowler"]:
                status += " 🎯"
        lines.append(f"{i}. <b>{name}</b> — {status}\n")
    
    await update.message.reply_text("".join(lines), parse_mode="HTML")
    try: await update.message.delete()
    except: pass

async def end_solo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    cm = await context.bot.get_chat_member(chat_id, uid)
    if cm.status not in ['administrator', 'creator']:
        await update.message.reply_text("❌ Only administrators can end the game."); return

    keyboard = [
        [
            InlineKeyboardButton("✅ YES, END GAME", callback_data="confirm_end_yes"),
            InlineKeyboardButton("❌ NO, CANCEL", callback_data="confirm_end_no")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "❓ <b>Are you sure you want to end the solo game?</b>\nThis will cancel the lobby or the active match.",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )



async def confirm_end_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    cm = await context.bot.get_chat_member(chat_id, user_id)
    if cm.status not in ['administrator', 'creator']:
        await query.answer("Only administrators can confirm this action.", show_alert=True)
        return

    if query.data == "confirm_end_yes":
        if chat_id in active_lobbies:
            del active_lobbies[chat_id]
            for j in context.job_queue.get_jobs_by_name(f"lobby_{chat_id}"):
                j.schedule_removal()
            await query.edit_message_text("✅ Lobby has been cancelled by an admin.")
        else:
            match = await get_match(chat_id)
            if match and match["match_status"] == "Live":
                await finish_match(chat_id, context)
                await query.edit_message_text("🏁 The match has been ended by an admin.")
            else:
                await query.edit_message_text("No active game to end.")
    else:
        await query.edit_message_text("❌ Action cancelled. The game continues!")

async def unified_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try: await update.message.delete()
    except: pass
    if get_lobby(chat_id):
        await score_team(update, context)
    else:
        await context.bot.send_photo(chat_id, photo=HEADER_IMAGE)
        await score_solo(update, context)

async def score_solo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    match = await get_match(chat_id)
    if not match or match["match_status"] != "Live":
        await update.message.reply_text("🚫 No game running.")
        return

    sb = match["scoreboard"]
    players = match["lobby_players"]
    total_runs = sum(sb[str(u)]["runs"] for u in players)
    total_balls = sum(sb[str(u)]["balls_faced"] for u in players)
    total_wkts = sum(1 for u in players if sb[str(u)]["is_out"])
    overs = total_balls // 6
    extra = total_balls % 6
    rr = round(total_runs / (total_balls / 6), 2) if total_balls > 0 else 0.0

    state_emojis = ["⚪", "🟠", "🟣", "🔵", "🔴", "🟡", "🟢"]
    cur_bat = match.get("current_batsman")
    cur_bowl = match.get("current_bowler")

    lines = [f"<b>📊 Current Solo Score</b>\n\n",
             f"─────⊱ Sᴏʟᴏ Pʟᴀʏᴇʀ ⊰────\n\n"]

    for idx, uid in enumerate(players, 1):
        s = sb[str(uid)]
        n = html.escape(await get_name(uid))
        bat_h = ", ".join(str(x) for x in s["bat_history"]) if s["bat_history"] else "-"
        bowl_h = ", ".join(str(x) for x in s["bowl_history"]) if s["bowl_history"] else "-"
        sr = round((s["runs"]/s["balls_faced"])*100, 2) if s["balls_faced"] > 0 else 0

        if uid == cur_bat:
            dot = "🟠"  # batting now
        elif s["is_out"]:
            dot = "❌"  # out
        elif uid == cur_bowl:
            dot = "🟣"  # bowling now
        else:
            dot = state_emojis[idx % len(state_emojis)]

        lines.append(
            f"{idx}. {dot} {n} = <b>{s['runs']}</b>({s['balls_faced']})\n"
            f"    ╰⊚ 4️⃣s: <b>{s['fours']:02d}</b>, 6️⃣s: <b>{s['sixes']:02d}</b> - ID: <code>{uid}</code>\n"
            f"      ╰⊚ Bat: ({bat_h})\n"
            f"      ╰⊚ Bowl: ({bowl_h})\n\n"
        )
    await update.message.reply_text("".join(lines), parse_mode="HTML")

async def userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = await get_user(uid)
    name = html.escape(u.get("first_name") or u.get("username", update.effective_user.first_name))
    import datetime
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    
    hs = u.get("highest_score", 0)
    hs_b = u.get("highest_score_balls", 0)
    runs = u.get("total_runs", 0)
    balls = u.get("total_balls", 0)
    wkts = u.get("total_wickets", 0)
    fours = u.get("fours", 0)
    sixes = u.get("sixes", 0)
    cents = u.get("centuries", 0)
    fifs = u.get("fifties", 0)
    ducks = u.get("ducks", 0)
    hats = u.get("hat_tricks", 0)
    
    sr = round((runs / balls) * 100, 2) if balls > 0 else 0
    econ = round(u.get("runs_conceded", 0) / (u.get("balls_bowled", 1)/6), 2) if u.get("balls_bowled", 0) > 0 else 0

    moms_bat = u.get("mom_bat", 0)
    moms_bowl = u.get("mom_bowl", 0)
    moms = moms_bat + moms_bowl
    
    cap_wins = u.get("captain_wins", 0)
    cap_losses = u.get("captain_losses", 0)
    cap_total = cap_wins + cap_losses
    cap_win_pct = round((cap_wins / cap_total) * 100, 1) if cap_total > 0 else 0

    lines = [
        f"🏏 <b>Stats Summary</b>\n",
        f"👤 User: {name}\n",
        f"🆔 User ID: <code>{uid}</code>\n",
        f"📅 Date: {date_str}\n",
        f"─────⊱◈◈◈⊰─────\n",
        f"🏆 Highest Score: <b>{hs}</b>({hs_b} Balls)\n",
        f"🎮 Best Game Host: {u.get('host_count', 0)}\n",
        f"📊 Runs: <b>{runs}</b> ({balls})\n",
        f"🎯 Wickets: <b>{wkts}</b>\n",
        f"✨ Fours: <b>{fours}</b>\n",
        f"🚀 Sixes: <b>{sixes}</b>\n",
        f"🔥 Centuries: {cents}\n",
        f"⭐ Fifties: {fifs}\n",
        f"🦆 Ducks: {ducks}\n",
        f"🎩 Hat-Tricks: {hats}\n",
        f"⚡ Strike Rate: {sr}\n",
        f"🎯 Economy Rate: {econ}\n",
        f"─────⊱◈◈◈⊰─────\n",
        f"🏅 Man of the Match: {moms}\n\n",
        f" ╰⊚(🏏:{moms_bat}) + (⚾:{moms_bowl})\n\n",
        f"─────⊱◈◈◈⊰─────\n",
        f"🧢 Best captain: {cap_total} (🏆: {cap_win_pct}%)\n",
        f" ╰⊚(🏆: {cap_wins}) + (😞:{cap_losses}) for team\n"
    ]
    if uid in banned_users:
        remaining = int(banned_users[uid] - time.time())
        if remaining > 0:
            lines.append(f"🚫 <b>BANNED:</b> {remaining}s remaining\n")
        else:
            del banned_users[uid]
    
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=HEADER_IMAGE,
        caption="".join(lines),
        parse_mode="HTML"
    )

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await update.message.delete()
    except: pass
    top_users = await users_col.find().sort("total_runs", -1).limit(10).to_list(None)
    if not top_users:
        await update.message.reply_text("No stats available yet."); return
    
    lines = ["🏆 <b>Global Runs Leaderboard</b> 🏆\n\n"]
    for i, u in enumerate(top_users, 1):
        name = html.escape(u.get("first_name") or u.get("username") or f"Player {u['user_id']}")
        runs = u.get("total_runs", 0)
        lines.append(f"{i}. <b>{name}</b> — <code>{runs}</code> runs\n")
    
    await update.message.reply_text("".join(lines), parse_mode="HTML")

async def reset_overs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    try: await update.message.delete()
    except: pass
    lobby = get_lobby(chat_id)
    if not lobby or lobby["host_id"] != uid:
        await update.message.reply_text("❌ Only the host can reset overs."); return
    if lobby["phase"] not in ["overs", "live_1st", "live_2nd"]:
        await update.message.reply_text("Overs cannot be reset at this stage."); return
    lobby["phase"] = "overs"
    await update.message.reply_text("🔄 <b>Overs have been reset!</b>\nUse /setovers <num> to set again.", parse_mode="HTML")

async def forcestart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = update.effective_user.id

    cm = await context.bot.get_chat_member(chat_id, uid)
    if cm.status not in ['administrator', 'creator']:
        await update.message.reply_text("❌ Only administrators can use /forcestart.")
        return

    m = await get_match(chat_id)
    if m and m["match_status"] == "Live":
        await update.message.reply_text("⚠️ A match is already live!")
        return

    if chat_id not in active_lobbies:
        await update.message.reply_text("❌ No lobby found. Use /play first to create one.")
        return

    lobby = active_lobbies[chat_id]
    # BYPASS VOTING — force open
    lobby["open"] = True
    if uid not in lobby["players"]:
        lobby["players"].append(uid)
        ud = await get_user(uid)
        if not ud.get("username"):
            await update_user(uid, {"username": update.effective_user.first_name})

    if len(lobby["players"]) >= 2:
        await update.message.reply_text("✅ Force starting the match now!")
        await start_game_logic(chat_id, context)
    else:
        for j in context.job_queue.get_jobs_by_name(f"lobby_{chat_id}"):
            j.schedule_removal()
        kb = [[InlineKeyboardButton("Join Game 🏏", callback_data="join_game")]]
        await update.message.reply_text(
            f"⚡ <b>Lobby Force-Opened by Admin!</b>\nPlayers: {len(lobby['players'])}/2\nWaiting for more...",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        for delay, sl in [(0, 120), (60, 60), (90, 30), (110, 10), (120, 0)]:
            context.job_queue.run_once(lobby_countdown, delay, data={'time_left': sl},
                chat_id=chat_id, name=f"lobby_{chat_id}")

async def handle_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text not in ["0", "1", "2", "3", "4", "5", "6"]:
        return
    num = int(text)
    uid = update.effective_user.id

    if update.effective_chat.type == "private":
        # BOWLER sends ball in DM
        # 1. Check Solo Match
        match = await matches_col.find_one({"current_bowler": int(uid), "match_status": "Live"})
        if match:
            delivery = match.get("current_delivery", {"status": "waiting_bowler"})
            if delivery.get("status") == "waiting_bowler":
                cancel_turn_jobs(match["match_id"], context)
                await update_match(match["match_id"], {
                    "current_delivery.bowler_num": num, "current_delivery.status": "waiting_batter"})
                gid = str(match["match_id"])
                # link to group with high message id to force scroll to bottom
                link = f"https://t.me/c/{gid[4:]}/999999999" if gid.startswith("-100") else "#"
                kb = [[InlineKeyboardButton("Return To Group 🏟", url=link)]]
                await update.message.reply_text(
                    f"<b>{html.escape(update.effective_user.first_name)}</b>\n{num}\n⚾️ Ball Delivered!\n\n"
                    f"<i>Go back to the group to see the result!</i>",
                    reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
                bat_name = html.escape(await get_name(match["current_batsman"]))
                await context.bot.send_message(match["match_id"],
                    f"⚾️ <b>Ball Delivered!</b>\n🏏 <a href='tg://user?id={match['current_batsman']}'>"
                    f"{bat_name}</a>, send your shot (0-6)!", parse_mode="HTML")
                context.job_queue.run_once(bat_timeout_cb, 30, chat_id=match["match_id"],
                    data={"time_left": 30}, name=f"bat_timeout_{match['match_id']}")
                return

        # 2. Check Team Match
        for chat_id, lobby in team_lobbies.items():
            if lobby.get("current_bowler") == uid and lobby.get("delivery", {}).get("status") == "waiting_bowler":
                _cancel_team_jobs(chat_id, context)
                lobby["delivery"]["bowler_num"] = num
                lobby["delivery"]["status"] = "waiting_batter"
                
                gid = str(chat_id)
                link = f"https://t.me/c/{gid[4:]}/999999999" if gid.startswith("-100") else "#"
                kb = [[InlineKeyboardButton("Return To Group 🏟", url=link)]]
                await update.message.reply_text(
                    f"<b>{html.escape(update.effective_user.first_name)}</b>\n{num}\n⚾️ Ball Delivered (Team Match)!\n\n"
                    f"<i>Go back to the group for the batter's turn!</i>",
                    reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
                
                # Notify group
                sid = lobby["striker"]; bid = lobby["current_bowler"]
                s_name = await _get_team_name(context, chat_id, sid, "Batter")
                b_name = await _get_team_name(context, chat_id, bid, "Bowler")
                
                await context.bot.send_message(chat_id,
                    f"⚾ <b>Ball delivered!</b>\n"
                    f"🏏 👍 <a href='tg://user?id={sid}'>{s_name}</a>, send your shot (0-6) within 1 minute!",
                    parse_mode="HTML")
                context.job_queue.run_once(_bat_timeout_team, 30, chat_id=chat_id, data={"time_left": 30}, name=f"tbat_{chat_id}")
                return
        
        # If we reached here, no active turn was found for this user in any game
        await update.message.reply_text("❌ It's not your turn to bowl, or the game is waiting for the batter. Please check the group!")
        return
    else:
        try:
            chat_id = update.effective_chat.id
            if get_lobby(chat_id):
                if text not in ["1","2","3","4","5","6"]: return
                if await handle_team_number(update, context): return
            match = await get_match(chat_id)
            if not match or match["match_status"] != "Live": return
            if text not in ["0","1","2","3","4","5","6"]: return
            num = int(text)
            if match["current_delivery"]["status"] == "waiting_bowler":
                if num == 0:
                    await update.message.reply_text("❌ Bowler cannot pick 0! Send 1-6.")
                    return
                return
            if match["current_batsman"] != uid:
                if match["current_bowler"] == uid:
                    await update.message.reply_text("❌ You are the <b>Bowler</b>! Send in <b>DM</b>.", parse_mode="HTML")
                return
            delivery = match.get("current_delivery", {"status": "waiting_bowler"})
            if delivery.get("status") != "waiting_batter":
                return
            cancel_turn_jobs(chat_id, context)
            await update.message.reply_text("👍")
            bowler_num = delivery.get("bowler_num", 1)
            match = await get_match(chat_id)
            await process_ball(chat_id, bowler_num, num, context, match)
        except Exception as e:
            logger.error(f"Error in handle_number: {e}")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_time = time.time()
    msg = await update.message.reply_text("🚀 Pinging...")
    end_time = time.time()
    latency = round((end_time - start_time) * 1000, 2)
    await msg.edit_text(f"🏁 <b>Pong!</b>\nLatency: <code>{latency}ms</code>", parse_mode="HTML")

async def bot_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID and uid not in sudo_users:
        await update.message.reply_text("❌ Owner/Sudo only."); return
    count = await users_col.count_documents({})
    active_matches = await matches_col.count_documents({"match_status": "Live"})
    active_solo_lobbies = len(active_lobbies)
    active_team_lobbies = len(team_lobbies)
    await update.message.reply_text(
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👤 Total Users: <code>{count}</code>\n"
        f"🏟 Active Solo Matches: <code>{active_matches}</code>\n"
        f"⏳ Solo Lobbies: <code>{active_solo_lobbies}</code>\n"
        f"⏳ Team Lobbies: <code>{active_team_lobbies}</code>",
        parse_mode="HTML"
    )

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID and uid not in sudo_users:
        await update.message.reply_text("❌ Owner/Sudo only."); return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>"); return
    text = update.message.text.partition(' ')[2]
    users = await users_col.find().to_list(None)
    sent = 0; failed = 0
    progress = await update.message.reply_text(f"🚀 <b>Broadcast Started...</b>\nTarget: {len(users)} users", parse_mode="HTML")
    for user in users:
        try:
            await context.bot.send_message(user['user_id'], text)
            sent += 1
            await asyncio.sleep(0.05)
        except: failed += 1
    await progress.edit_text(f"🏁 <b>Broadcast Complete!</b>\n\n✅ Sent: {sent}\n❌ Failed: {failed}", parse_mode="HTML")

async def add_sudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only Owner can add Sudos."); return
    if not context.args:
        await update.message.reply_text("Usage: /addsudo <id>"); return
    try:
        sid = int(context.args[0])
        sudo_users.add(sid)
        await add_sudo_user(sid)
        await update.message.reply_text(f"✅ User <code>{sid}</code> added as Sudo and saved to database.", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in add_sudo: {e}")
        await update.message.reply_text("❌ Invalid User ID or DB error.")

async def rm_sudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only Owner can remove Sudos."); return
    if not context.args:
        await update.message.reply_text("Usage: /rmsudo <id>"); return
    try:
        sid = int(context.args[0])
        if sid in sudo_users:
            sudo_users.remove(sid)
            await remove_sudo_user(sid)
            await update.message.reply_text(f"✅ User <code>{sid}</code> removed from Sudo and database.", parse_mode="HTML")
        else: await update.message.reply_text("❌ User not in Sudo list.")
    except Exception as e:
        logger.error(f"Error in rm_sudo: {e}")
        await update.message.reply_text("❌ Invalid User ID or DB error.")

async def log_bot_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log when the bot is added to a new group."""
    result = update.my_chat_member
    if not LOG_CHANNEL_ID: return
    
    chat = result.chat
    user = update.effective_user
    new_status = result.new_chat_member.status
    
    if result.old_chat_member.status in ["left", "kicked"] and new_status in ["member", "administrator"]:
        msg = (f"📥 <b>Bot Added to Group!</b>\n\n"
               f"👥 Group: {html.escape(chat.title)} (<code>{chat.id}</code>)\n"
               f"👤 Added by: {html.escape(user.first_name)} (<code>{user.id}</code>)")
        await context.bot.send_message(LOG_CHANNEL_ID, msg, parse_mode="HTML")
    elif new_status in ["left", "kicked"]:
        msg = (f"📤 <b>Bot Removed from Group</b>\n\n"
               f"👥 Group: {html.escape(chat.title)} (<code>{chat.id}</code>)")
        await context.bot.send_message(LOG_CHANNEL_ID, msg, parse_mode="HTML")

def main():
    logger.info("🚀 Starting bot...")
    # Start anti-idle server
    threading.Thread(target=run_web, daemon=True).start()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Initialize sudos from DB
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_sudo())
    
    # Priority 1: Callbacks (Must be fast!)
    app.add_handler(CallbackQueryHandler(join_button))
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("play", play))
    app.add_handler(CommandHandler("join", joingame))
    app.add_handler(CommandHandler("leave_solo", leave_solo))
    app.add_handler(CommandHandler("forcestart", forcestart))
    app.add_handler(CommandHandler("joingame", joingame))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("reset_over", reset_overs))
    app.add_handler(CommandHandler("resetover", reset_overs))
    app.add_handler(CommandHandler("endgame", end_solo_cmd))
    app.add_handler(CommandHandler("end_solo", end_solo_cmd))
    app.add_handler(ChatMemberHandler(log_bot_add, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CommandHandler("score", unified_score))
    app.add_handler(CommandHandler("userinfo", userinfo))
    app.add_handler(CommandHandler("top", leaderboard))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    
    # Admin commands
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("stats", bot_stats))
    app.add_handler(CommandHandler("total", bot_stats))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("addsudo", add_sudo))
    app.add_handler(CommandHandler("rmsudo", rm_sudo))
    # Team mode commands
    app.add_handler(CommandHandler("hostchange", hostchange))
    app.add_handler(CommandHandler("vote4host_change", vote4host_change))
    app.add_handler(CommandHandler("create_team", create_team))
    app.add_handler(CommandHandler("join_teamA", join_team_a))
    app.add_handler(CommandHandler("join_teamB", join_team_b))
    app.add_handler(CommandHandler("add_a", add_to_a))
    app.add_handler(CommandHandler("add_b", add_to_b))
    app.add_handler(CommandHandler("remove_a", remove_from_a))
    app.add_handler(CommandHandler("remove_b", remove_from_b))
    app.add_handler(CommandHandler("addcap_a", addcap_a))
    app.add_handler(CommandHandler("addcap_b", addcap_b))
    app.add_handler(CommandHandler("remove_cap_a", remove_cap_a))
    app.add_handler(CommandHandler("remove_cap_b", remove_cap_b))
    app.add_handler(CommandHandler("toss", toss))
    app.add_handler(CommandHandler("setovers", setovers))
    app.add_handler(CommandHandler("member_list", member_list_cmd))
    app.add_handler(CommandHandler("solo_list", member_list_cmd))
    app.add_handler(CommandHandler("play_team", play_team))
    app.add_handler(CommandHandler("bowling", bowling))
    app.add_handler(CommandHandler("batting", batting_cmd))
    app.add_handler(CommandHandler("swap", swap))
    app.add_handler(CommandHandler("score_team", unified_score))
    app.add_handler(CommandHandler("end_team", end_team))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_number))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
