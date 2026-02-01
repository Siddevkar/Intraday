import pandas as pd
import requests
import time
import math
import os
from datetime import datetime, timedelta
from SmartApi import SmartConnect
import pyotp

# --- PUBLIC SAFE CONFIGURATION (ENV VARS) ---
# This allows you to host on Public GitHub safely.
API_KEY = os.environ.get('API_KEY')
CLIENT_ID = os.environ.get('CLIENT_ID')
PIN = os.environ.get('PIN')
TOTP_KEY = os.environ.get('TOTP_KEY')

# --- STRATEGY PARAMETERS ---
CAPITAL_PER_TRADE = 5000       
LEVERAGE = 5.0                 
ATR_MULTIPLIER = 2.0           
NIFTY_TOKEN = "99926000"       
MAX_OPEN_POSITIONS = 1         
OI_BLAST_THRESHOLD = 3.0       # >3% Jump required

def login():
    try:
        if not API_KEY or not CLIENT_ID or not PIN or not TOTP_KEY:
            print("❌ ERROR: Secrets not found. Check GitHub Settings.")
            exit()
            
        obj = SmartConnect(api_key=API_KEY)
        data = obj.generateSession(CLIENT_ID, PIN, pyotp.TOTP(TOTP_KEY).now())
        return obj
    except Exception as e:
        print(f"❌ Login Failed: {e}")
        exit()

def get_ist_time():
    # GitHub Servers are UTC. We convert to IST.
    utc_now = datetime.utcnow()
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    return ist_now

def get_tokens_map():
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    try:
        data = requests.get(url).json()
        futures_map = {}
        # 1. Group Futures
        for item in data:
            if item['instrumenttype'] == 'FUTSTK' and item['exchangeseg'] == 'NFO':
                name = item['name']
                if name not in futures_map: futures_map[name] = []
                try:
                    exp = datetime.strptime(item['expirydate'], '%d%b%Y')
                    futures_map[name].append({'date': exp, 'token': item['token']})
                except: continue
        
        # 2. Map Equity to Near-Month Future
        final_map = {}
        for name, contracts in futures_map.items():
            contracts.sort(key=lambda x: x['date'])
            if not contracts: continue
            eq_token = None
            for item in data:
                if item['name'] == name and item['symbol'].endswith('-EQ') and item['exchangeseg'] == 'NSE':
                    eq_token = item['token']
                    break
            if eq_token:
                final_map[name] = {'eq': eq_token, 'fut': contracts[0]['token']}
        return final_map
    except: return {}

