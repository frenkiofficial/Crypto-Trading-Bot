import logging
import requests
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, JobQueue, ApplicationBuilder

# --- Configuration ---
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"  # Replace with your bot token
COINGECKO_API_URL = "https://api.coingecko.com/api/v3/simple/price"
# Check interval for alerts in seconds (e.g., every 60 seconds)
ALERT_CHECK_INTERVAL = 60

# --- Data Storage (In-Memory) ---
# Structure: {user_id: [{'coin_id': 'bitcoin', 'symbol': 'BTC', 'target_price': 70000.0}]}
# Warning: This data will be lost when the bot restarts.
# For persistence, consider using a database (SQLite, etc.) or a file (JSON).
user_alerts = {}

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def get_coin_id_from_symbol(symbol: str) -> str | None:
    """
    Attempts to map a common crypto symbol (like BTC) to a CoinGecko ID (like bitcoin).
    This is a simple mapping; a more robust solution might query CoinGecko's /coins/list endpoint.
    """
    symbol_lower = symbol.lower()
    # Add more mappings as needed
    mapping = {
        'btc': 'bitcoin',
        'eth': 'ethereum',
        'sol': 'solana',
        'xrp': 'ripple',
        'doge': 'dogecoin',
        'shib': 'shiba-inu',
        'ada': 'cardano',
        'dot': 'polkadot',
        'ltc': 'litecoin',
        'bch': 'bitcoin-cash',
        # ... add other popular coins
    }
    return mapping.get(symbol_lower)

async def get_crypto_price(coin_id: str) -> float | None:
    """Fetches the current price of a cryptocurrency from CoinGecko."""
    params = {
        'ids': coin_id,
        'vs_currencies': 'usd'
    }
    try:
        response = requests.get(COINGECKO_API_URL, params=params, timeout=10)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
        data = response.json()

        if coin_id in data and 'usd' in data[coin_id]:
            return float(data[coin_id]['usd'])
        else:
            logger.warning(f"Price data not found for {coin_id} in response: {data}")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching price for {coin_id} from CoinGecko: {e}")
        return None
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"Error parsing price data for {coin_id}: {e} - Data: {data if 'data' in locals() else 'N/A'}")
        return None

# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the /start command is issued."""
    await update.message.reply_text(
        "Hello! I'm your Crypto Price Bot.\n\n"
        "Use /price <COIN_SYMBOL> (e.g., /price BTC) to get the current price.\n"
        "Use /alert <COIN_SYMBOL> <TARGET_PRICE> (e.g., /alert BTC 70000) to set a price alert.\n"
        "Use /listalerts to see your active alerts."
    )

