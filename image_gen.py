from PIL import Image, ImageDraw, ImageFont
import io

def generate_hero_card(batting_hero_name, batting_runs, batting_balls, batting_sixes, batting_fours,
                      bowling_hero_name, bowling_wickets, bowling_overs, bowling_runs, bowling_econ):
    img = Image.new('RGB', (800, 400), color=(15, 23, 42))
    d = ImageDraw.Draw(img)
    
    try:
        font_title = ImageFont.truetype("arial.ttf", 30)
        font_text = ImageFont.truetype("arial.ttf", 20)
    except IOError:
        font_title = ImageFont.load_default()
        font_text = ImageFont.load_default()
        
    d.text((300, 20), "SOLO MATCH HEROES", fill=(255, 255, 255), font=font_title)
    
    d.text((100, 80), "BATTING HERO", fill=(251, 146, 60), font=font_title)
    d.text((100, 150), batting_hero_name, fill=(255, 255, 255), font=font_title)
    d.text((50, 250), f"{batting_runs}\nRUNS", fill=(251, 146, 60), font=font_text)
    d.text((150, 250), f"{batting_balls}\nBALLS", fill=(251, 146, 60), font=font_text)
    d.text((250, 250), f"{batting_sixes}\nSIXES", fill=(251, 146, 60), font=font_text)
    d.text((350, 250), f"{batting_fours}\nFOURS", fill=(251, 146, 60), font=font_text)
    
    d.line([(400, 80), (400, 350)], fill=(255, 255, 255), width=2)
    
    d.text((500, 80), "BOWLING HERO", fill=(56, 189, 248), font=font_title)
    d.text((500, 150), bowling_hero_name, fill=(255, 255, 255), font=font_title)
    d.text((450, 250), f"{bowling_wickets}\nWKTS", fill=(56, 189, 248), font=font_text)
    d.text((550, 250), f"{bowling_overs}\nOVS", fill=(56, 189, 248), font=font_text)
    d.text((650, 250), f"{bowling_runs}\nRUNS", fill=(56, 189, 248), font=font_text)
    d.text((750, 250), f"{bowling_econ}\nECON", fill=(56, 189, 248), font=font_text)
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf
