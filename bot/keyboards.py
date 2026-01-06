from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config.settings import VENDORS_CONFIG

def get_vendor_keyboard():
    """Клавиатура для выбора вендоров"""
    keyboard = []
    vendors = list(VENDORS_CONFIG.keys())
    
    # Создаем кнопки по 2 в ряд
    for i in range(0, len(vendors), 2):
        row = []
        for vendor in vendors[i:i+2]:
            row.append(InlineKeyboardButton(vendor, callback_data=f"sync_{vendor}"))
        keyboard.append(row)
    
    # Добавляем кнопку "Все вендоры"
    keyboard.append([InlineKeyboardButton("🔄 Все вендоры", callback_data="sync_all")])
    
    return InlineKeyboardMarkup(keyboard)

def get_confirmation_keyboard():
    """Клавиатура для подтверждения действий"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_yes"),
            InlineKeyboardButton("❌ Отменить", callback_data="confirm_no")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)