def get_yesterday_levels(obj, token):
    try:
        hist_params = {
            "exchange": "NSE", "symboltoken": token, "interval": "ONE_DAY",
            "fromdate": (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d %H:%M'), 
            "todate": datetime.now().strftime('%Y-%m-%d %H:%M')
        }
        data = obj.getCandleData(hist_params)
        df = pd.DataFrame(data['data'], columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        yesterday = df.iloc[-2] 
        return float(yesterday['high']), float(yesterday['low'])
    except: return None, None

def get_intraday_metrics(obj, token):
    try:
        from_time = (datetime.now() - timedelta(hours=4)).strftime('%Y-%m-%d %H:%M')
        to_time = datetime.now().strftime('%Y-%m-%d %H:%M')
        hist_params = {
            "exchange": "NSE", "symboltoken": token, "interval": "FIVE_MINUTE",
            "fromdate": from_time, "todate": to_time
        }
        data = obj.getCandleData(hist_params)
        if not data or not data.get('data'): return None

        df = pd.DataFrame(data['data'], columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        df[['high','low','close','volume']] = df[['high','low','close','volume']].apply(pd.to_numeric)
        
        # VWAP & 10 EMA
        df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()
        df['ema_10'] = df['close'].ewm(span=10, adjust=False).mean()
        
        # ATR Calculation
        df['h-l'] = df['high'] - df['low']
        df['h-pc'] = abs(df['high'] - df['close'].shift(1))
        df['l-pc'] = abs(df['low'] - df['close'].shift(1))
        df['tr'] = df[['h-l', 'h-pc', 'l-pc']].max(axis=1)
        df['atr'] = df['tr'].rolling(14).mean()

        latest = df.iloc[-1]
        return {
            "close": float(latest['close']),
            "vwap": float(latest['vwap']),
            "ema_10": float(latest['ema_10']),
            "atr": float(latest['atr']) if not pd.isna(latest['atr']) else float(latest['h-l'])
        }
    except: return None

def check_oi_blast(obj, fut_token):
    """
    Returns TRUE if Future OI > 3% jump (Absolute Change)
    """
    try:
        quote = obj.getMarketData("NFO", fut_token) 
        if quote and 'data' in quote:
            current_oi = float(quote['data']['oi'])  
            start_day_oi = float(quote['data'].get('opninterest', current_oi)) 
            if start_day_oi == 0: return False

            oi_change_pct = ((current_oi - start_day_oi) / start_day_oi) * 100
            
            # Check for > 3% OR < -3% (Big Move)
            if abs(oi_change_pct) > OI_BLAST_THRESHOLD:
                print(f"🔥 OI BLAST DETECTED: {oi_change_pct:.2f}%")
                return True
    except: return False
    return False

# --- 2:50 PM FORCE EXIT ---
def check_time_exit(obj):
    ist_now = get_ist_time()
    # IST 14:50 = 2:50 PM
    if ist_now.hour == 14 and ist_now.minute >= 50:
        print(f"⏰ 2:50 PM FORCE EXIT TRIGGERED.")
        positions = obj.position()
        if positions and positions['data']:
            for pos in positions['data']:
                qty = int(pos['netqty'])
                
                # SAFETY LOCK: Only close 'INTRADAY' product type
                # This protects your 'MARGIN' (Swing) trades
                if qty != 0 and pos['producttype'] == 'INTRADAY':
                    print(f"🚨 Selling {pos['tradingsymbol']}")
                    obj.placeOrder({
                        "variety": "NORMAL", "tradingsymbol": pos['tradingsymbol'], "symboltoken": pos['symboltoken'],
                        "transactiontype": "SELL" if qty > 0 else "BUY", "exchange": "NSE", "ordertype": "MARKET",
                        "producttype": "INTRADAY", "duration": "DAY", "quantity": abs(qty)
                    })
        return True
    return False

# --- 10 EMA TRAILING ---
def check_and_trail_sl(obj, token_map):
    print("🕵️ Managing Open Positions...")
    active_count = 0
    try:
        positions = obj.position()
        if not positions or not positions['data']: return 0
        orders = obj.orderBook()
        orders_data = orders['data'] if orders and orders['data'] else []

        for pos in positions['data']:
            qty = int(pos['netqty'])
            if qty == 0: continue
            if pos['producttype'] == 'INTRADAY': active_count += 1
            
            symbol = pos['tradingsymbol']
            token = pos['symboltoken']
            is_long = qty > 0
            
            sl_order = None
            for o in orders_data:
                if o['tradingsymbol'] == symbol and o['status'] == 'trigger pending':
                    if (is_long and o['transactiontype'] == 'SELL') or (not is_long and o['transactiontype'] == 'BUY'):
                        sl_order = o
                        break
            
            if not sl_order: continue
            metrics = get_intraday_metrics(obj, token)
            if not metrics: continue
            ema_10 = metrics['ema_10']
            curr_sl = float(sl_order['triggerprice'])

            # Trail Logic
            if is_long:
                new_sl = round((ema_10 - (ema_10 * 0.0005)) * 20) / 20
                if new_sl > curr_sl:
                    print(f"📈 Trailing Up: {curr_sl} -> {new_sl}")
                    obj.modifyOrder({
                        "variety": "STOPLOSS", "orderid": sl_order['orderid'], "ordertype": "STOPLOSS_LIMIT",
                        "producttype": "INTRADAY", "duration": "DAY", "price": new_sl, "quantity": abs(qty),
                        "triggerprice": new_sl, "tradingsymbol": symbol, "symboltoken": token, "exchange": "NSE"
                    })
            else:
                new_sl = round((ema_10 + (ema_10 * 0.0005)) * 20) / 20
                if new_sl < curr_sl:
                    print(f"📉 Trailing Down: {curr_sl} -> {new_sl}")
                    obj.modifyOrder({
                        "variety": "STOPLOSS", "orderid": sl_order['orderid'], "ordertype": "STOPLOSS_LIMIT",
                        "producttype": "INTRADAY", "duration": "DAY", "price": new_sl, "quantity": abs(qty),
                        "triggerprice": new_sl, "tradingsymbol": symbol, "symboltoken": token, "exchange": "NSE"
                    })
        return active_count
    except: return 0

def execute_trade(obj, name, token, ltp, atr, side):
    qty = math.floor((CAPITAL_PER_TRADE * LEVERAGE) / ltp)
    if qty < 1: return
    print(f"🚀 {side} {name} | Price: {ltp} | ATR: {atr:.2f}")
    try:
        obj.placeOrder({
            "variety": "NORMAL", "tradingsymbol": f"{name}-EQ", "symboltoken": token,
            "transactiontype": "BUY" if side == "LONG" else "SELL", "exchange": "NSE", "ordertype": "MARKET",
            "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
        })
        time.sleep(1)
        sl_dist = atr * ATR_MULTIPLIER
        sl_price = (ltp - sl_dist) if side == "LONG" else (ltp + sl_dist)
        sl_price = round(sl_price * 20) / 20
        obj.placeOrder({
            "variety": "STOPLOSS", "tradingsymbol": f"{name}-EQ", "symboltoken": token,
            "transactiontype": "SELL" if side == "LONG" else "BUY", "exchange": "NSE", "ordertype": "STOPLOSS_LIMIT",
            "producttype": "INTRADAY", "duration": "DAY", "price": sl_price, "triggerprice": sl_price, "quantity": qty
        })
        print(f"🛡️ SL Set: {sl_price}")
    except Exception as e: print(e)

def get_nifty_trend(obj):
    metrics = get_intraday_metrics(obj, NIFTY_TOKEN)
    if not metrics: return "NEUTRAL"
    return "BULLISH" if metrics['close'] > metrics['vwap'] else "BEARISH"

def run():
    obj = login()
    print("------------------------------------------")
    print("       🚀 ANGLE INTRADAY BOT STARTED      ")
    print("       Target: 3% OI Blast + Yest Lvl     ")
    print("------------------------------------------")
    
    # 1. CHECK TIME EXIT (2:50 PM)
    if check_time_exit(obj): return

    # 2. MANAGE EXISTING TRADES (Trailing SL)
    tokens = get_tokens_map()
    active_trades = check_and_trail_sl(obj, tokens)
    
    if active_trades >= MAX_OPEN_POSITIONS: 
        print("⏸️ Trade Limit Reached. Managing current trade only."); return

    # 3. 11:00 AM DEADLINE CHECK
    ist_now = get_ist_time()
    if ist_now.hour >= 11:
        print(f"⏰ It is {ist_now.strftime('%H:%M')} (After 11 AM). No new entries.")
        return

    # 4. SCAN FOR NEW TRADES (Only if Time < 11:00 AM)
    nifty_trend = get_nifty_trend(obj)
    if nifty_trend == "NEUTRAL": return
    print(f"🔎 Scanning {nifty_trend} Setups...")

    for name, ids in tokens.items():
        try:
            # 1. Get Yesterday's Levels
            y_high, y_low = get_yesterday_levels(obj, ids['eq'])
            if y_high is None: continue
            
            # 2. Get Current Data
            curr = get_intraday_metrics(obj, ids['eq'])
            if curr is None: continue
            
            ltp = curr['close']
            atr = curr['atr']
            
            # 3. OI BLAST Filter (> 3%) - Must Pass
            if not check_oi_blast(obj, ids['fut']): continue 
            
            # 4. ENTRY LOGIC
            if nifty_trend == "BULLISH":
                # Price > Yest High AND Price > VWAP
                if ltp > y_high and ltp > curr['vwap']:
                     print(f"✅ CONFIRMED LONG: {name}")
                     execute_trade(obj, name, ids['eq'], ltp, atr, "LONG")
                     break

            elif nifty_trend == "BEARISH":
                # Price < Yest Low AND Price < VWAP
                if ltp < y_low and ltp < curr['vwap']:
                     print(f"✅ CONFIRMED SHORT: {name}")
                     execute_trade(obj, name, ids['eq'], ltp, atr, "SHORT")
                     break
        except: continue

if __name__ == "__main__":
    run()
