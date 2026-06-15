# -*- coding: utf-8 -*-
import os, sys, time, json, logging, asyncio, threading
import importlib.util
from datetime import datetime
from pytz import timezone
import pandas as pd
import gspread
from flask import Flask, jsonify

# --- 導入 PTB 必要類別 ---
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    JobQueue
)

# --- 1. 設置日誌記錄 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 2. 核心參數設定 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SPREADSHEET_NAME = "雲端提醒"
TAIPEI_TZ = timezone('Asia/Taipei')

def safe_get_chat_id():
    val = os.environ.get("TELEGRAM_CHAT_ID")
    if not val: return None
    try:
        clean_val = "".join(c for c in str(val).strip() if c.isdigit() or c == '-')
        return int(clean_val)
    except:
        return None

# 全域變數
ANALYZE_FUNC = None
ta_helpers = None

# --- 3. 核心模組動態加載 ---
try:
    for m in ["ta_analyzer", "ta_helpers"]:
        path = os.path.join(current_dir, f"{m}.py")
        spec = importlib.util.spec_from_file_location(m, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if m == "ta_analyzer":
            ANALYZE_FUNC = mod.analyze_and_update_sheets
        else:
            ta_helpers = mod
    logger.info("✅ 核心分析模組加載成功")
except Exception as e:
    logger.error(f"❌ 模組載入失敗: {e}")

# --- 4. 資料處理函式 ---
def get_google_sheets_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json: return None
    try:
        return gspread.service_account_from_dict(json.loads(creds_json))
    except: return None

def fetch_stock_data_for_reminder():
    # 直接在程式碼中寫死你想監控的特定台股與指標欄位
    data = [
        ['代號', '名稱', '五日均線大於二十日均線', '每日收盤價小於布林通道下軌', 'kd黃金交叉', 'k小於20且kd黃金交叉', 'k值大於80且kd死亡交叉', '成交量暴增', '三大法人連續買超', '提供者'],
        ['2356.tw', '英業達', '1', '0', '1', '0', '0', '0', '0', ''],
        ['6443.tw', '元晶', '1', '0', '1', '0', '0', '0', '0', ''],
        ['1718.tw', '中纖', '1', '0', '1', '0', '0', '0', '0', ''],
        ['2324.tw', '仁寶', '1', '0', '1', '0', '0', '0', '0', ''],
        ['2409.tw', '友達', '1', '0', '1', '0', '0', '0', '0', ''],
        ['9105.tw', '泰金寶-DR', '1', '0', '1', '0', '0', '0', '0', ''],
        ['6116.tw', '彩晶', '1', '0', '1', '0', '0', '0', '0', ''],
        ['3481.tw', '群創', '1', '0', '1', '0', '0', '0', '0', '']
    ]

    
    # 以下保留原專案的資料轉換邏輯，確保後續運作正常
    df = pd.DataFrame(data[1:], columns=data[0])
    df['代號'] = df['代號'].str.strip()
    

        
        # 確保必要的名稱欄位存在
        if '名稱' not in df.columns:
            df.rename(columns={df.columns[1]: '名稱'}, inplace=True)
        
        df = df[df['代號'].astype(bool)].copy()
        provider_col = '提供者'
        if provider_col not in df.columns: df[provider_col] = ''
        
        if ta_helpers:
            df['連結'] = df.apply(lambda row: ta_helpers.get_static_link(row['代號'], row[provider_col]), axis=1)
        return df
    except Exception as e:
        logger.error(f"讀取試算表失敗: {e}")
        return pd.DataFrame()

# --- 5. 核心執行任務 ---
async def run_analysis_and_send(bot):
    target_id = safe_get_chat_id()
    if not target_id:
        logger.warning("‼️ 找不到 TELEGRAM_CHAT_ID")
        return False
        
    now_taipei = datetime.now(TAIPEI_TZ)
    logger.info(f"⏰ 啟動分析任務: {now_taipei.strftime('%Y-%m-%d %H:%M:%S')}")
    
    stock_df = fetch_stock_data_for_reminder()
    if stock_df.empty: return False

    gc = get_google_sheets_client()
    if ANALYZE_FUNC:
        # 呼叫分析函數。注意：去重的邏輯通常寫在 ta_analyzer.py 裡面
        # 它會比對 Excel 中的「去重日期」欄位
        alerts = ANALYZE_FUNC(gc, SPREADSHEET_NAME, stock_df['代號'].tolist(), stock_df)
        
        if alerts:
            header = f"🔔 *技術指標警報 ({now_taipei.strftime('%H:%M:%S')})*"
            await bot.send_message(chat_id=target_id, text=header, parse_mode='Markdown')
            for msg in alerts:
                try:
                    await bot.send_message(chat_id=target_id, text=msg, parse_mode='Markdown', disable_web_page_preview=True)
                    await asyncio.sleep(0.8) # 稍微增加延遲避免被 Telegram 阻擋
                except Exception as e:
                    logger.error(f"發送失敗: {e}")
            return True
        else:
            logger.info("✅ 目前無新觸發指標（或今日已發送過）")
    return False

# --- 6. Telegram 任務接口 ---
async def periodic_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    await run_analysis_and_send(context.bot)

async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🚀 收到指令，開始即時分析...")
    success = await run_analysis_and_send(context.bot)
    if not success:
        await update.message.reply_text("ℹ️ 分析完成，目前沒有符合條件的新警報。")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    current_id = update.effective_chat.id
    await update.message.reply_text(f"👋 綁定成功！\nChat ID: `{current_id}`")

# --- 7. 排程設定 (每 30 分鐘執行一次) ---
def setup_scheduling(job_queue: JobQueue):
    # 修改：週一至週五 08:00 - 13:30 每 30 分鐘執行
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '0,30', 'hour': '8-13', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Market_Hours')
    # 收盤提醒
    job_queue.run_custom(periodic_reminder_job, job_kwargs={'trigger': 'cron', 'minute': '40', 'hour': '13', 'day_of_week': 'mon-fri', 'timezone': TAIPEI_TZ}, name='Closing')

# --- 8. Web 服務 ---
app = Flask(__name__)
@app.route('/')
@app.route('/health')
def health_check():
    return jsonify({"status": "ok", "server_time": datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M:%S')}), 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# --- 9. 主程式入口 ---
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ 找不到 TELEGRAM_BOT_TOKEN")
        return

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    setup_scheduling(application.job_queue)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("run", run_command))
    
    logger.info("📢 Bot 運行中...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
