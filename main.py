import pandas as pd
import requests
import time
import math
import os
from datetime import datetime, timedelta
from SmartApi import SmartConnect
import pyotp

# --- 1. SECURE CONFIGURATION (GitHub Secrets) ---
API_KEY = os.environ.get('API_KEY')
CLIENT_ID = os.environ.get('CLIENT_ID')
PIN = os.environ.get('PIN')
TOTP_KEY = os.environ.get('TOTP_KEY')

# --- 2. STRATEGY SETTINGS ---
CAPITAL_PER_TRADE = 5000       
LEVERAGE = 5.0                 
ATR_MULTIPLIER = 2.0           
NIFTY_TOKEN = "99926000"       
MAX_OPEN_POSITIONS = 1         
OI_BLAST_THRESHOLD = 3.0       

def login():
    try:
        obj = SmartConnect(api_key=API_KEY)
        data = obj.generateSession(CLIENT_ID, PIN, pyotp.TOTP(TOTP_KEY).now())
        return obj
    except Exception as e:
        print(f"❌ Login Failed: {e}")
        exit()

def get_ist_time():
    # Convert Server Time (UTC) to Indian Time (IST)
    utc_now = datetime.utcnow()
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    return ist_now

def get_tokens_map():
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    try:
        data = requests.get(url).json()
        futures_map = {}
        for item in data:
            if item['instrumenttype'] == 'FUTSTK' and item['exchangeseg'] == 'NFO':
                name = item['name']
                if name not in futures_map: futures_map[name] = []
                try:
                    exp = datetime.strptime(item['expirydate'], '%d%b%Y')
                    futures_map[name].append({'date': exp, 'token': item['token']})
                except: continue
        
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
        
        df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()
        df['ema_10'] = df['close'].ewm(span=10, adjust=False).mean()
        
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
    try:
        quote = obj.getMarketData("NFO", fut_token) 
        if quote and 'data' in quote:
            current_oi = float(quote['data']['oi'])  
            start_day_oi = float(quote['data'].get('opninterest', current_oi)) 
            if start_day_oi == 0: return False
            oi_change_pct = ((current_oi - start_day_oi) / start_day_oi) * 100
            if abs(oi_change_pct) > OI_BLAST_THRESHOLD:
                print(f"🔥 OI BLAST DETECTED: {oi_change_pct:.2f}%")
                return True
    except: return False
    return False

# --- 🔥 CRITICAL: 2:50 PM FORCE EXIT LOGIC ---
def check_time_exit(obj):
    ist_now = get_ist_time()
    
    # Logic: If time is 2:50 PM or later (up to 2:59 PM)
    if ist_now.hour == 14 and ist_now.minute >= 50:
        print(f"⏰ 2:50 PM Check: Scanning for open Intraday trades to Close...")
        
        try:
            positions = obj.position()
            if positions and positions['data']:
                for pos in positions['data']:
                    qty = int(pos['netqty'])
                    
                    # ⚠️ ONLY Close 'INTRADAY'. Keep 'MARGIN' (Swing) Safe.
                    if qty != 0 and pos['producttype'] == 'INTRADAY':
                        print(f"🚨 FORCE EXIT TRIGGERED: Closing {pos['tradingsymbol']} (Qty: {qty})")
                        
                        # Place Market Exit Order
                        order_params = {
                            "variety": "NORMAL", 
                            "tradingsymbol": pos['tradingsymbol'], 
                            "symboltoken": pos['symboltoken'],
                            "transactiontype": "SELL" if qty > 0 else "BUY", # Opposite of current position
                            "exchange": "NSE", 
                            "ordertype": "MARKET",
                            "producttype": "INTRADAY", 
                            "duration": "DAY", 
                            "quantity": abs(qty)
                        }
                        obj.placeOrder(order_params)
            
            return True # Returns True meaning "We are in Exit Mode"
            
        except Exception as e:
            print(f"Error in Exit Logic: {e}")
            return True
            
    return False # Not 2:50 PM yet