async def get_price_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /price command."""
    chat_id = update.effective_chat.id
    try:
        if not context.args:
            await context.bot.send_message(chat_id=chat_id, text="Please provide a coin symbol. Usage: /price <COIN_SYMBOL> (e.g., /price BTC)")
            return

        symbol = context.args[0].upper()
        coin_id = get_coin_id_from_symbol(symbol)

        if not coin_id:
            await context.bot.send_message(chat_id=chat_id, text=f"Sorry, I don't recognize the symbol '{symbol}'. Please use common symbols like BTC, ETH, SOL, etc.")
            return

        price = await get_crypto_price(coin_id)

        if price is not None:
            await context.bot.send_message(chat_id=chat_id, text=f"The current price of {symbol} ({coin_id}) is ${price:,.2f}")
        else:
            await context.bot.send_message(chat_id=chat_id, text=f"Sorry, I couldn't fetch the price for {symbol} ({coin_id}) right now. Please try again later.")

    except Exception as e:
        logger.error(f"Error in /price command: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="An unexpected error occurred while fetching the price.")

async def set_alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /alert command."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if len(context.args) != 2:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Usage: /alert <COIN_SYMBOL> <TARGET_PRICE>\nExample: /alert BTC 70000"
        )
        return

    symbol = context.args[0].upper()
    target_price_str = context.args[1]

    coin_id = get_coin_id_from_symbol(symbol)
    if not coin_id:
        await context.bot.send_message(chat_id=chat_id, text=f"Sorry, I don't recognize the symbol '{symbol}'.")
        return

    try:
        target_price = float(target_price_str)
        if target_price <= 0:
            raise ValueError("Price must be positive")
    except ValueError:
        await context.bot.send_message(chat_id=chat_id, text=f"Invalid target price '{target_price_str}'. Please enter a valid number.")
        return

    # --- Add alert to user's list ---
    if user_id not in user_alerts:
        user_alerts[user_id] = []

    # Optional: Check for duplicate alerts for the same coin
    existing_alert = next((alert for alert in user_alerts[user_id] if alert['coin_id'] == coin_id and alert['target_price'] == target_price), None)
    if existing_alert:
        await context.bot.send_message(chat_id=chat_id, text=f"You already have an alert set for {symbol} at ${target_price:,.2f}.")
        return

    # Optional: Limit the number of alerts per user
    MAX_ALERTS_PER_USER = 10
    if len(user_alerts[user_id]) >= MAX_ALERTS_PER_USER:
         await context.bot.send_message(chat_id=chat_id, text=f"You have reached the maximum limit of {MAX_ALERTS_PER_USER} alerts. Please remove some using /removealert (feature not implemented yet) or wait for them to trigger.")
         return

    alert_data = {'coin_id': coin_id, 'symbol': symbol, 'target_price': target_price}
    user_alerts[user_id].append(alert_data)

    logger.info(f"User {user_id} set alert: {alert_data}")
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"✅ Alert set! I will notify you when {symbol} reaches ${target_price:,.2f}."
    )

async def list_alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /listalerts command."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if user_id not in user_alerts or not user_alerts[user_id]:
        await context.bot.send_message(chat_id=chat_id, text="You have no active alerts set.")
        return

    message = "Your active alerts:\n"
    for i, alert in enumerate(user_alerts[user_id]):
        message += f"{i+1}. {alert['symbol']} at ${alert['target_price']:,.2f}\n"

    await context.bot.send_message(chat_id=chat_id, text=message)

# --- Background Job for Checking Alerts ---

async def check_price_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodically checks prices against set alerts."""
    if not user_alerts:
        #logger.info("Alert check: No active alerts found.")
        return # Skip if no alerts are set globally

    logger.info(f"Running periodic alert check for {len(user_alerts)} users.")

    alerts_to_remove = {user_id: [] for user_id in user_alerts}
    coins_to_fetch = set()

    # Gather all unique coin_ids needed for this check cycle
    for user_id, alerts in user_alerts.items():
        for alert in alerts:
            coins_to_fetch.add(alert['coin_id'])

    if not coins_to_fetch:
        return

    # Fetch prices in bulk if possible (CoinGecko simple API allows this)
    coin_ids_str = ",".join(list(coins_to_fetch))
    params = {'ids': coin_ids_str, 'vs_currencies': 'usd'}
    current_prices = {}
    try:
        response = requests.get(COINGECKO_API_URL, params=params, timeout=15)
        response.raise_for_status()
        price_data = response.json()
        for coin_id, data in price_data.items():
            if 'usd' in data:
                current_prices[coin_id] = float(data['usd'])
            else:
                logger.warning(f"Alert Check: No USD price found for {coin_id} in bulk fetch.")
    except requests.exceptions.RequestException as e:
        logger.error(f"Alert Check: Error fetching bulk prices from CoinGecko: {e}")
        return # Skip checking this cycle if API fails
    except Exception as e:
        logger.error(f"Alert Check: Error processing bulk price data: {e}", exc_info=True)
        return

    # Now check alerts against fetched prices
    for user_id, alerts in user_alerts.items():
        for alert in alerts:
            coin_id = alert['coin_id']
            target_price = alert['target_price']
            symbol = alert['symbol']

            if coin_id not in current_prices:
                logger.warning(f"Alert Check: Price for {coin_id} not available in current fetch cycle for user {user_id}.")
                continue # Skip this alert if price wasn't fetched

            current_price = current_prices[coin_id]

            # --- Trigger Condition ---
            # Simple trigger: notify if current price is >= target price
            # More complex logic could be added (e.g., direction, crossing thresholds)
            if current_price >= target_price:
                message = (
                    f"🚨 Price Alert! 🚨\n\n"
                    f"{symbol} has reached your target of ${target_price:,.2f}!\n"
                    f"Current Price: ${current_price:,.2f}"
                )
                try:
                    await context.bot.send_message(chat_id=user_id, text=message)
                    logger.info(f"Sent alert notification to user {user_id} for {symbol} at ${target_price}")
                    # Mark alert for removal after successful notification
                    alerts_to_remove[user_id].append(alert)
                except Exception as e:
                    # Handle cases where the bot might be blocked by the user, etc.
                    logger.error(f"Failed to send alert notification to user {user_id}: {e}")
                    # Decide if you still want to remove the alert or retry later
                    # For simplicity, we'll still mark it for removal to avoid spamming logs
                    # if the error persists (like user blocking the bot).
                    alerts_to_remove[user_id].append(alert)

    # --- Clean up triggered alerts ---
    for user_id, alerts_list in alerts_to_remove.items():
        if not alerts_list:
            continue

        # Filter out the alerts that were marked for removal
        original_alerts = user_alerts.get(user_id, [])
        user_alerts[user_id] = [
            alert for alert in original_alerts if alert not in alerts_list
        ]
        if not user_alerts[user_id]: # If list becomes empty, remove user entry
            del user_alerts[user_id]
        logger.info(f"Removed {len(alerts_list)} triggered alerts for user {user_id}")


# --- Main Bot Execution ---

def main() -> None:
    """Start the bot."""
    if TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        logger.error("FATAL: Bot token not set. Please replace 'YOUR_TELEGRAM_BOT_TOKEN' with your actual token.")
        return

    # Create the Application and pass it your bot's token.
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Get the job queue for scheduling background tasks
    job_queue = application.job_queue

    # --- Register command handlers ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("price", get_price_command))
    application.add_handler(CommandHandler("alert", set_alert_command))
    application.add_handler(CommandHandler("listalerts", list_alerts_command))

    # --- Schedule the alert checking job ---
    # Run the job immediately upon start and then every ALERT_CHECK_INTERVAL seconds
    job_queue.run_repeating(check_price_alerts, interval=ALERT_CHECK_INTERVAL, first=10) # Start after 10 seconds
    logger.info(f"Scheduled price alert check job to run every {ALERT_CHECK_INTERVAL} seconds.")

    # Run the bot until the user presses Ctrl-C
    logger.info("Starting bot polling...")
    application.run_polling()
    logger.info("Bot stopped.")


if __name__ == '__main__':
    main()
