"""Telegram bot - command handlers"""
import asyncpg
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, filters
from src.config import get_settings
from src.utils.logger import setup_logger
from src.services.telegram_service import telegram_service

logger = setup_logger("telegram_bot")
settings = get_settings()

async def get_db():
    return await asyncpg.connect(settings.database_url)

class TelegramBot:
    """Telegram bot command handlers"""
    
    def __init__(self):
        self.app = None
        self.group_id = settings.telegram_group_id
    
    async def start_bot(self):
        """Start bot if token is configured"""
        if not settings.telegram_bot_token:
            logger.warning("TELEGRAM_BOT_TOKEN not set - bot disabled")
            return
        
        try:
            self.app = Application.builder().token(settings.telegram_bot_token).build()
            
            # Add command handlers
            self.app.add_handler(CommandHandler("status", self.cmd_status, filters=filters.ChatType.GROUPS))
            self.app.add_handler(CommandHandler("alerts", self.cmd_alerts, filters=filters.ChatType.GROUPS))
            self.app.add_handler(CommandHandler("top", self.cmd_top, filters=filters.ChatType.GROUPS))
            self.app.add_handler(CommandHandler("settings", self.cmd_settings, filters=filters.ChatType.GROUPS))
            self.app.add_handler(CommandHandler("logs", self.cmd_logs, filters=filters.ChatType.GROUPS))
            self.app.add_handler(CommandHandler("pause", self.cmd_pause, filters=filters.ChatType.GROUPS))
            self.app.add_handler(CommandHandler("resume", self.cmd_resume, filters=filters.ChatType.GROUPS))
            self.app.add_handler(CommandHandler("help", self.cmd_help, filters=filters.ChatType.GROUPS))
            
            await self.app.bot.set_my_commands([
                ("status", "Scanner status"),
                ("alerts", "Last 10 alerts"),
                ("top", "Top 5 this hour"),
                ("settings", "Current settings"),
                ("logs", "Recent errors"),
                ("pause", "Pause scanner"),
                ("resume", "Resume scanner"),
                ("help", "Show help"),
            ])
            
            logger.info("✅ Telegram bot initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize bot: {e}")
    
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        try:
            conn = await get_db()
            
            # Get scan stats
            stats = await conn.fetchrow(
                """
                SELECT COUNT(*) as tokens_found, 
                       SUM(CASE WHEN alerts_posted > 0 THEN alerts_posted ELSE 0 END) as alerts_total
                FROM scan_log
                WHERE scan_date > NOW() - INTERVAL '24 hours'
                """
            )
            
            message = f"""<b>📊 Scanner Status</b>

✅ Status: Online
🔍 Tokens Scanned (24h): {stats['tokens_found'] or 0}
🚨 Alerts Posted (24h): {stats['alerts_total'] or 0}
⏱️ Alert Threshold: {settings.alert_threshold}

<b>⚙️ Settings:</b>
• Scan Interval: {settings.scan_interval_seconds}s
• Min Market Cap: ${settings.min_market_cap:,}
• Data Retention: {settings.data_retention_hours}h"""
            
            await update.message.reply_text(message, parse_mode="HTML")
            await conn.close()
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)[:100]}", parse_mode="HTML")
            logger.error(f"status command error: {e}")
    
    async def cmd_alerts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /alerts command"""
        try:
            conn = await get_db()
            
            # Get last 10 alerts
            alerts = await conn.fetch(
                """
                SELECT t.name, t.symbol, s.score
                FROM alerts a
                JOIN signals s ON a.signal_id = s.id
                JOIN tokens t ON s.token_id = t.id
                WHERE a.status::text = 'posted'
                ORDER BY a.created_at DESC
                LIMIT 10
                """
            )
            
            if not alerts:
                await update.message.reply_text("No alerts posted yet.", parse_mode="HTML")
                await conn.close()
                return
            
            lines = ["<b>🚨 Last 10 Alerts</b>\n"]
            for i, alert in enumerate(alerts, 1):
                emoji = "🟢" if alert["score"] >= 70 else "🟡" if alert["score"] >= 50 else "🔴"
                lines.append(f"{i}. {emoji} {alert['name']} (${alert['symbol']}) - {alert['score']}")
            
            message = "\n".join(lines)
            await update.message.reply_text(message, parse_mode="HTML")
            await conn.close()
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)[:100]}", parse_mode="HTML")
            logger.error(f"alerts command error: {e}")
    
    async def cmd_top(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /top command"""
        try:
            conn = await get_db()
            
            # Get top 5 signals this hour
            signals = await conn.fetch(
                """
                SELECT t.name, t.symbol, s.score
                FROM signals s
                JOIN tokens t ON s.token_id = t.id
                WHERE s.created_at > NOW() - INTERVAL '1 hour'
                ORDER BY s.score DESC
                LIMIT 5
                """
            )
            
            if not signals:
                await update.message.reply_text("No signals found in the last hour.", parse_mode="HTML")
                await conn.close()
                return
            
            lines = ["<b>🏆 Top 5 Signals (This Hour)</b>\n"]
            for i, sig in enumerate(signals, 1):
                emoji = "🟢" if sig["score"] >= 70 else "🟡" if sig["score"] >= 50 else "🔴"
                lines.append(f"{i}. {emoji} {sig['name']} (${sig['symbol']}) - {sig['score']}/100")
            
            message = "\n".join(lines)
            await update.message.reply_text(message, parse_mode="HTML")
            await conn.close()
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)[:100]}", parse_mode="HTML")
            logger.error(f"top command error: {e}")
    
    async def cmd_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /settings command"""
        try:
            message = telegram_service.format_settings_message()
            await update.message.reply_text(message, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)[:100]}", parse_mode="HTML")
            logger.error(f"settings command error: {e}")
    
    async def cmd_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /logs command"""
        try:
            # Read last 5 lines from error log
            try:
                with open("/app/logs/main.log", "r") as f:
                    lines = f.readlines()[-5:]
                log_text = "".join(lines) or "No logs yet"
            except:
                log_text = "Could not read logs"
            
            message = f"""<b>📋 Recent Logs</b>

<code>{log_text[:500]}</code>"""
            await update.message.reply_text(message, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)[:100]}", parse_mode="HTML")
            logger.error(f"logs command error: {e}")
    
    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /pause command"""
        try:
            await update.message.reply_text("⏸️ Scanner paused (feature coming soon)", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)[:100]}", parse_mode="HTML")
    
    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /resume command"""
        try:
            await update.message.reply_text("▶️ Scanner resumed (feature coming soon)", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)[:100]}", parse_mode="HTML")
    
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        try:
            message = f"""<b>📖 Pump Signal Bot - Commands</b>

<b>Monitoring:</b>
/status - Scanner status & stats
/alerts - Last 10 alerts posted
/top - Top 5 signals this hour

<b>Settings:</b>
/settings - View current configuration
/logs - Recent error logs

<b>Control:</b>
/pause - Pause scanner
/resume - Resume scanner

<b>Help:</b>
/help - Show this message

Alerts auto-post when score ≥ {settings.alert_threshold}"""
            
            await update.message.reply_text(message, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)[:100]}", parse_mode="HTML")

telegram_bot = TelegramBot()