def check_and_trail_sl(obj, token_map):
    # This just counts active trades
    try:
        positions = obj.position()
        if not positions or not positions['data']: return 0
        active_count = 0
        for pos in positions['data']:
            qty = int(pos['netqty'])
            if qty != 0 and pos['producttype'] == 'INTRADAY': 
                active_count += 1
        return active_count
    except: return 0

def execute_trade(obj, name, token, ltp, atr, side):
    qty = math.floor((CAPITAL_PER_TRADE * LEVERAGE) / ltp)
    if qty < 1: return
    print(f"🚀 {side} {name} | Price: {ltp} | ATR: {atr:.2f}")
    try:
        # 1. Main Order
        obj.placeOrder({
            "variety": "NORMAL", "tradingsymbol": f"{name}-EQ", "symboltoken": token,
            "transactiontype": "BUY" if side == "LONG" else "SELL", "exchange": "NSE", "ordertype": "MARKET",
            "producttype": "INTRADAY", "duration": "DAY", "quantity": qty
        })
        time.sleep(1)
        
        # 2. Stoploss Order
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

# --- MAIN CONTINUOUS LOOP ---
def run():
    print("------------------------------------------")
    print("       🚀 CONTINUOUS BOT STARTED          ")
    print("       Mode: Non-Stop Monitor             ")
    print("       Exit: Force Close @ 2:50 PM        ")
    print("------------------------------------------")
    
    obj = login()
    tokens = get_tokens_map()

    # Infinite Loop (Stays Alive)
    while True:
        ist_now = get_ist_time()
        
        # A. Stop at 3:30 PM IST (Market Close)
        if ist_now.hour >= 15 and ist_now.minute >= 30:
            print("😴 Market Closed. Bot shutting down.")
            break

        print(f"Scanning at {ist_now.strftime('%H:%M:%S')}...")

        # B. CHECK 2:50 PM EXIT FIRST
        # If it is 2:50 PM, this function runs, closes trades, and returns True.
        if check_time_exit(obj):
            print("⚠️ In Force Exit Zone (2:50-3:00 PM). No new trades.")
            time.sleep(60) # Wait 1 min and check again (to ensure exit)
            continue

        # C. Count Trades
        active_trades = check_and_trail_sl(obj, tokens)
        
        if active_trades >= MAX_OPEN_POSITIONS:
            print(f"⏸️ Trade Active. Monitoring... (Time: {ist_now.strftime('%H:%M')})")
            time.sleep(60)
            continue

        # D. ENTRY LOGIC (Only 10 AM - 11 AM)
        if ist_now.hour == 10:
            nifty_trend = get_nifty_trend(obj)
            if nifty_trend != "NEUTRAL":
                for name, ids in tokens.items():
                    try:
                        y_high, y_low = get_yesterday_levels(obj, ids['eq'])
                        if y_high is None: continue
                        
                        curr = get_intraday_metrics(obj, ids['eq'])
                        if curr is None: continue
                        
                        ltp = curr['close']
                        atr = curr['atr']
                        
                        # 1. Check Blast
                        if not check_oi_blast(obj, ids['fut']): continue 
                        
                        # 2. Check Trend & Level
                        if nifty_trend == "BULLISH":
                            if ltp > y_high and ltp > curr['vwap']:
                                execute_trade(obj, name, ids['eq'], ltp, atr, "LONG")
                                break # Stop scanning after 1 trade

                        elif nifty_trend == "BEARISH":
                            if ltp < y_low and ltp < curr['vwap']:
                                execute_trade(obj, name, ids['eq'], ltp, atr, "SHORT")
                                break # Stop scanning after 1 trade
                    except: continue
        else:
            print("⏳ Waiting for 10 AM - 11 AM Window...")

        # E. Heartbeat Speed (60 Seconds)
        time.sleep(60)

if __name__ == "__main__":
    run()
