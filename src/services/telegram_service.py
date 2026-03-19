"""Telegram service - formatting and sending messages"""
import asyncio
from typing import Dict, List
from src.config import get_settings
from src.utils.logger import setup_logger
from telegram import Bot
from telegram.error import TelegramError

logger = setup_logger("telegram_service")
settings = get_settings()

class TelegramService:
    """Telegram message formatting and sending"""
    
    def __init__(self):
        self.bot = None
        self.group_id = settings.telegram_group_id
        if settings.telegram_bot_token:
            self.bot = Bot(token=settings.telegram_bot_token)
        else:
            logger.warning("TELEGRAM_BOT_TOKEN not set - Telegram features disabled")
    
    def format_alert_message(self, token: Dict, signal: Dict, breakdown: Dict) -> str:
        """Format alert message as HTML"""
        if not token or not signal:
            return ""
        
        name = token.get("name", "Unknown")
        symbol = token.get("symbol", "?")
        ca = token.get("mint", "?")
        score = signal.get("score", 0)
        market_cap = token.get("market_cap", 0)
        volume_24h = token.get("volume_24h", 0)
        holders = token.get("holders", 0)
        age_hours = token.get("age_hours", 0)
        narrative = breakdown.get("narrative_type", "Unknown")
        risk = breakdown.get("risk_level", "🔴 HIGH")
        
        # Emoji based on score
        if score >= 80:
            emoji = "🟢"
            action = "ADOPT"
        elif score >= 60:
            emoji = "🟡"
            action = "WATCH"
        else:
            emoji = "🔴"
            action = "RISK"
        
        # Format market cap
        if market_cap > 1000000:
            mc_str = f"${market_cap/1000000:.1f}M"
        elif market_cap > 1000:
            mc_str = f"${market_cap/1000:.1f}K"
        else:
            mc_str = f"${market_cap:.0f}"
        
        # Format volume
        if volume_24h > 1000000:
            vol_str = f"${volume_24h/1000000:.1f}M"
        elif volume_24h > 1000:
            vol_str = f"${volume_24h/1000:.1f}K"
        else:
            vol_str = f"${volume_24h:.0f}"
        
        message = f"""<b>{emoji} {action}: {name} (${symbol})</b>

<b>📊 Score: {score}/100</b>
CA: <code>{ca[:20]}...</code> | {mc_str}

<b>Breakdown:</b>
• Status: +{breakdown.get('status', 0)} | Market Cap: +{breakdown.get('market_cap', 0)} | Holders: +{breakdown.get('holders', 0)}
• Volume 24h: +{breakdown.get('volume', 0)} | Liquidity: +{breakdown.get('liquidity', 0)} | Narrative: {narrative} (+{breakdown.get('narrative', 0)})
• Age: {breakdown.get('age_penalty', 0)} | Risk: {risk}

💰 24h Volume: {vol_str} | Holders: {holders:,}
📈 Narrative: {narrative} | Liquidity: {token.get('liquidity_ratio', 0):.1f}%
🕐 Age: {age_hours:.1f}h

<a href="https://pump.fun/coin/{ca}">→ Buy on Pump.fun</a>"""
        
        return message
    
    def format_status_message(self, stats: Dict) -> str:
        """Format status report"""
        return f"""<b>📊 Scanner Status</b>

✅ Status: Online
🔍 Last Scan: Just now
📈 Tokens Scanned: {stats.get('tokens_today', 0)}
🚨 Alerts Posted: {stats.get('alerts_today', 0)}
⏱️ Uptime: {stats.get('uptime', 'unknown')}

<b>⚙️ Settings:</b>
• Alert Threshold: {settings.alert_threshold}
• Scan Interval: {settings.scan_interval_seconds}s
• Data Retention: {settings.data_retention_hours}h"""
    
    def format_top_signals(self, signals: List[Dict], limit: int = 5) -> str:
        """Format top signals list"""
        if not signals:
            return "<b>📊 Top Signals</b>\n\nNo signals found yet."
        
        lines = ["<b>🏆 Top Signals This Hour:</b>\n"]
        for i, sig in enumerate(signals[:limit], 1):
            name = sig.get("name", "Unknown")
            symbol = sig.get("symbol", "?")
            score = sig.get("score", 0)
            emoji = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
            lines.append(f"{i}. {emoji} {name} (${symbol}) - Score: {score}")
        
        return "\n".join(lines)
    
    def format_settings_message(self) -> str:
        """Format current settings"""
        return f"""<b>⚙️ Current Settings</b>

🚨 Alert Threshold: {settings.alert_threshold}
💰 Min Market Cap: ${settings.min_market_cap:,}
👥 Min Holders: {settings.min_holders}
⏱️ Scan Interval: {settings.scan_interval_seconds}s
🕐 Data Retention: {settings.data_retention_hours}h
🧹 Cleanup Interval: {settings.housekeeper_interval_minutes}m"""
    
    async def send_alert(self, message: str) -> bool:
        """Send alert to group"""
        if not self.bot or not self.group_id:
            logger.warning("Telegram bot not configured")
            return False
        
        try:
            await self.bot.send_message(
                chat_id=self.group_id,
                text=message,
                parse_mode="HTML",
                disable_web_page_preview=False
            )
            logger.info(f"✅ Alert posted to group {self.group_id}")
            return True
        except TelegramError as e:
            logger.error(f"Failed to post alert: {e}")
            return False
    
    async def send_message(self, message: str, chat_id: int = None) -> bool:
        """Send message to group or specific chat"""
        if not self.bot:
            return False
        
        chat = chat_id or self.group_id
        if not chat:
            return False
        
        try:
            # Split message if too long (Telegram limit: 4096)
            if len(message) > 4000:
                for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
                    await self.bot.send_message(
                        chat_id=chat,
                        text=chunk,
                        parse_mode="HTML"
                    )
                    await asyncio.sleep(0.5)  # Rate limit
            else:
                await self.bot.send_message(
                    chat_id=chat,
                    text=message,
                    parse_mode="HTML"
                )
            return True
        except TelegramError as e:
            logger.error(f"Failed to send message: {e}")
            return False

telegram_service = TelegramService()
